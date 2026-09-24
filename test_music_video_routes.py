"""The two music-video routes: what they refuse, and what they write.

Same shape as `test_music_studio.py` — the route functions are called
directly with a stand-in handler, because the thing worth testing is the
door policy, not HTTP. The policy is the one every other route in this file's
neighbour obeys: **a form field naming a file is a form field naming a file**.
It resolves inside the outputs folder or the uploads folder, or it is not
used — and a role is one of three words or the request is refused.

The song is a real click track and the beat map that reads it is the real
one, so `/music/video/plan` is exercised end to end: form -> sections ->
plan -> a board on disk that `load_storyboard` can read back.
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

import mlx_ltx_panel as P
import music_video as mv
import storyboard
from panel import routes_music as R
from panel.routes import POST_ROUTES
from test_music_video import LYRICS, SCORE, click_track

R.P = P


class _Handler:
    """Enough of the panel's handler for a route to answer into."""

    def __init__(self, form: dict):
        self._form = {k: [v] for k, v in form.items()}
        self.status = 200
        self.body = None

    def _read_form_body(self):
        return urlencode(self._form, doseq=True), self._form

    def _json(self, payload, status=200):
        self.body, self.status = payload, status


class _Fixture:
    """A song in the outputs and three pictures in the uploads."""

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        P.UPLOADS.mkdir(parents=True, exist_ok=True)
        # Resolved, because the routes resolve: on macOS the sandbox lives
        # under /var, which is a symlink to /private/var, and an unresolved
        # fixture path would compare unequal to a correct answer.
        self.song = (P.OUTPUT / "_mvtest_song.wav").resolve()
        click_track(self.song, bars=24, vocal_bars=range(8, 16))
        self.sidecar = Path(str(self.song) + ".json")
        self.sidecar.write_text(json.dumps({"engine": "music",
                                            "title": "Click Track",
                                            "lyrics": "", "score_abc": ""}))
        self.images = []
        for name, role in (("_mvtest_face.png", "singer"),
                           ("_mvtest_piano.png", "instrument"),
                           ("_mvtest_room.png", "room")):
            p = (P.UPLOADS / name).resolve()
            p.write_bytes(b"\x89PNG\r\n\x1a\n")
            self.images.append({"path": str(p), "role": role})
        # A pre-separated vocal of the same song, in the outputs where a
        # panel-made file lives.
        self.stem = (P.OUTPUT / "_mvtest_song_vocals.wav").resolve()
        click_track(self.stem, bars=24, vocal_bars=range(8, 16))
        self.outsider = root / "outside.png"
        self.outsider.write_bytes(b"\x89PNG\r\n\x1a\n")
        return self

    def __exit__(self, *a):
        self.song.unlink(missing_ok=True)
        self.stem.unlink(missing_ok=True)
        self.sidecar.unlink(missing_ok=True)
        for im in self.images:
            Path(im["path"]).unlink(missing_ok=True)
        self.tmp.cleanup()

    def form(self, **over):
        base = {"song": str(self.song), "images": json.dumps(self.images),
                "style": "handheld 16mm documentary", "title": "Click Track"}
        base.update(over)
        return base


