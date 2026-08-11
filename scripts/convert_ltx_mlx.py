#!/usr/bin/env python3
"""Convert an official LTX safetensors release into the MLX pack layout.

This is the missing link between "the bf16 weights finished downloading" and
`scripts/quantize_ltx.py`. Keep the boundary sharp, because conflating the two
is how packs get silently wrong:

    convert_ltx_mlx.py   changes KEY NAMES and FILE LAYOUT. Never numbers.
    quantize_ltx.py      changes NUMBERS. Never key names.

Input is whichever shape the vendor shipped:

  * a **monolithic** checkpoint — the whole model in one file under
    ``model.diffusion_model.*`` (the ComfyUI single-file layout), or
  * a set of **already-split** component files (the Diffusers-style repo).

Output is the flat, one-file-per-component pack the vendored loader consumes,
because that layout — not any tidier scheme — is the compatibility contract:

    transformer-distilled.safetensors   keys prefixed ``transformer.``
    connector.safetensors               keys prefixed ``connector.``
    vae_encoder.safetensors             ``vae_encoder.``
    vae_decoder.safetensors             ``vae_decoder.``
    audio_vae.safetensors               ``audio_vae.``
    vocoder.safetensors                 ``vocoder.``
    duration_head.safetensors           ``duration_head.``     (2.5, optional)
    config.json / embedded_config.json / split_model.json
    manifest.json                       sha256 + size for every file written

Three properties this script treats as non-negotiable, each bought with real
scar tissue:

**It never loads the file.** A 42 GB transformer is streamed tensor by tensor
through a bounded buffer. Peak RSS is one tensor, not one checkpoint — the
same discipline that lets `quantize_ltx.py` run in 1.6 GB.

**It plans before it writes.** The source header (8-byte LE length + JSON) is
parsed first, so every output tensor's name, dtype, shape and byte count is
known in closed form before a single weight byte moves. That gives an exact
disk preflight, a header that can be written first, and a final
``wrote N bytes, planned M`` assertion that makes a truncated pack impossible
to publish.

**An unmapped key is an ERROR, not a skip.** The June 2026 "mosaic" was one
missing 1 GB upscaler producing a randomly-initialised module and a rainbow
grid — chased for two weeks through three wrong theories. A converter that
quietly drops a key it does not recognise recreates that failure exactly. So
anything unrouted or unrenamed aborts the run and names itself. ``--allow-
unmapped`` exists for exploration and is refused for a real conversion unless
paired with ``--i-know-what-im-dropping``.

Usage
-----
    # monolithic 2.5 release -> MLX pack
    python3 scripts/convert_ltx_mlx.py \\
        --input  ~/dl/ltx-2.5-22b-distilled-transformer-bf16.safetensors \\
        --output mlx_models/ltx-2.5-mlx-bf16

    # add the separately-shipped components
    python3 scripts/convert_ltx_mlx.py \\
        --input ~/dl/ltx-2.5-video-vae-conv-bf16.safetensors \\
        --input ~/dl/ltx-2.5-audio-vae-bf16.safetensors \\
        --input ~/dl/ltx-2.5-duration-head-bf16.safetensors \\
        --output mlx_models/ltx-2.5-mlx-bf16 --merge

    # see the routing decision without writing anything
    python3 scripts/convert_ltx_mlx.py --input X.safetensors --output OUT --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# safetensors header primitives — deliberately dependency-free.
#
# `mx.load` would pull the whole file into memory, and `safetensors.safe_open`
# is not guaranteed present in the Pinokio venv. The format is small enough to
# own: 8-byte little-endian header length, then that many bytes of JSON, then
# a contiguous data blob addressed by per-tensor [begin, end) byte offsets.
# ---------------------------------------------------------------------------

DTYPE_SIZES = {
    "BOOL": 1, "U8": 1, "I8": 1, "F8_E4M3": 1, "F8_E5M2": 1,
    "U16": 2, "I16": 2, "F16": 2, "BF16": 2,
    "U32": 4, "I32": 4, "F32": 4,
    "U64": 8, "I64": 8, "F64": 8,
}


def read_header(path: Path) -> tuple[dict, int]:
    """Return ``(header_dict, data_offset)`` for a safetensors file."""
    with open(path, "rb") as fh:
        raw_len = fh.read(8)
        if len(raw_len) != 8:
            raise ValueError(f"{path}: too short to be safetensors")
        (header_len,) = struct.unpack("<Q", raw_len)
        header = json.loads(fh.read(header_len))
    return header, 8 + header_len


def tensor_nbytes(entry: dict) -> int:
    size = DTYPE_SIZES.get(entry["dtype"])
    if size is None:
        raise ValueError(f"unknown safetensors dtype {entry['dtype']!r}")
    n = size
    for dim in entry["shape"]:
        n *= dim
    return n


# ---------------------------------------------------------------------------
# Key renaming
#
# These rules are not invented here — they mirror ``LTXV_LORA_COMFY_RENAMING_MAP``
# in the vendored ltx-core-mlx loader, which is the rename table the library
# already trusts for ComfyUI-shaped LTX weights. Reusing it means a LoRA and a
# base checkpoint cannot disagree about what a layer is called.
# ---------------------------------------------------------------------------

RENAME_RULES: tuple[tuple[str, str], ...] = (
    (".to_out.0.", ".to_out."),
    (".ff.net.0.proj.", ".ff.proj_in."),
    (".ff.net.2.", ".ff.proj_out."),
    (".audio_ff.net.0.proj.", ".audio_ff.proj_in."),
    (".audio_ff.net.2.", ".audio_ff.proj_out."),
    (".linear_1.", ".linear1."),
    (".linear_2.", ".linear2."),
)

# Source-key prefixes that identify a component, longest first so the more
# specific prefix always wins. Each maps to (output stem, output key prefix).
@dataclass(frozen=True)
class Route:
    source_prefix: str
    stem: str
    target_prefix: str


DEFAULT_ROUTES: tuple[Route, ...] = (
    Route("model.diffusion_model.duration_head.", "duration_head", "duration_head."),
    Route("duration_head.", "duration_head", "duration_head."),
    Route("model.diffusion_model.video_embeddings_connector.", "connector", "connector.video_embeddings_connector."),
    Route("model.diffusion_model.audio_embeddings_connector.", "connector", "connector.audio_embeddings_connector."),
    Route("text_embedding_projection.", "connector", "connector.text_embedding_projection."),
    Route("connector.", "connector", "connector."),
    Route("vae.encoder.", "vae_encoder", "vae_encoder."),
    Route("vae.decoder.", "vae_decoder", "vae_decoder."),
    Route("vae_encoder.", "vae_encoder", "vae_encoder."),
    Route("vae_decoder.", "vae_decoder", "vae_decoder."),
    Route("encoder.", "vae_encoder", "vae_encoder."),
    Route("decoder.", "vae_decoder", "vae_decoder."),
    Route("audio_vae.", "audio_vae", "audio_vae."),
    Route("vocoder.", "vocoder", "vocoder."),
    Route("model.diffusion_model.", "transformer", "transformer."),
    Route("diffusion_model.", "transformer", "transformer."),
    Route("transformer.", "transformer", "transformer."),
)


def rename_key(key: str) -> str:
    for old, new in RENAME_RULES:
        key = key.replace(old, new)
    return key


def route_key(key: str, routes: tuple[Route, ...]) -> Route | None:
    """Longest-prefix match. Returns ``None`` when nothing claims the key."""
    best: Route | None = None
    for route in routes:
        if key.startswith(route.source_prefix):
            if best is None or len(route.source_prefix) > len(best.source_prefix):
                best = route
    return best


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


@dataclass
class PlannedTensor:
    target_key: str
    source_key: str
    source_path: Path
    source_begin: int
    source_end: int
    dtype: str
    shape: list

    @property
    def nbytes(self) -> int:
        return self.source_end - self.source_begin


@dataclass
class ComponentPlan:
    stem: str
    tensors: list[PlannedTensor] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def nbytes(self) -> int:
        return sum(t.nbytes for t in self.tensors)


class UnmappedKeys(RuntimeError):
    """Raised when the converter cannot account for every source tensor."""


def plan_conversion(
    inputs: list[Path],
    routes: tuple[Route, ...] = DEFAULT_ROUTES,
    *,
    transformer_stem: str = "transformer",
    allow_unmapped: bool = False,
) -> tuple[dict[str, ComponentPlan], list[str], dict]:
    """Decide every output byte before reading a single weight.

    Returns ``(plans_by_stem, unmapped_source_keys, source_metadata)``.
    """
    plans: dict[str, ComponentPlan] = {}
    unmapped: list[str] = []
    merged_metadata: dict = {}

    for path in inputs:
        header, data_offset = read_header(path)
        metadata = header.get("__metadata__") or {}
        for key, value in metadata.items():
            merged_metadata.setdefault(key, value)

        for key, entry in header.items():
            if key == "__metadata__":
                continue
            route = route_key(key, routes)
            if route is None:
                unmapped.append(f"{path.name}::{key}")
                continue

            stem = transformer_stem if route.stem == "transformer" else route.stem
            suffix = key[len(route.source_prefix) :]
            target_key = rename_key(route.target_prefix + suffix)

            begin, end = entry["data_offsets"]
            plan = plans.setdefault(stem, ComponentPlan(stem=stem))
            plan.tensors.append(
                PlannedTensor(
                    target_key=target_key,
                    source_key=key,
                    source_path=path,
                    source_begin=data_offset + begin,
                    source_end=data_offset + end,
                    dtype=entry["dtype"],
                    shape=list(entry["shape"]),
                )
            )
            expected = tensor_nbytes(entry)
            actual = end - begin
            if expected != actual:
                raise ValueError(
                    f"{path.name}::{key} header is self-inconsistent: "
                    f"{entry['dtype']}{entry['shape']} needs {expected} bytes, "
                    f"offsets span {actual}. Refusing to convert a corrupt file."
                )

    if unmapped and not allow_unmapped:
        preview = "\n  ".join(unmapped[:20])
        more = f"\n  ... and {len(unmapped) - 20} more" if len(unmapped) > 20 else ""
        raise UnmappedKeys(
            f"{len(unmapped)} source tensors matched no output component:\n  "
            f"{preview}{more}\n\n"
            "Dropping them would produce a pack that LOADS and renders garbage — "
            "that is the June 2026 mosaic, exactly. Add a Route for these keys, "
            "or re-run with --allow-unmapped --i-know-what-im-dropping if you "
            "genuinely mean to discard them."
        )

    for plan in plans.values():
        plan.tensors.sort(key=lambda t: t.target_key)

    return plans, unmapped, merged_metadata


def duplicate_target_keys(plan: ComponentPlan) -> list[str]:
    seen: dict[str, int] = {}
    for tensor in plan.tensors:
        seen[tensor.target_key] = seen.get(tensor.target_key, 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------

_COPY_CHUNK = 8 * 1024 * 1024


def write_component(
    plan: ComponentPlan,
    out_dir: Path,
    *,
    metadata: dict | None = None,
    chunk_size: int = _COPY_CHUNK,
) -> tuple[Path, str, int]:
    """Stream one component to disk atomically. Returns (path, sha256, size).

    Written to a temp file in the destination directory and ``os.replace``d
    into position, so a killed run never leaves a half-written pack that a
    later verify would happily checksum.
    """
    dupes = duplicate_target_keys(plan)
    if dupes:
        raise ValueError(
            f"{plan.stem}: {len(dupes)} target keys collide, e.g. {dupes[:5]}. "
            "Two source tensors renamed onto the same name — one would silently "
            "overwrite the other."
        )

    header: dict = {}
    cursor = 0
    for tensor in plan.tensors:
        header[tensor.target_key] = {
            "dtype": tensor.dtype,
            "shape": tensor.shape,
            "data_offsets": [cursor, cursor + tensor.nbytes],
        }
        cursor += tensor.nbytes
    if metadata:
        header["__metadata__"] = {k: str(v) for k, v in metadata.items()}

    header_bytes = json.dumps(header, separators=(",", ":")).encode()
    # safetensors requires the data blob to start 8-byte aligned.
    pad = (-len(header_bytes)) % 8
    header_bytes += b" " * pad

    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"{plan.stem}.safetensors"
    digest = hashlib.sha256()
    written = 0

    fd, tmp_path = tempfile.mkstemp(prefix=f".{plan.stem}.", suffix=".partial", dir=str(out_dir))
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(struct.pack("<Q", len(header_bytes)))
            out.write(header_bytes)
            digest.update(struct.pack("<Q", len(header_bytes)))
            digest.update(header_bytes)

            # Group by source file so each is opened once, in offset order.
            by_source: dict[Path, list[PlannedTensor]] = {}
            for tensor in plan.tensors:
                by_source.setdefault(tensor.source_path, []).append(tensor)

            # Emission order must match the header, so walk the planned order
            # and seek — sequential within a file in practice, because the
            # vendor writes tensors in roughly the same order we sort them.
            handles = {path: open(path, "rb") for path in by_source}
            try:
                for tensor in plan.tensors:
                    fh = handles[tensor.source_path]
                    fh.seek(tensor.source_begin)
                    remaining = tensor.nbytes
                    while remaining:
                        chunk = fh.read(min(chunk_size, remaining))
                        if not chunk:
                            raise IOError(
                                f"{tensor.source_path.name}: truncated while reading "
                                f"{tensor.source_key} ({remaining} bytes short)"
                            )
                        out.write(chunk)
                        digest.update(chunk)
                        written += len(chunk)
                        remaining -= len(chunk)
            finally:
                for fh in handles.values():
                    fh.close()

            out.flush()
            os.fsync(out.fileno())

        if written != plan.nbytes:
            raise IOError(f"{plan.stem}: wrote {written} bytes, planned {plan.nbytes}")
        os.replace(tmp_path, target)
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target, digest.hexdigest(), target.stat().st_size


def write_json_sidecars(out_dir: Path, metadata: dict, plans: dict[str, ComponentPlan]) -> list[Path]:
    """Emit the JSON files the vendored loader looks for beside the weights.

    2.5 carries its architecture in the safetensors ``__metadata__``; the
    vendored ``LTXModelConfig.from_checkpoint_dir`` reads ``config.json`` /
    ``embedded_config.json``. Materialising both from the header keeps a 2.5
    pack readable by code that predates header-config support.
    """
    written: list[Path] = []
    config = {}
    raw = metadata.get("config")
    if raw:
        try:
            config = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError):
            config = {}
    if "model_version" in metadata and "model_version" not in config:
        config["model_version"] = metadata["model_version"]

    if config:
        for name in ("config.json", "embedded_config.json"):
            path = out_dir / name
            path.write_text(json.dumps(config, indent=2))
            written.append(path)

    split = {
        "components": sorted(plans),
        "quantized": False,
        "quantization_bits": None,
        "model_version": config.get("model_version"),
    }
    path = out_dir / "split_model.json"
    path.write_text(json.dumps(split, indent=2))
    written.append(path)
    return written


def write_manifest(out_dir: Path, entries: list[dict]) -> Path:
    path = out_dir / "manifest.json"
    path.write_text(json.dumps({"files": entries}, indent=2))
    return path


def sha256_file(path: Path, chunk_size: int = _COPY_CHUNK) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def human(n: int) -> str:
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(n) < 1024 or unit == "TiB":
            return f"{n:.2f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n}"


def convert(
    inputs: list[Path],
    out_dir: Path,
    *,
    transformer_stem: str = "transformer",
    allow_unmapped: bool = False,
    dry_run: bool = False,
) -> dict:
    plans, unmapped, metadata = plan_conversion(
        inputs, transformer_stem=transformer_stem, allow_unmapped=allow_unmapped
    )

    total = sum(plan.nbytes for plan in plans.values())
    report = {
        "components": {stem: {"tensors": len(p.tensors), "bytes": p.nbytes} for stem, p in sorted(plans.items())},
        "total_bytes": total,
        "unmapped": unmapped,
        "metadata_keys": sorted(metadata),
        "files": [],
    }

    if dry_run:
        return report

    free = os.statvfs(out_dir if out_dir.exists() else out_dir.parent)
    available = free.f_bavail * free.f_frsize
    if available < total * 1.02:
        raise IOError(
            f"not enough space in {out_dir}: need ~{human(int(total * 1.02))}, "
            f"have {human(available)}"
        )

    entries = []
    for stem, plan in sorted(plans.items()):
        component_metadata = metadata if stem == transformer_stem else None
        path, digest, size = write_component(plan, out_dir, metadata=component_metadata)
        entries.append({"file": path.name, "sha256": digest, "bytes": size, "tensors": len(plan.tensors)})
        print(f"  wrote {path.name}  {len(plan.tensors)} tensors  {human(size)}")

    for path in write_json_sidecars(out_dir, metadata, plans):
        entries.append({"file": path.name, "sha256": sha256_file(path), "bytes": path.stat().st_size})

    write_manifest(out_dir, entries)
    report["files"] = entries
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", action="append", required=True, type=Path, help="source safetensors (repeatable)")
    parser.add_argument("--output", required=True, type=Path, help="destination pack directory")
    parser.add_argument(
        "--transformer-stem",
        default="transformer-distilled",
        help="output filename stem for the DiT (default: transformer-distilled; "
        "use transformer-dev for the full/trainable checkpoint)",
    )
    parser.add_argument("--dry-run", action="store_true", help="plan and report, write nothing")
    parser.add_argument("--allow-unmapped", action="store_true", help="do not abort on unroutable keys")
    parser.add_argument(
        "--i-know-what-im-dropping",
        action="store_true",
        help="required alongside --allow-unmapped for a real (non-dry-run) conversion",
    )
    args = parser.parse_args(argv)

    for path in args.input:
        if not path.exists():
            print(f"error: {path} does not exist", file=sys.stderr)
            return 2

    if args.allow_unmapped and not args.dry_run and not args.i_know_what_im_dropping:
        print(
            "error: --allow-unmapped on a real conversion also needs "
            "--i-know-what-im-dropping. Silently discarded weights are the "
            "single most expensive class of bug in this project's history.",
            file=sys.stderr,
        )
        return 2

    try:
        report = convert(
            args.input,
            args.output,
            transformer_stem=args.transformer_stem,
            allow_unmapped=args.allow_unmapped,
            dry_run=args.dry_run,
        )
    except UnmappedKeys as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print()
    for stem, info in report["components"].items():
        print(f"{stem:24s} {info['tensors']:6d} tensors  {human(info['bytes'])}")
    print(f"{'TOTAL':24s} {'':6s}          {human(report['total_bytes'])}")
    if report["unmapped"]:
        print(f"\nWARNING: {len(report['unmapped'])} unmapped source tensors were dropped.", file=sys.stderr)
    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
