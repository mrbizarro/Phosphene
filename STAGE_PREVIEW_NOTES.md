# Main-stage live preview receipts

Date: 2026-08-14  
Workspace: `/Users/salo/pinokio/api/phosphene-dev.git` (`dev`)  
Requested isolated port: `8272`

## Delivered behavior

- The existing `/status` poll now normalizes one live-preview object for both
  consumers: the compact Now thumbnail and the full-size main stage. No second
  request, timer, or queue-list render path was added.
- A preview-capable render takes the idle stage immediately. Before the
  engine-owned meaningful gate it shows a calm `Finding the shot…` state;
  afterwards it shows the cache-busted preview image with `LIVE`, `forming take
  · step n/m`, ETA and the existing cooperative Stop-early flow.
- A playing video, or a video whose controls were touched in the previous 12
  seconds, retains the stage. A `LIVE · return to render` chip is the explicit
  way back.
- Completion keeps the last preview mounted until `list_outputs()` admits the
  new mp4, then fades the decoded finished video over it. The intentional
  two-second in-flight/mtime listing cutoff therefore produces no empty-player
  flash.
- LTX meaningful thresholds remain server-owned (`6` on distilled lanes, `2`
  published estimates on `res_2s`). H3's `h3-live-preview/1` adapter marks its
  proven first forward meaningful. Special LTX lanes and older H3 runners that
  publish no preview remain unchanged.
- H3 preview wiring is capability-probed. A runner with `--live-preview` and
  `--live-preview-dir` receives the H3 TAE checkpoint and the per-job live
  directory; the currently shipped older runner receives an unchanged argv.
  H3 exit `75` is mapped to `stopped`, never `failed`.

## Rendered-page screenshot receipts

The four requested screenshots could not be captured in this execution
environment. Browser initialization returned `No browser is available`, and
the browser inventory was empty (`[]`). The browser workflow explicitly
forbids replacing the in-app browser with Playwright, shell/CDP, or another
browser backend, so no synthetic screenshots were fabricated.

| Requested receipt | Result | Executable coverage available here |
|---|---|---|
| Warming state | **BLOCKED — no browser backend** | `test_ltx_lane_warms_before_first_meaningful_frame`, `test_h3_capable_lane_warms_before_first_frame` |
| Full-size LIVE state and overlay | **BLOCKED — no browser backend** | `test_meaningful_preview_owns_full_stage` |
| Do-not-steal chip while a clip plays | **BLOCKED — no browser backend** | `test_playing_clip_is_not_stolen`, `test_recently_touched_paused_clip_is_not_stolen` |
| Completion handoff | **BLOCKED — no browser backend** | `test_completion_requests_seamless_handoff`, `test_completion_keeps_last_frame_until_output_is_listed` |

## Isolated end-to-end render receipt

The isolated directories were created under this repository:

- `.stage_preview_test/state`
- `.stage_preview_test/outputs`
- `.stage_preview_test/uploads`

The required locks were checked first and both were absent. Acquisition then
stopped at the first lock, in the required order:

```text
mkdir /Users/salo/AI/projects/hailuo-mlx/.gpu_lock
mkdir: /Users/salo/AI/projects/hailuo-mlx/.gpu_lock: Operation not permitted
```

That directory is outside the session's writable sandbox. The second lock was
not created, no render was launched, no GPU work ran, and no existing lock was
removed.

The isolated panel was also probed independently with port `8272` and its own
state/output/upload directories. It failed before serving a page:

```text
PermissionError: [Errno 1] Operation not permitted
  at ThreadingHTTPServer(("127.0.0.1", 8272), Handler)
```

Ports `8198` and `8199`, the production clone, and production state/output
directories were not touched.

## Executable receipts

The focused contract executes the real client functions extracted from
`mlx_ltx_panel.py` in Node plus the Python H3 file-schema adapter:

```text
./ltx-2-mlx/env/bin/python3.11 test_stage_live_preview.py
Ran 16 tests in 0.133s
OK
```

The broader CPU-only panel suite also passed:

```text
./ltx-2-mlx/env/bin/python3.11 -m unittest -v \
  test_stage_live_preview test_character_roundtrip test_lora_compat \
  test_storyboard_planner test_storyboard
Ran 216 tests in 0.946s
OK (skipped=1)
```

The skip is the existing opt-in live planner-model test. JavaScript syntax,
Python compilation and `git diff --check` also passed.

## Required before/after gates

Both the before and after runs produced the same result:

- `node scripts/check_pinokio_scripts.js` — PASS
- `node scripts/check_ltx_pin.js` — PASS
- `node scripts/check_post_update.js` — PASS
- `python3 scripts/assert_registry.py` — PASS (`154 passed, 0 failed`)
- `./ltx-2-mlx/env/bin/python3.11 scripts/assert_schedules.py` — **environment
  blocked before assertions**. MLX aborts while creating its Metal device with
  `NSRangeException: __NSArray0 objectAtIndex: index 0 beyond bounds for empty
  array`; this sandbox exposes no Metal device. The failure is unchanged from
  the baseline run and occurs before schedule logic executes.

## Private-beta publication

`beta/main` was verified unchanged at
`efb57dbcac0ab30311f05410e9b3d7ea2d046ec5`, matching the local `dev` parent.
The authenticated GitHub write was then rejected before any blob, commit or ref
update (`user cancelled MCP tool call`). The local `.git` directory is
read-only in this execution sandbox and shell network access is disabled, so no
alternate commit/push path was available. The private beta and public `main`
remain untouched; all validated edits remain in this dev working tree.
