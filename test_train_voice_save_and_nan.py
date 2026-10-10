#!/usr/bin/env python3
"""Two 4.19.1 trainer fixes, through the real trainer code on a tiny model.

1. VOICES WERE STILL SAVED SCRAMBLED ON 4.19.0 (#62). `train_audio` applied
   the contiguous-save patch and then "restored" the audio LoRA targets with
   `importlib.reload(ltx_trainer_mlx.trainer)`. A reload re-runs the module's
   `from safetensors.numpy import save_file as save_safetensors`, so the patch
   applied one line earlier was gone and every voice adapter went to disk in
   the scrambled layout 4.19.0 was released to fix. The restore now rebinds
   only `_find_lora_targets`, and the voice run refuses to start without the
   contiguous save.

2. A NaN loss used to train on for hours and finish (or die) as an anonymous
   "exit 1". After NAN_LOSS_ABORT_STEPS non-finite steps in a row the face
   trainer stops with NonFiniteLoss, classified `nan_loss`.

CPU only, no weights, no GPU lock. The end-to-end case runs
`train_character.run_training` itself — every 4.19 patch, the loss sniffer,
the step callback, the checkpoint save — with only `load_ltx_model` swapped
for a one-block LTXModel and three precomputed samples.
"""
from __future__ import annotations

import io
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

try:
    import mlx.core as mx
    import numpy as np
    from mlx_lm.tuner.lora import LoRALinear
    from safetensors.numpy import load_file

    import lora_lab.train as lab_train
    import lora_lab.train_audio as ta
    import lora_lab.train_character as tc
    import ltx_trainer_mlx.progress as tp_mod
    import ltx_trainer_mlx.trainer as trainer_mod
    from lora_lab import failure as F
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
class VoiceSaveStaysContiguous(unittest.TestCase):
    def setUp(self) -> None:
        self._save = getattr(trainer_mod, "_phos_orig_save_safetensors", trainer_mod.save_safetensors)
        self._targets = getattr(trainer_mod, "_phos_orig_find_lora_targets",
                                trainer_mod._find_lora_targets)
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        trainer_mod.save_safetensors = self._save
        lab_train._patch_contiguous_checkpoint_save()
        trainer_mod._find_lora_targets = self._targets
        self._tmp.cleanup()

    def test_restoring_the_audio_targets_keeps_the_contiguous_save(self) -> None:
        """The 4.19.0 bug in one line: this assertion fails there."""
        lab_train._patch_contiguous_checkpoint_save()
        ta._restore_audio_lora_targets()
        self.assertEqual(trainer_mod.save_safetensors.__name__, "save_contiguous")

    def test_the_voice_patch_set_saves_the_adapter_that_was_trained(self) -> None:
        lab_train._patch_lora_target_exclude_audio(True)  # as a face run left it
        ta._apply_trainer_patches(lab_train)
        self.assertIs(trainer_mod._find_lora_targets, self._targets, "audio targets restored")
        mx.random.seed(0)
        t = LtxvTrainer.__new__(LtxvTrainer)
        t._transformer = LTXModel(LTXModelConfig(**TINY))
        cfg = tc.resolve_preset("high", {"rank": 4, "alpha": 4, "steps": 10})
        d = tc.build_trainer_config(cfg=cfg, data_root=Path("/d"), output_dir=Path(self._tmp.name))
        d["model"]["model_path"] = self._tmp.name
        t._config = LtxTrainerConfig.model_validate(d)
        t._setup_lora()
        mem = {}
        for path, m in t._transformer.named_modules():
            if isinstance(m, LoRALinear):
                m.lora_b = mx.random.normal(m.lora_b.shape)
                mem[path] = (np.array(m.lora_a), np.array(m.lora_b))
        self.assertTrue(any("audio_attn" in p for p in mem), "voice runs train the audio paths")
        t._global_step, t._checkpoint_paths = 10, []
        disk = load_file(str(t._save_checkpoint()))
        for path, (a, b) in mem.items():
            np.testing.assert_array_equal(disk[f"diffusion_model.{path}.lora_A.weight"], a.T)
            np.testing.assert_array_equal(disk[f"diffusion_model.{path}.lora_B.weight"], b.T)

    def test_a_voice_run_without_the_contiguous_save_refuses(self) -> None:
        with mock.patch.object(lab_train, "_patch_contiguous_checkpoint_save", lambda: None):
            trainer_mod.save_safetensors = self._save
            with self.assertRaises(RuntimeError):
                ta._apply_trainer_patches(lab_train)


def _fake_load(model_dir=None, **kw):
    mx.random.seed(0)
    return types.SimpleNamespace(transformer=LTXModel(LTXModelConfig(**TINY)),
                                 video_vae_decoder=None, video_vae_encoder=None,
                                 audio_vae_decoder=None, vocoder=None)


def _data(root: Path, nan: bool) -> None:
    lat = root / ".precomputed" / "latents"
    con = root / ".precomputed" / "conditions"
    lat.mkdir(parents=True)
    con.mkdir(parents=True)
    for i in range(3):
        x = mx.random.normal((128, 1, 2, 2))
        if nan:
            x = x * float("nan")
        mx.save_safetensors(str(lat / f"latent_{i:03d}.safetensors"), {
            "latents": x, "num_frames": mx.array(1), "height": mx.array(2),
            "width": mx.array(2), "fps": mx.array(24.0)})
        mx.save_safetensors(str(con / f"condition_{i:03d}.safetensors"), {
            "video_prompt_embeds": mx.random.normal((8, 64)),
            "audio_prompt_embeds": mx.random.normal((8, 32)),
            "prompt_attention_mask": mx.ones((8,))})


@unittest.skipUnless(HAVE_MLX, "needs the trainer venv (mlx + ltx_trainer_mlx)")
class FaceTrainingEndToEnd(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._update = tp_mod.TrainingProgress.update_training
        self._stdout = io.StringIO()

    def tearDown(self) -> None:
        tp_mod.TrainingProgress.update_training = self._update
        self._tmp.cleanup()

    def _run(self, *, nan: bool, steps: int):
        root = Path(self._tmp.name)
        (root / "model").mkdir()
        _data(root / "data", nan)
        cfg = tc.resolve_preset("quick", {"rank": 4, "alpha": 4, "steps": steps, "lr": 1e-3})
        with mock.patch.object(trainer_mod, "load_ltx_model", _fake_load), \
                mock.patch.object(tc, "DEFAULT_MODEL_PATH", str(root / "model")), \
                mock.patch.object(tc, "_REAL_STDOUT", self._stdout):
            return tc.run_training(cfg=cfg, data_root=root / "data",
                                   output_dir=root / "out", estimated_wall_s=5)

    def test_a_short_run_trains_and_saves(self) -> None:
        ck, _ = self._run(nan=False, steps=6)
        self.assertTrue(Path(ck).is_file())
        self.assertIn('"event":"train_progress"', self._stdout.getvalue())

    def test_a_nan_loss_stops_the_run_as_nan_loss(self) -> None:
        with self.assertRaises(F.NonFiniteLoss) as cm:
            self._run(nan=True, steps=F.NAN_LOSS_ABORT_STEPS + 20)
        self.assertEqual(F.classify_exception(cm.exception), "nan_loss")


if __name__ == "__main__":
    unittest.main()
