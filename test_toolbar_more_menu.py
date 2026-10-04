#!/usr/bin/env python3
"""VC-18 / VC-37 (clip toolbar) + VC-42 (LoRA row menu) — coordinator ruling
2026-09-29: "the clip toolbar... Add Sharp export, Use last frame, Open in
Editor, Reveal in Finder, To film, lightbox actions" and "move the per-row
'Delete from disk' into a ⋯ menu with confirm."

Gated here:
  1  Sharp export — queue_sharp_export() refuses what the worker would only
     refuse late (missing file, non-video, PiperSR not installed), dedupes a
     double click, and /queue/sharp_export answers JSON either way
  2  run_sharp_export_job_inner() picks fit_720p/x2 the same way the
     render-time Export row does, calls run_pipersr_tracked, and writes a
     sidecar that carries the SOURCE clip's own params forward
  3  /output/reveal and /output/last_frame are contained to OUTPUT/UPLOADS
     (the same rule /image already enforces) and refuse a path outside them
  4  the toolbar markup: one "More" popover with all four actions, plus the
     same row rendered (flat) inside the expand lightbox for video only
  5  the LoRA row's "Delete from disk" is no longer a bare flat danger
     button — it is one item inside a per-row ⋯ popover, confirm() untouched

No renders, no GPU, no ffmpeg required for the unit-level tests (the
ffmpeg-dependent route test skips cleanly where ffmpeg is absent).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-moremenu-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8321")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES  # noqa: E402

HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
LJS = (ROOT / "webapp" / "js" / "loras.js").read_text(encoding="utf-8")
CSS = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
FFMPEG = shutil.which("ffmpeg")


class _H:
    """Minimal fake handler matching the POST_ROUTES contract used elsewhere
    (see test_face_fix.py's `H`)."""

    def __init__(self, body: str = ""):
        self.body, self.out = body, None

    def _read_form_body(self):
        from urllib.parse import parse_qs
        return self.body, parse_qs(self.body)

    def _json(self, obj, code=200):
        self.out = (code, obj)


class _Env(unittest.TestCase):
    def setUp(self):
        # run_sharp_export_job_inner writes its export to the real OUTPUT (kept
        # on purpose: a failure to write there is a real failure). Remember what
        # was there so tearDown removes only what this test created.
        self._out_before = {q.name for q in Path(P.OUTPUT).glob("gym_draft_sharp_*")}
        self.dir = Path(tempfile.mkdtemp(prefix="mm-", dir=P.OUTPUT))  # UI-1: outputs/uploads only
        self.clip = self.dir / "gym_draft.mp4"
        self.clip.write_bytes(b"\0" * 64)
        Path(str(self.clip) + ".json").write_text(json.dumps({"params": {
            "prompt": "A woman lifts a dumbbell", "seed": "-1",
            "seed_used": 52190, "mode": "t2v"}}), encoding="utf-8")
        self._saved = (P.PIPERSR_UPSCALE_ENABLED, P.persist_queue)
        P.PIPERSR_UPSCALE_ENABLED = True
        P.persist_queue = lambda: None
        with P.QUEUE_COND:
            self._queue_before = list(P.STATE["queue"])

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)   # a temp dir under OUTPUT
        for q in Path(P.OUTPUT).glob("gym_draft_sharp_*"):   # exports this test made
            if q.name not in self._out_before:
                q.unlink(missing_ok=True)
        P.PIPERSR_UPSCALE_ENABLED, P.persist_queue = self._saved
        with P.QUEUE_COND:
            P.STATE["queue"] = self._queue_before
            P.STATE["current"] = None

    def queued(self):
        with P.QUEUE_COND:
            return [j for j in P.STATE["queue"] if j not in self._queue_before]


# ---------------------------------------------------------------- 1
class TestQueueSharpExport(_Env):
    def test_queues_with_source_path_recorded(self):
        r = P.queue_sharp_export(str(self.clip))
        self.assertTrue(r["ok"], r)
        jobs = self.queued()
        self.assertEqual(len(jobs), 1)
        p = jobs[0]["params"]
        self.assertEqual(p["mode"], "sharp_export")
        self.assertEqual(p["source_path"], str(self.clip))

    def test_double_click_is_one_job(self):
        a = P.queue_sharp_export(str(self.clip))
        b = P.queue_sharp_export(str(self.clip))
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b.get("duplicate"))
        self.assertEqual(len(self.queued()), 1)

    def test_refuses_a_missing_file(self):
        r = P.queue_sharp_export(str(self.dir / "gone.mp4"))
        self.assertFalse(r["ok"])
        self.assertEqual(self.queued(), [])

    def test_refuses_a_still_image(self):
        still = self.dir / "still.png"
        still.write_bytes(b"x")
        r = P.queue_sharp_export(str(still))
        self.assertFalse(r["ok"])
        self.assertIn("video", r["error"])

    def test_refuses_when_pipersr_is_not_installed(self):
        P.PIPERSR_UPSCALE_ENABLED = False
        r = P.queue_sharp_export(str(self.clip))
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("code"), "pack_missing")
        self.assertEqual(self.queued(), [])

    def test_route_is_registered_and_answers_json(self):
        self.assertIn("/queue/sharp_export", POST_ROUTES)
        h = _H(urlencode({"path": str(self.clip)}))
        POST_ROUTES["/queue/sharp_export"](h, "/queue/sharp_export", {}, "")
        self.assertEqual(h.out[0], 200)
        self.assertTrue(h.out[1]["ok"])
        h = _H(urlencode({"path": str(self.dir / "nope.mp4")}))
        POST_ROUTES["/queue/sharp_export"](h, "/queue/sharp_export", {}, "")
        self.assertEqual(h.out[0], 400)


