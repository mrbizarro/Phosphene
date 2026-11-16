"""An H3 clip made from the agent tools is the length that was asked for.

The studio composes the H3 shape from two form fields, `h3_quality` and `h3_length`, and
renders its 3 s draft when either is missing or unknown (h3_compose_tier / make_job in
mlx_ltx_panel.py). The client used to send the length alone when no quality was given,
so `phosphene video ... --engine h3 --seconds 10` made a 3 s clip.

Offline: a fake studio on an ephemeral localhost port records what the client posts, and
the studio's OWN resolver functions, lifted out of mlx_ltx_panel.py with `ast` (the panel
is never imported or run), turn that form into the cell that would render. If either side
drifts, these tests break.

    python3 -B -m unittest discover -s tests -p 'test_agent_h3_length.py' -v
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
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

PANEL = REPO / "mlx_ltx_panel.py"
_SRC = PANEL.read_text(encoding="utf-8")
_TREE = ast.parse(_SRC)


def _top(name: str):
    for node in _TREE.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name == name:
            return node
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return node
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return node
    raise AssertionError(f"{name} is no longer a top-level name in mlx_ltx_panel.py")


def _axis(fn_name: str) -> dict[str, bool]:
    """The keys of the `out` table an H3 axis builder returns, and whether each is offered
    unconditionally (a literal True; an env-gated canvas is off on a fresh install)."""
    fn = _top(fn_name)
    for node in ast.walk(fn):
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == "out":
            table = node.value
            break
    else:
        raise AssertionError(f"{fn_name} no longer builds an `out` table")
    axis = {}
    for key, cell in zip(table.keys, table.values):
        offered = False
        for k, v in zip(cell.keys, cell.values):
            if isinstance(k, ast.Constant) and k.value == "offered":
                offered = isinstance(v, ast.Constant) and v.value is True
        axis[key.value] = offered
    return axis


QUALITIES = _axis("_h3_qualities")
LENGTHS = _axis("_h3_lengths")
OFFERED_QUALITIES = [k for k, on in QUALITIES.items() if on]
OFFERED_LENGTHS = [k for k, on in LENGTHS.items() if on]
TIER_DEFAULT = ast.literal_eval(_top("H3_TIER_DEFAULT").value)
QUALITY_DEFAULT = ast.literal_eval(_top("H3_QUALITY_DEFAULT").value)

# The studio's own resolvers, run on a table with every cell _build_h3_tiers makes.
_NS: dict = {"H3_TIERS": {f"{q}_{ln}": {} for q in QUALITIES for ln in LENGTHS},
             "H3_TIER_ALIASES": ast.literal_eval(_top("H3_TIER_ALIASES").value)}
for _name in ("h3_compose_tier", "h3_resolve_tier"):
    exec(compile(ast.Module([_top(_name)], []), str(PANEL), "exec"), _NS)

# make_job's order: the two axes, else the composite h3_tier, else the default.
MAKE_JOB_RESOLUTION = ('_h3_tier = (h3_compose_tier(f("h3_quality", ""), f("h3_length", ""))\n'
                       '                or h3_resolve_tier(f("h3_tier", ""))\n'
                       '                or H3_TIER_DEFAULT)')


def studio_cell(form: dict) -> str:
    """The H3 cell the studio renders for this /queue/add form."""
    return (_NS["h3_compose_tier"](form.get("h3_quality", ""), form.get("h3_length", ""))
            or _NS["h3_resolve_tier"](form.get("h3_tier", ""))
            or TIER_DEFAULT)


class FakeStudio:
    """Answers like a studio on 127.0.0.1:<ephemeral>; records every request."""

    def __init__(self, legacy: bool = False):
        self.legacy = legacy
        self.seen: list[tuple[str, str, dict]] = []
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
                studio.seen.append(("GET", self.path, {}))
                if self.path.startswith("/agents/info"):
                    if studio.legacy:
                        return self._send({"error": "not found"}, 404)
                    # The shape panel/routes_agents.py builds from H3_QUALITIES / H3_LENGTHS.
                    return self._send({"app": "phosphene", "api": 1, "engines": {"h3": {
                        "available": True, "qualities": OFFERED_QUALITIES, "lengths": OFFERED_LENGTHS}}})
                if self.path.startswith("/pro/status"):
                    return self._send({"active": False})
                return self._send({})

            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                ctype = self.headers.get("Content-Type", "")
                if ctype.startswith("application/x-www-form-urlencoded"):
                    data = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode(), keep_blank_values=True).items()}
                else:
                    data = {}
                studio.seen.append(("POST", self.path, data))
                if self.path.startswith("/upload"):
                    return self._send({"path": "/x/uploads/still.png"})
                return self._send({"ok": True, "id": "job1"})

        self.server = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def posts(self, path: str) -> list[dict]:
        return [d for m, p, d in self.seen if m == "POST" and p.startswith(path)]


class ServerSideStillAsReported(unittest.TestCase):
    """The studio-side facts these tests rest on. If one fails, re-read make_job first."""

    def test_make_job_resolves_axes_then_tier_then_default(self):
        self.assertIn(MAKE_JOB_RESOLUTION, _SRC)

    def test_one_axis_alone_is_the_default_draft(self):
        self.assertEqual(TIER_DEFAULT, "draft_3s")
        self.assertEqual(studio_cell({"h3_length": "10s"}), TIER_DEFAULT)
        self.assertEqual(studio_cell({"h3_quality": "high"}), TIER_DEFAULT)

    def test_client_default_quality_is_the_studios(self):
        self.assertEqual(QUALITY_DEFAULT, "draft")
        self.assertIn("draft", OFFERED_QUALITIES)
        for ln in ("3s", "5s", "10s", "15s", "10s_dense"):
            self.assertIn(ln, OFFERED_LENGTHS)


class _Base(unittest.TestCase):
    legacy = False

    def setUp(self):
        self.studio = FakeStudio(legacy=self.legacy)
        self.ph = phosphene_client.Phosphene(self.studio.url)

    def tearDown(self):
        self.studio.close()

    def cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = phosphene_cli.main(["--url", self.studio.url, *argv])
        return rc, out.getvalue()

    def queued_cell(self) -> str:
        posts = self.studio.posts("/queue/add")
        self.assertEqual(len(posts), 1, posts)
        return studio_cell(posts[0])


class ClientAsksForTheLength(_Base):
    def test_seconds_without_quality_renders_that_length(self):
        # The reported case: was draft_3s.
        self.ph.video("a tram", engine="h3", seconds=10)
        self.assertEqual(self.queued_cell(), "draft_10s")

    def test_every_offered_seconds_value(self):
        for sec in (3, 5, 10, 15):
            with self.subTest(seconds=sec):
                self.studio.seen.clear()
                self.ph.video("a tram", engine="h3", seconds=sec)
                self.assertEqual(self.queued_cell(), f"draft_{sec}s")

    def test_float_seconds_from_the_cli_parser(self):
        self.ph.video("a tram", engine="h3", seconds=15.0)
        self.assertEqual(self.queued_cell(), "draft_15s")

    def test_length_key_without_quality(self):
        self.ph.video("a tram", engine="h3", length="10s_dense")
        self.assertEqual(self.queued_cell(), "draft_10s_dense")

    def test_nothing_given_is_a_5s_draft(self):
        # The client always meant 5 s; the missing quality made it the studio's 3 s.
        self.ph.video("a tram", engine="h3")
        self.assertEqual(self.queued_cell(), "draft_5s")

    def test_quality_alone_still_5s(self):
        self.ph.video("a tram", engine="h3", quality="high")
        self.assertEqual(self.queued_cell(), "high_5s")

    def test_both_axes_still_honoured(self):
        for q in ("draft", "standard", "high", "fast_hd"):
            for sec in (3, 5, 10, 15):
                with self.subTest(quality=q, seconds=sec):
                    self.studio.seen.clear()
                    self.ph.video("a tram", engine="h3", quality=q, seconds=sec)
                    self.assertEqual(self.queued_cell(), f"{q}_{sec}s")

    def test_length_wins_over_seconds(self):
        self.ph.video("a tram", engine="h3", quality="standard", seconds=10, length="10s_dense")
        self.assertEqual(self.queued_cell(), "standard_10s_dense")

    def test_case_is_folded_like_the_studio_does(self):
        self.ph.video("a tram", engine="h3", quality="HIGH", length="10S")
        self.assertEqual(self.queued_cell(), "high_10s")


class ClientRefusesWhatTheStudioWouldSwap(_Base):
    def assertRefused(self, fn, *words):
        with self.assertRaises(phosphene_client.PhospheneError) as cm:
            fn()
        for w in words:
            self.assertIn(w, str(cm.exception))
        self.assertEqual(self.studio.posts("/queue/add"), [], "nothing may be queued")
        return cm.exception

    def test_seconds_that_is_no_length(self):
        for sec in (7, 1, 20):
            with self.subTest(seconds=sec):
                self.studio.seen.clear()
                self.assertRefused(lambda: self.ph.video("a tram", engine="h3", seconds=sec),
                                   f"{sec}s", "10s")

    def test_unknown_length_key(self):
        self.assertRefused(lambda: self.ph.video("a tram", engine="h3", length="15s_dense"), "15s_dense")

    def test_an_ltx_quality_on_h3(self):
        self.assertRefused(lambda: self.ph.video("a tram", engine="h3", quality="balanced", seconds=5),
                           "balanced", "draft")

    def test_env_gated_canvas_off_by_default(self):
        hidden = [k for k, on in QUALITIES.items() if not on]
        self.assertTrue(hidden, "expected at least one env-gated H3 canvas")
        self.assertRefused(lambda: self.ph.video("a tram", engine="h3", quality=hidden[0]), hidden[0])

    def test_refused_before_the_start_frame_is_uploaded(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        still = Path(d) / "still.png"
        still.write_bytes(b"\x89PNG")
        self.assertRefused(lambda: self.ph.video("a tram", engine="h3", image=str(still), seconds=7))
        self.assertEqual(self.studio.posts("/upload"), [])

    def test_image_to_video_still_uploads_when_valid(self):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d)
        still = Path(d) / "still.png"
        still.write_bytes(b"\x89PNG")
        self.ph.video("a tram", engine="h3", image=str(still), seconds=10)
        self.assertEqual(len(self.studio.posts("/upload")), 1)
        form = self.studio.posts("/queue/add")[0]
        self.assertEqual((form["mode"], form["image"]), ("i2v", "/x/uploads/still.png"))
        self.assertEqual(studio_cell(form), "draft_10s")


class OlderStudioWithoutAgentsInfo(_Base):
    legacy = True

    def test_form_goes_through_unchecked(self):
        # No list to check against: the studio's own answer stands, as before.
        self.ph.video("a tram", engine="h3", seconds=10)
        self.assertEqual(self.queued_cell(), "draft_10s")


class LtxUntouched(_Base):
    def test_ltx_form_has_no_h3_fields_and_asks_no_info(self):
        self.ph.video("a tram", quality="high", seconds=10)
        form = self.studio.posts("/queue/add")[0]
        self.assertEqual((form["engine"], form["quality"], form["duration"]), ("ltx", "high", "10"))
        self.assertFalse([k for k in form if k.startswith("h3_")])
        self.assertFalse([p for m, p, _ in self.studio.seen if p.startswith("/agents/info")])

    def test_ltx_seconds_not_checked_against_h3_lengths(self):
        self.ph.video("a tram", seconds=7)
        self.assertEqual(self.studio.posts("/queue/add")[0]["duration"], "7")


class CliEntryPoint(_Base):
    def test_seconds_without_quality(self):
        rc, _ = self.cli("video", "a tram", "--engine", "h3", "--seconds", "10")
        self.assertEqual(rc, 0)
        self.assertEqual(self.queued_cell(), "draft_10s")

    def test_bad_seconds_is_exit_1_with_the_offered_lengths(self):
        rc, out = self.cli("video", "a tram", "--engine", "h3", "--seconds", "7")
        self.assertEqual(rc, 1)
        self.assertIn("7s", out)
        for ln in OFFERED_LENGTHS:
            self.assertIn(ln, out)
        self.assertEqual(self.studio.posts("/queue/add"), [])

    def test_bad_seconds_json(self):
        rc, out = self.cli("--json", "video", "a tram", "--engine", "h3", "--seconds", "7")
        self.assertEqual(rc, 1)
        self.assertFalse(json.loads(out)["ok"])

    def test_help_names_the_defaults(self):
        p = phosphene_cli.build_parser()
        video = p._subparsers._group_actions[0].choices["video"]
        helps = {a.dest: a.help for a in video._actions}
        self.assertIn("draft (default)", helps["quality"])
        self.assertIn("5 (default)", helps["seconds"])


class McpEntryPoint(_Base):
    def call(self, **args) -> dict:
        return phosphene_mcp.call_tool(phosphene_mcp.Tools(self.studio.url), "phosphene_make_video", args)

    def test_seconds_without_quality(self):
        r = self.call(prompt="a tram", engine="h3", seconds=10)
        self.assertFalse(r["isError"], r)
        self.assertEqual(self.queued_cell(), "draft_10s")

    def test_bad_seconds_is_a_tool_error_with_a_sentence(self):
        r = self.call(prompt="a tram", engine="h3", seconds=7)
        self.assertTrue(r["isError"])
        self.assertIn("7s", json.loads(r["content"][0]["text"])["error"])
        self.assertEqual(self.studio.posts("/queue/add"), [])

    def test_schema_names_the_defaults(self):
        tool = next(t for t in phosphene_mcp.TOOLS if t["name"] == "phosphene_make_video")
        props = tool["inputSchema"]["properties"]
        self.assertIn("draft (default)", props["quality"]["description"])
        self.assertIn("5 (default)", props["seconds"]["description"])


if __name__ == "__main__":
    unittest.main()
