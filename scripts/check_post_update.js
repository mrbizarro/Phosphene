#!/usr/bin/env node
/*
 * Gate: scripts/post_update.sh keeps the two properties that were paid for in
 * blood, and that collapsing 18 Pinokio steps into one shell script can silently
 * take away.
 *
 * ---------------------------------------------------------------------------
 * 1. ORDER — the codec patch runs before anything optional
 * ---------------------------------------------------------------------------
 * On v3.8.1 the patch sat eleven steps down, behind litellm / smolagents /
 * mflux / three weight fetches. The owner's Pinokio run ended at step 7 of 18
 * — exit code 0, no error, the update presented as finished — and the patch
 * never executed. Every render on that install encoded 4:2:0. v3.8.2 moved the
 * patch to run immediately after the reinstall that replaces site-packages and
 * enforced the ordering with a gate rather than a convention. This is that
 * gate, re-pointed at the file the work now lives in.
 *
 * It must ALSO stay after the package reinstall: that step overwrites
 * site-packages, so a patch applied before it is thrown away.
 *
 * And at the other end: the LTX venv self-heal (step 0) must stay BEFORE the
 * mlx pin, because everything from the pin onward installs into that venv.
 * A dangling uv interpreter — which any other Pinokio pack can cause — turned
 * an Update into a run of install failures whose FATAL banner named a pin
 * instead of the venv, leaving reinstall as the only apparent cure.
 *
 * ---------------------------------------------------------------------------
 * 2. FATALITY — a failed load-bearing step fails the Update
 * ---------------------------------------------------------------------------
 * Under Pinokio each of these was its own step, so a non-zero exit aborted the
 * run for free. Inside one script it does not, and the first draft of
 * post_update.sh lost it: patch_ltx_codec.py printed its CODEC PATCH FAILURE
 * banner and the update carried on and reported success — reinstating the exact
 * silent-4:2:0 outcome the banner exists to prevent. Found by the v4.0 journey
 * sim. The four load-bearing steps must go through `require`.
 *
 * Run: node scripts/check_post_update.js      exit 0 = PASS
 */
const fs = require("fs")
const path = require("path")

const file = path.resolve(__dirname, "post_update.sh")
const lines = fs.readFileSync(file, "utf8").split("\n")

// Executable lines only — the rationale above each step names these commands.
const code = lines.map((l, i) => ({ n: i + 1, t: l }))
  .filter((l) => l.t.trim() && !l.t.trim().startsWith("#"))

const failures = []
const find = (re) => code.find((l) => re.test(l.t))
const at = (re, label) => {
  const hit = find(re)
  if (!hit) { failures.push(`no line matching ${label}`); return Infinity }
  return hit.n
}

const venv = at(/ltx_venv\.sh/, "the LTX venv self-heal")
const mlxPin = at(/mlx==0\.31\.1/, "the mlx pin")
const reinstall = at(/uv pip install .*--reinstall .*\.\/packages\/ltx-core-mlx/, "the vendored package reinstall")
const patch = at(/patch_ltx_codec\.py/, "the codec patch")
const optional = [
  ["mflux", /mflux==/],
  ["litellm", /litellm>=/],
  ["smolagents", /smolagents>=/],
  ["the mosaic upscaler fetch", /spatial_upscaler_x2_v1_1/],
  ["the LTX-2.5 weight fetch", /fetch_pack_release\.py/],
  ["the H3 compact-engine build", /h3_build_q8\.sh/],
  ["the model trim", /^rm -f /],
]

console.log(`venv self-heal    : line ${venv}`)
console.log(`mlx pin           : line ${mlxPin}`)
console.log(`package reinstall : line ${reinstall}`)
console.log(`codec patch       : line ${patch}`)

