"""Safety package (2026-09-29 mega review) — VC-03, VC-04, VC-05, VC-06,
VC-35, VC-38, H3-11, VA-08, VA-09, VA-10, VA-17.

Theme: no one-click destruction, and "another take" works. Covers the new
backend routes (/queue/newtake, /queue/restore, /stop/after_part,
/take/resume, /take/join_partial) and run_take_job_inner's partial-take
recovery. The frontend half (workflowSwitch tab fixes, the seed-lock chip,
the draft autosave, the destructive-action dialogs) is covered by
lint_webapp.mjs (every cross-module reference resolves) plus the existing
JS-extraction suites this package updated in place (test_face_fix.py,
test_review_416_ltx.py, test_outputs_cache_invalidation.py) rather than
duplicated here.
"""
from __future__ import annotations

import io
import json
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as p                                             # noqa: E402

routes_queue = sys.modules["panel.routes_queue"]


class _H:
    """A fake HTTP handler good enough for both calling conventions the new
    routes use: form-encoded (_read_form_body) and raw JSON body (headers +
    rfile), matching the shape routes_oneshot.py's _read_document expects."""

    def __init__(self, form=None, json_body=None, qs=None):
        self._form = form or {}
        self.status = None
        self.payload = None
        self.qs = qs or {}
        if json_body is not None:
            raw = json.dumps(json_body).encode()
            self.headers = {"Content-Length": str(len(raw))}
            self.rfile = io.BytesIO(raw)

    def _read_form_body(self):
        return ("", {k: [v] for k, v in self._form.items()})

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


def _tiny_clip(path: Path) -> None:
    import subprocess
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=32x32:d=0.2",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "0.2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )


@pytest.fixture(autouse=True)
def _sandbox(tmp_path, monkeypatch):
    out = tmp_path / "out"; out.mkdir()
    monkeypatch.setattr(p, "OUTPUT", out)
    monkeypatch.setattr(p, "UPLOADS", tmp_path / "up")
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    with p.QUEUE_COND:
        p.STATE["queue"] = []
        p.STATE["current"] = None
        p.STATE["history"] = []
    monkeypatch.setattr(p, "persist_queue", lambda: None)
    yield out


# ---------------------------------------------------------------------------
# VC-03 — New Take: same recipe, fresh seed, one click, sourced from disk.
# ---------------------------------------------------------------------------

def _write_output_with_sidecar(out_dir: Path, name: str, params: dict) -> Path:
    clip = out_dir / name
    _tiny_clip(clip)
    (clip.with_suffix(clip.suffix + ".json")).write_text(json.dumps({"params": params}))
    return clip


def test_newtake_replays_the_recipe_with_a_fresh_seed(_sandbox):
    out_dir = _sandbox
    clip = _write_output_with_sidecar(out_dir, "a.mp4", {
        "mode": "t2v", "prompt": "a hen skates", "seed": "42", "seed_used": 42,
        "width": 1024, "height": 576, "quality": "balanced",
        "loras": [{"path": "/l/x.safetensors", "strength": 0.8}],
    })
    h = _H(form={"path": str(clip)})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 200 and h.payload["ok"] is True
    assert len(p.STATE["queue"]) == 1
    job = p.STATE["queue"][0]
    assert job["params"]["seed"] == "-1"                  # the ONLY thing that changed
    assert "seed_used" not in job["params"]
    assert job["params"]["prompt"] == "a hen skates"
    assert job["params"]["width"] == 1024 and job["params"]["quality"] == "balanced"
    assert job["params"]["loras"][0]["path"] == "/l/x.safetensors"
    assert job["params"]["source"] == "new_take"
    assert job["id"] != ""


