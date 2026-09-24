# Hailuo H3 — Phosphene's second video engine

MiniMax-H3 (FL2VA) takes one prompt and returns **picture, dialogue and sound
generated together**. It is a **peer of LTX, not an add-on to it** — but it is
too big to ship in the base install, so it arrives as **its own one-click
install**: ~75 GB of weights, a 36 GB+ Mac, and a licence with territory
restrictions. LTX remains the default engine and is completely untouched by
any of this; whichever engines you have installed sit side by side in the
switcher.

---

## How much memory it actually needs

**Two lanes, two floors, and neither of them is 64.** The panel used to say
"about 64 GB" in every refusal and tooltip; that number was never one of the
product's own floors and it told 48 GB owners their Mac could not do something
it can.

| Band | Lane | State |
|---|---|---|
| **≥ 60 GB** (`H3_MIN_RAM_GB`) | bf16 master DiT | Renders. 64 GB Macs report ~63.x after firmware reservations, which is why the floor is 60. |
| **≥ 36 GB** (`H3_MIN_RAM_GB_Q8`), Q8 DiT pack on disk | quantised Q8 DiT | Renders. |
| **≥ 36 GB, Q8 DiT pack absent** | — | **Not a hardware verdict.** `scripts/pinokio/h3_build_q8.sh` builds that pack locally in ~5 min / ~22 GB with **no extra download**, and "Install Hailuo H3" runs it automatically below 60 GB. The switcher shows H3 as an offer, the inline card names the step, and `h3_status()` carries `needs_q8_dit: true` with the sentence in `ram_note`. The sentence depends on whether the ~75 GB is already on disk: never installed → the sidebar's **Install Hailuo H3** (a real download, the Q8 build is its last step); weights present → **Build Hailuo H3 compact engine** (minutes, no download). Both entries are in the Pinokio sidebar while the panel runs, too (4.15.3; before that they appeared only with the panel stopped). |
| **< 36 GB** | — | Genuinely out of reach. The refusal states 36, not 64. |

`h3_ram_verdict()` is the single place those bands are decided. The render
refusal, the `make_job` fallback line, the switcher tooltip, the engine-row
note and the inline install card all read it, so they cannot state four
different numbers again.

**The floors are per tier, not per render.** They were set from peaks measured
at 1024×576 / 124f (27.3 GiB Q8, 42.8 GiB bf16). On the staged loader at
243 frames / 10.125 s the same lanes peak at **32.6 GB (Q8)** and **50.8 GB
(bf16)** — peaks scale with frame count. A 3 s Q8 clip needs far less than a
10 s one, so a length-aware gate could open short H3 renders to smaller Macs.
That gate does **not** exist; the floors are deliberately one number per lane.

**Field data — 48 GB really runs it.** A user report (X, 2026-09-24, v4.15.3,
M5 Pro 48 GB, the compact Q8 engine, Image mode, 3 s clips). Peak is the
system memory the user read while rendering, not an MLX-internal number:

| Tier | Clip | Peak memory | Wall |
|---|---|---|---|
| Draft (640×384) | 3 s | 37 GB | 2 m 32 s |
| Standard (768×448) | 3 s | 36 GB | 3 m 53 s |
| High (1024×576) | 3 s | 39 GB | 13 m 27 s |

Consistent with the Q8 lane's 25.63 GiB run peak plus macOS and the panel, and
with the 36 GB floor leaving headroom on a 48 GB Mac.

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
| `LTX_H3_WIDE_DRAFT` | unset | adds an experimental **512×288 16:9 draft** (~2 min). Off because 0.15 MP is below anything this campaign has measured — see Tiers. |

**Set them in `ENVIRONMENT`, not in your shell.** An `export` in a terminal reaches
only a panel launched from that same terminal, and it is gone the moment Pinokio
restarts the app — which is exactly how a working H3 install "loses" its engine
after a restart, with the weights still on disk and the panel reporting the pack
missing. The `ENVIRONMENT` file at the install root is read on every launch, so
lines there survive restarts and updates:

