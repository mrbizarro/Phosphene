#!/usr/bin/env python3
"""Tests for storyboard.py — the storyboard spine.

No GPU, no weights, no panel import. Everything here is the real module.

Run:  python3 -m unittest test_storyboard
"""

import json
import tempfile
import unittest
from pathlib import Path

import storyboard as sb


def _shot(n, **kw):
    s = {"n": n, "mode": "text", "engine": "h3", "prompt": f"shot {n} happens",
         "duration_s": 5.0, "seed": 1000 + n, "refs": [], "status": "pending"}
    s.update(kw)
    return s


def _board(shots, **kw):
    b = sb.new_storyboard("sb_test", "The Long Walk", shots=shots)
    b.update(kw)
    return b


class FrameGrid(unittest.TestCase):
    def test_ltx_grid_matches_docs_api(self):
        # docs/API.md publishes exactly this table.
        self.assertEqual(sb.ltx_frames_for(3), 73)
        self.assertEqual(sb.ltx_frames_for(5), 121)
        self.assertEqual(sb.ltx_frames_for(7), 169)
        self.assertEqual(sb.ltx_frames_for(10), 241)

    def test_ltx_grid_is_always_on_the_sampler_grid(self):
        for tenths in range(10, 601):
            f = sb.ltx_frames_for(tenths / 10.0)
            self.assertEqual(f % 8, 1, f"{tenths/10.0}s -> {f}")
            self.assertGreaterEqual(f, 9)

    def test_h3_length_snap(self):
        self.assertEqual(sb.h3_length_for(3), "3s")
        self.assertEqual(sb.h3_length_for(4), "3s")     # tie -> shorter
        self.assertEqual(sb.h3_length_for(5), "5s")
        self.assertEqual(sb.h3_length_for(7), "5s")
        self.assertEqual(sb.h3_length_for(10), "10s")
        self.assertEqual(sb.h3_length_for(14), "15s")


class ShotToJob(unittest.TestCase):
    POLICY = {"quality": "quick", "width": 640, "height": 480, "frames": 49}

    def test_engine_rides_through(self):
        j = sb.shot_to_job(_shot(1, engine="h3"), self.POLICY)
        self.assertEqual(j["engine"], "h3")
        j = sb.shot_to_job(_shot(1, engine="ltx"), self.POLICY)
        self.assertEqual(j["engine"], "ltx")

    def test_unknown_engine_falls_back_to_builtin(self):
        j = sb.shot_to_job(_shot(1, engine="sora"), self.POLICY)
        self.assertEqual(j["engine"], "ltx")

    def test_character_shot_is_forced_to_ltx(self):
        j = sb.shot_to_job(
            _shot(1, engine="h3", mode="character", character_id="bizarrotrn",
                  trigger="bizarrotrn"), self.POLICY)
        self.assertEqual(j["engine"], "ltx")
        self.assertEqual(j["character_id"], "bizarrotrn")

    def test_h3_unavailable_forces_ltx(self):
        j = sb.shot_to_job(_shot(1, engine="h3"), self.POLICY, h3_available=False)
        self.assertEqual(j["engine"], "ltx")

    def test_mode_is_translated_to_the_panels_vocabulary(self):
        # The panel has no backend mode called "text" or "character" — both are t2v.
        self.assertEqual(sb.shot_to_job(_shot(1, mode="text"), self.POLICY)["mode"], "t2v")
        self.assertEqual(
            sb.shot_to_job(_shot(1, mode="character", character_id="x", trigger="x"),
                           self.POLICY)["mode"], "t2v")

    def test_duration_drives_frames_on_the_ltx_grid(self):
        for d, f in ((3, 73), (5, 121), (7, 169), (10, 241)):
            j = sb.shot_to_job(_shot(1, engine="ltx", duration_s=d), self.POLICY)
            self.assertEqual(j["frames"], f, f"{d}s")

    def test_duration_drives_the_h3_cell(self):
        j = sb.shot_to_job(_shot(1, engine="h3", duration_s=10), self.POLICY)
        self.assertEqual(j["h3_length"], "10s")
        self.assertEqual(j["frames"], 243)
        self.assertEqual(j["h3_quality"], "draft")      # policy quality "quick"

    def test_h3_quality_tracks_the_pass(self):
        final = {"quality": "standard", "width": 1024, "height": 576, "frames": 121}
        j = sb.shot_to_job(_shot(1, engine="h3"), final)
        self.assertEqual(j["h3_quality"], "high")

    def test_queue_linkage(self):
        j = sb.shot_to_job(_shot(3), self.POLICY,
                           board_id="sb_20260811_a1b2", board_title="The Long Walk")
        self.assertEqual(j["session_tag"], "sb:sb_20260811_a1b2#3")
        self.assertEqual(j["preset_label"], "S03 · The Long Walk")

    def test_enhance_stays_off_and_the_viewer_is_not_stolen(self):
        j = sb.shot_to_job(_shot(1), self.POLICY)
        self.assertEqual(j["enhance"], "off")
        self.assertEqual(j["open_when_done"], "off")
        self.assertNotIn("auto_open", j)   # was never in make_job's allowlist

    def test_trigger_is_injected_mechanically(self):
        j = sb.shot_to_job(
            _shot(1, mode="character", character_id="bizarrotrn", trigger="bizarrotrn",
                  prompt="a man walks"), self.POLICY)
        self.assertTrue(j["prompt"].startswith("bizarrotrn "))

    def test_seed_rides_through(self):
        self.assertEqual(sb.shot_to_job(_shot(4), self.POLICY)["seed"], 1004)


class FilmLevelEngine(unittest.TestCase):
    """The film's own engine choice — the owner asked for it by name."""

    POLICY = {"quality": "quick", "width": 640, "height": 480, "frames": 49}

    def test_auto_keeps_what_the_plan_wrote(self):
        self.assertEqual(sb.resolve_engine(_shot(1, engine="h3"), engine_mode="auto"), "h3")
        self.assertEqual(sb.resolve_engine(_shot(1, engine="ltx"), engine_mode="auto"), "ltx")

    def test_forcing_h3_moves_an_ltx_shot(self):
        self.assertEqual(sb.resolve_engine(_shot(1, engine="ltx"), engine_mode="h3"), "h3")

    def test_forcing_ltx_moves_an_h3_shot(self):
        self.assertEqual(sb.resolve_engine(_shot(1, engine="h3"), engine_mode="ltx"), "ltx")

    def test_a_cast_shot_beats_the_film_setting(self):
        # H3 stacks no LoRAs; forcing a cast shot onto it renders a stranger.
        cast = _shot(1, engine="h3", mode="character", character_id="c", trigger="c")
        self.assertEqual(sb.resolve_engine(cast, engine_mode="h3"), "ltx")

    def test_a_missing_pack_beats_everything(self):
        self.assertEqual(
            sb.resolve_engine(_shot(1, engine="h3"), engine_mode="h3", h3_available=False),
            "ltx")

    def test_shot_to_job_honours_the_film_setting(self):
        j = sb.shot_to_job(_shot(1, engine="ltx"), self.POLICY, engine_mode="h3")
        self.assertEqual(j["engine"], "h3")
        self.assertIn("h3_length", j)
        j = sb.shot_to_job(_shot(1, engine="h3"), self.POLICY, engine_mode="ltx")
        self.assertEqual(j["engine"], "ltx")
        self.assertNotIn("h3_length", j)

    def test_unknown_mode_falls_back_to_auto(self):
        self.assertEqual(sb.resolve_engine(_shot(1, engine="h3"), engine_mode="nonsense"), "h3")


