// webapp/js/settings.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
// ====== Inline models card (top of form) ======
// Displays what the current install needs RIGHT WHERE the user is about
// to act. Beats burying the download CTA in a header modal. State picked
// from /status: base missing → first-download offer, current-mode-needs-Q8 →
// amber prompt, downloading → animated progress, all-good → hidden.
function updateModelsCard(s) {
  const card     = document.getElementById('modelsInline');
  const icon     = document.getElementById('modelsInlineIcon');
  const title    = document.getElementById('modelsInlineTitle');
  const sub      = document.getElementById('modelsInlineSub');
  const progress = document.getElementById('modelsInlineProgress');
  const fill     = document.getElementById('modelsInlineFill');
  const last     = document.getElementById('modelsInlineLast');
  const actions  = document.getElementById('modelsInlineActions');
  if (!card) return;
  // Mac app: its own card hides this one while it owns the model download
  // (a <style> it injects, syncStudioModelCard). When that download FAILED its
  // card is a dead end (app 5.0.10: "Set up the studio first.", and Resume
  // fails the same way), so this card comes back and the Studio downloads the
  // video model itself (app_models_rescue_step, server side).
  const appFailed = !!(s.app_models && s.app_models.state === 'failed' && !s.app_models.active);
  const appHide = document.getElementById('phosphene-native-model-card');
  if (appFailed && appHide && appHide.textContent) appHide.textContent = '';
  const setActions = html => {
    if (actions._html === html) return;
    const focused = actions.contains(document.activeElement);
    actions._html = html;
    actions.innerHTML = html;
    if (focused) actions.querySelector('button')?.focus();
  };

  const baseOk = !!s.base_available;
  const q8Ok   = !!s.q8_available;
  const dl     = s.download && s.download.active ? s.download : null;
  const tier   = s.tier || {};
  const dismissed = !!(s.settings && s.settings.models_card_dismissed);

  // Reset state classes — we set the right one below.
  card.classList.remove('state-missing', 'state-warn', 'state-downloading', 'dismissible', 'is-ready');
  progress.style.display = 'none';

  if (s.model_path_error) {
    card.style.display = '';
    card.classList.add('state-warn');
    icon.textContent = '!';
    title.textContent = T('settings.model_folder_needs_attention', 'Model folder needs attention');
    sub.textContent = s.model_path_error;
    setActions('<button class="ghost" onclick="openModelsModal()">Check models</button>');
    return;
  }

  // ----- A Pinokio-side H3 build/install is running RIGHT NOW --------------
  // H3-19: install/build only ever runs as a Pinokio script — there is no
  // real in-panel trigger for the ~75 GB clone+venv+download (unlike the
  // small Fast/Turbo adapter installs, which DO run in-process). But the
  // worker already reads scripts/pinokio/h3_build_q8.sh's lock file to HOLD
  // the render queue while that script runs (_external_build_hold,
  // mlx_ltx_panel.py) — it just never told the UI why. Surface it as its
  // own precedence-topping state: not an in-panel installer, but an
  // in-panel PROGRESS SIGNAL, so a user who started the build from the
  // Pinokio sidebar doesn't have to keep that tab open to know it's working.
  const extBuild = s.h3 && s.h3.external_build;
  if (extBuild && extBuild.active) {
    card.style.display = '';
    card.classList.add('state-downloading');
    icon.textContent = '↓';
    // The Mac app runs this itself: no Pinokio, no sidebar, and (for its own
    // H3 runner update, hold scope "h3") other clips keep rendering meanwhile.
    const hold = s.hold || {};
    const what = extBuild.what || 'an H3 engine';
    title.textContent = distText(`Building ${what} in Pinokio`,
      hold.scope === 'h3' ? T('settings.updating_the_hailuo_h3_engine', 'Updating the Hailuo H3 engine') : `Building ${what}`);
    sub.textContent = distText(
      T('settings.running_in_the_pinokio_sidebar', 'Running in the Pinokio sidebar — Generate resumes here automatically when it finishes. No need to keep that tab open.'),
      hold.scope === 'h3'
        ? (hold.text || 'H3 clips wait; other clips keep going.')
        : T('settings.phosphene_is_doing_this_by', 'Phosphene is doing this by itself. Generate resumes here automatically when it finishes.'));
    setActions('');
    return;
  }

  // Requested video packs share one queue and one progress display.
  // Stop keeps partial files and clears every remaining pack in that request.
  const bgq = s.deferred_downloads || {};
  if (dl && dl.background) {
    card.style.display = '';
    card.classList.add('state-downloading');
    icon.textContent = '↓';
    const rest = Math.max(0, (bgq.pending || []).length - 1);
    title.textContent = T('settings.downloading_your_video_model', 'Downloading your video model');
    sub.textContent = `Downloading ${dl.name || dl.key}${dl.size_gb ? ` (~${dl.size_gb} GB)` : ''}`
      + (rest ? ` · ${rest} more after this (${bgq.left_gb} GB left in all)` : '')
      + T('settings.resumes_if_interrupted', ' · resumes if interrupted');
    progress.style.display = '';
    const m = (dl.last_line || '').match(/\b(\d{1,3})%/);
    fill.style.width = dl.bytes_total ? `${Math.min(99, 100 * dl.bytes_done / dl.bytes_total)}%` : m ? `${Math.min(99, parseInt(m[1]))}%` : '0%';
    last.textContent = dl.bytes_total ? `${(dl.bytes_done / 1e9).toFixed(2)} of about ${(dl.bytes_total / 1e9).toFixed(2)} GB · ${dl.last_line || 'Connecting'} ` : dl.last_line || 'Connecting';
    setActions(`<button class="ghost" onclick="cancelDownload()">${T('settings.stop_download', 'Stop download')}</button>`);
    return;
  }
  if (!dl && bgq.active && bgq.message) {
    card.style.display = '';
    card.classList.add('state-warn');
    icon.innerHTML = '<svg class="ph" aria-hidden="true"><use href="#ph-download-simple"/></svg>';
    const failed = bgq.state === 'retry_wait' || bgq.state === 'failed';
    title.textContent = bgq.state === 'waiting_disk' ? T('settings.download_paused_more_disk_space', 'Download paused: more disk space needed') : failed ? T('settings.download_needs_another_try', 'Download needs another try') : T('settings.download_waiting', 'Download waiting');
    sub.textContent = bgq.message;
    setActions((failed ? `<button onclick="downloadFirstVideo()">${T('settings.try_again', 'Try again')}</button>` : '')
      + `<button class="ghost" onclick="cancelDownload()">${T('settings.stop_download', 'Stop download')}</button><button class="ghost" onclick="openModelsModal()">${T('settings.models', 'Models')}</button>`);
    return;
  }

  // ----- Active download takes precedence over everything ------------------
  if (dl) {
    card.style.display = '';
    card.classList.add('state-downloading');
    icon.textContent = '↓';
    const labelByKey = { q4: T('settings.q4_base_model', 'Q4 base model'), gemma: T('settings.gemma_text_encoder', 'Gemma text encoder'), q8: T('settings.q8_high_quality_model', 'Q8 high-quality model') };
    title.textContent = `Downloading ${labelByKey[dl.key] || dl.repo_id}`;
    const elapsed = Math.max(0, Math.round((Date.now()/1000) - (dl.started_ts || 0)));
    sub.textContent = `${elapsed}s elapsed · resumable if interrupted`;
    progress.style.display = '';
    // Try to extract a percent from the last hf line (tqdm format).
    const m = (dl.last_line || '').match(/\b(\d{1,3})%/);
    fill.style.width = dl.bytes_total ? `${Math.min(99, 100 * dl.bytes_done / dl.bytes_total)}%` : m ? `${Math.min(99, parseInt(m[1]))}%` : '0%';
    last.textContent = dl.bytes_total ? `${(dl.bytes_done / 1e9).toFixed(2)} of about ${(dl.bytes_total / 1e9).toFixed(2)} GB · ${dl.last_line || 'Connecting'} ` : dl.last_line || 'Connecting';
    setActions(`<button class="danger" onclick="cancelDownload()">${T('settings.cancel', 'Cancel')}</button>`);
    return;
  }

  // Image and song readiness belong to their own forms. Falling through to
  // the video card's quiet state would falsely say all weights are installed.
  if (currentMode === 'image' || document.body.dataset.workflow === 'audio') {
    card.style.display = 'none';
    setActions('');
    return;
  }

  // ----- Offer the active video model on a first run ----------------------
  if (!baseOk && currentMode !== 'image' && document.body.dataset.engine !== 'h3' && document.body.dataset.workflow !== 'audio') {
    card.style.display = '';
    card.classList.add('state-warn');
    icon.innerHTML = '<svg class="ph" aria-hidden="true"><use href="#ph-warning-fill"/></svg>';
    title.textContent = T('settings.get_the_video_model', 'Get the video model');
    // Mac app: the app is fetching the base set itself (app_models.json).
    // Say so and offer no second Download - two fetchers on one pack is not
    // safe, and the server refuses it anyway. Absent under Pinokio.
    const appDl = s.app_models;
    if (appDl && appDl.active) {
      const gb = (b) => (Number(b || 0) / 1e9).toFixed(1);
      const amount = appDl.total_bytes ? `${gb(appDl.done_bytes)} of ${gb(appDl.total_bytes)} GB` : '';
      sub.textContent = appDl.state === 'paused'
        ? T('settings.download_paused', 'Download paused')
        : T('settings.base_models_are_downloading_you', 'Base models are downloading{v}. You can render when they’re ready.', {v: amount ? ` (${amount})` : ''});
      if (appDl.total_bytes) {
        progress.style.display = '';
        fill.style.width = `${Math.min(100, Math.round(100 * appDl.done_bytes / appDl.total_bytes))}%`;
        last.textContent = '';
      }
      setActions('');
      return;
    }
    // The Studio tried to take over the app's stopped download and could not
    // (disk space, a missing drive): say why, keep the button.
    const rescue = s.app_models_rescue;
    if (rescue && rescue.state === 'refused' && rescue.error) {
      sub.textContent = rescue.error;
      setActions(`<button onclick="downloadFirstVideo()">${T('settings.try_again', 'Try again')}</button>`
        + `<button class="ghost" onclick="openModelsModal()">${T('settings.all_models', 'All models')}</button>`);
      return;
    }
    const missingList = s.base_missing || [];
    // REGISTRY-DRIVEN, for the ACTIVE generation. `base_missing` is already
    // version-scoped, so offering `q4` here downloaded 2.3's base to fix a
    // broken 2.5 install: 20 GB spent, still broken.
    const P = ((BOOT.ltx || {}).packs) || {};
    const pBase = P.base, pEnc = P.encoder;
    // SYS-11: this used to say "X AND Y are required" unconditionally,
    // even when only one pack (say, just the base transformer) was
    // actually missing a file and the other (Gemma) was fully present —
    // a half-installed 16 GB install saw "LTX-2.5 base and Gemma 4 are
    // required" with Gemma 4 sitting right there on disk. Name only the
    // pack(s) whose local_dir actually shows up in base_missing.
    // Matched on the pack's own folder name: base_missing is shown in the
    // person's folder (`models/<pack>/...` in the Mac app), the registry's
    // local_dir is always `mlx_models/<pack>`.
    const packNeeded = (p) => !p || !p.local_dir
      || missingList.some(f => String(f).includes('/' + String(p.local_dir).split('/').pop() + '/')
                               || String(f).includes(p.local_dir));
    const needBase = packNeeded(pBase), needEnc = packNeeded(pEnc);
    const parts = [];
    if (needBase) parts.push(`${pBase ? pBase.name : T('settings.the_base_model', 'The base model')} (~${pBase ? pBase.size : '?'})`);
    if (needEnc) parts.push(`${pEnc ? pEnc.name : T('settings.its_text_encoder', 'its text encoder')} (~${pEnc ? pEnc.size : '?'})`);
    sub.textContent = T('settings.recommended_for_your_first_video', 'Recommended for your first video. ') + parts.join(' and ')
      + T('settings.download_once_then_make_videos', '. Download once, then make videos locally. Images and songs have their own models.');
    setActions(`<button onclick="downloadFirstVideo()">${T('settings.download_video_models', 'Download video models')}</button>`
      + `<button class="ghost" onclick="setMode('image')">${T('settings.try_images', 'Try images')}</button>`
      + `<button class="ghost" onclick="openModelsModal()">${T('settings.all_models', 'All models')}</button>`);
    return;
  }

  // ----- User picked a mode that needs Q8, but Q8 isn't there --------------
  // FFLF + Extend + High quality all need Q8. Surface the CTA *only* when
  // the user is about to do one of those — no point nagging a T2V user
  // about Q8 if they'll never use it.
  // Dismissible: a user who deliberately doesn't want Q8 (storage budget,
  // they only do T2V Quick/Standard) can × this away and we'll respect it
  // until either model state changes or they re-summon the modal.
  // Y1.036 — Extend joins FFLF and High in needing Q8. The Extend pipeline
  // loads `transformer-dev.safetensors` for CFG-guided denoise; Q4 doesn't
  // ship it after the Y1.024 download trim, so surface the same CTA here.
  const _onH3 = document.body.dataset.engine === 'h3';
  const needsQ8 = (!_onH3 && (currentMode === 'keyframe' || currentMode === 'extend'))
                || (!_onH3 && _qualityUsesHq(document.getElementById('quality').value));
  if (needsQ8 && !q8Ok && tier.allows_q8 !== false) {
    if (dismissed) { card.style.display = 'none'; return; }
    card.style.display = '';
    card.classList.add('state-warn', 'dismissible');
    icon.innerHTML = '<svg class="ph" aria-hidden="true"><use href="#ph-download-simple"/></svg>';
    // The title has to name the SAME download the body and the button do.
    // "High quality needs the Q8 model" over a button reading "Download
    // LTX-2.5 High add-on" is one card telling two stories.
    const P0 = ((BOOT.ltx || {}).packs) || {};
    const q8PackOk0 = (s.q8_pack_available !== undefined) ? s.q8_pack_available : q8Ok;
    const wantName = ((q8PackOk0 && P0.hq_addon) ? P0.hq_addon : P0.q8 || {}).name || 'the Q8 model';
    const feature = currentMode === 'keyframe' ? T('settings.keyframes', 'Keyframes')
                  : currentMode === 'extend'   ? T('settings.extend', 'Extend')
                                               : T('settings.high_quality', 'High quality');
    title.textContent = `${feature} needs ${wantName}`;
    // REGISTRY-DRIVEN. This card offered `startDownload('q8')` — 2.3's pack —
    // and advertised 37 GB, on a build where High needs 2.5's 30.02 GB Q8 pack
    // PLUS a separate 29.50 GB add-on. The button worked, which made it worse
    // than a broken one.
    const P = ((BOOT.ltx || {}).packs) || {};
    const pQ8 = P.q8, pHq = P.hq_addon;
    // High/Extend/Keyframe need the add-on too, when this generation has one.
    // Offer the pack that is actually missing rather than the one whose name
    // used to be hardcoded here.
    const q8PackOk = (s.q8_pack_available !== undefined) ? s.q8_pack_available : q8Ok;
    const want = (q8PackOk && pHq) ? pHq : pQ8;
    if (!want) { card.style.display = 'none'; return; }
    const missing = ((q8PackOk ? s.hq_addon_missing : s.q8_pack_missing) || []).length;
    sub.innerHTML = escapeHtml(
        `${want.name} (~${want.size}) is a separate one-time download. Resumable.`)
      + (pHq && !q8PackOk
          ? ` <span style="color:var(--muted)">${T('settings.the_high_tier_additionally_needs', 'The High tier additionally needs the {v} (~{v2}).', {v: escapeHtml(pHq.name), v2: escapeHtml(pHq.size)})}</span>` : '')
      + (missing && missing < 8
          ? ` <span style="color:var(--muted)">(${missing} files left — partial install detected)</span>` : '');
    const wantNeedsHf = (want.needs_hf !== false);
    setActions(((s.hf_available ?? true) || !wantNeedsHf)
      ? `<button onclick="startDownload('${escapeHtml(want.key)}')">Download ${escapeHtml(want.name)} (${escapeHtml(want.size)})</button>`
      : `<button disabled>${T('settings.hf_missing', 'hf missing')}</button>`);
    return;
  }

  // ----- Hailuo H3 installed but broken → one-click repair -----------------
  // `repairable` means the ~75 GB of H3 weights ARE on disk and only the
  // clone/venv needs rebuilding. The overwhelmingly common cause: H3's venv
  // interpreter is a symlink chain into Pinokio's SHARED uv-managed Python,
  // and installing any other pack can move that target out from under it —
  // the v3.4.0 "installed other packs and H3 vanished" report. Before this
  // branch the panel just silently demoted the engine pill to "not
  // installed", which reads as data loss and sent users to Reset (which does
  // not touch H3 at all, so it never helped).
  //
  // Gated on `repairable`, so a user who never installed H3 is never nagged.
  const h3s = s.h3 || {};
  // ----- Mac app: Hailuo H3 is never a repair job for the person -------------
  // The app sets H3 up by itself when its weights are on this Mac (bootstrap.sh
  // h3), so a Mac with weights and no runtime yet (`preparing`), or one that is
  // a build away on a smaller Mac, shows no card at all. Nothing to click.
  if (isMacApp() && (h3s.preparing || h3s.needs_q8_dit)) {
    card.style.display = 'none';
    setActions('');
    return;
  }
  // ----- 46-60 GB Mac whose reduced-RAM engine was never built -------------
  // Its own branch because the repair copy below is wrong here: nothing is
  // missing or broken, the machine simply needs the Q8 engine that
  // scripts/pinokio/h3_build_q8.sh produces. Before the `needs_q8_dit` band
  // existed this Mac was `capable: false` and got no card, no switcher
  // segment, and — if it ever reached a render — a refusal claiming it needed
  // 64 GB. The server owns the sentence; this only places it.
  //
  // Two different Macs share this band. `missing_q8_dit` holds all ~75 GB and
  // lacks only the local build: a warning, it installed H3 and cannot use it.
  // Anything else never installed H3 at all — an offer, not a fault, and "its
  // low-RAM engine isn't built yet" misread as "your Mac is not enough" beside
  // a size note that said 64 GB (X, 2026-09-24).
  if (h3s.needs_q8_dit) {
    // H3-20: this branch never added 'dismissible' (the × only renders on
    // that class — panel.css `.models-inline.dismissible .models-inline-
    // dismiss`), so the banner was permanent on every 36-59 GB Mac. It also
    // never checked `dismissed`, so a user who did dismiss it (nothing else
    // sets the flag differently) would have kept seeing it anyway. And the
    // body repeated the title almost verbatim: the title said "Hailuo H3
    // runs on this Mac..." and h3s.ram_note (the FULL server sentence, built
    // for the install-card modal) starts with "Hailuo H3 runs on this Mac —
    // on its reduced-RAM lane..." — two sentences that opened the same way,
    // wrapping to ~270px at 1440x900 and pushing Generate under the Now
    // card. One short line here; the full explanation lives one click away
    // in the install card, which already reads h3s.ram_note itself.
    if (dismissed) { card.style.display = 'none'; return; }
    const built = (h3s.reason === 'missing_q8_dit');
    card.style.display = '';
    card.classList.add('dismissible');
    if (built) card.classList.add('state-warn');
    icon.innerHTML = built
      ? '<svg class="ph" aria-hidden="true"><use href="#ph-warning-fill"/></svg>'
      : '<svg class="ph" aria-hidden="true"><use href="#ph-download-simple"/></svg>';
    title.textContent = T('settings.hailuo_h3_can_run_on', 'Hailuo H3 can run on this Mac');
    sub.textContent = built
      ? T('settings.its_compact_engine_just_needs', 'Its compact engine just needs building — no download.')
      : T('settings.needs_its_compact_q8_engine', 'Needs its compact (Q8) engine, built during install.');
    setActions(`<button onclick="openH3InstallCard()">${T('settings.set_up_h3', 'Set up H3 →')}</button>`);
    return;
  }
  if (h3s.capable && !h3s.available && h3s.repairable) {
    card.style.display = '';
    card.classList.add('state-warn');
    icon.innerHTML = '<svg class="ph" aria-hidden="true"><use href="#ph-warning-fill"/></svg>';
    title.textContent = T('settings.hailuo_h3_needs_repair_your', 'Hailuo H3 needs repair — your weights are still on disk');
    // Same missing-list-first logic as the repair modal (issue #68): the
    // two-way venv_broken guess blamed the checkout whenever the venv was
    // simply never built.
    const _miss = (h3s.missing || []).map(String);
    sub.innerHTML = h3s.venv_broken
      ? T('settings.h3_s_python_environment_points', 'H3’s Python environment points at a moved or deleted interpreter. ')
        + 'Rebuilding takes ~2 minutes and <b>re-downloads nothing</b>.'
      : _miss.some(m => m.includes('venv'))
      ? T('settings.h3_s_python_environment_was', 'H3’s Python environment was never built. Building it takes ~2 ')
        + 'minutes and <b>re-downloads nothing</b>.'
      : _miss.some(m => m.includes('runner') || m.includes('scripts/'))
      ? T('settings.h3_s_code_checkout_is', 'H3’s code checkout is missing or incomplete. Restoring it takes ~1 ')
        + 'minute and <b>re-downloads nothing</b>.'
      : T('settings.missing', 'Missing: ') + escapeHtml(_miss[0] || 'see the repair card') + '.';
    setActions(`<button onclick="openH3InstallCard()">${T('settings.how_to_repair_h3', 'How to repair H3')}</button>`);
    return;
  }

  // ----- All good — hide the card completely -------------------------------
  // Per user feedback: the "Models ready · 3/3" status was visual noise once
  // everything was downloaded. Hide the card on full readiness; the header
  // models pill still gives a way to reopen the modal if the user wants to
  // manage repos. If state regresses (a file gets deleted, partial download
  // appears), one of the branches above re-shows it automatically.
  const allReady = baseOk && q8Ok;
  if (allReady) {
    card.style.display = 'none';
    setActions('');
    return;
  }
  // ----- Partial-OK quiet state ---------------------------------------------
  // Base OK but Q8 missing on a tier that supports it AND the user hasn't
  // picked a Q8-needing mode — gentle nudge in neutral colours, dismissible.
  if (dismissed) { card.style.display = 'none'; return; }
  card.style.display = '';
  // `is-ready`: nothing is wrong here, so it is ONE quiet line under the Model
  // picker, never a card that competes with it (owner, 5 Nov 2026: on rc.2 this
  // card sat above "Model" and pushed it down). The × stays and the dismissal
  // persists (settings.models_card_dismissed), so once closed it stays closed.
  card.classList.add('dismissible', 'is-ready');
  icon.innerHTML = '<svg class="ph" aria-hidden="true"><use href="#ph-check-bold"/></svg>';
  // SYS-17: "Models ready · 3/12" read as "25% ready" -- the denominator
  // counts every optional add-on, most of which nobody needs. Also
  // dropped the inline "Manage models ->" link here: the card's own
  // static "Manage all models ->" footer link (index.html) said the
  // identical thing right below, so both showed together every time
  // this state rendered.
  const ready = s.repos_ready ?? 0;
  const total = s.repos_total ?? 0;
  const addons = Math.max(0, total - ready);
  title.textContent = addons > 0
    ? TP('settings.ready_to_render_add_on', addons, {one: 'Ready to render ✓ · {count} add-on available', other: 'Ready to render ✓ · {count} add-ons available'})
    : T('settings.ready_to_render', 'Ready to render ✓');
  sub.textContent = T('settings.all_the_weights_this_mac', 'All the weights this Mac renders with are installed.');
  setActions('');
}

