"""The Studio's own Phosphene Pro licence: for an install that has no Mac app (Pinokio).

The Mac app activates a licence key with the Phosphene licence server and keeps the signed
capability token the server answers with (see pro_token.py for what that token is). A Pinokio
install has no app, so it did that nowhere and Pro could not turn on. This module does the same
thing the app does, against the same server, with the same rules:

    POST https://phosphene.video/api/v1/licenses/activate    license_key, instance_name
    POST https://phosphene.video/api/v1/licenses/validate    license_key, instance_id, device_id, bundle_version
    POST https://phosphene.video/api/v1/licenses/deactivate  license_key, instance_id

* One Mac per key: the server decides. The instance name ends with this Mac's device id (the same
  id as the app's), and the server gives the Mac's older slot back when the same Mac activates
  again. So a Mac that already has Pro through the Mac app is NOT activated a second time here
  (it would take the app's slot): the app's token file is read instead, see pro_token.py.
* The server answers every validate with a token (valid <= 14 days). It is written next to the
  record and read by pro_token.py, which verifies the signature and the device on every call.
  Nothing here grants Pro: a hand-written record or token file is checked like any other.
* Offline: a network error or a 5xx/429 is never an answer about the licence. The last token
  keeps working until its own expiry (the 14-day offline grace), then the Studio reads Free.
* Revoked / refunded / expired / deactivated elsewhere: an explicit answer, the token file is
  removed at once and the Studio reads Free.
* Renewal: at start and every 6 hours the Studio validates again when the last good check is
  older than a day or the token has under 7 days left.

The licence key is kept in state/pro_licence.json (0600, replaced atomically), like the app keeps
its record (the app uses the Keychain). It is never logged and never sent anywhere but the server.
"""
from __future__ import annotations

import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pro_token

PRODUCTION = "https://phosphene.video/api"
RENEW_BELOW = 7 * 86400          # renew a token with less than this left
REVALIDATE_EVERY = 24 * 3600     # and at least this often while online
TICK = 6 * 3600                  # how often the background renewal looks
TIMEOUT = 15
RECORD_NAME = "pro_licence.json"

MESSAGES = {
    "unreachable": "Could not reach the licence server. Check your internet connection and try again.",
    "empty": "Paste your licence key first.",
    "via_app": "Phosphene Pro is already on through the Phosphene Mac app on this Mac, so there is nothing to activate here.",
    "no_device": "This Mac could not be identified, so a licence cannot be activated here.",
    "other_mac": "This licence was activated on another Mac. Deactivate it there, or use a key for this Mac.",
    "revoked": "This licence has been disabled. Contact support for help.",
    "expired": "This licence has expired. Renew it to keep Pro.",
    "grace_over": "The licence could not be checked for two weeks. Connect to the internet to renew it.",
}


def _base() -> str:
    """The licence server: ours, or a loopback test server when one is named (never anything else)."""
    raw = (os.environ.get("PHOSPHENE_LICENSE_SERVER") or "").strip()
    if raw:
        u = urllib.parse.urlparse(raw)
        if u.scheme in ("http", "https") and u.hostname in ("127.0.0.1", "localhost", "::1"):
            return raw.rstrip("/")
    return PRODUCTION


def state_dir() -> Path:
    raw = (os.environ.get("LTX_STATE_DIR") or "").strip()
    return Path(raw) if raw else Path(__file__).resolve().parent / "state"


def record_path() -> Path:
    return state_dir() / RECORD_NAME


def token_path() -> Path:
    return Path(pro_token.studio_token_path())


