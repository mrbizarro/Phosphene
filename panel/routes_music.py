"""Music Studio routes — the song as a first-class thing.

Every song YuE2 writes keeps its artifacts (plan, tokens, latents, noise),
which is what makes a song something you can come back to rather than a file
you got once. These routes are the verbs on it:

  /music/variation   queue a new take, a re-recording, or a restyle of a song
  /music/transcribe  listen to a recording and write out its score, no song
  /music/score       the sheet music (ABC) and the facts behind one output
  /music/score/render render an EDITED score as a new song
  /music/lyrics      Gemma writes lyrics from a concept, in YuE2's own format
  /music/lyrics/section  Gemma rewrites ONE section of the lyrics in place
  /music/simple      one description in, a whole brief out (Simple mode)

Same shape as the other route modules: `h` is the handler, `P` the panel.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs

from panel.routes import get, post

# `storyboard_edit` (the beat map) pulls in numpy at import time. The panel
# boots without numpy on purpose — a machine that never plans a music video
# must never fail to serve the UI because of one — so both imports are lazy,
# inside the routes that need them (Codex review, 2026-09-20).
music_video = None
storyboard_edit = None


def _planner():
    """The planner and the beat map, imported on first use, with the beat
    map's ffmpeg pointed at the one the panel resolved — storyboard_edit has a
    resolver of its own that does not know Pinokio's tool folders."""
    global music_video, storyboard_edit
    if music_video is None:
        import music_video as _mv
        import storyboard_edit as _se
        _se.FFMPEG = P.FFMPEG
        if hasattr(_se, "FFPROBE"):
            _se.FFPROBE = P.FFPROBE
        music_video, storyboard_edit = _mv, _se
    return music_video, storyboard_edit


def _helper_run(spec: dict, timeout: float):
    """The warm helper is the LTX process; the queue kills it when a music or
    H3 job starts, and a lyrics request that lands mid-render would spawn a
    second MLX model beside YuE2. Take the GPU lock like every other GPU
    user — briefly, and say so instead of blocking the browser for a whole
    render when it is taken."""
    if not P._GPU_LOCK.acquire(timeout=3.0):
        raise RuntimeError("A render is using the GPU right now — try again when it finishes.")
    P.gpu_gate_mark("lyrics")
    try:
        return P.HELPER.run(spec, timeout=timeout)
    finally:
        P.gpu_gate_unmark()
        P._GPU_LOCK.release()

P = None  # the running mlx_ltx_panel module; assigned at wiring time


def _output_roots() -> list:
    """OUTPUT plus every saved files folder (P.output_roots); a stub panel
    without it (unit tests) gets OUTPUT alone, the behaviour before 5.0.60."""
    f = getattr(P, "output_roots", None)
    return list(f()) if callable(f) else [P.OUTPUT.resolve()]

VARIATIONS = ("take", "sound", "restyle")


def _one(form, key, default=""):
    v = form.get(key, [default])
    v = v[0] if isinstance(v, list) else v
    return (v if v is not None else default)


def _sidecar(path: str) -> dict | None:
    try:
        return json.loads(Path(str(path) + ".json").read_text())
    except (OSError, ValueError):
        return None


def _song_output(path: str) -> Path | None:
    """The output the request names, only if it is one of ours.

    A form field naming a file is a form field naming a file: it is accepted
    when it resolves inside the outputs folder and carries a music sidecar,
    and refused otherwise — never used to read anything else."""
    try:
        p = Path(path).resolve()
    except (OSError, ValueError):
        return None
    if not any(p.is_relative_to(r) for r in _output_roots()) or not p.is_file():
        return None
    meta = _sidecar(str(p))
    if not isinstance(meta, dict) or meta.get("engine") != "music":
        return None
    return p


def _parent_picks(params: dict) -> list[dict]:
    """The parent's adapters as `[{id, strength}]`, for a form.

    Back through the form as ids and strengths, never as resolved paths:
    `music_params` re-resolves every id inside the LoRA folder, so a sidecar
    can name an adapter and still cannot name a file outside it. JSON rather
    than the browser's `id:strength,…` because an id is a filename and a
    filename may contain a comma."""
    return [{"id": str(pick["id"]).strip(), "strength": pick.get("strength", 1.0)}
            for pick in (params.get("music_loras") or [])
            if isinstance(pick, dict) and str(pick.get("id") or "").strip()]


def _queue(form: dict) -> dict:
    job = P.make_job(form)
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    return job


