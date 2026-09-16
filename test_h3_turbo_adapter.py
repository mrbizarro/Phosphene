#!/usr/bin/env python3
"""Contract gate for the ordered H3 Turbo adapter resolver + pinned installer."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import threading
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


def _reset_dl_state():
    P._set_h3_turbo_dl(status="idle", mb=0,
                       total_mb=P.H3_TURBO_ASSET_BYTES // (1 << 20),
                       error=None)


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

    def test_v4_600_ema_is_preferred_when_present_and_runs_six_forwards(self):
        v4 = self.put(P.H3_TURBO_V4_FILE)
        self.put(P.H3_TURBO_LORA_FILE)
        self.put(P.H3_TURBO_CKPT500_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], v4)
        self.assertEqual(resolved["version"], "v4-600-EMA")
        self.assertFalse(resolved["fallback"])
        self.assertEqual(P.h3_turbo_argv(resolved), ["--lora", f"{v4}:1.0"])
        self.assertEqual(P.h3_turbo_steps(resolved), 7)          # 6 forwards
        self.assertEqual(P.h3_turbo_steps({"version": "v1.0"}), 4)
        self.assertEqual(P.h3_turbo_steps({"version": "ckpt500-EMA"}), 4)

    def test_the_managed_download_is_the_v4_adapter_from_its_author(self):
        a = P._h3_turbo_asset()
        self.assertEqual(a["file"], P.H3_TURBO_V4_FILE)
        self.assertTrue(a["url"].startswith("https://huggingface.co/larryvrh/MiniMax-H3-Turbo-Lora/resolve/main/"))
        self.assertEqual(a["sha256"], "5f3a626cd72c93a8b9318d6760c510bc5092d2ab13aaba1f932c5bab07a416d3")
        self.assertEqual(a["bytes"], 779849816)
        # the LightX2V release asset stays reachable by key
        self.assertEqual(P._h3_turbo_asset("v1.0")["url"], P.H3_TURBO_ASSET_URL)

    def test_v1_is_preferred_when_all_three_exist(self):
        primary = self.put(P.H3_TURBO_LORA_FILE)
        self.put(P.H3_TURBO_FALLBACK_LORA_FILE)
        self.put(P.H3_TURBO_CKPT500_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], primary)
        self.assertEqual(resolved["version"], "v1.0")
        self.assertFalse(resolved["fallback"])
        self.assertEqual(
            P.h3_turbo_argv(resolved), ["--lora", f"{primary}:1.0"],
        )

    def test_alpha_folded_v01_beats_ckpt500(self):
        fallback = self.put(P.H3_TURBO_FALLBACK_LORA_FILE)
        self.put(P.H3_TURBO_CKPT500_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], fallback)
        self.assertEqual(resolved["version"], "v0.1")
        self.assertTrue(resolved["fallback"])
        self.assertEqual(
            P.h3_turbo_argv(resolved), ["--lora", f"{fallback}:1.0"],
        )

    def test_ckpt500_is_the_last_resort_and_labels_itself(self):
        # The retired adapter alone keeps Turbo alive (the v4.0.4 regression:
        # installs that rendered fine on it were un-Turboed by its removal),
        # but it must resolve LAST and carry the honest fallback flag.
        retired = self.put(P.H3_TURBO_CKPT500_FILE)
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], retired)
        self.assertEqual(resolved["version"], "ckpt500-EMA")
        self.assertTrue(resolved["fallback"])
        self.assertEqual(
            P.h3_turbo_argv(resolved), ["--lora", f"{retired}:1.0"],
        )

    def test_raw_v01_is_never_selected(self):
        self.put(P.H3_TURBO_RAW_V01_FILE)
        resolved = P.h3_turbo_paths()
        self.assertFalse(resolved["files_ok"])
        self.assertIsNone(resolved["lora"])
        with self.assertRaisesRegex(RuntimeError, "not available"):
            P.h3_turbo_argv(resolved)


class TestH3TurboInstallContract(unittest.TestCase):
    def setUp(self):
        self.old_paths = P.h3_paths
        self.old_supported = P.h3_supports_lora
        self.old_dir = P._h3_turbo_dir
        self.tmp = tempfile.TemporaryDirectory(prefix="phos-turbo-install-")
        self.target = Path(self.tmp.name)
        P.h3_paths = lambda: {"missing": []}
        P.h3_supports_lora = lambda: True
        P._h3_turbo_dir = lambda: self.target
        _reset_dl_state()

    def tearDown(self):
        P.h3_paths = self.old_paths
        P.h3_supports_lora = self.old_supported
        P._h3_turbo_dir = self.old_dir
        self.tmp.cleanup()
        _reset_dl_state()

    def test_install_starts_the_pinned_download(self):
        started = threading.Event()
        calls = []

        def fake_download(target_dir, push_log):
            calls.append(target_dir)
            started.set()

        logs = []
        result = P._h3_install_turbo(logs.append, download_fn=fake_download)
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        self.assertEqual(result["asset"], P._h3_turbo_asset()["url"])
        self.assertEqual(result["sha256"], P._h3_turbo_asset()["sha256"])
        self.assertEqual(result["bytes"], P._h3_turbo_asset()["bytes"])
        self.assertTrue(started.wait(timeout=5))
        self.assertEqual(calls, [self.target])

    def test_second_install_while_active_is_refused(self):
        gate = threading.Event()

        def blocked_download(target_dir, push_log):
            gate.wait(timeout=10)

        first = P._h3_install_turbo(lambda _m: None,
                                    download_fn=blocked_download)
        self.assertTrue(first["ok"])
        second = P._h3_install_turbo(lambda _m: None,
                                     download_fn=blocked_download)
        gate.set()
        self.assertFalse(second["ok"])
        self.assertIn("active", second["error"])

    def test_present_adapter_short_circuits(self):
        (self.target / P.H3_TURBO_LORA_FILE).write_bytes(b"fixture")
        result = P._h3_install_turbo(lambda _m: None,
                                     download_fn=lambda *a: None)
        self.assertTrue(result["ok"])
        self.assertFalse(result["started"])
        self.assertTrue(result["already_installed"])

    def test_missing_h3_and_old_runner_still_refuse(self):
        P.h3_paths = lambda: {"missing": ["dit (weights absent)"]}
        result = P._h3_install_turbo(lambda _m: None,
                                     download_fn=lambda *a: None)
        self.assertFalse(result["ok"])
        self.assertIn("isn't fully installed", result["error"])

        P.h3_paths = lambda: {"missing": []}
        P.h3_supports_lora = lambda: False
        result = P._h3_install_turbo(lambda _m: None,
                                     download_fn=lambda *a: None)
        self.assertFalse(result["ok"])
        self.assertIn("predates Turbo", result["error"])

    def test_status_offers_the_install(self):
        status = P.h3_turbo_status()
        self.assertTrue(status["install_available"])
        self.assertFalse(status["installing"])
        self.assertIn(P.H3_TURBO_LORA_FILE, status["install_note"])


def _write_lora_fixture(path: Path, adaln_modules: list[str],
                        other_modules: list[str]) -> Path:
    """A real (tiny) safetensors file: header + zero bytes, one 1x1 tensor per
    lora_A / lora_B key. Only the header is ever read by the panel."""
    import json as _json
    import struct as _struct
    header, offset = {}, 0
    for module in adaln_modules + other_modules:
        for suffix in (".lora_A.weight", ".lora_B.weight"):
            header[module + suffix] = {"dtype": "F32", "shape": [1, 1],
                                       "data_offsets": [offset, offset + 4]}
            offset += 4
    header["__metadata__"] = {"base_model": "fixture"}
    raw = _json.dumps(header).encode()
    raw += b" " * (-len(raw) % 8)
    path.write_bytes(_struct.pack("<Q", len(raw)) + raw + b"\0" * offset)
    return path


V4_ADALN = ([f"blocks.{i}.adaln_proj.linear" for i in range(50)]
            + ["final_layer.adaln_proj.linear"])
V4_OTHER = [f"blocks.{i}.attn.out_proj" for i in range(4)]


class TestH3TurboAdaln(unittest.TestCase):
    """Turbo v4 carries 51 adaLN pairs the pruned DiT cannot wrap; the runner
    absorbs them only when handed `--lora-adaln`. The panel dropped that flag
    on 2026-08-14 and every v4 render since lost all 51 (Codex audit
    2026-09-16, finding 2). Detection is by header, never by filename."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="phos-turbo-adaln-")
        self.directory = Path(self.tmp.name)
        self.saved = {name: getattr(P, name) for name in (
            "_h3_turbo_dir", "H3_TURBO_LORA_MIN_BYTES",
            "H3_TURBO_EMBEDDER_MIN_BYTES", "h3_supports_lora_adaln",
            "h3_supports_lora", "h3_paths", "_h3_python", "H3_ROOT")}
        P._h3_turbo_dir = lambda: self.directory
        P.H3_TURBO_LORA_MIN_BYTES = 1
        P.H3_TURBO_EMBEDDER_MIN_BYTES = 1
        P.h3_supports_lora_adaln = lambda: True
        _reset_dl_state()

    def tearDown(self):
        for name, value in self.saved.items():
            setattr(P, name, value)
        self.tmp.cleanup()
        _reset_dl_state()

    def v4(self) -> Path:
        return _write_lora_fixture(self.directory / P.H3_TURBO_V4_FILE,
                                   V4_ADALN, V4_OTHER)

    def embedder(self) -> Path:
        path = self.directory / P.H3_TURBO_EMBEDDER_FILE
        path.write_bytes(b"embedder fixture")
        return path

    def test_header_count_is_the_detector(self):
        self.assertEqual(P.h3_lora_adaln_pairs(self.v4()), 51)
        plain = _write_lora_fixture(self.directory / "plain.safetensors",
                                    [], V4_OTHER)
        self.assertEqual(P.h3_lora_adaln_pairs(plain), 0)
        # An A without its B is not a pair.
        junk = self.directory / "junk.safetensors"
        junk.write_bytes(b"not a safetensors file at all")
        self.assertEqual(P.h3_lora_adaln_pairs(junk), 0)
        self.assertEqual(P.h3_lora_adaln_pairs(self.directory / "nope"), 0)
        self.assertEqual(P.h3_lora_adaln_pairs(None), 0)

    def test_v4_with_companion_emits_lora_adaln(self):
        v4, emb = self.v4(), self.embedder()
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["adaln_pairs"], 51)
        self.assertEqual(resolved["embedder"], emb)
        self.assertEqual(P.h3_turbo_adaln_state(resolved), "absorbed")
        self.assertEqual(P.h3_turbo_argv(resolved),
                         ["--lora", f"{v4}:1.0", "--lora-adaln", str(emb)])
        status = P.h3_turbo_status()
        self.assertEqual(status["adaln"],
                         {"pairs": 51, "embedder": str(emb), "state": "absorbed"})

    def test_v4_without_companion_is_named_not_silent(self):
        v4 = self.v4()
        resolved = P.h3_turbo_paths()
        self.assertIsNone(resolved["embedder"])
        self.assertEqual(P.h3_turbo_adaln_state(resolved), "no_embedder")
        self.assertEqual(P.h3_turbo_argv(resolved), ["--lora", f"{v4}:1.0"])

    def test_old_runner_never_gets_the_flag(self):
        v4, _ = self.v4(), self.embedder()
        P.h3_supports_lora_adaln = lambda: False
        resolved = P.h3_turbo_paths()
        self.assertEqual(P.h3_turbo_adaln_state(resolved), "runner_old")
        self.assertEqual(P.h3_turbo_argv(resolved), ["--lora", f"{v4}:1.0"])

    def test_adapter_without_adaln_pairs_gets_no_flag(self):
        # LightX2V repacks carry none; a companion on disk must not change that.
        lx = _write_lora_fixture(self.directory / P.H3_TURBO_LORA_FILE,
                                 [], V4_OTHER)
        self.embedder()
        resolved = P.h3_turbo_paths()
        self.assertEqual(resolved["lora"], lx)
        self.assertEqual(P.h3_turbo_adaln_state(resolved), "none")
        self.assertEqual(P.h3_turbo_argv(resolved), ["--lora", f"{lx}:1.0"])

    def test_the_real_v4_adapter_has_51_pairs_when_present(self):
        real = Path("/Users/salo/AI/projects/hailuo-mlx/codex/models/turbo-lora/"
                    + P.H3_TURBO_V4_FILE)
        if not real.is_file():
            self.skipTest("v4 adapter not on this machine")
        self.assertEqual(P.h3_lora_adaln_pairs(real), 51)

    def test_render_dispatch_uses_the_argv_helper(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        body = src[src.index("def run_h3_job_inner("):]
        body = body[:body.index("\ndef ", 10)]
        self.assertIn("cmd += h3_turbo_argv(turbo_paths)", body)
        self.assertIn('h3_turbo_adaln_state(turbo_paths) == "no_embedder"', body)
        # provenance: the checkpoint that ran, not the bf16 default path
        self.assertIn('"model": str(_dit_path)', body)
        self.assertNotIn('"model": str(paths["dit"])', body)

    def test_fetch_embedder_uses_the_pack_script_and_is_non_fatal(self):
        self.v4()
        root = self.directory / "pack"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "fetch_time_embedder.py").write_text("# fixture")
        P.H3_ROOT = root
        P._h3_python = lambda: Path("/usr/bin/python3")
        calls, logs = [], []

        class Done:
            returncode, stdout, stderr = 0, "", ""

        def ok_runner(cmd, **kw):
            calls.append(cmd)
            Path(cmd[cmd.index("--out") + 1]).write_bytes(b"tensors")
            return Done()

        self.assertTrue(P._h3_turbo_fetch_embedder(self.directory, logs.append,
                                                   runner=ok_runner))
        self.assertEqual(calls[0][1], str(root / "scripts" / "fetch_time_embedder.py"))
        self.assertTrue((self.directory / P.H3_TURBO_EMBEDDER_FILE).is_file())
        self.assertFalse(list(self.directory.glob("*.partial")))
        # already there: no second fetch
        self.assertTrue(P._h3_turbo_fetch_embedder(self.directory, logs.append,
                                                   runner=ok_runner))
        self.assertEqual(len(calls), 1)

        (self.directory / P.H3_TURBO_EMBEDDER_FILE).unlink()

        class Fail:
            returncode, stdout, stderr = 1, "", "HTTP 403"

        logs.clear()
        self.assertFalse(P._h3_turbo_fetch_embedder(
            self.directory, logs.append, runner=lambda cmd, **kw: Fail()))
        self.assertTrue(any("WARNING" in m and "HTTP 403" in m for m in logs))
        self.assertFalse((self.directory / P.H3_TURBO_EMBEDDER_FILE).exists())

    def test_install_on_an_existing_v4_fetches_only_the_companion(self):
        self.v4()
        P.h3_paths = lambda: {"missing": []}
        P.h3_supports_lora = lambda: True
        done = threading.Event()
        seen = []

        def fake_embedder(target_dir, push_log):
            seen.append(target_dir)
            done.set()
            return True

        result = P._h3_install_turbo(lambda _m: None,
                                     download_fn=lambda *a: self.fail("no adapter download"),
                                     embedder_fn=fake_embedder)
        self.assertTrue(result["ok"])
        self.assertTrue(result["started"])
        self.assertTrue(result["embedder_only"])
        self.assertTrue(done.wait(timeout=5))
        self.assertEqual(seen, [self.directory])
        # with the companion present, it is a plain "already installed"
        self.embedder()
        _reset_dl_state()
        again = P._h3_install_turbo(lambda _m: None, download_fn=lambda *a: None,
                                    embedder_fn=lambda *a: self.fail("refetch"))
        self.assertFalse(again["started"])


