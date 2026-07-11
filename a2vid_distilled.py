"""A2V Distilled Pipeline — audio-to-video with the distilled model.

Mirrors the upstream ``DistilledPipeline`` approach: two-stage generation
(half-res → upscale → full-res refine) with the distilled transformer
and no CFG guidance. Handles audio conditioning from an input audio file.

Requires only the distilled checkpoint (``transformer-distilled.safetensors``
or ``transformer.safetensors``) — no dev model needed. Q4 compatible.
"""

from __future__ import annotations

from pathlib import Path
import tempfile

import mlx.core as mx

from ltx_core_mlx.components.patchifiers import compute_video_latent_shape
from ltx_core_mlx.conditioning.types.latent_cond import LatentState
from ltx_core_mlx.model.audio_vae import encode_audio
from ltx_core_mlx.model.transformer.model import X0Model
from ltx_core_mlx.utils.audio import load_audio
from ltx_core_mlx.utils.memory import aggressive_cleanup
from ltx_core_mlx.utils.positions import (
    compute_audio_positions,
    compute_audio_token_count,
    compute_video_positions,
)

from ltx_pipelines_mlx.a2vid_two_stage import A2VidPipelineTwoStage
from ltx_pipelines_mlx.scheduler import DISTILLED_SIGMAS, STAGE_2_SIGMAS
from ltx_pipelines_mlx.utils.helpers import create_noised_state
from ltx_pipelines_mlx.utils.progress import phase
from ltx_pipelines_mlx.utils.samplers import denoise_loop

_materialize = getattr(mx, "eval")


