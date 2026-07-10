#!/usr/bin/env python3
"""Fetch GitHub repo stats and append a daily snapshot to state/stats-data.jsonl.

Runs from .github/workflows/repo-stats.yml on a 24h cron. The GitHub Action
default token has push access to the repo it runs in, which is exactly what
the Traffic API (/repos/:owner/:repo/traffic/*) requires.

Why a custom script (not the off-the-shelf `github-repo-stats` Action):
- Mr Bizarro asked for "stable, has historic data, basic stuff" — keep the
  surface tiny and readable top-to-bottom.
- No build step, no node, no docker. Just stdlib urllib.
- Schema lives next to us; we can add fields later without renegotiating
  with an upstream marketplace action.

------------------------------------------------------------------------
RUNNING LOCALLY
------------------------------------------------------------------------

    GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_repo_stats.py
    GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_repo_stats.py --dry-run
    GITHUB_TOKEN=$(gh auth token) python3 scripts/fetch_repo_stats.py \
        --repo owner/name

Env vars:
- GITHUB_TOKEN     default token (auto-injected in GitHub Actions)
- GH_STATS_TOKEN   explicit override; wins over GITHUB_TOKEN
- PHOSPHENE_REPO   default repo override (CLI --repo wins over env)

Required token scopes for the default repo:
- public_repo / repo  (Traffic API requires push access)
- read:org            (only if querying a private org repo)

The classic `gh auth token` for the repo owner is sufficient.

------------------------------------------------------------------------
OUTPUT SCHEMA  (one JSON object per UTC day, newline-delimited)
------------------------------------------------------------------------

    {
      "date":   "2026-05-22",          # UTC YYYY-MM-DD (the day fetched)
      "fetched_at": "...Z",            # full ISO timestamp
      "stars":         N,                         # total today
      "forks":         N,
      "open_issues":   N,                         # excludes PRs
      "watchers":      N,                         # alias for stars (GH legacy)
      "subscribers":   N,                         # actual GitHub "watch"
      "open_prs":      N,
      # The live "what needs attention" board: the ACTUAL open issues +
      # PRs (not just counts), newest-first, capped at OPEN_ITEMS_CAP.
      # Non-sensitive fields only — this stays local-only (gitignored).
      "open_items": [
        {"number": N, "title": "...", "author": "login",
         "created_at": "...Z", "html_url": "...", "is_pr": false,
         "labels": [{"name": "bug", "color": "d73a4a"}],
         "comments": N, "draft": false},
        ...
      ],
      "open_items_truncated": false,   # true if list hit OPEN_ITEMS_CAP
      # Back-compat: most-recent-day slice. Same numbers as the last
      # element of clones_window/views_window. Kept so older dashboards
      # don't break.
      "clones": {"count": N, "uniques": N},
      "views":  {"count": N, "uniques": N},
      # Full 15-day window from the Traffic API. The API window rotates
      # daily — snapshotting this gives us a continuous record once we
      # accumulate a few days.
      "clones_window": [{"date": "2026-05-08", "count": N, "uniques": N}, ...],
      "views_window":  [{"date": "2026-05-08", "count": N, "uniques": N}, ...],
      # Cumulative star count per day, derived from /stargazers. Lets a
      # fresh dashboard show a stars curve from day 1 instead of
      # waiting for daily snapshots to accumulate.
      "stars_timeline": [{"date": "2024-09-12", "count": 1}, ...],
      "referrers": [{"referrer": "...", "count": N, "uniques": N}, ...],
      "paths":     [{"path": "...", "count": N, "uniques": N, "title": "..."}, ...],
      "releases":  {"total": N, "by_release": {"v1.0": N, ...}}
    }

Schema invariants:
- `date` is UTC YYYY-MM-DD, lexicographic-sortable.
- New fields may be added; existing field names/shapes must not change.
- stars_timeline is cumulative (not per-day). The dashboard derives
  deltas itself. Cumulative is monotonic, which is friendlier for
  partial / capped fetches.
- All counts are non-negative ints.

Idempotent: if today's date already has an entry, the new fetch REPLACES
it (so re-runs in the same UTC day don't duplicate rows).

The clones/views fields are duplicated for back-compat: they're the
SUM of the latest 24-hour entry. clones_window/views_window have the
full 15-day curve from the same API call.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# ---- config --------------------------------------------------------------

DEFAULT_REPO = os.environ.get("PHOSPHENE_REPO", "mrbizarro/phosphene")
TOKEN = (
    os.environ.get("GH_STATS_TOKEN")  # explicit override
    or os.environ.get("GITHUB_TOKEN")  # default in GH Actions
    or ""
).strip()
# Writes into the panel's gitignored state/ dir (NEVER in the public
# repo — Mr Bizarro 2026-05-22). PHOSPHENE_STATS_OUT overrides for
# debugging against a sandbox path. Pre-2026-05-22 this was
# docs/stats-data.jsonl which got committed publicly; that was rolled
# back and the location moved here.
OUTPUT = Path(
    os.environ.get(
        "PHOSPHENE_STATS_OUT",
        str(Path(__file__).resolve().parent.parent / "state" / "stats-data.jsonl"),
    )
)

API = "https://api.github.com"
UA = "phosphene-repo-stats/2"

# Star scan ceiling. /stargazers paginates 100/page so this is 50 pages.
# A real repo crossing 5k stars deserves a deliberate revisit, not a
# silent runaway.
STARS_CAP = 5000
# Pagination ceilings on the other historical pulls. Each cap maps to ~50
# API pages at per_page=100. For a fresh project these never trip; they're
# bounded-runtime insurance for forks/issues/PRs/commits on a viral repo.
FORKS_CAP   = 2000
ISSUES_CAP  = 5000   # combined issues + PRs in one stream
COMMITS_CAP = 3000   # trailing 365-day window
# The live "open items" board only needs the current backlog head, not the
# whole history. 200 open issues+PRs is already an unusable wall on a
# glance-board; beyond that we truncate and flag it.
OPEN_ITEMS_CAP = 200

# Retry policy for transient failures.
RETRY_BACKOFFS = (2, 8, 30)

# Rate-limit floor — sleep until reset if we drop below this.
RATE_LIMIT_FLOOR = 100

# Tracked across the run so we can print a single-line summary at exit.
_last_rate_limit: dict = {"remaining": None, "limit": None, "reset": None}

if not TOKEN:
    sys.stderr.write(
        "ERROR: no GitHub token. Set GH_STATS_TOKEN or run inside GitHub "
        "Actions (which injects GITHUB_TOKEN automatically).\n"
    )
    sys.exit(1)


# ---- http helpers --------------------------------------------------------


def _handle_rate_limit(headers) -> None:
    """Record current rate-limit state. If we're below the floor, sleep
    until the documented reset time."""
    remaining_raw = headers.get("X-RateLimit-Remaining")
    limit_raw = headers.get("X-RateLimit-Limit")
    reset_raw = headers.get("X-RateLimit-Reset")
    if remaining_raw is None:
        return
    try:
        remaining = int(remaining_raw)
    except (TypeError, ValueError):
        return
    try:
        limit = int(limit_raw) if limit_raw is not None else None
    except (TypeError, ValueError):
        limit = None
    try:
        reset = int(reset_raw) if reset_raw is not None else None
    except (TypeError, ValueError):
        reset = None
    _last_rate_limit["remaining"] = remaining
    _last_rate_limit["limit"] = limit
    _last_rate_limit["reset"] = reset

    if remaining < RATE_LIMIT_FLOOR and reset:
        now = int(time.time())
        wait = max(0, reset - now) + 2  # small cushion
        # Cap absurd waits so a misconfigured token doesn't hang CI.
        wait = min(wait, 3600)
        sys.stderr.write(
            f"  rate-limit low: {remaining}/{limit} — sleeping {wait}s "
            f"until reset\n"
        )
        time.sleep(wait)


def _do_request(url: str, accept: str):
    """Single HTTP request. Returns (parsed_body, headers).
    Raises HTTPError on non-2xx and URLError on network failure."""
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Accept": accept,
            "User-Agent": UA,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read()
        headers = resp.headers
    _handle_rate_limit(headers)
    parsed = json.loads(body) if body else None
    return parsed, headers


def _get(path: str, accept: str = "application/vnd.github+json", *,
         with_headers: bool = False):
    """GET an API path with retries. Permanent errors (4xx) fail fast;
    transient ones (5xx / network) retry per RETRY_BACKOFFS.

    If with_headers=True, returns (body, headers); else just body.
    """
    url = path if path.startswith("http") else f"{API}{path}"
    last_exc: Exception | None = None
    for attempt, backoff in enumerate((0,) + RETRY_BACKOFFS):
        if backoff:
            sys.stderr.write(
                f"  retry {attempt}/{len(RETRY_BACKOFFS)} after {backoff}s: {url}\n"
            )
            time.sleep(backoff)
        try:
            body, headers = _do_request(url, accept)
            return (body, headers) if with_headers else body
        except urllib.error.HTTPError as exc:
            err_body = b""
            try:
                err_body = exc.read()
            except Exception:
                pass
            # Try to record rate-limit headers even on errors.
            _handle_rate_limit(exc.headers or {})
            text = err_body.decode("utf-8", errors="replace")
            if exc.code in (401, 403, 404, 422):
                sys.stderr.write(
                    f"HTTP {exc.code} on {url}: {text[:300]}\n"
                )
                # 403 with rate-limit body is technically transient, but
                # without a token-permission diff we can't distinguish a
                # "you can't see this" 403 from a "you hit the limit"
                # 403. Fail fast; the operator can re-run later.
                raise
            sys.stderr.write(
                f"HTTP {exc.code} on {url} (transient): {text[:200]}\n"
            )
            last_exc = exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            sys.stderr.write(f"network error on {url}: {exc}\n")
            last_exc = exc
    assert last_exc is not None
    raise last_exc


def _get_paginated(path: str, accept: str = "application/vnd.github+json",
                   *, max_items: int | None = None) -> list:
    """Drain ?per_page=100 pages. Used for stargazers timeline. Stops
    when a page is short or when max_items is hit."""
    out: list = []
    page = 1
    sep = "&" if "?" in path else "?"
    while True:
        chunk = _get(f"{path}{sep}per_page=100&page={page}", accept=accept)
        if not chunk:
            break
        if not isinstance(chunk, list):
            break
        out.extend(chunk)
        if max_items is not None and len(out) >= max_items:
            out = out[:max_items]
            break
        if len(chunk) < 100:
            break
        page += 1
        if page > 200:  # absolute safety
            break
    return out


# ---- metric fetchers -----------------------------------------------------


def fetch_repo(repo: str) -> dict:
    """The repo object — covers stars/forks/issues/watchers."""
    r = _get(f"/repos/{repo}")
    return {
        "stars":       int(r.get("stargazers_count", 0)),
        "forks":       int(r.get("forks_count", 0)),
        "open_issues": int(r.get("open_issues_count", 0)),  # includes PRs
        "watchers":    int(r.get("watchers_count", 0)),     # alias for stars
        "subscribers": int(r.get("subscribers_count", 0)),  # actual "watch"
    }


def fetch_open_prs(repo: str) -> int:
    """The repo's open_issues_count includes PRs. Disambiguate so the
    dashboard can show issues and PRs separately."""
    # /search counts are cheap and don't paginate full result lists.
    q = f"repo:{repo}+is:pr+is:open"
    r = _get(f"/search/issues?q={q}")
    return int(r.get("total_count", 0))


def fetch_open_items(repo: str) -> list[dict]:
    """The actual list of currently-open issues AND pull requests, so the
    dashboard can render a live "what needs attention" board instead of a
    bare count.

    GitHub's /issues endpoint returns PRs too (a PR is a subclass of
    Issue), discriminated by the presence of a `pull_request` key. We keep
    only the small, non-sensitive fields the board renders — number, title,
    author login, created_at, is_pr, labels (name only), and comment count.
    We deliberately DROP body text, reactions, assignees, etc.: the board
    is a glanceable attention list, and this file is local-only anyway
    (Mr Bizarro 2026-07-10).

    Newest-first (GitHub's default sort=created&direction=desc), capped at
    OPEN_ITEMS_CAP so a viral repo's backlog can't bloat the row. The count
    fields (open_issues / open_prs) remain the source of truth for totals;
    a `truncated` flag tells the dashboard when the list is a head slice."""
    entries = _get_paginated(
        f"/repos/{repo}/issues?state=open&sort=created&direction=desc",
        max_items=OPEN_ITEMS_CAP,
    )
    out: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        is_pr = "pull_request" in e and bool(e.get("pull_request"))
        user = e.get("user") or {}
        labels = []
        for lb in e.get("labels") or []:
            if isinstance(lb, dict):
                name = lb.get("name")
                if name:
                    labels.append({
                        "name": str(name)[:60],
                        "color": str(lb.get("color") or "")[:6],
                    })
            elif isinstance(lb, str):
                labels.append({"name": lb[:60], "color": ""})
        out.append({
            "number":     int(e.get("number", 0)),
            "title":      (e.get("title") or "")[:280],
            "author":     str(user.get("login") or "") or "ghost",
            "created_at": e.get("created_at") or "",
            "html_url":   e.get("html_url") or "",
            "is_pr":      is_pr,
            "labels":     labels[:6],
            "comments":   int(e.get("comments", 0)),
            "draft":      bool(e.get("draft", False)) if is_pr else False,
        })
    return out


def _normalize_traffic_day(entry: dict) -> dict:
    """Map a Traffic-API entry to our schema. `timestamp` arrives as an
    ISO-8601 datetime (e.g. '2026-05-08T00:00:00Z'); we keep only the
    date portion to match our row-level `date` field."""
    ts = entry.get("timestamp", "")
    date = ts[:10] if isinstance(ts, str) and len(ts) >= 10 else ""
    return {
        "date":    date,
        "count":   int(entry.get("count", 0)),
        "uniques": int(entry.get("uniques", 0)),
    }


def fetch_clones_window(repo: str) -> list[dict]:
    """Full 15-day clones array. Per-day, normalized to {date,count,uniques}.
    The Traffic API rotates this window daily, so the only way to retain
    older days is to snapshot them into JSONL rows."""
    r = _get(f"/repos/{repo}/traffic/clones")
    days = r.get("clones") or []
    return [_normalize_traffic_day(d) for d in days]


def fetch_views_window(repo: str) -> list[dict]:
    """Full 15-day views array — same shape as clones."""
    r = _get(f"/repos/{repo}/traffic/views")
    days = r.get("views") or []
    return [_normalize_traffic_day(d) for d in days]


def fetch_stars_timeline(repo: str, total_stars: int) -> tuple[list[dict], bool]:
    """Cumulative star count per UTC day.

    Walks /stargazers with the star+json accept header (which adds
    `starred_at`). We DO NOT keep `user.login` — only the timestamp.
    Aggregates into a cumulative count: for each day where ≥1 star
    landed, emit {date, count}. Days with no stars are omitted so
    sparse history stays small; the dashboard can ffill itself.

    Returns (timeline, partial).
    `partial` is True if we hit STARS_CAP and bailed out — the dashboard
    can show a "showing first N stars" note.
    """
    # Skip the heavy pagination if the repo is empty.
    if total_stars <= 0:
        return [], False

    capped = total_stars > STARS_CAP
    max_items = STARS_CAP if capped else None
    entries = _get_paginated(
        f"/repos/{repo}/stargazers",
        accept="application/vnd.github.v3.star+json",
        max_items=max_items,
    )

    # Buckets keyed by YYYY-MM-DD. Use timestamps only — no login.
    counts_by_day: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts = e.get("starred_at", "")
        if not isinstance(ts, str) or len(ts) < 10:
            continue
        day = ts[:10]
        counts_by_day[day] = counts_by_day.get(day, 0) + 1

    # Convert to cumulative timeline.
    timeline: list[dict] = []
    running = 0
    for day in sorted(counts_by_day.keys()):
        running += counts_by_day[day]
        timeline.append({"date": day, "count": running})

    return timeline, capped


def fetch_forks_timeline(repo: str, total_forks: int) -> tuple[list[dict], bool]:
    """Cumulative fork count per UTC day. Same shape as stars_timeline,
    derived from /forks created_at. Cheap — most repos have few forks.
    Capped at FORKS_CAP for safety on viral repos."""
    if total_forks <= 0:
        return [], False
    capped = total_forks > FORKS_CAP
    max_items = FORKS_CAP if capped else None
    entries = _get_paginated(f"/repos/{repo}/forks?sort=oldest",
                             max_items=max_items)
    counts_by_day: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        ts = e.get("created_at", "")
        if not isinstance(ts, str) or len(ts) < 10:
            continue
        day = ts[:10]
        counts_by_day[day] = counts_by_day.get(day, 0) + 1
    timeline: list[dict] = []
    running = 0
    for day in sorted(counts_by_day.keys()):
        running += counts_by_day[day]
        timeline.append({"date": day, "count": running})
    return timeline, capped


def fetch_issue_pr_timelines(repo: str) -> dict:
    """Daily opened/closed counts for both Issues and PRs.

    Walks /issues with state=all (GitHub's "issues" endpoint returns
    PRs too — they're a subclass of Issue — discriminated by the
    presence of a `pull_request` key). Splits into 4 separate
    timelines: issues_opened / issues_closed / prs_opened / prs_closed.

    Each timeline: list of {date, count} for DAILY counts (not
    cumulative — the dashboard wants "velocity" which is a daily
    rate). Capped at ISSUES_CAP entries total."""
    entries = _get_paginated(
        f"/repos/{repo}/issues?state=all&sort=created&direction=asc",
        max_items=ISSUES_CAP,
    )
    issues_opened: dict[str, int] = {}
    issues_closed: dict[str, int] = {}
    prs_opened: dict[str, int]    = {}
    prs_closed: dict[str, int]    = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        is_pr = "pull_request" in e and bool(e.get("pull_request"))
        opened_at = (e.get("created_at") or "")[:10]
        closed_at = (e.get("closed_at") or "")[:10] if e.get("closed_at") else ""
        if len(opened_at) == 10:
            d = prs_opened if is_pr else issues_opened
            d[opened_at] = d.get(opened_at, 0) + 1
        if len(closed_at) == 10:
            d = prs_closed if is_pr else issues_closed
            d[closed_at] = d.get(closed_at, 0) + 1
    def _to_list(bucket: dict[str, int]) -> list[dict]:
        return [{"date": d, "count": bucket[d]} for d in sorted(bucket)]
    return {
        "issues_opened": _to_list(issues_opened),
        "issues_closed": _to_list(issues_closed),
        "prs_opened":    _to_list(prs_opened),
        "prs_closed":    _to_list(prs_closed),
    }


def fetch_commits_timeline(repo: str) -> list[dict]:
    """Daily commit count over the trailing window. Walks /commits with
    ?since=ISO-365-days-ago paginated. Capped at COMMITS_CAP to bound
    runtime on a long-lived repo. Daily counts (not cumulative — the
    dashboard wants activity, not totals)."""
    from datetime import timedelta
    since = (datetime.now(timezone.utc) - timedelta(days=365)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    entries = _get_paginated(
        f"/repos/{repo}/commits?since={since}",
        max_items=COMMITS_CAP,
    )
    counts_by_day: dict[str, int] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        # Commit author date (the developer's clock). /commits.commit.author.date
        # is the canonical timestamp; .committer.date can differ on rebases.
        commit = e.get("commit", {}) or {}
        author = commit.get("author", {}) or {}
        ts = (author.get("date") or "")[:10]
        if len(ts) != 10:
            continue
        counts_by_day[ts] = counts_by_day.get(ts, 0) + 1
    return [{"date": d, "count": counts_by_day[d]} for d in sorted(counts_by_day)]


def fetch_release_timeline(repo: str) -> list[dict]:
    """Release publish dates + cumulative download counts. Lets the
    dashboard show "release X shipped on Y, has Z downloads" — much
    more useful than the bare {total, by_release: {...}} blob."""
    try:
        rs = _get(f"/repos/{repo}/releases?per_page=100")
        if not isinstance(rs, list):
            return []
    except urllib.error.HTTPError:
        return []
    out: list[dict] = []
    for rel in rs:
        if not isinstance(rel, dict):
            continue
        tag = rel.get("tag_name", "?")
        published = (rel.get("published_at") or rel.get("created_at") or "")
        total = 0
        for a in rel.get("assets") or []:
            try:
                total += int(a.get("download_count", 0))
            except (TypeError, ValueError):
                continue
        out.append({
            "tag": tag,
            "published_at": published,
            "total_downloads": total,
        })
    # GitHub returns newest-first; sort oldest-first for charting.
    out.sort(key=lambda r: r.get("published_at") or "")
    return out


def fetch_referrers(repo: str) -> list[dict]:
    """Top 10 referring domains over the rolling 14-day window. We snapshot
    the 14d aggregate every day rather than try to derive a per-day
    breakdown (the API doesn't give one)."""
    r = _get(f"/repos/{repo}/traffic/popular/referrers")
    if not isinstance(r, list):
        return []
    return [
        {
            "referrer": x.get("referrer", "?"),
            "count":    int(x.get("count", 0)),
            "uniques":  int(x.get("uniques", 0)),
        }
        for x in r[:10]
    ]


def fetch_paths(repo: str) -> list[dict]:
    """Top 10 paths viewed over the rolling 14d. Tells us which docs are
    actually getting read (README vs STATE vs ROADMAP, etc)."""
    r = _get(f"/repos/{repo}/traffic/popular/paths")
    if not isinstance(r, list):
        return []
    return [
        {
            "path":    x.get("path", "?"),
            "title":   (x.get("title", "") or "")[:120],
            "count":   int(x.get("count", 0)),
            "uniques": int(x.get("uniques", 0)),
        }
        for x in r[:10]
    ]


def fetch_release_downloads(repo: str) -> dict:
    """Cumulative downloads per release asset. GitHub provides no time
    series here, so we just take a snapshot — the dashboard can derive
    deltas across snapshots itself. Empty when the repo has no releases
    (Phosphene currently ships via Pinokio clone, not release binaries)."""
    try:
        rs = _get(f"/repos/{repo}/releases?per_page=100")
        if not isinstance(rs, list):
            return {}
    except urllib.error.HTTPError:
        return {}
    total = 0
    per_release: dict = {}
    for rel in rs:
        tag = rel.get("tag_name", "?")
        rel_total = 0
        for a in rel.get("assets") or []:
            n = int(a.get("download_count", 0))
            rel_total += n
            total += n
        per_release[tag] = rel_total
    return {"total": total, "by_release": per_release}


# ---- summary helpers -----------------------------------------------------


def _window_total(window: list[dict]) -> int:
    return sum(int(d.get("count", 0)) for d in window)


def _wow_delta(window: list[dict]) -> int:
    """Week-over-week count delta inside the same 15-day window. Compares
    the last 7 entries to the 7 before them. Returns 0 if we don't have
    14 days of data yet."""
    if len(window) < 14:
        return 0
    recent = sum(int(d.get("count", 0)) for d in window[-7:])
    prior = sum(int(d.get("count", 0)) for d in window[-14:-7])
    return recent - prior


def _stars_wow_delta(timeline: list[dict]) -> int:
    """Stars gained in the last 7 UTC days, vs the 7 before. Cumulative
    timeline → derive deltas."""
    if len(timeline) < 2:
        return 0
    now = datetime.now(timezone.utc).date()
    today_iso = now.isoformat()
    # Find cumulative star count as of T, T-7d, T-14d. timeline is
    # cumulative-by-day with gaps; carry-forward by using the last entry
    # whose date <= target.
    def cum_at(target: str) -> int:
        last = 0
        for entry in timeline:
            if entry.get("date", "") <= target:
                last = int(entry.get("count", 0))
            else:
                break
        return last
    seven = (now.toordinal() - 7)
    fourteen = (now.toordinal() - 14)
    seven_iso = datetime.fromordinal(seven).strftime("%Y-%m-%d")
    fourteen_iso = datetime.fromordinal(fourteen).strftime("%Y-%m-%d")
    recent = cum_at(today_iso) - cum_at(seven_iso)
    prior = cum_at(seven_iso) - cum_at(fourteen_iso)
    return recent - prior


# ---- main ----------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Fetch GitHub repo stats and append a daily JSONL row.",
    )
    p.add_argument(
        "--repo",
        default=DEFAULT_REPO,
        help=f"OWNER/NAME (default: {DEFAULT_REPO}, or $PHOSPHENE_REPO)",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and print, but don't write the JSONL file.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    repo = args.repo

    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    iso_now = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{iso_now}] fetching stats for {repo}"
          + ("  (dry-run)" if args.dry_run else ""), flush=True)

    repo_obj = fetch_repo(repo)
    print(f"  repo: {repo_obj}", flush=True)
    open_prs = fetch_open_prs(repo)
    repo_obj["open_prs"] = open_prs
    # open_issues from the repo API includes PRs — subtract to disambiguate.
    repo_obj["open_issues"] = max(0, repo_obj["open_issues"] - open_prs)
    print(f"  prs: {open_prs} (open_issues adjusted to {repo_obj['open_issues']})",
          flush=True)

    # The actual open issues + PRs, so the dashboard renders a live
    # attention board (not just the count). Cheap on a normal repo; capped.
    open_items = fetch_open_items(repo)
    open_items_truncated = len(open_items) >= OPEN_ITEMS_CAP
    _n_pr = sum(1 for it in open_items if it.get("is_pr"))
    print(f"  open items: {len(open_items)} listed "
          f"({len(open_items) - _n_pr} issues · {_n_pr} prs)"
          + ("  (TRUNCATED)" if open_items_truncated else ""), flush=True)

    clones_window = fetch_clones_window(repo)
    views_window = fetch_views_window(repo)
    # Back-compat: most-recent-day slice as a dict.
    if clones_window:
        last = clones_window[-1]
        clones_today = {"count": last["count"], "uniques": last["uniques"]}
    else:
        clones_today = {"count": 0, "uniques": 0}
    if views_window:
        last = views_window[-1]
        views_today = {"count": last["count"], "uniques": last["uniques"]}
    else:
        views_today = {"count": 0, "uniques": 0}
    print(f"  clones window: {len(clones_window)} days, "
          f"total={_window_total(clones_window)}, "
          f"today={clones_today['count']} ({clones_today['uniques']} unique)",
          flush=True)
    print(f"  views  window: {len(views_window)} days, "
          f"total={_window_total(views_window)}, "
          f"today={views_today['count']} ({views_today['uniques']} unique)",
          flush=True)

    timeline, partial = fetch_stars_timeline(repo, repo_obj["stars"])
    if partial:
        print(f"  stars timeline: PARTIAL — capped at {STARS_CAP} "
              f"(repo has {repo_obj['stars']})", flush=True)
    else:
        print(f"  stars timeline: {len(timeline)} day-buckets, "
              f"final cum={(timeline[-1]['count'] if timeline else 0)}",
              flush=True)

    # Full historical backfill — captured fresh each run so old days
    # never need to be re-derived. These are the signals the GitHub
    # Traffic API can't ever give us beyond 14 days (clones/views), but
    # everything else has full history available retroactively. Mr Bizarro
    # 2026-05-22: "Can you retroactively pull the data from the first
    # days of the project?" — yes, for these.
    forks_timeline, forks_partial = fetch_forks_timeline(repo, repo_obj["forks"])
    if forks_partial:
        print(f"  forks timeline: PARTIAL — capped at {FORKS_CAP}", flush=True)
    else:
        print(f"  forks timeline: {len(forks_timeline)} day-buckets, "
              f"final cum={(forks_timeline[-1]['count'] if forks_timeline else 0)}",
              flush=True)

    issue_pr = fetch_issue_pr_timelines(repo)
    print(f"  issues/PRs: opened {len(issue_pr['issues_opened'])}/"
          f"{len(issue_pr['prs_opened'])} buckets, "
          f"closed {len(issue_pr['issues_closed'])}/"
          f"{len(issue_pr['prs_closed'])} buckets", flush=True)

    commits_timeline = fetch_commits_timeline(repo)
    commits_total_365 = sum(d["count"] for d in commits_timeline)
    print(f"  commits timeline (365d): {len(commits_timeline)} active days, "
          f"{commits_total_365} commits total", flush=True)

    release_timeline = fetch_release_timeline(repo)
    print(f"  release timeline: {len(release_timeline)} releases", flush=True)

    referrers = fetch_referrers(repo)
    paths = fetch_paths(repo)
    releases = fetch_release_downloads(repo)
    print(f"  referrers: {len(referrers)}  paths: {len(paths)}  "
          f"release downloads total: {releases.get('total', 0)}", flush=True)

    row = {
        "date": today,
        "fetched_at": iso_now,
        **repo_obj,
        "clones": clones_today,
        "views":  views_today,
        "clones_window": clones_window,
        "views_window":  views_window,
        "stars_timeline":   timeline,
        "forks_timeline":   forks_timeline,
        "issues_opened":    issue_pr["issues_opened"],
        "issues_closed":    issue_pr["issues_closed"],
        "prs_opened":       issue_pr["prs_opened"],
        "prs_closed":       issue_pr["prs_closed"],
        "open_items":            open_items,
        "open_items_truncated":  open_items_truncated,
        "commits_timeline": commits_timeline,
        "release_timeline": release_timeline,
        "referrers": referrers,
        "paths":     paths,
        "releases":  releases,
    }

    if args.dry_run:
        print("  dry-run: not writing JSONL", flush=True)
    else:
        # Idempotent append: replace today's row if it already exists.
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        existing: list[dict] = []
        if OUTPUT.exists():
            for line in OUTPUT.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    if obj.get("date") == today:
                        continue  # drop today's prior row
                    existing.append(obj)
                except json.JSONDecodeError:
                    continue
        existing.append(row)
        # 2026-05-31 review fix (D1): write atomically. The old direct
        # write_text() truncated the file before writing — a crash/kill
        # mid-write (or disk-full) destroyed ALL accumulated history, and
        # clones/views older than GitHub's 14-day window are unrecoverable.
        # Temp file + fsync + os.replace: the real file is always either the
        # complete old version or the complete new version, never torn. Guard
        # against writing an empty payload over good data.
        payload = "\n".join(json.dumps(r, ensure_ascii=False) for r in existing) + "\n"
        if not existing or not payload.strip():
            print("  WARN: refusing to overwrite stats with an empty payload", flush=True)
        else:
            import os as _os
            _tmp = OUTPUT.with_name(f".{OUTPUT.name}.{_os.getpid()}.tmp")
            with open(_tmp, "w", encoding="utf-8") as _fh:
                _fh.write(payload)
                _fh.flush()
                try:
                    _os.fsync(_fh.fileno())
                except OSError:
                    pass
            _os.replace(_tmp, OUTPUT)
            print(f"  wrote {len(existing)} rows -> "
                  f"{OUTPUT.relative_to(OUTPUT.parent.parent)}", flush=True)

    # Summary line for GitHub Actions step output.
    clones_wow = _wow_delta(clones_window)
    views_wow = _wow_delta(views_window)
    stars_wow = _stars_wow_delta(timeline)
    summary = (
        f"summary: stars={repo_obj['stars']} ({stars_wow:+d} w/w)  "
        f"clones={_window_total(clones_window)} ({clones_wow:+d} w/w)  "
        f"views={_window_total(views_window)} ({views_wow:+d} w/w)  "
        f"referrers={len(referrers)}  paths={len(paths)}"
    )
    print(summary, flush=True)

    rl = _last_rate_limit
    if rl["remaining"] is not None:
        print(f"  rate-limit remaining: {rl['remaining']}/{rl['limit']}",
              flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
