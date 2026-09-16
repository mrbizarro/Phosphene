#!/usr/bin/env python3
"""Contract gate: Auto H3 High runs 16 sigma points (15 forwards) per window.

Owner ruling 2026-09-16, from a matched gym A/B: High 1024x576 at 15 forwards is
clearly better than at 8 (the face was blurry at 8). Native stays on its own
constant until its 15-forward test is judged. A measured wall clock only prints
for a cell sampled at the depth it was measured at.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-high-steps-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ["LTX_H3_FORCE_CAPABLE"] = "1"
os.environ.setdefault("LTX_PORT", "8296")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
import storyboard as SB  # noqa: E402


def h3_job(form: dict) -> dict:
    base = {"mode": "t2v", "engine": "h3", "prompt": "a man lifts a dumbbell",
            "h3_turbo": "0"}
    base.update(form)
    return P.make_job({k: [v] for k, v in base.items()})


class TestHighCellsRunFifteenForwards(unittest.TestCase):
    def test_the_constants(self):
        self.assertEqual(P.H3_HIGH_STEPS, 16)
        # Native is deliberately ONE constant away from the same change.
        self.assertEqual(P.H3_NATIVE_STEPS, P.H3_STEPS_DEFAULT)
        self.assertEqual(P.H3_QUALITIES["high"]["steps"], P.H3_HIGH_STEPS)
        self.assertEqual(P.H3_QUALITIES["native"]["steps"], P.H3_NATIVE_STEPS)

    def test_every_high_cell_is_sixteen_points(self):
        for key, cell in P.H3_TIERS.items():
            if cell["quality"] != "high":
                continue
            with self.subTest(cell=key):
                self.assertEqual(cell["steps"], 16)
                self.assertEqual(cell["forwards"], 15 * cell["chain_windows"])

    def test_other_canvases_keep_their_length_count(self):
        for key, cell in P.H3_TIERS.items():
            if cell["quality"] == "high":
                continue
            want = 16 if cell["dense"] else P.H3_STEPS_DEFAULT
            if cell["quality"] == "native":
                want = max(want, P.H3_NATIVE_STEPS)
            with self.subTest(cell=key):
                self.assertEqual(cell["steps"], want)

    def test_a_canvas_never_lowers_a_dense_length(self):
        self.assertEqual(P.H3_TIERS["high_10s_dense"]["steps"], 16)
        self.assertEqual(P.H3_TIERS["native_10s_dense"]["steps"], 16)
        self.assertEqual(P.H3_TIERS["standard_10s_dense"]["steps"], 16)


class TestMeasuredEtaMatchesDepth(unittest.TestCase):
    def test_eight_forward_receipt_no_longer_prices_high(self):
        cell = P.H3_TIERS["high_5s"]
        self.assertFalse(cell["eta_measured"])
        model = P.h3_estimate_minutes(1024, 576, 124, 1, 15)
        self.assertAlmostEqual(cell["eta_min"], round(model, 2), places=2)
        # ~15 x 126 s + ~131 s fixed; well past the old 18.8.
        self.assertGreater(cell["eta_min"], 30.0)

    def test_receipts_at_their_own_depth_still_print(self):
        for key in ("draft_3s", "standard_5s", "standard_10s", "native_5s",
                    "standard_10s_dense"):
            with self.subTest(cell=key):
                self.assertTrue(P.H3_TIERS[key]["eta_measured"])

    def test_every_receipt_names_its_depth(self):
        for k, v in P.H3_MEASURED_ETA.items():
            with self.subTest(key=k):
                self.assertEqual(len(v), 3)
                self.assertGreaterEqual(int(v[2]), 1)

    def test_storyboard_fallback_tracks_the_model(self):
        per_sec = P.H3_TIERS["high_5s"]["eta_min"] * 60 / 5
        self.assertLess(abs(SB._H3_SECS_PER_VIDEO_SEC["high"] - per_sec),
                        0.1 * per_sec)


class TestMakeJobStampsTheDepth(unittest.TestCase):
    def test_auto_high_stamps_sixteen(self):
        p = h3_job({"h3_quality": "high", "h3_length": "5s"})["params"]
        self.assertEqual(p["h3_tier"], "high_5s")
        self.assertEqual(p["steps"], 16)
        self.assertEqual(p["h3_steps"], 0)

    def test_auto_standard_and_native_stay_at_nine(self):
        for q in ("standard", "native"):
            with self.subTest(quality=q):
                p = h3_job({"h3_quality": q, "h3_length": "5s"})["params"]
                self.assertEqual(p["steps"], P.H3_TIERS[f"{q}_5s"]["steps"])

    def test_a_pinned_count_still_wins_on_high(self):
        p = h3_job({"h3_quality": "high", "h3_length": "5s",
                    "h3_steps": "12"})["params"]
        self.assertEqual(p["steps"], 12)


class TestUiReadsTheCell(unittest.TestCase):
    def test_no_hardcoded_eight_forward_title(self):
        html = (ROOT / "webapp" / "index.html").read_text()
        self.assertNotIn("9 sigma points, 8 forwards per window", html)
        js = (ROOT / "webapp" / "js" / "engines.js").read_text()
        self.assertIn("function _h3SyncSamplerTitles", js)
        self.assertRegex(js, re.compile(r"renderH3Turbo\(\)\s*\{[\s\S]*?_h3SyncSamplerTitles\(\)"))


if __name__ == "__main__":
    unittest.main()
