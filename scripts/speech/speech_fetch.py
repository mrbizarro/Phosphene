#!/usr/bin/env python3
"""The Speech pack: the single downloader and verifier.

    speech-mlx/.venv/bin/python scripts/speech/speech_fetch.py            # download + verify
    python3 scripts/speech/speech_fetch.py --check                        # offline, sizes only

Two checkpoints of Qwen3-TTS 12 Hz 1.7B (Apache-2.0), the 8-bit MLX
conversions by mlx-community, each at a PINNED revision listed in
speech_manifest.json with every file's size and sha256:

  <models>/customvoice/   the nine preset voices + delivery instructions
  <models>/base/          voice cloning from a short clip

The two share one speech tokenizer byte for byte, so the second copy is a
hard link (one download, ~0.7 GB saved on disk).

Resumable: a file whose sha256 already matches is kept; a half-downloaded
file resumes where it stopped (huggingface_hub keeps the partial under
<models>/.staging). Phosphene re-hosts nothing.

The check half of this file (pack_problems, PACK_BYTES) is stdlib-only, so
the panel imports it to answer "is Speech installed" without the engine venv.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = json.loads((HERE / "speech_manifest.json").read_text())
PACKS: dict = MANIFEST["packs"]
SHARED = set(MANIFEST.get("shared") or [])
# Bytes on disk once installed (the shared tokenizer counted once).
PACK_BYTES = sum(p["bytes"] for p in PACKS.values()) - sum(
    f["size"] for f in PACKS["base"]["files"] if f["path"] in SHARED)
RECEIPT = "pack_source.json"


def models_dir(arg: str | None = None) -> Path:
    root = HERE.parent.parent
    raw = arg or os.environ.get("LTX_SPEECH_MODELS") or "mlx_models/speech"
    p = Path(raw).expanduser()
    return p if p.is_absolute() else root / p


def pack_problems(models: Path) -> list[str]:
    """Offline, sizes only — cheap enough for every /status poll."""
    out: list[str] = []
    for key, pack in PACKS.items():
        for f in pack["files"]:
            p = models / key / f["path"]
            try:
                size = p.stat().st_size
            except OSError:
                out.append(f"{key}/{f['path']} missing")
                continue
            if size != f["size"]:
                out.append(f"{key}/{f['path']} is {size} bytes, expected {f['size']}")
    return out


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def say(msg: str) -> None:
    print(f"[speech-fetch] {msg}", flush=True)


def fatal(msg: str) -> None:
    # One line Pinokio and the panel both recognise as the reason (see
    # scripts/pinokio/README.md: FATAL error: <reason + what to do>).
    print(f"FATAL error: {msg}", flush=True)
    sys.exit(1)


def _good(p: Path, f: dict) -> bool:
    try:
        return p.stat().st_size == f["size"] and sha256(p) == f["sha256"]
    except OSError:
        return False


def fetch(models: Path, min_free_gb: float) -> None:
    models.mkdir(parents=True, exist_ok=True)
    staging = models / ".staging"
    todo: list[tuple[str, dict]] = []
    for key, pack in PACKS.items():
        for f in pack["files"]:
            if not _good(models / key / f["path"], f):
                todo.append((key, f))
    if not todo:
        say("every file is present and verified")
        write_receipt(models)
        return
    need = sum(f["size"] for k, f in todo if not (k == "base" and f["path"] in SHARED))
    # A resumed install already holds part of it in staging; count only what is left.
    staged = 0
    if staging.is_dir():
        for p in staging.rglob("*"):
            try:
                staged += p.stat().st_size if p.is_file() else 0
            except OSError:
                pass
    need = max(0, need - staged)
    free = shutil.disk_usage(models).free
    if free < need + min_free_gb * 2**30:
        fatal(f"Not enough disk space for Speech: it needs {need / 1e9:.1f} GB plus "
              f"{min_free_gb:.0f} GB headroom and {free / 1e9:.1f} GB is free. Free some space, then Install again.")
    try:
        from huggingface_hub import hf_hub_download
        from huggingface_hub.utils import logging as hf_logging
        hf_logging.set_verbosity_error()   # no "unauthenticated requests" nag on the card
    except ImportError:
        fatal("The speech engine's packages are missing (huggingface_hub). Click Install again; it repairs the engine first.")
    say(f"{len(todo)} file(s) to fetch, {need / 1e9:.2f} GB")
    for i, (key, f) in enumerate(todo, 1):
        dest = models / key / f["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        twin = next((models / k / f["path"] for k in PACKS if k != key
                     and f["path"] in SHARED and _good(models / k / f["path"], f)), None)
        if twin is not None:
            dest.unlink(missing_ok=True)
            try:
                os.link(twin, dest)
            except OSError:
                shutil.copy2(twin, dest)
            say(f"{i}/{len(todo)} {key}/{f['path']} (linked to the identical copy)")
            continue
        say(f"{i}/{len(todo)} {key}/{f['path']} {f['size'] / 1e9:.2f} GB")
        repo, rev = PACKS[key]["repo"], PACKS[key]["revision"]
        got = None
        for attempt in range(1, 4):
            try:
                got = Path(hf_hub_download(repo, f["path"], revision=rev, local_dir=str(staging / key)))
                break
            except Exception as e:  # network blips: retry, then say what to do
                if attempt == 3:
                    fatal(f"Download of {f['path']} failed after 3 tries ({type(e).__name__}). "
                          "Check the connection and click Install again; finished files are kept.")
                say(f"retrying {f['path']} in {5 * attempt} s ({type(e).__name__})")
                time.sleep(5 * attempt)
        if sha256(got) != f["sha256"]:
            got.unlink(missing_ok=True)
            fatal(f"{f['path']} did not match its pinned checksum and was deleted. Click Install again to fetch it fresh.")
        os.replace(got, dest)
    shutil.rmtree(staging, ignore_errors=True)
    bad = [p for p in pack_problems(models)]
    if bad:
        fatal("The Speech pack is still incomplete: " + "; ".join(bad[:3]) + ". Click Install again.")
    write_receipt(models)
    say("done: Speech is ready")


def write_receipt(models: Path) -> None:
    (models / RECEIPT).write_text(json.dumps({
        "model": "Qwen3-TTS 12Hz 1.7B (CustomVoice + Base), 8-bit MLX",
        "license": "Apache-2.0",
        "sources": {k: {"repo": p["repo"], "revision": p["revision"], "upstream": p.get("upstream")}
                    for k, p in PACKS.items()},
        "files": {f"{k}/{f['path']}": f["sha256"] for k, p in PACKS.items() for f in p["files"]},
    }, indent=1))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--min-free-gb", type=float, default=2.0)
    a = ap.parse_args()
    models = models_dir(a.models)
    if a.check:
        bad = pack_problems(models)
        for b in bad:
            print(b)
        print("ok" if not bad else f"{len(bad)} problem(s)")
        return 0 if not bad else 1
    fetch(models, a.min_free_gb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
