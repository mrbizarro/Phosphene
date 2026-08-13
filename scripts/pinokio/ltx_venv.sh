#!/usr/bin/env bash
# Force a Python 3.11 venv for the LTX engine, self-healing.
#
# THIS IS THE STEP THAT HUNG PINOKIO (#50 reopened by @davidaircloud, #56 by
# @itrejomx). Its joined dispatch was 1,417 chars — @davidaircloud measured
# exactly that number on 3.8.0 and fixed his own install by doing precisely
# what this file does. Every individual line was already short; that was never
# what mattered. See scripts/pinokio/README.md.
#
#   cwd : ltx-2-mlx
#
# SEMANTICS: the original was a "\n"-joined string message, so it ran WITHOUT
# `set -e` and the step's exit code was the LAST command's. Preserved here —
# no `set -e`, same line order, same final command. The only intentional exit
# is the macOS preflight's `exit 1`.
#
# WHY IT IS NOT GATED ON `when: !exists(env)`. `uv venv` builds the interpreter
# as a symlink chain into Pinokio's SHARED managed Python:
#     env/bin/python3.11 -> python
#     env/bin/python     -> <pinokio>/cache/XDG_DATA_HOME/uv/python/
#                           cpython-3.11-macos-aarch64-none/bin/python3.11
# That target is Pinokio's, not ours. Any other pack install — or any other
# Pinokio app — that makes uv re-resolve, bump or prune the managed interpreter
# leaves the chain DANGLING while env/ still exists. A path-existence guard
# cannot handle that state: depending on whether it stats the link or its
# target, a dangling chain reads as present (rebuild silently skipped, broken
# venv that re-running Install can never fix) or absent. So we ask the only
# question that matters — does the interpreter RUN? — and rebuild when it
# doesn't. `python3.11` specifically, not `python`: a legacy conda-3.10 venv
# has a working `python` and no `python3.11`, and 3.11 is the exact interpreter
# every later step invokes. Healthy installs pay ~50 ms; broken ones rebuild in
# ~5 min with zero re-downloads.

echo '=== LTX venv check ==='
if env/bin/python3.11 -c 'import sys' >/dev/null 2>&1; then
echo 'venv healthy - reusing it'
else
echo 'venv missing or broken (wrong Python or dangling interpreter) - rebuilding, no models are re-downloaded'
echo '=== install diagnostics: venv create ==='
# SHIP-BLOCKER GUARD (2026-07-23, from @hottboytank's Pinokio report): mlx
# 0.31.1 publishes NO macOS-13 wheel — its builds start at macosx_14_0_arm64.
# On Ventura the mlx step dies deep inside uv's resolver and the user only sees
# a cryptic ModuleNotFoundError at the very end. Fail fast, with the real fix.
# FAIL-OPEN BY DESIGN: only block when we positively identify a macOS major
# < 14. If sw_vers is missing, unparseable or non-numeric, we PROCEED — a
# preflight that can't read the version must never brick an otherwise-fine
# install.
MACOS_VER=$(sw_vers -productVersion 2>/dev/null)
MACOS_MAJOR=$(echo "$MACOS_VER" | cut -d. -f1)
if echo "$MACOS_MAJOR" | grep -qE '^[0-9]+$' && [ "$MACOS_MAJOR" -lt 14 ]; then
  echo '=================================================================='
  echo "PHOSPHENE CANNOT INSTALL ON macOS $MACOS_VER"
  echo 'Phosphene needs macOS 14 (Sonoma) or newer.'
  echo 'Why: mlx 0.31.1 ships no macOS 13 build, so the engine cannot install'
  echo 'and every generation would fail.'
  echo 'Fix: System Settings > General > Software Update -> macOS 14 or 15,'
  echo 'then reinstall Phosphene.'
  echo '=================================================================='
  exit 1
else
  echo "macOS preflight OK (detected '$MACOS_VER', needs >= 14)"
fi
which uv && uv --version || echo 'uv NOT FOUND'
which python3.11 && python3.11 --version || echo 'system python3.11 NOT FOUND (uv will try to fetch)'
uname -a
echo '=== /diagnostics ==='
rm -rf env
uv venv --python 3.11 --seed env
echo '=== venv created ==='
ls -la env/bin/python* 2>&1 || echo 'venv create FAILED'
env/bin/python --version || echo 'venv python NOT executable'
fi
