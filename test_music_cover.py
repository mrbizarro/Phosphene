"""Cover: a real recording in, a new song out.

The engine has been able to do this since the pin — `lyra cover` transcribes a
mix into an ABC score with SheetSage2 and hands that score to the generator as
the plan. Phosphene exposed none of it. This is the wiring, and these are the
rules that keep it honest:

  * the two transcription models are an OPT-IN ~2.8 GB, so a cover asked for
    without them must fail at the form, not ten minutes into a job;
  * the transcription task decides the planning mode, because a full
    transcription can only be realised by full planning;
  * a supplied ABC file and a source recording are the same input arriving two
    ways, so asking for both is a refusal rather than a silent winner;
  * transcription shells out to ffmpeg, which on a Pinokio-started panel is not
    on PATH — the same trap that cost five installs their exports in 4.15.1.
"""
import subprocess
import sys
import unittest
from unittest import mock
from pathlib import Path

import mlx_ltx_panel as P

RUNNER = Path(__file__).with_name("scripts") / "music" / "yue2_run.py"


class TheFormCarriesIt(unittest.TestCase):

    def test_a_source_song_survives_the_allowlist(self):
        p = P.music_params({"music_style": "dream pop",
                            "music_source_audio": "/tmp/a song.wav"})
        self.assertEqual(p["music_source_audio"], "/tmp/a song.wav")

    def test_a_pasted_finder_path_is_cleaned(self):
        """Same treatment as every other path field: the field is in
        PASTED_PATH_FIELDS, so a dragged path with a tilde and stray spaces
        is a path by the time a gate looks at it."""
        self.assertIn("music_source_audio", P.PASTED_PATH_FIELDS)
        p = P.music_params({"music_source_audio": "  ~/Music/x.wav  "})
        self.assertTrue(p["music_source_audio"].startswith("/"))
        self.assertTrue(p["music_source_audio"].endswith("Music/x.wav"))

    def test_no_source_song_is_the_ordinary_job(self):
        p = P.music_params({"music_style": "dream pop"})
        self.assertEqual(p["music_source_audio"], "")

    def test_the_task_is_a_closed_vocabulary(self):
        for bad in ("", "everything", "../../etc"):
            p = P.music_params({"music_cover_task": bad})
            self.assertEqual(p["music_cover_task"], "melody-full")
        for good in ("full", "melody-full", "melody-vocal"):
            p = P.music_params({"music_cover_task": good})
            self.assertEqual(p["music_cover_task"], good)


