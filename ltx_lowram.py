"""Low-memory LTX rendering for 16-24 GB Macs (5.0.20).

Three exact techniques the warm helper uses on the block-streamed path
(``LTX_LOW_RAM_STREAM``, every Mac with 24 GB or less). "Exact" is measured,
not argued: each one leaves the final video and audio latents bit-identical
to the path it replaces (proof numbers in docs/LOWRAM_BUILD_2026-11.md).

1. **Layer-streamed prompt encode** (:func:`streamed_prompt_encode`).
   4.19.10 already split the encode in two halves (the text model, freed,
   then the connector), which took the encode peak from ~15.5 GB to ~8.6 GB.
   Each half was still loaded whole: the 6.7 GB Gemma 4 tower, then the
   6.3 GB bf16 connector. Here both are built over LAZY weights and walked
   one layer / one connector block at a time: a layer's weights are read just
   before it runs and dropped as soon as its output is materialized. Same
   modules, same primitives, same order, so the embeddings do not change;
   the encode keeps about one layer and the hidden states resident instead
   of a whole model. Each read is still done on its own, before the GPU work
   that needs it (the 4.19.17 watchdog fix, now per layer).

2. **Prompt-embedding cache** (:class:`EmbeddingCache`). A retake, a new seed
   or a queue of takes of one prompt re-ran the whole encode: ~13 GB read
   from disk and the phase where most 16 GB failures happen. The embeddings
   are a pure function of (prompt, text encoder, connector, max length), so a
   small LRU of them skips the encode, bit for bit.

3. **Runtime LoRAs on the streamed transformer** (:class:`StreamedLoras`).
   The block streamer rebinds ONE shared block's weights per index, so the
   engine could only FUSE a LoRA into streamed blocks - which on a Q4 pack
   destroys ~93 % of the delta - and refuses the exact unfused branch. Here
   the shared block's linears are wrapped once with the engine's own
   unfused adapters, and every bind loads that block's A/B next to its base
   weights. The arithmetic is the resident unfused path's, so a character or
   style LoRA renders the same on a 16 GB Mac as on a 64 GB one, at block-
   streaming memory instead of a resident 11 GB transformer.

Nothing here changes a Mac over 24 GB: the helper only calls into this
module on the streamed path (the cache aside, which is exact everywhere).
"""
from __future__ import annotations

import collections
import os
import sys
import threading
from pathlib import Path

__all__ = [
    "EmbeddingCache",
    "EMBEDDINGS",
    "StreamedLoras",
    "embedding_key",
    "stream_text_encoder_wanted",
    "streamed_prompt_encode",
]


def _mx():
    import mlx.core as mx
    return mx


def _materialize(*trees) -> None:
    """MLX graph evaluation of every array in ``trees`` (never Python eval)."""
    mx = _mx()
    from mlx.utils import tree_flatten
    arrays = []
    for tree in trees:
        if isinstance(tree, mx.array):
            arrays.append(tree)
        elif tree is not None:
            arrays.extend(v for _k, v in tree_flatten(tree) if isinstance(v, mx.array))
    if arrays:
        getattr(mx, "eval")(arrays)


def _cleanup() -> None:
    try:
        from ltx_core_mlx.utils.memory import aggressive_cleanup
        aggressive_cleanup()
    except Exception:
        try:
            _mx().clear_cache()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 1. Layer-streamed prompt encode
# ---------------------------------------------------------------------------


def stream_text_encoder_wanted() -> bool:
    """On unless ``LTX_STREAM_TEXT_ENCODER=0`` (the escape hatch). The helper
    asks only on the streamed path, so this never changes a big Mac."""
    return (os.environ.get("LTX_STREAM_TEXT_ENCODER") or "1").strip().lower() not in (
        "0", "false", "no", "off")


