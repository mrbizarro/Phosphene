#!/usr/bin/env python3
"""The real-audio tokenizer head for the YuE2 MLX engine — a recording in, YuE2
semantic codes out.

YuE2 writes songs from words. It has never been able to *listen*: there was no
way to put a real recording in front of it. Mothersuperior trained the missing
piece — `tokenizer_head_joint_v9`, an 8-layer transformer that reads MERT-v2-
FullSong features and predicts YuE2's own 32768-way semantic codes — and shipped
it beside the acoustic adapter it was trained with (`nar_lora_joint_v9`, already
in Phosphene's pack). This module is that head in MLX. With it a recording can
be re-synthesised through YuE2's decoder, and — the part that matters — used as
the semantic PREFIX a new song continues from.

WHERE THE PREPROCESSING COMES FROM
----------------------------------
Not guessed. Every number below is read out of Mothersuperior's own scripts in
the release repo (`scripts/prep_real.py` for the features, `scripts/joint_v6.py`
for the normalisation, the window and the inference-time tiling):

  audio     ffmpeg/soundfile → mono, 24 kHz, float32
  chunking  30 s chunks (720 000 samples); a chunk shorter than 1 s is dropped;
            each chunk is encoded on its own, so MERT attention never crosses a
            chunk boundary (`prep_real.mert_l20`)
  features  MERT-v2-FullSong `hidden_states[20]`, concatenated in time order,
            then linearly resampled to `round(seconds * 25)` frames and STORED
            AS FLOAT16 — the head was trained on fp16-rounded features, so the
            rounding is kept rather than skipped
  norm      per-TRACK, per-channel instance norm `(x - mean) / (std + 1e-5)`,
            over the whole recording, before any windowing
            (`joint_v6.instnorm`; numpy std, population, not Bessel-corrected)
  window    512 frames with the learned `pos` embedding added
  tiling    hop 256; a final window is pulled back to end-of-track; each
            window contributes its centre, trimming 128 frames off every
            interior edge (`joint_v6.predict`)
  decode    plain argmax over 32768 logits — no neighbour smoothing, even
            though the repo ships the neighbour tables (they are a TRAINING
            loss, `soft_ce`)

WHICH MERT LAYER, EXACTLY
-------------------------
The head's metadata says "layer 20". In transformers, `output_hidden_states`
for MERT2 collects ONLY the conformer block outputs — `_encode()` appends after
each `layer`, and never appends the pre-layer subsampled embedding. So
`hidden_states[20]` is the output of `layers[20]`, the 21st block. `lyra.mert`'s
`layer_weights` vector is one entry LONGER (25 for 24 blocks) because index 0 is
that pre-layer embedding. The one-hot therefore goes at index **21**, not 20.
`MERT_LAYER` below is the transformers index; `_mixer_weights()` does the +1,
and `--mert-layer` moves it for anyone who wants to re-check.

PRECISION
---------
The checkpoint is BF16. Mothersuperior runs the head under `torch.autocast`
bfloat16 and takes `.float().argmax(-1)`. This port promotes the weights to
FP32 and runs the encoder in FP32: the same opmath policy `lyra` uses for the
AR attention (`_source_sdpa`), more accurate than the training-time path, and
reproducible across machines. `--dtype bfloat16` runs the narrow path for
comparison.

WHAT IS OURS AND WHAT IS THE ENGINE'S
-------------------------------------
`lyra` is installed from a pinned SHA by `scripts/pinokio/music_sync.sh`, so
nothing may be written inside `yue2-mlx/`. This module lives in Phosphene's repo
and only *uses* the engine: `lyra.mert.MERT2` for the frontend, `lyra.ar.
_source_sdpa` for attention opmath, `lyra.pipeline.YuE2Pipeline` for synthesis,
and `yue2_lora.py` (ours) to attach `nar_lora_joint_v9`.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import mlx.core as mx
import mlx.nn as nn
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.pinokio.music_lora_fetch import (                   # noqa: E402
    HEAD_SUBDIR, TOKENIZER_HEAD, head_path, sha256_of,
)

__all__ = [
    "HEAD_VERSION", "WINDOW", "HOP", "FRAME_RATE", "VOCAB", "MERT_LAYER",
    "TokenizerHead", "Features", "convert_head_weights", "encode_codes",
    "instance_norm", "load_head", "mert_features", "predict_codes",
    "resample_time", "tile_starts", "code_statistics", "continue_from_codes",
    "log_mel", "spectral_distance", "chroma_correlation", "reference_mono_48k",
]

#: The pinned head this port was written and verified against.
HEAD_VERSION = "tokenizer_head_joint_v9"
HEAD_SHA256 = "cffb673cd663517c8978ba73d186a87517958025f7e409371ea56df3bb364db2"

#: Architecture, from the safetensors header (103 tensors, all BF16).
WINDOW = 512
HOP = WINDOW // 2
TRIM = WINDOW // 4
FEATURE_DIM = 1024
MODEL_DIM = 512
FFN_DIM = 2048
NUM_LAYERS = 8
NUM_HEADS = 8
VOCAB = 32768
LAYER_NORM_EPS = 1e-5

#: Feature extraction, from `prep_real.py`.
MERT_RATE = 24000
MERT_CHUNK = MERT_RATE * 30
MERT_MIN_CHUNK = MERT_RATE
MERT_LAYER = 20                  #: the transformers `hidden_states` index
FRAME_RATE = 25                  #: MERT-v2-FullSong's native frame rate
INSTNORM_EPS = 1e-5
#: The head was trained on features stored as float16; keep the rounding.
FEATURE_STORAGE_DTYPE = np.float16


# --------------------------------------------------------------- the encoder

def _sdpa(query, key, value, *, scale):
    """The engine's own attention opmath where it is importable, MLX's otherwise.

    `lyra.ar._source_sdpa` promotes BF16 operands to FP32 under Lyra's verified
    process policy. In FP32 the promotion is a no-op, so the fallback used by
    the repo-root test suite (which has MLX but not the music engine) computes
    the same values.
    """
    try:
        from lyra.ar import _source_sdpa
    except Exception:                                            # noqa: BLE001
        return mx.fast.scaled_dot_product_attention(query, key, value, scale=scale)
    return _source_sdpa(query, key, value, scale=scale)


class SelfAttention(nn.Module):
    """`torch.nn.MultiheadAttention` with the fused q,k,v projection kept fused.

    PyTorch stores one `in_proj_weight` of `[3*d, d]` whose row blocks are q,
    then k, then v. That order is the thing a port gets silently wrong — the
    shapes are identical under any permutation — so `test_yue2_tokenizer.py`
    pins it against a written-out reference and the release proof reports the
    max-abs-diff against a torch `TransformerEncoderLayer` built from these
    exact tensors.
    """

    def __init__(self, dim=MODEL_DIM, heads=NUM_HEADS):
        super().__init__()
        self.heads = heads
        self.in_proj_weight = mx.zeros((3 * dim, dim))
        self.in_proj_bias = mx.zeros((3 * dim,))
        self.out_proj = nn.Linear(dim, dim)

    def __call__(self, x):
        batch, length, dim = x.shape
        head_dim = dim // self.heads
        fused = x @ self.in_proj_weight.T + self.in_proj_bias
        query, key, value = mx.split(fused, 3, axis=-1)
        shape = (batch, length, self.heads, head_dim)
        query, key, value = (part.reshape(shape).transpose(0, 2, 1, 3)
                             for part in (query, key, value))
        out = _sdpa(query, key, value, scale=head_dim ** -0.5)
        return self.out_proj(out.transpose(0, 2, 1, 3).reshape(batch, length, dim))


class EncoderLayer(nn.Module):
    """`TransformerEncoderLayer(512, 8, 2048, norm_first=True, activation='gelu')`.

    Pre-norm: the residual carries the unnormalised stream, each block reads a
    normalised copy. Dropout was 0.1 in training and is absent at inference.
    """

    def __init__(self):
        super().__init__()
        self.self_attn = SelfAttention()
        self.linear1 = nn.Linear(MODEL_DIM, FFN_DIM)
        self.linear2 = nn.Linear(FFN_DIM, MODEL_DIM)
        self.norm1 = nn.LayerNorm(MODEL_DIM, eps=LAYER_NORM_EPS)
        self.norm2 = nn.LayerNorm(MODEL_DIM, eps=LAYER_NORM_EPS)

    def __call__(self, x):
        x = x + self.self_attn(self.norm1(x))
        return x + self.linear2(nn.gelu(self.linear1(self.norm2(x))))


class Encoder(nn.Module):
    """`nn.TransformerEncoder(layer, 8)` — no final norm of its own."""

    def __init__(self, layers=NUM_LAYERS):
        super().__init__()
        self.layers = [EncoderLayer() for _ in range(layers)]

    def __call__(self, x):
        for layer in self.layers:
            x = layer(x)
        return x


class TokenizerHead(nn.Module):
    """`Tok` from `joint_v6.py`, parameter for parameter.

    `head(norm(enc(inp(x) + pos[:, :T])))`. The learned `pos` is `[1, 512, 512]`
    and is sliced, never interpolated, so a short tail window sees exactly the
    positions it saw in training.
    """

    def __init__(self):
        super().__init__()
        self.inp = nn.Linear(FEATURE_DIM, MODEL_DIM)
        self.pos = mx.zeros((1, WINDOW, MODEL_DIM))
        self.enc = Encoder()
        self.norm = nn.LayerNorm(MODEL_DIM, eps=LAYER_NORM_EPS)
        self.head = nn.Linear(MODEL_DIM, VOCAB)

    def __call__(self, features):
        if features.ndim != 3 or features.shape[-1] != FEATURE_DIM:
            raise ValueError(f"Expected [batch, frames, {FEATURE_DIM}] MERT features")
        if features.shape[1] > WINDOW:
            raise ValueError(f"A window is at most {WINDOW} frames; tile longer inputs")
        x = self.inp(features) + self.pos[:, :features.shape[1]]
        return self.head(self.norm(self.enc(x)))


#: Every tensor the checkpoint carries, mapped to this module's parameter tree.
#: The names already line up (they are PyTorch's), so the map is the identity —
#: which is the point of naming the modules after theirs. Kept explicit so a
#: renamed or reordered release fails loudly instead of loading half a head.
EXPECTED_KEYS = tuple(sorted(
    ["inp.weight", "inp.bias", "pos", "norm.weight", "norm.bias",
     "head.weight", "head.bias"]
    + [f"enc.layers.{i}.{name}"
       for i in range(NUM_LAYERS)
       for name in ("self_attn.in_proj_weight", "self_attn.in_proj_bias",
                    "self_attn.out_proj.weight", "self_attn.out_proj.bias",
                    "linear1.weight", "linear1.bias", "linear2.weight", "linear2.bias",
                    "norm1.weight", "norm1.bias", "norm2.weight", "norm2.bias")]))


def convert_head_weights(weights, dtype=mx.float32):
    """Checkpoint tensors → `(name, array)` pairs, refusing an unexpected tree."""
    names = set(weights)
    missing = sorted(set(EXPECTED_KEYS) - names)
    extra = sorted(names - set(EXPECTED_KEYS))
    if missing or extra:
        raise ValueError(
            f"This is not {HEAD_VERSION}: "
            + (f"missing {len(missing)} tensors ({missing[:3]}…) " if missing else "")
            + (f"unexpected {len(extra)} tensors ({extra[:3]}…)" if extra else ""))
    return [(name, mx.array(weights[name]).astype(dtype)) for name in EXPECTED_KEYS]


def load_head(path, *, dtype=mx.float32, verify_sha=False):
    """Build the MLX head from a safetensors file and evaluate its parameters."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(
            f"No tokenizer head at {path}. Fetch it with "
            f"`python scripts/pinokio/music_lora_fetch.py --root <lora dir>`.")
    digest = sha256_of(path) if verify_sha else None
    if verify_sha and digest != HEAD_SHA256:
        raise ValueError(f"{path.name} is not the pinned {HEAD_VERSION} "
                         f"({digest} != {HEAD_SHA256})")
    # `mx.load` reads BF16 natively; numpy-framework safetensors cannot.
    weights, metadata = mx.load(str(path), return_metadata=True)
    if not isinstance(weights, dict) or not weights:
        raise ValueError(f"{path.name}: no named tensors")
    model = TokenizerHead()
    model.load_weights(convert_head_weights(weights, dtype), strict=True)
    mx.eval(model.parameters())
    object.__setattr__(model, "identity", {
        "head": path.name, "version": HEAD_VERSION, "path": str(path),
        "sha256": digest, "dtype": str(dtype).rsplit(".", 1)[-1],
        "metadata": metadata,
    })
    return model


