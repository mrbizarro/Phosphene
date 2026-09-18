#!/usr/bin/env python3
"""Upscale & Face Fix — the renamed Upscale ×2 lane as a one-click clip action.

Owner, 2026-09-17: "the upscaler, this thing we ship that fixes the faces, has
the wrong name. It should be called something like 'Upscale and Face Fix', and
it should be optional and also something you can do to an existing clip."

Gated here:
  1  one recipe (keep_shot 1.0, start=source, 1 refine step) for every door,
     and the queue label actually reaches the job (`preset_label`)
  2  /queue/facefix queues it for a clip with the clip's own prompt + seed,
     refuses what the worker would refuse late, and dedupes a double click
  3  an Editor order is remembered and offered against THAT clip only while
     it still plays the source; the relink can pick the file by `to`
  4  the lane's own "Faithful" pill is unchanged (3 refine steps)
  5  the UI: player split button, card chip, history button, Editor clip bar,
     after-the-draft checkbox (off, not persisted), renamed labels + docs
  6  internal ids stay: mode=upscale, h3_upscale=ltx_x2, the form keys

No renders, no GPU: make_job and the queue are exercised directly.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = Path(tempfile.mkdtemp(prefix="phos-facefix-"))
os.environ["LTX_STATE_DIR"] = str(STATE)
os.environ["PHOSPHENE_ANALYTICS_DISABLED"] = "1"
os.environ["PHOSPHENE_DISABLE_VERSION_CHECK"] = "1"
os.environ.setdefault("LTX_PORT", "8317")
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P  # noqa: E402
from panel.routes import POST_ROUTES  # noqa: E402

SRC = (ROOT / "mlx_ltx_panel.py").read_text(encoding="utf-8")
HTML = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
QJS = (ROOT / "webapp" / "js" / "queue.js").read_text(encoding="utf-8")
EJS = (ROOT / "webapp" / "js" / "engines.js").read_text(encoding="utf-8")
EDJS = (ROOT / "webapp" / "js" / "editor.js").read_text(encoding="utf-8")
DOCS = ROOT / "webapp" / "docs"


class _Env(unittest.TestCase):
    """A clip with a sidecar, the adapter 'installed', a probe that answers."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ff-", dir=STATE))
        self.clip = self.dir / "gym_draft.mp4"
        self.clip.write_bytes(b"\0" * 64)
        Path(str(self.clip) + ".json").write_text(json.dumps({"params": {
            "prompt": "A woman lifts a dumbbell", "seed": "-1",
            "seed_used": 52190, "label": "gym", "mode": "t2v"}}), encoding="utf-8")
        self.adapter = self.dir / "adapter.safetensors"
        self.adapter.write_bytes(b"x")
        self._saved = (P.CURATED_LORAS["upscale_x2"].get("local_path"),
                       P._probe_video_dims, P.persist_queue, P.tier_max_dim,
                       P._probe_video_frames)
        P.CURATED_LORAS["upscale_x2"]["local_path"] = str(self.adapter)
        P._probe_video_dims = lambda path: (640, 384)
        P._probe_video_frames = lambda path: 240
        P.tier_max_dim = lambda kind: 1536
        P.persist_queue = lambda: None
        with P.QUEUE_COND:
            self._queue_before = list(P.STATE["queue"])

    def tearDown(self):
        (P.CURATED_LORAS["upscale_x2"]["local_path"], P._probe_video_dims,
         P.persist_queue, P.tier_max_dim, P._probe_video_frames) = self._saved
        with P.QUEUE_COND:
            P.STATE["queue"] = self._queue_before
            P.STATE["current"] = None

    def queued(self):
        with P.QUEUE_COND:
            return [j for j in P.STATE["queue"] if j not in self._queue_before]


