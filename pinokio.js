// Phosphene Pinokio menu.
//
// Uses required_files.json as the single source of truth for what counts
// as "installed". The same file is consumed by mlx_ltx_panel.py (run-time
// completeness checks) and is what install.js / update.js are wired to
// produce — so the menu state never drifts from what the panel will accept.
//
// Three install levels we care about for menu rendering:
//   env_ready          — venv + ltx-2-mlx clone exist (install.js step 1-3)
//   base_models_ready  — Q4 + Gemma fully on disk (install.js step 4-5)
//   q8_ready           — optional Q8 bundle fully on disk (download_q8.js)
//
// Anything less than `env_ready && base_models_ready` means the user can't
// Start — we surface a Resume Install affordance instead of Start. This is
// the SHIP-BLOCKER from the deep review: a network hiccup after venv
// creation used to leave the menu showing Start, with the panel about to
// crash because Q4/Gemma aren't on disk.

const fs = require("fs")
const os = require("os")
const path = require("path")

// Hailuo H3 (optional second video engine): the compact Q8 DiT lane's measured
// peak is 25.63 GiB (text_encode, a 7-second phase), so the floor is 36 GB —
// the same number H3_MIN_RAM_GB_Q8 in mlx_ltx_panel.py and
// scripts/pinokio/h3_preflight.sh use. Keep all three in sync.
//
// 46e9 UNTIL v4.8.0, AND THAT IS THE INCIDENT. 46 was never a measurement — it
// was a guard band picked to sit ~4 GB under a 48 GB Mac's marketing number
// back when the only number anyone had was "27.3 GiB peak". v4.8.0 lowered the
// PANEL's floor to 36 on the phase profile above and left this restatement and
// the preflight's behind, so a 36-48 GB Mac was told by the panel that H3 runs
// and by this menu that H3 does not exist. One number, three files.
const H3_MIN_BYTES = 36 * 1000 * 1000 * 1000

function h3Capable() {
  try {
    return os.totalmem() >= H3_MIN_BYTES
  } catch (e) {
    // Fail CLOSED here (unlike the installer's fail-open preflight): if we
    // can't read the hardware, don't advertise a 75 GB download that might
    // never run. The user can still install it from the panel's instructions.
    return false
  }
}

function musicCapable() {
  try { return os.totalmem() >= 24 * 1000 * 1000 * 1000 } catch (e) { return false }
}

function getInstallRoot(info) {
  // Pinokio's `info.path` API has shifted across versions:
  //   - older Pinokio: info.path is a STRING property (the install dir itself)
  //   - newer Pinokio: info.path is a FUNCTION that joins args with install dir
  // cocktailpeanut's working diff uses the function form; some user installs
  // (Mr Bizarro's reproduced this) error with TypeError on the function call, then
  // Pinokio's outer error handler stat's a bogus path constructed from the
  // error's .errno property — surfacing as "ENOENT ... stat '.../Errno'".
  // Try both shapes; fall back to __dirname which Pinokio sets to the install
  // dir for the running menu module on every version we've tested.
  if (info && typeof info.path === "function") {
    try { return path.dirname(info.path("required_files.json")) } catch (e) {}
  }
  if (info && typeof info.path === "string") return info.path
  return __dirname
}

function loadRequired(installRoot) {
  // Read required_files.json synchronously — Pinokio menus are sync today
  // (info.exists is sync) and this is small (< 1 KB) so blocking is fine.
  try {
    return JSON.parse(fs.readFileSync(path.join(installRoot, "required_files.json"), "utf8"))
  } catch (e) {
    // Treat as completely uninstalled if the manifest is gone.
    return { repos: [], env: { marker_paths: [] }, min_size_bytes: 1024 }
  }
}

// Persistent user overrides live in the ENVIRONMENT file the Pinokio launcher
// sources — that is exactly where docs/H3_ENGINE.md tells users to put them.
// The PANEL reads these names from its own process env; this menu runs in a
// different process entirely, so the only way for the two to agree is to parse
// the same file. Read once, used for the model generation AND for the two H3
// roots (see the h3Path note below for what disagreeing costs).
//
// Deliberately narrow: `KEY=value`, no continuations, no expansion. Anything
// this parser cannot read leaves the caller on its default, which is always the
// shipped behaviour.
const ENV_KEYS = ["LTX_MODEL_VERSION", "LTX_H3_ROOT", "LTX_H3_MODELS", "LTX_MUSIC_ROOT", "LTX_MUSIC_MODELS"]

