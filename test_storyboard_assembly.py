#!/usr/bin/env python3
"""Tests for the storyboard EXPORT: the folder, the manifest, and the film.

Two things are locked here.

1. **Assembly.** A film's shots are not uniform — the draft pass writes
   640x448, delivery writes 1024x576, H3's export lands 768x448 or 1280x720,
   LTX carries 48 kHz sound and H3 carries 32 kHz, and a character test may
   carry none at all. A bare concat DEMUXER either refuses that or writes a
   file that plays the first shot and garbles the rest, so the panel builds a
   concat FILTER graph that normalises every segment first. These tests assert
   the graph, not a happy-path smoke render.

2. **Stop.** Pressing Stop must cancel the shot that is RENDERING, not only the
   ones still queued, and it must do it through the panel's one existing
   job-cancel path without touching anybody else's job.

No GPU, no weights, no model download. Everything that shells out is either
mocked or gated on a real ffmpeg being present (the two fixture clips are
lavfi test patterns, a fraction of a second each).

Run:  python3 -m unittest test_storyboard_assembly
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function  # noqa: E402
import mlx_ltx_panel as panel  # noqa: E402
import storyboard  # noqa: E402

NODE = shutil.which("node")


def _probe(w, h, duration, *, audio=True, rate=48000) -> dict:
    return {"w": w, "h": h, "duration": duration,
            "has_audio": audio, "sample_rate": rate if audio else 0}


def _clip(n, path, **kw):
    s = {"n": n, "mode": "text", "engine": "ltx", "prompt": f"shot {n} happens",
         "duration_s": 5.0, "seed": 1000 + n, "refs": [], "status": "done",
         "draft_output": str(path)}
    s.update(kw)
    return s


def _board(shots, **kw) -> dict:
    b = {"schema": 1, "id": "sb_assembly_test", "title": "The Long Walk",
         "created_at": 1_700_000_000, "shots": shots, "policy": {}, "cast": []}
    b.update(kw)
    return b


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _write_fake_clip(p: Path, body: bytes = b"not really an mp4") -> Path:
    p.write_bytes(body)
    return p


# =============================================================================
# The filter graph — the mixed-geometry problem itself
# =============================================================================
class FilterGraph(unittest.TestCase):
    MIXED = [
        ("draft.mp4", _probe(640, 448, 3.041667)),                  # LTX draft
        ("delivery.mp4", _probe(1024, 576, 5.041667)),              # LTX delivery
        ("h3.mp4", _probe(768, 448, 5.125, rate=32000)),            # H3, 32 kHz
        ("silent.mp4", _probe(1024, 576, 2.0, audio=False)),        # no sound
    ]

    def graph(self, probes=None, w=1024, h=576, rate=48000, pix="yuv420p",
              **kw):
        g, label = panel._sb_film_filtergraph(probes or self.MIXED, w, h, rate,
                                              pix, **kw)
        return g, label

    def test_every_segment_is_scaled_and_padded_to_one_geometry(self):
        g, _ = self.graph()
        for idx in range(len(self.MIXED)):
            self.assertIn(
                f"[{idx}:v]fps=24,scale=1024:576:"
                f"force_original_aspect_ratio=decrease,"
                f"pad=1024:576:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p"
                f"[v{idx}]", g)

    def test_no_shot_is_ever_cropped_or_stretched(self):
        # force_original_aspect_ratio=decrease + pad is the "bars, never crop"
        # recipe; `crop` and force_original_aspect_ratio=increase are the two
        # ways to silently lose picture, and neither may appear.
        g, _ = self.graph()
        self.assertNotIn("crop", g)
        self.assertNotIn("force_original_aspect_ratio=increase", g)
        # setsar=1 or a 4:3-flagged source skews the whole cut.
        self.assertEqual(g.count("setsar=1"), len(self.MIXED))

    def test_one_frame_rate_for_the_whole_film(self):
        g, _ = self.graph()
        self.assertEqual(g.count(f"fps={panel.FPS}"), len(self.MIXED))

    def test_pixel_format_is_uniform_and_comes_from_the_settings(self):
        # concat negotiates ONE pixel format across its inputs; making that
        # format the one the encode was going to write anyway avoids a second
        # conversion and keeps the film inside the codec gate's contract.
        g, _ = self.graph(pix="yuv444p")
        self.assertEqual(g.count("format=yuv444p"), len(self.MIXED))
        self.assertNotIn("format=yuv420p", g)

    def test_a_silent_clip_gets_synthesised_silence(self):
        # THE failure this exists for: an uneven audio-stream count and ffmpeg
        # refuses to build the graph at all.
        g, _ = self.graph()
        self.assertIn(
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=2.000000[a3]", g)

    def test_every_segment_has_exactly_one_audio_pad(self):
        g, _ = self.graph()
        for idx in range(len(self.MIXED)):
            self.assertEqual(g.count(f"[a{idx}]"), 2,  # produced once, consumed once
                             f"segment {idx}")

    def test_real_audio_is_resampled_to_the_films_one_rate(self):
        g, _ = self.graph(rate=48000)
        # The 32 kHz H3 track is segment 2 and must be brought to 48 kHz, not
        # left to fight the LTX segments inside concat.
        self.assertIn("[2:a]aresample=48000,", g)
        self.assertEqual(g.count("aresample=48000"), 3)  # the three sounded clips

    def test_audio_is_padded_and_trimmed_to_the_PICTURE_length(self):
        # An H3 clip's AAC track runs ~42 ms past its last frame. Believing the
        # container would drift the sound further behind the picture with every
        # shot, so each segment's audio is pinned to its VIDEO duration.
        g, _ = self.graph()
        self.assertIn("apad,atrim=0:5.125000,asetpts=PTS-STARTPTS[a2]", g)

    def test_concat_is_the_filter_not_the_demuxer(self):
        g, _ = self.graph()
        self.assertIn("concat=n=4:v=1:a=1[vcat][aout]", g)

    def test_bt709_tagging_rides_the_same_helper_as_every_other_export(self):
        with mock.patch.object(panel, "bt709_vf", return_value="setparams=x"):
            g, label = self.graph()
        self.assertEqual(label, "[vout]")
        self.assertTrue(g.endswith("[vcat]setparams=x[vout]"))

    def test_an_ffmpeg_without_setparams_still_produces_a_valid_graph(self):
        with mock.patch.object(panel, "bt709_vf", return_value=""):
            g, label = self.graph()
        self.assertEqual(label, "[vcat]")
        self.assertTrue(g.endswith("concat=n=4:v=1:a=1[vcat][aout]"))

    def test_a_single_clip_is_still_a_film(self):
        g, _ = self.graph(probes=self.MIXED[:1])
        self.assertIn("concat=n=1:v=1:a=1", g)


# =============================================================================
# The auto-edit — trimming and a soundtrack, in the SAME filtergraph pass
# =============================================================================
class AutoEditGraph(unittest.TestCase):
    """The opt-in path. Two properties matter more than any other: the default
    graph must be untouched, and the trim must happen HERE rather than in a
    second ffmpeg run that would decode and re-encode every shot twice."""

    PROBES = [
        (Path("/x/S01.mp4"), _probe(1920, 1080, 10.125)),
        (Path("/x/S02.mp4"), _probe(1024, 576, 5.167)),
        (Path("/x/S03.mp4"), _probe(1024, 576, 5.167, audio=False)),
    ]
    PLAN = [
        {"path": "/x/S01.mp4", "start": 0.5, "end": 5.25},
        {"path": "/x/S02.mp4", "start": 0.75, "end": 3.0},
        {"path": "/x/S03.mp4", "start": 1.0, "end": 3.5},
    ]

    def graph(self, probes=None, **kw):
        return panel._sb_film_filtergraph(probes or self.PROBES, 1920, 1080,
                                          48000, "yuv420p", **kw)

    # ---- the default cannot move -----------------------------------------
    def test_no_plan_and_no_music_is_byte_identical_to_the_old_graph(self):
        # The literal below is the graph as it was BEFORE the auto-editor
        # existed. If this test has to be edited, the default export changed,
        # and that is exactly the thing that must not happen by accident.
        expected = (
            "[0:v]fps=24,scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v0];"
            "[0:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            "apad,atrim=0:10.125000,asetpts=PTS-STARTPTS[a0];"
            "[1:v]fps=24,scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v1];"
            "[1:a]aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo,"
            "apad,atrim=0:5.167000,asetpts=PTS-STARTPTS[a1];"
            "[2:v]fps=24,scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v2];"
            "anullsrc=channel_layout=stereo:sample_rate=48000:d=5.167000[a2];"
            "[v0][a0][v1][a1][v2][a2]concat=n=3:v=1:a=1[vcat][aout]")
        with mock.patch.object(panel, "bt709_vf", return_value=""):
            g, label = self.graph()
            self.assertEqual(g, expected)
            self.assertEqual(label, "[vcat]")
            # ...and passing the arguments as None must be the same thing.
            self.assertEqual(self.graph(cuts=None, music=None)[0], expected)
            self.assertEqual(self.graph(cuts={}, music=None)[0], expected)

    # ---- trimming ---------------------------------------------------------
    def test_each_segment_is_trimmed_to_its_own_window(self):
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN))
        self.assertIn("[0:v]trim=start=0.500000:end=5.250000,"
                      "setpts=PTS-STARTPTS,fps=24,", g)
        self.assertIn("[1:v]trim=start=0.750000:end=3.000000,"
                      "setpts=PTS-STARTPTS,fps=24,", g)

    def test_the_trim_comes_BEFORE_the_rate_and_scale_conversions(self):
        # trim works on SOURCE timestamps. Put it after `fps` and the window
        # you asked for is not the window you get.
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN))
        for idx in range(3):
            chain = next(c for c in g.split(";") if c.startswith(f"[{idx}:v]"))
            self.assertLess(chain.index("trim=start"), chain.index("fps="))
            self.assertLess(chain.index("setpts=PTS-STARTPTS"),
                            chain.index("scale="))

    def test_the_audio_is_trimmed_to_the_same_window_and_pinned_to_it(self):
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN))
        self.assertIn("[1:a]atrim=start=0.750000:end=3.000000,"
                      "asetpts=PTS-STARTPTS,aresample=48000,", g)
        # the pad/trim tail still pins the sound to the PICTURE's new length
        self.assertIn("apad,atrim=0:2.250000,asetpts=PTS-STARTPTS[a1]", g)

    def test_a_silent_clip_gets_silence_of_the_TRIMMED_length(self):
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN))
        self.assertIn("sample_rate=48000:d=2.500000[a2]", g)
        self.assertNotIn("d=5.167000", g)

    def test_a_clip_with_no_plan_entry_plays_whole(self):
        # A plan can be partial. The clips it does not mention are not
        # silently dropped and not silently trimmed to zero.
        cuts = panel._sb_cut_index(self.PLAN[:1])
        g, _ = self.graph(cuts=cuts)
        self.assertIn("[0:v]trim=start=0.500000", g)
        self.assertIn("[1:v]fps=24,", g)
        self.assertIn("apad,atrim=0:5.167000,asetpts=PTS-STARTPTS[a1]", g)

    def test_a_window_longer_than_its_clip_cannot_stretch_the_segment(self):
        cuts = panel._sb_cut_index([{"path": "/x/S02.mp4",
                                     "start": 0.0, "end": 99.0}])
        g, _ = self.graph(cuts=cuts)
        self.assertIn("apad,atrim=0:5.167000,asetpts=PTS-STARTPTS[a1]", g)

    def test_the_plan_is_matched_by_PATH_not_by_position(self):
        # _sb_assemble_film drops clips ffprobe cannot read. A positional plan
        # would then apply shot 2's window to shot 3 — silently, and only when
        # a file is broken.
        idx = panel._sb_cut_index(self.PLAN)
        self.assertEqual(sorted(idx), ["/x/S01.mp4", "/x/S02.mp4",
                                       "/x/S03.mp4"])
        short = [self.PROBES[0], self.PROBES[2]]        # S02 unreadable
        g, _ = panel._sb_film_filtergraph(short, 1920, 1080, 48000,
                                          "yuv420p", cuts=idx)
        self.assertIn("[1:v]trim=start=1.000000:end=3.500000", g)

    def test_a_malformed_plan_entry_is_ignored_rather_than_fatal(self):
        idx = panel._sb_cut_index([
            {"path": "/x/S01.mp4", "start": 1.0, "end": 2.0},
            {"path": "/x/S02.mp4", "start": 3.0, "end": 3.0},   # zero length
            {"path": "/x/S03.mp4", "start": "nope", "end": 4.0},
            {"start": 0.0, "end": 1.0},                          # no path
            "not a dict",
        ])
        self.assertEqual(list(idx), ["/x/S01.mp4"])
        self.assertEqual(panel._sb_cut_index(None), {})
        self.assertEqual(panel._sb_cut_index([]), {})

    # ---- the soundtrack ---------------------------------------------------
    def test_music_replaces_the_clip_audio_entirely(self):
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN),
                          music={"path": "/x/song.mp3", "start": 0.0})
        self.assertIn("concat=n=3:v=1:a=0[vcat]", g)
        for idx in range(3):
            self.assertNotIn(f"[a{idx}]", g)
        self.assertNotIn("anullsrc", g)

    def test_the_music_is_trimmed_to_the_length_of_the_CUT(self):
        # 4.75 + 2.25 + 2.5 = 9.5 s of film, not 20.459 s of source clips.
        g, _ = self.graph(cuts=panel._sb_cut_index(self.PLAN),
                          music={"path": "/x/song.mp3", "start": 0.0})
        self.assertIn("[3:a]atrim=start=0.000000,asetpts=PTS-STARTPTS,"
                      "aresample=48000,", g)
        self.assertIn("apad,atrim=0:9.500000,asetpts=PTS-STARTPTS[aout]", g)

    def test_music_without_a_plan_still_lays_under_the_whole_clips(self):
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0})
        self.assertIn("apad,atrim=0:20.459000,asetpts=PTS-STARTPTS[aout]", g)

    # ---- the soundtrack, mode `under` -------------------------------------
    # `replace` silences the clips. That is right for a beat-cut music video
    # and catastrophic for anything with a voice in it: attaching a bed to a
    # dialogue film deleted the performance. These pin the other mode.
    def test_under_keeps_the_clip_audio_instead_of_dropping_it(self):
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertIn("concat=n=3:v=1:a=1[vcat][acat]", g)
        for idx in range(3):
            self.assertIn(f"[a{idx}]", g)      # every segment still has sound

    def test_under_applies_no_gain_the_document_did_not_ask_for(self):
        # THE DEFECT THIS PINS. The graph used to hold every `under` bed at a
        # hard-coded 0.20 and then duck it through `sidechaincompress`, neither
        # of them in any document and neither of them in the preview — so the
        # render invented a mix the Editor never played. A bed with no curve
        # now gets no filter at all, exactly like a clip with no envelope.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertNotIn("sidechaincompress", g)
        self.assertNotIn("volume=0.20", g)
        self.assertIn("asetpts=PTS-STARTPTS[bed]", g)

    def test_under_mixes_the_dialogue_and_the_bed_directly(self):
        # No `asplit` either: it existed only to feed the compressor's key.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertNotIn("asplit", g)
        self.assertIn("[acat][bed]amix=", g)

    def test_under_carries_the_documents_own_bed_curve(self):
        # The one field the graph always read and nothing ever set. A fade on
        # the soundtrack was expressible in the model, drawn on the strip, and
        # absent from the file.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under",
                                 "gain": [[0.0, 1.0], [4.0, 0.25]]})
        self.assertIn("volume=volume='", g)
        self.assertIn("eval=frame", g)
        self.assertIn("[bed]", g)

    def test_replace_carries_the_bed_curve_too(self):
        # "Fade the music out under the last shot" is the same request whether
        # or not the clips kept their own sound.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "gain": [[0.0, 1.0], [4.0, 0.0]]})
        self.assertIn("volume=volume='", g)
        self.assertIn("[aout]", g)

    def test_under_does_not_let_amix_rescale_the_mix(self):
        # normalize=0: amix otherwise halves both inputs to protect a headroom
        # budget the bed gain has already set deliberately.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertIn("normalize=0", g)
        self.assertIn("duration=first", g)

    def test_under_puts_a_ceiling_on_the_sum(self):
        # normalize=0 means nothing is protecting the sum, and hot engine
        # dialogue plus a bed peaked the first under-mix film at 1.31 with 1341
        # hard-clipped samples. The ceiling is the last thing before [aout].
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertIn(f"asoftclip=type=tanh:threshold={panel._sb_mix_ceiling():g}[aout]", g)

    def test_the_ceiling_is_not_alimiter(self):
        # alimiter is the filter this obviously wants and it is a NO-OP on
        # float samples in this ffmpeg: measured on the real mix, in 1.3075 ->
        # out 1.3075, unchanged by level=disabled or by an aformat to s16 on
        # either side. It fails silently and the failure is audible, so the
        # name is banned here rather than left as an inviting "cleanup".
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under"})
        self.assertNotIn("alimiter", g)

    def test_replace_is_still_the_default_when_no_mode_is_given(self):
        # Every existing caller passes no mode and must keep the old graph.
        without = self.graph(music={"path": "/x/song.mp3", "start": 0.0})[0]
        explicit = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                     "mode": "replace"})[0]
        self.assertEqual(without, explicit)
        self.assertNotIn("sidechaincompress", without)

    def test_an_unknown_mode_falls_back_to_replace_rather_than_breaking(self):
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "sidechain-please"})
        self.assertNotIn("sidechaincompress", g)
        self.assertIn("concat=n=3:v=1:a=0[vcat]", g)

    # ---- the soundtrack, as an object (Wave 4) ----------------------------
    def test_a_trimmed_soundtrack_stops_where_the_trim_says(self):
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 4.0,
                                 "end": 12.0})
        self.assertIn("[3:a]atrim=start=4.000000:end=12.000000,"
                      "asetpts=PTS-STARTPTS,aresample=48000,", g)

    def test_a_delayed_soundtrack_arrives_late_and_in_both_channels(self):
        # adelay and not a longer atrim: silence in front of a track is not a
        # property of the track. `all=1` because the default delays the FIRST
        # channel only — a bed that arrives in the left speaker.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 0.0,
                                 "delay": 2.5})
        self.assertIn("[3:a]atrim=start=0.000000,asetpts=PTS-STARTPTS,"
                      "adelay=delays=2500:all=1,aresample=48000,", g)

    def test_the_trim_and_the_delay_survive_the_under_mix(self):
        # The duck and the ceiling are what `under` is for; a music object
        # that only worked in `replace` would be half a feature.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 1.0,
                                 "end": 9.0, "delay": 3.0, "mode": "under"})
        self.assertIn("[3:a]atrim=start=1.000000:end=9.000000,"
                      "asetpts=PTS-STARTPTS,adelay=delays=3000:all=1,", g)
        self.assertIn("[acat][bed]amix=", g)
        self.assertIn(f"asoftclip=type=tanh:threshold={panel._sb_mix_ceiling():g}[aout]", g)

    def test_an_untouched_soundtrack_builds_the_graph_it_always_did(self):
        # The whole safety of Wave 4's schema: absent trims, no delay, and the
        # string is character-for-character the one every film so far used.
        plain = self.graph(music={"path": "/x/song.mp3", "start": 0.0})[0]
        for extra in ({"end": None, "delay": 0.0},
                      {"end": None}, {"delay": 0}):
            with self.subTest(extra=extra):
                self.assertEqual(
                    self.graph(music=dict({"path": "/x/song.mp3",
                                           "start": 0.0}, **extra))[0], plain)
        self.assertNotIn("adelay", plain)
        self.assertNotIn(":end=", plain)

    def test_an_end_that_is_not_after_the_start_is_ignored(self):
        # A window the trims closed is not a zero-length atrim ffmpeg refuses.
        g, _ = self.graph(music={"path": "/x/song.mp3", "start": 5.0,
                                 "end": 5.0})
        self.assertIn("[3:a]atrim=start=5.000000,asetpts", g)
        self.assertNotIn(":end=", g)

    # ---- J-cuts and L-cuts (Wave 4) --------------------------------------
    # "You can edit dialogue in a way that you hear her voice while you're
    # seeing him... I need to be able to leave some of the audio and drag only
    # the image."
    def _split_segs(self, delta=-1.0, alen=None):
        probe = {"w": 1920, "h": 1080, "duration": 8.0,
                 "sample_rate": 48000, "has_audio": True}
        with mock.patch.object(panel, "_sb_probe_clip", return_value=probe):
            segs, _unread, _inputs = panel._sb_timeline_segments([
                {"path": "/x/a.mp4", "start": 0.0, "end": 4.0,
                 "film_start": 0.0},
                {"path": "/x/b.mp4", "start": 0.0, "end": 4.0,
                 "film_start": 4.0,
             "audio": {"start": 0.0, "end": alen if alen else 4.0,
                       "film_start": 4.0 + delta}},
            ])
        return segs

    def test_nothing_unlinked_builds_the_graph_it_always_did(self):
        # The safety the whole schema rests on: absent means linked.
        plan = panel._sb_split_audio_plan(
            [{"kind": "video", "info": {"has_audio": True, "duration": 4.0},
              "window": {"start": 0.0, "end": 4.0}}])
        self.assertFalse(plan["split"])

    def test_an_unlinked_clip_puts_its_sound_at_its_own_offset(self):
        plan = panel._sb_split_audio_plan([
            {"kind": "video", "info": {"has_audio": True, "duration": 4.0},
             "window": {"start": 0.0, "end": 4.0}},
            {"kind": "video", "info": {"has_audio": True, "duration": 4.0},
             "window": {"start": 0.0, "end": 4.0},
             "audio": {"start": 0.0, "end": 4.0, "delta": -1.0}},
        ])
        self.assertTrue(plan["split"])
        self.assertEqual(plan["total"], 8.0)
        # The J-cut: clip 2's sound leads its picture in by a second, so the
        # first clip's sound is trimmed to make room — one lane, still.
        lanes = {L["idx"]: L for L in plan["lanes"]}
        self.assertEqual(lanes[1]["at"], 3.0)
        self.assertEqual(lanes[0]["at"], 0.0)
        self.assertAlmostEqual(lanes[0]["len"], 3.0, places=6)

    def test_the_offset_is_a_delta_so_a_closed_gap_cannot_desync_it(self):
        # The assembler concatenates; an absolute film second taken from the
        # editor's clock would put the sound where the picture used to be.
        plan = panel._sb_split_audio_plan([
            {"kind": "slug", "duration": 2.0},
            {"kind": "video", "info": {"has_audio": True, "duration": 4.0},
             "window": {"start": 0.0, "end": 4.0},
             "audio": {"start": 1.0, "end": 4.0, "delta": 1.0}},
        ])
        lane = plan["lanes"][0]
        self.assertEqual(lane["at"], 3.0)        # 2s of slug + 1s of delta
        self.assertEqual([lane["start"], lane["end"]], [1.0, 4.0])

    def test_a_j_cut_on_the_first_clip_has_nowhere_to_lead_from(self):
        plan = panel._sb_split_audio_plan([
            {"kind": "video", "info": {"has_audio": True, "duration": 4.0},
             "window": {"start": 0.0, "end": 4.0},
             "audio": {"start": 0.0, "end": 4.0, "delta": -2.0}},
        ])
        lane = plan["lanes"][0]
        self.assertEqual(lane["at"], 0.0)
        self.assertEqual(lane["start"], 2.0)     # the head is cut, not shifted

    def test_an_l_cut_past_the_last_frame_is_cut_to_the_film(self):
        plan = panel._sb_split_audio_plan([
            {"kind": "video", "info": {"has_audio": True, "duration": 8.0},
             "window": {"start": 0.0, "end": 4.0},
             "audio": {"start": 0.0, "end": 8.0, "delta": 0.0}},
        ])
        self.assertEqual(plan["total"], 4.0)
        self.assertEqual(plan["lanes"][0]["len"], 4.0)

    def test_the_split_graph_is_one_audio_lane_of_sound_and_silence(self):
        chains = panel._sb_split_audio_chains(
            [{"idx": 0, "start": 0.0, "end": 2.0, "at": 1.0, "len": 2.0}],
            5.0, 48000, "[aout]")
        g = ";".join(chains)
        # concat and NOT amix: concat cannot sum, so this graph is incapable
        # of becoming the mixer the refuse list bans.
        self.assertIn("concat=n=3:v=0:a=1[aout]", g)
        self.assertNotIn("amix", g)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000:d=1.000000", g)
        self.assertIn("d=2.000000", g)           # the tail silence

    def test_a_split_film_still_mixes_the_music_and_keeps_its_ceiling(self):
        # The two things the whole mix rests on, and the two a restructure
        # would quietly drop. The bed carries its own curve; the ceiling is
        # the safety net on the sum and belongs to no document.
        segs = self._split_segs()
        g, _ = self.graph(segments=segs,
                          music={"path": "/x/song.mp3", "start": 0.0,
                                 "mode": "under",
                                 "gain": [[0.0, 0.4], [3.0, 0.4]]})
        self.assertIn("concat=n=2:v=1:a=0[vcat]", g)
        self.assertIn(":v=0:a=1[acat]", g)
        self.assertIn("[acat][bed]amix=", g)
        self.assertIn("volume=volume='", g)
        self.assertIn(f"asoftclip=type=tanh:threshold={panel._sb_mix_ceiling():g}[aout]", g)

    def test_the_sound_is_padded_to_its_own_length_not_the_pictures(self):
        segs = self._split_segs(delta=-1.0, alen=2.0)
        g, _ = self.graph(segments=segs)
        self.assertIn("apad,atrim=0:2.000000,asetpts=PTS-STARTPTS[a1]", g)

    def test_segment_index_is_still_not_input_index_when_split(self):
        # The trap SegmentIndexIsNotInputIndex exists for: a slug consumes no
        # -i, so the soundtrack's index is the INPUT count, and a split lane
        # addresses its clip by SEGMENT index.
        probe = {"w": 1920, "h": 1080, "duration": 8.0,
                 "sample_rate": 48000, "has_audio": True}
        with mock.patch.object(panel, "_sb_probe_clip", return_value=probe):
            segs, unreadable, inputs = panel._sb_timeline_segments([
                {"kind": "slug", "start": 0.0, "end": 2.0, "film_start": 0.0},
                {"path": "/x/b.mp4", "start": 0.0, "end": 4.0,
                 "film_start": 2.0,
                 "audio": {"start": 0.0, "end": 4.0, "film_start": 1.0}},
            ])
        self.assertEqual(segs[0]["input"], None)
        self.assertEqual(segs[1]["input"], 0)
        self.assertEqual(len(inputs), 1)
        self.assertEqual(segs[1]["audio"]["delta"], -1.0)

    def test_bt709_still_rides_the_same_helper_when_auto_edited(self):
        with mock.patch.object(panel, "bt709_vf", return_value="setparams=x"):
            g, label = self.graph(cuts=panel._sb_cut_index(self.PLAN),
                                  music={"path": "/x/song.mp3", "start": 0.0})
        self.assertEqual(label, "[vout]")
        self.assertIn("[vcat]setparams=x[vout]", g)


# =============================================================================
# _sb_assemble_film — target geometry, argv, and failure that stays contained
# =============================================================================
class AssembleFilm(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _run(self, probes, *, ffmpeg_side_effect=None, pix="yuv420p", crf="18"):
        """Assemble with ffprobe + ffmpeg mocked. Returns (result, argv)."""
        clips = []
        for i, _ in enumerate(probes):
            clips.append(_write_fake_clip(self.dir / f"S{i:02d}.mp4"))
        seen = {}

        def fake_probe(path):
            return dict(probes[clips.index(Path(path))])

        def fake_ffmpeg(cmd, label):
            seen["cmd"] = list(cmd)
            if ffmpeg_side_effect:
                raise ffmpeg_side_effect
            Path(cmd[-1]).write_bytes(b"film")
            return "", ""

        with mock.patch.object(panel, "_sb_probe_clip", side_effect=fake_probe), \
             mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=fake_ffmpeg), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"preset": "standard",
                                             "pix_fmt": pix, "crf": crf}):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4")
        return res, seen.get("cmd", [])

    def test_target_geometry_is_the_largest_picture_in_the_cut(self):
        res, cmd = self._run([_probe(640, 448, 3.0),
                              _probe(1024, 576, 5.0),
                              _probe(768, 448, 5.0)])
        self.assertTrue(res["ok"])
        self.assertEqual((res["width"], res["height"]), (1024, 576))
        # Widest and tallest can come from DIFFERENT clips; the canvas takes
        # both so nothing is ever scaled down.
        res, _ = self._run([_probe(1280, 448, 3.0), _probe(640, 720, 5.0)])
        self.assertEqual((res["width"], res["height"]), (1280, 720))

    def test_odd_dimensions_are_rounded_up_to_even(self):
        # H.264 4:2:0 has no odd-dimension form; an odd canvas is a hard
        # encoder error at the very end of a long export.
        res, _ = self._run([_probe(641, 449, 3.0)])
        self.assertEqual((res["width"], res["height"]), (642, 450))

    def test_sample_rate_is_the_highest_present_not_a_guess(self):
        res, cmd = self._run([_probe(640, 448, 3.0, rate=32000),
                              _probe(640, 448, 3.0, rate=48000)])
        self.assertEqual(res["sample_rate"], 48000)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "48000")

    def test_an_all_silent_film_falls_back_to_the_default_rate(self):
        res, _ = self._run([_probe(640, 448, 3.0, audio=False)])
        self.assertEqual(res["sample_rate"], panel._SB_FILM_SAMPLE_RATE)

    def test_the_encode_uses_the_panels_own_output_settings(self):
        _, cmd = self._run([_probe(640, 448, 3.0)], pix="yuv444p", crf="0")
        self.assertEqual(cmd[cmd.index("-pix_fmt") + 1], "yuv444p")
        self.assertEqual(cmd[cmd.index("-crf") + 1], "0")
        self.assertEqual(cmd[cmd.index("-c:v") + 1], "libx264")

    def test_faststart_and_bt709_are_present(self):
        _, cmd = self._run([_probe(640, 448, 3.0)])
        self.assertIn("+faststart", cmd)
        self.assertEqual(cmd[cmd.index("-movflags") + 1], "+faststart")
        for flag in panel.BT709_FLAGS:
            self.assertIn(flag, cmd)

    def test_argv_is_a_list_of_arguments_never_a_shell_string(self):
        _, cmd = self._run([_probe(640, 448, 3.0), _probe(640, 448, 3.0)])
        self.assertIsInstance(cmd, list)
        self.assertTrue(all(isinstance(a, str) for a in cmd))
        self.assertEqual(cmd.count("-i"), 2)

    def test_duration_reported_is_the_sum_of_the_included_clips(self):
        res, _ = self._run([_probe(640, 448, 3.041667),
                            _probe(640, 448, 5.125)])
        self.assertAlmostEqual(res["duration"], 8.167, places=3)

    def test_an_unreadable_clip_is_dropped_from_the_cut_not_fatal(self):
        clips = [_write_fake_clip(self.dir / "good.mp4"),
                 _write_fake_clip(self.dir / "torn.mp4")]
        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=[_probe(640, 448, 3.0), None]), \
             mock.patch.object(panel, "run_ffmpeg_tracked",
                               side_effect=lambda c, l: (Path(c[-1])
                                                         .write_bytes(b"f"), ("", ""))[1]), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4")
        self.assertTrue(res["ok"])
        self.assertEqual(res["clips"], 1)
        self.assertEqual(res["unreadable"], ["torn.mp4"])

    def test_nothing_readable_is_an_honest_refusal(self):
        clips = [_write_fake_clip(self.dir / "torn.mp4")]
        with mock.patch.object(panel, "_sb_probe_clip", return_value=None):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4")
        self.assertFalse(res["ok"])
        self.assertIn("ffprobe", res["error"])

    def test_no_clips_at_all_is_an_honest_refusal(self):
        res = panel._sb_assemble_film([], self.dir / "film.mp4")
        self.assertFalse(res["ok"])

    def test_a_failed_encode_leaves_no_half_written_film_behind(self):
        target = self.dir / "film.mp4"

        def half_write(cmd, label):
            Path(cmd[-1]).write_bytes(b"half a film")
            raise RuntimeError("storyboard film exited with code 1")

        clips = [_write_fake_clip(self.dir / "a.mp4")]
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value=_probe(640, 448, 3.0)), \
             mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=half_write), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            res = panel._sb_assemble_film(clips, target)
        self.assertFalse(res["ok"])
        self.assertIn("exited with code 1", res["error"])
        self.assertFalse(target.exists(), "a half-written mp4 looks finished in Finder")

    # ---- the auto-edit, at the assembler's own seam ----------------------
    def _run_planned(self, probes, plan, music=None):
        clips = [_write_fake_clip(self.dir / f"S{i:02d}.mp4")
                 for i in range(len(probes))]
        for entry, clip in zip(plan, clips):
            entry["path"] = str(clip)
        seen = {}

        def fake_probe(path):
            p = Path(path)
            if music and str(p) == str(music):
                return None
            return dict(probes[clips.index(p)])

        def fake_ffmpeg(cmd, label):
            seen["cmd"] = list(cmd)
            Path(cmd[-1]).write_bytes(b"film")
            return "", ""

        with mock.patch.object(panel, "_sb_probe_clip", side_effect=fake_probe), \
             mock.patch.object(panel, "_sb_probe_audio_rate", return_value=44100), \
             mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=fake_ffmpeg), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4",
                                          plan=plan, music=music)
        return res, seen.get("cmd", []), clips

    def test_the_reported_duration_is_the_CUT_not_the_source(self):
        res, _, _ = self._run_planned(
            [_probe(1024, 576, 10.125), _probe(1024, 576, 5.167)],
            [{"start": 1.0, "end": 5.0}, {"start": 0.5, "end": 3.0}])
        self.assertTrue(res["ok"])
        self.assertAlmostEqual(res["duration"], 6.5, places=3)
        self.assertTrue(res["trimmed"])
        self.assertFalse(res["music"])

    def test_a_soundtrack_is_one_extra_input_at_the_end(self):
        song = self.dir / "song.mp3"
        song.write_bytes(b"mp3")
        res, cmd, clips = self._run_planned(
            [_probe(1024, 576, 5.167)], [{"start": 0.0, "end": 4.0}],
            music=song)
        self.assertEqual(cmd.count("-i"), 2)
        self.assertEqual(cmd[cmd.index("-i", cmd.index(str(clips[0]))) + 1],
                         str(song))
        self.assertTrue(res["music"])

    def test_the_soundtracks_sample_rate_wins_over_the_clips(self):
        # 44.1 kHz song under 32 kHz H3 clips: resampling the song DOWN to a
        # rate no longer present in the film is a loss for nothing.
        song = self.dir / "song.mp3"
        song.write_bytes(b"mp3")
        res, cmd, _ = self._run_planned(
            [_probe(1024, 576, 5.167, rate=32000)],
            [{"start": 0.0, "end": 4.0}], music=song)
        self.assertEqual(res["sample_rate"], 44100)
        self.assertEqual(cmd[cmd.index("-ar") + 1], "44100")

    def test_source_geometry_still_decides_the_canvas_when_auto_edited(self):
        res, _, _ = self._run_planned(
            [_probe(640, 448, 5.0), _probe(1024, 576, 5.0)],
            [{"start": 0.0, "end": 2.0}, {"start": 1.0, "end": 4.0}])
        self.assertEqual((res["width"], res["height"]), (1024, 576))

    def test_an_unreadable_clip_does_not_shift_everyone_elses_window(self):
        clips = [_write_fake_clip(self.dir / n)
                 for n in ("a.mp4", "torn.mp4", "c.mp4")]
        plan = [{"path": str(clips[0]), "start": 0.0, "end": 2.0},
                {"path": str(clips[1]), "start": 0.0, "end": 2.0},
                {"path": str(clips[2]), "start": 3.0, "end": 4.0}]
        seen = {}

        def fake_ffmpeg(cmd, label):
            seen["graph"] = cmd[cmd.index("-filter_complex") + 1]
            Path(cmd[-1]).write_bytes(b"film")
            return "", ""

        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=[_probe(640, 448, 5.0), None,
                                            _probe(640, 448, 5.0)]), \
             mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=fake_ffmpeg), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4",
                                          plan=plan)
        self.assertEqual(res["clips"], 2)
        # c.mp4 became input 1 — it must still get ITS window, 3.0-4.0.
        self.assertIn("[1:v]trim=start=3.000000:end=4.000000", seen["graph"])
        self.assertAlmostEqual(res["duration"], 3.0, places=3)

    def test_a_clean_exit_that_wrote_nothing_is_still_a_failure(self):
        clips = [_write_fake_clip(self.dir / "a.mp4")]
        with mock.patch.object(panel, "_sb_probe_clip",
                               return_value=_probe(640, 448, 3.0)), \
             mock.patch.object(panel, "run_ffmpeg_tracked", return_value=("", "")), \
             mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "18"}):
            res = panel._sb_assemble_film(clips, self.dir / "film.mp4")
        self.assertFalse(res["ok"])
        self.assertIn("no film", res["error"])


# =============================================================================
# _sb_probe_clip — the only thing that decides a clip is usable
# =============================================================================
class ProbeClip(unittest.TestCase):
    LTX = {"streams": [{"codec_type": "video", "width": 1024, "height": 576,
                        "duration": "3.041667"},
                       {"codec_type": "audio", "sample_rate": "48000",
                        "duration": "3.010000"}],
           "format": {"duration": "3.041667"}}
    SILENT = {"streams": [{"codec_type": "video", "width": 640, "height": 448,
                           "duration": "2.000000"}],
              "format": {"duration": "2.000000"}}
    NO_STREAM_DURATION = {"streams": [{"codec_type": "video", "width": 800,
                                       "height": 600}],
                          "format": {"duration": "4.5"}}

    def _probe(self, payload, rc=0):
        proc = subprocess.CompletedProcess([], rc, json.dumps(payload), "")
        with mock.patch.object(panel.subprocess, "run", return_value=proc):
            return panel._sb_probe_clip("/nope/clip.mp4")

    def test_reads_geometry_duration_and_audio(self):
        got = self._probe(self.LTX)
        self.assertEqual(got, {"w": 1024, "h": 576, "duration": 3.041667,
                               "has_audio": True, "sample_rate": 48000})

    def test_a_silent_clip_is_valid_with_has_audio_false(self):
        got = self._probe(self.SILENT)
        self.assertFalse(got["has_audio"])
        self.assertEqual(got["sample_rate"], 0)

    def test_duration_prefers_the_video_stream_over_the_container(self):
        h3 = {"streams": [{"codec_type": "video", "width": 768, "height": 448,
                           "duration": "5.125000"},
                          {"codec_type": "audio", "sample_rate": "32000"}],
              "format": {"duration": "5.167000"}}
        self.assertEqual(self._probe(h3)["duration"], 5.125)

    def test_container_duration_is_the_fallback(self):
        self.assertEqual(self._probe(self.NO_STREAM_DURATION)["duration"], 4.5)

    def test_unreadable_inputs_return_None_rather_than_raising(self):
        self.assertIsNone(self._probe({}, rc=1))
        self.assertIsNone(self._probe({"streams": []}))
        self.assertIsNone(self._probe({"streams": [{"codec_type": "audio"}]}))
        with mock.patch.object(panel.subprocess, "run",
                               side_effect=OSError("no ffprobe")):
            self.assertIsNone(panel._sb_probe_clip("/nope/clip.mp4"))
        proc = subprocess.CompletedProcess([], 0, "not json at all", "")
        with mock.patch.object(panel.subprocess, "run", return_value=proc):
            self.assertIsNone(panel._sb_probe_clip("/nope/clip.mp4"))


# =============================================================================
# _sb_export — selection, ordering, and a failure that stays contained
# =============================================================================
class ExportSelection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.src = self.root / "gallery"
        self.src.mkdir()
        self._patch = mock.patch.object(panel, "OUTPUT", self.root / "out")
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def clip(self, name: str) -> Path:
        return _write_fake_clip(self.src / name, name.encode() * 64)

    def export(self, board, film=None, **export_kw):
        """Run the real _sb_export with the assembler mocked. -> (res, clips)."""
        seen = {}

        def fake_assemble(clips, out, **kw):
            seen["clips"] = [Path(c) for c in clips]
            seen["out"] = Path(out)
            # The soundtrack arguments are part of the call now (a music video
            # exports whole clips WITH the song as the bed), so the fake has
            # to see them or the thing under test cannot be asserted.
            seen["kw"] = kw
            if film is None:
                Path(out).write_bytes(b"film")
                return {"ok": True, "path": str(out), "clips": len(clips),
                        "width": 1024, "height": 576, "sample_rate": 48000,
                        "duration": 10.0, "unreadable": []}
            return film

        with mock.patch.object(panel, "_sb_assemble_film", side_effect=fake_assemble):
            res = panel._sb_export(board, **export_kw)
        return res, seen

    def test_shots_reach_the_film_in_n_order_however_the_board_stores_them(self):
        a, b, c = self.clip("a.mp4"), self.clip("b.mp4"), self.clip("c.mp4")
        board = _board([_clip(3, c), _clip(1, a), _clip(2, b)])
        res, seen = self.export(board)
        self.assertEqual([p.name for p in seen["clips"]],
                         ["S01_shot-1-happens.mp4", "S02_shot-2-happens.mp4",
                          "S03_shot-3-happens.mp4"])
        self.assertEqual(res["files"], [p.name for p in seen["clips"]])

    def test_a_soundtrack_reaches_the_assembler_on_the_whole_clip_path(self):
        # THE BUG THIS PINS: the whole-clip branch called the assembler with
        # the clip list and nothing else, so `_sb_export(music=...)` without
        # auto_edit accepted a soundtrack at the door and wrote a film with no
        # soundtrack in it. That is the path a music video wants — its shots
        # are already cut to the beat, and re-cutting them would slide every
        # a2v clip off the seconds of the song it was rendered against.
        song = _write_fake_clip(self.src / "song.wav", b"RIFF" * 64)
        _res, seen = self.export(_board([_clip(1, self.clip("a.mp4"))]),
                                 music=str(song), music_mode="replace")
        self.assertEqual(seen["kw"].get("music"), str(song))
        self.assertEqual(seen["kw"].get("music_mode"), "replace")
        self.assertIsNone(seen["kw"].get("plan"))

    def test_an_export_with_no_soundtrack_asks_for_none(self):
        _res, seen = self.export(_board([_clip(1, self.clip("a.mp4"))]))
        self.assertIsNone(seen["kw"].get("music"))

    def test_skipped_shots_are_excluded_from_the_film_and_the_folder(self):
        a, b = self.clip("a.mp4"), self.clip("b.mp4")
        board = _board([_clip(1, a), _clip(2, b, status="skipped")])
        res, seen = self.export(board)
        self.assertEqual(len(seen["clips"]), 1)
        self.assertEqual(seen["clips"][0].name, "S01_shot-1-happens.mp4")
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("## Cut", md)
        self.assertIn("**S02**", md)

    def test_a_shot_whose_file_is_gone_is_excluded_from_the_film(self):
        a = self.clip("a.mp4")
        board = _board([_clip(1, a),
                        _clip(2, self.src / "vanished.mp4"),
                        _clip(3, "")])
        _res, seen = self.export(board)
        self.assertEqual([p.name for p in seen["clips"]],
                         ["S01_shot-1-happens.mp4"])

    def test_the_film_is_built_from_the_copies_the_loop_just_made(self):
        # One selection, not two: whatever is in the folder is what is in the
        # film, and the assembler never reads the gallery.
        a = self.clip("a.mp4")
        res, seen = self.export(_board([_clip(1, a)]))
        self.assertEqual(seen["clips"][0].parent, Path(res["dir"]))
        self.assertTrue(seen["clips"][0].is_file())

    def test_delivery_beats_draft_exactly_as_the_copy_loop_decides(self):
        d, f = self.clip("draft.mp4"), self.clip("final.mp4")
        board = _board([_clip(1, d, final_output=str(f))])
        res, seen = self.export(board)
        self.assertEqual(_sha(seen["clips"][0]), _sha(f))
        self.assertIn("| delivery |",
                      (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8"))

    def test_source_clips_are_never_modified_or_moved(self):
        a, b = self.clip("a.mp4"), self.clip("b.mp4")
        before = {p: _sha(p) for p in (a, b)}
        self.export(_board([_clip(1, a), _clip(2, b)]))
        for p, digest in before.items():
            self.assertTrue(p.is_file(), f"{p.name} was moved")
            self.assertEqual(_sha(p), digest, f"{p.name} was modified")

    def test_the_film_is_named_after_the_board_and_returned(self):
        a = self.clip("a.mp4")
        res, seen = self.export(_board([_clip(1, a)], title="The Long Walk"))
        self.assertEqual(seen["out"].name, "the-long-walk_film.mp4")
        self.assertEqual(res["film_name"], "the-long-walk_film.mp4")
        self.assertEqual(res["film"], str(seen["out"]))
        self.assertIsNone(res["film_error"])

    def test_the_film_is_listed_in_the_markdown(self):
        a = self.clip("a.mp4")
        res, _ = self.export(_board([_clip(1, a)]))
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("**Film:**", md)
        self.assertIn("the-long-walk_film.mp4", md)
        self.assertIn("1024×576", md)

    def test_the_film_is_not_listed_among_the_clips(self):
        a = self.clip("a.mp4")
        res, _ = self.export(_board([_clip(1, a)]))
        self.assertNotIn("the-long-walk_film.mp4", res["files"])

    # ---- the failure contract ------------------------------------------
    def test_a_failed_assembly_still_produces_the_folder_and_the_manifest(self):
        a, b = self.clip("a.mp4"), self.clip("b.mp4")
        res, _ = self.export(_board([_clip(1, a), _clip(2, b)]),
                             film={"ok": False, "error": "ffmpeg is not installed"})
        self.assertTrue(res["ok"])
        dest = Path(res["dir"])
        self.assertTrue(dest.is_dir())
        self.assertEqual(len(res["files"]), 2)
        for name in res["files"]:
            self.assertTrue((dest / name).is_file())
        self.assertTrue((dest / "storyboard.md").is_file())
        self.assertEqual(json.loads((dest / "storyboard.json")
                                    .read_text(encoding="utf-8"))["title"],
                         "The Long Walk")

    def test_a_failed_assembly_is_reported_honestly_not_swallowed(self):
        a = self.clip("a.mp4")
        res, _ = self.export(_board([_clip(1, a)]),
                             film={"ok": False, "error": "ffmpeg is not installed"})
        self.assertIsNone(res["film"])
        self.assertIsNone(res["film_name"])
        self.assertEqual(res["film_error"], "ffmpeg is not installed")
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("not assembled", md)
        self.assertIn("ffmpeg is not installed", md)

    def test_an_assembler_that_RAISES_still_leaves_the_export_intact(self):
        a = self.clip("a.mp4")
        with mock.patch.object(panel, "_sb_assemble_film",
                               side_effect=OSError("disk went away")):
            res = panel._sb_export(_board([_clip(1, a)]))
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["files"]), 1)
        self.assertIn("disk went away", res["film_error"])
        self.assertTrue((Path(res["dir"]) / "storyboard.md").is_file())

    def test_a_board_with_nothing_renderable_exports_without_a_film(self):
        board = _board([_clip(1, self.src / "vanished.mp4")])
        with mock.patch.object(panel, "_sb_assemble_film") as m:
            res = panel._sb_export(board)
        m.assert_not_called()
        self.assertTrue(res["ok"])
        self.assertEqual(res["files"], [])
        self.assertIsNone(res["film"])
        self.assertIn("no shots", res["film_error"])

    # ---- the auto-edit is OPT-IN -----------------------------------------
    def test_by_default_the_export_never_even_asks_for_a_plan(self):
        a = self.clip("a.mp4")
        with mock.patch.object(panel, "_sb_plan_auto_edit") as planner, \
             mock.patch.object(panel, "_sb_assemble_film") as asm:
            asm.return_value = {"ok": True, "path": "/x/f.mp4", "clips": 1,
                                "width": 640, "height": 448,
                                "sample_rate": 48000, "duration": 3.0,
                                "unreadable": []}
            res = panel._sb_export(_board([_clip(1, a)]))
        planner.assert_not_called()
        # and the assembler is asked for EXACTLY what it was asked for before
        # the auto-editor existed: the clips, where to write them, no plan and
        # no soundtrack. (It used to be two positional arguments and no
        # keywords at all; the soundtrack keywords are always passed now, and
        # `music=None` builds the identical filtergraph — see the comment at
        # the call site. The assertion is on the ASK, not on the argv shape,
        # because the argv shape was never the promise.)
        self.assertEqual(len(asm.call_args.args), 2)
        self.assertIsNone(asm.call_args.kwargs.get("music"))
        self.assertIsNone(asm.call_args.kwargs.get("plan"))
        self.assertFalse(res["auto_edit"])
        self.assertNotIn("Auto-edited",
                         (Path(res["dir"]) / "storyboard.md")
                         .read_text(encoding="utf-8"))

    def test_auto_edit_true_plans_and_hands_the_plan_to_the_assembler(self):
        a = self.clip("a.mp4")
        plan = [{"path": "x", "start": 0.0, "end": 2.0, "n": 1,
                 "duration": 2.0, "film_start": 0.0, "film_end": 2.0,
                 "snap": {"kind": "downbeat", "shift_ms": -12.0},
                 "window": {"reason": "because"}, "notes": []}]
        with mock.patch.object(panel, "_sb_plan_auto_edit",
                               return_value=(plan, "auto-edit: 1 shots")), \
             mock.patch.object(panel, "_sb_assemble_film") as asm:
            asm.return_value = {"ok": True, "path": "/x/f.mp4", "clips": 1,
                                "width": 640, "height": 448,
                                "sample_rate": 48000, "duration": 2.0,
                                "unreadable": []}
            res = panel._sb_export(_board([_clip(1, a)]), auto_edit=True,
                                   music="/x/song.mp3", target_seconds=60.0)
        self.assertIs(asm.call_args.kwargs["plan"], plan)
        self.assertEqual(asm.call_args.kwargs["music"], "/x/song.mp3")
        self.assertTrue(res["auto_edit"])
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("## The edit", md)
        self.assertIn("1 of 1 cuts landed on the beat", md)
        self.assertIn("because", md)
        self.assertEqual(json.loads((Path(res["dir"]) / "storyboard_edit.json")
                                    .read_text(encoding="utf-8")), plan)

    def test_an_auto_edit_that_cannot_run_falls_back_to_whole_clips(self):
        # A missing or broken storyboard_edit.py costs you the auto-edit, not
        # the export — and it says so in the manifest.
        a = self.clip("a.mp4")
        with mock.patch.object(panel, "_sb_plan_auto_edit",
                               return_value=(None, "auto-edit unavailable "
                                                   "(ImportError: no numpy)")), \
             mock.patch.object(panel, "_sb_assemble_film") as asm:
            asm.return_value = {"ok": True, "path": "/x/f.mp4", "clips": 1,
                                "width": 640, "height": 448,
                                "sample_rate": 48000, "duration": 5.0,
                                "unreadable": []}
            res = panel._sb_export(_board([_clip(1, a)]), auto_edit=True)
        # No plan reached the assembler — the whole-clip call. (The soundtrack
        # keywords ride along on every call now; this export named no song, so
        # they are empty and the filtergraph is the one that always ran.)
        self.assertIsNone(asm.call_args.kwargs.get("plan"))
        self.assertIsNone(asm.call_args.kwargs.get("music"))
        self.assertTrue(res["ok"])
        self.assertFalse(res["auto_edit"])
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("Whole clips", md)
        self.assertIn("no numpy", md)

    def test_a_planner_that_raises_is_caught_at_the_seam(self):
        with mock.patch.dict("sys.modules", {"storyboard_edit": None}):
            plan, note = panel._sb_plan_auto_edit(["/x/a.mp4"])
        self.assertIsNone(plan)
        self.assertIn("auto-edit unavailable", note)

    def test_unreadable_clips_are_named_in_the_markdown(self):
        a = self.clip("a.mp4")
        res, _ = self.export(_board([_clip(1, a)]),
                             film={"ok": True, "path": "/x/f.mp4", "clips": 1,
                                   "width": 640, "height": 448,
                                   "sample_rate": 48000, "duration": 3.0,
                                   "unreadable": ["S02_torn.mp4"]})
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("S02_torn.mp4", md)


# =============================================================================
# Stop — the shot in flight is cancelled, through the panel's own path
# =============================================================================
# =============================================================================
# ONE ASSEMBLER — Export renders the CUT when there is one
# =============================================================================
class ExportDelegatesToTheTimeline(unittest.TestCase):
    """Two buttons that both said "make the film" made two different films.

    Export wrote `<slug>_film.mp4` from a second auto-editor over its own
    copies; the Editor's Render wrote `<slug>_timeline.mp4` from the cut, with
    the soundtrack. Same folder, same board, different files — and the Film
    screen could only tell them apart by a chip naming the button you pressed.
    Now: one name, and the human's arrangement wins whenever there is one.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)
        self.state = self.root / "state"
        self.out = self.root / "out"
        self.state.mkdir()
        self.src = self.root / "gallery"
        self.src.mkdir()
        for pt in (mock.patch.object(panel, "STATE_DIR", self.state),
                   mock.patch.object(panel, "OUTPUT", self.out),
                   mock.patch.object(panel, "push", lambda *a, **k: None)):
            pt.start()
            self.addCleanup(pt.stop)
        self.a = _write_fake_clip(self.src / "a.mp4", b"a" * 64)
        self.b = _write_fake_clip(self.src / "b.mp4", b"b" * 64)
        self.board = _board([_clip(1, self.a), _clip(2, self.b)],
                            id="sb_one_assembler")
        storyboard.save_storyboard(self.state, self.board)
        self.bdir = storyboard.board_dir(self.state, "sb_one_assembler")

    def save_edit(self, audio=None):
        import storyboard_editor as sedit
        clips = [sedit.new_clip(self.a, 1.0, 3.0, 0.0),
                 sedit.new_clip(self.b, 0.5, 2.5, 2.0)]
        sedit.save_edit(self.bdir, {"version": sedit.EDIT_VERSION,
                                    "board_id": "sb_one_assembler",
                                    "revision": 0, "source": "human",
                                    "audio": audio, "beats": None,
                                    "clips": clips, "settings": {}})

    def export(self, **kw):
        seen = {}

        def fake_assemble(clips, out, **akw):
            seen["clips"] = [Path(c) for c in clips]
            seen["out"] = Path(out)
            seen.update(akw)
            Path(out).write_bytes(b"film")
            return {"ok": True, "path": str(out), "clips": len(clips),
                    "width": 1024, "height": 576, "sample_rate": 48000,
                    "duration": 10.0, "unreadable": []}

        board = storyboard.load_storyboard(self.state, "sb_one_assembler")
        with mock.patch.object(panel, "_sb_assemble_film",
                               side_effect=fake_assemble):
            res = panel._sb_export(board, **kw)
        return res, seen

    def test_with_no_timeline_export_is_exactly_what_it_always_was(self):
        res, seen = self.export()
        self.assertTrue(res["ok"])
        # The copies in the folder, whole, in `n` order — the old behaviour.
        self.assertEqual([p.name for p in seen["clips"]],
                         ["S01_shot-1-happens.mp4", "S02_shot-2-happens.mp4"])
        self.assertIsNone(seen.get("plan"))
        self.assertEqual(seen["out"].name, "the-long-walk_film.mp4")

    def test_with_a_timeline_export_renders_the_CUT(self):
        self.save_edit()
        res, seen = self.export()
        self.assertTrue(res["ok"])
        # The SOURCES, not the copies — the cut points at the gallery files,
        # and the plan carries the windows the human trimmed.
        self.assertEqual([p.name for p in seen["clips"]], ["a.mp4", "b.mp4"])
        # `timeline=`, not `plan=` (Wave 2). A cut list is the only shape that
        # can hold a slug — which has no path and therefore cannot appear in
        # the `clips` argument at all — so the timeline door stopped passing a
        # plan the moment black became a kind of clip.
        self.assertIsNone(seen.get("plan"))
        plan = seen["timeline"]
        self.assertEqual(len(plan), 2)
        self.assertAlmostEqual(plan[0]["start"], 1.0)
        self.assertAlmostEqual(plan[0]["end"], 3.0)
        self.assertAlmostEqual(plan[1]["film_start"], 2.0)

    def test_both_doors_write_the_same_name(self):
        # The whole "two films in one folder" finding, in one assertion.
        self.save_edit()
        _res, seen = self.export()
        self.assertEqual(seen["out"].name, panel._sb_film_name(self.board))
        self.assertEqual(seen["out"].name, "the-long-walk_film.mp4")
        self.assertNotIn("_timeline", seen["out"].name)

    def test_the_soundtrack_reaches_the_film_from_either_door(self):
        # Export posted `id` and nothing else, so a film scored in the Editor
        # came out of this door silent.
        track = self.src / "bed.wav"
        track.write_bytes(b"RIFF")
        self.save_edit(audio={"path": str(track), "offset": 0.0,
                              "mode": "under"})
        _res, seen = self.export()
        self.assertEqual(seen["music"], str(track))
        self.assertEqual(seen["music_mode"], "under")

    def test_an_explicit_music_mode_beats_the_edits_own(self):
        track = self.src / "bed.wav"
        track.write_bytes(b"RIFF")
        self.save_edit(audio={"path": str(track), "offset": 0.0,
                              "mode": "under"})
        _res, seen = self.export(music_mode="replace")
        self.assertEqual(seen["music_mode"], "replace")

    def test_the_manifest_says_the_film_came_from_the_timeline(self):
        self.save_edit()
        res, _ = self.export()
        md = (Path(res["dir"]) / "storyboard.md").read_text(encoding="utf-8")
        self.assertIn("Assembled from the timeline in the Editor", md)
        # The folder and the shot list are unchanged — only who assembled the
        # film changed.
        self.assertEqual(len(res["files"]), 2)
        self.assertIn("| # | file | length | seed | pass | prompt |", md)

    def test_an_empty_timeline_does_not_hijack_the_export(self):
        import storyboard_editor as sedit
        sedit.save_edit(self.bdir, {"version": sedit.EDIT_VERSION,
                                    "board_id": "sb_one_assembler",
                                    "revision": 0, "source": "auto",
                                    "audio": None, "beats": None,
                                    "clips": [], "settings": {}})
        _res, seen = self.export()
        self.assertEqual([p.name for p in seen["clips"]],
                         ["S01_shot-1-happens.mp4", "S02_shot-2-happens.mp4"])


