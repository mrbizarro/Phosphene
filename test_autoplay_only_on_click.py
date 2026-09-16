#!/usr/bin/env python3
"""Opening the panel must not play a clip ("it's still playing a clip every
time you open it", owner 2026-09-14). The 4.9 fix passed `autoplay: false` at
the four boot-time selections, but a storyboard poll, the clip restored after a
live preview and the finished-take handoff still selected with the old default
and played with sound. The rule now lives inside selectOutput itself."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _select_output_body() -> str:
    js = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
    i = js.index("function selectOutput(path, options)")
    j = js.index("\nfunction ", i + 1)
    return js[i:j]


class AutoplayIsForAClick(unittest.TestCase):
    def test_autoplay_follows_a_trusted_event(self):
        body = _select_output_body()
        m = re.search(r"const autoplay\s*=([^;]+);", body)
        self.assertIsNotNone(m, "selectOutput must compute autoplay once")
        rule = m.group(1)
        self.assertIn("userSelected", rule,
                      "a selection nobody clicked must not autoplay")
        self.assertIn("options.autoplay === true", rule)
        self.assertLess(body.index("const userSelected"), body.index("const autoplay"),
                        "autoplay reads userSelected, so it must come after it")

    def test_no_other_video_on_the_stage_hardcodes_autoplay(self):
        body = _select_output_body()
        tags = re.findall(r"<video[^>]*>", body)
        self.assertTrue(tags)
        for tag in tags:
            # `${autoplay ? ' autoplay' : ''}` is the computed flag; a bare
            # attribute outside an interpolation would play regardless.
            self.assertNotIn("autoplay", re.sub(r"\$\{[^}]*\}", "", tag),
                             "stage <video> tags must use the computed flag")


if __name__ == "__main__":
    unittest.main()
