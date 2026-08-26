#!/usr/bin/env python3
"""Regression gates for the documented manual H3 LoRA import flow."""
from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
class TestH3ManualLoraImportCopy(unittest.TestCase):
    def test_h3_empty_state_names_manual_drop_directory_and_key_layout(self):
        """The H3 picker must explain the no-CivitAI manual import route."""
        panel = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn("Drop a converted H3 <code>.safetensors</code>", panel)
        self.assertIn("<code>lora_A</code> / <code>lora_B</code>", panel)
        self.assertIn("the <strong>Hailuo H3</strong> CivitAI filter", panel)

    def test_readme_documents_h3_library_separately_from_ltx(self):
        """A user following the public manual cannot put an H3 LoRA in LTX's tree."""
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("`mlx_models/hailuo-h3/loras/`", readme)
        self.assertIn("`lora_A` / `lora_B`", readme)
        self.assertIn("Kohya", readme)
        self.assertIn("**My LoRA**", readme)


if __name__ == "__main__":
    unittest.main(verbosity=2)
