"""One loudness for every Hailuo H3 clip (5.0.9).

Symptom: clips from one H3 speed setting could sound clearly quieter than Best
on the same picture, prompt and seed. Measured on an M4 Max (same seed per
pair), the difference against Best in integrated loudness ran from +5.5 LU to
-3.1 LU depending on the clip.

Cause: H3 makes the sound and the picture together, and the level of the sound
is whatever the model lands on for that clip and that speed setting. Nothing
after the runner evened it out (the runner muxes the decoded waveform as is),
and the spread differs per clip, so there is no fixed offset to undo.

Fix: after the render, measure the clip (EBU R128 integrated loudness and true
peak) and set its sound to one target for every H3 render, whichever setting
made it, so all of them play at the same level. A plain gain, no compression;
a look-ahead limiter only where the gain would push the peaks over the
ceiling. The picture stream is copied untouched.

Left alone, on purpose:
- a clip whose sound is near silence (below SILENT_BELOW_LUFS): an ambience or
  "no sound" shot is quiet because it was asked to be, and lifting it would
  only lift hiss;
- clips within TOLERANCE_LU of the target (no re-encode for nothing);
- the caller decides the modes (only sound the model made itself) and skips
  storyboard shots, whose film master already levels the whole cut.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

TARGET_LUFS = -16.0          # integrated loudness every H3 clip is set to
TRUE_PEAK_CEILING = -1.5     # dBTP, measured on the delivered AAC
SILENT_BELOW_LUFS = -40.0    # quieter than this = an intentionally quiet shot: untouched
TOLERANCE_LU = 0.5           # already this close to the target: untouched
MAX_BOOST_DB = 18.0
MAX_CUT_DB = 18.0
# alimiter works on sample peaks; the AAC encode after it adds a little, so it
# is asked for a bit under the true-peak ceiling (checked again after encoding).
LIMITER_MARGIN_DB = 1.0
AUDIO_BITRATE = "192k"       # what the runner itself encodes at

ENV_SWITCH = "PHOSPHENE_H3_LEVEL_SOUND"   # "0" turns it off


def enabled() -> bool:
    return os.environ.get(ENV_SWITCH, "1").strip().lower() not in ("0", "off", "false", "no")


_I_RE = re.compile(r"^\s*I:\s*(-?[\d.]+|-inf)\s*LUFS", re.M)
_TP_RE = re.compile(r"^\s*Peak:\s*(-?[\d.]+|-inf)\s*dBFS", re.M)


def parse_ebur128(stderr: str) -> dict | None:
    """The Summary block of ffmpeg's ebur128 filter -> {"lufs", "tp"}; None without one."""
    at = stderr.rfind("Summary:")
    if at < 0:
        return None
    tail = stderr[at:]
    i, tp = _I_RE.search(tail), _TP_RE.search(tail)
    if not i:
        return None

    def num(m):
        return float("-inf") if m is None or m.group(1) == "-inf" else float(m.group(1))

    return {"lufs": num(i), "tp": num(tp)}


def has_audio(ffmpeg: str | Path, path: str | Path) -> bool:
    r = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)],
                       capture_output=True, text=True, timeout=60)
    return bool(re.search(r"Stream #\d+:\d+.*: Audio:", r.stderr))


