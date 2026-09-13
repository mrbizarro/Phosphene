#!/usr/bin/env python3
"""Contract gate for LTX geometry normalization in make_job.

Every registered LTX lane is two-stage: the engine floors the canvas to /64
and delivers frames on the 8k+1 grid. make_job must record the numbers the
render will actually produce — a job claiming 1000×500×100 for a render that
ships 960×448×97 is the CUSTOMIZE audit's "Width × Height LIES" row.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-geom-grid-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8299")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def job_for(form: dict) -> dict:
    base = {"mode": "t2v", "engine": "ltx", "prompt": "a calm harbor at dawn",
            "quality": "balanced"}
    base.update(form)
    return P.make_job({k: [v] for k, v in base.items()})


class TestLtxGeometryGrid(unittest.TestCase):
    def test_off_grid_canvas_is_floored_to_64(self):
        p = job_for({"width": "1000", "height": "500"})["params"]
        self.assertEqual((p["width"], p["height"]), (960, 448))

    def test_on_grid_canvas_is_untouched(self):
        p = job_for({"width": "1024", "height": "576"})["params"]
        self.assertEqual((p["width"], p["height"]), (1024, 576))

    def test_off_grid_frames_floor_to_8k_plus_1_never_up(self):
        # Math.round-style snapping turned 100 into 105 — more frames than
        # asked for. The rule is floor: 100 → 97.
        p = job_for({"frames": "100"})["params"]
        self.assertEqual(p["frames"], 97)

    def test_on_grid_frames_untouched_and_one_frame_allowed(self):
        self.assertEqual(job_for({"frames": "121"})["params"]["frames"], 121)
        self.assertEqual(job_for({"frames": "1"})["params"]["frames"], 1)

    def test_tiny_canvas_floors_to_64_not_zero(self):
        p = job_for({"width": "60", "height": "40"})["params"]
        self.assertEqual((p["width"], p["height"]), (64, 64))

    def test_h3_geometry_still_comes_from_the_tier_cell(self):
        # The H3 lane stamps the (quality × length) cell's own geometry over
        # the form; the LTX grid must not touch it (H3 frames ride 17n+5).
        p = job_for({"engine": "h3", "width": "1000", "height": "500",
                     "frames": "100"})["params"]
        tier = P.H3_TIERS[p["h3_tier"]]
        self.assertEqual((p["width"], p["height"], p["frames"]),
                         (tier["width"], tier["height"], tier["frames"]))



class ControlsHideWithTheirParents(unittest.TestCase):
    """Reported on Pinokio (fuschichou): the Remix tool row stayed on screen
    on every non-Video surface. #remixSubGroup is a SIBLING of the mode bar,
    not a child — it sits outside #genForm — so hiding #modeGroup left the row
    behind as a stray control on Audio, Train, Storyboard, Editor, Characters
    and Studio."""

    SURFACES = ("train", "audio", "storyboard", "editor", "characters",
                "studio", "oneshot")

    @classmethod
    def setUpClass(cls):
        # These rules are CSS, and the panel's CSS lives on disk at
        # webapp/style/panel.css since the slice-1 extraction
        # (docs/ARCHITECTURE.md) — assert against the stylesheet the
        # browser actually loads, not the Python source.
        cls.src = (Path(__file__).resolve().parent
                   / "webapp" / "style" / "panel.css").read_text()

    def test_the_remix_row_hides_wherever_the_mode_bar_hides(self):
        for wf in self.SURFACES:
            with self.subTest(workflow=wf):
                self.assertIn('body[data-workflow="%s"] #modeGroup,' % wf, self.src)
                self.assertIn('body[data-workflow="%s"] #remixSubGroup,' % wf,
                              self.src)

    def test_neither_id_is_ever_listed_without_the_other(self):
        # The rule that keeps the seventh surface from repeating it.
        mode = self.src.count('] #modeGroup,')
        remix = self.src.count('] #remixSubGroup,')
        self.assertEqual(mode, remix)
        self.assertEqual(remix, len(self.SURFACES))


class TheA2VWarningFollowsTheReports(unittest.TestCase):
    """Issue #46. The four reported datapoints cannot be separated by any
    frames x area constant — 832x480x721 = 287.9 Mpx holds together while
    1024x576x481 = 283.7 Mpx falls apart, a SMALLER product failing. They
    separate cleanly on PER-FRAME AREA, so the canvas is the lever."""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent
        # Panel source + the page (webapp/index.html since slice 2 of the
        # extraction — docs/ARCHITECTURE.md): these assertions span both.
        cls.src = (root / "mlx_ltx_panel.py").read_text() + "\n" + (
            root / "webapp" / "index.html").read_text()
        for _m in sorted((root / "webapp" / "js").glob("*.js")):
            cls.src += "\n" + _m.read_text()

    def test_the_refuted_budget_is_gone(self):
        self.assertNotIn("A2V_PIXEL_BUDGET", self.src)

    def test_the_knee_sits_between_the_clean_and_the_failing_reports(self):
        self.assertIn("const A2V_AREA_KNEE = 0.45e6;", self.src)
        self.assertLess(832 * 480 / 1e6, 0.45)      # highest clean: 0.399
        self.assertLess(0.45, 1024 * 576 / 1e6)     # lowest failing: 0.590

    def test_a_clean_canvas_is_never_warned_about_in_range(self):
        fn = self.src[self.src.index("function audioStudioDurationChanged"):]
        fn = fn[:fn.index("async function audioStudioEnhancePrompt")]
        self.assertIn("area > A2V_AREA_KNEE && frames > A2V_KNEE_FRAMES", fn)
        # 640x480 at the full 20 s is the render the old rule shouted at.
        self.assertLess(640 * 480, 0.45e6)

    def test_the_copy_names_the_canvas_and_keeps_its_provenance(self):
        fn = self.src[self.src.index("function audioStudioDurationChanged"):]
        fn = fn[:fn.index("async function audioStudioEnhancePrompt")]
        self.assertIn("is the <b>canvas</b>, not the length", fn)
        self.assertIn("field reports, not a limit measured here", fn)
        self.assertIn("issues/46", fn)
        self.assertIn("A2V_KNEE_FRAMES", fn)


if __name__ == "__main__":
    unittest.main(verbosity=2)
