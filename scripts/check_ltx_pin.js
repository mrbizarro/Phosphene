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

// ---- 5b. The Q8 launcher speaks the active generation ---------------------
// The fetch lane was made version-aware first, but its two notifications and
// failure text kept advertising 2.5/30 GB/High add-on to an LTX23 pin. Drive
// the launcher's real resolver for both generations; checking only today's
// default is how the stale 2.3 branch survived the previous gate.
try {
  const q8Launcher = require(path.join(root, "download_q8.js"))
  const q23 = q8Launcher._resolveQ8Offer(root, "ltx23")
  const q25 = q8Launcher._resolveQ8Offer(root, "ltx25")
  if (q23.key !== "q8" || q23.size !== "37 GB"
      || !/LTX.?2\.3/.test(q23.startHtml)
      || /2\.5|29\.5 GB|one more download/.test(q23.readyHtml)) {
    fail(`LTX23 Q8 notifications are version-wrong: ${JSON.stringify(q23)}`)
  } else {
    ok("LTX23 Q8 notifications name its 37 GB pack and no 2.5 add-on")
  }
  if (q25.key !== "q8_25" || q25.size !== "30.02 GB"
      || !/LTX-2\.5/.test(q25.startHtml)
      || !/High add-on \(~29\.5 GB\)/.test(q25.readyHtml)) {
    fail(`LTX25 Q8 notifications are version-wrong: ${JSON.stringify(q25)}`)
  } else {
    ok("LTX25 Q8 notifications name its pack and separate High add-on")
  }
} catch (e) {
  fail(`download_q8.js could not resolve both generation offers: ${e.stack || e}`)
}
const q8Shell = read("scripts/pinokio/q8_weights.sh")
if (!/ltx23\).*SIZE_GB=37/.test(q8Shell)
    || !/q8_25;.*SIZE_GB=30\.02/.test(q8Shell)
    || !/pack needs about \$SIZE_GB GB/.test(q8Shell)) {
  fail("q8_weights.sh failure copy does not follow the resolved generation size")
} else {
  ok("Q8 download failure copy follows the resolved generation size")
}