@post("/music/variation")
def post_music_variation(h, path, qs, ctype) -> None:
    # 4.19.8: YuE2 is CC BY-NC - its licence screen first (model_licences.py).
    _lic = P.licence_refusal_for("yue2")
    if _lic:
        h._json(_lic, 451); return
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    kind = _one(form, "kind").strip()
    if kind not in VARIATIONS:
        h._json({"error": f"kind must be one of {', '.join(VARIATIONS)}"}, 400); return
    parent = _song_output(_one(form, "path"))
    if parent is None:
        h._json({"error": "that is not one of this panel's songs"}, 404); return
    meta = _sidecar(str(parent)) or {}
    if kind in ("take", "sound") and P.music_artifacts_for(parent) is None:
        h._json({"error": "That song kept no artifacts, so it cannot be varied. "
                          "Songs made before v4.16 need composing again first."}, 409); return
    if kind == "restyle" and not meta.get("score_abc"):
        h._json({"error": "That song has no score to restyle (it was written with No score)."}, 409); return
    # A take or a re-recording keeps the parent's words and style — the runner
    # reads them off the saved plan and ignores the form — so the label, the
    # ETA and the gallery all still say what the song is. A restyle takes the
    # NEW style/lyrics from the form.
    style = _one(form, "style").strip() if kind == "restyle" else (meta.get("style") or "")
    lyrics = _one(form, "lyrics") if kind == "restyle" else (meta.get("lyrics") or "")
    if kind == "restyle" and not style and not lyrics.strip():
        h._json({"error": "A restyle needs a new style, new lyrics, or both."}, 400); return
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    quality = _one(form, "quality") or params.get("music_quality") or "final"
    seconds = _one(form, "max_seconds") or params.get("music_max_seconds") or 240
    job_form = {
        "mode": ["music"], "engine": ["music"],
        "music_style": [style], "music_lyrics": [lyrics],
        "music_mode": [str(meta.get("mode") or "full")],
        "music_instrumental": ["on" if meta.get("instrumental") else "off"],
        "music_quality": [str(quality)], "music_max_seconds": [str(seconds)],
        "music_seed": [_one(form, "seed", "-1") or "-1"],
        "music_title": [_one(form, "title") or (meta.get("title") or "")],
        "music_parent": [str(parent)], "music_variation": [kind],
        "music_precision": [str(params.get("music_precision") or "8bit")],
        # THE ADAPTERS ARE PART OF THE SONG. A take is the same singer singing
        # it again and a re-roll is the same performance recorded again, so a
        # variation that dropped the parent's picks handed back a voice the
        # user never chose, and a sound re-roll lost the acoustic adapter that
        # made the recording sound the way it does (Codex review, 2026-09-22).
        # The mode travels with them: `separate` also swaps the decoder's I/O
        # projections, so a variation without it is a different recipe.
        "music_lora_mode": [str(params.get("music_lora_mode") or "joint")],
    }
    picks = _parent_picks(params)
    if picks:
        job_form["music_loras"] = [json.dumps(picks)]
    try:
        job = _queue(job_form)
    except P.MusicRequestError as exc:
        # The parent's adapter is gone from the LoRA folder: say so rather
        # than make the variation with a different singer.
        h._json({"error": str(exc)}, 409); return
    P.push(f"[music] {kind} of {parent.name} queued as {job['id']}")
    h._json({"ok": True, "id": job["id"], "kind": kind, "parent": str(parent)})


