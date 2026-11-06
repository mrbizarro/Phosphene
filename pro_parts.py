"""Pro parts for a Studio without the Mac app (Pinokio): Remote Control's Mac side.

The Mac app installs the whole signed Pro pack as its studio, so `import pro_remote` finds the
compiled module there. A Pinokio Studio is the public tree from GitHub and has no Pro pack. With
its own licence (Settings > Pro, pro_licence.py) it takes ONLY the Remote modules out of the same
signed pack, from the same server, with the same checks as the app's ProInstaller:

  1. POST <server>/v1/pro/latest  license_key, instance_id, device_id, app_version
     -> base64 manifest, Ed25519 signature, key id, a short-lived download URL
  2. the manifest signature is checked against the release key compiled in here (pro-2026-10)
  3. the download (header X-Phosphene-Licence) must have the manifest's exact size and sha256
  4. only the allow-listed compiled modules are read out of it (regular files at the top of the
     tree, bounded size); nothing else of the pack is written anywhere
  5. state/pro_parts/<version>/ + current.json (the signed manifest, each part's sha256)

At start, activate() puts that folder on sys.path only when every part still matches the sha256
recorded at install. The modules check Pro themselves on every request (a signed, unexpired
token for this Mac), so installed parts with a lapsed licence do nothing; a refund removes the
token, not the files. The server is ours or a loopback test server (PHOSPHENE_LICENSE_SERVER).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import sys
import tarfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import distribution as _dist

SO = ".cpython-311-darwin.so"
PARTS = ("pro_remote", "pro_remote_crypto", "pro_relay_grant")
TRUSTED = {"pro-2026-10": "K/27SibA0PprIa5SZ6W8ITdYs8LExFerEUeiJRMB2qg="}   # the app's ProKeys
MAX_PART = 32 << 20
MAX_PACK = 1 << 30
CHECK_EVERY = 24 * 3600
TIMEOUT = 30
_lock = threading.Lock()
_state = {"busy": False, "error": None, "checked_at": None}


def _server() -> str:
    try:
        import pro_licence
        return pro_licence._base()
    except Exception:                                               # noqa: BLE001
        return "https://phosphene.video/api"


def _trusted() -> dict:
    keys = dict(TRUSTED)
    # A loopback test server may sign with a test key; never the real server.
    dev = (os.environ.get("PHOSPHENE_PRO_TEST_KEY") or "").strip()
    host = urllib.parse.urlparse(_server()).hostname
    if dev and host in ("127.0.0.1", "localhost", "::1") and re.fullmatch(r"[A-Za-z0-9+/]{43}=", dev):
        keys["test"] = dev
    return keys


def state_dir() -> Path:
    raw = (os.environ.get("LTX_STATE_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent / "state"


def root() -> Path:
    return state_dir() / "pro_parts"


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _record() -> dict | None:
    try:
        d = json.loads((root() / "current.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and isinstance(d.get("version"), str) and isinstance(d.get("files"), dict)):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,96}", d["version"]):
        return None
    return d


def installed() -> str | None:
    """The verified parts folder, or None."""
    rec = _record()
    if not rec:
        return None
    d = root() / rec["version"]
    want = {p + SO for p in PARTS}
    if set(rec["files"]) != want:
        return None
    for name, sha in rec["files"].items():
        p = d / name
        if p.is_symlink() or not p.is_file() or _sha(p) != sha:
            return None
    return str(d)


def activate() -> str | None:
    """Called before `import pro_remote`. The Mac app's studio IS the Pro pack: nothing to do."""
    if _dist.is_mac_app():
        return None
    try:
        d = installed()
    except OSError:
        return None
    if d and d not in sys.path:
        sys.path.insert(0, d)
    return d


def _licence() -> dict | None:
    try:
        import pro_licence
        rec = pro_licence.load()
    except Exception:                                               # noqa: BLE001
        return None
    if not isinstance(rec, dict) or rec.get("status") not in (None, "active"):
        return None
    key, iid, dev = rec.get("key"), rec.get("instance_id"), rec.get("device")
    if not all(isinstance(v, str) and v for v in (key, iid, dev)):
        return None
    return {"key": key, "instance_id": iid, "device": dev}


