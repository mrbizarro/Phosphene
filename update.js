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
          // MUST be one joined string, not seven array elements. Pinokio runs
          // each element of a `message` array in its OWN shell — a fresh
          // conda-base activation, a fresh process, terminated when the command
          // returns. Shell variables therefore do NOT survive from one element
          // to the next. As separate elements this block was silently broken:
          // Pinokio's own log shows the echo printing
          //     updating branch:  (upstream: )
          // which means `git fetch $REMOTE` degraded to a bare `git fetch`,
          // `git pull --ff-only $UPSTREAM` to a bare `git pull`, and worst of
          // all the divergence recovery `git reset --hard $UPSTREAM` to a bare
          // `git reset --hard` — a no-op against HEAD that cannot recover from
          // divergence at all, which is the one job it exists to do.
          // Joining with "\n" runs the whole block in a single shell, the way
          // the other multi-command steps in this file already do.
          "REMOTE=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | cut -d/ -f1)",
          "BRANCH=$(git rev-parse --abbrev-ref HEAD)",
          "UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)",
          "echo \"updating branch: $BRANCH (upstream: $UPSTREAM)\"",
          "git fetch $REMOTE",
          "git pull --ff-only $UPSTREAM || (echo 'history diverged from upstream; falling back to reset --hard' && git reset --hard $UPSTREAM)",
          "git rev-parse --short HEAD"
        ].join("\n")
      }
    },
    // ltx-2-mlx is PINNED to v0.14.19 (2026-08-11 catch-up from v0.14.8;
    // dgrauet's original tag-pin request 2026-05-12 — he pushes breaking
    // changes upstream to sync with the official Lightricks repo). Update
    // no longer tracks main; it fetches tags and re-checks-out the pinned
    // tag so a previously-installed user converges to a known-good state,
    // never to a moving HEAD. The ltx-package re-install step below then
    // force-copies the v0.14.19 source into site-packages (non-editable),
    // so an existing user's runtime actually moves to 0.14.19 — a bare
    // checkout alone would leave the old copy installed. To bump the pin:
    // edit BOTH install.js and update.js to the new tag, bump
    // _LTX_EXPECTED_VERSION in mlx_warm_helper.py, smoke-test on dev, push.
    //
    // The v0.14.19 bump needs NO model re-download. See install.js for the
    // per-release notes; the one weight-adjacent change is that 0.14.13+
    // prefers a versioned `transformer-distilled-*.safetensors` when one
    // exists and falls back to the unversioned name — and the trim step at
    // the bottom of this file removes the `-1.1` variants, so resolution
    // lands on exactly the files v0.14.8 loaded.
    // 2026-08-12: the pin is a FORK BUILD — mrbizarro/ltx-2-mlx
    // `feat/ltx-2.5` @ e6be9d6 (v0.14.19 + the LTX-2.5 port). An existing
    // install has only dgrauet as `origin`, so Update adds the fork remote
    // before it can check the SHA out. Both steps are idempotent: the remote
    // is added only when absent, and re-checking-out the same SHA is a no-op.
    // The ltx-package re-install step below is what actually moves the
    // runtime — a bare checkout leaves the old copy in site-packages.
    // See install.js for why this is a SHA and not a tag.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: [
          "git fetch --tags origin",
          "git remote get-url fork > /dev/null 2>&1 || git remote add fork https://github.com/mrbizarro/ltx-2-mlx.git",
          "git fetch fork feat/ltx-2.5",
          // 3.8.1 HOTFIX — DISCARD LOCAL EDITS BEFORE THE PIN MOVE.
          //
          // This checkout is the most fragile line in the updater, and on
          // 3.8.0 it failed for real users with
          //   error: Your local changes to the following files would be
          //   overwritten by checkout:
          //     packages/ltx-core-mlx/src/ltx_core_mlx/model/video_vae/video_vae.py
          // No retry could clear it — nothing in the flow ever un-dirties the
          // file, so the Update button was dead for good.
          //
          // WHY EVERY INSTALL IS DIRTY. ltx-2-mlx is a uv WORKSPACE, and
          // install.js installs its members WITH deps and WITHOUT --reinstall,
          // so uv links them EDITABLE: site-packages gets
          // `_editable_impl_ltx_core_mlx.pth`, not a copy. patch_ltx_codec.py
          // then resolves through VENV_ROOTS, finds no ltx_core_mlx directory
          // in site-packages, and falls through to `packages/ltx-core-mlx/src`
          // — the GIT-TRACKED source. So a perfectly successful install ends
          // with a modified tracked file, every single time. (An install that
          // DIED at the package step lands in the same place for a different
          // reason: nothing in site-packages at all.) The v0.14.8 -> 871694d
          // diff rewrites the very ffmpeg lines that patch edits, so git
          // refuses to move the pin. Same class, not the same SHA: ANY
          // modified tracked file that differs across a pin move blocks it.
          //
          // WHY reset --hard AND NOT `git checkout -- <file>`. This tree is
          // APP-MANAGED end to end: models live in ../mlx_models, the build
          // constraints in ../pip-build-constraints.txt, and on a real install
          // the only non-tracked thing inside it is the ignored env/ venv —
          // which reset --hard does not touch, so the multi-GB venv survives.
          // A surgical per-file revert would only fix today's filename.
          //
          // SAFE BECAUSE OF WHAT FOLLOWS. The uv step below reinstalls the
          // three packages with --reinstall --no-deps, which REPLACES the
          // editable .pth links with real copies in site-packages; the codec
          // patch further down then re-applies there, so the runtime imports a
          // patched video_vae.py and this tree stays clean for the NEXT pin
          // move. Proven end to end, from a real v0.14.8 editable install.
          // The guard stops reset --hard ever firing in a shell that did not
          // land in the vendored tree.
          "if [ -f packages/ltx-core-mlx/pyproject.toml ]; then echo 'vendored tree is app-managed; discarding local edits before the pin move:'; git status --porcelain | head -20; git reset --hard HEAD; else echo 'WARN: not the vendored ltx-2-mlx tree - skipping reset'; fi",
          "git checkout e6be9d61848b712516469fd9d44d20d18716a8bc",
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
        // 3.8.2: uv, not the venv's pip. Every plain-pip step in this file is
        // now a uv step, for one reason: mlx-vlm is deliberately installed
        // --no-deps, so its metadata is permanently unsatisfiable, and pip
        // runs a dependency-consistency pass after ANY install that actually
        // changes something. That pass prints
        //   ERROR: pip's dependency resolver does not currently take into
        //   account all the packages that are installed...
        // even when the install succeeded and pip exits 0 (measured). The
        // mflux step below already switched to uv for exactly this noise —
        // "this is what made cocktailpeanut's update look broken even though
        // mflux installed fine". On the owner's v3.8.1 run it was worse than
        // cosmetic: the Pinokio run ENDED at the certifi step that emitted
        // that block (session log: last `step: 7` of 18) and every remaining
        // step was skipped in silence. uv does the same installs and emits
        // none of it. One lane, one failure mode.
        message: "uv pip install --python ./ltx-2-mlx/env/bin/python --reinstall --no-deps 'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1'"
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
    //
    // `--build-constraints` pins the wheel BUILD backend (hatchling<1.32).
    // All three upstream pyprojects declare `readme = "../../README.md"` — a
    // path outside the package dir — which hatchling 1.32.0 turned into a
    // hard error ("Readme path must be within the project directory" →
    // metadata-generation-failed). The build runs in an isolated env that
    // pulls the newest backend from PyPI, so from the day 1.32.0 shipped
    // EVERY Update click died here, on every pinned tag — a moving
    // third-party dependency breaking a frozen source tree, not a pin
    // regression. See pip-build-constraints.txt.
    //
    // THIS STEP USES uv, NOT pip, AND THAT IS THE FIX. The obvious spelling
    // is `PIP_CONSTRAINT=... ./env/bin/pip install ...`, which is what the
    // pip docs used to recommend. It does not work on current pip: when pip
    // installs build dependencies it now spawns that sub-install with
    // `_PIP_IN_BUILD_IGNORE_CONSTRAINTS=1`, deliberately ignoring the
    // environment constraint (verified against pip 26.1 and 26.2.1 — the
    // build env resolved hatchling 1.32.0 and the install failed with the
    // readme error anyway, with the file at both a relative and an absolute
    // path). pip's supported spelling is the newer `--build-constraint`
    // flag, but that flag does not exist on older pips, and this step runs
    // on installs whose venv was seeded years apart — passing it would turn
    // a working Update into "no such option" for them. uv takes the
    // constraint on every version we ship with, needs no pip at all, and is
    // already assumed present by the uv steps further down this file. It is
    // also now literally the same mechanism install.js uses, which is the
    // point: one lane, one failure mode.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "uv pip install --python env/bin/python --reinstall --no-deps --build-constraints ../pip-build-constraints.txt ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer"
      }
    },
    // ---- Re-apply the codec patch — HERE, not at the bottom (3.8.2) --------
    //
    // This step used to sit eleven steps down, after litellm / smolagents /
    // mflux / the IC-LoRA fetches. On the owner's own machine the v3.8.1 run
    // executed steps 0-7 and STOPPED — Pinokio's own session log records
    // `step: 7` as the last one, the `certifi` step, whose plain-pip output
    // carried pip's "ERROR: pip's dependency resolver does not currently take
    // into account..." block (a side effect of mlx-vlm being installed
    // --no-deps). Everything after it, including this patch, silently never
    // ran, and the update still presented as finished. Corroborated on that
    // install: no smolagents, no mflux, no mlx-teacache, and an UNPATCHED
    // video_vae.py in site-packages — i.e. every render encoded 4:2:0.
    //
    // So: the patch runs IMMEDIATELY after the reinstall that replaces
    // site-packages, before a single optional package step can end the run.
    // Ordering is the fix; the pip->uv conversion below removes the trigger;
    // and patch_ltx_codec.py now verifies its own work and exits non-zero
    // with a banner if the file the runtime imports lacks the patch. Three
    // independent lines of defence, because the failure is invisible: an
    // unpatched install renders perfectly happily, just with blocky faces.
    //
    // It MUST stay after the uv reinstall above — that step overwrites
    // site-packages, so a patch applied before it would be thrown away.
    {
      method: "shell.run",
      params: { message: "./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py" }
    },
    // 3.0: pyyaml + pydantic + tqdm + rich are ltx-trainer-mlx's
    // transitive deps. We just installed ltx-trainer with --no-deps so
    // those weren't resolved. Install them explicitly (idempotent — pip
    // skips when already present).
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "uv pip install --python env/bin/python 'pyyaml>=6.0' 'pydantic>=2.0' 'tqdm>=4.65' 'rich>=13.0'"
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
        message: "uv pip install --python env/bin/python --no-deps 'mlx-vlm==0.4.4'"
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
        message: "uv pip install --python ./ltx-2-mlx/env/bin/python --upgrade 'hf_transfer>=0.1.6'"
      }
    },
    // 2026-05-31 review fix (E3): ensure certifi is present on every update.
    // start.js points SSL_CERT_FILE at certifi's cacert.pem (v3.0.4 fix); if
    // certifi is ever absent, that path vanishes and ALL panel stdlib HTTPS
    // breaks. Naming it explicitly keeps the cert bundle guaranteed-present.
    {
      method: "shell.run",
      params: {
        message: "uv pip install --python ./ltx-2-mlx/env/bin/python --upgrade certifi"
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
        message: "uv pip install --python ./ltx-2-mlx/env/bin/python --upgrade 'litellm>=1.83.14'"
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
    // The mflux FBCache patch stays HERE — it has to, because it patches
    // mflux, which is installed by the step directly above. (The codec patch
    // that used to sit alongside it moved up to run right after the ltx
    // package reinstall; see the 3.8.2 note there for why.) Idempotent —
    // skips when its marker is already present. Pin to the venv's python3.11
    // to match install.js: `python3` on Pinokio hosts isn't guaranteed to be
    // 3.11, or even present on PATH.
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

    // ---- LTX-2.5, the default generation (~28 GB) ------------------------
    // Self-heal existing installs onto the generation the panel now boots
    // into. Anyone who installed before 2026-08-12 has 2.3 weights and no 2.5
    // ones, and 2.5 is the default — so without this step an Update leaves
    // them on a default lane with nothing behind it.
    //
    // Mirrored as GitHub release assets, not on HuggingFace (our own
    // quantisation of a gated upstream, read-only HF token), so this is
    // scripts/fetch_pack_release.py rather than `hf download`. It verifies
    // every file it already has and re-downloads only what is missing, which
    // makes the steady-state cost of this step a read pass.
    //
    // BEST-EFFORT, unlike install.js: an Update must never be bricked by a
    // network hiccup, and the Models page's Download button now drives this
    // exact same fetcher, so the user has a one-click retry.
    {
      method: "shell.run",
      params: {
        message: [
          "echo 'Ensuring the LTX-2.5 weights are present (default generation)…'",
          "./ltx-2-mlx/env/bin/python3.11 scripts/fetch_pack_release.py \\",
          "  --repo-key q4_25 --repo-key gemma4_25 \\",
          "|| echo 'WARN: LTX-2.5 weight fetch failed — open the panel, go to Models, and click Download for the LTX 2.5 rows to retry. It resumes.'"
        ].join("\n")
      }
    },

    // ---- Colorize IC-LoRA (restore mode, ~0.3 GB, un-gated) --------------
    // Self-heal existing installs onto the Colorize restore feature. UN-GATED
    // community weights (no HF token). BEST-EFFORT — a network hiccup must not
    // fail the update; the worker falls back to the repo id, and the panel
    // surfaces Repair. hf skips it when already present (fast verify).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Ensuring the Colorize IC-LoRA is present (restore mode, optional)…' && \\",
          "hf download DoctorDiffusion/LTX-2.3-IC-LoRA-Colorizer --local-dir ../mlx_models/loras/ic --include 'LTX-2.3-22b-IC-LoRA-Colorizer-0.9.safetensors' \\",
          "|| echo 'WARN: Colorize IC-LoRA fetch failed — the Colorize mode will fetch it on first use, or click Repair.'"
        ].join("\n")
      }
    },

    // ---- Ingredients IC-LoRA (multi-reference mode, ~1.3 GB, un-gated) ----
    // Self-heal existing installs onto the flagship Ingredients feature. The
    // official weight is GATED; DeepBeepMeep/LTX-2 mirrors the BYTE-IDENTICAL
    // file un-gated (no HF token). BEST-EFFORT — must not fail the update; the
    // worker self-heals via a targeted single-file fetch, and the panel
    // surfaces Repair. CRITICAL: --include pulls ONLY the one ingredients file
    // (DeepBeepMeep/LTX-2 is a ~708 GB mega-repo). hf skips it when already
    // present (fast verify).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Ensuring the Ingredients IC-LoRA is present (multi-reference mode, optional)…' && \\",
          "hf download DeepBeepMeep/LTX-2 --local-dir ../mlx_models/loras/ic --include 'ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors' \\",
          "|| echo 'WARN: Ingredients IC-LoRA fetch failed — the Ingredients mode will fetch it on first use, or click Repair.'"
        ].join("\n")
      }
    },

    // ---- Control (Union) IC-LoRA (control mode, ~0.65 GB, OFFICIAL un-gated)
    // Self-heal existing installs onto the Control feature. This is the
    // OFFICIAL Lightricks weight and it is UN-GATED + public (no HF token, no
    // mirror, no mega-repo workaround) — a plain single-file fetch like the
    // Colorize one above. BEST-EFFORT — must not fail the update; the worker
    // falls back to the repo id, and the panel surfaces Repair. hf skips it
    // when already present (fast verify).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Ensuring the Control IC-LoRA is present (Union, control mode, optional)…' && \\",
          "hf download Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control --local-dir ../mlx_models/loras/ic --include 'ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors' \\",
          "|| echo 'WARN: Control IC-LoRA fetch failed — the Control mode will fetch it on first use, or click Repair.'"
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
