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


if __name__ == "__main__":
    unittest.main(verbosity=2)