// Persist the "user dismissed the models card" flag. POSTs to /settings
// and re-runs updateModelsCard with the latest status so the card hides
// immediately (not after the next /status poll cycle, ~5s away).
async function dismissModelsCard() {
  try {
    const fd = new URLSearchParams();
    fd.set('models_card_dismissed', 'true');
    await fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: fd,
    });
  } catch (e) { /* best effort — UI still hides locally on next poll */ }
  // Optimistically hide right now without waiting for the poll round-trip.
  const card = document.getElementById('modelsInline');
  if (card) card.style.display = 'none';
  // Patch LAST_STATUS so subsequent updateModelsCard calls before the next
  // /status fetch agree with the on-disk state.
  if (LAST_STATUS && LAST_STATUS.settings) {
    LAST_STATUS.settings.models_card_dismissed = true;
  }
}

// ====== Tier gating ======
// Disables the FFLF / Extend mode pills and the High quality pill when
// the detected hardware tier doesn't support them. Visual state +
// tooltip + intercepted clicks. Run from the poll handler so an env
// override flips state on restart.
function applyTierGates(tier) {
  // Mode pills
  document.querySelectorAll('#modeGroup .pill-btn').forEach(b => {
    const m = b.dataset.mode;
    // Hailuo H3 serves Keyframes and Extend on its own engine and lane.
    const _h3 = document.body.dataset.engine === 'h3';
    const allowed = (m === 'keyframe') ? (_h3 || tier.allows_keyframe)
                  : (m === 'extend')   ? (_h3 || tier.allows_extend)
                  : true;
    b.classList.toggle('disabled', !allowed);
    if (!allowed) {
      const need = m === 'keyframe'
        ? T('settings.first_last_frame_interpolation_needs', 'first/last-frame interpolation needs more memory than this Mac has — try Image → Video instead')
        : T('settings.extending_an_existing_clip_needs', 'extending an existing clip needs more memory than this Mac has — try Image → Video instead');
      b.title = T('settings.off_on_the_tier', 'Off on the {label} tier · {need}', {label: tier.label, need});
    } else {
      b.title = '';
    }
  });
  // Quality: High requires Q8. We already disable based on q8_available
  // for the no-download case; this layer enforces the RAM tier on top.
  // Both layers can disable — we OR them together via a class.
  const highBtn = document.getElementById('qualityHigh');
  if (highBtn) {
    if (!tier.allows_q8) {
      highBtn.classList.add('disabled');
      highBtn.title = T('settings.off_on_the_tier_the', 'Off on the {label} tier · the high-quality model needs more memory than this Mac has', {label: tier.label});
    } else {
      // Don't unconditionally clear .disabled — the Q8-not-installed code
      // path also sets it. Only clear if the tier is the only reason.
      // The poll() code that checks q8_available re-applies that state
      // every cycle, so this branch is safe to unset.
      highBtn.title = '';
    }
  }
}
// Intercept clicks on disabled mode pills so users get a helpful message
// instead of a broken-feeling no-op.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('#modeGroup .pill-btn.disabled');
  if (btn) {
    e.stopPropagation();
    e.preventDefault();
    alert(btn.title || 'This mode is not supported on this hardware tier.');
  }
}, true);