def test_newtake_reruns_a_whole_one_shot_with_a_fresh_seed(_sandbox):
    # Coordinator review, item 2: New Take on a One Shot re-runs the whole
    # take with a new seed rather than refusing outright. The sidecar's
    # `take.parts` (rendered file paths, post-completion) and `take.beats`
    # (the resolved text list) are NOT the beat-index/part-count shape
    # run_take_job_inner needs to start fresh — that's rebuilt from
    # take_plan(), which is exactly what a first-time One Shot submit does.
    clip = _write_output_with_sidecar(_sandbox, "take.mp4", {
        "mode": "i2v", "prompt": "a hen skates", "seed": "42", "seed_used": 42,
        "engine": "h3",
        "take": {
            "seconds": 30, "beats": ["b0", "b1", "b2", "b3", "b4", "b5"],
            "parts": ["/out/take.mp4"],   # post-completion: file paths, not index groups
            "engine": "h3", "beats_per_part": 3, "light_lock": "warm evening light",
            "retake": False, "camera": "slow dolly in", "handoff": "speech",
        },
    })
    h = _H(form={"path": str(clip)})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 200 and h.payload["ok"] is True
    assert len(p.STATE["queue"]) == 1
    job = p.STATE["queue"][0]["params"]
    assert job["seed"] == "-1"
    assert "seed_used" not in job
    new_take = job["take"]
    # A fresh, correctly-shaped plan for 30s/h3 (10 beats... 30/5=6 beats,
    # 2 parts of 3) — NOT the stored file-path list.
    assert new_take["parts"] == [[0, 1, 2], [3, 4, 5]]
    assert new_take["beats"] == 6                       # take_plan's own count, not the text list
    assert new_take["beat_prompts"] == ["b0", "b1", "b2", "b3", "b4", "b5"]
    assert new_take["light_lock"] == "warm evening light"
    assert new_take["retake"] is False
    assert new_take["camera"] == "slow dolly in"
    assert new_take["handoff"] == "speech"
    assert "_resume" not in new_take


def test_newtake_one_shot_with_an_invalid_stored_length_is_refused(_sandbox):
    clip = _write_output_with_sidecar(_sandbox, "take.mp4", {
        "mode": "t2v", "prompt": "x",
        "take": {"seconds": 999, "beats": ["a"], "engine": "ltx"},
    })
    h = _H(form={"path": str(clip)})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 400
    assert p.STATE["queue"] == []


def test_newtake_404s_outside_outputs_and_uploads(_sandbox, tmp_path):
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    (tmp_path / "elsewhere.mp4.json").write_text(json.dumps({"params": {"prompt": "x"}}))
    h = _H(form={"path": str(outside)})
    routes_queue.post_queue_newtake(h, "/queue/newtake", {}, "")
    assert h.status == 404


# ---------------------------------------------------------------------------
# VC-06 — Clear queue's Undo path: /queue/restore replays exactly what
# /queue/clear emptied, in order, with fresh ids.
# ---------------------------------------------------------------------------

def test_queue_restore_requeues_every_job_in_order(_sandbox):
    jobs = [{"params": {"prompt": f"p{i}", "mode": "t2v"}} for i in range(3)]
    h = _H(json_body={"jobs": jobs})
    routes_queue.post_queue_restore(h, "/queue/restore", {}, "application/json")
    assert h.status == 200 and h.payload["ok"] is True
    assert h.payload["restored"] == 3
    assert [j["params"]["prompt"] for j in p.STATE["queue"]] == ["p0", "p1", "p2"]
    ids = [j["id"] for j in p.STATE["queue"]]
    assert len(set(ids)) == 3                              # fresh ids, no collisions


def test_queue_restore_rejects_a_non_json_body(_sandbox):
    h = _H()
    h.headers = {"Content-Length": "9"}
    h.rfile = io.BytesIO(b"not-json!")
    routes_queue.post_queue_restore(h, "/queue/restore", {}, "application/json")
    assert h.status == 400


def test_queue_restore_rejects_an_empty_jobs_list(_sandbox):
    h = _H(json_body={"jobs": []})
    routes_queue.post_queue_restore(h, "/queue/restore", {}, "application/json")
    assert h.status == 400


