"""/agents family routes - what an agent tool needs to drive this studio.

GET /agents/info   who answers on this port (always "app": "phosphene"), the version, where it runs
                   (pinokio / mac-app), Free or Pro, which engines are installed and their choices,
                   and the exact command lines that connect the CLI and the MCP server to it.

It adds no way to make anything: agents/phosphene_cli.py and agents/phosphene_mcp.py render through
the same /queue/add, /speech/generate and /status the page uses, so every check the studio applies
to the page (licences, this Mac's limits, Free and Pro) applies to an agent unchanged.
Cheap on purpose (no disk walk), because clients call it to find a studio.
"""
from __future__ import annotations

import os
import shlex
import sys

from panel.routes import get

P = None  # the running mlx_ltx_panel module; assigned at wiring time


def _safe(fn, default=None):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - one broken probe must not hide the studio from its tools
        return default


def agents_payload() -> dict:
    agents_dir = P.ROOT / "agents"
    py = sys.executable
    mcp = str(agents_dir / "phosphene_mcp.py")
    cli = str(agents_dir / "phosphene_cli.py")
    h3_on = bool(_safe(P.h3_available, False))
    return {
        "app": "phosphene",
        "api": 1,
        "url": f"http://127.0.0.1:{P.PORT}",
        "port": P.PORT,
        "pid": os.getpid(),
        "version": _safe(P.running_version),
        "build": _safe(P.boot_build_stamp, {}),
        "distribution": P._dist.distribution(),
        "edition": _safe(P._dist.edition),
        "profile": P.PROFILE,
        "pro": _safe(P.pro_status, {"active": False}),
        "engines": {
            "ltx": {"available": not _safe(lambda: P.capability_missing("render"), ["?"]),
                    "default": True,
                    "qualities": [q for q in ("quick", "balanced", "standard", "high", "high_720p")
                                  if q in P.LTX_QUALITIES]},
            "h3": {"available": h3_on,
                   "qualities": [k for k, q in P.H3_QUALITIES.items() if q.get("offered", True)],
                   "lengths": [k for k, l in P.H3_LENGTHS.items() if l.get("offered", True)]},
            "speech": {"available": bool(_safe(lambda: P.speech_status()["available"], False))},
            "music": {"available": bool(_safe(lambda: not P.music_paths()["missing"], False))},
            "image": {"available": True, "note": "the engine chosen in Settings; it downloads on first use"},
        },
        "outputs_dir": str(P.OUTPUT),
        "agents": {
            "cli": cli, "mcp": mcp, "python": py,
            "mcp_config": {"mcpServers": {"phosphene": {"command": py, "args": [mcp]}}},
            "claude_code": f"claude mcp add phosphene -- {shlex.quote(py)} {shlex.quote(mcp)}",
            "codex": f"codex mcp add phosphene -- {shlex.quote(py)} {shlex.quote(mcp)}",
            "docs": "docs/AGENT_TOOLS.md",
        },
    }


@get("/agents/info")
def get_agents_info(h, parsed) -> None:
    h._json(agents_payload())
