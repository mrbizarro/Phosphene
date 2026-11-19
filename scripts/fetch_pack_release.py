#!/usr/bin/env python3
"""Fetch a Phosphene weight pack that is mirrored as **GitHub release assets**.

WHY THIS EXISTS
---------------
Every weight pack Phosphene has ever shipped came from HuggingFace, so
``install.js`` and the panel's Download button both just shell out to
``hf download``. The LTX-2.5 packs cannot take that lane: their upstream is
gated, we publish *our own* quantisation of it, and our HF token is read-only.
LTX-2.5 is nonetheless the **default** generation -- which meant that until this
script existed, a fresh install had **no weights at all** for the default lane.

So the 2.5 packs are mirrored the way the sample character LoRA already is: as
assets on a release of the public repo (see ``SAMPLE_CHARACTER`` in
``mlx_ltx_panel.py``). This is the downloader for that lane.

WHAT MAKES IT MORE THAN A CURL LOOP
-----------------------------------
A GitHub release asset is capped at **2 GiB** and the 2.5 q4 transformer alone is
11.32 GB. So every file over the cap is published as ordered **shards**, and the
reassembly instructions -- per-shard *and* per-file sha256 -- travel in a release
manifest that is a strict **superset of the in-pack**
``phosphene_quant_manifest.json`` (same ``files: {name: {bytes, sha256}}`` shape,
plus a ``shards`` list per file). One manifest format, not two: the panel's
existing ``_manifest_meta()`` deep-verify reader can consume either.

Guarantees, all of which the sample-character downloader also makes:

* **atomic** -- a file only appears under its real name after its whole-file
  sha256 matches; a killed download never leaves something the loader would pick
  up;
* **verified twice** -- every shard is checked against its own sha256 as it
  lands, and the reassembled file against the file sha256. A silent CDN
  truncation cannot survive both;
* **resumable at two levels** -- a partial shard resumes with an HTTP Range
  request, and a partly-assembled file resumes at shard granularity from a
  sidecar; re-running after a kill re-downloads only what is missing;
* **idempotent** -- a complete, hash-correct file is left alone, so
  ``install.js`` can re-run this on every Resume Install for the cost of a
  read pass;
* **frugal with disk** -- shards are appended into the output and deleted one at
  a time, so peak extra space is *one shard* (1.9 GB), not a second copy of the
  pack.

USAGE
-----
::

    python scripts/fetch_pack_release.py --repo-key q4_25
    python scripts/fetch_pack_release.py --repo-key q4_25 --repo-key gemma4_25
    python scripts/fetch_pack_release.py --repo-key q4_25 --dest /tmp/from-zero/pack
    python scripts/fetch_pack_release.py --repo-key q4_25 --check-only

Exit code 0 means every declared file is present and hash-correct. Exit code 75
(EX_TEMPFAIL) means another download is writing the same pack folder right now
and this one stopped without touching it.

ONE WRITER PER PACK FOLDER
--------------------------
The studio's Models page, its background fetcher, ``install.js`` and the Mac
app's own model download all run THIS script. Two copies writing one folder
append into the same ``.phosphene_parts`` partials and corrupt both, so a
download holds an exclusive ``flock`` on ``.<folder>.fetchlock`` beside the
destination folder for as long as it writes (the kernel drops the lock when
the process ends, however it ends; the panel's Remove and Repair take the
same lock before they delete). A second download of the same folder refuses
at once with exit 75; ``--check-only`` reads and takes no lock.

Stdlib only, on purpose: this runs from ``install.js`` before anything optional
is installed, and from the panel, which is stdlib-only by house rule.
"""

from __future__ import annotations

import argparse
import errno
import hashlib
import http.client
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = "required_files.json"
PARTS_DIRNAME = ".phosphene_parts"
USER_AGENT = "Phosphene"
CHUNK = 1 << 20
# Report at most this often, so a 6-shard 11 GB file does not spam the panel log.
PROGRESS_EVERY_SEC = 2.5
#: Exit code when another download holds the destination folder (EX_TEMPFAIL).
EXIT_BUSY = 75


class PackBusy(Exception):
    """Another download is writing this pack folder right now."""


#: The one-writer lock lives BESIDE the pack folder, as ".<folder>.fetchlock",
#: not on the folder itself: removing or repairing a pack deletes the folder,
#: and a lock on a deleted folder's inode would let the next writer lock a
#: fresh folder at the same path while the first one still writes there.
PACK_LOCK_SUFFIX = ".fetchlock"


