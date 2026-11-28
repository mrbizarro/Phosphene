"""A seed given to the agent tools reaches the song and the spoken line.

Music: the client posted `seed`, but the studio's music_params reads `music_seed` (the
field the page posts), so every song got a random seed. Speech: the client had no seed
at all, though /speech/generate takes one and the CLI offered --seed.

Offline: a fake studio on an ephemeral localhost port records what the client posts, and
the studio's OWN parsers then read that request — music_params lifted out of
mlx_ltx_panel.py with `ast` (the panel is never imported or run), the /speech/generate
route's _agent_form lifted out of panel/routes_speech.py, and speech_engine.speech_params
(a pure module). If either side drifts, these tests break.

    python3 -B -m unittest discover -s tests -p 'test_agent_seed.py' -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import sys
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "agents"))
sys.path.insert(0, str(REPO))

import phosphene_cli  # noqa: E402
import phosphene_client  # noqa: E402
import phosphene_mcp  # noqa: E402
import speech_engine  # noqa: E402  (pure logic, no panel globals)

PANEL = REPO / "mlx_ltx_panel.py"
ROUTES_SPEECH = REPO / "panel" / "routes_speech.py"


def _lift(path: Path, names: set[str], ns: dict) -> dict:
    """exec only the named top-level defs / assignments of a module (parsed, never imported)."""
    found = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        name = (node.name if isinstance(node, (ast.FunctionDef, ast.ClassDef))
                else node.targets[0].id if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                else None)
        if name in names:
            exec(compile(ast.Module([node], []), str(path), "exec"), ns)
            found.add(name)
    missing = names - found
    if missing:
        raise AssertionError(f"{sorted(missing)} no longer top-level in {path.name}")
    return ns


_MUSIC = _lift(PANEL, {"music_params", "MUSIC_QUALITY_STEPS"},
               {"normalize_pasted_path": lambda p: p, "music_lora_picks": lambda v: []})
_SPEECH_ROUTE = _lift(ROUTES_SPEECH, {"_agent_form"}, {})


def studio_music_seed(form: dict) -> int:
    """The seed make_job's music branch gives the song for this /queue/add form."""
    return _MUSIC["music_params"]({k: [str(v)] for k, v in form.items()})["music_seed"]


def studio_speech_seed(spec: dict) -> int:
    """The seed /speech/generate gives the line for this JSON body."""
    return speech_engine.speech_params(_SPEECH_ROUTE["_agent_form"](spec))["speech_seed"]


class FakeStudio:
    def __init__(self):
        self.seen: list[tuple[str, dict]] = []
        studio = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, doc, code=200):
                b = json.dumps(doc).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

            def do_GET(self):
                if self.path.startswith("/agents/info"):
                    return self._send({"app": "phosphene", "api": 1})
                return self._send({})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0)).decode()
                if self.headers.get("Content-Type", "").startswith("application/json"):
                    data = json.loads(body)
                else:
                    data = {k: v[0] for k, v in urllib.parse.parse_qs(body, keep_blank_values=True).items()}
                studio.seen.append((self.path, data))
                if self.path.startswith("/speech/generate"):
                    return self._send({"ok": True, "queued": True, "job_id": "sp1"}, 202)
                return self._send({"ok": True, "id": "job1"})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def last(self, path: str) -> dict:
        hits = [d for p, d in self.seen if p.startswith(path)]
        assert len(hits) == 1, self.seen
        return hits[0]


class ServerSideStillAsReported(unittest.TestCase):
    def test_make_job_builds_music_from_music_params(self):
        self.assertIn("params = music_params(form)", PANEL.read_text(encoding="utf-8"))

    def test_music_reads_music_seed_not_seed(self):
        self.assertEqual(studio_music_seed({"music_style": "x", "seed": 42}), -1)
        self.assertEqual(studio_music_seed({"music_style": "x", "music_seed": 42}), 42)

    def test_the_page_posts_the_same_fields(self):
        # The studio's own page is the reference caller of these forms.
        self.assertIn("music_seed:", (REPO / "webapp/js/characters.js").read_text(encoding="utf-8"))
        self.assertIn("fd.set('speech_seed'", (REPO / "webapp/js/speech.js").read_text(encoding="utf-8"))

    def test_speech_route_takes_seed(self):
        self.assertEqual(studio_speech_seed({"text": "hi", "seed": 42}), 42)
        self.assertEqual(studio_speech_seed({"text": "hi"}), -1)


class _Base(unittest.TestCase):
    def setUp(self):
        self.studio = FakeStudio()
        self.ph = phosphene_client.Phosphene(self.studio.url)

    def tearDown(self):
        self.studio.close()

    def cli(self, *argv) -> int:
        with contextlib.redirect_stdout(io.StringIO()):
            return phosphene_cli.main(["--url", self.studio.url, *argv])

    def mcp(self, tool: str, **args) -> dict:
        return phosphene_mcp.call_tool(phosphene_mcp.Tools(self.studio.url), tool, args)


