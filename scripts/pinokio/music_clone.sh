#!/usr/bin/env bash
# `set -e` here and a "FATAL error:" line on every failure: Pinokio 8.2 ignores a
# shell.run step's exit code and stops a script only when the step's OUTPUT
# matches /error:/i or /errno /i (pinokiod kernel/shells.js default `on`
# handlers), and git's own "fatal: unable to access ..." has neither.
set -euo pipefail
if [ "${PHOSPHENE_DISTRIBUTION:-}" = mac-app ]; then
  exec "${LTX_HELPER_PYTHON:-python3}" scripts/pinokio/music_app_source.py
fi
MUSIC_CHECKOUT="${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}"
if [ ! -d "$MUSIC_CHECKOUT/.git" ]; then
  git clone --no-checkout https://github.com/vanch007/mlx-Yue "$MUSIC_CHECKOUT" || {
    echo "FATAL error: could not download the YuE2 music engine (github.com/vanch007/mlx-Yue). Check your internet connection (a VPN, proxy or blocked GitHub is the usual cause) and free disk space, then click Install again."
    exit 1
  }
fi
