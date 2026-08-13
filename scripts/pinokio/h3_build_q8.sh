#!/usr/bin/env bash
# Build the reduced-RAM Q8 DiT locally, on the 48 GB-class Macs that need it.
#
# Lifted out of install_h3.js (595-char dispatch). See
# scripts/pinokio/README.md.
#
#   cwd : minimax-h3-mlx
#   $1  : the app root. `{{cwd}}` is substituted by Pinokio in the MESSAGE, so
#         it cannot appear in this file — it arrives as an argument.
#
# SEMANTICS: unchanged — one shell, no `set -e`, exit code from the last
# command. Idempotent: the pack's own quant_config.json gates the re-run.
#
# WHY IT MATTERS: the Q8 pack halves the render peak (27.3 vs 42.8 GiB
# measured), which is what makes H3 possible on this class of machine at all —
# and the panel's `h3_capable()` returns False on a sub-60 GB Mac until this
# pack EXISTS ON DISK, so a 48 GB Mac with no pack gets no Engine switcher.
# quantize_stream.py never holds more than one tensor (CPU stream,
# deterministic), so the build runs fine on the same 48 GB machine that could
# never load the model whole. ~22 GB on disk, ~5 min.
#
# 64 GB+ Macs skip it: bf16 is the quality default there, and the panel can
# build the pack later from Settings if the user wants the low-RAM mode.

APP_ROOT="$1"
if [ -z "$APP_ROOT" ]; then
  echo 'h3_build_q8.sh: no app root passed - skipping the Q8 build'
  exit 2
fi

MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null)
PACK="$APP_ROOT/mlx_models/hailuo-h3/models/h3-dit-q8"
SRC="$APP_ROOT/mlx_models/hailuo-h3/models/deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors"
if [ -f "$PACK/quant_config.json" ]; then
  echo 'Q8 engine already built - skipping'
elif echo "$MEM_BYTES" | grep -qE '^[0-9]+$' && [ "$MEM_BYTES" -lt 60000000000 ]; then
  echo '=== Building the reduced-RAM Q8 engine (~5 min, one time) ==='
  .venv/bin/python scripts/quantize_stream.py --src "$SRC" --out "$PACK"
else
  echo '64 GB-class Mac - bf16 engine is the default, Q8 not built'
fi
