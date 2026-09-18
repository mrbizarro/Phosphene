#!/usr/bin/env python3
"""Picture mode and Sound mode — the contract, on screen and in code.

The sound lanes used to size themselves: thin until you went near them, full
height for as long as you were on sound, small again 2.4 s after you left. The
owner, after cutting a film with it: "I don't like that the audio editing mode
is automatic and moves the screens up. It's really weird… The automatic
changing of the size of the timeline if you are going around with audio is
really bad. I think a button is warranted where you can enter sound editing
mode, and then you see the sound very big and the image very little."

So the split has TWO STATES AND ONE SWITCH, and this file holds it to that:

  * NOTHING MOVES BY ITSELF. No hover, click, selection, playback or timer
    changes the split. The functions that did (sbeAudioWant, sbeAudioArm,
    sbeAudioTouch, sbeAudioOver, sbeAudioBusy, the idle constant) are gone and
    may not come back under any name; the pointer-over wiring is gone with
    them.
  * ONE SWITCH, THREE DOORS. sbeSoundModeSet is the only writer; ⇧A, the ⌁
    Sound button on the tool row and the ▾ on the A1 head all call its toggle.
    Each mode remembers its own timeline height, per browser, never in the
    film.
  * THE LANE TABLE STILL CARRIES A SECOND HEIGHT, and the floor and the roof
    are still the sum of the lane set on screen — that arithmetic is what
    makes picture mode give the height to the picture.
  * A THIN LANE IS STILL A PICTURE, NOT A CONTROL: one click selects what was
    clicked and nothing resizes; a double-click is sound mode.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "webapp"
sys.path.insert(0, str(ROOT))
from test_storyboard_editor_ui import extract_function  # noqa: E402


class TheSplitHasTwoStatesAndOneSwitch(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.js = (WEB / "js" / "editor.js").read_text(encoding="utf-8")
        cls.css = (WEB / "style" / "panel.css").read_text(encoding="utf-8")
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.docs = (WEB / "docs" / "editor.md").read_text(encoding="utf-8")
        cls.keys = (WEB / "js" / "shortcuts.js").read_text(encoding="utf-8")

    # ---- the lane table and the two ends ------------------------------------
    def test_every_lane_in_the_sound_area_has_a_second_height(self):
        lanes = re.findall(
            r"\{ key: '(\w+)',\s+base:\s*(\d+), cap:\s*(\d+), share: ([\d.]+)"
            r"(, audio: true, small: (\d+))?", self.js)
        by = {l[0]: l for l in lanes}
        self.assertEqual(sorted(by), ["alane", "ov", "track", "wave"])
        for key, small in (("alane", "18"), ("wave", "20")):
            self.assertTrue(by[key][4], f"{key} has no small height")
            self.assertEqual(by[key][5], small)
            self.assertLess(int(small), int(by[key][1]))
        for key in ("ov", "track"):
            self.assertFalse(by[key][4], f"{key} is not part of the sound area")
        self.assertIn("lanes.alane", extract_function("sbeTracksExtraH", self.js))

    def test_the_floor_and_the_ceiling_are_the_lane_set_on_screen(self):
        self.assertIn("globalThis.SBE_TL_MIN_H = sbeTlFloor(false);", self.js)
        self.assertIn("globalThis.SBE_TL_MAX_H = sbeTlRoof(false);", self.js)
        fit = extract_function("sbeFitMonitors", self.js)
        self.assertIn("sbeTlFloor()", fit)
        self.assertIn("sbeTlRoof()", fit)
        self.assertIn("avail - SBE_MON_MIN_H - extra", fit)
        self.assertIn("(max-width: 900px)", fit)
        self.assertIn("return;", fit[fit.index("(max-width: 900px)"):])
        # "small" has exactly one input: the mode.
        self.assertIn("function sbeAudioSmall() { return !sbeSoundMode(); }", self.js)
        clamp = extract_function("sbeTlClamp", self.js)
        self.assertIn("const floor = sbeTlFloor(small), roof = sbeTlRoof(small);", clamp)

    # ---- nothing moves by itself -----------------------------------------------
    def test_nothing_resizes_the_split_but_the_switch(self):
        for gone in ("sbeAudioWant", "sbeAudioArm", "sbeAudioSync", "sbeAudioTouch",
                     "sbeAudioOver", "sbeAudioBusy", "sbeAudioPinToggle", "sbeAudioPinSet",
                     "sbeAudioSet", "sbeAudioSmallOnce", "SBE_ASMALL_IDLE",
                     "aTouch", "aHover", "aPin"):
            self.assertNotIn(gone, self.js, f"{gone} is back — the split may move by itself again")
        for wire in ("addEventListener('pointerenter', () => sbeAudio",
                     "addEventListener('pointerleave', () => sbeAudio",
                     "gutter.addEventListener('pointermove'"):
            self.assertNotIn(wire, self.js, wire)
        # The handle no longer "asks for the sound back" when dragged up.
        self.assertNotIn("sbeAudioTouch", extract_function("sbeTlSet", self.js))
        # A sound verb, a new track, a sound file, the Sound pool: none of them
        # touch the split.
        for fn in ("sbeToggleAudioLink", "sbeResyncSel", "sbeAlternateSel",
                   "sbeTrackAdd", "sbeTsAddSoundPath", "edPoolSrc"):
            body = extract_function(fn, self.js)
            self.assertNotIn("sbeSoundModeSet", body, fn)
            self.assertNotIn("sbeAudioTouch", body, fn)
        # sbePaint does not arm a timer.
        self.assertNotIn("sbeAudioArm", extract_function("sbePaint", self.js))

    def test_the_one_writer_flips_the_class_and_swaps_the_height(self):
        setter = extract_function("sbeSoundModeSet", self.js)
        self.assertIn("SBE.mode = want ? 'sound' : 'picture';", setter)
        self.assertIn("sbeModeWrite(SBE.mode);", setter)
        self.assertIn("plan.classList.toggle('is-asmall', !want);", setter)
        self.assertIn("plan.classList.toggle('is-sound', want);", setter)
        self.assertIn("SBE.tlH = sbeTlPrefRead();", setter)
        self.assertIn("sbeApplyTl(sbeTlClamp(SBE.tlH, SBE.tlMax));", setter)
        # animated on a change, never on the open of a document, and the drawn
        # waveforms are re-issued at the height they landed on
        self.assertIn("classList.add('sbe-aanim')", setter)
        self.assertIn("SBE.laneAt = -1;", setter)
        self.assertIn("opts.quiet", setter)
        self.assertIn("transition: height 180ms", self.css)
        # sbeSoundModeSet is the only thing that writes SBE.mode
        self.assertEqual(self.js.count("SBE.mode = "), 1)

    def test_each_mode_remembers_its_own_height_in_this_browser(self):
        self.assertIn("function sbeTlPrefKey() { return sbeSoundMode() ? 'phos_sbe_tl_h_sound' : 'phos_sbe_tl_h'; }",
                      self.js)
        read = extract_function("sbeTlPrefRead", self.js)
        self.assertIn("localStorage.getItem(sbeTlPrefKey())", read)
        # picture mode starts as low as it goes, sound mode as tall as it goes
        self.assertIn("return small ? sbeTlFloor(true) : sbeTlRoof(false);", read)
        self.assertIn("localStorage.setItem(sbeTlPrefKey()", extract_function("sbeTlPrefWrite", self.js))
        self.assertIn("localStorage.getItem('phos_sbe_mode')", self.js)
        self.assertIn("localStorage.setItem('phos_sbe_mode'", self.js)
        save = extract_function("sbeSaveBody", self.js)
        for word in ("mode", "phos_sbe_mode", "inspect", "phos_sbe_inspect"):
            self.assertNotIn(word, save)
        self.assertNotIn("audio_small", self.js)
        # the document open restores the mode quietly, before anything is drawn
        opened = extract_function("sbeOpen", self.js)
        self.assertIn("sbeSoundModeSet(sbeModeRead() === 'sound', { quiet: true });", opened)

    # ---- three doors, one switch --------------------------------------------------
    def test_the_button_the_key_and_the_head_are_the_same_control(self):
        self.assertIn('id="sbeSoundBtn" onclick="sbeSoundModeToggle()"', self.html)
        self.assertIn('data-shortcut="editor.soundMode"', self.html)
        self.assertIn('id="sbeAudioSize"', self.html)
        head = self.html[self.html.index('id="sbeAudioSize"'):]
        head = head[:head.index("</button>")]
        self.assertIn('onclick="sbeSoundModeToggle()"', head)
        row = self.keys[self.keys.index("id: 'editor.soundMode'"):]
        row = row[:row.index("},")]
        self.assertIn("combos: ['shift+a']", row)
        self.assertIn("calls: 'sbeSoundModeToggle'", row)
        self.assertIn("scope: 'editor'", row)
        self.assertIn("ev.preventDefault(); sbeSoundModeToggle(); return;", self.js)
        self.assertNotIn("editor.soundLanes", self.keys)
        self.assertNotIn("editor.soundLanes", self.js)
        heads = extract_function("sbePaintHeads", self.js)
        self.assertIn("shortcutHint('editor.soundMode')", heads)
        self.assertIn("'▸' : '▾'", heads)
        panels = extract_function("sbePaintPanels", self.js)
        self.assertIn("sbeViewTip(sbeEl('sbeSoundBtn'), sbeSoundMode(), 'Sound mode',", panels)
        self.assertIn("'editor.soundMode'", panels)
        tip = extract_function("sbeViewTip", self.js)
        self.assertIn("btn.setAttribute('aria-pressed', pressed ? 'true' : 'false');", tip)
        # the button lives in the header's View group now, with the other panels
        view = self.html[self.html.index('id="sbeView"'):]
        view = view[:view.index("</span>\n")]
        self.assertIn('id="sbeSoundBtn"', view)

    # ---- a thin lane is a picture -----------------------------------------------------
    def test_a_thin_lane_selects_on_one_click_and_is_sound_mode_on_two(self):
        for fn in ("sbeOnAudioDown", "sbeOnMusicDown", "sbeOnTsDown"):
            body = extract_function(fn, self.js)
            self.assertIn("if (sbeAudioSmall()) { sbeAudioOpenFrom(ev); return; }", body, fn)
        opener = extract_function("sbeAudioOpenFrom", self.js)
        self.assertIn("sbeTsSelectOne(", opener)
        self.assertIn("sbeSelectOne(", opener)
        self.assertIn("'@music'", opener)
        self.assertNotIn("sbeSoundModeSet", opener)       # one click never resizes
        for fn in ("sbeOnAudioDbl", "sbeOnMusicDbl", "sbeOnTsDbl"):
            body = extract_function(fn, self.js)
            self.assertIn("if (sbeAudioSmall()) { sbeSoundModeSet(true); return; }", body, fn)
        self.assertIn("sbeAudioSmall() ? 8 : 14", extract_function("sbeStripH", self.js))

    def test_the_stacked_page_keeps_its_controls(self):
        i = self.css.index("body.sbe-aanim .sbe-alane")
        guard = self.css.rindex("@media (min-width: 901px) {", 0, i)
        self.assertLess(guard, i)
        self.assertLess(i, self.css.index(".is-asmall .sbe-grip"))

    def test_the_manual_says_it_in_the_words_on_screen(self):
        self.assertIn("{#compact}", self.docs)
        for words in ("Sound mode", "Picture mode", "[[sc:editor.soundMode]]",
                      "Nothing changes the split by itself", "remembered in this browser"):
            self.assertIn(words, self.docs)
        self.assertNotIn("make themselves small", self.docs)


if __name__ == "__main__":
    unittest.main()
