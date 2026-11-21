#!/usr/bin/env bash
# One checkout implementation for Install and Update. The SHA has one home.
#
# Every failure prints one "FATAL error:" line: Pinokio 8.2 ignores a shell.run
# step's exit code and stops a script only when the step's OUTPUT matches
# /error:/i or /errno /i (pinokiod kernel/shells.js default `on` handlers).
# Update keeps its music step optional: post_update.sh runs this file through
# `defang`, which turns the FATAL line into a WARN.
set -euo pipefail
fatal() { echo "FATAL error: $*"; exit 1; }
MUSIC_PIN_FILE="$(cd "$(dirname "$0")/../music" && pwd)/engine_pin.txt"
[ -s "$MUSIC_PIN_FILE" ] || fatal "the YuE2 engine version file is missing ($MUSIC_PIN_FILE) - the Phosphene download is incomplete; run Update, then try again."
MUSIC_PIN=$(cat "$MUSIC_PIN_FILE")
[[ "$MUSIC_PIN" =~ ^[0-9a-f]{40}$ ]] || fatal "the YuE2 engine version file is invalid - run Update, then try again."
MUSIC_CHECKOUT="${1:-}"
[ -n "$MUSIC_CHECKOUT" ] || fatal "no music engine folder was given to the checkout step - report this line."
install_archive() {
  local source_script="$(dirname "$MUSIC_PIN_FILE")/../pinokio/music_app_source.py"
  [ -f "$source_script" ] || fatal "the music source installer is missing. Run Update, then Install again. Downloaded models are kept."
  LTX_MUSIC_ROOT="$MUSIC_CHECKOUT" "${LTX_HELPER_PYTHON:-python3}" "$source_script" || exit $?
}
if [ "${PHOSPHENE_DISTRIBUTION:-}" = mac-app ] || [ -f "$MUSIC_CHECKOUT/.phosphene_music_source" ]; then
  # Also refresh an archive checkout on Update when the pinned commit moves.
  install_archive
  [ "$(cat "$MUSIC_CHECKOUT/.phosphene_music_source" 2>/dev/null)" = "$MUSIC_PIN" ] && [ -f "$MUSIC_CHECKOUT/pyproject.toml" ] || fatal "the verified music engine source is incomplete; click Install again. Downloaded models are kept."
  echo "YuE2 engine pinned to $MUSIC_PIN"
  exit 0
fi
[ -d "$MUSIC_CHECKOUT/.git" ] || fatal "the YuE2 music engine is not on this Mac ($MUSIC_CHECKOUT is missing) - its download did not finish. Click Install again with a working internet connection."
cd "$MUSIC_CHECKOUT"
# The archive fallback runs from here too; keep a relative caller's target
# from being interpreted a second time beneath the engine folder.
MUSIC_CHECKOUT="$PWD"
# Resume can reach this step after the code is already complete and only the
# model download was interrupted. Do not make those users fetch the same code
# again: a temporary GitHub failure must not block their model download.
if ! git cat-file -e "${MUSIC_PIN}^{commit}" 2>/dev/null; then
  # The missing commit goes through the same bounded GitHub/archive fallback
  # as a new install. Preserve this checkout's environment and model files.
  install_archive
  exit 0
fi
# Use the verified pin itself, never a stale FETCH_HEAD left by another fetch.
# An already complete checkout needs no index write on a resumed download.
if [ "$(git rev-parse HEAD 2>/dev/null || true)" != "$MUSIC_PIN" ] || ! git diff --quiet "$MUSIC_PIN" --; then
  git checkout --force --detach "$MUSIC_PIN" || fatal "could not restore the downloaded YuE2 engine code (${MUSIC_PIN:0:7}). Another install, a locked code folder or insufficient disk space can prevent this. Quit Phosphene, reopen it, then click Install again. Downloaded models are kept."
fi
[ "$(git rev-parse HEAD)" = "$MUSIC_PIN" ] || fatal "the YuE2 engine is on the wrong version ($(git rev-parse --short HEAD), not ${MUSIC_PIN:0:7}) - click Install again; if it repeats, report this line."
echo "YuE2 engine pinned to $MUSIC_PIN"