class _UseOnce:
    """Stand-in for one layer of a model that is walked exactly once.

    On call: read the layer's weights (Load nodes only, so the disk read runs
    on its own with no GPU work queued behind it - the 4.19.17 watchdog
    lesson), run the layer, materialize its output, then drop the layer so
    its weights are freed before the next one is read. Attribute reads pass
    through (the Gemma 4 walk asks each layer for its ``layer_type``).
    A second call is a bug in the caller and raises instead of silently
    computing with nothing."""

    __slots__ = ("_m", "_name", "_after", "_next")

    def __init__(self, module, name: str, after=None, read_ahead=None):
        object.__setattr__(self, "_m", module)
        object.__setattr__(self, "_name", name)
        object.__setattr__(self, "_after", after)
        object.__setattr__(self, "_next", read_ahead)

    def __call__(self, *args, **kwargs):
        module = object.__getattribute__(self, "_m")
        if module is None:
            raise RuntimeError(f"streamed {self._name} was used twice")
        _materialize(module.parameters())        # a no-op when read ahead
        # Read the NEXT layer's weights while this one computes: Load nodes
        # run on the CPU stream, and no GPU work waits on them, so the disk
        # read overlaps the compute (two layers resident, not one).
        nxt = object.__getattribute__(self, "_next")
        if nxt is not None:
            object.__setattr__(self, "_next", None)
            _read_ahead(nxt)
        out = module(*args, **kwargs)
        _materialize(out)
        object.__setattr__(self, "_m", None)
        after = object.__getattribute__(self, "_after")
        if after is not None:
            after()
        return out

    def __getattr__(self, name):
        module = object.__getattribute__(self, "_m")
        if module is None:
            raise AttributeError(name)
        return getattr(module, name)


def _read_ahead(module) -> None:
    mx = _mx()
    from mlx.utils import tree_flatten
    try:
        arrays = [v for _k, v in tree_flatten(module.parameters()) if isinstance(v, mx.array)]
        if arrays:
            mx.async_eval(arrays)
    except Exception:
        pass   # read ahead is an optimisation; the layer reads its own weights anyway


def _free() -> None:
    """Per-layer release. No gc pass (the walk holds no cycles, and a full
    collection per layer costs seconds over 65 layers): dropping the last
    reference frees the arrays, and this returns the buffers to the system."""
    try:
        _mx().clear_cache()
    except Exception:
        pass


def _stream_list(items, label: str, first_after=None) -> None:
    """Replace each module in ``items`` (a list) by a use-once stand-in that
    reads the following module ahead."""
    raw = list(items)
    for i, module in enumerate(raw):
        after = first_after if (i == 0 and first_after is not None) else _free
        items[i] = _UseOnce(module, f"{label} {i}", after,
                            read_ahead=raw[i + 1] if i + 1 < len(raw) else None)


def _lazy_gemma4_language_model(gemma_path: str):
    """A GemmaLanguageModel whose Gemma 4 tower holds LAZY weights.

    The engine's ``load_gemma4_tower`` minus its final ``mx.eval``: the same
    config, sanitizer, pack quantization and strict load, so the tower is the
    same module with the same arrays - nothing is read from disk until a layer
    runs. Returns None for a Gemma 3 pack (LTX-2.3), which keeps the 4.19.10
    split encode."""
    from ltx_core_mlx.text_encoders.gemma.encoders import base_encoder as be
    from ltx_core_mlx.text_encoders.gemma import gemma4_pack as gp
    from ltx_core_mlx.text_encoders.gemma.gemma4 import (
        Gemma4TextConfig,
        Gemma4TextTower,
        sanitize_weights,
    )
    from ltx_core_mlx.text_encoders.gemma.loader import detect_text_encoder, resolve_text_tower

    te = be.GemmaLanguageModel()
    te._architecture = resolve_text_tower(detect_text_encoder(gemma_path))
    if not te.is_gemma4:
        return None
    raw = gp.read_config(gemma_path)
    cfg = Gemma4TextConfig.from_dict(raw)
    tower = Gemma4TextTower(cfg)
    weights = sanitize_weights(gp.load_shard_weights(gemma_path), cfg)
    gp.apply_pack_quantization(tower, weights, raw)
    tower.load_weights(list(weights.items()), strict=True)
    del weights
    te._model = tower
    te._tokenizer = be._load_tokenizer(gemma_path)
    te._model_path = gemma_path
    te._phos_lazy = True
    return te


