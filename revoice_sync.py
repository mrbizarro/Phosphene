"""Re-voice timing: every new word starts where the original word started.

The owner's v9 test (20 Nov 2026) heard four faults; each has its home here:
  * the guest's "Bro" lost its start: the old pass subtracted the original voice
    over the whole placed take. `window` bounds every replacement by the
    neighbouring lines' first and last sounds, and nothing outside it changes;
  * mouths moved with no voice ("Mr. Bizarro" drawn out over 1.7 s, said in
    0.9 s): `plan` maps each word of the take onto the original word, with a
    bounded, pitch-preserving stretch (`stretch`), keeping the original's pauses
    and dropping pauses the original does not have;
  * the voice changed line to line (gains 0.61, 0.77, 1.60): `voice_levels`
    brings every take of one voice to the original speaker's loudness and one
    shared tone (`shared_eq`), never a per-line gain;
  * delivery went flat (prompted with "mr bizarro"): `spoken_line` restores
    casing and punctuation and adds a pace and energy hint from the original.
Takes are chosen by `rhythm`, how well their words fall on the original's.
"""
import math
import re
import subprocess

import numpy as np

SR = 44100
FPS = 24.0
MIN_STRETCH, MAX_STRETCH = .75, 1.35
MAX_DRAWN = 1.6    # a word the original draws out may be drawn out further
PAUSE = .12        # silence at least this long between two words is a pause
GUARD = .06        # a window stops this far from a neighbouring line's sound
EDGE = .03         # crossfade inside each end of a window
PRE_ROLL = .03     # a word's attack starts just before its measured onset
TAIL = .08         # and its last sound rings out a little after its offset


# --------------------------------------------------------------- words


def speech_regions(pcm):
    from voice_pass import speech_regions as regions
    try:
        return regions(pcm)
    except ValueError:
        return []


def _distance(region, point):
    return max(region[0] - point, point - region[1], 0)


def envelope(pcm, sr=SR):
    """Level in dB per 10 ms of the (mono) voice."""
    mono = np.asarray(pcm, np.float32)
    mono = mono.mean(axis=1) if mono.ndim > 1 else mono
    hop = sr // 100
    frames = len(mono) // hop
    if not frames:
        return np.zeros(0)
    power = (mono[:frames * hop] ** 2).reshape(frames, hop).mean(axis=1)
    return 10 * np.log10(power + 1e-12)


def refine(words, pcm, sr=SR):
    """Word onsets/offsets from Whisper, corrected by the waveform. Whisper gives
    a pause to the following word ("what" 1.68-3.50, whose sound starts at 3.38),
    starts a word where the previous one ended ("Bro" at 4.32; its sound starts at
    4.70), and ends a drawn-out word early ("Bizarro" at 1.68; it rings to 1.95)."""
    regions = speech_regions(pcm) if pcm is not None else []
    env = envelope(pcm, sr) if pcm is not None else np.zeros(0)
    out = []
    for word in words:
        s, e = float(word['s']), float(word['e'])
        hit = [r for r in regions if r[1] > s and r[0] < e]
        if hit:
            # A sound that began before the previous word ended is that word's.
            own = [r for r in hit if not out or r[0] >= out[-1]['e'] - .02]
            if own:
                near = max(own, key=lambda r: min(r[1], e) - max(r[0], s))
                if near[0] > s + .04:
                    s = near[0]
            tails = [r[1] for r in hit if r[1] > s]
            tail = max(tails) if tails else e
            if tail < e - .04:
                e = tail
        if out and s < out[-1]['on']:
            s = out[-1]['on']
        out.append({'w': str(word['w']).strip(), 'on': s, 'off': max(e, s + .03),
                    's': float(word['s']), 'e': float(word['e'])})
    if len(env):
        # Two words Whisper runs together often have a real dip between them
        # ("Bizarro" starts at 0.78 s, not at Whisper's 0.54): split them there.
        for k in range(len(out) - 1):
            a, b = out[k], out[k + 1]
            if b['on'] - a['off'] >= PAUSE:
                continue
            edge = (a['off'] + b['on']) / 2
            lo = int(max(a['on'] + (a['off'] - a['on']) / 2, edge - .3) * 100)
            hi = int(min(b['on'] + (b['off'] - b['on']) / 2, edge + .3) * 100)
            if hi - lo < 3 or hi > len(env):
                continue
            peaks = (env[int(a['on'] * 100):max(int(a['on'] * 100) + 1, lo)].max() if lo > int(a['on'] * 100) else env[lo],
                     env[hi:max(hi + 1, int(b['off'] * 100))].max() if int(b['off'] * 100) > hi else env[hi - 1])
            quiet = env[lo:hi] < min(peaks) - 15
            if not quiet.any():
                continue
            # The quiet stretch nearest Whisper's edge: "Mis-ter" has a dip between its
            # syllables too, further from the edge than the real gap before "Bizarro".
            gaps, start = [], None
            for f, q in enumerate([*quiet, False]):
                if q and start is None:
                    start = f
                if not q and start is not None:
                    gaps.append((lo + start, lo + f)); start = None
            here = edge * 100
            first, end = min(gaps, key=lambda g: (max(g[0] - here, here - g[1], 0), -(g[1] - g[0])))
            a['off'] = max(a['on'] + .03, first / 100)
            b['on'] = max(a['off'], end / 100)
            b['off'] = max(b['off'], b['on'] + .03)
        # A word after a pause starts where its sound rises to within 20 dB of its
        # own peak: breath or a laugh before "Jamie" is not the word.
        for k, w in enumerate(out):
            if k and w['on'] - out[k - 1]['off'] < PAUSE:
                continue
            a, b = int(w['on'] * 100), int(w['off'] * 100)
            if b - a < 3 or b > len(env):
                continue
            seg = env[a:b]
            rise = int(np.argmax(seg >= seg.max() - 20))
            if rise:
                w['on'] = min(w['off'] - .03, (a + rise) / 100)
        for k, w in enumerate(out):
            limit = (out[k + 1]['on'] - .02) if k + 1 < len(out) else w['off'] + .8
            if limit - w['off'] < PAUSE:
                continue
            a, b = int(w['on'] * 100), int(w['off'] * 100)
            if b <= a or a >= len(env):
                continue
            floor = env[a:b].max() - 24
            t = b
            while t < min(len(env), int(limit * 100), b + 80) and env[t] > floor:
                t += 1
            w['off'] = max(w['off'], t / 100)
    return out


