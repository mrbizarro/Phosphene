#!/usr/bin/env python3
"""A saved LoRA must be the LoRA that was trained (#62 root cause).

`ltx_trainer_mlx.trainer._save_checkpoint` writes each factor as
`np.array(mx.transpose(param).astype(mx.float32))`. The params are float32, so
that is a non-contiguous transposed VIEW, and safetensors.numpy.save_file
(0.8.0) serializes the raw buffer with strides ignored. Every Train-tab
adapter — character and voice — was therefore written as the untransposed
bytes under the transposed shape: same element values, so it attached and
moved the output, but a delta uncorrelated with the trained one and about half
its strength. On the 2-image control
the file as saved fits its images by ~0%; unscrambled, by 41-83%.

`lora_lab.train._patch_contiguous_checkpoint_save` fixes it. Pinned here
through the REAL `_setup_lora` + `_save_checkpoint` on a tiny real LTXModel:
the patched file reloads to exactly the in-memory factors, and the unpatched
one does not (skipped, not failed, on a safetensors that honours strides).
CPU only; no weights, no GPU lock.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import lora_lab.train_character as tc  # noqa: E402

try:
    import mlx.core as mx
    import numpy as np
    from mlx_lm.tuner.lora import LoRALinear
    from safetensors.numpy import load_file

    import lora_lab.train as lab_train
    import ltx_trainer_mlx.trainer as trainer_mod
    from ltx_core_mlx.model.transformer.model import LTXModel, LTXModelConfig
    from ltx_trainer_mlx.config import LtxTrainerConfig
    from ltx_trainer_mlx.trainer import LtxvTrainer

    mx.set_default_device(mx.cpu)
    HAVE_MLX = True
except ImportError:  # pragma: no cover — the gates run in the trainer venv
    HAVE_MLX = False

TINY = dict(num_layers=1, video_dim=64, audio_dim=32, video_num_heads=2,
            audio_num_heads=2, video_head_dim=32, audio_head_dim=16,
            av_cross_num_heads=2, av_cross_head_dim=16)


@unittest.skipUnless(HAVE_MLX, "needs the trainer venv (mlx + ltx_trainer_mlx)")
class CheckpointLayout(unittest.TestCase):
    def setUp(self) -> None:
        self._orig = getattr(trainer_mod, "_phos_orig_save_safetensors", trainer_mod.save_safetensors)
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        trainer_mod.save_safetensors = self._orig
        lab_train._patch_contiguous_checkpoint_save()
        self._tmp.cleanup()

    def _save(self) -> tuple[dict, dict]:
        """Real _setup_lora + _save_checkpoint; returns (in-memory, on-disk)."""
        mx.random.seed(0)
        t = LtxvTrainer.__new__(LtxvTrainer)
        t._transformer = LTXModel(LTXModelConfig(**TINY))
        cfg = tc.resolve_preset("high", {"rank": 4, "alpha": 4, "steps": 10})
        d = tc.build_trainer_config(cfg=cfg, data_root=Path("/d"), output_dir=Path(self._tmp.name))
        t._config = LtxTrainerConfig.model_validate(d)
        t._setup_lora()
        mem = {}
        for path, m in t._transformer.named_modules():
            if isinstance(m, LoRALinear):
                m.lora_b = mx.random.normal(m.lora_b.shape)  # "trained": B away from zero
                mem[path] = (np.array(m.lora_a), np.array(m.lora_b))
        t._global_step, t._checkpoint_paths = 10, []
        return mem, load_file(str(t._save_checkpoint()))

    @staticmethod
    def _cosines(mem: dict, disk: dict) -> list[float]:
        out = []
        for path, (a, b) in mem.items():
            A = disk[f"diffusion_model.{path}.lora_A.weight"]
            B = disk[f"diffusion_model.{path}.lora_B.weight"]
            want, got = b.T @ a.T, B @ A
            out.append(float((want * got).sum() / np.linalg.norm(want) / np.linalg.norm(got)))
        return out

    def test_the_patched_save_is_exactly_the_trained_adapter(self) -> None:
        lab_train._patch_contiguous_checkpoint_save()
        mem, disk = self._save()
        self.assertTrue(mem)
        for path, (a, b) in mem.items():
            np.testing.assert_array_equal(disk[f"diffusion_model.{path}.lora_A.weight"], a.T)
            np.testing.assert_array_equal(disk[f"diffusion_model.{path}.lora_B.weight"], b.T)

    def test_the_unpatched_save_scrambles_the_delta(self) -> None:
        trainer_mod.save_safetensors = self._orig
        mem, disk = self._save()
        cos = self._cosines(mem, disk)
        if min(cos) > 0.999999:
            self.skipTest("this safetensors serializes strides; the patch is a no-op here")
        # Same element values, unrelated delta.
        self.assertLess(max(abs(c) for c in cos), 0.5, cos)

    def test_the_patch_is_idempotent(self) -> None:
        lab_train._patch_contiguous_checkpoint_save()
        first = trainer_mod.save_safetensors
        lab_train._patch_contiguous_checkpoint_save()
        self.assertIs(trainer_mod._phos_orig_save_safetensors, self._orig)
        self.assertIsNot(trainer_mod.save_safetensors, first)  # rebinds, never nests
        mem, disk = self._save()
        self.assertGreater(min(self._cosines(mem, disk)), 0.999999)


if __name__ == "__main__":
    unittest.main()
