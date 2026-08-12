// Phosphene install — idempotent.
//
// Pinokio will re-run this whenever the user clicks "Install" or "Resume
// Install" (the latter fires when env_ready && !base_models_ready, see
// pinokio.js). Every step below is safe to repeat:
//
//   - clone:       skipped if ltx-2-mlx/.git exists
//   - venv:        reused when its interpreter actually runs; rebuilt when
//                  it doesn't (self-healing — see that step's note)
//   - uv pip:      idempotent (already-installed packages are no-ops)
//   - patch:       patch_ltx_codec.py is idempotent + fails loud on drift
//   - hf download: resumes partial files, skips intact ones
//
// If the user's first install died after the venv was created but before
// the model downloads, hitting Resume Install picks up exactly where it
// left off without re-downloading the working pieces.
//
// IMPORTANT: we do NOT use Pinokio's `venv: "env"` directive to CREATE the
// venv — that uses conda-base's Python (currently 3.10 on the macOS bundle)
// which fails the ltx-core-mlx Python>=3.11 constraint. We force 3.11 with
// `uv venv --python 3.11` and then use `--python env/bin/python` on every
// pip step. The hf download steps still use `venv: "env"` for activation
// only (sourcing the existing 3.11 venv to put `hf` on PATH).

module.exports = {
  // Pulls in `huggingface-cli`/`hf`, `ffmpeg`, `git`, `uv`, `python3.11` etc.
  requires: { bundle: "ai" },
  run: [
    // ---- Apple Silicon gate ------------------------------------------------
    {
      when: "{{platform !== 'darwin' || arch !== 'arm64'}}",
      method: "notify",
      params: {
        html: "<b>Phosphene requires an Apple Silicon Mac (M1 or newer).</b><br>It will not run on Intel Macs, Linux, or Windows."
      },
      next: null
    },

    // ---- Persistent storage via fs.link (Y1.004+) -------------------------
    // Pinokio's fs.link maps these directories to a virtual drive that
    // lives OUTSIDE the panel install dir, so a Reset (which deletes
    // and re-clones the install) leaves the heavy assets intact. After
    // Reset → Install, fs.link re-creates the symlinks back into the
    // fresh clone and the drive is rediscovered automatically.
    //
    // What's in the drive:
    //   mlx_models/    LTX 2.3 weights (~36 GB), Gemma encoder, LoRAs
    //   mlx_outputs/   generated videos
    //   panel_uploads/ user-uploaded reference images
    //   state/         panel_settings.json, panel_queue.json, panel_hidden.json
    //
    // What's NOT linked:
    //   ltx-2-mlx/env/ — venv has historically been buggy under fs.link
    //                    (Python-version-restricted, pip mismatches);
    //                    rebuilds in ~5 min anyway. Models are the
    //                    expensive thing to lose, not the venv.
    //
    // First-run merge: if real folders already exist with content (e.g.
    // an upgrade from Y1.003-), fs.link merges them INTO the drive
    // before replacing them with symlinks. Idempotent on repeat runs.
    {
      method: "fs.link",
      params: {
        drive: {
          mlx_models:    "mlx_models",
          mlx_outputs:   "mlx_outputs",
          panel_uploads: "panel_uploads",
          state:         "state"
        }
      }
    },

    // ---- Clone ltx-2-mlx (skip if already cloned) -------------------------
    // Re-running install when the clone exists used to fail with
    // "destination path 'ltx-2-mlx' already exists and is not an empty
    // directory", aborting the whole install. Guard with `when:` so the
    // step is a no-op on Resume Install.
    {
      when: "{{!exists('ltx-2-mlx/.git')}}",
      method: "shell.run",
      params: {
        message: ["git clone https://github.com/dgrauet/ltx-2-mlx.git ltx-2-mlx"]
      }
    },

    // ---- STOP LOUDLY if that clone did not land ---------------------------
    // Everything below this point assumes ltx-2-mlx/ exists: the version pin
    // runs with `path: "ltx-2-mlx"`, the venv is built inside it, every pip
    // step targets it. When the clone fails — no network, a VPN/proxy, GitHub
    // unreachable, a full disk — none of those steps abort the run. Each one
    // just spawns a shell into a directory that isn't there, prints nothing
    // useful, and the install marches on for another dozen steps before dying
    // for a reason that has long since scrolled off screen.
    //
    // That silence is what made the v3.5.0 restart loop unreadable: the
    // console filled with anonymous "Starting Shell / Terminated Shell" pairs
    // (Pinokio gives every command in a `message` array its own shell) and the
    // single line that explained anything was thousands of lines up. Fail
    // here, name the cause, and stop.
    {
      when: "{{!exists('ltx-2-mlx/.git')}}",
      method: "notify",
      params: {
        html: "<b>Install stopped: the video engine could not be downloaded.</b><br>Phosphene needs to download <code>github.com/dgrauet/ltx-2-mlx</code> and that step did not finish.<br><br>This is almost always a network problem (no connection, a VPN or proxy, or GitHub blocked) or a full disk. Check your connection and free space, then click <b>Install</b> again — anything already downloaded is kept and will not be downloaded twice."
      },
      next: null
    },

    // ---- ltx-2-mlx version: PIN to v0.14.19 (2026-08-11 catch-up). dgrauet asked on
    //      2026-05-12 to lock onto a tag because he pushes breaking changes
    //      upstream to sync with the official Lightricks repo. Without a
    //      tag pin, every fresh install (and every Update) would pull the
    //      next breaking push and Phosphene would fail to start.
    //
    //      v0.14.19 is the pinned tag against which the current panel +
    //      helper + patch_ltx_codec.py are validated (2026-08-11 catch-up
    //      from v0.14.8, 11 releases). What it brings, in the order it
    //      matters to us:
    //        - 0.14.11 — the AV cross-attention gate is finally read FROM the
    //          checkpoint (`av_ca_timestep_scale_multiplier` 1000, not the
    //          dataclass default 1). Audio/dialogue weighting changes for
    //          every render, toward upstream parity. This also lands
    //          `LTXModelConfig.from_checkpoint_dir()` — a metadata-driven
    //          config path we need for any second model generation.
    //        - 0.14.15 — `frame_rate` forwarded at the A2V/lipdub call sites
    //          (our `_install_a2v_frame_rate_patch` shim is now inert there),
    //          and muxed video is no longer truncated to the shortest stream.
    //        - 0.14.16 — quantized transformers load at ANY group_size, not
    //          just 64; ic-lora dev mode fuses the distilled LoRA correctly.
    //        - 0.14.19 — macOS GPU-watchdog kills are explained instead of
    //          dying cryptically; the DiT is freed before VAE decode in
    //          low-memory mode; the Gemma encoder stops widening a stricter
    //          cache limit.
    //      Weight layout is UNCHANGED by this bump. 0.14.13+ prefers a
    //      versioned `transformer-distilled-*.safetensors` when one exists
    //      and falls back to the unversioned name — our shipped model dirs
    //      have no `-1.1` files (update.js trims them), so resolution is
    //      byte-identical to v0.14.8. No re-download, no manifest change.
    //
    //      STAY PINNED here: do not auto-track upstream main. Tag-bumps are a
    //      deliberate decision — read his release notes, smoke-test the full
    //      modality matrix on dev, bump `_LTX_EXPECTED_VERSION` in
    //      mlx_warm_helper.py, then bump this pin AND update.js in one commit.
    //
    //      Idempotent — works on a fresh clone (already on the cloned
    //      branch's tip) AND on a re-install where the clone exists.
    //      2026-08-12 — THE PIN IS NOW A FORK BUILD, NOT AN UPSTREAM TAG.
    //      Vendored: mrbizarro/ltx-2-mlx `feat/ltx-2.5` @ 871694d, which is
    //      v0.14.19 (1192051) plus the LTX-2.5 port — keyframe pos-emb, the
    //      vendored Gemma 4 tower, the Euler-ancestral sampler, the duration
    //      head. dgrauet has no 2.5 branch; if he ports it we drop ours and
    //      go back to a tag.
    //
    //      This is what lets LTX-2.5 render THROUGH the panel. v0.14.19 could
    //      register the 2.5 packs but never load them: it does not build
    //      keyframes_abs_pos_embedding and cannot construct a Gemma 4 tower.
    //
    //      Every 2.5 flag defaults to its 2.3 value, so an unversioned
    //      checkpoint builds exactly what it built before — proven here by
    //      802/22 on the vendored suite, a byte-identical job-dict capture
    //      across 18 form shapes, and a real 2.3 draft render.
    //
    //      The packages report `0.14.19+ltx25.1`, and `_LTX_EXPECTED_VERSION`
    //      in mlx_warm_helper.py must equal that string. The local segment is
    //      the ONLY thing distinguishing this tree from upstream v0.14.19 at
    //      runtime — the release segment is deliberately unchanged. Move the
    //      two together.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: [
          "git fetch --tags origin",
          "git remote get-url fork > /dev/null 2>&1 || git remote add fork https://github.com/mrbizarro/ltx-2-mlx.git",
          "git fetch fork feat/ltx-2.5",
          "git checkout 871694ddaa09c1598d663a49005a2f91ae6b4ed2",
          "git rev-parse --short HEAD"
        ]
      }
    },

    // ---- Force Python 3.11 venv (SHIP-BLOCKER fix) ------------------------
    // Pinokio's `venv: "env"` shortcut creates a venv using whatever python
    // is on `conda activate base` — on machines where conda's base env is
    // Python 3.10 (the current macOS bundle reality), that venv has no
    // python3.11 and the MLX packages refuse to install:
    //   "ltx-core-mlx==0.2.0 depends on Python>=3.11"
    //
    // Worse, that error doesn't abort the install — Pinokio happily moves on
    // to download 35 GB of weights into a broken venv. So we explicitly
    // create the venv with `uv venv --python 3.11` before any pip step.
    //
    // SELF-HEALING, and deliberately NOT gated on `when: !exists(...)` —
    // the same latent bug fixed for Hailuo H3 in v3.4.0's "installed other
    // packs and H3 vanished" regression (install_h3.js, 3c9bdf6). `uv venv`
    // creates the interpreter as a symlink chain into Pinokio's SHARED
    // managed Python (verified — this venv has the identical chain):
    //     env/bin/python3.11 -> python
    //     env/bin/python     -> <pinokio>/cache/XDG_DATA_HOME/uv/python/
    //                           cpython-3.11-macos-aarch64-none/bin/python3.11
    // That target is Pinokio's, not ours. Any other pack install — or any
    // other Pinokio app — that makes uv re-resolve, bump or prune the
    // managed interpreter leaves the chain DANGLING while env/ still
    // exists. A path-existence guard cannot handle that state: depending on
    // whether the check stats the link or its target, a dangling chain
    // reads as either present (rebuild silently skipped → a broken venv
    // that re-running Install can never fix) or absent. So we ask the only
    // question that matters — does the interpreter RUN? — inside the
    // shell, and rebuild only when it doesn't. python3.11 specifically
    // (not python): a legacy conda-3.10 venv (the failure mode above) has
    // a working `python` but no `python3.11`, so probing python3.11 keeps
    // that rebuild path too — and it is the exact interpreter every later
    // step invokes. Healthy installs pay ~50 ms; broken ones rebuild in
    // ~5 min with zero re-downloads. (Unlike H3, Reset+Install could
    // already recover this venv — reset.js deletes ltx-2-mlx/ wholesale —
    // but a user should never need Reset for it; env/ holds no user data.)
    //
    // One joined multi-line message (the mflux-step idiom below), so the
    // whole if/else runs as a single shell compound.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: [
          "echo '=== LTX venv check ==='",
          "if env/bin/python3.11 -c 'import sys' >/dev/null 2>&1; then",
          "echo 'venv healthy - reusing it'",
          "else",
          "echo 'venv missing or broken (wrong Python or dangling interpreter) - rebuilding, no models are re-downloaded'",
          // v2.0.3: log the toolchain BEFORE creating the venv so the
          // install log self-documents which Python uv landed on. A user
          // (KTDS) hit a silent "ModuleNotFoundError: ltx_pipelines_mlx"
          // after a green install — the most likely cause was uv falling
          // back to a Python that couldn't install mlx wheels, and we
          // had no log evidence to confirm. These echoes change nothing
          // operationally; they just leave a trail.
          "echo '=== install diagnostics: venv create ==='",
          // SHIP-BLOCKER GUARD (2026-07-23, from @hottboytank's Pinokio report):
          // mlx 0.31.1 publishes NO macOS-13 wheel — its builds start at
          // macosx_14_0_arm64. On Ventura the mlx step dies deep inside uv's
          // resolver ("No solution found ... no wheels with a matching platform
          // tag"), ltx_core_mlx never installs, and the user only sees a cryptic
          // ModuleNotFoundError at the very end — under an auto-generated title
          // about python3.11 that points at completely the wrong thing.
          // Fail fast, up front, with the actual fix.
          // FAIL-OPEN BY DESIGN: only block when we positively identify a
          // macOS major < 14. If sw_vers is missing, unparseable, or non-numeric
          // for any reason, we PROCEED — a preflight that can't read the version
          // must never be the thing that bricks an otherwise-fine install.
          // Pinokio 8.0.x wedges its shell on very long single lines (#50: 764
          // chars hangs, ~373 passes — bisected by @Morac2). Every line below
          // stays well under that; they run in ONE shell invocation joined by
          // \n, so the if/else and variables still span lines.
          [
            "MACOS_VER=$(sw_vers -productVersion 2>/dev/null)",
            "MACOS_MAJOR=$(echo \"$MACOS_VER\" | cut -d. -f1)",
            "if echo \"$MACOS_MAJOR\" | grep -qE '^[0-9]+$' && [ \"$MACOS_MAJOR\" -lt 14 ]; then",
            "  echo '=================================================================='",
            "  echo \"PHOSPHENE CANNOT INSTALL ON macOS $MACOS_VER\"",
            "  echo 'Phosphene needs macOS 14 (Sonoma) or newer.'",
            "  echo 'Why: mlx 0.31.1 ships no macOS 13 build, so the engine cannot install'",
            "  echo 'and every generation would fail.'",
            "  echo 'Fix: System Settings > General > Software Update -> macOS 14 or 15,'",
            "  echo 'then reinstall Phosphene.'",
            "  echo '=================================================================='",
            "  exit 1",
            "else",
            "  echo \"macOS preflight OK (detected '$MACOS_VER', needs >= 14)\"",
            "fi",
          ].join("\n"),
          "which uv && uv --version || echo 'uv NOT FOUND'",
          "which python3.11 && python3.11 --version || echo 'system python3.11 NOT FOUND (uv will try to fetch)'",
          "uname -a",
          "echo '=== /diagnostics ==='",
          "rm -rf env",
          "uv venv --python 3.11 --seed env",
          "echo '=== venv created ==='",
          "ls -la env/bin/python* 2>&1 || echo 'venv create FAILED'",
          "env/bin/python --version || echo 'venv python NOT executable'",
          "fi"
        ].join("\n")
      }
    },

    // ---- Install MLX pipeline packages into the 3.11 venv -----------------
    // `--python env/bin/python` pins the install to the venv we just made
    // (avoids any conda-base interference). uv pip install is idempotent —
    // already-installed packages are no-ops on Resume Install.
    // Non-editable install (no -e): packages get copied into
    // env/lib/python3.11/site-packages/ which is where patch_ltx_codec.py
    // looks for video_vae.py.
    // Pin huggingface-hub>=1.0 explicitly so older Pinokio bundles still
    // get the v1+ `hf` CLI used by the download steps below.
    //
    // SHIP-BLOCKER: pin mlx==0.31.1 (NOT 0.31.2). LTX 2.3 audio regresses
    // by 22 dB on mlx 0.31.2 — output peaks at -37 dB instead of the
    // expected -9 to -15 dB. Verified empirically by downgrading mlx in a
    // working install and re-running the same prompt:
    //   mlx 0.31.2 → max_volume -42.8 dB (broken)
    //   mlx 0.31.1 → max_volume -9.2  dB (working)
    // Same packages, same weights, same seed; only mlx differs. Numerical
    // change in 0.31.2 attenuates the vocoder output.
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: [
          // v2.0.3: log Python identity before each pip step. KTDS hit a
          // silent missing-package install and we had nothing in the log
          // to diagnose it. These echoes leave a paper trail of which
          // interpreter is being targeted by --python env/bin/python.
          "echo '=== install diagnostics: pip install ==='",
          "env/bin/python --version || echo 'venv python NOT executable'",
          "env/bin/python -c 'import sys; print(\"sys.executable:\", sys.executable); print(\"sys.path[0]:\", sys.path[0] if sys.path else None)'",
          "echo '=== /diagnostics ==='",
          // Force the mlx pin BEFORE installing ltx-* packages so their deps
          // resolve to the pinned version instead of pulling latest 0.31.x.
          //
          // SHIP-BLOCKER (2026-07-10, GitHub #40/#38/#37/#33): also pin
          // transformers <5.13.0. mlx-lm 0.31.1 declares `transformers>=5.0.0`
          // with NO upper bound, so any fresh install after transformers 5.13.0
          // dropped (~Jul 9) pulls 5.13.0 — which breaks mlx_lm.tokenizer_utils.
          // EVERY generation then crashes with "'str' object has no attribute
          // '__module__'": the Gemma text-encoder load silently no-ops ("done in
          // 0.0s") → downstream "Model not loaded. Call load() first." Known-good:
          // 5.7.0 (our validated build) and 5.12.x. Cap it on the SAME resolve as
          // mlx-lm so the constraint sticks (uv downgrades an already-installed
          // 5.13.0 on the next Update). Diagnosed by @saved-j + @xandreau.
          "uv pip install --python env/bin/python 'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1' 'transformers>=5.0.0,<5.13.0'",
          // Y3 — Train Character ships in 3.0. Without ltx-trainer-mlx in
          // the venv, the trainer subprocess fails at `import yaml` because
          // pyyaml is a transitive dep of ltx-trainer (declared in its
          // pyproject). Codex pre-ship review 2026-05-18 caught this.
          //
          // `--build-constraints ../pip-build-constraints.txt` pins the wheel
          // BUILD backend (hatchling<1.32). Upstream's three pyprojects all
          // declare `readme = "../../README.md"` — a path outside the package
          // dir — which hatchling 1.32.0 turned into a hard error
          // ("Readme path must be within the project directory" →
          // metadata-generation-failed). uv resolves the build backend fresh
          // from PyPI into an isolated env, so from the day 1.32.0 shipped
          // this step failed for every NEW install on every pinned tag. See
          // pip-build-constraints.txt; update.js carries the pip equivalent.
          "uv pip install --python env/bin/python --build-constraints ../pip-build-constraints.txt ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer",
          // Auto-caption (Gemma 3 12B via mlx-vlm) needs the mlx-vlm
          // package. Pinned to 0.4.4 — caption_with_gemma.py's import
          // surface (load, generate, prompt_utils.apply_chat_template)
          // is stable at that version. --no-deps so we don't drag in
          // mlx-vlm's heavy default deps (PIL>=10, av, etc. that fight
          // mflux/transformers pins). The runtime imports it lazily so
          // a partial install doesn't break the rest of the panel.
          "uv pip install --python env/bin/python --no-deps 'mlx-vlm==0.4.4'",
          // hf_transfer is HuggingFace's Rust-based downloader — 5-10× faster
          // than the default Python downloader for big repos like Q8 (~25 GB).
          // The panel sets HF_HUB_ENABLE_HF_TRANSFER=1 in download envs; if the
          // package is missing the hf CLI falls back gracefully with a warning.
          // litellm: agent's chat client (multi-provider router for OpenAI /
          // Anthropic / Ollama / mlx-lm.server). Pinned to >=1.83.14 — the
          // March 2026 PyPI supply-chain incident affected earlier 1.x
          // releases (stole SSH keys via a poisoned post-install script).
          // See agent/engine.py for routing details. Falls back to stdlib
          // urllib if missing — safe to omit but the loop is less robust.
          //
          // smolagents: Phase 2 of the agent-layer refactor. Powers
          // the optional CodeAgent runtime in agent/runtime_smol.py,
          // selectable per-request via PHOSPHENE_RUNTIME=smol. smolagents
          // pulls transformers as a transitive dep — the huggingface-hub
          // floor is bumped to >=1.5.0 to satisfy transformers' pin.
          // smolagents itself ships with a pessimistic <1.0 hub pin that
          // is empirically benign in practice.
          //
          // The hub pin range we settle on (>=1.5.0,<2.0) satisfies:
          //   - mflux>=0.17.5            wants >=1.1.6,<2.0
          //   - transformers (5.7.0+)    wants >=1.5.0,<2.0
          //   - smolagents 1.24.0        warns about <1.0 but works
          //   - hf download CLI          needs v1+ for the new command name
          // 2026-05-31 review fix (E3): pin `certifi` explicitly. start.js
          // points SSL_CERT_FILE at certifi's cacert.pem (the v3.0.4 fix for
          // the CivitAI CERTIFICATE_VERIFY_FAILED on uv-Python). certifi was
          // only ever a transitive dep — if a future dep change drops it, the
          // SSL_CERT_FILE path vanishes and ALL panel stdlib HTTPS breaks.
          // Naming it here keeps the cert bundle guaranteed-present.
          "uv pip install --python env/bin/python certifi pillow numpy 'huggingface-hub>=1.5.0,<2.0' 'hf_transfer>=0.1.6' 'litellm>=1.83.14' 'smolagents>=1.24.0'",
          // v2.0.3: post-install confirmation that the local packages
          // actually landed in site-packages. The Y1.034+ patch script's
          // i2v target tolerates a missing ltx_pipelines_mlx — without
          // this echo we'd discover the gap only at panel start time.
          "echo '=== post-pip site-packages check ==='",
          "ls env/lib/python3.11/site-packages/ | grep -E '^(ltx|mlx)' || echo 'WARN: no ltx_*/mlx packages in site-packages'",
          "echo '=== /site-packages check ==='"
        ]
      }
    },

    // ---- Apply patches (idempotent, fails loud on upstream drift) ---------
    // Codec → yuv444p crf 0 (lossless), I2V OOM cleanup before VAE decode.
    // Patch script exits non-zero if it can't find expected text — that
    // surfaces upstream-restructure problems instead of silently shipping
    // broken installs to users (deep-review recommendation).
    {
      method: "shell.run",
      params: {
        message: ["./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py"]
      }
    },

    // ---- Sanity-import the pipeline packages (v2.0.2+) --------------------
    // SHIP-BLOCKER guard: at least one user (KTDS, May 4) reported a
    // "ModuleNotFoundError: No module named 'ltx_pipelines_mlx'" after a
    // green Pinokio install — the upstream pip step had silently failed
    // mid-install but the patch script's i2v target check tolerates a
    // missing ltx_pipelines_mlx file (demotes MISSING → ALREADY for that
    // specific patch), so the install reported success and the user only
    // learned about the breakage when they clicked Generate.
    //
    // This step imports both packages explicitly. If either is missing
    // the Python call exits non-zero, Pinokio marks the install step as
    // failed, and the user sees an actionable error instead of a 30 GB
    // download into a broken venv. Idempotent — costs ~300ms on a working
    // install.
    //
    // v2.0.5: stripped the print('venv OK: ...') decoration. KTDS (and one
    // other Twitter user) hit a SyntaxError on v2.0.4 where the literal
    // `OK:` was being mangled out of the Python string by something between
    // install.js and the executed shell line — `OK:` got cut from inside
    // and `OK` got appended after the closing shell quote, so Python saw
    // `...importable')OK` and bailed. The exit code from a successful
    // `import` is already the only success signal `shell.run` needs; the
    // print was decorative. Keeping the line minimal sidesteps whatever the
    // rewriter is doing.
    {
      method: "shell.run",
      params: {
        message: [
          "./ltx-2-mlx/env/bin/python3.11 -c \"import ltx_core_mlx, ltx_pipelines_mlx, mlx\""
        ]
      }
    },

    // ---- Download Q4 LTX 2.3 (~20 GB, resumable) --------------------------
    // `hf download` is the v1+ name (huggingface_hub deprecated `huggingface-cli`).
    // Resume + verify is built-in; --local-dir avoids the HF cache store so
    // the panel can point at the path directly with no symlink chase.
    // On Resume Install with base files already complete, this is a fast
    // verify pass (~seconds) — `hf` checks each file's hash and skips.
    //
    // Y1.024: explicit --include allowlist. dgrauet's Q4 repo hosts multiple
    // transformer variants (transformer-distilled, -distilled-1.1, -dev),
    // duplicate distilled LoRAs (-384, -384-1.1), and the x1.5/temporal
    // upscalers we don't use. Without filters `hf download` grabs the full
    // 56 GB tree; the panel only needs ~20 GB. Keep this list in sync with
    // required_files.json → repos[q4].download_include.
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        // Y1.022: HF_HUB_ENABLE_HF_TRANSFER=1 enables the Rust accelerator,
        // ~5-10× faster on 20 GB. Falls back gracefully if hf_transfer
        // isn't yet on disk (warning + plain Python downloader).
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          // Short lines over shell continuations — Pinokio 8.0.x hangs on very
          // long single lines (#50); still one hf invocation.
          [
            "hf download dgrauet/ltx-2.3-mlx-q4 --local-dir ../mlx_models/ltx-2.3-mlx-q4 \\",
            "  --include '*.json' --include 'transformer-distilled.safetensors' \\",
            "  --include 'connector.safetensors' \\",
            "  --include 'vae_decoder.safetensors' --include 'vae_encoder.safetensors' \\",
            "  --include 'audio_vae.safetensors' --include 'vocoder.safetensors' \\",
            "  --include 'spatial_upscaler_x2_v1_1.safetensors'",
          ].join("\n")
        ]
      }
    },

    // ---- (no transformer.safetensors symlink needed on HEAD — 0.2.0 reads
    //      split_model.json to resolve transformer-distilled.safetensors.
    //      Symlink was a workaround for the dcd639e pin we no longer use.) ----

    // ---- Download Gemma 4-bit text encoder (~6 GB) ------------------------
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "hf download mlx-community/gemma-3-12b-it-4bit --local-dir ../mlx_models/gemma-3-12b-it-4bit"
        ]
      }
    },

    // ---- Image-engine pack (mflux: Ideogram 4 + Qwen-Edit) ----------------
    // 2026-06-13: Ideogram 4 + the visual text-placement editor are headline
    // features now, so the mflux image-engine runner ships by default instead
    // of only behind the optional "Install Qwen-Image-Edit" action. (Reported
    // by cocktailpeanut: token saved + regions drawn but Generate stayed
    // disabled because mflux — and thus mflux-generate-ideogram4 — was absent.)
    // Safe to bundle: install.js already pins huggingface-hub to a range mflux
    // requires, and mflux lives in the same venv as the LTX stack. BEST-EFFORT —
    // a pip hiccup must not fail the core (video) install; the panel surfaces a
    // one-click "Reinstall image engines" path if it didn't land. Two-step
    // (with-deps then --no-deps) mirrors install_qwen.js so transitive deps
    // resolve, then the version is locked + the FBCache patch applied.
    {
      method: "shell.run",
      params: {
        message: [
          "echo 'Installing the mflux image-engine pack (Ideogram 4 + Qwen-Edit)…' && \\",
          // uv, NOT plain pip: mlx-vlm is installed --no-deps (to dodge its
          // PIL>=10/av pins), which leaves its declared extras unsatisfied. Plain
          // `pip install` then dumps a scary "ERROR: pip's dependency resolver…"
          // block about mlx-vlm on EVERY later install — verified, and it made
          // cocktailpeanut's update look broken. uv installs the same packages
          // with zero such noise (same reason the base deps above use uv).
          "( uv pip install --python ./ltx-2-mlx/env/bin/python 'mflux==0.18.0' && \\",
          "  uv pip install --python ./ltx-2-mlx/env/bin/python --reinstall --no-deps 'mflux==0.18.0' && \\",
          "  uv pip install --python ./ltx-2-mlx/env/bin/python 'mlx-teacache==0.4.1' && \\",
          "  ./ltx-2-mlx/env/bin/python3.11 patch_mflux_fbcache.py ) \\",
          "|| echo 'WARN: mflux image-engine install hit an error — video still works; use the Reinstall image engines action to retry image generation.'"
        ].join("\n")
      }
    },

    // ---- Colorize IC-LoRA (~0.3 GB, un-gated community weights) -----------
    // Powers the Colorize restore mode (B&W clip → color). UN-GATED, so no
    // HF token is needed — but BEST-EFFORT regardless: a network hiccup (or a
    // future gating change) must NEVER fail the core video install. The panel
    // surfaces the same Repair path if the file didn't land, and the worker
    // falls back to resolving the repo id at first use. Lands the file at
    // mlx_models/loras/ic/ (matches required_files.json → repos[ic_colorize]
    // + CURATED_LORAS["colorize"].local_path).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Fetching the Colorize IC-LoRA (restore mode, ~0.3 GB, optional)…' && \\",
          "hf download DoctorDiffusion/LTX-2.3-IC-LoRA-Colorizer --local-dir ../mlx_models/loras/ic --include 'LTX-2.3-22b-IC-LoRA-Colorizer-0.9.safetensors' \\",
          "|| echo 'WARN: Colorize IC-LoRA fetch failed — video + image still work; the Colorize mode will fetch it on first use, or click Repair.'"
        ].join("\n")
      }
    },

    // ---- Ingredients IC-LoRA (~1.3 GB, un-gated mirror) -------------------
    // Powers the flagship Ingredients mode (2-8 refs → one composed clip).
    // The official Lightricks weight is GATED; DeepBeepMeep/LTX-2 carries the
    // BYTE-IDENTICAL file un-gated, so no HF token is needed. BEST-EFFORT: a
    // hiccup must never fail the core video install. CRITICAL: --include pulls
    // ONLY the one ingredients file — DeepBeepMeep/LTX-2 is a ~708 GB mega-repo,
    // so a bare `hf download` of it would be catastrophic. The worker falls
    // back to a targeted single-file fetch at first use; the panel surfaces
    // Repair. Lands the file at mlx_models/loras/ic/ (matches
    // required_files.json → repos[ic_ingredients] +
    // CURATED_LORAS["ingredients"].local_path).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Fetching the Ingredients IC-LoRA (multi-reference mode, ~1.3 GB, optional)…' && \\",
          "hf download DeepBeepMeep/LTX-2 --local-dir ../mlx_models/loras/ic --include 'ltx-2.3-22b-ic-lora-ingredients-0.9.safetensors' \\",
          "|| echo 'WARN: Ingredients IC-LoRA fetch failed — video + image still work; the Ingredients mode will fetch it on first use, or click Repair.'"
        ].join("\n")
      }
    },

    // ---- Control (Union) IC-LoRA (~0.65 GB, OFFICIAL + un-gated) ----------
    // Powers the Control mode (drive motion/structure/composition from a
    // control video). This is the OFFICIAL Lightricks weight AND it is UN-GATED
    // + public, so — unlike Ingredients — no token, no mirror, no mega-repo
    // workaround: a plain single-file `hf download --include`, exactly like the
    // Colorize fetch above. BEST-EFFORT: a hiccup must never fail the core
    // video install. The worker falls back to resolving the repo id at first
    // use; the panel surfaces Repair. Lands the file at mlx_models/loras/ic/
    // (matches required_files.json → repos[ic_union_control] +
    // CURATED_LORAS["union-control"].local_path).
    {
      method: "shell.run",
      params: {
        venv: "env",
        path: "ltx-2-mlx",
        env: { HF_HUB_ENABLE_HF_TRANSFER: "1" },
        message: [
          "echo 'Fetching the Control IC-LoRA (Union, control mode, ~0.65 GB, optional)…' && \\",
          "hf download Lightricks/LTX-2.3-22b-IC-LoRA-Union-Control --local-dir ../mlx_models/loras/ic --include 'ltx-2.3-22b-ic-lora-union-control-ref0.5.safetensors' \\",
          "|| echo 'WARN: Control IC-LoRA fetch failed — video + image still work; the Control mode will fetch it on first use, or click Repair.'"
        ].join("\n")
      }
    },

    // ---- Done -------------------------------------------------------------
    {
      method: "notify",
      params: {
        html: "<b>Phosphene installed</b> — video + image generation (incl. Ideogram 4).<br>Click <b>Start</b> to launch the panel, then <b>Open Panel</b>."
      }
    }
  ]
}
