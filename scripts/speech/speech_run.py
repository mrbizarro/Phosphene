#!/usr/bin/env python3
"""Phosphene Speech runner: a script in, one WAV out.

Runs inside the speech engine's own venv (`speech-mlx/.venv`, mlx-audio pinned
in scripts/speech/speech_constraints.txt). The panel builds the argv and the
environment; this file owns nothing but the synthesis.

Two voice kinds, two checkpoints of the same model (Qwen3-TTS 12 Hz 1.7B,
Apache-2.0, 8-bit MLX conversions):

  preset  CustomVoice: nine built-in speakers, and a free-text delivery
          instruction ("dry, deadpan, slightly offended") that steers emotion.
  clone   Base: a voice from a short reference clip. With the clip's words
          (ref_text) the model continues the recording (closest match); without
          them it uses only the speaker embedding.

Protocol on stdout, one line each, flushed:
  [speech] stage <name>
  [speech] line <i>/<n>
  [speech] done {json}
  [speech] error <sentence>
Exit codes: 0 ok, 2 bad input, 3 memory guard, 1 anything else.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

# Pinokio exports these into every app it launches. They are PyTorch switches
# and mean nothing to MLX, but nothing about this engine was validated with
# them, so they go before anything imports a framework (phosphene-release §0).
for _k in ("PYTORCH_ENABLE_MPS_FALLBACK", "PYTORCH_MPS_FAST_MATH"):
    os.environ.pop(_k, None)

SAMPLE_RATE = 24000
LINE_GAP_S = 0.35        # silence between script lines (a breath, not a cut)
EDGE_PAD_S = 0.12        # silence kept at the head and tail
PEAK_DBFS = -1.0
MAX_CHARS = 2000
RC_INPUT, RC_MEMORY = 2, 3


def say(kind: str, msg: str = "") -> None:
    print(f"[speech] {kind} {msg}".rstrip(), flush=True)


def fail(code: int, msg: str) -> None:
    say("error", msg)
    sys.exit(code)


def split_lines(script: str) -> list[str]:
    """One generation per non-empty line. Long lines are split at sentence
    ends so no single pass runs past ~400 characters (the model drifts on very
    long single prompts)."""
    out: list[str] = []
    for raw in script.replace("\r", "\n").split("\n"):
        line = " ".join(raw.split())
        if not line:
            continue
        while len(line) > 400:
            cut = max(line.rfind(". ", 0, 400), line.rfind("? ", 0, 400), line.rfind("! ", 0, 400))
            cut = cut + 1 if cut > 80 else 400
            out.append(line[:cut].strip())
            line = line[cut:].strip()
        if line:
            out.append(line)
    return out


def trim_silence(audio, threshold: float = 0.008, pad_s: float = EDGE_PAD_S):
    import numpy as np
    if audio.size == 0:
        return audio
    win = int(0.02 * SAMPLE_RATE)
    n = audio.size // win
    if n < 2:
        return audio
    rms = np.sqrt((audio[: n * win].reshape(n, win) ** 2).mean(axis=1))
    loud = np.nonzero(rms > threshold)[0]
    if loud.size == 0:
        return audio
    pad = int(pad_s * SAMPLE_RATE)
    start = max(0, loud[0] * win - pad)
    end = min(audio.size, (loud[-1] + 1) * win + pad)
    return audio[start:end]


def write_wav(path: Path, audio) -> None:
    import numpy as np
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())


def change_speed(src: Path, dst: Path, speed: float) -> None:
    """Pitch-preserving tempo change. atempo takes 0.5–2.0 per stage."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        fail(1, "Speed needs ffmpeg and none was found on PATH. Set Speed to 1.0, or restart Phosphene from Pinokio.")
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", str(src), "-filter:a", f"atempo={speed:.3f}",
                        "-ar", str(SAMPLE_RATE), "-ac", "1", "-c:a", "pcm_s16le", str(dst)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not dst.is_file():
        fail(1, "ffmpeg could not change the speed: " + (r.stderr.strip().splitlines() or ["no output"])[-1])


