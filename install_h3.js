// Phosphene — optional Hailuo H3 engine pack. Idempotent.
//
// H3 (MiniMax-H3 FL2VA) is Phosphene's SECOND video engine: one prompt in,
// video + synced dialogue + sound out. It is deliberately NOT part of the base
// install:
//
//   * ~75 GB of weights on top of the ~36 GB base — most users never want it.
//   * It needs ~40 GiB resident at peak, so it only runs on 64 GB+ Macs.
//   * MiniMax Community License — territory restrictions apply, so it must be
//     an explicit opt-in, not something that lands with the app.
//
// It also gets its OWN venv rather than sharing ltx-2-mlx/env, because the two
// engines disagree about MLX: Phosphene pins mlx==0.31.1 (0.31.2 regresses LTX
// audio by 22 dB), while the H3 port needs mlx>=0.32. Separate venvs mean an
// H3 install can never break LTX rendering, and removing H3 is `rm -rf`.
//
// Everything below is safe to re-run:
//   clone   — skipped when minimax-h3-mlx/.git exists
//   venv    — skipped when .venv/bin/python3.11 exists
//   uv pip  — already-installed packages are no-ops
//   weights — hf_hub_download resumes partial files, skips intact ones
//
// The panel discovers the result on its next /status tick (a couple of
// seconds) and unlocks the engine picker. No restart.

// The branch carrying the validated staged runner (scripts/generate_staged.py).
// NOTE: `--first-frame` (FL2VA image conditioning, i.e. Image mode on H3)
// landed AFTER this branch was published. The panel probes the installed
// runner for the flag and keeps Image mode on LTX when it's absent, so an
// older checkout degrades to Text-only instead of failing mid-render. Bump
// this pin when the first-frame work is published.
const H3_BRANCH = "codex/h3-engine"

module.exports = {
  requires: { bundle: "ai" },
  run: [
    {
      method: "notify",
      params: {
        html: "<b>Installing Hailuo H3 (optional, ~75 GB).</b><br>A second video engine that generates picture, dialogue and sound together. Needs a 64 GB+ Apple Silicon Mac. MiniMax Community License — territory restrictions apply. Resumable if interrupted."
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
          "MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null); if echo \"$MEM_BYTES\" | grep -qE '^[0-9]+$' && [ \"$MEM_BYTES\" -lt 60000000000 ]; then MEM_GB=$((MEM_BYTES / 1000000000)); echo '=================================================================='; echo \"HAILUO H3 NEEDS ABOUT 64 GB OF UNIFIED MEMORY (this Mac has ${MEM_GB} GB)\"; echo 'The staged runner peaks around 40 GiB with one component resident at'; echo 'a time. Below 64 GB it swaps and a 3-second clip takes hours.'; echo 'Nothing was downloaded. Keep using the built-in LTX-2.3 engine.'; echo '=================================================================='; exit 1; else echo 'H3 memory preflight OK'; fi",
          "echo 'free disk space:'",
          "df -h ."
        ]
      }
    },

    // ---- Clone the engine --------------------------------------------------
    // Guarded: `git clone` into an existing directory fails and would abort
    // the whole install on a re-run / resume.
    {
      when: "{{!exists('minimax-h3-mlx/.git')}}",
      method: "shell.run",
      params: {
        message: [
          "git clone --branch " + H3_BRANCH + " https://github.com/mrbizarro/minimax-h3-mlx.git minimax-h3-mlx"
        ]
      }
    },

    // Pin to the branch on every run, so Resume Install after a partial clone
    // lands on the same code the panel was validated against.
    {
      method: "shell.run",
      params: {
        path: "minimax-h3-mlx",
        message: [
          "git fetch origin " + H3_BRANCH,
          "git checkout " + H3_BRANCH,
          "git rev-parse --short HEAD"
        ]
      }
    },

    // ---- Its own Python 3.11 venv -----------------------------------------
    // Same reasoning as install.js: Pinokio's `venv:` shortcut builds from
    // conda-base (currently 3.10 on the macOS bundle), which can't install the
    // MLX wheels. Force 3.11 with uv, then target it explicitly on every pip
    // step. If a wrong-Python venv is already there, nuke it — it holds no
    // user data.
    {
      when: "{{!exists('minimax-h3-mlx/.venv/bin/python3.11')}}",
      method: "shell.run",
      params: {
        path: "minimax-h3-mlx",
        message: [
          "echo '=== H3 venv create ==='",
          "which uv && uv --version || echo 'uv NOT FOUND'",
          "rm -rf .venv",
          "uv venv --python 3.11 --seed .venv",
          ".venv/bin/python --version || echo 'venv python NOT executable'"
        ]
      }
    },

    {
      method: "shell.run",
      params: {
        path: "minimax-h3-mlx",
        message: [
          "echo '=== H3 dependencies ==='",
          "uv pip install --python .venv/bin/python -r requirements.txt",
          ".venv/bin/python -c \"import mlx.core as mx, numpy, PIL; print('H3 deps OK')\""
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
        path: "minimax-h3-mlx",
        env: {
          HF_HOME: "{{cwd}}/cache/HF_HOME",
          HF_XET_HIGH_PERFORMANCE: "1"
        },
        message: [
          ".venv/bin/python scripts/download_selected.py --root '{{cwd}}/mlx_models/hailuo-h3'"
        ]
      }
    },

    {
      method: "notify",
      params: {
        html: "<b>Hailuo H3 ready.</b><br>Open the panel — the Video tab now has an <b>Engine</b> row: LTX-2.3 | Hailuo H3. H3 serves Text and Image, and generates dialogue + sound from the same prompt, so write them into it."
      }
    }
  ]
}
