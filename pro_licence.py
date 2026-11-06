"""The Studio's own Phosphene Pro licence: for an install that has no Mac app (Pinokio).

The Mac app activates a licence key with the Phosphene licence server and keeps the signed
capability token the server answers with (see pro_token.py for what that token is). A Pinokio
install has no app, so it did that nowhere and Pro could not turn on. This module does the same
thing the app does, against the same server, with the same rules:

    POST https://phosphene.video/api/v1/licenses/activate    license_key, instance_name
    POST https://phosphene.video/api/v1/licenses/validate    license_key, instance_id, device_id, bundle_version
    POST https://phosphene.video/api/v1/licenses/deactivate  license_key, instance_id

* One Mac per key: the server decides. The instance name ends with this Mac's device id (the same
  id as the app's). One Mac may hold two installations of the product, the Mac app and this Studio:
  the server counts Macs, not installations, and each installation keeps its OWN place on the key,
  so activating here never invalidates the Mac app (and the other way round). The Mac app's token
  file is still read when this Studio has no licence of its own (Pro perks work), but only a licence
  of its own can download Remote's components and renew by itself while the app is closed.
* The server answers every validate with a token (valid <= 14 days). It is written next to the
  record and read by pro_token.py, which verifies the signature and the device on every call.
  Nothing here grants Pro: a hand-written record or token file is checked like any other.
* Offline: a network error or a 5xx/429 is never an answer about the licence. The last token
  keeps working until its own expiry (the 14-day offline grace), then the Studio reads Free.
* Revoked / refunded / expired / deactivated elsewhere: an explicit answer, the token file is
  removed at once and the Studio reads Free.
* Renewal: at start and every 6 hours the Studio validates again when the last good check is
  older than a day or the token has under 7 days left. A licence that was off (expired, refused)
  is asked again with a bounded back-off, so a paid renewal turns Pro back on without any action.
  Identity changes (activate, deactivate) are serialised; a server reply that arrives after the
  identity changed is discarded, never written.

The licence key is kept in state/pro_licence.json (0600, replaced atomically), like the app keeps
its record (the app uses the Keychain). It is never logged and never sent anywhere but the server.
"""
from __future__ import annotations

