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
        h._json(_unavailable_state())
        return
    h._json(m.page_state())


def _unavailable_state() -> dict:
    """No Remote module is loaded. Say WHY, with the real Pro status: a member sees the install
    state (checking, downloading, error with next step, restart needed), never a dead page."""
    try:
        pro = bool((P.pro_status() or {}).get("active"))
    except Exception:  # noqa: BLE001
        pro = False
    doc = {"ok": True, "available": False, "pro": pro, "requires_pro": not pro}
    if P._dist.is_mac_app():
        doc["parts"] = {"managed_by": "mac_app"}
        # Pro with no Remote module in a Mac-app studio = Pro was switched on after this engine
        # started: the running engine is the Free one. The page offers "Set up Remote", which asks
        # the app to start the Pro engine (no restart of Phosphene by hand).
        doc["needs_engine_start"] = bool(pro)
        return doc
    try:
        import pro_licence
        import pro_parts
        parts = pro_parts.status()
        lic = pro_licence.status()
    except Exception:  # noqa: BLE001
        return doc
    doc["parts"] = parts
    doc["licensed_here"] = bool(lic.get("licensed"))
    if pro and not lic.get("licensed"):
        doc["needs_licence"] = True
        doc["next"] = "Paste your licence key in Settings > Pro of this Studio (it does not use another seat). Remote Control's components are fetched with your own key."
    elif lic.get("storage_problem") or lic.get("note") and not pro:
        doc["next"] = lic.get("note")
    return doc


@post("/remote/parts_retry")
def post_parts_retry(h, path, qs, ctype) -> None:
    if _mod() is not None:
        h._json({"ok": True, "started": False, "message": "Remote Control is already installed."})
        return
    import pro_parts
    h._json(pro_parts.retry_now())


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
