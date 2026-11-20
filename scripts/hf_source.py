#!/usr/bin/env python3
"""Where Hugging Face files come from: huggingface.co, or the China mirror when it cannot be reached.

WHY THIS EXISTS (5.0.76, owner-approved 20 Nov 2026). huggingface.co is blocked in mainland
China. A Founding customer's Hailuo H3 install died at "Downloading the model" with
`curl: (28) Failed to connect to huggingface.co port 443 after 30003 ms`, and the fleet shows
the default picture model failing the same way on Macs in China. hf-mirror.com serves the
same public files at the same paths. This module decides, once per session, which of the two
a download uses, and gives every downloader the same rules for the mirror.

THE RULES (security review of 20 Nov 2026)
  * Hugging Face first. One quick probe (a few seconds) decides the source for the session
    and is remembered for CACHE_TTL_S, so a China user does not wait 30 s per file. A real
    download that then fails to CONNECT to huggingface.co also switches the session.
  * Only a connection failure switches: never a 401/403/404/429 (those are answers).
  * The mirror is NOT trusted. Every file taken from it is checked against a size and SHA-256
    written down in advance from huggingface.co itself: either the pins a downloader already
    carries (H3, the add-ons, the packs' manifests) or hf_mirror_catalog.json, built straight
    from Hugging Face at a pinned commit and shipped inside the signed Studio. Bytes land in a
    temporary file, are verified, then renamed into place; a partial file resumes.
  * No Hugging Face token, cookie or other credential is ever sent to the mirror, including
    across redirects. Redirects must stay on HTTPS (the mirror hands big files to its CDN;
    the host may change, the checksum cannot), at most MAX_REDIRECTS of them.
  * Gated or private models are Hugging Face only (they need a login, which never goes to the
    mirror); `hf_only_reason()` says so in plain words.

Switches (support and test rooms; nothing a user has to set):
  PHOSPHENE_HF_SOURCE        auto (default) | hf (never the mirror) | mirror (always the mirror)
  PHOSPHENE_HF_MIRROR        replaces the mirror base; empty turns the mirror off. Only https://,
                             or http:// on this Mac (127.0.0.1 / localhost) for test rooms.
  PHOSPHENE_HF_PROBE_URL     replaces the probe address (test rooms)
  PHOSPHENE_HF_SOURCE_CACHE  replaces the file that remembers the probe for the session

Stdlib only: it runs inside the panel, the pack fetcher (with python -I) and small fetch
scripts, before anything optional is installed.

  usage : hf_source.py probe            print the source this session uses
          hf_source.py seed <repo> --hub <hub cache dir>   fetch a catalog repo into the HF cache
          hf_source.py check <repo> --hub <dir>            is that repo complete in the cache?
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HF = "https://huggingface.co"
MIRROR = "https://hf-mirror.com"
#: What the install logs say when the session uses the mirror (owner's wording, 20 Nov 2026).
MIRROR_LINE = "Hugging Face unreachable, using the China mirror (hf-mirror.com)"
PROBE_URL = HF + "/api/models?limit=1"
PROBE_TIMEOUT_S = 6.0
CACHE_TTL_S = 15 * 60
MAX_REDIRECTS = 5
USER_AGENT = "Phosphene"
CHUNK = 1 << 20
CATALOG_FILE = Path(__file__).resolve().with_name("hf_mirror_catalog.json")
#: curl exit codes that mean "never reached the server": resolve, connect, TLS handshake.
#: 28 (timeout) counts only when nothing arrived (the caller knows that).
CURL_CONNECT_FAILURES = frozenset({5, 6, 7, 35})
CURL_TIMEOUT = 28

_MEMO: dict = {}
_ANNOUNCED = False
_LOCK = threading.Lock()


# ---- the source ---------------------------------------------------------------------------

def _loopback(url: str) -> bool:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host in ("127.0.0.1", "localhost", "::1")


def _allowed_base(url: str) -> bool:
    p = urllib.parse.urlsplit(url)
    if p.username or p.password or p.query or p.fragment:
        return False          # never a credential-bearing or odd origin
    if p.scheme == "https" and p.hostname:
        return True
    return p.scheme == "http" and _loopback(url)


def mirror_base() -> str:
    """The mirror's base URL, or "" when the mirror is off."""
    env = os.environ.get("PHOSPHENE_HF_MIRROR")
    if env is None:
        return MIRROR
    v = env.strip().rstrip("/")
    return v if v and _allowed_base(v) else ""