def _stream_tower_layers(tower) -> None:
    layers = tower.layers
    released = {"embed": False}

    def drop_embedding():
        # The first layer's output is materialized, so the token embeddings
        # (states[0]) are too: the ~0.5 GB embedding table is no longer used.
        if not released["embed"]:
            released["embed"] = True
            try:
                tower.pop("embed_tokens", None)
            except Exception:
                pass
            _free()

    _stream_list(layers, "text layer", first_after=drop_embedding)


class _OneSideAtATime:
    """The connector's text projection, one output at a time.

    ``TextEmbeddingProjection`` holds two big linears - video (1.54 GB, one
    tensor) and audio (0.77 GB) - and computes video, then audio. This runs
    the same two products with the same scale (``sqrt(dim / embedding_dim)``,
    the engine's formula) in the same order, but reads each weight just
    before its product and frees it right after, so both are never resident
    together."""

    def __init__(self, projection):
        self._p = projection

    def __call__(self, hidden_states):
        import math
        p = self._p
        if p is None:
            raise RuntimeError("streamed text projection was used twice")
        outs = []
        for name in ("video_aggregate_embed", "audio_aggregate_embed"):
            linear = p[name]
            _materialize(linear.parameters())
            scale = math.sqrt(linear.weight.shape[0] / p.embedding_dim)
            y = linear(hidden_states * scale)
            _materialize(y)
            outs.append(y)
            p.pop(name, None)
            del linear
            _free()
        self._p = None
        return outs[0], outs[1]


def _lazy_connector(model_dir):
    from ltx_core_mlx.text_encoders.gemma.feature_extractor import GemmaFeaturesExtractorV2
    from ltx_core_mlx.utils.weights import load_split_safetensors

    fx = GemmaFeaturesExtractorV2()
    weights = load_split_safetensors(Path(model_dir) / "connector.safetensors", prefix="connector.")
    fx.connector.load_weights(list(weights.items()))
    del weights
    c = fx.connector
    # nn.Module is a dict: assign the stand-ins as items so attribute reads
    # (which look in the dict first) find them.
    c["text_embedding_projection"] = _OneSideAtATime(c["text_embedding_projection"])
    for side in ("video_embeddings_connector", "audio_embeddings_connector"):
        _stream_list(c[side].transformer_1d_blocks, f"{side} block")
    return fx


def _debug_peak(what: str) -> None:
    if not os.environ.get("LTX_LOWRAM_DEBUG"):
        return
    mx = _mx()
    print(f"[lowram-debug] {what}: MLX peak {mx.get_peak_memory() / 1e9:.2f} GB",
          file=sys.stderr, flush=True)
    mx.reset_peak_memory()


def streamed_prompt_encode(pe, prompt: str, max_length: int, log=None):
    """Encode ``prompt`` for PromptEncoder ``pe`` one layer at a time.

    Returns ``(video, audio)`` embeddings, or None when the pack is not one
    this streams (Gemma 3): the caller then keeps the 4.19.10 split encode.
    Leaves ``pe`` holding neither model."""
    import time as _time
    t0 = _time.monotonic()
    te = getattr(pe, "_text_encoder", None)
    if te is None or not getattr(te, "_phos_lazy", False) or getattr(te, "_phos_used", False):
        pe._text_encoder = None
        _cleanup()
        te = _lazy_gemma4_language_model(pe.gemma_model_id)
        if te is None:
            return None
    te._phos_used = True
    pe._feature_extractor = None
    _stream_tower_layers(te._model)
    hidden, mask = te.encode_all_layers(prompt, max_length=max_length)
    _materialize(hidden, mask)
    pe._text_encoder = None
    del te
    _cleanup()
    _debug_peak("text model")
    fx = _lazy_connector(pe.model_dir)
    video, audio = fx(hidden, attention_mask=mask)
    _materialize(video, audio)
    del fx, hidden, mask
    _cleanup()
    _debug_peak("connector")
    if log is not None:
        log(f"[text-encoder] prompt encoded layer by layer in "
            f"{_time.monotonic() - t0:.1f} s (low-memory encode)")
    return video, audio