class MusicSeed(_Base):
    def test_seed_reaches_the_song(self):
        self.ph.music("lofi piano", seed=42)
        self.assertEqual(studio_music_seed(self.studio.last("/queue/add")), 42)

    def test_boundary_seeds(self):
        # 0 is a seed, not "no seed"; -1 is the studio's "random"; the studio clamps the top.
        for seed, want in ((0, 0), (-1, -1), (2**32 - 1, 2**32 - 1), (2**32, 2**32 - 1)):
            with self.subTest(seed=seed):
                self.studio.seen.clear()
                self.ph.music("lofi piano", seed=seed)
                self.assertEqual(studio_music_seed(self.studio.last("/queue/add")), want)

    def test_no_seed_is_random_and_sends_none(self):
        self.ph.music("lofi piano")
        form = self.studio.last("/queue/add")
        self.assertNotIn("music_seed", form)
        self.assertNotIn("seed", form)
        self.assertEqual(studio_music_seed(form), -1)

    def test_the_rest_of_the_song_unchanged(self):
        self.ph.music("lofi piano", lyrics="la la", seconds=60, title="Rain", seed=7)
        p = _MUSIC["music_params"]({k: [v] for k, v in self.studio.last("/queue/add").items()})
        self.assertEqual((p["music_style"], p["music_lyrics"], p["music_max_seconds"], p["music_title"],
                          p["music_instrumental"], p["music_seed"]),
                         ("lofi piano", "la la", 60, "Rain", False, 7))

    def test_cli(self):
        self.assertEqual(self.cli("music", "lofi piano", "--seed", "42"), 0)
        self.assertEqual(studio_music_seed(self.studio.last("/queue/add")), 42)

    def test_mcp(self):
        r = self.mcp("phosphene_make_music", style="lofi piano", seed=42)
        self.assertFalse(r["isError"], r)
        self.assertEqual(studio_music_seed(self.studio.last("/queue/add")), 42)


class SpeechSeed(_Base):
    def test_seed_reaches_the_line(self):
        self.ph.speech("Hello there.", seed=42)
        self.assertEqual(studio_speech_seed(self.studio.last("/speech/generate")), 42)

    def test_boundary_seeds(self):
        for seed, want in ((0, 0), (-1, -1), (2**31 - 1, 2**31 - 1), (2**31, 2**31 - 1)):
            with self.subTest(seed=seed):
                self.studio.seen.clear()
                self.ph.speech("Hello there.", seed=seed)
                self.assertEqual(studio_speech_seed(self.studio.last("/speech/generate")), want)

    def test_no_seed_is_random_and_sends_none(self):
        self.ph.speech("Hello there.")
        spec = self.studio.last("/speech/generate")
        self.assertNotIn("seed", spec)
        self.assertEqual(studio_speech_seed(spec), -1)

    def test_voice_and_speed_unchanged(self):
        self.ph.speech("Hello there.", voice="aiden", speed=1.25, seed=3)
        p = speech_engine.speech_params(_SPEECH_ROUTE["_agent_form"](self.studio.last("/speech/generate")))
        self.assertEqual((p["speech_text"], p["speech_voice"], p["speech_speed"], p["speech_seed"]),
                         ("Hello there.", "aiden", 1.25, 3))
        self.assertIs(self.studio.last("/speech/generate")["wait"], False)

    def test_cli(self):
        self.assertEqual(self.cli("speech", "Hello there.", "--seed", "42"), 0)
        self.assertEqual(studio_speech_seed(self.studio.last("/speech/generate")), 42)

    def test_mcp(self):
        r = self.mcp("phosphene_make_speech", text="Hello there.", seed=42)
        self.assertFalse(r["isError"], r)
        self.assertEqual(studio_speech_seed(self.studio.last("/speech/generate")), 42)

    def test_mcp_schema_offers_seed(self):
        tool = next(t for t in phosphene_mcp.TOOLS if t["name"] == "phosphene_make_speech")
        self.assertIs(tool["inputSchema"]["additionalProperties"], False)
        self.assertEqual(tool["inputSchema"]["properties"]["seed"]["type"], "integer")


class VideoAndImageSeedUntouched(_Base):
    # make_job reads `seed` for these (normalize_seed(f("seed", "-1"))).
    def test_video(self):
        self.ph.video("a tram", seed=42)
        self.assertEqual(self.studio.last("/queue/add")["seed"], "42")

    def test_image(self):
        self.ph.image("a tram", seed=0)
        self.assertEqual(self.studio.last("/queue/add")["seed"], "0")

    def test_make_job_still_reads_seed_for_them(self):
        self.assertIn('normalize_seed(f("seed", "-1"))', PANEL.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
