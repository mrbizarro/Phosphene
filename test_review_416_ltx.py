"""Contract gate for the 4.16.0 review round — the LTX lanes (LTX-*).

Findings: PM hub review-2026-09-24/findings_3-ltx.md. The render path is
driven for real with the helper replaced by a recorder and the engine-facing
edges stubbed; the One Shot joins run the real ffmpeg on tiny synthetic clips.
Nothing loads a model or touches the GPU; everything lands in a temp dir.
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


def _tiny_clip(path: Path, colour: str = "red", secs: float = 0.5) -> None:
    subprocess.run([str(P.FFMPEG), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"color=c={colour}:s=64x64:d={secs}:r=24", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-t", str(secs), "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


class _TakeHarness(unittest.TestCase):
    """An LTX One Shot whose parts are tiny real clips with real-shaped
    (nested) LTX sidecars, as run_job_inner writes them."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ltx-take-"))
        self.out_dir = self.tmp / "out"
        self.out_dir.mkdir()
        self.parts: list[dict] = []
        self.clip_secs = 0.5
        self.patches = [
            mock.patch.object(P, "OUTPUT", self.out_dir),
            mock.patch.object(P, "STATE_DIR", self.tmp / "state"),
            mock.patch.object(P, "set_hidden", lambda *a, **k: None),
            mock.patch.object(P, "take_drift",
                              lambda *a, **k: {"ok": True, "delta": 0.0, "drifted": False}),
            mock.patch.object(P, "run_job_inner", self._fake_part),
        ]
        for pt in self.patches:
            pt.start()

    def tearDown(self):
        for pt in reversed(self.patches):
            pt.stop()

    def _fake_part(self, child):
        cp = child["params"]
        self.parts.append(dict(cp))
        out = self.out_dir / f"{child['id']}.mp4"
        _tiny_clip(out, "red" if len(self.parts) % 2 else "blue", self.clip_secs)
        # What run_job_inner really writes: the part's intent under `params`.
        (self.out_dir / f"{child['id']}.mp4.json").write_text(json.dumps({
            "output": str(out), "raw_output": str(out), "native_output": str(out),
            "params": {**cp}, "command": "helper", "fps": 24, "queue_id": child["id"],
        }))
        child["output_path"] = str(out)


# ---------------------------------------------------------------------------
# LTX-01 — Stop could not interrupt the One Shot final join
# ---------------------------------------------------------------------------

class StopEndsTheOneShotJoin(_TakeHarness):

    def test_stop_during_the_join_kills_it_and_nothing_is_published(self):
        real_ff = str(P.FFMPEG)
        slow = self.tmp / "ffmpeg"
        # The join (and only the join) takes "forever".
        slow.write_text("#!/bin/sh\ncase \"$*\" in *concat=n=*) exec sleep 30;; esac\n"
                        f"exec '{real_ff}' \"$@\"\n")
        slow.chmod(0o755)
        job = P.make_job({"mode": "t2v", "engine": "ltx", "prompt": "a hen skates",
                          "take_seconds": "30"})
        self.assertEqual(len(job["params"]["take"]["parts"]), 3)
        errors: list = []

        def run():
            try:
                P.run_take_job_inner(job)
            except Exception as exc:                            # noqa: BLE001
                errors.append(exc)

        saved_current = P.STATE["current"]
        with mock.patch.object(P, "FFMPEG", slow), \
             mock.patch.object(P, "HELPER", _Helper()):
            with P.LOCK:
                P.STATE["current"] = job
            try:
                t = threading.Thread(target=run, daemon=True)
                t.start()
                deadline = time.time() + 30
                # Only the join is left once the runner says so; then wait
                # for its process group to be registered where Stop looks.
                while not (any("[take] joining" in str(l) for l in P.STATE.get("log", []))
                           and P.STATE.get("mux_pgid")):
                    self.assertLess(time.time(), deadline, "join never started")
                    time.sleep(0.02)
                t0 = time.time()
                P.stop_current_job()
                t.join(10)
                self.assertFalse(t.is_alive(), "the join ran on after Stop")
                self.assertLess(time.time() - t0, 10)
            finally:
                with P.LOCK:
                    P.STATE["current"] = saved_current
        self.assertTrue(errors)
        self.assertIsInstance(errors[0], P.JobCancelled)
        self.assertIsNone(job.get("output_path"))
        self.assertEqual(list(self.out_dir.glob("*_take30s.mp4.json")), [])

    def test_a_stop_as_the_join_finishes_is_not_published(self):
        job = P.make_job({"mode": "t2v", "engine": "ltx", "prompt": "a hen skates",
                          "take_seconds": "30"})
        real_join = P._join_take_parts

        def join(*a, **k):
            real_join(*a, **k)
            job["cancel_requested"] = True       # Stop lands as the encode ends
        with mock.patch.object(P, "_join_take_parts", join):
            with self.assertRaises(P.JobCancelled):
                P.run_take_job_inner(job)
        self.assertIsNone(job.get("output_path"))
        self.assertEqual(list(self.out_dir.glob("*_take30s.mp4.json")), [])


