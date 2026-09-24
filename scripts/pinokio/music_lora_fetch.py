#!/usr/bin/env python3
"""Fetch the YuE2 LoRA pack — and own what "a LoRA the studio can pick" means.

Two adapters, ~210 MB together, pinned by revision and checked by SHA-256:

    mlx_models/yue2-loras/ar_lora_inst_v3abc.bf16.safetensors   139.5 MB
    mlx_models/yue2-loras/nar_lora_joint_v9.bf16.safetensors     70.3 MB
    mlx_models/yue2-loras/user/*.safetensors                     whatever you drop in

The first is Mothersuperior's instrumental AR adapter: the one thing that
turns Phosphene's Instrumental toggle from a prompt trick into the same
mechanism Maestro uses. The second is the acoustic-branch adapter from the
real-audio tokenizer pack, kept because it is the one published NAR LoRA we
can verify against — it is trained beside a tokenizer head we have not ported
yet (phase 2), so it is offered as an experiment, not as a recommendation.

Plus, in its own subdirectory so the LoRA picker never offers it as an
adapter, the real-audio TOKENIZER HEAD:

    mlx_models/yue2-loras/tokenizer/tokenizer_head_joint_v9.bf16.safetensors  85.6 MB
    mlx_models/yue2-loras/tokenizer/sem_nbr_cos.npy                            2.1 MB
    mlx_models/yue2-loras/tokenizer/sem_nbr_idx.npy                            2.1 MB

The head turns a REAL recording into YuE2 semantic codes (MERT-v2-FullSong
layer-20 features at 25 Hz in, 32768-way codes out) — the thing that makes
`nar_lora_joint_v9` worth having, and the thing that makes an audio prompt
possible. `scripts/music/yue2_tokenizer.py` is the MLX port that runs it. The
two `sem_nbr_*` tables are the TRAINING-side semantic-neighbour lookups (8
neighbours per code); inference never reads them, they are pinned here so a
future training port does not have to re-derive their provenance, and they are
not required for readiness.

Deliberately plain directories, like the cover pack: readable with `ls`,
deletable with `rm -rf`, offline by construction. `user/` is scanned for any
`*.safetensors`, so a community or AI-Toolkit adapter is usable the moment it
is copied in — no registry, no install step. The head lives under `tokenizer/`
precisely because `pack_adapters()` globs only the root and `user/`.

This module is STDLIB ONLY apart from `huggingface_hub` inside `fetch()`: the
panel imports it at boot to list what the picker offers, and the panel must
not pull MLX or numpy onto that path.

Both adapters are CC BY-NC 4.0, the same terms as YuE2 itself.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

SOURCES = {
    "ar_lora_inst_v3abc.bf16.safetensors": {
        "repo": "Mothersuperior/YuE2-instrumental-cot-full-loras",
        "revision": "947f2f4b28978b2b6c3e316e6a87925c76bf3c4b",
        "sha256": "e408fd3148b75b1165f7ddbf63db575d83bb6402a0b5f876fcb767dbcb2c5414",
        "bytes": 139_502_088,
    },
    "nar_lora_joint_v9.bf16.safetensors": {
        "repo": "Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4",
        "revision": "e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5",
        "sha256": "ce57f98e9e0e65c724c2a00aa23dea2568952590504a428c588d82f00bc61c55",
        "bytes": 70_301_896,
    },
}
#: The real-audio tokenizer head and its training-side lookup tables. Same repo
#: and revision as the acoustic adapter it was trained beside. `remote` is the
#: path inside the repository; the key is the name on disk.
HEAD_SOURCES = {
    "tokenizer_head_joint_v9.bf16.safetensors": {
        "repo": "Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4",
        "revision": "e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5",
        "remote": "tokenizer_head_joint_v9.bf16.safetensors",
        "sha256": "cffb673cd663517c8978ba73d186a87517958025f7e409371ea56df3bb364db2",
        "bytes": 85_644_856,
        "required": True,
    },
    "sem_nbr_cos.npy": {
        "repo": "Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4",
        "revision": "e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5",
        "remote": "assets/sem_nbr_cos.npy",
        "sha256": "cf544760a9ef2f66b0bba4fa6ac3d055c628fc109cea47ba9bc6de59c767ad12",
        "bytes": 2_097_280,
        "required": False,
    },
    "sem_nbr_idx.npy": {
        "repo": "Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4",
        "revision": "e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5",
        "remote": "assets/sem_nbr_idx.npy",
        "sha256": "081d5f3d41ac2b604ee95db53f5c2069b33d871c317b611d7df8e02b42eda141",
        "bytes": 2_097_280,
        "required": False,
    },
}
INSTRUMENTAL_ADAPTER = "ar_lora_inst_v3abc.bf16.safetensors"
JOINT_NAR_ADAPTER = "nar_lora_joint_v9.bf16.safetensors"
TOKENIZER_HEAD = "tokenizer_head_joint_v9.bf16.safetensors"
USER_SUBDIR = "user"
HEAD_SUBDIR = "tokenizer"
LORA_BYTES = sum(entry["bytes"] for entry in SOURCES.values())
HEAD_BYTES = sum(entry["bytes"] for entry in HEAD_SOURCES.values())
MAX_STRENGTH = 1.5

NOTICE = """YuE2 LoRA pack — third-party weights
====================================