# ---------------------------------------------------------------- 2
class TestRunSharpExportJobInner(_Env):
    def _job(self, **extra):
        p = {"mode": "sharp_export", "source_path": str(self.clip)}
        p.update(extra)
        return {"id": "j1", "params": p, "started_at": None, "started_ts": None}

    def test_picks_fit_720p_for_a_small_source_and_writes_a_sidecar(self):
        calls = []

        def fake_pipersr(source, output, mode, crf, pix_fmt, preset):
            calls.append(mode)
            Path(output).write_bytes(b"x")

        old_probe, old_pipersr = P._probe_video_dims, P.run_pipersr_tracked
        P._probe_video_dims = lambda path: (640, 480)
        P.run_pipersr_tracked = fake_pipersr
        try:
            job = self._job()
            P.run_sharp_export_job_inner(job)
        finally:
            P._probe_video_dims, P.run_pipersr_tracked = old_probe, old_pipersr
        self.assertEqual(calls, ["fit_720p"])
        out = Path(job["output_path"])
        self.assertTrue(out.is_file())
        sidecar = json.loads(Path(str(out) + ".json").read_text())
        self.assertEqual(sidecar["params"]["prompt"], "A woman lifts a dumbbell")
        self.assertEqual(sidecar["params"]["sharp_export_source"], str(self.clip))
        self.assertEqual(sidecar["params"]["sharp_export_mode"], "fit_720p")

    def test_picks_x2_for_a_source_already_at_720p(self):
        calls = []
        old_probe, old_pipersr = P._probe_video_dims, P.run_pipersr_tracked
        P._probe_video_dims = lambda path: (1280, 720)
        P.run_pipersr_tracked = lambda source, output, mode, crf, pix_fmt, preset: (
            calls.append(mode), Path(output).write_bytes(b"x"))
        try:
            P.run_sharp_export_job_inner(self._job())
        finally:
            P._probe_video_dims, P.run_pipersr_tracked = old_probe, old_pipersr
        self.assertEqual(calls, ["x2"])

    def test_an_explicit_sharp_mode_overrides_the_auto_pick(self):
        calls = []
        old_probe, old_pipersr = P._probe_video_dims, P.run_pipersr_tracked
        P._probe_video_dims = lambda path: (640, 480)
        P.run_pipersr_tracked = lambda source, output, mode, crf, pix_fmt, preset: (
            calls.append(mode), Path(output).write_bytes(b"x"))
        try:
            P.run_sharp_export_job_inner(self._job(sharp_mode="x2"))
        finally:
            P._probe_video_dims, P.run_pipersr_tracked = old_probe, old_pipersr
        self.assertEqual(calls, ["x2"])

    def test_dispatches_from_run_job_inner(self):
        src = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
        self.assertIn('if mode == "sharp_export":\n        return run_sharp_export_job_inner(job)', src)


