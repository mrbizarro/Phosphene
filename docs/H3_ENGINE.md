# Hailuo H3 — Phosphene's second video engine

MiniMax-H3 (FL2VA) takes one prompt and returns **picture, dialogue and sound
generated together**. It ships as an **optional pack**, not part of the base
install: ~75 GB of weights, a 64 GB+ Mac, and a licence with territory
restrictions. LTX-2.3 stays the default engine and is completely untouched by
any of this.

---

## How it plugs in

H3 is a **subprocess engine**, exactly like the mflux image engines: the panel
spawns a CLI, streams its stdout into the log, and reads a metrics JSON when it
exits. It is *not* an action on the LTX warm helper.

```
POST /queue/add  (engine=h3, h3_tier=…)
      │
      ▼
  make_job()            engine + h3_tier are in the params allowlist;
      │                 the tier stamps width/height/frames/steps
      ▼
  worker_loop → run_job_inner()
      │                 dispatches on params.engine BEFORE any LTX clamp
      ▼
  run_h3_job_inner()
      │  1. gates: capable? installed? mode is Text/Image?
      │  2. HELPER.kill()   ← the one cross-engine interaction
      │  3. caffeinate -i → <H3 venv python> scripts/generate_staged.py …
      │  4. stream stdout → push() + STATE.current.progress
      │  5. metrics JSON → <output>.mp4.json sidecar
      ▼
  mlx_outputs/<name>_h3.mp4  — the normal gallery picks it up
```

### Why the warm helper gets killed first

H3's staged runner materialises one large component at a time (Q8 text encoder
→ free → bf16 pruned DiT → free → the two VAEs) and still peaks around
**40 GiB**. The LTX warm helper holds its own weights resident. Both at once
does not fit on a 64 GB Mac. `run_h3_job_inner` therefore kills the helper
before launching; it respawns lazily on the next LTX job (`WarmHelper._ensure`),
so the cost is one cold start and nothing else.

### Why a separate venv

Phosphene pins `mlx==0.31.1` — `0.31.2` regresses LTX audio by 22 dB. The H3
port needs `mlx>=0.32`. A separate venv means installing H3 can never break LTX
rendering, and uninstalling H3 is `rm -rf`.

---

## Paths

Everything is env-overridable, which is what makes a dev box possible without
duplicating 75 GB.

| Variable | Default | What it points at |
|---|---|---|
| `LTX_H3_ROOT` | `<install>/minimax-h3-mlx` | the engine checkout (`scripts/generate_staged.py`, `.venv/`) |
| `LTX_H3_MODELS` | `<install>/mlx_models/hailuo-h3` | the three weight components |
| `LTX_H3_PYTHON` | `<H3_ROOT>/.venv/bin/python3.11` → `python` | interpreter override (a checkout without its own venv) |
| `LTX_H3_FORCE_CAPABLE` | unset | test-only: stop the UI hiding the pill on a small Mac. Does **not** make it render. |
| `LTX_H3_DENSE_10S` | unset | re-adds the pre-chaining dense 10 s tier (36 min) for A/B work |

### Model layout — both shapes work

`h3_paths()` tries two roots in order, so no post-download move is ever needed:

```
<LTX_H3_MODELS>/deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors
<LTX_H3_MODELS>/ddalcu-q8/{text_encoder,video_vae,audio_vae}.safetensors + tokenizer/config
<LTX_H3_MODELS>/upstream-meta/FL2VA/text_encoder/config.json
```

…or the same three directories one level down under `models/`, which is what
the engine's own `scripts/download_selected.py --root X` writes (it appends
`models/` to whatever root it is given). `install_h3.js` produces the second
shape; the campaign checkout uses the first.

---

## Tiers

`H3_TIERS` in `mlx_ltx_panel.py` is the single source of truth — the UI renders
its chips from `/status.h3.tiers`, so a tier change is one Python edit.

