"""/queue family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


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
            "current": P.STATE["current"], "queue": P.STATE["queue"],
            "history": P.STATE["history"][:P.HISTORY_API_LIMIT], "log": P.STATE["log"],
            "pid": P.STATE["pid"], "pgid": P.STATE["pgid"],
        })
        hidden_count = len(P.HIDDEN_PATHS)
    # Polling fast path — newest 60 only. outputs_total tells the
    # carousel header "X older outputs not shown" so the "Show all"
    # button can surface them via the /outputs endpoint.
    _outs, _outs_total = P.list_outputs(
        include_hidden=include_hidden, limit=60, return_total=True,
    )
    payload["outputs"] = _outs
    payload["outputs_total"] = _outs_total
    payload["hidden_count"] = hidden_count
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
        if params.get("mode") == "image":
            return float(avg_image) if avg_image else 90.0
        return float(avg_video) if avg_video else 420.0
    payload["eta_sec"] = round(sum(_eta_for(j) for j in payload["queue"]))
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
        if _mode != "train" and _engine != "h3":
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
    # Repo-level counts for the header pill — granular view that
    # matches the modal's per-repo rows (Q4 + Gemma + Q8 = 3 in the
    # default manifest). Avoids the pill claiming "2/2 ready" while
    # the modal shows three rows.
    _repo_snap = P.repo_status_list()
    payload["repos_total"] = len(_repo_snap)
    payload["repos_ready"] = sum(1 for r in _repo_snap if r.get("complete"))
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
        "times": P.SYSTEM_CAPS.get("times", {}),
    }
    # Hailuo H3 — the optional second video engine. Re-read every tick
    # (it's a handful of stat() calls) so an install finishing in the
    # Pinokio sidebar unlocks the engine pill without a panel restart,
    # exactly like the Q8 download already does.
    payload["h3"] = P.h3_status()
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
            payload["download"] = {
                "active": True,
                "key": P.DOWNLOAD["key"],
                "repo_id": P.DOWNLOAD["repo_id"],
                "started_ts": P.DOWNLOAD["started_ts"],
                "last_line": P.DOWNLOAD["last_line"],
            }
        else:
            payload["download"] = {"active": False}
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
    # Defense-in-depth: re-queuing a historical job with character_id
    # + balanced quality (or a raw train_character LoRA + non-high
    # quality) reproduces the bug /queue/add already rejects. Wrap
    # src_params into the form-shape the validator expects: it reads
    # `character_id`, `quality`, and `loras`. The first two are
    # already strings; `loras` lives in src_params as a parsed list
    # and needs to be re-encoded as JSON so parse_loras_from_form
    # round-trips it cleanly.
    _retry_form = {
        "character_id": src_params.get("character_id") or "",
        "quality": src_params.get("quality") or "",
        "loras": P.json.dumps(src_params.get("loras") or []),
        "engine": src_params.get("engine") or P.ENGINE_DEFAULT,
        "mode": src_params.get("mode") or "t2v",
        "no_voice": src_params.get("no_voice") or "off",
    }
    err = P._validate_character_quality(_retry_form)
    if err:
        h._json({"error": err}, 400); return
    # Build a fresh job — copy params verbatim, mint a new id, drop
    # any open_when_done flag (retries are usually background; user
    # is mid-other-work and doesn't want the OS jumping windows).
    new_job = {
        "id": f"j-{int(P.time.time() * 1000):x}-{P.random.randrange(0xfff):03x}",
        "status": "queued",
        "queued_at": P.time.strftime("%Y-%m-%d %H:%M:%S"),
        "started_at": None,
        "finished_at": None,
        "elapsed_sec": None,
        "params": dict(src_params),
        "raw_path": None,
        "output_path": None,
        "error": None,
    }
    new_job["params"]["open_when_done"] = False
    new_job["params"]["source"] = "retry"
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


@post("/queue/clear")
def post_queue_clear(h, path, qs, ctype) -> None:
    with P.LOCK:
        count = len(P.STATE["queue"])
        P.STATE["queue"] = []
    P.persist_queue()
    h._json({"cleared": count}); return


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
    if mode not in ("t2v", "i2v"):
        mode = "t2v"
    if not user_prompt:
        h._json({"error": "no prompt provided"}, 400); return
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
    preserve_tokens = sorted(preserve_set)
    P.push(f"[enhance] {mode}: {user_prompt[:80]}…"
         + (f"  preserve={preserve_tokens}" if preserve_tokens else ""))
    try:
        result = P.HELPER.run({
            "action": "enhance_prompt",
            "id": f"enh-{int(P.time.time()*1000)}",
            "params": {"prompt": user_prompt, "mode": mode, "seed": 10,
                       "preserve_tokens": preserve_tokens},
        }, timeout=P.PROMPT_ENHANCE_TIMEOUT)
    except Exception as exc:
        P.push(f"[enhance] failed: {exc}")
        h._json({"error": str(exc)}, 500); return
    # A malformed terminal helper event must still become JSON. Before
    # this guard, None/non-string values raised after the only try/except
    # in the lane and BaseHTTPRequestHandler closed the socket empty.
    if not isinstance(result, dict):
        h._json({"error": "Gemma returned an invalid helper response"}, 500); return
    enhanced_raw = result.get("enhanced", "")
    if not isinstance(enhanced_raw, str):
        h._json({"error": "Gemma returned an invalid enhanced prompt"}, 500); return
    enhanced = enhanced_raw.strip()
    if not enhanced:
        h._json({"error": "Gemma returned empty result"}, 500); return
    P.push(f"[enhance] → {enhanced[:120]}… ({result.get('elapsed_sec','?')}s)")
    h._json({
        "ok": True,
        "original": user_prompt,
        "enhanced": enhanced,
        "mode": mode,
        "elapsed_sec": result.get("elapsed_sec"),
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
    with P.QUEUE_COND:
        P.STATE["queue"].append(job)
        P.QUEUE_COND.notify_all()
    P.persist_queue()
    h._json({"ok": True, "id": job["id"]})


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
        if not job_id:
            h._json({"error": "nothing is rendering"}, 404); return
        d = P.live_preview_dir(job_id)
        if not d.is_dir():
            h._json({"error": "this render has no live preview to stop "
                              "through — use Cancel."}, 409); return
        try:
            (d / "ABORT").touch()
        except OSError as exc:
            h._json({"error": f"could not write the stop sentinel: {exc}"}, 500); return
        P.push("Stop early requested — finishing the current step, then stopping.")
        h._json({"ok": True, "id": job_id}); return
    # The HARD stop: kill the helper. Unchanged, and still what the
    # Stop button does.
    P.stop_current_job()
    h._json({"ok": True})
