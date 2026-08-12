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
    # the whole 2.5 pack in ONE run — every input is planned together, so a
    # component fed by two files (the connector: blocks from the DiT, text
    # projection from the encoder) is written once, complete.
    python3 scripts/convert_ltx_mlx.py \\
        --input ~/dl/ltx-2.5-22b-distilled-transformer-bf16.safetensors \\
        --input ~/dl/ltx-2.5-video-vae-conv-bf16.safetensors \\
        --input ~/dl/ltx-2.5-audio-vae-bf16.safetensors \\
        --input ~/dl/ltx-2.5-duration-head-bf16.safetensors \\
        --input ~/dl/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors=spatial_upscaler_x2_v1_1 \\
        --output mlx_models/ltx-2.5-mlx-bf16 \\
        --transformer-stem transformer-distilled

    # see the routing decision without writing anything
    python3 scripts/convert_ltx_mlx.py --input X.safetensors --output OUT --dry-run

Run it in one invocation, not several: separate runs would each rewrite
``connector.safetensors`` from scratch and the last one would win.
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

# Per-channel latent statistics are spelled three different ways by three
# different consumers, and the SAME two source tensors feed all of them. The
# video VAE ships one copy that both the encoder and the decoder need, under
# leaf names that differ between the two. Getting this wrong does not raise —
# it denormalises latents with random stats and yields plausible-looking mush.
_STATS_ENCODER = (("mean-of-means", "_mean_of_means"), ("std-of-means", "_std_of_means"))
_STATS_DECODER = (("mean-of-means", "mean"), ("std-of-means", "std"))


# Source-key prefixes that identify a component. Longest prefix wins; when
# several routes share that longest prefix, ALL of them fire — that is how one
# source tensor fans out into two components.
@dataclass(frozen=True)
class Route:
    source_prefix: str
    stem: str
    target_prefix: str
    rename: bool = True
    """Apply RENAME_RULES. False for components whose on-disk contract keeps the
    PyTorch spelling — the embeddings connector is the one that matters: its
    attention output stays list-wrapped (``to_out.0.*``) and its feed-forward
    stays ``ff.net.0.proj`` / ``ff.net.2``, because the vendored
    ``ConnectorAttention`` builds exactly those names. Renaming them the way the
    DiT blocks are renamed produces a connector that cannot load."""
    leaf_map: tuple[tuple[str, str], ...] = ()
    """Exact trailing-segment substitutions applied after prefixing."""


DEFAULT_ROUTES: tuple[Route, ...] = (
    Route("model.diffusion_model.duration_head.", "duration_head", "duration_head."),
    Route("duration_head.", "duration_head", "duration_head."),
    # The 2.5 DiT packs both connectors in-file; 2.3 shipped them as a separate
    # connector.safetensors. Either way they land in connector.safetensors, and
    # neither is subject to the DiT rename rules.
    Route(
        "model.diffusion_model.video_embeddings_connector.",
        "connector",
        "connector.video_embeddings_connector.",
        rename=False,
    ),
    Route(
        "model.diffusion_model.audio_embeddings_connector.",
        "connector",
        "connector.audio_embeddings_connector.",
        rename=False,
    ),
    # 2.5 relocated the dual text projection out of the connector and into the
    # text-encoder file. Our pack layout still wants it beside the connector,
    # because that is where GemmaFeatureExtractor looks for it.
    Route("text_embedding_projection.", "connector", "connector.text_embedding_projection.", rename=False),
    Route("connector.", "connector", "connector.", rename=False),
    Route("vae.encoder.", "vae_encoder", "vae_encoder."),
    Route("vae.decoder.", "vae_decoder", "vae_decoder."),
    Route("vae_encoder.", "vae_encoder", "vae_encoder."),
    Route("vae_decoder.", "vae_decoder", "vae_decoder."),
    # One source copy, two destinations, different leaf names in each.
    Route(
        "per_channel_statistics.",
        "vae_encoder",
        "vae_encoder.per_channel_statistics.",
        leaf_map=_STATS_ENCODER,
    ),
    Route(
        "per_channel_statistics.",
        "vae_decoder",
        "vae_decoder.per_channel_statistics.",
        leaf_map=_STATS_DECODER,
    ),
    Route("encoder.", "vae_encoder", "vae_encoder."),
    Route("decoder.", "vae_decoder", "vae_decoder."),
    Route(
        "audio_vae.per_channel_statistics.",
        "audio_vae",
        "audio_vae.per_channel_statistics.",
        leaf_map=_STATS_ENCODER,
    ),
    Route("audio_vae.", "audio_vae", "audio_vae."),
    # 2.5 nests the base vocoder one level deeper than 2.3 did; bwe_generator
    # and mel_stft sit at the top level in both. Collapse only the doubled
    # segment, and let the shorter route carry the other two through.
    Route("vocoder.vocoder.", "vocoder", "vocoder."),
    Route("vocoder.", "vocoder", "vocoder."),
    Route("model.diffusion_model.", "transformer", "transformer."),
    Route("diffusion_model.", "transformer", "transformer."),
    Route("transformer.", "transformer", "transformer."),
)


