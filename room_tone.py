#!/usr/bin/env python3
"""Room tone — a generated ambience bed that runs under a whole film.

THE OWNER'S CASE (2026-09-17): "the cuts are very rough in terms of sound, and
it's really annoying. If it was all over the timeline as an ambient sound, not
something really subtle … it could be generated for the video each time."

Measured on DIVORCE v2: the clips' own noise floors run from −23 to −68 dBFS,
so a cut can drop the room by 26 dB in one frame. A continuous bed under the
whole timeline hides that step, the way every film mixer lays room tone.

WHAT THIS FILE DOES — numpy only, CPU, about a second:

  * **From this film** (`film`): decode each timeline clip's window, keep its
    quietest analysis windows (the room tone and tape hiss between lines),
    average their spectra per clip, take the per-bin MEDIAN across clips. That
    is the film's own ambience shape. Too little quiet → the "Quiet room"
    preset, and the result says so.
  * **Presets**: spectral tilt + resonant bands + hum harmonics + slow movement
    (+ crickets / rain ticks), one parameter row each.
  * **A new seed is a new take**, so there are as many versions as clicks.

HOW IT STAYS SEAMLESS: the loop is built in the frequency domain — a magnitude
per FFT bin times a random phase, then ONE inverse FFT over a `LOOP_S` grid. A
signal made that way is periodic with exactly that period, so tiling it has no
seam. Everything layered on top (hum on exact bins, the movement LFO, the
cricket pulses, the rain ticks) completes whole cycles over the same grid.

HOW LOUD: loudness is BS.1770 (K-weighted mean square, −0.691 offset), computed
from the loop's own spectrum, which is exact for a periodic signal. The file is
written at `REF_LUFS` (peak capped at −1 dBFS; if that cap bites, the real
reference is returned) and the level the user picks becomes the TRACK FADER,
`level_gain(level, ref)`. So a level change is instant and is the very number
the render applies. Gating is not modelled: the bed moves by a few dB at most,
and BS.1770's −70 LUFS / −10 LU gates never close on it.

Run the CLI:  python3 room_tone.py --variant film --clip a.mp4 --seconds 30 out.wav
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 48000
LOOP_S = 40                      # the periodic grid, seconds (bins are 1/40 Hz)
REF_LUFS = -18.0                 # what the file is written at
PEAK_CAP = 10 ** (-1.0 / 20)     # −1 dBFS
DEFAULT_LEVEL = -27.0            # "not something really subtle"
LEVEL_MIN, LEVEL_MAX = -45.0, -18.0
DEFAULT_VARIANT = "film"
FALLBACK_VARIANT = "quiet_room"
MIN_QUIET_S = 1.5                # less usable quiet than this → fallback
BED_STEP_S = 30                  # file length is rounded up to this
BED_MAX_S = 1800
STEREO_CORR = 0.35               # shared share of the two channels' power
WIN = 4096                       # analysis window (85 ms)
HOP = 2048

# ---------------------------------------------------------------------------
# THE PRESETS
# ---------------------------------------------------------------------------
# tilt: dB per octave around 1 kHz · hp/lp: 12 dB/oct corners · bands:
# (centre Hz, dB, width in octaves) · hum: (Hz, dB relative to the broadband
# RMS) · move: slow level movement, ± dB · lfo: which whole cycles over the
# loop the movement is made of · extra: a texture layer.
_PRESETS: list[dict] = [
    {"id": "quiet_room", "label": "Quiet room",
     "blurb": "A still interior — soft air, a faint mains hum.",
     "tilt": -4.5, "hp": 40, "lp": 9000, "bands": [(120, 4, 0.8)],
     "hum": [(50, -20)], "move": 1.0},
    {"id": "living_room", "label": "Living room",
     "blurb": "Warmer, a fridge somewhere in the flat.",
     "tilt": -5.0, "hp": 30, "lp": 7000, "bands": [(90, 6, 0.7), (300, 2, 1.0)],
     "hum": [(50, -18), (100, -24)], "move": 1.5},
    {"id": "office", "label": "Office",
     "blurb": "Air conditioning, fluorescent hum.",
     "tilt": -3.0, "hp": 40, "lp": 12000, "bands": [(200, 5, 1.2), (1500, 2, 1.0)],
     "hum": [(60, -20), (120, -22), (180, -28)], "move": 1.0},
    {"id": "tv_studio", "label": "TV studio",
     "blurb": "A big treated room — air handling, a bright top.",
     "tilt": -2.5, "hp": 30, "lp": 14000, "bands": [(80, 5, 0.8), (2500, 3, 1.2)],
     "hum": [(60, -24)], "move": 0.8},
    {"id": "kitchen", "label": "Kitchen",
     "blurb": "A fridge compressor and a hard room.",
     "tilt": -4.0, "hp": 40, "lp": 10000, "bands": [(250, 3, 1.0), (1800, 2, 0.8)],
     "hum": [(100, -10), (200, -16), (300, -22), (400, -26)], "move": 1.2},
    {"id": "car", "label": "Car interior",
     "blurb": "Road rumble, engine drone, moving.",
     "tilt": -8.0, "hp": 20, "lp": 5000,
     "bands": [(45, 10, 0.8), (110, 6, 0.8), (1000, 3, 1.5)],
     "hum": [(32, -16)], "move": 2.5, "lfo": (1, 3, 7, 19, 31, 43)},
    {"id": "outdoor_day", "label": "Outdoors, day",
     "blurb": "Wind in gusts, open air.",
     "tilt": -6.0, "hp": 25, "lp": 16000, "bands": [(60, 6, 1.2), (3500, 4, 1.5)],
     "move": 4.0, "lfo": (1, 2, 3, 5, 9, 14)},
    {"id": "night_outside", "label": "Night outside",
     "blurb": "Still night air and crickets.",
     "tilt": -3.0, "hp": 60, "lp": 12000, "bands": [(200, 2, 1.0)],
     "move": 1.0, "extra": {"kind": "crickets", "fc": 4700, "db": -4}},
    {"id": "city_street", "label": "City street",
     "blurb": "Distant traffic, a low city wash.",
     "tilt": -5.0, "hp": 25, "lp": 11000, "bands": [(70, 8, 1.0), (500, 4, 1.2)],
     "move": 3.5, "lfo": (1, 2, 4, 7, 11)},
    {"id": "rain", "label": "Rain",
     "blurb": "Steady rain on a window.",
     "tilt": -1.5, "hp": 150, "lp": 16000, "bands": [(2500, 5, 1.5), (8000, 3, 1.0)],
     "move": 1.5, "extra": {"kind": "ticks", "per_s": 90, "db": -9}},
    {"id": "vhs_tape", "label": "VHS tape",
     "blurb": "Tape hiss, mains hum and the line whine.",
     "tilt": 0.5, "hp": 60, "lp": 13000, "bands": [(4000, 4, 1.5)],
     "hum": [(60, -14), (180, -22), (15734, -30)], "move": 0.5},
    {"id": "big_hall", "label": "Big hall",
     "blurb": "A large dark space, low and wide.",
     "tilt": -7.0, "hp": 25, "lp": 5000, "bands": [(100, 6, 1.2), (400, 3, 1.0)],
     "move": 2.0},
    {"id": "plane_cabin", "label": "Plane cabin",
     "blurb": "The steady roar of a cabin in flight.",
     "tilt": -4.0, "hp": 30, "lp": 9000, "bands": [(300, 8, 1.2), (120, 5, 0.7)],
     "hum": [(400, -22)], "move": 0.6},
]
PRESETS = {p["id"]: p for p in _PRESETS}
FILM_VARIANT = {"id": "film", "label": "From this film",
                "blurb": "Made from the quiet moments of your own clips."}


def variants() -> list[dict]:
    """The picker's rows, film first: `[{id, label, blurb}]`."""
    return [dict(FILM_VARIANT)] + [
        {"id": p["id"], "label": p["label"], "blurb": p["blurb"]} for p in _PRESETS]


