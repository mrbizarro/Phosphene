#!/usr/bin/env python3
"""What the Editor sends, and WHEN it believes it: the save lane, driven.

`test_storyboard_editor_ui.py` locks the arrangement maths and each guard of
the save lane on its own. This file locks the SEAMS between them — the places
the 2026-09-24 review found work going missing while every guard passed:

  * SB5-01  the save and the backup both built their payload without the
            transitions, so every dissolve was discarded on Save.
  * SB5-02  a Save answered late marked edits made DURING it as saved, and
            dropped the second Save the user pressed.
  * SB5-14  a dependent action (Render, NLE export) read `'busy'` as "saved"
            and ran against the previous cut.
  * SB5-04  the save named no draft, so the server could not refuse one
            that landed on a draft another tab had switched to.
  * SB5-03  a load was adopted whenever it answered — another film's, or a
            quiet re-read of the SAVED file over unsaved work.
  * SB5-11  the soundtrack field kept the previous film's song, and Render
            posted it as an override for the next film.

Same method as the UI gate: the REAL functions, extracted and run in node, with
only the paint and the network stubbed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source  # noqa: E402

import test_storyboard_editor_ui as ui  # noqa: E402


# A fetch that can be HELD: a request parks until the test releases it, which
# is the only way to put an edit, or a second Save, inside the window a slow
# save leaves open.
HELD_FETCH = r"""
const HELD = [];
let HOLD = false;
global.fetch = (url, opts) => {
  FETCHES.push({ url, body: opts && opts.body });
  const r = NEXT;
  const answer = { ok: r.status < 400, status: r.status, json: async () => r.body };
  if (!HOLD) return Promise.resolve(answer);
  return new Promise(res => HELD.push(() => res(answer)));
};
const tick = () => new Promise(r => setImmediate(r));
async function release() { const f = HELD.shift(); if (f) f(); for (let i = 0; i < 8; i++) await tick(); }
async function releaseAt(i) { const f = HELD.splice(i, 1)[0]; if (f) f(); for (let k = 0; k < 8; k++) await tick(); }
"""


def run(body: str, extra: tuple = (), shim: str = "") -> dict:
    if ui.NODE is None:
        raise unittest.SkipTest("node not on PATH")
    source = panel_source()
    names = list(ui.FUNCTIONS) + [n for n in extra if n not in ui.FUNCTIONS]
    script = (ui.SHIM + HELD_FETCH + shim
              + "\n".join(extract_function(n, source) for n in names)
              + "\n(async () => {\nconst out = {};\n" + body
              + "\nprocess.stdout.write(JSON.stringify(out));\n})();\n")
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        res = subprocess.run([ui.NODE, str(path)], capture_output=True,
                             text=True, timeout=60)
        if res.returncode:
            raise AssertionError(res.stdout + "\n" + res.stderr)
        if not res.stdout.strip():
            raise AssertionError("the contract never finished (a promise "
                                 "nobody resolved?)\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


# Two clips and a dissolve on the cut between them — the smallest timeline a
# transition can exist on.
TWO_CLIPS = r"""
function twoClips() {
  SBE.clips = lay([clip({ id: 'a', end: 2 }), clip({ id: 'b', end: 3 })]);
  SBE.edit = { clips: SBE.clips };
  SBE.transitions = sbeTxSet([], 'a', 'dissolve', 0.5).transitions;
  SBE.overlays = []; SBE.tracks = [];
  SBE.dirty = true; SBE.dirtyAt = 1; SBE.conflict = 0; SBE.saving = false;
  SBE.savePending = false; SBE.saveFailed = ''; SBE.revision = 1;
  els.sbeAlarm = { hidden: true }; els.sbeAlarmWhy = { textContent: '' };
}
"""


class TransitionsTravel(unittest.TestCase):
    """SB5-01 — a dissolve on screen is a dissolve on disk."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
