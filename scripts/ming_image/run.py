#!/usr/bin/env python3
"""Phosphene CLI runner for Ming-Image-0.2-Design (one image per invocation).

Progress protocol (stdout, flushed):
    [ming-image] stage <name>
    [ming-image] step <i/N> (<s/step>)
    [mlx] <stage>: peak <X> GiB · active <Y> GiB · cache <Z> GiB
    [ming-image] done <seconds> <path>
    [ming-image] error <message>

Exit codes: 0 ok · 2 bad input · 130 stopped · 1 other.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import mlx.core as mx

from . import RUNNER_VERSION, pins
from .models import MingImageEngine


def _say(*parts: object) -> None:
    print("[ming-image]", *parts, flush=True)


def _emit_mlx_stage(stage: str) -> dict[str, float]:
    peak = mx.get_peak_memory() / (1024 ** 3)
    active = mx.get_active_memory() / (1024 ** 3)
    cache = mx.get_cache_memory() / (1024 ** 3)
    print(
        f"[mlx] {stage}: peak {peak:.2f} GiB · active {active:.2f} GiB · cache {cache:.2f} GiB",
        flush=True,
    )
    return {"peak_gib": round(peak, 3), "active_gib": round(active, 3), "cache_gib": round(cache, 3)}


def _system_ram_gb() -> float:
    env = os.environ.get("MING_SIMULATE_RAM_GB") or os.environ.get("H3_SIMULATE_RAM_GB")
    if env:
        try:
            return float(env)
        except ValueError:
            pass
    try:
        out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True).strip()
        return int(out) / (1024 ** 3)
    except Exception:
        return 32.0


def choose_low_memory_mode(requested: str, ram_gb: float, width: int, height: int) -> str:
    req = (requested or "auto").strip().lower()
    if req in ("off", "llm", "full"):
        return req
    # auto policy (measured Stage 4b fit pass on M4 Max 64 GB):
    # - <= 18.5 GB Mac (16 GB tier): stream both LLM and DiT ("full") -> 2.59 GiB MLX / 4.96 GiB footprint
    # - > 18.5 GB Mac (24/32/48/64 GB+): stream LLM ("llm") as the default — bit-for-bit identical to
    #   resident ("off"), 3.4 s faster (70.5 vs 73.9 s), and cuts peak 18.93 -> 12.22 GiB MLX / 14.30 GiB footprint.
    if ram_gb <= 18.5:
        return "full"
    return "llm"


def _apply_memory_caps(memory_gb: float | None, wired_gb: float | None) -> None:
    if memory_gb is not None and memory_gb > 0:
        limit_bytes = int(memory_gb * (1024 ** 3))
        if hasattr(mx, "set_memory_limit"):
            mx.set_memory_limit(limit_bytes)
        if hasattr(mx, "set_cache_limit"):
            mx.set_cache_limit(min(limit_bytes // 4, 2 * (1024 ** 3)))
    if wired_gb is not None and wired_gb > 0 and hasattr(mx, "set_wired_limit"):
        try:
            mx.set_wired_limit(int(wired_gb * (1024 ** 3)))
        except Exception:
            pass


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Phosphene Ming-Image-0.2-Design MLX runner.")
    ap.add_argument("--model", type=Path, required=True, help="Directory holding pinned MLX weights")
    ap.add_argument("--prompt", default="", help="Prompt text")
    ap.add_argument("--prompt-file", type=Path, help="Read UTF-8 prompt from file")
    ap.add_argument("--out", type=Path, required=True, help="Output PNG path")
    ap.add_argument("--tier", default="standard", choices=("standard", "high"))
    ap.add_argument("--aspect", default="1:1", help="Aspect ratio key (match, 1:1, 16:9, 9:16, 4:3, 3:4, 21:9)")
    ap.add_argument("--width", type=int, default=None, help="Explicit width (overrides tier/aspect)")
    ap.add_argument("--height", type=int, default=None, help="Explicit height (overrides tier/aspect)")
    ap.add_argument("--input-image", dest="input_images", action="append", type=Path, default=[],
                    help="Reference picture path for image editing (repeatable)")
    ap.add_argument("--steps", type=int, default=pins.DEFAULT_STEPS)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--low-memory", default="auto", choices=("auto", "off", "llm", "full"))
    ap.add_argument("--memory-gb", type=float, default=None)
    ap.add_argument("--wired-gb", type=float, default=None)
    ap.add_argument("--no-compile", action="store_true")
    ap.add_argument("--transparent", action="store_true",
                    help="Prepend upstream RGBA transparent-background prefix when not already present")
    ap.add_argument("--metrics", type=Path, default=None, help="Optional JSON metrics output path")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    def _handle_signal(signum, _frame):
        _say("stopped")
        raise SystemExit(128 + int(signum))

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    args = parse_args(argv)
    prompt = args.prompt_file.read_text(encoding="utf-8").strip() if args.prompt_file else args.prompt.strip()
    if not prompt:
        _say("error", "prompt is empty")
        return 2
    if args.transparent:
        prompt = pins.apply_transparent_prefix(prompt)
    if args.steps < 1:
        _say("error", "--steps must be >= 1")
        return 2
    if not (args.model / "config.json").is_file() or not (args.model / "index.json").is_file():
        _say("error", f"incomplete model directory: {args.model}")
        return 2

    ref_images = []
    for p in args.input_images:
        if not p.is_file():
            _say("error", f"input image does not exist: {p}")
            return 2
        from PIL import Image
        with Image.open(p) as im:
            ref_images.append(im.copy())

    is_edit = bool(ref_images)
    crop_to_aspect = False
    try:
        if is_edit and args.width is None and args.height is None:
            # Upstream image-edit runs on the 1024 bucket (TASK_RESOLUTION_BUCKETS["image-edit"] = (1024,)).
            if str(args.aspect or "match").strip().lower() in ("match", "auto", ""):
                w0, h0 = ref_images[0].size
                width, height = pins.resolve_edit_canvas(w0, h0)
            else:
                width, height = pins.resolve_canvas("standard", args.aspect, None, None)
                crop_to_aspect = True
        else:
            width, height = pins.resolve_canvas(args.tier, args.aspect, args.width, args.height)
            if is_edit:
                crop_to_aspect = True
    except ValueError as exc:
        _say("error", str(exc))
        return 2

    ram_gb = _system_ram_gb()
    low_mem = choose_low_memory_mode(args.low_memory, ram_gb, width, height)
    _apply_memory_caps(args.memory_gb, args.wired_gb)

    t_total = time.perf_counter()
    stage_stats: dict[str, dict[str, float]] = {}
    step_times: list[float] = []

    engine = MingImageEngine(args.model, low_memory=low_mem, compile_dit=not args.no_compile)
    try:
        ref_latents = None
        if is_edit:
            _say(f"stage ref_vae (refs={len(ref_images)}, canvas={width}x{height})")
            mx.reset_peak_memory()
            t0 = time.perf_counter()
            ref_latents = engine.encode_ref_latents(
                ref_images, width=width, height=height, crop_to_target_aspect=crop_to_aspect
            )
            ref_vae_s = time.perf_counter() - t0
            stage_stats["ref_vae"] = {**_emit_mlx_stage("ref_vae"), "seconds": round(ref_vae_s, 3)}

        _say(
            f"stage encode (low_memory={low_mem}, mode={'edit' if is_edit else 't2i'}, "
            f"refs={len(ref_images)}, canvas={width}x{height}, steps={args.steps}, seed={args.seed})"
        )
        mx.reset_peak_memory()
        t0 = time.perf_counter()
        cond, cond2 = engine.encode_prompt(prompt, ref_images=ref_images if is_edit else None)
        encode_s = time.perf_counter() - t0
        stage_stats["encode"] = {**_emit_mlx_stage("encode"), "seconds": round(encode_s, 3)}

        _say("stage denoise")
        mx.reset_peak_memory()
        t0 = time.perf_counter()

        def on_step(step_i: int, total: int, dt: float) -> None:
            step_times.append(dt)
            _say(f"step {step_i}/{total} ({dt:.2f}s)")

        latents = engine.denoise(
            cond, cond2, width, height, args.steps, args.seed, on_step=on_step, ref_latents=ref_latents
        )
        del cond, cond2, ref_latents
        denoise_s = time.perf_counter() - t0
        stage_stats["denoise"] = {**_emit_mlx_stage("denoise"), "seconds": round(denoise_s, 3)}

        _say("stage decode")
        mx.reset_peak_memory()
        t0 = time.perf_counter()
        image = engine.decode(latents)
        del latents
        decode_s = time.perf_counter() - t0
        stage_stats["decode"] = {**_emit_mlx_stage("decode"), "seconds": round(decode_s, 3)}

        args.out.parent.mkdir(parents=True, exist_ok=True)
        image.save(args.out)
        total_s = time.perf_counter() - t_total
        peak_gib = max(s["peak_gib"] for s in stage_stats.values())
        _say(f"done {total_s:.2f}s {args.out}")

        if args.metrics:
            args.metrics.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "runner_version": RUNNER_VERSION,
                "model": f"{pins.WEIGHTS_REPO}@{pins.WEIGHTS_REVISION[:7]}",
                "upstream": f"{pins.UPSTREAM_REPO}@{pins.UPSTREAM_REVISION[:7]}",
                "task": "image-edit" if is_edit else "text-to-image",
                "references": len(ref_images),
                "width": width,
                "height": height,
                "steps": args.steps,
                "seed": args.seed,
                "low_memory": low_mem,
                "compile_dit": not args.no_compile,
                "total_seconds": round(total_s, 3),
                "encode_seconds": round(encode_s, 3),
                "denoise_seconds": round(denoise_s, 3),
                "decode_seconds": round(decode_s, 3),
                "mean_step_seconds": round(sum(step_times) / max(1, len(step_times)), 3),
                "peak_gib": round(peak_gib, 3),
                "stages": stage_stats,
                "torch_imported": "torch" in sys.modules,
                "output": str(args.out.resolve()),
                "mode": image.mode,
            }
            args.metrics.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return 0
    finally:
        engine.close()


if __name__ == "__main__":
    raise SystemExit(main())
