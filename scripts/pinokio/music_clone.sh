#!/usr/bin/env bash
set -euo pipefail
MUSIC_CHECKOUT="${LTX_MUSIC_ROOT:-$PWD/yue2-mlx}"
if [ ! -d "$MUSIC_CHECKOUT/.git" ]; then
  git clone --no-checkout https://github.com/vanch007/mlx-Yue "$MUSIC_CHECKOUT"
fi
