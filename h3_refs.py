"""References: MiniMax H3's own reference-to-video model on Apple Silicon (Pro).

MiniMax publishes H3 as two video models with the same shape: the one Phosphene has run since
4.x (text or a first frame -> video and sound) and a reference model, where each picture is a
person, a thing or a place to put in a NEW shot rather than the first frame. The engine already
packs pictures the way the reference model expects (`--reference`, its own canvas, one slot on
the clock, `<Picture n>` in the prompt: "Face to keep" uses it on the first model). This module
adds the other model: where it lives, how it is fetched and built on this Mac, how a picture is
sized, how the prompt names the pictures and the runner argv.

Which published files, at which revision and checksum, the adapter's strength and the sampler
come from the Pro add-on by name (`extra("h3_references")`); Free has no add-on, and nothing
here runs. Nothing is hosted by Phosphene: the model and the adapter are fetched from their
publishers at a pinned revision, checked, and re-laid out on this Mac.

Measured (R&D, 17 Nov 2026, M4 Max 64 GB): see research/lab/h3-refs.md in the project notes.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

NAME = "References"
MAX_PICTURES = 3
PACK_DIRNAME = "h3-ref2va-q8"
ADAPTER_DIRNAME = "h3-ref2va"
SOURCE_DIRNAME = "h3-ref2va-source"
# 41.4 GB source + ~21 GB pack while the build runs, then the source goes.
DISK_NEED_GB = 66.0
# Memory floor until a smaller Mac is measured: the reference pack is the 8-bit model (about 20 GB of
# weights) and a 5 s Standard clip peaked at 27 GB on a 64 GB Mac (17 Nov 2026). The add-on may name
# its own floor (`min_ram_gb`) once a 16/24 GB proof exists; the Studio never goes below what it names.
MIN_RAM_GB = 32.0
# What the add-on is expected to name, for the explanation shown BEFORE the download starts.
DOWNLOAD_GB_DEFAULT = 42.8
KEEP_GB_DEFAULT = 22.5
SPEC_KEYS = ("source", "adapter", "adapter_file", "scale", "argv", "steps")
MODES = ("t2v",)

_BF16 = 2
_lock = threading.Lock()
_state: dict = {"status": "idle", "step": "", "mb": 0, "total_mb": 0, "error": None}


# ---- what the add-on names ---------------------------------------------------------------------

def spec_ok(spec) -> bool:
    if not isinstance(spec, dict) or not all(spec.get(k) for k in SPEC_KEYS):
        return False
    for part in ("source", "adapter"):
        p = spec.get(part)
        if not isinstance(p, dict) or not all(p.get(k) for k in ("repo", "revision", "file", "sha256", "bytes")):
            return False
    return isinstance(spec.get("argv"), list)


def _url(part: dict) -> str:
    return f"https://huggingface.co/{part['repo']}/resolve/{part['revision']}/{part['file']}"


# ---- where it lives ----------------------------------------------------------------------------

def min_ram_gb(spec) -> float:
    try:
        return max(16.0, float((spec or {}).get("min_ram_gb") or MIN_RAM_GB))
    except (TypeError, ValueError):
        return MIN_RAM_GB


def plan(models_root, spec, free_gb=None, ram_gb=None) -> dict:
    """What Get References will take on THIS Mac, before it starts: the download, what stays, the
    free disk it needs while it builds (less when part of it is already here), and whether the
    Mac's memory and disk are enough. Shown in the Studio next to the button."""
    spec = spec if isinstance(spec, dict) else {}
    download_gb = float(spec.get("download_gb") or (int((spec.get("source") or {}).get("bytes") or 0) / 1e9)
                        or DOWNLOAD_GB_DEFAULT)
    keep_gb = float(spec.get("gb") or KEEP_GB_DEFAULT)
    have_gb = 0.0
    built = False
    if models_root is not None and spec_ok(spec):
        root = Path(models_root)
        built = pack_complete(root / PACK_DIRNAME)
        try:
            part = root / SOURCE_DIRNAME / str(spec["source"]["file"])
            have_gb = part.stat().st_size / 1e9 if part.is_file() else 0.0
        except OSError:
            have_gb = 0.0
    need_gb = 1.0 if built else max(1.0, DISK_NEED_GB - have_gb)
    floor = min_ram_gb(spec)
    return {
        "download_gb": round(download_gb, 1), "keep_gb": round(keep_gb, 1),
        "disk_need_gb": round(need_gb), "have_gb": round(have_gb, 1),
        "free_gb": None if free_gb is None else round(float(free_gb)),
        "disk_ok": free_gb is None or float(free_gb) >= need_gb,
        "min_ram_gb": int(floor), "ram_gb": None if ram_gb is None else round(float(ram_gb)),
        "ram_ok": ram_gb is None or float(ram_gb) + 0.5 >= floor,
    }


