#!/usr/bin/env bash
# One checkout implementation for Install and Update. The SHA has one home.
set -euo pipefail
MUSIC_PIN_FILE="$(cd "$(dirname "$0")/../music" && pwd)/engine_pin.txt"
MUSIC_PIN=$(cat "$MUSIC_PIN_FILE")
MUSIC_CHECKOUT="${1:?music checkout required}"
[ -d "$MUSIC_CHECKOUT/.git" ] || { echo 'No music engine checkout to pin'; exit 1; }
cd "$MUSIC_CHECKOUT"
# Fetch succeeds before a forced checkout; app-managed source, external weights.
git fetch --force origin "$MUSIC_PIN"
git checkout --force --detach FETCH_HEAD
[ "$(git rev-parse HEAD)" = "$MUSIC_PIN" ]
echo "YuE2 engine pinned to $MUSIC_PIN"