def variant_ids() -> list[str]:
    return [v["id"] for v in variants()]


def variant_label(vid: str) -> str:
    for v in variants():
        if v["id"] == vid:
            return v["label"]
    return str(vid)


def clamp_level(v, default: float = DEFAULT_LEVEL) -> float:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x:
        return default
    return round(max(LEVEL_MIN, min(LEVEL_MAX, x)), 2)


def level_gain(level: float, ref: float = REF_LUFS) -> float:
    """The track fader that turns a file at `ref` LUFS into `level` LUFS.
    Capped at 1 — a fader never boosts (the tracks' model is 0..1)."""
    g = 10 ** ((clamp_level(level) - float(ref)) / 20.0)
    return round(max(0.0, min(1.0, g)), 6)


def gain_level(gain: float, ref: float = REF_LUFS) -> float:
    """The inverse: what level a fader value plays a bed at."""
    g = max(1e-6, float(gain))
    return round(float(ref) + 20 * math.log10(g), 2)


def bed_seconds(film_len: float) -> int:
    """The file length for a film: rounded UP to `BED_STEP_S`, so trims and
    small moves never need a new file."""
    n = max(0.0, float(film_len or 0.0)) + 0.5
    return int(min(BED_MAX_S, max(BED_STEP_S, math.ceil(n / BED_STEP_S) * BED_STEP_S)))