def words_by_phrase(phrases, heard):
    """The heard words that belong to each line, in order (Whisper's own text, so
    "Mr." "Bizarro," "to?" keep their casing and punctuation)."""
    from word_timing import match_all, norm
    tokens, owners = [], []
    for i, phrase in enumerate(phrases):
        for word in phrase['text'].split():
            if norm(word):
                tokens.append(norm(word)); owners.append(i)
    indices = match_all(tokens, [norm(w['w']) for w in heard])
    rows = [[] for _ in phrases]
    used = set()
    for owner, j in zip(owners, indices):
        if j is not None and j not in used:
            used.add(j); rows[owner].append(heard[j])
    return [sorted(row, key=lambda w: float(w['s'])) for row in rows]


def pair_words(text, original, take):
    """Original and take words for the same script words, in pairs."""
    from word_timing import match_all, norm
    tokens = [norm(w) for w in text.split() if norm(w)]
    a = match_all(tokens, [norm(w['w']) for w in original])
    b = match_all(tokens, [norm(w['w']) for w in take])
    pairs = [(original[i], take[j]) for i, j in zip(a, b) if i is not None and j is not None]
    # Matching never reorders, but two script words may claim one heard word.
    out = []
    for o, t in pairs:
        if out and (o is out[-1][0] or t is out[-1][1] or o['on'] < out[-1][0]['on'] or t['on'] < out[-1][1]['on']):
            continue
        out.append((o, t))
    return out


def features(pcm, sr=SR):
    """Speech shape per 10 ms (12 mel-cepstral coefficients, normalised per
    utterance), so two voices saying the same words can be lined up."""
    x = np.asarray(pcm, np.float32)
    x = x.mean(axis=1) if x.ndim > 1 else x
    x = np.append(x[0], x[1:] - .97 * x[:-1]) if len(x) else x
    win, hop, nfft = int(.025 * sr), sr // 100, 2048
    frames = max(1, 1 + (len(x) - win) // hop) if len(x) >= win else 1
    x = np.pad(x, (0, max(0, (frames - 1) * hop + win - len(x))))
    idx = np.arange(win)[None, :] + hop * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(x[idx] * np.hamming(win), nfft)) ** 2
    mel = lambda f: 2595 * np.log10(1 + f / 700)
    edges = 700 * (10 ** (np.linspace(mel(80), mel(7600), 28) / 2595) - 1)
    bins = np.floor((nfft + 1) * edges / sr).astype(int)
    bank = np.zeros((26, spec.shape[1]))
    for m in range(1, 27):
        a, c, b = bins[m - 1], bins[m], bins[m + 1]
        if c > a:
            bank[m - 1, a:c] = (np.arange(a, c) - a) / (c - a)
        if b > c:
            bank[m - 1, c:b] = (b - np.arange(c, b)) / (b - c)
    n = np.arange(26)
    dct = np.cos(np.pi / 26 * (n[None, :] + .5) * np.arange(1, 13)[:, None])
    # numpy on Apple's Accelerate can warn about overflow in a well-formed matmul.
    with np.errstate(all='ignore'):
        logmel = np.log(np.nan_to_num(spec @ bank.T) + 1e-10)
        ceps = np.nan_to_num(logmel @ dct.T)
    return (ceps - ceps.mean(axis=0)) / (ceps.std(axis=0) + 1e-6)


def dtw_path(a, b):
    """Lowest-cost monotonic match between frame sequences a and b."""
    n, m = len(a), len(b)
    cost = np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(axis=2))
    acc = np.full((n + 1, m + 1), np.inf); acc[0, 0] = 0
    move = np.zeros((n + 1, m + 1), np.int8)
    for i in range(1, n + 1):
        row = cost[i - 1]
        for j in range(1, m + 1):
            # A small price on sideways steps keeps the match near the diagonal.
            d, u, l = acc[i - 1, j - 1], acc[i - 1, j] + .2, acc[i, j - 1] + .2
            if d <= u and d <= l:
                acc[i, j], move[i, j] = row[j - 1] + d, 0
            elif u <= l:
                acc[i, j], move[i, j] = row[j - 1] + u, 1
            else:
                acc[i, j], move[i, j] = row[j - 1] + l, 2
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        k = move[i, j]
        i, j = (i - 1, j - 1) if k == 0 else (i - 1, j) if k == 1 else (i, j - 1)
    return path[::-1]


