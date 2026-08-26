#!/usr/bin/env python3
"""Contract tests for the local H3 LoRA file-import path."""
from __future__ import annotations

import json
import os
import struct
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-file-import-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"

import mlx_ltx_panel as P


def _safetensors_lora(*, alpha: float | None = None,
                      module: str = "blocks.24.attn.qkv_proj") -> bytes:
    """A tiny but complete F32 A/B safetensors payload for import validation."""
    names = (
        module + ".lora_A.weight",
        module + ".lora_B.weight",
    )
    values = [(name, struct.pack("<f", 0.0)) for name in names]
    if alpha is not None:
        values.append((module + ".alpha", struct.pack("<f", alpha)))
    header = {}
    offset = 0
    for key, raw in values:
        header[key] = {"dtype": "F32",
                       "shape": [1] if key.endswith(".alpha") else [1, 1],
                       "data_offsets": [offset, offset + len(raw)]}
        offset += len(raw)
    encoded = json.dumps(header).encode("utf-8")
    return len(encoded).to_bytes(8, "little") + encoded + b"".join(raw for _, raw in values)


def _kohya_header() -> bytes:
    header = {
        "lora_unet_blocks_24_attn_qkv_proj.lora_down.weight": {"dtype": "F32", "shape": [1, 1], "data_offsets": [0, 0]},
        "lora_unet_blocks_24_attn_qkv_proj.lora_up.weight": {"dtype": "F32", "shape": [1, 1], "data_offsets": [0, 0]},
        "lora_unet_blocks_24_attn_qkv_proj.alpha": {"dtype": "F32", "shape": [1, 1], "data_offsets": [0, 0]},
    }
    encoded = json.dumps(header).encode("utf-8")
    return len(encoded).to_bytes(8, "little") + encoded


class TestH3LoraFileImport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phos-h3-lora-import-")
        self.dir = Path(self.tmp.name)
        self.old_dir = P._safe_h3_loras_dir
        P._safe_h3_loras_dir = lambda: self.dir

    def tearDown(self):
        P._safe_h3_loras_dir = self.old_dir
        self.tmp.cleanup()

    def test_imports_runner_layout_into_h3_library(self):
        payload = _safetensors_lora()
        result = P.import_h3_lora_file("MysticXXX_MMH3-V4.safetensors", payload)

        target = self.dir / "MysticXXX_MMH3-V4.safetensors"
        self.assertEqual(Path(result["path"]), target)
        self.assertTrue(target.is_file())
        self.assertEqual(result["layout"], "bare")
        self.assertFalse(result["converted"])

    def test_refuses_raw_kohya_and_leaves_no_file(self):
        payload = _kohya_header()

        with self.assertRaisesRegex(RuntimeError, "kohya.*alpha"):
            P.import_h3_lora_file("raw-kohya.safetensors", payload)

        self.assertEqual(list(self.dir.iterdir()), [])

    def test_refuses_non_safetensors_uploads(self):
        with self.assertRaisesRegex(ValueError, "\.safetensors"):
            P.import_h3_lora_file("adapter.zip", b"not an adapter")

    def test_refuses_non_unit_alpha_without_leaving_a_file(self):
        with self.assertRaisesRegex(RuntimeError, "alpha/rank.*folded"):
            P.import_h3_lora_file("needs-scale.safetensors", _safetensors_lora(alpha=8.0))

        self.assertEqual(list(self.dir.iterdir()), [])

    def test_refuses_unreadable_alpha_without_leaving_a_file(self):
        payload = _safetensors_lora(alpha=1.0)[:-4]
        with self.assertRaisesRegex(RuntimeError, "alpha|truncated|offsets"):
            P.import_h3_lora_file("bad-alpha.safetensors", payload)

        self.assertEqual(list(self.dir.iterdir()), [])

    def test_refuses_modules_missing_from_the_installed_transformer(self):
        old_targets = P._h3_lora_target_modules
        P._h3_lora_target_modules = lambda: {"blocks.24.attn.qkv_proj"}
        try:
            with self.assertRaisesRegex(RuntimeError, "targets no module"):
                P.import_h3_lora_file(
                    "wrong-model.safetensors",
                    _safetensors_lora(module="blocks.24.attn.typo"))
        finally:
            P._h3_lora_target_modules = old_targets

        self.assertEqual(list(self.dir.iterdir()), [])

    def test_refuses_a_header_only_adapter(self):
        header_only = _safetensors_lora()[:-8]
        with self.assertRaisesRegex(RuntimeError, "truncated|offsets"):
            P.import_h3_lora_file("truncated.safetensors", header_only)

        self.assertEqual(list(self.dir.iterdir()), [])

    def test_refuses_duplicate_name_without_replacing_first_import(self):
        first = _safetensors_lora()
        replacement = _safetensors_lora(alpha=1.0)
        P.import_h3_lora_file("keep-me.safetensors", first)

        with self.assertRaises(FileExistsError):
            P.import_h3_lora_file("keep-me.safetensors", replacement)

        self.assertEqual((self.dir / "keep-me.safetensors").read_bytes(), first)

    def test_picker_markup_has_a_real_import_control(self):
        panel = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn("Import H3 LoRA", panel)
        self.assertIn("/h3/loras/import", panel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