# ---------------------------------------------------------------- 1
class TestRecipe(unittest.TestCase):
    def test_recipe_is_the_verified_face_safe_one(self):
        self.assertEqual(P.FACE_FIX_RECIPE, {"keep_shot": "1.0",
                                             "upscale_start": "source",
                                             "upscale_steps": "1"})
        self.assertEqual(P.FACE_FIX_NAME, "Upscale & Face Fix")

    def test_form_goes_through_make_job_intact(self):
        form = P.face_fix_form("/x/clip.mp4", prompt="p", seed=7, label="clip")
        j = P.make_job({k: [v] for k, v in form.items()})
        p = j["params"]
        self.assertEqual(p["mode"], "upscale")
        self.assertEqual(p["upscale_source_path"], "/x/clip.mp4")
        self.assertEqual(p["keep_shot"], "1.0")
        self.assertEqual(p["upscale_start"], "source")
        self.assertEqual(p["upscale_steps"], "1")
        self.assertEqual(p["prompt"], "p")
        self.assertEqual(str(p["seed"]), "7")
        # the old chain passed `label`, which make_job never reads
        self.assertEqual(p["label"], "clip · Upscale & Face Fix")

    def test_chain_uses_the_same_recipe_and_label(self):
        seen = {}
        old_mj, old_pq = P.make_job, P.persist_queue

        def fake(form):
            seen.update(form)
            return {"id": "ffchain", "params": {}}
        P.make_job, P.persist_queue = fake, (lambda: None)
        try:
            P._chain_upscale_after_h3({"id": "d1"}, {"prompt": "hi", "seed": "-1",
                                                     "seed_used": 9, "label": "gym",
                                                     "keep_shot": "0.4"},
                                      Path("/tmp/gym.mp4"))
            with P.QUEUE_COND:
                job = next(j for j in P.STATE["queue"] if j.get("id") == "ffchain")
        finally:
            P.make_job, P.persist_queue = old_mj, old_pq
            with P.QUEUE_COND:
                P.STATE["queue"] = [j for j in P.STATE["queue"] if j.get("id") != "ffchain"]
        self.assertEqual(seen["upscale_steps"], "1")
        self.assertEqual(seen["upscale_start"], "source")
        # a leftover Re-imagine pill on the form does not leak into the fix
        self.assertEqual(seen["keep_shot"], "1.0")
        self.assertEqual(seen["preset_label"], "gym · Upscale & Face Fix")
        self.assertEqual(job["params"]["source"], "chain")
        self.assertTrue(job["params"]["face_fix"])

    def test_worker_step_mapping_is_unchanged(self):
        # Faithful (keep_shot 1.0, no explicit steps) still resolves to 3.
        self.assertIn('refine_steps = 3 if keep >= 0.95 else 2', SRC)
        self.assertIn('if p.get("upscale_steps") and start_from == "source":', SRC)
        self.assertIn('"display_name": FACE_FIX_NAME', SRC)


