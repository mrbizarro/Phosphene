#!/usr/bin/env python3
"""Clip sound on two lanes (A/B) — alternation, lane-scoped overlap, the mix.

The owner, 2026-09-15: "When the app creates a timeline, the sound between
clips should already be intercalated into two different lanes. Clip 1: the
sound is in one lane. Clip 2: the sound is in another lane. If I want to blend
and dissolve from both sides, cut a little, and bring the clips together to
make it more realistic, it's easy."

Locked here:

  * absent is lane A: `clip_sound_lane`, normalise (lane 1 is no field), and a
    document that never used lane B builds a filter graph BYTE-IDENTICAL to the
    one the code built before lanes existed (golden hashes taken from dev
    85a7098 over 42 graphs: linked, J-cut, muted, level curve, speed,
    transition, still — each with no music / under / replace, with and without
    audio-track strips);
  * a fresh timeline (`edit_from_plan`) alternates A, B, A… by film order, and
    `alternate_sound_lanes` / `sound_lane_after` are the one-click and the
    insert rule;
  * overlap is refused (warned) WITHIN a lane only;
  * the render lays lane B as its own concat and SUMS it with lane A under the
    one `asoftclip`; a real ffmpeg render of two tones proves both sounds play
    across the cut on two lanes and the old tail-trim on one;
  * the bed duck follows sound on either lane;
  * the FCP7 export writes lane B as its own audio track;
  * the browser's model agrees with the server's (node harness).

Run:  python3 -m unittest test_audio_ab_lanes
"""
from __future__ import annotations

import copy
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
_SCRATCH = Path(tempfile.mkdtemp(prefix="phos-ablanes-"))
for _k, _d in (("LTX_STATE_DIR", "state"), ("LTX_OUTPUT_DIR", "out"),
               ("LTX_UPLOADS_DIR", "up")):
    os.environ.setdefault(_k, str(_SCRATCH / _d))
    Path(os.environ[_k]).mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as se                                       # noqa: E402

FFMPEG = str(getattr(panel, "FFMPEG", "") or "")


def _clip(cid, fs, length, **kw):
    c = {"id": cid, "path": f"/x/{cid}.mp4", "start": 0.0, "end": float(length),
         "film_start": float(fs), "film_end": float(fs + length),
         "source": "auto", "locked": False}
    c.update(kw)
    return c


def _doc(clips, **kw):
    d = {"version": se.EDIT_VERSION, "board_id": "b", "clips": clips, "audio": None}
    d.update(kw)
    return d