class StopCancelsTheRunningShot(unittest.TestCase):
    def _cancel(self, current, ids):
        with mock.patch.dict(panel.STATE, {"current": current}, clear=False), \
             mock.patch.object(panel, "stop_current_job") as killer:
            got = panel._sb_cancel_running_shot(ids)
        return got, killer

    def test_the_running_shot_of_THIS_film_is_cancelled(self):
        got, killer = self._cancel({"id": "j-abc-001"}, {"j-abc-001", "j-abc-002"})
        self.assertEqual(got, "j-abc-001")
        killer.assert_called_once_with()

    def test_it_reuses_the_panels_cancel_path_instead_of_inventing_one(self):
        # stop_current_job() is what POST /stop calls; it is what sets
        # cancel_requested, and therefore what makes the job land as
        # "cancelled" (a state _sb_reconcile already folds onto the shot)
        # rather than "failed". A second, storyboard-only kill would skip that
        # flag and every process group stop_current_job knows about, so the
        # function must contain no killing of its own.
        import inspect  # noqa: PLC0415
        src = inspect.getsource(panel._sb_cancel_running_shot)
        self.assertIn("stop_current_job()", src)
        for invented in ("killpg", "HELPER.kill", "SIGTERM", "SIGKILL",
                         "terminate("):
            self.assertNotIn(invented, src,
                             "Stop must not grow a second cancellation path")

    def test_another_features_job_is_never_collateral(self):
        got, killer = self._cancel({"id": "j-image-999"}, {"j-abc-001"})
        self.assertIsNone(got)
        killer.assert_not_called()

    def test_nothing_running_means_nothing_killed(self):
        for current in (None, {}, {"id": None}):
            got, killer = self._cancel(current, {"j-abc-001"})
            self.assertIsNone(got)
            killer.assert_not_called()

    def test_a_film_with_no_job_ids_kills_nothing(self):
        for ids in (set(), None, []):
            got, killer = self._cancel({"id": "j-abc-001"}, ids)
            self.assertIsNone(got)
            killer.assert_not_called()

    def test_a_cancelled_job_lands_in_a_state_the_reconciler_understands(self):
        # The worker writes status="cancelled" when cancel_requested is set
        # (see the run_job except branch). _sb_reconcile must already fold
        # that onto the shot — this is the contract Stop now depends on.
        shot = {"n": 1, "draft_job_id": "j-1", "status": "rendering"}
        board = {"shots": [shot]}
        with mock.patch.object(panel, "_sb_job_index",
                               return_value={"j-1": {"status": "cancelled",
                                                     "output_path": None,
                                                     "error": "Job cancelled."}}):
            changed = panel._sb_reconcile(board)
        self.assertTrue(changed)
        self.assertEqual(shot["status"], "failed")
        self.assertEqual(shot["error"], "Job cancelled.")


