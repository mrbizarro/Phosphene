"""Contract gate for the 4.16.0 review round — job lifecycle core (JOB-*).

Findings: PM hub review-2026-09-24/findings_2-jobs.md. Each class below names
the finding it pins and drives the REAL function with the GPU-facing edges
(helper, pack preflight, engines, ffmpeg) replaced — no model, no subprocess,
no panel. Everything a test writes lands in a temp dir (conftest's sandbox or
its own mkdtemp).
"""
from __future__ import annotations

import json
import os
import shutil
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
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as P  # noqa: E402

NODE = shutil.which("node")


class _Helper:
    """Stands in for the warm helper: records every spec, writes the output."""

    ready_info: dict = {}

    def __init__(self):
        self.sent: list[dict] = []

    def is_alive(self):
        return True

    def kill(self, *a, **k):
        pass

    def run(self, spec, *a, **k):
        self.sent.append(spec)
        out = (spec.get("params") or {}).get("output_path")
        if out:
            Path(out).write_bytes(b"x")
        return {"seed_used": 1, "elapsed_sec": 0.1}


# ---------------------------------------------------------------------------
# JOB-02 — a 30 s a2v on an 8 GB Mac reached the engine instead of a refusal
# ---------------------------------------------------------------------------

class CompactMacsRefuseOversizedA2V(unittest.TestCase):
    """The Audio tab's own form (characters.js audioStudioGenerate), 30 s on a
    simulated 8 GB Mac, through make_job and the real a2v branch."""

    def _form(self, seconds: int) -> dict:
        frames = ((max(1, round(seconds * 24)) - 1 + 7) >> 3 << 3) + 1
        return {"mode": "a2v", "prompt": "a singer at a microphone",
                "audio": str(self.wav), "width": "768", "height": "416",
                "frames": str(frames), "seed": "-1", "quality": "high",
                "accel": "off", "enhance": "off", "audio_start_time": "0"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="job02-"))
        self.wav = self.tmp / "song.wav"
        self.wav.write_bytes(b"RIFF0000WAVE")

    def _run(self, seconds: int, *, ram: float, tier: str, env: dict | None = None,
             helper: _Helper | None = None):
        helper = helper or _Helper()
        with mock.patch.dict(os.environ, env or {}):
            profile = P._select_generation_profile(ram, tier)
        job = P.make_job(self._form(seconds))
        job["started_ts"] = time.time()
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", self.tmp), \
             mock.patch.object(P, "SYSTEM_RAM_GB", ram), \
             mock.patch.object(P, "SYSTEM_TIER", tier), \
             mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES[tier]), \
             mock.patch.object(P, "GENERATION_PROFILE", profile), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar", lambda *a, **k: True):
            P.run_job_inner(job)
        return helper

    def test_thirty_seconds_on_8gb_is_refused_before_the_helper(self):
        helper = _Helper()
        with self.assertRaises(P.RenderRefused) as cm:
            self._run(30, ram=8.0, tier="base", helper=helper)
        self.assertEqual(cm.exception.reason, "hardware_tier")
        self.assertEqual(helper.sent, [])
        msg = str(cm.exception)
        self.assertIn("10 s", msg)
        self.assertIn("Start at", msg)
        self.assertEqual(P._analytics_refusal_reason(msg), "hardware_tier")

    def test_the_cap_is_the_compact_profiles_own_number(self):
        with mock.patch.object(P, "SYSTEM_TIER", "base"), \
             mock.patch.object(P, "GENERATION_PROFILE",
                               P._select_generation_profile(8.0, "base")):
            self.assertEqual(P.a2v_max_frames(),
                             P.GENERATION_PROFILE["auto_temporal_after_frames"])
            self.assertEqual(P.a2v_max_frames(),
                             P.LTX_LENGTHS["10s"]["frames"])

    def test_a_ten_second_clip_on_8gb_still_renders_on_the_q4_lane(self):
        helper = self._run(10, ram=8.0, tier="base")
        self.assertEqual([s["action"] for s in helper.sent],
                         ["generate_a2v_distilled"])

    def test_bigger_macs_are_not_capped(self):
        helper = self._run(30, ram=64.0, tier="standard")
        self.assertEqual(len(helper.sent), 1)
        self.assertEqual(helper.sent[0]["params"]["frames"], 721)

    def test_the_profile_escape_hatch_lifts_it(self):
        helper = self._run(30, ram=8.0, tier="base",
                           env={"LTX_GENERATION_PROFILE": "full"})
        self.assertEqual(len(helper.sent), 1)

    def test_the_q4_a2v_constructor_streams_like_the_others(self):
        src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
        body = src.split("def get_a2v_distilled_pipe")[1].split("\ndef ")[0]
        self.assertIn("_construct_pipeline(\n                A2VidDistilledPipeline", body)
        self.assertIn("low_ram_streaming=_stream_for(loras)", body)

    @unittest.skipUnless(NODE, "node not on PATH")
    def test_the_audio_tab_warns_with_the_same_number(self):
        from extract_panel_js import extract_function, panel_source  # noqa: PLC0415
        src = panel_source()
        js = """
var A2V_AREA_KNEE = 0.45e6, A2V_KNEE_FRAMES = 450, BOOT;
const els = {};
function el(id, v) { return els[id] = {id, value: v, style: {}, innerHTML: '', textContent: ''}; }
const document = { getElementById: id => els[id] || null };
el('audioStudioDuration', '30'); el('audioStudioDurationVal');
el('audioStudioDurationWarn'); el('audioStudioWidth', '768'); el('audioStudioHeight', '416');
""" + "\n".join(extract_function(n, src) for n in (
            "_a2vFramesForSeconds", "audioStudioDurationChanged")) + """
const out = {};
for (const [cap, sec] of [[241, 30], [241, 10], [0, 30]]) {
  BOOT = {a2v_max_frames: cap};
  audioStudioDurationChanged(String(sec));
  out[cap + '_' + sec] = els.audioStudioDurationWarn.style.display === 'none'
    ? '' : els.audioStudioDurationWarn.innerHTML;
}
console.log(JSON.stringify(out));
"""
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("10 s", out["241_30"])
        self.assertEqual(out["241_10"], "")
        self.assertNotIn("refused", out["0_30"])