// --- 0. THE VENV EXISTS BEFORE ANYTHING INSTALLS INTO IT --------------------
// Every step from the mlx pin onward is `uv pip install --python $VENV_PY`. If
// that interpreter is a dangling uv symlink chain — which any other Pinokio
// pack can cause, see the note in post_update.sh — the Update becomes a run of
// install failures whose FATAL banner names a pin instead of the venv, and the
// user's only remaining move is a full reinstall of a machine whose weights are
// all fine. The self-heal is idempotent and costs ~50 ms on a healthy install,
// so the only thing that can go wrong here is someone moving it later in the
// file during a future edit. That is what this asserts.
if (venv > mlxPin) {
  failures.push(`the LTX venv self-heal (line ${venv}) runs AFTER the mlx pin (line ${mlxPin}). Everything from the pin onward installs into that venv, so it has to be repaired first — see step 0 in post_update.sh.`)
} else {
  console.log(`  ok    venv self-heal(${venv}) < mlx pin(${mlxPin})`)
}

if (patch < reinstall) {
  failures.push(`the codec patch (line ${patch}) runs BEFORE the package reinstall (line ${reinstall}) — the reinstall overwrites site-packages, so the patch would be thrown away.`)
} else {
  console.log("  ok    patch runs after the reinstall that replaces site-packages")
}

for (const [label, re] of optional) {
  const n = at(re, label)
  if (n < patch) {
    failures.push(`${label} (line ${n}) runs BEFORE the codec patch (line ${patch}). Everything optional must come after it.`)
  } else {
    console.log(`  ok    patch(${patch}) < ${label}(${n})`)
  }
}

// --- fatality ---------------------------------------------------------------
if (!/^require\(\)/m.test(lines.join("\n"))) {
  failures.push("post_update.sh defines no `require()` helper — load-bearing steps have no way to fail the Update.")
}
const mustRequire = [
  // Not optional despite being a "repair": a venv that cannot be rebuilt makes
  // every later step fail, so continuing only makes the report worse.
  ["the LTX venv self-heal", /ltx_venv\.sh/],
  ["the vendored pin move", /ltx_checkout\.sh/],
  ["the mlx pin", /mlx==0\.31\.1/],
  ["the vendored package reinstall", /--reinstall .*\.\/packages\/ltx-core-mlx/],
  ["the codec patch", /patch_ltx_codec\.py/],
  // The fleet's second-commonest error is `No module named 'ltx_pipelines_mlx'`
  // — 464 events, an app that boots and cannot render a frame. install.js has
  // guarded this since v2.0.2; Update did not until v4.9. A pip step exiting 0
  // is not the same claim as "the module imports", and the user discovers the
  // difference at Generate time.
  ["the render engine import gate", /import ltx_core_mlx, ltx_pipelines_mlx, mlx/],
]
for (const [label, re] of mustRequire) {
  const hit = code.find((l) => re.test(l.t) && !/^\s*require\(\)/.test(l.t))
  if (!hit) { failures.push(`no executable line for ${label}`); continue }
  if (!/^require\s/.test(hit.t.trim())) {
    failures.push(`${label} (line ${hit.n}) is not wrapped in \`require\` — it would print its error and let the Update report success.`)
  } else {
    console.log(`  ok    ${label} is fatal on failure`)
  }
}

