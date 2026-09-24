#!/usr/bin/env python3
"""LoRA inference for the YuE2 MLX engine — Phosphene's own code, not the engine's.

The vendored engine (`lyra`, from vanch007/mlx-Yue) has no adapter support and
is installed from a pinned SHA by `scripts/pinokio/music_sync.sh`, so anything
written inside `yue2-mlx/` is lost on the next sync. This module therefore
lives in OUR repo and works on the models `lyra` hands back, from the runner.

WHAT AN ADAPTER IS HERE
-----------------------
`y = W x + strength · B (A x)`, with `lora_A: [rank, in]` and `lora_B: [out,
rank]`, in BF16, applied at inference and never merged into the base weights.
Not merging is the point: the AR model runs 8-bit by default in Phosphene, and
a merge would either force BF16 (5 GB more resident) or dequantise-requantise
a 3B model per song. A wrapper costs two small matmuls per projection.

HOW IT ATTACHES WITHOUT RENAMING ANYTHING
-----------------------------------------
`nar.load_nar()` validates the shared AR model by walking
`tree_flatten(ar_model.parameters())` and demanding the exact upstream names
(`model.layers.0.self_attn.q_proj.weight`, ...). A wrapper module holding the
original linear as a child would rename every one of them to `...q_proj.base.
weight` and the acoustic model would refuse to load. So the projection object
is kept and its CLASS is swapped for a generated subclass that adds the delta
after the base call; the adapter matrices hang off the instance through
`object.__setattr__`, the way `lyra` itself keeps runtime constants off the
parameter tree. The parameter tree comes out byte-identical, quantised bases
work unchanged, and removal is one `__class__` assignment back.

MODES (Maestro's semantics, replicated)
---------------------------------------
* `joint`   — the AR and NAR deltas are ordinary additive LoRAs. The decoder
              I/O projections are NOT touched, even when the file carries them
              (`nar_lora_joint_v9` does; we ignore them and say so).
* `separate`— an artist bundle whose NAR file is a complete decoder companion:
              the deltas still add, and `vae2llm` / `llm2vae` are REPLACED by a
              convex blend of every separate companion, weighted by strength.
              Summing them would double the shared decoder adaptation.

Key spellings accepted: the Mothersuperior `layers.{i}...lora_A` layout, an
optional `model.` prefix, PEFT / AI-Toolkit `base_model.model.` prefixes,
`.lora_A.weight` / `.lora_A.default.weight` spellings, kohya `lora_down` /
`lora_up`, and Maestro's own `.A` / `.B`. A PEFT `alpha` scalar, when present,
is folded into B as `alpha / rank` (the trap that flattened the first H3 LoRA
imports); files without one scale by strength alone, as Mothersuperior's do.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import sys

import mlx.core as mx

# The pack layout (which files, which revisions, how a pick is spelled) is
# STDLIB-ONLY and lives beside the fetcher, because the panel imports it at
# boot to draw the picker and must not pull MLX onto that path. One definition,
# two processes.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from scripts.pinokio.music_lora_fetch import (                   # noqa: E402
    INSTRUMENTAL_ADAPTER, JOINT_NAR_ADAPTER, MAX_STRENGTH, USER_SUBDIR,
    pack_adapters, parse_field, parse_picks, parse_spec, resolve_in_pack,
    sha256_of,
    validate_strength,
)

__all__ = [
    "Adapter", "LoRARuntime", "apply_adapters", "branch_of", "check_against",
    "describe_stack", "instrumental_lyrics", "instrumental_recipe", "is_adapted",
    "linear_shape", "load_stack", "normalize_key", "read_adapter",
    "remove_adapters", "style_with_triggers",
    "INSTRUMENTAL_ADAPTER", "JOINT_NAR_ADAPTER", "MAX_STRENGTH", "USER_SUBDIR",
    "pack_adapters", "parse_field", "parse_picks", "parse_spec",
    "resolve_in_pack", "sha256_of",
    "validate_strength",
]

#: One rank ceiling, same as Maestro's. Anything outside this is not a YuE2 LoRA.
MAX_RANK = 128
#: Which attention / MLP container each branch keeps its projections in.
BRANCH_GROUPS = {"ar": ("self_attn", "mlp"), "nar": ("nar_self_attn", "nar_mlp")}
ATTN_PROJECTIONS = ("q_proj", "k_proj", "v_proj", "o_proj")
MLP_PROJECTIONS = ("gate_proj", "up_proj", "down_proj")
#: The acoustic decoder's input/output projections — full weights, not deltas.
IO_MODULES = ("vae2llm", "llm2vae")
MODES = ("joint", "separate")

_ADAPTER_ATTR = "_phosphene_lora"
_ORIGINAL_CLASS_ATTR = "_phosphene_lora_base_class"
_lora_classes: dict[type, type] = {}


# ---------------------------------------------------------------- key mapping

_PREFIXES = ("base_model.model.model.", "base_model.model.", "base_model.",
             "transformer.", "model.")
# Longest first: `.lora_A.default.weight` must not be eaten by `.lora_A.weight`.
_SUFFIXES = (
    (".lora_A.default.weight", ".lora_A"), (".lora_B.default.weight", ".lora_B"),
    (".lora_A.weight", ".lora_A"), (".lora_B.weight", ".lora_B"),
    (".lora_down.weight", ".lora_A"), (".lora_up.weight", ".lora_B"),
    (".lora_down", ".lora_A"), (".lora_up", ".lora_B"),
    (".A", ".lora_A"), (".B", ".lora_B"),
)
_TARGET_RE = re.compile(
    r"^layers\.(\d+)\.(self_attn|mlp|nar_self_attn|nar_mlp)\."
    r"(q_proj|k_proj|v_proj|o_proj|gate_proj|up_proj|down_proj)$")


def normalize_key(key: str) -> str:
    """One spelling for the half-dozen an adapter file may arrive in."""
    key = str(key)
    for prefix in _PREFIXES:
        if key.startswith(prefix):
            key = key[len(prefix):]
            break
    for suffix, canonical in _SUFFIXES:
        if key.endswith(suffix):
            return key[: -len(suffix)] + canonical
    return key


def _describe(name: str) -> tuple[int, str, str] | None:
    match = _TARGET_RE.match(name)
    if match is None:
        return None
    return int(match.group(1)), match.group(2), match.group(3)


def branch_of(names) -> str:
    """`nar` when the file targets the acoustic branch, `ar` otherwise."""
    for name in names:
        described = _describe(name.rsplit(".", 1)[0]) if name.endswith((".lora_A", ".lora_B")) else None
        if described is not None and described[1].startswith("nar_"):
            return "nar"
    return "ar"


# ------------------------------------------------------------------- loading

@dataclass
class Adapter:
    """One adapter file, read and checked, ready to hang on a model."""

    path: Path
    branch: str
    strength: float
    mode: str
    rank: int
    deltas: dict[str, tuple[mx.array, mx.array]]
    io: dict[str, mx.array] = field(default_factory=dict)
    trigger: str = ""
    sha256: str = ""
    ignored_io: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    def summary(self) -> dict:
        """What the sidecar records: enough to reproduce, nothing heavy."""
        return {"file": self.name, "path": str(self.path), "branch": self.branch,
                "strength": round(float(self.strength), 4), "mode": self.mode,
                "rank": self.rank, "targets": len(self.deltas),
                "decoder_io": bool(self.io), "decoder_io_ignored": self.ignored_io,
                "trigger": self.trigger, "sha256": self.sha256}


def read_adapter(path, *, strength: float = 1.0, mode: str = "joint",
                 trigger: str = "", with_sha: bool = False) -> Adapter:
    """Read one `.safetensors` adapter into BF16 deltas, or say exactly why not."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"No LoRA at {path}")
    if path.suffix.lower() != ".safetensors":
        raise ValueError(f"{path.name}: a YuE2 LoRA is a .safetensors file")
    if mode not in MODES:
        raise ValueError(f"Unsupported LoRA mode {mode!r}; use {' or '.join(MODES)}")
    strength = validate_strength(strength)
    raw = mx.load(str(path))
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{path.name}: no named tensors")

    tensors: dict[str, mx.array] = {}
    for key, value in raw.items():
        canonical = normalize_key(key)
        if canonical in tensors:
            raise ValueError(f"{path.name}: two tensors map to {canonical!r}")
        tensors[canonical] = value
    branch = branch_of(tensors)

    pairs: dict[str, list] = {}
    alphas: dict[str, float] = {}
    io: dict[str, mx.array] = {}
    unexpected: list[str] = []
    for name, value in tensors.items():
        stem, _, leaf = name.rpartition(".")
        if leaf in ("lora_A", "lora_B") and _describe(stem) is not None:
            pairs.setdefault(stem, [None, None])[0 if leaf == "lora_A" else 1] = value
        elif leaf == "alpha" and _describe(stem) is not None:
            alphas[stem] = float(value.reshape(-1)[0].item())
        elif stem in IO_MODULES and leaf in ("weight", "bias"):
            io[name] = value
        else:
            unexpected.append(name)
    if unexpected:
        raise ValueError(f"{path.name}: unsupported tensor targets: "
                         f"{', '.join(sorted(unexpected)[:5])}")
    if not pairs:
        raise ValueError(f"{path.name}: no LoRA matrices for the AR or acoustic branch")

    ranks: set[int] = set()
    deltas: dict[str, tuple[mx.array, mx.array]] = {}
    for target in sorted(pairs):
        a, b = pairs[target]
        if a is None or b is None:
            raise ValueError(f"{path.name}: {target} is missing its "
                             f"{'lora_A' if a is None else 'lora_B'} half")
        if a.ndim != 2 or b.ndim != 2 or b.shape[1] != a.shape[0]:
            raise ValueError(f"{path.name}: {target} is not a [rank,in]/[out,rank] pair")
        rank = int(a.shape[0])
        if not 1 <= rank <= MAX_RANK:
            raise ValueError(f"{path.name}: rank {rank} is outside 1–{MAX_RANK}")
        if not (mx.issubdtype(a.dtype, mx.floating) and mx.issubdtype(b.dtype, mx.floating)):
            raise ValueError(f"{path.name}: {target} is not floating point")
        ranks.add(rank)
        a, b = a.astype(mx.bfloat16), b.astype(mx.bfloat16)
        if target in alphas:
            # PEFT / AI-Toolkit exports carry the scale separately. Folding it
            # here keeps "strength 1.0" meaning the adapter as it was trained.
            b = (b.astype(mx.float32) * (alphas[target] / rank)).astype(mx.bfloat16)
        deltas[target] = (a, b)
    if len(ranks) != 1:
        raise ValueError(f"{path.name}: one rank per adapter, found {sorted(ranks)}")
    for name, value in io.items():
        io[name] = value.astype(mx.bfloat16)
    if io and set(io) != {f"{m}.{k}" for m in IO_MODULES for k in ("weight", "bias")}:
        raise ValueError(f"{path.name}: an incomplete decoder companion "
                         f"({', '.join(sorted(io))})")
    if branch == "ar" and io:
        raise ValueError(f"{path.name}: decoder projections belong to an acoustic adapter")

    arrays = [x for pair in deltas.values() for x in pair] + list(io.values())
    mx.eval(arrays)
    for array in arrays:
        if not bool(mx.isfinite(array).all()):
            raise ValueError(f"{path.name}: adapter weights contain non-finite values")

    ignored = bool(io) and mode == "joint"
    return Adapter(path=path, branch=branch, strength=strength, mode=mode,
                   rank=ranks.pop(), deltas=deltas, io={} if ignored else io,
                   trigger=str(trigger or "").strip(),
                   sha256=sha256_of(path) if with_sha else "",
                   ignored_io=ignored)


