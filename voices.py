"""Saved voice samples and character assignments. No model imports in the server.

Samples live with personal state, never in the shipped preset catalogue. Each
write is atomic; an interrupted import cannot become a selectable voice.
"""
from __future__ import annotations

import json
from functools import lru_cache
import math
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
import wave

import voice_identity

MAX_UPLOAD_BYTES = 256 * 1024 * 1024
_LOCK = threading.RLock()


@lru_cache(maxsize=128)
def _character_sample_profile(path, size, mtime_ns, version):
    # File identity is part of the key: replacing a sample invalidates the profile.
    try:
        return voice_identity.profile(read_mono(Path(path)), 24000)
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def safe_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value or ""):
        raise ValueError("Invalid voice or character ID")
    return value


class VoiceLibrary:
    def __init__(self, state_dir: Path):
        self.root = Path(state_dir) / "voices"

    def folder(self, voice_id: str) -> Path:
        path = self.root / safe_id(voice_id)
        if not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Invalid voice path")
        return path

    def get(self, voice_id: str) -> dict:
        folder = self.folder(voice_id)
        try:
            record = json.loads((folder / "voice.json").read_text())
        except FileNotFoundError:
            raise ValueError("Voice not found") from None
        if record.get("id") != voice_id:
            raise ValueError("Invalid voice record")
        return record

    def list(self) -> list[dict]:
        records = []
        for file in sorted(self.root.glob("*/voice.json")):
            try:
                records.append(self.get(file.parent.name))
            except (ValueError, OSError):
                continue
        return sorted(records, key=lambda r: r["name"].casefold())

    def save(self, record: dict) -> None:
        with _LOCK:
            folder = self.folder(record["id"])
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / "voice.json.tmp"
            tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
            tmp.replace(folder / "voice.json")

    def sample(self, voice_id: str) -> Path:
        self.get(voice_id)
        path = self.folder(voice_id) / "sample.wav"
        if not path.is_file() or not path.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Voice sample is missing; add the clip again")
        return path

    def identity(self, voice_id: str) -> dict:
        """The voice's record with its speaker profile current: pitch statistics
        of its own (cleaned) sample and the speaker description take prompts
        name. Computed once per sample and kept in voice.json, so an existing
        voice gets one the first time it is listed or used."""
        with _LOCK:
            record = self.get(voice_id)
            try:
                sample = self.sample(voice_id)
            except ValueError:
                return record
            stamp = [sample.stat().st_size, int(sample.stat().st_mtime)]
            known = record.get("identity") or {}
            if known.get("sample") == stamp and known.get("version") == voice_identity.PROFILE_VERSION:
                return record
            try:
                prof = voice_identity.profile(read_mono(sample), 24000) if stamp[0] else None
            except (OSError, subprocess.SubprocessError, ValueError):
                prof = None  # an unreadable sample: no description, no pitch check
            record["identity"] = dict(prof or {"version": voice_identity.PROFILE_VERSION}, sample=stamp)
            record["speaker_auto"] = voice_identity.describe(prof)
            self.save(record)
            return record

    def set_speaker(self, voice_id: str, text: str) -> dict:
        """The creator's own words for who is speaking; empty goes back to the automatic one."""
        text = " ".join(str(text or "").split())
        if len(text) > 120:
            raise ValueError("Keep the description under 120 characters")
        if re.search(r'["<>{}\[\]]', text):
            raise ValueError("Describe the speaker in plain words, without quotes or brackets")
        with _LOCK:
            record = self.identity(voice_id)
            if text:
                record["speaker"] = text
            else:
                record.pop("speaker", None)
            self.save(record)
            return record

    def import_clip(self, source: Path, name: str) -> dict:
        name = str(name).strip()
        if not name or len(name) > 100:
            raise ValueError("Give the voice a name (up to 100 characters)")
        if source.stat().st_size > MAX_UPLOAD_BYTES:
            raise ValueError("Choose a clip smaller than 256 MB")
        # Probe the audio stream, not the container: a silent video must fail,
        # and a long video with a short soundtrack must not pass by accident.
        probe = subprocess.run([
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type,duration:format=duration",
            "-of", "json", str(source)], capture_output=True, check=True, timeout=30)
        media = json.loads(probe.stdout)
        streams = media.get("streams") or []
        if not streams:
            raise ValueError("Choose a clip with someone talking")
        raw_duration = streams[0].get("duration")
        if raw_duration in (None, "N/A"):
            raw_duration = media.get("format", {}).get("duration")
        duration = float(raw_duration) if raw_duration not in (None, "N/A") else None
        if duration is not None and (not math.isfinite(duration) or not 10 <= duration <= 60.1):
            raise ValueError("Choose 10–60 seconds of talking")
        voice_id = "voice_" + uuid.uuid4().hex[:16]
        folder = self.folder(voice_id)
        folder.mkdir(parents=True)
        try:
            subprocess.run([
                "ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source),
                "-map", "0:a:0", "-vn", "-t", "61", "-ac", "1", "-ar", "24000",
                "-af", "highpass=f=65,alimiter=limit=0.95", str(folder / "sample.wav")],
                capture_output=True, check=True, timeout=120)
            # MediaRecorder WebM has no duration until decoded. Measure the
            # bounded PCM output rather than rejecting a valid browser recording.
            with wave.open(str(folder / "sample.wav"), "rb") as decoded:
                duration = decoded.getnframes() / decoded.getframerate()
            if not 10 <= duration <= 60.1:
                raise ValueError("Choose 10–60 seconds of talking")
            record = {"id": voice_id, "name": name, "seconds": min(duration, 60),
                      "cleaning": "filtered", "training": "untrained"}
            self.save(record)
            return record
        except BaseException:
            shutil.rmtree(folder)
            raise

    def assignments(self) -> dict:
        try:
            return json.loads((self.root / "characters.json").read_text())
        except FileNotFoundError:
            return {}

    def assign(self, character_id: str, voice_id: str) -> None:
        safe_id(character_id)
        if voice_id:
            self.sample(voice_id)
        with _LOCK:
            assignments = self.assignments()
            if voice_id:
                assignments[character_id] = voice_id
            else:
                assignments.pop(character_id, None)
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / "characters.json.tmp"
            tmp.write_text(json.dumps(assignments) + "\n")
            tmp.replace(self.root / "characters.json")

    def remove(self, voice_id: str) -> None:
        """Delete a saved voice and every character link to it."""
        self.get(voice_id)
        with _LOCK:
            assignments = {c: v for c, v in self.assignments().items() if v != voice_id}
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.root / "characters.json.tmp"
            tmp.write_text(json.dumps(assignments) + "\n")
            tmp.replace(self.root / "characters.json")
            shutil.rmtree(self.folder(voice_id))

    def resolve(self, explicit: str | None, character_id: str = "") -> dict | None:
        # Missing means inherit; an explicit empty value means Default. Keep
        # that distinction in sidecars so replay never acquires a new voice.
        voice_id = explicit if explicit is not None else self.assignments().get(character_id, "")
        if not voice_id:
            return None
        record = self.get(voice_id)
        return dict(record, sample_path=str(self.sample(voice_id)))

    def resolve_form(self, form: dict, character_id: str = "") -> dict | None:
        def value(key, default=""):
            item = form.get(key, default)
            return str((item[0] if item else default) if isinstance(item, list) else item)
        if value("no_voice", "off").lower() in ("on", "true", "1", "yes"):
            return None
        choice = value("voice_id", "inherit")
        # URL-encoded forms drop blank values; a nonempty Default token must
        # survive that parser rather than silently inheriting the character.
        if choice == "default":
            choice = ""
        return self.resolve(None if choice == "inherit" else choice,
                            value("voice_character_id", character_id))