# ------------------------------------------------------------------- features

@dataclass
class Features:
    """One recording's MERT features, already instance-normalised."""

    values: np.ndarray                 #: [frames, 1024] float32, instance-normed
    seconds: float
    source: str
    mert_layer: int
    resampler: str
    chunks: int

    def summary(self) -> dict:
        return {"frames": int(len(self.values)), "seconds": round(self.seconds, 3),
                "mert_layer": self.mert_layer, "frame_rate": FRAME_RATE,
                "resampler": self.resampler, "chunks": self.chunks}


def resample_time(values: np.ndarray, frames: int) -> np.ndarray:
    """`F.interpolate(mode='linear', align_corners=False)` over the time axis.

    Written out because the whole feature pipeline hangs off it: MERT's native
    rate is already 25 Hz, so this is the identity for any recording whose
    length is a whole number of 960-sample frames, and a one-frame stretch
    otherwise. Getting the half-pixel convention wrong would shift every code.
    """
    source = int(values.shape[0])
    if frames < 1:
        raise ValueError("Need at least one output frame")
    if source == frames:
        return values
    scale = source / frames
    position = np.clip((np.arange(frames, dtype=np.float64) + 0.5) * scale - 0.5, 0.0, None)
    low = np.floor(position).astype(np.int64)
    high = np.minimum(low + 1, source - 1)
    weight = (position - low)[:, None]
    return (values[low] * (1.0 - weight) + values[high] * weight).astype(values.dtype)


