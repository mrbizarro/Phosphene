#!/usr/bin/env python3
"""phosphene: drive the Phosphene studio running on this Mac from a terminal or a script.

    phosphene status                         is it running, where, Free or Pro, what it can make
    phosphene video "a red tram in the rain" [--engine ltx|h3] [--image still.png] [--quality ..] [--seconds 5] [--wait] [--out DIR]
    phosphene image "a poster of a red tram" [--n 2] [--aspect 1:1] [--wait] [--out DIR]
    phosphene speech "Hello there." [--voice ryan] [--wait] [--out DIR]
    phosphene music "warm lo-fi piano, rain" [--lyrics FILE] [--seconds 60] [--wait] [--out DIR]
    phosphene jobs                           running, queued and recent jobs
    phosphene wait JOB [--out DIR]           wait until a job finishes (and copy its files)
    phosphene fetch JOB --out DIR            copy a finished job's files
    phosphene stop [JOB]                     stop the running job
    phosphene servers                        every Phosphene studio answering on this Mac
    phosphene mcp-config                     the MCP entry to paste into Claude Code / Codex / any MCP client

Add --json to any command for machine-readable output. Exit codes: 0 ok, 1 the studio refused
or the job failed, 2 bad arguments, 3 Phosphene is not running.

Works the same for a Pinokio install and for the Mac app: it finds the running studio by
itself (agents/phosphene_client.py). The studio checks every request exactly as it checks
its own page, including what Free and Pro include.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

# Never write __pycache__ beside these files: in the Mac app they sit inside a verified code
# folder, and a file that is not in its manifest fails the next launch's tree check.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phosphene_client import NotRunning, Phosphene, PhospheneError, find_servers  # noqa: E402

HERE = Path(__file__).resolve().parent


def _print(args, data, text: str | None = None) -> None:
    if args.json or text is None:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(text)


def _finish(args, ph: Phosphene, job_id: str) -> int:
    if not args.wait and not args.out:
        _print(args, {"ok": True, "id": job_id}, f"queued {job_id}  (phosphene wait {job_id} --out DIR)")
        return 0
    progress = None if args.json else (lambda line, _row: print(f"  {line}", file=sys.stderr, flush=True))
    row = ph.wait(job_id, timeout=args.timeout, on_progress=progress)
    if row.get("status") != "done":
        _print(args, {"ok": False, "id": job_id, "status": row.get("status"), "error": row.get("error")},
               f"{job_id} {row.get('status')}: {row.get('error') or ''}".strip())
        return 1
    files = ph.fetch(job_id, args.out) if args.out else ph.outputs_of(row)
    _print(args, {"ok": True, "id": job_id, "status": "done", "elapsed_sec": row.get("elapsed_sec"), "files": files},
           "\n".join([f"done {job_id} in {row.get('elapsed_sec')} s"] + files))
    return 0


def cmd_status(args, ph: Phosphene) -> int:
    info = ph.info()
    if args.json:
        _print(args, info)
        return 0
    pro = info.get("pro") or {}
    eng = info.get("engines") or {}
    ready = ", ".join(k for k, v in eng.items() if (v or {}).get("available")) or "nothing yet"
    j = ph.jobs()
    lines = [f"Phosphene {info.get('version') or ''} at {ph.url} ({info.get('distribution', '?')})".replace("  ", " "),
             f"Plan: {'Pro' if pro.get('active') else 'Free'}",
             f"Can make: {ready}",
             f"Jobs: {len(j['running'])} running, {len(j['queued'])} queued" + (" (queue paused)" if j["paused"] else "")]
    print("\n".join(lines))
    return 0


def cmd_servers(args, _ph) -> int:
    rows = find_servers()
    if args.json:
        _print(args, rows)
    elif not rows:
        print("No Phosphene studio is answering on this Mac.")
    else:
        for r in rows:
            i = r.get("info") or {}
            print(f"{r['url']}  {i.get('distribution', '?')}  {i.get('version') or ''}  (found by {r['source']})")
    return 0 if rows else 3


def cmd_video(args, ph: Phosphene) -> int:
    doc = ph.video(args.prompt, engine=args.engine, image=args.image, quality=args.quality,
                   seconds=args.seconds, length=args.length, seed=args.seed, label=args.label)
    return _finish(args, ph, doc["id"])


def cmd_image(args, ph: Phosphene) -> int:
    doc = ph.image(args.prompt, n=args.n, aspect=args.aspect, engine=args.engine, seed=args.seed, label=args.label)
    return _finish(args, ph, doc["id"])


def cmd_speech(args, ph: Phosphene) -> int:
    doc = ph.speech(args.text, voice=args.voice, speed=args.speed)
    return _finish(args, ph, doc.get("job_id") or doc.get("id"))


def cmd_music(args, ph: Phosphene) -> int:
    lyrics = Path(args.lyrics).read_text(encoding="utf-8") if args.lyrics else ""
    doc = ph.music(args.style, lyrics=lyrics, instrumental=args.instrumental, seconds=args.seconds,
                   title=args.title, seed=args.seed)
    return _finish(args, ph, doc["id"])


def cmd_jobs(args, ph: Phosphene) -> int:
    j = ph.jobs()
    if args.json:
        _print(args, j)
        return 0
    def line(r, st=None):
        p = r.get("params") or {}
        return f"{r.get('id')}  {st or r.get('status')}  {p.get('mode', '')}/{p.get('engine', 'ltx')}  {str(p.get('prompt', ''))[:60]}"
    for r in j["running"]:
        prog = r.get("progress") or {}
        print(line(r, "running") + (f"  {prog.get('pct')}%" if prog.get("pct") is not None else ""))
    for r in j["queued"]:
        print(line(r, "queued"))
    for r in j["history"][: args.limit]:
        print(line(r))
    return 0


def cmd_wait(args, ph: Phosphene) -> int:
    args.wait = True
    return _finish(args, ph, args.job)


def cmd_fetch(args, ph: Phosphene) -> int:
    files = ph.fetch(args.job, args.out)
    _print(args, {"ok": True, "files": files}, "\n".join(files))
    return 0


def cmd_stop(args, ph: Phosphene) -> int:
    _print(args, ph.stop(args.job), "stop requested")
    return 0


def mcp_entry() -> dict:
    return {"mcpServers": {"phosphene": {"command": sys.executable, "args": [str(HERE / "phosphene_mcp.py")]}}}


def cmd_mcp_config(args, _ph) -> int:
    entry = mcp_entry()
    if args.json:
        _print(args, entry)
        return 0
    srv = entry["mcpServers"]["phosphene"]
    print(json.dumps(entry, indent=2))
    line = " ".join(shlex.quote(x) for x in [srv["command"], *srv["args"]])
    print("\nClaude Code:  claude mcp add phosphene -- " + line)
    print("Codex:        codex mcp add phosphene -- " + line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="phosphene", description="Drive the Phosphene studio running on this Mac.")
    ap.add_argument("--url", help="a studio URL (default: find the running one)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def make(name, fn, help_):
        p = sub.add_parser(name, help=help_)
        p.set_defaults(fn=fn)
        p.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
        return p

    def finishing(p):
        p.add_argument("--wait", action="store_true", help="wait until it is finished")
        p.add_argument("--out", help="copy the finished files here (implies --wait)")
        p.add_argument("--timeout", type=float, default=3600, help="seconds to wait (default 3600)")
        p.add_argument("--seed", type=int)
        p.add_argument("--label")

    make("status", cmd_status, "is Phosphene running, Free or Pro, what it can make")
    make("servers", cmd_servers, "every studio answering on this Mac")
    p = make("video", cmd_video, "make a video clip")
    p.add_argument("prompt")
    p.add_argument("--engine", default="ltx", choices=["ltx", "h3"])
    p.add_argument("--image", help="start frame (a picture on this Mac)")
    p.add_argument("--quality", help="LTX: quick|balanced|standard|high|high_720p; H3: draft|fast_hd|standard|high|...")
    p.add_argument("--seconds", type=float, help="length in seconds (LTX any; H3 3, 5, 10 or 15)")
    p.add_argument("--length", help="H3 length key, e.g. 5s, 10s, 10s_dense")
    finishing(p)
    p = make("image", cmd_image, "make still images")
    p.add_argument("prompt")
    p.add_argument("--n", type=int, default=1)
    p.add_argument("--aspect", default="16:9")
    p.add_argument("--engine", default="auto", help="image engine (default: the one set in Phosphene)")
    finishing(p)
    p = make("speech", cmd_speech, "text to speech")
    p.add_argument("text")
    p.add_argument("--voice")
    p.add_argument("--speed", type=float)
    finishing(p)
    p = make("music", cmd_music, "make a song or an instrumental")
    p.add_argument("style", help="the style: genre, instruments, mood")
    p.add_argument("--lyrics", help="a text file with the lyrics (none = instrumental)")
    p.add_argument("--instrumental", action="store_true")
    p.add_argument("--seconds", type=int)
    p.add_argument("--title")
    finishing(p)
    p = make("jobs", cmd_jobs, "running, queued and recent jobs")
    p.add_argument("--limit", type=int, default=10)
    p = make("wait", cmd_wait, "wait for a job to finish")
    p.add_argument("job")
    p.add_argument("--out")
    p.add_argument("--timeout", type=float, default=3600)
    p = make("fetch", cmd_fetch, "copy a finished job's files")
    p.add_argument("job")
    p.add_argument("--out", required=True)
    p = make("stop", cmd_stop, "stop the running job")
    p.add_argument("job", nargs="?")
    make("mcp-config", cmd_mcp_config, "the MCP server entry for agent tools")
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        ph = None if args.fn in (cmd_servers, cmd_mcp_config) else Phosphene(args.url)
        return args.fn(args, ph)
    except NotRunning as e:
        _print(args, {"ok": False, "error": str(e), "code": "not_running"}, str(e))
        return 3
    except PhospheneError as e:
        _print(args, {"ok": False, "error": str(e), "status": e.status, **({"detail": e.body} if e.body else {})}, str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