def lazy_text_encoder_for_load(pe):
    """For ``PromptEncoder.load`` on the streamed path: the tokenizer is
    there for callers that read it (Prompt Relay), and no weight is read."""
    if getattr(pe, "_text_encoder", None) is not None:
        return True
    te = _lazy_gemma4_language_model(pe.gemma_model_id)
    if te is None:
        return False
    pe._text_encoder = te
    return True


# ---------------------------------------------------------------------------
# 1b. Time-chunked VAE decode (exact)
# ---------------------------------------------------------------------------
#
# The LTX video decoder is non-causal 3D convolutions, per-voxel PixelNorm and
# SiLU. Its single-pass peak is a handful of full-size activations held at the
# last, largest stage (0.67 GB each at 768x448 x 121): 7.2 GB for a 5 s clip,
# 13.4 GB for 10 s. Here every residual block, and every other stride-1 conv,
# computes its output a few frames at a time, each chunk from exactly the input
# frames it depends on (a one-frame halo per conv, the engine's own replicate
# padding at the true clip edges). Same primitives on the same values: the
# decoded frames are bit-identical (sha 531250a7... at 5 s, bfed2599... at 10 s),
# at 3.4 GB and 6.3 GB, for +1.8 s and +4 s. The causal VAE encoder is not
# touched (causal convs keep the engine's path).

DECODE_CHUNK_FRAMES = 8
_DECODE_PATCH = {"on": False}


def _time_window(x, s, e, p):
    """x[:, s-p : e+p] along time, replicate-padded only at the true edges."""
    mx = _mx()
    T = x.shape[1]
    lo, hi = max(s - p, 0), min(e + p, T)
    parts = []
    if s - p < 0:
        parts.append(mx.repeat(x[:, :1], p - s, axis=1))
    parts.append(x[:, lo:hi])
    if e + p > T:
        parts.append(mx.repeat(x[:, -1:], e + p - T, axis=1))
    return mx.concatenate(parts, axis=1) if len(parts) > 1 else parts[0]


def _spatial_pad(block, x):
    """Conv3dBlock's spatial padding, exactly as the engine applies it."""
    mx = _mx()
    sp_h, sp_w = block.spatial_padding
    if sp_h > 0 or sp_w > 0:
        if block.spatial_padding_mode == "reflect":
            if sp_h > 0:
                x = mx.concatenate([x[:, :, 1:1 + sp_h], x, x[:, :, -(1 + sp_h):-1]], axis=2)
            if sp_w > 0:
                x = mx.concatenate([x[:, :, :, 1:1 + sp_w], x, x[:, :, :, -(1 + sp_w):-1]], axis=3)
        else:
            x = mx.pad(x, [(0, 0), (0, 0), (sp_h, sp_h), (sp_w, sp_w), (0, 0)])
    return x


