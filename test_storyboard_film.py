#!/usr/bin/env python3
"""The finished film — the folder it lands in, and the screen that shows it.

Both assemblers (Export's whole-clip join and the timeline's render) wrote an
mp4 into `mlx_outputs/storyboards/<date>_<slug>/` and then told nobody: the
gallery globs `OUTPUT/*.mp4` and never descends, so the one thing the feature
makes was the one thing it could not show you. The owner's words: *"it
shouldn't be so hard to see the finalized clip instead of seeing these clips
all around. Where is the finalized clip?"*

Two halves are locked here, the same way the rest of the storyboard suite locks
its two halves.

**Server.** One function owns the folder name, so the writer and the reader can
never disagree about where a film is. A film is anything in that folder that is
not a `S07_*.mp4` shot copy. The assembler writes down what it already knows,
so the board list can say "0:28" without shelling out to ffprobe twice a
second.

**Client.** The rail — the four steps a film goes through — decides what is
reachable, and it is the answer to the other complaint: *"how do I get to the
editor? It doesn't even have a button."* Its model is pure and runs in node
here, as does `sbOpen`'s promise: the row's Edit button used to open the
timeline and then have it torn straight back down, because `sbOpen` swallowed
the load it was supposed to return.

Run:  python3 -m unittest test_storyboard_film
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function  # noqa: E402
import mlx_ltx_panel as panel  # noqa: E402

NODE = shutil.which("node")


def _board(**kw) -> dict:
    b = {"schema": 1, "id": "sb_film_test", "title": "The Last Dawn",
         "created_at": 1_700_000_000, "cast": [], "shots": []}
    b.update(kw)
    return b


# =============================================================================
# The folder — one name, one owner
# =============================================================================
class FilmDir(unittest.TestCase):

    def test_the_folder_is_date_and_slug_under_the_outputs_root(self):
        d = panel._sb_film_dir(_board())
        self.assertEqual(d.parent, panel.OUTPUT / "storyboards")
        # ...and the board: date + title alone was shared by every board made
        # the same day under the same name (SB5-05). A board whose old
        # date+title folder is already on disk keeps it — see
        # test_storyboard_film_integrity.py.
        self.assertTrue(d.name.endswith("_the-last-dawn_sb-film-test"), d.name)
        day = time.strftime("%Y-%m-%d", time.localtime(1_700_000_000))
        self.assertTrue(d.name.startswith(day), d.name)

    def test_an_untitled_board_still_gets_a_folder(self):
        self.assertTrue(str(panel._sb_film_dir(_board(title=""))).endswith(
            "_storyboard_sb-film-test"))

    def test_the_export_writes_into_exactly_that_folder(self):
        # The whole point of the helper: the writer and the reader agree by
        # construction, not by two copies of the same six lines.
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(panel, "OUTPUT", Path(tmp)):
                b = _board()
                res = panel._sb_export(b)
                self.assertEqual(Path(res["dir"]), panel._sb_film_dir(b))

    def test_a_path_inside_the_install_is_shown_relative(self):
        shown = panel._sb_display_path(panel.OUTPUT / "storyboards" / "x")
        self.assertFalse(Path(shown).is_absolute(), shown)
        self.assertIn("storyboards", shown)

    def test_a_path_outside_everything_is_shown_whole_rather_than_wrong(self):
        self.assertEqual(panel._sb_display_path(Path("/nowhere/at/all")),
                         "/nowhere/at/all")


# =============================================================================
# What counts as a film
# =============================================================================
class Films(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)
        self.board = _board()
        p = mock.patch.object(panel, "OUTPUT", self.out)
        p.start()
        self.addCleanup(p.stop)
        self.dir = panel._sb_film_dir(self.board)
        self.dir.mkdir(parents=True)
        panel._SB_FILM_FACTS.clear()

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, name, *, at=None, sidecar=None, size=1024):
        p = self.dir / name
        p.write_bytes(b"\0" * size)
        if at:
            import os
            os.utime(p, (at, at))
        if sidecar is not None:
            (self.dir / (name + ".json")).write_text(
                json.dumps({"film": sidecar}), encoding="utf-8")
        return p

    def test_a_shot_copy_is_not_a_film(self):
        self._write("S01_cold-open.mp4")
        self._write("S12_the-empty-gym.mp4")
        self.assertEqual(panel._sb_films(self.board, probe=False), [])

    def test_a_legacy_timeline_render_is_still_named_as_one(self):
        # There is ONE assembler now, and one name — `<slug>_film.mp4` from
        # either door. `_timeline.mp4` only exists on installs that rendered
        # before the merge, and the screen should not rewrite their history.
        self._write("the-last-dawn_film.mp4", at=1000, sidecar={"duration": 30})
        self._write("the-last-dawn_timeline.mp4", at=2000, sidecar={"duration": 20})
        films = panel._sb_films(self.board, probe=False)
        self.assertEqual([f["name"] for f in films],
                         ["the-last-dawn_timeline.mp4", "the-last-dawn_film.mp4"])
        self.assertEqual([f["kind"] for f in films], ["timeline", "film"])

    def test_films_are_newest_first(self):
        self._write("a_film.mp4", at=1000, sidecar={"duration": 1})
        self._write("b_timeline.mp4", at=5000, sidecar={"duration": 1})
        self._write("c_cut.mp4", at=3000, sidecar={"duration": 1})
        self.assertEqual([f["name"] for f in panel._sb_films(self.board, probe=False)],
                         ["b_timeline.mp4", "c_cut.mp4", "a_film.mp4"])

    def test_the_url_is_the_panels_own_file_route(self):
        p = self._write("x_film.mp4", sidecar={"duration": 3})
        row = panel._sb_films(self.board, probe=False)[0]
        self.assertTrue(row["url"].startswith("/file?path="))
        self.assertIn(p.name, row["url"])

    def test_the_sidecar_answers_without_an_ffprobe(self):
        self._write("x_film.mp4", sidecar={"duration": 27.875, "width": 1280,
                                           "height": 720, "clips": 6})
        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=AssertionError("must not probe")):
            row = panel._sb_films(self.board, probe=False)[0]
        self.assertEqual(row["duration"], 27.875)
        self.assertEqual(row["width"], 1280)
        self.assertEqual(row["clips"], 6)

    def test_the_polling_path_never_shells_out(self):
        # The board list asks "does this board have a film" every two seconds.
        # A list that shells out is a list that stutters.
        self._write("x_film.mp4")                       # no sidecar at all
        with mock.patch.object(panel, "_sb_probe_clip",
                               side_effect=AssertionError("must not probe")):
            row = panel._sb_films(self.board, probe=False)[0]
        self.assertIsNone(row["duration"])

    def test_a_film_with_no_sidecar_is_probed_once_and_written_down(self):
        self._write("old_film.mp4")
        calls = []

        def fake(p):
            calls.append(p)
            return {"w": 640, "h": 360, "duration": 12.5,
                    "has_audio": True, "sample_rate": 48000}

        with mock.patch.object(panel, "_sb_probe_clip", side_effect=fake):
            first = panel._sb_films(self.board)[0]
            second = panel._sb_films(self.board)[0]
        self.assertEqual(first["duration"], 12.5)
        self.assertEqual(second["duration"], 12.5)
        self.assertEqual(len(calls), 1, "probed twice for the same bytes")
        # And it survives a restart, because it is on disk now.
        self.assertTrue((self.dir / "old_film.mp4.json").is_file())

    def test_a_missing_folder_is_no_films_not_an_error(self):
        shutil.rmtree(self.dir)
        self.assertEqual(panel._sb_films(self.board), [])

    def test_the_summary_is_the_newest_film_and_how_many_there_are(self):
        self._write("a_film.mp4", at=1000, sidecar={"duration": 9})
        self._write("b_timeline.mp4", at=9000, sidecar={"duration": 25})
        s = panel._sb_film_summary(self.board)
        self.assertEqual(s["name"], "b_timeline.mp4")
        self.assertEqual(s["duration"], 25)
        self.assertEqual(s["count"], 2)

    def test_a_board_with_no_film_summarises_to_nothing(self):
        self.assertIsNone(panel._sb_film_summary(self.board))

    def test_the_board_summary_carries_the_film_so_the_row_can_say_so(self):
        self._write("b_timeline.mp4", at=9000, sidecar={"duration": 25})
        row = panel._sb_board_summary(self.board)
        self.assertIn("film", row)
        self.assertEqual(row["film"]["name"], "b_timeline.mp4")


# =============================================================================
# The assembler writes down what it knows
# =============================================================================
class Sidecar(unittest.TestCase):

    def test_a_successful_assembly_leaves_its_facts_beside_the_film(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x_film.mp4"

            def fake_ffmpeg(cmd, *a, **kw):
                # The encoder writes its OWN last argument — a hidden sibling
                # that replaces the film only once it is whole (SB5-06).
                Path(cmd[-1]).write_bytes(b"\0" * 4096)

            probe = {"w": 1024, "h": 576, "duration": 5.0,
                     "has_audio": True, "sample_rate": 48000}
            with mock.patch.object(panel, "_sb_probe_clip", return_value=probe), \
                 mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=fake_ffmpeg), \
                 mock.patch.object(panel, "output_codec_settings",
                                   return_value={"pix_fmt": "yuv420p", "crf": "23"}):
                res = panel._sb_assemble_film([Path(tmp) / "a.mp4",
                                               Path(tmp) / "b.mp4"], out)
            self.assertTrue(res["ok"], res.get("error"))
            side = json.loads((Path(tmp) / "x_film.mp4.json").read_text())
            self.assertEqual(side["film"]["clips"], 2)
            self.assertEqual(side["film"]["width"], 1024)
            self.assertAlmostEqual(side["film"]["duration"], 10.0, places=3)

    def test_a_failed_assembly_writes_no_sidecar(self):
        # A sidecar for a film that does not exist would make the board list
        # advertise a film nobody can play.
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x_film.mp4"
            with mock.patch.object(panel, "_sb_probe_clip", return_value=None):
                res = panel._sb_assemble_film([Path(tmp) / "a.mp4"], out)
            self.assertFalse(res["ok"])
            self.assertFalse((Path(tmp) / "x_film.mp4.json").exists())


# =============================================================================
# The client: the rail, the row, and the promise that broke the Edit button
# =============================================================================
FUNCTIONS = ("sbFmtClock", "sbFmtBytes", "sbFmtAgo", "sbFilmKind", "sbFilmPick",
             "sbRailModel", "sbBoardChip", "sbRowAction", "sbOpen", "sbOpenAt",
             "sbGo", "edOpenBoard")

SHIM = r"""
'use strict';
const ORDER = [];
const SB = { id: '', films: [], filmsFor: '', filmOpen: '', stage: '' };
let SBE = { open: false };
function escapeHtml(s) { return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;'); }
const localStorage = { setItem() {}, getItem() { return ''; }, removeItem() {} };
// The four things sbGo can reach for. Stubs, because what is under test is
// which one it picks — not what they do.
function sbeClose() { ORDER.push('sbeClose'); SBE.open = false; }
function sbeOpen(id) { ORDER.push('sbeOpen'); SBE.open = true; SBE.id = id || SB.id; }
// The Editor is a TAB now. Step 3 switches to it carrying the film, so the
// door under test is a workflow switch plus an open — not a stage swap.
function workflowSwitch(n) { ORDER.push('workflowSwitch:' + n); }
function sbOpenReplan() { ORDER.push('sbOpenReplan'); }
function sbFilmOpen() { ORDER.push('sbFilmOpen'); }
function sbShow(w) { ORDER.push('sbShow:' + w); SB.stage = w; }
function sbRenderPlan() { ORDER.push('sbRenderPlan'); }
let LOAD_DELAY = 0;
async function sbLoad(id) {
  await new Promise(r => setTimeout(r, LOAD_DELAY));
  ORDER.push('sbLoad:' + id);
}
__FUNCTIONS__
async function main() {
  const out = {};

  // ---- formatting ----
  out.clock = [sbFmtClock(0), sbFmtClock(27.875), sbFmtClock(65), sbFmtClock(600)];
  out.bytes = [sbFmtBytes(0), sbFmtBytes(2048), sbFmtBytes(18179743),
               sbFmtBytes(3 * 1073741824)];
  const NOW = 1700000000000;
  out.ago = [sbFmtAgo(0, NOW), sbFmtAgo(NOW / 1000 - 10, NOW),
             sbFmtAgo(NOW / 1000 - 600, NOW), sbFmtAgo(NOW / 1000 - 7200, NOW),
             sbFmtAgo(NOW / 1000 - 172800, NOW)];
  out.kinds = [sbFilmKind({ kind: 'timeline' }), sbFilmKind({ kind: 'export' }),
               sbFilmKind({ kind: 'film' }), sbFilmKind(null)];

  // ---- which film is on screen ----
  const films = [{ name: 'b.mp4', at: 2 }, { name: 'a.mp4', at: 1 }];
  out.pickNewest = sbFilmPick(films, '').name;
  out.pickNamed = sbFilmPick(films, 'a.mp4').name;
  out.pickMissing = sbFilmPick(films, 'gone.mp4').name;
  out.pickEmpty = sbFilmPick([], 'a.mp4');

  // ---- the rail ----
  out.railNoClips = sbRailModel({ clips: 0, shots: 6, film: null, stage: 'plan' })
    .map(s => [s.key, s.state]);
  out.railClips = sbRailModel({ clips: 6, shots: 6, film: null, stage: '' })
    .map(s => [s.key, s.state]);
  out.railTimeline = sbRailModel({ clips: 6, shots: 6, film: null, stage: 'editor' })
    .map(s => [s.key, s.state]);
  out.railFilm = sbRailModel({ clips: 6, shots: 6, film: { duration: 27.875 },
                               stage: 'film' });
  out.railLockedHint = sbRailModel({ clips: 0, shots: 6, film: null, stage: 'plan' })
    .filter(s => s.key === 'edit')[0].hint;
  out.railShotsSub = [
    sbRailModel({ clips: 0, shots: 6, film: null, stage: 'plan' })
      .filter(s => s.key === 'shots')[0].sub,
    sbRailModel({ clips: 4, shots: 6, film: null, stage: 'plan' })
      .filter(s => s.key === 'shots')[0].sub];

  // ---- the board row ----
  out.chips = [
    sbBoardChip({ planning: true, shots: 6, done: 0, clips: 0 }),
    sbBoardChip({ running: true, shots: 6, done: 2, clips: 2 }),
    sbBoardChip({ shots: 6, done: 0, clips: 0 }),
    sbBoardChip({ shots: 7, done: 0, clips: 7 }),
    sbBoardChip({ shots: 6, done: 6, clips: 6 }),
    sbBoardChip({ shots: 6, done: 3, clips: 3 }),
    sbBoardChip({ shots: 6, done: 1, clips: 1, failed: 2 })];
  out.rowNothing = sbRowAction({ id: 'x', shots: 6, done: 0, clips: 0 });
  out.rowClips = sbRowAction({ id: 'x', shots: 6, done: 0, clips: 7 });
  out.rowFilm = sbRowAction({ id: 'x', shots: 6, done: 6, clips: 6,
                              film: { name: 'x.mp4', duration: 27.875 } });

  // ---- the promise the Edit button needed ----
  ORDER.length = 0;
  LOAD_DELAY = 20;
  SB.id = '';
  await sbOpenAt('sb_demo', 'edit');
  out.openAtOrder = ORDER.slice();

  // ---- every door ----
  ORDER.length = 0;
  SB.id = 'sb_demo'; SBE.open = false; SB.stage = 'plan';
  sbGo('edit'); sbGo('film');
  SBE.open = true; sbGo('shots');
  SBE.open = false; sbGo('plan');
  out.doors = ORDER.slice();
  ORDER.length = 0;
  SB.id = '';
  sbGo('edit');
  out.doorsWithNoBoard = ORDER.slice();

  process.stdout.write(JSON.stringify(out));
}
main();
"""


def run_client() -> dict:
    if NODE is None:
        raise unittest.SkipTest("node not on PATH")
    body = "\n".join(extract_function(n) for n in FUNCTIONS)
    script = SHIM.replace("__FUNCTIONS__", body)
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
        path = Path(fh.name)
    try:
        res = subprocess.run([NODE, str(path)], capture_output=True, text=True,
                             timeout=60)
        if res.returncode:
            raise AssertionError(res.stdout + "\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


@unittest.skipUnless(NODE, "needs node")
class FilmClient(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.r = run_client()

    # ---- formatting ------------------------------------------------------
    def test_a_films_length_is_written_the_way_a_player_writes_it(self):
        self.assertEqual(self.r["clock"], ["0:00", "0:28", "1:05", "10:00"])

    def test_sizes_are_readable(self):
        self.assertEqual(self.r["bytes"], ["0 B", "2 KB", "17 MB", "3.0 GB"])

    def test_when_it_was_made_needs_no_arithmetic(self):
        self.assertEqual(self.r["ago"],
                         ["", "just now", "10 minutes ago", "2 hours ago",
                          "2 days ago"])

    def test_the_screen_says_which_button_made_the_file(self):
        # `export` survives as a client label for anything still carrying that
        # kind, but nothing writes it any more: one assembler, one name, so a
        # new film is just the film. `timeline` remains for the files written
        # before the merge — the screen should not rewrite their history.
        self.assertEqual(self.r["kinds"],
                         ["Timeline render", "Export", "Film", "Film"])

    def test_new_films_are_no_longer_labelled_by_the_button_that_made_them(self):
        import inspect
        self.assertIn("'Timeline render'", extract_function("sbFilmKind"))
        code = inspect.getsource(panel._sb_films)
        self.assertIn('"timeline" if p.stem.endswith("_timeline") else "film"',
                      code)
        self.assertNotIn('"export"', code)

    # ---- which film ------------------------------------------------------
    def test_the_newest_film_is_the_one_on_screen_by_default(self):
        self.assertEqual(self.r["pickNewest"], "b.mp4")

    def test_a_named_film_wins_over_the_newest(self):
        # The render that just finished is the one to show, even in the second
        # it takes the folder listing to agree about mtimes.
        self.assertEqual(self.r["pickNamed"], "a.mp4")

    def test_a_name_that_is_gone_falls_back_rather_than_showing_nothing(self):
        self.assertEqual(self.r["pickMissing"], "b.mp4")

    def test_no_films_is_no_film(self):
        self.assertIsNone(self.r["pickEmpty"])

    # ---- the rail --------------------------------------------------------
    def test_with_nothing_rendered_the_last_two_steps_are_locked(self):
        self.assertEqual(self.r["railNoClips"],
                         [["plan", "done"], ["shots", "now"],
                          ["edit", "locked"], ["film", "locked"]])

    def test_a_clip_on_disk_unlocks_the_editor(self):
        # Not `done` — that counts jobs THIS panel ran. A board of imported or
        # restored clips is exactly as editable, and gating on `done` hid the
        # timeline on a film whose clips were sitting right there.
        self.assertEqual(self.r["railClips"],
                         [["plan", "done"], ["shots", "done"],
                          ["edit", "ready"], ["film", "ready"]])

    def test_the_step_you_are_standing_in_says_so(self):
        # "Standing in it" now means the EDITOR TAB holds this film — the
        # timeline stopped being a state of the storyboard's stage.
        self.assertEqual(self.r["railTimeline"],
                         [["plan", "done"], ["shots", "done"],
                          ["edit", "now"], ["film", "ready"]])

    def test_the_film_step_carries_the_runtime(self):
        film = [s for s in self.r["railFilm"] if s["key"] == "film"][0]
        self.assertEqual(film["state"], "now")
        self.assertEqual(film["sub"], "0:28")

    def test_a_locked_step_still_says_what_would_unlock_it(self):
        self.assertIn("Render a shot first", self.r["railLockedHint"])

    def test_the_shots_step_counts_what_is_on_disk(self):
        self.assertEqual(self.r["railShotsSub"], ["6 planned", "4 of 6 rendered"])

    # ---- the board row ---------------------------------------------------
    def test_a_board_of_imported_clips_is_not_called_plan_only(self):
        # It said "plan only" while seven finished shots sat in the folder.
        self.assertEqual(self.r["chips"],
                         ["planning", "rendering", "plan only", "7 clips",
                          "drafts done", "3 of 6", "2 failed"])

    def test_a_board_with_no_clips_offers_nothing_it_cannot_do(self):
        self.assertEqual(self.r["rowNothing"], "")

    def test_a_board_with_clips_offers_the_editor(self):
        # One name for one surface: the row says "Open in Editor", the rail's
        # step 3 says "Edit", the tab says "Editor". Nothing says "Arrange".
        self.assertIn("sbOpenAt('x','edit')", self.r["rowClips"])
        self.assertIn("Open in Editor", self.r["rowClips"])

    def test_a_board_with_a_film_offers_the_film_and_its_length(self):
        self.assertIn("sbOpenAt('x','film')", self.r["rowFilm"])
        self.assertIn("0:28", self.r["rowFilm"])
        self.assertNotIn("'edit'", self.r["rowFilm"])

    # ---- the bug the Edit button had -------------------------------------
    def test_the_step_is_taken_AFTER_the_board_has_loaded(self):  # noqa: D401
        # sbOpen() used to swallow sbLoad()'s promise, so `await sbOpen(id)`
        # resolved immediately, the timeline opened, and the load that was
        # still in flight then called sbShow('plan') and tore it back down.
        # Clicking Edit landed on the shot list. Order is the whole fix.
        self.assertEqual(self.r["openAtOrder"],
                         ["sbLoad:sb_demo", "workflowSwitch:editor", "sbeOpen"])

    # ---- every door ------------------------------------------------------
    def test_each_step_opens_exactly_one_thing(self):
        # Step 3 is a TAB SWITCH that carries the film. Step 2 no longer has
        # to close the editor to show the shot list — they are different tabs
        # now, and leaving one does not tear the other down.
        self.assertEqual(self.r["doors"],
                         ["workflowSwitch:editor", "sbeOpen", "sbFilmOpen",
                          "sbShow:plan", "sbOpenReplan"])

    def test_no_board_open_is_nowhere_to_go(self):
        self.assertEqual(self.r["doorsWithNoBoard"], [])


# =============================================================================
# The markup the client drives has to exist
# =============================================================================
class FilmMarkup(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.src = panel.HTML

    def test_the_rail_carries_all_four_steps(self):
        for step in ("plan", "shots", "edit", "film"):
            self.assertIn('data-step="%s"' % step, self.src)

    def test_the_film_state_exists_and_has_somewhere_to_paint(self):
        self.assertIn('id="sbFilm"', self.src)
        self.assertIn('id="sbFilmBody"', self.src)

    def test_sbShow_knows_about_the_film_state(self):
        self.assertIn("film: 'sbFilm'", extract_function("sbShow"))

    def test_leaving_the_film_screen_stops_its_player(self):
        # A <video> left playing behind a hidden div is a decoder and a
        # soundtrack nobody can see.
        self.assertIn("v.pause()", extract_function("sbShow"))

    def test_both_assemblies_end_on_the_film(self):
        # A toast that fades is what made the finished film invisible.
        self.assertIn("sbFilmOpen", extract_function("sbeRenderFilm"))
        self.assertIn("sbFilmOpen", extract_function("sbExport"))

    def test_the_render_still_discloses_that_the_concat_closes_gaps(self):
        # The disclosure the editor gate already locks — proving the landing
        # was added to it, not swapped for it.
        fn = extract_function("sbeRenderFilm")
        self.assertIn("gaps_note", fn)
        self.assertIn("CONCATENATES", fn)


if __name__ == "__main__":
    unittest.main()