def instance_norm(values: np.ndarray) -> np.ndarray:
    """`joint_v6.instnorm`: per-track, per-channel, population standard deviation.

    Per TRACK, not per window — the statistics are taken over the whole
    recording once, before it is cut up. numpy's `std` is ddof=0; torch's
    default is ddof=1, and the original reads the array as numpy.
    """
    values = np.asarray(values, dtype=np.float32)
    if values.ndim != 2:
        raise ValueError("Instance norm expects [frames, channels]")
    return (values - values.mean(0)) / (values.std(0) + INSTNORM_EPS)


def _ffmpeg() -> str:
    for name in ("ffmpeg",):
        found = shutil.which(name)
        if found:
            return found
    raise FileNotFoundError("ffmpeg is not on PATH; it decodes the recording")


def _run_ffmpeg(command, timeout=900) -> bytes:
    done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=timeout)
    if done.returncode:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg failed: {message[-1] if message else done.returncode}")
    return done.stdout


def _probe_rate(path: Path):
    probe = shutil.which("ffprobe")
    if not probe:
        return None
    try:
        done = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a:0", "-show_entries",
             "stream=sample_rate", "-of", "csv=p=0", str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return int(done.stdout.decode().strip()) if not done.returncode else None
    except (ValueError, OSError, subprocess.SubprocessError):
        return None


