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

**Time estimates on 36-59 GB (v4.16.2).** Every H3 receipt was measured on a
64 GB M4 Max, and until 4.16.2 the estimate only scaled it by chip. A Pinokio
user on an M4 Pro 48 GB was told ~17 min for a 15 s Draft and waited 40+. The
fleet (60 days to 2026-09-26, 6,587 H3 renders, wall bucket over the model's
own estimate, one median per install) puts the reduced-RAM lane at **1.32x**
the >=60 GB lane on chained 10/15 s lengths (29 vs 117 installs) and **1.07x**
on single windows (43 vs 170). `_h3_ram_factor()` carries those two numbers
(`H3_LOWRAM_FACTOR_CHAIN` / `_SINGLE`) into every H3 estimate below 60 GB, and
chained cells there show a note: about a third longer than 64 GB, quit other
apps, swapping can take far longer. The mechanism is not measured
(`render_completed` has no pressure field); memory pressure inside a budget
that leaves the Mac max(6, RAM/8) GB is the likely one. Separately, Fast is a
one-click 180 MB adapter: until it is installed, a "Draft" render runs the
full 8-forward sampler, about twice the Fast time.

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

### Its package set is locked (4.19.5)

`scripts/pinokio/h3_constraints.txt` is the H3 venv's known-good set - every
package, exact - captured from a from-zero install and validated with H3
renders. `scripts/pinokio/h3_deps.sh` is the one install into the venv and
always passes it with `-c`: Install / Repair Hailuo H3 / Update Hailuo H3
runner (`install_h3.js`), every Update (`post_update.sh` 8b) and the panel's
`POST /h3/repair`. uv moves a package already outside the set back into it,
so the same script is the repair. Before every H3 render the panel compares
the packages the runner is built on (`H3_ENV_JUDGED`) with the set, plus
transformers' own import-time dependency check, and refuses in a second with
**Repair Hailuo H3** on the card when they disagree. Through 4.19.4 the venv
was resolved from `requirements.txt` floors alone, so each new install got
whatever PyPI held that day.