ar_lora_inst_v3abc.bf16.safetensors
    Mothersuperior/YuE2-instrumental-cot-full-loras
    revision 947f2f4b28978b2b6c3e316e6a87925c76bf3c4b
    An AR-branch LoRA (rank 64) for instrumental generation with full score
    planning. Applied at strength 1.0 by Phosphene's Instrumental toggle.

nar_lora_joint_v9.bf16.safetensors
    Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4
    revision e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5
    An acoustic-branch LoRA (rank 32) trained jointly with the real-audio
    tokenizer head `tokenizer_head_joint_v9`, which Phosphene has NOT ported.
    The file also carries complete vae2llm / llm2vae decoder projections;
    Phosphene's `joint` mode ignores them, as Maestro's does.

Both files are licensed CC BY-NC 4.0 (Creative Commons Attribution-
NonCommercial 4.0 International), the same terms as YuE2 itself
(m-a-p/YuE2-3B). Non-commercial use only. Neither file is redistributed by
Phosphene: this script downloads them from Hugging Face on request.

tokenizer/tokenizer_head_joint_v9.bf16.safetensors
tokenizer/sem_nbr_cos.npy
tokenizer/sem_nbr_idx.npy
    Mothersuperior/yue2-mothersuperior-realaudio-tokenizer-v4
    revision e2e63d859f3af879baf1b4d4e9f22d1eeda6fde5
    The real-audio tokenizer head trained jointly with nar_lora_joint_v9: an
    8-layer transformer encoder that turns MERT-v2-FullSong layer-20 features
    at 25 Hz into YuE2 semantic codes. The two sem_nbr_*.npy tables are the
    training-side semantic-neighbour lookups (assets/ in the repository);
    inference does not read them.

Anything under user/ is yours and is never touched by an update or a repair.
"""


# ------------------------------------------------------------------ the pack

def part_problems(root: Path, name: str, source: dict | None = None,
                  *, deep: bool = False) -> list[str]:
    """Empty when the file on disk is the file that was pinned.

    A NAME IS NOT A FILE (Codex review, 2026-09-22). Any non-empty file used
    to count, so a download that dropped halfway through left a truncated
    adapter that read as ready forever: the card said so, the Download button
    did nothing, and the first song that picked it failed to load it.

    Cheap by default - presence and exact size - because the panel asks this
    on every status poll and hashing 300 MB every few seconds is not a thing a
    UI may do. The hash is not skipped, it is moved: `_download` verifies it
    before a file is ever given its pinned name, so on-disk size is a
    sufficient gate afterwards. `deep=True` hashes anyway, which is what
    `--check --deep` is for.
    """
    path = Path(root) / name
    try:
        size = path.stat().st_size
    except OSError:
        return [name]
    if not path.is_file() or size == 0:
        return [name]
    if source is not None:
        if size != int(source["bytes"]):
            return [name]
        if deep and sha256_of(path) != source["sha256"]:
            return [name]
    return []


def lora_problems(root: Path, *, deep: bool = False) -> list[str]:
    """Empty when the two pinned ADAPTERS are usable — which is what the
    studio's picker needs and all it needs. Not the same question as "is the
    whole pack here": see `pack_problems`."""
    out: list[str] = []
    for name, source in SOURCES.items():
        out += part_problems(Path(root), name, source, deep=deep)
    return out


