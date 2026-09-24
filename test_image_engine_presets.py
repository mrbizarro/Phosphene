"""The Quality image tier must not inherit the Lightning distillation LoRA.

The `qwen_edit_high_inline` preset was created "no LoRA" (30211f8), and then
eca173a moved the Lightning 4-step LoRA into `ImageEngineConfig`'s DEFAULT
factory — so any builder that stays silent about `mflux_lora_paths` inherits
a 4-step step-distillation adapter. Quality then fused it at scale 1.0 into
a 40-step true-CFG schedule and produced pure speckle noise against a
perfectly healthy model download (2026-08-30, marcus_images_hq — every
candidate sidecar records the Lightning path). The medium tier carried the
same latent defect behind a comment that claimed "no LoRA".

Also pinned: FBCache stays OFF for long Qwen schedules. Its 28% speedup and
"identical output" were measured at 8 steps; the skip path replays a stale
pre-last-layer state with no consecutive-skip cap, which at 40 steps can
serve dozens of middle steps from one cache.
"""

import unittest
from unittest import mock

import image_engine
import mlx_ltx_panel as panel


def _cfg(override: str):
    return panel._build_image_engine_config(override, {})


class _Spawned(Exception):
    pass


def _argv(cfg) -> list:
    """The mflux argv _generate_mflux would spawn — nothing is spawned."""
    import tempfile
    from pathlib import Path
    seen = {}

    def popen(cmd, *a, **k):
        seen["cmd"] = list(cmd)
        raise _Spawned()
    with tempfile.TemporaryDirectory() as tmp:
        ref = Path(tmp) / "ref.png"
        ref.write_bytes(b"x")
        with mock.patch.object(image_engine, "_resolve_mflux_bin", lambda c: "/nonexistent/mflux-generate-qwen-edit"), \
                mock.patch.object(image_engine, "repair_partial_hf_download", lambda *a, **k: None), \
                mock.patch("subprocess.Popen", popen):
            try:
                image_engine._generate_mflux("a test", 1, 512, 512, Path(tmp), 1, cfg, refs=[str(ref)])
            except _Spawned:
                pass
    return seen["cmd"]


def _guidance(cfg) -> float:
    cmd = _argv(cfg)
    return float(cmd[cmd.index("--guidance") + 1])


class LightningRunsWithoutCfg(unittest.TestCase):
    """Codex UI-03: Reference Edit Fast paired the 4-step Lightning adapter with
    `--guidance 4.0` (the qwen_edit family default, i.e. Quality's true-CFG)."""

    def test_fast_sends_guidance_one(self):
        cmd = _argv(_cfg("qwen_edit_lightning_inline"))
        self.assertEqual(float(cmd[cmd.index("--guidance") + 1]), 1.0)
        self.assertEqual(cmd[cmd.index("--steps") + 1], "4")
        self.assertIn("--lora-paths", cmd)

    def test_the_default_and_promoted_lightning_configs_send_guidance_one(self):
        self.assertEqual(_guidance(image_engine.ImageEngineConfig(
            kind="mflux", mflux_model="Qwen/Qwen-Image-Edit-2511", mflux_family="qwen_edit")), 1.0)
        with mock.patch.object(image_engine, "_resolve_mflux_bin", lambda c: "/x"):
            # a v2.x config: 8 steps, no LoRA — the branch that writes the recipe
            promoted = panel._auto_promote_image_engine_kind(image_engine.ImageEngineConfig(
                kind="mflux", mflux_family="qwen_edit", mflux_steps=8, mflux_lora_paths=[]))
        self.assertEqual(promoted.mflux_guidance, 1.0)
        self.assertEqual(_guidance(promoted), 1.0)

    def test_quality_keeps_its_cfg_and_an_explicit_value_wins(self):
        self.assertEqual(_guidance(_cfg("qwen_edit_high_inline")), 4.0)
        cfg = _cfg("qwen_edit_lightning_inline")
        cfg.mflux_guidance = 2.5
        self.assertEqual(_guidance(cfg), 2.5)


class PresetLoraContracts(unittest.TestCase):
    def test_quality_carries_no_lora(self):
        cfg = _cfg("qwen_edit_high_inline")
        self.assertEqual(cfg.mflux_lora_paths, [])
        self.assertEqual(cfg.mflux_lora_scales, [])

    def test_medium_carries_no_lora(self):
        cfg = _cfg("qwen_edit_inline")
        self.assertEqual(cfg.mflux_lora_paths, [])
        self.assertEqual(cfg.mflux_lora_scales, [])

    def test_lightning_keeps_its_lora(self):
        cfg = _cfg("qwen_edit_lightning_inline")
        self.assertTrue(any("Lightning" in p for p in cfg.mflux_lora_paths),
                        "the fast tier IS the distillation recipe — its LoRA "
                        "must stay")

    def test_the_dataclass_default_is_still_a_trap(self):
        # The default factory still carries Lightning (changing it would
        # silently alter every config built outside the panel's builders).
        # This test documents WHY the builders must be explicit: if this
        # ever becomes empty, the two pins above keep passing and the
        # explicit [] simply becomes redundant.
        cfg = image_engine.ImageEngineConfig(kind="mflux")
        self.assertTrue(any("Lightning" in p for p in cfg.mflux_lora_paths))


class FBCacheStepGate(unittest.TestCase):
    def _env_for(self, steps: int) -> dict:
        cfg = image_engine.ImageEngineConfig(
            kind="mflux", mflux_model="Qwen/Qwen-Image-Edit-2511",
            mflux_family="qwen_edit", mflux_steps=steps,
            mflux_lora_paths=[], mflux_lora_scales=[],
        )
        captured = {}

        def fake_run(cmd, env=None, **kw):
            captured["env"] = dict(env or {})
            raise RuntimeError("stop before spawning")

        with mock.patch.object(image_engine.subprocess, "run", fake_run), \
             mock.patch.object(image_engine.subprocess, "Popen", fake_run,
                               create=True):
            try:
                image_engine.generate(
                    prompt="p", n=1, aspect="16:9", seed=1, config=cfg,
                    refs=["/nonexistent-ref.png"],
                )
            except Exception:
                pass
        return captured.get("env", {})

    def test_gate_shape_in_source(self):
        # The env capture above depends on how far generate() gets before
        # the ref check; the source-level pin is the invariant either way:
        src = open(image_engine.__file__).read()
        self.assertIn('fam == "qwen_edit" and int(config.mflux_steps or 0) <= 12',
                      src)


if __name__ == "__main__":
    unittest.main()
