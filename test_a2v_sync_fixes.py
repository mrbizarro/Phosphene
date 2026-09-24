"""The a2v lip-sync fixes — the lane default, the vocal stem, the prompt rules.

Measured on one shot, at one seed, with the image, the audio window and the
prompt held fixed: the
per-second correlation between the mouth's aperture and the vocal-band energy
of the soundtrack went **-0.065 -> +0.128** by changing the audio-guidance
default alone, and **-> +0.156** with a demucs vocal stem as the conditioning
waveform. -0.065 is worse than the same clip scored against deliberately WRONG
audio, i.e. the shipped default was not lip-syncing at all.

Structural assertions (reading a source file rather than running a render) are
used where the alternative is a GPU: the a2v branch of `run_job_inner` needs a
weights pack, a helper subprocess and twenty minutes. They are written against
the exact line that decides the behaviour, never against prose.
"""

import json
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

import mlx_ltx_panel as P
import music_video as mv
import storyboard
import storyboard_planner
from panel import routes_music

PANEL_SRC = Path(__file__).with_name("mlx_ltx_panel.py").read_text()
HELPER_SRC = Path(__file__).with_name("mlx_warm_helper.py").read_text()
INDEX_HTML = Path(__file__).with_name("webapp") / "index.html"
CHARACTERS_JS = Path(__file__).with_name("webapp") / "js" / "characters.js"


def _helper_functions(*names):
    """Exec the named top-level functions of mlx_warm_helper.py in isolation."""
    import ast
    tree = ast.parse(HELPER_SRC)
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in names]
    assert sorted(d.name for d in defs) == sorted(names), names
    ns: dict = {}
    exec(compile(ast.Module(body=defs, type_ignores=[]), "mlx_warm_helper.py",
                 "exec"), ns)
    return ns


# ---------------------------------------------------------------------------
# 1. THE LANE-AWARE AUDIO-GUIDANCE DEFAULT
# ---------------------------------------------------------------------------
class LaneAwareAudioScale(unittest.TestCase):
    """1.0 means "audio fully on" on Q4 and "audio exactly off" on Q8."""

    def test_the_lane_defaults_are_the_engines_own(self):
        # Q8: the vendored a2vid_two_stage hardcodes modality_scale=3.0.
        # Q4: a2vid_distilled multiplies the audio tokens, so 1.0 is identity.
        self.assertEqual(P.A2V_AUDIO_SCALE_ON["q8"], 3.0)
        self.assertEqual(P.A2V_AUDIO_SCALE_ON["q4"], 1.0)

    def test_a_q8_job_with_no_scale_gets_the_lanes_on_value(self):
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), 3.0)
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), 1.0)

    def test_an_explicit_value_passes_through_unchanged_on_both_lanes(self):
        for raw in ("0.5", "1.0", 2.5, "5.0"):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), float(raw))
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), float(raw))

    def test_garbage_and_nonpositive_fall_back_to_the_lane(self):
        # Same rule the helper applies one level down: <= 0 means "the
        # engine's own default", never a negative multiplier on audio tokens.
        for raw in ("banana", "0", 0, -1, "-3.0", [], {}, "nan", "inf", "-inf"):
            with self.subTest(raw=raw):
                self.assertEqual(P.a2v_audio_scale(raw, q8=True), 3.0)
                self.assertEqual(P.a2v_audio_scale(raw, q8=False), 1.0)

    def test_make_job_no_longer_fabricates_a_number(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav"})
        self.assertEqual(job["params"]["audio_conditioning_scale"], "")

    def test_make_job_keeps_a_value_the_caller_sent(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav",
                          "audio_conditioning_scale": "2.5"})
        self.assertEqual(job["params"]["audio_conditioning_scale"], "2.5")

    def test_the_dispatch_resolves_against_the_lane_it_is_about_to_run(self):
        # The lane is `uses_q8`, which also carries the Q8->Q4 fallback when
        # the Q8 surface is missing. Resolving against anything else would put
        # a 3.0 meant for the guider onto the distilled lane's token multiplier.
        self.assertIn("a2v_audio_scale(\n                a2v_requested_scale(p), q8=uses_q8)",
                      PANEL_SRC)

    def test_the_fabricated_default_cannot_come_back(self):
        self.assertNotIn('float(f("audio_conditioning_scale"', PANEL_SRC)
        self.assertNotIn('float(p.get("audio_conditioning_scale", 1.0))', PANEL_SRC)

    def test_the_helper_still_treats_blank_as_the_engine_default(self):
        # A hand-written job spec that names nothing must stay safe on both
        # lanes even though the panel now always resolves before sending.
        # The helper cannot be imported (its main loop reads stdin at module
        # level), so the two parsers are lifted out of the source and RUN.
        ns = _helper_functions("_a2v_distilled_scale_value",
                               "_a2v_modality_scale_value")
        q4 = ns["_a2v_distilled_scale_value"]
        q8 = ns["_a2v_modality_scale_value"]
        for raw in (None, "", "   ", "0", 0, -2, "-1.5", "banana"):
            with self.subTest(lane="q4", raw=raw):
                self.assertEqual(q4(raw), 1.0)
            with self.subTest(lane="q8", raw=raw):
                self.assertIsNone(q8(raw))      # None = the engine's own 3.0
        for raw, want in (("2.5", 2.5), (0.7, 0.7), ("3", 3.0)):
            with self.subTest(explicit=raw):
                self.assertEqual(q4(raw), want)
                self.assertEqual(q8(raw), want)
        self.assertIn("ms = 3.0 if ms is None else float(ms)", HELPER_SRC)
        # And the Q4 branch really calls the guarded parser.
        self.assertIn("audio_conditioning_scale=_a2v_distilled_scale_value(",
                      HELPER_SRC)

    def test_the_sidecar_records_the_number_the_engine_ran_with(self):
        # `params` keeps the caller's blank (so a re-run resolves against its
        # own lane again); the resolved value is recorded beside it, so a clip
        # says which default it was rendered at.
        self.assertIn('"audio_conditioning_scale_used":\n'
                      '                a2v_params["audio_conditioning_scale"]',
                      PANEL_SRC)

    def test_the_slider_starts_on_auto_and_sends_nothing(self):
        html = INDEX_HTML.read_text()
        self.assertIn('id="audioConditioningScale"', html)
        self.assertIn('data-auto="1"', html)
        self.assertIn('class="range-val">Auto<', html)
        js = CHARACTERS_JS.read_text()
        # The field is set only inside the guard, so an untouched control
        # cannot send a number the panel would then treat as deliberate.
        self.assertIn("if (audioConditioningScale !== null", js)
        self.assertIn("(acsEl && !acsEl.dataset.auto)", js)


