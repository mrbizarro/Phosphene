#!/usr/bin/env bash
# CONVERGE THE PANEL REPO — fast-forward if we can, reset only on PROVEN
# divergence, and print the new short SHA either way.
#
# Lifted out of update.js (484-char dispatch, a single "\n"-joined string, so
# it was one write to the pty — above the ~350 that Pinokio 8.0.x is proven to
# survive, and the largest dispatch in the repo). See
# scripts/pinokio/README.md.
#
#   cwd : the app root, i.e. the panel repo
#
# SEMANTICS: unchanged, byte for byte in behaviour — one shell, NO `set -e`.
# `git merge --ff-only` failing is the normal path into the divergence
# branch, and the `exec` on the happy path is what makes the fast-forward
# exit with the rev-parse's own status and output.
#
# ORDER IS NOT THE GUARANTEE — this script re-runs
# scripts/pinokio/update_obstruction_guard.sh itself (see below). Reaching
# `reset --hard` requires BOTH of these to have passed:
#
#   1. the obstruction guard (run again here, in this shell), and
#   2. `git rev-list --count $U..HEAD` > 0 — the number of commits this clone
#      has that upstream does not.
#
# Divergence is PROVEN, never inferred from what else failed. The version
# before this classified every merge failure that left tracked files clean as
# "divergence" and reset — but `git diff --quiet` IGNORES UNTRACKED FILES, so
# the most common non-divergence failure (an untracked path that a newly
# tracked upstream path would overwrite) passed both checks and the reset
# deleted the user's file as the "obstruction". Zero local commits now means
# the history did not diverge, whatever made the merge fail, so we stop and
# print git's own error — which names the obstructing paths. Nothing is
# deleted on that path, ever.

# THE GUARD AGAIN, IN THIS SHELL, before anything moves the tree. "Its step,
# before this one" was not a guarantee: Pinokio 8.2.0 runs the next step after
# one that exits 1 unless the output says "error:", and the guard's refusal did
# not — so a diverged clone with an untracked obstruction was reset over it
# anyway, and on a clone with no divergence `merge --ff-only` silently
# overwrote an IGNORED obstruction, which git treats as expendable (Codex
# INST-02, 2026-09-24). A missing guard is a refusal too.
G="$(dirname "$0")/update_obstruction_guard.sh"
[ -f "$G" ] || { echo 'FATAL error: update_obstruction_guard.sh missing - not updating. Nothing deleted.'; exit 1; }
bash "$G" || exit 1

U=$(git rev-parse --abbrev-ref --symbolic-full-name @{u})
M=$(git merge --ff-only "$U" 2>&1) && exec git rev-parse --short HEAD
A=$(git rev-list --count "$U"..HEAD)
[ "$A" -gt 0 ] || {
  echo "$M"
  echo 'FATAL error: blocked above; history has NOT diverged. Nothing deleted.'
  exit 1
}
git diff --quiet && git diff --cached --quiet || {
  echo 'FATAL error: local edits to tracked files - not resetting. Nothing deleted.'
  exit 1
}
echo "diverged: $A commit(s) - resetting"
git reset --hard "$U" || exit 1
git rev-parse --short HEAD
