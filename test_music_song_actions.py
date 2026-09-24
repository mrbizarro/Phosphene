#!/usr/bin/env python3
"""The Music Studio's verbs on an existing song: what they send, and what the
queued job keeps of the song it came from.

  * M6-01  Restyle and an edited score queued the PARENT's Max length, not
           the slider on screen.
  * M6-03  An edited score dropped the song's adapters, Instrumental, LoRA
           mode, guidance and precision.
  * M6-06  Two edits of one song in the same second shared one .abc file,
           so the first job rendered the second one's notes.
  * M6-02  A ⋯-menu action fired 400 ms after opening the card and used
           whatever song the card held by then — the previous one.
  * M6-10  Every parent-song link was an inline handler with a syntax error.

Routes run for real against a scratch outputs/state tree and an in-memory
queue; the browser half runs in node. No model, no GPU, no audio.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402
from panel import routes_music as R                                  # noqa: E402
from scripts.extract_panel_js import extract_function                # noqa: E402

R.P = P
NODE = shutil.which("node")


class _Handler:
    def __init__(self, form):
        self.form = {k: [v] for k, v in form.items()}
        self.payload, self.status = None, None

    def _read_form_body(self):
        return b"", self.form

    def _json(self, payload, status=200):
        self.payload, self.status = payload, status


class Studio(unittest.TestCase):
    """A scratch outputs tree, LoRA folder and queue."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.out, self.loras, self.art = root / "out", root / "loras", root / "art"
        for d in (self.out, self.loras / "user", self.art):
            d.mkdir(parents=True)
        (self.loras / "user" / "voice.safetensors").write_bytes(b"x" * 64)
        self.queue = {"queue": [], "current": None, "history": []}
        for p in (mock.patch.object(P, "OUTPUT", self.out),
                  mock.patch.object(P, "MUSIC_LORAS", self.loras),
                  mock.patch.object(P, "MUSIC_ARTIFACTS", self.art),
                  mock.patch.object(P, "STATE", self.queue),
                  mock.patch.object(P, "persist_queue", lambda: None),
                  mock.patch.object(P, "push", lambda *a, **k: None)):
            p.start()
            self.addCleanup(p.stop)

    def song(self, name="music_song.wav", *, instrumental=False, **params):
        wav = self.out / name
        wav.write_bytes(b"RIFF")
        base = {"music_max_seconds": 180, "music_quality": "final",
                "music_lora_mode": "separate", "music_cfg_scale": 1.3,
                "music_precision": "bf16",
                "music_loras": [] if instrumental else
                [{"id": "user/voice.safetensors", "strength": 0.8,
                  "path": str(self.loras / "user" / "voice.safetensors")}]}
        base.update(params)
        side = {"engine": "music", "style": "dream pop", "lyrics": "[verse]\nla",
                "mode": "full", "instrumental": instrumental,
                "score_abc": "X:1\nK:C\nCDEF|\n", "params": base}
        Path(str(wav) + ".json").write_text(json.dumps(side))
        return wav

    def queued(self):
        return P.music_params(self.queue["queue"][-1]["params"])


class TheSliderOnScreenWins(Studio):
    """M6-01, server half."""

    def test_an_edited_score_takes_the_form_limit(self):
        wav = self.song()
        h = _Handler({"path": str(wav), "abc": "X:1\nK:C\nG|\n", "max_seconds": "60"})
        R.post_music_score_render(h, "/music/score/render", {}, "")
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertEqual(self.queued()["music_max_seconds"], 60)

    def test_a_restyle_takes_the_form_limit(self):
        wav = self.song()
        h = _Handler({"path": str(wav), "kind": "restyle", "style": "punk",
                      "max_seconds": "60"})
        R.post_music_variation(h, "/music/variation", {}, "")
        self.assertTrue(h.payload.get("ok"), h.payload)
        self.assertEqual(self.queued()["music_max_seconds"], 60)

    def test_no_slider_value_still_inherits_the_parents(self):
        wav = self.song()
        R.post_music_score_render(_Handler({"path": str(wav), "abc": "X:1\nK:C\nG|\n"}),
                                  "/music/score/render", {}, "")
        self.assertEqual(self.queued()["music_max_seconds"], 180)