# ------------------------------------------------------------------ applying

def _lora_class(base: type) -> type:
    """A subclass of the projection's own class that adds the deltas."""
    if base in _lora_classes:
        return _lora_classes[base]

    class _LoRAProjection(base):                                  # type: ignore[misc]
        def __call__(self, x, *args, **kwargs):
            out = super().__call__(x, *args, **kwargs)
            for a, b, scale in object.__getattribute__(self, _ADAPTER_ATTR):
                delta = (x.astype(a.dtype) @ a.T) @ b.T
                out = out + delta.astype(out.dtype) * scale
            return out

    _LoRAProjection.__name__ = f"LoRA{base.__name__}"
    _LoRAProjection.__qualname__ = _LoRAProjection.__name__
    _lora_classes[base] = _LoRAProjection
    return _LoRAProjection


def _projection_slots(model, branch: str) -> dict[str, tuple[object, str]]:
    """`layers.{i}.{group}.{proj}` → (owning module, attribute name)."""
    if branch not in BRANCH_GROUPS:
        raise ValueError(f"branch must be one of {', '.join(BRANCH_GROUPS)}")
    attn_name, mlp_name = BRANCH_GROUPS[branch]
    backbone = getattr(model, "model", None)
    layers = getattr(backbone, "layers", None)
    if layers is None:
        raise TypeError("This model has no transformer layers to adapt")
    slots: dict[str, tuple[object, str]] = {}
    for index, layer in enumerate(layers):
        for group, projections in ((attn_name, ATTN_PROJECTIONS),
                                   (mlp_name, MLP_PROJECTIONS)):
            container = getattr(layer, group, None)
            if container is None:
                continue
            for projection in projections:
                if hasattr(container, projection):
                    slots[f"layers.{index}.{group}.{projection}"] = (container, projection)
    return slots


