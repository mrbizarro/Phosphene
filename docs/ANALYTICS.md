# Anonymous usage analytics

Phosphene sends a small number of anonymous counts so that bugs in the field
get noticed. This page is the complete specification: every event, every
field, every value. If the panel ever sends something that isn't listed here,
that's a bug — please open an issue.

**In one line:** counts and hardware classes, never content. No prompts, no
filenames, no paths, no images, no video, no audio, no seeds, no LoRA or
character names.

---

## The short version

| | |
|---|---|
| **What identifies you** | One random UUID, generated on your Mac, tied to nothing |
| **How often** | Once ever when the install is new, once per panel start, plus once per finished render. No heartbeats |
| **Where it goes** | PostHog (`us.i.posthog.com`), or a self-hosted endpoint you choose |
| **Location** | None sent, and every event tells the receiver not to derive one from your IP — [details](#location) |
| **How to turn it off** | Settings → *Anonymous usage analytics* → **Turn off**, or `PHOSPHENE_ANALYTICS_DISABLED=1` |
| **Default** | ON, and the shipped build carries a working key — see below |
| **What you can inspect** | `state/usage-log.jsonl` — a plain-text copy of everything the panel sends |
| **Effect on renders** | None. Background thread, 2 s timeout, all failures ignored |

**The build you install ships a project key.** Phosphene is distributed as
source — you `git clone` it through Pinokio — so a key left empty in the
repository would never reach anyone and these counts would not exist. What is
committed, in `mlx_ltx_panel.py` as `ANALYTICS_KEY_DEFAULT`, is a PostHog
**project key** (`phc_…`): write-only and client-side by design. It can send
events. It cannot read one back, list anything, or change the project. Reading
the data needs a separate *personal* key, which is **not** in this repository.
Shipping a client key in the source is what PostHog documents these keys for —
so the thing worth checking is not that a key exists, it's what the panel does
with it, which is the rest of this page.

**A fresh clone therefore does send.** Clone it, run it, and it sends exactly
the events listed below and nothing else. The first time it ever runs, it says
so — one line, in the panel log, before you have read anything:

> `Phosphene sends anonymous usage counts (version, hardware class, render
> stats, error signatures - never your prompts or media). Disable in Settings.`

**Turning it off is the toggle, not the key.** Settings → *Anonymous usage
analytics* → **Turn off** stops the network calls *and* the local log;
`PHOSPHENE_ANALYTICS_DISABLED=1` does the same and beats the setting. Emptying
the project-key field does **not** silence the panel: that field is an override
for forks and self-hosters, and an empty override falls back to the shipped
key. Use the toggle.

---

## How to turn it off

**Settings → Anonymous usage analytics → Turn off.** One click; no confirm
step. Turning it off stops the network calls *and* stops writing the local
log — `_analytics_capture()` returns before it builds a payload, so there is
nothing left to send or mirror.

Before the panel has ever written a settings file — or if you'd rather set it
in your environment — use:

```sh
PHOSPHENE_ANALYTICS_DISABLED=1
```

That env var wins over the setting, always.

**Clearing the project key is not an off switch.** It is an override field: an
empty override resolves back to the key this build ships with. The two things
above are the off switches, and they are the only two.

---

## What identifies you

A single random **UUID4**, generated the first time an event is captured and
stored as `analytics_install_id` in `state/panel_settings.json`.

It is **not derived from anything**: not your hardware serial, not the MAC
address, not the username, not the hostname, not the install path. Delete the
key (or the file) and this install becomes a brand-new anonymous install with
no way to correlate it to the old one. The panel shows you the exact value in
Settings.

---

## Location

The panel sends no location field of any kind — no country, no city, no region,
no timezone, no coordinates, no locale. It never has.

That on its own is not the whole promise, because a receiver can *derive* a
location from where a request came from, and PostHog does that by default. So
every event now also carries two instructions telling it not to:

| Property | Value | What it does |
|---|---|---|
| `$geoip_disable` | `true` | PostHog's GeoIP step returns immediately instead of deriving country, city, subdivision, timezone and the city's coordinates |
| `$ip` | `"0.0.0.0"` | PostHog copies the connecting address into the event's `$ip` property only when the event didn't bring its own. Bringing one keeps the real address off the stored event |

**What that does not do, said plainly:** the request still arrives over TCP from
a real address, and nothing inside a request body can change that — that is how
HTTP works, for this panel and for every other program on your Mac. What the
two flags control is what the receiver is instructed to *derive* from that
address and *store* on the event. Discarding it at the edge as well is a
setting on the receiving project, not something this source tree can promise
you, so this page does not.

If you want the connection itself gone: turn analytics off, or point
`PHOSPHENE_ANALYTICS_HOST` at a receiver you run (below).

**A note on `0.0.0.0`, since it looks arbitrary.** The obvious spelling —
sending `$ip: null` — does nothing at all: PostHog's ingest fills the property
in when the event's value is *falsy*, so a null is silently replaced by your
real address. It has to be a non-empty string. `127.0.0.1` would be the natural
choice and is the wrong one: PostHog rewrites loopback and `192.168.*` to a real
address in Sweden as a local-development convenience, which would invent a
location the day the disable flag ever went missing.

---

## Events

Five events. That's the whole list. (A sixth is described at the end because
people expect it — it is documented as the thing we deliberately don't send.)

### `app_installed`

Once per install, **ever** — fired on the first boot that an install id
reports, immediately before its first `app_boot`.

| Field | Type | Example | Notes |
|---|---|---|---|
| `version` | string | `"3.7.0"` | Which release a new install actually landed on |
| `chip_family` | string | `"M4 Max"` | Same hardware *class* as on `app_boot` |
| `ram_gb` | int | `64` | Unified memory, rounded to whole GB |

**Why it exists:** it is the denominator. Without it, "new installs over time"
has to be reverse-engineered from first-sightings of unique install ids, which
is both fiddly and worse for you — it is the shape of question that tempts
someone to start profiling ids. One counter answers it instead.

**Once-ever, and how:** a boolean `analytics_install_reported` is written to
`state/panel_settings.json` the moment the event is captured, and it is checked
before every subsequent boot. Rebooting the panel never re-counts. Deleting
your install id (or the settings file) makes a genuinely new install, which
reports its own `app_installed` once — that is the same "no way to correlate it
to the old one" property described above, not a loophole in it.

### `app_boot`

Once per panel start.

| Field | Type | Example | Notes |
|---|---|---|---|
| `version` | string | `"3.4.1"` | Contents of the repo `VERSION` file |
| `os_version` | string | `"26.4"` | macOS major.minor. Patch level deliberately dropped |
| `chip_family` | string | `"M4 Max"` | Parsed from the CPU brand string. A hardware *class*, identical across every machine of that model. `"unknown"` or `"non-apple-silicon"` when unparseable |
| `ram_gb` | int | `64` | Unified memory, rounded to whole GB |
| `cap_tier` | string | `"q8"` | `q4` or `q8` — which capability surface the UI is showing |
| `packs` | object | `{"h3":true,"sharp":false,"q8":true,"qwen":false}` | Booleans only: is each optional pack installed |
| `h3_chain_supported` | bool | `false` | Whether the installed H3 runner supports window chaining (10 s / 15 s tiers) |

### `render_completed`

Once per job that finishes successfully — every engine and every mode,
including image and training jobs.

| Field | Type | Example | Notes |
|---|---|---|---|
| `engine` | string | `"ltx"` | `ltx` or `h3` |
| `mode` | string | `"i2v"` | `t2v`, `i2v`, `extend`, `keyframe`, `a2v`, `restore`, `ingredients`, `control`, `image`, `train` |
| `tier` | string | `"standard"` | LTX quality (`quick`/`balanced`/`standard`/`high`) or H3 tier (`3s`/`5s`/`10s`/`15s`) |
| `duration_bucket` | string | `"5-15m"` | One of `<2m`, `2-5m`, `5-15m`, `15-40m`, `>40m`, `unknown`. Bucketed, not raw — a precise duration plus a resolution plus a timestamp starts to look like a fingerprint |
| `resolution` | string | `"1216x704"` | Output dimensions, or `"unknown"` |
| `frames` | int | `121` | Frame count |

### `render_failed`

Same as `render_completed`, plus one field:

| Field | Type | Example | Notes |
|---|---|---|---|
| `error_signature` | string | `"RuntimeError: helper exited before first frame"` | **The only free-text field the panel sends.** See the scrubbing rules below |

Cancelled jobs are **not** reported — a user cancelling is not a signal about
the software.

#### How `error_signature` is scrubbed

In this order:

1. **First line only.** Tracebacks and multi-line detail are discarded.
2. **Exact content redaction.** This job's prompt, negative prompt, image
   path, audio path, output path, character name and training-job id are
   removed by exact substring match → `<redacted>`. This is the defense that
   matters: the realistic leak is an exception that quotes your prompt back.
3. **Path stripping.** Anything shaped like an absolute path — `/Users/…`,
   `~/…`, `/private/var/…`, `/Volumes/…`, or any run of two or more path
   segments — becomes `<path>`.
4. **Truncation to 120 characters.**

Strings shorter than 6 characters are not redacted, so a terse prompt can't
blank out ordinary words in an error message.

### `pack_state_change`

Fired at boot when an optional pack's installed state differs from the
previous boot. At most four per boot; usually zero.

| Field | Type | Example |
|---|---|---|
| `pack` | string | `"h3"` — one of `h3`, `sharp`, `q8`, `qwen` |
| `from` | bool | `true` |
| `to` | bool | `false` |

A `true → false` transition means a pack that *was* installed is no longer
detectable — a broken install, not a user choice. This is the whole reason
this event exists: nothing else in the panel notices that today.

### `engine_selected` — not implemented, on purpose

The obvious way to count engine-picker usage is a ping per click. That adds a
network request to an interaction that currently has none, and it would be by
far the chattiest event in the system. The same question is answered for free
by the `engine` field on every render event plus the `packs.h3` field on
`app_boot`, so the picker is measured by what people actually render rather
than what they click. If clicks-without-renders ever become the question,
that's the point to add it.

---

## What is never sent

Not "we try not to send" — these are dropped by name before any payload is
built, and there's a test asserting it:

> `prompt`, `negative_prompt`, `override_prompt`, `caption`, `image`,
> `image_path`, `images`, `audio`, `audio_path`, `video`, `output`,
> `output_path`, `raw_output`, `native_output`, `path`, `paths`, `file`,
> `filename`, `files`, `dir`, `directory`, `root`, `first_frame`,
> `last_frame`, `refs`, `reference`, `seed_image`, `lora`, `loras`,
> `lora_path`, `lora_paths`, `character`, `trigger`, `trigger_words`,
> `hostname`, `username`, `user`, `email`, `home`, `command`, `cmd`, `argv`,
> `env`, `token`, `key`, `api_key`

No media file ever leaves the machine under any circumstance — there is no
code path that reads a rendered file for analytics purposes.

---

## The local log

Every captured event is appended to **`state/usage-log.jsonl`** *before* the
network is touched, and independently of whether the send then succeeds. One
JSON object per line:

```json
{"event":"render_completed","props":{"engine":"ltx","mode":"t2v","tier":"standard","duration_bucket":"2-5m","resolution":"1216x704","frames":121},"install_id":"…","ts":1754400000.0,"at":"2026-08-05 17:40:00","utc":"2026-08-05T14:40:00Z"}
```

Two reasons it exists: it's the "this machine" data source for the Usage
section of the stats dashboard, and it means you never have to take this
document's word for anything — you can read exactly what the panel sent, line
for line, after the fact.

The file is capped at **5 MB**; at the cap the oldest half is dropped. It's
gitignored (both via `state/` and by basename) and never leaves your Mac.

---

## Owner setup — activating this

Both key fields live in **Settings → Anonymous usage analytics → Maintainer /
self-hosting keys**, and are stored in `state/panel_settings.json` (mode
`0600`). One of the two keys is committed and one must never be — that
asymmetry is the whole design.

### 1. The capture key — PostHog **Project API key** (shipped)

PostHog → *Project settings* → *Project API key* (starts `phc_…`). Write-only:
it can send events and do nothing else, which is why it is safe to hold on disk
and safe to commit. **Phosphene's own project key is already in the source** as
`ANALYTICS_KEY_DEFAULT`, and it is what a stock install reports with.

Paste a different one into **PostHog project key (capture)** to point a fork at
its own project. That field *overrides* the shipped key; clearing it falls back
to the shipped key rather than switching capture off. To send nothing, use the
toggle or `PHOSPHENE_ANALYTICS_DISABLED=1`.

Env override: `PHOSPHENE_ANALYTICS_KEY`.

### 2. The read key — PostHog **Personal API key** (never committed)

PostHog → *Personal settings* → *Personal API keys* → create one with **read**
scope on the project (starts `phx_…`).

Paste it into **PostHog personal API key (fleet view)**. This unlocks the
fleet numbers in the Usage section at <http://127.0.0.1:8199/stats>. It is
never used for sending, only for querying.

Env override: `PHOSPHENE_ANALYTICS_QUERY_KEY`.

### Self-hosting

| Variable | Default | Purpose |
|---|---|---|
| `PHOSPHENE_ANALYTICS_HOST` | `https://us.i.posthog.com` | Ingestion endpoint. Must accept a PostHog-shaped single-event `POST /i/v0/e/` |
| `PHOSPHENE_ANALYTICS_API_HOST` | `https://us.posthog.com` | Query API host for the fleet view |
| `PHOSPHENE_ANALYTICS_PROJECT` | `@current` | PostHog project id used in the query URL |
| `PHOSPHENE_ANALYTICS_DISABLED` | *(unset)* | `1` disables everything, overriding the setting |

---

## The dashboard — `/stats` → Usage

The maintainer dashboard at <http://127.0.0.1:8199/stats> (127.0.0.1-only,
like every other panel endpoint) has a **Usage** section fed by
`GET /stats/usage`. Two tiers, one renderer:

