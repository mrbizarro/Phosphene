#!/usr/bin/env bash
# The STEMS extra for the engine venv — what a vocal-stem-conditioned A2V
# render needs and an ordinary render does not.
#
# WHY IT IS OPTIONAL. A2V drives a mouth from a waveform, and the waveform it
# is given is normally the whole record. Drums, bass and guitar reach the audio
# encoder as energy that can be read as syllables, so the model hedges: the
# mouth moves a little, all the time, on everything. Conditioning on a
# SEPARATED VOCAL removes that noise (the panel muxes the original song back
# over the finished clip, so nothing the audience hears changes). Measured on
# one shot at one seed, that swap was the difference between a mouth whose
# movement tracked the voice and one that did not — and it doubled how much
# the mouth moved at all.
#
# It is an extra rather than a dependency because it drags torch and
# torchaudio in (~2.5 GB) for a lane most renders never take, and because a
# queued job must never fail on a missing optional download — the panel
# degrades to the full mix and says so.
#
# WHERE IT GOES: the engine venv's own bin, which is the FIRST place
# `mlx_ltx_panel._resolve_demucs()` looks. Nothing else on the Mac counts — a
# demucs living in some other project's virtualenv is not a dependency of
# Phosphene and must never become one by accident.
#
# Usage: a2v_stems_deps.sh <path to the ltx-2-mlx checkout>
set -euo pipefail

MLX_CHECKOUT="$(cd "${1:?ltx-2-mlx checkout required}" && pwd)"

# The venv the panel spawns its helper from, in the two layouts that exist.
VENV=""
for cand in "$MLX_CHECKOUT/.venv" "$MLX_CHECKOUT/env"; do
  if [ -x "$cand/bin/python3.11" ] || [ -x "$cand/bin/python" ]; then
    VENV="$cand"
    break
  fi
done
if [ -z "$VENV" ]; then
  echo "no engine venv under $MLX_CHECKOUT (looked for .venv/ and env/)" >&2
  echo "run the engine install first, then this script" >&2
  exit 1
fi

PY="$VENV/bin/python3.11"
[ -x "$PY" ] || PY="$VENV/bin/python"

# Already there? Say so and stop — this script is run from a sidebar button
# and a second click must not spend ten minutes re-resolving torch.
if [ -x "$VENV/bin/demucs" ]; then
  echo "demucs is already installed at $VENV/bin/demucs"
  "$VENV/bin/demucs" --help >/dev/null 2>&1 \
    && echo "it runs" \
    || echo "WARNING: it does not run — reinstall with --force-reinstall" >&2
  exit 0
fi

# demucs pulls torch + torchaudio. Installed BY NAME, not pinned here: the
# engine's own constraints file is the version authority on this machine, the
# same rule music_cover_deps.sh follows for the transcription extra.
CONSTRAINTS=""
for cand in "$MLX_CHECKOUT/../pip-build-constraints.txt" \
            "$MLX_CHECKOUT/pip-build-constraints.txt"; do
  [ -f "$cand" ] && { CONSTRAINTS="$cand"; break; }
done

if [ -n "$CONSTRAINTS" ]; then
  "$PY" -m pip install --upgrade --constraint "$CONSTRAINTS" demucs
else
  "$PY" -m pip install --upgrade demucs
fi

# Prove it, rather than trusting pip's exit code: the panel resolves an
# executable, so an install that produced a library and no console script is
# an install that has not happened as far as A2V is concerned.
if [ ! -x "$VENV/bin/demucs" ]; then
  echo "pip finished but $VENV/bin/demucs is not there" >&2
  exit 1
fi
"$VENV/bin/demucs" --help >/dev/null
echo "demucs installed at $VENV/bin/demucs"