def linear_shape(module) -> tuple[int, int]:
    """(out_features, in_features) for a plain or a quantised linear."""
    weight = module.weight
    out = int(weight.shape[0])
    scales = module.get("scales") if hasattr(module, "get") else None
    if scales is not None:
        return out, int(scales.shape[1]) * int(module.group_size)
    return out, int(weight.shape[1])


def check_against(adapter: Adapter, model) -> list[str]:
    """Shape-check every delta against the model that will run it."""
    slots = _projection_slots(model, adapter.branch)
    problems: list[str] = []
    for target, (a, b) in adapter.deltas.items():
        slot = slots.get(target)
        if slot is None:
            problems.append(f"{target} is not a projection of this model")
            continue
        out_features, in_features = linear_shape(getattr(slot[0], slot[1]))
        if int(a.shape[1]) != in_features or int(b.shape[0]) != out_features:
            problems.append(
                f"{target} expects [rank,{in_features}]/[{out_features},rank], "
                f"got {tuple(a.shape)}/{tuple(b.shape)}")
    if adapter.io:
        for name, value in adapter.io.items():
            module = getattr(model, name.split(".")[0], None)
            reference = None if module is None else module.get(name.split(".")[1])
            if reference is None:
                problems.append(f"{name} has no counterpart on this model")
            elif tuple(value.shape) != tuple(reference.shape):
                problems.append(f"{name} is {tuple(value.shape)}, "
                                f"expected {tuple(reference.shape)}")
    return problems