def install_chunked_decode(chunk_frames: int = DECODE_CHUNK_FRAMES) -> bool:
    """Patch the decoder's Conv3dBlock / ResBlock3d to run in time chunks.
    Idempotent; returns False on an engine whose decoder does not have the
    shape this was proven on (the engine path is left alone)."""
    if _DECODE_PATCH["on"]:
        return True
    try:
        import mlx.nn as nn
        from ltx_core_mlx.model.video_vae import convolution as C, resnet as R
        from ltx_core_mlx.model.video_vae.normalization import pixel_norm
    except Exception:
        return False
    mx = _mx()
    N = int(chunk_frames)
    orig_conv, orig_res = C.Conv3dBlock.__call__, R.ResBlock3d.__call__
    def plain(block):
        return (not block.causal and block.stride[0] == 1 and block.kernel_size[0] % 2 == 1)

    def conv(self, x):
        T = x.shape[1]
        if T <= N or not plain(self):
            return orig_conv(self, x)
        p = (self.kernel_size[0] - 1) // 2
        outs = []
        for s in range(0, T, N):
            y = self.conv(_spatial_pad(self, _time_window(x, s, min(T, s + N), p)))
            _materialize(y)
            outs.append(y)
        return mx.concatenate(outs, axis=1)

    def res(self, x):
        T = x.shape[1]
        c1, c2 = self.conv1, self.conv2
        if T <= N or not (plain(c1) and plain(c2) and c1.kernel_size[0] == 3 and c2.kernel_size[0] == 3):
            return orig_res(self, x)
        outs = []
        for s in range(0, T, N):
            e = min(T, s + N)
            lo, hi = max(s - 1, 0), min(e + 1, T)        # the conv1 outputs conv2 needs
            h1 = c1.conv(_spatial_pad(c1, nn.silu(pixel_norm(_time_window(x, lo, hi, 1)))))
            g = nn.silu(pixel_norm(h1))
            parts = ([g[:, :1]] if s == 0 else []) + [g] + ([g[:, -1:]] if e == T else [])
            g = mx.concatenate(parts, axis=1) if len(parts) > 1 else g
            y = c2.conv(_spatial_pad(c2, g)) + x[:, s:e]
            _materialize(y)
            outs.append(y)
        return mx.concatenate(outs, axis=1)

    C.Conv3dBlock.__call__ = conv
    R.ResBlock3d.__call__ = res
    _DECODE_PATCH["on"] = True
    return True


def chunked_decode_wanted() -> bool:
    return (os.environ.get("LTX_CHUNKED_DECODE") or "1").strip().lower() not in (
        "0", "false", "no", "off")


# ---------------------------------------------------------------------------
# 2. Prompt-embedding cache
# ---------------------------------------------------------------------------


def _file_facts(path) -> tuple:
    try:
        st = os.stat(path)
        return (str(path), int(st.st_size), int(st.st_mtime_ns))
    except OSError:
        return (str(path), -1, -1)


def embedding_key(prompt: str, gemma_path, model_dir, max_length: int) -> tuple:
    """Everything the embeddings depend on. The text encoder is keyed by its
    weight files, the connector by its file, so a re-downloaded or switched
    model can never be served an old prompt's embeddings."""
    gemma = Path(str(gemma_path))
    shards = tuple(_file_facts(p) for p in sorted(gemma.glob("*.safetensors"))) if gemma.is_dir() \
        else (_file_facts(gemma),)
    return (str(prompt), str(gemma_path), shards,
            _file_facts(Path(str(model_dir)) / "connector.safetensors"), int(max_length))


class EmbeddingCache:
    """A small LRU of materialized (video, audio) prompt embeddings.

    ~12 MB per entry for LTX-2.5 at max length 1024; eight entries is under
    100 MB, against ~13 GB of disk reads and the riskiest phase on a 16 GB
    Mac skipped per hit. ``LTX_EMBED_CACHE=0`` turns it off."""

    def __init__(self, max_items: int = 8):
        self.max_items = max_items
        self._d: collections.OrderedDict = collections.OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def enabled() -> bool:
        return (os.environ.get("LTX_EMBED_CACHE") or "1").strip().lower() not in (
            "0", "false", "no", "off")

    def get(self, key):
        if not self.enabled():
            return None
        with self._lock:
            hit = self._d.get(key)
            if hit is None:
                self.misses += 1
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return hit

    def put(self, key, video, audio) -> None:
        if not self.enabled():
            return
        _materialize(video, audio)
        with self._lock:
            self._d[key] = (video, audio)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


