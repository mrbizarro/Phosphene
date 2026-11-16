#!/usr/bin/env python3
"""Restyle a picture on the Hailuo H3 engine: the same scene back in a named style (R&D L55, 15 Nov 2026).

One picture in, one picture out. Runs in the H3 pack's own interpreter against the INSTALLED engine
checkout (``--engine-root``): nothing here is new model code, only the engine's own modules in a new
order, so the engine pin does not move.

    python3 scripts/h3_restyle.py "A clay figurine ..." -o out.png --content photo.png \
        --engine-root <H3 checkout> --dit <Q8 DiT dir> --text-dir <ddalcu-q8> \
        --text-config <upstream text_encoder/config.json> --decoder <p-h3-image/vae> \
        --lora <restyle adapter> [--lora-scale 1.3] [--steps 16] [--mp 1.0] [--seed 7] [--metrics m.json]

The recipe, as measured (research/lab/L55.md): the content picture is the ONLY keyframe anchor (FL2VA's
``first`` slot, held at its training noise level); the style comes from the prompt, never from a second
picture (a second anchor makes the model copy one of them); one latent target frame plus two throw-away
audio latents; 16 sigma points with the video shift (15 forwards, no guidance); the rank-16 restyle
adapter at run time, scale 1.0-1.3; decoded by the P-H3-Image single-frame decoder (the video VAE
cannot decode one latent frame). About 2 minutes per 1 MP picture on an M4 Max, 27.6 GB peak.

The adapter is published in ComfyUI naming (``lora_unet_blocks_N_attn_qkv_proj.lora_down.weight``,
fused qkv rows contiguous q|k|v). Our DiT names modules ``blocks.N.attn.qkv_proj`` and reads qkv rows
per head, so on first use the file is re-keyed once beside itself (``<name>.ourkeys.safetensors``) and
applied with the engine's qkv row permute (row-norm F-test 160 vs 3.7: the contiguous layout is the
true one for this file). A file already in our naming is used as-is.

Progress lines ("step 3/15", "wrote ...") go to stdout for the Studio's render log. Exit 0 with the
PNG and the metrics JSON written; any other exit is a failure the Studio reports.
"""
from __future__ import annotations

import argparse
import gc
import json
import os
import sys
import time
from pathlib import Path

NUM_AUDIO_LATENTS = 2
LATENT_CHANNELS = 24
RATIO = 16
COMFY_PREFIX = "lora_unet_blocks_"
COMFY_MODULES = {"attn_qkv_proj": "attn.qkv_proj", "attn_out_proj": "attn.out_proj",
                 "mlp_fc1": "mlp.fc1", "mlp_fc2": "mlp.fc2"}
COMFY_SUFFIX = {"lora_down.weight": "lora_A.weight", "lora_up.weight": "lora_B.weight"}


def _release() -> None:
    import mlx.core as mx
    gc.collect()
    mx.clear_cache()


def canvas_for(width: int, height: int, megapixels: float) -> tuple[int, int]:
    """The content's shape at about ``megapixels``, both sides multiples of 32 (two latent patches)."""
    scale = (float(megapixels) * 1e6 / float(width * height)) ** 0.5
    w = max(32, int(round(width * scale / 32)) * 32)
    h = max(32, int(round(height * scale / 32)) * 32)
    return w, h


def load_image(path: str):
    from PIL import Image, ImageOps
    return ImageOps.exif_transpose(Image.open(path).convert("RGB"))


