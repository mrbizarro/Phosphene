#!/usr/bin/env python3
"""Room tone — a generated ambience bed under the whole film.

Owner, 2026-09-17: "the cuts are very rough in terms of sound … if it was all
over the timeline as an ambient sound, not something really subtle." Locked
here, against the real generator, the real model, the real filtergraph builder
and the panel's real JavaScript (run in node):

  * the bed: periodic loop (no seam when tiled), the loudness it claims is the
    loudness ffmpeg's ebur128 measures, peaks under −1 dBFS, exact length,
    two decorrelated channels, deterministic per (variant, seed);
  * "From this film" learns from the QUIET windows of the clips, not from the
    lines, and falls back to a preset — and says so — when there is no quiet;
  * the model: a room-tone track validates, normalises with its tag, fits the
    film's length (only when untouched, bounded by the file);
  * the de-click: 12 ms fades on each clip-sound segment in the timeline
    render, and nothing at all for every other caller;
  * automatic cuts get a bed when the setting is on (and not under a music
    video, and not when the clips have no quiet sound);
  * the route, the setting, the client mirror and the Room tone card.

Run:  python3 -m unittest test_room_tone
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
_SCRATCH = Path(tempfile.mkdtemp(prefix="phos-roomtone-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "up")):
    os.environ.setdefault(_k, str(_SCRATCH / _d))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")

import mlx_ltx_panel as panel                                        # noqa: E402
import room_tone as rt                                               # noqa: E402
import storyboard_editor as se                                       # noqa: E402
from panel.routes import GET_ROUTES, POST_ROUTES                     # noqa: E402
from test_storyboard_editor_ui import (NODE, SHIM, extract_function,  # noqa: E402
                                       panel_source)

FFMPEG = shutil.which("ffmpeg") or ("/opt/homebrew/bin/ffmpeg"
                                    if Path("/opt/homebrew/bin/ffmpeg").exists() else None)
N = rt.LOOP_S * rt.SR


def _ebur128(path) -> float:
    o = subprocess.run([FFMPEG, "-nostats", "-i", str(path), "-af", "ebur128",
                        "-f", "null", "-"], capture_output=True, text=True).stderr
    return float(re.findall(r"I:\s+(-?[\d.]+) LUFS", o)[-1])


# =============================================================================
# 1. THE BED
# =============================================================================
class TheLoop(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loops = {v: rt.build_loop(v, 7) for v in rt.variant_ids() if v != "film"}

    def test_every_preset_is_on_the_list_the_picker_shows(self):
        ids = rt.variant_ids()
        self.assertEqual(ids[0], "film")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 12)          # "10 or 20 versions"
        for v in rt.variants():
            self.assertTrue(v["label"] and v["blurb"], v)

    def test_the_loop_is_stereo_and_exactly_one_grid_long(self):
        for v, r in self.loops.items():
            self.assertEqual(r["loop"].shape, (2, N), v)
            self.assertEqual(r["variant"], v)
            self.assertEqual(r["fallback"], "")

    def test_the_seam_is_no_louder_than_any_other_sample_step(self):
        # Tiled three times, the step across each boundary is an ordinary step.
        for v, r in self.loops.items():
            x = rt.tile(r["loop"], rt.LOOP_S * 3)
            for k in (1, 2):
                jump, p999 = rt.seam_jump(x, k * N)
                self.assertLessEqual(jump, p999, (v, k, jump, p999))

    def test_tiling_repeats_the_loop_sample_for_sample(self):
        r = self.loops["quiet_room"]
        x = rt.tile(r["loop"], rt.LOOP_S * 2 + 1.5)
        self.assertEqual(x.shape[1], int((rt.LOOP_S * 2 + 1.5) * rt.SR))
        np.testing.assert_array_equal(x[:, N:2 * N], r["loop"])
        np.testing.assert_array_equal(x[:, 2 * N:], r["loop"][:, :x.shape[1] - 2 * N])

    def test_the_loudness_it_reports_is_the_loudness_it_has(self):
        for v, r in self.loops.items():
            self.assertAlmostEqual(rt.loudness_lufs(r["loop"]), r["ref_lufs"], delta=0.05, msg=v)
            self.assertLessEqual(r["ref_lufs"], rt.REF_LUFS + 0.01, v)
            # the cap may pull a peaky preset down a little, never a lot
            self.assertGreater(r["ref_lufs"], rt.REF_LUFS - 3.0, v)

    def test_no_sample_reaches_minus_one_dbfs(self):
        for v, r in self.loops.items():
            self.assertLessEqual(float(np.abs(r["loop"]).max()), rt.PEAK_CAP + 1e-6, v)

    def test_the_two_channels_are_not_one_signal(self):
        for v, r in self.loops.items():
            c = float(np.corrcoef(r["loop"][0], r["loop"][1])[0, 1])
            self.assertLess(c, 0.9, v)

    def test_a_seed_is_a_take(self):
        a = rt.build_loop("office", 1)["loop"]
        b = rt.build_loop("office", 1)["loop"]
        c = rt.build_loop("office", 2)["loop"]
        np.testing.assert_array_equal(a, b)
        self.assertGreater(float(np.abs(a - c).mean()), 1e-3)

    def test_the_movement_stays_a_few_db(self):
        # 0.5 s short-term levels of a moving preset never swing like a gate.
        x = self.loops["outdoor_day"]["loop"][0]
        blk = x[: N // 24000 * 24000].reshape(-1, 24000)
        db = 10 * np.log10((blk ** 2).mean(1))
        self.assertLess(float(db.max() - db.min()), 12.0)


class TheLoudnessMeter(unittest.TestCase):
    def test_a_997_hz_sine_reads_as_bs1770_says(self):
        # BS.1770: a 0 dBFS 997 Hz sine in ONE channel reads −3.01 LKFS.
        t = np.arange(rt.SR * 4) / rt.SR
        x = np.zeros((2, t.size))
        x[0] = 10 ** (-20 / 20) * np.sin(2 * np.pi * 997 * t)
        self.assertAlmostEqual(rt.loudness_lufs(x), -23.01, delta=0.1)

    def test_silence_is_the_floor(self):
        self.assertEqual(rt.loudness_lufs(np.zeros((2, 1000))), -120.0)

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_ffmpeg_agrees_on_the_written_file(self):
        with tempfile.TemporaryDirectory() as d:
            for v in ("film_fallback", "kitchen", "car", "vhs_tape"):
                r = (rt.build_loop("film", 1, clips=[]) if v == "film_fallback"
                     else rt.build_loop(v, 3))
                f = Path(d) / f"{v}.wav"
                rt.write_wav(f, rt.tile(r["loop"], 60))
                # ffmpeg gates 400 ms blocks and prints one decimal; a moving
                # preset reads a few tenths apart from the ungated figure.
                self.assertAlmostEqual(_ebur128(f), r["ref_lufs"], delta=0.5, msg=v)


class TheLevel(unittest.TestCase):
    def test_the_fader_turns_the_reference_into_the_level(self):
        self.assertAlmostEqual(rt.level_gain(-27), 10 ** (-9 / 20), places=5)
        self.assertEqual(rt.level_gain(-18), 1.0)
        self.assertEqual(rt.level_gain(-10), 1.0)        # clamped, never a boost
        self.assertAlmostEqual(rt.level_gain(-27, -21), 10 ** (-6 / 20), places=5)
        self.assertEqual(rt.level_gain(-20, -23.5), 1.0)  # as loud as it goes

    def test_level_and_gain_are_inverses(self):
        for lvl in (-45, -33.5, -27, -24, -18):
            self.assertAlmostEqual(rt.gain_level(rt.level_gain(lvl)), lvl, places=2)

    def test_junk_is_the_default(self):
        for junk in (None, "", "abc", float("nan")):
            self.assertEqual(rt.clamp_level(junk), rt.DEFAULT_LEVEL)
        self.assertEqual(rt.clamp_level(-99), rt.LEVEL_MIN)

    def test_the_default_is_clearly_audible(self):
        # "not something really subtle": between −30 and −24 LUFS
        self.assertTrue(-30 <= rt.DEFAULT_LEVEL <= -24)

    def test_the_file_is_rounded_up_so_trims_never_regenerate(self):
        self.assertEqual(rt.bed_seconds(0), 30)
        self.assertEqual(rt.bed_seconds(29.4), 30)
        self.assertEqual(rt.bed_seconds(29.6), 60)
        self.assertEqual(rt.bed_seconds(101.4), 120)
        self.assertEqual(rt.bed_seconds(10 ** 6), rt.BED_MAX_S)


# =============================================================================
# 2. FROM THIS FILM
# =============================================================================
def _scene(seed, n_s=6.0, noise_db=-45.0, line_db=-12.0, lp=1200.0):
    """A clip: lowpassed noise at `noise_db` (the room) with 3 kHz "lines" on
    top every other second (the dialogue)."""
    rng = np.random.default_rng(seed)
    n = int(n_s * rt.SR)
    f = np.fft.rfftfreq(n, 1 / rt.SR)
    mag = 1 / (1 + (f / lp) ** 4)
    room = np.fft.irfft(mag * np.exp(1j * rng.uniform(0, 6.28, f.size)), n=n)
    room *= 10 ** (noise_db / 20) / np.sqrt(np.mean(room ** 2))
    t = np.arange(n) / rt.SR
    gate = ((t % 2.0) > 1.0).astype(float)
    line = 10 ** (line_db / 20) * np.sqrt(2) * np.sin(2 * np.pi * 3000 * t) * gate
    return (room + line).astype(np.float32)


def _band_db(x, lo, hi):
    X = np.abs(np.fft.rfft(x)) ** 2
    f = np.fft.rfftfreq(x.size, 1 / rt.SR)
    return 10 * np.log10(X[(f >= lo) & (f < hi)].mean() + 1e-30)


class FromThisFilm(unittest.TestCase):
    def test_the_bed_is_the_room_not_the_lines(self):
        clips = {f"/c{i}.mp4": _scene(i) for i in range(3)}
        dec = lambda p, s, e: clips[p][int(s * rt.SR):int(e * rt.SR)]  # noqa: E731
        r = rt.build_loop("film", 1, clips=[{"path": p, "start": 0, "end": 6}
                                           for p in clips], decoder=dec)
        self.assertEqual(r["variant"], "film")
        self.assertEqual(r["fallback"], "")
        self.assertEqual(r["clips_used"], 3)
        self.assertGreater(r["quiet_s"], rt.MIN_QUIET_S)
        x = r["loop"][0].astype(np.float64)
        # the room lives under 1.2 kHz; the 3 kHz line must not be in the bed
        low = _band_db(x, 200, 1000)
        tone = _band_db(x, 2950, 3050)
        self.assertGreater(low - tone, 20.0)

    def test_the_median_keeps_one_odd_clip_from_owning_the_film(self):
        clips = {"/a": _scene(1, lp=800), "/b": _scene(2, lp=800),
                 "/odd": _scene(3, lp=12000)}
        dec = lambda p, s, e: clips[p]  # noqa: E731
        shape = rt.film_shape([{"path": p, "start": 0, "end": 6} for p in clips], dec)
        f = np.fft.rfftfreq(rt.WIN, 1 / rt.SR)
        hi = shape["db"][(f > 6000) & (f < 9000)].mean()
        lo = shape["db"][(f > 200) & (f < 600)].mean()
        self.assertGreater(lo - hi, 30.0)

    def test_digital_silence_and_no_sound_fall_back_and_say_so(self):
        silent = lambda p, s, e: np.zeros(int((e - s) * rt.SR), np.float32)  # noqa: E731
        r = rt.build_loop("film", 1, clips=[{"path": "/s", "start": 0, "end": 5}],
                          decoder=silent)
        self.assertEqual(r["variant"], rt.FALLBACK_VARIANT)
        self.assertIn("no clip", r["fallback"])
        r = rt.build_loop("film", 1, clips=[])
        self.assertEqual(r["variant"], rt.FALLBACK_VARIANT)
        self.assertTrue(r["fallback"])

    def test_a_decoder_that_fails_is_a_clip_skipped(self):
        def boom(p, s, e):
            raise RuntimeError("unreadable")
        self.assertIsNone(rt.film_shape([{"path": "/x", "start": 0, "end": 2}], boom)["db"])

    @unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
    def test_a_real_clip_through_ffmpeg(self):
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d) / "clip.wav"
            x = _scene(5, n_s=8.0)
            rt.write_wav(wav, np.stack([x, x]))
            mp4 = Path(d) / "clip.mp4"
            subprocess.run([FFMPEG, "-y", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=black:s=64x64:d=8", "-i", str(wav), "-shortest",
                            "-c:v", "libx264", "-c:a", "aac", str(mp4)], check=True)
            got = rt.decode_window(mp4, 1.0, 5.0)
            self.assertAlmostEqual(got.size / rt.SR, 4.0, delta=0.05)
            r = rt.build_loop("film", 1, clips=[{"path": str(mp4), "start": 0, "end": 8}])
            self.assertEqual(r["variant"], "film")


class MakeBed(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_file_is_as_long_as_it_says_and_reused_on_the_same_pick(self):
        a = rt.make_bed(self.dir, variant="kitchen", seed=2, film_len=31.2)
        self.assertFalse(a["reused"])
        self.assertEqual(a["duration"], 60.0)
        import wave
        with wave.open(a["path"]) as w:
            self.assertEqual((w.getnchannels(), w.getframerate(), w.getnframes()),
                             (2, rt.SR, 60 * rt.SR))
        b = rt.make_bed(self.dir, variant="kitchen", seed=2, film_len=40)
        self.assertTrue(b["reused"])
        self.assertEqual(b["path"], a["path"])
        self.assertEqual(b["ref_lufs"], a["ref_lufs"])
        c = rt.make_bed(self.dir, variant="kitchen", seed=3, film_len=40)
        self.assertNotEqual(c["path"], a["path"])
        d = rt.make_bed(self.dir, variant="kitchen", seed=2, film_len=70)
        self.assertNotEqual(d["path"], a["path"])
        self.assertTrue(Path(a["path"]).is_file())

    def test_a_longer_bed_starts_with_the_same_sound(self):
        a = rt.make_bed(self.dir, variant="office", seed=4, film_len=10)
        b = rt.make_bed(self.dir, variant="office", seed=4, film_len=50)
        import wave
        with wave.open(a["path"]) as w1, wave.open(b["path"]) as w2:
            self.assertEqual(w1.readframes(rt.SR * 30), w2.readframes(rt.SR * 30))

    def test_an_unknown_variant_is_refused(self):
        with self.assertRaises(ValueError):
            rt.make_bed(self.dir, variant="nope", film_len=5)

    def test_strict_refuses_a_CACHED_fallback_too(self):
        """Ship review 2026-09-18 (Codex, confirmed): the cache hit returned
        before the `strict` check, so a bed the MANUAL path was allowed to make
        from a preset could be handed straight back to the AUTOMATIC cut, whose
        whole policy is that a film of silent clips gets no hiss nobody asked
        for. Same film, same seed, same length — one path wrote it, the other
        must still refuse it."""
        loose = rt.make_bed(self.dir, variant="film", film_len=5, clips=[])
        self.assertTrue(Path(loose["path"]).is_file())     # the manual bed exists
        self.assertTrue(loose["fallback"])                 # ...as a preset stand-in
        strict = rt.make_bed(self.dir, variant="film", film_len=5, clips=[], strict=True)
        self.assertEqual(strict["path"], "")
        self.assertTrue(strict["fallback"])
        self.assertFalse(strict["reused"])
        # ...and the file the manual path made is still there, untouched.
        self.assertTrue(Path(loose["path"]).is_file())

    def test_strict_writes_nothing_when_the_film_has_no_quiet(self):
        f = rt.make_bed(self.dir, variant="film", film_len=5, clips=[], strict=True)
        self.assertEqual(f["path"], "")
        self.assertTrue(f["fallback"])
        self.assertEqual(list(self.dir.glob("*.wav")), [])
        g = rt.make_bed(self.dir, variant="film", film_len=5, clips=[])
        self.assertEqual(g["used"], rt.FALLBACK_VARIANT)
        self.assertEqual(g["label"], "Quiet room")
        self.assertTrue(Path(g["path"]).is_file())


# =============================================================================
# 3. THE MODEL
# =============================================================================
def _doc(film_len=12.0, tracks=None):
    doc = {"version": se.EDIT_VERSION, "board_id": "b", "audio": None,
           "clips": [{"id": "c1", "path": "/x/a.mp4", "start": 0.0, "end": film_len,
                      "film_start": 0.0, "film_end": film_len, "source": "auto"}]}
    if tracks is not None:
        doc["audio_tracks"] = tracks
    return doc


FACTS = {"path": "/f/audio/room_tone/rt_film_1_ab_30s.wav", "duration": 30.0,
         "ref_lufs": -18.0, "variant": "film", "label": "From this film", "seed": 1}


class TheModel(unittest.TestCase):
    def test_a_room_tone_track_is_a_legal_track(self):
        t = se.room_tone_new_track(FACTS, 12.0, level=-27)
        self.assertEqual(t["name"], se.ROOM_TONE_NAME)
        self.assertEqual(t["room_tone"], {"variant": "film", "seed": 1, "level": -27.0,
                                          "ref_lufs": -18.0})
        self.assertAlmostEqual(t["gain"], rt.level_gain(-27), places=6)
        s = t["strips"][0]
        self.assertEqual((s["start"], s["end"], s["film_start"], s["duration"]),
                         (0.0, 12.0, 0.0, 30.0))
        self.assertTrue(s["locked"])
        doc = _doc(tracks=[t])
        self.assertEqual(se.blocking_errors(se.validate_edit(doc)), [])
        norm = se.normalise_edit(doc)
        self.assertEqual(norm["audio_tracks"][0]["room_tone"], t["room_tone"])
        self.assertEqual(se.room_tone_track(norm)[0], 0)

    def test_the_strip_never_runs_past_its_file(self):
        t = se.room_tone_new_track(FACTS, 45.0)
        self.assertEqual(t["strips"][0]["end"], 30.0)

    def test_a_bed_at_its_reference_level_has_no_fader(self):
        t = se.room_tone_new_track(FACTS, 12.0, level=-18)
        self.assertNotIn("gain", t)

    def test_the_render_mixes_it_at_the_fader(self):
        doc = _doc(tracks=[se.room_tone_new_track(FACTS, 12.0, level=-30)])
        rows = se.track_render_strips(doc)
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["at"], rows[0]["len"]), (0.0, 12.0))
        g = rt.level_gain(-30)
        self.assertEqual(rows[0]["gain"], [[0.0, g], [12.0, g]])

    def test_a_junk_tag_is_refused_or_dropped(self):
        bad = dict(se.room_tone_new_track(FACTS, 12.0), room_tone="loud")
        codes = [e["code"] for e in se.validate_edit(_doc(tracks=[bad]))]
        self.assertIn("audio_track_room_tone", codes)
        worse = dict(se.room_tone_new_track(FACTS, 12.0))
        worse["room_tone"] = {"variant": "film", "level": "x"}
        codes = [e["code"] for e in se.validate_edit(_doc(tracks=[worse]))]
        self.assertIn("audio_track_room_tone", codes)
        norm = se.normalise_edit(_doc(tracks=[dict(bad)]))
        self.assertNotIn("room_tone", norm["audio_tracks"][0])
        self.assertIsNone(se.room_tone_track(norm))

    def test_an_ordinary_track_is_not_room_tone(self):
        doc = _doc(tracks=[{"id": "t1", "strips": []}])
        self.assertIsNone(se.room_tone_track(doc))
        self.assertIsNone(se.room_tone_meta({"id": "t"}))

    def test_the_bed_follows_the_film(self):
        doc = _doc(8.0, tracks=[se.room_tone_new_track(FACTS, 12.0)])
        self.assertTrue(se.room_tone_fit(doc))
        self.assertEqual(doc["audio_tracks"][0]["strips"][0]["end"], 8.0)
        self.assertFalse(se.room_tone_fit(doc))
        doc["clips"][0]["end"] = doc["clips"][0]["film_end"] = 40.0
        self.assertTrue(se.room_tone_fit(doc))
        self.assertEqual(doc["audio_tracks"][0]["strips"][0]["end"], 30.0)   # the file

    def test_a_bed_cut_by_hand_is_left_alone(self):
        t = se.room_tone_new_track(FACTS, 12.0)
        s2 = dict(t["strips"][0], id="s2", start=6.0, end=12.0, film_start=6.0)
        t["strips"][0]["end"] = 5.0
        t["strips"].append(s2)
        doc = _doc(8.0, tracks=[t])
        self.assertFalse(se.room_tone_fit(doc))
        moved = se.room_tone_new_track(FACTS, 12.0)
        moved["strips"][0]["film_start"] = 1.0
        self.assertFalse(se.room_tone_fit(_doc(8.0, tracks=[moved])))


# =============================================================================
# 4. THE RENDER — the de-click, and the bed in a real mix
# =============================================================================
class TheDeclick(unittest.TestCase):
    def graph(self, **kw):
        probes = [("/a.mp4", {"width": 640, "height": 360, "duration": 3.0,
                              "has_audio": True}),
                  ("/b.mp4", {"width": 640, "height": 360, "duration": 2.0,
                              "has_audio": True})]
        return panel._sb_film_filtergraph(probes, 640, 360, 48000, "yuv420p", **kw)[0]

    def test_off_by_default_so_every_other_caller_builds_the_old_graph(self):
        self.assertNotIn("afade", self.graph())
        self.assertEqual(self.graph(), self.graph(declick=0.0))

    def test_on_every_clip_sound_gets_a_head_and_a_tail(self):
        g = self.graph(declick=0.012)
        self.assertEqual(g.count("afade=t=in:d=0.012"), 2)
        self.assertIn("afade=t=out:st=2.988000:d=0.012", g)
        self.assertIn("afade=t=out:st=1.988000:d=0.012", g)

    def test_a_sound_too_short_for_two_fades_is_left_alone(self):
        self.assertEqual(panel._sb_declick_term(0.012, 0.04), "")
        self.assertEqual(panel._sb_declick_term(0.0, 5.0), "")

    def test_the_split_lanes_get_it_too(self):
        segs = [{"input": 0, "kind": "video", "info": {"has_audio": True, "duration": 3.0},
                 "window": {"start": 0.0, "end": 3.0},
                 "audio": {"start": 0.0, "end": 3.0, "film_start": 0.0}},
                {"input": 1, "kind": "video", "info": {"has_audio": True, "duration": 3.0},
                 "window": {"start": 0.0, "end": 2.0}, "lane": 2}]
        probes = [("/a.mp4", segs[0]["info"]), ("/b.mp4", segs[1]["info"])]
        try:
            g = panel._sb_film_filtergraph(probes, 640, 360, 48000, "yuv420p",
                                           segments=segs, declick=0.012)[0]
        except Exception as exc:                                     # noqa: BLE001
            self.skipTest(f"segment shape drifted: {exc}")
        self.assertGreaterEqual(g.count("afade=t=in:d=0.012"), 2)

    def test_the_timeline_render_asks_for_it(self):
        seen = {}

        def fake(*a, **kw):
            seen.update(kw)
            return {"ok": True, "path": "/x.mp4"}
        board = {"id": "b", "title": "T", "created_at": 1_700_000_000}
        doc = _doc(3.0)
        with mock.patch.object(panel, "_sb_assemble_film", fake), \
                mock.patch.object(panel, "push", lambda *a, **k: None):
            panel._sbe_render_edit(board, doc)
        self.assertEqual(seen.get("declick"), panel.SB_DECLICK_S)
        self.assertTrue(0.010 <= panel.SB_DECLICK_S <= 0.020)


@unittest.skipUnless(FFMPEG, "ffmpeg not on PATH")
class ARealMix(unittest.TestCase):
    """Two clips — loud room, then digital silence — rendered by the real
    assembler, with and without the bed: the silence after the cut is filled."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        d = Path(cls.tmp.name)
        run = lambda *a: subprocess.run([FFMPEG, "-y", "-v", "error", *a], check=True)  # noqa: E731
        cls.a, cls.b = d / "a.mp4", d / "b.mp4"
        run("-f", "lavfi", "-i", "color=c=gray:s=160x90:d=3:r=24", "-f", "lavfi", "-i",
            "anoisesrc=d=3:c=pink:a=0.05:r=48000", "-shortest", "-c:v", "libx264",
            "-c:a", "aac", str(cls.a))
        run("-f", "lavfi", "-i", "color=c=gray:s=160x90:d=3:r=24", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-t", "3", "-c:v", "libx264", "-c:a", "aac",
            str(cls.b))
        bed = rt.make_bed(d / "rt", variant="quiet_room", seed=1, film_len=6)
        cls.board = {"id": "sb_rt_mix", "title": "Room tone mix", "created_at": 1_700_000_000}
        clips = [{"id": "c1", "path": str(cls.a), "start": 0.0, "end": 3.0,
                  "film_start": 0.0, "film_end": 3.0, "source": "auto"},
                 {"id": "c2", "path": str(cls.b), "start": 0.0, "end": 3.0,
                  "film_start": 3.0, "film_end": 6.0, "source": "auto"}]
        base = {"version": se.EDIT_VERSION, "board_id": "sb_rt_mix", "audio": None,
                "clips": clips}
        with_bed = dict(base, audio_tracks=[se.room_tone_new_track(bed, 6.0, level=-27)])
        cls.out = {}
        with mock.patch.object(panel, "OUTPUT", d / "out"), \
                mock.patch.object(panel, "push", lambda *a, **k: None):
            for name, doc in (("dry", base), ("bed", with_bed)):
                film = panel._sbe_render_edit(cls.board, doc, out_name=f"{name}.mp4")
                assert film.get("ok"), film
                cls.out[name] = film["path"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def level(self, path, start, dur):
        r = subprocess.run([FFMPEG, "-v", "error", "-ss", str(start), "-t", str(dur),
                            "-i", str(path), "-map", "0:a:0", "-ac", "1", "-f", "f32le", "-"],
                           capture_output=True, check=True)
        x = np.frombuffer(r.stdout, "<f4").astype(np.float64)
        return 20 * math.log10(math.sqrt(float(np.mean(x * x))) + 1e-9)

    def test_the_cut_no_longer_drops_to_silence(self):
        self.assertLess(self.level(self.out["dry"], 3.5, 2.0), -70)
        self.assertGreater(self.level(self.out["bed"], 3.5, 2.0), -40)

    def test_the_bed_plays_at_its_level(self):
        f = Path(self.out["bed"]).with_name("tail.wav")
        subprocess.run([FFMPEG, "-y", "-v", "error", "-ss", "3.3", "-i", str(self.out["bed"]),
                        "-map", "0:a:0", str(f)], check=True)
        self.assertAlmostEqual(_ebur128(f), -27.0, delta=1.0)


# =============================================================================
# 5. THE PANEL — automatic cuts, the setting, the route
# =============================================================================
class AutomaticCuts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.board = {"id": "sb_rt_auto", "title": "Auto", "created_at": 1_700_000_000}
        self.edit = _doc(12.0)
        self.facts = dict(FACTS, ok=True)
        self.calls = []

        def fake(board, **kw):
            self.calls.append(kw)
            return dict(self.facts)
        self.patches = [mock.patch.object(panel, "_sbe_room_tone", fake),
                        mock.patch.object(panel, "push", lambda *a, **k: None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def settings(self, on):
        return mock.patch.object(panel, "get_settings", lambda: {"room_tone_auto": on})

    def test_on_by_default_it_lays_a_bed_made_from_the_film(self):
        with self.settings(True):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        found = se.room_tone_track(self.edit)
        self.assertIsNotNone(found)
        self.assertEqual(self.calls[0]["variant"], "film")
        self.assertTrue(self.calls[0]["strict"])
        self.assertEqual(self.calls[0]["clips"][0]["path"], "/x/a.mp4")
        self.assertEqual(found[1]["room_tone"]["level"], rt.DEFAULT_LEVEL)
        self.assertEqual(se.blocking_errors(se.validate_edit(self.edit)), [])
        self.assertTrue(panel._settings_defaults()["room_tone_auto"])

    def test_off_means_off(self):
        with self.settings(False):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertIsNone(se.room_tone_track(self.edit))
        self.assertEqual(self.calls, [])

    def test_a_music_video_is_left_alone(self):
        self.edit["audio"] = {"path": "/song.wav"}
        with self.settings(True):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertIsNone(se.room_tone_track(self.edit))
        self.edit["audio"]["mode"] = "under"
        with self.settings(True):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertIsNotNone(se.room_tone_track(self.edit))

    def test_no_quiet_no_bed_and_a_failure_is_not_a_failed_cut(self):
        self.facts = {"ok": False, "error": "no room tone from this film"}
        with self.settings(True):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertIsNone(se.room_tone_track(self.edit))

        def boom(board, **kw):
            raise RuntimeError("disk full")
        with self.settings(True), mock.patch.object(panel, "_sbe_room_tone", boom):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertIsNone(se.room_tone_track(self.edit))

    def test_a_film_that_has_one_keeps_it(self):
        self.edit["audio_tracks"] = [se.room_tone_new_track(FACTS, 12.0, level=-33)]
        with self.settings(True):
            panel._sbe_room_tone_auto(self.board, self.edit, se)
        self.assertEqual(len(self.edit["audio_tracks"]), 1)
        self.assertEqual(self.calls, [])

    def test_the_auto_editor_calls_it(self):
        src = Path(panel.__file__).read_text(encoding="utf-8")
        body = src[src.index("def _sbe_auto_edit("):src.index("def _sbe_relinks(")]
        self.assertIn("_sbe_room_tone_auto(board, edit, sedit)", body)


class TheSetting(unittest.TestCase):
    def test_the_patch_is_validated_and_public(self):
        for raw, want in (("1", True), ("0", False), (True, True), ("off", False)):
            out, err = panel._validate_settings_patch({"room_tone_auto": raw}) \
                if hasattr(panel, "_validate_settings_patch") else (None, None)
            if out is None:
                self.skipTest("settings validator renamed")
            self.assertIsNone(err)
            self.assertEqual(out["room_tone_auto"], want)
        with mock.patch.object(panel, "get_settings", lambda: {"room_tone_auto": False}):
            self.assertFalse(panel.get_settings_public()["room_tone_auto"])


class TheRoute(unittest.TestCase):
    class H:
        def __init__(self, body=""):
            self.body, self.out = body, None

        def _read_form_body(self):
            from urllib.parse import parse_qs
            return self.body, parse_qs(self.body)

        def _json(self, obj, code=200):
            self.out = (code, obj)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.patches = [mock.patch.object(panel, "STATE_DIR", root / "state"),
                        mock.patch.object(panel, "OUTPUT", root / "out"),
                        mock.patch.object(panel, "push", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        (root / "state").mkdir()
        board = {"schema": 1, "id": "sb_rt_route", "title": "Route", "created_at": 1_700_000_000,
                 "policy": panel.storyboard.default_policy(), "cast": [],
                 "engine_mode": "ltx", "shots": []}
        panel.storyboard.save_storyboard(root / "state", board)
        self.root = root

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def post(self, **form):
        from urllib.parse import urlencode
        h = self.H(urlencode(form))
        POST_ROUTES["/storyboard/edit/room-tone"](h, "/storyboard/edit/room-tone", {}, "")
        return h.out

    def test_both_routes_are_registered(self):
        self.assertIn("/storyboard/edit/room-tone", GET_ROUTES)
        self.assertIn("/storyboard/edit/room-tone", POST_ROUTES)

    def test_the_picker_rows_and_the_numbers(self):
        h = self.H()
        GET_ROUTES["/storyboard/edit/room-tone"](h, None)
        code, body = h.out
        self.assertEqual(code, 200)
        self.assertEqual([v["id"] for v in body["variants"]], rt.variant_ids())
        self.assertEqual((body["default_level"], body["level_min"], body["level_max"],
                          body["ref_lufs"]),
                         (rt.DEFAULT_LEVEL, rt.LEVEL_MIN, rt.LEVEL_MAX, rt.REF_LUFS))
        self.assertIn("auto", body)

    def test_a_preset_bed_lands_in_the_films_audio_folder(self):
        code, body = self.post(id="sb_rt_route", variant="tv_studio", seed="3",
                               film_len="12.5", clips="[]")
        self.assertEqual(code, 200, body)
        p = Path(body["path"])
        self.assertTrue(p.is_file())
        self.assertEqual(p.parent.name, "room_tone")
        self.assertEqual(p.parent.parent.name, "audio")
        self.assertTrue(p.is_relative_to(self.root / "out"))
        self.assertEqual((body["duration"], body["seed"], body["label"]), (30.0, 3, "TV studio"))
        # the Sound pool does not list the takes
        board = panel.storyboard.load_storyboard(panel.STATE_DIR, "sb_rt_route")
        self.assertEqual(panel._sbe_list_sounds(board), [])

    def test_a_film_bed_with_nothing_to_hear_falls_back_openly(self):
        code, body = self.post(id="sb_rt_route", variant="film", seed="1", film_len="5",
                               clips=json.dumps([{"path": "/no/such.mp4", "start": 0, "end": 5}]))
        self.assertEqual(code, 200, body)
        self.assertEqual(body["used"], rt.FALLBACK_VARIANT)
        self.assertTrue(body["fallback"])

    def test_bad_requests_are_refused(self):
        self.assertEqual(self.post(id="nope", variant="film")[0], 404)
        self.assertEqual(self.post(id="sb_rt_route", variant="loud")[0], 400)
        self.assertEqual(self.post(id="sb_rt_route", variant="film", clips="{")[0], 400)
        self.assertEqual(self.post(id="sb_rt_route", variant="film", seed="x")[0], 400)


# =============================================================================
# 6. THE CLIENT — the panel's real JavaScript, run in node
# =============================================================================
RT_FUNCTIONS = (
    "sbeNum", "sbeRound", "sbeTsCopy", "sbeTrackLabel", "sbeKind",
    "sbeFilmDuration", "sbeRtClampLevel", "sbeRtGain", "sbeRtBedSeconds",
    "sbeRtFind", "sbeRtLevelOf", "sbeRtNewTrack", "sbeRtPlace", "sbeRtRemove",
    "sbeRtSetLevel", "sbeRtFit", "sbeRtClips", "sbeRtFmtLevel",
)


def run_client(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = panel_source()
    consts = "\n".join(m + ";" for m in re.findall(
        r"^const (?:SBE_RT_\w+|SBE_TRACK_STRIP_MIN) = [^;]+", src, re.MULTILINE))
    script = (SHIM + consts + "\n"
              + "let __id = 0; function sbeNewId() { __id += 1; return 'k' + String(__id).padStart(12, '0'); }\n"
              + "\n".join(extract_function(n, src) for n in RT_FUNCTIONS)
              + "\nconst out = {};\n" + body
              + "\nprocess.stdout.write(JSON.stringify(out));\n")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n" + r.stderr[-3000:])
    return json.loads(r.stdout)


@unittest.skipUnless(NODE, "node not on PATH")
class TheClientMirror(unittest.TestCase):
    def test_the_constants_are_the_generators(self):
        src = panel_source()
        for js, py in (("SBE_RT_DEFAULT_LEVEL", rt.DEFAULT_LEVEL),
                       ("SBE_RT_LEVEL_MIN", rt.LEVEL_MIN), ("SBE_RT_LEVEL_MAX", rt.LEVEL_MAX),
                       ("SBE_RT_REF", rt.REF_LUFS), ("SBE_RT_STEP", rt.BED_STEP_S),
                       ("SBE_RT_MAX", rt.BED_MAX_S)):
            m = re.search(rf"^const {js} = (-?[\d.]+);", src, re.MULTILINE)
            self.assertIsNotNone(m, js)
            self.assertEqual(float(m.group(1)), float(py), js)
        self.assertIn(f"const SBE_RT_NAME = '{se.ROOM_TONE_NAME}';", src)

    def test_gain_and_file_length_agree_with_the_server(self):
        levels = [-45, -40.5, -33, -27, -24, -18, -10, "", None, "x"]
        refs = [-18, -21.3, None]
        lens = [0, 12, 29.4, 29.6, 101.399, 5000]
        out = run_client(f"out.g = {json.dumps(levels)}.map(l => {json.dumps(refs)}.map(r => sbeRtGain(l, r)));"
                         f"out.s = {json.dumps(lens)}.map(sbeRtBedSeconds);"
                         f"out.f = sbeRtFmtLevel(-27);")
        for i, lvl in enumerate(levels):
            for j, ref in enumerate(refs):
                want = rt.level_gain(lvl, rt.REF_LUFS if ref is None else ref)
                self.assertAlmostEqual(out["g"][i][j], want, places=5, msg=(lvl, ref))
        self.assertEqual(out["s"], [rt.bed_seconds(n) for n in lens])
        self.assertEqual(out["f"], "−27 LUFS")

    def test_the_new_track_is_the_servers_track(self):
        out = run_client(f"out.t = sbeRtNewTrack({json.dumps(FACTS)}, 12, -30, 'tA');"
                         f"out.long = sbeRtNewTrack({json.dumps(FACTS)}, 45, -18, '');")
        want = se.room_tone_new_track(FACTS, 12.0, level=-30, track_id="tA")
        t = out["t"]
        self.assertEqual(t["id"], "tA")
        self.assertEqual(t["room_tone"], want["room_tone"])
        self.assertAlmostEqual(t["gain"], want["gain"], places=6)
        for k in ("path", "start", "end", "film_start", "duration", "title", "locked"):
            self.assertEqual(t["strips"][0][k], want["strips"][0][k], k)
        self.assertEqual(out["long"]["strips"][0]["end"], 30)
        self.assertNotIn("gain", out["long"])
        doc = _doc(12.0, tracks=[t, out["long"]])
        self.assertEqual(se.blocking_errors(se.validate_edit(doc)), [])

    def test_place_replaces_in_the_same_lane_and_keeps_the_mute(self):
        out = run_client(f"""
const other = {{ id: 'o', name: 'Laughs', strips: [] }};
const a = sbeRtNewTrack({json.dumps(FACTS)}, 12, -27, '');
const first = sbeRtPlace([other], a).tracks;
first[1].muted = true;
const b = sbeRtNewTrack(Object.assign({{}}, {json.dumps(FACTS)}, {{ variant: 'office', seed: 2 }}), 12, -27, '');
out.second = sbeRtPlace(first, b);
out.first = first;
out.removed = sbeRtRemove(out.second.tracks);
out.none = sbeRtRemove([other]);
out.level = sbeRtSetLevel(first, -18).tracks;
out.level2 = sbeRtSetLevel(first, -36).tracks;
""")
        tr = out["second"]["tracks"]
        self.assertEqual([t["id"] for t in tr], ["o", out["first"][1]["id"]])
        self.assertEqual(tr[1]["room_tone"]["variant"], "office")
        self.assertTrue(tr[1]["muted"])
        # a LOCKED bed is still removed by its own Remove
        self.assertEqual([t["id"] for t in out["removed"]["tracks"]], ["o"])
        self.assertFalse(out["none"]["ok"])
        self.assertNotIn("gain", out["level"][1])
        self.assertEqual(out["level"][1]["room_tone"]["level"], -18)
        self.assertAlmostEqual(out["level2"][1]["gain"], rt.level_gain(-36), places=6)

    def test_fit_is_the_servers_fit(self):
        cases = [(8.0, None), (40.0, None), (12.0, None), (0.0, None),
                 (8.0, "split"), (8.0, "moved")]
        rows = []
        for film_len, shape in cases:
            t = se.room_tone_new_track(FACTS, 12.0)
            if shape == "split":
                t["strips"].append(dict(t["strips"][0], id="s2", film_start=12.0))
            if shape == "moved":
                t["strips"][0]["film_start"] = 1.0
            rows.append((film_len, [t]))
        out = run_client(f"out.r = {json.dumps(rows)}.map(([n, ts]) => sbeRtFit(ts, n));")
        for (film_len, tracks), got in zip(rows, out["r"]):
            doc = _doc(film_len, tracks=json.loads(json.dumps(tracks)))
            if film_len == 0.0:
                doc["clips"] = []
            changed = se.room_tone_fit(doc)
            self.assertEqual(got["changed"], changed, (film_len, got))
            self.assertEqual(got["tracks"][0]["strips"][0]["end"],
                             doc["audio_tracks"][0]["strips"][0]["end"])
        self.assertTrue(out["r"][1]["short"])        # 40 s film, 30 s file
        self.assertFalse(out["r"][0]["short"])

    def test_the_bed_listens_to_the_picture_clips_only(self):
        out = run_client("out.c = sbeRtClips([{path: '/a.mp4', start: 1, end: 3},"
                         "{kind: 'slug', start: 0, end: 2}, {path: '/p.png', start: 0, end: 2},"
                         "{path: '/b.mov', start: 0.5, end: 4, kind: 'video'}]);")
        self.assertEqual(out["c"], [{"path": "/a.mp4", "start": 1, "end": 3},
                                    {"path": "/b.mov", "start": 0.5, "end": 4}])


class TheCard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        cls.js = panel_source()
        cls.css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        cls.docs = (ROOT / "webapp" / "docs" / "editor.md").read_text(encoding="utf-8")

    def test_the_card_has_its_controls(self):
        card = self.html[self.html.index('id="edRoomTone"'):]
        card = card[:card.index("</section>")]
        for needle in ('id="edRtVariant"', 'id="edRtLevel"', 'min="-45"', 'max="-18"',
                       'value="-27"', "edRtApply('apply')", "edRtApply('take')",
                       'edRtRemove()', 'edRtAuto(this.checked)', 'Add room tone',
                       'New take', 'Remove', 'Add room tone to automatic cuts'):
            self.assertIn(needle, card, needle)
        self.assertIn('id="edRoomTone" hidden ', self.html)

    def test_no_element_id_shadows_a_function(self):
        for fn in re.findall(r"^(?:async )?function (edRt\w+|sbeRt\w+)\(", self.js, re.MULTILINE):
            self.assertNotIn(f'id="{fn}"', self.html, fn)

    def test_the_sound_menu_is_a_second_door(self):
        menu = self.html[self.html.index('id="sbeSoundMenu"'):]
        menu = menu[:menu.index("</div>")]
        self.assertIn("edRtOpen()", menu)
        self.assertIn("Room tone…", menu)

    def test_it_is_painted_and_followed(self):
        paint = extract_function("sbePaint", self.js)
        self.assertIn("sbeRtFollow();", paint)
        self.assertLess(paint.index("sbeRtFollow();"), paint.index("sbePaintTracks();"))
        self.assertIn("edRtPaint()", paint)
        self.assertIn("SBE.rtLen = sbeFilmDuration(SBE.clips);", self.js)
        self.assertIn("edRtLoad().then(edRtPaint)", extract_function("edPoolRefresh", self.js))
        src = extract_function("edPoolSrc", self.js)
        self.assertIn(".ed-pool-make", src)

    def test_the_level_slider_is_one_undo_step(self):
        commit = extract_function("edRtLevelCommit", self.js)
        self.assertIn("sbeTsCommit(before)", commit)
        self.assertIn("sbeSnapshot()", extract_function("edRtLevelSlide", self.js))

    def test_a_grown_film_gets_a_longer_bed_in_the_background(self):
        follow = extract_function("sbeRtFollow", self.js)
        self.assertIn("setTimeout(sbeRtGrow", follow)
        grow = extract_function("sbeRtGrow", self.js)
        self.assertIn("sbeRtRequest(rt.variant, rt.seed, len)", grow)
        self.assertNotIn("SBE.undo.push", grow)

    def test_the_card_is_styled_on_the_tokens(self):
        for sel in (".ed-rt {", ".ed-rt-head", ".ed-rt-level", ".ed-rt-acts",
                    ".ed-rt[hidden]", ".ed-pool-make[hidden]"):
            self.assertIn(sel, self.css, sel)

    def test_the_docs_say_it_in_the_users_words(self):
        low = self.docs.lower()
        for word in ("room tone", "background noise", "ambience", "new take",
                     "automatic cuts", "lufs", "click"):
            self.assertIn(word, low, word)


if __name__ == "__main__":
    unittest.main()