def pack_lock_path(dest_dir: Path) -> Path:
    """The lock file for a pack folder: its REAL path's sibling, so a
    Pinokio-style symlink and the Mac app's own path meet on one lock."""
    real = Path(os.path.realpath(str(dest_dir)))
    return real.parent / f".{real.name}{PACK_LOCK_SUFFIX}"


def lock_dest(dest_dir: Path) -> int:
    """Take the one-writer lock for ``dest_dir`` (an exclusive, non-blocking
    flock on its lock file) and return the descriptor; keep it open for as
    long as the download writes. Raises PackBusy when another process holds
    it. The kernel drops the lock when the process ends, however it ends."""
    try:
        import fcntl
    except ImportError:              # not a POSIX system: nothing to lock with
        return -1
    lock = pack_lock_path(dest_dir)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if e.errno in (errno.EWOULDBLOCK, errno.EAGAIN, errno.EACCES):
            raise PackBusy(str(dest_dir)) from None
        raise
    try:                             # who holds it, for a human reading the file
        os.ftruncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
    except OSError:
        pass
    return fd


def unlock_dest(fd: int) -> None:
    if fd >= 0:
        try:
            os.close(fd)             # closing the descriptor releases the flock
        except OSError:
            pass


def log(msg: str) -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def load_repo_entry(repo_key: str, root: Path) -> dict:
    """The ``required_files.json`` entry for ``repo_key``.

    That file stays the single source of truth for what a complete pack is --
    the same reason the panel's registry points at it by reference instead of
    copying the file list.
    """
    data = json.loads((root / REQUIRED_FILES).read_text())
    for repo in data.get("repos", []):
        if repo.get("key") == repo_key:
            return repo
    known = ", ".join(r.get("key", "?") for r in data.get("repos", []))
    raise SystemExit(f"unknown repo key {repo_key!r} (known: {known})")


def mirror_of(repo: dict) -> dict:
    """The GitHub-release mirror block, or a hard error naming the reason.

    A pack with no ``mirror`` block belongs to the HuggingFace lane and must be
    fetched with ``hf download`` -- silently doing nothing here would look like
    a successful install with no weights, which is the exact failure this whole
    script exists to end.
    """
    mirror = repo.get("mirror") or {}
    if mirror.get("kind") != "github-release":
        raise SystemExit(
            f"repo {repo.get('key')!r} is not mirrored through a GitHub release "
            f"(mirror.kind={mirror.get('kind')!r}) -- use `hf download` for it"
        )
    for field in ("release_repo", "tag", "manifest_asset"):
        if not mirror.get(field):
            raise SystemExit(f"repo {repo.get('key')!r} mirror block is missing {field!r}")
    return mirror


def asset_url(mirror: dict, asset: str) -> str:
    """The GitHub release address of ``asset`` (PHOSPHENE_PACK_GITHUB replaces the host)."""
    return (f"{(_github_base() or GITHUB_BASE).rstrip('/')}/{mirror['release_repo']}"
            f"/releases/download/{mirror['tag']}/{asset}")


#: Our own copy of every release asset: a BACKUP since 17 Nov 2026 (owner: GitHub first, our
#: server only when GitHub fails, so normal days cost our server no bandwidth). Same file
#: names, laid out as <base>/<tag>/<asset>; every shard is still checked against the
#: manifest's sha256, so a copy can never change what lands on disk.
#: PHOSPHENE_PACK_MIRRORS (space separated) replaces the list, for test rooms; an empty value
#: means no own server at all.
OWN_MIRRORS: tuple[str, ...] = ("https://phosphene.video/app/Phosphene-mirror",)
#: GitHub gets ONE short try per file: a 404/403/5xx or a stall moves on at once instead of
#: spending the per-source retries there (seconds; PHOSPHENE_GITHUB_TIMEOUT for test rooms).
GITHUB_TIMEOUT = int(os.environ.get("PHOSPHENE_GITHUB_TIMEOUT") or 15)
#: Base of GitHub's release downloads; PHOSPHENE_PACK_GITHUB replaces it (test rooms: a local
#: server, or an unreachable address to prove the backups). Empty = skip GitHub.
GITHUB_BASE = "https://github.com"