- **`this mac`** — aggregated from `state/usage-log.jsonl`. Always available,
  needs no keys, works offline. Labelled *"this machine only — add a PostHog
  query key in Settings for fleet data"*.
- **`fleet`** — aggregated by PostHog across every install that has pinged.
  Requires the personal API key. Cached to `state/usage-fleet.json` for **6
  hours**; the section's *refresh* button bypasses the cache.

It shows: weekly active installs, renders this week, H3 share, error rate, the
top 5 error signatures of the last 7 days, version / chip / memory
distributions, and a **pack-regression alert** that turns red when any pack
went `true → false` in the last week.

The fleet view runs ten read-only aggregate HogQL `SELECT`s against `events`.
Each is independent — one failing leaves that panel empty rather than
collapsing the view. If every query fails, the section falls back to local
data with a visible warning.

---

## Design notes

**Why default ON.** An opt-in version of this shipped on 2026-05-21 and was
reverted the next day (`da1d6f5`). It was off by default, which meant it would
have told us nothing even if it had stayed. The trade this version makes
instead: default ON *with a key that really ships*, paid for by a far smaller
payload, a one-line disclosure in the boot log the first time it runs, a
one-click off switch that stops the local log too, and a plain-text copy on
your own disk of every event that leaves.

**A correction, recorded rather than quietly fixed.** Until 2026-08-12 this
page said the public repo shipped no key and that a fresh clone sent nothing.
That stopped being true on 2026-08-09 (`acfbdc7`), when the project key was
committed on purpose — and the page kept saying it for three days. Nothing
extra was ever collected; the page was simply wrong about the default, which
for a page whose entire job is being checkable is the worse kind of wrong.
Adding an event or a field is a documentation change first: if `ANALYTICS.md`
and the dry-run suite aren't in the same commit, the commit is incomplete.

