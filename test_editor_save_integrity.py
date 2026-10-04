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
function sbeNoticeHoles() {}
function sbePaintRenderChip() {}
function sbeOpenFilmScreen() {}
function sbeRevealRender() {}
global.document = { createElement: () => ({ appendChild() {} }) };
""")

    def test_render_waits_for_the_save_in_flight(self):
        self.assertEqual(self.r["urlsWhileSaving"], ["/storyboard/edit/save"])
        # FILM-28: the render is a job now — the door it knocks on once the
        # save lands is /render/start, not the old one-shot /render.
        self.assertEqual(self.r["urlsAfter"],
                         ["/storyboard/edit/save", "/storyboard/edit/render/start"])

    def test_a_save_that_does_not_land_stops_the_render(self):
        self.assertNotIn("/storyboard/edit/render/start", self.r["urlsAfterConflict"])



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
function sbePaintOffline() {}
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
  const f = FETCHES.find(x => x.url === '/storyboard/edit/render/start');
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
function sbePaintOffline() {}
function sbeDeliverPaint() {}
function edPoolRefresh() {}
async function sbeFetchPeaks() {}
function sbeShowFrameAt() {}
function sbeStop() {}
function sbeVersionsClose() {}
function edRemember() {}
function edShowPicker() {}
function sbeNoticeHoles() {}
function sbePaintRenderChip() {}
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


class RenderStaysOnTheTimeline(unittest.TestCase):
    """FILM-24 — a finished render used to switch to Storyboard and open the
    Film screen unconditionally. Driven: the render must not navigate, and
    must paint the "last render" chip instead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
els.sbeMusic = Object.assign(stubEl('sbeMusic'), { value: '' });
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
function mkNode() {
  return { textContent: '', className: '', onclick: null, href: '',
           _kids: [], appendChild(c) { this._kids.push(c); } };
}
global.document = { createElement: () => mkNode() };
els.sbeRenderChip = Object.assign(stubEl('sbeRenderChip', true),
                                  { _kids: [], appendChild(c) { this._kids.push(c); } });
SBE.open = true; SBE.id = 'F'; SBE.activeDraft = 'draft-1'; SBE.session = 's1';
// FILM-28: the render is a job now — /start answers with a job id, and
// sbeRenderPoll (kicked off, not awaited, inside sbeRenderFilm) is what
// actually reaches a terminal state. The fetch mock answers every URL with
// the SAME body regardless of which route asked, so one body that already
// carries both a "job" (what /start reads) and a "done" status (what
// /status reads) lets the very first poll settle immediately — sbeRenderFilm
// itself only awaits the /start leg, so the test drives the rest with tick().
NEXT = { status: 200, body: { ok: true, job: 'j1', state: 'done',
                              result: { ok: true, clips: 2, duration: 12.4,
                                       path: '/out/sb/f_film.mp4',
                                       deliver: { format: 'h264' } } } };
await sbeRenderFilm();
for (let i = 0; i < 8; i++) await tick();
out.switched = switched.slice();
out.chipHidden = els.sbeRenderChip.hidden;
out.chipKids = els.sbeRenderChip._kids.length;
out.toastSaysReady = toasts.some(t => t.indexOf('Film ready') === 0);
out.renderingClearedAfter = SBE.rendering;
""", extra=("sbeRenderFilm", "sbeRenderPoll", "sbeRenderSettle",
            "sbeRenderCancel", "sbeRenderFinish", "sbeOpenFilmScreen",
            "sbePaintRenderChip", "sbeRevealRender", "sbeNoticeHoles",
            "sbeCloseAllGaps", "sbeCloseGapAt"), shim=r"""
const ED = { src: 'gallery' };
function sbeDeliverGet() { return { format: 'h264', size: 'native', finish: 'none' }; }
function sbeMusicMode() { return 'under'; }
global.confirm = () => true;
const SB = { id: '' };
const switched = [];
function workflowSwitch(n) { switched.push(n); }
function sbFilmOpen() { switched.push('sbFilmOpen'); }
async function sbOpen() { switched.push('sbOpen'); }
global.window = { addEventListener() {}, removeEventListener() {} };
""")

    def test_a_finished_render_never_switches_tabs_or_opens_the_film(self):
        self.assertEqual(self.r["switched"], [])

    def test_it_paints_the_last_render_chip_instead(self):
        self.assertFalse(self.r["chipHidden"])
        # a label plus two actions (Open, Show in Finder)
        self.assertEqual(self.r["chipKids"], 3)

    def test_the_toast_still_says_the_film_is_ready(self):
        self.assertTrue(self.r["toastSaysReady"])

    def test_rendering_clears_once_the_job_settles(self):
        # FILM-28: the poll loop must leave SBE.rendering behind it, not
        # stuck true because the job split left some path that forgot to
        # settle.
        self.assertFalse(self.r["renderingClearedAfter"])


class OfflineMediaBlocksTheRender(unittest.TestCase):
    """FILM-15 — an offline clip used to render silently: a shorter,
    out-of-sync film behind a success toast, and a black preview with no
    explanation. Render now refuses, named, before spending an encode.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
