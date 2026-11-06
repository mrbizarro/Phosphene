#!/usr/bin/env bash
# One process owns the source journal and dependency repair lock.
cd "$(dirname "$0")/../.." || { echo 'FATAL error: cannot open the app folder.'; exit 1; }
for py in ltx-2-mlx/env/bin/python python3 python; do
  if command -v "$py" >/dev/null 2>&1; then
    "$py" update_transaction.py && exit 0
    echo 'FATAL error: Update did not finish. Keep the update receipt and run Update again.'
    exit 1
  fi
done
echo 'FATAL error: Python is missing. Repair the engine, then run Update again.'
exit 1