FETCHES = [];
NEXT = { status: 200, body: { ok: true, edit: { revision: 2, clips: [] } } };
await sbeSave(false);
out.save = JSON.parse(FETCHES[0].body);
twoClips();
FETCHES = [];
NEXT = { status: 200, body: { ok: true } };
await sbeBackup(true);
out.backup = JSON.parse(FETCHES[0].body);
out.live = SBE.transitions;
""")

    def test_the_save_carries_the_transition(self):
        tx = self.r["save"]["edit"]["transitions"]
        self.assertEqual(len(tx), 1)
        self.assertEqual((tx[0]["after_clip"], tx[0]["kind"], tx[0]["duration"]),
                         ("a", "dissolve", 0.5))

    def test_the_crash_backup_carries_the_transition(self):
        tx = self.r["backup"]["edit"]["transitions"]
        self.assertEqual([t["after_clip"] for t in tx], ["a"])

    def test_the_saved_document_keeps_it_through_the_server_model(self):
        # The payload, as the server's own model reads it back: the boundary
        # must still resolve between the two clips it was put on.
        import storyboard_editor as sedit                     # noqa: PLC0415
        doc = sedit.normalise_edit(self.r["save"]["edit"])
        self.assertEqual([t["after_clip"] for t in sedit.transition_items(doc)],
                         ["a"])


class SaveThatLandsLate(unittest.TestCase):
    """SB5-02 — a response describes the arrangement it was SENT with."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
FETCHES = []; HOLD = true;
NEXT = { status: 200, body: { ok: true, edit: { revision: 2, clips: [] } } };
const first = sbeSave(false);
await tick();
// The user trims while the first Save is on the wire...
sbeMutate(cs => sbeTrim(cs, 'b', 'r', 4.5));
const liveEnd = sbeById(SBE.clips, 'b').film_end;
// ...and presses Save again.
const second = sbeSave(false);
await tick();
out.requestsBeforeRelease = FETCHES.length;
NEXT = { status: 200, body: { ok: true, edit: { revision: 3, clips: [] } } };
await release();                 // the first answer lands
out.dirtyAfterFirst = SBE.dirty;
out.stateAfterFirst = (states[states.length - 1] || [])[0];
out.requestsAfterFirst = FETCHES.length;
while (HELD.length) await release();
const results = [await first, await second];
out.results = results;
const sent = FETCHES.map(f => JSON.parse(f.body));
out.sentEnds = sent.map(b => (b.edit.clips.find(c => c.id === 'b') || {}).film_end);
out.sentExpect = sent.map(b => b.expect_revision);
out.liveEnd = liveEnd;
out.dirtyAtEnd = SBE.dirty;
out.revisionAtEnd = SBE.revision;
""")

    def test_the_late_answer_does_not_mark_newer_work_saved(self):
        self.assertEqual(self.r["requestsBeforeRelease"], 1)
        self.assertTrue(self.r["dirtyAfterFirst"])
        self.assertNotRegex(self.r["stateAfterFirst"] or "", r"^saved")

    def test_the_second_save_is_sent_with_the_trim(self):
        self.assertEqual(len(self.r["sentEnds"]), 2)
        self.assertNotEqual(self.r["sentEnds"][0], self.r["liveEnd"])
        self.assertEqual(self.r["sentEnds"][1], self.r["liveEnd"])
        # ...against the revision the first one produced, not the stale one.
        self.assertEqual(self.r["sentExpect"], [1, 2])

    def test_both_presses_are_answered_by_the_save_that_carried_them(self):
        self.assertEqual(self.r["results"], [True, True])
        self.assertFalse(self.r["dirtyAtEnd"])
        self.assertEqual(self.r["revisionAtEnd"], 3)


class DependentActionsWaitForTheSave(unittest.TestCase):
    """SB5-14 — `'busy'` is not `true`: Render must not outrun its Save."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
// ---- a save in flight, then Render ----------------------------------------
twoClips();
SBE.rendering = false;
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
els.sbeMusic = Object.assign(stubEl('sbeMusic'), { value: '' });
FETCHES = []; HOLD = true;
NEXT = { status: 200, body: { ok: true, edit: { revision: 2, clips: [] } } };
const saving = sbeSave(false);
await tick();
const rendering = sbeRenderFilm();
for (let i = 0; i < 6; i++) await tick();
out.urlsWhileSaving = FETCHES.map(f => f.url);
NEXT = { status: 200, body: { ok: false, error: 'stop here' } };
while (HELD.length) await release();
await saving; await rendering;
out.urlsAfter = FETCHES.map(f => f.url);

// ---- a save in flight that FAILS: no render at all ------------------------
twoClips();
FETCHES = []; HOLD = true;
NEXT = { status: 409, body: { ok: false, conflict: true, revision: 7 } };
const s2 = sbeSave(false);
await tick();
const r2 = sbeRenderFilm();
while (HELD.length) await release();
await s2; await r2;
for (let i = 0; i < 6; i++) await tick();
while (HELD.length) await release();
out.urlsAfterConflict = FETCHES.map(f => f.url);
""", extra=("sbeRenderFilm",),
            shim=r"""