def prepare_adapter(path: Path, log=print) -> tuple[Path, bool]:
    """Return (file to apply, permute_qkv). A ComfyUI export is re-keyed once into our names beside
    itself; the engine's qkv row permute is then required (L55). Our own naming is used as-is, with
    the permute only when the file's metadata declares the ComfyUI layout."""
    import mlx.core as mx
    import struct
    with open(path, "rb") as fh:
        n = struct.unpack("<Q", fh.read(8))[0]
        head = json.loads(fh.read(n))
    keys = [k for k in head if k != "__metadata__"]
    meta = head.get("__metadata__") or {}
    if not any(k.startswith(COMFY_PREFIX) for k in keys):
        declared = str(meta.get("converted_layout") or "")
        return path, declared.startswith("comfyui")
    out = path.with_name(path.stem + ".ourkeys.safetensors")
    if out.is_file():
        try:
            with open(out, "rb") as fh:
                m = struct.unpack("<Q", fh.read(8))[0]
                ohead = json.loads(fh.read(m))
            if (ohead.get("__metadata__") or {}).get("rekeyed_from_bytes") == str(path.stat().st_size):
                return out, True
        except (OSError, ValueError, struct.error):
            pass
    log(f"re-keying {path.name} into the engine's module names (once)")
    raw = mx.load(str(path))
    rekeyed = {}
    alphas = set()
    rank = None
    for k, v in raw.items():
        if k.endswith(".alpha"):
            alphas.add(float(v.item()) if hasattr(v, "item") else float(v))
            continue
        name, _, tail = k.partition(".")
        if not name.startswith(COMFY_PREFIX):
            raise SystemExit(f"restyle adapter: unexpected tensor name {k!r}")
        idx, _, mod = name[len(COMFY_PREFIX):].partition("_")
        if mod not in COMFY_MODULES or tail not in COMFY_SUFFIX:
            raise SystemExit(f"restyle adapter: unexpected tensor name {k!r}")
        rekeyed[f"blocks.{idx}.{COMFY_MODULES[mod]}.{COMFY_SUFFIX[tail]}"] = v
        if tail == "lora_down.weight":
            rank = int(v.shape[0])
    if rank is None or not rekeyed:
        raise SystemExit("restyle adapter: no LoRA pairs in the file")
    if alphas and any(abs(a - rank) > 1e-6 for a in alphas):
        # The engine applies `scale` as given (alpha / rank is folded into the file by its author).
        raise SystemExit(f"restyle adapter: alpha {sorted(alphas)} differs from rank {rank}; refusing to guess the scale")
    # mx.save_safetensors appends ".safetensors" to any other name, so the temp file keeps the suffix.
    tmp = out.with_name(out.stem + ".partial.safetensors")
    mx.save_safetensors(str(tmp), rekeyed, metadata={
        "rekeyed_from": path.name, "rekeyed_from_bytes": str(path.stat().st_size),
        "rekeyed_by": "phosphene scripts/h3_restyle.py", "rank": str(rank), "alpha": str(rank),
        "source_layout": "comfyui_minimax_h3", "qkv_rows": "permute at load",
    })
    os.replace(tmp, out)
    log(f"re-keyed {len(rekeyed)} tensors, rank {rank} -> {out.name}")
    return out, True


