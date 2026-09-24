"""H3 FP16 VAE decode is the default (4.16.0), and can be turned off.

perf/vae-2026-09-23 measured the runner's FP16 ViT decode on a real
1152x640/124f latent: 105.1 s -> 90.2 s, 11.1 -> 8.0 GiB, PSNR 72.8 dB. The
runner has carried it since minimax-h3-mlx 6bbed80 as `--vae-dtype float16`;
the panel passes it unless the `h3_vae_fp16` setting (default on since 4.16.0)
or PHOSPHENE_H3_VAE_FP16=0 turns it off. Popen is intercepted: nothing renders.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("LTX_H3_FORCE_CAPABLE", "1")

import mlx_ltx_panel as P                                            # noqa: E402

_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-vae16-"))


class _Intercept(Exception):
    pass


def _argv(settings: dict, env: dict, runner_has_flag: bool = True):
    seen = {}

    def popen(cmd, **kw):
        seen["cmd"] = cmd
        raise _Intercept()
    job = {"id": "vae16", "params": {"engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
                                      "prompt": "a dancer", "seed": "7", "h3_turbo": False}}
    paths = dict(missing=[], repairable=False, dit=_TMP / "dit", python=sys.executable,
                 runner=_TMP / "gen.py", compact_root=_TMP / "c", text_config=_TMP / "t")
    ff = _TMP / "ffmpeg"
    ff.write_text("")
    logs = []
    with ExitStack() as st:
        for name, val in {
                "h3_paths": lambda: paths, "h3_capable": lambda: True,
                "h3_supports_lora": lambda: True, "h3_supports_lora_stack": lambda: False,
                "h3_lora_max_stack": lambda: 1, "h3_dit_choice": lambda: ("bf16", None),
                "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
                "_h3_runner_has_flag": lambda flag: runner_has_flag and flag == "--vae-dtype",
                "get_settings": lambda: dict(settings), "push": logs.append,
                "FFMPEG": ff}.items():
            st.enter_context(unittest.mock.patch.object(P, name, val))
        for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                     "h3_live_preview_ready", "h3_supports_stage_a", "h3_supports_tae_draft"):
            st.enter_context(unittest.mock.patch.object(P, name, lambda: False))
        st.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
        st.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
        st.enter_context(unittest.mock.patch.dict(os.environ, env))
        if "PHOSPHENE_H3_VAE_FP16" not in env:
            os.environ.pop("PHOSPHENE_H3_VAE_FP16", None)
        try:
            P.run_h3_job_inner(job)
        except _Intercept:
            pass
    return seen["cmd"], job["params"], logs


def _fp16(cmd) -> bool:
    return "--vae-dtype" in cmd and cmd[cmd.index("--vae-dtype") + 1] == "float16"


class FP16DecodeIsTheDefault(unittest.TestCase):
    def test_on_by_default(self):
        # Flipped for 4.16.0 after the release A/B: 90.1 s -> 77.4 s decode,
        # no visible difference.
        self.assertTrue(P._settings_defaults()["h3_vae_fp16"])
        cmd, params, _ = _argv({}, {})
        self.assertTrue(_fp16(cmd), cmd)
        self.assertEqual(params["h3_vae_dtype"], "float16")

    def test_the_setting_turns_it_off(self):
        cmd, params, _ = _argv({"h3_vae_fp16": False}, {})
        self.assertNotIn("--vae-dtype", cmd)
        self.assertNotIn("h3_vae_dtype", params)

    def test_the_setting_turns_it_on_and_the_recipe_records_it(self):
        cmd, params, _ = _argv({"h3_vae_fp16": True}, {})
        self.assertTrue(_fp16(cmd), cmd)
        self.assertEqual(params["h3_vae_dtype"], "float16")

    def test_the_env_overrides_the_setting_both_ways(self):
        self.assertTrue(_fp16(_argv({}, {"PHOSPHENE_H3_VAE_FP16": "1"})[0]))
        self.assertNotIn("--vae-dtype", _argv({"h3_vae_fp16": True},
                                              {"PHOSPHENE_H3_VAE_FP16": "0"})[0])

    def test_an_old_runner_is_told_about_not_handed_an_unknown_flag(self):
        cmd, params, logs = _argv({"h3_vae_fp16": True}, {}, runner_has_flag=False)
        self.assertNotIn("--vae-dtype", cmd)
        self.assertNotIn("h3_vae_dtype", params)
        self.assertTrue(any("predates the faster FP16" in l for l in logs), logs)

    def test_the_setting_validates_and_is_readable(self):
        for raw, want in (("1", True), ("on", True), ("0", False), (False, False)):
            out, err = P._validate_settings_patch({"h3_vae_fp16": raw})
            self.assertIsNone(err)
            self.assertIs(out["h3_vae_fp16"], want)
        self.assertIn("h3_vae_fp16", P.get_settings_public())


if __name__ == "__main__":
    unittest.main()