```
LTX_H3_ROOT=/path/to/minimax-h3-mlx
LTX_H3_MODELS=/path/to/models
```

The `export` form shown further down is for one-off CLI runs, not for the panel.

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

| Tier | Geometry | Aspect | Windows | Sigma points | Wall time |
|---|---|---|---|---|---|
| Draft · 3s | 640×384 · 73f | 5:3 | 1 | 9 (8 forwards) | ~3 min |
| HQ · 3s | 768×448 · 73f | 12:7 | 1 | 9 (8 forwards) | ~4-5 min |
| HQ · 5s | 768×448 · 124f | 12:7 | 1 | 9 (8 forwards) | ~8 min |
| **Wide / High · 5s** | **1024×576 · 124f** | **16:9** | 1 | **16 (15 forwards)** since 2026-09-16 | **~34 min** (was ~19 at 8 forwards) |
| Long · 10s | 768×448 · 243f | 12:7 | **2 × 124f chained** | 9 (8 forwards) | ~17 min |
| Long · 15s | 768×448 · 362f | 12:7 | **3 × 124f chained** | 9 (8 forwards) | ~27 min · batch |

### Aspect — why one tier is 16:9 and the rest are not (2026-08-06)

Every tier used to render at an odd ratio and get **pillarboxed** by the export
pass, and nothing in the UI said so. The canvas must be a multiple of 32 in both
axes (the runner errors otherwise), so exact 16:9 means width `512k`, height
`288k`, and there are only three of those:

| k | Canvas | Pixels | Verdict |
|---|---|---|---|
| 1 | 512×288 | 0.15 MP | below anything measured here — `LTX_H3_WIDE_DRAFT=1` only |
| 2 | **1024×576** | **0.59 MP** | **the delivery canvas — `wide_5s`** |
| 3 | 1536×864 | 1.33 MP | over the model's own `MAX_PIXELS` (1.03 MP) and dearer than native 1344×768. Not shipped. |

`wide_5s` is measured, not extrapolated (the quality loop's R1 run, on the same
M4 Max): 22,923 packed rows, 126.0 s/step, 90.5 s VAE decode, 10.71 GiB decode
peak, **18.8 min**; the same probe put 768×448 at 9.1 min against this table's
~8 min, hence the ~17-19 band. Denoise stayed at 37.6 GiB — identical to
768×448, because the DiT weights dominate. **The retired ckpt500-EMA Turbo
adapter measured 8.5 min on the same canvas** (3 forwards at 128.0 / 127.4 /
123.9 s + 131 s of fixed load/decode,
`codex/opt_out/wide169/w169.log`) — which is why `turbo_eta` is
derived per tier from forward counts rather than from a flat ratio (Turbo always
runs 3 forwards and the fixed cost never shrinks, so the observed ratio was
0.45 on an 8-forward tier and 0.59 on a 6-forward one). LightX2V v1.0 has not
yet received an end-to-end timing run, so the active UI marks its estimates as
derived rather than claiming the retired adapter's wall clock. Against 768×448
at the same seed and forwards, the historical run resolves individual eyebrow hairs, forehead pores,
eyelashes and fur strands where the smaller canvas has smears — **and** 1080p
delivery drops from a 2.41× enlargement to 1.875× with no bars.

Each tier's `aspect` is derived from its own width/height (`_h3_aspect`) and
appended to the `spec` string the chip prints, so the advertised ratio can never
drift from the geometry that renders.

**High runs 16 points (2026-09-16).** Every High cell (1024×576, any length)
now runs 16 sigma points = 15 forwards per window. A matched gym A/B (same seed,
still and prompt) at 15 forwards was clearly better than at 8, where the face was
blurry; 1024×576/124f is ~22.9k packed rows, well past the ~13k envelope below.
Knobs: `H3_HIGH_STEPS` and `H3_NATIVE_STEPS` in `mlx_ltx_panel.py` (Native stays
at 9 until its own 15-forward test is judged). A cell runs
`max(length steps, canvas steps)`. `H3_MEASURED_ETA` entries carry the forwards
they were measured at, so the old 18.8 min High receipt no longer prints as
"measured" for a 15-forward cell.

