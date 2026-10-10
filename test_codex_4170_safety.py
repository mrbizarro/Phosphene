#!/usr/bin/env python3
"""4.17.0 Codex review — the SAFETY area (Stop, queue, job lifecycle, One Shot
recovery, Retry smaller, queue edit/restore, draft autosave). Every finding
fixed gets its pin here. Findings:
~/AI/projects/phosphene/review-2026-09-29-ux/codex/findings_safety.md.
JS is executed in node where it is behaviour.
"""
from __future__ import annotations

import copy
import os
import io
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import mlx_ltx_panel as p                                             # noqa: E402
from extract_panel_js import extract_function                         # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]
NODE = shutil.which("node")
JS = ROOT / "webapp" / "js"


def _node(script: str):
    if NODE is None:
        pytest.skip("node not on PATH")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = fh.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr[-3000:]
        return json.loads(r.stdout.strip().splitlines()[-1])
    finally:
        Path(path).unlink(missing_ok=True)


class _H:
    """A route handler double: a form body in, the JSON reply captured."""

    def __init__(self, form=None, body=b""):
        self.status, self.payload = None, None
        self._form = form or {}
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)

    def _read_form_body(self):
        return b"", self._form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


@pytest.fixture
def queue_state():
    """A private queue/history/current for the test; the real one comes back."""
    with p.LOCK:
        saved = {k: copy.deepcopy(p.STATE.get(k)) for k in ("queue", "history", "current", "paused")}
        p.STATE["queue"], p.STATE["history"], p.STATE["current"] = [], [], None
        p.STATE["paused"] = True
        undo = getattr(p, "_QUEUE_UNDO", {})
        undo.clear()
    with mock.patch.object(p, "persist_queue", lambda: None):
        yield p.STATE
    with p.LOCK:
        for k, v in saved.items():
            p.STATE[k] = v
        undo.clear()


# ---- SAFETY-2: Retry smaller answers success, queues exactly one ----------
def test_safety_2_retry_smaller_replies_ok_and_queues_one(queue_state):
    queue_state["history"].append({
        "id": "j-src", "status": "failed", "error": "GPU watchdog",
        "params": {"mode": "t2v", "engine": "ltx", "prompt": "a fox", "quality": "high",
                   "width": 1280, "height": 704, "frames": 121}})
    h = _H({"id": ["j-src"], "smaller": ["1"]})
    routes_queue.post_queue_retry(h, "/queue/retry", {}, "")
    assert h.status == 200 and h.payload["ok"] is True, h.payload
    assert len(queue_state["queue"]) == 1
    got = queue_state["queue"][0]["params"]
    assert got["quality"] == "standard"
    assert max(got["width"], got["height"]) < 1280 and got["frames"] < 121
    assert h.payload["id"] == queue_state["queue"][0]["id"]


# ---- SAFETY-8/9/10: Clear queue -> Undo restores the server's own removal --
def _qjob(jid, prompt="x"):
    return {"id": jid, "status": "queued", "params": {"mode": "t2v", "prompt": prompt}}


def _clear():
    h = _H()
    routes_queue.post_queue_clear(h, "/queue/clear", {}, "")
    assert h.status == 200
    return h.payload


def _restore(body: dict):
    raw = json.dumps(body).encode()
    h = _H(body=raw)
    routes_queue.post_queue_restore(h, "/queue/restore", {}, "application/json")
    return h


def test_safety_9_undo_restores_exactly_what_clear_removed(queue_state):
    # The browser's last poll showed [A, B]; the worker then started A, and
    # another tab added C — all before Clear reached the server.
    queue_state["queue"][:] = [_qjob("j-B", "b"), _qjob("j-C", "c")]
    queue_state["current"] = dict(_qjob("j-A", "a"), status="running")
    out = _clear()
    assert out["cleared"] == 2 and out["ids"] == ["j-B", "j-C"] and out["undo_token"]
    assert queue_state["queue"] == []
    h = _restore({"token": out["undo_token"]})
    assert h.status == 200 and h.payload["restored"] == 2, h.payload
    assert [j["id"] for j in queue_state["queue"]] == ["j-B", "j-C"]   # A not duplicated, C kept
    # single use
    h2 = _restore({"token": out["undo_token"]})
    assert h2.status == 410 and [j["id"] for j in queue_state["queue"]] == ["j-B", "j-C"]


