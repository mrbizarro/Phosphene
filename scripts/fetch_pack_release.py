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

Exit code 0 means every declared file is present and hash-correct.

Stdlib only, on purpose: this runs from ``install.js`` before anything optional
is installed, and from the panel, which is stdlib-only by house rule.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = "required_files.json"
PARTS_DIRNAME = ".phosphene_parts"
USER_AGENT = "Phosphene"
CHUNK = 1 << 20
# Report at most this often, so a 6-shard 11 GB file does not spam the panel log.
PROGRESS_EVERY_SEC = 2.5


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
    return (f"https://github.com/{mirror['release_repo']}"
            f"/releases/download/{mirror['tag']}/{asset}")


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _open(url: str, offset: int, timeout: int):
    """Open ``url``, asking to start at ``offset``.

    Returns ``(response, resumed)``. ``resumed`` is False when the server
    ignored the Range header (some proxies do) -- the caller then restarts the
    shard from zero rather than assuming a byte position it cannot verify.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
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
            got = sha256_file(dest)
            if got != expect_sha:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"shard sha256 mismatch (expected {expect_sha[:12]}…, got {got[:12]}…)")
            size = dest.stat().st_size
            if size != expect_bytes:
                dest.unlink(missing_ok=True)
                raise RuntimeError(f"shard size mismatch (expected {expect_bytes}, got {size})")
            return
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, RuntimeError) as e:
            last_err = e
            if attempt < attempts:
                back = 5 * attempt
                log(f"[fetch] {label} attempt {attempt}/{attempts} failed ({e}) — retrying in {back}s")
                time.sleep(back)
    raise RuntimeError(f"{label}: giving up after {attempts} attempts — {last_err}")


def fetch_json(url: str, *, attempts: int, timeout: int) -> dict:
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as e:
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
    target = dest_dir / name
    want_sha = spec["sha256"]
    want_bytes = int(spec["bytes"])

    if target.exists():
        if target.stat().st_size == want_bytes and sha256_file(target) == want_sha:
            log(f"[fetch] {name} — already complete and verified, skipping")
            return
        log(f"[fetch] {name} — present but does not match the manifest, refetching")

    parts_dir = dest_dir / PARTS_DIRNAME
    parts_dir.mkdir(parents=True, exist_ok=True)
    partial = dest_dir / (name + ".partial")
    shards = spec["shards"]
    done = _read_progress(partial)
    if done:
        log(f"[fetch] {name} — resuming after shard {done}/{len(shards)}")
    elif partial.exists():
        partial.unlink()

    for idx, shard in enumerate(shards):
        if idx < done:
            continue
        asset = shard["asset"]
        label = f"{name} [{idx + 1}/{len(shards)}]"
        shard_path = parts_dir / asset
        download_shard(asset_url(mirror, asset), shard_path,
                       int(shard["bytes"]), shard["sha256"],
                       attempts=attempts, timeout=timeout, label=label)
        with open(partial, "ab") as out, open(shard_path, "rb") as src:
            shutil.copyfileobj(src, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        shard_path.unlink(missing_ok=True)
        _write_progress(partial, idx + 1)

    size = partial.stat().st_size
    if size != want_bytes:
        raise RuntimeError(f"{name}: assembled {size} bytes, manifest declares {want_bytes}")
    got = sha256_file(partial)
    if got != want_sha:
        raise RuntimeError(f"{name}: assembled sha256 {got[:12]}… != manifest {want_sha[:12]}…")
    partial.replace(target)
    _progress_path(partial).unlink(missing_ok=True)
    log(f"[fetch] {name} — {size >> 20} MB, sha256 verified")


# --------------------------------------------------------------------------- #
# One pack
# --------------------------------------------------------------------------- #
def check_pack(manifest: dict, dest_dir: Path) -> list[str]:
    """Names of declared files that are missing or do not match the manifest."""
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
    mirror = mirror_of(repo)
    dest_dir = dest or (root / repo["local_dir"])
    url = asset_url(mirror, mirror["manifest_asset"])

    log(f"[fetch] {repo_key}: {repo.get('name', repo_key)} → {dest_dir}")
    log(f"[fetch] manifest {url}")
    manifest = fetch_json(url, attempts=attempts, timeout=timeout)
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise SystemExit(f"release manifest for {repo_key} declares no files")

    # The manifest is the mirror's word for what a pack is; required_files.json
    # is the app's. They must not drift, and a drift found HERE (before 21 GB is
    # pulled) is worth far more than one found by a loader at render time.
    missing = [f for f in repo.get("files", []) if f not in files]
    if missing:
        raise SystemExit(
            f"release manifest for {repo_key} is missing files that "
            f"required_files.json declares mandatory: {', '.join(missing)}"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)
    if check_only:
        bad = check_pack(manifest, dest_dir)
        if bad:
            log(f"[fetch] {repo_key}: INCOMPLETE — {len(bad)} file(s): {', '.join(bad)}")
            return 1
        log(f"[fetch] {repo_key}: complete, {len(files)} file(s) verified")
        return 0

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
            f"only what is missing")
        return 1
    log(f"[fetch] {repo_key}: done — {len(files)} file(s) verified against the manifest")
    return 0


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
    for key in args.repo_key:
        try:
            rc |= fetch_pack(key, args.root, args.dest,
                             attempts=args.attempts, timeout=args.timeout,
                             check_only=args.check_only)
        except SystemExit:
            raise
        except Exception as e:  # noqa: BLE001 — the installer needs a cause, not a traceback
            log(f"[fetch] {key}: FAILED — {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