def apply_adapters(model, branch: str, adapters) -> int:
    """Hang every adapter for `branch` on `model`. Returns projections wrapped.

    Idempotent per model: applying twice replaces the stack rather than
    doubling it, so a pipeline that reloads a model mid-song cannot drift.
    """
    adapters = [a for a in adapters if a.branch == branch and a.strength > 0]
    remove_adapters(model)
    if not adapters:
        return 0
    for adapter in adapters:
        problems = check_against(adapter, model)
        if problems:
            raise ValueError(f"{adapter.name}: " + "; ".join(problems[:3]))

    stacks: dict[str, list] = {}
    for adapter in adapters:
        for target, (a, b) in adapter.deltas.items():
            stacks.setdefault(target, []).append((a, b, float(adapter.strength)))

    slots = _projection_slots(model, branch)
    for target, stack in stacks.items():
        container, attribute = slots[target]
        module = getattr(container, attribute)
        object.__setattr__(module, _ORIGINAL_CLASS_ATTR, type(module))
        object.__setattr__(module, _ADAPTER_ATTR, tuple(stack))
        module.__class__ = _lora_class(type(module))

    # A complete decoder companion is not a delta: the separate bundles blend
    # their I/O projections by relative strength, and replace the base ones.
    companions = [a for a in adapters if a.mode == "separate" and a.io]
    if companions:
        total = sum(a.strength for a in companions)
        blended: dict[str, mx.array] = {}
        for adapter in companions:
            share = (adapter.strength / total) if total else (1.0 / len(companions))
            for name, value in adapter.io.items():
                scaled = value.astype(mx.float32) * share
                blended[name] = scaled if name not in blended else blended[name] + scaled
        for name, value in blended.items():
            module_name, leaf = name.split(".")
            module = getattr(model, module_name)
            original = getattr(model, "_phosphene_lora_io", None)
            if original is None:
                original = {}
                object.__setattr__(model, "_phosphene_lora_io", original)
            original.setdefault(name, module[leaf])
            module[leaf] = value.astype(mx.bfloat16)
        mx.eval([getattr(model, m)[k] for m in IO_MODULES for k in ("weight", "bias")])
    return len(stacks)