def prepare_reference(src: str, work: Path, max_s: int = 20) -> str:
    """The clip the model clones from, as a 24 kHz mono WAV of at most max_s.
    mlx-audio's own reader handles WAV/MP3/FLAC but not every container a user
    drops (a .mov, an .m4a from a phone), and a long clip only slows the clone
    down. ffmpeg normalises all of them; without ffmpeg the file goes as is."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return src
    out = work / "reference.wav"
    r = subprocess.run([ffmpeg, "-v", "error", "-y", "-i", src, "-vn", "-t", str(max_s), "-ac", "1",
                        "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(out)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace")
    if r.returncode != 0 or not out.is_file() or out.stat().st_size < 4096:
        fail(RC_INPUT, "The voice clip could not be read as audio. Use a WAV, MP3, M4A or FLAC of someone speaking.")
    return str(out)


def peak_footprint_gb() -> float | None:
    """This process's lifetime peak physical footprint (what Activity
    Monitor's Memory column counts, GPU buffers included), from macOS
    proc_pid_rusage(RUSAGE_INFO_V4).ri_lifetime_max_phys_footprint.
    ru_maxrss leaves the Metal buffers out, so it understates an MLX job."""
    try:
        import ctypes
        lib = ctypes.CDLL("/usr/lib/libSystem.dylib")
        buf = (ctypes.c_uint64 * 48)()
        if lib.proc_pid_rusage(os.getpid(), 4, ctypes.byref(buf)) != 0:
            return None
        # 16-byte uuid, then uint64 fields; lifetime_max_phys_footprint is #28.
        return round(buf[2 + 28] / 1e9, 2)
    except Exception:
        return None


def low_memory() -> bool:
    """True when the Mac is close to swapping. vm_stat is cheap and needs no
    extra package; a 16 GB Mac with a browser open is where this matters."""
    try:
        out = subprocess.run(["vm_stat"], capture_output=True, text=True, errors="replace", timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    page = 16384
    m = re.search(r"page size of (\d+)", out)
    if m:
        page = int(m.group(1))
    def pages(name):
        mm = re.search(rf"{name}:\s+(\d+)", out)
        return int(mm.group(1)) if mm else 0
    free = (pages("Pages free") + pages("Pages inactive") + pages("Pages speculative")) * page
    return free < 0.6 * 2**30


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pack", required=True, help="mlx_models/speech")
    ap.add_argument("--kind", choices=("preset", "clone"), required=True)
    ap.add_argument("--voice", default="")
    ap.add_argument("--ref-audio", default="")
    ap.add_argument("--ref-text", default="")
    ap.add_argument("--style", default="", help="delivery instruction (presets)")
    ap.add_argument("--language", default="english")
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=-1)
    ap.add_argument("--temperature", type=float, default=0.9)
    ap.add_argument("--text-file", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)

    script = Path(a.text_file).read_text(encoding="utf-8", errors="replace")
    lines = split_lines(script)
    if not lines:
        fail(RC_INPUT, "The script is empty. Write at least one line.")
    if sum(len(l) for l in lines) > MAX_CHARS:
        fail(RC_INPUT, f"The script is longer than {MAX_CHARS} characters. Split it into shorter takes.")
    if not 0.5 <= a.speed <= 2.0:
        fail(RC_INPUT, "Speed must be between 0.5 and 2.0.")
    pack = Path(a.pack)
    if a.kind == "preset":
        model_dir = pack / "customvoice"
        if not a.voice:
            fail(RC_INPUT, "Pick a voice.")
    else:
        model_dir = pack / "base"
        if not a.ref_audio or not Path(a.ref_audio).is_file():
            fail(RC_INPUT, "The voice clip is missing. Pick the character again, or add the clip again.")
    if not (model_dir / "config.json").is_file():
        fail(1, f"The speech model is not installed at {model_dir}. Open Audio → Speech and install it.")

    work = Path(tempfile.mkdtemp(prefix="phos-speech-run-"))
    # With the clip's words the model continues the recording, so the clip must
    # not be cut mid-sentence (30 s ceiling); without them 20 s is plenty.
    ref_audio = (prepare_reference(a.ref_audio, work, 30 if a.ref_text.strip() else 20)
                 if a.kind == "clone" else "")

    say("stage", "load")
    t0 = time.time()
    import numpy as np
    import mlx.core as mx
    if os.environ.get("PHOSPHENE_SPEECH_DEVICE") == "cpu":   # debugging only: very slow
        mx.set_default_device(mx.cpu)
    mx.set_cache_limit(256 * 2**20)
    from mlx_audio.tts.utils import load_model
    model = load_model(str(model_dir))
    load_s = time.time() - t0
    seed = a.seed if a.seed >= 0 else int.from_bytes(os.urandom(4), "little") & 0x7FFFFFFF
    mx.random.seed(seed)

    kw: dict = {"lang_code": a.language, "temperature": a.temperature, "split_pattern": ""}
    if a.kind == "preset":
        kw["voice"] = a.voice
        if a.style.strip():
            kw["instruct"] = a.style.strip()[:300]
    else:
        kw["ref_audio"] = ref_audio
        if a.ref_text.strip():
            kw["ref_text"] = a.ref_text.strip()

    pieces = []
    gap = np.zeros(int(LINE_GAP_S * SAMPLE_RATE), dtype=np.float32)
    t1 = time.time()
    for i, line in enumerate(lines):
        say("line", f"{i + 1}/{len(lines)}")
        if low_memory():
            fail(RC_MEMORY, "Stopped before the Mac ran out of memory. Close other apps (a browser with many tabs is the usual one) and try again.")
        chunks = [np.asarray(r.audio, dtype=np.float32).reshape(-1) for r in model.generate(text=line, **kw)]
        if not chunks:
            fail(1, f"The model returned no audio for line {i + 1}.")
        audio = trim_silence(np.concatenate(chunks), pad_s=0.0 if len(lines) > 1 else EDGE_PAD_S)
        if pieces:
            pieces.append(gap)
        pieces.append(audio)
    gen_s = time.time() - t1
    audio = np.concatenate(pieces)
    if len(lines) > 1:
        pad = np.zeros(int(EDGE_PAD_S * SAMPLE_RATE), dtype=np.float32)
        audio = np.concatenate([pad, audio, pad])
    peak = float(np.abs(audio).max()) if audio.size else 0.0
    if peak > 0:
        audio = audio * (10 ** (PEAK_DBFS / 20) / peak)

    say("stage", "write")
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_name(out.stem + ".partial" + out.suffix)
    if abs(a.speed - 1.0) > 1e-3:
        with tempfile.TemporaryDirectory() as td:
            raw = Path(td) / "raw.wav"
            write_wav(raw, audio)
            change_speed(raw, partial, a.speed)
    else:
        write_wav(partial, audio)
    with wave.open(str(partial), "rb") as w:
        seconds = w.getnframes() / float(w.getframerate())
    os.replace(partial, out)
    shutil.rmtree(work, ignore_errors=True)
    done = {"audio_seconds": round(seconds, 3), "sample_rate": SAMPLE_RATE, "lines": len(lines),
            "seed": seed, "load_seconds": round(load_s, 2), "generate_seconds": round(gen_s, 2),
            "mlx_peak_gb": round(mx.get_peak_memory() / 1e9, 2),
            "process_peak_gb": peak_footprint_gb(),
            "rss_peak_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9, 2),
            "realtime_factor": round(seconds / gen_s, 2) if gen_s > 0 else None}
    say("done", json.dumps(done))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
