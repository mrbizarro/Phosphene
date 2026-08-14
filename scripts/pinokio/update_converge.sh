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
# ORDER IS THE GUARANTEE. This runs after scripts/pinokio/update_obstruction_
# guard.sh, which has already proven nothing untracked is in the way. Reaching
# `reset --hard` requires BOTH of these to have passed:
#
#   1. the obstruction guard (its step, before this one), and
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

U=$(git rev-parse --abbrev-ref --symbolic-full-name @{u})
M=$(git merge --ff-only "$U" 2>&1) && exec git rev-parse --short HEAD
A=$(git rev-list --count "$U"..HEAD)
[ "$A" -gt 0 ] || {
  echo "$M"
  echo 'FATAL: blocked above; history has NOT diverged. Nothing deleted.'
  exit 1
}
git diff --quiet && git diff --cached --quiet || {
  echo 'FATAL: local edits to tracked files'
  exit 1
}
echo "diverged: $A commit(s) - resetting"
git reset --hard "$U" || exit 1
git rev-parse --short HEAD
