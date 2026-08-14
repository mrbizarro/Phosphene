"""Fail-closed LTX LoRA/checkpoint compatibility inspection.

The MLX LoRA loaders historically treated an adapter whose keys matched zero
transformer weights as a successful no-op.  A video still came out, which is
especially dangerous for trained characters: the result looks like a plausible
stranger instead of an error.  This module reads only safetensors headers and
checks the exact post-remap module names before a pipeline spends memory or
starts denoising.

It deliberately has no MLX or safetensors dependency.  The panel uses it while
building the library, and the helper uses the same code against the transformer
file it is about to load.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable, Iterable, Mapping


LORA_A_SUFFIX = ".lora_A.weight"
LORA_B_SUFFIX = ".lora_B.weight"
MIN_MATCH_RATIO = 0.90
MAX_HEADER_BYTES = 64 * 1024 * 1024


class LoraCompatibilityError(RuntimeError):
    """The named adapter cannot safely affect the named transformer."""


@dataclass(frozen=True)
class LoraCompatibility:
    lora_path: Path
    transformer_path: Path
    declared_tensors: int
    paired_modules: int
    matched_modules: int
    matched_module_names: frozenset[str]
    minimum_match_ratio: float = MIN_MATCH_RATIO

    @property
    def matched_tensors(self) -> int:
        return self.matched_modules * 2

    @property
    def match_ratio(self) -> float:
        if self.paired_modules <= 0:
            return 0.0
        return self.matched_modules / self.paired_modules

    @property
    def compatible(self) -> bool:
        return (
            self.paired_modules > 0
            and self.matched_modules > 0
            and self.match_ratio >= self.minimum_match_ratio
            and self.paired_modules * 2 == self.declared_tensors
        )

    @property
    def tally(self) -> str:
        return (
            f"FUSED={self.matched_tensors}/{self.declared_tensors} tensors "
            f"({self.matched_modules}/{self.paired_modules} modules)"
        )

    def failure_message(self) -> str:
        name = self.lora_path.name
        target = self.transformer_path.name
        if self.paired_modules == 0:
            detail = "no complete LTX lora_A/lora_B module pairs were found"
        elif self.paired_modules * 2 != self.declared_tensors:
            detail = (
                f"only {self.paired_modules * 2} of {self.declared_tensors} "
                "LoRA tensors form complete A/B pairs"
            )
        else:
            detail = (
                f"only {self.match_ratio:.1%} of its module pairs match; "
                f"at least {self.minimum_match_ratio:.0%} is required"
            )
        return (
            f"LoRA '{name}' is incompatible with {target}: {self.tally}; "
            f"{detail}. The file likely targets a different model or key "
            "layout. Rendering was refused before it could produce a "
            "LoRA-free result."
        )

    def require_compatible(self) -> None:
        if not self.compatible:
            raise LoraCompatibilityError(self.failure_message())


def _stat_key(path: Path) -> tuple[str, int, int]:
    resolved = path.resolve()
    stat = resolved.stat()
    return str(resolved), stat.st_size, stat.st_mtime_ns


@lru_cache(maxsize=512)
def _tensor_header_cached(path_raw: str, size: int, mtime_ns: int) -> dict[str, dict]:
    del mtime_ns  # cache-key material; size is also checked against the header
    path = Path(path_raw)
    with path.open("rb") as fh:
        prefix = fh.read(8)
        if len(prefix) != 8:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': truncated safetensors header."
            )
        header_size = struct.unpack("<Q", prefix)[0]
        if header_size <= 0 or header_size > MAX_HEADER_BYTES:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': invalid safetensors header size "
                f"{header_size}."
            )
        if header_size > size - 8:
            raise LoraCompatibilityError(
                f"Cannot inspect '{path.name}': safetensors header exceeds the file."
            )
        raw = fh.read(header_size)
    try:
        header = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoraCompatibilityError(
            f"Cannot inspect '{path.name}': invalid safetensors JSON header ({exc})."
        ) from exc
    if not isinstance(header, dict):
        raise LoraCompatibilityError(
            f"Cannot inspect '{path.name}': safetensors header is not an object."
        )
    return {
        str(key): value
        for key, value in header.items()
        if key != "__metadata__" and isinstance(value, dict)
    }


def read_tensor_header(path: str | Path) -> dict[str, dict]:
    """Return tensor metadata without materialising any tensor payload."""
    p = Path(path)
    return _tensor_header_cached(*_stat_key(p))


def remap_ltx_lora_key(key: str) -> str:
    """Mirror ``LTXV_LORA_COMFY_RENAMING_MAP`` from the pinned engine."""
    replacements = (
        ("diffusion_model.", ""),
        (".to_out.0.", ".to_out."),
        (".ff.net.0.proj.", ".ff.proj_in."),
        (".ff.net.2.", ".ff.proj_out."),
        (".linear_1.", ".linear1."),
        (".linear_2.", ".linear2."),
        ("audio_ff.net.0.proj.", "audio_ff.proj_in."),
        ("audio_ff.net.2.", "audio_ff.proj_out."),
    )
    for old, new in replacements:
        key = key.replace(old, new)
    return key


def _model_weight_keys(header: Mapping[str, object]) -> set[str]:
    out: set[str] = set()
    for key in header:
        # load_split_safetensors(..., prefix="transformer.") removes this
        # namespace before the LoRA matcher sees the model state dict.
        if key.startswith("transformer."):
            key = key[len("transformer.") :]
        out.add(key)
    return out


def inspect_lora_compatibility(
    lora_path: str | Path,
    transformer_path: str | Path,
    *,
    minimum_match_ratio: float = MIN_MATCH_RATIO,
) -> LoraCompatibility:
    """Compare one LoRA's complete A/B pairs with one transformer header."""
    lora = Path(lora_path)
    transformer = Path(transformer_path)
    lora_keys = {remap_ltx_lora_key(key) for key in read_tensor_header(lora)}
    model_keys = _model_weight_keys(read_tensor_header(transformer))

    a_modules = {
        key[: -len(LORA_A_SUFFIX)]
        for key in lora_keys
        if key.endswith(LORA_A_SUFFIX)
    }
    b_modules = {
        key[: -len(LORA_B_SUFFIX)]
        for key in lora_keys
        if key.endswith(LORA_B_SUFFIX)
    }
    paired = a_modules & b_modules
    matched = {module for module in paired if f"{module}.weight" in model_keys}
    return LoraCompatibility(
        lora_path=lora,
        transformer_path=transformer,
        declared_tensors=len(a_modules) + len(b_modules),
        paired_modules=len(paired),
        matched_modules=len(matched),
        matched_module_names=frozenset(matched),
        minimum_match_ratio=minimum_match_ratio,
    )