def load_mono_24k(path, *, start=0.0, seconds=None, resampler="polyphase"):
    """The recording as mono float32 at 24 kHz, plus the resampler actually used.

    `prep_real.py` resampled with `scipy.signal.resample_poly`. That is the
    default here when scipy is importable (it always is in the music engine's
    venv); `ffmpeg` is the fallback and the explicit alternative, and which one
    ran is recorded in the metadata rather than assumed.
    """
    if resampler not in {"polyphase", "ffmpeg"}:
        raise ValueError("resampler must be 'polyphase' or 'ffmpeg'")
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(path)
    trim = ["-ss", f"{float(start):.6f}"] if start else []
    if seconds is not None:
        trim += ["-t", f"{float(seconds):.6f}"]
    native = _probe_rate(path) if resampler == "polyphase" else None
    if resampler == "polyphase" and native:
        try:
            from scipy.signal import resample_poly
        except ImportError:
            native = None
    if resampler == "polyphase" and native:
        raw = _run_ffmpeg([_ffmpeg(), "-v", "error", "-nostdin", *trim, "-i", str(path),
                           "-vn", "-ac", "1", "-ar", str(native), "-f", "f32le", "pipe:1"])
        audio = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        if native != MERT_RATE:
            divisor = math.gcd(native, MERT_RATE)
            audio = resample_poly(audio, MERT_RATE // divisor, native // divisor).astype(np.float32)
        used = f"scipy.resample_poly({native}->{MERT_RATE})"
    else:
        raw = _run_ffmpeg([_ffmpeg(), "-v", "error", "-nostdin", *trim, "-i", str(path),
                           "-vn", "-ac", "1", "-ar", str(MERT_RATE), "-f", "f32le", "pipe:1"])
        audio = np.frombuffer(raw, dtype="<f4").astype(np.float32)
        used = f"ffmpeg(->{MERT_RATE})"
    if len(audio) < MERT_MIN_CHUNK:
        raise ValueError(f"Need at least one second of audio at {MERT_RATE} Hz, "
                         f"got {len(audio) / MERT_RATE:.2f} s")
    if not np.isfinite(audio).all():
        raise ValueError("The decoded recording is not finite")
    return audio, used


def resolve_mert(directory=None, *, offline=False, cache_dir=None) -> Path:
    """The plain MERT-v2-FullSong checkpoint — not SheetSage2's merged copy.

    Phosphene's cover pack already puts the parent on disk at
    `mlx_models/yue2-cover/mert2/`; that is the default. A directory is taken as
    given, the pinned repo is downloaded when nothing is there.
    """
    from lyra.transcription.model import MERT_REPO, MERT_REVISION

    if directory is not None:
        directory = Path(directory).expanduser()
        if not directory.is_dir():
            raise FileNotFoundError(f"No MERT-v2-FullSong checkpoint at {directory}")
        return directory
    root = os.environ.get("LTX_MUSIC_MODELS") or (Path(__file__).resolve().parents[2] / "mlx_models")
    candidate = Path(root).expanduser() / "yue2-cover" / "mert2"
    if (candidate / "model.safetensors").is_file():
        return candidate
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(MERT_REPO, revision=MERT_REVISION, local_files_only=offline,
                                  cache_dir=cache_dir,
                                  allow_patterns=["config.json", "model.safetensors"]))


def load_mert(directory=None, **kwargs):
    """`lyra.mert.MERT2` with the official parent weights, in FP32."""
    from safetensors.numpy import load_file

    from lyra.mert import MERT2, convert_mert_weights

    directory = resolve_mert(directory, **kwargs)
    config = json.loads((directory / "config.json").read_text())
    model = MERT2(config)
    model.load_weights(convert_mert_weights(load_file(directory / "model.safetensors")), strict=True)
    mx.eval(model.parameters())
    object.__setattr__(model, "mert_config", config)
    object.__setattr__(model, "mert_dir", str(directory))
    return model


def _mixer_weights(layers: int, layer: int) -> mx.array:
    """One-hot for `lyra.mert`'s weighted mixer.

    Index 0 of that vector is the pre-layer subsampled embedding; transformers'
    `hidden_states[k]` is the output of block `k`. Hence the +1.
    """
    if not 0 <= layer < layers:
        raise ValueError(f"MERT layer must be in [0, {layers})")
    weights = np.zeros((layers + 1,), dtype=np.float32)
    weights[layer + 1] = 1.0
    return mx.array(weights)


def mert_features(audio, model, *, layer=MERT_LAYER, cancelled=None,
                  progress=None) -> np.ndarray:
    """`prep_real.mert_l20`: 30-second chunks, layer 20, resampled to 25 Hz.

    The chunks are encoded one at a time rather than as one padded batch. Every
    operation in MERT2 is per-sample (global response norm reduces over time
    within a sample, attention never crosses the batch), so this is the same
    arithmetic with bounded memory.
    """
    audio = np.asarray(audio, dtype=np.float32)
    layers = model.mert_config["num_hidden_layers"] if hasattr(model, "mert_config") else len(model.layers)
    mixer = _mixer_weights(layers, layer)
    chunks = [audio[start:start + MERT_CHUNK] for start in range(0, len(audio), MERT_CHUNK)]
    chunks = [chunk for chunk in chunks if len(chunk) >= MERT_MIN_CHUNK]
    if not chunks:
        raise ValueError("Nothing to encode: the recording is shorter than one second")
    parts = []
    for index, chunk in enumerate(chunks):
        if cancelled is not None and cancelled():
            raise InterruptedError("Cancelled during MERT encoding")
        hidden = model(mx.array(chunk[None]), mixer, cancelled)
        mx.eval(hidden)
        parts.append(np.asarray(hidden, dtype=np.float32).reshape(-1, FEATURE_DIM))
        del hidden
        mx.clear_cache()
        if progress is not None:
            progress(index + 1, len(chunks))
    stacked = np.concatenate(parts, axis=0)
    frames = int(round(len(audio) / MERT_RATE * FRAME_RATE))
    return resample_time(stacked, frames).astype(FEATURE_STORAGE_DTYPE), len(chunks)