function sbeDeliverGet() { return { format: 'h264', size: 'native', finish: 'none' }; }
function sbeMusicMode() { return 'under'; }
global.confirm = () => true;
const SB = { id: '' };
function workflowSwitch() {}
function sbFilmOpen() {}
""")

    def test_render_waits_for_the_save_in_flight(self):
        self.assertEqual(self.r["urlsWhileSaving"], ["/storyboard/edit/save"])
        self.assertEqual(self.r["urlsAfter"],
                         ["/storyboard/edit/save", "/storyboard/edit/render"])

    def test_a_save_that_does_not_land_stops_the_render(self):
        self.assertNotIn("/storyboard/edit/render", self.r["urlsAfterConflict"])



class SaveNamesItsDraft(unittest.TestCase):
    """SB5-04 — the client half: the save says which draft it was cut in."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
SBE.activeDraft = 'draft-1';
FETCHES = [];
NEXT = { status: 409, body: { ok: false, conflict: true, draft_conflict: true,
                              active_draft: 'take-two', revision: 1 } };
out.ok = await sbeSave(false);
out.draft = JSON.parse(FETCHES[0].body).draft;
out.text = els.sbeConflictText.textContent;
out.conflict = SBE.conflict;
out.dirty = SBE.dirty;
""")

    def test_the_body_names_the_draft(self):
        self.assertEqual(self.r["draft"], "draft-1")

    def test_a_draft_conflict_says_which_draft_won_and_keeps_the_work(self):
        self.assertFalse(self.r["ok"])
        self.assertIn("take-two", self.r["text"])
        self.assertIn("draft-1", self.r["text"])
        self.assertTrue(self.r["conflict"])
        self.assertTrue(self.r["dirty"])



class LoadsBelongToTheirFilm(unittest.TestCase):
    """SB5-03 — a response is adopted only by the film and load that asked."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
// ---- open A, switch to B, B answers first --------------------------------
SBE.open = true; SBE.id = 'A'; SBE.dirty = false; SBE.saving = false;
SBE.conflict = 0; SBE.session = 's1';
FETCHES = []; HOLD = true; adopted.length = 0;
NEXT = { status: 200, body: { ok: true, title: 'film A', edit: { revision: 4, clips: [] } } };
const la = sbeLoad();
await tick();
SBE.id = 'B';
NEXT = { status: 200, body: { ok: true, title: 'film B', edit: { revision: 1, clips: [] } } };
const lb = sbeLoad();
await tick();
out.asked = FETCHES.map(f => f.url);
await releaseAt(1);
await releaseAt(0);
await la; await lb;
out.adopted = adopted.map(r => r.title);

// ---- Prepare finishes while there is unsaved work ------------------------
HOLD = false; adopted.length = 0;
SBE.id = 'B';
twoClips();
sbeMutate(cs => sbeTrim(cs, 'b', 'r', 4.5));
const shapeBefore = JSON.stringify(shape(SBE.clips));
const undoBefore = SBE.undo.length;
NEXT = { status: 200, body: { ok: true, title: 'film B',
  edit: { revision: 1, clips: [clip({ id: 'a', proxy: '/p/a.mp4' })] },
  clips: [], unplaced: [{ path: '/o/new.mp4' }], relink: [],
  prepare: { state: 'done' } } };
await sbeLoad(true);
out.prepAdopted = adopted.length;
out.prepShapeKept = JSON.stringify(shape(SBE.clips)) === shapeBefore;
out.prepUndoKept = SBE.undo.length === undoBefore && undoBefore > 0;
out.prepDirty = SBE.dirty;
out.prepProxy = sbeById(SBE.clips, 'a').proxy;
out.prepUnplaced = (SBE.unplaced || []).length;
out.prepState = (SBE.prepare || {}).state;
out.prepTransitions = (SBE.transitions || []).length;