def test_safety_9_an_expired_or_superseded_undo_restores_nothing(queue_state):
    queue_state["queue"][:] = [_qjob("j-1")]
    first = _clear()
    queue_state["queue"][:] = [_qjob("j-2")]
    _clear()                                  # a second Clear supersedes the first Undo
    assert _restore({"token": first["undo_token"]}).status == 410
    assert queue_state["queue"] == []


def test_safety_9_the_undo_toast_sends_the_server_token_not_a_snapshot():
    src = (JS / "queue.js").read_text(encoding="utf-8")
    fn = extract_function("requestClearQueue", src)
    out = _node(r"""
const sent = [];
let toastEl = null;
global.LAST_STATUS = { queue: [{ id: 'stale-A', params: { prompt: 'a' } }] };
global.poll = () => {};
global.api = async (path) => { sent.push(['api', path]); return { cleared: 2, ids: ['j-B', 'j-C'], undo_token: 'tok123' }; };
global.fetch = async (path, opts) => { sent.push(['fetch', path, opts.body]);
  return { ok: true, status: 200, json: async () => ({ ok: true, restored: 2 }) }; };
const toasts = [];
global.phosToast = (msg) => { toasts.push(msg); if (toastEl) return null;
  toastEl = { children: [], appendChild(c) { this.children.push(c); }, remove() {} }; return toastEl; };
global.document = { createElement: () => ({}) };
""" + fn + r"""
(async () => {
  requestClearQueue();
  await new Promise(r => setTimeout(r, 10));
  await toastEl.children[0].onclick({ preventDefault() {} });
  console.log(JSON.stringify({ sent, toasts }));
})();
""")
    assert out["sent"][0] == ["api", "/queue/clear"]
    assert out["sent"][1][:2] == ["fetch", "/queue/restore"]
    assert json.loads(out["sent"][1][2]) == {"token": "tok123"}
    assert out["toasts"][0].startswith("Cleared 2 queued renders")      # the server's count
    assert out["toasts"][1] == "Restored 2 renders"


def test_safety_10_restoring_200_jobs_keeps_every_id_unique(queue_state):
    queue_state["queue"][:] = [_qjob(f"j-orig-{i:03d}", f"p{i}") for i in range(200)]
    tok = _clear()["undo_token"]
    with mock.patch.object(p.time, "time", return_value=1_700_000_000.0), \
            mock.patch.object(p.random, "randrange", return_value=7):
        h = _restore({"token": tok})
        assert h.status == 200
        assert [j["id"] for j in queue_state["queue"]] == [f"j-orig-{i:03d}" for i in range(200)]
        # the API's params-only form mints ids too — through the one allocator
        queue_state["queue"][:] = []
        h = _restore({"jobs": [{"params": {"prompt": f"q{i}"}} for i in range(200)]})
        assert h.status == 200
        ids = [j["id"] for j in queue_state["queue"]]
        assert len(ids) == 200 and len(set(ids)) == 200


def test_safety_10_no_route_mints_ids_from_a_clock_and_a_small_random():
    for f in (ROOT / "panel" / "routes_queue.py", ROOT / "mlx_ltx_panel.py"):
        src = f.read_text(encoding="utf-8")
        assert "randrange(0xfff):03x}" not in src, f


def test_safety_8_undo_reattaches_restored_storyboard_jobs(queue_state):
    board = {"id": "sb_20260929_abcdef", "shots": [
        {"n": 1, "uid": "shot_00000001", "status": "queued", "draft_job_id": "j-shot1"}]}
    queue_state["queue"][:] = [_qjob("j-shot1")]
    tok = _clear()["undo_token"]
    # the board polls between Clear and Undo: the shot must NOT be unhooked
    p._sb_reconcile(board)
    shot = board["shots"][0]
    assert shot.get("draft_job_id") == "j-shot1" and shot["status"] == "queued"
    assert _restore({"token": tok}).status == 200
    # the restored job (same id) finishes — its clip lands on the original shot
    job = queue_state["queue"].pop(0)
    job.update(status="done", output_path="/o/shot1.mp4")
    queue_state["history"].insert(0, job)
    with mock.patch.object(p, "_probe_video_dims", return_value=(0, 0)), \
            mock.patch.object(p, "_sb_sidecar_seed", return_value=None):
        p._sb_reconcile(board)
        p._sb_reconcile(board)
    assert shot["draft_output"] == "/o/shot1.mp4" and shot["status"] == "done"
    assert shot.get("takes") in (None, [])                                 # attached exactly once


