"""/files family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, get_when, post, post_when

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/uploads")
def get_uploads(h, parsed) -> None:
    # Recent panel_uploads/* images for the picker's "click to reuse"
    # strip. Limit defaults to 40; client can paginate later if needed.
    try:
        limit = int(P.parse_qs(parsed.query).get("limit", ["40"])[0])
    except (TypeError, ValueError):
        limit = 40
    h._json({"uploads": P.list_uploads(limit=max(1, min(200, limit)))})


# Delete one imported reference image (and drop its thumbnails). Asked for
# on Pinokio: the Recent-uploads strip had no way to remove an image, and a
# file removed by hand in Finder kept its cached thumbnail. Path-bound to
# UPLOADS like every other file route; the thumb cache is keyed by
# (path, mtime, size, width) so it cannot be purged per-file — it is
# cleared whole and rebuilds lazily (thumbnails are small).
@post("/upload/delete")
def post_upload_delete(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    raw = (form.get("path", [""])[0] or "").strip()
    if not raw:
        h._json({"ok": False, "error": "path required"}, 400); return
    try:
        target = P.Path(raw).resolve()
        uploads = P.UPLOADS.resolve()
    except Exception:
        h._json({"ok": False, "error": "bad path"}, 400); return
    if not target.is_relative_to(uploads) or target == uploads:
        h._json({"ok": False, "error": "path must be an imported image"}, 400); return
    if not target.is_file():
        h._json({"ok": False, "error": "no such upload"}, 404); return
    # A picture a waiting render needs stays until that render is done.
    in_use = P.delete_in_use_refusal([target], "picture")
    if in_use:
        h._json({"ok": False, "error": in_use, "in_use": True}, 409); return
    try:
        target.unlink()
    except OSError as exc:
        h._json({"ok": False, "error": f"could not delete: {exc}"}, 500); return
    try:
        P.shutil.rmtree(P._THUMBCACHE, ignore_errors=True)
    except Exception:
        pass
    P.push(f"upload removed: {target.name}")
    h._json({"ok": True, "deleted": str(target),
             "uploads": P.list_uploads(limit=24)})


# 4.17 Codex SAFETY-13: the draft restore brings back an Image/Keyframe/
# Extend/audio source path saved in localStorage — possibly one deleted since.
# Images validate themselves in the picker (/image 404s); an audio upload or
# an Extend source has no preview to fail, so the restore asks here. Bound to
# outputs/ and uploads/ like every file route; answers only a boolean.
@get("/file/exists")
def get_file_exists(h, parsed) -> None:
    raw = (P.parse_qs(parsed.query).get("path", [""])[0] or "").strip()
    try:
        path = P.Path(raw).resolve() if raw else None
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        path, roots = None, []
    ok = bool(path) and any(path.is_relative_to(r) for r in roots) and path.is_file()
    h._json({"exists": ok})


@get("/file")
def get_file(h, parsed) -> None:
    qs = P.parse_qs(parsed.query)
    try:
        path = P.Path(qs.get("path", [""])[0]).resolve()
    except Exception:
        h.send_error(400); return
    # Strict containment via Path.is_relative_to — str.startswith
    # would let "mlx_outputs_evil/" slip past since the prefix string
    # match is true even though the directory is a sibling, not a child.
    # OUTPUT or UPLOADS, the same two roots /image, /file/exists and the
    # poster route already serve. The Editor imports videos into
    # UPLOADS/timeline and previews them here; with OUTPUT alone, an imported
    # clip showed black in the media pool and the Source monitor.
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        h.send_error(500); return
    if not any(path.is_relative_to(r) for r in roots):
        h.send_error(404); return
    if not path.exists():
        h.send_error(404); return
    ctype = {".wav": "audio/wav", ".mp4": "video/mp4", ".png": "image/png",
             ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(path.suffix.lower(), "video/mp4")
    h._serve_video_with_range(path, ctype=ctype)


#: What /image will serve. Every URL the panel mints for it points at one of
#: these (uploads, outputs, previews, stills), and nothing secret has them.
#: The picker takes `image/*`, so the less common raster formats a Mac hands
#: over (BMP, TIFF, AVIF, HEIC) stay viewable as reference thumbnails. SVG is
#: deliberately absent: opened from this origin it can run script.
IMAGE_ROUTE_EXTS = frozenset({".png", ".jpg", ".jpeg", ".jpe", ".jfif",
                              ".webp", ".gif", ".bmp", ".tif", ".tiff", ".avif",
                              ".heic", ".heif"})


@get("/image")
def get_image(h, parsed) -> None:
    qs = P.parse_qs(parsed.query)
    try:
        path = P.Path(qs.get("path", [""])[0]).resolve()
    except Exception:
        h.send_error(400); return
    # Loopback alone isn't enough — a malicious page or extension on
    # the local machine could request /image?path=/etc/shadow. Resolve
    # both sides and require the requested path to live under our
    # OUTPUT, UPLOADS, or STATE_DIR. is_relative_to() already handles
    # the symlink/.. tricks since we resolved both ends.
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve(), P.STATE_DIR.resolve()]
    except Exception:
        roots = []
    if not any(path.is_relative_to(r) for r in roots):
        h.send_error(403); return
    # IMAGES ONLY, checked before anything is opened. The roots above include
    # STATE_DIR (live previews, character stills), and STATE_DIR also holds
    # panel_settings.json — with the HF, CivitAI and PostHog keys that
    # /settings deliberately never returns. Without this line
    # `/image?path=<state>/panel_settings.json` answered 200 with the whole
    # file as application/octet-stream (Codex UI-01, 2026-09-24). The same
    # refusal covers queue, stats and every other non-image under the roots,
    # and the thumbnail path below, which fell back to the original on error.
    if path.suffix.lower() not in IMAGE_ROUTE_EXTS:
        h.send_error(403); return
    if not path.exists() or not path.is_file():
        h.send_error(404); return
    # Optional thumbnail resize. Mr Bizarro 2026-05-20 diag: the
    # main pane carousel decoded 1+ GB of bitmaps because HiDream
    # Quality renders are 2560x1440 PNGs (14 MB each, decoded as
    # RGBA), painted at ~200 px thumbnails. Clients pass `w=480`
    # for thumbnail use-cases; the player/lightbox still requests
    # the full image (no w param). Resized variants are cached on
    # disk keyed by (source path, mtime, requested width) so
    # repeated requests cost one disk read.
    try:
        w_raw = qs.get("w", [""])[0] or ""
        req_w = int(w_raw) if w_raw else 0
    except (TypeError, ValueError):
        req_w = 0
    req_w = max(0, min(2048, req_w))
    thumb_path: P.Path | None = None
    if req_w > 0:
        try:
            thumb_path = P._ensure_thumbnail(path, req_w)
        except Exception as exc:
            # Fall back to the original — better to serve a big
            # image than to break the gallery.
            P.push(f"thumbnail resize failed for {path.name} @ {req_w}: {exc}")
            thumb_path = None
    served = thumb_path or path
    served_ext = served.suffix.lower()
    ctype = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".jpe": "image/jpeg", ".jfif": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp",
        ".tif": "image/tiff", ".tiff": "image/tiff", ".avif": "image/avif",
        ".heic": "image/heic", ".heif": "image/heif",
    }.get(served_ext, "application/octet-stream")
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(served.stat().st_size))
    # The ORIGINAL's name, the SERVED file's extension.
    #
    # Neither half is optional. `served.name` alone would hand the user
    # a cache key (`clip.png.480.webp`), which is not a name anyone
    # asked for. But `path.name` alone lies in the other direction: a
    # `w=480` request re-encodes a PNG as JPEG, so calling those bytes
    # `alice_start.png` saves a file whose extension contradicts its
    # contents. Stem from the original, suffix from what actually went
    # down the socket — recognisable AND true.
    _dl_name = h._inline_filename(path)
    if served is not path:
        _dl_name = P.Path(_dl_name).stem + served.suffix
    h.send_header(
        "Content-Disposition",
        f'inline; filename="{_dl_name}"')
    h.end_headers()
    with served.open("rb") as fh:
        h.wfile.write(fh.read())


#: VC-31/36: video containers the poster route will generate a frame for.
#: Everything else under OUTPUT/UPLOADS (images, audio) has no poster to
#: make and is refused rather than silently 404ing from inside
#: _ensure_video_poster.
POSTER_ROUTE_EXTS = frozenset({".mp4", ".mov", ".m4v", ".webm"})


@get("/poster")
def get_poster(h, parsed) -> None:
    """One cached JPEG frame of a rendered clip — what lets a gallery card
    be a plain `<img>` instead of a live `<video preload="metadata">`
    (VC-31/36). Same containment rule as /image; generation itself is
    _ensure_video_poster's job (idempotent, cached in the same
    _THUMBCACHE _ensure_thumbnail already uses)."""
    qs = P.parse_qs(parsed.query)
    try:
        path = P.Path(qs.get("path", [""])[0]).resolve()
    except Exception:
        h.send_error(400); return
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(path.is_relative_to(r) for r in roots):
        h.send_error(403); return
    if path.suffix.lower() not in POSTER_ROUTE_EXTS:
        h.send_error(403); return
    if not path.exists() or not path.is_file():
        h.send_error(404); return
    try:
        poster = P._ensure_video_poster(path)
    except Exception as exc:                                     # noqa: BLE001
        P.push(f"poster generation failed for {path.name}: {exc}")
        poster = None
    if poster is None:
        h.send_error(404); return
    h.send_response(200)
    h.send_header("Content-Type", "image/jpeg")
    h.send_header("Content-Length", str(poster.stat().st_size))
    # Cacheable — the file only changes if the SOURCE clip's mtime/size
    # changes, and the cache key already encodes that (a rewritten source
    # gets a new key, this response for the old one is simply never
    # requested again).
    h.send_header("Cache-Control", "public, max-age=604800, immutable")
    h.end_headers()
    with poster.open("rb") as fh:
        h.wfile.write(fh.read())


@get("/sidecar")
def get_sidecar(h, parsed) -> None:
    qs = P.parse_qs(parsed.query)
    try:
        path = P.Path(qs.get("path", [""])[0]).resolve()
    except Exception:
        h.send_error(400); return
    # M1: only serve sidecars whose underlying media has a known
    # extension. Without this, an attacker who dropped a `.json`
    # alongside an arbitrary file under OUTPUT/UPLOADS could read
    # it via /sidecar?path=<that-file>. Sidecars are always
    # `<media>.json` where <media> is mp4/png/webp/jpg/jpeg/wav, so
    # rejecting other suffixes closes that off without breaking
    # any legitimate caller. See security-review.md §M1.
    _SIDECAR_MEDIA_SUFFIXES = {".mp4", ".png", ".webp", ".jpg", ".jpeg", ".wav"}
    if path.suffix.lower() not in _SIDECAR_MEDIA_SUFFIXES:
        h.send_error(400, "sidecar requires media path "
                             "(.mp4/.png/.webp/.jpg/.jpeg/.wav)")
        return
    # Sidecars live next to the output they describe. Videos sit
    # under OUTPUT (mlx_outputs/*.mp4.json); image-mode queue jobs
    # write theirs under UPLOADS/library/manual/<...>/cand_*.png.json.
    # Allow both roots so the right-pane Outputs gallery can fetch
    # the prompt for a photo's Animate flow without 404'ing.
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(path.is_relative_to(r) for r in roots):
        h.send_error(404); return
    sidecar = path.with_suffix(path.suffix + ".json")
    if not sidecar.exists():
        # Companion fallback: after an upscale pass the panel writes
        # the sidecar next to the `<base>_<tag>.mp4` (visible card),
        # NOT next to the raw `<base>.mp4`. If the user clicks Load
        # Params on the raw — which is still in the gallery as a
        # second card — the direct lookup 404s even though a real
        # sidecar exists on the upscaled sibling.
        # Walk the same family the /output/delete code walks:
        # strip / append known upscale tags + check whichever
        # variant has a `.json`. Bounded to the same OUTPUT/
        # UPLOADS roots so this can't be tricked into leaking
        # arbitrary `.json` files.
        stem = path.stem
        ext = path.suffix
        parent = path.parent
        base = stem
        for tag in P.UPSCALE_TAGS:
            suf = f"_{tag}"
            if base.endswith(suf):
                base = base[: -len(suf)]
                break
        candidates = [parent / f"{base}{ext}.json"]
        for tag in P.UPSCALE_TAGS:
            candidates.append(parent / f"{base}_{tag}{ext}.json")
        companion = next(
            (c for c in candidates
             if c.is_file()
             and any(c.resolve().is_relative_to(r) for r in roots)),
            None,
        )
        if companion is None:
            h.send_error(404); return
        sidecar = companion
    # The sidecar itself must stay under the roots too (ov-review A-02): the
    # media path was resolved and confined above, but `<media>.json` was read
    # as found - a symlink at that name would have served any file it points
    # at (panel_settings.json and its saved keys included).
    try:
        _side_real = sidecar.resolve()
    except OSError:
        h.send_error(404); return
    if not _side_real.is_file() or not any(_side_real.is_relative_to(r) for r in roots):
        h.send_error(404); return
    # VC-19: the modal's "Format" line used to read straight from
    # params.width/height — the RENDER canvas, not the DELIVERED file.
    # Balanced's default 720p-fit export (or any upscale) changes the two,
    # and the modal reported the render size as if it were the file on
    # disk: "Format 1024x576" for a 1280x720 video. `sidecar` is always
    # `<media>.json`, so its own stem names the real media file — probe
    # THAT and attach the answer as a sibling field the params never had a
    # reason to carry (an ffprobe on render's own output would just repeat
    # the request; this asks the file, not the memory of the request).
    # One ffprobe, only when a human opens the info modal — never on the
    # gallery's poll path.
    try:
        payload = P.json.loads(sidecar.read_bytes())
    except (ValueError, OSError):
        h._ok(sidecar.read_bytes(), "application/json")
        return
    if isinstance(payload, dict) and "actual_width" not in payload:
        media_path = sidecar.parent / sidecar.stem  # "<media>.json" -> "<media>"
        if media_path.suffix.lower() == ".mp4" and media_path.is_file():
            aw, ah = P._probe_video_dims(str(media_path))
            if aw and ah:
                payload["actual_width"] = aw
                payload["actual_height"] = ah
    h._json(payload)


@get("/library/images")
def get_library_images(h, parsed) -> None:
    # Drives the Image Studio's right-pane gallery (and any other
    # client that wants the same view as the agent's
    # list_library_images tool). Mirrors that tool's filter set
    # exactly so behavior is consistent across UI + agent.
    qs = P.parse_qs(parsed.query)
    try:
        limit = max(1, min(200, int((qs.get("limit", ["48"])[0] or "48"))))
    except ValueError:
        limit = 48
    contains = (qs.get("contains", [""])[0] or "").strip().lower()
    include_manual = (qs.get("include_manual", ["1"])[0] or "1") not in ("0", "false")
    include_agent = (qs.get("include_agent", ["1"])[0] or "1") not in ("0", "false")
    # `since` lets the modal cheaply poll for new gens after
    # generation finishes (instead of re-scanning every PNG on
    # disk). After months of agent use this dir is tens of
    # thousands of files; a since-filter knocks the per-call
    # cost down to whatever's been generated since the cursor.
    try:
        since = float((qs.get("since", ["0"])[0] or "0"))
    except ValueError:
        since = 0.0

    roots: list[P.Path] = []
    if include_agent:
        roots.append(P.UPLOADS / "agentflow")
    if include_manual:
        roots.append(P.UPLOADS / "library" / "manual")

    # Scan caps. The since-filter is the cheap fast-path for
    # incremental polls; these caps protect the cold-start case
    # (modal opened, no since cursor) where the dir already has
    # tens of thousands of files. MAX_DEPTH=5 covers the
    # agentflow/<sid>/<label>/[take_NN]/cand_NN.png shape;
    # MAX_SCAN bounds the worst case.
    MAX_DEPTH = 5
    MAX_SCAN = 5000
    scanned = 0

    def _walk_capped(start: P.Path, base_depth: int = 0):
        nonlocal scanned
        stack = [(start, base_depth)]
        while stack and scanned < MAX_SCAN:
            cur, depth = stack.pop()
            if depth > MAX_DEPTH:
                continue
            try:
                with P.os.scandir(cur) as it:
                    entries = list(it)
            except (PermissionError, FileNotFoundError, NotADirectoryError):
                continue
            for entry in entries:
                if scanned >= MAX_SCAN:
                    return
                try:
                    if entry.is_dir(follow_symlinks=False) and depth < MAX_DEPTH:
                        stack.append((P.Path(entry.path), depth + 1))
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(".png"):
                        scanned += 1
                        yield P.Path(entry.path)
                except OSError:
                    continue

    items: list[dict] = []
    for root in roots:
        if not root.exists():
            continue
        for png in _walk_capped(root):
            # Cheap pre-filter: skip files whose mtime falls
            # before `since` BEFORE doing the sidecar JSON read.
            # On a 10K-file library this turns a 3-second walk
            # into a sub-100ms one for incremental polls.
            if since:
                try:
                    mtime = png.stat().st_mtime
                except OSError:
                    continue
                if mtime <= since:
                    continue
            sidecar = png.with_suffix(png.suffix + ".json")
            meta: dict = {}
            if sidecar.is_file():
                try:
                    meta = P.json.loads(sidecar.read_text(encoding="utf-8"))
                except (OSError, P.json.JSONDecodeError):
                    meta = {}
            if "png_path" not in meta:
                meta["png_path"] = str(png)
            if "generated_at" not in meta:
                try:
                    meta["generated_at"] = png.stat().st_mtime
                except OSError:
                    meta["generated_at"] = 0
            # Apply since to the sidecar's logical generated_at
            # too (sidecar may carry a slightly different ts than
            # the file mtime — the explicit since check above
            # used mtime, this honors the canonical sidecar ts).
            if since and meta.get("generated_at", 0) <= since:
                continue
            if contains:
                p = (meta.get("prompt") or "").lower()
                if contains not in p:
                    continue
            items.append(meta)

    items.sort(key=lambda m: m.get("generated_at", 0), reverse=True)
    items = items[:limit]
    h._json({
        "count": len(items),
        "images": [{
            "png_path": m.get("png_path"),
            "prompt": m.get("prompt"),
            "refs": m.get("refs") or [],
            "engine": m.get("engine"),
            "family": m.get("family"),
            "model": m.get("model"),
            "seed": m.get("seed"),
            "width": m.get("width"),
            "height": m.get("height"),
            "generated_at": m.get("generated_at"),
            "shot_label": m.get("shot_label"),
            "session_id": m.get("session_id"),
        } for m in items],
    })


# ====== One-click delete: removes the file + its sidecar JSON
# from disk. Containment check identical to /output/hide so the
# endpoint can't be tricked into deleting arbitrary paths. Used
# by the per-card × button in the Outputs gallery.
def _reserve_trash_name(trash_dir, c, ts: str):
    """A Trash destination nobody else holds, CLAIMED before the move.

    The old rule checked only the plain name and fell back to one
    timestamp-suffixed name without checking that one: same-seed image
    batches share basenames (`cand_42_mflux.png`), so a second deletion in the
    same second — two tabs, or two batches — picked the same suffixed name
    and the move silently replaced the first trashed image (Codex UI-05).
    Each candidate is created with O_EXCL, so a concurrent request cannot pick
    it too; the move then lands on our empty placeholder."""
    names = [c.name, f"{c.stem}-{ts}{c.suffix}"]
    names += [f"{c.stem}-{ts}-{n}{c.suffix}" for n in range(2, 1000)]
    for name in names:
        dest = trash_dir / name
        if dest.exists() or dest.is_symlink():
            continue
        try:
            fd = P.os.open(str(dest), P.os.O_WRONLY | P.os.O_CREAT | P.os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        P.os.close(fd)
        return dest
    raise OSError(f"no free name in the Trash for {c.name}")


@post("/output/delete")
def post_output_delete(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    target = qs.get("path", [""])[0] or form.get("path", [""])[0]
    if not target:
        h._json({"error": "missing path"}, 400); return
    try:
        _resolved = P.Path(target).resolve()
    except OSError as e:
        h._json({"error": f"path not resolvable: {e}"}, 400); return
    try:
        _roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except OSError:
        _roots = []
    if not any(_resolved.is_relative_to(r) for r in _roots):
        h._json({
            "error": "path must resolve under outputs/ or uploads/"
        }, 400); return
    if not _resolved.is_file():
        h._json({"error": "not a file"}, 404); return
    # Move media + sibling sidecars to ~/.Trash instead of
    # hard-deleting. Users can restore from Finder Trash if
    # they fat-finger Delete on a good take. Collision-safe:
    # if a file with the same name already lives in Trash we
    # suffix with a timestamp so nothing in Trash is silently
    # overwritten.
    trash_dir = P.Path.home() / ".Trash"
    try:
        trash_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        h._json({"error": f"trash dir unavailable: {e}"}, 500); return

    # Gather every file that belongs to this clip — not just the one
    # the user clicked. After an upscale pass, the panel produces a
    # `<base>_720p.mp4` (the user-visible card) AND keeps the native
    # `<base>.mp4` on disk but hidden from the gallery (see the
    # `set_hidden(str(native_target), True)` in the upscale path).
    # The old delete code only moved the clicked file's stem +
    # sidecars, so the hidden native variant got orphaned in
    # mlx_outputs/ forever. Now we collect every companion file we
    # can find via:
    #   (a) sidecar JSON of the clicked file (raw_output /
    #       native_output / output / upscaled_output fields),
    #   (b) filename heuristic — strip the known upscale-suffix
    #       (`_720p`, `_v720p`, `_1080p`, `_v1080p`, `_up2x`) from the
    #       stem to get the raw, and append each suffix to get any
    #       companion upscaled file that exists.
    # Each candidate's matching sidecars (`<full>.json` and
    # `<stem>.json`) come along too. UPSCALE_TAGS is module-level so
    # this list and compute_upscale_plan's tags can never drift.

    def _add(seen: set, candidates: list, cand: P.Path) -> None:
        key = str(cand)
        if key in seen:
            return
        seen.add(key)
        if cand.is_file():
            candidates.append(cand)

    def _expand_for_media(media_path: P.Path,
                          seen: set, candidates: list) -> None:
        # The media file itself.
        _add(seen, candidates, media_path)
        # Sidecars next to it: <stem>.<ext>.json (canonical) and
        # <stem>.json (older shape).
        _add(seen, candidates, P.Path(str(media_path) + ".json"))
        _add(seen, candidates, media_path.with_suffix(".json"))
        # H3 writes more than a clip and a sidecar (#77, @PhantombrainM):
        # the mixed audio track (<stem>.wav), the pre-mix source audio
        # (<stem>_source.wav) and a draft's stage-A cache
        # (<stem>.stage_a.npz — np.savez appends .npz; the bare name is
        # kept for older engines). They used to stay behind and pile up.
        _add(seen, candidates, media_path.with_suffix(".wav"))
        _add(seen, candidates,
             media_path.with_name(media_path.stem + "_source.wav"))
        _add(seen, candidates, media_path.with_suffix(".stage_a.npz"))
        _add(seen, candidates, media_path.with_suffix(".stage_a"))

    def _base_stem(stem: str) -> str:
        # If this stem ends with `_720p` / `_v720p` / `_up2x`,
        # return the bare base; else return the stem unchanged.
        for tag in P.UPSCALE_TAGS:
            suf = f"_{tag}"
            if stem.endswith(suf):
                return stem[: -len(suf)]
        return stem

    seen: set = set()
    candidates: list = []

    # (a) sidecar-driven expansion. Read the sidecar of the clicked
    # file (if present) and pull every path field that points at a
    # mp4. Restrict to files under OUTPUT/UPLOADS so a tampered
    # sidecar can't trick us into trashing arbitrary files.
    roots = []
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except OSError:
        roots = []
    for sc_cand in (P.Path(str(_resolved) + ".json"),
                    _resolved.with_suffix(".json")):
        if not sc_cand.is_file():
            continue
        try:
            meta = P.json.loads(sc_cand.read_text())
        except Exception:
            continue
        for fld in ("output", "raw_output", "native_output",
                    "upscaled_output"):
            v = meta.get(fld)
            if not v:
                continue
            try:
                p_resolved = P.Path(str(v)).resolve()
            except OSError:
                continue
            if not any(p_resolved.is_relative_to(r) for r in roots):
                continue
            _expand_for_media(p_resolved, seen, candidates)
        # An Extend's lossless audio master (issue #48) — a FLAC in the
        # outputs' `.extend/masters` dot-folder that only the next extend of
        # this clip reads — goes with the clip instead of piling up.
        v = meta.get("extend_audio_master")
        if v:
            try:
                m_resolved = P.Path(str(v)).resolve()
            except OSError:
                m_resolved = None
            if m_resolved is not None and any(m_resolved.is_relative_to(r) for r in roots):
                _add(seen, candidates, m_resolved)

    # (b) filename-pattern expansion. Always include the clicked
    # file. Then derive base + every known upscale suffix and add
    # whichever variants exist on disk. This catches the case where
    # the sidecar is missing or stale.
    _expand_for_media(_resolved, seen, candidates)
    stem = _resolved.stem
    ext = _resolved.suffix  # ".mp4"
    parent = _resolved.parent
    base = _base_stem(stem)
    # Raw / native: <base><ext>
    _expand_for_media(parent / f"{base}{ext}", seen, candidates)
    # Upscaled siblings: <base>_<tag><ext> for each tag
    for tag in P.UPSCALE_TAGS:
        _expand_for_media(parent / f"{base}_{tag}{ext}",
                          seen, candidates)

    # Refuse to do nothing — if for some reason the clicked file
    # wasn't is_file() at expansion time, surface that instead of
    # silently 200ing with an empty trashed list.
    if not candidates:
        h._json({"error": "no files to delete"}, 404); return

    # A clip or picture a waiting render takes as its input (Extend, Upscale
    # & Face Fix, an Image-to-video reference picked from the gallery) stays
    # until that render is done - it used to fail at its turn instead.
    in_use = P.delete_in_use_refusal(candidates)
    if in_use:
        h._json({"error": in_use, "in_use": True}, 409); return

    trashed = []
    ts = P.time.strftime("%Y%m%d-%H%M%S")
    for c in candidates:
        try:
            dest = _reserve_trash_name(trash_dir, c, ts)
        except OSError as e:
            h._json({"error": f"move to Trash failed: {e}"}, 500); return
        try:
            P.shutil.move(str(c), str(dest))
            trashed.append(str(dest))
        except OSError as e:
            try:
                dest.unlink()          # our empty placeholder, never a user file
            except OSError:
                pass
            h._json({"error": f"move to Trash failed: {e}"}, 500); return
    # Drop every trashed-original from HIDDEN_PATHS so we don't
    # leak orphan entries into panel_hidden.json. The native
    # variant was hidden by the upscale path; the upscaled one
    # might also have been hidden if the user toggled it.
    for c in candidates:
        P.set_hidden(str(c), False)
    h._json({"ok": True, "trashed": trashed})


# ====== Reveal the outputs folder in Finder (one click, no
# per-card duplication). Runs `open <OUTPUT>` — same subprocess
# pattern used elsewhere in this file for `open -a Pinokio` and
# `open <final_out>`. macOS-native; Phosphene is Apple Silicon
# only so no cross-platform branch needed.
@post("/output/open_folder")
def post_output_open_folder(h, path, qs, ctype) -> None:
    try:
        # Reveal the folder that actually holds the user's LATEST
        # output. Videos land under OUTPUT (mlx_outputs); images land
        # under UPLOADS/library/manual/<date>/. Always opening OUTPUT
        # showed an empty Finder window to anyone whose last render
        # was an image (reported by cocktailpeanut). Find the newest
        # file across both roots and `open -R` it so Finder opens the
        # right folder with that file selected.
        roots = [P.OUTPUT, P.UPLOADS / "library" / "manual"]
        newest_file = None
        newest_mtime = -1.0
        for _root in roots:
            if not _root.is_dir():
                continue
            for _p in _root.rglob("*"):
                # Skip dotfiles and .json sidecars so `open -R` selects
                # the actual render (png/mp4), not its metadata twin.
                if (not _p.is_file() or _p.name.startswith(".")
                        or _p.suffix.lower() == ".json"):
                    continue
                try:
                    _mt = _p.stat().st_mtime
                except OSError:
                    continue
                if _mt > newest_mtime:
                    newest_mtime, newest_file = _mt, _p
        if newest_file is not None:
            P.subprocess.run(["open", "-R", str(newest_file)], check=False)
            h._json({"ok": True, "opened": str(newest_file.parent)})
        else:
            # Nothing rendered yet — open OUTPUT (create if missing).
            target_dir = P.OUTPUT.resolve()
            target_dir.mkdir(parents=True, exist_ok=True)
            P.subprocess.run(["open", str(target_dir)], check=False)
            h._json({"ok": True, "opened": str(target_dir)})
    except Exception as e:
        h._json({"error": f"open failed: {e}"}, 500)


# VC-18/37: per-clip "Reveal in Finder" — the toolbar/lightbox "More" menu
# action. `/output/open_folder` above only ever reveals the newest file
# across the whole gallery; this reveals the SPECIFIC clip the user has
# selected, wherever it lives (a plain render, a Sharp export, a face-fix
# pass, an uploaded reference — same containment rule as /image).
@post("/output/reveal")
def post_output_reveal(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    raw = (form.get("path", [""])[0] or qs.get("path", [""])[0] or "").strip()
    if not raw:
        h._json({"ok": False, "error": "no path given"}, 400); return
    try:
        target = P.Path(raw).resolve()
    except Exception:
        h._json({"ok": False, "error": "bad path"}, 400); return
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(target.is_relative_to(r) for r in roots):
        h._json({"ok": False, "error": "that path is outside the outputs/uploads folders"}, 403)
        return
    if not target.is_file():
        h._json({"ok": False, "error": "that file is not on disk any more"}, 404)
        return
    P.subprocess.run(["open", "-R", str(target)], check=False)
    h._json({"ok": True, "opened": str(target)})


# VC-18/37: "Use last frame" — extract the clip's final frame as a new
# reference image so the player can offer it straight into i2v ("continue
# from here on a fresh roll of the dice", the non-Extend way of picking up
# where a take left off). Same ffmpeg recipe One Shot's own take-to-take
# handoff already uses (`-sseof -0.05 -frames:v 1`), just pointed at
# UPLOADS instead of a private take-dir so the picker can show it.
@post("/output/last_frame")
def post_output_last_frame(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    raw = (form.get("path", [""])[0] or qs.get("path", [""])[0] or "").strip()
    if not raw:
        h._json({"ok": False, "error": "no path given"}, 400); return
    try:
        src = P.Path(raw).resolve()
    except Exception:
        h._json({"ok": False, "error": "bad path"}, 400); return
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(src.is_relative_to(r) for r in roots):
        h._json({"ok": False, "error": "that path is outside the outputs/uploads folders"}, 403)
        return
    if not src.is_file() or src.suffix.lower() != ".mp4":
        h._json({"ok": False, "error": "Use last frame works on rendered video clips."}, 400)
        return
    ff = P.shutil.which("ffmpeg")
    if not ff:
        h._json({"ok": False, "error": "ffmpeg is not available"}, 500); return
    out_dir = P.UPLOADS / "library" / "manual" / P.time.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{src.stem}_lastframe_{int(P.time.time())}.png"
    try:
        P.subprocess.run(
            [ff, "-loglevel", "error", "-y", "-sseof", "-0.05", "-i", str(src),
             "-frames:v", "1", "-update", "1", str(out_path)],
            check=True, capture_output=True, timeout=30,
        )
    except Exception as e:
        h._json({"ok": False, "error": f"could not extract the last frame: {e}"}, 500)
        return
    if not out_path.is_file() or out_path.stat().st_size == 0:
        h._json({"ok": False, "error": "ffmpeg produced no image"}, 500); return
    h._json({"ok": True, "path": str(out_path), "url": f"/image?path={P.quote(str(out_path))}"})


@post("/external/open")
def post_external_open(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    # Open an external link in the system default browser. A plain
    # target="_blank" anchor doesn't reliably open from inside Pinokio's
    # webview; `open <url>` always does. Restricted to https + an
    # allowlisted host so this loopback endpoint can't be coerced into
    # an arbitrary-URL / file opener.
    url = (form.get("url", [""])[0] or "").strip()
    _ALLOWED_HOSTS = {
        "huggingface.co", "civitai.com", "github.com",
        "ideogram.ai", "pinokio.co", "pinokio.computer",
        # The Docs live on the website (owner, 1 Nov 2026).
        "phosphene.video",
        # Phosphene Remote's App Store page (the review nudge, 12 Nov 2026).
        "apps.apple.com",
    }
    # The one non-https link: Phosphene Remote's write-a-review sheet in the
    # App Store app, exactly this URL. If nothing handles itms-apps, the same
    # sheet on the web (review nudge, owner 12 Nov 2026).
    if url == P.REMOTE_REVIEW_APP_URL:
        try:
            r = P.subprocess.run(["open", url], check=False, timeout=5)
            if r.returncode != 0:
                P.subprocess.run(["open", P.REMOTE_REVIEW_WEB_URL], check=False, timeout=5)
                h._json({"ok": True, "opened": P.REMOTE_REVIEW_WEB_URL})
                return
            h._json({"ok": True, "opened": url})
        except Exception as e:
            h._json({"error": f"open failed: {e}"}, 500)
        return
    try:
        _pp = P.urllib.parse.urlparse(url)
        _host = (_pp.netloc or "").lower().split("@")[-1].split(":")[0]
        if _host.startswith("www."):
            _host = _host[4:]
        if _pp.scheme != "https" or _host not in _ALLOWED_HOSTS:
            h._json({"error": f"refused (https + allowlisted host only): {url!r}"}, 400)
            return
        P.subprocess.run(["open", url], check=False, timeout=5)
        h._json({"ok": True, "opened": url})
    except Exception as e:
        h._json({"error": f"open failed: {e}"}, 500)


@post("/output/hide")
def post_output_hide(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    target = qs.get("path", [""])[0] or form.get("path", [""])[0]
    if not target:
        h._json({"error": "missing path"}, 400); return
    # M6: require the path to resolve under OUTPUT or UPLOADS
    # before persisting it into HIDDEN_PATHS / panel_hidden.json.
    # Without this, an attacker who beats the loopback/Origin
    # check (or a buggy first-party caller) could fill the
    # hidden-list with arbitrary strings, slowly polluting
    # persistent state. Same containment shape as
    # `agent/tools.py:_ensure_under` used for submit_shot. See
    # security-review.md §M6.
    try:
        _resolved = P.Path(target).resolve()
    except OSError as e:
        h._json({"error": f"path not resolvable: {e}",
                    "rejected": [target]}, 400)
        return
    try:
        _roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except OSError:
        _roots = []
    if not any(_resolved.is_relative_to(r) for r in _roots):
        h._json({"error": (
            "path must resolve under outputs/ or uploads/"
        ), "rejected": [target]}, 400)
        return
    P.set_hidden(str(_resolved), True)
    h._json({"hidden": str(_resolved)})


@post("/output/show")
def post_output_show(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    target = qs.get("path", [""])[0] or form.get("path", [""])[0]
    if target:
        P.set_hidden(target, False); h._json({"shown": target})
    else:
        h._json({"error": "missing path"}, 400)


@post("/output/show_all")
def post_output_show_all(h, path, qs, ctype) -> None:
    with P.LOCK:
        count = len(P.HIDDEN_PATHS)
        P.HIDDEN_PATHS.clear()
    P.persist_hidden()
    h._json({"unhidden_count": count}); return


# Multipart upload
@post("/upload")
def post_upload(h, path, qs, ctype) -> None:
    # The chain arm carried this condition; its failure fell
    # through to the chain end, which answers 404.
    if not (ctype.startswith("multipart/form-data")):
        h.send_error(404)
        return
    # Hard cap on body size so a misbehaving / malicious caller can't
    # spool a multi-GB file into memory via cgi.FieldStorage. 64 MB
    # comfortably covers any reasonable still-image reference; multipart
    # framing adds a small overhead so we read the declared length.
    MAX_UPLOAD_BYTES = 64 * 1024 * 1024
    try:
        clen = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        clen = 0
    if clen <= 0:
        h._json({"error": "Content-Length required"}, 411); return
    if clen > MAX_UPLOAD_BYTES:
        h._json({"error": f"upload too large (max {MAX_UPLOAD_BYTES} bytes)"}, 413)
        return
    try:
        form = P._parse_multipart_form(h.rfile, ctype, clen)
        # Accept either field name — the endpoint is generic
        # (reference images for i2v, audio clips for i2v_clean_audio,
        # whatever). Originally `image` only; `audio` added 2026-05-16
        # for Characters-tab i2v_clean_audio uploads.
        field_name = "image" if "image" in form else ("audio" if "audio" in form else None)
        if field_name is None:
            h._json({"error": "no field 'image' or 'audio'"}, 400); return
        fld = form[field_name]
        if not getattr(fld, "filename", None):
            h._json({"error": "no filename"}, 400); return
        P.UPLOADS.mkdir(parents=True, exist_ok=True)
        raw = fld.file.read()
        _orig = P.Path(fld.filename)
        out_ext = _orig.suffix
        # SYS-01/SYS-02: normalize every picked IMAGE (not the audio field —
        # i2v_clean_audio's external track shares this endpoint) once, here,
        # before it ever reaches a picker preview, a render, or a thumbnail.
        # EXIF orientation is applied and stripped; HEIC/HEIF/AVIF (what
        # AirDrop/Photos hand a Mac by default, and what Pillow cannot open
        # at all) is converted to JPEG. A failure here — a genuinely corrupt
        # file, or HEIC on a Mac without sips — refuses the upload with a
        # clear reason instead of writing bytes the render will die on 30 s
        # into a helper's model load.
        if field_name == "image":
            raw, out_ext, err = P.normalize_ingested_image_bytes(raw, fld.filename)
            if err:
                h._json({"error": err}, 400); return
        # SYS-26: this used to be `[^A-Za-z0-9._-]+ -> "_"`, ASCII-only, so
        # テスト画像_猫.jpg landed as ____.jpg and "Recent uploads" listed
        # it the same way — unsearchable, indistinguishable from every
        # other erased name. _unicode_slug keeps any script's letters and
        # digits; the extension is kept separately (conventionally ASCII,
        # and matters for the server's own extension checks elsewhere).
        # The extension is the NORMALIZED one (a HEIC converted above is
        # written as .jpg), not the name the file arrived with.
        _stem = P._unicode_slug(_orig.stem, max_chars=120, fallback="upload")
        _ext = P.re.sub(r"[^A-Za-z0-9.]", "", out_ext or "")[:10]
        safe_name = f"{_stem}{_ext}"
        # Cap the FULL name (this slug + the millisecond timestamp prefix
        # below) at 200 bytes, well under the 255-byte filesystem limit on
        # APFS — a multi-byte-per-character script (CJK, emoji) can blow
        # past a character-count cap in byte terms even at a modest
        # character count.
        while len(safe_name.encode("utf-8")) > 200 and _stem:
            _stem = _stem[:-1]
            safe_name = f"{_stem}{_ext}"
        dest = P.UPLOADS / f"{int(P.time.time()*1000)}_{safe_name}"
        dest.write_bytes(raw)
        resp = {"ok": True, "path": str(dest)}
        # Audio duration (VA-03): the A2V form's Duration slider and Audio
        # start have always been blind to the length of what was just
        # dropped — an uploaded 4s vocal silently defaulted to a 7s render,
        # 43% of it silence rendered against nothing (exactly where a
        # lip-sync mouth freezes). The client already reads duration_sec
        # when present (AUDIO_STUDIO.audioDuration in characters.js); this
        # is the half of that contract the server never filled in.
        if field_name == "audio":
            dur = P.probe_media_duration(dest)
            if dur is not None:
                resp["duration_sec"] = dur
        h._json(resp)
    except Exception as exc:
        h._json({"error": f"upload failed: {exc}"}, 500)


@get_when(lambda p: p.startswith("/assets/"))
def get_asset(h, parsed) -> None:
    # Serve files from <ROOT>/assets/ (creator avatar, future static).
    # Path-bound to that directory only — no traversal.
    rel = parsed.path[len("/assets/"):]
    assets_dir = (P.ROOT / "assets").resolve()
    try:
        path = (assets_dir / rel).resolve()
    except Exception:
        h.send_error(400); return
    if not path.is_relative_to(assets_dir) or not path.is_file():
        h.send_error(404); return
    ext = path.suffix.lower()
    ctype = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".gif": "image/gif", ".svg": "image/svg+xml",
        ".mp4": "video/mp4", ".json": "application/json",
    }.get(ext, "application/octet-stream")
    if ext == ".mp4":
        # WKWebView's <video> needs byte ranges (the Remote page's film).
        h._serve_video_with_range(path)
        return
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(path.stat().st_size))
    h.send_header("Cache-Control", "public, max-age=86400")
    h.end_headers()
    h.wfile.write(path.read_bytes())


@get_when(lambda p: p.startswith("/webapp/"))
def get_webapp_file(h, parsed) -> None:
    # Frontend files extracted out of this file's embedded page —
    # today webapp/style/panel.css, later the markup and JS modules
    # (docs/ARCHITECTURE.md). Path-bound to <ROOT>/webapp like
    # /assets. no-cache, not max-age: these files change on every
    # git pull under a running panel and carry no cache-bust token.
    rel = parsed.path[len("/webapp/"):]
    webapp_dir = (P.ROOT / "webapp").resolve()
    try:
        path = (webapp_dir / rel).resolve()
    except Exception:
        h.send_error(400); return
    if not path.is_relative_to(webapp_dir) or not path.is_file():
        h.send_error(404); return
    ext = path.suffix.lower()
    ctype = {
        ".css": "text/css; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".mjs": "text/javascript; charset=utf-8",
        ".html": "text/html; charset=utf-8",
        ".svg": "image/svg+xml",
        ".woff2": "font/woff2",
        # The Docs pages (webapp/docs/*.md), rendered in the browser.
        ".md": "text/markdown; charset=utf-8",
    }.get(ext, "application/octet-stream")
    # Production serves the bytes this process booted with (snapshot taken
    # beside HTML at import) so a pull under a running panel can't pair new
    # modules with old markup; dev reads from disk for hard-refresh edits.
    _snap = getattr(P, "_WEBAPP_SNAPSHOT", None) or {}
    body = _snap.get(rel) if rel in _snap else path.read_bytes()
    if ext == ".css":
        # The one dynamic seam in the stylesheet: per-engine accent +
        # fold rules are emitted from the ENGINES registry, exactly as
        # page() substituted them when the CSS was embedded. The
        # registry stays single-source server-side.
        body = body.replace(b"__ENGINE_RULES__",
                            P._engine_css().encode("utf-8"))
    elif ext == ".md" and P._dist.is_mac_app():
        # Mac app: a Docs page's [[pinokio]] passages (Spicy mode) never leave
        # the server (owner ruling 2026-10-22).
        body = P.drop_pinokio_doc_blocks(body.decode("utf-8", "replace")).encode("utf-8")
    elif ext == ".html" and P._dist.is_mac_app():
        body = P._strip_pinokio_only(body.decode("utf-8", "replace")).encode("utf-8")
    elif ext in (".js", ".mjs"):
        # The editor's user-facing strings carry the __SEQ__ noun
        # seams. page() substitutes them in the page; module files
        # are served raw, so the same substitution — same values,
        # same order (SEQCAP before SEQS before SEQ, since the
        # longer tokens contain the shorter) — happens here.
        body = (body.replace(b"__SEQCAP__", P.SEQ_NOUN_CAP.encode())
                    .replace(b"__SEQS__", P.SEQ_NOUN_PL.encode())
                    .replace(b"__SEQ__", P.SEQ_NOUN.encode()))
    h.send_response(200)
    h.send_header("Content-Type", ctype)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Cache-Control", "no-cache")
    h.end_headers()
    try: h.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError): pass
