#!/usr/bin/env python3
"""A finished film is a thing the panel must not lose, and must not share.

  * SB5-05  the film folder was `<day>_<title slug>`: two boards made the same
            day with the same title — or any two with non-Latin titles, which
            slug to "shot" — wrote into one folder and overwrote each other's
            film, shot copies and manifests.
  * SB5-06  the encoder wrote straight over the last good film and deleted it
            on failure, so a failed or cancelled re-render lost the film that
            had already worked.
  * SB5-13  a still (or a black slug) with a fade previewed the fade and
            rendered a hard cut: the segment builder dropped its `fx`.

Everything runs against a scratch OUTPUT/STATE_DIR; ffmpeg and ffprobe are
mocked. Nothing here reaches the live state or output tree.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard                                                    # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402

DAY = 1_700_050_000          # 2023-11-15 midday UTC: one date in every zone ±11h


def _board(bid, title, created=DAY):
    return {"schema": 1, "id": bid, "title": title, "created_at": created,
            "policy": storyboard.default_policy(), "cast": [],
            "engine_mode": "ltx", "shots": []}


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.state, self.out = root / "state", root / "outputs"
        self.state.mkdir()
        self.out.mkdir()
        for p in (mock.patch.object(panel, "STATE_DIR", self.state),
                  mock.patch.object(panel, "OUTPUT", self.out),
                  mock.patch.object(panel, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def save(self, board):
        storyboard.save_storyboard(self.state, board)
        return board


class FilmFolderBelongsToOneBoard(Sandbox):
    """SB5-05."""

    def test_same_day_same_title_is_two_folders_and_two_films(self):
        a = self.save(_board("sb_20231115_aaaaaa", "Night Drive"))
        b = self.save(_board("sb_20231115_bbbbbb", "Night Drive"))
        da, db = panel._sb_film_dir(a), panel._sb_film_dir(b)
        self.assertNotEqual(da, db)
        self.assertNotEqual(da / panel._sb_film_name(a),
                            db / panel._sb_film_name(b))

    def test_japanese_titles_do_not_collapse_into_one_folder(self):
        a = self.save(_board("sb_20231115_111111", "京都の夜"))
        b = self.save(_board("sb_20231115_222222", "東京の朝"))
        self.assertNotEqual(panel._sb_film_dir(a), panel._sb_film_dir(b))

    def test_the_first_six_words_matching_is_not_identity(self):
        t = "one two three four five six"
        a = self.save(_board("sb_20231115_333333", t + " seven"))
        b = self.save(_board("sb_20231115_444444", t + " eight"))
        self.assertNotEqual(panel._sb_film_dir(a), panel._sb_film_dir(b))

    def test_a_board_that_already_exported_keeps_its_folder(self):
        # MIGRATION: an Export wrote storyboard.json (with the board's id)
        # into the old-style folder. That board keeps it, so its films stay
        # listed; a newcomer with the same day and title does not get it.
        a = self.save(_board("sb_20231115_aaaaaa", "Night Drive"))
        legacy = self.out / "storyboards" / panel._sb_film_dir(a).name
        legacy = legacy.parent / legacy.name.rsplit("_", 1)[0]
        legacy.mkdir(parents=True)
        (legacy / "storyboard.json").write_text(json.dumps(a))
        (legacy / "night-drive_film.mp4").write_bytes(b"A's film")
        b = self.save(_board("sb_20231115_bbbbbb", "Night Drive", DAY - 3600))
        self.assertEqual(panel._sb_film_dir(a), legacy)
        self.assertNotEqual(panel._sb_film_dir(b), legacy)
        self.assertEqual([f["name"] for f in panel._sb_films(a, probe=False)],
                         ["night-drive_film.mp4"])
        self.assertEqual(panel._sb_films(b, probe=False), [])

    def test_an_unmarked_old_folder_goes_to_the_oldest_board_with_that_name(self):
        # Timeline renders wrote no manifest, so the folder says nothing about
        # whose it is. The board made first made it.
        a = self.save(_board("sb_20231115_aaaaaa", "Night Drive", DAY))
        b = self.save(_board("sb_20231115_bbbbbb", "Night Drive", DAY + 60))
        legacy = self.out / "storyboards" / (
            panel._sb_film_dir(b).name.rsplit("_", 1)[0])
        legacy.mkdir(parents=True)
        (legacy / "night-drive_film.mp4").write_bytes(b"somebody's film")
        self.assertEqual(panel._sb_film_dir(a), legacy)
        self.assertNotEqual(panel._sb_film_dir(b), legacy)

    def test_writing_a_film_marks_the_folder_as_this_boards(self):
        a = self.save(_board("sb_20231115_aaaaaa", "Night Drive", DAY))
        d = panel._sb_film_dir_for_write(a)
        self.assertTrue(d.is_dir())
        self.assertEqual(panel._sb_film_owner(d), a["id"])

    def test_a_board_alone_with_its_old_folder_keeps_it(self):
        a = self.save(_board("sb_divorce_v2", "Divorce"))
        legacy = self.out / "storyboards" / (
            panel._sb_film_dir(a).name.rsplit("_", 1)[0])
        # (The scoped name for a non-minted id still starts with the legacy
        # one; that is what rsplit relies on.)
        self.assertTrue(panel._sb_film_dir(a).name.startswith(legacy.name))
        legacy.mkdir(parents=True)
        self.assertEqual(panel._sb_film_dir(a), legacy)


def _probe(w, h, duration, *, audio=True, rate=48000):
    return {"w": w, "h": h, "duration": duration,
            "has_audio": audio, "sample_rate": rate if audio else 0}


class AFailedRenderKeepsTheLastFilm(unittest.TestCase):
    """SB5-06."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.clip = self.dir / "a.mp4"
        self.clip.write_bytes(b"clip")
        self.film = self.dir / "night-drive_film.mp4"
        self.film.write_bytes(b"THE GOOD FILM")
        panel._sb_film_sidecar(self.film).write_text('{"film": "good"}')

    def _assemble(self, ffmpeg):
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value=_probe(640, 448, 3.0)), \
             mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=ffmpeg), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            return panel._sb_assemble_film([self.clip], self.film)

    def _leftovers(self):
        return sorted(p.name for p in self.dir.iterdir()
                      if p.name not in {"a.mp4", self.film.name,
                                        self.film.name + ".json"})

    def test_a_crash_after_partial_bytes_keeps_the_old_film_and_sidecar(self):
        def half(cmd, label):
            Path(cmd[-1]).write_bytes(b"half a film")
            raise RuntimeError("exited with code 1")
        res = self._assemble(half)
        self.assertFalse(res["ok"])
        self.assertEqual(self.film.read_bytes(), b"THE GOOD FILM")
        self.assertEqual(panel._sb_film_sidecar(self.film).read_text(),
                         '{"film": "good"}')
        self.assertEqual(self._leftovers(), [])

    def test_a_failure_before_any_bytes_keeps_the_old_film(self):
        def dead(cmd, label):
            raise RuntimeError("cancelled")
        self.assertFalse(self._assemble(dead)["ok"])
        self.assertEqual(self.film.read_bytes(), b"THE GOOD FILM")
        self.assertEqual(self._leftovers(), [])

    def test_an_encoder_that_writes_nothing_keeps_the_old_film(self):
        self.assertFalse(self._assemble(lambda c, l: ("", ""))["ok"])
        self.assertEqual(self.film.read_bytes(), b"THE GOOD FILM")
        self.assertEqual(self._leftovers(), [])

    def test_a_good_render_replaces_it(self):
        def ok(cmd, label):
            # Never the finished name while it is being written.
            self.assertNotEqual(Path(cmd[-1]), self.film)
            self.assertEqual(Path(cmd[-1]).suffix, ".mp4")
            Path(cmd[-1]).write_bytes(b"THE NEW FILM")
            return "", ""
        res = self._assemble(ok)
        self.assertTrue(res["ok"])
        self.assertEqual(res["path"], str(self.film))
        self.assertEqual(self.film.read_bytes(), b"THE NEW FILM")
        self.assertEqual(self._leftovers(), [])

    def test_the_film_list_never_shows_a_film_still_being_written(self):
        board = _board("sb_20231115_aaaaaa", "Night Drive")
        with mock.patch.object(panel, "_sb_film_dir", return_value=self.dir):
            (self.dir / ".night-drive_film.part-1234abcd.mp4").write_bytes(b"x")
            names = [f["name"] for f in panel._sb_films(board, probe=False)]
        self.assertIn(self.film.name, names)
        self.assertFalse([n for n in names if n.startswith(".")], names)