def test_safety_8_after_the_undo_window_film_18_still_unhooks_the_shot(queue_state):
    board = {"id": "sb_x", "shots": [
        {"n": 1, "uid": "shot_00000002", "status": "queued", "draft_job_id": "j-gone"}]}
    queue_state["queue"][:] = [_qjob("j-gone")]
    _clear()
    with mock.patch.object(p.time, "monotonic", return_value=p.time.monotonic() + p.QUEUE_UNDO_TTL_SEC + 1):
        p._sb_reconcile(board)
    assert board["shots"][0]["status"] == "pending" and "draft_job_id" not in board["shots"][0]


# ---- SAFETY-3: a Stop confirmation only ever stops the job it described ----
def test_safety_3_stale_stop_ids_leave_the_successor_running(queue_state):
    queue_state["current"] = {"id": "j-B", "status": "running",
                              "params": {"take": {"seconds": 20}}, "progress": {}}
    with mock.patch.object(p.HELPER, "kill") as kill:
        h = _H()
        routes_queue.post_stop(h, "/stop", {"id": ["j-A"]}, "")
        assert h.status == 409 and h.payload.get("stale") is True
        assert not kill.called and "cancel_requested" not in queue_state["current"]
        h = _H()
        routes_queue.post_stop_after_part(h, "/stop/after_part", {"id": ["j-A"]}, "")
        assert h.status == 409 and "stop_after_part" not in queue_state["current"]
        # the job the dialog named is still stoppable, both ways
        h = _H()
        routes_queue.post_stop_after_part(h, "/stop/after_part", {"id": ["j-B"]}, "")
        assert h.status == 200 and queue_state["current"]["stop_after_part"] is True
        with mock.patch.object(p.agent_image_engine, "kill_active_image_procs", return_value=0):
            h = _H()
            routes_queue.post_stop(h, "/stop", {"id": ["j-B"]}, "")
        assert h.status == 200 and kill.called and queue_state["current"]["cancel_requested"]


def test_safety_3_the_dialog_sends_the_id_it_was_opened_for():
    src = (JS / "queue.js").read_text(encoding="utf-8")
    fn = extract_function("requestStop", src)
    out = _node(r"""
const sent = []; let modal = null;
global.BOOT = { tier: {} };
global.LAST_STATUS = { server_now: 1000, current: { id: 'j-A', started_ts: 1,
  params: { prompt: 'a fox', take: { parts: [1, 2, 3] } }, take_progress: { part: 1, parts: 3 } } };
global._phModalShow = (o) => { modal = o; };
global.phosToast = () => {};
global.poll = () => {};
global.snippet = (s) => s;
global.api = async (path, m) => { sent.push(path); return { ok: true }; };
global.fetch = async (path) => { sent.push(path); return { json: async () => ({ ok: true }) }; };
""" + fn + r"""
(async () => {
  requestStop();
  // A finishes, B starts, THEN the user confirms either button
  LAST_STATUS.current = { id: 'j-B', params: { prompt: 'b' } };
  modal.onPrimary(); modal.onSecondary();
  await new Promise(r => setTimeout(r, 10));
  console.log(JSON.stringify(sent));
})();
""")
    assert out == ["/stop/after_part?id=j-A", "/stop?id=j-A"]


