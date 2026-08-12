#!/usr/bin/env python3
"""Phosphene's own bf16 -> q4/q8 quantizer for LTX (and LTX-shaped) weight packs.

WHY THIS EXISTS
---------------
Every quantized LTX pack a Phosphene user has ever downloaded was built by the
upstream MLX port author (``dgrauet``). We had *zero* weight-conversion tooling
of our own -- verified by the LTX-2.5 port plan (risk #1). If upstream does not
publish ``ltx-2.5-mlx-q4/q8``, we must build the packs ourselves, and the
install path depends on them. This script is that pipeline.

WHAT IT DOES
------------
Takes a **bf16 component already in MLX layout** (a DiT ``transformer-*.safetensors``,
a Gemma text encoder, an upscaler...) and writes a quantized pack in *exactly* the
on-disk layout the vendored loader consumes -- key names, per-file key prefixes,
``config.json`` / ``quantize_config.json`` / ``split_model.json``, and (for sharded
components) a correct ``model.safetensors.index.json``.

It is NOT the PyTorch->MLX converter. Input keys must already be MLX-layout.
That conversion is a separate, later step (port plan §2.2).

HOW IT WORKS
------------
One tensor at a time, start to finish:

* the source safetensors **header** is parsed directly (8-byte LE length + JSON),
  so the whole plan -- every output tensor's name, dtype, shape and byte count --
  is known in closed form *before a single byte is read*;
* each tensor is ``pread``-ed at its own offset, quantized on the **CPU stream**
  (``mx.stream(mx.cpu)`` -- the GPU quant stream is not bit-deterministic across
  runs; see the H3 rebuild findings this script's streaming design is adapted from),
  written straight to the output file, then dropped;
* the output safetensors is written **by hand** as a byte stream (header first,
  because the offsets are all known up front), so peak RSS is one tensor, not one
  pack. ``mx.save_safetensors`` would have to hold the entire 11-23 GB result in
  memory -- impossible on the 48 GB Macs the q4 pack is FOR.

Safety rails, all on by default:

* **disk preflight** -- refuses to start unless free space covers the planned
  output plus a margin;
* **atomic writes** -- everything lands as ``<name>.partial.safetensors`` and is
  ``os.replace``-d into place only after the full byte stream + fsync
  (``.partial.safetensors``, not ``.partial``: ``mx.load`` dispatches on the
  extension and rejects unknown ones, so a crashed run leaves a file that is
  still *loadable for inspection* but never mistaken for the real thing);
* **resume** -- ``--resume`` skips any output whose sha256 already matches the
  manifest; ``.partial.*`` leftovers are always deleted at start (clean restart);
* **self-validation** -- closed-form shape check on every quantized triple, a
  dequantize probe against the source (max abs / relative error), a strict
  ``safetensors.safe_open`` re-read, and an optional full loader round-trip;
* **manifest** -- ``phosphene_quant_manifest.json`` with per-file sha256, size,
  recipe, and tool version, so a published pack is verifiable after upload and
  can feed the panel's existing deep-verify path.

RECIPES
-------
A recipe says which modules get quantized and which stay bf16. The LTX one was
read off the shipped 2.3 packs, not guessed -- see ``RECIPES`` below.

USAGE
-----
Single component::

    python scripts/quantize_ltx.py \
        --src  /path/to/ltx-2.5-mlx-bf16/transformer-distilled.safetensors \
        --out-dir /path/to/ltx-2.5-mlx-q4 \
        --recipe ltx-dit --bits 4

Whole pack (the real job)::

    python scripts/quantize_ltx.py \
        --from-pack /path/to/ltx-2.5-mlx-bf16 \
        --out-dir   /path/to/ltx-2.5-mlx-q4 \
        --recipe ltx-dit --bits 4 --variants distilled

Sharded component (text encoder)::

    python scripts/quantize_ltx.py \
        --src /path/to/gemma4-12b-mlx-bf16 \
        --out-dir /path/to/gemma4-12b-mlx-q4 \
        --recipe ltx-te-gemma --bits 4 --layout sharded
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import resource
import shutil
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mlx.core as mx
import numpy as np

TOOL = "phosphene/quantize_ltx.py"
TOOL_VERSION = "1.0.0"

# safetensors dtype string -> (mlx dtype, bytes per element)
DTYPES: dict[str, tuple[Any, int]] = {
    "BF16": (mx.bfloat16, 2),
    "F16": (mx.float16, 2),
    "F32": (mx.float32, 4),
    "F64": (None, 8),
    "I64": (mx.int64, 8),
    "U64": (mx.uint64, 8),
    "I32": (mx.int32, 4),
    "U32": (mx.uint32, 4),
    "I16": (mx.int16, 2),
    "U16": (mx.uint16, 2),
    "I8": (mx.int8, 1),
    "U8": (mx.uint8, 1),
    "BOOL": (mx.bool_, 1),
}
MX_TO_ST = {
    mx.bfloat16: "BF16",
    mx.float16: "F16",
    mx.float32: "F32",
    mx.uint32: "U32",
    mx.int32: "I32",
    mx.uint8: "U8",
    mx.int8: "I8",
    mx.bool_: "BOOL",
}
QUANTIZABLE_SOURCE_DTYPES = {"BF16", "F16", "F32"}

# 128 MiB read/write chunk for verbatim copies + hashing.
CHUNK = 128 * 1024 * 1024


# --------------------------------------------------------------------------- #
# Recipes
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Recipe:
    """Which modules get quantized, and how.

    A module is quantized when **all** of these hold:
      * its key ends in ``.weight``
      * the tensor is 2-D  (Linear / Embedding weight)
      * the source dtype is a float type
      * the key starts with one of ``include_prefixes``
      * the key contains none of ``exclude_substrings``
      * ``in_features % group_size == 0``  (MLX requirement)

    Everything else is copied through byte-for-byte at its source dtype --
    norms, biases, scale/shift tables, patch/out projections, AdaLN.
    """

    name: str
    description: str
    include_prefixes: tuple[str, ...]
    exclude_substrings: tuple[str, ...] = ()
    group_size: int = 64
    # dtype the scales/biases are stored at in the shipped packs
    scales_dtype: str = "BF16"
    # per-file key prefix the vendored loader strips (documentation + assertion)
    loader_prefix: str | None = None
    # extra keys written into quantize_config.json's "quantization" block
    quant_config_extra: dict[str, Any] = field(default_factory=dict)


RECIPES: dict[str, Recipe] = {
    # ---------------------------------------------------------------- LTX DiT
    # Extracted from the SHIPPED packs, not guessed:
    #   mlx_models/ltx-2.3-mlx-q4/transformer-distilled.safetensors
    #   mlx_models/ltx-2.3-mlx-q8/transformer-dev.safetensors
    # Both: 7450 tensors, 1632 quantized modules == 48 blocks x 34 linears,
    # group_size 64, scales/biases BF16, `.bias` kept BF16 alongside `.biases`,
    # every scale_shift_table left F32, and every module OUTSIDE
    # `transformer.transformer_blocks.` (patchify_proj, proj_out, all eight
    # *adaln_single* stacks) left BF16. That is exactly
    # quantize_config.json's `"only_transformer_blocks": true`.
    "ltx-dit": Recipe(
        name="ltx-dit",
        description="LTX-2.x DiT — quantize the 34 linears inside each transformer block; everything else bf16.",
        include_prefixes=("transformer.transformer_blocks.",),
        loader_prefix="transformer.",
        quant_config_extra={"only_transformer_blocks": True},
    ),
    # ------------------------------------------------------- LTX text encoder
    # Recipe read off mlx_models/gemma-3-12b-it-4bit (what 2.3 ships today):
    # group_size 64, bits 4, quantized set = every language-tower attention and
    # MLP projection PLUS embed_tokens; the vision tower is untouched (and we
    # never load it). For 2.5's custom Gemma 4 the tower dims are identical, so
    # the same prefixes apply -- add `model.language_model.` if the checkpoint
    # nests one level deeper.
    "ltx-te-gemma": Recipe(
        name="ltx-te-gemma",
        description="Gemma text tower — attention + MLP projections + embed_tokens; vision/audio towers untouched.",
        include_prefixes=(
            "language_model.model.layers.",
            "language_model.model.embed_tokens",
            "model.layers.",
            "model.embed_tokens",
        ),
        exclude_substrings=("vision_tower.", "audio_tower.", "vision_model."),
    ),
    # ------------------------------------------------------------- H3 (proof)
    # Not a shipping recipe. The LTX-shaped recipe transposed onto the H3
    # pruned DiT so the pipeline can be proven end-to-end at LTX scale
    # (38.5 GB quantizable) before the gated 2.5 weights land: quantize the
    # linears INSIDE the stacked blocks, leave the refiner (H3's analogue of
    # LTX's never-quantized connector), the patch/out projections and every
    # AdaLN projection alone -- the same shape of decision, key-for-key.
    "h3-dit-probe": Recipe(
        name="h3-dit-probe",
        description="PROOF ONLY — LTX-style recipe transposed onto the H3 pruned DiT.",
        include_prefixes=("blocks.",),
        exclude_substrings=("adaln",),
    ),
    # -------------------------------------------------------------- generic
    "all-linear": Recipe(
        name="all-linear",
        description="Every 2-D float .weight in the file. Use only when a real recipe does not exist yet.",
        include_prefixes=("",),
    ),
}


def load_recipe(name_or_path: str) -> Recipe:
    """Resolve a built-in recipe name, or a JSON file overriding one."""
    if name_or_path in RECIPES:
        return RECIPES[name_or_path]
    p = Path(name_or_path)
    if not p.exists():
        raise SystemExit(f"unknown recipe {name_or_path!r}; known: {', '.join(sorted(RECIPES))} (or a .json path)")
    blob = json.loads(p.read_text())
    base = RECIPES.get(blob.get("base", ""), None)
    fields = dataclasses.asdict(base) if base else {}
    fields.update({k: v for k, v in blob.items() if k != "base"})
    for key in ("include_prefixes", "exclude_substrings"):
        if key in fields and fields[key] is not None:
            fields[key] = tuple(fields[key])
    fields.setdefault("name", p.stem)
    fields.setdefault("description", f"loaded from {p}")
    fields.setdefault("include_prefixes", ())
    return Recipe(**fields)


# --------------------------------------------------------------------------- #
# safetensors primitives
# --------------------------------------------------------------------------- #
def read_header(path: Path) -> tuple[dict[str, dict], dict | None, int]:
    """Return (tensor_index, __metadata__, data_start_offset). Reads the header only."""
    with open(path, "rb") as f:
        raw_len = f.read(8)
        if len(raw_len) != 8:
            raise SystemExit(f"{path}: not a safetensors file (short read)")
        n = struct.unpack("<Q", raw_len)[0]
        head = json.loads(f.read(n))
    meta = head.pop("__metadata__", None)
    return head, meta, 8 + n


def _nelems(shape: list[int]) -> int:
    out = 1
    for s in shape:
        out *= s
    return out


def read_tensor(fh, data_start: int, entry: dict) -> mx.array:
    """Read ONE tensor's raw bytes and reinterpret them as an mx.array.

    Deliberately not ``mx.load`` — an explicit seek+read is the only way to
    make "peak RSS is one tensor" a property of the code rather than a hope
    about mmap and page-cache eviction.
    """
    off0, off1 = entry["data_offsets"]
    fh.seek(data_start + off0)
    buf = fh.read(off1 - off0)
    if len(buf) != off1 - off0:
        raise SystemExit(f"short read: wanted {off1 - off0} bytes, got {len(buf)}")
    mx_dtype, _ = DTYPES[entry["dtype"]]
    if mx_dtype is None:
        raise SystemExit(f"unsupported source dtype {entry['dtype']}")
    flat = mx.array(np.frombuffer(buf, dtype=np.uint8))
    return flat.view(mx_dtype).reshape(entry["shape"])


def tensor_bytes(arr: mx.array) -> memoryview:
    """Raw little-endian byte buffer of an evaluated mx.array (no copy).

    ``.cast("B")`` flattens to 1-D bytes — a bare ``memoryview`` of a 2-D array
    reports ``len()`` as the ROW COUNT and a dtype-dependent format, which
    silently miscounts written bytes.
    """
    mv = memoryview(arr)
    if not mv.contiguous:  # never happens for fresh reads / quantize outputs
        raise SystemExit("non-contiguous array reached the writer")
    return mv.cast("B")


# --------------------------------------------------------------------------- #
# Planning
# --------------------------------------------------------------------------- #
@dataclass
class OutTensor:
    """One tensor in the output file, fully described before any byte is read."""

    key: str
    dtype: str
    shape: list[int]
    nbytes: int
    kind: str  # "copy" | "q_weight" | "q_scales" | "q_biases"
    src_key: str


@dataclass
class Plan:
    src: Path
    outputs: list[OutTensor]
    quantized_modules: list[str]
    skipped_unaligned: list[tuple[str, list[int]]]
    src_index: dict[str, dict]
    src_meta: dict | None
    data_start: int

    @property
    def total_bytes(self) -> int:
        return sum(t.nbytes for t in self.outputs)


def build_plan(src: Path, recipe: Recipe, bits: int, limit: int | None = None) -> Plan:
    index, meta, data_start = read_header(src)
    gs = recipe.group_size
    scales_st = recipe.scales_dtype
    _, scales_width = DTYPES[scales_st]

    keys = sorted(index)
    if limit is not None:
        keys = keys[:limit]

    outputs: list[OutTensor] = []
    quantized: list[str] = []
    unaligned: list[tuple[str, list[int]]] = []

    for key in keys:
        entry = index[key]
        shape = list(entry["shape"])
        dtype = entry["dtype"]
        base = key[: -len(".weight")] if key.endswith(".weight") else None

        eligible = (
            base is not None
            and len(shape) == 2
            and dtype in QUANTIZABLE_SOURCE_DTYPES
            and any(base.startswith(p) for p in recipe.include_prefixes)
            and not any(x in key for x in recipe.exclude_substrings)
        )
        if eligible:
            out_f, in_f = shape
            if in_f % gs != 0:
                # MLX cannot group this row; the shipped packs leave such
                # layers bf16 too. Record it — silence here is how a pack
                # silently loses a layer.
                unaligned.append((key, shape))
            else:
                n_groups = in_f // gs
                packed_cols = in_f * bits // 32
                outputs.append(OutTensor(key, "U32", [out_f, packed_cols], out_f * packed_cols * 4, "q_weight", key))
                outputs.append(
                    OutTensor(
                        f"{base}.scales", scales_st, [out_f, n_groups], out_f * n_groups * scales_width, "q_scales", key
                    )
                )
                outputs.append(
                    OutTensor(
                        f"{base}.biases", scales_st, [out_f, n_groups], out_f * n_groups * scales_width, "q_biases", key
                    )
                )
                quantized.append(base)
                continue

        _, width = DTYPES[dtype]
        outputs.append(OutTensor(key, dtype, shape, _nelems(shape) * width, "copy", key))

    return Plan(src, outputs, quantized, unaligned, index, meta, data_start)


# --------------------------------------------------------------------------- #
# Streaming writer
# --------------------------------------------------------------------------- #
def _partial_path(final: Path) -> Path:
    """``foo.safetensors`` -> ``foo.partial.safetensors``.

    The extension is preserved on purpose: ``mx.load`` dispatches on it and
    refuses unknown suffixes, so a crashed run leaves something inspectable
    rather than something opaque — and it can never be confused for the
    finished file, because the loader asks for the exact final name.
    """
    return final.with_name(f"{final.stem}.partial{final.suffix}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(CHUNK):
            h.update(chunk)
    return h.hexdigest()


def _peak_rss_gb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1e9


def _clear_cache() -> None:
    """Return MLX's pooled buffers to the OS between tensors."""
    fn = getattr(mx, "clear_cache", None)
    if fn is not None:
        fn()