// ---- 6. update.js stays THIN -----------------------------------------------
// It is read BEFORE the pull, so anything it does itself can only be fixed one
// click late. See the header of `scripts/post_update.sh`.
// Optional path makes the behavioural gate mutation-testable:
//   node scripts/check_ltx_pin.js /tmp/previous-update.js
// Round 4 proved that a gate which can only inspect the current file can pass
// beside the same old unsafe implementation forever.
const updatePath = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(root, "update.js")
const updateSrc = fs.readFileSync(updatePath, "utf8")
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
// argument and had never once succeeded — measured against the real update
// path, not reasoned about.
// v4.0.1 replaced the pull with an explicit fetch + merge so the fetch result
// can be checked, which made that assertion obsolete while the property it was
// protecting got MORE important. So the gate now checks the property.
const updCode = updateSrc.split("\n").filter((l) => !/^\s*\/\//.test(l)).join("\n")

// 1. The original bug must never come back in either form.
if (/git pull --ff-only \s*\$/.test(updCode)) {
  fail("update.js passes $UPSTREAM to `git pull --ff-only`, which git reads as a REPOSITORY argument. That form has never once succeeded — every update silently took the reset --hard fallback.")
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

// 3+4. THE DESTRUCTIVE PATH IS ASSERTED BY BEHAVIOUR, NOT BY STRINGS.
//
// The previous two checks tested that `git diff --quiet` and `rev-list --count`
// APPEARED SOMEWHERE in the file. Both were present and the updater still
// deleted an untracked file: a clone with genuine divergence AND an untracked
// path that upstream now tracks passed every string test and reached
// `reset --hard`. A gate that greps for the fix cannot see the case the fix
// misses — so this one builds a real repository, drives the REAL dispatches out
// of update.js, and shims `git reset` so any call is recorded rather than run.
//
// Three scenarios, one rule: reset is allowed ONLY when history truly diverged
// AND nothing untracked is in the way.
const { execFileSync, spawnSync } = require("child_process")
const os = require("os")

function updateDispatches() {
  const mod = require(updatePath)
  return (mod.run || [])
    .filter((st) => st.method === "shell.run" && st.params && st.params.message)
    .map((st) => st.params.message)
    .filter((m) => !/post_update/.test(m))
}

function probe(scenario) {
  const work = fs.mkdtempSync(path.join(os.tmpdir(), "phos-upd-"))
  const G = { cwd: null, env: Object.assign({}, process.env, {
    GIT_CONFIG_GLOBAL: "/dev/null", GIT_CONFIG_SYSTEM: "/dev/null" }) }
  const git = (cwd, args) => execFileSync("git", args, { cwd, env: G.env, stdio: "pipe" })
  const up = path.join(work, "up"), clone = path.join(work, "clone")
  fs.mkdirSync(up)
  git(up, ["init", "-q", "."])
  git(up, ["config", "user.email", "a@b"]); git(up, ["config", "user.name", "t"])
  fs.writeFileSync(path.join(up, "f.txt"), "v1\n")
  // update.js's pre-pull steps dispatch `bash scripts/pinokio/*.sh`, so the
  // fixture has to be a tree that CARRIES them — they ship in the same commit
  // as update.js, which is the whole reason a pre-pull step is allowed to call
  // them. Committed in the base revision so every scenario's clone has them
  // tracked and identical on both sides (they never appear in the diff).
  const pinokioSrc = path.join(root, "scripts", "pinokio")
  fs.mkdirSync(path.join(up, "scripts", "pinokio"), { recursive: true })
  for (const f of fs.readdirSync(pinokioSrc)) {
    if (!f.endsWith(".sh")) continue
    fs.copyFileSync(path.join(pinokioSrc, f),
                    path.join(up, "scripts", "pinokio", f))
  }
  git(up, ["add", "f.txt", "scripts"]); git(up, ["commit", "-qm", "v1"])
  git(work, ["clone", "-q", up, clone])
  git(clone, ["config", "user.email", "a@b"]); git(clone, ["config", "user.name", "t"])

  let protectedPath = "notes.txt"
  let arrivalPath = null      // an upstream path that MUST land (non-blocking)
  if (scenario === "mixed" || scenario === "obstruction_only") {
    fs.writeFileSync(path.join(up, "notes.txt"), "upstream\n")
    git(up, ["add", "notes.txt"]); git(up, ["commit", "-qm", "add notes"])
    fs.writeFileSync(path.join(clone, "notes.txt"), "MINE\n")
    if (scenario === "mixed") git(clone, ["commit", "-q", "--allow-empty", "-m", "local"])
  } else if (scenario === "file_blocks_directory" || scenario === "ignored_file_blocks_directory") {
    fs.mkdirSync(path.join(up, "scratch"))
    fs.writeFileSync(path.join(up, "scratch", "private.txt"), "upstream\n")
    git(up, ["add", "scratch/private.txt"]); git(up, ["commit", "-qm", "add directory"])
    fs.writeFileSync(path.join(clone, "scratch"), "MINE\n")
    protectedPath = "scratch"
    if (scenario === "ignored_file_blocks_directory") {
      fs.appendFileSync(path.join(clone, ".git", "info", "exclude"), "\nscratch\n")
    }
    git(clone, ["commit", "-q", "--allow-empty", "-m", "local"])
  } else if (scenario === "directory_blocks_file" || scenario === "ignored_directory_blocks_file") {
    fs.writeFileSync(path.join(up, "scratch"), "upstream\n")
    git(up, ["add", "scratch"]); git(up, ["commit", "-qm", "add file"])
    fs.mkdirSync(path.join(clone, "scratch"))
    fs.writeFileSync(path.join(clone, "scratch", "private.txt"), "MINE\n")
    protectedPath = "scratch/private.txt"
    if (scenario === "ignored_directory_blocks_file") {
      fs.appendFileSync(path.join(clone, ".git", "info", "exclude"), "\nscratch/\n")
    }
    git(clone, ["commit", "-q", "--allow-empty", "-m", "local"])
  } else if (scenario === "ignored_exact") {
    fs.writeFileSync(path.join(up, "ignored.txt"), "upstream\n")
    git(up, ["add", "ignored.txt"]); git(up, ["commit", "-qm", "add ignored"])
    fs.appendFileSync(path.join(clone, ".git", "info", "exclude"), "\nignored.txt\n")
    fs.writeFileSync(path.join(clone, "ignored.txt"), "MINE\n")
    protectedPath = "ignored.txt"
    git(clone, ["commit", "-q", "--allow-empty", "-m", "local"])
  } else if (scenario === "dir_contains_nothing_conflicting"
             || scenario === "ignored_dir_contains_nothing_conflicting"
             || scenario === "deep_dir_contains_nothing_conflicting") {
    // THE FLEET-WIDE FALSE REFUSAL. Upstream adds a tracked file INSIDE a
    // directory the user already has untracked (or ignored). git writes it
    // without complaint — measured, `ff-only` returns 0 and both files
    // coexist — so the guard must not block, must not reset, must keep the
    // user's file, and the new file must actually arrive.
    const deep = scenario === "deep_dir_contains_nothing_conflicting"
    const dir = deep ? "notes/sub" : "notes"
    fs.mkdirSync(path.join(up, dir), { recursive: true })
    fs.writeFileSync(path.join(up, dir, "new.md"), "upstream\n")
    git(up, ["add", `${dir}/new.md`]); git(up, ["commit", "-qm", "add in dir"])
    fs.mkdirSync(path.join(clone, dir), { recursive: true })
    fs.writeFileSync(path.join(clone, dir, "mine.md"), "MINE\n")
    protectedPath = `${dir}/mine.md`
    arrivalPath = `${dir}/new.md`
    if (scenario === "ignored_dir_contains_nothing_conflicting") {
      fs.appendFileSync(path.join(clone, ".git", "info", "exclude"),
                        "\nnotes/\n")
    }
  } else if (scenario === "emptydir_does_not_block_file") {
    // An EMPTY directory where upstream now tracks a FILE: git removes it and
    // writes the file (measured). No user data can be lost, so blocking here
    // is pure refusal with nothing protected.
    fs.writeFileSync(path.join(up, "scratch"), "upstream\n")
    git(up, ["add", "scratch"]); git(up, ["commit", "-qm", "add file"])
    fs.mkdirSync(path.join(clone, "scratch"))
    protectedPath = "f.txt"
    arrivalPath = "scratch"
  } else if (scenario === "divergence_only") {
    fs.writeFileSync(path.join(up, "f.txt"), "v2\n")
    git(up, ["commit", "-qam", "v2"])
    git(clone, ["commit", "-q", "--allow-empty", "-m", "local"])
  } else if (scenario === "clean_ff") {
    fs.writeFileSync(path.join(up, "f.txt"), "v2\n")
    git(up, ["commit", "-qam", "v2"])
  }
  git(clone, ["fetch", "-q", "origin"])

  const bin = path.join(work, "bin")
  fs.mkdirSync(bin)
  fs.writeFileSync(path.join(bin, "git"),
    '#!/usr/bin/env bash\nif [ "${1:-}" = "reset" ]; then echo "RESET_CALLED $*"; exit 0; fi\nexec ' +
    execFileSync("which", ["git"]).toString().trim() + ' "$@"\n')
  fs.chmodSync(path.join(bin, "git"), 0o755)

  const script = updateDispatches()
    .map((m, i) => `(\n${m}\n)\nrc=$?; [ $rc -eq 0 ] || exit $rc\n`).join("")
  const r = spawnSync("bash", ["-c", script], {
    cwd: clone, encoding: "utf8",
    env: Object.assign({}, G.env, { PATH: bin + ":" + process.env.PATH }),
  })
  const outText = (r.stdout || "") + (r.stderr || "")
  const protectedValue = fs.existsSync(path.join(clone, protectedPath))
    && fs.statSync(path.join(clone, protectedPath)).isFile()
    ? fs.readFileSync(path.join(clone, protectedPath), "utf8").trim() : null
  const arrived = arrivalPath
    ? fs.existsSync(path.join(clone, arrivalPath))
      && fs.statSync(path.join(clone, arrivalPath)).isFile()
    : null
  fs.rmSync(work, { recursive: true, force: true })
  return { reset: /RESET_CALLED/.test(outText), code: r.status,
           protectedValue, protectedPath, arrived, arrivalPath, out: outText }
}

let probesRan = false
try {
  execFileSync("git", ["--version"], { stdio: "ignore" })
  probesRan = true
} catch (e) {
  ok("SKIPPED the updater behaviour probes — git is not available here")
}

if (probesRan) {
  const mixed = probe("mixed")
  if (mixed.reset || mixed.protectedValue !== "MINE") {
    fail(`update.js RESET on a diverged clone whose untracked file is tracked upstream — the file is destroyed. Divergence and obstruction are independent; the destructive step must clear BOTH. (reset=${mixed.reset}, value=${JSON.stringify(mixed.protectedValue)})`)
  } else {
    ok("mixed divergence + untracked obstruction: no reset, file intact")
  }

  const obs = probe("obstruction_only")
  if (obs.reset || obs.protectedValue !== "MINE") {
    fail(`update.js RESET over an untracked obstruction with no divergence at all (reset=${obs.reset}, value=${JSON.stringify(obs.protectedValue)})`)
  } else {
    ok("untracked obstruction alone: no reset, file intact")
  }

  for (const [scenario, label] of [
    ["file_blocks_directory", "untracked FILE obstructing an upstream DIRECTORY"],
    ["directory_blocks_file", "untracked DIRECTORY obstructing an upstream FILE"],
    ["ignored_exact", "ignored exact-path obstruction"],
    ["ignored_file_blocks_directory", "ignored FILE obstructing an upstream DIRECTORY"],
    ["ignored_directory_blocks_file", "ignored child beneath an upstream FILE"],
  ]) {
    const result = probe(scenario)
    if (result.reset || result.protectedValue !== "MINE") {
      fail(`${label}: reset=${result.reset}, ${result.protectedPath}=${JSON.stringify(result.protectedValue)}`)
    } else {
      ok(`${label}: no reset, file intact`)
    }
  }

  // ---- the guard must not REFUSE what git accepts ---------------------------
  // The mirror image of the destruction bug, and the more expensive one: the
  // guard flagged any ancestor that merely EXISTED, so an untracked directory
  // — logs/, cache/, __pycache__/, mlx_models/, a folder the user dropped in
  // the app dir — read as an obstruction the moment a release added a tracked
  // file inside it. Nobody could update, including to the fix. Each row here
  // was measured against real `git merge --ff-only` before it was asserted.
  for (const [scenario, label, keep] of [
    ["dir_contains_nothing_conflicting",
     "untracked DIRECTORY holding nothing that conflicts", "MINE"],
    ["ignored_dir_contains_nothing_conflicting",
     "IGNORED directory holding nothing that conflicts", "MINE"],
    ["deep_dir_contains_nothing_conflicting",
     "nested untracked directories holding nothing that conflicts", "MINE"],
    ["emptydir_does_not_block_file",
     "EMPTY directory where upstream now tracks a file", "v1"],
  ]) {
    const r = probe(scenario)
    if (r.code !== 0 || r.reset || r.arrived !== true || r.protectedValue !== keep) {
      fail(`${label}: git accepts this worktree but update.js did not ` +
        `(exit=${r.code}, reset=${r.reset}, ${r.arrivalPath} arrived=${r.arrived}` +
        `, ${r.protectedPath}=${JSON.stringify(r.protectedValue)}). A release ` +
        `adding a tracked file under a folder users commonly have untracked ` +
        `or ignored is then a fleet-wide update REFUSAL — including the ` +
        `release that would fix it.\n      ${r.out.trim().split("\n").slice(0, 3).join(" | ")}`)
    } else {
      ok(`${label}: update proceeds, ${r.arrivalPath} lands, nothing lost`)
    }
  }

  const div = probe("divergence_only")
  if (!div.reset) {
    fail("update.js refused to reset a genuinely diverged, clean clone — the updater must still be able to converge.")
  } else {
    ok("genuine divergence with a clean tree: reset is reached")
  }

  const ff = probe("clean_ff")
  if (ff.reset || ff.code !== 0) {
    fail(`a plain fast-forward must not reset and must succeed (reset=${ff.reset}, exit=${ff.code})`)
  } else {
    ok("plain fast-forward: converges without reset")
  }
}

console.log("")
console.log(failures.length ? `RESULT: FAIL (${failures.length})` : "RESULT: PASS")
process.exit(failures.length ? 1 : 0)