def head_root(root) -> Path:
    """Where the tokenizer head lives — never the adapter root itself."""
    return Path(root).expanduser() / HEAD_SUBDIR


def head_path(root) -> Path:
    return head_root(root) / TOKENIZER_HEAD


def head_problems(root, *, deep: bool = False) -> list[str]:
    """Empty when the tokenizer head is usable. Optional assets are not gates."""
    out: list[str] = []
    for name, source in HEAD_SOURCES.items():
        if source["required"]:
            out += part_problems(head_root(root), name, source, deep=deep)
    return out


def pack_problems(root, *, deep: bool = False) -> list[str]:
    """Empty when there is NOTHING LEFT TO DOWNLOAD — adapters and head.

    The second of the two readiness questions, and the one the fetch endpoint
    has to ask. It used to ask the first: both adapter names present meant
    "ready", so a pack whose tokenizer-head download was interrupted could
    never be completed from the UI (Codex review, 2026-09-22).
    """
    return lora_problems(root, deep=deep) + [
        f"{HEAD_SUBDIR}/{name}" for name in head_problems(root, deep=deep)]


_LABELS = {
    INSTRUMENTAL_ADAPTER: ("Instrumental", "ar",
                           "Mothersuperior's instrumental AR adapter — the one the "
                           "Instrumental toggle uses"),
    JOINT_NAR_ADAPTER: ("Real-audio sound v9", "nar",
                        "Experimental: an acoustic adapter trained beside a tokenizer "
                        "head Phosphene has not ported yet"),
}


