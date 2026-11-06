"""/capabilities - what every engine and variant can do (model_capabilities.py).

GET /capabilities   the resolved table: per variant its modes, canvases,
                    lengths, inputs, outputs, options, Pro status and the one
                    plain reason the page shows on every greyed control.

The page also gets the same table in BOOT.capabilities for first paint; this
route is for agents and external tools (docs/AGENT_API.md) and for a page that
wants it again after Pro turns on.
"""
from __future__ import annotations

import model_capabilities
from panel.routes import get

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/capabilities")
def get_capabilities(h, parsed) -> None:
    h._json(P._capabilities_boot())
