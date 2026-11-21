#!/usr/bin/env python3
"""One-time setup for H3 "Write it for me" (see h3_rewriter.py).

1. Download the publisher's checkpoint at a pinned revision (resumable; the
   weights are fetched from LightX2V's own repo, never re-hosted).
2. Convert it to MLX on this Mac with mlx-lm (text model only: mlx-lm drops the
   vision tower), quantized to 8 or 4 bits.
3. Put the upstream LICENSE and MiniMax's writing guide beside the converted
   weights, plus a NOTICE naming the source and what was changed.
4. Write the completion marker LAST, move the folder into place, and delete
   the download.

Runs in the LTX engine venv. Prints `PCT <n>` lines for the panel's progress
and plain lines for the log. Exit 0 only when the converted model is in place.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

ALLOW = [
    "LICENSE", "README.md", "VIDEO_PROMPT_WRITING_GUIDE_base_en.md",
    "chat_template.jinja", "config.json", "generation_config.json",
    "model-0000*-of-00004.safetensors", "model.safetensors.index.json",
    "tokenizer.json", "tokenizer_config.json",
    "preprocessor_config.json", "processor_config.json", "video_preprocessor_config.json",
]
CARRY = ["LICENSE", "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"]
MARKER = "phosphene_rewriter.json"
DOWNLOAD_BYTES = 18_820_000_000


def say(msg: str) -> None:
    print(msg, flush=True)


def dir_bytes(p: Path) -> int:
    total = 0
    for f in p.rglob("*"):
        try:
            if f.is_file():
                total += f.stat().st_size
        except OSError:
            pass
    return total


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest", required=True)
    ap.add_argument("--repo", required=True)
    ap.add_argument("--revision", required=True)
    ap.add_argument("--bits", type=int, choices=(4, 8), default=8)
    ap.add_argument("--wait-gpu", action="store_true")
    a = ap.parse_args()
    dest = Path(a.dest)
    work = dest.parent / (dest.name + ".download")
    building = dest.parent / (dest.name + ".building")
    dest.parent.mkdir(parents=True, exist_ok=True)

    say(f"downloading the prompt writer from {a.repo} (about {DOWNLOAD_BYTES / 1e9:.0f} GB)")
    done = threading.Event()

    def progress() -> None:
        while not done.wait(3.0):
            pct = min(89, int(90 * dir_bytes(work) / DOWNLOAD_BYTES))
            say(f"PCT {pct}")

    threading.Thread(target=progress, daemon=True).start()
    try:
        from huggingface_hub import snapshot_download
        snapshot_download(repo_id=a.repo, revision=a.revision, local_dir=str(work),
                          allow_patterns=ALLOW)
    except Exception as exc:                                    # noqa: BLE001
        done.set()
        say(f"FATAL error: the download stopped ({type(exc).__name__}: {exc}). "
            "Press Write it for me again to resume.")
        return 1
    done.set()
    say("PCT 90")

    if a.wait_gpu:
        # The conversion is an MLX job: wait until the Studio says no render holds the GPU.
        say("WAITGPU")
        if sys.stdin.readline().strip() != "go":
            say("FATAL error: the setup was stopped before converting. Press Write it for me again to resume.")
            return 1
    say(f"converting it for this Mac ({a.bits}-bit, a few minutes)")
    shutil.rmtree(building, ignore_errors=True)
    try:
        from mlx_lm import convert
        convert(hf_path=str(work), mlx_path=str(building), quantize=True, q_bits=a.bits)
    except Exception as exc:                                    # noqa: BLE001
        shutil.rmtree(building, ignore_errors=True)
        say(f"FATAL error: the conversion failed ({type(exc).__name__}: {exc}).")
        return 1
    say("PCT 98")

    for name in CARRY:
        src = work / name
        if not src.is_file():
            say(f"FATAL error: the download has no {name}.")
            return 1
        shutil.copy2(src, building / name)
    (building / "NOTICE.md").write_text(
        "# H3 prompt writer (converted for Phosphene)\n\n"
        f"Source: https://huggingface.co/{a.repo} at revision {a.revision},\n"
        "by LightX2V, licensed under the Apache License 2.0 (LICENSE, beside this file).\n\n"
        "Modified by Phosphene on this Mac: converted to MLX with mlx-lm, the vision tower\n"
        f"dropped, the weights quantized to {a.bits} bits. "
        "VIDEO_PROMPT_WRITING_GUIDE_base_en.md is unchanged.\n",
        encoding="utf-8")
    (building / MARKER).write_text(json.dumps({
        "repo": a.repo, "revision": a.revision, "bits": a.bits,
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }, indent=1), encoding="utf-8")
    if dest.exists():
        shutil.rmtree(dest)
    os.replace(building, dest)
    shutil.rmtree(work, ignore_errors=True)
    say("PCT 100")
    say(f"the prompt writer is ready ({dir_bytes(dest) / 1e9:.1f} GB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
