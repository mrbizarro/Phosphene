#!/usr/bin/env python3
"""Publish a Phosphene weight pack as **GitHub release assets** + a release manifest.

WHY THIS EXISTS
---------------
The LTX-2.5 packs are quantised by us (``scripts/quantize_ltx.py``) from the
official Lightricks bf16 release. We cannot push them to HuggingFace -- the token
is read-only -- and 2.5 is the *default* generation, so without a mirror a fresh
install has no weights for the lane it boots into. The owner's call was to mirror
through GitHub releases on the public repo, the lane the ``bizarrotrn_v2`` sample
character already takes.

THE ONE HARD CONSTRAINT
-----------------------
A GitHub release asset is capped at **2 GiB**. The 2.5 q4 transformer is
11.32 GB, its connector 6.34 GB, the Gemma 4 encoder 6.70 GB. So anything over
the cap is published as ordered **shards** of at most 1.9 GB, and the reassembly
instructions travel in a release manifest.

THE MANIFEST IS NOT A NEW FORMAT
--------------------------------
It is the in-pack ``phosphene_quant_manifest.json`` with two additions: a
``release`` block and a ``shards`` list inside each file entry. The
``files: {name: {bytes, sha256}}`` shape is preserved exactly, which is what
lets the panel's existing ``_manifest_meta()`` deep-verify reader consume it
unchanged, and what keeps the quantiser as the origin of the numbers rather
than a second, drifting source of truth. Where the in-pack manifest and the real
file disagree (sidecar JSONs rewritten after the quantiser ran), the **file on
disk wins** and the drift is reported -- a published hash that does not match
the published bytes is worse than no hash.

DISK DISCIPLINE
---------------
Shards are written **one at a time**, uploaded, and deleted, so publishing a
21 GB pack costs 1.9 GB of scratch rather than a second copy of the pack. Files
that already fit under the cap are hard-linked into the staging dir under their
asset name (zero bytes copied) and unlinked after upload.

RESUME
------
Re-running skips any asset already on the release with a matching byte count, so
an upload killed at hour two picks up where it stopped. The manifest is uploaded
**last**, so a half-published release never advertises a pack that isn't there.

USAGE
-----
::

    # build + inspect the manifest, upload nothing
    python scripts/publish_pack_release.py --repo-key q4_25 --dry-run

    # publish (creates the release if needed; safe to re-run)
    python scripts/publish_pack_release.py --repo-key q4_25 \
        --tag weights-ltx25-v1 --target <public-main-sha>

Requires the ``gh`` CLI authenticated for the repo that owns the release.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = "required_files.json"
QUANT_MANIFEST = "phosphene_quant_manifest.json"
SCHEMA = "phosphene-release-manifest/1"

# 1.9 GB decimal. The GitHub cap is 2 GiB (2,147,483,648 B); this leaves ~12 %
# headroom and produces a round number in every log line and asset name.
SHARD_BYTES = 1_900_000_000
CHUNK = 1 << 22

LICENSE_ASSET = "LICENSE-LTX-2.x-Community-License.md"
NOTICE_ASSET = "NOTICE.md"

# Never shipped: scratch from an interrupted quantise or publish run.
SKIP_SUFFIXES = (".partial", ".progress")
SKIP_NAMES = {".DS_Store"}


def log(msg: str) -> None:
    print(msg, flush=True)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# Registry + pack contents
# --------------------------------------------------------------------------- #
def load_repo_entry(repo_key: str, root: Path) -> dict:
    data = json.loads((root / REQUIRED_FILES).read_text())
    for repo in data.get("repos", []):
        if repo.get("key") == repo_key:
            return repo
    raise SystemExit(f"unknown repo key {repo_key!r}")


def pack_files(pack_dir: Path) -> list[str]:
    """Every regular file in the pack, sorted -- the pack *as built*.

    Not just ``required_files.json``'s mandatory list: the loader also reads
    sidecar JSON (``split_model.json``, the upscaler configs, the quantiser's
    own manifest) that the mandatory list deliberately does not enumerate, and
    the ``download_include`` allowlist pulls with ``*.json`` for exactly that
    reason. Publishing the directory keeps the mirror a faithful copy.
    """
    out = []
    for p in sorted(pack_dir.iterdir()):
        if not p.is_file() or p.name.startswith("."):
            continue
        if p.name in SKIP_NAMES or p.name.endswith(SKIP_SUFFIXES):
            continue
        out.append(p.name)
    return out


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(CHUNK), b""):
            h.update(block)
    return h.hexdigest()


def asset_name(prefix: str, filename: str, shard_idx: int | None) -> str:
    """Deterministic asset names.

    ``<repo_key>__<filename>`` for a whole file, ``…partNNN`` for a shard. Only
    ASCII alphanumerics, dot, dash and underscore, because GitHub rewrites
    anything else in an asset name and a rewritten name breaks the manifest's
    URLs silently.
    """
    base = f"{prefix}__{filename}"
    return base if shard_idx is None else f"{base}.part{shard_idx:03d}"


# --------------------------------------------------------------------------- #
# gh
# --------------------------------------------------------------------------- #
def gh(*args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], check=check,
                          capture_output=capture, text=True)


def release_assets(release_repo: str, tag: str) -> dict[str, int]:
    """``{asset name: size}`` already on the release; ``{}`` if it doesn't exist."""
    p = gh("release", "view", tag, "-R", release_repo, "--json", "assets", check=False)
    if p.returncode != 0:
        return {}
    try:
        data = json.loads(p.stdout)
    except ValueError:
        return {}
    return {a["name"]: int(a.get("size") or 0) for a in data.get("assets", [])}


