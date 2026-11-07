"""Signed notice for Pinokio installs ("Notify everyone", 5.0.4).

The Mac app reads the same file natively (app/Sources/SignedNotice.swift). Here is the Pinokio side: the
studio fetches https://phosphene.video/app/notice.json and notice.json.sig every 10 minutes and at start,
checks the Ed25519 signature (the Sparkle key, the public half below; verify-only code from pro_token) and
serves the one verified notice at GET /notice. TEXT THAT IS NOT SIGNED IS NEVER SERVED. The page shows each
notice id once (dismissal is remembered in the browser). min_version / max_version are compared with this
studio's own version; expires_at is honoured.

    {"id":"n-20261107-1","text":"...","action":"update","link":"https://phosphene.video/...",
     "min_version":"5.0.0","max_version":"5.0.3","expires_at":"2026-11-14T00:00:00Z"}
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import re
import threading
import time
import urllib.request

import pro_token

# SUPublicEDKey of the Mac app (app/Support/Info.plist): the Sparkle EdDSA public key.
SPARKLE_PUBLIC_KEY_B64 = "hukjR4xWNJ/YsAv03YecfKeDenK4Voh6pr7244sB8Zo="
NOTICE_URL = "https://phosphene.video/app/notice.json"
MAX_BYTES = 4096
INTERVAL_SEC = 600

_lock = threading.Lock()
_current: dict | None = None
_last = "not checked"


def _parts(v):
    p = str(v).split(".")
    if not 1 <= len(p) <= 4 or not all(x.isdigit() for x in p):
        return None
    return [int(x) for x in p]


def _cmp(a, b):
    n = max(len(a), len(b))
    a, b = a + [0] * (n - len(a)), b + [0] * (n - len(b))
    return (a > b) - (a < b)


def accept(body: bytes, sig_text: bytes, version: str, public_b64: str = SPARKLE_PUBLIC_KEY_B64, now: float | None = None):
    """The notice dict for this studio, or None (bad signature, malformed, outside the window, expired)."""
    try:
        if len(body) > MAX_BYTES:
            return None
        sig = base64.b64decode(sig_text.strip(), validate=True)
        if not pro_token.ed25519_verify(base64.b64decode(public_b64), body, sig):
            return None
        o = json.loads(body)
        if not isinstance(o, dict):
            return None
        nid, text = o.get("id"), o.get("text")
        if not (isinstance(nid, str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", nid)):
            return None
        if not (isinstance(text, str) and 1 <= len(text.strip()) <= 400) or re.search(r"[\x00-\x1f\x7f]", text):
            return None
        app = _parts(version)
        if app is None:
            return None
        if "min_version" in o:
            lo = _parts(o["min_version"])
            if lo is None or _cmp(app, lo) < 0:
                return None
        if "max_version" in o:
            hi = _parts(o["max_version"])
            if hi is None or _cmp(app, hi) > 0:
                return None
        if "expires_at" in o:
            try:
                exp = datetime.datetime.fromisoformat(str(o["expires_at"]).replace("Z", "+00:00")).timestamp()
            except ValueError:
                return None
            if (now if now is not None else time.time()) >= exp:
                return None
        action = o.get("action", "update")
        if action not in ("update", "link", "none"):
            return None
        link = o.get("link")
        if link is not None:
            m = re.fullmatch(r"https://([a-z0-9.-]+)(/\S*)?", str(link))
            if not m or not (m.group(1) == "phosphene.video" or m.group(1).endswith(".phosphene.video") or m.group(1) == "github.com"):
                return None
        if action == "link" and not link:
            action = "none"
        return {"id": nid, "text": text.strip(), "action": action, "link": link}
    except Exception:
        return None


def current() -> dict | None:
    with _lock:
        return dict(_current) if _current else None


def last_outcome() -> str:
    with _lock:
        return _last


def refresh(version: str, url: str | None = None) -> None:
    global _current, _last
    url = url or os.environ.get("PHOSPHENE_NOTICE_URL") or NOTICE_URL

    def get(u, limit):
        req = urllib.request.Request(u, headers={"User-Agent": f"PhospheneStudio/{version}"})
        with urllib.request.urlopen(req, timeout=8) as r:
            return r.read(limit + 1)

    try:
        body, sig = get(url, MAX_BYTES), get(url + ".sig", 1024)
    except Exception:
        with _lock:
            _last = "no notice file"
        return
    n = accept(body, sig, version)
    with _lock:
        _current = n
        _last = f"shown {n['id']}" if n else "rejected (unsigned, outside the version window, or expired)"


def loop(version_fn) -> None:
    time.sleep(8)
    while True:
        try:
            refresh(version_fn())
        except Exception:
            pass
        time.sleep(INTERVAL_SEC)
