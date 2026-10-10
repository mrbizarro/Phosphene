"""/meta family routes — moved out of the chain (slice 4).

Bodies are verbatim from mlx_ltx_panel.py's do_GET/do_POST chains except
the two mechanical renames the move forces: `self` -> `h`, and panel
globals -> `P.<name>`. See panel/routes_stats.py for the pattern and
panel/__init__.py for why P is assigned rather than imported.
"""
from __future__ import annotations

from urllib.parse import parse_qs

from panel.routes import get, get_when, post, post_when

P = None  # the running mlx_ltx_panel module; assigned at wiring time


# Codex UI-6: the crash-report filter (SYS-12) matched on the FILE NAME —
# "python3*" / "python*" / "phosphene*" — so any other Python program that
# crashed on the Mac (another app's script, a notebook kernel, a CLI tool)
# was counted in the modal and zipped verbatim into a Phosphene bug report,
# its own paths and content included. A name says which interpreter
# crashed, not whose process it was. The reliable signal is inside the
# report: an .ips body lists `procPath` and every loaded image
# (`usedImages[].path`), and a Phosphene process always runs a binary or
# loads extension modules (mlx, the engines' venvs) from under this
# install. A report that shows nothing under these roots is ambiguous and
# is left out — the opt-in checkbox never attaches it.
_CRASH_NAME_PREFIXES = ("python3", "python", "phosphene")
_CRASH_MAX_READ_BYTES = 32 * 1024 * 1024


def _phosphene_crash_roots() -> tuple[str, ...]:
    roots: set[str] = set()
    for name in ("ROOT", "H3_ROOT", "LORA_LAB_ROOT"):
        base = getattr(P, name, None)
        if not base:
            continue
        for cand in (P.Path(base), P.Path(base) / "ltx-2-mlx"):
            for form in (cand, cand.resolve() if cand.exists() else None):
                if form is not None and str(form) not in ("", "/"):
                    roots.add(str(form).rstrip("/") + "/")
    return tuple(sorted(roots))


def _is_phosphene_crash(path, roots: tuple[str, ...]) -> bool:
    """True only when the .ips shows a process of THIS install."""
    try:
        if path.stat().st_size > _CRASH_MAX_READ_BYTES:
            return False
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    _header, _, body = text.partition("\n")
    try:
        doc = P.json.loads(body)
    except ValueError:
        return False
    if not isinstance(doc, dict):
        return False
    paths = [doc.get("procPath")]
    images = doc.get("usedImages")
    if isinstance(images, list):
        paths.extend(i.get("path") for i in images if isinstance(i, dict))
    return any(isinstance(p, str) and p.startswith(roots) for p in paths)


def _phosphene_crash_reports(diag) -> list:
    """This install's own crash reports in `diag`, newest first."""
    roots = _phosphene_crash_roots()
    if not roots:
        return []
    cands = [p for p in diag.iterdir()
             if p.is_file() and p.suffix.lower() == ".ips"
             and p.name.lower().startswith(_CRASH_NAME_PREFIXES)]
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [p for p in cands if _is_phosphene_crash(p, roots)]


@get("/sw.js")
def get_service_worker(h, parsed) -> None:
    """The push service worker, served from the ROOT so its scope covers the
    whole panel — a worker under /webapp/ could only wake for /webapp/."""
    p = (P.ROOT / "webapp" / "sw.js")
    if not p.is_file():
        h.send_error(404); return
    body = p.read_bytes()
    h.send_response(200)
    h.send_header("Content-Type", "text/javascript; charset=utf-8")
    h.send_header("Cache-Control", "no-cache")
    h.send_header("Service-Worker-Allowed", "/")
    h.send_header("Content-Length", str(len(body)))
    h.end_headers()
    h.wfile.write(body)


@get("/push/key")
def get_push_key(h, parsed) -> None:
    keys = P._vapid_keys() if P.push_available() else None
    if not keys:
        h._json({"ok": False, "available": False,
                    "error": "push is not available on this install"}, 503)
        return
    h._json({"ok": True, "available": True, "public_key": keys["public"],
                "subscriptions": len(P._push_subs())})