def write_quantized(
    plan: Plan,
    out_path: Path,
    recipe: Recipe,
    bits: int,
    *,
    stamp_metadata: bool,
    verbose: bool = True,
) -> tuple[str, dict]:
    """Stream the plan into ``out_path``. Returns (sha256, stats)."""
    partial = _partial_path(out_path)
    partial.unlink(missing_ok=True)

    # --- header, fully determined before the first byte of data ------------
    header: dict[str, Any] = {}
    if plan.src_meta or stamp_metadata:
        meta = dict(plan.src_meta or {})
        if stamp_metadata:
            # Deterministic ONLY. No timestamps here — two runs must be
            # byte-identical. Time goes in the sidecar manifest instead.
            meta["phosphene_quant"] = json.dumps(
                {
                    "tool": TOOL,
                    "tool_version": TOOL_VERSION,
                    "recipe": recipe.name,
                    "bits": bits,
                    "group_size": recipe.group_size,
                    "source_file": plan.src.name,
                },
                separators=(",", ":"),
                sort_keys=True,
            )
        header["__metadata__"] = meta

    cursor = 0
    for t in plan.outputs:
        header[t.key] = {"dtype": t.dtype, "shape": t.shape, "data_offsets": [cursor, cursor + t.nbytes]}
        cursor += t.nbytes

    header_bytes = json.dumps(header, separators=(",", ":")).encode()
    hasher = hashlib.sha256()
    t0 = time.time()
    written = 0
    n_q = 0

    # Group the plan by source key so each source tensor is read exactly once.
    by_src: dict[str, list[OutTensor]] = {}
    order: list[str] = []
    for t in plan.outputs:
        if t.src_key not in by_src:
            by_src[t.src_key] = []
            order.append(t.src_key)
        by_src[t.src_key].append(t)

    quantize_kwargs: dict[str, Any] = {"group_size": recipe.group_size, "bits": bits}
    scales_mx, _ = DTYPES[recipe.scales_dtype]

    with open(plan.src, "rb") as fsrc, open(partial, "wb") as fout:

        def emit(buf) -> None:
            nonlocal written
            fout.write(buf)
            hasher.update(buf)
            written += len(buf)

        emit(struct.pack("<Q", len(header_bytes)))
        emit(header_bytes)

        for i, src_key in enumerate(order):
            group = by_src[src_key]
            src_arr = read_tensor(fsrc, plan.data_start, plan.src_index[src_key])

            if group[0].kind == "copy":
                mx.eval(src_arr)
                emit(tensor_bytes(src_arr))
            else:
                # CPU stream: bit-deterministic across runs. The GPU quant
                # stream is not — that is the whole reason this is here.
                with mx.stream(mx.cpu):
                    w, sc, bi = mx.quantize(src_arr, **quantize_kwargs)
                    sc = sc.astype(scales_mx)
                    bi = bi.astype(scales_mx)
                    mx.eval(w, sc, bi)
                produced = {"q_weight": w, "q_scales": sc, "q_biases": bi}
                for t in group:
                    arr = produced[t.kind]
                    if list(arr.shape) != t.shape:
                        raise SystemExit(
                            f"{t.key}: planned shape {t.shape} != produced {list(arr.shape)} — plan/mlx disagree"
                        )
                    if MX_TO_ST.get(arr.dtype) != t.dtype:
                        raise SystemExit(f"{t.key}: planned dtype {t.dtype} != produced {arr.dtype}")
                    emit(tensor_bytes(arr))
                del w, sc, bi, produced
                n_q += 1

            del src_arr, group
            # MLX pools freed buffers; without this the pool grows to the size
            # of the largest few tensors x the allocator's high-water mark and
            # "RAM-bounded" stops meaning much on a 48 GB machine.
            _clear_cache()
            if verbose and (i % 200 == 0 or i == len(order) - 1):
                pct = 100.0 * written / max(1, len(header_bytes) + 8 + plan.total_bytes)
                print(
                    f"  [{i + 1}/{len(order)}] {pct:5.1f}%  {written / 1e9:6.2f} GB  "
                    f"peakRSS {_peak_rss_gb():.2f} GB  {time.time() - t0:6.0f}s",
                    flush=True,
                )

        fout.flush()
        os.fsync(fout.fileno())

    expected = 8 + len(header_bytes) + plan.total_bytes
    if written != expected:
        partial.unlink(missing_ok=True)
        raise SystemExit(f"wrote {written} bytes, planned {expected} — refusing to publish a truncated pack")

    os.replace(partial, out_path)
    stats = {
        "seconds": round(time.time() - t0, 1),
        "bytes": written,
        "tensors_out": len(plan.outputs),
        "modules_quantized": n_q,
        "peak_rss_gb": round(_peak_rss_gb(), 2),
    }
    return hasher.hexdigest(), stats