# ---------------------------------------------------------------------------
# JOB-03 — an older queue snapshot could overwrite a newer one on disk
# ---------------------------------------------------------------------------

class QueueFileIsNeverRolledBack(unittest.TestCase):
    """Force the exact interleaving: writer 1 snapshots, is held at the write,
    writer 2 snapshots AND writes, then writer 1 is released."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="job03-"))
        self.saved = {k: P.STATE[k] for k in ("queue", "current", "history", "paused")}

    def tearDown(self):
        with P.LOCK:
            P.STATE.update(self.saved)

    def _race(self, first: list, second: list) -> list:
        real_write = P.atomic_write_text
        second_done = threading.Event()
        calls = {"n": 0}

        def write(path, text, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:                  # the older writer, held here
                second_done.wait(0.5)
            real_write(path, text, *a, **k)

        def older():
            P.persist_queue()

        qfile = self.tmp / "panel_queue.json"
        with mock.patch.object(P, "QUEUE_FILE", qfile), \
             mock.patch.object(P, "atomic_write_text", write):
            with P.LOCK:
                P.STATE.update(queue=list(first), current=None, history=[], paused=False)
            t = threading.Thread(target=older)
            t.start()
            while calls["n"] == 0:               # older has snapshotted
                time.sleep(0.005)
            with P.LOCK:
                P.STATE["queue"] = list(second)
            P.persist_queue()
            second_done.set()
            t.join(5)
        return [j["id"] for j in json.loads(qfile.read_text())["queue"]]

    def test_an_acknowledged_add_survives_the_race(self):
        a, b = {"id": "A", "params": {}}, {"id": "B", "params": {}}
        self.assertEqual(self._race([a], [a, b]), ["A", "B"])

    def test_a_removed_job_does_not_come_back(self):
        a, b = {"id": "A", "params": {}}, {"id": "B", "params": {}}
        self.assertEqual(self._race([a, b], [a]), ["A"])


# ---------------------------------------------------------------------------
# JOB-07 — a full disk at the sidecar write was reported as a clean success
# ---------------------------------------------------------------------------

class ASidecarTheDiskRefusedIsNotASilentSuccess(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="job07-"))
        self.wav = self.tmp / "song.wav"
        self.wav.write_bytes(b"RIFF0000WAVE")
        self.saved_history = P.STATE["history"]

    def tearDown(self):
        with P.LOCK:
            P.STATE["history"] = self.saved_history
        P._JOB_CTX.job = None

    def test_enospc_lands_on_the_job_and_a_retry_restores_it_without_a_render(self):
        import errno
        real_write = P.atomic_write_text

        def full_disk(path, text, *a, **k):
            if str(path).endswith(".json"):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_write(path, text, *a, **k)

        helper = _Helper()
        job = P.make_job({"mode": "a2v", "prompt": "a singer", "audio": str(self.wav),
                          "width": "512", "height": "288", "frames": "49"})
        job["started_ts"] = time.time()
        P._JOB_CTX.job = job                       # what worker_loop does
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", self.tmp), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "atomic_write_text", full_disk):
            P.run_job_inner(job)
        video = Path(job["output_path"])
        sidecar = video.with_suffix(video.suffix + ".json")
        self.assertTrue(video.exists(), "the finished video is kept")
        self.assertFalse(sidecar.exists())
        self.assertIn("could not be written", job.get("warning", ""))
        self.assertIn("No space left", job["warning"])
        self.assertEqual(len(job["sidecar_pending"]), 1)
        json.dumps(job)                            # still persistable

        # Space is back: the record is written from the kept payload.
        with P.LOCK:
            P.STATE["history"] = [job]
        self.assertEqual(P.retry_pending_sidecars(), 1)
        self.assertTrue(sidecar.exists())
        self.assertEqual(json.loads(sidecar.read_text())["params"]["mode"], "a2v")
        self.assertNotIn("sidecar_pending", job)
        self.assertNotIn("warning", job)
        self.assertEqual(len(helper.sent), 1, "no second render")

    def test_the_writer_reports_its_own_outcome(self):
        ok = P.write_sidecar(self.tmp / "ok.mp4.json", {"a": 1})
        self.assertIs(ok, True)
        with mock.patch.object(P, "atomic_write_text",
                               mock.Mock(side_effect=OSError(28, "No space left"))):
            self.assertIs(P.write_sidecar(self.tmp / "x.mp4.json", {"a": 1}), False)

    def test_the_worker_retries_before_each_render(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        body = src.split("def worker_loop")[1].split("\ndef ")[0]
        self.assertLess(body.index("retry_pending_sidecars()"), body.index("run_job_inner(job)"))


# ---------------------------------------------------------------------------
# JOB-01 — the upscaled export overwrote an existing video and its sidecar
# ---------------------------------------------------------------------------

def _fake_ffmpeg(cmd, *a, **k):
    """Encoder stand-in: the output (last argument) gets the input's bytes."""
    src = Path(cmd[cmd.index("-i") + 1])
    Path(cmd[-1]).write_bytes(src.read_bytes() + b"+enc")
    return "", ""


