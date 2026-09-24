#!/usr/bin/env bash
# The H3 engine's own Python 3.11 venv — self-healing. cwd: the H3 checkout.
# Lifted out of install_h3.js (a 372-char dispatch, above the ~350 Pinokio 8 is
# proven to survive) when that step had to learn the relocated checkout; the
# reasoning for probing the interpreter instead of a path lives above the step
# in install_h3.js.
echo '=== H3 venv check ==='
if .venv/bin/python -c 'import sys' >/dev/null 2>&1; then
  echo 'H3 venv healthy - reusing it'
else
  echo 'H3 venv missing or broken (dangling interpreter) - rebuilding, no weights are re-downloaded'
  which uv && uv --version || echo 'uv NOT FOUND'
  rm -rf .venv
  uv venv --python 3.11 --seed .venv
  .venv/bin/python --version || echo 'venv python NOT executable'
fi
