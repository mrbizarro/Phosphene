module.exports = {
  run: [
    // Wipe the ltx-2-mlx clone and the venv inside it. Models stay (huge).
    // Users who want to nuke models too should delete mlx_models/ via the
    // "Models" file-browser entry in the Pinokio sidebar.
    //
    // DELIBERATELY NOT TOUCHED — the optional Hailuo H3 pack:
    //   minimax-h3-mlx/        its clone + its own venv
    //   mlx_models/hailuo-h3/  ~75 GB of weights (user content, must persist)
    // H3 has no dependency on the LTX venv, so wiping LTX must never cost a
    // user a 75 GB re-download. Note the corollary, learned from the v3.4.0
    // report: because Reset does not touch H3, it also cannot REPAIR a broken
    // H3 — reaching for Reset when the engine disappears is wasted effort.
    // The fix for that lives in pinokio.js ("Repair Hailuo H3", kept visible
    // in every menu state including post-Reset) and install_h3.js (which
    // rebuilds a dangling venv in place). If you ever add an H3 path here,
    // you are deleting user content — don't.
    { method: "fs.rm", params: { path: "ltx-2-mlx" } }
  ]
}
