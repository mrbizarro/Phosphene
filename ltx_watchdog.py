"""Bound the largest prompt projection on M2 Pro/Max without shortening prompts.

Per-layer evaluation and one-op command buffers cannot split a single large
matrix product. The connector projects 188160 input channels for every token.
Split independent token rows, never the reduction axis or attention context.
The fleet identifies encoding, not this individual operation: this mitigation
still needs validation on affected hardware after the local equality gate.
"""
from __future__ import annotations

from functools import lru_cache
import math
import os
import sys

import hostinfo

TOKEN_CHUNK = 128
TOKEN_CHUNK_SHORT = 64


@lru_cache(maxsize=1)
def _affected_host() -> bool:
    return hostinfo.IS_MAC and hostinfo.chip_brand() in ("Apple M2 Pro", "Apple M2 Max")


def _low_ram_stream() -> bool:
    return (os.environ.get("LTX_LOW_RAM_STREAM") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _short_gpu_steps() -> bool:
    return (os.environ.get("MLX_MAX_OPS_PER_BUFFER") or "").strip() == "1"


def _chunk_size() -> int:
    return TOKEN_CHUNK_SHORT if _short_gpu_steps() else TOKEN_CHUNK


def enabled() -> bool:
    # Test hook: exercise the real small-chunk path on the lab's M4, or compare
    # against the unchanged path. No launcher or user setting sets this.
    force = os.environ.get("PHOSPHENE_TEST_LTX_WATCHDOG")
    if force in ("0", "1"):
        return force == "1"
    if not hostinfo.IS_MAC:
        return False
    return _affected_host() or _low_ram_stream() or _short_gpu_steps()


def project(linear, inputs):
    """Same linear layer, bounded token rows; unaffected hosts call it directly."""
    if not enabled():
        return linear(inputs)
    import mlx.core as mx

    # Finish upstream work and lazy file reads before queuing the products.
    mx.eval(inputs, linear.parameters())
    length = inputs.shape[-2]
    if length == 0:
        return linear(inputs)
    chunk = _chunk_size()
    parts = []
    for start in range(0, length, chunk):
        part = linear(inputs[..., start:start + chunk, :])
        mx.eval(part)
        parts.append(part)
    return parts[0] if len(parts) == 1 else mx.concatenate(parts, axis=-2)


def install() -> None:
    """Install once in the render helper; the low-RAM path calls project too."""
    if not enabled():
        return
    from ltx_core_mlx.text_encoders.gemma.feature_extractor import TextEmbeddingProjection

    if getattr(TextEmbeddingProjection, "_phosphene_watchdog", False):
        return
    original = TextEmbeddingProjection.__call__

    def bounded(self, hidden_states):
        if not enabled():
            return original(self, hidden_states)
        outputs = []
        for linear in (self.video_aggregate_embed, self.audio_aggregate_embed):
            scale = math.sqrt(linear.weight.shape[0] / self.embedding_dim)
            outputs.append(project(linear, hidden_states * scale))
        return tuple(outputs)

    TextEmbeddingProjection.__call__ = bounded
    TextEmbeddingProjection._phosphene_watchdog = True
    print("[text-connector] prompt projection uses smaller token groups",
          file=sys.stderr, flush=True)