class AnEditedScoreKeepsTheSongsRecipe(Studio):
    """M6-03."""

    def _render(self, wav):
        h = _Handler({"path": str(wav), "abc": "X:1\nK:C\nG|\n"})
        R.post_music_score_render(h, "/music/score/render", {}, "")
        self.assertTrue(h.payload.get("ok"), h.payload)
        return self.queued()

    def test_the_singer_and_the_sound_survive_a_note_change(self):
        p = self._render(self.song())
        self.assertEqual([(x["id"], x["strength"]) for x in p["music_loras"]],
                         [("user/voice.safetensors", 0.8)])
        self.assertEqual(p["music_lora_mode"], "separate")
        self.assertEqual(p["music_cfg_scale"], 1.3)
        self.assertEqual(p["music_precision"], "bf16")
        self.assertFalse(p["music_instrumental"])

    def test_an_instrumental_stays_instrumental(self):
        p = self._render(self.song("music_inst.wav", instrumental=True))
        self.assertTrue(p["music_instrumental"])

    def test_the_recipe_reaches_the_runner(self):
        p = self._render(self.song())
        paths = {"python": Path("/x/py"), "runner": Path("/x/run.py"),
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        argv = P.music_argv({"id": "j", "params": p}, paths, self.out / "o.wav")
        self.assertIn("--lora", argv)
        self.assertEqual(argv[argv.index("--lora-mode") + 1], "separate")
        self.assertIn(":0.8", argv[argv.index("--lora") + 1])


class EveryEditGetsItsOwnScore(Studio):
    """M6-06."""

    def test_two_edits_in_one_second_do_not_share_a_file(self):
        wav = self.song()
        paths = []
        with mock.patch.object(R.time, "strftime", return_value="_20260924_120000"):
            for abc in ("X:1\nK:C\nA|\n", "X:1\nK:C\nB|\n"):
                h = _Handler({"path": str(wav), "abc": abc})
                R.post_music_score_render(h, "/music/score/render", {}, "")
                paths.append(h.payload["abc_path"])
        self.assertNotEqual(paths[0], paths[1])
        jobs = [j["params"]["music_abc_path"] for j in self.queue["queue"]]
        self.assertEqual(jobs, paths)
        self.assertEqual(Path(paths[0]).read_text(), "X:1\nK:C\nA|\n")
        self.assertEqual(Path(paths[1]).read_text(), "X:1\nK:C\nB|\n")


@unittest.skipIf(NODE is None, "node not on PATH")
class TheBrowserSendsTheSlider(unittest.TestCase):
    """M6-01, browser half: both requests carry Max length."""

    def test_restyle_and_score_edit_post_the_slider(self):
        src = (ROOT / "webapp/js/music.js").read_text()
        funcs = "\n".join(extract_function(n, src) for n in (
            "musicRestyleFromSong", "musicRenderEditedScore"))
        script = r"""
const EL = { musicStyle: {value: 'punk'}, musicLyrics: {value: ''},
  musicSeed: {value: '-1'}, musicQuality: {value: 'final'},
  musicTitle: {value: ''}, musicMaxSeconds: {value: '60'},
  songScoreText: {value: 'X:1\nK:C\nG|\n'} };
function _el(id) { return EL[id]; }
function _say() {}
function musicScoreEditToggle() {}
let SONG = { path: '/out/music_song.wav' };
const bodies = [];
global.fetch = async (url, o) => { bodies.push([url, String(o.body)]);
  return { ok: true, json: async () => ({ ok: true }) }; };
""" + funcs + r"""
(async () => {
  await musicRestyleFromSong();
  await musicRenderEditedScore();
  process.stdout.write(JSON.stringify(bodies.map(([u, b]) =>
    [u, new URLSearchParams(b).get('max_seconds')])));
})();
"""
        with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
            fh.write(script)
        try:
            out = subprocess.run([NODE, fh.name], capture_output=True, text=True,
                                 timeout=30)
        finally:
            Path(fh.name).unlink(missing_ok=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout),
                         [["/music/variation", "60"], ["/music/score/render", "60"]])



def _node(script: str):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(script)
    try:
        out = subprocess.run([NODE, fh.name], capture_output=True, text=True, timeout=30)
    finally:
        Path(fh.name).unlink(missing_ok=True)
    if out.returncode:
        raise AssertionError(out.stderr)
    return json.loads(out.stdout)


