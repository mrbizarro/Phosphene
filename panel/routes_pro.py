"""/pro family routes - whether Pro is active on this Mac, and the measured Free-vs-Pro comparison.

GET /pro/status    {active, plan, until, holder, source}; Free is the default and nothing here nags
GET /pro/compare   the bundled "same clip, Free vs Pro" comparisons (assets/pro/compare.json)

Pro is on only with a signed capability token for this Mac (pro_token.py, passed by the Mac app).
"""
from __future__ import annotations

import json
from pathlib import Path

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/pro/status")
def get_pro_status(h, parsed) -> None:
    doc = P.pro_status()
    # The LTX Pro speed-up beside Quality: on offer / ready / downloading, and why not (no "how").
    doc["ltx_speedup"] = P.ltx_speedup_status()
    # Pinokio only: whether this Studio can take a licence key itself (the Mac app has its own door).
    doc["licence_here"] = not P._dist.is_mac_app()
    if doc["licence_here"]:
        import pro_licence
        doc["own_licence"] = pro_licence.load() is not None
    h._json(doc)


def _whats_new_doc(force: bool) -> dict:
    import whats_new
    root = Path(P.ROOT)
    try:
        version = (root / "VERSION").read_text("utf-8").strip()
    except OSError:
        version = ""
    pro = bool(P.pro_status().get("active"))
    return whats_new.card(root, Path(P.STATE_DIR), version, pro, force=force,
                          pack_json=root / "pro_engine" / "whats_new_pro.json")


@get("/whats-new")
def get_whats_new(h, parsed) -> None:
    """The once-per-version "What's new" card; ?force=1 reopens this version's card (version pill)."""
    from urllib.parse import parse_qs
    force = (parse_qs(parsed.query).get("force") or [""])[0] in ("1", "true")
    h._json(_whats_new_doc(force))


@get("/whats-new/how")
def get_whats_new_how(h, parsed) -> None:
    """One feature's how-to clip (Free and Pro takes) for its "How it works" link; {"how": null} when none."""
    import whats_new
    from urllib.parse import parse_qs
    hid = (parse_qs(parsed.query).get("id") or [""])[0][:64]
    h._json({"how": whats_new.how(Path(P.ROOT), hid) if hid else None})


@post("/whats-new/seen")
def post_whats_new_seen(h, path, qs, ctype) -> None:
    import whats_new
    rb = h._read_form_body()
    if rb is None:
        return
    ver = (rb[1].get("version") or [""])[0]
    h._json({"ok": whats_new.mark_seen(Path(P.STATE_DIR), ver)})


def _licence_call(h, name: str, with_key: bool = False) -> None:
    if P._dist.is_mac_app():
        h._json({"ok": False, "message": "Use Phosphene \u2192 Pro in the Mac app to manage your licence."}, 404)
        return
    rb = h._read_form_body()
    if rb is None:
        return
    form = {k: (v[0] if v else "") for k, v in rb[1].items()}
    import pro_licence
    try:
        doc = getattr(pro_licence, name)(form.get("key", "")) if with_key else getattr(pro_licence, name)()
    except Exception as exc:  # noqa: BLE001
        doc = {"ok": False, "message": "The licence check failed: %s" % type(exc).__name__}
    doc["pro_status"] = P.pro_status()
    h._json(doc)


@post("/pro/activate")
def post_pro_activate(h, path, qs, ctype) -> None:
    _licence_call(h, "activate", with_key=True)


@post("/pro/refresh")
def post_pro_refresh(h, path, qs, ctype) -> None:
    _licence_call(h, "validate")


@post("/pro/deactivate")
def post_pro_deactivate(h, path, qs, ctype) -> None:
    _licence_call(h, "deactivate")


@get("/pro/licence")
def get_pro_licence(h, parsed) -> None:
    import pro_licence
    h._json(pro_licence.status())


@get("/pro/compare")
def get_pro_compare(h, parsed) -> None:
    try:
        data = json.loads((P.ROOT / "assets" / "pro" / "compare.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {"items": []}
    h._json(data)
