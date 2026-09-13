"""The ✦ Quality chip and the image preflight never send a render to an engine
this Mac does not have.

HiDream has been hidden from Image Studio since issue #15 (it needs a lab
checkout no install ships), but the gallery's ✦ Quality chip still swapped the
engine to hidream_quality_inline and auto-submitted, and each attempt died
inside the engine with "HiDream venv python not found" — 81 failed renders
from 12 installs in the week to 2026-09-09. Three things pin the fix: the chip's
engine choice (run in node, the real function), the preflight refusing HiDream
at the door as a refusal rather than an error, and the character sheet's
default engine following the same rule.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))
from extract_panel_js import extract_function  # noqa: E402

NODE = shutil.which("node")


def _run_node(script: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise AssertionError("node failed:\n%s\n%s" % (r.stdout[-2000:], r.stderr[-2000:]))
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class TheChipPicksAnEngineThisMacHas(unittest.TestCase):
    def test_hidream_only_when_installed_and_cached(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        fn = extract_function("remakeQualityEngine", src)
        got = _run_node(fn + """
const out = {
  nothing: remakeQualityEngine({}),
  undefined_map: remakeQualityEngine(undefined),
  hidream_absent: remakeQualityEngine({hidream_quality_inline: {family_installed: false, cached: false}}),
  hidream_listed_not_cached: remakeQualityEngine({hidream_quality_inline: {family_installed: true, cached: false}}),
  hidream_ready: remakeQualityEngine({hidream_quality_inline: {family_installed: true, cached: true}}),
};
console.log(JSON.stringify(out));
""")
        self.assertEqual(got["nothing"], "qwen_edit_high_inline")
        self.assertEqual(got["undefined_map"], "qwen_edit_high_inline")
        self.assertEqual(got["hidream_absent"], "qwen_edit_high_inline")
        self.assertEqual(got["hidream_listed_not_cached"], "qwen_edit_high_inline")
        self.assertEqual(got["hidream_ready"], "hidream_quality_inline")

    def test_the_chip_no_longer_hardcodes_hidream(self):
        src = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        body = extract_function("remakeInQuality", src)
        self.assertNotIn("engineEl.value = 'hidream_quality_inline'", body)
        self.assertIn("remakeQualityEngine(", body)


class ThePreflightRefusesHiDreamAtTheDoor(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="phos_remake_")
        os.environ["LTX_STATE_DIR"] = tmp
        os.environ["LTX_OUTPUT_DIR"] = os.path.join(tmp, "out")
        os.environ["LTX_UPLOADS_DIR"] = os.path.join(tmp, "up")
        os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
        os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
        os.makedirs(os.environ["LTX_OUTPUT_DIR"], exist_ok=True)
        os.makedirs(os.environ["LTX_UPLOADS_DIR"], exist_ok=True)
        sys.path.insert(0, str(ROOT))
        import mlx_ltx_panel as p
        cls.p = p

    def test_hidream_without_the_checkout_is_a_refusal_not_an_error(self):
        p = self.p
        with mock.patch.object(p, "_hidream_available", return_value=False):
            with self.assertRaises(p.RenderRefused) as cm:
                p._preflight_image_job(object(), engine_override="hidream_quality_inline")
        self.assertIn("Reference Edit", str(cm.exception))

    def test_other_engines_get_past_that_gate(self):
        p = self.p
        # The gate is the first check; with HiDream absent a Qwen request
        # must reach the RAM estimate (which raises on a dummy cfg).
        with mock.patch.object(p, "_hidream_available", return_value=False), \
             mock.patch.object(p, "_preflight_estimate_ram_gb", side_effect=KeyError("reached")):
            with self.assertRaises(KeyError):
                p._preflight_image_job(object(), engine_override="qwen_edit_high_inline")

    def test_the_character_sheet_defaults_to_a_shipped_engine(self):
        p = self.p
        with mock.patch.object(p, "_hidream_available", return_value=False), \
             mock.patch.object(p, "_character_safe_id", side_effect=LookupError("stop here")):
            with self.assertRaises(LookupError):
                p.generate_character_sheet("nobody")
        src = (ROOT / "panel" / "routes_characters.py").read_text(encoding="utf-8")
        self.assertNotIn('or "hidream_inline"', src)


if __name__ == "__main__":
    unittest.main()
