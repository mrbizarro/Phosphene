#!/usr/bin/env python3
"""Tests for storyboard_planner.py.

Two layers, deliberately separated:

  * The STUB layer (default, no GPU, no weights, runs anywhere in <1 s) drives the whole
    extraction / coercion / repair / validation pipeline through a fake PlannerSession that
    replays canned model output. Every ugly thing a 4-bit 12B has actually been observed to
    do — fenced JSON, prose around the object, a whole three-field prompt pasted into one
    key, a bad character name, an out-of-range duration, truncation mid-object — is a test
    case here rather than a surprise in front of a user.

  * The LIVE layer (opt-in) loads the real planner model, plans a fixed 6-shot concept,
    asserts it validates, and prints the measured plan-phase RSS. It is skipped unless
    PLANNER_LIVE=1 so `python3 -m unittest test_storyboard_planner` stays instant on a
    machine with no weights.

    PLANNER_LIVE=1 python3.11 -m unittest test_storyboard_planner -v
"""

from __future__ import annotations

import copy
import re
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import storyboard  # noqa: E402
import storyboard_planner as P  # noqa: E402


# --------------------------------------------------------------------------------------
# Stub model
# --------------------------------------------------------------------------------------

class StubSession(object):
    """Stands in for PlannerSession. Replays canned replies, records what it was asked."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []
        self.model_path = Path("stub-model")
        self.stats = {"model_path": "stub-model", "load_s": 0.0, "calls": 0,
                      "gen_s_total": 0.0, "prompt_tokens": 0, "output_tokens": 0,
                      "peak_rss_bytes": 0, "mx_peak_bytes": 0, "released": False}
        self.released = False

    def generate(self, system, user, **kw):
        self.calls.append({"system": system, "user": user, "kw": kw})
        self.stats["calls"] += 1
        if not self.replies:
            raise AssertionError("stub ran out of replies after %d calls" % len(self.calls))
        nxt = self.replies.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"text": nxt, "load_s": 0.0, "gen_s": 0.0,
                "prompt_tokens": 10, "output_tokens": 10,
                "peak_rss_bytes": 0, "mx_peak_bytes": 0}

    def release(self):
        self.released = True
        self.stats["released"] = True
        return self.stats

    unload = release


def _shot(n, desc=None, **kw):
    """One shot in the shape the model is asked for: description WITHOUT the camera
    sentence and WITHOUT the ending — those are the `camera` and `settle` keys."""
    d = {
        "n": n,
        "title": "Beat %d" % n,
        "character_id": None,
        "duration_s": 5,
        "camera": "static",
        "description": desc or (
            "Live-action, cinematic, a close-up of a brass key lying on a wet stone step. "
            "Rain beads on the metal and one drop runs off the bow of the key."),
        "settle": "the key lies still and wet",
        "soundscape": "Steady rain on stone, a distant gutter running, no voices.",
        "music": "N/A",
    }
    d.update(kw)
    return d


def _plan_json(n=6, **kw):
    return json.dumps({"title": "The Key", "shots": [_shot(i + 1, **kw) for i in range(n)]})


class _StubFactory(object):
    """Replaces PlannerSession so plan_film() OWNS the stub — which means the release
    path under test is the same one production takes."""

    def __init__(self, stub):
        self.stub = stub

    def __call__(self, **kw):
        return self.stub


def _plan(replies, **kw):
    """plan_film() against a stub, with the arguments the panel would pass.

    The stub is installed as the PlannerSession class rather than handed in as
    `session=`, so plan_film() owns it and its `finally:` release is exercised.
    """
    stub = StubSession(replies)
    kw.setdefault("concept", "a lost brass key finds its door")
    kw.setdefault("n_shots", 6)
    concept = kw.pop("concept")
    real = P.PlannerSession
    P.PlannerSession = _StubFactory(stub)
    try:
        out = P.plan_film(concept, **kw)
    finally:
        P.PlannerSession = real
    return out, stub


# --------------------------------------------------------------------------------------

class TestJSONExtraction(unittest.TestCase):
    def test_clean_object(self):
        self.assertEqual(P.extract_json_object('{"title":"x","shots":[]}')["title"], "x")

    def test_fenced(self):
        raw = "Sure!\n```json\n{\"title\":\"x\",\"shots\":[]}\n```\nLet me know."
        self.assertEqual(P.extract_json_object(raw)["title"], "x")

    def test_bare_fence_and_trailing_prose(self):
        raw = "```\n{\"title\":\"y\",\"shots\":[]}\n```\n\nHope that helps!"
        self.assertEqual(P.extract_json_object(raw)["title"], "y")

    def test_prose_before_and_after_unfenced(self):
        raw = 'Here is the plan:\n{"title":"z","shots":[{"n":1}]}\nWant changes?'
        self.assertEqual(P.extract_json_object(raw)["title"], "z")

    def test_think_block_is_ignored(self):
        raw = '<think>{"title":"WRONG","shots":[]}</think>{"title":"right","shots":[]}'
        self.assertEqual(P.extract_json_object(raw)["title"], "right")

    def test_trailing_commas_and_smart_quotes(self):
        raw = '{“title”: “q”, “shots”: [1,2,],}'
        self.assertEqual(P.extract_json_object(raw)["title"], "q")

    def test_object_wrapped_in_list(self):
        self.assertEqual(P.extract_json_object('[{"title":"L","shots":[]}]')["title"], "L")

    def test_truncated_output_is_rescued(self):
        raw = '{"title":"T","shots":[{"n":1,"description":"a key on a step'
        got = P.extract_json_object(raw)
        self.assertIsNotNone(got)
        self.assertEqual(got["title"], "T")

    def test_braces_inside_strings_do_not_confuse_the_scanner(self):
        raw = '{"title":"a { brace } inside","shots":[]}'
        self.assertEqual(P.extract_json_object(raw)["title"], "a { brace } inside")

    def test_no_json_at_all(self):
        self.assertIsNone(P.extract_json_object("I'm sorry, I can't help with that."))


class TestThreeFieldSplit(unittest.TestCase):
    def test_pasted_assembled_prompt_is_taken_apart(self):
        blob = ("integrated_multimodal_description: [Shot 1] Live-action, cinematic, a key.\n\n"
                "overall_soundscape: Rain on stone.\n\n"
                "non_diegetic_music: N/A")
        d, s, m = P._split_three_fields(blob)
        self.assertTrue(d.startswith("[Shot 1] Live-action"))
        self.assertEqual(s, "Rain on stone.")
        self.assertEqual(m, "N/A")
        self.assertNotIn("overall_soundscape", d)
        self.assertNotIn("non_diegetic_music", d)

    def test_plain_description_passes_through(self):
        d, s, m = P._split_three_fields("Live-action, cinematic, a key.")
        self.assertEqual(d, "Live-action, cinematic, a key.")
        self.assertEqual((s, m), ("", ""))


class TestCoercion(unittest.TestCase):
    def setUp(self):
        self.kw = dict(concept="a lost brass key", n_shots=3, storyboard_mod=storyboard)

    def test_assembles_the_h3_three_field_dialect(self):
        spec, _ = P.coerce_spec(json.loads(_plan_json(3)), **self.kw)
        p = spec["shots"][0]["prompt"]
        self.assertTrue(p.startswith("integrated_multimodal_description: [Shot 1] "))
        self.assertIn("\n\noverall_soundscape: ", p)
        self.assertIn("\n\nnon_diegetic_music: ", p)
        self.assertEqual(spec["shots"][0]["engine"], "h3")
        self.assertEqual(spec["shots"][0]["tier"], "draft")

    def test_shot_marker_is_never_doubled(self):
        raw = json.loads(_plan_json(1))
        raw["shots"][0]["description"] = "[Shot 1] Live-action, cinematic, a key."
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        self.assertEqual(spec["shots"][0]["prompt"].count("[Shot 1]"), 1)

    def test_renumbers_and_deduplicates_shot_numbers(self):
        raw = {"title": "t", "shots": [_shot(4), _shot(4), _shot(9)]}
        spec, _ = P.coerce_spec(raw, **self.kw)
        self.assertEqual([s["n"] for s in spec["shots"]], [1, 2, 3])

    def test_durations_are_clamped_and_snapped(self):
        raw = {"title": "t", "shots": [_shot(1, duration_s=999), _shot(2, duration_s=-4),
                                       _shot(3, duration_s="8")]}
        spec, _ = P.coerce_spec(raw, **self.kw)
        got = [s["duration_s"] for s in spec["shots"]]
        self.assertEqual(got, [15.0, 5.0, 10.0])
        for d in got:
            self.assertTrue(0 < d <= 60)

    def test_seeds_are_assigned_and_deterministic(self):
        a, _ = P.coerce_spec(json.loads(_plan_json(3)), **self.kw)
        b, _ = P.coerce_spec(json.loads(_plan_json(3)), **self.kw)
        self.assertEqual([s["seed"] for s in a["shots"]], [s["seed"] for s in b["shots"]])
        self.assertEqual(len({s["seed"] for s in a["shots"]}), 3)

    def test_character_shot_gets_ltx_engine_mode_and_trigger(self):
        raw = {"title": "t", "shots": [_shot(1, character_id="bizarrotrn")]}
        spec, _ = P.coerce_spec(raw, cast=P._normalise_cast(["bizarrotrn"]),
                                **dict(self.kw, n_shots=1))
        s = spec["shots"][0]
        self.assertEqual(s["engine"], "ltx")
        self.assertEqual(s["mode"], "character")
        self.assertEqual(s["character_id"], "bizarrotrn")
        self.assertIn("bizarrotrn", s["prompt"])
        self.assertNotIn("integrated_multimodal_description", s["prompt"])
        self.assertEqual(spec["cast"][0]["id"], "bizarrotrn")

    def test_unknown_character_is_dropped_not_passed_to_the_validator(self):
        raw = {"title": "t", "shots": [_shot(1, character_id="someone_who_does_not_exist")]}
        spec, warns = P.coerce_spec(raw, cast=P._normalise_cast(["bizarrotrn"]),
                                    **dict(self.kw, n_shots=1))
        self.assertIsNone(spec["shots"][0].get("character_id"))
        self.assertEqual(spec["shots"][0]["mode"], "text")
        self.assertTrue(any("unknown character" in w for w in warns))

    def test_ltx_register_strips_h3_markup(self):
        raw = {"title": "t", "shots": [_shot(
            1, character_id="bizarrotrn",
            description="[Shot 1] A man on a step. He (S1) says: <d>[English] Found it.</d>")]}
        spec, _ = P.coerce_spec(raw, cast=P._normalise_cast(["bizarrotrn"]),
                                style="documentary realism, no letterbox",
                                **dict(self.kw, n_shots=1))
        p = spec["shots"][0]["prompt"]
        for junk in ("<d>", "</d>", "[Shot 1]", "(S1)"):
            self.assertNotIn(junk, p)
        self.assertIn("'Found it.'", p)
        self.assertIn("documentary realism, no letterbox", p)
        self.assertIn("Audio:", p)

    def test_camera_choice_becomes_a_canonical_camera_sentence(self):
        raw = {"title": "t", "shots": [_shot(1, camera="push_in", description="A key."),
                                       _shot(2, camera="nonsense", description="A door."),
                                       _shot(3, camera="dolly_in", description="A step.")]}
        spec, _ = P.coerce_spec(raw, **self.kw)
        self.assertIn("pushes in with small amplitude at slow speed", spec["shots"][0]["prompt"])
        self.assertIn("holds a static shot", spec["shots"][1]["prompt"])   # unknown -> static
        self.assertIn("pushes in with small amplitude", spec["shots"][2]["prompt"])  # alias

    def test_stored_camera_is_the_key_that_actually_rendered(self):
        # Observed: the model answered the `face` enum in the `camera` slot, and the shot
        # card then read `cam=medium` while the prompt said "holds a static shot".
        raw = {"title": "t", "shots": [_shot(1, camera="medium"), _shot(2, camera="dolly_in")]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=2))
        self.assertEqual([s["camera"] for s in spec["shots"]], ["static", "push_in"])
        self.assertIn("holds a static shot", spec["shots"][0]["prompt"])
        self.assertTrue(any("not one of" in w for w in warns), warns)
        for s in spec["shots"]:
            self.assertIn(s["camera"], P.CAMERA_KEYS)

    def test_a_camera_sentence_the_model_wrote_is_not_duplicated(self):
        raw = {"title": "t", "shots": [_shot(
            1, camera="push_in",
            description="A key. The camera holds a static shot and never moves.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        self.assertEqual(spec["shots"][0]["prompt"].lower().count("the camera"), 1)

    def test_settle_phrase_becomes_the_end_state_law(self):
        raw = {"title": "t", "shots": [_shot(1, description="A key.",
                                             settle="the key lies still and wet")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        p = spec["shots"][0]["prompt"]
        self.assertIn("completely finished before the shot ends", p)
        self.assertIn("for the last two seconds the key lies still and wet", p)

    def test_face_law_is_added_only_when_a_person_is_in_the_shot(self):
        raw = {"title": "t", "shots": [
            _shot(1, description="Live-action, cinematic, a woman on a step."),
            _shot(2, description="Live-action, cinematic, an empty steel dumpster.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=2))
        self.assertIn("holds the exact angle to the lens", spec["shots"][0]["prompt"])
        self.assertNotIn("holds the exact angle to the lens", spec["shots"][1]["prompt"])

    # --- the face law -------------------------------------------------------------
    # Faces are the quality metric. A live plan wrote "his face obscured by the angle" and
    # the render put the head half out of frame; a sweep of 56 shots found 5 face-hiding
    # phrases, mostly "silhouetted against the ..." on the final shot.

    def test_face_level_renders_the_right_law(self):
        raw = {"title": "t", "shots": [
            _shot(1, face="close", description="A close-up of a boxer's face."),
            _shot(2, face="medium", description="A woman at a workbench."),
            _shot(3, face="none", description="An empty steel dumpster.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=3))
        self.assertIn("The face fills much of the frame", spec["shots"][0]["prompt"])
        self.assertIn("holds the exact angle to the lens", spec["shots"][1]["prompt"])
        self.assertNotIn("fills much of the frame", spec["shots"][1]["prompt"])
        self.assertNotIn("holds the exact angle", spec["shots"][2]["prompt"])
        self.assertEqual([s["face"] for s in spec["shots"]], ["close", "medium", "none"])

    def test_face_level_defaults_from_whether_a_person_is_on_screen(self):
        raw = {"title": "t", "shots": [
            _shot(1, face=None, description="Live-action, cinematic, a woman on a step."),
            _shot(2, face=None, description="Live-action, cinematic, an empty dumpster.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=2))
        self.assertEqual([s["face"] for s in spec["shots"]], ["medium", "none"])

    def test_face_none_cannot_switch_off_the_law_when_a_person_is_present(self):
        """The escape that shipped a silhouette: the model labelled a wide shot containing a
        woman `face: "none"`, which disabled the scrub."""
        raw = {"title": "t", "shots": [
            _shot(1, face="none",
                  description=("Live-action, cinematic, a wide shot from the rooftop showing "
                               "the woman standing beside the neon sign, silhouetted against "
                               "the vibrant lights of the market below."),
                  settle="the market glows below and she stands silhouetted against the lights"),
            _shot(2, face="none", description="Live-action, cinematic, an empty rooftop.")]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=2))
        self.assertEqual(spec["shots"][0]["face"], "medium")
        self.assertEqual(spec["shots"][1]["face"], "none")   # genuinely no person: honoured
        body = spec["shots"][0]["prompt"].replace(P._FACE_LAW_MEDIUM, " ")
        self.assertNotIn("silhouetted", body)
        self.assertTrue(any("said no face" in w for w in warns), warns)

    def test_person_silhouette_regex_catches_the_forms_that_shipped(self):
        for blocking in ("silhouetted against the vibrant lights of the market below",
                         "and she stands silhouetted against the lights",
                         "He's silhouetted against the bright light of the lens",
                         "her silhouette framed against the backdrop of the city lights",
                         "they stood silhouetted in the doorway"):
            self.assertTrue(P._PERSON_SILHOUETTE_RE.search(blocking), blocking)
        for fine in ("the dune line behind him is a clean dark silhouette against a pale sky",
                     "the lighthouse stands silhouetted against the night sky",
                     "the crane is a hard silhouette on the skyline"):
            self.assertFalse(P._PERSON_SILHOUETTE_RE.search(fine), fine)

    def test_hidden_faces_are_refused_unless_the_brief_asked(self):
        raw = {"title": "t", "shots": [_shot(1, face="hidden",
                                             description="A woman on a rooftop.")]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        self.assertEqual(spec["shots"][0]["face"], "medium")
        self.assertIn("holds the exact angle", spec["shots"][0]["prompt"])
        self.assertTrue(any("kept visible" in w for w in warns), warns)

    def test_hidden_faces_are_allowed_when_the_brief_asked(self):
        raw = {"title": "t", "shots": [_shot(1, face="hidden",
                                             description="A woman on a rooftop.")]}
        spec, warns = P.coerce_spec(raw, allow_hidden_faces=True, **dict(self.kw, n_shots=1))
        self.assertEqual(spec["shots"][0]["face"], "hidden")
        self.assertNotIn("holds the exact angle", spec["shots"][0]["prompt"])
        self.assertEqual([w for w in warns if "kept visible" in w], [])

    def test_face_blocking_prose_is_scrubbed_out(self):
        # Every one of these is verbatim from a real plan, or the reported defect.
        raw = {"title": "t", "shots": [
            _shot(1, description=("Live-action, cinematic, a close-up of a boxer on a stool, "
                                  "his face obscured by the angle, sweat on his shoulders. "
                                  "He breathes out slowly.")),
            _shot(2, description=("Live-action, cinematic, a wide shot of the keeper at the "
                                  "window. He stands very still."),
                  settle=("he is standing at the window, silhouetted against the setting "
                          "sun, watching the light sweep out")),
            _shot(3, description=("Live-action, cinematic, a medium shot of a woman on a "
                                  "rooftop, her silhouette framed against the city lights. "
                                  "She sets down her tools.")),
            _shot(4, description=("Live-action, cinematic, a violinist seen from behind, "
                                  "her bow arm rising. She draws one long note."))]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=4))
        joined = " ".join(s["prompt"] for s in spec["shots"])
        for banned in ("obscured", "silhouetted against", "her silhouette", "seen from behind"):
            self.assertNotIn(banned, joined, "%r survived the scrub" % banned)
        # The rest of each direction survives — this is a clause scrub, not a shot delete.
        self.assertEqual(len(spec["shots"]), 4)
        self.assertIn("sweat on his shoulders", spec["shots"][0]["prompt"])
        self.assertIn("watching the light sweep out", spec["shots"][1]["prompt"])
        self.assertIn("She sets down her tools", spec["shots"][2]["prompt"])
        self.assertIn("her bow arm rising", spec["shots"][3]["prompt"])
        self.assertEqual(len([w for w in warns if "face-hiding framing" in w]), 4, warns)

    def test_scrub_leaves_legitimate_non_person_silhouettes_alone(self):
        # From the C1 exemplar: a landscape silhouette is good cinematography, not a
        # hidden face. Same for a face half in shadow.
        desc = ("Live-action, cinematic, a medium close-up of a man on a dune ridge. Hard "
                "low sun rakes from camera left, carving one bright edge down his cheekbone "
                "while the other side of his face falls into open shadow; the dune line "
                "behind him is a clean dark silhouette against a pale sky.")
        out, removed = P._scrub_face_blocking(desc)
        self.assertEqual(removed, [])
        self.assertIn("clean dark silhouette against a pale sky", out)
        self.assertIn("falls into open shadow", out)

    def test_scrub_does_not_fire_on_ordinary_behind(self):
        desc = "A daughter stands behind her mother, the crowd close behind him."
        out, removed = P._scrub_face_blocking(desc)
        self.assertEqual(removed, [])
        self.assertEqual(out, desc)

    def test_brief_detection_for_hidden_faces(self):
        for yes in ("a film told entirely in silhouette",
                    "we only ever see her from behind",
                    "a faceless narrator",
                    "shot without showing his face"):
            self.assertTrue(P._WANTS_HIDDEN_RE.search(yes), yes)
        for no in ("a boxer between rounds, close on the face",
                   "a lighthouse keeper on his last night",
                   "the dune line behind him is a dark shape"):
            self.assertFalse(P._WANTS_HIDDEN_RE.search(no), no)

    def test_curly_punctuation_is_normalised_out_of_the_prompt(self):
        raw = {"title": "t", "shots": [_shot(
            1, description="A key — he says “you’ve got it”…",
            soundscape="Rain — steady.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        p = spec["shots"][0]["prompt"]
        for ch in ("’", "“", "”", "—", "…"):
            self.assertNotIn(ch, p)

    def test_no_text_clause_is_omitted_when_typography_was_requested(self):
        raw = {"title": "t", "shots": [
            _shot(1, description='A wall where the word "PHOSPHENE" burns in.'),
            _shot(2, description="A plain wall."),
            # Observed failure: the model used single quotes and the refusal landed on a
            # title sequence whose whole point was the lettering.
            _shot(3, description="Mercury coalescing into the letter 'P' on black glass."),
            _shot(4, description="The mercury's surface stays perfectly smooth.")]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=4))
        self.assertNotIn("No text appears", spec["shots"][0]["prompt"])
        self.assertIn("No text appears", spec["shots"][1]["prompt"])
        self.assertNotIn("No text appears", spec["shots"][2]["prompt"])
        # An apostrophe is not a quoted string.
        self.assertIn("No text appears", spec["shots"][3]["prompt"])
        self.assertTrue(any("double quotes" in w for w in warns), warns)

    def test_typography_detector_does_not_fire_on_dialogue_or_props(self):
        # Both were real false positives: single-quoted LTX dialogue, and "neon sign" —
        # where the refusal is exactly what the shot needs.
        dialogue = ("A man at a workbench. He says, 'Everything has a story, you know. "
                    "And a purpose. Even if it's broken.'")
        neon = "A woman repairing a large crimson neon sign on a rooftop."
        self.assertEqual(P._typography_strings(dialogue), [])
        self.assertEqual(P._typography_strings(neon), [])
        self.assertEqual(P._typography_strings("the word \"PHOSPHENE\" burns in"),
                         ["PHOSPHENE"])
        self.assertEqual(P._typography_strings("the letter 'P' forms"), ["P"])
        spec, warns = P.coerce_spec(
            {"title": "t", "shots": [_shot(1, description=dialogue), _shot(2, description=neon)]},
            **dict(self.kw, n_shots=2))
        for s in spec["shots"]:
            self.assertIn("No text appears", s["prompt"])
        self.assertEqual([w for w in warns if "double quotes" in w], [])

    def test_camera_talk_is_stripped_out_of_the_settle_clause(self):
        raw = {"title": "t", "shots": [
            _shot(1, settle="the camera stops orbiting, the lens keeps turning silently"),
            _shot(2, settle="the camera holds on the scene")]}
        spec, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=2))
        p1, p2 = spec["shots"][0]["prompt"], spec["shots"][1]["prompt"]
        self.assertIn("for the last two seconds the lens keeps turning silently", p1)
        self.assertNotIn("the camera stops orbiting", p1)
        # Nothing but camera talk -> no settle clause at all, rather than a contradiction.
        self.assertNotIn("completely finished before the shot ends", p2)
        self.assertTrue(any("instead of an end state" in w for w in warns), warns)

    def test_camera_monoculture_is_reported_as_a_warning(self):
        raw = {"title": "t", "shots": [_shot(i + 1, camera="static", settle="it is still")
                                       for i in range(4)]}
        _, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=4))
        self.assertTrue(any("same camera behaviour" in w for w in warns), warns)

    def test_missing_end_states_are_reported_as_a_warning(self):
        raw = {"title": "t", "shots": [_shot(i + 1, settle="") for i in range(4)]}
        _, warns = P.coerce_spec(raw, **dict(self.kw, n_shots=4))
        self.assertTrue(any("no shot named an end state" in w for w in warns), warns)

    def test_unbalanced_dialogue_tag_is_closed(self):
        raw = {"title": "t", "shots": [_shot(1, description="A key. <d>[English] Hello.")]}
        spec, _ = P.coerce_spec(raw, **dict(self.kw, n_shots=1))
        p = spec["shots"][0]["prompt"]
        self.assertEqual(p.count("<d>"), p.count("</d>"))

    def test_empty_description_shot_is_dropped(self):
        raw = {"title": "t", "shots": [_shot(1), _shot(2, description=""), _shot(3)]}
        spec, warns = P.coerce_spec(raw, **self.kw)
        self.assertEqual(len(spec["shots"]), 2)
        self.assertTrue(any("empty description" in w for w in warns))

    def test_policy_is_clamped_to_the_machine_cap(self):
        spec, _ = P.coerce_spec(json.loads(_plan_json(3)), max_dim=768, **self.kw)
        for key in ("draft", "final"):
            p = spec["policy"][key]
            self.assertLessEqual(max(p["width"], p["height"]), 768)

    def test_garbage_input_still_produces_a_legal_envelope(self):
        spec, warns = P.coerce_spec(None, **self.kw)
        self.assertEqual(spec["schema"], storyboard.SCHEMA_VERSION)
        self.assertTrue(spec["id"])
        self.assertEqual(spec["shots"], [])
        self.assertTrue(warns)


class TestValidatorContract(unittest.TestCase):
    """The point of the module: what comes out passes the REAL validator."""

    def test_stub_plan_validates_clean(self):
        out, stub = _plan([_plan_json(6)])
        self.assertFalse(P.is_plan_error(out), out.get("error"))
        self.assertEqual(storyboard.validate_storyboard(out), [])
        self.assertEqual(len(out["shots"]), 6)
        self.assertTrue(out["_planner"]["first_try_clean"])
        self.assertEqual(out["_planner"]["attempts"], 1)
        self.assertTrue(stub.released)

    def test_plan_survives_a_roundtrip_through_save_and_load(self):
        out, _ = _plan([_plan_json(6)])
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            storyboard.save_storyboard(Path(td), out)
            back = storyboard.load_storyboard(Path(td), out["id"])
        self.assertEqual(storyboard.validate_storyboard(back), [])
        self.assertEqual(back["shots"][0]["prompt"], out["shots"][0]["prompt"])

    def test_plan_translates_to_panel_jobs(self):
        out, _ = _plan([_plan_json(6)])
        shot = out["shots"][0]
        job = storyboard.shot_to_job(shot, out["policy"]["draft"],
                                     board_id=out["id"], board_title=out["title"])
        self.assertEqual(job["enhance"], "off")
        self.assertTrue(job["prompt"])
        # The PANEL's mode vocabulary, not the storyboard schema's: mlx_ltx_panel has one
        # backend video mode for both of v1's shot types, and "text"/"character" are not it.
        self.assertIn(job["mode"], ("t2v", "i2v", "keyframe", "extend", "a2v"))
        # The engine the planner assigned must survive the translation — this is the seam
        # that used to send every H3 shot to LTX.
        self.assertEqual(job["engine"], shot["engine"])
        self.assertEqual(job["session_tag"], "sb:%s#%d" % (out["id"], shot["n"]))

    def test_character_plan_validates_with_known_ids(self):
        raw = json.dumps({"title": "t", "shots": [
            _shot(1, character_id="bizarrotrn"), _shot(2), _shot(3)]})
        out, _ = _plan([raw], n_shots=3, characters=["bizarrotrn"])
        self.assertFalse(P.is_plan_error(out), out.get("error"))
        self.assertEqual(storyboard.validate_storyboard(
            out, known_character_ids=["bizarrotrn"]), [])
        self.assertEqual(out["_planner"]["engine_mix"], {"ltx": 1, "h3": 2})


class TestRepairRoundTrip(unittest.TestCase):
    def test_wrong_shot_count_triggers_exactly_one_repair(self):
        out, stub = _plan([_plan_json(4), _plan_json(6)])
        self.assertFalse(P.is_plan_error(out))
        self.assertEqual(len(out["shots"]), 6)
        self.assertEqual(len(stub.calls), 2)
        self.assertEqual(out["_planner"]["attempts"], 2)
        self.assertIn("exactly 6 were requested", stub.calls[1]["user"])

    def test_unparseable_first_reply_is_repaired(self):
        out, stub = _plan(["I cannot do that.", _plan_json(6)])
        self.assertFalse(P.is_plan_error(out))
        self.assertEqual(len(out["shots"]), 6)
        self.assertIn("did not contain a JSON object", stub.calls[1]["user"])

    def test_two_bad_replies_return_a_structured_error_not_a_traceback(self):
        out, stub = _plan(["nope", "still nope"])
        self.assertTrue(P.is_plan_error(out))
        err = out["error"]
        self.assertEqual(err["kind"], "invalid_plan")
        self.assertTrue(err["message"])
        self.assertTrue(err["problems"])
        self.assertNotIn("Traceback", json.dumps(out))
        self.assertEqual(len(stub.calls), 2)   # never more than one repair
        self.assertTrue(stub.released)

    def test_a_worse_repair_does_not_replace_a_better_first_draft(self):
        # First draft: 5 shots (count wrong, otherwise valid). Repair: unusable.
        out, _ = _plan([_plan_json(5), "{}"])
        self.assertFalse(P.is_plan_error(out))
        self.assertEqual(len(out["shots"]), 5)
        self.assertFalse(out["_planner"]["shot_count_ok"])

    def test_model_unavailable_is_a_structured_error(self):
        out, _ = _plan([P.PlannerError("planner model not found at /nowhere")])
        self.assertTrue(P.is_plan_error(out))
        self.assertEqual(out["error"]["kind"], "model_unavailable")
        self.assertIn("mlx-lm", out["error"]["hint"])


class TestFeedback(unittest.TestCase):
    def setUp(self):
        self.base, _ = _plan([_plan_json(6)])

    def test_feedback_string_is_parsed_into_a_shot_reroll(self):
        self.assertEqual(P._parse_feedback("shot 4: he should not turn his head"),
                         ("shot", 4, "he should not turn his head"))
        self.assertEqual(P._parse_feedback({"shot": 2, "note": "colder"}),
                         ("shot", 2, "colder"))
        self.assertEqual(P._parse_feedback("make it colder")[0], "film")
        self.assertEqual(P._parse_feedback(None)[0], "none")

    def test_shot_reroll_changes_only_that_shot_and_leaves_the_rest_byte_stable(self):
        before = copy.deepcopy(self.base)
        reply = json.dumps({"title": "The Key", "shots": [_shot(
            4, description=(
                "Live-action, cinematic, a wide shot of a green door in a brick wall. "
                "The camera holds a static shot, the frame never moves. Rain runs down the "
                "paint and the letterbox flap swings once and stops. The movement is "
                "completely finished before the shot ends, and for the last two seconds the "
                "door is simply shut and streaming, with no new movement of any kind."))]})
        out, _ = _plan([reply], feedback="shot 4: make it a door, not a key",
                       previous=before, n_shots=6)
        self.assertFalse(P.is_plan_error(out), out.get("error"))
        self.assertEqual(storyboard.validate_storyboard(out), [])
        self.assertEqual(len(out["shots"]), 6)
        self.assertIn("green door", out["shots"][3]["prompt"])
        self.assertEqual(out["shots"][3]["n"], 4)
        for i in (0, 1, 2, 4, 5):
            self.assertEqual(json.dumps(out["shots"][i], sort_keys=True),
                             json.dumps(before["shots"][i], sort_keys=True),
                             "shot %d drifted during a per-shot re-roll" % (i + 1))

    def test_shot_reroll_prompt_carries_the_neighbours_but_asks_for_one_shot(self):
        _, stub = _plan([json.dumps({"title": "T", "shots": [_shot(4)]})],
                        feedback={"shot": 4, "note": "colder"},
                        previous=copy.deepcopy(self.base), n_shots=6)
        user = stub.calls[0]["user"]
        self.assertIn("re-rolling ONE shot", user)
        self.assertIn("n=4", user)
        self.assertIn("One shot only", user)

    def test_film_feedback_replans_everything(self):
        _, stub = _plan([_plan_json(6)], feedback="make it colder and drop the voiceover",
                        previous=copy.deepcopy(self.base), n_shots=6)
        user = stub.calls[0]["user"]
        self.assertIn("DIRECTOR'S NOTES", user)
        self.assertIn("make it colder", user)

    def test_feedback_without_previous_is_a_programmer_error(self):
        with self.assertRaises(P.PlannerError):
            _plan([_plan_json(6)], feedback="shot 2: colder")


class TestPromptContent(unittest.TestCase):
    """The prompt IS the product. If a law silently falls out, a test should notice."""

    def test_system_prompt_carries_the_laws_and_the_dialect(self):
        sys_p = P._build_system_prompt("auto", False)
        for needle in ("integrated_multimodal_description", "overall_soundscape",
                       "non_diegetic_music", "<d>[English]", "holds a static shot",
                       "completely finished before the shot ends", "no negative prompt",
                       "Heads stay square to the lens", "VARIETY IS A HARD REQUIREMENT"):
            self.assertIn(needle, sys_p, "missing from the system prompt: %r" % needle)

    def test_contract_lists_every_camera_key_the_coercer_accepts(self):
        sys_p = P._build_system_prompt("auto", False)
        for key in P.CAMERA_KEYS:
            self.assertIn(key, sys_p, "camera %r is legal but never offered to the model" % key)

    def test_face_law_is_in_the_prompt_and_hidden_is_not_offered_by_default(self):
        sys_p = P._build_system_prompt("auto", False)
        self.assertIn("L11 THE FACE IS THE WHOLE POINT", sys_p)
        self.assertIn('"face"         ONE of: close, medium, none.', sys_p)
        self.assertIn("There is no fourth option", sys_p)
        self.assertNotIn('you may use "hidden"', sys_p)
        relaxed = P._build_system_prompt("auto", False, allow_hidden=True)
        self.assertIn('you may use "hidden"', relaxed)

    def test_no_exemplar_teaches_the_model_to_hide_a_face(self):
        """The audit that caught it: the box exemplar used to say 'he tips his face up to
        the sky' and hold it there in the settle — a face aimed away from the lens, taught
        by example. The LTX exemplar sent the eyes off-camera on a talking head."""
        sys_p = P._build_system_prompt("auto", True)
        # Strip L11 itself, which necessarily quotes the phrases it forbids.
        body = sys_p.split("L11 THE FACE IS THE WHOLE POINT")[0]
        for phrase in ("tips his face up", "tipped up", "off-camera", "obscur",
                       "from behind", "seen from behind", "back to the camera"):
            self.assertNotIn(phrase, body, "exemplars still teach %r" % phrase)
        self.assertEqual(P._FACE_BLOCK_RE.findall(body), [])
        # Every exemplar declares a face level.
        self.assertEqual(re.findall(r'"face": "(\w+)"', sys_p),
                         ["close", "medium", "close", "none", "close"])

    def test_ltx_example_appears_only_when_there_is_a_cast(self):
        self.assertNotIn("letterbox", P._build_system_prompt("auto", False))
        self.assertIn("letterbox", P._build_system_prompt("auto", True))

    def test_forcing_ltx_drops_the_h3_dialect(self):
        sys_p = P._build_system_prompt("ltx", True)
        self.assertNotIn("integrated_multimodal_description:", sys_p.split("EVERY shot")[-1])
        self.assertIn("LTX register", sys_p)

    def test_user_prompt_lists_cast_and_must_include(self):
        u = P._build_user_prompt("a key", 6, "documentary", P._normalise_cast(["bizarrotrn"]),
                                 ["a green door"])
        self.assertIn("bizarrotrn", u)
        self.assertIn("a green door", u)
        self.assertIn("exactly 6 shots", u)


class TestMemoryDiscipline(unittest.TestCase):
    def test_release_is_idempotent_and_safe_before_spawn(self):
        s = P.PlannerSession(model_path="/definitely/not/here")
        self.assertTrue(s.release()["released"])
        self.assertTrue(s.release()["released"])

    def test_missing_model_raises_a_planner_error_not_an_oserror(self):
        s = P.PlannerSession(model_path="/definitely/not/here")
        with self.assertRaises(P.PlannerError):
            s.generate("sys", "user")
        s.release()

    def test_context_manager_releases(self):
        s = P.PlannerSession(model_path="/definitely/not/here")
        with s:
            pass
        self.assertTrue(s.stats["released"])

    def test_a_borrowed_session_is_the_callers_to_release(self):
        stub = StubSession([_plan_json(3)])
        out = P.plan_film("a key", n_shots=3, session=stub)
        self.assertFalse(P.is_plan_error(out))
        self.assertFalse(stub.released, "plan_film released a session it does not own")
        stub.release()

    def test_an_owned_session_is_released_even_when_the_model_explodes(self):
        stub = StubSession([RuntimeError("boom")])
        real = P.PlannerSession
        P.PlannerSession = _StubFactory(stub)
        try:
            with self.assertRaises(RuntimeError):
                P.plan_film("a key", n_shots=3)
        finally:
            P.PlannerSession = real
        self.assertTrue(stub.released, "the model survived an exception inside plan_film")

    def test_missing_model_reports_release_in_the_metadata(self):
        out = P.plan_film("a key", n_shots=3, model_path="/definitely/not/here")
        self.assertTrue(P.is_plan_error(out))
        self.assertEqual(out["error"]["kind"], "model_unavailable")
        self.assertTrue(out["_planner"]["model_released"])


# --------------------------------------------------------------------------------------
# LIVE — real weights. Opt-in.
# --------------------------------------------------------------------------------------

LIVE_CONCEPT = ("A lighthouse keeper on his last night before the light is automated. "
                "He says goodbye to the machine that kept him company for thirty years.")


@unittest.skipUnless(os.environ.get("PLANNER_LIVE") == "1",
                     "set PLANNER_LIVE=1 to run against the real planner model")
class TestLivePlanner(unittest.TestCase):
    def test_six_shot_plan_validates_and_the_model_is_released(self):
        if not P.DEFAULT_MODEL_PATH.exists():
            self.skipTest("planner model not on disk: %s" % P.DEFAULT_MODEL_PATH)
        out = P.plan_film(LIVE_CONCEPT, n_shots=6,
                          style="Live-action, cinematic, photoreal, heavy 35mm film grain")
        self.assertFalse(P.is_plan_error(out),
                         "planner failed: %s" % json.dumps(out.get("error"), indent=2))
        errs = storyboard.validate_storyboard(out)
        self.assertEqual(errs, [], "validator complained: %s" % errs)
        self.assertEqual(len(out["shots"]), 6)

        meta = out["_planner"]
        for s in out["shots"]:
            self.assertTrue(s["prompt"].startswith("integrated_multimodal_description: [Shot 1] "))
            self.assertIn("\n\noverall_soundscape: ", s["prompt"])
            self.assertIn("\n\nnon_diegetic_music: ", s["prompt"])
            self.assertEqual(s["prompt"].count("<d>"), s["prompt"].count("</d>"))
            self.assertEqual(s["tier"], "draft")
            self.assertEqual(s["engine"], "h3")

        # The whole point of the subprocess design.
        self.assertTrue(meta["model_released"])
        self.assertGreater(meta["peak_rss_gb"], 0.5)
        self.assertLess(meta["peak_rss_gb"], 16.0,
                        "plan-phase RSS blew past the budget: %s GB" % meta["peak_rss_gb"])

        sys.stderr.write(
            "\n  LIVE: model=%s  attempts=%d  load=%.1fs  gen=%.1fs  total=%.1fs\n"
            "        peak RSS=%.2f GB  mlx peak=%.2f GB  first-try-clean=%s\n"
            % (meta["model"], meta["attempts"], meta["load_s"] or 0.0, meta["gen_s"] or 0.0,
               meta["elapsed_s"], meta["peak_rss_gb"], meta["mx_peak_gb"],
               meta["first_try_clean"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