@post("/music/transcribe")
def post_music_transcribe(h, path, qs, ctype) -> None:
    # 4.19.8: YuE2 is CC BY-NC - its licence screen first (model_licences.py).
    _lic = P.licence_refusal_for("yue2")
    if _lic:
        h._json(_lic, 451); return
    """Get the score of a recording. A queue job like any other so it never
    races a render for the GPU; the result is fetched by job id."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    source = P.normalize_pasted_path(_one(form, "path"))
    if not source or not Path(source).is_file():
        h._json({"error": "pick a recording first"}, 400); return
    if not P.music_cover_status()["ready"]:
        h._json({"error": "the transcription models are not installed yet"}, 409); return
    task = _one(form, "task") or "melody-full"
    job = _queue({
        "mode": ["music"], "engine": ["music"],
        "music_style": ["score"], "music_lyrics": [""],
        "music_source_audio": [source], "music_cover_task": [task],
        "music_variation": ["score"], "music_title": [_one(form, "title") or Path(source).stem],
        "music_max_seconds": ["360"], "music_quality": ["draft"],
    })
    h._json({"ok": True, "id": job["id"]})


@get("/music/score")
def get_music_score(h, parsed) -> None:
    """The sheet music and the song facts behind an output — or behind a
    finished transcription job, by id. (GET handlers take the parsed URL;
    POST handlers take the body — see panel/routes.py.)"""
    q = parse_qs(parsed.query or "")
    job_id = (q.get("job", [""])[0] or "").strip()
    out = (q.get("path", [""])[0] or "").strip()
    meta = None
    if job_id:
        with P.LOCK:
            hist = [j for j in (P.STATE.get("history") or []) if j.get("id") == job_id]
        if not hist:
            h._json({"error": "no such job"}, 404); return
        j = hist[0]
        if j.get("status") != "done" or not j.get("output_path"):
            h._json({"status": j.get("status"), "error": j.get("error")}); return
        meta = _sidecar(j["output_path"])
        out = j["output_path"]
    else:
        p = _song_output(out)
        if p is None:
            h._json({"error": "that is not one of this panel's songs"}, 404); return
        meta = _sidecar(str(p))
    if not isinstance(meta, dict):
        h._json({"error": "no sidecar"}, 404); return
    lineage = meta.get("lineage") if isinstance(meta.get("lineage"), dict) else {}
    h._json({
        "path": out,
        "title": meta.get("title") or "",
        "style": meta.get("style") or "",
        "lyrics": meta.get("lyrics") or "",
        "mode": meta.get("mode"),
        "seed": meta.get("seed"),
        "abc": meta.get("score_abc") or "",
        "cover": meta.get("cover"),
        "variation": (meta.get("variation") or {}).get("kind") if isinstance(meta.get("variation"), dict) else lineage.get("variation"),
        "parent": lineage.get("parent"),
        "has_artifacts": P.music_artifacts_for(out) is not None,
        "audio_seconds": meta.get("audio_seconds"),
        "ended_naturally": meta.get("ended_naturally"),
        "lora": meta.get("lora"),
        "instrumental_recipe": meta.get("instrumental_recipe"),
    })


@post("/music/score/render")
def post_music_score_render(h, path, qs, ctype) -> None:
    # 4.19.8: YuE2 is CC BY-NC - its licence screen first (model_licences.py).
    _lic = P.licence_refusal_for("yue2")
    if _lic:
        h._json(_lic, 451); return
    """Render an EDITED score. The text is written into the studio's own
    tree and the job points there; the worker refuses any other path."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    abc = _one(form, "abc")
    if not abc.strip() or "K:" not in abc:
        h._json({"error": "that does not look like an ABC score (no K: key line)"}, 400); return
    parent = _song_output(_one(form, "path"))
    meta = (_sidecar(str(parent)) or {}) if parent else {}
    style = _one(form, "style").strip() or (meta.get("style") or "")
    lyrics = _one(form, "lyrics")
    if not lyrics.strip():
        lyrics = meta.get("lyrics") or ""
    edits = P.MUSIC_ARTIFACTS / "edits"
    edits.mkdir(parents=True, exist_ok=True)
    # ONE FILE PER REQUEST (M6-06). The name was the parent's stem and the
    # second, and the write overwrote: two edits of one song queued in the
    # same second (two tabs, or a busy queue) both pointed at one file, and
    # the first job sang the second one's notes. Exclusive creation with a
    # random tail cannot hand two jobs the same path.
    stem = (parent.stem if parent else "score") + time.strftime("_%Y%m%d_%H%M%S")
    while True:
        abc_path = edits / f"{stem}_{uuid.uuid4().hex[:8]}.abc"
        try:
            with open(abc_path, "x", encoding="utf-8") as fh:
                fh.write(abc)
            break
        except FileExistsError:
            continue
    mode = str(meta.get("mode") or "full")
    params = meta.get("params") if isinstance(meta.get("params"), dict) else {}
    job_form = {
        "mode": ["music"], "engine": ["music"],
        "music_style": [style], "music_lyrics": [lyrics],
        "music_mode": [mode if mode in ("full", "melody") else "full"],
        "music_quality": [_one(form, "quality") or str(params.get("music_quality") or "final")],
        # THE SLIDER ON SCREEN, THEN THE PARENT'S (M6-01). This read only the
        # parent's saved limit, so a song made at 3:00 re-rendered at 3:00
        # whatever Max length said.
        "music_max_seconds": [_one(form, "max_seconds")
                              or str(params.get("music_max_seconds") or 240)],
        "music_seed": [_one(form, "seed", "-1") or "-1"],
        "music_title": [_one(form, "title") or (meta.get("title") or "")],
        "music_abc_path": [str(abc_path)],
        "music_parent": [str(parent) if parent else ""],
        # THE REST OF THE SONG'S RECIPE (M6-03). An edited score is the same
        # song with other notes: it keeps its singer (the adapters, their
        # strengths and mode), Instrumental, the guidance and the precision.
        # Without them `music_params` filled in no adapters, vocals on, joint,
        # the default guidance and 8-bit — a different singer and sound
        # nobody chose, from a note change.
        "music_instrumental": ["on" if meta.get("instrumental") else "off"],
        "music_lora_mode": [str(params.get("music_lora_mode") or "joint")],
        "music_precision": [str(params.get("music_precision") or "8bit")],
    }
    if params.get("music_cfg_scale") is not None:
        job_form["music_cfg_scale"] = [str(params["music_cfg_scale"])]
    picks = _parent_picks(params)
    if picks:
        job_form["music_loras"] = [json.dumps(picks)]
    try:
        job = _queue(job_form)
    except P.MusicRequestError as exc:
        h._json({"error": str(exc)}, 409); return
    h._json({"ok": True, "id": job["id"], "abc_path": str(abc_path)})