class H3ChainPrompts(unittest.TestCase):
    """A 10 s H3 clip is two 5 s windows; both were being asked for the same
    prompt, so the one-off action happened twice."""

    POLICY = {"quality": "quick", "width": 640, "height": 480, "frames": 49}

    def _shot10(self, **kw):
        s = _shot(1, engine="h3", duration_s=10,
                  settle="he stands empty-handed with his shoulders down",
                  soundscape="Wind across open sand, and one long exhale.")
        s.update(kw)
        return s

    def test_single_window_shapes_get_nothing(self):
        for d in (3, 5):
            self.assertEqual(sb.h3_chain_prompts_for(self._shot10(duration_s=d)), [])

    def test_ltx_gets_nothing(self):
        self.assertEqual(sb.h3_chain_prompts_for(self._shot10(engine="ltx")), [])

    def test_ten_seconds_gets_two_entries_first_blank(self):
        got = sb.h3_chain_prompts_for(self._shot10())
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0], "")          # "" == use the main prompt
        self.assertTrue(got[1].startswith("integrated_multimodal_description:"))

    def test_fifteen_seconds_gets_three(self):
        self.assertEqual(len(sb.h3_chain_prompts_for(self._shot10(duration_s=15))), 3)

    def test_the_continuation_holds_the_settled_state(self):
        tail = sb.h3_chain_prompts_for(self._shot10())[1]
        self.assertIn("he stands empty-handed with his shoulders down", tail)
        self.assertIn("without a cut", tail)
        self.assertIn("overall_soundscape:", tail)
        self.assertIn("non_diegetic_music:", tail)

    def test_no_settle_means_no_invention(self):
        # Nothing honest to say about how it continues -> today's behaviour.
        self.assertEqual(sb.h3_chain_prompts_for(self._shot10(settle="")), [])

    def test_shot_to_job_only_sends_it_when_the_runner_supports_it(self):
        s = self._shot10()
        self.assertNotIn("h3_chain_prompts", sb.shot_to_job(s, self.POLICY))
        j = sb.shot_to_job(s, self.POLICY, h3_chain_prompts=True)
        self.assertEqual(len(json.loads(j["h3_chain_prompts"])), 2)


class Bucketing(unittest.TestCase):
    def test_engine_is_part_of_the_bucket(self):
        self.assertNotEqual(sb.bucket_key(_shot(1, engine="h3")),
                            sb.bucket_key(_shot(2, engine="ltx")))

    def test_mixed_film_groups_by_engine_not_story_order(self):
        shots = [_shot(1, engine="h3"), _shot(2, engine="ltx", mode="character",
                                              character_id="c", trigger="c"),
                 _shot(3, engine="h3"), _shot(4, engine="ltx", mode="character",
                                              character_id="c", trigger="c")]
        order = [s["n"] for s in sb.shooting_order(shots)]
        self.assertEqual(order, [1, 3, 2, 4])

    def test_twelve_shots_one_engine_means_one_bucket(self):
        shots = [_shot(i, engine="h3") for i in range(1, 13)]
        est = sb.estimate(_board(shots), pass_name="draft")
        self.assertEqual(est["pipeline_loads"], 1)
        self.assertEqual([s["n"] for s in sb.shooting_order(shots)], list(range(1, 13)))

    def test_rendered_and_cut_are_excluded(self):
        # "Rendered" means THIS PASS produced a clip — not that `status` happens
        # to read "done". A shot whose status says done but which has no output
        # for this pass still has to be shot.
        shots = [_shot(1, status="done", draft_output="/tmp/s1.mp4"),
                 _shot(2, status="skipped"),
                 _shot(3),
                 _shot(4, status="done")]          # done, but nothing to show for it
        self.assertEqual([s["n"] for s in sb.shooting_order(shots)], [3, 4])


class TwoPasses(unittest.TestCase):
    """Draft then delivery. The release-blocker lived here: the scheduler asked
    the shot's single `status` field whether it was finished, and the reconciler
    set that field to "done" the moment the DRAFT landed — so the delivery pass
    saw an empty film and enqueued nothing, for every board, forever."""

    def _drafted(self):
        """A film whose draft pass has completed, exactly as reconcile leaves it."""
        return [_shot(n, status="done", draft_output=f"/tmp/s{n}.mp4",
                      draft_job_id=f"j-{n}") for n in (1, 2, 3)]

    def test_the_draft_pass_is_finished(self):
        self.assertEqual(sb.shooting_order(self._drafted(), "draft"), [])

    def test_the_delivery_pass_still_has_every_shot(self):
        got = sb.shooting_order(self._drafted(), "final")
        self.assertEqual([s["n"] for s in got], [1, 2, 3])

    def test_delivery_drops_a_shot_once_it_has_delivered(self):
        shots = self._drafted()
        shots[1]["final_output"] = "/tmp/s2_final.mp4"
        self.assertEqual([s["n"] for s in sb.shooting_order(shots, "final")], [1, 3])

    def test_cut_shots_are_out_of_both_passes(self):
        shots = self._drafted()
        shots[0]["status"] = "skipped"
        self.assertEqual([s["n"] for s in sb.shooting_order(shots, "final")], [2, 3])
        self.assertEqual(sb.shooting_order(shots, "draft"), [])

    def test_the_delivery_estimate_is_not_zero(self):
        board = _board(self._drafted())
        self.assertEqual(sb.estimate(board, pass_name="draft")["shots"], 0)
        self.assertEqual(sb.estimate(board, pass_name="final")["shots"], 3)
        self.assertGreater(sb.estimate(board, pass_name="final")["total_secs"], 0)

    def test_delivery_jobs_carry_the_delivery_policy(self):
        board = _board(self._drafted())
        policy = board["policy"]["final"]
        j = sb.shot_to_job(sb.shooting_order(board["shots"], "final")[0], policy)
        self.assertEqual(j["quality"], "balanced")
        self.assertEqual((j["width"], j["height"]), (1024, 576))

    def test_pass_helpers(self):
        s = {"draft_output": "/tmp/a.mp4"}
        self.assertTrue(sb.shot_pass_done(s, "draft"))
        self.assertFalse(sb.shot_pass_done(s, "final"))
        self.assertEqual(sb.pass_output_key("final"), "final_output")
        self.assertEqual(sb.pass_job_key("final"), "final_job_id")
        # An unknown pass name must not silently mean "final".
        self.assertEqual(sb.pass_output_key("nonsense"), "draft_output")

    def test_default_pass_is_draft_for_every_old_caller(self):
        self.assertEqual([s["n"] for s in sb.shooting_order(self._drafted())], [])


