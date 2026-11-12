#!/usr/bin/env bash
# Mac app only: bring this Studio folder's Hailuo H3 runner to the engine commit this
# Studio pins (scripts/pinokio/h3_checkout.sh), with no new app build and no weights.
#
# WHY THIS EXISTS (5.0.7). The app's own `bootstrap.sh h3` builds the runner for the
# BUNDLED channel's Studio folder only, from the engine commit that channel pins, and
# leaves any runner that already starts alone. A Studio the app got through the signed
# pin lives in its own folder and never had a runner; an old runner never moved. The
# low-memory engine (16 GB Macs) has to reach both, so the Studio does it itself, the
# way the 5.0.5 Studio adds the image engine itself. Pinokio does not use this file:
# its Update runs h3_checkout.sh.
#
#   usage : bash h3_app_runner.sh [studio-folder]
#   env   : PHOSPHENE_HOME (required)  PHOSPHENE_UV  LTX_STATE_DIR
#   last line of output: "@@result h3 have|built|busy|skipped_<why>|failed"
#
# Safe to run twice. Nothing in the weights folder is touched. The old runner of this
# folder is put back if anything fails; the old runner of ANOTHER folder is never touched.
# The caller (the Studio) holds new renders while this runs.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="${1:-$(cd "$HERE/../.." && pwd)}"
HP="${PHOSPHENE_HOME:-}"
result() { echo "@@result h3 $1"; }
[ -n "$HP" ] && [ -d "$HP" ] || { echo "H3 runner: no Phosphene home folder; nothing done."; result skipped_setup; exit 0; }

# The archive of the commit this Studio pins (measured from GitHub's tarball of that commit).
# h3_checkout.sh owns the commit; this must be re-measured with every pin move
# (test_h3_app_runner.py fails when the two disagree).
H3_ARCHIVE_FOR="953ec466586d6b35823e475ce64e43b43fa7f39a"
H3_ARCHIVE_SHA256="551f68aa1f5483872edf764fc1540457c5e8de944dc575ad1f2de74273f13f1a"
H3_ARCHIVE_BYTES=408523
H3_ARCHIVE_URL="https://codeload.github.com/mrbizarro/minimax-h3-mlx/tar.gz/$H3_ARCHIVE_FOR"
PY_VERSION="3.11"

PIN=$(sed -n 's/^H3_PIN_SHA="\([0-9a-f]*\)".*/\1/p' "$HERE/h3_checkout.sh" 2>/dev/null | head -1)
H3D="$ROOT/minimax-h3-mlx"
STATE_DIR="${LTX_STATE_DIR:-$HP/state}"
H3STATE="$STATE_DIR/app_h3.json"
h3_state() {   # building | ready | failed
  mkdir -p "$STATE_DIR" 2>/dev/null
  printf '{"state": "%s", "pid": %s, "updated_at": "%s"}\n' "$1" "$$" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$H3STATE.tmp.$$" \
    && mv -f "$H3STATE.tmp.$$" "$H3STATE" 2>/dev/null || rm -f "$H3STATE.tmp.$$"
}
runner_ok() {   # a runner that is here and starts
  [ -f "$H3D/scripts/generate_staged.py" ] && [ -x "$H3D/.venv/bin/python" ] \
    && "$H3D/.venv/bin/python" -c 'import mlx.core' >/dev/null 2>&1
}
at_pin() {      # the runner here IS the pinned commit (our marker, or a git checkout on it)
  [ "$(cat "$H3D/.phosphene_h3_ok" 2>/dev/null)" = "$PIN" ] && return 0
  [ -d "$H3D/.git" ] && [ "$(git -C "$H3D" rev-parse HEAD 2>/dev/null)" = "$PIN" ] && return 0
  return 1
}
retire() {      # set a path aside into the Trash (never rm); 1 when it is still there
  [ -e "$1" ] || [ -L "$1" ] || return 0
  local t; t=~/.Trash/"$(basename "$1").$(date +%s).$$"
  mkdir -p ~/.Trash 2>/dev/null
  [ ! -e "$t" ] && mv "$1" "$t" 2>/dev/null && [ ! -e "$1" ] && [ ! -L "$1" ]
}

