#!/usr/bin/env python3
"""Run the installed H3 runner with a voice sample: H3's own reference-audio conditioning.

Usage: <h3 venv python> scripts/h3_voice_run.py <h3 checkout> <canvas w> <canvas h>
       --voice FILE [--voice-seconds S] [--voice-label-only] <runner arguments...>

MiniMax H3's reference model takes up to three audio references next to its pictures
(``packing_ref2va.build_ref2va_packed_sequence`` upstream). An audio reference is:

* labelled ``"<Audio j>: "`` in the text presentation, after the picture blocks, before the
  prompt (it never reaches the vision tower);
* encoded by the audio VAE (posterior mean, normalised), stereo at 32 kHz, cut to the target's
  own length, packed channel-major as clean conditioning rows (t = 1.0);
* placed on the shared rotary clock right after the pictures, one unit per latent, at the two
  extremes of the target's width grid; the target audio and video then start after it.

The installed runner already packs clean audio conditioning rows ahead of the target audio (the
"carried sound" of a chained window, at t = 1.0, posterior mean): this wrapper feeds the voice in
through that path and moves the target one voice-length later on the clock, so the layout equals
the upstream one. Pictures keep References' sizing (canvas area, own aspect), like
``h3_refs_run.py``. Nothing in the engine checkout changes; the installed engine stays the pinned
one. One window only (5 s): the voice is not carried into chained windows.
"""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

import numpy as np

RATE = 32000
HOP = 800  # audio VAE hop at 32 kHz: 40 latents a second
FPS = 24


def _pop(argv: list[str], flag: str, has_value: bool = True):
    if flag not in argv:
        return None
    i = argv.index(flag)
    if has_value:
        value = argv[i + 1]
        del argv[i:i + 2]
        return value
    del argv[i]
    return True


