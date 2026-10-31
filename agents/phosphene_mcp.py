#!/usr/bin/env python3
"""Phosphene as an MCP server (stdio): Claude Code, Codex or any MCP client makes clips on this Mac.

    claude mcp add phosphene -- <python3> <studio>/agents/phosphene_mcp.py
    codex  mcp add phosphene -- <python3> <studio>/agents/phosphene_mcp.py

`phosphene mcp-config` (or GET /agents/info on the running studio) prints the exact line
for this install. Standard library only: newline-delimited JSON-RPC 2.0 on stdin/stdout,
protocol 2025-06-18 (older clients get the version they ask for). Logs go to stderr.

The tools are the same actions as the `phosphene` CLI, over the same client, so they find
the studio the same way (Pinokio or the Mac app) and the studio checks every request as it
checks its own page: model licences, what this Mac can run, what Free and Pro include.
There is deliberately no tool that accepts a model licence on the person's behalf.
"""
from __future__ import annotations

import json
import sys
import threading
import traceback
from pathlib import Path

# Never write __pycache__ beside these files: in the Mac app they sit inside a verified code
# folder, and a file that is not in its manifest fails the next launch's tree check.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phosphene_client import NotRunning, Phosphene, PhospheneError, WaitCancelled, find_servers, row_outputs  # noqa: E402

SERVER_NAME = "phosphene"
SERVER_VERSION = "1.0.0"
PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

_WAIT = {"type": "boolean", "description": "Wait until it is finished and return the file paths "
         "(a video can take 2-20 minutes). Default false: return the job id at once and use phosphene_wait."}
_SEED = {"type": "integer", "description": "Fixed seed for a repeatable result; omit for random."}

TOOLS = [
    {"name": "phosphene_status",
     "description": "Is Phosphene running on this Mac, where, Free or Pro, which engines are installed and "
                    "what each can do (qualities, lengths), and what is rendering now. Call this first.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "phosphene_make_video",
     "description": "Queue a video clip (with its own sound) on this Mac's GPU. Engines: 'ltx' (default; faces, "
                    "voices, dialogue) or 'h3' (Hailuo H3; motion, landscapes, if installed). A prompt works best "
                    "as one paragraph: who, where, light, the action, the camera, and the sound. Optional start "
                    "frame: a picture path on this Mac.",
     "inputSchema": {"type": "object", "required": ["prompt"], "additionalProperties": False, "properties": {
         "prompt": {"type": "string"},
         "engine": {"type": "string", "enum": ["ltx", "h3"], "default": "ltx"},
         "image": {"type": "string", "description": "Absolute path of a start-frame picture (makes it image-to-video)."},
         "quality": {"type": "string", "description": "LTX: quick | balanced (default) | standard | high | high_720p. "
                                                      "H3: draft | fast_hd | standard | high (see phosphene_status)."},
         "seconds": {"type": "number", "description": "Length. LTX: about 2-10 s (default 5). H3: 3, 5, 10 or 15."},
         "seed": _SEED, "label": {"type": "string"}, "wait": _WAIT}}},
    {"name": "phosphene_make_image",
     "description": "Queue still images with the image engine chosen in Phosphene's settings.",
     "inputSchema": {"type": "object", "required": ["prompt"], "additionalProperties": False, "properties": {
         "prompt": {"type": "string"},
         "n": {"type": "integer", "minimum": 1, "maximum": 8, "default": 1},
         "aspect": {"type": "string", "enum": ["16:9", "1:1", "9:16", "4:3", "3:4", "21:9"], "default": "16:9"},
         "seed": _SEED, "wait": _WAIT}}},
    {"name": "phosphene_make_speech",
     "description": "Text to speech (if the Speech pack is installed). Returns a WAV.",
     "inputSchema": {"type": "object", "required": ["text"], "additionalProperties": False, "properties": {
         "text": {"type": "string"},
         "voice": {"type": "string", "description": "A preset voice name (default ryan)."},
         "speed": {"type": "number"}, "wait": _WAIT}}},
    {"name": "phosphene_make_music",
     "description": "Queue a song (style + lyrics) or an instrumental (style only), if the music pack is installed.",
     "inputSchema": {"type": "object", "required": ["style"], "additionalProperties": False, "properties": {
         "style": {"type": "string", "description": "Genre, instruments, mood, voice."},
         "lyrics": {"type": "string", "description": "Omit for an instrumental."},
         "seconds": {"type": "integer"}, "title": {"type": "string"}, "seed": _SEED, "wait": _WAIT}}},
    {"name": "phosphene_list_jobs",
     "description": "Running, queued and recent jobs, with status, progress and output paths.",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {
         "limit": {"type": "integer", "default": 10}}}},
    {"name": "phosphene_wait",
     "description": "Wait for a job to finish (up to timeout_sec, default 1500) and return its status and file "
                    "paths. If it is still running at the timeout, call again.",
     "inputSchema": {"type": "object", "required": ["job_id"], "additionalProperties": False, "properties": {
         "job_id": {"type": "string"}, "timeout_sec": {"type": "number", "default": 1500}}}},
    {"name": "phosphene_fetch",
     "description": "Copy a finished job's files into a folder (for example the project you are working in).",
     "inputSchema": {"type": "object", "required": ["job_id", "dest"], "additionalProperties": False, "properties": {
         "job_id": {"type": "string"}, "dest": {"type": "string", "description": "Absolute folder path."}}}},
    {"name": "phosphene_stop",
     "description": "Stop the job that is rendering now (optionally only if it is job_id).",
     "inputSchema": {"type": "object", "additionalProperties": False, "properties": {"job_id": {"type": "string"}}}},
]


