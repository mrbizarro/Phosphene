"""The "What's new" card: what a user got when Phosphene updated.

whats_new.json (shipped in the Studio, public) holds per version {free: [...], pro: [...]}.
The Pro pack may carry whats_new_pro.json beside its engine (same shape, pro[] only); those extra
lines are shown to Pro users only, so Pro-only detail never sits in the public repo. Free users see
the public Pro list too (public marketing copy: plain benefit wording, no trick or engine names).

Shown once per version: state/whats_new_seen.json remembers the last version the user closed.
If versions were skipped, the card combines every entry after the last seen one, newest first.
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


def vkey(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", str(v or ""))[:4])


def _lines(raw) -> list[dict]:
    out = []
    for it in raw or []:
        if isinstance(it, str) and it.strip():
            out.append({"text": it.strip()})
        elif isinstance(it, dict) and str(it.get("text") or "").strip():
            d = {"text": str(it["text"]).strip()}
            if isinstance(it.get("media"), str) and it["media"].startswith("/"):
                d["media"] = it["media"]
            out.append(d)
    return out


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
    return {
        "version": version, "pro_active": bool(pro_active),
        "show": bool(free or pro) and (force or not seen_now),
        "title": "What's new in %s" % version,
        "free": free, "pro": pro,
        "free_title": FREE_TITLE, "pro_title": PRO_TITLE,
        "thanks": THANKS if pro_active else None,
        "pricing_url": PRICING_URL,
    }
