"""Music Studio: the song as a thing you come back to.

A song keeps its artifacts (plan, tokens, latents, noise), and that is what
every variation restarts from. These are the rules the studio's verbs obey:

  * a variation names its parent by OUTPUT PATH; the worker turns that into an
    artifact folder from the parent's own sidecar — a form never names a
    state directory, and a song that kept nothing is refused with the reason;
  * an edited score is rendered only from inside the studio's own tree;
  * the lineage lands in the sidecar and the gallery reads it back;
  * the runner refuses the impossible combinations before loading a model.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import mlx_ltx_panel as P

RUNNER = Path(__file__).with_name("scripts") / "music" / "yue2_run.py"


class TheParams(unittest.TestCase):

    def test_variation_is_a_closed_vocabulary(self):
        for bad in ("", "remix", "../x"):
            self.assertEqual(P.music_params({"music_variation": bad})["music_variation"], "")
        for good in ("take", "sound", "restyle", "score"):
            self.assertEqual(P.music_params({"music_variation": good})["music_variation"], good)

    def test_precision_defaults_to_what_every_song_was_made_with(self):
        self.assertEqual(P.music_params({})["music_precision"], "8bit")
        self.assertEqual(P.music_params({"music_precision": "bf16"})["music_precision"], "bf16")
        self.assertEqual(P.music_params({"music_precision": "fp8"})["music_precision"], "8bit")

    def test_guidance_is_none_unless_chosen_and_sane(self):
        self.assertIsNone(P.music_params({})["music_cfg_scale"])
        self.assertEqual(P.music_params({"music_cfg_scale": "1.4"})["music_cfg_scale"], 1.4)
        self.assertIsNone(P.music_params({"music_cfg_scale": "40"})["music_cfg_scale"])
        self.assertIsNone(P.music_params({"music_cfg_scale": "loud"})["music_cfg_scale"])

    def test_title_and_parent_travel(self):
        p = P.music_params({"music_title": "Lights On", "music_parent": "/tmp/a song.wav"})
        self.assertEqual(p["music_title"], "Lights On")
        self.assertEqual(p["music_parent"], "/tmp/a song.wav")


class _Song:
    """A finished song with a complete artifact folder, or without one."""

    def __init__(self, artifacts=True, score=True):
        self.artifacts, self.score = artifacts, score

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        root = Path(self.tmp.name)
        self.wav = root / "music_x.wav"
        self.wav.write_bytes(b"RIFF")
        side = {"engine": "music", "style": "dream pop", "lyrics": "[Verse]\nla", "mode": "full"}
        if self.artifacts:
            art = root / "art"
            art.mkdir()
            (art / "result.json").write_text("{}")
            if self.score:
                (art / "score.abc").write_text("X:1\nK:C\n")
            side["artifacts"] = str(art)
            self.art = art
        Path(str(self.wav) + ".json").write_text(json.dumps(side))
        return self

    def __exit__(self, *a):
        self.tmp.cleanup()


class TheArgv(unittest.TestCase):

    def argv(self, form, job_id="j-test"):
        paths = {"python": Path("/x/py"), "runner": RUNNER,
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        return P.music_argv({"id": job_id, "params": form}, paths, Path("/tmp/out.wav"))

    def test_every_song_keeps_its_artifacts(self):
        argv = self.argv({"music_style": "dream pop"})
        d = Path(argv[argv.index("--artifacts") + 1])
        self.assertTrue(d.is_relative_to(P.MUSIC_ARTIFACTS))

    def test_a_take_restarts_from_the_parents_artifacts(self):
        with _Song() as song:
            argv = self.argv({"music_parent": str(song.wav), "music_variation": "take"})
            self.assertEqual(Path(argv[argv.index("--from-artifacts") + 1]), song.art)
            self.assertEqual(argv[argv.index("--variation") + 1], "take")

    def test_a_song_that_kept_nothing_is_refused_with_the_reason(self):
        with _Song(artifacts=False) as song:
            with self.assertRaises(RuntimeError) as cm:
                self.argv({"music_parent": str(song.wav), "music_variation": "sound"})
            self.assertIn("no saved artifacts", str(cm.exception))

    def test_a_restyle_points_at_the_saved_score(self):
        with _Song() as song:
            argv = self.argv({"music_parent": str(song.wav), "music_variation": "restyle",
                              "music_style": "bossa nova"})
            self.assertEqual(Path(argv[argv.index("--abc-file") + 1]), song.art / "score.abc")
            self.assertNotIn("--from-artifacts", argv)

    def test_a_restyle_of_a_scoreless_song_is_refused(self):
        with _Song(score=False) as song:
            with self.assertRaises(RuntimeError):
                self.argv({"music_parent": str(song.wav), "music_variation": "restyle"})

    def test_a_score_job_transcribes_only(self):
        argv = self.argv({"music_variation": "score", "music_source_audio": "/tmp/x.mp3"})
        self.assertIn("--transcribe-only", argv)
        self.assertIn("--source-audio", argv)

    def test_an_edited_score_must_come_from_the_studio_tree(self):
        with TemporaryDirectory() as tmp:
            stray = Path(tmp) / "evil.abc"
            stray.write_text("X:1\nK:C\n")
            with self.assertRaises(RuntimeError):
                self.argv({"music_style": "x", "music_abc_path": str(stray)})
        edits = P.MUSIC_ARTIFACTS / "edits"
        edits.mkdir(parents=True, exist_ok=True)
        ok = edits / "test_edit.abc"
        ok.write_text("X:1\nK:C\n")
        try:
            argv = self.argv({"music_style": "x", "music_abc_path": str(ok)})
            self.assertEqual(Path(argv[argv.index("--abc-file") + 1]), ok.resolve())
        finally:
            ok.unlink()

    def test_guidance_precision_and_title_reach_the_runner(self):
        argv = self.argv({"music_style": "x", "music_cfg_scale": "1.3",
                          "music_precision": "bf16", "music_title": "Lights On"})
        self.assertEqual(argv[argv.index("--cfg-scale") + 1], "1.3")
        self.assertEqual(argv[argv.index("--precision") + 1], "bf16")
        self.assertEqual(argv[argv.index("--title") + 1], "Lights On")


class TheRunnerRefusesTheImpossible(unittest.TestCase):

    def run_runner(self, *extra):
        out = subprocess.run(
            [sys.executable, str(RUNNER), "--model-dir", "/x", "--vae-dir", "/x",
             "--output", "/tmp/never.wav", *extra],
            capture_output=True, text=True, timeout=120)
        return out.returncode, out.stdout + out.stderr

    def test_a_variation_needs_its_artifacts(self):
        rc, out = self.run_runner("--variation", "take")
        self.assertEqual(rc, 2); self.assertIn("go together", out)

    def test_a_missing_artifact_folder_is_named(self):
        rc, out = self.run_runner("--variation", "sound", "--from-artifacts", "/tmp/not-a-song")
        self.assertEqual(rc, 2); self.assertIn("no saved song", out)

    def test_transcribe_only_writes_a_score_not_a_wav(self):
        rc, out = self.run_runner("--transcribe-only", "--source-audio", "/tmp/x.mp3")
        self.assertEqual(rc, 2); self.assertIn(".abc", out)

    def test_a_variation_needs_no_prompt(self):
        """The words and the style come off the saved plan."""
        with TemporaryDirectory() as tmp:
            (Path(tmp) / "result.json").write_text("{}")
            rc, out = self.run_runner("--variation", "take", "--from-artifacts", tmp)
        self.assertNotIn("Give it something to work with", out)


class TheGalleryReadsItBack(unittest.TestCase):

    def test_music_artifacts_for_reads_the_sidecar_not_the_filename(self):
        with _Song() as song:
            self.assertEqual(P.music_artifacts_for(song.wav), song.art)
        with _Song(artifacts=False) as song:
            self.assertIsNone(P.music_artifacts_for(song.wav))
        self.assertIsNone(P.music_artifacts_for(""))
        self.assertIsNone(P.music_artifacts_for("/tmp/nope.wav"))

    def test_the_song_card_facts_ride_on_the_output_row(self):
        """One poll payload, no second request for what the card shows first."""
        src = Path(__file__).with_name("mlx_ltx_panel.py").read_text()
        i = src.index("def list_outputs")
        block = src[i:i + 40000]
        for key in ('"has_score"', '"has_artifacts"', '"parent"', '"variation"', '"title"'):
            self.assertIn(key, block)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TheRoutesTrustNothing(unittest.TestCase):
    """A form field naming a file is a form field naming a file."""

    def setUp(self):
        from panel import routes_music
        routes_music.P = P
        self.R = routes_music

    def test_only_a_song_inside_the_outputs_folder_is_a_song(self):
        self.assertIsNone(self.R._song_output("/etc/passwd"))
        self.assertIsNone(self.R._song_output(""))
        self.assertIsNone(self.R._song_output(str(P.OUTPUT / "nope.wav")))

    def test_a_clip_that_is_not_music_is_not_a_song(self):
        P.OUTPUT.mkdir(parents=True, exist_ok=True)
        clip = P.OUTPUT / "_studio_test_clip.wav"
        clip.write_bytes(b"RIFF")
        Path(str(clip) + ".json").write_text(json.dumps({"engine": "h3"}))
        try:
            self.assertIsNone(self.R._song_output(str(clip)))
            Path(str(clip) + ".json").write_text(json.dumps({"engine": "music"}))
            self.assertEqual(self.R._song_output(str(clip)), clip.resolve())
        finally:
            clip.unlink(missing_ok=True)
            Path(str(clip) + ".json").unlink(missing_ok=True)

    def test_the_variation_vocabulary_matches_the_params(self):
        for kind in self.R.VARIATIONS:
            self.assertEqual(P.music_params({"music_variation": kind})["music_variation"], kind)


class _Handler:
    """Just enough of the panel's request handler to run a route body: the
    form it read, and where the JSON answer lands."""

    def __init__(self, form):
        self.form = {k: [v] for k, v in form.items()}
        self.payload, self.status = None, None

    def _read_form_body(self):
        return b"", self.form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class _Helper:
    """The warm helper, stubbed: it records what it was asked and answers."""

    def __init__(self, reply):
        self.reply, self.asked = reply, []

    def run(self, msg, timeout=None):
        self.asked.append(msg)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class TheSectionRewrite(unittest.TestCase):
    """Rewrite asks Gemma for ONE section, and the tag never travels: the
    editor owns it, and a tag in the answer lands doubled in the box."""

    def setUp(self):
        from panel import routes_music
        routes_music.P = P
        self.R = routes_music
        self._helper = getattr(P, "HELPER", None)

    def tearDown(self):
        if self._helper is not None:
            P.HELPER = self._helper

    def call(self, form, reply):
        P.HELPER = _Helper(reply)
        h = _Handler(form)
        self.R.post_music_lyrics_section(h, "/music/lyrics/section", "", "")
        return h, P.HELPER

    def test_with_neither_a_concept_nor_lines_the_model_is_never_loaded(self):
        h, helper = self.call({"section": "Chorus", "lines": "  ", "concept": ""},
                              {"lyrics": "never asked"})
        self.assertEqual(h.status, 400)
        self.assertEqual(helper.asked, [])

    def test_the_label_and_the_lines_reach_the_lyricist(self):
        h, helper = self.call({"section": "[Chorus]", "lines": "old hook",
                               "concept": "leaving a town", "style": "dream pop"},
                              {"lyrics": "a new hook", "elapsed_sec": 3.2})
        self.assertEqual(h.status, 200)
        params = helper.asked[0]["params"]
        self.assertEqual(helper.asked[0]["action"], "write_lyrics")
        # The brackets are the editor's, not the model's.
        self.assertEqual(params["section"], "Chorus")
        self.assertEqual(params["lines"], "old hook")
        self.assertEqual(params["concept"], "leaving a town")
        self.assertEqual(params["style"], "dream pop")
        self.assertEqual(h.payload["lines"], "a new hook")
        self.assertEqual(h.payload["section"], "Chorus")

    def test_a_rewrite_can_stand_on_the_lines_alone(self):
        h, helper = self.call({"section": "Verse", "lines": "a line that needs work"},
                              {"lyrics": "a better line"})
        self.assertEqual(h.status, 200)
        self.assertEqual(helper.asked[0]["params"]["concept"], "")

    def test_nothing_back_is_an_error_not_an_emptied_section(self):
        h, _ = self.call({"section": "Verse", "lines": "words"}, {"lyrics": "   "})
        self.assertEqual(h.status, 500)

    def test_a_helper_that_blows_up_says_so_instead_of_hanging(self):
        h, _ = self.call({"section": "Verse", "lines": "words"},
                         RuntimeError("helper failed to start"))
        self.assertEqual(h.status, 500)
        self.assertIn("helper failed", h.payload["error"])


class TheLyricistPrompt(unittest.TestCase):
    """The helper action the route leans on. The contract is narrow and the
    route cannot hold it up on its own: with `section` set, Gemma is asked
    for that section's lines and any tag it adds anyway is dropped."""

    def setUp(self):
        src = Path(__file__).with_name("mlx_warm_helper.py").read_text()
        i = src.index('if action == "write_lyrics":')
        self.block = src[i:src.index('if action == "enhance_prompt":', i)]

    def test_the_section_is_read_off_the_params_and_the_lines_with_it(self):
        self.assertIn('section = (p.get("section") or "").strip().strip("[]")', self.block)
        self.assertIn('lines_in = (p.get("lines") or "").strip()', self.block)

    def test_a_section_rewrite_does_not_need_a_concept(self):
        self.assertIn("if not concept and not (section and lines_in):", self.block)

    def test_the_answer_comes_back_without_its_tag(self):
        self.assertIn('ln.strip().startswith("[") and ln.strip().endswith("]")', self.block)
        # The whole-song branch keeps its own rule: everything before the
        # first tag is a title or an apology and gets dropped.
        self.assertIn('while lines and not lines[0].strip().startswith("["):', self.block)


