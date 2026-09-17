#!/usr/bin/env bash
# Same 24 GB floor as MUSIC_MIN_RAM_GB and the sidebar. Unknown RAM/df is fail-open.
set -u
MUSIC_MEM=$(sysctl -n hw.memsize 2>/dev/null || true)
if [[ "$MUSIC_MEM" =~ ^[0-9]+$ ]] && [ "$MUSIC_MEM" -lt 24000000000 ]; then
  echo "YuE2 needs about 24 GB of unified memory; this Mac reports $((MUSIC_MEM / 1000000000)) GB. The rest of Phosphene is unaffected."
  exit 1
fi
MUSIC_DEST="${LTX_MUSIC_MODELS:-mlx_models/yue2}"
# df must inspect the destination volume even before its directory exists.
while [ ! -d "$MUSIC_DEST" ] && [ "$MUSIC_DEST" != / ] && [ "$MUSIC_DEST" != . ]; do
  MUSIC_DEST=$(dirname "$MUSIC_DEST")
done
MUSIC_FREE=$(df -Pk "$MUSIC_DEST" 2>/dev/null | awk 'NR==2 {print $4}')
# Repair with weights already present needs no second 14 GB reserve.
if [ -f "${LTX_MUSIC_MODELS:-mlx_models/yue2}/generator/conversion.json" ]; then
  echo 'YuE2 repair: the fetcher will check space for missing files only.'
elif [[ "$MUSIC_FREE" =~ ^[0-9]+$ ]] && [ "$MUSIC_FREE" -lt 13671875 ]; then
  echo 'Installing YuE2 needs at least 14 GB free on the weights volume.'
  exit 1
else
  echo 'YuE2 preflight OK (24 GB memory, 14 GB free disk required).'
fi
