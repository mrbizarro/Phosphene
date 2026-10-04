#!/usr/bin/env python3
"""4.17.0 Codex review — the Editor area (findings_editor.md, EDITOR-1..14).

Every fixed finding is pinned here with a test that fails on e724af7 and
passes after its fix. JS runs in node against the REAL functions, extracted
by scripts/extract_panel_js.py, on the same DOM shim the Editor's own gates
use (test_storyboard_editor_ui.SHIM + test_editor_save_integrity's fetch).
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from extract_panel_js import extract_function, panel_source      # noqa: E402

import storyboard_editor as sedit                                  # noqa: E402
import test_storyboard_editor_ui as ui                             # noqa: E402
import test_editor_save_integrity as si                            # noqa: E402

JS = ROOT / "webapp" / "js"


def run(body: str, extra: tuple = (), stubs: tuple = (), shim: str = "") -> dict:
    """Like test_editor_save_integrity.run, plus `stubs`: no-op functions for
    the painters a real function reaches that this gate is not about. A stub
    is declared BEFORE the extractions, so an extracted function of the same
    name (the shim's `sbeAdopt` stub included) wins."""
    if ui.NODE is None:
        raise unittest.SkipTest("node not on PATH")
    source = panel_source()
    names = list(ui.FUNCTIONS) + [n for n in extra if n not in ui.FUNCTIONS]
    stub_js = "".join(f"function {n}() {{ return null; }}\n"
                      for n in stubs if n not in names)
    script = (ui.SHIM + si.HELD_FETCH + shim + stub_js
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
            raise AssertionError("the script never finished\n" + res.stderr)
        return json.loads(res.stdout)
    finally:
        path.unlink(missing_ok=True)


def _clip(cid, path, start, end, film_start, **kw):
    c = sedit.new_clip(path, start, end, film_start, id=cid, duration=10.0,
                       source="human")
    c.update(kw)
    return c


def _doc(clips, **kw):
    d = {"version": sedit.EDIT_VERSION, "board_id": "sb_e", "revision": 0,
         "source": "human", "audio": None, "beats": None, "clips": clips,
         "settings": {}}
    d.update(kw)
    return d


# Everything sbeAdopt / sbeCloseDoc reach that is paint, audio or network.
ADOPT_STUBS = (
    "sbeKeyedDismiss", "sbePaintNotices", "sbePaintMusicName", "sbeSetMusicMode",
    "sbeSyncMusic", "sbePaintRelink", "sbePaintOffline", "sbeDeliverPaint",
    "edPoolRefresh", "sbeFetchPeaks", "sbeRestoreUndoIfFresh", "sbeZoomMin",
    "sbeShowFrameAt", "sbeStop", "sbeVersionsClose", "edRemember", "edShowPicker",
    "sbePaintRenderChip",
)
ADOPT_SHIM = r"""
const ED = { src: 'board' };
global.window = { addEventListener() {}, removeEventListener() {} };
"""


# =============================================================================
# EDITOR-1 [P0] — markers survive Save, backup, unload, reopen; films isolated
# =============================================================================
class Editor1MarkersTravel(unittest.TestCase):

    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
const beacons = [];
Object.defineProperty(globalThis, "navigator", { value: { sendBeacon: (url, blob) => { beacons.push({ url, blob }); return true; } }, configurable: true, writable: true });
function filmA() {
  return { title: 'A', edit: { revision: 3, clips: [clip({ id: 'a', end: 2 })],
           markers: [{ id: 'm0', at: 0.5, kind: 'beat', label: 'drop' }] } };
}
SBE.id = 'film-A'; SBE.open = true; SBE.session = 's1';
sbeAdopt(filmA(), false);
out.adoptedA = (SBE.markers || []).map(m => m.id);
// The real M-key handler.
SBE.playhead = 1.25;
sbeMarkerAtPlayhead();
out.liveAfterAdd = (SBE.markers || []).map(m => m.at);
// Save: the request, and the response comparison ("did anything move?").
FETCHES = [];
NEXT = { status: 200, body: { ok: true, edit: { revision: 4, clips: [],
         markers: [{ id: 'm0', at: 0.5, kind: 'beat', label: 'drop' }] } } };
await sbeSave(false);
out.saved = (JSON.parse(FETCHES[0].body).edit.markers || []).map(m => m.at);
out.dirtyAfterSave = SBE.dirty;
// The crash backup (debounced lane) and the unload beacon.
SBE.dirty = true; SBE.backedUpAt = 0; SBE.lastBackupSig = '';
FETCHES = [];
NEXT = { status: 200, body: { ok: true } };
await sbeBackup(true);
out.backup = (JSON.parse(FETCHES[0].body).edit.markers || []).map(m => m.at);
sbeBeaconBackup();
out.beacon = (JSON.parse(await beacons[0].blob.text()).edit.markers || []).map(m => m.at);
// Another film: its own markers (none), not A's.
SBE.id = 'film-B';
sbeAdopt({ title: 'B', edit: { revision: 1, clips: [clip({ id: 'b', end: 2 })] } }, false);
out.adoptedB = (SBE.markers || []).length;
// Back to A, then close.
SBE.id = 'film-A';
sbeAdopt(filmA(), false);
SBE.dirty = false;
sbeCloseDoc({ quiet: true });
out.afterClose = (SBE.markers || []).length;
""", extra=("sbeAdopt", "sbeCloseDoc", "sbeMarkerAtPlayhead", "sbeMarkerMutate",
            "sbeMarkerAdd", "sbeBackup", "sbeBeaconBackup", "sbeTsCopy",
            "sbeQueueSave"),
            stubs=ADOPT_STUBS, shim=ADOPT_SHIM)

    def test_a_reopened_film_shows_its_markers(self):
        self.assertEqual(self.r["adoptedA"], ["m0"])
        self.assertEqual(self.r["liveAfterAdd"], [0.5, 1.25])

    def test_save_backup_and_unload_all_carry_them(self):
        self.assertEqual(self.r["saved"], [0.5, 1.25])
        self.assertEqual(self.r["backup"], [0.5, 1.25])
        self.assertEqual(self.r["beacon"], [0.5, 1.25])

    def test_the_save_response_is_compared_with_the_markers_too(self):
        # Same serialisation on both sides of the wire: nothing moved.
        self.assertFalse(self.r["dirtyAfterSave"])

    def test_films_do_not_share_markers(self):
        self.assertEqual(self.r["adoptedB"], 0)
        self.assertEqual(self.r["afterClose"], 0)

    def test_every_save_body_caller_passes_every_collection_it_reads(self):
        src = (JS / "editor.js").read_text(encoding="utf-8")
        calls = src.split("sbeSaveBody({")[1:]
        self.assertGreaterEqual(len(calls), 4)
        for c in calls:
            head = c[:c.index("})")]
            for key in ("clips:", "overlays:", "tracks:", "transitions:",
                        "markers:"):
                self.assertIn(key, head, head)

    def test_the_saved_document_reaches_the_nle_export(self):
        body = {"edit": _doc([_clip("a", "/x/a.mp4", 0, 2, 0)],
                             markers=[{"id": "m1", "at": 1.25, "kind": "note",
                                       "label": ""}])}
        with tempfile.TemporaryDirectory() as d:
            sedit.save_edit(d, body["edit"])
            back = sedit.load_edit(d)
        self.assertEqual([m["at"] for m in sedit.markers_of(back)], [1.25])


# =============================================================================
# EDITOR-2 [P1] — "-14 LUFS" delivers a film instead of an ffmpeg refusal
# =============================================================================
def _ffmpeg():
    import mlx_ltx_panel as panel                                   # noqa: PLC0415
    return str(panel.FFMPEG) if panel.FFMPEG else None


def _av_clip(path: Path, secs=3, w=320, h=180, vol_db=-32, freq=440) -> None:
    subprocess.run([_ffmpeg(), "-y", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"testsrc2=s={w}x{h}:d={secs}:r=24",
                    "-f", "lavfi", "-i", f"sine=f={freq}:d={secs}:sample_rate=48000",
                    "-af", f"volume={vol_db}dB", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(path)], check=True)


def _integrated_lufs(path: Path) -> float:
    r = subprocess.run([_ffmpeg(), "-hide_banner", "-nostats", "-i", str(path),
                        "-map", "0:a:0", "-af", "ebur128", "-f", "null", "-"],
                       capture_output=True, text=True)
    import re                                                        # noqa: PLC0415
    vals = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    assert vals, r.stderr[-2000:]
    return float(vals[-1])


class Editor2LoudnormRenders(unittest.TestCase):
    def test_a_real_encode_with_loudnorm_writes_a_levelled_film(self):
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        if not _ffmpeg():
            raise unittest.SkipTest("no ffmpeg")
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.mp4"
            _av_clip(src)
            out = Path(d) / "film.mp4"
            res = panel._sb_assemble_film(
                [str(src)], out,
                timeline=[{"path": str(src), "start": 0.0, "end": 3.0,
                           "film_start": 0.0}],
                deliver={"format": "h264", "size": "native", "loudnorm": True})
            self.assertTrue(res.get("ok"), res)
            self.assertTrue(out.is_file() and out.stat().st_size > 0)
            before, after = _integrated_lufs(src), _integrated_lufs(out)
            self.assertLess(before, -25.0)
            self.assertAlmostEqual(after, -14.0, delta=2.5)


# =============================================================================
# EDITOR-3 [P1] — the frame heal never pushes a clip into a locked one
# =============================================================================
class Editor3HealRespectsLockedClips(unittest.TestCase):
    def _fixture(self):
        return _doc([
            _clip("a", "/x/a.mp4", 0.0, 0.98, 0.0),
            _clip("b", "/x/b.mp4", 0.0, 0.98, 0.98),
            _clip("c", "/x/c.mp4", 0.0, 2.0, 1.96, locked=True),
        ], settings={"fps": 24})

    def _save_twice(self, doc):
        with tempfile.TemporaryDirectory() as d:
            sedit.save_edit(d, doc)
            first = sedit.load_edit(d)
            sedit.save_edit(d, json.loads(json.dumps(first)))
            second = sedit.load_edit(d)
        return first, second

    def test_the_saved_document_validates_and_the_lock_does_not_move(self):
        self.assertEqual(sedit.blocking_errors(sedit.validate_edit(self._fixture())), [])
        first, second = self._save_twice(self._fixture())
        self.assertEqual(sedit.blocking_errors(sedit.validate_edit(first)), [],
                         [(c["id"], c["film_start"], c["film_end"]) for c in first["clips"]])
        by = {c["id"]: c for c in first["clips"]}
        self.assertEqual((by["c"]["film_start"], by["c"]["film_end"]), (1.96, 3.96))
        self.assertLessEqual(by["b"]["film_end"], 1.96 + 1e-6)
        # ...and the healed lengths are whole frames.
        for cid in ("a", "b"):
            n = (by[cid]["film_end"] - by[cid]["film_start"]) * 24
            self.assertAlmostEqual(n, round(n), places=3)

    def test_repeated_saves_are_stable(self):
        first, second = self._save_twice(self._fixture())
        shape = lambda d: [(c["id"], c["start"], c["end"], c["film_start"],
                            c["film_end"]) for c in d["clips"]]
        self.assertEqual(shape(first), shape(second))

    def test_the_heal_itself_is_bounded_by_the_anchor(self):
        doc = self._fixture()
        sedit.heal_subframe_lengths(doc)
        self.assertEqual(sedit.blocking_errors(sedit.validate_edit(doc)), [])

    def test_a_run_with_room_still_rounds_to_nearest(self):
        doc = _doc([_clip("a", "/x/a.mp4", 0.0, 0.98, 0.0),
                    _clip("b", "/x/b.mp4", 0.0, 0.98, 0.98)], settings={"fps": 24})
        sedit.heal_subframe_lengths(doc)
        self.assertEqual([c["film_end"] for c in doc["clips"]], [1.0, 2.0])

    def test_a_heal_that_would_not_validate_is_never_written(self):
        # Backstop: even a heal that misbehaves cannot write an invalid file.
        def bad_heal(edit):
            edit["clips"][1]["film_end"] = 5.0
            return []
        from unittest import mock                                   # noqa: PLC0415
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sedit, "heal_subframe_lengths", bad_heal):
            sedit.save_edit(d, self._fixture())
            back = sedit.load_edit(d)
        self.assertEqual(sedit.blocking_errors(sedit.validate_edit(back)), [])


# =============================================================================
# EDITOR-4 [P1] — every Replace result is a document Save accepts
# =============================================================================
class Editor4ReplaceStaysSaveable(unittest.TestCase):
    CASES = [
        # (name, old clip kwargs, new item) — the old clip sits at 10..15 s.
        ("shorter", dict(start=0.0, end=5.0), {"duration_s": 4.0}),
        ("shorter_in_point", dict(start=5.0, end=10.0), {"duration_s": 4.9}),
        ("in_point_clamped", dict(start=5.0, end=10.0), {"duration_s": 7.0}),
        ("in_point_kept", dict(start=2.0, end=7.0), {"duration_s": 30.0}),
        ("speed_2x_fits", dict(start=3.0, end=13.0, speed=2.0), {"duration_s": 12.0}),
        ("speed_2x_short", dict(start=0.0, end=10.0, speed=2.0), {"duration_s": 8.0}),
        ("speed_half", dict(start=1.0, end=3.5, speed=0.5), {"duration_s": 3.0}),
        ("exact", dict(start=0.0, end=5.0), {"duration_s": 5.0}),
        ("still", dict(start=0.0, end=10.0, speed=2.0),
         {"duration_s": 3.0, "kind": "still", "path": "/x/p.png"}),
    ]

    @classmethod
    def setUpClass(cls) -> None:
        cases = []
        for name, kw, item in cls.CASES:
            c = dict(id="b", path="/x/old.mp4", film_start=10.0, film_end=15.0,
                     duration=30.0, source="human", locked=False)
            c.update(kw)
            it = {"path": "/x/new.mp4"}
            it.update(item)
            cases.append([name, [dict(id="a", path="/x/a.mp4", start=0.0, end=10.0,
                                      film_start=0.0, film_end=10.0, duration=10.0,
                                      source="human", locked=False), c], it])
        cls.r = run("out.res = {};\nfor (const [name, cs, it] of %s) {\n"
                    "  const r = sbeReplaceClip(cs, 'b', it);\n"
                    "  out.res[name] = { ok: r.ok, why: r.why || '', "
                    "clips: r.clips.map(sbeCleanClip) };\n}\n" % json.dumps(cases),
                    extra=("sbeReplaceClip",))["res"]

    def test_every_accepted_replacement_validates(self):
        for name, _kw, _it in self.CASES:
            with self.subTest(name):
                res = self.r[name]
                doc = _doc(res["clips"])
                errs = sedit.blocking_errors(sedit.validate_edit(doc))
                self.assertEqual(errs, [], (name, res))
                b = [c for c in res["clips"] if c["id"] == "b"][0]
                self.assertEqual((b["film_start"], b["film_end"]), (10.0, 15.0))
                if res["ok"]:
                    self.assertNotEqual(b["path"], "/x/old.mp4")

    def test_a_take_that_cannot_fill_the_slot_is_refused_with_the_numbers(self):
        for name in ("shorter", "shorter_in_point", "speed_2x_short"):
            with self.subTest(name):
                self.assertFalse(self.r[name]["ok"])
                self.assertRegex(self.r[name]["why"], r"needs \d")
        for name in ("in_point_clamped", "in_point_kept", "speed_2x_fits",
                     "speed_half", "exact", "still"):
            self.assertTrue(self.r[name]["ok"], name)

    def test_in_points_and_speed_are_kept_where_the_take_allows(self):
        b = lambda n: [c for c in self.r[n]["clips"] if c["id"] == "b"][0]
        self.assertEqual((b("in_point_kept")["start"], b("in_point_kept")["end"]), (2.0, 7.0))
        self.assertEqual((b("in_point_clamped")["start"], b("in_point_clamped")["end"]), (2.0, 7.0))
        self.assertEqual((b("speed_2x_fits")["start"], b("speed_2x_fits")["end"]), (2.0, 12.0))
        self.assertEqual(b("speed_2x_fits").get("speed"), 2.0)
        self.assertEqual(b("still").get("kind"), "still")
        self.assertNotIn("speed", b("still"))


# =============================================================================
# EDITOR-5 [P1] — Paste Attributes applies speed as timing, not as a field
# =============================================================================
class Editor5PasteAttributesSpeed(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
function film() {
  return lay([clip({ id: 'fast', start: 0, end: 10, speed: 2, duration: 30, source: 'human' }),
              clip({ id: 'norm', start: 0, end: 5, duration: 30, source: 'human' }),
              clip({ id: 'pic', kind: 'still', path: '/x/p.png', start: 0, end: 3,
                     duration: null, source: 'human' }),
              clip({ id: 'tail', start: 0, end: 2, duration: 30, source: 'human' })]);
}
out.cases = {};
for (const [name, from, to] of [['2x_onto_1x', 'fast', ['norm']],
                                ['1x_onto_2x', 'norm', ['fast']],
                                ['2x_onto_still', 'fast', ['pic']],
                                ['2x_onto_two', 'fast', ['norm', 'tail']]]) {
  SBE.clips = film(); SBE.undo = []; SBE.redo = [];
  SBE.overlays = []; SBE.tracks = []; SBE.transitions = []; SBE.markers = [];
  const src = sbeClipboardCopy(SBE.clips, [from])[0];
  const ok = sbeMutate(cs => sbeClipboardPasteAttrs(cs, to, src));
  out.cases[name] = { ok, undo: SBE.undo.length, clips: SBE.clips.map(sbeCleanClip) };
}
""", extra=("sbeClipboardCopy", "sbeClipboardPasteAttrs"))["cases"]

    def _by(self, name, cid):
        return [c for c in self.r[name]["clips"] if c["id"] == cid][0]

    def test_every_paste_leaves_a_saveable_timeline_in_one_undo_step(self):
        for name, res in self.r.items():
            with self.subTest(name):
                self.assertTrue(res["ok"])
                self.assertEqual(res["undo"], 1)
                errs = sedit.blocking_errors(sedit.validate_edit(_doc(res["clips"])))
                self.assertEqual(errs, [], (name, res["clips"]))

    def test_speed_changes_the_slot_not_the_window(self):
        n = self._by("2x_onto_1x", "norm")
        self.assertEqual(n.get("speed"), 2.0)
        self.assertEqual((n["start"], n["end"]), (0.0, 5.0))
        self.assertAlmostEqual(n["film_end"] - n["film_start"], 2.5)
        back = self._by("1x_onto_2x", "fast")
        self.assertNotIn("speed", back)             # back to 1x = no field
        self.assertAlmostEqual(back["film_end"] - back["film_start"], 10.0)

    def test_a_still_never_gets_a_video_speed(self):
        self.assertNotIn("speed", self._by("2x_onto_still", "pic"))


# =============================================================================
# EDITOR-6 [P1] — two renders at one delivery become two versions
# =============================================================================
from test_render_job import Sandbox as _RenderSandbox                    # noqa: E402


class Editor6ConcurrentRendersAreVersions(_RenderSandbox):
    def setUp(self):
        super().setUp()
        import threading                                            # noqa: PLC0415
        from unittest import mock                                   # noqa: PLC0415
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        self.panel = panel
        self.gate = threading.Event()
        self.entered = []

        def held_ffmpeg(cmd, label):
            self.entered.append(cmd[-1])
            k = len(self.entered)
            self.gate.wait(10)
            Path(cmd[-1]).write_bytes(b"film-" + str(k).encode())
        p = mock.patch.object(panel, "run_ffmpeg_tracked", side_effect=held_ffmpeg)
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self.gate.set)

    def _status(self, job):
        return self._get(f"/storyboard/edit/render/status?job={job}").payload

    def test_both_films_survive_with_their_own_sidecars(self):
        a = self._post("edit/render/start", {"id": "sb_t"}).payload["job"]
        self.assertTrue(self._wait_until(lambda: len(self.entered) >= 1))
        b = self._post("edit/render/start", {"id": "sb_t"}).payload["job"]
        self.assertTrue(self._wait_until(lambda: len(self.entered) >= 2))
        self.gate.set()
        for j in (a, b):
            self.assertTrue(self._wait_until(lambda: self._status(j)["state"] != "running",
                                             timeout=20))
        ra, rb = self._status(a), self._status(b)
        self.assertEqual((ra["state"], rb["state"]), ("done", "done"), (ra, rb))
        pa, pb = Path(ra["result"]["path"]), Path(rb["result"]["path"])
        self.assertNotEqual(pa, pb)
        self.assertTrue(pa.is_file() and pb.is_file())
        self.assertNotEqual(pa.read_bytes(), pb.read_bytes())
        sa, sb_ = self.panel._sb_film_sidecar(pa), self.panel._sb_film_sidecar(pb)
        self.assertNotEqual(sa, sb_)
        self.assertTrue(sa.is_file() and sb_.is_file())
        # Nothing stays claimed once both are finished.
        self.assertEqual(self.panel._SB_FILM_NAMES_IN_USE, set())

    def test_a_running_render_holds_its_name_until_it_ends(self):
        a = self._post("edit/render/start", {"id": "sb_t"}).payload["job"]
        self.assertTrue(self._wait_until(lambda: len(self.entered) >= 1))
        claimed = set(self.panel._SB_FILM_NAMES_IN_USE)
        self.assertEqual(len(claimed), 1)
        held = Path(next(iter(claimed)))
        # The next name offered for the same delivery is not the held one.
        self.assertNotEqual(self.panel._sb_film_name({"title": "Night Drive"}, None,
                                                     dest=held.parent), held.name)
        self.gate.set()
        self.assertTrue(self._wait_until(lambda: self._status(a)["state"] != "running",
                                         timeout=20))
        self.assertEqual(self.panel._SB_FILM_NAMES_IN_USE, set())