def resolve_distilled_transformer(model_dir: str | Path) -> Path | None:
    """Resolve the distilled file using the pinned pipeline's precedence."""
    root = Path(model_dir)
    direct = root / "transformer.safetensors"
    if direct.is_file():
        return direct
    versioned = sorted(root.glob("transformer-distilled-*.safetensors"))
    if versioned:
        return versioned[-1]
    fallback = root / "transformer-distilled.safetensors"
    return fallback if fallback.is_file() else None


def validate_lora_stack(
    loras: Iterable[tuple[str, float]],
    transformer_path: str | Path,
    *,
    reporter: Callable[[str], None] | None = None,
) -> list[LoraCompatibility]:
    """Validate and report every non-zero adapter before transformer load."""
    reports: list[LoraCompatibility] = []
    for index, (path, strength) in enumerate(loras, start=1):
        strength = float(strength)
        if strength == 0.0:
            if reporter is not None:
                reporter(
                    f"LoRA[{index}] file={Path(path).name} strength=0.00 "
                    "SKIPPED=disabled"
                )
            continue
        report = inspect_lora_compatibility(path, transformer_path)
        if reporter is not None:
            reporter(
                f"LoRA[{index}] file={Path(path).name} strength={strength:.2f} "
                f"{report.tally}"
            )
        report.require_compatible()
        reports.append(report)
    return reports


def validate_runtime_application(
    loras: Iterable[tuple[LoraCompatibility, float]],
    applied_module_names: Iterable[str],
    *,
    reporter: Callable[[str], None] | None = None,
) -> None:
    """Refuse when the live loader attached anomalously few expected modules.

    Header compatibility proves that an adapter *can* target the checkpoint.
    This second gate proves that the loader actually routed those modules into
    the live model.  It catches implementation regressions such as a wrong
    namespace/block prefix, which otherwise turn a valid character LoRA into a
    silent no-op after preflight has passed.
    """
    applied = set(applied_module_names)
    for index, (report, strength) in enumerate(loras, start=1):
        strength = float(strength)
        if strength == 0.0:
            continue
        live_count = len(report.matched_module_names & applied)
        expected_count = report.matched_modules
        live_ratio = live_count / expected_count if expected_count else 0.0
        tally = (
            f"FUSED={live_count * 2}/{report.declared_tensors} tensors "
            f"({live_count}/{report.paired_modules} modules)"
        )
        if reporter is not None:
            reporter(
                f"LoRA[{index}] strength={strength:.2f} {tally} "
                f"file={report.lora_path.name}"
            )
        if live_count <= 0 or live_ratio < report.minimum_match_ratio:
            raise LoraCompatibilityError(
                f"LoRA '{report.lora_path.name}' failed to attach to "
                f"{report.transformer_path.name}: {tally}; the live loader "
                f"applied only {live_ratio:.1%} of the compatible modules "
                f"(minimum {report.minimum_match_ratio:.0%}). Rendering was "
                "refused before it could produce a LoRA-free result."
            )