# =============================================================================
# THE MODEL
# =============================================================================
class TheLane(unittest.TestCase):
    def test_absent_is_lane_a_and_only_the_integer_two_is_b(self):
        self.assertEqual(se.clip_sound_lane(_clip("a", 0, 2)), 1)
        self.assertEqual(se.clip_sound_lane(_clip("a", 0, 2, sound_lane=2)), 2)
        self.assertEqual(se.clip_sound_lane(_clip("a", 0, 2, sound_lane=True)), 1)
        self.assertEqual(se.clip_sound_lane(_clip("a", 0, 2, sound_lane="2")), 1)
        still = _clip("s", 0, 2, kind="still", path="/x/s.png", sound_lane=2)
        self.assertEqual(se.clip_sound_lane(still), 1)

    def test_alternate_lays_video_clips_a_b_a_in_film_order(self):
        clips = [_clip("c", 8, 2), _clip("a", 0, 3, sound_lane=2),
                 _clip("s", 3, 2, kind="still", path="/x/s.png"), _clip("b", 5, 3)]
        before = copy.deepcopy(clips)
        changed = se.alternate_sound_lanes(clips)
        lanes = {c["id"]: se.clip_sound_lane(c) for c in clips}
        self.assertEqual(lanes, {"a": 1, "b": 2, "c": 1, "s": 1})
        self.assertEqual(changed, 2)                       # a: B→A, b: A→B
        self.assertNotIn("sound_lane", clips[1])           # A is no field
        # Nothing moved in time.
        for x, y in zip(before, clips):
            for k in ("start", "end", "film_start", "film_end"):
                self.assertEqual(x[k], y[k])
        self.assertEqual(se.alternate_sound_lanes(clips), 0)

    def test_an_arriving_clip_takes_the_lane_its_neighbour_is_not_on(self):
        clips = [_clip("a", 0, 3), _clip("b", 3, 3, sound_lane=2)]
        self.assertEqual(se.sound_lane_after(clips, 6.0), 1)
        self.assertEqual(se.sound_lane_after(clips, 3.0), 2)   # after a (A)
        self.assertEqual(se.sound_lane_after(clips, 0.0), 2)   # before a: not a's
        self.assertEqual(se.sound_lane_after([], 0.0), 1)

    def test_a_fresh_timeline_alternates(self):
        plan = [{"path": f"/x/{i}.mp4", "start": 0.0, "end": 2.0,
                 "film_start": 2.0 * i, "film_end": 2.0 * i + 2} for i in range(5)]
        ed = se.edit_from_plan(plan, board_id="b")
        self.assertEqual([se.clip_sound_lane(c) for c in ed["clips"]], [1, 2, 1, 2, 1])
        self.assertNotIn("sound_lane", ed["clips"][0])
        self.assertEqual(ed["clips"][1]["sound_lane"], 2)
        self.assertEqual(se.blocking_errors(se.validate_edit(ed)), [])

    def test_normalise_writes_b_only(self):
        doc = se.normalise_edit(_doc([
            _clip("a", 0, 2, sound_lane=1), _clip("b", 2, 2, sound_lane=2),
            _clip("s", 4, 2, kind="still", path="/x/s.png", sound_lane=2)]))
        self.assertNotIn("sound_lane", doc["clips"][0])
        self.assertEqual(doc["clips"][1]["sound_lane"], 2)
        self.assertNotIn("sound_lane", doc["clips"][2])

    def test_the_lane_is_content(self):
        a = se.normalise_edit(_doc([_clip("a", 0, 2), _clip("b", 2, 2)]))
        b = copy.deepcopy(a)
        b["clips"][1]["sound_lane"] = 2
        self.assertNotEqual(se.edit_digest(a), se.edit_digest(b))

    def test_cuts_carry_lane_b_and_nothing_for_a(self):
        doc = se.normalise_edit(_doc([_clip("a", 0, 2), _clip("b", 2, 2, sound_lane=2)]))
        cuts = se.edit_to_cuts(doc)
        self.assertNotIn("lane", cuts[0])
        self.assertEqual(cuts[1]["lane"], 2)


class OverlapIsPerLane(unittest.TestCase):
    def jcut(self, lane_b):
        extra = {"sound_lane": 2} if lane_b else {}
        # b's sound starts 1 s before its picture, under a.
        return _doc([_clip("a", 0, 3),
                     _clip("b", 3, 3, audio={"start": 0.0, "end": 3.0, "film_start": 2.0},
                           **extra)])

    def test_one_lane_warns(self):
        errs = se.validate_edit(self.jcut(False))
        codes = [e["code"] for e in errs]
        self.assertEqual(codes, ["clips_audio_overlap"])
        self.assertIn("sound lane A", errs[0]["message"])
        self.assertEqual(se.blocking_errors(errs), [])

    def test_two_lanes_are_clean(self):
        self.assertEqual(se.validate_edit(self.jcut(True)), [])

    def test_two_sounds_on_b_still_warn_and_say_b(self):
        doc = self.jcut(True)
        doc["clips"][0]["sound_lane"] = 2
        errs = se.validate_edit(doc)
        self.assertEqual([e["code"] for e in errs], ["clips_audio_overlap"])
        self.assertIn("sound lane B", errs[0]["message"])

    def test_a_bad_value_is_refused(self):
        for v in (3, 0, "2", True):
            doc = _doc([_clip("a", 0, 2, sound_lane=v)])
            self.assertIn("clip_sound_lane",
                          [e["code"] for e in se.blocking_errors(se.validate_edit(doc))], v)

    def test_the_duck_follows_either_lane(self):
        # a on A 0-3, b on B from 2.0 to 5.0 → one merged window 0-5 either way.
        both = se.audible_strips(self.jcut(True))
        self.assertEqual(both, [[0.0, 5.0]])
        # A lane-B sound alone in a stretch ducks the bed there too.
        doc = _doc([_clip("a", 0, 2),
                    _clip("b", 6, 2, sound_lane=2)])
        self.assertEqual(se.audible_strips(doc), [[0.0, 2.0], [6.0, 8.0]])
        doc["audio"] = {"path": "/x/m.wav", "duration": 10.0, "mix": {"duck": True}}
        curve = se.bed_gain_points(doc)
        self.assertLess(se._lerp_gain(curve, 7.0), 0.3)
        self.assertAlmostEqual(se._lerp_gain(curve, 4.0), 1.0, places=6)