class ExportsNeverReplaceAnotherClip(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="job01-"))

    def test_scene_fit_720p_does_not_overwrite_scene_720p(self):
        victim = self.tmp / "scene_720p.mp4"
        victim.write_bytes(b"THE EARLIER RENDER")
        victim_json = self.tmp / "scene_720p.mp4.json"
        victim_json.write_text('{"output": "earlier"}')
        helper = _Helper()
        job = P.make_job({"mode": "t2v", "prompt": "a lighthouse at dusk",
                          "preset_label": "scene", "quality": "balanced",
                          "width": "1024", "height": "576", "frames": "49",
                          "upscale": "fit_720p", "upscale_method": "lanczos"})
        job["started_ts"] = time.time()
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", self.tmp), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "run_ffmpeg_tracked", _fake_ffmpeg), \
             mock.patch.object(P, "set_hidden", lambda *a, **k: None):
            P.run_job_inner(job)
        self.assertEqual(victim.read_bytes(), b"THE EARLIER RENDER")
        self.assertEqual(victim_json.read_text(), '{"output": "earlier"}')
        out = Path(job["output_path"])
        self.assertNotEqual(out, victim)
        self.assertTrue(out.exists())
        side = json.loads(out.with_suffix(".mp4.json").read_text())
        self.assertEqual(side["output"], str(out))


