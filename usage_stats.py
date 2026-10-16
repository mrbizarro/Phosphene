"""Usage log and stats report: the local usage log, the local report and the fleet (PostHog) report.

Reads and writes state/usage-log.jsonl and builds the numbers behind the stats dashboard; the event contract is docs/ANALYTICS.md.

Moved verbatim out of mlx_ltx_panel.py. The body of each function is unchanged
except that panel globals and this module's own functions are spelled
`P.<name>` (the same mechanical rename panel/routes_*.py uses). `P` is the
RUNNING panel module, assigned by mlx_ltx_panel where it imports this module;
it is never imported here because the panel usually runs as __main__ and
importing it by name would execute the whole file a second time. Routing every
cross-reference through `P` keeps test patches effective: a test that patches
`P.USAGE_LOG_FILE` or `P._usage_fleet_query_one` still reaches the code that
uses it. Constants and locks stay defined in the panel.

The names below stay importable from mlx_ltx_panel, which re-exports them, so
panel/routes_*.py and the tests are unchanged.
"""
from __future__ import annotations

from urllib.parse import quote
import json
import os
import time
import urllib.request

P = None  # the running mlx_ltx_panel module; assigned where the panel imports this


def _usage_log_rotate() -> None:
    """Halve the local mirror when it hits the cap. Keeps the NEWEST half —
    the dashboard's windows are all 7/14-day, so old lines have no readers.
    Rewrites in place via the same temp+replace shape the rest of the panel
    uses so a kill mid-rotate can't truncate the log to nothing."""
    try:
        lines = P.USAGE_LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return
    keep = lines[len(lines) // 2:]
    tmp = P.USAGE_LOG_FILE.with_name(f".{P.USAGE_LOG_FILE.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
        os.replace(tmp, P.USAGE_LOG_FILE)
    except OSError:
        try: tmp.unlink()
        except OSError: pass


def _usage_log_append(record: dict) -> None:
    """Append one JSON line to the local mirror. Never raises."""
    try:
        P._ensure_state_dir()
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with P._USAGE_LOG_LOCK:
            try:
                if P.USAGE_LOG_FILE.stat().st_size + len(line) > P.USAGE_LOG_MAX_BYTES:
                    P._usage_log_rotate()
            except OSError:
                pass  # missing file is the normal first-write case
            with P.USAGE_LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:
        pass


def _usage_log_read(max_age_days: float | None = None) -> list[dict]:
    """Parse the local mirror. Bad lines are skipped, not fatal — this file
    is append-only from several threads and a torn last line is possible."""
    out: list[dict] = []
    try:
        raw = P.USAGE_LOG_READ_FILE.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    cutoff = (time.time() - max_age_days * 86400) if max_age_days else None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(rec, dict) or "event" not in rec:
            continue
        if cutoff is not None:
            try:
                if float(rec.get("ts") or 0) < cutoff:
                    continue
            except (TypeError, ValueError):
                continue
        out.append(rec)
    return out


def _usage_rank(counter: dict, key_name: str, limit: int = 12) -> list[dict]:
    """{value: count} -> sorted [{<key_name>: value, "count": n}] rows."""
    rows = sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))
    return [{key_name: k, "count": v} for k, v in rows[:limit]]


