#!/usr/bin/env python3
"""The Editor's name is the way to another film.

Observed alongside the 2026-09-24 review: the title in the Editor's header
looked like a control and did nothing, and the list of films sat behind the
full-screen Editor with ⋯ → Close as the only way back to it. The title now
drops the films that have clips; picking one goes through the ordinary door
(`edOpenBoard`), which backs up unsaved work the way Close does.

The switcher's functions run for real in node; the markup and the global
publication are checked in the served sources, because an onclick naming a
function the module did not publish is a silent no-op in the browser.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

from test_editor_save_integrity import run  # noqa: E402


class TheTitleSwitchesFilms(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.src = panel_source()
        cls.r = run(r"""
SBE.open = true; SBE.id = 'sb_a';
els.sbeFilmsMenu = stubEl('sbeFilmsMenu', true);
FETCHES = [];
NEXT = { status: 200, body: { boards: [
  { id: 'sb_a', title: 'Night Drive', clips: 3 },
  { id: 'sb_b', title: 'Divorce <v2>', clips: 1 },
  { id: 'sb_c', title: 'Not rendered yet', clips: 0 } ] } };
await sbeFilmsToggle();
out.asked = FETCHES.map(f => f.url);
out.open = !els.sbeFilmsMenu.hidden;
out.html = els.sbeFilmsList.innerHTML;
out.noteHidden = els.sbeFilmsNote.hidden;
sbeFilmsPick('sb_a');
sbeFilmsPick('sb_b');
out.opened = opened.slice();
out.closedAfterPick = els.sbeFilmsMenu.hidden;
// A second press closes it without asking the panel again.
FETCHES = [];
await sbeFilmsToggle();
await sbeFilmsToggle();
out.toggledShut = els.sbeFilmsMenu.hidden;
out.askedOnClose = FETCHES.length;
sbeFilmsAll();
out.allClosed = closes;
// Alone: says so instead of offering an empty list.
NEXT = { status: 200, body: { boards: [{ id: 'sb_a', title: 'Night Drive', clips: 3 }] } };
els.sbeFilmsMenu.hidden = true;
await sbeFilmsToggle();
out.aloneNote = [els.sbeFilmsNote.hidden, els.sbeFilmsNote.textContent];
""", extra=("sbeFilmsToggle", "sbeFilmsPick", "sbeFilmsAll"), shim=r"""
const opened = [];
let closes = 0;
function edOpenBoard(id) { opened.push(id); }
function sbeClose() { closes++; }
function sbePopToggle(id) { const el = sbeEl(id); el.hidden = !el.hidden; }
function sbePopCloseAll() { sbeEl('sbeFilmsMenu').hidden = true; }
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({ '&': '&amp;',
    '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}
""")

    def test_the_title_is_a_button_that_opens_the_film_list(self):
        btn = re.search(r'<button[^>]*id="sbeFilmsBtn"[^>]*>(.*?)</button>',
                        self.src, re.S)
        self.assertIsNotNone(btn)
        self.assertIn('onclick="sbeFilmsToggle()"', btn.group(0))
        self.assertIn('aria-haspopup="menu"', btn.group(0))
        # The name the client writes into is still there, inside it.
        self.assertIn('id="sbeTitle">Editor<', btn.group(1))
        self.assertIn('id="sbeFilmsMenu"', self.src)

    def test_it_lists_the_films_with_clips_and_marks_the_open_one(self):
        self.assertEqual(self.r["asked"], ["/storyboard/list"])
        self.assertTrue(self.r["open"])
        html = self.r["html"]
        self.assertIn("Night Drive", html)
        self.assertIn("Divorce &lt;v2&gt;", html)           # escaped
        self.assertNotIn("Not rendered yet", html)
        self.assertEqual(html.count('aria-current="true"'), 1)
        self.assertIn("sbeFilmsPick('sb_b')", html)
        self.assertTrue(self.r["noteHidden"])

    def test_picking_another_film_opens_it_and_the_open_one_is_a_no_op(self):
        self.assertEqual(self.r["opened"], ["sb_b"])
        self.assertTrue(self.r["closedAfterPick"])

    def test_it_toggles_and_has_a_way_to_every_film(self):
        self.assertTrue(self.r["toggledShut"])
        self.assertEqual(self.r["askedOnClose"], 1)
        self.assertEqual(self.r["allClosed"], 1)
        self.assertIn('onclick="sbeFilmsAll()"', self.src)

    def test_alone_it_says_so(self):
        hidden, text = self.r["aloneNote"]
        self.assertFalse(hidden)
        self.assertIn("No other", text)

    def test_the_menu_is_one_of_the_popovers_and_its_verbs_are_global(self):
        pops = self.src[self.src.index("const SBE_POPS = ["):]
        self.assertIn("'sbeFilmsMenu'", pops[:pops.index("];")])
        publish = self.src[self.src.index("Object.assign(globalThis, {\n  sbeStripY"):]
        publish = publish[:publish.index("});")]
        for name in ("sbeFilmsToggle", "sbeFilmsPick", "sbeFilmsAll"):
            self.assertRegex(publish, r"\b%s\b" % name)

    def test_leaving_through_it_backs_up_like_close(self):
        # The one door: edOpenBoard -> sbeOpen -> sbeCloseDoc, which backs up
        # a dirty document before it lets go of it.
        self.assertIn("edOpenBoard(id)", extract_function("sbeFilmsPick", self.src))
        self.assertIn("sbeCloseDoc({ quiet: true })",
                      extract_function("sbeOpen", self.src))
        self.assertIn("sbeBackup(true)", extract_function("sbeCloseDoc", self.src))


if __name__ == "__main__":
    unittest.main()