# ---------------------------------------------------------------------------
# JOB-04 — Enhance Prompt started a second GPU consumer beside H3 / music
# ---------------------------------------------------------------------------

class _Req:
    def __init__(self, prompt):
        self.form = {"prompt": [prompt], "mode": ["t2v"]}
        self.replies = []

    def _read_form_body(self):
        return b"", self.form

    def _json(self, body, status=200):
        self.replies.append((status, body))


class EnhanceWaitsForTheGpu(unittest.TestCase):

    def setUp(self):
        from panel import routes_queue                           # noqa: PLC0415
        self.rq = routes_queue
        # Whichever panel module the routes are wired to in this process (a
        # suite-mate may have booted a second copy) is the one to patch.
        self.PM = routes_queue.P or P

    def test_busy_gpu_answers_busy_and_spawns_nothing(self):
        helper = _Helper()
        busy = threading.Lock()
        busy.acquire()                    # an H3 / music job holds the gate
        req = _Req("a lighthouse")
        with mock.patch.object(self.PM, "HELPER", helper), \
             mock.patch.object(self.PM, "_GPU_LOCK", mock.Mock(wraps=busy)) as gate:
            gate.acquire.side_effect = lambda *a, **k: busy.acquire(blocking=False)
            self.rq.post_prompt_enhance(req, "/prompt/enhance", {}, "")
        self.assertEqual(helper.sent, [])
        self.assertEqual(req.replies[0][0], 409)
        self.assertIn("GPU", req.replies[0][1]["error"])

    def test_free_gpu_enhances_and_releases_the_gate(self):
        class _Enh(_Helper):
            def run(self, spec, *a, **k):
                self.sent.append(spec)
                return {"enhanced": "a lighthouse at dusk, waves", "elapsed_sec": 0.1}
        helper = _Enh()
        gate = threading.Lock()
        req = _Req("a lighthouse")
        with mock.patch.object(self.PM, "HELPER", helper), \
             mock.patch.object(self.PM, "_GPU_LOCK", gate):
            self.rq.post_prompt_enhance(req, "/prompt/enhance", {}, "")
            self.assertFalse(gate.locked(), "released after the request")
        self.assertEqual(len(helper.sent), 1)
        self.assertEqual(req.replies[0][0], 200)
        self.assertTrue(req.replies[0][1]["ok"])


# ---------------------------------------------------------------------------
# JOB-06 — Stop before the image process registered was ignored
# ---------------------------------------------------------------------------