# =============================================================================
# End-to-end with a REAL ffmpeg — two lavfi patterns, no GPU, no weights
# =============================================================================
@unittest.skipUnless(Path(panel.FFMPEG).is_file() and Path(panel.FFPROBE).is_file(),
                     "needs a real ffmpeg + ffprobe")
class RealAssembly(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def _make(self, name, w, h, seconds, *, audio_rate=None):
        out = self.dir / name
        cmd = [str(panel.FFMPEG), "-y", "-f", "lavfi",
               "-i", f"testsrc=size={w}x{h}:rate=24:duration={seconds}"]
        if audio_rate:
            cmd += ["-f", "lavfi",
                    "-i", f"sine=frequency=440:sample_rate={audio_rate}:"
                          f"duration={seconds}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio_rate:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd += [str(out)]
        subprocess.run(cmd, capture_output=True, check=True, timeout=120)
        return out

    def test_mixed_geometry_and_a_silent_clip_join_into_one_film(self):
        loud = self._make("loud.mp4", 320, 240, 1, audio_rate=44100)
        silent = self._make("silent.mp4", 640, 360, 2)
        before = {p: _sha(p) for p in (loud, silent)}

        with mock.patch.object(panel, "output_codec_settings",
                               return_value={"preset": "standard",
                                             "pix_fmt": "yuv420p", "crf": "23"}):
            res = panel._sb_assemble_film([loud, silent], self.dir / "film.mp4")

        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual((res["width"], res["height"]), (640, 360))

        film = Path(res["path"])
        probe = json.loads(subprocess.run(
            [str(panel.FFPROBE), "-v", "error", "-show_entries",
             "stream=codec_type,width,height,pix_fmt,nb_frames:format=duration",
             "-of", "json", str(film)],
            capture_output=True, text=True, timeout=60).stdout)
        streams = probe["streams"]
        self.assertEqual(sum(1 for s in streams if s["codec_type"] == "video"), 1)
        self.assertEqual(sum(1 for s in streams if s["codec_type"] == "audio"), 1)
        video = next(s for s in streams if s["codec_type"] == "video")
        self.assertEqual((video["width"], video["height"]), (640, 360))
        self.assertEqual(video["pix_fmt"], "yuv420p")
        # ~3 s = the two clips end to end, not one of them and not both halves.
        self.assertAlmostEqual(float(probe["format"]["duration"]),
                               res["duration"], delta=0.15)
        self.assertAlmostEqual(int(video["nb_frames"]),
                               res["duration"] * panel.FPS, delta=2)

        # +faststart, via the release gate's own box walk.
        import scripts.check_output_codec as gate  # noqa: PLC0415
        self.assertIs(gate.has_faststart(film), True)

        for p, digest in before.items():
            self.assertEqual(_sha(p), digest, f"{p.name} was modified")

    def _probe_json(self, path, entries):
        return json.loads(subprocess.run(
            [str(panel.FFPROBE), "-v", "error", "-show_entries", entries,
             "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60).stdout)

    def test_a_planned_cut_really_is_only_the_windows_that_were_chosen(self):
        a = self._make("a.mp4", 320, 240, 4, audio_rate=48000)
        b = self._make("b.mp4", 320, 240, 4, audio_rate=48000)
        plan = [{"path": str(a), "start": 0.5, "end": 2.0},
                {"path": str(b), "start": 1.0, "end": 2.0}]
        before = {p: _sha(p) for p in (a, b)}

        with mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "23"}):
            res = panel._sb_assemble_film([a, b], self.dir / "cut.mp4",
                                          plan=plan)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertAlmostEqual(res["duration"], 2.5, places=3)
        probe = self._probe_json(res["path"],
                                 "stream=codec_type,nb_frames:format=duration")
        # 2.5 s, not the 8 s the two source clips hold.
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.5,
                               delta=0.12)
        video = next(s for s in probe["streams"] if s["codec_type"] == "video")
        self.assertAlmostEqual(int(video["nb_frames"]), 2.5 * panel.FPS,
                               delta=2)
        for p, digest in before.items():
            self.assertEqual(_sha(p), digest, f"{p.name} was modified")

    def test_the_trim_takes_the_window_it_was_asked_for_frame_for_frame(self):
        # `testsrc` writes its own frame counter into the picture, so "did the
        # trim start where the plan said" is answerable by comparing pixels
        # rather than by trusting the container's duration.
        src = self._make("counter.mp4", 320, 240, 5)
        plan = [{"path": str(src), "start": 2.0, "end": 4.0}]
        with mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "23"}):
            res = panel._sb_assemble_film([src], self.dir / "trim.mp4",
                                          plan=plan)
        self.assertTrue(res["ok"], res.get("error"))

        def grab(path, t):
            out = subprocess.run(
                [str(panel.FFMPEG), "-v", "error", "-ss", f"{t:.3f}",
                 "-i", str(path), "-frames:v", "1", "-vf",
                 "scale=64:48,format=gray", "-f", "rawvideo",
                 "-pix_fmt", "gray", "-"],
                capture_output=True, timeout=60).stdout
            return bytearray(out[:64 * 48])

        def diff(x, y):
            return sum(abs(p - q) for p, q in zip(x, y)) / max(1, len(x))

        # Where in the SOURCE does the film's half-second mark come from? Scan
        # the whole source and take the best match — an absolute threshold
        # would only be measuring how fast `testsrc` moves.
        film_at_0_5 = grab(res["path"], 0.5)
        scan = [(diff(film_at_0_5, grab(src, k / panel.FPS)), k / panel.FPS)
                for k in range(0, 5 * panel.FPS)]
        best = min(scan)[1]
        self.assertAlmostEqual(best, 2.5, delta=2.5 / panel.FPS,
                               msg=f"the trim landed at {best:.3f}s, not 2.5s")
        # ...and 0.5 s into the SOURCE is emphatically not the answer.
        self.assertLess(min(scan)[0], diff(film_at_0_5, grab(src, 0.5)))

    def test_a_soundtrack_replaces_the_clip_audio_end_to_end(self):
        clip = self._make("loud.mp4", 320, 240, 4, audio_rate=48000)
        song = self.dir / "song.wav"
        subprocess.run(
            [str(panel.FFMPEG), "-y", "-f", "lavfi",
             "-i", "sine=frequency=110:sample_rate=44100:duration=10",
             str(song)], capture_output=True, check=True, timeout=120)
        plan = [{"path": str(clip), "start": 0.0, "end": 2.0}]
        with mock.patch.object(panel, "output_codec_settings",
                               return_value={"pix_fmt": "yuv420p", "crf": "23"}):
            res = panel._sb_assemble_film([clip], self.dir / "scored.mp4",
                                          plan=plan, music=song)
        self.assertTrue(res["ok"], res.get("error"))
        self.assertEqual(res["sample_rate"], 44100)
        probe = self._probe_json(res["path"],
                                 "stream=codec_type,sample_rate:format=duration")
        audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
        self.assertEqual(audio["sample_rate"], "44100")
        # The film is 2 s long even though the song is 10 s: the soundtrack is
        # trimmed to the CUT, never the other way round.
        self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0,
                               delta=0.12)


