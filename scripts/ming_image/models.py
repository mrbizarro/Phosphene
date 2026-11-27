"""Pure-MLX Ming-Image-0.2-Design model definitions (Phosphene implementation).

Includes:
- Mersenne Twister Box-Muller normal RNG matching PyTorch CPU seed semantics.
- Static-shift FlowMatch Euler scheduler (shift=6.0, sigma_min=0.0, 12 steps).
- BailingMoeV2 text encoder (MultiRouter + 8-bit SwitchGLU + 3D video RoPE)
  with resident and single-layer disk-streaming execution modes.
- Bidirectional Qwen2 conditioner + directvlm multi-layer projection.
- 6B single-stream Ming DiT with 3-axis RoPE, zero-masked padding elimination,
  fused Metal QK-RMSNorm+RoPE kernel, and resident / block-streamed execution.
- 2D-collapsed Qwen-Image RGBA VAE decoder.
"""
from __future__ import annotations

import gc
import math
import os
from pathlib import Path
from typing import Callable

import mlx.core as mx
import mlx.nn as nn
import numpy as np

from .weights import ShardStore

# ---------------------------------------------------------------------------
# Seeded Gaussian noise (MT19937 Box-Muller matching PyTorch CPU generator)
# ---------------------------------------------------------------------------

_TWO_PI = 2.0 * math.pi
_FLT_EPS = float(np.finfo(np.float32).eps)


def seeded_randn(shape: tuple[int, ...], seed: int) -> np.ndarray:
    """Generate standard-normal float32 noise matching ``torch.manual_seed(seed)``."""
    n = int(math.prod(shape))
    # PyTorch MT19937 uses the low 32 bits of the seed.
    s = int(seed) & 0xFFFFFFFF
    mt = [0] * 624
    mt[0] = s
    for i in range(1, 624):
        mt[i] = (1812433253 * (mt[i - 1] ^ (mt[i - 1] >> 30)) + i) & 0xFFFFFFFF
    idx = 624

    def next_u32() -> int:
        nonlocal idx
        if idx >= 624:
            for k in range(624):
                y = (mt[k] & 0x80000000) | (mt[(k + 1) % 624] & 0x7FFFFFFF)
                mt[k] = mt[(k + 397) % 624] ^ (y >> 1) ^ (0x9908B0DF if (y & 1) else 0)
            idx = 0
        y = mt[idx]
        idx += 1
        y ^= y >> 11
        y ^= (y << 7) & 0x9D2C5680
        y ^= (y << 15) & 0xEFC60000
        y ^= y >> 18
        return y

    # PyTorch draws pairs of uniform floats (1 - u_raw) -> Box-Muller.
    count = n + (n & 1)
    u = np.empty(count, dtype=np.float32)
    scale = np.float32(1.0 / (1 << 24))
    for i in range(count):
        # 24-bit mantissa uniform in (0, 1] via 1.0 - (r & 0xffffff) / 2^24
        u[i] = np.float32(1.0) - np.float32(next_u32() & 0xFFFFFF) * scale

    u1 = np.maximum(u[0::2], _FLT_EPS)
    u2 = u[1::2]
    r = np.sqrt(-2.0 * np.log(u1.astype(np.float64))).astype(np.float32)
    theta = (_TWO_PI * u2.astype(np.float64)).astype(np.float32)
    out = np.empty(count, dtype=np.float32)
    out[0::2] = r * np.cos(theta)
    out[1::2] = r * np.sin(theta)
    return out[:n].reshape(shape)


# ---------------------------------------------------------------------------
# Flow-match Euler schedule (static shift=6.0, sigma_min=0.0)
# ---------------------------------------------------------------------------

class FlowMatchSchedule:
    """FlowMatchEuler schedule matching upstream Ming-Image-0.2-Design."""

    def __init__(self, cfg: dict) -> None:
        self.num_train_timesteps = int(cfg.get("num_train_timesteps", 1000))
        self.shift = float(cfg.get("shift", 6.0))
        grid = np.linspace(1.0, float(self.num_train_timesteps), self.num_train_timesteps, dtype=np.float32)[::-1]
        base = grid / np.float32(self.num_train_timesteps)
        shifted = (self.shift * base / (1.0 + (self.shift - 1.0) * base)).astype(np.float32)
        self.sigma_max = float(shifted[0])
        self.sigma_min = 0.0

    def build(self, steps: int) -> tuple[np.ndarray, np.ndarray]:
        n = float(self.num_train_timesteps)
        t_lin = np.linspace(self.sigma_max * n, self.sigma_min * n, int(steps), dtype=np.float64)
        s = t_lin / n
        s = (self.shift * s / (1.0 + (self.shift - 1.0) * s)).astype(np.float32)
        timesteps = (s * np.float32(self.num_train_timesteps)).astype(np.float32)
        sigmas = np.concatenate([s, np.zeros(1, dtype=np.float32)])
        return timesteps, sigmas


# ---------------------------------------------------------------------------
# Fused Metal kernel: per-head RMSNorm (fp32 stats) + interleaved 2D RoPE
# ---------------------------------------------------------------------------

_FUSED_QK_SRC = """
    uint gid = thread_position_in_grid.x;
    uint pair = thread_position_in_threadgroup.x;
    uint row = gid / 64;
    uint head = row % H;
    uint seq = (row / H) % L;
    uint batch = row / (H * L);
    float a = static_cast<float>(x[row * 128 + 2 * pair]);
    float b = static_cast<float>(x[row * 128 + 2 * pair + 1]);
    float sum_sq = simd_sum(a * a + b * b);
    threadgroup float buf[2];
    if (thread_index_in_simdgroup == 0) {
        buf[simdgroup_index_in_threadgroup] = sum_sq;
    }
    threadgroup_barrier(mem_flags::mem_threadgroup);
    float inv_rms = metal::rsqrt((buf[0] + buf[1]) / 128.0f + eps[0]);
    bfloat16_t na = static_cast<bfloat16_t>(a * inv_rms) * w[2 * pair];
    bfloat16_t nb = static_cast<bfloat16_t>(b * inv_rms) * w[2 * pair + 1];
    float fa = static_cast<float>(na);
    float fb = static_cast<float>(nb);
    float c = cosv[seq * 64 + pair];
    float s = sinv[seq * 64 + pair];
    uint dst = ((batch * H + head) * L + seq) * 128 + 2 * pair;
    out[dst] = static_cast<bfloat16_t>(fa * c - fb * s);
    out[dst + 1] = static_cast<bfloat16_t>(fa * s + fb * c);
"""

_FUSED_QK_KERNEL = None


def _get_fused_qk_kernel():
    global _FUSED_QK_KERNEL
    if _FUSED_QK_KERNEL is None:
        _FUSED_QK_KERNEL = mx.fast.metal_kernel(
            name="phosphene_ming_qk_norm_rope",
            input_names=["x", "w", "cosv", "sinv", "eps"],
            output_names=["out"],
            source=_FUSED_QK_SRC,
        )
    return _FUSED_QK_KERNEL


