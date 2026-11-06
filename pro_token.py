"""Is Phosphene Pro active on this Mac: the studio's own check of the signed capability token.

The licence server issues a short-lived capability token for a Pro licence on every successful
validate (at most 15 days: the 14-day offline grace). The Mac app keeps it with the licence and
passes it to the studio it starts (PHOSPHENE_PRO_TOKEN, and PHOSPHENE_PRO_TOKEN_FILE, which the
app rewrites on every renewal). The sealed Pro modules check the same token themselves; this
module lets the open studio ask the same question for the things it gates in the open (the PRO
mark, Pro estimates, H3 Flex Turbo), so a paying Mac reads Pro and a hand-written file cannot.

    token   = "v1." + b64url(payload) + "." + b64url(signature)
    signed  = b"phosphene-pro-token-v1\\0" + payload
    payload = {"kid", "iid", "dev", "bv", "iat", "exp", "ed": "pro", "m"}

A token counts when: its Ed25519 signature verifies against a key listed here, its key id is
that key's, it is a Pro token, its lifetime is at most 15 days, it has not expired, it is not
dated more than a day ahead, and it was issued for THIS Mac (device id from the hardware UUID,
the same derivation as the app and the server). The bundle version is NOT checked here: the
studio is not a Pro bundle, and the sealed modules check it for the paid code.

Revocation: the server stops issuing tokens at once (refund, cancellation, a licence moved to
another Mac); a token already on this Mac lapses at its own expiry, as offline grace allows.

Ed25519 verification is implemented here (RFC 8032, verify only) so the studio needs no extra
package. The signature verdict is cached per token; time and device are decided on every call.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import threading
import time

# Public keys of the licence server's token signer, by key id. Public by design.
PUBLIC_KEYS = {"tok-2026-10": "johlJ6vvCc7C2k2QyQkgbms+im/Y5VpK35hAcCQcfAQ="}
DOMAIN = b"phosphene-pro-token-v1\x00"
MAX_LIFE = 15 * 86400
SKEW = 86400
TOKEN_FILE_MAX = 4096

REASONS = {
    "missing": "no licence token reached the studio",
    "malformed": "the licence token is damaged",
    "bad_signature": "the licence token was not issued by Phosphene",
    "wrong_key": "the licence token was signed with a key this studio does not know",
    "wrong_edition": "the licence token is not a Pro token",
    "too_long": "the licence token claims a lifetime this studio does not accept",
    "expired": "the licence check has expired (open Phosphene while online to renew it)",
    "not_yet_valid": "the licence token is dated in the future (check the Mac's clock)",
    "wrong_device": "the licence token belongs to another Mac",
    "no_device": "this Mac's hardware id could not be read",
}

# ----------------------------------------------------------------------------- Ed25519 (verify)

_P = 2 ** 255 - 19
_L = 2 ** 252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_I = pow(2, (_P - 1) // 4, _P)


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


def _add(a, b):
    # extended twisted Edwards coordinates (X, Y, Z, T), a = -1
    A = (a[1] - a[0]) * (b[1] - b[0]) % _P
    B = (a[1] + a[0]) * (b[1] + b[0]) % _P
    C = 2 * a[3] * b[3] * _D % _P
    D = 2 * a[2] * b[2] % _P
    E, F, G, H = B - A, D - C, D + C, B + A
    return (E * F % _P, G * H % _P, F * G % _P, E * H % _P)


def _scalar(k: int, pt):
    acc = (0, 1, 1, 0)
    while k:
        if k & 1:
            acc = _add(acc, pt)
        pt = _add(pt, pt)
        k >>= 1
    return acc


def _x_from_y(y: int, sign: int):
    if y >= _P:
        return None
    xx = (y * y - 1) * _inv(_D * y * y + 1) % _P
    if xx == 0:
        return None if sign else 0
    x = pow(xx, (_P + 3) // 8, _P)
    if (x * x - xx) % _P:
        x = x * _I % _P
    if (x * x - xx) % _P:
        return None
    return _P - x if (x & 1) != sign else x


def _decode_point(raw: bytes):
    if len(raw) != 32:
        return None
    n = int.from_bytes(raw, "little")
    y, sign = n & ((1 << 255) - 1), n >> 255
    x = _x_from_y(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def _equal(a, b) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


_BY = 4 * _inv(5) % _P
_B = (_x_from_y(_BY, 0), _BY, 1, _x_from_y(_BY, 0) * _BY % _P)


def ed25519_verify(public: bytes, message: bytes, signature: bytes) -> bool:
    if len(public) != 32 or len(signature) != 64:
        return False
    A, R = _decode_point(public), _decode_point(signature[:32])
    if A is None or R is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _L:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public + message).digest(), "little") % _L
    return _equal(_scalar(s, _B), _add(R, _scalar(h, A)))


# ----------------------------------------------------------------------------- the token

def _b64(s: str) -> bytes:
    s = str(s).strip().rstrip("=").replace("+", "-").replace("/", "_")
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify_signature(token: str, keys: dict | None = None):
    """(ok, reason, payload): what depends only on the token and the keys (signature, key id,
    edition, lifetime). Expensive; cached by the caller."""
    keys = PUBLIC_KEYS if keys is None else keys
    if not token:
        return False, "missing", None
    parts = str(token).strip().split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return False, "malformed", None
    try:
        payload, sig = _b64(parts[1]), _b64(parts[2])
    except (ValueError, TypeError):
        return False, "malformed", None
    if not payload or len(payload) > 2048:
        return False, "malformed", None
    try:
        p = json.loads(payload.decode("utf-8"))
        if not isinstance(p, dict):
            raise ValueError
        kid = str(p.get("kid", ""))
        iat, exp = int(p["iat"]), int(p["exp"])
        str(p["dev"])
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return False, "malformed", None
    pub = keys.get(kid)
    if not pub:
        return False, "wrong_key", None
    try:
        pub_raw = _b64(pub)
    except (ValueError, TypeError):
        return False, "wrong_key", None
    if not ed25519_verify(pub_raw, DOMAIN + payload, sig):
        return False, "bad_signature", None
    if str(p.get("ed", "")) != "pro":
        return False, "wrong_edition", p
    if exp <= iat or exp - iat > MAX_LIFE:
        return False, "too_long", p
    return True, "ok", p


def check(token: str, now: int, device: str | None, keys: dict | None = None, verified=None):
    """(ok, reason, payload). Pure: every input is an argument."""
    ok, reason, p = verified if verified is not None else verify_signature(token, keys)
    if not ok:
        return ok, reason, p
    if now >= int(p["exp"]):
        return False, "expired", p
    if now + SKEW < int(p["iat"]):
        return False, "not_yet_valid", p
    if not device:
        return False, "no_device", p
    if str(p["dev"]) != device:
        return False, "wrong_device", p
    return True, "ok", p


# ----------------------------------------------------------------------------- this Mac

_lock = threading.Lock()
_sig_cache: dict = {}
_device: list = []


def device_id() -> str | None:
    """sha256("phosphene-device-v1:" + IOPlatformUUID)[:16], like the app's Device.swift."""
    with _lock:
        if _device:
            return _device[0]
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"], capture_output=True,
                             timeout=10, env={"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.decode("utf-8", "replace").splitlines():
        if '"IOPlatformUUID"' in line and "=" in line:
            uuid = line.split("=", 1)[1].strip().strip('"')
            if uuid:
                d = hashlib.sha256(("phosphene-device-v1:" + uuid).encode()).hexdigest()[:16]
                with _lock:
                    _device[:] = [d]
                return d
    return None


def _read_token_file(path: str) -> str | None:
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if (st.st_mode & 0o170000) != 0o100000 or st.st_size > TOKEN_FILE_MAX:
            return None
        raw = os.read(fd, TOKEN_FILE_MAX + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    try:
        return raw.decode("ascii").strip() or None
    except UnicodeDecodeError:
        return None


STUDIO_TOKEN_NAME = "pro_studio.token"
APP_TOKEN_NAMES = ("studio.token", "capability.token")


def studio_token_path(environ=None) -> str:
    """Where the Studio's own licence (pro_licence.py) keeps the token it fetched."""
    env = os.environ if environ is None else environ
    raw = (env.get("LTX_STATE_DIR") or "").strip()
    base = raw if raw else os.path.join(os.path.dirname(os.path.abspath(__file__)), "state")
    return os.path.join(base, STUDIO_TOKEN_NAME)


def studio_token_valid() -> bool:
    t = _read_token_file(studio_token_path())
    if not t:
        return False
    v = _verified(t)
    return bool(v[0] and check(t, int(time.time()), device_id(), verified=v)[0])


def app_tokens(environ=None) -> list[str]:
    """The tokens the Phosphene Mac app keeps on this Mac (Pinokio reads them; the app passes its
    own). Off with PHOSPHENE_PRO_DISCOVER=0 (the tests)."""
    env = os.environ if environ is None else environ
    if (env.get("PHOSPHENE_PRO_DISCOVER") or "").strip() == "0":
        return []
    home = (env.get("PHOSPHENE_HOME") or "").strip()
    if not home:
        h = (env.get("HOME") or "").strip()
        if not h:
            return []
        home = os.path.join(h, "Library", "Application Support", "Phosphene")
    out = []
    for name in APP_TOKEN_NAMES:
        t = _read_token_file(os.path.join(home, "pro", name))
        if t and t not in out:
            out.append(t)
    return out


def _discovered() -> list[str]:
    out = []
    t = _read_token_file(studio_token_path())
    if t:
        out.append(t)
    if (os.environ.get("PHOSPHENE_DISTRIBUTION") or "").strip().lower() != "mac-app":
        out += [a for a in app_tokens() if a not in out]
    return out


def candidates(environ=None) -> list[str]:
    """The renewal file first (the app rewrites it on every renewal), then the launch token; on the
    real environment also the Studio's own licence token and, on Pinokio, the Mac app's."""
    env = os.environ if environ is None else environ
    out = []
    path = (env.get("PHOSPHENE_PRO_TOKEN_FILE") or "").strip()
    if path:
        t = _read_token_file(path)
        if t:
            out.append(t)
    t = (env.get("PHOSPHENE_PRO_TOKEN") or "").strip()
    if t and t not in out:
        out.append(t)
    if environ is None:
        out += [d for d in _discovered() if d not in out]
    return out


def _verified(token: str):
    with _lock:
        hit = _sig_cache.get(token)
    if hit is None:
        hit = verify_signature(token)
        with _lock:
            if len(_sig_cache) >= 16:
                _sig_cache.clear()
            _sig_cache[token] = hit
    return hit


def status(environ=None, now: int | None = None, device=None) -> dict:
    """{ok, reason, expires_at}: the verdict NOW over every candidate token (the valid one that
    expires last wins; with none valid, the first one's reason)."""
    tokens = candidates(environ)
    if not tokens:
        return {"ok": False, "reason": "missing", "expires_at": None}
    now = int(time.time()) if now is None else int(now)
    best = None
    for token in tokens:
        v = _verified(token)
        dev = (device if device is not None else device_id()) if v[0] else None
        r = check(token, now, dev, verified=v)
        if r[0]:
            if best is None or not best[0] or int(r[2]["exp"]) > int(best[2]["exp"]):
                best = r
        elif best is None:
            best = r
    ok, reason, p = best
    return {"ok": bool(ok), "reason": reason, "expires_at": int(p["exp"]) if ok and p else None}