# ---------------------------------------------------------------------------
# 2. THE VOCAL-STEM CONDITIONING SEAM
# ---------------------------------------------------------------------------
class ConditioningAudioIsNotAlwaysTheSoundtrack(unittest.TestCase):
    """What the model listens to vs. what the finished clip plays."""

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.song = self.root / "song.wav"
        self.song.write_bytes(b"RIFF....WAVE")
        self.stem = self.root / "vocals.wav"
        self.stem.write_bytes(b"RIFF....WAVE")
        self.addCleanup(self.tmp.cleanup)

    def test_no_stem_asked_for_means_the_song_itself(self):
        got, note = P.a2v_conditioning_audio({}, str(self.song))
        self.assertEqual(got, str(self.song))
        self.assertEqual(note, "")

    def test_an_explicit_stem_is_what_the_model_hears(self):
        got, note = P.a2v_conditioning_audio(
            {"audio_stem": str(self.stem)}, str(self.song))
        self.assertEqual(got, str(self.stem))
        self.assertEqual(note, "")

    def test_an_explicit_stem_that_is_not_there_is_a_refusal(self):
        # A typo in something the caller wrote. Rendering the full mix quietly
        # would hide it for the whole film.
        with self.assertRaises(RuntimeError) as cm:
            P.a2v_conditioning_audio(
                {"audio_stem": str(self.root / "nope.wav")}, str(self.song))
        self.assertIn("nope.wav", str(cm.exception))

    def test_auto_without_demucs_degrades_with_a_visible_note(self):
        with mock.patch.object(P, "_resolve_demucs", return_value=None):
            got, note = P.a2v_conditioning_audio(
                {"audio_stem_auto": "on"}, str(self.song))
        self.assertEqual(got, str(self.song))
        self.assertIn("stems extra", note)

    def test_auto_that_fails_mid_separation_never_fails_the_job(self):
        with mock.patch.object(P, "_resolve_demucs",
                               return_value=Path("/bin/true")), \
             mock.patch.object(P, "_a2v_separate_vocals",
                               side_effect=RuntimeError("demucs failed: boom")):
            got, note = P.a2v_conditioning_audio(
                {"audio_stem_auto": "1"}, str(self.song))
        self.assertEqual(got, str(self.song))
        self.assertIn("full mix", note)

    def test_auto_that_works_hands_back_the_stem(self):
        with mock.patch.object(P, "_resolve_demucs",
                               return_value=Path("/bin/true")), \
             mock.patch.object(P, "_a2v_separate_vocals",
                               return_value=self.stem):
            got, note = P.a2v_conditioning_audio(
                {"audio_stem_auto": "true"}, str(self.song))
        self.assertEqual(got, str(self.stem))
        self.assertEqual(note, "")

    def test_auto_is_off_unless_it_is_asked_for(self):
        for raw in ("", "0", "off", "no", None):
            with self.subTest(raw=raw):
                with mock.patch.object(P, "_resolve_demucs") as res:
                    got, note = P.a2v_conditioning_audio(
                        {"audio_stem_auto": raw}, str(self.song))
                res.assert_not_called()
                self.assertEqual(got, str(self.song))

    def test_the_stem_cache_is_keyed_to_the_file_not_the_name(self):
        first = P._a2v_stem_cache_path(str(self.song))
        self.assertEqual(first, P._a2v_stem_cache_path(str(self.song)))
        self.song.write_bytes(b"RIFF....WAVE-edited-and-longer")
        self.assertNotEqual(first, P._a2v_stem_cache_path(str(self.song)))