def matched_take_words(original, orig_pcm, take_words, take_pcm, sr=SR):
    """Take words placed by sound, not by label: the original line and the take
    are lined up frame by frame, and each original word's start and end are
    carried over. Whisper's labels cannot tell "Mis-ter" from "Bi-zarro"; the
    sound can. Returns take words parallel to `original`, or None."""
    if not original or not take_words:
        return None
    a0, a1 = max(0.0, original[0]['on'] - .05), original[-1]['off'] + .05
    b0, b1 = max(0.0, take_words[0]['on'] - .05), take_words[-1]['off'] + .05
    fa = features(orig_pcm[int(a0 * sr):int(a1 * sr)], sr)
    fb = features(take_pcm[int(b0 * sr):int(b1 * sr)], sr)
    if len(fa) < 5 or len(fb) < 5 or len(fa) * len(fb) > 4_000_000:
        return None
    path = dtw_path(fa, fb)
    first, last = {}, {}
    for i, j in path:
        first.setdefault(i, j); last[i] = j
    out = []
    for k, w in enumerate(original):
        i_on = min(len(fa) - 1, max(0, int(round((w['on'] - a0) * 100))))
        i_off = min(len(fa) - 1, max(0, int(round((w['off'] - a0) * 100))))
        # A pause only the take has sits between the end of one word and the next:
        # its start is where the match leaves the word, its end where it rejoins.
        on, off = b0 + last[i_on] / 100, b0 + first[i_off] / 100
        if out and on < out[-1]['off']:
            on = out[-1]['off']
        out.append({'w': w['w'], 'on': on, 'off': max(off, on + .03), 's': on, 'e': off})
    # Silence the match handed to a word is the take's own pause, not the word.
    env = envelope(take_pcm, sr)
    floor = (env.max() if len(env) else 0) - 35
    for k, w in enumerate(out):
        end = out[k + 1]['on'] if k + 1 < len(out) else w['off']
        a, b = int(w['on'] * 100), min(len(env), int(end * 100))
        loud = np.flatnonzero(env[a:b] > floor) if b > a else []
        if not len(loud):
            continue
        w['on'] = max(w['on'], (a + loud[0]) / 100)
        quiet = np.diff(loud) * .01 >= PAUSE
        stop = loud[np.argmax(quiet)] if quiet.any() else loud[-1]
        w['off'] = max(w['on'] + .03, min(max(w['off'], (a + loud[-1] + 1) / 100), (a + stop + 1) / 100))
    return out


# --------------------------------------------------------------- the prompt


HONORIFICS = {'mr': 'Mr.', 'mrs': 'Mrs.', 'ms': 'Ms.', 'dr': 'Dr.', 'st': 'St.'}
QUESTION_WORDS = {'what', 'who', 'whom', 'whose', 'where', 'when', 'why', 'how', 'which', 'do', 'does',
                  'did', 'is', 'are', 'was', 'were', 'can', 'could', 'will', 'would', 'should', 'have',
                  'has', 'had', 'shall', 'may', 'am', "isn't", "aren't", "don't", "didn't", "won't"}


