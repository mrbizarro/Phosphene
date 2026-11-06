"""Speech — text to speech in preset voices or a character's own voice.

The panel-side half of Audio → Speech. Pure logic only (no panel globals), so
it is importable and testable on its own; mlx_ltx_panel.py owns the queue
glue (run_speech_job_inner, the install task) and calls in here.

Engine: Qwen3-TTS 12 Hz 1.7B (Apache-2.0, the Qwen team) through mlx-audio
(MIT), 8-bit MLX conversions by mlx-community, in its own Python 3.12 venv
(`speech-mlx/.venv`; scripts/speech/speech_constraints.txt is the known-good
set). Two checkpoints of the same model:

  customvoice  nine preset speakers + a free-text delivery instruction
  base         a voice cloned from a short clip of someone speaking

Why this model: see docs/SPEECH.md ("Choosing the model"). In one line: the
only candidate that is Apache/MIT end to end, runs natively on MLX, clones
from a few seconds, AND takes emotion as words — at ~4 GB peak, so it runs on
a 16 GB Mac next to the panel.

SAFETY: cloning a voice requires the caller to confirm, per request, that the
voice is their own or that they have the speaker's permission (Phosphene's
acceptable-use policy forbids impersonation). make_job refuses a clone job
without `speech_voice_consent`; the confirmation is written into the output's
sidecar. There are no celebrity presets — the presets are the model's own
fictional speakers.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

SPEECH_MIN_RAM_GB = 16.0
SPEECH_SAMPLE_RATE = 24000
SPEECH_MAX_CHARS = 2000
# Words per second of natural English read speech at Speed 1.0 — the
# dialogue-fit hint ("about 6 s") and the length estimate both use it.
WORDS_PER_SECOND = 2.8
# Measured on an M4 Max (GPU, 8-bit, 2026-10-22; docs/SPEECH.md → Memory and
# speed): a job is ~3 s of process start + model load, then 3.5–5x realtime.
EST_LOAD_SEC = 3.0
EST_SEC_PER_AUDIO_SEC = 0.25
# A lip-sync clip is one LTX window (~10 s at 24 fps, 257 frames max).
LIPSYNC_MAX_SEC = 10.0

ENV_STRIP = ("PYTORCH_ENABLE_MPS_FALLBACK", "PYTORCH_MPS_FAST_MATH")

# The model's own speakers (model card: Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice).
# Fictional voices — no real person is imitated. English speakers first.
PRESETS: tuple[dict, ...] = (
    {"id": "ryan", "label": "Ryan", "sub": "dynamic male voice, strong rhythm", "native": "English"},
    {"id": "aiden", "label": "Aiden", "sub": "sunny American male, clear midrange", "native": "English"},
    {"id": "serena", "label": "Serena", "sub": "warm, gentle young female", "native": "Chinese"},
    {"id": "vivian", "label": "Vivian", "sub": "bright, slightly edgy young female", "native": "Chinese"},
    {"id": "uncle_fu", "label": "Uncle Fu", "sub": "seasoned male, low and mellow", "native": "Chinese"},
    {"id": "dylan", "label": "Dylan", "sub": "youthful male, clear and natural", "native": "Chinese (Beijing)"},
    {"id": "eric", "label": "Eric", "sub": "lively male, slightly husky", "native": "Chinese (Sichuan)"},
    {"id": "ono_anna", "label": "Ono Anna", "sub": "playful female, light and nimble", "native": "Japanese"},
    {"id": "sohee", "label": "Sohee", "sub": "warm female, rich emotion", "native": "Korean"},
)
PRESET_IDS = frozenset(p["id"] for p in PRESETS)

# One-click delivery styles. Each is the instruction text the model reads;
# the box under them takes any other wording ("whispering, like a secret").
STYLES: tuple[dict, ...] = (
    {"id": "natural", "label": "Natural", "instruct": ""},
    {"id": "warm", "label": "Warm", "instruct": "Warm and friendly, smiling while speaking."},
    {"id": "excited", "label": "Excited", "instruct": "Very excited and energetic, fast and bright."},
    {"id": "deadpan", "label": "Deadpan", "instruct": "Dry and deadpan, flat delivery, completely serious."},
    {"id": "dramatic", "label": "Dramatic", "instruct": "Grand and theatrical, like a ringmaster announcing the show."},
    {"id": "whisper", "label": "Whisper", "instruct": "Whispering softly, close to the microphone, secretive."},
    {"id": "sad", "label": "Sad", "instruct": "Sad and quiet, slow, on the edge of tears."},
    {"id": "angry", "label": "Angry", "instruct": "Angry and sharp, forceful, clipped words."},
)
STYLE_BY_ID = {s["id"]: s for s in STYLES}

LANGUAGES = ("auto", "english", "chinese", "japanese", "korean", "german",
             "french", "russian", "portuguese", "spanish", "italian")


class SpeechRequestError(ValueError):
    """A speech request that cannot be queued as asked (400, plain words)."""


def _v(form: dict, key: str, default: str = "") -> str:
    v = form.get(key, default)
    if isinstance(v, list):
        v = v[0] if v else default
    return str(v if v is not None else default).strip()


def _truthy(s: str) -> bool:
    return s.lower() in ("1", "true", "yes", "on")


def speech_params(form: dict, normalize_path=lambda p: p) -> dict:
    """The explicit allowlist (queue construction, dispatch, replays).
    Every field a job can carry is named here; anything else is dropped."""
    text = _v(form, "speech_text").replace("\r\n", "\n")
    kind = _v(form, "speech_voice_kind", "preset")
    kind = kind if kind in ("preset", "character", "clip") else "preset"
    style_id = _v(form, "speech_style", "natural")
    style_id = style_id if style_id in STYLE_BY_ID else "natural"
    instruct = _v(form, "speech_instruct")[:300]
    try:
        speed = float(_v(form, "speech_speed", "1.0") or 1.0)
    except ValueError:
        speed = 1.0
    try:
        seed = int(_v(form, "speech_seed", "-1") or -1)
    except ValueError:
        seed = -1
    lang = _v(form, "speech_language", "english").lower()
    return {
        "mode": "speech", "engine": "speech",
        "speech_text": text[:SPEECH_MAX_CHARS + 1],
        "speech_voice_kind": kind,
        "speech_voice": _v(form, "speech_voice").lower()[:64],
        # A character id (trigger) or an uploaded clip path. Resolved and
        # checked by validate_speech(); the job stores the resolved path.
        "speech_character": _v(form, "speech_character")[:64],
        "speech_ref_audio": normalize_path(_v(form, "speech_ref_audio")),
        "speech_ref_text": _v(form, "speech_ref_text")[:600],
        "speech_style": style_id,
        "speech_instruct": instruct,
        "speech_speed": max(0.5, min(2.0, speed)),
        "speech_language": lang if lang in LANGUAGES else "english",
        "speech_seed": max(-1, min(2**31 - 1, seed)),
        "speech_title": _v(form, "speech_title")[:120],
        "speech_voice_consent": _truthy(_v(form, "speech_voice_consent")),
    }


def instruction_for(p: dict) -> str:
    """The delivery instruction the preset model reads: the typed one wins,
    else the picked style's. Clones take none (the Base model has no
    instruction input — delivery follows the clip and the punctuation)."""
    if p.get("speech_voice_kind") != "preset":
        return ""
    return (p.get("speech_instruct") or STYLE_BY_ID.get(p.get("speech_style") or "", {}).get("instruct") or "").strip()


def words(text: str) -> int:
    return len(re.findall(r"[\w']+", text or ""))


def estimate(text: str, speed: float = 1.0, speed_factor: float = 1.0) -> dict:
    """Audio length from the word count, wall time from the measured rate.
    Always labelled an estimate."""
    spoken = words(text) / WORDS_PER_SECOND if text else 0.0
    # The model always speaks at its own pace; Speed is an ffmpeg tempo pass
    # afterwards, so it changes the file's length, not the time to make it.
    audio = spoken / max(0.5, speed)
    wall = (EST_LOAD_SEC + spoken * EST_SEC_PER_AUDIO_SEC) * speed_factor
    return {"audio_sec": round(audio, 1), "eta_sec": round(wall), "lipsync_fits": audio <= LIPSYNC_MAX_SEC}


def validate_speech(p: dict, characters: list[dict]) -> dict:
    """Refuse what would render something else, before it is queued.
    Returns the params with the reference clip resolved for clone kinds."""
    text = p.get("speech_text") or ""
    if not text.strip():
        raise SpeechRequestError("Write the line to speak.")
    if len(text) > SPEECH_MAX_CHARS:
        raise SpeechRequestError(f"The script is longer than {SPEECH_MAX_CHARS} characters. Split it into shorter takes.")
    kind = p["speech_voice_kind"]
    if kind == "preset":
        if p["speech_voice"] not in PRESET_IDS:
            raise SpeechRequestError("Pick one of the voices in the list.")
        return {**p, "speech_ref_audio": "", "speech_ref_text": ""}
    # A cloned voice. The confirmation is not optional and is asked per job.
    if not p.get("speech_voice_consent"):
        raise SpeechRequestError(
            "Cloning a voice needs your confirmation that it is your own voice or that you "
            "have the speaker's permission. Tick the box under the voice, then Generate again.")
    if kind == "character":
        cid = p.get("speech_character") or ""
        ch = next((c for c in characters if c.get("id") == cid), None)
        if ch is None:
            raise SpeechRequestError(f"There is no character called {cid or '(none)'} on this Mac.")
        ref = ch.get("voice_sample") or ""
        if not ref or not Path(ref).is_file():
            raise SpeechRequestError(
                f"{ch.get('name') or cid} has no voice sample. Add one in Train, or pick a preset voice.")
        return {**p, "speech_ref_audio": ref, "speech_voice": "", "speech_character_name": ch.get("name") or cid}
    ref = p.get("speech_ref_audio") or ""
    if not ref:
        raise SpeechRequestError("Add a short clip of the voice (5 to 20 seconds of clear speech).")
    if not Path(ref).is_file():
        raise SpeechRequestError(f"There is no audio file at {ref}.")
    if Path(ref).suffix.lower() not in (".wav", ".mp3", ".m4a", ".flac", ".aac", ".ogg", ".mp4", ".mov"):
        raise SpeechRequestError("The voice clip must be an audio file (WAV, MP3, M4A or FLAC).")
    return {**p, "speech_voice": "", "speech_character": ""}


def child_env(base: dict | None = None, path: str | None = None) -> dict:
    """The runner's env, decided rather than inherited (phosphene-release §0)."""
    env = {k: v for k, v in (base if base is not None else os.environ).items()
           if k not in ENV_STRIP and k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV")}
    env["PYTHONUNBUFFERED"] = "1"
    env["HF_HUB_OFFLINE"] = "1"          # weights are local; never fetch at render time
    env["TOKENIZERS_PARALLELISM"] = "false"
    if path is not None:
        env["PATH"] = path
    return env


def argv(python: Path, runner: Path, models: Path, p: dict, text_file: Path, ref_audio: Path | None,
         out: Path) -> list[str]:
    kind = "preset" if p["speech_voice_kind"] == "preset" else "clone"
    cmd = [str(python), str(runner), "--pack", str(models), "--kind", kind,
           "--language", p["speech_language"], "--speed", f"{p['speech_speed']:.2f}",
           "--seed", str(p["speech_seed"]), "--text-file", str(text_file), "--out", str(out)]
    if kind == "preset":
        cmd += ["--voice", p["speech_voice"]]
        style = instruction_for(p)
        if style:
            cmd += ["--style", style]
    else:
        cmd += ["--ref-audio", str(ref_audio)]
        if p.get("speech_ref_text"):
            cmd += ["--ref-text", p["speech_ref_text"]]
    return cmd


_LINE_RE = re.compile(r"^\[speech\] (stage|line|done|error)\s*(.*)$")


def progress(line: str) -> dict | None:
    """Decode the runner's protocol for the Now card."""
    m = _LINE_RE.match(line.strip())
    if not m:
        return None
    kind, rest = m.group(1), m.group(2).strip()
    if kind == "stage":
        return {"phase": rest, "phase_label": {"load": "Loading the voice model",
                                               "write": "Writing the WAV"}.get(rest, rest), "pct": 5 if rest == "load" else 95}
    if kind == "line":
        try:
            i, n = (int(x) for x in rest.split("/"))
        except ValueError:
            return None
        return {"phase": "speak", "phase_label": f"Speaking line {i} of {n}" if n > 1 else "Speaking",
                "pct": int(10 + 80 * (i - 1) / max(1, n))}
    if kind == "done":
        return {"phase": "done", "phase_label": "Done", "pct": 100}
    return {"phase": "error", "phase_label": rest[:200], "pct": 0}


def output_stem(p: dict, stamp: str, slug: str) -> str:
    return f"speech_{stamp}_{slug}" if slug else f"speech_{stamp}"


def voice_label(p: dict) -> str:
    kind = p.get("speech_voice_kind")
    if kind == "preset":
        return next((x["label"] for x in PRESETS if x["id"] == p.get("speech_voice")), p.get("speech_voice") or "")
    if kind == "character":
        return p.get("speech_character_name") or p.get("speech_character") or "character"
    return "your clip"


def voices_payload(characters: list[dict]) -> dict:
    """What the voice picker offers. Characters appear only when they carry a
    voice sample (the clip is what the model clones from)."""
    return {
        "presets": list(PRESETS),
        "styles": [{"id": s["id"], "label": s["label"], "instruct": s["instruct"]} for s in STYLES],
        "characters": [{"id": c["id"], "name": c.get("name") or c["id"],
                        "has_sample": bool(c.get("voice_sample"))}
                       for c in characters if c.get("voice_sample")],
        "languages": list(LANGUAGES),
    }
