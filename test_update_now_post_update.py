"""The panel's own "Update now" finishes the update like Pinokio's Update.

THE HOLE (found validating 4.17.4). /version/pull was a git pull and nothing
else; the restart after it ("Restart to finish update", or Stop/Start) never
ran scripts/post_update.sh, the step Pinokio's Update runs after its pull. So
whatever a release adds there silently did not happen for anyone who used the
panel's button: 4.17.3's Lip-sync voice separator, on a 16 GB Mac that then had
three Lip-sync jobs refused a second after the new build came up.

THE FIX: after a pull that moved HEAD the panel runs post_update.sh itself,
in the background, with Pinokio's environment for it; the queue holds and a
running render is waited out; /restart is refused until it has finished, and
a failure names its step. These tests run a REAL pull against throwaway repos
whose post_update.sh is a marker script, and a real subprocess.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

routes_meta = sys.modules["panel.routes_meta"]
GIT_ENV = dict(os.environ, GIT_CONFIG_GLOBAL="/dev/null", GIT_CONFIG_SYSTEM="/dev/null",
               GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
               GIT_COMMITTER_EMAIL="t@t")

# What the marker post_update.sh records: that it ran, and the env it ran in.
MARKER_SCRIPT = """#!/bin/bash
ROOT="${1:?}"
{ echo "ran $(git -C "$ROOT" rev-parse --short HEAD)"
  echo "MPS=${PYTORCH_ENABLE_MPS_FALLBACK:-}"
  echo "TORCH_LOAD=${TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD:-}"
  echo "SSL=${SSL_CERT_FILE:-}"
  echo "PYTHONPATH=${PYTHONPATH:-}"
  echo "VENV=${VIRTUAL_ENV:-}"
} > "$ROOT/post_update_marker.txt"
echo "=== Phosphene post-update - marker step ==="
"""

FAILING_SCRIPT = """#!/bin/bash
echo "!! PHOSPHENE UPDATE FAILED: the mlx 0.31.1 pin"
echo "!! error: the Update did not complete"
exit 1
"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), env=GIT_ENV, check=True,
                          capture_output=True, text=True, errors="replace").stdout.strip()


class _H:
    def __init__(self):
        self.status, self.payload = None, None
        self.wfile = mock.Mock()

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


