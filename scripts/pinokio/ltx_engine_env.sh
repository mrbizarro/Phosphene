#!/usr/bin/env bash
# THE ENGINE ENVIRONMENT: the Python packages a render needs, installed into
# the engine venv (ltx-2-mlx/env). ONE implementation, three callers:
#   install.js        - Install / Resume Install / the sidebar Repair entry
#   the panel         - "Repair engine" (POST /engine/repair), which re-runs
#                       ONLY this step, no downloads, no venv rebuild
#   by hand           - bash scripts/pinokio/ltx_engine_env.sh
#
# WHY IT IS A SCRIPT NOW (4.17.4 fleet fix). `venv_broken` - "the engine venv
# has a Python but no packages" - on fresh installs, every version: 18
# installs in 30 days, nearly all at the very first boot. This step used to be
# install.js's inline message array, so nothing but Pinokio's /error:/i output
# match decided whether it had worked: the first transient network failure
# ended the whole Install, and nothing could re-run just this step - the only
# repair was the whole install.js, and only with the panel stopped.
#
# WHAT IT DOES
#   * the same five uv commands install.js ran, in the same order (mlx trio +
#     transformers cap, the dependency pass, the --reinstall --no-deps pass that
#     turns the workspace's editable links into real copies, mlx-vlm, the
#     runtime extras);
#   * up to 3 attempts, so a dropped connection is retried, not fatal;
#   * then VERIFIES the result the way the panel judges it
#     (engine_env_check.py: real package directories in site-packages, and an
#     import) - "uv exited 0" is not the question, "can a render start" is;
#   * a lock file (env/.phosphene_engine_install, our pid) for the whole run:
#     the panel reads it to say "still installing" instead of "broken" while a
#     concurrent Install is mid-way, and a second run refuses to start.
#
# PINOKIO SEMANTICS. Pinokio 8.2.0 stops a run when its output matches
# /error:/i or /errno /i, and ignores exit codes. An attempt that will be
# retried must therefore not print uv's own "error:" lines verbatim - they are
# shown with the word defused. The FINAL failure prints "FATAL error:" on
# purpose, so Install stops there instead of downloading 37 GB into a venv that
# cannot render.
#
# Usage: ltx_engine_env.sh [path to the ltx-2-mlx checkout]
set -uo pipefail

APP_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MLX="${1:-$APP_ROOT/ltx-2-mlx}"
cd "$MLX" 2>/dev/null || { echo "FATAL error: no engine checkout at $MLX - run Install"; exit 1; }
PY=env/bin/python
# The engine venv's known-good set (4.19.4): every install below passes it with
# -c, so no resolve can leave tokenizers / transformers / huggingface-hub / the
# mlx trio outside what renders. engine_constraints.txt says why each line is there.
EC="$APP_ROOT/scripts/pinokio/engine_constraints.txt"
[ -f "$EC" ] || { echo "FATAL error: scripts/pinokio/engine_constraints.txt is missing - re-run Update"; exit 1; }

# OUTCOME RECORD (4.17.5). Every finished run writes env/.phosphene_engine_result
# .json: {"outcome","cause","ts"} - closed words only (install_cause.sh), no
# paths. The panel puts `cause` on the install_step engine_env event and in the
# Repair bar, so a failed fresh install says network / disk / uv / Python /
# timeout instead of only "venv_broken". Written only when env/ exists.
# shellcheck source=install_cause.sh
if [ -f "$APP_ROOT/scripts/pinokio/install_cause.sh" ]; then
  . "$APP_ROOT/scripts/pinokio/install_cause.sh"
else
  install_cause() { echo other; }
fi
RESULT=env/.phosphene_engine_result.json
record() {
  [ -d env ] || return 0
  printf '{"outcome":"%s","cause":"%s","ts":%s}\n' "$1" "$2" "$(date +%s)" \
    > "$RESULT" 2>/dev/null
  return 0
}

if ! "$PY" -c 'import sys' >/dev/null 2>&1; then
  record failed python_missing
  echo 'FATAL error: the engine venv has no working Python - run Install (it rebuilds the venv, models kept)'
  exit 1
fi

