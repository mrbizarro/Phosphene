"""/models family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, post

P = None  # the running mlx_ltx_panel module; assigned at wiring time


@post("/models/download")
def post_models_download(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    # POST { repo_key: "q4" | "gemma" | "q8" }
    # Validates the key against required_files.json (so the user
    # can't trick the panel into running `hf download` on an
    # arbitrary repo by faking the form). One slot — return 409
    # if a download is already in progress.
    key = (form.get("repo_key", [""])[0] or "").strip()
    repo = next((r for r in P._repos() if r.get("key") == key), None)
    if not repo:
        h._json({"error": f"unknown repo key: {key!r}. Valid keys: "
                             f"{[r['key'] for r in P._repos()]}"}, 400); return
    # A GitHub-release-mirrored pack does not go through `hf` at all
    # (scripts/fetch_pack_release.py is stdlib-only), so a missing hf
    # binary must not block the one lane that never needed it.
    if P.HF_BIN is None and (repo.get("mirror") or {}).get("kind") != "github-release":
        h._json({"error": "hf binary not found. Reinstall Phosphene "
                             "or install huggingface_hub>=1.0 in the venv."}, 500); return
    with P.DOWNLOAD_LOCK:
        if P.DOWNLOAD["active"]:
            h._json({"error": f"another download is in progress: "
                                 f"{P.DOWNLOAD['repo_id']}. Wait for it to finish "
                                 f"(or click Cancel)."}, 409); return
        P.DOWNLOAD["active"] = True
        P.DOWNLOAD["key"] = key
        P.DOWNLOAD["repo_id"] = repo["repo_id"]
        P.DOWNLOAD["started_ts"] = P.time.time()
        P.DOWNLOAD["last_line"] = "starting…"
    P.threading.Thread(target=P._download_thread, args=(repo,), daemon=True).start()
    h._json({"ok": True, "key": key, "repo_id": repo["repo_id"]}); return


@post("/models/remove")
def post_models_remove(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    # POST { repo_key } — free the space a retired pack is holding.
    #
    # It REFUSES THREE THINGS, server-side, and says which. This is the
    # one endpoint in the panel that deletes multi-GB weights, so the
    # refusals are here rather than in the UI that calls it: a stale
    # tab, a replayed request or a curl must hit the same wall the
    # button does.
    #
    # And it NEVER TAKES A PATH FROM THE CLIENT. Every branch resolves
    # its own paths out of the registry from a key, so the worst a
    # malicious caller can do is delete a pack the registry names —
    # which is exactly what the button does.
    key = (form.get("repo_key", [""])[0] or "").strip()

    # (2) a download in flight is using that folder.
    with P.DOWNLOAD_LOCK:
        busy_key = P.DOWNLOAD.get("key") if P.DOWNLOAD["active"] else None

    targets: list[P.Path] = []
    label = ""
    if key == "music":
        with P.LOCK:
            jobs = [P.STATE.get("current")] + list(P.STATE.get("queue") or [])
            if any(j and (j.get("params") or {}).get("engine") == "music" for j in jobs):
                h._json({"error": "Music is queued or rendering — stop or remove those jobs first."}, 409); return
        targets = [P.MUSIC_MODELS] if P.MUSIC_MODELS.is_dir() else []
        label = "YuE2 weights"
    elif key.startswith("version:"):
        vid = key.split(":", 1)[1]
        ver = next((v for v in P.MODEL_VERSIONS if v["id"] == vid), None)
        if not ver:
            h._json({"error": f"unknown model version: {vid!r}"}, 400); return
        # (1) never the model this build renders with — and never the
        # REGISTRY DEFAULT either. Pinned back with
        # LTX_MODEL_VERSION=ltx23, `active` is ltx23, so this check
        # alone would have happily deleted 80 GB of 2.5: the generation
        # the product ships with, that the very next unset of that env
        # var boots into with no weights at all.
        if vid == P.ACTIVE_MODEL_VERSION:
            h._json({"error": "That's the model this build renders with."}, 400); return
        if (next((v for v in P.MODEL_VERSIONS if v["id"] == vid), {}) or {}).get("default"):
            h._json({"error": "That's the generation this build boots "
                                 "into by default — it is only inactive "
                                 "because LTX_MODEL_VERSION is set."}, 400); return
        for pack in ver.get("packs", ()):
            if busy_key and P._quant_for_repo_key(busy_key, vid) == pack["quant"]:
                h._json({"error": "A download is using that folder — "
                                     "cancel it first."}, 409); return
            p = P.Path(pack["path"])
            if p.is_dir():
                targets.append(p)
        label = f"{ver['label']}"
    else:
        repo = next((r for r in P._repos() if r.get("key") == key), None)
        # (3) not registered.
        if not repo:
            h._json({"error": f"unknown repo key: {key!r}"}, 400); return
        if busy_key == key:
            h._json({"error": "A download is using that folder — "
                                 "cancel it first."}, 409); return
        addon_key = P.model_version().get("hq_addon_repo_key")
        if key == addon_key:
            # The add-on has no directory of its own: delete exactly the
            # two files it consists of, BY NAME. A directory walk here
            # would delete the q8 pack.
            base = P.pack_path("q8")
            targets = [base / n for n in P.hq_weights().values()]
            label = f"{P.model_version()['label']} High add-on"
        else:
            _q = P._quant_for_repo_key(key)
            if _q and _q in P.version_quants():
                h._json({"error": "That's the model this build renders with."}, 400); return
            if key == "gemma":
                h._json({"error": "Gemma 3 is also what Enhance and the "
                                     "Storyboard planner run on."}, 400); return
            # A directory SHARED by several repos must never be
            # rmtree'd by key: the three IC-LoRAs all resolve
            # mlx_models/loras/ic, so removing one would take all
            # three. When the directory is shared, delete exactly this
            # repo's own declared files — the same rule the HQ add-on
            # branch above follows, and for the same reason.
            p = P.ROOT / repo["local_dir"]
            shared = sum(1 for o in P._repos()
                         if str(o.get("local_dir")) == str(repo.get("local_dir"))) > 1
            if shared:
                targets = [p / f for f in (repo.get("files") or [])]
            elif p.is_dir():
                targets.append(p)
            label = repo.get("name") or key

    freed = 0
    for t in targets:
        try:
            if t.is_dir():
                freed += P._dir_size_bytes(t)
                P.shutil.rmtree(t)
            elif t.is_file():
                freed += t.stat().st_size
                t.unlink()
        except OSError as exc:
            P.push(f"[storage] could not remove {t}: {exc}")
            h._json({"error": f"could not remove {t.name}: {exc}"}, 500); return
    P.push(f"[storage] removed {label} — {P._fmt_gb(freed)} freed")
    h._json({"ok": True, "freed": freed, "freed_label": P._fmt_gb(freed),
                "label": label}); return


@post("/models/cancel")
def post_models_cancel(h, path, qs, ctype) -> None:
    # Best-effort kill — the next status poll will see active=False.
    # `cancelled` is what stops the worker's retry loop (INST-07): killing the
    # process alone made the attempt look failed and the worker started the
    # next one. The slot itself stays held until the worker releases it, so a
    # new download cannot start and be wiped by the old worker's cleanup.
    with P.DOWNLOAD_LOCK:
        was_active = P.DOWNLOAD["active"]
        rid = P.DOWNLOAD.get("repo_id")
        if was_active:
            P.DOWNLOAD["cancelled"] = True
    if not was_active:
        h._json({"error": "no active download"}, 404); return
    P._kill_active_download()
    P.push(f"[hf] cancel requested for {rid}")
    h._json({"ok": True}); return


@post("/models/verify")
def post_models_verify(h, path, qs, ctype) -> None:
    # On-demand structural integrity re-scan of installed weights.
    h._json(P._model_integrity(force=True)); return


@post("/models/verify-deep")
def post_models_verify_deep(h, path, qs, ctype) -> None:
    # Deep (checksum) re-scan: hash every installed weight + compare to
    # the published upstream SHA-256. Slow (~1-2 min/repo), so it runs in
    # a daemon thread and the client polls /status.deep_verify. Catches
    # right-size-but-wrong-content weights — the residual "mosaic" cause
    # header+size can't see (GitHub #18 / #5).
    with P._DEEP_VERIFY_LOCK:
        if P._DEEP_VERIFY["active"]:
            h._json({"ok": True, "active": True,
                        "progress": P._DEEP_VERIFY["progress"]}); return
        P._DEEP_VERIFY["active"] = True
        P._DEEP_VERIFY["result"] = None
        P._DEEP_VERIFY["started_ts"] = P.time.time()
        P._DEEP_VERIFY["progress"] = "starting…"
    P.threading.Thread(target=P._deep_verify_thread, daemon=True).start()
    h._json({"ok": True, "active": True}, 202); return


@post("/models/repair")
def post_models_repair(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    P.h3_status_invalidate()   # the 3 s /status memo must not outlive an install action
    # Re-download corrupt/partial weight files for a repo. We DELETE the
    # bad files first (hf skips files it believes are already complete,
    # so a same-size-but-corrupt file would never be re-fetched), then
    # run the normal resumable download. POST { repo_key }.
    key = (form.get("repo_key", [""])[0] or "").strip()
    repo = next((r for r in P._repos() if r.get("key") == key), None)
    if not repo:
        h._json({"error": f"unknown repo key: {key!r}"}, 400); return
    bad = [b["file"] for b in P._model_integrity(force=True)["bad"] if b["repo"] == key]
    # Union in deep (checksum) mismatches for this repo — right-size,
    # wrong-content files that header+size verification can't detect.
    with P._DEEP_VERIFY_LOCK:
        _dv_bad = (P._DEEP_VERIFY.get("result") or {}).get("bad", [])
    for _b in _dv_bad:
        if _b.get("repo") == key and _b.get("file") not in bad:
            bad.append(_b["file"])
    if not bad:
        h._json({"ok": True, "nothing_to_repair": True,
                    "note": f"no corrupt files detected for {key!r}"}); return
    if P.HF_BIN is None and (repo.get("mirror") or {}).get("kind") != "github-release":
        h._json({"error": "hf binary not found. Reinstall Phosphene."}, 500); return
    # CLAIM THE SLOT FIRST, delete second. This deleted the files and only
    # then found another download running — answering 409 "Wait or Cancel"
    # with nothing started to replace what it had just removed (Codex INST-11).
    with P.DOWNLOAD_LOCK:
        if P.DOWNLOAD["active"]:
            h._json({"error": f"another download is in progress: "
                                 f"{P.DOWNLOAD['repo_id']}. Wait or Cancel."}, 409); return
        P.DOWNLOAD["active"] = True
        P.DOWNLOAD["key"] = key
        P.DOWNLOAD["repo_id"] = repo["repo_id"]
        P.DOWNLOAD["started_ts"] = P.time.time()
        P.DOWNLOAD["last_line"] = "repairing…"
    # The deep-check findings for this repo are CONSUMED by this repair. They
    # carry no file identity, so left in place a second Repair — after this
    # one had fetched good copies — would delete the now-valid files again.
    # A fresh deep check re-finds anything still wrong.
    with P._DEEP_VERIFY_LOCK:
        _dv = P._DEEP_VERIFY.get("result")
        if isinstance(_dv, dict) and _dv.get("bad"):
            _dv["bad"] = [b for b in _dv["bad"] if b.get("repo") != key]
    target = P.Q8_LOCAL_PATH if key == "q8" else (P.ROOT / repo["local_dir"])
    deleted = []
    for fname in bad:
        for cand in {P.Path(target) / fname, P.ROOT / repo["local_dir"] / fname}:
            try:
                if cand.exists():
                    cand.unlink()
                    if fname not in deleted:
                        deleted.append(fname)
            except OSError:
                pass
    with P._INTEGRITY_LOCK:           # bust the cache so the next scan re-checks
        P._INTEGRITY_CACHE["ts"] = 0.0
    P.push(f"[repair] {key}: deleted {len(deleted)} corrupt/partial file(s) "
         f"({', '.join(deleted)}) — re-downloading.")
    P.threading.Thread(target=P._download_thread, args=(repo,), daemon=True).start()
    h._json({"ok": True, "deleted": deleted, "repo_id": repo["repo_id"]}, 202); return


@post("/music/install")
def post_music_install(h, path, qs, ctype) -> None:
    # Audio → Compose's "Install music engine" / "Repair music engine". Runs
    # install_music.js's own steps as a background task; progress rides
    # /status.music_install. Resumable: every step skips finished work.
    _rb = h._read_form_body()
    if _rb is None:
        return
    _body, form = _rb
    kind = "repair" if (form.get("kind", [""])[0] or "") == "repair" else "install"
    code, payload = P.music_install_start(kind)
    h._json(payload, code); return


@post("/music/cover/install")
def post_music_cover_install(h, path, qs, ctype) -> None:
    # The opt-in ~2.8 GB that lets YuE2 read a score off a real recording.
    # Separate from /music/install because the engine works without it.
    code, payload = P.music_cover_fetch_start()
    h._json(payload, code); return


@get("/music/lora/status")
def get_music_lora_status(h, parsed) -> None:
    # What the Voice / style picker can offer right now, plus whatever the
    # background download is doing. /status carries the same block for the
    # first paint; this route is what the picker polls while fetching.
    h._json({**P.music_lora_status(), "fetch": P.music_lora_fetch_state()}); return


@post("/music/lora/fetch")
def post_music_lora_fetch(h, path, qs, ctype) -> None:
    # The opt-in ~210 MB that makes Instrumental a real adapter instead of a
    # prompt convention. Separate from /music/install: the engine writes songs
    # without it, and a user who never asks for one never pays for it.
    code, payload = P.music_lora_fetch_start()
    h._json(payload, code); return


@post("/music/install/stop")
def post_music_install_stop(h, path, qs, ctype) -> None:
    if not P.music_install_stop():
        h._json({"error": "no music engine install is running"}, 404); return
    P.push("[music-install] stop requested")
    h._json({"ok": True}); return
