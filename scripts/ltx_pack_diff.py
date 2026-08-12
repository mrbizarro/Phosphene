#!/usr/bin/env python3
"""Diff one LTX pack's tensor CONTRACT against a reference pack's.

This is the standing gate the LTX-2.5 build kept re-deriving by hand. Every
time a pack was built, the thing that actually caught the bugs was not a test
written alongside the code — it was diffing the new pack's key set, shapes and
dtypes against the shipped LTX-2.3 pack, which is ground truth for MLX layout.
Three of the four conversion bugs on weights day were invisible to the
converter's own routing report and visible here. So it lives in ``scripts/``
with a test, not in a scratchpad someone remembers to run.

    convert_ltx_mlx.py   changes KEY NAMES and FILE LAYOUT.
    quantize_ltx.py      changes NUMBERS.
    ltx_pack_diff.py     changes NOTHING. It reads headers and refuses.

**Header-only.** It parses the 8-byte length + JSON header of each safetensors
file and never touches a weight byte, so diffing a 44 GB pack costs
milliseconds and a few MB of RAM. That is deliberate: a gate that is expensive
does not get run.

What it compares, per file present in both packs:

  * key sets            → ``only in new`` / ``only in ref``
  * shapes on shared keys   → **always a failure**
  * dtypes on shared keys   → **always a failure**

Key-set differences are *reported* by default and *enforced* the moment you
declare what you expect. Pass ``--allow-only-new`` / ``--allow-only-ref`` with
collapsed patterns (``transformer_blocks.N.…`` stands for every numbered
block) and any key difference NOT covered by a pattern becomes a failure. That
turns "read the output and squint" into a contract the CI-shaped thing can
assert. The 2.5-vs-2.3 DiT contract, for the record, is exactly:

    --allow-only-new  transformer.keyframes_abs_pos_embedding \\
    --allow-only-ref  transformer.transformer_blocks.N.ff.proj_in.bias \\
    --allow-only-ref  transformer.transformer_blocks.N.ff.proj_out.bias

Usage
-----
    # whole pack against the 2.3 pack of the same bit width
    python3 scripts/ltx_pack_diff.py \\
        mlx_models/ltx-2.3-mlx-q8 mlx_models/ltx-2.5-mlx-q8

    # a file that changed names between generations (ref=new)
    python3 scripts/ltx_pack_diff.py REF NEW \\
        --map transformer-dev.safetensors=transformer-distilled.safetensors

    # the same pack twice, to prove two variants share one architecture
    python3 scripts/ltx_pack_diff.py PACK PACK \\
        --map transformer-distilled.safetensors=transformer-dev.safetensors \\
        --strict-keys

Exit codes: ``0`` clean · ``1`` a mismatch or an undeclared key difference ·
``2`` bad invocation (no comparable files, missing directory).
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from collections import Counter
from pathlib import Path

__all__ = [
    "collapse_key",
    "collapse_keys",
    "diff_file",
    "diff_packs",
    "pair_files",
    "read_header",
]


# ---------------------------------------------------------------------------
# safetensors header — deliberately dependency-free, same primitive the
# converter and the quantizer own. `mx.load` would read the whole file.
# ---------------------------------------------------------------------------


def read_header(path: Path) -> dict:
    """Return the tensor index of a safetensors file, without ``__metadata__``.

    Raises ``ValueError`` on a file too short or with an unparseable header —
    a truncated download must not read as "a pack with no tensors".
    """
    with open(path, "rb") as fh:
        raw_len = fh.read(8)
        if len(raw_len) != 8:
            raise ValueError(f"{path}: too short to be safetensors")
        (header_len,) = struct.unpack("<Q", raw_len)
        blob = fh.read(header_len)
    if len(blob) != header_len:
        raise ValueError(f"{path}: header truncated ({len(blob)} of {header_len} bytes)")
    index = json.loads(blob)
    if not isinstance(index, dict):
        raise ValueError(f"{path}: header is not a JSON object")
    index.pop("__metadata__", None)
    return index


_NUMBERED = re.compile(r"\.\d+\.")


def collapse_key(key: str) -> str:
    """``…transformer_blocks.7.ff.proj_in.bias`` → ``…transformer_blocks.N.ff.proj_in.bias``.

    48 blocks' worth of the same structural difference is one fact, not 48.
    Collapsing is what lets a human-writable ``--allow-*`` pattern cover a
    whole stack without a wildcard language.
    """
    return _NUMBERED.sub(".N.", key)


def collapse_keys(keys) -> list[tuple[str, int]]:
    """Collapsed patterns with their counts, sorted by pattern."""
    return sorted(Counter(collapse_key(k) for k in keys).items())


# ---------------------------------------------------------------------------
# the diff
# ---------------------------------------------------------------------------


def diff_file(ref: Path, new: Path) -> dict:
    """Compare two safetensors files by header alone."""
    a, b = read_header(ref), read_header(new)
    ka, kb = set(a), set(b)
    shared = ka & kb
    return {
        "ref": ref.name,
        "new": new.name,
        "n_ref": len(ka),
        "n_new": len(kb),
        "shared": len(shared),
        "only_new": sorted(kb - ka),
        "only_ref": sorted(ka - kb),
        "shape_mm": [
            (k, a[k]["shape"], b[k]["shape"]) for k in sorted(shared) if a[k]["shape"] != b[k]["shape"]
        ],
        "dtype_mm": [
            (k, a[k]["dtype"], b[k]["dtype"]) for k in sorted(shared) if a[k]["dtype"] != b[k]["dtype"]
        ],
    }


def pair_files(ref_dir: Path, new_dir: Path, mapping: dict[str, str] | None = None) -> list[tuple[Path, Path]]:
    """Which files to compare.

    Explicit ``--map ref=new`` pairs first, in the order given, then every
    ``*.safetensors`` in the new pack that has a same-named sibling in the
    reference pack. A file present in only one pack is not a mismatch — it is
    what ``--strict-files`` is for, and callers usually want the shared subset.
    """
    pairs: list[tuple[Path, Path]] = []
    seen: set[Path] = set()
    for ref_name, new_name in (mapping or {}).items():
        pairs.append((ref_dir / ref_name, new_dir / (new_name or ref_name)))
        seen.add(new_dir / (new_name or ref_name))
    for path in sorted(new_dir.glob("*.safetensors")):
        if path in seen:
            continue
        sibling = ref_dir / path.name
        if sibling.exists():
            pairs.append((sibling, path))
    return pairs


def _uncovered(keys, patterns: list[str]) -> list[tuple[str, int]]:
    """Collapsed key patterns not named by any allow pattern."""
    allowed = set(patterns)
    return [(pat, count) for pat, count in collapse_keys(keys) if pat not in allowed]


def diff_packs(
    ref_dir: Path,
    new_dir: Path,
    *,
    mapping: dict[str, str] | None = None,
    allow_only_new: list[str] | None = None,
    allow_only_ref: list[str] | None = None,
    strict_keys: bool = False,
) -> dict:
    """Diff every comparable file and decide pass/fail.

    ``ok`` is False when any shape or dtype disagrees on a shared key — that is
    unconditional, because a shape difference means the two packs cannot be
    loaded by the same code and no allowlist should be able to wave it through.

    Key-set differences fail only when a contract was declared: any
    ``--allow-*`` pattern, or ``--strict-keys`` (which declares the empty
    contract, i.e. the key sets must be identical).
    """
    allow_only_new = list(allow_only_new or [])
    allow_only_ref = list(allow_only_ref or [])
    enforce = strict_keys or bool(allow_only_new) or bool(allow_only_ref)

    pairs = pair_files(ref_dir, new_dir, mapping)
    files, mismatches, undeclared = [], 0, 0
    for ref, new in pairs:
        entry = diff_file(ref, new)
        entry["uncovered_new"] = _uncovered(entry["only_new"], allow_only_new) if enforce else []
        entry["uncovered_ref"] = _uncovered(entry["only_ref"], allow_only_ref) if enforce else []
        mismatches += len(entry["shape_mm"]) + len(entry["dtype_mm"])
        undeclared += sum(c for _, c in entry["uncovered_new"]) + sum(c for _, c in entry["uncovered_ref"])
        files.append(entry)

    return {
        "ref_dir": str(ref_dir),
        "new_dir": str(new_dir),
        "compared": len(files),
        "files": files,
        "mismatches": mismatches,
        "undeclared_keys": undeclared,
        "enforced": enforce,
        "ok": mismatches == 0 and undeclared == 0,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def render(report: dict) -> str:
    lines = []
    for entry in report["files"]:
        lines.append(f"\n=== {entry['new']}   vs ref {entry['ref']} ===")
        lines.append(f"  keys: ref {entry['n_ref']}   new {entry['n_new']}   shared {entry['shared']}")
        lines.append(f"  shape mismatches on shared tensors: {len(entry['shape_mm'])}")
        lines.append(f"  dtype mismatches on shared tensors: {len(entry['dtype_mm'])}")
        for key, sa, sb in entry["shape_mm"][:12]:
            lines.append(f"    SHAPE  {key}: ref {sa}  new {sb}")
        for key, da, db in entry["dtype_mm"][:12]:
            lines.append(f"    DTYPE  {key}: ref {da}  new {db}")
        for label, bucket, uncovered in (
            ("ONLY in new", entry["only_new"], entry["uncovered_new"]),
            ("ONLY in ref", entry["only_ref"], entry["uncovered_ref"]),
        ):
            if not bucket:
                continue
            lines.append(f"  {label} ({len(bucket)}):")
            undeclared = {pat for pat, _ in uncovered}
            for pat, count in collapse_keys(bucket):
                flag = "  <- UNDECLARED" if pat in undeclared else ""
                lines.append(f"    {count:5d}  {pat}{flag}")
    lines.append(
        f"\ncompared {report['compared']} file(s): "
        f"{report['mismatches']} shape+dtype mismatch(es), "
        f"{report['undeclared_keys']} undeclared key difference(s)"
        + ("" if report["enforced"] else "  (key sets reported only — pass --strict-keys or --allow-* to enforce)")
    )
    lines.append("RESULT: " + ("PASS" if report["ok"] else "FAIL"))
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("ref_dir", type=Path, help="reference pack directory (ground truth, e.g. the 2.3 pack)")
    parser.add_argument("new_dir", type=Path, help="pack directory under test")
    parser.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="REF=NEW",
        help="compare differently-named files, repeatable (e.g. "
        "transformer-dev.safetensors=transformer-distilled.safetensors)",
    )
    parser.add_argument(
        "--allow-only-new",
        action="append",
        default=[],
        metavar="PATTERN",
        help="collapsed key pattern expected to exist only in the NEW pack. "
        "Declaring any pattern turns key-set checking into a hard gate.",
    )
    parser.add_argument(
        "--allow-only-ref",
        action="append",
        default=[],
        metavar="PATTERN",
        help="collapsed key pattern expected to exist only in the REFERENCE pack.",
    )
    parser.add_argument(
        "--strict-keys",
        action="store_true",
        help="the key sets must be identical (the empty contract)",
    )
    parser.add_argument(
        "--strict-files",
        action="store_true",
        help="also fail when the two packs do not hold the same set of .safetensors files",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON instead of text")
    args = parser.parse_args(argv)

    for directory in (args.ref_dir, args.new_dir):
        if not directory.is_dir():
            print(f"error: {directory} is not a directory", file=sys.stderr)
            return 2

    mapping: dict[str, str] = {}
    for raw in args.map:
        ref_name, _, new_name = raw.partition("=")
        if not ref_name:
            print(f"error: --map needs REF=NEW, got {raw!r}", file=sys.stderr)
            return 2
        mapping[ref_name] = new_name or ref_name

    report = diff_packs(
        args.ref_dir,
        args.new_dir,
        mapping=mapping,
        allow_only_new=args.allow_only_new,
        allow_only_ref=args.allow_only_ref,
        strict_keys=args.strict_keys,
    )

    if args.strict_files:
        ref_files = {p.name for p in args.ref_dir.glob("*.safetensors")}
        new_files = {p.name for p in args.new_dir.glob("*.safetensors")}
        report["only_new_files"] = sorted(new_files - ref_files)
        report["only_ref_files"] = sorted(ref_files - new_files)
        if report["only_new_files"] or report["only_ref_files"]:
            report["ok"] = False

    if not report["compared"]:
        print(
            f"error: no comparable .safetensors between {args.ref_dir} and {args.new_dir}. "
            "A diff that compares nothing must not report success.",
            file=sys.stderr,
        )
        return 2

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
        for label in ("only_new_files", "only_ref_files"):
            if report.get(label):
                print(f"{label}: {', '.join(report[label])}")

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
