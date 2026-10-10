#!/usr/bin/env bash
# Hailuo H3 memory preflight — refuse the 75 GB download on a Mac that can
# never render with it.
#
# Lifted out of install_h3.js (845-char dispatch — the whole if/else is one
# "\n"-joined string, so it was one write to the pty). See
# scripts/pinokio/README.md.
#
#   cwd : the app root
#
# SEMANTICS: unchanged — one shell, no `set -e`, one deliberate `exit 1`.
# The `echo 'free disk space:' && df -h .` that followed it stays in
# install_h3.js as its own array element (its own shell, ~25 chars).
#
# FAIL-OPEN BY DESIGN: if sysctl is missing or unparseable we PROCEED. A
# preflight that can't read the hardware must never be the thing that blocks an
# otherwise-fine install.
#
# THE FLOOR IS 36e9 BYTES, ON MEASUREMENT. It said 46e9 — a guard band picked to
# sit ~4 GB under a 48 GB Mac's marketing number, back when the only number
# anyone had was "27.3 GiB peak". v4.8.0 lowered the panel's floor to 36 on the
# full phase profile (text_encode 25.63 GiB is the run peak, a 7-second phase)
# and left this restatement and pinokio.js's behind, so a 36-48 GB Mac was told
# by the panel that H3 runs and REFUSED the download here. Keep this number in
# sync with `H3_MIN_BYTES` in pinokio.js and `H3_MIN_RAM_GB_Q8` in
# mlx_ltx_panel.py — one number, three files.

# Linux has no hw.memsize; MemTotal (kB) is the same number there.
MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null || awk '/^MemTotal:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo 2>/dev/null)
if echo "$MEM_BYTES" | grep -qE '^[0-9]+$' && [ "$MEM_BYTES" -lt 36000000000 ]; then
  MEM_GB=$((MEM_BYTES / 1000000000))
  echo '=================================================================='
  echo "HAILUO H3 NEEDS AT LEAST A 36 GB MAC (this Mac has ${MEM_GB} GB)"
  echo 'Even the reduced-RAM Q8 engine peaks around 25.6 GiB while rendering;'
  echo 'below 36 GB it swaps and a 3-second clip takes hours.'
  echo 'Nothing was downloaded. Keep using the built-in LTX engine.'
  echo '=================================================================='
  exit 1
elif [ "$MEM_BYTES" -lt 60000000000 ]; then
  echo 'H3 memory preflight OK - 36-60 GB class: the reduced-RAM Q8 engine'
  echo 'is REQUIRED here and will be built locally after the weights'
  echo 'download (adds ~5 min).'
else
  echo 'H3 memory preflight OK'
fi

# ---- Disk preflight (H3-18) -------------------------------------------------
# Before this, install_h3.js's ONLY disk-space behaviour was `df -h .` printed
# after the fact — an install that ran out of space partway through failed
# loudly mid-download instead of being told up front. Refuse the same way the
# RAM check does: fail-open if `df` is unreadable, otherwise a real number and
# a real refusal.
#
# REQUIRED SPACE is context-dependent, not one constant: a Mac whose weights
# are already on disk (Repair / Build, or a resumed install) only needs room
# for the compact Q8 build (~22 GB, plus headroom for whatever weight files
# are still incomplete). A Mac starting from nothing needs the ~75 GB
# download PLUS that ~22 GB build, because both exist on disk at once before
# the build reads the download's output — ~97 GB, matching the review's
# figure. Presence of the bf16 DiT (the single largest file in the tree, and
# the same marker h3_roots.sh uses) is the cheap signal for "mostly here
# already"; it is a heuristic, not the panel's real required_files.json
# check, so it only ever WIDENS the requirement it isn't sure about.
_h3_abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$(pwd)" "$1" ;; esac; }
H3_MODELS_ROOT_PF="$(_h3_abs "${LTX_H3_MODELS:-mlx_models/hailuo-h3}")"
H3_DIT_REL_PF='deepbeep-pruned-bf16/MiniMax-H3-FL2VA-pruned_bf16.safetensors'
if { [ -f "$H3_MODELS_ROOT_PF/h3-dit-q8/.built_ok" ] || [ -f "$H3_MODELS_ROOT_PF/models/h3-dit-q8/.built_ok" ]; } \
   && { [ -f "$H3_MODELS_ROOT_PF/$H3_DIT_REL_PF" ] || [ -f "$H3_MODELS_ROOT_PF/models/$H3_DIT_REL_PF" ]; }; then
  # Weights AND the compact engine already built (h3_build_q8.sh's own
  # .built_ok marker): an Update / Repair only refreshes the runner and its
  # venv — a few GB, not a 22 GB build (4.17 Codex H3-6).
  NEED_GB=5
  NEED_WHY='a runner / environment refresh (weights and the compact engine are already built)'
elif [ -f "$H3_MODELS_ROOT_PF/$H3_DIT_REL_PF" ] || [ -f "$H3_MODELS_ROOT_PF/models/$H3_DIT_REL_PF" ]; then
  NEED_GB=25
  NEED_WHY='the compact Q8 engine build (weights already on disk)'
else
  NEED_GB=97
  NEED_WHY='the ~75 GB download plus the ~22 GB compact engine build it does afterward'
fi
# Measure the volume the WEIGHTS land on (LTX_H3_MODELS may point at another
# drive), via its nearest existing ancestor — not the app folder's volume
# (4.17 Codex H3-7).
_PF_DF_AT="$H3_MODELS_ROOT_PF"
while [ ! -e "$_PF_DF_AT" ] && [ "$_PF_DF_AT" != "/" ] && [ -n "$_PF_DF_AT" ]; do
  _PF_DF_AT="$(dirname "$_PF_DF_AT")"
done
[ -e "$_PF_DF_AT" ] || _PF_DF_AT=.
AVAIL_KB=$(df -Pk "$_PF_DF_AT" 2>/dev/null | awk 'NR==2 {print $4}')
if echo "$AVAIL_KB" | grep -qE '^[0-9]+$'; then
  AVAIL_GB=$((AVAIL_KB / 1024 / 1024))
  if [ "$AVAIL_GB" -lt "$NEED_GB" ]; then
    echo '=================================================================='
    echo "HAILUO H3 NEEDS ~${NEED_GB} GB FREE (this Mac has ${AVAIL_GB} GB free)"
    echo "That covers ${NEED_WHY}."
    echo 'Free up space and try again. Nothing was downloaded. Keep using the'
    echo 'built-in LTX engine until then.'
    echo '=================================================================='
    exit 1
  fi
  echo "H3 disk preflight OK - ${AVAIL_GB} GB free, needs ~${NEED_GB} GB"
else
  echo 'H3 disk preflight skipped - could not read free space (df unreadable)'
fi
