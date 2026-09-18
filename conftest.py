"""NO TEST MAY TOUCH THE PANEL SOMEBODY IS USING.

2026-09-18: a full-suite run on this machine wrote
`analytics_query_key = "phx_secret_from_the_other_panel"` and
`civitai_api_key = "civ_from_the_other_panel"` into the LIVE
`state/panel_settings.json` — destroying the owner's real PostHog and CivitAI
keys — and `analytics_last_version = "9.9.9"` along with them. The test that
did it (`test_settings_merge.py`) sets `LTX_STATE_DIR` to a temp dir at import
time and is correct on its own: run alone it is airtight. But STATE_DIR is
resolved ONCE, when `mlx_ltx_panel` is first imported, so in a whole-suite run
any earlier test module that imported the panel had already fixed it to the
real `state/` — and every "temp dir" assignment after that was decoration.
The same trap explains `state/usage-log.jsonl.bak-fixtures` (an earlier
session's rescue) and the suite's order-dependent failures.

A conftest is imported before any test module, so this is the one place the
sandbox can be set up in time. Everything a test could write — state, outputs,
uploads — is pointed at a per-run temp tree unless the caller set it
deliberately. `test_state_sandbox.py` holds the line.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: One tree per pytest run; the OS reaps it.
SANDBOX = Path(tempfile.mkdtemp(prefix="phos-test-sandbox-"))

for _var, _sub in (("LTX_STATE_DIR", "state"),
                   ("LTX_OUTPUT_DIR", "mlx_outputs"),
                   ("LTX_UPLOADS_DIR", "panel_uploads")):
    if not (os.environ.get(_var) or "").strip():
        _dir = SANDBOX / _sub
        _dir.mkdir(parents=True, exist_ok=True)
        os.environ[_var] = str(_dir)

# A test run also has no business reporting to PostHog or phoning home for a
# version check; both were already set per-file, inconsistently.
os.environ.setdefault("PHOSPHENE_ANALYTICS_DISABLED", "1")
os.environ.setdefault("PHOSPHENE_DISABLE_VERSION_CHECK", "1")