[ "$(uname -m)" = arm64 ] || { result skipped_mac; exit 0; }
if [ -z "$PIN" ] || [ "$PIN" != "$H3_ARCHIVE_FOR" ]; then
  echo "H3 runner: the engine this Studio pins (${PIN:0:7}) has no measured download in this Studio; nothing done."
  result skipped_pin; exit 0
fi
if runner_ok && at_pin; then result have; exit 0; fi

# Another Hailuo H3 runner build (the app's own, or the compact-engine build) is running: later.
LOCKS="$HP/.locks"; mkdir -p "$LOCKS" 2>/dev/null
alive_other() {   # pid file -> 0 when a live process other than us owns it
  local p; p=$(cat "$1" 2>/dev/null); case "$p" in ''|*[!0-9]*) return 1;; esac
  [ "$p" != "$$" ] && kill -0 "$p" 2>/dev/null
}
if alive_other "$LOCKS/h3.lock/owner"; then echo "H3 runner: the app is building Hailuo H3 right now; trying again next time."; result busy; exit 0; fi
MYLOCK="$LOCKS/h3-studio.lock"
if ! mkdir "$MYLOCK" 2>/dev/null; then
  if alive_other "$MYLOCK/owner"; then result busy; exit 0; fi
  rm -f "$MYLOCK/owner"; rmdir "$MYLOCK" 2>/dev/null
  mkdir "$MYLOCK" 2>/dev/null || { result busy; exit 0; }
fi
echo "$$" > "$MYLOCK/owner"
trap 'rm -f "$MYLOCK/owner"; rmdir "$MYLOCK" 2>/dev/null' EXIT

free=$(df -k "$HP" 2>/dev/null | awk 'NR==2 {printf "%.0f", $4*1024}')
if [ "${PHOSPHENE_TEST_FREE_BYTES:-}" != "" ]; then free="$PHOSPHENE_TEST_FREE_BYTES"; fi
if [ "${free:-0}" -lt 4000000000 ]; then
  echo "H3 runner: only $((free / 1000000)) MB free; the Hailuo H3 update waits for more room."
  result skipped_disk; exit 0
fi
UV="${PHOSPHENE_UV:-$HP/runtime/bin/uv}"
[ -x "$UV" ] || { echo "H3 runner: the setup tool is missing; the update is tried again next time."; result skipped_setup; exit 0; }
RUNTIME="$(cd "$(dirname "$UV")/.." && pwd)"
export PATH="$(dirname "$UV"):/usr/bin:/bin:/usr/sbin:/sbin"
export UV_PYTHON_INSTALL_DIR="$RUNTIME/python" UV_CACHE_DIR="$RUNTIME/uv-cache" UV_PYTHON_PREFERENCE=only-managed \
       UV_NO_CONFIG=1 UV_PYTHON_BIN_DIR="$RUNTIME/python-bin" UV_TOOL_BIN_DIR="$RUNTIME/tool-bin"
unset VIRTUAL_ENV PYTHONPATH PYTHONHOME

fail() {   # plain reason; the old runner is already back
  echo "H3 runner: $1 The update is tried again next time Phosphene opens."
  h3_state failed; result failed; exit 0
}

h3_state building
echo "H3 runner: updating to engine ${PIN:0:7} (no weights are downloaded)"
DL="$HP/downloads"; mkdir -p "$DL" 2>/dev/null
ARC="$DL/minimax-h3-mlx-${PIN:0:12}.tar.gz"
sha_of() { shasum -a 256 "$1" | awk '{print $1}'; }
if [ -s "$ARC" ] && [ "$(sha_of "$ARC")" != "$H3_ARCHIVE_SHA256" ]; then retire "$ARC"; fi
if [ ! -s "$ARC" ]; then
  curl -fsSL --connect-timeout 20 --max-time 600 -o "$ARC.part" "$H3_ARCHIVE_URL" 2>"$ARC.err" \
    || { retire "$ARC.part"; fail "could not download the engine (check the internet connection)."; }
  mv "$ARC.part" "$ARC"