def _usage_local_report(range_key: str = None) -> dict:
    """Aggregate the local mirror into the dashboard's payload shape.

    Deliberately the SAME shape the fleet path produces, so stats.html has
    exactly one renderer and the only difference the user sees is the
    `source` label and the note above the tiles.

    Day buckets use UTC (time.gmtime), matching the fleet path's HogQL
    `toDate(timestamp)` (ClickHouse dates are UTC by default) and the
    GitHub-stats archive's UTC `date` field. This used to bucket by the
    MACHINE's own local midnight — on Mr Bizarro's Mac that is UTC+2/+3, so
    the same calendar day could show a different render count depending on
    whether /stats/usage happened to be serving the local fallback or the
    fleet view that day. A single-machine report has no meaningful "vs
    prior period" delta at small counts (2 renders vs 3 reads as "+50%"),
    so this path leaves deltas out rather than manufacture noise — the
    fleet path is where deltas matter and where there's enough volume for
    them to mean something."""
    if range_key is None:  # default is a panel constant; P is not assigned yet when this def runs
        range_key = P.STATS_RANGE_DEFAULT
    recs = P._usage_log_read()
    now = time.time()
    days = P.STATS_RANGE_DAYS.get(range_key, P.STATS_RANGE_DAYS[P.STATS_RANGE_DEFAULT])
    range_cutoff = (now - days * 86400) if days else 0.0
    # Every windowed figure below (boots, engines/H3 share, error rate,
    # versions/chips/RAM, pack flips) follows the selected range, same as
    # the fleet path. Only renders_by_day keeps its fixed 30-day chart.
    d30 = now - 30 * 86400

    boots_by_day: dict[str, int] = {}
    engines: dict[str, int] = {}
    errors: dict[str, int] = {}
    error_groups: dict[str, dict] = {}
    versions: dict[str, int] = {}
    chips: dict[str, int] = {}
    rams: dict[str, int] = {}
    flips: dict[tuple, int] = {}
    refusals: dict[str, int] = {}
    renders_in_range = ok_in_range = fail_in_range = 0
    h3_in_range = tot_in_range = 0
    refused_in_range = 0
    active_in_range = False

    total_renders = 0
    renders_by_day: dict[str, int] = {}
    first_boot_ts = None
    pack_seq: dict[str, list] = {}
    last_boot_ts = 0.0

    for rec in recs:
        try:
            ts = float(rec.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        ev = str(rec.get("event") or "")
        props = rec.get("props") if isinstance(rec.get("props"), dict) else {}
        if ts >= range_cutoff:
            active_in_range = True
        if ev == "app_boot":
            if first_boot_ts is None or ts < first_boot_ts:
                first_boot_ts = ts
            if ts >= range_cutoff:
                day = time.strftime("%Y-%m-%d", time.gmtime(ts))
                boots_by_day[day] = boots_by_day.get(day, 0) + 1
                versions[str(props.get("version") or "unknown")] = \
                    versions.get(str(props.get("version") or "unknown"), 0) + 1
                chips[str(props.get("chip_family") or "unknown")] = \
                    chips.get(str(props.get("chip_family") or "unknown"), 0) + 1
                ram_key = str(props.get("ram_gb") or "unknown")
                rams[ram_key] = rams.get(ram_key, 0) + 1
        elif ev in ("render_completed", "render_failed"):
            if ev == "render_completed":
                total_renders += 1
                if ts >= d30:
                    day = time.strftime("%Y-%m-%d", time.gmtime(ts))
                    renders_by_day[day] = renders_by_day.get(day, 0) + 1
            if ts >= range_cutoff:
                renders_in_range += 1
            if ts >= range_cutoff:
                tot_in_range += 1
                eng = str(props.get("engine") or "unknown")
                engines[eng] = engines.get(eng, 0) + 1
                if eng == "h3":
                    h3_in_range += 1
                if ev == "render_completed":
                    ok_in_range += 1
                else:
                    fail_in_range += 1
            if ev == "render_failed" and ts >= range_cutoff:
                sig = str(props.get("error_signature") or "unknown error")
                errors[sig] = errors.get(sig, 0) + 1
                cls = str(props.get("error_class") or "other")
                g = error_groups.setdefault(cls, {
                    "count": 0, "first_seen": ts, "last_seen": ts,
                    "sigs": {},
                })
                g["count"] += 1
                g["first_seen"] = min(g["first_seen"], ts)
                g["last_seen"] = max(g["last_seen"], ts)
                g["sigs"][sig] = g["sigs"].get(sig, 0) + 1
        elif ev == "render_refused":
            # DELIBERATELY OUTSIDE every render number above. A refusal did
            # not render, did not fail and cost no GPU time, so it belongs
            # in neither the numerator nor the denominator of the error
            # rate — counting it in either is the bug this branch exists to
            # prevent. It gets its own tile because "how often do we send
            # someone into a wall" is a real question, just a different one.
            if ts >= range_cutoff:
                refused_in_range += 1
                slug = str(props.get("refusal") or "unknown")
                refusals[slug] = refusals.get(slug, 0) + 1
        elif ev == "pack_state_change" and ts >= range_cutoff:
            k = (str(props.get("pack") or "?"),
                 bool(props.get("from")), bool(props.get("to")))
            flips[k] = flips.get(k, 0) + 1
            pack_seq.setdefault(k[0], []).append((ts, k[1], k[2]))
        if ev == "app_boot":
            last_boot_ts = max(last_boot_ts, ts)

    flip_rows = [{"pack": p, "from": f, "to": t, "count": c}
                 for (p, f, t), c in sorted(flips.items(), key=lambda kv: -kv[1])]
    h3_lost = sum(r["count"] for r in flip_rows
                  if r["pack"] == "h3" and r["from"] and not r["to"])
    # Same per-install shape as the fleet's pack_losses (this Mac = 1 install).
    _loss_rows, _boot_rows = [], [("local", last_boot_ts)] if last_boot_ts else []
    for _pk, _seq in pack_seq.items():
        _seq.sort()
        _lost = [t for t, f, to in _seq if f and not to]
        if _lost:
            _loss_rows.append((_pk, "local", _seq[-1][2], max(_lost)))
    pack_losses = P._usage_pack_loss_summary(_loss_rows, _boot_rows)

    error_group_rows = []
    for cls, g in error_groups.items():
        top_sig = max(g["sigs"].items(), key=lambda kv: kv[1])[0] if g["sigs"] else "unknown error"
        error_group_rows.append({
            "error_class": cls,
            "count": g["count"],
            "installs": 1,  # local mirror is always this one machine
            # ISO 8601 with a Z suffix — same shape PostHog's timestamp
            # column returns, so the dashboard's one JS date parser handles
            # both sources identically.
            "first_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(g["first_seen"])),
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(g["last_seen"])),
            "top_message": top_sig,
            "signatures": len(g["sigs"]),
        })
    error_group_rows.sort(key=lambda r: (-r["installs"], -r["count"]))

    return {
        "ok": True,
        "source": "local",
        "generated_at": P.iso_now(),
        "cached": False,
        "range": range_key,
        "note": ("this machine only - add a PostHog query key in Settings "
                 "for fleet data"),
        "tiles": {
            "weekly_active_installs": 1 if active_in_range else 0,
            # The stats-v2 tile reads active_in_range (the fleet path's
            # name); without it the local fallback showed "—" (4.17.3 review).
            "active_in_range": 1 if active_in_range else 0,
            "total_renders": total_renders,
            "total_installs": 1 if first_boot_ts else 0,
            "rendered_installs": 1 if total_renders else 0,
            "never_rendered_installs": 1 if (first_boot_ts and not total_renders) else 0,
            "renders_in_range": renders_in_range,
            "h3_share_pct": round(100.0 * h3_in_range / tot_in_range, 1) if tot_in_range else None,
            "error_rate_pct": (round(100.0 * fail_in_range / (ok_in_range + fail_in_range), 1)
                               if (ok_in_range + fail_in_range) else None),
            "refusals_in_range": refused_in_range,
        },
        "deltas": {},  # single machine, small counts — see docstring
        "growth": P._usage_growth_block(
            [(time.strftime("%Y-%m-%d", time.gmtime(first_boot_ts)), 1)] if first_boot_ts else [],
            [(d, renders_by_day[d]) for d in sorted(renders_by_day)],
            []),
        "boots_by_day": [{"date": d, "count": boots_by_day[d]}
                         for d in sorted(boots_by_day)],
        "engines": P._usage_rank(engines, "engine", 8),
        "top_errors": [{"signature": k, "count": v} for k, v in
                       sorted(errors.items(), key=lambda kv: (-kv[1], kv[0]))[:5]],
        "error_groups": error_group_rows,
        "top_refusals": [{"refusal": k, "count": v} for k, v in
                         sorted(refusals.items(),
                                key=lambda kv: (-kv[1], kv[0]))[:5]],
        "versions": P._usage_rank(versions, "version"),
        "chips": P._usage_rank(chips, "chip"),
        "ram": P._usage_rank(rams, "ram_gb"),
        "pack_flips": {"h3_lost": h3_lost, "rows": flip_rows,
                       "losses": pack_losses},
        "events_logged": len(recs),
    }