def pack_complete(d: Path) -> bool:
    """A built pack: config + quant recipe + every shard its index names + the build receipt."""
    try:
        if not ((d / "config.json").is_file() and (d / "quant_config.json").is_file()
                and (d / ".built_ok").is_file()):
            return False
        index = json.loads((d / "model.safetensors.index.json").read_text("utf-8"))
        shards = set((index.get("weight_map") or {}).values())
        return bool(shards) and all((d / s).is_file() and (d / s).stat().st_size > 0 for s in shards)
    except (OSError, ValueError):
        return False


def adapter_ok(path: Path, spec: dict) -> bool:
    receipt = path.with_suffix(path.suffix + ".ok")
    try:
        doc = json.loads(receipt.read_text("utf-8"))
        return (path.is_file() and str(doc.get("source_sha256")) == str(spec["adapter"]["sha256"])
                and int(doc.get("bytes") or -1) == path.stat().st_size)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def paths(models_root, spec) -> dict:
    """{pack, adapter, files_ok, missing} under the H3 models root."""
    root = Path(models_root) if models_root else None
    if root is None or not spec_ok(spec):
        return {"pack": None, "adapter": None, "files_ok": False,
                "missing": ["the Hailuo H3 pack" if root is None else f"{NAME} is part of Phosphene Pro"]}
    pack = root / PACK_DIRNAME
    adapter = root / ADAPTER_DIRNAME / str(spec["adapter_file"])
    missing = []
    if not pack_complete(pack):
        missing.append(f"the reference model (about {spec.get('gb') or 23:g} GB on disk once built)")
    if not adapter_ok(adapter, spec):
        missing.append("its speed adapter")
    return {"pack": pack, "adapter": adapter, "files_ok": not missing, "missing": missing}


# ---- pictures and prompt -----------------------------------------------------------------------

def match_size(w: int, h: int, canvas_w: int, canvas_h: int) -> tuple[int, int]:
    """The size a picture is encoded at: its own shape, the canvas's pixel area, never upscaled,
    on the 32-pixel grid (the sizing the reference adapter was distilled with)."""
    if w <= 0 or h <= 0:
        raise ValueError(f"a reference picture must have a positive size, got {w}x{h}")
    if max(w, h) > 4 * min(w, h):
        raise ValueError(f"a reference picture must be within 1:4 and 4:1, got {w}x{h}")
    s = min(1.0, math.sqrt(canvas_w * canvas_h / float(w * h)))
    return max(32, round(w * s / 32) * 32), max(32, round(h * s / 32) * 32)


CORE_FIELDS = ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music")


def _fields(text: str) -> dict:
    """The H3 prompt's own sections ({field: body}); {} when the text has none."""
    pat = re.compile(r"(?m)^\s*(" + "|".join(CORE_FIELDS) + r")\s*:\s*")
    hits = list(pat.finditer(text))
    out = {}
    for k, m in enumerate(hits):
        end = hits[k + 1].start() if k + 1 < len(hits) else len(text)
        out[m.group(1)] = text[m.end():end].strip()
    return out


def _first_sentence(text: str) -> str:
    """Up to the first sentence end that is not inside a quoted line of dialogue."""
    quoted = False
    for i, ch in enumerate(text):
        if ch in "\"\u201c\u201d":
            quoted = not quoted
        elif ch in ".!?" and not quoted and (i + 1 == len(text) or text[i + 1].isspace()):
            return text[:i + 1]
    return text


