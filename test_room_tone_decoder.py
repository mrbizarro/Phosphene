#!/usr/bin/env python3
"""Room tone "From this film" decodes with the panel's own ffmpeg (M6-08).

The panel resolves ffmpeg at boot, including Pinokio's bundled tool folders
that need not be on the process PATH. `room_tone` searched only PATH and two
Homebrew paths, so on such a Mac it could not start a decoder, read that as
"no clip with usable quiet sound", and bedded the film with a preset (or, on
the automatic cut, with nothing) while blaming the clips.

Scratch OUTPUT only; the decoder process is mocked, nothing is decoded.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import room_tone as rt                                               # noqa: E402


class RoomToneUsesThePanelsFfmpeg(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out = root / "outputs"
        self.out.mkdir()
        # The only ffmpeg on this "Mac": a bundled one the panel found.
        self.bundled = root / "pinokio-bin" / "ffmpeg"
        self.bundled.parent.mkdir()
        self.bundled.write_text("#!/bin/sh\nexit 0\n")
        self.bundled.chmod(0o755)
        self.clip = root / "S01.mp4"
        self.clip.write_bytes(b"x")
        self.board = {"id": "sb_20231115_aaaaaa", "title": "Room",
                      "created_at": 1_700_050_000, "shots": []}
        for p in (mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "FFMPEG", self.bundled),
                  mock.patch.object(panel, "push", lambda *a, **k: None),
                  mock.patch.object(rt, "FFMPEG", None),
                  mock.patch.dict("os.environ", {"PHOSPHENE_FFMPEG": ""}),
                  mock.patch.object(rt.shutil, "which", return_value=None)):
            p.start()
            self.addCleanup(p.stop)

    def _bed(self, run, strict=True):
        with mock.patch.object(rt.subprocess, "run", side_effect=run):
            return panel._sbe_room_tone(
                self.board, variant="film", seed=1, film_len=10.0, strict=strict,
                clips=[{"path": str(self.clip), "start": 0.0, "end": 5.0}])

    def test_the_decoder_is_the_one_the_panel_resolved(self):
        seen = []

        def run(cmd, **kw):
            seen.append(cmd[0])
            return subprocess.CompletedProcess(cmd, 0, stdout=b"", stderr=b"")

        self._bed(run)
        self.assertEqual(seen, [str(self.bundled)])

    def test_a_decoder_that_cannot_start_is_said_as_that(self):
        def run(cmd, **kw):
            raise FileNotFoundError(2, "No such file or directory", cmd[0])

        facts = self._bed(run)
        self.assertFalse(facts["ok"])
        self.assertIn("ffmpeg could not be started", facts["error"])
        self.assertNotIn("no clip with usable quiet sound", facts["error"])

    def test_by_hand_it_falls_back_but_does_not_cache_the_fallback(self):
        def run(cmd, **kw):
            raise PermissionError(13, "Permission denied", cmd[0])

        facts = self._bed(run, strict=False)
        self.assertTrue(facts["ok"])
        self.assertIn("could not be read", facts["fallback"])
        # The next pick — with a decoder that works — must not be answered
        # by a cached preset that was only ever a symptom.
        self.assertEqual(list(Path(facts["path"]).parent.glob("*.json")), [])

    def test_a_silent_clip_is_still_called_silent(self):
        def run(cmd, **kw):
            return subprocess.CompletedProcess(cmd, 0, stdout=b"\0\0" * 48000 * 5,
                                               stderr=b"")

        facts = self._bed(run)
        self.assertIn("no clip with usable quiet sound", facts["error"])


if __name__ == "__main__":
    unittest.main()