def _write_private(path: Path, text: str) -> bool:
    """Replace `path` atomically with `text`, readable by this user only."""
    tmp = path.with_name(".%s.%d.tmp" % (path.name, os.getpid()))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def load() -> dict | None:
    try:
        d = json.loads(record_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("key") and d.get("instance_id") else None


def _save(rec: dict) -> bool:
    return _write_private(record_path(), json.dumps(rec, sort_keys=True, indent=1))


# ----------------------------------------------------------------------------- the server

class Offline(Exception):
    """No answer about the licence (network trouble, 5xx, 429, garbage): never downgrades anyone."""


def _post(endpoint: str, form: dict, opener=None) -> dict:
    data = urllib.parse.urlencode(form).encode("ascii")
    req = urllib.request.Request(_base() + "/v1/licenses/" + endpoint, data=data, method="POST", headers={
        "Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded", "User-Agent": "Phosphene-Studio"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
            body = r.read(1 << 20)
            code = getattr(r, "status", 200)
    except urllib.error.HTTPError as e:
        code, body = e.code, e.read(1 << 20)
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise Offline(str(e)[:120]) from e
    if code >= 500 or code == 429:
        raise Offline("http %s" % code)
    try:
        d = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        raise Offline("unreadable answer") from e
    if not isinstance(d, dict):
        raise Offline("unreadable answer")
    return d


def current_bundle(opener=None) -> str | None:
    """The Pro pack version the server hands out now (the token is issued for a published one)."""
    req = urllib.request.Request(_base() + "/health", headers={"Accept": "application/json", "User-Agent": "Phosphene-Studio"})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=TIMEOUT) as r:
            rel = json.loads(r.read(1 << 16).decode("utf-8")).get("release")
    except (urllib.error.URLError, OSError, ValueError, AttributeError):
        return None
    rel = str(rel or "").strip()
    return rel if re.fullmatch(r"[A-Za-z0-9._+-]{1,64}", rel) else None


def _mac_name() -> str:
    try:
        name = socket.gethostname().split(".")[0]
    except OSError:
        name = ""
    return (re.sub(r"[^\w .-]", "", name) or "Mac")[:40]


def instance_name(device: str) -> str:
    # the server reads the device id from the end of this string
    return "%s · Phosphene Studio · %s" % (_mac_name(), device)


# ----------------------------------------------------------------------------- actions

def _app_token_valid() -> bool:
    """Is Pro already on this Mac through the Mac app's token (so a second activation would take
    the app's slot)?"""
    env = {"HOME": os.environ.get("HOME", ""), "PHOSPHENE_HOME": os.environ.get("PHOSPHENE_HOME", "")}
    for t in pro_token.app_tokens(env):
        v = pro_token.verify_signature(t)
        if v[0] and pro_token.check(t, int(time.time()), pro_token.device_id(), verified=v)[0]:
            return True
    return False


def activate(key: str, opener=None, now=None) -> dict:
    """Activate `key` on this Mac and fetch the first token. {ok, message, ...status()}."""
    key = (key or "").strip()
    if not key or len(key) > 128:
        return {"ok": False, "message": MESSAGES["empty"], **status()}
    dev = pro_token.device_id()
    if not dev:
        return {"ok": False, "message": MESSAGES["no_device"], **status()}
    if _app_token_valid() and not load():
        return {"ok": False, "message": MESSAGES["via_app"], **status()}
    old = load()
    try:
        r = _post("activate", {"license_key": key, "instance_name": instance_name(dev)}, opener)
    except Offline:
        return {"ok": False, "message": MESSAGES["unreachable"], **status()}
    inst = r.get("instance") or {}
    if not (r.get("activated") is True and inst.get("id") and r.get("meta")):
        return {"ok": False, "message": _human(r.get("error")), **status()}
    t = int(now if now is not None else time.time())
    rec = {"key": key, "instance_id": str(inst["id"]), "device": dev, "status": "active", "last_validated": t,
           "variant": str((r.get("meta") or {}).get("variant_name") or ""),
           "expires_at": (r.get("license_key") or {}).get("expires_at"), "token_error": None}
    if not _save(rec):
        # not durable: give the slot back rather than lose it at the next start
        try:
            _post("deactivate", {"license_key": key, "instance_id": rec["instance_id"]}, opener)
        except Offline:
            pass
        return {"ok": False, "message": "The licence could not be saved on this Mac (is the disk full?).", **status()}
    if old and old.get("key") == key and old.get("instance_id") != rec["instance_id"]:
        pass  # the server already gave the old slot of this Mac back
    out = validate(opener=opener, now=t)
    return {**out, "message": out.get("message") or "Pro is active on this Mac."}


def _human(error) -> str:
    e = str(error or "").lower()
    if "limit" in e:
        return MESSAGES["other_mac"]
    if "disabled" in e:
        return MESSAGES["revoked"]
    if "expired" in e:
        return MESSAGES["expired"]
    if "not found" in e or "invalid" in e:
        return "That licence key was not recognised. Check it and try again."
    if "not for phosphene" in e:
        return "That key is not a Phosphene Pro key."
    return (str(error)[:160] if error else "The licence server refused this key.")


def validate(opener=None, now=None) -> dict:
    """Ask the server again and store the fresh token. An explicit 'no' removes the token."""
    rec = load()
    if not rec:
        return {"ok": False, "message": None, **status()}
    dev = pro_token.device_id()
    if rec.get("device") != dev:
        return {"ok": False, "message": MESSAGES["other_mac"], **status()}
    t = int(now if now is not None else time.time())
    form = {"license_key": rec["key"], "instance_id": rec["instance_id"]}
    bundle = current_bundle(opener)
    if bundle and dev:
        form["device_id"] = dev
        form["bundle_version"] = bundle
    try:
        r = _post("validate", form, opener)
    except Offline:
        return {"ok": False, "message": MESSAGES["unreachable"], **status()}
    lk = r.get("license_key") or {}
    if r.get("valid") is True and r.get("meta") and lk:
        rec.update(status=str(lk.get("status") or "active"), expires_at=lk.get("expires_at"), last_validated=t,
                   variant=str((r.get("meta") or {}).get("variant_name") or rec.get("variant") or ""))
        tok = r.get("pro_token")
        if tok:
            if _write_private(token_path(), str(tok).strip() + "\n"):
                rec["token_error"] = None
            else:
                rec["token_error"] = "store_unavailable"
        else:
            rec["token_error"] = str(r.get("pro_token_error") or "no_token")
            if rec["token_error"] in ("instance_bound_to_another_mac", "licence_expired"):
                _remove(token_path())
        _save(rec)
        return {"ok": True, "message": None, **status()}
    if r.get("valid") is None and not r.get("error") and not lk:
        return {"ok": False, "message": MESSAGES["unreachable"], **status()}      # an empty answer is not a verdict
    # the server said no: refunded, disabled, expired, or this slot was given up elsewhere
    rec.update(status=str(lk.get("status") if lk.get("status") not in (None, "active") else "invalid"), token_error="refused")
    _remove(token_path())
    _save(rec)
    return {"ok": False, "message": _human(r.get("error")) if r.get("error") else MESSAGES["revoked"], **status()}


def deactivate(opener=None) -> dict:
    """Give this Mac's place on the key back. The record goes only once the server confirmed it."""
    rec = load()
    if not rec:
        return {"ok": True, "message": None, **status()}
    try:
        r = _post("deactivate", {"license_key": rec["key"], "instance_id": rec["instance_id"]}, opener)
    except Offline:
        return {"ok": False, "message": "Could not reach the licence server, so this Mac was not deactivated. Try again online.", **status()}
    gone = r.get("deactivated") is True or "not found" in str(r.get("error") or "").lower()
    if not gone:
        return {"ok": False, "message": _human(r.get("error")), **status()}
    _remove(record_path())
    _remove(token_path())
    return {"ok": True, "message": "This Mac is deactivated. Pro is off here.", **status()}


def status() -> dict:
    """What Settings > Pro shows. `via`: 'app' (the Mac app's token), 'studio' (this module's record), or None."""
    rec = load()
    st = pro_token.status()
    via = None
    if st.get("ok"):
        via = "studio" if (rec and pro_token.studio_token_valid()) else ("app" if _app_token_valid() else "studio")
    out = {"licensed": bool(rec), "via": via, "pro": bool(st.get("ok")), "reason": st.get("reason"),
           "expires_at": st.get("expires_at"), "variant": (rec or {}).get("variant") or None}
    if rec and not st.get("ok"):
        out["note"] = _note(rec, st)
    return out


def _note(rec: dict, st: dict) -> str | None:
    if rec.get("status") not in ("active",):
        return MESSAGES["revoked"] if rec.get("status") == "disabled" else MESSAGES["expired"] if rec.get("status") == "expired" \
            else "This licence is no longer active. Contact support if this is a mistake."
    if st.get("reason") == "expired":
        return MESSAGES["grace_over"]
    return None


# ----------------------------------------------------------------------------- renewal

def due(now: int | None = None) -> bool:
    rec = load()
    if not rec or rec.get("status") != "active":
        return False
    now = int(time.time()) if now is None else int(now)
    if now - int(rec.get("last_validated") or 0) >= REVALIDATE_EVERY:
        return True
    st = pro_token.status()
    return (not st.get("ok")) or (st.get("expires_at") or 0) - now < RENEW_BELOW


_loop_started = threading.Event()


def start_renewal(sleep=time.sleep) -> bool:
    """A daemon thread: renew at start and every 6 hours. Pinokio only, once per process."""
    if _loop_started.is_set():
        return False
    _loop_started.set()

    def run():
        while True:
            try:
                if due():
                    validate()
            except Exception:  # noqa: BLE001 - a renewal problem must never reach a render
                pass
            sleep(TICK)

    threading.Thread(target=run, name="pro-licence-renewal", daemon=True).start()
    return True