def _own_mirrors() -> tuple[str, ...]:
    env = os.environ.get("PHOSPHENE_PACK_MIRRORS")
    if env is not None:
        return tuple(b for b in env.split() if b)
    return OWN_MIRRORS


def _github_base() -> str:
    env = os.environ.get("PHOSPHENE_PACK_GITHUB")
    return GITHUB_BASE if env is None else env.strip()


#: Every GitHub address handed out this run (so a test room's stand-in counts as GitHub too).
_GITHUB_URLS: set[str] = set()


def github_url(mirror: dict, asset: str) -> list[str]:
    if not _github_base():
        return []
    url = asset_url(mirror, asset)
    _GITHUB_URLS.add(url)
    return [url]


def own_urls(mirror: dict, asset: str) -> list[str]:
    return [f"{b.rstrip('/')}/{mirror['tag']}/{asset}" for b in _own_mirrors()]


def asset_urls(mirror: dict, asset: str) -> list[str]:
    """Every place this asset can come from, in order: GitHub, then our server(s)."""
    return github_url(mirror, asset) + own_urls(mirror, asset)


def is_github(url: str) -> bool:
    return url in _GITHUB_URLS


def source_name(url: str) -> str:
    """Anonymous label of the place a file came from (for the log and analytics)."""
    if is_github(url):
        return "github"
    if _RANGE_MARK in url or "huggingface.co" in url:
        return "hf"
    if any(url.startswith(b.rstrip("/") + "/") for b in _own_mirrors()):
        return "own"
    return "other"


#: name -> source label of every file this run downloaded (what served its LAST shard).
SERVED: dict[str, str] = {}


#: Hugging Face: the same bytes on our own account (pages open since 17 Nov 2026, card #872).
#: HF holds each file WHOLE, so a shard is asked for as a byte range of it (HF answers
#: ranges from its CDN). Big files come from HF right after GitHub and our server is
#: their last resort (owner, card #871: "don't serve big files from the server"); small
#: files try HF last. Only repos under our account qualify, plus the exact
#: official files listed in HF_OFFICIAL_COPIES. A gated repo answers 401 without a key,
#: which counts as "not there". The shard sha256 check is unchanged, so no source can
#: change what lands on disk. PHOSPHENE_PACK_HF replaces the base (test rooms); an
#: empty value turns HF off.
HF_BASE = "https://huggingface.co"
HF_OWN_ACCOUNT = "mrbizarro"
_RANGE_MARK = "#bytes="
#: A file at least this big is "big": HF first, our server only as a fallback.
BIG_FILE_BYTES = 32 << 20
#: Official upstream files that open public pages hold byte for byte, by sha256 of the
#: whole file -> (repo, path) pairs tried after our own repo. Checked 17 Nov 2026.
HF_OFFICIAL_COPIES: dict[str, tuple[tuple[str, str], ...]] = {
    # LTX-2.5 distilled LoRA (Lightricks' file; the High add-on's second piece)
    "86370bbf79a9eb4edaa158907e2b48a5188fe4c5dc8ce30c7eb8f2f131a9bbf5": (
        ("comfyicu/LTX-2.5", "loras/ltx-2.5-22b-distilled-lora-450-bf16.safetensors"),
    ),
}


def _hf_base() -> str:
    env = os.environ.get("PHOSPHENE_PACK_HF")
    return HF_BASE if env is None else env.strip()


def hf_repo_of(repo: dict) -> str | None:
    rid = str(repo.get("repo_id") or "")
    if rid.split("/", 1)[0].lower() != HF_OWN_ACCOUNT or "/" not in rid:
        return None
    return rid


def _hf_slice(rid: str, path: str, start: int, length: int) -> str | None:
    base = _hf_base()
    if not rid or not base or length <= 0:
        return None
    quoted = urllib.parse.quote(path)
    return f"{base.rstrip('/')}/{rid}/resolve/main/{quoted}{_RANGE_MARK}{start}-{start + length - 1}"


def hf_range_url(mirror: dict, name: str, start: int, length: int) -> str | None:
    """URL for bytes [start, start+length) of ``name`` in our HF repo, or None."""
    return _hf_slice(mirror.get("hf_repo") or "", name, start, length)


