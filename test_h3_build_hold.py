"""The compact-engine build holds new renders while it loads ~20 GB (Codex H3-02).

h3_build_q8.sh (offered by the sidebar while the panel runs) waited for the job
already running, but the panel kept taking new ones — so a render queued after
the idle check ran beside the build's full-model load on a 36-59 GB Mac. The
build now writes STATE_DIR/h3_build.lock for its whole duration and the worker
starts no job while it is live. Everything here is a stub in a temp dir: no
model, no quantizer, no GPU.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402

PY_STUB = r"""#!/bin/bash
code=""
[ "$1" = "-" ] && code=$(cat)
if [ -f "$STATE/h3_build.lock" ]; then h=HELD; else h=FREE; fi
case "$code$*" in
  *quantize_stream.py*)
    out="${@: -1}"; mkdir -p "$out"; echo x > "$out/model-00001.safetensors"
    echo '{"weight_map": {"a": "model-00001.safetensors"}}' > "$out/model.safetensors.index.json"
    echo "$h quantize" >> "$CALLS" ;;
  *load_dit*) echo "$h validate" >> "$CALLS"; echo "LOAD OK" ;;
  *weight_map*) [ -f "$2/model.safetensors.index.json" ] ;;
  *) exit 1 ;;
esac
"""


class TheBuildHoldsThePanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.app, self.ck, self.state = base / "app", base / "ck", base / "state"
        dit = (self.app / "mlx_models/hailuo-h3/models/deepbeep-pruned-bf16"
               / "MiniMax-H3-FL2VA-pruned_bf16.safetensors")
        dit.parent.mkdir(parents=True)
        dit.write_bytes(b"x")
        (self.ck / ".venv/bin").mkdir(parents=True)
        py = self.ck / ".venv/bin/python"
        py.write_text(PY_STUB)
        py.chmod(0o755)
        self.state.mkdir()
        self.calls = base / "calls.log"

    def tearDown(self):
        self.tmp.cleanup()

    def test_both_model_loads_run_under_the_hold_and_it_is_released(self):
        env = {k: v for k, v in os.environ.items() if k != "LTX_H3_MODELS"}
        env.update(LTX_STATE_DIR=str(self.state), STATE=str(self.state), CALLS=str(self.calls))
        r = subprocess.run(["bash", str(ROOT / "scripts/pinokio/h3_build_q8.sh"), str(self.app)],
                           cwd=self.ck, env=env, capture_output=True, text=True,
                           errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.calls.read_text().split(), ["HELD", "quantize", "HELD", "validate"])
        self.assertFalse((self.state / "h3_build.lock").exists(), "the hold outlived the build")


class TheWorkerRespectsTheHold(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        self.lock = self.state / "h3_build.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def _hold(self, pid=None, age=0.0):
        self.lock.write_text(json.dumps({"pid": pid or os.getpid(), "ts": int(time.time()),
                                         "what": "the Hailuo H3 compact-engine build"}))
        if age:
            t = time.time() - age
            os.utime(self.lock, (t, t))

    def test_a_live_hold_is_reported_and_a_stale_one_is_not(self):
        with mock.patch.object(P, "STATE_DIR", self.state):
            self.assertEqual(P._external_build_hold(), "")
            self._hold()
            self.assertIn("compact-engine build", P._external_build_hold())
            dead = subprocess.Popen(["true"])
            dead.wait()
            self._hold(pid=dead.pid)
            self.assertEqual(P._external_build_hold(), "", "a dead builder wedged the queue")
            self._hold(age=5 * 3600)
            self.assertEqual(P._external_build_hold(), "", "an ancient lock wedged the queue")

    def test_no_job_starts_while_held_and_the_queue_resumes_after(self):
        class _StopWorker(BaseException):
            pass
        ran = threading.Event()

        def fake_run(job):
            ran.set()
            raise _StopWorker()
        job = {"id": "hold-test", "params": {}, "status": "queued"}
        saved = {k: P.STATE.get(k) for k in ("queue", "paused", "current", "history")}
        self._hold()
        try:
            P.STATE.update(queue=[job], paused=False, current=None, history=[])
            with mock.patch.object(P, "STATE_DIR", self.state), \
                    mock.patch.object(P, "run_job_inner", fake_run), \
                    mock.patch.object(P, "persist_queue", lambda: None), \
                    mock.patch.object(P, "caffeinate_on", lambda: None), \
                    mock.patch.object(P, "caffeinate_off", lambda: None), \
                    mock.patch.object(P, "_analytics_install_step", lambda *a, **k: None), \
                    mock.patch.object(P, "_analytics_render_event", lambda *a, **k: None), \
                    mock.patch.object(P, "push", lambda *a, **k: None):
                def _worker():
                    try:
                        P.worker_loop()
                    except _StopWorker:
                        pass
                t = threading.Thread(target=_worker, daemon=True)
                t.start()
                self.assertFalse(ran.wait(3), "a render started during the build's hold")
                self.lock.unlink()
                self.assertTrue(ran.wait(6), "the queue never resumed after the hold")
                t.join(5)
        finally:
            P.STATE.update(saved)


if __name__ == "__main__":
    unittest.main()