def measure(ffmpeg: str | Path, path: str | Path) -> dict | None:
    """Integrated loudness and true peak of the first audio stream; None without audio."""
    r = subprocess.run([str(ffmpeg), "-nostats", "-hide_banner", "-i", str(path), "-vn",
                        "-map", "0:a:0", "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        return None
    return parse_ebur128(r.stderr)


def plan(measured: dict | None, target: float = TARGET_LUFS,
         ceiling: float = TRUE_PEAK_CEILING) -> dict:
    """What to do with a clip measured at `measured` - pure, so it is unit-tested.

    Returns {"action": "skip"|"gain", "reason", "gain_db", "limit"}; `limit` is
    the limiter ceiling in dBFS when the gained peaks would cross `ceiling`."""
    if not measured or measured.get("lufs") is None:
        return {"action": "skip", "reason": "no sound", "gain_db": 0.0, "limit": None}
    lufs, tp = float(measured["lufs"]), float(measured.get("tp", float("-inf")))
    if lufs == float("-inf") or lufs < SILENT_BELOW_LUFS:
        return {"action": "skip", "reason": f"near silence ({lufs:.1f} LUFS), kept as made",
                "gain_db": 0.0, "limit": None}
    gain = max(-MAX_CUT_DB, min(MAX_BOOST_DB, target - lufs))
    if abs(gain) < TOLERANCE_LU:
        return {"action": "skip", "reason": f"already at {lufs:.1f} LUFS", "gain_db": 0.0,
                "limit": None}
    limit = None
    if tp != float("-inf") and tp + gain > ceiling:
        limit = ceiling - LIMITER_MARGIN_DB
    return {"action": "gain", "reason": f"{lufs:.1f} -> {target:.1f} LUFS",
            "gain_db": round(gain, 2), "limit": limit}


def audio_seconds(ffprobe: str | Path | None, path: str | Path) -> float | None:
    """The first audio stream's own duration (the runner cuts it at frames / fps)."""
    if not ffprobe:
        return None
    try:
        r = subprocess.run([str(ffprobe), "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=60)
        value = float(r.stdout.strip().splitlines()[0])
        return value if value > 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def filter_chain(gain_db: float, limit: float | None, seconds: float | None = None) -> str:
    chain = [f"volume={gain_db:.2f}dB"]
    if limit is not None:
        lin = 10 ** (limit / 20.0)
        # attack 5 ms / release 50 ms: transparent on speech; level=disabled so the
        # limiter never adds its own make-up gain on top of ours; latency=1 so its
        # 5 ms look-ahead is compensated (without it the sound lands 5 ms late
        # against the picture and the last 5 ms are cut by the atrim below).
        chain.append(f"alimiter=limit={lin:.6f}:attack=5:release=50:level=disabled:latency=1")
    if seconds:
        # Without a stated length the AAC encoder's last-frame padding makes the sound
        # 10-30 ms longer than the picture; the runner cuts it at frames / fps, so do we.
        chain.append(f"atrim=duration={seconds:.6f}")
    return ",".join(chain)


def _apply(ffmpeg, src: Path, dst: Path, gain_db: float, limit: float | None,
           seconds: float | None = None) -> None:
    cmd = [str(ffmpeg), "-y", "-loglevel", "error", "-i", str(src),
           "-map", "0:v?", "-map", "0:a:0", "-c:v", "copy",
           "-af", filter_chain(gain_db, limit, seconds), "-c:a", "aac", "-b:a", AUDIO_BITRATE,
           "-movflags", "+faststart", str(dst)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if r.returncode != 0 or not dst.is_file():
        raise RuntimeError((r.stderr or "ffmpeg failed").strip()[-300:])


def level_clip(ffmpeg: str | Path, path: str | Path, target: float = TARGET_LUFS,
               ceiling: float = TRUE_PEAK_CEILING, ffprobe: str | Path | None = None) -> dict:
    """Set `path`'s sound to `target` in place (atomic). Never raises: on any
    failure the clip is left exactly as the runner wrote it and the record says why.

    Returns the record the sidecar keeps: before/after loudness, gain, limiter."""
    path = Path(path)
    rec: dict = {"target_lufs": target, "true_peak_ceiling": ceiling}
    # A hidden name, like the repo's other in-place writes: the gallery never
    # lists a half-written or orphaned temp.
    tmp = path.with_name(f".{path.stem}.part-level{path.suffix}")
    try:
        before = measure(ffmpeg, path)
        rec["before"] = before
        p = plan(before, target, ceiling)
        rec.update(action=p["action"], reason=p["reason"])
        if p["action"] != "gain":
            return rec
        gain, limit = p["gain_db"], p["limit"]
        secs = audio_seconds(ffprobe, path)
        _apply(ffmpeg, path, tmp, gain, limit, secs)
        after = measure(ffmpeg, tmp)
        # One correction: a limiter that had work to do takes a little loudness
        # with it, and an encode can land a peak over the ceiling.
        if after and limit is None and after["tp"] > ceiling:
            limit = ceiling - LIMITER_MARGIN_DB
            _apply(ffmpeg, path, tmp, gain, limit, secs)
            after = measure(ffmpeg, tmp)
        if after and limit is not None and target - after["lufs"] > TOLERANCE_LU:
            gain2 = min(MAX_BOOST_DB, gain + (target - after["lufs"]))
            _apply(ffmpeg, path, tmp, gain2, limit - 0.5, secs)
            after2 = measure(ffmpeg, tmp)
            if after2 and after2["tp"] <= ceiling:
                gain, limit, after = gain2, limit - 0.5, after2
            else:
                _apply(ffmpeg, path, tmp, gain, limit, secs)
                after = measure(ffmpeg, tmp)
        if not after or after["tp"] > ceiling + 0.3:
            raise RuntimeError(f"levelled clip measured {after}; kept the original")
        os.replace(tmp, path)
        rec.update(gain_db=round(gain, 2), limiter_dbfs=limit, after=after)
        return rec
    except Exception as exc:                                     # noqa: BLE001
        rec.update(action="kept", reason=f"{type(exc).__name__}: {exc}"[:300])
        try:
            tmp.unlink()
        except OSError:
            pass
        return rec


def describe(rec: dict) -> str:
    """One log line for the job log."""
    b, a = rec.get("before") or {}, rec.get("after") or {}
    if rec.get("action") == "gain":
        return (f"sound levelled: {b.get('lufs'):.1f} -> {a.get('lufs'):.1f} LUFS "
                f"(gain {rec.get('gain_db'):+.1f} dB, true peak {a.get('tp'):.1f} dBTP"
                + (", limiter on" if rec.get("limiter_dbfs") is not None else "") + ")")
    return f"sound kept as made: {rec.get('reason')}"