def hf_sources(mirror: dict, name: str, sha256: str, start: int, length: int) -> list[str]:
    """Every HF place for this slice: our own repo, then official copies of the same file."""
    urls = [hf_range_url(mirror, name, start, length)]
    urls += [_hf_slice(rid, path, start, length)
             for rid, path in HF_OFFICIAL_COPIES.get(sha256, ())]
    return [u for u in urls if u]


def shard_sources(mirror: dict, name: str, spec: dict, asset: str,
                  start: int, length: int) -> list[str]:
    """Where one shard can come from, in order (owner, 17 Nov 2026: GitHub first).
    Big file: GitHub, then Hugging Face, then our server (it costs bandwidth, HF is free).
    Small file: GitHub, then our server, then Hugging Face."""
    gh = github_url(mirror, asset)
    ours = own_urls(mirror, asset)
    hf = hf_sources(mirror, name, str(spec.get("sha256") or ""), start, length)
    if int(spec.get("bytes") or 0) >= BIG_FILE_BYTES:
        return gh + hf + ours
    return gh + ours + hf


def _tries_for(url: str, attempts: int, timeout: int) -> tuple[int, int]:
    """(attempts, timeout) for one source: GitHub gets one short try, so a failing GitHub
    hands over to the backups at once; the backups keep the full retries."""
    if is_github(url):
        return 1, max(1, min(timeout, GITHUB_TIMEOUT))
    return attempts, timeout


def _split_range(url: str) -> tuple[str, int | None, int | None]:
    if _RANGE_MARK in url:
        bare, rng = url.split(_RANGE_MARK, 1)
        a, b = rng.split("-", 1)
        return bare, int(a), int(b)
    return url, None, None


class _Gone(Exception):
    """The server answered 404/403/410 for this URL: try the next source at once."""


class _NowhereYet(RuntimeError):
    """Every source answered "not there" (404/403/410): nothing to retry."""


def _first_source(urls: list[str], fn, label: str, gone_all: bool = False):
    """Run ``fn(url)`` on each source in turn until one works; the last error wins.

    A source that answers "not there" is skipped without the per-source retries; a
    source that fails after its retries hands over to the next one."""
    last: BaseException | None = None
    gone = 0
    for i, url in enumerate(urls):
        try:
            return fn(url)
        except _Gone as e:
            last = e
            gone += 1
            if i + 1 < len(urls):
                log(f"[fetch] {label}: not at {url.split('/')[2]} ({e}), trying the next source")
        except (RuntimeError, SystemExit) as e:
            last = e
            if i + 1 < len(urls):
                log(f"[fetch] {label}: {url.split('/')[2]} failed ({e}), trying the next source")
    if isinstance(last, SystemExit):
        raise last
    if gone_all and gone == len(urls):
        raise _NowhereYet(str(last))
    raise RuntimeError(str(last))


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _open(url: str, offset: int, timeout: int):
    """Open ``url``, asking to start at ``offset``.

    Returns ``(response, resumed)``. ``resumed`` is False when the server
    ignored the Range header (some proxies do) -- the caller then restarts the
    shard from zero rather than assuming a byte position it cannot verify.
    """
    url, start, end = _split_range(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    if start is not None:
        # A slice of a whole file (the HF source): only a 206 is the slice we asked for.
        req.add_header("Range", f"bytes={start + offset}-{end}")
        resp = urllib.request.urlopen(req, timeout=timeout)
        if resp.status != 206:
            resp.close()
            raise RuntimeError(f"source ignored the byte range (HTTP {resp.status})")
        return resp, bool(offset)
    if offset:
        req.add_header("Range", f"bytes={offset}-")
    resp = urllib.request.urlopen(req, timeout=timeout)
    return resp, bool(offset) and resp.status == 206


def download_shard(url: str, dest: Path, expect_bytes: int, expect_sha: str,
                   *, attempts: int, timeout: int, label: str) -> None:
    """Fetch one shard to ``dest``, resuming a partial file, and verify it.

    Raises on a hash mismatch after the last attempt. The shard is deleted on a
    mismatch so the next attempt cannot inherit corrupt bytes -- an important
    difference from a plain resume, which would happily append to garbage.
    """
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            have = dest.stat().st_size if dest.exists() else 0
            if have > expect_bytes:      # overshoot => the file is not ours
                dest.unlink()
                have = 0
            if have < expect_bytes:
                resp, resumed = _open(url, have, timeout)
                mode = "ab" if resumed else "wb"
                if not resumed:
                    have = 0
                with resp, open(dest, mode) as fh:
                    last = 0.0
                    while True:
                        chunk = resp.read(CHUNK)
                        if not chunk:
                            break
                        fh.write(chunk)
                        have += len(chunk)
                        now = time.time()
                        if now - last > PROGRESS_EVERY_SEC:
                            pct = (100.0 * have / expect_bytes) if expect_bytes else 100.0
                            log(f"[fetch] {label} {have >> 20} / {expect_bytes >> 20} MB ({pct:.1f}%)")
                            last = now
            # A clean early EOF is an interrupted transfer, not a bad hash.
            # Keep the prefix so a retry (including another source) can resume it.
            size = dest.stat().st_size if dest.exists() else 0
            if size < expect_bytes:
                raise RuntimeError(f"download interrupted at {size} of {expect_bytes} bytes; saved for resume")
            got = sha256_file(dest)
            if got != expect_sha:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"shard sha256 mismatch (expected {expect_sha[:12]}…, got {got[:12]}…)")
            size = dest.stat().st_size
            if size != expect_bytes:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"shard size mismatch (expected {expect_bytes}, got {size})")
            return
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):
                raise _Gone(f"HTTP {e.code}") from e
            last_err = e
            if attempt < attempts:
                back = 5 * attempt
                log(f"[fetch] {label} attempt {attempt}/{attempts} failed ({e}) — retrying in {back}s")
                time.sleep(back)
        except (urllib.error.URLError, http.client.HTTPException, OSError, RuntimeError) as e:
            last_err = e
            if attempt < attempts:
                back = 5 * attempt
                log(f"[fetch] {label} attempt {attempt}/{attempts} failed ({e}) — retrying in {back}s")
                time.sleep(back)
    raise RuntimeError(f"{label}: giving up after {attempts} attempts — {last_err}")