class StopCanEndASeparation(unittest.TestCase):
    """Demucs is a child of this panel, so Stop has to reach it.

    It ran through a bare, untracked `subprocess.run` (Codex review,
    2026-09-22): pressing Stop during separation killed the helper and left
    demucs grinding, with the queue held by a job the user had already
    cancelled, for up to the 900-second timeout. Worse, Stop DURING the final
    remux was caught as an ordinary mux failure, and the panel published the
    clip anyway — playing the a-cappella, because the original song is muxed
    back only at that step.
    """

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.song = self.root / "song.wav"
        self.song.write_bytes(b"RIFF....WAVE")
        # A stand-in separator: writes what demucs writes, exits 0.
        self.demucs = self.root / "demucs"
        self.demucs.write_text(
            "#!/bin/sh\n"
            'out=""\n'
            'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
            'mkdir -p "$out/htdemucs/song" && printf RIFF > "$out/htdemucs/song/vocals.wav"\n'
            "exit 0\n")
        self.demucs.chmod(0o755)
        self.job = {"id": "job-stem-1", "cancel_requested": False}

    def _separate(self):
        with mock.patch.object(P, "_thread_job", return_value=self.job):
            return P._a2v_separate_vocals(str(self.song), self.demucs)

    def test_a_real_separation_still_produces_the_stem(self):
        # The gate is a real exit code from a real child, not a mock.
        stem = self._separate()
        self.assertTrue(stem.is_file())
        self.assertEqual(stem.name, "vocals.wav")
        self.assertFalse((stem.parent / "work").exists(), "the work dir leaked")
        # Cached: the second call must not run the separator again.
        self.demucs.write_text("#!/bin/sh\nexit 9\n")
        self.assertEqual(self._separate(), stem)

    def test_the_child_is_registered_where_stop_looks_for_it(self):
        seen = []
        real = P._register_job_pgid
        with mock.patch.object(P, "_register_job_pgid",
                               side_effect=lambda key, pgid, job=None: (
                                   seen.append(key), real(key, pgid, job))[1]):
            self._separate()
        self.assertIn("mux_pgid", seen)
        # And that is a key /stop actually kills.
        stop = PANEL_SRC.split("def stop_current_job")[1].split("\ndef ")[0]
        self.assertIn('STATE.get("mux_pgid")', stop)
        self.assertIn("os.killpg(mux_pgid", stop)

    def test_stop_before_the_separator_starts_is_a_cancellation(self):
        self.job["cancel_requested"] = True
        with mock.patch.object(P, "_thread_job", return_value=self.job):
            with self.assertRaises(P.JobCancelled):
                P._a2v_separate_vocals(str(self.song), self.demucs)

    def test_a_separation_that_fails_is_still_only_a_note(self):
        self.demucs.write_text("#!/bin/sh\necho 'no such model' >&2\nexit 1\n")
        self.demucs.chmod(0o755)
        with mock.patch.object(P, "_thread_job", return_value=self.job), \
             mock.patch.object(P, "_resolve_demucs", return_value=self.demucs):
            got, note = P.a2v_conditioning_audio(
                {"audio_stem_auto": "on"}, str(self.song))
        self.assertEqual(got, str(self.song))
        self.assertIn("full mix", note)

    def test_stop_during_the_separation_is_never_downgraded_to_a_note(self):
        # A cancelled job must not quietly become a full-mix render that the
        # user then waits twenty minutes for.
        with mock.patch.object(P, "_resolve_demucs", return_value=self.demucs), \
             mock.patch.object(P, "_a2v_separate_vocals",
                               side_effect=P.JobCancelled("Stopped during it.")):
            with self.assertRaises(P.JobCancelled):
                P.a2v_conditioning_audio({"audio_stem_auto": "on"}, str(self.song))

    def test_the_separator_runs_through_the_tracked_runner(self):
        body = PANEL_SRC.split("def _a2v_separate_vocals")[1].split("\ndef ")[0]
        self.assertIn("run_tracked_subprocess(", body)
        self.assertNotIn("subprocess.run(", body)
        self.assertIn("A2V_STEM_TIMEOUT_S", body)

    def test_stop_during_the_remux_does_not_publish_the_a_cappella(self):
        # The clip on disk is still the one the model sang against; the song
        # goes back over it in the remux, so a remux that was STOPPED may not
        # be filed as a finished take.
        block = PANEL_SRC.split("if cond_audio != audio_src:")[1].split("sidecar = {")[0]
        self.assertIn("a2v_mux_original_audio(", block)
        self.assertLess(block.index("except JobCancelled:"),
                        block.index("except Exception as exc:"),
                        "Stop is still caught as an ordinary mux failure")


class OnlyToolsTheInstallScriptsProvide(unittest.TestCase):

    def test_a_resolved_demucs_is_a_file_that_exists_or_nothing(self):
        # `_resolve_tool` ends on a LAST-RESORT path that need not exist. A
        # separator that is not there has to read as "not installed" now, not
        # as a command that dies twenty minutes into a render.
        got = P._resolve_demucs()
        self.assertTrue(got is None or got.is_file())

    def test_no_foreign_virtualenv_is_a_dependency(self):
        # Some other project's venv on this Mac is not a dependency of
        # Phosphene, and an install script cannot provide one.
        self.assertNotIn("voice-lab", PANEL_SRC)
        # The resolver looks at exactly two places: the engine venv's own bin
        # (what the installer script fills) and the ordinary tool resolver
        # (env override, PATH, Pinokio's folders, Homebrew). Anything else
        # would be a path this project cannot install into.
        body = PANEL_SRC.split("def _resolve_demucs")[1].split("\ndef ")[0]
        self.assertIn('HELPER_PYTHON.parent / "demucs"', body)
        self.assertIn('_resolve_tool("demucs", "PHOSPHENE_DEMUCS")', body)
        self.assertNotIn("/Users/", body)

    def test_the_installer_script_exists_and_parses(self):
        script = Path(__file__).with_name("scripts") / "pinokio" / "a2v_stems_deps.sh"
        self.assertTrue(script.is_file())
        self.assertTrue(os.access(script, os.X_OK), "not executable")
        self.assertEqual(
            subprocess.run(["bash", "-n", str(script)]).returncode, 0)

    def test_the_note_points_at_the_installer(self):
        self.assertIn("a2v_stems_deps.sh", P.A2V_STEM_MISSING_NOTE)


