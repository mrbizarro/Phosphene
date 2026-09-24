#!/usr/bin/env python3
"""A voice trained by `yue2_train_voice.py --install` is used the way it was
trained (M6-04).

The trainer writes `<name>.json` beside the adapter: the training `trigger`
and the `dialect` (`direct` = generate with no score, `score` = with one).
The Studio derived its picker from the filename alone, so the trigger never
reached inference and the default Score setting ("Melody + chords") put a
direct-dialect voice under conditioning it had never seen.

Scratch LoRA folder; the runner's own argument parser reads the argv the
panel builds. No model is loaded.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402
from scripts.pinokio import music_lora_fetch as F                    # noqa: E402


def _runner():
    spec = importlib.util.spec_from_file_location(
        "yue2_run_under_test", ROOT / "scripts" / "music" / "yue2_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TrainedVoice(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "loras"
        user = self.root / "user"
        user.mkdir(parents=True)
        (user / "myvoice.safetensors").write_bytes(b"x" * 64)
        (user / "myvoice.json").write_text(json.dumps({
            "name": "myvoice", "kind": "yue2-ar-voice", "branch": "ar",
            "trigger": "myvoice", "dialect": "direct",
            "generate_with": "--mode off"}))
        (user / "scorevoice.safetensors").write_bytes(b"x" * 64)
        (user / "scorevoice.json").write_text(json.dumps({
            "trigger": "scv", "dialect": "score"}))
        (user / "plain.safetensors").write_bytes(b"x" * 64)
        p = mock.patch.object(P, "MUSIC_LORAS", self.root)
        p.start()
        self.addCleanup(p.stop)

    def _form(self, picks, mode):
        return parse_qs(urlencode({"mode": "music", "music_style": "soul",
                                   "music_mode": mode,
                                   "music_loras": json.dumps(picks)}))

    def test_the_picker_shows_what_the_training_said(self):
        rows = {a["id"]: a for a in F.pack_adapters(self.root)}
        mine = rows["user/myvoice.safetensors"]
        self.assertEqual((mine["trigger"], mine["dialect"]), ("myvoice", "direct"))
        self.assertIn("No score", mine["note"])
        self.assertIn("myvoice", mine["note"])
        self.assertNotIn("trigger", rows["user/plain.safetensors"])

    def test_a_direct_voice_under_a_score_is_refused_before_queueing(self):
        with self.assertRaises(P.MusicRequestError) as cm:
            P.make_job(self._form([{"id": "user/myvoice.safetensors"}], "full"))
        self.assertIn("No score", str(cm.exception))
        with self.assertRaises(P.MusicRequestError):
            P.make_job(self._form([{"id": "user/scorevoice.safetensors"}], "off"))

    def test_the_trigger_reaches_the_runner_paired_with_its_adapter(self):
        job = P.make_job(self._form([{"id": "user/plain.safetensors", "strength": 0.5},
                                     {"id": "user/myvoice.safetensors", "strength": 1}],
                                    "off"))
        paths = {"python": Path("/x/py"), "runner": Path("/x/run.py"),
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        argv = P.music_argv(job, paths, Path(self.tmp.name) / "o.wav")
        runner_args = argv[argv.index("/x/run.py") + 1:] if "/x/run.py" in argv else argv[2:]
        args = _runner().parse_args(runner_args)
        self.assertEqual(args.mode, "off")
        self.assertEqual([Path(s.rsplit(":", 1)[0]).name for s in args.lora],
                         ["plain.safetensors", "myvoice.safetensors"])
        self.assertEqual(args.lora_trigger, ["", "myvoice"])

    def test_the_runner_adds_it_to_the_style_once(self):
        sys.path.insert(0, str(ROOT / "scripts" / "music"))
        import yue2_lora                                             # noqa: PLC0415

        class A:
            def __init__(self, t):
                self.trigger = t

        self.assertEqual(yue2_lora.style_with_triggers("soul", [A(""), A("myvoice")]),
                         "myvoice, soul")

    def test_no_trained_voice_no_trigger_flags(self):
        job = P.make_job(self._form([{"id": "user/plain.safetensors"}], "full"))
        paths = {"python": Path("/x/py"), "runner": Path("/x/run.py"),
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        self.assertNotIn("--lora-trigger",
                         P.music_argv(job, paths, Path(self.tmp.name) / "o.wav"))


if __name__ == "__main__":
    unittest.main()
