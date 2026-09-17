#!/usr/bin/env python3
"""Stop means stop, and the Turbo companion is fetched once (ship review 2026-09-17).

P1: an H3 Turbo job whose adapter lacks its adaLN companion fetched it with a
plain subprocess, outside anything Stop knew about, and then spawned the
renderer without looking at `cancel_requested`. A Stop pressed during the
fetch was followed by a full GPU render. Registration of every tracked
process group also raced Stop: the pgid was published, and the flag read,
in separate moments.

P4: a failed "once" fetch was retried by every Turbo job (up to 300 s each,
holding the GPU gate), outside the installer's lock, on a shared .partial.

No GPU, no network, no model: the renderer's Popen is replaced by a short
`sleep` (or refused outright), and the fetch runner is a fake."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-stop-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "uploads")):
    (_TMP / _d).mkdir(parents=True, exist_ok=True)
    os.environ[_k] = str(_TMP / _d)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8298")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    # A zombie still answers kill(0); ask ps for its state.
    out = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)],
                         capture_output=True, text=True).stdout.strip()
    return bool(out) and not out.startswith("Z")


class _CurrentJob:
    """Make `job` the worker's current job for the duration."""

    def __init__(self, job):
        self.job = job

    def __enter__(self):
        with P.LOCK:
            self.saved = (P.STATE.get("current"), P.STATE.get("h3_pgid"),
                          P.STATE.get("mux_pgid"))
            P.STATE["current"] = self.job
        return self.job

    def __exit__(self, *exc):
        with P.LOCK:
            P.STATE["current"], P.STATE["h3_pgid"], P.STATE["mux_pgid"] = self.saved
        return False


