#!/usr/bin/env python3
"""What Compose sends, and what the panel refuses rather than render wrong.

  * M6-05  LoRA picks went out as `id:strength` joined by commas; a filename
           with a comma split into two ids nobody has, both were dropped, and
           the song came back without the ticked voice.
  * M6-07  Cover with no recording was indistinguishable from Write and
           composed a new song instead of refusing.
  * M6-09  The LoRA / cover-model downloads reported only their start; the
           next status tick painted the Download offer back and a failure
           never reached the screen.

Browser halves run in node against a DOM stub; server halves run the real
make_job / music_params / music_argv / music_status against scratch folders.
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
from urllib.parse import parse_qs, urlencode

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import mlx_ltx_panel as P                                            # noqa: E402
from scripts.extract_panel_js import extract_function                # noqa: E402

NODE = shutil.which("node")
CHARS = (ROOT / "webapp/js/characters.js").read_text()

NAMES = ["user/Voice, warm.safetensors", "user/日本 語:take 2.safetensors",
         "user/plain.safetensors"]


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


DOM = r"""
const mk = (extra) => Object.assign({ hidden: false, innerHTML: '', value: '', textContent: '',
  checked: false, open: false, dataset: {}, querySelectorAll: () => [] }, extra || {});
const els = {};
const document = { getElementById: id => els[id] || (els[id] = mk()),
                   querySelectorAll: () => [] };
function row(id, on, strength) {
  const cb = { checked: on }, rg = { value: String(strength) };
  return { dataset: { id }, querySelector: sel => sel.includes('checkbox') ? cb : rg };
}
const charactersEscapeHtml = s => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
"""


class Sandbox(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.loras = self.root / "loras"
        (self.loras / "user").mkdir(parents=True)
        for n in NAMES:
            (self.loras / n).write_bytes(b"x" * 64)
        p = mock.patch.object(P, "MUSIC_LORAS", self.loras)
        p.start()
        self.addCleanup(p.stop)


@unittest.skipIf(NODE is None, "node not on PATH")
class PicksSurviveTheirFilenames(Sandbox):
    """M6-05."""

    def _browser_field(self):
        funcs = extract_function("musicLoraPicks", CHARS)
        rows = json.dumps([[n, True, s] for n, s in zip(NAMES, (0.8, 1.2, 0.5))])
        return _node(DOM + funcs + r"""
els.musicLoraList = mk({ querySelectorAll: () =>
  """ + rows + r""".map(([id, on, s]) => row(id, on, s)).concat([row('user/off.safetensors', false, 1)]) });
process.stdout.write(JSON.stringify(musicLoraPicks()));
""")

    def test_the_exact_files_and_strengths_reach_the_runner(self):
        field = self._browser_field()
        form = parse_qs(urlencode({"mode": "music", "music_style": "dub",
                                   "music_loras": field}))
        picks = P.music_params(form)["music_loras"]
        self.assertEqual([(p["id"], p["strength"]) for p in picks],
                         list(zip(NAMES, (0.8, 1.2, 0.5))))
        job = P.make_job(form)
        paths = {"python": Path("/x/py"), "runner": Path("/x/run.py"),
                 "generator": Path("/x/gen"), "vae": Path("/x/vae")}
        argv = P.music_argv(job, paths, self.root / "o.wav")
        loras = [argv[i + 1] for i, a in enumerate(argv) if a == "--lora"]
        self.assertEqual(loras, [f"{(self.loras / n).resolve()}:{s:g}"
                                 for n, s in zip(NAMES, (0.8, 1.2, 0.5))])

    def test_a_pick_that_does_not_resolve_is_refused_not_dropped(self):
        form = parse_qs(urlencode({"mode": "music", "music_style": "dub",
                                   "music_loras": json.dumps(
                                       [{"id": "user/gone.safetensors", "strength": 1}])}))
        with self.assertRaises(P.MusicRequestError) as cm:
            P.make_job(form)
        self.assertIn("user/gone.safetensors", str(cm.exception))

    def test_queue_add_answers_the_refusal_with_a_400(self):
        from panel import routes_queue
        routes_queue.P = P

        class H:
            def _read_form_body(self):
                return b"", {"mode": ["music"], "music_style": ["dub"],
                             "music_loras": ['[{"id": "user/gone.safetensors"}]']}

            def _json(self, payload, status=200):
                self.payload, self.status = payload, status

        h = H()
        with mock.patch.object(P, "STATE", {"queue": []}), \
             mock.patch.object(P, "persist_queue", lambda: None):
            routes_queue.post_run(h, "/queue/add", {}, "")
        self.assertEqual(h.status, 400)
        self.assertIn("not in the music LoRA folder", h.payload["error"])

    def test_the_summary_reads_the_json(self):
        funcs = "\n".join(extract_function(n, CHARS) for n in
                          ("musicLoraPicks", "musicLoraSummaryText"))
        out = _node(DOM + funcs + r"""
