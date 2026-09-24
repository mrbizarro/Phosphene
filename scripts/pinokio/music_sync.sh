#!/usr/bin/env bash
# Frozen dependency sync for the music engine venv. The checkout path is made
# absolute first: uv resolves a relative UV_PROJECT_ENVIRONMENT against the
# project dir, so LTX_MUSIC_ROOT=engines/yue2 would otherwise install into
# engines/yue2/engines/yue2/.venv while everything else uses engines/yue2/.venv.
set -euo pipefail
MUSIC_CHECKOUT="$(cd "${1:?music checkout required}" && pwd)"
UV_PROJECT_ENVIRONMENT="$MUSIC_CHECKOUT/.venv" uv sync --frozen --no-dev --extra transcription --project "$MUSIC_CHECKOUT"
# `--extra transcription` on EVERY sync: an exact sync without it removes the
# cover's libraries (mir_eval, mido, pretty_midi) on the next Update while the
# cover models stay on disk and the readiness check keeps saying ready.