# ---------------------------------------------------------------------------
# The OTHER video VAE
#
# LTX-2.5 ships two video VAEs, and they differ only in the decoder half:
#
#   ltx-2.5-video-vae-conv-bf16.safetensors   170 tensors, legacy conv decoder
#   ltx-2.5-video-vae-bf16.safetensors        396 tensors, NA diffusion decoder
#
# Both carry the SAME conv encoder and the SAME per-channel statistics, and both
# spell their decoder ``decoder.*``. Routing the second one with the default table
# would therefore produce a ``vae_decoder.safetensors`` full of transformer weights
# under a filename the conv decoder loads — a shape error at best, and at worst a
# silently wrong pack. So the decoder half of a diffusion VAE file goes to its own
# stem, and the file is identified the way the vendor identifies it: by one key.
# ---------------------------------------------------------------------------

DIFFUSION_DECODER_STEM = "vae_decoder_diffusion"

#: ComfyUI's own detection rule (``comfy/sd.py`` at the LTX-2.5 merge): the presence
#: of this key is what selects ``CausalDiffusionVAE`` over the conv ``VideoVAE``.
DIFFUSION_DECODER_MARKER = "decoder.conv_in_x_t.weight"


def is_diffusion_vae_header(header: dict) -> bool:
    return any(k == DIFFUSION_DECODER_MARKER or k.endswith("." + DIFFUSION_DECODER_MARKER) for k in header)


def diffusion_vae_routes(routes: tuple[Route, ...]) -> tuple[Route, ...]:
    """Re-point every decoder-bound route in ``routes`` at the diffusion stem.

    The encoder routes are left exactly as they are: the encoder in this file is the
    same conv encoder the conv VAE file carries, so it converts to the same bytes and
    the pack ends up with one ``vae_encoder.safetensors`` either way.
    """
    swapped: list[Route] = []
    for route in routes:
        if route.stem != "vae_decoder":
            swapped.append(route)
            continue
        # The conv decoder is the whole component, so its keys sit at the file root
        # (``vae_decoder.conv_in.*``). The diffusion decoder is a *decoder inside a
        # VAE wrapper* that also owns the per-channel statistics, so its keys keep the
        # ``decoder.`` level the checkpoint spells. Matching the module tree here is
        # what lets the loader run ``strict=True`` with no rename table at all.
        if route.target_prefix == "vae_decoder.":
            target = f"{DIFFUSION_DECODER_STEM}.decoder."
        else:
            target = route.target_prefix.replace("vae_decoder.", f"{DIFFUSION_DECODER_STEM}.", 1)
        swapped.append(
            Route(
                source_prefix=route.source_prefix,
                stem=DIFFUSION_DECODER_STEM,
                target_prefix=target,
                rename=route.rename,
                leaf_map=route.leaf_map,
            )
        )
    return tuple(swapped)


# ---------------------------------------------------------------------------
# Convolution layout
#
# MLX puts the input-channel axis LAST; PyTorch puts it second. Every LTX
# release before 2.5 reached us already permuted, because `mlx-forge` did it
# during conversion — which is why the vendored package says flatly that all
# weights must be in MLX format on disk and that a PyTorch-format tensor is a
# converter bug, not something to work around at load time. 2.5 is the first
# checkpoint we convert ourselves, so the permutation has to live here.
#
# The DiT and the connector are pure Linear (rank <= 2) and are untouched;
# every affected tensor is in the VAEs, the vocoder and the upscalers. The
# table below is not inferred from module names — it was derived by diffing
# every shared key of the freshly converted 2.5 pack against the shipped 2.3
# pack, which is ground truth for MLX layout. All 768 differing tensors are
# explained by exactly four permutations, with no exceptions and no leftovers.
# ---------------------------------------------------------------------------

CONV_PERMUTATIONS: dict[int, tuple[int, ...]] = {
    3: (0, 2, 1),        # Conv1d          (O, I, K)       -> (O, K, I)
    4: (0, 2, 3, 1),     # Conv2d          (O, I, H, W)    -> (O, H, W, I)
    5: (0, 2, 3, 4, 1),  # Conv3d          (O, I, D, H, W) -> (O, D, H, W, I)
}