class StillFadesReachTheEncoder(unittest.TestCase):
    """SB5-13 — the same fade on a still, a slug and a video, end to end."""

    def _segments(self):
        fx = {"fade_in": 1.0, "fade_out": 0.5}
        edit = {"version": sedit.EDIT_VERSION, "board_id": "sb_t", "revision": 1,
                "source": "human", "audio": None, "beats": None, "settings": {},
                "clips": [
                    dict(sedit.new_clip("/x/card.png", 0.0, 3.0, 0.0,
                                        source="human"), kind="still", fx=fx),
                    dict(sedit.new_clip("", 0.0, 2.0, 3.0, source="human"),
                         kind="slug", path=None, fx=fx),
                    dict(sedit.new_clip("/x/a.mp4", 0.0, 4.0, 5.0,
                                        source="human", duration=10.0), fx=fx),
                ]}
        cuts = sedit.edit_to_cuts(sedit.normalise_edit(edit))
        self.assertEqual([c.get("fx") for c in cuts], [fx] * 3)
        info = {"w": 768, "h": 416, "duration": 10.0, "has_audio": False,
                "sample_rate": 0}
        with mock.patch.object(panel, "_sb_probe_still", return_value=dict(info)), \
             mock.patch.object(panel, "_sb_probe_clip", return_value=dict(info)):
            segs, unreadable, _ = panel._sb_timeline_segments(cuts)
        self.assertEqual(unreadable, [])
        return segs

    def test_every_kind_of_segment_keeps_its_fade(self):
        segs = self._segments()
        self.assertEqual([s["kind"] for s in segs], ["still", "slug", "video"])
        for s in segs:
            self.assertEqual(s.get("fx"), {"fade_in": 1.0, "fade_out": 0.5},
                             s["kind"])

    def test_the_graph_fades_the_still(self):
        segs = self._segments()
        with mock.patch.object(panel, "bt709_vf", return_value=""):
            g, _ = panel._sb_film_filtergraph([], 768, 416, 48000, "yuv420p",
                                              segments=segs)
        still_chain = next(c for c in g.split(";") if c.startswith("[0:v]"))
        self.assertIn("fade=t=in", still_chain)
        self.assertIn("fade=t=out", still_chain)


if __name__ == "__main__":
    unittest.main()
