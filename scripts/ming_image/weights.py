"""Lazy safetensors shard reader supporting per-layer streaming into MLX.

Reading individual tensors by exact byte offset via ``os.pread`` lets 16 GB and
24 GB Macs stream one BailingMoeV2 layer (~0.85 GB) or one DiT block (~0.41 GB)
at a time without ever materializing all 17.3 GB / 12.3 GB shards in memory.
"""
from __future__ import annotations

import json
import os
import struct
from pathlib import Path
from typing import Iterable

import mlx.core as mx
import numpy as np

_ST_DTYPE_INFO: dict[str, tuple[np.dtype, mx.Dtype | None]] = {
    "BF16": (np.dtype("<u2"), mx.bfloat16),
    "F16": (np.dtype("<f2"), mx.float16),
    "F32": (np.dtype("<f4"), mx.float32),
    "F64": (np.dtype("<f8"), mx.float32),
    "U32": (np.dtype("<u4"), mx.uint32),
    "I32": (np.dtype("<i4"), mx.int32),
    "I64": (np.dtype("<i8"), mx.int64),
    "U8": (np.dtype("<u1"), mx.uint8),
    "I8": (np.dtype("<i1"), mx.int8),
    "BOOL": (np.dtype("?"), mx.bool_),
}


class SafetensorsShard:
    """Header-indexed handle to a single safetensors file using ``os.pread``."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fd = os.open(str(self.path), os.O_RDONLY)
        try:
            hdr_len_raw = os.pread(self._fd, 8, 0)
            if len(hdr_len_raw) != 8:
                raise ValueError(f"truncated safetensors header in {self.path}")
            (hdr_len,) = struct.unpack("<Q", hdr_len_raw)
            hdr_raw = os.pread(self._fd, hdr_len, 8)
            if len(hdr_raw) != hdr_len:
                raise ValueError(f"truncated safetensors metadata in {self.path}")
            self._data_offset = 8 + hdr_len
            meta = json.loads(hdr_raw.decode("utf-8"))
            meta.pop("__metadata__", None)
            self.tensors: dict[str, dict] = meta
        except Exception:
            os.close(self._fd)
            self._fd = -1
            raise

    def close(self) -> None:
        if self._fd >= 0:
            os.close(self._fd)
            self._fd = -1

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def keys(self) -> Iterable[str]:
        return self.tensors.keys()

    def get(self, key: str) -> mx.array:
        info = self.tensors[key]
        st_dtype = info["dtype"]
        shape = tuple(int(d) for d in info["shape"])
        start, end = info["data_offsets"]
        nbytes = int(end) - int(start)
        raw = os.pread(self._fd, nbytes, self._data_offset + int(start))
        if len(raw) != nbytes:
            raise IOError(f"short read for {key} in {self.path}: {len(raw)} != {nbytes}")
        np_dt, mx_dt = _ST_DTYPE_INFO[st_dtype]
        arr_np = np.frombuffer(raw, dtype=np_dt).reshape(shape)
        if st_dtype == "BF16":
            return mx.array(arr_np).view(mx.bfloat16)
        if st_dtype == "F64":
            return mx.array(arr_np.astype(np.float32))
        arr = mx.array(arr_np)
        return arr if mx_dt is None or arr.dtype == mx_dt else arr.astype(mx_dt)


class ShardStore:
    """Indexed access across all shards of a Ming-Image MLX checkpoint."""

    def __init__(self, root: Path, index: dict[str, str]) -> None:
        self.root = Path(root)
        self.index = dict(index)
        self._shards: dict[str, SafetensorsShard] = {}

    def _shard(self, fname: str) -> SafetensorsShard:
        sh = self._shards.get(fname)
        if sh is None:
            sh = SafetensorsShard(self.root / fname)
            self._shards[fname] = sh
        return sh

    def close(self) -> None:
        for sh in self._shards.values():
            sh.close()
        self._shards.clear()

    def files_with_prefix(self, file_prefix: str) -> list[str]:
        return sorted({f for f in self.index.values() if f.startswith(file_prefix)})

    def load_all(self, file_prefix: str) -> dict[str, mx.array]:
        """Load every tensor whose shard filename starts with ``file_prefix``."""
        out: dict[str, mx.array] = {}
        for fname in self.files_with_prefix(file_prefix):
            out.update(mx.load(str(self.root / fname)))
        return out

    def load_keys_matching(
        self,
        file_prefix: str,
        key_prefix: str,
        strip_prefix: str = "",
    ) -> dict[str, mx.array]:
        """Read only the tensors matching ``key_prefix`` from ``file_prefix`` shards."""
        out: dict[str, mx.array] = {}
        for key, fname in self.index.items():
            if not fname.startswith(file_prefix):
                continue
            if not key.startswith(key_prefix):
                continue
            sh = self._shard(fname)
            out_key = key[len(strip_prefix):] if strip_prefix and key.startswith(strip_prefix) else key
            out[out_key] = sh.get(key)
        return out

    def load_non_layer_keys(self, file_prefix: str) -> dict[str, mx.array]:
        """Load tensors in ``file_prefix`` shards that do not belong to ``layers.<n>.``."""
        out: dict[str, mx.array] = {}
        for key, fname in self.index.items():
            if not fname.startswith(file_prefix):
                continue
            if key.startswith("layers."):
                continue
            out[key] = self._shard(fname).get(key)
        return out
