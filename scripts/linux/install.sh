#!/usr/bin/env bash
# Install Phosphene's engine environment on Linux on Apple Silicon (Omarchy),
# where MLX runs on the GPU through omarchy-mlx's Vulkan backend.
#
# The Pinokio installer stays Mac-only. This does what install.js +
# scripts/pinokio/ltx_engine_env.sh do, with three Linux differences:
#   * the venv uses the distro's Python 3 - omarchy-mlx publishes wheels for
#     that Python, not for 3.11;
#   * no mlx / mlx-metal pins: the omarchy-mlx wheel is installed LAST over
#     whatever `mlx` the resolver pulled in (anything installed after it
#     would put PyPI's CPU/CUDA mlx files back);
#   * mflux is installed without its Linux dependency set, which is
#     mlx[cuda13] plus CUDA torch (several GB of NVIDIA wheels). mflux's
#     weight loader imports torch, so the CPU build is installed instead,
#     with the matching torchaudio that vocal separation (demucs) needs.
#
# The omarchy-mlx wheel is a third-party build. pip verifies the pinned release sha256 before installation.
# Linux-only: the 00-phosphene-openblas.pth preload written below must never
# be written on macOS.
#
# Usage, from the app root:
#   bash scripts/linux/install.sh
#   OMARCHY_MLX_WHEEL=/path/or/url.whl OMARCHY_MLX_WHEEL_SHA256=<hash> bash scripts/linux/install.sh
# Then ./run_panel.sh. Models download from the panel, as on a Mac.
set -euo pipefail

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

if [ "$(uname -s)" != Linux ] || [ "$(uname -m)" != aarch64 ]; then
  echo "ERR: this installer is for Linux on Apple Silicon (aarch64)." >&2
  exit 1
fi
for tool in git python3 ffmpeg; do
  command -v "$tool" >/dev/null || { echo "ERR: $tool not found - install it with your package manager." >&2; exit 1; }
done

cd "$ROOT"
# pip unpacks large wheels into TMPDIR; /tmp is a small tmpfs on many distros.
mkdir -p .install-tmp
export TMPDIR="$ROOT/.install-tmp"

[ -d ltx-2-mlx/.git ] || git clone https://github.com/dgrauet/ltx-2-mlx.git ltx-2-mlx
bash scripts/pinokio/ltx_checkout.sh

[ -x ltx-2-mlx/env/bin/python3 ] || python3 -m venv ltx-2-mlx/env
PY="$ROOT/ltx-2-mlx/env/bin/python3"
"$PY" -m pip install --upgrade pip

cd ltx-2-mlx
"$PY" -m pip install --build-constraint ../pip-build-constraints.txt \
  'mlx-lm==0.31.1' 'transformers>=5.0.0,<5.13.0' \
  ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer
cd "$ROOT"
"$PY" -m pip install --no-deps 'mlx-vlm==0.4.4'
"$PY" -m pip install certifi pillow numpy 'huggingface-hub>=1.5.0,<2.0' 'hf_transfer>=0.1.6' \
  'litellm>=1.83.14' 'smolagents>=1.24.0' 'pywebpush>=2.0'
"$PY" -m pip install --no-deps 'mflux==0.18.0' 'mlx-teacache==0.4.1'
"$PY" -m pip install fonttools 'matplotlib<4,>=3.9.2' 'opencv-python<5,>=4.10' 'piexif<2,>=1.1.3' \
  'platformdirs<5,>=4' regex requests 'sentencepiece<1,>=0.2.1' 'toml<1,>=0.10.2' 'tqdm<5,>=4.66.5' \
  'urllib3>=2.6.0' 'protobuf<8,>=4.25' 'safetensors<1,>=0.4.4' 'filelock>=3.20.1'
"$PY" -m pip install --index-url https://download.pytorch.org/whl/cpu torch torchaudio
"$PY" -m pip install --force-reinstall --no-deps "$WHEEL"
# omarchy-mlx links the system libopenblas.so.0 and torch bundles its own under
# the same soname. Whichever loads first serves both, and the distro build can
# lack symbols torch needs (Arch: no sbgemm_), so `import mlx` then
# `import torch` - mflux's own order - fails. Load torch's copy at interpreter
# start; it is a superset for what mlx calls.
SITE="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
cat > "$SITE/00-phosphene-openblas.pth" <<'EOF'
import os, ctypes, sysconfig; _p = os.path.join(sysconfig.get_paths()["purelib"], "torch", "lib", "libopenblas.so.0"); os.path.exists(_p) and ctypes.CDLL(_p, mode=ctypes.RTLD_GLOBAL)
EOF
"$PY" -c 'import mlx.core, torch'

"$PY" patch_ltx_codec.py
"$PY" patch_mflux_fbcache.py
"$PY" scripts/pinokio/engine_env_check.py
"$PY" -c 'import mlx.core as mx; print("mlx", mx.__version__, mx.default_device())'
rm -rf "$TMPDIR"
echo 'Engine environment ready. Start the panel with ./run_panel.sh'
