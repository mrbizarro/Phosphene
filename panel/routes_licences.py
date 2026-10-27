"""/licences family routes - per-model licence screens and stored acceptance.

GET  /licences          every model licence the panel asks about, with this
                        Mac's acceptance state and the screen's words
POST /licences/accept   key=<model> records an explicit "I accept" for the
                        current licence version (model_licences.accept)

The gates themselves live where the work starts (queue routes, the H3 add-on
and music install routes, the worker): they answer HTTP 451 with
code=licence_required, and webapp/js/licences.js turns that into the screen.
"""
from __future__ import annotations

import model_licences
from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/licences")
def get_licences(h, parsed) -> None:
    h._json(model_licences.describe_all(P.STATE_DIR))


@post("/licences/accept")
def post_licences_accept(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    key = (form.get("key", [""])[0] or "").strip()
    if key not in model_licences.LICENCES:
        h._json({"ok": False, "error": f"unknown licence {key!r}"}, 400)
        return
    # The checkbox is the act of accepting; the client sends it explicitly so
    # a stray POST without it records nothing.
    if (form.get("accept", [""])[0] or "").strip() not in ("1", "true", "yes", "on"):
        h._json({"ok": False, "error": "tick the box to accept the licence first"}, 400)
        return
    if form.get("version", [""])[0] != model_licences.LICENCES[key]["version"]:
        h._json({"ok": False, "error": "The licence changed. Close this screen and open it again to read the current licence."}, 409)
        return
    try:
        rec = model_licences.accept(P.STATE_DIR, key, P._read_local_version())
    except OSError:
        h._json({"ok": False, "error": "Could not save acceptance. Check that the app state folder is writable, then try again."}, 503)
        return
    P.push(f"[licence] {model_licences.LICENCES[key]['licence']} accepted "
           f"({rec['version']})")
    try:
        P.h3_status_invalidate()
    except Exception:  # noqa: BLE001 - only a memo
        pass
    h._json({"ok": True, "key": key, "record": rec,
             "licence": model_licences.describe(P.STATE_DIR, key)})