import calendar
import json
import os
import re
import socket
import threading
import time
import uuid
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
    "via_app_only": "Pro is on through the Phosphene Mac app on this Mac. To use Remote Control from this Studio too, paste your licence key here (it does not use another seat).",
    "no_device": "This Mac could not be identified, so a licence cannot be activated here.",
    "other_mac": "This licence was activated on another Mac. Deactivate it there, or use a key for this Mac.",
    "revoked": "This licence has been disabled. Contact support for help.",
    "expired": "This licence has expired. Renew it to keep Pro.",
    "grace_over": "The licence could not be checked for two weeks. Your key is still saved: connect to the internet and press Check now.",
    "clock": "This Mac's clock looks wrong (the Pro pass is dated in the future). Fix the date and time, then press Check now.",
    "pass_unsaved": "Your licence is active, but this Mac could not save its Pro pass (is the disk full or the folder read-only?). Free some space, then press Check now. Your key is kept.",
    "record_unsaved": "The licence could not be saved on this Mac (is the disk full or the folder read-only?). Nothing was changed. Fix that and try again.",
    "unreadable": "The saved licence on this Mac cannot be read right now (permissions or a damaged file). Your key is not lost: fix the folder permissions or paste the key again.",
    "superseded": "The licence changed while it was being checked, so that answer was ignored. Press Check now.",
    "pass_invalid": "The licence server answered, but its Pro pass is not valid for this Mac. Press Check now; if it persists, contact support.",
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
    """Replace `path` atomically with `text`, readable by this user only. Every attempt has its own
    temporary name, so two writers never share one."""
    tmp = path.with_name(".%s.%d.%s.tmp" % (path.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _remove(path: Path) -> bool:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def read_record() -> tuple[dict | None, str | None]:
    """(record, problem). problem: None = readable or genuinely absent; 'unreadable' = the file is
    there but cannot be read; 'corrupt' = it is there but is not a licence record."""
    try:
        raw = record_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, None
    except OSError:
        return None, "unreadable"
    try:
        d = json.loads(raw)
    except ValueError:
        return None, "corrupt"
    if isinstance(d, dict) and d.get("key") and d.get("instance_id"):
        return d, None
    return None, "corrupt"


def load() -> dict | None:
    return read_record()[0]


def _save(rec: dict) -> bool:
    return _write_private(record_path(), json.dumps(rec, sort_keys=True, indent=1))


# One lock serialises every identity change (activate, deactivate, committing an answer). `_gen`
# counts identity changes: a server reply is only written when the identity it was asked about is
# still the saved one.
_op = threading.RLock()
_gen = [0]


def _same(a: dict | None, b: dict | None) -> bool:
    return bool(a and b and a.get("key") == b.get("key") and a.get("instance_id") == b.get("instance_id"))


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
    """Is Pro on this Mac through the Mac app's token (a borrowed pass: it gives the perks that
    check the pass, but it has no licence record here to renew or to fetch Remote's parts with)?"""
    env = {"HOME": os.environ.get("HOME", ""), "PHOSPHENE_HOME": os.environ.get("PHOSPHENE_HOME", "")}
    for t in pro_token.app_tokens(env):
        v = pro_token.verify_signature(t)
        if v[0] and pro_token.check(t, int(time.time()), pro_token.device_id(), verified=v)[0]:
            return True
    return False


def _backup_corrupt() -> None:
    """A damaged record is set aside, never silently overwritten."""
    try:
        os.replace(record_path(), record_path().with_name("%s.damaged-%d" % (RECORD_NAME, int(time.time()))))
    except OSError:
        pass


def _deactivate_remote(key: str, iid: str, opener) -> bool:
    """Give a server slot back. True only when the server confirmed (or said it never existed)."""
    try:
        # single=1: give back ONLY the instance this attempt just made. The Mac app on this Mac keeps its place.
        r = _post("deactivate", {"license_key": key, "instance_id": iid, "single": "1"}, opener)
    except Offline:
        return False
    return r.get("deactivated") is True or "not found" in str(r.get("error") or "").lower()


def activate(key: str, opener=None, now=None) -> dict:
    """Activate `key` on this Mac and fetch the first token. {ok, message, ...status()}.
    ok is True only when the record AND the Pro pass are saved. On any failure the typed key is
    kept by the caller (`keep_key`); the server slot is given back or, if that cannot be confirmed,
    reused by the next attempt from this Mac (the server gives a Mac its own slot back)."""
    key = (key or "").strip()
    if not key or len(key) > 128:
        return {"ok": False, "message": MESSAGES["empty"], "keep_key": True, **status()}
    dev = pro_token.device_id()
    if not dev:
        return {"ok": False, "message": MESSAGES["no_device"], "keep_key": True, **status()}
    with _op:
        rec0, prob = read_record()
        if prob == "unreadable":
            return {"ok": False, "message": MESSAGES["unreadable"], "keep_key": True, **status()}
        if prob == "corrupt":
            _backup_corrupt()
        try:
            r = _post("activate", {"license_key": key, "instance_name": instance_name(dev)}, opener)
        except Offline:
            return {"ok": False, "message": MESSAGES["unreachable"], "keep_key": True, **status()}
        inst = r.get("instance") or {}
        if not (r.get("activated") is True and inst.get("id") and r.get("meta")):
            return {"ok": False, "message": _human(r.get("error")), "keep_key": True, **status()}
        t = int(now if now is not None else time.time())
        rec = {"key": key, "instance_id": str(inst["id"]), "device": dev, "status": "active", "last_validated": t,
               "last_checked": t, "variant": str((r.get("meta") or {}).get("variant_name") or ""),
               "expires_at": (r.get("license_key") or {}).get("expires_at"), "token_error": None}
        if not _save(rec):
            gave_back = _deactivate_remote(key, rec["instance_id"], opener)
            msg = MESSAGES["record_unsaved"] + ("" if gave_back else " (Your place on the key may still be held by this Mac; trying again here reuses it.)")
            return {"ok": False, "message": msg, "keep_key": True, "slot_released": gave_back, **status()}
        _gen[0] += 1
        out = validate(opener=opener, now=t)
        if out.get("ok"):
            return {**out, "message": out.get("message") or "Pro is active on this Mac."}
        out["keep_key"] = True
        if out.get("storage_error") is None and not out.get("offline") and not out.get("superseded") and out.get("refused"):
            # the server refused the fresh slot straight away: nothing to keep
            _remove(record_path())
            _gen[0] += 1
            out = {**out, **status()}
        return out


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
    """Ask the server again and store the fresh token. An explicit 'no' removes the token.
    The answer is written only if the identity it was asked about is still the saved one."""
    rec, prob = read_record()
    if prob:
        return {"ok": False, "message": MESSAGES["unreadable"], "storage_error": prob, **status()}
    if not rec:
        return {"ok": False, "message": None, **status()}
    dev = pro_token.device_id()
    if rec.get("device") != dev:
        return {"ok": False, "message": MESSAGES["other_mac"], **status()}
    t = int(now if now is not None else time.time())
    snap, gen = dict(rec), _gen[0]
    form = {"license_key": rec["key"], "instance_id": rec["instance_id"]}
    bundle = current_bundle(opener)
    if bundle and dev:
        form["device_id"] = dev
        form["bundle_version"] = bundle
    try:
        r = _post("validate", form, opener)
    except Offline:
        return {"ok": False, "offline": True, "message": MESSAGES["unreachable"], **status()}
    lk = r.get("license_key") or {}
    granted = r.get("valid") is True and r.get("meta") and lk
    if not granted and r.get("valid") is None and not r.get("error") and not lk:
        return {"ok": False, "offline": True, "message": MESSAGES["unreachable"], **status()}      # an empty answer is not a verdict
    with _op:
        cur, prob = read_record()
        if gen != _gen[0] or prob or not _same(cur, snap):
            # deactivated, replaced or re-activated while the server was thinking: this answer is about the past
            return {"ok": False, "superseded": True, "message": MESSAGES["superseded"], **status()}
        rec = dict(cur)
        if granted:
            rec.update(status=str(lk.get("status") or "active"), expires_at=lk.get("expires_at"), last_validated=t,
                       last_checked=t, variant=str((r.get("meta") or {}).get("variant_name") or rec.get("variant") or ""))
            rec.pop("refused_since", None)
            tok = r.get("pro_token")
            failure = None
            if tok:
                text = str(tok).strip()
                if not _write_private(token_path(), text + "\n"):
                    rec["token_error"], failure = "store_unavailable", ("pass_unsaved", MESSAGES["pass_unsaved"])
                elif not pro_token.studio_token_valid():
                    rec["token_error"], failure = "pass_invalid", ("pass_invalid", MESSAGES["pass_invalid"])
                else:
                    rec["token_error"] = None
            else:
                rec["token_error"] = str(r.get("pro_token_error") or "no_token")
                if rec["token_error"] in ("instance_bound_to_another_mac", "licence_expired"):
                    _remove(token_path())
            if not _save(rec):
                return {"ok": False, "storage_error": "record", "message": MESSAGES["record_unsaved"], **status()}
            if failure:
                return {"ok": False, "storage_error": failure[0], "message": failure[1], **status()}
            return {"ok": True, "message": None, **status()}
        # the server said no: refunded, disabled, expired, or this slot was given up elsewhere
        rec.update(status=str(lk.get("status") if lk.get("status") not in (None, "active") else "invalid"),
                   token_error="refused", last_checked=t)
        rec.setdefault("refused_since", t)
        gone = _remove(token_path())
        _save(rec)
        msg = _human(r.get("error")) if r.get("error") else MESSAGES["revoked"]
        if not gone:
            msg += " (The old Pro pass could not be removed from this Mac; it lapses by itself within two weeks.)"
        return {"ok": False, "refused": True, "message": msg, **status()}


def deactivate(opener=None) -> dict:
    """Give this Mac's place on the key back. The record goes only once the server confirmed it."""
    with _op:
        rec, prob = read_record()
        if prob == "unreadable":
            return {"ok": False, "message": MESSAGES["unreadable"], **status()}
        if not rec:
            if prob == "corrupt":
                _backup_corrupt()
            return {"ok": True, "message": None, **status()}
        try:
            r = _post("deactivate", {"license_key": rec["key"], "instance_id": rec["instance_id"]}, opener)
        except Offline:
            return {"ok": False, "message": "Could not reach the licence server, so this Mac was not deactivated. Try again online.", **status()}
        gone = r.get("deactivated") is True or "not found" in str(r.get("error") or "").lower()
        if not gone:
            return {"ok": False, "message": _human(r.get("error")), **status()}
        _gen[0] += 1
        ok_rec, ok_tok = _remove(record_path()), _remove(token_path())
        if not (ok_rec and ok_tok):
            return {"ok": False, "message": "This Mac was deactivated on the server, but its saved files could not be removed. Remove the Studio's state/pro_* files or restart the Studio.", **status()}
        out = status()
        msg = "This Mac is deactivated. Its place on the key is free; you can activate another Mac now."
        if out.get("pro"):
            # the Mac app's own pass is a separate file; it is released by the same deactivation on the server
            # and lapses at its next check (open the Mac app while online), at the latest in 14 days
            msg += " Pro can still show here for a short while from the Mac app's last pass; it ends at the app's next check."
        return {"ok": True, "message": msg, **out}


def status() -> dict:
    """What Settings > Pro shows. `via`: 'app' (the Mac app's token), 'studio' (this module's record), or None."""
    rec, prob = read_record()
    st = pro_token.status()
    via = None
    if st.get("ok"):
        via = "studio" if (rec and pro_token.studio_token_valid()) else ("app" if _app_token_valid() else "studio")
    out = {"licensed": bool(rec), "via": via, "pro": bool(st.get("ok")), "reason": st.get("reason"),
           "expires_at": st.get("expires_at"), "variant": (rec or {}).get("variant") or None,
           "storage_problem": prob, "remote_needs_licence": bool(via == "app" and not rec)}
    note = _note(rec, st, prob)
    if note:
        out["note"] = note
    return out


def _note(rec: dict | None, st: dict, prob: str | None = None) -> str | None:
    if prob:
        return MESSAGES["unreadable"]
    if not rec:
        return MESSAGES["via_app_only"] if st.get("ok") and _app_token_valid() else None
    if rec.get("token_error") in ("store_unavailable",):
        return MESSAGES["pass_unsaved"]
    if rec.get("token_error") == "pass_invalid":
        return MESSAGES["pass_invalid"]
    if st.get("ok"):
        return None
    if rec.get("status") not in ("active",):
        if rec.get("status") == "disabled":
            return MESSAGES["revoked"]
        if rec.get("status") == "expired":
            return "Your paid period has ended. If you renewed, Pro turns back on by itself shortly (or press Check now). Your key is kept."
        return "This licence is not active. Press Check now if that changed; contact support if this is a mistake."
    if st.get("reason") == "expired":
        return MESSAGES["grace_over"]
    if st.get("reason") == "not_yet_valid":
        return MESSAGES["clock"]
    return None


# ----------------------------------------------------------------------------- renewal

INACTIVE_EVERY = 6 * 3600        # a licence that is off is asked again this often ...
INACTIVE_LONG = 24 * 3600        # ... daily once it has been refused for over three days
INACTIVE_FINAL = 7 * 86400       # ... weekly once its paid period ended over 30 days ago
BACKOFF = (60, 120, 300, 900, 3600)   # after "no answer": retry soon, then back off


def _epoch(iso) -> int | None:
    try:
        return calendar.timegm(time.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S")) if iso else None
    except (ValueError, OverflowError):
        return None


def due(now: int | None = None) -> bool:
    rec = load()
    if not rec:
        return False
    now = int(time.time()) if now is None else int(now)
    if rec.get("status") != "active":
        # A refusal is not forever: a paid renewal turns the licence on again on the server. Ask again,
        # slowly. The server alone decides (a refund stays off until the server says otherwise).
        last = int(rec.get("last_checked") or rec.get("last_validated") or 0)
        every = INACTIVE_EVERY
        since = int(rec.get("refused_since") or 0)
        if since and now - since > 3 * 86400:
            every = INACTIVE_LONG
        ended = _epoch(rec.get("expires_at"))
        if ended and now - ended > 30 * 86400:
            every = INACTIVE_FINAL
        return now - last >= every or now < last - 86400
    if now - int(rec.get("last_validated") or 0) >= REVALIDATE_EVERY:
        return True
    st = pro_token.status()
    return (not st.get("ok")) or (st.get("expires_at") or 0) - now < RENEW_BELOW


_loop_started = threading.Event()
_fails = [0]


def renew_step(now: int | None = None) -> int:
    """One look: validate when due. Returns the seconds until the next look (a short back-off after
    'no answer', so a reconnect or a wake is noticed fast and a dead network is not hammered)."""
    try:
        if due(now):
            r = validate(now=now)
            if r.get("offline"):
                n = _fails[0]
                _fails[0] = n + 1
                return BACKOFF[min(n, len(BACKOFF) - 1)]
        _fails[0] = 0
    except Exception:  # noqa: BLE001 - a renewal problem must never reach a render
        pass
    return TICK


SLICE = 30   # seconds: the loop watches the WALL clock, so a Mac that slept is looked at on waking


def start_renewal(sleep=time.sleep) -> bool:
    """A daemon thread: renew at start, then when due. Pinokio only, once per process."""
    if _loop_started.is_set():
        return False
    _loop_started.set()

    def run():
        nxt = 0.0
        while True:
            now = time.time()
            if now >= nxt or nxt - now > 2 * TICK:       # due, or the clock was set back
                nxt = time.time() + renew_step()
            sleep(SLICE)

    threading.Thread(target=run, name="pro-licence-renewal", daemon=True).start()
    return True