# ---------------------------------------------------------------------------
# LOUDNESS — BS.1770 K-weighting, in the frequency domain
# ---------------------------------------------------------------------------
_K_SHELF = ((1.53512485958697, -2.69169618940638, 1.19839281085285),
            (1.0, -1.69065929318241, 0.73248077421585))
_K_HP = ((1.0, -2.0, 1.0), (1.0, -1.99004745483398, 0.99007225036621))


def _biquad_power(b, a, freqs, sr=SR):
    z = np.exp(-1j * 2 * np.pi * np.asarray(freqs, dtype=np.float64) / sr)
    num = b[0] + b[1] * z + b[2] * z * z
    den = a[0] + a[1] * z + a[2] * z * z
    return np.abs(num / den) ** 2


def k_weight_power(freqs, sr: int = SR) -> np.ndarray:
    """|H_K(f)|² at 48 kHz — the two BS.1770 stages."""
    return (_biquad_power(*_K_SHELF, freqs, sr) * _biquad_power(*_K_HP, freqs, sr))


def loudness_lufs(x: np.ndarray, sr: int = SR) -> float:
    """Ungated BS.1770 loudness of `x` ((channels, n) or (n,)), exact for a
    periodic signal and a close estimate for a steady one."""
    x = np.atleast_2d(np.asarray(x, dtype=np.float64))
    n = x.shape[1]
    if n < 2:
        return -120.0
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    kw = k_weight_power(freqs, sr)
    kw[0] = 0.0
    total = 0.0
    for ch in x:
        X = np.fft.rfft(ch)
        p = np.abs(X) ** 2 * kw
        # Parseval: mean(x²) = (|X0|² + 2Σ|Xk|²) / n², Nyquist counted once.
        s = 2.0 * p[1:].sum()
        if n % 2 == 0:
            s -= p[-1]
        total += s / (n * n)
    if total <= 0:
        return -120.0
    return round(-0.691 + 10 * math.log10(total), 3)


# ---------------------------------------------------------------------------
# SPECTRA
# ---------------------------------------------------------------------------
def _db_shape(p: dict, freqs: np.ndarray) -> np.ndarray:
    f = np.maximum(freqs, 1.0)
    db = float(p.get("tilt", -3.0)) * np.log2(f / 1000.0)
    for fc, g, bw in p.get("bands") or []:
        db += g * np.exp(-0.5 * (np.log2(f / fc) / bw) ** 2)
    hp, lp = float(p.get("hp", 20)), float(p.get("lp", 16000))
    db += -10 * np.log10(1 + (hp / f) ** 4)       # 2nd order, 12 dB/oct
    db += -10 * np.log10(1 + (f / lp) ** 4)
    return db


