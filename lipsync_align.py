"""Lip-sync alignment: the mouth moves a few frames BEFORE the voice in lip-sync (a2v) clips.

Measured 4 Nov 2026 on H3 and LTX lip-sync renders (research note
a2v_lipsync_lead_2026-11-04): the picture leads the soundtrack it was conditioned on by
0-6 frames, typically 2, and the amount changes from clip to clip. The conditioning path
itself is exact (the audio VAE round trip is 0 samples, the drive slice starts on the
frame clock, the mux is 0 samples off), and the models' own text-to-video talking clips
lead by a similar, equally variable amount, so the lead is the models' habit, not a
rounding in our code. A fixed correction would leave the 0- and 6-frame clips wrong, so
every lip-sync clip is MEASURED after it renders and its picture is held back by the
measured lead:

  * measure  - motion inside the mouth box (frame differences) against the voice's
               speech-band envelope, cross-correlated over the whole clip; the lag of the
               peak is the lead (negative = the mouth moves first, the convention of the
               measuring tool the film edit was cut with);
  * correct  - the first frame is held for N frames and the last N frames are dropped:
               the soundtrack is untouched, the clip keeps its exact length and its place
               on the song's clock (Editor, Storyboard and Continue-the-song read the
               audio window, which does not move);
  * refuse   - no face, no speech, a weak or flat correlation, or a lead outside the
               plausible range: the clip is delivered exactly as rendered.

The pure parts (lag search, plan, filter) are unit-tested; the I/O parts need ffmpeg and
OpenCV and return None rather than raise, so a measurement failure never fails a render.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# Search range for the lag, in frames, each way. Wider than any lead seen (6) so a real
# 6-frame lead is a peak inside the range, not a value pinned to its edge.
SEARCH_FRAMES = 12
# The largest correction ever applied. A measured lead past this is treated as a bad
# measurement (a hand, a head turn, music in the voice band), not as a lead.
MAX_SHIFT_FRAMES = 8
# The measurement must be clearly better at the lag than at zero, and correlate at all.
# Validation, 4 Nov 2026: a sung take (SUNDAY MASS s25) and a plateau (s15) scored only
# 5 % / 3 % better at their "lead" than at zero; shifting s25 by its measured 5 frames
# left it 3 frames LATE against the owner-approved 2. Clips with a real, clear lead
# (kitchen line, s08, s09) score 12-68 % better. Sound arriving early is easier to
# notice than a mouth a little ahead, so an unclear clip is delivered as rendered.
MIN_PEAK = 0.25
MIN_GAIN = 0.02        # absolute: peak - score at zero
MIN_REL_GAIN = 0.09    # relative: (peak - score at zero) / peak
# Fewer speaking frames than this cannot be measured (one short word).
MIN_FRAMES = 36


def best_lag(motion, envelope, search: int = SEARCH_FRAMES):
    """PURE. `(lag, sub_frame_lag, peak, at_zero)` or None.

    `motion[i]` is how much the mouth moved INTO frame i, `envelope[i]` the voice's
    loudness during frame i. A NEGATIVE lag means the motion series matches the voice
    LATER in time - the mouth moves first - by that many frames. `sub_frame_lag` refines
    the integer peak with a parabola through its two neighbours.
    """
    import numpy as np

    e = np.asarray(envelope, dtype=float)
    m = np.asarray(motion, dtype=float)
    n = min(len(e), len(m))
    if n < MIN_FRAMES:
        return None
    e, m = e[:n], m[:n]
    if e.std() < 1e-9 or m.std() < 1e-9:
        return None
    e = (e - e.mean()) / e.std()
    m = (m - m.mean()) / m.std()
    search = max(1, min(int(search), n // 3))
    score = {}
    for lag in range(-search, search + 1):
        a = e[max(0, -lag):n - max(0, lag)]
        b = m[max(0, lag):n - max(0, -lag)]
        score[lag] = float(np.mean(a * b))
    lag = max(score, key=score.get)
    sub = float(lag)
    if -search < lag < search:
        lo, mid, hi = score[lag - 1], score[lag], score[lag + 1]
        den = lo - 2.0 * mid + hi
        if den < 0:
            sub = lag + 0.5 * (lo - hi) / den
    return lag, round(sub, 2), round(score[lag], 3), round(score[0], 3)


def plan_shift(measure, *, max_shift: int = MAX_SHIFT_FRAMES, min_peak: float = MIN_PEAK,
               min_gain: float = MIN_GAIN) -> tuple[int, str]:
    """PURE. `(frames to hold the picture back, why)`; 0 = deliver as rendered.

    Only a mouth-FIRST lead is corrected (the direction the models drift); a clip whose
    mouth trails the voice is left alone - holding the voice back would move the clip
    off the song's clock, and no render measured that way.
    """
    if measure is None:
        return 0, "not measured (no face or no speech found)"
    lag, sub, peak, at0 = measure
    if peak < min_peak:
        return 0, f"too weak to trust (peak {peak:+.2f})"
    if lag >= 0:
        return 0, "already in sync" if lag == 0 else f"mouth after the voice ({lag} f), left alone"
    if peak - at0 < min_gain or (peak - at0) < MIN_REL_GAIN * peak:
        return 0, f"no clear lead (peak {peak:+.2f} vs {at0:+.2f} at 0)"
    lead = int(round(-float(sub)))
    if lead > max_shift:
        return 0, f"implausible lead ({lead} f), left alone"
    if lead <= 0:
        return 0, "already in sync"
    return lead, f"mouth led the voice by {lead} frame{'s' if lead != 1 else ''}"


def delay_filter(shift: int, frames: int) -> str:
    """PURE. ffmpeg video filter: hold frame 0 for `shift` frames, keep `frames` in all."""
    shift, frames = int(shift), int(frames)
    if shift <= 0:
        return f"trim=end_frame={frames},setpts=PTS-STARTPTS"
    return (f"tpad=start={shift}:start_mode=clone,trim=end_frame={frames},"
            "setpts=PTS-STARTPTS")


# ----------------------------------------------------------------- I/O (ffmpeg + OpenCV)
# The panel sets this to its own resolved ffprobe (mlx_ltx_panel.FFPROBE): some installs
# keep ffprobe in another folder than ffmpeg, and Pinokio's PATH may not have it.
FFPROBE: str | None = None


def _probe(ffmpeg: str, video: Path) -> tuple[int, int, float, int] | None:
    ffprobe = FFPROBE or str(Path(ffmpeg).with_name("ffprobe"))
    if not Path(ffprobe).exists():
        ffprobe = "ffprobe"
    out = subprocess.run(
        [ffprobe, "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,r_frame_rate,nb_read_packets",
         "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, errors="replace", timeout=60).stdout.strip().split(",")
    try:
        w, h = int(out[0]), int(out[1])
        num, den = out[2].split("/")
        return w, h, float(num) / float(den or 1), int(out[3])
    except (IndexError, ValueError, ZeroDivisionError):
        return None


def mouth_box(ffmpeg: str, video: Path, size: tuple[int, int], fps: float, frames: int):
    """(x0, y0, x1, y1) of the mouth area - the lower part of the biggest frontal face,
    the median over five frames - or None when no face is found."""
    try:
        import cv2
        import numpy as np
    except Exception:                                              # noqa: BLE001
        return None
    w, h = size
    casc = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    boxes = []
    for frac in (0.15, 0.32, 0.5, 0.68, 0.85):
        t = frac * frames / fps
        raw = subprocess.run([ffmpeg, "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                              "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "gray", "-"],
                             capture_output=True, timeout=60).stdout
        if len(raw) < w * h:
            continue
        g = np.frombuffer(raw[:w * h], np.uint8).reshape(h, w)
        faces = casc.detectMultiScale(g, 1.1, 5, minSize=(max(40, w // 20), max(40, w // 20)))
        if len(faces):
            fx, fy, fw, fh = max(faces, key=lambda b: b[2] * b[3])
            boxes.append((fx + 0.22 * fw, fy + 0.62 * fh, fx + 0.78 * fw, fy + 1.05 * fh))
    if len(boxes) < 2:
        return None
    x0, y0, x1, y1 = (int(v) for v in np.median(np.array(boxes), 0))
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None
    return x0 - x0 % 2, y0 - y0 % 2, x1 - x1 % 2, y1 - y1 % 2


def mouth_motion(ffmpeg: str, video: Path, box, fps: float):
    import numpy as np
    x0, y0, x1, y1 = box
    raw = subprocess.run([ffmpeg, "-v", "error", "-i", str(video), "-vf",
                          f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0},scale=48:24,format=gray",
                          "-f", "rawvideo", "-"], capture_output=True, timeout=300).stdout
    f = np.frombuffer(raw[:len(raw) // 1152 * 1152], np.uint8).reshape(-1, 24, 48).astype(float)
    if len(f) < 2:
        return None
    return np.r_[0.0, np.abs(np.diff(f, axis=0)).mean((1, 2))]


def voice_envelope(ffmpeg: str, audio: Path, fps: float, *, start: float = 0.0,
                   duration: float | None = None):
    """Speech-band (250-3500 Hz) loudness per picture frame."""
    import numpy as np
    rate = 24000
    cmd = [ffmpeg, "-v", "error"]
    if start > 0:
        cmd += ["-ss", f"{start:.4f}"]
    cmd += ["-i", str(audio)]
    if duration:
        cmd += ["-t", f"{duration:.4f}"]
    cmd += ["-vn", "-af", "highpass=f=250,lowpass=f=3500", "-ac", "1", "-ar", str(rate),
            "-f", "s16le", "-"]
    a = np.frombuffer(subprocess.run(cmd, capture_output=True, timeout=300).stdout,
                      np.int16).astype(float)
    hop = rate / float(fps)
    n = int(len(a) // hop)
    if n < 2:
        return None
    return np.array([np.sqrt(np.mean(a[int(i * hop):int((i + 1) * hop)] ** 2) + 1e-9)
                     for i in range(n)])


def measure(ffmpeg: str, video, audio=None, *, audio_start: float = 0.0):
    """Measure one clip: `{lag, sub, peak, at0, box, fps, frames}` or None.

    `audio` is the soundtrack the picture was conditioned on (a vocal stem when one was
    used - the cleanest voice there is); None = the clip's own sound."""
    try:
        video = Path(video)
        info = _probe(ffmpeg, video)
        if info is None:
            return None
        w, h, fps, frames = info
        box = mouth_box(ffmpeg, video, (w, h), fps, frames)
        if box is None:
            return None
        motion = mouth_motion(ffmpeg, video, box, fps)
        env = voice_envelope(ffmpeg, Path(audio) if audio else video, fps,
                             start=float(audio_start) if audio else 0.0,
                             duration=frames / fps)
        if motion is None or env is None:
            return None
        got = best_lag(motion, env)
        if got is None:
            return None
        lag, sub, peak, at0 = got
        return {"lag": lag, "sub": sub, "peak": peak, "at0": at0, "box": list(box),
                "fps": round(fps, 3), "frames": frames}
    except Exception:                                              # noqa: BLE001
        return None


def hold_picture(ffmpeg: str, video, shift: int, frames: int, *, crf: str = "18",
                 pix_fmt: str = "yuv420p") -> bool:
    """Re-encode `video` in place with its picture held back by `shift` frames; the sound
    is copied untouched. False (file unchanged) on any failure."""
    video = Path(video)
    # Not *.mp4: the gallery lists every .mp4 in the output folder.
    tmp = video.with_name(video.name + ".lipsync-tmp")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", str(video), "-map", "0:v:0", "-map", "0:a?",
           "-vf", delay_filter(shift, frames), "-c:v", "libx264", "-preset", "medium",
           "-crf", str(crf), "-pix_fmt", str(pix_fmt), "-c:a", "copy",
           "-movflags", "+faststart", "-f", "mp4", str(tmp)]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=1800)
        got = _probe(ffmpeg, tmp)
        if got is None or got[3] != int(frames):
            tmp.unlink(missing_ok=True)
            return False
        tmp.replace(video)
        return True
    except Exception:                                              # noqa: BLE001
        tmp.unlink(missing_ok=True)
        return False