def _version() -> str:
    try:
        return (Path(__file__).resolve().parent / "VERSION").read_text().strip() or "0"
    except OSError:
        return "0"


def _vt(v: str) -> tuple:
    return tuple(int(x) for x in re.findall(r"\d+", v.split("-")[0])[:3])


def _b64(s: str) -> bytes:
    return base64.b64decode(s, validate=True)


def _verify(offer: dict) -> tuple[dict, bytes]:
    import pro_token
    pk = _trusted().get(str(offer.get("key_id", "")))
    if not pk:
        raise ValueError("unknown_key")
    msg = _b64(str(offer.get("manifest", "")))
    if not pro_token.ed25519_verify(_b64(pk), msg, _b64(str(offer.get("signature", "")))):
        raise ValueError("bad_signature")
    m = json.loads(msg.decode("utf-8"))
    if not (isinstance(m, dict) and m.get("schema") == 1 and m.get("channel") == "pro" and m.get("component") == "panel"):
        raise ValueError("bad_manifest")
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,96}", str(m.get("version", ""))) \
            or not re.fullmatch(r"[A-Za-z0-9._+-]{1,160}", str(m.get("object", ""))) \
            or not re.fullmatch(r"[0-9a-f]{64}", str(m.get("sha256", ""))) \
            or not 0 < int(m.get("bytes", 0)) <= MAX_PACK or not 0 <= int(m.get("strip_components", -1)) <= 3:
        raise ValueError("bad_manifest")
    if _vt(str(m.get("min_app", "0"))) > _vt(_version()):
        raise ValueError("studio_too_old")
    return m, msg


def _download(url: str, dest: Path, size: int, licence: str, opener) -> None:
    u = urllib.parse.urlparse(url)
    base = urllib.parse.urlparse(_server())
    if u.scheme != base.scheme or u.hostname != base.hostname:
        raise ValueError("bad_url")           # the licence header only ever goes to our server
    req = urllib.request.Request(url, headers={"X-Phosphene-Licence": licence, "User-Agent": "Phosphene-Studio"})
    n = 0
    with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r, open(dest, "wb") as f:
        while True:
            b = r.read(1 << 20)
            if not b:
                break
            n += len(b)
            if n > size:
                raise ValueError("size")
            f.write(b)
    if n != size:
        raise ValueError("size")