class TheStemNeverReachesTheAudience(unittest.TestCase):

    def test_make_job_carries_both_stem_fields(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav",
                          "audio_stem": "/y.wav", "audio_stem_auto": "on"})
        self.assertEqual(job["params"]["audio_stem"], "/y.wav")
        self.assertEqual(job["params"]["audio_stem_auto"], "on")

    def test_the_stem_goes_through_the_paste_cleaner(self):
        self.assertIn("audio_stem", P.PASTED_PATH_FIELDS)

    def test_the_helper_is_handed_the_conditioning_file(self):
        self.assertIn('"audio_path": cond_audio,', PANEL_SRC)

    def test_the_original_is_muxed_back_when_they_differ(self):
        self.assertIn("if cond_audio != audio_src:", PANEL_SRC)
        self.assertIn("a2v_mux_original_audio(", PANEL_SRC)

    def test_the_mux_copies_the_video_stream(self):
        # A container edit, not a second render.
        src = PANEL_SRC.split("def a2v_mux_original_audio")[1].split("\ndef ")[0]
        self.assertIn('"-c:v", "copy"', src)


class TheBoardCarriesTheStem(unittest.TestCase):

    def _board(self, **kw):
        sections = [{"start": 0.0, "end": 24.0, "kind": "vocal", "name": "v"}]
        images = [{"path": "/u/face.png", "role": "singer"},
                  {"path": "/u/room.png", "role": "room"}]
        return mv.plan_music_video(sections, images, style="warm 16mm",
                                   bpm_grid={}, song="/o/song.wav", **kw)

    def test_a_singing_shot_carries_the_stem_and_b_roll_does_not(self):
        out = self._board(vocal_stem="/o/vocals.wav")
        sing = [s for s in out["shots"] if s["music_video"]["kind"] == "singing"]
        broll = [s for s in out["shots"] if s["music_video"]["kind"] == "broll"]
        self.assertTrue(sing)
        for s in sing:
            self.assertEqual(s["audio_stem"], "/o/vocals.wav")
        for s in broll:
            self.assertNotIn("audio_stem", s)
        self.assertEqual(out["board"]["music_video"]["vocal_stem"],
                         "/o/vocals.wav")

    def test_no_stem_means_no_key_at_all(self):
        out = self._board()
        for s in out["shots"]:
            self.assertNotIn("audio_stem", s)
        self.assertEqual(out["board"]["music_video"]["vocal_stem"], "")

    def test_the_stem_moves_nothing_about_the_clock(self):
        # The stem is the whole song, separated: a shot's audio_start_time
        # means the same second in both files, so the tiling must be identical.
        plain = self._board()
        stemmed = self._board(vocal_stem="/o/vocals.wav")
        self.assertEqual(
            [(s["n"], s["duration_s"], s.get("audio_start_time"))
             for s in plain["shots"]],
            [(s["n"], s["duration_s"], s.get("audio_start_time"))
             for s in stemmed["shots"]])

    def test_shot_to_job_maps_the_stem_on_a2v_and_nowhere_else(self):
        pol = storyboard.default_policy()["final"]
        shot = {"n": 1, "mode": "a2v", "prompt": "she sings",
                "duration_s": 5.0, "audio": "/o/song.wav",
                "audio_start_time": 12.0, "audio_stem": "/o/vocals.wav",
                "still": "/u/face.png", "engine": "ltx"}
        job = storyboard.shot_to_job(shot, pol)
        self.assertEqual(job["audio_stem"], "/o/vocals.wav")
        self.assertEqual(job["audio"], "/o/song.wav")
        self.assertEqual(job["audio_start_time"], "12.0")
        text = storyboard.shot_to_job(
            {"n": 2, "mode": "text", "prompt": "a room", "duration_s": 5.0,
             "engine": "ltx", "audio_stem": "/o/vocals.wav"}, pol)
        self.assertNotIn("audio_stem", text)


# ---------------------------------------------------------------------------
# 3. THE PROMPT RULES FOR A SINGING SHOT
# ---------------------------------------------------------------------------
class StillnessIsTheLipSyncKiller(unittest.TestCase):
    """LTX reads a stillness word as "nothing moves" - lips included."""

    BANNED = (
        "The camera holds a static shot, the frame never moves.",
        "a static hold on the stage",
        "she holds perfectly still at the microphone",
        "he stands still, singing",
        "the singer barely moves",
        "with no new movement of any kind",
        "she is motionless",
        "a freeze frame of the band",
        "nothing in the scene moves",
        "she does not move",
    )

    def test_every_banned_phrase_is_reported(self):
        for line in self.BANNED:
            with self.subTest(line=line):
                self.assertTrue(storyboard.a2v_stillness_problems(line), line)

    def test_a_cleaned_prompt_reports_nothing(self):
        for line in self.BANNED:
            with self.subTest(line=line):
                cleaned = storyboard.a2v_prompt(line)
                self.assertEqual(storyboard.a2v_stillness_problems(cleaned), [],
                                 cleaned)

    def test_still_as_an_adverb_is_not_a_freeze_frame(self):
        # This list BLOCKS a render, so a false positive is expensive.
        for line in ("the coffee is still hot, she sings",
                     "a still life of flowers on the piano",
                     "she is still singing when the lights come up"):
            with self.subTest(line=line):
                self.assertEqual(storyboard.a2v_stillness_problems(line), [])

    def test_a_camera_clause_is_deleted_and_a_person_clause_is_rewritten(self):
        # Two different treatments, on purpose: an audio-driven shot does not
        # want a camera restraint sentence at all, but a clause about a person
        # has to keep its subject.
        got = storyboard.a2v_clean_prompt(
            "The camera holds a static shot, the frame never moves - no pan, "
            "no push-in, no reframing. She stands still at the microphone, singing.")
        self.assertNotIn("camera", got.lower())
        self.assertIn("She stands steady at the microphone", got)
        self.assertIn("singing", got)

    def test_the_planners_canonical_sentences_are_the_ones_it_kills(self):
        # Not a hypothetical list: these are the strings storyboard_planner
        # writes mechanically onto every shot.
        self.assertTrue(storyboard.a2v_stillness_problems(
            storyboard_planner._CAMERA_SENTENCES["static"]))
        self.assertTrue(storyboard.a2v_stillness_problems(
            "for the last two seconds she is still, with no new movement of any kind"))


