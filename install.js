// Install the tested runtime first. Models are requested inside the app.
// Engine pin: v0.14.19+ltx25.7, shared with Update in ltx_checkout.sh.
// fs.link keeps models, outputs and settings across Reset. All steps resume.
module.exports = {
  requires: {
    bundle: "ai"
  },
  run: [
    {
      when: "{{platform !== 'darwin' || arch !== 'arm64'}}",
      method: "notify",
      params: {
        html: "<b>Phosphene requires an Apple Silicon Mac (M1 or newer).</b><br>It will not run on Intel Macs or Windows. Linux on Apple Silicon (M1/M2) installs outside Pinokio: run <code>bash scripts/linux/install.sh</code> in the app folder."
      },
      next: null
    },
    {
      method: "fs.link",
      params: {
        drive: {
          mlx_models: "mlx_models",
          mlx_outputs: "mlx_outputs",
          panel_uploads: "panel_uploads",
          state: "state"
        }
      }
    },
    {
      method: "notify",
      params: {
        html: "<b>Installing Phosphene.</b> The app opens after Python and its engine packages are ready, usually in a few minutes. <b>No models download before Start.</b> Setup needs about <b>18.0 GB free</b>, including 15 GB kept free for your work.<br><br>Choose what to make inside the app. Each feature shows its model size before you download it. Interrupted downloads resume."
      }
    },
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/install_preflight.sh"
      }
    },
    {
      when: "{{!exists('ltx-2-mlx/.git')}}",
      method: "shell.run",
      params: {
        message: [
          "git clone https://github.com/dgrauet/ltx-2-mlx.git ltx-2-mlx"
        ]
      }
    },
    {
      when: "{{!exists('ltx-2-mlx/.git')}}",
      method: "notify",
      params: {
        html: "<b>Install stopped: the video engine could not be downloaded.</b><br>Phosphene needs to download <code>github.com/dgrauet/ltx-2-mlx</code> and that step did not finish.<br><br>This is almost always a network problem (no connection, a VPN or proxy, or GitHub blocked) or a full disk. Check your connection and free space, then click <b>Install</b> again. Anything already downloaded is kept and will not be downloaded twice."
      },
      next: null
    },
    // ---- ltx-2-mlx version: PIN to v0.14.19 (2026-08-11 catch-up). dgrauet asked on
    //      2026-05-12 to lock onto a tag because he pushes breaking changes
    //      upstream to sync with the official Lightricks repo. Without a
    //      tag pin, every fresh install (and every Update) would pull the
    //      next breaking push and Phosphene would fail to start.
    //
    //      v0.14.19 is the pinned tag against which the current panel +
    //      helper + patch_ltx_codec.py are validated (2026-08-11 catch-up
    //      from v0.14.8, 11 releases). What it brings, in the order it
    //      matters to us:
    //        - 0.14.11 — the AV cross-attention gate is finally read FROM the
    //          checkpoint (`av_ca_timestep_scale_multiplier` 1000, not the
    //          dataclass default 1). Audio/dialogue weighting changes for
    //          every render, toward upstream parity. This also lands
    //          `LTXModelConfig.from_checkpoint_dir()` — a metadata-driven
    //          config path we need for any second model generation.
    //        - 0.14.15 — `frame_rate` forwarded at the A2V/lipdub call sites
    //          (our `_install_a2v_frame_rate_patch` shim is now inert there),
    //          and muxed video is no longer truncated to the shortest stream.
    //        - 0.14.16 — quantized transformers load at ANY group_size, not
    //          just 64; ic-lora dev mode fuses the distilled LoRA correctly.
    //        - 0.14.19 — macOS GPU-watchdog kills are explained instead of
    //          dying cryptically; the DiT is freed before VAE decode in
    //          low-memory mode; the Gemma encoder stops widening a stricter
    //          cache limit.
    //      Weight layout is UNCHANGED by this bump. 0.14.13+ prefers a
    //      versioned `transformer-distilled-*.safetensors` when one exists
    //      and falls back to the unversioned name — our shipped model dirs
    //      have no `-1.1` files (update.js trims them), so resolution is
    //      byte-identical to v0.14.8. No re-download, no manifest change.
    //
    //      STAY PINNED here: do not auto-track upstream main. Tag-bumps are a
    //      deliberate decision — read his release notes, smoke-test the full
    //      modality matrix on dev, bump `_LTX_EXPECTED_VERSION` in
    //      mlx_warm_helper.py, then bump this pin AND update.js in one commit.
    //
    //      Idempotent — works on a fresh clone (already on the cloned
    //      branch's tip) AND on a re-install where the clone exists.
    //      2026-08-12 — THE PIN IS NOW A FORK BUILD, NOT AN UPSTREAM TAG.
    //      Vendored: mrbizarro/ltx-2-mlx `feat/ltx-2.5` @ e6be9d6, which is
    //      v0.14.19 (1192051) plus the LTX-2.5 port — keyframe pos-emb, the
    //      vendored Gemma 4 tower, the Euler-ancestral sampler, the duration
    //      head. dgrauet has no 2.5 branch; if he ports it we drop ours and
    //      go back to a tag.
    //
    //      This is what lets LTX-2.5 render THROUGH the panel. v0.14.19 could
    //      register the 2.5 packs but never load them: it does not build
    //      keyframes_abs_pos_embedding and cannot construct a Gemma 4 tower.
    //
    //      Every 2.5 flag defaults to its 2.3 value, so an unversioned
    //      checkpoint builds exactly what it built before — proven here by
    //      876/22 on the vendored suite, a byte-identical job-dict capture
    //      across 18 form shapes, and THREE real 2.3 draft renders whose mp4s
    //      are sha256-identical to the one recorded before any of this work
    //      (three arms, the last of which renders out of the INSTALLED
    //      packages with no PYTHONPATH override — i.e. the thing a user
    //      actually runs).
    //
    //      What this pin CHANGES, and only on 2.5: the Euler-ancestral sampler
    //      is finally reached (it had zero callers, so every prior 2.5 render
    //      used the 2.3 Euler step), and stage 2 starts at the vendor's 0.85
    //      instead of 2.3's 0.909375. Both are keyed off the checkpoint's own
    //      model_version, which is why 2.3 can be byte-stable across a change
    //      this large.
    //
    //      The packages report `0.14.19+ltx25.2`, and `_LTX_EXPECTED_VERSION`
    //      in mlx_warm_helper.py must equal that string. The local segment is
    //      the ONLY thing distinguishing this tree from upstream v0.14.19 at
    //      runtime — the release segment is deliberately unchanged. Move the
    //      two together.
    //      2026-08-13 (v4.0) — THE PIN IS A TAG, AND IT NO LONGER LIVES IN A
    //      LAUNCHER SCRIPT. Both lanes now call the one implementation in
    //      `scripts/pinokio/ltx_checkout.sh`, which holds the literal:
    //
    //          v0.14.19+ltx25.7   on mrbizarro/ltx-2-mlx
    //
    //      Two reasons, both structural. (1) A SHA on a branch is not a pin:
    //      v3.8.x fetched `feat/ltx-2.5` and checked out a SHA that the branch
    //      head had ALREADY moved past, so one rebase upstream would have
    //      stranded every existing install with an un-fetchable pin and a dead
    //      Update button. (2) A pin written in `update.js` can only ever move
    //      ONE CLICK LATE — Pinokio loads that file before our own step pulls
    //      the repo — so it has to live in the post-pull tree. install.js has
    //      no such constraint but shares the file anyway: before v4.0 the two
    //      lanes carried byte-identical copies of this block, and a duplicated
    //      fix is a fix that half-lands.
    //
    //      2026-08-14 (v4.0.2) — the pin moves to `v0.14.19+ltx25.6` for one
    //      change, and it is the reason image-to-video on 2.5 now animates the
    //      image you gave it: the euler-ancestral step rescaled and re-noised
    //      the pinned conditioning tokens on every intermediate step, so the
    //      clip was composed without them and only the terminal step stamped
    //      the image back in. The sample is now re-composited against the clean
    //      latent, which is the half of ComfyUI's 2.5 i2v graph this port had
    //      not implemented. t2v is sha256-identical across the change and every
    //      Euler lane never reaches the branch.
    //
    //      The packages report `0.14.19+ltx25.6`, and `_LTX_EXPECTED_VERSION`
    //      in mlx_warm_helper.py must equal that string. The local segment is
    //      the ONLY thing distinguishing this tree from upstream v0.14.19 at
    //      runtime — the release segment is deliberately unchanged. Move the
    //      two together. `node scripts/check_ltx_pin.js` is the gate that keeps
    //      this comment, the shell script and the helper constant in agreement.
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/ltx_checkout.sh"
      }
    },
    {
      method: "shell.run",
      params: {
        path: "ltx-2-mlx",
        message: "bash ../scripts/pinokio/ltx_venv.sh"
      }
    },
    // ---- Install MLX pipeline packages into the 3.11 venv -----------------
    // `--python env/bin/python` pins the install to the venv we just made
    // (avoids any conda-base interference). uv pip install is idempotent —
    // already-installed packages are no-ops on Resume Install.
    // Non-editable install (no -e): packages get copied into
    // env/lib/python3.11/site-packages/ which is where patch_ltx_codec.py
    // looks for video_vae.py.
    // Pin huggingface-hub>=1.0 explicitly so older Pinokio bundles still
    // get the v1+ `hf` CLI used by the download steps below.
    //
    // SHIP-BLOCKER (2026-04): pin mlx==0.31.1 (NOT 0.31.2). LTX 2.3 audio
    // regresses by 22 dB on mlx 0.31.2 — output peaks at -37 dB instead of the
    // expected -9 to -15 dB. Verified empirically by downgrading mlx in a
    // working install and re-running the same prompt:
    //   mlx 0.31.2 → max_volume -42.8 dB (broken)
    //   mlx 0.31.1 → max_volume -9.2  dB (working)
    // Same packages, same weights, same seed; only mlx differs. Numerical
    // change in 0.31.2 attenuates the vocoder output.
    //
    // 2026-08-28 — mlx 0.32.1 and 0.32.2 EVALUATED AND HELD BACK, on MEMORY,
    // not on audio. Recorded here so the next reader does not re-derive it.
    //
    // The audio half of this blocker is 0.31.2-SPECIFIC and does not survive
    // into 0.32. Identical latents through the isolated vocoder differ by
    // 0.000001 dB, and five full renders per arm land at max_volume -1.7 vs
    // -1.8 dB, -9.2 vs -9.3 LUFS, -1.6 vs -1.7 true peak, no time shift, and
    // spectra within 0.1 dB from 250 Hz to 16 kHz. Claimed 22 dB, measured 0.1.
    // The speed is real as well: 1024x576 Q8 121f goes 139.8 s → 133.6 s
    // (-4.5%), all of it in DiT load and VAE decode, denoise a wash.
    //
    // What holds it back is PEAK MEMORY, and it is not a 64 GB-only problem.
    // 0.31.1's footprint is a function of the WORKLOAD; 0.32.x's is a function
    // of the MACHINE — it consumes ~95% of whatever ceiling MLX derives from
    // RAM (0.95 * hw.memsize; the same formula on both versions, so the
    // ceiling — and the appetite — scales down with the Mac, it does not go
    // away). Peak process footprint, /usr/bin/time -l, same prompt/seed/dims,
    // MLX's memory AND cache limits set to the value each Mac would derive:
    //   Q4 768x432 121f    16 GB Mac   17.41 → 20.35 GB  (0.32.2: 19.35)
    //                      32 GB Mac   25.66 → 33.18 GB  = 97% of physical
    //                      64 GB Mac   25.53 → 54.28 GB  = 2.13x
    //   Q8 1024x576 121f   48 GB Mac   39.49 → 49.95 GB  = 97% of physical
    // On this 64 GB box the Q4 render moved system memory pressure from 58% to
    // 84% and added 1.5 GB of swap; the Q8 one reached 87%. plan_memory_policy()
    // in mlx_ltx_panel.py calls a machine "pressured" at 82% and answers by
    // forcing the streamed VAE decode — ~30 s on a 5 s clip, which is more than
    // the 4.5% the move earns. So 0.32 would buy speed and spend more of it.
    //
    // Ruled out, so nobody chases it: Metal residency sets (new in 0.32) are
    // NOT the cause — MLX_RESIDENCY_SET_MAX_PCT=0 reproduces the same footprint
    // to within 300 KB across three runs.
    //
    // THE CACHE POLICY SHIPPED FIRST, ON THIS PIN, AND IT SETTLED THE QUESTION
    // THE OTHER WAY. mlx_warm_helper.py now caps the MLX allocator cache at one
    // eighth of physical RAM (floor 2 GiB, ceiling 8 GiB). Re-running the tier
    // table with that policy applied to BOTH versions — same prompt/seed,
    // /usr/bin/time -l peak footprint, share of the simulated Mac's RAM:
    //
    //   Q4 768x432 121f    0.31.1 today   0.31.1+policy   0.32.1+policy
    //     16 GiB (cap 2)   17.41 (101%)   16.50 ( 96%)    19.91 (116%)  WORSE
    //     32 GiB (cap 4)   25.66 ( 75%)   16.49 ( 48%)    23.40 ( 68%)
    //     64 GiB (cap 8)   25.53 ( 37%)   16.50 ( 24%)    27.87 ( 41%)  WORSE
    //   Q8 1024x576 121f
    //     48 GiB (cap 6)   39.49 ( 77%)   25.39 ( 49%)    42.11 ( 82%)  WORSE
    //
    // Three of the four tiers end up at a HIGHER share of physical RAM than
    // 0.31.1 uses today, and the 48 GiB Comfortable tier — the commonest
    // paying-attention Mac — lands at 81.7%, which is the 82% at which
    // plan_memory_policy() forces the streamed VAE decode, before Chrome and
    // Slack are counted.
    //
    // WHY THE CAP CANNOT RESCUE IT: the extra footprint is not all cache. With
    // the cache capped identically, 0.32.1's ACTIVE memory is +12.0 GB on Q4
    // (26.57 vs 14.53) and +29.2 GB on Q8 (52.23 vs 23.08). Capping an
    // allocator cache cannot reach that. Driving the cache to ZERO does get
    // 0.32.1 under today's numbers (Q8@48 GiB 32.25 GB / 138.37 s; Q4@16 GiB
    // 17.04 GB / 72.59 s) — and at cache 0 the entire speed advantage is gone
    // (-1.1% and -0.5% vs 0.31.1 today, and slower than 0.31.1 WITH the policy).
    // There is no cache setting where 0.32.1 beats 0.31.1+policy on both axes.
    //
    // So the move is REFUSED again, on the same ground, with the mitigation
    // now tried: step 1 of the route took the win the version move was supposed
    // to deliver, and took it on the safe pin. Every cache setting is
    // sha256-IDENTICAL output within a version, so none of this is a quality
    // question — but the two VERSIONS are not byte-identical with each other
    // (144da74a... vs 016d2dd2...), so a future attempt still owes a look at
    // faces, not just a green suite.
    //
    // And the pin does not move alone. scripts/check_post_update.js carries the
    // measurement: moving mlx to 0.32.x while mflux stays at 0.18.0 makes the
    // step-7 mflux resolve walk mlx back DOWN to 0.31.2 — the broken one — on
    // every fresh install and every Update, silently.
    //
    // 4.17.4: THE COMMANDS LIVE IN scripts/pinokio/ltx_engine_env.sh NOW, in
    // the same order with the same pins (mlx trio + transformers cap, the
    // dependency pass, the --reinstall --no-deps pass that turns the
    // workspace's editable links into real copies, mlx-vlm, the runtime
    // extras - every reason above still applies, line for line). Moved for
    // the fleet's `venv_broken` ("the engine venv has a Python but no
    // packages", 18 installs in 30 days, nearly all at first boot): the script
    // retries a dropped connection up to 3 times, VERIFIES the result the way
    // the panel judges it (scripts/pinokio/engine_env_check.py: real package
    // directories, and an import), and ends with "FATAL error:" when it cannot
    // - so Install stops here, loudly, instead of downloading 37 GB into a
    // venv that cannot render. And it is ONE step the panel's own "Repair
    // engine" button can re-run alone, with no downloads and no venv rebuild.
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/ltx_engine_env.sh"
      }
    },
    {
      method: "shell.run",
      params: {
        message: [
          "bash scripts/pinokio/must.sh 'The video codec patch did not apply - click Install again; if it repeats, report the lines above' ./ltx-2-mlx/env/bin/python3.11 patch_ltx_codec.py"
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        message: [
          "bash scripts/pinokio/must.sh 'The video engine packages cannot be loaded - click Install again to rebuild them' ./ltx-2-mlx/env/bin/python3.11 -c \"import ltx_core_mlx, ltx_pipelines_mlx, mlx\""
        ]
      }
    },
    {
      method: "shell.run",
      params: {
        message: "bash scripts/pinokio/mflux_pack.sh"
      }
    },
    {
      method: "notify",
      params: {
        html: "<b>Phosphene is ready.</b> Click <b>Start</b>, then <b>Open Panel</b>. Choose video, images or songs inside the app. Models download only when you ask for them."
      }
    }
  ]
}