#: ConvTranspose1d stores (I, O, K) and needs (O, K, I) — a different rank-3
#: permutation from Conv1d's. In the LTX vocoder these are exactly the
#: upsampling stacks: 11 tensors, all ``*.ups.<n>.weight``, and no Conv1d
#: weight anywhere in the pack matches that pattern.
CONV_TRANSPOSE_1D_PERM: tuple[int, ...] = (1, 2, 0)
_CONV_TRANSPOSE_MARKER = ".ups."


def conv_permutation(key: str, shape: list) -> tuple[int, ...] | None:
    """The axis permutation this tensor needs, or ``None`` to copy verbatim.

    Rank alone decides. Restricting this to ``*.weight`` looks safer and is
    wrong: the vocoder's anti-aliasing resamplers carry their kernels as
    ``*.filter`` buffers, 402 of them, in the very same channel layout as a
    Conv1d weight. In the 2.3 pack — ground truth — **every** tensor of rank 3,
    4 or 5 is permuted and none is left alone, so a name-based predicate can
    only ever be a way to miss some.
    """
    if len(shape) not in CONV_PERMUTATIONS:
        return None
    if len(shape) == 3 and _CONV_TRANSPOSE_MARKER in key:
        return CONV_TRANSPOSE_1D_PERM
    return CONV_PERMUTATIONS[len(shape)]


def permuted_shape(shape: list, perm: tuple[int, ...]) -> list:
    return [shape[i] for i in perm]


def permute_bytes(raw: bytes, shape: list, perm: tuple[int, ...], itemsize: int) -> bytes:
    """Reorder axes without interpreting the element type.

    Views the payload as raw bytes with a trailing element axis, so bf16 —
    which numpy has no native dtype for — permutes exactly like anything else.
    A permutation moves numbers; it never changes one.
    """
    import numpy as np

    arr = np.frombuffer(raw, dtype=np.uint8).reshape(*shape, itemsize)
    return np.ascontiguousarray(arr.transpose(*perm, len(shape))).tobytes()


def rename_key(key: str) -> str:
    for old, new in RENAME_RULES:
        key = key.replace(old, new)
    return key


def apply_leaf_map(key: str, leaf_map: tuple[tuple[str, str], ...]) -> str:
    for old, new in leaf_map:
        if key.endswith(old):
            return key[: -len(old)] + new
    return key


