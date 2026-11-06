"""/pro family routes - whether Pro is active on this Mac, and the measured Free-vs-Pro comparison.

GET /pro/status    {active, plan, until, holder, source}; Free is the default and nothing here nags
GET /pro/compare   the bundled "same clip, Free vs Pro" comparisons (assets/pro/compare.json)

Pro is on only with a signed capability token for this Mac (pro_token.py, passed by the Mac app);
PHOSPHENE_PRO=<plan> is a developer override honoured on a test rig only (PHOSPHENE_TEST_RIG=1).
"""
from __future__ import annotations

import json

from panel.routes import get

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/pro/status")
def get_pro_status(h, parsed) -> None:
    h._json(P.pro_status())


@get("/pro/compare")
def get_pro_compare(h, parsed) -> None:
    try:
        data = json.loads((P.ROOT / "assets" / "pro" / "compare.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"items": []}
    h._json(data)