# The lock is a DIRECTORY: mkdir is atomic, so two runs starting together
# (Pinokio's Install and the panel's Repair) cannot both take it. Our pid
# goes inside; a lock whose pid is gone was left by a killed run and is
# taken over. Only the owner removes it.
LOCK=env/.phosphene_engine_install.d
take_lock() { mkdir "$LOCK" 2>/dev/null && printf '%s\n%s\n' "$$" "$(date +%s)" > "$LOCK/pid"; }
if ! take_lock; then
  # The owner writes its pid a moment AFTER mkdir: give it two seconds.
  other=''
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    other="$(sed -n 1p "$LOCK/pid" 2>/dev/null)"
    [ -n "$other" ] && break
    sleep 0.1
  done
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    echo "FATAL error: another engine install is running (pid $other) - let it finish"
    exit 1
  fi
  rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null
  take_lock || { echo 'FATAL error: another engine install just started - let it finish'; exit 1; }
fi
release_lock() {
  [ "$(sed -n 1p "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -f "$LOCK/pid" && rmdir "$LOCK"
  [ -f "${LOG:-}" ] && rm -f "$LOG"
  [ -n "${EC_COPY:-}" ] && rm -f "$EC_COPY"
  return 0
}
trap release_lock EXIT
# uv 0.11 splits constraints arguments at spaces, even quoted shell arguments.
# Keep the original EC assignment for the native bootstrap compatibility shim.
# A relative temporary name works even when this engine lives in a spaced home.
EC_COPY="$(mktemp env/.phosphene_constraints.XXXXXX)" || {
  echo 'FATAL error: cannot prepare engine constraints - check disk space and retry Repair engine'; exit 1;
}
cp "$EC" "$EC_COPY" || { echo 'FATAL error: cannot copy engine constraints - retry Repair engine'; exit 1; }
EC="$EC_COPY"

# uv's own failure lines, defused for Pinokio's matcher (see above). The raw
# text goes to $LOG first - install_cause reads it after a failed run.
LOG="$(mktemp -t phos_engine_env)" || LOG=/dev/null
defuse() { tee -a "$LOG" | sed -u -e 's/[Ee][Rr][Rr][Oo][Rr]:/problem -/g' -e 's/[Ee]rrno /errno-/g'; }

attempt() {
  uv pip install --python env/bin/python -c "$EC" 'mlx==0.31.1' 'mlx-lm==0.31.1' \
    'mlx-metal==0.31.1' 'transformers>=5.0.0,<5.13.0' 2>&1 | defuse || return 1
  # The packages' dependencies resolve WITH the pins, the same as
  # scripts/post_update.sh step 3 (4.19.0). Alone, this resolve took the newest
  # mlx-arsenal (ltx-core-mlx asks only >=0.2.4); 0.12.0 onward (2026-08-24)
  # requires mlx>=0.32.1, so it moved mlx to 0.32.3, and the mflux pack then
  # walked it DOWN to 0.31.2 (mflux 0.18.0 wants <0.32) - every fresh install
  # since late August ran mlx 0.31.2 with an mlx-arsenal that declares it
  # cannot. Pinned here the solver picks mlx-arsenal 0.11.1 and mlx stays 0.31.1.
  uv pip install --python env/bin/python -c "$EC" --build-constraints ../pip-build-constraints.txt \
    'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1' 'transformers>=5.0.0,<5.13.0' \
    ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer 2>&1 | defuse || return 1
  uv pip install --python env/bin/python -c "$EC" --reinstall --no-deps \
    --build-constraints ../pip-build-constraints.txt \
    ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer 2>&1 | defuse || return 1
  uv pip install --python env/bin/python -c "$EC" --no-deps 'mlx-vlm==0.4.4' 2>&1 | defuse || return 1
  uv pip install --python env/bin/python -c "$EC" certifi pillow numpy 'huggingface-hub>=1.5.0,<2.0' \
    'hf_transfer>=0.1.6' 'litellm>=1.83.14' 'smolagents>=1.24.0' 'pywebpush>=2.0' 2>&1 | defuse || return 1
  # The --reinstall above replaced the patched ltx_core_mlx: put the codec patch
  # back before anything renders (install.js runs it again after; idempotent).
  "$PY" "$APP_ROOT/patch_ltx_codec.py" 2>&1 | defuse || return 1
  "$PY" "$APP_ROOT/scripts/pinokio/engine_env_check.py" 2>&1 | defuse || return 1
}

echo '=== engine environment: installing the render packages ==='
"$PY" --version 2>&1
n=1
while :; do
  echo "--- attempt $n of 3 ---"
  if attempt; then
    record ok ""
    echo 'engine environment ready - ltx_core_mlx, ltx_pipelines_mlx and mlx import'
    exit 0
  fi
  [ "$n" -ge 3 ] && break
  wait_s=$(( n * ${PHOSPHENE_ENGINE_RETRY_WAIT:-15} ))
  echo "attempt $n did not finish - retrying in ${wait_s} s (a network drop is the usual cause)"
  sleep "$wait_s"
  n=$(( n + 1 ))
done
CAUSE="$(install_cause "$LOG")"
record failed "$CAUSE"
case "$CAUSE" in
  disk)     WHY='the disk is full - free some space' ;;
  timeout)  WHY='downloads kept timing out - check the connection' ;;
  network)  WHY='the package server could not be reached - check the network' ;;
  uv_error) WHY='the package installer could not resolve or build a package' ;;
  python_missing) WHY='the engine venv Python stopped working' ;;
  *)        WHY='see the lines above' ;;
