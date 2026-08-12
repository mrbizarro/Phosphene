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
        }
        for name, board, kw in self._cases():
            codes = [e["code"] for e in sb.validate_storyboard_detail(board, **kw)]
            self.assertEqual(codes, want[name], f"{name}: {codes}")

    def test_all_sixteen_codes_are_reachable(self):
        seen = set()
        for _, board, kw in self._cases():
            seen |= {e["code"] for e in sb.validate_storyboard_detail(board, **kw)}
        self.assertEqual(len(seen), 16, sorted(seen))

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


if __name__ == "__main__":
    unittest.main()