**Why 9 points everywhere else.** `--steps` is sigma *points*; the runner does
`points - 1` forwards. A matched-cost A/B showed 8 forwards is visually free at
or below ~13k packed rows (640×384/73f ≈ 5.6k, 768×448/124f ≈ 13.7k). A *dense*
768×448/243f pass is ~25k rows, where 8 forwards **ghosts** (two astronauts on
screen) — which is why the old 10 s tier needed 15 forwards and 36 minutes.
Chaining removes that regime entirely: every pass is a 5 s pass, so every pass
is inside the window where 8 forwards was proven.

Geometry rules the runner enforces: width and height must be multiples of 32,
and frame counts snap up to the `17n+5` grid.

### Turbo adapter resolution (2026-08-14)

Turbo uses the runner's `--lora PATH:SCALE` interface at scale `1.0`. The panel
resolves an exact ordered allowlist under `turbo-lora/`:

1. `lightx2v_v1.0_768p_ourlayout.safetensors` — the default.
2. `lightx2v_v0.1_ourlayout_alpha8.safetensors` — compatibility fallback only
   when v1.0 is absent.

The raw upstream v0.1 file,
`minimax_h3_fl2v_turbo_4step_v0.1.safetensors`, is never accepted. Its
alpha/rank factor is external to the checkpoint; loading it at scale 1.0
renders coloured noise. Resolution therefore uses explicit filenames, not a
glob.

The Apache-2.0 v1.0 source is
[`lightx2v/Minimax-h3-Turbo`](https://huggingface.co/lightx2v/Minimax-h3-Turbo)'s
`minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors`
(SHA-256
`1bdabc2e9fce20b1db563b96bcf6e46adcad4c1964f423676436bf266cc7416c`).
It still needs the runner-layout repack. `install_h3.js` deliberately contains
no raw-file fetch: it records the exact release-asset publication TODO and must
gain a digest-checked fetch only after the repacked asset and its output digest
exist.

### Speed: ⚡ Fast | ✦ Best — TaoMate's 3-step adapter (2026-09-17)

ONE switch above the Quality cards. **Fast** renders with TaoLiveAIGC's
TaoMate-H3 3-step adapter on its own sigma ladder; **Best** is each shape's own
sampler, unchanged (Draft/Standard 8 forwards, High 15, Native as tuned) and the
only place the Steps override shows. Fast is the default once its adapter is
installed; the choice is remembered per browser (`phos_h3_speed`). The cards'
estimates follow the switch and the mode (T2V/I2V). Fast runs on **Draft,
Standard and High** (5 s windows, chained too); **Native** and the dense 10 s
single pass are not validated on 3 steps and always render Best — the switch
says so under itself when one of them is picked.

| Cell (5 s, M4 Max, char + style LoRA) | Best | Fast |
|---|---:|---:|
| Draft 640×384 | 6.9 min (8 fwd, full decode) | **3.0 min** I2V (panel, cold cache: 3.4) |
| Standard 768×448 | ~9 min (8 fwd) | **4.8** I2V · **4.9** T2V |
| High 1024×576 | 35.1 min (15 fwd) | **9.0** I2V · **8.4** T2V |

- **Recipe.** TaoMate distils H3 onto points `(0, 16, 33, 49)` of the shifted
  50-point sigma grid (video σ `[1, .96117, .85333, 0]`, audio σ
  `[1, .86087, .59259, 0]`) — **3 forwards**. Runner spelling:
  `--steps 4 --sigma-subset 50:0,16,33,49 --lora <adapter>:1.0` (`--steps 4`
  alone would re-space a 4-point grid the adapter never saw). Needs a runner
  with `--sigma-subset` (minimax-h3-mlx `codex/h3-engine-v2` from `ccf532b`)
  and stacking `--lora`; on an older runner the Fast half says "update H3
  runner". Stacks with character/style LoRAs (adapter first; 416 wrapped,
  0 unaccounted on the validation render). Full VAE decode, never TAE.