# ---------------------------------------------------------------------------
# H3-11 — "Stop after this part": only meaningful mid-take, sets a flag the
# take loop honours at the next part boundary (see run_take_job_inner tests
# below for the loop side of this).
# ---------------------------------------------------------------------------

def test_stop_after_part_refuses_when_nothing_is_a_take(_sandbox):
    with p.LOCK:
        p.STATE["current"] = {"id": "j1", "params": {"mode": "t2v"}}
    h = _H()
    routes_queue.post_stop_after_part(h, "/stop/after_part", {}, "")
    assert h.status == 409


def test_stop_after_part_refuses_when_idle(_sandbox):
    with p.LOCK:
        p.STATE["current"] = None
    h = _H()
    routes_queue.post_stop_after_part(h, "/stop/after_part", {}, "")
    assert h.status == 404


def test_stop_after_part_arms_the_flag_on_a_take(_sandbox):
    job = {"id": "j1", "params": {"take": {"seconds": 30}}}
    with p.LOCK:
        p.STATE["current"] = job
    h = _H()
    routes_queue.post_stop_after_part(h, "/stop/after_part", {}, "")
    assert h.status == 200 and job["stop_after_part"] is True


# ---------------------------------------------------------------------------
# VA-17 — a stopped/failed One Shot keeps its finished parts, with Resume
# and Join what's done. run_take_job_inner's own recovery path, end to end.
# ---------------------------------------------------------------------------

def test_take_stop_keeps_parts_unhidden_with_a_resume_manifest(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"; out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    hidden_calls = []
    monkeypatch.setattr(p, "set_hidden", lambda path, flag: hidden_calls.append((path, flag)))

    made = []

    def fake_h3(child):
        out = out_dir / f"{child['id']}.mp4"
        _tiny_clip(out)
        child["output_path"] = str(out)
        made.append(out)
        if len(made) == 1:
            job["cancel_requested"] = True             # Stop lands right after part 1 finishes

    monkeypatch.setattr(p, "run_h3_job_inner", fake_h3)
    monkeypatch.setattr(p, "take_drift", lambda *a, **k: {"ok": True, "delta": 0.0, "drifted": False})
    job = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "a hen skates",
                      "take_seconds": "45",
                      "beats": json.dumps([f"b{i}" for i in range(9)])})
    with pytest.raises(RuntimeError, match="(?i)stopped"):
        p.run_take_job_inner(job)

    # Part 1 was hidden as a normal working file, then UN-hidden again by the
    # recovery path once the take stopped — net effect: visible.
    part1 = made[0]
    unhide_calls = [c for c in hidden_calls if c[0] == str(part1)]
    assert unhide_calls[-1] == (str(part1), False)

    side = json.loads((part1.with_suffix(part1.suffix + ".json")).read_text())
    assert side["label"].endswith("(unfinished)")
    manifest = side.get("one_shot_unfinished")
    assert manifest is not None
    assert manifest["done_parts"] == 1
    assert manifest["total_parts"] == 3          # 9 beats / 3 per part on H3
    assert manifest["outs"] == [str(part1)]
    assert manifest["job_id"] == job["id"]