def read_mono(path, rate=24000):
    """A sound file as mono float samples at `rate` (ffmpeg decode)."""
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-v", "error", "-nostdin", "-i", str(path), "-ac", "1", "-ar", str(rate),
                          "-f", "f32le", "-"], capture_output=True, check=True, timeout=120).stdout
    return np.frombuffer(raw, np.float32).copy()


def speaker_text(record: dict) -> str:
    """Who a take prompt says is speaking: the creator's words, else the sample's."""
    return str(record.get("speaker") or record.get("speaker_auto") or "").strip()


def plain_error(exc: BaseException, cancelled: bool = False) -> str:
    """What went wrong while getting a voice ready, and what to do, in words a
    creator reads. The raw tail stays in the voice's training.log."""
    raw = str(exc or "")
    low = raw.lower()
    if cancelled or "stopped before" in low:
        return "Stopped before it finished. Press Try again to finish this voice."
    if "out of memory" in low or "insufficient memory" in low or "memoryerror" in low:
        return "Your Mac ran out of memory while learning this voice. Close other apps, then press Try again."
    if "no space left" in low or "disk full" in low:
        return "Your disk is full. Free some space, then press Try again."
    if any(k in low for k in ("urlopen", "connection", "timed out", "temporary failure", "httperror", "download")):
        return "Something it needs could not be downloaded. Check your internet connection, then press Try again."
    if "no vocals" in low or "silent" in low or "no speech" in low:
        return "No clear voice was heard in this clip. Try 10–60 seconds of one person talking, without music."
    last = next((line.strip() for line in reversed(raw.splitlines()) if line.strip()), "")
    last = re.sub(r"^(\w+(Error|Exception)):\s*", "", last)[:160]
    return ("Learning this voice stopped" + (f": {last}" if last else "") + ". Press Try again.")