class Estimate(unittest.TestCase):
    def test_h3_is_not_priced_as_ltx(self):
        h3 = _board([_shot(1, engine="h3", duration_s=5)])
        ltx = _board([_shot(1, engine="ltx", duration_s=5)])
        h3["policy"]["final"]["quality"] = "standard"
        ltx["policy"]["final"]["quality"] = "standard"
        e_h3 = sb.estimate(h3)["render_secs"]
        e_ltx = sb.estimate(ltx)["render_secs"]
        self.assertGreater(e_h3, e_ltx * 1.8, "H3 was under-reported by ~2x before this")

    def test_the_panels_measured_hook_wins(self):
        board = _board([_shot(1, engine="h3", duration_s=5)])
        seen = []

        def cost(q, ln):
            seen.append((q, ln))
            return 510.0                       # 8.5 min, the measured high_5s turbo cell

        est = sb.estimate(board, h3_cost=cost)
        self.assertEqual(est["render_secs"], 510)
        self.assertEqual(seen, [("standard", "5s")])   # final pass default is "balanced"

    def test_h3_bucket_is_not_charged_a_pipeline_load(self):
        # An H3 job spawns its own process and loads its own weights every time; that cost is
        # already inside the measured per-clip eta.
        board = _board([_shot(1, engine="h3")])
        est = sb.estimate(board, h3_cost=lambda q, ln: 100.0)
        self.assertEqual(est["total_secs"], 100)

    def test_ltx_bucket_is_charged_once(self):
        board = _board([_shot(1, engine="ltx", duration_s=5), _shot(2, engine="ltx", duration_s=5)])
        board["policy"]["final"]["quality"] = "quick"     # 24 s per video-second
        est = sb.estimate(board)
        self.assertEqual(est["render_secs"], 240)
        self.assertEqual(est["total_secs"], 240 + 90)

    def test_engine_mix_and_buckets_are_reported(self):
        board = _board([_shot(1, engine="h3"), _shot(2, engine="ltx")])
        est = sb.estimate(board)
        self.assertEqual(est["engine_mix"], {"h3": 1, "ltx": 1})
        self.assertEqual([b["engine"] for b in est["buckets"]], ["h3", "ltx"])
        self.assertEqual(est["buckets"][0]["shots"], [1])

    def test_per_shot_estimate_covers_every_shot(self):
        board = _board([_shot(1), _shot(2), _shot(3)])
        per = sb.per_shot_estimate(board, pass_name="draft")
        self.assertEqual(sorted(per.keys()), ["1", "2", "3"])
        self.assertTrue(all(v > 0 for v in per.values()))


class ValidatorSplit(unittest.TestCase):
    """The messages must stay byte-identical — the whole point of the split."""

    def _cases(self):
        good = _board([_shot(1, mode="character", character_id="bizarrotrn",
                             trigger="bizarrotrn", prompt="bizarrotrn walks")])
        yield "clean", good, {}
        yield "schema", _board([_shot(1)], schema=2), {}
        yield "no_id", _board([_shot(1)], id=""), {}
        yield "no_shots", _board([]), {}
        yield "not_object", _board(["nope"]), {}
        bad_n = _shot(1); bad_n["n"] = "x"
        yield "bad_n", _board([bad_n]), {}
        yield "dupe_n", _board([_shot(1), _shot(1)]), {}
        yield "bad_mode", _board([_shot(1, mode="montage")]), {}
        yield "empty_prompt", _board([_shot(1, prompt="  ")]), {}
        yield ("unknown_char",
               _board([_shot(1, character_id="ariatrn", trigger="ariatrn",
                             prompt="ariatrn walks")]),
               {"known_character_ids": ["bizarrotrn"]})
        yield ("missing_trigger",
               _board([_shot(1, character_id="bizarrotrn", trigger="bizarrotrn")]),
               {"known_character_ids": ["bizarrotrn"]})
        yield "char_no_id", _board([_shot(1, mode="character")]), {}
        yield "bad_dur", _board([_shot(1, duration_s=90)]), {}
        yield "refs_not_list", _board([_shot(1, refs="x.png")]), {}
        yield "ref_missing", _board([_shot(1, refs=["/nope/desert.png"])]), {}
        yield "remix_no_ref", _board([_shot(1, mode="remix")]), {}
        yield "over_cap", _board([_shot(1)]), {"max_dim": 512}
        loc = sb.new_location("carwash", "The car wash", "a soapy sedan",
                              views=[sb.new_view("wide", "Establishing", "the sedan")])
        yield ("unknown_view",
               _board([dict(_shot(1), location_id="carwash", view="reverse")],
                      locations=[loc]), {})
        yield ("view_dupe",
               _board([_shot(1)], locations=[sb.new_location(
                   "carwash", "The car wash", "a soapy sedan",
                   views=[sb.new_view("wide", "A", "the sedan"),
                          sb.new_view("wide", "B", "the houses")])]), {})
        yield ("view_bad_id",
               _board([_shot(1)], locations=[sb.new_location(
                   "carwash", "The car wash", "a soapy sedan",
                   views=[{"id": "Not An Id!", "description": "x"}])]), {})
        yield ("view_empty",
               _board([_shot(1)], locations=[sb.new_location(
                   "carwash", "The car wash", "a soapy sedan",
                   views=[sb.new_view("wide", "A", "  ")])]), {})
        yield ("view_long",
               _board([_shot(1)], locations=[sb.new_location(
                   "carwash", "The car wash", "a soapy sedan",
                   views=[sb.new_view("wide", "A", "x" * 601)])]), {})
        yield ("views_shape",
               _board([_shot(1)], locations=[dict(sb.new_location(
                   "carwash", "The car wash", "a soapy sedan"), views="wide")]), {})
        yield "bad_eyeline", _board([dict(_shot(1), eyeline="frame-left")]), {}

    def test_detail_and_flat_agree_byte_for_byte(self):
        for name, board, kw in self._cases():
            flat = sb.validate_storyboard(board, **kw)
            detail = sb.validate_storyboard_detail(board, **kw)
            self.assertEqual(flat, [e["message"] for e in detail], name)

    def test_every_case_produces_the_expected_code(self):
        want = {
            "clean": [], "schema": ["schema_version"], "no_id": ["board_id_empty"],
            "no_shots": ["no_shots"], "not_object": ["shot_not_object"],
            "bad_n": ["shot_number"], "dupe_n": ["shot_duplicate"],
            "bad_mode": ["bad_mode"], "empty_prompt": ["empty_prompt"],
            "unknown_char": ["unknown_character"], "missing_trigger": ["missing_trigger"],
            "char_no_id": ["character_without_id"], "bad_dur": ["bad_duration"],
            "refs_not_list": ["refs_not_list"], "ref_missing": ["ref_missing"],
            "remix_no_ref": ["remix_needs_ref"],
            "over_cap": ["over_cap", "over_cap"],
            "unknown_view": ["unknown_view"], "view_dupe": ["view_duplicate"],
            "view_bad_id": ["view_id"], "view_empty": ["view_empty"],
            "view_long": ["view_too_long"], "views_shape": ["views_shape"],
            "bad_eyeline": ["bad_eyeline"],
        }
        for name, board, kw in self._cases():
            codes = [e["code"] for e in sb.validate_storyboard_detail(board, **kw)]
            self.assertEqual(codes, want[name], f"{name}: {codes}")

    def test_every_code_is_reachable(self):
        seen = set()
        for _, board, kw in self._cases():
            seen |= {e["code"] for e in sb.validate_storyboard_detail(board, **kw)}
        self.assertEqual(len(seen), 23, sorted(seen))

    def test_a_non_dict_shot_does_not_raise(self):
        # It used to: `where` was computed from s.get() before the isinstance check.
        errs = sb.validate_storyboard_detail(_board([{"n": 1}, "nope"]))
        self.assertIn("shot 2: not an object", [e["message"] for e in errs])

    def test_detail_entries_carry_the_shot_number(self):
        errs = sb.validate_storyboard_detail(_board([_shot(1), _shot(2, prompt="")]))
        empty = [e for e in errs if e["code"] == "empty_prompt"]
        self.assertEqual(len(empty), 1)
        self.assertEqual(empty[0]["n"], 2)
        self.assertEqual(empty[0]["field"], "prompt")

    def test_unknown_character_carries_the_installed_list(self):
        errs = sb.validate_storyboard_detail(
            _board([_shot(1, character_id="ariatrn", trigger="ariatrn",
                          prompt="ariatrn walks")]),
            known_character_ids=["bizarrotrn"])
        self.assertEqual(errs[0]["data"]["have"], ["bizarrotrn"])