# ---------------------------------------------------------------------------
# LTX-02 — the One Shot sidecar carried the last part's parameters
# ---------------------------------------------------------------------------

class TheTakesSidecarIsTheTakes(_TakeHarness):

    def _take(self, **form):
        job = P.make_job({"mode": "t2v", "engine": "ltx", "prompt": "a hen skates on a pond",
                          "take_seconds": "30", "preset_label": "ride",
                          "beats": json.dumps(["b1", "b2", "b3", "b4", "b5", "b6"]), **form})
        P.run_take_job_inner(job)
        final = Path(job["output_path"])
        return job, final, json.loads(final.with_suffix(final.suffix + ".json").read_text())

    def test_params_hold_the_parent_intent_and_outputs_name_the_take(self):
        job, final, side = self._take()
        q = side["params"]
        self.assertEqual(q["take"]["seconds"], 30)
        self.assertTrue(q["take"]["beats"][0].startswith("b1"))
        self.assertTrue(q["take"]["beats"][1].startswith("b2"))
        self.assertEqual(q["mode"], "t2v")
        self.assertEqual(q["prompt"], "a hen skates on a pond")
        self.assertIsNone(q["image"], "never a handoff frame")
        self.assertEqual(q["frames"], job["params"]["frames"])
        for k in ("output", "raw_output", "native_output"):
            self.assertEqual(side[k], str(final), k)

    def test_a_speech_handoff_take_still_has_params(self):
        # The last part is a remux in the state dir with no sidecar of its own.
        self.clip_secs = 2.0
        with mock.patch.object(P, "take_handoff_points", lambda *a, **k: (1.0, 1.5)), \
             mock.patch.object(P, "take_lipsync_score", lambda *a, **k: None):
            job, final, side = self._take(take_handoff="speech")
        self.assertEqual(side["take"]["handoff"], "speech")
        self.assertTrue(side["take"]["parts"][-1].endswith("_lead.mp4"))
        self.assertEqual(side["params"]["take"]["seconds"], 30)
        self.assertEqual(side["params"]["prompt"], "a hen skates on a pond")

    @unittest.skipUnless(NODE, "node not on PATH")
    def test_load_params_reopens_new_and_legacy_takes_as_one_shot(self):
        from extract_panel_js import extract_function, panel_source  # noqa: PLC0415
        src = panel_source()
        _, _, new_side = self._take()
        legacy = {"take": {"seconds": 30, "beats": ["b1"], "engine": "ltx"},
                  "prompt": "a hen skates on a pond", "engine": "ltx", "mode": "t2v",
                  # the last part's params (malformed legacy shape)
                  "params": {"mode": "i2v", "prompt": "b5 b6", "take": None,
                             "image": "/state/take/x/part2_last.png"}}
        speech_legacy = {"take": {"seconds": 30, "beats": ["b1"], "engine": "ltx"},
                         "prompt": "a hen skates on a pond", "engine": "ltx"}
        js = """
var activePath = '/x.mp4', opened = [], modes = [];
function _parseIdeoCaption() { return null; }
async function charactersLoadParams() { opened.push('characters'); }
function oneshotOpenFromParams(p) { opened.push(p); }
function setMode(m) { modes.push(m); }
""" + extract_function("loadParams", src) + """
const cases = %s;
(async () => {
  const out = [];
  for (const c of cases) {
    opened = []; modes = [];
    globalThis.fetch = async () => ({ok: true, json: async () => c});
    let err = null;
    try { await loadParams(); } catch (e) { err = String(e); }
    const o = opened[0] || {};
    out.push({err, seconds: o.take && o.take.seconds, prompt: o.prompt,
              image: o.image || '', modes});
  }
  console.log(JSON.stringify(out));
})();
""" % json.dumps([new_side, legacy, speech_legacy])
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        for got in json.loads(r.stdout):
            self.assertIsNone(got["err"])
            self.assertEqual(got["seconds"], 30)
            self.assertEqual(got["prompt"], "a hen skates on a pond")
            self.assertEqual(got["image"], "")
            self.assertEqual(got["modes"], [], "never the single-clip form")