def prompt(user_prompt: str, n: int) -> str:
    """The reference model's prompt layout (subject_definitions, summary, retention_analysis,
    detailed_description, overall_soundscape, non_diegetic_music) around what the user typed, one
    subject per picture. H3's usual sections carry over: the description becomes the detailed
    description, sound and music stay; a description may name the pictures (<Picture 2>). A prompt
    already in the reference layout (it has subject_definitions) is the user's own and stays."""
    text = (user_prompt or "").strip()
    if re.search(r"(?mi)^\s*subject_definitions\s*:", text) or n < 1:
        return text
    f = _fields(text)
    detail = f.get("integrated_multimodal_description") or (text if not f else "")
    subjects = "\n".join(
        f"<Subject {i}> is the subject of <Picture {i}> (a person, a thing or a place), "
        f"preserving exactly how it looks there." for i in range(1, n + 1))
    keep = "\n".join(
        f"<Subject {i}> (appears throughout): fully_preserved - keep the same look as in <Picture {i}>."
        for i in range(1, n + 1))
    plain = re.sub(r"\[Shot \d+[^\]]*\]\s*", "", detail).strip()
    first = _first_sentence(plain) if plain else "A shot with the subjects."
    out = (f"subject_definitions:\n{subjects}\n\nsummary:\n[reference generation] {first}\n\n"
           f"retention_analysis:\n{keep}\n\ndetailed_description:\n{detail}")
    for k in ("overall_soundscape", "non_diegetic_music"):
        if f.get(k):
            out += f"\n\n{k}:\n{f[k]}"
    return out


def argv(resolved: dict, spec: dict, pictures) -> list[str]:
    """The runner fragment: the add-on's sampler, the adapter at its strength, each picture."""
    out = [str(a) for a in spec["argv"]]
    out += ["--lora", f"{resolved['adapter']}:{float(spec['scale']):g}"]
    if spec.get("qkv_permute", True):
        out.append("--lora-qkv-permute")
    for p in pictures:
        out += ["--reference", str(p)]
    return out


# ---- fetch and build on this Mac ---------------------------------------------------------------

def state() -> dict:
    with _lock:
        return dict(_state)


def _set(**kw) -> None:
    with _lock:
        _state.update(kw)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(part: dict, dest: Path, stop: threading.Event | None = None) -> None:
    """Fetch one pinned file (resuming a partial), then check its size and sha256."""
    total = int(part["bytes"])
    have = dest.stat().st_size if dest.is_file() else 0
    if have > total:
        dest.unlink()
        have = 0
    _set(mb=have >> 20, total_mb=total >> 20)
    if have < total:
        headers = {"User-Agent": "Phosphene"}
        if have:
            headers["Range"] = f"bytes={have}-"
        req = urllib.request.Request(_url(part), headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "ab" if have else "wb") as fh:
            if have and resp.status != 206:
                fh.seek(0)
                fh.truncate()
                have = 0
            while True:
                if stop is not None and stop.is_set():
                    raise RuntimeError("stopped")
                chunk = resp.read(4 << 20)
                if not chunk:
                    break
                fh.write(chunk)
                have += len(chunk)
                _set(mb=have >> 20)
    if dest.stat().st_size != total:
        raise RuntimeError(f"the download stopped at {dest.stat().st_size / 1e9:.1f} of {total / 1e9:.1f} GB. "
                           "What arrived is kept: Get References again carries on from there.")
    _set(step="check")
    if _sha256(dest) != str(part["sha256"]):
        dest.unlink()
        raise RuntimeError("the downloaded file did not match the publisher's copy, so it was removed. "
                           "Get References again downloads it fresh.")


def _prefixes():
    return ([(f"transformer_blocks.{i}", f"blocks.{i}") for i in range(50)]
            + [(f"token_refiner.refiner_blocks.{i}", f"token_refiner.blocks.{i}") for i in range(2)])


