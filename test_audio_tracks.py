#!/usr/bin/env python3
"""Audio tracks — A3, A4, … under the music. The server half.

The owner needed a laugh track, a slap-bass sting and a looping bed on one
film and had nowhere to put them: A1 is each shot's own sound and A2 holds ONE
soundtrack. `edit["audio_tracks"]` is a list of tracks, each a list of strips.
Locked here, against the real model, the real filtergraph builder and — when
ffmpeg is on the machine — a real render measured with volumedetect:

  * absent is none: an edit with no tracks (or an empty list) normalises and
    builds the graph byte-identically to one written before tracks existed;
  * the shape is validated, and two strips on ONE track may not overlap while
    two strips on two tracks may;
  * normalise rounds, sorts, invents ids and drops neutral fields, deciding
    nothing about where a strip is;
  * the strip curve is the envelope times the strip fader times the track
    fader, and muted strips / muted tracks / silent curves never reach the mix;
  * the render graph trims, shapes, delays and MIXES each strip under the same
    soft limiter, addressing the inputs the argv actually passes;
  * the NLE export writes each track as its own XML audio track and its strips
    as AE layers, with in/out, level keyframes and mutes as editable settings.

Run:  python3 -m unittest test_audio_tracks
"""
from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
_SCRATCH = Path(tempfile.mkdtemp(prefix="phos-tracks-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "up")):
    os.environ.setdefault(_k, str(_SCRATCH / _d))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as se                                       # noqa: E402


def _clip(cid="c1", path="/x/a.mp4", start=0.0, end=6.0, fs=0.0):
    return {"id": cid, "path": path, "start": start, "end": end,
            "film_start": fs, "film_end": fs + (end - start),
            "source": "auto", "locked": False}


def _base():
    return {"version": se.EDIT_VERSION, "board_id": "b", "clips": [_clip()],
            "audio": None}


def _tracks():
    return [
        {"id": "t1", "name": "Laughs", "strips": [
            {"id": "s1", "path": "/x/laugh.wav", "start": 0.0, "end": 2.0,
             "film_start": 1.0, "afx": {"fade_in": 0.5}},
            {"id": "s2", "path": "/x/laugh.wav", "start": 0.0, "end": 1.0,
             "film_start": 3.0, "muted": True},
        ]},
        {"id": "t2", "gain": 0.5, "strips": [
            {"id": "s3", "path": "/x/sting.wav", "start": 0.25, "end": 1.25,
             "film_start": 2.5, "gain": 0.5},
        ]},
    ]


class AbsentIsNone(unittest.TestCase):
    def test_no_key_and_an_empty_list_normalise_identically(self):
        a = se.normalise_edit(_base())
        b = se.normalise_edit(dict(_base(), audio_tracks=[]))
        self.assertNotIn("audio_tracks", a)
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_an_old_document_validates_and_digests_the_same(self):
        self.assertEqual(se.validate_edit(_base()), [])
        self.assertEqual(se.edit_digest(se.normalise_edit(_base())),
                         se.edit_digest(se.normalise_edit(dict(_base(), audio_tracks=[]))))

    def test_the_graph_is_byte_identical_without_tracks(self):
        seg = {"kind": "video", "input": 0, "path": "/x/a.mp4", "duration": 6.0,
               "info": {"w": 320, "h": 240, "duration": 6.0, "has_audio": True,
                        "sample_rate": 48000}, "window": {"start": 0.0, "end": 6.0}}
        for music in (None,
                      {"path": "/x/m.wav", "start": 0.0, "end": None, "delay": 0.0,
                       "mode": "under", "gain": []},
                      {"path": "/x/m.wav", "start": 0.0, "end": None, "delay": 0.0,
                       "mode": "replace", "gain": []}):
            with self.subTest(music=bool(music) and music["mode"]):
                g0, l0 = panel._sb_film_filtergraph([], 320, 240, 48000, "yuv420p",
                                                    segments=[dict(seg)], music=music)
                g1, l1 = panel._sb_film_filtergraph([], 320, 240, 48000, "yuv420p",
                                                    segments=[dict(seg)], music=music,
                                                    sound_strips=[], sound_base=7)
                self.assertEqual((g0, l0), (g1, l1))

    def test_the_render_of_an_edit_with_no_tracks_passes_no_strips(self):
        self.assertEqual(se.track_render_strips(_base()), [])


class Validation(unittest.TestCase):
    def codes(self, doc):
        return [e["code"] for e in se.blocking_errors(se.validate_edit(doc))]

    def test_a_good_document_is_clean(self):
        self.assertEqual(self.codes(dict(_base(), audio_tracks=_tracks())), [])

    def test_same_track_overlap_is_refused(self):
        tr = _tracks()
        tr[0]["strips"][1]["film_start"] = 2.5          # s1 runs 1.0-3.0
        self.assertIn("audio_track_strips_overlap",
                      self.codes(dict(_base(), audio_tracks=tr)))

    def test_a_butt_join_is_not_an_overlap(self):
        tr = _tracks()
        tr[0]["strips"][1]["film_start"] = 3.0 - 1e-4
        self.assertEqual(self.codes(dict(_base(), audio_tracks=tr)), [])

    def test_different_tracks_may_overlap(self):
        tr = _tracks()
        tr[1]["strips"][0]["film_start"] = 1.0          # right on top of s1
        self.assertEqual(self.codes(dict(_base(), audio_tracks=tr)), [])

    def test_shape_errors_are_named(self):
        cases = {
            "audio_tracks_shape": "nope",
            "audio_track_shape": ["x"],
            "audio_track_gain_range": [{"gain": 2, "strips": []}],
            "audio_track_strips": [{"strips": {}}],
            "track_strip_path": [{"strips": [{"start": 0, "end": 1, "film_start": 0}]}],
            "track_strip_window": [{"strips": [{"path": "/a.wav", "start": 1, "end": 1,
                                                "film_start": 0}]}],
            "track_strip_film_start": [{"strips": [{"path": "/a.wav", "start": 0, "end": 1,
                                                    "film_start": -1}]}],
            "track_strip_past_the_end": [{"strips": [{"path": "/a.wav", "start": 0, "end": 5,
                                                      "film_start": 0, "duration": 2}]}],
            "track_strip_gain_range": [{"strips": [{"path": "/a.wav", "start": 0, "end": 1,
                                                    "film_start": 0, "gain": -1}]}],
            "track_strip_muted": [{"strips": [{"path": "/a.wav", "start": 0, "end": 1,
                                               "film_start": 0, "muted": "yes"}]}],
            "track_strip_afx_point": [{"strips": [{"path": "/a.wav", "start": 0, "end": 1,
                                                   "film_start": 0, "afx": {"points": [[1]]}}]}],
        }
        for code, rows in cases.items():
            with self.subTest(code=code):
                self.assertIn(code, self.codes(dict(_base(), audio_tracks=rows)))

    def test_save_refuses_an_overlap_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            se.save_edit(d, dict(_base(), audio_tracks=_tracks()))
            before = (Path(d) / "edit.json").read_text()
            tr = _tracks()
            tr[0]["strips"][1]["film_start"] = 2.0
            with self.assertRaises(se.EditError):
                se.save_edit(d, dict(_base(), audio_tracks=tr))
            self.assertEqual((Path(d) / "edit.json").read_text(), before)

    def test_a_saved_document_round_trips_through_load(self):
        with tempfile.TemporaryDirectory() as d:
            se.save_edit(d, dict(_base(), audio_tracks=_tracks()))
            back = se.load_edit(d)
            self.assertEqual([s["id"] for t in back["audio_tracks"] for s in t["strips"]],
                             ["s1", "s2", "s3"])
            self.assertEqual(se.validate_edit(back), [])


class Normalise(unittest.TestCase):
    def test_neutral_is_absent_and_ids_are_invented(self):
        doc = dict(_base(), audio_tracks=[
            {"name": "  ", "muted": False, "gain": 1.0, "strips": [
                {"path": "/a.wav", "start": 0, "end": 1.0000004, "film_start": 2,
                 "gain": 1, "muted": False, "locked": False, "afx": {"fade_in": 0},
                 "_drag": 1},
                {"path": "/a.wav", "start": 0, "end": 1, "film_start": 0.5},
            ]}])
        t = se.normalise_edit(doc)["audio_tracks"][0]
        self.assertTrue(t["id"])
        for k in ("name", "muted", "gain"):
            self.assertNotIn(k, t)
        self.assertEqual([s["film_start"] for s in t["strips"]], [0.5, 2.0])
        s = t["strips"][1]
        self.assertEqual(s["end"], 1.0)
        for k in ("gain", "muted", "locked", "afx", "_drag"):
            self.assertNotIn(k, s)
        self.assertNotEqual(t["strips"][0]["id"], s["id"])

    def test_an_empty_track_is_kept(self):
        t = se.normalise_edit(dict(_base(), audio_tracks=[{"id": "t9"}]))["audio_tracks"]
        self.assertEqual(t, [{"id": "t9", "strips": []}])

    def test_duplicate_ids_are_made_unique(self):
        doc = dict(_base(), audio_tracks=[
            {"id": "t", "strips": [{"id": "s", "path": "/a.wav", "start": 0, "end": 1,
                                    "film_start": 0}]},
            {"id": "t", "strips": [{"id": "s", "path": "/a.wav", "start": 0, "end": 1,
                                    "film_start": 0}]}])
        tr = se.normalise_edit(doc)["audio_tracks"]
        self.assertEqual(len({t["id"] for t in tr}), 2)
        self.assertEqual(len({t["strips"][0]["id"] for t in tr}), 2)


class TheCurve(unittest.TestCase):
    def test_faders_multiply_the_envelope(self):
        tr = _tracks()
        self.assertEqual(se.track_strip_gain_points(tr[0], tr[0]["strips"][0]),
                         [[0.0, 0.0], [0.5, 1.0], [2.0, 1.0]])
        # 0.5 on the strip, 0.5 on the track, no envelope: a flat quarter.
        self.assertEqual(se.track_strip_gain_points(tr[1], tr[1]["strips"][0]),
                         [[0.0, 0.25], [1.0, 0.25]])
        self.assertEqual(se.track_strip_gain_at(tr[1], tr[1]["strips"][0], 0.5), 0.25)

    def test_unity_is_no_curve(self):
        t = {"strips": [{"path": "/a.wav", "start": 0, "end": 1, "film_start": 0}]}
        self.assertEqual(se.track_strip_gain_points(t, t["strips"][0]), [])

    def test_the_render_list_skips_what_cannot_be_heard(self):
        tr = _tracks()
        rows = se.track_render_strips(dict(_base(), audio_tracks=tr))
        self.assertEqual([r["id"] for r in rows], ["s1", "s3"])       # s2 muted
        self.assertEqual(rows[1]["start"], 0.25)
        self.assertEqual(rows[1]["at"], 2.5)
        tr[1]["muted"] = True
        self.assertEqual([r["id"] for r in se.track_render_strips(dict(_base(), audio_tracks=tr))],
                         ["s1"])
        tr[0]["strips"][0]["gain"] = 0
        self.assertEqual(se.track_render_strips(dict(_base(), audio_tracks=tr)), [])


class TheRenderGraph(unittest.TestCase):
    SEG = {"kind": "video", "input": 0, "path": "/x/a.mp4", "duration": 6.0,
           "info": {"w": 320, "h": 240, "duration": 6.0, "has_audio": True,
                    "sample_rate": 48000}, "window": {"start": 0.0, "end": 6.0}}

    def graph(self, music=None, strips=None, base=3):
        g, _ = panel._sb_film_filtergraph(
            [], 320, 240, 48000, "yuv420p", segments=[dict(self.SEG)], music=music,
            sound_strips=strips, sound_base=base)
        return g

    def rows(self):
        return se.track_render_strips(dict(_base(), audio_tracks=_tracks()))

    def test_each_strip_is_trimmed_shaped_delayed_and_padded(self):
        g = self.graph(strips=self.rows())
        self.assertIn("[3:a]atrim=start=0.000000:end=2.000000,asetpts=PTS-STARTPTS,"
                      "volume=volume='", g)
        self.assertIn("adelay=delays=48000S:all=1,apad,atrim=0:6.000000", g)     # 1.0 s
        self.assertIn("[4:a]atrim=start=0.250000:end=1.250000", g)
        self.assertIn("adelay=delays=120000S:all=1", g)                          # 2.5 s
        self.assertIn("[acat_unused]" if False else "[abase][ts0][ts1]amix=inputs=3:"
                      "duration=first:dropout_transition=0:normalize=0,asoftclip=", g)
        self.assertIn("[vcat][abase]", g)
        self.assertEqual(g.count("[aout]"), 1)

    def test_under_mix_takes_the_strips_into_one_sum(self):
        music = {"path": "/x/m.wav", "start": 0.0, "end": None, "delay": 0.0,
                 "mode": "under", "gain": []}
        g = self.graph(music=music, strips=self.rows())
        self.assertIn("[acat][bed][ts0][ts1]amix=inputs=4:", g)
        self.assertEqual(g.count("asoftclip"), 1)
        self.assertNotIn("[abase]", g)

    def test_a_strip_that_starts_after_the_film_contributes_nothing(self):
        rows = [{"path": "/x/l.wav", "start": 0.0, "end": 1.0, "at": 9.0, "len": 1.0,
                 "gain": []}]
        g = self.graph(strips=rows)
        self.assertNotIn("[ts0]", g)
        self.assertEqual(g, self.graph())

    def test_the_argv_passes_the_inputs_the_graph_addresses(self):
        with tempfile.TemporaryDirectory() as d:
            clip, snd = Path(d) / "a.mp4", Path(d) / "s.wav"
            clip.write_bytes(b"x")
            snd.write_bytes(b"x")
            cap = {}
            info = {"w": 320, "h": 240, "duration": 6.0, "has_audio": True,
                    "sample_rate": 48000}
            real_probe, real_run = panel._sb_probe_clip, panel.run_ffmpeg_tracked
            panel._sb_probe_clip = lambda p: dict(info)
            panel.run_ffmpeg_tracked = lambda cmd, label: cap.setdefault("cmd", cmd) and ("", "")
            try:
                panel._sb_assemble_film(
                    [str(clip)], Path(d) / "film.mp4",
                    timeline=[{"path": str(clip), "start": 0.0, "end": 6.0, "film_start": 0.0}],
                    sound_strips=[{"path": str(snd), "start": 0.0, "end": 1.0, "at": 1.0,
                                   "len": 1.0, "gain": []}])
            finally:
                panel._sb_probe_clip, panel.run_ffmpeg_tracked = real_probe, real_run
            cmd = cap["cmd"]
            ins = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-i"]
            self.assertEqual(ins, [str(clip), str(snd)])
            self.assertEqual(cmd[cmd.index(str(snd)) - 2], "-vn")
            graph = cmd[cmd.index("-filter_complex") + 1]
            self.assertIn("[1:a]atrim=start=0.000000:end=1.000000", graph)

    def test_render_refuses_a_missing_sound_file(self):
        doc = dict(_base(), audio_tracks=_tracks())
        with tempfile.TemporaryDirectory() as d:
            clip = Path(d) / "a.mp4"
            clip.write_bytes(b"x")
            doc["clips"][0]["path"] = str(clip)
            res = panel._sbe_render_edit({"id": "b", "title": "t", "created_at": 0}, doc)
        self.assertFalse(res["ok"])
        self.assertEqual(res.get("status"), 400)
        self.assertIn("no sound file at /x/laugh.wav (audio track A3)", res["error"])


class TheNleExport(unittest.TestCase):
    def export(self, tracks):
        d = Path(tempfile.mkdtemp(prefix="phos-nle-"))
        clip, laugh, sting = d / "a.mp4", d / "laugh.wav", d / "sting.wav"
        for p in (clip, laugh, sting):
            p.write_bytes(b"x")
        tr = copy.deepcopy(tracks)
        for t in tr:
            for s in t["strips"]:
                s["path"] = str(d / Path(s["path"]).name)
        doc = dict(_base())
        doc["clips"][0]["path"] = str(clip)
        probe = lambda p: {"w": 320, "h": 240, "duration": 6.0, "has_audio": True}  # noqa: E731
        res = se.export_nle(doc["clips"], d / "out", name="tracks", probe=probe,
                            audio_tracks=tr)
        return res, Path(res["xml"]).read_text(), Path(res["jsx"]).read_text()

    def test_each_track_is_its_own_xml_audio_track(self):
        res, xml, _ = self.export(_tracks())
        self.assertEqual(res["sound_strips"], 3)
        seq = ET.fromstring(xml.split("\n", 2)[2]).find("sequence")
        atracks = seq.find("media").find("audio").findall("track")
        self.assertEqual(len(atracks), 3)          # A1 clip sound + A3 + A4
        a3 = atracks[1].findall("clipitem")
        self.assertEqual(len(a3), 2)
        first = a3[0]
        self.assertEqual((first.findtext("start"), first.findtext("end"),
                          first.findtext("in"), first.findtext("out")),
                         ("24", "72", "0", "48"))
        self.assertEqual(first.findtext("enabled"), "TRUE")
        self.assertEqual(a3[1].findtext("enabled"), "FALSE")   # muted, not dropped
        kf = first.find("filter/effect/parameter").findall("keyframe")
        self.assertEqual([(k.findtext("when"), k.findtext("value")) for k in kf],
                         [("0", "0.0000"), ("12", "1.0000"), ("48", "1.0000")])
        a4 = atracks[2].findall("clipitem")[0]
        self.assertEqual((a4.findtext("in"), a4.findtext("out")), ("6", "30"))
        self.assertIn("0.2500", ET.tostring(a4, encoding="unicode"))
        # One file, described once: the two laugh strips share an id.
        ids = [c.find("file").get("id") for c in a3]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(xml.count(f'<file id="{ids[0]}">'), 1)

    def test_the_ae_script_gives_every_strip_a_named_layer(self):
        _, _, jsx = self.export(_tracks())
        self.assertIn("lay.name = \"Laughs · laugh\";", jsx)
        self.assertIn("lay.startTime = 2.250000;", jsx)        # 2.5 - 0.25
        self.assertIn("lay.inPoint = 2.500000;", jsx)
        self.assertIn("lay.audioEnabled = false;", jsx)
        self.assertIn("au.setValueAtTime(2.500000, [-12.041, -12.041]);", jsx)

    def test_no_tracks_exports_what_it_always_did(self):
        d = Path(tempfile.mkdtemp(prefix="phos-nle0-"))
        clip = d / "a.mp4"
        clip.write_bytes(b"x")
        doc = dict(_base())
        doc["clips"][0]["path"] = str(clip)
        probe = lambda p: {"w": 320, "h": 240, "duration": 6.0, "has_audio": True}  # noqa: E731
        a = se.export_nle(doc["clips"], d / "a", name="n", probe=probe)
        b = se.export_nle(doc["clips"], d / "b", name="n", probe=probe, audio_tracks=[])
        norm = lambda p: Path(p).read_text().replace(str(d / "a"), "").replace(str(d / "b"), "")  # noqa: E731
        self.assertEqual(norm(a["xml"]), norm(b["xml"]))
        self.assertEqual(norm(a["jsx"]), norm(b["jsx"]))


FFMPEG = str(getattr(panel, "FFMPEG", "") or "")


@unittest.skipUnless(FFMPEG and Path(FFMPEG).exists(), "ffmpeg not available")
class ARealRenderIsMeasured(unittest.TestCase):
    """Two tones on two tracks over a silent picture, measured per window.

    A 1 kHz "laugh" on A3 from 1.0 s (2 s long, 0.5 s fade in) and a 3 kHz
    "sting" on A4 from 2.5 s at half gain (1 s long). A bandpass around each
    tone tells the two apart inside the one mixed track.
    """

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="phos-mix-"))
        run = lambda *a: subprocess.run([FFMPEG, "-y", "-v", "error", *a], check=True)  # noqa: E731
        clip, laugh, sting = cls.d / "clip.mp4", cls.d / "laugh.wav", cls.d / "sting.wav"
        run("-f", "lavfi", "-i", "color=c=gray:s=320x240:r=24:d=6", "-f", "lavfi", "-i",
            "anullsrc=r=48000:cl=stereo", "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(clip))
        run("-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=48000:duration=2", "-ac", "2",
            str(laugh))
        run("-f", "lavfi", "-i", "sine=frequency=3000:sample_rate=48000:duration=1", "-ac", "2",
            str(sting))
        doc = {"version": se.EDIT_VERSION, "board_id": "b",
               "clips": [_clip(path=str(clip))],
               "audio_tracks": [
                   {"id": "t1", "strips": [{"id": "s1", "path": str(laugh), "start": 0,
                                            "end": 2, "film_start": 1.0,
                                            "afx": {"fade_in": 0.5}}]},
                   {"id": "t2", "strips": [{"id": "s2", "path": str(sting), "start": 0,
                                            "end": 1, "film_start": 2.5, "gain": 0.5}]}]}
        real = panel._sb_film_dir
        panel._sb_film_dir = lambda board: cls.d / "film"
        try:
            cls.res = panel._sbe_render_edit({"id": "b", "title": "t", "created_at": 0},
                                             se.normalise_edit(doc), out_name="mix.mp4")
        finally:
            panel._sb_film_dir = real
        cls.src_max = cls.vd(laugh, 0, 2)[1]

    @classmethod
    def vd(cls, path, a, d, band=None):
        af = (band + "," if band else "") + "volumedetect"
        r = subprocess.run([FFMPEG, "-hide_banner", "-ss", str(a), "-t", str(d), "-i",
                            str(path), "-af", af, "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"mean_volume: ([-\d.]+) dB", r.stderr)
        x = re.search(r"max_volume: ([-\d.]+) dB", r.stderr)
        return float(m.group(1)), float(x.group(1))

    def win(self, a, d, hz):
        return self.vd(self.res["path"], a, d, f"bandpass=f={hz}:width_type=q:w=8")[1]

    def test_it_rendered_both_strips(self):
        self.assertTrue(self.res["ok"], self.res.get("error"))
        self.assertEqual(self.res["sound_strips"], 2)

    def test_silence_before_and_after(self):
        self.assertLess(self.vd(self.res["path"], 0, 0.9)[1], -80)
        self.assertLess(self.vd(self.res["path"], 3.6, 2.3)[1], -80)

    def test_the_laugh_plays_at_its_own_level_after_its_fade(self):
        self.assertAlmostEqual(self.win(1.6, 0.8, 1000), self.src_max, delta=0.5)

    def test_the_fade_in_rises(self):
        q1, q2 = self.win(1.0, 0.25, 1000), self.win(1.25, 0.25, 1000)
        self.assertLess(q1, q2 - 4.0)
        self.assertLess(q2, self.src_max)

    def test_the_sting_is_six_db_down_and_overlaps_the_laugh(self):
        self.assertAlmostEqual(self.win(3.1, 0.8, 3000), self.src_max - 6.02, delta=0.6)
        self.assertGreater(self.win(2.55, 0.4, 1000), self.src_max - 1.0)
        self.assertGreater(self.win(2.55, 0.4, 3000), self.src_max - 7.0)

    def test_the_laugh_stops_where_its_strip_does(self):
        self.assertLess(self.win(3.1, 0.8, 1000), self.src_max - 25)


# =============================================================================
# THE CLIENT — the panel's real JavaScript, run in node
# =============================================================================
from test_storyboard_editor_ui import (FUNCTIONS, NODE, SHIM,  # noqa: E402
                                       extract_function, panel_source)

TS_FUNCTIONS = (
    "sbeTrackLabel", "sbeUnitGain", "sbeTrackGain", "sbeTsGain", "sbeTsWindow",
    "sbeTsGainPoints", "sbeTsGainAt", "sbeTsCopy", "sbeTsTrackById", "sbeTsFind",
    "sbeTsSort", "sbeTsFits", "sbeTsNearest", "sbeTsNextFree", "sbeTsNewTrack",
    "sbeTsRemoveTrack", "sbeTsSetTrack", "sbeTsPlace", "sbeTsMove", "sbeTsMoveGroup",
    "sbeTsTrim", "sbeTsSplit", "sbeTsCopyTitle", "sbeTsDuplicate", "sbeTsDelete",
    "sbeTsSetStrip", "sbeTsSetFade", "sbeTsPointsWrite", "sbeTsAddKeyframe",
    "sbeTsMoveKeyframe", "sbeTsDeleteKeyframe", "sbeTsAt", "sbeTsSnaps",
    "sbeTsFromClip", "sbeTsFromBed", "sbeTsClean", "sbeTsSelIds", "sbeTsSelectOne",
    "sbeTsSelectToggle", "sbeTsMutateEach", "sbeTsMutate", "sbeSoundLaneSel",
    "sbeCbarDupRow", "sbeTsSplitWhy", "sbeCbarTsModel", "sbeTsDuplicateSel",
    "sbeTsDeleteSel", "sbeSelNormalise", "sbeSelCount", "sbeNiceName",
)


def run_client(body: str) -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    src = panel_source()
    consts = "\n".join(m + ";" for m in re.findall(
        r"^const (?:SBE_MIX_\w+|SBE_TRACK_STRIP_MIN|SBE_TADD_H) = [^;]+", src, re.MULTILINE))
    names = list(dict.fromkeys(FUNCTIONS + TS_FUNCTIONS))
    script = (SHIM + consts + "\n"
              + "function sbeKeyHint() { return ''; }\n"
              + "function sbeBlurControl() {}\n"
              + "const SBE_TS_SPLIT_TITLE = 'split';\n"
              + "\n".join(extract_function(n, src) for n in names)
              + "\nconst out = {};\n" + body
              + "\nprocess.stdout.write(JSON.stringify(out));\n")
    with tempfile.TemporaryDirectory() as d:
        f = Path(d) / "harness.js"
        f.write_text(script, encoding="utf-8")
        r = subprocess.run([NODE, str(f)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise AssertionError("node harness failed:\n" + r.stderr[-3000:])
    return json.loads(r.stdout)


TABLE = [
    ({"strips": []}, {"path": "/a.wav", "start": 0, "end": 2, "film_start": 1}),
    ({"gain": 0.5}, {"path": "/a.wav", "start": 0, "end": 2, "film_start": 1, "gain": 0.4}),
    ({}, {"path": "/a.wav", "start": 0.5, "end": 3, "film_start": 0,
          "afx": {"fade_in": 0.3, "fade_out": 0.7}}),
    ({"gain": 0.8}, {"path": "/a.wav", "start": 0, "end": 4, "film_start": 2,
                     "afx": {"fade_out": 1, "points": [[1.0, 0.5], [2.5, 0.9]]}}),
    ({}, {"path": "/a.wav", "start": 0, "end": 1, "film_start": 0,
          "afx": {"fade_in": 0.8, "fade_out": 0.8}}),
]


@unittest.skipUnless(NODE, "node not on PATH")
class TheClientModel(unittest.TestCase):
    def test_the_browser_and_the_server_draw_the_same_strip_curve(self):
        out = run_client("out.rows = " + json.dumps(TABLE) + ".map(([t, s]) => ["
                         "sbeTsGainPoints(t, s), sbeTsGainAt(t, s, 0.6)]);")
        for (t, s), (curve, at) in zip(TABLE, out["rows"]):
            want = se.track_strip_gain_points(t, s)
            self.assertEqual(len(curve), len(want), (t, s))
            for (a, b), (c, d) in zip(curve, want):
                self.assertAlmostEqual(a, c, places=5)
                self.assertAlmostEqual(b, d, places=5)
            self.assertAlmostEqual(at, se.track_strip_gain_at(t, s, 0.6), places=5)

    def test_every_edit_keeps_a_track_legal_by_the_servers_rule(self):
        out = run_client(r"""
const tr = [{ id: 't1', strips: [
  { id: 'a', path: '/l.wav', start: 0, end: 2, film_start: 1, duration: 5 },
  { id: 'b', path: '/l.wav', start: 1, end: 2, film_start: 4, duration: 5 }] },
  { id: 't2', strips: [] }];
out.moveOnto = sbeTsMove(tr, 'b', 't1', 2.2).tracks;          // lands butted, not on top
out.moveAcross = sbeTsMove(tr, 'b', 't2', 1.5).tracks;        // other track: exactly there
out.trimR = sbeTsTrim(tr, 'a', 'trimR', 9).tracks;            // stops at b
out.trimL = sbeTsTrim(tr, 'b', 'trimL', 0).tracks;            // stops at a, keeps its out
out.dup = sbeTsDuplicate(tr, 'a');                            // after itself: 3..5 overlaps b
out.split = sbeTsSplit([{ id: 't', strips: [{ id: 's', path: '/l.wav', start: 1, end: 3,
   film_start: 10, afx: { fade_in: 0.2, fade_out: 0.4, points: [[0.5, 0.5], [1.5, 1]] } }] }],
   's', 11, 'n2').tracks;
out.ripple = sbeTsDelete(tr, 'a', true).tracks;
out.lift = sbeTsDelete(tr, 'a', false).tracks;
out.group = sbeTsMoveGroup(tr, ['a', 'b'], -5);
out.place = sbeTsPlace(tr, '', { path: '/x.wav', start: 0, end: 1, film_start: 0 }, 1.5);
out.placeNew = sbeTsPlace([], 'new', { path: '/x.wav', start: 0, end: 1 }, 3);
out.untouched = tr;
""")
        def legal(tracks):
            doc = dict(_base(), audio_tracks=tracks)
            return [e["code"] for e in se.blocking_errors(se.validate_edit(doc))]

        for key in ("moveOnto", "moveAcross", "trimR", "trimL", "split", "ripple", "lift"):
            with self.subTest(key=key):
                self.assertEqual(legal(out[key]), [])
        self.assertEqual(legal(out["dup"]["tracks"]), [])
        t1 = {s["id"]: s for s in out["moveOnto"][0]["strips"]}
        self.assertEqual(t1["b"]["film_start"], 3.0)
        self.assertEqual(out["moveAcross"][1]["strips"][0]["film_start"], 1.5)
        self.assertEqual([s["end"] for s in out["trimR"][0]["strips"]], [3, 2])
        b = {s["id"]: s for s in out["trimL"][0]["strips"]}["b"]
        # The head comes back one second — as far as the file has, and exactly
        # to where the strip before it ends — and the out-point stays put.
        self.assertEqual((b["film_start"], b["start"], b["end"]), (3.0, 0, 2))
        self.assertEqual(out["dup"]["added"]["film_start"], 5.0)
        self.assertEqual(out["dup"]["added"]["title"], "l.wav (copy)")
        halves = out["split"][0]["strips"]
        self.assertEqual([(h["start"], h["end"], h["film_start"]) for h in halves],
                         [(1, 2, 10), (2, 3, 11)])
        self.assertEqual(halves[0]["afx"], {"fade_in": 0.2, "points": [[0.5, 0.5], [1, 0.75]]})
        self.assertEqual(halves[1]["afx"], {"fade_out": 0.4, "points": [[0, 0.75], [0.5, 1]]})
        self.assertEqual(out["ripple"][0]["strips"][0]["film_start"], 2.0)
        self.assertEqual(out["lift"][0]["strips"][0]["film_start"], 4)
        self.assertEqual(out["group"]["moved"], -1)                  # clamped at second 0
        self.assertEqual(out["place"]["track"], "t2")                # t1 has no room at 1.5
        self.assertEqual(len(out["placeNew"]["tracks"]), 1)
        self.assertEqual(out["untouched"][0]["strips"][1]["film_start"], 4)   # pure

    def test_duplicate_works_on_every_kind_of_sound_and_undoes(self):
        out = run_client(r"""
function sbeSetMute() {}
SBE.tracks = []; SBE.undo = []; SBE.redo = [];
SBE.clips = [clip({ id: 'c1', path: '/o/c1.mp4', start: 1, end: 4, film_start: 0, film_end: 3,
                    duration: 10, afx: { fade_out: 0.5 } }),
             clip({ id: 'c2', path: '/o/c2.mp4', start: 0, end: 2, film_start: 3, film_end: 4,
                    speed: 2, duration: 10 })];
SBE.audio = { path: '/o/bed.wav', offset: 0, duration: 8, mix: { bed_gain: 0.5 } };
// A2: the bed, onto a new track right after its own end.
SBE.sel = '@music'; SBE.selSet = [];
out.musicRow = sbeCbarDupRow(0, false, null, 'no');
out.music = sbeTsDuplicateSel();
out.afterMusic = JSON.parse(JSON.stringify(SBE.tracks));
// A1: the clip's SOUND (its window, its envelope), not the shot.
SBE.sel = 'c1'; SBE.selSet = ['c1']; SBE.selLane = 'c1';
out.laneRow = sbeCbarDupRow(1, false, SBE.clips[0], 'no');
out.lane = sbeTsDuplicateSel();
out.afterLane = JSON.parse(JSON.stringify(SBE.tracks));
out.clipsAfter = SBE.clips.length;
// A retimed clip's sound is refused, and says why.
SBE.sel = 'c2'; SBE.selSet = ['c2']; SBE.selLane = 'c2';
out.fastRow = sbeCbarDupRow(1, false, SBE.clips[1], 'no');
// The shot is still the shot when the PICTURE was clicked.
SBE.selLane = '';
out.shotRow = sbeCbarDupRow(1, false, SBE.clips[1], 'no');
// A track strip: after itself on its own track, and the bar says so.
const first = SBE.tracks[0].strips.find(s => s.path === '/o/bed.wav').id;
sbeTsSelectOne(first);
out.tsModel = sbeCbarTsModel(sbeTsSelIds()).rows.map(r => [r.id, r.why]);
out.ts = sbeTsDuplicateSel();
out.afterTs = JSON.parse(JSON.stringify(SBE.tracks));
out.undoDepth = SBE.undo.length;
sbeUndo(); sbeUndo(); sbeUndo();
out.afterUndo = SBE.tracks;
out.body0 = sbeSaveBody({ id: 'b', edit: { audio_tracks: [{ id: 'old', strips: [] }] }, clips: [], tracks: [] }).edit;
out.body1 = sbeSaveBody({ id: 'b', edit: {}, clips: [], tracks: [{ id: 't', strips: [{ id: 's', path: '/a.wav', start: 0, end: 1, film_start: 0, _drag: 1 }] }] }).edit;
""")
        self.assertEqual(out["musicRow"]["why"], "")
        self.assertTrue(out["music"])
        s = out["afterMusic"][0]["strips"][0]
        self.assertEqual((s["path"], s["start"], s["end"], s["film_start"], s["gain"]),
                         ("/o/bed.wav", 0, 8, 8, 0.5))
        self.assertEqual(out["laneRow"]["why"], "")
        self.assertIn("sound onto an audio track", out["laneRow"]["title"])
        a1 = [x for t in out["afterLane"] for x in t["strips"] if x["path"] == "/o/c1.mp4"][0]
        self.assertEqual((a1["start"], a1["end"], a1["film_start"], a1["afx"]),
                         (1, 4, 3, {"fade_out": 0.5}))
        self.assertEqual(out["clipsAfter"], 2)
        self.assertIn("1x", out["fastRow"]["why"])
        self.assertIn("same shot", out["shotRow"]["title"])
        rows = dict(out["tsModel"])
        self.assertEqual(rows["sbeCbDup"], "")
        self.assertIn("no picture", rows["sbeCbLink"])
        bed_track = [t for t in out["afterTs"] if any(x["path"] == "/o/bed.wav" for x in t["strips"])][0]
        # The bed copy was duplicated right after itself on its own track; the
        # A1 copy found room on that same track at 3 s, before it.
        self.assertEqual([x["film_start"] for x in bed_track["strips"]
                          if x["path"] == "/o/bed.wav"], [8, 16])
        self.assertEqual(len(out["afterTs"]), 1)
        self.assertEqual(out["undoDepth"], 3)
        self.assertEqual(out["afterUndo"], [])
        self.assertNotIn("audio_tracks", out["body0"])
        self.assertEqual(out["body1"]["audio_tracks"][0]["strips"][0],
                         {"id": "s", "path": "/a.wav", "start": 0, "end": 1, "film_start": 0})
        doc = dict(_base(), audio_tracks=out["afterTs"])
        self.assertEqual(se.blocking_errors(se.validate_edit(doc)), [])

    def test_the_preview_plays_what_the_render_mixes(self):
        tr = _tracks()
        out = run_client("out.at = [0.5, 1.2, 2.7, 3.4].map(t => sbeTsAt("
                         + json.dumps(tr) + ", t));")
        rows = se.track_render_strips(dict(_base(), audio_tracks=tr))
        for t, voices in zip((0.5, 1.2, 2.7, 3.4), out["at"]):
            want = [r for r in rows if r["at"] <= t < r["at"] + r["len"]]
            self.assertEqual(sorted(v["id"] for v in voices),
                             sorted("ts:" + r["id"] for r in want), t)
            for v in voices:
                r = [x for x in want if "ts:" + x["id"] == v["id"]][0]
                self.assertAlmostEqual(v["at"], r["start"] + (t - r["at"]), places=5)
                track = [x for x in tr if any(s["id"] == r["id"] for s in x["strips"])][0]
                strip = [s for s in track["strips"] if s["id"] == r["id"]][0]
                self.assertAlmostEqual(v["vol"], se.track_strip_gain_at(track, strip, t - r["at"]),
                                       places=5)


if __name__ == "__main__":
    unittest.main()