# ---- SAFETY-4: editing a queued job keeps its engine and whole recipe -------
_RECORDING_SHIM = r"""
globalThis.calls = [];
globalThis._els = {};
globalThis._mk = function (id, v) {
  return _els[id] = {id, value: v === undefined ? '' : String(v), textContent: '', dataset: {},
    hidden: false, style: {}, innerHTML: '', checked: false,
    classList: {toggle(){}, add(){}, remove(){}, contains(){ return false; }},
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    setAttribute(){}, getAttribute(){ return null; }, addEventListener(){}, focus(){}};
};
globalThis.document = {getElementById: id => _els[id] || _mk(id), querySelector: () => null,
                querySelectorAll: () => [], body: {dataset: {}, classList: {toggle(){}}}};
globalThis.window = globalThis;
Object.assign(globalThis, {BOOT: {tier: {}}, activePath: '/some/other/clip.mp4', FPS: 24, ASPECTS: {}});
_mk('prompt', 'FORM PROMPT'); _mk('seed', '99'); _mk('steps', '30');
globalThis.RECORD = ['setEngine', 'setMode', 'setQuality', 'setH3Speed', 'setH3Steps', 'setH3Upscale',
  'setH3Orientation', '_h3ApplyShape', 'setH3Tier', 'setH3ChainPrompts', 'setUpscale',
  'setUpscaleMethod', 'setTemporalMode', 'setAccel', '_flashActionDone', '_restoreLoraPicker',
  'charactersLoadParams', 'workflowSwitch'];
globalThis.SHIM = new Proxy({}, {
  has: (_, k) => k !== Symbol.unscopables && typeof k === 'string' && !(k in globalThis),
  get: (_, k) => {
    if (k === 'engineById') return () => true;
    if (k === 'h3CellFor') return () => true;
    if (k === 'h3SpeedOfParams') return (p) => (p.h3_tristep ? 'fast' : 'quality');
    if (k === 'setH3Quality') return function () {};
    if (k === 'framesToDuration') return (f) => f / 24;
    if (RECORD.includes(k)) return function () { calls.push([k].concat([].slice.call(arguments)));
      return k === 'setEngine' ? arguments[0] : undefined; };
    return function () {};
  },
});
globalThis.fetch = async () => { calls.push(['fetch']); return { ok: false }; };
"""


def _load_recipe(params):
    from extract_panel_js import panel_source  # noqa: PLC0415
    fn = extract_function("loadParams", panel_source())
    return _node(_RECORDING_SHIM + "with (SHIM) {\n" + fn + """
loadParams({ params: %s }).then(() => console.log(JSON.stringify({ calls,
  prompt: document.getElementById('prompt').value, seed: document.getElementById('seed').value,
  steps: document.getElementById('steps').value })),
  e => { console.error(e && e.stack || e); process.exit(1); });
}""" % json.dumps(params))


def test_safety_4_a_queued_h3_job_restores_its_engine_and_h3_recipe(monkeypatch):
    # As an H3-capable Mac (issue #90): below the RAM floor make_job falls
    # back to LTX by design, and there is no H3 recipe left to restore.
    monkeypatch.setenv("LTX_H3_FORCE_CAPABLE", "1")
    h3 = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "a hen skates",
                     "h3_quality": "high", "h3_length": "15s", "h3_tristep": "1",
                     "h3_chain_prompts": json.dumps(["w1", "w2", "w3"]), "seed": "-1"})["params"]
    out = _load_recipe(h3)
    names = [c[0] for c in out["calls"]]
    assert "fetch" not in names                         # a queued job has no sidecar
    assert ["setEngine", "h3", {"persist": False}] in out["calls"]
    assert any(c[0] in ("_h3ApplyShape", "setH3Tier") for c in out["calls"])
    assert ["setH3Speed", "fast" if h3.get("h3_tristep") else "quality"] in out["calls"]
    assert any(c[0] == "setH3ChainPrompts" for c in out["calls"])
    assert "_flashActionDone" not in names and "charactersLoadParams" not in names
    assert out["prompt"] == "a hen skates"