def ensure_release(release_repo: str, tag: str, target: str, title: str, notes_file: Path) -> None:
    p = gh("release", "view", tag, "-R", release_repo, "--json", "tagName", check=False)
    if p.returncode == 0:
        log(f"[publish] release {tag} exists on {release_repo}")
        return
    log(f"[publish] creating release {tag} on {release_repo} @ {target[:12]}")
    gh("release", "create", tag, "-R", release_repo,
       "--target", target, "--title", title,
       "--notes-file", str(notes_file), "--latest=false")


def upload_asset(release_repo: str, tag: str, path: Path, *, attempts: int = 3) -> None:
    for attempt in range(1, attempts + 1):
        p = gh("release", "upload", tag, str(path), "-R", release_repo,
               "--clobber", check=False)
        if p.returncode == 0:
            return
        log(f"[publish] upload {path.name} attempt {attempt}/{attempts} failed: "
            f"{(p.stderr or '').strip()[:300]}")
        if attempt < attempts:
            time.sleep(10 * attempt)
    raise RuntimeError(f"could not upload {path.name} after {attempts} attempts")


# --------------------------------------------------------------------------- #
# Publishing one file
# --------------------------------------------------------------------------- #
def publish_file(src: Path, prefix: str, staging: Path, *, release_repo: str,
                 tag: str, existing: dict[str, int], upload: bool) -> dict:
    """Shard (if needed), upload, and return the manifest entry for one file.

    One read pass: the whole-file hash and every shard hash are computed from
    the same stream. Shards are written, uploaded and deleted one at a time.
    """
    size = src.stat().st_size
    if size == 0:
        # A zero-byte file in a pack is the exact corruption the panel's
        # integrity scan exists to catch (min_size_bytes). Publishing one would
        # mirror the corruption to every user, so refuse loudly instead.
        raise SystemExit(f"{src.name} is zero bytes — refusing to publish a corrupt pack")
    file_h = hashlib.sha256()
    shards: list[dict] = []

    if size <= SHARD_BYTES:
        name = asset_name(prefix, src.name, None)
        digest = sha256_file(src)
        shards.append({"asset": name, "bytes": size, "sha256": digest})
        if upload and existing.get(name) != size:
            link = staging / name
            link.unlink(missing_ok=True)
            try:
                os.link(src, link)            # zero-copy when same filesystem
            except OSError:
                shutil.copy2(src, link)
            log(f"[publish] ↑ {name} ({size >> 20} MB)")
            upload_asset(release_repo, tag, link)
            link.unlink(missing_ok=True)
        elif upload:
            log(f"[publish] = {name} already uploaded ({size >> 20} MB)")
        return {"bytes": size, "sha256": digest, "shards": shards}

    n_shards = (size + SHARD_BYTES - 1) // SHARD_BYTES
    log(f"[publish] {src.name}: {size / 1e9:.2f} GB → {n_shards} shards")
    with open(src, "rb") as fh:
        for idx in range(n_shards):
            name = asset_name(prefix, src.name, idx)
            shard_path = staging / name
            shard_h = hashlib.sha256()
            written = 0
            want = min(SHARD_BYTES, size - idx * SHARD_BYTES)
            # A dry run hashes the same stream but writes nothing: the manifest
            # it produces is the manifest the real run would produce, at zero
            # disk cost, which is what makes --dry-run a usable rehearsal.
            out = open(shard_path, "wb") if upload else None
            try:
                while written < want:
                    block = fh.read(min(CHUNK, want - written))
                    if not block:
                        break
                    if out is not None:
                        out.write(block)
                    shard_h.update(block)
                    file_h.update(block)
                    written += len(block)
            finally:
                if out is not None:
                    out.close()
            if written != want:
                raise RuntimeError(f"{src.name}: short read on shard {idx} ({written} != {want})")
            shards.append({"asset": name, "bytes": written, "sha256": shard_h.hexdigest()})
            if upload and existing.get(name) != written:
                log(f"[publish] ↑ {name} ({written >> 20} MB, shard {idx + 1}/{n_shards})")
                upload_asset(release_repo, tag, shard_path)
            elif upload:
                log(f"[publish] = {name} already uploaded ({written >> 20} MB)")
            shard_path.unlink(missing_ok=True)     # peak scratch stays one shard

    digest = file_h.hexdigest()
    return {"bytes": size, "sha256": digest, "shards": shards}


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #
def in_pack_manifest(pack_dir: Path) -> dict:
    try:
        return json.loads((pack_dir / QUANT_MANIFEST).read_text())
    except (OSError, ValueError):
        return {}


