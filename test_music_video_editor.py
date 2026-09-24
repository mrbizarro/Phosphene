#!/usr/bin/env python3
"""A music video opened in the Editor keeps its plan and its song (SB5-12).

The planner stores the master track at `board["music_video"]["song"]` and
places every shot at a planned film second; each singing (a2v) shot was
rendered against exactly that stretch of the song. The Editor's first GET
used to run the ordinary auto-editor over it — no song, every shot re-cut —
and the edit it wrote is also what `/music/video/film` renders afterwards.

Driven through the real Editor GET on a scratch board; ffprobe is mocked and
the auto-editor is made to fail loudly if it is reached at all.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_edit                                               # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402
from test_storyboard_editor_api import EditorCase, FakeHandler       # noqa: E402

# Singing / B-roll / singing, at the seconds the planner laid them out.
PLAN = [(1, "singing", 0.0, 4.25), (2, "broll", 4.25, 9.5),
        (3, "singing", 9.5, 13.0)]


class MusicVideoOpensOnItsPlan(EditorCase):

    def setUp(self):
        super().setUp()
        self.song = self.root / "song.wav"
        self.song.write_bytes(b"RIFF")
        shots = []
        for (n, kind, fs, fe), clip in zip(PLAN, self.clips):
            shots.append({
                "n": n, "mode": "image", "engine": "ltx", "prompt": f"shot {n}",
                "duration_s": fe - fs, "seed": n, "refs": [], "status": "done",
                "draft_output": str(clip),
                "music_video": {"kind": kind, "film_start": fs, "film_end": fe,
                                "panel_mode": "a2v" if kind == "singing" else "i2v"}})
        board = dict(self.board, shots=shots,
                     music_video={"song": str(self.song), "film_seconds": 13.0})
        storyboard.save_storyboard(self.state, board)
        # File durations: shot 2 came out one frame short, as renders do.
        self.durations = {str(self.clips[0]): 4.25, str(self.clips[1]): 5.25 - 1 / 24,
                          str(self.clips[2]): 3.5, str(self.song): 13.0}

    def _get(self):
        def probe(path):
            d = self.durations.get(str(path))
            return {"duration": d, "w": 768, "h": 416, "has_audio": True} if d else None

        def no_recut(*a, **k):
            raise AssertionError("the auto-editor re-cut a music video")

        with mock.patch.object(storyboard_edit, "probe_media", side_effect=probe), \
             mock.patch.object(storyboard_edit, "plan_cut", side_effect=no_recut):
            return FakeHandler().get("/storyboard/edit?id=sb_t")

    def test_every_shot_sits_where_the_plan_put_it_whole(self):
        h = self._get()
        self.assertEqual(h.status, 200, h.payload)
        clips = sorted(h.payload["edit"]["clips"], key=lambda c: c["film_start"])
        self.assertEqual([round(c["film_start"], 4) for c in clips], [0.0, 4.25, 9.5])
        self.assertEqual([c["start"] for c in clips], [0.0, 0.0, 0.0])
        # Whole shots — trimmed only where the file is shorter than planned.
        self.assertEqual([round(c["end"], 4) for c in clips],
                         [4.25, round(5.25 - 1 / 24, 4), 3.5])

    def test_the_song_is_the_bed_replacing_the_clips_at_unity(self):
        audio = self._get().payload["edit"]["audio"]
        self.assertEqual(audio["path"], str(self.song))
        self.assertEqual(audio["mode"], "replace")
        self.assertEqual(float(audio.get("offset") or 0.0), 0.0)
        mix = sedit.audio_mix(audio)
        self.assertEqual((mix["bed_gain"], mix["duck"]), (1.0, False))

    def test_the_render_plan_carries_the_song_for_the_whole_film(self):
        self._get()
        edit = sedit.load_edit(self.bdir)
        win = sedit.music_window(edit["audio"])
        self.assertEqual((win["start"], win["film_start"]), (0.0, 0.0))
        self.assertIsNone(win["end"])
        cuts = sedit.edit_to_cuts(edit)
        self.assertEqual([round(c["film_start"], 4) for c in cuts], [0.0, 4.25, 9.5])

    def test_an_ordinary_board_still_gets_the_auto_editor(self):
        board = dict(self.board)
        board.pop("music_video", None)
        storyboard.save_storyboard(self.state, board)
        with mock.patch.object(storyboard_edit, "plan_cut",
                               return_value=[]) as pc, \
             mock.patch.object(storyboard_edit, "probe_media", return_value=None):
            FakeHandler().get("/storyboard/edit?id=sb_t")
        pc.assert_called_once()


if __name__ == "__main__":
    unittest.main()