def main() -> int:
    ap = argparse.ArgumentParser(description="Restyle one picture on the H3 engine (content kept, style from the prompt).")
    ap.add_argument("prompt", help="the result, in words; name the style plainly ('a clay figurine ...')")
    ap.add_argument("-o", "--output", required=True)
    ap.add_argument("--content", required=True, help="the picture to restyle (its content is kept)")
    ap.add_argument("--engine-root", required=True, help="the installed H3 engine checkout (minimax_h3_mlx/ inside)")
    ap.add_argument("--dit", required=True, help="the H3 FL2VA Q8 DiT folder")
    ap.add_argument("--text-dir", required=True, help="the H3 Q8 components folder (text_encoder, video_vae)")
    ap.add_argument("--text-config", required=True, help="upstream text_encoder/config.json")
    ap.add_argument("--decoder", required=True, help="the P-H3-Image vae folder (decoder.safetensors + config.json)")
    ap.add_argument("--lora", required=True, help="the restyle adapter (as published, or already in our names)")
    ap.add_argument("--lora-scale", type=float, default=1.3)
    ap.add_argument("--steps", type=int, default=16, help="sigma points (forwards = steps - 1)")
    ap.add_argument("--shift", type=float, default=12.0)
    ap.add_argument("--mp", type=float, default=1.0, help="output megapixels, at the content's shape")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--metrics", default=None)
    args = ap.parse_args()

    engine_root = Path(args.engine_root).resolve()
    if not (engine_root / "minimax_h3_mlx").is_dir():
        print(f"no H3 engine at {engine_root}", file=sys.stderr)
        return 2
    sys.path.insert(0, str(engine_root))

    import mlx.core as mx
    import numpy as np
    from PIL import Image
    from minimax_h3_mlx.adaln import ModulationCache, drop_adaln_weights
    from minimax_h3_mlx.packing import (AUDIO_CHANNELS, KEYFRAME_NOISE_AUG, build_packed_sequence,
                                        build_row_timesteps, patchify_video_latents, unpatchify_video_tokens)
    from minimax_h3_mlx.scheduler import MiniMaxH3Scheduler

    t_all = time.perf_counter()
    forwards = max(1, int(args.steps) - 1)
    metrics: dict = {"engine": "h3-restyle", "lora": str(args.lora), "lora_scale": float(args.lora_scale),
                     "steps": int(args.steps), "forwards": forwards, "shift": float(args.shift),
                     "seed": int(args.seed), "mp": float(args.mp), "content": str(args.content)}

    content = load_image(args.content)
    width, height = canvas_for(content.width, content.height, args.mp)
    metrics.update(width=width, height=height)
    print(f"restyle: {content.width}x{content.height} -> {width}x{height}, {forwards} forwards", flush=True)

    lora_path, permute = prepare_adapter(Path(args.lora), log=lambda s: print(f"restyle: {s}", flush=True))
    metrics.update(lora_applied=str(lora_path), permute_qkv=bool(permute))

    # 1. Text: prompt only (no vision tokens), one encoder, released before the DiT.
    from minimax_h3_mlx.text_encoder import MiniMaxH3TextEncoder
    t0 = time.perf_counter()
    encoder = MiniMaxH3TextEncoder(Path(args.text_dir), dtype=mx.bfloat16, load_vision=False, verbose=False,
                                   config_path=Path(args.text_config))
    embeds, tags = encoder.encode(args.prompt)
    mx.eval(embeds)
    tags = np.asarray(tags)
    del encoder
    _release()
    metrics["text_s"] = time.perf_counter() - t0
    print(f"text encoded in {metrics['text_s']:.1f} s", flush=True)

    # 2. The content picture as the one keyframe anchor (FL2VA `first` slot), encoder-only video VAE.
    from minimax_h3_mlx.pipeline import encode_keyframe_rows
    from minimax_h3_mlx.vae_stream import load_compact_video_vae_encoder_only
    t0 = time.perf_counter()
    vae_enc = load_compact_video_vae_encoder_only(Path(args.text_dir) / "video_vae.safetensors")
    patch = (1, 2, 2)
    anchors = ("first",)
    cond_rows = encode_keyframe_rows(vae_enc, [content], height, width, patch)
    mx.eval(cond_rows)
    del vae_enc
    _release()
    metrics["keyframe_s"] = time.perf_counter() - t0
    print(f"content encoded in {metrics['keyframe_s']:.1f} s ({int(cond_rows.shape[0])} condition rows)", flush=True)

    # 3. DiT + the restyle adapter at run time; one trajectory.
    from minimax_h3_mlx.load import load_dit
    from minimax_h3_mlx.lora import apply_loras
    t0 = time.perf_counter()
    dit = load_dit(Path(args.dit), verbose=False)
    metrics["dit_load_s"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    reports = apply_loras(dit, [(lora_path, float(args.lora_scale))], mode="runtime", verbose=False,
                          permute_qkv=bool(permute), swap_fc1=False)
    summary = reports[0].summary() if reports else {}
    metrics["lora_apply_s"] = time.perf_counter() - t0
    metrics["lora_report"] = summary
    applied = int(summary.get("applied", 0) or 0) if isinstance(summary, dict) else 0
    print(f"adapter applied to {applied} modules at scale {args.lora_scale:g}", flush=True)
    if applied == 0:
        print("the restyle adapter matched no module of this engine; refusing to render a plain copy", file=sys.stderr)
        return 3

    video_sched = MiniMaxH3Scheduler(shift=float(args.shift))
    audio_sched = MiniMaxH3Scheduler(shift=3.0)
    video_sched.set_timesteps(int(args.steps))
    audio_sched.set_timesteps(int(args.steps))
    lh, lw = height // RATIO, width // RATIO
    layout = build_packed_sequence(tags, 1, lh, lw, NUM_AUDIO_LATENTS, patch, anchors)
    n_cond_v = layout.num_condition_video_rows
    per_step = []
    for t, at in zip(video_sched.timesteps.tolist(), audio_sched.timesteps.tolist()):
        distinct, inverse = build_row_timesteps(layout, float(t), float(at), max(float(t), KEYFRAME_NOISE_AUG), 1.0)
        per_step.append((np.array(distinct), np.array(inverse)))
    table = sorted({float(v) for d, _ in per_step for v in d})
    lookup = {v: i for i, v in enumerate(table)}
    plan = [mx.array(np.array([lookup[float(v)] for v in d], dtype=np.int32)[inv].astype(np.int32))
            for d, inv in per_step]
    timestep_table = mx.array(np.array(table, dtype=np.float32))
    t0 = time.perf_counter()
    cache = ModulationCache.build(dit, timestep_table, dtype=mx.bfloat16)
    drop_adaln_weights(dit)
    mx.eval(dit.parameters())
    metrics["adaln_cache_s"] = time.perf_counter() - t0

    # Noise in the reference's draw order: conditioning noise, then video, then audio.
    mx.random.seed(int(args.seed))
    cond_noise = mx.random.normal(cond_rows.shape).astype(mx.float32)
    rows = video_sched.scale_noise(cond_rows, KEYFRAME_NOISE_AUG, cond_noise)
    video_rows = patchify_video_latents(mx.random.normal((1, LATENT_CHANNELS, 1, lh, lw)).astype(mx.float32), patch)
    audio_rows = mx.random.normal((NUM_AUDIO_LATENTS * AUDIO_CHANNELS, dit.config.audio_latents_dim)).astype(mx.float32)
    video_rows = mx.concatenate([rows, video_rows])
    emb = embeds.astype(mx.bfloat16)
    print(f"{layout.sequence_length:,} rows ({len(tags)} text, {n_cond_v} condition)", flush=True)

    times = []
    steps_t = video_sched.timesteps.tolist()
    for i, t in enumerate(steps_t):
        s0 = time.perf_counter()
        video_pred, audio_pred = dit(
            video_rows[None].astype(mx.bfloat16), audio_rows[None].astype(mx.bfloat16), emb,
            timestep_table, plan[i], layout.token_tags, layout.position_ids,
            layout.video_indices, layout.audio_indices, layout.text_indices,
            modulation_cache=cache,
        )
        stepped = video_sched.step(video_pred[0, n_cond_v:].astype(mx.float32), float(t), video_rows[n_cond_v:])
        audio_rows = audio_sched.step(audio_pred[0].astype(mx.float32), float(audio_sched.timesteps[i].item()), audio_rows)
        video_rows = mx.concatenate([video_rows[:n_cond_v], stepped])
        mx.eval(video_rows, audio_rows)
        times.append(time.perf_counter() - s0)
        print(f"step {i + 1}/{len(steps_t)} ({times[-1]:.1f} s)", flush=True)
    lat = unpatchify_video_tokens(video_rows[n_cond_v:], 1, lh, lw, LATENT_CHANNELS, patch)
    mx.eval(lat)
    if not bool(mx.all(mx.isfinite(lat)).item()):
        print("the denoised picture is not finite (NaN); nothing written", file=sys.stderr)
        return 4
    metrics.update(denoise_s=sum(times), step_s=times, rows=int(layout.sequence_length),
                   peak_after_dit_gb=mx.get_peak_memory() / 1e9)
    del dit, cache
    _release()

    # 4. Decode the one frame with the P-H3-Image single-frame decoder.
    from minimax_h3_mlx.ph3_image_vae import decode_to_uint8, load_image_vae
    t0 = time.perf_counter()
    decoder_dir = Path(args.decoder)
    vae = load_image_vae(decoder_dir / "decoder.safetensors", json.loads((decoder_dir / "config.json").read_text()))
    img = decode_to_uint8(vae, lat)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img).save(out)
    metrics["decode_s"] = time.perf_counter() - t0
    del vae
    _release()
    metrics["request_seconds"] = time.perf_counter() - t_all
    metrics["total_seconds"] = metrics["request_seconds"]
    metrics["peak_gb"] = mx.get_peak_memory() / 1e9
    metrics["width"], metrics["height"] = int(img.shape[1]), int(img.shape[0])
    if args.metrics:
        Path(args.metrics).write_text(json.dumps(metrics, indent=1))
    print(f"wrote {out} {img.shape[1]}x{img.shape[0]} in {metrics['request_seconds']:.0f} s, peak {metrics['peak_gb']:.1f} GB", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
