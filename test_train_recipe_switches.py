#!/usr/bin/env python3
"""The iter7 training-recipe switches from the #62 trainer comparison.

docs/ISSUE_62_VALERIOSAN_2026-09-30.md ("Comparison against known-working
trainers") lists four places the panel's trainer does something neither the
Lightricks ltx-trainer nor ai-toolkit does. Each is now a spec switch. The default
is the legacy recipe (the screen in that document found the reference values
halve fit at equal steps with no gain in trigger binding); the other values
stay available for A/B runs. What is pinned here:

- the switches resolve, default and refuse typos the same way the panel spec
  reaches them (flat spec -> advanced -> preset);
- the optimizer really is bias-corrected and the schedule really decays;
- the image-only audio patch, run through the REAL vendored loss function on
  a tiny real LTXModel, cuts the audio stream off the video loss (skip_a2v),
  puts the stand-in audio at the video's sigma (matched_sigma), or leaves the
  vendored behaviour alone (clean_zero) — and never touches a run that trains
  real audio;
- the target filter can be switched off and back on in one process.

No weights are loaded and nothing here needs the GPU lock: the model is two
blocks of a few dozen channels.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import lora_lab.train_character as tc  # noqa: E402

try:
    import mlx.core as mx
    import mlx.nn as nn
    from mlx.utils import tree_flatten

    import lora_lab.train as lab_train
    from ltx_core_mlx.model.transformer.model import LTXModel, LTXModelConfig
    from ltx_trainer_mlx.config import LtxTrainerConfig
    from ltx_trainer_mlx.timestep_samplers import ShiftedLogitNormalTimestepSampler
    from ltx_trainer_mlx.trainer import LtxvTrainer
    from ltx_trainer_mlx.training_strategies.text_to_video import (
        TextToVideoConfig,
        TextToVideoStrategy,
    )
    # CPU on purpose: a gate that touches Metal would have to take both GPU
    # locks (CLAUDE.md §7), and a two-block model gains nothing from it.
    mx.set_default_device(mx.cpu)
    HAVE_MLX = True
except ImportError:  # pragma: no cover — the gates run in the trainer venv
    HAVE_MLX = False

# The defaults are the legacy recipe: the #62 screen found the reference-
# trainer values halve fit at equal steps with no gain in trigger binding.
DEFAULTS = {
    "adam_bias_correction": False,
    "scheduler_type": "constant",
    "image_audio": "clean_zero",
    "lora_target_families": "video",
}
LEGACY = {
    "adam_bias_correction": False,
    "scheduler_type": "constant",
    "image_audio": "clean_zero",
    "lora_target_families": "video",
}


class RecipeResolution(unittest.TestCase):
    def test_defaults_are_the_legacy_recipe(self) -> None:
        self.assertEqual(tc.resolve_recipe({}), DEFAULTS)
        self.assertEqual(tc.resolve_recipe(tc.resolve_preset("high", None)), DEFAULTS)

    def test_the_last_allowed_value_is_the_pre_iter7_behaviour(self) -> None:
        legacy = {k: allowed[-1] for k, (_d, allowed) in tc.RECIPE_SWITCHES.items()}
        self.assertEqual(legacy, LEGACY)
        self.assertEqual(tc.resolve_recipe(legacy), LEGACY)

    def test_a_typo_is_refused_not_defaulted(self) -> None:
        with self.assertRaises(ValueError):
            tc.resolve_recipe({"image_audio": "skip"})
        with self.assertRaises(ValueError):
            tc.resolve_recipe({"scheduler_type": "cosine"})

    def test_string_booleans_from_a_hand_edited_spec(self) -> None:
        self.assertFalse(tc.resolve_recipe({"adam_bias_correction": "false"})["adam_bias_correction"])
        self.assertTrue(tc.resolve_recipe({"adam_bias_correction": "true"})["adam_bias_correction"])

    def test_a_flat_panel_spec_reaches_the_recipe(self) -> None:
        spec = {
            "job_id": "t", "trigger": "abctrn", "preset": "high",
            "images_dir": "/x/images", "output_path": "/x/o.safetensors",
            "rank": 32, "steps": 740, **LEGACY,
            "lora_target_families": "all_attention",
        }
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec.json"
            p.write_text(json.dumps(spec))
            loaded = tc.load_spec(p)
        cfg = tc.resolve_preset(loaded["preset"], loaded["advanced"])
        self.assertEqual(tc.resolve_recipe(cfg),
                         {**LEGACY, "lora_target_families": "all_attention"})

    def test_the_trainer_config_carries_the_schedule(self) -> None:
        for sched in ("linear", "constant"):
            cfg = tc.resolve_preset("high", {"scheduler_type": sched})
            d = tc.build_trainer_config(cfg=cfg, data_root=Path("/d"), output_dir=Path("/o"))
            self.assertEqual(d["optimization"]["scheduler_type"], sched)


@unittest.skipUnless(HAVE_MLX, "needs the trainer venv (mlx + ltx_trainer_mlx)")
class OptimizerAndSchedule(unittest.TestCase):
    def _trainer(self, steps: int = 100, **advanced: object) -> "LtxvTrainer":
        cfg = tc.resolve_preset("high", {"steps": steps, **advanced})
        d = tc.build_trainer_config(cfg=cfg, data_root=Path("/d"), output_dir=Path("/o"))
        t = LtxvTrainer.__new__(LtxvTrainer)
        t._config = LtxTrainerConfig.model_validate(d)
        return t

    def test_bias_correction_is_switched_on_and_off(self) -> None:
        for enabled in (True, False):
            lab_train._patch_adam_bias_correction(enabled)
            t = self._trainer()
            t._init_optimizer()
            self.assertIs(t._optimizer.bias_correction, enabled)
        lab_train._patch_adam_bias_correction(True)

    def test_the_first_corrected_step_is_lr_sized_not_3x(self) -> None:
        # Corrected Adam's first step is lr * g/|g|; uncorrected it is
        # lr * 0.1/sqrt(0.001) = 3.16x that. This is the whole of fix A.
        for enabled, want in ((True, 1.0), (False, 0.1 / 0.001 ** 0.5)):
            lab_train._patch_adam_bias_correction(enabled)
            t = self._trainer()
            t._init_optimizer()
            m = nn.Linear(4, 4, bias=False)
            w0 = mx.array(m.weight)
            g = {"weight": mx.full((4, 4), 0.5)}
            t._optimizer.update(m, g)
            step = (w0 - m.weight) / 1e-4
            self.assertAlmostEqual(float(mx.mean(step).item()), want, places=2)
        lab_train._patch_adam_bias_correction(True)

    def test_linear_decays_to_a_tenth_and_constant_does_not(self) -> None:
        t = self._trainer(steps=1000, scheduler_type="linear")
        sched = t._create_schedule()
        self.assertAlmostEqual(sched(0), 1e-4)
        self.assertAlmostEqual(sched(500), 0.55e-4)
        self.assertAlmostEqual(sched(1000), 1e-5)
        self.assertIsNone(self._trainer(scheduler_type="constant")._create_schedule())


# Two blocks, a few dozen channels: the real block code, none of the cost.
TINY = dict(num_layers=2, video_dim=64, audio_dim=32, video_num_heads=2,
            audio_num_heads=2, video_head_dim=32, audio_head_dim=16,
            av_cross_num_heads=2, av_cross_head_dim=16,
            av_ca_timestep_scale_multiplier=1000)


@unittest.skipUnless(HAVE_MLX, "needs the trainer venv (mlx + ltx_trainer_mlx)")
class ImageOnlyAudioStream(unittest.TestCase):
    """Through the vendored `_build_loss_fn`, not a copy of it."""

    def tearDown(self) -> None:
        lab_train._patch_image_only_audio("clean_zero")

    @staticmethod
    def _batch() -> dict:
        mx.random.seed(1)
        return {
            "latents": {
                "latents": mx.random.normal((1, 128, 1, 2, 2)),
                "num_frames": mx.array([1]), "height": mx.array([2]),
                "width": mx.array([2]), "fps": mx.array([24.0]),
            },
            "conditions": {
                "video_prompt_embeds": mx.random.normal((1, 8, 64)),
                "audio_prompt_embeds": mx.random.normal((1, 8, 32)),
                "prompt_attention_mask": mx.ones((1, 8)),
            },
        }

    def _trainer(self, mode: str, transformer: object, with_audio: bool = False) -> "LtxvTrainer":
        lab_train._patch_image_only_audio(mode)
        t = LtxvTrainer.__new__(LtxvTrainer)
        t._training_strategy = TextToVideoStrategy(
            TextToVideoConfig(first_frame_conditioning_p=0.0, with_audio=with_audio))
        t._feature_extractor = None
        t._timestep_sampler = ShiftedLogitNormalTimestepSampler()
        t._transformer = transformer
        return t

    def _grads(self, mode: str) -> tuple[float, dict]:
        mx.random.seed(0)
        model = LTXModel(LTXModelConfig(**TINY))
        t = self._trainer(mode, model)
        loss_fn = t._build_loss_fn()
        batch = self._batch()
        mx.random.seed(7)  # same sigma + noise draw in every arm
        loss, grads = nn.value_and_grad(t._transformer, loss_fn)(batch)
        self.assertIs(t._transformer, model, "the stand-in must be swapped back out")
        return float(loss.item()), dict(tree_flatten(grads))

    @staticmethod
    def _norm(grads: dict, family: str) -> float:
        parts = [mx.sum(v.astype(mx.float32) ** 2) for k, v in grads.items()
                 if f".{family}." in k]
        return float(mx.sqrt(sum(parts)).item())

    def test_skip_a2v_cuts_the_audio_stream_off_the_video_loss(self) -> None:
        _, g = self._grads("skip_a2v")
        for fam in ("audio_to_video_attn", "audio_attn1", "audio_attn2", "video_to_audio_attn"):
            self.assertEqual(self._norm(g, fam), 0.0, fam)
        self.assertGreater(self._norm(g, "attn2"), 0.0, "video text cross-attn still trains")

    def test_the_old_clean_audio_reaches_the_video_and_matched_sigma_differs(self) -> None:
        loss_clean, g_clean = self._grads("clean_zero")
        loss_matched, g_matched = self._grads("matched_sigma")
        self.assertGreater(self._norm(g_clean, "audio_to_video_attn"), 0.0)
        self.assertGreater(self._norm(g_matched, "audio_to_video_attn"), 0.0)
        self.assertNotEqual(loss_clean, loss_matched, "audio sigma must reach the model")

    def test_what_each_mode_hands_the_model(self) -> None:
        seen: list[dict] = []

        def recorder(**kw):
            seen.append(kw)
            return mx.zeros(kw["video_latent"].shape), mx.zeros(kw["audio_latent"].shape)

        for mode in ("clean_zero", "matched_sigma", "skip_a2v"):
            seen.clear()
            batch = self._batch()
            mx.random.seed(7)
            self._trainer(mode, recorder)._build_loss_fn()(batch)
            kw = seen[0]
            ats, sigma = kw["audio_timesteps"], kw["timestep"]
            if mode == "matched_sigma":
                self.assertTrue(bool(mx.all(ats == sigma.reshape(-1, 1)).item()))
                self.assertGreater(float(sigma[0].item()), 0.0)
            else:
                self.assertEqual(float(mx.max(mx.abs(ats)).item()), 0.0)
            self.assertEqual(kw.get("perturbations") is not None, mode == "skip_a2v")

    def test_a_run_that_trains_real_audio_is_never_touched(self) -> None:
        for mode in ("skip_a2v", "matched_sigma"):
            t = self._trainer(mode, object(), with_audio=True)
            self.assertEqual(t._build_loss_fn().__name__, "loss_fn")

    def test_unknown_mode_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            lab_train._patch_image_only_audio("skip")


@unittest.skipUnless(HAVE_MLX, "needs the trainer venv (mlx + ltx_trainer_mlx)")
class TargetFamilies(unittest.TestCase):
    def tearDown(self) -> None:
        lab_train._patch_lora_target_exclude_audio(True)

    def test_the_filter_switches_off_and_back_on(self) -> None:
        import ltx_trainer_mlx.trainer as trainer_mod

        model = LTXModel(LTXModelConfig(**TINY))
        targets = ["to_q", "to_k", "to_v", "to_out"]
        counts = {}
        for enabled in (True, False, True):
            lab_train._patch_lora_target_exclude_audio(enabled)
            counts[enabled] = len(trainer_mod._find_lora_targets(model, targets))
        # Per block: 3 kept families x 4 vs all 6 x 4. Two blocks.
        self.assertEqual(counts[True], 2 * 3 * 4)
        self.assertEqual(counts[False], 2 * 6 * 4)


if __name__ == "__main__":
    unittest.main()