class TheSyncContractSurvivesEverything(unittest.TestCase):

    def test_the_contract_is_literal_and_says_lip_sync(self):
        self.assertIn("lip-syncs every vocal syllable to the supplied "
                      "soundtrack", storyboard.A2V_SYNC_CONTRACT)

    def test_it_is_appended_last(self):
        got = storyboard.a2v_prompt("close-up, singing to camera, warm 16mm")
        self.assertTrue(got.endswith(storyboard.A2V_SYNC_CONTRACT), got)

    def test_it_is_not_collected_twice(self):
        once = storyboard.a2v_prompt("close-up, singing to camera")
        self.assertEqual(storyboard.a2v_prompt(once), once)
        self.assertEqual(once.count("lip-syncs"), 1)

    def test_a_silent_window_gets_relaxed_closed_lips_instead(self):
        got = storyboard.a2v_prompt("wide, the band plays", silent=True)
        self.assertIn("relaxed closed lips", got)
        self.assertNotIn("lip-syncs", got)

    def test_an_empty_direction_is_still_a_contract(self):
        self.assertEqual(storyboard.a2v_prompt(""),
                         storyboard.A2V_SYNC_CONTRACT)

    def test_compose_shot_prompt_is_the_last_line_of_defence(self):
        # A board planned before this law existed, or hand-edited since, still
        # reaches the model with the contract on it.
        shot = {"n": 1, "mode": "a2v", "audio": "/o/s.wav",
                "prompt": "she holds perfectly still and sings"}
        got = storyboard.compose_shot_prompt(shot)
        self.assertIn("lip-syncs", got)
        self.assertEqual(storyboard.a2v_stillness_problems(got), [])
        # And it does not touch a shot that is not audio-driven.
        plain = storyboard.compose_shot_prompt(
            {"n": 2, "mode": "text", "prompt": "she holds perfectly still"})
        self.assertNotIn("lip-syncs", plain)

    def test_the_last_line_of_defence_does_not_cap(self):
        # Capping a FINISHED prompt would delete the face law and the style.
        long_prompt = ("a woman at a microphone, " * 20).rstrip(", ")
        shot = {"n": 1, "mode": "a2v", "audio": "/o/s.wav",
                "prompt": long_prompt + ". " + storyboard.A2V_SYNC_CONTRACT}
        got = storyboard.compose_shot_prompt(shot)
        self.assertGreater(len(got.split()), storyboard.A2V_MAX_WORDS)


class TheDirectionIsShort(unittest.TestCase):

    def test_the_creative_direction_is_capped(self):
        long_line = ", ".join(f"detail number {i}" for i in range(40))
        got = storyboard.a2v_direction(long_line)
        self.assertLessEqual(len(got.split()), storyboard.A2V_MAX_WORDS)

    def test_the_cap_falls_on_a_clause_boundary(self):
        long_line = ", ".join(f"detail number {i}" for i in range(40))
        got = storyboard.a2v_direction(long_line)
        # No half-clause left hanging for the model to finish by inventing.
        self.assertTrue(got.endswith("."), got)
        self.assertNotIn(",,", got)

    def test_a_short_line_is_left_exactly_alone(self):
        self.assertEqual(storyboard.a2v_direction("close-up, singing to camera"),
                         "close-up, singing to camera")

    def test_no_cap_means_no_cap(self):
        long_line = ", ".join(f"detail number {i}" for i in range(40))
        self.assertEqual(storyboard.a2v_direction(long_line, None), long_line)


class ValidationNoLongerSkipsA2V(unittest.TestCase):

    def _board(self, prompt):
        board = storyboard.new_storyboard("sb_test", "T", shots=[{
            "n": 1, "title": "S01", "mode": "a2v", "engine": "ltx",
            "prompt": prompt, "duration_s": 10.0, "seed": -1, "refs": [],
            "audio": "/o/s.wav", "audio_start_time": 0.0, "status": "pending"}])
        return storyboard.validate_storyboard_detail(board)

    def test_a_stillness_word_is_an_error_now(self):
        errs = self._board("she holds perfectly still, singing to camera. "
                           + storyboard.A2V_SYNC_CONTRACT)
        codes = [e["code"] for e in errs]
        self.assertIn("a2v_stillness", codes)
        hit = next(e for e in errs if e["code"] == "a2v_stillness")
        self.assertEqual(hit["n"], 1)
        self.assertEqual(hit["field"], "prompt")
        self.assertIn("holds perfectly still", hit["message"])

    def test_a_clean_singing_shot_passes(self):
        self.assertEqual(
            self._board(storyboard.a2v_prompt("close-up, singing to camera")),
            [])

    def test_the_old_exemptions_are_still_exemptions(self):
        # The words an a2v mouth says are a WAVEFORM, so the speech and pacing
        # laws still do not apply - only the stillness law was added.
        codes = [e["code"] for e in
                 self._board(storyboard.a2v_prompt("close-up, singing to camera"))]
        self.assertNotIn("speech_without_words", codes)
        self.assertNotIn("dialogue_does_not_fit", codes)

    def test_a_planned_music_video_validates(self):
        sections = [{"start": 0.0, "end": 40.0, "kind": "vocal", "name": "v"}]
        images = [{"path": "/u/face.png", "role": "singer"},
                  {"path": "/u/room.png", "role": "room"}]
        out = mv.plan_music_video(sections, images, style="warm 16mm",
                                  bpm_grid={}, song="/o/s.wav",
                                  board_id="sb_mv")
        self.assertEqual(storyboard.validate_storyboard(out["board"]), [])


