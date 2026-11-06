"""Speech routes — Audio → Speech (text to speech) and its agent door.

  GET  /speech/voices        what the voice picker offers (presets, styles,
                             characters that carry a voice sample) + status
  GET  /speech/estimate      ?text=&speed= → audio length and wall time
  POST /speech/install       the install card (venv + packages + weights)
  POST /speech/install/stop
  POST /speech/generate      the agent API: JSON or form in, a WAV out
                             (wait:true blocks until it is written)

The UI's Generate goes through /queue/add (mode=speech) like every other
form; /speech/generate is the same job with an agent-shaped request and
response. Same module shape as the others: `h` is the handler, `P` the panel.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time

_WAIT_MAX_S = 10 * 60


@get("/speech/voices")
def get_speech_voices(h, parsed) -> None:
    payload = P.speech_engine.voices_payload(P.list_characters())
    payload["status"] = P.speech_status()
    payload["install"] = P.speech_install_status()
    h._json(payload); return


@get("/speech/estimate")
def get_speech_estimate(h, parsed) -> None:
    q = parse_qs(parsed.query)
    text = (q.get("text") or [""])[0]
    try:
        speed = float((q.get("speed") or ["1"])[0] or 1)
    except ValueError:
        speed = 1.0
    h._json(P.speech_estimate(text[:P.speech_engine.SPEECH_MAX_CHARS], max(0.5, min(2.0, speed)))); return


@post("/speech/install")
def post_speech_install(h, path, qs, ctype) -> None:
    # Apache-2.0 weights: no licence screen (model_licences.py is for models
    # that ask for an explicit "I accept").
    _rb = h._read_form_body()
    if _rb is None:
        return
    code, payload = P.speech_install_start()
    h._json(payload, code); return


@post("/speech/install/stop")
def post_speech_install_stop(h, path, qs, ctype) -> None:
    if not P.speech_install_stop():
        h._json({"error": "no Speech install is running"}, 404); return
    P.push("[speech-install] stop requested")
    h._json({"ok": True}); return


def _agent_form(spec: dict) -> dict:
    """The agent's JSON spec → the same form fields the panel posts."""
    voice = str(spec.get("voice") or "").strip()
    character = str(spec.get("character") or "").strip()
    clip = str(spec.get("clip") or spec.get("ref_audio") or "").strip()
    kind = "character" if character else "clip" if clip else "preset"
    consent = spec.get("voice_consent", spec.get("consent", False))
    form = {
        "mode": "speech",
        "speech_text": str(spec.get("text") or ""),
        "speech_voice_kind": kind,
        "speech_voice": voice or ("ryan" if kind == "preset" else ""),
        "speech_character": character,
        "speech_ref_audio": clip,
        "speech_ref_text": str(spec.get("clip_text") or spec.get("ref_text") or ""),
        "speech_style": str(spec.get("style") or "natural"),
        "speech_instruct": str(spec.get("instruct") or ""),
        "speech_speed": str(spec.get("speed") if spec.get("speed") is not None else "1.0"),
        "speech_language": str(spec.get("language") or "english"),
        "speech_seed": str(spec.get("seed") if spec.get("seed") is not None else "-1"),
        "speech_title": str(spec.get("title") or ""),
        "speech_voice_consent": "1" if consent is True or str(consent).lower() in ("1", "true", "yes", "on") else "",
    }
    return {k: [v] for k, v in form.items()}


def _find_job(jid: str):
    with P.LOCK:
        cur = P.STATE.get("current")
        if cur and cur.get("id") == jid:
            return cur
        for j in P.STATE.get("queue", []):
            if j.get("id") == jid:
                return j
        for j in P.STATE.get("history", []):
            if j.get("id") == jid:
                return j
    return None


@post("/speech/generate")
def post_speech_generate(h, path, qs, ctype) -> None:
    if ctype.startswith("application/json"):
        try:
            length = int(h.headers.get("Content-Length") or "0")
        except ValueError:
            h._json({"ok": False, "error": "invalid Content-Length"}, 400); return
        if length <= 0 or length > 256 * 1024:
            h._json({"ok": False, "error": "send a JSON body under 256 KB"}, 400); return
        try:
            spec = json.loads(h.rfile.read(length).decode() or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            h._json({"ok": False, "error": "invalid JSON body"}, 400); return
        if not isinstance(spec, dict):
            h._json({"ok": False, "error": "the body must be a JSON object"}, 400); return
    else:
        _rb = h._read_form_body()
        if _rb is None:
            return
        spec = {k: (v[0] if isinstance(v, list) and v else v) for k, v in _rb[1].items()}
    if not P.speech_status()["available"]:
        st = P.speech_status()
        h._json({"ok": False, "code": "not_installed",
                 "error": f"Speech is not installed. POST /speech/install (~{st['install_gb']} GB, resumable), "
                          "then poll GET /status → speech_install until state is done."}, 409); return
    try:
        job = P.make_job(_agent_form(spec))
    except P.CharacterRequestError as exc:
        h._json({"ok": False, "error": str(exc)}, 400); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    jid = job["id"]
    est = P.speech_estimate(job["params"]["speech_text"], job["params"]["speech_speed"])
    wait = spec.get("wait", True)
    if wait in (False, "false", "0", 0):
        h._json({"ok": True, "queued": True, "job_id": jid, "estimate": est,
                 "poll": "GET /status (history[] entry with this id; output_path when done)"}, 202); return
    deadline = P.time.time() + _WAIT_MAX_S
    while True:
        rec = _find_job(jid)
        if rec is not None and rec.get("status") in ("done", "failed", "cancelled", "stopped"):
            break
        if P.time.time() > deadline:
            h._json({"ok": False, "job_id": jid, "error": "Timed out after 10 minutes waiting in the queue; "
                                                          "the job may still finish (see GET /status)."}, 504); return
        P.time.sleep(0.5)
    rec = _find_job(jid) or {}
    if rec.get("status") != "done":
        stopped = rec.get("status") in ("cancelled", "stopped")
        h._json({"ok": False, "job_id": jid, "status": rec.get("status"),
                 "error": ("The job was stopped before it finished." if stopped and not rec.get("error")
                           else (rec.get("error") or "speech failed").strip())}, 409 if stopped else 500); return
    out = rec.get("output_path") or ""
    meta = {}
    try:
        meta = json.loads(P.Path(out + ".json").read_text())
    except (OSError, ValueError):
        pass
    h._json({"ok": True, "job_id": jid, "path": out,
             "url": "/file?path=" + P.quote(out),
             "audio_seconds": meta.get("audio_seconds"), "voice": meta.get("voice"),
             "seed": meta.get("seed"), "elapsed_sec": meta.get("elapsed_sec"),
             "next": {"lipsync": "POST /queue/add mode=a2v audio=<path> image=<face picture> prompt=<scene> "
                                 "(docs/API.md, Lip-sync)"}}); return