# =============================================================================
# THE RENDER GRAPH
# =============================================================================
INFO = {"w": 320, "h": 240, "duration": 5.0, "has_audio": True, "sample_rate": 48000}


def _seg(i, **kw):
    s = {"kind": "video", "input": i, "path": f"/x/{i}.mp4", "duration": 4.0,
         "info": dict(INFO), "window": {"start": 0.5, "end": 4.5}}
    s.update(kw)
    return s


CASES = {
    "plain": [_seg(0), _seg(1), _seg(2)],
    "jcut": [_seg(0), _seg(1, audio={"start": 0.0, "end": 4.5, "delta": -0.5}), _seg(2)],
    "muted": [_seg(0), _seg(1, mute=True), _seg(2)],
    "gain": [_seg(0, gain=[[0, 1.0], [1, 0.5]]), _seg(1)],
    "speed": [_seg(0, speed=2.0, duration=2.0),
              _seg(1, audio={"start": 0.2, "end": 4.0, "delta": 0.3})],
    "tx": [_seg(0, transition={"kind": "dissolve", "duration": 0.5}), _seg(1, tx_in=0.25),
           _seg(2)],
    "still": [_seg(0), {"kind": "still", "input": 1, "path": "/x/s.png", "duration": 2.0,
                        "info": {"w": 320, "h": 240}, "window": None}, _seg(2)],
}
MUSIC = {"path": "/x/m.wav", "start": 0.0, "end": None, "delay": 0.5, "mode": "under",
         "gain": [[0.5, 0.7], [9.0, 0.7]]}
STRIPS = [{"path": "/x/l.wav", "start": 0.0, "end": 1.0, "at": 1.0, "len": 1.0, "gain": []}]

