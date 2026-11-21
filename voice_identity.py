"""Who is speaking: a saved voice's identity, described and checked.

Re-voice renders each line as a short LTX clip of a person speaking and keeps
only the sound. LTX makes the person up from the prompt, and the voice comes
out as that person's: the voice adapter colours it but does not override it.
The take prompt used to name the voice by its id ("Close-up of
voice_585e7a6839c24a1d"), which names nobody, so each seed drew a new person;
on the owner's v9 podcast test two of four takes of one line drew a woman and
her voice came out (median pitch 222 and 262 Hz against the voice's 121-180),
and best-of-four, which listened only for rhythm, picked one.

Two fixes live here:
- `describe` turns the voice's own sample into a speaker description ("a man
  with a low, slightly husky voice") that every take prompt names, identically;
  Voices lets the creator correct it.
- `check` is the gate: every take is compared with the voice's own sample by
  median pitch and, when the Speech pack's speaker encoder is installed, by a
  speaker fingerprint. A take that is someone else is rejected before rhythm
  scoring.

Pure numpy and stdlib except `fingerprints`, which runs
scripts/voice_identity_embed.py in the Speech venv.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

PROFILE_VERSION = 2  # 2: pitch peak climbed + interpolated (1 read up to 7 % high)
# Median pitch further than this from the voice's own, in semitones, is
# another person. Measured on the 24 takes of the owner's two v9 runs against
# the rogan sample: his own takes sit at -1.6..+4.8 st, the takes that drew
# a woman at +7.1..+12.7 st. The line sits between the two.
PITCH_TOLERANCE_ST = 6.0
# Speaker-fingerprint cosine (Qwen3-TTS Base speaker encoder) below this is
# another person. That encoder hears timbre more than sex: the same takes
# scored 0.968-0.981 (his) and 0.952-0.964 (the women), and the sample shifted
# up an octave still scores 0.99, so pitch decides between a man and a woman
# and the fingerprint only refuses a clearly different voice (the sample
# shifted down 7 st scores 0.88).
SIMILARITY_FLOOR = 0.93
MAN_BELOW_HZ, WOMAN_ABOVE_HZ = 160.0, 185.0


def _mono(pcm):
    import numpy as np
    pcm = np.asarray(pcm, dtype=np.float32)
    return pcm.mean(axis=1) if pcm.ndim == 2 else pcm


def pitch_track(pcm, rate=44100):
    """Per-frame F0 (Hz) and clarity (normalised autocorrelation peak, 0..1)
    on the voiced frames: 40 ms frames, 10 ms hop, 60-400 Hz."""
    import numpy as np
    mono = _mono(pcm)
    step = max(1, rate // 11025)
    if step > 1:
        mono = mono[:len(mono) // step * step].reshape(-1, step).mean(axis=1)
    sr = rate / step
    frame, hop = int(.04 * sr), int(.01 * sr)
    lo, hi = int(sr / 400), int(sr / 60)
    if len(mono) < frame:
        return np.zeros(0), np.zeros(0)
    idx = np.arange(0, len(mono) - frame, hop)[:, None] + np.arange(frame)
    frames = mono[idx]
    frames = frames - frames.mean(axis=1, keepdims=True)
    energy = np.sqrt((frames ** 2).mean(axis=1))
    if not len(energy) or energy.max() < 1e-6:
        return np.zeros(0), np.zeros(0)
    frames = frames[energy >= energy.max() * 10 ** (-30 / 20)]
    size = 1 << int(np.ceil(np.log2(2 * frame)))
    acf = np.fft.irfft(np.abs(np.fft.rfft(frames * np.hanning(frame), size)) ** 2, size)[:, :hi + 1]
    window = np.fft.irfft(np.abs(np.fft.rfft(np.hanning(frame), size)) ** 2, size)[:hi + 1]
    # Dividing by the window's own autocorrelation undoes its taper (Boersma 1993).
    r = (acf / np.maximum(acf[:, :1], 1e-12)) / (window / window[0])
    r = r[:, lo:hi + 1]
    peak = r.max(axis=1)
    # The first peak close to the best one: a sub-octave (period doubling) peak
    # is nearly as high and would halve the pitch. Climb from the first lag over
    # 0.9 x best to that peak's top (stopping at the first lag would read a broad
    # peak up to 7 % high), then place the top between lags (parabola).
    rows = np.arange(len(r))
    k = np.argmax(r >= .9 * peak[:, None], axis=1)
    for _ in range(r.shape[1]):
        up = (k + 1 < r.shape[1]) & (r[rows, np.minimum(k + 1, r.shape[1] - 1)] > r[rows, k])
        if not up.any():
            break
        k = k + up
    a = r[rows, np.maximum(k - 1, 0)]; b = r[rows, k]; c = r[rows, np.minimum(k + 1, r.shape[1] - 1)]
    bend = a - 2 * b + c
    shift = np.where((bend < 0) & (k > 0) & (k + 1 < r.shape[1]), .5 * (a - c) / np.where(bend < 0, bend, -1), 0)
    lag = lo + k + np.clip(shift, -.5, .5)
    keep = (peak >= .45) & (peak <= 1.05)
    return sr / lag[keep], np.minimum(peak[keep], 1.0)


def profile(pcm, rate=44100):
    """Pitch statistics of one person's speech. None when nothing voiced was heard."""
    import numpy as np
    f0, clarity = pitch_track(pcm, rate)
    if len(f0) < 20:
        return None
    return {'version': PROFILE_VERSION, 'f0_median': round(float(np.median(f0)), 1),
            'f0_low': round(float(np.percentile(f0, 10)), 1), 'f0_high': round(float(np.percentile(f0, 90)), 1),
            'clarity': round(float(np.median(clarity)), 3), 'voiced_seconds': round(len(f0) * .01, 2)}