def _slim(row: dict) -> dict:
    p = row.get("params") or {}
    out = {"id": row.get("id"), "status": row.get("status"), "mode": p.get("mode"), "engine": p.get("engine", "ltx"),
           "prompt": str(p.get("prompt") or "")[:200], "elapsed_sec": row.get("elapsed_sec"),
           "error": row.get("error"), "warning": row.get("warning")}
    prog = row.get("progress") or {}
    if prog:
        out["progress"] = {k: prog.get(k) for k in ("pct", "phase_label", "remaining_sec") if k in prog}
    files = row_outputs(row)
    if files:
        out["files"] = files
    return {k: v for k, v in out.items() if v not in (None, "", [])}


_CALL = threading.local()   # per tools/call thread: .cancelled (its threading.Event), .ph (the studio it is bound to)


class Tools:
    def __init__(self, url: str | None = None):
        self._url = url
        self._ph = None
        self._lock = threading.Lock()   # calls run on their own threads: one studio is found, once

    def ph(self) -> Phosphene:
        # Found while it is not yet running, so starting Phosphene after the client fixes it. A call
        # keeps the studio it first got (a make with wait polls the studio it queued on), however
        # discovery changes meanwhile.
        bound = getattr(_CALL, "ph", None)
        if bound is not None:
            return bound
        with self._lock:
            if self._ph is None:
                self._ph = Phosphene(self._url)
            _CALL.ph = self._ph
            return self._ph

    def invalidate(self) -> None:
        """This call's studio stopped answering: forget it, unless another call already found a new one."""
        failed = getattr(_CALL, "ph", None)
        with self._lock:
            if failed is None or self._ph is failed:
                self._ph = None

    def _queued(self, job_id: str, wait: bool, timeout: float = 1500) -> dict:
        if not wait:
            return {"ok": True, "job_id": job_id, "status": "queued",
                    "next": "call phosphene_wait with this job_id (renders take minutes)"}
        return self.phosphene_wait(job_id, timeout)

    def phosphene_status(self) -> dict:
        ph = self.ph()
        info = ph.info()
        j = ph.jobs()
        info["now"] = {"running": [_slim(r) for r in j["running"]], "queued": len(j["queued"]), "paused": j["paused"]}
        others = [s["url"] for s in find_servers(scan=False) if s["url"] != ph.url]
        if others:
            info["other_studios"] = others
        return info

    def phosphene_make_video(self, prompt, engine="ltx", image=None, quality=None, seconds=None, seed=None,
                             label=None, wait=False) -> dict:
        doc = self.ph().video(prompt, engine=engine, image=image, quality=quality, seconds=seconds, seed=seed, label=label)
        return self._queued(doc["id"], wait)

    def phosphene_make_image(self, prompt, n=1, aspect="16:9", seed=None, wait=False) -> dict:
        doc = self.ph().image(prompt, n=n, aspect=aspect, seed=seed)
        return self._queued(doc["id"], wait)

    def phosphene_make_speech(self, text, voice=None, speed=None, wait=False) -> dict:
        doc = self.ph().speech(text, voice=voice, speed=speed)
        return self._queued(doc.get("job_id") or doc.get("id"), wait)

    def phosphene_make_music(self, style, lyrics="", seconds=None, title=None, seed=None, wait=False) -> dict:
        doc = self.ph().music(style, lyrics=lyrics or "", seconds=seconds, title=title, seed=seed)
        return self._queued(doc["id"], wait)

    def phosphene_list_jobs(self, limit=10) -> dict:
        j = self.ph().jobs()
        return {"running": [_slim(r) for r in j["running"]], "queued": [_slim(r) for r in j["queued"]],
                "recent": [_slim(r) for r in j["history"][: max(0, int(limit))]], "paused": j["paused"]}

    def phosphene_wait(self, job_id, timeout_sec=1500) -> dict:
        ph = self.ph()
        try:
            row = ph.wait(job_id, timeout=float(timeout_sec), every=5, cancelled=getattr(_CALL, "cancelled", None))
        except WaitCancelled:
            raise
        except PhospheneError as e:
            if "still" in str(e):
                cur = ph.job(job_id) or {}
                return {"ok": True, "job_id": job_id, "finished": False, **_slim(cur),
                        "next": "still rendering; call phosphene_wait again"}
            raise
        out = {"ok": row.get("status") == "done", "job_id": job_id, "finished": True, **_slim(row)}
        out["files"] = ph.outputs_of(row)
        return out

    def phosphene_fetch(self, job_id, dest) -> dict:
        return {"ok": True, "files": self.ph().fetch(job_id, dest)}

    def phosphene_stop(self, job_id=None) -> dict:
        return {"ok": True, "result": self.ph().stop(job_id)}