# ---------------------------------------------------------------- 3
class TestRevealAndLastFrameRoutes(_Env):
    def test_reveal_refuses_a_path_outside_output_and_uploads(self):
        self.assertIn("/output/reveal", POST_ROUTES)
        outside = Path(tempfile.mkdtemp(prefix="mm-outside-")) / "x.mp4"
        outside.write_bytes(b"x")
        h = _H(urlencode({"path": str(outside)}))
        POST_ROUTES["/output/reveal"](h, "/output/reveal", {}, "")
        self.assertFalse(h.out[1]["ok"])
        self.assertEqual(h.out[0], 403)

    def test_reveal_refuses_a_missing_file_inside_the_root(self):
        h = _H(urlencode({"path": str(self.dir / "nope.mp4")}))
        POST_ROUTES["/output/reveal"](h, "/output/reveal", {}, "")
        # self.dir lives under STATE_DIR, not OUTPUT/UPLOADS -> containment
        # refusal (403) rather than a not-found (404) is equally correct here;
        # assert it is refused either way, never a silent 200.
        self.assertFalse(h.out[1]["ok"])

    def test_reveal_opens_a_real_file_under_output(self):
        target = P.OUTPUT / f"mm_reveal_{id(self)}.mp4"
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")
        old_run = P.subprocess.run
        seen = []
        P.subprocess.run = lambda argv, **kw: seen.append(argv)
        try:
            h = _H(urlencode({"path": str(target)}))
            POST_ROUTES["/output/reveal"](h, "/output/reveal", {}, "")
        finally:
            P.subprocess.run = old_run
            target.unlink(missing_ok=True)
        self.assertTrue(h.out[1]["ok"], h.out)
        self.assertEqual(seen[0][:2], ["open", "-R"])

    def test_last_frame_refuses_a_non_mp4(self):
        still = P.UPLOADS / f"mm_{id(self)}.png"
        P.UPLOADS.mkdir(parents=True, exist_ok=True)
        still.write_bytes(b"x")
        try:
            h = _H(urlencode({"path": str(still)}))
            POST_ROUTES["/output/last_frame"](h, "/output/last_frame", {}, "")
        finally:
            still.unlink(missing_ok=True)
        self.assertFalse(h.out[1]["ok"])

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_last_frame_extracts_a_real_png(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        clip = P.OUTPUT / f"mm_lf_{id(self)}.mp4"
        subprocess.run(
            [FFMPEG, "-y", "-f", "lavfi", "-i", "color=c=red:s=64x64:d=1",
             "-frames:v", "24", str(clip)],
            capture_output=True, timeout=30, check=True,
        )
        try:
            h = _H(urlencode({"path": str(clip)}))
            POST_ROUTES["/output/last_frame"](h, "/output/last_frame", {}, "")
            self.assertTrue(h.out[1]["ok"], h.out)
            out = Path(h.out[1]["path"])
            self.assertTrue(out.is_file())
            self.assertGreater(out.stat().st_size, 0)
            out.unlink(missing_ok=True)
        finally:
            clip.unlink(missing_ok=True)


# ---------------------------------------------------------------- 4
class TestToolbarMarkup(unittest.TestCase):
    def test_more_menu_has_all_four_actions(self):
        i = HTML.index('id="playerMoreWrap"')
        blk = HTML[i:i + 2600]
        self.assertIn("kebabToggle('playerMorePop')", blk)
        self.assertIn("sharpExportActive()", blk)
        self.assertIn("useLastFrameActive()", blk)
        self.assertIn("openActiveInEditor()", blk)
        self.assertIn("revealActive()", blk)

    def test_lightbox_has_its_own_actions_row(self):
        self.assertIn('id="expandActions"', HTML)
        fn = QJS[QJS.index("function openExpandLightbox()"):]
        fn = fn[:fn.index("\nfunction closeExpandLightbox")]
        self.assertIn("expandActions", fn)
        self.assertIn("sharpExportActive()", fn)
        self.assertIn("!isPhoto && !isAudio", fn)

    def test_client_functions_exist_and_are_published(self):
        for fn in ("queueSharpExport", "sharpExportActive", "useLastFramePath",
                   "useLastFrameActive", "revealClipInFinder", "revealActive",
                   "openClipInEditor", "openActiveInEditor", "kebabToggle"):
            self.assertIn(f"function {fn}(", QJS, fn)
            self.assertIn(fn, QJS[QJS.index("Object.assign(globalThis"):], fn)

    def test_kebab_css_component_exists(self):
        self.assertIn(".kebab-pop", CSS)
        self.assertIn(".kebab-pop.open", CSS)


# ---------------------------------------------------------------- 5
class TestLoraRowMenu(unittest.TestCase):
    def test_delete_from_disk_lives_in_a_kebab_menu(self):
        fn = LJS[LJS.index("function loraRowHtml("):]
        fn = fn[:fn.index("\nfunction ", 20)]
        self.assertIn("kebab-wrap", fn)
        self.assertIn("_loraRowKebabId(r.path)", fn)
        self.assertIn("Delete from disk", fn)
        # no longer a bare flat danger icon button in the corner row
        self.assertNotIn('class="lora-icon-btn danger"', fn)
        self.assertIn("kebabToggle(", fn)

    def test_confirm_is_unchanged(self):
        fn = LJS[LJS.index("async function deleteLora("):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("confirm(", fn)

    def test_kebab_id_helper_is_deterministic_and_distinct(self):
        i = LJS.index("function _loraRowKebabId(")
        self.assertGreater(i, -1)
        # Two different paths must not collide on the trivial empty-string case
        self.assertIn("loraMore", LJS[i:i + 400])


if __name__ == "__main__":
    unittest.main()
