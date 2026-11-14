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
| **How often** | Once ever when the install is new, once per panel start, plus once per finished render, and Phosphene Bench results only when you press Submit ([`bench_result`](#bench_result)). No heartbeats |
| **Where it goes** | PostHog (`us.i.posthog.com`), or a self-hosted endpoint you choose |
| **Location** | None sent. Country and city are *derived* from the address your computer connects from, and kept — [details](#location) |
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
no timezone, no coordinates, no locale. It never has, and the guard suite fails
the build if one ever appears in a payload.

**That is not the whole story, and this page states the rest plainly.** A
receiver can *derive* a location from the address a request arrives from, and
PostHog does that by default. **Since 2026-09-19 — the first build after
v4.15.0 — Phosphene lets it.** Every event is therefore stored with:

| Derived by the receiver | Example |
|---|---|
| `$geoip_country_code`, `$geoip_country_name` | `DE`, `Germany` |
| `$geoip_city_name`, `$geoip_subdivision_1_name` | `Hamburg`, `Hamburg` |
| `$geoip_postal_code` | `20095` |
| `$geoip_latitude`, `$geoip_longitude` | the city's centre, not your building |
| `$geoip_time_zone` | `Europe/Berlin` |

**Why.** Phosphene is a Mac app that renders video locally, and the fleet view
exists to answer "which build is broken, and on what hardware". *Where* turns
out to belong to that question: a failure that is really a regional mirror, a
Hugging Face route, or a locale-dependent path shows up as noise without it.
The maintainer chose to keep it. This page's job is to tell you, not to talk
you into it.

**What is NOT stored: the address itself.** The receiving project has *Discard
client IP data* turned on, which runs the GeoIP step first and then drops the
address — so the country and city above are kept and the IP that produced them
is not. Being honest about the shape of that promise: it is a setting on the
receiving project, not a field inside a request body, so it is something this
source tree can *state* and cannot *prove to you*. What the source tree can
prove is the other half — that the panel itself sends nothing, and reads no
locale, timezone or coordinate off your Mac to send. That is what the tests
pin.

**A person is still not built.** `$process_person_profile: false` rides on every
event, so none of this accumulates into a person record. It is a property of an
event, next to a random install id.

> **Correction, 2026-09-18 — the history, which is not tidy.** An audit of the
> receiving project found **1,304 stored events carrying a derived location**
> across **85 installs**, from builds that shipped *before* the suppression flags
> existed (chiefly 2026-08-09 → 2026-08-15, plus a reverted 2025-05 build).
> Between **2026-08-12 and v4.15.0** the panel did suppress derivation, on every
> event, with two properties: `$geoip_disable: true`, and `$ip: "0.0.0.0"` to
> occupy the property PostHog would otherwise fill from the socket. Those flags
> are **gone as of 2026-09-19**, deliberately and with this page rewritten in the
> same commit — which is the only way a promise like this is worth anything. The
> older rows were left in place. Three postures in thirteen months is the real
> record, so it is written down instead of smoothed over.

**If you don't want any of it**, there are three off switches and they are the
same ones as for every other event on this page: the Settings toggle,
`PHOSPHENE_ANALYTICS_DISABLED=1`, or `PHOSPHENE_ANALYTICS_HOST` pointed at a
receiver you run. Turning analytics off means no request is made at all, which
is the only thing that makes a connecting address a non-question — nothing
inside a request body ever could, for this panel or for any other program on
your Mac.

---

## Super properties (5.0.3)

Phosphene runs in two places: through Pinokio, and as the Phosphene Mac app. Both
run the same Studio, so an event on its own could not say which. From 5.0.3 every
event below also carries these, unless the event already names the field itself:

| Property | Values | Why |
|---|---|---|
| `distribution` | `mac-app` or `pinokio` | A bug in the app is not a bug in Pinokio, and the other way round |
| `edition` | `free` or `pro` | Pro runs a different render path |
| `app_version`, `app_build` | e.g. `1.0.3`, `27`; Mac app only | The app and the Studio update separately |
| `studio_version` | e.g. `5.0.3` | The Studio build that sent it (same as `version`) |
| `os_version`, `chip_family`, `ram_gb` | `26.4`, `M4 Max`, `64` | The coarse machine class, so any event can be split by hardware |

All are closed words, version numbers or whole numbers. No path, no name, no
prompt. The Mac app also sends its own few events (`mac_*`, below) with the same
rules and the same off switch. The Settings toggle and
`PHOSPHENE_ANALYTICS_DISABLED=1` switch both off. When the Mac app minted this
Mac's random id before the Studio first ran, the Studio keeps that same id
(`PHOSPHENE_ANALYTICS_INSTALL_ID`), so one install is one id.

### The Mac app's own events (`mac_*`)

Sent by the app itself, not the Studio, to the same place, only while analytics is
on. Properties: the super properties above (without the Studio-only ones) plus:

| Event | When | Extra properties |
|---|---|---|
| `mac_launch` | each time the app opens | `first_launch` (true on the first ever open) |
| `mac_setup` | setup, before the Studio exists: the plan and the install; and (app 5.0.11+) the background download of the video model after the Studio opens | `step` (`plan`, `install` or `models`), `outcome` (`started`, `ok`, `failed`), on a failure `error_class` (closed words such as `disk_full`, `network`, `download_failed`, `timeout`) and `error_signature` (one line, 120 characters, paths / URLs / e-mails / keys / ids / your user name removed), `seconds_bucket` |
| `mac_engine` | the app starting the Studio | `step` is `studio`, `outcome` (`ok`, `failed`, `exited` when it quit by itself), `error_class`, `error_signature`, `exit_status`, `seconds_bucket` |
| `mac_update` | Sparkle, the app's updater | `stage` `check` with `outcome` `found` or `failed` (with `error_domain` and `error_code`, numbers only, never the message; at most once per cause per six hours), or `stage` `installed` with `outcome` `ok`, `from_version`, `to_version` (noticed the next time the new app opens) |
| `mac_crash` | the app did not quit cleanly last time | `outcome` is `crashed` |

A copy of every `mac_*` event is written, as sent, to `mac-usage-log.jsonl` in the app's
`state` folder (the key is not in it). Success steps are reported once per launch; nothing
repeats on a timer.

---

## Events

Seven events. That's the whole list. (An eighth is described at the end
because people expect it — it is documented as the thing we deliberately don't
send.)

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
| `cap_tier` | string | `"q8"` | `q4` or `q8` — which capability surface the UI is showing. A statement about this Mac's RAM folded down to what the generation can serve, NOT about which weights are on disk |
| `model_version` | string | `"ltx25"` | Which LTX generation this install serves (`ltx23` / `ltx25`). Added in v4.0 as a NEW field rather than by redefining `cap_tier`, so the existing capability series stays comparable across the 2.5 cutover |
| `packs` | object | `{"h3":true,"sharp":false,"q8":true,"qwen":false}` | Booleans only: is each optional pack installed |
| `h3_chain_supported` | bool | `false` | Whether the installed H3 runner supports window chaining (10 s / 15 s tiers) |

### `render_completed`

Once per job that finishes successfully — every engine and every mode,
including image and training jobs.

| Field | Type | Example | Notes |
|---|---|---|---|
| `engine` | string | `"ltx"` | `ltx`, `h3`, `music`, `speech`, an image engine (`flux3`, `mflux/<family>`), or `image` for an image job that failed before its engine was chosen (before 4.19.16 failed image jobs said `ltx`) |
| `mode` | string | `"i2v"` | `t2v`, `i2v`, `extend`, `keyframe`, `a2v`, `restore`, `ingredients`, `control`, `upscale`, `image`, `train`, `music`, `speech` (a Speech job sends only the counts every render sends: never the script, the voice, a character name or the clip) |
| `tier` | string | `"standard"` | LTX quality (`quick`/`balanced`/`standard`/`high`) or the H3 composite cell (`standard_5s`, `high_10s`, …) |
| `duration_bucket` | string | `"5-15m"` | Legacy 5-value bucket, kept one more release for dashboard continuity, then removed in favour of `wall_sec_bucket` |
| `wall_sec_bucket` | int | `480` | The lower edge of a log-spaced ladder (`15, 30, 45, 60, 90, 120, 180, 240, 300, 420, 600, 900, 1200, 1800, 2400, 3600, 5400`). Sharp enough to see a performance regression, far too coarse to be a timing fingerprint. Absent when the wall clock is unknown |
| `resolution` | string | `"1216x704"` | Output dimensions, or `"unknown"` |
| `canvas_class` | string | `"720p"` | Coarse megapixel class: `<=480p`, `576p`, `720p`, `1080p`, `native+` |
| `frames` | int | `121` | Frame count |
| `version` | string | `"4.0.6"` | Same value `app_boot` sends. Repeated here so "failure rate by version" is a one-click breakdown instead of a join |
| `chip_family` | string | `"M4 Max"` | Hardware **class**, identical across every machine of that model — same value `app_boot` sends |
| `ram_gb` | int | `64` | Hardware class — same value `app_boot` sends |
| `os_version` | string | `"macOS 15"` | Coarse — same value `app_boot` sends |
| `steps` | int | `8` | Sampler depth the job ran — separates a real regression from a user on 30 steps |
| `accel` | string | `"off"` | `off` / `boost` / `turbo` |
| `temporal_mode` | string | `"native"` | `native` or `fps12_interp24` (Long Clip Boost) |
| `upscale` | string | `"fit_720p"` | `off` / `fit_720p` / `fit_1080p` / `x2` |
| `upscale_method` | string | `"lanczos"` | `lanczos` or `pipersr` |
| `schedule_preset` | string | `"default"` | `default` or `fast` — the LTX-2.5 draft schedule's adoption |
| `chain_windows` | int | `2` | H3 chained-window count (1 = single pass) |
| `chain_prompts_used` | bool | `false` | Did the per-window shot list carry any text |
| `lora_count` | int | `1` | **Count only. Which adapters is deliberately never sent** |
| `lora_kinds` | string | `"style"` | Closed vocabulary: `none` / `style` / `character` / `mixed` |
| `character_used` | bool | `true` | A cast character drove this render |
| `audio_mode` | string | `"joint"` | `joint` / `none` (external audio replaces the generated track) / `a2v_dub` / `h3_native` |
| `h3_sampler` | string | `"v13"` | 4.19.11 — H3 renders only: which sampler ran. **Closed vocabulary**: `best` (the full sampler), `fast`, `turbo`, `v13` (lightx2v v1.3 on Best at High), `flex` (Pika H3 Flex, its six-step re-noise sampler), `flex_turbo` (5.0: H3 Flex Turbo, Pro, four euler steps). Keeps the timing lanes apart, which `steps` alone cannot |
| `first_render` | bool | `true` | Present once per install, on its first successful render ever — the activation funnel without a join |
| `gpu_steps` | string | `"short"` | 4.17.5 — present only when the render ran in **short GPU steps** (one Metal op per command buffer), the path a macOS GPU-watchdog kill switches a setting to. Closed vocabulary: `short`. Absent = default steps. Answers "does the short path stop the watchdog loop" |
| `watchdog_retry` | bool | `true` | 4.17.5 — present only when a GPU-watchdog kill was re-run inside the same job (once, on short steps). On `render_completed` it means the retry saved the render |
| `watchdog_phase` | string | `"load"` | 4.17.5 — present only when macOS's GPU watchdog stopped this job (on `render_completed`, the run that was then re-run): which phase the GPU was in, from the helper's own step lines. **Closed vocabulary**: `encode` (Gemma prompt encoding), `load` (the transformer loading), `denoise`, `decode` |
| `gpu_abort_retry` | bool | `true` | 4.19.6 — present only when a GPU abort that was not the watchdog (crash_line `gpu_internal` / `gpu_victim` / `gpu_other`) stopped prompt encoding and the job was re-run once in a fresh engine process after a pause. 5.0.23 — also set, together with `watchdog_retry`, when a GPU-watchdog kill in prompt encoding had no mitigation left to try (short steps and the shorter encode were already on) and got that same paused re-run. On `render_completed` it means the re-run saved the render |
| `helper_warm` | bool | `false` | 4.19.6 — LTX renders that ran in the warm engine process: `true` when the (last) run started in a process that had already finished a render, `false` when it started fresh. Absent for engines that do not use it. Tells whether prompt-encoding GPU aborts follow a warm process |
| `mem_pressure` | int | `1` | 4.19.16 — LTX renders: macOS's memory pressure level when the render started (`1` normal, `2` warning, `4` critical). Absent when unread. Tells memory pressure apart from the render itself in the 16 GB Metal-watchdog kills |
| `mem_avail_gb` | int | `8` | 4.19.16 — LTX renders: the memory the kernel reported available when the render started, as the lower edge of `0, 2, 4, 6, 8, 12, 16, 24, 32, 64` GB. Absent when unread |
| `source` | string | `"storyboard"` | v4.9.7 — which surface queued the job. **Closed vocabulary**: `form`, `batch`, `storyboard`, `characters`, `image_studio`, `retry`, `api`, `chain` (an Upscale ×2 queued automatically behind an H3 draft), `bench` (a Phosphene Bench step, see [`bench_result`](#bench_result)), `unknown`. Makes "how much of the rendering is Storyboard" a one-click breakdown |

### `render_failed`

Same as `render_completed` (minus `first_render`), plus:

| Field | Type | Example | Notes |
|---|---|---|---|
| `error_class` | string | `"metal_watchdog"` | **A closed 19-value taxonomy** (`refused`, `oom_jetsam`, `metal_watchdog`, `metal_oom`, `native_crash`, `helper_start_timeout`, `helper_exit`, `model_missing`, `model_corrupt`, `download_failed`, `venv_broken`, `bad_params`, `input_missing`, `file_missing`, `disk_full`, `export_failed`, `timeout`, `cancelled_race`, `other`). Classification runs on the original error text locally; **only the class leaves the machine.** `refused` is in the taxonomy but never rides on a `render_failed` event — it is the value that routes the event to `render_refused` instead, below |
| `error_fingerprint` | string | `"a3f09c21e7b4"` | Only when `error_class` is `other`: 12 hex chars of the SHA-256 of the already-scrubbed first line. Lets "the same unknown error, 17 times, all on M1 Max" be counted without transmitting the text — the readable line stays in your own `state/usage-log.jsonl` |
| `error_signature` | string | `"RuntimeError: helper exited before first frame"` | **The only free-text field the panel sends**, see the scrubbing rules below. Kept for ONE transition release alongside `error_class`, then removed |
| `crash_line` | string | `"gpu_timeout"` | 4.19.2 — only when the LTX render helper died from a signal (SIGABRT, SIGSEGV, …): the **class** of the last native error line it printed, never the line. **Closed vocabulary**: `gpu_timeout`, `gpu_interactivity` (the two GPU-watchdog names), `gpu_hang`, `gpu_page_fault`, `gpu_oom`, `gpu_victim`, `gpu_internal`, `gpu_submissions_ignored`, `gpu_invalid_resource`, `gpu_not_permitted`, `gpu_device_removed` (4.19.4 - the remaining IOGPU/Metal command-buffer error names, split out of `gpu_other`), `gpu_other` (any other Metal command-buffer error), `resource_limit`, `cxx_exception`, `assertion`, `none` (no such line reached the panel). Why: until 4.19.2 the generic SIGABRT sentence quoted the watchdog line as an example, so every unexplained abort was counted as `metal_watchdog` (24 of 4.19.1's 37); those now count as `native_crash`, and this says what they were |
| `crash_phase` | string | `"start"` | 4.19.2 — same condition as `crash_line`: how far the helper got, from its own step lines. **Closed vocabulary**: `encode`, `load`, `denoise`, `decode` (as `watchdog_phase`), `start` (before its first step line) |
| `train_error` | string | `"oom"` | 4.19.1 — only on a failed **training** job (`mode: train`): why it stopped. **Closed vocabulary**: `oom` (MLX/Metal could not allocate, or macOS killed the trainer with SIGKILL), `missing_file` (incl. the LTX-2.3 training weights not downloaded: the trainer now checks them first and stops within seconds), `caption_check` (the captions carry another trigger), `disk_full`, `import_error` (the venv is missing a package), `nan_loss` (the loss stopped being a number for 10 steps in a row; the trainer now stops there), `other`. The trainer classifies its own exception by TYPE first and then by MLX's fixed allocation-failure fragments (`lora_lab/failure.py`), sends the word in its `error` event, and the panel forwards it only if it is on this list. Never a path, caption, trigger or message: those stay in the Logs tab and `<dataset>/train.log`. Before 4.19.1 every one of these reached the fleet as `helper_exit` + "training exited with code 1"; that sentence and its `error_class` stayed until 4.19.17, when the card started showing the trainer's own sentence (so `error_class` for a training failure now follows that sentence, and `error_signature` is the fixed line described under `train_stage`). Read training failures by `train_error` + `train_stage`, not `error_class` |
| `train_stage` | string | `"preprocess"` | 4.19.17 — only on a failed **training** job, next to `train_error`: where the trainer stopped. **Closed vocabulary** (`lora_lab/failure.py` `TRAIN_STAGES`): `models`, `caption`, `config`, `ingest`, `crop`, `preprocess`, `train`, `publish`, `sidecar`, `validate`, `verify`, `unknown` (the trainer died before it said; then `train_error` is read from the last traceback line's exception TYPE). From 4.19.17 a training failure's `error_signature` is the fixed line `training stopped: <train_error> (stage <train_stage>)` built from these two words, because the card now shows the trainer's own sentence (which can quote a trigger or file name) and that text never leaves the Mac. A run that died with no word and no traceback still sends "training exited with code N" |

Cancelled jobs are **not** reported — a user cancelling is not a signal about
the software.

Neither are **refusals** — those get their own event, next.

> **⚠️ A SERIES CHANGES SHAPE FROM v4.6.1 — `model_missing` gains, `other`
> loses.** Four render faults that mean *"your Hailuo H3 clone is older than
> this panel"* (its runner has no `--lora`, no `--first-frame`, or no
> `--chain-windows`) used to classify as `other`, because each raise site wrote
> its own sentence and no needle matched any of them. They are not unknowns:
> they are one well-understood fault with one remedy — re-run **Install Hailuo
> H3**, which keeps every weight already on disk. On the owner's ruling
> (2026-08-23) they now classify as **`model_missing`**, matched on the shared
> phrase `H3_RUNNER_BEHIND` (`the installed Hailuo H3 runner is behind this
> panel`) so a fifth flag added later is classified without another edit.
>
> Consequences for anyone reading the fleet: a step up in `model_missing` and a
> step down in `other` at that release is **this change, not a regression**, and
> the affected `error_fingerprint` values stop being emitted entirely (a
> fingerprint rides only on `other`). Do not compare `model_missing` across the
> boundary without saying which side you are on.

> **⚠️ A SECOND SERIES CHANGES SHAPE FROM v4.6.1 — `input_missing` gains, and
> it was previously EMPTY.** Its needles were `does not exist` / `no longer
> exists`, and **no raise site in this codebase has ever said either** — every
> one of them says *not found*. So the class matched nothing at all, and the
> widest-spread real failure in the fleet (`image not found: <path>` — 35 events
> across 22 people in 14 days) sat in `other`. It now matches, on the
> colon-anchored `not found:` plus `no longer on disk`.
>
> The same change fixed a **misclassification driven by the user's own
> filesystem**: classification runs on the raw error text *including the path*,
> and `download_failed` matches the bare word `download`, so a missing reference
> image living in `~/Downloads` was counted as a failed fetch. `input_missing`
> is now asked before the loose half of `download_failed` (a single needle,
> `repo not found`, is hoisted above it so a hub lookup stays a fetch fault).
>
> So at that release expect: `input_missing` to go from ~zero to a real number,
> `other` to drop again, and a small, permanent drop in `download_failed`. All
> three are this change.

### `feature_used` (v4.9.7)

The surfaces render events cannot see. One event per action, no free text.

| prop | type | example | why |
|---|---|---|---|
| `feature` | string | `"storyboard_plan"` | **Closed vocabulary**: `storyboard_plan`, `storyboard_export`, `editor_open`, `editor_export`, `civitai_download`, `sample_character`, `train_start`, `enhance_prompt`, `train_refused`, `enhance_prompt` is sent by the server when an Enhance succeeds, with the mode (`t2v`, `i2v`, `a2v`) as `detail` (from 2026-10-19; before that nothing sent it, so earlier zeros mean unmeasured, not unused). `share` (the clip toolbar's Share: a caption with the Phosphene link copied and the file selected in Finder; only that the button was used is sent, never which clip). Unknown names are dropped locally, never sent. `train_start` fires only past `/train/start`'s RAM refusal (SYS-41) — a refused click counts as `train_refused` with `detail: "ram"` instead, or (4.19.17+) `detail: "models"` when the LTX-2.3 training weights are not downloaded |
| `detail` | string | `"nle"` | Optional, lowercase `[a-z0-9_.-]` only, ≤32 chars — a sub-choice within the feature |
| `version` | string | `"4.9.7"` | As on `app_boot` |

### `app_updated` (v4.9.7)

Sent once at the first boot after the running version changed. Answers "did people move to the new release" without a per-install join across `app_boot` rows.

| prop | type | example | why |
|---|---|---|---|
| `from_version` | string | `"4.9.5"` | The version the previous boot ran (kept locally in settings) |
| `to_version` | string | `"4.9.7"` | The version now running |

### `update_prompt` (v4.9.7)

How the update pop-up and banner are answered — the only way to know whether the "please update" surfaces work.

| prop | type | example | why |
|---|---|---|---|
| `action` | string | `"later"` | **Closed vocabulary**: `shown`, `update_now`, `later` (the pop-up), `banner_update`, `banner_later` (the banner), `restart_needed` (the "Restart to finish update" pill, once per page load) |
| `version` | string | `"4.9.5"` | The version that was prompted |

### `install_step` (v4.16.0)

The dark half of the funnel. In the 14 days to 2026-09-19, **577 installs
reported in and only 44.5% of them ever rendered** — 278 booted once and were
never seen again. Between `app_installed` and the first `render_*` the panel
said nothing, so *"they tried and it was broken"* and *"they looked and left"*
were the same shape in the data. These events tell those apart.

**`first_use` (growth pass, 2026-10-19).** Pinokio auto-runs Start the moment
the install finishes, so `first_boot` can fire with nobody at the screen. In
the 2026-09-20 → 10-11 cohorts, 20% of new installs booted once and never
rendered, and three in four of those sent nothing but the boot events. The
page now reports one `first_use` the first time anyone clicks or presses a key
on it (a hover or scroll does not count), which separates "nobody ever looked"
from "looked and left". Nothing about what was clicked is sent.

**At most one event per step per install, ever** — with one deliberate
exception: `engine_env` reports again when its answer *changes* (outcome, or
from 4.17.5 the `cause` of a failure), because a broken environment that gets
repaired is the most useful transition on this event. This is not a heartbeat and the panel still has none.

| prop | type | example | why |
|---|---|---|---|
| `step` | string | `"engine_env"` | **Closed vocabulary**: `first_boot` (the panel came up), `engine_env` (its Python environment can run a render), `weights_check` (the base model is on disk), `first_queue` (a job reached the worker — someone pressed Render), `first_use` (someone clicked or typed on the panel page for the first time — reported by the page, the only step it may report; always `ok`) |
| `outcome` | string | `"failed"` | **Closed vocabulary**: `started`, `ok`, `failed`, `skipped` |
| `error_class` | string | `"venv_broken"` | Only on `failed`, and only from the same closed taxonomy `render_failed` uses |
| `cause` | string | `"network"` | 4.17.5 — only on a failed `engine_env`: WHY, one word. **Closed vocabulary**: `network` (a package server could not be reached), `timeout` (downloads timed out), `disk` (disk full), `uv_error` (the package tool could not resolve or build), `python_missing` (the venv's Python does not run), `installing` (Pinokio's Install was still writing the venv when the panel looked - reported again when it finishes), `mismatch` (4.19.4 - the packages are all there but transformers refuses the version of one of its own dependencies, e.g. tokenizers 0.23.3 under transformers <5.13; read from the venv's files by the panel, Repair engine fixes it), `other`, `unknown` (no installer record: an install that stopped before the engine step, or one older than 4.17.5). Read from the installer's own record (`ltx-2-mlx/env/.phosphene_engine_result.json`, written by `scripts/pinokio/ltx_engine_env.sh` in these words via `install_cause.sh`) - never from its output text |
| `version` | string | `"4.16.0"` | The build that reported it |
| `ram_gb` | int | `16` | SYS-16: lets `first_queue` (this event) be sliced the same way `render_completed`'s `first_render` already is — the reviewer's finding was that activation is LOWEST on the machines that struggle most (36 GB 53%, 8 GB 57%, 16 GB 63%), and answering "first_queue vs first_render, per tier" needed this on both events, not a join against `app_boot` by install_id |

**What this event deliberately does not carry**, and why: no path, no file
name, no URL, no host, no byte count, and — unlike `render_failed` — **no
`error_signature` and no `error_fingerprint`**. An install-time error line is
the likeliest place in the whole product for a username-bearing path to
survive scrubbing, and the class on its own answers the question the event
exists to ask.

### `update_outcome` (v4.16.0)

Whether pressing **Update** actually produced a new version. 139 people
pressed it in 14 days; 116 ran something new afterwards and **23 did not**, and
nothing in the data could say whether the pull failed or the app never
restarted. `app_updated` cannot answer it by construction — a failed update
emits nothing at all.

The panel records the version you pressed Update *on* (one string, in local
settings) and reads it on the next boot: a different version means it landed,
the same version means it didn't. The marker is cleared when it is read.
4.17.5: the marker also carries the build's SHA (`4.17.4@<sha>`), so a new
build under the same VERSION counts as landed; and an in-panel Update whose
pull moved nothing ("Already up to date") clears the marker instead of asking
for a restart - the two false `restart_pending` failures on 4.17.4 were people
pressing Update on the build they already ran.

| prop | type | example | why |
|---|---|---|---|
| `stage` | string | `"restart_pending"` | **Closed vocabulary**: `running_new` (a new version is running), `restart_pending` (same version came back) |
| `outcome` | string | `"failed"` | **Closed vocabulary**: `ok`, `failed` |
| `from_version` | string | `"4.15.0"` | The version Update was pressed on |
| `version` | string | `"4.15.0"` | The version now running |

### `separator_install` (v4.17.4)

Whether the Lip-sync voice separator (demucs, "Listen to the voice only")
actually installed, by which path, and if not, why. On 4.17.3 a 16 GB Mac
updated with the panel's own Update (a pull plus a restart, which never runs
the post-update step that installs it): its queued voice-only jobs were
refused at once and nothing in the data said the separator was missing. The
installer now records its outcome next to the weights; the panel reports
that record once at the next boot, and reports its own installs (at start-up,
for a waiting job, or from the form's Install button) when they finish.

| prop | type | example | why |
|---|---|---|---|
| `outcome` | string | `"failed"` | **Closed vocabulary**: `ok`, `failed` |
| `via` | string | `"update"` | **Closed vocabulary**: `install` (install.js), `update` (the Pinokio Update), `panel_boot` (the panel found it missing at start-up), `panel_job` (a voice-only job needed it), `panel_form` (the Lip-sync form's Install / Try again) |
| `error_class` | string | `"pip_failed"` | Only on `failed`. **Closed vocabulary**: `no_venv`, `pip_failed`, `import_failed`, `weights_failed` (the package works, the 80 MB model did not download - it downloads on first use), `timeout`, `spawn_failed`, `other` |
| `cause` | string | `"network"` | 4.17.5 — only on `failed`, when the installer recorded why: the same closed words as `install_step.cause` (`network`, `timeout`, `disk`, `uv_error`, `python_missing`, `other`). `a2v_stems_deps.sh` now retries a network/timeout package failure 3 times and the model download 4 times (resuming) before it reports one |
| `ready` | bool | `true` | Whether a voice-only render can run now |
| `weights` | bool | `false` | Whether the separator's model is on disk |
| `version` | string | `"4.17.4"` | The build that reported it |
| `ram_gb` | int | `16` | Same machine class as `install_step` |

No installer output, no path, no host, no byte count: the class is read from
a record the installer writes in closed words, never parsed out of pip's text.

### `broadcast_seen` (v4.9.7)

One event when a developer broadcast (`BROADCAST.json`) is acknowledged. No text — which message is derivable from the date.

### `queue_paused_breaker` (v4.9.7)

The queue paused itself after three identical failures in a row - or, from
5.0.14 (4.19.19), after ONE when the render helper could not start because a package in
the engine will not import (`helper failed to start: ... ImportError`): a
broken install no retry renders through. Those fire with `n_failed: 1` and
`error_class: "helper_start_timeout"`.

| prop | type | example | why |
|---|---|---|---|
| `n_failed` | int | `3` | The streak length when it fired |
| `queued` | int | `12` | Jobs left waiting |
| `error_class` | string | `"model_missing"` | The same closed taxonomy as `render_failed` |

The browser reports `update_prompt`, `broadcast_seen`, the `feature_used` values `editor_open`, `editor_export`, `enhance_prompt` and `share`, and the single `install_step` value `first_use` through **one** route, `POST /analytics/ui`, which allowlists every (event, value) pair and answers 400 to anything else. Everything else is emitted server-side.

### `render_refused`

Once per job the panel **declined on purpose**. Same fields as
`render_failed`, except that the three error fields are replaced by one:

| Field | Type | Example | Notes |
|---|---|---|---|
| `refusal` | string | `"ingredients_generation"` | **A closed vocabulary** — `ingredients_generation`, `hardware_tier`, `h3_ram` (5.0.20+ also the H3 runner's own "This Mac cannot make this clip:" admission sentence, exit 76), `h3_mode`, `h3_lora_slots`, `stale_engine` (v4.9+: a 2.5 render refused because the vendored engine predates the Gemma 4 tower — remedy is a second Update click). `image_ram` (v4.9.3+: the Image Studio memory guard — the engine holds more weights than this Mac can hold, or too little is free right now). `pack_missing` (v4.9.3+: High / Keyframes / Extend chosen on a Mac that has not downloaded the Q8 add-on — remedy is Settings → Models). `vocal_separator` (4.17.x+: Lip-sync with "Listen to the voice only" on an install whose vocal separation is missing — remedy is the form's own Install button). `licence_required` (4.19.8+: a model whose licence asks for an "I accept" - Hailuo H3, Ideogram 4, YuE2, FLUX.1 Krea [dev], Bria FIBO - before it was accepted on this Mac; remedy is the licence screen, once). `speech_ram`, `speech_installing`, `speech_consent` (Audio → Speech: a Mac under the 16 GB floor, a line queued while Speech installs, a cloned voice without the voice-owner confirmation). `pack_incomplete` (4.19.16+: an LTX render on a pack that is not completely on disk and is not downloading, named file by file; remedy is Repair. Before 4.19.16 these were `render_failed`; a pack that is still downloading now waits instead). `memory_low` (4.19.16+: a Mac with 24 GB or less whose prompt encoding macOS stopped in the last 30 minutes, still short of memory after a 90 s wait; remedy is quitting other apps. 5.0.20+: also Hailuo H3 on the low-memory plan when the runner says other apps hold the memory it needs, still so after a 5-minute wait, and the runner's own exit-77 sentence; before that these were `render_failed`, "This Mac can make this clip, but other apps are using the memory right now"). `train_models` (4.19.17+: a training run whose LTX-2.3 weights - the base pack or the full-precision dev transformer - are not on this Mac; refused at Start, at Retry and before the trainer spawns, with the Train tab's Download all. Before 4.19.17 these were `render_failed` with `train_error: missing_file`, one Mac 22 times in an hour). `lora_mismatch` (4.19.17+: an Image render with a FLUX.2 LoRA made for another FLUX.2 size - a [klein] 9B or [dev] adapter on the [klein] 4B that Auto and Klein-Edit run - stopped before the engine starts; remedy is un-picking it. Before 4.19.17 these were `render_failed` with mflux's `[matmul] Last dimension of first input`). `image_refs` (4.19.17+: an Image render with more reference images than its engine takes - 3, FLUX 3 10 - reaching the worker; /queue/add refuses it first. Before 4.19.17 these were `render_failed`, "This engine supports at most 3 reference images."). `image_disk` (4.19.17+: an Image engine whose first download does not fit on the disk that holds the image models - the model's real download size, less what an interrupted download left, plus 2 GB; remedy is freeing space. Before 4.19.17 these were `render_failed`, "pre-flight: only N GB free on the HF cache volume", against a flat 10 GB). `input_unreadable` (5.0.14+ / 4.19.19+: a Control or Colorize source clip that is on disk but cannot be read as a video - incomplete, damaged, no video track - refused before any model loads and at /queue/add, with the reason. Before that these were `render_failed` with a bare `ffprobe failed:` from the engine). Which guard said no. No free text, no fingerprint: the message is our own copy, so there is nothing to scrub and nothing unknown to count |

#### The rule — a refusal is not a failure

This is the distinction the two event names exist to keep apart, and it is
the one thing on this page most likely to get quietly re-merged by someone
adding a chart:

> **`render_failed`** — the engine tried and something went wrong. A bug, a
> crash, a missing file, an out-of-memory. Every one of these is a defect
> report.
>
> **`render_refused`** — the panel understood the request perfectly and
> declined it, because *this install* cannot serve that capability: the wrong
> hardware tier, the wrong engine, or the wrong model generation. Nothing
> broke. No GPU time was spent. The message names the way out.

**Therefore: `render_refused` is not in the failure rate, is not in the render
count, and is not in the engine mix — neither numerator nor denominator.** A
refusal did not render, so counting it as a render (successful *or* failed)
makes both numbers lie.

The capture boundary checks known refusal signatures as well as the worker's
typed refusal. This applies to both asynchronous and synchronous capture,
before the local log or network delivery, and removes fault fields from a
refusal event. Ingredients on an incompatible generation is refused before
generation setup. Existing events on the server are not rewritten by this fix.

An image subprocess exiting with SIGABRT is a `native_crash` unless its log
explicitly reports an allocation failure, in which case it is `metal_oom`.
A download progress line alone does not establish either a network fault or
an out-of-memory fault.

**Why a separate event rather than an `error_class` value.** Both were built
and the event won. A class value keeps one event and one funnel, but it makes
every query that touches failures carry an `error_class != 'refused'`
exclusion — in this file, in `_USAGE_FLEET_QUERIES`, and in every PostHog
insight anyone writes afterwards. One forgotten exclusion silently restores
the exact bug this replaced. A separate event name is right by default: a
query that asks about `render_failed` gets failures, always, without anyone
having to remember anything.

**What it is actually for.** A refusal is a *product* signal, not an
engineering one. The right reading of a tall `refusal` bar is "the UI offered
a control that can never work on that install" — the fix is upstream, in the
form, not in the engine. That is precisely how this event came to exist: on
2026-08-23 the largest single `render_failed` signature in the whole fleet
turned out to be the Ingredients-on-2.5 refusal — 65 events from 16 different
people, all of them clicking a button that could not possibly succeed. The
guard was right; offering the button was not. Both were fixed in the same
change.

**2026-08-28 — the `h3_ram` slug's WORDING moved; the series did not.** That
refusal used to say "Hailuo H3 needs about 64 GB of unified memory", a number
neither of the product's two floors has ever been (60 on the bf16 lane, 46 on
the Q8 DiT lane). It now says one of three things, one per RAM band — see
`h3_ram_verdict()`. The slug, the event and the series are unchanged, and
`_ANALYTICS_REFUSAL_REASONS` gained needles for the two new sentences **while
keeping the retired one**, so a replayed pre-2026-08-28 usage log still
classifies as `refused` instead of falling back into `other`. Nothing raises
the old sentence any more. Counts across the cutover stay comparable; a rise
in `h3_ram` after it is a real change in who is being refused, not a rewording
artefact.

**A refusal still shows as an error in your own panel.** The job lands in
history with a red card and the refusal text, because nothing was produced
and that is honest. The queue does not gain a fourth status. The split is in
what gets *counted*, which is what was wrong.

#### How `error_signature` is scrubbed

In this order:

1. **First line only.** Tracebacks and multi-line detail are discarded.
2. **Exact content redaction.** This job's prompt, negative prompt, music lyrics, music style, image
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

### `star_prompt`

Fired when someone answers the one-time GitHub star ask — at most once per
install, and only on a deliberate click. Nothing fires when the ask is
ignored or dismissed.

| Field | Type | Example |
|---|---|---|
| `via` | string | `"link"` — one of `link` (opened the repo) or `already` (said they had already starred) from the update banner and the header star; or, from the one-time card after the third finished render (2 Nov 2026; the fifth until 12 Nov 2026), `card_shown`, `card_star` (opened GitHub), `card_star_inapp` (starred in place with one click, through the Mac's own signed-in GitHub CLI; 4 Nov 2026), `card_waitlist` (the quiet Pro waitlist link, 5.0 line only) `card_not_now` (the close button or Escape) or `card_review` (opened Phosphene Remote's write-a-review page in the App Store from the review line; 12 Nov 2026). A card retired unseen because GitHub says that account already starred sends nothing |
| `variant` | string | `"github"` — card values only (12 Nov 2026): `github` (the star card) or `remote` (the one review line shown instead on a Mac that has paired Phosphene Remote, known only from whether `state/remote/devices.json` lists a confirmed phone). Fixed when the card is shown. No device name, id or count |

The banner's two values are coerced in their handler: anything else becomes
`link`. The card's values come from `POST /star-prompt`, which refuses
anything else with 400 and sends each at most once per install: `card_shown`
when the card goes up, then one answer. The card is the one exception to
"nothing fires when the ask is ignored": `card_shown` is what makes its
answer rate measurable. No account, no username, no repo state — the panel cannot see whether
a star was actually given, and does not ask GitHub. `via` exists only to keep
"a click we caused" separate from "a click that told us we did not need to
ask", which is the whole question the ask was added to answer.

### `bench_result`

**Only on a click.** Sent when someone presses *Submit to the leaderboard* in
Phosphene Bench (Settings → Phosphene Bench) and confirms. Never sent by a run
on its own, never sent while analytics is off (the button says so instead), and
never sent for an incomplete run. One event per run: a submit sends **every**
complete run of the current suite stored on the Mac that has not been sent
before (`state/bench/`, `submitted.json` records which), so the leaderboard can
take each install's median rather than its best run. The panel delivers these on the click itself and marks a run as sent only when the receiver accepted it, so an offline click leaves the run for the next one.

Unlike `render_completed`, times here are exact seconds, not buckets: the person
asked for them to be published. Everything is still a hardware class or a
measurement; no prompt (the suite's prompts are fixed and are not sent anyway),
no seed, no path, no file name, no clip.

| Field | Type | Example |
|---|---|---|
| `schema` | string | `"phosphene-bench/1"` |
| `suite_id`, `suite_fp` | string | `"PB1"`, `"170df0bdfe5a"` — the frozen suite and the hash of its settings |
| `run_id` | string | `"9185f62deeec4ab4"` — random per run, so a re-sent run is counted once |
| `run_index`, `runs_total` | int | `2`, `3` — this run's place in the submit batch |
| `version`, `edition`, `distribution` | string | `"4.19.12"`, `"free"`, `"pinokio"` |
| `chip_family` | string | `"M4 Max"` |
| `gpu_cores`, `cpu_p`, `cpu_e` | int | `40`, `12`, `4` |
| `ram_gb` | int | `64` |
| `model_id` | string | `"Mac16,9"` — the model identifier, a hardware class shared by every Mac of that model |
| `os_version` | string | `"26.4"` (major.minor) |
| `power` | string | `"ac"`, `"battery"` or `"unknown"` |
| `low_power` | bool | `false` |
| `thermal_worst` | string | `"nominal"`, `"fair"`, `"serious"`, `"critical"` or `"unknown"` |
| `warmup_sec` | float | `42.0` — the untimed engine warm-up |
| `complete`, `eligible` | bool | `true`, `true` |
| `st_<step>` | string | `"done"`, `"failed"`, `"skipped"` or `"cancelled"` |
| `s_<step>` | float or null | `139.4` — wall seconds, the same job timer as `render_completed` |
| `mem_<step>`, `add_<step>` | float or null | `36.0`, `18.0` — GB of the Mac's memory in use at the step's peak, and how much the step added |
| `cv_<step>` | string | `"1024×576"` — the canvas that ran |

`<step>` is one of `ltx_quick_5s`, `ltx_balanced_5s`, `image_klein`,
`h3_draft_5s`. The full local record (the JSON file the Bench saves) has a few
more fields — macOS build, swap, the engine's own memory peak — that are **not**
sent. The public page (phosphene.video/bench) shows per chip and memory size
the median of each install's median, and how many installs; never an install
id. The renders a Bench run makes are ordinary renders and send their own
`render_completed` with `source: "bench"`.

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
{"event":"render_completed","props":{"engine":"ltx","mode":"t2v","tier":"standard","duration_bucket":"2-5m","wall_sec_bucket":180,"resolution":"1216x704","canvas_class":"720p","frames":121,"version":"4.0.6","chip_family":"M4 Max","ram_gb":64,"os_version":"macOS 15","steps":8,"accel":"off","temporal_mode":"native","upscale":"fit_720p","upscale_method":"lanczos","schedule_preset":"default","chain_windows":1,"chain_prompts_used":false,"lora_count":0,"lora_kinds":"none","character_used":false,"audio_mode":"joint"},"install_id":"…","ts":1754400000.0,"at":"2026-08-05 17:40:00","utc":"2026-08-05T14:40:00Z"}
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
| `PHOSPHENE_TEST_RIG` | *(unset)* | `1` sends events as normal but tags every one with `test_rig: true` (SYS-41). For the owner's own dev machine, a release-gate run, or a clean-room test install — anything that sends REAL telemetry on purpose (unlike an agent review worktree, which sets `PHOSPHENE_ANALYTICS_DISABLED=1` and sends nothing) but is not a real user. A fleet query excludes these with `WHERE NOT properties.test_rig`, instead of a hand-derived "which rows are probably the owner" heuristic. |

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

It shows: weekly active installs, renders this week, H3 share, error rate,
refusals this week, the top 5 error signatures of the last 7 days, the top
refusals of the last 7 days (with how many *distinct people* hit each — 65
refusals from 16 people is a product bug, 65 from one person is a bookmark),
version / chip / memory distributions, and a **pack-regression alert** that
turns red when any pack went `true → false` in the last week. Refusals sit in
their own tile and their own column, never inside the error rate.

The fleet view runs eleven read-only aggregate HogQL `SELECT`s against `events`.
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

56 tests covering: the shipped key is a write-only `phc_` project key and is
really the one that goes on the wire, the toggle and the env kill-switch each
produce zero sockets *and* zero log lines, the exact field set of every event,
every event name the panel can fire is documented on this page, the
refusal/fault fork (a refusal is `render_refused` and never a `render_failed`
carrying `error_class: refused`; a real crash is untouched; refusals stay out
of the local error rate), prompt/path
non-leakage (including a prompt quoted inside an exception), forbidden-key
dropping, the receiver directives riding on *every* event type, no `$ip` and no
`$geoip_*` key leaving the panel (either would empty the country column or
invent one) and the whole `$` namespace being unreachable from a call site,
bucketing, log rotation and the local
aggregates.

To watch it live, tail the local log while you render:

```sh
tail -f state/usage-log.jsonl
```

## Fleet dashboard tiles (HogQL)

The queries behind the **Phosphene Fleet** dashboard's tiles, kept here so the board can be rebuilt (PostHog → New insight → SQL → paste → Add to dashboard). Each is a one-click breakdown; none joins across installs.

**Activation — installs vs first renders per day (14d)**

```sql
select toDate(timestamp) as day, countIf(event='app_installed') as installs, countIf(event='render_completed' and toString(properties.first_render)='true') as first_renders
from events where timestamp > now() - interval 14 day group by day order by day
```

**Versions in use — active installs by version (7d)**

```sql
select properties.version as version, count(distinct distinct_id) as installs
from events where timestamp > now() - interval 7 day and event='app_boot' group by version order by installs desc
```

**Failure rate by version (7d)**

```sql
select properties.version as version, countIf(event='render_completed') as ok, countIf(event='render_failed') as failed, round(100*failed/greatest(ok+failed,1),1) as fail_pct
from events where timestamp > now() - interval 7 day group by version having ok+failed >= 10 order by version
```

**Why renders fail — error class (7d)**

```sql
select properties.error_class as class, count() as events, count(distinct distinct_id) as installs
from events where event='render_failed' and timestamp > now() - interval 7 day group by class order by events desc
```

**Where we said no — refusals (7d)**

```sql
select properties.refusal as reason, count() as events, count(distinct distinct_id) as installs
from events where event='render_refused' and timestamp > now() - interval 7 day group by reason order by events desc
```

**Where renders come from — source (7d)**

```sql
select properties.source as source, count() as renders, count(distinct distinct_id) as installs
from events where event in ('render_completed','render_failed') and timestamp > now() - interval 7 day group by source order by renders desc
```

**Feature use (7d)**

```sql
select properties.feature as feature, count() as uses, count(distinct distinct_id) as installs
from events where event='feature_used' and timestamp > now() - interval 7 day group by feature order by uses desc
```

**Update prompt — how people answer it (7d)**

```sql
select properties.action as action, count() as events, count(distinct distinct_id) as installs
from events where event='update_prompt' and timestamp > now() - interval 7 day group by action order by events desc
```

**Updates landed per day (14d)**

```sql
select toDate(timestamp) as day, count(distinct distinct_id) as installs_updated
from events where event='app_updated' and timestamp > now() - interval 14 day group by day order by day
```

**Hardware mix of active installs (7d)**

```sql
select multiIf(toFloat(properties.ram_gb) <= 16, '≤16 GB', toFloat(properties.ram_gb) <= 24, '24 GB', toFloat(properties.ram_gb) <= 36, '32–36 GB', toFloat(properties.ram_gb) <= 64, '48–64 GB', '96 GB+') as ram, count(distinct distinct_id) as installs
from events where event='app_boot' and timestamp > now() - interval 7 day group by ram order by installs desc
```

**Renders per active install (7d)**

```sql
select bucket, count() as installs from (select distinct_id, multiIf(c=0,'0', c<=2,'1–2', c<=10,'3–10', c<=50,'11–50','50+') as bucket from (select distinct_id, countIf(event='render_completed') as c from events where timestamp > now() - interval 7 day group by distinct_id)) group by bucket order by bucket
```

**Queue breaker fires (14d)**

```sql
select toDate(timestamp) as day, count() as fires, count(distinct distinct_id) as installs
from events where event='queue_paused_breaker' and timestamp > now() - interval 14 day group by day order by day
```

### `pro_click`

(5.0.7, owner 8 Nov 2026.) Someone opened the Pro window or pressed **Get Pro**. Two closed
fields: `where` is the surface (`topbar`, `remote_hint`, `remote_panel`, `pro_modal`,
`star_card`, `speedup_line`, `feature_gate`, `settings`) and `action` is `open` (the Pro window
opened) or `get` (the Get Pro / See Pro link was pressed). Nothing else; the usual super
properties (edition, chip family, RAM) ride along. Same opt-out as every event.

### `pro_hint`

(5.0.7.) The one quiet "Pro would have made this faster" line under a finished Free render.
`action` is `shown`, `dismissed` or `opened` (once each per line); `lane` names the measured
setting it was for (`h3_high_i2v_5s`, `h3_high_i2v_10s`, `ltx_high_t2v_5s`, `ltx_high_i2v_5s`).
No times, no prompt, no file. The line itself appears only on a Mac and settings with a measured
speed-up, at most once a day, and ends after three dismissals.
