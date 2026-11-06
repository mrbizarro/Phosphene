#!/usr/bin/env bash
# Speech engine setup, one step per call (the panel's install card runs them in
# order; every step is idempotent, so Install again resumes):
#
#   bash scripts/speech/speech_setup.sh preflight   memory + disk
#   bash scripts/speech/speech_setup.sh venv        Python 3.12 venv at speech-mlx/.venv
#   bash scripts/speech/speech_setup.sh packages    mlx-audio at the known-good set
#
# Paths stay RELATIVE to the app folder (the cwd): uv 0.11.23 splits a -c path
# at spaces, and the Mac app lives under "Application Support".
# A real failure prints ONE "FATAL error: <reason + next click>" line (Pinokio
# and the panel both act on it); nothing optional prints that word.
set -uo pipefail
STEP="${1:-}"
ROOT_REL="${LTX_SPEECH_ROOT:-speech-mlx}"
VENV="$ROOT_REL/.venv"
# The engine + packages need ~1 GB; the weights step checks its own remaining bytes
# (speech_fetch.py), so a resumed install is never refused for space it already used.
MIN_FREE_GB="${SPEECH_MIN_FREE_GB:-2}"
MIN_RAM_GB="${SPEECH_MIN_RAM_GB:-16}"
case "$STEP" in
  preflight)
    ram_gb=$(( $(sysctl -n hw.memsize 2>/dev/null || echo 0) / 1073741824 ))
    # 16 GB Macs report 16; allow a GiB of rounding.
    if [ "$ram_gb" -gt 0 ] && [ "$ram_gb" -lt $(( MIN_RAM_GB - 1 )) ]; then
      echo "FATAL error: Speech needs about ${MIN_RAM_GB} GB of unified memory and this Mac has ${ram_gb} GB."
      exit 1
    fi
    mkdir -p "${LTX_SPEECH_MODELS:-mlx_models/speech}"
    free_kb=$(df -Pk "${LTX_SPEECH_MODELS:-mlx_models/speech}" | awk 'NR==2 {print $4}')
    if [ -n "$free_kb" ] && [ "$free_kb" -lt $(( MIN_FREE_GB * 1048576 )) ]; then
      echo "FATAL error: Speech needs ${MIN_FREE_GB} GB free disk and only $(( free_kb / 1048576 )) GB is free. Free some space, then click Install again."
      exit 1
    fi
    echo "preflight ok: ${ram_gb} GB memory, $(( free_kb / 1048576 )) GB free"
    ;;
  venv)
    if ! "$VENV/bin/python" -c 'import sys; assert sys.version_info[:2] == (3, 12)' >/dev/null 2>&1; then
      uv venv --clear --python 3.12 "$VENV" || {
        echo "FATAL error: could not create the Python 3.12 environment for Speech. Check your connection and free disk space (Python downloads the first time), then click Install again."
        exit 1
      }
    fi
    echo "venv ok"
    ;;
  packages)
    uv pip install --python "$VENV/bin/python" -c scripts/speech/speech_constraints.txt "mlx-audio==0.5.8" || {
      echo "FATAL error: could not install the speech engine packages. Check your connection, then click Install again."
      exit 1
    }
    "$VENV/bin/python" -c 'import mlx.core, mlx_audio.tts.utils, huggingface_hub' >/dev/null 2>&1 || {
      echo "FATAL error: the speech engine installed but does not start on this Mac. Click Install again to repair it."
      exit 1
    }
    # The panel reads this to call the engine ready: written only after the probe
    # passed, and it names the constraints it was built from (a later change makes
    # the venv stale, and Install again repairs it).
    "$VENV/bin/python" -c 'import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],"rb").read()).hexdigest())' \
      scripts/speech/speech_constraints.txt > "$VENV/.phosphene_speech_packages"
    echo "packages ok"
    ;;
  *)
    echo "FATAL error: unknown speech setup step '$STEP' - report this line."
    exit 2
    ;;
esac
