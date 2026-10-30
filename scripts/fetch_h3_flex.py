#!/usr/bin/env python3
"""Build Pika H3 Flex's FL2VA adapter for Phosphene's MLX H3 runner, on THIS Mac.

Pika H3 Flex running natively on Apple Silicon (MLX) - first Mac port.

Pika H3 Flex (Pika Labs / Mellis, Inc., released 2026-10-29) is a rank-128 LoRA
on MiniMax H3 plus the six-forward "re-noise" sampler it was distilled for. Pika
ships it for NVIDIA GPUs only; Phosphene runs it on the Mac with its own MLX
code (the runner's `--pika-flex` sampler preset) and this adapter build.

Phosphene does not host the adapter. It is a MiniMax H3 Model Derivative under
the MiniMax H3 Community License, so - like fetch_h3_v13.py - the panel fetches
the ORIGINAL file from Pika's Hugging Face repo at a pinned commit and SHA-256,
together with Pika's LICENSE and NOTICE, and re-lays the adapter out locally
into the layout the runner's `--lora` loader applies as-is (no runner flags):

  * the split diffusers q/k/v adapters become ONE block-diagonal adapter whose B
    rows are written in the DiT's own fused-qkv order, per head
    (heads, 3, head_dim) - the base checkpoint stores qkv_proj that way;
  * diffusers' `ff.net.0.proj` rows are [value; gate]; the runner's fc1 reads
    [gate; value], so the two halves of B swap;
  * the source is float32; the result is bfloat16 (round to nearest even),
    which is the precision Pika's own loader runs the adapter at
    (its `apply_lora` casts every lora_ parameter to bf16). alpha == rank
    (128/128), so no scale is folded.

The build is deterministic (sorted keys, fixed metadata), so every Mac makes
the same bytes; ASSET_SHA256 pins them. The result carries a modification
notice in its metadata, as the licence's Section III.2 asks of modified files.

Needs numpy (the panel's and the H3 runner's interpreters both have it).
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import struct
import sys
import urllib.request
from pathlib import Path

# ---- the upstream release (fetched, never re-hosted) ------------------------
SOURCE_REPO = "Pika-Labs/Pika-H3-Flex"
SOURCE_REVISION = "0ddf55ad7067ca1e76812253bd7b4600df87f759"
SOURCE_FILE = "fl2va/adapter.safetensors"
SOURCE_SHA256 = "8ed6115d647ab11c1b38d04e63c4c6e1594128ae312f7b5635cd50563b7b9327"
SOURCE_BYTES = 2767274120
# Small files that travel with the adapter: the release's own sampling manifest
# and its licence + notice (the MiniMax H3 licence asks that the NOTICE text
# accompany the model, and that recipients get the licence).
SIDE_FILES = {
    "fl2va/manifest.json": "7d06377f04a3e822f5f21ad72501312da25101976218bde37b12a2171746a8a5",
    "LICENSE": "59b99642b95ea21630e311198ddbfffbfe05aadba0c2f5d884cbdf4efcc90f44",
    "NOTICE": "8fc52805a4f8d034f853b5ed494574e37f292def5cdd326d22547afa2658527b",
}
BASE_REPO = "MiniMaxAI/MiniMax-H3"
BASE_REVISION = "42ed227ee7df40d41602854ae760620d6eb651fe"


def source_url(name: str) -> str:
    return f"https://huggingface.co/{SOURCE_REPO}/resolve/{SOURCE_REVISION}/{name}"


# ---- the result, built locally ------------------------------------------------
ASSET_NAME = "pika_h3_flex_fl2va_phosphene.safetensors"
ASSET_SHA256 = "c71f527d0092dd43d368a20223d4aec41e7a1cca6abecdc457d54cee785f0327"
ASSET_BYTES = 1956166104

# The DiT's attention shape (H3 FL2VA: 56 heads x 128) and the release's rank.
HEADS = 56
HEAD_DIM = 128
RANK = 128
ALPHA = 128

MODIFICATION_NOTICE = (
    "Modified by Phosphene (Mr Bizarro) in 2026: Pika H3 Flex FL2VA adapter re-laid out "
    "for the MLX H3 runner (fused per-head qkv, SwiGLU halves swapped, float32 -> bfloat16). "
    "Original: Copyright (c) 2026 Mellis, Inc. Model Derivative of MiniMax H3, distributed "
    "under the MiniMax H3 Community License Agreement."
)
METADATA = {
    "converted_from": f"{SOURCE_REPO}@{SOURCE_REVISION}:{SOURCE_FILE}",
    "source_sha256": SOURCE_SHA256,
    "base_model": f"{BASE_REPO}@{BASE_REVISION}",
    "license": "MiniMax H3 Community License Agreement (H3 Model Derivative); Pika code Apache-2.0",
    "notice": "MiniMax H3 is licensed under the MiniMax H3 Community License Agreement, "
              "Copyright (c) 2026 MiniMax. All Rights Reserved.",
    "modification_notice": MODIFICATION_NOTICE,
    "fc1_layout": "[gate;value]",
    "qkv_layout": "per-head (heads,3,head_dim), the DiT's own order; apply with no permute",
    "lora_alpha": str(ALPHA),
    "source_rank": str(RANK),
    "qkv_fused_rank": str(3 * RANK),
    "sampler": "renoise; knots 1000,750,500,250,100,40,0; video shift 12; audio shift 3",
    "phosphene_runner_flags": "--pika-flex --lora <this file>:1.0",
}

_SRC_A = ".lora_A.default.weight"
_SRC_B = ".lora_B.default.weight"


def _plain(text) -> str:
    """An exception's text with Pinokio's two stop words defused."""
    return re.sub(r"(?i)errno ", "errno-", re.sub(r"(?i)error:", "problem -", str(text)))


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _prefixes() -> list[tuple[str, str]]:
    return ([(f"transformer_blocks.{i}", f"blocks.{i}") for i in range(50)]
            + [(f"token_refiner.refiner_blocks.{i}", f"token_refiner.blocks.{i}")
               for i in range(2)])


class Source:
    """Random access to the float32 matrices of a safetensors file."""

    def __init__(self, path: Path):
        import numpy as np
        self.np = np
        with open(path, "rb") as fh:
            (n,) = struct.unpack("<Q", fh.read(8))
            header = json.loads(fh.read(n))
        self.meta = header.pop("__metadata__", {}) or {}
        self.base = 8 + n
        self.tensors = header
        self.mm = np.memmap(path, dtype=np.uint8, mode="r")

    def shape(self, key: str) -> list[int]:
        return list(self.tensors[key]["shape"])

    def read(self, key: str):
        """The matrix as float32 numpy, finite-checked."""
        np = self.np
        t = self.tensors[key]
        if t["dtype"] != "F32" or len(t["shape"]) != 2:
            raise ValueError(f"{key}: expected a 2-D F32 matrix, got {t['dtype']} {t['shape']}")
        a, b = t["data_offsets"]
        raw = self.mm[self.base + a:self.base + b]
        if raw.size != b - a:
            raise ValueError(f"{key}: source is truncated")
        arr = raw.view("<f4").reshape(t["shape"])
        if not np.isfinite(arr).all():
            raise ValueError(f"{key}: inf/NaN in the source")
        return arr


def to_bf16(arr):
    """float32 -> bfloat16 bits (uint16), round to nearest, ties to even."""
    np = sys.modules["numpy"]
    u = np.ascontiguousarray(arr, dtype="<f4").view("<u4").astype(np.uint64)
    rounded = (u + 0x7FFF + ((u >> 16) & 1)) >> 16
    return rounded.astype("<u2")


def plan_tensors(src: Source) -> dict:
    """{output key: (shape, make_uint16_array)}; one block's tensors in memory at a time."""
    np = src.np
    plan: dict = {}
    used: set = set()

    def pair(prefix: str) -> tuple[list[int], list[int]]:
        sa, sb = src.shape(prefix + _SRC_A), src.shape(prefix + _SRC_B)
        if sa[0] != RANK or sb[1] != RANK:
            raise ValueError(f"{prefix}: expected rank {RANK}, got A{sa} B{sb}")
        used.update((prefix + _SRC_A, prefix + _SRC_B))
        return sa, sb

    for s, d in _prefixes():
        qkv = [f"{s}.attn.{p}" for p in ("to_q", "to_k", "to_v")]
        shapes = [pair(p) for p in qkv]
        if len({(tuple(a), tuple(b)) for a, b in shapes}) != 1:
            raise ValueError(f"{s}.attn: q/k/v shapes disagree: {shapes}")
        (_rank, in_cols), (out_rows, _r) = shapes[0]
        if out_rows != HEADS * HEAD_DIM:
            raise ValueError(f"{s}.attn: q rows {out_rows} != {HEADS} heads x {HEAD_DIM}")

        def qkv_a(qkv=qkv):
            # A stacks [A_q; A_k; A_v]: rank 3*RANK over the shared input.
            return to_bf16(np.concatenate([src.read(p + _SRC_A) for p in qkv], axis=0))

        def qkv_b(qkv=qkv, out_rows=out_rows):
            # Block-diagonal B, rows in the DiT's fused order: head h -> its q rows, k rows,
            # v rows; q's rows only see A_q's rank slice, and so on.
            out = np.zeros((HEADS, 3, HEAD_DIM, 3 * RANK), dtype=np.float32)
            for j, p in enumerate(qkv):
                b = src.read(p + _SRC_B).reshape(HEADS, HEAD_DIM, RANK)
                out[:, j, :, j * RANK:(j + 1) * RANK] = b
            return to_bf16(out.reshape(3 * out_rows, 3 * RANK))

        plan[f"{d}.attn.qkv_proj.lora_A.weight"] = ([3 * RANK, in_cols], qkv_a)
        plan[f"{d}.attn.qkv_proj.lora_B.weight"] = ([3 * out_rows, 3 * RANK], qkv_b)

        for sp, dp in (("attn.to_out.0", "attn.out_proj"),
                       ("ff.net.0.proj", "mlp.fc1"),
                       ("ff.net.2", "mlp.fc2")):
            p = f"{s}.{sp}"
            sa, sb = pair(p)
            if dp == "mlp.fc1" and sb[0] % 2:
                raise ValueError(f"{p}: SwiGLU B has an odd row count {sb}")

            def a_arr(p=p):
                return to_bf16(src.read(p + _SRC_A))

            def b_arr(p=p, swap=(dp == "mlp.fc1")):
                b = src.read(p + _SRC_B)
                if swap:
                    half = b.shape[0] // 2
                    b = np.concatenate([b[half:], b[:half]], axis=0)
                return to_bf16(b)

            plan[f"{d}.{dp}.lora_A.weight"] = (sa, a_arr)
            plan[f"{d}.{dp}.lora_B.weight"] = (sb, b_arr)
    unused = sorted(set(src.tensors) - used)
    if unused:
        raise ValueError(f"source has {len(unused)} tensors this layout does not use, e.g. {unused[:3]}")
    return plan