def extract(path, *, mert=None, mert_dir=None, layer=MERT_LAYER, start=0.0,
            seconds=None, resampler="polyphase", cancelled=None, progress=None) -> Features:
    """A path on disk → instance-normalised features ready for the head."""
    audio, used = load_mono_24k(path, start=start, seconds=seconds, resampler=resampler)
    model = mert if mert is not None else load_mert(mert_dir)
    raw, chunks = mert_features(audio, model, layer=layer, cancelled=cancelled, progress=progress)
    return Features(instance_norm(raw), len(audio) / MERT_RATE, str(Path(path).expanduser()),
                    layer, used, chunks)


# --------------------------------------------------------------- the decoding

def tile_starts(frames: int, window: int = WINDOW, hop: int = HOP) -> list[int]:
    """`joint_v6.predict`'s window starts, including its pull-back final window."""
    if frames < 1:
        raise ValueError("Need at least one frame")
    starts = list(range(0, max(1, frames - window + 1), hop))
    if starts[-1] + window < frames:
        starts.append(max(0, frames - window))
    return starts


def predict_codes(head, features: np.ndarray, *, batch_dtype=mx.float32,
                  cancelled=None, progress=None) -> np.ndarray:
    """Tile, run the head, keep each window's centre — `joint_v6.predict`.

    Interior windows contribute frames `[start+128, start+384)`; the first
    window keeps its head and the last keeps its tail, so every frame is
    written exactly once and no frame is decided by a window edge.
    """
    features = np.asarray(features, dtype=np.float32)
    if features.ndim != 2 or features.shape[1] != FEATURE_DIM:
        raise ValueError(f"Expected [frames, {FEATURE_DIM}] features")
    frames = len(features)
    out = np.zeros(frames, dtype=np.int64)
    starts = tile_starts(frames)
    for index, start in enumerate(starts):
        if cancelled is not None and cancelled():
            raise InterruptedError("Cancelled during tokenisation")
        window = features[start:start + WINDOW]
        length = len(window)
        if length < WINDOW:
            window = np.pad(window, ((0, WINDOW - length), (0, 0)))
        logits = head(mx.array(window[None]).astype(batch_dtype))
        codes = np.asarray(mx.argmax(logits.astype(mx.float32), axis=-1)[0, :length])
        low = start + (0 if start == 0 else TRIM)
        high = start + length - (0 if start + length >= frames else TRIM)
        out[low:high] = codes[low - start:high - start]
        mx.clear_cache()
        if progress is not None:
            progress(index + 1, len(starts))
    if out.min() < 0 or out.max() >= VOCAB:
        raise ValueError("The head produced a code outside YuE2's semantic vocabulary")
    return out


def code_statistics(codes) -> dict:
    """What a token stream looks like, in the numbers `joint_v6` reports itself.

    `repeat` is the share of frames equal to their predecessor and `run_mean`
    the average run length: a head that has collapsed prints repeat near 1.
    """
    codes = np.asarray(codes).astype(np.int64).reshape(-1)
    if not len(codes):
        raise ValueError("No codes")
    values, counts = np.unique(codes, return_counts=True)
    runs = 1 + int((codes[1:] != codes[:-1]).sum()) if len(codes) > 1 else 1
    return {
        "frames": int(len(codes)),
        "seconds": round(len(codes) / FRAME_RATE, 3),
        "unique": int(len(values)),
        "unique_share": round(float(len(values)) / len(codes), 4),
        "top_code": int(values[int(np.argmax(counts))]),
        "top_share": round(float(counts.max()) / len(codes), 4),
        "repeat": round(float((codes[1:] == codes[:-1]).mean()), 4) if len(codes) > 1 else 0.0,
        "runs": runs,
        "run_mean": round(float(len(codes)) / runs, 3),
        "run_max": int(max((len(list(group)) for group in _runs(codes)), default=1)),
    }


def _runs(codes):
    start = 0
    for index in range(1, len(codes) + 1):
        if index == len(codes) or codes[index] != codes[start]:
            yield codes[start:index]
            start = index


def encode_codes(path, *, head=None, head_file=None, mert=None, mert_dir=None,
                 layer=MERT_LAYER, start=0.0, seconds=None, resampler="polyphase",
                 dtype=mx.float32, cancelled=None, on_stage=None):
    """A recording → `(codes, metadata)`. The one call everything else goes through."""
    if on_stage:
        on_stage("features")
    features = extract(path, mert=mert, mert_dir=mert_dir, layer=layer, start=start,
                       seconds=seconds, resampler=resampler, cancelled=cancelled,
                       progress=(lambda k, n: on_stage(f"mert {k}/{n}")) if on_stage else None)
    if mert is None:
        mx.clear_cache()
    if on_stage:
        on_stage("tokenising")
    model = head if head is not None else load_head(head_file, dtype=dtype)
    codes = predict_codes(model, features.values, batch_dtype=dtype, cancelled=cancelled,
                          progress=(lambda k, n: on_stage(f"codes {k}/{n}")) if on_stage else None)
    metadata = {
        "head": getattr(model, "identity", {"version": HEAD_VERSION}),
        "features": features.summary(),
        "source": features.source,
        "start_seconds": float(start),
        "requested_seconds": None if seconds is None else float(seconds),
        "window": WINDOW, "hop": HOP, "trim": TRIM,
        "statistics": code_statistics(codes),
    }
    return codes, metadata


