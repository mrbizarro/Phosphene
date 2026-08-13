#!/usr/bin/env node
/*
 * Gate: the vendored LTX pin is stated in four places and they must agree.
 *
 * ---------------------------------------------------------------------------
 * WHY
 * ---------------------------------------------------------------------------
 * The pin is not one value, it is a value plus the version string the runtime
 * reports when that value is checked out. If they drift, one of two bad things
 * happens and NEITHER produces an error at install time:
 *
 *   - pin moves, _LTX_EXPECTED_VERSION doesn't  -> every boot prints VERSION
 *     SKEW at users who are perfectly fine, and the one real skew that matters
 *     hides in the noise.
 *   - _LTX_EXPECTED_VERSION moves, pin doesn't  -> the same, inverted.
 *
 * That is exactly the blindness `871694d` was created to remove: before it, the
 * fork carried the bare string "0.14.19", so a 2.5 runtime reported
 * `match: true` against the 2.3 pin. A skew gate blind to the one skew that
 * matters is worse than no gate.
 *
 * The four places:
 *   scripts/pinokio/ltx_checkout.sh   LTX_PIN=...           the truth, and the
 *                                                           only value any
 *                                                           lane executes
 *   mlx_warm_helper.py                _LTX_EXPECTED_VERSION  what the runtime
 *                                                           must report
 *   install.js                        a documentation block naming both
 *   scripts/post_update.sh            must delegate, never inline a checkout
 *
 * The pin is a TAG of the form vX.Y.Z+ltx25.N and the expected version is the
 * same string without the leading "v" — that is not a coincidence to be
 * tolerated, it is the property that makes the two checkable against each
 * other, so it is asserted.
 *
 * Run: node scripts/check_ltx_pin.js       exit 0 = PASS
 */
const fs = require("fs")
const path = require("path")

const root = path.resolve(__dirname, "..")
const read = (p) => fs.readFileSync(path.join(root, p), "utf8")
const failures = []
const ok = (msg) => console.log("  ok    " + msg)
const fail = (msg) => { failures.push(msg); console.log("  FAIL  " + msg) }

// ---- 1. The truth ----------------------------------------------------------
const checkoutSrc = read("scripts/pinokio/ltx_checkout.sh")
const pinMatch = checkoutSrc.match(/^LTX_PIN="([^"]+)"$/m)
if (!pinMatch) {
  console.log("FAIL  scripts/pinokio/ltx_checkout.sh has no `LTX_PIN=\"...\"` line")
  process.exit(1)
}
const PIN = pinMatch[1]
console.log(`vendored pin: ${PIN}`)

// ---- 2. It is a tag, not a SHA and not a branch ----------------------------
// A 40-hex SHA on a branch is not a pin: v3.8.x fetched `feat/ltx-2.5` and
// checked out a SHA the branch head had already moved past, so one upstream
// rebase would have stranded every install with an un-fetchable pin.
if (/^[0-9a-f]{7,40}$/.test(PIN)) {
  fail(`the pin is a bare SHA (${PIN}). It must be an immutable TAG — a SHA on a branch stops being fetchable after a rebase and takes every existing install's Update button with it.`)
} else if (!/^v\d+\.\d+\.\d+\+ltx25\.\d+$/.test(PIN)) {
  fail(`the pin "${PIN}" is not of the form vX.Y.Z+ltx25.N`)
} else {
  ok("the pin is a tag of the expected shape")
}

// ---- 3. _LTX_EXPECTED_VERSION agrees ---------------------------------------
const helperSrc = read("mlx_warm_helper.py")
const expMatch = helperSrc.match(/^_LTX_EXPECTED_VERSION\s*=\s*"([^"]+)"$/m)
if (!expMatch) {
  fail("mlx_warm_helper.py has no `_LTX_EXPECTED_VERSION = \"...\"` line")
} else if (expMatch[1] !== PIN.replace(/^v/, "")) {
  fail(`_LTX_EXPECTED_VERSION is "${expMatch[1]}" but the pin is "${PIN}" (expected "${PIN.replace(/^v/, "")}"). Move them together or the skew gate cries wolf forever.`)
} else {
  ok(`_LTX_EXPECTED_VERSION == "${expMatch[1]}"`)
}