class Persistence(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as d:
            board = _board([_shot(1)])
            sb.save_storyboard(Path(d), board)
            got = sb.load_storyboard(Path(d), "sb_test")
            self.assertEqual(got["shots"][0]["n"], 1)
            rows = sb.list_storyboards(Path(d))
            self.assertEqual(rows[0]["id"], "sb_test")
            self.assertEqual(rows[0]["shots"], 1)

    def test_additive_fields_survive_and_do_not_invalidate(self):
        board = _board([_shot(1, grade="keep", note="more light", draft_job_id="j-1")],
                       concept="a film", style="16mm", must=["the beam"], shots_target=6,
                       planner={"state": "done", "stage": "unload"})
        self.assertEqual(sb.validate_storyboard(board), [])
        with tempfile.TemporaryDirectory() as d:
            sb.save_storyboard(Path(d), board)
            got = json.loads((Path(d) / "storyboards" / "sb_test" / "storyboard.json")
                             .read_text())
        self.assertEqual(got["concept"], "a film")
        self.assertEqual(got["shots"][0]["grade"], "keep")
        self.assertEqual(got["schema"], 1)      # never bumped for additive fields


class DialogueFitsTheShot(unittest.TestCase):
    """A line must fit its clock and must close.

    Owner, after the same truncation twice in one day: "when people talk,
    there is a structure to the talk... a beginning and an end. It's not just
    open-ended sentences that get cut." The budget constants are measured: a
    7-word line in 4.04s delivered; a 20-word line in 7.04s was cut mid-phrase.
    """

    def test_the_line_that_actually_got_cut_is_caught(self):
        # Verbatim from the shot the owner watched truncate.
        p = ("She says slowly and low: 'Update the app. The new timeline "
             "editor is in. Cut your shots, move them around, put music "
             "under your voice.'")
        prob = sb.shot_pacing_problem(p, 7.04)
        self.assertIsNotNone(prob)
        self.assertIn("cut off", prob)

    def test_the_same_line_fits_when_the_shot_is_long_enough(self):
        # SLOW read: 23 words at 1.7 w/s needs ~14.5s — 13.04 was measured to
        # truncate, 15.04 fits.
        p = ("She says slowly and low: 'Update the app. There is a new "
             "timeline editor. Cut your shots. Move them around. Put music "
             "under your voice. That is it.'")
        self.assertIsNotNone(sb.shot_pacing_problem(p, 13.04))
        self.assertIsNone(sb.shot_pacing_problem(p, 15.04))

    def test_a_warm_read_is_budgeted_slower_than_a_bright_one(self):
        # The same 9 words truncate under-the-breath and deliver fine bright:
        # both halves observed on real renders the same day.
        line = "'Update the app. There is a timeline editor now.'"
        self.assertIsNotNone(sb.shot_pacing_problem(
            "She says quietly, almost under her breath: " + line, 5.04))
        self.assertIsNone(sb.shot_pacing_problem(
            "She says brightly and clearly: " + line, 5.04))

    def test_the_lines_that_delivered_fine_still_pass(self):
        for line, dur in [("'Ladies and gentlemen — version four point six.'", 4.04),
                          ("'Is it any good?'", 4.04),
                          ("'It is buggy. But it works.'", 4.04),
                          ("'Ship it.'", 3.04)]:
            self.assertIsNone(sb.shot_pacing_problem("He says: " + line, dur), line)

    def test_a_line_that_trails_off_is_unfinished(self):
        p = "He says: 'And another thing about the update,'"
        prob = sb.shot_pacing_problem(p, 8.04)
        self.assertIsNotNone(prob)
        self.assertIn("never finishes", prob)

    def test_a_silent_shot_has_no_pacing_problem(self):
        self.assertIsNone(sb.shot_pacing_problem(
            "ariatrn scrubs the door with a foam-loaded sponge", 4.04))

    def test_contractions_do_not_end_the_count_early(self):
        # A quote flanked by letters is an apostrophe, not the end of the
        # line — without that rule "There's" stops the counter at word four
        # and every long line after a contraction sails through unfitted.
        spans = sb.spoken_spans("She says: 'There's a new editor and it does "
                                "a great many things you will surely enjoy.'")
        self.assertEqual(len(spans), 1)
        self.assertGreater(len(spans[0].split()), 10)

    def test_speech_fit_frames_is_a_legal_frame_count(self):
        for words in (2, 7, 23, 40):
            for slow in (False, True):
                f = sb.speech_fit_frames(words, slow=slow)
                self.assertEqual((f - 1) % 24, 0, f)
                lead = "He says slowly: '" if slow else "He says: '"
                self.assertIsNone(sb.shot_pacing_problem(
                    lead + " ".join(["word"] * (words - 1)) + " end.'",
                    f / 24.0))

    def test_the_board_validator_blocks_an_overstuffed_shot(self):
        board = _board([dict(_shot(1),
                             prompt="He says: 'twenty words of dialogue that "
                                    "can not possibly be spoken aloud inside "
                                    "four seconds of screen time at all.'",
                             mode="text", character_id=None, trigger=None,
                             duration_s=4.0)])
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("dialogue_does_not_fit", codes)


class TrainedVoiceActuallyLoads(unittest.TestCase):
    """`no_voice` gates the trained voice LoRA, and it was engine-blind.

    The gate read `_HAS_DIALOGUE`, which matches `<d>…</d>` and nothing else,
    under a comment calling itself "a derivation, not a heuristic". True of H3.
    False of LTX, where `_strip_h3_markup` has already turned the tag into
    'single quotes' before the prompt exists — so the tag was never there,
    `no_voice` was always "on", and the voice LoRA was stripped from every LTX
    character shot with a line in it.

    Confirmed on a real sidecar: `no_voice: true` with a one-entry LoRA stack
    holding only the face. The owner reported it as the characters never using
    their own voices, more than once.
    """
    POLICY = {"quality": "balanced", "width": 1024, "height": 576, "frames": 121}

    def _job(self, prompt):
        return sb.shot_to_job(
            {"n": 1, "mode": "character", "character_id": "bizarrotrn",
             "trigger": "bizarrotrn", "prompt": prompt}, self.POLICY)

    def test_an_ltx_line_in_single_quotes_loads_the_voice(self):
        j = self._job("bizarrotrn announces in a showman's voice: "
                      "'Ladies and gentlemen.'")
        self.assertEqual(j["no_voice"], "off")

    def test_an_h3_line_in_a_tag_still_loads_the_voice(self):
        j = self._job("A man says: <d>[English] They said this was impossible.</d>")
        self.assertEqual(j["no_voice"], "off")

    def test_a_silent_shot_still_leaves_the_voice_off(self):
        # The other half of the contract: a voice LoRA loaded onto a shot with
        # nothing to say is the babble this gate exists to prevent.
        j = self._job("ariatrn a woman scrubs the door with a foam-loaded sponge")
        self.assertEqual(j["no_voice"], "on")

    def test_an_empty_tag_is_not_a_line(self):
        self.assertEqual(self._job("A man stands there. <d></d>")["no_voice"], "on")

    def test_the_voice_gate_and_the_speech_law_use_the_SAME_detector(self):
        # If they ever disagree you get one of the two bugs: a mouth with
        # nothing to say, or a line delivered by a stranger's voice.
        for p in ("bizarrotrn says: 'Ship it.'",
                  "A man says: <d>[English] Ship it.</d>",
                  "ariatrn scrubs the door in silence"):
            speaks_law = sb.shot_speech_problem(p) is None and bool(
                sb._SPOKEN_WORDS_RE.search(p))
            speaks_gate = self._job(p)["no_voice"] == "off"
            self.assertEqual(speaks_law, speaks_gate, p)


class Wardrobe(unittest.TestCase):
    """The same outfit in every shot, for the same reason as the same room.

    Four shots of one man came back in a navy suit, a navy suit, a period
    collar and a different period collar. "A man in a dark suit" is a different
    suit each time it is re-rolled.
    """
    CAST = [{"id": "bizarrotrn", "trigger": "bizarrotrn",
             "wardrobe": "a loud floral Hawaiian shirt with the collar open"}]

    def test_the_outfit_reaches_the_rendered_prompt(self):
        w = sb.board_wardrobe({"cast": self.CAST})
        got = sb.compose_shot_prompt(
            {"n": 1, "character_id": "bizarrotrn", "trigger": "bizarrotrn",
             "prompt": "a man stands square to the camera"}, {}, w)
        self.assertIn("Hawaiian", got)

    def test_it_lands_on_the_person_before_the_frame_and_the_room(self):
        w = sb.board_wardrobe({"cast": self.CAST})
        locs = {"carwash": {"id": "carwash", "description": "a sunlit driveway"}}
        got = sb.compose_shot_prompt(
            {"n": 1, "character_id": "bizarrotrn", "trigger": "bizarrotrn",
             "location_id": "carwash", "prompt": "a man stands there",
             "framing": "medium shot"}, locs, w)
        self.assertLess(got.index("Hawaiian"), got.index("medium shot"))
        self.assertLess(got.index("medium shot"), got.index("sunlit driveway"))

    def test_a_shot_that_already_says_it_does_not_get_it_twice(self):
        w = sb.board_wardrobe({"cast": self.CAST})
        got = sb.compose_shot_prompt(
            {"n": 1, "character_id": "bizarrotrn", "trigger": "bizarrotrn",
             "prompt": "a loud floral Hawaiian shirt with the collar open, he waves"}, {}, w)
        self.assertEqual(got.lower().count("hawaiian"), 1)

    def test_no_wardrobe_on_the_board_changes_nothing(self):
        shot = {"n": 1, "character_id": "bizarrotrn", "trigger": "bizarrotrn",
                "prompt": "a man stands there"}
        self.assertEqual(sb.compose_shot_prompt(shot, {}, {}),
                         sb.compose_shot_prompt(shot, {}))

    def test_it_only_applies_to_the_character_it_belongs_to(self):
        w = sb.board_wardrobe({"cast": self.CAST})
        got = sb.compose_shot_prompt(
            {"n": 1, "character_id": "ariatrn", "trigger": "ariatrn",
             "prompt": "a woman washes a car"}, {}, w)
        self.assertNotIn("Hawaiian", got)

    def test_shot_to_job_carries_it(self):
        w = sb.board_wardrobe({"cast": self.CAST})
        job = sb.shot_to_job(
            {"n": 1, "mode": "character", "character_id": "bizarrotrn",
             "trigger": "bizarrotrn", "prompt": "a man waves"},
            {"quality": "balanced", "width": 1024, "height": 576, "frames": 121},
            wardrobe=w)
        self.assertIn("Hawaiian", job["prompt"])


class H3ThreeFieldsCompose(unittest.TestCase):
    """The room, the frame and the eyeline land INSIDE the H3 description.

    An H3 shot's prompt is the three-field form. Appending the composed parts
    to the whole prompt put them after `non_diegetic_music: N/A` — a located
    sitcom shot rendered with the apartment in its score (2026-09-08).
    """
    H3 = ("integrated_multimodal_description: [Shot 1] Live-action, a man says: "
          "<d>[English] Hi.</d> His mouth settles closed. No text appears at any point."
          "\n\noverall_soundscape: Room tone.\n\nnon_diegetic_music: N/A")
    LOCS = {"apt": {"id": "apt", "description": "a sitcom apartment"}}

    def test_the_additions_go_into_the_description(self):
        got = sb.compose_shot_prompt(
            {"n": 1, "prompt": self.H3, "location_id": "apt", "framing": "medium shot",
             "eyeline": "right", "pronoun": "he", "engine": "h3"}, self.LOCS)
        head, tail = got.split("\n\noverall_soundscape:")
        self.assertIn("medium shot", head)
        self.assertIn("a sitcom apartment", head)
        self.assertIn("his eyes fixed past the right edge of frame", head)
        self.assertTrue(head.endswith("."), head[-40:])
        self.assertEqual(tail, " Room tone.\n\nnon_diegetic_music: N/A")
        self.assertNotIn("apartment", tail)

    def test_nothing_to_add_leaves_the_prompt_byte_identical(self):
        self.assertEqual(sb.compose_shot_prompt({"n": 1, "prompt": self.H3}, {}), self.H3)

    def test_a_prose_prompt_composes_exactly_as_before(self):
        got = sb.compose_shot_prompt(
            {"n": 1, "prompt": "a man waves.", "location_id": "apt",
             "framing": "medium shot"}, self.LOCS)
        self.assertEqual(got, "a man waves., medium shot, a sitcom apartment")

    def test_split_h3_fields_round_trips(self):
        head, tail = sb.split_h3_fields(self.H3)
        self.assertEqual(head + tail, self.H3)
        self.assertEqual(sb.split_h3_fields("plain prose"), ("plain prose", ""))


class SpeechLawAtBoardLevel(unittest.TestCase):
    """No mouth moves without words, whoever wrote the shot.

    The planner has enforced this since the owner first said "talking
    gibberish". It ran INSIDE the planner, so a hand-authored board walked
    straight past it and the owner reported gibberish a second time. These pin
    the law at the level every shot passes through.

    The BAD strings below are verbatim from the board that actually produced
    the second gibberish clip, and the GOOD ones from the board that fixed it.
    """
    BAD = ("bizarrotrn a man in a dark navy suit sits upright and addresses the camera",
           "ariatrn a woman pushes her wet hair back and announces something to the lens",
           "bizarrotrn a man asks a dry follow-up question",
           "a weary man explains the situation to the room")

    GOOD = ("bizarrotrn announces in a big brassy showman's voice: "
            "'Ladies and gentlemen — version four point six.'",
            "ariatrn says brightly and clearly: 'Update the app. "
            "There is a timeline editor now.'",
            "A man on a dune ridge says: <d>[English] They said this was impossible.</d>")

    SILENT = ("ariatrn scrubs slow circles across the door with a foam-loaded sponge",
              "Tight on a sponge pressed against the panel, foam bulging and water running",
              "bizarrotrn walks to the window and looks out at the rain without a word")

    def test_it_catches_the_prompts_that_actually_babbled(self):
        for p in self.BAD:
            self.assertIsNotNone(sb.shot_speech_problem(p), f"missed: {p!r}")

    def test_a_written_line_passes_in_either_wrapper(self):
        # H3 keeps <d>...</d>; LTX has already been converted to single quotes
        # by the time the prompt is on a board. Both are "the words are there".
        for p in self.GOOD:
            self.assertIsNone(sb.shot_speech_problem(p), f"false positive: {p!r}")

    def test_an_honestly_silent_shot_is_not_flagged(self):
        for p in self.SILENT:
            self.assertIsNone(sb.shot_speech_problem(p), f"false positive: {p!r}")

    def test_a_briefing_room_is_a_room(self):
        # The planner's version of this law paid for this false positive once
        # already, firing three times on "a dimly lit briefing room".
        self.assertIsNone(sb.shot_speech_problem(
            "a dimly lit briefing room, empty chairs, a map still on the table"))

    def test_gesturing_is_not_speaking(self):
        # Under-reach on purpose. A shot can hand over with a look and stay
        # silent, and this check blocks renders — it may not guess.
        self.assertIsNone(sb.shot_speech_problem(
            "bizarrotrn turns and gestures to someone off-screen, one eyebrow raised"))

    def test_the_validator_blocks_a_board_that_would_babble(self):
        board = _board([dict(_shot(1), prompt="bizarrotrn addresses the camera",
                             mode="text", character_id=None, trigger=None)])
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("speech_without_words", codes)

    def test_the_error_says_what_to_do_about_it(self):
        board = _board([dict(_shot(1), prompt="bizarrotrn addresses the camera",
                             mode="text", character_id=None, trigger=None)])
        msg = next(e["message"] for e in sb.validate_storyboard_detail(board)
                   if e["code"] == "speech_without_words")
        self.assertIn("Write the line", msg)


class Locations(unittest.TestCase):
    """The same room in every shot that claims to be in it.

    Four Bizarro shots that each said "dim room, cinematic close-up" rendered
    as four different rooms — one of them a vintage parlour with no monitors in
    it at all, with the collar changing between them. Nobody wrote a
    contradiction; the shots simply never agreed, and what a prompt leaves
    unstated gets re-rolled per shot.
    """
    LOC = {"id": "study", "name": "The study",
           "description": "a dark oak-panelled study, three curved monitors "
                          "glowing cold blue, a brass desk lamp"}

    def test_the_location_reaches_the_rendered_prompt(self):
        shot = {"n": 1, "trigger": "bizarrotrn", "prompt": "speaks to camera",
                "location_id": "study"}
        got = sb.compose_shot_prompt(shot, {"study": self.LOC})
        self.assertIn("oak-panelled", got)
        self.assertTrue(got.startswith("bizarrotrn speaks to camera"),
                        "subject and action come first; the room is appended")

    def test_the_framing_lands_between_the_action_and_the_room(self):
        # Order is not cosmetic: leading with scenery buries the action.
        shot = {"n": 1, "trigger": "x", "prompt": "leans on the car",
                "framing": "wide shot, full body, clear space around her",
                "location_id": "study"}
        got = sb.compose_shot_prompt(shot, {"study": self.LOC})
        self.assertLess(got.index("leans on the car"), got.index("wide shot"))
        self.assertLess(got.index("wide shot"), got.index("oak-panelled"))

    def test_a_shot_with_no_location_is_completely_unchanged(self):
        # Every board written before locations existed has none.
        shot = {"n": 1, "trigger": "x", "prompt": "does a thing"}
        self.assertEqual(sb.compose_shot_prompt(shot, {}), "x does a thing")
        self.assertEqual(sb.compose_shot_prompt(shot, None), "x does a thing")

    def test_shot_to_job_injects_it_because_that_is_the_only_choke_point(self):
        # Every render path goes through shot_to_job. Composing at the call
        # sites instead would give each of them its own chance to forget.
        shot = {"n": 1, "mode": "character", "trigger": "bizarrotrn",
                "prompt": "speaks", "location_id": "study",
                "framing": "medium close-up"}
        job = sb.shot_to_job(shot, {"quality": "balanced", "width": 1024,
                                    "height": 576, "frames": 121},
                             locations={"study": self.LOC})
        self.assertIn("oak-panelled", job["prompt"])
        self.assertIn("medium close-up", job["prompt"])
        self.assertIn("bizarrotrn", job["prompt"])

    def test_a_shot_naming_a_location_the_board_lacks_is_an_error(self):
        # It would render with NO room injected and look exactly like a shot
        # that never claimed one — the continuity failure arriving silently.
        board = _board([dict(_shot(1), location_id="nowhere")])
        board["locations"] = [self.LOC]
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("unknown_location", codes)

    def test_a_location_with_no_description_is_an_error(self):
        board = _board([_shot(1)])
        board["locations"] = [{"id": "study", "name": "The study", "description": "  "}]
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("location_empty", codes)

    def test_duplicate_and_malformed_location_ids_are_caught(self):
        board = _board([_shot(1)])
        board["locations"] = [self.LOC, dict(self.LOC), {"id": "Not An Id!", "description": "x"}]
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("location_duplicate", codes)
        self.assertIn("location_id", codes)

    def test_a_board_with_locations_still_validates(self):
        board = _board([dict(_shot(1), location_id="study")])
        board["locations"] = [self.LOC]
        self.assertEqual(sb.validate_storyboard(board), [])


class LocationViews(unittest.TestCase):
    """THE CAR WASH — the acceptance benchmark, on the deterministic half.

    A whole day of manual continuity work on `sb_carwash` produced exactly one
    paragraph of floor plan: a man presents at a soapy car, a woman washes it,
    houses across the street, a low sun. From that paragraph the establishing
    angle and the reverse angle are DERIVABLE, and the two things that make the
    cut work are the two things a single location description cannot express —
    the reverse does not contain the car, and the sun that raked in from camera
    LEFT rakes in from camera RIGHT once the camera turns 180 degrees.

    The floor plan itself is a model's job. Everything below it — which
    description reaches the prompt, which side the eyes go, what happens to a
    board that has no views at all — is arithmetic, and this is where it is
    pinned.
    """
    CARWASH = sb.new_location(
        "carwash", "The car wash",
        "a suburban driveway on a bright afternoon",
        views=[
            sb.new_view("establishing", "Establishing — facing the driveway",
                        "a soapy blue sedan on the driveway, a woman crouched at the "
                        "front wheel with a sponge, the low sun raking in from camera "
                        "left"),
            sb.new_view("reverse", "Reverse — facing the street",
                        "the row of low houses across the street, a hand-painted sign "
                        "on the far verge, no car in frame, the low sun raking in from "
                        "camera right"),
        ])
    LOCS = {"carwash": CARWASH}

    def _shot(self, view, **kw):
        s = {"n": 1, "trigger": "bizarrotrn", "prompt": "throws both arms wide",
             "location_id": "carwash", "view": view}
        s.update(kw)
        return s

    # ---- the benchmark ------------------------------------------------------
    def test_the_establishing_view_has_the_car_and_the_woman(self):
        got = sb.compose_shot_prompt(self._shot("establishing"), self.LOCS)
        self.assertIn("soapy blue sedan", got)
        self.assertIn("woman", got)
        self.assertIn("camera left", got)

    def test_the_reverse_view_has_the_houses_and_no_car(self):
        got = sb.compose_shot_prompt(self._shot("reverse"), self.LOCS)
        self.assertIn("houses across the street", got)
        self.assertNotIn("soapy blue sedan", got)
        self.assertNotIn("driveway", got)

    def test_the_light_flips_sides_at_180_degrees(self):
        # Measured on the real cut: a camera-left sun is a camera-right sun
        # turned around. Both descriptions carry it, and neither carries both.
        front = sb.compose_shot_prompt(self._shot("establishing"), self.LOCS)
        back = sb.compose_shot_prompt(self._shot("reverse"), self.LOCS)
        self.assertIn("camera left", front)
        self.assertNotIn("camera right", front)
        self.assertIn("camera right", back)
        self.assertNotIn("camera left", back)

    def test_the_eyeline_pair_is_complementary(self):
        him = sb.compose_shot_prompt(
            self._shot("establishing", eyeline="right", pronoun="he"), self.LOCS)
        her = sb.compose_shot_prompt(
            dict(self._shot("reverse", eyeline="left", pronoun="she"),
                 trigger="ariatrn", prompt="keeps scrubbing"), self.LOCS)
        self.assertIn("his eyes fixed past the right edge of frame", him)
        self.assertIn("her eyes fixed past the left edge of frame", her)
        self.assertEqual(sb.eyeline_complement("right"), "left")
        self.assertEqual(sb.eyeline_complement("left"), "right")
        self.assertEqual(sb.eyeline_complement("lens"), "")

    # ---- the composition contract ------------------------------------------
    def test_the_eyeline_lands_with_the_framing_not_with_the_room(self):
        # Where a person looks is a fact about the FRAME. Behind the scenery it
        # reads as a note about the street.
        got = sb.compose_shot_prompt(
            self._shot("establishing", framing="medium close-up", eyeline="right"),
            self.LOCS)
        self.assertLess(got.index("throws both arms wide"), got.index("medium close-up"))
        self.assertLess(got.index("medium close-up"), got.index("eyes fixed"))
        self.assertLess(got.index("eyes fixed"), got.index("soapy blue sedan"))

    def test_looking_down_the_lens_says_nothing(self):
        # These models point a piece to camera at the lens unprompted; a
        # sentence ordering it buys a stiffer performance for nothing.
        got = sb.compose_shot_prompt(self._shot("establishing", eyeline="lens"), self.LOCS)
        self.assertNotIn("eyes fixed", got)
        self.assertEqual(sb.eyeline_clause("lens"), "")
        self.assertEqual(sb.eyeline_clause(None), "")

    def test_an_eyeline_with_no_pronoun_is_still_renderable(self):
        self.assertEqual(sb.eyeline_clause("left"), "eyes fixed past the left edge of frame")

    # ---- back-compat: a location with no views is exactly what it was -------
    def test_a_location_with_no_views_behaves_as_it_always_did(self):
        loc = sb.new_location("study", "The study", "a dark oak-panelled study")
        self.assertNotIn("views", loc)
        shot = {"n": 1, "trigger": "x", "prompt": "speaks", "location_id": "study"}
        self.assertEqual(sb.compose_shot_prompt(shot, {"study": loc}),
                         "x speaks, a dark oak-panelled study")

    def test_a_shot_that_names_no_view_gets_the_location_description(self):
        shot = {"n": 1, "trigger": "x", "prompt": "speaks", "location_id": "carwash"}
        got = sb.compose_shot_prompt(shot, self.LOCS)
        self.assertIn("suburban driveway on a bright afternoon", got)
        self.assertNotIn("soapy blue sedan", got)

    def test_a_view_with_no_description_falls_back_rather_than_vanishing(self):
        # The validator calls this out (view_empty); compose still has to
        # produce a prompt, and a prompt with no place at all is worse.
        loc = sb.new_location("x", "X", "a place", views=[{"id": "v", "description": ""}])
        shot = {"n": 1, "trigger": "t", "prompt": "does a thing",
                "location_id": "x", "view": "v"}
        self.assertIn("a place", sb.compose_shot_prompt(shot, {"x": loc}))

    # ---- the render choke point --------------------------------------------
    def test_shot_to_job_carries_the_view_and_the_eyeline(self):
        shot = {"n": 1, "mode": "character", "trigger": "bizarrotrn",
                "prompt": "throws both arms wide", "location_id": "carwash",
                "view": "reverse", "eyeline": "right", "pronoun": "he"}
        job = sb.shot_to_job(shot, {"quality": "balanced", "width": 1024,
                                    "height": 576, "frames": 121},
                             locations=self.LOCS)
        self.assertIn("houses across the street", job["prompt"])
        self.assertIn("his eyes fixed past the right edge of frame", job["prompt"])
        self.assertNotIn("soapy blue sedan", job["prompt"])

    # ---- the schema ---------------------------------------------------------
    def test_helpers_read_the_shape_the_planner_writes(self):
        self.assertEqual(sorted(sb.location_views(self.CARWASH)),
                         ["establishing", "reverse"])
        self.assertEqual(sb.location_views(None), {})
        self.assertEqual(sb.location_views({"id": "x"}), {})
        self.assertEqual(
            sb.shot_view(self._shot("reverse"), self.LOCS)["name"],
            "Reverse — facing the street")
        self.assertIsNone(sb.shot_view(self._shot("nope"), self.LOCS))
        self.assertIsNone(sb.shot_view({"n": 1}, self.LOCS))

    def test_a_board_with_views_still_validates(self):
        board = _board([dict(_shot(1), location_id="carwash", view="reverse",
                             eyeline="left")], locations=[self.CARWASH])
        self.assertEqual(sb.validate_storyboard(board), [])

    def test_every_eyeline_in_the_vocabulary_is_legal(self):
        for eye in sb.EYELINES:
            board = _board([dict(_shot(1), eyeline=eye)])
            self.assertEqual(sb.validate_storyboard(board), [], eye)

    def test_a_view_on_a_shot_with_no_location_is_an_error(self):
        board = _board([dict(_shot(1), view="reverse")], locations=[self.CARWASH])
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertIn("unknown_view", codes)

    def test_the_error_names_the_views_that_do_exist(self):
        board = _board([dict(_shot(1), location_id="carwash", view="behind")],
                       locations=[self.CARWASH])
        row = next(e for e in sb.validate_storyboard_detail(board)
                   if e["code"] == "unknown_view")
        self.assertEqual(row["data"]["known"], ["establishing", "reverse"])
        self.assertEqual(row["field"], "view")

    # ---- adoption: the plan's views onto the user's own locations ----------
    def test_the_plans_views_land_on_the_users_own_words(self):
        # The board row is the Locations box, typed by a human. Only `views`
        # comes from the model.
        mine = [sb.new_location("carwash", "The car wash", "MY OWN WORDS")]
        merged = sb.merge_location_views(mine, [self.CARWASH])
        self.assertEqual(merged[0]["description"], "MY OWN WORDS")
        self.assertEqual([v["id"] for v in merged[0]["views"]],
                         ["establishing", "reverse"])
        self.assertNotIn("views", mine[0], "the board's own list was mutated")

    def test_a_plan_with_no_views_does_not_strip_the_ones_on_the_board(self):
        # A re-plan with geography off, or one whose floor plan came back as
        # chatter. The shots still name these views and an unknown view id is
        # a hard error.
        merged = sb.merge_location_views(
            [self.CARWASH], [sb.new_location("carwash", "The car wash", "x")])
        self.assertEqual([v["id"] for v in merged[0]["views"]],
                         ["establishing", "reverse"])

    def test_a_location_the_board_lacks_is_appended_not_dropped(self):
        merged = sb.merge_location_views([], [self.CARWASH])
        self.assertEqual([l["id"] for l in merged], ["carwash"])

    def test_merging_nothing_into_nothing_is_empty(self):
        self.assertEqual(sb.merge_location_views(None, None), [])
        self.assertEqual(sb.merge_location_views([{"name": "no id"}], None), [])

    def test_a_broken_view_is_reported_once_not_on_every_shot(self):
        # A view whose description is empty is still a view that EXISTS. Adding
        # `unknown_view` to every shot that names it buries the one line worth
        # reading under the shot list.
        loc = sb.new_location("x", "X", "a place",
                              views=[sb.new_view("v", "V", "   ")])
        board = _board([dict(_shot(1), location_id="x", view="v"),
                        dict(_shot(2), location_id="x", view="v")],
                       locations=[loc])
        codes = [e["code"] for e in sb.validate_storyboard_detail(board)]
        self.assertEqual(codes, ["view_empty"])


if __name__ == "__main__":
    unittest.main()