# ------------------------------------------------ synthesis and continuation

def semantic_result(pipe, codes, *, style="", lyrics="", seed=0, cot="off"):
    """Wrap codes as something `pipe.synthesize()` will accept.

    The acoustic stage is conditioned on the request prefix, so a round trip
    still needs one. `cot="off"` is what `prep_real.py` used when it prepared
    real recordings for training, and it is the only mode whose prefix is
    independent of a score: `[EOD] + text + [ABC_START, ABC_END, MUSIC_START]`.
    """
    from yue2.pipeline import SemanticResult, SymbolicPlan
    from yue2.protocol import SongRequest, token_prefixes

    request = SongRequest(style=style, lyrics=lyrics, cot=cot, seed=int(seed), id="realaudio")
    plan = SymbolicPlan(request, None, [], token_prefixes(request, pipe.tokenizer), {}, False)
    return SemanticResult(plan, [int(code) for code in codes], {}, False)


def continue_from_codes(pipe, plan, codes, *, sampling=None, cancelled=None, on_token=None):
    """Generate a song that CONTINUES a real recording.

    The prompt's codes are appended to the request prefix as ordinary codec
    tokens, so the AR model prefills on them exactly as it would on its own
    output, and generation carries on from there. The returned tokens are the
    prompt followed by what the model wrote; the plan keeps its canonical
    prefix, which is what `synthesize()` validates against the request.

    Two honest edges: the repetition-penalty window and the minimum-length
    counter both start empty at the boundary, because `generate_tokens()` takes
    no prior history — the model sees the prompt, the sampler does not.
    """
    from yue2.pipeline import SemanticResult
    from yue2.protocol import CODEC_OFFSET, negative_prefix, resolve_sampling

    codes = [int(code) for code in codes]
    if not codes:
        raise ValueError("An audio prompt needs at least one code")
    if min(codes) < 0 or max(codes) >= VOCAB:
        raise ValueError("Audio-prompt codes are outside YuE2's semantic vocabulary")
    request = plan.request
    sampling = resolve_sampling(sampling, pipe.generation_config.semantic)
    codec = [CODEC_OFFSET + code for code in codes]
    negative = None
    if request.guidance != 1:
        negative = negative_prefix(request, pipe.tokenizer, plan.abc_ids) + codec
    ids, timing, truncated = pipe._generate(
        list(plan.prefix) + codec, sampling, request.seed, "semantic",
        negative=negative, cfg_scale=request.guidance, legacy_off=request.cot == "off",
        cancelled=cancelled, on_token=on_token)
    tokens = codes + [int(token) - CODEC_OFFSET for token in ids]
    return SemanticResult(plan, tokens, {**timing, "audio_prompt_frames": len(codes)}, truncated)


def build_pipeline(model_dir, vae_dir, *, precision="bf16", steps=32,
                   memory_budget_gib=None, lora=(), lora_mode="joint"):
    """The engine pipeline plus any adapters, ready for `synthesize`/`decode`."""
    from yue2.protocol import GenerationConfig

    from lyra.pipeline import YuE2Pipeline

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import yue2_lora

    adapters = yue2_lora.load_stack([(Path(path), 1.0) for path in lora], mode=lora_mode) if lora else []
    config = GenerationConfig(ode_steps=int(steps))
    kwargs = {} if memory_budget_gib is None else {"memory_budget_gib": float(memory_budget_gib)}
    pipe = YuE2Pipeline(Path(model_dir), Path(vae_dir), precision=precision,
                        generation_config=config, progress=False, **kwargs)
    runtime = yue2_lora.LoRARuntime(adapters)
    runtime.attach(pipe)
    return pipe, runtime, adapters


def synthesize_codes(pipe, codes, *, style="", lyrics="", seed=0, cancelled=None):
    """Codes → 48 kHz stereo float32, through the NAR and the VAE decoder."""
    from lyra.pipeline import initial_noise

    semantic = semantic_result(pipe, codes, style=style, lyrics=lyrics, seed=seed)
    noise = initial_noise(len(semantic.tokens), int(seed))
    latents = pipe.synthesize(semantic, noise=noise, cancelled=cancelled)
    return pipe.decode(latents, cancelled=cancelled), semantic


# -------------------------------------------------------------- measurements