SBE.clips[0].id = 'a'; SBE.clips[0].title = 'The bow shot';
els.sbeMusic = Object.assign(stubEl('sbeMusic'), { value: '' });
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
els.sbeOffline = Object.assign(stubEl('sbeOffline', true), {});
els.sbeOfflineText = stubEl('sbeOfflineText');
global.document = { createElement: () => ({ appendChild() {} }) };
SBE.open = true; SBE.id = 'F'; SBE.activeDraft = 'draft-1';
SBE.dirty = false;                 // isolate the offline gate from the save gate
SBE.offline = ['a'];
FETCHES = [];
const before = toasts.length;
await sbeRenderFilm();
out.hitRender = FETCHES.some(f => f.url === '/storyboard/edit/render');
out.toasted = toasts.slice(before);
out.barHidden = els.sbeOffline.hidden;
out.barText = els.sbeOfflineText.textContent;
out.rendering = !!SBE.rendering;
""", extra=("sbeRenderFilm", "sbeClipOffline", "sbePaintOffline",
            "sbeNoticeHoles", "sbeOpenFilmScreen", "sbePaintRenderChip",
            "sbeRevealRender", "sbeNiceName"), shim=r"""
const ED = { src: 'gallery' };
function sbeDeliverGet() { return { format: 'h264', size: 'native', finish: 'none' }; }
function sbeMusicMode() { return 'under'; }
global.confirm = () => true;
const SB = { id: '' };
function workflowSwitch() {}
function sbFilmOpen() {}
async function sbOpen() {}
global.window = { addEventListener() {}, removeEventListener() {} };
""")

    def test_the_render_never_reaches_the_server(self):
        self.assertFalse(self.r["hitRender"])
        self.assertFalse(self.r["rendering"])   # the button never went into flight

    def test_it_names_the_offline_clip_in_a_danger_toast(self):
        self.assertEqual(len(self.r["toasted"]), 1)
        self.assertIn("offline", self.r["toasted"][0])
        self.assertIn("bow shot", self.r["toasted"][0])

    def test_the_offline_bar_is_shown_too(self):
        self.assertFalse(self.r["barHidden"])
        self.assertIn("offline", self.r["barText"])


class ClipOfflineFlag(unittest.TestCase):
    """`sbeClipOffline` — matched by id first, path second (a clip with no
    id yet, e.g. mid-mutation, still has to be findable by what it plays)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
SBE.offline = ['c1', '/x/by-path.mp4'];
out.byId = sbeClipOffline({ id: 'c1', path: '/x/a.mp4' });
out.byPath = sbeClipOffline({ id: 'c9', path: '/x/by-path.mp4' });
out.neither = sbeClipOffline({ id: 'c2', path: '/x/b.mp4' });
out.noPath = sbeClipOffline({ id: 'c1' });
""", extra=("sbeClipOffline",))

    def test_matches_by_id_or_by_path(self):
        self.assertTrue(self.r["byId"])
        self.assertTrue(self.r["byPath"])
        self.assertFalse(self.r["neither"])

    def test_a_slug_with_no_path_is_never_offline(self):
        self.assertFalse(self.r["noPath"])


class PoolAddIgnoresAFilmSwitchMidFlight(unittest.TestCase):
    """FILM-32 — `edPoolAdd` awaits the proxy build (the slow part — real
    footage, sometimes several seconds) and used to mutate `SBE.clips` with
    no check that the film it was building for is still the one open. A
    clip dropped on film A, added after the user switched to film B, landed
    in B instead.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