# ---------------------------------------------------------------------------
# LTX-03 — Extend, FFLF and Q4 Audio→Video dropped the selected LoRAs
# ---------------------------------------------------------------------------

STYLE = [{"path": "/loras/crisp.safetensors", "strength": 0.8}]


class EveryLaneSendsTheLoraStack(unittest.TestCase):
    """The real dispatch branches, the helper intercepted: the stack the job
    carries is the stack the helper is asked to attach."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ltx03-"))
        self.clip = self.tmp / "src.mp4"
        _tiny_clip(self.clip)
        self.png = self.tmp / "a.png"
        subprocess.run([str(P.FFMPEG), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=green:s=64x64:d=1", "-frames:v", "1", "-update", "1",
                        str(self.png)], check=True)
        self.wav = self.tmp / "song.wav"
        self.wav.write_bytes(b"RIFF0000WAVE")

    def _dispatch(self, form: dict, *, q8: bool = True) -> dict:
        helper = _Helper()
        job = P.make_job(form)
        job["params"]["loras"] = [dict(l) for l in STYLE]
        job["started_ts"] = time.time()
        caps = dict(P.CAPABILITIES["pro"], allows_q8=q8)
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", self.tmp), \
             mock.patch.object(P, "SYSTEM_CAPS", caps), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "_native_render_for", lambda x: x), \
             mock.patch.object(P, "run_ffmpeg_tracked", lambda *a, **k: ("", "")), \
             mock.patch.object(P, "write_sidecar", lambda *a, **k: True):
            try:
                P.run_job_inner(job)
            except Exception:                                   # noqa: BLE001
                # Anything after the helper call (muxing a fake clip) is not
                # this test's business; the request is.
                pass
        self.assertTrue(helper.sent, "the lane never reached the helper")
        return helper.sent[0]

    def test_extend(self):
        spec = self._dispatch({"mode": "extend", "prompt": "the hen keeps skating",
                               "video_path": str(self.clip), "extend_frames": "2"})
        self.assertEqual(spec["action"], "extend")
        self.assertEqual(spec["params"]["loras"], STYLE)

    def test_fflf(self):
        spec = self._dispatch({"mode": "keyframe", "prompt": "the hen crosses the pond",
                               "start_image": str(self.png), "end_image": str(self.png),
                               "width": "512", "height": "288", "frames": "49"})
        self.assertEqual(spec["action"], "generate_keyframe")
        self.assertEqual(spec["params"]["loras"], STYLE)

    def test_a2v_on_both_lanes(self):
        form = {"mode": "a2v", "prompt": "a singer", "audio": str(self.wav),
                "width": "512", "height": "288", "frames": "49"}
        for q8, action in ((True, "generate_a2v"), (False, "generate_a2v_distilled")):
            with self.subTest(q8=q8):
                spec = self._dispatch(form, q8=q8)
                self.assertEqual(spec["action"], action)
                self.assertEqual(spec["params"]["loras"], STYLE)


class TheHelperAttachesThem(unittest.TestCase):
    """get_kf_pipe / get_a2v_distilled_pipe executed from the helper source
    (the helper is a script — never imported) against stand-in pipelines."""

    def _ns(self, fake_modules: dict):
        import ast
        import types
        src = (ROOT / "mlx_warm_helper.py").read_text(encoding="utf-8")
        names = {"get_kf_pipe", "get_a2v_distilled_pipe", "_lora_fingerprint",
                 "_lora_fingerprint_base", "_stream_for", "_construct_pipeline",
                 "_filter_unsupported_kwargs", "_attach_loras"}
        tree = ast.parse(src)
        picked = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        self.assertEqual({n.name for n in picked}, names)
        ns = {"os": os, "threading": threading, "emit": lambda *a, **k: None,
              "_pipe_lock": threading.RLock(), "release_pipelines": lambda **k: None,
              "_install_lora_fusion_patches": lambda: None,
              "_install_video_decoder_patch": lambda: None,
              "_install_a2v_frame_rate_patch": lambda: None,
              "_resolve_lora_path": lambda path: path,
              "GEMMA_PATH": "/g", "LOW_MEMORY": True, "LOW_RAM_STREAM": True,
              "_kf_pipe": None, "_kf_model_dir": None, "_kf_weight_key": None,
              "_kf_lora_key": None, "_a2v_distilled_pipe": None,
              "_a2v_distilled_model_dir": None, "_a2v_distilled_lora_key": None}
        exec(compile(ast.Module(body=picked, type_ignores=[]), "mlx_warm_helper.py", "exec"), ns)
        for name, attrs in fake_modules.items():
            mod = types.ModuleType(name)
            mod.__dict__.update(attrs)
            sys.modules[name] = mod
            self.addCleanup(sys.modules.pop, name, None)
        return ns

    def test_fflf_attaches_and_rebuilds_on_a_new_set(self):
        built = []

        class KeyframeInterpolationPipeline:
            def __init__(self, **kw):
                built.append(self)
        ns = self._ns({"ltx_pipelines_mlx": {},
                       "ltx_pipelines_mlx.keyframe_interpolation":
                           {"KeyframeInterpolationPipeline": KeyframeInterpolationPipeline}})
        a = ns["get_kf_pipe"]("/q8", loras=STYLE)
        self.assertEqual(a._pending_loras, [("/loras/crisp.safetensors", 0.8)])
        self.assertIs(ns["get_kf_pipe"]("/q8", loras=STYLE), a)
        b = ns["get_kf_pipe"]("/q8", loras=[])
        self.assertIsNot(b, a)
        self.assertFalse(getattr(b, "_pending_loras", None))

    def test_q4_a2v_attaches_and_does_not_stream_with_adapters(self):
        built = []

        class A2VidDistilledPipeline:
            def __init__(self, model_dir=None, gemma_model_id=None, low_memory=None,
                         low_ram_streaming=False):
                self.stream = low_ram_streaming
                built.append(self)
        ns = self._ns({"a2vid_distilled": {"A2VidDistilledPipeline": A2VidDistilledPipeline},
                       "ltx_core_mlx": {}, "ltx_core_mlx.utils": {},
                       "ltx_core_mlx.utils.memory": {"aggressive_cleanup": lambda: None}})
        plain = ns["get_a2v_distilled_pipe"]("/q4")
        self.assertTrue(plain.stream)
        styled = ns["get_a2v_distilled_pipe"]("/q4", loras=STYLE)
        self.assertIsNot(styled, plain)
        self.assertFalse(styled.stream, "adapters keep the unfused branch")
        self.assertEqual(styled._pending_loras, [("/loras/crisp.safetensors", 0.8)])


# ---------------------------------------------------------------------------
# LTX-04 — Load Params turned an Audio → Video clip into a Text render
# ---------------------------------------------------------------------------

A2V_DOM = r"""
var BOOT = {a2v_max_frames: 0}, A2V_AREA_KNEE = 0.45e6, A2V_KNEE_FRAMES = 450;
var activePath = '/x.mp4', calls = [], posted = null, _activeLoras = [];
var AUDIO_STUDIO = {busy: false, audioPath: null, audioName: null, audioDuration: null};
window = globalThis; window.PHOSPHENE_CAP_TIER = 'q8';
const els = {};
function el(id, v) { return els[id] = {id, value: v === undefined ? '' : v, style: {}, dataset: {},
                                         innerHTML: '', textContent: '', disabled: false, open: false}; }