def syllables(word):
    plain = re.sub(r'[^a-z]', '', word.lower())
    if not plain:
        return max(1, len(word.strip()) // 2)
    groups = re.findall(r'[aeiouy]+', plain)
    count = len(groups) - (1 if plain.endswith('e') and len(groups) > 1 and not plain.endswith('le') else 0)
    return max(1, count)


def drawn_out(word):
    """A word said slowly on purpose ("Bizarrooo"): long for its syllables."""
    length = word['off'] - word['on']
    return length > .45 and length / syllables(word['w']) > .36


def db(x):
    return 10 * math.log10(max(float(x), 1e-12))


def spoken_line(words, level=0.0):
    """The line as it was said, for the voice model: (text, delivery hint).
    `words` are refined original words; `level` is this line's loudness against
    the speaker's usual level, in dB."""
    raw = [w['w'] for w in words]
    punctuated = any(re.search(r'[.,!?;:]', w) for w in raw)
    cased = any(any(c.isupper() for c in w) for w in raw)
    text_words = list(raw)
    if not (punctuated and cased):
        # Whisper sometimes returns "mr bizarro": put back what a reader needs.
        text_words = []
        for k, w in enumerate(raw):
            key = re.sub(r'[^a-z]', '', w.lower())
            w = HONORIFICS.get(key, w)
            after_title = k > 0 and text_words[-1] in HONORIFICS.values()
            if k == 0 or after_title or w.lower() == 'i' or w.lower().startswith("i'"):
                w = w[:1].upper() + w[1:]
            # A real pause after a word is a comma ("Jamie, pull up that toad").
            if k + 1 < len(words) and words[k + 1]['on'] - words[k]['off'] > .25 and not re.search(r'[.,!?;:]$', w):
                w += ','
            text_words.append(w)
        if not re.search(r'[.!?]$', text_words[-1]):
            first = re.sub(r'[^a-z\']', '', text_words[0].lower())
            text_words[-1] += '?' if first in QUESTION_WORDS else '.'
    text = ' '.join(text_words)
    if level >= 4 and not text.endswith('?'):
        text = text.rstrip('.,;:') + '!'
    hints = []
    spoken = max(1e-3, words[-1]['off'] - words[0]['on'])
    rate = sum(syllables(w['w']) for w in words) / spoken
    if rate > 5.6:
        hints.append('quickly')
    elif rate < 3.0:
        hints.append('slowly')
    drawn = max(words, key=lambda w: (w['off'] - w['on']) / syllables(w['w']))
    if drawn_out(drawn):
        hints.append('drawing out the word ' + re.sub(r'[.,!?;:"]+', '', text_words[words.index(drawn)]))
    if level >= 4:
        hints.append('loud, almost shouting')
    elif level >= 2:
        hints.append('with energy')
    elif level <= -4:
        hints.append('quietly')
    return text, (', '.join(hints))


# --------------------------------------------------------------- the window


def window(index, spans, rows, clip_end):
    """The only stretch of audio this line may change. It reaches from a moment
    before the line's first sound to a moment after its last, and never closer
    than GUARD to a neighbouring line's sound (the waveform's edge, not
    Whisper's: Whisper starts "Bro" where "to?" ended, 0.38 s before its sound).
    `rows` are the refined words of every line."""
    own = rows[index]
    start = min([spans[index][0]] + [w['on'] for w in own[:1]])
    end = max([spans[index][1]] + [w['off'] for w in own[-1:]])
    lo, hi = max(0.0, start - .1), min(clip_end, end + .12)
    if index > 0:
        lo = max(lo, max([spans[index - 1][1]] + [w['off'] for w in rows[index - 1][-1:]]) + GUARD)
    if index + 1 < len(spans):
        hi = min(hi, min([spans[index + 1][0]] + [w['on'] for w in rows[index + 1][:1]]) - GUARD)
    # A window that would cut off one of the line's own words would leave the old
    # voice saying it: the line is left as it was instead.
    if hi - lo < .1 or hi <= own[-1]['on'] + .05 or lo >= own[0]['off'] - .05:
        raise ValueError('This line touches the next speaker, so it cannot be changed on its own.')
    return lo, hi


# --------------------------------------------------------------- the warp


def plan(original, take, lo, hi):
    """Where each piece of the take goes. `original`/`take` are paired refined
    words. Returns segments (take_a, take_b, out_a, out_b), the onset error per
    word in seconds, and how much of the original speech has a voice under it."""
    segments, errors = [], []
    cursor = lo
    n = len(original)
    for k in range(n):
        o, t = original[k], take[k]
        place = max(o['on'], cursor)
        errors.append(place - o['on'])
        last = k + 1 == n
        o_pause = last or original[k + 1]['on'] - o['off'] >= PAUSE
        t_pause = last or take[k + 1]['on'] - t['off'] >= PAUSE
        if k == 0:
            # The attack starts a moment before the measured onset.
            pre = min(PRE_ROLL, t['on'], max(0.0, place - lo))
            if pre > 0:
                segments.append((t['on'] - pre, t['on'], place - pre, place))
        if not o_pause and not t_pause:
            # Connected speech in both: this word runs into the next one.
            t_b = take[k + 1]['on']
            want = original[k + 1]['on'] - place
        else:
            # A pause in either: map the word's own sound; the original's pause
            # stays a pause (its room tone), the take's own pause is dropped.
            t_b = t['off']
            want = o['off'] - place
        if last:
            t_b = t['off']
        have = max(1e-3, t_b - t['on'])
        ceiling = MAX_DRAWN if drawn_out(o) else MAX_STRETCH
        ratio = min(ceiling, max(MIN_STRETCH, max(want, 1e-3) / have))
        room = (original[k + 1]['on'] if not last else hi) - place
        if have * ratio > room:
            ratio = max(MIN_STRETCH, room / have)
        out_b = place + have * ratio
        if last:
            out_b = min(out_b, hi)
        segments.append((t['on'], t_b, place, out_b))
        cursor = out_b
        if last:
            ring = min(TAIL, max(0.0, hi - out_b))
            if ring > 0:
                segments.append((t_b, t_b + ring, out_b, out_b + ring))
    speech = sum(w['off'] - w['on'] for w in original)
    covered = 0.0
    for w in original:
        for _, _, a, b in segments:
            covered += max(0.0, min(b, w['off']) - max(a, w['on']))
    return segments, errors, min(1.0, covered / max(speech, 1e-3))


def runs(segments):
    """Segments that join in both the take and the output play as one piece."""
    out = []
    for seg in segments:
        if out and abs(out[-1][-1][1] - seg[0]) < 1e-6 and abs(out[-1][-1][3] - seg[2]) < 1e-6:
            out[-1].append(seg)
        else:
            out.append([seg])
    return out


def stretch(x, t_in, t_out, sr=SR, win_s=.03):
    """WSOLA time-scale change following a piecewise-linear map (output seconds
    `t_out` -> take seconds `t_in`): speech keeps its pitch; each grain is the
    take's own waveform, chosen to continue the last one smoothly."""
    x = np.asarray(x, np.float32)
    if x.ndim == 1:
        x = x[:, None]
    win = max(64, int(round(win_s * sr)) // 2 * 2)
    hop = win // 2
    tol = win // 4
    n_out = max(1, int(round((t_out[-1] - t_out[0]) * sr)))
    pad = np.pad(x, ((win + tol, win + tol), (0, 0)))
    mono = pad.mean(axis=1)
    window_fn = np.hanning(win).astype(np.float32)
    out = np.zeros((n_out + win, x.shape[1]), np.float32)
    norm = np.zeros(n_out + win, np.float32)
    previous = None
    for start in range(0, n_out, hop):
        centre = t_out[0] + (start + hop) / sr
        src = np.interp(centre, t_out, t_in) * sr - hop
        nominal = int(round(src)) + win + tol
        if previous is None:
            chosen = nominal
        else:
            natural = previous + hop
            lo = max(0, nominal - tol); hi = min(len(mono) - win, nominal + tol)
            if hi <= lo:
                chosen = min(max(nominal, 0), len(mono) - win)
            else:
                ref = mono[natural:natural + win] if natural + win <= len(mono) else mono[-win:]
                span = mono[lo:hi + win]
                score = np.correlate(span, ref, mode='valid')
                # Normalised: a louder neighbour must not win just for being loud.
                energy = np.convolve(span * span, np.ones(win, np.float32), mode='valid')
                score = score / np.sqrt(np.maximum(energy, 1e-9))
                chosen = lo + int(np.argmax(score))
        chosen = min(max(chosen, 0), len(pad) - win)
        out[start:start + win] += pad[chosen:chosen + win] * window_fn[:, None]
        norm[start:start + win] += window_fn
        previous = chosen
    norm[norm < 1e-3] = 1.0
    return (out / norm[:, None])[:n_out]


def render(take_pcm, segments, length, sr=SR):
    """The warped take on a silent track of `length` samples."""
    out = np.zeros((length, take_pcm.shape[1]), np.float32)
    fade = int(.008 * sr)
    for run in runs(segments):
        t_in = [run[0][0]] + [s[1] for s in run]
        t_out = [run[0][2]] + [s[3] for s in run]
        if t_out[-1] - t_out[0] < 1 / sr:
            continue
        a = max(0, int(math.floor(t_in[0] * sr)) - 1)
        b = min(len(take_pcm), int(math.ceil(t_in[-1] * sr)) + 1)
        piece = stretch(take_pcm[a:b], [v - a / sr for v in t_in], t_out, sr)
        k = min(fade, len(piece) // 2)
        if k:
            piece[:k] *= np.linspace(0, 1, k, dtype=np.float32)[:, None]
            piece[-k:] *= np.linspace(1, 0, k, dtype=np.float32)[:, None]
        pos = int(round(t_out[0] * sr))
        end = min(length, pos + len(piece))
        if end > pos >= 0:
            out[pos:end] += piece[:end - pos]
    return out


def rhythm(original, take, lo, hi):
    """How well a take falls on the original (lower is better) and its plan."""
    segments, errors, coverage = plan(original, take, lo, hi)
    ratios = [(s[3] - s[2]) / max(1e-3, s[1] - s[0]) for s in segments if s[1] - s[0] > .02]
    stretchy = float(np.mean([abs(math.log(r)) for r in ratios])) if ratios else 1.0
    # Word length and pause pattern: what a stretch cannot fix.
    shape = 0.0
    for k in range(len(original)):
        o, t = original[k], take[k]
        shape += abs(math.log(max(.03, o['off'] - o['on']) / max(.03, t['off'] - t['on'])))
        if k + 1 < len(original):
            op = original[k + 1]['on'] - o['off'] >= PAUSE
            tp = take[k + 1]['on'] - t['off'] >= PAUSE
            shape += .3 * (op != tp)
    shape /= max(1, len(original))
    err = float(np.median([abs(e) for e in errors])) if errors else 1.0
    score = stretchy + shape + 3 * (1 - coverage) + err / (1 / FPS) * .5
    return score, {'segments': segments, 'errors': errors, 'coverage': coverage}


def gate(errors, coverage, words_matched, words_total, fps=FPS):
    """Plain reasons this line is weak, or [] when it is in sync."""
    frame = 1 / fps
    reasons = []
    if words_matched < words_total:
        reasons.append('the new voice did not say every word')
    if errors:
        if float(np.median([abs(e) for e in errors])) > frame:
            reasons.append('its words start late against the mouth')
        elif max(abs(e) for e in errors) > 3 * frame:
            reasons.append('one word starts late against the mouth')
    if coverage < .8:
        reasons.append('the mouth moves for a moment with no voice')
    return reasons


# --------------------------------------------------------------- level and tone


def _k_weight(freqs, sr):
    """Magnitude of the BS.1770 K-weighting (shelf + high-pass) at `freqs`."""
    def biquad(b, a):
        z = np.exp(-2j * np.pi * freqs / sr)
        return np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z))
    f0, gain, q = 1681.974450955533, 3.999843853973347, .7071752369554196
    k = math.tan(math.pi * f0 / sr); vh = 10 ** (gain / 20); vb = vh ** .4996667741545416
    a0 = 1 + k / q + k * k
    shelf = biquad([(vh + vb * k / q + k * k) / a0, 2 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0],
                   [1, 2 * (k * k - 1) / a0, (1 - k / q + k * k) / a0])
    f0, q = 38.13547087602444, .5003270373238773
    k = math.tan(math.pi * f0 / sr)
    hp = biquad([1, -2, 1], [1, 2 * (k * k - 1) / (1 + k / q + k * k), (1 - k / q + k * k) / (1 + k / q + k * k)])
    return shelf * hp


def loudness(pieces, sr=SR):
    """Loudness (LUFS-style: K-weighted, speech only) of one or more pieces together."""
    total, count = 0.0, 0
    for x in pieces:
        x = np.asarray(x, np.float32)
        if x.ndim == 1:
            x = x[:, None]
        if len(x) < 64:
            continue
        spec = np.fft.rfft(x, axis=0)
        weighted = np.fft.irfft(spec * _k_weight(np.fft.rfftfreq(len(x), 1 / sr), sr)[:, None], n=len(x), axis=0)
        hop = sr // 100
        frames = len(x) // hop
        if not frames:
            continue
        power = (weighted[:frames * hop] ** 2).reshape(frames, hop, -1).mean(axis=1).sum(axis=1)
        active = power > power.max() * 10 ** (-35 / 10)
        total += float(power[active].sum()); count += int(active.sum())
    if not count:
        return -70.0
    return -.691 + db(total / count)


BANDS = [125 * 2 ** (i / 3) for i in range(0, 20)]      # 125 Hz .. ~12 kHz, third octaves


def spectrum(pieces, sr=SR):
    power = np.zeros(len(BANDS)); count = 0
    for x in pieces:
        x = np.asarray(x, np.float32)
        mono = x.mean(axis=1) if x.ndim > 1 else x
        if len(mono) < 2048:
            continue
        frames = len(mono) // 1024 - 1
        win = np.hanning(2048)
        freqs = np.fft.rfftfreq(2048, 1 / sr)
        for f in range(frames):
            chunk = mono[f * 1024:f * 1024 + 2048]
            if len(chunk) < 2048 or np.sqrt(np.mean(chunk ** 2)) < 1e-4:
                continue
            spec = np.abs(np.fft.rfft(chunk * win)) ** 2
            for i, centre in enumerate(BANDS):
                sel = (freqs >= centre / 2 ** (1 / 6)) & (freqs < centre * 2 ** (1 / 6))
                power[i] += spec[sel].mean() if sel.any() else 0
            count += 1
    return power / max(count, 1), count


def shared_eq(original_pieces, take_pieces, sr=SR, strength=.5, limit=6.0):
    """One gentle tone curve that moves this voice's takes toward the original
    speaker's sound: band gains in dB, smoothed and capped, applied the same way
    to every take of the voice."""
    want, n1 = spectrum(original_pieces, sr)
    have, n2 = spectrum(take_pieces, sr)
    if n1 < 4 or n2 < 4:
        return [0.0] * len(BANDS)
    gains = [strength * max(-limit, min(limit, db(w + 1e-12) - db(h + 1e-12))) for w, h in zip(want, have)]
    # Smooth across neighbouring bands, then centre: tone only, level is set apart.
    smooth = [float(np.mean(gains[max(0, i - 1):i + 2])) for i in range(len(gains))]
    mid = float(np.mean(smooth))
    return [round(g - mid, 2) for g in smooth]


def apply_eq(x, gains, sr=SR):
    if not any(abs(g) > .05 for g in gains):
        return x
    n = len(x)
    size = 1 << int(math.ceil(math.log2(max(n, 2) + 4096)))
    freqs = np.fft.rfftfreq(size, 1 / sr)
    logf = np.log2(np.maximum(freqs, 1.0))
    curve = np.interp(logf, np.log2(BANDS), gains, left=gains[0], right=gains[-1])
    spec = np.fft.rfft(np.pad(x, ((0, size - n), (0, 0))), axis=0)
    return np.fft.irfft(spec * (10 ** (curve / 20))[:, None], n=size, axis=0)[:n].astype(np.float32)


# --------------------------------------------------------------- the soundtrack


def native_audio(path):
    """The clip's own sound at its own rate and channels: (pcm, rate, info)."""
    import json
    probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
        'stream=codec_name,sample_rate,channels,bit_rate,profile', '-of', 'json', str(path)],
        capture_output=True, check=True).stdout)['streams'][0]
    rate, channels = int(probe['sample_rate']), int(probe['channels'])
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-i', str(path), '-vn', '-f', 'f32le', '-'],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.float32).reshape(-1, channels).copy(), rate, probe


