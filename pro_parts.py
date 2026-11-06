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
_state = {"busy": False, "error": None, "checked_at": None, "restart_needed": False, "rolled_back": None,
          "phase": None, "last_manual": 0.0}
MANUAL_GAP = 20          # seconds between two manual retries


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


def _read_json(name: str) -> dict | None:
    try:
        d = json.loads((root() / name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not (isinstance(d, dict) and isinstance(d.get("version"), str) and isinstance(d.get("files"), dict)):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,96}", d["version"]):
        return None
    if not re.fullmatch(r"[A-Za-z0-9._+-]{1,120}", str(d.get("dir", d["version"]))):
        return None
    return d


def _record() -> dict | None:
    return _read_json("current.json")


def _previous() -> dict | None:
    return _read_json("previous.json")


def _dir_of(rec: dict) -> Path:
    return root() / str(rec.get("dir") or rec["version"])


def _trusted_record(rec: dict) -> bool:
    """A restored install is trusted only if the signed manifest saved with it still verifies against a
    release key we know and names this version (the local file hashes alone prove nothing about who
    signed them)."""
    try:
        offer = {"key_id": rec.get("key_id"), "manifest": rec.get("manifest"), "signature": rec.get("signature")}
        import pro_token
        pk = _trusted().get(str(offer["key_id"]))
        if not pk:
            return False
        msg = _b64(str(offer["manifest"]))
        if not pro_token.ed25519_verify(_b64(pk), msg, _b64(str(offer["signature"]))):
            return False
        m = json.loads(msg.decode("utf-8"))
        return isinstance(m, dict) and m.get("version") == rec["version"] and m.get("component") == "panel"
    except (ValueError, TypeError, KeyError):
        return False


def _verified(rec: dict | None) -> str | None:
    if not rec:
        return None
    d = _dir_of(rec)
    want = {p + SO for p in PARTS}
    if set(rec["files"]) != want or not _trusted_record(rec):
        return None
    for name, sha in rec["files"].items():
        p = d / name
        if p.is_symlink() or not p.is_file() or _sha(p) != sha:
            return None
    return str(d)


def installed() -> str | None:
    """The verified parts folder, or None."""
    return _verified(_record())


def rollback() -> str | None:
    """The current install failed a check: switch back to the verified previous version (atomic
    pointer swap). Returns the folder now current, or None when there is nothing to go back to."""
    prev = _previous()
    d = _verified(prev)
    if not d:
        return None
    cur = root() / "current.json"
    part = root() / (".current.%s.tmp" % os.urandom(4).hex())
    try:
        part.write_text(json.dumps(prev, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(part, cur)
    except OSError:
        return None
    with _lock:
        _state["rolled_back"] = prev["version"]
    return d


def activate() -> str | None:
    """Called before `import pro_remote`. The Mac app's studio IS the Pro pack: nothing to do."""
    if _dist.is_mac_app():
        return None
    try:
        d = installed()
        if not d and _record() is not None:
            d = rollback()          # the current one no longer verifies: use the last good one
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
        tmp = root() / (".fetch-%d-%s" % (os.getpid(), os.urandom(4).hex()))
        tmp.mkdir()
        _phase("downloading")
        pack = tmp / "pack.tar.gz"
        _download(str(offer.get("url", "")), pack, int(m["bytes"]), lic["key"], opener)
        _phase("verifying")
        if _sha(pack) != m["sha256"]:
            raise ValueError("hash")
        out = tmp / "parts"
        out.mkdir()
        files = _extract(pack, int(m["strip_components"]), out)
        # A unique folder per install attempt: the version that is running is never touched.
        dest_name = "%s.%s" % (m["version"], os.urandom(3).hex())
        dest = root() / dest_name
        if not _probe(out):                       # a correctly signed package that cannot load here is refused
            raise ValueError("incompatible")
        os.replace(out, dest)
        doc = {"version": m["version"], "dir": dest_name, "files": files, "key_id": offer["key_id"],
               "manifest": offer["manifest"], "signature": offer["signature"],
               "installed_at": int(time.time())}
        old = _record()
        old_ok = _verified(old) if old else None
        cur = root() / "current.json"
        part = root() / (".current.%s.tmp" % os.urandom(4).hex())
        try:
            part.write_text(json.dumps(doc, indent=1, sort_keys=True), encoding="utf-8")
            if old_ok:                              # keep one verified previous version to roll back to
                pj = root() / (".previous.%s.tmp" % os.urandom(4).hex())
                pj.write_text(json.dumps(old, indent=1, sort_keys=True), encoding="utf-8")
                os.replace(pj, root() / "previous.json")
            os.replace(part, cur)                   # the atomic switch
        except OSError:
            shutil.rmtree(dest, ignore_errors=True)
            raise
        _prune(keep={dest_name})
        return _done({"ok": True, "version": m["version"], "changed": True})
    except (ValueError, KeyError, TypeError, OSError, tarfile.TarError) as e:
        return _done({"ok": False, "error": (e.args[0] if isinstance(e, ValueError) and e.args else type(e).__name__)[:60]})
    finally:
        if tmp is not None:
            shutil.rmtree(tmp, ignore_errors=True)
        with _lock:
            _state["busy"] = False


def _phase(name) -> None:
    with _lock:
        _state["phase"] = name


def _probe(parts_dir: Path) -> bool:
    """Would these modules import on this Studio? Tried in a throw-away interpreter with ONLY this
    folder in front, so a bad build is refused before it replaces anything."""
    import subprocess
    if not PROBE:
        return True
    code = "import sys; sys.path[:0] = [sys.argv[1], sys.argv[2]]; import pro_remote_crypto, pro_remote, pro_relay_grant"
    try:
        r = subprocess.run([sys.executable, "-I", "-c", code, str(parts_dir), str(Path(__file__).resolve().parent)],
                           capture_output=True, timeout=60, cwd=str(parts_dir),
                           env={"PATH": "/usr/bin:/bin", "PHOSPHENE_PROBE": "1"})
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


PROBE = True


def _prune(keep: set) -> None:
    """Remove install folders that are neither current nor the kept previous."""
    prev = _previous()
    if prev:
        keep.add(str(prev.get("dir") or prev["version"]))
    try:
        for p in root().iterdir():
            if p.is_dir() and not p.name.startswith(".") and p.name not in keep:
                shutil.rmtree(p, ignore_errors=True)
    except OSError:
        pass


def _done(r: dict) -> dict:
    with _lock:
        _state["error"] = None if r.get("ok") else r.get("error")
        _state["checked_at"] = int(time.time())
        _state["phase"] = None
    return r


PLAIN = {
    "no_licence": "Remote Control needs your Pro licence key in this Studio. Paste it in Settings > Pro.",
    "unreachable": "The licence server could not be reached. Check the internet connection.",
    "busy": "Already working on it.",
    "bad_signature": "The downloaded Pro components did not pass the signature check, so they were not installed. Try again; if it repeats, contact support.",
    "hash": "The download was damaged (its checksum did not match). Nothing was installed. Try again.",
    "size": "The download was cut short or the wrong size. Nothing was installed. Try again.",
    "incompatible": "These Pro components cannot run on this Studio yet. Your previous version stays in use. Update the Studio or try again later.",
    "studio_too_old": "These Pro components need a newer Studio. Update Phosphene in Pinokio, then try again.",
    "pack_without_remote": "The Pro download did not contain Remote Control. Contact support.",
    "bad_url": "The download address was refused for safety. Contact support.",
    "OSError": "The components could not be saved (is the disk full?). Free some space and try again.",
    "TarError": "The download could not be unpacked. Try again.",
    "licence_not_active": "Your licence is not active, so the components were not offered. Check Settings > Pro.",
}


def explain(code) -> str | None:
    if not code:
        return None
    return PLAIN.get(str(code), "Remote Control components could not be installed (%s). Try again, or contact support." % str(code)[:40])


def status() -> dict:
    rec = _record()
    with _lock:
        st = dict(_state)
    ok = bool(rec and installed())
    return {"installed": rec["version"] if ok else None, "busy": st["busy"], "phase": st["phase"],
            "error": st["error"], "message": explain(st["error"]), "checked_at": st["checked_at"],
            "restart_needed": st["restart_needed"], "rolled_back": st["rolled_back"]}


def retry_now() -> dict:
    """Settings > Remote 'Try again': one background attempt, rate limited, never stacks up."""
    if _dist.is_mac_app():
        return {"ok": False, "error": "mac_app"}
    if not _licence():
        return {"ok": False, "error": "no_licence", "message": explain("no_licence")}
    with _lock:
        if _state["busy"]:
            return {"ok": True, "started": False, "message": "Already working on it."}
        now = time.time()
        if now - _state["last_manual"] < MANUAL_GAP:
            return {"ok": True, "started": False, "message": "Just tried; give it a few seconds."}
        _state["last_manual"] = now
        _state["phase"] = "checking"
    threading.Thread(target=_manual_run, name="pro-parts-retry", daemon=True).start()
    return {"ok": True, "started": True}


_P = [None]


def _manual_run() -> None:
    r = fetch()
    if r.get("ok") and _P[0] is not None:
        _bring_up(_P[0])


def _bring_up(P) -> None:
    """Newly installed parts start without a Studio restart (routes look P.pro_remote up live)."""
    if getattr(P, "pro_remote", None) is not None:
        with _lock:
            if _state.get("changed_since_load"):
                _state["restart_needed"] = True        # new components are on disk; the running ones stay until restart
        return
    if activate() is None:
        return
    try:
        import importlib
        mod = importlib.import_module("pro_remote")
    except Exception as exc:                                        # noqa: BLE001
        print(f"[remote] Pro parts did not load: {type(exc).__name__}", flush=True)
        back = rollback()                                           # the next start uses the last good version
        with _lock:
            _state["error"] = "incompatible"
            _state["restart_needed"] = bool(back)
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

    _P[0] = P

    def loop():
        n = 0
        while rounds is None or n < rounds:
            n += 1
            r = fetch() if _licence() else None
            if r and r.get("ok"):
                if r.get("changed") and getattr(P, "pro_remote", None) is not None:
                    with _lock:
                        _state["restart_needed"] = True
                _bring_up(P)
            sleep(_next_wait(r))
    if rounds is not None:
        loop()
        return True
    threading.Thread(target=loop, name="pro-parts", daemon=True).start()
    return True
