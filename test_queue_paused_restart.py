"""A restart resumes the queue (#80).

The breaker pauses the worker after three identical failures and the Pause
chip pauses it by hand; both were written to panel_queue.json and restored on
the next boot, so a user who restarted Pinokio to unstick the worker got the
same "Worker paused" card with no reason left to read. The flag is a session
decision and dies with the session; the queue and history still come back.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent


class PausedFlagIsNotRestored(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tmp = tempfile.mkdtemp(prefix="phos_queue_")
        os.environ["LTX_STATE_DIR"] = tmp
        os.environ["LTX_OUTPUT_DIR"] = os.path.join(tmp, "out")
        os.environ["LTX_UPLOADS_DIR"] = os.path.join(tmp, "up")
        os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
        os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
        os.makedirs(os.environ["LTX_OUTPUT_DIR"], exist_ok=True)
        os.makedirs(os.environ["LTX_UPLOADS_DIR"], exist_ok=True)
        sys.path.insert(0, str(ROOT))
        import mlx_ltx_panel as p
        cls.p = p

    def test_a_queue_saved_paused_comes_back_running_with_its_jobs(self):
        p = self.p
        saved = {"queue": [{"id": "j1", "status": "queued", "params": {"mode": "t2v"}}],
                 "history": [{"id": "j0", "status": "done"}], "paused": True, "current": None}
        p.QUEUE_FILE.parent.mkdir(parents=True, exist_ok=True)
        p.QUEUE_FILE.write_text(json.dumps(saved))
        with p.LOCK:
            p.STATE["paused"] = True; p.STATE["queue"] = []; p.STATE["history"] = []
        p.load_queue()
        self.assertFalse(p.STATE["paused"])
        self.assertEqual([j["id"] for j in p.STATE["queue"]], ["j1"])
        self.assertEqual([j["id"] for j in p.STATE["history"]], ["j0"])


if __name__ == "__main__":
    unittest.main()