# ---------------------------------------------------------------- 2
class TestQueueFaceFix(_Env):
    def test_queues_with_the_clips_own_prompt_and_seed(self):
        r = P.queue_face_fix(str(self.clip))
        self.assertTrue(r["ok"], r)
        jobs = self.queued()
        self.assertEqual(len(jobs), 1)
        p = jobs[0]["params"]
        self.assertEqual(p["prompt"], "A woman lifts a dumbbell")
        self.assertEqual(str(p["seed"]), "52190")
        self.assertEqual(p["label"], "gym · Upscale & Face Fix")
        self.assertEqual(p["upscale_steps"], "1")
        self.assertTrue(p["face_fix"])
        self.assertNotIn("face_fix_targets", p)
        # the whole clip, not make_job's 121-frame default: 240 frames asks
        # for the grid above (241, which make_job does not floor) and the
        # lane delivers all 240
        self.assertEqual(p["frames"], 241)
        self.assertEqual(P.upscale_frame_plan(240, p["frames"]), (240, 241))

    def test_a_one_shot_take_uses_the_whole_takes_prompt(self):
        Path(str(self.clip) + ".json").write_text(json.dumps({
            "prompt": "the whole take", "label": "take", "take": {"seconds": 20},
            "params": {"prompt": "part 4 only", "label": "part 4 of 4", "seed_used": 3}}),
            encoding="utf-8")
        P.queue_face_fix(str(self.clip))
        p = self.queued()[0]["params"]
        self.assertEqual(p["prompt"], "the whole take")
        self.assertEqual(p["label"], "take · Upscale & Face Fix")

    def test_double_click_is_one_job(self):
        a = P.queue_face_fix(str(self.clip))
        b = P.queue_face_fix(str(self.clip))
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b.get("duplicate"))
        self.assertEqual(len(self.queued()), 1)

    def test_click_while_it_renders_is_not_a_second_job(self):
        a = P.queue_face_fix(str(self.clip))
        with P.QUEUE_COND:
            job = next(j for j in P.STATE["queue"] if j["id"] == a["id"])
            P.STATE["queue"].remove(job)
            P.STATE["current"] = job
        b = P.queue_face_fix(str(self.clip))
        self.assertEqual(b["id"], a["id"])
        self.assertTrue(b["running"])
        self.assertEqual(self.queued(), [])

    def test_refusals_come_before_the_queue(self):
        self.assertFalse(P.queue_face_fix(str(self.dir / "gone.mp4"))["ok"])
        still = self.dir / "still.png"
        still.write_bytes(b"x")
        self.assertIn("video", P.queue_face_fix(str(still))["error"])
        self.assertFalse(P.queue_face_fix(str(self.clip), board_id="../etc",
                                          clip_id="c1")["ok"])
        (STATE / "storyboard.json").write_text("{}", encoding="utf-8")
        try:
            for bad in ("..", ".", "a.b"):
                self.assertFalse(P.queue_face_fix(str(self.clip), board_id=bad,
                                                  clip_id="c1")["ok"], bad)
            self.assertFalse((STATE / "face_fix.json").exists())
        finally:
            (STATE / "storyboard.json").unlink()
        self.assertFalse(P.queue_face_fix(str(self.clip), board_id="nosuchfilm",
                                          clip_id="c1")["ok"])
        P._probe_video_dims = lambda path: (1280, 768)
        r = P.queue_face_fix(str(self.clip))
        self.assertEqual(r.get("code"), "hardware_tier")
        P._probe_video_dims = lambda path: (640, 384)
        P.CURATED_LORAS["upscale_x2"]["local_path"] = str(self.dir / "missing")
        r = P.queue_face_fix(str(self.clip))
        self.assertEqual(r.get("code"), "pack_missing")
        self.assertEqual(self.queued(), [])

    def test_route_is_registered(self):
        self.assertIn("/queue/facefix", POST_ROUTES)

    def test_route_answers_json(self):
        class H:
            def __init__(s, body):
                s.body, s.out = body, None

            def _read_form_body(s):
                from urllib.parse import parse_qs
                return s.body, parse_qs(s.body)

            def _json(s, obj, code=200):
                s.out = (code, obj)
        from urllib.parse import urlencode
        h = H(urlencode({"path": str(self.clip)}))
        POST_ROUTES["/queue/facefix"](h, "/queue/facefix", {}, "")
        self.assertEqual(h.out[0], 200)
        self.assertTrue(h.out[1]["ok"])
        h = H(urlencode({"path": str(self.dir / "nope.mp4")}))
        POST_ROUTES["/queue/facefix"](h, "/queue/facefix", {}, "")
        self.assertEqual(h.out[0], 400)


