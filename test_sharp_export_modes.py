"""The Sharp (PiperSR) export script answers every `upscale` value the panel can
hand it.

`run_pipersr_tracked` passes the job's own `upscale` field as `--mode`, and the
API accepts `fit_720p`, `fit_1080p` and `x2`. The script knew two of the three:
a Sharp export at 1080p died with "Unsupported upscale mode" after the whole
render had finished (2026-09-09). This pins all three, both orientations.
"""
import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "scripts" / "upscale_compare_pipersr.py"


def _load():
    spec = importlib.util.spec_from_file_location("upscale_compare_pipersr", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class SharpExportModes(unittest.TestCase):
    def setUp(self):
        self.m = _load()

    def test_every_panel_upscale_value_has_a_canvas(self):
        t = self.m.target_for_mode
        self.assertEqual(t(1024, 576, "fit_720p"), (1280, 720, "720p"))
        self.assertEqual(t(576, 1024, "fit_720p"), (720, 1280, "v720p"))
        self.assertEqual(t(1536, 864, "fit_1080p"), (1920, 1080, "1080p"))
        self.assertEqual(t(864, 1536, "fit_1080p"), (1080, 1920, "v1080p"))
        self.assertEqual(t(640, 384, "x2"), (1280, 768, "up2x"))

    def test_an_unknown_mode_still_refuses(self):
        with self.assertRaises(SystemExit):
            self.m.target_for_mode(1024, 576, "fit_4k")

    def test_the_cli_offers_the_same_three(self):
        src = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('choices=("fit_720p", "fit_1080p", "x2")', src)


if __name__ == "__main__":
    unittest.main()
