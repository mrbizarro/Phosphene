#!/bin/bash
# Move the vendored minimax-h3-mlx checkout to the EXACT engine commit this
# Phosphene release was validated against. (Its venv's packages: h3_deps.sh.)
# THE ONE PLACE THE H3 ENGINE PIN LIVES.
#
# Issue #74 (diagnosed by @blackest): installs cloned before the v2 engine sat
# on `codex/h3-engine`, and Update's Q8 build step ran h3_build_q8.sh against
# that tree — where scripts/quantize_stream.py has never existed — so the
# half-memory engine never got built on any pre-existing install. The H3
# installer already moved the branch on a re-install; Update did not. Both
# call this now.
#
# 4.19.0 — A COMMIT, NOT A BRANCH TIP. Through 4.18.x this checked out
# whatever `codex/h3-engine-v2` pointed at on the day it ran, so pushing the
# engine branch changed every installed Phosphene's H3 at its next Update or
# "Update Hailuo H3 runner" — with no Phosphene release, no gates and no way
# back. The panel and the runner are one product (the panel passes flags the
# runner must know), so the engine now moves only when a Phosphene release
# moves this pin, the same contract the LTX lane (ltx_checkout.sh, a tag) and
# the music engine (engine_pin.txt, a SHA) already have. H3_PIN_REF is a ref on
# the engine repo that CONTAINS the pin — fetching it is how the commit
# arrives; the commit is what gets checked out, and HEAD is verified after.
# Moving the pin: push the engine commit to a ref there (a new branch or tag;
# never force-move one), then change both lines below in the same commit.
#
# Usage: bash scripts/pinokio/h3_checkout.sh <h3-checkout-dir>
# Exit 0 = the tree is at the pin. Non-zero = left as
# it was (nothing destructive happens before the pinned commit is on disk).
#
# HOW A FAILURE STOPS THE INSTALL (4.20). Pinokio 8.2 ignores a shell.run step's
# exit code; it stops a script only when the step's OUTPUT matches /error:/i or
# /errno /i (pinokiod kernel/shells.js default `on` handlers). Every refusal
# below used to print "WARN: ..." - no "error:" - so install_h3.js marched on
# into the venv and a 75 GB download with the engine missing or on the wrong
# commit. They print "FATAL error:" now, which stops Install Hailuo H3. Update
# keeps its H3 step optional: scripts/post_update.sh runs this file through
# `defang`, which turns the FATAL line into a WARN and never ends the Update.
set -u
H3_PIN_SHA="bef6d03c5545fe9dc38400ad2c52edd958e3b2d4"
H3_PIN_REF="phosphene/v4.19.15"
H3_LOCAL_BRANCH="phosphene-pinned"
H3_URL="https://github.com/mrbizarro/minimax-h3-mlx.git"
H3_DIR="${1:-}"
if [ -z "$H3_DIR" ] || [ ! -d "$H3_DIR/.git" ]; then
  echo "FATAL error: the Hailuo H3 engine is not on this Mac ('${H3_DIR:-<none>}' is missing) - its download did not finish. Check your internet connection (a VPN, proxy or blocked GitHub is the usual cause), then click Install again."
  exit 1
fi
cd "$H3_DIR" || { echo "FATAL error: cannot open the Hailuo H3 engine folder ($H3_DIR) - check the folder's permissions, then click Install again."; exit 1; }
git remote get-url origin >/dev/null 2>&1 || git remote add origin "$H3_URL"
have_pin() { git cat-file -e "${H3_PIN_SHA}^{commit}" 2>/dev/null; }
# The ref first (one round trip, and it keeps the pin reachable locally); the
# bare commit as a fallback for a remote where the ref was renamed but the
# commit is still reachable from something else.
git fetch --force origin "$H3_PIN_REF" >/dev/null 2>&1 || true
have_pin || git fetch --force origin "$H3_PIN_SHA" >/dev/null 2>&1 || true
if ! have_pin; then
  echo "FATAL error: could not download the Hailuo H3 engine version Phosphene needs (${H3_PIN_SHA:0:7} from $(git remote get-url origin)). The H3 folder was left as it was - check your internet connection, then click Install again."
  exit 1
fi
if [ -d minimax_h3_mlx ]; then
  git reset --hard HEAD >/dev/null
else
  echo 'WARN: not the H3 tree - skipping reset'
fi
if ! git checkout --force -B "$H3_LOCAL_BRANCH" "$H3_PIN_SHA" >/dev/null 2>&1; then
  echo "FATAL error: could not switch the Hailuo H3 engine to the version Phosphene needs (${H3_PIN_SHA:0:7}). The H3 folder was left as it was - click Install again; if it repeats, report this line."
  exit 1
fi
if [ "$(git rev-parse HEAD)" != "$H3_PIN_SHA" ]; then
  echo "FATAL error: the Hailuo H3 engine is on the wrong version ($(git rev-parse --short HEAD), not ${H3_PIN_SHA:0:7}) - click Install again; if it repeats, report this line."
  exit 1
fi
echo "H3 engine at ${H3_PIN_SHA:0:7} (pinned, $H3_PIN_REF)"
if [ ! -f scripts/quantize_stream.py ]; then
  echo "FATAL error: the Hailuo H3 engine version Phosphene pins has no scripts/quantize_stream.py, so the compact Q8 engine cannot be built from it - report this line."
  exit 1
fi
# The venv's packages are NOT installed here any more (4.19.5). This used to
# finish with a bare `uv pip install -r requirements.txt` - floors only, no
# lock - so every caller now runs scripts/pinokio/h3_deps.sh itself, after
# this move: install_h3.js as its own step once the venv exists, Update in
# post_update.sh 8b whether or not this move worked. One install into the H3
# venv, always under scripts/pinokio/h3_constraints.txt.
