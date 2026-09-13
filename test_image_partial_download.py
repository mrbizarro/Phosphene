#!/usr/bin/env python3
"""An interrupted image-engine download must resume, not crash (#73).

The report: Qwen-Image-Edit-2511 stopped at 38 of 54 GB with 14 blobs still
`.incomplete`; `/image/engine_status` said the engine was ready; every
generation died in mflux's weight loader; and the error the panel showed was
the HEAD of the traceback, cut off before the exception. Three assertions:
the partial state is detected from the one unambiguous sign huggingface_hub
leaves (`blobs/*.incomplete`), the repair removes only the snapshot symlink
tree (finished blobs survive, so nothing downloads twice), and the status
route stops calling a partial download "cached".
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HF_HOME = Path(tempfile.mkdtemp(prefix="phos-hf-"))
STATE = Path(tempfile.mkdtemp(prefix="phos-partial-state-"))
os.environ["HF_HOME"] = str(HF_HOME)
os.environ.pop("HF_HUB_CACHE", None)
os.environ.pop("HUGGINGFACE_HUB_CACHE", None)
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8312")
sys.path.insert(0, str(ROOT))

import image_engine  # noqa: E402

REPO = "Qwen/Qwen-Image-Edit-2511"


def _fake_repo(root: Path, repo: str, *, partial: bool) -> Path:
    repo_dir = root / "hub" / ("models--" + repo.replace("/", "--"))
    blobs = repo_dir / "blobs"
    snap = repo_dir / "snapshots" / "abc123" / "transformer"
    blobs.mkdir(parents=True, exist_ok=True)
    snap.mkdir(parents=True, exist_ok=True)
    (blobs / "aaaa").write_bytes(b"\0" * 4096)
    if partial:
        (blobs / "bbbb.incomplete").write_bytes(b"\0" * 1024)
    link = snap / "model-00001.safetensors"
    if not link.exists():
        os.symlink(os.path.relpath(blobs / "aaaa", snap), link)
    return repo_dir


class PartialDownloadDetection(unittest.TestCase):
    def test_clean_repo_is_not_partial_and_repair_is_a_noop(self) -> None:
        root = Path(tempfile.mkdtemp())
        repo_dir = _fake_repo(root, REPO, partial=False)
        env = {"HF_HOME": str(root)}
        self.assertIsNone(image_engine.hf_repo_partial_download(REPO, env))
        self.assertIsNone(image_engine.repair_partial_hf_download(REPO, env))
        self.assertTrue((repo_dir / "snapshots").is_dir(), "a complete cache is left alone")

    def test_incomplete_blob_is_detected_and_repair_keeps_finished_blobs(self) -> None:
        root = Path(tempfile.mkdtemp())
        repo_dir = _fake_repo(root, REPO, partial=True)
        env = {"HF_HOME": str(root)}
        info = image_engine.hf_repo_partial_download(REPO, env)
        self.assertIsNotNone(info)
        self.assertEqual(info["incomplete"], 1)
        self.assertGreaterEqual(info["on_disk_gb"], 0.0)
        repaired = image_engine.repair_partial_hf_download(REPO, env)
        self.assertIsNotNone(repaired)
        self.assertFalse((repo_dir / "snapshots").exists(),
                         "the snapshot symlink tree goes, so mflux's cached-first rule fails and it resumes")
        self.assertTrue((repo_dir / "blobs" / "aaaa").exists(), "finished blobs stay — nothing downloads twice")
        self.assertTrue((repo_dir / "blobs" / "bbbb.incomplete").exists(),
                        "the partial blob stays — huggingface_hub resumes it")

    def test_hub_root_follows_the_subprocess_env(self) -> None:
        self.assertEqual(image_engine._hf_hub_root({"HF_HOME": "/x"}), Path("/x/hub"))
        self.assertEqual(image_engine._hf_hub_root({"HF_HUB_CACHE": "/y"}), Path("/y"))
        self.assertTrue(image_engine._is_hf_repo_id(REPO))
        self.assertFalse(image_engine._is_hf_repo_id("/abs/path"))
        self.assertFalse(image_engine._is_hf_repo_id("~/local/model"))


class EngineStatusRoute(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        _fake_repo(HF_HOME, REPO, partial=True)
        import mlx_ltx_panel as P  # noqa: E402 — after HF_HOME is set
        cls.P = P
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), P.Handler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def test_partial_download_is_not_reported_cached(self) -> None:
        body = urllib.request.urlopen(
            f"http://127.0.0.1:{self.port}/image/engine_status").read()
        engines = {e["engine"]: e for e in json.loads(body)["engines"]}
        qwen = engines["qwen_edit_inline"]
        self.assertFalse(qwen["cached"], "an interrupted download is not cached")
        self.assertTrue(qwen["partial"], "the status names the partial state")
        self.assertGreaterEqual(qwen["partial_gb"], 0.0)


class LeftoversOfACompleteDownload(unittest.TestCase):
    """FLUX.2 klein (fleet, 14 renders on 3 installs): "stuck at 4.6 GB" about a
    4.6 GB model. A complete download kept a leftover `.incomplete`, the repair
    found nothing to fetch, the size never moved, and the panel called it stuck."""

    def setUp(self) -> None:
        image_engine._REPAIR_MEMO.clear()

    def _repo(self, name: str):
        root = Path(tempfile.mkdtemp())
        return root, _fake_repo(root, name, partial=False)

    def test_incomplete_with_a_finished_twin_is_litter(self) -> None:
        name = "Runpod/klein-twin"
        root, repo_dir = self._repo(name)
        (repo_dir / "blobs" / "aaaa.incomplete").write_bytes(b"\0" * 10)
        self.assertIsNone(image_engine.hf_repo_partial_download(name, {"HF_HOME": str(root)}))
        self.assertFalse((repo_dir / "blobs" / "aaaa.incomplete").exists())
        self.assertTrue((repo_dir / "blobs" / "aaaa").exists(), "the finished blob is never touched")

    def test_leftover_untouched_by_a_full_pass_is_litter(self) -> None:
        name = "Runpod/klein-orphan"
        root, repo_dir = self._repo(name)
        left = repo_dir / "blobs" / "zzzz.incomplete"
        left.write_bytes(b"\0" * 10)
        old = time.time() - 3600
        os.utime(left, (old, old))
        env = {"HF_HOME": str(root)}
        self.assertIsNotNone(image_engine.repair_partial_hf_download(name, env))
        (repo_dir / "snapshots" / "def456").mkdir(parents=True)   # the pass re-linked the snapshot
        self.assertIsNone(image_engine.repair_partial_hf_download(name, env))
        self.assertFalse(left.exists())

    def test_a_stalled_download_is_still_reported_and_kept(self) -> None:
        name = "Runpod/klein-stalled"
        root, repo_dir = self._repo(name)
        left = repo_dir / "blobs" / "zzzz.incomplete"
        left.write_bytes(b"\0" * 10)
        old = time.time() - 3600
        os.utime(left, (old, old))
        env = {"HF_HOME": str(root)}
        image_engine.repair_partial_hf_download(name, env)
        with self.assertRaises(RuntimeError) as cm:           # no snapshot came back
            image_engine.repair_partial_hf_download(name, env)
        self.assertIn("stuck", str(cm.exception))
        self.assertTrue(left.exists(), "a partial that may still be needed is never deleted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
