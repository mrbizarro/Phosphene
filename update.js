// =============================================================================
// update.js — THIN AND STABLE. Pull, then delegate to the post-pull tree.
// =============================================================================
//
// THIS FILE SHOULD IDEALLY NEVER BE EDITED AGAIN. Everything that can break
// lives in `scripts/post_update.sh`.
//
// WHY. Pinokio wires the menu item as { text: "Update", href: "update.js" } —
// it loads this file from disk INTO MEMORY and then executes its steps. Step 1
// below is what pulls the repo, so the repo moves to the new version MID-RUN
// and execution continues from the STALE in-memory copy. cocktailpeanut (who
// wrote Pinokio) hit this himself on 2026-08-12 and confirmed the sequencing
// with timestamps: the repo moved to v3.8.0 at 13:12:17 and the *old* script's
// command failed ~18 s later. His verdict was that it does not require updater
// restructuring — and it doesn't, on his side. On ours it means:
//
//   an app can never deliver a fix to its own installer on the click that
//   pulls that fix. It always lands one click late, and the user sees a
//   failure they have already downloaded the cure for.
//
// `scripts/post_update.sh` is read AFTER the pull, so a broken update step is
// fixable by shipping — the normal way. So is THE VENDORED PIN, which is why
// it lives in `scripts/pinokio/ltx_checkout.sh` and not here: a pin held in
// this file would move one click late on every single bump, forever.
//
// Full analysis: notes/update_path_sequencing.md §1 and §10.
//
// RULES FOR EDITING THIS FILE
// ---------------------------
//   - No pip/uv calls, no patches, no downloads, no trims. Those all belong in
//     scripts/post_update.sh.
//   - Assume the copy on the user's disk is ANY historical version. A change
//     here gets the scrutiny of a schema migration.
//   - Every dispatch stays under 500 chars — `node scripts/check_pinokio_scripts.js`.
//     Step 1 CANNOT be moved into a .sh: it runs before the pull, so it may
//     only reference files that already exist in the user's current tree.
//
// Note: fs.link is only declared in install.js, not here. Running it on every
// Update would migrate existing model folders into the drive without a Reset,
// conflating durability of model assets with routine code updates and adding a
// 36 GB merge step to a flow that should be fast.
module.exports = {
  run: [
    // ---- 1. Move the panel repo -------------------------------------------
    // Branch- and remote-aware, because the same update.js serves the public
    // panel (origin/main) and the private dev clone (beta/main) — the
    // explicit-origin form `git pull origin <branch>` broke when public
    // origin/dev was deleted in the 2026-05-22 split.
    //
    // MUST be one joined string, not separate array elements. Pinokio runs each
    // element of a `message` array in its OWN shell, so shell variables do not
    // survive between them: as separate elements this block silently degraded
    // `git fetch $REMOTE` to a bare `git fetch` and the divergence recovery
    // `git reset --hard $UPSTREAM` to a bare `git reset --hard`, a no-op
    // against HEAD that cannot recover from divergence at all.
    //
    // v4.0 FIX — `git pull --ff-only $UPSTREAM` HAD NEVER ONCE SUCCEEDED.
    // `$UPSTREAM` expands to "origin/main", and `git pull --ff-only origin/main`
    // passes that as the REPOSITORY argument, not a refspec:
    //     fatal: 'origin/main' does not appear to be a git repository
    // So the ff-only attempt always failed and EVERY Phosphene update since
    // Y1.002 silently took the reset --hard fallback, printing "history
    // diverged from upstream" on a repo that had never diverged. Harmless in
    // practice (a panel install carries no local commits, and neither
    // `git pull --ff-only` nor `git reset --hard` touches untracked files) but
    // it is alarming noise and it meant we had no real ff-only guard at all.
    // The bare form uses the configured upstream and fast-forwards correctly;
    // the reset --hard fallback is kept, and now only fires when it is true.
    // Measured in notes/update_path_sequencing.md §5.
    // v4.0.1 FIX — THE UPDATE IS TRANSACTIONAL NOW, AND IT WAS NOT.
    //
    // The old block ignored the fetch result and sent EVERY pull failure to
    // `git reset --hard $UPSTREAM`. `$UPSTREAM` is the LOCAL tracking ref, so
    // when the fetch was the thing that failed the reset landed on the stale
    // ref that was already checked out — a no-op that exits 0. The sequence a
    // user offline (or behind an auth failure) actually got was:
    //
    //     failed fetch → failed pull → "successful" reset onto old code →
    //     successful rev-parse → post_update runs against the old tree →
    //     Update reports success and nothing has changed.
    //
    // The same fallback also could not tell a dirty worktree from genuine
    // divergent history, so it answered "you have local edits" by destroying
    // them.
    //
    // Split into two steps, both short enough for the dispatch gate, and the
    // ordering is the guarantee: NOTHING moves the tree until a fetch has
    // provably succeeded, and reset only ever runs against a ref that fetch
    // just refreshed.
    {
      method: "shell.run",
      params: {
        message: [
          "U=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)",
          "[ -n \"$U\" ] || { echo 'FATAL: no upstream configured'; exit 1; }",
          "echo \"updating $(git rev-parse --abbrev-ref HEAD) from $U\"",
          "git fetch \"${U%%/*}\" || { echo 'FATAL: fetch failed (offline?) - nothing changed, re-run Update'; exit 1; }"
        ].join("\n")
      }
    },
    // Converge. A reset is reached ONLY on proven history divergence.
    //
    // v4.0.1 second pass — THE UNTRACKED-FILE DELETION PATH. The previous
    // version classified every merge failure that left tracked files clean as
    // "divergence" and reset. But `git diff --quiet` IGNORES UNTRACKED FILES,
    // so the most common non-divergence failure — an untracked path that a
    // newly tracked upstream path would overwrite — passed both checks, and
    // `git reset --hard` then deleted the user's file as the "obstruction".
    // A user who dropped a note or a config beside the app lost it to an
    // update that had no local commits to reconcile in the first place.
    //
    // Divergence is now PROVEN, not inferred from what else failed:
    // `git rev-list --count $U..HEAD` is the number of commits this clone has
    // that upstream does not. Zero means the history did not diverge, whatever
    // made the merge fail — so we stop and print git's own error, which names
    // the obstructing paths. Nothing is deleted on that path, ever.
    // THE OBSTRUCTION GUARD, and it runs BEFORE anything can move the tree.
    //
    // The rev-list proof added in the previous pass only covered the
    // zero-local-commit case. A clone that has genuinely diverged (A > 0) AND
    // holds an untracked file where upstream now tracks one still reached
    // `reset --hard`, because both `git diff` checks ignore untracked files —
    // so the reset deleted it while legitimately reconciling real divergence.
    // Divergence and obstruction are independent facts and the destructive
    // step must clear BOTH.
    //
    // The set is computed from paths the fetched tree will write, then checked
    // against the worktree itself. That direction is deliberate: `git status`
    // omits ignored files and reports only leaf paths, so it misses both an
    // untracked FILE where upstream needs a DIRECTORY and an untracked
    // DIRECTORY where upstream needs a FILE. Walking each upstream path toward
    // its first existing worktree ancestor covers exact and parent/child
    // collisions, including ignored files, without enumerating a multi-GB
    // ignored cache. A path already present in HEAD is tracked and belongs to
    // the tracked-dirty/divergence checks below, not this obstruction set.
    {
      method: "shell.run",
      params: {
        message: [
          "O=$(git diff --name-only --diff-filter=ADRT HEAD '@{u}'|while IFS= read -r f;do git cat-file -e \"@{u}:$f\" 2>/dev/null||continue;git cat-file -e \"HEAD:$f\" 2>/dev/null&&continue;p=$f;while :;do if [ -e \"$p\" ]||[ -L \"$p\" ];then git cat-file -e \"HEAD:$p\" 2>/dev/null||echo \"$p -> $f\";break;fi;[ \"${p%/*}\" != \"$p\" ]||break;p=${p%/*};done;done)",
          "[ -z \"$O\" ]||{ echo \"$O\";echo 'FATAL: untracked/ignored paths obstruct Update. Move them; nothing deleted.';exit 1;}"
        ].join("\n")
      }
    },
    {
      method: "shell.run",
      params: {
        message: [
          "U=$(git rev-parse --abbrev-ref --symbolic-full-name @{u})",
          "M=$(git merge --ff-only \"$U\" 2>&1) && exec git rev-parse --short HEAD",
          "A=$(git rev-list --count \"$U\"..HEAD)",
          "[ \"$A\" -gt 0 ] || { echo \"$M\"; echo 'FATAL: blocked above; history has NOT diverged. Nothing deleted.'; exit 1; }",
          "git diff --quiet && git diff --cached --quiet || { echo 'FATAL: local edits to tracked files'; exit 1; }",
          "echo \"diverged: $A commit(s) - resetting\"",
          "git reset --hard \"$U\" || exit 1",
          "git rev-parse --short HEAD"
        ].join("\n")
      }
    },
    // ---- 2. Everything else, from the tree we just pulled ------------------
    // One step on purpose. v3.8.2's failure was a Pinokio RUN that stopped
    // after a step which exited 0, because that step's stdout carried pip's
    // "ERROR: pip's dependency resolver…" block — steps 8-17 of 18 never ran,
    // including the codec patch, and the update still presented as finished.
    // Inside one shell script Pinokio sees one step, so no amount of scary
    // text in the middle can silently drop the tail.
    //
    // `bash <path>`, never `./<path>` — no execute bit, nothing depends on git
    // preserving the mode. The `-f` guard is the one concession to history: a
    // tree that somehow did not pull (offline, permissions) would otherwise hit
    // a bare "No such file" and say nothing useful.
    {
      method: "shell.run",
      params: {
        message: "if [ -f scripts/post_update.sh ]; then bash scripts/post_update.sh; else echo 'FATAL: scripts/post_update.sh missing - the repo pull above did not land. Re-run Update.'; exit 1; fi"
      }
    }
  ]
}