def _phases(rng, n):
    return np.exp(1j * rng.uniform(0, 2 * np.pi, n))


def _lfo(rng, n: int, ks, depth_db: float) -> np.ndarray:
    """Slow level movement: whole cycles over the loop, peak ±depth dB."""
    if depth_db <= 0:
        return np.ones(n, dtype=np.float64)
    t = np.arange(n, dtype=np.float64) / n
    m = np.zeros(n)
    for k in ks:
        m += np.sin(2 * np.pi * k * t + rng.uniform(0, 2 * np.pi)) / math.sqrt(k)
    m /= max(1e-9, np.abs(m).max())
    return 10 ** (depth_db * m / 20.0)


def _texture(extra: dict, rng, n: int, freqs: np.ndarray) -> np.ndarray | None:
    """Crickets or rain ticks, periodic over the loop. Unit RMS."""
    kind = (extra or {}).get("kind")
    t = np.arange(n, dtype=np.float64) / n
    if kind == "crickets":
        fc = float(extra.get("fc", 4700))
        band = np.exp(-0.5 * (np.log2(np.maximum(freqs, 1) / fc) / 0.06) ** 2)
        carrier = np.fft.irfft(band * _phases(rng, freqs.size), n=n)
        pulse = (0.5 + 0.5 * np.sin(2 * np.pi * 16 * LOOP_S * t)) ** 6
        gate = np.clip(np.sin(2 * np.pi * 29 * t + rng.uniform(0, 6.28))
                       + 0.4 * np.sin(2 * np.pi * 83 * t + rng.uniform(0, 6.28)), 0, None)
        y = carrier * pulse * np.sqrt(gate)
    elif kind == "ticks":
        count = int(float(extra.get("per_s", 80)) * LOOP_S)
        imp = np.zeros(n)
        pos = rng.integers(0, n, count)
        np.add.at(imp, pos, rng.uniform(0.2, 1.0, count) * rng.choice([-1, 1], count))
        shape = np.exp(-0.5 * (np.log2(np.maximum(freqs, 1) / 5000.0) / 1.2) ** 2)
        y = np.fft.irfft(np.fft.rfft(imp) * shape, n=n)   # periodic convolution
    else:
        return None
    r = np.sqrt(np.mean(y * y))
    return y / r if r > 0 else None


def _add_hum(ch: np.ndarray, hum, rng, phases: dict) -> None:
    n = ch.size
    rms = math.sqrt(float(np.mean(ch * ch))) or 1e-6
    t = np.arange(n, dtype=np.float64) / SR
    for f, rel in hum or []:
        k = round(f * LOOP_S)                    # an exact bin → whole cycles
        if k <= 0 or k >= n // 2:
            continue
        ph = phases.setdefault(f, rng.uniform(0, 2 * np.pi))
        ch += (rms * 10 ** (rel / 20.0) * math.sqrt(2)
               * np.sin(2 * np.pi * (k / LOOP_S) * t + ph))


def _synth(db: np.ndarray, rng, p: dict) -> np.ndarray:
    """Two channels of periodic noise with the magnitude `db` (per rfft bin of
    a LOOP_S grid), partly correlated, then hum, texture and movement."""
    n = LOOP_S * SR
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    mag = 10 ** (db / 20.0)
    mag[0] = 0.0
    shared = np.fft.irfft(mag * _phases(rng, freqs.size), n=n)
    out = np.empty((2, n))
    for c in range(2):
        own = np.fft.irfft(mag * _phases(rng, freqs.size), n=n)
        out[c] = math.sqrt(STEREO_CORR) * shared + math.sqrt(1 - STEREO_CORR) * own
    tex = _texture(p.get("extra") or {}, rng, n, freqs)
    hum_ph: dict = {}
    for c in range(2):
        rms = math.sqrt(float(np.mean(out[c] ** 2))) or 1e-6
        out[c] /= rms
        if tex is not None:
            out[c] += 10 ** (float(p["extra"].get("db", -6)) / 20.0) * np.roll(tex, c * 997)
        _add_hum(out[c], p.get("hum"), rng, hum_ph)
    lfo = _lfo(rng, n, p.get("lfo") or (1, 2, 3, 5, 8, 13), float(p.get("move", 1.0)))
    return out * lfo