EMBEDDINGS = EmbeddingCache()


# ---------------------------------------------------------------------------
# 3. Runtime (unfused) LoRAs on the block-streamed transformer
# ---------------------------------------------------------------------------


class _ExtraWeights:
    """Hands ``BlockStreamer.bind`` a block whose ``load_weights`` also loads
    this block's LoRA A/B. The streamer's own bind (eviction, re-mmap, its
    key map) runs unchanged; only the weight list grows."""

    __slots__ = ("_block", "_extra")

    def __init__(self, block, extra):
        self._block = block
        self._extra = extra

    def load_weights(self, weights, strict=True):
        return self._block.load_weights(list(weights) + self._extra, strict=strict)

    def __getattr__(self, name):
        return getattr(self._block, name)


class _LoraBinder:
    __slots__ = ("_inner", "_owner")

    def __init__(self, inner, owner):
        self._inner = inner
        self._owner = owner

    def bind(self, block, idx, evict_previous=None, lora_sources=None):
        extra = self._owner._block_weights(idx)
        target = _ExtraWeights(block, extra) if extra else block
        return self._inner.bind(target, idx, evict_previous=evict_previous,
                                lora_sources=lora_sources)

    def __getattr__(self, name):
        return getattr(self._inner, name)


class StreamedLoras:
    """Unfused runtime LoRAs carried through a ``StreamingLTXModel``.

    ``specs`` are ``(state_dict, strength)`` pairs whose keys have been
    through ``LTXV_LORA_COMFY_RENAMING_MAP`` (the engine's
    ``LoraStateDictWithStrength``). Per module path the A/B are combined the
    way ``runtime_loras.attach_loras`` combines them for a resident model -
    one LoRA keeps its strength as the adapter scale, several are stacked on
    the rank axis with the strengths folded into B - so the arithmetic is the
    resident path's. Blocks a LoRA does not cover bind zero A/B of the same
    rank (an exact zero delta), so the compiled shared block never retraces.

    ``set_enabled(False)`` puts the plain linears back; ``set_enabled(True)``
    wraps them again. Non-block LoRA keys are attached
    to the resident remainder of the model with the engine's own
    ``attach_loras``.
    """

    def __init__(self, model, specs, *, block_prefix: str = "transformer_blocks."):
        from ltx_core_mlx.loader import runtime_loras as rl

        mx = _mx()
        self._rl = rl
        self.model = model
        self.applied: list[str] = []          # full module names, as attach_loras reports them
        self.skipped: list[tuple[str, str]] = []
        self.enabled = False
        self._shared = object.__getattribute__(model, "_shared_block")
        streamer = object.__getattribute__(model, "_streamer")
        if isinstance(streamer, _LoraBinder):
            raise RuntimeError("a streamed LoRA set is already attached to this model")
        self._streamer = streamer
        n_blocks = int(streamer.block_count)
        # Bind a real block first. A freshly loaded streamed model's shared
        # block still holds the constructor's float32 placeholders, and the
        # engine narrows a LoRA to the layer's compute dtype (its scales'
        # dtype) - read off a placeholder that is float32, so an fp32
        # character LoRA stayed fp32 here while the resident path ran it in
        # bf16 (measured: PSNR 54.7 dB apart instead of identical). The next
        # forward rebinds whatever block it needs, so this costs one bind.
        streamer.bind(self._shared, 0)

        merged: dict[tuple[int, str], list] = {}
        rest = []
        for state, strength in specs:
            sd = state.sd if hasattr(state, "sd") else state
            non_block = {}
            for name, (a, b) in rl._split_lora_pairs(sd).items():
                if name.startswith(block_prefix):
                    idx_s, _, rel = name[len(block_prefix):].partition(".")
                    try:
                        idx = int(idx_s)
                    except ValueError:
                        self.skipped.append((name, "block index is not a number"))
                        continue
                    if not 0 <= idx < n_blocks:
                        self.skipped.append((name, f"block {idx} is not in the model"))
                        continue
                    merged.setdefault((idx, rel), []).append((a, b, float(strength)))
                else:
                    non_block[name + rl.LORA_A_SUFFIX] = a
                    non_block[name + rl.LORA_B_SUFFIX] = b
            if non_block:
                rest.append((non_block, float(strength)))

        rels = sorted({rel for _i, rel in merged})
        self._eager = False
        self._scales: dict[int, dict[str, float]] = {}
        self._bases: dict[str, object] = {}
        self._adapters: dict[str, object] = {}
        self._where: dict[str, tuple] = {}
        self._per_block: dict[int, list] = {}
        for rel in rels:
            resolved = rl._resolve_module(self._shared, rel)
            if resolved is None:
                for (i, r) in merged:
                    if r == rel:
                        self.skipped.append((f"{block_prefix}{i}.{rel}", "no matching module in the model tree"))
                continue
            container, key, base = resolved
            import mlx.nn as nn
            if not isinstance(base, (nn.Linear, nn.QuantizedLinear)):
                for (i, r) in merged:
                    if r == rel:
                        self.skipped.append((f"{block_prefix}{i}.{rel}",
                                             f"target is {type(base).__name__}, not a linear layer"))
                continue
            in_f = rl._logical_in_features(base)
            out_f = int(base["weight"].shape[0])
            dtype = rl._compute_dtype(base)

            combined: dict[int, tuple] = {}
            for i in range(n_blocks):
                deltas = merged.get((i, rel))
                if not deltas:
                    continue
                if any(int(a.shape[-1]) != in_f or int(b.shape[0]) != out_f for a, b, _ in deltas):
                    self.skipped.append((f"{block_prefix}{i}.{rel}", "shape mismatch"))
                    continue
                if len(deltas) == 1:
                    a, b, s = deltas[0]
                else:
                    dt = deltas[0][0].dtype
                    a = mx.concatenate([d[0].astype(dt) for d in deltas], axis=0)
                    b = mx.concatenate([d[1].astype(dt) * d[2] for d in deltas], axis=1)
                    s = 1.0
                a, b = rl._match_dtype(a, b, base)
                combined[i] = (a, b, s)
            if not combined:
                continue
            scales = {s for _a, _b, s in combined.values()}
            ranks = {int(a.shape[0]) for a, _b, _s in combined.values()}
            if len(scales) > 1 or len(ranks) > 1:
                # Blocks disagree (rare: two LoRAs covering different blocks
                # stack on some and not others). A compiled block bakes the
                # adapter scale in at trace time, so this set runs the shared
                # block uncompiled and sets each block's own scale as it binds
                # - the resident path's arithmetic, block by block.
                self._eager = True
            scale = next(iter(scales))
            rank = max(ranks)
            a_dt = next(iter(combined.values()))[0].dtype
            b_dt = next(iter(combined.values()))[1].dtype
            zero_a = mx.zeros((rank, in_f), dtype=a_dt)
            zero_b = mx.zeros((out_f, rank), dtype=b_dt)
            for i in range(n_blocks):
                self._scales.setdefault(i, {})[rel] = combined[i][2] if i in combined else 0.0
                if i in combined:
                    a, b, _s = combined[i]
                    r = int(a.shape[0])
                    if r < rank:
                        a = mx.concatenate([a, mx.zeros((rank - r, in_f), dtype=a.dtype)], axis=0)
                        b = mx.concatenate([b, mx.zeros((out_f, rank - r), dtype=b.dtype)], axis=1)
                    self.applied.append(f"{block_prefix}{i}.{rel}")
                else:
                    a, b = zero_a, zero_b
                self._per_block.setdefault(i, []).extend(
                    [(f"{rel}.lora_a", a), (f"{rel}.lora_b", b)])
            self._bases[rel] = base
            self._where[rel] = (container, key)
            first = combined[min(combined)]
            self._adapters[rel] = rl._wrap(base, first[0], first[1], scale)

        self.rest_report = None
        if rest:
            from ltx_core_mlx.loader.primitives import LoraStateDictWithStrength, StateDict
            pairs = [LoraStateDictWithStrength(StateDict(sd=sd, size=0, dtype=set()), s)
                     for sd, s in rest]
            self.rest_report = rl.attach_loras(model.inner, pairs, verbose=False)
            self.applied.extend(m.name for m in self.rest_report.applied)
            self.skipped.extend((m.name, m.reason) for m in self.rest_report.skipped)

        object.__setattr__(model, "_streamer", _LoraBinder(streamer, self))
        self.set_enabled(True)

    @property
    def wrapped_modules(self) -> int:
        return len(self._adapters)

    def _block_weights(self, idx: int) -> list:
        if not self.enabled:
            return []
        if self._eager:
            for rel, s in self._scales.get(idx, {}).items():
                adapter = self._adapters.get(rel)
                if adapter is not None:
                    adapter.lora_scale = float(s)
        return self._per_block.get(idx, [])

    def set_enabled(self, on: bool) -> None:
        on = bool(on) and bool(self._adapters)
        if on == self.enabled:
            return
        mx = _mx()
        for rel, (container, key) in self._where.items():
            module = self._adapters[rel] if on else self._bases[rel]
            if isinstance(container, list):
                container[key] = module
            else:
                setattr(container, key, module)
        self.enabled = on
        # The compiled block traced the module tree it was given: recompile
        # for the new one (inputs=shared keeps per-bind weights as inputs).
        # A set whose blocks disagree runs the block uncompiled (see above).
        eager = on and (self._eager or bool(os.environ.get("LTX_LOWRAM_EAGER")))
        block = self._shared if eager else mx.compile(self._shared, inputs=self._shared)
        object.__setattr__(self.model, "_compiled_block", block)

    def detach(self) -> None:
        """Back to the plain streamed model (the binder comes off too)."""
        self.set_enabled(False)
        object.__setattr__(self.model, "_streamer", self._streamer)
        # Every LoRA array goes with it: the per-block A/B and the adapters'
        # own references (block 0's), so the decode that follows a two-stage
        # render never carries the add-on's memory.
        self._per_block = {}
        self._adapters = {}
        self._bases = {}
        self._where = {}


def load_lora_specs(lora_paths):
    """``[(path, strength)]`` -> engine ``LoraStateDictWithStrength`` list,
    with each file's declared alpha/dim applied exactly as
    ``runtime_loras.load_and_attach_loras`` applies it."""
    from ltx_core_mlx.loader import runtime_loras as rl
    from ltx_core_mlx.loader.primitives import LoraStateDictWithStrength
    from ltx_core_mlx.loader.sd_ops import LTXV_LORA_COMFY_RENAMING_MAP
    from ltx_core_mlx.loader.sft_loader import SafetensorsStateDictLoader

    loader = SafetensorsStateDictLoader()
    specs = []
    for path, strength in lora_paths:
        declared, _why = rl.declared_lora_scale(path)
        specs.append(LoraStateDictWithStrength(
            state_dict=loader.load(str(Path(path)), sd_ops=LTXV_LORA_COMFY_RENAMING_MAP),
            strength=float(strength) * declared))
    return specs


def is_streaming_model(model) -> bool:
    try:
        object.__getattribute__(model, "_shared_block")
        object.__getattribute__(model, "_streamer")
        return True
    except AttributeError:
        return False


if __name__ == "__main__":  # pragma: no cover
    print(__doc__, file=sys.stderr)
