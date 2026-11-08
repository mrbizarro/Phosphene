// Phosphene — optional Hailuo H3 engine pack. Idempotent.
//
// H3 (MiniMax-H3 FL2VA) is Phosphene's SECOND video engine: one prompt in,
// video + synced dialogue + sound out. It is deliberately NOT part of the base
// install:
//
//   * ~75 GB of weights on top of the ~36 GB base — most users never want it.
//   * Memory: the full bf16 engine peaks at ~42.8 GiB and wants a 60 GB+ Mac;
//     the compact Q8 engine built at the end of this script peaks at 25.63 GiB
//     and runs from 36 GB. Q8 has been the AUTO default on every machine since
//     v4.8.0, so 36 GB is the real floor — and it is the number
//     scripts/pinokio/h3_preflight.sh enforces and H3_MIN_RAM_GB_Q8 in
//     mlx_ltx_panel.py gates on. (This said "only runs on 64 GB+ Macs" for two
//     releases after that stopped being true.)
//   * MiniMax Community License — territory restrictions apply, so it must be
//     an explicit opt-in, not something that lands with the app.
//
// It also gets its OWN venv rather than sharing ltx-2-mlx/env, because the two
// engines disagree about MLX: Phosphene pins mlx==0.31.1 (0.31.2 regresses LTX
// audio by 22 dB), while the H3 port needs mlx>=0.32. Separate venvs mean an
// H3 install can never break LTX rendering, and removing H3 is `rm -rf`.
//
// Everything below is safe to re-run, and re-running it is ALSO the repair
// path — the Pinokio menu points "Repair Hailuo H3" at this same script:
//   clone   — skipped when minimax-h3-mlx/.git exists
//   venv    — reused when its interpreter actually runs; rebuilt when it
//             doesn't (see the self-healing note on that step)
//   uv pip  — already-installed packages are no-ops
//   weights — hf_hub_download resumes partial files, skips intact ones
// So a user whose engine broke re-runs this and pays ~2 minutes, not 75 GB.
//
// The panel discovers the result on its next /status tick (a couple of
// seconds) and unlocks the engine picker. No restart.

// The branch carrying the validated staged runner (scripts/generate_staged.py).
// NOTE: `--first-frame` (FL2VA image conditioning, i.e. Image mode on H3)
// landed AFTER this branch was published. The panel probes the installed
// runner for the flag and keeps Image mode on LTX when it's absent, so an
// older checkout degrades to Text-only instead of failing mid-render. Bump
// this pin when the first-frame work is published.
// v2 (2026-08-10): --lora (single adapter slot), TAE draft decode, native
// single-window 10s/15s drafts, Stage-A latent caching, the streaming Q8
// quantizer for 48 GB Macs, and the LoRA-on-quantized logical-dims fix.
// Published as a NEW branch: the histories diverged, and force-moving a
// branch users' installs already track is how updates break mid-clone.
// 4.19.0: this is ONLY the branch a fresh clone starts on. The engine commit
// the app runs is the PIN in scripts/pinokio/h3_checkout.sh (an exact SHA),
// which the next step moves the checkout to — so pushing this branch no
// longer changes what any 4.19+ install runs.
const H3_BRANCH = "codex/h3-engine-v2"
// Every step that works inside the checkout starts here: resolve the roots the
// panel uses (LTX_H3_ROOT / LTX_H3_MODELS), then enter the checkout. See
// scripts/pinokio/h3_roots.sh.
//
// 4.20 - HOW A STEP STOPS THIS INSTALL. Pinokio 8.2 ignores a shell.run step's
// exit code; it stops a script only when the step's OUTPUT matches /error:/i or
// /errno /i (pinokiod kernel/shells.js, the default `on` handlers). So every
// required script this file dispatches (h3_preflight, h3_checkout, h3_venv,
// h3_deps, h3_build_q8, and h3_roots.sh's h3_enter / h3_fetch_weights) prints
// one "FATAL error: <reason + what to do>" line when it fails - before this,
// the preflight refused the download and the install carried on and started it.
// `h3_enter` replaces the inline `cd`: a missing checkout used to end the `&&`
// chain in silence. The two optional fetches (Turbo, TAE) never say "error:".
// scripts/check_pinokio_scripts.js fails the build for a call that cannot stop.
const RH3 = ". scripts/pinokio/h3_roots.sh && h3_enter && "