def test_safety_4_a_queued_ltx_job_restores_steps_and_export_method(monkeypatch):
    # 4.17.4: on a DISTILLED quality make_job now clamps steps to 8 (the lane
    # cannot run anything else - clamp_distilled_steps), so a restorable,
    # non-default count has to live on an HQ quality, where `steps` is kept.
    # 4.19.1: as a Mac WITH the PiperSR upscaler (#90 class) - make_job folds
    # pipersr to lanczos where it is not installed, which is every fresh
    # install, so this read the machine instead of testing the restore.
    monkeypatch.setattr(p, "PIPERSR_UPSCALE_ENABLED", True)
    ltx = p.make_job({"mode": "t2v", "engine": "ltx", "prompt": "a fox", "steps": "12",
                      "upscale": "fit_720p", "upscale_method": "pipersr", "quality": "high"})["params"]
    assert ltx["steps"] == 12
    ltx["source"] = "characters"          # would have jumped to the Characters tab
    out = _load_recipe(ltx)
    assert ["setEngine", "ltx", {"persist": False}] in out["calls"]
    assert ["setUpscaleMethod", "pipersr"] in out["calls"]
    assert str(out["steps"]) == "12" and out["prompt"] == "a fox"
    assert "charactersLoadParams" not in [c[0] for c in out["calls"]]


def test_safety_4_edit_row_restores_through_load_params():
    src = (JS / "queue.js").read_text(encoding="utf-8")
    fn = extract_function("editQueuedJob", src)
    out = _node(r"""
let got = null; let editingSeen = 'unset';
var _editingQueuedJobId = null;
const _EDITABLE_QUEUE_MODES = ['t2v', 'i2v', 'i2v_clean_audio', 'extend', 'keyframe'];
global.LAST_STATUS = { queue: [{ id: 'j-h3', params: { mode: 't2v', engine: 'h3', prompt: 'p',
  h3_tier: 'high_15s', h3_chain_prompts: ['a', 'b', 'c'] } }] };
global.loadParams = async (recipe) => { got = recipe; editingSeen = _editingQueuedJobId; };
global.phosToast = () => {};
global.document = { getElementById: () => ({ hidden: true }), querySelector: () => null };
""" + fn + r"""
editQueuedJob('j-h3').then(() => console.log(JSON.stringify({ got, editingSeen, after: _editingQueuedJobId })));
""")
    assert out["got"]["params"]["engine"] == "h3" and out["got"]["params"]["h3_tier"] == "high_15s"
    assert out["editingSeen"] is None and out["after"] == "j-h3"


def test_safety_4_update_keeps_what_the_form_cannot_express(queue_state, monkeypatch):
    monkeypatch.setenv("LTX_H3_FORCE_CAPABLE", "1")     # see the test above
    orig = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "old", "h3_quality": "high",
                       "h3_length": "10s", "session_tag": "sb:sb_1#2"})
    orig["params"]["source"] = "storyboard"
    queue_state["queue"].append(orig)
    form = {"mode": ["t2v"], "engine": ["h3"], "prompt": ["new"], "h3_quality": ["high"],
            "h3_length": ["10s"]}
    out = p.queue_update_job(orig["id"], form)
    assert out["ok"] is True
    got = queue_state["queue"][0]["params"]
    assert got["prompt"] == "new" and got["engine"] == "h3"
    assert got["source"] == "storyboard" and got["session_tag"] == "sb:sb_1#2"


# ---- One Shot recovery: SAFETY-5 / 6 / 7 / 11 / 12 -------------------------
def _clip(path: Path, secs: float = 0.2) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=red:s=32x32:d={secs}",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", str(secs),
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True)


@pytest.fixture
def take_env(tmp_path, monkeypatch, queue_state):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not on PATH")
    out_dir = tmp_path / "out"; out_dir.mkdir()
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "UPLOADS", tmp_path / "up")
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(p, "HIDDEN_PATHS", set())
    monkeypatch.setattr(p, "persist_hidden", lambda: None)
    monkeypatch.setattr(p, "take_drift", lambda *a, **k: {"delta": 0.0, "drifted": False})
    monkeypatch.setattr(p, "take_expects_speech", lambda *a, **k: False)
    env = {"made": [], "fail_at": None, "secs": 0.2}

    def fake_render(child):
        n = len(env["made"]) + 1
        if env["fail_at"] == n:
            raise RuntimeError("the engine died")
        out = out_dir / f"{child['id']}.mp4"
        _clip(out, env["secs"])
        seed = 700 + n
        child["output_path"] = str(out)
        child["params"]["seed_used"] = seed
        # both engines write the seed under params (not top level)
        (out.parent / (out.name + ".json")).write_text(json.dumps(
            {"output": str(out), "params": {**child["params"], "seed_used": seed}}))
        env["made"].append(out)

    monkeypatch.setattr(p, "run_h3_job_inner", fake_render)
    monkeypatch.setattr(p, "run_job_inner", fake_render)
    return env


