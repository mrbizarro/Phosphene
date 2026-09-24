#!/usr/bin/env bash
# The transcription extra for the music engine venv — what a COVER needs and a
# prompt-written song does not.
#
# `lyra.transcription` imports mir_eval, mido, pretty_midi and scipy at module
# import time, and `uv sync --frozen --no-dev` (music_sync.sh) installs the base
# dependency set without extras. So a venv built for writing songs raises
# ModuleNotFoundError the moment a cover starts — which is exactly what it did
# the first time this ran end to end. The extra is declared upstream in
# pyproject.toml as [project.optional-dependencies].transcription; install it by
# NAME rather than pinning here, so the engine pin stays the only version
# authority.
set -euo pipefail
MUSIC_CHECKOUT="$(cd "${1:?music checkout required}" && pwd)"
UV_PROJECT_ENVIRONMENT="$MUSIC_CHECKOUT/.venv" \
  uv sync --frozen --no-dev --extra transcription --project "$MUSIC_CHECKOUT"
