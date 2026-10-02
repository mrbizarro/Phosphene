"""Detect and repair LoRA files the trainer wrote scrambled (#62 root cause).

Every Train-tab adapter (character and voice) saved by a venv with
safetensors 0.8.0 (released 2026-06-09; the packages require only
``safetensors>=0.4.0``) holds each factor's UNTRANSPOSED bytes under the
transposed shape — see ``lora_lab.train._patch_contiguous_checkpoint_save``.
The element values are all there, so the file can be repaired exactly:

    lora_A (r, in)  := file_A.reshape(in, r).T
    lora_B (out, r) := file_B.reshape(r, out).T

Applying that to a file that was saved correctly would scramble it, so the
repair is gated on a content check rather than on dates or versions. In a
real adapter the rank rows of ``lora_A`` share the input-channel magnitude
profile (the gradient into ``lora_a`` is scaled by the input activations), so
``|A|``'s rows correlate; scrambling interleaves rank components and input
channels and the correlation collapses to ~0. The check scores both readings
and only calls a file scrambled when the unscrambled reading wins by
``MARGIN``. Measured: a Phosphene 2-image run 0.0008 as saved vs 0.0146
unscrambled, ``valeriosan_v5`` 0.0015 vs 0.0402, and a third-party PyTorch
LoRA (DoctorDiffusion Colorizer) 0.0050 as-is vs 0.0006 "unscrambled".

    python -m lora_lab.repair_scrambled_lora FILE.safetensors [...]          # report only
    python -m lora_lab.repair_scrambled_lora FILE.safetensors --out FIXED.safetensors
    python -m lora_lab.repair_scrambled_lora FILE.safetensors --in-place     # keeps FILE.scrambled.bak
"""

from __future__ import annotations

import argparse
import json
import shutil
import statistics
import sys
from pathlib import Path

import numpy as np

MARGIN = 3.0
MIN_SCORE = 0.005  # real winners measured 0.0118-0.0406; losers <= 0.0032
MAX_MODULES = 96
_A, _B = ".lora_A.weight", ".lora_B.weight"


def load_weights(path: Path) -> dict[str, np.ndarray]:
    """float32 numpy view of every tensor; bf16 files go through MLX."""
    try:
        from safetensors.numpy import load_file
        return {k: v.astype(np.float32, copy=False) for k, v in load_file(str(path)).items()}
    except TypeError:  # bfloat16 is not a numpy dtype
        import mlx.core as mx
        return {k: np.array(v.astype(mx.float32)) for k, v in mx.load(str(path)).items()}


def _row_corr(a: np.ndarray) -> float:
    """Mean off-diagonal correlation of |A|'s rows across the input axis."""
    m = np.abs(a)
    m = (m - m.mean(1, keepdims=True)) / (m.std(1, keepdims=True) + 1e-12)
    c = m @ m.T / m.shape[1]
    r = c.shape[0]
    return float((c.sum() - np.trace(c)) / (r * r - r)) if r > 1 else 0.0


def _unscramble_a(a: np.ndarray) -> np.ndarray:
    r, n_in = a.shape
    return np.ascontiguousarray(a).reshape(n_in, r).T


def scramble_scores(weights: dict[str, np.ndarray]) -> tuple[float, float, int, float]:
    """(as-is score, unscrambled-reading score, modules scored, noise floor).

    The floor is ~6 sigma of the score structureless noise would produce
    (one row-pair correlation over n_in samples has sd ~1/sqrt(n_in); the
    score averages r(r-1) of them), and never below MIN_SCORE.
    """
    keys = sorted(k for k, v in weights.items() if k.endswith(_A) and v.ndim == 2 and v.shape[0] > 1)
    keys = keys[:: max(1, len(keys) // MAX_MODULES)][:MAX_MODULES]
    if not keys:
        return 0.0, 0.0, 0, MIN_SCORE
    as_is = [_row_corr(weights[k]) for k in keys]
    unscr = [_row_corr(_unscramble_a(weights[k])) for k in keys]
    r, n_in = weights[keys[0]].shape
    floor = max(MIN_SCORE, 6.0 / np.sqrt(n_in * r * (r - 1)))
    return statistics.median(as_is), statistics.median(unscr), len(keys), float(floor)


def classify(as_is: float, unscr: float, floor: float = MIN_SCORE) -> str:
    """'scrambled', 'ok', or 'undetermined' (never guess on a close call).

    A reading wins only if it clears the noise floor AND beats the other by
    MARGIN; anything else — including structureless noise — is undetermined.
    """
    if unscr >= floor and unscr >= MARGIN * max(as_is, 1e-6):
        return "scrambled"
    if as_is >= floor and as_is >= MARGIN * max(unscr, 1e-6):
        return "ok"
    return "undetermined"


def unscramble(weights: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for k, v in weights.items():
        if k.endswith(_A):
            out[k] = np.ascontiguousarray(_unscramble_a(v))
        elif k.endswith(_B):
            n_out, r = v.shape
            out[k] = np.ascontiguousarray(np.ascontiguousarray(v).reshape(r, n_out).T)
        else:
            out[k] = np.ascontiguousarray(v)
    return out


def _note_sidecar(path: Path, report: dict) -> None:
    sidecar = path.with_suffix(path.suffix + ".json")
    if not sidecar.exists():
        return
    try:
        data = json.loads(sidecar.read_text())
    except (OSError, ValueError):
        return
    data["repaired_scrambled_save"] = report
    sidecar.write_text(json.dumps(data, indent=2))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("files", nargs="+", type=Path)
    g = p.add_mutually_exclusive_group()
    g.add_argument("--out", type=Path, help="write the repaired copy here (one input file only)")
    g.add_argument("--in-place", action="store_true", help="repair in place, keeping <file>.scrambled.bak")
    args = p.parse_args(argv)
    if args.out and len(args.files) != 1:
        p.error("--out takes exactly one input file")

    worst = 0
    for f in args.files:
        w = load_weights(f)
        as_is, unscr, n, floor = scramble_scores(w)
        verdict = classify(as_is, unscr, floor)
        print(f"{f}: {verdict}  (as-is {as_is:+.4f}, unscrambled {unscr:+.4f}, {n} modules)")
        if verdict != "scrambled" or not (args.out or args.in_place):
            worst = max(worst, 0 if verdict != "undetermined" else 2)
            continue
        from safetensors.numpy import save_file
        fixed = unscramble(w)
        report = {"as_is": round(as_is, 5), "unscrambled": round(unscr, 5), "modules_scored": n}
        if args.out:
            save_file(fixed, str(args.out))
            print(f"  wrote repaired copy -> {args.out}")
        else:
            backup = f.with_name(f.name + ".scrambled.bak")
            if backup.exists():
                print(f"  refusing: {backup} already exists", file=sys.stderr)
                worst = 1
                continue
            shutil.copy2(f, backup)
            save_file(fixed, str(f))
            _note_sidecar(f, report)
            print(f"  repaired in place (original kept as {backup.name})")
    return worst


if __name__ == "__main__":
    sys.exit(main())
