"""Version check / update notifier: the "Update available" pill's server side.

Moved out of mlx_ltx_panel.py with the function bodies unchanged except for the
mechanical rename the other split modules use: panel globals and this module's
own names are spelled `P.<name>`, and `P` is the RUNNING panel module (it
usually runs as __main__, so it is never imported by name here). Routing every
cross-reference through P keeps test patches effective: test_version_pull_route
and test_update_noop_pull patch `P.ROOT`, `P._git_capture`, `P._check_remote_once`
and friends, and test_stale_process sets `panel._BOOT_*` directly.

Two things deliberately stay in mlx_ltx_panel.py:
  * the four `_BOOT_*` snapshot globals and the import-time `_capture_boot_head()`
    call. They are REBOUND (a re-export would be a copy that goes stale), and
    the capture has to run at import, before anything can serve a request.
  * the wiring of `P` for this module happens right before that call, not in the
    late loop with the route modules, for the same reason.

One signature change: `_git_capture(args, cwd=ROOT)` used to bind the panel's
ROOT when the function was defined. ROOT is a panel global and not available
here at import, so the default is None and `P.ROOT` is read per call (which is
also what lets a test that patches `P.ROOT` reach it).

Every name below stays importable from mlx_ltx_panel (it re-exports them), so
panel/routes_meta.py and the tests are unchanged.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path

P = None  # the running mlx_ltx_panel module; assigned at wiring time

# Phosphene ships fixes often (multiple commits a day in active periods) and
# users keep telling us "I clicked Update but I don't see anything new" — by
# the time their feedback lands, we've usually pushed three more commits.
# This module surfaces a small "Update available" pill in the header when the
# local install is behind origin/main, so users notice mismatch immediately
# instead of finding out later via a friend's tweet.
#
# How it works:
#   1. At startup we read the local commit SHA via `git rev-parse HEAD` and
#      branch name via `git rev-parse --abbrev-ref HEAD`. Stored once.
#   2. A daemon thread polls the GitHub commits API every 30 minutes (no
#      auth, public repo, well within unauthenticated rate limit).
#   3. We list `origin/main` commits newest-first and find the index of the
#      local SHA. If found at index N, we are N commits behind. If not found
#      in the first 30 commits, we report "30+ behind".
#   4. The /version endpoint returns the snapshot; the UI renders a pill
#      that links to the version modal listing each unseen commit.
#
# Suppressed cases (no pill rendered):
#   - User is on a non-main branch (local dev / fork)
#   - User has uncommitted local changes (in-progress work; we'd be wrong
#     to call them "behind")
#   - We can't reach the GitHub API (offline; suppress silently — we don't
#     bug users with red error toasts every 30 minutes)

_VERSION_LOCK = threading.Lock()
_VERSION_REPO_OWNER = "mrbizarro"
_VERSION_REPO_NAME = "phosphene"
_VERSION_POLL_INTERVAL_SEC = 30 * 60          # 30 minutes between checks
_VERSION_STARTUP_DELAY_SEC = 30                # don't compete with boot
_VERSION_STATE: dict = {
    "local_sha": None,            # 40-char hex of HEAD at panel start
    "local_short": None,           # 7-char display form
    "local_version": None,         # human label from /VERSION file (e.g. "Y1.001")
    "local_branch": None,          # branch name (e.g. "main") or "(detached)"
    "local_commit_date": None,     # ISO-day of HEAD commit (e.g. "2026-05-21") — for dev pill
    "local_dirty": False,          # uncommitted changes in the working tree
    "remote_sha": None,            # latest sha on origin/main per GitHub API
    "remote_short": None,
    "remote_version": None,        # /VERSION file at origin/main (raw fetch)
    "behind_by": 0,                # number of commits between local and remote (0 = current)
    "behind_more_than": False,     # True when behind > 30 (we cap the API response)
    "commits_ahead": [],           # list of {sha, short, message, author, date} for the dropdown
    "broadcast": None,             # maintainer message from BROADCAST.json on public main, or None
    "checked_ts": None,            # epoch seconds of last successful poll
    "error": None,                 # last failure reason (None when last check OK)
    "suppress_reason": None,       # set when we deliberately skip checking (dev mode, dirty, etc.)
    # Pull state — populated only when the user clicks the magic button on
    # the pill while behind. Kept on _VERSION_STATE so the UI can render a
    # "restart needed" pill without a separate state store.
    "pull_state": "idle",          # idle | pulling | pulled | error
    "pull_message": None,          # human-readable result line (last git output line, or error)
    "pull_pulled_to_short": None,  # SHA we ended up at after the pull
    "pull_pulled_to_version": None,# VERSION label after the pull
    "pull_requires_full_update": False,  # True if the diff touched deps/patches
}


def _git_capture(args: list[str], cwd: Path | None = None) -> str | None:
    """Run a git subcommand and return stripped stdout (or None on any failure).
    No exceptions ever escape — we never want a missing git binary to crash
    the panel boot."""
    if cwd is None:
        cwd = P.ROOT          # looked up per call so a patched P.ROOT is honoured
    try:
        out = subprocess.check_output(
            ["git"] + args, cwd=str(cwd), stderr=subprocess.DEVNULL,
        )
        return out.decode("utf-8", "replace").strip()
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return None


def _read_local_version() -> str | None:
    """Return the contents of <repo>/VERSION as a single stripped string,
    or None if the file is missing / unreadable. We use this to surface a
    human-friendly label like 'Y1.001' instead of raw 7-char SHAs in the
    header pill. Older checkouts that predate this file fall back to the
    short SHA in the UI without breaking."""
    try:
        v = (P.ROOT / "VERSION").read_text().strip()
        return v or None
    except Exception:
        return None


def _fetch_raw_text(url: str, timeout: int = 10) -> str | None:
    """GET a public URL and return its body as a stripped string, or None
    on any error. Used for the raw VERSION-file fetch from origin/main on
    GitHub — `https://raw.githubusercontent.com/.../main/VERSION`. No
    auth, no rate-limit headers worth honouring at this volume (one
    request per 30-min poll)."""
    import urllib.request as _urlreq
    try:
        req = _urlreq.Request(url, headers={"User-Agent": "phosphene-panel-version-check"})
        with _urlreq.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace").strip() or None
    except Exception:
        return None


def _capture_boot_head() -> None:
    """Record what THIS process loaded, exactly once. Idempotent by design.

    Runs at import. `version_check_loop` still calls it, belt-and-braces for
    any path that reaches the loop without the import-time call having taken
    (a test that reset the globals, a future entry point); the guard makes the
    second call a no-op rather than a re-point at whatever is on disk now."""
    if P._BOOT_VERSION is None:
        # A zip install has no git and still ships a VERSION file. That file
        # is a property of the code this process loaded, so freeze it whether
        # or not the SHA lookup below works.
        P._BOOT_VERSION = P._read_local_version()
    if P._BOOT_HEAD_SHA is not None:
        return
    sha = P._git_capture(["rev-parse", "HEAD"])
    if not sha:
        return                      # not a checkout — the SHA fields stay None
    P._BOOT_HEAD_SHA = sha
    P._BOOT_BRANCH = P._git_capture(["rev-parse", "--abbrev-ref", "HEAD"])
    P._BOOT_COMMIT_DATE = P._git_capture(
        ["log", "-1", "--format=%cd", "--date=format:%Y-%m-%d"])


def boot_build_stamp() -> dict:
    """What this process is running. ONE accessor, so a second reader cannot
    quietly grow a second answer to "which build is this?"."""
    return {
        "sha": P._BOOT_HEAD_SHA,
        "short": P._BOOT_HEAD_SHA[:7] if P._BOOT_HEAD_SHA else None,
        "version": P._BOOT_VERSION,
        "branch": P._BOOT_BRANCH,
        "commit_date": P._BOOT_COMMIT_DATE,
    }


def running_version() -> str:
    """The VERSION label of the code THIS PROCESS is running.

    Everything that stamps a label onto something the process produced — an
    analytics event, a rendered film, a sidecar — has to name the build that
    produced it. `_read_local_version()` re-reads the tree, so after a pull
    under a running panel every one of those said the NEW number while the
    OLD code was doing the work: an app_boot event from a build that never
    booted, a film crediting a renderer that never touched it. Same defect as
    the header stamp, same fix. Disk is the fallback for a zip install with
    no git, where the boot capture has no SHA to anchor to."""
    return P.boot_build_stamp()["version"] or P._read_local_version() or "dev"


def build_stamp_text() -> str:
    """The build stamp as one greppable line, for the `phosphene-build` meta
    tag. The page is full of real SHAs in comments that reference historical
    commits ("the loadParams fix (b024bb5)"); thirteen of them as of today,
    and NONE of them the running build, which appeared nowhere in the served
    HTML at all. Anyone answering "which build is this?" by searching the page
    hit a decoy. This is the one line that is the answer."""
    b = P.boot_build_stamp()
    parts = [b["version"] or "dev", b["branch"] or "no-branch",
             b["short"] or "no-git"]
    if b["commit_date"]:
        parts.append(b["commit_date"])
    return " · ".join(parts)


def _detect_local_install_state() -> None:
    """Populate _VERSION_STATE.local_* fields. Called once at startup; result
    doesn't change while the panel is running (no auto-pull on the user's
    behalf). Pinokio's Update step kills + restarts the panel, so a fresh
    detect runs after every Update."""
    sha = P._git_capture(["rev-parse", "HEAD"])
    branch = P._git_capture(["rev-parse", "--abbrev-ref", "HEAD"]) or "(unknown)"
    # `git status --porcelain -uno` empty = no MODIFIED TRACKED files.
    #
    # -uno (untracked-files=no) is load-bearing, not a tidy-up. Plain
    # --porcelain also lists `??` untracked paths, and an install grows
    # those by design: the optional H3 pack clones minimax-h3-mlx/ into
    # this directory, Pinokio fills cache/ and logs/. That made the tree
    # permanently "dirty" for every H3 user, which suppressed the update
    # check outright — @Morac2 reported the pill stuck on "Update check
    # is paused: local changes uncommitted" with nothing but `?? minimax-
    # h3-mlx/` in his status (#52). .gitignore now covers those three
    # paths, but only for users who already got the new .gitignore, which
    # they can't without updating. -uno is what actually fixes it, and it
    # is the honest question anyway: untracked files are exactly the ones
    # a fast-forward pull (and the reset --hard fallback) leave alone. The
    # one case where an untracked file DOES matter — an incoming commit
    # adding a file at the same path — git refuses on its own, and that
    # error is surfaced verbatim by the /version/pull handler.
    porcelain = P._git_capture(["status", "--porcelain", "-uno"])
    dirty = bool(porcelain and porcelain.strip())
    local_version = P._read_local_version()
    # 2026-05-21 — capture HEAD commit date so the dev pill can show
    # users which dated build they're running. Reddit context: dev
    # users were reporting "I'm on 2.0.5" with character training,
    # confused because the panel showed an old VERSION value but they
    # had pulled fresh code. Showing the commit date alongside the
    # short SHA lets us tell them concretely "you're on the May 18
    # build — please pull again, latest is May 21."
    local_commit_date = P._git_capture(["log", "-1", "--format=%cd",
                                       "--date=format:%Y-%m-%d"])
    with P._VERSION_LOCK:
        P._VERSION_STATE["local_sha"] = sha
        P._VERSION_STATE["local_short"] = sha[:7] if sha else None
        P._VERSION_STATE["local_version"] = local_version
        P._VERSION_STATE["local_branch"] = branch
        P._VERSION_STATE["local_commit_date"] = local_commit_date
        P._VERSION_STATE["local_dirty"] = dirty
        # Suppress remote checks when the user is clearly running their own
        # variant — we'd be wrong to nag them about being "behind."
        if not P.VERSION_CHECK_ENABLED:
            P._VERSION_STATE["suppress_reason"] = "disabled by PHOSPHENE_DISABLE_VERSION_CHECK"
        elif not sha:
            P._VERSION_STATE["suppress_reason"] = "not a git checkout"
        elif branch != "main":
            P._VERSION_STATE["suppress_reason"] = f"on branch '{branch}', not main"
        elif dirty:
            P._VERSION_STATE["suppress_reason"] = "local changes uncommitted"
        else:
            P._VERSION_STATE["suppress_reason"] = None


def _fetch_remote_commits(limit: int = 30) -> list[dict] | None:
    """GET the latest N commits on origin/main from the public GitHub API.
    Returns a list of raw commit dicts (newest first), or None on any error.
    Caller is expected to handle None gracefully — we don't show errors to
    the user every 30 minutes when their wifi is flaky."""
    import urllib.request as _urlreq
    import urllib.error as _urlerr
    url = (f"https://api.github.com/repos/{P._VERSION_REPO_OWNER}/"
           f"{P._VERSION_REPO_NAME}/commits?sha=main&per_page={limit}")
    req = _urlreq.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "phosphene-panel-version-check",
    })
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list):
            return None
        return data
    except (_urlerr.URLError, _urlerr.HTTPError, OSError, ValueError, TimeoutError):
        return None


def _parse_broadcast(raw: bytes | str, local_version: str = "") -> dict | None:
    """Validate a BROADCAST.json payload into the one message the UI may show.

    The maintainer's channel to every install: a JSON file committed to public
    main. Closed shape on purpose — id (string, the dismissal key), title,
    body (plain text, rendered as text never HTML), and optionally
    show_if_version_below ("4.9.0": installs at or past it skip the message,
    so "please update" posts stop nagging people who already did). Anything
    malformed returns None: a broken file must read as "no message", never as
    a broken panel.
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    mid = str(data.get("id") or "").strip()[:64]
    title = str(data.get("title") or "").strip()[:120]
    body = str(data.get("body") or "").strip()[:2000]
    if not mid or not (title or body):
        return None
    below = str(data.get("show_if_version_below") or "").strip()
    if below and local_version:
        def _v(v):
            try:
                return tuple(int(x) for x in v.strip().split("."))
            except ValueError:
                return None
        lv, bv = _v(local_version), _v(below)
        if lv is not None and bv is not None and lv >= bv:
            return None
    return {"id": mid, "title": title, "body": body}