def call_tool(tools: Tools, name: str, arguments: dict) -> dict:
    """MCP tools/call result. Failures are tool results with isError (the model reads the sentence), not protocol errors."""
    _CALL.ph = None
    fn = getattr(tools, name, None) if name.startswith("phosphene_") else None
    if fn is None or name not in {t["name"] for t in TOOLS}:
        return {"content": [{"type": "text", "text": f"Unknown tool {name}."}], "isError": True}
    try:
        result = fn(**(arguments or {}))
        return {"content": [{"type": "text", "text": json.dumps(result, indent=2, default=str)}],
                "structuredContent": result, "isError": False}
    except NotRunning as e:
        tools.invalidate()
        return {"content": [{"type": "text", "text": str(e)}], "isError": True}
    except PhospheneError as e:
        detail = {"error": str(e), "status": e.status}
        if e.body.get("code"):
            detail["code"] = e.body["code"]
        return {"content": [{"type": "text", "text": json.dumps(detail)}], "isError": True}
    except TypeError as e:
        return {"content": [{"type": "text", "text": f"Bad arguments for {name}: {e}"}], "isError": True}


def handle(tools: Tools, msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:   # a notification (initialized, cancelled): nothing to answer
        return None
    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}
    if method == "initialize":
        asked = params.get("protocolVersion")
        return ok({"protocolVersion": asked if asked in PROTOCOLS else PROTOCOLS[0],
                   "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                   "instructions": "Phosphene renders video, images, speech and music locally on this Mac. "
                                   "Call phosphene_status first. Renders take minutes: queue, then phosphene_wait."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        return ok(call_tool(tools, str(params.get("name") or ""), params.get("arguments") or {}))
    if method in ("resources/list", "prompts/list"):
        return ok({method.split("/")[0]: []})
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"Method not found: {method}"}}


def serve(stdin=sys.stdin, stdout=sys.stdout, url: str | None = None) -> None:
    """Newline JSON-RPC over stdio. A tools/call runs on its own thread, so while phosphene_wait blocks
    for minutes the loop keeps reading: a ping or a phosphene_stop is answered at once, and
    notifications/cancelled ends that wait (the render keeps going) with no reply, as MCP asks."""
    tools = Tools(url)
    lock = threading.Lock()
    inflight: dict = {}   # request id -> threading.Event, for tools/call still running
    calls: list = []      # the tools/call threads this session started

    def send(obj) -> None:
        with lock:
            stdout.write(json.dumps(obj) + "\n")
            stdout.flush()

    def run_call(m: dict, ev: threading.Event) -> None:
        _CALL.cancelled = ev
        try:
            r = handle(tools, m)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc(file=sys.stderr)
            r = {"jsonrpc": "2.0", "id": m.get("id"), "error": {"code": -32603, "message": str(e)}}
        finally:
            inflight.pop(m.get("id"), None)
        if r is not None and not ev.is_set():
            send(r)

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if isinstance(msg, dict) and msg.get("method") == "notifications/cancelled":
            ev = inflight.get((msg.get("params") or {}).get("requestId"))
            if ev is not None:
                ev.set()
            continue
        if isinstance(msg, dict) and msg.get("method") == "tools/call" and msg.get("id") is not None:
            ev = threading.Event()
            inflight[msg["id"]] = ev
            t = threading.Thread(target=run_call, args=(msg, ev), daemon=True)
            calls.append(t)
            t.start()
            continue
        batch = msg if isinstance(msg, list) else [msg]
        replies = []
        for m in batch:
            try:
                r = handle(tools, m) if isinstance(m, dict) else None
            except Exception as e:  # noqa: BLE001 - one bad request must not end the session
                traceback.print_exc(file=sys.stderr)
                r = {"jsonrpc": "2.0", "id": m.get("id") if isinstance(m, dict) else None,
                     "error": {"code": -32603, "message": str(e)}}
            if r is not None:
                replies.append(r)
        if replies:
            send(replies if isinstance(msg, list) else replies[0])
    # stdin closed: give calls already running a moment to answer (a piped one-shot call still gets its
    # reply); a long wait is abandoned with the process, and the render keeps going in Phosphene.
    for t in calls:
        t.join(timeout=10)


if __name__ == "__main__":
    serve()
