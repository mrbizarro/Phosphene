#!/usr/bin/env bash
# The Hailuo H3 venv's packages, installed - or put back - at the known-good set
# in scripts/pinokio/h3_constraints.txt. THE ONE implementation: install_h3.js
# (Install, Repair Hailuo H3, Update Hailuo H3 runner), every Update
# (scripts/post_update.sh 8b) and the panel's own Repair (POST /h3/repair) all
# run this file and nothing else installs into the H3 venv.
#
# WHY (4.19.5). Through 4.19.4 the H3 venv was the one engine venv with no
# lock at all: `uv pip install -r requirements.txt`, whose lines are floors
# only (mlx>=0.32.0, transformers>=4.57, huggingface_hub>=0.30, ...). Every
# fresh install resolved whatever PyPI held that day, so the next upstream
# release - the way tokenizers 0.23.3 broke LTX renders in 4.19.3 - would have
# reached every new H3 install with no Phosphene release, no gate and no way
# back. Under -c the same resolve can only land on the set this release was
# validated with, and uv MOVES a package that already sits outside it (checked:
# a drifted transformers / mlx-vlm / tokenizers comes back on this run), so
# this file is the repair as well as the install.
#
# Usage: bash scripts/pinokio/h3_deps.sh <h3-checkout-dir>
# Exit 0 = the venv holds the set and the runner's imports load. Non-zero =
# a "FATAL error:" line says why (Pinokio 8.2 ignores the exit code and stops a
# run only when the output matches /error:/i or /errno /i).
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
HC="$HERE/h3_constraints.txt"
H3_DIR="${1:-}"
echo '=== H3 dependencies (known-good set: scripts/pinokio/h3_constraints.txt) ==='
if [ "$(uname -s)" != Darwin ]; then
  # The set is macOS arm64 wheels (mlx-metal, cp311). Linux on Apple Silicon
  # builds its H3 venv in scripts/linux/install_h3.sh with its own mlx wheel.
  echo 'H3 known-good set is for macOS - skipped here (scripts/linux/install_h3.sh owns this venv).'
  exit 0
fi
[ -f "$HC" ] || { echo "FATAL error: $HC is missing - the app update did not land. Re-run Update."; exit 1; }
if [ -z "$H3_DIR" ] || [ ! -f "$H3_DIR/requirements.txt" ]; then
  echo "FATAL error: no H3 engine checkout at '${H3_DIR:-<none>}' - run Install Hailuo H3."
  exit 1
fi
cd "$H3_DIR" || { echo "FATAL error: cannot open the Hailuo H3 engine folder ($H3_DIR) - run Install Hailuo H3."; exit 1; }
if ! .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo 'FATAL error: the H3 venv has no working Python - run Repair Hailuo H3 from the Pinokio sidebar (weights kept).'
  exit 1
fi
# ONE INSTALL AT A TIME (Codex 4.19.5): Pinokio's Install / Update and the
# panel's Repair can all reach this venv. The lock is a directory (mkdir is
# atomic) holding our pid; the panel reads it too and holds queued H3 renders
# while it is live. A lock whose pid is gone is a killed run's leftover.
LOCK=.venv/.phosphene_h3_deps.d
if ! mkdir "$LOCK" 2>/dev/null; then
  other="$(head -1 "$LOCK/pid" 2>/dev/null)"
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    echo "FATAL error: another install into the H3 venv is running (pid $other) - let it finish, then try again."
    exit 1
  fi
  echo 'An earlier H3 package install was interrupted - taking over.'
fi
echo "$$" > "$LOCK/pid"
HC_COPY=''
trap 'rm -f "$LOCK/pid"; rmdir "$LOCK" 2>/dev/null; [ -z "$HC_COPY" ] || rm -f "$HC_COPY"' EXIT
# uv splits even quoted constraints paths at spaces. Use a relative snapshot.
HC_COPY="$(mktemp .venv/.phosphene_constraints.XXXXXX)" || {
  echo 'FATAL error: cannot prepare H3 constraints - check disk space and retry Repair'; exit 1;
}
cp "$HC" "$HC_COPY" || { echo 'FATAL error: cannot copy H3 constraints - retry Repair'; exit 1; }
HC="$HC_COPY"
if ! uv pip install --python .venv/bin/python -c "$HC" -r requirements.txt; then
  echo 'FATAL error: the H3 packages did not install - the lines above say why (network, disk).'
  exit 1
fi
.venv/bin/python "$HERE/h3_env_check.py" "$HC" || {
  echo 'FATAL error: the H3 venv is still not on its known-good set after the install.'
  exit 1
}