@unittest.skipIf(NODE is None, "node not on PATH")
class AnActionUsesItsOwnSong(unittest.TestCase):
    """M6-02."""

    SHIM = r"""
const EL = {};
function _el(id) { return EL[id] || (EL[id] = { value: '', textContent: '', innerHTML: '',
  hidden: false, querySelectorAll: () => [] }); }
EL.musicStyle = { value: 'punk' }; EL.musicLyrics = { value: '' };
EL.musicMaxSeconds = { value: '60' };
const said = [];
function _say(m) { said.push(m); }
function _esc(s) { return String(s); }
let _barSong = 1;
function musicBarShow() {}
function _songNameFromFile(n) { return n; }
function _badges() { return ''; }
function musicScoreEditToggle() {}
function _renderLineage() {} function _renderScore() {} function _renderLyrics() {}
function selectOutput(path) { songCardRender({ kind: 'audio', engine: 'music', path, name: path, music: {} }); }
const posted = [];
const held = [];
let failScore = false;
global.fetch = (url, o) => {
  if (url.startsWith('/music/score?')) {
    const path = decodeURIComponent(url.split('path=')[1]);
    return new Promise(res => held.push(() => res(failScore
      ? { ok: false, json: async () => ({ error: 'gone' }) }
      : { ok: true, json: async () => ({ path, abc: 'X:1', has_artifacts: true }) })));
  }
  posted.push([url, new URLSearchParams(String(o.body)).get('path')]);
  return Promise.resolve({ ok: true, json: async () => ({ ok: true }) });
};
const tick = () => new Promise(r => setImmediate(r));
async function releaseAll() { while (held.length) { held.shift()(); for (let i = 0; i < 6; i++) await tick(); } }
"""

    def _run(self, body):
        src = (ROOT / "webapp/js/music.js").read_text()
        funcs = "\n".join(extract_function(n, src) for n in (
            "songCardRender", "_songFor", "musicSongAction",
            "musicRestyleFromSong", "musicCoverFromSong"))
        return _node(self.SHIM + "let SONG = null; let songReq = 0; let songShown = '';\n"
                     + funcs + "\n(async () => { const out = {};\n" + body
                     + "\nprocess.stdout.write(JSON.stringify(out)); })();")

    def test_the_action_waits_for_its_song_and_uses_it(self):
        r = self._run(r"""
SONG = { path: '/out/A.wav', abc: 'X:1' }; songShown = '/out/A.wav';
const act = musicSongAction('/out/B.wav', 'restyle');
await tick();
out.whileLoading = posted.slice();
out.songWhileLoading = SONG;
await releaseAll(); await act;
out.after = posted.slice();
""")
        self.assertEqual(r["whileLoading"], [])
        self.assertIsNone(r["songWhileLoading"])
        self.assertEqual(r["after"], [["/music/variation", "/out/B.wav"]])

    def test_a_song_that_cannot_be_read_queues_nothing(self):
        r = self._run(r"""
SONG = { path: '/out/A.wav', abc: 'X:1' }; songShown = '/out/A.wav';
failScore = true;
const act = musicSongAction('/out/B.wav', 'restyle');
await releaseAll(); await act;
out.posted = posted; out.src = (EL.musicSourceAudio || {}).value || '';
const cover = musicSongAction('/out/B.wav', 'cover');
await releaseAll(); await cover;
out.coverSrc = (EL.musicSourceAudio || {}).value || '';
out.cardButton = (musicRestyleFromSong(), posted.length);
""")
        self.assertEqual(r["posted"], [])
        self.assertEqual(r["coverSrc"], "")
        self.assertEqual(r["cardButton"], 0)


@unittest.skipIf(NODE is None, "node not on PATH")
class TheParentLinkWorks(unittest.TestCase):
    """M6-10."""

    def test_the_link_hands_back_the_exact_path(self):
        src = (ROOT / "webapp/js/music.js").read_text()
        paths = ["/out/music_a.wav", "/out/日本語 song.wav",
                 "/out/a \"quoted\" & <odd> 'name'.wav"]
        r = _node(r"""
const EL = { songLineage: { innerHTML: '' } };
function _el(id) { return EL[id]; }
""" + next(ln for ln in src.splitlines() if ln.startswith("function _esc("))
            + "\n" + extract_function("_renderLineage", src) + r"""
const got = [];
function musicOpenSong(p) { got.push(p); }
const unesc = s => s.replace(/&quot;/g, '"').replace(/&#39;/g, "'").replace(/&lt;/g, '<')
  .replace(/&gt;/g, '>').replace(/&amp;/g, '&');
for (const p of """ + json.dumps(paths) + r""") {
  _renderLineage({ parent: p, variation: 'take' });
  const m = EL.songLineage.innerHTML.match(/onclick="([^"]*)"/);
  new Function('musicOpenSong', unesc(m[1]))(musicOpenSong);
}
process.stdout.write(JSON.stringify(got));
""")
        self.assertEqual(r, paths)


if __name__ == "__main__":
    unittest.main()
