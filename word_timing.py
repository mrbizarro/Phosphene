"""Word timing shared by the voice pass and Re-voice: Whisper on MLX (word times, forced alignment of
known text) and fuzzy script-to-heard word matching. Public and generic; the captions tool builds on it."""
from __future__ import annotations

import difflib
import os
import re
import subprocess
import time
import unicodedata

MODEL = os.environ.get("PHOSPHENE_SUBS_MODEL", "mlx-community/whisper-large-v3-turbo")
SR = 16000


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