def fetch_json(url: str, attempts: int, timeout: int) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 404, 410):
                raise _Gone(f"HTTP {e.code}") from e
            last_err = e
            if attempt < attempts:
                time.sleep(5 * attempt)
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            last_err = e
            if attempt < attempts:
                time.sleep(5 * attempt)
    raise SystemExit(f"could not read the release manifest at {url} — {last_err}")


# --------------------------------------------------------------------------- #
# Hashing
# --------------------------------------------------------------------------- #
def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Assembly
# --------------------------------------------------------------------------- #
def _pack_path(root: Path, name: str, *, finished: bool = False) -> Path:
    """Confine a publisher's flat filename to the chosen pack directory.

    The destination itself may be a user-selected symlink (Pinokio's shared
    models). Children must not be symlinks, including dangling ones: partials
    and progress files are opened for writing, and shards may be resumed.
    This checks existing paths; it is not a sandbox against another local
    process replacing paths concurrently.

    ``finished`` (a declared file's own name, H31): a link there is allowed. The
    Mac app's "reuse from Pinokio" links finished files into the pack folder.
    Such a link is only read (hashed), or replaced whole by the rename of a
    downloaded file; nothing is ever written through it.
    """
    if (not isinstance(name, str) or not name or name in (".", "..")
            or any(c in name for c in ("/", "\\", ":"))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError(f"unsafe pack filename: {name!r}")
    path = root / name
    if finished and path.is_symlink():
        return path
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe pack path: {path}")
    return path


def _validate_file_paths(name: str, spec: dict, dest_dir: Path) -> None:
    if name == PARTS_DIRNAME:
        raise ValueError(f"reserved pack filename: {name!r}")
    _pack_path(dest_dir, name, finished=True)
    partial = _pack_path(dest_dir, name + ".partial")
    _pack_path(dest_dir, _progress_path(partial).name)
    parts_dir = _pack_path(dest_dir, PARTS_DIRNAME)
    # A spec may legitimately carry no shards here (e.g. the lock-contract
    # manifests that mock assemble_file): assemble_file is the only reader of
    # spec["shards"], so the preflight must not be stricter about structure
    # than the base. Any shards that DO exist are still confined, which is all
    # the guard needs — a manifest can only escape via a shard it declares.
    for shard in spec.get("shards") or []:
        _pack_path(parts_dir, shard["asset"])


def _validate_pack_paths(files: dict, dest_dir: Path) -> None:
    # Validate the whole manifest before writing anything, even if an unsafe
    # entry follows valid ones. Extra sidecars are legitimate publisher output.
    for name, spec in files.items():
        _validate_file_paths(name, spec, dest_dir)


def _progress_path(partial: Path) -> Path:
    return partial.with_suffix(partial.suffix + ".progress")


def _read_progress(partial: Path) -> int:
    """How many shards of ``partial`` are already appended, per its sidecar.

    The sidecar is authoritative only when it agrees with the file's actual
    length; any disagreement (a kill between the write and the fsync, a manual
    truncation) resets to zero rather than trusting a number that would splice
    shards at the wrong offset.
    """
    try:
        rec = json.loads(_progress_path(partial).read_text())
        done, size = int(rec["shards_done"]), int(rec["bytes"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    if not partial.exists() or partial.stat().st_size != size:
        return 0
    return done


def _write_progress(partial: Path, shards_done: int) -> None:
    _progress_path(partial).write_text(json.dumps({
        "shards_done": shards_done,
        "bytes": partial.stat().st_size,
    }))


def assemble_file(name: str, spec: dict, dest_dir: Path, mirror: dict,
                  *, attempts: int, timeout: int) -> None:
    """Download + reassemble one declared file into ``dest_dir``.

    Shards are appended to ``<name>.partial`` and deleted as they land, so the
    peak extra disk cost is one shard rather than a second copy of the pack --
    the difference between +1.9 GB and +11.3 GB on the transformer, on machines
    that are already tight enough that we quantised the model for them.
    """
    _validate_file_paths(name, spec, dest_dir)
    target = _pack_path(dest_dir, name, finished=True)
    want_sha = spec["sha256"]
    want_bytes = int(spec["bytes"])

    if target.exists():
        if target.stat().st_size == want_bytes and sha256_file(target) == want_sha:
            log(f"[fetch] {name} — already complete and verified, skipping")
            return
        log(f"[fetch] {name} — present but does not match the manifest, refetching")

    parts_dir = _pack_path(dest_dir, PARTS_DIRNAME)
    parts_dir.mkdir(parents=True, exist_ok=True)
    partial = _pack_path(dest_dir, name + ".partial")
    shards = spec["shards"]
    done = _read_progress(partial)
    if done:
        log(f"[fetch] {name} — resuming after shard {done}/{len(shards)}")
    elif partial.exists():
        partial.unlink()

    offsets = [0]
    for shard in shards:
        offsets.append(offsets[-1] + int(shard["bytes"]))
    for idx, shard in enumerate(shards):
        if idx < done:
            continue
        asset = shard["asset"]
        label = f"{name} [{idx + 1}/{len(shards)}]"
        shard_path = _pack_path(parts_dir, asset)
        sources = shard_sources(mirror, name, spec, asset, offsets[idx], int(shard["bytes"]))
        def _one(url, shard=shard, shard_path=shard_path, label=label):
            a, t = _tries_for(url, attempts, timeout)
            download_shard(url, shard_path, int(shard["bytes"]), shard["sha256"],
                           attempts=a, timeout=t, label=label)
            return url
        served = _first_source(sources, _one, label)
        SERVED[name] = source_name(served)
        if SERVED[name] != "github":
            log(f"[fetch] {label}: served by {SERVED[name]} (GitHub did not answer)")
        with open(partial, "ab") as out, open(shard_path, "rb") as src:
            shutil.copyfileobj(src, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        shard_path.unlink(missing_ok=True)
        _write_progress(partial, idx + 1)

    size = partial.stat().st_size
    got = sha256_file(partial) if size == want_bytes else None
    if got != want_sha:
        # DISCARD THE ASSEMBLY, not just the attempt. Every shard verified on
        # its way in, so a bad whole means bytes ALREADY in the partial went bad
        # (a disk error, a copy of a half-synced tree) — and the progress
        # sidecar only checks length. Kept, it made every retry skip the same
        # shards and fail the same hash forever (Codex INST-08). Dropped, the
        # next run refetches this one file; verified files are untouched.
        partial.unlink(missing_ok=True)
        _progress_path(partial).unlink(missing_ok=True)
        if got is None:
            raise RuntimeError(f"{name}: assembled {size} bytes, manifest declares "
                               f"{want_bytes} — discarded; the next run refetches it")
        raise RuntimeError(f"{name}: assembled sha256 {got[:12]}… != manifest "
                           f"{want_sha[:12]}… — discarded; the next run refetches it")
    partial.replace(target)
    _progress_path(partial).unlink(missing_ok=True)
    log(f"[fetch] {name} — {size >> 20} MB, sha256 verified")


# --------------------------------------------------------------------------- #
# One pack
# --------------------------------------------------------------------------- #
def check_pack(manifest: dict, dest_dir: Path) -> list[str]:
    """Names of declared files that are missing or do not match the manifest."""
    _validate_pack_paths(manifest["files"], dest_dir)
    bad: list[str] = []
    for name, spec in manifest["files"].items():
        path = dest_dir / name
        if not path.exists() or path.stat().st_size != int(spec["bytes"]):
            bad.append(name)
            continue
        if sha256_file(path) != spec["sha256"]:
            bad.append(name)
    return bad


def fetch_pack(repo_key: str, root: Path, dest: Path | None,
               *, attempts: int, timeout: int, check_only: bool) -> int:
    repo = load_repo_entry(repo_key, root)
    mirror = dict(mirror_of(repo), hf_repo=hf_repo_of(repo))
    dest_dir = dest or (root / repo["local_dir"])
    # Native bootstrap uses Python -I, which omits the script directory from
    # sys.path. Load our sibling by its exact source path in that mode too.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "model_path_guard", Path(__file__).with_name("model_path_guard.py"))
    model_path_guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(model_path_guard)
    configured = os.environ.get("LTX_MODELS_DIR")
    model_root = Path(configured) if configured else (root / "mlx_models" if dest is None else dest.parent)
    model_path_guard.require(model_root, dest_dir, write=not check_only,
                             allow_new=not configured and dest is None)
    urls = asset_urls(mirror, mirror["manifest_asset"])

    log(f"[fetch] {repo_key}: {repo.get('name', repo_key)} → {dest_dir}")
    log(f"[fetch] manifest {urls[0]}")
    try:
        manifest = _first_source(urls, lambda url: fetch_json(url, *_tries_for(url, attempts, timeout)),
                                 f"{repo_key} manifest", gone_all=True)
    except _NowhereYet:
        # 17 Nov 2026: q8_25 / hq_25 are not on our server yet and GitHub hides the account.
        log(f"[fetch] {repo_key}: this model isn't available for download right now; the standard models work. "
            "It comes back soon.")
        raise RuntimeError("not available for download right now (no source has it)")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit(f"release manifest for {repo_key} declares no files")
    _validate_pack_paths(files, dest_dir)

    # The manifest is the mirror's word for what a pack is; required_files.json
    # is the app's. They must not drift, and a drift found HERE (before 21 GB is
    # pulled) is worth far more than one found by a loader at render time.
    declared = list(repo.get("files", [])) + list(repo.get("sidecars") or [])
    missing = [f for f in declared if f not in files]
    if missing:
        raise SystemExit(
            f"release manifest for {repo_key} is missing files that "
            f"required_files.json declares mandatory: {', '.join(missing)}"
        )
    # The app checks a declared size EXACTLY (fleet #206), so a size that
    # drifted from the manifest would leave a finished pack reading incomplete.
    drift = [f for f, n in (repo.get("file_sizes") or {}).items()
             if f in files and int(files[f].get("bytes", -1)) != int(n)]
    if drift:
        raise SystemExit(
            f"release manifest for {repo_key} disagrees with required_files.json "
            f"file_sizes for: {', '.join(drift)}"
        )

    model_path_guard.require(model_root, dest_dir, write=not check_only,
                             allow_new=not configured and dest is None)
    if not check_only:
        model_path_guard.prepare_directory(model_root, dest_dir,
            allow_new=not configured and dest is None)
    if check_only:
        bad = check_pack(manifest, dest_dir)
        if bad:
            log(f"[fetch] {repo_key}: INCOMPLETE — {len(bad)} file(s): {', '.join(bad)}")
            return 1
        log(f"[fetch] {repo_key}: complete, {len(files)} file(s) verified")
        return 0

    # ONE WRITER (Mac app review #7): the Mac app's download and the studio's
    # Models page / background fetcher both run this script. Refuse, untouched,
    # when another copy is writing this folder.
    try:
        lock_fd = lock_dest(dest_dir)
    except PackBusy:
        log(f"[fetch] {repo_key}: BUSY — another download is writing {dest_dir} right now; "
            f"this one stopped without touching it, so the two never write the same files. "
            f"It finishes on its own; download again after it if anything is still missing.")
        return EXIT_BUSY
    try:
        return _fetch_locked(repo_key, files, dest_dir, mirror,
                             attempts=attempts, timeout=timeout)
    finally:
        unlock_dest(lock_fd)


def _fetch_locked(repo_key: str, files: dict, dest_dir: Path, mirror: dict,
                  *, attempts: int, timeout: int) -> int:
    _validate_pack_paths(files, dest_dir)
    SERVED.clear()
    total_gb = sum(int(s["bytes"]) for s in files.values()) / 1e9
    log(f"[fetch] {len(files)} file(s), {total_gb:.2f} GB — resumable, sha256-verified")

    # One bad file does NOT abandon the rest of the pack. A poisoned shard in
    # the middle of an 11 GB transformer used to stop the loop dead, so a user
    # who retried started the other 10 GB from nothing. Every file gets its own
    # attempt; the failures are collected and reported together, and the next
    # run skips everything that already verified.
    failed: list[str] = []
    for name, spec in files.items():
        try:
            assemble_file(name, spec, dest_dir, mirror, attempts=attempts, timeout=timeout)
        except Exception as e:  # noqa: BLE001 — a cause, not a traceback
            failed.append(name)
            log(f"[fetch] {name}: FAILED — {e}")

    parts_dir = dest_dir / PARTS_DIRNAME
    if parts_dir.is_dir() and not any(parts_dir.iterdir()):
        parts_dir.rmdir()
    if failed:
        log(f"[fetch] {repo_key}: {len(failed)} of {len(files)} file(s) FAILED "
            f"({', '.join(failed)}) — run this again, it resumes and re-checks "
            f"only what is missing. In Studio choose Download again; in the Mac app choose Resume.")
        return 1
    log(f"[fetch] {repo_key}: done — {len(files)} file(s) verified against the manifest")
    _log_sources(repo_key)
    return 0


def _log_sources(repo_key: str) -> None:
    """One machine-readable line naming which kind of source served this pack's files
    (github / hf / own / other, counts only; no addresses, no user data). The Studio's
    downloader turns it into the anonymous `source` field of its download event, so
    failovers to our server show up in analytics."""
    if not SERVED:
        return
    counts: dict[str, int] = {}
    for v in SERVED.values():
        counts[v] = counts.get(v, 0) + 1
    line = json.dumps({"pack": repo_key, "served": counts}, sort_keys=True)
    log("[fetch] sources " + line)
    # The Mac app's setup runs this script before any Studio is up: it names a file
    # (PHOSPHENE_SOURCES_LOG) and the Studio reports the lines once when it starts.
    side = os.environ.get("PHOSPHENE_SOURCES_LOG")
    if side:
        try:
            with open(side, "a") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-key", action="append", required=True,
                    help="key in required_files.json (repeatable), e.g. q4_25")
    ap.add_argument("--dest", type=Path, default=None,
                    help="override the destination dir (single --repo-key only; for from-zero tests)")
    ap.add_argument("--root", type=Path, default=ROOT, help="app root holding required_files.json")
    ap.add_argument("--attempts", type=int, default=4)
    ap.add_argument("--timeout", type=int, default=60)
    ap.add_argument("--check-only", action="store_true",
                    help="verify what is on disk against the published manifest; download nothing")
    args = ap.parse_args(argv)

    if args.dest and len(args.repo_key) > 1:
        ap.error("--dest applies to a single --repo-key")

    rc = 0
    busy = False
    for key in args.repo_key:
        try:
            one = fetch_pack(key, args.root, args.dest,
                             attempts=args.attempts, timeout=args.timeout,
                             check_only=args.check_only)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — the installer needs a cause, not a traceback
            log(f"[fetch] {key}: FAILED — {e}")
            log("[fetch] Download stopped. Check your connection, then choose Download in Studio "
                "or Resume in the Mac app. Saved progress will be reused.")
            one = 1
        if one == EXIT_BUSY:
            busy = True
        elif one:
            rc = 1
    # A real failure wins over "busy": the caller must not mistake a broken
    # download for one that is merely being served by someone else.
    return rc if rc else (EXIT_BUSY if busy else 0)


if __name__ == "__main__":
    sys.exit(main())