// ---- 4. install.js documents the same pin, and executes none of it ----------
const installSrc = read("install.js")
if (!installSrc.includes(PIN)) {
  fail(`install.js does not mention the pin "${PIN}" — its comment block is the one a human reads before bumping.`)
} else {
  ok("install.js documents the pin")
}
if (!installSrc.includes("bash scripts/pinokio/ltx_checkout.sh")) {
  fail("install.js no longer delegates to scripts/pinokio/ltx_checkout.sh — the two lanes must share one checkout implementation.")
} else {
  ok("install.js delegates the checkout")
}

// ---- 5. Nobody else checks out the vendored tree ----------------------------
// A second copy of the checkout is a fix that half-lands; that is precisely how
// v3.8.1's guard had to be written twice.
for (const f of ["install.js", "update.js", "scripts/post_update.sh"]) {
  const src = read(f)
  if (/git\s+checkout\s+[0-9a-f]{7,40}/.test(src)) {
    fail(`${f} checks out a bare SHA directly — call scripts/pinokio/ltx_checkout.sh instead.`)
  }
}
if (!read("scripts/post_update.sh").includes("scripts/pinokio/ltx_checkout.sh")) {
  fail("scripts/post_update.sh does not call scripts/pinokio/ltx_checkout.sh")
} else {
  ok("post_update.sh delegates the checkout")
}

// ---- 6. update.js stays THIN -----------------------------------------------
// It is read BEFORE the pull, so anything it does itself can only be fixed one
// click late. notes/update_path_sequencing.md §10.
const updateSrc = read("update.js")
const banned = [
  [/uv\s+pip\s+install/, "a uv/pip install"],
  [/\bpip\s+install/, "a pip install"],
  [/patch_\w+\.py/, "a patch script"],
  [/\bhf\s+download/, "a weight download"],
  [/fetch_pack_release\.py/, "a weight download"],
  [/\brm\s+-f\b/, "a model trim"],
]
let thin = true
for (const [re, what] of banned) {
  // Comments are allowed to DISCUSS these; only executable strings count.
  const code = updateSrc.split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n")
  if (re.test(code)) {
    fail(`update.js contains ${what}. It must stay thin: pull, then delegate to scripts/post_update.sh, which ships WITH the pull and can therefore be fixed by shipping.`)
    thin = false
  }
}
if (thin) ok("update.js is thin — no installs, patches, downloads or trims")

// ---- the update is transactional -------------------------------------------
// This used to assert one string: that the ff-only pull was the BARE form,
// because `git pull --ff-only $UPSTREAM` passes "origin/main" as the REPOSITORY
// argument and had never once succeeded (notes/update_path_sequencing.md §5).
// v4.0.1 replaced the pull with an explicit fetch + merge so the fetch result
// can be checked, which made that assertion obsolete while the property it was
// protecting got MORE important. So the gate now checks the property.
const updCode = updateSrc.split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n")

// 1. The original bug must never come back in either form.
if (/git pull --ff-only \s*\$/.test(updCode)) {
  fail("update.js passes $UPSTREAM to `git pull --ff-only`, which git reads as a REPOSITORY argument. That form has never once succeeded — every update silently took the reset --hard fallback (notes/update_path_sequencing.md §5).")
} else {
  ok("no `git pull --ff-only $UPSTREAM` — the form that never worked")
}

// 2. A failed fetch must stop the update. The whole class of "Update reported
//    success while staying on stale code" hangs off this one guard: $UPSTREAM is
//    the LOCAL tracking ref, so resetting to it after a failed fetch is a no-op
//    that exits 0 and lets post_update run against the old tree.
if (!/git fetch[^\n]*\|\|[^\n]*exit 1/.test(updCode)) {
  fail("update.js does not make a failed `git fetch` fatal. Without it a network or auth failure becomes: failed fetch -> failed pull -> 'successful' reset onto the stale tracking ref -> post_update against old code -> Update reports success.")
} else {
  ok("a failed fetch aborts the update")
}

// 3. reset --hard must never be reachable without a clean-tree check, or the
//    updater answers "you have local edits" by destroying them.
if (/git reset --hard/.test(updCode) && !/git diff --quiet/.test(updCode)) {
  fail("update.js can `git reset --hard` without first proving the worktree is clean — dirty-worktree and genuine non-fast-forward must not share one blunt fallback.")
} else {
  ok("reset --hard is guarded by a clean-worktree check")
}

console.log("")
console.log(failures.length ? `RESULT: FAIL (${failures.length})` : "RESULT: PASS")
process.exit(failures.length ? 1 : 0)
