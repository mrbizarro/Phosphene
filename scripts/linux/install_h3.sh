#!/usr/bin/env bash
# Install the Hailuo H3 engine on Linux on Apple Silicon: the same steps as
# install_h3.js, through the same scripts/pinokio helpers, with the Linux
# venv rules of scripts/linux/install.sh (distro Python, omarchy-mlx wheel
# installed last). Needs ~75 GB for the weights plus ~22 GB for the Q8 pack.
#
# Usage, from the app root, after scripts/linux/install.sh:
#   bash scripts/linux/install_h3.sh
#   PHOSPHENE_H3_SKIP_WEIGHTS=1 ...   # code + venv only; weights placed by hand
set -euo pipefail

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  echo "ERR: this installer is for Linux on Apple Silicon (aarch64)." >&2
  exit 1
fi

# The omarchy-mlx wheel is a third-party build. pip verifies the pinned release sha256 before installation.

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PINNED_WHEEL_URL='https://github.com/joshuaswarren/omarchy-mlx/releases/download/v0.7.31/mlx_omarchy-0.32.4.dev202610071347%2B9b5c938-cp314-cp314-linux_aarch64.whl'
WHEEL="${OMARCHY_MLX_WHEEL:-$PINNED_WHEEL_URL}"
SHA256="a2f8c83e5c5f635d00702565a8557d87c40a9eccac885329dcec4692d85d300d"
if [[ -n "${OMARCHY_MLX_WHEEL:-}" ]]; then
  SHA256="${OMARCHY_MLX_WHEEL_SHA256:?Set OMARCHY_MLX_WHEEL_SHA256 for a custom wheel}"
fi
[[ "$SHA256" =~ ^[0-9a-fA-F]{64}$ ]] || { echo 'ERR: invalid wheel sha256' >&2; exit 1; }
[[ "$WHEEL" == *://* ]] || WHEEL="file://$(realpath "$WHEEL")"
WHEEL="${WHEEL%%#*}#sha256=$SHA256"
cd "$ROOT"
mkdir -p .install-tmp
export TMPDIR="$ROOT/.install-tmp"

bash scripts/pinokio/h3_preflight.sh
# shellcheck source=scripts/pinokio/h3_roots.sh
. scripts/pinokio/h3_roots.sh
[ -d "$H3_CHECKOUT/.git" ] || git clone https://github.com/mrbizarro/minimax-h3-mlx.git "$H3_CHECKOUT"
bash scripts/pinokio/h3_checkout.sh "$H3_CHECKOUT"

cd "$H3_CHECKOUT"
# quantize_stream.py keeps every source tensor it has read alive in `src`. macOS maps the file lazily, so that
# costs nothing there; on Linux each tensor is a real allocation and the 38.6 GiB bf16 DiT exhausts the GPU heap
# mid-build. Releasing each tensor once it is quantized restores the "one tensor at a time" bound.
git apply --check "$ROOT/scripts/linux/patches/quantize_stream-free-source.patch" 2>/dev/null \
  && git apply "$ROOT/scripts/linux/patches/quantize_stream-free-source.patch"
[ -x .venv/bin/python3 ] || python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install --force-reinstall --no-deps "$WHEEL"
.venv/bin/python -c "import mlx.core as mx, numpy, PIL; print('H3 deps OK', mx.default_device())"

if [ "${PHOSPHENE_H3_SKIP_WEIGHTS:-0}" != 1 ]; then
  HF_HOME="$ROOT/cache/HF_HOME" HF_XET_HIGH_PERFORMANCE=1 h3_fetch_weights
  .venv/bin/python "$ROOT/scripts/fetch_h3_turbo.py" --dir "$H3_LAYOUT/turbo-lora" \
    || echo 'Turbo fetch failed - the panel offers a one-click retry'
  mkdir -p "$H3_LAYOUT/tae"
  .venv/bin/python "$ROOT/scripts/pinokio/h3_fetch_tae.py" "$H3_LAYOUT/tae/taeh3.safetensors"
  bash "$ROOT/scripts/pinokio/h3_build_q8.sh" "$ROOT"
fi
rm -rf "$TMPDIR"
echo 'Hailuo H3 engine ready. Restart the panel (./run_panel.sh).'
