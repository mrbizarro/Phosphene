#!/usr/bin/env bash
# `set -e` here and a "FATAL error:" line on every failure: Pinokio 8.2 ignores a
# shell.run step's exit code and stops a script only when the step's OUTPUT
# matches /error:/i or /errno /i (pinokiod kernel/shells.js default `on`
# handlers), and git's own "fatal: unable to access ..." has neither.
set -euo pipefail
MUSIC_CHECKOUT="${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}"
if [ "${PHOSPHENE_DISTRIBUTION:-}" = mac-app ] || [ ! -d "$MUSIC_CHECKOUT/.git" ]; then
  SOURCE_SCRIPT="$(cd "$(dirname "$0")" && pwd)/music_app_source.py"
  [ -f "$SOURCE_SCRIPT" ] || {
    echo "FATAL error: the music source installer is missing. Run Update, then Install again."
    exit 1
  }
  exec "${LTX_HELPER_PYTHON:-python3}" "$SOURCE_SCRIPT"
fi