// ====== Tier modal ======
function openTierModal() {
  // SYS-19: the health popover's Tier row opened this modal UNDERNEATH the
  // still-open popover. Close it first.
  if (typeof closeHealthPop === 'function') closeHealthPop();
  const modal = document.getElementById('tierModal');
  modal.style.display = 'flex';
  // Defensive: show "loading" state immediately so the modal never appears
  // with the body completely blank (which is what happens if the panel
  // process is dead and fetch fails — looked like a "buggy bug" to a user
  // who was just kicked off a stale browser view).
  document.getElementById('tierModalTitle').textContent = T('settings.hardware_tier', 'Hardware tier');
  document.getElementById('tierModalBlurb').innerHTML = '<em>Loading…</em>';
  document.getElementById('tierCapsList').innerHTML = '';
  // Tier doesn't change at runtime — RAM is fixed at boot — so a single
  // fetch on open is plenty. No need for live polling here.
  fetch('/status').then(r => r.json()).then(s => {
    const t = s.tier || {};
    const tt = t.times || {};
    // Helper: a row is "available" if it's allowed; "max" is the friendly
    // size limit (or "Any size" / "—" when there is no limit / disabled).
    const sizeLine = (on, maxDim, fallback) => {
      if (!on) return fallback || 'Not available on this Mac';
      if (!maxDim) return T('settings.any_size', 'Any size');
      return `Up to ${maxDim} pixels on the longer side`;
    };
    document.getElementById('tierModalTitle').textContent = T('settings.what_this_mac_can_do', 'What this Mac can do · {v}', {v: t.label || 'unknown'});
    document.getElementById('tierModalBlurb').innerHTML = `
      <div style="margin-bottom: 6px"><strong>${escapeHtml(t.label || '')}</strong> · ${escapeHtml(t.ram_label || '')} of memory</div>
      <div>${escapeHtml(t.blurb || '')}</div>`;
    // One row per mode/option, with three pieces of info each:
    //   - is it available? (✓ / ✗)
    //   - what's the size limit? (plain English)
    //   - how long does a typical 5-second render take? (rough estimate)
    const items = [
      {
        title: T('settings.text_video', 'Text → video'),
        desc: T('settings.type_a_prompt_get_a', 'Type a prompt, get a clip. The default mode.'),
        on: true,
        size: sizeLine(true, t.t2v_max_dim),
        time: tt.t2v_standard,
      },
      {
        title: T('settings.image_video', 'Image → video'),
        desc: T('settings.drop_in_a_still_get', 'Drop in a still, get it animated. Same speed as text → video.'),
        on: true,
        size: sizeLine(true, t.i2v_max_dim),
        time: tt.i2v_standard,
      },
      {
        title: T('settings.quick_640_448', 'Quick (640×448)'),
        desc: T('settings.smaller_preview_to_scout_prompts', 'Smaller preview to scout prompts and seeds before a full-size render.'),
        on: true,
        size: T('settings.always_smaller_than_standard', 'Always smaller than Standard'),
        time: tt.t2v_draft,
      },
      {
        title: T('settings.high_quality', 'High quality'),
        desc: T('settings.bigger_model_two_stage_denoising', 'Bigger model, two-stage denoising, sharper faces. Needs the optional Q8 download.'),
        on: !!t.allows_q8,
        size: sizeLine(!!t.allows_q8, 0, T('settings.needs_more_memory_than_this', 'Needs more memory than this Mac has')),
        time: tt.high,
      },
      {
        title: T('settings.first_last_frame_fflf', 'First / last frame (FFLF)'),
        desc: T('settings.pick_a_start_image_and', 'Pick a start image and an end image, the model fills the motion between.'),
        on: !!t.allows_keyframe,
        size: sizeLine(!!t.allows_keyframe, t.keyframe_max_dim, T('settings.needs_more_memory_than_this', 'Needs more memory than this Mac has')),
        time: tt.keyframe,
      },
      {
        title: T('settings.extend_an_existing_clip', 'Extend an existing clip'),
        desc: T('settings.pick_a_video_you_already', 'Pick a video you already rendered, the model adds more time onto either end.'),
        on: !!t.allows_extend,
        size: sizeLine(!!t.allows_extend, t.extend_max_dim, T('settings.needs_more_memory_than_this', 'Needs more memory than this Mac has')),
        time: tt.extend,
      },
    ];
    document.getElementById('tierCapsList').innerHTML = items.map(it => `
      <li class="${it.on ? 'ready' : 'missing'}">
        <span class="icon"><svg class="ph" aria-hidden="true"><use href="${it.on ? '#ph-check-bold' : '#ph-x-circle-fill'}"/></svg></span>
        <div class="meta">
          <span class="ttl">${escapeHtml(it.title)}</span>
          <span class="sub">${escapeHtml(it.desc)}</span>
          <span class="sub" style="margin-top:2px">
            <span style="color:var(--fg,#d8e0ee)">${escapeHtml(it.size)}</span>${
              it.time ? ` · <span style="color:var(--accent-bright,#7e98ff)">~ ${escapeHtml(it.time)} for a 5-second clip</span>` : ''
            }
          </span>
        </div>
        <span></span>
      </li>`).join('');
  }).catch(err => {
    // Panel might be dead, status endpoint unreachable, or response not JSON.
    // Replace the loading state with a visible error so the modal doesn't
    // look "broken" with empty content.
    document.getElementById('tierModalBlurb').innerHTML =
      '<div style="color: var(--danger, #f85149)">' + distText(
        T('settings.could_not_load_tier_info', 'Could not load tier info — the panel server may have stopped responding. Check the Pinokio terminal and restart the panel if needed.'),
        T('settings.could_not_load_tier_info_2', 'Could not load tier info — the studio server may have stopped responding. Check the logs with Help \u2192 Open Logs Folder and use Studio \u2192 Restart Engine if needed.')) + '</div>';
    document.getElementById('tierCapsList').innerHTML = '';
    console.error('tier modal fetch failed:', err);
  });
}
function closeTierModal() { document.getElementById('tierModal').style.display = 'none'; }

// ====== Bug report modal ======
// Fetches env + log tail from /panel/bug-context on open, lets the user
// type into pre-marked sections, then on submit POSTs the title+body to
// /panel/bug-report (which builds the GitHub URL + optional crash zip)
// and opens the URL in a new tab. Stays purely in the panel — no auth,
// no API hit, the user reviews the issue body in GitHub before posting.
let _bugCtxCache = null;
// 4.19.3 (#106): the report must say what went wrong. Issue #106 arrived
// with "What happened" and "Expected" empty and one unrelated log line —
// nothing to act on. The required box above the details is checked here,
// with inline copy (never alert()), and the server checks it again.
const BUG_WHAT_MIN = 10;
const BUG_WHAT_HINT = T('settings.a_sentence_or_two_in', "A sentence or two in your own words. The log below is ")
  + T('settings.attached_for_you_but_it', "attached for you, but it can't tell us what you saw.");
const BUG_WHAT_SHORT = T('settings.please_tell_us_what_went', 'Please tell us what went wrong in a sentence or two ')
  + T('settings.at_least', '(at least ') + BUG_WHAT_MIN + T('settings.characters_e_g_the_render', ' characters), e.g. "The render stopped at ')
  + T('settings.40_and_the_card_says', '40% and the card says helper failed." Without it we can\'t tell what to fix.');
