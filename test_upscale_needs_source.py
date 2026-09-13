#!/usr/bin/env python3
"""LTX Upscale with no clip picked must not queue a job that can only fail
("source clip for Upscale ×2 not found: ''", fleet 4.12.3)."""
from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class GenerateChecksTheClip(unittest.TestCase):
    def test_submit_guards_an_empty_source(self):
        js = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
        i = js.index("document.getElementById('genForm').addEventListener('submit'")
        j = js.index("const genBtn = document.getElementById('genBtn');", i)
        block = js[i:j]
        self.assertIn("=== 'upscale'", block)
        self.assertIn("upscale_source_path", block)
        self.assertIn("return;", block)


if __name__ == "__main__":
    unittest.main()
