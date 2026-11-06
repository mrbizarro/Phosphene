"""Prompt guards for Hailuo H3 — short in-prompt sentences against failures
users reported upstream, applied right before a prompt reaches the runner.

H3 has no negative prompt (guidance-distilled: no CFG, one forward per step),
so the only lever is a plain sentence inside the description field — and the
storyboard planner's L5 allows exactly that for things the model adds by
itself. Each guard here is deterministic, idempotent and only fires when the
prompt does not already ask for the opposite:

* ``ellipsis`` — ``...``/``…`` inside ``<d>…</d>`` become a comma or a full
  stop. The ComfyUI H3 prompt guide: "Ellipses often hurt intelligibility".
  This is the ONLY change ever made to a user's dialogue words.
* ``spoken`` — a prompt with a dialogue line and no singing anywhere gets
  "Every line is spoken in a natural talking voice, not sung." (MiniMax-H3
  issue #68, "All spoken lines sung"). Not on Lip-sync, whose sound is the
  user's own file.
* ``hands`` — hands in the shot, no jewellery or gloves asked for: "Every
  hand in the shot is bare, with no rings or jewellery on any finger." (#86,
  wedding bands invented on bare hands). Not on Add sound, whose picture is
  kept as it is.
* ``nouns`` — a dialogue line that talks about things the description does
  not show gets "The picture shows only what is described here; the things
  the dialogue talks about stay off screen." (#81, whatever the dialogue
  mentions appears in the picture). Not on Add sound.

The sentences go at the END of the description field (before
``overall_soundscape:`` on a three-field prompt, at the end of a prose one),
never inside a ``<d>`` tag, and each is added at most once.

Defaults (DEFAULT_GUARDS) were set by a matched-seed A/B — see
docs/H3_ENGINE.md, "Prompt guards".
"""
from __future__ import annotations

import re

GUARD_KEYS = ("ellipsis", "spoken", "hands", "nouns")

SPOKEN_SENTENCE = "Every line is spoken in a natural talking voice, not sung."
HANDS_SENTENCE = ("Every hand in the shot is bare, with no rings or jewellery "
                  "on any finger.")
NOUNS_SENTENCE = ("The picture shows only what is described here; the things "
                  "the dialogue talks about stay off screen.")

_SENTENCES = {"spoken": SPOKEN_SENTENCE, "hands": HANDS_SENTENCE,
              "nouns": NOUNS_SENTENCE}

# The guards a render gets when the job does not say otherwise. Decided by
# the 2026-10-19 Draft Fast A/B (docs/H3_ENGINE.md "Prompt guards"): ellipsis,
# spoken and hands did no harm; nouns put a stray red prop in the guarded
# take of the umbrella line (none in the unguarded one), so it stays
# available ("all" / "nouns") but off until a wider A/B clears it.
DEFAULT_GUARDS = ("ellipsis", "spoken", "hands")

# Picture guards make no sense when the picture is held (Add sound); the
# voice guard makes none when the sound is the user's file (Lip-sync).
_SKIP_BY_MODE = {"v2a": {"hands", "nouns"}, "a2v": {"spoken"}}

_D_SPAN_RE = re.compile(r"<d>(.*?)</d>", re.IGNORECASE | re.DOTALL)
_LANG_TAG_RE = re.compile(r"^\s*\[[^\]\n]{1,40}\]\s*")
_FIELD_BOUNDARY_RE = re.compile(
    r"\n\s*(?:overall_soundscape|non_diegetic_music)\s*:", re.IGNORECASE)
# Three or more dots, any run of the ellipsis character, or ". . ." spaced.
_ELLIPSIS_RE = re.compile(r"\s*(?:\.\s?){2,}\.|\s*\.{2,}|\s*…+")

# Asking for a sung or musical vocal anywhere outside the dialogue words
# means the spoken guard stays out. Instrumental music alone does not count:
# #68 is lines sung OVER a score.
_SINGING_RE = re.compile(
    r"(?<!birds )(?<!bird )\b(?:sing|sings|sang|sung|singing|chant|chants|chanted|chanting)\b"
    r"|\b(?:singer|singers|song|songs|lyric|lyrics|choir|choral|"
    r"rapper|rappers|serenade\w*|croon\w*|karaoke|a cappella|"
    r"vocalist|vocals|melod\w*|opera|operas|operatic|aria|ballad|anthem|musical)\b", re.IGNORECASE)
