"""/storyboard family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, get_when, post, post_when

P = None  # the running mlx_ltx_panel module; assigned at wiring time


# ---- Storyboard reads ------------------------------------------
@get("/storyboard/list")
def get_storyboard_list(h, parsed) -> None:
    h._json({"ok": True, "boards": P._sb_all_summaries()})


# The finished films, for the screen that shows them. Reads the folder
# the assemblers write into; makes nothing, changes nothing.
@get("/storyboard/films")
def get_storyboard_films(h, parsed) -> None:
    bid = (P.parse_qs(parsed.query).get("id") or [""])[0].strip()
    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, bid)
    except Exception as exc:                                # noqa: BLE001
        h._json({"ok": False, "error": str(exc)}, 404)
        return
    d = P._sb_film_dir(board)
    h._json({"ok": True, "dir": str(d),
                "dir_short": P._sb_display_path(d),
                "films": P._sb_films(board)})


@get("/storyboard/get")
def get_storyboard_get(h, parsed) -> None:
    bid = (P.parse_qs(parsed.query).get("id") or [""])[0].strip()
    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, bid)
    except Exception as exc:                                # noqa: BLE001
        h._json({"ok": False, "error": str(exc)}, 404)
        return
    # Reconcile job ids -> shot status BEFORE replying, and save if the
    # queue told us something the board didn't know. This is what makes
    # a panel restart mid-render invisible to the user.
    try:
        if P._sb_reconcile(board):
            P.storyboard.save_storyboard(P.STATE_DIR, board)
    except Exception:
        pass
    h._json(P._sb_payload(board))


# ====== the Editor's media pool — bring your own file =============
# "You cannot upload your own images and insert them into the
# timeline." The pool could show everything the panel MADE and nothing
# the user already had, which for a title card, a logo or a phone clip
# meant re-rendering something that was already on disk.
#
# WHERE THE BYTES LAND IS THE WHOLE DESIGN. An image goes into
# UPLOADS/library/manual/, which `list_outputs` already walks — so it
# appears in the Images source on the next refresh and STAYS there
# across restarts, with no second listing to keep in sync. A video
# goes into UPLOADS/timeline/, which `_sbe_pool_path_ok` already
# accepts, and is listed by the route below: the gallery scans
# OUTPUT/*.mp4 only, and dropping somebody's phone clip in among the
# renders would put a file the panel never made into the one folder
# that means "the panel made this".
@post("/storyboard/edit/upload")
def post_storyboard_edit_upload(h, path, qs, ctype) -> None:
    # The chain arm carried this condition; its failure fell
    # through to the chain end, which answers 404.
    if not (ctype.startswith("multipart/form-data")):
        h.send_error(404)
        return
    # 320 MB. `_parse_multipart_form` reads the body into memory, so
    # this is a real ceiling and not a formality — big enough for any
    # clip somebody drags in, small enough that two at once cannot
    # take the panel down.
    SBE_UPLOAD_MAX = 320 * 1024 * 1024
    SBE_UPLOAD_IMAGE = {".png", ".jpg", ".jpeg", ".webp"}
    SBE_UPLOAD_VIDEO = {".mp4", ".mov", ".m4v", ".webm"}
    try:
        clen = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        clen = 0
    if clen <= 0:
        h._json({"ok": False, "error": "Content-Length required"}, 411)
        return
    if clen > SBE_UPLOAD_MAX:
        h._json({"ok": False,
                    "error": f"that file is larger than "
                             f"{SBE_UPLOAD_MAX // (1024 * 1024)} MB"}, 413)
        return
    try:
        form = P._parse_multipart_form(h.rfile, ctype, clen)
        fld = form["file"] if "file" in form else None
        if fld is None or not getattr(fld, "filename", None):
            h._json({"ok": False, "error": "no file"}, 400)
            return
        out = P._sbe_accept_upload(str(fld.filename), fld.file.read(),
                                 images=SBE_UPLOAD_IMAGE,
                                 videos=SBE_UPLOAD_VIDEO)
    except Exception as exc:                                # noqa: BLE001
        out = {"ok": False, "error": f"upload failed: {exc}",
               "status": 500}
    h._json({k: v for k, v in out.items() if k != "status"},
               200 if out.get("ok") else int(out.get("status") or 400))


# ====== the Editor's audio tracks — where a sound comes from ========
# The Sound source of the media pool: the film's own `audio/` folder and
# every sound file at the top of the outputs. A read; it makes nothing.
@get("/storyboard/edit/sounds")
def get_storyboard_edit_sounds(h, parsed) -> None:
    bid = (P.parse_qs(parsed.query).get("id") or [""])[0].strip()
    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, bid)
    except Exception as exc:                                # noqa: BLE001
        h._json({"ok": False, "error": str(exc)}, 404)
        return
    h._json({"ok": True, "sounds": P._sbe_list_sounds(board),
             "dir": str(P._sbe_sound_dir(board))})


# One file → a sound an audio track can hold: probed for its length, and
# brought into the film's `audio/` folder when it lives anywhere the preview
# cannot play it from. The strip itself is placed by the client, which owns
# the arrangement, and saved like every other edit.
@post("/storyboard/edit/add-sound")
def post_storyboard_edit_add_sound(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb

    def f(name: str) -> str:
        v = form.get(name) or [""]
        return str(v[0] if isinstance(v, list) else v).strip()

    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, f("id"))
    except Exception as exc:                                # noqa: BLE001
        h._json({"ok": False, "error": str(exc)}, 404)
        return
    try:
        out = P._sbe_add_sound(board, f("path"))
    except Exception as exc:                                # noqa: BLE001
        out = {"ok": False, "status": 500, "error": f"could not add that sound: {exc}"}
    h._json({k: v for k, v in out.items() if k != "status"},
            200 if out.get("ok") else int(out.get("status") or 400))


# ====== Room tone — a generated bed under the whole film ============
# The picker's rows and the numbers the Room tone card needs. Read-only.
@get("/storyboard/edit/room-tone")
def get_storyboard_edit_room_tone(h, parsed) -> None:
    import room_tone as rt                                  # noqa: PLC0415
    h._json({"ok": True, "variants": rt.variants(),
             "default_variant": rt.DEFAULT_VARIANT,
             "default_level": rt.DEFAULT_LEVEL,
             "level_min": rt.LEVEL_MIN, "level_max": rt.LEVEL_MAX,
             "ref_lufs": rt.REF_LUFS,
             "auto": bool(P.get_settings().get("room_tone_auto", True))})


# Make (or reuse) one bed. The client sends the timeline it is showing —
# `clips` as JSON `[{path, start, end}]` and `film_len` — because a room tone
# "from this film" must be made from the cut on screen, saved or not. The
# track itself is placed by the client, like every other sound.
@post("/storyboard/edit/room-tone")
def post_storyboard_edit_room_tone(h, path, qs, ctype) -> None:
    import json as _json                                    # noqa: PLC0415
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb

    def f(name: str) -> str:
        v = form.get(name) or [""]
        return str(v[0] if isinstance(v, list) else v).strip()

    try:
        board = P.storyboard.load_storyboard(P.STATE_DIR, f("id"))
    except Exception as exc:                                # noqa: BLE001
        h._json({"ok": False, "error": str(exc)}, 404)
        return
    try:
        clips = _json.loads(f("clips") or "[]")
        if not isinstance(clips, list):
            raise ValueError("clips must be a list")
        seed = int(f("seed") or 1)
        film_len = float(f("film_len") or 0.0)
    except (ValueError, TypeError) as exc:
        h._json({"ok": False, "error": f"bad room tone request: {exc}"}, 400)
        return
    try:
        out = P._sbe_room_tone(board, variant=f("variant") or "film", seed=seed,
                               film_len=film_len, clips=clips)
    except Exception as exc:                                # noqa: BLE001
        out = {"ok": False, "status": 500,
               "error": f"could not make the room tone: {exc}"}
    h._json({k: v for k, v in out.items() if k != "status"},
            200 if out.get("ok") else int(out.get("status") or 400))


# ====== Storyboard — plan a film, then shoot it ====================
# Sits with the /queue/* cluster on purpose: every one of these routes
# ends up going through the SAME make_job -> STATE["queue"] contract
# above, and none of them has a private execution path.
@post_when(lambda p: p.startswith("/storyboard/"))
def post_storyboard_write(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    h._storyboard_post(path[len("/storyboard/"):], body, form)