# =============================================================================
# The Export button itself — the real sbExport(), run in node
# =============================================================================
SB_EXPORT_SHIM = r"""
const CALLS = { toasts: [], fetches: [], states: [], landed: [] };
function node() {
  return { textContent: 'Export', disabled: false, dataset: {}, onclick: null };
}
const BTN = node();
function sbEl(id) { return BTN; }
const SB = { id: 'sb_1' };
function phosToast(msg, opts) { CALLS.toasts.push({ msg: String(msg), opts: opts || {} }); }
// Where a successful export now ENDS: on the film it made. Stubbed, because
// what is under test here is that the export goes there at all — the screen
// itself is locked by test_storyboard_film.py.
function sbFilmOpen(opts) { CALLS.landed.push(opts || {}); }
class URLSearchParams { constructor() { this.v = {}; } set(k, x) { this.v[k] = x; } }
let RESPONSE = null;
async function fetch(url) {
  CALLS.fetches.push(url);
  // Snapshot the button WHILE the request is in flight — this is the state a
  // second, impatient click would meet.
  CALLS.states.push({ busy: BTN.dataset.busy, disabled: BTN.disabled,
                      label: BTN.textContent });
  return { json: async () => RESPONSE };
}
__SB_EXPORT__
async function main() {
  RESPONSE = JSON.parse(process.argv[2]);
  await sbExport();
  console.log(JSON.stringify({ toasts: CALLS.toasts, fetches: CALLS.fetches,
                               states: CALLS.states, landed: CALLS.landed,
                               after: { busy: BTN.dataset.busy,
                                        disabled: BTN.disabled,
                                        label: BTN.textContent } }));
}
main();
"""