def resolve_render_voice(library, form, characters, character_id=""):
    """Resolve the user's choice; inherit a character's trained adapter directly."""
    import unicodedata
    def _nk(s):
        return unicodedata.normalize("NFC", str(s or "")).strip().casefold()
    def value(key, default=""):
        v = form.get(key, default)
        return str((v[0] if v else default) if isinstance(v, list) else v)
    if value("no_voice", "off").lower() in ("on", "true", "1", "yes"):
        return None
    choice = value("voice_id", "inherit")
    character_id = value("voice_character_id", character_id)
    if choice.startswith("character:"):
        character_id = choice.split(":", 1)[1]
        choice = "inherit"
    def aliases(c):
        name = re.sub(r"\s*\([^)]*\)\s*$", "", str(c.get("name") or ""))
        return {_nk(c["id"]), _nk(c.get("trigger") or ""),
                _nk(name), _nk(c.get("name") or "")}
    character = next((c for c in characters if character_id and _nk(character_id) in aliases(c)), None)
    # An explicit library selection overrides inheritance; otherwise an existing
    # character adapter is authoritative, even if a sample was assigned earlier.
    if choice == "inherit" and character and character.get("audio_lora_path"):
        adapter = Path(character["audio_lora_path"])
        if adapter.is_file():
            voice = {"id": "character:" + character["id"], "name": character.get("name") or character["id"],
                     "trigger": character.get("trigger") or character["id"],
                     "adapter_path": str(adapter), "sample_path": character.get("voice_sample") or ""}
            sample = Path(voice["sample_path"])
            if sample.is_file():
                try:
                    stat = sample.stat()
                    prof = _character_sample_profile(str(sample.resolve()), stat.st_size, stat.st_mtime_ns,
                                                     voice_identity.PROFILE_VERSION)
                except (OSError, subprocess.SubprocessError):
                    prof = None
                voice.update(identity=dict(prof) if prof else {}, speaker=voice_identity.describe(prof))
            return voice
    # A character picker value is an inheritance request, not a library ID.
    # Normalize it before falling back to a picture-only character's saved voice.
    resolved_character = character["id"] if character else character_id
    record = library.resolve_form(dict(form, voice_id=choice, voice_character_id=resolved_character),
                                  resolved_character)
    if record:
        record.update(library.identity(record["id"]), sample_path=record["sample_path"])
        record["speaker"] = speaker_text(record)
        record["trigger"] = record.get("trigger") or record["id"]
        record["adapter_path"] = str(library.folder(record["id"]) / "voice.audio.safetensors") if record.get("training") == "ready" else ""
    return record


def resolve_speaker_voice(library, speaker, characters, speaker_voices=None):
    """Resolve a line's speaker label to an explicit map override, a character voice, or a standalone saved voice."""
    import unicodedata
    def _nk(s):
        return unicodedata.normalize("NFC", str(s or "")).strip().casefold()
    who = str(speaker or "").strip()
    if not who:
        return None
    key = _nk(who)
    if isinstance(speaker_voices, dict):
        mapped = ""
        for k, v in speaker_voices.items():
            if _nk(k) == key:
                mapped = str(v or "").strip()
                break
        if mapped and mapped != "default":
            picked = resolve_render_voice(library, {"voice_id": mapped}, characters, who)
            if picked:
                return picked
    picked = resolve_render_voice(library, {"voice_id": "inherit"}, characters, who)
    if picked:
        return picked
    for v in library.list():
        vname = re.sub(r"\s*\([^)]*\)\s*$", "", str(v.get("name") or "")).strip()
        aliases = {_nk(v.get("id") or ""), _nk(v.get("name") or ""),
                   _nk(vname), _nk(v.get("trigger") or "")}
        if key in aliases:
            return resolve_render_voice(library, {"voice_id": v["id"]}, characters, "")
    return None