def mode() -> str:
    m = (os.environ.get("PHOSPHENE_HF_SOURCE") or "auto").strip().lower()
    return m if m in ("auto", "hf", "mirror") else "auto"


def _cache_file() -> Path:
    env = os.environ.get("PHOSPHENE_HF_SOURCE_CACHE")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library" / "Caches" / "Phosphene" / "hf_source.json"


def _read_cache() -> "bool | None":
    try:
        d = json.loads(_cache_file().read_text(encoding="utf-8"))
        if time.time() - float(d["ts"]) < CACHE_TTL_S and isinstance(d.get("reachable"), bool):
            return d["reachable"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _write_cache(reachable: bool) -> None:
    f = _cache_file()
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_name(f.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"reachable": reachable, "ts": time.time()}), encoding="utf-8")
        os.replace(tmp, f)
    except OSError:
        pass


def _probe_once(url: str, timeout: float) -> bool:
    """Any HTTP answer = reachable (a 4xx/5xx is the server talking). Only a failure to
    connect, resolve or finish TLS within `timeout` is "unreachable". DNS lookups are not
    covered by urllib's timeout, so the probe runs in a thread with its own deadline."""
    out = {"ok": False}

    def run():
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout):
                out["ok"] = True
        except urllib.error.HTTPError:
            out["ok"] = True
        except Exception:  # noqa: BLE001 - URLError, timeout, ssl, reset: all "not reachable"
            out["ok"] = False

    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout + 2.0)
    return bool(out["ok"]) and not t.is_alive()


def hf_reachable(force: bool = False) -> bool:
    """Can this Mac reach huggingface.co? Probed once, remembered for the session."""
    with _LOCK:
        now = time.time()
        if not force:
            m = _MEMO.get("reachable")
            if m is not None and now - m[1] < CACHE_TTL_S:
                return m[0]
            cached = _read_cache()
            if cached is not None:
                _MEMO["reachable"] = (cached, now)
                return cached
        url = os.environ.get("PHOSPHENE_HF_PROBE_URL") or PROBE_URL
        ok = _probe_once(url, PROBE_TIMEOUT_S)
        if not ok:                       # one quick second look: a single dropped packet is not China
            ok = _probe_once(url, PROBE_TIMEOUT_S / 2)
        _MEMO["reachable"] = (ok, now)
        _write_cache(ok)
        return ok


def mark_unreachable() -> None:
    """A real download could not connect to huggingface.co: the session uses the mirror now."""
    with _LOCK:
        _MEMO["reachable"] = (False, time.time())
        _write_cache(False)


def use_mirror() -> bool:
    if not mirror_base():
        return False
    m = mode()
    if m == "mirror":
        return True
    if m == "hf":
        return False
    return not hf_reachable()


def announce(log=None) -> None:
    """Say once per process that the mirror is in use (the install log line)."""
    global _ANNOUNCED
    if _ANNOUNCED:
        return
    _ANNOUNCED = True
    (log or (lambda s: print(s, flush=True)))(MIRROR_LINE)


def base() -> str:
    """The base URL this session downloads Hugging Face files from."""
    return mirror_base() if use_mirror() else HF


def to_mirror(url: str) -> "str | None":
    """The same file on the mirror, or None for anything that is not a huggingface.co URL."""
    mb = mirror_base()
    if not mb or not isinstance(url, str):
        return None
    for prefix in (HF + "/", "http://huggingface.co/"):
        if url.startswith(prefix):
            return mb + "/" + url[len(prefix):]
    return None


def resolve(url: str, log=None) -> str:
    """`url` when Hugging Face answers, else the mirror's copy (announced once). For
    downloaders that check a pinned SHA-256 before using the file."""
    if use_mirror():
        alt = to_mirror(url)
        if alt:
            announce(log)
            return alt
    return url


