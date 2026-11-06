"""/queue family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

import threading
from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time

# SAFETY-7: one recovery join at a time owns STATE["take_join_pgid"].
_TAKE_JOIN_LOCK = threading.Lock()


@get("/status")
def get_status(h, parsed) -> None:
    qs = P.parse_qs(parsed.query)
    include_hidden = qs.get("include_hidden", ["0"])[0] == "1"
    # Deep-snapshot STATE under lock — payload built with refs only
    # is racy when JSON serialization happens after lock release
    # (worker thread could mutate current/queue mid-encode and we'd
    # ship torn state to the browser).
    import copy as _copy
    with P.LOCK:
        avg = P._avg_elapsed()
        avg_image = P._avg_elapsed("image")
        avg_video = P._avg_elapsed("video")
        payload = _copy.deepcopy({
            "running": P.STATE["running"], "paused": P.STATE["paused"],
            "paused_reason": P.STATE.get("paused_reason"),
            "current": P.STATE["current"], "queue": P.STATE["queue"],
            "history": P.STATE["history"][:P.HISTORY_API_LIMIT], "log": P.STATE["log"],
            "pid": P.STATE["pid"], "pgid": P.STATE["pgid"],
        })
        hidden_count = len(P.HIDDEN_PATHS)
    # Human title per job, derived at read time (old history included).
    for _j in ([payload["current"]] if payload.get("current") else []) + \
            list(payload.get("queue") or []) + list(payload.get("history") or []):
        try:
            _p = _j.get("params") or {}
            _j["title"] = P.clip_title.display_title(_p.get("prompt"), _p.get("label") or _j.get("label"))
        except Exception:
            pass
    # Polling fast path — newest 60 only. outputs_total tells the
    # carousel header "X older outputs not shown" so the "Show all"
    # button can surface them via the /outputs endpoint.
    _outs, _outs_total = P.list_outputs(
        include_hidden=include_hidden, limit=60, return_total=True,
    )
    payload["outputs"] = _outs
    payload["outputs_total"] = _outs_total
    payload["hidden_count"] = hidden_count
    # The one-time "star us on GitHub" card (owner, 2 Nov 2026): true only
    # between the fifth finished render and the page putting the card up.
    payload["star_prompt_due"] = P.star_prompt_due()
    # Storyboards — one line per board. Three jobs, all of them cheap:
    # the `S03/12` badge on a queue card needs the denominator, the tab
    # count during an overnight run needs `running`, and `To film` in
    # the player overlay stays hidden until this list is non-empty
    # (rule 5 — with no film in progress the panel looks exactly as it
    # did yesterday).
    payload["storyboards"] = P._sb_all_summaries()
    payload["memory"] = P.get_memory()
    payload["comfy_pids"] = P.find_comfy_pids()
    payload["server_now"] = P.time.time()
    payload["avg_elapsed_sec"] = avg
    # v3.0.7 (P2): surface the installed ltx-2-mlx version (from the
    # helper's ready event) so every bug report — not just a mismatch
    # log — carries what's actually running. Empty until the helper
    # has booted at least once this session.
    payload["ltx_version"] = P.HELPER.ready_info.get("ltx_version")
    payload["ltx_version_expected"] = P.HELPER.ready_info.get("ltx_version_expected")
    payload["ltx_version_match"] = P.HELPER.ready_info.get("ltx_version_match")
    # Runtime fingerprint (mlx/chip/macOS) for triaging the "mosaic"
    # MLX-numerical bug — surfaces in every bug report's /status.
    payload["mlx_version"] = P.HELPER.ready_info.get("mlx_version")
    payload["mlx_metal_version"] = P.HELPER.ready_info.get("mlx_metal_version")
    payload["chip"] = P.HELPER.ready_info.get("chip")
    payload["macos"] = P.HELPER.ready_info.get("macos")
    # #44: null on a healthy machine. Non-null means this boot hit the
    # Metal GPU watchdog during prompt encoding and Gemma is now
    # encoding at the shorter padded length — the single most useful
    # field on a "my prompts feel weaker" follow-up report.
    payload["gemma_max_length"] = P.HELPER.gemma_max_length
    # Per-kind avg ETA: image jobs are 30s–2min, video jobs are
    # 5–30min. Computing queue ETA from one mixed avg makes an
    # image queued after a few videos show "~30 min" — the
    # videos drown out the truth. Use the kind-specific avg per
    # queued job and fall back to category-appropriate defaults
    # when history is empty (90s for images, 420s for videos).
    def _eta_for(job: dict) -> float:
        params = job.get("params") or {}
        # VC-26: prefer the PRICED number the quality chips themselves show
        # (LTX_TIERS/H3_TIERS eta_min) over the coarse per-kind average —
        # it is the same table VC-28 already made honest, and a queue row
        # showing a different number than the chip it was submitted from
        # would just be a second class of the VC-28 bug.
        priced = P.job_priced_eta_sec(params)
        if priced is not None:
            return priced
        if params.get("engine") == "music":
            return P.music_estimate(params["music_quality"], params["music_max_seconds"])["eta_sec"]
        if params.get("engine") == "speech":
            return P.speech_estimate(params.get("speech_text") or "", params.get("speech_speed") or 1.0)["eta_sec"]
        if params.get("mode") == "image":
            return float(avg_image) if avg_image else 90.0
        return float(avg_video) if avg_video else 420.0
    # Stamped per-row (VC-26: "an ETA per job") as well as summed for the
    # existing total — one call per job either way, just captured once.
    for _j in payload["queue"]:
        _j["eta_sec"] = round(_eta_for(_j))
    payload["eta_sec"] = round(sum(_j["eta_sec"] for _j in payload["queue"]))
    # Y1.039 — per-job progress for the Now-card. Phase-aware,
    # config-bucketed ETA, denoise-step extrapolation. Replaces the
    # old elapsed/global-avg ratio that mis-paced Quick/High renders.
    #
    # Train jobs OWN their own progress dict — the trainer writes
    # step / phase / eta directly into STATE["current"]["progress"]
    # at runtime (see run_train_job_inner around line 5212 for the
    # face phase and 5461 for the voice phase). _compute_progress
    # is built around the video helper's log-tail format and would
    # blindly stamp a "phase=setup" snapshot on top of the trainer's
    # real "Training face · step N / total", making the Now card
    # appear stuck at Loading pipeline. Skip the override when the
    # job mode is "train".
    #
    # Hailuo H3 renders are in the SAME boat and for the same reason:
    # run_h3_job_inner writes its own phase + step progress from the
    # staged runner's stdout ("== joint_denoise ==", "step 3/8: 57.9s"),
    # which _compute_progress can't parse — leaving the Now card on
    # "Loading pipeline" for the whole render. Caught in validation.
    if payload.get("current"):
        _cur_params = (payload["current"].get("params") or {})
        _mode = (_cur_params.get("mode") or "").lower()
        _engine = (_cur_params.get("engine") or "ltx").lower()
        _waiting = payload["current"].get("waiting_note")
        if _waiting:
            # 4.17.4: a voice-only Lip-sync job holding for the separator
            # install says so, instead of "Loading pipeline" at 0 %.
            _started = payload["current"].get("started_ts") or 0
            payload["current"]["progress"] = {
                "phase": "waiting", "pct": 0, "phase_label": str(_waiting),
                "elapsed_sec": max(0.0, P.time.time() - _started) if _started else 0,
            }
        elif _mode != "train" and _engine not in ("h3", "music", "speech"):
            payload["current"]["progress"] = P._compute_progress(
                payload["current"], payload.get("log") or [],
            )
        elif _engine == "h3":
            # The H3 runner owns its progress object, so preserve it
            # and layer the separate h3-live-preview/1 file contract
            # onto the snapshot. This executes in the existing status
            # poll; there is no preview-specific request or timer.
            _h3_progress = dict(payload["current"].get("progress") or {})
            _h3_progress.update(P._h3_preview_progress(payload["current"]))
            payload["current"]["progress"] = _h3_progress
    payload["helper"] = {
        "alive": P.HELPER.is_alive(), "pid": P.HELPER.pid(),
        "low_memory": P.HELPER_LOW_MEMORY == "true",
        "idle_timeout_sec": P.HELPER_IDLE_TIMEOUT,
    }
    # Completeness checks come from the shared required_files.json so
    # the menu, the UI, and the run-time job validator all agree on
    # what counts as "installed". Single source of truth — see the
    # _load_required_files() helper near the top of this file.
    _q8_missing = P.q8_missing_files()
    _base_missing = P.base_missing()
    # q8_available consults BOTH layers (local + HF cache) via
    # q8_available_anywhere() so the FFLF/Extend/HQ gate agrees
    # with the model browser modal. Closes issue #9 (oo2music).
    # q8_missing keeps reporting local-dir-missing so the user
    # can still see what would be needed for a fresh install;
    # we zero it out when Q8 is reachable via cache so the UI
    # doesn't show a spurious "missing files" warning alongside
    # an enabled FFLF button.
    # The UI reads q8_available to enable High / Extend / Keyframe /
    # FFLF. Those need the HQ add-on as well as the pack, so a pack-only
    # answer would light the pill up and let the job fail at load time
    # -- the same wave-through e870061 closed, moved to the UI layer.
    # For 2.3 `hq_addon_missing()` is [] and this is byte-for-byte the
    # old expression.
    _hq_addon_missing = P.hq_addon_missing()
    _q8_available = P.q8_available_anywhere() and not _hq_addon_missing
    payload["q8_available"] = _q8_available
    payload["q8_missing"] = [] if _q8_available else _q8_missing
    # THE Q8 WEIGHTS PACK, on its own — distinct from `q8_available`
    # above, which folds in the HQ add-on because High / Extend /
    # Keyframe genuinely need both.
    #
    # Characters need the PACK and not the add-on: on 2.5 they render on
    # q8 + distilled, which is the recipe every graded 2.5 character
    # clip ran. Reading `q8_available` for them would tell a user who
    # has the full 30 GB pack that they must "Install Q8 (30 GB)"
    # because a DIFFERENT 29.5 GB download is absent — the pack they
    # already have, demanded again, to run a path that does not use the
    # missing file. One conflated boolean, two very different questions.
    payload["q8_pack_available"] = P.q8_available_anywhere()
    payload["q8_pack_missing"] = _q8_missing
    # Reported separately so the Models page can say WHICH download is
    # the one standing between the user and the High tier.
    payload["hq_addon_missing"] = _hq_addon_missing
    payload["hq_surface_missing"] = P.hq_surface_missing()
    payload["q8_path"] = str(P.pack_path("q8"))
    payload["base_available"] = not _base_missing
    payload["base_missing"] = _base_missing
    path_errors = [P.model_path_problem(P._deferred_repo(k), write=bool(_base_missing))
                   for k in P.capability_repo_keys("render")]
    payload["model_path_error"] = next((e for e in path_errors if e), "")
    # Repo-level counts for the header pill — granular view that
    # matches the modal's per-repo rows (Q4 + Gemma + Q8 = 3 in the
    # default manifest). Avoids the pill claiming "2/2 ready" while
    # the modal shows three rows.
    #
    # VC-32: this used to count ALL of required_files.json's repos (12,
    # once the 2.5 generation and four IC-LoRA/tae extras joined the 2.3
    # trio the comment above still describes) — so a complete, working 2.5
    # install with no IC-LoRAs read "models 4/12", and a partial-but-
    # correctly-green pill (every REQUIRED file present) still showed a
    # scary-looking fraction. relevant_repo_keys() scopes this to the
    # ACTIVE generation's own packs plus the version-agnostic extras —
    # never another generation's base pack the install isn't running.
    _repo_snap = P.repo_status_list()
    _relevant_keys = P.relevant_repo_keys()
    _relevant_snap = [r for r in _repo_snap if r.get("key") in _relevant_keys]
    payload["repos_total"] = len(_relevant_snap)
    payload["repos_ready"] = sum(1 for r in _relevant_snap if r.get("complete"))
    # Structural integrity of installed weights — corrupt/partial
    # safetensors decode to a garbage "mosaic". Cached + header-only.
    payload["model_integrity"] = P._model_integrity(force=False)
    with P._DEEP_VERIFY_LOCK:
        payload["deep_verify"] = {
            "active": P._DEEP_VERIFY["active"],
            "progress": P._DEEP_VERIFY["progress"],
            "result": P._DEEP_VERIFY["result"],
        }
    # Hardware tier — UI uses this to disable mode pills / quality
    # buttons / show a helpful banner explaining what this Mac can
    # and can't do. Detected once at startup; the override env
    # var lets users force a tier for testing.
    payload["tier"] = {
        "key": P.SYSTEM_TIER,
        "label": P.SYSTEM_CAPS["label"],
        "ram_label": P.SYSTEM_CAPS["ram_label"],
        "tagline": P.SYSTEM_CAPS["tagline"],
        "blurb": P.SYSTEM_CAPS["blurb"],
        "allows_q8": P.SYSTEM_CAPS["allows_q8"],
        "allows_keyframe": P.SYSTEM_CAPS["allows_keyframe"],
        "allows_extend": P.SYSTEM_CAPS["allows_extend"],
        "t2v_max_dim": P.SYSTEM_CAPS["t2v_max_dim"],
        "i2v_max_dim": P.SYSTEM_CAPS["i2v_max_dim"],
        "keyframe_max_dim": P.SYSTEM_CAPS["keyframe_max_dim"],
        "extend_max_dim": P.SYSTEM_CAPS["extend_max_dim"],
        # VC-12 / SYS-20: computed live from the same model the Quality
        # chips use, not the old hand-typed CAPABILITIES[tier]["times"]
        # table that disagreed with them on every tier.
        "times": P.honest_tier_times(),
        # VA-11 / VA-12: keyframe/extend price the ACTUAL pipeline that
        # runs (Q8 two-stage at a clamped canvas; the extend step count),
        # not whatever Quality pill happens to be selected in the strip.
        "keyframe_price": P.ltx_mode_price_card("keyframe"),
        "extend_price_draft": P.ltx_mode_price_card("extend", steps=8),
        "extend_price_pro": P.ltx_mode_price_card("extend", steps=30),
        # SYS-03: the Train tab needs the exact RAM figure (not just the
        # tier label) to gate itself before a Mac under TRAIN_MIN_RAM_GB
        # can even see the 41 GB download offer. One number, one source
        # (SYSTEM_RAM_GB / TRAIN_MIN_RAM_GB), so the client never hardcodes
        # its own copy of the threshold.
        "ram_gb": round(P.SYSTEM_RAM_GB, 1),
        "train_min_ram_gb": P.TRAIN_MIN_RAM_GB,
    }
    # Hailuo H3 — the optional second video engine. Re-read every tick
    # (it's a handful of stat() calls) so an install finishing in the
    # Pinokio sidebar unlocks the engine pill without a panel restart,
    # exactly like the Q8 download already does.
    payload["h3"] = P.h3_status()
    payload["music"] = P.music_status()
    payload["music_install"] = P.music_install_status()
    payload["speech"] = P.speech_status()
    payload["speech_install"] = P.speech_install_status()
    # 4.17.4: can a render start at all (the engine venv), and the one-click
    # repair's progress. File checks only - cheap enough for every poll.
    payload["engine_env"] = P.engine_env_status()
    # 4.19.5: the same question for the Hailuo H3 venv - on its known-good set
    # (scripts/pinokio/h3_constraints.txt) or not - and its repair's progress.
    payload["h3_env"] = P.h3_env_status()
    payload["train_profile"] = P.TRAIN_PROFILE
    payload["train_presets"] = P.TRAIN_PRESETS
    payload["train_style_presets"] = P.TRAIN_STYLE_PRESETS
    payload["train_default_preset"] = P.TRAIN_DEFAULT_PRESET
    payload["generation_profile"] = P.GENERATION_PROFILE
    # Active model-download status — UI shows a progress strip when
    # this is set. last_line is the most recent hf output line so the
    # user gets live feedback even before opening the log panel.
    with P.DOWNLOAD_LOCK:
        if P.DOWNLOAD["active"]:
            _dl_repo = next((r for r in P._repos() if r.get("key") == P.DOWNLOAD["key"]), {})
            payload["download"] = {
                "active": True,
                "key": P.DOWNLOAD["key"],
                "repo_id": P.DOWNLOAD["repo_id"],
                "started_ts": P.DOWNLOAD["started_ts"],
                "last_line": P.DOWNLOAD["last_line"],
                # A bundle request uses the shared background queue.
                "background": bool(P.DOWNLOAD.get("background")),
                "name": _dl_repo.get("name") or P.DOWNLOAD["key"],
                "size_gb": _dl_repo.get("size_gb"),
                **P.model_download_progress(_dl_repo),
            }
        else:
            payload["download"] = {"active": False}
    # Requested model packs and progress. Empty on a fresh first open.
    payload["deferred_downloads"] = P.deferred_status()
    # Mac app review #11: the process-wide GPU gate. `current` shows the queued
    # job; an inline image render, a character sheet, Enhance, a lyrics request
    # or the post-update script hold the GPU OUTSIDE the queue and showed
    # nowhere, so a client deciding "is it safe to stop the studio" must read
    # this too: {busy, holder, since, outside_queue}.
    payload["gpu"] = P.gpu_gate_status()
    # Mac app only: the app's own model download (app_models.json). The
    # "Base models needed" card names it instead of offering a second fetch.
    _app = P.app_models_state()
    if _app is not None:
        payload["app_models"] = _app
    payload["hf_available"] = P.HF_BIN is not None
    # Settings snapshot — only needs the public-safe view (booleans
    # for token presence, no secret values). The UI reads
    # `settings.models_card_dismissed` on each /status tick to know
    # whether to keep the inline models card hidden.
    payload["settings"] = P.get_settings_public()
    h._json(payload)


@get("/outputs")
def get_outputs(h, parsed) -> None:
    # Paginated unified gallery — full history, not just the newest
    # 60 that /status surfaces. Carousel's "Show all (N)" button
    # calls this when the user wants to scroll older renders.
    # Defaults: include_hidden=0, limit=10000 (effectively all on
    # a typical install), offset=0. Negative values are rejected.
    qs = P.parse_qs(parsed.query)
    include_hidden = qs.get("include_hidden", ["0"])[0] == "1"
    try:
        limit = int(qs.get("limit", ["10000"])[0])
        offset = int(qs.get("offset", ["0"])[0])
    except (TypeError, ValueError):
        h._json({"error": "limit/offset must be integers"}, 400); return
    if limit < 0 or offset < 0:
        h._json({"error": "limit/offset must be >= 0"}, 400); return
    _outs, _total = P.list_outputs(
        include_hidden=include_hidden, limit=limit, offset=offset,
        return_total=True,
    )
    h._json({
        "outputs": _outs,
        "total": _total,
        "offset": offset,
        "limit": limit,
        "returned": len(_outs),
    })


# ====== Re-submit a failed/cancelled job by id ====================
# The Recent tab's Retry button hits this. We look up the source
# job in history (and current/queue defensively), clone its params,
# and append a new entry to the queue. The new job gets a fresh id;
# the source row stays in history so the user can see they retried.
@post("/queue/retry")
def post_queue_retry(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    source_id = (form.get("id", [""])[0] or "").strip()
    if not source_id:
        h._json({"error": "id required"}, 400); return
    source = None
    with P.LOCK:
        for j in P.STATE.get("history") or []:
            if j.get("id") == source_id:
                source = j; break
        if source is None:
            for j in P.STATE.get("queue") or []:
                if j.get("id") == source_id:
                    source = j; break
        cur = P.STATE.get("current")
        if source is None and cur and cur.get("id") == source_id:
            source = cur
    if source is None:
        h._json({"error": f"job {source_id!r} not found in history/queue"}, 404); return
    src_params = source.get("params") or {}
    new_params = dict(src_params)
    # VC-08: the OOM/jetsam failure card's ONLY action used to be "Retry"
    # of the identical job — same size, same length, same failure, another
    # 10+ minutes on a memory-pressured Mac. "Retry smaller" sends a small,
    # explicit allowlist of overrides (never an arbitrary form) that the
    # client fills in from the SAME quality ladder and frame-count math the
    # render form itself uses — this route never invents a size, it only
    # accepts one the client already knows how to produce.
    overrides_raw = (form.get("overrides", [""])[0] or "").strip()
    if overrides_raw:
        try:
            overrides = P.json.loads(overrides_raw)
        except (TypeError, ValueError):
            h._json({"error": "overrides must be JSON"}, 400); return
        if not isinstance(overrides, dict):
            h._json({"error": "overrides must be a JSON object"}, 400); return
        allowed = {"quality", "frames"}
        unknown = set(overrides) - allowed
        if unknown:
            h._json({"error": f"overrides may only set {sorted(allowed)}, "
                               f"got {sorted(unknown)}"}, 400); return
        if "quality" in overrides and overrides["quality"] not in (
                "quick", "balanced", "standard", "high"):
            h._json({"error": f"unknown quality {overrides['quality']!r}"}, 400); return
        if "frames" in overrides:
            try:
                overrides["frames"] = int(overrides["frames"])
            except (TypeError, ValueError):
                h._json({"error": "frames override must be an int"}, 400); return
        new_params.update(overrides)
    # Defense-in-depth: re-queuing a historical job with character_id
    # + balanced quality (or a raw train_character LoRA + non-high
    # quality) reproduces the bug /queue/add already rejects. Wrap
    # new_params into the form-shape the validator expects: it reads
    # `character_id`, `quality`, and `loras`. The first two are
    # already strings; `loras` lives in new_params as a parsed list
    # and needs to be re-encoded as JSON so parse_loras_from_form
    # round-trips it cleanly. Validated against new_params (post-
    # overrides) — a "smaller" retry must not reintroduce the exact
    # character+quality combination this gate exists to refuse.
    _retry_form = {
        "character_id": new_params.get("character_id") or "",
        "quality": new_params.get("quality") or "",
        "loras": P.json.dumps(new_params.get("loras") or []),
        "engine": new_params.get("engine") or P.ENGINE_DEFAULT,
        "mode": new_params.get("mode") or "t2v",
        "no_voice": new_params.get("no_voice") or "off",
    }
    err = P._validate_character_quality(_retry_form)
    if err:
        h._json({"error": err}, 400); return
    # Build a fresh job — copy params (verbatim, or with the validated
    # overrides applied above), mint a new id, drop any open_when_done
    # flag (retries are usually background; user is mid-other-work and
    # doesn't want the OS jumping windows).
    new_job = {
        "id": P._new_job_id(),
        "status": "queued",
        "queued_at": P.time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None,
        "finished_at": None,
        "elapsed_sec": None,
        "params": new_params,
        "raw_path": None,
        "output_path": None,
        "error": None,
    }
    new_job["params"]["open_when_done"] = False
    new_job["params"].pop("face_fix_result", None)
    if new_job["params"].get("face_fix_targets"):
        new_job["params"]["face_fix_targets"] = [list(x) for x in new_job["params"]["face_fix_targets"]]
    new_job["params"]["source"] = "retry"
    # SYS-07: "Retry smaller" — the failure card for the Metal-watchdog /
    # jetsam classes (the two biggest fleet failures) used to offer only a
    # plain Retry, which re-queues the SAME size and SAME length that just
    # died of a GPU timeout or an OOM kill. This re-queues one quality rung
    # down, at a scaled-and-grid-snapped canvas (via the same ltx_fit_canvas
    # every hardware clamp goes through, so the result is never an
    # off-grid size the pipeline would reject), and roughly halves the
    # clip length toward the 8k+1 frame grid — real, not cosmetic, relief
    # for the two failure classes it's offered on.
    if (form.get("smaller", ["0"])[0] or "0") == "1":
        p = new_job["params"]
        step_down = {"pro": "high", "high": "standard", "standard": "balanced", "balanced": "quick"}
        p["quality"] = step_down.get(str(p.get("quality") or "balanced").lower(), "quick")
        # 4.17 Codex SAFETY-2: the height used to be bound to `h` — the HTTP
        # handler — so the reply below raised AttributeError AFTER the
        # smaller job was queued: the button reported failure and a second
        # click queued a second replacement.
        try:
            w, ht = int(p.get("width") or 0), int(p.get("height") or 0)
            if w > 0 and ht > 0:
                p["width"], p["height"] = P.ltx_fit_canvas(w, ht, int(max(w, ht) * 0.75))
        except (TypeError, ValueError):
            pass
        try:
            frames = int(p.get("frames") or 121)
            if frames > 41:
                p["frames"] = max(41, ((frames // 2) // 8) * 8 + 1)
        except (TypeError, ValueError):
            pass
        # 4.17.5 (Codex): Extend renders `extend_frames` latents, not
        # `frames` - halve what that pipeline actually consumes, or "smaller"
        # re-ran the same workload.
        if str(p.get("mode") or "") == "extend":
            try:
                ef = int(p.get("extend_frames") or 0)
                if ef > 1:
                    p["extend_frames"] = max(1, ef // 2)
            except (TypeError, ValueError):
                pass
    # 4.17.5: a retry of a job whose input is gone (or was never there) can
    # only fail again - say so now instead of after its turn in the queue.
    refusal = P.job_input_refusal(new_job["params"])
    if refusal:
        h._json({"error": refusal, "code": "input_missing"}, 400); return
    # Fleet 2 Nov 2026: Retry on a failed training run re-queued the same run
    # the trainer had just refused for want of its weights. Say it here.
    if str(new_job["params"].get("mode") or "") == "train":
        _weights = P.train_weights_refusal()
        if _weights:
            h._json({"error": _weights, "code": "train_models"}, 409); return
    _lic = P.job_licence_refusal(new_job["params"])
    if _lic:
        h._json(_lic, 451); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(new_job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "id": new_job["id"], "source_id": source_id})


@post("/queue/batch")
def post_queue_batch(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    err = P._validate_character_quality(form)
    if err:
        h._json({"error": err}, 400); return
    raw = (form.get("prompts", [""])[0] or "").strip()
    if not raw:
        h._json({"error": "no prompts"}, 400); return
    chunks = [c.strip() for c in P.re.split(r"^\s*---\s*$", raw, flags=P.re.MULTILINE)]
    chunks = [c for c in chunks if c]
    if not chunks:
        h._json({"error": "no prompts after split"}, 400); return
    # The character contract applies here too. /queue/batch accepts any
    # form the Manual tab can build, character_id included, and it was
    # the one enqueue path that neither validated up front nor caught
    # CharacterRequestError — so a batch cast with a voice-less
    # character raised mid-loop, INSIDE the queue lock, after some jobs
    # were already appended and before persist_queue() or any response.
    # Half a batch, no answer, and the refusal the rest of the panel
    # makes politely arriving as a 500.
    err = P._validate_character_quality(form)
    if err:
        h._json({"error": err}, 400); return
    ids = []
    try:
        built = [P.make_job(form, override_prompt=pr) for pr in chunks]
    except P.CharacterRequestError as exc:
        h._json({"error": str(exc)}, 400); return
    # 4.19.2: the same up-front refusals /queue/add makes. A batch skipped
    # them, so a batch of image prompts whose picture had gone failed once per
    # prompt, each after its turn (fleet 4.19.1: 12 in a row on one Mac).
    for job in built:
        refusal = P.job_input_refusal(job.get("params") or {})
        if refusal:
            h._json({"error": refusal, "code": "input_missing"}, 400); return
    for job in built[:1]:            # the form is shared; one licence screen
        _lic = P.job_licence_refusal(job.get("params") or {})
        if _lic:
            h._json(_lic, 451); return
    for job in built[:1]:            # the form is shared; one install offer
        refusal = P.a2v_stem_refusal(job["params"])
        if refusal:
            h._json({"error": refusal, "code": "vocal_separator_missing"}, 400); return
    # Built first, appended second: a batch is all-or-nothing, so a
    # refusal on prompt 7 cannot leave prompts 1-6 queued.
    with P.QUEUE_COND:
        for job in built:
            job["params"]["open_when_done"] = False
            P.STATE["queue"].append(job)
            ids.append(job["id"])
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "added": len(ids), "ids": ids})


@post("/queue/remove")
def post_queue_remove(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    job_id = qs.get("id", [""])[0] or form.get("id", [""])[0]
    removed = False
    with P.LOCK:
        for i, j in enumerate(P.STATE["queue"]):
            if j["id"] == job_id:
                P.STATE["queue"].pop(i)
                removed = True
                break
    P.persist_queue()
    h._json({"removed": removed}); return


@post("/queue/reorder")
def post_queue_reorder(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    job_id = (form.get("id", [""])[0] or qs.get("id", [""])[0] or "").strip()
    direction = (form.get("direction", [""])[0] or qs.get("direction", [""])[0] or "").strip()
    if not job_id:
        h._json({"ok": False, "error": "no job id given"}, 400); return
    out = P.queue_move_job(job_id, direction)
    h._json(out, 200 if out.get("ok") else 400)


@post("/queue/update")
def post_queue_update(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    job_id = (form.get("id", [""])[0] or qs.get("id", [""])[0] or "").strip()
    if not job_id:
        h._json({"ok": False, "error": "no job id given"}, 400); return
    try:
        out = P.queue_update_job(job_id, form)
    except Exception as exc:                                   # noqa: BLE001
        h._json({"ok": False, "error": f"could not update job: {exc}"}, 500)
        return
    h._json(out, 200 if out.get("ok") else 400)


@post("/queue/clear")
def post_queue_clear(h, path, qs, ctype) -> None:
    # SAFETY-9: the reply carries a single-use `undo_token` for exactly the
    # jobs this call removed (see P.queue_clear_with_undo).
    out = P.queue_clear_with_undo()
    P.persist_queue()
    h._json(out); return


@post("/queue/pause")
def post_queue_pause(h, path, qs, ctype) -> None:
    with P.QUEUE_COND:
        P.STATE["paused"] = True
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"paused": True}); return


@post("/queue/resume")
def post_queue_resume(h, path, qs, ctype) -> None:
    with P.QUEUE_COND:
        P.STATE["paused"] = False
        # SYS-32: clear the breaker's reason on any resume, manual or not —
        # otherwise a stale "last 3 renders failed" notice would keep
        # showing after the user fixed the cause and pressed Resume.
        P.STATE["paused_reason"] = None
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"paused": False}); return


@post("/helper/restart")
def post_helper_restart(h, path, qs, ctype) -> None:
    # Mark any in-flight job as user-cancelled BEFORE killing the
    # helper. Otherwise the worker sees the helper exit and writes
    # status=failed/"helper exited" instead of status=cancelled.
    with P.LOCK:
        cur = P.STATE.get("current")
        if cur is not None:
            cur["cancel_requested"] = True
    P.HELPER.kill()
    h._json({"ok": True}); return


@post("/prompt/enhance")
def post_prompt_enhance(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    # Gemma-driven prompt enhancement, routed through the warm
    # helper subprocess. First call after panel start eats a
    # ~10-15s Gemma load; cached afterwards (subsequent enhances
    # ~3-5s). Helper's release_pipelines frees Gemma when a real
    # render comes in, so memory doesn't accumulate on top of
    # the dev transformer.
    user_prompt = (form.get("prompt", [""])[0] or "").strip()
    mode = (form.get("mode", ["t2v"])[0] or "t2v").lower()
    # VC-13: default TRUE (unchanged behaviour — Enhance has always
    # translated to English). "0"/"false"/"off" is the explicit opt-out
    # from the prompt box's "Translate to English" toggle, which only
    # shows once a non-Latin-script prompt is typed.
    translate = (form.get("translate", ["1"])[0] or "1").strip().lower() not in ("0", "false", "off")
    source_non_latin = P.prompt_looks_non_latin(user_prompt)
    # A2V (VA-24): the t2v Gemma prompt asks for "describe the scene AND the
    # sound" and readily invents camera-restraint sentences — both wrong on a
    # shot driven by supplied audio (a sound description competes with the
    # real track; a stillness sentence freezes the mouth, the exact bug this
    # week's A2V prompt law exists to prevent). There is no dedicated a2v
    # Gemma system prompt, so this borrows the i2v one (closer: an image
    # anchors the shot, the prompt directs motion, no "and the sound" ask)
    # and then runs the result through the same stillness-cleanup + word-cap
    # every other a2v prompt gets before it reaches the engine.
    a2v_mode = (mode == "a2v")
    if a2v_mode:
        mode = "i2v"
    elif mode not in ("t2v", "i2v"):
        mode = "t2v"
    if not user_prompt:
        h._json({"error": "no prompt provided"}, 400); return
    # 4.20: Gemma 3 is downloaded in the background after the first boot, so
    # for a while after a fresh install it is not on disk yet. Say so - with
    # what to do - instead of letting the helper fail to load a model that is
    # still arriving.
    if P.enhance_model_missing():
        h._json(P.gemma3_refusal("Enhance", "its language model, Gemma 3", P.GEMMA), 409); return
    # 2026-05-20 — collect trigger tokens that Gemma MUST preserve
    # case-exact. Three sources, unioned:
    #   1. The panel-supplied `preserve_tokens` form field (the
    #      Enhance button sends active-LoRA triggers + character
    #      trigger).
    #   2. Known character names from list_characters() — covers
    #      the case where the user typed a character name without
    #      having loaded the LoRA yet (e.g. typing "bizarrotrn" in
    #      a fresh session before the avatar picker fired).
    #   3. Tokens in the user's prompt that look like trigger words
    #      (lowercase, no spaces, ends in `trn` or matches a known
    #      character id) — defense in depth against (1) being
    #      empty.
    preserve_raw = (form.get("preserve_tokens", [""])[0] or "").strip()
    preserve_set: set[str] = set()
    if preserve_raw:
        try:
            preserve_set.update(
                str(t).strip() for t in P.json.loads(preserve_raw)
                if str(t).strip()
            )
        except (TypeError, ValueError, P.json.JSONDecodeError):
            # Allow plain comma-separated fallback for API users
            preserve_set.update(
                t.strip() for t in preserve_raw.split(",")
                if t.strip()
            )
    try:
        for char in P.list_characters():
            trig = (char.get("trigger") or "").strip()
            if trig and trig in user_prompt:
                preserve_set.add(trig)
    except Exception:
        pass
    # 4.18.0: the LoRAs on this render, as the prompt helper should know them
    # (name, trigger words, the guide or the creator's note). Bounded and only
    # read from files the LoRA library lists — see lora_enhance_context.
    loras_raw = (form.get("loras", [""])[0] or "").strip()
    try:
        lora_ctx = P.lora_enhance_context(P.json.loads(loras_raw)) if loras_raw else []
    except (TypeError, ValueError):
        lora_ctx = []
    preserve_tokens = sorted(preserve_set)
    P.push(f"[enhance] {mode}: {user_prompt[:80]}…"
         + (f"  preserve={preserve_tokens}" if preserve_tokens else ""))
    # THE GPU GATE, like every other GPU user (routes_music._helper_run). The
    # queue unloads the LTX helper before an H3 or music render; an Enhance
    # click mid-render respawned it and loaded Gemma beside that engine — two
    # models on a Mac sized for one. The worker holds _GPU_LOCK for a whole
    # job, so during any render this answers "busy" at once instead of
    # queueing behind it until the enhance timeout.
    if not P._GPU_LOCK.acquire(timeout=3.0):
        # VC-16: name what's actually rendering (and how long, when the
        # priced tier table can say) instead of a flat "a render" refusal.
        reason = P.current_render_busy_reason()
        P.push(f"[enhance] skipped: {reason}")
        h._json({"error": f"{reason} Enhance works again when it finishes."}, 409); return
    P.gpu_gate_mark("enhance")
    try:
        result = P.HELPER.run({
            "action": "enhance_prompt",
            "id": f"enh-{int(P.time.time()*1000)}",
            "params": {"prompt": user_prompt, "mode": mode, "seed": 10,
                       "preserve_tokens": preserve_tokens, "translate": translate,
                       "lora_notes": lora_ctx},
        }, timeout=P.PROMPT_ENHANCE_TIMEOUT)
    except Exception as exc:
        P.push(f"[enhance] failed: {exc}")
        h._json({"error": str(exc)}, 500); return
    finally:
        P.gpu_gate_unmark()
        P._GPU_LOCK.release()
    # A malformed terminal helper event must still become JSON. Before
    # this guard, None/non-string values raised after the only try/except
    # in the lane and BaseHTTPRequestHandler closed the socket empty.
    if not isinstance(result, dict):
        h._json({"error": "Gemma returned an invalid helper response"}, 500); return
    enhanced_raw = result.get("enhanced", "")
    if not isinstance(enhanced_raw, str):
        h._json({"error": "Gemma returned an invalid enhanced prompt"}, 500); return
    enhanced = enhanced_raw.strip()
    if a2v_mode:
        # Same law every other a2v prompt goes through (storyboard.a2v_prompt):
        # stillness words out, capped to the direction word budget, the
        # lip-sync contract appended. Gemma's i2v system prompt has no reason
        # to know any of that.
        enhanced = P.storyboard.a2v_prompt(enhanced, silent=False)
    if not enhanced:
        h._json({"error": "Gemma returned empty result"}, 500); return
    # A LoRA whose trigger word the result never names would not fire — put
    # its first trigger back at the front (after the a2v law above, so the
    # word cap there cannot cut it off again).
    enhanced, triggers_added = P.ensure_lora_triggers(enhanced, lora_ctx)
    if triggers_added:
        P.push(f"[enhance] added trigger word(s): {', '.join(triggers_added)}")
    P.push(f"[enhance] → {enhanced[:120]}… ({result.get('elapsed_sec','?')}s)")
    # `enhance_prompt` was in the feature vocabulary and the /analytics/ui
    # allowlist since v4.9.7, but nothing ever sent it: the fleet showed 0
    # Enhance uses across 809 active installs (2026-09-19 -> 10-19), which is
    # "unmeasured", not "unused". Counted here, on success, where it happens.
    # It decides whether the 6 GB Gemma 3 that Enhance runs on has to be
    # downloaded before a new user can press Start at all.
    P._analytics_feature("enhance_prompt", "a2v" if a2v_mode else mode)
    h._json({
        "ok": True,
        "original": user_prompt,
        "enhanced": enhanced,
        "mode": "a2v" if a2v_mode else mode,
        "elapsed_sec": result.get("elapsed_sec"),
        # VC-13: lets the client say "Translated to English." without
        # re-deriving the same script check the toggle's visibility used.
        "source_non_latin": source_non_latin,
        "translated": bool(source_non_latin and translate),
        # 4.18.0: which LoRAs' notes the helper read, and the trigger words
        # put back — the Enhance panel says both in one line.
        "lora_notes_used": [c["name"] for c in lora_ctx if c.get("note")],
        "lora_triggers_added": triggers_added,
    })


@post("/run")
@post("/queue/add")
def post_run(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    err = P._validate_character_quality(form)
    if err:
        h._json({"error": err}, 400); return
    # The validator runs FIRST, so make_job's own refusals should be
    # unreachable here — but "should be" is not a guarantee: the two
    # seams read the same form through different code, and a character
    # deleted between the two calls is a plain TOCTOU. This endpoint has no
    # top-level handler, so an escaping CharacterRequestError becomes a
    # traceback and a dropped connection instead of the 400 the rest of
    # this endpoint answers with. The refusal is polite everywhere else;
    # it must be polite here too.
    try:
        job = P.make_job(form)
    except P.CharacterRequestError as exc:
        h._json({"error": str(exc)}, 400); return
    # 4.17.5: a job that can only fail for want of its input (Upscale or
    # Control with no clip, Image with no picture, an unknown mode) is refused
    # here, in the form's own words, instead of failing after its turn.
    refusal = P.job_input_refusal(job.get("params") or {})
    if refusal:
        h._json({"error": refusal, "code": "input_missing"}, 400); return
    # 4.19.8: a model whose licence asks for an explicit "I accept" (H3, the
    # non-commercial models) is refused with 451 until it is accepted; the
    # panel's licences.js shows the screen and re-sends this request.
    _lic = P.job_licence_refusal(job.get("params") or {})
    if _lic:
        h._json(_lic, 451); return
    # 4.17 Codex EST-10: a follow-up that can never queue is refused NOW,
    # not discovered in the log after a long Extend render.
    refusal = P.extend_face_fix_refusal(job["params"])
    if refusal:
        h._json({"error": refusal, "code": "pack_missing"}, 400); return
    # Upscale ×2 above QHD exhausted 96 GB + swap: refused before it queues.
    _up = job["params"]
    if _up.get("mode") == "upscale" and _up.get("upscale_source_path"):
        _sw, _sh = P._probe_video_dims(str(_up["upscale_source_path"]))
        refusal = P.upscale_output_refusal(_sw, _sh)
        if refusal:
            h._json({"error": refusal, "code": "hardware_tier"}, 400); return
    # "Listen to the voice only" on an install that cannot separate a voice:
    # refused NOW with an install offer, never rendered on the full mix.
    refusal = P.a2v_stem_refusal(job["params"])
    if refusal:
        h._json({"error": refusal, "code": "vocal_separator_missing"}, 400); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "id": job["id"]})


# UPSCALE & FACE FIX as a clip action: one click on a finished clip (player,
# Outputs card, history row, Editor clip bar) queues the face-safe recipe for
# that clip. Server-side so the prompt/seed come from the clip's own sidecar
# and every door queues exactly the same job.
@post("/queue/facefix")
def post_queue_facefix(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb

    def f(name: str) -> str:
        v = form.get(name, "")
        if isinstance(v, list):
            v = v[0] if v else ""
        return str(v or "").strip()

    try:
        out = P.queue_face_fix(f("path"), board_id=f("board"), clip_id=f("clip"))
    except P.CharacterRequestError as exc:
        h._json({"ok": False, "error": str(exc)}, 400); return
    except Exception as exc:                                   # noqa: BLE001
        h._json({"ok": False, "error": f"could not queue {P.FACE_FIX_NAME}: {exc}"}, 500)
        return
    h._json(out, 200 if out.get("ok") else 400)


@post("/queue/sharp_export")
def post_queue_sharp_export(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    src = (form.get("path", [""])[0] or qs.get("path", [""])[0] or "").strip()
    try:
        out = P.queue_sharp_export(src)
    except Exception as exc:                                   # noqa: BLE001
        h._json({"ok": False, "error": f"could not queue Sharp export: {exc}"}, 500)
        return
    h._json(out, 200 if out.get("ok") else 400)


# The chain held /stop as TWO arms split on ?mode=early; one path, one
# handler, the same two branches.
@post("/stop")
def post_stop(h, path, qs, ctype) -> None:
    if qs.get("mode", [""])[0] == "early":
        # STOP EARLY — distinct from the hard /stop below, which kills the
        # helper. This one touches the ABORT sentinel the runner checks
        # between forwards; it stops cleanly at the next boundary and
        # exits 75.
        #
        # The two must not be the same button: a kill leaves a half-written
        # process to reap and reports as a cancellation of unknown shape,
        # while this is the render agreeing to stop.
        #
        # A FILE, not a signal, and that is the runner's design: a sentinel
        # works across a UI, a shell, an ssh session and a supervisor
        # without any of them holding the process handle. It also means
        # this endpoint cannot half-kill a render — the worst case is a
        # file nobody reads.
        with P.LOCK:
            cur = P.STATE.get("current")
            job_id = (cur or {}).get("id")
            # A One Shot's preview lives under the PART rendering now.
            preview_id = P.live_preview_job_id(cur) if cur else ""
        if not job_id:
            h._json({"error": "nothing is rendering"}, 404); return
        expect = (qs.get("id", [""])[0] or "").strip()
        if expect and expect != job_id:          # SAFETY-3, same as below
            h._json({"ok": False, "stale": True,
                     "error": "That render already finished — nothing was stopped."}, 409); return
        d = P.live_preview_dir(preview_id)
        if not d.is_dir():
            h._json({"error": "this render has no live preview to stop "
                              "through — use Cancel."}, 409); return
        try:
            (d / "ABORT").touch()
        except OSError as exc:
            h._json({"error": f"could not write the stop sentinel: {exc}"}, 500); return
        P.push("Stop early requested — finishing the current step, then stopping.")
        h._json({"ok": True, "id": job_id}); return
    # The HARD stop: kill the helper. Still what the Stop button does.
    # SAFETY-3: `id` names the job the user confirmed; if it already
    # finished and another started, nothing is stopped (409, stale).
    expect = (qs.get("id", [""])[0] or "").strip() or None
    if not P.stop_current_job(expect_id=expect):
        h._json({"ok": False, "stale": True,
                 "error": "That render already finished — nothing was stopped."}, 409); return
    h._json({"ok": True})


@get("/a2v/estimate")
def get_a2v_estimate(h, parsed) -> None:
    """VA-06: the Audio -> Video footer showed no time estimate at all.
    Priced the same way /oneshot/estimate and /take/estimate already are —
    server-side, from the exact pipeline (Q8 two-stage vs Q4 distilled)
    and the exact clamp run_job_inner applies — so the browser computes
    nothing of its own (Structure Law)."""
    qs = P.parse_qs(parsed.query)
    try:
        frames = int((qs.get("frames", ["121"])[0] or "121"))
    except (TypeError, ValueError):
        frames = 121
    try:
        width = int((qs.get("width", ["0"])[0] or "0")) or None
    except (TypeError, ValueError):
        width = None
    try:
        height = int((qs.get("height", ["0"])[0] or "0")) or None
    except (TypeError, ValueError):
        height = None
    # Fleet first (4.17 integration): the cost model runs ~2x low for a2v
    # against the fleet's own wall clocks. ltx_a2v_price is the one pricing
    # function — the queue row reads it too (4.17 Codex EST-5).
    h._json({"ok": True, **P.ltx_a2v_price(frames, width, height)})


@get("/keyframe/estimate")
def get_keyframe_estimate(h, parsed) -> None:
    """4.17 Codex EST-7: Keyframe's footer and Shot setup summary read ONE
    price card priced at 5 s on the default canvas, whatever duration and
    aspect the form held. This prices the frames and canvas actually asked
    for (ltx_mode_price_card — clamp, pipeline and fleet range as the
    render and BOOT's first-paint card); the browser prices nothing."""
    qs = P.parse_qs(parsed.query)

    def _int(name: str) -> int | None:
        try:
            return int((qs.get(name, ["0"])[0] or "0")) or None
        except (TypeError, ValueError):
            return None
    card = P.ltx_mode_price_card("keyframe", frames=_int("frames"),
                                 width=_int("width"), height=_int("height"))
    if not card:
        h._json({"ok": False, "error": "keyframe is not available on this Mac"}); return
    h._json({"ok": True, **card})


# ====== Lip-sync keyframe helpers (VA "Continue the song") =================
# Both routes are pure reads (plus one PNG write for the anchor image) — no
# job queued, no GPU lock taken. See mlx_ltx_panel.py's mouth_open_ratio /
# find_closed_mouth_frame / extract_frame_png for the cv2 mechanics, and the
# "Keyframe + lip-sync" section of report_video-advanced.txt for why a
# lip-sync anchor is frame_idx-0-only and closed-mouth-only.
def _lipsync_media_path(raw: str):
    """LIPSYNC-1: the lip-sync helpers open a picture/clip by path and write a
    frame of it into uploads (served by /image). Same boundary as /image:
    resolve (symlinks and .. included) and require the file to live under
    outputs, uploads or state — anything else would copy private media out.
    Returns the resolved path, or None when it is outside the roots."""
    try:
        media = P.Path(raw).resolve()
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve(), P.STATE_DIR.resolve()]
    except Exception:                                            # noqa: BLE001
        return None
    if not any(media.is_relative_to(r) for r in roots):
        return None
    return media