def copy_verbatim(src: Path, dst: Path, verbose: bool = True) -> tuple[str, int]:
    """Stream-copy a never-quantized component, hashing as we go."""
    partial = _partial_path(dst) if dst.suffix else dst.with_suffix(dst.suffix + ".partial")
    partial.unlink(missing_ok=True)
    h = hashlib.sha256()
    total = 0
    with open(src, "rb") as fi, open(partial, "wb") as fo:
        while chunk := fi.read(CHUNK):
            fo.write(chunk)
            h.update(chunk)
            total += len(chunk)
        fo.flush()
        os.fsync(fo.fileno())
    os.replace(partial, dst)
    if verbose:
        print(f"  copied {dst.name} ({total / 1e9:.2f} GB)", flush=True)
    return h.hexdigest(), total


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_pack_file(
    src: Path,
    out_path: Path,
    plan: Plan,
    recipe: Recipe,
    bits: int,
    *,
    probes: int = 8,
) -> dict:
    """Re-open the written file and prove it means what the source meant.

    Three independent checks:
      1. strict reader  — ``safetensors.safe_open`` (the Rust validator, which
         enforces contiguous non-overlapping offsets) must accept the file;
      2. header parity  — every planned key/shape/dtype is present;
      3. dequant probe  — dequantize N quantized modules and compare against
         the ORIGINAL source tensor.
    """
    from safetensors import safe_open

    with safe_open(str(out_path), framework="numpy") as f:
        strict_keys = set(f.keys())

    out_index, _, _ = read_header(out_path)
    planned = {t.key: t for t in plan.outputs}
    missing = set(planned) - set(out_index)
    extra = set(out_index) - set(planned)
    if missing or extra:
        raise SystemExit(f"header parity failed — missing={sorted(missing)[:5]} extra={sorted(extra)[:5]}")
    if strict_keys != set(planned):
        raise SystemExit("safetensors strict reader disagrees with our header")
    for k, t in planned.items():
        got = out_index[k]
        if list(got["shape"]) != t.shape or got["dtype"] != t.dtype:
            raise SystemExit(f"{k}: shape/dtype mismatch {got} vs {t}")

    q_modules = plan.quantized_modules
    if not q_modules:
        return {"probes": 0, "note": "no quantized modules in this file"}

    step = max(1, len(q_modules) // max(1, probes))
    chosen = q_modules[::step][:probes]
    worst_abs, worst_rel, worst_key = 0.0, 0.0, ""
    with open(src, "rb") as fsrc, open(out_path, "rb") as fout:
        out_start = read_header(out_path)[2]
        for base in chosen:
            ref = read_tensor(fsrc, plan.data_start, plan.src_index[base + ".weight"]).astype(mx.float32)
            w = read_tensor(fout, out_start, out_index[base + ".weight"])
            sc = read_tensor(fout, out_start, out_index[base + ".scales"]).astype(mx.float32)
            bi = read_tensor(fout, out_start, out_index[base + ".biases"]).astype(mx.float32)
            with mx.stream(mx.cpu):
                deq = mx.dequantize(w, sc, bi, group_size=recipe.group_size, bits=bits)
                err = mx.abs(deq - ref)
                a = float(mx.max(err))
                scale = float(mx.mean(mx.abs(ref))) or 1.0
                r = float(mx.mean(err)) / scale
            if a > worst_abs:
                worst_abs, worst_key = a, base
            worst_rel = max(worst_rel, r)
            del ref, w, sc, bi, deq, err
            _clear_cache()

    # Empirical ceilings for affine group quantization of a normal-ish weight
    # matrix: q4 keeps ~4 bits of a 64-wide group, q8 ~8. A blown budget here
    # means the wrong tensor was read or the group size is wrong — not noise.
    limit = 0.35 if bits == 4 else 0.12
    if worst_rel > limit:
        raise SystemExit(f"dequant probe: mean relative error {worst_rel:.4f} > {limit} — refusing to accept pack")
    return {
        "probes": len(chosen),
        "worst_max_abs_err": round(worst_abs, 6),
        "worst_mean_rel_err": round(worst_rel, 6),
        "worst_module": worst_key,
        "rel_err_limit": limit,
    }


def add_vendored_to_path() -> None:
    """Make the vendored ltx-2-mlx packages importable without installing them."""
    for pkg in ("ltx-core-mlx", "ltx-pipelines-mlx"):
        p = str(_vendored_src(pkg))
        if p not in sys.path:
            sys.path.insert(0, p)


def _pack_config(out_path: Path) -> Any:
    """The written pack's own architecture, or ``None`` if it declares none.

    Prefers the safetensors header (LTX-2.5 carries ``__metadata__["config"]``
    with the weights) and falls back to the sibling ``config.json`` /
    ``embedded_config.json`` that 2.3-era packs use. Returning ``None`` keeps
    the pre-2.5 behaviour — hardcoded defaults — for a checkpoint that says
    nothing about itself.
    """
    from ltx_core_mlx.model.transformer.model import LTXModelConfig

    for reader, arg in (
        (getattr(LTXModelConfig, "from_checkpoint_file", None), out_path),
        (getattr(LTXModelConfig, "from_checkpoint_dir", None), out_path.parent),
    ):
        if reader is None:
            continue
        try:
            cfg = reader(arg)
        except Exception:
            continue
        if cfg is not None:
            return cfg
    return None


def verify_load_ltx_dit(out_path: Path, config: Any = None) -> dict:
    """Full round-trip through the VENDORED loader.

    Mirrors ``ltx_pipelines_mlx.utils._orchestration.load_transformer`` exactly
    (``load_split_safetensors`` -> ``apply_quantization`` -> ``load_weights``
    -> ``mx.eval``, lines 102-115). The ONLY difference is that ``config`` may
    be supplied, so a tiny synthetic pack can be round-tripped without
    instantiating the real 48-layer / 4096-dim DiT. Pass ``None`` for the
    production shape.
    """
    add_vendored_to_path()
    from ltx_core_mlx.model.transformer.model import LTXModel, LTXModelConfig
    from ltx_core_mlx.utils.weights import _detect_quantization_bits, apply_quantization, load_split_safetensors

    weights = load_split_safetensors(out_path, prefix="transformer.")
    if config is None:
        # Build the reference model from the CHECKPOINT's architecture, not
        # from the dataclass defaults. The defaults are LTX-2.3's, and the
        # generation-varying flags are exactly the ones that decide which
        # parameters exist — so defaulting here would fail a correct 2.5 pack
        # and, worse, would pass a 2.5 pack that had silently lost them.
        config = _pack_config(out_path)
    dit = LTXModel(config) if config is not None else LTXModel()
    detected = _detect_quantization_bits(weights)
    apply_quantization(dit, weights)
    dit.load_weights(list(weights.items()))
    mx.eval(dit.parameters())
    return {"loaded": True, "n_weights": len(weights), "loader_detected_bits": detected}


def _vendored_src(pkg: str) -> Path:
    """Where to import the LTX packages from.

    Defaults to the app's own checkout, which is pinned to a released tag and
    must stay that way — ``install.js`` and ``update.js`` both ``git checkout``
    it, and the live panel runs out of that venv.

    ``LTX_MLX_SRC`` points this at a different working tree. That is not a
    convenience: a checkpoint generation newer than the pinned tag cannot be
    round-tripped against the pinned tag at all. LTX-2.5 ships
    ``keyframes_abs_pos_embedding``, which a v0.14.19 ``LTXModel`` does not
    build, so the verification would report a "parameter not in model" error
    that says nothing about the pack — the pack is fine, the reference model is
    simply older than the weights.
    """
    override = os.environ.get("LTX_MLX_SRC")
    root = Path(override).expanduser() if override else Path(__file__).resolve().parents[1] / "ltx-2-mlx"
    return root / "packages" / pkg / "src"


# --------------------------------------------------------------------------- #
# Pack-level artifacts
# --------------------------------------------------------------------------- #
MANIFEST_NAME = "phosphene_quant_manifest.json"


def load_manifest(out_dir: Path) -> dict:
    p = out_dir / MANIFEST_NAME
    if p.exists():
        return json.loads(p.read_text())
    return {"tool": TOOL, "tool_version": TOOL_VERSION, "files": {}}


def save_manifest(out_dir: Path, manifest: dict) -> None:
    manifest["written_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    (out_dir / MANIFEST_NAME).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


def write_quantize_config(out_dir: Path, recipe: Recipe, bits: int) -> None:
    """Emit ``quantize_config.json`` in the shipped 2.3 shape."""
    block: dict[str, Any] = {"bits": bits, "group_size": recipe.group_size}
    block.update(recipe.quant_config_extra)
    (out_dir / "quantize_config.json").write_text(json.dumps({"quantization": block}, indent=2) + "\n")


def patch_split_model(out_dir: Path, bits: int) -> None:
    """Mark ``split_model.json`` as quantized at these bits (2.3 pack does this)."""
    p = out_dir / "split_model.json"
    if not p.exists():
        return
    blob = json.loads(p.read_text())
    blob["quantized"] = True
    blob["quantization_bits"] = bits
    p.write_text(json.dumps(blob, indent=2, ensure_ascii=False) + "\n")


def write_shard_index(out_dir: Path, weight_map: dict[str, str], total_size: int) -> None:
    """Emit ``model.safetensors.index.json`` for a sharded component.

    NOTE: ``mlx_models/gemma-3-12b-it-4bit`` ships a STALE index inherited from
    its bf16 parent — it names five shards that do not exist and a total_size of
    24 GB against an 8 GB pack. Ours is generated from what we actually wrote.
    """
    (out_dir / "model.safetensors.index.json").write_text(
        json.dumps({"metadata": {"total_size": total_size}, "weight_map": weight_map}, indent=2) + "\n"
    )


def preflight_disk(out_dir: Path, need_bytes: int, margin_gb: float = 5.0) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(out_dir).free
    need = need_bytes + int(margin_gb * 1e9)
    if free < need:
        raise SystemExit(
            f"disk preflight FAILED: need {need / 1e9:.1f} GB "
            f"(output {need_bytes / 1e9:.1f} GB + {margin_gb:.0f} GB margin), "
            f"free {free / 1e9:.1f} GB on {out_dir}"
        )
    print(f"disk preflight OK: need {need / 1e9:.1f} GB, free {free / 1e9:.1f} GB", flush=True)


def clean_partials(out_dir: Path) -> int:
    n = 0
    if out_dir.exists():
        for p in out_dir.glob("*.partial*"):
            p.unlink()
            n += 1
    return n


# --------------------------------------------------------------------------- #
# Drivers
# --------------------------------------------------------------------------- #
def quantize_one(
    src: Path,
    out_dir: Path,
    out_name: str,
    recipe: Recipe,
    bits: int,
    args: argparse.Namespace,
    manifest: dict,
) -> None:
    out_path = out_dir / out_name
    plan = build_plan(src, recipe, bits, limit=args.limit)

    print(f"\n=== {src.name} -> {out_name}  [{recipe.name} q{bits} g{recipe.group_size}] ===", flush=True)
    print(
        f"  source {src.stat().st_size / 1e9:.2f} GB, {len(plan.src_index)} tensors; "
        f"plan {len(plan.outputs)} out-tensors, {len(plan.quantized_modules)} modules quantized, "
        f"{plan.total_bytes / 1e9:.2f} GB out",
        flush=True,
    )
    if plan.skipped_unaligned:
        print(
            f"  NOTE: {len(plan.skipped_unaligned)} eligible weights left bf16 "
            f"(in_features not a multiple of {recipe.group_size}): "
            f"{', '.join(k for k, _ in plan.skipped_unaligned[:3])}",
            flush=True,
        )

    prev = manifest["files"].get(out_name)
    if args.resume and out_path.exists() and prev and prev.get("sha256"):
        if sha256_file(out_path) == prev["sha256"]:
            print("  resume: existing file matches manifest sha256 — skipping", flush=True)
            return
        print("  resume: sha mismatch — rewriting", flush=True)

    if not args.no_preflight:
        preflight_disk(out_dir, plan.total_bytes)

    sha, stats = write_quantized(plan, out_path, recipe, bits, stamp_metadata=not args.no_stamp)
    print(
        f"  wrote {out_name}: {stats['bytes'] / 1e9:.2f} GB in {stats['seconds']}s "
        f"(peak RSS {stats['peak_rss_gb']} GB)",
        flush=True,
    )

    val = validate_pack_file(src, out_path, plan, recipe, bits, probes=args.probes)
    print(f"  validate: {json.dumps(val)}", flush=True)

    if args.verify_load == "ltx-dit":
        print(f"  loader round-trip: {json.dumps(verify_load_ltx_dit(out_path))}", flush=True)

    manifest["files"][out_name] = {
        "sha256": sha,
        "bytes": stats["bytes"],
        "source": src.name,
        "source_sha256_prefix": None,
        "recipe": recipe.name,
        "bits": bits,
        "group_size": recipe.group_size,
        "modules_quantized": stats["modules_quantized"],
        "tensors": stats["tensors_out"],
        "seconds": stats["seconds"],
        "peak_rss_gb": stats["peak_rss_gb"],
        "validation": val,
    }
    save_manifest(out_dir, manifest)


def quantize_sharded(
    src_dir: Path,
    out_dir: Path,
    recipe: Recipe,
    bits: int,
    args: argparse.Namespace,
    manifest: dict,
) -> None:
    """Sharded component (text encoder): one output shard per input shard."""
    shards = sorted(src_dir.glob("*.safetensors"))
    if not shards:
        raise SystemExit(f"no .safetensors in {src_dir}")
    plans = [(s, build_plan(s, recipe, bits, limit=args.limit)) for s in shards]
    total = sum(p.total_bytes for _, p in plans)
    if not args.no_preflight:
        preflight_disk(out_dir, total)

    weight_map: dict[str, str] = {}
    written_total = 0
    for i, (s, plan) in enumerate(plans, 1):
        name = f"model-{i:05d}-of-{len(plans):05d}.safetensors"
        print(f"\n=== {s.name} -> {name}  [{recipe.name} q{bits}] ===", flush=True)
        sha, stats = write_quantized(plan, out_dir / name, recipe, bits, stamp_metadata=not args.no_stamp)
        val = validate_pack_file(s, out_dir / name, plan, recipe, bits, probes=args.probes)
        print(f"  wrote {name} {stats['bytes'] / 1e9:.2f} GB; validate {json.dumps(val)}", flush=True)
        for t in plan.outputs:
            weight_map[t.key] = name
        written_total += stats["bytes"]
        manifest["files"][name] = {
            "sha256": sha,
            "bytes": stats["bytes"],
            "source": s.name,
            "recipe": recipe.name,
            "bits": bits,
            "group_size": recipe.group_size,
            "validation": val,
        }

    write_shard_index(out_dir, weight_map, written_total)
    # mlx-lm reads the quantization block out of config.json, not a sidecar.
    cfg_path = src_dir / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        cfg["quantization"] = {"group_size": recipe.group_size, "bits": bits}
        (out_dir / "config.json").write_text(json.dumps(cfg, indent=2) + "\n")
    for extra in ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "tokenizer.model"):
        p = src_dir / extra
        if p.exists():
            sha, n = copy_verbatim(p, out_dir / extra)
            manifest["files"][extra] = {"sha256": sha, "bytes": n, "source": extra, "recipe": "verbatim"}
    save_manifest(out_dir, manifest)


TRANSFORMER_RE = re.compile(r"^transformer(-(?P<variant>[a-z0-9_]+))?\.safetensors$")


def from_pack(src_dir: Path, out_dir: Path, recipe: Recipe, bits: int, args: argparse.Namespace) -> None:
    """Build a whole quantized LTX pack from a bf16 MLX-layout pack directory."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(out_dir)
    manifest["recipe"] = recipe.name
    manifest["bits"] = bits
    manifest["group_size"] = recipe.group_size
    manifest["source_pack"] = str(src_dir)

    variants = set(args.variants.split(",")) if args.variants else None
    transformers: list[Path] = []
    passthrough: list[Path] = []
    for p in sorted(src_dir.iterdir()):
        if p.is_dir() or p.name.startswith("."):
            continue
        m = TRANSFORMER_RE.match(p.name)
        if m:
            v = m.group("variant") or "default"
            if variants is None or v in variants:
                transformers.append(p)
            else:
                print(f"skipping transformer variant {v!r} (not in --variants)", flush=True)
            continue
        passthrough.append(p)

    if not transformers:
        raise SystemExit(f"no transformer*.safetensors selected in {src_dir}")

    plans_bytes = sum(build_plan(p, recipe, bits, limit=args.limit).total_bytes for p in transformers)
    copy_bytes = sum(p.stat().st_size for p in passthrough)
    if not args.no_preflight:
        preflight_disk(out_dir, plans_bytes + copy_bytes)

    for p in transformers:
        quantize_one(p, out_dir, p.name, recipe, bits, args, manifest)

    print("\n=== verbatim components (never quantized in the shipped packs) ===", flush=True)
    for p in passthrough:
        dst = out_dir / p.name
        prev = manifest["files"].get(p.name)
        if args.resume and dst.exists() and prev and prev.get("sha256") == sha256_file(dst):
            print(f"  resume: {p.name} already present", flush=True)
            continue
        sha, n = copy_verbatim(p, dst)
        manifest["files"][p.name] = {"sha256": sha, "bytes": n, "source": p.name, "recipe": "verbatim"}

    write_quantize_config(out_dir, recipe, bits)
    patch_split_model(out_dir, bits)
    for extra in ("quantize_config.json", "split_model.json"):
        pp = out_dir / extra
        if pp.exists():
            manifest["files"][extra] = {
                "sha256": sha256_file(pp),
                "bytes": pp.stat().st_size,
                "recipe": "generated",
            }
    save_manifest(out_dir, manifest)
    print(f"\nPACK COMPLETE -> {out_dir}", flush=True)


# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Quantize MLX-layout LTX weights to a loader-ready q4/q8 pack.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="recipes: " + "\n         ".join(f"{k:14s} {v.description}" for k, v in RECIPES.items()),
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--src", type=Path, help="single bf16 component (.safetensors) or a sharded dir with --layout sharded")
    g.add_argument("--from-pack", type=Path, help="a bf16 MLX-layout pack dir; quantize transformers, copy the rest")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--out-name", help="output filename for --src (default: same as source)")
    ap.add_argument("--recipe", default="ltx-dit", help="built-in recipe name or path to a recipe .json")
    ap.add_argument("--bits", type=int, default=4, choices=[2, 3, 4, 5, 6, 8])
    ap.add_argument("--group-size", type=int, default=None, help="override the recipe group size")
    ap.add_argument("--layout", choices=["single", "sharded"], default="single")
    ap.add_argument("--variants", default=None, help="comma list of transformer variants for --from-pack, e.g. distilled")
    ap.add_argument("--limit", type=int, default=None, help="only the first N source tensors (determinism/subset runs)")
    ap.add_argument("--probes", type=int, default=8, help="how many modules to dequant-probe during validation")
    ap.add_argument("--verify-load", choices=["none", "ltx-dit"], default="none", help="full vendored-loader round trip")
    ap.add_argument("--resume", action="store_true", help="skip outputs whose sha256 already matches the manifest")
    ap.add_argument("--no-preflight", action="store_true")
    ap.add_argument("--no-stamp", action="store_true", help="do not add phosphene provenance to safetensors metadata")
    args = ap.parse_args(argv)

    recipe = load_recipe(args.recipe)
    if args.group_size:
        recipe = dataclasses.replace(recipe, group_size=args.group_size)

    args.out_dir.mkdir(parents=True, exist_ok=True)
    n = clean_partials(args.out_dir)
    if n:
        print(f"cleaned {n} leftover .partial file(s)", flush=True)

    t0 = time.time()
    if args.from_pack:
        from_pack(args.from_pack, args.out_dir, recipe, args.bits, args)
    elif args.layout == "sharded":
        manifest = load_manifest(args.out_dir)
        manifest.update({"recipe": recipe.name, "bits": args.bits, "group_size": recipe.group_size})
        quantize_sharded(args.src, args.out_dir, recipe, args.bits, args, manifest)
    else:
        manifest = load_manifest(args.out_dir)
        manifest.update({"recipe": recipe.name, "bits": args.bits, "group_size": recipe.group_size})
        quantize_one(args.src, args.out_dir, args.out_name or args.src.name, recipe, args.bits, args, manifest)
        if recipe.quant_config_extra or recipe.name.startswith("ltx-"):
            write_quantize_config(args.out_dir, recipe, args.bits)
            patch_split_model(args.out_dir, args.bits)
        save_manifest(args.out_dir, manifest)

    print(f"\nDONE in {time.time() - t0:.0f}s — peak RSS {_peak_rss_gb():.2f} GB", flush=True)


if __name__ == "__main__":
    main()