class StopReachesAnImageJobInPreflight(unittest.TestCase):

    def setUp(self):
        self.job = P.make_job({"mode": "image", "prompt": "a brass lamp on a desk",
                               "n": "1"})
        P._JOB_CTX.job = self.job                  # what worker_loop does
        self.calls = []

    def tearDown(self):
        P._JOB_CTX.job = None

    def _generate(self, **kw):
        self.calls.append(kw)
        out = Path(kw["output_dir"]) / "c0.png"
        out.write_bytes(b"png")
        return [{"path": str(out), "seed": 1}]

    def _run(self, preflight):
        with mock.patch.object(P, "_preflight_image_job", preflight), \
             mock.patch.object(P, "_sync_hf_token_to_env", lambda: None), \
             mock.patch.object(P, "_video_render_active", lambda: False), \
             mock.patch.object(P.agent_image_engine, "generate", self._generate):
            P.run_image_job_inner(self.job)

    def test_stop_pressed_during_preflight_starts_no_engine(self):
        def preflight(*a, **k):
            self.job["cancel_requested"] = True    # Stop, while memory settles
        with self.assertRaises(P.JobCancelled):
            self._run(preflight)
        self.assertEqual(self.calls, [])
        self.assertIsNone(self.job.get("output_path"))

    def test_stop_that_lands_as_the_engine_exits_is_not_filed_done(self):
        def generate(**kw):
            r = self._generate(**kw)
            self.job["cancel_requested"] = True
            return r
        with mock.patch.object(P, "_preflight_image_job", lambda *a, **k: None), \
             mock.patch.object(P, "_sync_hf_token_to_env", lambda: None), \
             mock.patch.object(P, "_video_render_active", lambda: False), \
             mock.patch.object(P.agent_image_engine, "generate", generate):
            with self.assertRaises(P.JobCancelled):
                P.run_image_job_inner(self.job)
        self.assertIsNone(self.job.get("output_path"))

    def test_stop_between_popen_and_registration_kills_the_new_group(self):
        ie = P.agent_image_engine
        # The panel installs the hook at import (a suite-mate that boots a
        # second panel copy re-installs its own, hence by name).
        self.assertEqual(getattr(ie._CANCEL_CHECK, "__name__", ""), "_image_job_cancelled")
        self.job["cancel_requested"] = True
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            with mock.patch.object(ie, "_CANCEL_CHECK", P._image_job_cancelled), \
                 self.assertRaises(ie.ImageJobCancelled):
                ie._register_active_proc(proc)
            self.assertIsNotNone(proc.poll(), "the process group was killed")
            self.assertNotIn(proc, ie._ACTIVE_PROCS)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()

    def test_registration_outside_a_cancelled_job_is_untouched(self):
        ie = P.agent_image_engine
        P._JOB_CTX.job = None                      # inline /image/generate
        proc = subprocess.Popen(["sleep", "30"], start_new_session=True)
        try:
            with mock.patch.object(ie, "_CANCEL_CHECK", P._image_job_cancelled):
                ie._register_active_proc(proc)
            self.assertIsNone(proc.poll())
            self.assertIn(proc, ie._ACTIVE_PROCS)
        finally:
            ie._unregister_active_proc(proc)
            proc.kill()
            proc.wait()


# ---------------------------------------------------------------------------
# JOB-08 — Stop waited out the helper's startup handshake
# ---------------------------------------------------------------------------

class StopInterruptsAHelperThatIsStillStarting(unittest.TestCase):

    def test_kill_lands_while_the_handshake_is_waiting(self):
        import signal
        tmp = Path(tempfile.mkdtemp(prefix="job08-"))
        script = tmp / "silent_helper.py"
        script.write_text("import time\ntime.sleep(60)\n")   # never says ready
        h = P.WarmHelper()
        errors: list = []

        def start():
            try:
                with h.run_lock:
                    h._ensure()
            except Exception as exc:                            # noqa: BLE001
                errors.append(exc)

        with mock.patch.object(P, "HELPER_PYTHON", Path(sys.executable)), \
             mock.patch.object(P, "HELPER_SCRIPT", script), \
             mock.patch.object(P, "MLX", tmp), \
             mock.patch.object(P, "engine_env_fault", lambda: None):
            starter = threading.Thread(target=start, daemon=True)
            starter.start()
            deadline = time.time() + 10
            while h.proc is None and time.time() < deadline:
                time.sleep(0.01)
            proc = h.proc
            self.assertIsNotNone(proc)
            try:
                killer = threading.Thread(target=h.kill, daemon=True)
                t0 = time.time()
                killer.start()
                killer.join(8)
                self.assertFalse(killer.is_alive(), "kill() waited on the handshake")
                self.assertLess(time.time() - t0, 8)
                self.assertIsNotNone(proc.poll(), "the starting helper was signalled")
                starter.join(5)
                self.assertFalse(starter.is_alive())
                self.assertTrue(errors and isinstance(errors[0], RuntimeError))
                self.assertIn("stopped while it was starting", str(errors[0]))
            finally:
                if proc.poll() is None:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait()


if __name__ == "__main__":
    unittest.main()
