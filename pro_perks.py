"""pro_perks.py - reads pro_perks.json, the one list of every Pro speed-up and Pro feature.

Every place that tells people what Pro gives them reads its numbers from that file:

- What's new, "Now in Pro" at the top (whats_new.py): the perks a release names in its "pro_perks", and
  "Coming next in Pro": the unreleased perks that carry a 'next' line (coming()).
- The Pro moment after a Free render (pro_speedup.py): the measured Fast draft rows and the moment line.
- The saved-time figure on a finished Pro render (PRO_SPEEDUPS in mlx_ltx_panel.py): each perk's "ratio".

So a new speed-up is one entry in pro_perks.json, not an edit in four files. Pure and stdlib only: the
Studio imports it at start, the tests and the release gate read it without the engine.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().with_name("pro_perks.json")
KINDS = ("speed-up", "feature")
PRO_URL = "https://phosphene.video/pricing/"
_CACHE: dict[str, dict] = {}


def load(path: Path | None = None) -> dict:
    """The whole file ({"perks": [...], ...}); an empty list when it is missing or broken, so a bad edit
    hides Pro notes instead of breaking the Studio. Read once per path."""
    p = Path(path or PATH)
    key = str(p)
    if key not in _CACHE:
        try:
            doc = json.loads(p.read_text("utf-8"))
            if not isinstance(doc, dict) or not isinstance(doc.get("perks"), list):
                raise ValueError("no perks list")
        except (OSError, ValueError):
            doc = {"perks": []}
        _CACHE[key] = doc
    return _CACHE[key]


def perks(path: Path | None = None) -> list[dict]:
    return [p for p in load(path).get("perks") or [] if isinstance(p, dict) and p.get("id")]


def perk(pid: str, path: Path | None = None) -> dict | None:
    return next((p for p in perks(path) if p.get("id") == pid), None)


def released(p: dict | None) -> bool:
    """Shipped in a Studio version. A perk still waiting to be released has since=null and is never
    advertised as new."""
    return bool(p and isinstance(p.get("since"), str) and p["since"].strip())


def _tr(p: dict, lang: str | None) -> dict:
    """The perk's own words in the user's language (its "i18n": {lang: {name, now, next, gain}}), {} for English or
    when there is none: the English then shows."""
    tr = p.get("i18n") if isinstance(p, dict) else None
    if not lang or not isinstance(tr, dict) or str(lang).split("-")[0].lower() == "en":
        return {}
    if lang in tr and isinstance(tr[lang], dict):
        return tr[lang]
    base = str(lang).split("-")[0].lower()
    for k, v in tr.items():
        if k.split("-")[0].lower() == base and isinstance(v, dict):
            return v
    return {}


def _word(p: dict, key: str, lang: str | None) -> str:
    t = _tr(p, lang).get(key)
    if isinstance(t, str) and t.strip():
        return t.strip()
    v = p.get(key)
    return v.strip() if isinstance(v, str) else ""


def teaser(p: dict | None, lang: str | None = None) -> str:
    """The perk's 'next' line: the one public sentence about what is coming ('' when it has none, or when the
    perk is already released: a released perk is news, not a tease)."""
    if not p or released(p):
        return ""
    if not (isinstance(p.get("next"), str) and p["next"].strip()):
        return ""
    return _word(p, "next", lang)


def coming(path: Path | None = None, lang: str | None = None) -> list[dict]:
    """What is coming next in Pro (owner, 14 Nov 2026: "if we don't have something to release in Pro, we can still
    tease it"): the unreleased perks that carry a 'next' line, in file order. An unreleased perk without one is kept
    quiet. Same shape as line(), with the teaser as the text."""
    out = []
    for p in perks(path):
        t = teaser(p, lang)
        if t:
            out.append({**line(p, lang), "text": t})
    return out


def rows(pid: str, path: Path | None = None) -> dict:
    """The measured rows of one perk ({} when nothing was measured)."""
    m = (perk(pid, path) or {}).get("measured") or {}
    r = m.get("rows") if isinstance(m, dict) else None
    return r if isinstance(r, dict) else {}


def ratio(pid: str, default: float = 1.0, path: Path | None = None) -> float:
    """How many times faster Pro is, for the saved-time figure: the perk's own "ratio" when it states one,
    else free_s / pro_s of its single measured row."""
    p = perk(pid, path) or {}
    try:
        if p.get("ratio"):
            return float(p["ratio"])
        r = rows(pid, path)
        if r.get("free_s") and r.get("pro_s"):
            return round(float(r["free_s"]) / float(r["pro_s"]), 2)
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return default


def moment(pid: str, path: Path | None = None) -> str:
    """The perk's gentle in-app line ('' when it has none)."""
    t = (perk(pid, path) or {}).get("moment")
    return t.strip() if isinstance(t, str) else ""


def pro_url(path: Path | None = None) -> str:
    u = load(path).get("pro_url")
    return u if isinstance(u, str) and u.startswith("https://phosphene.video/") else PRO_URL


def line(p: dict, lang: str | None = None) -> dict:
    """What What's new shows for one perk: name, the one line, the measured gain (in the user's language)."""
    name = _word(p, "name", lang)
    return {"id": p["id"], "kind": p.get("kind") or "speed-up", "name": name,
            "text": _word(p, "now", lang) or name, "gain": _word(p, "gain", lang)}


def problems(path: Path | None = None) -> list[str]:
    """What the release gate refuses: a perk without a line, a kind, or (for a speed-up) a measurement
    and where it came from. Numbers are never projected."""
    out, seen = [], set()
    for p in perks(path):
        pid = p["id"]
        if pid in seen:
            out.append(f"{pid}: listed twice")
        seen.add(pid)
        if p.get("kind") not in KINDS:
            out.append(f"{pid}: kind must be one of {KINDS}")
        for k in ("name", "now", "gain"):
            if not str(p.get(k) or "").strip():
                out.append(f"{pid}: no {k}")
        m = p.get("measured")
        if p.get("kind") == "speed-up":
            if not isinstance(m, dict) or not m.get("rows") or not str(m.get("source") or "").strip():
                out.append(f"{pid}: a speed-up needs measured.rows and measured.source")
        mo = p.get("moment")
        if mo is not None and not isinstance(mo, str):
            out.append(f"{pid}: moment must be text")
        nx = p.get("next")
        if nx is not None and not isinstance(nx, str):
            out.append(f"{pid}: next must be text")
        tr = p.get("i18n")
        if tr is not None and not isinstance(tr, dict):
            out.append(f"{pid}: i18n must be {{lang: {{name, now, next, gain}}}}")
        if released(p) and isinstance(nx, str) and nx.strip():
            out.append(f"{pid}: released in {p['since']} but still carries a 'next' tease: remove it")
    return out
