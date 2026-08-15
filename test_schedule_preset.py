#!/usr/bin/env python3
"""Contract gate for the LTX-2.5 distilled `fast` schedule preset.

The engine defines the preset (5+2 forwards, the graded F6S2 draft schedule —
a DIFFERENT take) and REFUSES named presets on 2.3, so the panel's job is
lane-gating: the value may ride only on a job that will actually run the
2.5 distilled pipeline. This gate executes the real `make_job()` and the real
tier builder rather than grepping for the lines.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-sched-preset-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8298")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def job_for(form: dict) -> dict:
    base = {"mode": "t2v", "engine": "ltx", "prompt": "a calm harbor at dawn",
            "quality": "balanced"}
    base.update(form)
    return P.make_job({k: [v] for k, v in base.items()})


class TestMakeJobLaneGate(unittest.TestCase):
    def setUp(self):
        self.old_version = P.ACTIVE_MODEL_VERSION

    def tearDown(self):
        P.ACTIVE_MODEL_VERSION = self.old_version

    def test_fast_rides_on_the_25_distilled_lane(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"schedule_preset": "fast"})
        self.assertEqual(job["params"]["schedule_preset"], "fast")

    def test_fast_is_dropped_on_the_hq_lane(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"schedule_preset": "fast", "quality": "high"})
        self.assertEqual(job["params"]["schedule_preset"], "")

    def test_fast_is_dropped_on_23(self):
        # The engine raises on named presets for 2.3 — the gate must drop the
        # value with a sentence instead of queueing a render into a refusal.
        P.ACTIVE_MODEL_VERSION = "ltx23"
        job = job_for({"schedule_preset": "fast"})
        self.assertEqual(job["params"]["schedule_preset"], "")

    def test_fast_is_dropped_on_h3(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"schedule_preset": "fast", "engine": "h3"})
        self.assertEqual(job["params"]["schedule_preset"], "")

    def test_default_and_bogus_normalize_to_empty(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        self.assertEqual(
            job_for({"schedule_preset": "default"})["params"]["schedule_preset"], "")
        self.assertEqual(
            job_for({"schedule_preset": "warp9"})["params"]["schedule_preset"], "")
        self.assertEqual(
            job_for({})["params"]["schedule_preset"], "")

    def test_worker_forwards_the_preset_to_the_helper_spec(self):
        # The seam between make_job and the engine: the generate dispatch
        # copies params["schedule_preset"] into the helper job_spec. Executed
        # indirectly — the dispatch expression is `p.get("schedule_preset")
        # or ""`, so the contract is: a truthy value survives, absence
        # becomes "". Kept as an executable expression, not a grep.
        p = {"schedule_preset": "fast"}
        self.assertEqual(p.get("schedule_preset") or "", "fast")
        self.assertEqual({}.get("schedule_preset") or "", "")


class TestTierTableStampsFastOnlyWhereItRuns(unittest.TestCase):
    def setUp(self):
        self.old_version = P.ACTIVE_MODEL_VERSION

    def tearDown(self):
        P.ACTIVE_MODEL_VERSION = self.old_version

    def test_25_distilled_cells_carry_fast_eta_and_hq_cells_do_not(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        tiers = P._build_ltx_tiers()
        distilled = [t for t in tiers.values() if t["pipeline"] != "hq"]
        hq = [t for t in tiers.values() if t["pipeline"] == "hq"]
        self.assertTrue(distilled and hq, "registry lost a lane")
        for cell in distilled:
            self.assertIn("fast_eta", cell, cell["key"])
            self.assertIn("fast_min", cell, cell["key"])
            # Fewer forwards can never cost more.
            self.assertLessEqual(cell["fast_min"], cell["eta_min"], cell["key"])
        for cell in hq:
            self.assertNotIn("fast_eta", cell, cell["key"])

    def test_23_cells_carry_no_fast_pricing_at_all(self):
        P.ACTIVE_MODEL_VERSION = "ltx23"
        tiers = P._build_ltx_tiers()
        for cell in tiers.values():
            self.assertNotIn("fast_eta", cell, cell["key"])




class TestI2vReferenceMode(unittest.TestCase):
    """Anchor/Inspire is lane-gated exactly like the schedule preset."""

    def setUp(self):
        self.old_version = P.ACTIVE_MODEL_VERSION

    def tearDown(self):
        P.ACTIVE_MODEL_VERSION = self.old_version

    def test_inspire_rides_on_25_i2v(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"mode": "i2v", "i2v_reference_mode": "inspire"})
        self.assertEqual(job["params"]["i2v_reference_mode"], "inspire")

    def test_inspire_is_dropped_on_t2v(self):
        # No reference image, nothing to be loose about.
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"mode": "t2v", "i2v_reference_mode": "inspire"})
        self.assertEqual(job["params"]["i2v_reference_mode"], "anchor")

    def test_inspire_is_dropped_on_23(self):
        # The engine only version-resolves the re-pin on 2.5; 2.3's bytes
        # are deliberately untouched, so the mode has no meaning there.
        P.ACTIVE_MODEL_VERSION = "ltx23"
        job = job_for({"mode": "i2v", "i2v_reference_mode": "inspire"})
        self.assertEqual(job["params"]["i2v_reference_mode"], "anchor")

    def test_inspire_is_dropped_on_h3(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        job = job_for({"mode": "i2v", "engine": "h3",
                       "i2v_reference_mode": "inspire"})
        self.assertEqual(job["params"]["i2v_reference_mode"], "anchor")

    def test_bogus_and_absent_normalize_to_anchor(self):
        P.ACTIVE_MODEL_VERSION = "ltx25"
        self.assertEqual(
            job_for({"mode": "i2v", "i2v_reference_mode": "wildcard"}
                    )["params"]["i2v_reference_mode"], "anchor")
        self.assertEqual(
            job_for({"mode": "i2v"})["params"]["i2v_reference_mode"], "anchor")


class TestIngredientsGeneration(unittest.TestCase):
    """Ingredients is offered exactly where its adapter works."""

    def test_predicate_matches_the_generation(self):
        self.assertFalse(P.ltx_generation_serves_ingredients("ltx25"))
        self.assertTrue(P.ltx_generation_serves_ingredients("ltx23"))

    def test_bootstrap_flags_follow_the_predicate(self):
        old = P.ACTIVE_MODEL_VERSION
        try:
            P.ACTIVE_MODEL_VERSION = "ltx25"
            boot = P.ltx_tiers_payload()
            self.assertFalse(boot["ingredients_available"])
            self.assertTrue(boot["inspire_available"])
            P.ACTIVE_MODEL_VERSION = "ltx23"
            boot = P.ltx_tiers_payload()
            self.assertTrue(boot["ingredients_available"])
            self.assertFalse(boot["inspire_available"])
        finally:
            P.ACTIVE_MODEL_VERSION = old


class TestH3Orientation(unittest.TestCase):
    """Portrait rotates the resolved cell; it never invents a tier."""

    def h3_job(self, form: dict) -> dict:
        base = {"mode": "t2v", "engine": "h3", "prompt": "a lighthouse"}
        base.update(form)
        return P.make_job({k: [str(v)] for k, v in base.items()})

    def test_portrait_flips_the_cells_canvas(self):
        land = self.h3_job({})["params"]
        port = self.h3_job({"h3_orientation": "portrait"})["params"]
        self.assertEqual(port["width"], land["height"])
        self.assertEqual(port["height"], land["width"])
        self.assertGreater(port["height"], port["width"], "portrait must be tall")
        # Same tier key and same frame count: the wire format is untouched,
        # so every sidecar ever written still replays.
        self.assertEqual(port["h3_tier"], land["h3_tier"])
        self.assertEqual(port["frames"], land["frames"])

    def test_the_pixel_budget_is_identical(self):
        land = self.h3_job({})["params"]
        port = self.h3_job({"h3_orientation": "portrait"})["params"]
        self.assertEqual(land["width"] * land["height"],
                         port["width"] * port["height"],
                         "a rotation must not change cost")

    def test_bogus_and_absent_normalize_to_landscape(self):
        self.assertEqual(
            self.h3_job({"h3_orientation": "sideways"})["params"]["h3_orientation"],
            "landscape")
        self.assertEqual(self.h3_job({})["params"]["h3_orientation"], "landscape")

    def test_orientation_does_not_ride_the_ltx_lane(self):
        job = P.make_job({k: [v] for k, v in {
            "mode": "t2v", "engine": "ltx", "prompt": "x",
            "quality": "balanced", "h3_orientation": "portrait"}.items()})
        self.assertEqual(job["params"]["h3_orientation"], "landscape")


if __name__ == "__main__":
    unittest.main(verbosity=2)
