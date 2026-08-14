#!/usr/bin/env bash
# THE UNTRACKED-OBSTRUCTION GUARD — refuse the Update only when the worktree
# genuinely blocks it, and never delete anything to make room.
#
# Lifted out of update.js (455-char dispatch, a single "\n"-joined string, so
# it was one write to the pty — above the ~350 that Pinokio 8.0.x is proven to
# survive). See scripts/pinokio/README.md.
#
#   cwd : the app root, i.e. the panel repo
#
# SEMANTICS: unchanged — one shell, NO `set -e`. Almost every command in here
# exits non-zero as ordinary control flow (`git cat-file -e ... || continue`,
# `[ -d ]` probes), and the step's exit code is the final `[ -z "$O" ]` test.
# `set -e` would turn the scan itself into a fatal error.
#
# WHEN THIS RUNS: after the fetch, BEFORE anything moves the tree. It is read
# from the PRE-PULL tree — same one-click-late property as update.js itself,
# which is why update.js aborts rather than continuing if this file is absent:
# the destructive step below it must never run without its guard.
#
# =============================================================================
# WHY IT EXISTS
# =============================================================================
# `git reset --hard` deletes untracked files that stand where upstream now
# tracks one, and both `git diff` checks are blind to untracked files — so a
# clone with genuine divergence AND an untracked obstruction reached the reset
# and lost the user's file to an update that had nothing to reconcile there.
# Divergence and obstruction are independent facts; the destructive step must
# clear BOTH.
#
# The set is computed from paths the FETCHED tree will write, then checked
# against the worktree. That direction is deliberate: `git status` omits
# ignored files and reports only leaf paths, so it misses both an untracked
# FILE where upstream needs a DIRECTORY and an untracked DIRECTORY where
# upstream needs a FILE. Walking each upstream path toward its first existing
# worktree ancestor covers exact and parent/child collisions, including ignored
# files, without enumerating a multi-GB ignored cache. A path already present
# in HEAD is tracked and belongs to the tracked-dirty/divergence checks in
# update_converge.sh, not to this set.
#
# =============================================================================
# A DIRECTORY IS NOT AN OBSTRUCTION — the fleet-wide false refusal
# =============================================================================
# The first version tested `[ -e "$p" ] || [ -L "$p" ]`: it stopped at the
# first ancestor that EXISTED and flagged it, never asking what it was. But
# git cannot create a directory where a FILE sits, while it happily writes a
# new file into an existing untracked directory. So:
#
#   upstream adds notes/new.md, the user has an untracked notes/ with their
#   own mine.md in it  ->  OBSTRUCTIONS=[notes -> notes/new.md], Update aborts
#                          ...while `git merge --ff-only @{u}` succeeds
#                          cleanly and both files coexist.
#
# That made any release adding a tracked file under a directory users commonly
# have untracked or ignored — logs/, cache/, __pycache__/, mlx_models/,
# ltx-2-mlx/, minimax-h3-mlx/, or any folder dropped into the app dir — a
# FLEET-WIDE update refusal. Nobody could take the fix that unbreaks them.
#
# The rule below is measured against git, not reasoned about (`git merge
# --ff-only` on a real clone, one scenario per row):
#
#   untracked dir ancestor, nothing conflicting inside   ff-only SUCCEEDS
#   untracked dir ancestor, ignored                      ff-only SUCCEEDS
#   nested untracked dirs, nothing conflicting           ff-only SUCCEEDS
#   EMPTY dir where upstream needs a file                ff-only SUCCEEDS
#   untracked file at the exact incoming path            ff-only FAILS
#   untracked FILE where upstream needs a DIRECTORY      ff-only FAILS
#   non-empty DIRECTORY where upstream needs a FILE      ff-only FAILS
#   symlink as an ancestor                               ff-only FAILS
#
# so: at the EXACT path, anything but an empty directory obstructs; at a
# STRICT ANCESTOR, only a file or a symlink obstructs — a directory is fine
# and the walk stops there. Every scenario above is a probe in
# scripts/check_ltx_pin.js, which drives these real dispatches against real
# throwaway clones.

O=$(git diff --name-only --diff-filter=ADRT HEAD '@{u}' | while IFS= read -r f; do
  # Only paths the fetched tree actually writes, and only ones we don't track.
  git cat-file -e "@{u}:$f" 2>/dev/null || continue
  git cat-file -e "HEAD:$f" 2>/dev/null && continue

  # 1. The exact path. An empty directory holds no user data and git removes
  #    it to write the file (measured); anything else stands in the way.
  if [ -e "$f" ] || [ -L "$f" ]; then
    if [ -d "$f" ] && [ ! -L "$f" ] && [ -z "$(ls -A "$f" 2>/dev/null)" ]; then
      continue
    fi
    echo "$f -> $f"
    continue
  fi

  # 2. Strict ancestors, deepest first. A file or symlink here means git must
  #    create a directory where one sits: a real obstruction. An existing
  #    DIRECTORY is not — git writes into it — and everything above an
  #    existing directory is a directory too, so the walk can stop.
  p=$f
  while [ "${p%/*}" != "$p" ]; do
    p=${p%/*}
    if [ -f "$p" ] || [ -L "$p" ]; then
      git cat-file -e "HEAD:$p" 2>/dev/null || echo "$p -> $f"
      break
    fi
    [ -d "$p" ] && break
  done
done)

[ -z "$O" ] || {
  echo "$O"
  echo 'FATAL: untracked/ignored paths obstruct Update. Move them; nothing deleted.'
  exit 1
}