def route_key(key: str, routes: tuple[Route, ...]) -> list[Route]:
    """Longest-prefix match, returning EVERY route that ties for longest.

    Returns an empty list when nothing claims the key. Ties are meaningful:
    two routes sharing a source prefix duplicate one source tensor into two
    output components (the video VAE's per-channel statistics need exactly
    that, under a different leaf name in each).
    """
    best = -1
    winners: list[Route] = []
    for route in routes:
        if key.startswith(route.source_prefix):
            length = len(route.source_prefix)
            if length > best:
                best, winners = length, [route]
            elif length == best:
                winners.append(route)
    return winners


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
    permute: tuple = ()
    """Axis permutation from the source's layout to MLX's. Empty = verbatim."""

    @property
    def nbytes(self) -> int:
        # A permutation reorders bytes; it never adds or removes any.
        return self.source_end - self.source_begin

    @property
    def target_shape(self) -> list:
        return permuted_shape(self.shape, self.permute) if self.permute else self.shape


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
    inputs: list[Path] | list[tuple[Path, str | None]],
    routes: tuple[Route, ...] = DEFAULT_ROUTES,
    *,
    transformer_stem: str = "transformer",
    allow_unmapped: bool = False,
) -> tuple[dict[str, ComponentPlan], list[str], dict]:
    """Decide every output byte before reading a single weight.

    Each input is either a path (routed by key prefix) or a ``(path, stem)``
    pair. An explicit stem is a **fallback for keys the route table does not
    claim**, not an override: routed keys still go where the table says.

    That fallback is what makes two otherwise-impossible files convertible.
    The latent upscalers ship bare, unprefixed keys (``final_conv.*``,
    ``res_blocks.*``) that are byte-identical between the spatial and the
    temporal file — nothing in the key can say which component it is, only the
    filename can. And the text encoder ships the DiT's text projection
    (``text_embedding_projection.*``, which belongs in the connector) sitting
    beside a 26 GB Gemma tower (which does not belong in this pack at all);
    because the fallback yields to the route table, one pass puts each where it
    goes.

    Returns ``(plans_by_stem, unmapped_source_keys, source_metadata)``.
    """
    plans: dict[str, ComponentPlan] = {}
    unmapped: list[str] = []
    merged_metadata: dict = {}

    normalised: list[tuple[Path, str | None]] = [
        item if isinstance(item, tuple) else (item, None) for item in inputs
    ]

    for path, forced_stem in normalised:
        header, data_offset = read_header(path)
        metadata = header.get("__metadata__") or {}
        for key, value in metadata.items():
            merged_metadata.setdefault(key, value)

        # Per-file: the diffusion video VAE re-points its decoder half at its own
        # stem. Decided from the file's own header, not from a flag, so the two video
        # VAE files cannot be converted into each other's filename by mistake.
        file_routes = diffusion_vae_routes(routes) if is_diffusion_vae_header(header) else routes

        for key, entry in header.items():
            if key == "__metadata__":
                continue
            matches = route_key(key, file_routes)
            if not matches and forced_stem:
                # Verbatim: a file pinned by name has told us nothing about its
                # module structure, so applying the DiT's rename table would be
                # a guess. Verified correct for the upscalers, whose 2.5 keys
                # are identical to the 2.3 pack's once the stem prefix is added.
                matches = [Route("", forced_stem, f"{forced_stem}.", rename=False)]
            if not matches:
                unmapped.append(f"{path.name}::{key}")
                continue

            expected = tensor_nbytes(entry)
            begin, end = entry["data_offsets"]
            actual = end - begin
            if expected != actual:
                raise ValueError(
                    f"{path.name}::{key} header is self-inconsistent: "
                    f"{entry['dtype']}{entry['shape']} needs {expected} bytes, "
                    f"offsets span {actual}. Refusing to convert a corrupt file."
                )

            for route in matches:
                stem = transformer_stem if route.stem == "transformer" else route.stem
                suffix = key[len(route.source_prefix) :]
                target_key = route.target_prefix + suffix
                if route.rename:
                    target_key = rename_key(target_key)
                target_key = apply_leaf_map(target_key, route.leaf_map)

                plan = plans.setdefault(stem, ComponentPlan(stem=stem))
                if not plan.metadata and metadata:
                    # Which file a component came from decides its config. The
                    # upscalers each carry their own and they are NOT the same
                    # shape as each other, so a single merged blob cannot serve
                    # them: the spatial one is mid_channels 1024, the temporal
                    # 512, and building either at the other's width fails to
                    # load with a shape error that names no cause.
                    plan.metadata = dict(metadata)
                plan.tensors.append(
                    PlannedTensor(
                        target_key=target_key,
                        source_key=key,
                        source_path=path,
                        source_begin=data_offset + begin,
                        source_end=data_offset + end,
                        dtype=entry["dtype"],
                        shape=list(entry["shape"]),
                        permute=conv_permutation(target_key, list(entry["shape"])) or (),
                    )
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
            "shape": tensor.target_shape,
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
                    if tensor.permute:
                        # Bounded: the largest permuted tensor in the 2.5 pack
                        # is the spatial upscaler's 75 MB Conv2d. Still one
                        # tensor resident, never one checkpoint.
                        raw = fh.read(tensor.nbytes)
                        if len(raw) != tensor.nbytes:
                            raise IOError(
                                f"{tensor.source_path.name}: truncated reading {tensor.source_key}"
                            )
                        itemsize = DTYPE_SIZES[tensor.dtype]
                        buf = permute_bytes(raw, tensor.shape, tensor.permute, itemsize)
                        if len(buf) != tensor.nbytes:
                            raise IOError(
                                f"{tensor.target_key}: permutation changed the byte count "
                                f"({len(buf)} vs {tensor.nbytes})"
                            )
                        out.write(buf)
                        digest.update(buf)
                        written += len(buf)
                        del raw, buf
                        continue
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

    def parse(raw):
        if not raw:
            return {}
        try:
            return json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    config = parse(metadata.get("config"))
    if "model_version" in metadata and "model_version" not in config:
        config["model_version"] = metadata["model_version"]

    # embedded_config.json is the UNION of every component's own config, keyed
    # by the section names the components already use ("transformer", "vae",
    # "audio_vae", "vocoder", "scheduler") — the same shape the 2.3 pack ships.
    # 2.5 splits that blob across the component files; reassembling it here is
    # what keeps a 2.5 pack readable by the ordinary config path.
    embedded = dict(config)
    for plan in plans.values():
        for key, value in parse(plan.metadata.get("config")).items():
            embedded.setdefault(key, value)

    if config:
        (out_dir / "config.json").write_text(json.dumps(config, indent=2))
        written.append(out_dir / "config.json")
    if embedded:
        (out_dir / "embedded_config.json").write_text(json.dumps(embedded, indent=2))
        written.append(out_dir / "embedded_config.json")

    # Per-component sidecar for anything the pipeline builds from a
    # "<stem>_config.json" beside the weights. Today that is the latent
    # upscalers, and the file itself says so via _class_name — so this keys off
    # the checkpoint's own declaration rather than off a filename convention.
    for stem, plan in sorted(plans.items()):
        own = parse(plan.metadata.get("config"))
        if own.get("_class_name") != "LatentUpsampler":
            continue
        path = out_dir / f"{stem}_config.json"
        path.write_text(json.dumps({"config": own}, indent=2))
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


def parse_input_spec(raw: str) -> tuple[Path, str | None]:
    """``PATH`` or ``PATH=STEM``.

    The stem form pins a whole file to one output component. Split on the last
    ``=`` only when the right-hand side looks like a stem rather than part of a
    path, so a directory containing ``=`` still works.
    """
    if "=" in raw:
        head, _, tail = raw.rpartition("=")
        if head and tail and "/" not in tail and not tail.endswith(".safetensors"):
            return Path(head), tail
    return Path(raw), None


def convert(
    inputs: list[tuple[Path, str | None]],
    out_dir: Path,
    *,
    transformer_stem: str = "transformer",
    allow_unmapped: bool = False,
    dry_run: bool = False,
    skip_stems: tuple[str, ...] = (),
) -> dict:
    plans, unmapped, metadata = plan_conversion(
        inputs, transformer_stem=transformer_stem, allow_unmapped=allow_unmapped
    )

    # A skipped stem is PLANNED and reported, then not written — for components
    # another tool owns. That is categorically different from --allow-unmapped:
    # the keys are still accounted for by name, so nothing can go missing
    # unnoticed. It exists because the text encoder's Gemma tower travels in the
    # same file as the connector's text projection but belongs in its own pack.
    skipped = {stem: plans.pop(stem) for stem in skip_stems if stem in plans}

    total = sum(plan.nbytes for plan in plans.values())
    report = {
        "components": {
            stem: {
                "tensors": len(p.tensors),
                "bytes": p.nbytes,
                "permuted": sum(1 for t in p.tensors if t.permute),
            }
            for stem, p in sorted(plans.items())
        },
        "skipped": {stem: {"tensors": len(p.tensors), "bytes": p.nbytes} for stem, p in sorted(skipped.items())},
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
    parser.add_argument(
        "--input",
        action="append",
        required=True,
        help="source safetensors, repeatable. Use PATH=STEM to pin a whole file "
        "to one output component (needed for the latent upscalers, whose keys "
        "carry no component name).",
    )
    parser.add_argument("--output", required=True, type=Path, help="destination pack directory")
    parser.add_argument(
        "--transformer-stem",
        default="transformer-distilled",
        help="output filename stem for the DiT (default: transformer-distilled; "
        "use transformer-dev for the full/trainable checkpoint)",
    )
    parser.add_argument("--dry-run", action="store_true", help="plan and report, write nothing")
    parser.add_argument(
        "--skip-stem",
        action="append",
        default=[],
        help="plan and report this component but do not write it (repeatable). "
        "For components another tool owns — unlike --allow-unmapped, the keys "
        "are still named in the report, so nothing vanishes unnoticed.",
    )
    parser.add_argument("--allow-unmapped", action="store_true", help="do not abort on unroutable keys")
    parser.add_argument(
        "--i-know-what-im-dropping",
        action="store_true",
        help="required alongside --allow-unmapped for a real (non-dry-run) conversion",
    )
    args = parser.parse_args(argv)

    specs = [parse_input_spec(raw) for raw in args.input]
    for path, _ in specs:
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
            specs,
            args.output,
            transformer_stem=args.transformer_stem,
            allow_unmapped=args.allow_unmapped,
            dry_run=args.dry_run,
            skip_stems=tuple(args.skip_stem),
        )
    except UnmappedKeys as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3

    print()
    for stem, info in report["components"].items():
        perm = f"  ({info['permuted']} conv-permuted)" if info.get("permuted") else ""
        print(f"{stem:34s} {info['tensors']:6d} tensors  {human(info['bytes'])}{perm}")
    print(f"{'TOTAL':34s} {'':6s}          {human(report['total_bytes'])}")
    for stem, info in report.get("skipped", {}).items():
        print(f"{'(skipped) ' + stem:34s} {info['tensors']:6d} tensors  {human(info['bytes'])}")
    if report["unmapped"]:
        print(f"\nWARNING: {len(report['unmapped'])} unmapped source tensors were dropped.", file=sys.stderr)
    if args.dry_run:
        print("\n(dry run — nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