const _rows = [{ path: '/x/clip.mp4', kind: 'video', title: 'A clip' }];
global.document = {
  getElementById: (id) => id === 'edPoolList' ? { _rows }
    : (id === 'edPoolNote' ? els.edPoolNote : null),
};
els.edPoolNote = stubEl('edPoolNote');
async function tryIt(sameFilm) {
  SBE.open = true; SBE.id = 'A'; SBE.clips = []; SBE.edit = { clips: [] };
  SBE.dirty = false; SBE.dirtyAt = 0; SBE.saveTimer = null; SBE.backingUp = false;
  ED.suppressClick = false;
  FETCHES = []; HOLD = true;
  NEXT = { status: 200, body: { ok: true,
    clip: { path: '/x/clip.mp4', duration_s: 4, n: 1 } } };
  const p = edPoolAdd(0);
  await tick();
  if (!sameFilm) SBE.id = 'B';         // switched while the proxy built
  while (HELD.length) await release();
  await p;
  return SBE.clips.length;
}
out.landedOnSwitchedFilm = await tryIt(false);
out.landedOnSameFilm = await tryIt(true);
""", extra=("edPoolAdd",), shim=r"""
const ED = { src: 'gallery', suppressClick: false };
async function sbeLoad() {}
""")

    def test_the_clip_does_not_land_after_the_film_switched(self):
        self.assertEqual(self.r["landedOnSwitchedFilm"], 0)

    def test_the_clip_still_lands_when_the_film_did_not_change(self):
        self.assertEqual(self.r["landedOnSameFilm"], 1)


class MusicModeSurvivesTheObjectASaveReplaces(unittest.TestCase):
    """FILM-33 — `sbeSetMusicMode` used to mutate `SBE.audio.mode` in place.
    A clean save replaces `SBE.edit` with a fresh object built from the
    server's answer but never touches `SBE.audio`, so after the FIRST save
    the two point at different objects and every mode change after that
    mutates the orphaned one — invisible to `sbeSaveBody`, which reads
    `SBE.edit.audio`. It now goes through `sbeSetAudio` (writes both
    references to the same new object) and `sbeMusicCommit` (undo + queued
    backup), like every other soundtrack gesture.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
els.sbeMusicMode = stubEl('sbeMusicMode');
els.sbeMusicWarn = Object.assign(stubEl('sbeMusicWarn', true), { textContent: '' });
SBE.audio = { path: '/x/song.wav', mode: 'under', offset: 0 };
SBE.edit = { audio: SBE.audio, clips: [] };
SBE.dirty = false; SBE.dirtyAt = 0; SBE.undo = []; SBE.saveTimer = null;
SBE.backingUp = false;
FETCHES = [];
NEXT = { status: 200, body: { ok: true, at: 1, session: {} } };
sbeSetMusicMode('replace');
for (let i = 0; i < 6; i++) await tick();
out.editAudioAfterFirst = SBE.edit.audio.mode;
out.sameObject = SBE.audio === SBE.edit.audio;
out.undoLen = SBE.undo.length;
out.backedUp = FETCHES.some(f => f.url === '/storyboard/edit/backup');

// Simulate what a clean save does: SBE.edit REPLACED, SBE.audio untouched.
SBE.edit = Object.assign({}, { audio: Object.assign({}, SBE.audio) },
                         { clips: SBE.edit.clips || [] });
out.stillSameObjectAfterSave = SBE.audio === SBE.edit.audio;   // expected false

// The second mode change — this is the one that used to vanish.
sbeSetMusicMode('under');
out.editAudioAfterSecond = SBE.edit.audio.mode;
out.audioAfterSecond = SBE.audio.mode;
""", extra=("sbeSetMusicMode", "sbeSetAudio", "sbeMusicCommit"))

    def test_the_first_change_lands_on_both_references(self):
        self.assertEqual(self.r["editAudioAfterFirst"], "replace")
        self.assertTrue(self.r["sameObject"])
        self.assertEqual(self.r["undoLen"], 1)
        self.assertTrue(self.r["backedUp"])

    def test_the_change_after_a_save_still_lands_where_the_save_reads_it(self):
        self.assertFalse(self.r["stillSameObjectAfterSave"])   # the save DID diverge them
        self.assertEqual(self.r["editAudioAfterSecond"], "under")
        self.assertEqual(self.r["audioAfterSecond"], "under")


class QueueSaveWritesTheFirstEditImmediately(unittest.TestCase):
    """FILM-06 — a reload inside the 1.4s debounce used to lose the very
    first edit off a clean timeline with no backup at all. The first edit
    now writes the crash backup immediately; only the ones after it debounce.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
