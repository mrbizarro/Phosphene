#!/usr/bin/env python3
"""Build the H3 Turbo runner-layout adapter on THIS Mac, from its upstream source.

Phosphene does not host this file. The adapter is a MiniMax H3 "Model
Derivative" (LightX2V's 4-step distillation of H3), and the MiniMax H3
Community License only allows it to be distributed inside its Applicable
Territory. So, since 4.19.8, the installer fetches the ORIGINAL file from the
LightX2V Hugging Face repo, pinned to a commit and its SHA-256, and re-lays it
out locally into the layout the H3 runner's `--lora` loader reads:

  * the split diffusers q/k/v adapters become ONE block-diagonal adapter whose
    output rows stay contiguous [q_all; k_all; v_all]. The checkpoint stores
    them per head, and the runner stopped interleaving them by default on
    2026-08-15, so the panel hands the runner a per-head copy
    (`h3_bake_runner_layout` in mlx_ltx_panel.py); this file stays as built,
    so its digest still matches ASSET_SHA256;
  * diffusers' `ff.net.0.proj` rows are [value; gate]; the runner's fc1 wants
    [gate; value], so the two halves of B swap;
  * PEFT's alpha/rank scale is folded into B. For this file alpha == rank
    (128 / 128), so the scale is exactly 1 and B is copied unchanged - the
    script refuses any source where that is not true rather than doing float
    maths that could make the result differ by a bit.

Every operation is a byte re-arrangement of bf16 rows (plus zero blocks), done
with the standard library only, because install_h3.js runs this before anything
optional exists. The output is then checked against the SHA-256 and byte count
of the file Phosphene used to host (ASSET_SHA256 / ASSET_BYTES), so the result
is byte-identical and Turbo behaves exactly as before. The file layout order
(DATA_ORDER) is the one MLX's save_safetensors wrote for the original repack;
it is fixed here so the build does not depend on MLX's hash-map order.

Resumes a partial source download with an HTTP Range request, verifies the
source digest before converting, writes the result to `<name>.partial` and
renames it into place only after both digests pass - a killed or corrupt run
never leaves a file the panel's resolver would pick up. An install that
already has the adapter keeps it untouched.

The pins below MUST match mlx_ltx_panel.py (H3_TURBO_SOURCE_* / H3_TURBO_ASSET_*);
test_h3_turbo_adapter.py asserts they are identical so they cannot drift.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import sys
import urllib.request
from pathlib import Path


def _hf_urlopen(req, timeout=60):
    """urllib.request.urlopen for a pinned-checksum Hugging Face download, with the China
    mirror rule (scripts/hf_source.py, 5.0.76): the mirror when huggingface.co cannot be
    reached, never a login sent there. Plain urlopen when the helper is missing."""
    import importlib
    import os as _os
    import sys as _sys
    try:
        hs = importlib.import_module("scripts.hf_source")      # inside the panel (one copy)
    except ImportError:
        d = _os.path.dirname(_os.path.abspath(__file__))
        d = d if _os.path.basename(d) == "scripts" else _os.path.join(d, "scripts")
        if d not in _sys.path:
            _sys.path.append(d)                                 # never ahead of the panel's own modules
        try:
            hs = importlib.import_module("hf_source")
        except ImportError:
            return urllib.request.urlopen(req, timeout=timeout)
    return hs.urlopen(req, timeout)

# ---- the upstream source (fetched, never re-hosted) -------------------------
SOURCE_REPO = "lightx2v/Minimax-h3-Turbo"
SOURCE_REVISION = "30f485ed874aa44c8183348ad361c5eabad318da"
SOURCE_FILE = "minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors"
SOURCE_URL = ("https://huggingface.co/" + SOURCE_REPO + "/resolve/"
              + SOURCE_REVISION + "/" + SOURCE_FILE)
SOURCE_SHA256 = "1bdabc2e9fce20b1db563b96bcf6e46adcad4c1964f423676436bf266cc7416c"
SOURCE_BYTES = 1383677808

# ---- the result, built locally ------------------------------------------------
ASSET_NAME = "lightx2v_v1.0_768p_ourlayout.safetensors"
ASSET_SHA256 = "d51d626fe0845da7e5845a47c323cf3f29086d44d24cb1a4b980882488746197"
ASSET_BYTES = 1956165254

# The metadata the original repack carried, verbatim.
METADATA = {
    "converted_from": "v1.0_768p.safetensors",
    "fc1_layout": "[gate;value]",
    "lora_alpha": "128",
    "peft_scale_folded_into_B": "1",
    "qkv_fused_rank": "384",
    "qkv_layout": "contiguous [q_all;k_all;v_all]; loader interleaves exactly once",
    "source_namespace": "diffusers PEFT split q/k/v",
    "source_rank": "128",
    "target_namespace": "minimax_h3_mlx.lora loader input",
}

# Data order of the 416 output tensors in the file, as indices into
# output_keys(). Fixed so the bytes never depend on a hash-map order.
DATA_ORDER = tuple(int(x) for x in (
    "415 414 412 411 406 403 402 399 398 393 392 177 388 383 382 381 98 325 47 "
    "380 375 343 374 326 368 370 367 365 364 322 360 357 354 224 353 351 215 252 "
    "350 57 349 346 1 342 341 338 376 335 3 210 333 377 146 324 162 320 315 314 "
    "313 312 310 237 307 306 372 301 330 300 316 299 298 394 352 323 296 295 294 "
    "241 293 291 289 288 336 309 287 285 192 303 213 284 283 280 279 277 276 8 "
    "274 273 270 232 321 268 328 219 267 266 264 76 44 261 260 124 259 28 371 "
    "258 405 319 254 45 253 250 249 142 163 247 242 211 286 120 240 281 13 234 "
    "308 58 228 227 226 223 244 221 220 344 48 239 391 332 218 305 217 140 154 "
    "216 386 214 272 212 208 255 205 203 94 339 222 0 202 389 75 201 229 366 200 "
    "207 304 198 196 195 194 185 290 102 184 181 91 123 174 409 180 172 170 190 "
    "384 168 369 26 167 11 2 183 297 100 128 131 29 32 256 148 145 147 182 151 "
    "139 169 248 137 136 153 117 334 135 171 359 132 126 125 65 165 122 121 119 "
    "118 401 114 7 187 329 400 110 175 112 109 134 87 186 63 311 197 83 317 46 "
    "107 150 292 36 106 159 251 30 104 233 103 397 348 243 101 99 373 92 17 209 "
    "71 161 33 157 64 379 90 52 85 62 105 204 12 361 96 80 410 176 79 144 74 56 "
    "60 97 188 73 199 54 69 141 88 9 67 331 41 236 4 59 27 53 66 82 95 61 138 16 "
    "31 238 20 257 5 345 318 86 269 68 408 189 14 337 275 21 363 358 404 116 407 "
    "191 130 50 179 355 155 156 77 127 113 6 40 84 38 49 35 166 302 271 37 230 "
    "164 34 282 263 262 149 89 193 231 10 70 42 93 18 108 396 265 340 81 387 385 "
    "173 115 158 206 23 143 225 160 22 55 347 43 19 39 390 278 24 356 378 413 "
    "178 395 51 111 246 15 362 235 72 327 129 78 133 245 25 152"
).split())

_SRC_A = ".lora_A.default.weight"
_SRC_B = ".lora_B.default.weight"
_BF16 = 2


def _plain(text) -> str:
    """An exception's text with Pinokio's two stop words defused.

    This fetch is OPTIONAL (the panel offers a one-click retry). Pinokio 8.2
    ends an install when a step's output matches /error:/i or /errno /i, and a
    dropped connection reads "<urlopen error [Errno 8] ...>" - which would end
    the whole H3 install over a file the panel can fetch later.
    """
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


def output_keys() -> list[str]:
    """The 416 output tensor names, in the order the repack built them."""
    keys: list[str] = []
    for _src, dst in _prefixes():
        for mod in ("attn.qkv_proj", "attn.out_proj", "mlp.fc1", "mlp.fc2"):
            keys += [f"{dst}.{mod}.lora_A.weight", f"{dst}.{mod}.lora_B.weight"]
    return keys


class Source:
    """Random access to the tensors of a safetensors file (bf16 only)."""

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


def _rows(data: bytes, cols: int) -> list[bytes]:
    w = cols * _BF16
    return [data[i:i + w] for i in range(0, len(data), w)]


def plan_tensors(src: Source) -> dict:
    """{output key: (shape, make_bytes)} for all 416 output tensors, in
    output_keys() order. Nothing is read until make_bytes() is called, so the
    build holds one tensor (at most ~16 MB) in memory at a time."""
    alpha = str(src.meta.get("alpha", ""))
    if alpha != "128":
        # alpha / rank must be exactly 1 for B to be copied unchanged.
        raise ValueError(f"source alpha is {alpha!r}, expected '128' (scale 1)")
    plan: dict = {}
    used: set = set()

    def pair(prefix: str) -> tuple[list[int], list[int]]:
        sa, sb = src.shape(prefix + _SRC_A), src.shape(prefix + _SRC_B)
        if sa[0] != 128 or sb[1] != 128:
            raise ValueError(f"{prefix}: expected rank 128, got A{sa} B{sb}")
        used.update((prefix + _SRC_A, prefix + _SRC_B))
        return sa, sb

    for s, d in _prefixes():
        # q/k/v -> one block-diagonal adapter of rank 3r.
        qkv = [f"{s}.attn.{p}" for p in ("to_q", "to_k", "to_v")]
        shapes = [pair(p) for p in qkv]
        if len({(tuple(a), tuple(b)) for a, b in shapes}) != 1:
            raise ValueError(f"{s}.attn: q/k/v shapes disagree: {shapes}")
        (rank, in_cols), (out_rows, _r) = shapes[0]

        def qkv_a(qkv=qkv):
            return b"".join(src.read(p + _SRC_A) for p in qkv)

        def qkv_b(qkv=qkv, rank=rank):
            zero = bytes(rank * _BF16)
            rows = []
            for i, p in enumerate(qkv):
                left, right = zero * i, zero * (2 - i)
                rows.extend(left + r + right for r in _rows(src.read(p + _SRC_B), rank))
            return b"".join(rows)

        plan[f"{d}.attn.qkv_proj.lora_A.weight"] = ([3 * rank, in_cols], qkv_a)
        plan[f"{d}.attn.qkv_proj.lora_B.weight"] = ([3 * out_rows, 3 * rank], qkv_b)

        # out_proj, fc1, fc2. B is copied (scale 1); fc1's B rows are
        # [value; gate] upstream and [gate; value] for the runner.
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
                b = src.read(p + _SRC_B)
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
    """Re-lay the source (an open binary file) out, calling write(bytes) in order."""
    src = Source(src_fh)
    keys = output_keys()
    if sorted(DATA_ORDER) != list(range(len(keys))):
        raise ValueError("DATA_ORDER is not a permutation of the 416 output tensors")
    plan = plan_tensors(src)
    if list(plan) != keys:
        raise ValueError("internal: output tensors came out in an unexpected order")
    header = {"__metadata__": METADATA}
    off = 0
    for i in DATA_ORDER:
        shape = plan[keys[i]][0]
        n = shape[0] * shape[1] * _BF16
        header[keys[i]] = {"data_offsets": [off, off + n], "dtype": "BF16",
                           "shape": shape}
        off += n
    blob = json.dumps(header, separators=(",", ":"), sort_keys=True).encode()
    write(struct.pack("<Q", len(blob)))
    write(blob)
    for i in DATA_ORDER:
        shape, make = plan[keys[i]]
        data = make()
        if len(data) != shape[0] * shape[1] * _BF16:
            raise ValueError(f"internal: {keys[i]} has {len(data)} bytes for shape {shape}")
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
            print(f"resuming the upstream download at {have // (1 << 20)} MB")
        else:
            print(f"downloading {SOURCE_FILE} from {SOURCE_REPO} "
                  f"(~{SOURCE_BYTES // (1 << 20)} MB)")
        req = urllib.request.Request(SOURCE_URL, headers=headers)
        written = have
        try:
            with _hf_urlopen(req, 60) as resp, \
                    open(dest, "ab" if have else "wb") as fh:
                if have and resp.status != 206:
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
            print(f"TURBO FETCH FAILED at {written // (1 << 20)} MB: {_plain(exc)} "
                  "(partial kept for resume; panel offers a one-click retry)",
                  file=sys.stderr)
            return 1
        if written != SOURCE_BYTES:
            print(f"TURBO FETCH INCOMPLETE: {written} of {SOURCE_BYTES} bytes "
                  "(partial kept for resume)", file=sys.stderr)
            return 1
    if file_sha256(dest) != SOURCE_SHA256:
        dest.unlink()
        print("TURBO SOURCE DIGEST MISMATCH - download discarded; "
              "panel offers a one-click retry", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True,
                    help="target directory (the H3 pack's turbo-lora/)")
    ap.add_argument("--source", default=None,
                    help="use this already-downloaded upstream file instead of fetching it")
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

    if args.source:
        source = Path(args.source)
        if not source.is_file() or file_sha256(source) != SOURCE_SHA256:
            print(f"REFUSING: {source} is not the pinned upstream {SOURCE_FILE}",
                  file=sys.stderr)
            return 1
        own_source = False
    else:
        source = target_dir / (SOURCE_FILE + ".download")
        rc = download_source(source)
        if rc:
            return rc
        own_source = True

    print(f"building {ASSET_NAME} on this Mac (re-layout, no maths)")
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
        print(f"TURBO BUILD FAILED: {_plain(exc)} "
              "(panel offers a one-click retry)", file=sys.stderr)
        return 1
    size = partial.stat().st_size
    if size != ASSET_BYTES or h.hexdigest() != ASSET_SHA256:
        partial.unlink(missing_ok=True)
        print(f"TURBO BUILD MISMATCH: {size} bytes, sha256 {h.hexdigest()[:12]} "
              f"(want {ASSET_BYTES}, {ASSET_SHA256[:12]}) - nothing installed",
              file=sys.stderr)
        return 1
    partial.replace(target)
    if own_source:
        source.unlink(missing_ok=True)
    print(f"Turbo adapter ready: {target}")
    return 0


if __name__ == "__main__":
    # OPTIONAL step: whatever goes wrong (a cleanup that cannot unlink, a full
    # disk mid-rename), the text Pinokio sees must not carry its stop words.
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        print(f"TURBO FETCH FAILED: {_plain(type(exc).__name__)} {_plain(exc)} "
              "(panel offers a one-click retry)", file=sys.stderr)
        sys.exit(1)