def _fetch_broadcast() -> dict | None:
    """GET BROADCAST.json from public main. None on any failure or absence."""
    import urllib.request as _urlreq
    import urllib.error as _urlerr
    url = (f"https://raw.githubusercontent.com/{P._VERSION_REPO_OWNER}/"
           f"{P._VERSION_REPO_NAME}/main/BROADCAST.json")
    req = _urlreq.Request(url, headers={
        "User-Agent": "phosphene-panel-version-check",
    })
    try:
        with _urlreq.urlopen(req, timeout=10) as resp:
            raw = resp.read(64 * 1024)
    except (_urlerr.URLError, _urlerr.HTTPError, OSError, ValueError,
            TimeoutError):
        return None
    with P._VERSION_LOCK:
        local = P._VERSION_STATE.get("local_version") or ""
    return P._parse_broadcast(raw, local)


def _check_remote_once() -> None:
    """Run one remote check, mutate _VERSION_STATE accordingly. Idempotent.
    Safe to call from a background thread or from a /version/check endpoint.
    Re-detects local state (HEAD, branch, dirty) at the top of every call so
    a tree that was dirty when the panel started but is clean now (e.g. user
    just committed) gets picked up without needing a panel restart. The
    earlier build only ran _detect_local_install_state once at startup,
    which left the suppress_reason stale across the panel's lifetime."""
    P._detect_local_install_state()
    with P._VERSION_LOCK:
        suppress = P._VERSION_STATE["suppress_reason"]
        local_sha = P._VERSION_STATE["local_sha"]
    if suppress:
        return                             # never poll in suppressed states
    commits = P._fetch_remote_commits(limit=30)
    if commits is None:
        with P._VERSION_LOCK:
            P._VERSION_STATE["error"] = "could not reach github.com"
        return
    if not commits:
        with P._VERSION_LOCK:
            P._VERSION_STATE["error"] = "github returned no commits"
        return
    remote_sha = commits[0].get("sha")
    if not remote_sha:
        with P._VERSION_LOCK:
            P._VERSION_STATE["error"] = "github response missing sha"
        return

    # Find local SHA in the list. If it's at index N, we are N commits behind.
    behind_by = None
    for i, c in enumerate(commits):
        if c.get("sha") == local_sha:
            behind_by = i
            break
    if behind_by is None:
        # Local is older than what fits in the window OR has been rebased
        # away. Either way, "30+" is a fine UX answer — the modal will say
        # "many updates available, please run Pinokio Update."
        behind_by = len(commits)
        more = True
    else:
        more = False

    # Slim each commit dict down to what the modal renders. Keep only the
    # commits the user is missing.
    ahead: list[dict] = []
    for c in commits[:behind_by]:
        commit = c.get("commit") or {}
        author = (commit.get("author") or {}).get("name") or "unknown"
        date = (commit.get("author") or {}).get("date") or ""
        msg = (commit.get("message") or "").splitlines()[0]   # first line only
        sha = c.get("sha") or ""
        ahead.append({
            "sha": sha,
            "short": sha[:7],
            "message": msg,
            "author": author,
            "date": date,
        })

    # Fetch the upstream VERSION file in parallel with the commits API so
    # the pill can show the human-friendly label (Y1.NNN). This is a raw
    # GET on raw.githubusercontent.com — no rate limit concern at our
    # 30-min cadence. Tolerate missing-file silently: older origin/main
    # commits predate VERSION, and we still have the SHA fallback.
    remote_version = P._fetch_raw_text(
        f"https://raw.githubusercontent.com/{P._VERSION_REPO_OWNER}/"
        f"{P._VERSION_REPO_NAME}/main/VERSION"
    )
    # 4.17.5 (fleet): "Phosphene 4.17.4 is out - you're on 4.17.4". A local
    # HEAD that is not among public main's commits (a local merge or commit
    # on main, a re-made history) read as "30+ behind" even when the tree
    # already IS the latest release - and the pop-up asked people to update
    # to the build they run. Public main bumps VERSION on every release, so
    # an unknown SHA carrying the remote's own VERSION is not behind.
    with P._VERSION_LOCK:
        _local_v = (P._VERSION_STATE.get("local_version") or "").strip()
    same_release = bool(more and remote_version and _local_v
                        and remote_version.strip() == _local_v)
    if same_release:
        behind_by, more, ahead = 0, False, []
    # The maintainer broadcast rides the same 30-min cadence. Failures and
    # absence read as "no message" — _fetch_broadcast never raises.
    broadcast = P._fetch_broadcast()
    with P._VERSION_LOCK:
        P._VERSION_STATE["remote_sha"] = remote_sha
        P._VERSION_STATE["remote_short"] = remote_sha[:7]
        P._VERSION_STATE["remote_version"] = remote_version
        P._VERSION_STATE["behind_by"] = behind_by
        P._VERSION_STATE["behind_more_than"] = more
        P._VERSION_STATE["local_unlisted_same_release"] = same_release
        P._VERSION_STATE["commits_ahead"] = ahead
        P._VERSION_STATE["checked_ts"] = time.time()
        P._VERSION_STATE["error"] = None
        P._VERSION_STATE["broadcast"] = broadcast