def _qk_norm_rope(x: mx.array, weight: mx.array, cos: mx.array, sin: mx.array, eps: float, fused: bool) -> mx.array:
    """Normalize [B, L, H, D] per head and apply interleaved complex RoPE -> [B, H, L, D]."""
    B, L, H, D = x.shape
    if fused and D == 128 and x.dtype == mx.bfloat16 and weight.dtype == mx.bfloat16:
        kern = _get_fused_qk_kernel()
        return kern(
            inputs=[x, weight, cos, sin, mx.array([eps], mx.float32)],
            template=[("H", H), ("L", L)],
            grid=(B * L * H * 64, 1, 1),
            threadgroup=(64, 1, 1),
            output_shapes=[(B, H, L, D)],
            output_dtypes=[mx.bfloat16],
        )[0]
    normed = mx.fast.rms_norm(x, weight, eps).transpose(0, 2, 1, 3)
    dt = normed.dtype
    xf = normed.astype(mx.float32).reshape(B, H, L, D // 2, 2)
    xr, xi = xf[..., 0], xf[..., 1]
    c, s = cos[None, None], sin[None, None]
    rot = mx.stack([xr * c - xi * s, xr * s + xi * c], axis=-1)
    return rot.reshape(B, H, L, D).astype(dt)


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------

class FastRMSNorm(nn.Module):
    def __init__(self, dim: int, eps: float) -> None:
        super().__init__()
        self.weight = mx.ones((dim,))
        self.eps = float(eps)

    def __call__(self, x: mx.array) -> mx.array:
        return mx.fast.rms_norm(x, self.weight, self.eps)


class FastLayerNorm(nn.Module):
    def __init__(self, dim: int, eps: float = 1e-5, affine: bool = True) -> None:
        super().__init__()
        self.eps = float(eps)
        if affine:
            self.weight = mx.ones((dim,))
            self.bias = mx.zeros((dim,))

    def __call__(self, x: mx.array) -> mx.array:
        w = self.weight if "weight" in self else None
        b = self.bias if "bias" in self else None
        return mx.fast.layer_norm(x, w, b, self.eps)


class ExpertLinear(nn.Module):
    """Unquantized [E, out, in] routed expert linear layer."""

    def __init__(self, in_dim: int, out_dim: int, num_experts: int) -> None:
        super().__init__()
        self.weight = mx.zeros((num_experts, out_dim, in_dim))

    def __call__(self, x: mx.array, idx: mx.array, sorted_indices: bool = False) -> mx.array:
        return mx.gather_mm(
            x,
            self.weight.swapaxes(-1, -2),
            rhs_indices=idx,
            sorted_indices=sorted_indices,
        )

    def to_quantized(self, group_size: int = 64, bits: int = 8, mode: str = "affine") -> "QuantizedExpertLinear":
        q = QuantizedExpertLinear.__new__(QuantizedExpertLinear)
        nn.Module.__init__(q)
        q.group_size = group_size
        q.bits = bits
        q.weight, q.scales, q.biases = mx.quantize(self.weight, group_size, bits)
        return q


class QuantizedExpertLinear(nn.Module):
    """Quantized affine [E, out, in] routed expert linear layer via mx.gather_qmm."""

    def __init__(self, in_dim: int, out_dim: int, num_experts: int, group_size: int = 64, bits: int = 8) -> None:
        super().__init__()
        self.group_size = group_size
        self.bits = bits
        dummy = mx.zeros((num_experts, out_dim, in_dim))
        self.weight, self.scales, self.biases = mx.quantize(dummy, group_size, bits)

    def __call__(self, x: mx.array, idx: mx.array, sorted_indices: bool = False) -> mx.array:
        return mx.gather_qmm(
            x,
            self.weight,
            self.scales,
            self.biases,
            rhs_indices=idx,
            transpose=True,
            group_size=self.group_size,
            bits=self.bits,
            sorted_indices=sorted_indices,
        )


class ExpertSwiGLU(nn.Module):
    def __init__(self, dim: int, hidden: int, num_experts: int) -> None:
        super().__init__()
        self.gate_proj = ExpertLinear(dim, hidden, num_experts)
        self.up_proj = ExpertLinear(dim, hidden, num_experts)
        self.down_proj = ExpertLinear(hidden, dim, num_experts)

    def __call__(self, x: mx.array, indices: mx.array) -> mx.array:
        n_tok, k = indices.shape
        flat = indices.flatten()
        order = mx.argsort(flat)
        inv = mx.argsort(order)
        xs = mx.expand_dims(x, -2)[order // k]
        s_idx = flat[order]
        h = nn.silu(self.gate_proj(xs, s_idx, True)) * self.up_proj(xs, s_idx, True)
        y = self.down_proj(h, s_idx, True)
        return y[inv].reshape(n_tok, k, -1)


# ---------------------------------------------------------------------------
# BailingMoeV2 text encoder (resident or single-layer disk-streamed)
# ---------------------------------------------------------------------------

class DenseSwiGLU(nn.Module):
    def __init__(self, dim: int, hidden: int) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(dim, hidden, bias=False)
        self.up_proj = nn.Linear(dim, hidden, bias=False)
        self.down_proj = nn.Linear(hidden, dim, bias=False)

    def __call__(self, x: mx.array) -> mx.array:
        return self.down_proj(nn.silu(self.gate_proj(x)) * self.up_proj(x))


class MoERouter(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.weight = mx.zeros((cfg["num_experts"], cfg["hidden_size"]))
        self.expert_bias = mx.zeros((cfg["num_experts"],))
        self.top_k = int(cfg["num_experts_per_tok"])
        self.n_group = int(cfg["n_group"])
        self.topk_group = int(cfg["topk_group"])
        self.scale = float(cfg["routed_scaling_factor"])

    def __call__(self, x: mx.array) -> tuple[mx.array, mx.array]:
        logits = x.astype(mx.float32) @ self.weight.astype(mx.float32).T
        scores = mx.sigmoid(logits)
        biased = scores + self.expert_bias.astype(mx.float32)
        n_tok, n_exp = biased.shape
        per_grp = n_exp // self.n_group
        grp_scores = mx.sum(mx.topk(biased.reshape(n_tok, self.n_group, per_grp), 2, axis=-1), axis=-1)
        drop_cnt = self.n_group - self.topk_group
        drop_idx = mx.argpartition(grp_scores, kth=drop_cnt - 1, axis=-1)[..., :drop_cnt]
        keep = mx.ones_like(grp_scores)
        keep = mx.put_along_axis(keep, drop_idx, mx.zeros(drop_idx.shape, dtype=keep.dtype), axis=-1)
        masked = mx.where(mx.repeat(keep, per_grp, axis=-1) > 0, biased, -mx.inf)
        top_idx = mx.argpartition(-masked, kth=self.top_k - 1, axis=-1)[..., : self.top_k]
        w = mx.take_along_axis(scores, top_idx, axis=-1)
        w = (w / (mx.sum(w, axis=-1, keepdims=True) + 1e-20)) * self.scale
        return top_idx.astype(mx.uint32), w


class MultiRouterMoE(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        dim = int(cfg["hidden_size"])
        inter = int(cfg["moe_intermediate_size"])
        self.gate = MoERouter(cfg)
        self.image_gate = MoERouter(cfg)
        self.switch_mlp = ExpertSwiGLU(dim, inter, int(cfg["num_experts"]))
        self.shared_experts = DenseSwiGLU(dim, inter * int(cfg["num_shared_experts"]))

    def __call__(self, x: mx.array, img_mask: mx.array | None) -> mx.array:
        b, seq, dim = x.shape
        flat = x.reshape(-1, dim)
        idx, w = self.gate(flat)
        if img_mask is not None:
            i_idx, i_w = self.image_gate(flat)
            m = img_mask.reshape(-1, 1)
            idx = mx.where(m, i_idx, idx)
            w = mx.where(m, i_w, w)
        routed = self.switch_mlp(flat, idx)
        combined = (routed.astype(mx.float32) * w[..., None]).sum(axis=1).astype(x.dtype)
        return combined.reshape(b, seq, dim) + self.shared_experts(x)


class BailingAttention(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        dim = int(cfg["hidden_size"])
        self.nh = int(cfg["num_attention_heads"])
        self.nkv = int(cfg["num_key_value_heads"])
        self.hd = int(cfg.get("head_dim") or (dim // self.nh))
        self.query_key_value = nn.Linear(dim, (self.nh + 2 * self.nkv) * self.hd, bias=bool(cfg.get("use_qkv_bias", False)))
        self.dense = nn.Linear(self.nh * self.hd, dim, bias=bool(cfg.get("use_bias", False)))
        self.q_norm = FastRMSNorm(self.hd, cfg["rms_norm_eps"])
        self.k_norm = FastRMSNorm(self.hd, cfg["rms_norm_eps"])

    @staticmethod
    def _rotate_half(t: mx.array) -> mx.array:
        mid = t.shape[-1] // 2
        return mx.concatenate([-t[..., mid:], t[..., :mid]], axis=-1)

    def __call__(self, x: mx.array, cos: mx.array, sin: mx.array) -> mx.array:
        b, seq, _ = x.shape
        qkv = self.query_key_value(x).reshape(b, seq, self.nh + 2 * self.nkv, self.hd)
        q, k, v = mx.split(qkv, [self.nh, self.nh + self.nkv], axis=2)
        q = self.q_norm(q).transpose(0, 2, 1, 3)
        k = self.k_norm(k).transpose(0, 2, 1, 3)
        v = v.transpose(0, 2, 1, 3)
        rd = cos.shape[-1]
        c, s = cos[:, None], sin[:, None]
        q = mx.concatenate([q[..., :rd] * c + self._rotate_half(q[..., :rd]) * s, q[..., rd:]], axis=-1)
        k = mx.concatenate([k[..., :rd] * c + self._rotate_half(k[..., :rd]) * s, k[..., rd:]], axis=-1)
        attn = mx.fast.scaled_dot_product_attention(q, k, v, scale=self.hd ** -0.5, mask="causal")
        return self.dense(attn.transpose(0, 2, 1, 3).reshape(b, seq, -1))


class BailingBlock(nn.Module):
    def __init__(self, cfg: dict, layer_idx: int) -> None:
        super().__init__()
        self.attention = BailingAttention(cfg)
        self.is_moe = layer_idx >= int(cfg["first_k_dense_replace"])
        self.mlp = MultiRouterMoE(cfg) if self.is_moe else DenseSwiGLU(int(cfg["hidden_size"]), int(cfg["intermediate_size"]))
        self.input_layernorm = FastRMSNorm(int(cfg["hidden_size"]), cfg["rms_norm_eps"])
        self.post_attention_layernorm = FastRMSNorm(int(cfg["hidden_size"]), cfg["rms_norm_eps"])

    def __call__(self, x: mx.array, cos: mx.array, sin: mx.array, img_mask: mx.array | None) -> mx.array:
        x = x + self.attention(self.input_layernorm(x), cos, sin)
        h = self.post_attention_layernorm(x)
        h = self.mlp(h, img_mask) if self.is_moe else self.mlp(h)
        return x + h


def _quantize_llm_module(module: nn.Module, bits: int, group_size: int) -> None:
    def pred(path: str, m: nn.Module) -> bool:
        if not hasattr(m, "to_quantized"):
            return False
        leaf = path.split(".")[-1]
        if leaf in ("query_key_value", "dense", "gate_proj", "up_proj", "down_proj"):
            return True
        return False

    nn.quantize(module, group_size=group_size, bits=bits, class_predicate=pred)


def build_video_rope(pos3: np.ndarray, cfg: dict, dtype: mx.Dtype) -> tuple[mx.array, mx.array]:
    hd = int(cfg.get("head_dim") or (int(cfg["hidden_size"]) // int(cfg["num_attention_heads"])))
    rd = int(hd * float(cfg.get("partial_rotary_factor", 1.0)))
    theta = float(cfg["rope_theta"])
    inv = 1.0 / (theta ** (np.arange(0, rd, 2, dtype=np.int64).astype(np.float32) / rd))
    half = rd // 2
    # mrope_section [8, 12, 12] -> first 24 alternate h(1)/w(2), last 8 use t(0)
    hw_slots = int(half * 3 // 4)
    axis = np.array([(1 if j % 2 == 0 else 2) if j < hw_slots else 0 for j in range(half)], dtype=np.int64)
    freqs = pos3.astype(np.float32)[:, :, None] * inv[None, None, :]
    selected = freqs[axis, :, np.arange(half)].T
    angles = np.concatenate([selected, selected], axis=-1)
    return mx.array(np.cos(angles))[None].astype(dtype), mx.array(np.sin(angles))[None].astype(dtype)


def build_multiscale_positions(input_ids: np.ndarray, cfg: dict, grids: list[tuple[int, int, int]]) -> np.ndarray:
    merge = int(cfg["spatial_merge_size"])
    patch_tok = int(cfg["image_patch_token"])
    start_tok = int(cfg["image_start_token"])
    ids = input_ids.tolist()
    n_img = sum(1 for i, tok in enumerate(ids) if tok == start_tok and i + 1 < len(ids) and ids[i + 1] == patch_tok)
    chunks: list[np.ndarray] = []
    pos = 0
    for g_idx in range(n_img):
        ed = ids.index(patch_tok, pos)
        gt, gh, gw = int(grids[g_idx][0]), int(grids[g_idx][1]) // merge, int(grids[g_idx][2]) // merge
        txt_len = ed - pos
        base_idx = int(chunks[-1][0].max()) + 1 if chunks else 0
        chunks.append(np.broadcast_to(np.arange(txt_len) + base_idx, (3, txt_len)))
        ti = np.repeat(np.arange(gt), gh * gw)
        hi = np.tile(np.repeat(np.arange(gh), gw), gt) - (gh - 1) // 2
        wi = np.tile(np.arange(gw), gt * gh) - (gw - 1) // 2
        ti = ti * 2.0 + txt_len + base_idx
        chunks.append(np.stack([ti, hi + ti, wi + ti]))
        pos = ed + gt * gh * gw
    if pos < len(ids):
        base_idx = int(chunks[-1][0].max()) + 1 if chunks else 0
        rem = len(ids) - pos
        chunks.append(np.broadcast_to(np.arange(rem) + base_idx, (3, rem)))
    return np.concatenate(chunks, axis=1).astype(np.int64)


class BailingTextEncoder(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.cfg = cfg
        self.word_embeddings = nn.Embedding(int(cfg["vocab_size"]), int(cfg["hidden_size"]))
        self.layers = [BailingBlock(cfg, i) for i in range(int(cfg["num_hidden_layers"]))]
        self.norm = FastRMSNorm(int(cfg["hidden_size"]), cfg["rms_norm_eps"])

    def __call__(
        self,
        embeds: mx.array,
        pos3: np.ndarray,
        img_mask: mx.array,
        keep_layers: set[int],
    ) -> dict[int, mx.array]:
        cos, sin = build_video_rope(pos3, self.cfg, embeds.dtype)
        saved: dict[int, mx.array] = {}
        h = embeds
        n = len(self.layers)
        for i, layer in enumerate(self.layers):
            if i in keep_layers:
                saved[i] = h
            h = layer(h, cos, sin, img_mask)
        if n in keep_layers:
            saved[n] = self.norm(h)
        return saved


def run_llm_streamed(
    cfg: dict,
    q_cfg: dict | None,
    store: ShardStore,
    full_ids: np.ndarray,
    pos3: np.ndarray,
    patch_rows: np.ndarray,
    query_tokens: mx.array,
    keep_layers: set[int],
) -> dict[int, mx.array]:
    """Stream BailingMoeV2 one layer at a time from disk (bit-identical to resident)."""
    base_w = store.load_non_layer_keys("llm")
    vocab = int(cfg["vocab_size"])
    dim = int(cfg["hidden_size"])

    emb_mod = nn.Embedding(vocab, dim)
    emb_mod.load_weights([("weight", base_w["word_embeddings.weight"])], strict=True)
    h = emb_mod(mx.array(np.clip(full_ids, 0, vocab - 1))[None])
    q_cast = query_tokens.astype(h.dtype)
    rows = mx.array(patch_rows)
    h = h.at[0, rows].add(q_cast - h[0, rows])
    mx.eval(h)
    del emb_mod

    norm_w = base_w["norm.weight"]
    del base_w

    cos, sin = build_video_rope(pos3, cfg, h.dtype)
    img_mask = mx.array(full_ids == int(cfg["image_patch_token"]))[None, :, None]
    n_layers = int(cfg["num_hidden_layers"])
    saved: dict[int, mx.array] = {}

    for i in range(n_layers):
        if i in keep_layers:
            saved[i] = h
        block = BailingBlock(cfg, i)
        if q_cfg:
            _quantize_llm_module(block, int(q_cfg["bits"]), int(q_cfg["group_size"]))
        prefix = f"layers.{i}."
        lw = store.load_keys_matching("llm", prefix, strip_prefix=prefix)
        block.load_weights(list(lw.items()), strict=True)
        h = block(h, cos, sin, img_mask)
        mx.eval(h)
        del lw, block
        mx.clear_cache()

    if n_layers in keep_layers:
        norm = FastRMSNorm(dim, cfg["rms_norm_eps"])
        norm.load_weights([("weight", norm_w)], strict=True)
        saved[n_layers] = norm(h)
        mx.eval(saved[n_layers])
    return saved


# ---------------------------------------------------------------------------
# Qwen2 bidirectional conditioner + directvlm projection
# ---------------------------------------------------------------------------

class BidirQwen2Attention(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        dim = int(cfg["hidden_size"])
        self.nh = int(cfg["num_attention_heads"])
        self.nkv = int(cfg["num_key_value_heads"])
        self.hd = dim // self.nh
        self.q_proj = nn.Linear(dim, self.nh * self.hd, bias=True)
        self.k_proj = nn.Linear(dim, self.nkv * self.hd, bias=True)
        self.v_proj = nn.Linear(dim, self.nkv * self.hd, bias=True)
        self.o_proj = nn.Linear(self.nh * self.hd, dim, bias=False)
        self.rope = nn.RoPE(self.hd, traditional=False, base=float(cfg["rope_theta"]))

    def __call__(self, x: mx.array) -> mx.array:
        b, seq, _ = x.shape
        q = self.rope(self.q_proj(x).reshape(b, seq, self.nh, self.hd).transpose(0, 2, 1, 3))
        k = self.rope(self.k_proj(x).reshape(b, seq, self.nkv, self.hd).transpose(0, 2, 1, 3))
        v = self.v_proj(x).reshape(b, seq, self.nkv, self.hd).transpose(0, 2, 1, 3)
        o = mx.fast.scaled_dot_product_attention(q, k, v, scale=self.hd ** -0.5, mask=None)
        return self.o_proj(o.transpose(0, 2, 1, 3).reshape(b, seq, -1))


class BidirQwen2Layer(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        dim = int(cfg["hidden_size"])
        inter = int(cfg["intermediate_size"])
        self.self_attn = BidirQwen2Attention(cfg)
        self.mlp = DenseSwiGLU(dim, inter)
        self.input_layernorm = nn.RMSNorm(dim, eps=float(cfg["rms_norm_eps"]))
        self.post_attention_layernorm = nn.RMSNorm(dim, eps=float(cfg["rms_norm_eps"]))

    def __call__(self, x: mx.array) -> mx.array:
        x = x + self.self_attn(self.input_layernorm(x))
        return x + self.mlp(self.post_attention_layernorm(x))


class BidirQwen2Stack(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.layers = [BidirQwen2Layer(cfg) for _ in range(int(cfg["num_hidden_layers"]))]
        self.norm = nn.RMSNorm(int(cfg["hidden_size"]), eps=float(cfg["rms_norm_eps"]))

    def __call__(self, x: mx.array) -> mx.array:
        for layer in self.layers:
            x = layer(x)
        return self.norm(x)


class Qwen2Conditioner(nn.Module):
    def __init__(self, llm_dim: int, conn_cfg: dict, mlp_cfg: dict) -> None:
        super().__init__()
        self.scales = list(mlp_cfg.get("img_gen_scales", [16]))
        cdim = int(conn_cfg["hidden_size"])
        out_dim = int(mlp_cfg.get("diffusion_c_input_dim", 2560))
        self.query_tokens = {f"{s}x{s}": mx.zeros((s * s, llm_dim)) for s in self.scales}
        self.proj_in = nn.Linear(llm_dim, cdim)
        self.connector = BidirQwen2Stack(conn_cfg)
        self.proj_out = nn.Linear(cdim, out_dim)
        self.connector_norm = bool(mlp_cfg.get("connector_norm", False))
        self.use_directvlm = bool(mlp_cfg.get("use_vlm_directvlm_condition", False))
        self.selected = list(mlp_cfg.get("selected_hidden_states_layers") or [])
        if self.use_directvlm:
            n_sel = len(self.selected) if self.selected else 1
            self.proj_directvlm = [
                nn.RMSNorm(llm_dim * n_sel, eps=1e-5),
                nn.Linear(llm_dim * n_sel, int(mlp_cfg["diffusion_inner_dim"])),
            ]

    def stacked_queries(self) -> mx.array:
        return mx.concatenate([self.query_tokens[f"{s}x{s}"] for s in self.scales], axis=0)

    def required_layers(self, n_layers: int) -> set[int]:
        ks = {n_layers}
        if self.use_directvlm and self.selected:
            ks.update(int(i) for i in self.selected)
        return ks

    def __call__(
        self,
        hidden: dict[int, mx.array],
        n_layers: int,
        gen_rows: mx.array,
        prompt_len: int,
    ) -> tuple[mx.array, mx.array | None]:
        q_feats = hidden[n_layers][:, gen_rows]
        cond = self.proj_out(self.connector(self.proj_in(q_feats)))
        if self.connector_norm:
            denom = mx.maximum(mx.linalg.norm(cond.astype(mx.float32), axis=-1, keepdims=True), 1e-12)
            cond = (cond.astype(mx.float32) / denom).astype(cond.dtype)
        cond2 = None
        if self.use_directvlm:
            layers = self.selected if self.selected else [n_layers]
            cat = mx.concatenate([hidden[int(l)][:, :prompt_len] for l in layers], axis=-1)
            cond2 = self.proj_directvlm[1](self.proj_directvlm[0](cat))
        return cond, cond2


# ---------------------------------------------------------------------------
# Qwen2.5-ViT vision tower + linear_proj (for image editing / references)
# ---------------------------------------------------------------------------

_VISION_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
_VISION_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def _smart_resize(height: int, width: int, factor: int = 28, min_pixels: int = 451584, max_pixels: int = 451584) -> tuple[int, int]:
    if max(height, width) / max(1, min(height, width)) > 200:
        raise ValueError(f"image aspect ratio too extreme: {width}x{height}")
    h_bar = max(factor, round(height / factor) * factor)
    w_bar = max(factor, round(width / factor) * factor)
    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = max(factor, math.floor(height / beta / factor) * factor)
        w_bar = max(factor, math.floor(width / beta / factor) * factor)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = max(factor, math.ceil(height / beta * 1.0 if False else (height * beta) / factor) * factor)
        w_bar = max(factor, math.ceil((width * beta) / factor) * factor)
    return int(h_bar), int(w_bar)


def preprocess_vision_images(
    images: list,
    proc_cfg: dict | None = None,
) -> tuple[np.ndarray, list[tuple[int, int, int]]]:
    """Preprocess PIL images into Qwen2.5-ViT patches matching BailingMM2ImageProcessor."""
    from PIL import Image

    cfg = proc_cfg or {}
    patch_size = int(cfg.get("patch_size", 14))
    t_patch = int(cfg.get("temporal_patch_size", 2))
    merge_size = int(cfg.get("merge_size", 2))
    min_pixels = int(cfg.get("min_pixels", 451584))
    max_pixels = int(cfg.get("max_pixels", 451584))
    mean = np.array(cfg.get("image_mean", _VISION_MEAN.tolist()), dtype=np.float32)
    std = np.array(cfg.get("image_std", _VISION_STD.tolist()), dtype=np.float32)
    factor = patch_size * merge_size

    all_patches: list[np.ndarray] = []
    grids: list[tuple[int, int, int]] = []
    for im in images:
        rgb = im.convert("RGB")
        # Match upstream two-step resize: fetch_image (4*28^2 .. 1024*28^2) then processor smart_resize.
        w0, h0 = rgb.size
        h1, w1 = _smart_resize(h0, w0, factor=factor, min_pixels=4 * factor * factor, max_pixels=1024 * factor * factor)
        if (w1, h1) != (w0, h0):
            rgb = rgb.resize((w1, h1), Image.Resampling.BICUBIC)
        h2, w2 = _smart_resize(h1, w1, factor=factor, min_pixels=min_pixels, max_pixels=max_pixels)
        if (w2, h2) != (w1, h1):
            rgb = rgb.resize((w2, h2), Image.Resampling.BICUBIC)

        arr = np.asarray(rgb, dtype=np.float32) * np.float32(1.0 / 255.0)
        arr = (arr - mean[None, None, :]) / std[None, None, :]
        arr = arr.transpose(2, 0, 1)[None]  # [1, 3, H, W]
        patches = np.tile(arr, (t_patch, 1, 1, 1))  # [2, 3, H, W]
        grid_t = patches.shape[0] // t_patch
        grid_h, grid_w = h2 // patch_size, w2 // patch_size
        patches = patches.reshape(
            grid_t, t_patch, 3,
            grid_h // merge_size, merge_size, patch_size,
            grid_w // merge_size, merge_size, patch_size,
        )
        patches = patches.transpose(0, 3, 6, 4, 7, 2, 1, 5, 8)
        flat = patches.reshape(grid_t * grid_h * grid_w, 3 * t_patch * patch_size * patch_size)
        all_patches.append(flat)
        grids.append((grid_t, grid_h, grid_w))
    return np.concatenate(all_patches, axis=0), grids


def preprocess_reference_for_vae(
    images: list,
    width: int,
    height: int,
    crop_to_target_aspect: bool = False,
) -> list[np.ndarray]:
    """Prepare RGBA [-1, 1] NHWC float32 arrays matching upstream transform_reference_images."""
    from PIL import Image

    out: list[np.ndarray] = []
    target_ratio = float(width) / float(height)
    for im in images:
        rgba = im.convert("RGBA")
        if crop_to_target_aspect:
            w0, h0 = rgba.size
            orig_ratio = float(w0) / float(h0)
            if abs(orig_ratio - target_ratio) > 1e-4:
                if orig_ratio > target_ratio:
                    new_w = max(1, min(w0, int(math.floor(target_ratio * h0))))
                    left = (w0 - new_w) // 2
                    rgba = rgba.crop((left, 0, left + new_w, h0))
                else:
                    new_h = max(1, min(h0, int(math.floor(w0 / target_ratio))))
                    top = (h0 - new_h) // 2
                    rgba = rgba.crop((0, top, w0, top + new_h))
        if rgba.size != (width, height):
            rgba = rgba.resize((width, height), Image.Resampling.BILINEAR)
        arr = (np.asarray(rgba, dtype=np.float32) / np.float32(255.0) - np.float32(0.5)) * np.float32(2.0)
        out.append(arr[None])  # [1, H, W, 4]
    return out


class VisionPatchEmbed(nn.Module):
    def __init__(self, patch_size: int = 14, t_patch: int = 2, in_ch: int = 3, embed_dim: int = 1280) -> None:
        super().__init__()
        self.proj = {"weight": mx.zeros((embed_dim, in_ch, t_patch, patch_size, patch_size))}
        self.embed_dim = embed_dim

    def __call__(self, x: mx.array) -> mx.array:
        w = self.proj["weight"]
        return x.astype(w.dtype) @ w.reshape(self.embed_dim, -1).T


class VisionAttention(nn.Module):
    def __init__(self, dim: int, num_heads: int = 16) -> None:
        super().__init__()
        self.num_heads = num_heads
        self.head_dim = dim // num_heads
        self.qkv = nn.Linear(dim, dim * 3, bias=True)
        self.proj = nn.Linear(dim, dim, bias=True)

    @staticmethod
    def _rotate_half(t: mx.array) -> mx.array:
        mid = t.shape[-1] // 2
        return mx.concatenate([-t[..., mid:], t[..., :mid]], axis=-1)

    def __call__(self, x: mx.array, cu_seqlens: list[int], cos: mx.array, sin: mx.array) -> mx.array:
        seq_len, _ = x.shape
        qkv = self.qkv(x).reshape(seq_len, 3, self.num_heads, self.head_dim)
        q, k, v = qkv[:, 0], qkv[:, 1], qkv[:, 2]
        orig_dt = q.dtype
        c, s = cos[:, None, :], sin[:, None, :]
        qf = q.astype(mx.float32)
        kf = k.astype(mx.float32)
        q = (qf * c + self._rotate_half(qf) * s).astype(orig_dt)
        k = (kf * c + self._rotate_half(kf) * s).astype(orig_dt)

        seg_lens = [cu_seqlens[i] - cu_seqlens[i - 1] for i in range(1, len(cu_seqlens)) if cu_seqlens[i] > cu_seqlens[i - 1]]
        scale = self.head_dim ** -0.5
        if len(seg_lens) == 1:
            o = mx.fast.scaled_dot_product_attention(
                q.transpose(1, 0, 2)[None], k.transpose(1, 0, 2)[None], v.transpose(1, 0, 2)[None], scale=scale
            )[0].transpose(1, 0, 2).reshape(seq_len, -1)
        elif len(set(seg_lens)) == 1:
            n_seg, sl = len(seg_lens), seg_lens[0]
            qb = q.reshape(n_seg, sl, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
            kb = k.reshape(n_seg, sl, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
            vb = v.reshape(n_seg, sl, self.num_heads, self.head_dim).transpose(0, 2, 1, 3)
            o = mx.fast.scaled_dot_product_attention(qb, kb, vb, scale=scale).transpose(0, 2, 1, 3).reshape(seq_len, -1)
        else:
            parts = []
            for a, b in zip(cu_seqlens[:-1], cu_seqlens[1:]):
                if b <= a:
                    continue
                os_ = mx.fast.scaled_dot_product_attention(
                    q[a:b].transpose(1, 0, 2)[None],
                    k[a:b].transpose(1, 0, 2)[None],
                    v[a:b].transpose(1, 0, 2)[None],
                    scale=scale,
                )[0].transpose(1, 0, 2).reshape(b - a, -1)
                parts.append(os_)
            o = mx.concatenate(parts, axis=0)
        return self.proj(o)


class VisionMLP(nn.Module):
    def __init__(self, dim: int, hidden: int) -> None:
        super().__init__()
        self.gate_proj = nn.Linear(dim, hidden, bias=True)
        self.up_proj = nn.Linear(dim, hidden, bias=True)
        self.down_proj = nn.Linear(hidden, dim, bias=True)

    def __call__(self, x: mx.array) -> mx.array:
        return self.down_proj(nn.silu(self.gate_proj(x)) * self.up_proj(x))


class VisionBlock(nn.Module):
    def __init__(self, dim: int, inter: int, num_heads: int) -> None:
        super().__init__()
        self.norm1 = FastRMSNorm(dim, 1e-6)
        self.norm2 = FastRMSNorm(dim, 1e-6)
        self.attn = VisionAttention(dim, num_heads)
        self.mlp = VisionMLP(dim, inter)

    def __call__(self, x: mx.array, cu_seqlens: list[int], cos: mx.array, sin: mx.array) -> mx.array:
        x = x + self.attn(self.norm1(x), cu_seqlens, cos, sin)
        return x + self.mlp(self.norm2(x))


class VisionPatchMerger(nn.Module):
    def __init__(self, out_dim: int, context_dim: int, merge_size: int = 2) -> None:
        super().__init__()
        self.hidden_size = context_dim * (merge_size ** 2)
        self.ln_q = FastRMSNorm(context_dim, 1e-6)
        self.mlp = [nn.Linear(self.hidden_size, self.hidden_size), nn.GELU(), nn.Linear(self.hidden_size, out_dim)]

    def __call__(self, x: mx.array) -> mx.array:
        h = self.ln_q(x).reshape(-1, self.hidden_size)
        for layer in self.mlp:
            h = layer(h)
        return h


class Qwen25VisionTransformer(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.patch_size = int(cfg.get("patch_size", 14))
        self.t_patch = int(cfg.get("temporal_patch_size", 2))
        self.merge_size = int(cfg.get("spatial_merge_size", 2))
        self.merge_unit = self.merge_size * self.merge_size
        self.window_size = int(cfg.get("window_size", 112))
        self.fullatt_blocks = set(int(i) for i in cfg.get("fullatt_block_indexes", [7, 15, 23, 31]))
        dim = int(cfg.get("hidden_size", 1280))
        inter = int(cfg.get("intermediate_size", 3456))
        num_heads = int(cfg.get("num_heads", 16))
        self.head_dim = dim // num_heads
        self.patch_embed = VisionPatchEmbed(self.patch_size, self.t_patch, int(cfg.get("in_channels", 3)), dim)
        self.blocks = [VisionBlock(dim, inter, num_heads) for _ in range(int(cfg.get("depth", 32)))]
        self.merger = VisionPatchMerger(int(cfg.get("out_hidden_size", 8192)), dim, self.merge_size)

    def _rot_pos_emb(self, grids: list[tuple[int, int, int]]) -> np.ndarray:
        ms = self.merge_size
        pos_ids = []
        max_grid = 1
        for t, h, w in grids:
            max_grid = max(max_grid, h, w)
            hpos = np.broadcast_to(np.arange(h)[:, None], (h, w)).reshape(h // ms, ms, w // ms, ms).transpose(0, 2, 1, 3).reshape(-1)
            wpos = np.broadcast_to(np.arange(w)[None, :], (h, w)).reshape(h // ms, ms, w // ms, ms).transpose(0, 2, 1, 3).reshape(-1)
            pos_ids.append(np.tile(np.stack([hpos, wpos], axis=-1), (t, 1)))
        pos_ids_all = np.concatenate(pos_ids, axis=0)
        rot_dim = self.head_dim // 2
        inv_freq = 1.0 / (10000.0 ** (np.arange(0, rot_dim, 2, dtype=np.float32) / rot_dim))
        seq = np.arange(max_grid, dtype=np.float32)
        full = np.outer(seq, inv_freq)  # [max_grid, rot_dim // 2]
        return full[pos_ids_all].reshape(pos_ids_all.shape[0], -1)  # [L, rot_dim]

    def _window_index(self, grids: list[tuple[int, int, int]]) -> tuple[np.ndarray, list[int]]:
        win_idx: list[np.ndarray] = []
        cu_win: list[int] = [0]
        offset = 0
        ms = self.merge_size
        vw = self.window_size // ms // self.patch_size
        for t, h, w in grids:
            gh, gw = h // ms, w // ms
            idx = np.arange(t * gh * gw, dtype=np.int64).reshape(t, gh, gw)
            pad_h = vw - gh % vw
            pad_w = vw - gw % vw
            nh = (gh + pad_h) // vw
            nw = (gw + pad_w) // vw
            padded = np.pad(idx, ((0, 0), (0, pad_h), (0, pad_w)), mode="constant", constant_values=-100)
            padded = padded.reshape(t, nh, vw, nw, vw).transpose(0, 1, 3, 2, 4).reshape(t, nh * nw, vw, vw)
            seqlens = (padded != -100).sum(axis=(2, 3)).reshape(-1)
            flat = padded.reshape(-1)
            win_idx.append(flat[flat != -100] + offset)
            cu_tmp = (np.cumsum(seqlens) * self.merge_unit + cu_win[-1]).tolist()
            cu_win.extend(int(v) for v in cu_tmp)
            offset += t * gh * gw
        # Deduplicate consecutive equal cu_seqlens (matches torch.unique_consecutive).
        dedup = [cu_win[0]]
        for v in cu_win[1:]:
            if v != dedup[-1]:
                dedup.append(v)
        return np.concatenate(win_idx, axis=0), dedup

    def __call__(self, pixel_values: mx.array, grids: list[tuple[int, int, int]]) -> mx.array:
        h = self.patch_embed(pixel_values)
        freqs = self._rot_pos_emb(grids)
        win_idx_np, cu_win = self._window_index(grids)
        win_idx = mx.array(win_idx_np)

        seq_len = h.shape[0]
        h = h.reshape(seq_len // self.merge_unit, self.merge_unit, -1)[win_idx].reshape(seq_len, -1)
        freqs_win = freqs.reshape(seq_len // self.merge_unit, self.merge_unit, -1)[win_idx_np].reshape(seq_len, -1)
        cos = mx.array(np.concatenate([np.cos(freqs_win), np.cos(freqs_win)], axis=-1).astype(np.float32))
        sin = mx.array(np.concatenate([np.sin(freqs_win), np.sin(freqs_win)], axis=-1).astype(np.float32))

        cu_full = [0]
        for t, gh, gw in grids:
            for _ in range(t):
                cu_full.append(cu_full[-1] + gh * gw)

        for i, blk in enumerate(self.blocks):
            cu = cu_full if i in self.fullatt_blocks else cu_win
            h = blk(h, cu, cos, sin)
            if (i + 1) % 8 == 0:
                mx.eval(h)

        h = self.merger(h)
        rev_idx = mx.array(np.argsort(win_idx_np))
        return h[rev_idx]


class MingVisionTower(nn.Module):
    """Qwen2.5-ViT + 2-layer GELU linear_proj + L2 normalization matching BailingMM2."""

    def __init__(self, vision_cfg: dict, llm_dim: int, mlp_depth: int = 2) -> None:
        super().__init__()
        self.vision = Qwen25VisionTransformer(vision_cfg)
        emb_dim = int(vision_cfg.get("out_hidden_size", 8192))
        layers: list[nn.Module] = [nn.Linear(emb_dim, llm_dim, bias=True)]
        for _ in range(1, mlp_depth):
            layers.append(nn.GELU())
            layers.append(nn.Linear(llm_dim, llm_dim, bias=True))
        self.linear_proj = layers

    def __call__(self, pixel_values: mx.array, grids: list[tuple[int, int, int]]) -> mx.array:
        x = self.vision(pixel_values, grids)
        for layer in self.linear_proj:
            x = layer(x)
        xf = x.astype(mx.float32)
        denom = mx.maximum(mx.sqrt(mx.sum(xf * xf, axis=-1, keepdims=True)), 1e-12)
        return (xf / denom).astype(x.dtype)


# ---------------------------------------------------------------------------
# 6B single-stream Ming DiT (resident or block-streamed)
# ---------------------------------------------------------------------------

_ADALN_DIM = 256
_ALIGN_MULT = 32


def _sinusoidal_t(t: mx.array, dim: int = 256, max_period: float = 10000.0) -> mx.array:
    half = dim // 2
    freqs = mx.exp(-math.log(max_period) * mx.arange(0, half, dtype=mx.float32) / half)
    ang = t[:, None].astype(mx.float32) * freqs[None]
    return mx.concatenate([mx.cos(ang), mx.sin(ang)], axis=-1)


class TimeEmbed(nn.Module):
    def __init__(self, out_dim: int, mid_dim: int = 1024, freq_dim: int = 256) -> None:
        super().__init__()
        self.mlp = [nn.Linear(freq_dim, mid_dim), nn.SiLU(), nn.Linear(mid_dim, out_dim)]
        self.freq_dim = freq_dim

    def __call__(self, t: mx.array) -> mx.array:
        x = _sinusoidal_t(t, self.freq_dim).astype(self.mlp[0].weight.dtype)
        for m in self.mlp:
            x = m(x)
        return x


def _rope3d_cos_sin(pos_ids: np.ndarray, axes_dims: list[int], theta: float) -> tuple[mx.array, mx.array]:
    parts = []
    for axis_i, d in enumerate(axes_dims):
        freqs = 1.0 / (theta ** (np.arange(0, d, 2, dtype=np.float64) / d))
        parts.append(np.outer(pos_ids[:, axis_i].astype(np.float64), freqs).astype(np.float32))
    ang = np.concatenate(parts, axis=-1)
    return mx.array(np.cos(ang)), mx.array(np.sin(ang))


class DiTAttention(nn.Module):
    def __init__(self, dim: int, n_heads: int, qk_norm: bool = True, eps: float = 1e-5) -> None:
        super().__init__()
        self.n_heads = n_heads
        self.head_dim = dim // n_heads
        self.to_q = nn.Linear(dim, dim, bias=False)
        self.to_k = nn.Linear(dim, dim, bias=False)
        self.to_v = nn.Linear(dim, dim, bias=False)
        self.to_out = [nn.Linear(dim, dim, bias=False)]
        if qk_norm:
            self.norm_q = FastRMSNorm(self.head_dim, eps)
            self.norm_k = FastRMSNorm(self.head_dim, eps)

    def __call__(self, x: mx.array, mask: mx.array | None, cos: mx.array, sin: mx.array, fused: bool = True) -> mx.array:
        b, seq, _ = x.shape
        q = self.to_q(x).reshape(b, seq, self.n_heads, self.head_dim)
        k = self.to_k(x).reshape(b, seq, self.n_heads, self.head_dim)
        v = self.to_v(x).reshape(b, seq, self.n_heads, self.head_dim)
        if "norm_q" in self:
            q = _qk_norm_rope(q, self.norm_q.weight, cos, sin, self.norm_q.eps, fused=fused)
            k = _qk_norm_rope(k, self.norm_k.weight, cos, sin, self.norm_k.eps, fused=fused)
        else:
            q = _qk_norm_rope(q, mx.ones((self.head_dim,), q.dtype), cos, sin, 1e-5, fused=False)
            k = _qk_norm_rope(k, mx.ones((self.head_dim,), k.dtype), cos, sin, 1e-5, fused=False)
        o = mx.fast.scaled_dot_product_attention(
            q, k, v.transpose(0, 2, 1, 3), scale=self.head_dim ** -0.5, mask=mask
        )
        return self.to_out[0](o.transpose(0, 2, 1, 3).reshape(b, seq, -1))


class DiTFFN(nn.Module):
    def __init__(self, dim: int, hidden: int) -> None:
        super().__init__()
        self.w1 = nn.Linear(dim, hidden, bias=False)
        self.w2 = nn.Linear(hidden, dim, bias=False)
        self.w3 = nn.Linear(dim, hidden, bias=False)

    def __call__(self, x: mx.array) -> mx.array:
        return self.w2(nn.silu(self.w1(x)) * self.w3(x))


class DiTBlock(nn.Module):
    def __init__(self, dim: int, n_heads: int, eps: float, qk_norm: bool, modulated: bool = True) -> None:
        super().__init__()
        self.attention = DiTAttention(dim, n_heads, qk_norm, eps=eps)
        self.feed_forward = DiTFFN(dim, int(dim / 3 * 8))
        self.attention_norm1 = FastRMSNorm(dim, eps)
        self.ffn_norm1 = FastRMSNorm(dim, eps)
        self.attention_norm2 = FastRMSNorm(dim, eps)
        self.ffn_norm2 = FastRMSNorm(dim, eps)
        self.modulated = modulated
        if modulated:
            self.adaLN_modulation = [nn.Linear(min(dim, _ADALN_DIM), 4 * dim)]

    def __call__(
        self,
        x: mx.array,
        mask: mx.array | None,
        cos: mx.array,
        sin: mx.array,
        adaln: mx.array | None = None,
        fused: bool = True,
    ) -> mx.array:
        if self.modulated:
            mod = self.adaLN_modulation[0](adaln)[:, None, :]
            s_msa, g_msa, s_mlp, g_mlp = mx.split(mod, 4, axis=-1)
            g_msa, g_mlp = mx.tanh(g_msa), mx.tanh(g_mlp)
            a = self.attention(self.attention_norm1(x) * (1.0 + s_msa), mask, cos, sin, fused=fused)
            x = x + g_msa * self.attention_norm2(a)
            x = x + g_mlp * self.ffn_norm2(self.feed_forward(self.ffn_norm1(x) * (1.0 + s_mlp)))
        else:
            a = self.attention(self.attention_norm1(x), mask, cos, sin, fused=fused)
            x = x + self.attention_norm2(a)
            x = x + self.ffn_norm2(self.feed_forward(self.ffn_norm1(x)))
        return x


class DiTFinalLayer(nn.Module):
    def __init__(self, dim: int, out_ch: int) -> None:
        super().__init__()
        self.norm_final = FastLayerNorm(dim, eps=1e-6, affine=False)
        self.linear = nn.Linear(dim, out_ch)
        self.adaLN_modulation = [nn.SiLU(), nn.Linear(min(dim, _ADALN_DIM), dim)]

    def __call__(self, x: mx.array, cond: mx.array) -> mx.array:
        scale = 1.0 + self.adaLN_modulation[1](nn.silu(cond))
        return self.linear(self.norm_final(x) * scale[:, None, :])


class MingDiT(nn.Module):
    def __init__(self, cfg: dict, mlp_cfg: dict, include_main_layers: bool = True) -> None:
        super().__init__()
        self.cfg = cfg
        dim = int(cfg["dim"])
        n_heads = int(cfg["n_heads"])
        eps = float(cfg["norm_eps"])
        qk_norm = bool(cfg.get("qk_norm", True))
        self.dim = dim
        self.n_heads = n_heads
        self.eps = eps
        self.qk_norm = qk_norm
        self.n_layers = int(cfg["n_layers"])
        self.patch = int(cfg["all_patch_size"][0])
        self.fpatch = int(cfg["all_f_patch_size"][0])
        self.in_channels = int(cfg["in_channels"])
        self.t_scale = float(cfg.get("t_scale", 1000.0))
        self.axes_dims = list(cfg["axes_dims"])
        self.theta = float(cfg.get("rope_theta", 256.0))
        self.padding_mode = cfg.get("alignment_padding_mode", "zero_masked")
        self.fused_qk = os.environ.get("PHOSPHENE_MING_FUSED_ROPE", "1") != "0"

        key = f"{self.patch}-{self.fpatch}"
        pdim = self.fpatch * self.patch * self.patch * self.in_channels
        self.all_x_embedder = {key: nn.Linear(pdim, dim)}
        self.all_final_layer = {key: DiTFinalLayer(dim, pdim)}
        n_ref = int(cfg.get("n_refiner_layers", 2))
        self.noise_refiner = [DiTBlock(dim, n_heads, eps, qk_norm, True) for _ in range(n_ref)]
        self.context_refiner = [DiTBlock(dim, n_heads, eps, qk_norm, False) for _ in range(n_ref)]
        self.t_embedder = TimeEmbed(min(dim, _ADALN_DIM), 1024)
        cap_dim = int(cfg["cap_feat_dim"])
        self.cap_embedder = [FastRMSNorm(cap_dim, eps), nn.Linear(cap_dim, dim)]
        if self.padding_mode == "learned":
            self.x_pad_token = mx.zeros((1, dim))
            self.cap_pad_token = mx.zeros((1, dim))
        if include_main_layers:
            self.layers = [DiTBlock(dim, n_heads, eps, qk_norm, True) for _ in range(self.n_layers)]
        else:
            self.layers = []
        self.use_identity_mlp = bool(mlp_cfg.get("use_identity_mlp", True))
        self.text_encoder_norm = bool(mlp_cfg.get("text_encoder_norm", False))
        self._layout_cache: dict[tuple[int, int, int], dict] = {}

    def prepare_cond(self, cond: mx.array | None) -> mx.array | None:
        if cond is None:
            return None
        if self.text_encoder_norm:
            n = mx.sqrt(mx.sum(cond.astype(mx.float32) ** 2, axis=-1, keepdims=True))
            cond = (cond / mx.maximum(n, 1e-12) * 1000.0).astype(cond.dtype)
        return cond

    def _layout(self, h: int, w: int, cap_len: int, num_frames: int = 1) -> dict:
        key = (h, w, cap_len, num_frames)
        cached = self._layout_cache.get(key)
        if cached is not None:
            return cached
        p = self.patch
        cap_pad = (-cap_len) % _ALIGN_MULT
        ht, wt = h // p, w // p
        n_target = ht * wt
        n_img = num_frames * n_target
        img_pad = (-n_img) % _ALIGN_MULT
        f0 = cap_len + cap_pad + 1

        cap_pos = np.stack(
            [np.arange(1, 1 + cap_len + cap_pad), np.zeros(cap_len + cap_pad), np.zeros(cap_len + cap_pad)],
            axis=-1,
        ).astype(np.int64)
        ff, hh, ww = np.meshgrid(np.arange(f0, f0 + num_frames), np.arange(ht), np.arange(wt), indexing="ij")
        img_pos = np.stack([ff.ravel(), hh.ravel(), ww.ravel()], axis=-1).astype(np.int64)
        if img_pad:
            img_pos = np.concatenate([img_pos, np.zeros((img_pad, 3), dtype=np.int64)], axis=0)

        # In zero_masked mode, padded keys are masked out everywhere and discarded
        # at output; dropping them while preserving f0 is exact and avoids SDPA masks.
        if self.padding_mode == "zero_masked":
            img_pos = img_pos[:n_img]
            cap_pos = cap_pos[:cap_len]
            img_pad = 0
            cap_pad = 0

        xc, xs = _rope3d_cos_sin(img_pos, self.axes_dims, self.theta)
        cc, cs = _rope3d_cos_sin(cap_pos, self.axes_dims, self.theta)
        uc, us = mx.concatenate([xc, cc], axis=0), mx.concatenate([xs, cs], axis=0)
        lay = {
            "n_target": n_target,
            "n_img": n_img,
            "img_pad": img_pad,
            "cap_pad": cap_pad,
            "x": (xc, xs),
            "cap": (cc, cs),
            "uni": (uc, us),
        }
        self._layout_cache[key] = lay
        return lay

    def _prologue(
        self,
        latents: mx.array,
        t: mx.array,
        cap_feats: mx.array | None,
        cap_feats_2: mx.array | None,
        ref_latents: mx.array | None = None,
    ) -> tuple[mx.array, mx.array, dict, str]:
        b, c_ch, h, w = latents.shape
        p = self.patch
        key = f"{p}-{self.fpatch}"
        dtype = self.all_x_embedder[key].weight.dtype
        cap_len = (0 if cap_feats is None else cap_feats.shape[1]) + (
            0 if cap_feats_2 is None else cap_feats_2.shape[1]
        )
        if ref_latents is not None:
            ref_5d = ref_latents[:, :, None, :, :] if ref_latents.ndim == 4 else ref_latents
            x_frames = mx.concatenate([latents[:, :, None, :, :], ref_5d], axis=2)
        else:
            x_frames = latents[:, :, None, :, :]
        num_frames = int(x_frames.shape[2])
        lay = self._layout(h, w, cap_len, num_frames=num_frames)
        adaln = self.t_embedder(t * self.t_scale).astype(dtype)

        x = x_frames.reshape(b, c_ch, num_frames, h // p, p, w // p, p).transpose(0, 2, 3, 5, 4, 6, 1)
        x = x.reshape(b, lay["n_img"], p * p * c_ch)
        if lay["img_pad"]:
            x = mx.concatenate([x, mx.repeat(x[:, -1:], lay["img_pad"], axis=1)], axis=1)
        x = self.all_x_embedder[key](x.astype(dtype))
        if lay["img_pad"]:
            pad_val = self.x_pad_token if self.padding_mode == "learned" else mx.zeros((1, x.shape[-1]), dtype)
            x = mx.concatenate(
                [x[:, : lay["n_img"]], mx.broadcast_to(pad_val[None], (b, lay["img_pad"], x.shape[-1])).astype(dtype)],
                axis=1,
            )
        for blk in self.noise_refiner:
            x = blk(x, None, *lay["x"], adaln, fused=self.fused_qk)

        parts = []
        if cap_feats is not None:
            parts.append(self.cap_embedder[1](self.cap_embedder[0](cap_feats.astype(dtype))))
        if cap_feats_2 is not None:
            parts.append(cap_feats_2.astype(dtype))
        c_stream = mx.concatenate(parts, axis=1) if len(parts) > 1 else parts[0]
        if lay["cap_pad"]:
            pad_val = self.cap_pad_token if self.padding_mode == "learned" else mx.zeros((1, c_stream.shape[-1]), dtype)
            c_stream = mx.concatenate(
                [c_stream, mx.broadcast_to(pad_val[None], (b, lay["cap_pad"], c_stream.shape[-1])).astype(dtype)],
                axis=1,
            )
        for blk in self.context_refiner:
            c_stream = blk(c_stream, None, *lay["cap"], fused=self.fused_qk)

        return mx.concatenate([x, c_stream], axis=1), adaln, lay, key

    def _epilogue(self, u: mx.array, adaln: mx.array, lay: dict, key: str, shape: tuple[int, int, int, int]) -> mx.array:
        b, c_ch, h, w = shape
        p = self.patch
        u = self.all_final_layer[key](u, adaln)
        u = u[:, : lay["n_target"]].astype(mx.float32)
        u = u.reshape(b, h // p, w // p, p, p, c_ch).transpose(0, 5, 1, 3, 2, 4)
        return u.reshape(b, c_ch, h, w)

    def __call__(
        self,
        latents: mx.array,
        t: mx.array,
        cap_feats: mx.array | None,
        cap_feats_2: mx.array | None = None,
        ref_latents: mx.array | None = None,
    ) -> mx.array:
        u, adaln, lay, key = self._prologue(latents, t, cap_feats, cap_feats_2, ref_latents=ref_latents)
        for blk in self.layers:
            u = blk(u, None, *lay["uni"], adaln, fused=self.fused_qk)
        return self._epilogue(u, adaln, lay, key, latents.shape)

    def forward_streamed(
        self,
        latents: mx.array,
        t: mx.array,
        cap_feats: mx.array | None,
        cap_feats_2: mx.array | None,
        store: ShardStore,
        ref_latents: mx.array | None = None,
    ) -> mx.array:
        """Run the 30 main DiT blocks one block at a time from disk (exact match to resident)."""
        u, adaln, lay, key = self._prologue(latents, t, cap_feats, cap_feats_2, ref_latents=ref_latents)
        mx.eval(u, adaln)
        for i in range(self.n_layers):
            blk = DiTBlock(self.dim, self.n_heads, self.eps, self.qk_norm, True)
            prefix = f"layers.{i}."
            bw = store.load_keys_matching("dit", prefix, strip_prefix=prefix)
            blk.load_weights(list(bw.items()), strict=True)
            u = blk(u, None, *lay["uni"], adaln, fused=self.fused_qk)
            mx.eval(u)
            del bw, blk
            mx.clear_cache()
        return self._epilogue(u, adaln, lay, key, latents.shape)


# ---------------------------------------------------------------------------
# 2D-collapsed Qwen-Image RGBA VAE decoder
# ---------------------------------------------------------------------------

class ChannelRMSNorm(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.gamma = mx.ones((dim,))
        self.scale = dim ** 0.5

    def __call__(self, x: mx.array) -> mx.array:
        dt = x.dtype
        xf = x.astype(mx.float32)
        n = mx.sqrt(mx.sum(xf * xf, axis=-1, keepdims=True))
        return (xf / mx.maximum(n, 1e-12)).astype(dt) * self.scale * self.gamma.astype(dt)


class Conv2D(nn.Module):
    def __init__(self, cin: int, cout: int, k: int, stride: int = 1, padding: int = 0, bias: bool = True) -> None:
        super().__init__()
        self.weight = mx.zeros((cout, k, k, cin))
        if bias:
            self.bias = mx.zeros((cout,))
        self.stride = stride
        self.padding = padding

    def __call__(self, x: mx.array) -> mx.array:
        y = mx.conv2d(x, self.weight, stride=self.stride, padding=self.padding)
        return y + self.bias if "bias" in self else y


class VaeResBlock(nn.Module):
    def __init__(self, cin: int, cout: int) -> None:
        super().__init__()
        self.norm1 = ChannelRMSNorm(cin)
        self.conv1 = Conv2D(cin, cout, 3, padding=1)
        self.norm2 = ChannelRMSNorm(cout)
        self.conv2 = Conv2D(cout, cout, 3, padding=1)
        self.has_shortcut = cin != cout
        if self.has_shortcut:
            self.conv_shortcut = Conv2D(cin, cout, 1)

    def __call__(self, x: mx.array) -> mx.array:
        skip = self.conv_shortcut(x) if self.has_shortcut else x
        h = self.conv1(nn.silu(self.norm1(x)))
        h = self.conv2(nn.silu(self.norm2(h)))
        return h + skip


class VaeMidAttn(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.norm = ChannelRMSNorm(dim)
        self.to_qkv = Conv2D(dim, dim * 3, 1)
        self.proj = Conv2D(dim, dim, 1)

    def __call__(self, x: mx.array) -> mx.array:
        b, h, w, c = x.shape
        qkv = self.to_qkv(self.norm(x)).reshape(b, 1, h * w, 3 * c)
        q, k, v = mx.split(qkv, 3, axis=-1)
        o = mx.fast.scaled_dot_product_attention(q, k, v, scale=c ** -0.5).reshape(b, h, w, c)
        return self.proj(o) + x


class VaeUpsample2x(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.conv = Conv2D(dim, dim // 2, 3, padding=1)

    def __call__(self, x: mx.array) -> mx.array:
        b, h, w, c = x.shape
        up = mx.broadcast_to(x[:, :, None, :, None, :], (b, h, 2, w, 2, c)).reshape(b, 2 * h, 2 * w, c)
        return self.conv(up)


class VaeUpStage(nn.Module):
    def __init__(self, cin: int, cout: int, num_res: int, upsample: bool) -> None:
        super().__init__()
        self.resnets = []
        cur = cin
        for _ in range(num_res + 1):
            self.resnets.append(VaeResBlock(cur, cout))
            cur = cout
        self.upsampler = VaeUpsample2x(cout) if upsample else None

    def __call__(self, x: mx.array) -> mx.array:
        for block in self.resnets:
            x = block(x)
        if self.upsampler is not None:
            x = self.upsampler(x)
        return x


class VaeInnerDecoder(nn.Module):
    def __init__(self, base_dim: int, z_dim: int, dim_mult: tuple[int, ...], num_res: int, out_ch: int) -> None:
        super().__init__()
        dims = [base_dim * m for m in [dim_mult[-1]] + list(dim_mult[::-1])]
        self.conv_in = Conv2D(z_dim, dims[0], 3, padding=1)
        self.mid_res0 = VaeResBlock(dims[0], dims[0])
        self.mid_attn = VaeMidAttn(dims[0])
        self.mid_res1 = VaeResBlock(dims[0], dims[0])
        self.up_blocks = []
        for i, (cin, cout) in enumerate(zip(dims[:-1], dims[1:])):
            if i > 0:
                cin = cin // 2
            self.up_blocks.append(VaeUpStage(cin, cout, num_res, i != len(dim_mult) - 1))
        self.norm_out = ChannelRMSNorm(cout)
        self.conv_out = Conv2D(cout, out_ch, 3, padding=1)

    def __call__(self, x: mx.array) -> mx.array:
        x = self.mid_res1(self.mid_attn(self.mid_res0(self.conv_in(x))))
        for stage in self.up_blocks:
            x = stage(x)
            mx.eval(x)
        return self.conv_out(nn.silu(self.norm_out(x)))


class VaeDownsample2x(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.conv = Conv2D(dim, dim, 3, stride=2, padding=0)

    def __call__(self, x: mx.array) -> mx.array:
        # Matches upstream ZeroPad2d((0, 1, 0, 1)): pad right=1, bottom=1 before stride-2 3x3 conv.
        padded = mx.pad(x, [(0, 0), (0, 1), (0, 1), (0, 0)])
        return self.conv(padded)


class VaeInnerEncoder(nn.Module):
    def __init__(self, base_dim: int, z_dim_out: int, dim_mult: tuple[int, ...], num_res: int, in_ch: int) -> None:
        super().__init__()
        dims = [base_dim * m for m in [1] + list(dim_mult)]
        self.conv_in = Conv2D(in_ch, dims[0], 3, padding=1)
        self.down_blocks = []
        for i, (cin, cout) in enumerate(zip(dims[:-1], dims[1:])):
            cur = cin
            for _ in range(num_res):
                self.down_blocks.append(VaeResBlock(cur, cout))
                cur = cout
            if i != len(dim_mult) - 1:
                self.down_blocks.append(VaeDownsample2x(cout))
        self.mid_res0 = VaeResBlock(dims[-1], dims[-1])
        self.mid_attn = VaeMidAttn(dims[-1])
        self.mid_res1 = VaeResBlock(dims[-1], dims[-1])
        self.norm_out = ChannelRMSNorm(dims[-1])
        self.conv_out = Conv2D(dims[-1], z_dim_out, 3, padding=1)

    def __call__(self, x: mx.array) -> mx.array:
        x = self.conv_in(x)
        for blk in self.down_blocks:
            x = blk(x)
            if isinstance(blk, VaeDownsample2x):
                mx.eval(x)
        x = self.mid_res1(self.mid_attn(self.mid_res0(x)))
        return self.conv_out(nn.silu(self.norm_out(x)))


class RgbaVaeEncoder(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        self.z_dim = int(cfg["z_dim"])
        self.quant_conv = Conv2D(self.z_dim * 2, self.z_dim * 2, 1)
        self.encoder = VaeInnerEncoder(
            base_dim=int(cfg["base_dim"]),
            z_dim_out=self.z_dim * 2,
            dim_mult=tuple(int(m) for m in cfg["dim_mult"]),
            num_res=int(cfg["num_res_blocks"]),
            in_ch=int(cfg.get("input_channels", 4)),
        )

    def encode_mode(self, x_nhwc: mx.array) -> mx.array:
        """Return deterministic posterior mean (argmax mode) of shape [B, H//8, W//8, z_dim]."""
        h = self.quant_conv(self.encoder(x_nhwc))
        return h[..., : self.z_dim]


class RgbaVaeDecoder(nn.Module):
    def __init__(self, cfg: dict) -> None:
        super().__init__()
        z_dim = int(cfg["z_dim"])
        self.post_quant_conv = Conv2D(z_dim, z_dim, 1)
        self.decoder = VaeInnerDecoder(
            base_dim=int(cfg["base_dim"]),
            z_dim=z_dim,
            dim_mult=tuple(int(m) for m in cfg["dim_mult"]),
            num_res=int(cfg["num_res_blocks"]),
            out_ch=int(cfg.get("input_channels", 4)),
        )

    def decode(self, z_nhwc: mx.array) -> mx.array:
        return mx.clip(self.decoder(self.post_quant_conv(z_nhwc)), -1.0, 1.0)


# ---------------------------------------------------------------------------
# Staged end-to-end runner
# ---------------------------------------------------------------------------

_CHAT_PREFIX = "<role>SYSTEM</role>你是一个友好的AI助手。\n\ndetailed thinking off<|role_end|><role>HUMAN</role>"
_CHAT_SUFFIX = "<|role_end|><role>ASSISTANT</role>"


def format_chat_prompt(prompt: str) -> str:
    return _CHAT_PREFIX + prompt + _CHAT_SUFFIX


def _build_llm_token_inputs(
    ids: np.ndarray,
    llm_cfg: dict,
    mlp_cfg: dict,
    ref_grids: list[tuple[int, int, int]] | None = None,
):
    patch = int(llm_cfg["image_patch_token"])
    start = patch + 1
    end = patch + 2
    scales = list(mlp_cfg.get("img_gen_scales", [16]))
    extra: list[int] = []
    grids: list[tuple[int, int, int]] = list(ref_grids or [])
    for s in scales:
        extra.extend([start] + [patch] * (s * s) + [end])
        grids.append((1, 2, s * s * 2))
    full = np.concatenate([ids, np.array(extra, dtype=np.int64)])
    pos3 = build_multiscale_positions(full, llm_cfg, grids)
    patch_rows = np.nonzero(full == patch)[0]
    gen_rows = patch_rows[-(scales[-1] ** 2) :]
    return full, pos3, patch_rows, gen_rows


class MingImageEngine:
    """Staged Ming-Image-0.2-Design MLX engine with resident & streamed modes.

    ``low_memory`` modes:
      - ``"off"``  : resident LLM (17.3 GB) + resident DiT (12.3 GB), released between stages.
      - ``"llm"``  : layer-streamed LLM (~2.5 GiB) + resident DiT (~14-24 GiB). Ideal for 32-48 GB Macs.
      - ``"full"`` : layer-streamed LLM + block-streamed DiT (~3-6 GiB at 1024). Fits 16-24 GB Macs.
    """

    def __init__(self, model_dir: Path, low_memory: str = "off", compile_dit: bool = True) -> None:
        import json
        from tokenizers import Tokenizer

        self.root = Path(model_dir)
        self.cfg = json.loads((self.root / "config.json").read_text(encoding="utf-8"))
        self.index = json.loads((self.root / "index.json").read_text(encoding="utf-8"))
        q_path = self.root / "quantization.json"
        self.q_cfg = json.loads(q_path.read_text(encoding="utf-8")) if q_path.is_file() else {}
        tok_cfg_path = self.root / "tokenizer" / "config.json"
        self.mllm_cfg = json.loads(tok_cfg_path.read_text(encoding="utf-8")) if tok_cfg_path.is_file() else {}
        proc_cfg_path = self.root / "tokenizer" / "preprocessor_config.json"
        self.proc_cfg = json.loads(proc_cfg_path.read_text(encoding="utf-8")) if proc_cfg_path.is_file() else {}
        self.store = ShardStore(self.root, self.index)
        self.scheduler = FlowMatchSchedule(self.cfg["scheduler"])
        self.tokenizer = Tokenizer.from_file(str(self.root / "tokenizer" / "tokenizer.json"))
        self.low_memory = low_memory
        self.compile_dit = compile_dit

    def close(self) -> None:
        self.store.close()

    def _find_edit_file(self, filename: str) -> Path:
        for cand in (self.root / filename, self.root.parent / filename):
            if cand.is_file():
                return cand
        raise FileNotFoundError(
            f"Missing {filename} for Ming-Image picture editing in {self.root}. "
            f"Run `python3 -m scripts.ming_image.fetch --dir {self.root}` to fetch edit weights."
        )

    def encode_vision(self, ref_images: list) -> tuple[mx.array, list[tuple[int, int, int]]]:
        """Run Qwen2.5-ViT + linear_proj + L2 norm on reference PIL image(s), then release weights."""
        vis_path = self._find_edit_file("vision.safetensors")
        vision_cfg = self.mllm_cfg.get("vision_config") or self.cfg.get("vision") or {}
        mlp_depth = int(self.mllm_cfg.get("mlp_depth", 2))
        llm_dim = int(self.cfg["llm"]["hidden_size"])

        pixel_np, grids = preprocess_vision_images(ref_images, self.proc_cfg)
        tower = MingVisionTower(vision_cfg, llm_dim, mlp_depth=mlp_depth)
        vw = mx.load(str(vis_path))
        tower.load_weights(list(vw.items()), strict=True)
        del vw
        mx.eval(tower.parameters())

        embeds = tower(mx.array(pixel_np), grids)
        mx.eval(embeds)
        del tower
        gc.collect()
        mx.clear_cache()
        return embeds, grids

    def encode_ref_latents(
        self,
        ref_images: list,
        width: int,
        height: int,
        crop_to_target_aspect: bool = False,
    ) -> mx.array:
        """Encode 1+ RGBA reference images through the 2D-collapsed Qwen-Image VAE encoder.

        Returns shape ``[1, z_dim, N_ref, H//8, W//8]`` in bfloat16.
        """
        enc_path = self._find_edit_file("vae_encoder.safetensors")
        vc = self.cfg["vae"]
        vae_enc = RgbaVaeEncoder(vc)
        ew = mx.load(str(enc_path))
        vae_enc.load_weights(list(ew.items()), strict=True)
        del ew
        mx.eval(vae_enc.parameters())

        arrays = preprocess_reference_for_vae(
            ref_images, width=width, height=height, crop_to_target_aspect=crop_to_target_aspect
        )
        shift = mx.array(float(vc.get("shift_factor", 0.0)), mx.bfloat16)
        scale = mx.array(float(vc["scaling_factor"]), mx.bfloat16)
        lat_list = []
        for arr in arrays:
            x_bf = mx.array(arr).astype(mx.bfloat16)
            z = vae_enc.encode_mode(x_bf)
            z = (z - shift) * scale
            z_nchw = z.transpose(0, 3, 1, 2)  # [1, C, H//8, W//8]
            mx.eval(z_nchw)
            lat_list.append(z_nchw)
        ref_lat = mx.stack(lat_list, axis=2)  # [1, C, N_ref, H//8, W//8]
        mx.eval(ref_lat)
        del vae_enc, lat_list
        gc.collect()
        mx.clear_cache()
        return ref_lat

    def _tokenize_prompt(
        self,
        prompt: str,
        llm_cfg: dict,
        ref_grids: list[tuple[int, int, int]] | None = None,
    ) -> np.ndarray:
        if not ref_grids:
            return np.array(
                self.tokenizer.encode(format_chat_prompt(prompt), add_special_tokens=False).ids,
                dtype=np.int64,
            )
        # Match upstream apply_chat_template + _expand_image_tokens verbatim:
        # <role>SYSTEM</role>...<role>HUMAN</role><image><imagePatch>*N</image>\n...{prompt}<|role_end|><role>ASSISTANT</role>
        merge = int(llm_cfg.get("spatial_merge_size", 2))
        patch = int(llm_cfg["image_patch_token"])
        start = int(llm_cfg.get("image_start_token", patch + 1))
        end = int(llm_cfg.get("image_end_token", patch + 2))
        nl_ids = self.tokenizer.encode("\n", add_special_tokens=False).ids
        prefix_ids = self.tokenizer.encode(_CHAT_PREFIX, add_special_tokens=False).ids
        suffix_ids = self.tokenizer.encode(prompt + _CHAT_SUFFIX, add_special_tokens=False).ids
        ids_list: list[int] = list(prefix_ids)
        for t, gh, gw in ref_grids:
            n_tok = int(t) * (int(gh) // merge) * (int(gw) // merge)
            ids_list.append(start)
            ids_list.extend([patch] * n_tok)
            ids_list.append(end)
            ids_list.extend(nl_ids)
        ids_list.extend(suffix_ids)
        return np.array(ids_list, dtype=np.int64)

    def encode_prompt(self, prompt: str, ref_images: list | None = None) -> tuple[mx.array, mx.array | None]:
        llm_cfg = self.cfg["llm"]
        mlp_cfg = self.cfg["mlp"]
        ref_embeds: mx.array | None = None
        ref_grids: list[tuple[int, int, int]] | None = None
        if ref_images:
            ref_embeds, ref_grids = self.encode_vision(ref_images)

        ids = self._tokenize_prompt(prompt, llm_cfg, ref_grids=ref_grids)
        full, pos3, patch_rows, gen_rows = _build_llm_token_inputs(ids, llm_cfg, mlp_cfg, ref_grids=ref_grids)

        cond_mod = Qwen2Conditioner(int(llm_cfg["hidden_size"]), self.cfg["connector"], mlp_cfg)
        cond_w = mx.load(str(self.root / "cond.safetensors"))
        # Strip __dit_mlp__ if present (unused when use_identity_mlp=True).
        cond_w = {k: v for k, v in cond_w.items() if not k.startswith("__dit_mlp__.")}
        cond_mod.load_weights(list(cond_w.items()), strict=True)
        del cond_w
        mx.eval(cond_mod.parameters())

        n_layers = int(llm_cfg["num_hidden_layers"])
        keep = cond_mod.required_layers(n_layers)

        q_tok = cond_mod.stacked_queries()
        if ref_embeds is not None:
            all_patch_embeds = mx.concatenate([ref_embeds.astype(q_tok.dtype), q_tok], axis=0)
        else:
            all_patch_embeds = q_tok
        mx.eval(all_patch_embeds)

        if self.low_memory in ("llm", "full"):
            # Release conditioner temporarily during LLM pass so peak stays minimal
            del cond_mod, q_tok, ref_embeds
            gc.collect()
            mx.clear_cache()

            hidden = run_llm_streamed(
                llm_cfg,
                self.q_cfg.get("llm"),
                self.store,
                full,
                pos3,
                patch_rows,
                all_patch_embeds,
                keep,
            )
            del all_patch_embeds
            mx.clear_cache()

            cond_mod = Qwen2Conditioner(int(llm_cfg["hidden_size"]), self.cfg["connector"], mlp_cfg)
            cond_w = {k: v for k, v in mx.load(str(self.root / "cond.safetensors")).items() if not k.startswith("__dit_mlp__.")}
            cond_mod.load_weights(list(cond_w.items()), strict=True)
            del cond_w
            cond, cond2 = cond_mod(hidden, n_layers, mx.array(gen_rows), len(ids))
            mx.eval(cond, cond2)
            del hidden, cond_mod
        else:
            llm = BailingTextEncoder(llm_cfg)
            llm_q = self.q_cfg.get("llm")
            if llm_q:
                _quantize_llm_module(llm, int(llm_q["bits"]), int(llm_q["group_size"]))
            lw = self.store.load_all("llm")
            llm.load_weights(list(lw.items()), strict=True)
            del lw
            mx.eval(llm.parameters())

            vocab = int(llm_cfg["vocab_size"])
            emb = llm.word_embeddings(mx.array(np.clip(full, 0, vocab - 1))[None])
            p_cast = all_patch_embeds.astype(emb.dtype)
            rows = mx.array(patch_rows)
            emb = emb.at[0, rows].add(p_cast - emb[0, rows])
            img_mask = mx.array(full == int(llm_cfg["image_patch_token"]))[None, :, None]
            hidden = llm(emb, pos3, img_mask, keep_layers=keep)
            cond, cond2 = cond_mod(hidden, n_layers, mx.array(gen_rows), len(ids))
            mx.eval(cond, cond2)
            del hidden, llm, cond_mod, all_patch_embeds, q_tok, ref_embeds

        gc.collect()
        mx.clear_cache()
        return cond, cond2

    def denoise(
        self,
        cond: mx.array,
        cond2: mx.array | None,
        width: int,
        height: int,
        steps: int,
        seed: int,
        on_step: Callable[[int, int, float], None] | None = None,
        ref_latents: mx.array | None = None,
    ) -> mx.array:
        import time

        h_lat = 2 * (height // 16)
        w_lat = 2 * (width // 16)
        ch = int(self.cfg["transformer"]["in_channels"])
        latents = mx.array(seeded_randn((1, ch, h_lat, w_lat), seed))
        timesteps, sigmas = self.scheduler.build(steps)

        stream_dit = self.low_memory == "full"
        dit = MingDiT(self.cfg["transformer"], self.cfg["mlp"], include_main_layers=not stream_dit)
        if stream_dit:
            w = self.store.load_non_layer_keys("dit")
            dit.load_weights(list(w.items()), strict=True)
            del w
            mx.eval(dit.parameters())
        else:
            w = self.store.load_all("dit")
            dit.load_weights(list(w.items()), strict=True)
            del w
            mx.eval(dit.parameters())

        feats = dit.prepare_cond(cond)
        key = f"{dit.patch}-{dit.fpatch}"
        dtype = dit.all_x_embedder[key].weight.dtype
        ref_typed = ref_latents.astype(dtype) if ref_latents is not None else None
        if self.compile_dit and not stream_dit:
            if ref_typed is not None:
                step_fn = mx.compile(lambda lat, t_n, f1, f2, r_l: dit(lat, t_n, f1, f2, r_l))
            else:
                step_fn = mx.compile(dit)
        else:
            step_fn = dit

        for i, t_val in enumerate(timesteps):
            dt = float(sigmas[i + 1]) - float(sigmas[i])
            if dt == 0.0:
                # Upstream FlowMatchEuler with sigma_min=0 ends on sigma=0 -> sigma=0;
                # multiplying velocity by 0 is an exact no-op.
                if on_step is not None:
                    on_step(i + 1, len(timesteps), 0.0)
                continue
            ts = time.perf_counter()
            t_norm = mx.array(np.array([(np.float32(1000.0) - np.float32(t_val)) / np.float32(1000.0)], dtype=np.float32))
            if stream_dit:
                v = dit.forward_streamed(latents.astype(dtype), t_norm, feats, cond2, self.store, ref_latents=ref_typed)
            elif ref_typed is not None:
                v = step_fn(latents.astype(dtype), t_norm, feats, cond2, ref_typed)
            else:
                v = step_fn(latents.astype(dtype), t_norm, feats, cond2)
            latents = latents + dt * (-v)
            mx.eval(latents)
            if on_step is not None:
                on_step(i + 1, len(timesteps), time.perf_counter() - ts)

        del dit, step_fn, ref_typed
        gc.collect()
        mx.clear_cache()
        return latents

    def decode(self, latents: mx.array):
        from PIL import Image

        vc = self.cfg["vae"]
        vae = RgbaVaeDecoder(vc)
        vw = mx.load(str(self.root / "vae.safetensors"))
        vae.load_weights(list(vw.items()), strict=True)
        del vw
        mx.eval(vae.parameters())

        z = latents.astype(mx.bfloat16)
        z = z / mx.array(float(vc["scaling_factor"]), mx.bfloat16) + mx.array(float(vc["shift_factor"]), mx.bfloat16)
        img = vae.decode(z.transpose(0, 2, 3, 1))
        mx.eval(img)
        arr = np.array(mx.clip(img[0].astype(mx.float32) * 0.5 + 0.5, 0.0, 1.0))
        arr_u8 = (arr * 255.0).round().astype(np.uint8)
        del vae, img, z
        gc.collect()
        mx.clear_cache()
        return Image.fromarray(arr_u8, mode="RGBA" if arr_u8.shape[-1] == 4 else "RGB")
