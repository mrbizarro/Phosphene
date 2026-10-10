# Contributing to Phosphene

**Short version: yes, send it.** Bug fixes, new features, new engines, docs,
copy fixes, a better ETA estimate — all of it is welcome, and none of it needs
to be asked about first.

---

## Read this before you open a PR

**Your PR will say CONFLICTING. That is expected and it is not about your work.**

Public `main` is published as a curated snapshot commit rather than by merging
branches — each release replaces the tree in one commit. Nothing can
fast-forward onto that, so *every* incoming PR shows as conflicting regardless
of how clean the patch is.

It still gets read, and it still gets shipped. What happens is:

1. Your PR is reviewed as a patch — the diff, the reasoning, the validation.
2. If it holds up, the change is applied to the working tree.
3. It ships in the next release, and **you are credited by name in the release
   notes and usually in the commit message**.
4. Your PR is closed with a comment saying exactly what landed and where.

Closed does not mean rejected here. Two recent examples: a 48 GB RAM-gate
mismatch and a Pinokio shell-dispatch hang were both contributor-diagnosed;
the second one became an enforced build gate across all ten launcher scripts,
which is more than the original patch asked for.

If a change is *not* going to land, you will be told that and why, in the PR.

---

## What is most useful

**A good bug report is worth as much as a patch.** The most valuable
contributions this project has had were reports where someone captured the
actual crash line, showed what *did* work on the same machine, and narrowed the
difference. That turns an unreproducible report into a ten-minute fix.

If you are reporting a render problem, the things that matter:

- **Chip and macOS version** (`Apple M2 Max`, `macOS 26.5.2`) — several bugs
  have been chip-specific, including one where macOS reports the same failure
  under a different name on different silicon.
- **Phosphene version** and the vendored engine tag (both are in the header).
- **What the panel log says**, and if it crashed hard, what the process printed
  to stderr.
- **Whether the engine CLI reproduces it** outside the panel. This single fact
  splits "engine bug" from "panel bug" immediately.

## Where code goes — read the map first

The frontend and the HTTP routes are no longer inside `mlx_ltx_panel.py`.
**`docs/ARCHITECTURE.md` is the map**, and every kind of change has exactly
one home: styles in `webapp/style/panel.css`, markup in `webapp/index.html`,
JS in the one `webapp/js/` module that owns that screen (startup calls in
`main.js`, which loads last on purpose), and every HTTP endpoint as a
registered handler in `panel/routes_*.py` — never an if-arm in
`do_GET`/`do_POST`. These aren't conventions, they're enforced:
`test_routes.py`, `test_no_duplicate_defs.py`, `test_panel_assets.py` and
`node scripts/lint_webapp.mjs` fail the build on a violation. A patch that
puts code in the right home reviews in minutes; one that reopens the old
monolith gets sent back with a link to the map.

## New engines are welcome

The engine table is data-driven on purpose. Adding one is an `ENGINES` entry, a
`<symbol>` for its mark, and a probe — the picker, the tooltips, the mode
gating, the install offer and the CSS accents all derive from that row. If you
want to add an engine, open an issue first so we can agree on the shape before
you write the adapter.

## Things that will get pushed back on

- **A dependency added without a reason in the commit message.** Every pin in
  this repo is a paid lesson and they are documented as such in `CLAUDE.md`.
- **A change to a render path with no evidence it is identical where it should
  be.** Byte-identical output at a fixed seed, or a measured difference — not
  "looks the same to me".
- **Long Pinokio shell payloads.** `node scripts/check_pinokio_scripts.js`
  enforces this and will fail the build.
- **A claim in user-facing copy that is not true on a fresh install.**

## Before you send

```bash
bash scripts/release_gates.sh          # the whole battery in one command
```

That runs the pin checks, the launcher payload gate, the codec check, the
frontend lint and every panel test suite, and prints a PASS/FAIL table. It
must exit 0. (`--fast` skips the two slowest gates while iterating.)

Run `npm run lint:anti-slop` for the generic Oxlint rules. Fix findings your
changes introduce; findings in untouched files remain outside your patch.
These development dependencies do not affect the panel runtime.

Tests are not a formality here. Several of them exist because a thing shipped
broken in a way that looked fine — they encode the failure, not the feature.

## Licence

By contributing you agree your work ships under the repository's licence.
Third-party model weights and brand marks are **not** vendored into this repo;
if your change needs an asset, it downloads it at install time.