['audioStudioPrompt', 'audioStudioStatus', 'audioStudioGenBtn', 'audioStudioDurationVal',
 'audioStudioDurationWarn', 'audioConditioningScaleVal', 'audioConditioningScaleHint',
 'mvOneShotDetails', 'a2v_image'].forEach(i => el(i));
el('audioStudioWidth', '1024'); el('audioStudioHeight', '576'); el('audioStudioSeed', '-1');
el('audioStudioStart', '0'); el('audioStudioDuration', '7'); el('audioConditioningScale', '3.0');
const document = { getElementById: id => els[id] || null };
function audioModeSet(m) { calls.push('audioMode:' + m); }
function workflowSwitch(w) { calls.push('workflow:' + w); }
function audioStudioRenderSlots() {}
function pickerSetImage(k, v) { if (k === 'a2v_image') els.a2v_image.value = v; calls.push('picker:' + k + '=' + v); }
function _restoreLoraPicker(l) { calls.push('loras:' + l.length); _activeLoras = l; }
function setMode(m) { calls.push('setMode:' + m); }
function _parseIdeoCaption() { return null; }
function phosToast() {}
"""


@unittest.skipUnless(NODE, "node not on PATH")
class AnA2VClipReopensAsAudioToVideo(unittest.TestCase):

    def _run(self, params: dict) -> dict:
        from extract_panel_js import extract_function, panel_source  # noqa: PLC0415
        src = panel_source()
        fns = ("_a2vFramesForSeconds", "audioStudioDurationChanged", "a2vLaneAudioScale",
               "audioConditioningScaleChanged", "audioConditioningScaleReset",
               "a2vLoadParams", "loadParams", "audioStudioGenerate")
        js = A2V_DOM + "\n".join(extract_function(n, src) for n in fns) + """