@post("/music/lyrics")
def post_music_lyrics(h, path, qs, ctype) -> None:
    """Gemma writes lyrics from a concept, in the format YuE2 reads."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    concept = _one(form, "concept").strip()
    if not concept:
        h._json({"error": "say what the song is about"}, 400); return
    try:
        seconds = int(float(_one(form, "seconds", "150") or 150))
    except ValueError:
        seconds = 150
    P.push(f"[lyrics] {concept[:80]}")
    try:
        result = _helper_run({
            "action": "write_lyrics", "id": f"lyr-{int(time.time() * 1000)}",
            "params": {"concept": concept, "style": _one(form, "style").strip(),
                       "seconds": seconds, "language": _one(form, "language").strip(),
                       "seed": int(_one(form, "seed", "10") or 10)},
        }, timeout=P.PROMPT_ENHANCE_TIMEOUT)
    except Exception as exc:                                    # noqa: BLE001
        P.push(f"[lyrics] failed: {exc}")
        h._json({"error": str(exc)}, 500); return
    lyrics = result.get("lyrics") if isinstance(result, dict) else None
    if not isinstance(lyrics, str) or not lyrics.strip():
        h._json({"error": (result or {}).get("error") or "Gemma returned nothing usable"}, 500); return
    h._json({"ok": True, "lyrics": lyrics, "elapsed_sec": result.get("elapsed_sec")})


@post("/music/lyrics/section")
def post_music_lyrics_section(h, path, qs, ctype) -> None:
    """Gemma rewrites ONE section of the lyrics.

    The same action and the same resident model as /music/lyrics — what
    differs is that the editor sends the label and the lines it already has,
    and gets back the LINES alone. The tag never travels: the editor owns it,
    and a tag in the answer would land doubled in the box it came from."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    section = _one(form, "section").strip().strip("[]") or "Verse"
    lines = _one(form, "lines")
    concept = _one(form, "concept").strip()
    # A rewrite can stand on the lines alone; with neither there is nothing
    # to go on, and that is worth saying before the model loads.
    if not concept and not lines.strip():
        h._json({"error": "say what the song is about, or write a line in this "
                          "section first"}, 400); return
    try:
        seconds = int(float(_one(form, "seconds", "150") or 150))
    except ValueError:
        seconds = 150
    P.push(f"[lyrics] rewriting [{section}]")
    try:
        result = _helper_run({
            "action": "write_lyrics", "id": f"lyrsec-{int(time.time() * 1000)}",
            "params": {"concept": concept, "style": _one(form, "style").strip(),
                       "seconds": seconds, "language": _one(form, "language").strip(),
                       "section": section, "lines": lines,
                       "seed": int(_one(form, "seed", "10") or 10)},
        }, timeout=P.PROMPT_ENHANCE_TIMEOUT)
    except Exception as exc:                                    # noqa: BLE001
        P.push(f"[lyrics] section failed: {exc}")
        h._json({"error": str(exc)}, 500); return
    out = result.get("lyrics") if isinstance(result, dict) else None
    if not isinstance(out, str) or not out.strip():
        h._json({"error": (result or {}).get("error") or "Gemma returned nothing usable"}, 500); return
    h._json({"ok": True, "section": section, "lines": out,
             "elapsed_sec": result.get("elapsed_sec")})


