#!/usr/bin/env python3
"""The sound lanes make themselves small — the contract, on screen and in code.

The owner, editing a 13-shot film with clip sound on two lanes and four audio
tracks: "the sound expands lower. The picture is so tiny, and it's a problem. I
cannot see anything in the image, and I can also not push it down if I want to
compact it. Think of some way that is really seamless and user-friendly to maybe
compact all audio lanes when I am not working with them and make them little
(because I want to see the image also as I'm working sometimes)."

Three things had to be true for that, and each one is checked here:

  * THE AREA SIZES ITSELF. Every lane in the sound area — clip sound A and B,
    the soundtrack, and every A3+ track — has a second height, and one decision
    function says which one is on screen. It may not collapse mid-drag, during
    playback or under an open menu, which is the difference between "seamless"
    and "jumps".
  * THE FLOOR IS NOT A CONSTANT any more. It is the sum of the lane set that is
    on screen, so with the sound small the handle really does push the timeline
    down — the arithmetic lives in test_storyboard_editor_ui's probe; what is
    checked here is that sbeFitMonitors budgets the monitors against it.
  * IT IS A CONTROL, NOT A TRICK. A ▾ on the A1 head, a key in the one
    SHORTCUTS table, and the manual's own words — a behaviour that only
    happens to you is a bug report waiting to be filed.
"""
from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function  # noqa: E402

WEB = ROOT / "webapp"