class ThePlannerStopsWritingTheKiller(unittest.TestCase):

    def test_an_a2v_body_gets_a_locked_tripod_not_a_frozen_scene(self):
        got = storyboard_planner._compose_body(
            "A woman at a microphone", "static", "she is still", "close",
            "a2v")
        self.assertEqual(storyboard.a2v_stillness_problems(got), [], got)
        self.assertIn("locked in place", got)
        self.assertIn("lips and jaw work clearly", got)

    def test_the_settle_clause_is_not_written_on_an_a2v_shot(self):
        got = storyboard_planner._compose_body(
            "A woman at a microphone", "static",
            "she is quiet with her hands on the stand", "close", "a2v")
        self.assertNotIn("completely finished before the shot ends", got)

    def test_an_ordinary_shot_is_untouched(self):
        got = storyboard_planner._compose_body(
            "A woman at a microphone", "static",
            "she is quiet with her hands on the stand", "close")
        self.assertIn(storyboard_planner._CAMERA_SENTENCES["static"], got)
        self.assertIn("completely finished before the shot ends", got)

    def test_the_contract_lands_after_the_style_and_the_audio_line(self):
        got = storyboard_planner._assemble_ltx_prompt(
            "A woman at a microphone, singing", "room tone",
            "1990s broadcast video", "static", "she is still", "close", "a2v")
        self.assertTrue(got.endswith(storyboard.A2V_SYNC_CONTRACT), got)
        self.assertIn("1990s broadcast video", got)
        self.assertIn("Audio: room tone", got)
        self.assertEqual(storyboard.a2v_stillness_problems(got), [])

    def test_the_face_law_survives_the_cap(self):
        got = storyboard_planner._assemble_ltx_prompt(
            ", ".join(f"detail number {i}" for i in range(40)), "room tone",
            "warm 16mm", "static", "", "close", "a2v")
        self.assertIn("holds the exact angle", got)

    def test_a_reassembly_keeps_the_shot_s_own_mode(self):
        shot = {"mode": "a2v", "engine": "ltx",
                "description": "A woman at a microphone, singing",
                "soundscape": "room tone", "camera": "static",
                "settle": "she is still", "face": "close"}
        got = storyboard_planner._reassemble_prompt(shot, "warm 16mm", (), None)
        self.assertTrue(got.endswith(storyboard.A2V_SYNC_CONTRACT), got)
        self.assertEqual(storyboard.a2v_stillness_problems(got), [])


class TheMusicVideoWritesSafeSingingShots(unittest.TestCase):

    def _plan(self, sections=None, **kw):
        sections = sections or [{"start": 0.0, "end": 40.0, "kind": "vocal",
                                 "name": "v"}]
        images = [{"path": "/u/face.png", "role": "singer"},
                  {"path": "/u/room.png", "role": "room"}]
        return mv.plan_music_video(sections, images, style="warm 16mm",
                                   bpm_grid={}, song="/o/s.wav", **kw)

    def test_every_singing_shot_carries_the_contract_and_no_stillness(self):
        out = self._plan()
        sang = 0
        for shot in out["shots"]:
            if shot["music_video"]["kind"] == "singing":
                self.assertIn("lip-syncs", shot["prompt"])
                self.assertEqual(
                    storyboard.a2v_stillness_problems(shot["prompt"]), [])
                sang += 1
        self.assertTrue(sang)

    def test_b_roll_gets_no_contract_because_it_has_no_waveform(self):
        out = self._plan()
        for shot in out["shots"]:
            if shot["music_video"]["kind"] == "broll":
                self.assertNotIn("lip-syncs", shot["prompt"])

    def test_a_per_shot_line_is_made_safe_too(self):
        # The override replaces the direction; it must not be a way around the
        # law, or the one shot somebody cared enough to write by hand is the
        # one that freezes.
        out = self._plan(shot_prompts={1: "she holds perfectly still and sings"})
        first = out["shots"][0]
        self.assertEqual(first["music_video"]["kind"], "singing")
        self.assertIn("lip-syncs", first["prompt"])
        self.assertEqual(storyboard.a2v_stillness_problems(first["prompt"]), [])

    def test_a_silent_window_is_told_to_close_the_mouth(self):
        self.assertIn("relaxed closed lips",
                      mv._prompt_for({"path": "/u/f.png", "role": "singer"},
                                     "singing", "warm 16mm", silent=True))

    def test_vocal_activity_is_read_off_the_sections(self):
        sections = [{"start": 0.0, "end": 30.0, "kind": "vocal"},
                    {"start": 30.0, "end": 60.0, "kind": "instrumental"}]
        self.assertFalse(mv.slice_is_silent(sections, 5.0, 15.0))
        self.assertTrue(mv.slice_is_silent(sections, 40.0, 50.0))
        # A window that straddles counts as singing: a mouth that sings
        # slightly too long reads far better than one that stops mid-line.
        self.assertFalse(mv.slice_is_silent(sections, 25.0, 35.0))
        self.assertTrue(mv.slice_is_silent(sections, 29.0, 60.0))