def log_mel(audio, rate=48000, n_fft=2048, hop=480, mels=128):
    """A small log-mel magnitude spectrogram — numpy only, no torchaudio.

    Slaney-style mel scale on a Hann-windowed magnitude STFT, the shape
    `joint_v6.spec_loss` measures with. It exists so the round-trip proof can
    be reported in numbers rather than adjectives.
    """
    audio = np.ascontiguousarray(np.asarray(audio, dtype=np.float64).reshape(-1))
    if not np.isfinite(audio).all():
        raise ValueError("Cannot measure a recording that is not finite")
    window = np.hanning(n_fft + 1)[:-1]
    frames = 1 + max(0, (len(audio) - n_fft) // hop)
    if frames < 1:
        raise ValueError("Recording is shorter than one STFT frame")
    strided = np.lib.stride_tricks.as_strided(
        audio, (frames, n_fft), (audio.strides[0] * hop, audio.strides[0]))
    spectrum = np.abs(np.fft.rfft(strided * window, axis=-1))
    hz = np.fft.rfftfreq(n_fft, 1.0 / rate)
    mel = lambda f: 2595.0 * np.log10(1.0 + f / 700.0)           # noqa: E731
    edges = np.linspace(mel(0.0), mel(rate / 2), mels + 2)
    centres = 700.0 * (10.0 ** (edges / 2595.0) - 1.0)
    bank = np.zeros((len(hz), mels))
    for index in range(mels):
        left, centre, right = centres[index:index + 3]
        rising = (hz - left) / max(centre - left, 1e-9)
        falling = (right - hz) / max(right - centre, 1e-9)
        bank[:, index] = np.clip(np.minimum(rising, falling), 0.0, None)
    # Accelerate leaves FP status flags set after a large GEMM, so numpy raises
    # divide/overflow/invalid warnings for a product that is finite and correct
    # (the reference measured against itself returns exactly 0.0 dB).
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        energy = spectrum @ bank
    return np.log(energy + 1e-5)


def spectral_distance(reference, estimate, rate=48000) -> float:
    """Mean |Δ log-mel| in dB between two recordings, on their common length."""
    length = min(len(reference), len(estimate))
    a, b = log_mel(reference[:length], rate), log_mel(estimate[:length], rate)
    frames = min(len(a), len(b))
    return float((20.0 / math.log(10)) * np.abs(a[:frames] - b[:frames]).mean())


def chroma(audio, rate=48000, n_fft=4096, hop=2048) -> np.ndarray:
    """A 12-bin chroma sequence: which pitch classes are sounding, over time."""
    audio = np.ascontiguousarray(np.asarray(audio, dtype=np.float64).reshape(-1))
    window = np.hanning(n_fft + 1)[:-1]
    frames = 1 + max(0, (len(audio) - n_fft) // hop)
    strided = np.lib.stride_tricks.as_strided(
        audio, (frames, n_fft), (audio.strides[0] * hop, audio.strides[0]))
    spectrum = np.abs(np.fft.rfft(strided * window, axis=-1))
    hz = np.fft.rfftfreq(n_fft, 1.0 / rate)
    usable = (hz >= 55.0) & (hz <= 4186.0)
    pitch = np.zeros(len(hz), dtype=np.int64)
    pitch[usable] = np.round(12 * np.log2(hz[usable] / 440.0) + 69).astype(np.int64) % 12
    bins = np.zeros((frames, 12))
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        for index in range(12):
            mask = usable & (pitch == index)
            if mask.any():
                bins[:, index] = spectrum[:, mask].sum(axis=1)
    return bins / (bins.sum(axis=1, keepdims=True) + 1e-9)


def chroma_correlation(reference, estimate, rate=48000) -> float:
    """Pearson correlation of the two mean chroma profiles: same notes or not."""
    a, b = chroma(reference, rate).mean(0), chroma(estimate, rate).mean(0)
    a, b = a - a.mean(), b - b.mean()
    return float((a @ b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12))


# ---------------------------------------------------------------------- CLI

def say(*parts):
    print("[tokenizer]", *parts, flush=True)


def _default_head() -> Path:
    root = os.environ.get("LTX_MUSIC_LORAS")
    if not root:
        root = Path(__file__).resolve().parents[2] / "mlx_models" / "yue2-loras"
    return head_path(root)


def reference_mono_48k(path, *, start=0.0, seconds=None) -> np.ndarray:
    """The measured excerpt of the ORIGINAL recording, mono, 48 kHz.

    It must be the same excerpt the codes came from: measuring a round trip of
    seconds 60–90 against the record from its first second is the kind of
    mistake that reads as a quiet, plausible number instead of an error.
    """
    trim = ["-ss", f"{float(start):.6f}"] if start else []
    if seconds is not None:
        trim += ["-t", f"{float(seconds):.6f}"]
    raw = _run_ffmpeg([_ffmpeg(), "-v", "error", "-nostdin", *trim, "-i", str(Path(path).expanduser()),
                       "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "pipe:1"])
    return np.frombuffer(raw, dtype="<f4").astype(np.float32)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    encode = sub.add_parser("encode", help="a recording → YuE2 semantic codes")
    encode.add_argument("audio", type=Path)
    encode.add_argument("--out", type=Path, required=True, help="codes .npy (int32)")
    encode.add_argument("--head", type=Path, default=None,
                        help=f"default: <lora pack>/{HEAD_SUBDIR}/{TOKENIZER_HEAD}")
    encode.add_argument("--mert-dir", type=Path, default=None,
                        help="MERT-v2-FullSong checkpoint (default: the cover pack's)")
    encode.add_argument("--mert-layer", type=int, default=MERT_LAYER,
                        help="transformers hidden_states index (default 20)")
    encode.add_argument("--start", type=float, default=0.0)
    encode.add_argument("--seconds", type=float, default=None)
    encode.add_argument("--resampler", choices=("polyphase", "ffmpeg"), default="polyphase")
    encode.add_argument("--dtype", choices=("float32", "bfloat16"), default="float32")
    encode.add_argument("--verify-sha", action="store_true")
    encode.add_argument("--meta", type=Path, default=None, help="default: <out>.json")
    encode.add_argument("--features-out", type=Path, default=None,
                        help="also save the instance-normed MERT features")
    # --- the round trip ---
    encode.add_argument("--roundtrip", type=Path, default=None,
                        help="also synthesise the codes back to audio (.wav/.flac)")
    encode.add_argument("--model-dir", type=Path, default=None)
    encode.add_argument("--vae-dir", type=Path, default=None)
    encode.add_argument("--lora", action="append", default=[],
                        help="an adapter for the acoustic branch, e.g. nar_lora_joint_v9")
    encode.add_argument("--lora-mode", choices=("joint", "separate"), default="joint")
    encode.add_argument("--steps", type=int, default=32)
    encode.add_argument("--seed", type=int, default=0)
    encode.add_argument("--precision", choices=("bf16", "8bit", "4bit"), default="bf16")
    encode.add_argument("--style", default="")
    encode.add_argument("--lyrics", default="")
    encode.add_argument("--memory-budget-gib", type=float, default=None)

    args = parser.parse_args(argv)
    os.environ.setdefault("MLX_ENABLE_TF32", "0")
    os.environ.pop("PYTORCH_ENABLE_MPS_FALLBACK", None)
    os.environ.pop("PYTORCH_MPS_FAST_MATH", None)

    if args.roundtrip is not None and (args.model_dir is None or args.vae_dir is None):
        say("error", "--roundtrip needs --model-dir and --vae-dir")
        return 2
    started = time.perf_counter()
    dtype = mx.float32 if args.dtype == "float32" else mx.bfloat16
    head_file = args.head or _default_head()
    try:
        head = load_head(head_file, dtype=dtype, verify_sha=args.verify_sha)
        say("stage", f"head {Path(head_file).name} · {args.dtype}")
        features = extract(args.audio, mert_dir=args.mert_dir, layer=args.mert_layer,
                           start=args.start, seconds=args.seconds, resampler=args.resampler,
                           progress=lambda k, n: say("mert", f"{k}/{n}"))
        mx.clear_cache()
        say("stage", f"features {len(features.values)} frames · {features.resampler}")
        codes = predict_codes(head, features.values, batch_dtype=dtype,
                              progress=lambda k, n: say("codes", f"{k}/{n}"))
    except (FileNotFoundError, ValueError, RuntimeError) as error:
        say("error", f"{type(error).__name__}: {error}")
        return 2
    args.out.parent.mkdir(parents=True, exist_ok=True)
    np.save(args.out, codes.astype(np.int32), allow_pickle=False)
    statistics = code_statistics(codes)
    metadata = {
        "head": head.identity, "features": features.summary(), "source": features.source,
        "start_seconds": args.start, "requested_seconds": args.seconds,
        "window": WINDOW, "hop": HOP, "trim": TRIM, "statistics": statistics,
        "codes": str(args.out), "encode_seconds": round(time.perf_counter() - started, 2),
    }
    say("codes", f"{statistics['frames']} frames · {statistics['seconds']:.2f} s · "
                 f"{statistics['unique']} unique · top {statistics['top_share'] * 100:.1f}% · "
                 f"repeat {statistics['repeat']:.3f} · run mean {statistics['run_mean']:.2f}")
    if args.features_out is not None:
        np.save(args.features_out, features.values.astype(np.float32), allow_pickle=False)

    if args.roundtrip is not None:
        del head
        mx.clear_cache()
        say("stage", "synthesising")
        pipe, runtime, adapters = build_pipeline(
            args.model_dir, args.vae_dir, precision=args.precision, steps=args.steps,
            memory_budget_gib=args.memory_budget_gib, lora=args.lora, lora_mode=args.lora_mode)
        for adapter in adapters:
            say("stage", f"lora {adapter.name} · {adapter.branch} · {args.lora_mode}")
        synth_started = time.perf_counter()
        with pipe:
            audio, semantic = synthesize_codes(pipe, codes, style=args.style,
                                               lyrics=args.lyrics, seed=args.seed)
        import soundfile as sf

        args.roundtrip.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(args.roundtrip), audio, 48000, subtype="PCM_24",
                 format="WAV" if args.roundtrip.suffix.lower() == ".wav" else "FLAC")
        metadata["roundtrip"] = {
            "audio": str(args.roundtrip), "seconds": round(len(audio) / 48000, 3),
            "lora": [adapter.summary() for adapter in adapters], "lora_mode": args.lora_mode,
            "steps": args.steps, "seed": args.seed, "precision": args.precision,
            "style": args.style, "lyrics": args.lyrics,
            "synthesis_seconds": round(time.perf_counter() - synth_started, 2),
            "mlx_peak_gib": round(mx.get_peak_memory() / 2 ** 30, 2),
        }
        runtime.detach()
        try:
            reference = reference_mono_48k(args.audio, start=args.start, seconds=args.seconds)
            estimate = audio.mean(axis=1) if audio.ndim == 2 else audio
            metadata["roundtrip"]["log_mel_db"] = round(spectral_distance(reference, estimate), 3)
            metadata["roundtrip"]["chroma_correlation"] = round(
                chroma_correlation(reference, estimate), 4)
            metadata["roundtrip"]["reference_seconds"] = round(len(reference) / 48000, 3)
        except Exception as error:                               # noqa: BLE001
            metadata["roundtrip"]["measurement_error"] = f"{type(error).__name__}: {error}"
        say("roundtrip", f"{metadata['roundtrip']['seconds']:.2f} s → {args.roundtrip}")

    meta_path = args.meta or args.out.with_name(args.out.name + ".json")
    meta_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")
    say("done", f"{time.perf_counter() - started:.2f}", str(args.out))
    return 0


if __name__ == "__main__":                                       # pragma: no cover
    sys.exit(main())
