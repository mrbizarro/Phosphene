#!/usr/bin/env python3
"""Turn LTX-2.5's single-file Gemma-4 text encoder into a loadable MLX pack.

LTX-2.3's encoder was a stock, ungated ``gemma-3-12b-it-4bit`` repo: weights,
``config.json`` and ``tokenizer.json`` as ordinary sibling files. LTX-2.5 ships
one 26 GB safetensors instead, and puts three different kinds of thing inside
it:

  1. the Gemma-4 text tower                    ``model.*``
  2. the DiT's dual text projection            ``text_embedding_projection.*``
  3. the tokenizer and its config, **as U8 tensors**
     (``tokenizer_json``, ``hf_asset__*.json``, ``hf_asset__*.jinja``)

plus vision/audio decoys from the unified-multimodal packaging that the text
tower must never load.

Only (1) belongs in the encoder pack. (2) belongs beside the connector and is
routed there by ``convert_ltx_mlx.py`` — do not also write it here, or the
projection exists twice and the two copies can drift. (3) are not weights at
all: they are extracted to real files, because ``mlx_lm.tokenizer_utils.load``
reads a directory, and because a tokenizer smuggled through a weights dict
would reach ``load_weights`` and fail as a phantom parameter.

The architecture lives in the header's ``gemma_config`` metadata rather than a
sibling ``config.json``; this writes it out so the pack is readable by the
ordinary path.

Same three rules as the pack converter: never load the file, plan before
writing, and never drop a key silently — every dropped tensor is named on
stdout and counted in the summary.

Usage
-----
    python3 scripts/build_gemma4_te_pack.py \\
        --input  mlx_models/_incoming_ltx25/gemma4-12b-with-proj-ltx-2.5-bf16.safetensors \\
        --output mlx_models/gemma4-12b-ltx25-bf16
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from convert_ltx_mlx import human, read_header, tensor_nbytes  # noqa: E402

#: Kept, with keys verbatim. ``sanitize_weights`` strips the ``model.`` prefix.
TOWER_PREFIXES: tuple[str, ...] = ("model.",)

#: Dropped on purpose. These are the encoder-free multimodal packaging's other
#: towers; the text tower never allocates them. Named here rather than implied
#: by a fallthrough, so the summary can prove what left.
DECOY_PREFIXES: tuple[str, ...] = (
    "vision_model.",
    "vision_tower.",
    "audio_tower.",
    "audio_projector.",
    "multi_modal_projector.",
)

#: Belongs to the LTX pack's connector, written by convert_ltx_mlx.py.
ELSEWHERE_PREFIXES: tuple[str, ...] = ("text_embedding_projection.",)

#: U8 byte blobs that are files, not tensors: source key -> filename on disk.
ASSET_FILENAMES: dict[str, str] = {
    "tokenizer_json": "tokenizer.json",
    "hf_asset__tokenizer_config.json": "tokenizer_config.json",
    "hf_asset__generation_config.json": "generation_config.json",
    "hf_asset__processor_config.json": "processor_config.json",
    "hf_asset__chat_template.jinja": "chat_template.jinja",
}

_CHUNK = 8 * 1024 * 1024


def asset_filename(key: str) -> str | None:
    if key in ASSET_FILENAMES:
        return ASSET_FILENAMES[key]
    if key.startswith("hf_asset__"):
        return key[len("hf_asset__") :]
    return None


def classify(key: str) -> str:
    if asset_filename(key) is not None:
        return "asset"
    if key.startswith(ELSEWHERE_PREFIXES):
        return "elsewhere"
    if key.startswith(DECOY_PREFIXES):
        return "decoy"
    if key.startswith(TOWER_PREFIXES):
        return "tower"
    return "unknown"


def build(input_path: Path, out_dir: Path, *, dry_run: bool = False) -> dict:
    header, data_offset = read_header(input_path)
    metadata = header.get("__metadata__") or {}

    buckets: dict[str, list[str]] = {"tower": [], "asset": [], "decoy": [], "elsewhere": [], "unknown": []}
    for key in header:
        if key == "__metadata__":
            continue
        buckets[classify(key)].append(key)

    if buckets["unknown"]:
        preview = "\n  ".join(sorted(buckets["unknown"])[:20])
        raise SystemExit(
            f"error: {len(buckets['unknown'])} tensors match no known category:\n  {preview}\n\n"
            "Refusing to guess. Add them to TOWER_PREFIXES, DECOY_PREFIXES or "
            "ASSET_FILENAMES — an unclassified tensor is either a parameter that "
            "would go missing or a phantom that would break a strict load."
        )

    tower = sorted(buckets["tower"])
    planned = sum(tensor_nbytes(header[k]) for k in tower)

    report = {
        "tower_tensors": len(tower),
        "tower_bytes": planned,
        "assets": {k: asset_filename(k) for k in sorted(buckets["asset"])},
        "dropped_decoys": sorted(buckets["decoy"]),
        "written_elsewhere": sorted(buckets["elsewhere"]),
        "files": [],
    }
    if dry_run:
        return report

    out_dir.mkdir(parents=True, exist_ok=True)

    # --- config.json, from the header's own metadata -----------------------
    raw_config = metadata.get("gemma_config") or metadata.get("config")
    if not raw_config:
        raise SystemExit("error: the encoder header carries no gemma_config; cannot write config.json")
    config = json.loads(raw_config) if isinstance(raw_config, str) else dict(raw_config)
    (out_dir / "config.json").write_text(json.dumps(config, indent=2, sort_keys=True))
    report["files"].append("config.json")

    # --- assets: byte blobs out to real files ------------------------------
    with open(input_path, "rb") as fh:
        for key in sorted(buckets["asset"]):
            name = asset_filename(key)
            begin, end = header[key]["data_offsets"]
            fh.seek(data_offset + begin)
            (out_dir / name).write_bytes(fh.read(end - begin))
            report["files"].append(name)
            print(f"  asset  {name}  {human(end - begin)}")

    # --- the tower, streamed ----------------------------------------------
    out_header: dict = {}
    cursor = 0
    for key in tower:
        entry = header[key]
        nbytes = tensor_nbytes(entry)
        out_header[key] = {"dtype": entry["dtype"], "shape": entry["shape"], "data_offsets": [cursor, cursor + nbytes]}
        cursor += nbytes
    quant = config.get("quantization")
    if quant:
        out_header["__metadata__"] = {"quantization": json.dumps(quant)}

    blob = json.dumps(out_header, separators=(",", ":")).encode()
    blob += b" " * ((-len(blob)) % 8)

    target = out_dir / "model.safetensors"
    digest = hashlib.sha256()
    written = 0
    fd, tmp = tempfile.mkstemp(prefix=".model.", suffix=".partial", dir=str(out_dir))
    try:
        with os.fdopen(fd, "wb") as out, open(input_path, "rb") as src:
            out.write(struct.pack("<Q", len(blob)))
            out.write(blob)
            digest.update(struct.pack("<Q", len(blob)))
            digest.update(blob)
            for i, key in enumerate(tower):
                begin, end = header[key]["data_offsets"]
                src.seek(data_offset + begin)
                remaining = end - begin
                while remaining:
                    chunk = src.read(min(_CHUNK, remaining))
                    if not chunk:
                        raise IOError(f"{input_path.name}: truncated reading {key}")
                    out.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    remaining -= len(chunk)
                if i % 100 == 0:
                    print(f"  [{i + 1}/{len(tower)}]  {human(written)}", flush=True)
            out.flush()
            os.fsync(out.fileno())
        if written != planned:
            raise IOError(f"wrote {written} bytes, planned {planned}")
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    report["files"].append("model.safetensors")
    report["sha256"] = digest.hexdigest()
    report["bytes"] = target.stat().st_size
    (out_dir / "te_pack_manifest.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    if not args.input.exists():
        print(f"error: {args.input} does not exist", file=sys.stderr)
        return 2

    report = build(args.input, args.output, dry_run=args.dry_run)

    print()
    print(f"tower            {report['tower_tensors']:5d} tensors  {human(report['tower_bytes'])}")
    print(f"assets           {len(report['assets']):5d} files    -> {', '.join(sorted(report['assets'].values()))}")
    print(f"dropped decoys   {len(report['dropped_decoys']):5d} tensors  {', '.join(report['dropped_decoys'][:4])}"
          f"{' ...' if len(report['dropped_decoys']) > 4 else ''}")
    print(f"written elsewhere{len(report['written_elsewhere']):5d} tensors  (connector, by convert_ltx_mlx.py)")
    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
