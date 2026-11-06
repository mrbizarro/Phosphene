"""One human title for a clip/job, derived from its prompt at read time.

Shared rule (Studio server, Remote API, and mirrored in webapp/js/queue.js
displayTitle() and the iPhone app's ClipTitle): prefer a user-set label; else
strip the H3 structured-prompt scaffolding and take the first sentence of the
scene, trimmed to ~60 chars at a word boundary. Display only: never written
back to prompts, sidecars or file names.
"""
from __future__ import annotations

import re

TITLE_MAX = 60

_FIELDS = re.compile(
    r"\b(?:integrated_multimodal_description|overall_soundscape|non_diegetic_music|"
    r"summary|high_level_description)\s*:", re.I)
_SHOT = re.compile(r"\[\s*Shot\s*\d+\s*\]", re.I)
_TIMESTAMP = re.compile(r"\bAt\s+\d{1,2}:\d{2}(?:\.\d+)?\s*,", re.I)
_SPEAKER = re.compile(r"\(\s*S\d+\s*\)")
_REFTAG = re.compile(r"<\s*(?:Picture|Image|Audio|Video)\s*\d+\s*>(?:\s*\(from[^)]*\))?", re.I)
_DIALOGUE = re.compile(r"<d>[\s\S]*?</d>", re.I)
_ALIGN = re.compile(r"^\s*For the target video, at 0(?:\.0+)? seconds[^\n]*\n?", re.I)
# "vh5tape, worn VHS tape recording..." : a LoRA trigger lead-in, not the scene.
_TRIGGER_LEAD = re.compile(r"^[A-Za-z]*\d[A-Za-z0-9_]*\s*,|^[a-z0-9_]*trn\s*,", re.I)


_STYLE_LEAD = re.compile(
    r"^(?:live-action(?:-style [a-z]+)?|cinematic|animated|photorealistic|vintage film|"
    r"warm film look|film look)\s*,\s*", re.I)
# A sentence that only names the look ("..., shot on VHS", "warm film look").
_STYLE_ONLY = re.compile(r"(?:shot on [A-Za-z0-9 ]{2,20}|film look|film grain|vhs look|tape damage|tape recording)$", re.I)


def _clean(t: str) -> str:
    t = _ALIGN.sub(" ", t)
    for rx in (_FIELDS, _SHOT, _TIMESTAMP, _SPEAKER, _REFTAG):
        t = rx.sub(" ", t)
    return re.sub(r"\s+", " ", t).strip(" :;-")


def _sentences(t: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", t) if s.strip()]


def _trim(t: str, limit: int) -> str:
    if len(t) <= limit:
        return t
    cut = t[:limit + 1]
    sp = cut.rfind(" ")
    cut = cut[:sp] if sp >= limit * 0.5 else t[:limit]
    return cut.rstrip(" ,;:-.") + "…"


def display_title(prompt, label=None, limit: int = TITLE_MAX) -> str:
    lab = re.sub(r"\s+", " ", str(label or "")).strip()
    if lab:
        return _trim(lab, limit)
    orig = str(prompt or "")
    t = _clean(_DIALOGUE.sub(" ", orig))
    if not t:
        inner = " ".join(re.findall(r"<d>([\s\S]*?)</d>", orig, re.I))
        t = _clean(re.sub(r"^\s*\[[A-Za-z ]+\]\s*", "", inner))
    if not t:
        return ""
    sents = _sentences(t)
    first = ""
    for i, sent in enumerate(sents):
        cand = sent.rstrip(".!?")
        while _STYLE_LEAD.match(cand):
            cand = _STYLE_LEAD.sub("", cand, 1)
        last = i == len(sents) - 1
        cand = _TRIGGER_LEAD.sub("", cand, 1).strip()
        if not last and (_STYLE_ONLY.search(cand) or len(cand) < 12):
            continue
        first = cand
        break
    first = first or sents[0].rstrip(".!?")
    if first and first[0].islower():
        first = first[0].upper() + first[1:]
    return _trim(first, limit)