def to_rate(x, rate, channels):
    """`x` (SR, stereo) at `rate` with `channels`. Zero stays exactly zero only
    where the caller zeroes it again."""
    if rate == SR:
        y = np.asarray(x, np.float32)
    else:
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-f', 'f32le', '-ar', str(SR), '-ac', str(x.shape[1]),
            '-i', '-', '-af', f'aresample={rate}', '-f', 'f32le', '-'],
            input=np.ascontiguousarray(x, np.float32).tobytes(), capture_output=True, check=True).stdout
        y = np.frombuffer(raw, np.float32).reshape(-1, x.shape[1]).copy()
    if channels == 1:
        y = y.mean(axis=1, keepdims=True)
    elif channels > y.shape[1]:
        y = np.repeat(y[:, :1], channels, axis=1)
    return y


def _boxes(data, start, end):
    """(type, box start, payload start, box end) for the MP4 boxes in data[start:end]."""
    out, i = [], start
    while i + 8 <= end:
        size = int.from_bytes(data[i:i + 4], 'big'); kind = bytes(data[i + 4:i + 8]).decode('latin-1')
        head = 8
        if size == 1:
            size = int.from_bytes(data[i + 8:i + 16], 'big'); head = 16
        elif size == 0:
            size = end - i
        if size < head or i + size > end:
            raise ValueError('damaged MP4 box')
        out.append((kind, i, i + head, i + size)); i += size
    return out