function readEnvironment(installRoot) {
  const out = {}
  try {
    const envPath = path.join(installRoot, "ENVIRONMENT")
    if (!fs.existsSync(envPath)) return out
    const text = fs.readFileSync(envPath, "utf8")
      .split("\n").filter(l => !/^\s*#/.test(l)).join("\n")
    for (const key of ENV_KEYS) {
      const m = text.match(new RegExp("^\\s*" + key + "\\s*=\\s*(\\S+)\\s*$", "m"))
      if (m) out[key] = m[1].replace(/^["']|["']$/g, "")
    }
  } catch (e) { /* unreadable ENVIRONMENT: every caller keeps its default */ }
  return out
}

function repoComplete(installRoot, repo, minBytes) {
  // A repo is "complete" iff every listed file exists at >= minBytes under
  // its local_dir. Mirrors the Python-side _repo_missing in mlx_ltx_panel.py.
  for (const fname of (repo.files || [])) {
    try {
      const abs = path.join(installRoot, repo.local_dir, fname)
      const st = fs.statSync(abs)
      if (!st.isFile() || st.size < minBytes) return false
    } catch (e) {
      return false
    }
  }
  return true
}

module.exports = {
  version: "7.0",
  title: "Phosphene BETA",
  // The Pinokio store listing. It still said "via LTX 2.3 (MLX)" three weeks after 2.5
  // became the generation a fresh install renders with — the first sentence a prospective
  // user reads, naming the wrong engine, while a confused existing user hunting a "why
  // does it keep asking for LTX 2.3" answer finds it confirming their suspicion.
  description: "[BETA — unreleased builds, runs alongside the stable install on port 8199] Local generative video panel for Apple Silicon. Joint audio+video via LTX-2.5 (MLX), with Hailuo H3 as a second engine. T2V, I2V, FFLF, Extend, trained characters. Lossless h264. Hardware-tier feature gating. Free, open source.",
  icon: "icon.png",
  menu: async (kernel, info) => {
    // Resolve the install root. cocktailpeanut diagnosed that `info.path` is
    // a function on his Pinokio (call as info.path("file") → absolute path
    // inside install dir). On older Pinokio versions it's a string property.
    // getInstallRoot() handles both shapes and falls back to __dirname when
    // info is unusable. See the helper above for the full history.
    const installRoot = getInstallRoot(info)
    const required = loadRequired(installRoot)
    const minBytes = required.min_size_bytes || 1024
    // Every ENVIRONMENT override this menu honours, read once. See readEnvironment().
    const envFile = readEnvironment(installRoot)

    // --- env detection: either Pinokio's `env/` or manual `.venv/` ---
    const env_ready = (required.env.marker_paths || []).some(p => info.exists(p))

    // --- per-repo completeness from the unified manifest ---
    const repos = required.repos || []
    // WHAT HIDES START IS "CAN THIS RENDER", NOT "IS EVERY base ROW COMPLETE".
    // `kind: "base"` had come to mean two different things — fetched on a fresh
    // install, AND the panel cannot render without it — and Gemma 3 is the first
    // but not the second: it is LTX-2.3's encoder and the Enhance/planner model,
    // and the active 2.5 generation renders with Gemma 4 instead. Marked base, a
    // half-downloaded planner model made Pinokio declare the base renderer
    // incomplete and hide Start on an install that renders perfectly. The
    // preview decoder had the identical bug one commit earlier; this closes the
    // class by asking the capability instead of the label.
    const caps = required.capabilities || {}
    const capRender = caps.render || {}
    // THE ACTIVE GENERATION, where it is knowable. A user pinned back with
    // LTX_MODEL_VERSION=ltx23 renders on 2.3's packs, and evaluating the
    // manifest's default here told them their install was incomplete for a
    // generation they are not using. The panel resolves this from its own
    // process env; Pinokio's menu runs elsewhere, so it reads the same override
    // from the ENVIRONMENT file the launcher sources — which is exactly where
    // docs/H3_ENGINE.md tells users to put persistent overrides. Falls back to
    // the manifest default when nothing has been pinned, which is the norm.
    let activeVersion = capRender.default_version
    const pinnedVersion = envFile.LTX_MODEL_VERSION
    if (pinnedVersion && (capRender.repos_by_version || {})[pinnedVersion]) {
      activeVersion = pinnedVersion
    }
    const renderKeys = ((capRender.repos_by_version || {})[activeVersion] || [])
    const baseRepos = renderKeys.length
      ? renderKeys.map(k => repos.find(r => r.key === k)).filter(Boolean)
      : repos.filter(r => r.kind === "base")
    // THE PACK THE BUTTON ACTUALLY INSTALLS. This read `key === "q8"` —
    // LTX-2.3's pack — while the menu entry it gates dispatches
    // download_q8.js -> q8_weights.sh -> `--repo-key q8_25`, i.e. LTX-2.5's.
    // The gate and the action were about different packs, so after a
    // successful 30 GB install the menu kept offering the download forever.
    // Resolved by mirror-block presence rather than a hardcoded key so a third
    // generation does not reopen it: the 2.5-era packs are the mirrored ones.
    // THE Q8 PACK OF THE ACTIVE GENERATION. This preferred q8_25 unconditionally,
    // so a user pinned back with LTX_MODEL_VERSION=ltx23 whose 2.3 Q8 pack was
    // already complete still saw "Download Q8 weights (~30 GB)" forever — an
    // offer to fetch 30 GB their generation does not load. `characters` is the
    // capability that names it, and it is declared per version.
    const capChars  = caps.characters || {}
    const q8Keys    = ((capChars.repos_by_version || {})[activeVersion]
                       || (capChars.repos_by_version || {})[capChars.default_version] || [])
    const q8Repo    = q8Keys.map(k => repos.find(r => r.key === k)).filter(Boolean)[0]
                   || repos.find(r => r.key === "q8_25") || repos.find(r => r.key === "q8")
    const q8Size    = q8Repo && q8Repo.size_gb
      ? `~${Math.round(q8Repo.size_gb)} GB` : "unknown size"
    const q8Name    = q8Repo && q8Repo.name ? q8Repo.name : "Q8 weights"

    const base_ready = baseRepos.length > 0 && baseRepos.every(r => repoComplete(installRoot, r, minBytes))
    const q8_ready   = q8Repo ? repoComplete(installRoot, q8Repo, minBytes) : false
    const sharp_ready =
      info.exists("ltx-2-mlx/env/lib/python3.11/site-packages/pipersr") ||
      info.exists("ltx-2-mlx/env/lib/python3.11/site-packages/pipersr-1.0.0.dist-info")
    // Qwen-Image-Edit-2509 readiness — the mflux package on PATH is the
    // canonical signal that the user opted into multi-reference image
    // generation. Weights live lazily in ~/.cache/huggingface (outside
    // install dir, so they survive Reset). Probing for the per-family CLI
    // covers the case where mflux was upgraded but the new
    // `mflux-generate-qwen-edit` binary didn't land (mflux <0.17.5).
    const qwen_ready =
      info.exists("ltx-2-mlx/env/bin/mflux-generate-qwen-edit")
    // Hailuo H3 readiness — the engine's own venv, its runner, and EVERY weight
    // component the panel requires.
    //
    // The old note here said "the one 41 GB file; if that landed, the small
    // siblings did too". That assumption is what Medium 6 was: a partial install
    // where the DiT arrived and a compact Q8 component did not looked ready from
    // this side and refused to run from the panel's, and because the menu thought
    // it was fine it hid the Install/Repair entry that would have fixed it. The
    // components are declared once in required_files.json and checked here, so
    // the assumption is gone rather than reworded.
    //
    // Weights live under mlx_models/ so they survive Reset like every other
    // model, and both download layouts are accepted (capabilities.h3.model_roots
    // mirrors _h3_model_roots() in the panel).
    //
    // Probed with Node's own fs, NOT info.exists() — deliberately, and this is
    // the whole fix. `uv venv` builds the venv interpreter as a symlink chain
    // into Pinokio's SHARED managed Python:
    //     .venv/bin/python3.11 -> python
    //     .venv/bin/python     -> <pinokio>/cache/XDG_DATA_HOME/uv/python/
    //                             cpython-3.11-macos-aarch64-none/bin/python3.11
    // That target belongs to Pinokio, not to us: any other pack install (or
    // any other Pinokio app) that makes uv re-resolve, bump or prune the
    // managed interpreter leaves the chain DANGLING. Nothing is deleted — the
    // venv, the clone and all ~75 GB of weights stay put — but H3 stops
    // resolving. That is the v3.4.0 "installed other packs and Hailuo H3
    // vanished" report.
    //
    // required_files.json already documents info.exists() as unreliable on
    // exactly this chain, so it cannot answer the question we need answered
    // here — "does this interpreter still resolve?" — in either direction.
    // fs.existsSync FOLLOWS symlinks, so a dangling chain is definitively
    // false, which makes the menu agree with the panel's _h3_python() by
    // construction. (repoComplete() above already probes with fs.statSync +
    // absolute paths for the same reason.) Keep the two in sync.
    const onDisk = (rel) => {
      try { return fs.existsSync(path.join(installRoot, rel)) } catch (e) { return false }
    }

    // ...AND AT THE ROOT THE PANEL ACTUALLY USES. required_files.json states the
    // H3 paths relative to the install dir, but the panel's H3_ROOT / H3_MODELS
    // are env-overridable (mlx_ltx_panel.py — `LTX_H3_ROOT`, `LTX_H3_MODELS`,
    // documented in docs/H3_ENGINE.md as THE way to point a second install at an
    // existing 75 GB checkout instead of duplicating it). This menu joined every
    // manifest path onto installRoot unconditionally, so on exactly that setup
    // the panel reported H3 installed and the menu reported it missing — and
    // offered a 75 GB download for weights already on disk, next to a "Repair"
    // for an engine that was never broken. Two consumers, one manifest, and now
    // one set of roots. The two prefixes below are the panel's own defaults;
    // they are what the override REPLACES, so they have to be named somewhere.
    const H3_ROOT_REL = "minimax-h3-mlx"
    const H3_MODELS_REL = "mlx_models/hailuo-h3"
    // A relative override is relative to the install dir (same as the default it
    // replaces); an absolute one passes straight through. path.join collapses a
    // trailing "" cleanly, so an exact prefix match resolves to the root itself.
    const underRoot = (base, rel) =>
      path.isAbsolute(base) ? path.join(base, rel) : path.join(installRoot, base, rel)
    const h3Path = (rel) => {
      const norm = String(rel).replace(/\\/g, "/")
      for (const [prefix, override] of [[H3_ROOT_REL, envFile.LTX_H3_ROOT],
                                        [H3_MODELS_REL, envFile.LTX_H3_MODELS]]) {
        if (!override) continue
        if (norm === prefix) return underRoot(override, "")
        if (norm.startsWith(prefix + "/")) return underRoot(override, norm.slice(prefix.length + 1))
      }
      return path.join(installRoot, norm)
    }
    const h3Resolves = (rel) => {
      try { return fs.existsSync(h3Path(rel)) } catch (e) { return false }
    }
    // DECLARED, NOT RE-DERIVED. This used to call H3 ready on venv + runner +
    // the one big DiT, while the panel additionally required every compact Q8
    // component and the upstream text config — so a partial install hid the
    // Install/Repair entry here while the panel refused to run H3, and the user
    // had no route to the fix. Both sides now read the same component list from
    // required_files.json → capabilities.h3.
    const capH3 = ((required.capabilities || {}).h3) || {}
    const h3_venv = (capH3.venv_any || []).some(h3Resolves)
    const h3_runner = (capH3.paths || []).every(h3Resolves)
    // The weights are the expensive thing (~75 GB) and they live under
    // mlx_models/, a completely different tree from the clone — so they
    // routinely survive whatever broke the engine. `model_roots` are
    // ALTERNATIVES (either download layout is valid); every entry in `models`
    // is required beneath whichever root carried the DiT.
    // PER FILE, not per root. This required every component beneath ONE root,
    // while the panel resolves each file against any of them — and the panel is
    // right: LTX_H3_MODELS and LTX_H3_COMPACT_DIR legitimately split a layout
    // across roots, which is the documented dev setup. On a split layout the
    // panel said installed and this said not, so Pinokio offered a 75 GB
    // install for weights already on disk. Two consumers, one manifest, and now
    // one resolution rule.
    const h3_weights = (capH3.models || []).every(rel =>
      (capH3.model_roots || []).some(root => h3Resolves(root + "/" + rel)))
    const h3_ready = h3_venv && h3_runner && h3_weights
    // Weights on disk but the code/venv gone → a REPAIR, not a 75 GB install.
    // install_h3.js is idempotent and skips every intact weight, so the same
    // script serves both; only the label changes, because telling a user to
    // "install ~75 GB" when they already have the 75 GB is the thing that
    // made this look like data loss.
    const h3_repair = h3_weights && !h3_ready

    const musicRoot = underRoot(envFile.LTX_MUSIC_ROOT || "yue2-mlx", "")
    const musicModels = underRoot(envFile.LTX_MUSIC_MODELS || "mlx_models/yue2", "")
    const musicFile = p => { try { return fs.statSync(p).isFile() } catch (e) { return false } }
    let musicWeights = false
    try {
      const generator = path.join(musicModels, "generator")
      const files = JSON.parse(fs.readFileSync(path.join(generator, "conversion.json"), "utf8")).files
      const generatorNames = ["LICENSE", "THIRD_PARTY_NOTICES.md", "ar-8bit.safetensors", "ar-bf16.safetensors",
        "config.json", "licenses/SnakeBeta-NVIDIA-MIT.txt", "licenses/stable-audio-tools-MIT.txt",
        "nar-bf16.safetensors", "qwen.tiktoken"]
      const allowed = new Set([...Object.keys(files), "conversion.json", "README.md"])
      const clean = (dir, prefix = "") => fs.readdirSync(dir, { withFileTypes: true }).every(e => {
        const rel = prefix + e.name
        if (e.isSymbolicLink()) return false
        if (e.isDirectory()) return [...allowed].some(n => n.startsWith(rel + "/")) && clean(path.join(dir, e.name), rel + "/")
        return allowed.has(rel)
      })
      const vae = path.join(musicModels, "vae")
      const vfiles = JSON.parse(fs.readFileSync(path.join(vae, "weights_manifest.json"), "utf8")).files
      const intact = (base, entries) => Object.entries(entries).every(([n, r]) => {
        if (n.includes("..") || path.isAbsolute(n)) return false
        const st = fs.lstatSync(path.join(base, n))
        return st.isFile() && st.size > 0 && st.size === r.bytes && /^[0-9a-f]{64}$/.test(r.sha256)
      })
      musicWeights = Object.keys(files).length === generatorNames.length && generatorNames.every(n => files[n])
        && clean(generator) && intact(generator, files)
        && vfiles["model.safetensors"].bytes === 530512720
        && vfiles["model.safetensors"].sha256 === "807ce9d5149fa27c5ad3e6582058469852e908f6c5acc8c8aa338e7ab7751346"
        && intact(vae, vfiles)
        && ["config.json", "LICENSE", "THIRD_PARTY_NOTICES.md"].every(n => musicFile(path.join(vae, n)))
        && fs.readdirSync(path.join(vae, "licenses")).length > 0
    } catch (e) { /* partial pack: keep the install route available */ }
    const musicReady = musicWeights && ["python", "python3.12"].some(n => {
      const p = path.join(musicRoot, ".venv/bin", n)
      try { fs.accessSync(p, fs.constants.X_OK); return musicFile(p) } catch (e) { return false }
    })
      && musicFile(path.join(musicRoot, "pyproject.toml"))
      && musicFile(path.join(installRoot, "scripts/music/yue2_run.py"))
    const musicRepair = musicWeights && !musicReady
    const musicMenu = () => !musicCapable() ? [] : [musicReady || musicRepair
      ? { icon: "fa-solid fa-screwdriver-wrench", text: "Repair the music engine (weights kept — no re-download)", href: "install_music.js" }
      : { icon: "fa-solid fa-music", text: "Install the music engine (YuE2, ~11 GB)", href: "install_music.js" }]


    // Keep the H3 recovery affordance reachable from EVERY menu state, not
    // only the healthy one. Reset wipes ltx-2-mlx, so env_ready goes false and
    // the menu early-returns (below) long before it reaches the H3 row — which
    // is exactly why the v3.4.0 reporter ran Reset and still found "no H3
    // anywhere". The pack is independent of the LTX install (own clone, own
    // venv, weights in a different tree), so offering the repair mid-reinstall
    // is safe and never competes with the default action.
    const pushH3Recovery = (m) => {
      if (musicRepair && musicCapable()) {
        m.push({ icon: "fa-solid fa-screwdriver-wrench", text: "Repair the music engine (weights kept — no re-download)", href: "install_music.js" })
      }
      if (h3_repair && h3Capable()) {
        m.push({ icon: "fa-solid fa-screwdriver-wrench",
                 text: "Repair Hailuo H3 (weights kept — no re-download)",
                 href: "install_h3.js" })
      }
      return m
    }

    // --- LTX engine health: the SAME dangling-interpreter class, one tree over -
    // `env_ready` above trusts a marker path — ltx-2-mlx/env/pyvenv.cfg, a plain
    // FILE that survives everything. The interpreter beside it does not: install
    // .js builds it with `uv venv`, which makes bin/python a symlink chain into
    // Pinokio's SHARED managed Python, and any other pack (or any other Pinokio
    // app) that makes uv re-resolve, bump or prune that interpreter leaves the
    // chain DANGLING. That is precisely the v3.4.0 report the H3 probes above
    // exist for — LTX is simply the tree nobody re-checked afterwards.
    //
    // In that state the menu showed a confident `default: true` Start, Python
    // never came up, and the only entries offered were Update (which pip-installs
    // into the venv that isn't there) and Reset (which deletes the install). No
    // route to the fix, for a machine whose venv is the ONLY broken thing.
    // fs.existsSync FOLLOWS symlinks, so a dangling chain is definitively false.
    //
    // install.js IS the repair: its ltx_venv.sh step probes the interpreter,
    // rebuilds only when it does not run, and every download step after it skips
    // what is already on disk. Nothing is re-fetched — hence "models kept".
    const ltx_python = onDisk("ltx-2-mlx/env/bin/python3.11") ||
                       onDisk("ltx-2-mlx/env/bin/python")
    const ltx_repair = env_ready && !ltx_python
    const pushLtxRepair = (m) => {
      if (ltx_repair) {
        m.push({ icon: "fa-solid fa-screwdriver-wrench",
                 text: "Repair Phosphene engine (models kept)",
                 href: "install.js" })
      }
      return m
    }

    // User-content folders persist across Reset (which only removes the venv).
    // Keep their shortcuts visible whenever they exist on disk so users can
    // still recover their renders / models / uploads.
    const has_outputs = info.exists("mlx_outputs")
    const has_models  = info.exists("mlx_models")
    const has_uploads = info.exists("panel_uploads")

    // --- has an install already been ATTEMPTED here? ------------------------
    // THE INFINITE-RESTART FIX (@natxou field report, v3.5.0).
    //
    // Two Pinokio rules combine badly. From PINOKIO.md:
    //   "Dynamic menu rendering" — the sidebar menu is re-rendered every time
    //   a step in the currently running script finishes.
    //   "Auto-executing menu items" — when a menu item marked `default: true`
    //   has a script as its href, selecting it also STARTS that script.
    //
    // So a `default: true` -> install.js entry is only safe while install.js
    // is still capable of succeeding. The moment install.js aborts — a dropped
    // connection during the ltx-2-mlx clone, a HuggingFace 5xx mid-download, a
    // full disk — the menu re-renders, still sees an incomplete install, hands
    // Pinokio the very same auto-run entry, and Pinokio starts install.js
    // again. Which aborts again. Forever.
    //
    // The user-visible symptom is NOT a crash loop in the panel: it is the
    // Pinokio console scrolling
    //     Starting Shell <uuid> ... Terminated Shell <uuid>
    // with a DIFFERENT uuid every time (Pinokio gives each command in a
    // `message` array its own shell, so one install pass alone emits a dozen),
    // the app parked at ~90 MB on disk, and Python never starting — because
    // nothing in this state ever reaches start.js. Present in every public
    // release from v2.0.0 through v3.5.0; it just needed a failing install to
    // become visible.
    //
    // fs.link is install.js's FIRST side-effecting step — it runs before the
    // clone, before the venv, before every download — so these folders exist
    // after even an attempt that died immediately. Their presence is a
    // reliable "install.js has already run at least once" marker. Probed with
    // fs.existsSync as well as info.exists because fs.link creates them as
    // symlinks into Pinokio's drive, and a dangling link must still count as
    // an attempt (see the H3 note above for the same lesson).
    //
    // Net effect: a clean machine still auto-installs on first open (nothing
    // exists yet, so nothing to detect). Every state AFTER a failed attempt
    // waits for a deliberate click instead of respawning itself.
    // (`onDisk` is defined next to the H3 probes above — same fs.existsSync
    // reasoning, one definition.)
    const install_attempted =
      has_models || has_outputs || has_uploads ||
      onDisk("mlx_models") || onDisk("mlx_outputs") ||
      onDisk("panel_uploads") || onDisk("state") || onDisk("ltx-2-mlx")

    const running = {
      install:    info.running("install.js"),
      start:      info.running("start.js"),
      update:     info.running("update.js"),
      reset:      info.running("reset.js"),
      q8download: info.running("download_q8.js"),
      sharp:      info.running("install_sharp.js"),
      qwen:       info.running("install_qwen.js"),
      h3:         info.running("install_h3.js"),
      music:      info.running("install_music.js"),
    }

    // Running states first — show what's in progress, hide everything else.
    if (running.install)    return [{ default: true, icon: "fa-solid fa-plug",     text: "Installing",                   href: "install.js" }]
    if (running.update)     return [{ default: true, icon: "fa-solid fa-rotate",   text: "Updating",                     href: "update.js" }]
    if (running.reset)      return [{ default: true, icon: "fa-solid fa-eraser",   text: "Resetting",                    href: "reset.js" }]
    if (running.q8download) return [{ default: true, icon: "fa-solid fa-download", text: `Downloading ${q8Name} (${q8Size})`, href: "download_q8.js" }]
    if (running.sharp)      return [{ default: true, icon: "fa-solid fa-wand-magic-sparkles", text: "Installing Sharp upscaler", href: "install_sharp.js" }]
    if (running.qwen)       return [{ default: true, icon: "fa-solid fa-images", text: "Installing Qwen-Image-Edit (multi-ref)", href: "install_qwen.js" }]
    if (running.music) return [{ default: true, icon: "fa-solid fa-music", text: "Installing the music engine (~11 GB)", href: "install_music.js" }]
    if (running.h3)         return [{ default: true, icon: "fa-solid fa-comments", text: "Installing Hailuo H3 (~75 GB)", href: "install_h3.js" }]

    // No env at all → fresh install path. Recovery shortcuts to user content
    // folders if a previous install left files behind.
    if (!env_ready) {
      // `default: true` — and therefore Pinokio's auto-run — ONLY on a machine
      // that has never attempted an install. Anywhere else this is the entry
      // that restarts a failing install.js forever (see install_attempted).
      const m = [install_attempted
        ? { icon: "fa-solid fa-rotate-right",
            text: "Resume Install (last attempt didn't finish — click to retry)",
            href: "install.js" }
        : { default: true, icon: "fa-solid fa-plug", text: "Install", href: "install.js" }]
      if (has_outputs) m.push({ icon: "fa-solid fa-film",  text: "Outputs", href: "mlx_outputs?fs=true" })
      if (has_models)  m.push({ icon: "fa-solid fa-cube",  text: "Models",  href: "mlx_models?fs=true" })
      if (has_uploads) m.push({ icon: "fa-solid fa-image", text: "Uploads", href: "panel_uploads?fs=true" })
      pushH3Recovery(m)
      // Escape hatch. This branch used to offer no Reset at all, so a user
      // whose install died before the venv existed had nothing to click except
      // the thing that kept failing.
      if (install_attempted) m.push({ icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" })
      return m
    }

    // Env exists but base models aren't fully there → SHIP-BLOCKER fix.
    // Don't show Start — the panel would crash on the first job. Run
    // install.js again (it's idempotent: skips clone + venv if present,
    // re-runs `hf download` which itself resumes any partial files).
    if (!base_ready) {
      const m = [
        // Deliberately NOT `default: true`. Reaching this state means
        // install.js has already run and did not complete, so auto-running it
        // is precisely the respawn loop documented at install_attempted. The
        // user clicks Resume when they're ready.
        { icon: "fa-solid fa-rotate-right", text: "Resume Install (base models incomplete)", href: "install.js" },
      ]
      if (has_outputs) m.push({ icon: "fa-solid fa-film",  text: "Outputs", href: "mlx_outputs?fs=true" })
      if (has_models)  m.push({ icon: "fa-solid fa-cube",  text: "Models",  href: "mlx_models?fs=true" })
      if (has_uploads) m.push({ icon: "fa-solid fa-image", text: "Uploads", href: "panel_uploads?fs=true" })
      pushLtxRepair(m)
      pushH3Recovery(m)
      m.push({ icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" })
      return m
    }

    if (running.start) {
      const local = info.local("start.js")
      if (local && local.url) {
        return [
          { default: true, icon: "fa-solid fa-rocket", text: "Open Panel", href: local.url },
          { icon: "fa-solid fa-terminal", text: "Terminal",   href: "start.js" },
          { icon: "fa-solid fa-film",     text: "Outputs",    href: "mlx_outputs?fs=true" },
          { icon: "fa-solid fa-cube",     text: "Models",     href: "mlx_models?fs=true" },
          { icon: "fa-solid fa-image",    text: "Uploads",    href: "panel_uploads?fs=true" },
          // Settings → Models tells a running panel's user to click this, so it
          // has to exist while the panel runs. The install touches only
          // yue2-mlx/ and mlx_models/yue2/; the panel picks it up on /status.
          ...musicMenu(),
        ]
      }
      return [{ default: true, icon: "fa-solid fa-terminal", text: "Terminal", href: "start.js" }, ...musicMenu()]
    }

    // Healthy install — Start path.
    const baseMenu = [
      { default: true, icon: "fa-solid fa-power-off", text: "Start",   href: "start.js" },
      { icon: "fa-solid fa-film",  text: "Outputs", href: "mlx_outputs?fs=true" },
      { icon: "fa-solid fa-cube",  text: "Models",  href: "mlx_models?fs=true" },
      { icon: "fa-solid fa-image", text: "Uploads", href: "panel_uploads?fs=true" },
    ]
    // Right under Start, because it is what the user reaches for when Start does
    // nothing: base models and clone intact, interpreter gone. See ltx_repair.
    pushLtxRepair(baseMenu)
    if (!q8_ready) {
      // ~30 GB and what it actually buys on 2.5: trained characters and voices.
      // High additionally needs the separate 29.5 GB add-on, which is offered
      // in Settings -> Models rather than here — one menu entry, one download.
      // Size and wording follow the pack that will actually be fetched.
      baseMenu.push({ icon: "fa-solid fa-download",
                      text: `Download ${q8Name} (${q8Size}) — trained characters + voices`,
                      href: "download_q8.js" })
    }
    if (!sharp_ready) {
      baseMenu.push({ icon: "fa-solid fa-wand-magic-sparkles", text: "Install Sharp upscaler (PiperSR, optional)", href: "install_sharp.js" })
    }
    if (!qwen_ready) {
      // mflux image-engine pack now ships in install.js/update.js; this entry
      // is a recovery action shown only when the pack isn't present (e.g. a
      // failed pip step, or a pre-3.2.1 install that hasn't updated). Renamed
      // off "Qwen-Image-Edit" — it enables Ideogram 4 too (cocktailpeanut's
      // confusion: installing "Qwen" to use Ideogram).
      baseMenu.push({ icon: "fa-solid fa-images", text: "Reinstall image engines (Ideogram 4 + Qwen-Edit)", href: "install_qwen.js" })
    }
    baseMenu.push(...musicMenu())
    if (!h3_ready && h3Capable()) {
      // Second VIDEO engine — joint picture + dialogue + sound. Opt-in only:
      // ~75 GB, 36 GB+ Macs (H3_MIN_BYTES above), MiniMax Community License
      // with territory restrictions. Hidden entirely on machines that can't
      // run it, so it never reads as a missing piece of the base install.
      baseMenu.push(h3_repair
        ? { icon: "fa-solid fa-screwdriver-wrench", text: "Repair Hailuo H3 (weights kept — no re-download)", href: "install_h3.js" }
        // ENGINES ARE PEERS: "optional" told a user that half the panel's
        // video capability was a side dish. The entry names what it IS.
        // The panel's install card quotes this string verbatim -- change both.
        : { icon: "fa-solid fa-comments", text: "Install Hailuo H3 (second video engine, ~75 GB)", href: "install_h3.js" })
    } else if (h3_ready) {
      // THE DOOR MUST NOT CLOSE BEHIND THE INSTALL. The panel's live-preview
      // note tells users with an older H3 runner to update it "from the
      // Phosphene sidebar" — but this entry used to exist only while H3 was
      // NOT installed, so the exact people the note addressed had no button
      // to press (reported by a user on X, 2026-08-18, whose sidebar showed
      // only the sharp/Q8 offers). install_h3.js is idempotent: runner
      // refresh, weights untouched.
      baseMenu.push({ icon: "fa-solid fa-rotate", text: "Update Hailuo H3 runner (weights kept — no re-download)", href: "install_h3.js" })
    }
    baseMenu.push(
      { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" },
      { icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" },
    )
    return baseMenu
  }
}
