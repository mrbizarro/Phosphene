#!/usr/bin/env python3
"""The H3 model choice existed for months with no way to reach it.

`h3_dit_choice()` has always honoured a `h3_dit` setting, `update_settings()`
has always validated it (auto|bf16|q8), and the docstring says in as many words
"`h3_dit` in Settings overrides both ways". There was no control in Settings.
It was reachable only by hand-writing a POST.

That mattered on 2026-08-29: the owner watched H3 sit at ~50 GB and asked why
we could not reduce it. The answer was a setting he could not see — Automatic
gives a 64 GB Mac the bf16 master (38.9 GiB peak) when the Q8 pack renders the
same shot at 21.4 GiB, 45% less memory for 1.6% more time, measured same-seed.

It was ALSO write-only: `update_settings` stored it but `get_settings_public()`
never returned it, so any control would have opened showing "auto" regardless
of what was saved. Both halves are covered here.
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="phos-dit-")))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8307")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402

SRC = ((ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
       + "\n" + (ROOT / "webapp" / "index.html").read_text(encoding="utf-8"))
for _m in sorted((ROOT / "webapp" / "js").glob("*.js")):
    SRC += "\n" + _m.read_text(encoding="utf-8")


class TheSettingRoundTrips(unittest.TestCase):

    def test_the_ui_can_read_the_current_value(self):
        self.assertIn("h3_dit", P.get_settings_public())

    def test_a_saved_choice_comes_back(self):
        for want in ("q8", "bf16", "auto"):
            with self.subTest(want=want):
                P.update_settings({"h3_dit": want})
                self.assertEqual(P.get_settings_public()["h3_dit"], want)

    def test_junk_is_refused_with_the_valid_values_named(self):
        _, err = P.update_settings({"h3_dit": "tiny"})
        self.assertIsNotNone(err)
        for v in ("auto", "bf16", "q8"):
            self.assertIn(v, err)

    def test_the_choice_actually_reaches_the_dit_picker(self):
        P.update_settings({"h3_dit": "bf16"})
        kind, _ = P.h3_dit_choice()
        self.assertEqual(kind, "bf16")


class AutoPrefersTheLightModel(unittest.TestCase):
    """Owner graded the pair 2026-08-29: "b look fine ... quality is good."

    Before that, `auto` gave any Mac with 60 GB+ the bf16 master, so the machine
    with the most memory was the one that spent the most: 38.89 GiB peak versus
    21.38 GiB for the same shot at the same seed.
    """

    def test_auto_picks_q8_whenever_the_pack_is_present(self):
        P.update_settings({"h3_dit": "auto"})
        real = P._h3_q8_dit_dir
        try:
            P._h3_q8_dit_dir = lambda: Path("/tmp/pretend-q8")
            kind, path = P.h3_dit_choice()
            self.assertEqual(kind, "q8")
            self.assertIsNotNone(path)
        finally:
            P._h3_q8_dit_dir = real

    def test_auto_falls_back_to_bf16_when_q8_is_absent(self):
        P.update_settings({"h3_dit": "auto"})
        real = P._h3_q8_dit_dir
        try:
            P._h3_q8_dit_dir = lambda: None
            self.assertEqual(P.h3_dit_choice()[0], "bf16")
        finally:
            P._h3_q8_dit_dir = real

    def test_bf16_can_still_be_forced(self):
        P.update_settings({"h3_dit": "bf16"})
        real = P._h3_q8_dit_dir
        try:
            P._h3_q8_dit_dir = lambda: Path("/tmp/pretend-q8")
            self.assertEqual(P.h3_dit_choice()[0], "bf16")
        finally:
            P._h3_q8_dit_dir = real
            P.update_settings({"h3_dit": "auto"})


class TheControlExists(unittest.TestCase):
    """The whole defect was a capability with no surface."""

    def test_there_is_a_select_with_all_three_values(self):
        self.assertIn('id="settingsH3Dit"', SRC)
        block = SRC[SRC.index('id="settingsH3Dit"'):][:900]
        for v in ('value="auto"', 'value="bf16"', 'value="q8"'):
            self.assertIn(v, block)

    def test_the_control_is_populated_and_saved(self):
        self.assertIn("ditSelect.value =", SRC, "the control never shows the saved value")
        self.assertIn("fd.set('h3_dit', _dit)", SRC, "the control never saves")

    def test_it_reads_a_status_global_that_exists(self):
        m = re.search(r"const haveH3 = !!\((\w+) &&", SRC)
        self.assertIsNotNone(m, "the H3-installed check is gone")
        # Since the queue module extraction (slice 3, docs/ARCHITECTURE.md)
        # the status global is declared as a globalThis property — which is
        # exactly what makes it readable across module files.
        self.assertTrue(
            f"let {m.group(1)} = null;" in SRC
            or f"globalThis.{m.group(1)} = null;" in SRC,
            f"{m.group(1)} is not a declared global")

    def test_the_memory_numbers_shown_are_the_measured_ones(self):
        block = SRC[SRC.index('id="settingsH3DitHint"'):][:900]
        for n in ("38.9", "21.4", "45"):
            self.assertIn(n, block, f"the measured figure {n} is not shown")


class BelowTheFullEngineFloor(unittest.TestCase):
    """A 48 GB Mac with the Q8 pack built, whose Settings said Full: every render
    loaded the 41 GB master and died of Metal memory (Pinokio, M5 Max 48 GB).
    Below the floor, Full with Q8 present resolves to Q8."""

    def setUp(self):
        self._ram = P.SYSTEM_RAM_GB
        self._q8 = P._h3_q8_dit_dir
        self._pack = Path(tempfile.mkdtemp(prefix="phos-q8pack-"))
        P._h3_q8_dit_dir = lambda: self._pack
        P.update_settings({"h3_dit": "bf16"})

    def tearDown(self):
        P.SYSTEM_RAM_GB = self._ram
        P._h3_q8_dit_dir = self._q8
        P.update_settings({"h3_dit": "auto"})

    def test_48gb_full_preference_renders_q8(self):
        P.SYSTEM_RAM_GB = 48.0
        self.assertEqual(P.h3_dit_choice(), ("q8", self._pack))

    def test_64gb_full_preference_is_honoured(self):
        P.SYSTEM_RAM_GB = 64.0
        self.assertEqual(P.h3_dit_choice(), ("bf16", None))

    def test_48gb_without_q8_is_not_silently_q8(self):
        P.SYSTEM_RAM_GB = 48.0
        P._h3_q8_dit_dir = lambda: None
        self.assertEqual(P.h3_dit_choice(), ("bf16", None))

    def test_the_menu_is_told(self):
        self.assertIn('"bf16_fits": SYSTEM_RAM_GB >= H3_MIN_RAM_GB', SRC)
        self.assertIn("bf16_fits === false", SRC)


if __name__ == "__main__":
    unittest.main()