def remove_adapters(model) -> int:
    """Put every adapted projection (and any replaced decoder I/O) back."""
    restored = 0
    for branch in BRANCH_GROUPS:
        try:
            slots = _projection_slots(model, branch)
        except TypeError:
            return restored
        for container, attribute in slots.values():
            module = getattr(container, attribute)
            original = getattr(module, _ORIGINAL_CLASS_ATTR, None)
            if original is None:
                continue
            module.__class__ = original
            object.__delattr__(module, _ORIGINAL_CLASS_ATTR)
            object.__delattr__(module, _ADAPTER_ATTR)
            restored += 1
    saved = getattr(model, "_phosphene_lora_io", None)
    if saved:
        for name, value in saved.items():
            module_name, leaf = name.split(".")
            getattr(model, module_name)[leaf] = value
        object.__delattr__(model, "_phosphene_lora_io")
    return restored


def is_adapted(model) -> bool:
    try:
        slots = _projection_slots(model, "ar")
        slots.update(_projection_slots(model, "nar"))
    except TypeError:
        return False
    return any(hasattr(getattr(c, a), _ORIGINAL_CLASS_ATTR) for c, a in slots.values())


# ------------------------------------------------------- pipeline attachment

class LoRARuntime:
    """Keeps a pipeline's models adapted across its own lazy reloads.

    `YuE2Pipeline._load_model()` builds the AR model for planning, a second
    BF16 AR for acoustic conditioning, and the acoustic model itself — each on
    first use, and the 8-bit path drops and rebuilds them mid-song. Wrapping
    that one method is the only hook that sees every model the song will use.
    """

    def __init__(self, adapters):
        self.adapters = list(adapters)
        self._pipeline = None
        self._original = None
        self._applied: dict[int, int] = {}

    @property
    def active(self) -> bool:
        return bool(self.adapters)

    def summary(self) -> list[dict]:
        return [a.summary() for a in self.adapters]

    def attach(self, pipeline):
        if not self.adapters:
            return self
        if self._pipeline is not None:
            raise RuntimeError("This LoRA runtime is already attached")
        self._pipeline, self._original = pipeline, pipeline._load_model

        def load_model(for_nar=False):
            model = self._original(for_nar=for_nar)
            self.refresh()
            return model

        pipeline._load_model = load_model
        return self

    def refresh(self) -> None:
        """Apply the stack to whichever models the pipeline holds right now."""
        pipeline = self._pipeline
        if pipeline is None:
            return
        for attribute, branch in (("_ar", "ar"), ("_bf16_ar", "ar"), ("_nar", "nar")):
            model = getattr(pipeline, attribute, None)
            if model is None:
                continue
            token = id(model)
            if self._applied.get(token) == len(self.adapters) and is_adapted(model):
                continue
            count = apply_adapters(model, branch, self.adapters)
            if count:
                self._applied[token] = len(self.adapters)

    def detach(self) -> None:
        pipeline, self._pipeline = self._pipeline, None
        if pipeline is None:
            return
        pipeline._load_model = self._original
        self._original = None
        for attribute in ("_ar", "_bf16_ar", "_nar"):
            model = getattr(pipeline, attribute, None)
            if model is not None:
                remove_adapters(model)
        self._applied.clear()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.detach()
        return False