def test_take_resume_route_continues_from_the_manifest(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"; out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(p, "UPLOADS", tmp_path / "up")
    monkeypatch.setattr(p, "set_hidden", lambda *a, **k: None)
    monkeypatch.setattr(p, "persist_queue", lambda: None)
    with p.QUEUE_COND:
        p.STATE["queue"] = []

    part1 = out_dir / "part1.mp4"
    _tiny_clip(part1)
    manifest = {
        "job_id": "j-old", "done_parts": 1, "total_parts": 3,
        "outs": [str(part1)], "last_png": None,
        "take": {"seconds": 45, "beats": ["b0", "b1", "b2", "b3", "b4", "b5", "b6", "b7", "b8"],
                 "parts": [[0, 1, 2], [3, 4, 5], [6, 7, 8]], "engine": "h3",
                 "beats_per_part": 3, "light_lock": "", "retake": True,
                 "camera": "", "handoff": "last"},
        "label": "ride", "engine": "h3",
        "p": {"mode": "t2v", "engine": "h3", "prompt": "a hen skates", "label": "ride"},
    }
    part1.with_suffix(part1.suffix + ".json").write_text(
        json.dumps({"label": "ride · part 1 of 3 (unfinished)", "one_shot_unfinished": manifest}))

    h = _H(form={"path": str(part1)})
    routes_queue.post_take_resume(h, "/take/resume", {}, "")
    assert h.status == 200 and h.payload["ok"] is True
    assert h.payload["resumed_from_part"] == 1
    assert len(p.STATE["queue"]) == 1
    resumed = p.STATE["queue"][0]["params"]
    assert resumed["take"]["_resume"]["outs"] == [str(part1)]
    assert resumed["take"]["_resume"]["start_k"] == 1
    assert resumed["prompt"] == "a hen skates"


def test_take_join_partial_publishes_what_finished(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"; out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "UPLOADS", tmp_path / "up")
    hidden_calls = []
    monkeypatch.setattr(p, "set_hidden", lambda path, flag: hidden_calls.append((path, flag)))

    part1 = out_dir / "part1.mp4"
    _tiny_clip(part1)
    manifest = {
        "job_id": "j-old", "done_parts": 1, "total_parts": 3,
        "outs": [str(part1)], "last_png": None,
        "take": {"seconds": 45}, "label": "ride", "engine": "h3",
        "p": {"mode": "t2v", "engine": "h3", "prompt": "a hen skates", "label": "ride"},
    }
    part1.with_suffix(part1.suffix + ".json").write_text(
        json.dumps({"label": "ride · part 1 of 3 (unfinished)", "one_shot_unfinished": manifest}))

    h = _H(form={"path": str(part1)})
    routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
    assert h.status == 200 and h.payload["ok"] is True
    assert h.payload["parts_joined"] == 1
    final = Path(h.payload["output"])
    assert final.is_file()
    side = json.loads(final.with_suffix(final.suffix + ".json").read_text())
    assert side["take"]["stopped_early"] is True
    assert side["take"]["joined_partial"] is True
    assert "(stopped early)" in side["label"]
    # The raw part is a working file again now that it's been published.
    assert (str(part1), True) in hidden_calls


def test_take_join_partial_404s_without_a_manifest(tmp_path, monkeypatch):
    out_dir = tmp_path / "out"; out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "UPLOADS", tmp_path / "up")
    clip = out_dir / "ordinary.mp4"
    _tiny_clip(clip)
    clip.with_suffix(clip.suffix + ".json").write_text(json.dumps({"label": "ordinary clip"}))
    h = _H(form={"path": str(clip)})
    routes_queue.post_take_join_partial(h, "/take/join_partial", {}, "")
    assert h.status == 404


def test_take_stop_after_part_finishes_cleanly_with_fewer_parts(tmp_path, monkeypatch):
    """The OTHER half of H3-11: 'stop after this part' is not a failure —
    the take joins and publishes normally, just shorter than asked."""
    out_dir = tmp_path / "out"; out_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(p, "OUTPUT", out_dir)
    monkeypatch.setattr(p, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(p, "set_hidden", lambda *a, **k: None)
    made = []

    def fake_h3(child):
        out = out_dir / f"{child['id']}.mp4"
        _tiny_clip(out)
        child["output_path"] = str(out)
        made.append(out)
        if len(made) == 1:
            job["stop_after_part"] = True              # asked to stop after part 1

    monkeypatch.setattr(p, "run_h3_job_inner", fake_h3)
    monkeypatch.setattr(p, "take_drift", lambda *a, **k: {"ok": True, "delta": 0.0, "drifted": False})
    job = p.make_job({"mode": "t2v", "engine": "h3", "prompt": "a hen skates",
                      "take_seconds": "45",
                      "beats": json.dumps([f"b{i}" for i in range(9)])})
    p.run_take_job_inner(job)                            # must NOT raise
    assert len(made) == 1
    final = Path(job["output_path"])
    assert final.is_file()
    side = json.loads(final.with_suffix(final.suffix + ".json").read_text())
    assert side["take"]["stopped_early"] is True
    assert len(side["take"]["parts"]) == 1


# ---------------------------------------------------------------------------
# H3-11 (full scope): a plain chained H3 render — no One Shot wrapper, just a
# 10/15 s tier that renders as N windows in ONE subprocess call. The coordinator
# review flagged that the first pass only covered One Shot; this covers the
# other shape it explicitly named: Stop during window 3 must not throw away
# windows 1-2. Runs the REAL run_h3_job_inner against a fake "runner" — a
# small script standing in for the vendored H3 CLI (not installed on this
# machine) — that prints the same window/phase/step log lines the real one
# does and writes a real tiny mp4 to `-o` after "finishing" window 1, then
# hangs. /stop/after_part's flag should make the panel SIGTERM it right when
# window 2 starts, and the file window 1 already wrote should get published.
import subprocess as _subprocess                                     # noqa: E402
import tempfile                                                       # noqa: E402
import textwrap                                                       # noqa: E402
import time                                                            # noqa: E402
import unittest.mock                                                   # noqa: E402
from contextlib import ExitStack                                       # noqa: E402

_H3_TMP = Path(tempfile.mkdtemp(prefix="phos-h3-window-stop-"))


def _write_fake_h3_runner() -> Path:
    """A stand-in for the vendored H3 CLI: prints the exact log shape
    run_h3_job_inner parses (### window N/M ###, == phase ==, step X/Y:),
    writes a real tiny mp4 to `-o` once "window 1" is done, then announces
    window 2 and sleeps — long enough that the panel's SIGTERM (sent the
    instant it sees the window-2 line, per the stop_after_part flag) always
    lands before this script would finish on its own."""
    script = _H3_TMP / "fake_h3_runner.py"
    script.write_text(textwrap.dedent("""
        import subprocess, sys, time
        args = sys.argv[1:]
        out_path = args[args.index('-o') + 1]
        print("== text_encode_q8 ==", flush=True)
        print("### window 1/2 ###", flush=True)
        print("== w1_joint_denoise ==", flush=True)
        for s in (1, 2):
            print(f"step {s}/2:", flush=True)
            time.sleep(0.03)
        print("== w1_encode_mux ==", flush=True)
        # Real H3 output is well over the panel's 50 KB partial-clip floor
        # (_h3_partial_clip_info) even for one short window; a flat `color`
        # source (what the other fake-clip helpers in this repo use for cheap
        # presence checks) h264-compresses to a few KB regardless of
        # resolution/duration, so this needs a source with real per-frame
        # detail (testsrc's moving pattern) to size like a real clip does.
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                         "-i", "testsrc=size=640x384:duration=1.5:rate=24", "-f", "lavfi",
                         "-i", "anullsrc=r=8000:cl=mono", "-t", "1.5",
                         "-c:v", "libx264", "-crf", "0", "-pix_fmt", "yuv420p",
                         "-c:a", "aac", "-shortest", out_path], check=True)
        print("### window 2/2 ###", flush=True)
        time.sleep(30)
        print("== w2_joint_denoise ==", flush=True)
    """), encoding="utf-8")
    return script


def _h3_chain_dispatch_patches(stack, popen):
    """Same shape as test_h3_stop_and_companion.py's _h3_dispatch_patches,
    but h3_supports_chain() answers True (a chained tier needs it) and
    H3_ROOT is a real directory (the real renderer Popen call uses it as
    cwd, and none of the existing H3 tests reach that far)."""
    paths = dict(missing=[], repairable=False, dit=_H3_TMP / "dit",
                 python=sys.executable, runner=_write_fake_h3_runner(),
                 compact_root=_H3_TMP / "compact", text_config=_H3_TMP / "text")
    for name, val in {
            "h3_paths": lambda: paths, "h3_capable": lambda: True,
            "h3_supports_lora": lambda: True, "h3_dit_choice": lambda: ("bf16", None),
            "output_codec_settings": lambda: {"crf": "18", "pix_fmt": "yuv420p"},
            "_h3_runner_has_flag": lambda flag: flag == "--chain-windows",
            "h3_supports_lora_adaln": lambda: True}.items():
        stack.enter_context(unittest.mock.patch.object(p, name, val))
    for name in ("h3_supports_memory_limits", "h3_supports_prompt_cache",
                 "h3_live_preview_ready", "h3_supports_stage_a", "h3_supports_tae_draft"):
        stack.enter_context(unittest.mock.patch.object(p, name, lambda: False))
    stack.enter_context(unittest.mock.patch.object(p.HELPER, "is_alive", lambda: False))
    stack.enter_context(unittest.mock.patch.object(p.subprocess, "Popen", popen))
    stack.enter_context(unittest.mock.patch.object(p, "H3_ROOT", _H3_TMP))


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg not on PATH")
def test_h3_chain_stop_after_window_publishes_finished_windows():
    job = {"id": "t-h3chain", "params": {"engine": "h3", "mode": "t2v",
                                         "h3_tier": "draft_10s", "prompt": "a lighthouse at dusk",
                                         "seed": "42", "loras": []},
           "stop_after_part": True}
    real_popen = _subprocess.Popen
    spawned = []

    def popen(cmd, **kw):
        # subprocess.run() (ffprobe, the fake runner's own ffmpeg call) is
        # ITSELF implemented on top of Popen, and patching P.subprocess.Popen
        # patches the one process-wide `subprocess` module — so this sees
        # every Popen in the process, not just the H3 renderer's. Only the
        # renderer (<keep-awake prefix> <python> <fake runner> <argv...>; the
        # runner IS the fake one, via h3_paths) is tracked; everything else
        # passes straight through.
        if not (isinstance(cmd, list) and str(_H3_TMP / "fake_h3_runner.py") in map(str, cmd)):
            return real_popen(cmd, **kw)
        p_ = real_popen(cmd, **kw)
        spawned.append(p_)
        return p_

    with ExitStack() as st:
        _h3_chain_dispatch_patches(st, popen)
        with p.LOCK:
            saved = (p.STATE.get("current"), p.STATE.get("h3_pgid"))
            p.STATE["current"] = job
        try:
            t0 = time.time()
            p.run_h3_job_inner(job)                     # must NOT raise
            elapsed = time.time() - t0
        finally:
            with p.LOCK:
                p.STATE["current"], p.STATE["h3_pgid"] = saved

    # Killed at the window-2 boundary, not left to run its 30 s sleep.
    assert elapsed < 15, f"stop-after-window did not cut the render short ({elapsed}s)"
    assert len(spawned) == 1
    spawned[0].wait(timeout=5)

    assert job.get("_h3_stopped_after_window") is not None
    out_path = Path(job["output_path"])
    assert out_path.is_file()
    side = json.loads(out_path.with_suffix(out_path.suffix + ".json").read_text())
    assert side.get("stopped_early") is True
    assert side["h3"]["delivered_frames"] == job["_h3_stopped_after_window"]["frames"]
    # The published clip is window 1's real duration, NOT the full 10 s the
    # tier asked for — the exact bug this fix closes.
    assert side["video_duration_sec"] < 2.0
    assert "(stopped early" in side["params"]["label"]


if __name__ == "__main__":
    unittest.main()