def read_voice(path: str, seconds: float) -> np.ndarray:
    """The voice as (2, samples) float32 at 32 kHz, cut to whole latents, level set to -20 dBFS RMS."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "2", "-ar", str(RATE), "-f", "f32le", "-"],
        check=True, capture_output=True).stdout
    wave = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2).T.copy()
    wave = wave[:, : int(seconds * RATE)]
    wave = wave[:, : (wave.shape[1] // HOP) * HOP]
    if wave.shape[1] < 2 * RATE:
        raise SystemExit(f"the voice sample must hold at least 2 seconds of sound, got {wave.shape[1] / RATE:.1f}")
    rms = float(np.sqrt(np.mean(wave ** 2))) or 1.0
    wave = np.clip(wave * (10 ** (-20 / 20) / rms), -1.0, 1.0).astype(np.float32)
    return wave


def shift_for_voice(layout, n_text: int, patch, n_voice: int, reference_grids=(),
                    num_latent_frames: int | None = None) -> tuple[float, int]:
    """Move the target (and any keyframe rows) ``n_voice`` units later on the rotary clock.

    The runner placed the voice as carried sound at the media origin (right after the picture
    slots) and the target at that same origin; upstream ref2va puts the target after the voice.
    Picture reference rows keep their own slots. Returns the voice origin and its row count.
    """
    import mlx.core as mx
    _, ph, pw = patch
    n_ref_rows = sum((int(rh) // ph) * (int(rw) // pw) for rh, rw in reference_grids)
    pos = np.array(layout.position_ids.astype(mx.float32), dtype=np.float64)
    video_idx = np.array(layout.video_indices.tolist(), dtype=np.int64)
    audio_idx = np.array(layout.audio_indices.tolist(), dtype=np.int64)
    moved = np.concatenate([video_idx[video_idx >= n_text + n_ref_rows],
                            audio_idx[layout.num_condition_audio_rows:]])
    pos[moved, 0] += float(n_voice)
    if num_latent_frames:
        # The target frames' times carry 5/3 fractions: rebuild them in float64 (upstream builds the
        # grid in float64 and casts once) so the float32 grid is bit-equal, not one ulp off.
        from minimax_h3_mlx.packing import _temporal_position_grid
        target_video = video_idx[layout.num_condition_video_rows:]
        per_frame = len(target_video) // int(num_latent_frames)
        origin_t = float(n_text) + float(len(reference_grids)) + float(n_voice)
        pos[target_video, 0] = np.repeat(_temporal_position_grid(int(num_latent_frames), origin_t), per_frame)
    voice_rows = audio_idx[: layout.num_condition_audio_rows]
    origin = float(n_text) + float(len(reference_grids))
    if abs(pos[voice_rows, 0].min() - origin) > 1e-3:
        raise RuntimeError(f"voice rows start at {pos[voice_rows, 0].min()}, expected {origin}")
    layout.position_ids = mx.array(pos.astype(np.float32))
    return origin, int(len(voice_rows))


def main() -> None:
    if len(sys.argv) < 4:
        raise SystemExit("usage: h3_voice_run.py <h3 checkout> <canvas w> <canvas h> --voice FILE <runner args...>")
    engine, cw, ch = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    rest = sys.argv[4:]
    voice_path = _pop(rest, "--voice")
    voice_seconds = _pop(rest, "--voice-seconds")
    label_only = bool(_pop(rest, "--voice-label-only", has_value=False))  # A/B: the label without the rows
    # A text cache keyed on the prompt alone would hand back an encoding without the voice label.
    _pop(rest, "--draft-cache-dir")
    _pop(rest, "--prompt-cache")
    frames = int(rest[rest.index("--frames") + 1]) if "--frames" in rest else 124
    seconds = float(voice_seconds) if voice_seconds else frames / FPS

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, here)
    from h3_refs import match_size
    sys.path.remove(here)
    sys.path.insert(0, engine)
    os.chdir(engine)
    import minimax_h3_mlx.reference as reference
    from minimax_h3_mlx.config import TAG_TEXT
    from minimax_h3_mlx.text_encoder import MiniMaxH3TextEncoder

    def prepare(path, short_edge=2048):  # noqa: ARG001 - References' sizing wins, as in h3_refs_run
        from PIL import Image, ImageOps
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        w, h = match_size(im.size[0], im.size[1], cw, ch)
        print(f"[h3-voice] picture {os.path.basename(str(path))} {im.size[0]}x{im.size[1]} -> {w}x{h}", flush=True)
        return im if im.size == (w, h) else im.resize((w, h), Image.Resampling.LANCZOS)

    reference.prepare_reference_image = prepare

    voice = read_voice(voice_path, seconds) if voice_path else None
    state = {"armed": False, "latents": 0}
    if voice is not None:
        print(f"[h3-voice] voice {os.path.basename(voice_path)}: {voice.shape[1] / RATE:.2f} s, "
              f"{voice.shape[1] // HOP} latents{' (label only)' if label_only else ''}", flush=True)

    # 1. The prompt presentation: "<Audio 1>: " after the picture blocks, right before the prompt.
    original_request = MiniMaxH3TextEncoder.build_request

    def build_request(self, prompt, images=None, label_format="<Picture {n}>: "):
        import mlx.core as mx
        ids, tags, vision = original_request(self, prompt, images, label_format)
        if not state["armed"]:
            return ids, tags, vision
        n_prompt = len(self.tokenizer(prompt, add_special_tokens=False)["input_ids"])
        label = self.tokenizer("<Audio 1>: ", add_special_tokens=False)["input_ids"]
        flat = np.array(ids).reshape(-1).tolist()
        cut = len(flat) - n_prompt
        flat = flat[:cut] + label + flat[cut:]
        tags = np.concatenate([tags[:cut], np.full(len(label), TAG_TEXT, dtype=tags.dtype), tags[cut:]])
        print(f"[h3-voice] presentation: '<Audio 1>: ' ({len(label)} tokens) before the prompt", flush=True)
        return mx.array(np.array([flat], dtype=np.int32)), tags, vision

    MiniMaxH3TextEncoder.build_request = build_request

    spec = importlib.util.spec_from_file_location("h3_generate_staged", os.path.join(engine, "scripts", "generate_staged.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["h3_generate_staged"] = mod
    spec.loader.exec_module(mod)

    # 2. The voice rows ride the runner's clean audio-conditioning path (first window only).
    original_window = mod.render_window

    def render_window(*args, **kwargs):
        cond = kwargs.get("cond")
        armed = (voice is not None and cond is not None and cond.history_audio is None
                 and cond.history_frames is None and kwargs.get("window_index", 1) == 1)
        state["armed"] = armed
        if armed and not label_only:
            cond.history_audio = voice
            state["latents"] = voice.shape[1] // HOP
        else:
            state["latents"] = 0
        try:
            return original_window(*args, **kwargs)
        finally:
            state["armed"] = False

    mod.render_window = render_window

    # 3. The clock: voice right after the pictures, the target one voice-length later.
    original_pack = mod.build_packed_sequence

    def build_packed_sequence(text_tags, num_latent_frames, latent_h, latent_w, audio_latents, patch,
                              anchors=(), **kw):
        import mlx.core as mx
        layout = original_pack(text_tags, num_latent_frames, latent_h, latent_w, audio_latents, patch,
                               anchors, **kw)
        n_voice = state["latents"]
        if not n_voice or kw.get("history_latent_frames", 0):
            return layout
        if int(kw.get("history_audio_latents", 0)) != n_voice or float(kw.get("history_audio_offset", 0.0)):
            raise RuntimeError(f"voice rows {kw.get('history_audio_latents')} / offset "
                               f"{kw.get('history_audio_offset')} do not match the {n_voice}-latent voice")
        origin, n_rows = shift_for_voice(layout, len(text_tags), patch, n_voice, kw.get("reference_grids", ()),
                                         num_latent_frames)
        print(f"[h3-voice] layout: {n_rows} voice rows at t={origin:.0f}.."
              f"{origin + n_voice - 1:.0f}; target moved +{n_voice}", flush=True)
        return layout

    mod.build_packed_sequence = build_packed_sequence

    sys.argv = [os.path.join(engine, "scripts", "generate_staged.py")] + rest
    raise SystemExit(mod.main())


if __name__ == "__main__":
    main()
