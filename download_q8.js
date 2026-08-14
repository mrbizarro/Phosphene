const fs = require("fs")
const path = require("path")

// Resolve the words from the SAME registry/capability rows as pinokio.js and
// the panel Models card. The shell already chose q8 vs q8_25 per ENVIRONMENT,
// but these notifications stayed hardcoded to 2.5, so an LTX23-pinned user was
// told that a 37 GB 2.3 download was 30 GB and still needed a 2.5-only add-on.
function resolveQ8Offer(installRoot = __dirname, versionOverride = "") {
  let required = { repos: [], capabilities: {} }
  try {
    required = JSON.parse(fs.readFileSync(
      path.join(installRoot, "required_files.json"), "utf8"))
  } catch (e) {}
  const caps = required.capabilities || {}
  const chars = caps.characters || {}
  const high = caps.high_tier || {}
  const known = chars.repos_by_version || {}
  let version = versionOverride || chars.default_version || "ltx25"
  if (!versionOverride) {
    try {
      const env = fs.readFileSync(path.join(installRoot, "ENVIRONMENT"), "utf8")
        .split("\n").filter(l => !/^\s*#/.test(l)).join("\n")
      const m = env.match(/^\s*LTX_MODEL_VERSION\s*=\s*(\S+)\s*$/m)
      if (m && known[m[1]]) version = m[1]
    } catch (e) {}
  }
  if (!known[version]) version = chars.default_version || Object.keys(known)[0] || "ltx25"
  const repos = required.repos || []
  const byKey = Object.fromEntries(repos.map(r => [r.key, r]))
  const q8Key = (known[version] || [])[0]
  const q8 = byKey[q8Key] || { key: q8Key || "q8", name: "Q8 weights", size_gb: null }
  const highKeys = ((high.repos_by_version || {})[version] || [])
  const addon = highKeys.map(k => byKey[k]).find(r => r && r.key !== q8.key) || null
  // Preserve the registry's measured 30.02 while naturally printing 37
  // without a decimal suffix.
  const sizeText = (typeof q8.size_gb === "number")
    ? String(q8.size_gb).replace(/\.0+$/, "") + " GB" : "?"
  const addonText = addon
    ? ` The High tier needs one more download — ${addon.name} (~${addon.size_gb} GB) — in Settings → Models.`
    : " The High tier is included in this pack."
  return {
    version,
    key: q8.key,
    name: q8.name,
    size: sizeText,
    startHtml: `<b>Downloading ${q8.name} (~${sizeText})…</b><br>8-bit weights instead of 4-bit. This is what trained characters and voices need — on the base pack most of a trained face is lost before the first frame. Resumable if interrupted.`,
    readyHtml: `<b>${q8.name} ready.</b><br>Characters and voices now render at full strength.${addonText}`,
  }
}

const q8Offer = resolveQ8Offer()

module.exports = {
  // Exported only so scripts/check_ltx_pin.js can exercise BOTH generations;
  // Pinokio consumes `run` exactly as before.
  _resolveQ8Offer: resolveQ8Offer,
  run: [
    {
      method: "notify",
      params: { html: q8Offer.startHtml }
    },
    {
      method: "shell.run",
      params: { message: "bash scripts/pinokio/q8_weights.sh" }
    },
    {
      method: "notify",
      params: { html: q8Offer.readyHtml }
    }
  ]
}
