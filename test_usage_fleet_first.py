#!/usr/bin/env python3
"""Total renders means THE FLEET, and the dashboard says which is which.

Owner, 2026-09-18: "when I ask you for total renders, I mean total renders from
the fleet too… one of the first windows I want to see is total renders for all
the fleet."

Before this, /stats had ONE tile called "Total renders" whose meaning changed
with whether a PostHog read key happened to be configured: the fleet's total
when it was, this Mac's when it wasn't, distinguishable only by a caption in
10 px type. A number that changes meaning under a fixed label is not a number
you can quote in a release post.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("LTX_STATE_DIR", tempfile.mkdtemp(prefix="phos-usage-"))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import mlx_ltx_panel as P  # noqa: E402

ROOT = Path(P.__file__).resolve().parent
HTML = (ROOT / "panel_assets" / "stats.html").read_text(encoding="utf-8")


class ThePayloadCarriesBoth(unittest.TestCase):
    def test_a_report_without_a_key_says_why_the_fleet_is_missing(self):
        real = P._analytics_query_key
        P._analytics_query_key = lambda: ""
        try:
            rep = P._usage_report()
        finally:
            P._analytics_query_key = real
        self.assertEqual(rep.get("source"), "local")
        self.assertEqual(rep.get("fleet_blocked"), "no_key")
        # ...and this machine's own numbers ride along, named as such.
        self.assertIn("machine", rep)
        self.assertIn("total_renders", rep["machine"])

    def test_attach_local_never_overwrites_the_fleet_tiles(self):
        fleet = {"ok": True, "source": "fleet", "tiles": {"total_renders": 12345}}
        out = P._usage_attach_local(dict(fleet))
        self.assertEqual(out["tiles"]["total_renders"], 12345)
        self.assertIn("machine", out)
        self.assertIsNot(out["machine"].get("total_renders"), 12345)


class TheDashboardLeadsWithTheFleet(unittest.TestCase):
    def test_usage_is_the_first_section_on_the_page(self):
        first = HTML.index('id="sec-usage"')
        for other in ('id="sec-now"', 'id="sec-attention"', 'id="sec-funnel"'):
            self.assertLess(first, HTML.index(other), other + " comes before Usage")

    def test_the_hero_tile_is_the_fleet_and_says_so(self):
        i = HTML.index("const tiles = [")
        block = HTML[i:i + 1600]
        self.assertIn("'Total renders · fleet'", block)
        self.assertIn("'Total renders · this Mac'", block)
        # The hero is the fleet one.
        self.assertLess(block.index("fleet'"), block.index("this Mac'"))
        self.assertIn("hero: true", block[:block.index("this Mac'")])

    def test_an_unreachable_fleet_shows_a_dash_and_a_remedy(self):
        i = HTML.index("const tiles = [")
        block = HTML[i:i + 1600]
        # never a local number under the fleet label
        self.assertIn("isFleet ? fmt(t.total_renders) : '—'", block)
        self.assertIn("add a PostHog read key in Settings", HTML)
        self.assertIn("PostHog query failed", HTML)
        self.assertIn("is-unknown", HTML)


if __name__ == "__main__":
    unittest.main()
