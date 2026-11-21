"""Fetching a job into the folder its files already live in answers with their paths.

`phosphene fetch JOB --out <the studio's outputs folder>` (or `wait --out`, or the MCP
phosphene_fetch tool with that `dest`) copied each file onto itself, and
shutil.copy2 raises SameFileError there. It is not a PhospheneError, so the CLI printed
a traceback and the MCP server answered with a JSON-RPC internal error.

Offline: a fake studio on an ephemeral localhost port answers /status with one finished
job whose files sit in a temporary folder; nothing else is touched.

    python3 -B -m unittest discover -s tests -p 'test_agent_fetch.py' -v
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
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

CLIP = b"\x00\x00\x00\x18ftypmp42 a finished clip"


class FakeStudio:
    """/status with one finished job (`row`); /file serves a path it was asked for."""

    def __init__(self, row: dict, served: dict | None = None):
        self.row = row
        self.served = served or {}
        studio = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, body: bytes, code=200, ctype="application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path.startswith("/agents/info"):
                    return self._send(json.dumps({"app": "phosphene", "api": 1}).encode())
                if self.path.startswith("/status"):
                    return self._send(json.dumps({"current": None, "queue": [], "history": [studio.row]}).encode())
                if self.path.startswith("/file?"):
                    q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                    body = studio.served.get(q.get("path", [""])[0])
                    return self._send(body, ctype="video/mp4") if body is not None else self._send(b"{}", 404)
                return self._send(b"{}")

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.outputs = self.tmp / "mlx_outputs"
        self.outputs.mkdir()
        self.clip = self.outputs / "tram_0001.mp4"
        self.clip.write_bytes(CLIP)
        self.studio = None
        self.serve({"id": "j1", "status": "done", "output_path": str(self.clip)})

    def serve(self, row: dict, served: dict | None = None):
        if self.studio:
            self.studio.close()
        self.studio = FakeStudio(row, served)
        self.ph = phosphene_client.Phosphene(self.studio.url)

    def tearDown(self):
        self.studio.close()

    def assertUntouched(self, path: Path = None):
        self.assertEqual((path or self.clip).read_bytes(), CLIP)


class FetchIntoTheSameFolder(_Base):
    def test_the_folder_it_lives_in(self):
        self.assertEqual(self.ph.fetch("j1", str(self.outputs)), [str(self.clip)])
        self.assertUntouched()
        self.assertEqual(sorted(os.listdir(self.outputs)), ["tram_0001.mp4"])

    def test_other_spellings_of_that_folder(self):
        link = self.tmp / "link_to_outputs"
        link.symlink_to(self.outputs, target_is_directory=True)
        for dest in (str(self.outputs) + "/", os.path.join(str(self.outputs), "."), str(link)):
            with self.subTest(dest=dest):
                saved = self.ph.fetch("j1", dest)
                self.assertEqual(len(saved), 1)
                self.assertTrue(os.path.samefile(saved[0], self.clip))
                self.assertUntouched()

    def test_relative_dest(self):
        cwd = os.getcwd()
        os.chdir(self.outputs)
        self.addCleanup(os.chdir, cwd)
        self.assertEqual(self.ph.fetch("j1", "."), ["tram_0001.mp4"])
        self.assertUntouched()

    def test_home_relative_dest(self):
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old is not None else os.environ.pop("HOME"))
        self.assertEqual(self.ph.fetch("j1", "~/mlx_outputs"), [str(self.clip)])
        self.assertUntouched()

    def test_a_hard_link_already_in_dest(self):
        dest = self.tmp / "project"
        dest.mkdir()
        os.link(self.clip, dest / self.clip.name)
        self.assertEqual(self.ph.fetch("j1", str(dest)), [str(dest / self.clip.name)])
        self.assertUntouched()
        self.assertUntouched(dest / self.clip.name)

    def test_a_batch_some_already_there(self):
        # An image batch: output_path is the first picture, candidate_paths all of them.
        other = self.tmp / "elsewhere"
        other.mkdir()
        far = other / "tram_0002.png"
        far.write_bytes(b"png two")
        self.serve({"id": "j1", "status": "done", "output_path": str(self.clip),
                    "params": {"candidate_paths": [str(self.clip), str(far)]}})
        saved = self.ph.fetch("j1", str(self.outputs))
        self.assertEqual(saved, [str(self.clip), str(self.outputs / "tram_0002.png")])
        self.assertUntouched()
        self.assertEqual((self.outputs / "tram_0002.png").read_bytes(), b"png two")
        self.assertEqual(far.read_bytes(), b"png two")


class FetchElsewhereUnchanged(_Base):
    def test_copies_into_another_folder(self):
        dest = self.tmp / "project" / "clips"
        saved = self.ph.fetch("j1", str(dest))
        self.assertEqual(saved, [str(dest / "tram_0001.mp4")])
        self.assertEqual((dest / "tram_0001.mp4").read_bytes(), CLIP)
        self.assertUntouched()

    def test_replaces_a_different_file_of_the_same_name(self):
        dest = self.tmp / "project"
        dest.mkdir()
        (dest / "tram_0001.mp4").write_bytes(b"an older take")
        self.ph.fetch("j1", str(dest))
        self.assertEqual((dest / "tram_0001.mp4").read_bytes(), CLIP)

    def test_a_file_only_the_studio_can_read_is_downloaded(self):
        remote = "/somewhere/the/client/cannot/see/tram_0003.mp4"
        self.serve({"id": "j1", "status": "done", "output_path": remote}, served={remote: b"from /file"})
        dest = self.tmp / "dl"
        self.assertEqual(self.ph.fetch("j1", str(dest)), [str(dest / "tram_0003.mp4")])
        self.assertEqual((dest / "tram_0003.mp4").read_bytes(), b"from /file")

    def test_not_done_is_still_a_sentence(self):
        self.serve({"id": "j1", "status": "failed", "error": "out of memory"})
        with self.assertRaises(phosphene_client.PhospheneError):
            self.ph.fetch("j1", str(self.outputs))


class CliEntryPoint(_Base):
    def cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            rc = phosphene_cli.main(["--url", self.studio.url, *argv])
        return rc, out.getvalue()

    def test_fetch_into_its_own_folder(self):
        rc, out = self.cli("fetch", "j1", "--out", str(self.outputs))
        self.assertEqual((rc, out.strip()), (0, str(self.clip)))
        self.assertUntouched()

    def test_fetch_json(self):
        rc, out = self.cli("--json", "fetch", "j1", "--out", str(self.outputs))
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out), {"ok": True, "files": [str(self.clip)]})

    def test_wait_out_into_its_own_folder(self):
        rc, out = self.cli("wait", "j1", "--out", str(self.outputs))
        self.assertEqual(rc, 0)
        self.assertIn(str(self.clip), out)
        self.assertUntouched()


class McpEntryPoint(_Base):
    def test_tool_result(self):
        r = phosphene_mcp.call_tool(phosphene_mcp.Tools(self.studio.url), "phosphene_fetch",
                                    {"job_id": "j1", "dest": str(self.outputs)})
        self.assertFalse(r["isError"], r)
        self.assertEqual(r["structuredContent"], {"ok": True, "files": [str(self.clip)]})
        self.assertUntouched()

    def test_over_stdio_no_protocol_error(self):
        req = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
               "params": {"name": "phosphene_fetch", "arguments": {"job_id": "j1", "dest": str(self.outputs)}}}
        out = io.StringIO()
        with contextlib.redirect_stderr(io.StringIO()):
            phosphene_mcp.serve(stdin=io.StringIO(json.dumps(req) + "\n"), stdout=out, url=self.studio.url)
        reply = json.loads(out.getvalue().strip())
        self.assertEqual(reply["id"], 7)
        self.assertNotIn("error", reply)
        self.assertEqual(reply["result"]["structuredContent"]["files"], [str(self.clip)])


if __name__ == "__main__":
    unittest.main()