def is_mirror_url(url: str) -> bool:
    mb = mirror_base()
    return bool(mb) and isinstance(url, str) and url.startswith(mb + "/")


def connect_failure(exc: BaseException) -> bool:
    """Did this exception mean the server was never reached (not an HTTP answer)?"""
    if isinstance(exc, urllib.error.HTTPError):
        return False
    if isinstance(exc, urllib.error.URLError):
        return True
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    import socket
    import ssl
    return isinstance(exc, (socket.timeout, socket.gaierror, ssl.SSLError))


def request_headers(url: str, token: "str | None" = None) -> dict:
    """Headers for a download. A token only ever goes to huggingface.co itself."""
    h = {"User-Agent": USER_AGENT}
    if token and url.startswith(HF + "/"):
        h["Authorization"] = f"Bearer {token}"
    return h


# ---- opening a mirror URL safely ----------------------------------------------------------

class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow the mirror's redirects (its CDN) on HTTPS only, at most MAX_REDIRECTS, and
    drop any credential header on the way. The host may change; the checksum cannot."""
    max_redirections = MAX_REDIRECTS

    # hf-mirror.com answers 308 (seen 20 Nov 2026); urllib before Python 3.11 does not follow
    # 308 at all. For GET/HEAD a 308 means the same as a 307.
    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_302(req, fp, code, msg, headers)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code == 308:
            code = 307
        newurl = urllib.parse.urljoin(req.full_url, newurl)
        p = urllib.parse.urlsplit(newurl)
        ok = p.scheme == "https" or (p.scheme == "http" and _loopback(newurl) and _loopback(req.full_url))
        if not ok or p.username or p.password:
            raise urllib.error.HTTPError(newurl, code, f"refused redirect to {p.scheme}://{p.hostname}",
                                         headers, fp)
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None:
            for k in list(new.headers):
                if k.lower() in ("authorization", "cookie", "proxy-authorization"):
                    del new.headers[k]
            for k in list(new.unredirected_hdrs):
                if k.lower() in ("authorization", "cookie", "proxy-authorization"):
                    del new.unredirected_hdrs[k]
        return new


def opener():
    """An opener with the safe redirect rule (proxies from the environment still apply)."""
    return urllib.request.build_opener(_SafeRedirect())


def open_url(url: str, offset: int = 0, timeout: float = 60.0, token: "str | None" = None):
    """Open `url` for reading from byte `offset`. A mirror URL gets no token and the safe
    redirect rule. Returns (response, resumed)."""
    headers = request_headers(url, None if is_mirror_url(url) else token)
    if offset:
        headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(url, headers=headers)
    resp = opener().open(req, timeout=timeout)
    resumed = bool(offset) and getattr(resp, "status", 200) == 206
    return resp, resumed


#: The only caller headers a mirror request carries. Never Host (urllib stores the failed
#: huggingface.co attempt's Host among the request's own headers, and hf-mirror.com answers
#: such a request 200 with an empty body), never Authorization, Cookie or a proxy login.
_MIRROR_HEADERS = ("user-agent", "range", "accept", "accept-encoding")


def _open_on_mirror(req, alt: str, timeout: float):
    """The same request on the mirror: allowed headers only, safe redirects."""
    h = {k: v for k, v in req.headers.items() if k.lower() in _MIRROR_HEADERS}
    if not any(k.lower() == "user-agent" for k in h):
        h["User-Agent"] = USER_AGENT
    return opener().open(urllib.request.Request(alt, headers=h), timeout=timeout)


def _switch_after(exc: BaseException) -> bool:
    """A huggingface.co request failed: is it really blocked? Only a failure to connect
    counts, and only when a fresh probe agrees (a reset or a slow read in the middle of a
    transfer from a reachable Hugging Face is not China)."""
    if mode() == "hf" or not mirror_base() or not connect_failure(exc):
        return False
    return not hf_reachable(force=True)


def open_request(req, timeout: float = 60.0, log=None):
    """Open a urllib Request for a huggingface.co file, for a caller that checks the bytes
    against a pinned SHA-256 before using them. When Hugging Face answers, this is plain
    urllib.request.urlopen, exactly as before. The mirror is used when this session cannot
    reach Hugging Face, or when this very request fails to connect; a mirror request never
    carries the caller's Authorization/Cookie header. Range and other headers are kept."""
    url = req.full_url
    alt = to_mirror(url)
    if alt and use_mirror():
        announce(log)
        return _open_on_mirror(req, alt, timeout)
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        if alt and _switch_after(e):
            announce(log)
            return _open_on_mirror(req, alt, timeout)
        raise


