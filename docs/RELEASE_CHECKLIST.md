# Release checklist — DON'T ship broken

Hard rule: **a release is not validated until it has been run FROM ZERO** — the
state a brand-new user is in when they click Install in Pinokio. "It works on my
machine" is not validation; my machine has every model cached, a token saved, and
every dep already installed. New users have none of that. Most of the things that
have shipped broken (gated model needing a token, mflux not bundled, the missing
upscaler "mosaic", the scary pip dependency block) were invisible on a warm dev
box and only showed up from zero.

Run these BEFORE promoting `dev`/`beta` → public `main`. All must pass.

## 0. The mechanical gates, in one command

```
bash scripts/release_gates.sh          # everything (~2 min)
bash scripts/release_gates.sh --fast   # skips the two slowest
```

It runs every check on this page that a command can run — `py_compile`, the
three `node` gates, `assert_registry`, `assert_schedules`, `check_output_codec`
(§3a), the whole root `test_*.py` sweep, and `scripts/test_*.py` **through
pytest** (three of those suites are pytest-style; `python -m unittest` collects
zero tests from them and prints a green "Ran 0 tests / OK" that asserts
nothing). It prints a PASS/FAIL/SKIP table and exits non-zero on any failure.
**A SKIP is not a PASS** — it means the gate could not run.

**Key every public step on the runner's exit code, explicitly.** On 2026-09-08 a
release chain written as `set -e; …; [ "$G" = "0" ]; git push …` ran straight
through a red gate (one UI test) and 4.12.2 went out with a failing test in its
tree. `set -e` is not a gate. Write `if [ "$G" != "0" ]; then exit 1; fi` right
after the gate line, or chain every public step with `&&` from that test onward.

This does NOT replace the sections below. From-zero, the Ideogram eyeball, the
smoke renders, the weight mirror and the update-path gate all need a human and
a real machine.

## Music — from-zero install and listening gate

- [ ] On a fresh Pinokio install with empty music weights and no cached HF
  files, use **Install the music engine (YuE2, ~11 GB)**. Verify the isolated
  Python 3.12 venv, pinned checkout, frozen sync, resumable staged downloads,
  full SHA-256 check and clean generator tree. Open Audio → Compose, make a
  real song, play its WAV, inspect the sidecar and credit, and use **Drive
  video** without an automatic submission. Test Stop, Repair with weights
  kept, and Update's offline check. Record memory and timing at Draft/Final;
  the ETA table stays unmeasured until receipts exist. The owner's listening
  verdict and explicit ship instruction are required. YuE2: the owner ordered
  the ship on 2026-09-17 (v4.14.0); the real 10.5 GB download on a clean Mac is
  still unrun.

## 1. Ideogram — from-zero render gate (the one that keeps biting us)

```
bash scripts/validate_ideogram_fresh.sh
```

