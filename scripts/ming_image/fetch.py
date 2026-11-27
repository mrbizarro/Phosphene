#!/usr/bin/env python3
"""Pinned, resumable, SHA-256-verified downloader for Ming-Image-0.2-Design MLX weights.

Fetches:
  1. The 18 pinned MLX base files directly from amarck/Ming-Image-0.2-Design-MLX (32.36 GB).
  2. Only the 3 contiguous byte ranges (~1.56 GB) for Qwen2.5-ViT, linear_proj, and the
     4-channel RGBA VAE encoder from upstream inclusionAI/Ming-Image-0.2-Design at a
     pinned revision, verified by header + slice SHA-256 and packed locally into
     deterministic `vision.safetensors` and `vae_encoder.safetensors` (never re-hosted).

Uses automatic China mirror fallback via scripts/hf_source.py when huggingface.co
cannot be reached.

Progress protocol (stdout, flushed):
    [ming-image] [i/N] cached <path>
    [ming-image] [i/N] fetching <path> (<MB> MB)
    [ming-image] [i/N] verified <path>
    [ming-image] ready <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import struct
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np

from . import pins

CHUNK_BYTES = 4 * 1024 * 1024


def _ensure_ssl_certs() -> None:
    if not os.environ.get("SSL_CERT_FILE"):
        try:
            import certifi  # noqa: PLC0415
            os.environ["SSL_CERT_FILE"] = certifi.where()
        except Exception:
            pass


def _hf_urlopen(req: urllib.request.Request, timeout: float = 60.0):
    """Route pinned Hugging Face fetches through scripts/hf_source.py when available."""
    _ensure_ssl_certs()
    try:
        hs = importlib.import_module("scripts.hf_source")
    except ImportError:
        scripts_dir = str(Path(__file__).resolve().parent.parent)
        if scripts_dir not in sys.path:
            sys.path.append(scripts_dir)
        try:
            hs = importlib.import_module("hf_source")
        except ImportError:
            return urllib.request.urlopen(req, timeout=timeout)
    return hs.urlopen(req, timeout)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(CHUNK_BYTES)
            if not buf:
                break
            digest.update(buf)
    return digest.hexdigest()


def verify_directory(target_dir: Path, check_sha: bool = False, include_edit: bool = True) -> list[str]:
    """Return a list of missing or mismatched pinned relative paths."""
    root = Path(target_dir)
    entries = list(pins.PINNED_FILES) + (list(pins.EDIT_OUTPUT_FILES) if include_edit else [])
    bad: list[str] = []
    for entry in entries:
        rel = str(entry["path"])
        want_size = int(entry["bytes"])
        p = root / rel
        try:
            st = p.stat()
        except OSError:
            bad.append(rel)
            continue
        if st.st_size != want_size:
            bad.append(rel)
            continue
        if check_sha and _sha256_file(p) != str(entry["sha256"]):
            bad.append(rel)
    return bad


def verify_edit_weights(target_dir: Path, check_sha: bool = False) -> list[str]:
    """Return missing or mismatched edit weight files (vision.safetensors, vae_encoder.safetensors)."""
    root = Path(target_dir)
    bad: list[str] = []
    for entry in pins.EDIT_OUTPUT_FILES:
        rel = str(entry["path"])
        want_size = int(entry["bytes"])
        p = root / rel
        try:
            st = p.stat()
        except OSError:
            bad.append(rel)
            continue
        if st.st_size != want_size:
            bad.append(rel)
            continue
        if check_sha and _sha256_file(p) != str(entry["sha256"]):
            bad.append(rel)
    return bad


def _fetch_upstream_slice(
    spec: dict[str, object],
    part_path: Path,
) -> tuple[int, dict[str, dict]]:
    """Range-fetch and SHA-256 verify one upstream safetensors header + tensor byte span."""
    url = pins.upstream_file_url(str(spec["remote_path"]))
    want_hlen = int(spec["header_len"])
    want_hsha = str(spec["header_sha256"])
    start = int(spec["data_start"])
    end = int(spec["data_end"])
    need = int(spec["data_bytes"])
    want_dsha = str(spec["data_sha256"])

    last_err: Exception | None = None
    header_dict: dict[str, dict] | None = None
    for attempt in range(1, 6):
        try:
            if header_dict is None:
                hreq = urllib.request.Request(
                    url,
                    headers={"Range": f"bytes=0-{8 + want_hlen - 1}", "User-Agent": "Phosphene"},
                )
                with _hf_urlopen(hreq, timeout=60.0) as resp:
                    hblob = resp.read()
                if len(hblob) != 8 + want_hlen:
                    raise IOError(f"short header for {spec['remote_path']}: {len(hblob)} != {8 + want_hlen}")
                (hlen,) = struct.unpack("<Q", hblob[:8])
                hraw = hblob[8:]
                hsha = hashlib.sha256(hraw).hexdigest()
                if hlen != want_hlen or hsha != want_hsha:
                    raise ValueError(f"header SHA-256 mismatch for {spec['remote_path']}: {hsha} != {want_hsha}")
                hdr = json.loads(hraw.decode("utf-8"))
                hdr.pop("__metadata__", None)
                header_dict = hdr

            have = part_path.stat().st_size if part_path.is_file() else 0
            if have > need:
                part_path.unlink()
                have = 0
            if have < need:
                dreq = urllib.request.Request(
                    url,
                    headers={"Range": f"bytes={start + have}-{end - 1}", "User-Agent": "Phosphene"},
                )
                with _hf_urlopen(dreq, timeout=120.0) as resp:
                    status = getattr(resp, "status", 200)
                    if status != 206:
                        raise IOError(f"server ignored Range request for {spec['remote_path']} (HTTP {status})")
                    mode = "ab" if have > 0 else "wb"
                    with part_path.open(mode) as out_f:
                        while True:
                            chunk = resp.read(CHUNK_BYTES)
                            if not chunk:
                                break
                            out_f.write(chunk)
            if part_path.stat().st_size != need:
                raise IOError(f"short range slice for {spec['tag']}: {part_path.stat().st_size} != {need}")
            got_sha = _sha256_file(part_path)
            if got_sha != want_dsha:
                part_path.unlink()
                raise ValueError(f"SHA-256 mismatch for upstream slice {spec['tag']}: {got_sha} != {want_dsha}")
            return 8 + want_hlen, header_dict
        except Exception as exc:
            last_err = exc
            if attempt < 5:
                time.sleep(min(15.0, 2.0 * attempt))
    raise RuntimeError(f"Failed to range-fetch {spec['remote_path']}: {last_err}") from last_err


def _pack_vision_safetensors(
    target_dir: Path,
    vis_spec: dict[str, object],
    vis_part: Path,
    vis_meta: tuple[int, dict[str, dict]],
    lp_spec: dict[str, object],
    lp_part: Path,
    lp_meta: tuple[int, dict[str, dict]],
) -> None:
    """Deterministically pack verified vision.* + linear_proj.* slices into vision.safetensors."""
    want_entry = next(e for e in pins.EDIT_OUTPUT_FILES if e["path"] == "vision.safetensors")
    entries: list[tuple[str, str, list[int], Path, int, int]] = []
    for spec, part, (data_base, hdr) in ((vis_spec, vis_part, vis_meta), (lp_spec, lp_part, lp_meta)):
        pref = str(spec["key_prefix"])
        slice_start = int(spec["data_start"])
        for k in sorted(hdr):
            if k.startswith(pref):
                info = hdr[k]
                s0, s1 = int(info["data_offsets"][0]), int(info["data_offsets"][1])
                entries.append((k, str(info["dtype"]), list(info["shape"]), part, (data_base + s0) - slice_start, s1 - s0))
    entries.sort(key=lambda x: x[0])

    out_hdr: dict[str, dict] = {}
    cursor = 0
    for k, dt, shp, _, _, nbytes in entries:
        out_hdr[k] = {"dtype": dt, "shape": shp, "data_offsets": [cursor, cursor + nbytes]}
        cursor += nbytes
    hdr_bytes = json.dumps(out_hdr, separators=(",", ":")).encode("utf-8")
    hdr_bytes += b" " * ((-len(hdr_bytes)) % 8)

    dest = target_dir / "vision.safetensors"
    tmp = dest.with_name(dest.name + ".tmp")
    dig = hashlib.sha256()
    fds = {vis_part: os.open(str(vis_part), os.O_RDONLY), lp_part: os.open(str(lp_part), os.O_RDONLY)}
    try:
        with tmp.open("wb") as out_f:
            prefix = struct.pack("<Q", len(hdr_bytes)) + hdr_bytes
            out_f.write(prefix)
            dig.update(prefix)
            for _, _, _, part, rel_off, nbytes in entries:
                fd = fds[part]
                rem = nbytes
                off = rel_off
                while rem > 0:
                    buf = os.pread(fd, min(CHUNK_BYTES, rem), off)
                    if not buf:
                        raise IOError("unexpected EOF packing vision.safetensors")
                    out_f.write(buf)
                    dig.update(buf)
                    off += len(buf)
                    rem -= len(buf)
    finally:
        for fd in fds.values():
            os.close(fd)

    got_sha = dig.hexdigest()
    got_size = tmp.stat().st_size
    if got_size != int(want_entry["bytes"]) or got_sha != str(want_entry["sha256"]):
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"vision.safetensors digest mismatch: size={got_size} sha={got_sha} "
            f"(expected {want_entry['bytes']} / {want_entry['sha256']})"
        )
    os.replace(tmp, dest)


def _pack_vae_encoder_safetensors(
    target_dir: Path,
    vae_spec: dict[str, object],
    vae_part: Path,
    vae_meta: tuple[int, dict[str, dict]],
) -> None:
    """Collapse single-frame causal 3D RGBA VAE encoder weights to 2D NHWC BF16 safetensors."""
    want_entry = next(e for e in pins.EDIT_OUTPUT_FILES if e["path"] == "vae_encoder.safetensors")
    data_base, hdr = vae_meta
    slice_start = int(vae_spec["data_start"])
    fd = os.open(str(vae_part), os.O_RDONLY)
    tensors: dict[str, np.ndarray] = {}
    try:
        for k in sorted(hdr):
            if not (k.startswith("encoder.") or k.startswith("quant_conv.")):
                continue
            if ".time_conv." in k:
                continue
            info = hdr[k]
            if info["dtype"] != "BF16":
                raise ValueError(f"unexpected VAE encoder dtype for {k}: {info['dtype']}")
            s0, s1 = int(info["data_offsets"][0]), int(info["data_offsets"][1])
            raw = os.pread(fd, s1 - s0, (data_base + s0) - slice_start)
            arr = np.frombuffer(raw, dtype=np.uint16).reshape(info["shape"])
            nk = k.replace("encoder.mid_block.resnets.0.", "encoder.mid_res0.")
            nk = nk.replace("encoder.mid_block.resnets.1.", "encoder.mid_res1.")
            nk = nk.replace("encoder.mid_block.attentions.0.", "encoder.mid_attn.")
            if ".resample.1." in nk:
                nk = nk.replace(".resample.1.", ".conv.")
            if nk.endswith(".gamma"):
                arr = np.ascontiguousarray(arr.reshape(-1))
            elif nk.endswith(".weight") and arr.ndim == 5:
                arr = np.ascontiguousarray(arr[:, :, -1, :, :].transpose(0, 2, 3, 1))
            elif nk.endswith(".weight") and arr.ndim == 4:
                arr = np.ascontiguousarray(arr.transpose(0, 2, 3, 1))
            else:
                arr = np.ascontiguousarray(arr)
            tensors[nk] = arr
    finally:
        os.close(fd)

    out_hdr: dict[str, dict] = {}
    cursor = 0
    for k in sorted(tensors):
        arr = tensors[k]
        out_hdr[k] = {"dtype": "BF16", "shape": list(arr.shape), "data_offsets": [cursor, cursor + arr.nbytes]}
        cursor += arr.nbytes
    hdr_bytes = json.dumps(out_hdr, separators=(",", ":")).encode("utf-8")
    hdr_bytes += b" " * ((-len(hdr_bytes)) % 8)

    dest = target_dir / "vae_encoder.safetensors"
    tmp = dest.with_name(dest.name + ".tmp")
    dig = hashlib.sha256()
    with tmp.open("wb") as out_f:
        prefix = struct.pack("<Q", len(hdr_bytes)) + hdr_bytes
        out_f.write(prefix)
        dig.update(prefix)
        for k in sorted(tensors):
            raw = tensors[k].tobytes()
            out_f.write(raw)
            dig.update(raw)

    got_sha = dig.hexdigest()
    got_size = tmp.stat().st_size
    if got_size != int(want_entry["bytes"]) or got_sha != str(want_entry["sha256"]):
        tmp.unlink(missing_ok=True)
        raise ValueError(
            f"vae_encoder.safetensors digest mismatch: size={got_size} sha={got_sha} "
            f"(expected {want_entry['bytes']} / {want_entry['sha256']})"
        )
    os.replace(tmp, dest)


def fetch_edit_weights(
    target_dir: Path,
    progress=None,
    idx_offset: int = 0,
    total_files: int | None = None,
) -> Path:
    """Range-fetch and pack vision.safetensors and vae_encoder.safetensors into ``target_dir``."""
    root = Path(target_dir)
    root.mkdir(parents=True, exist_ok=True)
    total = total_files if total_files is not None else len(pins.EDIT_OUTPUT_FILES)

    def emit(msg: str) -> None:
        if progress is not None:
            progress(msg)
        else:
            print(f"[ming-image] {msg}", flush=True)

    slices_by_tag = {str(s["tag"]): s for s in pins.UPSTREAM_EDIT_SLICES}

    # 1. vision.safetensors (vision.* + linear_proj.*)
    vis_entry = next(e for e in pins.EDIT_OUTPUT_FILES if e["path"] == "vision.safetensors")
    vis_dest = root / "vision.safetensors"
    vis_idx = idx_offset + 1
    if vis_dest.is_file() and vis_dest.stat().st_size == int(vis_entry["bytes"]) and _sha256_file(vis_dest) == str(vis_entry["sha256"]):
        emit(f"[{vis_idx}/{total}] cached vision.safetensors")
    else:
        if vis_dest.exists():
            vis_dest.unlink()
        vis_spec = slices_by_tag["vision_raw"]
        lp_spec = slices_by_tag["linproj_raw"]
        mb = (int(vis_spec["data_bytes"]) + int(lp_spec["data_bytes"])) / 1e6
        emit(f"[{vis_idx}/{total}] fetching vision.safetensors ({mb:.1f} MB range slice)")
        vis_part = root / ".vision_raw.part"
        lp_part = root / ".linproj_raw.part"
        vis_meta = _fetch_upstream_slice(vis_spec, vis_part)
        lp_meta = _fetch_upstream_slice(lp_spec, lp_part)
        _pack_vision_safetensors(root, vis_spec, vis_part, vis_meta, lp_spec, lp_part, lp_meta)
        vis_part.unlink(missing_ok=True)
        lp_part.unlink(missing_ok=True)
        emit(f"[{vis_idx}/{total}] verified vision.safetensors")

    # 2. vae_encoder.safetensors (RGBA VAE encoder + quant_conv)
    vae_entry = next(e for e in pins.EDIT_OUTPUT_FILES if e["path"] == "vae_encoder.safetensors")
    vae_dest = root / "vae_encoder.safetensors"
    vae_idx = idx_offset + 2
    if vae_dest.is_file() and vae_dest.stat().st_size == int(vae_entry["bytes"]) and _sha256_file(vae_dest) == str(vae_entry["sha256"]):
        emit(f"[{vae_idx}/{total}] cached vae_encoder.safetensors")
    else:
        if vae_dest.exists():
            vae_dest.unlink()
        vae_spec = slices_by_tag["vae_enc_raw"]
        mb = int(vae_spec["data_bytes"]) / 1e6
        emit(f"[{vae_idx}/{total}] fetching vae_encoder.safetensors ({mb:.1f} MB range slice)")
        vae_part = root / ".vae_enc_raw.part"
        vae_meta = _fetch_upstream_slice(vae_spec, vae_part)
        _pack_vae_encoder_safetensors(root, vae_spec, vae_part, vae_meta)
        vae_part.unlink(missing_ok=True)
        emit(f"[{vae_idx}/{total}] verified vae_encoder.safetensors")

    return root


def fetch_model(target_dir: Path, progress=None, include_edit: bool = True) -> Path:
    """Download and verify all pinned base + edit weight files into ``target_dir``."""
    root = Path(target_dir)
    root.mkdir(parents=True, exist_ok=True)
    base_count = len(pins.PINNED_FILES)
    total = base_count + (len(pins.EDIT_OUTPUT_FILES) if include_edit else 0)

    def emit(msg: str) -> None:
        if progress is not None:
            progress(msg)
        else:
            print(f"[ming-image] {msg}", flush=True)

    # Check free disk space for the bytes not yet present.
    remaining = 0
    for entry in pins.PINNED_FILES:
        p = root / str(entry["path"])
        want = int(entry["bytes"])
        if not (p.is_file() and p.stat().st_size == want):
            remaining += want
    if include_edit:
        for entry in pins.EDIT_OUTPUT_FILES:
            p = root / str(entry["path"])
            want = int(entry["bytes"])
            if not (p.is_file() and p.stat().st_size == want):
                # Allow temporary raw slice beside output during packing
                remaining += want * 2
    if remaining > 0:
        free = shutil.disk_usage(root).free
        need = remaining + 1_000_000_000  # 1 GB headroom
        if free < need:
            raise RuntimeError(
                f"Not enough free disk space for Ming-Image 0.2 Design: need "
                f"{need / 1e9:.1f} GB, have {free / 1e9:.1f} GB."
            )

    for idx, entry in enumerate(pins.PINNED_FILES, 1):
        rel = str(entry["path"])
        want_bytes = int(entry["bytes"])
        want_sha = str(entry["sha256"])
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)

        if dest.is_file() and dest.stat().st_size == want_bytes:
            if _sha256_file(dest) == want_sha:
                emit(f"[{idx}/{total}] cached {rel}")
                continue
            dest.unlink()

        part = dest.with_name(dest.name + ".part")
        url = pins.file_url(rel)
        emit(f"[{idx}/{total}] fetching {rel} ({want_bytes / 1e6:.1f} MB)")

        last_err: Exception | None = None
        for attempt in range(1, 6):
            have = part.stat().st_size if part.is_file() else 0
            if have > want_bytes:
                part.unlink()
                have = 0
            try:
                if have < want_bytes:
                    headers = {"User-Agent": "Phosphene"}
                    if have > 0:
                        headers["Range"] = f"bytes={have}-"
                    req = urllib.request.Request(url, headers=headers)
                    with _hf_urlopen(req, timeout=60.0) as resp:
                        status = getattr(resp, "status", 200)
                        mode = "ab" if (have > 0 and status == 206) else "wb"
                        with part.open(mode) as out_f:
                            while True:
                                chunk = resp.read(CHUNK_BYTES)
                                if not chunk:
                                    break
                                out_f.write(chunk)
                if part.stat().st_size != want_bytes:
                    raise IOError(
                        f"short download for {rel}: got {part.stat().st_size}, expected {want_bytes}"
                    )
                got_sha = _sha256_file(part)
                if got_sha != want_sha:
                    part.unlink()
                    raise ValueError(f"SHA-256 mismatch for {rel}: got {got_sha}, expected {want_sha}")
                os.replace(part, dest)
                emit(f"[{idx}/{total}] verified {rel}")
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                if attempt < 5:
                    time.sleep(min(15.0, 2.0 * attempt))
        if last_err is not None:
            raise RuntimeError(f"Failed to download {rel}: {last_err}") from last_err

    if include_edit:
        fetch_edit_weights(root, progress=progress, idx_offset=base_count, total_files=total)

    emit(f"ready {root}")
    return root


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Fetch pinned Ming-Image-0.2-Design MLX weights.")
    ap.add_argument("--dir", type=Path, required=True, help="Destination directory for weights")
    ap.add_argument("--check", action="store_true", help="Verify existing files without downloading")
    ap.add_argument("--edit-only", action="store_true", help="Fetch/verify only the edit weights (vision + VAE encoder)")
    args = ap.parse_args(argv)
    if args.check:
        bad = verify_edit_weights(args.dir, check_sha=True) if args.edit_only else verify_directory(args.dir, check_sha=True)
        if bad:
            print(f"[ming-image] incomplete: {', '.join(bad)}", file=sys.stderr)
            return 1
        print(f"[ming-image] verified {args.dir}")
        return 0
    if args.edit_only:
        fetch_edit_weights(args.dir)
    else:
        fetch_model(args.dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