# ---------------------------------------------------------------------------
# FROM THIS FILM
# ---------------------------------------------------------------------------
def _ffmpeg() -> str:
    for c in (os.environ.get("PHOSPHENE_FFMPEG"), shutil.which("ffmpeg"),
              "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if c and Path(c).exists():
            return c
    return "ffmpeg"


def decode_window(path, start: float, end: float, sr: int = SR) -> np.ndarray:
    """Mono float32 of `path` between two SOURCE seconds; empty when the file
    has no sound."""
    dur = max(0.0, float(end) - float(start))
    if dur <= 0:
        return np.zeros(0, dtype=np.float32)
    cmd = [_ffmpeg(), "-v", "error", "-nostdin", "-ss", f"{max(0.0, float(start)):.6f}",
           "-i", str(path), "-t", f"{dur:.6f}", "-map", "0:a:0?", "-vn", "-ac", "1",
           "-ar", str(sr), "-f", "s16le", "-acodec", "pcm_s16le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return np.zeros(0, dtype=np.float32)
    if r.returncode != 0:
        return np.zeros(0, dtype=np.float32)
    pcm = np.frombuffer(r.stdout[: len(r.stdout) // 2 * 2], dtype="<i2")
    return pcm.astype(np.float32) / 32768.0


def quiet_spectrum(x: np.ndarray) -> tuple[np.ndarray | None, float]:
    """The average power spectrum of one clip's quiet windows → (psd, seconds).

    Quiet = at or under the clip's own 25th percentile, under −30 dBFS, over
    −80 dBFS (digital silence carries no room), and at least two windows away
    from anything 10 dB louder than that percentile (a line's breath and
    reverb tail are not room tone)."""
    x = np.asarray(x, dtype=np.float64)
    if x.size < WIN * 4:
        return None, 0.0
    idx = np.arange(0, x.size - WIN + 1, HOP)
    frames = np.stack([x[i:i + WIN] for i in idx])
    db = 10 * np.log10(np.mean(frames ** 2, axis=1) + 1e-12)
    p25 = float(np.percentile(db, 25))
    keep = (db <= min(p25, -30.0)) & (db > -80.0)
    loud = db > p25 + 10.0
    near = loud.copy()
    for s in (1, 2):
        near[s:] |= loud[:-s]
        near[:-s] |= loud[s:]
    keep &= ~near
    if keep.sum() < 4:
        return None, 0.0
    w = np.hanning(WIN)
    spec = np.abs(np.fft.rfft(frames[keep] * w, axis=1)) ** 2
    return spec.mean(axis=0), float(keep.sum() * HOP / SR)


def film_shape(clips, decoder=None) -> dict:
    """The film's ambience: `{db (per WIN bin, 0 dB = unit power), quiet_s,
    clips_used}` or `{db: None, ...}` when nothing usable was found.

    `clips` is `[{path, start, end}]` in SOURCE seconds. Every clip's spectrum
    is normalised to unit power first (shape, not level), and the per-bin
    MEDIAN across clips keeps one noisy shot from owning the film."""
    dec = decoder or decode_window
    shapes, quiet, used = [], 0.0, 0
    seen = set()
    for c in clips or []:
        path = str((c or {}).get("path") or "")
        if not path:
            continue
        key = (path, round(float(c.get("start") or 0), 3), round(float(c.get("end") or 0), 3))
        if key in seen:
            continue
        seen.add(key)
        try:
            x = dec(path, float(c.get("start") or 0.0), float(c.get("end") or 0.0))
        except Exception:                                          # noqa: BLE001
            continue
        psd, secs = quiet_spectrum(x)
        if psd is None:
            continue
        tot = psd[1:].sum()
        if tot <= 0:
            continue
        shapes.append(10 * np.log10(psd / tot + 1e-20))
        quiet += secs
        used += 1
    if not shapes:
        return {"db": None, "quiet_s": 0.0, "clips_used": 0}
    return {"db": np.median(np.stack(shapes), axis=0), "quiet_s": round(quiet, 2),
            "clips_used": used}


# ---------------------------------------------------------------------------
# THE BED
# ---------------------------------------------------------------------------
def _seed_int(variant: str, seed) -> int:
    h = hashlib.sha1(f"{variant}:{seed}".encode()).hexdigest()
    return int(h[:12], 16)


def build_loop(variant: str = DEFAULT_VARIANT, seed: int = 1, clips=None,
               decoder=None) -> dict:
    """One LOOP_S-second periodic loop at REF_LUFS → a dict with `loop`
    ((2, n) float32), `ref_lufs`, `peak`, `variant` (the one actually used),
    `fallback` (why, or ""), `quiet_s`, `clips_used`."""
    rng = np.random.default_rng(_seed_int(variant, seed))
    n = LOOP_S * SR
    freqs = np.fft.rfftfreq(n, 1.0 / SR)
    used, fallback, quiet, nclips = variant, "", 0.0, 0
    if variant == "film":
        shape = film_shape(clips, decoder)
        quiet, nclips = shape["quiet_s"], shape["clips_used"]
        if shape["db"] is None or quiet < MIN_QUIET_S:
            used = FALLBACK_VARIANT
            fallback = (f"only {quiet:.1f} s of quiet sound in the clips"
                        if nclips else "no clip with usable quiet sound")
        else:
            wf = np.fft.rfftfreq(WIN, 1.0 / SR)
            db = np.interp(freqs, wf, shape["db"])
            # Nothing below 20 Hz: a bed that moves the woofer is not room tone.
            db += -10 * np.log10(1 + (20.0 / np.maximum(freqs, 1.0)) ** 4)
            loop = _synth(db, rng, {"move": 1.0})
    if used != "film":
        p = PRESETS.get(used) or PRESETS[FALLBACK_VARIANT]
        used = p["id"]
        loop = _synth(_db_shape(p, freqs), rng, p)
    lufs = loudness_lufs(loop)
    g = 10 ** ((REF_LUFS - lufs) / 20.0)
    peak = float(np.abs(loop).max()) * g
    if peak > PEAK_CAP:
        g *= PEAK_CAP / peak
        peak = PEAK_CAP
    loop = (loop * g).astype(np.float32)
    ref = round(lufs + 20 * math.log10(g), 2)
    return {"loop": loop, "ref_lufs": ref, "peak": round(peak, 4), "variant": used,
            "fallback": fallback, "quiet_s": quiet, "clips_used": nclips}


def tile(loop: np.ndarray, seconds: float) -> np.ndarray:
    total = int(round(float(seconds) * SR))
    reps = -(-total // loop.shape[1])
    return np.tile(loop, (1, reps))[:, :total]


def seam_jump(x: np.ndarray, at: int) -> tuple[float, float]:
    """(|step| across sample `at`, 99.9th percentile of |step| elsewhere) —
    a seam is audible when the first is far above the second."""
    x = np.atleast_2d(x)
    d = np.abs(np.diff(x, axis=1))
    return float(d[:, at - 1].max()), float(np.percentile(d, 99.9))


def write_wav(path, samples: np.ndarray, sr: int = SR) -> None:
    x = np.clip(np.atleast_2d(samples), -1.0, 1.0)
    pcm = (x.T * 32767.0).round().astype("<i2")
    tmp = Path(str(path) + ".part")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(x.shape[0])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    os.replace(tmp, path)


def clips_digest(clips) -> str:
    rows = []
    for c in clips or []:
        p = str((c or {}).get("path") or "")
        try:
            m = int(Path(p).stat().st_mtime)
        except OSError:
            m = 0
        rows.append([p, round(float(c.get("start") or 0), 3),
                     round(float(c.get("end") or 0), 3), m])
    return hashlib.sha1(json.dumps(rows).encode()).hexdigest()[:8]


def make_bed(dest_dir, *, variant: str = DEFAULT_VARIANT, seed: int = 1,
             film_len: float = 0.0, clips=None, decoder=None,
             strict: bool = False) -> dict:
    """Write (or reuse) the bed file for a film → the facts the Editor needs:
    `{path, duration, ref_lufs, variant, label, seed, fallback, quiet_s,
    clips_used, reused}`. The name is a function of every input, so the same
    pick reuses the same file and a different pick never overwrites one.

    `strict` (the automatic cut's mode): a "From this film" bed that would
    fall back to a preset is NOT written — `path` is "" and `fallback` says
    why. A film of silent clips gets no hiss nobody asked for."""
    if variant not in variant_ids():
        raise ValueError(f"unknown room tone: {variant}")
    seed = int(seed) if str(seed).lstrip("-").isdigit() else 1
    secs = bed_seconds(film_len)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    tag = clips_digest(clips) if variant == "film" else "p"
    stem = f"rt_{variant}_{seed}_{tag}_{secs}s"
    wav, meta = dest / f"{stem}.wav", dest / f"{stem}.json"
    if wav.is_file() and meta.is_file():
        try:
            facts = json.loads(meta.read_text(encoding="utf-8"))
            # A cache hit is still subject to `strict`. The same film can be
            # bedded by hand first ("From this film" falling back to a preset,
            # which the manual path allows) and re-cut automatically later —
            # and the automatic path refuses preset hiss over silent clips.
            # Returning the cached fallback here handed the automatic cut the
            # very file its own policy had just refused to make.
            if strict and facts.get("fallback"):
                return {"path": "", "duration": 0.0, "variant": variant,
                        "used": facts.get("used", variant),
                        "fallback": facts.get("fallback"),
                        "quiet_s": facts.get("quiet_s", 0.0),
                        "clips_used": facts.get("clips_used", 0),
                        "seed": seed, "reused": False}
            facts.update(path=str(wav), reused=True)
            return facts
        except (OSError, ValueError):
            pass
    res = build_loop(variant, seed, clips=clips, decoder=decoder)
    if strict and res["fallback"]:
        return {"path": "", "duration": 0.0, "variant": variant, "used": res["variant"],
                "fallback": res["fallback"], "quiet_s": res["quiet_s"],
                "clips_used": res["clips_used"], "seed": seed, "reused": False}
    write_wav(wav, tile(res["loop"], secs))
    facts = {"path": str(wav), "duration": float(secs), "ref_lufs": res["ref_lufs"],
             "peak": res["peak"], "variant": variant, "used": res["variant"],
             "label": variant_label(res["variant"]), "seed": seed,
             "fallback": res["fallback"], "quiet_s": res["quiet_s"],
             "clips_used": res["clips_used"]}
    meta.write_text(json.dumps(facts, indent=1), encoding="utf-8")
    facts["reused"] = False
    return facts


def _main(argv) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("out")
    ap.add_argument("--variant", default=DEFAULT_VARIANT, choices=variant_ids())
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--clip", action="append", default=[],
                    help="path[:start:end] (repeatable) for --variant film")
    a = ap.parse_args(argv)
    clips = []
    for spec in a.clip:
        parts = spec.rsplit(":", 2)
        if len(parts) == 3:
            clips.append({"path": parts[0], "start": float(parts[1]), "end": float(parts[2])})
        else:
            clips.append({"path": spec, "start": 0.0, "end": 1e6})
    res = build_loop(a.variant, a.seed, clips=clips)
    write_wav(a.out, tile(res["loop"], a.seconds))
    print(json.dumps({k: v for k, v in res.items() if k != "loop"}))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
