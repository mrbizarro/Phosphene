#!/usr/bin/env python3.11
"""Rebuild data/fleet_timings.json from the fleet's own real wall-clock data.

Owner direction (2026-09-29): "If we have the fleet data, we can provide
proper time estimations, or at least a range." Every estimate in the panel
before this was a MODEL — a per-chip speed factor times a cost function
fitted to a handful of measured renders, all on the maintainer's own Macs.
This script instead asks PostHog what the fleet's OWN installs actually
measured, at three levels of granularity, and the panel's runtime loader
(fleet_estimate_range() in mlx_ltx_panel.py) walks from the most specific
level down to the least specific, falling back to the model only when the
fleet has nothing to say.

WHAT IT QUERIES
    render_completed events, last WINDOW_DAYS days, this project's own
    PostHog (via HogQL — https://posthog.com/docs/hogql). Every field read
    here is documented in docs/ANALYTICS.md; nothing this script computes
    ever leaves a user's machine — the query runs from the MAINTAINER's
    own workstation using the maintainer's own read-only PostHog personal
    API key (the same key the /stats dashboard already uses, resolved the
    same way: Settings -> analytics_query_key in panel_settings.json).

THE OWNER'S OWN INSTALL IS EXCLUDED, by distinct_id (analytics_install_id
in the SAME settings file) — a maintainer boots this panel constantly for
development and testing, and that traffic would otherwise dominate every
"M4 Max 64 GB" cell it appears in.

THREE LEVELS, precomputed here (not reconstructed from each other at
runtime, because quantiles do not combine — re-deriving a coarser
quantile from finer ones would be a second, silently wrong model):
    cell  — engine, mode, tier, frames, chip_family, ram_gb, speed
            (speed only distinguishes anything for H3: <=5 steps is the
            Fast/TriStep lane, per docs/ANALYTICS.md's `steps` field)
    chip  — same, without ram_gb (every RAM class of one chip, pooled)
    model — engine, mode, tier, frames, speed only (every Mac, pooled) —
            the last real-data rung before the panel's own cost model

WHAT THE NUMBERS ARE: render_completed never reports an exact wall clock,
only `wall_sec_bucket` — the LOWER edge of the ladder rung the render fell
in (mlx_ltx_panel.FLEET_WALL_SEC_LADDER). The p25/p50/p75 written here are
quantiles of those lower edges, and the file says so (`"wall_sec":
"bucket_lower_edge"`). The panel's reader (_fleet_cell_seconds) turns them
back into what they support — p25 a rung's lower edge, p50 its middle, p75
its UPPER edge — so a cell whose renders all sit in the 10-15 min rung reads
"~10-15 min", not a tight "~10 min" (4.17 Codex EST-4). Keep the marker in
step with the reader if this script ever stores anything else.

WHAT'S NOT HERE: "same chip+RAM, other length, scaled" is level 2 of the
runtime fallback (VC-01's ledger) — it needs the REQUESTED cell's own
frame count to scale by, which only the running panel knows, so it lives
in fleet_estimate_range() itself, not in this precomputed table.

Run it from the repo root, with the venv that has network access:
    python3 scripts/fleet_timings_build.py

Reads the query key from the REAL install's settings (read-only) — never
prints it, never writes it to the output file, never commits it.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "data" / "fleet_timings.json"
WINDOW_DAYS = 75
# The maintainer's own live dev panel state — READ ONLY, one field pulled
# (analytics_query_key) and one more to exclude (analytics_install_id).
# Never the phosphene-dev.git CHECKOUT itself (CLAUDE.md forbids touching
# that panel); this is its persisted settings file on disk, read once.
SETTINGS_PATH = Path.home() / "pinokio" / "api" / "phosphene-dev.git" / "state" / "panel_settings.json"
POSTHOG_QUERY_URL = "https://us.posthog.com/api/projects/@current/query/"
# Render modes priced by the panel's Quality-strip / keyframe / extend / a2v
# estimates (VC-01/VA-06/VA-11/VA-12's territory) — image and training jobs
# have their own, unrelated estimate paths.
PRICED_MODES = ("t2v", "i2v", "a2v", "extend", "keyframe")
MIN_N_TO_SHIP = 3  # a cell with fewer samples than this is not written at all


def _posthog_key_and_owner() -> tuple[str, str]:
    try:
        settings = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"Could not read {SETTINGS_PATH} — this script must run on the "
            f"maintainer's own Mac, with Settings -> analytics_query_key set."
        ) from exc
    key = str(settings.get("analytics_query_key") or "").strip()
    owner = str(settings.get("analytics_install_id") or "").strip()
    if not key:
        raise SystemExit("analytics_query_key is empty in panel_settings.json — "
                         "set a PostHog personal API key in Settings first.")
    return key, owner


def _hogql(key: str, sql: str) -> list[list]:
    body = json.dumps({"query": {"kind": "HogQLQuery", "query": sql}}).encode("utf-8")
    req = urllib.request.Request(
        POSTHOG_QUERY_URL, data=body,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            out = json.loads(r.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:800]
        raise SystemExit(f"PostHog query failed ({exc.code}): {detail}") from exc
    if "results" not in out:
        raise SystemExit(f"PostHog query returned no results: {out}")
    return out["results"]


def _sql_literal(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


# lightx2v v1.3 on Best at High is its own lane, so its ~15 min renders never
# pull the full sampler's Best range down. 4.19.11 renders say which sampler
# ran (`h3_sampler`); `steps` cannot (7 points is v1.3, Turbo AND a Best
# pinned to 7), so an event without it - every version before 4.19.11, none of
# which ran v1.3 - falls back to the old rule: <= 5 points Fast, else Best.
# Turbo gets a lane of its own that no price reads, and so does Pika H3 Flex
# (also 7 points) until it has receipts of its own.
_SPEED_EXPR = (
    "multiIf(properties.engine != 'h3', '', "
    "properties.h3_sampler = 'v13', 'v13', "
    "properties.h3_sampler = 'flex', 'flex', "
    "properties.h3_sampler = 'fast', 'fast', "
    "properties.h3_sampler = 'turbo', 'turbo', "
    "properties.h3_sampler = 'best', 'best', "
    "toFloat64OrNull(toString(properties.steps)) <= 5, 'fast', 'best')"
)
_MODE_LIST = "(" + ",".join(_sql_literal(m) for m in PRICED_MODES) + ")"


def _quantile_select(group_cols: list[str]) -> str:
    cols = ",\n    ".join(group_cols)
    return f"""