const sidecar = %s;
(async () => {
  globalThis.fetch = async () => ({ok: true, json: async () => sidecar});
  await loadParams();
  globalThis.fetch = async (url, opts) => { posted = Object.fromEntries(opts.body); return {ok: true}; };
  await audioStudioGenerate();
  console.log(JSON.stringify({calls, posted, open: els.mvOneShotDetails.open}));
})();
""" % json.dumps({"output": "/x.mp4", "params": params})
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def _params(self, **over):
        base = {"mode": "a2v", "prompt": "a singer at a microphone", "audio": "/songs/take.wav",
                "image": "/uploads/singer.png", "width": 832, "height": 480, "frames": 241,
                "seed": "-1", "seed_used": 4242, "audio_start_time": 30.0,
                "audio_conditioning_scale": "4", "loras": [{"path": "/l.safetensors", "strength": 1}]}
        base.update(over)
        return base

    def test_the_round_trip_keeps_mode_track_and_conditioning(self):
        out = self._run(self._params())
        self.assertNotIn("setMode:t2v", out["calls"])
        self.assertIn("workflow:audio", out["calls"])
        self.assertIn("loras:1", out["calls"])
        self.assertTrue(out["open"])
        got = out["posted"]
        self.assertEqual(got["mode"], "a2v")
        self.assertEqual(got["audio"], "/songs/take.wav")
        self.assertEqual(got["image"], "/uploads/singer.png")
        self.assertEqual((got["width"], got["height"], got["frames"]), ("832", "480", "241"))
        self.assertEqual(got["seed"], "4242")
        self.assertEqual(got["audio_start_time"], "30")
        self.assertEqual(got["audio_conditioning_scale"], "4")

    def test_auto_stays_auto(self):
        out = self._run(self._params(audio_conditioning_scale=""))
        self.assertNotIn("audio_conditioning_scale", out["posted"])

    def test_the_pre_4152_float_default_reopens_as_auto(self):
        # Old jobs stored a FLOAT 1.0 on every a2v render; on Q8 that is audio
        # guidance off. The server reads it as Auto; so must Load Params
        # (Codex integration review, 2026-09-24).
        out = self._run(self._params(audio_conditioning_scale=1.0))
        self.assertNotIn("audio_conditioning_scale", out["posted"])

    def test_a_chosen_one_is_kept(self):
        out = self._run(self._params(audio_conditioning_scale="1"))
        self.assertEqual(out["posted"]["audio_conditioning_scale"], "1")


# ---------------------------------------------------------------------------
# LTX-05 — Load Params lost Sliding windows and the per-window prompts
# ---------------------------------------------------------------------------

# Everything loadParams touches that is not under test resolves to a no-op
# (or an auto-created element); the functions under test are the real ones.
LOOSE_SHIM = r"""
var _els = {};
function _mk(id, v) {
  return _els[id] = {id, value: v === undefined ? '' : String(v), textContent: '', dataset: {},
    hidden: false, style: {}, innerHTML: '', checked: false, open: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    setAttribute(){}, getAttribute(){ return null; }, dispatchEvent(){ return true; },
    addEventListener(){}, focus(){}};
}
var document = {getElementById: id => _els[id] || _mk(id), querySelector: () => null,
                  querySelectorAll: () => [], body: {dataset: {}, classList: {toggle(){}}}};