// ---- ...and with nothing unsaved, the same re-read IS adopted -------------
SBE.dirty = false;
await sbeLoad(true);
out.cleanAdopted = adopted.length;
""", extra=("sbeLoad", "sbeAdoptMeta"), shim=r"""
const ED = { src: 'gallery' };
function sbePaintRelink() {}
function edPoolRefresh() {}
async function sbeFetchPeaks() {}
function sbeShowDocError() {}
""")

    def test_a_late_answer_for_the_film_you_left_is_dropped(self):
        self.assertEqual(len(self.r["asked"]), 2)
        self.assertIn("id=A", self.r["asked"][0])
        self.assertIn("id=B", self.r["asked"][1])
        self.assertEqual(self.r["adopted"], ["film B"])

    def test_prepare_finishing_does_not_replace_unsaved_work(self):
        self.assertEqual(self.r["prepAdopted"], 0)
        self.assertTrue(self.r["prepShapeKept"])
        self.assertTrue(self.r["prepUndoKept"])
        self.assertTrue(self.r["prepDirty"])
        self.assertEqual(self.r["prepTransitions"], 1)

    def test_prepare_finishing_still_brings_its_proxies_and_shots(self):
        self.assertEqual(self.r["prepProxy"], "/p/a.mp4")
        self.assertEqual(self.r["prepUnplaced"], 1)
        self.assertEqual(self.r["prepState"], "done")

    def test_a_clean_timeline_still_takes_the_re_read(self):
        self.assertEqual(self.r["cleanAdopted"], 1)



class TheSoundtrackFieldFollowsTheFilm(unittest.TestCase):
    """SB5-11 — film B renders with film B's song, or with none."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
els.sbeMusic = Object.assign(stubEl('sbeMusic'), { value: '' });
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
SBE.session = 's1';
const payload = (title, song) => ({ ok: true, title: title, edit: {
  revision: 1, audio: song ? { path: song, offset: 0 } : null,
  clips: [clip({ id: 'a' })] } });
async function renderMusic() {
  FETCHES = []; SBE.dirty = false; SBE.rendering = false;
  NEXT = { status: 200, body: { ok: false, error: 'stop here' } };
  await sbeRenderFilm();
  const f = FETCHES.find(x => x.url === '/storyboard/edit/render');
  return f ? (f.body.get('music') || '') : null;
}
// Film A, with song A.
SBE.open = true; SBE.id = 'A'; SBE.activeDraft = 'draft-1';
sbeAdopt(payload('A', '/songs/song-A.wav'), true);
out.fieldA = els.sbeMusic.value;
out.renderA = await renderMusic();
// Close it, open film B with song B.
sbeCloseDoc({ quiet: true });
out.fieldClosed = els.sbeMusic.value;
SBE.open = true; SBE.id = 'B';
sbeAdopt(payload('B', '/songs/song-B.wav'), true);
out.fieldB = els.sbeMusic.value;
out.renderB = await renderMusic();
// ...and a silent film C straight after B, without closing first.
SBE.id = 'C';
sbeAdopt(payload('C', null), true);
out.fieldC = els.sbeMusic.value;
out.renderC = await renderMusic();
// A re-read of the SAME film keeps what the user typed into the field.
els.sbeMusic.value = '/songs/typed.wav';
sbeAdopt(payload('C', null), true);
out.typedKept = els.sbeMusic.value;
""", extra=("sbeAdopt", "sbeCloseDoc", "sbeRenderFilm", "sbeTsCopy",
            "sbePaintMusicName"), shim=r"""
const ED = { src: 'gallery' };
function sbeDeliverGet() { return { format: 'h264', size: 'native', finish: 'none' }; }
function sbeMusicMode() { return 'under'; }
global.confirm = () => true;
const SB = { id: '' };
function workflowSwitch() {}
function sbFilmOpen() {}
function sbeSetMusicMode() {}
function sbeSyncMusic() {}
function sbePaintRelink() {}
function sbeDeliverPaint() {}
function edPoolRefresh() {}
async function sbeFetchPeaks() {}
function sbeShowFrameAt() {}
function sbeStop() {}
function sbeVersionsClose() {}
function edRemember() {}
function edShowPicker() {}
global.window = { addEventListener() {}, removeEventListener() {} };
""")

    def test_each_film_opens_with_its_own_song(self):
        self.assertEqual(self.r["fieldA"], "/songs/song-A.wav")
        self.assertEqual(self.r["fieldB"], "/songs/song-B.wav")

    def test_render_posts_the_open_films_song_never_the_last_ones(self):
        self.assertEqual(self.r["renderA"], "/songs/song-A.wav")
        self.assertEqual(self.r["renderB"], "/songs/song-B.wav")

    def test_a_silent_film_renders_with_no_soundtrack_override(self):
        self.assertEqual(self.r["fieldClosed"], "")
        self.assertEqual(self.r["fieldC"], "")
        self.assertEqual(self.r["renderC"], "")

    def test_a_re_read_of_the_same_film_keeps_a_typed_path(self):
        self.assertEqual(self.r["typedKept"], "/songs/typed.wav")


if __name__ == "__main__":
    unittest.main()