class TheDoorTrustsNothing(unittest.TestCase):

    def test_a_file_outside_the_panel_is_not_a_panel_file(self):
        with _Fixture() as fx:
            self.assertIsNone(R._panel_file("/etc/passwd"))
            self.assertIsNone(R._panel_file(""))
            self.assertIsNone(R._panel_file(str(fx.outsider)))
            self.assertIsNone(R._panel_file(str(P.OUTPUT / "nope.wav")))
            self.assertEqual(R._panel_file(str(fx.song)), fx.song.resolve())
            self.assertEqual(R._panel_file(fx.images[0]["path"]),
                             Path(fx.images[0]["path"]).resolve())

    def test_an_upload_counts_even_though_it_has_no_sidecar(self):
        # `_song_output` needs a music sidecar; a song somebody dropped in has
        # none and never will, which is why the music-video door is wider.
        with _Fixture() as fx:
            self.assertIsNotNone(R._panel_file(fx.images[0]["path"]))
            self.assertIsNone(R._song_output(fx.images[0]["path"]))

    def test_the_roles_are_a_closed_vocabulary(self):
        with _Fixture() as fx:
            self.assertEqual(R.ROLES, mv.ROLES)
            bad = [dict(fx.images[0], role="guitarist")]
            rows, err = R._music_video_images(json.dumps(bad))
            self.assertEqual(rows, [])
            self.assertIn("guitarist", err)

    def test_a_picture_from_outside_is_named_and_refused(self):
        with _Fixture() as fx:
            rows, err = R._music_video_images(json.dumps(
                [{"path": str(fx.outsider), "role": "room"}]))
            self.assertEqual(rows, [])
            self.assertIn("outside.png", err)

    def test_broken_or_empty_json_is_a_sentence_not_a_traceback(self):
        for raw in ("", "[]", "{", "not json", '["a string"]'):
            rows, err = R._music_video_images(raw)
            self.assertEqual(rows, [])
            self.assertTrue(err)

    def test_a_plan_with_no_song_is_refused(self):
        with _Fixture() as fx:
            h = _Handler(fx.form(song="/etc/passwd"))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 400)
            self.assertIn("song", h.body["error"])

    def test_a_plan_with_a_bad_role_is_refused_before_any_decode(self):
        with _Fixture() as fx:
            bad = [dict(fx.images[0], role="")]
            h = _Handler(fx.form(images=json.dumps(bad)))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 400)

    def test_a_pin_and_a_weight_are_a_closed_vocabulary_too(self):
        with _Fixture() as fx:
            for bad, word in (({"use": "middle"}, "use"),
                              ({"weight": "lots"}, "weight"),
                              ({"weight": 0}, "weight"),
                              ({"weight": 99}, "weight")):
                rows, err = R._music_video_images(
                    json.dumps([dict(fx.images[0], **bad)]))
                self.assertEqual(rows, [])
                self.assertIn(word, err)

    def test_a_good_pin_and_weight_reach_the_planner(self):
        with _Fixture() as fx:
            rows, err = R._music_video_images(json.dumps(
                [dict(fx.images[1], use="close", weight=3)]))
            self.assertEqual(err, "")
            self.assertEqual(rows[0]["use"], "close")
            self.assertEqual(rows[0]["weight"], 3)
            # Absent is the old shape, and it still means "no opinion".
            rows, _ = R._music_video_images(json.dumps([fx.images[1]]))
            self.assertEqual((rows[0]["use"], rows[0]["weight"]), ("any", 1))


class TheRefusalSaysWhereFilesGo(unittest.TestCase):
    """The containment rule stays; the dead end goes. A refusal that does not
    say where a file BECOMES a panel file leaves the person guessing at a
    folder name, which is what the first real film's operator had to do."""

    def test_a_picture_from_outside_is_told_the_uploads_folder_and_the_route(self):
        with _Fixture() as fx:
            _rows, err = R._music_video_images(json.dumps(
                [{"path": str(fx.outsider), "role": "room"}]))
            self.assertIn(str(P.UPLOADS), err)
            self.assertIn(str(P.OUTPUT), err)
            self.assertIn("/upload", err)

    def test_a_song_from_outside_gets_the_same_sentence(self):
        with _Fixture() as fx:
            h = _Handler(fx.form(song=str(fx.outsider)))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 400)
            self.assertIn(str(P.UPLOADS), h.body["error"])
            self.assertIn("/upload", h.body["error"])

    def test_the_route_the_error_names_is_a_route_this_panel_has(self):
        # An error that points at an endpoint which does not exist is worse
        # than the bare one it replaced.
        self.assertIn("/upload", POST_ROUTES)


