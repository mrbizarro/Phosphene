#!/usr/bin/env python3
"""Fetch the two models that let YuE2 cover a real recording.

Cover is the engine's most interesting mode and the cheapest to add: the
generator and decoder are already here (~10 GB), and listening to a record
costs **2.8 GB more**. SheetSage2 transcribes a mix into an ABC score;
MERT-v2-FullSong is the audio encoder it is a LoRA adapter over, merged on
load. Neither is needed to write a song from a prompt, so neither ships with
the music pack — this is an opt-in second download.

Pinned to the revisions the ENGINE pins (lyra/transcription/model.py), not to
whatever is newest: SheetSage2's config carries the sha256 of the MERT
checkpoint it was trained against and refuses to load beside any other one.
Bump both together with engine_pin.txt or not at all.

Layout, deliberately plain directories rather than an HF cache:

    mlx_models/yue2-cover/sheetsage2/{config.json,model.safetensors}
    mlx_models/yue2-cover/mert2/{config.json,model.safetensors}

`resolve_models()` takes a directory as readily as a repo id, so a plain tree
is offline by construction, checkable with `ls`, and deletable with `rm -rf`.

Both models are CC BY-NC 4.0, the same terms as YuE2 itself.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

SOURCES = {
    "sheetsage2": {"repo": "m-a-p/SheetSage2",
                   "revision": "eab522a8168e8b8b8c4856bf8609cd86198f01fe"},
    "mert2": {"repo": "m-a-p/MERT-v2-FullSong",
              "revision": "d8ba1c745e733b3908ce6ad16ebeb17ac7600a42"},
}
FILES = ("config.json", "model.safetensors")
EXTRA = ("LICENSE", "THIRD_PARTY_NOTICES.md", "README.md")
COVER_BYTES = 2_800_000_000


RECEIPT = "pack_source.json"


def _receipt(root: Path) -> dict | None:
    try:
        return json.loads((root / RECEIPT).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _file_ok(root: Path, rel: str, receipt: dict | None) -> bool:
    """A weight file counts only when the receipt vouches for it.

    "Nonzero size" was the whole check, so a copy cut short by a crash, a full
    disk or an unplugged drive left a truncated checkpoint that readiness
    accepted, a retry skipped and the in-panel installer reported "ready"
    (Codex INST-10). The receipt is written only after every file landed, and
    records each one's size. A receipt from before sizes were recorded still
    vouches for files that are present — those installs finished."""
    f = root / rel
    if receipt is None or not f.is_file() or f.stat().st_size == 0:
        return False
    want = (receipt.get("bytes") or {}).get(rel)
    return want is None or f.stat().st_size == int(want)


def part_problems(root: Path, part: str) -> list[str]:
    receipt = _receipt(root)
    return [f"{part}/{name}" for name in FILES
            if not _file_ok(root, f"{part}/{name}", receipt)]


def cover_problems(root: Path) -> list[str]:
    """Empty when a cover is possible. The panel's install card reads this."""
    out: list[str] = []
    for part in SOURCES:
        out += part_problems(root, part)
    return out


def fetch(root: Path, *, min_free_gb: float = 6.0) -> None:
    from huggingface_hub import hf_hub_download

    root.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(root).free / 1e9
    if free < min_free_gb:
        raise SystemExit(f"[cover] only {free:.1f} GB free; need about {min_free_gb:g} GB")
    receipt = _receipt(root)
    sizes: dict[str, int] = {}
    for part, src in SOURCES.items():
        target = root / part
        target.mkdir(parents=True, exist_ok=True)
        # The two weight files are what the loader reads. The licence and
        # notices ride along because we are keeping a copy of someone else's
        # model on a user's disk, and the terms belong next to it.
        for name in FILES + EXTRA:
            dest = target / name
            rel = f"{part}/{name}"
            if _file_ok(root, rel, receipt):
                print(f"[cover] have {rel}", flush=True)
                if name in FILES:
                    sizes[rel] = dest.stat().st_size
                continue
            try:
                staged = hf_hub_download(repo_id=src["repo"], revision=src["revision"],
                                         filename=name)
            except Exception as exc:                            # noqa: BLE001
                if name in EXTRA:
                    continue        # a missing notice is not a failed install
                raise SystemExit(f"[cover] could not fetch {part}/{name}: {exc}") from exc
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Sibling temp file, size-checked, then an atomic rename: the final
            # name only ever holds a complete copy (INST-10).
            part_file = dest.with_name(dest.name + ".part")
            shutil.copyfile(staged, part_file)
            if part_file.stat().st_size != Path(staged).stat().st_size:
                part_file.unlink(missing_ok=True)
                raise SystemExit(f"[cover] short copy of {rel}; run it again")
            os.replace(part_file, dest)
            if name in FILES:
                sizes[rel] = dest.stat().st_size
            print(f"[cover] wrote {rel} "
                  f"({dest.stat().st_size / 1e6:.1f} MB)", flush=True)
    tmp = root / (RECEIPT + ".tmp")
    tmp.write_text(json.dumps(
        {"sources": SOURCES, "files": list(FILES), "bytes": sizes,
         "license": "CC BY-NC 4.0",
         "purpose": "YuE2 cover: transcribe a recording into an ABC score"},
        indent=2), encoding="utf-8")
    os.replace(tmp, root / RECEIPT)
    missing = cover_problems(root)
    if missing:
        raise SystemExit(f"[cover] incomplete after fetch: {', '.join(missing)}")
    print("[cover] ready", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--min-free-gb", type=float, default=6.0)
    ap.add_argument("--check", action="store_true", help="report readiness and exit")
    args = ap.parse_args(argv)
    if args.check:
        missing = cover_problems(args.root)
        print(json.dumps({"ready": not missing, "missing": missing}))
        return 0 if not missing else 1
    fetch(args.root, min_free_gb=args.min_free_gb)
    return 0


if __name__ == "__main__":
    sys.exit(main())