// The person's own text, line breaks kept; any line that starts with "#"
// is escaped so it can't become a heading that ends the section early
// (Codex, 4.19.3).
function bugWhatText() {
  const el = document.getElementById('bugWhat');
  const raw = el ? String(el.value || '').replace(/\r\n?/g, '\n').trim() : '';
  return raw.replace(/^([ \t]*)#/gm, '$1\\#');
}
// Counted with whitespace collapsed, the same way the server counts.
function bugWhatOk() {
  const el = document.getElementById('bugWhat');
  return (el ? String(el.value || '') : '').replace(/\s+/g, ' ').trim().length >= BUG_WHAT_MIN;
}
function bugWhatShow(invalid) {
  const el = document.getElementById('bugWhat');
  const hint = document.getElementById('bugWhatHint');
  const wrap = el && el.closest('.bug-what');
  if (wrap) wrap.classList.toggle('is-invalid', !!invalid);
  if (el) el.setAttribute('aria-invalid', invalid ? 'true' : 'false');
  if (hint) hint.textContent = invalid ? BUG_WHAT_SHORT : BUG_WHAT_HINT;
}
// Clears the warning as soon as the text is long enough; never shows it
// while someone is still typing their first words.
function bugWhatInput() {
  const wrap = document.getElementById('bugWhat')?.closest('.bug-what');
  if (wrap && wrap.classList.contains('is-invalid') && bugWhatOk()) bugWhatShow(false);
}
// Put the box's text under "## What happened" in the issue body: into an
// existing heading if the person kept one, else before "## Expected" /
// "## Logs", else at the end.
function bugComposeBody(body, what) {
  if (!what) return body;
  const head = /^##[ \t]*What happened[ \t]*$/mi;
  const m = head.exec(body);
  if (m) {
    const at = m.index + m[0].length;
    return body.slice(0, at) + '\n\n' + what + '\n' + body.slice(at);
  }
  const sec = '## What happened\n\n' + what + '\n\n';
  for (const re of [/^##[ \t]*Expected/mi, /^##[ \t]*Logs/mi]) {
    const k = body.search(re);
    if (k >= 0) return body.slice(0, k) + sec + body.slice(k);
  }
  return body.replace(/\s*$/, '\n\n') + sec;
}
// "[bug] " alone is no title: use the first line of what happened.
function bugComposeTitle(title, what) {
  const t = (title || '').trim();
  if (t && !/^\[bug\]$/i.test(t)) return t;
  const line = String(what || '').split('\n').map(x => x.replace(/^[\s\\#]+/, '').trim())
    .find(Boolean) || '';
  if (!line) return T('settings.bug_untitled', '[bug] (untitled)');
  const first = line.length > 72 ? line.slice(0, 71).replace(/\s+\S*$/, '') + '…' : line;
  return '[bug] ' + first;
}
async function openBugModal() {
  const modal = document.getElementById('bugModal');
  modal.style.display = 'flex';
  const titleEl = document.getElementById('bugTitle');
  const bodyEl = document.getElementById('bugBody');
  const crashRow = document.getElementById('bugCrashRow');
  const crashLabel = document.getElementById('bugCrashLabel');
  const statusEl = document.getElementById('bugStatus');
  const submitBtn = document.getElementById('bugSubmitBtn');
  // Clear stale state in case the user opens twice in a row.
  titleEl.value = '[bug] ';
  const whatEl = document.getElementById('bugWhat');
  if (whatEl) whatEl.value = '';
  bugWhatShow(false);
  bodyEl.value = 'Loading environment…';
  statusEl.textContent = '';
  submitBtn.disabled = false;
  submitBtn.textContent = T('settings.open_github_issue', 'Open GitHub issue');
  // A finished report rewires this button to "Done — close"; without this
  // the NEXT report's "Open GitHub issue" just closed the dialog.
  submitBtn.onclick = submitBugReport;
  try {
    const r = await fetch('/panel/bug-context');
    if (!r.ok) throw new Error('bug-context: ' + r.status);
    _bugCtxCache = await r.json();
  } catch (err) {
    bodyEl.value = '## Environment\n- (server context unavailable: ' + err + ')\n\n'
      + '## What I was doing\n\n\n## Expected\n\n';
    setTimeout(() => document.getElementById('bugWhat')?.focus(), 50);
    crashRow.style.display = 'none';
    return;
  }
  const c = _bugCtxCache;
  const tail = (c.logTail || []).join('\n') || '(no recent log lines)';
  bodyEl.value =
`## Environment
- Phosphene version: ${c.version}${c.commit ? ' (' + c.commit + ')' : ''}
- Branch: ${c.branch}
- macOS: ${c.macOS || 'unknown'}
- Mac: ${c.hwModel || 'unknown'} · ${c.ramGB || '?'} GB RAM

## What I was doing


## Expected


## Logs / repro
\`\`\`
${tail}
\`\`\`
`;
  // Crash-report checkbox only when there's something to bundle.
  // SYS-12: default OFF, per report (was pre-ticked). Zipping a crash log
  // into a public GitHub issue should be something the reporter chooses
  // each time, not a box that was already checked when they opened this.
  if ((c.crashCount || 0) > 0) {
    crashRow.style.display = 'flex';
    crashLabel.textContent =
      T('settings.include_latest_crash_reports_zips', 'Include latest crash reports (zips up to 5 of {crashCount} .ips files)', {crashCount: c.crashCount});
    document.getElementById('bugCrashCheck').checked = false;
  } else {
    crashRow.style.display = 'none';
  }
  // Focus the required "What happened?" box — the one thing only the
  // person filing the report can supply.
  setTimeout(() => document.getElementById('bugWhat')?.focus(), 50);
}
function closeBugModal() {
  document.getElementById('bugModal').style.display = 'none';
}
// SYS-40: for anyone without a GitHub account (or more comfortable
// reporting somewhere else in their own language) — copies the exact
// title + description GitHub would have received, already scrubbed
// server-side the same way (see get_panel_bug_context).
async function copyBugDiagnostics() {
  const statusEl = document.getElementById('bugStatus');
  const what = bugWhatText();
  const title = bugComposeTitle(document.getElementById('bugTitle').value, what);
  const body = bugComposeBody(document.getElementById('bugBody').value, what);
  try {
    await navigator.clipboard.writeText(title + '\n\n' + body);
    if (statusEl) statusEl.textContent = T('settings.copied_paste_it_anywhere_a', 'Copied — paste it anywhere (a forum post, a DM, a translated message).');
  } catch (e) {
    if (statusEl) statusEl.textContent = T('settings.could_not_copy_select_the', 'Could not copy — select the text above manually.');
  }
}
async function submitBugReport() {
  const submitBtn = document.getElementById('bugSubmitBtn');
  const statusEl = document.getElementById('bugStatus');
  if (!bugWhatOk()) {
    // Inline, next to the box, and focus it: no pop-up dialog, nothing sent.
    bugWhatShow(true);
    statusEl.textContent = '';
    document.getElementById('bugWhat')?.focus();
    return;
  }
  const what = bugWhatText();
  const title = bugComposeTitle(document.getElementById('bugTitle').value, what);
  const body = bugComposeBody(document.getElementById('bugBody').value, what);
  const includeCrashEl = document.getElementById('bugCrashCheck');
  const includeCrash = includeCrashEl && document.getElementById('bugCrashRow').style.display !== 'none'
    ? !!includeCrashEl.checked : false;
  submitBtn.disabled = true;
  submitBtn.textContent = T('settings.working', 'Working…');
  statusEl.textContent = T('settings.building_issue', 'Building issue…');
  try {
    const r = await fetch('/panel/bug-report', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title, body, whatHappened: what, includeCrashReports: includeCrash }),
    });
    const data = await r.json();
    if (data && data.field === 'what_happened') {
      // Server backstop agreed the description is missing: same inline copy.
      bugWhatShow(true);
      statusEl.textContent = '';
      submitBtn.disabled = false;
      submitBtn.textContent = T('settings.open_github_issue', 'Open GitHub issue');
      document.getElementById('bugWhat')?.focus();
      return;
    }
    if (!r.ok || !data.issueUrl) throw new Error(data.error || ('HTTP ' + r.status));
    // Open the issue in a new tab. noopener for safety.
    window.open(data.issueUrl, '_blank', 'noopener');
    if (data.zipPath) {
      // Try to copy the path to clipboard so the user can paste-find it
      // in Finder, OR drag the file from /tmp directly onto the GitHub
      // issue. Best-effort — clipboard write requires a user gesture
      // context, which a click handler counts as.
      try { await navigator.clipboard.writeText(data.zipPath); } catch (e) { /* ignore */ }
      statusEl.innerHTML =
        'Crash report bundled. Drag this file into the GitHub issue:<br>' +
        '<code style="font-size:11px">' + data.zipPath + '</code><br>' +
        '<span style="color:var(--success,#3fb950)">(path copied to clipboard)</span>';
      submitBtn.textContent = T('settings.done_close', 'Done — close');
      submitBtn.disabled = false;
      submitBtn.onclick = closeBugModal;
    } else {
      statusEl.textContent = T('settings.github_issue_opened_in_a', 'GitHub issue opened in a new tab.');
      submitBtn.textContent = T('settings.done_close', 'Done — close');
      submitBtn.disabled = false;
      submitBtn.onclick = closeBugModal;
      // Auto-close after a short delay if the user doesn't click Close.
      setTimeout(() => {
        const m = document.getElementById('bugModal');
        if (m && m.style.display !== 'none') closeBugModal();
      }, 4000);
    }
  } catch (err) {
    statusEl.innerHTML = '<span style="color:var(--danger,#f85149)">Failed: ' + err + '</span>';
    submitBtn.disabled = false;
    submitBtn.textContent = T('settings.try_again', 'Try again');
  }
}

// ====== Settings modal ======
// Single-shot fetch on open (settings change rarely). The modal hydrates
// preset cards from the /settings response so the labels and blurbs
// match the server-side OUTPUT_PRESETS table — no preset content
// duplicated in JS.
globalThis._settingsCache = null;

// Storage. Refreshed on modal OPEN, never on a timer — it walks real
// directories, and the only moment anyone looks at it is when the dialog opens.
async function refreshStorageSection() {
  const sec = document.getElementById('settingsStorageSection');
  const list = document.getElementById('storageList');
  const foot = document.getElementById('storageFoot');
  if (!sec || !list || !foot) return;
  let d;
  try { d = await api('/storage'); } catch (e) { sec.style.display = ''; foot.textContent = T('settings.could_not_read_storage_close', 'Could not read storage. Close Settings and try again.'); return; }
  const rows = d.rows || [];
  sec.style.display = '';
  list.innerHTML = rows.map(r => `
    <li class="kept"><div class="meta">
      <span class="ttl">${escapeHtml(r.name)} (${escapeHtml(r.size)})</span>
      <span class="sub">${escapeHtml((r.paths || []).join(' + '))}</span>
      <span class="sub">${escapeHtml(r.note)}</span>
    </div></li>`).join('');
  if (!rows.length) list.textContent = d.message || 'No model folders found.';
  foot.textContent = d.message || `${d.reclaimable_label} superseded · ${d.free_label} free on this disk`;
  const button = document.getElementById('storageCleanup');
  button.textContent = `Free up ${d.reclaimable_label}`;
  button.disabled = !d.reclaimable;

}

async function cleanupSupersededModels() {
  const button = document.getElementById('storageCleanup');
  const returnFocus = document.activeElement === button;
  button.disabled = true;
  try {
    const d = await api('/storage');
    if (!d.reclaimable) return;
    const folders = d.rows.filter(r => r.superseded_bytes).map(r => r.name).join('\n');
    if (!confirm(T('settings.free_up_remove_superseded_model', 'Free up {reclaimable_label}?\n\nRemove superseded model files in:\n{folders}\n\nThis cannot be undone. Current models, outputs and LoRAs stay on disk.', {reclaimable_label: d.reclaimable_label, folders}))) return;
    const res = await api('/storage/cleanup', 'POST', new URLSearchParams({
      confirmed: 'yes', confirmation: d.confirmation
    }));
    phosToast(`Freed ${res.freed_label}.`, { kind: 'success' });
  } catch (e) {
    phosToast(String((e && e.message) || e), { kind: 'danger', duration: 6000 });
  } finally {
    await refreshStorageSection();
    if (returnFocus && document.activeElement === document.body) {
      (button.disabled ? document.querySelector('#settingsModal button') : button)?.focus();
    }
  }
}

// "Move to another drive…" (Mac app). The move belongs to the app: its Settings window
// (Phosphene > Settings…, Models) copies the models, checks every file and switches over.
// People look for it HERE, in the Studio's Settings and Models screens (13 Nov 2026: a user
// on app 5.0.20 found model folders here and no move option), so both screens carry this row.
//   app 5.0.22+ announces window.__phosNativeCaps = ['modelStorage']: one click opens it.
//   app 5.0.20/5.0.21 (BOOT.app_build >= 50320) has it but no way to open it from here: say where.
//   older apps have no move at all: update the app first (the bridge's checkUpdate, 5.0.4+).
//   Pinokio: nothing (Pinokio keeps models in its own folder).
const MODELS_DRIVE_APP_BUILD = 50320;
function modelsDriveView() {
  if (!isMacApp()) return null;
  const caps = Array.isArray(window.__phosNativeCaps) ? window.__phosNativeCaps : [];
  if (caps.includes('modelStorage')) {
    return { text: T('settings.models_take_the_most_space', 'Models take the most space. You can keep them on an external drive instead of this Mac.'),
             button: T('settings.move_to_another_drive', 'Move to another drive\u2026'), send: 'modelStorage' };
  }
  if ((Number(BOOT.app_build) || 0) >= MODELS_DRIVE_APP_BUILD) {
    return { text: T('settings.to_keep_models_on_an', 'To keep models on an external drive: in the menu bar, choose Phosphene > Settings\u2026 (\u2318,). ')
                 + T('settings.under_models_choose_move_to', 'Under Models, choose Move to another drive\u2026'), button: '', send: '' };
  }
  return { text: T('settings.to_keep_models_on_an_2', 'To keep models on an external drive, update the Phosphene app first. ')
               + T('settings.then_choose_phosphene_settings_in', 'Then choose Phosphene > Settings\u2026 in the menu bar, and Move to another drive\u2026 under Models.'),
           button: T('settings.check_for_updates', 'Check for updates'), send: 'checkUpdate' };
}
function renderModelsDriveRows() {
  const v = modelsDriveView();
  document.querySelectorAll('.models-drive-row').forEach(row => {
    row.hidden = !v;
    row.textContent = '';
    if (!v) return;
    const p = document.createElement('div');
    p.className = 'hint';
    p.textContent = v.text;
    row.appendChild(p);
    if (v.button) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'ghost';
      b.textContent = v.button;
      b.addEventListener('click', () => {
        try { window.webkit.messageHandlers.phosphene.postMessage({ type: v.send }); }
        catch (e) { phosToast('Choose Phosphene > Settings\u2026 in the menu bar, then Models.', 'info'); }
      });
      row.appendChild(b);
    }
  });
}

async function openSettingsModal() {
  const modal = document.getElementById('settingsModal');
  modal.style.display = 'flex';
  renderModelsDriveRows();
  document.getElementById('settingsStatus').textContent = '';
  document.getElementById('settingsStatus').className = 'settings-status';
  refreshStorageSection();
  try {
    const r = await fetch('/settings');
    _settingsCache = await r.json();
  } catch (e) {
    document.getElementById('settingsStatus').textContent = T('settings.could_not_load_settings', 'Could not load settings.');
    document.getElementById('settingsStatus').className = 'settings-status err';
    return;
  }
  const cur = _settingsCache.settings;
  const presets = _settingsCache.presets;
  renderNotifyState(cur || {});
  applyAppearance();
  // Render preset cards (Standard, Video production, Web, Custom).
  // Display order matches the typical user journey: most users want
  // Standard, video pros pick Video production, web preview folks pick Web.
  const order = ['standard', 'archival', 'web'];
  const grid = document.getElementById('settingsPresets');
  grid.innerHTML = '';
  for (const key of order) {
    const p = presets[key];
    const active = cur.output_preset === key ? 'active' : '';
    const card = document.createElement('label');
    card.className = `preset-card ${active}`;
    card.dataset.preset = key;
    card.innerHTML = `
      <input type="radio" name="settingsPreset" value="${key}" ${cur.output_preset === key ? 'checked' : ''}>
      <div class="preset-text">
        <div class="preset-label">${escapeHtml(p.label)}</div>
        <div class="preset-blurb">${escapeHtml(p.blurb)}</div>
        <div class="preset-spec">pix_fmt=${p.pix_fmt} · crf=${p.crf}</div>
      </div>`;
    card.addEventListener('click', () => selectPreset(key));
    grid.appendChild(card);
  }
  // Custom row.
  const customActive = cur.output_preset === 'custom' ? 'active' : '';
  const custom = document.createElement('label');
  custom.className = `preset-card ${customActive}`;
  custom.dataset.preset = 'custom';
  custom.innerHTML = `
    <input type="radio" name="settingsPreset" value="custom" ${cur.output_preset === 'custom' ? 'checked' : ''}>
    <div class="preset-text">
      <div class="preset-label">${T('settings.custom', 'Custom')}</div>
      <div class="preset-blurb">${T('settings.set_pix_fmt_and_crf', 'Set pix_fmt and CRF manually. For unusual workflows: 10-bit HDR, format-specific delivery, or non-standard CRF for video production work.')}</div>
      <div class="preset-spec">pix_fmt=${cur.output_pix_fmt} · crf=${cur.output_crf}</div>
    </div>`;
  custom.addEventListener('click', () => selectPreset('custom'));
  grid.appendChild(custom);
  // Pre-fill custom inputs with current values
  document.getElementById('settingsPixFmt').value = cur.output_pix_fmt;
  document.getElementById('settingsCrfRange').value = cur.output_crf;
  document.getElementById('settingsCrfNum').value = cur.output_crf;
  document.getElementById('settingsCustomSection').style.display =
    cur.output_preset === 'custom' ? 'block' : 'none';

  const lpSelect = document.getElementById('settingsLivePreview');
  if (lpSelect) {
    lpSelect.value = (cur.live_preview === 'off') ? 'off' : 'on';
  }
  // SYS-28: the select can read "On" while the 22 MB decoder isn't on disk —
  // BOOT.ltx.preview_state names that case (`reason === 'missing_decoder'`)
  // the same way the Now card's preview area already does (preview.js).
  const lpNote = document.getElementById('settingsLivePreviewNote');
  if (lpNote) {
    const ps = ((BOOT.ltx || {}).preview_state) || {};
    if (ps.reason === 'missing_decoder') {
      lpNote.hidden = false;
      lpNote.innerHTML = T('settings.live_preview_needs_a_22', 'Live preview: needs a 22 MB add-on — ') +
        '<a href="#" onclick="closeSettingsModal();openModelsModal();return false;">Get it</a>';
    } else {
      lpNote.hidden = true;
      lpNote.textContent = '';
    }
  }
  // H3 model. Hidden entirely when H3 is not installed — an install that
  // cannot render H3 has no use for a control that only changes how it does.
  const ditRow = document.getElementById('settingsH3DitRow');
  const ditHint = document.getElementById('settingsH3DitHint');
  const ditSelect = document.getElementById('settingsH3Dit');
  if (ditRow && ditSelect) {
    const haveH3 = !!(LAST_STATUS && LAST_STATUS.h3 && LAST_STATUS.h3.available);
    ditRow.hidden = !haveH3;
    if (ditHint) ditHint.hidden = !haveH3;
    const v = String(cur.h3_dit || 'auto').toLowerCase();
    ditSelect.value = ['auto', 'bf16', 'q8'].includes(v) ? v : 'auto';
    // Below the full engine's memory floor "Full" is not a real choice: the
    // master alone loads ~39 GiB and the render dies of Metal memory. The
    // server renders Compact there anyway; the menu says why instead of
    // offering it.
    const fullOpt = ditSelect.querySelector('option[value="bf16"]');
    if (fullOpt) {
      const h3s = (LAST_STATUS && LAST_STATUS.h3) || {};
      const fits = h3s.bf16_fits !== false;
      fullOpt.disabled = !fits;
      fullOpt.textContent = fits
        ? T('settings.full_bf16_42_gib_peak', 'Full (bf16) — 42 GiB peak')
        : T('settings.full_bf16_needs', 'Full (bf16) — needs ') + Math.round(h3s.min_ram_gb || 60) + T('settings.gb_of_memory_not_this', ' GB of memory, not this Mac');
      if (h3s.bf16_fits === false && ditSelect.value === 'bf16') ditSelect.value = 'auto';
    }
  }

  // Token rows. We never receive the actual key from the server (the
  // /settings GET returns has_X booleans only), so we display either
  // "set" with an empty placeholder input, or "—" with the placeholder.
  // Inputs start empty on every modal open; user pastes when they want
  // to change.
  setTokenStatus('civitaiKey', cur.has_civitai_key);
  setTokenStatus('hfToken', cur.has_hf_token);
  // Placeholders reflect the saved state so an empty input doesn't read
  // as "no token here" when there is one. The asterisks make it clear
  // something's persisted; the hint reminds users they paste to replace.
  const civInput = document.getElementById('civitaiKeyInput');
  const hfInput = document.getElementById('hfTokenInput');
  civInput.value = '';
  hfInput.value = '';
  civInput.placeholder = cur.has_civitai_key
    ? T('settings.saved_paste_new_to_replace', '•••••••••• saved — paste new to replace')
    : T('settings.32_char_api_key', '32-char API key');
  hfInput.placeholder = cur.has_hf_token
    ? T('settings.saved_paste_new_to_replace', '•••••••••• saved — paste new to replace')
    : 'hf_…';
  document.getElementById('civitaiKeyClear').style.display = cur.has_civitai_key ? '' : 'none';
  document.getElementById('hfTokenClear').style.display = cur.has_hf_token ? '' : 'none';

  // Spicy mode — render current state. _spicyArmed is the mid-confirm
  // state (clicked once, waiting for the second click). It lives only
  // on the JS side; only ON/OFF gets persisted.
  _spicyArmed = false;
  renderSpicyState(!!cur.spicy_mode);

  // Anonymous usage analytics — badge, hint (shows the actual install id
  // so "you are a random ID" is verifiable, not a claim), and the two
  // maintainer key rows. Same has_X-boolean treatment as the other
  // secrets: the keys never come back from the server.
  renderAnalyticsState(cur);
}

// ---- Appearance ------------------------------------------------------------
// A browser preference, not a panel setting: two people can look at the same
// panel in two rooms. `system` re-applies live when the Mac switches.
function appearanceGet() {
  try { const v = localStorage.getItem('phos_appearance'); return (v === 'light' || v === 'system') ? v : 'dark'; }
  catch (e) { return 'dark'; }
}
function applyAppearance(mode) {
  const m = mode || appearanceGet();
  const dark = m === 'dark' || (m === 'system' && !(window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches));
  document.documentElement.setAttribute('data-theme', dark ? 'dark' : 'light');
  // The server stamps this on <html> for the NEXT load so a light palette is
  // painted first, not switched to after the modules run (cookie, one year).
  try { document.cookie = 'phos_theme=' + (dark ? 'dark' : 'light') + ';path=/;max-age=31536000;samesite=lax'; } catch (e) {}
  document.querySelectorAll('#appearanceGroup .pill-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.appearance === m));
}
function setAppearance(mode) {
  try { localStorage.setItem('phos_appearance', mode); } catch (e) {}
  applyAppearance(mode);
}
try {
  if (window.matchMedia) {
    window.matchMedia('(prefers-color-scheme: light)').addEventListener('change', () => {
      if (appearanceGet() === 'system') applyAppearance('system');
    });
  }
} catch (e) {}

// ---- Completion alerts -----------------------------------------------------
// One switch, and one ask. The chime needs nothing; the browser notification
// needs the person to allow it once, and the button says which state that is
// in rather than asking on every render.
function renderNotifyState(cur) {
  const on = cur.notify_done !== false;
  const badge = document.getElementById('notifyStateBadge');
  const btn = document.getElementById('notifyToggleBtn');
  const ask = document.getElementById('notifyAllowBtn');
  const hint = document.getElementById('notifyHint');
  if (badge) { badge.textContent = on ? 'ON' : 'OFF'; badge.className = 'spicy-state' + (on ? ' on' : ''); }
  if (btn) btn.textContent = on ? T('settings.turn_off', 'Turn off') : T('settings.turn_on', 'Turn on');
  const perm = (typeof window.Notification !== 'undefined') ? window.Notification.permission : 'unsupported';
  if (ask) {
    ask.hidden = !on || perm !== 'default';
  }
  if (hint) {
    hint.textContent = !on ? T('settings.no_chime_no_browser_alert', 'No chime, no browser alert.')
      : perm === 'granted' ? T('settings.a_chime_in_this_tab', 'A chime in this tab, and a browser alert when the tab is in the background')
          + (cur.push_available ? T('settings.and_once_you_turn_it', ' — and, once you turn it on below, even with the tab closed.') : T('settings.closed_tab_alerts_need_an', '. Closed-tab alerts need an Update on this install.'))
      : perm === 'denied' ? T('settings.a_chime_in_this_tab_2', 'A chime in this tab. Browser alerts are blocked for this site in the browser\'s own settings.')
      : perm === 'unsupported' ? T('settings.a_chime_in_this_tab_3', 'A chime in this tab.')
      : T('settings.a_chime_in_this_tab_4', 'A chime in this tab. Allow browser alerts to be told when the tab is in the background')
          + (cur.push_available ? T('settings.or_even_when_it_is', ', or even when it is closed.') : '.');
  }
  // PUSH, for a closed tab. Offered when the install can sign pushes and the
  // browser can hold a subscription; the button says which state it is in.
  const pb = document.getElementById('notifyPushBtn');
  const pt = document.getElementById('notifyPushTestBtn');
  const canPush = on && cur.push_available && ('serviceWorker' in navigator) && ('PushManager' in window) && perm !== 'denied';
  if (pb) {
    pb.hidden = !canPush;
    if (canPush) {
      pushSubscription().then(s => {
        pb.textContent = s ? T('settings.stop_closed_tab_alerts_in', 'Stop closed-tab alerts in this browser') : T('settings.alert_me_here_even_when', 'Alert me here even when the tab is closed');
        if (pt) pt.hidden = !s;
      }).catch(() => {});
    } else if (pt) pt.hidden = true;
  }
}
async function toggleNotify() {
  const cur = (_settingsCache && _settingsCache.settings) || {};
  const target = !(cur.notify_done !== false);
  const fd = new URLSearchParams();
  fd.set('notify_done', target ? '1' : '0');
  try {
    const r = await fetch('/settings', { method: 'POST', body: fd });
    const j = await r.json();
    if (j.settings) _settingsCache.settings = j.settings;
    else if (_settingsCache && _settingsCache.settings) _settingsCache.settings.notify_done = target;
  } catch (e) {}
  if (target && typeof playDoneChime === 'function') playDoneChime(false);
  renderNotifyState((_settingsCache && _settingsCache.settings) || {});
  if (typeof phosToast === 'function') phosToast(target ? T('settings.completion_alerts_on_saved', 'Completion alerts on — saved.') : T('settings.completion_alerts_off_saved', 'Completion alerts off — saved.'), { duration: 2500 });
}
// ---- Push: the alert that reaches a closed tab ---------------------------
// The browser keeps a subscription for this origin; the panel signs pushes
// with its own key. `pushToggle` subscribes or unsubscribes THIS browser.
function _pushKeyBytes(b64) {
  const pad = '='.repeat((4 - b64.length % 4) % 4);
  const raw = window.atob((b64 + pad).replace(/-/g, '+').replace(/_/g, '/'));
  return Uint8Array.from(raw, c => c.charCodeAt(0));
}
async function pushSubscription() {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return null;
  const reg = await navigator.serviceWorker.getRegistration('/');
  return reg ? reg.pushManager.getSubscription() : null;
}
async function pushToggle() {
  try {
    const have = await pushSubscription();
    if (have) {
      const fd = new URLSearchParams(); fd.set('endpoint', have.endpoint);
      await fetch('/push/unsubscribe', { method: 'POST', body: fd });
      await have.unsubscribe();
      if (typeof phosToast === 'function') phosToast(T('settings.this_browser_will_no_longer', 'This browser will no longer be alerted when closed.'), { duration: 3000 });
    } else {
      const k = await (await fetch('/push/key')).json();
      if (!k.ok) { if (typeof phosToast === 'function') phosToast(T('settings.closed_tab_alerts_are_not', 'Closed-tab alerts are not available on this install yet — run Update.'), { kind: 'danger' }); return; }
      if (typeof window.Notification !== 'undefined' && window.Notification.permission !== 'granted') {
        const p = await window.Notification.requestPermission();
        if (p !== 'granted') { if (typeof phosToast === 'function') phosToast(T('settings.the_browser_did_not_allow', 'The browser did not allow notifications.'), {}); return; }
      }
      const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true,
                                                    applicationServerKey: _pushKeyBytes(k.public_key) });
      const fd = new URLSearchParams(); fd.set('subscription', JSON.stringify(sub));
      const r = await (await fetch('/push/subscribe', { method: 'POST', body: fd })).json();
      if (typeof phosToast === 'function') phosToast(r.ok ? T('settings.done_this_browser_will_be', 'Done — this browser will be alerted even when the tab is closed.') : (r.error || 'Could not turn it on.'), { duration: 4000 });
    }
  } catch (e) {
    if (typeof phosToast === 'function') phosToast(T('settings.closed_tab_alerts_could_not', 'Closed-tab alerts could not be set up in this browser: ') + (e && e.message ? e.message : e), { kind: 'danger', duration: 6000 });
  }
  renderNotifyState((_settingsCache && _settingsCache.settings) || {});
}
async function pushTest() {
  try {
    const r = await (await fetch('/push/test', { method: 'POST' })).json();
    if (typeof phosToast === 'function') phosToast(r.ok ? (T('settings.sent_to', 'Sent to ') + r.sent + (r.sent === 1 ? ' browser.' : ' browsers.')) : (r.error || 'Nothing sent.'), { duration: 4000 });
  } catch (e) {}
}