- **Install.** Not installed → the Fast half reads **Install (180 MB)**; one
  click (confirm names the license) runs `POST /h3/tristep/install`: Kijai's
  rank-resized conversion
  `turbo-lora/minimax_h3_taomate_3step_lora_avg_rank_19_bf16.safetensors`
  (alpha == rank, ~87 % of the full delta) from `Kijai/MiniMax-H3_comfy` @
  `098f8c4…`, resumable (HTTP Range on the `.partial`), exact size + sha256
  `de9663d9…` before the rename. Never re-hosted. License: MiniMax H3
  Community License (same as H3). A full-rank runner-layout copy
  `taomate_h3_3step_ourlayout.safetensors` in the same folder wins. The
  official release is T2AV only; I2V is unofficial (owner-approved look).
- **Turbo is folded in.** The old Standard | Turbo row is gone: Fast replaces
  it (faster, and the owner's pick), and two speed controls or two adapters on
  one render is what the switch removes. The form posts `h3_turbo=0`; the
  server still honours `h3_turbo` from the API (Storyboard, One Shot, curl)
  wherever Fast does not run, and drops it when Fast does. A Turbo clip's Load
  Params / Finish maps to Fast.
- **Job field** `h3_tristep`: `1` Fast, `0` Best, absent = default (Fast where
  offered and installed, unless a Steps pin asks for a depth). make_job and
  `run_h3_job_inner` both refuse it off Draft/Standard/High and never stack it
  with Turbo.
- **Upscale & Face Fix** sits beside Generate (same state as the checkbox under
  Upscale); the footer line prices the pair from the cell, e.g.
  `Draft · Fast + Face Fix ≈ 5.5 min`. Face Fix receipts: 640×384 → 2.5 min,
  1024×576 → ~9 min; 768×448 ~4 min is interpolated.
- **Provenance.** Sidecar `params.h3_tristep`, and `h3.tristep` = file,
  version, repo, revision, sha256, source repo, license, steps, forwards,
  sigma subset, decode; plus `h3.schedule` (the sigmas the runner ran) and
  `h3.lora_accounting`. The ⓘ modal prints a Speed row.

### Chained windows (v3.4.1)

Window *N*'s **last decoded frame** becomes window *N+1*'s first-frame keyframe
through the ordinary FL2VA conditioning path; the duplicate frame is dropped at
the join and the pieces butt-join in pixel space, with a one-frame equal-power
audio cross-fade (the chain's real overlap). The panel passes:

```
--frames <window_frames>  --chain-windows N  --chain-total-frames <delivered>
```

Measured (M4 Max 64 GB, on the H3 campaign's chained-window runs):

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
and `make_job` falls back to `H3_TIER_FINISH_DEFAULT` (`hq_5s`) rather than
failing a queued job — named explicitly, because the old "last single-pass row
in the table" scan started meaning the ~19 min `wide_5s` the day a tier was
appended, and a fallback nobody asked for has to be the cheap one.
`LTX_H3_DENSE_10S=1` restores the old dense 10 s tier for A/B work.

### Export pass — the same post-process LTX renders get

Draft writes 640×384 (5:3) and Standard 768×448 (12:7), neither of them 720p
nor 1080p. The panel runs the identical ffmpeg recipe an LTX render gets:
lanczos scale + `libx264` with the user's codec settings (`yuv420p crf 18` by
default) + `+faststart`, audio copied through untouched.

**Since 4.16.1 a near-16:9 canvas FILLS the frame instead of getting bars.**
Until then a non-matching source was fitted inside the canvas and padded:
Draft → 720p had **40 px of black on each side**, Standard 23 px (luma-scanned
on real exports). A 48 GB user on X asked whether that was a bug (2026-09-24),
and for a file whose job is to be 720p it was. Now a source within
`EXPORT_FILL_MAX_TRIM` (8%) of the target aspect is scaled to **cover** the
canvas and centre-cropped: 5:3 → 720p trims 24 px top and bottom, 12:7 13 px,
Native 7:4 → 1080p 8 px. The same rule applies to LTX exports (1280×704 → 720p
trims 14 px left and right instead of 8 px bars) and to the Sharp (PiperSR)
path, which carries its own copy of the constant. A source further off —
square, 4:3 — still pads: cutting a third of the picture is worse than bars.
The plan reports `fill: True` plus `trim_w`/`trim_h`.

The picture was never the problem: an Image-mode reference is already
cover-cropped onto the tier canvas before conditioning (`_h3_fit_first_frame`,
centre crop — no face detection ships with the base install), so the bars came
only from this export pass. The High tier (1024×576) is exact 16:9 and gets
neither a crop nor a pad.

**A source that already matches the target aspect takes a pure-scale path**
(2026-08-06). `compute_upscale_plan` compares `w·target_h == h·target_w`; on a match
it emits `scale=W:H:flags=lanczos` with **no pad filter at all** and reports
`pad: False` on the plan (plus `fit_w`/`fit_h`, the content size inside the
canvas, so a sidecar reader can tell bars from picture). `wide_5s` → 720p is a
pure 1.25× and → 1080p a pure 1.875×. Everything that does not match keeps the
fill-or-pad rule above.

The panel says which one you are about to get: `_h3_export_notes()` generates one
sentence per export mode **from `compute_upscale_plan` itself** — "720p: pure
1.25× scale to 1280×720 — no bars, no padding." vs "720p: 12:7 fills 1280×720 —
13 px trimmed top and bottom, no bars." — ships it on each tier as
`export_note`, and `_h3SyncExportNote()` prints it under the Export row. The copy
can never disagree with the ffmpeg command because it is generated from it.

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

## Dialogue on H3 — the recipe (measured 2026-09-08)

H3 speaks only what is written inside its dialogue tag. A line written as
prose quotes — `She says: "You said Tuesday."` — leaves the voice switched
off and the model guessing: a 10 s draft written that way transcribed (whisper
large-v3-turbo) as *"You know, for when? Please, Jack, you're telling him…"*.
The same engine, same canvas, with the line in the tag, transcribed the line
word for word — at 640×384 draft and at 768×448 standard alike, so a draft is
enough to judge whether a line comes out.

One 5-second window, in the three-field form the Storyboard planner writes
(`docs/PROMPTING.md` has the laws; this is the shape):

```text
integrated_multimodal_description: [Shot 1] Live-action, vintage 1990s
multi-camera sitcom, a medium shot of GEORGE, <two or three anchors: build,
glasses, jacket>, <where he is, the light>. The camera holds a static shot,
the frame never moves - no pan, no push-in, no reframing. <What he does
first, in silence.> Then George, with a nasal, agitated New York voice (S1),
says: <d>[English] I thought it was ashwagandha!</d> Exactly as his voice
stops, his jaw ceases speaking motion and his mouth settles closed, and a
studio audience laughs. No text appears at any point.

overall_soundscape: The room tone of a television studio, then a burst of
studio-audience laughter right after the line.

non_diegetic_music: N/A
```

The rules that were paid for:

- **The words go in `<d>[English] …</d>` and nowhere else.** Speaker, voice
  and delivery stay outside the tag; the speaker keeps one ID (`(S1)`) across
  every shot. The panel and the runner pass the tag through untouched.
- **At most about eight words per five seconds.** Speech runs ~2.4 words a
  second and the mouth needs a second to settle (`storyboard.SPEECH_WORDS_PER_SEC`
  is the same budget the board validator enforces). A twelve-word line spills
  past the window.
- **Stop the mouth in the same sentence** — "his jaw ceases speaking motion
  and his mouth settles closed" — or the model keeps it moving on nothing.
- **Voiceover has a fixed phrase.** `George (S1) says in an off-screen
  voiceover: <d>[English] …</d> while his lips remain completely closed.`
  Without the closing clause the on-screen mouth lip-syncs the narration.
- **One face in frame while a line plays.** Every mouth in frame moves to
  whatever voice plays. The other character's hands may enter the frame; the
  reverse angle is its own shot.
- **A laugh track is a soundscape event**, not dialogue: "then a burst of
  studio-audience laughter right after the line" gave a clean line at
  0.5–2.5 s and laughter at 3–4.5 s at the same loudness; the identical prompt
  without it is silent after the line.
- **Music is instrumentation.** "A funky slap bass riff with a bright synth
  pop, fast tempo, two bars, that ends before he speaks" plays as a sting under
  the first second; "upbeat sitcom music" is a mood word and does nothing
  predictable.

A chained 10 s or 15 s clip carries one line per window through the
per-window prompts (`h3_chain_prompts`); a board shot carries one line per
5 s shot. The Saint Feld chapter (twelve 5 s draft shots, every line
transcribed back word for word on the first take) is the reference render.

---

## LoRAs

H3 takes community adapters through the same `--lora PATH[:SCALE]` flag Turbo
rides on. Four things about that are not obvious, and each is enforced in code:

**Its own library.** H3 LoRAs live in `<models root>/loras/`, beside
`turbo-lora/` and the weight components — **not** in `mlx_models/loras/`, which
is the LTX (and mflux image) tree. The split is a directory, not a naming
convention, because the picker filters on *which tree a file was found in*
before it looks at any metadata: a hand-dropped `.safetensors` with no sidecar
classifies as `unknown`, and `unknown` is deliberately permissive. The two
engines share no module tree, so an LTX adapter handed to H3's loader matches
zero modules — a **silent** no-op — and an H3 one handed to LTX fails inside the
fuser. `make_job` scrubs the wrong lane off every job, in both directions.

**One slot.** `generate_staged.py` declares `--lora` with `default=None`, not
`action="append"`, and calls `parse_spec` once. So there is exactly one adapter
per render and **Turbo and a user LoRA cannot both run**. The panel refuses to
pick a winner silently: `h3_lora_slot` (make_job allowlist) is `turbo` by
default, in which case picking a LoRA while Turbo is on is a hard error naming
the conflict; setting it to `user` is the explicit opt-out — Turbo is released,
the step count goes back to the tier's, and both facts are logged. Stacking two
LoRAs is refused the same way.

**Stacking (runner with the repeatable `--lora`, 2026-09-06+).** The runtime
adapter keeps every delta out of the base weight, so N adapters are a sum:
`y = base(x) + Σ scale_i · (x @ A_iᵀ) @ B_iᵀ`. `generate_staged.py` takes
`--lora PATH[:SCALE]` any number of times, Turbo first, and the panel posts
Turbo plus up to four user LoRAs on a runner whose `--lora` help carries
"Repeat the flag to stack adapters" (`h3_supports_lora_stack`, probed from the
script text like every other capability). `/status.h3.loras.max_stack` reports
the live limit, so the slot control disappears on a stacking runner and stays
for an old pack. The community rule the panel repeats in its advisory: keep
the strengths' total near 1.5 or under, and never stack two LoRAs that pull
the same axis (two faces, two styles). A second `apply_lora` appends to the
existing wrapper rather than nesting one — nesting hid the quantized base's
`scales` from `plan()` and skipped every module (`tests/test_lora_stack.py`
in the engine tree pins this).

**Key layouts.** Three exist in the wild and only two work:

| Layout | Keys | What happens |
|---|---|---|
| bare | `blocks.N.attn.qkv_proj.lora_A/B.weight` | loads as-is |
| ComfyUI repack | the same, namespaced under `diffusion_model.` | **converted in place** at install time — a safetensors *header* rewrite (the offsets are relative to the tensor buffer, so every tensor byte is untouched), recorded in the sidecar as `lora_layout` / `lora_converted_prefix` |
| diffusers / PEFT | split `to_q`/`to_k`/`to_v`, `ff.net.*` | **refused, by design** |
| kohya | `lora_down`/`lora_up` + `.alpha` | **refused, by design** |

The refusals are not laziness. A diffusers-namespace adapter needs a runtime
`alpha / rank` multiplier that **is not in the file** — LightX2V's is
`8 / 128 = 0.0625`, supplied externally by its own inference script — and
applying it at 1.0 renders coloured noise, not a slightly-off clip. See
`LIGHTX2V_LORA_FIX.md` and `scripts/convert_lightx2v_lora.py` in the engine
repo for the manual path. The panel says exactly this in the error.

**CivitAI.** The browser's video context has family pills (`ltx` / `h3`),
preselected from the active engine, so an LTX user's default view is unchanged.
CivitAI's base-model string is **`MiniMax H3`** — verified against the live API;
`MiniMax`, `Hailuo` and `Hailuo H3` all return zero. Downloads route by the
item's *own* base model, not by which pill was showing, so a MiniMax H3 LoRA
lands in the H3 library even if it was found under "All".

`/status.h3.loras` reports `{supported, dir, count, usable, max_stack}`;
`supported` is a probe of the **installed** runner for `--lora`, and the picker
is hidden entirely on a pack that predates it.

---

## What H3 does *not* do

- **Modes**: Text and Image only. Every other mode (FFLF, Extend, Remix,
  Character, A2V) is LTX-pipeline-specific; the picker snaps back to LTX with a
  note. Character does too — it submits `mode=t2v` but stacks **LTX** LoRAs, and
  those cannot load on H3 (see LoRAs below, which is about H3's own library).
- **LoRA STACKING**: H3 takes exactly one adapter — see LoRAs below.
- **Orientation, accel, temporal interpolation, the LTX upscale control**: none
  apply. Those carry `data-ltx-only` and fold away under
  `body[data-engine="h3"]`. H3 has its own export control (`h3_upscale`,
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
export LTX_H3_ROOT=<campaign-checkout>/opt
export LTX_H3_PYTHON=<campaign-checkout>/minimax-h3-mlx/.venv/bin/python
export LTX_H3_MODELS=<campaign-checkout>/models
```

**What a user gets from `install_h3.js` today (Text only)** — the published
branch, with its own venv:

```sh
export LTX_H3_ROOT=<campaign-checkout>/minimax-h3-mlx
export LTX_H3_MODELS=<campaign-checkout>/models
```

Add either block to the normal panel env (`start.js` / `run_panel.sh`) and
restart. `GET /status` → `.h3` tells you what resolved:

```json
{ "capable": true, "available": true, "first_frame": true, "missing": [] }
```

---

## Troubleshooting

> **Where the control is.** The engine switcher lives in the **top right of the
> header**, beside the memory / models pills — not in the Video tab. The
> "Engine row" that older copy pointed at has not existed since the engine
> table landed (#58 is a user who installed H3 fine and went looking there).

| Symptom | Cause |
|---|---|
| Engine switcher not visible at all | Under the **36 GB** floor. `h3.capable` is false, so there is only one engine left to choose between and the switcher hides with its divider. A 48 GB-class Mac whose reduced-RAM Q8 DiT pack is not on disk **no longer lands here** — see the RAM bands below. |
| H3 pill dashed, "not installed" | `h3.missing` lists exactly which component didn't resolve |
| Image mode snaps back to LTX | `h3.first_frame` false — the installed runner has no `--first-frame` |
| `ffmpeg not found on PATH` | the runner pipes raw RGB into `ffmpeg`; the panel prepends `FFMPEG_BIN` to the subprocess PATH, so this means the bundled binary is missing |
| Job cancelled but memory stays high | shouldn't happen — `/stop` SIGTERMs the whole process group and SIGKILLs after 8 s; check `STATE["h3_pgid"]` |
| 10 s / 15 s tiers missing from the strip | `h3.chain` false — the installed runner has no `--chain-windows`; update the pack |
| A render survived a panel crash | expected of `kill -9` (atexit can't run), but the NEXT boot reaps it: `state/h3_running.json` names the pid/pgid and `reap_orphan_subprocesses()` kills it before the queue moves. Same guard exists for the warm helper (`state/helper_running.json`). |

Metrics for every run are kept at `state/h3_metrics/<job_id>.json` (the runner's
own phase timings, peak GiB, packed rows), and summarised into the render's
`<output>.mp4.json` sidecar under the `h3` key.
