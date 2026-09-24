"""Cancel stops a model download — it does not restart it (Codex INST-07 / JOB-05),
and Repair deletes nothing it cannot re-download (INST-11).

/models/cancel killed the downloader but never told the worker, which read the
killed attempt as a FAILED one and started the next attempt 5-10 s later; a
cancel during that backoff signalled a process that had already exited and did
nothing. Every process here is a fake; nothing is spawned or downloaded.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

routes_models = sys.modules["panel.routes_models"]


class _H:
    def __init__(self):
        self.status = self.payload = None

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


def _cancel():
    h = _H()
    routes_models.post_models_cancel(h, "/models/cancel", {}, "")
    return h


class _Out:
    def __init__(self, on_read=None):
        self.on_read, self.done = on_read, False

    def read(self, n):
        if not self.done:
            self.done = True
            if self.on_read:
                self.on_read()
            return "progress 10%\n"
        return ""


class _Proc:
    def __init__(self, rc, on_read=None):
        self.pid, self.rc, self.stdout = 4242, rc, _Out(on_read)

    def wait(self, timeout=None):
        return self.rc


class CancelStopsTheDownload(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.killed = []
        self.sleep_hook = None

        def _sleep(_s):
            if self.sleep_hook:
                hook, self.sleep_hook = self.sleep_hook, None
                hook()
        self._patches = [
            mock.patch.object(P, "ROOT", Path(self.tmp.name)),
            mock.patch.object(P, "HF_BIN", Path("/nonexistent/hf")),
            mock.patch.object(P, "push", lambda *a, **k: None),
            mock.patch.object(P, "_active_hf_token", lambda: None),
            mock.patch.object(P.time, "sleep", _sleep),
            mock.patch.object(P.os, "getpgid", lambda pid: 777),
            mock.patch.object(P.os, "killpg", lambda pg, sig: self.killed.append(pg)),
        ]
        for p in self._patches:
            p.start()
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=True, key="q4", repo_id="x/y", cancelled=False)
        self.repo = {"key": "q4", "repo_id": "x/y", "local_dir": "models/q4"}

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=False, key=None, repo_id=None, proc=None,
                              pgid=None, cancelled=False)
        self.tmp.cleanup()

    def _run(self, procs):
        it = iter(procs)
        with mock.patch.object(P.subprocess, "Popen", side_effect=lambda *a, **k: next(it)()) as pop:
            P._download_thread(self.repo)
        return pop.call_count

    def test_cancel_during_an_attempt_does_not_retry(self):
        calls = self._run([lambda: _Proc(-15, on_read=_cancel)] + [lambda: _Proc(0)] * 2)
        self.assertEqual(calls, 1)
        self.assertEqual(self.killed, [777])

    def test_cancel_during_backoff_does_not_retry(self):
        self.sleep_hook = _cancel
        calls = self._run([lambda: _Proc(1)] + [lambda: _Proc(0)] * 2)
        self.assertEqual(calls, 1)

    def test_cancel_between_spawn_and_registration_kills_the_new_group(self):
        def spawn_after_cancel():
            _cancel()                    # lands while no process is registered
            return _Proc(-15)
        calls = self._run([spawn_after_cancel, lambda: _Proc(0), lambda: _Proc(0)])
        self.assertEqual(calls, 1)
        self.assertIn(777, self.killed)

    def test_the_slot_is_released_clean_for_the_next_download(self):
        self._run([lambda: _Proc(-15, on_read=_cancel)])
        with P.DOWNLOAD_LOCK:
            self.assertFalse(P.DOWNLOAD["active"])
            self.assertFalse(P.DOWNLOAD["cancelled"])
        # and an uncancelled download that fails once still retries
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=True, key="q4", repo_id="x/y")
        self.assertEqual(self._run([lambda: _Proc(1), lambda: _Proc(0)]), 2)


class RepairDeletesNothingUntilItCanDownload(unittest.TestCase):
    """Codex INST-11: Repair unlinked the corrupt files and only THEN found the
    download slot busy, answering 409 with nothing started to replace them."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.f = root / "models" / "q4" / "model.safetensors"
        self.f.parent.mkdir(parents=True)
        self.f.write_bytes(b"x" * 10)
        self.started = []
        repo = {"key": "q4", "repo_id": "x/y", "local_dir": "models/q4"}
        self._patches = [
            mock.patch.object(P, "ROOT", root),
            mock.patch.object(P, "HF_BIN", Path("/nonexistent/hf")),
            mock.patch.object(P, "push", lambda *a, **k: None),
            mock.patch.object(P, "h3_status_invalidate", lambda: None),
            mock.patch.object(P, "_repos", lambda: [repo]),
            mock.patch.object(P, "_model_integrity", lambda force=False: {"bad": []}),
            mock.patch.object(P.threading, "Thread",
                              lambda target=None, args=(), daemon=None: mock.Mock(
                                  start=lambda: self.started.append(args))),
        ]
        for p in self._patches:
            p.start()
        self._dv = P._DEEP_VERIFY.get("result")
        P._DEEP_VERIFY["result"] = {"bad": [{"repo": "q4", "file": "model.safetensors"},
                                            {"repo": "gemma", "file": "g.safetensors"}]}

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        P._DEEP_VERIFY["result"] = self._dv
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=False, key=None, repo_id=None, started_ts=None,
                              last_line="", cancelled=False)
        self.tmp.cleanup()

    def _repair(self):
        h = _H()
        h._read_form_body = lambda: ("repo_key=q4", {"repo_key": ["q4"]})
        routes_models.post_models_repair(h, "/models/repair", {}, "")
        return h

    def test_busy_slot_deletes_nothing(self):
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=True, repo_id="other/pack")
        h = self._repair()
        self.assertEqual(h.status, 409)
        self.assertTrue(self.f.exists(), "Repair deleted a file it could not re-download")
        self.assertEqual(self.started, [])

    def test_a_repeat_repair_does_not_reuse_the_consumed_deep_check(self):
        h = self._repair()
        self.assertEqual(h.status, 202, h.payload)
        self.assertFalse(self.f.exists())
        self.assertEqual(len(self.started), 1)
        # the re-download landed a good copy; the old deep-check verdict must
        # not delete it again
        self.f.write_bytes(b"good" * 3)
        with P.DOWNLOAD_LOCK:
            P.DOWNLOAD.update(active=False)
        h = self._repair()
        self.assertTrue(h.payload.get("nothing_to_repair"), h.payload)
        self.assertTrue(self.f.exists())
        self.assertEqual([b["repo"] for b in P._DEEP_VERIFY["result"]["bad"]], ["gemma"])


if __name__ == "__main__":
    unittest.main()