async function askNotifyPermission() {
  try {
    if (typeof window.Notification !== 'undefined') await window.Notification.requestPermission();
  } catch (e) {}
  renderNotifyState((_settingsCache && _settingsCache.settings) || {});
}

// ---- Anonymous usage analytics ---------------------------------------------
// Single-click both ways: turning it OFF must be at least as easy as
// leaving it ON, which is the whole justification for defaulting to ON.
// No confirm dance here (unlike Spicy mode) — nothing irreversible happens.
function renderAnalyticsState(cur) {
  const on = cur.analytics_enabled !== false;
  const badge = document.getElementById('analyticsStateBadge');
  const btn = document.getElementById('analyticsToggleBtn');
  const hint = document.getElementById('analyticsHint');
  if (badge) {
    badge.textContent = on ? 'ON' : 'OFF';
    badge.className = 'spicy-state' + (on ? ' on' : '');
  }
  if (btn) btn.textContent = on ? T('settings.turn_off', 'Turn off') : T('settings.turn_on', 'Turn on');
  if (hint) {
    if (!on) {
      hint.textContent = T('settings.off_nothing_is_sent_and', 'Off — nothing is sent, and nothing is written to ')
        + T('settings.the_local_usage_log_either', 'the local usage log either.');
    } else if (!cur.has_analytics_key) {
      hint.innerHTML = T('settings.on_but_no_project_key', 'On, but no project key is configured — this panel is ')
        + 'sending <b>nothing</b> over the network. Events are only written to '
        + T('settings.the_local_log', 'the local log.');
    } else {
      hint.textContent = T('settings.your_anonymous_id', 'Your anonymous ID: ') + (cur.analytics_install_id || '(not yet generated)');
    }
  }
  setTokenStatus('analyticsKey', !!cur.has_analytics_key);
  setTokenStatus('analyticsQueryKey', !!cur.has_analytics_query_key);
  const k1 = document.getElementById('analyticsKeyInput');
  const k2 = document.getElementById('analyticsQueryKeyInput');
  if (k1) { k1.value = ''; k1.placeholder = cur.has_analytics_key ? T('settings.saved_paste_new_to_replace', '•••••••••• saved — paste new to replace') : 'phc_…'; }
  if (k2) { k2.value = ''; k2.placeholder = cur.has_analytics_query_key ? T('settings.saved_paste_new_to_replace', '•••••••••• saved — paste new to replace') : 'phx_…'; }
  const c1 = document.getElementById('analyticsKeyClear');
  const c2 = document.getElementById('analyticsQueryKeyClear');
  if (c1) c1.style.display = cur.has_analytics_key ? '' : 'none';
  if (c2) c2.style.display = cur.has_analytics_query_key ? '' : 'none';
}