def _take_job(handoff="last"):
    job = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "a hen skates",
                      "take_seconds": "45", "take_handoff": handoff,
                      "beats": json.dumps([f"beat {i}" for i in range(9)])})
    job["params"]["take"]["handoff"] = handoff
    return job


def _manifest_of(path):
    return json.loads(Path(str(path) + ".json").read_text())["one_shot_unfinished"]


def test_safety_5_speech_handoff_recovery_card_is_a_visible_output(take_env, monkeypatch):
    take_env["secs"] = 1.0
    take_env["fail_at"] = 2
    monkeypatch.setattr(p, "take_handoff_points", lambda out: (0.6, 0.8))
    job = _take_job("speech")
    assert job["params"]["take"]["handoff"] == "speech"
    with pytest.raises(RuntimeError, match="engine died"):
        p.run_take_job_inner(job)
    part1 = take_env["made"][0]
    assert str(part1).startswith(str(p.OUTPUT))
    assert str(part1) not in p.HIDDEN_PATHS                     # the card is shown
    man = _manifest_of(part1)                                  # manifest on the VISIBLE part
    assert man["visible"] == [str(part1)]
    assert man["outs"][0].endswith("part1_speech.mp4")          # join input kept separately
    assert man["part_seeds"] == [701]
    # /outputs lists it with the recovery flag
    old = p.time.time() - 60
    os.utime(part1, (old, old))
    items = p.list_outputs(limit=0)
    card = next(o for o in items if o.get("path") == str(part1))
    assert card["one_shot_unfinished"] is True
    # both recovery actions accept that path
    h = _H({"path": [str(part1)]})
    routes_queue.post_take_resume(h, "/take/resume", {}, "")
    assert h.status == 200, h.payload
    h = _H({"path": [str(part1)]})
    routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
    assert h.status == 200, h.payload
    assert str(part1) in p.HIDDEN_PATHS                         # the joined clip replaces it


def test_safety_6_resume_rebuilds_a_handoff_frame_stop_never_wrote(take_env, monkeypatch):
    real = p._take_ffmpeg
    calls = {"n": 0}

    def stop_during_frame(cmd, label, job, pgid_key="mux_pgid"):
        if label == "One Shot handoff frame" and calls["n"] == 0:
            calls["n"] += 1
            raise p.JobCancelled("Stopped during One Shot handoff frame.")
        return real(cmd, label, job) if pgid_key == "mux_pgid" else real(cmd, label, job, pgid_key)

    monkeypatch.setattr(p, "_take_ffmpeg", stop_during_frame)
    job = _take_job()
    with pytest.raises(p.JobCancelled):
        p.run_take_job_inner(job)
    part1 = take_env["made"][0]
    man = _manifest_of(part1)
    assert man["done_parts"] == 1
    assert man["last_png"] is None or Path(man["last_png"]).is_file()
    h = _H({"path": [str(part1)]})
    routes_queue.post_take_resume(h, "/take/resume", {}, "")
    assert h.status == 200
    resumed = p.STATE["queue"][-1]
    seen = []
    orig_render = p.run_h3_job_inner

    def check_anchor(child):
        seen.append(child["params"].get("image"))
        assert Path(child["params"]["image"]).is_file(), "part 2 started from a missing frame"
        orig_render(child)

    monkeypatch.setattr(p, "run_h3_job_inner", check_anchor)
    p.run_take_job_inner(resumed)
    assert len(seen) == 2 and resumed.get("output_path")


def test_safety_12_resumed_parts_keep_their_params_seed_used(take_env):
    take_env["fail_at"] = 3
    job = _take_job()
    with pytest.raises(RuntimeError):
        p.run_take_job_inner(job)
    last = take_env["made"][-1]
    man = _manifest_of(last)
    # an older manifest with no part_seeds: seeds come from params.seed_used
    man.pop("part_seeds", None)
    side = json.loads(Path(str(last) + ".json").read_text())
    side["one_shot_unfinished"] = man
    Path(str(last) + ".json").write_text(json.dumps(side))
    h = _H({"path": [str(last)]})
    routes_queue.post_take_resume(h, "/take/resume", {}, "")
    assert h.status == 200
    take_env["fail_at"] = None
    resumed = p.STATE["queue"][-1]
    p.run_take_job_inner(resumed)
    final = Path(resumed["output_path"])
    meta = json.loads(Path(str(final) + ".json").read_text())["take"]["parts_meta"]
    assert meta == [{"seed_used": 701}, {"seed_used": 702}, {"seed_used": 703}]