fi
[ "$(sha_of "$ARC")" = "$H3_ARCHIVE_SHA256" ] || { retire "$ARC"; fail "the engine download was damaged."; }
[ "$(stat -f %z "$ARC" 2>/dev/null)" = "$H3_ARCHIVE_BYTES" ] || { retire "$ARC"; fail "the engine download had the wrong size."; }

SRC="$H3D.src"; retire "$SRC" || fail "could not clear an old working folder."
mkdir -p "$SRC" || fail "could not create a folder."
tar -xzf "$ARC" -C "$SRC" --strip-components 1 || { retire "$SRC"; fail "the engine download could not be opened."; }
[ -f "$SRC/scripts/generate_staged.py" ] && [ -f "$SRC/requirements.txt" ] && grep -q -- '--low-memory' "$SRC/scripts/generate_staged.py" \
  || { retire "$SRC"; fail "the engine download is not the expected version."; }

mkdir -p "$H3D" || fail "could not create the Hailuo H3 folder."
# The venv: kept when it runs, built when missing (its packages are checked below either way).
if ! "$H3D/.venv/bin/python" -c 'import sys' >/dev/null 2>&1; then
  [ -e "$H3D/.venv" ] && { retire "$H3D/.venv" || { retire "$SRC"; fail "could not replace a broken Python environment."; }; }
  ( cd "$H3D" && "$UV" venv --python "$PY_VERSION" --seed .venv ) 2>&1 | sed 's/^/  /'
fi
[ -x "$H3D/.venv/bin/python" ] || { retire "$SRC"; fail "could not prepare the Python environment."; }

# Swap: the old tree (everything but the venv) goes to .prev, the new one in; undone on any failure.
PREV="$H3D.prev"; retire "$PREV" || { retire "$SRC"; fail "could not clear an old working folder."; }
mkdir -p "$PREV"
for f in "$H3D"/* "$H3D"/.[!.]*; do
  [ -e "$f" ] || [ -L "$f" ] || continue
  case "$(basename "$f")" in .venv) continue;; esac
  mv "$f" "$PREV/" || true
done
put_back() {   # drop the new files, restore the old ones
  local f
  for f in "$H3D"/* "$H3D"/.[!.]*; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    case "$(basename "$f")" in .venv) continue;; esac
    retire "$f" || true
  done
  for f in "$PREV"/* "$PREV"/.[!.]*; do
    [ -e "$f" ] || [ -L "$f" ] || continue
    mv "$f" "$H3D/" || true
  done
  rmdir "$PREV" 2>/dev/null
}
moved=0
for f in "$SRC"/* "$SRC"/.[!.]*; do
  [ -e "$f" ] || continue
  mv "$f" "$H3D/" || { moved=1; break; }
done
if [ "$moved" = 1 ]; then put_back; retire "$SRC"; fail "could not save the new engine files."; fi
rmdir "$SRC" 2>/dev/null

DEPS_LOG="$DL/h3_app_runner_deps.log"
( cd "$H3D" && bash "$HERE/h3_deps.sh" "$H3D" ) > "$DEPS_LOG" 2>&1; deps_rc=$?
sed 's/^/  /' "$DEPS_LOG" | tail -20
if [ "$deps_rc" != 0 ] || ! "$H3D/.venv/bin/python" -c 'import mlx.core, numpy, PIL' >/dev/null 2>&1; then
  put_back; fail "the Hailuo H3 packages did not finish installing."
fi
if ! runner_ok; then put_back; fail "the new Hailuo H3 engine did not start."; fi
echo "$PIN" > "$H3D/.phosphene_h3_ok"
retire "$PREV" || true
h3_state ready
echo "H3 runner: now on engine ${PIN:0:7}"
result built
exit 0
