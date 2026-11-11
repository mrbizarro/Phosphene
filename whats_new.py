"""The "What's new" card: what a user got when Phosphene updated.

whats_new.json (shipped in the Studio, public) holds per version {free: [...], pro: [...]}.
The Pro pack may carry whats_new_pro.json beside its engine (same shape, pro[] only); those extra
lines are shown to Pro users only, so Pro-only detail never sits in the public repo. Free users see
the public Pro list too (public marketing copy: plain benefit wording, no trick or engine names).

Shown once per version: state/whats_new_seen.json remembers the last version the user closed.
If versions were skipped, the card combines every entry after the last seen one, newest first.

A version may carry a "date" and a "hero" (the big visual at the top: Make it vertical's three-way clip, or
one picture/clip). The top-level "showcase" holds the measured "What Pro adds" items shown to Free users.
"how" holds a short muted clip per new feature, recorded on the real Studio: where the button is and what
you get, a Free take and a Pro take when Pro adds something. The card shows it, and the feature itself links
to it (GET /whats-new/how?id=...). Every new feature gets one: it is the habit, not a one-off.
"earlier" lists every older entry so the card can page back through past releases without leaving the app.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MAX_LINES = 8          # per section: a card, not a changelog
FREE_TITLE = "New for everyone"
PRO_TITLE = "New in Pro"
THANKS = "Thank you for supporting Phosphene. This is what your Pro licence added."
PRICING_URL = "https://phosphene.video/pricing/"
PLANS_URL = "https://phosphene.video/pricing/#plans"
CHANGELOG_URL = "https://phosphene.video/changelog/"
SITE = "https://phosphene.video/"


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def _lines(raw) -> list[dict]:
    out = []
    for it in raw or []:
        if isinstance(it, str) and it.strip():
            out.append({"text": it.strip()})
        elif isinstance(it, dict) and str(it.get("text") or "").strip():
            d = {"text": str(it["text"]).strip()}
            for k in ("media", "poster"):
                if isinstance(it.get(k), str) and it[k].startswith("/"):
                    d[k] = it[k]
            if isinstance(it.get("link"), str) and it["link"].startswith(SITE):
                d["link"] = it["link"]
            out.append(d)
    return out


def _clip(c) -> dict | None:
    """One {video|image, poster, label} piece of a hero; local paths only."""
    if not isinstance(c, dict):
        return None
    out = {k: c[k] for k in ("video", "image", "poster") if isinstance(c.get(k), str) and c[k].startswith("/")}
    if not (out.get("video") or out.get("image")):
        return None
    if isinstance(c.get("label"), str):
        out["label"] = c["label"].strip()
    return out


def _hero(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    kind = raw.get("kind")
    out = {"kind": kind}
    for k in ("title", "caption"):
        if isinstance(raw.get(k), str):
            out[k] = raw[k].strip()
    if isinstance(raw.get("still"), str) and raw["still"].startswith("/"):
        out["still"] = raw["still"]
    if kind == "vertical":
        parts = {k: _clip(raw.get(k)) for k in ("original", "free", "pro")}
        if not all(parts.values()):
            return None
        out.update(parts)
        return out
    if kind == "media":
        c = _clip(raw)
        return {**out, **c} if c else None
    return None


def _how_one(raw) -> dict | None:
    """One how-to clip: {id, title, free: clip, pro: clip}; at least one of free/pro."""
    if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
        return None
    out = {"id": raw["id"].strip(), "title": str(raw.get("title") or "").strip()}
    for k in ("free", "pro"):
        c = _clip(raw.get(k))
        if c:
            if isinstance((raw.get(k) or {}).get("caption"), str):
                c["caption"] = raw[k]["caption"].strip()
            out[k] = c
    return out if (out.get("free") or out.get("pro")) and out["id"] else None


def _hows(ent: dict) -> list[dict]:
    return [h for h in (_how_one(x) for x in (ent.get("how") or [])) if h] if isinstance(ent, dict) else []


def how(root: Path, how_id: str) -> dict | None:
    """The newest how-to clip with this id, from any version (the feature's own "How it works" link)."""
    pub = _load(root / "whats_new.json")
    for _v, ent in sorted(pub.items(), key=lambda kv: vkey(kv[0]), reverse=True):
        for h in _hows(ent):
            if h["id"] == how_id:
                return h
    return None


def _showcase(path: Path) -> list[dict]:
    try:
        doc = json.loads(path.read_text("utf-8"))
        return _lines(((doc.get("showcase") or {}).get("pro")))[:3]
    except (OSError, ValueError, AttributeError):
        return []


def _load(path: Path) -> dict:
    try:
        doc = json.loads(path.read_text("utf-8"))
        return {k: v for k, v in doc.items() if isinstance(v, dict) and vkey(k)}
    except (OSError, ValueError):
        return {}


def read_seen(state_dir: Path) -> str | None:
    try:
        return str(json.loads((state_dir / "whats_new_seen.json").read_text("utf-8")).get("last_seen") or "") or None
    except (OSError, ValueError, AttributeError):
        return None


def mark_seen(state_dir: Path, version: str) -> bool:
    if not vkey(version):
        return False
    prev = read_seen(state_dir)
    if prev and vkey(prev) > vkey(version):
        return True
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
        tmp = state_dir / "whats_new_seen.json.tmp"
        tmp.write_text(json.dumps({"last_seen": version}), "utf-8")
        tmp.replace(state_dir / "whats_new_seen.json")
        return True
    except OSError:
        return False


def _merge(entries: list[tuple[str, dict]], key: str, extra: dict, pro_user: bool) -> list[dict]:
    seen, out = set(), []
    for ver, ent in sorted(entries, key=lambda e: vkey(e[0]), reverse=True):
        lines = _lines(ent.get(key))
        if key == "pro" and pro_user:
            lines = lines + _lines((extra.get(ver) or {}).get("pro"))
        for ln in lines:
            if ln["text"] not in seen:
                seen.add(ln["text"])
                out.append(ln)
    return out[:MAX_LINES]


def card(root: Path, state_dir: Path, version: str, pro_active: bool, force: bool = False,
         pack_json: Path | None = None) -> dict:
    """The card for this Studio. show=False when there is nothing new or it was already closed."""
    pub = _load(root / "whats_new.json")
    extra = _load(pack_json) if (pack_json and pro_active) else {}
    last = read_seen(state_dir)
    cur = vkey(version)
    if force or not last:
        picked = [(v, e) for v, e in pub.items() if vkey(v) == cur]   # first run / reopened: this version only
    else:
        picked = [(v, e) for v, e in pub.items() if vkey(last) < vkey(v) <= cur]
    if force or not last:
        picked += [(v, {}) for v in extra if vkey(v) == cur and v not in pub]
    free = _merge(picked, "free", extra, pro_active)
    pro = _merge(picked, "pro", extra, pro_active)
    seen_now = bool(last) and vkey(last) >= cur
    newest = sorted(picked, key=lambda e: vkey(e[0]), reverse=True)
    hero = next((h for h in (_hero(e.get("hero")) for _, e in newest) if h), None)
    hows, seen_how = [], set()
    for _, e in newest:
        for h in _hows(e):
            if h["id"] not in seen_how:
                seen_how.add(h["id"])
                hows.append(h)
    shown = {v for v, _ in picked}
    earlier = []
    for v, e in sorted(pub.items(), key=lambda kv: vkey(kv[0]), reverse=True):
        if v in shown or vkey(v) > cur:
            continue
        ent = {"version": v, "date": e.get("date") if isinstance(e.get("date"), str) else None,
               "free": _lines(e.get("free")), "pro": _lines(e.get("pro"))}
        h = _hero(e.get("hero"))
        if h and h.get("still"):
            ent["still"] = h["still"]
        if ent["free"] or ent["pro"]:
            earlier.append(ent)
    date = next((e.get("date") for _, e in newest if isinstance(e.get("date"), str)), None)
    return {
        "version": version, "pro_active": bool(pro_active),
        "show": bool(free or pro) and (force or not seen_now),
        "title": "What's new in %s" % version,
        "free": free, "pro": pro,
        "free_title": FREE_TITLE, "pro_title": PRO_TITLE,
        "thanks": THANKS if pro_active else None,
        "pricing_url": PRICING_URL, "plans_url": PLANS_URL, "changelog_url": CHANGELOG_URL,
        "date": date, "hero": hero, "how": hows,
        "showcase": [] if pro_active else _showcase(root / "whats_new.json"),
        "earlier": earlier,
    }