Not judged: an `LTX_H3_PYTHON` interpreter (a developer's own), Linux
(`scripts/linux/install_h3.sh` builds that venv with its own mlx wheel), and
any panel started with `LTX_H3_ENV_CHECK=0` - for a dev box whose
`LTX_H3_ROOT` is a runner checkout you manage yourself. Moving the set:
regenerate the whole file from a from-zero install's venv, then a from-zero
install and H3 renders on it.

---

## Paths

Everything is env-overridable, which is what makes a dev box possible without
duplicating 75 GB.

| Variable | Default | What it points at |
|---|---|---|
| `LTX_H3_ROOT` | `<install>/minimax-h3-mlx` | the engine checkout (`scripts/generate_staged.py`, `.venv/`) |
| `LTX_H3_MODELS` | `<install>/mlx_models/hailuo-h3` | the three weight components |
| `LTX_H3_PYTHON` | `<H3_ROOT>/.venv/bin/python3.11` → `python` | interpreter override (a checkout without its own venv) |
| `LTX_H3_ENV_CHECK` | unset (on) | `0` stops the pre-render check of the H3 venv against `h3_constraints.txt` - for a dev checkout whose venv you manage yourself |
| `LTX_H3_FORCE_CAPABLE` | unset | test-only: stop the UI hiding the pill on a small Mac. Does **not** make it render. |
| `LTX_H3_DENSE_10S` | **dead — not read anywhere in the code (H3-15)** | the dense 10 s tier ("10s single pass") ships `"offered": True` unconditionally; this var does nothing. Kept documented so a future re-gate has somewhere to land, not because it currently works. |
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

The table below uses the CURRENT names — two independent axes, Quality
(canvas) × Length (duration) — the same ones `/status.h3.tiers` and the UI's
chips use. H3-31: this table used to be written entirely in the RETIRED
fixed-tier vocabulary (HQ / Wide / Long), which the UI dropped for the
two-axis picker; `H3_TIER_ALIASES` (mlx_ltx_panel.py) still resolves those old
composite keys for a sidecar written before the split — **HQ → Standard**,
**Wide → High**, **Long → the chained lengths (10s/15s) at any quality** — but
they are not choices in the UI, and this table should not have kept reading
as if they were.

| Quality | Length | Geometry | Aspect | Windows | Sigma points | Fleet median, M4 Max (Fast · Best) |
|---|---|---|---|---|---|---|
| Draft | 3s | 640×384 · 73f | 5:3 | 1 | 9 (8 forwards) | 3:15 · 4:35 |
| Standard | 3s | 768×448 · 73f | 12:7 | 1 | 9 (8 forwards) | 4:10 · 6:00 |
| Standard | 5s | 768×448 · 124f | 12:7 | 1 | 9 (8 forwards) | 5:10 · 13:00 |
| **High** | **5s** | **1024×576 · 124f** | **16:9** | 1 | **16 (15 forwards)** since 2026-09-16 | 12:00 · no fleet data (lab ~34 min) |
| Standard | 10s | 768×448 · 243f | 12:7 | **2 × 124f chained** | 9 (8 forwards) | 12:40 · 24:00 |
| Standard | 15s | 768×448 · 362f | 12:7 | **3 × 124f chained** | 9 (8 forwards) | too few fleet renders (lab ~27 min Best) |

The last column is the median wall time of real renders on M4 Max Macs (any memory size), 2026-08-10 to 2026-10-20, from the anonymous `render_completed` counts — the same numbers as https://phosphene.video/benchmarks/ and the user guide (`webapp/docs/hailuo-h3.md`). The earlier single-Mac lab figures in this column (Draft 3 s ~3 min, Standard 5 s ~8 min) were faster than what users get: the fleet puts them at 3–4.5 min and 13 min.

Legacy composite key → current two-axis key, for reading an old sidecar:

| Legacy key | Current key |
|---|---|
| `hq_3s` | `standard_3s` |
| `hq_5s` | `standard_5s` |
| `wide_5s` | `high_5s` |
| `long_10s` | `standard_10s` |
| `long_15s` | `standard_15s` |
| `long_10s_dense` | `standard_10s_dense` |
| `wide_draft_3s` | `preview_3s` |

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
peak, **18.8 min**; the same probe put 768×448 at 9.1 min against the old lab table's
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

The v1.0 source is
[`lightx2v/Minimax-h3-Turbo`](https://huggingface.co/lightx2v/Minimax-h3-Turbo)'s
`minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors`
(SHA-256
`1bdabc2e9fce20b1db563b96bcf6e46adcad4c1964f423676436bf266cc7416c`).
It is a MiniMax H3 Model Derivative, so the MiniMax H3 Community License
applies to it. Phosphene does not host it. Since 4.19.8,
`scripts/fetch_h3_turbo.py` (run by `install_h3.js`) fetches that file from
LightX2V's repo at a pinned commit, verifies its digest, re-lays it out on your
Mac into the runner layout (pure byte moves: q/k/v fused block-diagonally, the
fc1 halves swapped, alpha/rank = 1 so nothing is rescaled) and installs it only
when the result matches SHA-256
`d51d626fe0845da7e5845a47c323cf3f29086d44d24cb1a4b980882488746197` - the same
bytes as the file earlier versions downloaded. An install that already has the
file keeps it.

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

### Every H3 LoRA runs in the base's qkv row order (2026-10-22)

H3's DiT stores the fused `attn.qkv_proj` rows **per head**, `(heads, 3,
head_dim)`. The ComfyUI/diffusers definition the community trains against
splits them **contiguously**, `(3, heads, head_dim)`, and every H3 LoRA
measured is in that order: the speed adapters (Kijai's TaoMate r19, the
official TaoMate r128, larryvrh's v4-600 EMA, the LightX2V v1.x repack) and
every user LoRA in the library (ostris ai-toolkit, the fal
`minimax-h3-trainer`, a kohya-style trainer). The runner only re-orders rows
under `--lora-qkv-permute`, which has been OFF by default since 2026-08-15
and is global to a run. So since then every H3 LoRA has added its q update
to the wrong heads' rows.

**The test.** One-way ANOVA on per-row norms with q/k/v as the groups, read
both ways. The base (bf16 pruned DiT and the Q8 pack alike) reads per head:
blocks.0 F 1362 vs 83, blocks.49 5006 vs 43, token_refiner.0 1549 vs 10.
`h3_lora_qkv_layout()` runs the same test on each of a LoRA's qkv modules —
row norms of B·A via the rank×rank Gram matrix, so the full delta is never
built — and each trained module votes for the larger F. 70 % agreement
calls the file (measured files sit at 87-98 %); below that it is
"unclear". A file whose metadata declares per-head rows (our own
runner-layout copies, `fetch_h3_v13.py` builds, Viggle's "interleaved"
exports) skips the vote. 0.03-0.9 s per file, cached per size + mtime.

**Contiguous is the default (2026-10-23, 4.19.12 blocker).** MiniMax's own
release is diffusers-shaped (separate `to_q` / `to_k` / `to_v`), and every
tool that fuses them into one `attn.qkv_proj` stacks them `[q; k; v]`. So
only a declared or measured per-head file runs as it is; "unclear" (or a
dtype the vote cannot read) is re-ordered like every other fused file, with
a log line. Before this, "unclear" ran as trained — wrong for every trainer
we know of.

**fc1 is not in this class.** The runner reads `mlp.fc1` as `[gate; value]`
and so does every fused-name lineage: Kijai's comfy repack of LightX2V v1.0
swaps diffusers' `[value; gate]` into it (his metadata says so, and our own
diffusers repack agrees with his file as-is at +0.962), and his TaoMate r19
keeps the official TaoMate r128's half order (per-block half-norm ratios
identical: 0.014 / 0.014 / 0.206 at blocks 5 / 25 / 45). Diffusers-format
files are refused at import, so `--lora-fc1-swap` stays off.

**The fix.** `h3_lora_for_runner()` hands the runner, for a contiguous file,
`<dir>/runner-layout/<same name>`: each qkv `lora_B`'s 168 blocks of 128 rows
moved into per-head order by `h3_bake_runner_layout()` (whole-row byte moves,
dtype-agnostic, no arithmetic; byte-identical to the runner's
`_permute_qkv_rows` on the real TaoMate and bizarrotrn_h3 files). A per-head
file runs as it is; an MLP-only LoRA has nothing to re-order. The copy is
stamped with the source's size + mtime and re-made when either changes,
written through `.partial` + rename, and pruned once its source leaves the
library; the picker lists only the library's top level, so it never shows.
The source file is never touched, so a recipe keeps naming it. Fast and
Turbo get their copy right after download; everything else on first use. A
runner without `--lora-qkv-permute` predates 2026-08-15 and re-orders every
`--lora` itself, so it is handed the source.

**Evidence.** Fast @ High 1024×576 5 s, seed 52031: the re-ordered TaoMate
copy renders byte-identical to `--lora-qkv-permute`, and against the shipped
order the mouth is 56 % sharper (Laplacian variance 26.4 → 41.1) with no
added flicker. A character LoRA moves further: bizarrotrn_h3 at 1.0 on Fast
@ High, seed 52032, ArcFace identity against its 42 training stills 0.399 →
0.640, mouth region 24 % sharper, face 30 % steadier frame to frame — as
shipped, the trigger's description (a waxed handlebar) won over the face the
LoRA learned.

**The 4.19.12 blocker (2026-10-23).** 4.19.11 shipped only the speed-adapter
half (e1568d7): user LoRAs still went to the runner as trained, on every
speed including Best at High with lightx2v v1.3 (its own file is per-head;
a user LoRA stacked after it was not). Regression gates in
`test_h3_tristep_draft.py`: the delta the runner applies, B'·A, equals the
trained B·A with its rows moved (3, heads, head_dim) → (heads, 3,
head_dim), derived from row indices, not from either implementation; and on
the maintainer's Mac the bizarrotrn_h3 copy's tensor bytes match the copy
that rendered the corrected clips (sha256 758e0096…). The fused block weight
W + B'·A matches the pinned runner's own `--lora-qkv-permute` bit for bit
(blocks 0 / 25 / 49); as 4.19.11 passed it, |Δ − Δref| / |Δref| ≈ 1.41,
i.e. an orthogonal, unrelated update.

**Provenance.** `h3.tristep` / `h3.turbo` carry `lora` (the file the runner
read), `source` and `qkv_rows`; `params.h3_lora_qkv` lists each user LoRA
with its `qkv_rows` (`re-ordered`, `as-trained`, `none`, `runner`).

### Best on High: lightx2v v1.3 (2026-10-22)

LightX2V's v1.3 768p H3 adapter (`lightx2v/Minimax-h3-Turbo` @ `30f485ed`,
`minimax_h3_fl2v_turbo_4step_v1.3_768p_bf16.safetensors`, released 2026-10-20)
is what Best renders on High once it is built:

| High 1024×576, 124 frames, M4 Max 64 GB | forwards | wall | process peak |
|---|---:|---:|---:|
| Best, full sampler (shift 12) | 15 | 1950 s | 28.9 GiB |
| Best, lightx2v v1.3 (shift 6) | 6 | 870 s | 32.8 GiB |

Runner argv: `--steps 7 --lora <adapter>:1.0`, plus `--video-shift 6` on a
runner that takes it. Shift 6 is the shift LightX2V trained its 768p adapters
at; `--video-shift` (a runner commit not pinned yet) moves the video schedule
only. The pinned runner (3f6c75c, any stacking runner) has no such flag and
renders v1.3 at the default shift 12, and logs it: on the two-person dialogue
test, shift 12 and shift 6 gave matching faces, mouths and timing (931.8 vs
937.6 s), so an install gets v1.3 from the panel Update alone. 4.19.11 ships on
the 3f6c75c pin; shift 6 follows if a later release moves the pin. A forward costs ~3% more
with the runtime adapter, which the cell prices carry (`H3_V13_FORWARD_COST`).

Where it runs (`h3_cell_takes_v13`, `make_job`): Best on the High canvas, Text
and Image, every 5 s-window length. Never with Fast or Turbo, never under a
Steps pin (the pin is the way back to the full sampler), never on Draft,
Standard, Native, the dense pass or the media modes, none of which were
rendered on v1.3. `h3_v13=0` on the API keeps the full sampler.

**The file.** Not hosted by Phosphene (an H3 Model Derivative; MiniMax H3
Community License on the weights, Apache-2.0 on LightX2V's code).
`scripts/fetch_h3_v13.py` (install_h3.js, best-effort; `POST /h3/v13/install`
from the Speed row) downloads the pinned source (sha256 `97b77570…`,
1,383,677,808 B), then builds `lightx2v_v1.3_768p_phosphene.safetensors`
(sha256 `45ee324a…`, 1,956,165,376 B) with the standard library only:

- q/k/v fused into one block-diagonal adapter whose B rows are written in the
  DiT's own per-head order `(heads, 3, head_dim)`. LightX2V trained against the
  contiguous `(3, heads, head_dim)` split (row-norm grouping of the delta), and
  the base checkpoint is per-head (row-norm ANOVA on `qkv_proj`: F 1362 vs 84 on
  block 0). Baking the move in keeps `--lora-qkv-permute` off, so a user LoRA
  stacked after it is applied as trained. Tensor for tensor this equals the
  file the validation renders loaded with `--lora-qkv-permute`.
- `ff.net.0.proj` B halves swapped to `[gate; value]`.
- alpha/rank = 8/128 = 2^-4 folded into B as an exponent shift (exact); a
  value that would leave bf16's normal range is refused, not rounded.

Both digests are checked; a corrupt or partial build is deleted, never
renamed into place. `test_h3_v13.py` pins script↔panel parity, the re-layout
against an independent reference, the job rules, the argv and the prices.

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
The dense 10 s tier ("10s single pass") ships visible by default, always —
`LTX_H3_DENSE_10S` is not read anywhere and restores nothing; see the
constants table above.

### Media modes (4.19): Keyframes, Extend, Lip-sync, Add sound, carried context

All of these ride on the packed sequence the reference already defines —
video conditioning rows held at the keyframe noise level (0.999), audio
conditioning rows held clean, and per-row timesteps — and on runner flags the
panel probes on the INSTALLED runner (`/status.h3.keyframes`, `.continuation`,
`.extend`, `.audio_drive`, `.video_to_audio`). An older pack falls back to LTX
with a sentence (Add sound, which has no LTX lane, is refused with the update
sentence). The panel-side map is `h3_media_plan()` in `mlx_ltx_panel.py`.

| Capability | Runner flags | What the model sees |
|---|---|---|
| Carried context (chained 10 s / 15 s, Extend, Add sound) | `--chain-context-frames 17`, `--seam-colour-frames 24` | the previous window's last 17 frames as history rows placed just BEFORE the target on the rotary clock (one VAE clip, 5 latents), plus their sound as clean audio rows from the same instant; the hand-over frame stays the "first" anchor. Each window's grade is eased onto the carried frame over 24 frames. |
| Start & end frame / Keyframes | `--first-frame`, `--last-frame`, `--keyframe PATH@FRAME` | MiniMax's FL2VA / L2VA tasks; a timed still is a `frame:<i>` anchor at `origin + i·5/3` (the same clock every target frame lives on). Each still also goes to the vision tower as `<Picture n>`, and the official alignment sentence is prepended when the prompt has none. On a chain each still is routed to the window that owns its frame. The start frame is optional on H3. |
| Extend | `--extend-from SRC --extend-frames N` | the source is window 0; new 5 s windows continue from its last 17 frames and their sound. Output = source (on the H3 canvas) + N frames. Sources up to 60 s (the decoded source is held through the render). |
| Lip-sync | `--audio-drive TRACK [--audio-mux ORIGINAL]` | the track is written into the TARGET audio rows and held clean (t = 1) for every step; only the picture is denoised. The delivered audio is the user's file, never the VAE round trip. "Voice only" conditions on the demucs vocal stem and delivers the mix. |
| Add sound | `--video-to-audio SRC` | the clip is written into the TARGET video rows and held clean; only the audio is denoised. The delivery is the source's own video stream (`-c:v copy`) with the new track — no export re-encode, no Face Fix. |
| Fast HD (quality) | the Draft canvas + Fast, then `mode=upscale` (start = source, 1 refine step — the "face kept" recipe) | H3 at 640×384 on the 3-step pass, LTX's x2 Upscale & Face Fix to 1280×768, queued together. |

#### Measured (M4 Max 64 GB, Q8 DiT, 2026-10-07)

Seam = the first frame of a new window. "Jump" = the change across the seam over the
median frame-to-frame change around it (1.0 = invisible). Sound step = RMS 0.25 s after
minus 0.25 s before.

| Chain | Sound step at seams | Colour jump (eased) | Motion jump (eased) | Time |
|---|---|---|---|---|
| High 10 s, one still per window | +5.7 dB | 0.85 | 1.30 | 1008 s |
| High 10 s, carried context | −0.9 dB | 1.81 | 1.14 | 1131 s |
| Standard 15 s ×2 seeds, one still | avg 3.4 dB (worst 7.6) | avg 1.02 | avg 0.93 | 769 s |
| Standard 15 s ×2 seeds, carried | avg 1.3 dB (worst 2.4) | avg 1.20 | avg 1.08 | 862 s |

Without the easing the colour jump is 10–40× on either path — the grade step is a
per-window decode artefact and the easing is what removes it. What carried context buys
is the sound: the level and timbre continue across the seam (spectral similarity at the
second seam 0.85/0.89 → 0.89/0.91, random-pair baseline 0.94). Transcripts were complete
and identical on every chain.

Other modes, High (1024×576) unless noted:

- Keyframes, first + last, 5 s: normalised cross-correlation 1.000 at frame 0 and frame
  123; ArcFace median 0.956 against the reference stills; 545 s.
- Extend, 5 s source + 5 s: seam motion 1.60, colour 1.35, sound −0.8 dB, voice
  similarity 0.997; the dialogue continues; ArcFace 0.927; 606 s.
- Lip-sync, 10 s spoken voice on a still: mouth/audio correlation 0.35 without the
  character LoRA, 0.47 with it (native H3 dialogue renders: −0.24…0.38); ArcFace 0.82 /
  0.83; ~1110–1170 s. A song on its full mix scores −0.08 (the mouth stops after ~2 s) —
  songs need Voice only (the vocal stem).
- Add sound, Draft canvas, a talking clip with its audio removed: the line comes back
  word for word on the lips (correlation 0.71, peak −7.9 dB); video stream byte-identical
  to the source; 187 s for 5 s.

#### Fast HD

Same Bizarro prompt, still and seed, 5 s (124 frames):

| Recipe | Output | Time | ArcFace median (min) |
|---|---|---|---|
| Fast HD: 640×384 Fast + x2 Face Fix, 1 step | 1280×768 | 181 + 158 = 339 s | 0.891 (0.841) |
| 640×384 Fast + x2 Face Fix, 3 steps | 1280×768 | 181 + 357 = 538 s | 0.850 (0.818) |
| High Fast (1024×576) | 1024×576 | 535 s | 0.914 (0.839) |
| High Best (1024×576, 15 forwards) | 1024×576 | 2253 s | 0.922 (0.902) |
| Native Best (1344×768, 8 forwards) | 1344×768 | 2888 s | 0.926 (0.886) |

The 3-step finish redraws the face and costs as much as High Fast, so Fast HD uses the
1-step "face kept" recipe. bf16 DiT on High Fast: 512 s, ArcFace 0.911, peak 42.6 GiB
(Q8: 25.1 GiB) — not adopted. Skipping the 5 smallest-gate DiT blocks (`--skip-blocks
auto:5`): 489 s, ArcFace 0.892 (min 0.814), visible expression change at the same seed —
not adopted; the flag stays in the runner for research only.


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

### Prompt guards (2026-10-19)

`h3_prompt_guards.py`, applied in `run_h3_job_inner` to the text the runner encodes (the
main prompt and every per-window prompt; `p["prompt"]` keeps what was typed, the guards
applied land in `h3_prompt_guards_applied`). H3 has no negative prompt, so each guard is
one in-prompt sentence at the end of the description field, added only when the prompt
does not already ask for the opposite, and never twice:

| Guard | Against | Fires when | Default |
|---|---|---|---|
| `ellipsis` | ComfyUI H3 guide: ellipses hurt intelligibility | `...` / `…` inside `<d>` → `,` or `.` (the only edit to dialogue words) | on |
| `spoken` | MiniMax-H3 #68, spoken lines sung | a `<d>` line and no singing word anywhere outside it (instrumental music does not count); not on Lip-sync | on |
| `hands` | #86, rings invented on bare hands | hands in the description, no ring/jewellery/gloves/watch asked; not on Add sound | on |
| `nouns` | #81, what the dialogue mentions appears | the line names content words the description does not; not on Add sound | **opt-in** |

`h3_prompt_guards=off` sends the prompt verbatim; `all` adds `nouns`. The storyboard
planner's L14 tells the LLM the same: no `...` in `<d>`, and the description names only
what should be seen.

A/B, Draft Fast (640×384, 124 f, TaoMate 3-step), T2V, same seed per pair, unguarded vs
every guard that fires (the review folder's `work/out/ux_*`). Voice measured on
the demucs vocal stem (pyin): a known spoken clip reads voiced 0.20 / held-note 0.08, a
known sung one 0.90 / 0.40.

| Prompt (seed) | Guards | Whisper (raw → guarded) | Vocal: voiced, held-note (raw → guarded) | Picture |
|---|---|---|---|---|
| pier line over a guitar score (5101) | spoken, nouns | word-perfect → word-perfect | 0.40, 0.21 → 0.39, 0.11 | same shot; neither take sung |
| two hands, candlelit table (5102) | hands | — (no line) | — | a ring on her finger in BOTH takes |
| umbrella line with `...` (5103) | ellipsis, spoken, nouns | word-perfect → word-perfect | 0.51, 0.16 → 0.49, 0.13 | no umbrella or train in either; the guarded take adds a red fire extinguisher |

So at one seed each: no guard cost intelligibility, and the guarded voice reads further
from singing; the hands sentence did not stop the ring; the nouns sentence did not
change the absent objects and came with a stray red prop, which is why it is opt-in.
None of the three failures reproduced cleanly on the unguarded side either — a
many-seed run is what would show a rate.

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

**Key layouts.** Four exist in the wild; three load, one is refused:

| Layout | Keys | What happens |
|---|---|---|
| bare | `blocks.N.attn.qkv_proj.lora_A/B.weight` | loads as-is |
| ComfyUI repack | the same, namespaced under `diffusion_model.` | **converted in place** at install time — a safetensors *header* rewrite (the offsets are relative to the tensor buffer, so every tensor byte is untouched), recorded in the sidecar as `lora_layout` / `lora_converted_prefix` |
| kohya / sd-scripts | `lora_down`/`lora_up` + `.alpha` | **converted in place** (2026-09-06) — the character LoRAs people actually train for H3 come out of kohya-style trainers in exactly this shape, and the two things the runner can't read (the naming, the `.alpha` scalar) are both mechanical: `_h3_lora_convert_kohya` renames the keys onto the runner's `lora_A`/`lora_B` and folds alpha/rank into `lora_B`, so strength 1.0 means "as trained" — same as every other file. Recorded in the sidecar as `lora_layout: "kohya"`. |
| diffusers / PEFT | split `to_q`/`to_k`/`to_v`, `ff.net.*` | **refused, by design** |

The diffusers refusal is not laziness. That adapter needs a runtime
`alpha / rank` multiplier that **is not in the file** — LightX2V's is
`8 / 128 = 0.0625`, supplied externally by its own inference script — and
applying it at 1.0 renders coloured noise, not a slightly-off clip. See
`LIGHTX2V_LORA_FIX.md` and `scripts/convert_lightx2v_lora.py` in the engine
repo for the manual path. The panel says exactly this in the error, and
since a diffusers-layout file trained for LTX is the file people most often
try here by mistake, the import UI also tells them it looks like an LTX
LoRA and won't run on H3.

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

- **Modes**: Remix and Character are LTX-pipeline-specific; the picker snaps
  back to LTX with a note. Character submits `mode=t2v` but stacks **LTX**
  LoRAs, and those cannot load on H3 (see LoRAs above, which is about H3's own
  library). Text, Image, Keyframes, Extend, Lip-sync and Add sound all render
  on H3 since 4.19 — see "Media modes" above.
- **LoRA STACKING**: up to four adapters on a stacking runner — see LoRAs above.
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
instead of dying 30 s into a render with an argparse error. (Historical: the
first-frame work has long been published.)

### The engine pin (4.19.0)

The engine an install runs is an exact commit: `H3_PIN_SHA` + `H3_PIN_REF` in
`scripts/pinokio/h3_checkout.sh`, called by both `install_h3.js` (Install,
Repair, "Update Hailuo H3 runner") and `scripts/post_update.sh` (Update, and
the panel's Update now). Through 4.18.x it was the tip of
`codex/h3-engine-v2`, so a push to that branch reached every install with no
release behind it. To move the engine: push the commit to a ref on
`mrbizarro/minimax-h3-mlx` (a new branch or tag — never force-move one), change
both lines in one commit, and ship it in a Phosphene release.
`test_h3_engine_pin.py` pins the behaviour.

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