# =============================================================================
# EDITOR-7 [P1] — the aspect crop keeps the delivery's codec and container
# =============================================================================
def _probe_stream(path: Path) -> dict:
    import mlx_ltx_panel as panel                                   # noqa: PLC0415
    out = subprocess.run([str(panel.FFPROBE), "-v", "error", "-show_entries",
                          "stream=codec_type,codec_name,pix_fmt,width,height,codec_tag_string"
                          ":format=format_name", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = [st for st in j["streams"] if st["codec_type"] == "video"][0]
    a = [st for st in j["streams"] if st["codec_type"] == "audio"]
    return {"codec": v["codec_name"], "pix_fmt": v["pix_fmt"], "w": v["width"],
            "h": v["height"], "tag": v.get("codec_tag_string"),
            "audio": a[0]["codec_name"] if a else None,
            "format": j["format"]["format_name"]}


class Editor7AspectCropKeepsTheDelivery(unittest.TestCase):
    WANT = {"h264": ("h264", None, "mp4"), "hevc": ("hevc", "yuv420p", "mp4"),
            "prores": ("prores", "yuv422p10le", "mov")}

    def _one(self, fmt):
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        if not (_ffmpeg() and panel.FFPROBE):
            raise unittest.SkipTest("no ffmpeg/ffprobe")
        dl = panel._sb_deliver(fmt, "native")
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / "a.mp4"
            _av_clip(src, secs=1, w=320, h=180)
            master = Path(d) / f"film_{fmt}{dl['ext']}"
            # The delivery exactly as the assembler encodes it.
            subprocess.run([_ffmpeg(), "-y", "-loglevel", "error", "-i", str(src),
                            *panel._sb_encode_args(dl, panel.output_codec_settings()),
                            str(master)], check=True)
            before = _probe_stream(master)
            err = panel._sb_crop_to_aspect(master, "9:16", deliver=dl)
            self.assertIsNone(err)
            after = _probe_stream(master)
            left = sorted(x.name for x in Path(d).iterdir())
        return before, after, left

    def test_h264_hevc_and_prores_stay_what_was_asked_for(self):
        for fmt, (codec, pix, container) in self.WANT.items():
            with self.subTest(fmt):
                try:
                    before, after, left = self._one(fmt)
                except subprocess.CalledProcessError as exc:
                    if fmt == "hevc":
                        raise unittest.SkipTest(f"no HEVC encoder here: {exc}")
                    raise
                self.assertEqual(after["codec"], codec)
                self.assertEqual(after["codec"], before["codec"])
                self.assertEqual(after["pix_fmt"], before["pix_fmt"])
                if pix:
                    self.assertEqual(after["pix_fmt"], pix)
                self.assertIn(container, after["format"])
                self.assertEqual(after["tag"], before["tag"])
                self.assertEqual(after["audio"], before["audio"])
                self.assertEqual((after["w"], after["h"]), (100, 180))
                # No temp file of any kind is left beside the master.
                self.assertEqual(len(left), 2, left)


# =============================================================================
# EDITOR-8 / EDITOR-9 [P1] — Auto-align measures the window on the film, and
# its proposal corrects the lag instead of doubling it
# =============================================================================
class Editor8And9AutoAlign(unittest.TestCase):
    VFPS = 24.0
    SR = 16000

    def _world(self, *, src_true, film_start, film_len, speed, offset, seed=7):
        """A 10 s take whose mouth signal is S, and a song whose loudness
        envelope at film second t follows S at source second
        `src_true + (t - film_start) * speed` — so `src_true` is THE in-point
        that puts the picture on the song."""
        import numpy as np                                          # noqa: PLC0415
        rng = np.random.default_rng(seed)
        S = np.convolve(rng.standard_normal(int(12 * self.VFPS)), np.ones(4) / 4, "same")
        S = 0.05 + (S - S.min())
        opens = {i: float(v) for i, v in enumerate(S[:int(10 * self.VFPS)])}
        T = np.arange(int(20 * self.SR)) / self.SR                 # track seconds
        u = src_true + (T - offset - film_start) * speed            # source seconds
        idx = np.clip(np.floor(u * self.VFPS + 1e-6).astype(int), 0, len(S) - 1)
        env = np.where((T - offset >= film_start - 1) & (T - offset <= film_start + film_len + 1),
                       S[idx], 0.05)
        song = (rng.standard_normal(len(T)) * env).astype(np.float32)
        return opens, song

    def _measure(self, opens, song, src_start, **kw):
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        return panel._lipsync_align_measure(opens, self.VFPS, song, self.SR,
                                            src_start=src_start, **kw)

    def _roundtrip(self, *, placed, src_true, speed=1.0, offset=0.0,
                   film_start=5.0, film_len=4.0):
        opens, song = self._world(src_true=src_true, film_start=film_start,
                                  film_len=film_len, speed=speed, offset=offset)
        kw = dict(film_start=film_start, film_end=film_start + film_len, speed=speed,
                  song_offset=offset, play_start=0.0, play_end=None, fps=24.0)
        first = self._measure(opens, song, placed, **kw)
        self.assertIsNotNone(first)
        fixed = placed + first["delta_sec"]
        second = self._measure(opens, song, fixed, **kw)
        return first, fixed, second

    def test_a_trimmed_take_early_or_late_is_corrected_to_zero_residual(self):
        # A 10 s take trimmed to 4 s starting 3 s in (EDITOR-9's case) —
        # the picture 3 frames early, then 2 frames late.
        for placed, true in ((3.0, 3.0 - 3 / 24), (3.0, 3.0 + 2 / 24)):
            with self.subTest(placed=placed, true=true):
                first, fixed, second = self._roundtrip(placed=placed, src_true=true)
                self.assertNotEqual(first["lag_frames"], 0)
                self.assertAlmostEqual(fixed, true, places=3)
                self.assertEqual(second["lag_frames"], 0)

    def test_speed_and_a_moved_soundtrack_are_on_the_same_clock(self):
        first, fixed, second = self._roundtrip(placed=2.0, src_true=2.0 + 0.25,
                                               speed=2.0, offset=1.5)
        self.assertEqual(first["lag_frames"], -3)
        self.assertAlmostEqual(fixed, 2.25, places=3)
        self.assertEqual(second["lag_frames"], 0)

    def test_an_unmeasurable_window_is_none_not_zero(self):
        import numpy as np                                          # noqa: PLC0415
        song = np.zeros(20 * self.SR, dtype=np.float32)
        self.assertIsNone(self._measure({}, song, 0.0, film_start=0.0, film_end=4.0))
        self.assertIsNone(self._measure({i: 0.3 for i in range(240)}, song, 0.0,
                                        film_start=0.0, film_end=4.0))

    def test_the_route_passes_the_window_and_reports_unmeasured_shots(self):
        from unittest import mock                                   # noqa: PLC0415
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        import test_editor_film_sync as fs                          # noqa: PLC0415
        case = fs.TheAutoAlignRoute("test_a_lagging_shot_is_proposed")
        case.setUp()
        try:
            edit = sedit.load_edit(case.root / "sb_t")
            edit["clips"][0].update(start=3.0, end=7.0, speed=1.0)
            sedit.save_edit(case.root / "sb_t", edit)
            with mock.patch.object(panel, "take_lipsync_best_lag_vs_song",
                                   return_value=None) as fn:
                h = case.FakeHandler()
                h.post("edit/auto-align", {"id": "sb_t"})
            kw = fn.call_args.kwargs
            self.assertEqual(kw["src_start"], 3.0)
            self.assertEqual(kw["speed"], 1.0)
            self.assertEqual(h.payload["proposals"], [])
            self.assertEqual([s["id"] for s in h.payload["skipped"]], ["c1"])
            self.assertEqual(h.payload["checked"], 0)
        finally:
            case.doCleanups()
            case.tmp.cleanup()


class Editor9UnmeasuredIsNotInSync(unittest.TestCase):
    def test_the_toast_says_not_measured_never_already_matches(self):
        r = run(r"""
SBE.clips = lay([clip({ id: 'c1', end: 4 })]);
out.toasts = [];
for (const body of [{ ok: true, proposals: [], skipped: [{ id: 'c1' }], checked: 0 },
                    { ok: true, proposals: [], skipped: [], checked: 0 },
                    { ok: true, proposals: [], skipped: [], checked: 2 }]) {
  toasts.length = 0;
  NEXT = { status: 200, body: body };
  await sbeAutoAlign();
  out.toasts.push(toasts.slice());
}
""", extra=("sbeAutoAlign", "sbeAnalysisSnapshot", "sbeAnalysisFp", "sbeAnalysisFresh"))
        unmeasured, none, matched = r["toasts"]
        self.assertIn("could not be measured", unmeasured[0])
        self.assertNotIn("already", unmeasured[0])
        self.assertIn("no singing", none[0])
        self.assertIn("already matches", matched[0])


# =============================================================================
# EDITOR-10 [P1] — Keep never names another tab's arrangement
# =============================================================================
class Editor10KeepDuringAConflict(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
function setUp(conflict, dirty) {
  SBE.clips = lay([clip({ id: 'a', end: 2 })]);
  SBE.edit = { clips: SBE.clips }; SBE.overlays = []; SBE.tracks = [];
  SBE.transitions = []; SBE.markers = [];
  SBE.conflict = conflict; SBE.dirty = dirty; SBE.dirtyAt = dirty ? 1 : 0;
  SBE.saving = false; SBE.savePending = false; SBE.revision = 1;
  sbeEl('sbeVersName').value = 'my cut';
  FETCHES = []; toasts.length = 0;
}
out.runs = {};
for (const [name, conflict, dirty] of [['revision_conflict', 2, true],
                                       ['conflict_clean', 2, false],
                                       ['dirty_no_conflict', 0, true]]) {
  setUp(conflict, dirty);
  NEXT = { status: 200, body: { ok: true, revision: 2, edit: { revision: 2, clips: [] } } };
  await sbeKeepVersion();
  out.runs[name] = { urls: FETCHES.map(f => f.url), toasts: toasts.slice() };
}
""", extra=("sbeKeepVersion",), stubs=("sbeNameMode",))["runs"]

    def test_a_conflicted_tab_is_refused_and_nothing_is_named(self):
        for name in ("revision_conflict", "conflict_clean"):
            with self.subTest(name):
                run_ = self.r[name]
                self.assertEqual(run_["urls"], [])
                self.assertTrue(any("Keep mine" in t for t in run_["toasts"]), run_)
                self.assertFalse(any(t.startswith("Kept") for t in run_["toasts"]))

    def test_without_a_conflict_it_saves_then_names(self):
        self.assertEqual(self.r["dirty_no_conflict"]["urls"],
                         ["/storyboard/edit/save", "/storyboard/edit/version"])


# =============================================================================
# EDITOR-11 [P1] — after Save, the browser plays the cut the server saved
# =============================================================================
class Editor11SaveAdoptsHealedTimings(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sent = [_clip("a", "/x/a.mp4", 0.0, 0.98, 0.0), _clip("b", "/x/b.mp4", 1.0, 2.97, 0.98),
                _clip("c", "/x/c.mp4", 0.0, 1.49, 2.95)]
        with tempfile.TemporaryDirectory() as d:
            sedit.save_edit(d, _doc(json.loads(json.dumps(sent)), settings={"fps": 24}))
            cls.saved = sedit.load_edit(d)
        cls.r = run(r"""
const SENT = %s, SAVED = %s;
function film() {
  SBE.clips = lay(JSON.parse(JSON.stringify(SENT)).map(c => clip(c)));
  SBE.edit = { clips: SBE.clips, settings: { fps: 24 } };
  SBE.overlays = []; SBE.tracks = []; SBE.transitions = []; SBE.markers = [];
  SBE.dirty = true; SBE.dirtyAt = 1; SBE.conflict = 0; SBE.saving = false;
  SBE.savePending = false; SBE.revision = 1; SBE.sel = 'b';
  SBE.undo = ['u1']; SBE.redo = [];
}
film();
NEXT = { status: 200, body: { ok: true, edit: SAVED } };
await sbeSave(false);
out.after = SBE.clips.map(sbeCleanClip);
out.dirty = SBE.dirty; out.sel = SBE.sel; out.undo = SBE.undo.length;
// The next save sends exactly what is on disk.
out.next = sbeSaveBody({ id: SBE.id, edit: SBE.edit, clips: SBE.clips }).edit.clips;
// An edit made while the save was on the wire is never overwritten.
film();
HOLD = true; FETCHES = [];
NEXT = { status: 200, body: { ok: true, edit: SAVED } };
const p = sbeSave(false);
await tick();
sbeMutate(cs => sbeTrim(cs, 'c', 'r', 4.0));
out.trimmedEnd = sbeById(SBE.clips, 'c').film_end;
await release();
HOLD = false;
await p;
out.afterRaceEnd = sbeById(SBE.clips, 'c').film_end;
out.afterRaceDirty = SBE.dirty;
""" % (json.dumps(sent), json.dumps(cls.saved)))

    def _shape(self, clips):
        return [(c["id"], round(c["start"], 6), round(c["end"], 6),
                 round(c["film_start"], 6), round(c["film_end"], 6)) for c in clips]

    def test_the_healed_timings_are_what_the_browser_now_holds(self):
        # The fixture is one the heal really rewrites...
        self.assertNotEqual([c["film_end"] for c in self.saved["clips"]], [0.98, 2.95, 4.44])
        self.assertEqual(self._shape(self.r["after"]), self._shape(self.saved["clips"]))
        self.assertEqual(self._shape(self.r["next"]), self._shape(self.saved["clips"]))
        self.assertFalse(self.r["dirty"])
        self.assertEqual(self.r["sel"], "b")
        self.assertEqual(self.r["undo"], 1)

    def test_a_concurrent_edit_is_kept(self):
        self.assertEqual(self.r["afterRaceEnd"], self.r["trimmedEnd"])
        self.assertTrue(self.r["afterRaceDirty"])


# =============================================================================
# EDITOR-12 [P1] — a finished render belongs to the film it rendered
# =============================================================================
RENDER_SHIM = r"""
const opened = [], revealed = [], lastToastEls = [];
function mkEl() {
  return { children: [], hidden: false, dataset: {}, innerHTML: '', textContent: '',
           appendChild(c) { this.children.push(c); }, remove() {} };
}
global.document = { createElement: () => mkEl() };
function phosToast(m, o) { toasts.push(String(m)); const e = mkEl(); lastToastEls.push(e); return e; }
async function sbeOpenFilmScreen(id, focus) { opened.push([id, focus]); }
async function sbeRevealRender(id, focus) { revealed.push([id, focus]); }
"""


class Editor12RenderBelongsToItsFilm(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
SBE.id = 'film-A'; SBE.title = 'Film A';
els.sbeRenderBtn = Object.assign(stubEl('sbeRenderBtn'), { textContent: 'Render' });
els.sbeRenderChip = Object.assign(stubEl('sbeRenderChip', true),
                                  { appendChild() {} });
SBE.renderJob = 'j1'; SBE.rendering = true;
// A's render is running; the user opens film B before it lands.
SBE.id = 'film-B'; SBE.title = 'Film B';
NEXT = { status: 200, body: { ok: true, state: 'done',
         result: { ok: true, path: '/out/film-A_film.mp4', duration: 12 } } };
await sbeRenderPoll('j1', els.sbeRenderBtn, 'Render', 'film-A', 'Film A');
out.chipHiddenOnB = els.sbeRenderChip.hidden;
out.chipBoardOnB = els.sbeRenderChip.dataset.board || '';
out.toast = toasts[toasts.length - 1];
// The toast's two actions.
const t = lastToastEls[lastToastEls.length - 1];
for (const a of t.children) a.onclick({ preventDefault() {} });
out.opened = opened; out.revealed = revealed;
// Back on A, its chip is there; on B again, it is not.
sbeRenderChipFor('film-A');
out.chipOnA = [els.sbeRenderChip.hidden, els.sbeRenderChip.dataset.board];
sbeRenderChipFor('film-B');
out.chipOnBAgain = els.sbeRenderChip.hidden;
""", extra=("sbeRenderPoll", "sbeRenderSettle", "sbeRenderFinish", "sbePaintRenderChip",
            "sbeRenderChipFor"), shim=RENDER_SHIM)

    def test_the_open_film_does_not_get_the_other_films_chip(self):
        self.assertTrue(self.r["chipHiddenOnB"])
        self.assertEqual(self.r["chipBoardOnB"], "")
        self.assertIn("Film A", self.r["toast"])

    def test_open_and_show_in_finder_target_the_rendered_film(self):
        self.assertEqual(self.r["opened"], [["film-A", "film-A_film.mp4"]])
        self.assertEqual(self.r["revealed"], [["film-A", "film-A_film.mp4"]])

    def test_the_chip_follows_the_film(self):
        self.assertEqual(self.r["chipOnA"], [False, "film-A"])
        self.assertTrue(self.r["chipOnBAgain"])


# =============================================================================
# EDITOR-13 [P1] — Match colour and Auto-align measure the arrangement on
# screen, and a proposal only lands on the clip it was measured on
# =============================================================================
class Editor13RoutesMeasureTheSentArrangement(unittest.TestCase):
    def _case(self, mod, cls, name):
        case = getattr(mod, cls)(name)
        case.setUp()
        self.addCleanup(case.doCleanups)
        self.addCleanup(case.tmp.cleanup)
        return case

    def test_match_colour_samples_the_replaced_clip_not_the_saved_one(self):
        from unittest import mock                                   # noqa: PLC0415
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        import test_editor_film_grade as fg                         # noqa: PLC0415
        case = self._case(fg, "TheMatchColourRoute", "test_no_ref_uses_the_films_median")
        live = json.loads(json.dumps(case.edit))
        new = case.root / "b_retake.mp4"
        new.write_bytes(b"x")
        live["clips"][1].update(path=str(new), start=2.0, end=6.0, duration=10.0)
        live["clips"].append(dict(live["clips"][2], id="d", film_start=12.0, film_end=16.0))
        seen = []

        def rgb(path, at):
            seen.append((Path(path).name, at))
            return (0.5, 0.5, 0.5)
        with mock.patch.object(panel, "take_frame_rgb", side_effect=rgb):
            h = case.FakeHandler()
            h.post("edit/match-colour", {"id": "sb_g", "edit": json.dumps(live)})
        self.assertEqual(h.status, 200, h.payload)
        self.assertIn(("b_retake.mp4", 4.0), seen)
        self.assertNotIn("b.mp4", [n for n, _ in seen])
        self.assertEqual(len(seen), 4)                     # the unsaved clip too

    def test_auto_align_measures_the_sent_window(self):
        from unittest import mock                                   # noqa: PLC0415
        import mlx_ltx_panel as panel                               # noqa: PLC0415
        import test_editor_film_sync as fs                          # noqa: PLC0415
        case = self._case(fs, "TheAutoAlignRoute", "test_a_lagging_shot_is_proposed")
        live = json.loads(json.dumps(case.edit))
        live["clips"][0].update(start=1.5, end=5.5, duration=10.0)
        with mock.patch.object(panel, "take_lipsync_best_lag_vs_song",
                               return_value=None) as fn:
            h = case.FakeHandler()
            h.post("edit/auto-align", {"id": "sb_t", "edit": json.dumps(live)})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(fn.call_args.kwargs["src_start"], 1.5)

    def test_an_arrangement_that_would_not_save_is_not_measured(self):
        import test_editor_film_grade as fg                         # noqa: PLC0415
        case = self._case(fg, "TheMatchColourRoute", "test_no_ref_uses_the_films_median")
        live = json.loads(json.dumps(case.edit))
        live["clips"][1]["end"] = 9.0                      # window != slot
        h = case.FakeHandler()
        h.post("edit/match-colour", {"id": "sb_g", "edit": json.dumps(live)})
        self.assertEqual(h.status, 400)
        self.assertIn("problem", h.payload["error"])


ANALYSIS_SHIM = r"""
let _sbeMatchProposals = [], _sbeMatchFp = {};
const toastEls = [];
function mkEl() {
  return { children: [], hidden: false, dataset: {}, className: '', classList: { add() {}, remove() {} },
           appendChild(c) { this.children.push(c); }, remove() {} };
}
global.document = { createElement: () => mkEl() };
function phosToast(m, o) { toasts.push(String(m)); const e = mkEl(); toastEls.push(e); return e; }
"""


class Editor13ProposalsOnlyLandWhereMeasured(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.r = run(r"""
function film() {
  SBE.clips = lay([clip({ id: 'a', end: 4, duration: 10 }), clip({ id: 'b', end: 4, duration: 10 })]);
  SBE.edit = { clips: SBE.clips }; SBE.overlays = []; SBE.tracks = [];
  SBE.transitions = []; SBE.markers = []; SBE.undo = []; SBE.redo = [];
  SBE.open = true; SBE.id = 'sb_t';
}
// ---- Auto-align: the request carries the screen; a clip replaced after the
// measurement is not slipped by it.
film();
sbeMutate(cs => sbeReplaceClip(cs, 'a', { path: '/o/a2.mp4', duration_s: 10 }));  // unsaved
sbeMutate(cs => sbeSlip(cs, 'a', 1.0));                                         // in-point 1 s
FETCHES = []; toasts.length = 0;
NEXT = { status: 200, body: { ok: true, checked: 2, skipped: [],
         proposals: [{ id: 'a', delta_sec: -0.125 }, { id: 'b', delta_sec: 0.25 }] } };
await sbeAutoAlign();
const sent = new URLSearchParams(FETCHES[0].body.toString());
out.sentPaths = JSON.parse(sent.get('edit')).clips.map(c => c.path);
// ...then b is trimmed before Accept.
sbeMutate(cs => sbeSlip(cs, 'b', 1.0));
const bStart = sbeById(SBE.clips, 'b').start;
const accept = toastEls[toastEls.length - 1].children[0];
accept.onclick({ preventDefault() {} });
out.aStart = sbeById(SBE.clips, 'a').start;
out.bStart = sbeById(SBE.clips, 'b').start;
out.bStartBefore = bStart;
out.acceptToast = toasts[toasts.length - 1];
// ---- Match colour: a clip replaced while the dialog is open is left alone.
film();
FETCHES = []; toasts.length = 0;
NEXT = { status: 200, body: { ok: true, reference: { r: .5, g: .5, b: .5 },
         proposals: [{ id: 'a', exposure: 0.2, temp: 0, tint: 0 },
                     { id: 'b', exposure: 0.2, temp: 0, tint: 0 }] } };
els.sbeMatchModal = Object.assign(stubEl('sbeMatchModal'), { classList: { add() {}, remove() {} } });
els.sbeMatchStrength = Object.assign(stubEl('sbeMatchStrength'), { value: 100 });
await sbeMatchColourOpen();
out.mcSent = new URLSearchParams(FETCHES[0].body.toString()).has('edit');
sbeMutate(cs => sbeReplaceClip(cs, 'b', { path: '/o/b2.mp4', duration_s: 10 }));
sbeMatchApply();
out.aGrade = sbeGrade(sbeById(SBE.clips, 'a')).exposure;
out.bGrade = sbeGrade(sbeById(SBE.clips, 'b')).exposure;
out.mcToast = toasts[toasts.length - 1];
""", extra=("sbeAutoAlign", "sbeAnalysisSnapshot", "sbeAnalysisFp", "sbeAnalysisFresh",
            "sbeReplaceClip", "sbeSlip", "sbeMutateEach", "sbeMatchColourOpen",
            "sbeMatchApply", "sbeMatchClose", "sbeGrade", "sbeSetGrade"),
            stubs=("sbeNiceName", "sbeSelCount"), shim=ANALYSIS_SHIM)

    def test_the_request_carries_the_unsaved_replacement(self):
        self.assertEqual(self.r["sentPaths"], ["/o/a2.mp4", "/o/b.mp4"])
        self.assertTrue(self.r["mcSent"])

    def test_a_clip_changed_after_measuring_is_left_alone(self):
        self.assertAlmostEqual(self.r["aStart"], 0.875)     # the measured clip is slipped
        self.assertEqual(self.r["bStart"], self.r["bStartBefore"])
        self.assertIn("changed after", self.r["acceptToast"])

    def test_match_colour_skips_the_replaced_clip(self):
        self.assertAlmostEqual(self.r["aGrade"], 0.2)
        self.assertAlmostEqual(self.r["bGrade"], 0.0)
        self.assertIn("changed after", self.r["mcToast"])


# =============================================================================
# EDITOR-14 [P1] — Film Look reaches the After Effects export
# =============================================================================
class Editor14FilmLookReachesAfterEffects(unittest.TestCase):
    def _export(self, look):
        from unittest import mock                                   # noqa: PLC0415
        c = _clip("a", "/x/a.mp4", 0.0, 4.0, 0.0, adjust={"saturation": 1.2})
        probe = lambda p: {"w": 768, "h": 416, "duration": 10.0, "has_audio": True}
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(sedit.Path, "is_file", return_value=True), \
                mock.patch.object(sedit, "_link_or_copy", return_value="link"):
            res = sedit.export_nle([c], d, name="m", probe=probe, film_look=look)
            return Path(res["jsx"]).read_text()

    def test_warm_tungsten_changes_the_ae_saturation_the_render_uses(self):
        off, on = self._export(None), self._export("warm_tungsten")
        self.assertIn("satur(lay, 20.0);", off)       # the clip's own 1.2
        self.assertIn("satur(lay, 30.0);", on)        # + the look's 1.1
        # ...and the stacked grade is the one the render plan draws.
        seg = sedit._nle_segments([_clip("a", "/x/a.mp4", 0.0, 4.0, 0.0)],
                                  film_look="warm_tungsten")[0]
        self.assertEqual((seg["fx"]["saturation"], seg["fx"]["temp"], seg["fx"]["tint"]),
                         (1.1, 0.3, -0.05))

    def test_a_slug_gets_no_look(self):
        slug = dict(_clip("s", "", 0.0, 2.0, 0.0), kind="slug")
        seg = sedit._nle_segments([slug], film_look="warm_tungsten")
        self.assertEqual(len(seg), 1)
        self.assertEqual(seg[0]["fx"]["saturation"], 1.0)


class Editor14TheRoutePassesTheLook(_RenderSandbox):
    def test_export_nle_route_sends_settings_film_look(self):
        from unittest import mock                                   # noqa: PLC0415
        edit = sedit.load_edit(self.bdir)
        edit["settings"] = {"film_look": "warm_tungsten"}
        sedit.save_edit(self.bdir, edit)
        fake = {"ok": True, "dir": "/x/p", "clips": 1, "linked": 1, "copied": 0,
                "missing": []}
        with mock.patch.object(sedit, "export_nle", return_value=fake) as fn:
            h = self._post("edit/export-nle", {"id": "sb_t"})
        self.assertEqual(h.status, 200, h.payload)
        self.assertEqual(fn.call_args.kwargs.get("film_look"), "warm_tungsten")


if __name__ == "__main__":
    unittest.main()
