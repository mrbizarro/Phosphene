"""/image family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@get("/image/agent/schema")
def get_image_agent_schema(h, parsed) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    # Self-serve contract for an LLM agent driving Ideogram 4 layout
    # via POST /image/agent. An agent that reads ONLY this response
    # should be able to compose a valid request: it documents the
    # box model (fractions, top-left origin), every field + its
    # allowed values, the caption rules we enforce, two complete
    # worked examples, and how wait/validate_only behave. No secrets.
    h._json({
        "version": "1.0",
        "description": (
            "Compose an Ideogram 4 image — scene, on-image text, object "
            "regions, styles, colors — by describing it in plain terms. "
            "POST your spec to /image/agent. The server translates it into "
            "Ideogram 4's strict internal caption for you; you never write "
            "that caption by hand. Coordinates are FRACTIONS of the frame "
            "with a top-left origin: x,y is the top-left corner of a box, "
            "w,h its size, each in [0,1]. (0,0)=top-left, (1,1)=bottom-right. "
            "Coordinates are aspect-independent — the same box lands in the "
            "same relative spot at any aspect ratio."
        ),
        "endpoint": {
            "method": "POST",
            "path": "/image/agent",
            "content_type": "application/json",
        },
        "spec_schema": {
            "scene": {
                "type": "string", "required": True,
                "doc": "Overall background / setting / mood of the whole image. "
                       "This becomes the caption background — describe the world, "
                       "not the text. e.g. 'A moody vintage travel poster of a "
                       "mountain lake at golden hour, warm muted palette'.",
            },
            "boxes": {
                "type": "array", "required": True,
                "doc": "List of placed elements (text and/or objects). May be "
                       "empty for a pure-scene render. Each item is a box object "
                       "(see box_schema). Max 6 TEXT boxes; object boxes are "
                       "uncapped but keep layouts legible.",
                "item": "box",
            },
            "render": {
                "type": "string", "required": False, "default": "design",
                "enum": list(P.ideogram_caption.VALID_RENDER),
                "doc": "'design' = graphic/poster/vector look (strong typography). "
                       "'photo' = photographic look. Affects the high-level "
                       "description the model receives.",
            },
            "aspect": {
                "type": "string", "required": False, "default": "16:9",
                "enum": list(P.ideogram_caption.VALID_ASPECT),
                "doc": "Frame aspect ratio. 16:9=1280x720, 1:1=1024x1024, "
                       "9:16=720x1280, 4:3=1024x768, 3:4=768x1024, 21:9=1280x544.",
            },
            "quality": {
                "type": "string", "required": False, "default": "turbo",
                "enum": list(P.ideogram_caption.VALID_QUALITY),
                "doc": "Sampler effort. 'turbo' (12 steps, fastest), "
                       "'default' (20 steps), 'quality' (48 steps, slowest/best).",
            },
            "n": {
                "type": "integer", "required": False, "default": 1,
                "min": 1, "max": 4,
                "doc": "How many candidate images to render (1-4).",
            },
            "seed": {
                "type": "integer", "required": False, "default": -1,
                "doc": "Fixed seed for reproducibility; -1 (or omit) = random. "
                       "For n>1 the server uses seed, seed+1, ... per candidate.",
            },
            "validate_only": {
                "type": "boolean", "required": False, "default": False,
                "doc": "If true, do NOT render — return the built caption + any "
                       "warnings so you can inspect/iterate cheaply.",
            },
            "wait": {
                "type": "boolean", "required": False, "default": True,
                "doc": "If true (default), block until the render finishes and "
                       "return image paths/urls. If false, enqueue and return "
                       "immediately with queued:true.",
            },
            "box_schema": {
                "type": {
                    "type": "string", "required": True,
                    "enum": list(P.ideogram_caption.VALID_TYPES),
                    "doc": "'text' renders literal words; 'object' renders a "
                           "described thing in that region.",
                },
                "x": {"type": "number", "required": True, "min": 0, "max": 1,
                      "doc": "Left edge as a fraction of frame width (0=left)."},
                "y": {"type": "number", "required": True, "min": 0, "max": 1,
                      "doc": "Top edge as a fraction of frame height (0=top)."},
                "w": {"type": "number", "required": True, "min": 0, "max": 1,
                      "doc": "Width as a fraction of frame width. x+w must be <= 1."},
                "h": {"type": "number", "required": True, "min": 0, "max": 1,
                      "doc": "Height as a fraction of frame height. y+h must be <= 1."},
                "text": {"type": "string", "required": "for type=text",
                         "doc": "The literal words to render. Required for text "
                                "boxes; empty text boxes are dropped."},
                "desc": {"type": "string", "required": "for type=object",
                         "doc": "What to render. REQUIRED for object boxes. "
                                "Optional for text boxes — when given it overrides "
                                "the auto-generated style/align/color description."},
                "style": {"type": "string", "required": False, "default": "headline",
                          "enum": list(P.ideogram_caption.VALID_STYLES),
                          "doc": "Text-only. Typographic feel: headline/subhead/"
                                 "body/caps/script/serif."},
                "align": {"type": "string", "required": False, "default": "center",
                          "enum": list(P.ideogram_caption.VALID_ALIGN),
                          "doc": "Text-only. left/center/right."},
                "color": {"type": "string", "required": False, "default": "#FFFFFF",
                          "doc": "Text-only. Hex #RRGGBB (any case; normalized to "
                                 "uppercase). Bad values fall back to #FFFFFF."},
            },
        },
        "caption_rules_summary": [
            "Coordinates are fractions in [0,1] with a top-left origin; "
            "internally each box becomes a bbox [y_min,x_min,y_max,x_max] of "
            "row-first integers in 0..1000 (you never write this yourself).",
            "At most 6 text boxes. Object boxes carry a required desc.",
            "Each text box gets one color (#RRGGBB, uppercased). Boxes that "
            "extend past the frame edge (x+w>1 or y+h>1) are clamped — a warning "
            "is returned.",
            "Heavily overlapping boxes (>40% of the smaller box) get a warning "
            "but still render.",
            "Empty text boxes are silently dropped from the final caption.",
        ],
        "examples": [
            {
                "name": "16:9 poster — 2 text + 1 object",
                "request": {
                    "scene": "A moody vintage travel poster of a mountain lake at "
                             "golden hour, warm muted palette",
                    "render": "design",
                    "aspect": "16:9",
                    "quality": "turbo",
                    "n": 1,
                    "boxes": [
                        {"type": "text", "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.18,
                         "text": "LAKE DISTRICT", "style": "headline",
                         "align": "center", "color": "#F5C518"},
                        {"type": "text", "x": 0.30, "y": 0.80, "w": 0.40, "h": 0.10,
                         "text": "Est. 1951", "style": "serif",
                         "align": "center", "color": "#FFFFFF"},
                        {"type": "object", "x": 0.55, "y": 0.32, "w": 0.35, "h": 0.42,
                         "desc": "A small wooden canoe drifting on the still lake"},
                    ],
                },
                "built_caption": P.ideogram_caption.build_caption({
                    "scene": "A moody vintage travel poster of a mountain lake at "
                             "golden hour, warm muted palette",
                    "render": "design",
                    "boxes": [
                        {"type": "text", "x": 0.08, "y": 0.08, "w": 0.84, "h": 0.18,
                         "text": "LAKE DISTRICT", "style": "headline",
                         "align": "center", "color": "#F5C518"},
                        {"type": "text", "x": 0.30, "y": 0.80, "w": 0.40, "h": 0.10,
                         "text": "Est. 1951", "style": "serif",
                         "align": "center", "color": "#FFFFFF"},
                        {"type": "object", "x": 0.55, "y": 0.32, "w": 0.35, "h": 0.42,
                         "desc": "A small wooden canoe drifting on the still lake"},
                    ],
                }),
            },
            {
                "name": "9:16 label — single headline on a clean background",
                "request": {
                    "scene": "A minimalist product label, soft off-white paper "
                             "texture, centered composition",
                    "render": "design",
                    "aspect": "9:16",
                    "quality": "default",
                    "n": 1,
                    "boxes": [
                        {"type": "text", "x": 0.10, "y": 0.42, "w": 0.80, "h": 0.16,
                         "text": "COLD BREW", "style": "caps",
                         "align": "center", "color": "#0A0A0A"},
                    ],
                },
                "built_caption": P.ideogram_caption.build_caption({
                    "scene": "A minimalist product label, soft off-white paper "
                             "texture, centered composition",
                    "render": "design",
                    "boxes": [
                        {"type": "text", "x": 0.10, "y": 0.42, "w": 0.80, "h": 0.16,
                         "text": "COLD BREW", "style": "caps",
                         "align": "center", "color": "#0A0A0A"},
                    ],
                }),
            },
        ],
        "usage_notes": {
            "validate_only": "POST with validate_only:true to get {ok, caption, "
                             "issues} back without rendering — use it to iterate on "
                             "a layout for free. issues is a list of human-readable "
                             "warnings; an empty list means the spec is clean.",
            "wait_true": "Default. The request blocks until the render completes "
                         "(or fails/times out) and returns {ok, caption, images:"
                         "[{path,url}], seconds}. Open each url on this same host to "
                         "view the PNG.",
            "wait_false": "Returns 202 {ok, queued:true, caption, where_results_land} "
                          "immediately; the job runs on the panel's queue and the "
                          "results appear in the panel's Recent tab / library.",
            "busy": "If the GPU is busy with another render or training job, a "
                    "wait:true request waits in the queue. Renders are serialized; "
                    "expect to wait behind any in-flight job.",
            "render_times_per_image": {
                "turbo": "~12-step sampler; fastest. Seconds-to-low-minutes per "
                         "image after a one-time model load (first call also pays "
                         "the cold model load).",
                "default": "~20-step sampler; moderate.",
                "quality": "~48-step sampler; slowest, highest fidelity.",
                "note": "Wall time = one-time model cold-load + n × per-image "
                        "sampler time. The first Ideogram render after panel start "
                        "is slower because the weights load once.",
            },
            "timeout": "A wait:true render gives up after 25 minutes and returns an "
                       "error; the job may still complete on the queue.",
        },
    })


@get("/image/engine_status")
def get_image_engine_status(h, parsed) -> None:
    # Per-engine cache + wall-time data for the Image Studio's status
    # pill + Generate button label. The Studio polls this on entry +
    # whenever the user picks a different engine so the user can see
    # at a glance whether the chosen engine's weights are local or
    # need a fresh download. Bundled-mflux engines (flux2-klein-4b,
    # flux2-klein-base-4b) ship inside the panel venv — flagged
    # "ready" without a cache check.
    #
    # Per-image seconds are best-effort defaults pulled from the
    # engine option labels in the Studio dropdown so the wall-time
    # estimate matches the user-facing copy.
    try:
        # (engine_override, repo_id_or_none, est_dl_gb, sec_per_image,
        #  cold_start_sec). sec_per_image is the steady-state denoise
        # PER candidate. cold_start_sec is the one-time subprocess
        # model-load that's paid ONCE per batch, regardless of n.
        # The JS estimator does: total = cold_start + n × sec_per_image.
        #
        # All numbers anchored to measured wall-time on M4 Max @ 1024
        # for mflux engines, 2560×1440 for HiDream. mflux subprocess
        # cold loads are not negligible: Qwen-Edit Q6 = ~50s,
        # Qwen-Edit Q8 = ~60s, Flux2 family ~30s, Z-Image-Turbo ~12s.
        # The previous table folded loads into sec_per_image, which
        # made N=1 understate and N=4+ overstate the wall-time. The
        # studio now estimates correctly across all batch sizes.
        ENGINES = [
            # Qwen-Image-Edit-2511 — three-tier ladder. ~24 GB
            # download (one-time) shared across all three tiers.
            # ALL tiers use FBCache via the mflux patch — at 8+
            # steps we measured 1.39x speedup (243s -> 175s);
            # at 4-step Lightning the threshold usually keeps
            # caching off, so the number is conservative.
            ("qwen_edit_lightning_inline", "Qwen/Qwen-Image-Edit-2511", 24.0,  35.0,  50.0),
            ("qwen_edit_inline",           "Qwen/Qwen-Image-Edit-2511", 24.0,  75.0,  50.0),
            ("qwen_edit_high_inline",      "Qwen/Qwen-Image-Edit-2511", 24.0, 170.0,  60.0),
            # Ideogram 4 fp8 — ~28 GB gated download. sec_per_image is
            # the default V4_DEFAULT_20 (20-step) baseline; the canvas
            # Quality dropdown can switch to V4_TURBO_12 (faster) or
            # V4_QUALITY_48 (slower) but the static estimate stays on
            # the default tier until observed timings h-correct it.
            ("ideogram4_inline",           P.IDEOGRAM_REPO_UNGATED,        28.0, 150.0,  60.0),
            # HiDream lab subprocess: ~45s cold load (BF16 weights →
            # MLX) per batch. Denoise times measured from the May 2026
            # step+FBCache bench at HD 2560×1440.
            ("hidream_fast_inline",        None,                         0.0,  45.0,  45.0),
            ("hidream_inline",             None,                         0.0,  80.0,  45.0),
            ("hidream_quality_inline",     None,                         0.0, 120.0,  45.0),
            ("mock_inline",                None,                         0.0,   0.5,   0.0),
        ]
        # Map engine_override → mflux family (for install-gate
        # check). HiDream lives outside mflux; we surface its
        # install status via the lab-model existence check below.
        ENGINE_FAMILY = {
            "qwen_edit_lightning_inline": "qwen_edit",
            "qwen_edit_inline":           "qwen_edit",
            "qwen_edit_high_inline":      "qwen_edit",
            "ideogram4_inline":           "ideogram",
        }
        out = []
        for engine, repo, dl_gb, sec, cold in ENGINES:
            family_installed = True
            partial = None
            if engine in ("hidream_inline", "hidream_fast_inline", "hidream_quality_inline"):
                # HiDream lives outside the HF cache (lab venv path).
                # All three modes share the same Dev-BF16 model dir.
                cached = (P.agent_image_engine.HIDREAM_DEFAULT_MODEL / "model.safetensors").exists() and \
                         (P.agent_image_engine.HIDREAM_DEFAULT_MODEL / "extras" / "custom_heads.safetensors").exists()
            elif repo is None:
                cached = True
            else:
                _snap = P._repo_hf_cache_dir(repo)
                cached = _snap is not None
                # An interrupted download is NOT cached (#73): a snapshot
                # with `.incomplete` blobs beside it looked ready here,
                # and the render died in mflux's loader. The pill says
                # "resume" and the render's pre-flight resumes it.
                partial = P.agent_image_engine.hf_repo_partial_download(repo)
                if partial:
                    cached = False
                # Ideogram: count EITHER the un-gated mirror OR an existing
                # official download, and require a real weight file (a
                # gated stub snapshot has only README/LICENSE).
                if engine == "ideogram4_inline":
                    cached = P._ideogram_any_cached()
                # mflux engines additionally need their per-family
                # binary on disk (e.g. mflux-generate-qwen-edit).
                # Issue #12 (sureshkpiitk): the Image Studio let
                # users submit jobs that couldn't possibly run
                # because the Qwen-Image-Edit add-on hadn't been
                # installed via Pinokio. Surfacing the gate here so
                # the pill + Generate button can refuse upfront.
                fam = ENGINE_FAMILY.get(engine)
                if fam:
                    probe = P.agent_image_engine.ImageEngineConfig(
                        kind="mflux", mflux_family=fam,
                    )
                    family_installed = bool(
                        P.agent_image_engine._resolve_mflux_bin(probe)
                    )
            # Adaptive override: if we've observed actual gens on
            # this engine, replace the static baseline with the
            # mean of recent (elapsed - cold) / n. After ≥2 samples
            # the estimate h-corrects for whatever rig the user
            # is actually on. The baseline tuple values stay as
            # the cold-start fallback for the very first gen.
            observed = P._IMG_ENGINE_TIMING.get(engine) or []
            samples = "static"
            if len(observed) >= 2:
                per_image = [
                    max(1.0, (el - cold) / max(1, batch_n))
                    for (batch_n, el) in observed
                ]
                sec = sum(per_image) / len(per_image)
                samples = f"observed-{len(observed)}"
            # Memory verdict (review 2026-09-02): the Studio offered 8 GB
            # and 24 GB Macs engines that hold ~24 GB of weights, and the
            # render's pre-flight refused every time. Say it up front.
            try:
                _need = P._preflight_estimate_ram_gb(
                    P._build_image_engine_config(engine))
            except Exception:                              # noqa: BLE001
                _need = 8.0
            _fits, _mac_gb = P._image_engine_fit(_need)
            out.append({
                "engine": engine,
                "repo_id": repo or "",
                "cached": cached,
                "ram_need_gb": round(float(_need), 1),
                "mac_gb_needed": int(_mac_gb),
                "fits_mac": bool(_fits),
                "partial": bool(partial),
                "partial_gb": (partial["on_disk_gb"] if partial else 0),
                "family_installed": family_installed,
                "download_gb": dl_gb,
                "sec_per_image": round(sec, 1),
                "cold_start_sec": cold,
                "samples_source": samples,
                # Ideogram 4 now pulls an un-gated mirror — no token, no
                # license click. gated stays False; the UI just shows a
                # one-time-download heads-up until the weights land.
                "gated": False,
                "license_url": ("https://huggingface.co/" + repo)
                               if (repo and engine == "ideogram4_inline") else None,
            })
        h._json({"engines": out,
                 "host_ram_gb": int(round(float(P.SYSTEM_RAM_GB or 0)))})
    except Exception as exc:                                # noqa: BLE001
        h._json({"error": f"engine_status failed: {exc}"}, 500)


@get("/agent/image/config")
def get_agent_image_config(h, parsed) -> None:
    cfg = P._load_agent_image_config()
    ok, msg = P.agent_image_engine.health_check(cfg)
    # Per-family install status — exposes which mflux-generate-*
    # binaries are present so the browser can show install hints
    # next to options the user hasn't installed yet (e.g.
    # Qwen-Image-Edit-2511). Probing all families on every config
    # fetch is cheap (a few stat() calls).
    family_status = {}
    for fam in P.agent_image_engine.MFLUX_FAMILY_BIN.keys():
        probe_cfg = P.agent_image_engine.ImageEngineConfig(
            kind="mflux", mflux_family=fam,
        )
        family_status[fam] = bool(P.agent_image_engine._resolve_mflux_bin(probe_cfg))
    h._json({
        "image_engine": cfg.to_public_dict(),
        "ok": ok,
        "message": msg,
        "family_status": family_status,
    })


# ====== Hailuo H3 Turbo — install the runner-layout adapter on demand.
# This currently fails closed with the exact publication requirement;
# _h3_install_turbo must not fetch a raw LightX2V file as a substitute.
@post("/h3/turbo/install")
def post_h3_turbo_install(h, path, qs, ctype) -> None:
    P.h3_status_invalidate()   # the 3 s /status memo must not outlive an install action
    result = P._h3_install_turbo(P.push)
    if not result.get("ok"):
        h._json(result, 409 if "active" in result.get("error", "") else 400)
        return
    h._json(result, 202)


# ====== Hailuo H3 Draft · fast 3-step — fetch TaoMate's 3-step adapter
# (Kijai's 182 MB conversion, pinned revision + sha256, resumable).
@post("/h3/tristep/install")
def post_h3_tristep_install(h, path, qs, ctype) -> None:
    P.h3_status_invalidate()
    result = P._h3_install_tristep(P.push)
    if not result.get("ok"):
        h._json(result, 409 if "active" in result.get("error", "") else 400)
        return
    h._json(result, 202)


# Image-engine config (pluggable: mock | bfl).
@post("/agent/image/config")
def post_agent_image_config(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    try:
        payload = (P.json.loads(body or "{}")
                   if ctype.startswith("application/json")
                   else {k: v[0] if v else "" for k, v in form.items()})
    except P.json.JSONDecodeError as e:
        h._json({"error": f"bad JSON: {e}"}, 400); return
    try:
        cfg = P._save_agent_image_config(payload)
    except ValueError as e:
        h._json({"error": str(e)}, 400); return
    P.push(f"agent: image engine updated to {cfg.kind}"
         + (f" ({cfg.bfl_model})" if cfg.kind == "bfl" else ""))
    ok, msg = P.agent_image_engine.health_check(cfg)
    h._json({"ok": True, "image_engine": cfg.to_public_dict(),
                "health_ok": ok, "health_message": msg})


# ===== Agent-facing Ideogram 4 layout endpoint =====================
# POST /image/agent — an LLM agent describes a composition in plain
# terms (scene + fractional text/object boxes) and we translate it
# into Ideogram 4's strict internal caption, then render it through
# the SAME queue path the browser UI uses (engine_override=
# ideogram4_inline, prompt = the JSON caption). GET /image/agent/schema
# is the self-serve contract an agent reads first. Lives before the
# urlencoded parsing because the body is JSON. NEVER echoes secrets.
@post("/image/agent")
def post_image_agent(h, path, qs, ctype) -> None:
    # The chain arm carried this condition; its failure fell
    # through to the chain end, which answers 404.
    if not (ctype.startswith("application/json")):
        h.send_error(404)
        return
    try:
        length = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        h._json({"ok": False, "error": "invalid Content-Length"}, 400); return
    if length <= 0:
        h._json({"ok": False, "error": "Content-Length required for JSON body"}, 411); return
    MAX_AGENT_JSON = 1 * 1024 * 1024
    if length > MAX_AGENT_JSON:
        h._json({"ok": False, "error": f"body too large (max {MAX_AGENT_JSON} bytes)"}, 413); return
    try:
        spec = P.json.loads(h.rfile.read(length).decode() or "{}")
    except (P.json.JSONDecodeError, UnicodeDecodeError):
        h._json({"ok": False, "error": "invalid JSON body"}, 400); return
    if not isinstance(spec, dict):
        h._json({"ok": False, "error": "body must be a JSON object (the spec)"}, 400); return

    # ---- option fields (with HTTP-layer enum/range enforcement) ----
    render = spec.get("render", "design")
    aspect = spec.get("aspect", "16:9")
    quality = spec.get("quality", "turbo")
    wait = spec.get("wait", True)
    validate_only = bool(spec.get("validate_only", False))
    try:
        n = int(spec.get("n", 1))
    except (TypeError, ValueError):
        n = -1
    try:
        seed = int(spec.get("seed", -1))
    except (TypeError, ValueError):
        seed = -1

    # Fatal, request-shape errors (400) — these block before we even
    # build a caption. Distinct from spec WARNINGS (returned in issues).
    shape_errors: list[str] = []
    if render not in P.ideogram_caption.VALID_RENDER:
        shape_errors.append(f"render must be one of {list(P.ideogram_caption.VALID_RENDER)}")
    if aspect not in P.ideogram_caption.VALID_ASPECT:
        shape_errors.append(f"aspect must be one of {list(P.ideogram_caption.VALID_ASPECT)}")
    if quality not in P.ideogram_caption.VALID_QUALITY:
        shape_errors.append(f"quality must be one of {list(P.ideogram_caption.VALID_QUALITY)}")
    if not isinstance(n, int) or n < 1 or n > 4:
        shape_errors.append("n must be an integer in 1..4")
    if not isinstance(spec.get("boxes", []), list):
        shape_errors.append("boxes must be a list")

    # Content validation — warnings + harder problems, human-readable.
    issues = P.ideogram_caption.validate_spec(spec)

    # Which issues are FATAL (the model could not produce a usable
    # render)? Off-frame clamps, overlaps and bad-color fallbacks are
    # tolerable warnings; the rest (no scene, object w/o desc, bad
    # type, out-of-range/missing coords, >6 text boxes) are fatal.
    def _is_warning(msg: str) -> bool:
        return (
            "extends past the" in msg
            or "overlap" in msg
            or "default to #FFFFFF" in msg
            or "it will be dropped from the caption" in msg
        )
    fatal_issues = [m for m in issues if not _is_warning(m)]

    # Build the caption regardless (so validate_only can show it even
    # when warnings exist); guarded so a malformed box can't 500.
    try:
        caption = P.ideogram_caption.build_caption(spec)
    except Exception as exc:  # noqa: BLE001
        h._json({"ok": False, "issues": [f"could not build caption: {exc}"] + issues}, 400)
        return

    # A render needs at least one element that survives into the
    # caption (empty boxes => pure-scene is allowed, but an all-empty-
    # text spec with no objects renders nothing meaningful).
    elements = caption.get("compositional_deconstruction", {}).get("elements", [])
    boxes_in = spec.get("boxes", []) if isinstance(spec.get("boxes", []), list) else []
    if boxes_in and not elements:
        fatal_issues.append("no renderable elements — every box was empty or invalid")

    if shape_errors or fatal_issues:
        h._json({"ok": False, "issues": shape_errors + fatal_issues}, 400)
        return

    if validate_only:
        # No render — hand back the caption + any remaining warnings.
        h._json({"ok": True, "caption": caption, "issues": issues})
        return

    # ---- submit through the SAME queue path the UI uses ----
    # Build the exact form make_job()'s mode=image branch consumes,
    # then enqueue like /queue/add. The worker serializes GPU work
    # (holds _GPU_LOCK for the whole job) and routes mode=image to
    # run_image_job_inner — identical to a browser submit, so the
    # render also shows up in the panel's Now/Queue/Recent surfaces.
    prompt_json = P.json.dumps(caption, ensure_ascii=False)
    form = {
        "mode": "image",
        "prompt": prompt_json,
        "engine_override": "ideogram4_inline",
        "ideo_preset": P.ideogram_caption.QUALITY_PRESET[quality],
        "aspect": aspect,
        "n": str(n),
        "seed": str(seed),
        "refs": "[]",
        "session_tag": "agent",
    }
    try:
        job = P.make_job(form)
    except Exception as exc:  # noqa: BLE001
        h._json({"ok": False, "error": f"could not build job: {exc}"}, 400)
        return
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    job_id = job["id"]

    if not wait:
        # Fire-and-forget — tell the agent where to look.
        h._json({
            "ok": True,
            "queued": True,
            "job_id": job_id,
            "caption": caption,
            "where_results_land": (
                "The render runs on the panel queue; results appear in the "
                "panel's Recent tab and under panel_uploads/library/manual/"
                "<date>/. Poll GET /state for this job_id, or re-submit with "
                "wait:true to block for the images."
            ),
        }, 202)
        return

    # ---- wait: poll the job to completion (server-side) ----------
    # A job lives in STATE['queue'] (waiting), STATE['current']
    # (running) or STATE['history'] (finished) — find it by id across
    # all three under LOCK. We poll the JOB RECORD (not a global
    # guess): success => status done + output_path/candidate_paths
    # populated; failure => status failed/cancelled + error.
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

    deadline = P.time.time() + 25 * 60  # 25 min timeout
    poll_t0 = P.time.time()
    while True:
        rec = _find_job(job_id)
        if rec is not None:
            status = rec.get("status")
            if status in ("done", "failed", "cancelled"):
                break
        if P.time.time() > deadline:
            h._json({
                "ok": False,
                "error": "render timed out after 25 minutes (the job may still "
                         "finish on the queue)",
                "job_id": job_id,
                "caption": caption,
            }, 504)
            return
        P.time.sleep(1.0)

    rec = _find_job(job_id) or {}
    status = rec.get("status")
    if status != "done":
        err = (rec.get("error") or "render failed").strip()
        low = err.lower()
        # GPU/lock contention or video-in-flight → 503 (transient), not a
        # generic 500: tell the agent it can retry once the GPU frees up.
        if ("in progress" in low or "gpu is busy" in low
                or "already in progress" in low or "contend" in low):
            code = 503
        elif status == "cancelled":
            code = 409
        else:
            code = 500
        h._json({"ok": False, "error": err, "job_id": job_id, "caption": caption}, code)
        return

    # Success — collect the candidate PNG paths the worker recorded.
    params = rec.get("params", {}) or {}
    paths = list(params.get("candidate_paths") or [])
    if not paths and rec.get("output_path"):
        paths = [rec["output_path"]]
    images = [{"path": p, "url": "/file?path=" + P.quote(p)} for p in paths]
    seconds = rec.get("elapsed_sec")
    if seconds is None:
        seconds = round(P.time.time() - poll_t0, 2)
    h._json({
        "ok": True,
        "job_id": job_id,
        "caption": caption,
        "images": images,
        "seconds": seconds,
    })


# JSON-body endpoint for the manual Image Studio. Lives BEFORE
# the urlencoded body parsing because the body is JSON, not form
# data. Drives `/image/generate` — the panel-side counterpart to
# the agent's `generate_shot_images` tool, but with explicit
# engine override so the user can pick a backend in the UI
# without first going through Settings.
@post("/image/generate")
def post_image_generate(h, path, qs, ctype) -> None:
    # The chain arm carried this condition; its failure fell
    # through to the chain end, which answers 404.
    if not (ctype.startswith("application/json")):
        h.send_error(404)
        return
    # Content-Length validation — reject unbounded bodies (a misbehaving
    # client could otherwise spool gigabytes into memory). The request
    # is just text + paths so 1 MB is more than enough.
    try:
        length = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        h._json({"error": "invalid Content-Length"}, 400); return
    if length <= 0:
        h._json({"error": "Content-Length required for JSON body"}, 411); return
    MAX_IMAGE_GEN_JSON = 1 * 1024 * 1024
    if length > MAX_IMAGE_GEN_JSON:
        h._json({"error": f"body too large (max {MAX_IMAGE_GEN_JSON} bytes)"}, 413); return
    try:
        payload = P.json.loads(h.rfile.read(length).decode() or "{}")
    except (P.json.JSONDecodeError, UnicodeDecodeError):
        h._json({"error": "invalid JSON body"}, 400); return

    prompt = (payload.get("prompt") or "").strip()
    if not prompt:
        h._json({"error": "prompt is required"}, 400); return

    try:
        n = max(1, min(8, int(payload.get("n", 4))))
    except (TypeError, ValueError):
        n = 4
    aspect = (payload.get("aspect") or "16:9").strip()
    try:
        seed = int(payload.get("seed", -1))
    except (TypeError, ValueError):
        seed = -1
    base_seed = seed if seed >= 0 else None

    # Refs: 0-3 absolute paths. Resolve via UPLOADS for safety
    # (path traversal protection — `_ensure_under` matches the
    # agent-tools convention).
    refs_in = payload.get("refs") or []
    if not isinstance(refs_in, list):
        h._json({"error": "refs must be a list of paths"}, 400); return
    if len(refs_in) > 3:
        h._json({"error": "refs supports at most 3 images (Qwen-Edit-2509 limit)"}, 400); return
    refs_resolved: list[str] = []
    for r in refs_in:
        if not isinstance(r, str) or not r.strip():
            h._json({"error": "each ref must be a non-empty path"}, 400); return
        r = P.normalize_pasted_path(r)
        rp = P.Path(r)
        if not rp.is_absolute():
            rp = (P.UPLOADS / r).resolve()
        else:
            rp = rp.resolve()
        # Security: refs must live under UPLOADS or the public
        # outputs dir. Prevents the JSON body from naming /etc/...
        # Use Path.is_relative_to (3.9+) — string startswith() lets
        # `panel_uploads_evil/` slip through because it shares the
        # `panel_uploads` prefix without being under it. Both ends
        # are already .resolved() so symlink/.. tricks are
        # neutralized. Mirrors the agent-side _ensure_under
        # convention in agent/tools.py.
        allowed_roots = [P.UPLOADS.resolve(), P.OUTPUT.resolve()]
        if not any(rp.is_relative_to(root) for root in allowed_roots):
            h._json({"error": f"ref path not under uploads/outputs: {r}"}, 403); return
        if not rp.is_file():
            h._json({"error": f"ref image not found: {r}"}, 404); return
        refs_resolved.append(str(rp))

    # Engine override: "auto" (use saved Settings config), or
    # one of the inline shorthands the modal exposes. Catalogue
    # is centralised in `_build_image_engine_config` so the
    # ``mode == "image"`` queue worker shares the same shorthands.
    engine_override = (payload.get("engine_override") or "auto").lower()
    try:
        cfg = P._build_image_engine_config(engine_override, form=payload)
    except ValueError as e:
        h._json({"error": str(e)}, 400); return

    # Output dir: panel_uploads/library/manual/<YYYYMMDD>/<unix_ms>/
    # — date-bucketed for directory hygiene PLUS a per-request
    # millisecond subdir so two generations in the same day don't
    # collide on `cand_NN_<family>.png` filenames (the engine
    # picks deterministic names; without the per-request subdir
    # a 10am n=4 batch would be overwritten by an 11am n=2 batch
    # on indices 0-1). The library reader walks recursively so
    # the extra depth is invisible to consumers.
    out_dir = (P.UPLOADS / "library" / "manual"
               / P.time.strftime("%Y%m%d")
               / str(int(P.time.time() * 1000)))
    out_dir.mkdir(parents=True, exist_ok=True)

    # Acquire the process-wide GPU gate — fail fast (429) if ANY GPU
    # job is running: a video/image/training render on the worker
    # (which holds _GPU_LOCK for its whole duration) OR another inline
    # image. This is the v3.0.7 P1 fix — it covers BOTH directions, so
    # a second concurrent GPU consumer can never start and OOM the Mac.
    # Released in the finally below.
    if not P._GPU_LOCK.acquire(blocking=False):
        h._json({
            "error": "the GPU is busy with a render or training job — "
                     "image generation is paused until it finishes (they'd "
                     "contend for GPU memory). Try again once it completes.",
        }, 429); return
    t0 = P.time.time()
    try:
        try:
            # Stream mflux stdout into the panel log card so users
            # see step-by-step progress while the gen runs.
            # tqdm progress bars come through line-buffered.
            candidates = P.agent_image_engine.generate(
                prompt=prompt, n=n, aspect=aspect,
                output_dir=out_dir,
                base_seed=base_seed,
                refs=refs_resolved or None,
                config=cfg,
                on_log=lambda line: P.push(f"[image] {line}"),
            )
        except FileNotFoundError as e:
            h._json({"error": str(e)}, 404); return
        except RuntimeError as e:
            # Engine-side failures (missing binary, etc.) → 500 with
            # the engine's own message so the UI surfaces the install
            # hint or whatever else the engine knows about.
            h._json({"error": str(e)}, 500); return
        except ValueError as e:
            h._json({"error": str(e)}, 400); return
    finally:
        P._GPU_LOCK.release()
    elapsed = round(P.time.time() - t0, 2)

    # Sidecar JSON next to each candidate so list_library_images
    # surfaces full metadata. Same schema as
    # agent.tools._generate_shot_images writes — keeps the library
    # reader consistent regardless of the source.
    generated_at = P.time.time()
    for c in candidates:
        png = c.get("png_path")
        if not png:
            continue
        sidecar_path = P.Path(png).with_suffix(P.Path(png).suffix + ".json")
        sidecar = {
            "schema": "phosphene/library/image@1",
            "png_path": png,
            "prompt": prompt,
            "refs": list(refs_resolved),
            "engine": c.get("engine"),
            "family": c.get("family"),
            "model": c.get("model"),
            "seed": c.get("seed"),
            "width": c.get("width"),
            "height": c.get("height"),
            "aspect": aspect,
            "session_id": None,
            "shot_label": None,
            "take_index": None,
            "generated_at": generated_at,
            "refs_ignored": c.get("refs_ignored", False),
            "source": "panel.image_studio",
        }
        try:
            sidecar_path.write_text(P.json.dumps(sidecar, indent=2), encoding="utf-8")
        except OSError:
            pass  # best-effort

    h._json({
        "ok": True,
        "candidates": candidates,
        "elapsed_seconds": elapsed,
        "engine": cfg.kind + (f"/{cfg.mflux_family}" if cfg.kind == "mflux" else ""),
        "model": getattr(cfg, "mflux_model", None) or cfg.kind,
        "output_dir": str(out_dir),
    })