@post("/music/simple")
def post_music_simple(h, path, qs, ctype) -> None:
    """Simple mode: one description, and Gemma writes the brief.

    It renders NOTHING. What comes back fills the Custom fields — title,
    style, the lyrics editor — and the person reads it before pressing
    Compose themselves, because a song is minutes of this Mac's GPU and a
    brief nobody read is the expensive way to find that out."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    description = _one(form, "description").strip()
    if not description:
        h._json({"error": "say what the song should be"}, 400); return
    instrumental = _one(form, "instrumental").strip().lower() in ("on", "1", "true", "yes")
    try:
        seconds = int(float(_one(form, "seconds", "150") or 150))
    except ValueError:
        seconds = 150
    P.push(f"[song] {description[:80]}")
    try:
        result = _helper_run({
            "action": "write_song", "id": f"song-{int(time.time() * 1000)}",
            "params": {"description": description, "instrumental": instrumental,
                       "seconds": seconds, "seed": int(_one(form, "seed", "10") or 10)},
        }, timeout=P.PROMPT_ENHANCE_TIMEOUT)
    except Exception as exc:                                    # noqa: BLE001
        P.push(f"[song] failed: {exc}")
        h._json({"error": str(exc)}, 500); return
    style = (result or {}).get("style") if isinstance(result, dict) else None
    if not isinstance(style, str) or not style.strip():
        h._json({"error": (result or {}).get("error") or "Gemma returned nothing usable"}, 500); return
    # Instrumental is the caller's decision, not the model's: it is echoed back
    # so the form and the brief cannot disagree about whether anyone sings.
    h._json({"ok": True, "title": result.get("title") or "", "style": style,
             "lyrics": "" if instrumental else (result.get("lyrics") or ""),
             "instrumental": instrumental, "elapsed_sec": result.get("elapsed_sec")})


# ===========================================================================
# MUSIC VIDEO — the song and the pictures become a film
# ===========================================================================
# Two verbs. `/music/video/plan` turns a song plus a set of tagged pictures
# into an ordinary storyboard board, which is where it stops: the shot list is
# then rendered, re-rolled and edited by the machinery every other board uses,
# because a music video that could only be shot one way would be a dead end
# the first time somebody wanted shot 7 again. `/music/video/film` is the
# assembly, and the only thing it adds to Export is that the SONG is the bed.
#
# `music_video.py` holds every decision either route makes and imports nothing
# from here — see its docstring. These two functions are the door: they check
# that the paths came from this panel, hand the plan to the board writer the
# storyboard already has, and answer in JSON.

ROLES = ("singer", "instrument", "room")   # music_video.ROLES, stated here so the import can stay lazy
USES = ("open", "close", "any")            # music_video.USES, same reason
MAX_WEIGHT = 20                            # music_video.MAX_WEIGHT, same reason


def _where_to_put_files() -> str:
    """The second half of every containment refusal.

    The rule itself is not negotiable — a form field naming a file is a form
    field naming a file — but "that is not a file this panel has" is a dead
    end unless it also says where a file BECOMES one. Both answers, because
    the person is sometimes at a terminal and sometimes at the browser."""
    return (f"Pictures and songs have to live in this panel's uploads folder "
            f"({P.UPLOADS}) or its outputs folder ({P.OUTPUT}) — copy the file "
            f"there, drop it on the pane, or send it to POST /upload "
            f"(multipart, field 'image' or 'audio') and use the path that "
            f"comes back.")


def _panel_file(path: str) -> Path | None:
    """A file this panel made or was given, or None.

    THE CONTAINMENT RULE, the same one `_song_output` applies to songs and for
    the same reason: a form field naming a file is a form field naming a file.
    It resolves inside the outputs folder or the uploads folder, or it is not
    used. Wider than `_song_output` on purpose — an uploaded song and a
    dropped photograph carry no sidecar and never will.
    """
    raw = P.normalize_pasted_path(str(path or "").strip())
    if not raw:
        return None
    try:
        p = Path(raw).resolve()
    except (OSError, ValueError):
        return None
    if not p.is_file():
        return None
    for root in (*_output_roots(), P.UPLOADS):
        try:
            if p.is_relative_to(Path(root).resolve()):
                return p
        except (OSError, ValueError):
            continue
    return None


def _music_video_images(raw: str) -> tuple[list, str]:
    """The cast, checked -> (images, error). Every path, every role, every pin."""
    try:
        rows = json.loads(raw or "[]")
    except ValueError as exc:
        return [], f"the pictures are not readable JSON: {exc}"
    if not isinstance(rows, list) or not rows:
        return [], "drop at least one picture first"
    out = []
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return [], f"picture {i} is not an object"
        p = _panel_file(row.get("path"))
        if p is None:
            return [], (f"picture {i} is not a file this panel has "
                        f"({row.get('path')!r}). {_where_to_put_files()}")
        role = str(row.get("role") or "").strip().lower()
        if role not in ROLES:
            return [], (f"picture {i}: role {row.get('role')!r} must be one "
                        f"of {', '.join(ROLES)}")
        use = str(row.get("use") or "any").strip().lower()
        if use not in USES:
            return [], (f"picture {i}: use {row.get('use')!r} must be one of "
                        f"{', '.join(USES)}")
        raw_weight = row.get("weight")
        try:
            weight = int(float(raw_weight)) if raw_weight not in (None, "") else 1
        except (TypeError, ValueError):
            return [], (f"picture {i}: weight {raw_weight!r} must be a whole "
                        f"number between 1 and {MAX_WEIGHT}")
        if not (1 <= weight <= MAX_WEIGHT):
            return [], (f"picture {i}: weight {weight} is outside 1–"
                        f"{MAX_WEIGHT}")
        # HOW MUCH OF THE PICTURE THE FACE FILLS, 0..1, optional. It decides
        # which Singer leads the close-up rotation and nothing else, so a
        # value that is not a number is a picture with no measurement rather
        # than a refused film - `music_video._face_frac` is the one parser.
        face_frac = row.get("face_frac")
        if face_frac not in (None, ""):
            try:
                face_frac = float(face_frac)
            except (TypeError, ValueError):
                return [], (f"picture {i}: face_frac {row.get('face_frac')!r} "
                            f"must be a number between 0 and 1")
            if not (0 < face_frac <= 1):
                return [], (f"picture {i}: face_frac {face_frac} is outside "
                            f"0-1 (it is the share of the frame the face fills)")
        out.append({"path": str(p), "role": role,
                    "prompt": str(row.get("prompt") or "").strip(),
                    "use": use, "weight": weight,
                    "face_frac": face_frac if face_frac not in (None, "") else None})
    return out, ""


def _music_video_shot_prompts(raw: str) -> tuple[dict, str]:
    """`shots` -> ({shot number: line}, error).

    Two spellings, because both are natural: a list of `{"n": 24, "prompt":
    "…"}` (what a caller building from the plan's own shot rows writes) and a
    plain `{"24": "…"}` map (what a person editing JSON by hand writes).
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, ""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"the shot prompts are not readable JSON: {exc}"
    if isinstance(data, dict):
        rows = [{"n": k, "prompt": v} for k, v in data.items()]
    elif isinstance(data, list):
        rows = data
    else:
        return {}, "the shot prompts must be a list or an object"
    out: dict[int, str] = {}
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return {}, f"shot prompt {i} is not an object"
        try:
            n = int(row.get("n"))
        except (TypeError, ValueError):
            return {}, (f"shot prompt {i}: {row.get('n')!r} is not a shot "
                        f"number")
        if n < 1:
            return {}, f"shot prompt {i}: shot numbers start at 1"
        line = str(row.get("prompt") or "").strip()
        if not line:
            # A row may pin the PICTURE instead of the line (see
            # `_music_video_shot_images`), so "no prompt" is only empty when
            # the row says nothing at all.
            if str(row.get("image") or "").strip():
                continue
            return {}, f"shot {n}: the prompt is empty"
        out[n] = line
    return out, ""


def _music_video_shot_images(raw) -> tuple[dict, str]:
    """`shots` -> ({shot number: path}, error).

    The per-shot twin of `_music_video_shot_prompts`, reading the same blob:
    a row may carry a line, a picture, or both. Same containment rule as every
    other path in this file.
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, ""
    try:
        data = json.loads(raw)
    except ValueError as exc:
        return {}, f"the shot prompts are not readable JSON: {exc}"
    rows = data if isinstance(data, list) else []
    out: dict[int, str] = {}
    for i, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            return {}, f"shot prompt {i} is not an object"
        path = str(row.get("image") or "").strip()
        if not path:
            continue
        try:
            n = int(row.get("n"))
        except (TypeError, ValueError):
            return {}, (f"shot prompt {i}: {row.get('n')!r} is not a shot "
                        f"number")
        if n < 1:
            return {}, f"shot prompt {i}: shot numbers start at 1"
        pinned = _panel_file(path)
        if pinned is None:
            return {}, (f"shot {n}: the picture {path!r} is not a file this "
                        f"panel has. {_where_to_put_files()}")
        out[n] = str(pinned)
    return out, ""


def _shot_rows(shots: list) -> list:
    """The shot list as the browser wants it — flat, no board nesting."""
    rows = []
    for s in shots:
        block = s.get("music_video") or {}
        rows.append({
            "n": s.get("n"), "title": s.get("title"),
            "mode": block.get("panel_mode") or s.get("mode"),
            "kind": block.get("kind"), "role": block.get("role"),
            "image": block.get("image"), "frames": block.get("frames"),
            "duration_s": s.get("duration_s"),
            "film_start": block.get("film_start"),
            "audio_start_time": s.get("audio_start_time"),
            "section": block.get("section"),
            "prompt": s.get("prompt"),
            # So a caller re-planning with `shots` can see which lines it
            # already owns and which are still the picture's default.
            "prompt_override": bool(block.get("prompt_override")),
        })
    return rows




def _board_title_from_song(raw) -> str:
    """A song's name as a BOARD title (4.17): never its file extension
    ("song40.wav"), the upload's millisecond prefix ("1790123456789_take 3")
    or the composer's "music_YYYYMMDD_HHMMSS_" stamp — the client strips the
    same three (music.js _songNameFromFile), this is the authoritative copy
    for any caller that posts a raw filename."""
    import re as _re                                              # noqa: PLC0415
    t = str(raw or "").strip()
    t = t.rsplit("/", 1)[-1]
    t = _re.sub(r"\.(wav|mp3|m4a|flac|aac|ogg|opus|aiff?)$", "", t, flags=_re.I)
    t = _re.sub(r"^\d{10,}_", "", t)
    t = _re.sub(r"^music_\d{8}_\d{6}_", "", t)
    return t.replace("_", " ").strip()

@post("/music/video/plan")
def post_music_video_plan(h, path, qs, ctype) -> None:
    """Song + pictures -> a storyboard board, and the shot list to show.

    WHAT THE CALLER MAY SAY ABOUT THE SONG, strongest first: `sections` (a
    JSON list of `{start, end, kind, label?}` in seconds — the structure
    somebody measured), then `lyrics` / `score_abc` in the form, then the
    song's own sidecar, then the band-energy classifier. Each rung is only
    reached when the one above it is silent, because every rung down is one
    more thing being guessed.
    """
    mv = music_video
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    song = _panel_file(_one(form, "song"))
    if song is None:
        h._json({"error": "pick a song from the library, or drop one in. "
                          + _where_to_put_files()}, 400)
        return
    images, err = _music_video_images(_one(form, "images"))
    if err:
        h._json({"error": err}, 400); return
    shot_prompts, err = _music_video_shot_prompts(_one(form, "shots"))
    if err:
        h._json({"error": err}, 400); return
    shot_images, err = _music_video_shot_images(_one(form, "shots"))
    if err:
        h._json({"error": err}, 400); return
    style = _one(form, "style").strip()
    # A PRE-SEPARATED VOCAL OF THE SAME SONG, optional. It conditions every
    # singing shot and never reaches the audience: the panel muxes the
    # original song back over each rendered clip. Same containment rule as
    # the song and the pictures - outputs folder or uploads folder, or it is
    # not used.
    vocal_stem_raw = _one(form, "vocal_stem").strip()
    vocal_stem = _panel_file(vocal_stem_raw) if vocal_stem_raw else None
    if vocal_stem_raw and vocal_stem is None:
        h._json({"error": f"the vocal stem is not a file this panel has "
                          f"({vocal_stem_raw!r}). {_where_to_put_files()}"}, 400)
        return
    title = _board_title_from_song(
        _one(form, "title").strip() or (_sidecar(str(song)) or {}).get("title")
        or song.stem) or "Music video"
    meta = _sidecar(str(song)) or {}
    sections_raw = _one(form, "sections").strip()
    lyrics = _one(form, "lyrics")
    score_abc = _one(form, "score_abc")
    # The pass that DELIVERS the film decides the duration axis: the draft
    # pass is a look at the cut, the final pass is the film, and a cell the
    # final canvas cannot render is a shot that dies after an hour.
    quality = str(P.get_settings().get("storyboard_final_quality", "standard")
                  or "standard")

    notes = []
    # ONE beat map, read here and handed to both callees. The grid is the
    # expensive part of the whole request (an ffmpeg decode plus an
    # autocorrelation) and both `song_sections` and `plan_music_video` need
    # the same one — two would be two answers about where the bars are.
    try:
        mv, sedit = _planner()
        beats = sedit.beat_map(str(song))
    except Exception as exc:                                   # noqa: BLE001
        h._json({"error": f"could not read the beat of that song: {exc}"}, 400)
        return
    try:
        if sections_raw:
            try:
                rows = json.loads(sections_raw)
            except ValueError as exc:
                h._json({"error": f"the sections are not readable JSON: {exc}"},
                        400)
                return
            sections, given = mv.given_sections(
                rows, duration=float(beats.get("duration") or 0.0),
                start=float((beats.get("span") or [0.0])[0]))
            notes.extend(given)
        else:
            sections = mv.song_sections(
                str(song),
                lyrics=lyrics if lyrics.strip() else meta.get("lyrics"),
                score_abc=score_abc if score_abc.strip() else meta.get("score_abc"),
                beats=beats)
            # FILM-12: a real 40 s vocal track with a Singer picture cast
            # planned "0 singing shots · 8 identical B-roll" and said
            # nothing — the band-energy classifier is crude (one ratio
            # against the track's own median) and got this one wrong with
            # no way for the user to notice before the render was spent.
            # Only fires for the CLASSIFIER's own guess (no explicit
            # `sections`/lyrics/score — those are the user's own word on
            # it) and only when a Singer is actually cast — an
            # instrumental-only cast is correctly all B-roll and must not
            # be nagged. `plan_anyway` is the escape hatch once the user
            # has seen the sections and still wants to proceed as-is.
            plan_anyway = _one(form, "plan_anyway").strip().lower() in ("1", "on", "true", "yes")
            has_singer = any(isinstance(im, dict)
                             and str(im.get("role") or "").strip().lower() == "singer"
                             for im in images)
            no_lyrics_or_score = not (lyrics.strip() or score_abc.strip()
                                      or meta.get("lyrics") or meta.get("score_abc"))
            if (has_singer and no_lyrics_or_score and not plan_anyway
                    and not any(str(r.get("kind") or "").strip().lower() == "vocal"
                               for r in sections)):
                h._json({
                    "error": "I heard no singing in this track, so nobody "
                             "would be filmed singing — mark the sung parts "
                             "below, or plan it as B-roll only.",
                    "no_vocal_detected": True,
                    "sections": sections,
                }, 409)
                return
        note = mv.tempo_disagreement(
            beats, (score_abc if score_abc.strip() else meta.get("score_abc")) or "")
        if note:
            notes.append(note)
        # THE DURATION AXIS COMES FROM THE PANEL, not from the planner's own
        # copy: `LTX_LENGTHS` is the table the sampler and `shot_to_job` read,
        # and a cell that exists in one place and not the other is a render
        # that fails after the plan looked fine. It is cut to the delivery
        # QUALITY too — the table's own `qualities` column says the 20 s cell
        # is Quick-only, and a 481-frame a2v shot on the standard 1024×576
        # canvas dies around frame 454.
        #
        # FILM-43: re-planning (a new song, a cast change, "Plan again"
        # after marking sections) used to mint a brand-new board every
        # time — the board list filled up with identically-named copies of
        # the same music video instead of the plan just updating. A caller
        # that names an EXISTING music-video board (`board_id`, the id
        # /music/video/plan itself returned last time) gets that board
        # UPDATED in place; anything else — a fresh plan, or a board_id
        # that turns out not to be a music-video board at all — still gets
        # a new one, exactly as before.
        reuse_id = _one(form, "board_id").strip()
        if reuse_id:
            try:
                existing = P.storyboard.load_storyboard(P.STATE_DIR, reuse_id)
            except Exception:                                       # noqa: BLE001
                existing = None
            if not (isinstance(existing, dict) and existing.get("music_video")):
                reuse_id = ""
        if reuse_id:
            # BOARD-1: never swap a board out from under its own render.
            busy = P._sb_board_busy_reason(reuse_id, existing)
            if busy:
                h._json({"error": busy, "busy": True}, 409)
                return
        board_id = reuse_id or P._sb_new_id(title)
        out = mv.plan_music_video(
            sections, images, style=style, bpm_grid=beats, song=str(song),
            title=title, board_id=board_id, quality=quality,
            shot_prompts=shot_prompts, shot_images=shot_images,
            vocal_stem=str(vocal_stem) if vocal_stem else "",
            cells=mv.cells_from_lengths(P.LTX_LENGTHS, quality=quality))
    except mv.MusicVideoError as exc:
        h._json({"error": str(exc)}, 400); return
    except Exception as exc:                                   # noqa: BLE001
        h._json({"error": f"could not plan that video: {exc}"}, 500); return

    board = out["board"]
    if reuse_id:
        # The board is being UPDATED, not born — keep its original
        # "created" time; `updated_at` (set by _sb_normalize below) is
        # what moves it to the top of a Recent sort.
        board["created_at"] = existing.get("created_at") or board["created_at"]
    # The pass policy is the user's saved Quality, exactly as a planned film
    # gets it — `new_storyboard` hands back the module default, which ignores
    # this machine's canvas cap.
    board["policy"] = P._sb_policy_for(
        P.get_settings().get("storyboard_draft_quality", "quick"),
        P.get_settings().get("storyboard_final_quality", "standard"))
    board["music_video"]["notes"] = list(out["notes"]) + notes
    board["music_video"]["quality"] = quality
    P._sb_normalize(board)
    with P._SB_LOCK:
        if reuse_id:
            # BOARD-1: re-checked under the lock the render route claims its
            # slot with, and held through the save — a render started while
            # this plan was being computed wins, and the old board stays.
            try:
                current = P.storyboard.load_storyboard(P.STATE_DIR, reuse_id)
            except Exception:                                   # noqa: BLE001
                current = existing
            busy = P._sb_board_busy_reason(reuse_id, current)
            if busy:
                h._json({"error": busy, "busy": True}, 409)
                return
        P.storyboard.save_storyboard(P.STATE_DIR, board)
    if reuse_id:
        # BOARD-7: the Editor's timeline was cut from the OLD plan, and
        # Export prefers a saved timeline over the shots — it would have
        # assembled the previous version. It is kept as a draft of its own;
        # the new plan starts a fresh timeline.
        try:
            aside = P._sbe_import().set_aside_for_replan(
                P._sbe_board_dir(board["id"]),
                time.strftime("%b %d %H:%M"))
        except Exception as exc:                                # noqa: BLE001
            aside = None
            P.push(f"[music video] could not set the old timeline aside: {exc}")
        if aside:
            board["music_video"]["notes"].append(
                f"Your earlier Editor timeline is kept as the draft "
                f"“{aside['kept']}” — the new plan starts a fresh one.")
            P.storyboard.save_storyboard(P.STATE_DIR, board)
    P.push(f"[music video] {out['summary']} — {title}")
    h._json({"ok": True, "board_id": board["id"], "title": title,
             "summary": out["summary"], "quality": quality,
             "vocal_stem": str(vocal_stem) if vocal_stem else "",
             "notes": board["music_video"]["notes"],
             "film_seconds": out["film_seconds"],
             "sections": out["board"]["music_video"]["sections"],
             "shots": _shot_rows(out["shots"])})


@post("/music/video/film")
def post_music_video_film(h, path, qs, ctype) -> None:
    """Assemble the board into one film with the SONG as the bed.

    `music_mode="replace"` is the whole point: every a2v clip's own audio is
    thrown away and the song plays alone, so the master is continuous. The
    lip-sync survives that because each singing shot was rendered against its
    own `audio_start_time` segment of this very file — the mouths are already
    on these words.

    THE AUTO-EDITOR IS OFF BY DEFAULT HERE, and this is the one place the
    music-video lane deliberately differs from Export. `plan_cut` snaps every
    cut BACKWARDS onto the nearest downbeat within half a bar and the cursor
    carries the shortfall forward, so on a board whose shots are fixed LTX
    cells it walks the film earlier and earlier — and every a2v clip after the
    first drift slides off the seconds of the song it was rendered against.
    The board is already cut to the grid (`plan_music_video` put every
    boundary there); a second editor can only take that away. `auto_edit=on`
    is accepted for anyone who wants the trimming anyway and knows the cost.
    """
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, _one(form, "board_id").strip())
    except Exception as exc:                                   # noqa: BLE001
        h._json({"error": str(exc)}, 404); return
    block = board.get("music_video") if isinstance(board.get("music_video"), dict) else None
    if not block:
        h._json({"error": "that film was not planned as a music video, so "
                          "there is no song to lay under it"}, 409)
        return
    # BOARD-8: A SAVED TIMELINE OWNS THE FILM'S AUDIO. Somebody who changed
    # the soundtrack or chose the "under" mix in the Editor made the film's
    # sound decisions there; forcing the planning song and `replace` here
    # made Storyboard Export and Editor Render two different films. The
    # board's song is the fallback only when no timeline with clips exists —
    # `_sb_export` then hands the timeline to `_sbe_render_edit`, which reads
    # the soundtrack and the mode off the document, exactly as Render does.
    try:
        edit = P._sbe_import().load_edit(P._sbe_board_dir(board.get("id") or ""))
    except Exception:                                          # noqa: BLE001
        edit = None
    timeline_owns_audio = bool(edit and (edit.get("clips") or []))
    music, music_mode = None, None
    if not timeline_owns_audio:
        song = _panel_file(block.get("song"))
        if song is None:
            h._json({"error": f"the song is no longer at {block.get('song')!r}"},
                    404)
            return
        music, music_mode = str(song), "replace"
    auto = _one(form, "auto_edit") in ("1", "true", "on", "yes")
    P._analytics_feature("music_video_film")
    h._json(P._sb_export(board, auto_edit=auto, music=music,
                         music_mode=music_mode))
