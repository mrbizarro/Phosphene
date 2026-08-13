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
# otherwise-fine install. The floor is 46e9 bytes, not 48 — a 48 GB Mac reports
# ~47.x GB after firmware reservations. Keep this number in sync with
# `H3_MIN_BYTES` in pinokio.js and `H3_MIN_RAM_GB_Q8` in mlx_ltx_panel.py.

MEM_BYTES=$(sysctl -n hw.memsize 2>/dev/null)
if echo "$MEM_BYTES" | grep -qE '^[0-9]+$' && [ "$MEM_BYTES" -lt 46000000000 ]; then
  MEM_GB=$((MEM_BYTES / 1000000000))
  echo '=================================================================='
  echo "HAILUO H3 NEEDS AT LEAST A 48 GB MAC (this Mac has ${MEM_GB} GB)"
  echo 'Even the reduced-RAM Q8 engine peaks around 27 GiB while rendering;'
  echo 'below 48 GB it swaps and a 3-second clip takes hours.'
  echo 'Nothing was downloaded. Keep using the built-in LTX-2.3 engine.'
  echo '=================================================================='
  exit 1
elif [ "$MEM_BYTES" -lt 60000000000 ]; then
  echo 'H3 memory preflight OK - 48 GB class: the reduced-RAM Q8 engine'
  echo 'will be built locally after the weights download (adds ~5 min).'
else
  echo 'H3 memory preflight OK'
fi