def urlopen(req, timeout: float = 60.0):
    """Drop-in for urllib.request.urlopen(req, timeout=...) in a pinned-checksum downloader:
    huggingface.co requests follow the China mirror rule, anything else opens as before."""
    url = req.full_url if hasattr(req, "full_url") else str(req)
    if not hasattr(req, "full_url"):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if to_mirror(url) is None:
        return urllib.request.urlopen(req, timeout=timeout)
    return open_request(req, timeout)


# ---- verified download ---------------------------------------------------------------------

def file_digest(path: Path, kind: str = "sha256", size: "int | None" = None) -> str:
    """sha256 of the bytes, or (kind "git") the git blob id the HF API lists for small files."""
    if kind == "git":
        n = path.stat().st_size if size is None else size
        h = hashlib.sha1(b"blob %d\0" % n)
    else:
        h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(8 * CHUNK)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class Rejected(Exception):
    """The bytes did not match the pinned size or checksum (nothing was put in place)."""


def fetch_verified(url: str, dest: Path, size: int, sha256: str, *, part: "Path | None" = None,
                   timeout: float = 60.0, attempts: int = 4, log=None, progress=None) -> None:
    """Download `url` into `dest`: grow `part` (resuming from its size), check the size and
    the SHA-256, then rename into place. Raises Rejected on a wrong file (the part is
    removed), or the last network error."""
    dest = Path(dest)
    # Never "<name>.incomplete": that is huggingface_hub's own partial file, which it resumes
    # from Hugging Face and promotes by size alone; a mirror prefix must never get there.
    part = Path(part) if part else dest.with_name(dest.name + ".phosphene-part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    last: "BaseException | None" = None
    for attempt in range(1, attempts + 1):
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        try:
            if have < size:
                resp, resumed = open_url(url, have, timeout)
                with resp:
                    if have and not resumed:
                        have = 0               # the server sent the whole file: start over
                    with open(part, "ab" if have else "wb") as fh:
                        while True:
                            b = resp.read(CHUNK)
                            if not b:
                                break
                            fh.write(b)
                            have += len(b)
                            if have > size:
                                break
                            if progress:
                                progress(have, size)
            got = part.stat().st_size if part.exists() else 0
            if got < size:
                last = IOError(f"download interrupted at {got} of {size} bytes; kept for resume")
                time.sleep(min(10, 2 * attempt))
                continue
            if got != size or file_digest(part) != sha256:
                part.unlink(missing_ok=True)
                raise Rejected(f"{dest.name} did not match its pinned size and checksum")
            os.replace(part, dest)
            return
        except Rejected:
            raise
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410, 429):
                raise
            last = e
        except Exception as e:  # noqa: BLE001 - network: retry, resuming
            last = e
        time.sleep(min(10, 2 * attempt))
    raise last or IOError(f"could not download {dest.name}")


# ---- the catalog and the Hugging Face cache ------------------------------------------------