| Tier | Geometry | Windows | Sigma points | Wall time |
|---|---|---|---|---|
| Draft · 3s | 640×384 · 73f | 1 | 9 (8 forwards) | ~3 min |
| HQ · 3s | 768×448 · 73f | 1 | 9 (8 forwards) | ~4-5 min |
| HQ · 5s | 768×448 · 124f | 1 | 9 (8 forwards) | ~8 min |
| Long · 10s | 768×448 · 243f | **2 × 124f chained** | 9 (8 forwards) | ~17 min |
| Long · 15s | 768×448 · 362f | **3 × 124f chained** | 9 (8 forwards) | ~27 min · batch |

**Why 9 points everywhere now.** `--steps` is sigma *points*; the runner does
`points - 1` forwards. A matched-cost A/B showed 8 forwards is visually free at
or below ~13k packed rows (640×384/73f ≈ 5.6k, 768×448/124f ≈ 13.7k). A *dense*
768×448/243f pass is ~25k rows, where 8 forwards **ghosts** (two astronauts on
screen) — which is why the old 10 s tier needed 15 forwards and 36 minutes.
Chaining removes that regime entirely: every pass is a 5 s pass, so every pass
is inside the window where 8 forwards was proven.

Geometry rules the runner enforces: width and height must be multiples of 32,
and frame counts snap up to the `17n+5` grid.

### Chained windows (v3.4.1)

Window *N*'s **last decoded frame** becomes window *N+1*'s first-frame keyframe
through the ordinary FL2VA conditioning path; the duplicate frame is dropped at
the join and the pieces butt-join in pixel space, with a one-frame equal-power
audio cross-fade (the chain's real overlap). The panel passes:

```
--frames <window_frames>  --chain-windows N  --chain-total-frames <delivered>
```

Measured (M4 Max 64 GB, `notes/CHAIN_WINDOWS.md` in the H3 campaign):

| | Dense 10 s | Chained 10 s | Chained 15 s |
|---|---:|---:|---:|
| Wall | 36:12 | **17:05** | **26:34** |
| Packed rows | 25,138 | 13,662 max | 13,580 flat |
| Peak Metal | 42.6 GiB | 40.3 GiB | 40.2 GiB |

Duration is now **linear in wall clock and constant in memory**. Seam grading:
both 15 s joins measured *quieter* than the clip's own median frame-to-frame
motion; identity (face, wardrobe, light) held across both.

**Known artefact, surfaced in the UI:** every window receives the same prompt,
so a prompt that scripts a line asks for that line in *each* window and gets it.
The tier's `note` field carries that warning ("scripted dialogue repeats once
per 5 s window — put dialogue cues late in the prompt") and the panel renders it
under the tier strip. The fix is per-window prompts (`--chain-prompts` on the
runner); when that reaches the panel UI, delete the `note` and the warning
disappears everywhere at once.

**Gating.** Chaining needs `--chain-windows` on the *installed* runner.
`h3_supports_chain()` probes the script source, `/status.h3.chain` reports it,
`h3_visible_tiers()` withholds the chained tiers from the UI when it's false,
and `make_job` falls back to the longest single-pass tier rather than failing a
queued job. `LTX_H3_DENSE_10S=1` restores the old dense 10 s tier for A/B work.

### Export pass — the same post-process LTX renders get

H3 writes 768×448 (12:7), which is neither 720p nor 1080p. The panel now runs
the identical ffmpeg recipe an LTX render gets: lanczos fit inside the canvas +
pad the remainder + `libx264` with the user's codec settings (`yuv420p crf 18`
by default) + `+faststart`, audio copied through untouched. Letterbox bars on
12:7 content are correct — no crop, no distortion.

- Form field: `h3_upscale` = `off` | `fit_720p` (default) | `fit_1080p`. It is
  in the `make_job` allowlist; an unlisted field silently no-ops on
  `/queue/add`.
- The native file stays on disk but is `set_hidden()` from the gallery, exactly
  like the LTX upscale path. `UPSCALE_TAGS` (module-level) carries `1080p` /
  `v1080p` so `/output/delete` trashes the native companion and Load Params
  still finds the sidecar.