esac
echo "FATAL error: the engine environment did not install after 3 attempts ($CAUSE: $WHY)."
echo 'Run Install again - it resumes, nothing is downloaded twice - or press Repair engine in the panel.'
echo 'Nothing was deleted; every model is kept.'
exit 1

# ---- WHY EACH COMMAND ABOVE IS WHAT IT IS ------------------------------------
# Moved verbatim from install.js's inline step (4.17.4), where these notes sat
# beside the commands they explain. The diagnostics echoes became the
# python --version line and engine_env_check.py.
#
# v2.0.3: log Python identity before each pip step. KTDS hit a
# silent missing-package install and we had nothing in the log
# to diagnose it. These echoes leave a paper trail of which
# interpreter is being targeted by --python env/bin/python.
#   (install.js ran: "echo '=== install diagnostics: pip install ==='")
#   (install.js ran: "env/bin/python --version || echo 'venv python NOT executable'")
#   (install.js ran: "env/bin/python -c 'import sys; print(\"sys.executable:\", sys.executable); print(\"sys.path[0]:\", sys.path[0] if sys.path else None)'")
#   (install.js ran: "echo '=== /diagnostics ==='")
# Force the mlx pin BEFORE installing ltx-* packages so their deps
# resolve to the pinned version instead of pulling latest 0.31.x.
#
# SHIP-BLOCKER (2026-07-10, GitHub #40/#38/#37/#33): also pin
# transformers <5.13.0. mlx-lm 0.31.1 declares `transformers>=5.0.0`
# with NO upper bound, so any fresh install after transformers 5.13.0
# dropped (~Jul 9) pulls 5.13.0 — which breaks mlx_lm.tokenizer_utils.
# EVERY generation then crashes with "'str' object has no attribute
# '__module__'": the Gemma text-encoder load silently no-ops ("done in
# 0.0s") → downstream "Model not loaded. Call load() first." Known-good:
# 5.7.0 (our validated build) and 5.12.x. Cap it on the SAME resolve as
# mlx-lm so the constraint sticks. Diagnosed by @saved-j + @xandreau.
#
# The Update path enforces this too — scripts/post_update.sh step 2b,
# a `require` (fatal) step. That sentence used to live here as "uv
# downgrades an already-installed 5.13.0 on the next Update" and was
# simply false for a month: nothing in the update path constrained
# transformers at all, so an existing 5.13.0 survived every Update and
# the install stayed unable to generate anything. A promise about
# another file now names the step that keeps it.
#   (install.js ran: "uv pip install --python env/bin/python -c "$EC" 'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1' 'transformers>=5.0.0,<5.13.0'")
# Y3 — Train Character ships in 3.0. Without ltx-trainer-mlx in
# the venv, the trainer subprocess fails at `import yaml` because
# pyyaml is a transitive dep of ltx-trainer (declared in its
# pyproject). Codex pre-ship review 2026-05-18 caught this.
#
# `--build-constraints ../pip-build-constraints.txt` pins the wheel
# BUILD backend (hatchling<1.32). Upstream's three pyprojects all
# declare `readme = "../../README.md"` — a path outside the package
# dir — which hatchling 1.32.0 turned into a hard error
# ("Readme path must be within the project directory" →
# metadata-generation-failed). uv resolves the build backend fresh
# from PyPI into an isolated env, so from the day 1.32.0 shipped
# this step failed for every NEW install on every pinned tag. See
# pip-build-constraints.txt; update.js runs the same uv command
# (it used to spell it `PIP_CONSTRAINT=`, which modern pip ignores
# by design — one lane now, one failure mode).
#   (install.js ran: "uv pip install --python env/bin/python -c "$EC" --build-constraints ../pip-build-constraints.txt ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packag)
# v4.0 — THE SECOND PASS IS THE POINT, and it closes a trap that has
# shipped since the workspace landed. `ltx-2-mlx` is a uv WORKSPACE:
# the line above (with deps, without --reinstall) links its members
# EDITABLE. site-packages gets `_editable_impl_ltx_core_mlx.pth`
# instead of a copy, so `import ltx_core_mlx` resolves to
# `packages/ltx-core-mlx/src/...` — the GIT-TRACKED source. The codec
# patch further down then finds no ltx_core_mlx directory in
# site-packages and patches the tracked file, which is why a
# PERFECTLY SUCCESSFUL install ended with
#     M packages/ltx-core-mlx/src/.../video_vae.py
# every single time — and why the v3.8.0 pin move hit "your local
# changes would be overwritten by checkout" for the whole fleet.
#
# v3.8.1 made the pin move survive that (reset --hard first) and
# v3.8.1's own notes filed this as the follow-up: cure the cause, so
# a FRESH install no longer starts dirty. `--reinstall --no-deps`
# replaces the .pth links with real copies and re-resolves nothing;
# it is the exact command update.js has run for many releases, so
# the end state is one every install already converges to on its
# first Update. One lane, one runtime shape.
#   (install.js ran: "uv pip install --python env/bin/python -c "$EC" --reinstall --no-deps --build-constraints ../pip-build-constraints.txt ./packages/ltx-core-mlx ./packages/ltx-)
# Auto-caption (Gemma 3 12B via mlx-vlm) needs the mlx-vlm
# package. Pinned to 0.4.4 — caption_with_gemma.py's import
# surface (load, generate, prompt_utils.apply_chat_template)
# is stable at that version. --no-deps so we don't drag in
# mlx-vlm's heavy default deps (PIL>=10, av, etc. that fight
# mflux/transformers pins). The runtime imports it lazily so
# a partial install doesn't break the rest of the panel.
#   (install.js ran: "uv pip install --python env/bin/python -c "$EC" --no-deps 'mlx-vlm==0.4.4'")
# hf_transfer is HuggingFace's Rust-based downloader — 5-10× faster
# than the default Python downloader for big repos like Q8 (~25 GB).
# The panel sets HF_HUB_ENABLE_HF_TRANSFER=1 in download envs; if the
# package is missing the hf CLI falls back gracefully with a warning.
# litellm: agent's chat client (multi-provider router for OpenAI /
# Anthropic / Ollama / mlx-lm.server). Pinned to >=1.83.14 — the
# March 2026 PyPI supply-chain incident affected earlier 1.x
# releases (stole SSH keys via a poisoned post-install script).
# See agent/engine.py for routing details. Falls back to stdlib
# urllib if missing — safe to omit but the loop is less robust.
#
# smolagents: Phase 2 of the agent-layer refactor. Powers
# the optional CodeAgent runtime in agent/runtime_smol.py,
# selectable per-request via PHOSPHENE_RUNTIME=smol. smolagents
# pulls transformers as a transitive dep — the huggingface-hub
# floor is bumped to >=1.5.0 to satisfy transformers' pin.
# smolagents itself ships with a pessimistic <1.0 hub pin that
# is empirically benign in practice.
#
# The hub pin range we settle on (>=1.5.0,<2.0) satisfies:
#   - mflux>=0.17.5            wants >=1.1.6,<2.0
#   - transformers (5.7.0+)    wants >=1.5.0,<2.0
#   - smolagents 1.24.0        warns about <1.0 but works
#   - hf download CLI          needs v1+ for the new command name
# 2026-05-31 review fix (E3): pin `certifi` explicitly. start.js
# points SSL_CERT_FILE at certifi's cacert.pem (the v3.0.4 fix for
# the CivitAI CERTIFICATE_VERIFY_FAILED on uv-Python). certifi was
# only ever a transitive dep — if a future dep change drops it, the
# SSL_CERT_FILE path vanishes and ALL panel stdlib HTTPS breaks.
# Naming it here keeps the cert bundle guaranteed-present.
#   (install.js ran: "uv pip install --python env/bin/python -c "$EC" certifi pillow numpy 'huggingface-hub>=1.5.0,<2.0' 'hf_transfer>=0.1.6' 'litellm>=1.83.14' 'smolagents>=1.24.0)
# v2.0.3: post-install confirmation that the local packages
# actually landed in site-packages. The Y1.034+ patch script's
# i2v target tolerates a missing ltx_pipelines_mlx — without
# this echo we'd discover the gap only at panel start time.
#   (install.js ran: "echo '=== post-pip site-packages check ==='")
#   (install.js ran: "ls env/lib/python3.11/site-packages/ | grep -E '^(ltx|mlx)' || echo 'WARN: no ltx_*/mlx packages in site-packages'")
#   (install.js ran: "echo '=== /site-packages check ==='")
