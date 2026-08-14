#!/usr/bin/env python3
"""Contract gate for the ordered H3 Turbo adapter resolver."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-h3-turbo-contract-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8297")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


class TestH3TurboResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phos-turbo-files-")
        self.directory = Path(self.tmp.name)
        self.old_dir = P._h3_turbo_dir
        self.old_floor = P.H3_TURBO_LORA_MIN_BYTES
        P._h3_turbo_dir = lambda: self.directory
        P.H3_TURBO_LORA_MIN_BYTES = 1

    def tearDown(self):
        P._h3_turbo_dir = self.old_dir
        P.H3_TURBO_LORA_MIN_BYTES = self.old_floor
        self.tmp.cleanup()

    def put(self, filename: str) -> Path:
        path = self.directory / filename
        path.write_bytes(b"runner-layout fixture")
        return path

    def test_v1_is_preferred_when_both_exist(self):
        primary = self.put(P.H3_TURBO_LORA_FILE)
        self.put(P.H3_TURBO_FALLBACK_LORA_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], primary)
        self.assertEqual(resolved["version"], "v1.0")
        self.assertFalse(resolved["fallback"])
        self.assertEqual(
            P.h3_turbo_argv(resolved), ["--lora", f"{primary}:1.0"],
        )

    def test_alpha_folded_v01_is_the_only_fallback(self):
        fallback = self.put(P.H3_TURBO_FALLBACK_LORA_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], fallback)
        self.assertEqual(resolved["version"], "v0.1")
        self.assertTrue(resolved["fallback"])
        self.assertEqual(
            P.h3_turbo_argv(resolved), ["--lora", f"{fallback}:1.0"],
        )

    def test_raw_v01_and_old_adapter_are_never_selected(self):
        self.put(P.H3_TURBO_RAW_V01_FILE)
        self.put("minimax_h3_turbo_4step_ema_ckpt500.safetensors")
        resolved = P.h3_turbo_paths()
        self.assertFalse(resolved["files_ok"])
        self.assertIsNone(resolved["lora"])
        with self.assertRaisesRegex(RuntimeError, "not available"):
            P.h3_turbo_argv(resolved)


class TestH3TurboInstallContract(unittest.TestCase):
    def test_unpublished_repack_fails_closed(self):
        old_paths = P.h3_paths
        old_supported = P.h3_supports_lora
        old_dir = P._h3_turbo_dir
        with tempfile.TemporaryDirectory(prefix="phos-turbo-install-") as tmp:
            target = Path(tmp)
            try:
                P.h3_paths = lambda: {"missing": []}
                P.h3_supports_lora = lambda: True
                P._h3_turbo_dir = lambda: target
                logs = []
                result = P._h3_install_turbo(logs.append)
            finally:
                P.h3_paths = old_paths
                P.h3_supports_lora = old_supported
                P._h3_turbo_dir = old_dir

        self.assertFalse(result["ok"])
        self.assertIn(P.H3_TURBO_LORA_FILE, result["error"])
        self.assertIn(P.H3_TURBO_SOURCE_SHA256, result["error"])
        self.assertIn(P.H3_TURBO_RAW_V01_FILE, result["error"])
        self.assertTrue(logs)

    def test_installer_records_exact_publication_todo(self):
        installer = (ROOT / "install_h3.js").read_text(encoding="utf-8")
        self.assertIn("lightx2v_v1.0_768p_ourlayout.safetensors", installer)
        self.assertIn(P.H3_TURBO_SOURCE_FILE, installer)
        self.assertIn(P.H3_TURBO_SOURCE_SHA256, installer)
        self.assertIn("release-asset SHA-256: REQUIRED BEFORE WIRING", installer)
        self.assertIn(P.H3_TURBO_FALLBACK_LORA_FILE, installer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