def version_check_loop() -> None:
    """Daemon thread entry: detect local state once, then poll the remote
    every _VERSION_POLL_INTERVAL_SEC. First poll happens after a 30s
    delay so we don't compete with boot-time work."""
    P._capture_boot_head()          # BEFORE anything can re-detect local state
    P._detect_local_install_state()
    time.sleep(P._VERSION_STARTUP_DELAY_SEC)
    while True:
        try:
            P._check_remote_once()
        except Exception as exc:                          # belt + braces
            sys.stderr.write(f"WARN: version check raised: {exc}\n")
        time.sleep(P._VERSION_POLL_INTERVAL_SEC)


def get_version_state() -> dict:
    """Snapshot of _VERSION_STATE for the /version endpoint. Returns a copy
    so the caller can't mutate the live state under the lock.

    TWO BUILDS, ALWAYS BOTH. `local_*` is what THIS PROCESS LOADED — the boot
    snapshot, frozen at import. `disk_*` is the working tree right now, one
    `git rev-parse` per request (the pill polls every 5 minutes). On a healthy
    install they are equal, and that is the point: when they diverge the
    header has to keep naming the code that is actually answering, or the
    stamp stops being the thing the owner uses to tell which build he is on.
    The 2026-08-14 incident where :8198 served pre-4.0.4 HTML for three hours
    after the folder updated is what `stale_process` is for.

    `_VERSION_STATE["local_*"]` is NOT that snapshot, despite the name. It is
    re-detected from disk at the top of every remote poll, deliberately, so a
    tree that was dirty at boot and is clean now stops being suppressed
    without a restart. It stays the basis for `behind_by` — being behind
    origin/main is a fact about the TREE — and is overwritten here for
    display, which is a fact about the PROCESS."""
    with P._VERSION_LOCK:
        snap = dict(P._VERSION_STATE)
    boot = P.boot_build_stamp()
    disk_sha = P._git_capture(["rev-parse", "HEAD"])
    # The header stamp answers exactly one question — what code is this
    # process running — so it is answered from the boot snapshot or not at
    # all. Never snap["local_*"]: that agrees with disk within one poll.
    if boot["sha"]:
        snap["local_sha"] = boot["sha"]
        snap["local_short"] = boot["short"]
        snap["local_version"] = boot["version"]
        snap["local_branch"] = boot["branch"] or snap.get("local_branch")
        snap["local_commit_date"] = boot["commit_date"]
    # Unconditional, not "only when stale". A consumer that must check
    # stale_process before it may trust disk_short is a consumer that will
    # eventually forget to.
    snap["disk_sha"] = disk_sha
    snap["disk_short"] = disk_sha[:7] if disk_sha else None
    snap["disk_version"] = P._read_local_version()
    snap["stale_process"] = bool(
        disk_sha and boot["sha"] and disk_sha != boot["sha"])
    snap["post_update"] = P.post_update_status()
    return snap
