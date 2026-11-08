"""Where a Pinokio Studio keeps its Pro licence so that deleting and reinstalling the app never loses it.

Before 5.0.6 the record lived only in the install's own folder (`state/pro_licence.json`), so
Pinokio's Delete + Install threw it away and a paying user had to find the purchase email and paste
the key again. The record now lives OUTSIDE every install:

  1. the macOS login Keychain, a generic password: service `app.phosphene.pro-licence`, account
     `studio` (the Studio's record) or `app` (the Mac app's, when the app writes one). Written and read
     through `/usr/bin/security`, so the item trusts that Apple tool and not one build of one app: no
     install, update or rebuild can lock it, and no password dialog ever appears;
  2. when the Keychain cannot be used (locked, missing, a remote session), the fallback file
     `~/Library/Application Support/Phosphene/licence.json` (0600, replaced atomically).

The install's own `state/pro_licence.json` is still written as a mirror, so an older Studio that is
started again (a downgrade) finds its licence where it always did.

What is stored: the same record pro_licence.py always kept (key, the server's instance id, this Mac's
device id, dates) plus the last Pro pass the server issued, so Pro is on straight after a reinstall,
online or not. Nothing here decides Pro: the pass is checked by pro_token.py (signature, this Mac,
expiry) exactly as before, and the server is asked again on its normal schedule.

The secret never appears on a command line (it goes to `security -i` on stdin) and is never logged.

Switch: PHOSPHENE_LICENCE_STORE = keychain | file | off. Default: keychain (with the file as fallback)
on macOS, file elsewhere; the file only when PHOSPHENE_HOME names a custom home (a sandbox); OFF when
PHOSPHENE_PRO_DISCOVER=0 (the release gates and tests). A test never touches the operator's Keychain
unless it asks for it by name (and then with PHOSPHENE_LICENCE_SERVICE set to a throwaway service).
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

SERVICE = "app.phosphene.pro-licence"
STUDIO = "studio"
APP = "app"
ACCOUNTS = (STUDIO, APP)
FILE_NAME = "licence.json"
LABEL = "Phosphene Pro licence"
SECURITY = "/usr/bin/security"
TIMEOUT = 10
NOT_FOUND = 44                 # security's exit code for "no such item"
_SAFE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def mode(env=None) -> str:
    env = os.environ if env is None else env
    m = (env.get("PHOSPHENE_LICENCE_STORE") or "").strip().lower()
    if m in ("keychain", "file", "off"):
        return m
    if (env.get("PHOSPHENE_PRO_DISCOVER") or "").strip() == "0":
        return "off"
    if (env.get("PHOSPHENE_HOME") or "").strip():
        return "file"           # a custom home is a sandbox (tests, clean rooms): never the real Keychain
    return "keychain" if sys.platform == "darwin" else "file"


def enabled(env=None) -> bool:
    return mode(env) != "off"


def home(env=None) -> Path:
    """The Phosphene folder in Application Support (the Mac app's home; no install ever deletes it)."""
    env = os.environ if env is None else env
    raw = (env.get("PHOSPHENE_HOME") or "").strip()
    if raw:
        return Path(raw)
    return Path(env.get("HOME") or os.path.expanduser("~")) / "Library" / "Application Support" / "Phosphene"


def file_path(env=None) -> Path:
    return home(env) / FILE_NAME


def service(env=None) -> str:
    env = os.environ if env is None else env
    raw = (env.get("PHOSPHENE_LICENCE_SERVICE") or "").strip()      # throwaway names for tests and clean rooms
    return raw if raw and _SAFE.match(raw) else SERVICE


def _security() -> str:
    raw = (os.environ.get("PHOSPHENE_SECURITY_BIN") or "").strip()  # a fake Keychain for the unit tests
    return raw or SECURITY


def _child_env() -> dict:
    return {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": os.environ.get("HOME", ""),
            "PHOSPHENE_FAKE_KEYCHAIN": os.environ.get("PHOSPHENE_FAKE_KEYCHAIN", "")}


def _encode(payload: dict) -> str:
    return base64.b64encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _decode(text: str) -> dict | None:
    try:
        d = json.loads(base64.b64decode(text.strip(), validate=True).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return d if isinstance(d, dict) else None


# ----------------------------------------------------------------------------- the Keychain

def _kc_read(account: str) -> tuple[dict | None, str | None]:
    """(payload, problem). problem None = found or genuinely absent; 'unavailable' = the Keychain
    could not be asked; 'corrupt' = an item is there but is not ours."""
    try:
        r = subprocess.run([_security(), "find-generic-password", "-s", service(), "-a", account, "-w"],
                           capture_output=True, timeout=TIMEOUT, env=_child_env())
    except (OSError, subprocess.SubprocessError):
        return None, "unavailable"
    if r.returncode == NOT_FOUND:
        return None, None
    if r.returncode != 0:
        return None, "unavailable"
    d = _decode(r.stdout.decode("ascii", "replace"))
    return (d, None) if d is not None else (None, "corrupt")


def _kc_write(account: str, payload: dict) -> bool:
    blob = _encode(payload)
    line = 'add-generic-password -U -s %s -a %s -l "%s" -w %s\n' % (service(), account, LABEL, blob)
    try:
        subprocess.run([_security(), "-i"], input=line.encode("ascii"), capture_output=True,
                       timeout=TIMEOUT, env=_child_env())
    except (OSError, subprocess.SubprocessError):
        return False
    got, _ = _kc_read(account)                 # `security -i` answers 0 either way: read it back
    return got == json.loads(json.dumps(payload, sort_keys=True))


def _kc_delete(account: str) -> bool:
    try:
        r = subprocess.run([_security(), "delete-generic-password", "-s", service(), "-a", account],
                           capture_output=True, timeout=TIMEOUT, env=_child_env())
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode in (0, NOT_FOUND)


# ----------------------------------------------------------------------------- the fallback file

def _file_load() -> tuple[dict, str | None]:
    try:
        raw = file_path().read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, None
    except OSError:
        return {}, "unavailable"
    try:
        d = json.loads(raw)
    except ValueError:
        return {}, "corrupt"
    return (d, None) if isinstance(d, dict) else ({}, "corrupt")


def _file_write(doc: dict) -> bool:
    path = file_path()
    tmp = path.with_name(".%s.%d.%s.tmp" % (path.name, os.getpid(), uuid.uuid4().hex[:8]))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(doc, sort_keys=True, indent=1))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        os.chmod(path, 0o600)
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _file_read(account: str) -> tuple[dict | None, str | None]:
    doc, prob = _file_load()
    if prob:
        return None, prob
    d = doc.get(account)
    if d is None:
        return None, None
    return (d, None) if isinstance(d, dict) else (None, "corrupt")


def _file_put(account: str, payload: dict | None) -> bool:
    doc, prob = _file_load()
    if prob == "unavailable":
        return False
    if payload is None:
        if account not in doc:
            return True
        doc.pop(account, None)
        if not doc:
            try:
                file_path().unlink()
                return True
            except FileNotFoundError:
                return True
            except OSError:
                return False
    else:
        doc[account] = payload
    return _file_write(doc)


# ----------------------------------------------------------------------------- the store

def read(account: str = STUDIO) -> tuple[dict | None, str | None]:
    """(payload, problem). The Keychain first, then the fallback file. problem is set only when
    nothing usable was found AND something was in the way ('unavailable' / 'corrupt')."""
    if account not in ACCOUNTS or not enabled():
        return None, None
    problems = []
    if mode() == "keychain":
        d, p = _kc_read(account)
        if d is not None:
            return d, None
        if p:
            problems.append(p)
    d, p = _file_read(account)
    if d is not None:
        return d, None
    if p:
        problems.append(p)
    return None, (problems[0] if problems else None)


def write(account: str, payload: dict) -> str | None:
    """Keep `payload`. Answers where it went ('keychain' / 'file'), or None when nowhere would take it.
    A copy in the file is removed once the Keychain holds the record, so two copies never disagree."""
    if account not in ACCOUNTS or not enabled():
        return None
    payload = dict(payload, saved_at=int(time.time()))
    if mode() == "keychain" and _kc_write(account, payload):
        _file_put(account, None)
        return "keychain"
    return "file" if _file_put(account, payload) else None


def delete(account: str = STUDIO) -> bool:
    """Forget the record everywhere it may be. True when nothing of it is left."""
    if account not in ACCOUNTS or not enabled():
        return True
    ok = True
    if mode() == "keychain":
        ok = _kc_delete(account) and ok
    return _file_put(account, None) and ok
