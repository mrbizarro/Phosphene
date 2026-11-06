#!/usr/bin/env bash
# Same 16 GB floor as MUSIC_MIN_RAM_GB and the sidebar. Unknown RAM/df is fail-open.
# HOW A REFUSAL STOPS THE INSTALL (4.20). Pinokio 8.2 ignores a shell.run step's
# exit code; it stops a script only when the step's OUTPUT matches /error:/i or
# /errno /i (pinokiod kernel/shells.js default `on` handlers). Both refusals
# used to print a plain sentence with neither, so install_music.js carried on
# and cloned the engine and started the 11 GB download anyway. Each is a
# "FATAL error:" line now.
set -u
# Linux has no hw.memsize; MemTotal (kB) is the same number there.
MUSIC_MEM=$(sysctl -n hw.memsize 2>/dev/null || awk '/^MemTotal:/ {printf "%.0f\n", $2 * 1024}' /proc/meminfo 2>/dev/null || true)
if [[ "$MUSIC_MEM" =~ ^[0-9]+$ ]] && [ "$MUSIC_MEM" -lt 16000000000 ]; then
  echo "FATAL error: YuE2 (the music engine) needs a Mac with at least 16 GB of memory; this Mac has $((MUSIC_MEM / 1000000000)) GB. Nothing was downloaded and the rest of Phosphene is unaffected."
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
  echo "FATAL error: YuE2 (the music engine) needs at least 14 GB free on the drive that holds the models (this Mac has $((MUSIC_FREE / 1048576)) GB free). Nothing was downloaded - free up space, then click Install again."
  exit 1
else
  echo 'YuE2 preflight OK (16 GB memory, 14 GB free disk required).'
fi