SBE.dirty = false; SBE.dirtyAt = 0; SBE.saveTimer = null; SBE.backingUp = false;
FETCHES = [];
NEXT = { status: 200, body: { ok: true, at: 1, session: {} } };
sbeQueueSave();
for (let i = 0; i < 8; i++) await tick();
out.firstEditTimerArmed = !!SBE.saveTimer;
out.firstEditBackedUp = FETCHES.some(f => f.url === '/storyboard/edit/backup');
// A second edit right after — this one debounces as before.
FETCHES = [];
sbeQueueSave();
out.secondEditTimerArmed = !!SBE.saveTimer;
out.secondEditBackedUpYet = FETCHES.some(f => f.url === '/storyboard/edit/backup');
""")

    def test_the_first_edit_off_clean_is_backed_up_without_waiting(self):
        self.assertTrue(self.r["firstEditBackedUp"])
        self.assertFalse(self.r["firstEditTimerArmed"])

    def test_the_edit_right_after_it_still_debounces(self):
        self.assertTrue(self.r["secondEditTimerArmed"])
        self.assertFalse(self.r["secondEditBackedUpYet"])


class TheWatchdogStopsCallingWhenNothingChanged(unittest.TestCase):
    """FILM-57 — sbeTick's watchdog re-arms sbeQueueSave on every tick the
    document is dirty, not-saving and not-already-timered ("re-queueing is
    free"), which is correct: nothing unsaved may sit one dropped timer away
    from unprotected. What it cannot know is that the timer it just armed
    fires against content that has not changed since the last snapshot — the
    server already dedups by digest (storyboard_editor.py's `write_backup`)
    so no new file was ever written, but the reported bug was the ROUND TRIP
    itself: a fetch every 1.4s forever while the document sat dirty and
    untouched. sbeBackup now compares against its own last successful body
    and skips the network call — without a network call — when nothing
    changed; a real edit still reaches the server exactly as before."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
FETCHES = [];
NEXT = { status: 200, body: { ok: true, session: {} } };
const first = await sbeBackup(true);
out.firstCount = FETCHES.length;
out.firstOk = first;
// The watchdog's own move: re-arm and fire again with NOTHING having
// changed on screen in between.
const second = await sbeBackup(true);
out.secondCount = FETCHES.length;
out.secondOk = second;
// A real edit — a different clip end — is a different body, and reaches
// the server exactly as before.
SBE.clips = lay([clip({ id: 'a', end: 2.5 }), clip({ id: 'b', end: 3 })]);
SBE.edit = { clips: SBE.clips };
const third = await sbeBackup(true);
out.thirdCount = FETCHES.length;
out.thirdOk = third;
""")

    def test_a_repeat_with_nothing_new_never_leaves_the_tab(self):
        self.assertEqual(self.r["firstCount"], 1)
        self.assertTrue(self.r["firstOk"])
        self.assertEqual(self.r["secondCount"], 1)   # unchanged — no new fetch
        self.assertTrue(self.r["secondOk"])           # still reports success

    def test_a_real_edit_still_reaches_the_server(self):
        self.assertEqual(self.r["thirdCount"], 2)
        self.assertTrue(self.r["thirdOk"])


class UnloadBeaconsTheBackup(unittest.TestCase):
    """FILM-06 — nothing told the browser to wait for the debounce before a
    reload or close. `beforeunload`/`pagehide` now fire a `sendBeacon` at the
    same crash-backup door, with the same body `sbeSaveBody` already builds.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
const beacons = [];
Object.defineProperty(globalThis, "navigator", { value: { sendBeacon: (url, blob) => { beacons.push({ url, blob }); return true; } }, configurable: true, writable: true });
SBE.open = true; SBE.id = 'F'; SBE.activeDraft = 'draft-1'; SBE.session = 'sess1';
sbeBeaconBackup();
out.count = beacons.length;
out.url = beacons[0] && beacons[0].url;
out.body = beacons[0] ? JSON.parse(await beacons[0].blob.text()) : null;
// A clean timeline sends nothing — there is nothing to protect.
beacons.length = 0;
SBE.dirty = false;
sbeBeaconBackup();
out.cleanCount = beacons.length;
// Neither does a conflicted one — the server copy is not this tab's to send.
SBE.dirty = true; SBE.conflict = 1;
sbeBeaconBackup();
out.conflictCount = beacons.length;
""", extra=("sbeBeaconBackup",))

    def test_a_dirty_unload_sends_exactly_one_beacon_to_the_backup_door(self):
        self.assertEqual(self.r["count"], 1)
        self.assertEqual(self.r["url"], "/storyboard/edit/backup")

    def test_the_beacon_carries_the_transitions_like_any_other_backup(self):
        tx = self.r["body"]["edit"]["transitions"]
        self.assertEqual([t["after_clip"] for t in tx], ["a"])
        self.assertEqual(self.r["body"]["draft"], "draft-1")
        self.assertEqual(self.r["body"]["session"], "sess1")

    def test_a_clean_or_conflicted_tab_sends_nothing(self):
        self.assertEqual(self.r["cleanCount"], 0)
        self.assertEqual(self.r["conflictCount"], 0)


class SuspendNeverWritesTheDocument(unittest.TestCase):
    """FILM-29 — leaving the tab is not the user pressing Save. Driven,
    because the static gate in test_storyboard_editor_ui.py only reads the
    source; this proves the actual HTTP call that lands.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
SBE.open = true; SBE.id = 'F'; SBE.activeDraft = 'draft-1';
FETCHES = [];
NEXT = { status: 200, body: { ok: true, at: 1, session: {} } };
sbeSuspend();
for (let i = 0; i < 8; i++) await tick();
out.urls = FETCHES.map(f => f.url);
""", extra=("sbeSuspend", "sbeSweepViewToasts"), shim=r"""
function sbeStop() {}
function sbeSrcStop() {}
global.window = { addEventListener() {}, removeEventListener() {} };
global.document = { querySelectorAll: () => [] };
""")

    def test_leaving_the_tab_backs_up_and_never_saves(self):
        self.assertIn("/storyboard/edit/backup", self.r["urls"])
        self.assertNotIn("/storyboard/edit/save", self.r["urls"])


class KeepVersionNamesWhatIsOnScreen(unittest.TestCase):
    """FILM-30 — "Keep this version" named the save already on disk. A
    dirty timeline now saves first, so the label attaches to the arrangement
    the user is actually looking at.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
twoClips();
els.sbeVersName = Object.assign(stubEl('sbeVersName'), { value: 'first cut' });
SBE.open = true; SBE.id = 'F'; SBE.activeDraft = 'draft-1';
FETCHES = [];
NEXT = { status: 200, body: { ok: true, edit: { revision: 2 }, label: 'first cut',
                              revision: 2, versions: [] } };
await sbeKeepVersion();
out.dirtyUrls = FETCHES.map(f => f.url);
// A clean timeline needs no save first.
twoClips();
SBE.dirty = false;
els.sbeVersName = Object.assign(stubEl('sbeVersName'), { value: 'second cut' });
FETCHES = [];
await sbeKeepVersion();
out.cleanUrls = FETCHES.map(f => f.url);
""", extra=("sbeKeepVersion",))

    def test_a_dirty_timeline_saves_before_naming_the_version(self):
        self.assertEqual(self.r["dirtyUrls"],
                         ["/storyboard/edit/save", "/storyboard/edit/version"])

    def test_a_clean_timeline_only_needs_the_name(self):
        self.assertEqual(self.r["cleanUrls"], ["/storyboard/edit/version"])


class ConflictRefusesRatherThanDiscards(unittest.TestCase):
    """FILM-23 — Restore, Retake-use and Relink all guarded with
    `SBE.dirty && !SBE.conflict && !(await sbeSave(true))`: with a conflict
    set, `!SBE.conflict` made the WHOLE condition false, so the save was
    skipped AND the action went ahead, installing the server's copy over an
    arrangement the user was never offered a way to keep. Each now refuses
    outright on a conflict, the way Face Fix already did.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(TWO_CLIPS + r"""
async function tryIt(fn, url) {
  twoClips();
  SBE.conflict = 1;
  FETCHES = [];
  const before = toasts.length;
  adopted.length = 0;
  await fn();
  return { hitAction: FETCHES.some(f => f.url === url),
           adopted: adopted.length,
           toasted: toasts.length > before };
}
out.restore = await tryIt(() => sbeRestoreVersion('save-r00003.json'),
                          '/storyboard/edit/restore');
out.retake = await tryIt(() => sbeRetakeUse('c1', 'delivery'),
                         '/storyboard/edit/relink');
out.relink = await tryIt(() => sbeRelink(), '/storyboard/edit/relink');
""", extra=("sbeRestoreVersion", "sbeRetakeUse", "sbeRelink"))

    def test_restore_refuses_on_a_conflict(self):
        r = self.r["restore"]
        self.assertFalse(r["hitAction"])
        self.assertEqual(r["adopted"], 0)
        self.assertTrue(r["toasted"])

    def test_retake_use_refuses_on_a_conflict(self):
        r = self.r["retake"]
        self.assertFalse(r["hitAction"])
        self.assertEqual(r["adopted"], 0)
        self.assertTrue(r["toasted"])

    def test_relink_refuses_on_a_conflict(self):
        r = self.r["relink"]
        self.assertFalse(r["hitAction"])
        self.assertEqual(r["adopted"], 0)
        self.assertTrue(r["toasted"])


if __name__ == "__main__":
    unittest.main()