var BOOT = {ltx: {qualities: [{key: 'balanced', pipeline: 'distilled'}]}}, currentMode = 't2v',
    activePath = '/x.mp4', FPS = 24;
_mk('mode', 't2v'); _mk('quality', 'balanced'); _mk('frames', '481');
_mk('window_prompts_text', 'STALE LINE'); _mk('window_invariants', 'stale');
var _noop = function () {};
var SHIM = new Proxy({}, {
  has: (_, k) => k !== Symbol.unscopables && !(k in globalThis) && typeof k === 'string',
  get: (_, k) => _noop,
});
"""


@unittest.skipUnless(NODE, "node not on PATH")
class WindowedClipsReloadAsWindowed(unittest.TestCase):

    def _load(self, params: dict) -> dict:
        from extract_panel_js import extract_function, panel_source  # noqa: PLC0415
        src = panel_source()
        fns = ("_qualityUsesHq", "temporalModeAllowed", "setTemporalMode",
               "windowPromptsInput", "loadParams")
        js = LOOSE_SHIM + "with (SHIM) {\n" + "\n".join(
            extract_function(n, src) for n in fns) + """
globalThis.fetch = async () => ({ok: true, json: async () => (%s)});
loadParams().then(() => console.log(JSON.stringify({
  temporal: document.getElementById('temporal_mode').value,
  prompts: document.getElementById('window_prompts').value,
  invariants: document.getElementById('window_invariants').value})),
  e => { console.error(e); process.exit(1); });
}""" % json.dumps({"output": "/x.mp4", "params": params})
        r = subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr[-2000:])
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_a_windowed_sidecar_round_trips_through_make_job(self):
        form = {"mode": "t2v", "prompt": "a hen skates", "frames": "481",
                "quality": "balanced", "temporal_mode": "windows",
                "window_prompts": json.dumps(["opening", "", "the hen jumps", "landing"]),
                "window_invariants": "same pond, same dusk light"}
        saved = P.make_job(form)["params"]
        self.assertEqual(saved["long_mode"], "windows")
        got = self._load(saved)
        self.assertEqual(got["temporal"], "windows")
        again = P.make_job({**form, "temporal_mode": got["temporal"],
                            "window_prompts": got["prompts"],
                            "window_invariants": got["invariants"]})["params"]
        self.assertEqual(again["long_mode"], "windows")
        self.assertEqual(again["window_prompts"], saved["window_prompts"])
        self.assertEqual(again["window_invariants"], saved["window_invariants"])

    def test_a_native_clip_clears_stale_window_text(self):
        saved = P.make_job({"mode": "t2v", "prompt": "x", "frames": "121"})["params"]
        got = self._load(saved)
        self.assertEqual(got["temporal"], "native")
        self.assertEqual(got["prompts"], "")
        self.assertEqual(got["invariants"], "")


# ---------------------------------------------------------------------------
# LTX-06 / LTX-10 — hardware clamps squashed the aspect and left the /64 grid
# ---------------------------------------------------------------------------

def _fake_ffmpeg(cmd, *a, **k):
    Path(cmd[-1]).write_bytes(b"enc")
    return "", ""


class HardwareClampsKeepShapeAndGrid(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="ltx06-"))
        self.wav = self.tmp / "song.wav"
        self.wav.write_bytes(b"RIFF0000WAVE")
        self.png = self.tmp / "a.png"
        subprocess.run([str(P.FFMPEG), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=green:s=64x64:d=1", "-frames:v", "1", "-update", "1",
                        str(self.png)], check=True)

    def _dispatch(self, form: dict, tier: str, *, profile=None) -> tuple[dict, dict]:
        helper, sidecars = _Helper(), []
        job = P.make_job(form)
        job["started_ts"] = time.time()
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", self.tmp), \
             mock.patch.object(P, "SYSTEM_TIER", tier), \
             mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES[tier]), \
             mock.patch.object(P, "GENERATION_PROFILE",
                               profile or P._select_generation_profile(128.0, "pro")), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "run_ffmpeg_tracked", _fake_ffmpeg), \
             mock.patch.object(P, "set_hidden", lambda *a, **k: None), \
             mock.patch.object(P, "write_sidecar",
                               lambda _p, data: sidecars.append(data) or True):
            P.run_job_inner(job)
        spec = helper.sent[0]["params"]
        return spec, (sidecars[-1]["params"] if sidecars else job["params"])

    def _check(self, got, want_long, req):
        w, h = got
        self.assertEqual((w % 64, h % 64), (0, 0), got)
        self.assertLessEqual(max(w, h), want_long)
        scale = want_long / max(req)
        for have, asked in zip(got, req):
            self.assertLess(abs(have - asked * scale), 64, (got, req))

    def test_a2v_scales_both_sides_and_records_what_rendered(self):
        for req in ((1280, 704), (704, 1280), (1024, 1024)):
            with self.subTest(req=req):
                spec, side = self._dispatch(
                    {"mode": "a2v", "prompt": "a singer", "audio": str(self.wav),
                     "width": str(req[0]), "height": str(req[1]), "frames": "49",
                     "quality": "high"}, "base")
                got = (spec["width"], spec["height"])
                self._check(got, 768, req)
                self.assertEqual((side["width"], side["height"]), got)

    def test_the_base_tier_t2v_clamp_stays_on_the_grid(self):
        spec, side = self._dispatch({"mode": "t2v", "prompt": "a lighthouse", "quality": "standard",
                                     "width": "1280", "height": "704", "frames": "49"}, "base")
        got = (spec["width"], spec["height"])
        self._check(got, 768, (1280, 704))
        self.assertEqual((side["width"], side["height"]), got)

    def test_the_fflf_clamp_stays_on_the_grid(self):
        spec, side = self._dispatch({"mode": "keyframe", "prompt": "across the pond",
                                     "start_image": str(self.png), "end_image": str(self.png),
                                     "width": "1280", "height": "704", "frames": "49"}, "standard")
        got = (spec["width"], spec["height"])
        self._check(got, 768, (1280, 704))
        self.assertEqual((side["width"], side["height"]), got)

    def test_the_compact_profile_clamp_stays_on_the_grid(self):
        job = P.make_job({"mode": "t2v", "prompt": "x", "quality": "balanced",
                          "width": "1536", "height": "832", "frames": "49"})
        with mock.patch.object(P, "GENERATION_PROFILE", P._select_generation_profile(48.0, "standard")):
            P._apply_generation_profile_to_job(job)
        got = (job["params"]["width"], job["params"]["height"])
        self._check(got, 1024, (1536, 832))


# ---------------------------------------------------------------------------
# LTX-07 — an apostrophe in the output folder broke the windows join
# ---------------------------------------------------------------------------

def _frames_clip(path: Path, frames: int) -> None:
    subprocess.run([str(P.FFMPEG), "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"testsrc=s=64x64:r=24:d={frames / 24:.4f}", "-f", "lavfi", "-i",
                    "anullsrc=r=48000:cl=stereo", "-frames:v", str(frames),
                    "-t", f"{frames / 24:.4f}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", str(path)], check=True)


def _probe_frames(path: Path) -> int:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
                          "-show_entries", "stream=nb_read_packets", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True).stdout.strip()
    return int(out or 0)


class TheWindowsJoinSurvivesAnyFolderName(unittest.TestCase):

    def test_the_real_join_under_awkward_output_roots(self):
        import ltx_windows as lw
        if not shutil.which("ffprobe"):
            self.skipTest("ffprobe not on PATH")
        for name in ("Artist's Drive", "with space", "出力 フォルダ"):
            with self.subTest(root=name):
                root = Path(tempfile.mkdtemp(prefix="ltx07-")) / name / "outputs"
                root.mkdir(parents=True)
                first = root / "clip_w0.mp4"
                _frames_clip(first, 121)
                raw = root / "clip.mp4"

                def fake_run(spec):
                    # an extend: context + the new window, as one clip
                    _frames_clip(Path(spec["params"]["output_path"]), 121 + 112)
                    return {"elapsed_sec": 1}
                with mock.patch.object(P, "OUTPUT", root), \
                     mock.patch.object(P.HELPER, "run", side_effect=fake_run), \
                     mock.patch.object(P, "set_hidden", lambda *a, **k: None), \
                     mock.patch.object(P, "pack_path", return_value=Path("/q8")), \
                     mock.patch.object(P, "hq_weights",
                                       return_value={"dev_transformer": "dev.safetensors"}):
                    out = P._run_windows_chain({"id": "j7"}, {"prompt": "a walk", "seed": 7},
                                               lw.plan_windows(241), first, raw, 241)
                self.assertTrue(raw.is_file())
                self.assertEqual(out["output_frames"], 241)
                self.assertGreaterEqual(_probe_frames(raw), 230)

    def test_the_manifest_line_quotes_an_apostrophe(self):
        self.assertEqual(P.ffconcat_line("/V/Artist's Drive/a.mp4"),
                         "file '/V/Artist'\\''s Drive/a.mp4'\n")


# ---------------------------------------------------------------------------
# LTX-08 — the first sliding-window line was saved but never rendered
# ---------------------------------------------------------------------------

class EveryWindowRendersItsOwnLine(unittest.TestCase):

    def test_three_windows_render_the_three_saved_prompts(self):
        tmp = Path(tempfile.mkdtemp(prefix="ltx08-"))
        helper, sidecars = _Helper(), []
        job = P.make_job({"mode": "t2v", "prompt": "BASE PROMPT", "quality": "balanced",
                          "width": "512", "height": "320", "frames": "241",
                          "temporal_mode": "windows",
                          "window_prompts": json.dumps(["OPENING OVERRIDE", "SECOND", "THIRD"])})
        job["started_ts"] = time.time()
        with mock.patch.object(P, "HELPER", helper), \
             mock.patch.object(P, "OUTPUT", tmp), \
             mock.patch.object(P, "SYSTEM_CAPS", P.CAPABILITIES["pro"]), \
             mock.patch.object(P, "GENERATION_PROFILE", P._select_generation_profile(128.0, "pro")), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "run_ffmpeg_tracked", _fake_ffmpeg), \
             mock.patch.object(P, "set_hidden", lambda *a, **k: None), \
             mock.patch.object(P, "write_sidecar",
                               lambda _p, data: sidecars.append(data) or True):
            P.run_job_inner(job)
        sent = [s["params"]["prompt"] for s in helper.sent]
        saved = sidecars[-1]["windows"]["prompts"]
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0], "OPENING OVERRIDE")
        self.assertEqual(sent, saved)


# ---------------------------------------------------------------------------
# LTX-09 — One Shot previews were written where the status reader never looked
# ---------------------------------------------------------------------------

class TheTakesPreviewFollowsThePartRendering(_TakeHarness):

    def _publish(self, job_id: str) -> Path:
        d = P.live_preview_dir(job_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "status.json").write_text(json.dumps({"forward": 8, "total_forwards": 10}))
        (d / "preview_latest.png").write_bytes(b"png")
        return d

    def test_status_and_stop_early_resolve_the_active_part(self):
        from panel import routes_queue                           # noqa: PLC0415
        PM = routes_queue.P or P
        job = P.make_job({"mode": "t2v", "engine": "ltx", "prompt": "a hen skates",
                          "take_seconds": "30"})
        seen = []

        def part(child):
            seen.append(job.get("preview_id"))
            d = self._publish(child["id"])
            got = P._preview_progress(job, 12.0, "denoise")
            self.assertIn("preview", got, "status sees the part's preview")
            self.assertIn(child["id"], got["preview"]["url"])
            # Stop early, through the real route, lands in the part's folder.
            saved = PM.STATE["current"]
            replies = []

            class _Req:
                def _json(self, body, status=200):
                    replies.append((status, body))
            with PM.LOCK:
                PM.STATE["current"] = job
            try:
                with mock.patch.object(PM, "live_preview_dir", P.live_preview_dir):
                    routes_queue.post_stop(_Req(), "/stop", {"mode": ["early"]}, "")
            finally:
                with PM.LOCK:
                    PM.STATE["current"] = saved
            self.assertEqual(replies[0][0], 200, replies)
            self.assertTrue((d / "ABORT").is_file())
            (d / "ABORT").unlink()
            self._fake_part(child)

        with mock.patch.object(P, "run_job_inner", part), \
             mock.patch.object(P, "STATE_DIR", self.tmp / "state"):
            P.run_take_job_inner(job)
        self.assertEqual(seen, [f"{job['id']}-p1", f"{job['id']}-p2", f"{job['id']}-p3"])
        self.assertNotIn("preview_id", job, "cleared for the join")

    def test_an_ordinary_job_is_unchanged(self):
        self.assertEqual(P.live_preview_job_id({"id": "j-1"}), "j-1")


if __name__ == "__main__":
    unittest.main()