# ---------------------------------------------------------------------------
# 4. THE FACE-TIGHT PICTURE SINGS THE CLOSE SHOTS
# ---------------------------------------------------------------------------
class TheBiggestFaceLeadsTheSinging(unittest.TestCase):
    """A mouth at a quarter of the frame is mush; at half of it, legible."""

    WIDE = {"path": "/u/wide.png", "role": "singer", "face_frac": 0.23}
    TIGHT = {"path": "/u/tight.png", "role": "singer", "face_frac": 0.46}
    ROOM = {"path": "/u/room.png", "role": "room"}

    def _plan(self, images, **kw):
        sections = [{"start": 0.0, "end": 60.0, "kind": "vocal", "name": "v"}]
        return mv.plan_music_video(sections, images, style="warm 16mm",
                                   bpm_grid={}, song="/o/s.wav", **kw)

    def _singing(self, out):
        return [s["music_video"]["image"] for s in out["shots"]
                if s["music_video"]["kind"] == "singing"]

    def test_the_biggest_measured_face_opens_the_rotation(self):
        out = self._plan([self.WIDE, self.TIGHT, self.ROOM])
        self.assertEqual(self._singing(out)[0], "/u/tight.png")
        self.assertTrue(any("biggest face" in n for n in out["notes"]))

    def test_the_rotation_still_alternates(self):
        # Biggest first is about WHO LEADS, not about shooting one picture for
        # the whole song - the angle still has to change.
        got = self._singing(self._plan([self.WIDE, self.TIGHT, self.ROOM]))
        self.assertGreater(len(set(got)), 1)
        self.assertEqual(got[0], "/u/tight.png")
        self.assertEqual(got[1], "/u/wide.png")

    def test_a_use_pin_means_the_caller_decided_and_nothing_is_reordered(self):
        images = [dict(self.WIDE, use="open"), self.TIGHT, self.ROOM]
        got = self._singing(self._plan(images))
        self.assertEqual(got[0], "/u/wide.png")
        self.assertFalse(any("biggest face" in n for n in self._plan(images)["notes"]))

    def test_a_per_shot_image_pin_wins_outright(self):
        out = self._plan([self.WIDE, self.TIGHT, self.ROOM],
                         shot_images={1: "/u/wide.png"})
        self.assertEqual(self._singing(out)[0], "/u/wide.png")
        self.assertFalse(any("biggest face" in n for n in out["notes"]))

    def test_unmeasured_pictures_keep_the_order_they_were_given(self):
        a = {"path": "/u/a.png", "role": "singer"}
        b = {"path": "/u/b.png", "role": "singer"}
        got = self._singing(self._plan([a, b, self.ROOM]))
        self.assertEqual(got[0], "/u/a.png")

    def test_a_measured_face_leads_the_unmeasured_ones(self):
        a = {"path": "/u/a.png", "role": "singer"}
        got = self._singing(self._plan([a, self.TIGHT, self.ROOM]))
        self.assertEqual(got[0], "/u/tight.png")

    def test_a_pin_moves_who_is_in_frame_and_never_the_clock(self):
        plain = self._plan([self.WIDE, self.TIGHT, self.ROOM])
        pinned = self._plan([self.WIDE, self.TIGHT, self.ROOM],
                            shot_images={2: "/u/tight.png"})
        self.assertEqual(
            [(s["n"], s["duration_s"], s.get("audio_start_time"))
             for s in plain["shots"]],
            [(s["n"], s["duration_s"], s.get("audio_start_time"))
             for s in pinned["shots"]])

    def test_a_pin_to_a_picture_outside_the_cast_is_a_note_not_a_refusal(self):
        out = self._plan([self.WIDE, self.TIGHT, self.ROOM],
                         shot_images={1: "/u/stranger.png"})
        self.assertTrue(any("stranger.png" in n for n in out["notes"]))
        self.assertEqual(self._singing(out)[0], "/u/tight.png")

    def test_face_frac_is_never_invented_from_the_picture(self):
        # The cheap proxies (aspect, short side) cannot tell a 1:1 crop of a
        # face from a 1:1 crop of a room, so a picture nobody measured has no
        # number at all rather than a made-up one.
        for raw in (None, "", "banana", 0, -1, [], float("nan")):
            with self.subTest(raw=raw):
                self.assertIsNone(mv._face_frac(raw))
        self.assertEqual(mv._face_frac("0.46"), 0.46)
        self.assertEqual(mv._face_frac(2.0), 1.0)

    def test_the_cast_carries_the_measurement_through(self):
        cast = mv._cast([self.TIGHT, self.ROOM])
        self.assertEqual(cast[0]["face_frac"], 0.46)
        self.assertIsNone(cast[1]["face_frac"])


class TheRouteTakesTheMeasurementAndThePin(unittest.TestCase):

    def test_a_face_frac_outside_zero_to_one_is_refused_by_name(self):
        rows, err = routes_music._music_video_images(json.dumps(
            [{"path": "/etc/passwd", "role": "singer", "face_frac": 4}]))
        self.assertEqual(rows, [])
        self.assertTrue(err)

    def test_a_row_that_pins_only_a_picture_is_not_an_empty_prompt(self):
        prompts, err = routes_music._music_video_shot_prompts(
            json.dumps([{"n": 3, "image": "/u/face.png"}]))
        self.assertEqual((prompts, err), ({}, ""))

    def test_a_row_that_says_nothing_at_all_is_still_refused(self):
        prompts, err = routes_music._music_video_shot_prompts(
            json.dumps([{"n": 3, "prompt": "  "}]))
        self.assertEqual(prompts, {})
        self.assertIn("empty", err)

    def test_a_pinned_picture_obeys_the_containment_rule(self):
        pins, err = routes_music._music_video_shot_images(
            json.dumps([{"n": 3, "image": "/etc/passwd"}]))
        self.assertEqual(pins, {})
        self.assertIn("/etc/passwd", err)
        self.assertIn("uploads", err)