def _child(data, box, kind):
    for found in _boxes(data, box[2], box[3]):
        if found[0] == kind:
            return found
    raise ValueError(f'no {kind} box')


def _sample_table(data):
    """An audio-only MP4's sample sizes, chunk layout and the boxes that hold them."""
    top = _boxes(data, 0, len(data))
    moov = next(b for b in top if b[0] == 'moov')
    mdat = next(b for b in top if b[0] == 'mdat')
    traks = [b for b in _boxes(data, moov[2], moov[3]) if b[0] == 'trak']
    if len(traks) != 1:
        raise ValueError('expected one track')
    stbl = _child(data, _child(data, _child(data, traks[0], 'mdia'), 'minf'), 'stbl')
    stsz = _child(data, stbl, 'stsz'); stsc = _child(data, stbl, 'stsc')
    try:
        stco, wide = _child(data, stbl, 'stco'), False
    except ValueError:
        stco, wide = _child(data, stbl, 'co64'), True
    p = stsz[2]
    fixed, count = int.from_bytes(data[p + 4:p + 8], 'big'), int.from_bytes(data[p + 8:p + 12], 'big')
    sizes = ([fixed] * count if fixed else
             [int.from_bytes(data[p + 12 + 4 * k:p + 16 + 4 * k], 'big') for k in range(count)])
    p = stsc[2]
    rules = [tuple(int.from_bytes(data[p + 8 + 12 * k + 4 * j:p + 12 + 12 * k + 4 * j], 'big') for j in range(3))
             for k in range(int.from_bytes(data[p + 4:p + 8], 'big'))]
    p = stco[2]; step = 8 if wide else 4
    offsets = [int.from_bytes(data[p + 8 + step * k:p + 8 + step * (k + 1)], 'big')
               for k in range(int.from_bytes(data[p + 4:p + 8], 'big'))]
    per_chunk = []
    for k in range(len(offsets)):
        rule = [r for r in rules if r[0] <= k + 1][-1]
        per_chunk.append(rule[1])
    return {'sizes': sizes, 'fixed': fixed, 'offsets': offsets, 'per_chunk': per_chunk, 'stsz': stsz,
            'stco': stco, 'wide': wide, 'mdat': mdat, 'moov': moov}