def _extract(pack: Path, strip: int, out: Path) -> dict:
    want = {p + SO for p in PARTS}
    got = {}
    with tarfile.open(pack, "r:gz") as t:
        for mem in t:
            parts = [x for x in mem.name.split("/") if x not in ("", ".")]
            if len(parts) != strip + 1 or parts[-1] not in want:
                continue
            if not mem.isreg() or mem.size > MAX_PART or parts[-1] in got:
                raise ValueError("bad_part")
            src = t.extractfile(mem)
            data = src.read(MAX_PART + 1)
            if len(data) != mem.size:
                raise ValueError("bad_part")
            fd = os.open(out / parts[-1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            got[parts[-1]] = hashlib.sha256(data).hexdigest()
    if set(got) != want:
        raise ValueError("pack_without_remote")
    return got


def fetch(opener=None) -> dict:
    """Install or update the parts. {ok, version | error}. Never raises."""
    if _dist.is_mac_app():
        return {"ok": False, "error": "mac_app"}
    lic = _licence()
    if not lic:
        return {"ok": False, "error": "no_licence"}
    with _lock:
        if _state["busy"]:
            return {"ok": False, "error": "busy"}
        _state["busy"] = True
    tmp = None
    try:
        form = urllib.parse.urlencode({"license_key": lic["key"], "instance_id": lic["instance_id"],
                                       "device_id": lic["device"], "app_version": _version()}).encode()
        req = urllib.request.Request(_server() + "/v1/pro/latest", data=form, method="POST", headers={
            "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
            "User-Agent": "Phosphene-Studio"})
        try:
            with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
                offer = json.loads(r.read(1 << 16).decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                why = json.loads(e.read(1 << 12).decode("utf-8")).get("error") or "refused"
            except (ValueError, AttributeError):
                why = "refused"
            return _done({"ok": False, "error": str(why)[:60]})
        except (urllib.error.URLError, OSError, ValueError):
            return _done({"ok": False, "error": "unreachable"})
        m, msg = _verify(offer)
        rec = _record()
        if rec and rec["version"] == m["version"] and installed():
            return _done({"ok": True, "version": m["version"], "changed": False})
        root().mkdir(parents=True, exist_ok=True)
        tmp = root() / (".fetch-%d" % os.getpid())
        shutil.rmtree(tmp, ignore_errors=True)
        tmp.mkdir()
        pack = tmp / "pack.tar.gz"
        _download(str(offer.get("url", "")), pack, int(m["bytes"]), lic["key"], opener)
        if _sha(pack) != m["sha256"]:
            raise ValueError("hash")
        out = tmp / "parts"
        out.mkdir()
        files = _extract(pack, int(m["strip_components"]), out)
        dest = root() / m["version"]
        if dest.exists():
            shutil.rmtree(dest)
        os.replace(out, dest)
        doc = {"version": m["version"], "files": files, "key_id": offer["key_id"],
               "manifest": offer["manifest"], "signature": offer["signature"],
               "installed_at": int(time.time())}
        cur = root() / "current.json"
        part = root() / ".current.json.tmp"
        part.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
        old = _record()
        os.replace(part, cur)
        if old and old["version"] != m["version"]:
            shutil.rmtree(root() / old["version"], ignore_errors=True)
        return _done({"ok": True, "version": m["version"], "changed": True})
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError) as e:
        return _done({"ok": False, "error": (e.args[0] if isinstance(e, ValueError) and e.args else type(e).__name__)[:60]})
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        with _lock:
            _state["busy"] = False


def _done(r: dict) -> dict:
    with _lock:
        _state["error"] = None if r.get("ok") else r.get("error")
        _state["checked_at"] = int(time.time())
    return r


def status() -> dict:
    rec = _record()
    with _lock:
        return {"installed": rec["version"] if rec and installed() else None, "busy": _state["busy"],
                "error": _state["error"], "checked_at": _state["checked_at"]}


def _bring_up(P) -> None:
    """Newly installed parts start without a Studio restart (routes look P.pro_remote up live)."""
    if getattr(P, "pro_remote", None) is not None:
        return
    if activate() is None:
        return
    try:
        import importlib
        mod = importlib.import_module("pro_remote")
    except Exception as exc:                                        # noqa: BLE001
        print(f"[remote] Pro parts did not load: {type(exc).__name__}", flush=True)
        return
    P.pro_remote = mod
    mod.boot(P)


IDLE_POLL = 30          # no licence yet: a licence pasted in Settings > Pro is noticed this fast
RETRY = 15 * 60         # the server refused or was unreachable (it allows a few offers a day)


def _next_wait(r: dict | None) -> int:
    if r is None:
        return IDLE_POLL
    return CHECK_EVERY if r.get("ok") else RETRY


def start(P, sleep=time.sleep, rounds=None) -> bool:
    """Background: fetch as soon as the Studio has a licence (at start, or within IDLE_POLL of
    activating one), then once a day; retry a failure after RETRY."""
    if _dist.is_mac_app():
        return False

    def loop():
        n = 0
        while rounds is None or n < rounds:
            n += 1
            r = fetch() if _licence() else None
            if r and r.get("ok"):
                _bring_up(P)
            sleep(_next_wait(r))
    if rounds is not None:
        loop()
        return True
    threading.Thread(target=loop, name="pro-parts", daemon=True).start()
    return True