def convert(source: Path, write) -> None:
    """Re-lay the source out, calling write(bytes) in order. Tensors are stored in
    sorted-name order so the bytes never depend on a hash-map order."""
    plan = plan_tensors(Source(source))
    keys = sorted(plan)
    header: dict = {"__metadata__": METADATA}
    off = 0
    for k in keys:
        shape = plan[k][0]
        n = shape[0] * shape[1] * 2
        header[k] = {"data_offsets": [off, off + n], "dtype": "BF16", "shape": shape}
        off += n
    blob = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    blob += b" " * (-len(blob) % 8)          # 8-byte aligned tensor data
    write(struct.pack("<Q", len(blob)))
    write(blob)
    for k in keys:
        shape, make = plan[k]
        data = make()
        if tuple(data.shape) != (shape[0], shape[1]) and data.size != shape[0] * shape[1]:
            raise ValueError(f"internal: {k} has shape {data.shape} for {shape}")
        write(data.tobytes())


def _download(url: str, dest: Path, want_bytes: int | None, want_sha: str, label: str) -> int:
    """Fetch `url` to `dest` (resuming), then check the digest. 0 when verified."""
    have = dest.stat().st_size if dest.is_file() else 0
    if want_bytes is not None and have > want_bytes:
        dest.unlink()
        have = 0
    if want_bytes is None or have < want_bytes:
        headers = {"User-Agent": "Phosphene"}
        token = (os.environ.get("HF_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"resuming {label} at {have // (1 << 20)} MB", flush=True)
        else:
            size = f" (~{want_bytes // (1 << 20)} MB)" if want_bytes else ""
            print(f"downloading {label} from {SOURCE_REPO}{size}", flush=True)
        req = urllib.request.Request(url, headers=headers)
        written = have
        try:
            with urllib.request.urlopen(req, timeout=60) as resp, \
                    open(dest, "ab" if have else "wb") as fh:
                if have and resp.status != 206:
                    fh.seek(0)
                    fh.truncate()
                    written = 0
                step = 64 << 20
                mark = written + step
                while True:
                    chunk = resp.read(1 << 20)
                    if not chunk:
                        break
                    fh.write(chunk)
                    written += len(chunk)
                    if want_bytes and written >= mark:
                        print(f"PROGRESS {written} {want_bytes}", flush=True)
                        mark += step
        except Exception as exc:  # noqa: BLE001
            print(f"FLEX FETCH FAILED ({label}) at {written // (1 << 20)} MB: {_plain(exc)} "
                  "(partial kept for resume; the panel offers a retry)", file=sys.stderr)
            return 1
        if want_bytes is not None and written != want_bytes:
            print(f"FLEX FETCH INCOMPLETE ({label}): {written} of {want_bytes} bytes "
                  "(partial kept for resume)", file=sys.stderr)
            return 1
    if file_sha256(dest) != want_sha:
        dest.unlink()
        print(f"FLEX DIGEST MISMATCH ({label}) - download discarded; the panel offers a retry",
              file=sys.stderr)
        return 1
    return 0


def build(source: Path, target: Path) -> int:
    partial = target.with_suffix(target.suffix + ".partial")
    print(f"building {ASSET_NAME} on this Mac (re-layout + float32 -> bfloat16)", flush=True)
    h = hashlib.sha256()
    try:
        with open(partial, "wb") as out:
            def write(b: bytes) -> None:
                out.write(b)
                h.update(b)
            convert(source, write)
    except Exception as exc:  # noqa: BLE001
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"FLEX BUILD FAILED: {_plain(exc)} (the panel offers a retry)", file=sys.stderr)
        return 1
    size = partial.stat().st_size
    digest = h.hexdigest()
    if ASSET_SHA256 and (size != ASSET_BYTES or digest != ASSET_SHA256):
        partial.unlink(missing_ok=True)
        print(f"FLEX BUILD MISMATCH: {size} bytes, sha256 {digest[:12]} "
              f"(want {ASSET_BYTES}, {ASSET_SHA256[:12]}) - nothing installed", file=sys.stderr)
        return 1
    partial.replace(target)
    print(f"built {target.name}: {size} bytes, sha256 {digest}", flush=True)
    return 0


def licence_accepted(state_dir: Path) -> bool:
    """Whether the MiniMax H3 licence is accepted in Phosphene (the same marker
    scripts/pinokio/h3_licence_gate.sh and the panel read)."""
    gate = Path(__file__).resolve().parent / "pinokio" / "h3_licence_gate.sh"
    m = re.search(r'^H3_LICENCE_VERSION="([^"]+)"', gate.read_text(), re.M) if gate.is_file() else None
    if not m:
        return any(state_dir.glob("licence-h3-*.accepted"))
    return (state_dir / f"licence-h3-{m.group(1)}.accepted").is_file()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="target directory (the H3 pack's pika-h3-flex/)")
    ap.add_argument("--source", default=None,
                    help="use this already-downloaded upstream fl2va/adapter.safetensors")
    ap.add_argument("--keep-source", action="store_true",
                    help="keep the 2.8 GB upstream file after a good build")
    ap.add_argument("--licence-state", default=None,
                    help="Phosphene's state dir: skip (exit 0) unless the H3 licence is accepted there")
    args = ap.parse_args()

    if args.licence_state and not licence_accepted(Path(args.licence_state)):
        print("Pika H3 Flex skipped: accept the MiniMax H3 licence in Phosphene first.")
        return 0
    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    lock_fh = open(target_dir / ".pika_h3_flex.lock", "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("FLEX FETCH BUSY: another Pika H3 Flex download is running for this folder",
              file=sys.stderr)
        return 1

    # Licence + notice + the release's sampling manifest, beside the adapter.
    for name, sha in SIDE_FILES.items():
        dest = target_dir / ("pika_" + name.replace("/", "_"))
        if dest.is_file() and file_sha256(dest) == sha:
            continue
        rc = _download(source_url(name), dest, None, sha, name)
        if rc:
            return rc

    target = target_dir / ASSET_NAME
    if target.is_file():
        if not ASSET_BYTES or target.stat().st_size == ASSET_BYTES:
            print(f"Pika H3 Flex adapter already present: {target}")
            return 0
        print(f"REFUSING: {target} exists with unexpected size {target.stat().st_size} "
              f"(want {ASSET_BYTES}). Move it aside and re-run.", file=sys.stderr)
        return 1

    if args.source:
        source = Path(args.source)
        if (not source.is_file() or source.stat().st_size != SOURCE_BYTES
                or file_sha256(source) != SOURCE_SHA256):
            print(f"REFUSING: {source} is not the pinned upstream {SOURCE_FILE}", file=sys.stderr)
            return 1
        own_source = False
    else:
        source = target_dir / "pika_fl2va_adapter.safetensors.download"
        rc = _download(source_url(SOURCE_FILE), source, SOURCE_BYTES, SOURCE_SHA256, SOURCE_FILE)
        if rc:
            return rc
        own_source = True

    rc = build(source, target)
    if rc:
        return rc
    if own_source and not args.keep_source:
        source.unlink(missing_ok=True)
    print(f"Pika H3 Flex adapter ready: {target}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        print(f"FLEX FETCH FAILED: {_plain(type(exc).__name__)} {_plain(exc)} "
              "(the panel offers a retry)", file=sys.stderr)
        sys.exit(1)