def _samples(data, table):
    out, k = [], 0
    for offset, n in zip(table['offsets'], table['per_chunk']):
        for _ in range(n):
            size = table['sizes'][k]; out.append(bytes(data[offset:offset + size])); offset += size; k += 1
    if k != len(table['sizes']):
        raise ValueError('chunk table does not cover every sample')
    return out


def _audio_only(src, dst):
    subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(src), '-vn', '-map', '0:a:0', '-c:a', 'copy',
                    '-map_metadata', '-1', '-f', 'mp4', str(dst)], check=True, capture_output=True)
    return bytearray(dst.read_bytes())


def splice_aac(source, encoded, windows, rate, folder):
    """An audio track that keeps the source's own AAC packets (and its exact
    timing: priming, edit list, the shortened last packet) everywhere except the
    changed windows, so untouched speech is not compressed a second time.
    Returns the spliced audio-only MP4, or None when the two encodes do not
    share a packet grid."""
    base = _audio_only(source, folder / 'source-sound.m4a')
    new_data = _audio_only(encoded, folder / 'new-sound-copy.m4a')
    table, new_table = _sample_table(base), _sample_table(new_data)
    old, new = _samples(base, table), _samples(new_data, new_table)
    if len(old) != len(new) or not old or table['mdat'][1] > table['moov'][1]:
        return None
    # Sample k carries output samples [(k-1)*1024, k*1024) after the 1024-sample
    # priming packet; one more packet each way covers the transform's overlap.
    frame = 1024 / rate
    pick = set()
    for lo, hi in windows:
        pick.update(range(max(0, int(math.floor(lo / frame))), min(len(new) - 1, int(math.ceil(hi / frame)) + 1) + 1))
    chosen = [new[k] if k in pick else old[k] for k in range(len(old))]
    mdat = table['mdat']
    payload = b''.join(chosen)
    head = mdat[2] - mdat[1]
    if head == 8 and len(payload) + 8 >= 2 ** 32:
        return None
    header = ((len(payload) + 8).to_bytes(4, 'big') + b'mdat' if head == 8 else
              (1).to_bytes(4, 'big') + b'mdat' + (len(payload) + 16).to_bytes(8, 'big'))
    moov = bytearray(base[table['moov'][1]:table['moov'][3]])
    shift = table['moov'][1]
    if table['fixed'] or len(table['sizes']) != len(chosen):
        return None
    p = table['stsz'][2] - shift
    for k, sample in enumerate(chosen):
        moov[p + 12 + 4 * k:p + 16 + 4 * k] = len(sample).to_bytes(4, 'big')
    p = table['stco'][2] - shift; step = 8 if table['wide'] else 4
    offset = mdat[1] + len(header); k = 0
    for c, n in enumerate(table['per_chunk']):
        if not table['wide'] and offset >= 2 ** 32:
            return None
        moov[p + 8 + step * c:p + 8 + step * (c + 1)] = offset.to_bytes(step, 'big')
        offset += sum(len(chosen[j]) for j in range(k, k + n)); k += n
    target = folder / 'spliced-sound.m4a'
    target.write_bytes(bytes(base[:mdat[1]]) + header + payload + bytes(base[mdat[3]:table['moov'][1]]) + bytes(moov)
                       + bytes(base[table['moov'][3]:]))
    return target


def _bitrate(info):
    try:
        value = int(info.get('bit_rate') or 0)
    except (TypeError, ValueError):
        value = 0
    return f'{max(128, min(320, round(value / 1000) if value else 192))}k'


def encode_command(source, wav, pending, duration, info):
    return ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-i', str(wav), '-map', '0:v:0',
            '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'aac', '-b:a', _bitrate(info), '-t', str(duration),
            '-movflags', '+faststart', str(pending)]


def mux_command(source, wav, pending, folder, duration, windows, rate, info):
    """(ffmpeg command, 'spliced' | 'encoded'). An AAC source keeps its own sound
    packets outside the changed windows; anything else is encoded once."""
    if info.get('codec_name') == 'aac' and str(info.get('profile') or 'LC') == 'LC':
        new = folder / 'new-sound.m4a'
        try:
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(wav), '-c:a', 'aac',
                            '-b:a', _bitrate(info), str(new)], check=True, capture_output=True)
            spliced = splice_aac(source, new, windows, rate, folder)
        except (subprocess.CalledProcessError, ValueError, OSError, StopIteration, IndexError):
            spliced = None
        if spliced:
            return (['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-i', str(spliced),
                     '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-t', str(duration),
                     '-movflags', '+faststart', str(pending)], 'spliced')
    return encode_command(source, wav, pending, duration, info), 'encoded'


def _rms(x):
    return float(np.sqrt(np.mean(np.square(x)))) if len(x) else 0.0


def splice_holds(pending, mixed, windows, rate):
    """The spliced file decodes to the soundtrack we meant, within codec noise."""
    try:
        got, got_rate, _ = native_audio(pending)
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError):
        return False
    if got_rate != rate or abs(len(got) - len(mixed)) > 2048 or got.shape[1] != mixed.shape[1]:
        return False
    n = min(len(got), len(mixed))
    signal = _rms(mixed[:n])
    return _rms(got[:n] - mixed[:n]) <= max(1e-4, signal * 10 ** (-30 / 20))


# --------------------------------------------------------------- the mix


def frame_rate(path):
    import json
    try:
        probe = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
            'stream=avg_frame_rate,r_frame_rate', '-of', 'json', str(path)], capture_output=True, check=True).stdout)
        for key in ('avg_frame_rate', 'r_frame_rate'):
            num, _, den = str(probe['streams'][0].get(key) or '').partition('/')
            value = float(num) / float(den or 1)
            if 1 <= value <= 240:
                return value
    except (subprocess.CalledProcessError, OSError, ValueError, KeyError, IndexError, ZeroDivisionError):
        pass
    return FPS


