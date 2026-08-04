# Phosphene repo stats — maintainer guide

A tiny, stdlib-only pipeline that snapshots GitHub repo metrics into a local
JSONL on the maintainer's Mac and serves a dashboard through the Phosphene
panel itself. Nothing public, no GitHub Pages, no telemetry, no cloud.

**Open the dashboard:** the Phosphene panel must be running, then visit
**<http://127.0.0.1:8199/stats>**.

## The pieces

| Path | Role | In public repo? |
|---|---|---|
| `scripts/fetch_repo_stats.py` | Stdlib-only fetcher. Hits GitHub's REST API, writes one JSON row per UTC day. | yes (code only, no data) |
| `panel_assets/stats.html` | Dashboard. Loads the JSONL at page load, renders cards + Chart.js charts client-side. No build step. | yes (template only, no data) |
| `state/stats-data.jsonl` | Append-only data store on the maintainer's Mac. | **NO** — gitignored, never leaves your machine |
| Panel routes `/stats` + `/stats/data` | Serve the dashboard + data file. Both 127.0.0.1-only via the panel's standard local-only guard. | n/a |
| Panel `stats_fetch_loop` background thread | Runs the fetcher once a day (and once at startup if the data file is stale or missing). | n/a |

## What you actually need to do

**Once:** explicitly enable the feature and provide a dedicated token to the
panel process:

```bash
PHOSPHENE_ENABLE_REPO_STATS=1 \
PHOSPHENE_REPO_STATS_TOKEN='<dedicated token>' \
./ltx-2-mlx/env/bin/python3.11 mlx_ltx_panel.py
```

The panel deliberately ignores `GH_TOKEN`, `GITHUB_TOKEN`, `GH_STATS_TOKEN`,
and the GitHub CLI keychain. An unrelated authenticated developer shell is not
consent to give Phosphene a push-capable token.

The token needs access to the target repository's Traffic API. Create a
dedicated, narrowly scoped token for this feature; do not reuse your normal
GitHub CLI credential.

**Then:** restart the panel. Within 90 seconds the background thread runs
the first fetch. Open `/stats` — done.

## Run the fetcher manually (debug / catch-up)

```bash
PHOSPHENE_REPO_STATS_TOKEN='<dedicated token>' \
  python3 scripts/fetch_repo_stats.py
```

Useful flags:

```bash
PHOSPHENE_REPO_STATS_TOKEN='<dedicated token>' \
  python3 scripts/fetch_repo_stats.py --dry-run
PHOSPHENE_REPO_STATS_TOKEN='<dedicated token>' \
  python3 scripts/fetch_repo_stats.py --repo OWNER/NAME
```

The fetcher is idempotent — re-running on the same UTC day REPLACES today's
row rather than appending a duplicate.

## What's collected

One JSON object per UTC day. Top-level fields (see the fetcher's docstring
for the canonical schema):

- `date`, `fetched_at`
- `stars`, `forks`, `watchers`, `subscribers`, `open_issues`, `open_prs`
- `clones` (today), `views` (today)
- `clones_window` / `views_window` — full 15-day Traffic API arrays
- `stars_timeline` — cumulative `[{date, count}]`. Logins are dropped, not
  stored — privacy by construction.
- `referrers` — top 10 with `{referrer, count, uniques}`, 14-day aggregate
- `paths` — top 10 with `{path, title, count, uniques}`
- `releases` — `{total, by_release}` (empty for Phosphene, which ships via
  Pinokio clone, not release binaries)

## What's deliberately NOT collected

GitHub's API cannot give you any of these no matter how hard you mine it:

- Apple Silicon tier of installs (M1 / M2 / M3 / M4 family/variant)
- Unified RAM per install
- macOS version
- Whether `pinokio install` actually completed
- Which modes get used (Character vs Base, Q4 vs Q8 HQ, Fast vs Exact)
- Render wall-times in the wild
- Error frequency / crash signatures
- Active vs lapsed users

If you ever want any of those you'd have to ship client-side telemetry,
which we explicitly decided against (the brief 2026-05-21 opt-in experiment
was rolled back the next day). GitHub-data signal is the floor and the
ceiling.

## Pinokio notes

Pinokio publishes NO install / activation stats per app. The only signal
you have that a Pinokio user installed Phosphene is the `clone` count
spike on the repo — indistinguishable from a `git clone` from a human, a
`git pull` in CI, or a researcher poking at the source. Don't read too
much into day-level fluctuations.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Dashboard at /stats is blank | No data yet (first fetch hasn't run) | Wait 90 s after panel boot or run fetcher manually |
| Panel log: `stats: enabled but PHOSPHENE_REPO_STATS_TOKEN is not set` | Feature enabled without its dedicated token | Set `PHOSPHENE_REPO_STATS_TOKEN`, then restart |
| Panel log: `stats: fetch failed (exit 1)` | Dedicated token lacks target-repository Traffic access | Replace it with a narrowly scoped token that can read Traffic data |
| `/stats/data` returns empty body | `state/stats-data.jsonl` doesn't exist yet | Same as "blank" above |
| Charts blank but cards filled | Only one snapshot so far — charts need ≥2 points | Wait for tomorrow's fetch or change system date for testing |
| Dashboard ships data into a public commit | Should be impossible after the 2026-05-22 rewrite — `state/` is gitignored | If you see this, file an issue immediately |

## FAQ

**Why no client-side telemetry?** Mr Bizarro's call on 2026-05-22: "not
going to be well accepted in the open source world." We stand on what
GitHub already publishes about the repo. Stays private; stays simple.

**Why is the data file local and not in the repo?** Daily clone / view
counts + referrer breakdowns are private business signal. If you want a
public dashboard down the road, that's a deliberate later decision — the
machinery is here.

**How long is the history?** Forever, in `state/stats-data.jsonl`. The
GitHub Traffic API only retains 14 days; this file is exactly how we
keep older days from disappearing.

**Can I rebuild old data?** No. The Traffic API's 15-day window is hard;
anything older than today − 14 days is unrecoverable. So don't delete
the JSONL.

**Can a teammate see this?** Only if they have access to your Mac and the
panel is running. The panel binds 127.0.0.1 with no auth — same posture
as the rest of Phosphene.
