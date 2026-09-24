"""The music-video planner: the song is the clock and the shots tile it.

Everything here runs with no weights and no GPU. The song is a click track
this file writes with numpy — a real file, decoded by the real ffmpeg, read by
the real `beat_map`, because the whole planner hangs off that grid and a
stubbed one would test the stub.

The rules being pinned:

  * a singing shot is a REAL LTX cell, never a length the sampler cannot
    render, and never longer than the singing window;
  * the shots tile the song — shot n+1 starts where shot n ended — and a
    singing shot's `audio_start_time` IS its place in that tiling, which is
    the one thing lip-sync depends on;
  * Singer pictures alternate, so two adjacent windows are not the same angle;
  * a song nobody sings in, and a cast with no face in it, both come back as
    B-roll with the reason written down rather than a silent half-plan.
"""
from __future__ import annotations

import json
import math
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np

import music_video as mv
import storyboard
import storyboard_edit as sedit

SR = 44100
BPM = 120.0


def click_track(path: Path, *, bars: int = 24, bpm: float = BPM,
                vocal_bars=()) -> None:
    """A 4/4 click track at `bpm`, with a voice-band tone over `vocal_bars`.

    The accent is an 80 Hz decaying kick and the bed a 220 Hz sine, so a bar
    with no tone has almost nothing in the 1–4 kHz band the classifier reads;
    a "vocal" bar adds 2.2 kHz on top. That separation is the point — it lets
    the band heuristic be tested for what it decides, not for how well it
    copes with pink noise.
    """
    beat = 60.0 / bpm
    total = bars * 4 * beat
    n = int(total * SR)
    t = np.arange(n, dtype=np.float64) / SR
    x = 0.15 * np.sin(2 * math.pi * 220.0 * t)
    for b in range(bars * 4):
        i0 = int(b * beat * SR)
        i1 = min(n, i0 + int(0.06 * SR))
        env = np.exp(-np.linspace(0.0, 8.0, i1 - i0))
        amp = 0.9 if b % 4 == 0 else 0.6
        x[i0:i1] += amp * env * np.sin(
            2 * math.pi * 80.0 * np.arange(i1 - i0) / SR)
    for bar in vocal_bars:
        i0 = int(bar * 4 * beat * SR)
        i1 = min(n, int((bar + 1) * 4 * beat * SR))
        x[i0:i1] += 0.45 * np.sin(2 * math.pi * 2200.0 * t[i0:i1])
    x = np.clip(x, -1.0, 1.0)
    with wave.open(str(path), "wb") as fh:
        fh.setnchannels(1)
        fh.setsampwidth(2)
        fh.setframerate(SR)
        fh.writeframes((x * 32000).astype("<i2").tobytes())


IMAGES = [
    {"path": "/tmp/mv/singer_a.png", "role": "singer"},
    {"path": "/tmp/mv/singer_b.png", "role": "singer"},
    {"path": "/tmp/mv/piano.png", "role": "instrument"},
    {"path": "/tmp/mv/room.png", "role": "room"},
]
STYLE = "handheld 16mm documentary, warm tungsten light"

# A 4/4 score at 120 BPM: one bar is exactly 2 seconds, so the arithmetic in
# the assertions below is readable rather than derived.
SCORE = """X:1
T:
M:4/4
L:1/8
Q:1/4=120
V: Vocal clef=treble
V: Ins clef=treble
K:C
% intro
V: Vocal
z8|z8|z8|z8|
V: Ins
Z4|
% verse
V: Vocal
C8|D8|E8|F8|G8|A8|B8|c8|
V: Ins
Z8|
% outro
V: Vocal
z8|z8|z8|z8|
V: Ins
Z2|Z2|
"""
LYRICS = "[Verse]\nsomething about the rain\nand the lights\n"


class TheScoreIsRead(unittest.TestCase):

    def test_bars_and_tempo_come_off_the_headers(self):
        rows, spb = mv.score_sections(SCORE)
        self.assertEqual([(r["name"], r["bars"]) for r in rows],
                         [("intro", 4), ("verse", 8), ("outro", 4)])
        self.assertAlmostEqual(spb, 2.0, places=6)

    def test_a_multi_measure_rest_is_as_many_bars_as_it_says(self):
        self.assertEqual(mv._count_bars("Z4|"), 4)
        self.assertEqual(mv._count_bars("Z|"), 1)
        self.assertEqual(mv._count_bars("Z2|z16e6g2a6e2|g16z6G2A4c4|"), 4)

    def test_a_bare_tag_is_an_instrumental_section(self):
        rows = mv.lyric_sections("[Intro]\n[Verse]\nwords here\n[Verse 2]\nmore\n")
        self.assertEqual([(r["name"], r["vocal"]) for r in rows],
                         [("intro", False), ("verse", True), ("verse", True)])

    def test_a_score_with_no_tempo_says_so_rather_than_guessing(self):
        rows, spb = mv.score_sections("X:1\nK:C\n% a\nV: V\nC8|D8|\n")
        self.assertEqual([r["bars"] for r in rows], [2])
        self.assertIsNone(spb)


