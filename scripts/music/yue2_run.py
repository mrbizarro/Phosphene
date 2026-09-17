#!/usr/bin/env python3
"""Phosphene's YuE2 music runner — one song per process.

Runs inside the music engine's own venv (Python 3.12, mlx 0.32.2, the
`mlx-yue` package from vanch007/mlx-Yue pinned by SHA). The Phosphene panel
spawns it the way it spawns the H3 runner: argv in, parseable progress lines
on stdout, a WAV plus a provenance sidecar out, SIGTERM to stop.

Progress protocol (one line each, stdout, flushed):
    [music] stage <name>
    [music] plan <k>            ABC score tokens written so far
    [music] song <k>/<max>      semantic tokens (25 per second of audio)
    [music] synth <k>/<n>       flow-matching steps
    [music] decode <k>/<n>      VAE tiles
    [music] done <seconds> <path>
    [music] error <message>

Exit codes: 0 ok · 2 bad input · 3 memory guard · 130 stopped · 1 other.

YuE2 has no duration input. A song is as long as its lyrics make it; the
only length control is the semantic token budget, which TRUNCATES (25 tokens
= 1 s). `--max-seconds` sets that budget and the sidecar records whether the
song ended naturally or was cut.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import sys
import threading
import time
from pathlib import Path

TOKENS_PER_SECOND = 25
MAX_SECONDS = 360  # upstream semantic max_tokens 9000
RUNNER_VERSION = "1"
# YuE2 has no instrumental switch. Its lyrics protocol reads bare section tags
# with no lines under them as instrumental passages, so an instrumental request
# is a skeleton of tags plus an explicit style cue. Validated by ear in the
# bake-off (notes/yue2/MEASUREMENTS.md).
INSTRUMENTAL_LYRICS = "[Intro]\n\n[Instrumental]\n\n[Instrumental]\n\n[Outro]"
INSTRUMENTAL_STYLE = "instrumental, no vocals"


def say(*parts):
    print("[music]", *parts, flush=True)


def parse_args(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model-dir", required=True, type=Path, help="converted generator dir (conversion.json)")
    p.add_argument("--vae-dir", required=True, type=Path, help="YuE2-Vae dir (model.safetensors + config.json)")
    p.add_argument("--output", required=True, type=Path, help=".wav or .flac")
    p.add_argument("--style", default="")
    p.add_argument("--lyrics", default=None)
    p.add_argument("--lyrics-file", type=Path, default=None)
    p.add_argument("--abc-file", type=Path, default=None, help="supplied ABC score (full/melody only)")
    p.add_argument("--mode", choices=("full", "melody", "off"), default="full")
    p.add_argument("--instrumental", action="store_true",
                   help="no vocal line: lyrics become bare section tags (see INSTRUMENTAL_LYRICS)")
    p.add_argument("--seed", type=int, default=-1)
    p.add_argument("--max-seconds", type=float, default=MAX_SECONDS)
    p.add_argument("--steps", type=int, default=32, help="flow-matching midpoint steps (32 = reference)")
    p.add_argument("--cfg-scale", type=float, default=None)
    p.add_argument("--precision", choices=("bf16", "8bit", "4bit"), default="bf16")
    p.add_argument("--memory-budget-gib", type=float, default=None)
    p.add_argument("--vae-core-frames", type=int, default=256)
    p.add_argument("--artifacts", type=Path, default=None, help="also keep score/tokens/latents here")
    p.add_argument("--sidecar", type=Path, default=None, help="default: <output>.json")
    p.add_argument("--extra-json", default=None, help="merged into the sidecar (panel provenance)")
    return p.parse_args(argv)


def default_budget_gib():
    """Whole-process footprint budget. Upstream fixes 16 GiB and refuses
    anything that leaves < 4 GiB for the OS; scale with the machine instead."""
    try:
        import psutil
        total = psutil.virtual_memory().total / 2**30
    except Exception:
        return 16.0
    return float(max(12, min(24, int(total) - 8)))


def relax_guard():
    """The upstream guard samples SYSTEM-WIDE pressure and swap every 0.25 s
    and aborts on any non-normal reading. Inside Phosphene the panel, the
    browser and other apps move those numbers, so a single transient sample
    killed whole songs. Keep the per-process footprint budget strict; tolerate
    transient pressure while real headroom remains; bound swap growth by a
    configurable, larger window. Same idea as yue2-studio's 24 GB patch."""
    from lyra import measure

    gib, mib = measure._GIB, measure._MIB
    min_avail = float(os.environ.get("YUE2_MIN_AVAILABLE_GIB", "1.5"))
    swap_out = float(os.environ.get("YUE2_MAX_SWAP_OUT_MIB", "1024"))
    swap_growth = float(os.environ.get("YUE2_MAX_SWAP_GROWTH_MIB", "2048"))
    counters = {"pressure_warnings": 0, "peak_footprint_gib": 0.0, "min_available_gib": None}

    def check(self, sample):
        if self._baseline is None:
            self._baseline = sample
        footprint = sample["physical_footprint_bytes"]
        counters["peak_footprint_gib"] = max(counters["peak_footprint_gib"], round(footprint / gib, 2))
        avail = round(sample["system_available_bytes"] / gib, 2)
        if counters["min_available_gib"] is None or avail < counters["min_available_gib"]:
            counters["min_available_gib"] = avail
        if footprint > self.memory_budget_gib * gib:
            raise MemoryError(f"Process footprint {footprint / gib:.2f} GiB exceeds "
                              f"{self.memory_budget_gib:g} GiB budget")
        if sample["system_available_bytes"] < min_avail * gib:
            raise MemoryError(f"Less than {min_avail:g} GiB of system memory available")
        if sample["system_memory_pressure_level"] != 1:
            counters["pressure_warnings"] += 1
        swapped = sample["system_swap_out_bytes"] - self._baseline["system_swap_out_bytes"]
        growth = sample["system_swap_used_bytes"] - self._baseline["system_swap_used_bytes"]
        if swapped > swap_out * mib or growth > swap_growth * mib:
            raise MemoryError(f"Stopping: the system is swapping ({swapped / mib:.0f} MiB out, "
                              f"{growth / mib:.0f} MiB swap growth)")

    measure.GPUExecution._check_sample = check
    return {"min_available_gib": min_avail, "max_swap_out_mib": swap_out,
            "max_swap_growth_mib": swap_growth, "counters": counters}


