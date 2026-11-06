"""A small client for a running Phosphene studio: find it, talk to it, wait for a job.

Standard library only (Python 3.9+), so it runs on any Python on a Mac: the
studio's own venv, the Mac app's runtime, /usr/bin/python3. The CLI
(phosphene_cli.py) and the MCP server (phosphene_mcp.py) are both thin layers
over this one module, so they find the studio the same way and say the same
thing when it is not running.

Finding the studio, in order:
  1. PHOSPHENE_URL (e.g. http://127.0.0.1:8198), when set: that and nothing else.
  2. The records running studios write to ~/.phosphene/servers/<port>.json
     (agent_discovery.py in the studio), newest first, pid alive and answering.
  3. The ports the studio uses when nobody wrote a record (an older studio):
     8198 (Pinokio), 8199 (dev profile), 8930-8999 (the Mac app).

Every request goes through the studio's own HTTP API, the same routes its page
uses, so the studio applies the same checks to an agent as to a person: model
licences, what this Mac can run, and what Free and Pro include. Nothing here
decides any of that.
"""
from __future__ import annotations

import json
import mimetypes
import os
import re
import shutil
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

PINOKIO_PORT = 8198
DEV_PORT = 8199
MAC_APP_PORTS = range(8930, 9000)
TERMINAL = ("done", "failed", "cancelled", "stopped", "error")

NOT_RUNNING = (
    "Phosphene is not running on this Mac. Start it first: open the Phosphene app, "
    "or press Start on Phosphene in Pinokio. Then try again. "
    "(If it runs somewhere unusual, set PHOSPHENE_URL, e.g. http://127.0.0.1:8198.)")


class PhospheneError(Exception):
    """A failure with a sentence a person can act on. `status` and `body` carry the studio's answer, if any."""

    def __init__(self, message: str, status: int | None = None, body: dict | None = None):
        super().__init__(message)
        self.status = status
        self.body = body or {}


class NotRunning(PhospheneError):
    pass


def discovery_dir() -> Path:
    override = (os.environ.get("PHOSPHENE_DISCOVERY_DIR") or "").strip()
    return Path(override).expanduser() if override else Path.home() / ".phosphene" / "servers"


def _pid_alive(pid) -> bool:
    if isinstance(pid, bool):
        return False
    try:
        pid = int(pid)
    except (TypeError, ValueError, OverflowError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, OverflowError):
        return False
    except PermissionError:
        return True
    return True


def _port_open(port: int, timeout: float = 0.15) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout):
            return True
    except OSError:
        return False


def _get_json(url: str, timeout: float = 3.0):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def probe(url: str, timeout: float = 3.0) -> dict | None:
    """The studio's identity at `url`, or None when nothing (or something else) answers there."""
    url = url.rstrip("/")
    try:
        info = _get_json(url + "/agents/info", timeout)
        if isinstance(info, dict) and info.get("app") == "phosphene":
            return info
    except urllib.error.HTTPError as e:
        if e.code != 404:
            return None
    except (OSError, ValueError):
        return None
    # A studio from before /agents/info: /pro/status is a Phosphene-only route.
    try:
        pro = _get_json(url + "/pro/status", timeout)
        if isinstance(pro, dict) and "active" in pro:
            return {"app": "phosphene", "url": url, "legacy": True, "pro": pro}
    except (OSError, ValueError):
        pass
    return None


def discovery_records() -> list[dict]:
    out = []
    d = discovery_dir()
    try:
        files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        files = []
    for f in files:
        try:
            rec = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(rec, dict) and rec.get("app") == "phosphene" and rec.get("url"):
            rec["_file"] = str(f)
            out.append(rec)
    return out


def find_servers(scan: bool = True) -> list[dict]:
    """Every studio answering on this Mac: [{url, source, info}], best first."""
    explicit = (os.environ.get("PHOSPHENE_URL") or "").strip().rstrip("/")
    if explicit:
        info = probe(explicit)
        return [{"url": explicit, "source": "PHOSPHENE_URL", "info": info}] if info else []
    found, seen = [], set()
    for rec in discovery_records():
        url = str(rec["url"]).rstrip("/")
        if url in seen or not _pid_alive(rec.get("pid")):
            continue
        info = probe(url)
        if info:
            seen.add(url)
            found.append({"url": url, "source": "discovery", "record": rec.get("_file"), "info": info})
    if scan:
        for port in [PINOKIO_PORT, DEV_PORT, *MAC_APP_PORTS]:
            url = f"http://127.0.0.1:{port}"
            if url in seen or not _port_open(port):
                continue
            info = probe(url)
            if info:
                seen.add(url)
                found.append({"url": url, "source": "port scan", "info": info})
    return found


def find_server() -> str:
    servers = find_servers()
    if not servers:
        raise NotRunning(NOT_RUNNING)
    return servers[0]["url"]


