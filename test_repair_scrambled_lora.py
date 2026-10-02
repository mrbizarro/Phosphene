#!/usr/bin/env python3
"""The scrambled-save repair (#62) must fix exactly the files it should.

`lora_lab.repair_scrambled_lora` reverses the safetensors-0.8.0 raw-buffer
write that scrambled every Train-tab adapter (see
test_train_checkpoint_layout.py). Unscrambling a CORRECT file would scramble
it, so the repair is gated on a content check. Pinned here, numpy only:

- a file written the way the trainer wrote it is called "scrambled" and comes
  back bit-exact to the trained factors;
- a correctly saved file is called "ok" and is never touched, by --in-place
  or otherwise;
- structureless noise is "undetermined", not guessed;
- --in-place keeps a backup and notes the repair in the sidecar.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from lora_lab import repair_scrambled_lora as R  # noqa: E402
from safetensors.numpy import load_file, save_file  # noqa: E402

N_IN, N_OUT, RANK, MODULES = 512, 384, 8, 6


def _trained_like(seed: int) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """lora_a (in, r), lora_b (r, out) with the structure training leaves:
    every rank component scaled by the same per-input-channel profile."""
    rng = np.random.default_rng(seed)
    out = {}
    for m in range(MODULES):
        channel_scale = rng.lognormal(0.0, 1.0, size=(N_IN, 1))
        a = (rng.standard_normal((N_IN, RANK)) * channel_scale * 1e-2).astype(np.float32)
        b = (rng.standard_normal((RANK, N_OUT)) * 1e-3).astype(np.float32)
        out[f"diffusion_model.transformer_blocks.{m}.attn1.to_q"] = (a, b)
    return out


def _as_trainer_wrote(factors: dict) -> dict[str, np.ndarray]:
    """The safetensors-0.8.0 write: transposed shape, untransposed bytes."""
    w = {}
    for k, (a, b) in factors.items():
        w[k + ".lora_A.weight"] = np.ascontiguousarray(a).reshape(RANK, N_IN)
        w[k + ".lora_B.weight"] = np.ascontiguousarray(b).reshape(N_OUT, RANK)
    return w


def _as_correct(factors: dict) -> dict[str, np.ndarray]:
    w = {}
    for k, (a, b) in factors.items():
        w[k + ".lora_A.weight"] = np.ascontiguousarray(a.T)
        w[k + ".lora_B.weight"] = np.ascontiguousarray(b.T)
    return w


class Detection(unittest.TestCase):
    @staticmethod
    def _s(w: dict) -> tuple[float, float, float]:
        as_is, unscr, _n, floor = R.scramble_scores(w)
        return as_is, unscr, floor

    def test_a_trainer_written_file_is_scrambled_and_repairs_exactly(self) -> None:
        f = _trained_like(1)
        bad = _as_trainer_wrote(f)
        self.assertEqual(R.classify(*self._s(bad)), "scrambled")
        fixed = R.unscramble(bad)
        for k, v in _as_correct(f).items():
            np.testing.assert_array_equal(fixed[k], v)

    def test_a_correct_file_is_ok(self) -> None:
        self.assertEqual(R.classify(*self._s(_as_correct(_trained_like(2)))), "ok")

    def test_structureless_noise_is_not_guessed(self) -> None:
        rng = np.random.default_rng(3)
        w = {f"m{i}.lora_A.weight": rng.standard_normal((RANK, N_IN)).astype(np.float32) for i in range(MODULES)}
        self.assertEqual(R.classify(*self._s(w)), "undetermined")


class InPlace(unittest.TestCase):
    def _write(self, d: Path, weights: dict) -> Path:
        p = d / "char_v2.safetensors"
        save_file(weights, str(p))
        p.with_suffix(".safetensors.json").write_text(json.dumps({"trigger": "abctrn"}))
        return p

    def test_repairs_with_backup_and_a_sidecar_note(self) -> None:
        f = _trained_like(4)
        with tempfile.TemporaryDirectory() as d:
            p = self._write(Path(d), _as_trainer_wrote(f))
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            self.assertTrue((Path(d) / "char_v2.safetensors.scrambled.bak").exists())
            got = load_file(str(p))
            for k, v in _as_correct(f).items():
                np.testing.assert_array_equal(got[k], v)
            side = json.loads(p.with_suffix(".safetensors.json").read_text())
            self.assertIn("repaired_scrambled_save", side)
            self.assertEqual(side["trigger"], "abctrn")
            # Second pass: already correct, must not touch it again.
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            np.testing.assert_array_equal(load_file(str(p))[next(iter(got))], got[next(iter(got))])

    def test_a_correct_file_is_never_modified(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = self._write(Path(d), _as_correct(_trained_like(5)))
            before = p.read_bytes()
            self.assertEqual(R.main([str(p), "--in-place"]), 0)
            self.assertEqual(p.read_bytes(), before)
            self.assertFalse((Path(d) / "char_v2.safetensors.scrambled.bak").exists())


if __name__ == "__main__":
    unittest.main()