# sha1(graph + "|" + video label)[:16], from dev 85a7098 — BEFORE the lanes.
GOLDEN = {
    "gain/none/nostrips": "26328df2c6a5518c", "gain/none/strips": "1fe1bbd8f7f5e1e3",
    "gain/replace/nostrips": "bc74719a3282928f", "gain/replace/strips": "3891b6ddd8986055",
    "gain/under/nostrips": "4eae2abc183feeb6", "gain/under/strips": "2094c00763dc5e2f",
    "jcut/none/nostrips": "0d06218f449c00ff", "jcut/none/strips": "1e4f665011998733",
    "jcut/replace/nostrips": "e82fab35e281221b", "jcut/replace/strips": "b26ac1bc8cff1206",
    "jcut/under/nostrips": "2c6be62fa54ea27e", "jcut/under/strips": "9b6eac9faf3a72a2",
    "muted/none/nostrips": "bc8e331298c1dfd3", "muted/none/strips": "2501e4eca31f70e8",
    "muted/replace/nostrips": "e82fab35e281221b", "muted/replace/strips": "b26ac1bc8cff1206",
    "muted/under/nostrips": "afb8c03f1551fb6b", "muted/under/strips": "6ff5dfe2883410ff",
    "plain/none/nostrips": "fdafe1778bf98ff9", "plain/none/strips": "375ccc2d3f50bfd2",
    "plain/replace/nostrips": "e82fab35e281221b", "plain/replace/strips": "b26ac1bc8cff1206",
    "plain/under/nostrips": "0512064b984b463c", "plain/under/strips": "36c78562ff78e518",
    "speed/none/nostrips": "cf9cbb71f9ee5be5", "speed/none/strips": "96ab5cf4615dc896",
    "speed/replace/nostrips": "8fa09870321202cc", "speed/replace/strips": "c4932074109a38be",
    "speed/under/nostrips": "b930c957df4ef257", "speed/under/strips": "55c7c04bd6f64c70",
    "still/none/nostrips": "3f51d1683320c9d0", "still/none/strips": "ca87f2cc6c3e4ffa",
    "still/replace/nostrips": "e30198934f2f4611", "still/replace/strips": "2e5b4b941a4ea43f",
    "still/under/nostrips": "157947e6a66be182", "still/under/strips": "d601be1018111d02",
    "tx/none/nostrips": "cb95c34f1f53e095", "tx/none/strips": "f0185d7dd7eab553",
    "tx/replace/nostrips": "f07823f072ebf251", "tx/replace/strips": "63a102609b48d9ad",
    "tx/under/nostrips": "1f3ed50670016d46", "tx/under/strips": "0fac66de0004635f",
}


def _graph(segs, music=None, strips=None):
    g, v = panel._sb_film_filtergraph([], 320, 240, 48000, "yuv420p",
                                      segments=copy.deepcopy(segs), music=music,
                                      sound_strips=strips, sound_base=5)
    return g, v


class TheLegacyGraphIsByteIdentical(unittest.TestCase):
    def test_every_golden_graph(self):
        seen = 0
        for name, segs in CASES.items():
            for mname, music in (("none", None), ("under", MUSIC),
                                 ("replace", dict(MUSIC, mode="replace"))):
                for sname, strips in (("nostrips", None), ("strips", STRIPS)):
                    g, v = _graph(segs, music, strips)
                    key = f"{name}/{mname}/{sname}"
                    got = hashlib.sha1((g + "|" + v).encode()).hexdigest()[:16]
                    self.assertEqual(got, GOLDEN[key], key)
                    seen += 1
        self.assertEqual(seen, len(GOLDEN))

    def test_lane_a_explicitly_is_the_same_graph(self):
        segs = [_seg(0), _seg(1, lane=1), _seg(2)]
        self.assertEqual(_graph(segs), _graph(CASES["plain"]))


