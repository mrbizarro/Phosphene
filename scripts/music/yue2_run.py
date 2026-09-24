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
import shutil
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
    # COVER: a real recording in, a new song out. The transcriber (SheetSage2 +
    # MERT-v2) writes an ABC score of the source, and that score becomes the
    # plan the generator realises under a NEW style and NEW lyrics. It is the
    # same road as --abc-file, with the score read off a record instead of
    # typed.
    p.add_argument("--source-audio", type=Path, default=None,
                   help="cover this recording: transcribe it, then render the score")
    p.add_argument("--cover-task", choices=("full", "melody-full", "melody-vocal"),
                   default="melody-full",
                   help="full = melody+chords+accompaniment, melody-full = melody+chords "
                        "from the mix, melody-vocal = the sung line only")
    p.add_argument("--transcription-model", default="m-a-p/SheetSage2")
    p.add_argument("--transcription-base-model", default="m-a-p/MERT-v2-FullSong")
    p.add_argument("--transcription-cache", type=Path, default=None,
                   help="local HF cache holding the two transcription models")
    p.add_argument("--cover-seconds", type=float, default=None,
                   help="transcribe only the first N seconds of the source")
    # VARIATIONS. A song's artifact folder holds its plan (the score and the
    # tokenised prefix), its semantic tokens, its latents and its noise. Each
    # is a place to restart from, and each restart is a different kind of
    # "again":
    #   take  — same score, same words, a NEW PERFORMANCE: the semantic stage
    #           is re-run from the saved plan with a new seed. Costs a full
    #           song minus the planning.
    #   sound — same performance, a NEW RECORDING of it: the saved semantic
    #           tokens are re-synthesised with fresh solver noise and decoded.
    #           Costs the NAR + VAE only — a fraction of a song.
    # (A "restyle" — same score, new words or style — is --abc-file pointed at
    # the saved score.abc; the panel builds that one itself.)
    p.add_argument("--from-artifacts", type=Path, default=None,
                   help="a song's artifact folder to vary")
    p.add_argument("--variation", choices=("take", "sound"), default=None)
    # SCORE ONLY: listen to a recording and write out its ABC score, no song.
    p.add_argument("--transcribe-only", action="store_true",
                   help="with --source-audio: write the score and stop")
    p.add_argument("--title", default="", help="carried into the sidecar only")
    p.add_argument("--mode", choices=("full", "melody", "off"), default="full")
    p.add_argument("--instrumental", action="store_true",
                   help="no vocal line. With the LoRA pack installed this is Maestro's "
                        "recipe — the instrumental AR adapter at 1.0, full score planning, "
                        "'[instrumental]' in place of words; without it, bare section tags")
    # ---- LoRA ---------------------------------------------------------------
    # Adapters are applied to the loaded MLX models at inference and never
    # merged: the AR model runs 8-bit here, and a merge would cost either 5 GB
    # more resident weights or a dequantise/requantise per song.
    p.add_argument("--lora", action="append", default=[], metavar="PATH[:STRENGTH]",
                   help="a YuE2 adapter to run, repeatable; strength 0–1.5, default 1.0")
    p.add_argument("--lora-mode", choices=("joint", "separate"), default="joint",
                   help="joint: additive deltas only (Mothersuperior's nar_lora_joint_*). "
                        "separate: an artist bundle whose acoustic file also REPLACES the "
                        "decoder's vae2llm/llm2vae, blended across bundles by strength")
    p.add_argument("--lora-trigger", action="append", default=[], metavar="WORD",
                   help="training trigger for the matching --lora, added to the style once")
    p.add_argument("--lora-dir", type=Path, default=None,
                   help="the LoRA pack folder; --instrumental reads its AR adapter from here")
    # ---- AUDIO PROMPT -------------------------------------------------------
    # Continue a REAL recording. The tokenizer head turns its first seconds
    # into YuE2 semantic codes; those codes become the prefix the song is
    # written from, so the model hears the take rather than reading about it.
    p.add_argument("--audio-prompt", type=Path, default=None,
                   help="continue this recording: tokenise it and use its codes as the "
                        "semantic prefix")
    p.add_argument("--audio-prompt-seconds", type=float, default=10.0,
                   help="how much of the recording to hear (default 10 s)")
    p.add_argument("--audio-prompt-start", type=float, default=0.0,
                   help="where in the recording to start listening")
    p.add_argument("--audio-prompt-head", type=Path, default=None,
                   help="the tokenizer head (default: the LoRA pack's tokenizer/ folder)")
    p.add_argument("--mert-dir", type=Path, default=None,
                   help="MERT-v2-FullSong checkpoint for the audio prompt")
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
    os.environ.setdefault("MLX_ENABLE_TF32", "0")
    # lyra/pipeline.py rejects these PyTorch MPS vars (it's an MLX runtime; fallback
    # paths are not validated). Strip them if the parent env (e.g. Pinokio) set them.
    # Settled here, at the top, because the LoRA module imports MLX while the
    # request is still being read — long before the pipeline is built.
    os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    os.environ.pop("PYTORCH_MPS_FAST_MATH", None)
    out = args.output.expanduser().resolve()
    want = (".abc",) if args.transcribe_only else (".wav", ".flac")
    if out.suffix.lower() not in want:
        say("error", f"output must end in {' or '.join(want)}")
        return 2
    if args.transcribe_only and not args.source_audio:
        say("error", "--transcribe-only needs --source-audio")
        return 2
    if (args.variation is None) != (args.from_artifacts is None):
        say("error", "--variation and --from-artifacts go together")
        return 2
    if args.from_artifacts is not None and not (args.from_artifacts / "result.json").is_file():
        say("error", f"no saved song at {args.from_artifacts} — only songs made with "
                     f"artifacts kept can be varied")
        return 2
    if args.audio_prompt is not None:
        if args.variation is not None or args.transcribe_only:
            say("error", "--audio-prompt is for writing a new song, not for a variation "
                         "or a transcription")
            return 2
        if not args.audio_prompt.expanduser().is_file():
            say("error", f"audio prompt not found: {args.audio_prompt}")
            return 2
        if not 1.0 <= float(args.audio_prompt_seconds) <= 60.0:
            say("error", "--audio-prompt-seconds must be between 1 and 60")
            return 2
    if args.lyrics_file is not None:
        lyrics = args.lyrics_file.read_text(encoding="utf-8")
    else:
        lyrics = args.lyrics or ""
    style = (args.style or "").strip()
    lyrics = lyrics.replace("\r\n", "\n").strip("\n")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import yue2_lora

    # ---- INSTRUMENTAL -------------------------------------------------------
    # Two recipes, and which one runs depends only on whether the 140 MB AR
    # adapter is on disk. With it: Maestro's — adapter at 1.0, full score
    # planning, `[instrumental]` (or the author's bare section tags) in place
    # of words, other LoRAs paused, stock acoustic decoder. Without it: the
    # tag skeleton that shipped in v4.16, which was validated by ear. A queued
    # song must never fail because an optional download is missing.
    lora_specs = [yue2_lora.parse_spec(spec) for spec in (args.lora or [])]
    instrumental_info = None
    if args.instrumental:
        recipe = yue2_lora.instrumental_recipe(args.lora_dir, lyrics)
        if recipe["adapter"] is not None:
            lyrics = recipe["lyrics"]
            args.mode = recipe["cot"]
            paused = [str(path) for path, _ in lora_specs]
            lora_specs = [(recipe["adapter"], recipe["strength"])]
            instrumental_info = {"recipe": "mothersuperior-ar-lora",
                                 "adapter": recipe["adapter"].name,
                                 "strength": recipe["strength"], "cot": recipe["cot"],
                                 "decoder": recipe["decoder"], "paused_loras": paused}
        else:
            lyrics = INSTRUMENTAL_LYRICS
            instrumental_info = {"recipe": "section-tags", "adapter": None,
                                 "reason": "the instrumental LoRA is not installed"}
        if "instrumental" not in style.lower():
            style = f"{style}, {INSTRUMENTAL_STYLE}" if style else INSTRUMENTAL_STYLE
    if not style and not lyrics.strip() and args.variation is None and not args.transcribe_only:
        say("error", "Give it something to work with — lyrics, a style description, or both.")
        return 2
    abc = args.abc_file.read_text(encoding="utf-8") if args.abc_file else None
    if abc is not None and args.mode == "off":
        say("error", "a supplied score needs mode full or melody")
        return 2
    cover_source = args.source_audio.expanduser() if args.source_audio else None
    if cover_source is not None:
        if abc is not None:
            say("error", "a cover reads its score off the recording — drop the ABC file")
            return 2
        if not cover_source.is_file():
            say("error", f"source song not found: {cover_source}")
            return 2
        # The transcription task decides the planning mode, exactly as upstream's
        # own cover command does: a full transcription can only be realised by
        # full planning, a melody-only one by melody planning.
        args.mode = "full" if args.cover_task == "full" else "melody"
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

    say("stage", "loading")
    import mlx.core as mx
    from yue2.protocol import GenerationConfig
    from lyra.pipeline import YuE2Pipeline

    guard = relax_guard()
    budget = args.memory_budget_gib or default_budget_gib()

    # ---- COVER: transcribe first, and let go of the transcriber -----------
    # Order is the whole trick. SheetSage2 + MERT-v2 is ~2.8 GB of weights and
    # YuE2 is ~10 GB; on a 24 GB Mac the two together are the difference
    # between a song and the memory guard. Upstream's own cover command does
    # the same thing — transcribe inside the guard, collect, clear the Metal
    # cache, and only then build the generator — so the two never coexist.
    cover_info = None
    if cover_source is not None:
        import gc

        from lyra.measure import GPUExecution
        from lyra.transcription.pipeline import transcribe

        say("stage", "listening")
        # BESIDE the artifacts folder, never inside it: save_artifacts() insists on
        # an empty directory, and a transcription placed inside it made every
        # cover fail AFTER the song was published (Codex review, 2026-09-20).
        base = args.artifacts.parent if args.artifacts is not None else out.parent
        work = base / ((args.artifacts.name if args.artifacts is not None else out.stem) + ".transcription")
        if work.exists() and any(work.iterdir()):
            shutil.rmtree(work)
        try:
            with GPUExecution(memory_budget_gib=budget):
                transcription = transcribe(
                    cover_source, work,
                    model_path=args.transcription_model,
                    base_model=args.transcription_base_model,
                    cache_dir=str(args.transcription_cache) if args.transcription_cache else None,
                    offline=args.transcription_cache is not None,
                    task=args.cover_task,
                    max_seconds=args.cover_seconds,
                    cancelled=stop.is_set,
                )
        except InterruptedError:
            say("error", "stopped")
            return 130
        except MemoryError as exc:
            say("error", f"memory guard: {exc}")
            return 3
        except FileNotFoundError as exc:
            # The two transcription models are a separate, optional download.
            # Say which thing is missing rather than surfacing a cache path.
            say("error", f"the cover models are not installed yet ({exc}). "
                         f"Install them from Compose - Cover a song.")
            return 2
        gc.collect()
        mx.clear_cache()
        if transcription.get("status") != "complete" or transcription.get("truncated"):
            say("error", "the transcription came out incomplete — try a shorter "
                         "excerpt with --cover-seconds, or a cleaner recording")
            return 2
        abc = (work / "score.abc").read_text(encoding="utf-8")
        cover_info = {"source_sha256": transcription.get("source_audio_sha256"),
                      "task": args.cover_task,
                      "seconds_read": args.cover_seconds,
                      "artifacts": str(work)}
        if args.transcribe_only:
            # "Get the score": the recording's sheet music, nothing rendered.
            # Same sidecar shape as a song so the gallery reads it the same
            # way — minus everything about audio that was never made.
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(abc, encoding="utf-8")
            side = args.sidecar or out.with_name(out.name + ".json")
            side.write_text(json.dumps({
                "engine": "music", "kind": "score", "title": args.title,
                "score_abc": abc, "cover": cover_info, "source": str(cover_source),
                "wall_seconds": round(time.perf_counter() - t0, 2),
                "runner": f"phosphene yue2_run v{RUNNER_VERSION}",
                **(json.loads(args.extra_json) if args.extra_json else {}),
            }, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
            say("done", "0.00", str(out))
            return 0
        say("stage", "covering")
    # ---- AUDIO PROMPT: listen first, then let the listener go ---------------
    # Same order, and the same reason, as the cover path above: MERT-v2 is
    # ~2.5 GB of weights and YuE2 is ~10 GB. Tokenise inside the guard, drop
    # the frontend, clear the Metal cache, and only then build the generator.
    audio_prompt_codes = None
    audio_prompt_info = None
    if args.audio_prompt is not None:
        import gc

        from lyra.measure import GPUExecution
        import yue2_tokenizer

        say("stage", "listening")
        head_file = args.audio_prompt_head or yue2_tokenizer.head_path(
            args.lora_dir or (Path(__file__).resolve().parents[2] / "mlx_models" / "yue2-loras"))
        listen_started = time.perf_counter()
        try:
            with GPUExecution(memory_budget_gib=budget):
                audio_prompt_codes, prompt_meta = yue2_tokenizer.encode_codes(
                    args.audio_prompt, head_file=head_file, mert_dir=args.mert_dir,
                    start=float(args.audio_prompt_start),
                    seconds=float(args.audio_prompt_seconds),
                    cancelled=stop.is_set,
                    on_stage=lambda text: say("listen", text))
        except InterruptedError:
            say("error", "stopped")
            return 130
        except MemoryError as exc:
            say("error", f"memory guard: {exc}")
            return 3
        except FileNotFoundError as exc:
            say("error", f"the real-audio tokenizer head is not installed yet ({exc}). "
                         f"Fetch the LoRA pack to get it.")
            return 2
        except (OSError, ValueError, RuntimeError) as exc:
            say("error", f"could not listen to the audio prompt: {exc}")
            return 2
        gc.collect()
        mx.clear_cache()
        audio_prompt_info = {
            "path": str(args.audio_prompt.expanduser()),
            "seconds": float(args.audio_prompt_seconds),
            "start_seconds": float(args.audio_prompt_start),
            "frames": int(len(audio_prompt_codes)),
            "head": prompt_meta["head"].get("version"),
            "head_file": Path(head_file).name,
            "head_sha256": prompt_meta["head"].get("sha256"),
            "mert_layer": prompt_meta["features"]["mert_layer"],
            "resampler": prompt_meta["features"]["resampler"],
            "statistics": prompt_meta["statistics"],
            "listen_seconds": round(time.perf_counter() - listen_started, 2),
        }
        say("stage", f"heard {audio_prompt_info['frames']} codes "
                     f"({audio_prompt_info['frames'] / TOKENS_PER_SECOND:.1f} s) in "
                     f"{audio_prompt_info['listen_seconds']:.2f}s")

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

    # ---- LORA STACK ---------------------------------------------------------
    # Read and shape-checked BEFORE the pipeline is built, so a bad adapter
    # costs a second rather than ten minutes. Applied through a wrapper on
    # `_load_model`, which is the only hook that sees all three models a song
    # uses (the planning AR, the BF16 conditioning AR, and the acoustic model
    # built halfway through).
    adapters = []
    if lora_specs:
        try:
            adapters = yue2_lora.load_stack(
                lora_specs, mode=args.lora_mode, triggers=args.lora_trigger)
        except (OSError, ValueError) as exc:
            say("error", f"{exc}")
            return 2
        for adapter in adapters:
            say("stage", f"lora {adapter.name} · {adapter.branch} · rank {adapter.rank} "
                         f"· strength {adapter.strength:g}")
        style = yue2_lora.style_with_triggers(style, adapters)
    lora_runtime = yue2_lora.LoRARuntime(adapters)

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
    lora_runtime.attach(pipe)
    peak = {"gib": 0.0}
    try:
        with pipe:
            variation_info = None
            if args.variation is not None:
                from dataclasses import replace

                from lyra.pipeline import SongResult, initial_noise
                from yue2.storage import identity

                if args.variation == "take":
                    # Same score, same words — a new performance. The saved plan
                    # carries the tokenised prefix (style, lyrics, mode) and the
                    # score IDs; only the request seed changes, and the seed is
                    # not part of the prefix, so the plan stays valid as-is.
                    from yue2.pipeline import SymbolicPlan
                    plan = SymbolicPlan.load(args.from_artifacts)
                    plan = replace(plan, request=replace(plan.request, seed=seed))
                    style, lyrics, abc = plan.request.style, plan.request.lyrics, plan.abc
                    args.mode = plan.request.cot
                    say("stage", "writing")
                    semantic = pipe.generate_semantic(plan, sampling=semantic_sampling,
                                                      cancelled=stop.is_set, on_token=on_token)
                    noise_seed = seed
                else:
                    # Same performance — a new recording of it. The semantic
                    # tokens are the song; fresh solver noise through the NAR
                    # and the decoder is a different take on how it SOUNDS.
                    # This is the cheap one: no planning, no semantic stage.
                    from lyra.artifacts import load_artifacts
                    saved = load_artifacts(args.from_artifacts)
                    semantic = saved.semantic
                    plan = semantic.plan
                    style, lyrics, abc = plan.request.style, plan.request.lyrics, plan.abc
                    args.mode = plan.request.cot
                    seed = plan.request.seed          # the song's own seed, unchanged
                    noise_seed = int.from_bytes(os.urandom(4), "little") if args.seed < 0 else args.seed
                    say("stage", "recording")
                noise = initial_noise(len(semantic.tokens), noise_seed)
                latents = pipe.synthesize(semantic, noise=noise, cancelled=stop.is_set)
                audio = pipe.decode(latents, cancelled=stop.is_set)
                cfg = pipe.effective_config(plan.request, semantic_sampling=semantic_sampling)
                stamp = identity({"request": plan.request.to_dict(), "config": cfg,
                                  "weights": pipe.weights})
                timing = {"abc": plan.timing, "semantic": semantic.timing,
                          "load": dict(pipe.load_timing)}
                result = SongResult(audio, 48000, semantic, latents, cfg, pipe.weights,
                                    timing, stamp, noise)
                variation_info = {"kind": args.variation, "from": str(args.from_artifacts),
                                  "noise_seed": noise_seed}
            elif audio_prompt_codes is not None:
                # ---- CONTINUATION ------------------------------------------
                # `pipe()` plans, writes and synthesises in one call and has no
                # seam for a prefix, so the three stages are run in the open
                # here — the same sequence, with the recording's codes joined
                # to the request prefix before the semantic stage and kept at
                # the head of the token stream afterwards.
                from lyra.pipeline import SongResult, initial_noise
                from yue2.storage import identity

                import yue2_tokenizer

                request_kw = {"cot": args.mode, "seed": seed}
                if abc is not None:
                    request_kw["abc"] = abc
                if args.cfg_scale is not None:
                    request_kw["cfg_scale"] = float(args.cfg_scale)
                request = pipe._request(style, lyrics, **request_kw)
                cfg = pipe.effective_config(request, semantic_sampling=semantic_sampling)
                stamp = identity({"request": request.to_dict(), "config": cfg,
                                  "weights": pipe.weights})
                started = time.perf_counter()
                say("stage", "planning" if args.mode != "off" and abc is None else "writing")
                plan = pipe.plan(request=request, cancelled=stop.is_set, on_token=on_token)
                say("stage", "continuing")
                semantic = yue2_tokenizer.continue_from_codes(
                    pipe, plan, audio_prompt_codes, sampling=semantic_sampling,
                    cancelled=stop.is_set, on_token=on_token)
                noise = initial_noise(len(semantic.tokens), request.seed)
                nar_started = time.perf_counter()
                latents = pipe.synthesize(semantic, noise=noise, cancelled=stop.is_set)
                nar_seconds = time.perf_counter() - nar_started
                vae_started = time.perf_counter()
                audio = pipe.decode(latents, cancelled=stop.is_set)
                timing = {"abc": plan.timing, "semantic": semantic.timing,
                          "nar_seconds": nar_seconds,
                          "vae_seconds": time.perf_counter() - vae_started,
                          "load": dict(pipe.load_timing),
                          "e2e_seconds": time.perf_counter() - started}
                result = SongResult(audio, 48000, semantic, latents, cfg, pipe.weights,
                                    timing, stamp, noise)
                audio_prompt_info["generated_frames"] = (
                    len(semantic.tokens) - len(audio_prompt_codes))
            else:
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
        "instrumental_recipe": instrumental_info,
        "lora": yue2_lora.describe_stack(adapters, instrumental=instrumental_info)
                if adapters else None,
        "abc_supplied": abc is not None,
        "cover": cover_info,
        "audio_prompt": audio_prompt_info,
        "variation": variation_info,
        "title": args.title,
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