It **deletes the Ideogram model** and re-renders with the un-gated mirror and
**no Hugging Face token** — exactly a new user's first render. It fails loudly if:
- `mflux-generate-ideogram4` is missing (install.js didn't bundle mflux),
- the model can't download without a token (gating regressed),
- the render crashes or produces no/empty PNG.

Exit 0 = pass. Then **eyeball `/tmp/validate_ideo_fresh.png`** — the text must be
legible (an auto-check can't tell crisp typography from a mosaic).

## 2. Install scripts produce no scary errors

The mflux pack must install via **uv, not plain pip** (mlx-vlm is `--no-deps`, so
plain pip dumps an "ERROR: dependency resolver…" block that makes the update look
broken). Grep to confirm no plain-pip mflux install slipped back in:

```
grep -n "pip install 'mflux" install.js update.js   # expect NOTHING (all uv)
node -c install.js && node -c update.js              # syntax
```

## 3. Video modality smoke (when LTX/ltx-2-mlx is touched)

Render T2V + I2V at Q4 and confirm no mosaic (frames, not just exit code). The
mosaic was a missing `spatial_upscaler_x2_v1_1.safetensors` in the Q4 download —
if you touch `required_files.json` / `install.js` / `update.js` model lists,
re-confirm a fresh Q4 install pulls it.

## 3a. Output codec — render-level gate (MANDATORY, the v3.8.1 class)

v3.8.1 shipped fleet-wide silent 4:2:0: the codec patch never ran, and every
gate that existed was static — `node --check`, shell-line ceilings, patch
exit codes. Nothing ever looked at a produced file, so renders completed,
looked like renders, and carried avoidable chroma compression on faces for a
whole release. This gate looks at the file. Run it after the section-3 smoke
renders (any panel render on this install works), **every release, whether or
not LTX was touched** — it is one ffprobe:

```
python3 scripts/check_output_codec.py
```

It picks the newest panel-rendered LTX clip under `mlx_outputs/` (H3 clips
are skipped — their mux is not the patched encoder) and fails non-zero
unless BOTH hold:

- **pix_fmt matches what was requested** — the sidecar's
  `output_codec.pix_fmt` (recorded at render time), or `LTX_OUTPUT_PIX_FMT`
  if set (helper-CLI renders), else the patched default `yuv444p`;
- **`+faststart` is present** (moov before mdat) — the unpatched upstream
  encoder writes neither faststart nor reads the env vars, so this catches
  the patch-bypassed class even when the requested pix_fmt happens to equal
  upstream's hardcoded yuv420p (the "standard" preset), where the pix_fmt
  check alone is blind.

When the sidecar names a separate `native_output` (upscaled deliveries), the
NATIVE file is what gets checked — the panel-side export re-encode always
applies the settings codec + faststart and would mask a broken patch
underneath it. Explicit file: `python3 scripts/check_output_codec.py
<path.mp4>`. Exit 0 = pass; **anything else = DO NOT PROMOTE.**

Defense in depth: every install continuously self-reports the same check
(same script, imported) via `/status.model_integrity.output_codec`, with a
red banner + boot warning on mismatch — so even a regression that slips a
gate gets named on users' own machines instead of shipping silently.

## 3b. Weight mirror — the packs GitHub carries, not HuggingFace

Only applies to packs whose `required_files.json` entry has a `mirror` block
(the LTX-2.5 ones). They are quantised by us and published as release assets, so
unlike an `hf download` repo **nothing publishes them but us** — and a pack that
is not published is a fresh install with no weights, silently, only for new
users. That is exactly how LTX-2.5 shipped as the default generation with no
download step behind it.

If you touched a mirrored pack's contents, its file list, or its `tag`:

```
python3 scripts/publish_pack_release.py --repo-key <key> --dry-run   # rehearse
python3 scripts/publish_pack_release.py --repo-key <key> \
    --tag <tag> --release-repo mrbizarro/Phosphene --target <PUBLIC-MAIN-SHA> \
    --license LICENSES/LTX-2.x-Community-License.md \
    --notice  LICENSES/NOTICE-ltx25-weights.md
```

**The tag goes on a PUBLIC `main` commit, never a dev/beta one** — check with
`gh api repos/mrbizarro/Phosphene/commits/main --jq .sha` first. The publisher
refuses a pack missing anything `required_files.json` calls mandatory; do not
work around that refusal, it is the guard against shipping a pack the panel
reports incomplete on arrival.

Then prove it from zero, into an **empty** directory, against the real release:

```
ltx-2-mlx/env/bin/python3.11 scripts/fetch_pack_release.py \
    --repo-key <key> --dest /tmp/from-zero/<pack>
ltx-2-mlx/env/bin/python3.11 scripts/fetch_pack_release.py \
    --repo-key <key> --dest /tmp/from-zero/<pack> --check-only
```

Exit 0 on both. For a DiT pack, finish with a real loader round-trip on the
reassembled transformer (`verify_load_ltx_dit` in `scripts/quantize_ltx.py`) —
sha256 proves the bytes arrived, only the loader proves they are a model.

## 4. Version + compile

```
ltx-2-mlx/env/bin/python -m py_compile mlx_ltx_panel.py image_engine.py
cat VERSION                                          # bumped for this release
```

## 5. Update-path gate — the release most users actually receive

From-zero is the gate above, and it stays mandatory. It is also **not the path
most people take**: two of the last five releases (v4.4.0, v4.8.1) were fixes to
the update path itself, and both of them were invisible to a from-zero install
by construction. v4.4.0 shipped because Pinokio reads `update.js` into memory
and *then* runs it, so an update that fixes the updater always landed one click
late. v4.8.1 shipped because **an update ships code, not weights** — installs
that already had H3 kept running the full bf16 engine after v4.8.0 promised them
the compact one, and nothing told them why. A from-zero box has neither problem:
it gets the new `update.js` as a file and downloads every weight anyway.

So run this too, on a real previous install:

```
# 1. install the PREVIOUS public release into a scratch dir
git clone https://github.com/mrbizarro/phosphene.git /tmp/upd-gate
git -C /tmp/upd-gate checkout <PREVIOUS-TAG>          # e.g. v4.8.0
#    then install it through Pinokio (or run install.js) so it has a real venv
#    and the packs the previous release shipped

# 2. run the Update flow — the same button a user clicks, not `git pull`
#    (update.js is read into memory BEFORE it runs; a thin update.js that
#     delegates to a file read AFTER the pull is what makes fixes land on time)

# 3. assert BOTH of these, not just the first
cat /tmp/upd-gate/VERSION                              # == this release's VERSION
```

**Both halves must arrive, and they are different mechanisms:**

- **Code version** — `VERSION` on disk, the panel's `/version`, and the
  stale-process pill all agree with what was promoted.
- **New weight-pack requirements** — anything this release ADDED to
  `required_files.json`, or any engine/quantisation it made the new default,
  is actually **on disk in the updated install**, not merely referenced. If a
  release changes what a pack must contain, the update must build or fetch it;
  a headline the update cannot keep is a v4.8.0. Confirm from the updated
  install's own `/status` (`model_integrity`), and confirm the panel does not
  quietly fall back — a fallback that is not named in the render log is the
  bug, not the fallback.

If the previous release's install cannot reach this release's headline feature
by pressing Update, **do not promote** — ship the delivery fix in the same
release.

---

## Promoting — a curated SNAPSHOT, never a branch push

> ### ⛔ NEVER push the `dev` branch onto public `main`
>
> Not as a refspec, not fast-forward, not with any flag. This file used to end
> by telling you to, and it was wrong.
>
> Public `main` is **not** `dev` under a different name. It is a chain of
> single-parent **snapshot** commits, one per release
> (`release(v4.8.1): …` → `release(v4.8.0): …` → …), each carrying a tree and
> nothing else. A branch push would publish the **entire dev history** — 465+
> commits of internal notes, dead experiments, abandoned branches, and every
> message written on the assumption nobody outside would read it. It is not
> undoable in any way that matters: it is public the second it lands.
>
> `test_release_coherence.py` fails if that command ever reappears in this file.

The promote is: build the tree you want to publish, remove what must not go
public, commit that tree onto `origin/main` with `commit-tree`, **verify the
result before it is pushed**, then push exactly one commit.

```
git fetch origin

# 1. stage dev's tree, then remove what public main does not carry
git read-tree --empty
git read-tree <DEV-SHA>
git rm --cached -q <scratch paths>        # currently: the dev-only scratch
                                          # test files not on main — diff first,
                                          # never guess this list
TREE=$(git write-tree)

# 2. one snapshot commit, parented on public main
COMMIT=$(git commit-tree "$TREE" -p origin/main \
    -m "release(vX.Y.Z): <the release headline>")

# 3. LEAK-VERIFY before pushing — this is the step that cannot be skipped
git diff --stat origin/main "$COMMIT"     # read EVERY path; expect only this
                                          # release's changes
diff <(git ls-tree -r --name-only "$COMMIT" | sort) \
     <(git ls-tree -r --name-only <DEV-SHA> | sort)   # only the removals above
git log --format=%P -1 "$COMMIT"          # exactly ONE parent == origin/main

# 4. push the single commit, then tag it
git push origin "$COMMIT":refs/heads/main
git tag -a vX.Y.Z "$COMMIT" -m "vX.Y.Z"
git push origin vX.Y.Z
gh release create vX.Y.Z --repo mrbizarro/Phosphene ...
```

Reminders that bit before: the git index is shared, so `read-tree`/`rm --cached`
leaves it staging the promote tree — reset it when you are done. And any weight
pack published for this release must be tagged on the **public main commit you
just pushed** (§3b), which means the packs go up *after* step 4, not before.

## 6. Announce on Pinokio — part of every release

A release is not done until its Pinokio post is live and the post's URL is in
the release report. Owner, 2026-08-05 and again 2026-09-13: "This should be
part of the protocol for releases."

- **Check the login BEFORE promoting.** Open the app page on pinokio.co in the
  owner's Chrome. A **Log in** link in the header means the session dropped:
  tell the owner at once, so he signs in while the gates run. Never type
  credentials on his behalf.
- **Enter through the app page's Post button** (owner-only, top right).
  Navigating straight to `/posts/new` bounces and can drop the session.
- **Size the post to the release.** x.y.Z: a short text post (headline title,
  4–6 bullets in user language, credit the reporters, link the release).
  x.Y.0: media-rich, with a hero image or clip made from real Phosphene output,
  screenshots of the new surface, and the fuller story.
- **Paste ASCII only** (straight quotes, `-` instead of dashes). Non-ASCII
  mojibakes through the clipboard paste.
- **Media goes in through the composer's own file input**, so it lands on
  Pinokio's CDN before publishing.
- **Publish → Review → pick the feed cover → Publish.** Write the post URL into
  the release's `docs/STATE.md` entry.
- **Missed a release?** The next post covers every unannounced version, one
  section each.

---

If any gate fails, **do not ship** — that's the whole point of this file.