# Hands that are in the picture. "handheld"/"hand-held" is a camera word.
_HAND_CAMERA_RE = re.compile(r"\bhand[- ]?held\b", re.IGNORECASE)
_HANDS_RE = re.compile(
    r"\b(?:hands?|fingers?|fingertips?|palms?|fists?|knuckles?|wrists?|thumbs?)\b",
    re.IGNORECASE)
# Anything on the hands the prompt asked for — the guard would contradict it.
_JEWELLERY_RE = re.compile(
    r"\b(?:rings?|wedding band|engagement|jewel\w*|bracelets?|bangles?|signet|"
    r"gloves?|gloved|mittens?|manicure\w*|nail polish|painted nails|tattoo\w*|"
    r"wristwatch\w*|(?:a|his|her|their|gold|silver) watch|henna|bandage\w*)\b", re.IGNORECASE)
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]+")
# Words a line can use without naming a thing that could appear on screen.
_STOP = frozenset("""
about above after again against almost alone already also always another
anyone anything anyway around away back because been before behind being
believe better between both bring came come comes coming could didn doesn
doing done don down each else enough even ever every everyone everything
feel feels felt find first forget forgot from gave give gives going gone
good got gotta great hadn hasn have haven having hear heard hello here
hers herself himself home hope however just keep kept know knew last least
leave left less let like little long look looks make made many maybe mean
might mind mine more most much must myself need needs never next nice
nobody none nothing okay once only other ours ourselves over please pretty
quite rather real really right said same saying says should since some
someone something sometimes soon sorry still such sure take tell tells than
thank thanks that their theirs them then there these they thing things
think this those though thought through time times today together told
tomorrow tonight took truly turn twice under until upon very wait want
wanted wants wasn well went were weren what whatever when where which while
who whole whom whose why will wish with without won wonder word words
would wouldn yeah year years yes yesterday yet you your yours yourself
again once twice ever never always back away again ain isn aren cannot
can't won't don't didn't doesn't isn't aren't wasn't weren't i'm i've i'll
you're you've you'll he's she's it's we're we've they're they've that's
there's let's what's
""".split())


def split_description(prompt: str) -> tuple[str, str]:
    """``(description, rest)``: the rest starts at the first
    ``overall_soundscape:``/``non_diegetic_music:`` line, so head + rest is the
    prompt byte for byte. A prose prompt has no rest."""
    p = prompt or ""
    m = _FIELD_BOUNDARY_RE.search(p)
    if not m:
        return p, ""
    return p[:m.start()], p[m.start():]


def _ellipsis_fix(content: str) -> str:
    """One <d> span's text with every ellipsis turned into , or . — the words
    themselves untouched. The [Language] tag is kept as it is."""
    tag_m = _LANG_TAG_RE.match(content)
    tag = tag_m.group(0) if tag_m else ""
    body = content[len(tag):]

    def repl(m: re.Match) -> str:
        before = body[:m.start()].rstrip()
        after = body[m.end():]
        nxt = after.lstrip()[:1]
        if not before:                       # "...and then" -> "and then"
            return ""
        if not nxt:                          # trailing "..." -> "."
            return "" if before[-1] in ".!?,;:" else "."
        if nxt in ".,!?;:":                   # "Wait...?" -> "Wait?"
            return ""
        if before[-1] in ".!?,;:":            # "No!... never" -> "No! never"
            return " " if not after[:1].isspace() else ""
        sep = "." if nxt.isupper() else ","
        return sep + ("" if after[:1].isspace() else " ")

    fixed = _ELLIPSIS_RE.sub(repl, body)
    if tag and not tag.endswith((" ", "\t")) and fixed[:1] not in ("", " "):
        tag = tag + " "
    return tag + fixed.lstrip() if tag else fixed.lstrip()


def fix_dialogue_ellipses(prompt: str) -> str:
    """The prompt with ellipses inside every <d>…</d> replaced; nothing else
    in the prompt changes."""
    def span(m: re.Match) -> str:
        inner = m.group(1)
        if "…" not in inner and ".." not in inner and ". ." not in inner:
            return m.group(0)
        return "<d>" + _ellipsis_fix(inner) + "</d>"
    return _D_SPAN_RE.sub(span, prompt or "")


def _outside_dialogue(text: str) -> str:
    return _D_SPAN_RE.sub(" ", text or "")