def _usage_growth_block(installs_by_day: list, renders_by_day: list,
                        active_by_week: list) -> dict:
    """The dashboard's growth series, one shape for fleet and local.

    installs_by_day carries the cumulative count alongside the day's new
    installs so the page draws the curve without re-summing; the last
    cumulative value equals total installs by construction."""
    inst, running = [], 0
    for d, c in installs_by_day:
        running += int(c)
        inst.append({"date": str(d), "new": int(c), "cumulative": running})
    return {
        "installs_by_day": inst,
        "renders_by_day": [{"date": str(d), "count": int(c)} for d, c in renders_by_day],
        "active_by_week": [{"week": str(w), "installs": int(c)} for w, c in active_by_week],
    }


def _usage_parse_ts(v) -> float | None:
    """A HogQL timestamp ('2026-10-09T08:42:11Z', '...+00:00', or with a
    space) as epoch seconds, or None."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    from datetime import datetime, timezone
    txt = str(v).strip().replace(" ", "T")
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(txt)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _usage_rolling_weeks(rows: list, now: float | None = None) -> list:
    """active_by_week rows [(k, installs)] -> [(window_end_date, installs)],
    oldest first. k is whole 7-day windows before now; the window k ends at
    now - 7k days and is labelled by that end date (UTC)."""
    now = time.time() if now is None else now
    out = []
    for r in rows:
        try:
            k, c = int(r[0]), int(r[1])
        except (TypeError, ValueError, IndexError):
            continue
        if k < 0 or k > 11:
            continue  # the 84-day window can spill one partial 13th bucket
        out.append((time.strftime("%Y-%m-%d", time.gmtime(now - k * 7 * 86400)), c, k))
    out.sort(key=lambda t: -t[2])
    return [(d, c) for d, c, _k in out]


def _usage_pack_loss_summary(loss_rows: list, last_boot_rows: list | None) -> dict:
    """{pack: {"installs", "recovered", "still_missing", "not_seen_since",
    "unconfirmed"}}.

    One entry per install that LOST the pack (installed -> missing) in the
    range. `recovered`: its latest pack change put the pack back — missing at
    one launch, present at a later one (the launch-during-Update pattern).
    `still_missing`: still gone, and the install has launched again since,
    so a later boot confirmed it. `not_seen_since`: still gone at the last
    launch we know of, which was the launch that noticed — nothing confirms
    or clears it yet. `unconfirmed`: not recovered, and the last-launch query
    failed (last_boot_rows is None) — never guessed into either bucket."""
    last_boot: dict[str, float] = {}
    for r in last_boot_rows or []:
        try:
            ts = P._usage_parse_ts(r[1])
        except (IndexError, TypeError):
            continue
        if ts is not None:
            last_boot[str(r[0])] = ts
    out: dict[str, dict] = {}
    for r in loss_rows or []:
        if not (isinstance(r, (list, tuple)) and len(r) >= 4):
            continue
        pack, did, last_to, lost_at = str(r[0] or "?"), str(r[1]), r[2], P._usage_parse_ts(r[3])
        b = out.setdefault(pack, {"installs": 0, "recovered": 0,
                                  "still_missing": 0, "not_seen_since": 0,
                                  "unconfirmed": 0})
        b["installs"] += 1
        if str(last_to).lower() in ("true", "1"):
            b["recovered"] += 1
        elif last_boot_rows is None:
            b["unconfirmed"] += 1
        elif lost_at is not None and last_boot.get(did, 0) > lost_at + 60:
            b["still_missing"] += 1
        else:
            b["not_seen_since"] += 1
    return out


def _usage_fleet_query_one(hogql: str, key: str) -> list:
    """Run one HogQL query. Returns the `results` rows, or [] on any error.

    10 s timeout (not the 2 s capture timeout — this is a human waiting on a
    dashboard, not a render path). Raises nothing; the caller treats an
    empty result as "this panel of the dashboard has no data"."""
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": hogql}}).encode("utf-8")
    project = (os.environ.get("PHOSPHENE_ANALYTICS_PROJECT") or "@current").strip()
    req = urllib.request.Request(
        f"{P._analytics_api_host()}/api/projects/{quote(project)}/query/",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {key}",
                 "User-Agent": "phosphene-panel-stats"},
        method="POST",
    )
    # 25 s: the owner-exclusion subqueries make the all-time queries heavier,
    # and at 10 s the all-range pack query timed out on every attempt.
    with urllib.request.urlopen(req, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    rows = data.get("results")
    return rows if isinstance(rows, list) else []


def _usage_fleet_report(range_key: str = None) -> dict | None:
    """Build the fleet payload, or None when no query key is configured.

    Every sub-query is independent: one failing (rate limit, schema drift,
    a property that no install has sent yet) leaves that panel empty rather
    than collapsing the whole view back to local. If EVERY query fails we
    return None so the caller falls back to local with a warning."""
    if range_key is None:  # default is a panel constant; P is not assigned yet when this def runs
        range_key = P.STATS_RANGE_DEFAULT
    key = P._analytics_query_key()
    if not key:
        return None
    queries = P._fleet_queries(range_key)
    res: dict[str, list] = {}
    failed: set[str] = set()

    # Four at a time, one retry each. Run one after another, 24 queries took
    # over a minute per range, and a single 10 s timeout under load blanked
    # its tile — the measured 2026-10-14 run lost total_installs,
    # total_renders and the pack queries to exactly that ("partial").
    def _run(item):
        name, hogql = item
        for attempt in (1, 2):
            try:
                return name, P._usage_fleet_query_one(hogql, key), True
            except Exception:
                if attempt == 2:
                    return name, [], False
                time.sleep(1.5)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="phos-fleet-q") as ex:
        for name, rows, ok in ex.map(_run, queries.items()):
            res[name] = rows
            if not ok:
                failed.add(name)
    failures = len(failed)
    if failures == len(queries):
        return None

    def _scalar(name):
        rows = res.get(name) or []
        try:
            return rows[0][0]
        except (IndexError, TypeError):
            return None

    def _outcomes(name):
        rows = {str(r[0]): int(r[1]) for r in (res.get(name) or [])
                if isinstance(r, (list, tuple)) and len(r) >= 2}
        return rows.get("render_completed", 0), rows.get("render_failed", 0)

    # `people` rides along because a refusal's whole question is "how many
    # DIFFERENT people did we send into this wall" — 65 events from 16
    # people is a product bug; 65 from one person is a bookmark.
    refusals = [{"refusal": str(r[0] or "unknown"), "count": int(r[1]),
                 "people": int(r[2]) if len(r) >= 3 else 0}
                for r in (res.get("refusals") or [])
                if isinstance(r, (list, tuple)) and len(r) >= 2]
    ok_n, fail_n = _outcomes("outcomes")
    ok_prior, fail_prior = _outcomes("outcomes_prior")
    engines = [{"engine": str(r[0] or "unknown"), "count": int(r[1])}
               for r in (res.get("engines") or [])
               if isinstance(r, (list, tuple)) and len(r) >= 2]
    tot = sum(e["count"] for e in engines)
    h3 = sum(e["count"] for e in engines if e["engine"] == "h3")

    flip_rows = []
    for r in (res.get("pack_flips") or []):
        if not (isinstance(r, (list, tuple)) and len(r) >= 4):
            continue
        flip_rows.append({
            "pack": str(r[0] or "?"),
            "from": str(r[1]).lower() in ("true", "1"),
            "to": str(r[2]).lower() in ("true", "1"),
            "count": int(r[3]),
        })
    h3_lost = sum(r["count"] for r in flip_rows
                  if r["pack"] == "h3" and r["from"] and not r["to"])
    pack_losses = P._usage_pack_loss_summary(
        res.get("pack_losses") or [],
        None if "pack_loss_last_boot" in failed else (res.get("pack_loss_last_boot") or []))

    weeks = P._usage_rolling_weeks(res.get("active_by_week") or [])
    # Drop a window that starts before the first install ever reported: it
    # is part-empty by construction and would draw as a collapse.
    _first = next((str(r[0]) for r in (res.get("installs_by_day") or [])
                   if isinstance(r, (list, tuple)) and r), None)
    if _first:
        import calendar
        weeks = [(d, c) for d, c in weeks
                 if time.strftime("%Y-%m-%d", time.gmtime(
                     calendar.timegm(time.strptime(d, "%Y-%m-%d")) - 7 * 86400)) >= _first[:10]]
    # The weekly-active tile IS the curve's last window (k == 0), so the two
    # can never disagree; the stand-alone 'wau' query is the fallback.
    wau = next((c for d, c in weeks[-1:]), None) if weeks else None
    if wau is None:
        wau = _scalar("wau")
    active_n = _scalar("active_in_range")
    if range_key == "7d" and wau is not None:
        active_n = wau
    active_prior_n = _scalar("active_in_range_prior")
    error_rate_n = (round(100.0 * fail_n / (ok_n + fail_n), 1)
                    if (ok_n + fail_n) else None)
    error_rate_prior = (round(100.0 * fail_prior / (ok_prior + fail_prior), 1)
                        if (ok_prior + fail_prior) else None)

    error_groups = []
    for r in (res.get("error_groups") or []):
        if not (isinstance(r, (list, tuple)) and len(r) >= 6):
            continue
        error_groups.append({
            "error_class": str(r[0] or "other"),
            "count": int(r[1] or 0),
            "installs": int(r[2] or 0),
            "first_seen": str(r[3] or ""),
            "last_seen": str(r[4] or ""),
            "top_message": str(r[5] or "unknown error"),
        })

    return {
        "ok": True,
        "source": "fleet",
        "generated_at": P.iso_now(),
        "cached": False,
        "range": range_key,
        "note": "",
        "partial": failures > 0,
        "tiles": {
            "weekly_active_installs": wau,
            "daily_active_installs": _scalar("dau"),
            "total_renders": _scalar("total_renders"),
            "total_installs": _scalar("total_installs"),
            "rendered_installs": _scalar("rendered_installs"),
            "never_rendered_installs": (
                max(0, int(_scalar("total_installs")) - int(_scalar("rendered_installs")))
                if _scalar("total_installs") is not None
                and _scalar("rendered_installs") is not None else None),
            "active_in_range": active_n,
            "renders_in_range": ok_n + fail_n,
            "h3_share_pct": round(100.0 * h3 / tot, 1) if tot else None,
            "error_rate_pct": error_rate_n,
            # Not in renders_in_range and not in error_rate_pct — see the
            # local aggregator's render_refused branch for why.
            "refusals_in_range": sum(r["count"] for r in refusals),
        },
        # None (not zeroed) for range_key == "all". _fleet_window_sql
        # returns an EMPTY clause for both the current and prior "all"
        # queries (there is no window to bound them by), which means
        # ok_prior/fail_prior/active_prior_n above are the SAME all-time
        # totals as the current ones — _pct_delta(x, x) computes a real
        # 0.0, not "no baseline". Gate on range_key explicitly rather than
        # trust the arithmetic: "all time" has no prior period, full stop.
        "deltas": {} if range_key == "all" else {
            "renders_in_range_pct": P._pct_delta(ok_n + fail_n, ok_prior + fail_prior),
            "active_in_range_pct": P._pct_delta(active_n, active_prior_n),
            "error_rate_pp": (round(error_rate_n - error_rate_prior, 1)
                              if error_rate_n is not None and error_rate_prior is not None
                              else None),
            # Raw failure-count trend for the Errors section header — a
            # separate question from the RATE above (fewer installs total
            # can raise the rate while the raw count falls).
            "errors_count_pct": P._pct_delta(fail_n, fail_prior),
        },
        "growth": P._usage_growth_block(
            [(r[0], r[1]) for r in (res.get("installs_by_day") or [])
             if isinstance(r, (list, tuple)) and len(r) >= 2],
            [(r[0], r[1]) for r in (res.get("renders_by_day") or [])
             if isinstance(r, (list, tuple)) and len(r) >= 2],
            weeks),
        "boots_by_day": [{"date": str(r[0]), "count": int(r[1])}
                         for r in (res.get("boots_by_day") or [])
                         if isinstance(r, (list, tuple)) and len(r) >= 2],
        "engines": engines,
        "top_errors": [{"signature": str(r[0] or "unknown error"), "count": int(r[1]),
                        "people": int(r[2]) if len(r) > 2 and r[2] is not None else None}
                       for r in (res.get("top_errors") or [])
                       if isinstance(r, (list, tuple)) and len(r) >= 2],
        "error_groups": error_groups,
        "failures_by_version": [{"version": str(r[0] or "unknown"), "failed": int(r[1] or 0),
                                 "ok": int(r[2] or 0), "people": int(r[3] or 0),
                                 "count": int(r[1] or 0),
                                 "pct": round(100.0 * int(r[1] or 0) / max(1, int(r[1] or 0) + int(r[2] or 0)), 1)}
                                for r in (res.get("failures_by_version") or [])
                                if isinstance(r, (list, tuple)) and len(r) >= 4],
        "top_refusals": refusals,
        "versions": [{"version": str(r[0] or "unknown"), "count": int(r[1])}
                     for r in (res.get("versions") or [])
                     if isinstance(r, (list, tuple)) and len(r) >= 2],
        "chips": [{"chip": str(r[0] or "unknown"), "count": int(r[1])}
                  for r in (res.get("chips") or [])
                  if isinstance(r, (list, tuple)) and len(r) >= 2],
        "ram": [{"ram_gb": str(r[0] or "unknown"), "count": int(r[1])}
                for r in (res.get("ram") or [])
                if isinstance(r, (list, tuple)) and len(r) >= 2],
        "pack_flips": {"h3_lost": h3_lost, "rows": flip_rows,
                       "losses": pack_losses},
        "owner_excluded": bool(P._owner_exclusion_sql()),
    }


def _usage_attach_local(report: dict) -> dict:
    """Ride this machine's own totals along with whatever we return.

    Owner, 2026-09-18: "when I ask you for total renders, I mean total renders
    from the fleet too… one of the first windows I want to see is total renders
    for all the fleet." The dashboard used to show ONE number called
    "Total renders" whose meaning silently changed with whether a query key was
    configured — the fleet's total, or this Mac's. Two numbers, both labelled,
    always: the fleet answers "how much is this app used", the machine answers
    "how much have I used it", and neither can be mistaken for the other.
    """
    try:
        # Fixed 7-day window regardless of the dashboard's selected range —
        # this block is an evergreen "how much have I used it" reference
        # (same idea as the fleet tiles' wau), not the range-scoped view.
        local = P._usage_local_report("7d")
    except Exception:
        return report
    report["machine"] = {
        "total_renders": (local.get("tiles") or {}).get("total_renders"),
        "renders_7d": (local.get("tiles") or {}).get("renders_in_range"),
        "error_rate_pct": (local.get("tiles") or {}).get("error_rate_pct"),
        "first_day": (((local.get("growth") or {}).get("installs_by_day") or [{}])[0]
                      or {}).get("date"),
    }
    return report


def _usage_report(force: bool = False, range_key: str = None) -> dict:
    """What GET /stats/usage returns. Fleet when a query key is configured
    and the 6 h cache is stale; otherwise the cache; otherwise local.

    Either way the payload carries `machine` (this Mac) AND says whether the
    fleet half is available — `fleet_blocked` is "no_key" or "query_failed",
    so the dashboard can show the fleet tile as unknown with a remedy instead
    of quietly substituting a local number under a fleet label.

    Cached PER RANGE (stats-v2, 2026-09-30): USAGE_FLEET_CACHE on disk holds
    one entry per range key, each with its own `_fetched_at`, so switching
    the dashboard's range control doesn't serve 7-day numbers under a
    30-day label or force a fresh PostHog round trip on every click — only
    the FIRST time a given range goes stale within the 6 h TTL."""
    if range_key is None:  # default is a panel constant; P is not assigned yet when this def runs
        range_key = P.STATS_RANGE_DEFAULT
    range_key = range_key if range_key in P.STATS_RANGE_DAYS else P.STATS_RANGE_DEFAULT
    with P._USAGE_FLEET_LOCK:
        if P._analytics_query_key():
            if not force:
                try:
                    all_cached = json.loads(P.USAGE_FLEET_CACHE.read_text(encoding="utf-8"))
                    cached = all_cached.get(range_key) if isinstance(all_cached, dict) else None
                    if (cached and cached.get("_schema") == P.USAGE_FLEET_SCHEMA
                            and time.time() - float(cached.get("_fetched_at") or 0)
                            < P.USAGE_FLEET_TTL_SEC):
                        cached = dict(cached)
                        cached["cached"] = True
                        cached["fleet_blocked"] = ""
                        return P._usage_attach_local(cached)
                except (OSError, ValueError, json.JSONDecodeError, AttributeError):
                    pass
            fleet = None
            try:
                fleet = P._usage_fleet_report(range_key)
            except Exception:
                fleet = None
            if fleet:
                fleet["_fetched_at"] = time.time()
                fleet["_schema"] = P.USAGE_FLEET_SCHEMA
                fleet["fleet_blocked"] = ""
                try:
                    P._ensure_state_dir()
                    try:
                        all_cached = json.loads(P.USAGE_FLEET_CACHE.read_text(encoding="utf-8"))
                        if not isinstance(all_cached, dict):
                            all_cached = {}
                    except (OSError, ValueError, json.JSONDecodeError):
                        all_cached = {}
                    all_cached[range_key] = fleet
                    P.atomic_write_text(P.USAGE_FLEET_CACHE,
                                      json.dumps(all_cached, indent=2))
                except Exception:
                    pass
                return P._usage_attach_local(fleet)
            local = P._usage_local_report(range_key)
            local["fleet_blocked"] = "query_failed"
            local["warning"] = ("PostHog query failed - showing this machine "
                                "only. Check the personal API key in Settings.")
            return P._usage_attach_local(local)
    local = P._usage_local_report(range_key)
    local["fleet_blocked"] = "no_key"
    return P._usage_attach_local(local)