class TestH3TurboPins(unittest.TestCase):
    def _load_fetch_script(self):
        spec = importlib.util.spec_from_file_location(
            "fetch_h3_turbo", ROOT / "scripts" / "fetch_h3_turbo.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_fetch_script_pins_match_the_panel(self):
        script = self._load_fetch_script()
        self.assertEqual(script.ASSET_NAME, P.H3_TURBO_LORA_FILE)
        self.assertEqual(script.ASSET_URL, P.H3_TURBO_ASSET_URL)
        self.assertEqual(script.ASSET_SHA256, P.H3_TURBO_ASSET_SHA256)
        self.assertEqual(script.ASSET_BYTES, P.H3_TURBO_ASSET_BYTES)

    def test_installer_runs_the_digest_checked_fetch(self):
        installer = (ROOT / "install_h3.js").read_text(encoding="utf-8")
        self.assertIn("scripts/fetch_h3_turbo.py", installer)
        self.assertIn(P.H3_TURBO_LORA_FILE, installer)
        self.assertIn(P.H3_TURBO_ASSET_SHA256, installer)
        # Provenance of the repack stays recorded.
        self.assertIn(P.H3_TURBO_SOURCE_FILE, installer)
        self.assertIn(P.H3_TURBO_SOURCE_SHA256, installer)
        self.assertIn(P.H3_TURBO_FALLBACK_LORA_FILE, installer)
        # The publication TODO is history; a revert would resurrect it.
        self.assertNotIn("REQUIRED BEFORE WIRING", installer)


if __name__ == "__main__":
    unittest.main(verbosity=2)