**Why it can't break a render.** `_analytics_capture()` builds the payload,
starts a daemon thread and returns. Delivery has a 2-second timeout and a bare
`except: pass` around the entire path. Nothing is retried and nothing is
queued — a dropped event is strictly preferable to state that outlives the
render it describes. There are no background timers and no heartbeats.

**Where it's wired.** Two call sites, both in `mlx_ltx_panel.py`:
`_analytics_boot()` in `__main__` (never at import time, so `import
mlx_ltx_panel` from a script sends nothing), and `_analytics_render_event()`
in `worker_loop`'s `finally` — the single point every job from every engine
passes through, so a future engine is counted for free.

---

## Verifying it yourself

```sh
# The dry-run suite: no network, no panel, isolated temp state dir.
python3 scripts/test_analytics_dryrun.py
```

43 tests covering: the shipped key is a write-only `phc_` project key and is
really the one that goes on the wire, the toggle and the env kill-switch each
produce zero sockets *and* zero log lines, the exact field set of every event,
every event name the panel can fire is documented on this page, prompt/path
non-leakage (including a prompt quoted inside an exception), forbidden-key
dropping, the geo-disable and `$ip` flags riding on *every* event type and
being un-overridable by a call site, bucketing, log rotation and the local
aggregates.

To watch it live, tail the local log while you render:

```sh
tail -f state/usage-log.jsonl
```