def convert_adapter(src: Path, dst: Path) -> int:
    """Re-lay a diffusers-PEFT H3 adapter (split q/k/v, [value; gate] SwiGLU rows) into the runner's
    layout: one block-diagonal q/k/v adapter with contiguous rows (the runner re-orders them with
    --lora-qkv-permute), fc1 halves as [gate; value]. Byte moves only; alpha/rank is NOT folded
    (the strength the add-on names applies it). Streams one tensor at a time. Returns the count."""
    with open(src, "rb") as fh:
        (n,) = struct.unpack("<Q", fh.read(8))
        header = json.loads(fh.read(n))
        header.pop("__metadata__", None)
        base = 8 + n

        def read(key):
            t = header[key]
            if t["dtype"] != "BF16" or len(t["shape"]) != 2:
                raise ValueError(f"{key}: expected a 2-D BF16 matrix")
            a, b = t["data_offsets"]
            fh.seek(base + a)
            return fh.read(b - a)

        A, B = ".lora_A.default.weight", ".lora_B.default.weight"
        plan: list = []
        used = set()
        for s, d in _prefixes():
            qkv = [f"{s}.attn.{p}" for p in ("to_q", "to_k", "to_v")]
            ra, ia = header[qkv[0] + A]["shape"]
            ob = header[qkv[0] + B]["shape"][0]
            for p in qkv:
                used.update((p + A, p + B))
            plan.append((f"{d}.attn.qkv_proj.lora_A.weight", [3 * ra, ia],
                         lambda qkv=qkv: b"".join(read(p + A) for p in qkv)))

            def qkv_b(qkv=qkv, r=ra):
                zero = bytes(r * _BF16)
                rows = []
                for i, p in enumerate(qkv):
                    data = read(p + B)
                    w = r * _BF16
                    rows.extend(zero * i + data[j:j + w] + zero * (2 - i) for j in range(0, len(data), w))
                return b"".join(rows)
            plan.append((f"{d}.attn.qkv_proj.lora_B.weight", [3 * ob, 3 * ra], qkv_b))
            for sp, dp in (("attn.to_out.0", "attn.out_proj"), ("ff.net.0.proj", "mlp.fc1"),
                           ("ff.net.2", "mlp.fc2")):
                p = f"{s}.{sp}"
                used.update((p + A, p + B))
                plan.append((f"{d}.{dp}.lora_A.weight", header[p + A]["shape"], lambda p=p: read(p + A)))

                def b_bytes(p=p, swap=(dp == "mlp.fc1")):
                    b = read(p + B)
                    return b[len(b) // 2:] + b[:len(b) // 2] if swap else b
                plan.append((f"{d}.{dp}.lora_B.weight", header[p + B]["shape"], b_bytes))
        unused = sorted(set(header) - used)
        if unused:
            raise ValueError(f"adapter has {len(unused)} tensors this layout does not use, e.g. {unused[:2]}")
        out_header: dict = {"__metadata__": {"converted_from": src.name, "qkv_layout": "contiguous",
                                             "fc1_layout": "[gate;value]", "peft_scale_folded_into_B": "0"}}
        off = 0
        for key, shape, _ in plan:
            nbytes = shape[0] * shape[1] * _BF16
            out_header[key] = {"data_offsets": [off, off + nbytes], "dtype": "BF16", "shape": list(shape)}
            off += nbytes
        blob = json.dumps(out_header, separators=(",", ":"), sort_keys=True).encode()
        tmp = dst.with_name(dst.stem + ".partial" + dst.suffix)
        with open(tmp, "wb") as out:
            out.write(struct.pack("<Q", len(blob)))
            out.write(blob)
            for key, shape, make in plan:
                data = make()
                if len(data) != shape[0] * shape[1] * _BF16:
                    raise ValueError(f"{key}: {len(data)} bytes for {shape}")
                out.write(data)
        os.replace(tmp, dst)
        return len(plan)


def build(models_root: Path, spec: dict, h3_root: Path, python: Path, push=print,
          stop: threading.Event | None = None, run=subprocess.run) -> None:
    """Fetch the reference model and its adapter, build the 8-bit pack, drop the source.
    Every step is skipped when its result is already there, so a stopped build resumes."""
    models_root = Path(models_root)
    pack = models_root / PACK_DIRNAME
    adapter = models_root / ADAPTER_DIRNAME / str(spec["adapter_file"])
    if not pack_complete(pack):
        src_dir = models_root / SOURCE_DIRNAME
        src_dir.mkdir(parents=True, exist_ok=True)
        src = src_dir / str(spec["source"]["file"])
        _set(step="download")
        push(f"[h3-refs] downloading the reference model ({int(spec['source']['bytes']) / 1e9:.1f} GB, once)")
        _download(spec["source"], src, stop)
        _set(step="build")
        push("[h3-refs] building the 8-bit model on this Mac (about 5-10 minutes)")
        tmp = models_root / (PACK_DIRNAME + ".partial")
        shutil.rmtree(tmp, ignore_errors=True)
        r = run([str(python), str(Path(h3_root) / "scripts" / "quantize_stream.py"),
                 "--src", str(src), "--out", str(tmp)], cwd=str(h3_root))
        if getattr(r, "returncode", 1) != 0:
            raise RuntimeError("building it on this Mac did not finish. The download is kept: Get References again builds it without downloading.")
        (tmp / ".built_ok").write_text(json.dumps({"source_sha256": spec["source"]["sha256"],
                                                   "at": time.time()}), "utf-8")
        if not pack_complete(tmp):
            raise RuntimeError("building it on this Mac did not finish. The download is kept: Get References again builds it without downloading.")
        shutil.rmtree(pack, ignore_errors=True)
        os.replace(tmp, pack)
        try:
            src.unlink()
            src_dir.rmdir()
        except OSError:
            pass
    if not adapter_ok(adapter, spec):
        adapter.parent.mkdir(parents=True, exist_ok=True)
        raw = adapter.parent / str(spec["adapter"]["file"])
        _set(step="adapter")
        _download(spec["adapter"], raw, stop)
        convert_adapter(raw, adapter)
        adapter.with_suffix(adapter.suffix + ".ok").write_text(json.dumps(
            {"source_sha256": spec["adapter"]["sha256"], "bytes": adapter.stat().st_size}), "utf-8")
        try:
            raw.unlink()
        except OSError:
            pass
    push(f"[h3-refs] {NAME} is ready")


def start(models_root, spec, h3_root, python, push=print, free_gb=None, ram_gb=None) -> dict:
    """Start the one background build; {ok, started|already_installed|error}."""
    if not spec_ok(spec):
        return {"ok": False, "pro_required": True, "error": f"{NAME} is part of Phosphene Pro."}
    if models_root is None or python is None:
        return {"ok": False, "error": "Install Hailuo H3 first."}
    if paths(models_root, spec)["files_ok"]:
        return {"ok": True, "started": False, "already_installed": True}
    pl = plan(models_root, spec, free_gb, ram_gb)
    if not pl["ram_ok"]:
        return {"ok": False, "too_small": True,
                "error": f"{NAME} needs a Mac with {pl['min_ram_gb']} GB of memory or more; "
                         f"this Mac has {pl['ram_gb']} GB. Nothing was downloaded."}
    if not pl["disk_ok"]:
        return {"ok": False, "no_space": True,
                "error": f"{NAME} needs about {pl['disk_need_gb']} GB free on the models disk while it builds "
                         f"(about {pl['keep_gb']:g} GB stay). That disk has {pl['free_gb']} GB free. "
                         "Nothing was downloaded."}
    with _lock:
        if _state.get("status") == "running":
            return {"ok": False, "already_running": True, "error": f"the {NAME} download is already running"}
        _state.clear()
        _state.update(status="running", step="starting", mb=0, total_mb=0, error=None)

    def work():
        try:
            build(Path(models_root), spec, Path(h3_root), Path(python), push)
            _set(status="done", step="ready")
        except Exception as exc:  # noqa: BLE001 - the panel shows it and offers Try again
            msg = str(exc)
            if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:   # ENOSPC
                msg = ("the models disk filled up. What arrived is kept: free some space, then Get "
                       "References again carries on from there.")
            elif isinstance(exc, OSError) and not isinstance(exc, FileNotFoundError):
                # urllib/socket errors read as code ("<urlopen error ...>"): say what happened.
                msg = ("the download stopped (the connection dropped or the publisher did not answer). "
                       "What arrived is kept: Get References again carries on from there.")
            _set(status="failed", error=msg[:300])
            push(f"[h3-refs] {NAME} download stopped: {str(exc)[:200]}")
    threading.Thread(target=work, daemon=True, name="h3-refs-build").start()
    return {"ok": True, "started": True}
