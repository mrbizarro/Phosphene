#!/usr/bin/env bash
# Python 3.12 / MLX 0.32.2 must never enter LTX's Python 3.11 / MLX 0.31.1 venv.
set -euo pipefail
MUSIC_CHECKOUT="${1:?music checkout required}"
# A pyvenv.cfg can survive a dangling managed-Python symlink. Probe execution.
if ! "$MUSIC_CHECKOUT/.venv/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
  uv venv --clear --python 3.12 "$MUSIC_CHECKOUT/.venv"
fi