- Sidecar gains `upscale` (target, method, source, codec) and `output_codec`,
  and `h3.chain_windows` / `h3.window_frames` / `h3.delivered_frames` /
  `h3.seams`.

---

## What H3 does *not* do

- **Modes**: Text and Image only. Every other mode (FFLF, Extend, Remix,
  Character, A2V) is LTX-pipeline-specific; the picker snaps back to LTX with a
  note. Character does too — it submits `mode=t2v` but stacks LTX LoRAs, and H3
  has no LoRA path.
- **LoRAs, orientation, accel, temporal interpolation, the LTX upscale
  control**: none apply. Those carry `data-ltx-only` and fold away under
  `body[data-h3-engine="h3"]`. H3 has its own export control (`h3_upscale`,
  `data-h3-only`) — see the export-pass section above.
- **External audio**: H3 generates its own. `i2v_clean_audio` stays LTX.

### `--first-frame` (Image mode) is branch-dependent

FL2VA first-frame conditioning landed on the engine repo **after** the branch
`install_h3.js` pins (`codex/practical-apple-silicon`). The panel probes the
installed `scripts/generate_staged.py` for the flag
(`h3_supports_first_frame()`), reports it as `/status.h3.first_frame`, and keeps
Image mode on LTX when it's absent — so an older checkout degrades to Text-only
instead of dying 30 s into a render with an argparse error. **Bump `H3_BRANCH`
in `install_h3.js` once the first-frame work is published.**

---

## Running the dev box

The campaign checkout already has the weights; don't copy them into the Pinokio
install. Two working configurations:

**Full feature set (Text *and* Image)** — the `opt` tree has `--first-frame` but
no venv of its own, so borrow the sibling one:

```sh
export LTX_H3_ROOT=/Users/salo/AI/projects/hailuo-mlx/codex/opt
export LTX_H3_PYTHON=/Users/salo/AI/projects/hailuo-mlx/codex/minimax-h3-mlx/.venv/bin/python
export LTX_H3_MODELS=/Users/salo/AI/projects/hailuo-mlx/codex/models
```

**What a user gets from `install_h3.js` today (Text only)** — the published
branch, with its own venv:

```sh
export LTX_H3_ROOT=/Users/salo/AI/projects/hailuo-mlx/codex/minimax-h3-mlx
export LTX_H3_MODELS=/Users/salo/AI/projects/hailuo-mlx/codex/models
```

Add either block to the normal panel env (`start.js` / `run_panel.sh`) and
restart. `GET /status` → `.h3` tells you what resolved:

```json
{ "capable": true, "available": true, "first_frame": true, "missing": [] }
```

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| Engine row not visible at all | `h3.capable` false — under the 60 GB floor (a 64 GB Mac reports ~63.x after firmware reservations) |
| H3 pill dashed, "not installed" | `h3.missing` lists exactly which component didn't resolve |
| Image mode snaps back to LTX | `h3.first_frame` false — the installed runner has no `--first-frame` |
| `ffmpeg not found on PATH` | the runner pipes raw RGB into `ffmpeg`; the panel prepends `FFMPEG_BIN` to the subprocess PATH, so this means the bundled binary is missing |
| Job cancelled but memory stays high | shouldn't happen — `/stop` SIGTERMs the whole process group and SIGKILLs after 8 s; check `STATE["h3_pgid"]` |
| 10 s / 15 s tiers missing from the strip | `h3.chain` false — the installed runner has no `--chain-windows`; update the pack |
| A render survived a panel crash | expected of `kill -9` (atexit can't run), but the NEXT boot reaps it: `state/h3_running.json` names the pid/pgid and `reap_orphan_subprocesses()` kills it before the queue moves. Same guard exists for the warm helper (`state/helper_running.json`). |

Metrics for every run are kept at `state/h3_metrics/<job_id>.json` (the runner's
own phase timings, peak GiB, packed rows), and summarised into the render's
`<output>.mp4.json` sidecar under the `h3` key.