SELECT
    {cols},
    quantile(0.25)(toFloat64OrNull(toString(properties.wall_sec_bucket))) p25,
    quantile(0.5)(toFloat64OrNull(toString(properties.wall_sec_bucket))) p50,
    quantile(0.75)(toFloat64OrNull(toString(properties.wall_sec_bucket))) p75,
    count() n
FROM events
WHERE event = 'render_completed'
  AND timestamp > now() - INTERVAL {WINDOW_DAYS} DAY
  AND properties.mode IN {_MODE_LIST}
"""


_CELL_COLS = [
    "properties.engine AS engine",
    "properties.mode AS mode",
    "properties.tier AS tier",
    "toFloat64OrNull(toString(properties.frames)) AS frames",
    "properties.chip_family AS chip_family",
    "toFloat64OrNull(toString(properties.ram_gb)) AS ram_gb",
    f"{_SPEED_EXPR} AS speed",
]
_CHIP_COLS = [c for c in _CELL_COLS if "ram_gb" not in c]
_MODEL_COLS = [c for c in _CHIP_COLS if "chip_family" not in c]


def _run_level(key: str, owner_lit: str, cols: list[str], *, exclude_owner: bool,
              require_chip: bool, limit: int) -> list[list]:
    sql = _quantile_select(cols)
    if exclude_owner and owner_lit:
        sql += f"  AND distinct_id != {owner_lit}\n"
    if require_chip:
        sql += "  AND properties.chip_family IS NOT NULL AND toString(properties.chip_family) != ''\n"
    group_names = [c.rsplit(" AS ", 1)[-1] for c in cols]
    sql += f"GROUP BY {', '.join(group_names)}\nHAVING n >= 1\nORDER BY n DESC\nLIMIT {limit}\n"
    return _hogql(key, sql)


def _row_to_cell(rec: dict) -> dict | None:
    p25, p50, p75, n = rec["p25"], rec["p50"], rec["p75"], int(rec["n"])
    if p25 is None or p50 is None or p75 is None or n < MIN_N_TO_SHIP:
        return None
    return {
        "p25_sec": round(float(p25), 1), "p50_sec": round(float(p50), 1),
        "p75_sec": round(float(p75), 1), "n": n,
    }


def _key_for(rec: dict, dims: list[str]) -> str | None:
    """The pipe-joined lookup key for one row. `speed` is legitimately ""
    for LTX (the axis only exists for H3's Fast/Best split) — "" there
    means "no speed axis", not "missing data", so it becomes the token
    "any" rather than rejecting the row. Every OTHER dimension rejects a
    missing value outright (frames/chip_family/etc with nothing behind
    them is a row this table cannot key reliably)."""
    parts = []
    for d in dims:
        v = rec.get(d)
        if v is None:
            return None
        if v == "":
            if d != "speed":
                return None
            parts.append("any")
            continue
        parts.append(str(v).strip())
    return "|".join(parts)


def build() -> dict:
    key, owner = _posthog_key_and_owner()
    owner_lit = _sql_literal(owner) if owner else ""

    levels = {
        "cell": (_CELL_COLS, ["engine", "mode", "tier", "frames", "chip_family", "ram_gb", "speed"], True, 4000),
        "chip": (_CHIP_COLS, ["engine", "mode", "tier", "frames", "chip_family", "speed"], True, 3500),
        "model": (_MODEL_COLS, ["engine", "mode", "tier", "frames", "speed"], False, 1200),
    }
    out_levels: dict[str, dict] = {}
    total_n = 0
    for level_name, (cols, dims, require_chip, limit) in levels.items():
        col_names = [c.rsplit(" AS ", 1)[-1] for c in cols]
        rows = _run_level(key, owner_lit, cols, exclude_owner=True,
                          require_chip=require_chip, limit=limit)
        cells: dict[str, dict] = {}
        for row in rows:
            rec = dict(zip(col_names + ["p25", "p50", "p75", "n"], row))
            k = _key_for(rec, dims)
            if not k:
                continue
            cell = _row_to_cell(rec)
            if cell is None:
                continue
            cells[k] = cell
            total_n += cell["n"]
        out_levels[level_name] = {"dims": dims, "cells": cells}
        print(f"[fleet_timings_build] {level_name}: {len(cells)} cells "
              f"(from {len(rows)} raw rows)", file=sys.stderr)

    return {
        "schema": "fleet_timings/1",
        # Quantiles of wall_sec_bucket LOWER edges — see the docstring.
        "wall_sec": "bucket_lower_edge",
        "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "window_days": WINDOW_DAYS,
        "owner_excluded": bool(owner),
        "min_n_to_ship": MIN_N_TO_SHIP,
        "priced_modes": list(PRICED_MODES),
        "total_events_counted": total_n,
        "levels": out_levels,
    }


def main() -> None:
    data = build()
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    n_cells = sum(len(lv["cells"]) for lv in data["levels"].values())
    print(f"[fleet_timings_build] wrote {OUT_PATH} — {n_cells} cells across "
          f"{len(data['levels'])} levels, {data['total_events_counted']} events counted",
          file=sys.stderr)


if __name__ == "__main__":
    main()
