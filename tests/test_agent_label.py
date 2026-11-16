"""A label given to the agent tools names the job on the studio's queue card.

The client posted `label`, but make_job reads a video's and an image's queue label from
`preset_label` (the page's hidden field) and drops every key outside its allowlist, so
`phosphene video ... --label X` showed the prompt instead. Speech and music jobs are
named by the studio itself, so --label on those did nothing; the CLI no longer offers it.

Offline: a fake studio on an ephemeral localhost port records what the client posts, and
make_job's OWN label expressions, together with its own form reader `f`, are lifted out
of mlx_ltx_panel.py with `ast` (the panel is never imported or run) and evaluated on that
form. If either side drifts, these tests break.

    python3 -B -m unittest discover -s tests -p 'test_agent_label.py' -v
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

import phosphene_cli  # noqa: E402
import phosphene_client  # noqa: E402
import phosphene_mcp  # noqa: E402

PANEL = REPO / "mlx_ltx_panel.py"
_TREE = ast.parse(PANEL.read_text(encoding="utf-8"))
_MAKE_JOB = next(n for n in _TREE.body if isinstance(n, ast.FunctionDef) and n.name == "make_job")
# make_job's form reader, as written there (`form` is make_job's argument).
_F = next(n for n in _MAKE_JOB.body if isinstance(n, ast.FunctionDef) and n.name == "f")
# Every `"label": <expression over f(...)>` in a params dict make_job builds: the image
# branch and the video branch. (Training's comes from its own preset; speech and music
# set params["label"] from the text.)
_LABEL_EXPRS = [v for d in ast.walk(_MAKE_JOB) if isinstance(d, ast.Dict)
                for k, v in zip(d.keys, d.values)
                if isinstance(k, ast.Constant) and k.value == "label"
                and any(isinstance(c, ast.Call) and getattr(c.func, "id", None) == "f" for c in ast.walk(v))]


def studio_labels(form: dict) -> list:
    """What each make_job label expression gives for this /queue/add form."""
    ns = {"form": {k: [v] for k, v in form.items()}, "PASTED_PATH_FIELDS": (),
          "normalize_pasted_path": lambda p: p}
    exec(compile(ast.Module([_F], []), str(PANEL), "exec"), ns)
    return [eval(compile(ast.Expression(e), str(PANEL), "eval"), ns) for e in _LABEL_EXPRS]


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

    def queued(self) -> dict:
        hits = [d for p, d in self.seen if p.startswith("/queue/add")]
        assert len(hits) == 1, self.seen
        return hits[0]


class ServerSideStillAsReported(unittest.TestCase):
    def test_image_and_video_read_preset_label(self):
        self.assertEqual(len(_LABEL_EXPRS), 2, [ast.unparse(e) for e in _LABEL_EXPRS])
        self.assertEqual(studio_labels({"preset_label": "X"}), ["X", "X"])

    def test_a_label_key_is_dropped(self):
        self.assertEqual(studio_labels({"label": "X"}), [None, None])

    def test_the_page_posts_preset_label(self):
        self.assertIn('name="preset_label"', (REPO / "webapp/index.html").read_text(encoding="utf-8"))


class _Base(unittest.TestCase):
    def setUp(self):
        self.studio = FakeStudio()
        self.ph = phosphene_client.Phosphene(self.studio.url)

    def tearDown(self):
        self.studio.close()

    def cli(self, *argv) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return phosphene_cli.main(["--url", self.studio.url, *argv])


class VideoAndImageLabel(_Base):
    def test_video_label_reaches_the_job(self):
        self.ph.video("a tram", label="Tram, take 2")
        self.assertEqual(studio_labels(self.studio.queued()), ["Tram, take 2"] * 2)

    def test_h3_video_label(self):
        self.ph.video("a tram", engine="h3", quality="draft", seconds=5, label="Tram")
        self.assertEqual(studio_labels(self.studio.queued()), ["Tram"] * 2)

    def test_image_label_reaches_the_job(self):
        self.ph.image("a poster", label="Poster A")
        self.assertEqual(studio_labels(self.studio.queued()), ["Poster A"] * 2)

    def test_no_label_or_empty_label_sends_none(self):
        for label in (None, ""):
            with self.subTest(label=label):
                self.studio.seen.clear()
                self.ph.video("a tram", label=label)
                form = self.studio.queued()
                self.assertFalse([k for k in form if "label" in k], form)
                self.assertEqual(studio_labels(form), [None, None])

    def test_non_ascii_label(self):
        self.ph.image("a poster", label="Affiche · été")
        self.assertEqual(studio_labels(self.studio.queued()), ["Affiche · été"] * 2)

    def test_extra_preset_label_still_wins(self):
        # `extra` is applied last, as before: a caller that already sent preset_label keeps it.
        self.ph.video("a tram", label="ignored", extra={"preset_label": "Mine"})
        self.assertEqual(studio_labels(self.studio.queued()), ["Mine"] * 2)

    def test_cli_video(self):
        self.assertEqual(self.cli("video", "a tram", "--label", "Tram"), 0)
        self.assertEqual(studio_labels(self.studio.queued()), ["Tram"] * 2)

    def test_cli_image(self):
        self.assertEqual(self.cli("image", "a poster", "--label", "Poster"), 0)
        self.assertEqual(studio_labels(self.studio.queued()), ["Poster"] * 2)

    def test_mcp_video(self):
        r = phosphene_mcp.call_tool(phosphene_mcp.Tools(self.studio.url), "phosphene_make_video",
                                    {"prompt": "a tram", "label": "Tram"})
        self.assertFalse(r["isError"], r)
        self.assertEqual(studio_labels(self.studio.queued()), ["Tram"] * 2)


class SpeechAndMusicOfferNoLabel(_Base):
    def test_cli_refuses_label_on_speech_and_music(self):
        for argv in (["speech", "Hello there."], ["music", "lofi piano"]):
            with self.subTest(cmd=argv[0]):
                with self.assertRaises(SystemExit) as cm:
                    self.cli(*argv, "--label", "X")
                self.assertEqual(cm.exception.code, 2)   # bad arguments, nothing sent
                self.assertEqual(self.studio.seen, [])

    def test_speech_and_music_still_work_without_it(self):
        self.assertEqual(self.cli("speech", "Hello there.", "--seed", "1"), 0)
        self.assertEqual(self.cli("music", "lofi piano", "--title", "Rain"), 0)
        self.assertEqual([p for p, _ in self.studio.seen], ["/speech/generate", "/queue/add"])

    def test_mcp_schemas_agree(self):
        props = {t["name"]: t["inputSchema"]["properties"] for t in phosphene_mcp.TOOLS}
        self.assertIn("label", props["phosphene_make_video"])
        self.assertNotIn("label", props["phosphene_make_speech"])
        self.assertNotIn("label", props["phosphene_make_music"])


class ApiDocSaysWhatTheStudioReads(unittest.TestCase):
    def test_queue_add_table(self):
        doc = (REPO / "docs/API.md").read_text(encoding="utf-8")
        self.assertIn("| `preset_label` | string |", doc)
        self.assertNotIn("| `label` | string | Optional UI label for the queue card. |", doc)


if __name__ == "__main__":
    unittest.main()