def describe(prof, character_text=''):
    """A plain speaker description for the take prompt, from the sample's pitch.
    A linked character's own words (age, look) lead when there are any."""
    if not prof:
        return ''
    f0 = prof['f0_median']
    if f0 < MAN_BELOW_HZ:
        who = 'a man'
        pitch = 'deep' if f0 < 105 else 'low' if f0 < 135 else 'warm, mid-range'
    elif f0 > WOMAN_ABOVE_HZ:
        who = 'a woman'
        pitch = 'low' if f0 < 200 else 'warm, mid-range' if f0 < 240 else 'bright, high'
    else:
        who = 'an adult'
        pitch = 'mid-range'
    # Low autocorrelation clarity on clean speech = breath/rasp in the voice.
    texture = ', slightly husky' if prof.get('clarity', 1) < .78 else ''
    hint = ' '.join(str(character_text or '').split())[:120].rstrip('.')
    lead = hint if hint else who
    return f'{lead} with a {pitch}{texture} voice'


def semitones(a, b):
    return 12 * math.log2(a / b) if a > 0 and b > 0 else float('inf')


def cosine(a, b):
    import numpy as np
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / d) if d else 0.0


def check(take_profile, reference, take_vector=None):
    """Is this take the saved voice? Returns the per-take record kept in the job:
    {'passed', 'f0', 'pitch_off_st', 'similarity', 'method', 'reason'}."""
    record = {'passed': False, 'f0': None, 'pitch_off_st': None, 'similarity': None,
              'method': 'pitch+speaker' if take_vector is not None and reference.get('vector') else 'pitch',
              'reason': ''}
    if not take_profile:
        record['reason'] = 'no clear voice in this take'
        return record
    record['f0'] = take_profile['f0_median']
    off = semitones(take_profile['f0_median'], reference['f0_median'])
    record['pitch_off_st'] = round(off, 1)
    if take_vector is not None and reference.get('vector'):
        record['similarity'] = round(cosine(take_vector, reference['vector']), 3)
    if abs(off) > PITCH_TOLERANCE_ST:
        record['reason'] = 'its pitch is ' + ('higher' if off > 0 else 'lower') + ' than the voice'
    elif record['similarity'] is not None and record['similarity'] < SIMILARITY_FLOOR:
        record['reason'] = 'it sounds like a different person'
    else:
        record['passed'] = True
    return record


def fingerprints(P, job, paths, folder):
    """Speaker vectors for audio files, {path: list|None}; {} when the Speech
    pack (its speaker encoder) is not installed. Never downloads anything."""
    models = Path(getattr(P, 'SPEECH_MODELS', '') or '') / 'base'
    python = P._speech_python() if hasattr(P, '_speech_python') else None
    if not paths or python is None or not (models / 'config.json').is_file() \
            or not any(models.glob('*.safetensors')):
        return {}
    out = Path(folder) / 'fingerprints.json'
    result = P.run_tracked_subprocess([str(python), str(P.ROOT / 'scripts/voice_identity_embed.py'), str(models),
                                       str(out), *map(str, paths)], pgid_key='train_pgid',
                                      label='voice identity', job=job, timeout=300)
    if result.returncode or not out.is_file():
        P.push('[voice pass] Speaker check unavailable, checking pitch only: '
               + ((result.stderr or result.stdout or '')[-200:]).strip())
        return {}
    return json.loads(out.read_text())