def main(argv=None):
    args = parse_args(argv)
    t0 = time.perf_counter()
    out = args.output.expanduser().resolve()
    if out.suffix.lower() not in (".wav", ".flac"):
        say("error", "output must end in .wav or .flac")
        return 2
    if args.lyrics_file is not None:
        lyrics = args.lyrics_file.read_text(encoding="utf-8")
    else:
        lyrics = args.lyrics or ""
    style = (args.style or "").strip()
    lyrics = lyrics.replace("\r\n", "\n").strip("\n")
    if args.instrumental:
        lyrics = INSTRUMENTAL_LYRICS
        if "instrumental" not in style.lower():
            style = f"{style}, {INSTRUMENTAL_STYLE}" if style else INSTRUMENTAL_STYLE
    if not style and not lyrics.strip():
        say("error", "Give it something to work with — lyrics, a style description, or both.")
        return 2
    abc = args.abc_file.read_text(encoding="utf-8") if args.abc_file else None
    if abc is not None and args.mode == "off":
        say("error", "a supplied score needs mode full or melody")
        return 2
    seed = args.seed if args.seed is not None and args.seed >= 0 else int.from_bytes(os.urandom(4), "little")
    max_seconds = max(8.0, min(float(args.max_seconds), float(MAX_SECONDS)))
    max_tokens = int(round(max_seconds * TOKENS_PER_SECOND))
    semantic_sampling = {"max_tokens": max_tokens, "min_tokens": min(200, max_tokens)}

    stop = threading.Event()

    def on_signal(signum, _frame):
        stop.set()
        say("stage", "stopping")

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)

    os.environ.setdefault("MLX_ENABLE_TF32", "0")
    # lyra/pipeline.py rejects these PyTorch MPS vars (it's an MLX runtime; fallback
    # paths are not validated). Strip them if the parent env (e.g. Pinokio) set them.
    os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    os.environ.pop("PYTORCH_MPS_FAST_MATH", None)
    say("stage", "loading")
    import mlx.core as mx
    from yue2.protocol import GenerationConfig
    from lyra.pipeline import YuE2Pipeline

    guard = relax_guard()
    budget = args.memory_budget_gib or default_budget_gib()
    counts = {"abc": 0, "semantic": 0}
    last = {"t": 0.0}

    def on_token(phase, _token):
        counts[phase] = counts.get(phase, 0) + 1
        now = time.monotonic()
        if now - last["t"] >= 0.5:
            last["t"] = now
            if phase == "abc":
                say("plan", counts["abc"])
            else:
                say("song", f"{counts['semantic']}/{max_tokens}")

    config = GenerationConfig(ode_steps=int(args.steps))
    pipe = YuE2Pipeline(
        args.model_dir, args.vae_dir, precision=args.precision, generation_config=config,
        memory_budget_gib=budget, vae_core_frames=args.vae_core_frames, progress=False,
    )
    # Surface the NAR / VAE progress the pipeline only reports when its own
    # stderr progress is enabled.
    from lyra import nar as _nar
    _orig_synth = _nar.synthesize

    def synth_with_progress(*a, on_progress=None, **kw):
        def report(k, n):
            if on_progress is not None:
                on_progress(k, n)
            say("synth", f"{k}/{n}")
        return _orig_synth(*a, on_progress=report, **kw)

    _nar.synthesize = synth_with_progress
    from lyra import vae as _vae
    _orig_decode = _vae.OobleckDecoder.decode

    def decode_with_progress(self, *a, on_progress=None, **kw):
        def report(k, n):
            if on_progress is not None:
                on_progress(k, n)
            say("decode", f"{k}/{n}")
        return _orig_decode(self, *a, on_progress=report, **kw)

    _vae.OobleckDecoder.decode = decode_with_progress
    peak = {"gib": 0.0}
    try:
        with pipe:
            say("stage", "planning" if args.mode != "off" and abc is None else "writing")
            kw = dict(style=style, lyrics=lyrics, cot=args.mode, seed=seed,
                      semantic_sampling=semantic_sampling, cancelled=stop.is_set,
                      on_token=on_token)
            if abc is not None:
                kw["abc"] = abc
            if args.cfg_scale is not None:
                kw["cfg_scale"] = float(args.cfg_scale)
            result = pipe(**kw)
            # The port checks cancellation before each decode tile, not after
            # the last one: a Stop that lands there must not publish a song.
            if stop.is_set():
                raise InterruptedError("stopped after decoding")
            peak["gib"] = mx.get_peak_memory() / 2**30
            out.parent.mkdir(parents=True, exist_ok=True)
            import soundfile as sf
            subtype = "PCM_24"
            tmp = out.with_name(out.stem + ".partial" + out.suffix)
            sf.write(tmp, result.audio, result.sample_rate, subtype=subtype,
                     format="WAV" if out.suffix.lower() == ".wav" else "FLAC")
            # Last chance for Stop: past this line the song is published.
            if stop.is_set():
                tmp.unlink(missing_ok=True)
                raise InterruptedError("stopped before publishing")
            os.replace(tmp, out)
            if args.artifacts is not None:
                result.save_artifacts(args.artifacts)
    except InterruptedError:
        say("error", "stopped")
        return 130
    except MemoryError as exc:
        say("error", f"memory guard: {exc}")
        return 3
    except Exception as exc:  # noqa: BLE001 — the panel shows this line
        say("error", f"{type(exc).__name__}: {exc}")
        raise

    seconds = len(result.audio) / result.sample_rate
    truncated = dict(result.truncated) if isinstance(result.truncated, dict) else result.truncated
    sidecar = {
        "engine": "music",
        "model": "YuE2-3B",
        "model_credit": "YuE2 by Multimodal Art Projection (m-a-p), CC BY-NC 4.0 + creator permission",
        "port": "vanch007/mlx-Yue (MLX)",
        "runner": f"phosphene yue2_run v{RUNNER_VERSION}",
        "style": style,
        "lyrics": lyrics,
        "mode": args.mode,
        "instrumental": bool(args.instrumental),
        "abc_supplied": abc is not None,
        "score_abc": result.abc,
        "seed": seed,
        "steps": int(args.steps),
        "cfg_scale": args.cfg_scale,
        "precision": args.precision,
        "max_seconds": max_seconds,
        "audio_seconds": round(seconds, 3),
        "sample_rate": result.sample_rate,
        "channels": int(result.audio.shape[1]) if result.audio.ndim == 2 else 1,
        "truncated": truncated,
        "ended_naturally": not (result.truncated.get("semantic") if isinstance(result.truncated, dict) else bool(result.truncated)),
        "timing": result.timing,
        "wall_seconds": round(time.perf_counter() - t0, 2),
        "mlx_peak_gib": round(peak["gib"], 2),
        "memory_budget_gib": budget,
        "guard": {k: v for k, v in guard.items() if k != "counters"} | guard["counters"],
        "weights": {"mot": (result.weights.get("mot") or {}).get("files") and
                    {k: v.get("sha256") for k, v in result.weights["mot"]["files"].items()
                     if isinstance(v, dict)}},
        "runtime": {"python": platform.python_version(), "mlx": mx.__version__,
                    "macos": platform.mac_ver()[0], "machine": platform.machine()},
        "output": str(out),
    }
    if args.extra_json:
        try:
            sidecar.update(json.loads(args.extra_json))
        except ValueError:
            pass
    side = args.sidecar or out.with_name(out.name + ".json")
    side.write_text(json.dumps(sidecar, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    say("done", f"{seconds:.2f}", str(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
