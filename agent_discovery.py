"""Where a running Phosphene studio can be found by the tools that drive it.

The studio listens on a different port in each place it runs: 8198 in a
Pinokio install, 8199 in the dev profile, and a free port in 8930-8999 in the
Mac app (PanelProcess.freePort). An agent tool (agents/phosphene_cli.py, the
MCP server agents/phosphene_mcp.py, a script) must not have to know which.

So the studio announces itself: once its listener is bound it writes
``~/.phosphene/servers/<port>.json`` and removes it when it stops. HOME is the
one location both wrappers share (Pinokio rewrites TMPDIR, the Mac app keeps its
data in one folder of its own), and the file holds no secret: the URL, the pid,
the version and where this install keeps its outputs and its agent tools.

``PHOSPHENE_DISCOVERY_DIR`` moves the folder (tests, sandboxes).
``PHOSPHENE_DISCOVERY=0`` turns announcing off.

Readers must treat a file as a hint: they check the pid is alive and the URL
answers before they trust it (agents/phosphene_client.py does).
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

FORMAT = 1


def discovery_dir(env=None) -> Path:
    env = os.environ if env is None else env
    override = (env.get("PHOSPHENE_DISCOVERY_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".phosphene" / "servers"


def enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return (env.get("PHOSPHENE_DISCOVERY") or "").strip().lower() not in ("0", "off", "false", "no")


def record_path(port: int, env=None) -> Path:
    return discovery_dir(env) / f"{int(port)}.json"


def announce(port: int, info: dict, env=None) -> Path | None:
    """Write this studio's record. Never raises: a read-only home must not stop a studio from starting."""
    if not enabled(env):
        return None
    try:
        target = record_path(port, env)
        target.parent.mkdir(parents=True, exist_ok=True)
        prune(env)
        record = {"format": FORMAT, "app": "phosphene", "port": int(port),
                  "url": f"http://127.0.0.1:{int(port)}", "pid": os.getpid(),
                  "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "python": sys.executable}
        record.update({k: v for k, v in (info or {}).items() if k not in ("format", "app", "port", "url", "pid")})
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(record, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        return target
    except Exception:  # noqa: BLE001 - discovery is optional; nothing here may stop a studio from starting
        return None


def _alive(pid) -> bool:
    try:
        if isinstance(pid, bool) or int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
    except (ProcessLookupError, ValueError, TypeError, OverflowError):
        return False
    except PermissionError:
        return True
    return int(pid) > 0


def prune(env=None) -> int:
    """Remove records whose process is gone. A studio stopped by a signal never reaches withdraw() (the
    Mac app and Pinokio both stop it that way), so the next studio to start tidies up after it."""
    removed = 0
    try:
        for f in discovery_dir(env).glob("*.json"):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
                if not isinstance(rec, dict) or rec.get("app") != "phosphene":
                    continue   # not a studio record: leave it alone
                if not _alive(rec.get("pid")):
                    f.unlink()
                    removed += 1
            except (OSError, ValueError):
                continue
    except Exception:  # noqa: BLE001
        pass
    return removed


def withdraw(port: int, env=None) -> bool:
    """Remove this studio's record, only if it is still ours (a newer studio on the same port keeps its own)."""
    try:
        target = record_path(port, env)
        data = json.loads(target.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("pid") != os.getpid():
            return False
        target.unlink()
        return True
    except Exception:  # noqa: BLE001 - runs on the way out; never in the way of the shutdown after it
        return False
