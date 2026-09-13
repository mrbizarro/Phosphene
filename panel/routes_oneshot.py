"""One Shot — its own door (2026-09-08).

A One Shot is one continuous shot of 30 s – 2 min that never cuts, rendered
as parts that continue from each other's last frame (`run_take_job_inner`).
It used to be a mode chip on the video form; it is a workflow tab now, and
these routes are its API — the same document the tab posts:

  POST /oneshot            {prompt, seconds, engine, quality, beats, camera,
                            image, character_id, handoff, light_lock, retake,
                            seed, label, ...} → {ok, id, plan}
  GET  /oneshot/options    what this Mac can do: engines, qualities, characters
  GET  /oneshot/estimate   ?engine&quality&seconds → parts, minutes, eta
  POST /oneshot/plan       {prompt, seconds, engine} → {ok, beats}  (20–40 s)
  GET  /oneshot/status     the running take, its parts, verdicts and log

`P.oneshot_form()` maps the document onto the make_job form, so the tab, the
API and /queue/add render exactly the same job. `P` is assigned by
mlx_ltx_panel at import-wiring time — see panel/__init__.py.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time

MAX_ONESHOT_JSON = 256 * 1024


def _read_document(h, ctype) -> dict | None:
    """The request as one flat dict: a JSON object, or a form (each field's
    first value). Answers the error itself and returns None when it did."""
    if (ctype or "").startswith("application/json"):
        try:
            length = int(h.headers.get("Content-Length") or "0")
        except ValueError:
            h._json({"ok": False, "error": "invalid Content-Length"}, 400); return None
        if length <= 0:
            h._json({"ok": False, "error": "Content-Length required for JSON body"}, 411); return None
        if length > MAX_ONESHOT_JSON:
            h._json({"ok": False, "error": f"body too large (max {MAX_ONESHOT_JSON} bytes)"}, 413); return None
        try:
            doc = P.json.loads(h.rfile.read(length).decode() or "{}")
        except (P.json.JSONDecodeError, UnicodeDecodeError):
            h._json({"ok": False, "error": "invalid JSON body"}, 400); return None
        if not isinstance(doc, dict):
            h._json({"ok": False, "error": "the body must be a JSON object"}, 400); return None
        return doc
    rb = h._read_form_body()
    if rb is None:
        return None
    _body, form = rb
    return {k: (v[0] if isinstance(v, list) and v else v) for k, v in form.items()}


@post("/oneshot")
def post_oneshot(h, path, qs, ctype) -> None:
    doc = _read_document(h, ctype)
    if doc is None:
        return
    form, err = P.oneshot_form(doc)
    if err:
        h._json({"ok": False, "error": err}, 400); return
    err = P._validate_character_quality(form)
    if err:
        h._json({"ok": False, "error": err}, 400); return
    try:
        job = P.make_job(form)
    except P.CharacterRequestError as exc:
        h._json({"ok": False, "error": str(exc)}, 400); return
    take = (job.get("params") or {}).get("take") or {}
    if not take:
        # make_job refused the take (it logs why) — say so instead of queueing a clip
        h._json({"ok": False, "error": "the request did not become a One Shot — check seconds and engine"}, 400); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    engine = "h3" if take.get("engine") == "h3" else "ltx"
    quality = (form.get("h3_quality") or form.get("quality") or form.get("quality_choice") or [""])[0]
    minutes = P.take_estimate_minutes(engine, quality, take.get("seconds"))
    h._json({"ok": True, "id": job["id"],
             "plan": {"seconds": take.get("seconds"), "parts": len(take.get("parts") or []),
                      "beats": take.get("beats"), "engine": engine, "handoff": take.get("handoff"),
                      "minutes": minutes, "eta": (P._fmt_eta(minutes) if minutes else None)}})


@get("/oneshot/options")
def get_oneshot_options(h, parsed) -> None:
    h._json(P.oneshot_options())


@get("/oneshot/estimate")
def get_oneshot_estimate(h, parsed) -> None:
    q = parse_qs(parsed.query)
    engine = (q.get("engine", ["ltx"])[0] or "ltx").strip().lower()
    quality = (q.get("quality", [""])[0] or "").strip().lower()
    seconds = (q.get("seconds", ["0"])[0] or "0").strip()
    plan = P.take_plan(seconds, engine)
    if not plan:
        h._json({"ok": False, "error": f"seconds must be one of {list(P.TAKE_SECONDS)}",
                 "choices": list(P.TAKE_SECONDS)}, 400); return
    minutes = P.take_estimate_minutes(engine, quality, seconds)
    h._json({"ok": True, "seconds": plan["seconds"], "beats": plan["beats"],
             "parts": len(plan["parts"]), "beats_per_part": plan["beats_per_part"],
             "part_seconds": (15 if plan["engine"] == "h3" else P.TAKE_LTX_PART_FRAMES // 24),
             "engine": plan["engine"], "minutes": minutes,
             "eta": (P._fmt_eta(minutes) if minutes else None)})


@post("/oneshot/plan")
def post_oneshot_plan(h, path, qs, ctype) -> None:
    doc = _read_document(h, ctype)
    if doc is None:
        return
    res = P.oneshot_plan_beats(doc.get("prompt"), doc.get("seconds") or 60, doc.get("engine") or "ltx")
    h._json(res, 200 if res.get("ok") else 400)


@get("/oneshot/status")
def get_oneshot_status(h, parsed) -> None:
    h._json(P.oneshot_status())
