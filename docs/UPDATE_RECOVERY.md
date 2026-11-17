# Recovering an unfinished update

The studio and Pinokio Update share a local update record. Update fetches a
candidate, keeps a Git reference to the previous source, moves to the candidate,
and runs its dependency repair. It reports the update as prepared until that
candidate starts and binds the studio listener. A restart request alone does
not count as success.

If an update stops, use **Try again** in the existing update banner or run
**Update** again in Pinokio. The same candidate resumes. A second request cannot
replace a candidate awaiting repair or restart. After a successful boot, a new
Update can fetch a newer candidate.

Local tracked edits, local commits and conflicting untracked or ignored files
are kept. Save or commit edits before retrying. A copy whose local commits sit
on top of the newest upstream source is already up to date. Divergent history
stops for manual reconciliation instead of resetting local commits: Update
says so before it records a candidate or moves anything, and works again once
the commits are rebased or merged onto upstream. Update never runs
`git clean`, and it never stashes or discards edits, or resets the source
with `--hard`, without being asked.

Being asked happens in one place: the studio's own Update button, when
tracked files have uncommitted edits and no update is in flight. It names the
files and offers three answers. **Set aside and update** runs `git stash`
(tracked edits only, message `Phosphene update <UTC time>`); bring them back
with `git stash pop` after the new version starts. Update never pops them
itself, because they could clash with the new version's changes to the same
files. **Discard my changes** asks a second time, naming the files, then
restores tracked files to the current commit; it cannot be undone from the
studio. (The record's `set_aside.commit` names an unreferenced snapshot that
Git prunes on its own schedule; it is for support, not an undo.) **Cancel**
changes nothing. Untracked and ignored files are never touched. The answer is
honoured only before a candidate is recorded: after the fetch, and not when
the copy is already up to date or has diverged. While an update is in flight
a dirty tree may be an interrupted checkout, so Update keeps refusing. If an
update stops after a stash, its message says the edits are in the stash.
Pinokio's Update has no dialog and still stops on local edits.

## What the record preserves

The checkout's Git directory contains `phosphene-update.json`. Find that directory
with `git rev-parse --absolute-git-dir` from the app folder. The record gives the
phase, exact candidate SHA and `previous_ref`. The reference keeps the previous
Git source reachable even if Git expires its reflog. This is source recovery,
not a backup of installed Python packages.

A failed dependency command records its exit code and the exact retry command.
It keeps the candidate in place so retry uses the same repair scripts. Completed
repair is skipped on retry. If a process dies after a command finishes but before
its completion is recorded, repair must run again. Package commands therefore
need to tolerate retries. No exactly-once package installation is promised.

Do not restore source and assume the Python environment was restored with it.
The old process can still be running while packages on disk have changed. An
unfinished update blocks new queued renders and restart; repair must finish
first. Models, outputs and settings are not transaction storage.

## If Git itself was interrupted

Update refuses to reset a partial checkout or remove an `index.lock` for you.
Keep the update record and edited files. Inspect `git status` and `git diff`
from the app folder. The record's `previous_ref` identifies the complete previous
source; `git show <previous_ref>:<path>` reads an original file without changing
the checkout. You can export that source to a separate archive with
`git archive <previous_ref> --output=<new-archive-path>`.

Resolve the Git interruption while preserving your edits, then retry Update.
If source moved outside the recorded previous/candidate commits after replacement
began, Update stops and names the retained reference. Do not remove the journal
to bypass the check after dependency work has started.

## Scope

This applies to the Git-based studio updater. The native Mac app's own updater
is separate. Historical launchers execute their already-loaded update logic on
the first update that brings in this change; that first hop does not acquire a
transaction record retroactively. Linux still uses its separate install script.
A successful startup receipt proves studio startup, not a render or a complete
from-zero release validation.
