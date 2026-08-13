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

const reinstall = at(/uv pip install .*--reinstall .*\.\/packages\/ltx-core-mlx/, "the vendored package reinstall")
const patch = at(/patch_ltx_codec\.py/, "the codec patch")
const optional = [
  ["mflux", /mflux==/],
  ["litellm", /litellm>=/],
  ["smolagents", /smolagents>=/],
  ["the mosaic upscaler fetch", /spatial_upscaler_x2_v1_1/],
  ["the LTX-2.5 weight fetch", /fetch_pack_release\.py/],
  ["the model trim", /^rm -f /],
]

console.log(`package reinstall : line ${reinstall}`)
console.log(`codec patch       : line ${patch}`)

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
  ["the vendored pin move", /ltx_checkout\.sh/],
  ["the mlx pin", /mlx==0\.31\.1/],
  ["the vendored package reinstall", /--reinstall .*\.\/packages\/ltx-core-mlx/],
  ["the codec patch", /patch_ltx_codec\.py/],
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
