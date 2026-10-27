#!/usr/bin/env python3
"""Build LightX2V's v1.3 H3 adapter ("High · lightx2v v1.3") on THIS Mac, from its source.

Phosphene does not host this file. It is a MiniMax H3 "Model Derivative"
(LightX2V's few-step distillation of H3, Apache-2.0 code, MiniMax H3 Community
License on the weights), so - exactly like fetch_h3_turbo.py - the panel fetches
the ORIGINAL file from the LightX2V Hugging Face repo, pinned to a commit and its
SHA-256, and re-lays it out locally into the layout the H3 runner's `--lora`
loader applies as-is (no runner flags):

  * the split diffusers q/k/v adapters become ONE block-diagonal adapter, and
    its B rows are written in the DiT's own fused-qkv order, per head
    (heads, 3, head_dim) - the base checkpoint stores qkv_proj that way. The
    upstream file is trained against the contiguous (3, heads, head_dim) split,
    so this is the same row move the runner's `--lora-qkv-permute` makes;
    doing it here keeps the flag off, so a user LoRA stacked on top is never
    permuted with it;
  * diffusers' `ff.net.0.proj` rows are [value; gate]; the runner's fc1 wants
    [gate; value], so the two halves of B swap;
  * PEFT's alpha/rank scale is folded into B. The source declares alpha 8 at
    rank 128, a scale of 1/16 = 2^-4: every bf16 value's exponent drops by 4,
    which is exact. The build refuses any value that would leave the normal
    range rather than round it.

All of it is byte moves on bf16 rows plus that exponent shift, standard library
only. The result is checked against ASSET_SHA256 / ASSET_BYTES before it is
renamed into place, so every Mac builds the same bytes - the bytes whose renders
were validated (2026-10-22: identical, tensor for tensor, to the file the
validation runs loaded with --lora-qkv-permute).

Resumes a partial source download with an HTTP Range request, verifies the
source digest before converting, writes `<name>.partial` and renames it only
after both digests pass. The source is deleted after a good build (~1.4 GB).

The pins below MUST match mlx_ltx_panel.py (H3_V13_*); test_h3_v13.py holds
them together.
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

# ---- the upstream source (fetched, never re-hosted) -------------------------
SOURCE_REPO = "lightx2v/Minimax-h3-Turbo"
SOURCE_REVISION = "30f485ed874aa44c8183348ad361c5eabad318da"
SOURCE_FILE = "minimax_h3_fl2v_turbo_4step_v1.3_768p_bf16.safetensors"
SOURCE_URL = ("https://huggingface.co/" + SOURCE_REPO + "/resolve/"
              + SOURCE_REVISION + "/" + SOURCE_FILE)
SOURCE_SHA256 = "97b775707bb5732e01dba0cf3833d3fe2947c953859d4d05ea3bd74894c43d2b"
SOURCE_BYTES = 1383677808

# ---- the result, built locally ------------------------------------------------
ASSET_NAME = "lightx2v_v1.3_768p_phosphene.safetensors"
ASSET_SHA256 = "45ee324a31beacef7a2fbef019b99e16b35d80586dc4e0d60fecf6ca679751d8"
ASSET_BYTES = 1956165376

# The DiT's attention shape (H3 pruned FL2VA: 56 heads x 128).
HEADS = 56
HEAD_DIM = 128
RANK = 128
ALPHA = 8          # from the source's own metadata; checked, never assumed
EXP_SHIFT = 4      # alpha / rank = 8 / 128 = 2^-4

METADATA = {
    "converted_from": SOURCE_FILE,
    "source_repo": SOURCE_REPO,
    "license": "MiniMax H3 Community License (H3 Model Derivative); LightX2V code Apache-2.0",
    "fc1_layout": "[gate;value]",
    "qkv_layout": "per-head (heads,3,head_dim), the DiT's own order; apply with no permute",
    "lora_alpha": str(ALPHA),
    "source_rank": str(RANK),
    "peft_scale_folded_into_B": "0.0625",
    "qkv_fused_rank": str(3 * RANK),
}

_SRC_A = ".lora_A.default.weight"
_SRC_B = ".lora_B.default.weight"
_BF16 = 2


def _plain(text) -> str:
    """An exception's text with Pinokio's two stop words defused (optional step)."""
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
    """Random access to the bf16 matrices of a safetensors file."""

    def __init__(self, fh):
        self.fh = fh
        fh.seek(0)
        (n,) = struct.unpack("<Q", fh.read(8))
        header = json.loads(fh.read(n))
        self.meta = header.pop("__metadata__", {}) or {}
        self.base = 8 + n
        self.tensors = header

    def shape(self, key: str) -> list[int]:
        return list(self.tensors[key]["shape"])

    def read(self, key: str) -> bytes:
        t = self.tensors[key]
        if t["dtype"] != "BF16" or len(t["shape"]) != 2:
            raise ValueError(f"{key}: expected a 2-D BF16 matrix, got {t['dtype']} {t['shape']}")
        a, b = t["data_offsets"]
        self.fh.seek(self.base + a)
        data = self.fh.read(b - a)
        if len(data) != b - a:
            raise ValueError(f"{key}: source is truncated")
        return data


# bf16 is little-endian: the HIGH byte (sign + 7 exponent bits) is every second
# byte. Exponent - 4 is (high & 0x7f) - 2 with the sign kept; it is exact while
# the result stays normal. Values whose high byte is 0x00/0x80/0x01/0x81/0x02/
# 0x82 are checked one by one: only exact zeros (left alone) are accepted.
_SHIFT = bytes(h if (h & 0x7F) < 2 else (h & 0x80) | ((h & 0x7F) - 2) for h in range(256))
_RISKY = bytes(1 if (h & 0x7F) <= 2 or (h & 0x7F) == 0x7F else 0 for h in range(256))


def scale_b(data: bytes, key: str) -> bytes:
    """Multiply every bf16 value by 2^-EXP_SHIFT exactly (EXP_SHIFT == 4)."""
    buf = bytearray(data)
    hi = bytes(buf[1::2])
    flags = hi.translate(_RISKY)
    i = flags.find(1)
    while i != -1:
        h, lo = hi[i], buf[2 * i]
        e = ((h & 0x7F) << 1) | (lo >> 7)
        if e == 0xFF:
            raise ValueError(f"{key}: inf/NaN in the source")
        if not (e == 0 and (lo & 0x7F) == 0) and e - EXP_SHIFT < 1:
            raise ValueError(f"{key}: a value would leave bf16's normal range "
                             "under the 1/16 fold; refusing to round it")
        i = flags.find(1, i + 1)
    buf[1::2] = hi.translate(_SHIFT)
    return bytes(buf)


def _rows(data: bytes, cols: int) -> list[bytes]:
    w = cols * _BF16
    return [data[i:i + w] for i in range(0, len(data), w)]


def plan_tensors(src: Source) -> dict:
    """{output key: (shape, make_bytes)}; one tensor (<= ~16 MB) in memory at a time."""
    if str(src.meta.get("alpha", "")) != str(ALPHA):
        raise ValueError(f"source alpha is {src.meta.get('alpha')!r}, expected '{ALPHA}'")
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
            return b"".join(src.read(p + _SRC_A) for p in qkv)

        def qkv_b(qkv=qkv):
            # Rows in the DiT's order: head h -> its q rows, k rows, v rows;
            # each row padded to the block-diagonal width 3 * RANK.
            zero = bytes(RANK * _BF16)
            parts = [_rows(scale_b(src.read(p + _SRC_B), p + _SRC_B), RANK) for p in qkv]
            out = []
            for h in range(HEADS):
                for j in range(3):
                    left, right = zero * j, zero * (2 - j)
                    out.extend(left + r + right
                               for r in parts[j][h * HEAD_DIM:(h + 1) * HEAD_DIM])
            return b"".join(out)

        plan[f"{d}.attn.qkv_proj.lora_A.weight"] = ([3 * RANK, in_cols], qkv_a)
        plan[f"{d}.attn.qkv_proj.lora_B.weight"] = ([3 * out_rows, 3 * RANK], qkv_b)

        for sp, dp in (("attn.to_out.0", "attn.out_proj"),
                       ("ff.net.0.proj", "mlp.fc1"),
                       ("ff.net.2", "mlp.fc2")):
            p = f"{s}.{sp}"
            sa, sb = pair(p)
            if dp == "mlp.fc1" and sb[0] % 2:
                raise ValueError(f"{p}: SwiGLU B has an odd row count {sb}")

            def a_bytes(p=p):
                return src.read(p + _SRC_A)

            def b_bytes(p=p, swap=(dp == "mlp.fc1")):
                b = scale_b(src.read(p + _SRC_B), p + _SRC_B)
                if not swap:
                    return b
                half = len(b) // 2
                return b[half:] + b[:half]

            plan[f"{d}.{dp}.lora_A.weight"] = (sa, a_bytes)
            plan[f"{d}.{dp}.lora_B.weight"] = (sb, b_bytes)
    unused = sorted(set(src.tensors) - used)
    if unused:
        raise ValueError(f"source has {len(unused)} tensors this layout does not use, e.g. {unused[:3]}")
    return plan


def convert(src_fh, write) -> None:
    """Re-lay the source out, calling write(bytes) in order. Tensors are stored
    in sorted-name order so the bytes never depend on a hash-map order."""
    plan = plan_tensors(Source(src_fh))
    keys = sorted(plan)
    header: dict = {"__metadata__": METADATA}
    off = 0
    for k in keys:
        shape = plan[k][0]
        n = shape[0] * shape[1] * _BF16
        header[k] = {"data_offsets": [off, off + n], "dtype": "BF16", "shape": shape}
        off += n
    blob = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    blob += b" " * (-len(blob) % 8)          # 8-byte aligned tensor data
    write(struct.pack("<Q", len(blob)))
    write(blob)
    for k in keys:
        shape, make = plan[k]
        data = make()
        if len(data) != shape[0] * shape[1] * _BF16:
            raise ValueError(f"internal: {k} has {len(data)} bytes for shape {shape}")
        write(data)


def download_source(dest: Path) -> int:
    """Fetch the pinned upstream file to `dest` (resuming); 0 when verified."""
    have = dest.stat().st_size if dest.is_file() else 0
    if have > SOURCE_BYTES:
        dest.unlink()
        have = 0
    if have < SOURCE_BYTES:
        headers = {"User-Agent": "Phosphene"}
        token = (os.environ.get("HF_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if have:
            headers["Range"] = f"bytes={have}-"
            print(f"resuming the download at {have // (1 << 20)} MB", flush=True)
        else:
            print(f"downloading {SOURCE_FILE} from {SOURCE_REPO} "
                  f"(~{SOURCE_BYTES // (1 << 20)} MB)", flush=True)
        req = urllib.request.Request(SOURCE_URL, headers=headers)
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
                    if written >= mark:
                        print(f"PROGRESS {written} {SOURCE_BYTES}", flush=True)
                        mark += step
        except Exception as exc:  # noqa: BLE001
            print(f"V13 FETCH FAILED at {written // (1 << 20)} MB: {_plain(exc)} "
                  "(partial kept for resume; the panel offers a retry)", file=sys.stderr)
            return 1
        if written != SOURCE_BYTES:
            print(f"V13 FETCH INCOMPLETE: {written} of {SOURCE_BYTES} bytes "
                  "(partial kept for resume)", file=sys.stderr)
            return 1
    if file_sha256(dest) != SOURCE_SHA256:
        dest.unlink()
        print("V13 SOURCE DIGEST MISMATCH - download discarded; the panel offers a retry",
              file=sys.stderr)
        return 1
    return 0


def build(source: Path, target: Path) -> int:
    partial = target.with_suffix(target.suffix + ".partial")
    print(f"building {ASSET_NAME} on this Mac (re-layout + exact 1/16 fold)", flush=True)
    h = hashlib.sha256()
    try:
        with open(source, "rb") as src_fh, open(partial, "wb") as out:
            def write(b: bytes) -> None:
                out.write(b)
                h.update(b)
            convert(src_fh, write)
    except Exception as exc:  # noqa: BLE001
        try:
            partial.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"V13 BUILD FAILED: {_plain(exc)} (the panel offers a retry)", file=sys.stderr)
        return 1
    size = partial.stat().st_size
    if size != ASSET_BYTES or h.hexdigest() != ASSET_SHA256:
        partial.unlink(missing_ok=True)
        print(f"V13 BUILD MISMATCH: {size} bytes, sha256 {h.hexdigest()[:12]} "
              f"(want {ASSET_BYTES}, {ASSET_SHA256[:12]}) - nothing installed", file=sys.stderr)
        return 1
    partial.replace(target)
    return 0


def licence_accepted(state_dir: Path) -> bool:
    """Use the same explicit acceptance pair as the panel and shell gate."""
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import model_licences
    return model_licences.is_accepted(state_dir, "h3")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="target directory (the H3 pack's turbo-lora/)")
    ap.add_argument("--source", default=None,
                    help="use this already-downloaded upstream file instead of fetching it")
    ap.add_argument("--licence-state", default=None,
                    help="Phosphene's state dir: skip (exit 0) unless the H3 licence is accepted there")
    args = ap.parse_args()

    if args.licence_state and not licence_accepted(Path(args.licence_state)):
        print("lightx2v v1.3 skipped: accept the MiniMax H3 licence in Phosphene first; "
              "the Speed row offers it afterwards.")
        return 0
    target_dir = Path(args.dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    # One build at a time per folder, across processes (Pinokio's installer
    # and a panel's button can both start one; two panels can share weights).
    lock_fh = open(target_dir / ".lightx2v_v1.3.lock", "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("V13 FETCH BUSY: another lightx2v v1.3 download is running for this folder",
              file=sys.stderr)
        return 1
    target = target_dir / ASSET_NAME
    if target.is_file():
        if target.stat().st_size == ASSET_BYTES:
            print(f"lightx2v v1.3 adapter already present: {target}")
            return 0
        print(f"REFUSING: {target} exists with unexpected size {target.stat().st_size} "
              f"(want {ASSET_BYTES}). Move it aside and re-run.", file=sys.stderr)
        return 1

    if args.source:
        source = Path(args.source)
        if not source.is_file() or file_sha256(source) != SOURCE_SHA256:
            print(f"REFUSING: {source} is not the pinned upstream {SOURCE_FILE}", file=sys.stderr)
            return 1
        own_source = False
    else:
        source = target_dir / (SOURCE_FILE + ".download")
        rc = download_source(source)
        if rc:
            return rc
        own_source = True

    rc = build(source, target)
    if rc:
        return rc
    if own_source:
        source.unlink(missing_ok=True)
    print(f"lightx2v v1.3 adapter ready: {target}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        print(f"V13 FETCH FAILED: {_plain(type(exc).__name__)} {_plain(exc)} "
              "(the panel offers a retry)", file=sys.stderr)
        sys.exit(1)
