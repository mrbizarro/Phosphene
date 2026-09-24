#!/usr/bin/env python3
"""The Editor's server under two writers: who is allowed to land last.

`test_storyboard_editor_api.py` locks each route on its own. These are the
interleavings the 2026-09-24 review found, each driven for real against a
scratch board (the `EditorCase` sandbox — never the live state dir):

  * SB5-07  the first GET's auto-edit wrote over a timeline saved by hand
            while it was still analysing the clips.
  * SB5-04  revisions restart per draft, so a stale tab on draft A saved
            into a draft B another tab had just duplicated, with no conflict.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as panel                                        # noqa: E402
import storyboard_editor as sedit                                    # noqa: E402
from test_storyboard_editor_api import (EditorCase, FakeHandler,     # noqa: E402
                                        _clip, _edit)


class FirstGetLosesToAManualSave(EditorCase):
    """SB5-07 — the machine's cut is a first draft, never a last word."""

    def _auto(self):
        return sedit.edit_from_plan(
            [{"path": str(self.clips[1]), "start": 0.0, "end": 2.0,
              "film_start": 0.0}], board_id="sb_t")

    def test_a_save_made_during_the_first_auto_edit_survives_it(self):
        manual = _edit([_clip(str(self.clips[0]), 0.0, 1.0, 0.0)],
                       source="human")

        def slow_auto(board, **kw):
            # Another tab finished its own first load, cut by hand and saved
            # while this GET was still decoding clips.
            sedit.save_edit(self.bdir, manual, expect=0)
            return self._auto()

        with mock.patch.object(panel, "_sbe_auto_edit", slow_auto):
            h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertEqual(h.status, 200)
        on_disk = sedit.load_edit(self.bdir)
        self.assertEqual([c["path"] for c in on_disk["clips"]],
                         [str(self.clips[0])])
        self.assertEqual(on_disk["revision"], 1)
        # ...and the late GET answers with the document that won, not with
        # the cut it failed to write.
        self.assertEqual([c["path"] for c in h.payload["edit"]["clips"]],
                         [str(self.clips[0])])
        self.assertFalse(h.payload["generated"])

    def test_an_uncontested_first_get_still_writes_the_auto_edit(self):
        with mock.patch.object(panel, "_sbe_auto_edit",
                               lambda board, **kw: self._auto()):
            h = FakeHandler().get("/storyboard/edit?id=sb_t")
        self.assertTrue(h.payload["generated"])
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)
        self.assertEqual(sedit.load_edit(self.bdir)["origin"], "auto")



class SaveNamesItsDraft(EditorCase):
    """SB5-04 — a revision number is not a document identity."""

    def _two_tabs(self):
        a = _edit([_clip(str(self.clips[0]), 0.0, 1.0, 0.0)], source="human")
        sedit.save_edit(self.bdir, a)                           # draft-1 @ r1
        # Tab 2 duplicates A; the copy becomes active, also at revision 1.
        out = sedit.duplicate_draft(self.bdir, "draft-1", "B")
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 1)
        return out["slug"], sedit.load_edit(self.bdir)

    def test_a_stale_tab_gets_a_draft_conflict_not_a_write(self):
        b_slug, before = self._two_tabs()
        # Tab 1 still believes it is on draft-1 at revision 1.
        body = json.dumps({"id": "sb_t", "expect_revision": 1,
                           "draft": "draft-1",
                           "edit": _edit([_clip(str(self.clips[1]),
                                                0.0, 2.0, 0.0)],
                                         source="human")})
        h = FakeHandler().post("edit/save", {}, body)
        self.assertEqual(h.status, 409)
        self.assertTrue(h.payload["conflict"])
        self.assertTrue(h.payload["draft_conflict"])
        self.assertEqual(h.payload["active_draft"], b_slug)
        self.assertEqual(sedit.load_edit(self.bdir)["clips"], before["clips"])

    def test_the_guard_holds_at_the_write_too(self):
        # The early answer can be raced; the check inside the lock cannot.
        b_slug, before = self._two_tabs()
        with self.assertRaises(sedit.EditConflict) as cm:
            sedit.save_edit(self.bdir,
                            _edit([_clip(str(self.clips[1]), 0.0, 2.0, 0.0)]),
                            expect=1, expect_draft="draft-1")
        self.assertEqual(cm.exception.draft, b_slug)
        self.assertEqual(sedit.load_edit(self.bdir)["clips"], before["clips"])

    def test_the_right_draft_still_saves(self):
        b_slug, _ = self._two_tabs()
        body = json.dumps({"id": "sb_t", "expect_revision": 1, "draft": b_slug,
                           "edit": _edit([_clip(str(self.clips[1]),
                                                0.0, 2.0, 0.0)])})
        h = FakeHandler().post("edit/save", {}, body)
        self.assertTrue(h.payload["ok"])
        self.assertEqual(sedit.load_edit(self.bdir)["revision"], 2)

    def test_a_client_without_the_field_is_unchecked_as_before(self):
        self._two_tabs()
        body = json.dumps({"id": "sb_t", "expect_revision": 1,
                           "edit": _edit([_clip(str(self.clips[1]),
                                                0.0, 2.0, 0.0)])})
        self.assertTrue(FakeHandler().post("edit/save", {}, body).payload["ok"])

    def test_every_draft_verb_runs_inside_the_board_lock(self):
        # A switch between a save's check and its write is the whole race;
        # the verbs take the same lock the save holds.
        self._two_tabs()
        seen = []
        real = sedit._save_draft_index

        def spy(board_dir, idx):
            seen.append(sedit.board_write_lock(board_dir)._is_owned())
            return real(board_dir, idx)

        with mock.patch.object(sedit, "_save_draft_index", spy):
            c = sedit.create_draft(self.bdir, "C")
            sedit.rename_draft(self.bdir, c["slug"], "C2")
            sedit.activate_draft(self.bdir, "draft-1")
            sedit.duplicate_draft(self.bdir, "draft-1", "D")
            sedit.delete_draft(self.bdir, c["slug"])
        self.assertTrue(seen)
        self.assertTrue(all(seen), seen)


if __name__ == "__main__":
    unittest.main()