@post("/push/subscribe")
def post_push_subscribe(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    raw = form.get("subscription", [""])[0] if isinstance(form.get("subscription"), list) else (form.get("subscription") or "")
    try:
        sub = P.json.loads(raw)
    except (TypeError, ValueError):
        sub = None
    if not isinstance(sub, dict) or not sub.get("endpoint") or not isinstance(sub.get("keys"), dict):
        h._json({"ok": False, "error": "a PushSubscription JSON is required"}, 400)
        return
    subs = [s for s in P._push_subs() if s.get("endpoint") != sub["endpoint"]]
    subs.append({"endpoint": sub["endpoint"], "keys": sub["keys"],
                 "expirationTime": sub.get("expirationTime")})
    P._push_save_subs(subs)
    P.push(f"[push] a browser subscribed ({len(subs)} listening)")
    h._json({"ok": True, "subscriptions": len(subs)})


@post("/push/unsubscribe")
def post_push_unsubscribe(h, path, qs, ctype) -> None:
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    ep = form.get("endpoint", [""])[0] if isinstance(form.get("endpoint"), list) else (form.get("endpoint") or "")
    subs = [s for s in P._push_subs() if s.get("endpoint") != ep]
    P._push_save_subs(subs)
    h._json({"ok": True, "subscriptions": len(subs)})


@post("/push/test")
def post_push_test(h, path, qs, ctype) -> None:
    n = P.push_notify("Phosphene", "This is what a finished render will say.", tag="phos-test")
    h._json({"ok": n > 0, "sent": n,
                "error": None if n else "no browser is subscribed, or the push could not be delivered"})


@get("/docs/prompting")
def get_docs_prompting(h, parsed) -> None:
    """docs/PROMPTING.md on a page (webapp/prompting.html) with one button:
    copy the whole guide to paste into your own assistant. The page's markup
    lives in the template; this only fills the seam."""
    import html as _html
    md = P.ROOT / "docs" / "PROMPTING.md"
    tpl = P.ROOT / "webapp" / "prompting.html"
    if not md.is_file() or not tpl.is_file():
        h.send_error(404); return
    page = tpl.read_text().replace("__GUIDE_MD__", _html.escape(md.read_text()))
    h._ok(page.encode())


@get("/take/estimate")
def get_take_estimate(h, parsed) -> None:
    """What a take of this length costs on this Mac: beats, parts, minutes.
    `?engine=h3|ltx&quality=<engine quality key>&seconds=<TAKE_SECONDS>`."""
    qs = parse_qs(parsed.query)
    engine = (qs.get("engine", ["ltx"])[0] or "ltx").strip().lower()
    quality = (qs.get("quality", [""])[0] or "").strip().lower()
    seconds = (qs.get("seconds", ["0"])[0] or "0").strip()
    plan = P.take_plan(seconds, engine)
    if not plan:
        h._json({"ok": False, "error": f"seconds must be one of {list(P.TAKE_SECONDS)}",
                    "choices": list(P.TAKE_SECONDS)}, 400)
        return
    minutes = P.take_estimate_minutes(engine, quality, seconds)
    # `parts` on both engines: H3 15 s parts, LTX 10 s parts (last-frame
    # handoff, see take_plan). An LTX take is ordinary distilled renders, so
    # it needs no Q8 pack any more — only an HQ quality would, and that is
    # the quality chip's own gate.
    h._json({"ok": True, "seconds": plan["seconds"], "beats": plan["beats"],
                "parts": len(plan["parts"]), "beats_per_part": plan["beats_per_part"],
                "part_frames": plan["part_frames"], "engine": plan["engine"],
                "frames": plan["frames"],
                "minutes": minutes, "eta": (P._fmt_eta(minutes) if minutes else None),
                "needs_q8": False})


@get("/panel/bug-context")
def get_panel_bug_context(h, parsed) -> None:
    # Sysinfo + log tail bundle for the bug-report modal. The browser
    # has no access to sysctl / sw_vers / git, so we collect everything
    # server-side and ship it back as a single JSON payload. Used to
    # pre-fill the description textarea in openBugModal().
    try:
        # The build the PROCESS is running, never the working tree.
        # A report filed from a panel that has not been restarted has
        # to name the code that produced the bug — and when disk has
        # moved on, say so in the report, because that is frequently
        # the bug ("the fix didn't work" from a panel still serving
        # the code from before the fix).
        _boot = P.boot_build_stamp()
        _live = P.get_version_state()
        ver = _boot["version"] or P._read_local_version() or "unknown"
        sha = _boot["short"] or ""
        branch = _boot["branch"] or ""
        if _live.get("stale_process"):
            sha = (f"{sha or '?'} (RUNNING; disk is at "
                   f"{_live.get('disk_short') or '?'} — panel not "
                   f"restarted since the update)")
        mac_ver = P.hostinfo.os_version()
        hw_model = P.hostinfo.hw_model()
        mem = P.get_memory()
        ram_gb = round(float(mem.get("total_gb") or 0.0))
        with P.LOCK:
            tail = list(P.STATE["log"])[-50:]
        # SYS-12, privacy: this pre-fills the bug-report textarea that
        # opens a PUBLIC GitHub issue. Log lines routinely carry
        # /Users/<username>/... paths (panel_uploads, output paths), and
        # the macOS username is often the person's real name. Scrub before
        # it ever reaches the browser, not after.
        tail = [P._bug_report_scrub_line(line) for line in tail]
        # Crash count — the modal hides the "Include crash reports"
        # checkbox when zero so the user doesn't see an empty zip.
        # SYS-12: counted every .ips on the Mac, regardless of which process
        # crashed — mismatched what /panel/bug-report actually zips (now
        # filtered to python3*/Python*/phosphene*; see there). Same filter
        # here so the modal's count matches what "Include crash reports"
        # would actually attach.
        crash_count = 0
        try:
            diag = P.Path.home() / "Library" / "Logs" / "DiagnosticReports"
            if diag.is_dir():
                # UI-6: the SAME positive-identification filter the zip uses.
                crash_count = len(_phosphene_crash_reports(diag))
        except OSError:
            pass
        h._json({
            "version": ver,
            "commit": sha,
            "branch": branch or "unknown",
            "macOS": mac_ver,
            "hwModel": hw_model,
            "ramGB": ram_gb,
            "logTail": tail,
            "crashCount": crash_count,
        })
    except Exception as exc:                                # noqa: BLE001
        h._json({"error": f"context build failed: {exc}"}, 500)


@get("/storage")
def get_storage(h, parsed) -> None:
    # What can be reclaimed on this disk. Walks the real directories, so
    # it is deliberately NOT part of /status (which polls every 1.5 s).
    # The Settings modal asks for it on open, which is the only moment
    # anyone looks.
    h._json(P.storage_payload())


@get("/version")
def get_version(h, parsed) -> None:
    # Snapshot of the version-check state. Cheap (just a dict copy
    # under a lock); the UI polls this every ~5 minutes to render
    # the "Update available" pill in the header.
    h._json(P.get_version_state())


@get("/models")
def get_models(h, parsed) -> None:
    # Per-repo status snapshot for the Models modal in the UI.
    # Same data the menu/install rely on, just shaped per-repo so
    # the front-end can render rows without re-aggregating.
    payload = {
        "repos": P.repo_status_list(),
        "hf_available": P.HF_BIN is not None,
        "hf_path": str(P.HF_BIN) if P.HF_BIN else None,
    }
    with P.DOWNLOAD_LOCK:
        payload["active_download"] = (
            {"key": P.DOWNLOAD["key"], "repo_id": P.DOWNLOAD["repo_id"],
             "started_ts": P.DOWNLOAD["started_ts"], "last_line": P.DOWNLOAD["last_line"]}
            if P.DOWNLOAD["active"] else None
        )
    h._json(payload)


@get("/settings")
def get_settings(h, parsed) -> None:
    # Return current panel settings + the preset table so the UI
    # can render preset pills with labels and blurbs without
    # hardcoding any of it on the client side. Secrets are
    # surfaced as has_X booleans only — actual key values
    # never leave the panel process.
    h._json({
        "settings": P.get_settings_public(),
        "presets": P.OUTPUT_PRESETS,
        "memory_policies": P.MEMORY_POLICIES,
        "default_preset": P.DEFAULT_OUTPUT_PRESET,
    })


@post("/star-click")
def post_star_click(h, path, qs, ctype) -> None:
    # One anonymous count, no identity, no repeat — the client also
    # writes a local flag so the ask never returns on this install.
    # `via` separates "opened the link" from "said they already had",
    # because only the first is a click we caused.
    # `body` is the raw request string (this route speaks JSON, so read
    # it directly and fall back to the form field rather than trusting
    # either alone).
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    try:
        _payload = P.json.loads(body) if body.strip().startswith("{") else {}
    except (ValueError, TypeError):
        _payload = {}
    _via = str(_payload.get("via")
               or (form.get("via", ["link"])[0] if form else "link")).strip().lower()
    if _via not in ("link", "already"):
        _via = "link"
    P._analytics_capture("star_prompt", {"via": _via})
    h._json({"ok": True}); return


@post("/version/finish")
def post_version_finish(h, path, qs, ctype) -> None:
    # Re-run post_update.sh after a failed in-panel finish (the banner's
    # "Try again"). Same background run, same refusals.
    code, payload = P.post_update_start("retry")
    h._json(payload, code); return


@post("/restart")
def post_restart(h, path, qs, ctype) -> None:
    # The "Restart to finish update" pill used to open an alert() that
    # told the user to go and do it themselves in Pinokio — a button
    # naming an action and delivering a paragraph. Worse, Chrome
    # silently swallows repeat dialogs once "prevent additional
    # dialogs" is ticked, so for some users the click did visibly
    # nothing at all (reported 2026-08-16).
    #
    # os.execv REPLACES this process with a fresh one: same pid, same
    # port, same argv and cwd, running the code that is on disk NOW.
    # That is exactly what a stale process needs. The queue and
    # settings live on disk, so nothing in flight is lost EXCEPT a
    # running render — which is why a busy panel refuses instead.
    # The update is not finished until post_update.sh has run: restarting
    # into new code before that is how a release's new package was skipped.
    _pu = P.post_update_status()
    if _pu.get("pending") or _pu.get("active"):
        h._json({"ok": False, "post_update": True,
                    "error": "Finishing the update first (installing what the "
                             "new version needs) - restart becomes available "
                             "when it is done."}, 409); return
    if _pu.get("state") == "failed":
        h._json({"ok": False, "post_update": True,
                    "error": "The update did not finish: " + str(_pu.get("error"))
                             + ". Use Pinokio's Update (Stop, then Update), "
                             "or try again from the update banner."}, 409); return
    with P.LOCK:
        _busy = P.STATE.get("current") is not None
    if _busy:
        h._json({"ok": False, "busy": True,
                    "error": "A render is running. Stop it first, or "
                             "wait for it to finish — restarting now "
                             "would kill it."}, 409); return
    P.persist_queue()
    h._json({"ok": True, "restarting": True})
    try:
        h.wfile.flush()
    except Exception:                                      # noqa: BLE001
        pass

    def _reexec() -> None:
        # Give the response a moment to reach the browser; the socket
        # is non-inheritable (PEP 446) so exec closes the listener and
        # the new image rebinds the port cleanly.
        P.time.sleep(0.4)
        try:
            P.os.chdir(str(P.ROOT))
            P.os.execv(P.sys.executable, [P.sys.executable] + P.sys.argv)
        except Exception as exc:                           # noqa: BLE001
            P.sys.stderr.write(f"restart failed: {exc}\n")
    P.threading.Thread(target=_reexec, name="panel-restart",
                     daemon=True).start()


@post("/version/check")
def post_version_check(h, path, qs, ctype) -> None:
    # Force an immediate remote check (UI button on the version
    # pill). Runs synchronously so the client gets the fresh
    # state in the response — at most a 10s round-trip to GitHub.
    try:
        P._check_remote_once()
        h._json({"ok": True, "state": P.get_version_state()})
    except Exception as exc:
        h._json({"ok": False, "error": str(exc)}, 500)


@post("/analytics/ui")
def post_analytics_ui(h, path, qs, ctype) -> None:
    """The browser's ONLY analytics channel (v4.9.7): a strict allowlist of
    (event, props) shapes the page may report — how the update pop-up was
    answered, a broadcast being seen, the Editor being opened/exported.
    Anything outside the allowlist is dropped with 400; nothing here is
    free text and nothing reaches the network when analytics is off."""
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, form = _rb
    def _f(k):
        v = form.get(k, [""])
        return (v[0] if isinstance(v, list) else v) or ""
    event = str(_f("event")).strip()
    if event == "update_prompt":
        action = str(_f("action")).strip()
        if action not in ("shown", "later", "update_now", "banner_update",
                          "banner_later", "restart_needed"):
            h._json({"ok": False, "error": "unknown action"}, 400); return
        P._analytics_capture("update_prompt", {
            "action": action, "version": P.running_version()})
        if action in ("update_now", "banner_update"):
            # Remember the build they pressed it ON. The next boot compares:
            # a different version means the update landed, the same version
            # means it didn't — which is the 23-of-139 hole that `app_updated`
            # alone can never show, because a failed update emits nothing.
            # One local string, overwritten each press, cleared on read.
            # 4.17.5: "version@sha" - a build that moved without a VERSION
            # bump is still an update that landed (read in the boot report).
            P._settings_set_internal(
                analytics_update_pressed=P.update_pressed_marker())
    elif event == "broadcast_seen":
        P._analytics_capture("broadcast_seen", {"version": P.running_version()})
    elif event == "feature_used":
        feature = str(_f("feature")).strip()
        if feature not in ("editor_open", "editor_export", "enhance_prompt"):
            h._json({"ok": False, "error": "unknown feature"}, 400); return
        P._analytics_feature(feature, _f("detail"))
    else:
        h._json({"ok": False, "error": "unknown event"}, 400); return
    h._json({"ok": True})


@post("/version/pull")
def post_version_pull(h, path, qs, ctype) -> None:
    # The "magic button" path — when the pill is in the behind
    # state and the user clicks it, this endpoint runs git pull
    # on the panel repo and reports back. The user still has to
    # restart phosphene in Pinokio to load the new code (we can't
    # restart ourselves from inside our own process), but we
    # surface that clearly via the pull_state field.
    #
    # If the pulled diff touches dependency manifests / patch
    # scripts (anything that update.js does in addition to
    # `git pull`), we set pull_requires_full_update=True and the
    # UI nudges the user toward Pinokio's full Update flow
    # instead of just Stop+Start.
    # Server-side guards run BEFORE we mutate _VERSION_STATE so a
    # rejected 409 doesn't leave pull_state="pulling" stuck on
    # disk for the UI poller to chew on forever. Previously the
    # state was set first and the guards lived inside the try
    # block; a guarded return would skip the cleanup branch in
    # the bottom finally clause and never reset pull_state. Codex
    # caught this on the second pre-ship review.
    #
    # The UI already refuses to surface the magic Update button
    # unless the local repo is clean + on main + with no local
    # commits ahead, but a malicious POST could otherwise bypass
    # that and silently trigger `reset --hard`. Re-validate here.
    cur_branch = (P._git_capture(
        ["rev-parse", "--abbrev-ref", "HEAD"]) or "").strip()
    if cur_branch != "main":
        h._json({
            "ok": False,
            "error": (
                f"refusing to pull: local repo is on '{cur_branch}', "
                f"not 'main'. Switch to main or run a fresh Pinokio "
                f"install if you didn't intend to be on a side branch."
            ),
        }, 409)
        return
    # `git status --porcelain -uno` is empty iff no TRACKED file is
    # modified. Same probe (and same -uno reasoning) as
    # _detect_local_install_state — see the long note there: the
    # installers legitimately leave untracked dirs in this tree
    # (minimax-h3-mlx/, cache/, logs/) and refusing to pull because
    # of them is what left every H3 user unable to update (#52).
    dirty = (P._git_capture(["status", "--porcelain", "-uno"]) or "").strip()
    if dirty:
        h._json({
            "ok": False,
            "error": (
                "refusing to pull: local working tree has "
                "uncommitted changes to tracked files. Commit, "
                "stash, or discard them, then click Update again."
            ),
            "dirty_files": dirty.splitlines()[:20],
        }, 409)
        return
    # Block if there are local commits ahead of origin — those
    # would be wiped by the reset --hard fallback below.
    ahead = (P._git_capture(
        ["rev-list", "--count", "origin/main..HEAD"]) or "0").strip()
    try:
        ahead_n = int(ahead)
    except ValueError:
        ahead_n = 0
    if ahead_n > 0:
        h._json({
            "ok": False,
            "error": (
                f"refusing to pull: local has {ahead_n} commit(s) "
                f"ahead of origin/main. Push them or reset manually "
                f"if you don't need them."
            ),
        }, 409)
        return

    # All guards passed — claim the pulling state and proceed.
    with P._VERSION_LOCK:
        P._VERSION_STATE["pull_state"] = "pulling"
        P._VERSION_STATE["pull_message"] = None
        P._VERSION_STATE["pull_pulled_to_short"] = None
        P._VERSION_STATE["pull_pulled_to_version"] = None
        P._VERSION_STATE["pull_requires_full_update"] = False
    try:
        # Capture HEAD before the pull so we can diff afterwards.
        pre_sha = P._git_capture(["rev-parse", "HEAD"]) or ""
        # Step 1: fetch — populates origin/main without touching HEAD.
        fetch_proc = P.subprocess.run(
            ["git", "-C", str(P.ROOT), "fetch", "origin"],
            capture_output=True, timeout=60,
        )
        if fetch_proc.returncode != 0:
            raise RuntimeError(
                (fetch_proc.stdout + fetch_proc.stderr).decode("utf-8", "replace").strip()
                or f"git fetch exited {fetch_proc.returncode}")
        # Step 1b: THE OBSTRUCTION GUARD — the sidebar Update's, run on the
        # fetched origin/main, before anything can move the tree. `status
        # -uno` above is blind to untracked and ignored files, and both
        # moves below destroy them: `pull --ff-only` silently overwrites an
        # IGNORED file where upstream now tracks one, and the reset
        # overwrites untracked ones too (Codex INST-01, 2026-09-24).
        guard = P.ROOT / "scripts" / "pinokio" / "update_obstruction_guard.sh"
        if not guard.is_file():
            raise RuntimeError(
                "scripts/pinokio/update_obstruction_guard.sh is missing - "
                "not updating. Nothing was changed or deleted; use the "
                "Pinokio Update instead.")
        guard_proc = P.subprocess.run(
            ["bash", str(guard), "origin/main"],
            cwd=str(P.ROOT), capture_output=True, timeout=60,
        )
        if guard_proc.returncode != 0:
            raise RuntimeError(
                (guard_proc.stdout + guard_proc.stderr).decode("utf-8", "replace").strip()
                or "untracked or ignored files are in the way - nothing deleted")
        # Step 2: try a fast-forward pull. Happy path for fresh installs
        # whose local history lines up with origin/main.
        pull_proc = P.subprocess.run(
            ["git", "-C", str(P.ROOT), "pull", "--ff-only", "origin", "main"],
            capture_output=True, timeout=60,
        )
        pull_out = (pull_proc.stdout + pull_proc.stderr).decode("utf-8", "replace").strip()
        # Step 3: if the fast-forward refused (history diverged from
        # origin — e.g. because of a past force-push that scrubbed
        # commit identities), fall back to a hard reset onto
        # origin/main. Guards above already proved this is safe
        # (clean tree + on main + nothing ahead), so the reset just
        # snaps the diverged history back to upstream.
        #
        # ONLY ON PROVEN DIVERGENCE. Every other refusal — the commonest is
        # git's own "untracked working tree files would be overwritten" —
        # used to take this branch too and reset over the file git had just
        # protected (INST-01). The pre-fetch `ahead` check was against the
        # OLD origin/main, so it cannot prove anything here; count again
        # against the fetched one. Zero means history did not diverge,
        # whatever made the pull fail: stop and report git's message.
        if pull_proc.returncode != 0:
            ahead_after = (P._git_capture(
                ["rev-list", "--count", "origin/main..HEAD"]) or "0").strip()
            try:
                ahead_after_n = int(ahead_after)
            except ValueError:
                ahead_after_n = 0
            if ahead_after_n <= 0:
                raise RuntimeError(
                    f"update blocked - history has NOT diverged, so nothing "
                    f"was reset or deleted.\n{pull_out}")
            reset_proc = P.subprocess.run(
                ["git", "-C", str(P.ROOT), "reset", "--hard", "origin/main"],
                capture_output=True, timeout=30,
            )
            reset_out = (reset_proc.stdout + reset_proc.stderr).decode("utf-8", "replace").strip()
            if reset_proc.returncode != 0:
                raise RuntimeError(
                    f"fast-forward refused and reset --hard failed.\n"
                    f"pull: {pull_out}\nreset: {reset_out}")
            pull_out = (
                f"history diverged from origin (likely a past force-push); "
                f"recovered via reset --hard origin/main\n{reset_out}"
            )
        # Refresh local fields (HEAD, version) before computing the diff.
        P._detect_local_install_state()
        post_sha = P._git_capture(["rev-parse", "HEAD"]) or ""

        # 4.17.5 (fleet): NOTHING NEW IS NOT AN UPDATE. Two installs pressed
        # "Update now" on 4.17.4 itself; the pull moved nothing, the panel
        # still said "restart to finish", and the restart came back on the
        # same build - reported as a failed update (update_outcome
        # restart_pending 4.17.4 -> 4.17.4). When HEAD did not move and this
        # process already runs what is on disk, there is nothing to restart:
        # say so, and drop the "pressed Update" marker so the next boot does
        # not count a no-op as a failure.
        moved = bool(pre_sha and post_sha and pre_sha != post_sha)
        if not moved and not P.get_version_state().get("stale_process"):
            P._settings_set_internal(analytics_update_pressed="")
            with P._VERSION_LOCK:
                P._VERSION_STATE["pull_state"] = "current"
                P._VERSION_STATE["pull_message"] = (
                    "Already up to date - nothing to restart.")
            try:
                P._check_remote_once()
            except Exception:
                pass
            h._json({"ok": True, "moved": False, "state": P.get_version_state()})
            return

        # Did the pull touch anything that needs the heavier Pinokio
        # Update.js (pip reinstalls + patch reapply)? If so, flag it.
        deps_touched = False
        if pre_sha and post_sha and pre_sha != post_sha:
            diff_out = P._git_capture(
                ["diff", "--name-only", f"{pre_sha}..{post_sha}"]
            ) or ""
            deps_signals = (
                "install.js", "update.js", "pinokio.js", "download_q8.js",
                "patch_ltx_codec.py", "required_files.json",
                "requirements.txt", "pyproject.toml", "setup.py",
                # Since v4.0 every pin, patch and weight step lives in
                # post_update.sh and scripts/pinokio/ — a pull that touches
                # them without the full Update ships new code onto old
                # dependencies (review 2026-09-02).
                "scripts/post_update.sh", "scripts/check_post_update.js",
                # The wheel-build pin both installers read, and the YuE2
                # engine SHA: only the full Update runs music_checkout.sh, so
                # a pin-only pull used to say "Stop/Start is enough" and left
                # new runner code on the old engine (Codex INST-12).
                "pip-build-constraints.txt", "scripts/music/engine_pin.txt",
            )
            for line in diff_out.splitlines():
                if (line in deps_signals or line.startswith("ltx-2-mlx/")
                        or line.startswith("scripts/pinokio/")
                        # install_h3.js / install_music.js / install_qwen.js …
                        # carry their engines' pins the same way install.js does.
                        or (line.startswith("install") and line.endswith(".js"))):
                    deps_touched = True
                    break

        with P._VERSION_LOCK:
            P._VERSION_STATE["pull_state"] = "pulled"
            P._VERSION_STATE["pull_message"] = (pull_out.splitlines() or ["pulled"])[-1]
            # _VERSION_STATE, not the /version snapshot: these two
            # mean "what we pulled TO", which is a fact about the tree.
            # The snapshot's local_* deliberately means the opposite —
            # the build this process is still running.
            P._VERSION_STATE["pull_pulled_to_short"] = P._VERSION_STATE["local_short"]
            P._VERSION_STATE["pull_pulled_to_version"] = P._VERSION_STATE["local_version"]
            P._VERSION_STATE["pull_requires_full_update"] = deps_touched

        # Finish the update the way Pinokio's Update does: post_update.sh,
        # after every pull that moved HEAD (beta, after 4.17.4 - see
        # post_update_start). Restart stays refused until it is done.
        if pre_sha and post_sha and pre_sha != post_sha:
            P.post_update_start("update_now")

        # Re-run the remote check so behind_by recalculates to 0
        # (normally) or to whatever new commits landed in the
        # window since we pulled.
        try:
            P._check_remote_once()
        except Exception:
            pass

        h._json({"ok": True, "moved": moved, "state": P.get_version_state()})
    except P.subprocess.TimeoutExpired:
        with P._VERSION_LOCK:
            P._VERSION_STATE["pull_state"] = "error"
            P._VERSION_STATE["pull_message"] = "git pull timed out (60s)"
        h._json({"ok": False, "error": "git pull timed out", "state": P.get_version_state()}, 504)
    except Exception as exc:
        with P._VERSION_LOCK:
            P._VERSION_STATE["pull_state"] = "error"
            P._VERSION_STATE["pull_message"] = str(exc)
        h._json({"ok": False, "error": str(exc), "state": P.get_version_state()}, 500)


@post("/settings")
def post_settings(h, path, qs, ctype) -> None:
    # Accept partial-patch updates: only the fields the user
    # actually changed need to be present. Validation lives in
    # _validate_settings_patch — never trust the form payload.
    #
    # Re-parse with keep_blank_values=True so that an explicit
    # empty value (e.g. `civitai_api_key=`) is treated as "clear
    # this field" rather than dropped silently. The default form
    # parser in _read_form_body drops empty values, which
    # would otherwise turn the Clear button into a no-op.
    _rb = h._read_form_body()
    if _rb is None:
        return
    body, _ = _rb
    settings_form = P.parse_qs(body, keep_blank_values=True)
    payload: dict = {}
    for k, v in settings_form.items():
        payload[k] = v[0] if isinstance(v, list) else v
    prev = P.get_settings()
    current, err = P.update_settings(payload)
    if err:
        # Public-safe view on errors too — never echo a saved
        # secret back to the client even when validation fails
        # on a different field.
        h._json({"ok": False, "error": err,
                    "settings": P.get_settings_public()}, 400)
        return
    # Codec + token env vars are read at helper SPAWN time. If
    # the user changed any of them, kill the helper so the next
    # job respawns it with the new env. Job in flight finishes
    # with the OLD values (we're not interrupting a render).
    codec_changed = (
        prev.get("output_pix_fmt") != current.get("output_pix_fmt") or
        prev.get("output_crf") != current.get("output_crf")
    )
    tokens_changed = (
        prev.get("civitai_api_key", "") != current.get("civitai_api_key", "") or
        prev.get("hf_token", "") != current.get("hf_token", "")
    )
    # Anonymous usage analytics. No helper restart needed — the
    # analytics path reads get_settings() per event, in-process.
    # Log the flip so there's a visible record in the panel log
    # of a setting that governs what leaves the machine.
    if prev.get("analytics_enabled", True) != current.get("analytics_enabled", True):
        P.push("settings: anonymous usage analytics "
             + ("ON." if current.get("analytics_enabled", True)
                else "OFF - nothing is sent or logged."))
    if codec_changed:
        P.push(
            f"settings: output codec → {current['output_pix_fmt']} "
            f"crf {current['output_crf']} ({current['output_preset']}). "
            f"Helper restarted; takes effect on next job."
        )
    if tokens_changed:
        # Don't log token values themselves, just the action.
        P.push("settings: API tokens updated. Helper restarted; "
             "takes effect on next job.")
    if codec_changed or tokens_changed:
        # Settings change forces a helper restart. Mark any in-flight
        # job as user-cancelled so the worker reports it that way
        # instead of as a "helper exited" failure (same shape as
        # /helper/restart and stop_current_job).
        with P.LOCK:
            cur = P.STATE.get("current")
            if cur is not None:
                cur["cancel_requested"] = True
        P.HELPER.kill()
    # Return only the public-safe view — never echo the saved
    # key back to the client even on success.
    h._json({
        "ok": True,
        "settings": P.get_settings_public(),
        "helper_restarted": codec_changed or tokens_changed,
    })


@post("/stop_comfy")
def post_stop_comfy(h, path, qs, ctype) -> None:
    killed = P.kill_comfy()
    h._json({"killed": killed}); return


@post("/open_pinokio")
def post_open_pinokio(h, path, qs, ctype) -> None:
    P.open_pinokio()
    h._json({"ok": True}); return


# Bug-report endpoint — used by the header bug button + modal. The
# client supplies a title + body (already pre-filled with environment
# info) and an `includeCrashReports` flag. We URL-build the GitHub
# `issues/new?title=&body=&labels=bug` link entirely client-side
# (no auth, no API hit, no token required) and optionally bundle
# the latest crash IPS files into a tmp zip the user drags onto the
# issue. NO server-side issue creation — keeps the surface tiny.
@post("/panel/bug-report")
def post_panel_bug_report(h, path, qs, ctype) -> None:
    # The chain arm carried this condition; its failure fell
    # through to the chain end, which answers 404.
    if not (ctype.startswith("application/json")):
        h.send_error(404)
        return
    try:
        length = int(h.headers.get("Content-Length") or "0")
    except ValueError:
        h._json({"error": "invalid Content-Length"}, 400); return
    if length <= 0:
        h._json({"error": "Content-Length required"}, 411); return
    # Cap the body — title + body + a few flags is at most a couple
    # of KB; 256 KB is wide enough for paste-bombed log tails while
    # staying well below GitHub's 65 KB URL limit (we'll truncate
    # the body before URL-encoding anyway).
    if length > 256 * 1024:
        h._json({"error": "body too large (max 256 KB)"}, 413); return
    try:
        payload = P.json.loads(h.rfile.read(length).decode() or "{}")
    except (P.json.JSONDecodeError, UnicodeDecodeError):
        h._json({"error": "invalid JSON body"}, 400); return

    title = (payload.get("title") or "[bug] ").strip()
    body = (payload.get("body") or "").strip()
    include_crash = bool(payload.get("includeCrashReports"))

    # GitHub's URL parser tolerates ~8 KB of querystring before it
    # silently drops the body. Truncate the body to ~6 KB pre-encode
    # to leave headroom for percent-escaping (which roughly doubles
    # length for JSON / log content).
    if len(body) > 6000:
        body = body[:6000] + "\n\n…(truncated; remaining log on disk)…"
    from urllib.parse import quote as _urlq
    issue_url = (
        f"https://github.com/mrbizarro/phosphene/issues/new"
        f"?title={_urlq(title)}&body={_urlq(body)}&labels=bug"
    )

    zip_path: str | None = None
    if include_crash:
        try:
            # zipfile is imported at module scope (the caption bundle
            # endpoint also uses it). A local `import zipfile` here
            # would make zipfile local across the entire do_POST
            # method and break the bundle endpoint.
            diag = P.Path.home() / "Library" / "Logs" / "DiagnosticReports"
            if diag.is_dir():
                # SYS-12: this used to take the latest 5 .ips from ANY
                # process on the Mac — Safari, Mail, a game, whatever last
                # crashed — and zip it into a Phosphene bug report. The
                # panel's own crashes are always "python3*" or "Python*"
                # (see the watchdog error copy: python3.11_*.ips), so
                # filter to those; an unrelated app's crash tells the
                # Phosphene maintainer nothing and is a real, unnecessary
                # privacy cost for the reporter (another app's crash log
                # can carry its own paths/content, unrelated to this bug).
                # UI-6: ...and a "python*" name alone is not Phosphene —
                # _phosphene_crash_reports() reads each report and keeps
                # only the ones whose process ran from this install.
                ips = _phosphene_crash_reports(diag)[:5]
                if ips:
                    # 2026-05-31 review fix: crash .ips files can carry
                    # home paths / usernames. The old predictable
                    # /tmp/phosphene-bug-<ts>.zip was world-readable
                    # (default umask) and never cleaned. Write into a
                    # private 0700 dir with a 0600 zip so other local
                    # users can't read the crash bundle while it waits
                    # to be drag-uploaded.
                    import tempfile as _tf
                    _zdir = _tf.mkdtemp(prefix="phosphene-bug-")
                    try:
                        P.os.chmod(_zdir, 0o700)
                    except OSError:
                        pass
                    zp = P.Path(_zdir) / f"phosphene-bug-{int(P.time.time())}.zip"
                    with P.zipfile.ZipFile(zp, "w", P.zipfile.ZIP_DEFLATED) as zf:
                        for ip in ips:
                            zf.write(ip, arcname=ip.name)
                    try:
                        P.os.chmod(zp, 0o600)
                    except OSError:
                        pass
                    zip_path = str(zp)
        except Exception as exc:                            # noqa: BLE001
            # Crash bundling is a nicety; if it fails we still
            # surface the issue URL. Log so we can debug.
            P.push(f"[bug-report] crash-bundle skipped: {exc}")

    h._json({"ok": True, "issueUrl": issue_url, "zipPath": zip_path})


# ---- Agentic Flows GETs removed 2026-05-15.
# External agents use the documented HTTP API (docs/API.md).
# `/agent/image/config` retained below: image-engine config used by
# the Image Studio, unrelated to the removed chat surface.
@get_when(lambda p: p.startswith("/agent/") and p != "/agent/image/config")
def get_agent_gone(h, parsed) -> None:
    h._json({"error": "agentic flows removed; see docs/API.md"}, 410)


# ---- Agentic Flows POSTs removed 2026-05-15.
# External agents use the documented HTTP API (docs/API.md).
# `/agent/image/config` POST retained below: image-engine config used
# by the Image Studio, unrelated to the removed chat surface.
@post_when(lambda p: p.startswith("/agent/") and p != "/agent/image/config")
def post_agent_gone(h, path, qs, ctype) -> None:
    h._json({"error": "agentic flows removed; see docs/API.md"}, 410)


@get("/")
def get_root(h, parsed) -> None:
    # The palette the browser chose last time rides a cookie so the first
    # paint is already light or dark — the modules that apply it run after.
    _theme = ""
    try:
        from http.cookies import SimpleCookie  # noqa: PLC0415
        _ck = SimpleCookie(h.headers.get("Cookie") or "")
        _theme = _ck["phos_theme"].value if "phos_theme" in _ck else ""
    except Exception:                                              # noqa: BLE001
        _theme = ""
    h._ok(P.page(theme=_theme).encode())
