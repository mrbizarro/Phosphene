"""What is the newest Phosphene, for the Mac app's header pill.

The Mac app knows best what is behind and pushes it to the page (window.phosAppUpdate). This is the
studio's own second source, for apps that do not push (5.0.4 and older): it reads two public
files, at most once every ten minutes, in the background, and never blocks a request:

  * https://phosphene.video/app/channel.json  - the free Studio tag ("v5.0.7")
  * https://phosphene.video/api/health        - the Pro pack release ("5.0.7-pro.<hash>")

Both are only a HINT for a label. Nothing is installed from them: the app installs only
what its own signed manifests say. Any failure keeps the last answer (or says nothing).
"""
import json
import threading
import time
import urllib.request

TTL = 600.0
CHANNEL_URL = "https://phosphene.video/app/channel.json"
HEALTH_URL = "https://phosphene.video/api/health"

_lock = threading.Lock()
_state = {"ts": 0.0, "free": None, "pro": None, "busy": False}


def base(v) -> str:
    """'v5.0.7' / '5.0.7-pro.abc' -> '5.0.7'."""
    s = str(v or "").strip()
    if s[:1] in ("v", "V"):
        s = s[1:]
    out = ""
    for ch in s:
        if ch.isdigit() or ch == ".":
            out += ch
        else:
            break
    return out.strip(".")


def _key(v):
    try:
        return tuple(int(x) for x in base(v).split(".") if x != "")
    except ValueError:
        return ()


def newer(a, b) -> bool:
    ka, kb = _key(a), _key(b)
    n = max(len(ka), len(kb))
    ka += (0,) * (n - len(ka))
    kb += (0,) * (n - len(kb))
    return bool(ka) and ka > kb


def _get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "PhospheneStudio/label"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = r.read(200_000)
    return json.loads(data.decode("utf-8", "replace"))


def _refresh():
    free = pro = None
    try:
        free = base(_get_json(CHANNEL_URL).get("tag")) or None
    except Exception:
        pass
    try:
        pro = base(_get_json(HEALTH_URL).get("release")) or None
    except Exception:
        pass
    with _lock:
        _state["ts"] = time.time()
        _state["busy"] = False
        if free:
            _state["free"] = free
        if pro:
            _state["pro"] = pro


def info(local_version, edition, now=None) -> dict:
    """{latest, current, behind}; starts a background refresh when the answer is older than TTL."""
    now = time.time() if now is None else now
    with _lock:
        stale = (now - _state["ts"]) > TTL and not _state["busy"]
        if stale:
            _state["busy"] = True
        latest = (_state["pro"] if edition == "pro" else _state["free"]) or ""
    if stale:
        threading.Thread(target=_refresh, daemon=True, name="app-latest").start()
    cur = base(local_version)
    return {"latest": latest, "current": cur, "behind": bool(latest and cur and newer(latest, cur))}