class A2VidDistilledPipeline(A2VidPipelineTwoStage):
    """Audio-to-video two-stage pipeline using the distilled model.

    Uses the distilled transformer directly — no dev model, no CFG,
    no LoRA fusion. Two-stage: half-res distilled -> upscale -> full-res
    refine. Q4 compatible.

    Args:
        model_dir: Path to model weights or HuggingFace repo ID. Must
            contain the distilled checkpoint (``transformer.safetensors``
            or ``transformer-distilled.safetensors``).
        gemma_model_id: Gemma model for text encoding.
        low_memory: Aggressive memory management.
        low_ram_streaming: Stream transformer blocks from disk.
    """

    def load(self) -> None:
        """Load distilled DiT + upsampler (skip VAE encoder, skip decoders).

        VAE encoder is loaded on demand by ``image_conditioner`` callbacks
        during I2V conditioning and upscale steps — pre-loading it here
        would waste ~800 MB peak memory for no benefit.
        """
        if self._loaded:
            return

        if self.dit is None:
            transformer_path = self.model_dir / "transformer.safetensors"
            if not transformer_path.exists():
                transformer_path = self.model_dir / "transformer-distilled.safetensors"
            self.dit = self._load_transformer_with_optional_streaming(transformer_path)

        if self.upsampler is None:
            self._load_upsampler()
        self._loaded = True

    def generate_and_save(
        self,
        prompt: str,
        output_path: str,
        audio_path: str | Path | None = None,
        height: int = 480,
        width: int = 704,
        num_frames: int = 97,
        *,
        frame_rate: float,
        seed: int = 42,
        stage1_steps: int | None = None,
        stage2_steps: int | None = None,
        image: str | None = None,
        images=None,
        audio_start_time: float = 0.0,
        audio_max_duration: float | None = None,
        audio_conditioning_scale: float = 1.0,
    ) -> str:
        """Generate audio-to-video using the distilled pipeline.

        Two-stage: half-res distilled -> upscale -> full-res refine.
        Audio conditioning from the input file (frozen in stage 1,
        denoised in stage 2). Original audio muxed into output for
        maximum fidelity.

        Args:
            prompt: Text prompt.
            output_path: Path to output video file.
            audio_path: Path to input audio file (required).
            height: Video height.
            width: Video width.
            num_frames: Number of frames.
            frame_rate: Frame rate.
            seed: Random seed.
            stage1_steps: Stage 1 steps (default: full DISTILLED_SIGMAS = 8).
            stage2_steps: Stage 2 steps (default: full STAGE_2_SIGMAS = 3).
            image: Optional reference image for I2V conditioning (first frame).
            audio_start_time: Start time in seconds for audio.
            audio_max_duration: Max audio duration.
            audio_conditioning_scale: Amplify audio tokens before DiT (default: 1.0).
                Higher values increase audio adhesion at the cost of visual flexibility.

        Returns:
            Path to the output video file.
        """
        if self.low_memory:
            mx.set_cache_limit(0)

        if audio_path is None:
            raise ValueError("audio_path is required for A2VidDistilledPipeline")

        if audio_max_duration is None:
            audio_max_duration = num_frames / frame_rate

        # --- Encode audio ---
        self._load_audio_encoder()
        assert self.audio_encoder is not None
        assert self.audio_processor is not None

        audio_data = load_audio(
            audio_path,
            target_sample_rate=16000,
            start_time=audio_start_time,
            max_duration=audio_max_duration,
        )
        if audio_data is None:
            raise ValueError(f"No audio found in {audio_path}")

        audio_latent = encode_audio(
            audio_data.waveform,
            audio_data.sample_rate,
            self.audio_encoder,
            self.audio_processor,
        )

        audio_T = compute_audio_token_count(num_frames, frame_rate)
        audio_latent = audio_latent[:, :, :audio_T, :]
        audio_tokens, _ = self.audio_patchifier.patchify(audio_latent)
        if audio_conditioning_scale != 1.0:
            audio_tokens = audio_tokens * audio_conditioning_scale
        mx.synchronize()
        audio_T_actual = audio_tokens.shape[1]

        if self.low_memory:
            self.audio_conditioner.free()

        # --- Text encoding (positive only, no CFG) ---
        self._load_text_encoder()
        with phase("Encoding prompt", verbose=self.verbose):
            video_embeds, audio_embeds = self._encode_text(prompt)
            _materialize(video_embeds, audio_embeds)
        if self.low_memory:
            self.prompt_encoder.free()
            aggressive_cleanup()

        # --- Load distilled DiT + upsampler (VAE encoder on demand) ---
        self.load()
        assert self.dit is not None
        assert self.upsampler is not None

        # --- Stage 1: half resolution ---
        half_h, half_w = height // 2, width // 2
        F, H_half, W_half = compute_video_latent_shape(num_frames, half_h, half_w)
        video_shape = (1, F * H_half * W_half, 128)

        video_positions_1 = compute_video_positions(F, H_half, W_half, frame_rate=frame_rate)
        audio_positions = compute_audio_positions(audio_T_actual)

        # I2V conditioning at half resolution
        from ltx_pipelines_mlx.utils._orchestration import combined_image_conditionings
        from ltx_pipelines_mlx.utils.args import ImageConditioningInput

        enc_h_half = H_half * 32
        enc_w_half = W_half * 32
        resolved_images = list(images) if images else []
        if image is not None and not resolved_images:
            resolved_images = [ImageConditioningInput(path=image, frame_idx=0, strength=1.0)]
        conditionings_1: list = []
        if resolved_images:

            def _encode_combined(encoder):
                conds = combined_image_conditionings(
                    resolved_images,
                    enc_h=enc_h_half,
                    enc_w=enc_w_half,
                    spatial_dims=(F, H_half, W_half),
                    video_encoder=encoder,
                    frame_rate=frame_rate,
                )
                mx.synchronize()
                return conds

            conditionings_1 = self.image_conditioner(_encode_combined, free_after=self.low_memory)
            if self.low_memory:
                aggressive_cleanup()

        # Video: noisy state. Audio: frozen from input (denoise_mask=0)
        video_state_1 = create_noised_state(
            base_shape=video_shape,
            conditionings=conditionings_1,
            spatial_dims=(F, H_half, W_half),
            positions=video_positions_1,
            seed=seed,
            sigma=1.0,
            initial_latent=None,
            legacy_scalar_blend=True,
        )

        audio_state_1 = LatentState(
            latent=audio_tokens,
            clean_latent=audio_tokens,
            denoise_mask=mx.zeros((1, audio_tokens.shape[1], 1), dtype=mx.bfloat16),
            positions=audio_positions,
        )

        sigmas_1 = DISTILLED_SIGMAS[: stage1_steps + 1] if stage1_steps else DISTILLED_SIGMAS
        x0_model = X0Model(self.dit)

        self._pre_denoise_flush(video_state_1, audio_state_1)
        output_1 = denoise_loop(
            model=x0_model,
            video_state=video_state_1,
            audio_state=audio_state_1,
            video_text_embeds=video_embeds,
            audio_text_embeds=audio_embeds,
            sigmas=sigmas_1,
        )
        if self.low_memory:
            aggressive_cleanup()

        # --- Upscale (denorm/upsample/renorm) ---
        gen_tokens_1 = output_1.video_latent[:, : F * H_half * W_half, :]
        video_half = self.video_patchifier.unpatchify(gen_tokens_1, (F, H_half, W_half))
        H_full = H_half * 2
        W_full = W_half * 2

        def _upscale_and_optionally_encode(encoder):
            v_mlx = video_half.transpose(0, 2, 3, 4, 1)
            v_denorm = encoder.denormalize_latent(v_mlx).transpose(0, 4, 1, 2, 3)
            v_up = self.upsampler(v_denorm)
            v_up_renorm = encoder.normalize_latent(v_up.transpose(0, 2, 3, 4, 1)).transpose(0, 4, 1, 2, 3)
            mx.synchronize()
            conds: list = []
            if resolved_images:
                enc_h_full = H_full * 32
                enc_w_full = W_full * 32
                conds = combined_image_conditionings(
                    resolved_images,
                    enc_h=enc_h_full,
                    enc_w=enc_w_full,
                    spatial_dims=(F, H_full, W_full),
                    video_encoder=encoder,
                    frame_rate=frame_rate,
                )
            return v_up_renorm, conds

        video_upscaled, conditionings_2 = self.image_conditioner(
            _upscale_and_optionally_encode, free_after=self.low_memory
        )
        if self.low_memory:
            self.upsampler = None
            aggressive_cleanup()

        # --- Stage 2: full resolution refine (no LoRA swap, already distilled) ---
        video_tokens, _ = self.video_patchifier.patchify(video_upscaled)

        sigmas_2 = STAGE_2_SIGMAS[: stage2_steps + 1] if stage2_steps else STAGE_2_SIGMAS
        start_sigma = sigmas_2[0]

        video_positions_2 = compute_video_positions(F, H_full, W_full, frame_rate=frame_rate)

        video_state_2 = create_noised_state(
            base_shape=video_tokens.shape,
            conditionings=conditionings_2,
            spatial_dims=(F, H_full, W_full),
            positions=video_positions_2,
            seed=seed + 2,
            sigma=start_sigma,
            initial_latent=video_tokens,
            legacy_scalar_blend=True,
        )

        audio_state_2 = create_noised_state(
            base_shape=audio_tokens.shape,
            conditionings=[],
            spatial_dims=(F, H_full, W_full),
            positions=audio_positions,
            seed=seed + 2,
            sigma=start_sigma,
            initial_latent=audio_tokens,
        )

        self._pre_denoise_flush(video_state_2, audio_state_2)
        output_2 = denoise_loop(
            model=x0_model,
            video_state=video_state_2,
            audio_state=audio_state_2,
            video_text_embeds=video_embeds,
            audio_text_embeds=audio_embeds,
            sigmas=sigmas_2,
        )
        if self.low_memory:
            aggressive_cleanup()

        gen_tokens_2 = output_2.video_latent[:, : F * H_full * W_full, :]
        video_latent = self.video_patchifier.unpatchify(gen_tokens_2, (F, H_full, W_full))

        # --- Decode and save (mux original audio for max fidelity) ---
        # Free DiT before loading decoders — saves ~11 GB peak.
        self.dit = None
        self._loaded = False
        aggressive_cleanup()

        self._load_decoders()

        video_duration = num_frames / frame_rate
        audio_data_48k = load_audio(
            audio_path,
            target_sample_rate=48000,
            start_time=audio_start_time,
            max_duration=video_duration,
        )
        if audio_data_48k is not None:
            max_samples = int(video_duration * 48000)
            waveform_48k = audio_data_48k.waveform[:, :, :max_samples]
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as _tmp:
                temp_audio = _tmp.name
            self._save_waveform(waveform_48k, temp_audio, sample_rate=48000)
        else:
            temp_audio = None

        self.video_decoder_block.decode_and_stream(
            video_latent,
            output_path,
            frame_rate=frame_rate,
            audio_path=temp_audio,
        )

        if temp_audio is not None:
            Path(temp_audio).unlink(missing_ok=True)

        # Free all remaining Metal buffers so 24GB systems recover fully.
        # Without this, VAE decoder (~1 GB), audio decoder + vocoder
        # (~500 MB), and upsampler (~200 MB) stay pinned in Metal memory
        # until process exit.
        self.video_decoder_block.free()
        self.audio_decoder_block.free()
        self.upsampler = None
        self.dit = None
        self._loaded = False
        aggressive_cleanup()

        return output_path


__all__ = ["A2VidDistilledPipeline"]