class TheSongIsSectioned(unittest.TestCase):
    """The two paths: the score's arithmetic, and the band heuristic."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        # 24 bars = 48 s at 120 BPM. Bars 8..15 carry the voice band.
        cls.song = root / "click.wav"
        click_track(cls.song, bars=24, vocal_bars=range(8, 16))
        cls.instrumental = root / "instrumental.wav"
        click_track(cls.instrumental, bars=24, vocal_bars=())
        cls.beats = sedit.beat_map(cls.song)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_the_click_track_reads_as_the_tempo_it_was_written_at(self):
        # An octave error is the classic tempo failure and it would move every
        # downbeat, so the assertion is on the bar, not on a loose ratio.
        self.assertAlmostEqual(self.beats["bpm"], BPM, delta=2.0)

    def test_the_score_places_the_sections_to_the_second(self):
        rows = mv.song_sections(self.song, lyrics=LYRICS, score_abc=SCORE,
                                beats=self.beats)
        self.assertEqual([r["name"] for r in rows], ["intro", "verse", "outro"])
        self.assertEqual([r["kind"] for r in rows],
                         ["instrumental", "vocal", "instrumental"])
        self.assertAlmostEqual(rows[0]["start"], 0.0, places=6)
        # intro 4 bars = 8 s, verse 8 bars = 16 s; snapping to the measured
        # downbeat grid can move a boundary by less than half a bar.
        self.assertAlmostEqual(rows[0]["end"], 8.0, delta=1.0)
        self.assertAlmostEqual(rows[1]["end"], 24.0, delta=1.0)

    def test_the_sections_cover_the_song_with_no_gaps(self):
        for kwargs in ({"lyrics": LYRICS, "score_abc": SCORE}, {}):
            rows = mv.song_sections(self.song, beats=self.beats, **kwargs)
            for a, b in zip(rows, rows[1:]):
                self.assertEqual(a["end"], b["start"])
            self.assertEqual(rows[0]["start"], 0.0)
            self.assertAlmostEqual(rows[-1]["end"],
                                   self.beats["duration"], delta=0.05)

    def test_without_a_score_the_band_heuristic_finds_the_sung_phrase(self):
        rows = mv.song_sections(self.song, beats=self.beats)
        self.assertTrue(all(r["source"] == "phrase" for r in rows))
        sung = [r for r in rows if r["kind"] == "vocal"]
        self.assertTrue(sung, "the 2.2 kHz phrase should read as vocal")
        # Bars 8..15 = 16 s .. 32 s. Every phrase called vocal has to overlap
        # it; a classifier that said "all vocal" would fail here.
        for r in sung:
            self.assertLess(r["start"], 32.0)
            self.assertGreater(r["end"], 16.0)

    def test_a_track_with_no_voice_band_comes_back_instrumental(self):
        beats = sedit.beat_map(self.instrumental)
        rows = mv.song_sections(self.instrumental, beats=beats)
        self.assertEqual({r["kind"] for r in rows}, {"instrumental"})

    def test_a_score_longer_than_the_recording_is_clamped_not_believed(self):
        # 40 bars at 2 s = 80 s of score against a 48 s file.
        long_score = SCORE.replace("% outro\nV: Vocal\nz8|z8|z8|z8|\nV: Ins\nZ2|Z2|\n",
                                   "% outro\nV: Vocal\n" + "z8|" * 28 + "\n")
        rows = mv.song_sections(self.song, lyrics=LYRICS,
                                score_abc=long_score, beats=self.beats)
        self.assertLessEqual(rows[-1]["end"], self.beats["duration"] + 0.05)
        self.assertLessEqual(len(rows), 3)

    def test_the_tempo_disagreement_is_a_sentence_not_a_refusal(self):
        self.assertIsNone(mv.tempo_disagreement(self.beats, SCORE))
        # 120 written against 120 measured is silent; 60 written is not.
        half = SCORE.replace("Q:1/4=120", "Q:1/4=75")
        note = mv.tempo_disagreement(self.beats, half)
        self.assertIsNotNone(note)
        self.assertIn("75", note)


class TheDurationAxis(unittest.TestCase):

    def test_the_module_default_is_the_panel_table(self):
        # The planner may not import the panel, so it carries the seconds and
        # derives the frames. This is the assertion that keeps the two honest.
        import mlx_ltx_panel as P
        self.assertEqual(mv.LTX_CELLS, mv.cells_from_lengths(P.LTX_LENGTHS))

    def test_every_cell_is_a_frame_count_the_sampler_accepts(self):
        for secs, frames in mv.LTX_CELLS:
            self.assertEqual((frames - 1) % 8, 0)
            self.assertEqual(frames, storyboard.ltx_frames_for(secs))

    def test_the_largest_cell_that_fits_is_the_one_chosen(self):
        self.assertEqual(mv._cell_for(20.0, mv.LTX_CELLS), (20, 481))
        self.assertEqual(mv._cell_for(19.9, mv.LTX_CELLS), (10, 241))
        self.assertEqual(mv._cell_for(2.9, mv.LTX_CELLS), None)

    def test_the_restriction_table_is_the_panel_s_own(self):
        # The module mirrors the `qualities` column the same way it mirrors
        # the seconds, and for the same reason (no panel import). This is the
        # assertion that keeps the mirror honest.
        import mlx_ltx_panel as P
        panel = {int(l["seconds"]): tuple(l["qualities"])
                 for l in P.LTX_LENGTHS.values() if l.get("qualities")}
        self.assertEqual(panel, mv.LTX_CELL_QUALITIES)

    def test_a_cell_this_canvas_cannot_render_is_not_on_the_axis(self):
        # 481 frames holds at 640×480 and dies around frame 454 at 1024×576,
        # so the panel offers the 20 s cell at Quick only — and the planner
        # has to read that column, not just the seconds.
        import mlx_ltx_panel as P
        std = mv.cells_from_lengths(P.LTX_LENGTHS, quality="standard")
        quick = mv.cells_from_lengths(P.LTX_LENGTHS, quality="quick")
        self.assertNotIn((20, 481), std)
        self.assertIn((20, 481), quick)
        self.assertEqual(max(f for _, f in std), 241)
        # Every other cell survives both.
        self.assertEqual(set(std), set(quick) - {(20, 481)})

    def test_an_axis_handed_in_by_hand_is_cut_down_too(self):
        kept, dropped = mv._cells_for_quality(mv.LTX_CELLS, "standard")
        self.assertEqual(dropped, ((20, 481),))
        self.assertNotIn((20, 481), kept)
        self.assertEqual(mv._cells_for_quality(mv.LTX_CELLS, "quick"),
                         (mv.LTX_CELLS, ()))
        # No quality stated is no opinion — the axis comes back untouched.
        self.assertEqual(mv._cells_for_quality(mv.LTX_CELLS, ""),
                         (mv.LTX_CELLS, ()))


class TheBrollSplit(unittest.TestCase):

    def test_the_cuts_land_on_the_downbeats(self):
        grid = [2.0 * i for i in range(12)]
        spans = mv._broll_spans(0.0, 20.0, window=(3.0, 7.0), grid=grid,
                                bar=2.0)
        self.assertAlmostEqual(sum(spans), 20.0, places=6)
        for s in spans:
            self.assertGreaterEqual(s, 3.0 - 1e-6)
            self.assertLessEqual(s, 7.0 + 1e-6)
        edge = 0.0
        for s in spans[:-1]:
            edge += s
            self.assertTrue(any(abs(edge - g) < 1e-6 for g in grid),
                            f"cut at {edge} is not on the bar grid")

    def test_a_scrap_shorter_than_the_window_is_one_short_shot_not_a_hole(self):
        self.assertEqual(mv._broll_spans(0.0, 1.4, window=(3.0, 7.0),
                                         grid=[], bar=0.0), [1.4])

    def test_nothing_left_makes_nothing(self):
        self.assertEqual(mv._broll_spans(5.0, 5.0, window=(3.0, 7.0),
                                         grid=[], bar=0.0), [])


class _Planned:
    """One click track and one set of sections, shared by every plan test.

    A mixin rather than a base TestCase on purpose: subclassing a TestCase to
    reuse its fixture re-runs all of its tests in every child.
    """

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.song = Path(cls.tmp.name) / "click.wav"
        click_track(cls.song, bars=24, vocal_bars=range(8, 16))
        cls.beats = sedit.beat_map(cls.song)
        cls.sections = [
            {"start": 0.0, "end": 16.0, "kind": "instrumental",
             "name": "intro", "source": "score"},
            {"start": 16.0, "end": 72.0, "kind": "vocal",
             "name": "verse", "source": "score"},
            {"start": 72.0, "end": 88.0, "kind": "instrumental",
             "name": "outro", "source": "score"},
        ]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def plan(self, sections=None, images=None, **kw):
        return mv.plan_music_video(
            sections if sections is not None else self.sections,
            IMAGES if images is None else images,
            style=STYLE, bpm_grid=self.beats, song=str(self.song),
            title="Test video", board_id="sb_test", **kw)


class ThePlan(_Planned, unittest.TestCase):

    def test_the_shots_tile_the_song(self):
        out = self.plan()
        shots = out["shots"]
        self.assertTrue(shots)
        cursor = 0.0
        for s in shots:
            block = s["music_video"]
            self.assertAlmostEqual(block["film_start"], cursor, places=3)
            cursor = block["film_end"]
            self.assertAlmostEqual(
                block["film_end"] - block["film_start"],
                s["duration_s"], places=3)
        self.assertAlmostEqual(cursor, out["film_seconds"], places=3)

    def test_a_singing_shot_is_rendered_against_its_own_second_of_the_song(self):
        for s in self.plan()["shots"]:
            if s["music_video"]["kind"] != "singing":
                continue
            self.assertEqual(s["mode"], "a2v")
            self.assertEqual(s["audio"], str(self.song))
            self.assertAlmostEqual(s["audio_start_time"],
                                   s["music_video"]["film_start"], places=3)

    def test_a_singing_window_never_exceeds_a_cell(self):
        cells = dict((f, s) for s, f in mv.LTX_CELLS)
        lo, hi = mv.SINGING_WINDOW
        for s in self.plan()["shots"]:
            if s["music_video"]["kind"] != "singing":
                continue
            frames = s["music_video"]["frames"]
            self.assertIn(frames, cells, "a singing shot off the cell table")
            self.assertLessEqual(frames / mv.FPS, hi + 1.0)
            self.assertGreaterEqual(frames / mv.FPS, lo - 1e-6)

    def test_broll_stays_inside_its_window(self):
        lo, hi = mv.BROLL_WINDOW
        spans = [s["duration_s"] for s in self.plan()["shots"]
                 if s["music_video"]["kind"] == "broll"]
        self.assertTrue(spans)
        # The floor is the one a scrap at the end of a section may break, and
        # it is allowed to: a hole in the tiling is worse than a 1 s shot.
        self.assertTrue(all(s <= hi + 1.0 for s in spans), spans)

    def test_the_singers_alternate(self):
        faces = [s["still"] for s in self.plan()["shots"]
                 if s["music_video"]["kind"] == "singing"]
        self.assertGreaterEqual(len(faces), 2)
        for a, b in zip(faces, faces[1:]):
            self.assertNotEqual(a, b, "two singing shots in a row, same angle")

    def test_the_plan_is_the_same_plan_twice(self):
        self.assertEqual(json.dumps(self.plan()["board"], sort_keys=True),
                         json.dumps(self.plan()["board"], sort_keys=True))

    def test_an_all_instrumental_song_is_all_broll(self):
        rows = [dict(r, kind="instrumental") for r in self.sections]
        out = self.plan(sections=rows)
        kinds = {s["music_video"]["kind"] for s in out["shots"]}
        self.assertEqual(kinds, {"broll"})
        self.assertIn("0 singing", out["summary"])

    def test_a_cast_with_no_face_is_all_broll_and_says_why(self):
        out = self.plan(images=[{"path": "/tmp/mv/piano.png",
                                 "role": "instrument"}])
        self.assertEqual({s["music_video"]["kind"] for s in out["shots"]},
                         {"broll"})
        self.assertTrue(any("Singer" in n for n in out["notes"]), out["notes"])

    def test_only_faces_still_makes_a_film_and_says_what_it_did(self):
        out = self.plan(images=[{"path": "/tmp/mv/singer_a.png",
                                 "role": "singer"}])
        kinds = {s["music_video"]["kind"] for s in out["shots"]}
        self.assertEqual(kinds, {"singing", "broll"})
        self.assertTrue(any("B-roll" in n for n in out["notes"]), out["notes"])
        # And a Singer picture used as B-roll must not be told to sing.
        for s in out["shots"]:
            if s["music_video"]["kind"] == "broll":
                self.assertNotIn("singing", s["prompt"])

    def test_the_summary_reads_like_a_sentence(self):
        out = self.plan()
        n_sing = sum(1 for s in out["shots"]
                     if s["music_video"]["kind"] == "singing")
        self.assertIn(f"{len(out['shots'])} shots", out["summary"])
        self.assertIn(f"{n_sing} singing", out["summary"])
        self.assertRegex(out["summary"], r"\d+:\d\d$")

    def test_every_prompt_carries_the_look_and_the_picture_s_own_line(self):
        for s in self.plan()["shots"]:
            self.assertIn(STYLE, s["prompt"])
        override = self.plan(images=[
            {"path": "/tmp/mv/singer_a.png", "role": "singer",
             "prompt": "he leans back and laughs"}])
        self.assertIn("he leans back and laughs",
                      override["shots"][0]["prompt"])

    def test_a_picture_with_no_role_is_broll_never_a_singer(self):
        out = self.plan(images=[{"path": "/tmp/mv/x.png"},
                                {"path": "/tmp/mv/singer_a.png",
                                 "role": "singer"}])
        cast = out["board"]["music_video"]["images"]
        self.assertEqual(cast[0]["role"], "room")

    def test_a_song_with_no_pictures_is_refused_with_a_sentence(self):
        with self.assertRaises(mv.MusicVideoError):
            self.plan(images=[])


class TheQualityDecidesTheDurationAxis(_Planned, unittest.TestCase):
    """The bug the first real film found: a 481-frame a2v shot planned onto a
    1024×576 board, where LTX dies around frame 454. The planner now knows
    which canvas it is planning for."""

    def test_at_standard_nothing_longer_than_the_canvas_allows_is_planned(self):
        # Default quality, stated nowhere — the conservative half of the
        # choice, because a missing cell costs a cut and a dead cell costs
        # the shot.
        for s in self.plan()["shots"]:
            self.assertLessEqual(s["music_video"]["frames"], 241,
                                 f"{s['title']} is a Quick-only cell")

    def test_at_quick_the_twenty_second_cell_is_available_again(self):
        frames = [s["music_video"]["frames"]
                  for s in self.plan(quality="quick")["shots"]]
        self.assertIn(481, frames)

    def test_a_hand_written_axis_is_still_cut_to_the_canvas_and_said_so(self):
        out = self.plan(cells=mv.LTX_CELLS, quality="standard")
        self.assertTrue(all(s["music_video"]["frames"] <= 241
                            for s in out["shots"]))
        self.assertTrue(any("20 s" in n for n in out["notes"]), out["notes"])

    def test_naming_no_quality_at_all_leaves_the_axis_alone(self):
        frames = [s["music_video"]["frames"]
                  for s in self.plan(quality="")["shots"]]
        self.assertIn(481, frames)


class TheCastingCanBePinned(_Planned, unittest.TestCase):
    """B-roll was a blind round-robin: whatever came next in the list took the
    next slot, so the picture the film opens and closes on was cast by
    accident. `use` and `weight` are the two opinions a person actually has."""

    STAGE = "/tmp/mv/stage.png"
    WIDE = "/tmp/mv/wide.png"
    PINNED = [
        {"path": "/tmp/mv/singer_a.png", "role": "singer"},
        {"path": STAGE, "role": "room", "use": "open",
         "prompt": "the empty stage, the lights easing up"},
        {"path": "/tmp/mv/piano.png", "role": "instrument"},
        {"path": WIDE, "role": "room", "use": "close",
         "prompt": "wide of the whole band"},
    ]

    def broll(self, out):
        return [s for s in out["shots"]
                if s["music_video"]["kind"] == "broll"]

    def test_open_and_close_land_in_the_first_and_last_broll_shots(self):
        out = self.plan(images=self.PINNED)
        shots = self.broll(out)
        self.assertGreater(len(shots), 2)
        self.assertEqual(shots[0]["still"], self.STAGE)
        self.assertEqual(shots[-1]["still"], self.WIDE)
        # And the pinned picture's own line came with it.
        self.assertIn("the empty stage", shots[0]["prompt"])
        self.assertIn("wide of the whole band", shots[-1]["prompt"])

    def test_a_pin_moves_who_is_in_frame_and_nothing_about_the_clock(self):
        loose = [dict(im, use="any") for im in self.PINNED]
        a, b = self.plan(images=loose), self.plan(images=self.PINNED)
        self.assertEqual([s["duration_s"] for s in a["shots"]],
                         [s["duration_s"] for s in b["shots"]])
        self.assertEqual([s.get("audio_start_time") for s in a["shots"]],
                         [s.get("audio_start_time") for s in b["shots"]])
        self.assertEqual(a["film_seconds"], b["film_seconds"])

    def test_a_weight_buys_a_picture_more_of_the_rotation(self):
        plain = [{"path": "/tmp/mv/singer_a.png", "role": "singer"},
                 {"path": "/tmp/mv/piano.png", "role": "instrument"},
                 {"path": "/tmp/mv/room.png", "role": "room"}]
        heavy = [dict(im, weight=3) if im["path"].endswith("piano.png") else im
                 for im in plain]
        def times(images, path):
            return sum(1 for s in self.broll(self.plan(images=images))
                       if s["still"] == path)
        self.assertGreater(times(heavy, "/tmp/mv/piano.png"),
                           times(plain, "/tmp/mv/piano.png"))

    def test_a_per_shot_pin_is_not_overwritten_by_the_opening_pin(self):
        """Two kinds of casting decision, and the later pass ate the earlier.

        `emit()` honours `shots[i].image`, but `_pin_broll()` then recast the
        same shot for `use: open` (Codex review, 2026-09-22). The caller who
        pinned a picture to shot 1 by hand got the opening picture there
        instead, and the pin they wrote was silently worth nothing.
        """
        A, B = "/tmp/mv/roomA.png", "/tmp/mv/roomB.png"
        images = [{"path": A, "role": "room", "use": "open"},
                  {"path": B, "role": "room"}]
        out = self.plan(
            sections=[{"start": 0.0, "end": 15.0, "kind": "instrumental",
                       "name": "intro", "source": "score"}],
            images=images, shot_images={1: B})
        shots = out["shots"]
        self.assertEqual(shots[0]["still"], B, "the explicit pin lost")
        # …and the opening picture still gets into the film, in the first
        # B-roll slot nobody claimed.
        self.assertIn(A, [s["still"] for s in shots[1:]])

    def test_a_per_shot_pin_is_not_overwritten_by_the_closing_pin(self):
        A, B = "/tmp/mv/roomA.png", "/tmp/mv/roomB.png"
        images = [{"path": A, "role": "room", "use": "close"},
                  {"path": B, "role": "room"}]
        out = self.plan(images=images)
        last = len(out["shots"])
        self.assertEqual(out["shots"][-1]["still"], A)          # unpinned: A closes
        out = self.plan(images=images, shot_images={last: B})
        self.assertEqual(out["shots"][-1]["still"], B)
        self.assertIn(A, [s["still"] for s in out["shots"][:-1]])

    def test_a_film_whose_every_broll_shot_is_pinned_says_the_truth(self):
        # Not "every shot is a singing shot" — there were slots, the caller
        # had already cast all of them.
        A, B = "/tmp/mv/roomA.png", "/tmp/mv/roomB.png"
        out = self.plan(
            sections=[{"start": 0.0, "end": 6.0, "kind": "instrumental",
                       "name": "intro", "source": "score"}],
            images=[{"path": A, "role": "room", "use": "open"},
                    {"path": B, "role": "room"}],
            shot_images={1: B})
        self.assertEqual([s["still"] for s in out["shots"]], [B])
        self.assertTrue(any("pinned to a picture by hand" in n
                            for n in out["notes"]), out["notes"])
        self.assertFalse(any("singing shot" in n for n in out["notes"]),
                         out["notes"])

    def test_the_reserved_shot_is_said_out_loud(self):
        A, B = "/tmp/mv/roomA.png", "/tmp/mv/roomB.png"
        out = self.plan(images=[{"path": A, "role": "room", "use": "open"},
                                {"path": B, "role": "room"}],
                        shot_images={1: B})
        self.assertTrue(any("pinned" in n and "open" in n for n in out["notes"]),
                        out["notes"])

    def test_without_a_per_shot_pin_open_and_close_are_unchanged(self):
        out = self.plan(images=self.PINNED)
        shots = self.broll(out)
        self.assertEqual(shots[0]["still"], self.STAGE)
        self.assertEqual(shots[-1]["still"], self.WIDE)

    def test_a_singer_pinned_to_open_is_ignored_and_said_so(self):
        images = [dict(self.PINNED[0], use="open")] + self.PINNED[1:]
        out = self.plan(images=images)
        self.assertEqual(self.broll(out)[0]["still"], self.STAGE)
        self.assertTrue(any("Singer" in n for n in out["notes"]), out["notes"])


class TheLineForOneShot(_Planned, unittest.TestCase):
    """A per-image prompt follows the picture into every shot it is cast in;
    the last shot of a film needs a line no other shot may have."""

    def repeated(self, out):
        """(picture, [shot numbers]) for a picture cast more than once."""
        by_image: dict[str, list[int]] = {}
        for s in out["shots"]:
            by_image.setdefault(s["still"], []).append(int(s["n"]))
        return next((k, v) for k, v in by_image.items() if len(v) >= 2)

    def test_a_shot_level_line_does_not_leak_to_other_uses_of_the_picture(self):
        base = self.plan()
        _image, ns = self.repeated(base)
        line = "the stage lights slowly fading down to black"
        out = self.plan(shot_prompts={ns[0]: line})
        before = {int(s["n"]): s for s in base["shots"]}
        after = {int(s["n"]): s for s in out["shots"]}
        self.assertIn(line, after[ns[0]]["prompt"])
        self.assertTrue(after[ns[0]]["music_video"]["prompt_override"])
        for n in ns[1:]:
            self.assertNotIn(line, after[n]["prompt"])
            self.assertEqual(after[n]["prompt"], before[n]["prompt"])
            self.assertNotIn("prompt_override", after[n]["music_video"])

    def test_the_overridden_line_still_carries_the_film_s_look(self):
        out = self.plan(shot_prompts={1: "the door opens"})
        self.assertIn(STYLE, out["shots"][0]["prompt"])

    def test_a_line_and_a_pin_on_the_same_shot_both_survive(self):
        images = [{"path": "/tmp/mv/singer_a.png", "role": "singer"},
                  {"path": "/tmp/mv/piano.png", "role": "instrument"},
                  {"path": "/tmp/mv/wide.png", "role": "room", "use": "close"}]
        out = self.plan(images=images)
        last = out["shots"][-1]
        self.assertEqual(last["music_video"]["kind"], "broll")
        pinned = self.plan(images=images,
                           shot_prompts={int(last["n"]): "lights to black"})
        end = pinned["shots"][-1]
        self.assertEqual(end["still"], "/tmp/mv/wide.png")
        self.assertIn("lights to black", end["prompt"])

    def test_a_line_for_a_shot_that_does_not_exist_is_a_note_not_a_crash(self):
        out = self.plan(shot_prompts={9999: "nobody's shot"})
        self.assertTrue(any("9999" in n for n in out["notes"]), out["notes"])

    def test_nonsense_keys_are_ignored_rather_than_refused(self):
        self.assertEqual(mv._shot_prompts({"3": "a", "x": "b", 4: "", 0: "c"}),
                         {3: "a"})


class TheCallerCanWriteTheStructure(unittest.TestCase):
    """`given_sections`: the person with the song knows where the lines are.
    The first real film was planned against measured vocal onsets typed in by
    hand, because no analyser could have known them."""

    ROWS = [{"start": 0.0, "end": 8.5, "kind": "instrumental", "label": "open"},
            {"start": 8.5, "end": 51.6, "kind": "vocal", "label": "intro"},
            {"start": 51.6, "end": 60.0, "kind": "instrumental"}]

    def test_the_rows_are_taken_as_written(self):
        rows, notes = mv.given_sections(self.ROWS, duration=60.0)
        self.assertEqual([r["kind"] for r in rows],
                         ["instrumental", "vocal", "instrumental"])
        self.assertEqual([r["name"] for r in rows],
                         ["open", "intro", "instrumental"])
        self.assertTrue(all(r["source"] == "given" for r in rows))
        self.assertEqual(notes, [])

    def test_they_are_sorted_and_the_gaps_are_closed(self):
        rows, _ = mv.given_sections(
            [{"start": 20.0, "end": 30.0, "kind": "vocal"},
             {"start": 0.0, "end": 10.0, "kind": "instrumental"}],
            duration=30.0)
        self.assertEqual([r["start"] for r in rows], [0.0, 10.0])
        for a, b in zip(rows, rows[1:]):
            self.assertEqual(a["end"], b["start"])

    def test_the_first_section_is_pulled_to_the_start_of_the_song(self):
        # The film plays from 0:00 with the song under it from 0:00. A cursor
        # that began at 8.5 s would render every mouth 8.5 s off its words.
        rows, notes = mv.given_sections(
            [{"start": 8.5, "end": 40.0, "kind": "vocal"}], duration=40.0)
        self.assertEqual(rows[0]["start"], 0.0)
        self.assertTrue(any("0:00" in n for n in notes), notes)

    def test_a_section_past_the_end_of_the_song_is_clamped(self):
        rows, _ = mv.given_sections(
            [{"start": 0.0, "end": 30.0, "kind": "vocal"},
             {"start": 30.0, "end": 900.0, "kind": "instrumental"}],
            duration=40.0)
        self.assertEqual(rows[-1]["end"], 40.0)

    def test_sections_that_stop_early_are_honoured_and_noted(self):
        rows, notes = mv.given_sections(
            [{"start": 0.0, "end": 30.0, "kind": "vocal"}], duration=90.0)
        self.assertEqual(rows[-1]["end"], 30.0)
        self.assertTrue(any("no shots" in n for n in notes), notes)

    def test_overlapping_sections_are_refused_by_name(self):
        with self.assertRaises(mv.MusicVideoError) as cm:
            mv.given_sections([{"start": 0.0, "end": 30.0, "kind": "vocal"},
                               {"start": 20.0, "end": 40.0, "kind": "vocal"}],
                              duration=40.0)
        self.assertIn("overlap", str(cm.exception))

    def test_every_other_way_of_getting_it_wrong_is_a_sentence(self):
        for rows, word in (
                ([], "list"),
                ("not a list", "list"),
                ([{"start": 0.0, "end": 5.0}], "kind"),
                ([{"start": 0.0, "end": 5.0, "kind": "chorus"}], "kind"),
                ([{"start": 5.0, "end": 5.0, "kind": "vocal"}], "after"),
                ([{"end": 5.0, "kind": "vocal"}], "start"),
                ([["nope"]], "object"),
                ([{"start": 200.0, "end": 300.0, "kind": "vocal"}], "inside")):
            with self.assertRaises(mv.MusicVideoError, msg=rows) as cm:
                mv.given_sections(rows, duration=60.0)
            self.assertIn(word, str(cm.exception))


class TheBoardTheStoryboardGets(unittest.TestCase):
    """The plan has to be a board the existing machinery can shoot."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.song = root / "click.wav"
        click_track(cls.song, bars=24, vocal_bars=range(8, 16))
        cls.beats = sedit.beat_map(cls.song)
        # Real files, because the validator checks refs and the panel checks
        # stills; a fake path would pass the planner and fail the render.
        cls.images = []
        for name, role in (("singer_a.png", "singer"), ("singer_b.png", "singer"),
                           ("piano.png", "instrument"), ("room.png", "room")):
            p = root / name
            p.write_bytes(b"\x89PNG\r\n\x1a\n")
            cls.images.append({"path": str(p), "role": role})
        cls.out = mv.plan_music_video(
            [{"start": 0.0, "end": 10.0, "kind": "instrumental",
              "name": "intro", "source": "score"},
             {"start": 10.0, "end": 60.0, "kind": "vocal",
              "name": "verse", "source": "score"}],
            cls.images, style=STYLE, bpm_grid=cls.beats,
            song=str(cls.song), title="Test video", board_id="sb_test")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_it_validates(self):
        errs = storyboard.validate_storyboard(self.out["board"])
        self.assertEqual(errs, [], errs)

    def test_every_shot_is_ltx_because_a2v_only_exists_there(self):
        self.assertEqual(self.out["board"]["engine_mode"], "ltx")
        self.assertEqual({s["engine"] for s in self.out["shots"]}, {"ltx"})

    def test_a_singing_shot_becomes_an_a2v_job_carrying_its_audio(self):
        shot = next(s for s in self.out["shots"]
                    if s["music_video"]["kind"] == "singing")
        job = storyboard.shot_to_job(shot, storyboard.default_policy()["final"],
                                     board_id="sb_test", engine_mode="ltx")
        self.assertEqual(job["mode"], "a2v")
        self.assertEqual(job["audio"], str(self.song))
        self.assertAlmostEqual(float(job["audio_start_time"]),
                               shot["audio_start_time"], places=3)
        self.assertEqual(job["image"], shot["still"])
        self.assertEqual(int(job["frames"]), shot["music_video"]["frames"])

    def test_a_broll_shot_becomes_an_anchored_i2v_job(self):
        shot = next(s for s in self.out["shots"]
                    if s["music_video"]["kind"] == "broll")
        job = storyboard.shot_to_job(shot, storyboard.default_policy()["final"],
                                     board_id="sb_test", engine_mode="ltx")
        self.assertEqual(job["mode"], "i2v")
        self.assertEqual(job["image"], shot["still"])
        self.assertEqual(job["i2v_reference_mode"], "anchor")
        self.assertNotIn("audio", job)

    def test_the_board_remembers_the_song_and_the_cast(self):
        block = self.out["board"]["music_video"]
        self.assertEqual(block["song"], str(self.song))
        self.assertEqual([i["role"] for i in block["images"]],
                         [i["role"] for i in self.images])
        self.assertEqual(block["summary"], self.out["summary"])


if __name__ == "__main__":
    unittest.main()