class TheSimpleBrief(unittest.TestCase):
    """Simple mode: one description in, a brief out, nothing rendered."""

    def setUp(self):
        from panel import routes_music
        routes_music.P = P
        self.R = routes_music
        self._helper = getattr(P, "HELPER", None)
        self._queue = len(P.STATE.get("queue") or [])

    def tearDown(self):
        if self._helper is not None:
            P.HELPER = self._helper

    def call(self, form, reply):
        P.HELPER = _Helper(reply)
        h = _Handler(form)
        self.R.post_music_simple(h, "/music/simple", "", "")
        return h, P.HELPER

    BRIEF = {"title": "Leaving Town", "style": "English, slow piano ballad, 72 BPM",
             "lyrics": "[Verse]\nthe boxes by the door", "elapsed_sec": 8.1}

    def test_an_empty_description_never_loads_the_model(self):
        h, helper = self.call({"description": "   "}, self.BRIEF)
        self.assertEqual(h.status, 400)
        self.assertEqual(helper.asked, [])

    def test_the_brief_comes_back_whole_and_nothing_is_queued(self):
        h, helper = self.call({"description": "a ballad for a friend moving away",
                               "seconds": "180"}, self.BRIEF)
        self.assertEqual(h.status, 200)
        self.assertEqual(helper.asked[0]["action"], "write_song")
        self.assertEqual(helper.asked[0]["params"]["seconds"], 180)
        self.assertEqual(helper.asked[0]["params"]["instrumental"], False)
        self.assertEqual(h.payload["title"], "Leaving Town")
        self.assertIn("72 BPM", h.payload["style"])
        self.assertIn("[Verse]", h.payload["lyrics"])
        # A brief is not a job: Compose is still the user's press.
        self.assertEqual(len(P.STATE.get("queue") or []), self._queue)

    def test_instrumental_is_the_forms_decision_not_the_models(self):
        h, helper = self.call({"description": "a rainy night loop", "instrumental": "on"},
                              self.BRIEF)
        self.assertTrue(helper.asked[0]["params"]["instrumental"])
        self.assertEqual(h.payload["lyrics"], "")
        self.assertTrue(h.payload["instrumental"])

    def test_a_brief_with_no_style_is_nothing_to_show(self):
        h, _ = self.call({"description": "something nice"}, {"title": "x", "lyrics": "[Verse]\na"})
        self.assertEqual(h.status, 500)

    def test_a_nonsense_length_falls_back_instead_of_blowing_up(self):
        h, helper = self.call({"description": "x", "seconds": "forever"}, self.BRIEF)
        self.assertEqual(h.status, 200)
        self.assertEqual(helper.asked[0]["params"]["seconds"], 150)


class TheSongBrief(unittest.TestCase):
    """The helper action behind Simple mode. One Gemma call has to answer with
    the two fields YuE2 reads, and the parse has to survive the wrapping."""

    def setUp(self):
        src = Path(__file__).with_name("mlx_warm_helper.py").read_text()
        i = src.index('if action == "write_song":')
        self.block = src[i:src.index('if action == "enhance_prompt":', i)]

    def test_it_asks_for_one_json_object_with_the_three_fields(self):
        self.assertIn('{"title": "...", "style": "...", "lyrics": "..."}', self.block)
        self.assertIn("Answer with ONE JSON object and nothing else", self.block)

    def test_the_object_is_read_out_of_whatever_wraps_it(self):
        self.assertIn('i, j = text.find("{"), text.rfind("}")', self.block)

    def test_an_instrumental_brief_asks_for_no_words_at_all(self):
        self.assertIn("This one has no singing at all", self.block)
        self.assertIn('lyrics = "" if instrumental else', self.block)

    def test_a_brief_with_no_style_is_a_failure_not_a_blank_field(self):
        self.assertIn("raise ValueError(\"the model returned no style\")", self.block)

