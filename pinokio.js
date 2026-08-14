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

// Hailuo H3 (optional second video engine): since v3.7.0 the Q8 DiT lane runs
// in ~27 GiB peak, so the floor is 46 GB — the same number install_h3.js's
// preflight and H3_MIN_RAM_GB_Q8 in mlx_ltx_panel.py use. Keep all three in
// sync. (A 48 GB Mac reports ~47.x GB after firmware reservations.)
const H3_MIN_BYTES = 46 * 1000 * 1000 * 1000

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
  title: "Phosphene",
  // The Pinokio store listing. It still said "via LTX 2.3 (MLX)" three weeks after 2.5
  // became the generation a fresh install renders with — the first sentence a prospective
  // user reads, naming the wrong engine, while a confused existing user hunting a "why
  // does it keep asking for LTX 2.3" answer finds it confirming their suspicion.
  description: "[MAC ONLY] Local generative video panel for Apple Silicon. Joint audio+video via LTX-2.5 (MLX), with Hailuo H3 as a second engine. T2V, I2V, FFLF, Extend, trained characters. Lossless h264. Hardware-tier feature gating. Free, open source.",
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
    try {
      const envPath = path.join(installRoot, "ENVIRONMENT")
      if (fs.existsSync(envPath)) {
        const m = fs.readFileSync(envPath, "utf8")
          .split("\n").filter(l => !/^\s*#/.test(l))
          .join("\n").match(/^\s*LTX_MODEL_VERSION\s*=\s*(\S+)\s*$/m)
        if (m && (capRender.repos_by_version || {})[m[1]]) activeVersion = m[1]
      }
    } catch (e) { /* unreadable ENVIRONMENT: keep the manifest default */ }
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
    const h3Resolves = (rel) => {
      try { return fs.existsSync(path.join(installRoot, rel)) } catch (e) { return false }
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

    // Keep the H3 recovery affordance reachable from EVERY menu state, not
    // only the healthy one. Reset wipes ltx-2-mlx, so env_ready goes false and
    // the menu early-returns (below) long before it reaches the H3 row — which
    // is exactly why the v3.4.0 reporter ran Reset and still found "no H3
    // anywhere". The pack is independent of the LTX install (own clone, own
    // venv, weights in a different tree), so offering the repair mid-reinstall
    // is safe and never competes with the default action.
    const pushH3Recovery = (m) => {
      if (h3_repair && h3Capable()) {
        m.push({ icon: "fa-solid fa-screwdriver-wrench",
                 text: "Repair Hailuo H3 (weights kept — no re-download)",
                 href: "install_h3.js" })
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
    const onDisk = (rel) => {
      try { return fs.existsSync(path.join(installRoot, rel)) } catch (e) { return false }
    }
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
    }

    // Running states first — show what's in progress, hide everything else.
    if (running.install)    return [{ default: true, icon: "fa-solid fa-plug",     text: "Installing",                   href: "install.js" }]
    if (running.update)     return [{ default: true, icon: "fa-solid fa-rotate",   text: "Updating",                     href: "update.js" }]
    if (running.reset)      return [{ default: true, icon: "fa-solid fa-eraser",   text: "Resetting",                    href: "reset.js" }]
    if (running.q8download) return [{ default: true, icon: "fa-solid fa-download", text: "Downloading Q8 weights (~30 GB)", href: "download_q8.js" }]
    if (running.sharp)      return [{ default: true, icon: "fa-solid fa-wand-magic-sparkles", text: "Installing Sharp upscaler", href: "install_sharp.js" }]
    if (running.qwen)       return [{ default: true, icon: "fa-solid fa-images", text: "Installing Qwen-Image-Edit (multi-ref)", href: "install_qwen.js" }]
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
        ]
      }
      return [{ default: true, icon: "fa-solid fa-terminal", text: "Terminal", href: "start.js" }]
    }

    // Healthy install — Start path.
    const baseMenu = [
      { default: true, icon: "fa-solid fa-power-off", text: "Start",   href: "start.js" },
      { icon: "fa-solid fa-film",  text: "Outputs", href: "mlx_outputs?fs=true" },
      { icon: "fa-solid fa-cube",  text: "Models",  href: "mlx_models?fs=true" },
      { icon: "fa-solid fa-image", text: "Uploads", href: "panel_uploads?fs=true" },
    ]
    if (!q8_ready) {
      // ~30 GB and what it actually buys on 2.5: trained characters and voices.
      // High additionally needs the separate 29.5 GB add-on, which is offered
      // in Settings -> Models rather than here — one menu entry, one download.
      // Size and wording follow the pack that will actually be fetched.
      const q8Size = q8Repo && q8Repo.size_gb ? `~${Math.round(q8Repo.size_gb)} GB` : "~30 GB"
      baseMenu.push({ icon: "fa-solid fa-download",
                      text: `Download Q8 weights (${q8Size}) — trained characters + voices`,
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
    if (!h3_ready && h3Capable()) {
      // Second VIDEO engine — joint picture + dialogue + sound. Opt-in only:
      // ~75 GB, 64 GB+ Macs, MiniMax Community License with territory
      // restrictions. Hidden entirely on machines that can't run it, so it
      // never reads as a missing piece of the base install.
      baseMenu.push(h3_repair
        ? { icon: "fa-solid fa-screwdriver-wrench", text: "Repair Hailuo H3 (weights kept — no re-download)", href: "install_h3.js" }
        // ENGINES ARE PEERS: "optional" told a user that half the panel's
        // video capability was a side dish. The entry names what it IS.
        // The panel's install card quotes this string verbatim -- change both.
        : { icon: "fa-solid fa-comments", text: "Install Hailuo H3 (second video engine, ~75 GB)", href: "install_h3.js" })
    }
    baseMenu.push(
      { icon: "fa-solid fa-rotate", text: "Update", href: "update.js" },
      { icon: "fa-regular fa-circle-xmark", text: "Reset", href: "reset.js" },
    )
    return baseMenu
  }
}