def _wait_done(timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = P.post_update_status()
        if not st.get("pending") and not st.get("active"):
            return st
        time.sleep(0.1)
    raise AssertionError(f"post_update never finished: {P.post_update_status()}")


@unittest.skipUnless(shutil.which("git"), "git not on PATH")
class UpdateNowRunsPostUpdate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.up, self.clone = base / "up", base / "clone"
        (self.up / "scripts" / "pinokio").mkdir(parents=True)
        git(self.up, "init", "-q", "-b", "main", ".")
        (self.up / "VERSION").write_text("4.17.4\n")
        guard = ROOT / "scripts" / "pinokio" / "update_obstruction_guard.sh"
        shutil.copy(guard, self.up / "scripts" / "pinokio" / guard.name)
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", "4.17.4")
        git(base, "clone", "-q", str(self.up), str(self.clone))
        real_capture = P._git_capture
        self.helper = mock.Mock()
        self._patches = [
            mock.patch.object(P, "ROOT", self.clone),
            mock.patch.object(P, "HELPER", self.helper),
            mock.patch.object(P, "_git_capture",
                              lambda args, cwd=None: real_capture(args, cwd=self.clone)),
            mock.patch.object(P, "_detect_local_install_state", lambda: None),
            mock.patch.object(P, "_check_remote_once", lambda: None),
            mock.patch.dict(P._VERSION_STATE, {}, clear=False),
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": "/dev/null",
                                         "GIT_CONFIG_SYSTEM": "/dev/null",
                                         "SSL_CERT_FILE": "/x/cacert.pem",
                                         "PYTHONPATH": "/x/shim"}),
        ]
        for p in self._patches:
            p.start()
        with P.POST_UPDATE_LOCK:
            P.POST_UPDATE.clear()
            P.POST_UPDATE.update({"state": "idle", "active": False, "pending": False})

    def tearDown(self):
        for p in reversed(self._patches):
            p.stop()
        with P.POST_UPDATE_LOCK:
            P.POST_UPDATE.clear()
            P.POST_UPDATE.update({"state": "idle", "active": False, "pending": False})
        self.tmp.cleanup()

    def _release(self, script: str, version: str = "4.17.5"):
        (self.up / "scripts" / "post_update.sh").write_text(script)
        (self.up / "VERSION").write_text(version + "\n")
        git(self.up, "add", ".")
        git(self.up, "commit", "-qm", version)

    def _pull(self):
        h = _H()
        routes_meta.post_version_pull(h, "/version/pull", {}, "")
        return h

    def test_update_now_runs_the_new_post_update_with_pinokios_env(self):
        self._release(MARKER_SCRIPT)
        h = self._pull()
        self.assertEqual(h.status, 200, h.payload)
        st = _wait_done()
        self.assertEqual(st["state"], "done", st)
        rec = (self.clone / "post_update_marker.txt").read_text()
        self.assertIn("ran " + git(self.clone, "rev-parse", "--short", "HEAD"), rec)
        self.assertIn("MPS=1", rec)               # Pinokio's kernel hardcode
        self.assertIn("TORCH_LOAD=1", rec)
        self.assertIn("SSL=\n", rec)              # Pinokio deletes /SSH|SSL/ keys
        self.assertIn("PYTHONPATH=\n", rec)       # ...and PYTHONPATH
        self.assertIn("VENV=\n", rec)             # never inside the panel's venv
        self.helper.kill.assert_called()          # it had the packages open

    def test_a_pull_that_moves_nothing_runs_nothing(self):
        h = self._pull()
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(P.post_update_status()["state"], "idle")

    def test_restart_waits_for_it_and_a_failure_blocks_restart_with_its_step(self):
        self._release(FAILING_SCRIPT)
        self._pull()
        st = _wait_done()
        self.assertEqual(st["state"], "failed")
        self.assertEqual(st["error"], "the mlx 0.31.1 pin")
        h = _H()
        with mock.patch.object(P, "persist_queue"), \
             mock.patch.object(P.threading, "Thread") as thread:
            routes_meta.post_restart(h, "/restart", {}, "")
        self.assertEqual(h.status, 409)
        self.assertIn("did not finish", h.payload["error"])
        self.assertIn("mlx 0.31.1 pin", h.payload["error"])
        thread.assert_not_called()

    def test_a_running_render_is_waited_out_and_the_queue_holds(self):
        self._release(MARKER_SCRIPT)
        with P.LOCK:
            saved = P.STATE.get("current")
            P.STATE["current"] = {"id": "busy"}
        try:
            self._pull()
            time.sleep(0.5)
            st = P.post_update_status()
            self.assertTrue(st["pending"])
            self.assertFalse((self.clone / "post_update_marker.txt").exists())
            self.assertIn("the update", P._external_build_hold())
            h = _H()
            routes_meta.post_restart(h, "/restart", {}, "")
            self.assertEqual(h.status, 409)
            self.assertIn("Finishing the update", h.payload["error"])
        finally:
            with P.LOCK:
                P.STATE["current"] = saved
        self.assertEqual(_wait_done()["state"], "done")
        self.assertTrue((self.clone / "post_update_marker.txt").exists())
        self.assertNotIn("the update", P._external_build_hold())

    def test_retry_route_and_status_field(self):
        from panel import routes as R                                 # noqa: PLC0415
        self.assertIs(R.POST_ROUTES["/version/finish"], routes_meta.post_version_finish)
        # get_version_state lives in version_check.py now (it reaches the
        # panel's post_update_status as P.post_update_status).
        src = (ROOT / "version_check.py").read_text(encoding="utf-8")
        self.assertIn('snap["post_update"] = P.post_update_status()', src)


if __name__ == "__main__":
    unittest.main()