def build_manifest(repo_key: str, repo: dict, pack_dir: Path, files: dict,
                   mirror: dict, drift: list[str]) -> dict:
    quant = in_pack_manifest(pack_dir)
    return {
        "schema": SCHEMA,
        "pack": pack_dir.name,
        "repo_key": repo_key,
        "name": repo.get("name", repo_key),
        "written_utc": utc_now(),
        "shard_bytes": SHARD_BYTES,
        "release": {
            "release_repo": mirror["release_repo"],
            "tag": mirror["tag"],
            "asset_prefix": f"{repo_key}__",
        },
        "license": {
            "name": "LTX-2.x Community License Agreement",
            "licensor": "Lightricks Ltd.",
            "asset": LICENSE_ASSET,
            "notice_asset": NOTICE_ASSET,
        },
        # Provenance from the quantiser, so a published pack still points at the
        # run that produced it even though the file list is re-hashed here.
        "quantizer": {k: quant.get(k) for k in
                      ("tool", "tool_version", "recipe", "bits", "group_size",
                       "source_pack", "written_utc") if quant.get(k) is not None},
        "quant_manifest_drift": drift,
        "files": files,
    }


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def publish(repo_key: str, root: Path, *, tag: str | None, release_repo: str | None,
            target: str | None, upload: bool, staging_root: Path,
            license_path: Path | None, notice_path: Path | None,
            notes_file: Path | None, title: str | None) -> dict:
    repo = load_repo_entry(repo_key, root)
    declared = repo.get("mirror") or {}
    mirror = {
        "release_repo": release_repo or declared.get("release_repo"),
        "tag": tag or declared.get("tag"),
    }
    if not mirror["release_repo"] or not mirror["tag"]:
        raise SystemExit("need --tag and --release-repo (or a mirror block in required_files.json)")

    pack_dir = root / repo["local_dir"]
    if not pack_dir.is_dir():
        raise SystemExit(f"pack dir not found: {pack_dir}")

    staging = staging_root / mirror["tag"]
    staging.mkdir(parents=True, exist_ok=True)

    names = pack_files(pack_dir)
    total = sum((pack_dir / n).stat().st_size for n in names)
    log(f"[publish] {repo_key}: {len(names)} file(s), {total / 1e9:.2f} GB from {pack_dir}")

    missing = [f for f in repo.get("files", []) if f not in names]
    if missing:
        raise SystemExit(f"pack is incomplete — required_files.json wants: {', '.join(missing)}")

    if upload:
        if not target:
            raise SystemExit("--target (a commit sha on the PUBLIC default branch) is required to create a release")
        notes = notes_file
        if notes is None:
            notes = staging / "_notes.md"
            notes.write_text(
                f"Weight packs for Phosphene, mirrored as release assets.\n\n"
                f"Fetched and reassembled by `scripts/fetch_pack_release.py`; every shard "
                f"and every reassembled file is sha256-verified against "
                f"`<key>__phosphene_release_manifest.json`.\n\n"
                f"See `{NOTICE_ASSET}` and `{LICENSE_ASSET}`.\n")
        ensure_release(mirror["release_repo"], mirror["tag"], target,
                       title or f"Weights: {repo.get('name', repo_key)}", notes)
    existing = release_assets(mirror["release_repo"], mirror["tag"]) if upload else {}

    files: dict[str, dict] = {}
    drift: list[str] = []
    quant = in_pack_manifest(pack_dir).get("files") or {}
    for name in names:
        entry = publish_file(pack_dir / name, repo_key, staging,
                             release_repo=mirror["release_repo"], tag=mirror["tag"],
                             existing=existing, upload=upload)
        files[name] = entry
        q = quant.get(name) or {}
        if q.get("sha256") and q["sha256"] != entry["sha256"]:
            drift.append(name)
    if drift:
        log(f"[publish] NOTE: in-pack quant manifest disagrees with the bytes on disk "
            f"for {len(drift)} file(s): {', '.join(drift)} — the published hashes are "
            f"the files', not the stale manifest's.")

    # Licence + notice ride along as pack files, so the terms land on the user's
    # disk next to the weights (§3.2), not only on a web page.
    for src, asset in ((license_path, LICENSE_ASSET), (notice_path, NOTICE_ASSET)):
        if not src:
            continue
        size = src.stat().st_size
        digest = sha256_file(src)
        files[asset] = {"bytes": size, "sha256": digest,
                        "shards": [{"asset": asset, "bytes": size, "sha256": digest}]}
        if upload and existing.get(asset) != size:
            staged = staging / asset
            staged.unlink(missing_ok=True)
            shutil.copy2(src, staged)
            log(f"[publish] ↑ {asset} ({size} B)")
            upload_asset(mirror["release_repo"], mirror["tag"], staged)
            staged.unlink(missing_ok=True)

    manifest = build_manifest(repo_key, repo, pack_dir, files, mirror, drift)
    man_name = f"{repo_key}__{QUANT_MANIFEST.replace('quant', 'release')}"
    man_path = staging / man_name
    man_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    log(f"[publish] manifest → {man_path} ({man_path.stat().st_size} B)")
    if upload:
        # Last, always: until this lands the release cannot be consumed, which
        # is exactly the behaviour we want from a half-finished upload.
        upload_asset(mirror["release_repo"], mirror["tag"], man_path)
        log(f"[publish] {repo_key}: published as {man_name}")
    return manifest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo-key", required=True)
    ap.add_argument("--root", type=Path, default=ROOT)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--release-repo", default=None)
    ap.add_argument("--target", default=None,
                    help="commit sha the tag is created on — MUST be public main")
    ap.add_argument("--title", default=None)
    ap.add_argument("--notes-file", type=Path, default=None)
    ap.add_argument("--license", dest="license_path", type=Path, default=None)
    ap.add_argument("--notice", dest="notice_path", type=Path, default=None)
    ap.add_argument("--staging", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="build the manifest, upload nothing")
    ap.add_argument("--manifest-out", type=Path, default=None)
    args = ap.parse_args(argv)

    staging_root = args.staging or (args.root / "mlx_models" / "_release_staging")
    manifest = publish(args.repo_key, args.root, tag=args.tag,
                       release_repo=args.release_repo, target=args.target,
                       upload=not args.dry_run, staging_root=staging_root,
                       license_path=args.license_path, notice_path=args.notice_path,
                       notes_file=args.notes_file, title=args.title)
    if args.manifest_out:
        args.manifest_out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        log(f"[publish] manifest copy → {args.manifest_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
