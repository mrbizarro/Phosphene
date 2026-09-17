#!/usr/bin/env python3.11
"""A REAL browser geometry gate for the Editor.

WHY THIS EXISTS. Every layout defect the Editor has shipped was invisible to
the suite because the suite reads MARKUP. `.sbe-head .primary { width: auto }`
is a string in a test either way; what broke the owner's screen was that the
global `button { width: 100% }` rule resolved Save's flex basis to 1554px the
moment `sbePaintChrome` swapped it to `.primary`, the header wrapped to two
rows, and the column overflowed. No amount of grepping the stylesheet can see
that. Only a browser that has actually laid the page out can.

So this boots the panel out of THIS working tree against a throwaway state
directory, drives a headless Chrome over CDP with a websocket client written
here (the panel's python has no selenium, no playwright, no `websockets`, and
adding one would put a pip dependency into every Pinokio install), opens the
Editor on a seeded board, and reads five numbers off the live DOM at four
widths:

  header_rows                 the editor header must be ONE row
  save_width                  Save must be a button, not a 1554px bar
  column_overflow             #edStage must not scroll sideways
  timeline_clip               the timeline floor must not clip the music lane
  stage_layers_both_visible   the <video> and the still must never both be on

Everything is measured with getBoundingClientRect / scrollWidth / clientHeight
and computed opacity. Nothing is read out of CSS text.

    ./ltx-2-mlx/env/bin/python3.11 scripts/measure_editor_layout.py

Exit codes: 0 all measurements pass, 1 a measurement FAILED, 3 no browser on
this machine (the unittest skips on this), 4 the harness itself could not get
far enough to measure — which is never reported as a pass.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The owner's live panel. Nothing here may ever bind it.
FORBIDDEN_PORTS = {8199}

WIDTHS = (1920, 1520, 1280, 1100)
VIEWPORT_H = 1000

# The board the Editor is opened on. A board with no rendered shot is a
# legitimate document — the panel's own comment says "an empty timeline is a
# legitimate place to stand" — and it is the only seed that does not make the
# auto-editor decode video, which would put ffmpeg in the middle of a layout
# test.
BOARD_ID = "sb_geom_gate"

SAVE_MIN, SAVE_MAX = 40.0, 200.0
OVERFLOW_TOL = 1.0

# ---------------------------------------------------------------------------
# THE TIMELINE'S DRAGGABLE AFFORDANCES — one table, not three special cases
# ---------------------------------------------------------------------------
# WHY THIS IS A TABLE. Two defects of the SAME SHAPE reached the owner one
# after the other: a 12x12 fade handle sitting inside the trim grip's hit area
# ("not sure that grabbing the top corner is working — I'm not being able to do
# it"), and a level line drawn on the strip's own top edge with most of its
# 14px target clipped away outside the box ("you are able to drag the sound
# thingy, but it's not super user-friendly"). Both are the same two questions:
# is this thing BIG ENOUGH to hit, and does anything ELSE claim its pixels.
#
# So they are asked here of every draggable thing in the timeline, in a table a
# fifth affordance can join by adding a row rather than by writing a fourth
# special case. Every rectangle is the one a POINTER sees, not the one that is
# painted: a grip is grown by its `--sbe-grip-skirt`, and the level line — an
# SVG path whose own box has no height at all — is grown by half its stroke.
#
# The floor and the ceiling are both measured because the lanes are USER-SIZED.
# A control sized in absolute pixels inside a lane whose height is dragged will
# keep breaking, which is exactly how the fade handle came to be a fifth of the
# corner it was supposed to own: nobody moved it, the lane moved under it.
HANDLE_ENDS = ("floor", "ceiling")

# The viewport each end is measured in. The floor is only REACHABLE in a short
# window: `sbeFitMonitors` hands the timeline whatever the monitors decline, so
# in a tall one the picture track is flexed well past its own minimum and the
# shortest block never appears. Measured: at 560px the lanes sit exactly on
# their bases (a 52px picture block, a 37px sound strip); at 1000px the same
# call leaves the track at 84px and the gate would be testing nothing.
HANDLE_VIEWPORT = {"floor": (1280, 560), "ceiling": (1280, 1200)}
HANDLE_TL = {"floor": "SBE_TL_MIN_H", "ceiling": "SBE_TL_MAX_H"}

# What must be on screen at all. A gate that measured an empty timeline would
# report nothing and pass.
HANDLE_ROWS = {
    "clip": ("grip_l", "grip_r", "fade_in", "fade_out"),
    "aclip": ("grip_l", "grip_r", "fade_in", "fade_out", "level", "kf"),
    # THE MUSIC BED, which grew the same three affordances the strips have.
    # It is a ROW here rather than a fourth special case because that is what
    # the table is for: "fade the music out under the last shot" is the most
    # ordinary request in editing, and the bed had no fade, no level and no
    # points at all — so the handles had to be built, and a handle that is
    # built is a handle that can be too small or can sit on top of another one.
    "mclip": ("grip_l", "grip_r", "fade_in", "fade_out", "level", "kf"),
}

# The smallest each target may be, in the axis that matters for it. 20x20 is
# the floor for anything you are asked to grab with a mouse; the level line is
# a BAND, so its short axis is the stroke and its long axis is how much of the
# line is left after the corner handles have taken their ends.
MIN_HIT = {
    "grip_l": (12.0, 20.0),
    "grip_r": (12.0, 20.0),
    "fade_in": (20.0, 20.0),
    "fade_out": (20.0, 20.0),
    "level": (20.0, 12.0),
    "kf": (6.0, 6.0),
}

# Pairs that MAY share pixels, and the reason. Everything else in the same
# block must be disjoint. A keyframe dot lives ON the line by construction —
# it is the more specific target on it, not a rival to it, and `sbeOnAudioDown`
# tests it first — so demanding they not touch would be demanding the dot leave
# the line it belongs to.
ALLOWED_OVERLAP = {("kf", "level")}

CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
)


class HarnessError(RuntimeError):
    """The measurement could not be taken. NOT a failed measurement."""


class NoBrowser(HarnessError):
    """No Chrome/Chromium/Edge on this machine."""


# ---------------------------------------------------------------------------
# A websocket client, because the alternative is a pip install
# ---------------------------------------------------------------------------
# RFC6455, client side, only as much of it as CDP uses: one text stream, no
# extensions, no compression. Client frames MUST be masked or Chrome closes the
# socket; server frames never are. Continuation and 64-bit lengths are handled
# because a `Runtime.evaluate` result carrying a DOM measurement is small but a
# `Page` event is not, and a frame we cannot parse desynchronises the stream
# for every message after it.
class WebSocket:
    def __init__(self, url: str, timeout: float = 30.0):
        if not url.startswith("ws://"):
            raise HarnessError(f"only ws:// is supported, got {url!r}")
        rest = url[len("ws://"):]
        netloc, _, path = rest.partition("/")
        path = "/" + path
        host, _, port_s = netloc.partition(":")
        port = int(port_s or 80)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self._buf = b""
        self._handshake(host, port, path)

    def _handshake(self, host: str, port: int, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self.sock.sendall(req.encode("ascii"))
        while b"\r\n\r\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise HarnessError("devtools closed the socket during handshake")
            self._buf += chunk
        head, _, self._buf = self._buf.partition(b"\r\n\r\n")
        status = head.split(b"\r\n", 1)[0].decode("latin-1")
        if "101" not in status:
            raise HarnessError(f"websocket upgrade refused: {status}")

    # -- framing ----------------------------------------------------------
    def _read_exact(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise HarnessError("devtools closed the socket")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        head = bytearray([0x80 | opcode])
        n = len(payload)
        if n < 126:
            head.append(0x80 | n)
        elif n < (1 << 16):
            head.append(0x80 | 126)
            head += struct.pack("!H", n)
        else:
            head.append(0x80 | 127)
            head += struct.pack("!Q", n)
        mask = os.urandom(4)
        head += mask
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(bytes(head) + masked)

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        b0, b1 = self._read_exact(2)
        fin = bool(b0 & 0x80)
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack("!H", self._read_exact(2))[0]
        elif n == 127:
            n = struct.unpack("!Q", self._read_exact(8))[0]
        mask = self._read_exact(4) if masked else b""
        data = self._read_exact(n) if n else b""
        if masked:
            data = bytes(b ^ mask[i & 3] for i, b in enumerate(data))
        return fin, opcode, data

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self) -> str:
        parts: list[bytes] = []
        want_text = False
        while True:
            fin, opcode, data = self._recv_frame()
            if opcode == 0x8:                       # close
                raise HarnessError("devtools sent a close frame")
            if opcode == 0x9:                       # ping -> pong
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:                       # pong
                continue
            if opcode == 0x1:
                parts = [data]
                want_text = True
            elif opcode == 0x2:                     # binary: not ours
                parts = []
                want_text = False
            elif opcode == 0x0:                     # continuation
                parts.append(data)
            if fin:
                if want_text:
                    return b"".join(parts).decode("utf-8", "replace")
                parts = []

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# CDP
# ---------------------------------------------------------------------------
class CDP:
    def __init__(self, ws: WebSocket):
        self.ws = ws
        self._id = 0

    def call(self, method: str, params: dict | None = None,
             timeout: float = 40.0) -> dict:
        self._id += 1
        mine = self._id
        self.ws.send_text(json.dumps({"id": mine, "method": method,
                                      "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            msg = json.loads(self.ws.recv_text())
            if msg.get("id") == mine:
                if "error" in msg:
                    raise HarnessError(f"{method}: {msg['error']}")
                return msg.get("result") or {}
            if time.monotonic() > deadline:
                raise HarnessError(f"{method}: no reply in {timeout}s")

    def evaluate(self, expr: str, *, await_promise: bool = False,
                 timeout: float = 40.0):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "returnByValue": True,
                       "awaitPromise": await_promise},
                      timeout=timeout)
        det = r.get("exceptionDetails")
        if det:
            exc = (det.get("exception") or {})
            raise HarnessError("page threw: " +
                               str(exc.get("description") or det.get("text")))
        return (r.get("result") or {}).get("value")


# ---------------------------------------------------------------------------
# processes
# ---------------------------------------------------------------------------
def free_port() -> int:
    for _ in range(40):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        finally:
            s.close()
        if port not in FORBIDDEN_PORTS:
            return port
    raise HarnessError("could not find a free port")


def _http_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _http_ok(url: str, timeout: float = 3.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except (urllib.error.URLError, OSError, TimeoutError):
        return False


def seed_board(state_dir: Path) -> None:
    """One board on disk, so the Editor has a document to open.

    Written as plain JSON rather than through `storyboard.save_storyboard` so
    this script stays importable-free: it must run on a python that has only
    the standard library, and the stdlib-only rule is a test.

    AND AN `edit.json`, because the handle gate needs something to measure.
    An empty timeline draws no clip block, no sound strip, no grips and no
    corner handles — a gate run against it would report nothing and pass, which
    is precisely the failure mode it exists to prevent. The clips point at
    files that do not exist ON PURPOSE: `validate_edit` only demands a file for
    a STILL, so a video clip with a missing path is a valid document, draws
    every affordance, and puts no ffmpeg anywhere near a layout test.

    Clip 1 carries `audio` (so its strip is unlinked and gets grips and corner
    handles of its own) and one gain point in the middle of it (so the level
    line's dot is on screen to be measured).
    """
    d = state_dir / "storyboards" / BOARD_ID
    d.mkdir(parents=True, exist_ok=True)
    board = {
        "schema": 1, "id": BOARD_ID, "title": "Geometry gate",
        "created_at": 1_700_000_000, "cast": [], "engine_mode": "ltx",
        "policy": {},
        "shots": [{"n": i + 1, "title": f"shot {i + 1}", "mode": "text",
                   "engine": "ltx",
                   "prompt": "the gate does not render anything",
                   "duration_s": 5.0, "seed": 101 + i, "refs": [],
                   "status": "planned"} for i in range(2)],
    }
    (d / "storyboard.json").write_text(json.dumps(board, indent=2),
                                       encoding="utf-8")

    def clip(i: int, film_start: float, dur: float = 5.0, **extra) -> dict:
        c = {
            "id": f"kgeom{i}", "path": str(d / f"shot{i}.mp4"), "proxy": None,
            "kind": "video", "start": 0.0, "end": dur,
            "film_start": film_start, "film_end": film_start + dur,
            "source": "auto", "locked": False,
        }
        c.update(extra)
        return c

    edit = {
        "version": 2, "board": BOARD_ID, "revision": 1,
        # A SOUNDTRACK, for the same reason the clips carry one: the bed's
        # block is hidden without a path and a duration, and a gate measuring
        # a hidden block reports nothing and passes. `afx` puts a point on the
        # level line so the dot is on screen to be measured, exactly as clip 1
        # does one field down. The file does not exist ON PURPOSE — the block,
        # the grips, the corner handles and the level line are all drawn from
        # `audio.duration`, and the waveform (which would need the file) is
        # not one of the things under test.
        "audio": {"path": str(d / "bed.wav"), "duration": 40.0, "offset": 0.0,
                  "mode": "under", "afx": {"points": [[12.0, 0.55]]}},
        "overlays": [], "beats": None,
        "clips": [
            clip(0, 0.0,
                 audio={"start": 0.0, "end": 5.0, "film_start": 0.0},
                 afx={"points": [[2.5, 0.6]]}),
            clip(1, 5.0),
        ],
    }
    (d / "edit.json").write_text(json.dumps(edit, indent=2), encoding="utf-8")


def start_panel(work: Path, log: Path, verbose: bool) -> tuple[subprocess.Popen, str]:
    port = free_port()
    state = work / "state"
    state.mkdir(parents=True, exist_ok=True)
    (work / "outputs").mkdir(exist_ok=True)
    (work / "uploads").mkdir(exist_ok=True)
    seed_board(state)

    env = dict(os.environ)
    env.update({
        "LTX_PORT": str(port),
        "LTX_STATE_DIR": str(state),
        "LTX_OUTPUT_DIR": str(work / "outputs"),
        "LTX_UPLOADS_DIR": str(work / "uploads"),
        # Nothing here should phone home or open a window.
        "LTX_NO_BROWSER": "1",
        "PHOSPHENE_NO_TELEMETRY": "1",
    })
    fh = log.open("wb")
    proc = subprocess.Popen(
        [sys.executable, "mlx_ltx_panel.py"],
        cwd=str(ROOT), env=env, stdout=fh, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 90.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessError(
                f"the panel exited with {proc.returncode} before answering. "
                f"log tail: {_tail(log)}")
        if _http_ok(base + "/"):
            if verbose:
                print(f"panel up on {base}", file=sys.stderr)
            return proc, base
        time.sleep(0.25)
    raise HarnessError(f"the panel never answered GET / on {base}. "
                       f"log tail: {_tail(log)}")


def _tail(p: Path, n: int = 6) -> str:
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "(no log)"
    return " | ".join(lines[-n:])


def find_browser() -> str:
    env = os.environ.get("CHROME_PATH", "").strip()
    if env and Path(env).exists():
        return env
    for c in CHROME_CANDIDATES:
        if Path(c).exists():
            return c
    for name in ("google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    raise NoBrowser(
        "no Chrome/Chromium/Edge found - set CHROME_PATH to a browser binary")


def start_chrome(binary: str, profile: Path,
                 verbose: bool) -> tuple[subprocess.Popen, str]:
    port = free_port()
    proc = subprocess.Popen(
        [binary,
         f"--remote-debugging-port={port}",
         "--remote-allow-origins=*",
         "--headless=new",
         "--no-first-run",
         "--no-default-browser-check",
         f"--user-data-dir={profile}",
         "--disable-gpu",
         "--disable-extensions",
         "--disable-background-networking",
         "--disable-dev-shm-usage",
         "--mute-audio",
         "--window-size=1920,1000",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise HarnessError(f"the browser exited with {proc.returncode}")
        try:
            _http_json(base + "/json/version", timeout=1.0)
            if verbose:
                print(f"devtools up on {base}", file=sys.stderr)
            return proc, base
        except Exception:                                       # noqa: BLE001
            time.sleep(0.2)
    raise HarnessError("the browser never opened its devtools port")


def page_socket(devtools: str) -> str:
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        try:
            for t in _http_json(devtools + "/json/list", timeout=2.0):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    return t["webSocketDebuggerUrl"]
        except Exception:                                       # noqa: BLE001
            pass
        time.sleep(0.2)
    raise HarnessError("devtools listed no page target")


def kill(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass
        try:
            proc.wait(timeout=6)
            return
        except subprocess.TimeoutExpired:
            continue


# ---------------------------------------------------------------------------
# the page side
# ---------------------------------------------------------------------------
# Opening the Editor is the app's own door: `edOpenBoard` is what the picker,
# the board row and the rail's step 3 all call, and it does the tab switch and
# the document load together. Poking `#sbTimeline.hidden` directly would give a
# header laid out in a column that never ran `sbeFitMonitors`, which is exactly
# the measurement that would be worthless.
JS_OPEN = """
(() => {
  try { localStorage.setItem('phos_ed_doc', %(board)s); } catch (e) {}
  if (typeof workflowSwitch !== 'function') return 'workflowSwitch missing';
  if (typeof edOpenBoard !== 'function') return 'edOpenBoard missing';
  workflowSwitch('editor');
  edOpenBoard(%(board)s);
  return 'ok';
})()
"""

JS_READY = """
(() => {
  const plan = document.getElementById('sbTimeline');
  const scroll = document.getElementById('sbeScroll');
  const head = document.querySelector('#sbTimeline > header.sbe-head')
            || document.querySelector('#sbTimeline .sbe-head');
  if (!plan) return 'no #sbTimeline';
  if (plan.hidden) return 'the editor document is hidden';
  if (!window.SBE || !SBE.open) return 'SBE.open is false';
  if (!SBE.edit) return 'no edit loaded yet';
  if (!head) return 'no editor header';
  if (!scroll || scroll.clientHeight <= 0) return 'the timeline has no height';
  if (head.getBoundingClientRect().height <= 0) return 'the header has no height';
  return 'ok';
})()
"""

# ONE evaluate per width, and every number in it comes off the laid-out DOM.
#
# The Save button is measured TWICE on purpose. `sbePaintChrome` swaps it
# between `.ghost-btn` and `.primary` on SBE.dirty, and the 1554px defect only
# ever existed in the `.primary` half - "sometimes" was exactly "while
# unsaved". A gate that measured the idle button would have passed the whole
# time the owner's header was wrapped, so the asserted number is the dirty one.
JS_MEASURE = r"""
(async () => {
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const settle = async () => { await raf(); await raf(); await raf(); };

  const el = id => document.getElementById(id);
  const num = v => (typeof v === 'number' && isFinite(v)) ? Math.round(v * 100) / 100 : null;
  const out = { errors: [], missing: [] };
  const need = (id) => { const e = el(id); if (!e) out.missing.push('#' + id); return e; };

  await settle();

  const head = document.querySelector('#sbTimeline > header.sbe-head')
            || document.querySelector('#sbTimeline .sbe-head');
  if (!head) out.missing.push('.sbe-head');
  const save = need('sbeSaveBtn');
  const col = need('edStage');
  const scroll = need('sbeScroll');
  const plan = need('sbTimeline');
  const video = need('sbeVideo');
  const still = need('sbeStill');

  out.viewport_width = window.innerWidth;
  out.viewport_height = window.innerHeight;
  out.editor_open = !!(window.SBE && SBE.open) && !!plan && !plan.hidden;
  out.stacked = !!(window.matchMedia && window.matchMedia('(max-width: 900px)').matches);

  // The button as it sits when there is nothing to save.
  out.save_width_idle = save ? num(save.getBoundingClientRect().width) : null;
  out.save_class_idle = save ? save.className : null;

  // ...and as it sits the moment there is. This is the state the header
  // wrapped in, so this is the state the gate measures.
  try {
    if (window.SBE) { SBE.dirty = true; SBE.saving = false; }
    if (typeof sbePaint === 'function') sbePaint();
    else if (typeof sbePaintChrome === 'function') sbePaintChrome();
  } catch (e) { out.errors.push('paint threw: ' + e); }
  await settle();

  out.save_class = save ? save.className : null;
  out.save_primary = !!(save && save.classList.contains('primary'));
  out.save_width = save ? num(save.getBoundingClientRect().width) : null;

  // HEADER ROWS - the number of distinct rendered rows among the header's own
  // visible children.
  //
  // NOT a set of rounded `top` values, and the reason is in the stylesheet:
  // `.carousel-head` is align-items:center, so a 6px state dot, a 20px title
  // and a 28px button sitting in the SAME row have three different tops
  // (measured: 123 / 116 / 112 at 1100px). Counting tops would report that row
  // as three. What is invariant across a centered row is the CENTRE, so rows
  // are clustered on it - and the two cases are an order of magnitude apart:
  // same row is under a pixel, a wrapped row is a whole control height (28px+)
  // away. Every child's top, height and centre is reported below so the number
  // can be checked by hand.
  const centres = [];
  const kids = [];
  if (head) {
    for (const kid of head.children) {
      if (kid.offsetParent === null) continue;
      const r = kid.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) continue;
      const c = Math.round(r.top + r.height / 2);
      kids.push({ id: kid.id || kid.className || kid.tagName,
                  top: Math.round(r.top), h: num(r.height), centre: c,
                  w: num(r.width) });
      if (!centres.some(x => Math.abs(x - c) <= 8)) centres.push(c);
    }
  }
  centres.sort((a, b) => a - b);
  out.header_rows = head ? centres.length : null;
  out.header_row_centres = centres;
  out.header_children = kids;
  out.header_height = head ? num(head.getBoundingClientRect().height) : null;

  // THE COLUMN. A header that wrapped, or a control that stretched, shows up
  // here as sideways scroll on the editor's own scroller.
  out.column_scroll_width = col ? col.scrollWidth : null;
  out.column_client_width = col ? col.clientWidth : null;
  out.column_overflow = col ? (col.scrollWidth - col.clientWidth) : null;

  // THE TIMELINE FLOOR. `.sbe-tl` is overflow-y:hidden, so a floor too short
  // for its lanes does not scroll - it CLIPS, and what falls off the bottom is
  // the soundtrack. scrollHeight still reports the content, which is why this
  // is measurable at all.
  out.timeline_scroll_height = scroll ? scroll.scrollHeight : null;
  out.timeline_client_height = scroll ? scroll.clientHeight : null;
  out.timeline_clip = scroll ? (scroll.scrollHeight - scroll.clientHeight) : null;
  out.timeline_floor_h = plan
    ? (getComputedStyle(plan).getPropertyValue('--sbe-tl-h') || '').trim() : null;
  const music = el('sbeMusicLane');
  if (music && scroll) {
    const m = music.getBoundingClientRect(), s = scroll.getBoundingClientRect();
    out.music_lane_bottom_overhang = num(m.bottom - s.bottom);
  }

  // THE TWO STAGE LAYERS. Neither is ever display:none - a hidden <video> is
  // never loaded by WebKit - so `is-on` and opacity are the switch, and the
  // only thing that can be wrong is both of them being on at once.
  const on = (e) => {
    if (!e) return false;
    const cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden') return false;
    const r = e.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    return parseFloat(cs.opacity || '1') > 0.01;
  };
  out.video_opacity = video ? num(parseFloat(getComputedStyle(video).opacity)) : null;
  out.still_opacity = still ? num(parseFloat(getComputedStyle(still).opacity)) : null;
  out.video_visible = on(video);
  out.still_visible = on(still);
  out.stage_layers_both_visible = on(video) && on(still);

  // Put it back, so the next width does not start from a forced state.
  try {
    if (window.SBE) SBE.dirty = false;
    if (typeof sbePaint === 'function') sbePaint();
  } catch (e) {}

  return out;
})()
"""


# ONE END OF THE LANE RANGE, MEASURED. `sbeTlSet` is the app's own writer of
# the timeline's height — the same one the drag handle and the keyboard call —
# so this drives the lanes the way a user does rather than poking a variable.
JS_HANDLES = r"""
(async () => {
  const raf = () => new Promise(r => requestAnimationFrame(() => r()));
  const settle = async () => { await raf(); await raf(); await raf(); };
  const num = v => Math.round(v * 100) / 100;
  const out = { blocks: {}, errors: [] };

  if (typeof sbeTlSet !== 'function') { out.errors.push('sbeTlSet missing'); return out; }
  // THE HANDLES THIS GATE MEASURES LIVE ON THE SOUND LANES, and those lanes now
  // make themselves small when nobody is working on sound — a grip inside an
  // 18px strip is not the control this gate is about, and the page would have
  // compacted underneath it a couple of seconds after it loaded. Hold them open
  // the way a user does with the ▾ on the A1 head, through the app's own writer.
  if (typeof sbeAudioPinSet === 'function') sbeAudioPinSet('open');
  sbeTlSet(%(tl)s);
  if (typeof sbePaint === 'function') sbePaint();
  await settle();
  await settle();

  const plan = document.getElementById('sbTimeline');
  const cs = plan ? getComputedStyle(plan) : null;
  out.tl_now = (window.SBE || {}).tlNow;
  out.lane_track = cs ? cs.getPropertyValue('--sbe-track-h').trim() : null;
  out.lane_alane = cs ? cs.getPropertyValue('--sbe-alane-h').trim() : null;

  // A rectangle a POINTER sees: the painted box grown by whatever is hittable
  // past it. `padX`/`padY` are how the two cases differ and nothing else does.
  const grown = (el, padX, padY) => {
    const r = el.getBoundingClientRect();
    return { left: num(r.left - padX), top: num(r.top - padY),
             right: num(r.right + padX), bottom: num(r.bottom + padY),
             w: num(r.width + 2 * padX), h: num(r.height + 2 * padY) };
  };
  const cssNum = (el, prop) =>
    parseFloat(getComputedStyle(el).getPropertyValue(prop)) || 0;

  for (const [key, sel] of [['clip', '.sbe-clip'], ['aclip', '.sbe-aclip'],
                           ['mclip', '#sbeMusicClip']]) {
    const blk = document.querySelector(sel);
    if (!blk) { out.blocks[key] = null; continue; }
    const rows = {};
    const put = (name, el, padX, padY) => {
      if (el) rows[name] = grown(el, padX || 0, padY || 0);
    };
    for (const [name, s] of [['grip_l', '.sbe-grip.l'], ['grip_r', '.sbe-grip.r']]) {
      const g = blk.querySelector(s);
      // THE SKIRT IS THE TARGET. `.sbe-grip::before` widens what can be hit by
      // `--sbe-grip-skirt` on each side without widening what is drawn, and
      // the fade handle's inset is measured from the far side of it — so the
      // gate has to reconstruct it or it would be comparing the wrong box.
      if (g) put(name, g, cssNum(g, '--sbe-grip-skirt'), 0);
    }
    put('fade_in', blk.querySelector('.sbe-fade-h.in'), 0, 0);
    put('fade_out', blk.querySelector('.sbe-fade-h.out'), 0, 0);
    const lvl = blk.querySelector('.sbe-lvl-hit');
    if (lvl) {
      // AN SVG PATH'S OWN BOX IS ITS GEOMETRY, so a level line at unity
      // measures zero pixels tall. What a pointer can reach is that box grown
      // by half the stroke — `pointer-events: stroke` is what makes the line a
      // target at all, and the stroke is where the target lives.
      //
      // ACROSS THE LINE ONLY, unless the caps say otherwise. A `butt` cap —
      // the default, and what this line uses — ends the stroke exactly at the
      // last point, so the target does NOT reach half a stroke past either
      // end. Growing X anyway reported a 7px overhang at both ends of the
      // strip and failed this gate against the fade handles for pixels no
      // pointer can reach. `round` and `square` DO overhang, so they still
      // get the pad and a change of cap cannot quietly widen a target.
      const s = getComputedStyle(lvl);
      const half = (parseFloat(s.strokeWidth) || 0) / 2;
      const cap = (s.strokeLinecap || 'butt').trim();
      put('level', lvl, cap === 'butt' ? 0 : half, half);
    }
    put('kf', blk.querySelector('.sbe-kf'), 0, 0);
    rows.__block = grown(blk, 0, 0);
    const svg = blk.querySelector('.sbe-wave-svg');
    if (svg) rows.__svg = { attr: svg.getAttribute('width'),
                            viewBox: svg.getAttribute('viewBox'),
                            css: num(svg.getBoundingClientRect().width),
                            style: blk.style.width,
                            d: (lvl && lvl.getAttribute('d') || '').slice(0, 40) };
    out.blocks[key] = rows;
  }
  return out;
})()
"""


def _overlap(a: dict, b: dict) -> float:
    w = min(a["right"], b["right"]) - max(a["left"], b["left"])
    h = min(a["bottom"], b["bottom"]) - max(a["top"], b["top"])
    return round(max(0.0, w) * max(0.0, h), 2)


def check_handles(end: str, m: dict) -> list[str]:
    """Every failure names the affordance and both numbers."""
    bad: list[str] = []
    for e in (m.get("errors") or []):
        bad.append(f"handles/{end}: {e}")
    for block, want in HANDLE_ROWS.items():
        rows = (m.get("blocks") or {}).get(block)
        if not rows:
            bad.append(f"handles/{end}/{block}: nothing was on screen to "
                       f"measure - the gate would have passed on an empty "
                       f"timeline")
            continue
        missing = [n for n in want if n not in rows]
        if missing:
            bad.append(f"handles/{end}/{block}: no {', '.join(missing)} on the "
                       f"block (measured {sorted(k for k in rows if k != '__block')})")
        # -- big enough to hit ------------------------------------------
        for name in want:
            r = rows.get(name)
            if not r:
                continue
            mw, mh = MIN_HIT[name]
            if r["w"] + 0.01 < mw or r["h"] + 0.01 < mh:
                bad.append(f"handles/{end}/{block}: {name} is "
                           f"{r['w']}x{r['h']}, under the {mw:g}x{mh:g} it "
                           f"needs to be grabbable (block is "
                           f"{rows['__block']['w']}x{rows['__block']['h']})")
        # -- and nothing else claims its pixels -------------------------
        names = [n for n in want if n in rows]
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if tuple(sorted((a, b))) in ALLOWED_OVERLAP:
                    continue
                area = _overlap(rows[a], rows[b])
                if area > 0:
                    bad.append(f"handles/{end}/{block}: {a} and {b} share "
                               f"{area}px2 - {a} is {rows[a]}, {b} is "
                               f"{rows[b]}. A pointer aimed at one of them "
                               f"gets whichever is higher in the stack, which "
                               f"is how the fade handle was lost inside the "
                               f"trim grip")
    return bad


# THE GATE'S OWN GATE. A geometry test that has never been seen to fail is a
# test whose selectors might all be wrong; it would report five green numbers
# about elements it never found and nobody would know. This puts the three
# shipped defects BACK - the two stylesheet rules that made Save 1554px wide
# and let the header wrap, and a floor too short for the lanes - so the gate
# can be watched failing on demand:
#
#   scripts/measure_editor_layout.py --inject-defect      # must exit 1
#
# It changes nothing on disk and is never on in a normal run.
JS_DEFECT = """
(() => {
  const s = document.createElement('style');
  s.id = 'phos-injected-defect';
  s.textContent = [
    /* the global `button { width: 100% }` that `.primary` never cancelled */
    '.sbe-head .primary { width: 100% !important; flex: 1 1 auto !important; }',
    /* ...and the wrap that let the row grow a second line around it */
    '.carousel-head.sbe-head { flex-wrap: wrap !important; }',
    /* a timeline floor shorter than the lanes standing in it */
    '#sbTimeline { --sbe-tl-h: 150px !important; }',
    /* ...and the corner handle back inside the trim grip, 12x12 at `left:0`,
       which is the shape the owner could not hit */
    '.sbe-fade-band { left: 0 !important; right: 0 !important;',
    '  height: 12px !important; }',
    '.sbe-fade-h { flex: 0 1 12px !important; }'
  ].join('\\n');
  document.head.appendChild(s);
  return 'injected';
})()
"""


def check(width: int, m: dict) -> list[str]:
    """Every failure names the measurement and both numbers."""
    w = str(width)
    bad: list[str] = []
    if m.get("missing"):
        bad.append(f"{w}: elements not on the page: {', '.join(m['missing'])}")
    for e in (m.get("errors") or []):
        bad.append(f"{w}: {e}")
    if not m.get("editor_open"):
        bad.append(f"{w}: the editor was not open - nothing measured is meaningful")
        return bad

    rows = m.get("header_rows")
    if rows != 1:
        bad.append(f"{w}: header_rows {rows} != 1 "
                   f"(row centres {m.get('header_row_centres')})")

    sw = m.get("save_width")
    if sw is None or not (SAVE_MIN <= sw <= SAVE_MAX):
        bad.append(f"{w}: save_width {sw} outside "
                   f"[{SAVE_MIN:g}, {SAVE_MAX:g}] "
                   f"(class {m.get('save_class')!r})")

    over = m.get("column_overflow")
    if over is None or over > OVERFLOW_TOL:
        bad.append(f"{w}: column_overflow {over} > {OVERFLOW_TOL:g} "
                   f"(scrollWidth {m.get('column_scroll_width')} vs "
                   f"clientWidth {m.get('column_client_width')})")

    clip = m.get("timeline_clip")
    if clip is None or clip > OVERFLOW_TOL:
        bad.append(f"{w}: timeline_clip {clip} > {OVERFLOW_TOL:g} "
                   f"(scrollHeight {m.get('timeline_scroll_height')} vs "
                   f"clientHeight {m.get('timeline_client_height')}, "
                   f"floor {m.get('timeline_floor_h')})")

    both = m.get("stage_layers_both_visible")
    if both is not False:
        bad.append(f"{w}: stage_layers_both_visible {both} != false "
                   f"(video opacity {m.get('video_opacity')}, "
                   f"still opacity {m.get('still_opacity')})")
    return bad


def run(verbose: bool, inject_defect: bool = False) -> tuple[dict, int]:
    binary = find_browser()                       # raises NoBrowser -> exit 3

    # NOT "phos-geom-": test_geometry_grid.py already owns that prefix and
    # leaves its directories behind, and two different things sharing a prefix
    # makes "did the gate clean up?" unanswerable by looking.
    work = Path(tempfile.mkdtemp(prefix="phos-editor-geom-"))
    profile = work / "chrome"
    profile.mkdir()
    log = work / "panel.log"
    panel = chrome = None
    ws = None
    try:
        panel, base = start_panel(work, log, verbose)
        chrome, devtools = start_chrome(binary, profile, verbose)
        ws = WebSocket(page_socket(devtools))
        cdp = CDP(ws)
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Emulation.setDeviceMetricsOverride",
                 {"width": WIDTHS[0], "height": VIEWPORT_H,
                  "deviceScaleFactor": 1, "mobile": False})
        cdp.call("Page.navigate", {"url": base + "/"})

        deadline = time.monotonic() + 45.0
        while True:
            state = cdp.evaluate("document.readyState")
            if state == "complete":
                break
            if time.monotonic() > deadline:
                raise HarnessError(f"the panel page never finished loading "
                                   f"(readyState {state!r})")
            time.sleep(0.2)

        opened = cdp.evaluate(JS_OPEN % {"board": json.dumps(BOARD_ID)})
        if opened != "ok":
            raise HarnessError(f"could not open the Editor: {opened}")

        deadline = time.monotonic() + 30.0
        why = "never checked"
        while time.monotonic() < deadline:
            why = cdp.evaluate(JS_READY)
            if why == "ok":
                break
            time.sleep(0.25)
        if why != "ok":
            raise HarnessError(f"the Editor never came up: {why}")
        if verbose:
            print("editor open", file=sys.stderr)

        if inject_defect:
            cdp.evaluate(JS_DEFECT)
            if verbose:
                print("the shipped defects were injected - expecting failures",
                      file=sys.stderr)

        widths: dict[str, dict] = {}
        failures: list[str] = []
        for w in WIDTHS:
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": w, "height": VIEWPORT_H,
                      "deviceScaleFactor": 1, "mobile": False})
            m = cdp.evaluate(JS_MEASURE, await_promise=True)
            if not isinstance(m, dict):
                raise HarnessError(f"{w}: the page returned {m!r}, not a "
                                   f"measurement")
            widths[str(w)] = m
            failures.extend(check(w, m))
            if verbose:
                print(f"{w}: rows={m.get('header_rows')} "
                      f"save={m.get('save_width')} "
                      f"col={m.get('column_overflow')} "
                      f"clip={m.get('timeline_clip')}", file=sys.stderr)

        # THE AFFORDANCES, AT BOTH ENDS OF THE LANE RANGE. Last, and in its own
        # viewports, because the floor is only reachable in a short window —
        # see HANDLE_VIEWPORT. Nothing after this reads the width loop's
        # metrics, so the change is safe to leave in place.
        handles: dict[str, dict] = {}
        for end in HANDLE_ENDS:
            vw, vh = HANDLE_VIEWPORT[end]
            cdp.call("Emulation.setDeviceMetricsOverride",
                     {"width": vw, "height": vh, "deviceScaleFactor": 1,
                      "mobile": False})
            m = cdp.evaluate(JS_HANDLES % {"tl": HANDLE_TL[end]},
                             await_promise=True)
            if not isinstance(m, dict):
                raise HarnessError(f"handles/{end}: the page returned {m!r}, "
                                   f"not a measurement")
            handles[end] = m
            failures.extend(check_handles(end, m))
            if verbose:
                rows = (m.get("blocks") or {}).get("aclip") or {}
                print(f"handles/{end}: tl={m.get('tl_now')} "
                      f"track={m.get('lane_track')} alane={m.get('lane_alane')} "
                      f"fade_in={(rows.get('fade_in') or {}).get('w')}x"
                      f"{(rows.get('fade_in') or {}).get('h')}",
                      file=sys.stderr)

        return ({"ok": not failures, "widths": widths, "handles": handles,
                 "failures": failures},
                1 if failures else 0)
    finally:
        if ws is not None:
            ws.close()
        kill(chrome)
        kill(panel)
        shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true",
                    help="progress on stderr; stdout stays one JSON document")
    ap.add_argument("--inject-defect", action="store_true",
                    help="put the shipped layout defects back in the browser "
                         "only, to prove this gate can fail. Exits 1 when it "
                         "is working. Changes nothing on disk.")
    args = ap.parse_args(argv)
    try:
        doc, code = run(args.verbose, args.inject_defect)
    except NoBrowser as exc:
        print(f"no browser: {exc}", file=sys.stderr)
        return 3
    except HarnessError as exc:
        print(json.dumps({"ok": False, "widths": {},
                          "failures": [f"harness: {exc}"]}))
        print(f"harness: {exc}", file=sys.stderr)
        return 4
    print(json.dumps(doc, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