@post("/a2v/mouth_check")
def post_a2v_mouth_check(h, path, qs, ctype) -> None:
    """Open-mouth warning for a picked lip-sync start frame. The 2026-09-22
    anchor lesson: a still with the mouth open, pinned as a strength-1.0
    frame-0 anchor, freezes the performance open-mouthed for the whole clip —
    this is the one check that catches it before a 10-25 minute render."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    img = (form.get("image", [""])[0] or "").strip()
    if not img:
        h._json({"error": "image not found"}, 400); return
    media = _lipsync_media_path(img)
    if media is None:
        h._json({"error": "pick a picture from your uploads or outputs"}, 403); return
    if not media.is_file():
        h._json({"error": "image not found"}, 400); return
    img = str(media)
    ratio = P.mouth_open_ratio(img)
    if ratio is None:
        # No face found, or cv2 unavailable — not a verdict either way.
        h._json({"ok": True, "measured": False}); return
    h._json({"ok": True, "measured": True, "ratio": round(ratio, 4),
             "open": ratio > P.MOUTH_OPEN_WARN,
             "threshold": P.MOUTH_OPEN_WARN})


@post("/a2v/continue_song")
def post_a2v_continue_song(h, path, qs, ctype) -> None:
    """"Continue the song": scans the last ~0.5s of a finished a2v clip for
    its most-closed-mouth frame, saves that frame as a new upload, and
    returns where in the song the NEXT clip should start reading. The
    picture is deliberately NOT the clip's literal last frame — a2v's own
    last frame is usually mid-syllable, and anchoring the next clip there
    pins an open mouth for its whole duration (see the "Keyframe +
    lip-sync" section of report_video-advanced.txt). Queues nothing; the
    caller uses the returned anchor_image + audio_start_time to fill the
    next lip-sync job."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    src = (form.get("clip", [""])[0] or "").strip()
    if not src:
        h._json({"error": "clip not found"}, 400); return
    media = _lipsync_media_path(src)
    if media is None:
        h._json({"error": "only a clip from your outputs or uploads can be continued"}, 403); return
    if not media.is_file():
        h._json({"error": "clip not found"}, 400); return
    src = str(media)
    hit = P.find_closed_mouth_frame(src)
    if hit is None:
        h._json({"error": "could not find a face in the last half-second of "
                           "this clip — pick a start frame by hand instead."},
                 422); return
    P.UPLOADS.mkdir(parents=True, exist_ok=True)
    dest = P.UPLOADS / f"{int(P.time.time() * 1000)}_continue_anchor.png"
    if not P.extract_frame_png(src, hit["time_s"], dest):
        h._json({"error": "could not extract the anchor frame"}, 500); return
    # Where the NEXT clip should start reading the song: the source clip's
    # own offset into the file, plus how far into the CLIP the anchor sits
    # (the anchor is picked from the last ~0.5s, not necessarily the very
    # last frame, so this is a few frames short of "start + full duration").
    src_start = 0.0
    sidecar_path = P.Path(str(src) + ".json")
    if sidecar_path.exists():
        try:
            meta = P.json.loads(sidecar_path.read_text())
            src_start = float((meta.get("params") or {}).get("audio_start_time") or 0.0)
        except Exception:                                       # noqa: BLE001
            src_start = 0.0
    h._json({
        "ok": True,
        "anchor_image": str(dest),
        "anchor_time_s": hit["time_s"],
        "anchor_openness": hit["openness"],
        "audio_start_time": round(src_start + hit["time_s"], 3),
    })


@get("/ltx/windows_plan")
def get_ltx_windows_plan(h, parsed) -> None:
    """VA-18: the REAL per-window schedule (start_sec/end_sec, count), so the
    Windows UI can render one labelled box per window ("Window 2 · 0:05-0:09")
    instead of a free-text textarea whose first typed line silently replaces
    the main prompt (ltx_windows.window_prompts: index 0 blank means "use the
    main prompt" — the UI used to invite a user to put THEIR first line
    there). Pure read: ltx_windows.plan_windows is a pure function, no job,
    no render."""
    qs = parse_qs(parsed.query)
    try:
        frames = int((qs.get("frames", ["121"])[0] or "121"))
    except (TypeError, ValueError):
        frames = 121
    frames = max(1, frames)
    import ltx_windows as _lw                                        # noqa: PLC0415
    try:
        plan = _lw.plan_windows(frames)
    except ValueError as exc:
        h._json({"error": str(exc)}, 400); return
    h._json({
        "ok": True,
        "count": plan["count"],
        "windows": [{"index": w["index"], "start_sec": w["start_sec"],
                     "end_sec": w["end_sec"]} for w in plan["windows"]],
        "notes": plan["notes"],
    })


@post("/a2v/prompt_check")
def post_a2v_prompt_check(h, path, qs, ctype) -> None:
    """Live stillness-phrase check for the lip-sync prompt box (VA-05, the
    client half — the server ALWAYS applies storyboard.a2v_prompt() before a
    render regardless of what this endpoint says, so a client that never
    calls this is still safe). Pure read: no job, no render."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    prompt = (form.get("prompt", [""])[0] or "")
    problems = P.storyboard.a2v_stillness_problems(prompt)
    h._json({
        "ok": True,
        "problems": problems,
        "cleaned": P.storyboard.a2v_clean_prompt(prompt) if problems else prompt,
    })


# VC-05 / H3-11: the third stop shape, next to hard-kill and stop-early.
# Neither of those is right mid-chain — a hard kill loses the part/window
# rendering right now AND every part/window already finished, and stop-early
# only reaches through a live preview. This sets a flag the render loop
# checks at each PART/WINDOW BOUNDARY (never mid-part, never mid-window):
# what's in flight finishes (or is asked to stop as cleanly as the engine
# allows), the loop stops there instead of starting the next one, and
# whatever is done gets published — a clean finish, not a failure.
#
# Two shapes share this one endpoint and one flag:
#   - a One Shot (params.take): run_take_job_inner joins the finished parts.
#   - a plain H3 chained render (no take, 2+ windows): run_h3_job_inner asks
#     the engine's own process group to stop at the next window boundary and
#     publishes whatever landed on disk, honestly labelled if the engine
#     can't hand back a clean partial.
# Refused outside both so the button can't appear to do something on an
# ordinary single-shot render.
@post("/stop/after_part")
def post_stop_after_part(h, path, qs, ctype) -> None:
    # SAFETY-3: `id` = the job whose dialog the user confirmed. Checked in
    # the same LOCK section that sets the flag, so a dialog left open while
    # that job finished can never flag its successor.
    expect = (qs.get("id", [""])[0] or "").strip()
    with P.LOCK:
        cur = P.STATE.get("current")
        if cur is None:
            h._json({"error": "nothing is rendering"}, 404); return
        if expect and cur.get("id") != expect:
            h._json({"ok": False, "stale": True,
                     "error": "That render already finished — nothing was stopped."}, 409); return
        cur_params = cur.get("params") or {}
        is_take = bool(cur_params.get("take"))
        window_total = (cur.get("progress") or {}).get("window_total") or 1
        is_h3_chain = (not is_take
                       and (cur_params.get("engine") or "").strip().lower() == "h3"
                       and window_total > 1)
        if not is_take and not is_h3_chain:
            h._json({"error": "this render has no parts or windows to stop "
                              "between — use Stop."}, 409); return
        if is_h3_chain and not P.H3_RUNNER_KEEPS_WINDOWS_ON_STOP:
            # 4.17 Codex review: the H3 runner stitches and writes its ONE
            # output only after the last window (generate_staged.py), so a
            # SIGTERM between windows leaves nothing to publish — promising
            # "keep what's done" would throw the finished windows away.
            h._json({"error": "this H3 engine can't keep finished windows when "
                              "stopped mid-chain yet — use Stop, or let it finish."},
                    409); return
        cur["stop_after_part"] = True
        job_id = cur.get("id")
    P.push("Stop-after-this-part requested — finishing what's in progress, "
           "then keeping what's done." if is_take else
           "Stop-after-this-window requested — asking the engine to stop "
           "before the next window, then keeping what rendered.")
    h._json({"ok": True, "id": job_id, "shape": "take" if is_take else "h3_chain"})


# VC-03: the #1 creator gesture, one click. Sourced from the clip's own
# sidecar on disk (not job history, which rotates) so New Take works on any
# clip still in the gallery, not only a recent one. The ONLY thing this
# changes from the original recipe is the seed — same prompt, size, quality,
# LoRAs, character, engine — so what comes back answers "another one like
# this" rather than "something different." Shared by the board's own New
# Take (FILM-22): both read a sidecar's `params` and replay it with seed=-1
# through this one endpoint, so the two features can't drift into two
# different ideas of what "the same recipe" means.
#
# A One Shot clip (params.take) gets the same answer at the SHOT level: the
# whole take re-runs from part 1 with a fresh seed, same beats/camera/lock/
# handoff — not a per-part reseed, which would need each part's own retake
# logic to agree on what "the same take" means and wasn't verified. See the
# take-rebuild block below for why the sidecar's take block can't be reused
# verbatim.
@post("/queue/newtake")
def post_queue_newtake(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    raw = (form.get("path", [""])[0] or qs.get("path", [""])[0] or "").strip()
    if not raw:
        h._json({"error": "path required"}, 400); return
    try:
        media = P.Path(raw).resolve()
    except Exception:
        h._json({"error": "bad path"}, 400); return
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(media.is_relative_to(r) for r in roots):
        h._json({"error": "path must resolve under outputs/ or uploads/"}, 404); return
    sidecar = media.with_suffix(media.suffix + ".json")
    if not sidecar.is_file():
        h._json({"error": "no sidecar for this clip — nothing to replay"}, 404); return
    try:
        side = P.json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, P.json.JSONDecodeError):
        h._json({"error": "sidecar is unreadable"}, 500); return
    src_params = side.get("params") or {}
    if not src_params:
        h._json({"error": "sidecar has no params to replay"}, 400); return
    # A Sharp export of a clip that had no settings of its own (an imported
    # mp4) records only {mode: t2v, sharp_export_*}: no prompt, no canvas. New
    # Take queued that as a t2v job, which died on its first missing field -
    # the bare `'stop_comfy'` in the fleet (4.19.5). Nothing to re-roll.
    if src_params.get("sharp_export_source") and not (
            src_params.get("prompt") or src_params.get("width")):
        h._json({"error": "This clip is a Sharp export of a clip with no "
                          "recorded settings, so there is nothing to re-roll."},
                400); return
    new_params = dict(src_params)
    stored_take = new_params.get("take")
    if stored_take:
        # A One Shot's New Take means what it means everywhere else: the same
        # recipe, a fresh seed, re-run from scratch — not a per-part reseed
        # (each part's own seed is derived by run_take_job_inner from the
        # parent's, and letting that reroll independently was the half-
        # verified idea this endpoint used to refuse over). The sidecar's
        # `take.parts`/`take.beats` are POST-COMPLETION fields — parts is the
        # rendered file list, beats is the resolved text list — not the
        # beat-index/part-count shape run_take_job_inner needs to start a
        # fresh take, so that shape is rebuilt from take_plan() (exactly what
        # a fresh One Shot submit does) rather than replayed as-is.
        fresh = P.take_plan(stored_take.get("seconds"), stored_take.get("engine"))
        if fresh is None:
            h._json({"error": "This One Shot's length isn't valid anymore — "
                              "use Params, then Generate, instead."}, 400); return
        stored_beat_texts = stored_take.get("beats")
        if not isinstance(stored_beat_texts, list):
            # SAFETY-11: a take joined from a stopped One Shot before this fix
            # kept the INPUT shape — `beats` a count, the text in
            # `beat_prompts` — and New Take dropped every written beat.
            stored_beat_texts = stored_take.get("beat_prompts")
        fresh["beat_prompts"] = (list(stored_beat_texts)
                                  if isinstance(stored_beat_texts, list) else [])
        fresh["light_lock"] = stored_take.get("light_lock") or ""
        fresh["retake"] = stored_take.get("retake", True) is not False
        fresh["camera"] = stored_take.get("camera") or ""
        fresh["handoff"] = stored_take.get("handoff") or "last"
        new_params["take"] = fresh
    new_params["seed"] = "-1"
    new_params.pop("seed_used", None)
    new_params["open_when_done"] = False
    new_params.pop("face_fix_result", None)
    new_params["source"] = "new_take"
    new_job = {
        "id": P._new_job_id(),
        "status": "queued",
        "queued_at": P.time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None, "finished_at": None, "elapsed_sec": None,
        "params": new_params, "raw_path": None, "output_path": None, "error": None,
    }
    # 4.19.8: a model whose licence asks for an "I accept" (model_licences.py).
    _lic = P.job_licence_refusal(new_job.get("params") or {})
    if _lic:
        h._json(_lic, 451); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(new_job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    # The real elapsed time of the clip being re-rolled — the honest ETA for
    # "about the same again", cheaper than re-deriving one from the tier table.
    h._json({"ok": True, "id": new_job["id"], "eta_sec": side.get("elapsed_sec")})


# VC-06's undo path. `{"token": ...}` (what the panel sends) restores the
# exact jobs /queue/clear removed, under their original ids — see
# P.queue_clear_with_undo for why the browser's own snapshot was wrong.
# `{"jobs": [{"params": ...}, ...]}` still re-queues arbitrary params, in
# order, with fresh ids (P._new_job_id — never a clock + small random suffix,
# which collided inside a 200-job restore). Accepts a JSON body so a queue of
# arbitrary size/shape round-trips without URL-length limits.
@post("/queue/restore")
def post_queue_restore(h, path, qs, ctype) -> None:
    try:
        length = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        h._json({"error": "invalid Content-Length"}, 400); return
    if length <= 0:
        h._json({"error": "Content-Length required for JSON body"}, 411); return
    if length > 2_000_000:
        h._json({"error": "body too large (max 2000000 bytes)"}, 413); return
    try:
        payload = P.json.loads(h.rfile.read(length).decode() or "{}")
    except (P.json.JSONDecodeError, UnicodeDecodeError):
        h._json({"error": "invalid JSON body"}, 400); return
    # SAFETY-8/9/10: the Undo toast sends the token /queue/clear returned —
    # the server puts back exactly the jobs it removed, original ids and
    # all, once. The `jobs` list form below stays for API callers.
    token = payload.get("token") if isinstance(payload, dict) else None
    if token:
        out = P.queue_restore_cleared(str(token))
        if not out.get("ok"):
            h._json({"ok": False, "error": out.get("error")}, out.get("status") or 410); return
        P.persist_queue()
        h._json(out); return
    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, list) or not jobs:
        h._json({"error": "jobs: [...] required"}, 400); return
    if len(jobs) > 200:
        h._json({"error": "too many jobs to restore at once"}, 400); return
    restored = []
    with P.QUEUE_COND:
        for entry in jobs:
            params = entry.get("params") if isinstance(entry, dict) else None
            if not isinstance(params, dict) or not params:
                continue
            new_job = {
                "id": P._new_job_id(),
                "status": "queued",
                "queued_at": P.time.strftime("%Y-%m-%d %H:%M:%S"),
                "started_at": None, "finished_at": None, "elapsed_sec": None,
                "params": dict(params), "raw_path": None, "output_path": None, "error": None,
            }
            P.STATE["queue"].append(new_job)
            restored.append(new_job["id"])
        if restored:
            P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "restored": len(restored), "ids": restored})


# VA-17: the two ways back for a One Shot that stopped or failed mid-chain.
# Both read the `one_shot_unfinished` block run_take_job_inner's recovery
# path stashes on the LAST finished part's sidecar — see the except clause
# there. `path` names that part (the un-hidden card the user is looking at).
def _load_take_manifest(h, form_or_qs) -> dict | None:
    raw = (form_or_qs.get("path", [""])[0] or "").strip()
    if not raw:
        h._json({"error": "path required"}, 400); return None
    try:
        media = P.Path(raw).resolve()
    except Exception:
        h._json({"error": "bad path"}, 400); return None
    try:
        roots = [P.OUTPUT.resolve(), P.UPLOADS.resolve()]
    except Exception:
        roots = []
    if not any(media.is_relative_to(r) for r in roots):
        h._json({"error": "path must resolve under outputs/ or uploads/"}, 404); return None
    sidecar = media.with_suffix(media.suffix + ".json")
    if not sidecar.is_file():
        h._json({"error": "no sidecar for this part"}, 404); return None
    try:
        side = P.json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, P.json.JSONDecodeError):
        h._json({"error": "sidecar is unreadable"}, 500); return None
    manifest = side.get("one_shot_unfinished")
    if not manifest or not manifest.get("outs"):
        h._json({"error": "this clip has no unfinished One Shot to resume — "
                          "it may already have been joined or resumed."}, 404); return None
    return manifest


@post("/take/resume")
def post_take_resume(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    manifest = _load_take_manifest(h, form)
    if manifest is None:
        return
    take = dict(manifest.get("take") or {})
    take["_resume"] = {
        "outs": manifest.get("outs") or [],
        # SAFETY-5/6/12: the rendered parts' own files, their seeds, and the
        # speech handoff's pending tail; the handoff frame is rebuilt by the
        # take itself from the last part, so `last_png` is informational.
        "visible": manifest.get("visible") or [],
        "part_seeds": manifest.get("part_seeds"),
        "tail_wav": manifest.get("tail_wav"),
        "last_png": manifest.get("last_png"),
        "start_k": manifest.get("done_parts"),
    }
    base_params = dict(manifest.get("p") or {})
    base_params["take"] = take
    base_params["label"] = manifest.get("label") or base_params.get("label") or ""
    base_params["open_when_done"] = False
    new_job = {
        "id": P._new_job_id(),
        "status": "queued",
        "queued_at": P.time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None, "finished_at": None, "elapsed_sec": None,
        "params": base_params, "raw_path": None, "output_path": None, "error": None,
    }
    # 4.19.8: a model whose licence asks for an "I accept" (model_licences.py).
    _lic = P.job_licence_refusal(new_job.get("params") or {})
    if _lic:
        h._json(_lic, 451); return
    with P.QUEUE_COND:
        P.STATE["queue"].append(new_job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "id": new_job["id"],
             "resumed_from_part": manifest.get("done_parts"),
             "total_parts": manifest.get("total_parts")})


@post("/take/join_partial")
def post_take_join_partial(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    manifest = _load_take_manifest(h, form)
    if manifest is None:
        return
    outs = [str(o) for o in (manifest.get("outs") or []) if o and P.Path(o).is_file()]
    if not outs:
        h._json({"error": "none of this take's parts are still on disk"}, 404); return
    label = manifest.get("label") or ""
    prompt = ((manifest.get("p") or {}).get("prompt")) or ""
    take = manifest.get("take") or {}
    final = P._unique_output_path(
        P.OUTPUT,
        P._descriptive_filename(label, prompt, fallback="take")
        + f"_take{take.get('seconds', len(outs) * 10)}s_partial")
    # SAFETY-7: this join runs on the HTTP thread, outside the queue. It used
    # `mux_pgid` — the generation queue's slot — so a join overlapping a
    # queued render's encode overwrote that render's registration (Stop then
    # killed the join and left the encode running, unstoppable). It has its
    # own slot now, and one join at a time owns it.
    if not _TAKE_JOIN_LOCK.acquire(blocking=False):
        h._json({"error": "Another “Join what’s done” is still running — "
                          "try again when it finishes."}, 409); return
    try:
        P._join_take_parts(str(P.FFMPEG), outs, final, job=None,
                           pgid_key="take_join_pgid")
    except Exception as exc:                                        # noqa: BLE001
        h._json({"error": f"join failed: {exc}"}, 500); return
    finally:
        _TAKE_JOIN_LOCK.release()
    # SAFETY-11: the joined clip's take block has the COMPLETED shape (what a
    # finished One Shot writes): `beats` is the beat TEXT, not the manifest's
    # input-shaped count — New Take and Load Params read it as text.
    beat_texts = take.get("beat_prompts") if isinstance(take.get("beat_prompts"), list) \
        else (take.get("beats") if isinstance(take.get("beats"), list) else [])
    seeds = manifest.get("part_seeds") if isinstance(manifest.get("part_seeds"), list) else []
    done_take = {**{k: v for k, v in take.items() if k != "_resume"},
                 "beats": list(beat_texts), "parts": outs, "stopped_early": True,
                 "joined_partial": True,
                 "parts_meta": [{"seed_used": sd} for sd in seeds]}
    side = {
        "output": str(final), "raw_output": str(final), "native_output": str(final),
        "mode": (manifest.get("p") or {}).get("mode", "t2v"),
        "engine": manifest.get("engine"), "prompt": prompt,
        "label": f"{label or 'one shot'} · {len(outs)} of {manifest.get('total_parts')} parts (stopped early)",
        "take": done_take,
        "params": {**(manifest.get("p") or {}), "take": done_take},
    }
    P.write_sidecar(final.with_suffix(final.suffix + ".json"), side)
    # The joined file is now the canonical output; the raw parts go back to
    # being hidden working files, same as a take that finished normally —
    # the rendered cards (SAFETY-5) as well as any speech intermediates.
    for o in dict.fromkeys(outs + [str(v) for v in (manifest.get("visible") or []) if v]):
        try:
            P.set_hidden(o, True)
        except Exception:                                            # noqa: BLE001
            pass
    h._json({"ok": True, "output": str(final), "parts_joined": len(outs)})
