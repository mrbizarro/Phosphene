#!/usr/bin/env python3
"""Resumable YuE2 pack fetch / offline verifier. No model imports.

Downloads go ONLY into .staging. The generator loader rejects HF's .cache
and every undeclared file/directory. Receipts and the current model permission
therefore live at the pack root, outside generator/. HF_HOME is inherited.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import time

# The public repos the pinned mlx-Yue port documents, pinned by revision. The
# generator is vanch007's MLX conversion (its BF16 tensors are byte-identical to
# m-a-p/YuE2-3B); the decoder is the original m-a-p/YuE2-Vae file. Phosphene
# re-hosts nothing: bump a revision here only together with engine_pin.txt.
SOURCES = {
    "generator": {"repo": "vanch007/mlx-Yue2-3B", "revision": "fa66d203dd56d7e033e05ee8b32269768984c4cc", "prefix": ""},
    "vae": {"repo": "m-a-p/YuE2-Vae", "revision": "95535e72a97bc0f09b8ada125d26b4009428c0e8", "prefix": ""},
}
GENERATOR_FILES = (
    "LICENSE", "THIRD_PARTY_NOTICES.md", "ar-8bit.safetensors", "ar-bf16.safetensors",
    "config.json", "licenses/SnakeBeta-NVIDIA-MIT.txt", "licenses/stable-audio-tools-MIT.txt",
    "nar-bf16.safetensors", "qwen.tiktoken",
)
VAE_FILES = ("model.safetensors", "config.json", "weights_manifest.json", "LICENSE", "THIRD_PARTY_NOTICES.md")
VAE_SHA256 = "807ce9d5149fa27c5ad3e6582058469852e908f6c5acc8c8aa338e7ab7751346"
VAE_BYTES = 530512720
PACK_BYTES = 10448112576  # approximate; free-space decisions use missing manifest bytes


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_name(name: str) -> str:
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or str(p) != name:
        raise ValueError(f"unsafe manifest path: {name!r}")
    return name


def records(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict) or not files:
        raise ValueError(f"empty files map: {path.name}")
    for name, rec in files.items():
        safe_name(name)
        if not isinstance(rec, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(rec.get("sha256", ""))):
            raise ValueError(f"missing sha256: {name}")
        if not isinstance(rec.get("bytes"), int) or rec["bytes"] < 1:
            raise ValueError(f"invalid byte count: {name}")
    return files


def file_problem(path: Path, rec: dict | None = None, *, hashes: bool = False) -> str | None:
    if not path.is_file() or path.is_symlink():
        return "missing file"
    try:
        size = path.stat().st_size
        if size == 0 or (rec and size != rec["bytes"]):
            return "size mismatch"
        if hashes and rec and digest(path) != rec["sha256"]:
            return "sha256 mismatch"
    except OSError as e:
        return str(e)
    return None


def pack_problems(root: Path, *, hashes: bool = False) -> list[str]:
    """Stat-only for /status; full hashes for Install/Update/--check."""
    errors = []
    for part, manifest, required in (("generator", "conversion.json", GENERATOR_FILES),
                                      ("vae", "weights_manifest.json", VAE_FILES)):
        base = root / part
        try:
            recs = records(base / manifest)
            if part == "generator" and set(recs) != set(GENERATOR_FILES):
                errors.append("generator/conversion.json: unexpected files map")
            if part == "vae" and (recs.get("model.safetensors", {}).get("sha256") != VAE_SHA256
                                  or recs.get("model.safetensors", {}).get("bytes") != VAE_BYTES):
                errors.append("vae/weights_manifest.json: unexpected model identity")
        except (OSError, ValueError, TypeError) as e:
            recs = {}
            errors.append(f"{part}/{manifest}: missing or invalid ({e})")
        for name in sorted(set(required) | set(recs) | {manifest}):
            problem = file_problem(base / name, recs.get(name), hashes=hashes)
            if problem:
                errors.append(f"{part}/{name}: {problem}")
        if part == "generator":
            allowed = set(recs) | {manifest, "README.md"}
            parents = {str(p) for n in allowed for p in PurePosixPath(n).parents if str(p) != "."}
            for p in base.rglob("*"):
                rel = p.relative_to(base).as_posix()
                if p.is_symlink() or (rel not in (parents if p.is_dir() else allowed)):
                    errors.append(f"generator/{rel}: unexpected entry")
        elif not any((base / "licenses").glob("*")):
            errors.append("vae/licenses: missing notices")
    # Records all downloaded files, including metadata that has no source hash.
    receipt = root / "pack_source.json"
    if receipt.is_file():
        try:
            for name, rec in records(receipt).items():
                problem = file_problem(root / name, rec, hashes=hashes)
                if problem:
                    errors.append(f"{name}: {problem}")
        except (ValueError, OSError, TypeError) as e:
            errors.append(f"pack_source.json: {e}")
    return errors


def choose_sources(api=None) -> dict:
    return {k: dict(v) for k, v in SOURCES.items()}


def fetch_pack(root: Path, min_free_gb: float) -> None:
    from huggingface_hub import HfApi, hf_hub_download
    api = HfApi()
    root.mkdir(parents=True, exist_ok=True)
    # Restore the current permission on repairs too, including offline repairs.
    notices = Path(__file__).resolve().parents[2] / "LICENSES"
    for name in ("YuE2-MODEL_LICENSE.txt", "NOTICE-yue2.md"):
        shutil.copyfile(notices / name, root / name)
    # Completely intact repairs are offline and never require another 14 GB.
    if not pack_problems(root, hashes=True) and (root / "pack_source.json").is_file():
        print("YuE2 pack verified; all files intact, no download needed.", flush=True)
        return
    sources = choose_sources(api)
    stage = root / ".staging"
    receipts = {}

    def download(part, name, rec=None):
        safe_name(name)
        src = sources[part]
        dest = root / part / name
        if rec and file_problem(dest, rec, hashes=True) is None:
            print(f"Keep {part}/{name} (sha256 OK)", flush=True)
            receipts[f"{part}/{name}"] = rec
            return dest
        print(f"Fetch {part}/{name} from {src['repo']} @ {src['revision']}", flush=True)
        for attempt in range(2):
            staged = Path(hf_hub_download(repo_id=src["repo"], revision=src["revision"],
                filename=src["prefix"] + name, local_dir=stage / part,
                force_download=bool(attempt)))
            actual = {"bytes": staged.stat().st_size, "sha256": digest(staged)}
            if rec and actual != {"bytes": rec["bytes"], "sha256": rec["sha256"]}:
                if not attempt:
                    continue
                raise ValueError(f"sha256/size mismatch after download: {part}/{name}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            os.replace(staged, dest)
            receipts[f"{part}/{name}"] = actual
            return dest
        raise AssertionError("unreachable")

    gen = records(download("generator", "conversion.json"))
    if set(gen) != set(GENERATOR_FILES):
        raise ValueError("generator conversion.json does not describe the expected YuE2 pack")
    vae = records(download("vae", "weights_manifest.json"))
    if (vae.get("model.safetensors", {}).get("bytes") != VAE_BYTES
            or vae.get("model.safetensors", {}).get("sha256") != VAE_SHA256):
        raise ValueError("VAE manifest does not match the pinned model")
    # A file that finished downloading but was interrupted before it was moved
    # into place is still in .staging: verify and promote it now, or it would be
    # priced (and fetched) a second time.
    for part, rs in (("generator", gen), ("vae", vae)):
        for n, r in rs.items():
            dest = root / part / n
            staged = stage / part / (sources[part]["prefix"] + n)
            if file_problem(dest, r, hashes=True) and staged.is_file() and not staged.is_symlink():
                if file_problem(staged, r, hashes=True) is None:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(staged, dest)
                    print(f"Promoted staged {part}/{n} (sha256 OK)", flush=True)
    # Only absent/corrupt bytes need space. os.replace avoids a second copy.
    missing_bytes = sum(r["bytes"] for part, rs in (("generator", gen), ("vae", vae))
                        for n, r in rs.items() if file_problem(root / part / n, r, hashes=True))
    # huggingface_hub never resumes: every attempt writes a fresh
    # <name>.<uuid>.incomplete and an interrupted one is simply abandoned. Those
    # are this installer's own staging leftovers, so clear them before pricing
    # the space the missing files need.
    if stage.is_dir():
        for leftover in stage.rglob("*.incomplete"):
            try:
                leftover.unlink()
            except OSError:
                pass
    needed = missing_bytes + max(0, min_free_gb * 1e9 - PACK_BYTES) if missing_bytes else 0
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        print("Cannot read free disk space; continuing (preflight fail-open).", flush=True)
    else:
        if free < needed:
            raise ValueError(f"YuE2 needs {needed / 1e9:.1f} GB free for missing files; {free / 1e9:.1f} GB available")
    for name, rec in gen.items():
        download("generator", name, rec)
    vs = sources["vae"]
    names = api.list_repo_files(vs["repo"], revision=vs["revision"])
    vae_names = set(VAE_FILES) | set(vae)
    vae_names.update(n[len(vs["prefix"]):] for n in names if n.startswith(vs["prefix"] + "licenses/"))
    for name in sorted(vae_names - {"weights_manifest.json"}):
        download("vae", name, vae.get(name))
    # Recover from a previous direct `hf download --local-dir generator`.
    cache = root / "generator" / ".cache"
    if cache.exists():
        quarantine = stage / f"generator-cache-{time.time_ns()}"
        shutil.move(str(cache), str(quarantine))
        print("Moved HF metadata out of generator/ into .staging.", flush=True)
    receipt = {"sources": sources, "files": receipts}
    tmp = root / "pack_source.json.tmp"
    tmp.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    tmp.replace(root / "pack_source.json")
    errors = pack_problems(root, hashes=True)
    if errors:
        raise ValueError("\n".join(errors))
    print("YuE2 pack verified. Generator tree is clean; every recorded sha256 matches.", flush=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", type=Path, default=Path(os.environ.get("LTX_MUSIC_MODELS", Path(__file__).resolve().parents[2] / "mlx_models/yue2")))
    parser.add_argument("--check", action="store_true", help="offline verification only; exit 0/1")
    parser.add_argument("--min-free-gb", type=float, default=14.0)
    args = parser.parse_args(argv)
    try:
        if args.check:
            errors = pack_problems(args.models, hashes=True)
            for error in errors:
                print(error, flush=True)
            if not errors:
                print("YuE2 pack verified (sha256 + exact generator tree).", flush=True)
            return int(bool(errors))
        fetch_pack(args.models, max(0, args.min_free_gb))
        return 0
    except (OSError, ValueError, RuntimeError) as e:
        print(f"YuE2 pack: {e}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