def test_safety_11_new_take_after_join_keeps_every_beat(take_env):
    take_env["fail_at"] = 2
    job = _take_job()
    with pytest.raises(RuntimeError):
        p.run_take_job_inner(job)
    part1 = take_env["made"][0]
    written = list(job["params"]["take"]["beat_prompts"])
    assert len(written) == 9 and all(w.startswith(f"beat {i}") for i, w in enumerate(written))
    h = _H({"path": [str(part1)]})
    routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
    assert h.status == 200
    joined = h.payload["output"]
    jt = json.loads(Path(joined + ".json").read_text())["take"]
    assert jt["beats"] == written
    assert jt["parts_meta"] == [{"seed_used": 701}]
    h = _H({"path": [joined]})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 200, h.payload
    assert p.STATE["queue"][-1]["params"]["take"]["beat_prompts"] == written


def test_safety_11_new_take_reads_an_old_input_shaped_partial_sidecar(take_env, tmp_path):
    clip = p.OUTPUT / "old_partial.mp4"
    _clip(clip)
    beats = [f"old beat {i}" for i in range(6)]
    take = {"seconds": 30, "beats": 6, "beat_prompts": beats, "engine": "h3", "parts": [str(clip)]}
    (p.OUTPUT / "old_partial.mp4.json").write_text(json.dumps(
        {"take": take, "params": {"prompt": "x", "engine": "h3", "mode": "t2v", "take": take}}))
    h = _H({"path": [str(clip)]})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 200, h.payload
    assert p.STATE["queue"][-1]["params"]["take"]["beat_prompts"] == beats


def test_safety_7_a_recovery_join_never_takes_the_queues_mux_slot(take_env, monkeypatch):
    take_env["fail_at"] = 2
    job = _take_job()
    with pytest.raises(RuntimeError):
        p.run_take_job_inner(job)
    part1 = take_env["made"][0]
    seen_keys = []
    real = p.run_tracked_subprocess

    def spy(cmd, *, pgid_key, label, job=None, **kw):
        seen_keys.append(pgid_key)
        with p.LOCK:
            during = p.STATE.get("mux_pgid")
        assert during == 111, "the join overwrote the queue's mux registration"
        return real(cmd, pgid_key=pgid_key, label=label, job=job, **kw)

    with p.LOCK:
        p.STATE["mux_pgid"] = 111              # a queued render's encode is live
    try:
        monkeypatch.setattr(p, "run_tracked_subprocess", spy)
        h = _H({"path": [str(part1)]})
        routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
        assert h.status == 200, h.payload
        with p.LOCK:
            assert p.STATE.get("mux_pgid") == 111        # still registered, still stoppable
        assert seen_keys and all(k == "take_join_pgid" for k in seen_keys)
        # and only one join owns that slot at a time
        assert routes_queue._TAKE_JOIN_LOCK.acquire(blocking=False)
        try:
            h = _H({"path": [str(part1)]})
            routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
            assert h.status in (404, 409)
        finally:
            routes_queue._TAKE_JOIN_LOCK.release()
    finally:
        with p.LOCK:
            p.STATE["mux_pgid"] = None