# ---------------------------------------------------------------- 3
class TestEditorOffer(_Env):
    def setUp(self):
        super().setUp()
        self.bid = "fffilm_" + self._testMethodName[-24:]
        bdir = P._sbe_board_dir(self.bid)
        bdir.mkdir(parents=True, exist_ok=True)
        (bdir / "storyboard.json").write_text(json.dumps({"id": self.bid, "shots": []}),
                                              encoding="utf-8")

    def test_order_is_remembered_and_offered_once_it_lands(self):
        r = P.queue_face_fix(str(self.clip), board_id=self.bid, clip_id="c7")
        self.assertTrue(r["ok"], r)
        p = self.queued()[0]["params"]
        self.assertEqual(p["face_fix_targets"], [[self.bid, "c7"]])
        edit = {"clips": [{"id": "c7", "path": str(self.clip), "title": "gym",
                           "in": 0.5, "out": 2.0}]}
        board = {"id": self.bid, "shots": []}
        self.assertEqual(P._sbe_relinks(board, edit), [])      # not rendered yet
        fixed = self.dir / "gym_draft_x2_1.mp4"
        fixed.write_bytes(b"x")
        P._face_fix_note(self.bid, "c7", job=r["id"], to=str(fixed),
                         **{"from": str(self.clip)})
        # a late registration for the same job does not erase the result
        P._face_fix_note(self.bid, "c7", job=r["id"], to="", **{"from": str(self.clip)})
        rows = P._sbe_relinks(board, edit)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]["face_fix"] and rows[0]["retake"])
        self.assertEqual(rows[0]["to"], str(fixed))
        # swapped: no more offer
        edit["clips"][0]["path"] = str(fixed)
        self.assertEqual(P._sbe_relinks(board, edit), [])
        # a clip re-pointed at some other file gets no offer either
        edit["clips"][0]["path"] = str(self.dir / "other.mp4")
        self.assertEqual(P._sbe_relinks(board, edit), [])

    def test_one_waiting_job_answers_every_clip_that_asked(self):
        a = P.queue_face_fix(str(self.clip))
        b = P.queue_face_fix(str(self.clip), board_id=self.bid, clip_id="c9")
        c = P.queue_face_fix(str(self.clip), board_id=self.bid, clip_id="c10")
        self.assertEqual({a["id"], b["id"], c["id"]}, {a["id"]})
        p = self.queued()[0]["params"]
        self.assertEqual(p["face_fix_targets"], [[self.bid, "c9"], [self.bid, "c10"]])
        recs = P._face_fix_records(self.bid)
        self.assertEqual((recs["c9"]["job"], recs["c10"]["job"]), (a["id"], a["id"]))

    def test_a_click_after_the_render_landed_gets_the_result(self):
        a = P.queue_face_fix(str(self.clip))
        fixed = self.dir / "landed_x2.mp4"
        fixed.write_bytes(b"x")
        with P.QUEUE_COND:
            job = next(j for j in P.STATE["queue"] if j["id"] == a["id"])
            P.STATE["queue"].remove(job)
            P.STATE["current"] = job
            job["params"]["face_fix_result"] = {"to": str(fixed), "complete": True}
        P.queue_face_fix(str(self.clip), board_id=self.bid, clip_id="c4")
        rec = P._face_fix_records(self.bid)["c4"]
        self.assertEqual((rec["to"], rec["complete"]), (str(fixed), True))

    def test_a_short_fix_is_not_offered_as_a_swap(self):
        fixed = self.dir / "short_x2.mp4"
        fixed.write_bytes(b"x")
        P._face_fix_note(self.bid, "c3", job="j1", to=str(fixed), complete=False,
                         **{"from": str(self.clip)})
        edit = {"clips": [{"id": "c3", "path": str(self.clip)}]}
        self.assertEqual(P._sbe_relinks({"id": self.bid, "shots": []}, edit), [])

    def test_worker_and_relink_wiring(self):
        blk = SRC[SRC.index('    if mode == "upscale":'):SRC.index('    if mode == "ingredients":')]
        self.assertIn('p.get("face_fix_targets")', blk)
        self.assertIn("delivered == src_frames", blk)
        # the targets are read under the same lock registration appends under
        i = blk.index('p["face_fix_result"] = _ff_result')
        self.assertIn("with QUEUE_COND:", blk[i - 80:i])
        chain = SRC[SRC.index("def _chain_upscale_after_h3"):SRC.index("def run_h3_job_inner")]
        self.assertIn("frames=_probe_video_frames(str(native_path))", chain)
        rq = (ROOT / "panel" / "routes_queue.py").read_text(encoding="utf-8")
        self.assertIn('new_job["params"].pop("face_fix_result", None)', rq)
        self.assertIn("_face_fix_note(", blk)
        self.assertIn('_face_fix_offers(str(board.get("id")', SRC)
        rel = SRC[SRC.index('if sub == "relink":'):]
        rel = rel[:rel.index('if sub == "cancel":')]
        self.assertIn('want_to = f("to", "")', rel)
        # the swap rewrites the path only: in/out points are the clip's own
        self.assertIn('c["path"] = to', rel)
        self.assertNotIn('c["in"]', rel)


