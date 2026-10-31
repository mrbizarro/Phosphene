// webapp/js/main.js — the page's kickoff sequence (the old '====== Init
// ======' tail of the inline block), extracted in slice 3 of
// docs/ARCHITECTURE.md. Its <script type="module"> tag is deliberately
// the LAST one: in the single-block days every function was hoisted, so
// the first poll() could never race a definition. Split into modules,
// the only way to keep that guarantee is for the kickoffs to run after
// every module has evaluated — which 'last module tag' provides. Any
// future 'call this once at startup' line belongs HERE, not at the top
// level of a feature module.
// SYS-25: finishes the mode-bar tab semantics the workflow-tabs nav
// already has (SYS-25 partial, this pass). role=tab on every chip, and a
// MutationObserver keeps aria-selected in step with the existing .active
// class toggle -- WITHOUT touching the ~6 places that set .active
// (boot.js's setMode/applyTierGates, characters.js's renderCharacterStrip,
// engines.js's setEngine, settings.js's tier-gate pass), several of which
// are other packages' territory in this same mega-ship pass. A screen
// reader had no way to know this was a tab strip or which mode was active.
function _wireModeGroupTabSemantics() {
  const group = document.getElementById('modeGroup');
  if (!group) return;
  const sync = (btn) => btn.setAttribute('aria-selected', btn.classList.contains('active') ? 'true' : 'false');
  group.querySelectorAll('.mode-chip').forEach(btn => {
    btn.setAttribute('role', 'tab');
    sync(btn);
  });
  new MutationObserver((muts) => {
    muts.forEach(m => { if (m.target.classList.contains('mode-chip')) sync(m.target); });
  }).observe(group, { attributes: true, attributeFilter: ['class'], subtree: true });
}

// ====== Init ======
// Licence screens (4.19.8): wrap fetch() first, so a 451 licence_required
// from any later call opens the model's licence instead of a bare error.
installLicenceGate();
musicInit();
_wireModeGroupTabSemantics();
// A mode greyed because H3 Flex is the pick refuses the click (capture phase,
// ahead of the chip's own handler) and says why.
document.getElementById('modeGroup')?.addEventListener('click', modeChipEngineClick, true);
// VC-34: the Tier quick-link next to the Quality label. Tier is fixed at
// boot (RAM doesn't change at runtime), so this is a one-time read of the
// bootstrap payload — no reason to wait for the first /status poll.
(() => {
  const el = document.getElementById('qualityTierLinkLabel');
  if (el && BOOT.tier && BOOT.tier.label) el.textContent = BOOT.tier.label;
})();
// VC-16: the Enhance tooltip hardcoded "LTX 2.3 was trained on" — this
// panel has served LTX 2.5 as the default generation since v4.0.0. Also
// fixed at boot: the active generation doesn't change without a restart.
(() => {
  const btn = document.getElementById('enhanceBtn');
  if (!btn) return;
  const gen = ((BOOT.ltx || {}).generation === 'ltx23') ? '2.3' : '2.5';
  btn.title = `Use Gemma to rewrite your prompt in the style LTX ${gen} was trained on`;
})();
// Skip poll when the tab is backgrounded — at 1.5s cadence with a fan-
// spinning render in the background, every saved request matters. Pinokio
// users park the panel in a tab and switch to other apps for the 5–20 min
// a render takes; nothing in the UI needs updating until they come back.
// `visibilitychange` fires immediately when the user returns so the chrome
// catches up on the first frame.
setInterval(() => { if (!document.hidden) poll(); }, 1500);
document.addEventListener('visibilitychange', () => { if (!document.hidden) poll(); });

// Delegated click handler for the failed Now-card action buttons.
// Inline `onclick` attributes on these buttons were fragile — the
// failed branch of poll() rewrites .ttl's innerHTML every 1.5s, and a
// click that landed mid-rewrite could be lost. A single delegated
// listener on document survives every rewrite + costs nothing.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-action="retry"], [data-action="retry-smaller"], [data-action="dismiss"], [data-action="stop-early"], [data-action="resume"], [data-action="split"], [data-action="reopen"], [data-action="repair-engine"], [data-action="repair-h3"]');
  if (!btn) return;
  e.stopPropagation();
  e.preventDefault();
  if (btn.dataset.action === 'resume') { if (typeof togglePause === 'function') togglePause(); return; }
  // 4.17.4: the engine-env bar and a venv_broken failure card. Not tied to a
  // job id - the bar has none - so it is handled before the id lookup.
  if (btn.dataset.action === 'repair-engine') {
    if (typeof engineRepairStart === 'function') engineRepairStart();
    return;
  }
  // 4.19.5: the same for the Hailuo H3 venv (POST /h3/repair).
  if (btn.dataset.action === 'repair-h3') {
    if (typeof h3RepairStart === 'function') h3RepairStart();
    return;
  }
  // A third delegated action in the same row, for the same reason the other
  // two are delegated: poll() rewrites this element every 1.5 s, and an inline
  // handler would be lost to that race mid-click.
  if (btn.dataset.action === 'stop-early') { stopEarly(); return; }
  const actions = btn.closest('.now-card-actions');
  const id = actions ? actions.dataset.jobId : '';
  if (!id) return;
  if (btn.dataset.action === 'split' || btn.dataset.action === 'reopen') {
    // 4.17.3: a Lip-sync clip refused for its length on a Compact Mac. Reopen
    // THAT job (its song, start, picture, settings) in the Lip-sync form —
    // never whatever the form holds now — where the length note offers
    // "Split into N s clips" against the job's own audio.
    if (typeof openFailedJobInForm === 'function') openFailedJobInForm(id);
  } else if (btn.dataset.action === 'retry') {
    if (typeof retryJob === 'function') retryJob(id);
  } else if (btn.dataset.action === 'retry-smaller') {
    // SYS-07 / VC-08: "Retry smaller" for the GPU-watchdog / OOM failure
    // classes — re-queues one quality rung down at a scaled canvas + roughly
    // half the length, instead of the identical job that just died.
    if (typeof retryJob === 'function') retryJob(id, { smaller: true });
  } else {
    window._dismissedFailureId = id;
    if (typeof poll === 'function') poll();
  }
});

