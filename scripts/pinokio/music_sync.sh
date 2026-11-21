#!/usr/bin/env bash
# Frozen dependency sync for the music engine venv. The checkout path is made
# absolute first: uv resolves a relative UV_PROJECT_ENVIRONMENT against the
# project dir, so LTX_MUSIC_ROOT=engines/yue2 would otherwise install into
# engines/yue2/engines/yue2/.venv while everything else uses engines/yue2/.venv.
#
# A failure prints one "FATAL error:" line: Pinokio 8.2 ignores a shell.run
# step's exit code and stops a script only when the step's OUTPUT matches
# /error:/i or /errno /i (pinokiod kernel/shells.js default `on` handlers).
set -euo pipefail
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-60}"
MUSIC_ARG="${1:-}"
if [ -z "$MUSIC_ARG" ] || ! MUSIC_CHECKOUT="$(cd "$MUSIC_ARG" 2>/dev/null && pwd)"; then
  echo "FATAL error: the YuE2 music engine folder is missing (${MUSIC_ARG:-<none>}) - its download did not finish. Click Install again."
  exit 1
fi
UV_PROJECT_ENVIRONMENT="$MUSIC_CHECKOUT/.venv" uv sync --frozen --no-dev --extra transcription --project "$MUSIC_CHECKOUT" || {
  echo "FATAL error: the YuE2 music engine's Python packages did not install - the lines above say why (usually the internet connection or a full disk). Click Install again; nothing already downloaded is lost."
  exit 1
}
# `--extra transcription` on EVERY sync: an exact sync without it removes the
# cover's libraries (mir_eval, mido, pretty_midi) on the next Update while the
# cover models stay on disk and the readiness check keeps saying ready.
