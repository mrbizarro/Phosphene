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

One fixed format (5.0.20): every release reads header (version, date, "headline"), optional hero, New for everyone,
New in Pro, How it works. Each piece of media carries its real "w"/"h", read here from its poster or still, so the
window sizes every frame to the clip before the clip loads: no empty boxes, no jump, tall and wide both right.
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
            wh = (_dims(d.get("poster")) or _dims(d.get("media"))) if d.get("media") else None
            if wh:
                d["w"], d["h"] = wh
            out.append(d)
    return out


ROOT = Path(__file__).resolve().parent
_DIMS: dict[str, tuple[int, int] | None] = {}


def _image_dims(path: Path) -> tuple[int, int] | None:
    """Width and height of a JPEG, PNG or WebP from its header (no imaging library in the panel's import path)."""
    try:
        with path.open("rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return int.from_bytes(head[16:20], "big"), int.from_bytes(head[20:24], "big")
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                kind = head[12:16]
                if kind == b"VP8X":
                    return 1 + int.from_bytes(head[24:27], "little"), 1 + int.from_bytes(head[27:30], "little")
                if kind == b"VP8 ":
                    f.seek(26)
                    b = f.read(4)
                    return int.from_bytes(b[0:2], "little") & 0x3FFF, int.from_bytes(b[2:4], "little") & 0x3FFF
                if kind == b"VP8L":
                    b = head[21:25]
                    bits = int.from_bytes(b, "little")
                    return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
                return None
            if head[:2] != b"\xff\xd8":
                return None
            f.seek(2)
            while True:
                m = f.read(2)
                if len(m) < 2 or m[0] != 0xFF:
                    return None
                if m[1] in (0xD8, 0x01) or 0xD0 <= m[1] <= 0xD7:
                    continue
                ln = int.from_bytes(f.read(2), "big")
                if m[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    b = f.read(5)
                    return int.from_bytes(b[3:5], "big"), int.from_bytes(b[1:3], "big")
                f.seek(ln - 2, 1)
    except (OSError, ValueError):
        return None


def _dims(url: str | None) -> tuple[int, int] | None:
    """"/assets/whats_new/x.jpg" or "/webapp/..." -> (w, h), cached; None for anything else."""
    if not isinstance(url, str) or not url.startswith(("/assets/", "/webapp/")):
        return None
    if url not in _DIMS:
        p = (ROOT / url.lstrip("/")).resolve()
        _DIMS[url] = _image_dims(p) if ROOT in p.parents else None
    return _DIMS[url]


def _sized(out: dict) -> dict:
    d = _dims(out.get("poster")) or _dims(out.get("image"))
    if d and d[0] > 0 and d[1] > 0:
        out["w"], out["h"] = d
    return out


def _clip(c) -> dict | None:
    """One {video|image, poster, label} piece of a hero; local paths only, with its real w/h when known."""
    if not isinstance(c, dict):
        return None
    out = {k: c[k] for k in ("video", "image", "poster") if isinstance(c.get(k), str) and c[k].startswith("/")}
    if not (out.get("video") or out.get("image")):
        return None
    if isinstance(c.get("label"), str):
        out["label"] = c["label"].strip()
    return _sized(out)


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


def _headline(ent: dict) -> str | None:
    h = ent.get("headline") if isinstance(ent, dict) else None
    return h.strip() if isinstance(h, str) and h.strip() else None


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
    # A how-to clip shows once: in the current view when it is there, otherwise in the oldest earlier release that
    # carries it (the one that introduced the feature), never repeated down the list.
    how_home: dict[str, str] = {}
    for v, e in sorted(pub.items(), key=lambda kv: vkey(kv[0])):
        if v not in shown and vkey(v) <= cur:
            for h in _hows(e):
                if h["id"] not in seen_how:
                    how_home.setdefault(h["id"], v)
    earlier = []
    for v, e in sorted(pub.items(), key=lambda kv: vkey(kv[0]), reverse=True):
        if v in shown or vkey(v) > cur:
            continue
        ent = {"version": v, "date": e.get("date") if isinstance(e.get("date"), str) else None,
               "headline": _headline(e), "free": _lines(e.get("free")), "pro": _lines(e.get("pro")),
               "how": [h for h in _hows(e) if how_home.get(h["id"]) == v]}
        h = _hero(e.get("hero"))
        if h and h.get("still"):
            ent["still"] = h["still"]
            d = _dims(h["still"])
            if d:
                ent["still_w"], ent["still_h"] = d
        if ent["free"] or ent["pro"]:
            earlier.append(ent)
    date = next((e.get("date") for _, e in newest if isinstance(e.get("date"), str)), None)
    headline = next((hl for hl in (_headline(e) for _, e in newest) if hl), None)
    return {
        "version": version, "pro_active": bool(pro_active),
        "show": bool(free or pro) and (force or not seen_now),
        "title": "What's new in %s" % version,
        "free": free, "pro": pro,
        "free_title": FREE_TITLE, "pro_title": PRO_TITLE,
        "thanks": THANKS if pro_active else None,
        "pricing_url": PRICING_URL, "plans_url": PLANS_URL, "changelog_url": CHANGELOG_URL,
        "date": date, "headline": headline, "hero": hero, "how": hows,
        "showcase": [] if pro_active else _showcase(root / "whats_new.json"),
        "earlier": earlier,
    }