module.exports = {
  requires: { bundle: "ai" },
  run: [
    {
      method: "notify",
      params: {
        // "Needs a 64 GB+ Apple Silicon Mac" until v4.8.0 made the compact Q8
        // engine the default everywhere and dropped the real floor to 36 GB.
        // The preflight two steps down enforces 36; this sentence was turning
        // away 36-48 GB Macs the installer would have happily served.
        html: "<b>Installing Hailuo H3 (second video engine, ~75 GB).</b><br>Generates picture, dialogue and sound together, and sits beside LTX in the engine switcher. Needs an Apple Silicon Mac with 16 GB+ of memory (the compact Q8 engine, built at the end of this install, read from disk as it renders; 16 GB makes Draft and Standard clips, 18 GB+ also High); 60 GB+ additionally unlocks the full bf16 quality engine. MiniMax H3 Community License: accept it in Phosphene first (click Hailuo H3 in the engine switcher); it does not cover the EU, the UK, South Korea or the US. Resumable if interrupted."
      }
    },

    // ---- Licence first (4.19.8) --------------------------------------------
    // The MiniMax H3 Community License covers only its Applicable Territory
    // (worldwide except the EU, the UK, South Korea and the US) and asks a
    // product to bind each user to its use restrictions before giving access
    // (s. V.2). The panel's licence screen records the acceptance; this step
    // stops a NEW install until it exists (an install that already has the
    // weights continues - the panel asks before the next H3 render instead).
    {
      method: "shell.run",
      params: {
        message: [
          "bash scripts/pinokio/h3_licence_gate.sh"
        ]
      }
    },

    // ---- Hardware preflight ------------------------------------------------
    // Fail FAST and readably. Without this the install spends an hour pulling
    // 75 GB onto a machine that can never render with it — the panel would
    // then correctly refuse every H3 job and the user would rightly be angry.
    // Fail-open by design: if sysctl is missing or unparseable we proceed,
    // because a preflight that can't read the hardware must never be the thing
    // that blocks an otherwise-fine install.
    {
      method: "shell.run",
      params: {
        message: [
          // 3.8.3: the if/else was a "\n"-joined STRING, so Pinokio wrote all
          // 845 chars to the pty in one go (#56 / #50). Body — with the
          // fail-open rationale and the 36e9 floor — now in
          // scripts/pinokio/h3_preflight.sh. Gate: scripts/check_pinokio_scripts.js.
          "bash scripts/pinokio/h3_preflight.sh",
          "echo 'free disk space:'",
          "df -h ."
        ]
      }
    },

    // ---- Clone the engine --------------------------------------------------
    // Guarded: `git clone` into an existing directory fails and would abort
    // the whole install on a re-run / resume.
    //
    // EVERY STEP BELOW RESOLVES ITS ROOTS THROUGH scripts/pinokio/h3_roots.sh —
    // LTX_H3_ROOT / LTX_H3_MODELS, the overrides the panel and the sidebar
    // already honour. The steps used to hardcode `minimax-h3-mlx` and
    // `{{cwd}}/mlx_models/hailuo-h3`, so on a relocated install Repair fixed a
    // second checkout the panel never uses and could fetch ~75 GB of duplicate
    // weights (Codex INST-09). Hence no `path:` and no `when: exists(...)`:
    // both can only name the default location.
    {
      method: "shell.run",
      params: {
        message: [
          ". scripts/pinokio/h3_roots.sh && { [ -d \"$H3_CHECKOUT/.git\" ] || git clone --branch " + H3_BRANCH + " https://github.com/mrbizarro/minimax-h3-mlx.git \"$H3_CHECKOUT\"; }"
        ]
      }
    },

    // Pin to the branch on every run, so Resume Install after a partial clone
    // lands on the same code the panel was validated against.
    //
    // v4.0 — TWO BUGS OF THE SAME CLASS AS THE LTX LANE, both filed in the
    // v3.8.1 hotfix notes (§9.3) and the 2026-08-13 sweep (§8.6) and fixed here.
    //
    // 1. THE FETCH NEVER ADVANCED ANYTHING. `git fetch origin <branch>` writes
    //    FETCH_HEAD and updates `refs/remotes/origin/<branch>`; the following
    //    `git checkout <branch>` resolves the LOCAL branch, which the clone
    //    created once and which nothing has moved since. So every H3 user has
    //    been pinned to whatever the branch tip was on the day they first
    //    installed, and re-running the installer could never bring them
    //    forward. `--force -B <branch> FETCH_HEAD` makes the local branch point
    //    at what was actually fetched, which is what "pin to the branch on
    //    every run" always claimed to do.
    //
    // 2. A DIRTY TREE BLOCKED THE CHECKOUT, silently, the same way it killed
    //    the LTX pin move for the whole fleet on 3.8.0. Nothing of ours patches
    //    this tree today, which is exactly why it is worth guarding now rather
    //    than after the first user reports it: `minimax-h3-mlx/` is
    //    app-managed, its weights live outside it in ../mlx_models/hailuo-h3,
    //    and its venv is ignored, so reset --hard costs nothing and cannot
    //    touch user content. Same guard shape, same reasoning, one class.
    //    The guard tests `minimax_h3_mlx/` — the package directory, verified
    //    present at the branch tip; this repo has no root pyproject.toml, so
    //    the LTX lane's marker would have been wrong here.
    //
    // CONSEQUENCE, STATED RATHER THAN BURIED: an existing H3 user who re-runs
    // the installer now MOVES FORWARD to the branch tip instead of staying on
    // their install-day snapshot. That is what the step always said it did.
    // It is safe because the panel PROBES the installed runner for its
    // capabilities (`h3_supports_chain()`, `h3_supports_chain_prompts()`)
    // rather than assuming them, so a runner that gains or loses a flag is
    // handled by the UI instead of dying in argparse 30 s into a render.
    {
      method: "shell.run",
      params: {
        message: [
          // The pin itself lives in scripts/pinokio/h3_checkout.sh (#74: Update
          // must make the same move; one literal, two callers). H3_BRANCH above
          // is only the fresh-clone branch; the pin is a commit it need not contain.
          RH3 + "bash \"$APP_ROOT/scripts/pinokio/h3_checkout.sh\" \"$(pwd)\"",
          RH3 + "git rev-parse --short HEAD"
        ]
      }
    },

    // ---- Its own Python 3.11 venv -----------------------------------------
    // Same reasoning as install.js: Pinokio's `venv:` shortcut builds from
    // conda-base (currently 3.10 on the macOS bundle), which can't install the
    // MLX wheels. Force 3.11 with uv, then target it explicitly on every pip
    // step. If a wrong-Python venv is already there, nuke it — it holds no
    // user data.
    //
    // SELF-HEALING, and deliberately NOT gated on `when: !exists(...)`.
    // `uv venv` builds the interpreter as a symlink chain into Pinokio's
    // SHARED managed Python:
    //     .venv/bin/python3.11 -> python
    //     .venv/bin/python     -> <pinokio>/cache/XDG_DATA_HOME/uv/python/
    //                             cpython-3.11-macos-aarch64-none/bin/python3.11
    // That target is Pinokio's, not ours. Any other pack install (or any other
    // Pinokio app) that makes uv re-resolve, bump or prune the managed
    // interpreter leaves this chain DANGLING — the engine stops resolving even
    // though the venv, the clone and all ~75 GB of weights are untouched. That
    // is the v3.4.0 "installed other packs and Hailuo H3 vanished" report.
    //
    // A path-existence `when:` guard cannot fix that reliably: depending on
    // whether the check stats the link or its target, a dangling chain reads
    // as either present (repair silently skipped — the failure mode we must
    // never ship) or absent. So we ask the only question that actually
    // matters — does this interpreter RUN? — inside the shell, and rebuild
    // only when it doesn't. Healthy installs pay ~50 ms and skip the rebuild;
    // broken ones self-heal in ~2 minutes without re-downloading a byte.
    {
      method: "shell.run",
      params: {
        message: [
          // The body is scripts/pinokio/h3_venv.sh (it was a 372-char dispatch).
          RH3 + "bash \"$APP_ROOT/scripts/pinokio/h3_venv.sh\""
        ]
      }
    },

    // ---- Its packages, at the known-good set (4.19.5) ----------------------
    // Through 4.19.4 this was `uv pip install -r requirements.txt`, whose lines
    // are floors only - every new install resolved whatever PyPI held that
    // day, and an upstream release could break every new H3 install with no
    // Phosphene release behind it (the LTX venv's tokenizers 0.23.3 story,
    // 4.19.4). scripts/pinokio/h3_deps.sh is THE one install into this venv:
    // it resolves under scripts/pinokio/h3_constraints.txt (-c), which also
    // MOVES a drifted package back - so Repair and "Update Hailuo H3 runner",
    // which run this same script, repair a drifted venv - then checks the set
    // and the runner's imports. It prints "error:" on failure, which is what
    // stops the Pinokio run here instead of 75 GB later.
    {
      method: "shell.run",
      params: {
        message: [
          RH3 + "bash \"$APP_ROOT/scripts/pinokio/h3_deps.sh\" \"$(pwd)\"",
          RH3 + ".venv/bin/python -c \"import mlx.core as mx, numpy, PIL; print('H3 deps OK')\""
        ]
      }
    },

    // ---- Weights (~75 GB) --------------------------------------------------
    // The repo's own scripts/download_selected.py is the source of truth for
    // WHICH files the staged runner loads (pruned bf16 DiT + the Q8 compact
    // components + the upstream text-encoder config). Calling it instead of
    // re-listing filenames here means the manifest can never drift.
    //
    // It appends `models/` to whatever --root it is given, so the components
    // land at mlx_models/hailuo-h3/models/{deepbeep-pruned-bf16,ddalcu-q8,
    // upstream-meta}/. The panel resolves BOTH that shape and the flat one
    // (see _h3_model_roots in mlx_ltx_panel.py), so no post-download move is
    // needed — and a resumed install never has to reconcile a half-moved tree.
    //
    // mlx_models/ is fs.link-mapped to Pinokio's virtual drive, so these 75 GB
    // survive a Reset like every other weight.
    //
    // HF_HOME is declared explicitly (shell.run replaces the env wholesale, so
    // Pinokio's global ENVIRONMENT value doesn't reach the child). With
    // local_dir downloads, huggingface_hub writes straight to the destination
    // instead of duplicating into the hub cache.
    {
      method: "shell.run",
      params: {
        env: {
          HF_HOME: "{{cwd}}/cache/HF_HOME",
          HF_XET_HIGH_PERFORMANCE: "1"
        },
        message: [
          // A FLAT tree (see h3_roots.sh) cannot be written by this downloader
          // — it appends models/ — so it is left alone instead of duplicated.
          // An INCOMPLETE flat tree stops the install with the missing paths
          // ("error:" is what Pinokio 8.2.0 stops on) instead of skipping it
          // (h3_fetch_weights in h3_roots.sh; kept out of line for the width).
          RH3 + "h3_fetch_weights"
        ]
      }
    },

    // ---- Turbo v1.0: fetched from UPSTREAM, re-laid out on this Mac --------
    // Target: lightx2v_v1.0_768p_ourlayout.safetensors - the runner-layout
    // repack (SHA-256 d51d626fe0845da7e5845a47c323cf3f29086d44d24cb1a4b980882488746197).
    // 4.19.8: Phosphene no longer hosts this file. It is a MiniMax H3 Model
    // Derivative, and the H3 licence only allows distribution inside its
    // Applicable Territory, so scripts/fetch_h3_turbo.py fetches the ORIGINAL
    // from lightx2v/Minimax-h3-Turbo
    // (minimax_h3_fl2v_turbo_4step_v1.0_768p_bf16.safetensors, pinned commit,
    // source SHA-256 1bdabc2e9fce20b1db563b96bcf6e46adcad4c1964f423676436bf266cc7416c),
    // re-lays it out locally (pure byte moves, no maths) and installs it only
    // when the result matches the digest above - byte-identical to the file
    // installs used to download, so Turbo behaves exactly as before. An
    // install that already has the file is left alone.
    // Do not substitute the raw v0.1 file
    // `minimax_h3_fl2v_turbo_4step_v0.1.safetensors`: its alpha/rank scale
    // lives OUTSIDE the checkpoint and at `--lora ...:1.0` it renders
    // coloured noise. The ONLY accepted v0.1 fallback is the alpha-folded
    // `lightx2v_v0.1_ourlayout_alpha8.safetensors`.
    //
    // BEST-EFFORT ON PURPOSE: the panel's /h3/turbo/install endpoint fetches
    // the managed Turbo adapter on demand, so a transient network failure here
    // must not fail a 75 GB install that is otherwise complete. The script
    // resumes partials, verifies both digests and renames into place only
    // after the check - a corrupt transfer is deleted, never installed. (One
    // short line per command: Pinokio 8.0.x wedges its shell on ~350+
    // character lines.)
    {
      method: "shell.run",
      params: {
        message: [
          RH3 + ".venv/bin/python \"$APP_ROOT/scripts/fetch_h3_turbo.py\" --dir \"$H3_LAYOUT/turbo-lora\" || echo 'TURBO FETCH FAILED - panel offers a one-click retry'"
        ]
      }
    },

    // ---- lightx2v v1.3 (Best on High): fetched from UPSTREAM, built here ----
    // Target: lightx2v_v1.3_768p_phosphene.safetensors (SHA-256
    // 45ee324a31beacef7a2fbef019b99e16b35d80586dc4e0d60fecf6ca679751d8),
    // built by scripts/fetch_h3_v13.py from LightX2V's original
    // minimax_h3_fl2v_turbo_4step_v1.3_768p_bf16.safetensors (pinned commit,
    // source SHA-256 97b775707bb5732e01dba0cf3833d3fe2947c953859d4d05ea3bd74894c43d2b).
    // Never hosted by Phosphene: an H3 Model Derivative (MiniMax H3 Community
    // License; LightX2V's code is Apache-2.0). BEST-EFFORT, like Turbo: the
    // Speed row's "Get it" button runs the same script on demand.
    {
      method: "shell.run",
      params: {
        message: [
          RH3 + ".venv/bin/python \"$APP_ROOT/scripts/fetch_h3_v13.py\" --dir \"$H3_LAYOUT/turbo-lora\" --licence-state \"${LTX_STATE_DIR:-$APP_ROOT/state}\" || echo 'V13 FETCH FAILED - the Speed row offers a retry'"
        ]
      }
    },

    // ---- TAE: the fast draft decoder (~23 MB) ------------------------------
    // H3's video decoder is a 36-layer ViT and it is the biggest FIXED cost in
    // a render — 88 s of a 501 s clip, 77% of everything that isn't denoising.
    // madebyollin's TAE replaces it on DRAFT tiers only and takes a 640x384 5 s
    // draft from ~5 min to ~66 s. 23 MB against a 75 GB pack, so it is not
    // worth making optional; the panel offers the fast path only when this file
    // AND the runner flag are both present, and every delivery tier keeps the
    // untouched full VAE regardless.
    {
      method: "shell.run",
      params: {
        env: {
          HF_HOME: "{{cwd}}/cache/HF_HOME",
          HF_XET_HIGH_PERFORMANCE: "1"
        },
        message: [
          RH3 + "mkdir -p \"$H3_LAYOUT/tae\"",
          // The HF repo this used to come from (madebyollin/taeh3) was deleted
          // in 2026-09 and every H3 install died here with "Repository Not
          // Found" (Pinokio, @macstephen). The script fetches the same bytes
          // from a pinned commit of the author's GitHub repo, verifies the
          // sha256, and keeps HF as a fallback.
          RH3 + ".venv/bin/python \"$APP_ROOT/scripts/pinokio/h3_fetch_tae.py\" \"$H3_LAYOUT/tae/taeh3.safetensors\""
        ]
      }
    },

    // ---- Reduced-RAM engine (every Mac): build the Q8 DiT locally ----------
    // The Q8 pack halves the render peak (27.3 vs 42.8 GiB measured), which is
    // what makes H3 possible on this class of machine at all. Built locally by
    // quantize_stream.py — bounded memory (never holds more than one tensor,
    // CPU stream, deterministic), so the build itself runs fine on the same
    // 36 GB machine that could never load the model whole. ~22 GB on disk,
    // ~5 min.
    //
    // EVERY MACHINE BUILDS IT SINCE v4.8.0. This used to be skipped on 64 GB+
    // Macs ("bf16 is the quality default there") — then v4.8.0 made Q8 the AUTO
    // default everywhere without un-gating the build, so on a big Mac the new
    // default silently fell back to bf16 for want of a pack Install refused to
    // make: "i updated but still getting this much memory", python3.11 at
    // 47.76 GB. The gate now lives in h3_build_q8.sh with that history; here it
    // is unconditional. Idempotent: the pack's own quant_config.json gates the
    // re-run, so a healthy install pays one stat call.
    {
      method: "shell.run",
      params: {
        // 3.8.3: body moved to scripts/pinokio/h3_build_q8.sh (595-char
        // dispatch). The app root is passed as $1; h3_roots.sh has exported
        // LTX_H3_MODELS as an absolute path, which the build reads.
        message: [
          RH3 + "bash \"$APP_ROOT/scripts/pinokio/h3_build_q8.sh\" \"$APP_ROOT\""
        ]
      }
    },

    {
      method: "notify",
      params: {
        // 3.8.3: this used to say "the Video tab now has an Engine row". The
        // engine control has been the switcher in the TOP RIGHT of the header
        // since the engine table landed — there is no Engine row in the Video
        // tab any more, and #58 ("[bug] Engine Picker not showing") is a user
        // who installed H3 successfully and then went looking in the place this
        // sentence sent him. Say where the control actually is, and name the
        // one thing that can still hide it: the switcher only appears when
        // there is more than one engine this Mac can render with, and on a
        // sub-60 GB Mac that means the reduced-RAM Q8 pack built by the step
        // above must be on disk.
        html: "<b>Hailuo H3 ready.</b><br>Open the panel — the engine switcher is in the <b>top right of the header</b>, next to the memory and models pills: <b>LTX-2.3 | Hailuo H3</b>. (Not in the Video tab.) Restart the panel if it was already running. H3 serves Text and Image, and generates dialogue + sound from the same prompt, so write them into it."
      }
    }
  ]
}