async function toggleAnalytics() {
  const cur = (_settingsCache && _settingsCache.settings) || {};
  const target = !(cur.analytics_enabled !== false);
  const status = document.getElementById('settingsStatus');
  try {
    const fd = new URLSearchParams();
    fd.set('analytics_enabled', target ? 'true' : 'false');
    const r = await fetch('/settings', { method: 'POST', body: fd });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    if (j.settings) _settingsCache.settings = j.settings;
    else if (_settingsCache && _settingsCache.settings) {
      _settingsCache.settings.analytics_enabled = target;
    }
    renderAnalyticsState(_settingsCache.settings || {});
    if (status) {
      status.textContent = target
        ? T('settings.anonymous_usage_analytics_on', 'Anonymous usage analytics ON')
        : T('settings.anonymous_usage_analytics_off_nothing', 'Anonymous usage analytics OFF · nothing will be sent or logged');
      status.className = 'settings-status ok';
    }
  } catch (e) {
    if (status) {
      status.textContent = T('settings.could_not_change_analytics', 'Could not change analytics: ') + (e.message || e);
      status.className = 'settings-status err';
    }
  }
}

async function _persistAnalyticsKey(field, value) {
  const status = document.getElementById('settingsStatus');
  try {
    const fd = new URLSearchParams();
    fd.set(field, value);
    const r = await fetch('/settings', { method: 'POST', body: fd });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    if (j.settings) _settingsCache.settings = j.settings;
    renderAnalyticsState(_settingsCache.settings || {});
    if (status) {
      status.textContent = value ? T('settings.key_saved', 'Key saved.') : T('settings.key_cleared', 'Key cleared.');
      status.className = 'settings-status ok';
    }
  } catch (e) {
    if (status) {
      status.textContent = T('settings.could_not_save_key', 'Could not save key: ') + (e.message || e);
      status.className = 'settings-status err';
    }
  }
}

function saveAnalyticsKey(field) {
  const id = field === 'analytics_query_key' ? 'analyticsQueryKeyInput' : 'analyticsKeyInput';
  const el = document.getElementById(id);
  const val = (el && el.value || '').trim();
  if (!val) return;
  _persistAnalyticsKey(field, val);
}

function clearAnalyticsKey(field) {
  _persistAnalyticsKey(field, '');
}



let _spicyArmed = false;

function renderSpicyState(isOn) {
  const badge = document.getElementById('spicyStateBadge');
  const btn = document.getElementById('spicyToggleBtn');
  const hint = document.getElementById('spicyHint');
  if (!badge || !btn || !hint) return;      // not served in the Mac app
  badge.classList.remove('on', 'armed');
  if (_spicyArmed) {
    badge.textContent = 'ARMED';
    badge.classList.add('armed');
    btn.textContent = T('settings.click_again_to_confirm', 'Click again to confirm');
    btn.classList.remove('ghost-btn');
    btn.classList.add('primary-btn');
    hint.style.display = '';
    hint.textContent = T('settings.confirms_turning_spicy_mode_on', 'Confirms turning Spicy mode ON. NSFW LoRAs will be available in the CivitAI browser. Cancel by closing the modal.');
  } else if (isOn) {
    badge.textContent = 'ON';
    badge.classList.add('on');
    btn.textContent = T('settings.disable', 'Disable');
    btn.classList.remove('primary-btn');
    btn.classList.add('ghost-btn');
    hint.style.display = '';
    hint.textContent = T('settings.spicy_mode_is_on_nsfw', 'Spicy mode is ON. NSFW LoRAs are visible in the CivitAI browser when you tick "Show NSFW".');
  } else {
    badge.textContent = 'OFF';
    btn.textContent = T('settings.enable_spicy_mode', 'Enable Spicy mode');
    btn.classList.remove('primary-btn');
    btn.classList.add('ghost-btn');
    hint.style.display = 'none';
    hint.textContent = '';
  }
}

async function toggleSpicyMode() {
  // Two-click confirm to turn ON, single-click to turn OFF.
  // Easy to disable, deliberate to enable — matches the user spec
  // ("don't want people to turn it on by mistake, or kids").
  const cur = (_settingsCache && _settingsCache.settings) || {};
  const isOn = !!cur.spicy_mode;
  if (isOn) {
    // Single-click off, no confirm.
    await _persistSpicyMode(false);
    return;
  }
  if (!_spicyArmed) {
    _spicyArmed = true;
    renderSpicyState(false);
    // Auto-disarm after 6 s if the user doesn't confirm — prevents
    // the "click again" state lingering across an unrelated tab return.
    setTimeout(() => {
      if (_spicyArmed) {
        _spicyArmed = false;
        renderSpicyState(!!(_settingsCache?.settings?.spicy_mode));
      }
    }, 6000);
    return;
  }
  // Second click — actually persist.
  _spicyArmed = false;
  await _persistSpicyMode(true);
}

async function _persistSpicyMode(target) {
  const status = document.getElementById('settingsStatus');
  try {
    const fd = new URLSearchParams();
    fd.set('spicy_mode', target ? 'true' : 'false');
    const r = await fetch('/settings', { method: 'POST', body: fd });
    const j = await r.json();
    if (j.error) throw new Error(j.error);
    // Use the value acknowledged by the server as the shared UI source of
    // truth. This prevents the Settings panel and render form from briefly
    // disagreeing if validation/coercion changes server-side.
    if (!_settingsCache) _settingsCache = {};
    _settingsCache.settings = (j && j.settings) || Object.assign(
      {}, _settingsCache.settings || {}, { spicy_mode: !!target }
    );
    renderSpicyState(spicyModeEnabled());
    if (status) {
      status.textContent = target ? T('settings.spicy_mode_on_nsfw_loras', 'Spicy mode ON · NSFW LoRAs unlocked') : T('settings.spicy_mode_off_nsfw_loras', 'Spicy mode OFF · NSFW LoRAs hidden');
      status.className = 'settings-status ok';
    }
    // Refresh the CivitAI panel so the "Show NSFW" toggle appears /
    // disappears immediately without a full page reload.
    if (typeof renderSpicyAccess === 'function') renderSpicyAccess();
  } catch (e) {
    if (status) {
      status.textContent = T('settings.could_not_change_spicy_mode', 'Could not change Spicy mode: ') + (e.message || e);
      status.className = 'settings-status err';
    }
  }
}

function setTokenStatus(prefix, isSet, dirty) {
  const el = document.getElementById(prefix + 'Status');
  if (!el) return;
  el.classList.remove('set', 'dirty');
  if (dirty) {
    el.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-pencil-simple"/></svg>unsaved';
    el.classList.add('dirty');
  } else if (isSet) {
    el.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-check-bold"/></svg>saved';
    el.classList.add('set');
  } else {
    el.textContent = T('settings.not_set', 'not set');
  }
}

function onTokenInput(which) {
  const prefix = which === 'civitai' ? 'civitaiKey' : 'hfToken';
  const inp = document.getElementById(prefix + 'Input');
  setTokenStatus(prefix, false, !!inp.value);
}

function toggleTokenVisibility(inputId, btn) {
  const inp = document.getElementById(inputId);
  if (!inp) return;
  if (inp.type === 'password') {
    inp.type = 'text';
    btn.textContent = 'hide';
  } else {
    inp.type = 'password';
    btn.textContent = 'show';
  }
}

// Save-then-test in one click. The /civitai/test and /hf/test endpoints
// use the saved key, not the current input field. Pre-Y1.023 the user
// had to: paste → click Apply → click Test. That left a footgun: users
// pasted, clicked Test, saw it fail (because nothing was saved yet),
// closed the modal thinking the panel was broken. The token never
// landed in panel_settings.json, gated downloads kept failing.
//
// Now Test does Save first when the input has a value, so a single
// click works. If the save fails (validator rejects malformed token),
// we surface the error inline next to the field instead of just at
// the bottom of the modal.
async function testToken(which) {
  const path = which === 'civitai' ? '/civitai/test' : '/hf/test';
  const resultId = which === 'civitai' ? 'civitaiTestResult' : 'hfTestResult';
  const inputId = which === 'civitai' ? 'civitaiKeyInput' : 'hfTokenInput';
  const fieldName = which === 'civitai' ? 'civitai_api_key' : 'hf_token';
  const statusPrefix = which === 'civitai' ? 'civitaiKey' : 'hfToken';
  const clearBtnId = which === 'civitai' ? 'civitaiKeyClear' : 'hfTokenClear';
  const result = document.getElementById(resultId);
  if (!result) return;
  result.textContent = T('settings.testing', 'Testing…');
  result.style.color = 'var(--muted)';

  // If the input has content, save it first. Empty input means "test
  // the already-saved token" — the legitimate use after the panel is
  // configured.
  const inputEl = document.getElementById(inputId);
  const inputValue = inputEl ? inputEl.value.trim() : '';
  if (inputValue) {
    try {
      const fd = new URLSearchParams();
      fd.set(fieldName, inputValue);
      const saveResp = await fetch('/settings', {
        method: 'POST',
        headers: {'Content-Type': 'application/x-www-form-urlencoded'},
        body: fd,
      });
      const saveData = await saveResp.json();
      if (!saveResp.ok || saveData.error) {
        result.innerHTML = `<svg class="ph" aria-hidden="true" style="color:var(--danger,#f85149);margin-right:4px;vertical-align:-2px"><use href="#ph-x-circle-fill"/></svg>${escapeHtml(saveData.error || `HTTP ${saveResp.status}`)}`;
        return;
      }
      // Save succeeded — reflect the persisted state in the UI.
      inputEl.value = '';
      _settingsCache = { ...(_settingsCache || {}), settings: saveData.settings };
      setTokenStatus(statusPrefix, true);
      const clearBtn = document.getElementById(clearBtnId);
      if (clearBtn) clearBtn.style.display = '';
    } catch (e) {
      result.innerHTML = `<svg class="ph" aria-hidden="true" style="color:var(--danger,#f85149);margin-right:4px;vertical-align:-2px"><use href="#ph-x-circle-fill"/></svg>Save failed: ${escapeHtml(e.message || String(e))}`;
      return;
    }
  }

  // Now hit the test endpoint, which reads the freshly-saved token.
  try {
    const r = await fetch(path);
    const data = await r.json();
    if (data.ok) {
      result.innerHTML = `<svg class="ph" aria-hidden="true" style="color:var(--success,#3fb950);margin-right:4px;vertical-align:-2px"><use href="#ph-check-bold"/></svg>${escapeHtml(data.message)}`;
    } else {
      result.innerHTML = `<svg class="ph" aria-hidden="true" style="color:var(--danger,#f85149);margin-right:4px;vertical-align:-2px"><use href="#ph-x-circle-fill"/></svg>${escapeHtml(data.error)}`;
    }
  } catch (e) {
    result.innerHTML = `<svg class="ph" aria-hidden="true" style="color:var(--danger,#f85149);margin-right:4px;vertical-align:-2px"><use href="#ph-x-circle-fill"/></svg>Network error: ${escapeHtml(e.message || String(e))}`;
  }
}

async function clearToken(which) {
  const fd = new FormData();
  if (which === 'civitai') fd.set('civitai_api_key', '');
  if (which === 'hf')      fd.set('hf_token', '');
  try {
    // urlencoded body — see applySettings for why.
    const r = await fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams(fd),
    });
    const data = await r.json();
    if (!r.ok || data.error) {
      alert(T('settings.could_not_clear', 'Could not clear: ') + (data.error || `HTTP ${r.status}`));
      return;
    }
    // Refresh the modal so the status flips back to "not set".
    openSettingsModal();
  } catch (e) {
    alert(T('settings.network_error', 'Network error: ') + (e.message || e));
  }
}

function closeSettingsModal() {
  document.getElementById('settingsModal').style.display = 'none';
}

function selectPreset(key) {
  document.querySelectorAll('#settingsPresets .preset-card').forEach(c => {
    c.classList.toggle('active', c.dataset.preset === key);
    const r = c.querySelector('input[type="radio"]');
    if (r) r.checked = (c.dataset.preset === key);
  });
  document.getElementById('settingsCustomSection').style.display =
    key === 'custom' ? 'block' : 'none';
  // Clear status so it doesn't claim "saved" after a fresh selection.
  document.getElementById('settingsStatus').textContent = '';
  document.getElementById('settingsStatus').className = 'settings-status';
}