class TheTwoLaneGraph(unittest.TestCase):
    # b's sound J-cuts 1 s under a, on lane B.
    SEGS = [_seg(0), _seg(1, lane=2, audio={"start": 0.0, "end": 4.5, "delta": -1.0}),
            _seg(2)]

    def test_the_plan_does_not_trim_across_lanes(self):
        plan = panel._sb_split_audio_plan(copy.deepcopy(self.SEGS))
        self.assertTrue(plan["split"])
        self.assertEqual([L["idx"] for L in plan["lanes"]], [0, 2])
        self.assertEqual([L["idx"] for L in plan["lanes_b"]], [1])
        self.assertAlmostEqual(plan["lanes"][0]["len"], 4.0)       # a's tail intact
        self.assertAlmostEqual(plan["lanes_b"][0]["at"], 3.0)
        self.assertAlmostEqual(plan["lanes_b"][0]["len"], 4.5)
        # The same edit on ONE lane: the incoming sound trims a's tail, as ever.
        one = [dict(s) for s in copy.deepcopy(self.SEGS)]
        one[1].pop("lane")
        p1 = panel._sb_split_audio_plan(one)
        self.assertEqual(p1["lanes_b"], [])
        self.assertAlmostEqual(p1["lanes"][0]["len"], 3.0)

    def test_lane_b_is_its_own_concat_summed_under_one_limiter(self):
        g, _ = _graph(self.SEGS)
        self.assertIn("[a0]", g)
        self.assertIn("[a1]", g)
        lane_a = re.search(r"([^;]*)concat=n=\d+:v=0:a=1\[abase\]", g).group(1)
        lane_b = re.search(r"([^;]*)concat=n=\d+:v=0:a=1\[alb\]", g).group(1)
        self.assertIn("[a0]", lane_a)
        self.assertIn("[a2]", lane_a)
        self.assertNotIn("[a1]", lane_a)
        self.assertIn("[a1]", lane_b)
        self.assertIn("[aqb0]", lane_b)                           # its own silences
        self.assertIn("[abase][alb]amix=inputs=2:duration=first:dropout_transition=0:"
                      "normalize=0,asoftclip=type=tanh:", g)
        self.assertEqual(g.count("asoftclip"), 1)
        self.assertEqual(g.count("[aout]"), 1)

    def test_under_music_and_strips_join_the_same_sum(self):
        g, _ = _graph(self.SEGS, MUSIC, STRIPS)
        self.assertIn("[acat][bed][alb][ts0]amix=inputs=4:", g)
        self.assertEqual(g.count("asoftclip"), 1)

    def test_replace_throws_both_lanes_away(self):
        g, _ = _graph(self.SEGS, dict(MUSIC, mode="replace"))
        self.assertNotIn("[alb]", g)
        self.assertNotIn("[a1]", g)

    def test_a_linked_clip_on_b_leaves_the_plain_concat(self):
        segs = [_seg(0), _seg(1, lane=2)]
        g, _ = _graph(segs)
        self.assertNotIn("v=1:a=1", g)
        self.assertIn("[alb]", g)

    def test_the_timeline_carries_the_lane_into_the_segment(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = Path(d) / "a.mp4", Path(d) / "b.mp4"
            a.write_bytes(b"x")
            b.write_bytes(b"x")
            cap = {}
            info = dict(INFO)
            real_probe, real_run = panel._sb_probe_clip, panel.run_ffmpeg_tracked
            panel._sb_probe_clip = lambda p: dict(info)
            panel.run_ffmpeg_tracked = lambda cmd, label: cap.setdefault("cmd", cmd) and ("", "")
            try:
                panel._sb_assemble_film(
                    [str(a), str(b)], Path(d) / "film.mp4",
                    timeline=[{"path": str(a), "start": 0.0, "end": 3.0, "film_start": 0.0},
                              {"path": str(b), "start": 0.0, "end": 3.0, "film_start": 3.0,
                               "lane": 2}])
            finally:
                panel._sb_probe_clip, panel.run_ffmpeg_tracked = real_probe, real_run
            graph = cap["cmd"][cap["cmd"].index("-filter_complex") + 1]
            self.assertIn("[alb]", graph)


# =============================================================================
# A REAL RENDER, MEASURED
# =============================================================================
@unittest.skipUnless(FFMPEG, "ffmpeg not available")
class ARealCrossfadeIsMeasured(unittest.TestCase):
    """Shot a carries a 1 kHz tone, shot b a 3 kHz tone. b's sound is pulled
    1 s early (2.0 s) under a's last second (a ends at 3.0). On two lanes both
    tones play in 2.0–3.0; on one lane a's tail is cut at 2.0 (the old rule)."""

    @classmethod
    def setUpClass(cls):
        cls.d = Path(tempfile.mkdtemp(prefix="phos-abmix-"))
        run = lambda *a: subprocess.run([FFMPEG, "-y", "-v", "error", *a], check=True)  # noqa: E731
        cls.a, cls.b = cls.d / "a.mp4", cls.d / "b.mp4"
        for path, hz in ((cls.a, 1000), (cls.b, 3000)):
            run("-f", "lavfi", "-i", "color=c=gray:s=320x240:r=24:d=4", "-f", "lavfi",
                "-i", f"sine=frequency={hz}:sample_rate=48000:duration=4", "-ac", "2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                "-shortest", str(path))
        cls.two = cls.render(lane_b=True, name="two.mp4")
        cls.one = cls.render(lane_b=False, name="one.mp4")
        cls.src = cls.vd(cls.a, 0.5, 1.0)[1]

    @classmethod
    def render(cls, lane_b, name):
        clips = [_clip("a", 0, 3, path=str(cls.a), duration=4.0),
                 _clip("b", 3, 3, path=str(cls.b), duration=4.0,
                       audio={"start": 0.0, "end": 4.0, "film_start": 2.0})]
        if lane_b:
            clips[1]["sound_lane"] = 2
        real = panel._sb_film_dir
        panel._sb_film_dir = lambda board: cls.d / "film"
        try:
            return panel._sbe_render_edit({"id": "b", "title": "t", "created_at": 0},
                                          se.normalise_edit(_doc(clips)), out_name=name)
        finally:
            panel._sb_film_dir = real

    @classmethod
    def vd(cls, path, a, d, band=None):
        af = (band + "," if band else "") + "volumedetect"
        r = subprocess.run([FFMPEG, "-hide_banner", "-ss", str(a), "-t", str(d), "-i",
                            str(path), "-af", af, "-f", "null", "-"],
                           capture_output=True, text=True)
        m = re.search(r"mean_volume: ([-\d.]+) dB", r.stderr)
        x = re.search(r"max_volume: ([-\d.]+) dB", r.stderr)
        return float(m.group(1)), float(x.group(1))

    def band(self, res, a, d, hz):
        return self.vd(res["path"], a, d, f"bandpass=f={hz}:width_type=q:w=8")[1]

    def test_both_rendered(self):
        self.assertTrue(self.two["ok"], self.two.get("error"))
        self.assertTrue(self.one["ok"], self.one.get("error"))

    def test_two_lanes_play_both_sounds_across_the_cut(self):
        self.assertGreater(self.band(self.two, 2.2, 0.6, 1000), self.src - 1.5)
        self.assertGreater(self.band(self.two, 2.2, 0.6, 3000), self.src - 1.5)

    def test_one_lane_keeps_the_old_butt_join(self):
        self.assertLess(self.band(self.one, 2.2, 0.6, 1000), self.src - 20)
        self.assertGreater(self.band(self.one, 2.2, 0.6, 3000), self.src - 1.5)

    def test_outside_the_overlap_each_shot_is_itself(self):
        self.assertGreater(self.band(self.two, 0.5, 1.0, 1000), self.src - 1.5)
        self.assertLess(self.band(self.two, 0.5, 1.0, 3000), self.src - 20)
        self.assertGreater(self.band(self.two, 4.0, 1.0, 3000), self.src - 1.5)
        self.assertLess(self.band(self.two, 4.0, 1.0, 1000), self.src - 20)

    def test_the_sum_does_not_clip(self):
        self.assertLessEqual(self.vd(self.two["path"], 0, 6)[1], 0.0)


# =============================================================================
# THE NLE EXPORT
# =============================================================================
class TheExport(unittest.TestCase):
    def xml(self, clips):
        segs = se._nle_segments(clips, probe=lambda p: {"w": 320, "h": 240,
                                                         "duration": 5.0, "has_audio": True})
        media = {s["path"]: Path(s["path"]).name for s in segs}
        return ET.fromstring(se.fcp7_xml(segs, name="t", media=media, width=320,
                                         height=240, base="/tmp/p").split("\n", 2)[2])

    def test_lane_b_is_its_own_audio_track(self):
        root = self.xml([_clip("a", 0, 3), _clip("b", 3, 3, sound_lane=2), _clip("c", 6, 3)])
        tracks = root.findall("./sequence/media/audio/track")
        self.assertEqual(len(tracks), 2)
        self.assertEqual([c.findtext("name") for c in tracks[0].findall("clipitem")],
                         ["a", "c"])
        self.assertEqual([c.findtext("name") for c in tracks[1].findall("clipitem")], ["b"])
        self.assertEqual(tracks[1].find("clipitem/start").text, "72")

    def test_no_lane_b_is_one_clip_track_as_before(self):
        root = self.xml([_clip("a", 0, 3), _clip("b", 3, 3)])
        self.assertEqual(len(root.findall("./sequence/media/audio/track")), 1)


# =============================================================================
# THE CLIENT — the panel's real JavaScript, run in node
# =============================================================================
from test_audio_tracks import run_client  # noqa: E402
from test_storyboard_editor_ui import NODE  # noqa: E402


@unittest.skipUnless(NODE, "node not on PATH")
class TheClientModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r = run_client(r"""
const mk = (id, fs, len, extra) => Object.assign({ id: id, path: '/x/' + id + '.mp4',
  start: 0, end: len, film_start: fs, film_end: fs + len, source: 'auto', locked: false,
  duration: 10 }, extra || {});
const layAB = (cs) => { sbeAdoptGaps(cs); sbeLayout(cs); return cs; };
let clips = layAB([mk('a', 0, 3, { sound_lane: 2 }), mk('b', 3, 3),
  { id: 's', kind: 'still', path: '/x/s.png', start: 0, end: 2, film_start: 6, film_end: 8,
    source: 'auto', locked: false }, mk('c', 8, 3)]);
const alt = sbeAlternateLanes(clips);
out.alt = alt.clips.map(c => [c.id, sbeSoundLane(c), c.sound_lane === undefined]);
out.altChanged = alt.changed;
out.altTimesKept = alt.clips.map(c => [c.film_start, c.film_end]);
out.altAgain = sbeAlternateLanes(alt.clips).ok;
out.undoable = clips[0].sound_lane === 2;   // the input is not mutated
// Insert at the end: after c (A) → B. Insert at the head: before a (A) → B.
let ins = sbeInsertAt(alt.clips, { path: '/x/n.mp4', duration_s: 2, title: 'n' }, 99);
out.insEnd = sbeSoundLane(ins.added);
ins = sbeInsertAt(alt.clips, { path: '/x/h.mp4', duration_s: 2, title: 'h' }, 0);
out.insHead = sbeSoundLane(ins.added);
const dup = sbeDuplicate(alt.clips, 'b');   // b is on B → its copy goes on A
out.dup = [sbeSoundLane(sbeById(dup.clips, 'b')), sbeSoundLane(dup.added)];
const slug = sbeInsertAt(alt.clips, { kind: 'slug', duration_s: 1 }, 99);
out.slugLane = slug.added.sound_lane === undefined;
// One strip, one lane.
out.set = sbeSoundLane(sbeById(sbeSetSoundLane(alt.clips, 'a', 2).clips, 'a'));
out.setStill = sbeSetSoundLane(alt.clips, 's', 2).ok;
out.setLocked = sbeSetSoundLane(layAB([mk('z', 0, 2, { locked: true })]), 'z', 2).why;
out.setSame = sbeSetSoundLane(alt.clips, 'a', 1).ok;
// The save body writes B only.
out.clean = [sbeCleanClip(mk('p', 0, 1, { sound_lane: 1 })).sound_lane === undefined,
             sbeCleanClip(mk('q', 0, 1, { sound_lane: 2 })).sound_lane,
             sbeCleanClip({ id: 'r', kind: 'still', path: '/x/r.png', start: 0, end: 1,
                            film_start: 0, film_end: 1, sound_lane: 2 }).sound_lane === undefined];
// THE PREVIEW: b's sound J-cut 1 s under a, on lane B — both play at 2.5.
const jc = layAB([mk('a', 0, 3), mk('b', 3, 3, { sound_lane: 2,
  audio: { start: 0, end: 3, film_start: 2 } })]);
jc[0].audio = { start: 0, end: 3, film_start: 0 };
out.preview = sbeStripsAt(jc, 2.5).map(w => w.id);
""")

    def test_alternation_matches_the_server(self):
        self.assertEqual(self.r["alt"], [["a", 1, True], ["b", 2, False], ["s", 1, True],
                                         ["c", 1, True]])
        self.assertEqual(self.r["altChanged"], 2)
        self.assertEqual(self.r["altTimesKept"], [[0, 3], [3, 6], [6, 8], [8, 11]])
        self.assertFalse(self.r["altAgain"])
        self.assertTrue(self.r["undoable"])
        server = [_clip("a", 0, 3, sound_lane=2), _clip("b", 3, 3),
                  _clip("s", 6, 2, kind="still", path="/x/s.png"), _clip("c", 8, 3)]
        se.alternate_sound_lanes(server)
        self.assertEqual([[c["id"], se.clip_sound_lane(c)] for c in server],
                         [[x[0], x[1]] for x in self.r["alt"]])

    def test_new_clips_keep_the_alternation(self):
        self.assertEqual(self.r["insEnd"], 2)
        self.assertEqual(self.r["insHead"], 2)
        self.assertEqual(self.r["dup"], [2, 1])
        self.assertTrue(self.r["slugLane"])

    def test_one_strip_changes_lane_and_refuses_what_it_should(self):
        self.assertEqual(self.r["set"], 2)
        self.assertFalse(self.r["setStill"])
        self.assertEqual(self.r["setLocked"], "locked")
        self.assertFalse(self.r["setSame"])

    def test_the_save_body_writes_b_only(self):
        self.assertEqual(self.r["clean"], [True, 2, True])

    def test_the_preview_plays_both_lanes_at_once(self):
        self.assertEqual(sorted(self.r["preview"]), ["a", "b"])


class TheScreen(unittest.TestCase):
    """The markup and the words the Docs use are the ones on screen."""

    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "webapp" / "index.html").read_text(encoding="utf-8")
        cls.css = (ROOT / "webapp" / "style" / "panel.css").read_text(encoding="utf-8")
        cls.js = (ROOT / "webapp" / "js" / "editor.js").read_text(encoding="utf-8")
        cls.docs = (ROOT / "webapp" / "docs" / "editor.md").read_text(encoding="utf-8")

    def test_both_heads_and_the_one_click(self):
        self.assertIn("<b>Clip sound A</b>", self.html)
        self.assertIn("<b>Clip sound B</b>", self.html)
        self.assertIn('onclick="sbeAlternateSel()"', self.html)
        self.assertIn(">Alternate A/B</button>", self.html)
        self.assertIn("Alternate sound lanes", self.js)

    def test_the_box_is_two_lanes_and_the_timeline_pays_for_the_second(self):
        self.assertIn("#sbeAudioLane { height: calc(var(--sbe-alane-h, 44px) * 2); }", self.css)
        self.assertIn("#sbeAudioLane > .sbe-aclip.is-b", self.css)
        self.assertIn("sbeExtraLanesH(L)) + 'px'", self.js)

    def test_the_docs_use_the_screen_words(self):
        for words in ("Clip sound A", "Clip sound B", "Alternate A/B",
                      "Alternate sound lanes", "Move sound to lane B"):
            self.assertIn(words, self.docs)
            self.assertTrue(words in self.html or words in self.js
                            or words == "Move sound to lane B"
                            and "Move sound to lane ' + sbeLaneName(other)" in self.js, words)


if __name__ == "__main__":
    unittest.main()