class TrackedProcessesObeyStop(unittest.TestCase):
    def test_a_stopped_job_never_spawns(self):
        job = {"id": "t-pre", "cancel_requested": True}
        with _CurrentJob(job), unittest.mock.patch.object(
                P.subprocess, "Popen", side_effect=AssertionError("spawned")):
            with self.assertRaises(P.JobCancelled):
                P.run_tracked_subprocess(["sleep", "30"], pgid_key="h3_pgid",
                                         label="x", job=job)

    def test_stop_between_spawn_and_registration_kills_the_group(self):
        # The race the review named: the process exists, Stop has already
        # read the (empty) pgid slot, and only then is the pgid published.
        job = {"id": "t-race"}
        spawned = []
        real_popen = subprocess.Popen

        def popen_then_stop(*a, **kw):
            proc = real_popen(*a, **kw)
            spawned.append(proc)
            job["cancel_requested"] = True          # Stop, right here
            return proc

        with _CurrentJob(job), unittest.mock.patch.object(
                P.subprocess, "Popen", side_effect=popen_then_stop):
            t0 = time.time()
            with self.assertRaises(P.JobCancelled):
                P.run_tracked_subprocess(["sleep", "30"], pgid_key="mux_pgid",
                                         label="x", job=job)
        self.assertLess(time.time() - t0, 5)
        self.assertIsNotNone(spawned[0].poll())
        self.assertFalse(_alive(spawned[0].pid))
        self.assertIsNone(P.STATE.get("mux_pgid"))

    def test_stop_current_job_ends_a_running_tracked_process(self):
        job = {"id": "t-live"}
        result = {}

        def run():
            try:
                P.run_tracked_subprocess(["sleep", "30"], pgid_key="mux_pgid",
                                         label="the fetch", job=job)
                result["r"] = "finished"
            except P.JobCancelled as e:
                result["r"] = e

        with _CurrentJob(job):
            th = threading.Thread(target=run)
            t0 = time.time()
            th.start()
            deadline = time.time() + 5
            while P.STATE.get("mux_pgid") is None and time.time() < deadline:
                time.sleep(0.02)
            pgid = P.STATE.get("mux_pgid")
            self.assertIsNotNone(pgid)
            P.stop_current_job()
            th.join(timeout=10)
        self.assertFalse(th.is_alive())
        self.assertIsInstance(result["r"], P.JobCancelled)
        self.assertLess(time.time() - t0, 8)
        self.assertFalse(_alive(pgid))

    def test_a_finished_process_is_returned_like_subprocess_run(self):
        r = P.run_tracked_subprocess([sys.executable, "-c",
                                      "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                                     pgid_key="mux_pgid", label="x", job={"id": "t-ok"})
        self.assertEqual((r.returncode, r.stdout.strip(), r.stderr.strip()), (3, "out", "err"))

    def test_timeout_kills_the_group_and_raises(self):
        t0 = time.time()
        with self.assertRaises(subprocess.TimeoutExpired):
            P.run_tracked_subprocess(["sleep", "30"], pgid_key="mux_pgid",
                                     label="x", job={"id": "t-to"}, timeout=0.5)
        self.assertLess(time.time() - t0, 5)

    def test_stop_sets_the_flag_before_it_reads_the_pgids(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        body = src[src.index("def stop_current_job("):]
        body = body[:body.index('push("Stop requested')]
        self.assertLess(body.index('cur["cancel_requested"] = True'),
                        body.index('h3_pgid = STATE.get("h3_pgid")'))
        self.assertIn("with LOCK:", body[:body.index('cur["cancel_requested"] = True')])

    def test_work_outside_the_queue_ignores_a_stop_meant_for_the_render(self):
        # Codex hotfix review #3: an editor proxy built on an HTTP thread
        # while a stopped render unwinds must still run.
        stopped = {"id": "t-render", "cancel_requested": True}
        with _CurrentJob(stopped):
            out, _err = P.run_postprocess_tracked(
                [sys.executable, "-c", "print('proxy built')"], "Timeline proxy")
        self.assertIn("proxy built", out)

    def test_the_worker_threads_own_job_is_the_owner(self):
        job = {"id": "t-owner", "cancel_requested": True}
        P._JOB_CTX.job = job
        try:
            with unittest.mock.patch.object(P.subprocess, "Popen",
                                            side_effect=AssertionError("spawned")):
                with self.assertRaises(P.JobCancelled):
                    P.run_postprocess_tracked(["true"], "Mux")
        finally:
            P._JOB_CTX.job = None
        # and another thread's view is unaffected
        seen = []
        th = threading.Thread(target=lambda: seen.append(P._thread_job()))
        P._JOB_CTX.job = job
        try:
            th.start(); th.join()
        finally:
            P._JOB_CTX.job = None
        self.assertEqual(seen, [None])
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        loop = src[src.index("with _GPU_LOCK:\n                _JOB_CTX.job = job"):]
        self.assertLess(loop.index("run_job_inner(job)"), loop.index("_JOB_CTX.job = None"))

    def test_every_pgid_is_published_through_the_race_free_helper(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        for key in ("h3_pgid", "train_pgid", "mux_pgid"):
            self.assertNotIn(f'STATE["{key}"] = os.getpgid', src, key)


def _h3_dispatch_patches(stack, fetch, popen):
    tmp = _TMP / "pack"
    paths = dict(missing=[], repairable=False, dit=tmp / "dit", python=sys.executable,
                 runner=tmp / "generate_staged.py", compact_root=tmp / "compact",
                 text_config=tmp / "text")
    turbo = dict(files_ok=True, adaln_pairs=51, embedder=None, lora=tmp / "turbo.safetensors",
                 dir=tmp / "turbo", version="v4-600-EMA", fallback=False, missing=[])
    for name, val in {
            "h3_paths": lambda: paths, "h3_capable": lambda: True,
            "h3_supports_lora": lambda: True, "h3_turbo_paths": lambda: dict(turbo),
            "_h3_turbo_fetch_embedder": fetch, "h3_dit_choice": lambda: ("bf16", None),
            "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
            "push": lambda msg: None, "_h3_runner_has_flag": lambda flag: False,
            "h3_supports_lora_adaln": lambda: True}.items():
        stack.enter_context(unittest.mock.patch.object(P, name, val))
    for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                 "h3_live_preview_ready", "h3_supports_stage_a", "h3_supports_tae_draft"):
        stack.enter_context(unittest.mock.patch.object(P, name, lambda: False))
    stack.enter_context(unittest.mock.patch.object(P.HELPER, "is_alive", lambda: False))
    stack.enter_context(unittest.mock.patch.object(P.subprocess, "Popen", popen))
    ff = _TMP / "ffmpeg"
    ff.write_text("")
    stack.enter_context(unittest.mock.patch.object(P, "FFMPEG", ff))


def _turbo_job(jid):
    return {"id": jid, "params": {"engine": "h3", "mode": "t2v", "h3_tier": "standard_5s",
                                  "prompt": "a lighthouse at dusk", "seed": "42",
                                  "h3_turbo": True, "loras": []}}


class TheTurboDispatchObeysStop(unittest.TestCase):
    def test_stop_during_the_companion_fetch_never_reaches_the_renderer(self):
        job = _turbo_job("t-fetch-stop")
        fetches = []

        def fetch(target_dir, push_log, runner=None, **kw):
            fetches.append(runner)
            job["cancel_requested"] = True          # Stop, mid-fetch
            return False

        with ExitStack() as st, _CurrentJob(job):
            _h3_dispatch_patches(st, fetch, unittest.mock.Mock(
                side_effect=AssertionError("renderer spawned after Stop")))
            with self.assertRaises(P.JobCancelled):
                P.run_h3_job_inner(job)
        self.assertEqual(len(fetches), 1)
        self.assertIsNotNone(fetches[0], "the fetch must get the tracked runner")

    def test_the_fetch_runs_as_a_tracked_group_stop_can_kill(self):
        job = _turbo_job("t-fetch-kill")
        seen = {}

        def fetch(target_dir, push_log, runner=None, **kw):
            def stop_soon():
                deadline = time.time() + 5
                while P.STATE.get("h3_pgid") is None and time.time() < deadline:
                    time.sleep(0.02)
                seen["pgid"] = P.STATE.get("h3_pgid")
                P.stop_current_job()
            th = threading.Thread(target=stop_soon)
            th.start()
            try:
                runner(["sleep", "30"], capture_output=True, text=True, env=None,
                       timeout=1800)
            finally:
                th.join()

        real_popen = subprocess.Popen
        spawned = []

        def popen(cmd, **kw):
            if cmd and cmd[0] == "caffeinate":
                raise AssertionError("renderer spawned after Stop")
            p = real_popen(cmd, **kw)
            spawned.append(p)
            return p

        with ExitStack() as st, _CurrentJob(job):
            _h3_dispatch_patches(st, fetch, popen)
            t0 = time.time()
            with self.assertRaises(P.JobCancelled):
                P.run_h3_job_inner(job)
        self.assertLess(time.time() - t0, 8)
        self.assertEqual(seen["pgid"], spawned[0].pid)
        self.assertFalse(_alive(spawned[0].pid))

    def test_stop_racing_the_renderer_spawn_kills_it(self):
        job = _turbo_job("t-spawn-race")
        real_popen = subprocess.Popen
        spawned = []

        def popen(cmd, **kw):
            self.assertEqual(cmd[0], "caffeinate")
            kw.pop("cwd", None)
            p = real_popen(["sleep", "30"], **{k: v for k, v in kw.items()
                                               if k in ("stdout", "stderr", "text",
                                                        "bufsize", "start_new_session",
                                                        "env")})
            spawned.append(p)
            job["cancel_requested"] = True          # Stop lands mid-spawn
            return p

        with ExitStack() as st, _CurrentJob(job):
            _h3_dispatch_patches(st, lambda *a, **k: True, popen)
            st.enter_context(unittest.mock.patch.object(
                P, "h3_turbo_paths", lambda: dict(
                    files_ok=True, adaln_pairs=51, embedder=_TMP / "emb",
                    lora=_TMP / "turbo.safetensors", dir=_TMP / "turbo",
                    version="v4-600-EMA", fallback=False, missing=[])))
            with self.assertRaises(P.JobCancelled):
                P.run_h3_job_inner(job)
        self.assertEqual(len(spawned), 1)
        spawned[0].wait(timeout=5)
        self.assertFalse(_alive(spawned[0].pid))
        self.assertIsNone(P.STATE.get("h3_pgid"))

    def test_the_x2_lane_checks_stop_before_the_helper(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        i = src.index('    if mode == "upscale":')
        body = src[i:src.index('    if mode == "ingredients":', i)]
        self.assertIn('_raise_if_cancelled(job, "the ×2 render")', body)
        self.assertLess(body.index('_raise_if_cancelled(job, "the ×2 render")'),
                        body.index("HELPER.run(job_spec)"))
        self.assertIn('pgid_key="mux_pgid", label="the ×2 preparation"', body)
        self.assertNotIn("subprocess.run(_upscale_hold_tail_cmd", body)

    def test_the_helper_refuses_a_job_stopped_before_it_arrived(self):
        job = {"id": "t-helper", "cancel_requested": True}
        writes = []

        class FakeStdin:
            def write(self, s):
                writes.append(s)

            def flush(self):
                pass

        class FakeProc:
            stdin = FakeStdin()

            def poll(self):
                return None

        h = P.WarmHelper()
        h.proc = FakeProc()
        with _CurrentJob(job), unittest.mock.patch.object(h, "_ensure", lambda: None):
            with self.assertRaises(P.JobCancelled):
                h._run_once({"action": "generate", "id": "t-helper"})
        self.assertEqual(writes, [])
        h.proc = None


class AOneShotPartBelongsToItsTake(unittest.TestCase):
    """Codex hotfix review round 2: a take's parts are their own dicts
    (`<take>-p1`) and Stop marks only the queued take."""

    def setUp(self):
        self.parent = {"id": "take-x", "params": {}}
        P._JOB_CTX.job = self.parent
        P._h3_turbo_embedder_failures.clear()

    def tearDown(self):
        P._JOB_CTX.job = None
        P._h3_turbo_embedder_failures.clear()

    def test_the_owner_cancels_its_children(self):
        child = _turbo_job("take-x-p1")
        self.assertFalse(P._cancel_requested(child))
        self.parent["cancel_requested"] = True
        self.assertTrue(P._cancel_requested(child))
        self.assertFalse(child.get("cancel_requested"))
        with self.assertRaises(P.JobCancelled):
            P._raise_if_cancelled(child, "the part")

    def test_the_helper_refuses_a_part_of_a_stopped_take(self):
        self.parent["cancel_requested"] = True
        writes = []

        class FakeProc:
            class stdin:
                @staticmethod
                def write(s):
                    writes.append(s)

                @staticmethod
                def flush():
                    pass

            def poll(self):
                return None

        h = P.WarmHelper()
        h.proc = FakeProc()
        with _CurrentJob(self.parent), unittest.mock.patch.object(h, "_ensure", lambda: None):
            with self.assertRaises(P.JobCancelled):
                h._run_once({"action": "generate", "id": "take-x-p1"})
        self.assertEqual(writes, [])
        h.proc = None

    def test_stop_during_a_parts_companion_fetch_ends_the_take_and_is_no_failure(self):
        child = _turbo_job("take-x-p1")
        root = _TMP / "pack-take"
        (root / "scripts").mkdir(parents=True, exist_ok=True)
        fetcher = root / "scripts" / "fetch_time_embedder.py"
        fetcher.write_text("import time\ntime.sleep(30)\n")
        turbo_dir = _TMP / "turbo-take"
        real_fetch = P._h3_turbo_fetch_embedder
        spawned = []
        real_popen = subprocess.Popen

        def popen(cmd, **kw):
            if cmd and cmd[0] == "caffeinate":
                raise AssertionError("renderer spawned after Stop")
            proc = real_popen(cmd, **kw)
            spawned.append(proc)
            return proc

        def stop_soon():
            deadline = time.time() + 10
            while P.STATE.get("h3_pgid") is None and time.time() < deadline:
                time.sleep(0.02)
            P.stop_current_job()

        with ExitStack() as st, _CurrentJob(self.parent):
            _h3_dispatch_patches(st, real_fetch, popen)
            st.enter_context(unittest.mock.patch.object(P, "H3_ROOT", root))
            st.enter_context(unittest.mock.patch.object(P, "_h3_python", lambda: Path(sys.executable)))
            st.enter_context(unittest.mock.patch.object(
                P, "h3_turbo_paths", lambda: dict(
                    files_ok=True, adaln_pairs=51, embedder=None, lora=turbo_dir / "l.safetensors",
                    dir=turbo_dir, version="v4-600-EMA", fallback=False, missing=[])))
            th = threading.Thread(target=stop_soon)
            th.start()
            t0 = time.time()
            with self.assertRaises(P.JobCancelled):
                P.run_h3_job_inner(child)
            th.join()
        self.assertLess(time.time() - t0, 10)
        self.assertTrue(self.parent.get("cancel_requested"))
        self.assertEqual(len(spawned), 1)
        self.assertFalse(_alive(spawned[0].pid))
        self.assertEqual(P._h3_turbo_embedder_failures, {})
        self.assertFalse(list(turbo_dir.glob("*.partial")) if turbo_dir.exists() else [])


class TheCompanionIsFetchedOnce(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="phos-emb-"))
        root = self.dir / "pack"
        (root / "scripts").mkdir(parents=True)
        (root / "scripts" / "fetch_time_embedder.py").write_text("# fixture")
        self.st = ExitStack()
        for name, val in {"H3_ROOT": root, "_h3_python": lambda: Path(sys.executable),
                          "H3_TURBO_EMBEDDER_MIN_BYTES": 1,
                          "h3_turbo_paths": lambda: {"adaln_pairs": 51}}.items():
            self.st.enter_context(unittest.mock.patch.object(P, name, val))
        P._h3_turbo_embedder_failures.clear()
        self.logs = []
        self.calls = []

    def tearDown(self):
        self.st.close()
        P._h3_turbo_embedder_failures.clear()

    def failing(self, cmd, **kw):
        self.calls.append(cmd[cmd.index("--out") + 1])
        Path(cmd[cmd.index("--out") + 1]).write_bytes(b"half")

        class R:
            returncode, stdout, stderr = 1, "", "network is unreachable"
        return R()

    def ok(self, cmd, **kw):
        self.calls.append(cmd[cmd.index("--out") + 1])
        Path(cmd[cmd.index("--out") + 1]).write_bytes(b"tensors")

        class R:
            returncode, stdout, stderr = 0, "", ""
        return R()

    def test_a_failure_is_not_retried_by_the_next_job(self):
        f = P._h3_turbo_fetch_embedder
        self.assertFalse(f(self.dir, self.logs.append, runner=self.failing))
        self.assertFalse(f(self.dir, self.logs.append, runner=self.failing))
        self.assertFalse(f(self.dir, self.logs.append, runner=self.failing))
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(any("not fetching" in m and "Install Turbo" in m for m in self.logs))
        self.assertFalse(list(self.dir.glob("*.partial")))
        state = P._h3_turbo_embedder_retry_state(self.dir)
        self.assertIn("network is unreachable", state["error"])
        # The cooldown ends by itself...
        P._h3_turbo_embedder_failures[str(self.dir / P.H3_TURBO_EMBEDDER_FILE)]["retry_at"] = 0
        self.assertIsNone(P._h3_turbo_embedder_retry_state(self.dir))
        self.assertTrue(f(self.dir, self.logs.append, runner=self.ok))
        self.assertEqual(len(self.calls), 2)
        self.assertIsNone(P._h3_turbo_embedder_retry_state(self.dir))

    def test_the_install_button_retries_at_once(self):
        f = P._h3_turbo_fetch_embedder
        self.assertFalse(f(self.dir, self.logs.append, runner=self.failing))
        self.assertTrue(f(self.dir, self.logs.append, runner=self.ok, force=True))
        self.assertEqual(len(self.calls), 2)
        self.assertTrue((self.dir / P.H3_TURBO_EMBEDDER_FILE).is_file())
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        install = src[src.index("def _h3_install_turbo("):]
        install = install[:install.index("\ndef ")]
        self.assertIn("_h3_turbo_fetch_embedder(target, push_log, force=True)", install)
        bg = src[src.index("def _h3_turbo_download_bg("):src.index("def _h3_install_turbo(")]
        self.assertEqual(bg.count("_h3_turbo_fetch_embedder(target_dir, push_log, force=True)"), 2)

    def test_one_fetch_at_a_time_and_each_writes_its_own_file(self):
        f = P._h3_turbo_fetch_embedder
        with P._h3_turbo_embedder_lock:
            self.assertFalse(f(self.dir, self.logs.append, runner=self.ok, force=True))
        self.assertEqual(self.calls, [])
        self.assertTrue(any("already being fetched" in m for m in self.logs))
        self.assertIsNone(P._h3_turbo_embedder_retry_state(self.dir))  # not a failure
        self.assertFalse(f(self.dir, self.logs.append, runner=self.failing))
        self.assertTrue(f(self.dir, self.logs.append, runner=self.ok, force=True))
        self.assertEqual(len(set(self.calls)), 2)
        for c in self.calls:
            self.assertTrue(c.endswith(".partial"))
            self.assertNotEqual(Path(c).name, P.H3_TURBO_EMBEDDER_FILE + ".partial")
        self.assertFalse(list(self.dir.glob("*.partial")))

    def test_stop_during_the_fetch_is_not_a_failed_download(self):
        def stopped(cmd, **kw):
            raise P.JobCancelled("Stopped during the fetch.")
        with self.assertRaises(P.JobCancelled):
            P._h3_turbo_fetch_embedder(self.dir, self.logs.append, runner=stopped)
        self.assertIsNone(P._h3_turbo_embedder_retry_state(self.dir))
        self.assertFalse(P._h3_turbo_embedder_lock.locked())

    def test_status_names_a_remembered_failure(self):
        self.assertIsNone(P._h3_turbo_embedder_retry_state(self.dir))
        P._h3_turbo_fetch_embedder(self.dir, self.logs.append, runner=self.failing)
        with unittest.mock.patch.object(P, "h3_turbo_paths", lambda: {
                "adaln_pairs": 51, "dir": self.dir, "files_ok": True, "lora": None,
                "version": "v4-600-EMA", "fallback": False, "missing": [],
                "embedder": None}), \
                unittest.mock.patch.object(P, "h3_supports_lora", lambda: True), \
                unittest.mock.patch.object(P, "_h3_retune_turbo_estimates", lambda: None), \
                unittest.mock.patch.object(P, "h3_turbo_note", lambda paths: ""):
            status = P.h3_turbo_status()
        self.assertIn("network is unreachable", status["adaln_fetch"]["error"])


if __name__ == "__main__":
    unittest.main()