async function applySettings() {
  const status = document.getElementById('settingsStatus');
  const btn = document.getElementById('settingsApplyBtn');
  status.textContent = T('settings.saving', 'Saving…');
  status.className = 'settings-status';
  btn.disabled = true;
  // Read which preset is selected. Custom path also sends pix_fmt + crf.
  const checked = document.querySelector('#settingsPresets input[type="radio"]:checked');
  const preset = checked ? checked.value : 'standard';
  const fd = new FormData();
  fd.set('output_preset', preset);
  if (preset === 'custom') {
    fd.set('output_pix_fmt', document.getElementById('settingsPixFmt').value);
    fd.set('output_crf', document.getElementById('settingsCrfNum').value);
  }
  fd.set('live_preview', document.getElementById('settingsLivePreview')?.value || 'on');
  const _dit = document.getElementById('settingsH3Dit')?.value;
  if (_dit) fd.set('h3_dit', _dit);
  // Tokens — only send a key when the input has a value. Empty input
  // means "leave as-is" (clearing is explicit via the Clear button).
  // This protects against accidentally wiping a saved key by clicking
  // Apply on an unchanged form.
  const civInput = document.getElementById('civitaiKeyInput').value.trim();
  if (civInput) fd.set('civitai_api_key', civInput);
  const hfInput = document.getElementById('hfTokenInput').value.trim();
  if (hfInput)  fd.set('hf_token', hfInput);
  try {
    // Convert FormData → URLSearchParams so the body is sent as
    // x-www-form-urlencoded — the panel's parse_qs only understands
    // that wire format, NOT the multipart/form-data fetch sends by
    // default with FormData. This bug silently turned every settings
    // save into a no-op (server saw empty payload) until caught.
    const r = await fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: new URLSearchParams(fd),
    });
    const data = await r.json();
    if (!r.ok || data.error) {
      status.textContent = data.error || `HTTP ${r.status}`;
      status.className = 'settings-status err';
      btn.disabled = false;
      return;
    }
    status.textContent = data.helper_restarted
      ? T('settings.saved_helper_restarted_takes_effect', 'Saved. Helper restarted — takes effect on the next render.')
      : T('settings.saved', 'Saved.');
    status.className = 'settings-status ok';
    btn.disabled = false;
    // Refresh cache so a re-open shows the new values without a stale flash.
    _settingsCache = { ...(_settingsCache || {}), settings: data.settings };
  } catch (e) {
    status.textContent = T('settings.network_error', 'Network error: ') + (e.message || e);
    status.className = 'settings-status err';
    btn.disabled = false;
  }
}

// ====== HDR toggle pill (header pill behavior, same as No-music) ======
(function () {
  const pill = document.getElementById('hdrPill');
  const cb = document.getElementById('hdr');
  if (!pill || !cb) return;
  const sync = () => pill.classList.toggle('on', cb.checked);
  cb.addEventListener('change', sync);
  pill.addEventListener('click', () => setTimeout(sync, 0));
  sync();
})();

// ====== No-voice toggle pill (mirrors HDR / No-music sync pattern) ======
// 2026-05-21 — Mr Bizarro: "No voice button is not clickable for some
// reason." Root cause: the pill never had a sync IIFE like hdrPill /
// noMusicPill / civitaiNsfwPill. Clicking the <label> toggled the inner
// checkbox (standard HTML label-input pairing), but nothing flipped the
// .on class so the pill never lit up. From the user's perspective it
// read as "click doesn't do anything." This IIFE makes it visually
// reactive — same shape as the HDR pill wiring above.
(function () {
  const pill = document.getElementById('noVoicePill');
  const cb = document.getElementById('noVoice');
  if (!pill || !cb) return;
  const sync = () => pill.classList.toggle('on', cb.checked);
  cb.addEventListener('change', sync);
  pill.addEventListener('click', () => setTimeout(sync, 0));
  sync();
})();

// HDR pill tooltip annotator (2026-05-21).
// Earlier version (1ea5f1d) hard-disabled this pill in Character mode
// because the docs say HDR-IC requires the distilled checkpoint. Mr
// Bizarro pushed back — he should be allowed to try the combo and
// judge the quality himself. Lifted the hard block; this helper now
// just annotates the tooltip so the user knows when they're entering
// experimental territory.
function _applyHdrPillAvailability() {
  const pill = document.getElementById('hdrPill');
  const cb = document.getElementById('hdr');
  if (!pill || !cb) return;
  const charInp = document.getElementById('characterIdInput');
  const charId = charInp ? (charInp.value || '').trim() : '';
  const inChar = (typeof currentMode !== 'undefined' && currentMode === 'character') || !!charId;
  // Always clickable now.
  pill.classList.remove('disabled');
  pill.style.opacity = '';
  pill.style.pointerEvents = '';
  if (inChar) {
    pill.title = T('settings.hdr_character_is_experimental_the', 'HDR + character is experimental. The HDR IC-LoRA runs on ')
               + 'the distilled Q4 base; your character LoRA was trained against '
               + T('settings.q8_dev_mechanically_both_stack', 'Q8 dev. Mechanically both stack on the pipeline, but character ')
               + T('settings.fidelity_may_be_weaker_than', 'fidelity may be weaker than a non-HDR render. Try it and judge ')
               + T('settings.the_output_that_s_the', 'the output — that\'s the only way to know.');
  } else {
    pill.title = T('settings.hdr_via_the_official_lightricks', 'HDR via the official Lightricks LTX-2.3-22b IC-LoRA-HDR. ')
               + T('settings.auto_routes_to_the_distilled', 'Auto-routes to the distilled Q4 path (required by the IC-LoRA pipeline). ')
               + T('settings.first_hdr_job_downloads_the', 'First HDR job downloads the LoRA weights from Hugging Face (~330 MB, ')
               + T('settings.gated_needs_an_hf_token', 'gated — needs an HF token in Settings). Output is standard MP4 plus ')
               + T('settings.a_companion_hdr_npz_tensor', 'a companion .hdr.npz tensor (float32 scene-linear) for any pro tool ')
               + T('settings.that_wants_the_raw_hdr', 'that wants the raw HDR.');
  }
}
// Expose globally so setMode + character chip selection can call it.
window._applyHdrPillAvailability = _applyHdrPillAvailability;
// Run once at boot so the initial state (character_id might be set
// from localStorage / load-params) gets reflected immediately.
document.addEventListener('DOMContentLoaded', _applyHdrPillAvailability);
_applyHdrPillAvailability();

// ====== CivitAI NSFW toggle pill (mirrors HDR toggle UX) ======
(function () {
  const pill = document.getElementById('civitaiNsfwPill');
  const cb = document.getElementById('civitaiNsfw');
  if (!pill || !cb) return;
  const sync = () => pill.classList.toggle('on', cb.checked);
  cb.addEventListener('change', sync);
  pill.addEventListener('click', () => setTimeout(sync, 0));
  sync();
})();


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
// Every hidden -> visible visit starts the CSS entrance once. Status polls and
// repeated open calls leave it alone. The modal scaffold owns Escape and Tab.
let proReturnFocus = null;
// `where` names the door it was opened from, a closed slug (PRO_HINT_WHERE in mlx_ltx_panel.py, docs/ANALYTICS.md):
// topbar, remote_hint, your_pro, feature_gate (a locked Pro feature; the default for any caller that names none).
const PRO_OPEN_WHERE = ['topbar', 'remote_hint', 'your_pro', 'feature_gate'];
function proUiEvent(props) { if (typeof _uiEvent === 'function') _uiEvent('pro_click', props); }
function openProModal(opener = null, where = null) {
  const modal = document.getElementById('proModal');
  if (modal.style.display !== 'none') return;
  proReturnFocus = opener || document.activeElement;
  const oid = opener && opener.id;
  if (!PRO_OPEN_WHERE.includes(where)) where = oid === 'proBtn' ? 'topbar' : oid === 'rmWaitSettings' ? 'remote_hint' : 'feature_gate';
  proUiEvent({where, action: 'open'});
  modal.style.display = 'flex';
  proThisMacRender();
  proFeatureListRender();
  proLicenceLoad();
  // Media in a dialog plays muted on a loop while it is open, like What's new (none of it with reduced motion).
  const vid = document.getElementById('proCompareVideo');
  if (vid) {
    if (window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches) vid.controls = true;
    else vid.play().catch(() => {});
  }
  (modal.querySelector('.dlg') || modal.querySelector('button')).focus({preventScroll: true});
}
// The PRO mark: one GET /pro/status at boot. Free is the default state of the page; nothing nags.
async function proStatusRefresh() {
  let st = null;
  try { const r = await fetch('/pro/status', { cache: 'no-store' }); if (r.ok) st = await r.json(); } catch (_) { st = null; }
  const mark = document.getElementById('proMark');
  const active = !!(st && st.active);
  document.documentElement.dataset.pro = active ? (st.plan || 'pro') : '';
  // The header "Pro" door is for Free in Pinokio only: a Pro member sees the PRO mark by the logo
  // instead, and the Mac app has its own Pro chip in the top bar.
  const btn = document.getElementById('proBtn');
  // A member sees the door only to manage the licence this Studio holds (the modal hides the selling then).
  // Pinokio: always reachable. A Pro Mac that only borrows the Mac app's pass must still be able to paste its own key
  // (Remote's components and renewal while the app is closed need it).
  if (btn) { btn.hidden = isMacApp(); btn.textContent = active ? T('settings.licence', 'Licence') : 'Pro'; }
  if (mark) { mark.hidden = !active; mark.textContent = active && st.label ? st.label : 'Pro'; }
  globalThis.proSpeedStatus = (st && st.ltx_speedup) || null;
  proMarksApply();
  proSpeedRender();
  return st;
}

// ---- Pro speed-up, beside Quality (Pro ledger row ltx-faster) -----------------------------------------------
// Free: one quiet "Pro adds" line. Pro: a switch, on by default, that turns it off for a render; it only says
// "on" where the render can use it (LTX-2.5 Balanced). Whether a render is sped up is decided by the Studio
// and the Pro add-on, never here; this only writes the user's choice into #pro_speedup_choice.
const PRO_SPEED_KEY = 'phos_pro_speedup_choice';
function proSpeedRender() {
  const row = document.getElementById('proSpeedRow');
  if (!row) return;
  const pro = proIsOn();
  const st = globalThis.proSpeedStatus || {};
  const sw = document.getElementById('proSpeedSwitch');
  const note = document.getElementById('proSpeedNote');
  const field = document.getElementById('pro_speedup_choice');
  if (!pro) {                                   // Free: the line only
    sw.hidden = true; note.hidden = false; row.hidden = false;
    note.innerHTML = proNoteHtml(proSpotText('ltx-faster', false));
    if (field) field.value = 'on';
    return;
  }
  note.hidden = true;
  if (!st.offered) { row.hidden = true; return; }   // not on this Mac or this install: nothing to switch
  let choice = 'on';
  try { choice = localStorage.getItem(PRO_SPEED_KEY) || 'on'; } catch (e) { /* keep on */ }
  const box = document.getElementById('proSpeedToggle');
  box.checked = choice !== 'off';
  if (field) field.value = box.checked ? 'on' : 'off';
  const q = (document.getElementById('quality') || {}).value || '';
  const fits = q === 'balanced';
  const state = document.getElementById('proSpeedState');
  state.textContent = !box.checked ? 'off'
    : !fits ? T('settings.on_for_balanced_only', 'on for Balanced only')
    : st.downloading ? T('settings.getting_ready_one_time_download', 'getting ready (one-time download)')
    : !st.ready ? T('settings.on_first_use_downloads', 'on · first use downloads ') + (st.gb || 1.2) + T('settings.gb_once', ' GB once')
    : 'on';
  sw.title = T('settings.about_a_quarter_less_waiting', 'About a quarter less waiting on a 5-second LTX-2.5 clip, about a fifth on a 10-second one. Switch it off to render this clip the usual way.');
  sw.hidden = false; row.hidden = false;
  // Until the one-time download is done, look again every 20 s so the line moves on by itself.
  if (!st.ready && !globalThis._proSpeedTimer) {
    globalThis._proSpeedTimer = setTimeout(() => { globalThis._proSpeedTimer = null; proStatusRefresh(); }, 20000);
  }
}
function proSpeedChanged() {
  const box = document.getElementById('proSpeedToggle');
  try { localStorage.setItem(PRO_SPEED_KEY, box.checked ? 'on' : 'off'); } catch (e) { /* this render only */ }
  proSpeedRender();
}

