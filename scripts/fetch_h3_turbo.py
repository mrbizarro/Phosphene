#!/usr/bin/env python3
"""Fetch the digest-pinned H3 Turbo runner-layout adapter release asset.

Stdlib-only, because install_h3.js runs it before anything optional exists.
Streams to `<name>.partial`, resumes with an HTTP Range request when a
partial is already on disk, verifies the full file's SHA-256 and exact size,
and renames into place only after both checks pass — a killed or corrupt
download never leaves a file the panel's resolver would pick up.

The pins below MUST match mlx_ltx_panel.py (H3_TURBO_ASSET_*);
test_h3_turbo_adapter.py asserts they are identical so they cannot drift.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

ASSET_NAME = "lightx2v_v1.0_768p_ourlayout.safetensors"
ASSET_URL = (
    "https://github.com/mrbizarro/Phosphene/releases/download/"
    "weights-ltx25-v1/" + ASSET_NAME
)
ASSET_SHA256 = "d51d626fe0845da7e5845a47c323cf3f29086d44d24cb1a4b980882488746197"
ASSET_BYTES = 1956165254


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dir", required=True,
                    help="target directory (the H3 pack's turbo-lora/)")
    args = ap.parse_args()

    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ASSET_NAME
    partial = target.with_suffix(target.suffix + ".partial")

    if target.is_file():
        if target.stat().st_size == ASSET_BYTES:
            print(f"Turbo adapter already present: {target}")
            return 0
        # Wrong size can only be a prior bad rename (never ours) or a hand
        # copy; refuse to guess and make the operator look.
        print(f"REFUSING: {target} exists with unexpected size "
              f"{target.stat().st_size} (want {ASSET_BYTES}). "
              "Move it aside and re-run.", file=sys.stderr)
        return 1

    have = partial.stat().st_size if partial.is_file() else 0
    if have >= ASSET_BYTES:
        # A partial at-or-past full size cannot be trusted as a resume base.
        partial.unlink()
        have = 0

    headers = {"User-Agent": "Phosphene"}
    mode = "ab" if have else "wb"
    if have:
        headers["Range"] = f"bytes={have}-"
        print(f"resuming at {have // (1 << 20)} MB")
    req = urllib.request.Request(ASSET_URL, headers=headers)
    written = have
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(partial, mode) as fh:
            if have and resp.status != 206:
                # Server ignored the Range; start over inside the same call.
                fh.seek(0)
                fh.truncate()
                written = 0
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                written += len(chunk)
    except Exception as exc:  # noqa: BLE001
        print(f"TURBO FETCH FAILED at {written // (1 << 20)} MB: {exc} "
              "(partial kept for resume; panel offers a one-click retry)",
              file=sys.stderr)
        return 1

    if written != ASSET_BYTES:
        print(f"TURBO FETCH INCOMPLETE: {written} of {ASSET_BYTES} bytes "
              "(partial kept for resume)", file=sys.stderr)
        return 1
    digest = file_sha256(partial)
    if digest != ASSET_SHA256:
        partial.unlink()
        print("TURBO DIGEST MISMATCH — partial deleted; "
              "panel offers a one-click retry", file=sys.stderr)
        return 1
    partial.replace(target)
    print(f"Turbo adapter ready: {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
