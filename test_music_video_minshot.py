"""No planned shot is shorter than a second — the board writer would stretch it.

Codex review, 2026-09-20: the planner allowed a 9-frame scrap (0.375 s) at the
end of a section; `_sb_normalize` then raised it to 1 s while every singing
shot after it kept its planned `audio_start_time`, so the singer appeared at
11.08 s singing the words from 10.42 s. A scrap now folds into the B-roll shot
before it, and nothing the planner writes is ever below MIN_SHOT_S.
"""
import math
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

import music_video as mv
import storyboard_edit as sedit

IMAGES = [
    {"path": "/tmp/mv/singer_a.png", "role": "singer"},
    {"path": "/tmp/mv/piano.png", "role": "instrument"},
    {"path": "/tmp/mv/room.png", "role": "room"},
]


def click_track(path: Path, seconds: float, bpm: float = 120.0, sr: int = 44100):
    """A metronome: a short decaying click on every beat, louder on the one."""
    n = int(seconds * sr)
    y = np.zeros(n, dtype=np.float32)
    per = 60.0 / bpm
    t = 0.0
    k = 0
    while t < seconds:
        i = int(t * sr)
        length = min(n - i, int(0.03 * sr))
        if length <= 0:
            break
        env = np.exp(-np.arange(length) / (0.004 * sr))
        y[i:i + length] += (0.9 if k % 4 == 0 else 0.5) * env * np.sin(2 * np.pi * 1000 * np.arange(length) / sr)
        t += per
        k += 1
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1); fh.setsampwidth(2); fh.setframerate(sr)
        fh.writeframes((np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes())


class NothingUnderASecond(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.song = Path(cls.tmp.name) / "click.wav"
        click_track(cls.song, 40.0)
        cls.beats = sedit.beat_map(cls.song)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def plan(self, sections):
        return mv.plan_music_video(sections, IMAGES, style="x", bpm_grid=self.beats,
                                   song=str(self.song), title="t", board_id="sb_test")

    def shots(self, board):
        return board["shots"] if isinstance(board, dict) and "shots" in board else board["board"]["shots"]

    def test_a_scrap_at_a_section_end_folds_into_the_shot_before_it(self):
        # 5.375 s of B-roll cell + a 0.375 s scrap used to be two shots.
        board = self.plan([{"start": 0.0, "end": 5.75, "kind": "instrumental", "name": "intro"},
                           {"start": 5.75, "end": 30.0, "kind": "vocal", "name": "verse"}])
        for s in self.shots(board):
            self.assertGreaterEqual(float(s["duration_s"]), mv.MIN_SHOT_S - 1e-6, s)
            self.assertGreaterEqual(int(s["music_video"]["frames"]), 25, s)

    def test_the_singer_still_starts_where_the_film_has_reached(self):
        """The whole point of the cursor: a singing shot's audio offset is the
        film time at which it begins, after every fold before it."""
        board = self.plan([{"start": 0.0, "end": 5.75, "kind": "instrumental", "name": "intro"},
                           {"start": 5.75, "end": 30.0, "kind": "vocal", "name": "verse"}])
        cursor = 0.0
        for s in self.shots(board):
            if s["music_video"]["kind"] == "singing":
                self.assertAlmostEqual(float(s["audio_start_time"]), cursor, places=3)
            cursor += s["music_video"]["frames"] / float(mv.FPS)

    def test_a_song_of_only_scraps_still_gets_a_whole_second(self):
        board = self.plan([{"start": 0.0, "end": 0.4, "kind": "instrumental", "name": "blip"}])
        shots = self.shots(board)
        self.assertEqual(len(shots), 1)
        self.assertGreaterEqual(float(shots[0]["duration_s"]), mv.MIN_SHOT_S - 1e-6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
