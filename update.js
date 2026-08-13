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
    {
      method: "shell.run",
      params: {
        message: [
          "REMOTE=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null | cut -d/ -f1)",
          "UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name @{u} 2>/dev/null)",
          "echo \"updating branch: $(git rev-parse --abbrev-ref HEAD) (upstream: $UPSTREAM)\"",
          "git fetch $REMOTE",
          "git pull --ff-only || (echo 'not a fast-forward; resetting to upstream' && git reset --hard $UPSTREAM)",
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