// ---- What Pro turns on, shown where it applies ---------------------------------------------------
// One list, in the Pro ledger's own words (pro/ledger.json, features[].description), of the Pro rows that are
// live in this Studio build. Not listed: rows the ledger calls "not available yet", test switches, prototypes,
// anything going Free. AI fill has no ledger row yet; its words are the 5.0.16 release notes.
// Display text only. Whether a feature runs is decided where it runs, from the signed licence.
const PRO_FEATURES = [
  { id: 'ltx-faster', name: T('settings.faster_ltx_clips', 'Faster LTX clips'),
    line: T('settings.ltx_2_5_clips_render', 'LTX-2.5 clips render with less waiting: about a quarter less on a 5-second clip and about a fifth less on a 10-second clip (Balanced; measured on an M4 Max 64 GB). On by default; switch it off for any render. Macs with 16 GB or more.'),
    short: T('settings.about_a_quarter_less_waiting_2', 'about a quarter less waiting on LTX clips, on Macs with 16 GB or more.'),
    where: T('settings.beside_quality_on_ltx_2', 'Beside Quality, on LTX-2.5 Balanced') },
  { id: 'h3-flex-turbo', name: T('settings.faster_flex_turbo', 'Faster Flex Turbo'),
    line: T('settings.flex_turbo_renders_about_1', 'Flex Turbo renders about 1.5× faster than Flex on Free (M4 Max 64 GB; Macs with 36 GB or more). A little softer; for drafts.'),
    short: 'about 1.5× faster than Flex on Free. A little softer; for drafts.',
    where: T('settings.model_menu_faster_flex_turbo', 'Model menu, Faster Flex Turbo (needs Hailuo H3)') },
  { id: 'ph3-image-fast-pro', name: T('settings.faster_fast_mode_in_images', 'Faster Fast mode in Images'),
    line: T('settings.a_quicker_fast_mode_for', 'A quicker Fast mode for P-H3-Image: about 22 s for the next picture on a 64 GB M4 Max, at a smaller size. Free keeps its own 56 s default.'),
    short: T('settings.fast_makes_the_next_picture', 'Fast makes the next picture in about 22 s on a 64 GB M4 Max, at a smaller size. Free keeps its own 56 s default.'),
    where: T('settings.images_p_h3_image_version', 'Images, P-H3-Image, Version Fast') },
  { id: 'vertical-ai-fill', name: T('settings.make_it_vertical_ai_fill', 'Make it vertical (AI fill)'),
    line: T('settings.ai_paints_real_picture_above', 'AI paints real picture above and below, as if the clip was shot tall. For Macs with 48 GB or more.'),
    short: T('settings.ai_paints_real_picture_above_2', 'AI paints real picture above and below. For Macs with 48 GB or more.'),
    where: T('settings.more_on_any_finished_clip', 'More, on any finished clip') },
  { id: 'remote', name: T('settings.remote_control', 'Remote Control'),
    line: T('settings.start_and_watch_renders_from', 'Start and watch renders from your iPhone while your Mac does the work. Remote is part of Pro: get the Phosphene Remote app on the App Store and connect it to this Mac.'),
    short: T('settings.start_and_watch_renders_from_2', 'start and watch renders from your iPhone with the Phosphene Remote app.'),
    where: T('settings.remote_control_in_the_left', 'Remote Control, in the left bar') },
];
// Not a feature with a spot of its own, so only the two lists carry it.
const PRO_SUPPORT = { name: T('settings.priority_support', 'Priority support'), line: T('settings.pro_questions_and_feature_requests', 'Pro questions and feature requests go to the front of the queue.') };
const proFeature = id => PRO_FEATURES.find(f => f.id === id) || null;
const proIsOn = () => !!document.documentElement.dataset.pro;
// The words a spot shows, for who is looking: a Pro member is told it is on, a Free user what Pro adds.
const proLeadText = on => on ? T('settings.on_with_your_pro', 'On with your Pro:') : T('settings.pro_adds', 'Pro adds:');
function proSpotText(id, on = proIsOn()) {
  const f = proFeature(id);
  if (!f) return '';
  return proLeadText(on) + ' ' + f.short;
}
// The same sentence as markup: the lead in Pro violet (.pro-lead), the rest in the line's own colour.
function proNoteHtml(text) {
  const m = /^(Pro adds:|On with your Pro:)\s*/.exec(String(text || ''));
  if (!m) return escapeHtml(String(text || ''));
  return `<span class="pro-lead">${m[1]}</span> ` + escapeHtml(text.slice(m[0].length));
}
// Every [data-pro-feature] spot: a chip reads "Pro" for Free and "Pro ✓" for a member, with the line as its
// tooltip; a hint line (.pro-spot) shows the sentence, and data-pro-when="selectId=value" shows it only then.
function proMarksApply(root = document) {
  const on = proIsOn();
  root.querySelectorAll('[data-pro-feature]').forEach(el => {
    const f = proFeature(el.dataset.proFeature);
    if (!f) return;
    const text = proSpotText(f.id, on);
    if (el.classList.contains('pro-spot')) {
      el.innerHTML = proNoteHtml(text);
      const w = (el.dataset.proWhen || '').split('=');
      const sel = w[0] ? document.getElementById(w[0]) : null;
      el.hidden = !!(sel && sel.value !== w[1]);
    } else {
      el.textContent = on ? 'Pro ✓' : 'Pro';
      el.classList.toggle('pro-on', on);
      el.title = text;
    }
  });
}
document.addEventListener('change', e => {
  if (e.target && e.target.id && document.querySelector(`[data-pro-when^="${e.target.id}="]`)) proMarksApply();
});
function proFeatureListHtml() {
  const on = proIsOn();
  const item = f => `<li><p class="pro-feature-name">${escapeHtml(f.name)}${f.id ? `<span class="pro-tag${on ? ' pro-on' : ''}">${on ? 'Pro ✓' : 'Pro'}</span>` : ''}</p>`
    + `<p class="pro-feature-line">${escapeHtml(f.line)}</p>`
    + (f.where ? `<p class="pro-feature-where">${T('settings.where', 'Where:')} <b>${escapeHtml(f.where)}</b></p>` : '') + '</li>';
  return '<ul class="pro-feature-list">' + PRO_FEATURES.map(item).join('') + item(PRO_SUPPORT) + '</ul>';
}
let proVersion = '';
function proFeatureListRender() {
  if (!proVersion) {   // asked once, the first time a list opens (the version pill reads the same route)
    proVersion = ' ';
    fetch('/version', { cache: 'no-store' }).then(r => r.json())
      .then(v => { proVersion = (v && v.local_version) || ' '; proFeatureListRender(); }).catch(() => {});
  }
  const ver = proVersion.trim();
  const on = proIsOn();
  const title = document.getElementById('yourProTitle');
  if (title) title.textContent = on ? T('settings.your_pro', 'Your Pro') : T('settings.what_pro_turns_on', 'What Pro turns on');
  const lead = document.getElementById('yourProLead');
  if (lead) lead.textContent = on
    ? T('settings.your_licence_turns_these_on', 'Your licence turns these on') + (ver ? T('settings.in_phosphene', ' in Phosphene ') + ver : '') + '.'
    : T('settings.a_pro_licence_turns_these', 'A Pro licence turns these on') + (ver ? T('settings.in_phosphene', ' in Phosphene ') + ver : '') + T('settings.everything_else_stays_free', '. Everything else stays free.');
  const buy = document.getElementById('yourProBuy');
  if (buy) buy.hidden = on;
  ['yourProList', 'proFeatureList'].forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = proFeatureListHtml(); });
}
function openYourPro(ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const modal = document.getElementById('yourProModal');
  if (!modal || modal.style.display !== 'none') return;
  proFeatureListRender();
  modal.style.display = 'flex';
  modal.querySelector('.dlg').focus({preventScroll: true});
}
function closeYourPro() {
  const modal = document.getElementById('yourProModal');
  if (modal) modal.style.display = 'none';
}
// Settings > Pro on Pinokio: the Studio takes the licence key itself (pro_licence.py).
function proLicenceShow(doc) {
  const st = document.getElementById('proLicenceState'); if (!st) return;
  const lic = (doc && doc.licence) || doc || {};
  const pro = !!(doc && (doc.pro_status ? doc.pro_status.active : doc.active)) || !!lic.pro;
  const via = lic.via;
  let msg = (doc && doc.message) || lic.note || '';
  if (!msg) msg = pro ? (via === 'app' ? T('settings.pro_is_on_through_the', 'Pro is on through the Phosphene Mac app on this Mac.') : T('settings.pro_is_on_for_this', 'Pro is on for this Mac.')) : T('settings.free_paste_the_licence_key', 'Free. Paste the licence key from your purchase email to turn Pro on.');
  st.textContent = msg;
  st.dataset.on = pro ? '1' : '';
  const own = !!lic.licensed;
  // The key box is hidden only when this Studio already holds its own working licence; borrowed Pro (the Mac app's pass) keeps it.
  document.getElementById('proLicenceForm').hidden = pro && own;
  const restore = document.getElementById('proLicenceRestore'); if (restore) restore.hidden = pro && own;
  document.getElementById('proLicenceActions').hidden = !own;
}
async function proLicenceLoad() {
  try { const r = await fetch('/pro/licence', { cache: 'no-store' }); if (r.ok) { const l = await r.json(); proLicenceShow({ licence: l }); } } catch (_) {}
}
async function proLicencePost(path, body) {
  const go = document.getElementById('proLicenceGo'); if (go) go.disabled = true;
  try {
    const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: body || '' });
    const d = await r.json(); proLicenceShow(d);
    if (d.ok || d.licence_changed !== false) await proStatusRefresh();
    return d;
  } catch (_) { proLicenceShow({ message: T('settings.could_not_reach_the_studio', 'Could not reach the Studio.') }); } finally { if (go) go.disabled = false; }
}
async function proLicenceActivate(ev) {
  ev.preventDefault();
  const inp = document.getElementById('proLicenceKey'); const key = inp.value.trim(); if (!key) return;
  proUiEvent({where: 'pro_modal', action: 'key'});
  const d = await proLicencePost('/pro/activate', 'key=' + encodeURIComponent(key));
  if (d && d.ok) inp.value = '';
}
const proLicenceRefresh = () => proLicencePost('/pro/refresh');
const proLicenceDeactivate = () => proLicencePost('/pro/deactivate');
// What Pro does on THIS Mac, in the Pro ledger's memory floors: LTX faster from 16 GB, H3 video faster from 36 GB
// (the Mac app's H3 lane; Faster Flex Turbo on a Pinokio install), AI fill from 48 GB. Unknown memory: no line.
function proThisMacRender() {
  const el = document.getElementById('proThisMac');
  if (!el) return;
  const gb = Math.round(Number((typeof BOOT !== 'undefined' && BOOT.tier && BOOT.tier.ram_gb) || 0));
  if (!gb) { el.hidden = true; return; }
  const got = [];
  if (gb >= 16) got.push(T('pro.this_mac_ltx', 'faster LTX video'));
  if (gb >= 36) got.push(isMacApp() ? T('pro.this_mac_h3', 'a third less waiting on Hailuo H3 video') : T('pro.this_mac_flex_turbo', 'Faster Flex Turbo for Hailuo H3'));
  if (gb >= 48) got.push(T('pro.this_mac_fill', 'AI fill for Make it vertical'));
  const head = `<b>${T('pro.this_mac_head', 'On this Mac ({gb} GB)', {gb: gb})}</b> `;
  el.innerHTML = got.length
    ? head + T('pro.this_mac_gives', 'Pro gives you {perks}, plus the iPhone Remote, new features first and priority support.', {perks: got.join(T('pro.this_mac_join', ', '))})
    : head + T('pro.this_mac_support_only', 'Pro is mostly support today: the iPhone Remote, new features first and priority support. The speed-ups need 16 GB or more for now.');
  el.hidden = false;
}
// Restore Pro (a mailto) and the key form: only that the button was used is counted, never the key.
document.addEventListener('click', e => {
  const a = e.target && e.target.closest ? e.target.closest('#proModal [data-pro-action="restore"]') : null;
  if (a) proUiEvent({where: 'pro_modal', action: 'restore'});
}, true);
function closeProModal() {
  const modal = document.getElementById('proModal');
  if (modal.style.display === 'none') return;
  proUiEvent({where: 'pro_modal', action: 'close'});
  modal.style.display = 'none';
  const vid = document.getElementById('proCompareVideo'); if (vid) vid.pause();
  if (proReturnFocus && proReturnFocus.isConnected) proReturnFocus.focus();
}

Object.assign(globalThis, {
  proSpeedRender, proSpeedChanged,
  downloadFirstVideo,
  openProModal, closeProModal, proStatusRefresh,
  openYourPro, closeYourPro, proMarksApply, proSpotText, proNoteHtml,
  proLicenceActivate, proLicenceRefresh, proLicenceDeactivate,
  pushSubscription, pushToggle, pushTest,
  appearanceGet, applyAppearance, setAppearance,
  renderNotifyState, toggleNotify, askNotifyPermission,
  updateModelsCard, dismissModelsCard, applyTierGates, openTierModal,
  closeTierModal, openBugModal, closeBugModal, submitBugReport, copyBugDiagnostics, bugWhatInput,
  openSettingsModal, renderModelsDriveRows, modelsDriveView, toggleAnalytics, saveAnalyticsKey, clearAnalyticsKey,
  toggleSpicyMode, onTokenInput, toggleTokenVisibility, testToken,
  clearToken, closeSettingsModal, applySettings, _applyHdrPillAvailability,
  // inline-handler targets: generated markup resolves these through the
  // global scope (the v4.9.0 regression, PR #69)
  cleanupSupersededModels,
});

async function downloadFirstVideo() {
  try {
    const response = await fetch('/models/video', {method: 'POST'});
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'Download did not start. Try again.');
    phosToast(T('settings.video_models_queued_progress_appears', 'Video models queued. Progress appears here.'));
    poll();
  } catch (e) { phosToast(e.message || String(e), {kind: 'danger'}); }
}
