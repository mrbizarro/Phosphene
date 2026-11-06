"""/remote/* - the Studio's Remote Control page (left menu).

The page is public Studio UI. The working parts (pairing, encryption, relay, the Pro check)
are the private pro_remote module from the Pro pack; with it absent every route answers
"available: false" and the page says Remote Control comes with Phosphene Pro.

Loopback-only like every studio route (the Handler's host/origin checks run first). The
private module re-checks Pro on every call. Mutations are POST only.
"""
from __future__ import annotations

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time

_ACTIONS = ("enable", "name", "pair", "pair_cancel", "confirm", "revoke", "forget_all")


def _mod():
    return getattr(P, "pro_remote", None)


@get("/remote/state")
def get_remote_state(h, parsed) -> None:
    m = _mod()
    if m is None:
        h._json({"ok": True, "available": False, "pro": False, "requires_pro": True})
        return
    h._json(m.page_state())


def _do(action):
    def handler(h, path, qs, ctype) -> None:
        m = _mod()
        if m is None:
            h._json({"ok": False, "error": "Remote Control comes with Phosphene Pro.", "code": "unavailable"}, 404)
            return
        rb = h._read_form_body()
        if rb is None:
            return
        form = {k: (v[0] if v else "") for k, v in rb[1].items()}
        status, doc = m.admin(action, form)
        h._json(doc, status)
    return handler


for _a in _ACTIONS:
    post("/remote/" + _a)(_do(_a))