class TheStructureCanBeGiven(unittest.TestCase):
    """Precedence, strongest first: `sections` > form lyrics/score > the
    song's sidecar > the band classifier."""

    def test_sections_beat_the_classifier_on_a_song_with_no_sidecar(self):
        with _Fixture() as fx:
            # A song somebody dropped in: in the uploads, no sidecar, and with
            # NO voice band at all — the classifier would call every second of
            # it instrumental and the film would have no singing shot.
            song = (P.UPLOADS / "_mvtest_dropped.wav").resolve()
            click_track(song, bars=24, vocal_bars=())
            try:
                rows = [{"start": 0.0, "end": 12.0, "kind": "instrumental",
                         "label": "open"},
                        {"start": 12.0, "end": 48.0, "kind": "vocal",
                         "label": "verse"}]
                h = _Handler(fx.form(song=str(song),
                                     sections=json.dumps(rows)))
                R.post_music_video_plan(h, "/music/video/plan", {}, "")
                self.assertEqual(h.status, 200, h.body)
                self.assertEqual([s["source"] for s in h.body["sections"]],
                                 ["given", "given"])
                sung = [r for r in h.body["shots"] if r["kind"] == "singing"]
                self.assertTrue(sung, "the vocal span produced no singing shot")
                for r in sung:
                    self.assertGreaterEqual(r["film_start"], 12.0 - 1e-6)
                    self.assertEqual(r["section"], "verse")
            finally:
                song.unlink(missing_ok=True)

    def test_a_score_in_the_form_is_read_when_the_sidecar_has_none(self):
        with _Fixture() as fx:
            h = _Handler(fx.form(lyrics=LYRICS, score_abc=SCORE))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            self.assertEqual([s["name"] for s in h.body["sections"]],
                             ["intro", "verse", "outro"])
            self.assertTrue(all(s["source"] == "score"
                                for s in h.body["sections"]))

    def test_a_score_in_the_form_outranks_the_one_in_the_sidecar(self):
        # A song re-cut, or one whose sidecar belongs to an earlier take: the
        # score in the request is the one the caller is looking at.
        with _Fixture() as fx:
            stale = SCORE.replace("% intro", "% nobody").replace(
                "% verse", "% wrong").replace("% outro", "% stale")
            fx.sidecar.write_text(json.dumps(
                {"engine": "music", "title": "Click Track",
                 "lyrics": LYRICS, "score_abc": stale}))
            h = _Handler(fx.form(score_abc=SCORE, lyrics=LYRICS))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            self.assertEqual([s["name"] for s in h.body["sections"]],
                             ["intro", "verse", "outro"])

    def test_unreadable_sections_are_a_sentence_not_a_traceback(self):
        with _Fixture() as fx:
            for raw, word in (("{", "JSON"),
                              ("[]", "list"),
                              ('[{"start": 0, "end": 5}]', "kind"),
                              ('[{"start": 0, "end": 5, "kind": "vocal"},'
                               ' {"start": 2, "end": 9, "kind": "vocal"}]',
                               "overlap")):
                h = _Handler(fx.form(sections=raw))
                R.post_music_video_plan(h, "/music/video/plan", {}, "")
                self.assertEqual(h.status, 400, raw)
                self.assertIn(word, h.body["error"])


class TheShotPromptsAreParsed(unittest.TestCase):

    def test_both_spellings_mean_the_same_thing(self):
        listed, err = R._music_video_shot_prompts(
            json.dumps([{"n": 3, "prompt": "the lights fade"}]))
        self.assertEqual((listed, err), ({3: "the lights fade"}, ""))
        mapped, err = R._music_video_shot_prompts(
            json.dumps({"3": "the lights fade"}))
        self.assertEqual((mapped, err), ({3: "the lights fade"}, ""))
        self.assertEqual(R._music_video_shot_prompts(""), ({}, ""))

    def test_nonsense_is_named_rather_than_swallowed(self):
        for raw, word in (("{", "JSON"),
                          ('"a string"', "list"),
                          ('[{"prompt": "x"}]', "shot number"),
                          ('[{"n": 0, "prompt": "x"}]', "start at 1"),
                          ('[{"n": 2, "prompt": "  "}]', "empty")):
            rows, err = R._music_video_shot_prompts(raw)
            self.assertEqual(rows, {})
            self.assertIn(word, err, raw)

    def test_a_line_written_for_one_shot_reaches_that_shot_alone(self):
        with _Fixture() as fx:
            base = _Handler(fx.form())
            R.post_music_video_plan(base, "/music/video/plan", {}, "")
            self.assertEqual(base.status, 200, base.body)
            rows = base.body["shots"]
            self.assertTrue(all(r["prompt_override"] is False for r in rows))
            n = rows[-1]["n"]
            h = _Handler(fx.form(shots=json.dumps(
                [{"n": n, "prompt": "the lights fade down to black"}])))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            after = {r["n"]: r for r in h.body["shots"]}
            self.assertIn("the lights fade down to black", after[n]["prompt"])
            self.assertTrue(after[n]["prompt_override"])
            for r in rows[:-1]:
                self.assertEqual(after[r["n"]]["prompt"], r["prompt"])