def load_catalog(path: "Path | None" = None) -> dict:
    try:
        d = json.loads(Path(path or CATALOG_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def catalog_repo(repo_id: str, catalog: "dict | None" = None) -> "dict | None":
    cat = catalog if catalog is not None else load_catalog()
    entry = (cat.get("repos") or {}).get(str(repo_id))
    if not isinstance(entry, dict) or not entry.get("files"):
        return None
    return entry


def hf_only_reason(repo_id: str, catalog: "dict | None" = None) -> "str | None":
    """Why a repo cannot come from the mirror, in plain words, or None when it can."""
    cat = catalog if catalog is not None else load_catalog()
    if catalog_repo(repo_id, cat):
        return None
    why = (cat.get("hf_only") or {}).get(str(repo_id))
    return why or "it is not on Phosphene's list of checked mirror downloads"


def _repo_dir(hub_root: Path, repo_id: str) -> Path:
    return Path(hub_root) / ("models--" + repo_id.replace("/", "--"))


def _safe_rel(path: str) -> bool:
    p = Path(path)
    return bool(path) and not p.is_absolute() and ".." not in p.parts and "\\" not in path


def hub_complete(repo_id: str, hub_root: Path, catalog: "dict | None" = None) -> bool:
    """Every catalog file of the repo is in the cache snapshot at its exact size (a quick
    look; seed_hub_cache is what checks the bytes)."""
    entry = catalog_repo(repo_id, catalog)
    if not entry:
        return False
    return _snapshot_sizes_ok(_repo_dir(hub_root, repo_id) / "snapshots" / entry["revision"], entry)


def _snapshot_sizes_ok(snap: Path, entry: dict) -> bool:
    for f in entry["files"]:
        try:
            if (snap / f["path"]).stat().st_size != int(f["size"]):
                return False
        except OSError:
            return False
    return True


VERIFIED_FILE = ".phosphene_verified.json"


def _verified_load(rdir: Path) -> dict:
    try:
        d = json.loads((rdir / VERIFIED_FILE).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _verified_save(rdir: Path, record: dict) -> None:
    try:
        tmp = rdir / f"{VERIFIED_FILE}.{os.getpid()}.tmp"
        tmp.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
        os.replace(tmp, rdir / VERIFIED_FILE)
    except OSError:
        pass


def _blob_ok(blob: Path, size: int, sha: str, record: dict) -> bool:
    """The blob holds exactly these bytes: checked by SHA-256 once, then remembered by size
    and modification time (a blob that changes is checked again)."""
    try:
        st = blob.stat()
    except OSError:
        return False
    if not blob.is_file() or st.st_size != size:
        return False
    rec = record.get(blob.name) or {}
    if rec.get("sha256") == sha and rec.get("size") == size and rec.get("mtime") == int(st.st_mtime):
        return True
    if file_digest(blob) != sha:
        return False
    record[blob.name] = {"sha256": sha, "size": size, "mtime": int(st.st_mtime)}
    return True


class _BlobLock:
    """huggingface_hub's own per-blob lock (<hub>/.locks/models--x--y/<blob>.lock, an
    flock), so a seed never writes a blob while the hub, or a second Phosphene, does."""

    def __init__(self, hub_root: Path, repo_id: str, blob_name: str):
        self.path = Path(hub_root) / ".locks" / ("models--" + repo_id.replace("/", "--")) / f"{blob_name}.lock"
        self.fd = None

    def busy(self) -> bool:
        """Someone else holds the lock right now (for a log line before waiting)."""
        import fcntl
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        except OSError:
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        except OSError:
            return True
        finally:
            os.close(fd)

    def __enter__(self):
        import fcntl
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        import fcntl
        try:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
        finally:
            os.close(self.fd)


def seed_hub_cache(repo_id: str, hub_root: Path, *, log=None, catalog: "dict | None" = None,
                   progress=None, pinned: bool = False) -> bool:
    """Put every catalog file of `repo_id` into the Hugging Face cache, from the source this
    session uses, verified, laid out the way huggingface_hub itself lays it out (blobs/,
    snapshots/<commit>/, refs/main). Afterwards the engines load it offline. Returns True when
    the repo is complete; False when the repo is not in the catalog. Raises on a failure.

    Every blob used is checked against the catalog's SHA-256 (once; remembered in
    .phosphene_verified.json), including blobs already in the cache. Partial downloads use
    their own name, never huggingface_hub's "<blob>.incomplete", under the hub's own lock."""
    log = log or (lambda s: print(s, flush=True))
    entry = catalog_repo(repo_id, catalog)
    if not entry:
        return False
    rev = str(entry["revision"])
    if len(rev) != 40 or any(c not in "0123456789abcdef" for c in rev):
        raise ValueError(f"catalog entry for {repo_id} is not pinned to a commit")
    for f in entry["files"]:
        blob_name = str(f.get("blob") or f.get("sha256") or "")
        if not _safe_rel(str(f.get("path", ""))) or not blob_name or "/" in blob_name \
                or blob_name.startswith(".") or len(str(f.get("sha256", ""))) != 64:
            raise ValueError(f"catalog entry for {repo_id} has an unsafe file: {f.get('path')!r}")
    rdir = _repo_dir(hub_root, repo_id)
    refs = rdir / "refs"
    try:
        current = (refs / "main").read_text().strip()
    except OSError:
        current = ""
    # A complete snapshot the hub itself downloaded at a newer revision stays in charge:
    # the engines load it offline, nothing is downloaded again.
    # Not for a caller that loads the catalog's exact revision (pinned=True, FLUX 3).
    if not pinned and current and current != rev and _snapshot_sizes_ok(rdir / "snapshots" / current, entry):
        if base() != HF:
            announce(log)
        return True
    snap = rdir / "snapshots" / rev
    src = base()
    if src != HF:
        announce(log)
    record = _verified_load(rdir)
    total = sum(int(f["size"]) for f in entry["files"])
    done = 0
    for f in sorted(entry["files"], key=lambda x: int(x["size"])):
        path, size, sha = str(f["path"]), int(f["size"]), str(f["sha256"]).lower()
        blob_name = str(f.get("blob") or sha)
        link = snap / path
        blob = rdir / "blobs" / blob_name
        lock = _BlobLock(hub_root, repo_id, blob_name)
        if lock.busy():
            log(f"Waiting for another download of {repo_id}/{path} to finish")
        with lock:
            if not _blob_ok(blob, size, sha, record):
                if blob.exists() or blob.is_symlink():
                    blob.unlink()                   # wrong bytes under a checksum name: never keep
                url = f"{src}/{repo_id}/resolve/{rev}/{urllib.parse.quote(path)}"
                if size > 50 * 10 ** 6:
                    log(f"Downloading {repo_id}/{path} ({size / 1e9:.1f} GB)")
                part = blob.with_name(blob_name + ".phosphene-mirror-part")
                try:
                    fetch_verified(url, blob, size, sha, part=part,
                                   progress=(lambda h, s, d=done: progress(d + h, total)) if progress else None)
                except Exception as e:  # noqa: BLE001
                    if src == HF and _switch_after(e):
                        src = mirror_base()
                        announce(log)
                        url = f"{src}/{repo_id}/resolve/{rev}/{urllib.parse.quote(path)}"
                        fetch_verified(url, blob, size, sha, part=part)
                    else:
                        raise
                st = blob.stat()
                record[blob_name] = {"sha256": sha, "size": size, "mtime": int(st.st_mtime)}
                _verified_save(rdir, record)
            target = os.path.relpath(blob, link.parent)
            if not (link.is_symlink() and os.readlink(link) == target):   # under the lock: no race
                link.parent.mkdir(parents=True, exist_ok=True)
                if link.is_symlink() or link.exists():
                    link.unlink()
                os.symlink(target, link)
        done += size
        if progress:
            progress(done, total)
    _verified_save(rdir, record)
    refs.mkdir(parents=True, exist_ok=True)
    if current != rev:
        (refs / "main").write_text(rev)
    return True


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=("probe", "seed", "check"))
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--hub", type=Path)
    ap.add_argument("--force", action="store_true", help="probe again even if remembered")
    a = ap.parse_args(argv)
    if a.cmd == "probe":
        ok = hf_reachable(force=a.force)
        print(json.dumps({"hf_reachable": ok, "source": base(), "mode": mode()}))
        return 0
    if not a.repo or not a.hub:
        ap.error("seed/check need a repo and --hub")
    if a.cmd == "check":
        ok = hub_complete(a.repo, a.hub)
        print("complete" if ok else "incomplete")
        return 0 if ok else 1
    try:
        if not seed_hub_cache(a.repo, a.hub):
            print(f"FATAL error: {a.repo} cannot come from the mirror: {hf_only_reason(a.repo)}.", flush=True)
            return 2
    except Exception as e:  # noqa: BLE001
        print(f"FATAL error: {a.repo}: {e}", flush=True)
        return 1
    print(f"{a.repo}: all files in place and checked.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
