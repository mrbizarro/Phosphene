"""Thin CLI wrapper around ``LtxvTrainer`` for image-only character LoRAs.

Why a wrapper instead of using ``ltx-2-mlx`` directly:

- The default ``TextToVideoConfig.first_frame_conditioning_p`` is 0.1. With
  1-frame image samples, when this triggers the entire image is treated as
  conditioning, the loss mask becomes all-zero, ``mean(loss_mask) == 0``, and
  the masked-MSE loss in ``TextToVideoStrategy.compute_loss`` divides by zero
  → NaN. We force it to 0.0 for image-only training.
- We want first/last training-step timing logged to stdout so M3 prints a
  honest per-step number on this machine.

Usage::

    ./scripts/run.sh python -m lora_lab.train --config configs/char_image_lora.yaml
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

import yaml

import ltx_trainer_mlx.model_loader as ml_module
import ltx_trainer_mlx.training_strategies as ts_module
from ltx_trainer_mlx.config import LtxTrainerConfig
from ltx_trainer_mlx.trainer import LtxvTrainer

logger = logging.getLogger(__name__)


def _patch_loader_prefer_dev_transformer() -> None:
    """Force `load_transformer` to pick `transformer-dev.safetensors` when present.

    Default auto-detect order is `transformer.safetensors` → `transformer-distilled.safetensors`
    → `transformer-dev.safetensors`. In the `dgrauet/ltx-2.3-mlx-q4` model dir, the
    distilled file exists, so dev never gets picked.

    Per LTX-2 issue #175 (Lightricks/LTX-2): the distilled checkpoint uses a different
    sigma schedule (8-step DISTILLED_SIGMAS) than the standard flow-matching trainer
    objective. Training a character LoRA against the distilled weights produces gradients
    that point toward the wrong target — coarse features (hair/age/beard) survive the
    mismatch but fine face geometry doesn't. Switching the trainer to the dev transformer
    eliminates the objective mismatch.

    Q4/Q8 sibling fallback (2026-05-18):
        The trainer config hardcodes the Q4 model dir, but on a Q8-only install
        the dev transformer lives at ``<panel>/mlx_models/ltx-2.3-mlx-q8/
        transformer-dev.safetensors`` and the Q4 dir doesn't exist (or has only
        the distilled file). The panel's preflight (_train_required_models in
        mlx_ltx_panel.py) already accepts either location via ``extra_dirs=
        [q8_local_dir]``, but the trainer process — running in a separate venv
        — has to do its own probing. We look at both ``<model_dir>`` and the
        sibling ``ltx-2.3-mlx-q8/`` next to it, and pin to whichever holds a
        **full-precision** dev. If NEITHER exists we raise a clear, actionable
        error instead of letting the loader silently fall back to the distilled
        file — silent fallback was the original bug: identity capture quietly
        broke because the trainer was optimizing against the wrong sigma schedule.

    Precision guard (2026-07-20, #35/#36):
        A 4-bit *quantized* dev transformer (~11 GB) can sit in the Q4 dir and
        pass a bare exists() check — but training against quantized weights also
        yields a near-zero LoRA. So we compare file sizes: a full-precision dev
        is ~19 GB, quantized ~11 GB. We prefer the largest dev ≥ 15 GB and warn
        loudly if a quantized one is shadowing it. Reported by @saved-j in #36.
    """
    from pathlib import Path

    # A full-precision dev transformer is ~19 GB; a 4-bit quantized dev is ~11 GB.
    # Training against the quantized one produces a near-zero LoRA (#35/#36), so we
    # must never pick it while a full-precision dev exists anywhere. 15 GB cleanly
    # separates the two.
    FULL_DEV_MIN_BYTES = 15 * 1000**3

    def _dev_size(p: "Path") -> int:
        try:
            return p.stat().st_size if p.exists() else 0
        except OSError:
            return 0

    _orig = ml_module.load_transformer

    def patched(model_dir, transformer_file=None):
        if transformer_file is None:
            primary = Path(model_dir) / "transformer-dev.safetensors"
            sibling = Path(model_dir).parent / "ltx-2.3-mlx-q8" / "transformer-dev.safetensors"
            p_size, s_size = _dev_size(primary), _dev_size(sibling)

            # Each candidate is (dir_to_pass_to_loader, size_bytes, human_label).
            # The sibling repoints the loader at the Q8 DIR as `model_dir` so
            # `load_split_safetensors` resolves the shards relative to the right
            # snapshot — passing only the basename would still join against the
            # Q4 dir and miss the sibling's shard files.
            candidates = []
            if p_size:
                candidates.append((model_dir, p_size, str(primary.parent)))
            if s_size:
                candidates.append((str(sibling.parent), s_size, str(sibling.parent)))

            full = [c for c in candidates if c[1] >= FULL_DEV_MIN_BYTES]
            if full:
                chosen = max(full, key=lambda c: c[1])
                if p_size and p_size < FULL_DEV_MIN_BYTES:
                    # A quantized dev is shadowing the full one in the Q4 dir —
                    # this is the exact #35/#36 silent failure. Skip it, loudly.
                    logger.warning(
                        "training-side override: IGNORING quantized transformer-dev.safetensors "
                        "(%.1f GB) in %s — it would train a near-zero LoRA (#35/#36). Using the "
                        "full-precision dev (%.1f GB) from %s instead.",
                        p_size / 1e9, primary.parent, chosen[1] / 1e9, chosen[2],
                    )
                else:
                    logger.info(
                        "training-side override: using full-precision transformer-dev.safetensors "
                        "(%.1f GB) from %s (not distilled)",
                        chosen[1] / 1e9, chosen[2],
                    )
                return _orig(chosen[0], transformer_file="transformer-dev.safetensors")

            if candidates:
                # Only a suspiciously small (likely quantized) dev exists anywhere.
                # Better than the distilled base, but identity capture will be weak —
                # use it and shout so the user knows to re-download the full dev.
                chosen = max(candidates, key=lambda c: c[1])
                logger.warning(
                    "training-side override: only a small transformer-dev.safetensors (%.1f GB) "
                    "found at %s — expected the full-precision dev (~19 GB). Character identity "
                    "capture will be weak; re-download it via Train Character -> Preflight.",
                    chosen[1] / 1e9, chosen[2],
                )
                return _orig(chosen[0], transformer_file="transformer-dev.safetensors")

            raise FileNotFoundError(
                f"transformer-dev.safetensors not found in {primary.parent} "
                f"or {sibling.parent}. Open Phosphene → Train Character → "
                f"Preflight to download the full-precision dev transformer (~19 GB)."
            )
        return _orig(model_dir, transformer_file=transformer_file)

    ml_module.load_transformer = patched


def _patch_compute_video_positions_fps_kwarg() -> None:
    """Translate ``fps=`` → ``frame_rate=`` across the trainer.

    Upstream rename 2026-05-14 (same family as the rename in
    ``_decode_and_save_video``): ``ltx_core_mlx.utils.positions`` renamed
    ``fps`` → ``frame_rate`` on both ``compute_video_positions`` and
    ``compute_audio_token_count``. The trainer still calls with ``fps=``
    in multiple places (base_strategy, trainer, validation_sampler,
    text_to_video, video_to_video), so training raises
    ``TypeError: ... unexpected keyword argument 'fps'`` at the first
    training step.

    We wrap both functions to accept either kwarg name, then rebind the
    symbols in every trainer-side module that imported them by name.
    Lora-lab CLAUDE rule 1 says don't modify phosphene's tree — so we
    monkey-patch at runtime instead.
    """
    import ltx_core_mlx.utils.positions as _pos

    _orig_vid = _pos.compute_video_positions
    _orig_aud = _pos.compute_audio_token_count

    def patched_video(num_frames, height, width, *, fps=None, frame_rate=None):
        if frame_rate is None:
            frame_rate = fps if fps is not None else 24.0
        return _orig_vid(num_frames=num_frames, height=height, width=width,
                          frame_rate=frame_rate)

    def patched_audio(num_video_frames, *, fps=None, frame_rate=None):
        if frame_rate is None:
            frame_rate = fps if fps is not None else 24.0
        return _orig_aud(num_video_frames=num_video_frames, frame_rate=frame_rate)

    # Patch the source module.
    _pos.compute_video_positions = patched_video
    _pos.compute_audio_token_count = patched_audio

    # Rebind in every trainer-side module that imported the names directly.
    _consumer_modules = (
        "ltx_trainer_mlx.trainer",
        "ltx_trainer_mlx.validation_sampler",
        "ltx_trainer_mlx.training_strategies.base_strategy",
        "ltx_trainer_mlx.training_strategies.text_to_video",
        "ltx_trainer_mlx.training_strategies.video_to_video",
    )
    import importlib
    for name in _consumer_modules:
        try:
            mod = importlib.import_module(name)
        except ImportError:
            continue
        if hasattr(mod, "compute_video_positions"):
            mod.compute_video_positions = patched_video
        if hasattr(mod, "compute_audio_token_count"):
            mod.compute_audio_token_count = patched_audio


def _patch_lora_target_exclude_audio() -> None:
    """Exclude audio attention/FF layers from LoRA target matching.

    The trainer's ``_find_lora_targets`` (trainer.py:917) does suffix matching:
    it splits each module path by ``.`` and checks if ANY part is in
    ``target_modules``. With the canonical face-LoRA target_modules
    ``[to_q, to_k, to_v, to_out]``, the matcher catches both
    ``transformer_blocks.N.attn1.to_q`` (video attention — wanted) AND
    ``transformer_blocks.N.audio_attn1.to_q`` (audio attention — unwanted).

    Training on still images with no audio signal pushes random gradients
    into the audio attention layers. The saved face LoRA then carries those
    random "audio_attn" deltas. When stacked with the audio LoRA at inference,
    those random deltas corrupt the model's audio attention path → lips don't
    animate during dialogue, voice is distorted. Discovered 2026-05-15 on
    Aria v2 — face LoRA had 1152 audio_attn deltas that broke lip-sync until
    they were filtered out post-hoc.

    Fix: wrap ``_find_lora_targets`` so any path containing an ``audio_*``
    submodule is skipped at training time. The LoRA never includes those
    weights, never wastes gradient updates on them, and the saved file is
    clean by construction (no post-hoc filter needed).

    Lora-lab CLAUDE rule 1 says don't mutate phosphene's tree — so we
    monkey-patch at runtime instead.
    """
    import ltx_trainer_mlx.trainer as _trainer
    _orig = _trainer._find_lora_targets

    EXCLUDE_SUBSTRINGS = ("audio_attn", "audio_ff")

    def patched(model, target_names):
        results = _orig(model, target_names)
        filtered = [(path, mod) for (path, mod) in results
                    if not any(s in path for s in EXCLUDE_SUBSTRINGS)]
        dropped = len(results) - len(filtered)
        if dropped:
            logger.info(
                "lora target filter: kept %d video targets, "
                "dropped %d audio targets (audio_attn / audio_ff)",
                len(filtered), dropped,
            )
        return filtered

    _trainer._find_lora_targets = patched


def _patch_strategy_for_image_only() -> None:
    """Force `first_frame_conditioning_p=0.0` for image-only training.

    The Pydantic config doesn't expose this knob; intercept the factory to
    rebuild the strategy config with our override.
    """
    _orig = ts_module.get_training_strategy

    def patched(config):
        name = getattr(config, "name", None)
        if name == "text_to_video":
            generate_audio = bool(getattr(config, "generate_audio", False))
            native_cfg = ts_module.TextToVideoConfig(
                first_frame_conditioning_p=0.0,
                with_audio=generate_audio,
            )
            strategy = ts_module.TextToVideoStrategy(native_cfg)
            logger.info(
                "image-only override: first_frame_conditioning_p=0.0, with_audio=%s",
                generate_audio,
            )
            return strategy
        return _orig(config)

    ts_module.get_training_strategy = patched


def main() -> int:
    p = argparse.ArgumentParser(description="LTX-2 image-only character LoRA trainer")
    p.add_argument("--config", required=True, help="path to YAML config (see configs/char_image_lora.yaml)")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s | %(message)s",
    )

    cfg_path = Path(args.config).resolve()
    with cfg_path.open() as f:
        cfg_dict = yaml.safe_load(f)

    config = LtxTrainerConfig.model_validate(cfg_dict)

    _patch_loader_prefer_dev_transformer()
    _patch_strategy_for_image_only()
    _patch_compute_video_positions_fps_kwarg()
    _patch_lora_target_exclude_audio()

    t0 = time.time()
    trainer = LtxvTrainer(config)
    print(f"trainer init complete in {time.time() - t0:.1f}s")

    t0 = time.time()
    saved_path, stats = trainer.train(disable_progress_bars=False)
    print(f"\ntraining wall: {time.time() - t0:.1f}s")
    print(f"checkpoint: {saved_path}")
    print(f"per-step: {1.0 / stats.steps_per_second:.2f}s  ({stats.steps_per_second:.3f} steps/s)")
    print(f"peak memory: {stats.peak_memory_gb:.2f} GB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
