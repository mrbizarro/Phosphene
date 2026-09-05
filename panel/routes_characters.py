"""/characters family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, get_when, post, post_when

P = None  # the running mlx_ltx_panel module; assigned at wiring time


# ====== Characters — discover paired face+audio LoRA bundles =====
# Each bundle becomes a card in the Characters tab. The user picks
# a card, types the scene body, picks framing/duration/quality —
# /characters/<id>/generate assembles the full prompt server-side
# with the locked production recipe applied. See list_characters()
# for the discovery rules.
@get("/characters")
def get_characters(h, parsed) -> None:
    h._json({"characters": P.list_library_characters()})


# Progress poll for the one-click sample-character download.
@get("/characters/download-sample/status")
def get_characters_download_sample_status(h, parsed) -> None:
    with P._sample_char_lock:
        st = dict(P._sample_char_state)
    st["present"] = P._sample_character_present()
    h._json(st)


# ====== Styles — discover trained style LoRAs ====================
# Parallel to /characters but scans for `<trigger>.style.safetensors`
# rather than `<trigger>_v2.safetensors`. Styles are standalone
# LoRAs (no voice pair). Today this powers (a) the Style picker the
# Manual tab can stack alongside a character, and (b) future Compose
# UI work to attach a style to a character render.
@get("/styles")
def get_styles(h, parsed) -> None:
    h._json({"styles": P.list_styles()})


# One-click: fetch the shipped sample character so a user can try
# Character / Remix without training their own first. ~817 MB, so it
# runs in a background thread + returns 202; the UI polls
# /characters/download-sample/status until status == done|error.
@post("/characters/download-sample")
def post_characters_download_sample(h, path, qs, ctype) -> None:
    P._analytics_feature("sample_character")
    if P._sample_character_present():
        h._json({"ok": True, "status": "done", "already": True,
                    "character_id": P.SAMPLE_CHARACTER["trigger"]})
        return
    with P._sample_char_lock:
        already_running = P._sample_char_state.get("status") == "downloading"
    if not already_running:
        P._set_sample_state(status="downloading", mb=0,
                          total_mb=P.SAMPLE_CHARACTER["size_bytes"] // (1 << 20),
                          error=None)
        P.threading.Thread(target=P._download_sample_character_bg,
                         daemon=True, name="phos-sample-char").start()
    h._json({"ok": True, "status": "downloading"}, 202)


# Serve the sample training image for a character so the browser
# can <img src="/characters/<id>/preview">. Path-traversal safe:
# `id` is validated against [A-Za-z0-9_-]+ at parse time, and the
# resolved file MUST live inside the lora-lab dataset root.
@get_when(lambda p: p.startswith("/characters/") and p.endswith("/preview"))
def get_character_preview(h, parsed) -> None:
    cid_raw = parsed.path[len("/characters/"):-len("/preview")]
    try:
        cid = P._character_safe_id(cid_raw)
    except ValueError:
        h.send_error(404); return
    sample = P._character_dataset_image(cid)
    if not sample or not sample.is_file():
        h.send_error(404); return
    # Defense in depth: confirm the resolved file lives under one of
    # the known safe roots before serving its bytes. Either the
    # lora-lab dataset tree (legacy / manual characters) OR the
    # panel-owned characters cache (new Train-tab avatars).
    try:
        lab_root = P.LORA_LAB_ROOT.resolve()
        char_root = P._CHARACTERS_CACHE_PATH.resolve()
        resolved = sample.resolve()
        in_lab = resolved.is_relative_to(lab_root)
        in_char_cache = resolved.is_relative_to(char_root)
        if not (in_lab or in_char_cache):
            h.send_error(404); return
    except (OSError, ValueError):
        h.send_error(404); return
    try:
        data = resolved.read_bytes()
    except OSError:
        h.send_error(500); return
    # PNG content type for the dataset image convention. Falls
    # back to octet-stream if we ever broaden the discovery.
    ctype = ("image/png" if resolved.suffix.lower() == ".png"
             else "image/jpeg" if resolved.suffix.lower() in (".jpg", ".jpeg")
             else "application/octet-stream")
    h._ok(data, ctype)


# Serve the generated character sheet (multi-view turnaround PNG).
# Mirror of /characters/<id>/preview above, with two differences:
# containment is single-root (_CHARACTERS_CACHE_PATH only — sheets
# are panel-generated, there is no lora-lab fallback to allow), and
# `?w=<px>` returns a cached thumbnail through the same
# _ensure_thumbnail lane the /image route uses (a multi-view sheet
# is a 1536x896 PNG; grid cards should not decode the full thing).
@get_when(lambda p: p.startswith("/characters/") and p.endswith("/sheet"))
def get_character_sheet(h, parsed) -> None:
    cid_raw = parsed.path[len("/characters/"):-len("/sheet")]
    try:
        cid = P._character_safe_id(cid_raw)
    except ValueError:
        h.send_error(404); return
    sheet = P._character_sheet_png(cid)
    if not sheet or not sheet.is_file():
        h.send_error(404); return
    try:
        resolved = sheet.resolve()
        if not resolved.is_relative_to(P._CHARACTERS_CACHE_PATH.resolve()):
            h.send_error(404); return
    except (OSError, ValueError):
        h.send_error(404); return
    try:
        w_raw = (P.parse_qs(parsed.query).get("w") or [""])[0] or ""
        req_w = int(w_raw) if w_raw else 0
    except (TypeError, ValueError):
        req_w = 0
    served = resolved
    if req_w > 0:
        try:
            served = P._ensure_thumbnail(resolved, req_w)
        except Exception as exc:
            # Same fallback contract as /image: a failed resize
            # serves the full sheet rather than breaking the card.
            P.push(f"[sheet] thumbnail failed for {cid} @ {req_w}: {exc}")
            served = resolved
    try:
        data = served.read_bytes()
    except OSError:
        h.send_error(500); return
    # Thumbnails re-encode as JPEG for opaque sources; the full
    # sheet is always the PNG the compositor wrote.
    ctype = ("image/jpeg" if served.suffix.lower() == ".jpg"
             else "image/png")
    h._ok(data, ctype)


# Serve the avatar image for a trained style. Mirror of the
# /characters/<id>/preview endpoint but resolves through
# _style_dataset_image and confines the resolved path to
# mlx_models/styles/ (no lora-lab fallback — styles are
# panel-owned only).
@get_when(lambda p: p.startswith("/styles/") and p.endswith("/preview"))
def get_style_preview(h, parsed) -> None:
    sid_raw = parsed.path[len("/styles/"):-len("/preview")]
    try:
        sid = P._character_safe_id(sid_raw)
    except ValueError:
        h.send_error(404); return
    sample = P._style_dataset_image(sid)
    if not sample or not sample.is_file():
        h.send_error(404); return
    try:
        style_root = P._STYLES_CACHE_PATH.resolve()
        resolved = sample.resolve()
        if not resolved.is_relative_to(style_root):
            h.send_error(404); return
    except (OSError, ValueError):
        h.send_error(404); return
    try:
        data = resolved.read_bytes()
    except OSError:
        h.send_error(500); return
    ctype = ("image/png" if resolved.suffix.lower() == ".png"
             else "image/jpeg" if resolved.suffix.lower() in (".jpg", ".jpeg")
             else "image/webp" if resolved.suffix.lower() == ".webp"
             else "application/octet-stream")
    h._ok(data, ctype)


# ====== Character sheet — synchronous, JSON body ==================
# POST /characters/<id>/sheet/generate. All body fields optional:
# {engine_override, wardrobe, views, seed}. Synchronous like
# /image/generate (the caller blocks for the 3-view render);
# generate_character_sheet owns the GPU gate and refuses with a
# busy error rather than queueing.
#
# MUST stay above the urlencoded-body section: the Characters
# render route below matches `endswith("/generate")`, and
# "<id>/sheet/generate" satisfies that too — falling through would
# answer 400 "invalid character id" instead of rendering a sheet.
@post_when(lambda p: p.startswith("/characters/") and p.endswith("/sheet/generate"))
def post_character_sheet_generate(h, path, qs, ctype) -> None:
    cid_raw = path[len("/characters/"):-len("/sheet/generate")]
    try:
        length = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        h._json({"error": "invalid Content-Length"}, 400); return
    # Empty body is a valid request (every field defaults); only a
    # present body must be sane. 64 KB is generous for four fields.
    if length > 64 * 1024:
        h._json({"error": "body too large (max 65536 bytes)"}, 413)
        return
    payload: dict = {}
    if length > 0:
        try:
            payload = P.json.loads(h.rfile.read(length).decode() or "{}")
        except (P.json.JSONDecodeError, UnicodeDecodeError):
            h._json({"error": "invalid JSON body"}, 400); return
        if not isinstance(payload, dict):
            h._json({"error": "JSON body must be an object"}, 400)
            return
    try:
        result = P.generate_character_sheet(
            cid_raw,
            engine_override=(payload.get("engine_override")
                             or "hidream_inline"),
            views=payload.get("views"),
            wardrobe=str(payload.get("wardrobe") or ""),
            seed=payload.get("seed", -1),
        )
    except P.CharacterSheetBusyError as e:
        h._json({"error": str(e)}, 429); return
    except (LookupError, FileNotFoundError) as e:
        h._json({"error": str(e)}, 404); return
    except ValueError as e:
        h._json({"error": str(e)}, 400); return
    except RuntimeError as e:
        # Preflight refusals and engine-side failures both carry
        # actionable messages of their own — surface them verbatim,
        # same as /image/generate does.
        h._json({"error": str(e)}, 500); return
    h._json(result)


# ====== Characters — thin wrapper around /queue/add ================
# 2026-05-16 rewrite (Mr Bizarro): the prior version translated the user's
# request through a 25-field "locked recipe" composer + an auto
# negative_prompt to fight LoRA-baked artifacts. That made the
# Characters tab path diverge from /queue/add and introduced its
# own visual damage on top of LoRA-level issues.
#
# This endpoint now does ONLY:
#   1. look up the character → resolve face + (optional) audio LoRA
#   2. map duration string → frames
#   3. resolve the shared character quality token (Draft / Pro, plus
#      High / High 720p on LTX-2.5) to its real pipeline + canvas
#   4. take the user's prompt verbatim — no prefix, no suffix, no
#      negative-prompt injection, no framing word
#   5. build the same form payload /queue/add accepts and call
#      make_job() directly (same code path as the Manual tab)
@post_when(lambda p: p.startswith("/characters/") and p.endswith("/generate"))
def post_character_generate(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    cid_raw = path[len("/characters/"):-len("/generate")]
    try:
        cid = P._character_safe_id(cid_raw)
    except ValueError:
        h._json({"error": "invalid character id"}, 400); return
    chars = {c["id"]: c for c in P.list_characters()}
    char = chars.get(cid)
    if not char:
        h._json({"error": f"character {cid!r} not found"}, 404); return

    # User's prompt — accept either field name for back-compat.
    prompt = (
        form.get("full_prompt", [""])[0]
        or form.get("prompt", [""])[0]
        or form.get("prompt_body", [""])[0]
        or ""
    ).strip()
    if not prompt:
        h._json({"error": "prompt required"}, 400); return

    duration = (form.get("duration", ["7s"])[0] or "7s").strip()
    if duration not in P._CHARACTER_DURATION_FRAMES:
        h._json({"error": f"duration must be one of "
                    f"{sorted(P._CHARACTER_DURATION_FRAMES)}"}, 400); return
    frames = P._CHARACTER_DURATION_FRAMES[duration]

    _q_raw = (form.get("quality", [""])[0] or "").strip().lower()
    _resolved_quality = P.resolve_character_quality(
        _q_raw, P.ACTIVE_MODEL_VERSION)
    if _resolved_quality is None:
        h._json({"error": "quality must be draft, pro, high or "
                                 "high720"}, 400); return
    quality_choice = str(_resolved_quality["key"])
    quality = str(_resolved_quality["quality"])
    width = int(_resolved_quality["width"])
    height = int(_resolved_quality["height"])

    seed = (form.get("seed", ["-1"])[0] or "-1").strip()
    try:
        seed_int = int(seed)
    except (TypeError, ValueError):
        h._json({"error": "seed must be an integer"}, 400); return

    # Character LoRA strengths — face and voice, separately.
    #
    # ONE CHARACTER, ONE MEANING OF "STRENGTH", WHICHEVER TAB YOU ARE ON.
    # This lane read `character_strength` with a default of 0.8 and
    # applied that one number to BOTH files, while the Manual tab's
    # picker defaulted to 1.0 — so the same character rendered
    # differently depending on which surface launched it, and neither
    # number was written down anywhere the other could see. The 0.8 was
    # a 2.3-era correction for over-baked visual quirks at 5000 steps;
    # on 2.5 q8 the graded recipe is the face at 1.0.
    #
    # The voice takes its own number for the reason spelled out in
    # make_job: the face file's audio-branch deltas are noise and are
    # louder than the voice file's signal at equal strength. It defaults
    # to 1.0 — the graded pair, and the arm that won the listening test
    # against a hotter one. Same default on both lanes or the two
    # surfaces disagree again.
    try:
        char_strength = float(
            (form.get("character_strength", ["1.0"])[0] or "1.0"))
    except (TypeError, ValueError):
        char_strength = 1.0
    char_strength = max(0.0, min(2.0, char_strength))
    try:
        char_voice_strength = float(
            (form.get("character_voice_strength", ["1.0"])[0] or "1.0"))
    except (TypeError, ValueError):
        char_voice_strength = 1.0
    char_voice_strength = max(0.0, min(2.0, char_voice_strength))

    # LoRA stack: face always, audio when the character has one.
    # Extra LoRAs (style LoRAs like cinematronx, etc.) can be
    # appended via the `extra_loras` form field — JSON array of
    # `{path, strength}` objects. Strengths clamped to [0, 2.0]
    # to prevent footguns.
    lora_stack = [{"path": char["face_lora_path"], "strength": char_strength}]
    if char.get("audio_lora_path"):
        lora_stack.append({"path": char["audio_lora_path"],
                           "strength": char_voice_strength})
    extra_loras_raw = (form.get("extra_loras", [""])[0] or "").strip()
    if extra_loras_raw:
        try:
            extras = P.json.loads(extra_loras_raw)
            if not isinstance(extras, list):
                raise ValueError("extra_loras must be a JSON array")
            for entry in extras:
                if not isinstance(entry, dict) or "path" not in entry:
                    raise ValueError("each extra_loras entry must be {path, strength}")
                path_val = str(entry["path"]).strip()
                if not path_val:
                    continue
                # Defense-in-depth: the LoRA file must live under
                # mlx_models/loras/ — no path traversal.
                if not P.Path(path_val).resolve().is_relative_to(P.LORAS_DIR.resolve()):
                    raise ValueError(f"extra LoRA must be inside {P.LORAS_DIR}")
                try:
                    strength = float(entry.get("strength", 1.0))
                except (TypeError, ValueError):
                    strength = 1.0
                strength = max(0.0, min(2.0, strength))
                lora_stack.append({"path": path_val, "strength": strength})
        except (P.json.JSONDecodeError, ValueError) as exc:
            h._json({"error": f"extra_loras invalid: {exc}"}, 400); return

    # Optional image / audio paths for i2v and i2v+clean-audio modes.
    # Frontend uploads files via /upload, gets back a path, and
    # passes it here. Mode auto-switches:
    #   image + audio  → i2v_clean_audio (character lip-syncs to audio)
    #   image only     → i2v             (character animates from still)
    #   audio only     → t2v + audio     (audio drives generation; rare)
    #   neither        → t2v             (default)
    image_path = (form.get("image", [""])[0] or "").strip()
    audio_path = (form.get("audio", [""])[0] or "").strip()
    if image_path and not P.Path(image_path).exists():
        h._json({"error": f"image not found: {image_path}"}, 400); return
    if audio_path and not P.Path(audio_path).exists():
        h._json({"error": f"audio not found: {audio_path}"}, 400); return
    if image_path and audio_path:
        mode = "i2v_clean_audio"
    elif image_path:
        mode = "i2v"
    else:
        mode = "t2v"

    # Pass-through optional caller overrides (replay / Load Params)
    # — same fields /queue/add accepts. No invented panel-specific
    # transforms. Negative prompt is the user's responsibility; we
    # default to empty (matches /queue/add and /tmp/bizarro_talking_reel.py).
    def _val(key, default):
        v = form.get(key, [""])[0]
        return v if v else default

    job_form = {
        "mode": [mode],
        "prompt": [prompt],
        "negative_prompt": [_val("negative_prompt", "")],
        "width": [str(width)],
        "height": [str(height)],
        "frames": [str(frames)],
        "frame_rate": ["24"],
        "seed": [str(seed_int)],
        # THE RESOLVED quality, not a hardcoded "high". This line was
        # missed by the commit that added character_render_quality(),
        # so the Characters TAB kept doing exactly the two things that
        # function's docstring says it exists to prevent: routing a
        # DEFAULT 2.5 character onto the slower two-stage HQ path and
        # demanding the 29.5 GB High add-on without an explicit choice.
        # The main
        # form was fixed; this lane was not, so the same character
        # rendered differently depending on which tab launched it —
        # the exact split the strength unification closed earlier.
        #
        # `quality` above is character_render_quality() unless the
        # caller asked for something explicitly: ltx25 -> balanced
        # (q8 + distilled, the graded recipe), ltx23 -> high.
        # make_job's character branch then routes the PACK to q8.
        "quality": [quality],
        "temporal_mode": ["native"],
        # HQ TeaCache default — 1.8 is the empirical sweet spot for
        # character mode (see make_job comment). Reverted from 1.0
        # after the 2026-05-20 chartest v3 diagnostic confirmed 1.0
        # produces specks + slower wall than 1.8.
        "teacache_thresh": [_val("teacache_thresh", "1.8")],
        "cfg_scale": [_val("cfg_scale", "3.0")],
        "bongmath_max_iter": [_val("bongmath_max_iter", "100")],
        # skip-step entries removed (v4.0.5): make_job no longer
        # allowlists them and the engine boundary dropped them anyway.
        "upscale": ["fit_720p"],
        "upscale_method": ["lanczos"],
        "accel": ["off"],
        "enhance": ["false"],   # CRITICAL: don't let Gemma strip the trigger
        "hdr": ["false"],
        "loras": [P.json.dumps(lora_stack)],
        "label": [f"{char.get('name', char['trigger'])} · {duration} · {quality}"],
    }
    if image_path:
        job_form["image"] = [image_path]
    if audio_path:
        job_form["audio"] = [audio_path]

    # Minimal Characters-origin metadata for Load Params restoration.
    # Schedule step counts ride only when the CALLER sent them — the
    # same rule make_job now follows. This form used to hardcode
    # stage1_steps=10, re-introducing the pad-request landmine that
    # cost Colorize/Restore 218 s per failed render: inert on the HQ
    # lane (which computes its schedule) but a live hazard the moment a
    # character routes to a thinning lane. On 2.3 nothing changes —
    # generate_hq's own default is the same 10.
    for _k in ("stage1_steps", "stage2_steps"):
        _v = form.get(_k, [""])[0]
        if _v:
            job_form[_k] = [_v]
    job_form["character_id"] = [cid]
    job_form["source"] = ["characters"]
    job_form["duration"] = [duration]
    job_form["quality_choice"] = [quality_choice]
    # prompt_body kept as a back-compat alias to the verbatim prompt
    # so older Load-Params restorers still find something.
    job_form["prompt_body"] = [prompt]
    try:
        job = P.make_job(job_form)
    except P.CharacterRequestError as exc:
        # A bundle can become incomplete after the grid was drawn
        # (for example its audio LoRA is moved between click and
        # submit). Refuse the now-half-character rather than dropping
        # the connection or rendering only its face.
        h._json({"error": str(exc)}, 400); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    P.push(f"characters: queued {cid} duration={duration} quality={quality} "
         f"job_id={job['id']}")
    h._json({
        "ok": True,
        "job_id": job["id"],
        "assembled_prompt": prompt,
        "character": {
            "id": cid,
            "name": char.get("name"),
            "trigger": char.get("trigger"),
        },
    })


# ===== Character management (2026-05-18) =====================
# POST /characters/<id>/delete and /characters/<id>/rename
# power the "Manage characters" modal in the Manual tab. Delete
# removes the entire character bundle (face + audio LoRAs +
# sidecars + voice clip + characters/<id>/ avatar cache).
# Rename only edits the display name in bundle.json — the
# trigger word is baked into the trained weights via captions
# so it can't be meaningfully renamed; we keep the file names
# and trigger immutable to avoid corrupting saved jobs that
# reference the character by id/path.
@post_when(lambda p: p.startswith("/characters/") and p.endswith("/delete"))
def post_character_delete(h, path, qs, ctype) -> None:
    cid_raw = path[len("/characters/"):-len("/delete")]
    try:
        cid = P._character_safe_id(cid_raw)
    except ValueError:
        h._json({"ok": False, "error": "invalid id"}, 400); return
    try:
        # Bound the deletion to mlx_models/loras/ + characters/<cid>/.
        # We never touch state/train_character/<cid>/ — the user's
        # training dataset is a separate asset they may want to
        # retrain from, and a Manage-characters click shouldn't
        # destroy weeks of dataset curation.
        base = P._safe_loras_dir().resolve()
        char_cache = (P._CHARACTERS_CACHE_PATH / cid).resolve()
        removed: list[str] = []
        # All files in loras/ whose name starts with the trigger
        # AND is one of the known shapes — explicit pattern
        # whitelist so we don't accidentally remove an unrelated
        # file that happens to share a prefix.
        patterns = [
            f"{cid}_v2.safetensors",
            f"{cid}_v2.safetensors.json",
            f"{cid}_v2.json",
            f"{cid}.audio.safetensors",
            f"{cid}.audio.safetensors.json",
        ]
        for ext in P.TRAIN_VOICE_EXTS:
            patterns.append(f"{cid}.voice{ext}")
        for name in patterns:
            p = (base / name).resolve()
            if p.is_file() and p.is_relative_to(base):
                try:
                    p.unlink()
                    removed.append(str(p))
                except OSError as e:
                    return h._json({
                        "ok": False,
                        "error": f"failed to remove {p.name}: {e}",
                        "removed_so_far": removed,
                    }, 500)
        # Avatar cache directory (mlx_models/characters/<cid>/).
        # Best-effort recursive removal. Safe because the path is
        # bound to _CHARACTERS_CACHE_PATH which only contains
        # per-character subdirectories.
        if char_cache.is_dir() and char_cache.is_relative_to(P._CHARACTERS_CACHE_PATH.resolve()):
            try:
                # NOTE: shutil is imported at module level (line 25).
                # Do NOT add a local `import shutil` here — Python
                # scans the whole function and would mark `shutil`
                # as a local across all of do_POST, which made the
                # /output/delete handler ~30 lines up raise
                # UnboundLocalError on every call. Module-level
                # shutil is sufficient for shutil.rmtree below.
                P.shutil.rmtree(char_cache)
                removed.append(str(char_cache))
            except OSError as e:
                return h._json({
                    "ok": False,
                    "error": f"failed to remove avatar cache: {e}",
                    "removed_so_far": removed,
                }, 500)
        if not removed:
            return h._json({"ok": False, "error": "character not found"}, 404)
        h._json({"ok": True, "id": cid, "removed": removed})
    except Exception as exc:
        h._json({"ok": False, "error": str(exc)}, 500)


@post_when(lambda p: p.startswith("/characters/") and p.endswith("/rename"))
def post_character_rename(h, path, qs, ctype) -> None:
    # The one moved POST handler that lost its body-read preamble in the
    # route split — every rename raised NameError (review 2026-09-02).
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    cid_raw = path[len("/characters/"):-len("/rename")]
    try:
        cid = P._character_safe_id(cid_raw)
    except ValueError:
        h._json({"ok": False, "error": "invalid id"}, 400); return
    new_name_raw = form.get("name", [""])[0] or form.get("name", "")
    if isinstance(new_name_raw, list):
        new_name_raw = new_name_raw[0] if new_name_raw else ""
    new_name = str(new_name_raw).strip()
    if not new_name:
        h._json({"ok": False, "error": "name is required"}, 400); return
    if len(new_name) > 120:
        h._json({"ok": False, "error": "name too long (max 120 chars)"}, 400); return
    try:
        # Confirm the character exists before writing a stray
        # bundle. We don't want a rename to silently CREATE a
        # bundle for a trigger that has no LoRA on disk.
        face = P._safe_loras_dir().resolve() / f"{cid}_v2.safetensors"
        if not face.is_file():
            return h._json({"ok": False, "error": "character not found"}, 404)
        char_dir = P._CHARACTERS_CACHE_PATH / cid
        char_dir.mkdir(parents=True, exist_ok=True)
        bundle_path = char_dir / "bundle.json"
        # Merge-update — preserve any existing pronoun /
        # subject_noun / default_action fields the user may
        # have set previously.
        payload: dict = {}
        if bundle_path.is_file():
            try:
                existing = P.json.loads(bundle_path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing
            except (OSError, P.json.JSONDecodeError):
                payload = {}
        payload["name"] = new_name
        bundle_path.write_text(
            P.json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        h._json({"ok": True, "id": cid, "name": new_name})
    except Exception as exc:
        h._json({"ok": False, "error": str(exc)}, 500)