def _dialogue_text(text: str) -> str:
    return " ".join(_LANG_TAG_RE.sub("", m.group(1))
                    for m in _D_SPAN_RE.finditer(text or ""))


def _norm(word: str) -> str:
    w = word.lower().replace("’", "'").strip("'-")
    for suf in ("'s", "s"):
        if w.endswith(suf) and len(w) > 4:
            return w[: -len(suf)]
    return w


def dialogue_offscreen_nouns(prompt: str) -> list[str]:
    """Content words the dialogue says that the description never shows —
    the words #81 says can materialise. A heuristic gate, not a parser: words
    of 4+ letters, minus a stop list, minus every word the description
    (outside the tags) already uses."""
    head, _ = split_description(prompt)
    shown = {_norm(w) for w in _WORD_RE.findall(_outside_dialogue(head))}
    out: list[str] = []
    for w in _WORD_RE.findall(_dialogue_text(head)):
        n = _norm(w)
        if len(n) < 4 or n in _STOP or w.lower() in _STOP or n in shown:
            continue
        # Past-tense verbs ("crossed", "promised") name no thing that could
        # appear; "speed"/"seed" keep their -eed.
        if n.endswith("ed") and not n.endswith("eed"):
            continue
        if n not in out:
            out.append(n)
    return out


def guards_wanted(prompt: str, mode: str = "t2v") -> list[str]:
    """Which sentence guards this prompt needs (ellipsis excluded), in order."""
    head, rest = split_description(prompt)
    has_line = bool(_D_SPAN_RE.search(head))
    outside = _outside_dialogue(head) + " " + _outside_dialogue(rest)
    head_out = _HAND_CAMERA_RE.sub(" ", _outside_dialogue(head))
    skip = _SKIP_BY_MODE.get((mode or "").strip().lower(), set())
    want: list[str] = []
    if has_line and not _SINGING_RE.search(outside):
        want.append("spoken")
    if _HANDS_RE.search(head_out) and not _JEWELLERY_RE.search(head_out):
        want.append("hands")
    if has_line and dialogue_offscreen_nouns(prompt):
        want.append("nouns")
    return [g for g in want if g not in skip]


def _append_to_description(prompt: str, sentences: list[str]) -> str:
    head, rest = split_description(prompt)
    body = head.rstrip()
    trail = head[len(body):]
    if body and body[-1] not in ".!?":
        body += "."
    body = (body + " " + " ".join(sentences)).strip()
    return body + (trail if rest else "") + rest


def append_to_description(prompt: str, sentences: list[str]) -> str:
    """Public form of the description-field append (reference images use it)."""
    return _append_to_description(prompt, sentences)


def apply_guards(prompt: str, mode: str = "t2v",
                 enabled=DEFAULT_GUARDS) -> tuple[str, list[str]]:
    """``(guarded prompt, guards applied)``. ``enabled`` limits which guards
    may fire; a guard whose sentence is already in the prompt is skipped, so
    running this twice changes nothing."""
    text = prompt or ""
    enabled = set(enabled or ())
    applied: list[str] = []
    if not text.strip():
        return text, applied
    if "ellipsis" in enabled:
        fixed = fix_dialogue_ellipses(text)
        if fixed != text:
            text = fixed
            applied.append("ellipsis")
    low = text.lower()
    add = [g for g in guards_wanted(text, mode)
           if g in enabled and _SENTENCES[g].lower() not in low]
    if add:
        text = _append_to_description(text, [_SENTENCES[g] for g in add])
        applied += add
    return text, applied


def parse_guard_setting(raw) -> tuple[str, ...]:
    """The job field ``h3_prompt_guards``: "" / "default" -> DEFAULT_GUARDS,
    "off"/"none" -> (), "all" -> every guard, or a comma list of keys."""
    if raw is None:
        return DEFAULT_GUARDS
    if isinstance(raw, (list, tuple)):
        items = [str(x).strip().lower() for x in raw]
    else:
        s = str(raw).strip().lower()
        if s in ("", "default", "on", "1", "true", "yes"):
            return DEFAULT_GUARDS
        if s in ("off", "none", "0", "false", "no"):
            return ()
        if s == "all":
            return GUARD_KEYS
        items = [x.strip() for x in s.split(",")]
    return tuple(k for k in GUARD_KEYS if k in items)