class TheAxisFollowsTheDeliveryQuality(unittest.TestCase):

    def test_no_shot_uses_a_cell_the_final_pass_cannot_render(self):
        with _Fixture() as fx:
            h = _Handler(fx.form())
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            quality = str(P.get_settings().get(
                "storyboard_final_quality", "standard") or "standard")
            self.assertEqual(h.body["quality"], quality)
            allowed = {f for _s, f in
                       mv.cells_from_lengths(P.LTX_LENGTHS, quality=quality)}
            for row in h.body["shots"]:
                if row["mode"] == "a2v":
                    self.assertIn(row["frames"], allowed, row)
            board = storyboard.load_storyboard(P.STATE_DIR, h.body["board_id"])
            self.assertEqual(board["music_video"]["quality"], quality)


class ThePlanRouteWritesABoard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.fx = _Fixture().__enter__()
        cls.h = _Handler(cls.fx.form())
        R.post_music_video_plan(cls.h, "/music/video/plan", {}, "")

    @classmethod
    def tearDownClass(cls):
        cls.fx.__exit__(None, None, None)

    def test_it_answers_with_the_board_the_summary_and_the_shots(self):
        self.assertEqual(self.h.status, 200, self.h.body)
        body = self.h.body
        self.assertTrue(body["board_id"].startswith("sb_"))
        self.assertRegex(body["summary"], r"^\d+ shots · \d+ singing · ")
        self.assertEqual(len(body["shots"]), len(self.saved()["shots"]))

    def saved(self):
        return storyboard.load_storyboard(P.STATE_DIR, self.h.body["board_id"])

    def test_the_board_is_on_disk_and_valid(self):
        board = self.saved()
        self.assertEqual(storyboard.validate_storyboard(board), [])
        self.assertEqual(board["engine_mode"], "ltx")
        self.assertEqual(board["music_video"]["song"], str(self.fx.song))

    def test_the_policy_is_this_machine_s_saved_quality_not_the_module_default(self):
        # `new_storyboard` hands back storyboard.DEFAULT_POLICY, which ignores
        # this Mac's canvas cap — the same trap the planner route already fell
        # into once (the `setdefault("policy", …)` no-op).
        self.assertEqual(
            self.saved()["policy"],
            P._sb_policy_for(
                P.get_settings().get("storyboard_draft_quality", "quick"),
                P.get_settings().get("storyboard_final_quality", "standard")))

    def test_the_shot_rows_say_which_panel_mode_will_run(self):
        modes = {r["mode"] for r in self.h.body["shots"]}
        self.assertTrue(modes <= {"a2v", "i2v"}, modes)
        for row in self.h.body["shots"]:
            if row["mode"] == "a2v":
                self.assertIsNotNone(row["audio_start_time"])
                self.assertAlmostEqual(row["audio_start_time"],
                                       row["film_start"], places=3)

    def test_the_cells_come_from_the_panel_s_own_table(self):
        frames = {int(l["frames"]) for l in P.LTX_LENGTHS.values()}
        for row in self.h.body["shots"]:
            if row["mode"] == "a2v":
                self.assertIn(row["frames"], frames)