els.musicInstrumental = mk({ checked: false });
els.musicLoraList = mk({ querySelectorAll: () => [row('user/Voice, warm.safetensors', true, 0.8)] });
process.stdout.write(JSON.stringify((musicLoraSummaryText(), els.musicLoraSummary.textContent)));
""")
        self.assertTrue(out.endswith("@ 0.80"), out)


@unittest.skipIf(NODE is None, "node not on PATH")
class CoverNeedsARecording(Sandbox):
    """M6-07."""

    def test_the_composer_queues_nothing_without_a_recording(self):
        funcs = "\n".join(extract_function(n, CHARS) for n in
                          ("musicGenerate", "musicFormParams", "musicCfgValue",
                           "musicLoraPicks"))
        out = _node(DOM + r"""
const window = { _ENGINE_PROBES: { music: { available: true, cover: { ready: false } } } };
let musicBusy = false;
function musicSimpleActive() { return false; }
function musicFormChanged() {}
const posted = [];
global.fetch = async (url) => { posted.push(url); return { ok: true, json: async () => ({}) }; };
els.musicTask = mk({ value: 'cover' });
els.musicInstrumental = mk({ checked: false });
els.musicLyrics = mk({ value: 'la la' }); els.musicStyle = mk({ value: 'folk' });
els.musicMode = mk({ value: 'full' }); els.musicMaxSeconds = mk({ value: '120' });
els.musicSeed = mk({ value: '-1' }); els.musicQuality = mk({ value: 'final' });
els.musicSourceAudio = mk({ value: '   ' });
""" + funcs + r"""
(async () => {
  await musicGenerate();
  process.stdout.write(JSON.stringify({ posted, status: els.musicStatus.textContent,
    opened: els.musicCoverDetails.open,
    task: musicFormParams().get('music_task') }));
})();
""")
        self.assertEqual(out["posted"], [])
        self.assertIn("recording", out["status"])
        self.assertTrue(out["opened"])
        self.assertEqual(out["task"], "cover")

    def _job(self, **form):
        base = {"mode": "music", "music_style": "folk"}
        base.update(form)
        return P.make_job(parse_qs(urlencode(base)))

    def test_the_server_refuses_an_empty_or_missing_recording(self):
        for src in ("", "   ", str(self.root / "nope.wav")):
            with self.assertRaises(P.MusicRequestError, msg=repr(src)):
                self._job(music_task="cover", music_source_audio=src)

    def test_a_real_recording_is_a_cover_and_write_needs_none(self):
        rec = self.root / "take.wav"
        rec.write_bytes(b"RIFF")
        job = self._job(music_task="cover", music_source_audio=str(rec))
        self.assertEqual(job["params"]["music_source_audio"], str(rec))
        self.assertEqual(job["params"]["music_task"], "cover")
        self.assertEqual(self._job(music_task="write")["params"]["music_source_audio"], "")
        # A caller that predates the field is judged as before.
        self.assertEqual(self._job()["params"]["music_task"], "")


class DownloadsSayWhatTheyAreDoing(unittest.TestCase):
    """M6-09."""

    def test_status_carries_both_fetch_states(self):
        with mock.patch.dict(P._MUSIC_LORA_FETCH, running=True, line="adapter 1/2", error=""), \
             mock.patch.dict(P._MUSIC_COVER_FETCH, running=False, line="",
                             error="the transcription models failed (exit 1)"):
            st = P.music_status()
        self.assertEqual(st["loras"]["fetch"]["line"], "adapter 1/2")
        self.assertTrue(st["loras"]["fetch"]["running"])
        self.assertIn("exit 1", st["cover"]["fetch"]["error"])

    @unittest.skipIf(NODE is None, "node not on PATH")
    def test_the_cards_show_progress_failure_and_retry(self):
        funcs = "\n".join(extract_function(n, CHARS) for n in
                          ("musicFetchCard", "musicLoraInstallCard",
                           "musicCoverInstallCard"))
        out = _node(DOM + funcs + r"""
els.musicInstrumental = mk({ checked: true });
els.musicSourceAudio = mk({ value: '/rec.wav' });
const html = [];
for (const fetch of [{ running: true, line: 'adapter 1/2' },
                     { running: false, error: 'disk <full>' }, {}]) {
  musicLoraInstallCard({ loras: { instrumental_ready: false, bytes: 2.1e8, fetch } });
  musicCoverInstallCard({ cover: { ready: false, bytes: 2.8e9, fetch } });
  html.push([els.musicLoraInstall.innerHTML, els.musicCoverInstall.innerHTML]);
}
process.stdout.write(JSON.stringify(html));
""")
        running, failed, idle = out
        for card in running:
            self.assertIn("Downloading", card)
            self.assertNotIn("Download it", card)
            self.assertNotIn("Download them", card)
        self.assertIn("adapter 1/2", running[0])
        for card in failed:
            self.assertIn("failed", card)
            self.assertIn("disk &lt;full&gt;", card)
            self.assertIn("Try again", card)
        self.assertIn('onclick="musicLoraInstall()"', failed[0])
        self.assertIn('onclick="musicCoverInstall()"', failed[1])
        self.assertIn("Download it", idle[0])
        self.assertIn("Download them", idle[1])


if __name__ == "__main__":
    unittest.main()
