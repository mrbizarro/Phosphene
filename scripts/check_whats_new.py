#!/usr/bin/env python3
"""Release gate: whats_new.json must carry an entry for the version in VERSION.

  python3 scripts/check_whats_new.py [--version X] [--pack path/whats_new_pro.json]

FAIL (exit 1) when: the entry for the version is missing or has no line at all (a quick-fix release
may have one line); a line is empty or longer than 200 characters; a line contains an em dash, or
a trick / engine name (the card is public marketing copy that Free users read too); a line hits
the private leak patterns (scripts/public_leak_patterns.txt, when this private tree has it).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRICKS = re.compile(r"coarse|distill|adapter|\bsteps?\b|lora|\bmlx\b|checkpoint|flex turbo|\bh3 (fast|faster)\b|pro_engine|\bpin\b|ltx25",
                    re.I)


def _lines(ent):
    for key in ("free", "pro"):
        for it in ent.get(key) or []:
            yield key, (it if isinstance(it, str) else str((it or {}).get("text") or ""))


def _patterns():
    pf = ROOT / "scripts" / "public_leak_patterns.txt"
    out = []
    if not pf.exists():
        return out
    for ln in pf.read_text("utf-8").splitlines():
        parts = ln.split("\t")
        if ln.startswith("#") or len(parts) < 3 or parts[0] not in ("0", "1"):
            continue
        try:
            out.append((parts[1], re.compile(parts[2], re.I if parts[0] == "1" else 0)))
        except re.error:
            pass
    return out


def check(version: str, doc: dict, pack: dict | None = None) -> list[str]:
    errs = []
    ent = doc.get(version)
    if not isinstance(ent, dict):
        return ["whats_new.json has no entry for %s (add free[] and pro[] lines, one line is enough for a quick fix)" % version]
    pats = _patterns()
    sets = [("whats_new.json", ent)]
    if pack is not None and isinstance(pack.get(version), dict):
        sets.append(("pack whats_new_pro.json", pack[version]))
    hows = [h for h in (ent.get("how") or []) if isinstance(h, dict)]   # how-to clips: public copy too
    if hows:
        sets.append(("whats_new.json", {"free": [h.get("title") or "x" for h in hows]
                     + [(h.get(k) or {}).get("caption") or "x" for h in hows for k in ("free", "pro") if h.get(k)]}))
    if isinstance(doc.get("showcase"), dict):   # the standing "What Pro adds" items: public copy too
        sets.append(("whats_new.json", {"pro": doc["showcase"].get("pro") or []}))
    n = 0
    for src, e in sets:
        for key, text in _lines(e):
            n += 1
            t = text.strip()
            if not t:
                errs.append("%s: empty line in %s[]" % (src, key))
            if len(t) > 200:
                errs.append("%s: line over 200 characters: %r" % (src, t[:40]))
            if "—" in t or "–" in t:
                errs.append("%s: dash in line (STYLE.md): %r" % (src, t[:40]))
            # the Pack file is private, so only the public file is held to the no-trick-name rule
            if src == "whats_new.json":
                m = TRICKS.search(t)
                if m:
                    errs.append("%s: trick/engine wording %r in %r" % (src, m.group(0), t[:40]))
                for label, rx in pats:
                    if rx.search(t):
                        errs.append("%s: leak pattern '%s' in %r" % (src, label, t[:40]))
    if n == 0:
        errs.append("whats_new.json entry %s has no lines" % version)
    return errs


def main(argv: list[str]) -> int:
    version = (ROOT / "VERSION").read_text("utf-8").strip()
    pack_path = None
    it = iter(argv)
    for a in it:
        if a == "--version":
            version = next(it)
        elif a == "--pack":
            pack_path = Path(next(it))
    try:
        doc = json.loads((ROOT / "whats_new.json").read_text("utf-8"))
    except (OSError, ValueError) as exc:
        print("FAIL: whats_new.json unreadable: %s" % exc)
        return 1
    pack = None
    if pack_path:
        try:
            pack = json.loads(pack_path.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            print("FAIL: pack file unreadable: %s" % exc)
            return 1
    errs = check(version, doc, pack)
    for e in errs:
        print("FAIL:", e)
    if not errs:
        print("PASS: whats_new.json has a clean entry for %s" % version)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
