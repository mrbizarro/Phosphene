#!/usr/bin/env python3
"""The stale-engine gate must work on the FIRST render after a restart.

The helper reports whether its engine has the Gemma 4 tower on its ready line,
but the gate runs before the helper is spawned. It read an empty ready_info,
let the render through, and the render died with mlx_lm's "Model type
gemma4_unified not supported." (fleet 4.12.2, one install, four renders).
`_gemma4_tower_supported()` asks the helper's interpreter when the helper has
not answered yet.
"""
from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
os.environ["LTX_STATE_DIR"] = str(Path(tempfile.mkdtemp(prefix="phos-g4-")))
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8323")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def _fake_python(code: int) -> Path:
    d = Path(tempfile.mkdtemp(prefix="phos-fakepy-"))
    f = d / "python3"
    f.write_text(f"#!/bin/sh\nexit {code}\n")
    f.chmod(f.stat().st_mode | stat.S_IEXEC)
    return f


class ProbeBeforeTheHelperBoots(unittest.TestCase):
    def setUp(self):
        self._py = P.HELPER_PYTHON
        self._ready = P.HELPER.ready_info
        P._GEMMA4_PROBE.clear()
        P.HELPER.ready_info = {}

    def tearDown(self):
        P.HELPER_PYTHON = self._py
        P.HELPER.ready_info = self._ready
        P._GEMMA4_PROBE.clear()

    def test_missing_tower_is_false_before_boot(self):
        P.HELPER_PYTHON = _fake_python(3)
        self.assertIs(P._gemma4_tower_supported(), False)

    def test_present_tower_is_true_before_boot(self):
        P.HELPER_PYTHON = _fake_python(0)
        self.assertIs(P._gemma4_tower_supported(), True)

    def test_a_broken_interpreter_is_unknown_not_stale(self):
        P.HELPER_PYTHON = _fake_python(1)
        self.assertIsNone(P._gemma4_tower_supported())

    def test_the_helpers_own_answer_wins(self):
        P.HELPER_PYTHON = _fake_python(0)
        P.HELPER.ready_info = {"gemma4_tower_supported": False}
        self.assertIs(P._gemma4_tower_supported(), False)

    def test_the_gate_uses_the_probe(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn("and _gemma4_tower_supported() is False):", src)

    def test_this_install_has_the_tower(self):
        if not Path(str(self._py)).exists():
            self.skipTest("no helper interpreter on this box")
        P.HELPER_PYTHON = self._py
        self.assertIs(P._gemma4_tower_supported(), True)


if __name__ == "__main__":
    unittest.main()