# --------------------------------------------------------- instrumental mode

#: Maestro's own prompt. A bare `[instrumental]` lets the model pick sections.
INSTRUMENTAL_PROMPT = "[instrumental]"
INSTRUMENTAL_COT = "full"
_SECTION_RE = re.compile(
    r"\[(intro|verse|pre-chorus|chorus|bridge|outro)(?: (\d+):([0-5]\d)-(\d+):([0-5]\d))?\]",
    re.IGNORECASE)


def instrumental_lyrics(lyrics) -> str:
    """Bare section tags survive; anything else becomes `[instrumental]`.

    Sung prose fed to this adapter is what produces a vocal-like track, so the
    three caption forms Maestro accepts are the only ones that pass through.
    """
    lines = str(lyrics or "").strip().splitlines()
    plan: list[str] = []
    previous_end = 0
    for line in lines:
        if not line.strip():
            continue
        match = _SECTION_RE.fullmatch(line.strip())
        if match is None:
            return INSTRUMENTAL_PROMPT
        tag, start_min, start_sec, end_min, end_sec = match.groups()
        if start_min is not None:
            start = int(start_min) * 60 + int(start_sec)
            end = int(end_min) * 60 + int(end_sec)
            if start < previous_end or end <= start:
                return INSTRUMENTAL_PROMPT
            previous_end = end
        plan.append(line.strip().lower())
    return "\n".join(plan) or INSTRUMENTAL_PROMPT


def instrumental_recipe(lora_root, lyrics="") -> dict:
    """Maestro's recipe: the AR adapter at 1.0, full score planning, stock decoder.

    Returns the pieces the runner applies; `adapter` is None when the pack is
    not downloaded, and the caller falls back to the prompt-only instrumental
    that shipped in v4.16 rather than failing a queued song.
    """
    adapter = None
    if lora_root is not None:
        candidate = Path(lora_root).expanduser() / INSTRUMENTAL_ADAPTER
        if candidate.is_file():
            adapter = candidate
    return {"adapter": adapter, "strength": 1.0, "cot": INSTRUMENTAL_COT,
            "lyrics": instrumental_lyrics(lyrics) if adapter else None,
            "decoder": "stock", "pauses_other_loras": True}


def style_with_triggers(style: str, adapters) -> str:
    """Add each adapter's training trigger to the style prompt once."""
    style = str(style or "")
    folded = style.casefold()
    missing: list[str] = []
    for adapter in adapters:
        trigger = (adapter.trigger or "").strip()
        if trigger and trigger.casefold() not in folded and \
                trigger.casefold() not in {m.casefold() for m in missing}:
            missing.append(trigger)
    if not missing:
        return style
    return ", ".join([*missing, style] if style else missing)


def load_stack(specs, *, mode="joint", triggers=(), with_sha=True) -> list[Adapter]:
    """`[(path, strength), ...]` → checked adapters, in the order given."""
    triggers = list(triggers)
    adapters = []
    for index, (path, strength) in enumerate(specs):
        adapters.append(read_adapter(
            path, strength=strength, mode=mode, with_sha=with_sha,
            trigger=triggers[index] if index < len(triggers) else ""))
    return adapters


def describe_stack(adapters, *, instrumental=None) -> dict:
    """The sidecar block. Says what ran, at what strength, from which file."""
    return {"adapters": [a.summary() for a in adapters],
            "modes": sorted({a.mode for a in adapters}),
            "branches": sorted({a.branch for a in adapters}),
            "triggers": [a.trigger for a in adapters if a.trigger],
            "instrumental": instrumental}


if __name__ == "__main__":                                       # pragma: no cover
    import argparse

    parser = argparse.ArgumentParser(description="Inspect a YuE2 LoRA file.")
    parser.add_argument("path", type=Path, nargs="+")
    parsed = parser.parse_args()
    for one in parsed.path:
        adapter = read_adapter(one, with_sha=True)
        print(json.dumps(adapter.summary(), indent=2))
