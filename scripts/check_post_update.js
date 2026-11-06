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

// --- 2e. THE INSTALL'S ENGINE STEP RESOLVES THE SAME WAY (4.19.0) ----------
// scripts/pinokio/ltx_engine_env.sh (install.js and the panel's Repair engine)
// installed the vendored packages WITHOUT the pins on that resolve. The newest
// mlx-arsenal requires mlx>=0.32.1, so the solver moved mlx to 0.32.3 and the
// mflux pack then walked it to 0.31.2: every fresh install since 2026-08-24
// ran mlx 0.31.2 while every Update (2d above) put 0.31.1 back.
{
  const eng = fs.readFileSync(path.resolve(__dirname, "pinokio", "ltx_engine_env.sh"), "utf8")
  const cmds = eng.replace(/\\\n/g, " ").split("\n").filter((l) => !/^\s*#/.test(l))
  const dep = cmds.find((l) => /uv pip install/.test(l) && /\.\/packages\/ltx-core-mlx/.test(l) && !/--no-deps/.test(l))
  if (!dep) failures.push("scripts/pinokio/ltx_engine_env.sh has no dependency-resolving install of the vendored packages.")
  else if (!/mlx==0\.31\.1/.test(dep) || !/mlx-metal==0\.31\.1/.test(dep) || !/transformers>=5\.0\.0,<5\.13\.0/.test(dep)) failures.push("scripts/pinokio/ltx_engine_env.sh resolves the vendored packages without the mlx pin and the transformers cap on the same command - mlx-arsenal moves mlx (fresh installs land on 0.31.2).")
  else console.log("  ok    the install's engine step resolves the vendored packages with the pins on the same command")
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
  // 4.17.4: install.js's engine step now lives here (one implementation the
  // panel's "Repair engine" re-runs too), so the mlx pin is asserted here.
  ["scripts/pinokio/ltx_engine_env.sh", "mlx"],
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

// Updates must never opt a model-free install into video or adapter weights.
for (const re of [/IC-LoRA-Colorizer/, /fetch_pack_release\.py/, /DeepBeepMeep\/LTX-2/, /a2v_stems_deps\.sh/]) {
  if (code.some(l => re.test(l.t))) failures.push("Update must leave model downloads to explicit feature requests.")
}

// --- 4. EVERY INSTALL INTO THE ENGINE VENV CARRIES THE KNOWN-GOOD SET -------
// 4.19.4 fleet fix. tokenizers 0.23.3 shipped 2026-10-09; Update's
// `uv pip install --upgrade 'litellm>=1.83.14'` (uv's --upgrade is EAGER: it
// re-resolves everything under litellm, which accepts tokenizers <1.0) moved it
// there, and transformers <5.13 refuses to import with it - every render dies.
// A later resolve that included transformers happened to move it back, so the
// install was broken only while the Update was in between; one that stopped
// there (a dropped connection, Pinokio ending the run on an "error:" line)
// stayed broken. A constraints file on EVERY install into the venv closes the
// class: no step, however eager, can leave the set.
{
  const EC_REL = "scripts/pinokio/engine_constraints.txt"
  let ec = ""
  try { ec = fs.readFileSync(path.join(repoRoot, EC_REL), "utf8") } catch (e) {
    failures.push(`${EC_REL} is missing - nothing holds the engine venv to its known-good set.`)
  }
  const ecLines = ec.split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("#"))
  const need = [
    ["the transformers cap", (l) => l === "transformers>=5.0.0,<5.13.0"],
    // transformers <5.13 itself requires tokenizers >=0.22.0,<=0.23.0
    ["a tokenizers range inside transformers <5.13's own", (l) => /^tokenizers>=0\.22\.0,<(=)?0\.23\.0$/.test(l)],
    ["the huggingface-hub 1.x range", (l) => l === "huggingface-hub>=1.5.0,<2.0"],
    [`mlx==${PIN_PAIR.mlx}`, (l) => l === `mlx==${PIN_PAIR.mlx}`],
    [`mlx-metal==${PIN_PAIR.mlx}`, (l) => l === `mlx-metal==${PIN_PAIR.mlx}`],
    ["mlx-lm==0.31.1", (l) => l === "mlx-lm==0.31.1"],
  ]
  for (const [label, ok] of need) {
    if (ec && !ecLines.some(ok)) failures.push(`${EC_REL} does not carry ${label}.`)
  }
  if (ec) console.log(`  ok    ${EC_REL}: ${ecLines.length} constraints`)

  // Every install line, in every file that installs into the engine venv.
  const ecRef = /-c\s+("\$EC"|\$EC\b|scripts\/pinokio\/engine_constraints\.txt)|\$\{EC_ARGS\[@\]/
  const sites = [
    ["scripts/post_update.sh", /\buv pip install\b/],
    ["scripts/pinokio/ltx_engine_env.sh", /\buv pip install\b/],
    ["scripts/pinokio/mflux_pack.sh", /\buv pip install\b/],
    ["scripts/pinokio/a2v_stems_deps.sh", /\bpip install\b/],
    ["install_qwen.js", /\bpip install\b/],
    ["install_sharp.js", /\bpip install\b/],
  ]
  for (const [rel, re] of sites) {
    let text = ""
    try { text = fs.readFileSync(path.join(repoRoot, rel), "utf8") } catch (e) {
      failures.push(`${rel} is missing.`); continue
    }
    const cmds = text.replace(/\\\n/g, " ").split("\n")
      .filter((l) => !/^\s*(#|\/\/|\*)/.test(l) && re.test(l))
    // Count per INSTALL, not per line: `\`-continued chains join several
    // installs into one line, and one -c must not vouch for its neighbours.
    const installRe = /\bpip install\b/g
    const refRe = new RegExp(ecRef.source, "g")
    const bare = cmds.filter((l) => (l.match(installRe) || []).length > (l.match(refRe) || []).length)
    if (!cmds.length) failures.push(`${rel}: no install line found - is this gate still looking at the right file?`)
    else if (bare.length) failures.push(`${rel}: ${bare.length} install line(s) into the engine venv without -c ${EC_REL}: ${bare[0].trim().slice(0, 140)}`)
    else console.log(`  ok    ${rel}: all ${cmds.reduce((n, l) => n + (l.match(/\bpip install\b/g) || []).length, 0)} install(s) carry the engine constraints`)
    const eager = cmds.filter((l) => /--upgrade\b|\s-U\s/.test(l) && /litellm|smolagents|huggingface-hub|transformers|mflux/.test(l))
    if (eager.length) failures.push(`${rel}: an EAGER --upgrade of a package with a deep dependency tree (${eager[0].trim().slice(0, 120)}) - that is the step that moved tokenizers to 0.23.3.`)
  }
}

// ---------------------------------------------------------------------------
// 4.19.5 - THE HAILUO H3 VENV'S KNOWN-GOOD SET
// ---------------------------------------------------------------------------
// Through 4.19.4 the H3 venv (minimax-h3-mlx/.venv) was resolved from the
// runner's requirements.txt alone - floors, no lock - in two places
// (install_h3.js and h3_checkout.sh), so every new H3 install ran whatever
// PyPI held that day. Now there is ONE install into that venv,
// scripts/pinokio/h3_deps.sh, under -c scripts/pinokio/h3_constraints.txt.
// This holds the four properties that make that true:
//   1. the constraints file is a real lock (every line exact) and pins the
//      packages the panel's preflight judges (H3_ENV_JUDGED), with mlx >=0.32;
//   2. h3_deps.sh's install carries -c and no eager --upgrade;
//   3. install_h3.js and post_update.sh run h3_deps.sh, and no other script
//      installs into the H3 venv;
//   4. post_update.sh defangs its "error:" lines (an optional engine must not
//      end the Update before the LTX import gate).
{
  const HC_REL = "scripts/pinokio/h3_constraints.txt"
  let hc = ""
  try { hc = fs.readFileSync(path.join(repoRoot, HC_REL), "utf8") } catch (e) {
    failures.push(`${HC_REL} is missing - nothing holds the Hailuo H3 venv to a known-good set.`)
  }
  const hcLines = hc.split("\n").map((l) => l.split("#")[0].trim()).filter(Boolean)
  const loose = hcLines.filter((l) => !/^[A-Za-z0-9][A-Za-z0-9._-]*==[^\s,;]+$/.test(l))
  if (hc && loose.length) failures.push(`${HC_REL}: ${loose.length} line(s) are not an exact pin (${loose[0]}) - the H3 set is a lock, every package ==.`)
  const pinned = new Map(hcLines.map((l) => l.split("==")).map(([n, v]) => [n.toLowerCase().replace(/[-_.]+/g, "-"), v]))
  const JUDGED = ["mlx", "mlx-metal", "mlx-vlm", "transformers", "tokenizers", "huggingface-hub", "safetensors", "numpy", "pillow"]
  for (const n of JUDGED) {
    if (hc && !pinned.has(n)) failures.push(`${HC_REL} does not pin ${n} - the panel's H3 preflight judges it against this file.`)
  }
  const mlxH3 = pinned.get("mlx") || ""
  const [maj, min] = mlxH3.split(".").map(Number)
  if (mlxH3 && !(maj > 0 || min >= 32)) failures.push(`${HC_REL} pins mlx==${mlxH3}, but the H3 runner needs mlx>=0.32 ("seed(): incompatible function arguments" below it).`)
  if (mlxH3 && pinned.get("mlx-metal") !== mlxH3) failures.push(`${HC_REL}: mlx-metal (${pinned.get("mlx-metal")}) must equal mlx (${mlxH3}).`)
  if (hc && !loose.length) console.log(`  ok    ${HC_REL}: ${hcLines.length} exact pins, mlx ${mlxH3}`)

  const strip = (t, js) => t.replace(/\\\n/g, " ").split("\n").filter((l) => !(js ? /^\s*(\/\/|\*)/ : /^\s*#/).test(l))
  const read = (rel) => { try { return fs.readFileSync(path.join(repoRoot, rel), "utf8") } catch (e) { failures.push(`${rel} is missing.`); return "" } }

  const deps = strip(read("scripts/pinokio/h3_deps.sh"), false)
  const depInstalls = deps.filter((l) => /\bpip install\b/.test(l))
  if (depInstalls.length !== 1) failures.push(`scripts/pinokio/h3_deps.sh has ${depInstalls.length} install lines - it must have exactly one, under the H3 constraints.`)
  else if (!/-c\s+"\$HC"/.test(depInstalls[0]) || !/-r requirements\.txt/.test(depInstalls[0])) failures.push(`scripts/pinokio/h3_deps.sh installs without -c "$HC" (h3_constraints.txt): ${depInstalls[0].trim()}`)
  else console.log("  ok    scripts/pinokio/h3_deps.sh installs requirements.txt under -c h3_constraints.txt")
  if (!deps.some((l) => /h3_constraints\.txt/.test(l))) failures.push("scripts/pinokio/h3_deps.sh does not point HC at h3_constraints.txt.")
  if (depInstalls.some((l) => /--upgrade\b|\s-U\s/.test(l))) failures.push("scripts/pinokio/h3_deps.sh: an eager --upgrade - it re-resolves everything under it, constraints or not.")

  const installH3 = strip(read("install_h3.js"), true)
  if (!installH3.some((l) => /h3_deps\.sh/.test(l))) failures.push("install_h3.js does not run scripts/pinokio/h3_deps.sh - Install / Repair / Update Hailuo H3 runner would not get the known-good set.")
  const postH3 = code.filter((l) => /h3_deps\.sh/.test(l.t))
  if (!postH3.length) failures.push("post_update.sh does not run scripts/pinokio/h3_deps.sh - an Update would never repair a drifted H3 venv.")
  else {
    const next = (code[code.indexOf(postH3[0]) + 1] || {}).t || ""
    // Either the old inline pipe, or (4.20) the `defang` helper - whose own
    // body must carry the same sed.
    const viaHelper = /\bdefang\s+bash\b/.test(postH3[0].t) &&
      /^defang\(\)[\s\S]*?sed -u -e 's\/FATAL \[Ee\]rror:\/WARN:\/g' -e 's\/\[Ee\]\[Rr\]\[Rr\]\[Oo\]\[Rr\]:\//m.test(lines.join("\n"))
    if (!viaHelper && (!/2>&1/.test(postH3[0].t) || !/sed -u -e 's\/\[Ee\]\[Rr\]\[Rr\]\[Oo\]\[Rr\]:/.test(next))) failures.push(`post_update.sh line ${postH3[0].n}: h3_deps.sh's output is not piped through the "error:" defang - a failed optional H3 repair would end the Update before the LTX import gate.`)
    else console.log(`  ok    post_update.sh runs h3_deps.sh (line ${postH3[0].n}), "error:" defanged`)
    const gate = code.find((l) => /import ltx_core_mlx, ltx_pipelines_mlx, mlx/.test(l.t))
    if (gate && postH3[0].n > gate.n) failures.push("post_update.sh runs h3_deps.sh after the import gate - the gate must stay the last install step.")
  }
  // No other install into the H3 venv, anywhere it could happen.
  const h3Files = [["install_h3.js", true], ["scripts/pinokio/h3_checkout.sh", false], ["scripts/pinokio/h3_venv.sh", false],
                   ["scripts/pinokio/h3_build_q8.sh", false], ["scripts/pinokio/h3_roots.sh", false], ["update.js", true]]
  for (const [rel, js] of h3Files) {
    const bad = strip(read(rel), js).filter((l) => /\bpip install\b/.test(l))
    if (bad.length) failures.push(`${rel}: an install outside scripts/pinokio/h3_deps.sh (${bad[0].trim().slice(0, 120)}) - the H3 venv has one install, under its constraints.`)
  }
  const postBare = code.filter((l) => /\bpip install\b/.test(l.t) && /\.venv\/bin\/python|H3_CHECKOUT|requirements\.txt/.test(l.t))
  if (postBare.length) failures.push(`post_update.sh line ${postBare[0].n} installs into the H3 venv directly - use scripts/pinokio/h3_deps.sh.`)
  console.log("  ok    no install into the H3 venv outside h3_deps.sh" + (failures.length ? " (see failures)" : ""))
}

console.log("")
console.log(failures.length ? `RESULT: FAIL (${failures.length})` : "RESULT: PASS")
for (const f of failures) console.log("FAIL  " + f)
process.exit(failures.length ? 1 : 0)