class JobsSavedBeforeTheFix(unittest.TestCase):
    """A job queued or finished under 4.15.1 carries the old make_job's float
    1.0 on every job. It survives Update in the saved queue and /queue/retry
    copies it verbatim; reading it as a deliberate 1.0 would keep Q8 audio
    guidance OFF for exactly the renders this fix is for."""

    def test_a_legacy_float_one_is_the_old_default_not_a_choice(self):
        req = P.a2v_requested_scale({"audio_conditioning_scale": 1.0})
        self.assertEqual(req, "")
        self.assertEqual(P.a2v_audio_scale(req, q8=True), 3.0)
        self.assertEqual(P.a2v_audio_scale(req, q8=False), 1.0)

    def test_a_legacy_float_that_was_dragged_is_kept(self):
        for raw in (2.5, 0.5, 4.0):
            with self.subTest(raw=raw):
                req = P.a2v_requested_scale({"audio_conditioning_scale": raw})
                self.assertEqual(P.a2v_audio_scale(req, q8=True), raw)

    def test_a_new_job_that_sends_one_point_oh_keeps_it(self):
        # Today's make_job stores what the form sent, as a STRING, so a user
        # who drags the slider to 1.0 on purpose gets 1.0.
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav",
                          "audio_conditioning_scale": "1.0"})
        req = P.a2v_requested_scale(job["params"])
        self.assertEqual(P.a2v_audio_scale(req, q8=True), 1.0)

    def test_a_new_auto_job_and_a_missing_key_both_resolve_to_the_lane(self):
        job = P.make_job({"mode": "a2v", "prompt": "a face", "audio": "/x.wav"})
        for params in (job["params"], {}):
            with self.subTest(params=params.get("audio_conditioning_scale")):
                req = P.a2v_requested_scale(params)
                self.assertEqual(P.a2v_audio_scale(req, q8=True), 3.0)

    def test_make_job_never_stores_a_float(self):
        # The legacy rule above keys on the TYPE; it is only sound while no
        # current path stores a float here.
        for form in ({}, {"audio_conditioning_scale": "2.0"},
                     {"audio_conditioning_scale": "1.0"}):
            job = P.make_job({"mode": "a2v", "prompt": "p", "audio": "/x.wav",
                              **form})
            self.assertIsInstance(job["params"]["audio_conditioning_scale"], str)

    def test_the_run_log_calls_a_legacy_default_a_default(self):
        self.assertIn('_scale_note = "" if str(a2v_requested_scale(p) or "").strip()',
                      PANEL_SRC)



class TheDispatchSendsTheLaneValue(unittest.TestCase):
    """Behavioural, not structural: drive the REAL a2v branch of run_job_inner
    with a stub helper and read the spec it would have sent. No GPU, no weights,
    no subprocess — the helper, the pack preflight and the sidecar writer are
    the only things replaced, and all three are restored afterwards."""

    def _dispatch(self, acs, *, q8):
        import tempfile
        import time
        from unittest import mock

        tmp = Path(tempfile.mkdtemp(prefix="a2v-dispatch-"))
        wav = tmp / "a.wav"
        wav.write_bytes(b"RIFF0000WAVE")
        sent, sidecars = [], []

        class _Helper:
            ready_info: dict = {}

            def is_alive(self):
                return True

            def kill(self, *a, **k):
                pass

            def run(self, spec):
                sent.append(spec)
                Path(spec["params"]["output_path"]).write_bytes(b"x")
                return {"seed_used": 1, "elapsed_sec": 0.1}

        params = P.make_job({"mode": "a2v", "prompt": "a singer",
                             "audio": str(wav), "width": "512",
                             "height": "288", "frames": "49"})["params"]
        if acs is not None:
            params["audio_conditioning_scale"] = acs
        caps = dict(P.SYSTEM_CAPS, allows_q8=q8)
        with mock.patch.object(P, "HELPER", _Helper()), \
             mock.patch.object(P, "OUTPUT", tmp), \
             mock.patch.object(P, "SYSTEM_CAPS", caps), \
             mock.patch.object(P, "ltx_pack_preflight", lambda *a, **k: None), \
             mock.patch.object(P, "hq_surface_missing", lambda *a, **k: []), \
             mock.patch.object(P, "write_sidecar",
                               lambda _p, data: sidecars.append(data)):
            P.run_job_inner({"id": "t", "params": params,
                             "started_ts": time.time()})
        self.assertEqual(len(sent), 1)
        return (sent[0]["action"], sent[0]["params"]["audio_conditioning_scale"],
                sidecars[-1]["audio_conditioning_scale_used"])

    def test_the_matrix(self):
        cases = (
            # (what the job carries, Q8 lane result, Q4 lane result)
            (None,  3.0, 1.0),   # new job, slider on Auto (field not sent)
            ("1.0", 1.0, 1.0),   # new job, slider dragged to 1.0 on purpose
            ("2.5", 2.5, 2.5),   # new job, explicit value
            (1.0,   3.0, 1.0),   # saved by 4.15.1: the old fabricated default
            (2.2,   2.2, 2.2),   # saved by 4.15.1 after a real drag
        )
        for acs, want_q8, want_q4 in cases:
            with self.subTest(acs=acs, lane="q8"):
                self.assertEqual(self._dispatch(acs, q8=True),
                                 ("generate_a2v", want_q8, want_q8))
            with self.subTest(acs=acs, lane="q4"):
                self.assertEqual(self._dispatch(acs, q8=False),
                                 ("generate_a2v_distilled", want_q4, want_q4))


if __name__ == "__main__":
    unittest.main()