// VA-17 / H3-11 — the One Shot recovery bar's two buttons. Delegated for
// the same reason as the block above: poll() can repaint the bar (or hide
// it) between a click landing and its handler running.
document.addEventListener('click', (e) => {
  const btn = e.target.closest('[data-action="take-resume"], [data-action="take-join-partial"]');
  if (!btn) return;
  e.preventDefault();
  const bar = document.getElementById('oneShotRecoveryBar');
  const path = bar ? bar.dataset.path : '';
  if (!path) return;
  if (btn.dataset.action === 'take-resume' && typeof takeResumeFromPath === 'function') {
    takeResumeFromPath(path);
  } else if (btn.dataset.action === 'take-join-partial' && typeof takeJoinPartialFromPath === 'function') {
    takeJoinPartialFromPath(path);
  }
});

poll();
setMode('t2v');
setAspect('landscape');         // sets aspect first so the default preset orients correctly
setQuality('balanced');         // bundles quality + dims; respects current aspect
applyTierTimes();               // no-op for LTX since v4.0 — the tier table owns those subtitles
if (typeof applyExtendPillPrices === 'function') applyExtendPillPrices();  // VA-12
renderCharacterStrip();         // the generation-scoped character quality ladder
// Engine picker — re-apply the last-used engine after the boot sequence above
// has settled the mode. setEngine() re-runs every gate (capable / installed /
// mode), so a stale localStorage value from a machine that has since lost the
// pack just lands back on LTX. The tier was restored at parse time (see
// _restoreH3TierEarly).
(function restoreEngineChoice() {
  let engine = null;
  try { engine = localStorage.getItem(H3_ENGINE_LS_KEY); } catch (e) {}
  setEngine(engine === 'h3' ? 'h3' : 'ltx', { persist: false });
})();
updateCustomizeSummary();
updateDerived();

// VC-38: restore a saved draft AFTER the hardcoded t2v/landscape/balanced
// defaults above so it can actually override them, BEFORE anything else
// below touches the form. Autosave wiring runs regardless of whether a
// draft existed to restore — the point is that the NEXT reload has one.
if (typeof restoreDraftOnBoot === 'function') { try { restoreDraftOnBoot(); } catch (e) {} }
if (typeof draftAutosaveInstall === 'function') { try { draftAutosaveInstall(); } catch (e) {} }

// Wire the picker components (I2V image + FFLF start/end) and seed the
// "Recent uploads" strip. The strip is shared across all three pickers,
// so dropping a new image in one slot makes it instantly clickable in
// the other two.
PICKERS.forEach(pickerWire);
refreshUploadsStrip();
// Refresh the strip whenever a render finishes (queue/history changes
// don't fire here), and whenever the user opens FFLF — covers the case
// where they uploaded something via I2V, then switched to FFLF.
document.querySelectorAll('#modeGroup .pill-btn').forEach(b => b.addEventListener('click', refreshUploadsStrip));


// ============================================================================
// Workflow tabs — Manual / Characters / Train
// ============================================================================
// The in-panel agentic chat surface was removed 2026-05-15 (tag
// pre-agent-removal-2026-05-15). External agents drive Phosphene via the
// HTTP API in docs/API.md. The tab switcher now flips between three
// surfaces: the manual generate form, the Characters tab (LoRA pair +
// prompt + ship), and Train (character LoRA training).


// The completion-alert switch is read by the poller from the settings cache,
// which used to be filled only when the Settings modal opened. One fetch at
// boot; the modal refreshes it as before.
(async () => {
  try {
    if (!globalThis._settingsCache) {
      const r = await fetch('/settings');
      if (r.ok) globalThis._settingsCache = await r.json();
    }
  } catch (e) {}
})();

// The appearance is applied at boot, before anyone looks — a stored "light"
// that arrived after the first paint would flash the dark palette first.
try { if (typeof applyAppearance === 'function') applyAppearance(); } catch (e) {}
// The player scales to leave the Outputs pane a header and a row of cards on
// every screen (queue.js, fitStagePlayer).
initStagePlayerFit();
// install_step `first_use`: one report the first time anyone clicks or types
// on the page (health.js).
try { armFirstUseReport(); } catch (e) {}
