module.exports = {
  run: [
    // Note: fs.link is only declared in install.js, not here. New users
    // get the persistent drive on their first Install; users coming from
    // pre-Y1.004 will get it whenever they next reinstall (which is the
    // recommended recovery path anyway — see README). Running fs.link on
    // every Update would technically migrate their existing real folders
    // into the drive without a Reset, but it conflates two concerns
    // (durability of model assets vs. routine code updates) and adds a
    // 36 GB merge step to a flow that should be fast.

    // Resilient + branch-aware pull for the panel repo. Y1.015 made this
    // branch-aware so the same update.js works for both the production
    // panel (tracks `main`) and a local dev panel (tracks `dev`). The
    // currently-checked-out branch is whatever the install was set up
    // with; we pull origin/<that branch> rather than hardcoding `main`.
    //
    // Recovery from divergence (Y1.002) is preserved: if --ff-only
    // refuses, we fall back to reset --hard origin/<branch>. A Pinokio
    // panel install is never expected to carry local commits.
    {
      method: "shell.run",
      params: {
        message: [
          // Branch-aware + remote-aware fetch+pull. Resolves the
          // current branch's upstream (could be origin/main on the
          // prod clone, or beta/main on the private dev clone — see
          // 2026-05-22 split into public + private repos). The
          // explicit-origin form `git pull origin <branch>` broke
          // when public origin/dev was deleted.
          "REMOTE=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | cut -d/ -f1)",
          "BRANCH=$(git rev-parse --abbrev-ref HEAD)",
          "UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)",
          "echo \"updating branch: $BRANCH (upstream: $UPSTREAM)\"",
          "git fetch $REMOTE",
          "git pull --ff-only $UPSTREAM || (echo 'history diverged from upstream; falling back to reset --hard' && git reset --hard $UPSTREAM)",
          "git rev-parse --short HEAD"
        ]
      }
    },
    // ltx-2-mlx is PINNED to v0.14.8 (2026-06-01 catch-up from v0.14.0;
    // dgrauet's original tag-pin request 2026-05-12 — he pushes breaking
    // changes upstream to sync with the official Lightricks repo). Update
    // no longer tracks main; it fetches tags and re-checks-out the pinned
    // tag so a previously-installed user converges to a known-good state,
    // never to a moving HEAD. The ltx-package re-install step below then
    // force-copies the v0.14.8 source into site-packages (non-editable),
    // so an existing user's runtime actually moves to 0.14.8 — a bare
    // checkout alone would leave the old copy installed. To bump the pin:
    // edit BOTH install.js and update.js to the new tag, bump
    // _LTX_EXPECTED_VERSION in mlx_warm_helper.py, smoke-test on dev, push.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: [
          "git fetch --tags origin",
          "git checkout v0.14.8",
          "git rev-parse --short HEAD"
        ]
      }
    },
    // Force-downgrade mlx to 0.31.1 — fixes 22 dB audio regression on mlx
    // 0.31.2. Existing users who installed before this commit have mlx 0.31.2
    // and quiet audio; clicking Update reinstalls to the pinned version.
    // --force-reinstall + --no-deps so we change ONLY mlx without disturbing
    // ltx-* / transformers / etc. (some of which depend on mlx>=0.31.0).
    // See install.js for the full diagnostic note.
    {
      method: "shell.run",
      params: {
        message: "./ltx-2-mlx/env/bin/pip install --force-reinstall --no-deps 'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1'"
      }
    },
    // Re-install ltx-core-mlx + ltx-pipelines-mlx + ltx-trainer-mlx from
    // local packages. Critical for users who hit the dcd639e pin window
    // (commits 157b259 through e02e288): their site-packages still has
    // 0.1.0 installed even after `git checkout main` updates the source
    // tree to 0.2.0+. Without this re-install they'd have working source
    // but broken installed code (e.g. ExtendPipeline.extend_from_video
    // missing cfg_scale kwarg). --force-reinstall guarantees overwrite;
    // --no-deps avoids re-resolving (and re-pulling) mlx etc.
    //
    // 3.0: ltx-trainer added so existing v2 installs can run the new
    // Train Character tab without re-installing from scratch. The
    // trainer subprocess fails at `import yaml` without pyyaml, which
    // is a transitive dep of ltx-trainer-mlx — installing the local
    // package brings it cleanly.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "./env/bin/pip install --force-reinstall --no-deps ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer"
      }
    },
    // 3.0: pyyaml + pydantic + tqdm + rich are ltx-trainer-mlx's
    // transitive deps. We just installed ltx-trainer with --no-deps so
    // those weren't resolved. Install them explicitly (idempotent — pip
    // skips when already present).
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "./env/bin/pip install 'pyyaml>=6.0' 'pydantic>=2.0' 'tqdm>=4.65' 'rich>=13.0'"
      }
    },
    // 3.0: auto-caption (Gemma 3 12B via mlx-vlm) needs mlx-vlm 0.4.4.
    // --no-deps because mlx-vlm's heavy default deps fight our mflux /
    // transformers / mlx pins. The runtime import is lazy so a partial
    // install doesn't break the panel — but the Auto-caption button
    // will fail noisily if mlx-vlm is missing. Install idempotently.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "./env/bin/pip install --no-deps 'mlx-vlm==0.4.4'"
      }
    },
    // Y1.022: hf_transfer is HuggingFace's Rust accelerator — 5-10× faster
    // downloads on big repos (notably the ~25 GB Q8 bundle). Existing
    // installs that pre-date Y1.022 don't have it, so a Q8 download was
    // bottlenecked at ~50 KB/s on throttled HF anonymous tier. Update
    // installs it idempotently. The panel sets HF_HUB_ENABLE_HF_TRANSFER=1
    // when invoking hf download; if the package is missing for any reason
    // the hf CLI just emits a warning and falls back to plain Python.
    {
      method: "shell.run",
      params: {
        message: "./ltx-2-mlx/env/bin/pip install --upgrade 'hf_transfer>=0.1.6'"
      }
    },
    // 2026-05-31 review fix (E3): ensure certifi is present on every update.
    // start.js points SSL_CERT_FILE at certifi's cacert.pem (v3.0.4 fix); if
    // certifi is ever absent, that path vanishes and ALL panel stdlib HTTPS
    // breaks. Naming it explicitly keeps the cert bundle guaranteed-present.
    {
      method: "shell.run",
      params: {
        message: "./ltx-2-mlx/env/bin/pip install --upgrade certifi"
      }
    },
    // litellm: replaces the stdlib urllib chat client in agent/engine.py
    // with a multi-provider router (free retries, normalized errors,
    // single abstraction for OpenAI / Anthropic / Ollama / mlx-lm.server).
    // Pinned to >=1.83.14 — earlier 1.x had a March 2026 PyPI supply-
    // chain incident (post-install script stole SSH keys). engine.py
    // falls back to stdlib urllib if litellm is missing — safe but the
    // loop is less robust. Idempotent on repeat updates.
    {
      method: "shell.run",
      params: {
        message: "./ltx-2-mlx/env/bin/pip install --upgrade 'litellm>=1.83.14'"
      }
    },
    // smolagents: powers the optional CodeAgent runtime in
    // agent/runtime_smol.py (Phase 2 of the agent-layer refactor).
    // Off by default; the panel uses it only when launched with
    // PHOSPHENE_RUNTIME=smol.
    //
    // IMPORTANT: smolagents 1.24.0 hard-pins huggingface-hub<1.0.0 in its
    // setup, which conflicts with our >=1.5.0 floor (transformers 5+,
    // mflux, hf v1 CLI all need hub 1.x). Plain `pip install --upgrade`
    // refuses to resolve and fails the entire update with
    // ResolutionImpossible — Mr Bizarro saw this as a "blue screen error
    // flashing for a second every update".
    //
    // Fix: match install.js and use `uv pip install`. uv allows the
    // version-overlap conflict and installs both, leaving smolagents in
    // a "warned but functional" state (verified CodeAgent +
    // LocalPythonExecutor both work on hub 1.14.0).
    {
      method: "shell.run",
      params: {
        message: "uv pip install --python ./ltx-2-mlx/env/bin/python --upgrade 'huggingface-hub>=1.5.0,<2.0' 'smolagents>=1.24.0'"
      }
    },
    // Pin mflux to the exact version our FBCache patch is line-targeted
    // against (0.17.5). If a future bump is needed, change the pin here
    // AND in install_qwen.js AND re-validate patch_mflux_fbcache.py.
    //
    // Two-step shape mirrors install_qwen.js (the previous single-step
    // `--force-reinstall --no-deps` could leave mflux's transitive deps
    // MISSING after this update ran — the panel would show Qwen as
    // available, then runtime would ImportError on first call).
    //
    // Gated on "is mflux already installed?" because update.js runs for
    // every user on every update — including ones who never opted into
    // Qwen via install_qwen.js. Importing mflux is the cheapest probe
    // we can do without touching pip. The patch step below is already
    // idempotent (skips when its marker is present); the install step
    // we gate is the one that materially adds packages to the venv.
    {
      method: "shell.run",
      params: {
        message: [
          // 2026-06-13: the mflux image-engine pack (Ideogram 4 + Qwen-Edit) is
          // now STANDARD — installed for EVERY user on update, not just those who
          // once ran the optional installer. This is what unblocks Ideogram 4 for
          // everyone who never opted in (cocktailpeanut hit exactly this: token
          // saved, regions drawn, but family_installed:false because the mflux CLI
          // was absent). Safe to bundle: the base install already pins
          // huggingface-hub to a range mflux requires (see install.js), and mflux
          // lives in the same venv as the LTX stack.
          //
          // BEST-EFFORT: wrapped in ( … ) || echo so a pip hiccup (network) does
          // NOT fail the whole update — video is unaffected, and the panel
          // surfaces a one-click reinstall path if mflux didn't land.
          //
          // Two-step (WITH deps then --force-reinstall --no-deps) mirrors
          // install_qwen.js so the full transitive set (transformers, accelerate,
          // sentencepiece, …) resolves, then the version is locked.
          // uv, NOT plain pip: mlx-vlm is installed --no-deps, so plain pip dumps
          // a scary "ERROR: pip's dependency resolver…" block about mlx-vlm's
          // unsatisfied extras on every install (verified — this is what made
          // cocktailpeanut's update look broken even though mflux installed fine).
          // uv does the same install with zero such noise.
          "echo 'Installing/refreshing the mflux image-engine pack (Ideogram 4 + Qwen-Edit) — now standard…' && \\",
          "( uv pip install --python ./ltx-2-mlx/env/bin/python 'mflux==0.18.0' && \\",
          "  uv pip install --python ./ltx-2-mlx/env/bin/python --reinstall --no-deps 'mflux==0.18.0' && \\",
          "  uv pip install --python ./ltx-2-mlx/env/bin/python 'mlx-teacache==0.4.1' ) \\",
          "|| echo 'WARN: mflux image-engine install hit an error — video is unaffected; re-run Update, or use the Reinstall image engines action, to retry.'"
        ].join("\n")
      }
    },
    // Re-apply patches. Codec patch is required; I2V OOM patch is a no-op
    // on dcd639e (older I2V structure) and reports drift gracefully now.
    // mflux FBCache patch is idempotent (skips when its marker is already
    // present); installs the cache path the first time and stays put.
    // Pin to the venv's python3.11 to match install.js — `python3` on
    // Pinokio hosts isn't guaranteed to be 3.11 (or even present on PATH).
    {
      method: "shell.run",
      params: { message: "./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py" }
    },
    {
      method: "shell.run",
      params: { message: "./ltx-2-mlx/env/bin/python3.11 patch_mflux_fbcache.py" }
    },
    // Y1.024: reclaim disk on existing installs by deleting model files
    // we never load. dgrauet's LTX repos host duplicate transformer
    // variants and unused upscalers; pre-Y1.024 `hf download` grabbed
    // everything (Q4 → 56 GB instead of 20, Q8 → 82 GB instead of 37).
    // New installs use --include filters so the bloat never lands. This
    // step trims existing bloated installs. `rm -f` is silently a no-op
    // if the file is already absent.
    //
    //   Q4 trim: ~25 GB freed (transformer-distilled-1.1, distilled-lora-1.1,
    //            x1.5 upscaler, temporal upscaler)
    //   Q8 trim: ~45 GB freed (transformer-distilled, transformer-distilled-1.1,
    //            distilled-lora-384-1.1, x1.5 upscaler, temporal upscaler)
    //
    // Files chosen are those the panel never references at runtime —
    // see required_files.json for the canonical list of what we DO load.
    //
    // IMPORTANT (3.0+): `mlx_models/ltx-2.3-mlx-q4/transformer-dev.safetensors`
    // is no longer trimmed. Train Character downloads that 11 GB file on
    // demand into the Q4 dir (see `_train_install_dev_transformer` in
    // mlx_ltx_panel.py). Removing it here would silently undo the
    // training install on every panel update — users who clicked
    // Download Q8 dev for training and then Update would lose 11 GB of
    // bandwidth and have to re-download.
    // ---- Mosaic fix (2026-06-13) -----------------------------------------
    // The Q4 render path is two-stage: it upscales the half-res latent with
    // spatial_upscaler_x2_v1_1.safetensors. The Y1.024 download allowlist
    // dropped that file, so affected Q4 installs silently ran a
    // RANDOMLY-INITIALISED upsampler -> the "mosaic"/rainbow-grid (#23; root
    // cause found by @dgrauet, who also made ltx-pipelines-mlx fail loud about
    // it upstream). It's back in required_files.json (verify/repair flags it),
    // and fetched here so existing users self-heal on Update without clicking
    // Repair. ~1 GB, resumable; hf skips it when already present. BEST-EFFORT —
    // a network hiccup must not fail the update.
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Ensuring the Q4 spatial upscaler is present (mosaic fix)…' && \\",
          "hf download dgrauet/ltx-2.3-mlx-q4 --local-dir ../mlx_models/ltx-2.3-mlx-q4 --include 'spatial_upscaler_x2_v1_1.safetensors' \\",
          "|| echo 'WARN: spatial upscaler fetch failed — open the panel and click Repair to retry (fixes the mosaic).'"
        ].join("\n")
      }
    },
    {
      method: "shell.run",
      params: {
        message: [
          "echo 'Trimming unused model variants from mlx_models/ (saves up to ~70 GB on pre-Y1.024 installs)…'",
          "rm -f mlx_models/ltx-2.3-mlx-q4/transformer-distilled-1.1.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q4/ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q4/spatial_upscaler_x1_5_v1_0.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q4/temporal_upscaler_x2_v1_0.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q8/transformer-distilled.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q8/transformer-distilled-1.1.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q8/ltx-2.3-22b-distilled-lora-384-1.1.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q8/spatial_upscaler_x1_5_v1_0.safetensors",
          "rm -f mlx_models/ltx-2.3-mlx-q8/temporal_upscaler_x2_v1_0.safetensors",
          "echo 'Trim done.'"
        ]
      }
    }
  ]
}