class TheVocalStemRidesOnTheSingingShots(unittest.TestCase):
    """A stem conditions the mouth; the film still plays the record."""

    def test_a_stem_from_outside_the_panel_is_refused_by_name(self):
        with _Fixture() as fx:
            h = _Handler(fx.form(vocal_stem=str(fx.outsider)))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 400)
            self.assertIn("outside.png", h.body["error"])
            self.assertIn("uploads", h.body["error"])

    def test_the_stem_reaches_every_singing_shot_and_nothing_else(self):
        with _Fixture() as fx:
            h = _Handler(fx.form(vocal_stem=str(fx.stem)))
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            self.assertEqual(h.body["vocal_stem"], str(fx.stem))
            board = storyboard.load_storyboard(P.STATE_DIR, h.body["board_id"])
            self.assertEqual(board["music_video"]["vocal_stem"], str(fx.stem))
            sang = 0
            for shot in board["shots"]:
                if shot["mode"] == "a2v":
                    self.assertEqual(shot["audio_stem"], str(fx.stem))
                    self.assertEqual(shot["audio"], str(fx.song))
                    sang += 1
                else:
                    self.assertNotIn("audio_stem", shot)
            self.assertTrue(sang)

    def test_no_stem_named_means_no_stem_anywhere(self):
        with _Fixture() as fx:
            h = _Handler(fx.form())
            R.post_music_video_plan(h, "/music/video/plan", {}, "")
            self.assertEqual(h.status, 200, h.body)
            self.assertEqual(h.body["vocal_stem"], "")
            board = storyboard.load_storyboard(P.STATE_DIR, h.body["board_id"])
            for shot in board["shots"]:
                self.assertNotIn("audio_stem", shot)


class TheFilmRoute(unittest.TestCase):

    def call(self, board_id, **over):
        h = _Handler({"board_id": board_id, **over})
        R.post_music_video_film(h, "/music/video/film", {}, "")
        return h

    def test_an_unknown_board_is_a_404(self):
        self.assertEqual(self.call("sb_nope").status, 404)

    def test_a_board_that_is_not_a_music_video_is_refused_with_the_reason(self):
        board = storyboard.new_storyboard("sb_mvtest_plain", "Plain film")
        storyboard.save_storyboard(P.STATE_DIR, board)
        h = self.call("sb_mvtest_plain")
        self.assertEqual(h.status, 409)
        self.assertIn("song", h.body["error"])

    def test_a_song_that_moved_is_a_404_not_a_silent_film(self):
        board = storyboard.new_storyboard("sb_mvtest_gone", "Gone")
        board["music_video"] = {"song": "/tmp/not-here.wav", "images": []}
        storyboard.save_storyboard(P.STATE_DIR, board)
        h = self.call("sb_mvtest_gone")
        self.assertEqual(h.status, 404)

    def test_the_song_replaces_every_clip_s_audio_and_the_cut_is_left_alone(self):
        # THE TWO THINGS THIS ROUTE EXISTS FOR. `replace` throws the a2v
        # clips' own audio away so the master is one continuous song; the
        # auto-editor stays OFF because the board is already cut to the grid
        # and `plan_cut` would walk every later shot off its own words.
        from unittest import mock
        with _Fixture() as fx:
            board = storyboard.new_storyboard("sb_mvtest_film", "A film")
            board["music_video"] = {"song": str(fx.song), "images": []}
            storyboard.save_storyboard(P.STATE_DIR, board)
            with mock.patch.object(P, "_sb_export",
                                   return_value={"ok": True}) as exp:
                self.call("sb_mvtest_film")
            kw = exp.call_args.kwargs
            self.assertEqual(kw["music"], str(fx.song))
            self.assertEqual(kw["music_mode"], "replace")
            self.assertFalse(kw["auto_edit"])

            with mock.patch.object(P, "_sb_export",
                                   return_value={"ok": True}) as exp:
                self.call("sb_mvtest_film", auto_edit="on")
            self.assertTrue(exp.call_args.kwargs["auto_edit"])


if __name__ == "__main__":
    unittest.main()
