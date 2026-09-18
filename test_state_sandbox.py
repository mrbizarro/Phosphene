"""The suite must run in a sandbox, not in the owner's install.

See `conftest.py`. This is the gate: if the panel's state, outputs or uploads
ever resolve back to the checkout, a test can overwrite real settings, real
renders and real uploads — which is exactly what happened on 2026-09-18.
"""
from __future__ import annotations

import os
from pathlib import Path

import conftest
import mlx_ltx_panel as P


def test_state_outputs_and_uploads_live_in_the_sandbox():
    for name, path in (("STATE_DIR", P.STATE_DIR), ("OUTPUT", P.OUTPUT),
                       ("UPLOADS", P.UPLOADS)):
        assert str(path).startswith(str(conftest.SANDBOX)), \
            f"{name} is {path} — a test would write the real install"


def test_the_settings_file_is_not_the_installs_own():
    real = Path(P.__file__).resolve().parent / "state" / "panel_settings.json"
    assert P.SETTINGS_FILE.resolve() != real.resolve()


def test_a_write_to_a_real_installs_settings_is_refused():
    """The belt to the sandbox's braces: even with a bad STATE_DIR, a test
    process may not persist settings into somebody's install."""
    assert os.environ.get("PYTEST_CURRENT_TEST")
    real = Path(P.__file__).resolve().parent / "state" / "panel_settings.json"
    try:
        P._save_settings({"hijacked": True}, _target=real)
    except RuntimeError as exc:
        assert "test" in str(exc).lower()
    else:                                        # pragma: no cover
        raise AssertionError("a test just wrote the real panel_settings.json")
