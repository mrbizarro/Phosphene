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

MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null)
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
