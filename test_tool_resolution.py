"""ffmpeg and ffprobe must be found where PINOKIO actually puts them.

The fleet bug, 2026-09-20: five installs across 4.5.0 -> 4.15.0 failed their
export with "ffmpeg not found on PATH". Every engine spawn already prepends
FFMPEG_BIN to the child's PATH, so the miss was one level up — the resolver
knew `pinokio/bin/ffmpeg-env` and nothing else, while Pinokio's bootstrap
installs `ffmpeg=8.1.2` into its CONDA prefix (`pinokio/bin/miniforge/bin`)
and unpacks a private Homebrew beside it. On a Mac with no system ffmpeg the
resolver returned a /usr/local path that does not exist.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import mlx_ltx_panel as P


class _FakePinokio:
    """A Pinokio home with a binary in one of its tool folders."""

    def __init__(self, rel, name="ffmpeg"):
        self.rel, self.name = rel, name

    def __enter__(self):
        self.tmp = TemporaryDirectory()
        home = Path(self.tmp.name)
        d = home / self.rel
        d.mkdir(parents=True)
        self.bin = d / self.name
        self.bin.write_text("#!/bin/sh\n")
        self.bin.chmod(0o755)
        self.prev = os.environ.get("PINOKIO_HOME")
        os.environ["PINOKIO_HOME"] = str(home)
        self.which = P.shutil.which
        P.shutil.which = lambda _n: None          # nothing on PATH
        return self

    def __exit__(self, *a):
        P.shutil.which = self.which
        if self.prev is None:
            os.environ.pop("PINOKIO_HOME", None)
        else:
            os.environ["PINOKIO_HOME"] = self.prev
        self.tmp.cleanup()


class WherePinokioPutsIt(unittest.TestCase):

    def test_the_conda_prefix_is_searched(self):
        """THE REGRESSION. `bin/miniforge/bin` is where Pinokio's own
        bootstrap log shows ffmpeg being installed."""
        with _FakePinokio("bin/miniforge/bin") as fake:
            self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), fake.bin)

    def test_the_older_miniconda_prefix_is_searched(self):
        with _FakePinokio("bin/miniconda/bin") as fake:
            self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), fake.bin)

    def test_the_bundled_homebrew_is_searched(self):
        """Pinokio unpacks its own Homebrew.zip into bin/homebrew."""
        with _FakePinokio("bin/homebrew/bin") as fake:
            self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), fake.bin)

    def test_the_original_ffmpeg_env_still_wins_first(self):
        """The one folder the old resolver knew must keep working."""
        with _FakePinokio("bin/ffmpeg-env/bin") as fake:
            self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), fake.bin)

    def test_an_explicit_override_beats_every_search(self):
        with _FakePinokio("bin/miniforge/bin") as fake:
            other = Path(fake.tmp.name) / "my-own-ffmpeg"
            other.write_text("#!/bin/sh\n")
            os.environ["LTX_FFMPEG"] = str(other)
            try:
                self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), other)
            finally:
                os.environ.pop("LTX_FFMPEG", None)

    def test_an_override_pointing_nowhere_does_not_win(self):
        """A stale LTX_FFMPEG in a shell profile must not disable the search."""
        with _FakePinokio("bin/miniforge/bin") as fake:
            os.environ["LTX_FFMPEG"] = "/nope/ffmpeg"
            try:
                self.assertEqual(P._resolve_tool("ffmpeg", "LTX_FFMPEG"), fake.bin)
            finally:
                os.environ.pop("LTX_FFMPEG", None)

    def test_nothing_found_still_returns_a_path(self):
        """Resolution never raises at import time — a missing binary must
        fail at the call site with a real message, not kill the panel."""
        with _FakePinokio("bin/miniforge/bin"):
            got = P._resolve_tool("definitely-not-a-real-tool", "LTX_NOPE")
            self.assertEqual(got.name, "definitely-not-a-real-tool")
            self.assertFalse(got.exists())

    def test_the_folder_list_is_deduped(self):
        """PINOKIO_HOME is normally ~/pinokio, so both passes find the same
        folders — and this list is printed to the user in the boot warning."""
        dirs = [str(d) for d in P._pinokio_bin_dirs()]
        self.assertEqual(len(dirs), len(set(dirs)))


class FfprobeTravelsSeparately(unittest.TestCase):

    def test_ffprobe_is_taken_from_beside_ffmpeg_when_it_is_there(self):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "ffmpeg").write_text("")
            (d / "ffprobe").write_text("")
            self.assertEqual(P._resolve_ffprobe(d / "ffmpeg"), d / "ffprobe")

    def test_ffprobe_is_searched_when_it_is_not_beside_ffmpeg(self):
        """The fleet's bare "ffprobe failed:" (3 installs). The old code
        assumed the sibling path existed and handed the caller a path that
        never did."""
        with _FakePinokio("bin/miniforge/bin", name="ffprobe") as fake:
            with TemporaryDirectory() as lonely:
                ffmpeg = Path(lonely) / "ffmpeg"
                ffmpeg.write_text("")
                self.assertEqual(P._resolve_ffprobe(ffmpeg), fake.bin)


if __name__ == "__main__":
    unittest.main(verbosity=2)
