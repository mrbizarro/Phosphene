# Phosphene — project state, history, open work

**Public release: `v3.1.1`.** The entire v3.0 line shipped — v3.0.0 (Characters / Voice / Image Studio / A2V, May 23) → v3.0.1 (FFLF crash fix) → v3.0.2 (Boost/Turbo accel restored after a 2-month silent regression) → v3.0.3 (HiDream hidden, #15) → v3.0.4 (CivitAI SSL) → v3.0.5 (A2V kwarg signature shim, #5) → v3.0.6 (deep-review hardening) → v3.0.7 (GPU-race fix + /status version surfacing) → **v3.0.8 (ltx-2-mlx v0.14.8 catch-up — codec-only patches, I2V "mosaic"/Metal-watchdog fix #17, ASCII version-skew handshake fix)** → **v3.0.9 (custom I2V Width×height inputs restored for power users; Train-tab dev-transformer Download "unknown install key" fix — form-encoding, reported by @cocktailpeanut)** → **v3.0.10 (model-integrity self-heal — boot scan + `/status.model_integrity` + a one-click Repair banner for corrupt/partial weights, the leading *non-Metal* "mosaic" garbage-decode cause; safetensors header+size check)** → **v3.0.11 (latent helper-handshake hang fixed: `_read_until` mixed `select()` on the raw fd with a buffered `readline()`, so any event the helper emitted *right before* `ready` — a version-skew line, the new runtime fingerprint — got stranded in the TextIOWrapper buffer and the panel hung 120 s → "helper failed to start: None". This is the true root cause behind the mislabeled "⚠️-emoji" handshake break. Now reads the raw fd via `os.read()` into a carry buffer and drains every complete line before re-`select()`-ing. PLUS: every render log self-documents its runtime — mlx/mlx-metal version + Apple chip + macOS — surfaced in `/status`, the `helper ready ·` line, and a standalone `runtime |` line, the exact data needed to triangulate the remaining MLX-numerical "mosaic")** → **v3.0.12 (the REAL mosaic fix + 3 more. The mosaic is NOT a code bug or the missing-upscaler theory (both disproved by local repro — the bare 6-file Q4 set renders byte-identical-clean on M4 Max); it's stale/corrupt weight *content* — right size, wrong bytes — that v3.0.10's header+size check can't see (confirmed: ronyeoh/#18 + claude3d/#5 fixed it only by a fresh full re-download). Added **deep checksum verify vs upstream**: Settings → Model files → "Verify model files (checksum)" / `/models/verify-deep` hashes every weight + compares to HuggingFace's published SHA-256 (proven to match local), mismatches feed the existing one-click Repair re-download. Validated: flipped 16 bytes mid-file → header+size passed, deep-verify caught it. ALSO in v3.0.12: character-LoRA-shows-as-"style-only" fix (#5 — `list_user_loras` recovers trigger+kind from the `_v2` filename when the sidecar is missing); `cgi`→`email` multipart migration (Python-3.13-ready, removes the boot DeprecationWarning, adopted from @ssfeather's PR #22); dynamic 3–8 multi-keyframe UI (adopted from @youngbee12's PR #20, ported clean with the integrity feature preserved + a JS ReferenceError fixed). Folds in v3.0.11's handshake fix + runtime fingerprint.)** → **v3.0.13 (character LoRA fixes, #5 @claude3d: a multi-word/spaced character filename no longer mislabels as style-only — the id regex gated out spaces before the sidecar was read; and the Character tab no longer silently falls back to plain T2V — a UI desync left the avatar's selection ring lit after `character_id` was cleared. Plus an `enhancePrompt` element-id fix. Cherry-picked to prod `ce03139`, release `814dc2d`.)** → **v3.1.0 (Ideogram 4 — open-weight 9.3B text-rendering image model + a visual text-placement canvas that places exact text via normalized-bbox captions. mflux 0.18 `mflux-generate-ideogram4`; gated `ideogram-ai/ideogram-4-fp8` ~26 GB, needs a HF Read token + license. Fixed the gated-download 401 that would have hit EVERY user — the panel now exports its configured token to subprocess env via `_sync_hf_token_to_env` so a stale `hf auth login` cache can't shadow it; proven clean-room. Validated end-to-end: 8-text-element field-guide poster rendered clean, every label placed per its bbox.)** → **v3.1.1 (Ideogram output-card buttons hotfix, reported by Mr Bizarro: an Ideogram image's prompt is a caption JSON, which broke every output surface that fed it into a plain box / inline onclick — the Recent-list Animate button was worked-but-dead because apostrophes/&/< in the caption terminated the single-quoted onclick. Display-layer fix: `_displayPromptFor` derives `high_level_description`, `escapeHtml` the onclick args, friendly `_imgEngineLabel`, and `loadParams` restores an Ideogram image into the visual canvas instead of dumping JSON. `params.prompt` stays the caption JSON; renders unaffected. Validated live.)** All validated end-to-end on the dev panel (deep-verify corrupt-detect + repair, /loras, byte-identical upload, multi-keyframe render, FFLF regression). See §4 for the full version history. The May-17 Codex C+ UI restructure (capability tiers, Q4 surface, Character as 5th mode pill, accel kill, HQ-speed move) and the Train-tab / LoRA-chrome work that followed are all baseline now, folded into v3.0.x. There is no `v2.0.6` tag.

Current `dev` head: see `git log -1` for the live SHA. `dev` tracks `beta/main` (private repo — see §1).

> **Latest dev (2026-06-11, on `beta/main`, NOT yet public):**
> - **LoRA/character fixes — SHIPPED PUBLIC as `v3.0.13`** (beta `f4f992e` → cherry-picked to prod `ce03139`, release commit `814dc2d`, tag `v3.0.13`, 2026-06-11). Two bugs @claude3d reported on v3.0.12, fixed + validated live in the dev panel: (1) a character LoRA whose filename contains a **space** (e.g. `Annie Phosphene_v2.safetensors`) was misclassified as style-only / "No trained characters yet" even with a correct sidecar — `_CHARACTERS_ID_RE` rejected spaces and gated `list_characters()` *before* the sidecar was read. Widened the regex to `[A-Za-z0-9 _-]+` and made `_character_safe_id` URL-decode so spaced ids resolve in the `/characters/<id>/{preview,generate,delete,rename}` routes (unquote-then-validate stays traversal-safe). (2) Character tab could silently render plain T2V — a **UI desync** left the avatar's `.active` ring ON after the hidden `character_id` was cleared on a mode switch, so Generate shipped `character_id=""` → no LoRA. ("Params shows Text" is by design — `character_id` drives the LoRA stack, not the mode field.) The ring now re-renders in lockstep with the cleared selection. Plus a latent `enhancePrompt` wrong-element-id fix. **Deliberately NOT bundled with the Ideogram work below** so the fixes can ship without waiting on the gate. Validation: `/characters` lists a spaced-name char; in-browser pick→switch-away→switch-back leaves the ring OFF + field empty, re-pick ships `character_id`.
> - **Ideogram 4 engine + visual text-placement canvas — SHIPPED PUBLIC as `v3.1.0`** (2026-06-11). Open-weight 9.3B text-rendering model + a client-side bbox text-placement canvas. mflux 0.18 `mflux-generate-ideogram4`; gated `ideogram-ai/ideogram-4-fp8` (~26 GB; user needs a HF **Read** token + accept the license). **Root-caused + fixed the gated-download 401 that would have hit every user:** the image-engine mflux subprocess inherits `os.environ`, but the panel never exported its configured token there, so huggingface_hub fell back to a stale `hf auth login` cache (a different/unauthorized account) → 401. `_sync_hf_token_to_env()` now pushes the settings token → `HF_TOKEN` env (wins over the cache) at boot + per image job. Proven clean-room (fresh empty env: old path 401s, fix authenticates). Validated end-to-end: an 8-text-element field-guide poster rendered clean with every label placed per its bbox. Gate-error message reworded to name a wrong-account token instead of telling users to re-accept the license. Memory: `phosphene_hf_ideogram_account` + `feedback_fixes_must_reach_users`.
> - **Mosaic (Q4 on certain Apple GPUs) — confirmed an upstream engine bug.** dgrauet ([ltx-2-mlx#40](https://github.com/dgrauet/ltx-2-mlx/issues/40)) traced it to the **MLX 4-bit Metal kernel on specific GPU sub-families** — NOT monotonic by chip (his M2 Pro is clean; an M4 Max reporter, shdwmacca, mosaics). He gave a 30-sec `mlx_q4_check.py` repro (saved `/tmp/mlx_q4_check.py`); our M4 Max is clean (`applegpu_g16s`, all rows <1%). **Still need the script output from an affected box** (poppy0396 M3 Ultra / elbarto M3 Max / rathore M2) — relayed to GitHub #23 + #40; **still to relay on the Pinokio @rathore thread (Mr Bizarro pastes — Mac browser isn't logged into Pinokio).** Workaround: render High/Q8. Detail: memory `phosphene_ltx_pin_v0148.md`.
> - **8 AM triage cron fixed** — `fetch_morning_brief.sh` had been dead since **May 31** (exit 127: `gh` is in `~/bin`, off the launchd PATH). Fixed + verified in the PM hub (`~/AI/projects/phosphene/notes/triage/`, outside this repo). Closed GitHub #18 (ronyeoh) + #19 (shakeworks).

Live URL: `https://github.com/mrbizarro/phosphene` · Linear project: `https://linear.app/hairstylemojo/project/phosphene-9c11240704bb`

This doc is the **session-start handoff**. A new Claude window entering this project should read this first, then `CLAUDE.md` (architecture), then the relevant Linear issues.

> **Authoritative engineering snapshot:** `/Users/salo/AI/projects/phosphene/notes/DEEP_REVIEW_2026-05.md` (2026-05-31) — the deep stabilization review: risk register + phased stabilization plan. It is the source of truth for current bug state and supersedes the historical log in §7 below.

---

## 1. Where the code lives

**Repo split (2026-05-22):** there are now two GitHub repos:

- **Public `mrbizarro/phosphene`** — `main`-only, stable releases. The public `dev` branch was **DELETED**. Anyone with a hand-configured public-`dev` install must reinstall from `main`.
- **Private `mrbizarro/phosphene-beta`** — daily development. Holds `main` (daily dev) plus `archive/*` experimental branches.

Two clones on Mr Bizarro's Mac, both managed by Pinokio:

| Path | Branch tracked | Port | Role |
|---|---|---|---|
| `~/pinokio/api/phosphene-dev.git/` | local `dev` → `beta/main` (private) | 8199 | Active development. Most edits land here first. |
| `~/pinokio/api/phosphene.git/` | `main` → public `origin` | 8198 | Production / daily driver. Mr Bizarro's actual usage. |

The local branch is still named `dev`, but it tracks **`beta/main`** (private), NOT the deleted public `dev`. `update.js` auto-detects upstream (`@{upstream}`), so the dev clone pulls from `beta` and the prod clone pulls from public `main`.

GitHub is the source of truth (memory: `feedback_github_source_of_truth.md`). Branch policy is strict:

- Push daily work to **`beta`**, never to a public `dev` (it no longer exists).
- **Promotion to PUBLIC `main` is the gated step — NEVER push public `main` without Mr Bizarro's explicit OK** (memory: `phosphene_dev_workflow.md`).

State directories that live OUTSIDE the repo via Pinokio's `fs.link`:

- `mlx_models/` → ~63 GB of LTX 2.3 weights (Q4, Q8, Gemma encoder, PiperSR upscaler). Shared between dev and prod via symlink chain.
- `mlx_outputs/` → all rendered mp4s + sidecar JSON files.
- `panel_uploads/` → user-uploaded reference images for I2V / FFLF.
- `state/` → `panel_settings.json`, `panel_queue.json`, `panel_hidden.json`. Survives a Pinokio Reset.

A Pinokio Reset wipes the install dir but preserves all four — Mr Bizarro can Reset → Install without losing renders or settings.

## 2. Current capabilities (shipped in v3.0.6)

The May-17 Codex C+ items (Studio/Train tabs, Character 5th mode, capability tiers) are baseline now — they shipped as part of the v3.0 line.

**Workflow tabs (top nav)**
- Manual — video composer (T2V / Character / I2V / FFLF / Extend)
- Studio — image generation (was a mode chip inside Manual until 2026-05-17, commit `37c9d21`)
- Train Character — dataset → LoRA training, with Gemma 3 auto-caption + letterbox crop

**Modes (inside Manual)**
- Text — pure text→video
- Character (2026-05-17 commit `e420e3a`) — first-class mode for trained character LoRAs. Submits `mode=t2v` server-side; backend dispatches on `character_id`. Auto-stacks face + audio LoRAs, swaps the quality strip to Q8-only.
- Image — image→video (I2V)
- FFLF — first/last frame keyframe interpolation
- Extend — append seconds onto an existing clip

**Capability tier system (2026-05-17 commit `64dad87`)**
- `body[data-cap-tier="q4|q8"]` set at request time from `SYSTEM_CAPS.allows_q8`.
- `q4` (sub-48GB Macs): FFLF / Extend / Character mode pills hidden; chip strip hidden; Q8-Draft/Q8-Pro chips hidden; "High" chip in default strip hidden; skip-step toggle hidden. Manual collapses cleanly to Text/Image.
- `q8` (48GB+): full surface, Q4 still reachable via the default Quality strip for plain T2V/I2V.
- `LTX_FORCE_CAP_TIER=q4` env override lets a Q8 dev machine view the Q4 surface for testing.

**Quality dial** (mode-aware)
- Non-character T2V / I2V: `Quick · Balanced · Standard · High`. Quick / Balanced / Standard route to Q4 distilled; High routes to Q8 two-stage HQ + TeaCache.
- Character mode: 2-chip strip `Q8 Draft (736×416) · Q8 Pro (1024×576)`. Both submit `quality=high`. Default strip is hidden — character LoRAs can't fuse into Q4 distilled (mismatched sigma schedule produces identity-mushed output). Backend REJECTS `character_id + quality != high` with a 400 (commit `8b5a3cf`).
- Extend mode: 2 pills `Q8 Draft (12 steps · 64 GB safe) · Q8 Pro (30 steps · 96+ GB)`. Same labels as Character for vocabulary consistency; mechanism is the Extend-specific sampler (extend_steps + extend_cfg).

**HQ speed dial** (Customize accordion, visible only when quality=high)
- Fast — TeaCache + skip-step, ~12% faster on Q8 HQ (validated 2026-05-15 Codex contact sheet, ~426s → ~372s on a 7s 1024×576 clip).
- Exact — TeaCache only, reference quality (use if a specific LoRA / prompt looks degraded under Fast).
- The legacy Q4-distilled-only `Boost / Turbo` accel pill row was killed from the public surface (commit `e8a7f75`); the hidden `#accel` input survives for sidecar restore compat.

**Sharp upscale**
- PiperSR on the Apple Neural Engine, optional install via `install_sharp.js`

**Joint audio + video**
- Synced lip movement, footsteps, ambient bed (mlx 0.31.1 pin holds the audio fix)

**Hardware tier system**
- Compact / Comfortable / Roomy / Studio with per-tier feature gating
- Reference benchmarks throughout this doc are on **M4 Max 64 GB** (Comfortable tier)

**Other**
- CivitAI LoRA browser built-in
- LoRA picker per-row chrome: rename (sidecar-only, on-disk filename preserved), download (Content-Disposition attachment, streamed in 1 MiB chunks), companion-aware delete (also trashes the upscaled `_720p.mp4` + sidecar). Commit `0dba2dc`.
- Per-job progress bar (phase-aware, denoise-step-aware)
- Gallery with cache-bust URLs, no more black-clip race
- 80+ GB less disk than pre-Y1.024 installs (filtered hf downloads)
- Spicy mode gate (NSFW LoRAs hidden by default, opt-in toggle in Settings)

**Player + Expand lightbox (2026-05-17 commit `4987022`)**
- Player surface reads media natural dimensions into a `--media-aspect` CSS custom property on `loadedmetadata`; vertical clips letterbox correctly instead of being head-to-toe cropped by the prior hardcoded 16:9 + object-fit:cover.
- Expand button is now a real fullscreen modal (was inline-positioned and dumped the `<video>` at native dims inline).
- Aspect picker promoted out of Customize into a compact "Orientation" pill row under Quality.

**Train Character workflow (significant 2026-05-17 additions)**
- Gemma 3 auto-caption — one-click `[VISUAL]: <trigger>, <description>` per-image captioning via local `mlx-community/gemma-3-12b-it-4bit` weights (the same Gemma the prompt enhancer already downloads). New `caption_with_gemma.py` subprocess via `mlx-vlm==0.4.4` (pinned with `--no-deps` to avoid upgrading mlx-lm beyond 0.31.1). `POST /train/auto-caption`, `GET /train/auto-caption/status`. End-to-end verified at 87s for a 37-image dataset. Commit `e839bc2`.
- Letterbox crop strategy — pill row under the Quality preset. Center crop = scale-and-center-crop to square (legacy default, best for tight portraits). Letterbox = scale longer dim to target + pad shorter dim with black bars (preserves wide-shot proportions — addresses "blurry medium-long shots" issue when training on portrait-only crops). Trainer canvas stays a fixed square so the dataloader is unchanged. Commit `7a46b96`.
- Voice (audio LoRA) toggle defaults ON if a voice clip is uploaded. Commit `ea2cf02`.
- `/stop` button actually kills the training subprocess now (was a known no-op; trainer survived for hours after Stop, blocking the queue). `start_new_session=True` on both face + audio trainer Popens + SIGTERM via killpg with 8s SIGKILL fallback. Commit `b6d1222`.
- Vendored `lora_lab/` into the panel — installer-only users get training out of the box. `LTX_LORA_LAB_ROOT` env var still lets a dev iterate against `~/AI/projects/lora-lab/`. Commit `e9ce853`.

**Server-side validation (2026-05-17 commit `8b5a3cf`)**
- `_validate_character_quality(form)` runs on `/run`, `/queue/add`, `/queue/batch`. Refuses any submission with `character_id` set and `quality != "high"` with a descriptive 400 — defense-in-depth so a stale form or scripted call can't ship the broken Q4+character combination.

**Speed dial (legacy, killed 2026-05-17)**
- The pre-2026-05-17 `Exact · Boost · Turbo` accel pill row only ever fired on the Q4 distilled path; the HQ pipeline ignored it. Killed from public surface in pass 5 (commit `e8a7f75`) — the hidden `#accel` input survives so saved-state restore paths keep working. If a future Q4 lab tool needs the row back, restore it behind `body[data-cap-tier="q4"]` and wire it explicitly to the Q4 path.

**Sharp upscale**
- PiperSR on the Apple Neural Engine, optional install via `install_sharp.js`

**Joint audio + video**
- Synced lip movement, footsteps, ambient bed (mlx 0.31.1 pin holds the audio fix)

**Hardware tier system**
- Compact / Comfortable / Roomy / Studio with per-tier feature gating
- Reference benchmarks throughout this doc are on **M4 Max 64 GB** (Comfortable tier)

**Other**
- CivitAI LoRA browser built-in
- Spicy mode gate (NSFW LoRAs hidden by default, opt-in toggle in Settings)
- Per-job progress bar (phase-aware, denoise-step-aware)
- Gallery with cache-bust URLs, no more black-clip race
- 80+ GB less disk than pre-Y1.024 installs (filtered hf downloads)

**Agentic Flows (v2.0.5+, May 6–7 2026)**
- Engine kinds: `phosphene_local` (mlx-lm), `ollama`, `custom` (any
  OpenAI-compat), `anthropic` (Messages API, native preset)
- Two operating modes: `plan_sleep` (default — engine auto-stops after
  agent's `finish` call so RAM goes back to LTX renderer) /
  `interactive` (engine stays resident)
- Sessions sidebar (Cmd+K) with pinned/preview/rename/delete + auto
  search across titles
- "Queue them" batch bar above composer for explicit user-driven batch
- Multi-take per shot (`generate_shot_images append:true` adds Take
  N+1 below previous)
- Anchor pick / un-pick (re-click toggles), per-grid pick-state badge
- Project notes file (`state/agent_project_notes.md`) +
  `read_project_notes` / `append_project_notes` tools
- read_document tool (txt/md inline; PDF if pypdf installed)
- Image-engine plumbing: mock / mflux / bfl backends
- RAM headroom chip in agent header — green/amber/red based on free
  GB vs configured chat model size
- Memory-pressure guard: refuses to auto-spawn local engine when system
  is in swap or > 92% pressure
- Reasoning-model handling — `engine.chat()` reads `message.reasoning`
  separately from `message.content`; falls back when content is empty,
  raises informative error on length truncation
- Default `max_tokens` 8192 (was 3072 — too small for Qwen 3.6 / R1
  thinking budgets)
- Scroll-pinning + "↓ New messages" pill (no more auto-scroll yank)
- Stop button on long turns; abort via AbortController
- Offline banner (Phosphene-branded, pulsing) when /status fails
  twice in a row
- Phosphene-branded assistant avatar (favicon glyph, not "C")
- Live phasing on typing indicator: "Calling submit_shot · 12s",
  "Queued ce5c, planning next", etc.
- One-click "Stop engine" button (frees ~22 GB without going to
  Settings)
- Plan/Interactive mode pill toggle in agent header

**Image Studio (Klein/FLUX retired 2026-05-13; HiDream hidden since v3.0.3)**
- **Shipped default: Qwen-Image-Edit-2509** with a Lightning 4-step fast tier. Three tiers:
  - **Qwen-Image-Edit-2509** — Fast (Lightning, 4-step Q6 + FBCache,
    ~1:20 / image, multi-ref), Medium (8-step Q6 + FBCache, ~2:05 /
    image, multi-ref), Quality (40-step Q8 + CFG 4.0 + FBCache, ~3:50
    / image, multi-ref). (The `2511` string in the codebase is the
    Lightning **LoRA** path `lightx2v/Qwen-Image-Edit-2511-Lightning`,
    not the base model.)
- **HiDream-O1-Image-Dev is HIDDEN** since v3.0.3 (issue #15 — held
  pending its lab repo going public). The code is present and still
  reachable via a saved config or `engine_override`, just not in the
  visible dropdown. When exposed it offers Fast (3-step, ~3:45 / 4-img
  batch), Medium (6-step + FBCache, ~6 min, character-preserving),
  Quality (12-step + light FBCache, ~9 min, best detail), and lives in
  a separate one-time clone (`HIDREAM_LAB_DIR` env var or
  `~/HIDREAM-O1-MLX-LAB-active`); resolved in `image_engine.py`.
- Q6 default quantization for mflux Qwen tiers — Apple-Silicon
  community sweet spot, ~4-6% quality loss vs full precision (Q4 was
  8-12%), per-image speed gap negligible on M4 Max. Q8 reserved for
  the Quality tier.
- Image jobs go through the same queue worker as video — they appear
  in Now / Queue / Recent / Logs alongside video jobs (was: synchronous
  HTTP, invisible in panel).
- Right-pane viewer is mode-aware: `<img>` tag for image mode,
  `<video>` for video. Carousel thumbnails ditto.
- OUTPUTS gallery filter chips: All / Videos / Photos. Auto-flips
  on mode change (Photos on `setMode('image')`, Videos on
  t2v/i2v/keyframe/extend); manual clicks persist.
- Animate button on photo cells → pre-fills the i2v form (mode, image
  picker, prompt) so the user can tweak before clicking Generate.
  Pre-fill, not auto-submit.
- Pre-flight RAM/disk check rejects oversized jobs before mflux
  launches (24 GB Qwen-Edit on a Mac with 8 GB free was silently
  SIGABRTing mid-Metal). Lookup table per (family, quantize); compares
  to vm_stat free GB. `PHOSPHENE_SKIP_PREFLIGHT=1` escape hatch.
- Report-bug button (neon-pulsing icon in header pill row) opens a
  modal pre-filled with sysinfo + git branch/sha + sw_vers + last 50
  log lines, generates a github.com/.../issues/new link with
  labels=bug, optionally bundles the latest 5 .ips crash files into
  /tmp/phosphene-bug-TS.zip.
- FLUX / Klein-Edit / Klein-Base-Edit were retired 2026-05-13 — the
  flux2_edit family stopped being competitive with Qwen-Edit at the
  same step counts, and dropping it freed UI vocabulary for the Qwen
  Fast/Medium/Quality tiers above. (HiDream was later hidden in v3.0.3,
  so the visible dropdown today is Qwen-only.)

**Frontend extraction (parked on `archive/frontend-extraction`, private beta)**
- `webapp/` directory: index.html, style/all.css, js/main.js,
  vendor/marked.min.js + dompurify.min.js (MIT/Apache licenses)
- Panel slimmed from 16,223 → 5,866 lines (-10,357)
- New `/webapp/*` static route, `/api/page-config` endpoint
- Markdown rendering swapped to `marked.parse + DOMPurify.sanitize`
- Validated end-to-end on port 8210. Post-split this is an `archive/*`
  experimental branch on private `phosphene-beta`, not a merge-to-`dev`
  candidate. Note: a separate `cuda-port` branch (Phase 0 spike only)
  also lives on private beta — **not shipped, not on the Mac product.**

## 3. Marquee benchmarks (M4 Max 64 GB, sidecar-measured)

| Recipe | 5 sec | 10 sec | 20 sec |
|---|---|---|---|
| T2V Balanced + Turbo + 720p Sharp | 3:30 | 8:07 | 21:38 |
| T2V Quick + Turbo + 720p Sharp | — | — | 10:32 |
| T2V Standard 1280×704 Exact | 7:40 | — | — |
| T2V Standard Turbo | 5:26 | — | — |
| T2V High Q8 (max quality, no Sharp) | 11:51 | — | — |
| I2V Balanced + Turbo + Sharp | 3:37 | 8:26 | — |
| Extend +3 s on Q8 dev (768 px clamp) | — | 15:50 | — |
| FFLF (clamped 768×416, Comfortable tier) | — | 5:29 | — |

Per-step cost scales **~T^1.5** with frame count (218 s/step at 481f vs ~30 s/step at 121f, same width). Sub-quadratic — confirms LTX uses windowed/factorized attention. **20-second single clips are production-viable**; 30 sec at 1024×576 is plausible, 60 sec needs lower res or research breakthrough.

## 4. Version history (compressed)

Pre-2.0 was the `Y1.NNN` sequential counter. v2.0.0 cut over to semver on May 3 2026.

**Y1.001 → Y1.013** (Apr 28–30) — First usable T2V/I2V renders. Audio SHIP-BLOCKER fixed by pinning `mlx==0.31.1` (0.31.2 attenuated vocoder by 22 dB).

**Y1.014 → Y1.024** — Hardware-tier system, Boost/Turbo speed modes (adaptive denoise caching), CivitAI LoRA browser, Q8 two-stage HQ tier, FFLF + Extend modes, `hf_transfer` downloads, Q4/Q8 download filter (saved ~80 GB on existing installs via `update.js` trim).

**Y1.025 → Y1.035** (Codex-led arc) — Sharp upscale via PiperSR (Apple Neural Engine), I2V tail-stall fix (`Y1.034` free DiT before VAE decode), VAE temporal-streaming for long clips (`Y1.035`), license / install hardening, Spicy mode gate prep.

**Y1.036 → Y1.039** — Fixed `Y1.024` Extend regression (route to Q8 dev transformer), VAE auto-streaming threshold (recovered ~7 % on short clips), Now-card progress bar rewrite (phase + denoise-step aware), gallery black-frame race fix.

**v2.0.0** (May 3) — Marquee release. 2.0 badge in panel header, semver versioning starts.
**v2.0.1** — Spicy mode toggle gates NSFW LoRA visibility.
**v2.0.2** — Install fails loud when pipeline packages are missing (sanity-import step in `install.js`).
**v2.0.3** — Install log self-documents Python toolchain (uv version, system python presence, post-pip site-packages list).
**v2.0.4** (May 5) — Strip em-dash from install.js sanity check. Was breaking install on some Pinokio shells (KTDS + second user hit identical SyntaxError). Pure ASCII now.
**v2.0.5** (May 6) — Drop the `print('venv OK: ...')` decoration from the sanity-import step. KTDS reproduced the SyntaxError on v2.0.4 — turns out something in their environment (Pinokio's command preprocessor or a user-side rewriter) was cutting the literal `OK:` out of the Python string AND appending `OK` after the closing shell quote, so Python received `...importable')OK` and bailed. Removing the print sidesteps the rewriter entirely. The exit code from a successful `import` is the only success signal `shell.run` needs anyway.

**"v2.0.6" dev codename** (May 8 2026; shipped as part of v3.0.0) — Image Studio overhaul + agent quality pass + security review. Headline ships:
- Image jobs flow through the unified queue worker (Now / Queue / Recent / Logs); the in-Studio gallery is gone, the unified Recent tab covers it.
- Mode-aware right-pane viewer + OUTPUTS gallery (All / Videos / Photos chips, auto-flip on mode change, "Animate" button on photo cells pre-fills i2v).
- Q6 default quantization for mflux + non-distilled photoreal presets (`flux2_edit_high`, `qwen_edit_high`, `kontext_high`); klein-4B prompt structure taught to the agent (subject → environment → style → technical hierarchy). *(This session bumped the Qwen-Edit default toward 2511, but the **shipped base model is 2509** — see §2; `2511` is the Lightning LoRA path only.)*
- `submit_shots` plural tool — agent batches a multi-shot plan in one dispatch + finishes the turn before auto-pause kills the engine (used to crash mid-batch).
- Phase C i2v prompt-writing rules in `prompts.py` (forbid still-prompt reuse, require explicit motion beats, ~1 beat / 2-3 sec); production recipe taught as Balanced + Sharp 720p.
- Pre-flight RAM/disk check + Metal/MLX SIGABRT detection with actionable OOM hint (no more silent exit -6).
- Report-bug button — neon-pulsing icon, opens pre-filled GitHub issue with sysinfo + git sha + last 50 log lines + optional .ips crash bundle.
- Manual mode genuinely hides the AF pane (was a `display:flex` vs `[hidden]` no-op).
- Agent header strip rebuilt; Outputs photo/video filter wired from `setMode`.
- Composer in Image Studio restyled to match the video form's polish.
- Agent now switches engines via `engine_override` arg on `generate_shot_images`.
- Security review pass: 0 CRITICAL, 4 HIGH, 6 MEDIUM identified — all 10 shipped this session. (See Known bugs section for details.)

> Note: "v2.0.6" was the dev codename for the work that ultimately shipped as **v3.0.0**. There is **no `v2.0.6` tag** — the published 2.x tags stop at v2.0.5.

**v3.0.0** (May 23 2026) — Marquee release. Folded in the full Characters workflow (Train Character tab + first-class Character mode), Voice/audio LoRAs, the standalone Image Studio (Qwen-Image-Edit-2509 default + Lightning 4-step), A2V, the Codex C+ capability-tier UI restructure, and the Image Studio overhaul above. The in-panel agentic-flows chat was retired in this release.
**v3.0.1** — FFLF crash fix.
**v3.0.2** — Boost/Turbo accel restored after a 2-month silent regression (git-archaeology'd the per-mode tail + re-anchored the accel patch across all `denoise_loop` import sites).
**v3.0.3** — HiDream hidden from the visible dropdown until its lab repo is public (issue #15). Code stays, reachable via saved config / `engine_override`.
**v3.0.4** — CivitAI SSL fix.
**v3.0.5** — A2V `frame_rate` kwarg signature shim (issue #5).
**v3.0.6** — Deep-review hardening: CivitAI token-leak fix, dead-HDR revival (`HELPER_LOW_MEMORY` → `LOW_MEMORY` NameError), GPU-contention guard (inline image vs in-flight video render), boot-time version-gate against the ltx-2-mlx pin.

**Published tags (verified `git tag`): `v2.0.0`–`v2.0.5` and `v3.0.0`–`v3.0.6`.** Current public release is **v3.0.6**. (The `VERSION` file in-repo may lag the tag — read the tag, not the file, for "what release is this.")

## 5. The folder layout

```
phosphene-dev.git/
├── pinokio.js / pinokio.json          ← Pinokio menu logic + manifest
├── install.js / update.js             ← idempotent install / update flows
├── install_sharp.js                   ← optional PiperSR Sharp installer
├── download_q8.js                     ← optional Q8 weights download
├── download_upscaler.js               ← optional spatial upscaler download
├── start.js                           ← Pinokio start script (launches the panel)
├── reset.js                           ← Pinokio reset script
├── recover.sh                         ← rare-case manual recovery
│
├── mlx_ltx_panel.py                   ← the panel HTTP server (~9000 lines, single file)
│   ├── /status, /queue/*, /run, /upload, /file, /civitai/*, /loras, /settings ...
│   ├── HTML+CSS+JS for the UI all inlined as page() string
│   └── Worker thread + helper subprocess management
│
├── mlx_warm_helper.py                 ← persistent helper subprocess (~1300 lines)
│   ├── Loads + holds T2V/I2V/Extend/HQ/Keyframe pipelines from ltx_pipelines_mlx
│   ├── Reads job specs from stdin, emits events to stdout
│   └── action types: generate / generate_keyframe / extend
│
├── patch_ltx_codec.py                 ← idempotent runtime patches against installed
│   ├── Patch 1: codec → yuv444p crf 0 + faststart (lossless H.264)
│   ├── Patch 2: I2V free DiT before decode (matches T2V cleanup)
│   ├── Patch 3: free vae_encoder pre-denoise (peanut review)
│   ├── Patch 4: free feature_extractor in base load() (peanut review)
│   └── Patch 5: VAE temporal streaming decode (long clips no longer freeze)
│
├── required_files.json                ← single source of truth for "installed"
├── VERSION                            ← read by panel + version-check loop
├── .env.local                         ← LINEAR_API_KEY (gitignored, chmod 600)
│
├── README.md                          ← user-facing docs (homepage on GitHub)
├── CLAUDE.md / AGENTS.md / GEMINI.md / QWEN.md
│   ← agent manuals (architecture, conventions, history)
├── docs/                              ← long-form internal docs
│   ├── STATE.md                       ← this file
│   └── SDK_KEYFRAME_INTERPOLATION.md  ← multi-keyframe interpolation design + plan
├── launch/                            ← marketing copy (Pinokio article, X thread, Reddit, etc.)
│
├── ltx-2-mlx/                         ← upstream MLX port, PINNED v0.14.0 (clone of dgrauet/ltx-2-mlx; SHA b35254a)
│   └── env/                           ← Python 3.11 venv (uv-managed)
├── mlx_models/                        ← weights (~63 GB, fs.link symlink)
├── mlx_outputs/                       ← rendered mp4s + sidecars (fs.link symlink)
├── panel_uploads/                     ← user reference images (fs.link symlink)
├── state/                             ← panel_settings/queue/hidden.json (fs.link symlink)
├── cache/                             ← HF_HOME for downloads
└── logs/                              ← Pinokio's own command-execution logs
```

`mlx_ltx_panel.py` is the heart of it — almost all panel behavior lives there. `mlx_warm_helper.py` is the long-running inference subprocess. `patch_ltx_codec.py` is a runtime modifier that fixes upstream code without forking it.

> The vendored `ltx-2-mlx` is pinned at **v0.14.0** (SHA `b35254a`). The deep review (`notes/DEEP_REVIEW_2026-05.md`) flags the **runtime-monkey-patch + version-skew axis as the single top fragility** — the panel/helper patch a moving upstream at runtime, and (pre-Phase-0) nothing asserted the imported `ltx_pipelines_mlx` was actually v0.14.0. The stabilization plan there (Phase 0 loud version gate, Phase 3 retire the runtime-patch class) addresses it.

## 6. What worked / didn't this session (May 3–5 2026)

### Cinematic capability findings (from rendering ~30 clips)

**The model's wheelhouse**
- Human cinematic moments. Faces at medium and tighter, body language, atmospheric scenes.
- Static or near-static camera works better than moving camera.
- 2–3 dialogue turns per clip work cleanly when prompt follows LTX's docs literally:
    - Single continuous paragraph (NOT uppercase character cards)
    - Voice descriptor on every speech beat (not just first)
    - Single quotes around dialogue
    - Action density ~1 explicit beat per 2–3 sec of clip
- Joint audio + video really IS jointly diffused — lip-sync is uncannily tight.

**The model's weaknesses (avoid in prompts)**
- **Hands and held objects** — fingers morph, written text squiggles, pen/needle/cup interactions look off.
- **High-motion physics** — skater kickflips, water splash, motorcycle blur are out of distribution.
- **Faces below ~80 px in-frame size** — model fills a face-shape but identity-broken. Wide shots of single characters are unusable in their first/last seconds. ([Mr Bizarro's discovery May 4](#))
- **Multi-shot continuity is naive-failure** — same prompt + different seed = different person. The mom-kid scene experiment (M1 / M2 / M3 in `mlx_outputs/`) confirmed three different women across three angles despite identical character description.

### What earns 20 seconds
- 6–9 explicit beats described in the prompt. Anything less and the model fills with stasis.
- Static or near-static camera. Camera motion costs visual coherence.
- Specific named actions ("she turns slowly", "she breathes out", "the streetlight flickers off") give anchor points.

### Empirical experiment outcomes

- **M1/M2/M3 mom-kid trio** (1024×576, Balanced + Turbo + Sharp, ~21 min each): demonstrated multi-shot character drift problem. Three different women across three angles.
- **N1–N10 cinematographic moments** (May 4): ten 20-sec clips at varying shot scales. Tested medium / wide / two-shot composition with body-language-only prompts (no hands, no held objects). Output quality varied; faces are stable when in the safe pixel range.
- **E-DRAFT** (May 4): tested low-res draft → high-res commit hypothesis. Same prompt + seed at 640×480 vs 1024×576. Mr Bizarro: low-res output not usable due to face-distance issue. Premise was flawed because lower res = worse faces.
- **E-ANCHOR** (May 4): I2V from M1 frame to test character anchoring. Result was inconclusive in the session; final clip is at `mlx_outputs/` if needed for review.
- **20-sec single-clip viability** (May 4): confirmed at Balanced 1024×576 + Turbo + Sharp. ~21 min wall, audio synced, characters stable.

## 7. Known bugs

> **Source of truth for bug state: `/Users/salo/AI/projects/phosphene/notes/DEEP_REVIEW_2026-05.md`** (the deep stabilization review — full verified risk register + phased plan). The list below is the short reconciliation; the review has the complete severity-ranked register and the fix directions. The CHANGELOG further down is the historical record of what was fixed when — not a list of live bugs.

### Currently open

Reconciled against the deep review (2026-05-31). Everything that was a recent fire — the I2V/Extend post-decode hang (`94bd696`), FFLF crash (v3.0.1), Boost/Turbo regression (v3.0.2), A2V `frame_rate` kwarg (v3.0.5), the HDR NameError / CivitAI token leak / GPU contention (v3.0.6) — is **fixed**. The HiDream no-deadline reader now has a panel-side watchdog too (v3.0.6). What genuinely remains:

**Root-cause fragility (deep review §2, top of the register)**
- **Runtime-monkey-patch / version-skew axis** — the panel + helper patch a moving upstream (`ltx-2-mlx`) at runtime. v3.0.6 added a boot-time version gate (Phase 0); the structural fix (retire the runtime-patch class, re-pin to a SHA-pinned submodule) is Phase 3 of the plan, not yet done.
- **No test seam** — `mlx_ltx_panel.py` is ~28k lines, all 69 routes dispatched via a flat `if path==` chain with zero unit-testable surface. This is *why* regressions reach users. Phase 2 carves the first seams.

**Confirmed correctness / robustness items still open**
- **Recipe-override guardrails** — advanced LoRA-training overrides (rank/lr/steps) bypass the validated-recipe clamps (`train_character.py:135-144`, panel `:4807-4821`). Needs whitelist + a "non-standard recipe" warning.
- **`/status` polling cost** — every poll, per open tab, does a pgrep subprocess + filesystem scans (`mlx_ltx_panel.py:7845-7919`). Split fast fields from slow install-state probes / cache the slow group.
- **154 silent `except: pass` sites**, including persistence + chmod paths (`mlx_ltx_panel.py`). Tier them (best-effort vs state/security vs control-flow) behind a greppable `_swallow(label)` helper.
- Plus a tail of Med/Low items in the review (HiDream preflight exemption, `/prompt/enhance` shares the render lock, stats JSONL unbounded/growth, `character_runtime.py` dead/divergent, BFL URL host-validation, voice-silence trim). See the review's §2 table for the full set + fix directions.

**Model-capability limits (not bugs — won't be "fixed", design around them)**
- **Multi-shot character continuity is naive-failure** — same prompt + different seed = different person. IC-LoRA (deep review §6) is the proposed lever.
- **Faces below ~80 px in-frame** identity-break; **hands / held objects** and **high-motion physics** are out of distribution. See §6.

**Agent caveat (carried over)**
- **Qwen 3.6 reasoning loops** when planning large multi-shot batches — recursive chain-of-thought can exhaust any token budget. Workarounds: prefer Gemma 12B (`mlx_models/gemma-3-12b-it-4bit`, no reasoning blocks, 7.5 GB) or the Anthropic API for 20+ shot batches; trigger 5 shots at a time in plain text. (Note: the in-panel agentic-flows chat was retired in v3.0.0; this applies to the remaining `/prompt/enhance` + any external-engine use.)
- **KTDS install case** (Linear HAI-156): `ModuleNotFoundError: ltx_pipelines_mlx` after a "green" install. Likely the old v2.0.2/v2.0.3 em-dash sanity-check bug (install went green for the wrong reason); fixed in v2.0.4. The v3.0.6 boot version-gate + `--force-reinstall` install determinism should close this class. Pending the user's log tail to confirm.

---

### CHANGELOG (historical — what was fixed, newest first)

#### v3.0.6 — deep-review hardening
- **HDR action un-deaded** — `generate_hdr` referenced undefined `HELPER_LOW_MEMORY` → NameError killed every HDR job. One-token rename to `LOW_MEMORY` (`mlx_warm_helper.py`).
- **CivitAI token-leak fix** — downloader leaked the API token on redirect + used a weak `endswith("civitai.com")` host check. Exact-host allowlist + redirect handler that strips `Authorization`; prefer header over `?token=`.
- **GPU-contention guard** — inline `/image/generate` and the video worker shared no GPU lock; a concurrent mflux + LTX render could OOM the Mac. Now mutually exclusive.
- **Boot version-gate** — helper reads `ltx_pipelines_mlx` version at boot, compares to the expected v0.14.0 pin, and surfaces a loud panel-log banner on mismatch (Phase 0 of the stabilization plan).
- **HiDream `select()`+deadline reader** — the HiDream subprocess reader had no deadline (a hung render blocked the queue forever); now reuses the mflux deadline+`killpg` loop. (HiDream is dropdown-hidden but reachable via saved config / `engine_override`.)

#### v3.0.5 — A2V kwarg signature shim (issue #5)
- **A2V died ~10 s into every render with a reference image** (`combined_image_conditionings() missing 1 required keyword-only argument: 'frame_rate'`). Upstream v0.14.0 made `frame_rate=` mandatory, but `a2vid_two_stage.py` / `lipdub.py` don't forward it. Fixed via runtime monkey-patch `_install_a2v_frame_rate_patch()` (`frame_rate=24.0` default, idempotent). Commit `681f429`. (The shim is still required at v0.14.8 — the upstream bug is live; deep review flags hardcoding 24.0 vs the real fps as a Med item.)

#### v3.0.4 — CivitAI SSL fix

#### v3.0.3 — HiDream hidden (issue #15)
- HiDream removed from the visible Image Studio dropdown until its lab repo is public. Code stays; reachable via saved config / `engine_override`.

#### v3.0.2 — Boost/Turbo accel restored (2-month silent regression)
- Git-archaeology'd a regression where the Boost/Turbo accel path silently stopped firing. Re-anchored the accel `denoise_loop` replacement across all import sites + restored the per-mode tail. (Commit `2694f9f` shipped the issue-#12 install gate earlier; accel fix is the v3.0.2 headline.)

#### v3.0.1 — FFLF crash fix
- **Extend downscale crash** — `_ensure_downscaled` wrote to `<name>.mp4.partial`; ffmpeg can't infer mp4 from `.partial`. Added `-f mp4`. Commit `736ca0d`.
- **Image Studio submitted Qwen-Image-Edit jobs when the add-on wasn't installed** (issue #12). `/image/engine_status` now returns `family_installed` per engine; the engine pill turns red with an install tooltip; Generate refuses upfront. Commit `2694f9f`.
- **Silent panel boot when helper venv missing** (issue #5 footnote) — now logs a single stderr warning naming both probed paths + the `LTX_HELPER_PYTHON` override. Commit `fa17c61`.

#### v3.0.0 (May 23) — marquee release
The Characters / Voice / Image Studio / A2V release. Folded in the May-17 Codex C+ UI restructure, the Train-tab + LoRA-chrome work, the Image Studio overhaul, and the post-decode-hang fix. The in-panel agentic-flows chat was retired here.

**Stats dashboard — panel-internal (private).** The dashboard is served by the panel at **`http://127.0.0.1:8199/stats`** (127.0.0.1-only; panel must be running). Data lives at **`state/stats-data.jsonl`** — gitignored, on the user's Mac only, never on the public repo. `panel_assets/stats.html` holds the template (public code; only the data is private). Panel background thread `stats_fetch_loop` runs `scripts/fetch_repo_stats.py` once at startup (if data is missing/stale ≥ 6h) and daily thereafter. Token resolution (first hit wins): `PHOSPHENE_REPO_STATS_TOKEN` → `GH_STATS_TOKEN` → `GH_TOKEN` / `GITHUB_TOKEN` → `gh auth token`; skipped silently if none. Zero setup for the user — just open `/stats`. See `scripts/STATS_DASHBOARD.md`. *(An earlier GitHub-Pages + committed-JSONL dashboard and an opt-in analytics module were both rolled back before launch; analytics removed entirely in `da1d6f5`. The brief window where stats data touched the public repo (`151d0d2`..`827c5d8`) held one snapshot of public aggregate counts only — nothing private leaked.)*

**Post-decode hang FIXED** (commit `94bd696`). A first attempt at an in-helper daemon-thread watchdog (`adc1cd2`) did not fire in practice — Metal's command-buffer completion handlers block every Python thread's GIL during the deallocator chain, so the watchdog was starved by the very thing it was meant to escape. Working fix rescues from **the panel** (separate process, GIL irrelevant): `WarmHelper._build_post_decode_panic` returns a `(log_hook, panic_check)` pair; `log_hook` spots upstream's `[Decoding ... done in X.Xs]` and arms a 45s grace clock; `panic_check` runs every 500ms and, if grace expired + output file on disk > 8KB, SIGKILLs the helper and returns a synthetic done event. Helper respawns on the next job (~30s). Armed for `generate` (T2V/I2V Balanced) + `extend` only. Validated on the 768×416 +6f Extend that previously hung 5-13 min. Bundled: Extend default steps 12→8 + TeaCache threshold 0.5→0.7 (~6 min wall); gallery `_dnWxH` dn-cache leak fix (the spurious "21:19" duration label).

**Codex C+ UI restructure (2026-05-17, 30+ commits).** Driven by Codex's C+ recommendation (Q4 vs Q8 as separate surfaces). Per-bug:
- Player aspect-ratio cropped vertical clips (`.player-surface` hardcoded 16:9 + `object-fit:cover`) → read natural dims on `loadedmetadata` into `--media-aspect`, height-driven sizing for verticals, `object-fit:contain`. Commit `4987022`.
- Expand button was inline-positioned, not a modal (`.expand-lightbox` had no CSS) → real fullscreen modal. Commit `4987022`.
- `/output/delete` orphaned the raw mp4 after upscale → collects every companion via sidecar fields + `UPSCALE_TAGS` heuristic. Commit `0dba2dc`.
- `/sidecar` 404'd on a raw card after upscale → now walks the `UPSCALE_TAGS` family. Commit `331795a`.
- `/stop` didn't kill the training subprocess (trainer Popens inherited the panel's process group) → `start_new_session=True`, `STATE["train_pgid"]` tracked, SIGTERM via killpg + 8s SIGKILL fallback. Commit `b6d1222`.
- `/queue/batch` rendered 5s clips when curl sent `duration=10` (client-side duration→frames math skipped) → derive frames via `_duration_to_8k_frames`. Commit `038a0a1`.
- Train Character High preset subtitle skew (`~4 h · 768px` vs canonical `5000 steps · 512px · ~2h50m`). Commit `4255f12`.
- Dual quality strip rendered on top of each other (`.quality-strip{display:grid}` outranked UA `[hidden]`) → `.quality-strip[hidden]{display:none!important}`. Commit `7bd5057`.
- Train voice toggle defaulted OFF even with a clip uploaded → defaults ON. Commit `ea2cf02`.
- `caption_strategy="user_provided"` rejected by the trainer → alias map in lora-lab (`b04eaab`); panel-side defense-in-depth `8b5a3cf`.
- Q4-distilled inference of dev-trained character LoRAs gave generic output (wrong base) → UI forces Q8 chips for characters; backend rejects `character_id + quality != high`. Commits `1d7983a`, `8b5a3cf`.
- HQ-speed Fast pill inactive at boot (boot cascade cleared `.active`) → commits `1056c99`, `04d2ffd`; the HQ-speed pill in Customize is now the single source of truth.

**Image Studio + agent quality pass (the "v2.0.6" codename work, ~18 commits).** Per-bug:
- klein-4B prompt-structure mismatch + Q4 default → taught the subject→environment→style→technical hierarchy in `agent/prompts.py`; Q6 default in `ImageEngineConfig.mflux_quantize`.
- Image jobs invisible in Now/Queue/Recent/Logs (`/image/generate` bypassed the queue) → routed `mode='image'` through `make_job` + `run_image_job_inner`; `_IMG_STUDIO_LOCK` arbitrates the sync agent path.
- Redundant in-pane Image Studio gallery deleted (unified Recent tab covers it).
- i2v "barely moves, just a zoom out" (agent reused the still prompt) → "Phase C — writing prompts FOR i2v" rules in `agent/prompts.py` (forbid still-prompt reuse, require explicit motion beats).
- 400×400 still-output mystery (`flux2_edit` referenced in saved configs but never wired) → added to `MFLUX_FAMILY_BIN`/`MFLUX_FAMILY_DEFAULTS`, refs routed to `--image-paths`; added non-distilled photoreal presets. *(FLUX/Klein later removed 2026-05-13.)*
- Issue #2 (Metal abort crash) — mflux SIGABRTs (exit -6, uncatchable) when 24 GB Qwen-Edit runs with 8 GB free → pre-flight RAM/disk check + SIGABRT detector with an OOM hint + Report-bug button.
- `auto_pause_during_renders` killed mid-batch agent calls → `submit_shots` plural tool batches the whole plan + a `_finish_after_turn` flag.
- `submit_shot` coerced invalid accel values silently → strict validation; "exact" accepted as a friendly alias for "off".
- Agent obeyed broken user instructions (e.g. `aspect="1:1"` on a 16:9 i2v) → "push back when instructions produce broken output" rule.
- AF pane stayed visible in Manual mode (`[hidden]` overridden by `display:flex`) → explicit `style.display` toggling.

**Security review pass — 0 CRITICAL · 4 HIGH · 6 MEDIUM, all 10 shipped.**
- HIGH — reject `Origin: null` in `_is_local_request`; validate `mflux_python_path` at save; validate `model_path` at `/agent/local/start` (HF `<owner>/<name>` against an owner allow-list; local paths must resolve under `mlx_models/` or HF cache); cap `submit_shot`/`submit_shots` calls per turn.
- MEDIUM — `/sidecar?path=` requires a media file before serving the `.json`; `/agent/models/install` reuses the owner allow-list; `_save_settings` writes with O_EXCL + fsync + os.replace + chmod 0600; `inspect_clip` prompt fields wrapped + truncated (prompt-injection defang); `read_document` PDF branch rejects > 50 MB + 30 s watchdog; `/output/hide` containment check.

**Agentic-flows polish (the "v2.0.5" codename work).** Stage stuck at 0% (progress schema changed flat-float → object); offline banner restyled; typing indicator now refreshes every 1.5s with elapsed seconds; auto-scroll switched to scroll-pinning + "↓ New messages" pill; abort on long turns via AbortController; tool cards de-emphasized; anchor un-pick on re-click; "Queue them" batch pill; multi-take `append:true`; OOM memory guard (refuse engine auto-spawn >92% pressure / >8 GB swap); **reasoning-model empty-content fix** (Qwen 3.6 splits `reasoning`/`content`; bumped `max_tokens` 3072→8192, engine.chat() reads reasoning, raises on length truncation); Phosphene-branded assistant avatar. Also three Phase 0 agentic items: engine-readiness banner (`7334836`), turn-summary chip (`134b5b1`), inline wall-time predictor (`43a7c3b`).

#### Semver 2.x line (May 3–6)
- **v2.0.0** (May 3) — marquee release; semver versioning starts.
- **v2.0.1** — Spicy mode toggle gates NSFW LoRA visibility.
- **v2.0.2** — install fails loud when pipeline packages are missing.
- **v2.0.3** — install log self-documents the Python toolchain.
- **v2.0.4** (May 5) — strip em-dash from the install.js sanity check (Pinokio shells mangled the unicode em-dash → false SyntaxError → every install failed). Pure ASCII now.
- **v2.0.5** (May 6) — drop the `print('venv OK: ...')` decoration from the sanity-import step (a user-side rewriter was cutting `OK:` out of the string and appending `OK` after the shell quote). The import exit code is the only success signal needed.

#### Pre-semver Y1.NNN line (Apr 28 – May 3)
- **Y1.001 → Y1.013** — first usable T2V/I2V; audio SHIP-BLOCKER fixed by pinning `mlx==0.31.1` (0.31.2 attenuated the vocoder by 22 dB).
- **Y1.014 → Y1.024** — hardware-tier system, Boost/Turbo speed modes, CivitAI LoRA browser, Q8 two-stage HQ tier, FFLF + Extend, `hf_transfer`, Q4/Q8 download filter (~80 GB saved on existing installs).
- **Y1.025 → Y1.035** (Codex-led) — Sharp upscale (PiperSR), I2V tail-stall fix (`Y1.034`, free DiT before VAE decode), VAE temporal-streaming (`Y1.035`), license/install hardening.
- **Y1.036 → Y1.039** — fixed the `Y1.024` Extend regression (route to Q8), VAE auto-streaming threshold (recovered ~7% on short clips; the `Y1.034` patch had tiled even short clips for a ~30 s tax), Now-card progress-bar rewrite (phase + denoise-step aware), gallery black-frame race fix.
- **S2 noir dialogue attribution swap** — wrong character delivered "Same thing, honey"; root cause was prompt format diverging from LTX docs. Linear HAI-152.
## 8. Open work / future direction

Everything below is also tracked in Linear (HAI-150 → HAI-158 under the Phosphene project). This section duplicates the most current state for fast scan.

### Loose ends from May 8 session

- **Qwen-Image-Edit-2511 weights download paused** at ~54 GB partial in `cache/HF_HOME/hub`. User OK'd to keep when it completes; the old 2509 cache (~54 GB) should be deleted once 2511 is intact. Resume the download at the next session start.
- **Issue #2 (Akossimon Metal abort)** — SIGABRT detection + pre-flight RAM check shipped, but awaiting user repro details to confirm the fix lands their case.
- **L-tier security items still open** — especially L2 (anchors / select containment) from the May 8 audit. Re-run the audit on a fresh `/tmp/phos_audit/security-review.md` (the old one is gone with the next reboot).
- **Image Studio "auto" engine pill** still shows the literal string "auto"; should resolve to the actual saved-engine status server-side and display the resolved name.
- **Dead code cleanup** — `_imgStudioRefreshLibraryLegacy` + `imgStudioCopyPath` (~40 lines) can be deleted in a follow-up pass; they're vestigial after the unified Recent tab landed.

### Multi-keyframe interpolation as SDK shot-composition primitive

**See:** `docs/SDK_KEYFRAME_INTERPOLATION.md` (full design + research review).

**TL;DR**: ComfyGuy9000 demoed first-frame-last-frame method via `Deno2026/comfyui-deno-custom-nodes`. Phosphene's `ltx_pipelines_mlx.KeyframeInterpolationPipeline` already accepts arbitrary `list[Image]` keyframes + `list[int]` indices — but our panel/helper artificially restrict it to 2 keyframes (start + end). Exposing the full multi-keyframe API gives us the agentic-flow compositional primitive: agent picks N stills, model fills the motion, character is anchored at every shot start.

**Status (2026-05-06)**:
- **Layer 1 — DONE.** Helper `generate_keyframe` action accepts arbitrary `keyframe_images` + `keyframe_indices` lists, with strict validation. Backward-compatible with the old `start_image`/`end_image` shape so the panel keeps working.
- **Layer 2 — DONE (commit 1afa1be).** `mlx_ltx_panel.py:make_job` reads a `keyframes_json` form field (JSON-encoded list of `{image_path, frame_index}` plus a `keyframes_total_frames` companion). The keyframe branch in `run_job_inner` decodes, validates strictly-increasing indices within `[0, frames-1]`, and forwards `keyframe_images` + `keyframe_indices` arrays to the helper. Backward compat preserved: empty `keyframes_json` falls back to `start_image`/`end_image`.
- **Layer 3 (panel UI multi-row keyframe list) — NOT YET.** The manual UI still has 2 drop-zones. Agents already use the full primitive via `submit_shot(keyframes=[{image_path, frame_index}, ...])`.

**Today's agent path**: through the panel — `agent.tools.submit_shot` composes the form including `keyframes_json` and POSTs to `/queue/add`. The legacy stdin-direct path still works for non-panel callers.

### Long-video research (Strategy A / B / C)

Goal: 1-minute final video on M4 Max 64 GB, ~40-60 min wall time acceptable.

- Strategy A — push single LTX clip beyond 10 sec. 20-sec proven at 1024×576 + Turbo + Sharp. 30-sec untested; 60-sec needs research.
- Strategy B — Extend chaining. ~16 min per +3 s pass, ~4.5 h total for 1-min. Audio continuous.
- Strategy C — multi-scene assembly via LLM-driven shot-list planner. ~42-49 min total, hides cuts cinematically. **This is what the multi-keyframe SDK enables.**

Codex deep-research brief drafted; awaiting return for literature review on FreeNoise / FIFO-Diffusion / StreamingT2V applicability.

Mr Bizarro also has Claude.ai / ChatGPT deep-research running (May 5) on inference speed without quality loss.

### Director Mode (agent workflow) — SHIPPED as Agentic Flows

What ships: a chat-driven shot planner tab in the panel. User pastes a script or idea, agent breaks it into shots, queues every shot through the existing FIFO queue, writes a `manifest.json`, and finishes. Designed for overnight batch rendering. Auto-stitch is intentionally NOT included — manifest is the deliverable; cuts belong to the user.

See preceding "Agentic Flows" section + `docs/AGENTIC_FLOWS.md` for the full reference.

Long-video research (per-shot length sweet spot, FreeNoise / FIFO-Diffusion / StreamingT2V applicability) still pending Codex deep-research return.

### Speed optimization candidates (from May 4 research session)

Ordered by what to try first:

1. **Two-stage workflow: draft + commit** — render 5-sec at full res first, then 20-sec same seed if approved. ~6× faster iteration. Replaces the failed "low-res draft" idea (faces don't survive res drop).
2. **Skip Sharp on batch testing** — ~26-100 s saved per clip during iteration.
3. **Pre-warm helper on panel boot** — saves ~30 s on first job of a session.
4. **Resume cancelled jobs from latent checkpoint** — recovers ~10 min per cancellation in iterative work. Higher engineering cost.
5. **Character anchoring via I2V keyframe** — quality unlock, not speed (but enables SDK).
6. **Two parallel helpers on 64 GB** — 2× throughput on batch renders. Refactor risk.

### Optimization paths ruled out (May 5 lab — see PERF_RESEARCH_2026-05-05.md)

Full research log: `docs/PERF_RESEARCH_2026-05-05.md`. Tested + ruled out:
mlx-mfa SDPA, `mx.compile`, RoPE caching, sliding-window attention, 8→6/4 step
reduction (catastrophic on the distilled model), block-skip caching (DeepCache
for DiT — works at tiny scale, fails at production: SSIM 0.69-0.72, "different
identity"). Most useful finding: **conv3d kernel port is NOT a real M4 path
forward** — MLX already uses steel implicit-GEMM at 50-70% of M4 peak; the
Draw Things "2.4×" was vs MPSGraph (which MLX doesn't use). Saves 1-2 weeks.

Block-skip patch infrastructure (with full A/B strips and per-config numbers)
parked on the `experiment/block-skip` branch — reusable if Lightricks ships a
block-skip-aware fine-tune.

Honest verdict: M4 Max + MLX 0.31 + LTX-2.3 Q4 distilled is already running at
50-70% of theoretical peak. Real breakthroughs need M5 hardware (Neural
Accelerators, ~3× free), NVFP4 quantization (when MLX supports it), or
research-grade work on token merging.

### Marketing / launch (HAI-157, HAI-158)

- Tweet thread + slides drafted in scrollback (5-6 tweets, copy-paste ready).
- Personal-account post drafted for `@AIBizarrothe`.
- Launch copy bundle in `launch/` folder (Pinokio article, X, Reddit, CivitAI).
- Sample mp4s + frames cached in `/tmp/phos_frames/`, `/tmp/phos_frames2/`, `/tmp/phos_dialogue/`, `/tmp/phos_lab_frames/`, `/tmp/phos_sdk_frames/`.
- Awaiting Mr Bizarro's launch timing call.

## 9. Hard constraints (don't violate)

- **Apple Silicon (M1+) only**. No PyTorch, no CUDA, no MPS shim. Native MLX or it doesn't ship.
- **Joint audio + video must remain**. That's the differentiator vs Wan / Hunyuan / Mochi. We don't drop audio for length.
- **Existing queue + helper + patch architecture stays intact**. No new microservices.
- **Branch policy** (post-2026-05-22 split, see §1): there is **no public `dev`** branch — it was deleted. Daily work goes to private `beta/main` (the local `dev` clone tracks it). **Promotion to PUBLIC `main` is the gated step — only with Mr Bizarro's explicit OK.**
- **Mr Bizarro's voice in writing**: copy-edit, don't rewrite. See memory file `feedback_copy_edit_dont_rewrite.md`. Tweets, posts, README copy — fix typos and grammar, never restructure or stack value-prop language.

## 10. Memory pointers (for next-Claude)

See local memory files for the cross-cutting workflow context — branch discipline (`phosphene_dev_workflow`), Linear credentials (`phosphene_linear_project`), writing style feedback (`feedback_copy_edit_dont_rewrite`, `feedback_writing_style`), source-of-truth discipline (`feedback_github_source_of_truth`), memory-save reflex (`feedback_dont_ask_to_save_memory`), shared infra (`claudio_repo`), historical MLX/Comfy decisions (`ltx_video_setup`).

## 11. Linear board

`https://linear.app/hairstylemojo/project/phosphene-9c11240704bb` — Phosphene project under HAI team (free plan caps at 2 teams).

Issue prefixes are `HAI-NN` because of the team constraint. Active:

- HAI-150 History (Done — reference doc)
- HAI-151 Current state (Done — reference doc)
- HAI-152 Lab batch 1 (In Progress — folded into this STATE.md going forward)
- HAI-153 Lab batch 2 (Backlog — depends on what comes next)
- HAI-154 Long-video research Strategy A/B/C (Backlog)
- HAI-155 Director Mode agent workflow → SHIPPED as Agentic Flows (2026-05-06, dev branch)
- HAI-156 KTDS install case (In Progress — pending log tail)
- HAI-157 Tweet thread + writeup launch (Backlog — drafts ready)
- HAI-158 Marketing scenes (In Progress)

## 12. How to start a fresh session

1. `cd ~/pinokio/api/phosphene-dev.git/`
2. `git fetch origin && git status -sb` — surface any drift first
3. Read this file (`docs/STATE.md`) AND check `git log --oneline dev -25` — recent commits move faster than this doc; the v2.0.6 May 8 batch is a good example (~18 commits in one session). Read `CLAUDE.md` for architecture.
4. Skim Linear `HAI-150` through `HAI-158` for state of each workstream
5. Check the dev panel is alive: `curl -s http://127.0.0.1:8199/status | python3 -m json.tool | head -10`
6. Last 5 commits on dev: `git log --oneline -5 dev`

If you find on-disk state contradicts this doc (paths moved, commits diverged), surface that to Mr Bizarro before working around it. Updating this doc at session-end is part of the loop.