def adapter_meta(path) -> dict:
    """What a trained adapter's own sidecar says about how to USE it.

    `yue2_train_voice.py --install` puts `<name>.json` beside
    `<name>.safetensors`; it names the training `trigger` and the `dialect`
    (`direct`: generate with no score; `score`: generate with one). The picker
    used to derive everything from the filename, so a trained voice was
    offered as usable and then rendered without its trigger and under the
    wrong conditioning (M6-04). An adapter with no sidecar says nothing."""
    p = Path(path)
    side = p.with_name(p.name[: -len(".safetensors")] + ".json") \
        if p.name.lower().endswith(".safetensors") else p.with_suffix(".json")
    try:
        data = json.loads(side.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    trigger = str(data.get("trigger") or "").strip()
    if trigger:
        out["trigger"] = trigger
    dialect = str(data.get("dialect") or "").strip()
    if dialect in ("direct", "score"):
        out["dialect"] = dialect
    return out


def _entry(path: Path, root: Path, *, builtin: bool) -> dict:
    label, branch, note = _LABELS.get(
        path.name,
        (path.stem.replace("_", " ").strip(),
         "nar" if "nar" in path.name.lower() else "ar", ""))
    meta = {} if builtin else adapter_meta(path)
    if meta:
        bits = []
        if meta.get("trigger"):
            bits.append(f"trigger “{meta['trigger']}” is added for you")
        if meta.get("dialect") == "direct":
            bits.append("trained without a score — use Score: No score")
        elif meta.get("dialect") == "score":
            bits.append("trained with a score — use Melody or Melody + chords")
        note = " · ".join(filter(None, [note] + bits))
    return {"id": str(path.relative_to(root)), "file": path.name, "name": label,
            "note": note, "builtin": builtin, "branch": branch,
            "bytes": path.stat().st_size,
            "instrumental": path.name == INSTRUMENTAL_ADAPTER, **meta}


def pack_adapters(root) -> list[dict]:
    """Every adapter the picker offers: the pinned pack, then the user folder."""
    root = Path(root).expanduser()
    if not root.is_dir():
        return []
    # A pinned adapter is offered only when it is the pinned FILE; a truncated
    # download must not sit in the picker waiting to fail a render. Nothing is
    # pinned about `user/`, so anything non-empty there is the owner's to try.
    found = [_entry(path, root, builtin=True)
             for path in sorted(root.glob("*.safetensors"))
             if not part_problems(root, path.name, SOURCES.get(path.name))]
    user = root / USER_SUBDIR
    if user.is_dir():
        found += [_entry(path, root, builtin=False)
                  for path in sorted(user.rglob("*.safetensors"))]
    return found


def resolve_in_pack(root, identifier: str) -> Path:
    """A picker id → a real file, refusing anything outside the LoRA folder."""
    root = Path(root).expanduser().resolve()
    candidate = (root / str(identifier)).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError("A LoRA has to come from the music LoRA folder")
    if not candidate.is_file() or candidate.suffix.lower() != ".safetensors":
        raise FileNotFoundError(f"No LoRA named {identifier!r} in {root}")
    return candidate


def validate_strength(value) -> float:
    try:
        strength = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError("A LoRA strength must be a number") from error
    if not 0.0 <= strength <= MAX_STRENGTH:
        raise ValueError(f"A LoRA strength must be between 0 and {MAX_STRENGTH:g}, "
                         f"not {strength}")
    return strength


def parse_spec(spec: str) -> tuple[str, float]:
    """`path[:strength]` — split only when the tail really is a number, so a
    path that happens to contain a colon still resolves."""
    text = str(spec).strip()
    head, sep, tail = text.rpartition(":")
    if sep and head:
        try:
            return head, validate_strength(tail)
        except ValueError:
            pass
    return text, 1.0


def _items_from_text(raw) -> list:
    """One piece of TEXT -> the specs inside it. `a:0.8,b:1.2` or a JSON list."""
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
        except ValueError:
            return []
        return loaded if isinstance(loaded, list) else []
    return [part for part in text.split(",") if part.strip()]


def _picks_from_items(items) -> list[tuple[str, float]]:
    picks: list[tuple[str, float]] = []
    for item in items:
        if isinstance(item, dict):
            identifier = str(item.get("id") or "").strip()
            strength = item.get("strength", 1.0)
        elif isinstance(item, (list, tuple)):
            picks += _picks_from_items(item)
            continue
        else:
            identifier, strength = parse_spec(item)
        if not identifier:
            continue
        try:
            picks.append((identifier, validate_strength(strength)))
        except ValueError:
            continue
    return picks


def parse_picks(raw) -> list[tuple[str, float]]:
    """A NORMALISED selection: one `id:strength,id:strength` string, a JSON
    list, or a list whose every element is ONE spec (a string or a dict).

    This is the shape a job's saved params carry, because `music_params` has
    already resolved them once. It is NOT the shape a browser posts — see
    `parse_field`."""
    if isinstance(raw, dict):
        return _picks_from_items([raw])
    if isinstance(raw, (list, tuple)):
        return _picks_from_items(raw)
    return _picks_from_items(_items_from_text(raw))


def parse_field(raw) -> list[tuple[str, float]]:
    """A FORM FIELD, as `parse_qs` hands it over.

    THE DIFFERENCE IS THE BUG (Codex review, 2026-09-22). `parse_qs` returns a
    list with one element PER REPEAT of the field, and the studio repeats
    nothing: it joins the whole selection into a single value, so two picked
    adapters arrive as `["a.safetensors:0.8,b.safetensors:1.2"]`. Read as a
    normalised list, that one element is one spec — a filename with a comma in
    it, which resolves to nothing, so picking two voices silently turned both
    of them off and picking one worked. Every value of the field is therefore
    split here, and only an element that is already normalised (a dict, or a
    nested list) is passed through untouched."""
    values = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    picks: list[tuple[str, float]] = []
    for value in values:
        if isinstance(value, (dict, list, tuple)):
            picks += parse_picks(value)
        else:
            picks += _picks_from_items(_items_from_text(value))
    return picks


# --------------------------------------------------------------- downloading

def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _download(sources: dict, destination: Path, label: str) -> None:
    from huggingface_hub import hf_hub_download

    destination.mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        dest = destination / name
        if not part_problems(destination, name, source, deep=True):
            print(f"[{label}] have {name}", flush=True)
            continue
        if dest.exists():
            print(f"[{label}] {name} is damaged or unfinished — fetching it again",
                  flush=True)
        try:
            staged = hf_hub_download(repo_id=source["repo"], revision=source["revision"],
                                     filename=source.get("remote", name))
        except Exception as exc:                                 # noqa: BLE001
            raise SystemExit(f"[{label}] could not fetch {name}: {exc}") from exc
        # ATOMIC. The pinned name is given to a file only after that file has
        # passed its checksum, so an interrupted copy leaves a `.partial` to
        # throw away and never a plausible-looking adapter (Codex review,
        # 2026-09-22). `os.replace` is atomic within a filesystem, and the
        # temp file is a sibling so it always is one.
        tmp = dest.with_name(f"{dest.name}.{os.getpid()}.partial")
        try:
            shutil.copyfile(staged, tmp)
            digest = sha256_of(tmp)
            if digest != source["sha256"]:
                raise SystemExit(f"[{label}] {name} failed its checksum "
                                 f"({digest} != {source['sha256']})")
            os.replace(tmp, dest)
        finally:
            Path(tmp).unlink(missing_ok=True)
        print(f"[{label}] wrote {name} ({dest.stat().st_size / 1e6:.1f} MB)", flush=True)


def fetch(root: Path, *, min_free_gb: float = 1.0, include_head: bool = True) -> None:
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    (root / USER_SUBDIR).mkdir(exist_ok=True)
    free = shutil.disk_usage(root).free / 1e9
    if free < min_free_gb:
        raise SystemExit(f"[music-lora] only {free:.1f} GB free; need about {min_free_gb:g} GB")
    _download(SOURCES, root, "music-lora")
    if include_head:
        _download(HEAD_SOURCES, head_root(root), "music-head")
    (root / "NOTICE").write_text(NOTICE, encoding="utf-8")
    (root / "pack_source.json").write_text(json.dumps(
        {"sources": SOURCES, "tokenizer_head": HEAD_SOURCES, "license": "CC BY-NC 4.0",
         "purpose": "YuE2 LoRA inference: instrumental generation, acoustic adapters "
                    "and the real-audio tokenizer head"},
        indent=2), encoding="utf-8")
    missing = lora_problems(root) if not include_head else pack_problems(root)
    if missing:
        raise SystemExit(f"[music-lora] incomplete after fetch: {', '.join(missing)}")
    print("[music-lora] ready", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", type=Path, required=True)
    ap.add_argument("--min-free-gb", type=float, default=1.0)
    ap.add_argument("--check", action="store_true", help="report readiness and exit")
    ap.add_argument("--deep", action="store_true",
                    help="with --check: hash every pinned file, not just size it")
    ap.add_argument("--no-head", action="store_true",
                    help="adapters only; skip the real-audio tokenizer head")
    args = ap.parse_args(argv)
    if args.check:
        deep = bool(args.deep)
        missing = lora_problems(args.root, deep=deep)
        head_missing = head_problems(args.root, deep=deep)
        print(json.dumps({"ready": not missing, "missing": missing,
                          "head_ready": not head_missing, "head_missing": head_missing,
                          "complete": not (missing or head_missing),
                          "head": str(head_path(args.root)),
                          "adapters": pack_adapters(args.root)}, indent=2))
        return 0 if not missing else 1
    fetch(args.root, min_free_gb=args.min_free_gb, include_head=not args.no_head)
    return 0


if __name__ == "__main__":
    sys.exit(main())
