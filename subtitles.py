"""Subtitles - captions for a clip or a cut, timed to the audio.

Phosphene usually KNOWS the words: an H3 prompt carries its dialogue in
`<d>[English] ...</d>`, an LTX prompt in `says, "..."`, a music job carries its
lyrics. So the words come from the script and only the timing comes from the
audio. Whisper (large-v3-turbo on MLX) is the ear: it hears the clip with word
times, the script's words are matched to what it heard, and each script line is
then snapped to the audio by forced alignment (Whisper's own cross-attention,
fed the known words, the same model, no second download). A line Whisper did
not hear at all (a sung line under loud music) is still placed: it is aligned
inside the gap between the lines around it. A clip with no script falls back to
Whisper's own words.

Pure logic + a CLI; the panel calls it in a subprocess of an env that has
mlx_whisper (`python subtitles.py make CLIP --prompt-file .. --out ..`).

  make    clip -> cues.json + .srt + .vtt (+ --burn out.mp4)
  burn    clip + cues.json -> out.mp4 (libass when ffmpeg has it, else cards)
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

MODEL = os.environ.get("PHOSPHENE_SUBS_MODEL", "mlx-community/whisper-large-v3-turbo")
SR = 16000
MAX_CHARS = 42          # per caption row (Netflix-style limit)
MIN_CUE = 1.0           # s; a shorter cue is held longer when there is room
HOLD = 0.35             # s after the last word, so the eye can finish reading
LEAD = 0.08             # s before the first word

# ---------------------------------------------------------------- the script

_D_TAG = re.compile(r"<d>\s*(?:\[([^\]]{1,24})\])?\s*(.*?)\s*</d>", re.S | re.I)
_SAYS = re.compile(
    r"\b(says|said|asks|asked|replies|whispers|shouts|calls|announces|murmurs|mutters|sings|sang|chants)\b"
    r"[^\"“]{0,48}[\"“]([^\"”]{1,400})[\"”]", re.I)
# the same, single-quoted: "sings the words 'He was soft, so we could be hard'"
_SAYS1 = re.compile(
    r"\b(says|said|asks|asked|replies|whispers|shouts|calls|announces|murmurs|mutters|sings|sang|chants)\b"
    r"[^\"'\u2018\u201c]{0,48}(?<![A-Za-z])['\u2018]([^'\u2019]{1,400})['\u2019](?=[\s,.;:!?)]|$)", re.I)
_SECTION = re.compile(r"^\s*[\[(][^\])]*[\])]\s*$")


@dataclass
class Line:
    text: str
    kind: str = "say"                   # say | song
    lang: str | None = None             # from <d>[Latin]
    translation: str | None = None


def script_from_prompt(prompt: str) -> list[Line]:
    """Dialogue lines in prompt order. H3 `<d>[Lang] text</d>` first; else LTX
    `he says, "text"`. A sung verb marks the line as a song."""
    out: list[Line] = []
    for lang, text in _D_TAG.findall(prompt or ""):
        text = " ".join(text.split())
        if text:
            out.append(Line(text, "say", (lang or "").strip() or None))
    if out:
        return out
    found = sorted([(m.start(), m.group(1), m.group(2)) for rx in (_SAYS, _SAYS1) for m in rx.finditer(prompt or "")])
    for _, verb, text in found:
        text = " ".join(text.split())
        if text:
            out.append(Line(text, "song" if verb.lower() in ("sings", "sang", "chants") else "say"))
    return out


def script_from_lyrics(lyrics: str, translations: dict | None = None) -> list[Line]:
    """Lyrics -> one song line per sung line; [Verse] tags and blank lines go."""
    out = []
    for raw in (lyrics or "").splitlines():
        t = raw.strip()
        if not t or _SECTION.match(t):
            continue
        out.append(Line(t, "song", translation=(translations or {}).get(t)))
    return out


# ---------------------------------------------------------------- matching


def norm(w: str) -> str:
    w = unicodedata.normalize("NFKD", w)
    return re.sub(r"[^a-z0-9']", "", "".join(c for c in w if not unicodedata.combining(c)).lower())


def _sim(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_words(script: list[str], heard: list[str]) -> list[int | None]:
    """For each script word, the index of the heard word it is, or None.
    LOCAL alignment (Smith-Waterman): free skips at both ends of BOTH sides,
    because a clip may hold more audio than its script and a cut may use only
    part of a song's lyrics (the verse, not the chorus). Gaps inside cost a
    little; fuzzy word similarity lets 'proxies'/'proxy', 'wi-fi'/'wifi' match.
    Below that, one stretch of the script is matched; `match_all` repeats it
    on what is left so two separate heard passages both land."""
    n, m = len(script), len(heard)
    out: list[int | None] = [None] * n
    if not n or not m:
        return out
    GAP = -0.35
    S = [[0.0] * (m + 1) for _ in range(n + 1)]
    B = [[0] * (m + 1) for _ in range(n + 1)]   # 0 stop, 1 diag, 2 up, 3 left
    best, bi, bj = 0.0, 0, 0
    for i in range(1, n + 1):
        a = script[i - 1]
        for j in range(1, m + 1):
            sm = _sim(a, heard[j - 1])
            d = S[i - 1][j - 1] + (2.0 * sm - 0.8 if sm >= 0.55 else -1.0)
            u, l = S[i - 1][j] + GAP, S[i][j - 1] + GAP
            v, k = max((0.0, 0), (d, 1), (u, 2), (l, 3))
            S[i][j], B[i][j] = v, k
            if v > best:
                best, bi, bj = v, i, j
    i, j = bi, bj
    while i > 0 and j > 0 and B[i][j]:
        k = B[i][j]
        if k == 1:
            if _sim(script[i - 1], heard[j - 1]) >= 0.55:
                out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif k == 2:
            i -= 1
        else:
            j -= 1
    return out


def match_all(script: list[str], heard: list[str]) -> list[int | None]:
    """match_words, then again on the script words before and after the
    matched stretch against the heard words before and after it."""
    out = match_words(script, heard)
    hit = [k for k, v in enumerate(out) if v is not None]
    if not hit:
        return out
    k0, k1 = hit[0], hit[-1]
    h0, h1 = out[k0], out[k1]
    if k0 > 0 and h0 > 0:
        left = match_all(script[:k0], heard[:h0])
        out[:k0] = left
    if k1 + 1 < len(script) and h1 + 1 < len(heard):
        right = match_all(script[k1 + 1:], heard[h1 + 1:])
        out[k1 + 1:] = [None if v is None else v + h1 + 1 for v in right]
    return out


# ---------------------------------------------------------------- the ear


class Ear:
    """Whisper on MLX: transcribe with word times, and force-align known text."""

    def __init__(self, model: str = MODEL):
        import mlx.core as mx
        import mlx_whisper
        from mlx_whisper import audio as wa
        from mlx_whisper.load_models import load_model
        from mlx_whisper.tokenizer import get_tokenizer
        self.mx, self.mw, self.wa = mx, mlx_whisper, wa
        self.repo = model
        t = time.time()
        self.model = load_model(model, dtype=mx.float16)
        self.load_s = time.time() - t
        self._tok = {}
        self._get_tokenizer = get_tokenizer

    def tokenizer(self, lang: str | None):
        key = lang or "en"
        if key not in self._tok:
            self._tok[key] = self._get_tokenizer(self.model.is_multilingual, num_languages=self.model.num_languages,
                                                 language=key, task="transcribe")
        return self._tok[key]

    def hear(self, audio, language: str | None = None) -> tuple[list[dict], str]:
        """Words with times. No initial_prompt: feeding the script as a prompt
        made it loop ("66.32 66.32 ...") on SUNDAY MASS; the silence threshold
        stops it inventing words in the gaps."""
        from mlx_whisper.transcribe import ModelHolder
        ModelHolder.model, ModelHolder.model_path = self.model, self.repo     # reuse, no reload
        r = self.mw.transcribe(audio, path_or_hf_repo=self.repo, word_timestamps=True, language=language,
                               condition_on_previous_text=False, hallucination_silence_threshold=2.0,
                               temperature=(0.0, 0.2, 0.4), fp16=True)
        words = [{"w": w["word"].strip(), "s": float(w["start"]), "e": float(w["end"]), "p": float(w.get("probability", 0))}
                 for seg in r.get("segments", []) for w in seg.get("words", []) if w["word"].strip()]
        return words, r.get("language") or language or "en"

    def force(self, audio, t0: float, t1: float, text: str, lang: str | None) -> list[dict]:
        """Word times of KNOWN text inside [t0, t1] (seconds). Whisper's
        alignment heads over the decoder fed the given tokens, then DTW: the
        same method its own word timestamps use, but on our words."""
        import numpy as np
        from mlx_whisper.timing import find_alignment
        wa, mx = self.wa, self.mx
        a0, a1 = max(0, int(t0 * SR)), min(len(audio), int(t1 * SR))
        seg = audio[a0:a1]
        if len(seg) < SR * 0.2:
            return []
        n_frames = len(seg) // wa.HOP_LENGTH
        mel = wa.log_mel_spectrogram(np.asarray(seg, dtype=np.float32), n_mels=self.model.dims.n_mels,
                                     padding=wa.N_SAMPLES)
        mel = wa.pad_or_trim(mel, wa.N_FRAMES, axis=-2).astype(mx.float16)
        tok = self.tokenizer(lang)
        toks = tok.encode(" " + text.strip())
        al = find_alignment(self.model, tok, toks, mel, min(n_frames, wa.N_FRAMES))
        return [{"w": a.word.strip(), "s": t0 + float(a.start), "e": t0 + float(a.end), "p": float(a.probability)}
                for a in al if a.word.strip()]


def load_audio(path: str):
    import numpy as np
    raw = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0


# ---------------------------------------------------------------- cues


@dataclass
class Cue:
    start: float
    end: float
    text: str
    kind: str = "say"
    translation: str | None = None
    source: str = "script"            # script | heard
    sure: bool = True                 # False: placed in a gap, not confirmed by the ear
    words: list = field(default_factory=list)


def _spans(lines, sw, owner, idx, heard):
    out = []
    for li in range(len(lines)):
        hits = [heard[idx[k]] for k in range(len(sw)) if owner[k] == li and idx[k] is not None]
        nwords = sum(1 for o in owner if o == li)
        out.append([hits[0]["s"], hits[-1]["e"], len(hits) / max(1, nwords), hits]
                   if hits and len(hits) >= max(1, round(0.34 * nwords)) else None)
    return out


def _free_gap(heard, a, b):
    """The longest stretch of [a, b] no heard word sits in."""
    pts = sorted((max(a, h["s"]), min(b, h["e"])) for h in heard if h["e"] > a and h["s"] < b)
    best, cur = (a, a), a
    for s0, e0 in pts:
        if s0 - cur > best[1] - best[0]:
            best = (cur, s0)
        cur = max(cur, e0)
    if b - cur > best[1] - best[0]:
        best = (cur, b)
    return best


def align_script(ear: Ear, audio, lines: list[Line], heard: list[dict], lang: str | None,
                 refine: bool = True, lo: float = 0.0, hi: float | None = None) -> list[Cue]:
    """Known lines -> cues. `lo`/`hi` bound the search to where this source
    plays (a clip's slot in a cut, the span of the music bed).

    1. Match the script's words to the words Whisper heard (match_all): a line
       with enough matched words is timed by them. Measured on SUNDAY MASS this
       is the most accurate timing we have (median 0.06 s from hand-placed).
    2. A line it did not hear, BETWEEN heard lines or after them, gets a second
       listen: Whisper again on just the free gap (sung lines under music are
       often heard on a short window when the long pass missed them), and the
       match is redone with those words.
    3. Still nothing: forced alignment of the known words in the gap. Whisper's
       DTW stretches the first word over any music before it, so the time is a
       guess: the cue is kept, marked unsure, shown for review, never burned.
    `refine=False` stops after step 1 (the bench's "script-match")."""
    dur = len(audio) / SR if hi is None else min(hi, len(audio) / SR)
    heard = [h for h in heard if h["e"] > lo and h["s"] < dur]
    sw, owner = [], []
    for li, ln in enumerate(lines):
        for w in ln.text.split():
            if norm(w):
                sw.append(norm(w))
                owner.append(li)
    idx = match_all(sw, [norm(h["w"]) for h in heard])
    spans = _spans(lines, sw, owner, idx, heard)
    if refine and any(spans) and not all(spans):
        # step 2: listen again in each free gap that holds an unheard line after the first heard one
        first = next(li for li, sp in enumerate(spans) if sp)
        extra = []
        for li in range(first + 1, len(lines)):
            if spans[li]:
                continue
            p = next((spans[k][1] for k in range(li - 1, -1, -1) if spans[k]), lo)
            n_ = next((spans[k][0] for k in range(li + 1, len(lines)) if spans[k]), dur)
            g0, g1 = _free_gap(heard + extra, p, n_)
            if g1 - g0 >= 0.8:
                a0, a1 = int(max(0, g0 - 0.15) * SR), int(min(dur, g1 + 0.15) * SR)
                words, _ = ear.hear(audio[a0:a1], lang)
                extra += [dict(w, s=w["s"] + a0 / SR, e=w["e"] + a0 / SR) for w in words]
        if extra:
            heard = sorted(heard + extra, key=lambda h: h["s"])
            idx = match_all(sw, [norm(h["w"]) for h in heard])
            spans = _spans(lines, sw, owner, idx, heard)
    cues: list[Cue] = []
    first = next((li for li, sp in enumerate(spans) if sp), None)
    for li, ln in enumerate(lines):
        sp = spans[li]
        if sp:
            cues.append(Cue(sp[0], sp[1], ln.text, ln.kind, ln.translation, "script", True,
                            [{"w": h["w"], "s": round(h["s"], 3), "e": round(h["e"], 3)} for h in sp[3]]))
            continue
        if not refine or first is None or li < first:
            continue          # before anything was heard: not in this cut (the verse before the chorus)
        p = next((spans[k][1] for k in range(li - 1, -1, -1) if spans[k]), lo)
        n_ = next((spans[k][0] for k in range(li + 1, len(lines)) if spans[k]), dur)
        g0, g1 = _free_gap(heard, p, n_)
        nw = len(ln.text.split())
        if g1 - g0 < 0.3 * nw + 0.3:
            continue          # no room for it: it is not in the audio
        words = ear.force(audio, g0, g1, ln.text, ln.lang and _lang_code(ln.lang) or lang)
        if not words or sum(w["p"] for w in words) / len(words) < 0.2:
            continue
        cues.append(Cue(words[0]["s"], words[-1]["e"], ln.text, ln.kind, ln.translation, "script", False,
                        [dict(w, s=round(w["s"], 3), e=round(w["e"], 3)) for w in words]))
    return cues


def drop_unsure(cues: list[Cue]) -> list[Cue]:
    """What is burned in: lines the ear confirmed. A line only placed in a gap
    stays in the cue file (the editor shows it for a look) but is not burned."""
    return [c for c in cues if c.sure]


def cues_from_heard(heard: list[dict], kind: str = "say") -> list[Cue]:
    """No script: group Whisper's words into readable cues (sentence end,
    a pause over 0.6 s, or ~2 rows of text)."""
    cues, cur = [], []
    for i, w in enumerate(heard):
        cur.append(w)
        text = " ".join(x["w"] for x in cur)
        nxt = heard[i + 1] if i + 1 < len(heard) else None
        brk = (nxt is None or nxt["s"] - w["e"] > 0.6 or re.search(r"[.!?…]$", w["w"]) or len(text) > 2 * MAX_CHARS - 10
               or (w["e"] - cur[0]["s"]) > 5.5)
        if brk:
            cues.append(Cue(cur[0]["s"], cur[-1]["e"], text, kind, None, "heard", True,
                            [dict(x) for x in cur]))
            cur = []
    return cues


def _lang_code(name: str) -> str | None:
    from mlx_whisper.tokenizer import TO_LANGUAGE_CODE, LANGUAGES
    n = name.strip().lower()
    return n if n in LANGUAGES else TO_LANGUAGE_CODE.get(n)


def _settle(cues: list[Cue]) -> list[Cue]:
    """Two sources speaking at once (a line over the choir): the spoken line
    wins the screen, the sung one is cut back around it."""
    cues = sorted(cues, key=lambda c: c.start)
    out: list[Cue] = []
    for c in cues:
        if out and c.start < out[-1].end:
            p = out[-1]
            if norm(p.text.replace(" ", "")) == norm(c.text.replace(" ", "")):
                # the same line from two sources (the song's lyrics and the shot's prompt)
                if (c.sure, c.end - c.start) > (p.sure, p.end - p.start):
                    out[-1] = c
                continue
            if p.kind == "song" and c.kind == "say":
                p.end = c.start
                if p.end - p.start < 0.4:
                    out.pop()
            elif c.kind == "song" and p.kind == "say":
                c.start = p.end
                if c.end - c.start < 0.4:
                    continue
        out.append(c)
    return out


def finish(cues: list[Cue], dur: float) -> list[Cue]:
    """Lead-in, read-out hold, minimum length, no overlaps."""
    cues = sorted(cues, key=lambda c: c.start)
    for i, c in enumerate(cues):
        nxt = cues[i + 1].start if i + 1 < len(cues) else dur
        prv = cues[i - 1].end if i else 0.0
        c.start = max(prv, c.start - LEAD, 0.0)
        c.end = min(max(c.end + HOLD, c.start + MIN_CUE), max(c.end, nxt - 0.04), dur)
        c.start, c.end = round(c.start, 3), round(c.end, 3)
    return cues


def wrap(text: str, width: int = MAX_CHARS) -> list[str]:
    """One row if it fits, else two balanced rows (bottom-heavy)."""
    if len(text) <= width:
        return [text]
    words = text.split()
    best, cut = None, 1
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        score = abs(len(a) - len(b)) + (0 if len(a) <= len(b) else 3) + (0 if re.search(r"[,.;:!?]$", words[k - 1]) else 4)
        if best is None or score < best:
            best, cut = score, k
    return [" ".join(words[:cut]), " ".join(words[cut:])]


# ---------------------------------------------------------------- files


def _ts(t: float, sep: str) -> str:
    ms = int(round(t * 1000))
    return "%02d:%02d:%02d%s%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, sep, ms % 1000)


def to_srt(cues: list[Cue], notes: bool = True) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        body = "\n".join(wrap(c.text))
        if c.kind == "song" and notes:
            body = "♪ " + body + " ♪"
        if c.translation:
            body += "\n(" + c.translation + ")"
        out.append("%d\n%s --> %s\n%s\n" % (i, _ts(c.start, ","), _ts(c.end, ","), body))
    return "\n".join(out)


def to_vtt(cues: list[Cue]) -> str:
    out = ["WEBVTT", ""]
    for c in cues:
        body = "\n".join(wrap(c.text))
        if c.kind == "song":
            body = "<i>♪ " + body + " ♪</i>"
        if c.translation:
            body += "\n<i>" + c.translation + "</i>"
        out += ["%s --> %s line:88%%" % (_ts(c.start, "."), _ts(c.end, ".")), body, ""]
    return "\n".join(out)


# The look that worked on SUNDAY MASS: white dialogue, italic cream sung lines
# between notes, a smaller translation underneath, soft shadow, bottom centre.
STYLE = {"font": "Helvetica Neue", "size": 0.047, "margin": 0.075, "say": "&H00FFFFFF", "song": "&H00C8E8F6",
         "tr": "&H10D8E4EA", "outline": 0.0022, "shadow": 0.0028}


def to_ass(cues: list[Cue], w: int, h: int, style: dict | None = None) -> str:
    st = dict(STYLE, **(style or {}))
    fs, mv = round(h * st["size"]), round(h * st["margin"])
    ol, sh = max(1, round(h * st["outline"] * 1.6)), max(1, round(h * st["shadow"] * 1.6))
    head = ("[Script Info]\nScriptType: v4.00+\nPlayResX: %d\nPlayResY: %d\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
            "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
            "MarginL, MarginR, MarginV, Encoding\n") % (w, h)
    row = "Style: %s,%s,%d,%s,&H000000FF,&HA0000000,&H96000000,0,%d,0,0,100,100,0,0,1,%d,%d,2,%d,%d,%d,1\n"
    head += row % ("Say", st["font"], fs, st["say"], 0, ol, sh, mv, mv, mv)
    head += row % ("Song", st["font"], fs, st["song"], 1, ol, sh, mv, mv, mv)
    head += row % ("Tr", st["font"], round(fs * 0.74), st["tr"], 1, ol, sh, mv, mv, mv)
    head += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"

    def t(x):
        cs = int(round(x * 100))
        return "%d:%02d:%02d.%02d" % (cs // 360000, cs // 6000 % 60, cs // 100 % 60, cs % 100)

    ev = []
    for c in cues:
        body = r"\N".join(wrap(c.text))
        if c.kind == "song":
            body = "♪  " + body + "  ♪"
        lift = mv
        if c.translation:
            ev.append("Dialogue: 0,%s,%s,Tr,,0,0,%d,,%s" % (t(c.start), t(c.end), mv, c.translation))
            lift = mv + round(fs * 0.74 * 1.25)
        ev.append("Dialogue: 0,%s,%s,%s,,0,0,%d,,%s" % (t(c.start), t(c.end), "Song" if c.kind == "song" else "Say",
                                                      lift, body))
    return head + "\n".join(ev) + "\n"


def ffmpeg_has_libass(ff: str) -> bool:
    try:
        out = subprocess.run([ff, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=10).stdout
    except Exception:  # noqa: BLE001
        return False
    return bool(re.search(r"^\s*\S+\s+subtitles\s", out, re.M))


def burn(src: str, cues: list[Cue], dst: str, ff: str | None = None, style: dict | None = None) -> str:
    """Burn the cues in. libass when this ffmpeg has it (the Mac app's and
    Pinokio's both do); otherwise a still card per cue laid over the picture."""
    ff = ff or os.environ.get("LTX_FFMPEG") or shutil.which("ffmpeg")
    w, h = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                    "stream=width,height", "-of", "csv=p=0", src], capture_output=True, text=True,
                                   check=True).stdout.strip().split(",")[:2])
    work = Path(dst).with_suffix(".subs")
    work.mkdir(exist_ok=True)
    enc = ["-c:v", "libx264", "-crf", "17", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "copy",
           "-movflags", "+faststart"]
    if ffmpeg_has_libass(ff):
        ass = work / "subs.ass"
        ass.write_text(to_ass(cues, w, h, style), encoding="utf-8")
        esc = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        vf = "subtitles='%s':fontsdir=/System/Library/Fonts" % esc
        subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-i", src, "-vf", vf, *enc, dst], check=True)
        return "libass"
    # fallback: PNG cards
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    ins, chain, last = [], [], "0:v"
    for i, c in enumerate(cues):
        p = work / ("cue_%03d.png" % i)
        _card(c, w, h, Image, ImageDraw, ImageFilter, ImageFont).save(p)
        ins += ["-i", str(p)]
        chain.append("[%s][%d:v]overlay=0:0:enable='between(t,%.3f,%.3f)'[v%d]" % (last, i + 1, c.start, c.end, i))
        last = "v%d" % i
    if not cues:
        shutil.copyfile(src, dst)
        return "none"
    subprocess.run([ff, "-nostdin", "-v", "error", "-y", "-i", src, *ins, "-filter_complex", ";".join(chain),
                    "-map", "[%s]" % last, "-map", "0:a?", *enc, dst], check=True)
    return "cards"


def _card(c, w, h, Image, ImageDraw, ImageFilter, ImageFont):
    F = "/System/Library/Fonts/"
    s = h / 720
    main = ImageFont.truetype(F + ("SFNSItalic.ttf" if c.kind == "song" else "SFNS.ttf"), round(34 * s))
    small = ImageFont.truetype(F + "SFNSItalic.ttf", round(22 * s))
    col = (246, 232, 200, 255) if c.kind == "song" else (255, 255, 255, 255)
    rows = wrap(c.text)
    if c.kind == "song":
        rows[0], rows[-1] = "♪ " + rows[0], rows[-1] + " ♪"
    img = Image.new("RGBA", (w, h))
    d = ImageDraw.Draw(img)
    y = h - round((96 if c.translation else 70) * s) - (len(rows) - 1) * round(40 * s)
    for r in rows:
        d.text((w / 2, y), r, font=main, fill=col, anchor="ms")
        y += round(40 * s)
    if c.translation:
        d.text((w / 2, y - round(8 * s)), c.translation, font=small, fill=(220, 214, 200, 235), anchor="ms")
    a = img.getchannel("A")
    shadow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    shadow.putalpha(a.filter(ImageFilter.MaxFilter(5)).filter(ImageFilter.GaussianBlur(3 * s)).point(lambda v: int(v * .85)))
    return Image.alpha_composite(shadow, img)


# ---------------------------------------------------------------- CLI


def make(clip: str, lines: list[Line], out: str, language: str | None = None, ear: Ear | None = None,
         refine: bool = True, sources: list | None = None) -> dict:
    """`lines` for one clip; `sources` = [(t0, t1, lines), ...] for a cut, one
    per clip with a script and one per music bed with lyrics."""
    t0 = time.time()
    ear = ear or Ear()
    audio = load_audio(clip)
    dur = len(audio) / SR
    t1 = time.time()
    heard, lang = ear.hear(audio, language)
    t2 = time.time()
    if sources:
        cues = []
        for a, b, ls in sources:
            cues += align_script(ear, audio, ls, heard, language or lang, refine=refine, lo=a, hi=b)
        cues = _settle(cues)
        mode = "script"
    elif lines:
        cues = align_script(ear, audio, lines, heard, language or lang, refine=refine)
        mode = "script" if refine else "script-match"
    else:
        cues = cues_from_heard(heard)
        mode = "heard"
    cues = finish(cues, dur)
    t3 = time.time()
    base = Path(out)
    base.parent.mkdir(parents=True, exist_ok=True)
    rep = {"clip": str(clip), "mode": mode, "language": lang, "duration": round(dur, 2), "model": ear.repo,
           "timing": {"load_model": round(ear.load_s, 2), "audio": round(t1 - t0, 2), "hear": round(t2 - t1, 2),
                      "align": round(t3 - t2, 2)},
           "heard": heard, "cues": [asdict(c) for c in cues]}
    base.with_suffix(".json").write_text(json.dumps(rep, indent=1, ensure_ascii=False))
    base.with_suffix(".srt").write_text(to_srt(cues), encoding="utf-8")
    base.with_suffix(".vtt").write_text(to_vtt(cues), encoding="utf-8")
    return rep


def _cues(rep: dict) -> list[Cue]:
    return [Cue(**c) for c in rep["cues"]]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("clip")
    m.add_argument("--out", required=True, help="base path; writes .json .srt .vtt")
    m.add_argument("--prompt", default="")
    m.add_argument("--prompt-file")
    m.add_argument("--lyrics-file")
    m.add_argument("--translations", help="json {line: translation}")
    m.add_argument("--language")
    m.add_argument("--no-script", action="store_true", help="ignore the script: Whisper's words only")
    m.add_argument("--no-refine", action="store_true", help="script words timed by matching only")
    m.add_argument("--burn", help="also write a captioned copy here")
    m.add_argument("--source", action="append", default=[],
                   help="T0:T1:prompt=FILE or T0:T1:lyrics=FILE[:tr=FILE] (a cut: repeat per clip / music bed)")
    b = sub.add_parser("burn")
    b.add_argument("clip")
    b.add_argument("cues")
    b.add_argument("dst")
    b.add_argument("--cards", action="store_true", help="force the PNG-card route")
    a = ap.parse_args(argv)
    if a.cmd == "make":
        prompt = Path(a.prompt_file).read_text() if a.prompt_file else a.prompt
        tr = json.loads(Path(a.translations).read_text()) if a.translations else None
        lines = [] if a.no_script else script_from_prompt(prompt)
        if a.lyrics_file and not a.no_script:
            lines += script_from_lyrics(Path(a.lyrics_file).read_text(), tr)
        sources = []
        for spec in a.source:
            t0, t1, rest = spec.split(":", 2)
            parts = dict(p.split("=", 1) for p in rest.split(":"))
            if "prompt" in parts:
                ls = script_from_prompt(Path(parts["prompt"]).read_text())
            else:
                trs = json.loads(Path(parts["tr"]).read_text()) if "tr" in parts else None
                ls = script_from_lyrics(Path(parts["lyrics"]).read_text(), trs)
            if ls:
                sources.append((float(t0), float(t1), ls))
        rep = make(a.clip, lines, a.out, a.language, refine=not a.no_refine, sources=sources or None)
        print(json.dumps({k: rep[k] for k in ("mode", "language", "duration", "timing")}))
        for c in rep["cues"]:
            print("%7.2f %7.2f %-5s %s %s" % (c["start"], c["end"], c["kind"], "" if c["sure"] else "?", c["text"]))
        if a.burn:
            print("burned with", burn(a.clip, drop_unsure(_cues(rep)), a.burn))
    else:
        rep = json.loads(Path(a.cues).read_text())
        print("burned with", burn(a.clip, _cues(rep), a.dst, ff="/opt/homebrew/bin/ffmpeg" if a.cards else None))


if __name__ == "__main__":
    sys.exit(main())
