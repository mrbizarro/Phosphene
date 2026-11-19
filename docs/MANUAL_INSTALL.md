# Manual install (without Pinokio)

Pinokio is the supported way to install Phosphene (see the [install guide](https://phosphene.video/docs/install/)). These are the same steps by hand, for people who want to see every piece. Apple Silicon only.

Before you start, you need Git, [uv](https://docs.astral.sh/uv/) and FFmpeg with libx264 (both `ffmpeg` and `ffprobe`) on your PATH. Pinokio provides these; by hand, install them yourself (for example with Homebrew).

The installer also holds the engine to tested package versions with `scripts/pinokio/engine_constraints.txt`. Add `-c ../scripts/pinokio/engine_constraints.txt` to every `uv pip install` inside `ltx-2-mlx/` below, as `scripts/pinokio/ltx_engine_env.sh` does, or a later package can move mlx or Pillow off the versions Phosphene is tested with. When this page and that script disagree, the script is right.

```bash
# 1. Clone Phosphene + the MLX port (pinned to our LTX-2.5 fork build).
git clone https://github.com/mrbizarro/phosphene.git
cd phosphene
git clone https://github.com/dgrauet/ltx-2-mlx.git ltx-2-mlx
cd ltx-2-mlx
git remote add fork https://github.com/mrbizarro/ltx-2-mlx.git
git fetch fork +refs/tags/v0.14.19+ltx25.9:refs/tags/v0.14.19+ltx25.9
git checkout v0.14.19+ltx25.9
cd ..

# 2. Create the Python 3.11 venv inside ltx-2-mlx (uv-managed).
#    Every install below uses the `uv` on your PATH — `--seed` puts pip,
#    setuptools and wheel into the venv, not uv, so there is no env/bin/uv.
cd ltx-2-mlx
uv venv --python 3.11 --seed env

# 3. Install the MLX pipeline + trainer packages. Pin mlx to 0.31.1 —
#    0.31.2 attenuates the LTX vocoder by 22 dB — and cap transformers below
#    5.13.0, which breaks every generation. Both on the same resolve.
uv pip install --python env/bin/python \
  'mlx==0.31.1' 'mlx-lm==0.31.1' 'mlx-metal==0.31.1' 'transformers>=5.0.0,<5.13.0'
# --build-constraints pins hatchling below 1.32, which rejects upstream's
# `readme = "../../README.md"` and fails metadata generation. See
# pip-build-constraints.txt. The first pass resolves the packages'
# dependencies (mlx-arsenal among them); the second replaces the editable
# workspace links it leaves with real copies, which the codec patch needs.
uv pip install --python env/bin/python \
  --build-constraints ../pip-build-constraints.txt \
  ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer
uv pip install --python env/bin/python --reinstall --no-deps \
  --build-constraints ../pip-build-constraints.txt \
  ./packages/ltx-core-mlx ./packages/ltx-pipelines-mlx ./packages/ltx-trainer
uv pip install --python env/bin/python \
  pyyaml pydantic tqdm rich
# mlx-vlm powers Gemma 3 auto-caption. --no-deps so it doesn't drag mlx-lm past 0.31.1.
uv pip install --python env/bin/python --no-deps 'mlx-vlm==0.4.4'
# Agent + downloader + hub pin range.
uv pip install --python env/bin/python pillow numpy 'huggingface-hub>=1.5.0,<2.0' \
  'hf_transfer>=0.1.6' 'litellm>=1.83.14' 'smolagents>=1.24.0'
cd ..

# 4. Apply the runtime patches (idempotent, fail loud on upstream drift).
./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py

# 5. Download LTX-2.5 — the base install: the engine (20.74 GB), its Gemma 4
#    text encoder (6.73 GB) and the 22 MB live-preview decoder. 27.5 GB total,
#    resumable and sha256-verified. NOT `hf download`: these are our own
#    quantisation of a gated upstream, mirrored as GitHub release assets.
./ltx-2-mlx/env/bin/python3.11 scripts/fetch_pack_release.py \
  --repo-key q4_25 --repo-key gemma4_25 --repo-key tae

# 5b. (Optional) The Q8 weights (30.02 GB) — what trained characters and
#     voices need. The High tier additionally needs the High add-on (29.50 GB).
./ltx-2-mlx/env/bin/python3.11 scripts/fetch_pack_release.py --repo-key q8_25

# 6. (Optional) Image tab — install mflux + apply the FBCache patch.
uv pip install --python ltx-2-mlx/env/bin/python 'mflux==0.18.0'
uv pip install --python ltx-2-mlx/env/bin/python --reinstall --no-deps 'mflux==0.18.0'
uv pip install --python ltx-2-mlx/env/bin/python 'mlx-teacache==0.4.1'
./ltx-2-mlx/env/bin/python3.11 patch_mflux_fbcache.py

# 7. (Optional) HiDream — separate one-time clone for the photoreal engine.
#    Clone HIDREAM-O1-MLX-LAB-active into your home directory, or set
#    HIDREAM_LAB_DIR to point at it.
#    git clone <hidream-lab-repo> ~/HIDREAM-O1-MLX-LAB-active

# 8. Launch the panel.
./ltx-2-mlx/env/bin/python3.11 mlx_ltx_panel.py
```

About the version pins: `mlx 0.31.2` attenuates the LTX vocoder by 22 dB. Stay on 0.31.1. `ltx-2-mlx` is pinned to the fork **tag** `v0.14.19+ltx25.9` (`mrbizarro/ltx-2-mlx`, commit `cd71080`) — v0.14.19 plus the LTX-2.5 port, because upstream has no 2.5 branch. A tag, not a bare SHA: a force-push upstream would strand every install with an un-fetchable pin and a dead Update button. The installed packages report `0.14.19+ltx25.9` and `_LTX_EXPECTED_VERSION` must match that string exactly, or every render logs a VERSION SKEW warning. `scripts/pinokio/ltx_checkout.sh` holds the pin and `node scripts/check_ltx_pin.js` enforces the agreement. `mflux 0.18.0` is the version `patch_mflux_fbcache.py` is line-targeted against, and it is pinned TOGETHER with mlx — mflux declares its own mlx range, so installing it with deps after pinning mlx can silently move mlx (`node scripts/check_post_update.js` fails if either moves alone). `hatchling<1.32` (in `pip-build-constraints.txt`) is a *build-time* pin: 1.32 rejects the `readme = "../../README.md"` that all three upstream packages declare, which fails the wheel build on every tag.