// --- 2c. THE IMPORT GATE MUST BE LAST ---------------------------------------
// It is the only step that proves the app can render, so anything that can
// break the import must already have run. A gate in the middle of the script
// would pass and then be invalidated by step 7's mflux resolve, which is
// precisely the move that walks mlx to a version the engine cannot use.
{
  const gate = code.find((l) => /import ltx_core_mlx, ltx_pipelines_mlx, mlx/.test(l.t))
  if (gate) {
    const after = code.filter((l) => l.n > gate.n && /^\s*(require |uv pip install|\( cd )/.test(l.t))
    if (after.length) {
      failures.push(`the import gate (line ${gate.n}) is followed by ${after.length} more install step(s), starting at line ${after[0].n} — it must be the LAST thing the script does or it proves nothing.`)
    } else {
      console.log("  ok    the import gate is the last install step")
    }
  }
}

// --- 2d. THE VENDORED PACKAGES' DEPENDENCIES ARE RESOLVED, NOT ASSUMED -------
// The reinstall is --no-deps, which is right for moving the runtime and wrong
// as the ONLY install: on a venv step 0 just rebuilt, nothing installed what
// the packages declare, `mlx-arsenal` above all (core and pipelines import
// `mlx_arsenal.diffusion`; nothing else depends on it). Every Update then
// repeated the same dependency-free install and the import-gate "repair" did
// too — an install that Update could never fix (Codex INST-03, 2026-09-24).
// Reproduced 2026-09-24 in a fresh uv venv: the old sequence ends in
// `No module named 'mlx_arsenal'`; with the dependency pass it imports.
{
  const vendoredLines = code.filter((l) => /\.\/packages\/ltx-core-mlx/.test(l.t))
  const depPass = vendoredLines.find((l) => /uv pip install/.test(l.t) && !/--no-deps/.test(l.t))
  if (!depPass) {
    failures.push("no dependency-resolving install of the vendored packages — the --no-deps reinstall alone leaves a rebuilt venv without mlx-arsenal (INST-03).")
  } else {
    if (depPass.n > reinstall) failures.push(`the vendored dependency pass (line ${depPass.n}) runs AFTER the --no-deps reinstall (line ${reinstall}) — it would re-link the packages editable and leave them that way.`)
    if (!/^require\s/.test(depPass.t.trim())) failures.push(`the vendored dependency pass (line ${depPass.n}) is not wrapped in \`require\`.`)
    if (!/mlx==0\.31\.1/.test(depPass.t) || !/transformers>=5\.0\.0,<5\.13\.0/.test(depPass.t)) failures.push(`the vendored dependency pass (line ${depPass.n}) does not carry the mlx pin and the transformers cap on the same resolve — the solver could move either.`)
    else console.log(`  ok    vendored dependencies resolved (line ${depPass.n}) before the --no-deps reinstall, pins on the same resolve`)
  }
  const gate = code.find((l) => /import ltx_core_mlx, ltx_pipelines_mlx, mlx/.test(l.t))
  if (gate && !/mlx_arsenal/.test(gate.t)) failures.push("the import gate does not import mlx_arsenal — the package roots import without it, so the gate passes an engine that cannot render.")
  const repair = code.filter((l) => gate && l.n < gate.n && l.n > reinstall && /uv pip install --python env\/bin\/python/.test(l.t))
  if (gate && !repair.some((l) => !/--reinstall|--no-deps/.test(l.t))) failures.push("the import-gate repair only reinstalls --no-deps — it cannot install a missing dependency, so it fails the same way every time.")
}

// --- 3. THE mlx PIN AND THE mflux PIN ARE ONE DECISION -----------------------
// Step 2 pins mlx. Step 7 installs mflux WITH deps (the resolving call, there
// so a fresh install gets mflux's transitive set). mflux declares its OWN mlx
// range, so that later resolve can MOVE the version step 2 just pinned, in the
// one direction nobody would look for it.
//
// Measured 2026-08-28, in a copy of the real venv:
//   mlx 0.32.2 installed, then `uv pip install 'mflux==0.18.0'` →
//     - mlx==0.32.2  + mlx==0.31.2
//     - mlx-metal==0.32.2  + mlx-metal==0.31.2
// mflux 0.18.0 declares `mlx>=0.27.0,<0.32.0` on darwin, so the resolver walks
// mlx DOWN to the newest thing that satisfies it — which is 0.31.2, the exact
// release the audio ship-blocker exists to keep off users' machines. It is
// invisible today only because 0.31.1 sits inside mflux's range; the moment
// the mlx pin moves to 0.32.x on its own, every fresh install and every Update
// ends on 0.31.2 with a -42 dB vocoder, and nothing prints a warning.
//
// So the two pins move together or not at all. mflux 0.19.1 is the first
// release that declares `mlx>=0.32.0,<0.33.0`, and the whole pair was BUILT AND
// MEASURED on 2026-08-28 before being put back: in a copy of the real venv the
// post_update sequence in order (step 2 --no-deps, step 2b with the transformers
// cap, step 7 with deps, step 7 again --no-deps) lands on mlx 0.32.1 /
// mlx-metal 0.32.1 / mlx-lm 0.31.1 / mflux 0.19.1 / transformers 5.7.0 with
// nothing walked back, and both FBCache anchors applied cleanly to a real
// 0.19.1 install (idempotent on re-run). The mechanics are not the blocker.
//
// The blocker is memory, re-measured against the cache policy that shipped
// first: 0.32.1 sits at a higher share of physical RAM than 0.31.1 on three of
// four tiers, and the excess is ACTIVE memory (+12.0 GB Q4, +29.2 GB Q8) which
// no cache cap can reach. Full table in install.js above the pin and in
// CLAUDE.md sec 4. mlx-lm has no 0.32.x and declares `mlx>=0.30.4` with no upper
// bound, so it is not part of the pair.
//
// This gate is the place that knowledge lives. Change the pair here, and in
// every file below, in ONE commit — and re-read this comment first.
const PIN_PAIR = { mlx: "0.31.1", mflux: "0.18.0" }
const pinSites = [
  ["scripts/post_update.sh", "mlx"], ["install.js", "mlx"],
  ["scripts/post_update.sh", "mflux"], ["install.js", "mflux"],
  ["scripts/pinokio/mflux_pack.sh", "mflux"], ["install_qwen.js", "mflux"],
]
const repoRoot = path.resolve(__dirname, "..")
const seenPins = new Map()
for (const [rel, pkg] of pinSites) {
  const p = path.join(repoRoot, rel)
  let text
  try { text = fs.readFileSync(p, "utf8") } catch (e) {
    failures.push(`${rel} is missing — the ${pkg} pin cannot be checked.`); continue
  }
  // install.js delegates the mflux install to mflux_pack.sh; that is fine, the
  // script itself is in the list. Only assert on files that actually name it.
  //
  // Comment lines are stripped first, deliberately: the rationale above these
  // pins QUOTES the versions it is arguing about ("mlx==0.31.1 (NOT 0.31.2)"),
  // and a gate that reads prose would fail on the explanation instead of the
  // command. Only what actually runs is asserted.
  const runnable = text.split("\n").filter((l) => !/^\s*(\/\/|#)/.test(l)).join("\n")
  const found = [...runnable.matchAll(new RegExp(`(?:^|[^\\w.-])${pkg}==([0-9][0-9.]*)`, "gm"))].map((m) => m[1])
  if (!found.length) continue
  const wrong = found.filter((v) => v !== PIN_PAIR[pkg])
  const key = `${rel}:${pkg}`
  seenPins.set(key, found)
  if (wrong.length) {
    failures.push(`${rel} pins ${pkg}==${[...new Set(wrong)].join("/")} but this gate's pair says ${pkg}==${PIN_PAIR[pkg]}. mlx and mflux constrain each other (see the comment above): move both, in one commit, and update PIN_PAIR.`)
  } else {
    console.log(`  ok    ${rel} pins ${pkg}==${PIN_PAIR[pkg]}`)
  }
}
if (!seenPins.size) failures.push("no mlx/mflux pin found in any install path — the coupling gate is not looking at anything.")

// --- and the optional ones must NOT be fatal --------------------------------
// An Update that a network hiccup can brick is worse than one that warns.
for (const [label, re] of [["the IC-LoRA fetches", /IC-LoRA-Colorizer/], ["the LTX-2.5 weight fetch", /fetch_pack_release\.py/]]) {
  const hit = code.find((l) => re.test(l.t))
  const guarded = hit && (/\|\|/.test(hit.t) || /\|\|/.test((code[code.indexOf(hit) + 1] || {}).t || ""))
  if (!guarded) failures.push(`${label} (line ${hit && hit.n}) is not guarded with \`|| echo WARN\` — an Update must not be brickable by a network hiccup.`)
  else console.log(`  ok    ${label} is best-effort`)
}

console.log("")
console.log(failures.length ? `RESULT: FAIL (${failures.length})` : "RESULT: PASS")
for (const f of failures) console.log("FAIL  " + f)
process.exit(failures.length ? 1 : 0)