def _ramp(n, sr=SR):
    out = np.ones(n, np.float32)
    k = min(int(round(EDGE * sr)), n // 2)
    if k:
        out[:k] = np.linspace(0, 1, k); out[-k:] = np.linspace(1, 0, k)
    return out


def level_voices(voices, picked, lines, rows, phrases, stem, sr=SR):
    """One level and one tone per voice: every take of a voice is brought to the
    original speaker's loudness over all its lines, through the same gentle EQ.
    Edits `voices` in place; returns a receipt row per line."""
    groups = {}
    for i in sorted(picked):
        voice = phrases[i].get('voice') or {}
        groups.setdefault(voice.get('id') or voice.get('adapter_path') or voice.get('trigger') or '', []).append(i)
    receipt = []
    for members in groups.values():
        def original(i):
            return stem[int(rows[i][0]['on'] * sr):int(rows[i][-1]['off'] * sr)]
        def own(i):
            lo, hi = lines[i]['window']
            return voices[i][int(lo * sr):int(hi * sr)]
        tone = shared_eq([original(i) for i in members], [own(i) for i in members], sr)
        for i in members:
            voices[i] = apply_eq(voices[i], tone, sr)
        target = loudness([original(i) for i in members], sr)
        for i in members:
            before = loudness([own(i)], sr)
            gain = max(-18.0, min(18.0, target - before)) if before > -69 else 0.0
            voices[i] = voices[i] * np.float32(10 ** (gain / 20))
            receipt.append({'phrase': i, 'line': i + 1, 'loudness': round(loudness([own(i)], sr), 1),
                            'target_loudness': round(target, 1),
                            'original_loudness': round(loudness([original(i)], sr), 1),
                            'gain_db': round(gain, 1), 'eq_db': tone})
    return sorted(receipt, key=lambda row: row['phrase'])


def voice_delta(voices, windows, stem, length, sr=SR):
    """What changes, at the analysis rate: inside each line's window the original
    voice comes out and the new one goes in, with short crossfades; zero elsewhere."""
    delta = np.zeros((length, 2), np.float32)
    stem = stem[:length]
    if len(stem) < length:
        stem = np.pad(stem, ((0, length - len(stem)), (0, 0)))
    for i, voice in voices.items():
        lo, hi = windows[i]
        a, b = max(0, int(round(lo * sr))), min(length, int(round(hi * sr)))
        if b > a:
            delta[a:b] = _ramp(b - a, sr)[:, None] * (voice[a:b] - stem[a:b])
    return delta


def apply_delta(native, delta, windows, rate):
    """The clip's own sound plus the change, at its own rate. Outside the windows
    every sample is the original's, exactly."""
    change = to_rate(delta, rate, native.shape[1])[:len(native)]
    if len(change) < len(native):
        change = np.pad(change, ((0, len(native) - len(change)), (0, 0)))
    mask = np.zeros(len(native), bool)
    for lo, hi in windows:
        mask[max(0, int(round(lo * rate))):int(round(hi * rate))] = True
    change[~mask] = 0
    out = native + change
    for lo, hi in windows:
        a, b = max(0, int(round(lo * rate))), int(round(hi * rate))
        if b <= a or np.max(np.abs(out[a:b])) <= .999:
            continue
        low, high = 0.0, 1.0
        for _ in range(12):
            mid = (low + high) / 2
            if np.max(np.abs(native[a:b] + mid * change[a:b])) <= .999:
                low = mid
            else:
                high = mid
        if low < .5:
            raise ValueError('Voice pass mix would clip. Try another take.')
        out[a:b] = native[a:b] + low * change[a:b]
    return out


def _band_power(x, sr=SR):
    mono = np.asarray(x, np.float32)
    mono = mono.mean(axis=1) if mono.ndim > 1 else mono
    if len(mono) < 32:
        return 0.0
    spec = np.abs(np.fft.rfft(mono)) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / sr)
    return float(spec[(freqs >= 300) & (freqs <= 3400)].sum())


def leftover(original, stem, row, sr=SR):
    """How much of the original voice could still be under the new line: what is
    left of the original soundtrack after the voice comes out, against the voice,
    in the speech band (dB; room tone counts too, so it is an upper bound)."""
    a, b = int(row[0]['on'] * sr), int(row[-1]['off'] * sr)
    voice = stem[a:b]
    rest = original[a:b] - voice[:len(original[a:b])]
    return round(db(_band_power(rest, sr) / max(_band_power(voice, sr), 1e-12)), 1)


def line_receipt(choice, line, row, fps):
    frames = [round(e * fps, 2) for e in choice['errors']]
    ratios = [(s[3] - s[2]) / max(1e-3, s[1] - s[0]) for s in choice['segments'] if s[1] - s[0] > .02]
    lo, hi = line['window']
    return {'text': line['line'][0], 'hint': line['line'][1], 'window': [round(lo, 3), round(hi, 3)],
            'takes': line['attempts'], 'chosen': choice['file'], 'score': round(choice['score'], 3),
            'onset_error_frames': frames,
            'median_error_frames': round(float(np.median([abs(f) for f in frames])), 2) if frames else None,
            'max_error_frames': round(max(abs(f) for f in frames), 2) if frames else None,
            'coverage': round(choice['coverage'], 3), 'words': choice['matched'], 'words_total': len(row),
            'stretch': [round(min(ratios), 2), round(max(ratios), 2)] if ratios else [1, 1],
            'weak': choice['reasons'], 'speaker_onset': round(row[0]['on'], 3),
            'original_end': round(row[-1]['off'], 3),
            'placed_end': round(max(s[3] for s in choice['segments']), 3)}