@unittest.skipUnless(NODE, "needs node")
class ExportButton(unittest.TestCase):
    """The real client function, executed — not grepped."""

    @classmethod
    def setUpClass(cls):
        cls.script = SB_EXPORT_SHIM.replace("__SB_EXPORT__",
                                            extract_function("sbExport"))

    def run_export(self, response: dict) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            js = Path(tmp) / "sbexport.mjs"
            js.write_text(self.script, encoding="utf-8")
            proc = subprocess.run([NODE, str(js), json.dumps(response)],
                                  capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    OK = {"ok": True, "dir": "/out/sb", "files": ["S01.mp4", "S02.mp4"],
          "film": "/out/sb/x_film.mp4", "film_name": "x_film.mp4",
          "film_error": None, "film_duration": 22.375}
    NO_FILM = {"ok": True, "dir": "/out/sb", "files": ["S01.mp4"],
               "film": None, "film_name": None,
               "film_error": "ffmpeg is not installed", "film_duration": None}

    def test_a_finished_film_is_named_in_the_success_toast(self):
        got = self.run_export(self.OK)
        self.assertEqual(len(got["toasts"]), 1)
        toast = got["toasts"][0]
        self.assertEqual(toast["opts"]["kind"], "success")
        self.assertIn("x_film.mp4", toast["msg"])
        self.assertIn("22 s", toast["msg"])

    def test_a_failed_assembly_is_a_note_on_a_successful_export(self):
        # NOT a danger toast and NOT a silent success: the folder really did
        # land, and the reason the film didn't has to reach the user.
        got = self.run_export(self.NO_FILM)
        toast = got["toasts"][0]
        self.assertNotEqual(toast["opts"].get("kind"), "danger")
        self.assertIn("could not be assembled", toast["msg"])
        self.assertIn("ffmpeg is not installed", toast["msg"])
        self.assertIn("Exported 1 clips", toast["msg"])

    def test_the_button_is_held_shut_while_the_encode_runs(self):
        # Export was instant before it also encoded a film; a second click
        # would start a second ffmpeg writing the same file.
        got = self.run_export(self.OK)
        inflight = got["states"][0]
        self.assertEqual(inflight["busy"], "1")
        self.assertTrue(inflight["disabled"])
        self.assertEqual(inflight["label"], "Assembling…")

    def test_the_button_is_released_even_when_the_request_fails(self):
        got = self.run_export({"ok": False, "error": "boom"})
        self.assertFalse(got["after"]["disabled"])
        self.assertNotEqual(got["after"]["busy"], "1")
        self.assertEqual(got["toasts"][0]["opts"]["kind"], "danger")

    def test_a_successful_export_ENDS_ON_THE_FILM_IT_MADE(self):
        # A toast that fades is what made the finished film invisible: the
        # panel wrote a film, said so for eight seconds, and left the user on a
        # list of individual shots wondering which one was the movie.
        got = self.run_export(self.OK)
        self.assertEqual(got["landed"], [{"focus": "x_film.mp4"}])

    def test_an_export_with_no_film_does_not_send_you_to_a_film_screen(self):
        got = self.run_export(self.NO_FILM)
        self.assertEqual(got["landed"], [])


if __name__ == "__main__":
    unittest.main()


class ImportShotsBetweenFilms(unittest.TestCase):
    """A board is a timeline one-to-one, and coverage is not.

    Owner, holding a film in one board and its B-roll in another: "can you make
    a project with all the clips? … If you create two different project
    timelines, how can you storyboard? How can you move from one to the other?"
    Before this there was no answer but rendering them again.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clips = []
        for i in range(3):
            p = self.root / f"c{i}.mp4"
            p.write_bytes(b"x")
            self.clips.append(str(p))

    def _board(self, bid, shots, **kw):
        b = {"id": bid, "title": bid, "shots": shots}
        b.update(kw)
        return b

    def _shot(self, n, path=None, **kw):
        s = {"n": n, "title": f"shot {n}", "mode": "text", "status": "done"}
        if path:
            s["final_output"] = path
        s.update(kw)
        return s

    def test_rendered_shots_come_across_and_are_renumbered(self):
        dst = self._board("a", [self._shot(1, self.clips[0])])
        src = self._board("b", [self._shot(1, self.clips[1]),
                                self._shot(2, self.clips[2])])
        got, skipped = panel._sb_import_shots(dst, src)
        self.assertEqual([g["n"] for g in got], [2, 3])
        self.assertEqual(len(dst["shots"]), 3)
        self.assertEqual(skipped, [])

    def test_the_clip_is_referenced_not_copied(self):
        # An import must cost a JSON edit and no render time.
        dst = self._board("a", [])
        src = self._board("b", [self._shot(1, self.clips[0])])
        panel._sb_import_shots(dst, src)
        self.assertEqual(dst["shots"][0]["final_output"], self.clips[0])

    def test_provenance_stays_on_the_shot(self):
        # Otherwise an imported clip is indistinguishable from one this film
        # planned, and a re-plan silently rewrites somebody else's shot.
        dst = self._board("a", [])
        src = self._board("b", [self._shot(1, self.clips[0])], title="B-roll")
        panel._sb_import_shots(dst, src)
        self.assertEqual(dst["shots"][0]["imported_from"],
                         {"board": "b", "n": 1, "title": "B-roll"})

    def test_a_shot_that_never_rendered_is_skipped_not_imported(self):
        dst = self._board("a", [])
        src = self._board("b", [self._shot(1), self._shot(2, self.clips[0])])
        got, skipped = panel._sb_import_shots(dst, src)
        self.assertEqual(len(got), 1)
        self.assertEqual(skipped, [{"n": 1, "why": "not rendered yet"}])

    def test_importing_twice_does_not_duplicate(self):
        dst = self._board("a", [])
        src = self._board("b", [self._shot(1, self.clips[0])])
        panel._sb_import_shots(dst, src)
        got, skipped = panel._sb_import_shots(dst, src)
        self.assertEqual(got, [])
        self.assertEqual(skipped, [{"n": 1, "why": "already here"}])
        self.assertEqual(len(dst["shots"]), 1)

    def test_only_selects_a_subset(self):
        dst = self._board("a", [])
        src = self._board("b", [self._shot(1, self.clips[0]),
                                self._shot(2, self.clips[1]),
                                self._shot(3, self.clips[2])])
        got, _ = panel._sb_import_shots(dst, src, only={2})
        self.assertEqual(len(got), 1)
        self.assertEqual(dst["shots"][0]["final_output"], self.clips[1])

    def test_the_location_comes_with_the_shot(self):
        # An imported shot pointing at a location this board has never heard of
        # is `unknown_location`, which correctly refuses to render — turning a
        # successful import into an unrenderable film.
        loc = {"id": "carwash", "name": "The car wash", "description": "a driveway"}
        dst = self._board("a", [], locations=[])
        src = self._board("b", [self._shot(1, self.clips[0], location_id="carwash")],
                          locations=[loc])
        panel._sb_import_shots(dst, src)
        self.assertIn("carwash", [l["id"] for l in dst["locations"]])

    def test_a_location_already_present_is_not_duplicated(self):
        loc = {"id": "carwash", "name": "The car wash", "description": "a driveway"}
        dst = self._board("a", [], locations=[dict(loc)])
        src = self._board("b", [self._shot(1, self.clips[0])], locations=[loc])
        panel._sb_import_shots(dst, src)
        self.assertEqual(len(dst["locations"]), 1)

    def test_the_cast_comes_too_so_wardrobe_survives(self):
        cast = [{"id": "ariatrn", "trigger": "ariatrn", "wardrobe": "a red bikini"}]
        dst = self._board("a", [], cast=[])
        src = self._board("b", [self._shot(1, self.clips[0])], cast=cast)
        panel._sb_import_shots(dst, src)
        self.assertEqual(dst["cast"][0]["wardrobe"], "a red bikini")


# =============================================================================
# WAVE 2 — stills, slugs, brightness, and the input-index refactor
# =============================================================================
class SegmentIndexIsNotInputIndex(unittest.TestCase):
    """The refactor the two new kinds cost, pinned in both directions.

    Until a clip could be black, segment index WAS ffmpeg input index: one
    `-i` per probe, in probe order, and the soundtrack addressed as
    `len(probes)`. A slug consumes NO input (`color=` is a source filter); a
    still consumes one, but through `-loop 1 -framerate F -t D` and never
    through ffprobe. So every input after the first slug shifts, and a film
    whose sound is one shot late is what a wrong answer here looks like.
    """

    TIMELINE = [
        {"path": "/x/a.mp4", "start": 0.0, "end": 2.0, "film_start": 0.0},
        {"path": None, "kind": "slug", "start": 0.0, "end": 1.5,
         "film_start": 2.0},
        {"path": "/x/card.png", "kind": "still", "start": 0.0, "end": 3.0,
         "film_start": 3.5},
        {"path": "/x/b.mp4", "start": 1.0, "end": 4.0, "film_start": 6.5},
    ]

    def segments(self, timeline=None):
        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=lambda p: _probe(1024, 576, 10.0)), \
             mock.patch.object(panel, "_sb_probe_still",
                               side_effect=lambda p: {"w": 1920, "h": 1080,
                                                      "duration": 0.0,
                                                      "has_audio": False,
                                                      "sample_rate": 0}):
            return panel._sb_timeline_segments(timeline or self.TIMELINE)

    def test_only_the_segments_with_a_file_get_an_input_index(self):
        segs, unreadable, inputs = self.segments()
        self.assertEqual([s["kind"] for s in segs],
                         ["video", "slug", "still", "video"])
        # THE ASSERTION THIS CLASS EXISTS FOR: the slug is None, and the two
        # segments after it are 1 and 2, not 2 and 3.
        self.assertEqual([s["input"] for s in segs], [0, None, 1, 2])
        self.assertEqual(len(inputs), 3)
        self.assertEqual(unreadable, [])

    def test_a_still_is_looped_to_its_slot_and_a_slug_is_not_an_input(self):
        _segs, _unreadable, inputs = self.segments()
        self.assertEqual(inputs[0], ["-i", "/x/a.mp4"])
        # `-loop 1` WITHOUT `-t` runs until the disk is full.
        self.assertEqual(inputs[1], ["-loop", "1", "-framerate", str(panel.FPS),
                                     "-t", "3.000000", "-i", "/x/card.png"])
        self.assertEqual(inputs[2], ["-i", "/x/b.mp4"])
        self.assertNotIn(["-i", None], inputs)

    def test_the_graph_addresses_the_inputs_the_argv_will_actually_pass(self):
        segs, _u, inputs = self.segments()
        g, _label = panel._sb_film_filtergraph(
            [], 1920, 1080, 48000, "yuv420p", segments=segs)
        self.assertIn("[0:v]trim=start=0.000000:end=2.000000", g)
        self.assertIn("color=c=black:s=1920x1080", g)
        self.assertIn("[1:v]trim=0:3.000000", g)          # the still, not [2:v]
        self.assertIn("[2:v]trim=start=1.000000:end=4.000000", g)
        # Four segments, three inputs. Both numbers appear, and they differ.
        self.assertIn("concat=n=4:v=1:a=1[vcat][aout]", g)
        self.assertEqual(len(inputs), 3)

    def test_the_soundtrack_is_the_input_after_the_last_one_that_exists(self):
        segs, _u, inputs = self.segments()
        for mode in ("replace", "under"):
            g, _l = panel._sb_film_filtergraph(
                [], 1920, 1080, 48000, "yuv420p", segments=segs,
                music={"path": "/x/song.mp3", "start": 0.0, "mode": mode})
            # THREE inputs, so the music is [3:a] — not [4:a], which is what
            # counting segments instead of inputs would have produced.
            self.assertIn("[3:a]atrim=start=0.000000", g, mode)
            self.assertNotIn("[4:a]atrim", g, mode)
            self.assertEqual(len(inputs), 3)

    def test_an_unreadable_still_is_dropped_and_the_indices_close_up(self):
        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=lambda p: _probe(1024, 576, 10.0)), \
             mock.patch.object(panel, "_sb_probe_still", return_value=None):
            segs, unreadable, inputs = panel._sb_timeline_segments(self.TIMELINE)
        self.assertEqual(unreadable, ["card.png"])
        self.assertEqual([s["kind"] for s in segs], ["video", "slug", "video"])
        self.assertEqual([s["input"] for s in segs], [0, None, 1])
        self.assertEqual(len(inputs), 2)

    def test_a_slug_needs_no_probe_at_all(self):
        # No file, no ffprobe, no proxy — the cheapest of the three kinds is
        # also the one that forced the refactor.
        with mock.patch.object(panel, "_sb_probe_clip") as pc, \
             mock.patch.object(panel, "_sb_probe_still") as ps:
            segs, _u, inputs = panel._sb_timeline_segments(
                [{"path": None, "kind": "slug", "start": 0.0, "end": 2.0,
                  "film_start": 0.0}])
        pc.assert_not_called()
        ps.assert_not_called()
        self.assertEqual(inputs, [])
        self.assertEqual(segs[0]["input"], None)
        self.assertEqual(segs[0]["duration"], 2.0)


class StillsAndSlugsInTheGraph(unittest.TestCase):
    def graph(self, segments, **kw):
        return panel._sb_film_filtergraph([], 1024, 576, 48000, "yuv420p",
                                          segments=segments, **kw)[0]

    def slug(self, dur=2.0, **kw):
        return dict({"kind": "slug", "input": None, "info": None,
                     "window": None, "adjust": None, "duration": dur}, **kw)

    def still(self, dur=3.0, inp=0, **kw):
        return dict({"kind": "still", "input": inp,
                     "info": {"w": 1920, "h": 1080, "duration": 0.0,
                              "has_audio": False, "sample_rate": 0},
                     "window": None, "adjust": None, "duration": dur}, **kw)

    def test_a_slug_is_a_colour_source_at_the_films_own_geometry(self):
        g = self.graph([self.slug(2.0)])
        self.assertIn("color=c=black:s=1024x576:r=24:d=2.000000,"
                      "setsar=1,format=yuv420p[v0]", g)
        # No scale, no pad: `color` already emits the target size, so both
        # would be filters that do nothing to every frame of the slug.
        self.assertNotIn("scale=1024:576", g)

    def test_a_slug_is_silent_by_construction_not_by_accident(self):
        # An uneven audio-stream count and ffmpeg refuses to build the graph.
        g = self.graph([self.slug(2.0)])
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000:"
                      "d=2.000000[a0]", g)
        self.assertIn("concat=n=1:v=1:a=1", g)

    def test_a_still_gets_the_same_normalisation_every_other_segment_gets(self):
        g = self.graph([self.still(3.0)])
        self.assertIn("[0:v]trim=0:3.000000,setpts=PTS-STARTPTS,fps=24,"
                      "scale=1024:576:force_original_aspect_ratio=decrease,"
                      "pad=1024:576:(ow-iw)/2:(oh-ih)/2,setsar=1,"
                      "format=yuv420p[v0]", g)
        self.assertIn("anullsrc=channel_layout=stereo:sample_rate=48000:"
                      "d=3.000000[a0]", g)

    def test_a_film_of_nothing_but_black_still_assembles(self):
        g = self.graph([self.slug(1.0), self.slug(2.0)])
        self.assertIn("concat=n=2:v=1:a=1[vcat][aout]", g)
        self.assertEqual(g.count("color=c=black"), 2)

    def test_the_under_mix_path_is_untouched_by_the_new_kinds(self):
        # There are tests pinning this mix and there is a measured reason for
        # every term in it. A slug in the timeline must not change one.
        segs = [self.still(3.0), self.slug(1.0)]
        g = self.graph(segs, music={"path": "/x/s.mp3", "start": 0.0,
                                    "mode": "under",
                                    "gain": [[0.0, 0.2], [4.0, 0.2]]})
        self.assertIn("[bed]", g)
        self.assertIn("volume=volume='", g)
        self.assertIn("[acat][bed]amix=inputs=2:duration=first:"
                      "dropout_transition=0:"
                      f"normalize=0,asoftclip=type=tanh:"
                      f"threshold={panel._sb_mix_ceiling():g}[aout]", g)
        # `under` keeps the segments' own audio, so the silence they carry is
        # what the bed ducks against — the bed must not become the only sound.
        self.assertIn("[v0][a0][v1][a1]concat=n=2:v=1:a=1[vcat][acat]", g)

    def test_music_replace_still_drops_every_segments_audio(self):
        g = self.graph([self.still(3.0), self.slug(1.0)],
                       music={"path": "/x/s.mp3", "start": 0.0,
                              "mode": "replace"})
        self.assertIn("concat=n=2:v=1:a=0[vcat]", g)
        self.assertNotIn("anullsrc", g)
        self.assertIn("[1:a]atrim=start=0.000000", g)   # one input: the still


class BrightnessIsOneTermOrNoTerm(unittest.TestCase):
    PROBES = [(Path("/x/S01.mp4"), _probe(1024, 576, 5.0)),
              (Path("/x/S02.mp4"), _probe(1024, 576, 5.0))]

    def test_a_neutral_clip_adds_no_filter_at_all(self):
        # NOT `eq=brightness=0.000000`. That would change the graph of every
        # film ever exported in order to express nothing, and would break the
        # byte-for-byte default this suite pins two classes up.
        for adjust in (None, {}, {"brightness": 0}, {"brightness": 0.0},
                       {"brightness": "nonsense"}, {"brightness": None},
                       "not a dict"):
            self.assertEqual(panel._sb_brightness_term(adjust), "",
                             repr(adjust))

    def test_a_graded_clip_gets_one_eq_term_before_the_format(self):
        self.assertEqual(panel._sb_brightness_term({"brightness": 0.25}),
                         "eq=brightness=0.250000,")
        self.assertEqual(panel._sb_brightness_term({"brightness": -0.125}),
                         "eq=brightness=-0.125000,")

    def test_the_term_is_clamped_to_what_the_validator_allows(self):
        self.assertEqual(panel._sb_brightness_term({"brightness": 9}),
                         "eq=brightness=0.500000,")
        self.assertEqual(panel._sb_brightness_term({"brightness": -9}),
                         "eq=brightness=-0.500000,")

    def test_the_grade_lands_in_the_segment_it_belongs_to(self):
        cuts = panel._sb_cut_index([
            {"path": "/x/S01.mp4", "start": 0.0, "end": 2.0,
             "adjust": {"brightness": 0.2}},
            {"path": "/x/S02.mp4", "start": 0.0, "end": 2.0},
        ])
        g, _l = panel._sb_film_filtergraph(self.PROBES, 1024, 576, 48000,
                                           "yuv420p", cuts=cuts)
        self.assertIn("setsar=1,eq=brightness=0.200000,format=yuv420p[v0]", g)
        self.assertIn("setsar=1,format=yuv420p[v1]", g)
        self.assertEqual(g.count("eq=brightness"), 1)

    def test_one_source_used_twice_can_be_graded_two_different_ways(self):
        # The cut index has been list-per-path since repeats were possible;
        # what it never carried was anything to differ ABOUT.
        cuts = panel._sb_cut_index([
            {"path": "/x/S01.mp4", "start": 0.0, "end": 2.0,
             "adjust": {"brightness": -0.3}},
            {"path": "/x/S01.mp4", "start": 3.0, "end": 4.0,
             "adjust": {"brightness": 0.4}},
        ])
        probes = [(Path("/x/S01.mp4"), _probe(1024, 576, 5.0))] * 2
        g, _l = panel._sb_film_filtergraph(probes, 1024, 576, 48000,
                                           "yuv420p", cuts=cuts)
        self.assertIn("eq=brightness=-0.300000,format=yuv420p[v0]", g)
        self.assertIn("eq=brightness=0.400000,format=yuv420p[v1]", g)

    def test_the_cut_index_is_unchanged_for_a_plan_that_says_nothing_new(self):
        # A plan of plain trims must build the identical index it always did,
        # or every existing export changes for no reason.
        idx = panel._sb_cut_index([{"path": "/x/S01.mp4", "start": 1.0,
                                    "end": 2.0}])
        self.assertEqual(idx, {"/x/S01.mp4": [{"start": 1.0, "end": 2.0}]})

    def test_a_graded_still_and_a_graded_slug_carry_the_term_too(self):
        segs = [
            {"kind": "still", "input": 0,
             "info": {"w": 1024, "h": 576, "duration": 0.0,
                      "has_audio": False, "sample_rate": 0},
             "window": None, "adjust": {"brightness": 0.1}, "duration": 2.0},
            {"kind": "slug", "input": None, "info": None, "window": None,
             "adjust": {"brightness": 0.5}, "duration": 1.0},
        ]
        g, _l = panel._sb_film_filtergraph([], 1024, 576, 48000, "yuv420p",
                                           segments=segs)
        self.assertIn("setsar=1,eq=brightness=0.100000,format=yuv420p[v0]", g)
        self.assertIn("setsar=1,eq=brightness=0.500000,format=yuv420p[v1]", g)