def _multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = "----phosphene" + uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, path in files.items():
        p = Path(path)
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; filename="{p.name}"\r\n'
                      f"Content-Type: {ctype}\r\n\r\n").encode() + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _loads_status(raw: bytes):
    # /status can carry control characters from prompts with newlines (docs/API.md).
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", raw.decode("utf-8", "replace"))
    return json.loads(text, strict=False)


def row_outputs(row: dict) -> list[str]:
    """Every file a finished job made. An image batch keeps only its first picture in output_path;
    the whole batch is params.candidate_paths."""
    paths = []
    params = row.get("params") if isinstance(row.get("params"), dict) else {}
    for v in [row.get(k) for k in ("output_path", "output_paths", "outputs", "paths")] + [params.get("candidate_paths")]:
        if isinstance(v, str) and v:
            paths.append(v)
        elif isinstance(v, list):
            paths += [x if isinstance(x, str) else (x or {}).get("path") for x in v if isinstance(x, (str, dict))]
    return [p for p in dict.fromkeys(paths) if p]


class WaitCancelled(PhospheneError):
    """The caller gave up waiting. The job itself keeps going in Phosphene."""


class Phosphene:
    """One studio. `Phosphene()` finds it; `Phosphene("http://127.0.0.1:8198")` uses that one."""

    def __init__(self, url: str | None = None, timeout: float = 30.0):
        self.url = (url or find_server()).rstrip("/")
        self.timeout = timeout

    # ---- transport
    def request(self, method: str, path: str, *, form: dict | None = None, json_body=None,
                files: dict | None = None, timeout: float | None = None, raw: bool = False):
        data, headers = None, {}
        if files:
            data, headers["Content-Type"] = _multipart({k: str(v) for k, v in (form or {}).items()}, files)
        elif json_body is not None:
            data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
        elif form is not None:
            data = urllib.parse.urlencode({k: str(v) for k, v in form.items() if v is not None}).encode()
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.url + path, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            try:
                doc = json.loads(body.decode("utf-8", "replace"))
            except ValueError:
                doc = {}
            msg = (doc.get("error") if isinstance(doc, dict) else None) or f"HTTP {e.code} from Phosphene"
            if e.code == 451 and isinstance(doc, dict) and doc.get("code") == "licence_required":
                # Deliberately no way to accept from here: accepting on someone's behalf is accepting
                # for them (docs/API.md, Model licences). The person reads the terms and ticks the box.
                msg += (" The person using this Mac has to read and accept that model's licence once, "
                        "in Phosphene itself; an agent cannot accept it for them.")
            raise PhospheneError(msg, e.code, doc if isinstance(doc, dict) else {}) from None
        except (urllib.error.URLError, ConnectionError, socket.timeout) as e:
            raise NotRunning(f"Lost the connection to Phosphene at {self.url} ({e}). " + NOT_RUNNING) from None
        if raw:
            return body
        return _loads_status(body) if body else {}

    def get(self, path: str, **kw):
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw):
        return self.request("POST", path, **kw)

    # ---- what is here
    def info(self) -> dict:
        info = probe(self.url)
        if not info:
            raise NotRunning(NOT_RUNNING)
        return info

    def status(self) -> dict:
        return self.get("/status")

    # ---- inputs
    def upload(self, path: str) -> str:
        """Copy a picture into the studio's uploads and return the path it can use."""
        p = Path(path).expanduser()
        if not p.is_file():
            raise PhospheneError(f"There is no file at {p}.")
        doc = self.post("/upload", files={"image": str(p)}, timeout=120)
        out = doc.get("path") if isinstance(doc, dict) else None
        if not out:
            raise PhospheneError(f"Phosphene did not accept {p.name}: {doc}")
        return out

    # ---- make things
    def video(self, prompt: str, *, engine: str = "ltx", mode: str | None = None, image: str | None = None,
              quality: str | None = None, seconds: float | None = None, length: str | None = None,
              seed: int | None = None, label: str | None = None, extra: dict | None = None) -> dict:
        form: dict = {"prompt": prompt, "engine": engine}
        if image:
            form["image"] = image if Path(image).is_absolute() and _under_studio(image) else self.upload(image)
        form["mode"] = mode or ("i2v" if image else "t2v")
        if engine == "h3":
            if quality:
                form["h3_quality"] = quality
            form["h3_length"] = length or (f"{int(seconds)}s" if seconds else "5s")
        else:
            if quality:
                form["quality"] = quality
            if seconds:
                form["duration"] = seconds
        if seed is not None:
            form["seed"] = seed
        if label:
            form["label"] = label
        form["open_when_done"] = "off"
        form.update(extra or {})
        return self.post("/queue/add", form=form)

    def image(self, prompt: str, *, n: int = 1, aspect: str = "16:9", engine: str = "auto",
              seed: int | None = None, label: str | None = None) -> dict:
        form = {"mode": "image", "prompt": prompt, "n": n, "aspect": aspect, "engine_override": engine}
        if seed is not None:
            form["seed"] = seed
        if label:
            form["label"] = label
        return self.post("/queue/add", form=form)

    def speech(self, text: str, *, voice: str | None = None, speed: float | None = None) -> dict:
        spec: dict = {"text": text, "wait": False}
        if voice:
            spec["voice"] = voice
        if speed:
            spec["speed"] = speed
        try:
            return self.post("/speech/generate", json_body=spec)
        except PhospheneError as e:
            if e.status == 404:  # a studio from before Audio > Speech (the 4.19 line)
                raise PhospheneError("This Phosphene has no text to speech. It comes with a newer version "
                                     "of Phosphene; video, images and music work here.", 404) from None
            raise

    def music(self, style: str, *, lyrics: str = "", instrumental: bool = False, seconds: int | None = None,
              title: str | None = None, seed: int | None = None) -> dict:
        form = {"mode": "music", "engine": "music", "music_style": style, "music_lyrics": lyrics,
                "music_instrumental": "on" if instrumental or not lyrics else "off"}
        if seconds:
            form["music_max_seconds"] = seconds
        if title:
            form["music_title"] = title
        if seed is not None:
            form["seed"] = seed
        return self.post("/queue/add", form=form)

    # ---- jobs
    def jobs(self) -> dict:
        s = self.status()
        cur = s.get("current")
        return {"running": [cur] if cur else [], "queued": s.get("queue") or [],
                "history": s.get("history") or [], "paused": bool(s.get("paused"))}

    def job(self, job_id: str) -> dict | None:
        j = self.jobs()
        for group, rows in (("running", j["running"]), ("queued", j["queued"]), ("history", j["history"])):
            for row in rows:
                if row and row.get("id") == job_id:
                    row = dict(row)
                    row.setdefault("status", "running" if group == "running" else "queued" if group == "queued" else None)
                    if group == "running":
                        row["status"] = row.get("status") or "running"
                    return row
        return None

    def wait(self, job_id: str, timeout: float = 3600, every: float = 5.0, on_progress=None, cancelled=None) -> dict:
        """Poll until the job ends. `cancelled` (a threading.Event) ends the wait early with WaitCancelled."""
        deadline = time.time() + timeout
        last = None
        while True:
            row = self.job(job_id)
            if row is None:
                raise PhospheneError(f"Phosphene has no job {job_id} (it may have been removed).")
            st = row.get("status")
            if st in TERMINAL:
                return row
            if on_progress:
                prog = row.get("progress") or {}
                line = f"{st} {prog.get('pct', '')}% {prog.get('phase_label', '')}".strip()
                if line != last:
                    on_progress(line, row)
                    last = line
            if time.time() > deadline:
                raise PhospheneError(f"Job {job_id} is still {st} after {int(timeout)} s; it keeps going in Phosphene.")
            if cancelled is not None:
                if cancelled.wait(every):
                    raise WaitCancelled(f"Stopped waiting for {job_id}; it keeps going in Phosphene.")
            else:
                time.sleep(every)

    def stop(self, job_id: str | None = None) -> dict:
        return self.post("/stop" + (f"?id={urllib.parse.quote(job_id)}" if job_id else ""), form={})

    def outputs_of(self, row: dict) -> list[str]:
        return row_outputs(row)

    def fetch(self, job_id: str, dest: str) -> list[str]:
        """Copy a finished job's files into `dest`. Returns the new paths."""
        row = self.job(job_id)
        if row is None:
            raise PhospheneError(f"Phosphene has no job {job_id}.")
        if row.get("status") != "done":
            raise PhospheneError(f"Job {job_id} is {row.get('status')}, not done: {row.get('error') or ''}".strip())
        out_dir = Path(dest).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for src in self.outputs_of(row):
            target = out_dir / Path(src).name
            if Path(src).is_file():
                shutil.copy2(src, target)
            else:
                target.write_bytes(self.get("/file?path=" + urllib.parse.quote(src), raw=True, timeout=600))
            saved.append(str(target))
        if not saved:
            raise PhospheneError(f"Job {job_id} finished but names no output file.")
        return saved

    # ---- licences (the studio asks before some models run; a person accepts)
    def open_cut(self, spec: dict, *, wait: bool = True) -> dict:
        """An agent's cut -> a film in the Editor, ready to edit by hand. `spec` is a cut spec
        (`phosphene_cut: 1`, clips with in/at, soundtrack, overlays, levels; docs/API.md). Returns the new
        film's id, the notes (a clip slowed to fill its slot, a hole) and `editor_url` (relative to the studio).
        `wait` answers once the proxies are built, so the film plays at once."""
        doc = self.post("/storyboard/edit/import", json_body={"cut": spec, "wait": bool(wait)},
                        timeout=900 if wait else None)
        if isinstance(doc, dict) and doc.get("editor_url"):
            doc["editor_url"] = self.url + doc["editor_url"]
        return doc

    def licences(self) -> dict:
        return self.get("/licences")


def _under_studio(path: str) -> bool:
    # A path inside the studio's uploads/outputs can be passed as is; anything else is uploaded.
    return "/uploads/" in path or "/panel_uploads/" in path or "/outputs/" in path or "/mlx_outputs/" in path