class TheSoundAreaSizesItself(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (WEB / "js" / "editor.js").read_text(encoding="utf-8")
        cls.css = (WEB / "style" / "panel.css").read_text(encoding="utf-8")
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.docs = (WEB / "docs" / "editor.md").read_text(encoding="utf-8")
        cls.keys = (WEB / "js" / "shortcuts.js").read_text(encoding="utf-8")

    # ---- the lane table ---------------------------------------------------
    def test_every_lane_in_the_sound_area_has_a_second_height(self):
        lanes = re.findall(
            r"\{ key: '(\w+)',\s+base:\s*(\d+), cap:\s*(\d+), share: ([\d.]+)"
            r"(, audio: true, small: (\d+))?", self.js)
        by = {l[0]: l for l in lanes}
        self.assertEqual(sorted(by), ["alane", "ov", "track", "wave"])
        # The two sound lanes, and only them: the picture and the overlay are
        # not the owner's complaint and must not shrink behind his back.
        for key, small in (("alane", "18"), ("wave", "20")):
            self.assertTrue(by[key][4], f"{key} has no small height")
            self.assertEqual(by[key][5], small)
            self.assertLess(int(small), int(by[key][1]))
        for key in ("ov", "track"):
            self.assertFalse(by[key][4], f"{key} is not part of the sound area")
        # A3, A4 … are drawn at the clip lane's height, so they follow it for
        # free — that is the whole reason four tracks cost what they cost.
        self.assertIn("lanes.alane", extract_function("sbeTracksExtraH", self.js))

    def test_the_floor_and_the_ceiling_are_the_lane_set_on_screen(self):
        self.assertIn("globalThis.SBE_TL_MIN_H = sbeTlFloor(false);", self.js)
        self.assertIn("globalThis.SBE_TL_MAX_H = sbeTlRoof(false);", self.js)
        # The monitors' budget: the floor, the ceiling and the extra lanes all
        # read the CURRENT size, which is how compacting reaches the picture.
        fit = extract_function("sbeFitMonitors", self.js)
        self.assertIn("sbeTlFloor()", fit)
        self.assertIn("sbeTlRoof()", fit)
        self.assertIn("avail - SBE_MON_MIN_H - extra", fit)
        # ...and the stacked page still bails out before any of it.
        self.assertIn("(max-width: 900px)", fit)
        self.assertIn("return;", fit[fit.index("(max-width: 900px)"):])

    def test_the_handle_can_ask_for_the_smaller_floor(self):
        for fn in ("sbeApplyTl", "sbeTlReset"):
            self.assertIn("sbeTlFloor()", extract_function(fn, self.js))
        clamp = extract_function("sbeTlClamp", self.js)
        self.assertIn("const floor = sbeTlFloor(small), roof = sbeTlRoof(small);", clamp)
        # Pull the edge up past what thin lanes can use and the sound comes
        # back — there is no other height in the box left to be asking for.
        self.assertIn("sbeAudioTouch({ open: true })", extract_function("sbeTlSet", self.js))

    # ---- when it happens --------------------------------------------------
    def test_it_never_collapses_in_the_middle_of_something(self):
        busy = extract_function("sbeAudioBusy", self.js)
        for held in ("SBE.audioDrag", "SBE.musicDrag", "SBE.tsDrag", "SBE.kfDrag",
                     "SBE.tgSliding", "SBE.tlDrag", "SBE.playing", "SBE.aHover",
                     "sbePopAnyOpen()", "SBE.selLane", "'@ts:'", "'@music'",
                     "ED.src === 'sound'"):
            self.assertIn(held, busy, f"the lanes can collapse while {held}")
        want = extract_function("sbeAudioWant", self.js)
        self.assertIn("if (SBE.aPin === 'open') return false;", want)
        self.assertIn("if (SBE.aPin === 'small') return true;", want)
        self.assertIn("if (sbeAudioBusy()) return false;", want)
        self.assertIn("SBE_ASMALL_IDLE", want)
        # A couple of seconds: long enough that looking away is not a flicker.
        idle = int(re.search(r"const SBE_ASMALL_IDLE = (\d+);", self.js).group(1))
        self.assertGreaterEqual(idle, 1500)
        self.assertLessEqual(idle, 5000)

    def test_touching_sound_opens_it_at_once(self):
        touch = extract_function("sbeAudioTouch", self.js)
        self.assertIn("SBE.aTouch = Date.now();", touch)
        self.assertIn("if (SBE.aSmall && SBE.aPin !== 'small') sbeAudioSet(false);", touch)
        # The verbs aimed at sound say so, because none of them leaves a trace
        # in the selection the busy test reads.
        for fn in ("sbeToggleAudioLink", "sbeResyncSel", "sbeAlternateSel",
                   "sbeTrackAdd", "sbeTsAddSoundPath", "edPoolSrc"):
            self.assertIn("sbeAudioTouch({ open: true })",
                          extract_function(fn, self.js), fn)
        # The pointer over a lane or a head opens it, which is how anybody
        # discovers that it opens at all.
        for wire in ("alane.addEventListener('pointerenter'",
                     "lane.addEventListener('pointerenter'",
                     "tlanes.addEventListener('pointerenter'",
                     "gutter.addEventListener('pointermove'"):
            self.assertIn(wire, self.js)
        # ...and the idle is restarted by the one function every edit and
        # every selection already ends in.
        self.assertIn("sbeAudioArm();", extract_function("sbePaint", self.js))

    def test_a_small_lane_is_a_picture_and_one_click_opens_it(self):
        for fn in ("sbeOnAudioDown", "sbeOnMusicDown", "sbeOnTsDown"):
            body = extract_function(fn, self.js)
            self.assertIn("if (sbeAudioSmall()) { sbeAudioOpenFrom(ev); return; }", body, fn)
        opener = extract_function("sbeAudioOpenFrom", self.js)
        # One gesture: the area opens AND what was clicked is selected.
        self.assertIn("sbeTsSelectOne(", opener)
        self.assertIn("sbeSelectOne(", opener)
        self.assertIn("'@music'", opener)
        self.assertIn("sbeAudioTouch({ open: true });", opener)
        # The waveform still has a height to be drawn at down there.
        self.assertIn("sbeAudioSmall() ? 8 : 14", extract_function("sbeStripH", self.js))

    def test_the_change_is_animated_and_the_drag_is_not(self):
        setter = extract_function("sbeAudioSet", self.js)
        self.assertIn("classList.add('sbe-aanim')", setter)
        self.assertIn("classList.remove('sbe-aanim')", setter)
        # The waveforms are drawn, not styled: re-issued at the height they
        # landed on, exactly as the drag handle's own correction does it.
        self.assertIn("SBE.laneAt = -1;", setter)
        self.assertIn("transition: height 180ms", self.css)
        # ...and only while the class is on the body, so dragging the timeline's
        # edge — which moves the same numbers 60 times a second — stays instant.
        block = self.css[self.css.index("body.sbe-aanim .sbe-alane"):]
        self.assertLess(block.index("transition:"), block.index("}"))

    # ---- it is this browser's preference, never the film's -----------------
    def test_the_pin_lives_beside_the_timeline_height_and_not_in_the_document(self):
        self.assertIn("localStorage.getItem('phos_sbe_audio')", self.js)
        self.assertIn("localStorage.setItem('phos_sbe_audio'", self.js)
        # The save payload is the whole of what the server is told. A window's
        # shape in there would bump the film's revision — see sbeTlPrefRead.
        save = extract_function("sbeSaveBody", self.js)
        for word in ("aPin", "aSmall", "phos_sbe_audio"):
            self.assertNotIn(word, save)
        self.assertNotIn("audio_small", self.js)

    # ---- and it is a control, not a trick ---------------------------------
    def test_the_control_is_on_the_head_of_the_area_it_acts_on(self):
        self.assertIn('id="sbeAudioSize"', self.html)
        self.assertIn('onclick="sbeAudioPinToggle()"', self.html)
        self.assertIn('aria-expanded="true"', self.html)
        # On the A1 head — the top of the sound area, next to its name.
        head = self.html[self.html.index('class="sbe-gh sbe-gh-aud">'):]
        head = head[:head.index("</div>")]
        self.assertIn("sbeAudioSize", head)
        self.assertIn("<b>Clip sound A</b>", head)
        # Its glyph and its words are painted, so they cannot say "make them
        # small" while the lanes already are.
        heads = extract_function("sbePaintHeads", self.js)
        self.assertIn("sbeAudioSize", heads)
        self.assertIn("'▸' : '▾'", heads)
        self.assertIn("aria-expanded", heads)
        self.assertIn("shortcutHint('editor.soundLanes')", heads)

    def test_the_key_is_in_the_one_table(self):
        row = self.keys[self.keys.index("id: 'editor.soundLanes'"):]
        row = row[:row.index("},")]
        self.assertIn("combos: ['shift+a']", row)
        self.assertIn("calls: 'sbeAudioPinToggle'", row)
        self.assertIn("scope: 'editor'", row)
        # The Editor's own handler answers it — test_docs_and_shortcuts drives
        # the real listener; here it is enough that the branch is there.
        self.assertIn("ev.preventDefault(); sbeAudioPinToggle(); return;", self.js)

    def test_the_manual_says_it_in_the_words_on_screen(self):
        self.assertIn("{#compact}", self.docs)
        for words in ("Sound lanes small", "Clip sound A", "[[sc:editor.soundLanes]]"):
            self.assertIn(words, self.docs)
        # The docs promise the two halves the code has to keep.
        self.assertIn("come back to full height the moment you are", self.docs)
        self.assertIn("remembered in this browser", self.docs)

    def test_the_stacked_page_keeps_its_controls(self):
        # Below the breakpoint the page is the scroller, sbeFitMonitors stands
        # down and the lanes are back on their CSS heights — so the class that
        # takes the grips away may not reach down there.
        i = self.css.index("body.sbe-aanim .sbe-alane")
        guard = self.css.rindex("@media (min-width: 901px) {", 0, i)
        self.assertLess(guard, i)
        self.assertLess(i, self.css.index(".is-asmall .sbe-grip"))


if __name__ == "__main__":
    unittest.main()