# ---- SAFETY-13: a restored draft brings back its mode's source inputs ------
_DRAFT_SHIM = r"""
globalThis._els = {};
globalThis._mk = (id, v) => (_els[id] = { id, value: v === undefined ? '' : String(v) });
globalThis.document = { getElementById: (id) => _els[id] || null };
['mode', 'i2vMode', 'quality', 'prompt', 'negative_prompt', 'seed', 'width', 'height', 'frames',
 'duration', 'image', 'audio', 'video_path', 'start_image', 'end_image', 'extendSrcSelect']
  .forEach(id => _mk(id, ''));
globalThis.store = {};
globalThis.localStorage = { getItem: k => store[k] || null, setItem: (k, v) => { store[k] = v; } };
globalThis.DRAFT_LS_KEY = 'draft';
globalThis.activePath = null;
globalThis.calls = [];
globalThis.toasts = [];
globalThis.phosToast = (m, o) => toasts.push([m, (o || {}).kind || '']);
globalThis.setMode = (m) => { calls.push(['setMode', m]); _els.mode.value = m; };
globalThis.setQuality = () => {};
globalThis.pickerSetImage = (k, v) => { calls.push(['pickerSetImage', k, v]); _els[k].value = v; };
globalThis.i2vAudioSet = (v) => { calls.push(['i2vAudioSet', v]); _els.audio.value = v; };
globalThis.MISSING = new Set();
globalThis.fetch = async (url) => { const path = decodeURIComponent(url.split('path=')[1]);
  calls.push(['fetch', url.split('?')[0]]);
  return { json: async () => ({ exists: !MISSING.has(path) }) }; };
"""


def _draft_run(form: dict, missing=()):
    src = (JS / "queue.js").read_text(encoding="utf-8")
    fns = "\n".join(extract_function(n, src) for n in
                    ("_draftSnapshot", "saveDraftNow", "_draftDropIfMissing", "restoreDraftOnBoot"))
    return _node(_DRAFT_SHIM + fns + """
const form = %s;
for (const [k, v] of Object.entries(form)) _els[k].value = v;
saveDraftNow();
Object.keys(_els).forEach(k => _els[k].value = '');     // a reload: a blank form
MISSING = new Set(%s);
restoreDraftOnBoot();
setTimeout(() => console.log(JSON.stringify({ calls, toasts,
  vals: Object.fromEntries(Object.entries(_els).map(([k, e]) => [k, e.value])) })), 20);
""" % (json.dumps(form), json.dumps(list(missing))))


def test_safety_13_image_keyframe_and_extend_drafts_keep_their_inputs():
    out = _draft_run({"mode": "i2v_clean_audio", "i2vMode": "i2v_clean_audio", "prompt": "a",
                      "image": "/u/a.png", "audio": "/u/song.wav"})
    assert ["pickerSetImage", "image", "/u/a.png"] in out["calls"]
    assert out["vals"]["audio"] == "/u/song.wav" and out["vals"]["mode"] == "i2v_clean_audio"
    out = _draft_run({"mode": "keyframe", "prompt": "b", "start_image": "/u/s.png",
                      "end_image": "/u/e.png"})
    assert ["pickerSetImage", "start_image", "/u/s.png"] in out["calls"]
    assert ["pickerSetImage", "end_image", "/u/e.png"] in out["calls"]
    out = _draft_run({"mode": "extend", "prompt": "c", "video_path": "/o/clip.mp4"})
    assert out["vals"]["video_path"] == "/o/clip.mp4" and out["vals"]["extendSrcSelect"] == "/o/clip.mp4"


def test_safety_13_a_source_deleted_since_is_cleared_out_loud():
    out = _draft_run({"mode": "extend", "prompt": "c", "video_path": "/o/gone.mp4"},
                     missing=["/o/gone.mp4"])
    assert out["vals"]["video_path"] == ""
    assert any("gone.mp4 from your draft is no longer on disk" in t[0] and t[1] == "warning"
               for t in out["toasts"])


def test_safety_13_file_exists_route_is_bound_to_outputs_and_uploads(tmp_path, monkeypatch):
    from urllib.parse import urlparse, quote
    routes_files = sys.modules["panel.routes_files"]
    out_dir = tmp_path / "o"; out_dir.mkdir()
    up = tmp_path / "u"; up.mkdir()
    (up / "song.wav").write_bytes(b"x")
    (tmp_path / "secret.txt").write_bytes(b"x")
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "UPLOADS", up)
    got = []
    for f in (up / "song.wav", out_dir / "nope.mp4", tmp_path / "secret.txt"):
        h = _H()
        routes_files.get_file_exists(h, urlparse("/file/exists?path=" + quote(str(f))))
        got.append(h.payload["exists"])
    assert got == [True, False, False]