# ---------------------------------------------------------------- 5
class TestUI(unittest.TestCase):
    def test_player_split_button(self):
        i = HTML.index('id="faceFixWrap"')
        blk = HTML[i:i + 2200]
        self.assertIn('onclick="faceFixActive()"', blk)
        self.assertIn('<span class="po-act-label">Upscale &amp; Face Fix</span>', blk)
        self.assertIn('id="useAsUpscaleBtn"', blk)            # the settings half
        self.assertIn('onclick="useAsUpscaleSource()"', blk)
        self.assertNotIn(">LTX Upscale<", HTML)
        self.assertIn("getElementById('faceFixWrap')", QJS)

    def test_face_fix_only_on_the_big_player(self):
        # Owner 2026-09-17: no Face Fix on Outputs thumbnails or queue rows,
        # and never on a clip that is already an upscale.
        self.assertIn("fetch('/queue/facefix'", QJS)
        self.assertNotIn("card-action-facefix", QJS)
        self.assertNotIn("facefix-btn", QJS)
        self.assertRegex(QJS, r"faceFixClip, faceFixActive, isUpscaledPath")
        self.assertIn("isUpscaledPath(o && o.path)", QJS)
        self.assertIn("This clip is already upscaled.",
                      (Path(__file__).resolve().parent / "webapp/js/editor.js").read_text())

    def test_is_upscaled_path_pattern(self):
        import re
        m = re.search(r"function isUpscaledPath\(p\) \{\n  return /(.+)/i\.test", QJS)
        self.assertTrue(m)
        rx = re.compile(m.group(1).replace('\\\\', '\\'), re.I)
        for yes in ("a_h3_8_x2_20260917_120000.mp4", "x2_faithful_nolaugh/s01.mp4", "v01_x2f.mp4", "v01_bizvoice.mp4"):
            self.assertTrue(rx.search(yes), yes)
        for no in ("for_the_target_video_at_0_h3_55.mp4", "shot_1_live_h3_21.mp4", "max2_take.mp4"):
            self.assertFalse(rx.search(no), no)

    def test_remix_lane_names_and_presets(self):
        self.assertIn('data-remix="upscale"', HTML)
        self.assertIn('>Upscale &amp; Face Fix<span class="mc-sub sub">', HTML)
        grp = HTML[HTML.index('id="upscalePresetGroup"'):]
        grp = grp[:grp.index('id="keep_shot"')]
        pills = re.findall(r'<button[^>]*data-keep="([^"]+)"([^>]*)>', grp)
        self.assertEqual(len(pills), 4)
        self.assertIn('data-steps="1"', pills[0][1])           # Face Fix first
        self.assertIn('class="pill-btn active"', grp.split("<button")[1])
        for keep, rest in pills[1:]:
            self.assertNotIn("data-steps", rest)               # server mapping
        self.assertIn('id="upscale_start" value="source"', HTML)
        self.assertIn('id="upscale_steps" value="1"', HTML)
        fn = QJS[QJS.index("function setUpscalePreset"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("upscale_start", fn)
        self.assertIn("upscale_steps", fn)

    def test_load_params_restores_the_exact_recipe(self):
        lp = QJS[QJS.index("  else if (p.mode === 'upscale') {"):]
        lp = lp[:lp.index("  else if (p.mode === 'extend')")]
        self.assertIn("(b.dataset.start || '') === start && (b.dataset.steps || '') === steps", lp)
        self.assertIn("set('keep_shot', keep); set('upscale_start', start); set('upscale_steps', steps);", lp)
        self.assertIn("p.source_frames || p.frames", lp)
        self.assertIn("1 + 8 * Math.ceil((fr - 1) / 8)", lp)
        self.assertNotIn("getElementById('duration')", lp)
        self.assertIn("Number(b.dataset.keep) === keepN", lp)
        self.assertNotIn("toFixed", lp)
        self.assertIn("(p.seed_used != null) ? p.seed_used : p.seed", lp)

    def test_after_draft_is_an_optional_checkbox_off_by_default(self):
        self.assertNotIn('data-h3-upscale="ltx_x2"', HTML)
        row = HTML[HTML.index('id="h3FaceFixAfterRow"'):]
        row = row[:row.index("</label>")]
        self.assertIn('type="checkbox" id="h3FaceFixAfter"', row)
        tag = row.split('id="h3FaceFixAfter"')[1].split(">")[0]
        self.assertNotRegex(tag, r'(^|\s)checked(\s|=|$)')
        self.assertIn("Also run <b>Upscale &amp; Face Fix</b> after the draft", row)
        self.assertIn('id="h3_upscale" value="fit_720p"', HTML)
        self.assertEqual(P.H3_UPSCALE_DEFAULT, "fit_720p")
        self.assertIn("ltx_x2", P.H3_UPSCALE_MODES)            # stored value kept
        # never saved as the preference, never restored on reload
        self.assertIn("if (!fix) { try { localStorage.setItem('phos_h3_upscale', v)", EJS)
        self.assertIn("savedUp !== 'ltx_x2'", EJS)
        self.assertIn("setH3FaceFixAfter", EJS)

    def test_editor_clip_bar_and_offer(self):
        self.assertIn('id="sbeCbFaceFix" onclick="sbeFaceFixSel()"', HTML)
        self.assertIn("<b>Face Fix ×2</b>", HTML)
        self.assertIn("menuLabel: 'Upscale & Face Fix'", EDJS)
        self.assertIn('<symbol id="ic-facefix" viewBox="0 0 256 256">', HTML)
        self.assertEqual(EDJS.count("id: 'sbeCbFaceFix'"), 2)  # picture + sound models
        fn = EDJS[EDJS.index("async function sbeFaceFixSel()"):]
        fn = fn[:fn.index("\n}\n")]
        self.assertIn("sbeSave(true)", fn)
        self.assertIn("if (SBE.conflict)", fn)
        self.assertIn("fd.set('to', to)", EDJS)
        self.assertIn("board: film, clip: c.id", EDJS)
        self.assertIn("Swap it in", EDJS)
        self.assertIn("sbeRetakeUse, sbeFaceFixSel,", EDJS)
        # finishing a fix never adopts the arrangement over unsaved edits
        tick = EDJS[EDJS.index("async function sbeTick()"):]
        tick = tick[:tick.index("if (SBE.awaitingClip")]
        self.assertIn("sbeRefreshOffers()", tick)
        self.assertNotIn("sbeLoad(", tick[tick.index("SBE.fixHandled"):])
        self.assertIn("face_fix_targets", tick)
        # handled only after a refresh that worked
        self.assertLess(tick.index("await sbeRefreshOffers()"), tick.index("SBE.fixHandled[j.id] = 1"))
        sw = EDJS[EDJS.index("async function sbeFaceFixSwap("):]
        sw = sw[:sw.index("\n}\n")]
        # a local, undoable edit: no server rewrite, no adoption
        self.assertNotIn("sbeAdopt", sw)
        self.assertNotIn("/storyboard/edit/relink", sw)
        self.assertIn("SBE.undo.push(before)", sw)
        self.assertIn("SBE.dirty = true", sw)
        self.assertIn("String(c.path) !== String(row.path)", sw)
        self.assertIn("r.face_fix ? 'sbeFaceFixSwap(' : 'sbeRetakeUse('", EDJS)
        self.assertNotIn("fixPending", EDJS)

    def test_docs_use_the_new_name(self):
        allmd = "\n".join(f.read_text(encoding="utf-8") for f in DOCS.glob("*.md"))
        # the old name survives once, so a search for it still lands here
        self.assertEqual(allmd.count("LTX Upscale"), 1)
        self.assertIn("Earlier versions called this **LTX Upscale**", allmd)
        self.assertNotIn("Upscale ×2 pass", allmd)
        self.assertIn("## Upscale & Face Fix {#upscale-face-fix}", allmd)
        self.assertIn("#docs/remix/upscale-face-fix", allmd)
        self.assertIn("**Face Fix ×2** (Upscale & Face Fix)", (DOCS / "editor.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