class TheArgvSaysIt(unittest.TestCase):

    def argv(self, form):
        job = {"id": "j-1", "params": form}
        paths = {"python": Path("/x/python"), "runner": RUNNER,
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        return P.music_argv(job, paths, Path("/tmp/out.wav"))

    def test_a_prompt_job_passes_no_cover_flags(self):
        argv = self.argv({"music_style": "dream pop"})
        self.assertNotIn("--source-audio", argv)

    def test_a_cover_job_passes_the_source_the_task_and_both_models(self):
        argv = self.argv({"music_style": "dream pop",
                          "music_source_audio": "/tmp/song.mp3",
                          "music_cover_task": "melody-vocal"})
        self.assertEqual(argv[argv.index("--source-audio") + 1], "/tmp/song.mp3")
        self.assertEqual(argv[argv.index("--cover-task") + 1], "melody-vocal")
        self.assertTrue(argv[argv.index("--transcription-model") + 1].endswith("sheetsage2"))
        self.assertTrue(argv[argv.index("--transcription-base-model") + 1].endswith("mert2"))


class TheRunnerRefusesTheImpossible(unittest.TestCase):
    """Driven as a subprocess against the real script: these are argument
    rules, so they answer before any model is loaded."""

    def run_runner(self, *extra):
        out = subprocess.run(
            [sys.executable, str(RUNNER), "--model-dir", "/x", "--vae-dir", "/x",
             "--output", "/tmp/never-written.wav", "--style", "pop", *extra],
            capture_output=True, text=True, timeout=120)
        return out.returncode, out.stdout + out.stderr

    def test_a_missing_source_song_is_named(self):
        rc, out = self.run_runner("--source-audio", "/tmp/definitely-not-here.mp3")
        self.assertEqual(rc, 2)
        self.assertIn("source song not found", out)

    def test_a_score_and_a_recording_together_are_refused(self):
        with open("/tmp/cover_test_score.abc", "w") as fh:
            fh.write("X:1\n")
        rc, out = self.run_runner("--source-audio", "/tmp/cover_test_score.abc",
                                  "--abc-file", "/tmp/cover_test_score.abc")
        self.assertEqual(rc, 2)
        self.assertIn("drop the ABC file", out)

    def test_the_task_choices_are_the_engines_own(self):
        rc, out = self.run_runner("--cover-task", "melody")
        self.assertNotEqual(rc, 0)
        self.assertIn("invalid choice", out.lower())


class TheModelsAreOptional(unittest.TestCase):

    def test_status_reports_what_is_missing_rather_than_a_boolean(self):
        st = P.music_cover_status()
        self.assertIn("ready", st)
        self.assertIn("missing", st)
        self.assertIn("bytes", st)
        if not st["ready"]:
            self.assertTrue(all("/" in m for m in st["missing"]))

    def test_cover_readiness_rides_on_music_status(self):
        """The Compose card reads one payload."""
        self.assertIn("cover", P.music_status())

    def test_the_fetcher_pins_the_revisions_the_engine_pins(self):
        """SheetSage2's config carries the sha256 of the MERT checkpoint it was
        trained against and refuses to load beside any other one."""
        from scripts.pinokio.music_cover_fetch import SOURCES
        model = (Path(__file__).with_name("yue2-mlx") / "src" / "lyra" /
                 "transcription" / "model.py").read_text()
        self.assertIn(SOURCES["sheetsage2"]["revision"], model)
        self.assertIn(SOURCES["mert2"]["revision"], model)


class AnInterruptedCopyIsNeverReady(unittest.TestCase):
    """Codex INST-10: the cover models were copied straight onto their final
    names and "ready" meant "nonzero size", so a copy cut short left a
    truncated checkpoint that readiness accepted and every retry skipped.
    Fake HF: nothing is downloaded, everything lives in a temp dir."""

    def setUp(self):
        import sys as _sys
        import tempfile
        import types
        from scripts.pinokio import music_cover_fetch as mcf
        self.mcf = mcf
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.root = base / "yue2-cover"
        self.cache = base / "hf"
        self.cache.mkdir()
        self.payload = {n: (n * 50).encode() for n in mcf.FILES + mcf.EXTRA}

        def fake_download(repo_id, revision, filename):
            f = self.cache / repo_id.replace("/", "_") / filename
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(self.payload[filename])
            return str(f)
        self._hub = _sys.modules.get("huggingface_hub")
        _sys.modules["huggingface_hub"] = types.SimpleNamespace(hf_hub_download=fake_download)

    def tearDown(self):
        import sys as _sys
        if self._hub is not None:
            _sys.modules["huggingface_hub"] = self._hub
        else:
            _sys.modules.pop("huggingface_hub", None)
        self.tmp.cleanup()

    def test_a_copy_cut_short_stays_not_ready_and_the_retry_repairs_it(self):
        mcf = self.mcf
        real = mcf.shutil.copyfile

        def dies_half_way(src, dst):
            Path(dst).write_bytes(Path(src).read_bytes()[:7])
            raise OSError(28, "No space left on device")
        with mock.patch.object(mcf.shutil, "copyfile", dies_half_way):
            with self.assertRaises(OSError):
                mcf.fetch(self.root, min_free_gb=0)
        self.assertTrue(mcf.cover_problems(self.root), "a truncated copy read as ready")
        self.assertEqual(real, mcf.shutil.copyfile)
        mcf.fetch(self.root, min_free_gb=0)
        self.assertEqual(mcf.cover_problems(self.root), [])
        for part in mcf.SOURCES:
            for n in mcf.FILES:
                self.assertEqual((self.root / part / n).read_bytes(), self.payload[n])

    def test_a_truncated_file_from_the_old_copier_is_replaced(self):
        mcf = self.mcf
        for part in mcf.SOURCES:
            for n in mcf.FILES:
                f = self.root / part / n
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b"trunc")           # nonzero, and no receipt
        self.assertTrue(mcf.cover_problems(self.root))
        mcf.fetch(self.root, min_free_gb=0)
        self.assertEqual((self.root / "mert2" / "model.safetensors").read_bytes(),
                         self.payload["model.safetensors"])

    def test_a_finished_install_from_before_the_size_receipt_stays_ready(self):
        mcf = self.mcf
        for part in mcf.SOURCES:
            for n in mcf.FILES:
                f = self.root / part / n
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(self.payload[n])
        (self.root / "pack_source.json").write_text('{"sources": {}, "files": []}')
        self.assertEqual(mcf.cover_problems(self.root), [])


class FfmpegReachesTheChild(unittest.TestCase):

    def test_the_music_child_gets_the_resolved_ffmpeg_on_path(self):
        """Transcription builds an ffmpeg argv and reads f32le off its stdout.
        Pinokio's bundled binary is not on the default PATH."""
        env = P.music_child_env({"PATH": "/usr/bin"})
        self.assertTrue(env["PATH"].startswith(str(P.FFMPEG_BIN)))

    def test_the_mps_strip_still_happens(self):
        env = P.music_child_env({"PYTORCH_ENABLE_MPS_FALLBACK": "1", "PATH": "/usr/bin"})
        self.assertNotIn("PYTORCH_ENABLE_MPS_FALLBACK", env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
