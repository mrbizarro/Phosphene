// webapp/js/engines.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
// ============================================================================
// Engine registry — the header switcher and every gate behind it
// ============================================================================
// An engine is not a quality setting: different model, its own venv, its own
// subprocess, its own geometry rules, its own set of modes. The registry
// (BOOT.engines, from the server-side ENGINES table) is the single source for
// all of it, so adding a third engine is one Python entry plus one <symbol> —
// nothing below learns any engine's name.
//
// The four gate states a segment has to carry, unchanged from the two-engine
// version and now generic:
//   not capable   → not rendered at all (a 32 GB Mac never learns H3 exists,
//                   and with nothing left to choose the switcher disappears)
//   capable, not installed → dashed segment + a cost badge; the click opens
//                   the install card. An offer, never a dead button.
//   installed but broken   → same shape, badge says "repair" and the card
//                   never mentions a download (the v3.4.0 report, verbatim)
//   installed, wrong mode  → inert; the reason lands in #engineRowNote
//
// The truth is still server-side: make_job re-runs every one of these against
// the same table, so a stale tab, a Load-Params replay of an old sidecar, or a
// direct curl to /queue/add can never render on an engine this Mac (or this
// mode) can't actually run.
globalThis.ENGINES = (BOOT.engines || []);
globalThis.ENGINE_DEFAULT = (BOOT.default_engine || 'ltx');
globalThis.H3_ENGINE_LS_KEY = 'phos_video_engine';

function engineById(id) {
  return ENGINES.find(e => e.id === id) || null;
}
function defaultEngine() {
  return engineById(ENGINE_DEFAULT) || ENGINES[0]
      || { id: 'ltx', label: 'LTX', builtin: true, strip_label: 'Quality' };
}

// An engine's live capability block. `probe` names the bootstrap key that
// carries it (BOOT.h3 for Hailuo H3), so this function never mentions an
// engine by name. The built-in engine has nothing to probe: it IS the panel.
// An `announced` engine has no weights yet, so it is capable of nothing.
function engineStatus(e) {
  if (!e) return { capable: false, available: false };
  if (e.state === 'announced') return { capable: false, available: false, announced: true };
  if ((e.surfaces || []).indexOf('image') !== -1) return imageModelStatus(e);
  if (!e.probe) return { capable: true, available: true };
  return (window._ENGINE_PROBES && window._ENGINE_PROBES[e.probe]) || { capable: false, available: false };
}

// Modes this engine may serve. `excluded_modes` exists because some UI intents
// resolve to a mode an engine nominally supports but must not get: 'character'
// submits mode=t2v but stacks LTX LoRAs, and 'i2v_clean_audio' muxes an
// external track onto LTX video. Mirrors engine_serves_mode() in Python, which
// is the copy that decides.
function engineServesMode(e, mode) {
  if (!e) return false;
  // 'oneshot' is a UI mode that submits t2v (or i2v with an anchor) plus
  // take_seconds; both engines render it — H3 as 15 s parts, LTX as 10 s —
  // so it is asked about as the mode it ships under. Without this the chip
  // would be folded as eng-foreign on H3, the engine the feature was built on.
  if (mode === 'oneshot') mode = 't2v';
  if ((e.excluded_modes || []).indexOf(mode) !== -1) return false;
  if (!e.modes) return true;
  return e.modes.indexOf(mode) !== -1;
}

// Which workflow tab an engine belongs to. The switcher is a Video-form
// control; showing it while the user is in Images or Train Character would be
// offering a choice that changes nothing.
function _currentSurface() {
  const wf = (document.body.dataset.workflow || 'manual').toLowerCase();
  if (wf === 'audio' && musicComposeActive()) return 'music';
  // 'storyboard' MUST be in this map. Without it the `|| 'video'` fallback
  // leaves the engine switcher visible in a tab that has no engine choice to
  // offer — the film decides per shot, not a global toggle.
  return ({ manual: 'video', studio: 'image', audio: 'audio', train: 'train',
            storyboard: 'storyboard', oneshot: 'oneshot' })[wf] || 'video';
}
function engineOnSurface(e) {
  return (e.surfaces || ['video']).indexOf(_currentSurface()) !== -1;
}

// An engine gets a segment when this Mac could plausibly run it one day: the
// built-in always, an announced one always (that IS the news), and an optional
// pack only when the hardware is capable. Not-capable is not rendered — a
// permanently dead engine in the chrome is noise for the ~80% under 64 GB.
function engineRenderable(e) {
  if (!engineOnSurface(e)) return false;
  if (e.builtin || e.state === 'announced') return true;
  // Image models stay listed when they cannot fit this Mac, dimmed with the
  // Mac size that runs them (the Images tab's memory-fit rule, 2 Sep 2026).
  if ((e.surfaces || []).indexOf('image') !== -1) return true;
  return !!engineStatus(e).capable;
}

// ---- Capability probes ------------------------------------------------------
// One entry per engine whose registry row names a `probe`. /status carries a
// fresh block every tick, so an install finishing in the Pinokio sidebar
// unlocks its engine without a panel restart (the same contract the Q8
// download already has with the High pill). A new engine adds a key here the
// day it has a status function; nothing else in this file changes.
window._ENGINE_PROBES = {
  music: (BOOT.music || { capable: false, available: false, estimates: {} }),
  h3: (BOOT.h3 || { capable: false, available: false, tiers: [] }),
};
// H3 keeps its own binding because the H3 tier / Turbo / export code below is
// H3-specific by nature (its own geometry table, its own adapter) and reads it
// on nearly every line. It is the SAME object the probe map holds.
globalThis.H3 = window._ENGINE_PROBES.h3;

// ---- H3 render shape: two axes, one cell -----------------------------------
// The panel speaks (quality, length); the wire speaks a composite `h3_tier`
// key. These four helpers are the whole translation layer, and none of them
// invents a shape — every one of them looks up a cell the server built.

// A tier key of ANY vintage → the cell key it means. The alias map is the
// server's (H3_TIER_ALIASES), shipped in the status block, so the legacy names
// exist in exactly one place: hq_5s → standard_5s, wide_5s → high_5s,
// long_10s → standard_10s, and so on.
function h3ResolveTierKey(key) {
  const k = String(key || '').trim().toLowerCase();
  if (!k) return '';
  return ((H3.aliases || {})[k]) || k;
}
function h3TierByKey(key) {
  const k = h3ResolveTierKey(key);
  return (H3.tiers || []).find(t => t.key === k) || (H3.tiers || [])[0] || null;
}
// The cell at (quality, length), or null. This is what both strips read: a chip
// prints the eta of the cell it WOULD select, which is how the estimate stays
// live as either axis moves.
function h3CellFor(quality, length) {
  return (H3.tiers || []).find(t => t.quality === quality && t.length === length) || null;
}
function h3CurrentQuality() {
  return (document.getElementById('h3_quality') || {}).value
      || H3.default_quality || 'draft';
}
function h3CurrentLength() {
  return (document.getElementById('h3_length') || {}).value
      || H3.default_length || '3s';
}
// The cell the form is currently pointing at. Falls back through the composite
// key so a state restored from an old sidecar (which has no axes) still lands.
function h3CurrentCell() {
  return h3CellFor(h3CurrentQuality(), h3CurrentLength())
      || h3TierByKey((document.getElementById('h3_tier') || {}).value);
}

// Restore the saved shape into the hidden inputs HERE, at parse time — before
// the boot sequence at the bottom of this script runs setMode('t2v'), which
// reaches setEngine → setH3Tier and would otherwise persist the HTML default
// over the user's choice. (Caught in validation: the tier reset to Draft on
// every reload.)
(function _restoreH3TierEarly() {
  const inp = document.getElementById('h3_tier');
  if (!inp) return;
  // Two axes now, but the SINGLE key is still what older installs persisted, so
  // read the axes first and fall back to it. A user who reloads after this
  // update keeps the shape they were on rather than being reset to Draft 3s.
  let savedQ = null, savedL = null, saved = null;
  try {
    savedQ = localStorage.getItem('phos_h3_quality');
    savedL = localStorage.getItem('phos_h3_length');
    saved = localStorage.getItem('phos_h3_tier');
  } catch (e) {}
  let cell = (savedQ && savedL) ? h3CellFor(savedQ, savedL) : null;
  if (!cell && saved) {
    const k = h3ResolveTierKey(saved);
    cell = (H3.tiers || []).find(t => t.key === k) || null;
  }
  if (cell) {
    inp.value = cell.key;
    const q = document.getElementById('h3_quality');
    const l = document.getElementById('h3_length');
    if (q) q.value = cell.quality;
    if (l) l.value = cell.length;
  }
  // Same treatment for the export canvas, and for the same reason.
  const up = document.getElementById('h3_upscale');
  if (!up) return;
  let savedUp = null;
  try { savedUp = localStorage.getItem('phos_h3_upscale'); } catch (e) {}
  const allowed = H3.upscale_modes || ['off', 'fit_720p', 'fit_1080p'];
  // The after-the-draft Upscale & Face Fix is an extra job, so a reload
  // starts with it OFF even if an older panel saved it as the preference.
  if (savedUp && savedUp !== 'ltx_x2' && allowed.indexOf(savedUp) !== -1) up.value = savedUp;
  // And again for the sampler-depth pills, same reason.
  const st = document.getElementById('h3_steps');
  if (!st) return;
  let savedSt = null;
  try { savedSt = localStorage.getItem('phos_h3_steps'); } catch (e) {}
  if (savedSt) st.value = h3NormalizeSteps(savedSt);
  // Turbo is folded into the Speed switch (Fast | Best): the form never posts
  // it. renderH3Turbo() writes #h3_tristep from the saved speed.
  const tb = document.getElementById('h3_turbo');
  if (tb) tb.value = '0';
})();

// What the export pass will DO to the selected tier's canvas, in one line under
// the Export row. Every tier used to advertise itself as "768×448 · 124f" and
// nothing said which of them came back with black bars on it — the answer
// depends on BOTH the tier's aspect and the export target, so it is rendered
// here rather than baked into a chip. The strings come from the server
// (`tier.export_note[mode]`, generated from compute_upscale_plan itself), so
// this function only picks one; it never phrases anything.
// Reads the QUALITY's export_note, not the cell's: what the export pass does to
// a frame depends only on the canvas, so the sentence lives on the canvas and
// /status doesn't repeat three strings across twelve cells on every tick.
function h3QualityByKey(key) {
  return (H3.qualities || []).find(q => q.key === key) || (H3.qualities || [])[0] || null;
}
function h3LengthByKey(key) {
  return (H3.lengths || []).find(l => l.key === key) || (H3.lengths || [])[0] || null;
}
function _h3SyncExportNote() {
  const el = document.getElementById('h3ExportNote');
  if (!el) return;
  const q = h3QualityByKey(h3CurrentQuality());
  const mode = (document.getElementById('h3_upscale') || {}).value || 'off';
  const txt = (q && q.export_note && q.export_note[mode]) || '';
  el.textContent = txt;
  el.hidden = !txt;
}

// Export canvas for an H3 render. Separate from the LTX `upscale` control
// (which is data-ltx-only and folds away on H3) so one pill never means two
// things. Server-side make_job re-validates — a stale tab must never win.
// "ltx_x2" is the stored value of the OPTIONAL after-the-draft Upscale & Face
// Fix (a checkbox under the pills): the draft ships as rendered and the fix is
// queued behind it. It is never saved as the preference; the size the user had
// is, so unticking the box returns to it.
function setH3Upscale(mode) {
  const allowed = H3.upscale_modes || ['off', 'fit_720p', 'fit_1080p'];
  const v = allowed.indexOf(mode) !== -1 ? mode : (H3.default_upscale || 'fit_720p');
  const inp = document.getElementById('h3_upscale');
  if (inp) inp.value = v;
  const fix = v === 'ltx_x2';
  const shown = fix ? 'off' : v;
  const grp = document.getElementById('h3UpscaleGroup');
  if (grp) grp.classList.toggle('is-overridden', fix);
  document.querySelectorAll('#h3UpscaleGroup [data-h3-upscale]').forEach(b =>
    b.classList.toggle('active', b.dataset.h3Upscale === shown));
  const box = document.getElementById('h3FaceFixAfter');
  if (box) box.checked = fix;
  const box2 = document.getElementById('h3FaceFixFooter');
  if (box2) box2.checked = fix;
  _h3SyncFaceFixEta();
  const row = document.getElementById('h3FaceFixAfterRow');
  if (row) row.hidden = allowed.indexOf('ltx_x2') === -1;
  if (!fix) { try { localStorage.setItem('phos_h3_upscale', v); } catch (e) {} }
  _h3SyncExportNote();
  if (typeof updateDerived === 'function') { try { updateDerived(); } catch (e) {} }
  // The export target is what the "After the render" summary SAYS, and that
  // section is closed by default — so the line has to be rewritten here or it
  // keeps advertising 720p for a render that ships 1080p. (It was stale in the
  // old Customize summary for the same reason, where it mattered less.)
  if (typeof updateCustomizeSummary === 'function') { try { updateCustomizeSummary(); } catch (e) {} }
}
function setH3FaceFixAfter(on) {
  if (on) { setH3Upscale('ltx_x2'); return; }
  let size = null;
  try { size = localStorage.getItem('phos_h3_upscale'); } catch (e) {}
  setH3Upscale(size && size !== 'ltx_x2' ? size : (H3.default_upscale || 'fit_720p'));
}
document.querySelectorAll('#h3UpscaleGroup [data-h3-upscale]').forEach(b => {
  b.onclick = () => setH3Upscale(b.dataset.h3Upscale);
});

// H3 orientation — a per-render flip of the resolved cell's canvas, not a new
// tier. The chips' estimates stay valid because a rotation changes no pixel
// count. Persisted like the other H3 sub-preferences.
function setH3Orientation(v) {
  const val = (v === 'portrait') ? 'portrait' : 'landscape';
  const inp = document.getElementById('h3_orientation');
  if (inp) inp.value = val;
  document.querySelectorAll('#h3OrientationGroup [data-h3-orientation]').forEach(b =>
    b.classList.toggle('active', b.dataset.h3Orientation === val));
  try { localStorage.setItem('phos_h3_orientation', val); } catch (e) {}
  _h3SyncOrientationSubs();
  if (typeof updateDerived === 'function') { try { updateDerived(); } catch (e) {} }
  // Orientation is part of the shape the closed Shot setup summary prints.
  if (typeof updateCustomizeSummary === 'function') { try { updateCustomizeSummary(); } catch (e) {} }
}
// Say the ACTUAL canvases the current cell would produce, both ways round.
function _h3SyncOrientationSubs() {
  const cell = (typeof h3CurrentCell === 'function') ? h3CurrentCell() : null;
  const land = document.getElementById('h3OrientLandSub');
  const port = document.getElementById('h3OrientPortSub');
  if (!cell) return;
  if (land) land.textContent = `${cell.width}×${cell.height}`;
  if (port) port.textContent = `${cell.height}×${cell.width} · vertical`;
}
document.querySelectorAll('#h3OrientationGroup [data-h3-orientation]').forEach(b => {
  b.onclick = () => setH3Orientation(b.dataset.h3Orientation);
});
(function _restoreH3Orientation() {
  let v = 'landscape';
  try { v = localStorage.getItem('phos_h3_orientation') || 'landscape'; } catch (e) {}
  const apply = () => setH3Orientation(v);
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else { apply(); }
})();

// Sampler depth for an H3 render. 'auto' = the tier's tuned count (stamped in
// H3_TIERS); a number overrides it for every window of the job. Server-side
// make_job clamps to 4-30 and re-validates — a stale tab must never win.
// Any count the server accepts (4-30) is kept: a replayed render pinned at 21
// or 7 must replay at 21 or 7, not silently at the target's Auto. No pill lights
// for such a count and the row hint names it instead.
function h3NormalizeSteps(v) {
  const t = String(v == null ? '' : v).trim().toLowerCase();
  if (/^\d+$/.test(t)) {
    const n = parseInt(t, 10);
    if (n >= 4 && n <= 30) return String(n);
  }
  return 'auto';
}
function setH3Steps(v) {
  v = h3NormalizeSteps(v);
  const inp = document.getElementById('h3_steps');
  if (inp) inp.value = v;
  let lit = false;
  document.querySelectorAll('#h3StepsGroup [data-h3-steps]').forEach(b => {
    const on = b.dataset.h3Steps === v;
    lit = lit || on;
    b.classList.toggle('active', on);
  });
  const hint = document.querySelector('#h3StepsRow .cz-label-hint');
  if (hint) {
    hint.textContent = lit ? "Auto = this shape's tuned count"
      : ('Pinned at ' + v + ' (from a saved render) · pick a pill to change');
  }
  // Mirror the resolved count into the shared hidden `steps` so the queue
  // card and the estimate line read the truth (make_job re-stamps anyway).
  const tier = h3CurrentCell();
  const s = document.getElementById('steps');
  if (s && tier) s.value = (v === 'auto') ? tier.steps : parseInt(v, 10);
  try { localStorage.setItem('phos_h3_steps', v); } catch (e) {}
  // A pinned depth changes the wall clock of EVERY cell, so both strips have to
  // re-price. This is the whole point of showing an estimate on the chips: it
  // has to answer "what would this cost me, as things stand right now".
  if (typeof renderH3Axes === 'function') { try { renderH3Axes(); } catch (e) {} }
  if (typeof renderH3Turbo === 'function') { try { renderH3Turbo(); } catch (e) {} }
  if (typeof updateDerived === 'function') { try { updateDerived(); } catch (e) {} }
  if (typeof updateCustomizeSummary === 'function') { try { updateCustomizeSummary(); } catch (e) {} }
}
document.querySelectorAll('#h3StepsGroup [data-h3-steps]').forEach(b => {
  b.onclick = () => {
    if (b.classList.contains('flex-locked')) {
      if (typeof phosToast === 'function') phosToast(H3_FLEX_SPEED_TIP, { kind: 'info' });
      return;
    }
    setH3Steps(b.dataset.h3Steps);
  };
});

// ---- Speed: ⚡ Fast | ✦ Best ------------------------------------------------
// ONE switch above the Quality cards (owner, 2026-09-17: "finally fast
// generations", and no messy UI):
//   ⚡ Fast — TaoMate's 3-step adapter on its own sigma ladder (3 forwards).
//            Draft, Standard and High, T2V and I2V. Needs the 180 MB adapter
//            and a runner with --sigma-subset; until then the Fast half says
//            what one click does (install / update the runner).
//   ✦ Best — the shape's own sampler, as before (Draft/Standard 8 forwards,
//            High 15, Native as tuned), and the Steps override.
// Default Fast once installed; remembered per browser (phos_h3_speed).
// Native and the dense 10 s single pass have not been validated on 3 steps,
// so they always render Best and say so under the switch.
//
// TURBO IS FOLDED IN. The old Standard | Turbo row is gone: Fast is the faster
// AND the owner-preferred recipe, and two speed controls — or two adapters on
// one render — is the confusion this switch exists to remove. The UI posts
// h3_turbo=0 always; the server still honours h3_turbo from the API (and
// never stacks it with Fast). A Turbo clip's Load Params / Finish maps to Fast.
function h3TurboState() {
  return (H3 && H3.turbo) || { available: false, supported: false, downloaded: false };
}
function h3TriStepState() {
  return (H3 && H3.tristep) || { available: false, supported: false, downloaded: false };
}
// lightx2v v1.3: what Best renders on the High canvas once its adapter is
// built (server: h3_cell_takes_v13 / make_job). Text and Image only; a pinned
// Steps count keeps the full sampler.
function h3V13State() {
  return (H3 && H3.v13) || { available: false, supported: false, downloaded: false };
}
function _h3V13Mode() {
  const m = (document.getElementById('mode') || {}).value || 't2v';
  return m === 't2v' || m === 'i2v';
}
// Pika H3 Flex (Pika Labs' six-step H3 student, running natively on Apple
// Silicon): the third Speed choice. Text and Image, every 5 s-window cell the
// server prices (cell.flex_min); never a default — only when picked.
function h3FlexState() {
  return (H3 && H3.flex) || { available: false, supported: false, downloaded: false };
}
// H3 Flex Turbo (5.0, Pro, preview): the faster Flex draft. Same cells as
// Flex (cell.flex_turbo_min).
function h3FlexTurboState() {
  return (H3 && H3.flex_turbo) || { available: false, supported: false, downloaded: false, pro: true, pro_active: false };
}
function h3FlexTurboOn(cell) {
  cell = cell || h3CurrentCell();
  return !!(cell && cell.flex_turbo_min != null && (typeof h3FlexServesMode !== 'function' || h3FlexServesMode(null, 'flex_turbo'))
            && h3FlexTurboState().available && h3SpeedPref() === 'flex_turbo');
}
function h3FlexOn(cell) {
  cell = cell || h3CurrentCell();
  return !!(cell && cell.flex_min != null && (typeof h3FlexServesMode !== 'function' || h3FlexServesMode(null, 'flex'))
            && h3FlexState().available && h3SpeedPref() === 'flex');
}

// ---- What H3 Flex can render, from the server (owner, 31 Oct 2026) ---------
// Flex and Flex Turbo render Text and Image only, and only on 5 s-window cells
// (not the dense single pass, not Fast HD). Anywhere else make_job quietly
// renders H3's own sampler and only says so after submit, so while Flex is the
// pick every control it cannot honour is greyed and refuses the click: the
// mode chips, the Quality/Length cells, Fast/Best and the Steps pills.
// Both facts are the server's: `modes` on the Flex status block (H3_FLEX_MODES)
// and `takes_flex` on every cell (h3_cell_takes_flex). No copy lives here, so a
// new Flex mode or cell lights up with no JS change. make_job keeps its own
// fallback as the safety net.
const H3_FLEX_LOCK_TIP = 'H3 Flex makes videos from text or an image. Switch to H3 for this.';
const H3_FLEX_CELL_TIP = 'H3 Flex runs on 5-second windows. Switch to H3 for this.';
const H3_FLEX_SPEED_TIP = 'H3 Flex runs its own steps. Switch to H3 to pick Fast, Best or Steps.';
// Flex or Flex Turbo is what this form will ask for.
function h3FlexPickedAny() {
  const pref = h3SpeedPref();
  return currentEngine() === 'h3' && (pref === 'flex' || pref === 'flex_turbo');
}
// The modes the picked Flex renders (null = the server did not say: no lock,
// make_job decides).
function h3FlexModes(which) {
  which = which || h3SpeedPref();
  const st = which === 'flex_turbo' ? h3FlexTurboState() : h3FlexState();
  return Array.isArray(st.modes) ? st.modes : null;
}
function h3FlexServesMode(mode, which) {
  if (mode == null) mode = (document.getElementById('mode') || {}).value || 't2v';
  if (mode === 'oneshot') mode = 't2v';
  const modes = h3FlexModes(which);
  return !modes || modes.indexOf(mode) !== -1;
}
// Would Flex render THIS cell? The server's own answer; an older server that
// predates `takes_flex` still prices exactly the cells it takes.
function h3CellTakesFlex(cell) {
  if (!cell) return false;
  if (typeof cell.takes_flex === 'boolean') return cell.takes_flex;
  return cell.flex_min != null;
}
// The supported cell nearest to one Flex cannot render: same canvas at the
// closest duration first (10 s single pass -> 10 s in two windows), else, at
// this length, the biggest picture that is not bigger than what the cell
// delivers (Fast HD's 1280x768 -> High, never the far slower Native), like
// _h3FallbackCell never goes longer than asked; else any cell Flex takes.
function _h3FlexNearestCell(cell) {
  const tiers = (H3.tiers || []).filter(t => h3CellTakesFlex(t) && t.available !== false);
  if (!tiers.length) return null;
  const secs = c => Number((h3LengthByKey(c.length) || {}).seconds || c.seconds || 0);
  const qIdx = k => (H3.qualities || []).findIndex(q => q.key === k);
  const want = secs(cell);
  const sameQ = tiers.filter(t => t.quality === cell.quality);
  if (sameQ.length) {
    return sameQ.sort((a, b) => Math.abs(secs(a) - want) - Math.abs(secs(b) - want)
                                || secs(a) - secs(b))[0];
  }
  let pool = tiers.filter(t => t.length === cell.length);
  if (!pool.length) {
    const gap = Math.min(...tiers.map(t => Math.abs(secs(t) - want)));
    pool = tiers.filter(t => Math.abs(secs(t) - want) === gap);
  }
  const px = c => (c.final_width || c.width || 0) * (c.final_height || c.height || 0);
  const want_px = px(cell);
  const fits = pool.filter(t => px(t) <= want_px);
  if (fits.length) return fits.sort((a, b) => px(b) - px(a) || qIdx(a.quality) - qIdx(b.quality))[0];
  return pool.sort((a, b) => px(a) - px(b) || qIdx(a.quality) - qIdx(b.quality))[0];
}
// The supported mode nearest to one Flex cannot render: a mode that starts
// from a picture goes to Image, everything else to Text, whichever of the two
// the server lists (else its first).
function _h3FlexNearestMode(mode) {
  const modes = h3FlexModes() || [];
  // i2v_clean_audio is LTX's Image-with-your-audio, left in the Image select
  // by an earlier LTX render: still a picture mode.
  const fromPicture = mode === 'keyframe' || mode === 'a2v' || mode === 'i2v_clean_audio';
  const order = fromPicture ? ['i2v', 't2v'] : ['t2v', 'i2v'];
  return order.find(m => modes.indexOf(m) !== -1) || modes[0] || null;
}
// Picking Flex while the form points somewhere Flex cannot go moves the form to
// the nearest place it can (and says so, once per move) rather than greying
// Flex itself: the user asked for Flex, and the move is one visible step that
// a click on H3 undoes. Runs after the click, and after any repaint that left a
// picked Flex on an unsupported mode or cell (a restore, a status tick).
// setMode('i2v') copies the Image select (#i2vMode) into #mode, and that select
// may still hold LTX's 'i2v_clean_audio' from an earlier render: Image would
// snap straight back. Pin it first, as Load Params does for an H3 i2v sidecar.
function _h3FlexPinImageSelect(to) {
  if (to !== 'i2v') return;
  const sel = document.getElementById('i2vMode');
  if (sel && sel.value !== 'i2v') sel.value = 'i2v';
}
let _h3FlexFitBusy = false;
function h3FlexFit() {
  if (_h3FlexFitBusy || !h3FlexPickedAny()) return;
  _h3FlexFitBusy = true;
  try {
    const name = h3SpeedPref() === 'flex_turbo' ? 'H3 Flex Turbo' : 'H3 Flex';
    let movedMode = '', movedCell = null;
    const mode = (document.getElementById('mode') || {}).value || 't2v';
    if (!h3FlexServesMode(mode)) {
      const to = _h3FlexNearestMode(mode);
      if (to && typeof setMode === 'function') {
        _h3FlexPinImageSelect(to);
        setMode(to);
        // A plain Image click that only lost a leftover LTX audio variant is
        // not a move worth announcing.
        if (mode !== 'i2v_clean_audio') movedMode = to === 'i2v' ? 'Image' : 'Text';
      }
    }
    const cell = h3CurrentCell();
    if (cell && !h3CellTakesFlex(cell)) {
      const to = _h3FlexNearestCell(cell);
      if (to) { _h3ApplyShape(to.quality, to.length); movedCell = to; }
    }
    const msg = movedMode && movedCell
      ? name + ' makes videos from text or an image, on 5-second windows: you are on '
        + movedMode + ' at ' + movedCell.label + ' now.'
      : movedMode ? name + ' makes videos from text or an image: you are on ' + movedMode + ' now.'
      : movedCell ? name + ' runs on 5-second windows: the shape is ' + movedCell.label + ' now.'
      : '';
    if (msg && typeof phosToast === 'function') {
      phosToast(msg + ' Pick H3 to go back.', { kind: 'info', duration: 4200 });
    }
  } finally {
    _h3FlexFitBusy = false;
  }
}
// Generate while H3 Flex is the pick but not ready yet (still downloading, or
// never fetched) used to post h3_flex=0 and render plain H3, the same "thought
// it was Flex" trap. Returns the plain reason to refuse with, or ''.
function h3FlexSubmitBlock() {
  if (!h3FlexPickedAny()) return '';
  const turbo = h3SpeedPref() === 'flex_turbo';
  const st = turbo ? h3FlexTurboState() : h3FlexState();
  if (st.available) return '';
  const name = turbo ? 'H3 Flex Turbo' : 'H3 Flex';
  if (st.installing) {
    return name + ' is still downloading. It can render as soon as it is ready, or pick H3 to render now.';
  }
  return name + ' is not on this Mac yet. Pick it in the model menu to get it, or pick H3 to render now.';
}
let _h3FlexFitQueued = false;
function _h3FlexFitSoon() {
  if (_h3FlexFitQueued || _h3FlexFitBusy || !h3FlexPickedAny()) return;
  const cell = h3CurrentCell();
  if (h3FlexServesMode() && (!cell || h3CellTakesFlex(cell))) return;
  _h3FlexFitQueued = true;
  setTimeout(() => { _h3FlexFitQueued = false; h3FlexFit(); }, 0);
}
// Would THIS cell's Best render on v1.3, as things stand?
function h3V13On(cell, opts) {
  cell = cell || h3CurrentCell();
  if (!cell || cell.v13_min == null) return false;
  opts = opts || {};
  if (opts.flex == null && typeof h3FlexOn === 'function' && h3FlexOn(cell)) return false;
  const fast = (opts.fast != null) ? opts.fast : h3TriStepOn(cell);
  if (fast) return false;
  const ov = (opts.steps != null) ? String(opts.steps)
    : ((document.getElementById('h3_steps') || {}).value || 'auto');
  if (ov !== 'auto') return false;
  return _h3V13Mode() && !!h3V13State().available;
}
function h3SpeedPref() {
  let v = null;
  try { v = localStorage.getItem('phos_h3_speed'); } catch (e) {}
  return (v === 'best' || v === 'flex' || v === 'flex_turbo') ? v : 'fast';
}
function _h3SetSpeedPref(v) {
  try {
    localStorage.setItem('phos_h3_speed', (v === 'best' || v === 'flex' || v === 'flex_turbo') ? v : 'fast');
  } catch (e) {}
}
function _h3IsI2V() {
  return ((document.getElementById('mode') || {}).value || 't2v') === 'i2v';
}
// Would THIS cell render Fast, as things stand?
function h3TriStepOn(cell) {
  cell = cell || h3CurrentCell();
  // Fast HD is the Fast pass by definition (the server forces it too).
  if (cell && cell.fast_hd) return !!(cell.tristep_min != null && h3TriStepState().available);
  return !!(cell && cell.tristep_min != null
            && h3TriStepState().available && h3SpeedPref() === 'fast');
}
// Clean up sound (5.0): re-make a Fast render's sound with the adapter off.
// Remembered per browser; the server's default when never touched.
function h3RevoiceState() {
  return (H3 && H3.revoice) || { supported: false, default: false };
}
function h3CleanSoundPref() {
  let v = null;
  try { v = localStorage.getItem('phos_h3_clean_sound'); } catch (e) {}
  if (v === '1') return true;
  if (v === '0') return false;
  return !!h3RevoiceState().default;
}
// Would THIS cell's render clean up its sound, as things stand?
function h3CleanSoundOn(cell) {
  cell = cell || h3CurrentCell();
  const mode = (document.getElementById('mode') || {}).value || 't2v';
  // Never under H3 Flex: make_job turns clean sound off for a Flex render.
  return !!(h3RevoiceState().supported && h3TriStepOn(cell) && cell
            && cell.revoice_min != null && cell.revoice_fits !== false
            && mode !== 'a2v' && mode !== 'v2a' && h3CleanSoundPref()
            && !(typeof h3FlexPickedAny === 'function' && h3FlexPickedAny()));
}
function setH3CleanSound(on) {
  try { localStorage.setItem('phos_h3_clean_sound', on ? '1' : '0'); } catch (e) {}
  _h3SyncCleanSound();
  // Every price on screen follows: the Speed pill, the chips, the footer.
  _h3SpeedRepriced();
}
function _h3SyncCleanSound() {
  const row = document.getElementById('h3CleanSoundRow');
  const box = document.getElementById('h3CleanSound');
  const hint = document.getElementById('h3CleanSoundHint');
  const inp = document.getElementById('h3_clean_sound');
  const cell = h3CurrentCell();
  const mode = (document.getElementById('mode') || {}).value || 't2v';
  const show = !!(h3RevoiceState().supported && h3TriStepOn(cell) && cell
                  && cell.revoice_min != null && mode !== 'a2v' && mode !== 'v2a'
                  && !(typeof h3FlexPickedAny === 'function' && h3FlexPickedAny()));
  if (row) row.hidden = !show;
  const fits = !!(cell && cell.revoice_fits !== false);
  if (box) {
    box.checked = show && fits && h3CleanSoundPref();
    box.disabled = !fits;
  }
  if (hint) {
    hint.textContent = !show ? '' : !fits ? (cell.revoice_note || 'needs more memory at this Quality')
      : 'clearer voices, same picture · adds about ' + Math.max(1, Math.round(cell.revoice_min)) + ' min';
  }
  if (inp) inp.value = show ? (h3CleanSoundOn(cell) ? '1' : '0') : '';
}
function _h3TriStepMin(cell) {
  if (!cell || cell.tristep_min == null) return null;
  return (_h3IsI2V() && cell.tristep_min_i2v != null) ? cell.tristep_min_i2v : cell.tristep_min;
}
function _h3TriStepEta(cell) {
  if (!cell || cell.tristep_min == null) return '';
  if (_h3IsI2V()) return cell.tristep_eta_i2v || cell.tristep_eta;
  // Owner ruling (2026-09-29): real fleet range for Fast, where enough of
  // it exists (fleet_range_fast, priced separately from Best — H3's speed
  // split is a real difference in wall time, unlike LTX's mode axis).
  return cell.fleet_range_fast ? cell.fleet_range_fast.eta_range : cell.tristep_eta;
}

// "~9 min" → "9 min": a tilde at this size reads as a minus (the owner read
// "-4 min" once). Estimates on the switch are plain.
function _h3EtaPlain(s) {
  return String(s || '').replace(/[~≈]/g, '').trim();
}
// Minutes for a cell under the CURRENT speed (or the one opts names). The
// server ships every number (Best eta, Fast eta per mode, per-forward and
// fixed seconds for a pinned Steps count); nothing here models anything.
// Face to keep (5.0): the reference block's own cost, on top of either speed.
function _h3ReferenceExtra(cell, fast) {
  const ref = (document.getElementById('h3_reference') || {}).value || '';
  const mode = (document.getElementById('mode') || {}).value || 't2v';
  if (!ref || !cell || (mode !== 't2v' && mode !== 'i2v')) return 0;
  if (!(H3 && H3.reference && H3.reference.supported)) return 0;
  return (fast ? cell.reference_min_fast : cell.reference_min) || 0;
}
function h3CellEtaMin(cell, opts) {
  if (!cell) return 0;
  const _fastNow = (opts && opts.fast != null) ? opts.fast : h3TriStepOn(cell);
  const _ref = (typeof _h3ReferenceExtra === 'function') ? _h3ReferenceExtra(cell, _fastNow) : 0;
  return _ref + _h3CellEtaMinCore(cell, opts);
}
// The shape's own price at the current speed (Clean up sound
// and a pinned Steps count folded in); h3CellEtaMin adds the reference.
function _h3CellEtaMinCore(cell, opts) {
  if (!cell) return 0;
  // Fast HD is priced whole: the Fast H3 pass plus its Upscale & Face Fix.
  if (cell.fast_hd) {
    // The Fast pass (plus Clean up sound when it rides) and the Face Fix.
    const cleanHd = (opts && opts.clean != null) ? opts.clean
      : (typeof h3CleanSoundOn === 'function' && h3CleanSoundOn(cell));
    return (_h3TriStepMin(cell) || cell.tristep_min || 0) + (cell.facefix_min || 0)
      + (cleanHd ? (cell.revoice_min || 0) : 0);
  }
  opts = opts || {};
  const fast = (opts.fast != null) ? opts.fast : h3TriStepOn(cell);
  if (fast && cell.tristep_min != null) {
    const clean = (opts.clean != null) ? opts.clean
      : (typeof h3CleanSoundOn === 'function' && h3CleanSoundOn(cell));
    return _h3TriStepMin(cell) + (clean ? (cell.revoice_min || 0) : 0);
  }
  if (opts.turbo) return cell.turbo_min;
  const flexOn = (opts.flex != null) ? opts.flex
    : (typeof h3FlexOn === 'function' && h3FlexOn(cell));
  if (flexOn && cell.flex_min != null) return cell.flex_min;
  if (opts.flex == null && typeof h3FlexTurboOn === 'function' && h3FlexTurboOn(cell) && cell.flex_turbo_min != null) return cell.flex_turbo_min;
  if (h3V13On(cell, { fast: false, steps: opts.steps, flex: false })) return cell.v13_min;
  const ov = (opts.steps != null) ? String(opts.steps)
    : ((document.getElementById('h3_steps') || {}).value || 'auto');
  if (ov !== 'auto' && /^\d+$/.test(ov)) {
    const win = Math.max(1, cell.chain_windows || 1);
    const fwd = Math.max(1, parseInt(ov, 10) - 1);
    return (win * fwd * cell.per_forward_sec + win * cell.fixed_sec) / 60;
  }
  return cell.eta_min;
}
// The provenance line for a cell's tooltip, matching whichever speed is
// actually showing — mirrors ltxCellEtaBasis's contract.
function h3CellEtaBasis(cell, opts) {
  if (!cell) return '';
  opts = opts || {};
  const fast = (opts.fast != null) ? opts.fast : h3TriStepOn(cell);
  const fr = fast ? cell.fleet_range_fast
    : (h3V13On(cell, { fast: false }) ? cell.fleet_range_v13 : cell.fleet_range);
  if (!fr && !fast && h3V13On(cell, { fast: false }) && cell.v13_measured) return 'measured on an M4 Max';
  return fr ? fr.basis : 'estimated — few renders on this Mac class yet';
}
function h3FmtEtaMin(m) {
  // H3-37: "· batch" on a 25+ min chip was unexplained, and reads as the
  // unrelated Batch feature (queueing several renders) rather than what it
  // meant here — this single render is long enough to start and walk away
  // from. Say that plainly instead of a one-word label with two meanings.
  return '~' + Math.max(1, Math.round(m)) + ' min' + (m >= 25 ? ' · long, start and walk away' : '');
}
// The eta STRING for a cell under the current speed; the server's own string
// wherever it priced that state (that is where a MEASURED wall clock lives).
function h3CellEta(cell, opts) {
  if (!cell) return '';
  if (cell.fast_hd) return h3FmtEtaMin(h3CellEtaMin(cell));
  opts = opts || {};
  const fast = (opts.fast != null) ? opts.fast : h3TriStepOn(cell);
  const refExtra = (typeof _h3ReferenceExtra === 'function') ? _h3ReferenceExtra(cell, fast) : 0;
  if (fast && cell.tristep_min != null) {
    // Clean up sound adds its own pass: price the sum, not the bare Fast receipt.
    const clean = (opts.clean != null) ? opts.clean
      : (typeof h3CleanSoundOn === 'function' && h3CleanSoundOn(cell));
    return (clean || refExtra) ? h3FmtEtaMin(h3CellEtaMin(cell, { fast: true, clean })) : _h3TriStepEta(cell);
  }
  if (refExtra && !opts.turbo) return h3FmtEtaMin(h3CellEtaMin(cell, { fast: false }));
  if (opts.turbo) return cell.turbo_eta;
  const flexOn = (opts.flex != null) ? opts.flex
    : (typeof h3FlexOn === 'function' && h3FlexOn(cell));
  if (flexOn && cell.flex_eta) return cell.flex_eta;
  if (opts.flex == null && typeof h3FlexTurboOn === 'function' && h3FlexTurboOn(cell) && cell.flex_turbo_eta) return cell.flex_turbo_eta;
  const ov = (document.getElementById('h3_steps') || {}).value || 'auto';
  if (ov === 'auto' || !/^\d+$/.test(ov)) {
    if (h3V13On(cell, { fast: false, flex: false })) {
      return (!_h3IsI2V() && cell.fleet_range_v13) ? cell.fleet_range_v13.eta_range : cell.v13_eta;
    }
    // Owner ruling (2026-09-29): Best's own real fleet range, same idea as
    // Fast's above — never for i2v, which the server prices separately and
    // this fleet cell (mode "t2v") does not cover.
    if (!_h3IsI2V() && cell.fleet_range) return cell.fleet_range.eta_range + proEstSuffix(cell.fleet_range);
    return cell.eta;
  }
  return h3FmtEtaMin(h3CellEtaMin(cell, { fast: false }));
}
// Second line of each half of the switch.
function h3SpeedSub(which) {
  const cell = h3CurrentCell();
  if (which === 'best') {
    return _h3EtaPlain(h3CellEta(cell, { fast: false, flex: false })) || 'full steps';
  }
  if (which === 'flex') {
    const fx = h3FlexState();
    if (!fx.supported) return 'update H3 runner';
    if (!fx.downloaded) {
      if (fx.installing) {
        const d = fx.download || {};
        return d.status === 'building' ? 'preparing…'
          : 'installing ' + (d.mb || 0) + ' / ' + (d.total_mb || 2639) + ' MB';
      }
      return 'Install (' + (fx.download_gb || 2.8) + ' GB)';
    }
    if (!cell || cell.flex_min == null || !_h3V13Mode()) return 'not at this shape';
    return _h3EtaPlain(h3CellEta(cell, { fast: false, flex: true })) || '6 steps';
  }
  const tri = h3TriStepState();
  if (!tri.supported) return 'update H3 runner';
  if (!tri.downloaded) {
    if (tri.installing) {
      const d = tri.download || {};
      return 'installing ' + (d.mb || 0) + ' / ' + (d.total_mb || 173) + ' MB';
    }
    return 'Install (180 MB)';
  }
  if (!cell || cell.tristep_min == null) return 'Best only here';
  // h3CellEta adds Clean up sound / a reference when they ride on this render.
  return _h3EtaPlain(h3CellEta(cell, { fast: true })) || '3 steps';
}
// Kept for callers of the pre-switch API.
function h3TurboPillSub() { return h3SpeedSub('fast'); }

// The sampler depth Best runs is per CELL (High 16 sigma points, Draft/
// Standard/Native 9, dense 10 s 16), so the tooltips read it from the cell.
function _h3SyncSamplerTitles() {
  // Under H3 Flex the Best / Steps pills carry the Flex reason, not their own
  // description (renderH3Turbo / _h3ApplySpeed set it; never overwrite it).
  if (typeof h3FlexPickedAny === 'function' && h3FlexPickedAny()) return;
  const cell = h3CurrentCell();
  const pts = (cell && cell.steps) || 9;
  const fwd = Math.max(1, pts - 1);
  const best = document.querySelector('#h3SpeedGroup [data-h3-speed="best"]');
  if (best) best.title = h3V13On(cell, { fast: false })
    ? 'Best on High: ' + (h3V13State().name || 'lightx2v v1.3') + ', ' + (h3V13State().forwards || 6)
      + ' steps per window. Takes about half the time. Choose a Steps count to use the old Best sampler.'
    : "Best: this shape's full sampler — " + fwd + ' steps per window. '
      + 'The slower, most careful render.';
  const auto = document.querySelector('#h3StepsGroup [data-h3-steps="auto"]');
  if (auto) auto.title = "This shape's tuned count — " + pts + ' sigma points ('
    + fwd + ' forwards per window) at this canvas.';
  if (!cell || !cell.eta_min) return;
  document.querySelectorAll('#h3StepsGroup [data-h3-steps]').forEach(b => {
    const n = b.dataset.h3Steps;
    if (!/^\d+$/.test(n)) return;
    const k = h3CellEtaMin(cell, { fast: false, steps: n }) / cell.eta_min;
    const rel = Math.abs(k - 1) < 0.05 ? 'the same time as Auto at this shape'
      : '~' + k.toFixed(1) + '× Auto\'s render time at this shape';
    b.title = n + ' sigma points (' + (parseInt(n, 10) - 1) + ' forwards) — ' + rel
      + (n === '20' ? '. The official reference recipe.' : '.');
  });
}

// Paint the switch for the current cell and apply it. Called from every place
// that moves the shape, the steps, the mode or the install state.
function renderH3Turbo() {
  const row = document.getElementById('h3SpeedRow');
  const t = h3TurboState();
  const tri = h3TriStepState();
  // An old pack with no --lora at all can do neither; the switch hides, as
  // Turbo's row always did.
  if (row) row.hidden = !(t.supported || tri.supported);
  const cell = h3CurrentCell();
  const fastBtn = document.querySelector('#h3SpeedGroup [data-h3-speed="fast"]');
  const bestSub = document.getElementById('h3SpeedBestSub');
  const fastSub = document.getElementById('h3SpeedFastSub');
  if (fastSub) fastSub.textContent = h3SpeedSub('fast');
  if (bestSub) bestSub.textContent = h3SpeedSub('best');
  if (fastBtn) {
    fastBtn.classList.toggle('needs-download', !tri.available);
    // H3-13: distinct from needs-download (the adapter isn't installed) —
    // this cell has no Fast path at all (Native, the single-pass 10s), same
    // dashed/dimmed treatment so the chip itself says "not really available
    // here" instead of only a sub-label most people skim past.
    const cellOnlyBest = tri.available && cell && cell.tristep_min == null;
    fastBtn.classList.toggle('cell-only-best', cellOnlyBest);
    fastBtn.title = !tri.supported
      ? distText('Fast needs a newer H3 runner — run "Update Hailuo H3 runner" in the Pinokio sidebar (weights stay).',
                 'Fast needs a newer Hailuo H3 runner. Phosphene brings the Hailuo H3 runner up to date by itself; quit and reopen Phosphene if this stays.')
      : !tri.downloaded
        ? '3 steps — about 4× faster, great for drafts and most shots. One click installs the '
          + '180 MB adapter (' + (tri.license || 'MiniMax H3 Community License') + ').'
        : cellOnlyBest
          ? (cell.label || 'This shape') + ' has no Fast pass yet — it always renders on Best.'
          : '3 steps — about 4× faster, great for drafts and most shots.';
  }
  const flexBtn = document.querySelector('#h3SpeedGroup [data-h3-speed="flex"]');
  const flexSub = document.getElementById('h3SpeedFlexSub');
  const fx = h3FlexState();
  if (flexSub) flexSub.textContent = h3SpeedSub('flex');
  if (flexBtn) {
    // Hidden where the runner cannot take it at all (an older pack); dashed
    // until the adapter is in, like Fast.
    // 4.19.15: H3 Flex is its own entry in the top engine picker (owner:
    // "selected in the ticker, like we select H3"). One obvious way in, so the
    // Speed pill stays hidden; the Speed row is H3's own Fast / Best again.
    flexBtn.hidden = true;
    flexBtn.classList.toggle('needs-download', !fx.available);
    flexBtn.classList.toggle('cell-only-best',
      fx.available && !!cell && (cell.flex_min == null || !_h3V13Mode()));
  }
  const group = document.getElementById('h3SpeedGroup');
  if (group && flexBtn) {
    group.classList.toggle('cols-3', !flexBtn.hidden);
    group.classList.toggle('cols-2', flexBtn.hidden);
  }
  // While H3 Flex is the engine, Fast / Best do not apply. 4.19.15 hid them;
  // owner, 31 Oct 2026: show what Flex cannot do, greyed, so nobody believes a
  // Fast or Best choice still counts. The Flex note (credit, download,
  // progress) sits under them. _h3ApplySpeed greys the Steps pills the same way.
  const flexPicked = h3SpeedPref() === 'flex' || h3SpeedPref() === 'flex_turbo';
  if (group) {
    group.hidden = false;
    group.classList.toggle('flex-locked', flexPicked);
    group.querySelectorAll('[data-h3-speed="fast"], [data-h3-speed="best"]').forEach(b => {
      if (flexPicked) {
        if (b.dataset.flexTitle == null) b.dataset.flexTitle = b.getAttribute('title') || '';
        b.setAttribute('title', H3_FLEX_SPEED_TIP);
        b.setAttribute('aria-disabled', 'true');
      } else if (b.dataset.flexTitle != null) {
        b.setAttribute('title', b.dataset.flexTitle);
        delete b.dataset.flexTitle;
        b.removeAttribute('aria-disabled');
      }
      b.classList.toggle('flex-locked', flexPicked);
    });
  }
  const speedMeta = document.querySelector('#h3SpeedRow .qs-meta');
  if (speedMeta) speedMeta.hidden = flexPicked;
  const speedName = document.querySelector('#h3SpeedRow .qs-name');
  if (speedName) speedName.textContent = !flexPicked ? 'Speed'
    : (h3SpeedPref() === 'flex_turbo' ? 'H3 Flex Turbo (faster draft, Pro)' : 'H3 Flex (preview)');
  _h3SyncSamplerTitles();
  _h3ApplySpeed();
  _h3RenderFlexNote();
  _h3FlexPickerSync();
  _h3RenderV13Note();
  _h3SyncFaceFixEta();
  // The mode chips grey / un-grey with the Flex pick, whatever changed it
  // (the picker, Load Params, a status tick).
  if (typeof currentEngine === 'function' && currentEngine() === 'h3') { try { syncModeStripToEngine(); } catch (e) {} }
  if (typeof _h3FlexFitSoon === 'function') _h3FlexFitSoon();
}

// The line under the switch for Pika H3 Flex: the credit (and "Powered by
// MiniMax H3", which the H3 licence asks a product to show) while it is the
// choice, the one click that gets it before then.
function _h3RenderFlexNote() {
  const box = document.getElementById('h3FlexNote');
  if (!box) return;
  const cell = h3CurrentCell();
  const fx = h3FlexState();
  const text = document.getElementById('h3FlexNoteText');
  const btn = document.getElementById('h3FlexGet');
  if (h3SpeedPref() === 'flex_turbo' && !h3TriStepOn(cell)) {
    const message = _h3FlexTurboNote(cell);
    box.hidden = !message;
    if (text) text.textContent = message;
    if (btn) btn.hidden = true;
    return;
  }
  const show = h3SpeedPref() === 'flex' && !h3TriStepOn(cell);
  box.hidden = !show;
  if (!show || !text) return;
  const name = fx.name || 'Pika H3 Flex';
  let msg = '';
  let canGet = false;
  if (!fx.supported) {
    msg = name + ' needs a newer H3 runner. ' + distText(
      'Click "Update Hailuo H3 runner" in the Pinokio sidebar. Your weights stay.',
      'Phosphene brings the Hailuo H3 runner up to date by itself; quit and reopen Phosphene if this stays.');
  } else if (fx.installing) {
    const d = fx.download || {};
    msg = d.status === 'building' ? 'Preparing ' + name + ' on your Mac…'
      : 'Downloading ' + name + ': ' + (d.mb || 0) + ' / ' + (d.total_mb || 2639) + ' MB';
  } else if (!fx.downloaded) {
    const e = (fx.download || {}).error;
    msg = e ? 'Download failed: ' + e : name + ' needs a download.';
    canGet = true;
  } else if (h3FlexOn(cell)) {
    box.hidden = true;
    text.textContent = '';
    if (btn) btn.hidden = true;
    return;
  } else {
    msg = name + ' renders Text and Image on 5-second windows; this shape uses Best.';
  }
  text.textContent = msg;
  if (btn) {
    btn.hidden = !canGet;
    btn.textContent = 'Get it · ' + (fx.download_gb || 2.8) + ' GB';
  }
}
async function h3FlexInstall() {
  const toast = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
  const btn = document.getElementById('h3FlexGet');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/h3/flex/install', { method: 'POST' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    _h3SetSpeedPref('flex');
    toast(j.already_installed ? 'Pika H3 Flex is already installed.'
      : 'Downloading Pika H3 Flex from Pika Labs (2.8 GB), then preparing it on your Mac. '
        + 'It switches on by itself when ready.', 'ok');
  } catch (e) {
    toast('Pika H3 Flex: ' + (e.message || 'failed'), 'danger');
  } finally {
    if (btn) btn.disabled = false;
  }
}
{
  const _flexBtn = document.getElementById('h3FlexGet');
  if (_flexBtn) _flexBtn.onclick = (ev) => { ev.preventDefault(); h3FlexInstall(); };
}

// The line under the switch for lightx2v v1.3 on High: what it is and who
// made it once it is in, the one click that gets it before then.
function _h3RenderV13Note() {
  const box = document.getElementById('h3V13Note');
  if (!box) return;
  const cell = h3CurrentCell();
  const v = h3V13State();
  const text = document.getElementById('h3V13NoteText');
  const btn = document.getElementById('h3V13Get');
  // Not while Pika H3 Flex is the choice: its own note says what renders.
  const show = !!(cell && cell.v13_min != null && _h3V13Mode() && !h3TriStepOn(cell)
                  && h3SpeedPref() === 'best');
  box.hidden = !show;
  if (!show || !text) return;
  const name = v.name || 'lightx2v v1.3';
  let msg = '';
  let canGet = false;
  if (!v.supported) {
    msg = name + ' needs an update. ' + distText(
      'Update H3: click "Update Hailuo H3 runner" in the Pinokio sidebar. Your weights stay.',
      'It needs a newer H3 runner, but the Mac app can\u2019t update the runner yet.');
  } else if (v.installing) {
    const d = v.download || {};
    msg = d.status === 'building' ? 'Preparing ' + name + ' on your Mac…'
      : 'Downloading ' + name + ': ' + (d.mb || 0) + ' / ' + (d.total_mb || 1320) + ' MB';
  } else if (!v.downloaded) {
    const e = (v.download || {}).error;
    msg = e ? 'Download failed: ' + e : name + ' needs a download.';
    canGet = true;
  } else {
    box.hidden = true;
    text.textContent = '';
    if (btn) btn.hidden = true;
    return;
  }
  text.textContent = msg;
  if (btn) {
    btn.hidden = !canGet;
    btn.textContent = 'Get it · ' + (v.download_gb || 1.4) + ' GB';
  }
}
async function h3V13Install() {
  const toast = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
  const btn = document.getElementById('h3V13Get');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch('/h3/v13/install', { method: 'POST' });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    toast(j.already_installed ? 'lightx2v v1.3 is already installed.'
      : "Downloading LightX2V's lightx2v v1.3 (1.4 GB), then preparing it on your Mac. "
        + 'Best on High will use it automatically when ready.', 'ok');
  } catch (e) {
    toast('lightx2v v1.3: ' + (e.message || 'failed'), 'danger');
  } finally {
    if (btn) btn.disabled = false;
  }
}
{
  const _v13Btn = document.getElementById('h3V13Get');
  if (_v13Btn) _v13Btn.onclick = (ev) => { ev.preventDefault(); h3V13Install(); };
}

// ONE writer for what this render does: both hidden inputs, the lit half, the
// Steps row, the note. Guarded: setH3Steps re-enters through renderH3Turbo.
let _h3SpeedBusy = false;
function _h3ApplySpeed() {
  if (_h3SpeedBusy) return;
  _h3SpeedBusy = true;
  try {
    const cell = h3CurrentCell();
    const tri = h3TriStepState();
    const pref = h3SpeedPref();
    const fastOn = h3TriStepOn(cell);
    const triIn = document.getElementById('h3_tristep');
    if (triIn) triIn.value = fastOn ? '1' : '0';
    const tbIn = document.getElementById('h3_turbo');
    if (tbIn) tbIn.value = '0';
    const flexOn = !fastOn && typeof h3FlexOn === 'function' && h3FlexOn(cell);
    const fxIn = document.getElementById('h3_flex');
    if (fxIn) fxIn.value = flexOn ? '1' : '0';
    const ftOn = !fastOn && !flexOn && typeof h3FlexTurboOn === 'function' && h3FlexTurboOn(cell);
    const ftIn = document.getElementById('h3_flex_turbo');
    if (ftIn) ftIn.value = ftOn ? '1' : '0';
    // H3-13: the lit half used to be the PREFERENCE alone (pref === 'fast' &&
    // tri.available), so Native and the single-pass 10s — cells with no Fast
    // path at all (cell.tristep_min == null) — kept the Fast pill pink while
    // the render that actually queued ran Best, 8-25x slower. fastOn already
    // folds in per-cell availability (h3TriStepOn), so light whichever half
    // is really about to render; the note below still explains why.
    const flexPicked = pref === 'flex' || pref === 'flex_turbo';
    const lit = fastOn ? 'fast' : (flexOn ? 'flex' : (ftOn ? 'flex_turbo' : 'best'));
    document.querySelectorAll('#h3SpeedGroup [data-h3-speed]').forEach(b =>
      b.classList.toggle('active', b.dataset.h3Speed === lit));
    const note = document.getElementById('h3SpeedNote');
    if (note) {
      // Independent of `lit`: this is "you prefer Fast, but this shape can
      // only run Best", which is exactly the case where the pill and the
      // note used to disagree.
      const bestOnly = pref === 'fast' && cell && cell.tristep_min == null;
      note.textContent = bestOnly
        ? cell.label + ' renders on Best — Fast is not validated at this shape yet.'
        : '';
      note.hidden = !bestOnly;
    }
    const stIn = document.getElementById('h3_steps');
    const s = document.getElementById('steps');
    if (fastOn) {
      // Release a pinned Steps pill FIRST (it re-derives `steps` from the
      // cell), THEN pin Fast's own count.
      if (stIn && stIn.value !== 'auto' && typeof setH3Steps === 'function') {
        try { setH3Steps('auto'); } catch (e) {}
      }
      // H3-25: this used to mirror the adapter's own SIGMA-POINT count
      // (tri.steps, falling back to the literal 4) into the shared #steps
      // field that the derived line reads — but the Speed row says
      // "Fast = 3 steps", counting FORWARDS, not sigma points. Same render,
      // two different numbers on screen. cell.tristep_forwards is what
      // actually runs (windows * H3_TRISTEP_FORWARDS, server-side); a
      // sigma-point count minus one is the fallback only when a cell
      // hasn't loaded yet.
      if (s) s.value = (cell && cell.tristep_forwards != null)
        ? cell.tristep_forwards : (tri.steps ? tri.steps - 1 : 3);
    } else if (flexOn && s && cell) {
      // Flex owns its six-step ladder: a pinned Steps pill does not apply.
      if (stIn && stIn.value !== 'auto' && typeof setH3Steps === 'function') {
        try { setH3Steps('auto'); } catch (e) {}
      }
      s.value = (cell.flex_forwards != null) ? cell.flex_forwards : (h3FlexState().forwards || 6);
    } else if (s && cell) {
      const ov = (stIn || {}).value || 'auto';
      s.value = (ov !== 'auto' && /^\d+$/.test(ov)) ? parseInt(ov, 10)
        : (h3V13On(cell, { fast: false }) ? cell.v13_steps : cell.steps);
    }
    // Steps is a Best control: hidden under Fast; under H3 Flex it shows
    // greyed with the reason (Flex runs its own steps), like Fast / Best.
    const stepsRow = document.getElementById('h3StepsRow');
    if (stepsRow) {
      stepsRow.hidden = fastOn && !flexPicked;
      stepsRow.classList.toggle('flex-locked', flexPicked);
      const stHint = stepsRow.querySelector('.cz-label-hint');
      if (stHint && flexPicked) {
        if (stHint.dataset.flexText == null) stHint.dataset.flexText = stHint.textContent;
        stHint.textContent = 'H3 Flex runs its own steps';
      } else if (stHint && stHint.dataset.flexText != null) {
        stHint.textContent = stHint.dataset.flexText;
        delete stHint.dataset.flexText;
      }
      stepsRow.querySelectorAll('[data-h3-steps]').forEach(b => {
        if (flexPicked) {
          if (b.dataset.flexTitle == null) b.dataset.flexTitle = b.getAttribute('title') || '';
          b.setAttribute('title', H3_FLEX_SPEED_TIP);
          b.setAttribute('aria-disabled', 'true');
          b.classList.remove('active');
        } else if (b.dataset.flexTitle != null) {
          b.setAttribute('title', b.dataset.flexTitle);
          delete b.dataset.flexTitle;
          b.removeAttribute('aria-disabled');
          b.classList.toggle('active', b.dataset.h3Steps === ((stIn || {}).value || 'auto'));
        }
        b.classList.toggle('flex-locked', flexPicked);
      });
    }
    _h3SyncCleanSound();
    if (typeof renderH3LoraSlot === 'function') { try { renderH3LoraSlot(); } catch (e) {} }
  } finally {
    _h3SpeedBusy = false;
  }
}
function _h3SpeedRepriced() {
  if (typeof renderH3Axes === 'function') { try { renderH3Axes(); } catch (e) {} }
  if (typeof renderH3Turbo === 'function') { try { renderH3Turbo(); } catch (e) {} }
  if (typeof updateDerived === 'function') { try { updateDerived(); } catch (e) {} }
  if (typeof updateCustomizeSummary === 'function') { try { updateCustomizeSummary(); } catch (e) {} }
}

// Set the speed ('fast' | 'best'). Remembered per browser.
function setH3Speed(v) {
  _h3SetSpeedPref((v === 'best' || v === 'flex' || v === 'flex_turbo') ? v : 'fast');
  _h3ApplySpeed();
  _h3SpeedRepriced();
}
// Pre-switch API: Turbo on meant "the fast one".
function setH3Turbo(on) { if (on) setH3Speed('fast'); else _h3ApplySpeed(); }
function setH3TriStep(on) { setH3Speed(on ? 'fast' : 'best'); }
// What a saved clip was made with, as this switch's value.
function h3SpeedOfParams(p) {
  p = p || {};
  return (p.h3_tristep || p.h3_turbo) ? 'fast' : (p.h3_flex ? 'flex' : (p.h3_flex_turbo ? 'flex_turbo' : 'best'));
}
// H3-04: "Fast" / "Best", for any place that shows a submitted or running
// H3 job's params — the Now card, a queue row, the info modal. One
// function so all three read the same label from the same field.
function h3JobSpeedLabel(p) {
  if (!p || p.engine !== 'h3') return '';
  if (h3SpeedOfParams(p) === 'fast') return 'Fast';
  if (h3SpeedOfParams(p) === 'flex') return 'Pika H3 Flex';
  if (h3SpeedOfParams(p) === 'flex_turbo') return 'H3 Flex Turbo';
  return p.h3_v13 ? 'Best · lightx2v v1.3' : 'Best';
}
// "Hailuo H3 · Standard 10s" — the shape a submitted H3 job asked for,
// looked up from its own h3_tier (never re-derived from the CURRENT form
// state, which may have moved on to a different shot by the time this job
// is showing in the queue or the Now card).
function h3JobShapeLabel(p) {
  if (!p || p.engine !== 'h3') return '';
  const cell = h3TierByKey(p.h3_tier || '');
  const shape = cell ? `${cell.quality_label} ${cell.length_label}` : (p.h3_tier || '');
  return ['Hailuo H3', shape].filter(Boolean).join(' · ');
}

// The Fast half: select it when ready; otherwise the one click that makes it
// ready (install the adapter, or say which update is needed).
async function h3SpeedClick(which) {
  if (which === 'flex') {
    const fx = h3FlexState();
    if (fx.available) { setH3Speed('flex'); return; }
    const toastF = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
    if (!fx.supported) {
      toastF(distText('Pika H3 Flex needs a newer Hailuo H3 runner. Run "Update Hailuo H3 runner" in the '
             + 'Pinokio sidebar — your weights stay.',
             'Pika H3 Flex needs a newer Hailuo H3 runner. Phosphene brings the Hailuo H3 runner up to date by itself; quit and reopen Phosphene if this stays.'), 'danger');
      return;
    }
    // Show the note (credit + the Get button) for the one explicit click.
    _h3SetSpeedPref('flex');
    _h3ApplySpeed();
    _h3SpeedRepriced();
    return;
  }
  if (which !== 'fast') { setH3Speed('best'); return; }
  const tri = h3TriStepState();
  if (tri.available) { setH3Speed('fast'); return; }
  const toast = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
  if (!tri.supported) {
    toast(distText('Fast needs a newer Hailuo H3 runner. Run "Update Hailuo H3 runner" in the Pinokio '
          + 'sidebar — your weights stay.',
          'Fast needs a newer Hailuo H3 runner. Phosphene brings the Hailuo H3 runner up to date by itself; quit and reopen Phosphene if this stays.'), 'danger');
    return;
  }
  if (tri.installing) { toast('The Fast adapter is already installing — watch the log.', 'ok'); return; }
  // H3-27: a native confirm() blocks the tab and reads as a browser dialog,
  // not the panel's own UI (the same rule Stop's confirmation follows).
  // Replace it with the panel's existing "toast + action link" confirm
  // pattern (used for the engine-offer nudge, install prompts elsewhere) —
  // one informative toast, one explicit click to proceed, nothing blocking.
  // Also: when Fast can't install right now (or before the user commits),
  // mention Turbo if it's ALREADY installed — a real quicker-than-Best
  // option this click used to leave unmentioned entirely.
  const turbo = h3TurboState();
  const turboHint = (turbo && turbo.available)
    ? ' Turbo (already installed) is a quicker option too — pick it from this same switch after Best.'
    : '';
  const el = phosToast(
    'Install Fast — 180 MB adapter from ' + (tri.repo || 'Kijai/MiniMax-H3_comfy')
    + ' (a conversion of ' + (tri.source_repo || 'TaoLiveAIGC/TaoMate-H3') + '). License: '
    + (tri.license || 'MiniMax H3 Community License') + ', same terms as H3 itself.' + turboHint,
    { icon: 'ph-download-simple', duration: 12000 });
  if (!el) return;
  const a = document.createElement('a');
  a.href = '#';
  a.className = 'phos-toast-action';
  a.textContent = 'Install Fast';
  a.onclick = async (ev) => {
    ev.preventDefault();
    el.remove();
    try {
      const r = await fetch('/h3/tristep/install', { method: 'POST' });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
      _h3SetSpeedPref('fast');
      toast(j.already_installed ? 'Fast is already installed.'
        : 'Installing Fast (180 MB) — watch the log. It switches on by itself when it lands.', 'ok');
    } catch (e) {
      toast('Fast install: ' + (e.message || 'failed'), 'danger');
    }
  };
  el.appendChild(a);
}
document.querySelectorAll('#h3SpeedGroup [data-h3-speed]').forEach(b => {
  b.onclick = () => {
    if (b.classList.contains('flex-locked')) {
      if (typeof phosToast === 'function') phosToast(H3_FLEX_SPEED_TIP, { kind: 'info' });
      return;
    }
    h3SpeedClick(b.dataset.h3Speed);
  };
});

// The Upscale & Face Fix pair, priced from the cell's own numbers: the
// checkbox beside Generate and the estimate line in the footer.
function h3FaceFixOn() {
  const cell = (typeof h3CurrentCell === 'function') ? h3CurrentCell() : null;
  if (cell && cell.fast_hd) return true;   // Fast HD always finishes with it
  return ((document.getElementById('h3_upscale') || {}).value) === 'ltx_x2';
}
function _h3SyncFaceFixEta() {
  const cell = h3CurrentCell();
  const allowed = (H3.upscale_modes || []).indexOf('ltx_x2') !== -1;
  const row = document.getElementById('h3FaceFixFooterRow');
  // Fast HD always finishes with it, so the toggle has nothing to decide.
  if (row) row.hidden = !allowed || !!(cell && cell.fast_hd);
  const eta = document.getElementById('h3FaceFixFooterEta');
  if (eta) {
    eta.textContent = (cell && cell.facefix_min != null)
      ? '+' + (Math.round(cell.facefix_min * 2) / 2) + ' min · 2× · face and sound kept'
      : '2× · face and sound kept';
  }
  const legacy = document.getElementById('h3FaceFixEta');
  if (legacy) {
    legacy.textContent = 'optional · 2× · face and sound kept'
      + ((cell && cell.facefix_min != null)
        ? ' · +' + (Math.round(cell.facefix_min * 2) / 2) + ' min' : " · about the render's time again");
  }
}
// The footer line: "Draft · Fast ≈ 3 min" / "High · Best + Face Fix ≈ 44 min".
function h3EstimateLine() {
  const cell = h3CurrentCell();
  if (!cell) return '';
  // 4.19: Extend and Add sound are priced by the 5 s window they run in, not
  // by the Length strip (which they do not use).
  const _mode = (typeof currentMode !== 'undefined') ? currentMode : '';
  if (_mode === 'extend' || _mode === 'v2a') {
    const win = h3CellFor(cell.quality, '5s') || cell;
    const per = h3CellEtaMin(win);
    const fmt = (m) => h3FmtEtaMin(m).replace(/^~/, '').replace(/ · batch$/, '');
    if (_mode === 'extend') {
      const sec = Math.max(0.5, Math.min(15, parseFloat((document.getElementById('extend_seconds') || {}).value) || 5));
      const windows = 1 + Math.max(0, Math.ceil((sec * 24 + 1 - 124) / 123));
      return 'Extend +' + sec.toFixed(1) + ' s · ' + win.quality_label + ' · ' + windows
        + ' window' + (windows > 1 ? 's' : '') + ' ≈ ' + fmt(per * windows);
    }
    // Add sound never upscales (the picture is the user's), so a Fast HD pick
    // costs only its H3 pass here.
    const v2aPer = win.fast_hd ? (_h3TriStepMin(win) || win.tristep_min || per) : per;
    return 'Add sound · picture kept · ≈ ' + fmt(v2aPer) + ' per 5 s of clip';
  }
  const fast = h3TriStepOn(cell);
  let m = h3CellEtaMin(cell);
  if (cell.fast_hd) {
    // Already includes its Upscale & Face Fix (h3CellEtaMin prices it whole).
    return 'Fast HD · ' + cell.width + '×' + cell.height + ' → ' + cell.final_width + '×'
      + cell.final_height + ' ≈ ' + h3FmtEtaMin(m).replace(/^~/, '').replace(/ · batch$/, '');
  }
  const flexLine = !fast && typeof h3FlexOn === 'function' && h3FlexOn(cell);
  let txt = cell.quality_label + ' · ' + (fast ? 'Fast' : (flexLine ? 'Pika H3 Flex' : 'Best'));
  if (h3FaceFixOn()) {
    txt += ' + Face Fix';
    if (cell.facefix_min == null) return txt;
    m += cell.facefix_min;
  }
  // H3-24: this used to round to the nearest HALF minute below 10 min
  // (Math.round(m * 2) / 2, e.g. "6.5") while the chip and the Shot setup
  // summary round the SAME cell to the nearest whole minute (h3FmtEtaMin /
  // the server's own _fmt_eta) — one cell, three different numbers on
  // screen at once. One formatter now, everywhere.
  return txt + ' ≈ ' + h3FmtEtaMin(m).replace(/^~/, '').replace(/ · batch$/, '');
}

// Adapter-slot pills. Bound at parse time like the Turbo / Steps groups —
// the row itself is hidden until Turbo and a LoRA actually collide.
document.querySelectorAll('#h3LoraSlotGroup [data-h3-lora-slot]').forEach(b => {
  b.onclick = () => setH3LoraSlot(b.dataset.h3LoraSlot);
});

// The switcher earns its place only when there is a real choice to make: at
// least two ACTIONABLE engines on the current workflow. One engine means the
// control and its divider both disappear — which is exactly what the ~80% of
// users under 64 GB have always seen, preserved through the move to the
// header. An `announced` engine is news, not a choice: it rides along once
// the switcher is already up, but it must never bring it up on its own, or a
// 32 GB Mac gets a permanently dead chip — the exact noise the capability
// gate exists to prevent. (Old name kept: it is what the rest of this file
// calls.)
function _engineRowVisible() {
  const count = ENGINES.filter(e => engineRenderable(e) && e.state !== 'announced').length;
  return count > 1 || (_currentSurface() === 'music' && count === 1);
}

// ---- The header switcher ----------------------------------------------------
// Rendered entirely from the registry. Every visual decision — label, mark,
// accent, badge, tooltip, order — comes off the engine's own row, so this
// THE MODE LIST IS DRAWN FROM THE REGISTRY, AND IT NEVER CHANGES SHAPE
// (redesign v2.1, option A). Every model brings its own abilities and the next
// one will bring different ones, so the list of things you can make is the
// same on every engine: switching model never moves a chip or makes one vanish.
// What the registry adds is WHO can make each thing:
//
//   * a chip the active engine cannot render stays in place, dimmed
//     (.eng-foreign), and says which model does it ("uses H3"); clicking it
//     switches to that model (modeChipEngineClick) instead of the old silent
//     bounce, or opens its install card when it is not installed yet;
//   * where the engines on this Mac DISAGREE about a chip, the chip carries
//     the marks of the ones that can do it. A chip every engine can do carries
//     none, so the marks only ever say something;
//   * a chip NO engine on this Mac can ever render (Add sound on a Mac that
//     cannot run H3) is the one thing that does fold: it is not a task here.
//
// The authority stays engineServesMode() and make_job server-side; this only
// draws it. Registry-driven, so a third engine marks itself with no new code.
// The Remix parent pill is a UI GROUP whose children are real modes, so it
// counts as served when the engine serves ANY of them.
function _modeChipMode(chip) {
  if (chip.dataset.lipsync) return 'a2v';
  return chip.dataset.mode || '';
}
function _engineServesChip(e, mode) {
  if (mode === 'remix') {
    const kids = (typeof REMIX_MODES !== 'undefined' && REMIX_MODES) ? REMIX_MODES : ['ingredients'];
    return kids.some(m => engineServesMode(e, m));
  }
  return engineServesMode(e, mode);
}
function _engineShort(e) {
  return e.short || String(e.label || e.id).split(' ').pop();
}
function _videoEngines() {
  return ENGINES.filter(e => e.state !== 'announced' && (e.surfaces || ['video']).indexOf('video') !== -1
    && (e.builtin || engineStatus(e).capable));
}
function syncModeStripToEngine() {
  const e = engineById(currentEngine());
  if (!e) return;
  const pool = _videoEngines();
  // H3 Flex picked: a mode H3 renders but Flex does not is greyed AND refuses
  // the click (modeChipEngineClick), with the plain reason as its tooltip.
  const flexPicked = e.id === 'h3' && typeof h3FlexPickedAny === 'function' && h3FlexPickedAny();
  document.querySelectorAll('#modeGroup .mode-chip').forEach(chip => {
    const mode = _modeChipMode(chip);
    if (!mode) return;
    let ok = _engineServesChip(e, mode);
    const flexLocked = ok && flexPicked && !(mode === 'remix'
      ? ((typeof REMIX_MODES !== 'undefined' && REMIX_MODES) ? REMIX_MODES : []).some(m => h3FlexServesMode(m))
      : h3FlexServesMode(mode));
    if (flexLocked) ok = false;
    chip.classList.toggle('flex-locked', flexLocked);
    if (flexLocked) {
      if (chip.dataset.flexTitle == null) chip.dataset.flexTitle = chip.getAttribute('title') || '';
      chip.setAttribute('title', H3_FLEX_LOCK_TIP);
      chip.setAttribute('aria-disabled', 'true');
    } else if (chip.dataset.flexTitle != null) {
      if (chip.dataset.flexTitle) chip.setAttribute('title', chip.dataset.flexTitle);
      else chip.removeAttribute('title');
      delete chip.dataset.flexTitle;
      chip.removeAttribute('aria-disabled');
    }
    const by = pool.filter(x => _engineServesChip(x, mode));
    chip.classList.toggle('eng-foreign', !ok);
    chip.classList.toggle('mode-reachable', by.length > 0);
    chip.classList.toggle('mode-unreachable', by.length === 0);
    const sub = chip.querySelector('.mc-sub');
    if (sub) {
      // Flex-locked: plain H3 (the engine already picked) is the way to it.
      if (flexLocked) sub.dataset.uses = _engineShort(e);
      else if (!ok && by.length) sub.dataset.uses = _engineShort(by[0]);
      else delete sub.dataset.uses;
    }
    let marks = chip.querySelector('.mc-eng');
    const differs = pool.length > 1 && by.length > 0 && by.length < pool.length;
    if (!differs) { if (marks) marks.remove(); return; }
    if (!marks) {
      marks = document.createElement('span');
      marks.className = 'mc-eng';
      marks.setAttribute('aria-hidden', 'true');
      chip.appendChild(marks);
    }
    marks.innerHTML = by.map(x => `<svg class="ph" style="--eng-accent:${escapeHtml(x.accent || 'currentColor')}"><use href="#${escapeHtml(x.mark)}"/></svg>`).join('');
  });
}

// A dimmed chip is a door to the model that makes it, not a dead end. Runs in
// the capture phase ahead of the chip's own handler (characters.js), so the
// choice of engine is settled before setMode() asks _syncEngineForMode() which
// engine the user wants: the preference is written, setMode then lands on it
// through the normal gates. A model that is not installed yet answers with its
// own install card and the mode is left alone.
function modeChipEngineClick(ev) {
  const chip = ev.target.closest('#modeGroup .mode-chip.eng-foreign');
  if (!chip) return;
  // Greyed because H3 Flex is the pick: nothing switches; the reason shows.
  if (chip.classList.contains('flex-locked')) {
    ev.stopImmediatePropagation(); ev.preventDefault();
    if (typeof phosToast === 'function') phosToast(H3_FLEX_LOCK_TIP, { kind: 'info' });
    return;
  }
  const mode = _modeChipMode(chip);
  const target = _videoEngines().find(x => _engineServesChip(x, mode));
  if (!target) return;
  const st = engineStatus(target);
  if (!target.builtin && !st.available) {
    ev.stopImmediatePropagation(); ev.preventDefault();
    engineSegClick(target.id);
    return;
  }
  try { localStorage.setItem(H3_ENGINE_LS_KEY, target.id); } catch (e) {}
  if (typeof phosToast === 'function') {
    const name = (chip.firstChild && chip.firstChild.textContent || '').trim();
    phosToast(`${name} is made with ${target.label}${target.generation ? ' ' + target.generation : ''} — switched model.`, { duration: 2600 });
  }
}

// function has no idea which engines exist. Re-run on: boot, every setEngine,
// a workflow-tab change, and any /status tick that moves an install.
// The picker is a DROPDOWN, not a segmented row. Two reasons, and the second
// is the one that forced it:
//   1. Header room. Two full segments plus the health cluster already
//      overflowed a 14" window; a third engine (Flux Video) could not fit at
//      any width. A trigger showing only the ACTIVE engine costs one slot no
//      matter how many engines exist.
//   2. "Which one is selected" has to survive being glanced at. In a segmented
//      row that is carried entirely by a highlight; a dropdown states it.
//
// The MENU IS PORTALED TO <body> and positioned fixed, because <header> is
// overflow:hidden — the same clipping that produced the "avatar cut off"
// report. A menu rendered inside the header would be sliced at its edge.
window._engineMenuOpen = false;

function _engineMenuEl() {
  let m = document.getElementById('engineMenu');
  if (!m) {
    m = document.createElement('div');
    m.id = 'engineMenu';
    m.setAttribute('role', 'listbox');
    m.hidden = true;
    document.body.appendChild(m);     // portal: escape header overflow:hidden
  }
  return m;
}

// H3-17: the menu is portaled to <body> (it has to be — the header is
// overflow:hidden and would slice it) which is exactly what breaks Tab
// order: a portaled element sits at the END of the DOM regardless of where
// its trigger lives, so Tab from the trigger jumped clear over it to
// whatever else the header holds (the health chip). closeEngineMenu() used
// to only hide the menu, never give focus back anywhere, so a keyboard user
// who opened it with Enter/Space had no idea where focus went next.
function closeEngineMenu(opts) {
  window._engineMenuOpen = false;
  const m = document.getElementById('engineMenu');
  if (m) m.hidden = true;
  const t = document.querySelector('#engineSwitch .eng-trigger');
  if (t) {
    t.setAttribute('aria-expanded', 'false');
    if (!opts || opts.returnFocus !== false) t.focus();
  }
}

function toggleEngineMenu() {
  const m = _engineMenuEl();
  if (window._engineMenuOpen) { closeEngineMenu(); return; }
  const trig = document.querySelector('#engineSwitch .eng-trigger');
  if (!trig) return;
  const r = trig.getBoundingClientRect();
  m.style.minWidth = Math.max(r.width, 240) + 'px';
  m.hidden = false;
  // The trigger sits beside Generate, at the foot of the column: the menu
  // opens UPWARD when there is no room under it, and never runs off an edge.
  const mh = m.offsetHeight, mw = m.offsetWidth;
  const roomUp = r.top - 6 - mh >= 8, roomDown = r.bottom + 6 + mh <= window.innerHeight - 8;
  const up = roomUp && (r.top > window.innerHeight / 2 || !roomDown);
  m.style.top = (up ? r.top - 6 - mh : Math.min(r.bottom + 6, window.innerHeight - mh - 8)) + 'px';
  m.style.left = Math.max(8, Math.min(r.left, window.innerWidth - mw - 8)) + 'px';
  window._engineMenuOpen = true;
  trig.setAttribute('aria-expanded', 'true');
  // Move focus INTO the menu (the active engine's row, or the first row) —
  // a keyboard user who just opened this had no other way to reach it,
  // since Tab from the trigger skips clear over the portaled menu.
  const opts = Array.from(m.querySelectorAll('.eng-opt'));
  const target = opts.find(o => o.classList.contains('active')) || opts[0];
  if (target) target.focus();
}

document.addEventListener('click', (ev) => {
  if (!window._engineMenuOpen) return;
  if (ev.target.closest('#engineMenu') || ev.target.closest('#engineSwitch')) return;
  closeEngineMenu({ returnFocus: false });
}, true);
document.addEventListener('keydown', (ev) => {
  if (!window._engineMenuOpen) return;
  if (ev.key === 'Escape') { closeEngineMenu(); return; }
  // Arrow Up/Down move focus between rows; Home/End jump to the ends. Enter
  // and Space already work — they're plain <button> elements, so the
  // browser fires their onclick natively when focused.
  if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp'
      && ev.key !== 'Home' && ev.key !== 'End') return;
  const m = document.getElementById('engineMenu');
  if (!m) return;
  const opts = Array.from(m.querySelectorAll('.eng-opt'));
  if (!opts.length) return;
  ev.preventDefault();
  const cur = opts.indexOf(document.activeElement);
  let next;
  if (ev.key === 'Home') next = 0;
  else if (ev.key === 'End') next = opts.length - 1;
  else if (ev.key === 'ArrowDown') next = cur < 0 ? 0 : (cur + 1) % opts.length;
  else next = cur < 0 ? opts.length - 1 : (cur - 1 + opts.length) % opts.length;
  opts[next].focus();
});
window.addEventListener('resize', () => closeEngineMenu({ returnFocus: false }));

// ---- H3 Flex: its own entry in the engine picker (4.19.15, preview) --------
// Not a server engine: it renders on H3 (engine=h3, h3_flex=1, the Flex
// adapter + its six-step sampler), so every H3 gate, price and queue path
// stays the one H3 already has. The picker shows it as a sibling of H3 so it
// can be found, downloaded and chosen the way an engine is.
const H3FLEX_ID = 'h3flex';
function h3FlexPicked() {
  return currentEngine() === 'h3' && h3SpeedPref() === 'flex';
}
// The entry, built from H3's own row (accent, mark, surfaces), or null when
// this Mac's H3 runner cannot take Flex at all (an older H3 pack).
function h3FlexPickerEntry() {
  const h3e = engineById('h3');
  if (!h3e || !engineRenderable(h3e)) return null;
  const fx = h3FlexState();
  const h3st = engineStatus(h3e);
  if (h3st.available && !fx.supported && !fx.downloaded) return null;
  return Object.assign({}, h3e, {
    id: H3FLEX_ID, label: 'H3 Flex', generation: '', preview: true, virtual: true,
    sublabel: 'Pika H3 Flex · preview',
    tagline: "Pika Labs' six-step H3: about 2.2× faster than Best. Preview.",
  });
}
// Its state, in the same words the menu uses for every engine.
function h3FlexPickerStatus() {
  const h3st = engineStatus(engineById('h3'));
  const fx = h3FlexState();
  const d = fx.download || {};
  return {
    capable: !!h3st.capable,
    h3_ready: !!h3st.available,
    available: !!(h3st.available && fx.available),
    installing: !!fx.installing,
    progress: fx.installing
      ? (d.status === 'building' ? 'preparing'
        : Math.min(99, Math.round(100 * (d.mb || 0) / (d.total_mb || 2639))) + '%')
      : '',
    error: d.error || '',
    download_gb: fx.download_gb || 2.8,
  };
}
function _h3FlexBadge(st, h3e) {
  if (!st.h3_ready) return (h3e.install_size || '').replace(/^~/, '');
  if (st.installing) return st.progress;
  if (!st.available) return st.download_gb + ' GB';
  return '';
}
function _h3FlexTooltip(st, h3e) {
  const credit = ' By Pika Labs, running on your Mac. Powered by MiniMax H3.';
  if (!st.h3_ready) return 'H3 Flex (preview) — needs Hailuo H3 first ('
    + (h3e.install_size || '') + '); then its 2.8 GB adapter. Click to get them.' + credit;
  if (st.installing) return 'H3 Flex (preview) — downloading and preparing (' + st.progress + ').' + credit;
  if (!st.available) return 'H3 Flex (preview) — click to download it (' + st.download_gb
    + ' GB from Pika Labs, prepared on your Mac).' + credit;
  return "H3 Flex (preview) — Pika Labs' six-step H3, about 2.2× faster than Best." + credit;
}
// Flex picked from a mode H3 itself does not render (an LTX-only one): move
// to the nearest mode Flex renders first, instead of a click that does nothing.
// h3FlexFit says so once the engine has switched.
function _h3FlexEnterMode(h3e) {
  if (engineServesMode(h3e, currentMode)) return true;
  const to = _h3FlexNearestMode(currentMode);
  if (!to || !engineServesMode(h3e, to) || typeof setMode !== 'function') return false;
  _h3FlexPinImageSelect(to);
  setMode(to);
  if (typeof phosToast === 'function') {
    phosToast('H3 Flex makes videos from text or an image, so the form is on '
              + (to === 'i2v' ? 'Image' : 'Text') + ' now.', { kind: 'info', duration: 4200 });
  }
  return engineServesMode(h3e, currentMode);
}
// Picking it: H3 with the Flex sampler. Missing pieces download on the same
// click: H3 itself through its own install card (Flex follows on the first
// status tick that finds H3 ready), else the Flex adapter right here.
function h3FlexPickerClick() {
  const h3e = engineById('h3');
  const st = h3FlexPickerStatus();
  if (!h3e || !st.capable) return;
  _h3SetSpeedPref('flex');
  try { localStorage.setItem('phos_h3flex_wanted', '1'); } catch (e) {}
  if (!st.h3_ready) { engineSegClick('h3'); return; }
  if (typeof _h3FlexEnterMode === 'function' ? !_h3FlexEnterMode(h3e) : !engineServesMode(h3e, currentMode)) return;
  setEngine('h3');
  if (typeof setH3Speed === 'function') setH3Speed('flex');
  if (typeof h3FlexFit === 'function') h3FlexFit();
  if (!st.available && !st.installing) h3FlexInstall();
  renderEngineSwitch();
}

// ---- H3 Flex Turbo: its own picker entry (5.0, Pro, preview) --------------
// Not a server engine either: engine=h3, h3_flex_turbo=1. A Pro option.
const H3FLEXTURBO_ID = 'h3flexturbo';
function h3FlexTurboPicked() {
  return currentEngine() === 'h3' && h3SpeedPref() === 'flex_turbo';
}
function h3FlexTurboPickerEntry() {
  const h3e = engineById('h3');
  if (!h3e || !engineRenderable(h3e)) return null;
  const ft = h3FlexTurboState();
  const h3st = engineStatus(h3e);
  // A Pro member whose H3 runner cannot do it yet has nothing to pick; a Free user always sees
  // it, marked Pro (owner, 6 Nov: the option was silently missing for Free, so nobody knew it
  // existed). Clicking it as Free opens the Pro panel.
  if (ft.pro_active && h3st.available && !ft.supported && !ft.downloaded) return null;
  return Object.assign({}, h3e, {
    id: H3FLEXTURBO_ID, label: 'Faster Flex Turbo', generation: '', preview: true, pro: true, virtual: true,
    sublabel: 'Faster Flex Turbo · quick drafts · Pro',
    tagline: 'Quick drafts: try ideas fast, then render the keeper. Part of Phosphene Pro.',
  });
}
function h3FlexTurboPickerStatus() {
  const h3st = engineStatus(engineById('h3') || {});
  const ft = h3FlexTurboState();
  const d = ft.download || {};
  return {
    capable: !!h3st.capable,
    h3_ready: !!h3st.available,
    pro_active: !!ft.pro_active,
    available: !!(h3st.available && ft.available),
    installing: !!ft.installing,
    progress: ft.installing ? Math.min(99, Math.round(100 * (d.mb || 0) / (d.total_mb || 295))) + '%' : '',
    error: d.error || '',
    download_gb: ft.download_gb || 0.31,
  };
}
function _h3FlexTurboTooltip(st) {
  const credit = ' By akhaliq (Pika H3 Flex by Pika Labs + LightX2V Turbo), running on your Mac. Powered by MiniMax H3.';
  if (!st.pro_active) return 'Faster Flex Turbo is part of Phosphene Pro: quick drafts, so you can try ideas fast and render the keeper after. Click to see Pro.';
  if (!st.h3_ready) return 'H3 Flex Turbo needs Hailuo H3 first. Click to get it.' + credit;
  if (st.installing) return 'H3 Flex Turbo: downloading (' + st.progress + ').' + credit;
  if (!st.available) return 'H3 Flex Turbo: click to download it (' + st.download_gb + ' GB).' + credit;
  return 'H3 Flex Turbo (faster draft): a faster H3 Flex, about 1.4× faster and a little softer. Try ideas fast, then render the keeper on Flex or Best.' + credit;
}
function _h3FlexTurboNote(cell) {
  const ft = h3FlexTurboState();
  if (!ft.pro_active) return 'H3 Flex Turbo is part of Phosphene Pro. This render uses H3 Flex.';
  if (!ft.supported) return 'H3 Flex Turbo needs a newer H3 runner.';
  if (ft.installing) { const d = ft.download || {}; return 'Downloading H3 Flex Turbo: ' + (d.mb || 0) + ' / ' + (d.total_mb || 295) + ' MB.'; }
  if (!ft.downloaded) return 'H3 Flex Turbo: pick it in the engine picker to download it (0.3 GB).';
  if (h3FlexTurboOn(cell)) return '';
  return 'H3 Flex Turbo renders Text and Image on 5-second windows; this shape uses Best.';
}
function _h3FlexTurboMenuRow(e, active, surfaceMode) {
  const st = h3FlexTurboPickerStatus();
  const badge = !st.pro_active ? '' : !st.h3_ready ? (engineById('h3').install_size || '').replace(/^~/, '')
    : st.installing ? st.progress : !st.available ? st.download_gb + ' GB' : '';
  const ok = engineServesMode(e, surfaceMode);
  const cls = 'eng-opt eng-opt-flex eng-opt-pro' + (e.id === active ? ' active' : '')
    + (!st.available ? ' needs-install' : '') + (st.available && !ok ? ' inert' : '');
  return `<button type="button" class="${cls}" data-engine="${H3FLEXTURBO_ID}"
      role="option" aria-selected="${e.id === active ? 'true' : 'false'}"
      style="--eng-accent:${escapeHtml(e.accent)};--eng-dim:${escapeHtml(e.accent_dim)};--eng-soft:${escapeHtml(e.accent_soft)}"
      title="${escapeHtml(_h3FlexTurboTooltip(st))}">
    <span class="eng-mark"><svg class="ph" aria-hidden="true"><use href="#${escapeHtml(e.mark)}"/></svg></span>
    <span class="eng-opt-body">
      <span class="eng-opt-top">
        <span class="eng-seg-name">${escapeHtml(e.label)}</span><span class="eng-badge eng-preview">preview</span><span class="eng-badge eng-pro">Pro</span>${
        badge ? `<span class="eng-badge offer">${escapeHtml(badge)}</span>` : ''}
      </span>
      <span class="eng-opt-sub">${escapeHtml(e.tagline)}</span>
    </span>${
    e.id === active ? '<svg class="ph eng-tick" aria-hidden="true"><use href="#ph-check-bold"/></svg>' : ''}
  </button>`;
}
async function h3FlexTurboInstall() {
  const toast = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
  try {
    const r = await fetch('/h3/flex-turbo/install', { method: 'POST' });
    const j = await r.json().catch(() => ({}));
    if (r.status === 402) { if (typeof openProModal === 'function') openProModal(); return; }
    if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
    toast(j.already_installed ? 'H3 Flex Turbo is already installed.'
      : 'Downloading H3 Flex Turbo (0.3 GB). It switches on by itself when ready.', 'ok');
  } catch (e) {
    toast('H3 Flex Turbo: ' + (e.message || 'failed'), 'danger');
  }
}
// Picking it: Pro opens the Pro panel when Pro is off; otherwise H3 with the
// Turbo sampler, downloading the 0.3 GB LoRA on the same click.
function h3FlexTurboPickerClick() {
  const st = h3FlexTurboPickerStatus();
  if (!st.capable) return;
  if (!st.pro_active) {
    if (typeof openProModal === 'function') openProModal();
    else if (typeof phosToast === 'function') phosToast('H3 Flex Turbo is part of Phosphene Pro.', { kind: 'info' });
    return;
  }
  _h3SetSpeedPref('flex_turbo');
  if (!st.h3_ready) { engineSegClick('h3'); return; }
  const h3e = engineById('h3');
  if (typeof _h3FlexEnterMode === 'function' ? !_h3FlexEnterMode(h3e) : !engineServesMode(h3e, currentMode)) return;
  setEngine('h3');
  if (typeof setH3Speed === 'function') setH3Speed('flex_turbo');
  if (typeof h3FlexFit === 'function') h3FlexFit();
  if (!st.available && !st.installing) h3FlexTurboInstall();
  renderEngineSwitch();
}
// Status ticks: redraw the picker when Flex's state moves, and finish what
// a click on H3 Flex asked for once H3 itself has arrived.
let _h3FlexPickerSig = '';
function _h3FlexPickerSync() {
  const st = h3FlexPickerStatus();
  let wanted = false;
  try { wanted = localStorage.getItem('phos_h3flex_wanted') === '1'; } catch (e) {}
  if (wanted && st.available) { try { localStorage.removeItem('phos_h3flex_wanted'); } catch (e) {} }
  if (wanted && st.h3_ready && !st.available && !st.installing && !st.error
      && h3FlexState().supported && h3SpeedPref() === 'flex' && !window._h3FlexAutoAsked) {
    window._h3FlexAutoAsked = true;
    h3FlexInstall();
  }
  const ft = (typeof h3FlexTurboState === 'function') ? h3FlexTurboPickerStatus() : {};
  const sig = [st.h3_ready, st.available, st.installing, st.progress, h3SpeedPref(),
               ft.available, ft.installing, ft.progress, ft.pro_active].join('|');
  if (sig !== _h3FlexPickerSig) {
    _h3FlexPickerSig = sig;
    if (typeof renderEngineSwitch === 'function') { try { renderEngineSwitch(); } catch (e) {} }
  }
}

// ---- Image models: the same picker on the Images tab -------------------------
// Owner, 5 Nov 2026: "for pictures, you select inside the picture thingy" — the
// Images tab chose its model in a long select inside the form while Video and
// Compose chose it at the top of the column. Now the image MODELS are registry
// rows (surfaces=["image"]) drawn by this same picker, and the form's
// #imgStudioEngine keeps only the picked model's versions (FLUX 3 Q8/Q6/Q4,
// Reference Edit Fast/Standard/Quality, P-H3-Image Draft/Standard/High, MiniMax-H3-Image Draft/Standard/High). The
// select stays the form value every Images function reads, so nothing that
// renders, estimates or restores an image changed shape.
const IMG_MODEL_LS_KEY = 'phos_img_model_versions';

function imageModelRows() {
  return ENGINES.filter(e => (e.surfaces || []).indexOf('image') !== -1);
}

// Which image model owns an #imgStudioEngine value ('auto' -> FLUX.2 klein).
function imageModelForValue(v) {
  return imageModelRows().find(e => (e.image_engines || []).indexOf(v) !== -1) || null;
}

function _imgVersionsMemory() {
  try { return JSON.parse(localStorage.getItem(IMG_MODEL_LS_KEY) || '{}') || {}; } catch (e) { return {}; }
}

// The status of one image model, in the picker's vocabulary. Download state
// is per version and lives on the form's chip; the one thing the picker must
// say is that a model can never run on this Mac (every version needs more RAM).
function imageModelStatus(e) {
  const st = (typeof imgStudioEngineStatus === 'function') ? imgStudioEngineStatus() : {};
  const infos = (e.image_engines || []).map(v => st[v]).filter(Boolean);
  if (infos.length && infos.every(i => i.fits_mac === false)) {
    const gb = Math.min.apply(null, infos.map(i => Number(i.mac_gb_needed) || 0));
    return { capable: false, available: false, ram_floor_gb: gb };
  }
  return { capable: true, available: true };
}

// Show only the picked model's versions in the form; hide the select when the
// model has one version (its chip and estimate stay). Keeps the user's version
// per model, so FLUX 3 -> Ideogram -> FLUX 3 comes back on the same Q6.
function syncImageVersions() {
  const sel = document.getElementById('imgStudioEngine');
  if (!sel) return null;
  const model = imageModelForValue(sel.value);
  if (!model) return null;
  const own = model.image_engines || [];
  let shown = 0;
  Array.from(sel.options).forEach(o => {
    if (o.dataset.alwaysHidden === undefined) o.dataset.alwaysHidden = o.hidden ? '1' : '';
    const mine = own.indexOf(o.value) !== -1 && o.dataset.alwaysHidden !== '1';
    o.hidden = !mine;
    if (mine) shown++;
  });
  sel.hidden = shown <= 1;
  const row = sel.closest('.studio-engine-row');
  if (row) row.classList.toggle('single-version', shown <= 1);
  const only = document.getElementById('imgStudioVersionOnly');
  if (only) {
    only.hidden = shown > 1;
    const o = sel.options[sel.selectedIndex];
    only.textContent = (shown <= 1 && o) ? o.textContent : '';
  }
  const mem = _imgVersionsMemory();
  if (mem[model.id] !== sel.value) {
    mem[model.id] = sel.value;
    try { localStorage.setItem(IMG_MODEL_LS_KEY, JSON.stringify(mem)); } catch (e) {}
  }
  return model;
}

// Picking an image model: its last-used version (or its first), then the same
// change chain the select itself runs, so every Images gate re-evaluates.
function pickImageModel(id) {
  const e = engineById(id);
  const sel = document.getElementById('imgStudioEngine');
  if (!e || !sel) return;
  if (!imageModelStatus(e).capable) return;
  const own = (e.image_engines || []).filter(v => {
    const o = Array.from(sel.options).find(x => x.value === v);
    return o && !o.disabled && o.dataset.alwaysHidden !== '1';
  });
  if (!own.length) return;
  const remembered = _imgVersionsMemory()[id];
  const want = own.indexOf(remembered) !== -1 ? remembered : own[0];
  if (sel.value !== want) {
    sel.value = want;
    sel.dispatchEvent(new Event('change', { bubbles: true }));
  }
  renderEngineSwitch();
}

function renderEngineSwitch() {
  renderLtxGenSwitch();   // the Remix-row switch; independent of the header row below
  const box = document.getElementById('engineSwitch');
  const div = document.getElementById('engineSwitchDivider');
  if (!box) return;
  const show = _engineRowVisible();
  box.hidden = !show;
  if (div) div.hidden = !show;
  if (!show) { box.innerHTML = ''; closeEngineMenu(); return; }

  const surfaceMode = _currentSurface() === 'music' ? 'music'
    : _currentSurface() === 'oneshot' ? 'oneshot' : currentMode;
  const osSurface = _currentSurface() === 'oneshot';
  const list = ENGINES.filter(engineRenderable);
  // H3 Flex sits right after H3 (preview; see h3FlexPickerEntry).
  // typeof: harnesses that lift renderEngineSwitch alone (test_music_engine) run without it.
  // Flex rows are Video-form variants; One Shot renders plain LTX or H3 parts.
  const videoSurface = _currentSurface() === 'video';
  const flexEntry = (videoSurface && typeof h3FlexPickerEntry === 'function') ? h3FlexPickerEntry() : null;
  if (flexEntry) {
    const at = list.findIndex(e => e.id === 'h3');
    list.splice(at + 1, 0, flexEntry);
  }
  const turboEntry = (videoSurface && typeof h3FlexTurboPickerEntry === 'function') ? h3FlexTurboPickerEntry() : null;
  if (turboEntry) {
    const at = list.findIndex(e => e.id === (flexEntry ? H3FLEX_ID : 'h3'));
    list.splice(at + 1, 0, turboEntry);
  }
  const imgSurface = _currentSurface() === 'image';
  const imgModel = imgSurface ? syncImageVersions() : null;
  const active = _currentSurface() === 'music' ? 'music'
    : imgSurface ? (imgModel ? imgModel.id : ((list[0] || {}).id))
    : osSurface ? ((typeof osCurrentEngine === 'function' && osCurrentEngine()) || 'ltx')
    : (turboEntry && h3FlexTurboPicked() ? H3FLEXTURBO_ID
      : (flexEntry && h3FlexPicked() ? 'h3flex' : currentEngine()));

  // ---- The trigger: the active engine, and nothing else ----
  const act = list.find(e => e.id === active) || list[0];
  if (act) {
    box.innerHTML = `<button type="button" class="eng-trigger" aria-haspopup="listbox"
        aria-expanded="false"
        style="--eng-accent:${escapeHtml(act.accent)};--eng-dim:${escapeHtml(act.accent_dim)};--eng-soft:${escapeHtml(act.accent_soft)}"
        title="${escapeHtml(act.virtual ? (act.id === H3FLEXTURBO_ID ? _h3FlexTurboTooltip(h3FlexTurboPickerStatus())
          : _h3FlexTooltip(h3FlexPickerStatus(), engineById('h3')))
          : _engineTooltip(act, engineStatus(act), imgSurface || engineServesMode(act, surfaceMode)))}">
      <span class="eng-mark"><svg class="ph" aria-hidden="true"><use href="#${escapeHtml(act.mark)}"/></svg></span>
      <span class="eng-seg-name">${escapeHtml(act.label)}</span>${
      act.generation ? `<span class="eng-seg-gen">${escapeHtml(act.generation)}</span>` : ''}${
      act.preview ? '<span class="eng-badge eng-preview">preview</span>' : ''}${
      act.pro ? '<span class="eng-badge eng-pro">Pro</span>' : ''}
      <svg class="ph eng-caret" aria-hidden="true"><use href="#ph-caret-down-bold"/></svg>
    </button>`;
    const t = box.querySelector('.eng-trigger');
    if (t) t.onclick = (ev) => { ev.stopPropagation(); toggleEngineMenu(); };
  }

  // ---- The menu: every engine, each stating its own state ----
  const menu = _engineMenuEl();
  menu.innerHTML = list.map(e => {
    if (e.virtual && e.id === H3FLEXTURBO_ID) return _h3FlexTurboMenuRow(e, active, surfaceMode);
    if (e.virtual) {
      const fst = h3FlexPickerStatus();
      const fBadge = _h3FlexBadge(fst, engineById('h3'));
      const fOffer = !fst.available;
      const fOk = engineServesMode(e, surfaceMode);
      const fCls = 'eng-opt eng-opt-flex' + (e.id === active ? ' active' : '')
        + (fOffer ? ' needs-install' : '') + (fst.available && !fOk ? ' inert' : '');
      return `<button type="button" class="${fCls}" data-engine="${H3FLEX_ID}"
          role="option" aria-selected="${e.id === active ? 'true' : 'false'}"
          style="--eng-accent:${escapeHtml(e.accent)};--eng-dim:${escapeHtml(e.accent_dim)};--eng-soft:${escapeHtml(e.accent_soft)}"
          title="${escapeHtml(_h3FlexTooltip(fst, engineById('h3')))}">
        <span class="eng-mark"><svg class="ph" aria-hidden="true"><use href="#${escapeHtml(e.mark)}"/></svg></span>
        <span class="eng-opt-body">
          <span class="eng-opt-top">
            <span class="eng-seg-name">${escapeHtml(e.label)}</span><span class="eng-badge eng-preview">preview</span>${
            fBadge ? `<span class="eng-badge offer">${escapeHtml(fBadge)}</span>` : ''}
          </span>
          <span class="eng-opt-sub">${escapeHtml(e.tagline)}</span>
        </span>${
        e.id === active ? '<svg class="ph eng-tick" aria-hidden="true"><use href="#ph-check-bold"/></svg>' : ''}
      </button>`;
    }
    const st = engineStatus(e);
    // Mac app, weights on this Mac, runtime being set up by the app itself: not an
    // offer and not a repair, just a calm "getting ready".
    const preparing = !e.builtin && !st.announced && st.capable && !st.available && !!st.preparing;
    const offer = !e.builtin && !st.announced && st.capable && !st.available && !preparing;
    // Every image model makes pictures; the form under the picker says what
    // each needs (a reference for Reference Edit).
    const modeOk = imgSurface || engineServesMode(e, surfaceMode);
    // Four badges, four different sentences. An OFFER (download / repair) is
    // worth the engine's own colour; a constraint is not.
    let badge = '', badgeClass = '';
    if (st.announced) { badge = 'soon'; }
    else if (offer) {
      // H3-05: "repairable" also covers the missing_q8_dit case (weights on
      // disk, only the local compact build is missing) — Pinokio's sidebar
      // shows ONLY a Build entry there, no Repair entry (pinokio.js), so a
      // "repair" badge sent the user looking for a button that doesn't
      // exist. venv/runner actually broken is the real repair case.
      badge = (st.repairable && st.reason === 'missing_q8_dit') ? 'build'
        : st.repairable ? 'repair'
        : (e.install_size || '').replace(/^~/, '');
      badgeClass = ' offer';
    } else if (preparing) { badge = 'getting ready'; }
    else if (st.available && !modeOk) { badge = (e.serves_label || '').toLowerCase().replace(' and ', ' · '); }
    else if (imgSurface && !st.capable) { badge = 'needs ' + (st.ram_floor_gb || '?') + ' GB'; }
    else if (e.preview) { badge = 'preview'; }
    // Inert = real but unreachable RIGHT NOW. Distinct from needs-install,
    // which IS reachable — that click is the install.
    const inert = st.announced || preparing || (st.available && !modeOk) || (imgSurface && !st.capable);
    const cls = 'eng-opt'
      + (e.id === active ? ' active' : '')
      + (offer ? ' needs-install' : '')
      + (inert ? ' inert' : '');
    // The tagline earns its place here in a way it never could in a segment:
    // the menu is where someone decides BETWEEN engines, so each row says what
    // it is for rather than only what it is called.
    return `<button type="button" class="${cls}" data-engine="${escapeHtml(e.id)}"
        role="option" aria-selected="${e.id === active ? 'true' : 'false'}"
        style="--eng-accent:${escapeHtml(e.accent)};--eng-dim:${escapeHtml(e.accent_dim)};--eng-soft:${escapeHtml(e.accent_soft)}"
        title="${escapeHtml(_engineTooltip(e, st, modeOk))}">
      <span class="eng-mark"><svg class="ph" aria-hidden="true"><use href="#${escapeHtml(e.mark)}"/></svg></span>
      <span class="eng-opt-body">
        <span class="eng-opt-top">
          <span class="eng-seg-name">${escapeHtml(e.label)}</span>${
          e.generation ? `<span class="eng-seg-gen">${escapeHtml(e.generation)}</span>` : ''}${
          badge ? `<span class="eng-badge${badgeClass}">${escapeHtml(badge)}</span>` : ''}
        </span>
        <span class="eng-opt-sub">${escapeHtml(e.tagline || e.sublabel || '')}</span>
      </span>${
      e.id === active ? '<svg class="ph eng-tick" aria-hidden="true"><use href="#ph-check-bold"/></svg>' : ''}
    </button>`;
  }).join('');

  menu.querySelectorAll('.eng-opt').forEach(b => {
    b.onclick = () => {
      closeEngineMenu();
      if (b.dataset.engine === 'h3flex') { h3FlexPickerClick(); return; }
      if (b.dataset.engine === H3FLEXTURBO_ID) { h3FlexTurboPickerClick(); return; }
      // Plain H3 from the picker means H3's own Speed, not Flex.
      if (b.dataset.engine === 'h3' && (h3SpeedPref() === 'flex' || h3SpeedPref() === 'flex_turbo')
          && typeof setH3Speed === 'function') {
        try { localStorage.removeItem('phos_h3flex_wanted'); } catch (e) {}
        setH3Speed('fast');
      }
      if (imgSurface) { pickImageModel(b.dataset.engine); return; }
      if (osSurface) {
        // One Shot keeps its own engine state (OS.engine); an engine that is
        // not installed opens its install card exactly as on Video.
        const oe = engineById(b.dataset.engine), ost = engineStatus(oe);
        if (oe && !oe.builtin && ost.capable && !ost.available) { engineSegClick(oe.id); return; }
        if (typeof osSetEngine === 'function') osSetEngine(b.dataset.engine);
        renderEngineSwitch();
        return;
      }
      engineSegClick(b.dataset.engine);
      renderEngineSwitch();
    };
  });

  // ---- LTX generation: only when this install has more than one ----
  // The server sends `generations` already filtered to the ones whose base pack
  // is on disk, and empty when there is no real choice, so this row simply does
  // not exist for a single-generation install. A generation is fixed per panel
  // process, so picking one restarts the panel (see /ltx/generation).
  const ltxEng = list.find(e => e.id === 'ltx');
  const gens = (ltxEng && ltxEng.generations) || [];
  if (gens.length > 1) {
    const row = document.createElement('div');
    row.className = 'eng-gen';
    row.innerHTML = _ltxGenSwitchHtml(gens, true);
    _wireLtxGenSwitch(row);
    menu.appendChild(row);
  }
}

// The generation switch, as markup. Shared by the engine menu and the Remix row
// so the two cannot drift: same buttons, same endpoint, same wording.
function _ltxGenSwitchHtml(gens, withNote) {
  return `<span class="eng-gen-label">LTX generation</span>
      <span class="eng-gen-btns" role="group" aria-label="LTX generation">${
      gens.map(g => `<button type="button" class="eng-gen-btn${g.active ? ' active' : ''}"
          data-gen="${escapeHtml(g.id)}" aria-pressed="${g.active ? 'true' : 'false'}"
          title="${g.active ? 'Rendering with LTX-' + escapeHtml(g.label)
                           : 'Switch to LTX-' + escapeHtml(g.label) + ' (restarts the panel)'}">${escapeHtml(g.label)}</button>`).join('')
      }</span>${withNote
        ? '<span class="eng-gen-note">Switching restarts the panel. Ingredients needs 2.3.</span>'
        : '<span class="eng-gen-note">Only one runs at a time - switching restarts the panel.</span>'}`;
}
function _wireLtxGenSwitch(root) {
  root.querySelectorAll('.eng-gen-btn').forEach(b => {
    b.onclick = () => { if (!b.classList.contains('active')) switchLtxGeneration(b.dataset.gen); };
  });
}
// The same switch beside the Remix tools, where Ingredients' "needs 2.3" is
// felt. Hidden unless the server listed more than one installed generation.
function renderLtxGenSwitch() {
  const box = document.getElementById('ltxGenSwitch');
  if (!box) return;
  const ltxEng = (globalThis.ENGINES || []).find(e => e.id === 'ltx');
  const gens = (ltxEng && ltxEng.generations) || [];
  box.hidden = gens.length < 2;
  box.innerHTML = gens.length > 1 ? _ltxGenSwitchHtml(gens, false) : '';
  if (gens.length > 1) _wireLtxGenSwitch(box);
}
// Not called at script load: escapeHtml and the rest of the page's helpers are
// defined by scripts that load AFTER this one. renderEngineSwitch() runs once
// everything is up and calls this.

// Ask the server to pin a different LTX generation, then wait for the restarted
// panel and reload so the page, the engine chip and every gated control (the
// Ingredients chip among them) are rebuilt for the new generation.
async function switchLtxGeneration(id) {
  // phosToast (queue.js), NOT a bare `toast`: there is no global `toast` - the
  // one in this file is a const local to h3SpeedClick. The bare call threw a
  // ReferenceError on every path: a refusal (render running, jobs waiting) was
  // swallowed by the catch below and treated as a successful exec, and the
  // success path died before it polled, so the panel restarted under a page
  // that never reloaded. lint_webapp flags it (no-undef).
  const say = (m, kind) => { if (typeof phosToast === 'function') phosToast(m, { kind }); };
  closeEngineMenu({ returnFocus: false });
  let r = null;
  try {
    r = await fetch('/ltx/generation', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: 'version=' + encodeURIComponent(id),
    });
  } catch (e) {
    // Only a NETWORK failure lands here: the process may have exec'd before
    // the response reached us - a successful switch, so poll like
    // panelRestart() does. A refusal is an HTTP answer and is handled below.
  }
  if (r) {
    const d = await r.json().catch(() => ({}));
    if (!d.ok) {
      say(d.error || 'Could not switch the LTX generation.', r.status === 409 ? 'warning' : 'danger');
      return false;
    }
    if (d.unchanged) return true;
  }
  say('Switching LTX generation - restarting the panel\u2026', 'success');
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    await new Promise(res => setTimeout(res, 700));
    try {
      const v = await fetch('/version', {cache: 'no-store'});
      if (v.ok) { location.reload(); return true; }
    } catch (e) { /* still down */ }
  }
  say(distText('Panel did not come back within 60s - start it from Pinokio.',
               'The studio did not come back within 60s - use Studio \u2192 Restart Engine.'), 'danger');
  return false;
}

// One sentence per state, and it has to be the RIGHT one — telling a user with
// 75 GB of weights on disk that the engine "isn't installed" is the v3.4.0
// regression report, verbatim.
function _engineTooltip(e, st, modeOk) {
  // The generation belongs in the TITLE too: the compact breakpoint
  // (<=1500 px — a 14" MBP window) hides .eng-seg-name and .eng-seg-gen, so on
  // a laptop the release's headline fix had no surface at all and #modelTag was
  // the only thing naming the build.
  const name = e.label + (e.generation ? ' ' + e.generation : '')
             + ' · ' + (e.sublabel || '');
  if (st.announced) return name + ' — ' + (e.tagline || 'not released yet');
  if (!e.builtin && !st.capable) {
    // The floor is the LOWEST lane's, served by the panel. This read
    // `st.min_ram_gb || 64` — the bf16 number, or a literal 64 that no floor
    // in this codebase has ever been — then `|| 46`, a number no floor in
    // this codebase has ever been either (H3-28). The real bar is
    // H3_MIN_RAM_GB_Q8 = 36 (mlx_ltx_panel.py); both server fields should
    // always be present, so this is a last-resort fallback only.
    return name + ' — needs ' + (st.ram_floor_gb || st.min_ram_gb || 36)
                + ' GB unified memory';
  }
  if (!e.builtin && !st.available && st.preparing) {
    return name + ' is getting ready on this Mac. It turns on by itself when it is ready.';
  }
  if (!e.builtin && !st.available) {
    // H3-05: "repairable" also covers missing_q8_dit (every weight present,
    // only the local compact build is missing) — a genuinely different fix
    // than "repair", with its own Pinokio menu entry (Build, not Repair).
    return (st.repairable && st.reason === 'missing_q8_dit')
      ? name + ' — needs its compact engine built; your weights are already on disk. Click for the one-click fix.'
      : st.repairable
      ? name + ' — needs repair; your weights are still on disk. Click for the one-click fix.'
      : name + ' — available to install (' + (e.install_size || '')
        + '). Click to see what it does.';
  }
  if (!modeOk) {
    return name + ' — renders ' + (e.serves_label || 'other modes')
         + " only, and this mode isn't one of them.";
  }
  return name + ' — ' + (e.tagline || '');
}

// One click, several jobs, and never two at once: select the engine when it is
// ready, open its install/repair card when it isn't, and do nothing at all
// when it is inert. A click that starts a download must not also arm a render
// that has nothing to render with.
function engineSegClick(id) {
  const e = engineById(id);
  if (!e) return;
  const st = engineStatus(e);
  if (st.announced) return;
  // Mac app, runtime being set up by the app: nothing to open, nothing to click.
  if (!e.builtin && st.capable && !st.available && st.preparing) return;
  if (!e.builtin && st.capable && !st.available) {
    const fn = e.install_card && window[e.install_card];
    // 'chip' tells the card this click came from the switcher, where a repeat
    // click means "yes, I know" rather than "explain it again". The explicit
    // buttons elsewhere pass nothing and always get the full card.
    if (typeof fn === 'function') fn('chip');
    return;
  }
  if (!e.builtin && !st.capable) return;
  if (!engineServesMode(e, currentMode)) return;
  setEngine(id);
}

// ---- The two strips ---------------------------------------------------------
// Each chip prints the eta of the cell it WOULD select — the Quality chips at
// the current length, the Length chips at the current quality — so the estimate
// is live on both axes at once and changing either one re-prices the other. The
// numbers are the server's: a chip looks up a cell, it never computes a canvas
// or a duration of its own.
// Per-engine differences, and ONLY the differences. Everything else about a
// tier chip — the classes, the three spans, the unavailable state, the title
// rules — is shared, because it was already right for H3 and re-deriving it for
// LTX is how two strips start disagreeing about what "unavailable" looks like.
//
// The engine argument is DEFAULTED to 'h3' throughout this section on purpose:
// every existing H3 call site then stays byte-identical, which is the property
// the refactor is checked against (560 chips, diffed before and after).
const TIER_ENGINES = {
  h3: {
    boot: () => H3,
    // H3's canvases are several different aspect ratios, so the ratio is the
    // fact worth printing beside the canvas.
    qualitySpec: (item, cell) => `${item.canvas} · ${item.aspect}`,
    lengthSpec: (item, cell) => (cell
      ? `${cell.frames}f` + (cell.chain_windows > 1 ? ` · ${cell.chain_windows}×5s` : '')
      : `${item.frames}f`),
    eta: (cell) => h3CellEta(cell),
    cellFor: (q, l) => h3CellFor(q, l),
    currentQuality: () => h3CurrentQuality(),
    currentLength: () => h3CurrentLength(),
    setQuality: (k) => setH3Quality(k),
    setLength: (k) => setH3Length(k),
    qStripId: 'h3QualityGroup', lStripId: 'h3LengthGroup',
    metaId: 'h3LengthMeta', noteId: '',
    qAttr: 'h3-quality', lAttr: 'h3-length',
  },
  ltx: {
    boot: () => (BOOT.ltx || {}),
    // LTX is 16:9 or 4:3 and the user picked the canvas by name; what changes
    // under them as they move the Length axis is the FRAME COUNT, so that is
    // what the quality chip prints. It is the current cell's frames, never a
    // fixed number — a chip that said "73f" while rendering 121 would be the
    // same class of lie as the engine label this release is fixing.
    // VC-28: show the size that actually lands in Outputs, not just the
    // render canvas — Balanced's default fit_720p export means a 1024×576
    // render delivers a 1280×720 file, the same gap VC-19 closed for the
    // info modal. item.delivered_canvas comes from the server
    // (ltx_tiers_payload -> compute_upscale_plan); equals item.canvas
    // whenever nothing is exported, so this is a no-op everywhere else.
    qualitySpec: (item, cell) => {
      const canvas = (item.delivered_canvas && item.delivered_canvas !== item.canvas)
        ? `${item.canvas} → ${item.delivered_canvas}` : item.canvas;
      return `${canvas} · ${cell ? cell.frames : '—'}f`;
    },
    lengthSpec: (item, cell) => (cell ? `${cell.frames}f` : `${item.frames}f`),
    eta: (cell) => ltxCellEta(cell),
    // A cell whose weights are not on disk is an INSTALL OFFER, not a choice.
    // `offered` is the RAM question and is resolved at import time; this is the
    // pack question and it can only be answered at runtime, so it lives here.
    needsInstall: (cell) => ltxCellNeedsInstall(cell),
    installLabel: (cell) => ltxCellInstallLabel(cell),
    cellFor: (q, l) => ltxCellFor(q, l),
    currentQuality: () => ltxCurrentQuality(),
    currentLength: () => ltxCurrentLength(),
    setQuality: (k) => setLtxQuality(k),
    setLength: (k) => setLtxLength(k),
    qStripId: 'qualityGroup', lStripId: 'ltxLengthGroup',
    metaId: 'ltxLengthMeta', noteId: 'ltxTierNote',
    // The CANVAS chips keep the SHIPPED `data-quality` attribute rather than
    // taking an engine-prefixed one. Five surfaces already read it — the click
    // handler, setQuality's active toggle, applyTierTimes, the trained-LoRA
    // compatibility gate and the character-strip swap — and renaming it to
    // match a naming scheme would have broken all five for tidiness. H3's
    // chips are new markup and have no such history, so they keep theirs.
    qAttr: 'quality', lAttr: 'ltx-length',
    // A duration that is not on the axis is not an error and is not hidden —
    // power users have shipped work with the raw Frames field. It lights no
    // chip and says what it is: `custom · 337f · 14 s`.
    customMeta: () => {
      const f = parseInt((document.getElementById('frames') || {}).value, 10);
      if (!Number.isFinite(f)) return '';
      const secs = Math.round(((f - 1) / 24) * 10) / 10;
      return `custom · ${f}f · ${secs} s`;
    },
  },
};

function _tierChipHtml(engine, kind, item, cell, active) {
  const E = TIER_ENGINES[engine] || TIER_ENGINES.h3;
  // While H3 Flex is the pick, a cell it cannot render is greyed like an
  // unavailable one, with the plain reason (h3CellTakesFlex is the server's).
  const flexLocked = engine === 'h3' && !!cell && cell.available !== false
    && typeof h3FlexPickedAny === 'function' && h3FlexPickedAny() && !h3CellTakesFlex(cell);
  const ok = !cell ? false : (cell.available !== false && !flexLocked);
  // NEEDS-INSTALL is a third state, distinct from unavailable: the cell is
  // renderable on this Mac, the weights just aren't here yet. It reads as a
  // CTA (dashed + download glyph via .needs-install) and its third slot names
  // the download instead of an ETA the user cannot have yet.
  const needsInstall = ok && E.needsInstall ? !!E.needsInstall(cell) : false;
  const cls = 'q-chip pill-btn pill-quality'
    + (active ? ' active' : '') + (ok ? '' : ' unavailable')
    + (flexLocked ? ' flex-locked' : '')
    + (needsInstall ? ' needs-install' : '');
  const spec = (kind === 'quality') ? E.qualitySpec(item, cell)
                                    : E.lengthSpec(item, cell);
  const foot = flexLocked ? 'H3 only'
             : !ok ? 'unavailable'
             : needsInstall ? E.installLabel(cell)
             : E.eta(cell);
  // Owner ruling (2026-09-29): the tooltip says WHERE the time on the chip
  // comes from — "based on 523 renders on Macs like yours" vs "estimated —
  // few renders on this Mac class yet" — right where a user is already
  // looking to decide whether to trust it.
  const basisFn = kind === 'quality' && !needsInstall
    ? (engine === 'h3' ? h3CellEtaBasis : ltxCellEtaBasis) : null;
  const basis = (ok && basisFn) ? basisFn(cell) : '';
  const title = flexLocked ? H3_FLEX_CELL_TIP
    : ok
    ? basis
    : (cell && cell.unavailable_reason) || 'Not available on this install.';
  const attr = (kind === 'quality') ? (E.qAttr || `${engine}-quality`)
                                    : (E.lAttr || `${engine}-length`);
  return `
    <button type="button" class="${cls}" data-${attr}="${escapeHtml(item.key)}"
            ${ok ? '' : 'aria-disabled="true"'} title="${escapeHtml(title)}">
      <span class="ql-name">${escapeHtml(item.label)}</span>
      <span class="q-spec ql-spec sub">${escapeHtml(spec)}</span>
      <span class="ql-tier">${escapeHtml(foot)}</span>
    </button>`;
}
// The shipped name, kept as a one-line shim so every H3 call site is unchanged.
function _h3ChipHtml(kind, item, cell, active) {
  return _tierChipHtml('h3', kind, item, cell, active);
}

function renderTierAxes(engine) {
  engine = engine || 'h3';
  const E = TIER_ENGINES[engine] || TIER_ENGINES.h3;
  const B = E.boot() || {};
  const qStrip = document.getElementById(E.qStripId);
  const lStrip = document.getElementById(E.lStripId);
  if (!qStrip || !lStrip) return;
  const qualities = B.qualities || [];
  const lengths = B.lengths || [];
  const curQ = E.currentQuality();
  const curL = E.currentLength();

  // Both strips used to be fixed column counts, which squeezed and then
  // CLIPPED: LTX ships five canvases (Quick · Balanced · Standard · High ·
  // High 720p) whose specs wrapped to three lines in the pane's usual width,
  // and H3's four ran off the right edge as soon as the window was dragged in.
  // Auto-fit tracks answer to the strip's own width. The 90 px floor is
  // measured, not guessed: the pane's content box is ~386 px, where four
  // tracks + gaps fit and five do not — so H3's four canvases stay one row
  // across (unchanged) and LTX's five wrap 4 + 1 instead of squeezing five
  // into a row. Squeeze the pane and it gives ground a column at a time
  // instead of clipping. An unfilled track collapses, so a 2-cell strip still
  // stretches its two cells across the row.
  qStrip.style.gridTemplateColumns = 'repeat(auto-fit, minmax(90px, 1fr))';
  qStrip.innerHTML = qualities.map(q =>
    _tierChipHtml(engine, 'quality', q, E.cellFor(q.key, curL), q.key === curQ)).join('');
  // Past four lengths (the lab dense pass turns a fifth on) the chips would
  // squeeze; wrap to three per row and let the strip grow a line instead. The
  // wider floor is what makes it three rather than four at the pane's usual
  // width — same result the hard-coded 3 gave, and it keeps giving ground as
  // the pane narrows instead of clipping.
  lStrip.style.gridTemplateColumns = lengths.length > 4
    ? 'repeat(auto-fit, minmax(118px, 1fr))'
    : 'repeat(auto-fit, minmax(90px, 1fr))';
  lStrip.innerHTML = lengths.map(l =>
    _tierChipHtml(engine, 'length', l, E.cellFor(curQ, l.key), l.key === curL)).join('');

  const qAttr = E.qAttr || `${engine}-quality`;
  const lAttr = E.lAttr || `${engine}-length`;
  qStrip.querySelectorAll(`[data-${qAttr}]`).forEach(b => {
    b.onclick = () => {
      // A needs-install chip is a DOWNLOAD BUTTON, not a tier choice. Binding
      // setQuality directly here bypassed the shipped .needs-install routing:
      // clicking High selected a tier the machine could not render, the
      // pre-render block explained why for ~1.5 s, and the next poll silently
      // moved the form to a bigger, slower canvas nobody asked for.
      if (b.classList.contains('needs-install')) {
        if (typeof openModelsModal === 'function') openModelsModal();
        return;
      }
      E.setQuality(b.getAttribute(`data-${qAttr}`));
    };
  });
  lStrip.querySelectorAll(`[data-${lAttr}]`).forEach(b => {
    b.onclick = () => E.setLength(b.getAttribute(`data-${lAttr}`));
  });
  // The right-hand meta on the Length label: the combination, in one line.
  const meta = E.metaId ? document.getElementById(E.metaId) : null;
  const cell = E.cellFor(curQ, curL);
  if (meta) {
    // A synthesised custom cell (length '') gets the custom line, which is the
    // one that can state the DURATION a raw frame count works out to — the
    // number the user actually wanted to know when they typed it.
    const custom = !cell || cell.length === '';
    meta.textContent = custom
      ? (E.customMeta ? E.customMeta() : '')
      : `${cell.width}×${cell.height} · ${cell.frames}f · ${E.eta(cell)}`;
  }
  // The selected cell's honest notes, joined. H3 has no note element and
  // passes noteId '' — the lookup is skipped rather than guarded downstream.
  const note = E.noteId ? document.getElementById(E.noteId) : null;
  if (note) {
    const txt = (cell && cell.note) || '';
    note.textContent = txt;
    note.hidden = !txt;
  }
  // Every LTX repaint settles the Speed row too — one hook covers boot,
  // shape changes, Load Params and engine switches, and the function
  // deliberately clears the preset without re-entering this repaint.
  if (engine === 'ltx' && typeof _applySchedPresetRowVisibility === 'function') {
    try { _applySchedPresetRowVisibility(); } catch (e) {}
  }
}
// The shipped name, kept so every H3 call site is unchanged.
// H3-12: the Landscape/Portrait sub-labels ("Landscape 640x384") were only
// ever refreshed from setH3Orientation itself — every OTHER thing that
// changes the resolved cell's canvas (setH3Quality, setH3Length, setH3Tier,
// Load Params, a Fallback redirect) left them stale, showing a size that
// belonged to the PREVIOUS quality/length pick. renderH3Axes() is the one
// function every one of those already calls to repaint the strips, so
// syncing the subs here — instead of chasing each call site individually —
// fixes the whole class at once.
function renderH3Axes() {
  const result = renderTierAxes('h3');
  if (typeof _h3SyncOrientationSubs === 'function') { try { _h3SyncOrientationSubs(); } catch (e) {} }
  return result;
}

// ---- LTX's side of the shared axis machinery --------------------------------
// The mirror of h3CellFor / h3CurrentQuality / h3CellEta, and nothing more. The
// LTX form already owns #quality (the shipped hidden select every mode reads),
// so the CANVAS axis writes the field that already exists rather than a second
// one — a new hidden input here would be a second definition of "what will
// render", which is the bug class this release keeps closing.
function ltxCellFor(quality, length) {
  const cell = ((BOOT.ltx || {}).tiers || [])
    .find(t => t.quality === quality && t.length === length) || null;
  if (cell || length !== '') return cell;
  // CUSTOM DURATION. The canvas is still perfectly available — only the length
  // is off the axis — so the quality chips must not go grey and claim
  // otherwise. Synthesise a cell that carries the real canvas and the real
  // frame count, and leave the eta as the word `custom` rather than inventing
  // a number: pricing an arbitrary shape in the browser would be the second
  // cost model this whole table exists to avoid.
  const q = ((BOOT.ltx || {}).qualities || []).find(x => x.key === quality);
  if (!q) return null;
  const f = parseInt((document.getElementById('frames') || {}).value, 10);
  if (!Number.isFinite(f)) return null;
  return {
    quality: q.key, quality_label: q.label, length: '', length_label: 'custom',
    width: q.width, height: q.height, frames: f,
    pack: q.pack, pipeline: q.pipeline,
    eta: 'custom', eta_measured: false, available: true, note: '',
    blurb: q.blurb || '',
  };
}
function ltxCurrentQuality() {
  const v = (document.getElementById('quality') || {}).value;
  return v || (BOOT.ltx || {}).default_quality || 'balanced';
}
// DERIVED FROM #frames, not from the hidden field, and that direction matters.
// #frames is what actually renders; the chip is a label on top of it. Reading
// the label would let the two disagree the moment somebody types 337 into
// Customize — the chip would still show 5s while a 14-second clip rendered,
// which is precisely the 7-second lie this release exists to fix, relocated.
// An off-axis frame count matches no rung, lights no chip, and prints itself.
function ltxCurrentLength() {
  const f = parseInt((document.getElementById('frames') || {}).value, 10);
  const lens = (BOOT.ltx || {}).lengths || [];
  const hit = lens.find(l => Number(l.frames) === f);
  if (hit) return hit.key;
  if (Number.isFinite(f)) return '';        // custom — no chip is active
  return (document.getElementById('ltx_length') || {}).value
      || (BOOT.ltx || {}).default_length || '5s';
}
function ltxCurrentCell() {
  return ltxCellFor(ltxCurrentQuality(), ltxCurrentLength());
}
// The eta STRING for a cell. The server's own string wins whenever the state is
// one the server priced, because that is where a MEASURED wall clock lives —
// and where it is NOT measured the string is still the server's model, so the
// browser never carries a second cost model. The only case it recomputes is a
// custom duration, which by definition has no cell.
// Pro active (data-pro set by proStatusRefresh): add the honest estimate the server computed from the
// fleet p75 x the worst measured Pro/Free ratio. Free: nothing.
function proEstSuffix(fr) {
  try {
    if (!document.documentElement.dataset.pro || !fr || !fr.pro_est) return '';
    return ' · Pro ' + fr.pro_est.label;
  } catch (e) { return ''; }
}
function ltxCellEta(cell) {
  if (!cell) return '';
  if (cell.eta === 'custom') return 'custom';
  // Chips must not advertise the tuned wall clock while the Fast draft
  // schedule is armed — the same lie the H3 Turbo repaint exists to kill.
  // fast_eta exists only on cells that can run the preset (server-stamped),
  // so HQ cells keep their own number untouched. Fast draft has no fleet
  // range of its own yet, so this branch keeps the point estimate.
  if (cell.fast_eta && typeof schedPresetActive === 'function'
      && schedPresetActive()) {
    return cell.fast_eta + ' · fast draft';
  }
  // VC-23: "Q8 HQ" names the internal quantization + pipeline, not
  // anything a user asked for — "High detail" says what it delivers.
  const tail = (cell.pack === 'q8' && cell.pipeline === 'hq') ? ' · High detail' : '';
  // Owner ruling (2026-09-29): show the fleet's own p25-p75 RANGE ("~12-18
  // min") wherever real fleet data exists, instead of a single point
  // number — cell.fleet_range is server-computed (fleet_calibrated_range,
  // mlx_ltx_panel.py) from real installs' own wall clocks, this Mac's
  // chip/RAM class first, falling back through chip-family and fleet-wide
  // levels. Without fleet data, VC-28's modelled band (cell.eta_range:
  // +/-10% on a measured cell, skewed upward on a modelled one) still reads
  // more honestly than a single point; the bare point estimate is last.
  const fr = cell.fleet_range;
  return (fr ? fr.eta_range : (cell.eta_range || cell.eta || '')) + tail + proEstSuffix(fr);
}
// The provenance line for a cell's tooltip — "based on 523 renders on Macs
// like yours" / "estimated -- few renders on this Mac class yet". Kept
// separate from ltxCellEta so a caller that only wants the number (the
// Batch total, a queue-row label) doesn't have to strip a sentence back off.
function ltxCellEtaBasis(cell) {
  if (!cell) return '';
  return cell.fleet_range ? cell.fleet_range.basis : 'estimated — few renders on this Mac class yet';
}
// Are this cell's weights on disk? A RUNTIME question — the tier table is built
// at import time and cannot know. `offered` answers "can this Mac's RAM serve
// this tier"; both must be able to be true (§11-E).
function ltxCellNeedsInstall(cell) {
  if (!cell || cell.pack !== 'q8') return false;
  const s = window.__phosLastStatus || {};
  // UNKNOWN IS NOT MISSING. Before the first /status lands there is no pack
  // answer at all, and `!undefined` is `true` — so the chip was born claiming
  // weights were absent on a machine that has them, and clicking it opened a
  // download for a 30 GB pack the user already owned. A tier is only an install
  // OFFER once we have actually been told something is missing; until then it
  // is an ordinary chip. Being briefly wrong in the harmless direction beats
  // being confidently wrong in the expensive one.
  if (s.q8_available === undefined && s.q8_pack_available === undefined) return false;
  const packOk = (s.q8_pack_available !== undefined) ? s.q8_pack_available : s.q8_available;
  if (cell.pipeline === 'hq') {
    // High needs the pack AND the add-on.
    return !(packOk && (s.hq_addon_missing || []).length === 0);
  }
  return !packOk;
}
// What the chip says instead of an ETA, and it names the download that is
// actually missing rather than a fixed string.
function ltxCellInstallLabel(cell) {
  const s = window.__phosLastStatus || {};
  const P = ((BOOT.ltx || {}).packs) || {};
  const packOk = (s.q8_pack_available !== undefined) ? s.q8_pack_available : s.q8_available;
  // NAME THE DOWNLOAD THAT IS MISSING, not the family it belongs to. With the
  // 30 GB pack present and only the 29.5 GB add-on absent, offering "Q8
  // weights" is the exact conflation `q8_pack_available` was introduced to
  // kill — telling a user to re-buy what they already have.
  const addonMissing = (s.hq_addon_missing || []).length > 0;
  // The pack comes first: without it the add-on has nowhere to land. Once it is
  // present, High's only remaining gap is the add-on.
  let want = P.q8;
  if (cell && cell.pipeline === 'hq' && packOk && addonMissing) want = P.hq_addon;
  if (!want) return 'install needed';
  const short = String(want.name || '').replace(/^LTX-2\.5\s*/, '');
  return `Install ${short} · ${want.size}`;
}
// Set the CANVAS. Length is untouched — the whole point of the two axes.
function setLtxQuality(key) {
  const q = ((BOOT.ltx || {}).qualities || []).find(x => x.key === key);
  if (!q) return;
  _ltxApplyShape(q.key, ltxCurrentLength());
}
// Set the DURATION. Canvas is untouched.
function setLtxLength(key) {
  const l = ((BOOT.ltx || {}).lengths || []).find(x => x.key === key);
  if (!l) return;
  _ltxApplyShape(ltxCurrentQuality(), l.key);
}
// The ONE place the form's LTX shape is written — the mirror of _h3ApplyShape.
// Every other caller (the strips, Load Params, a restored state) routes through
// here, so there is exactly one definition of what the form now says it will
// render, and #duration / #frames stay in agreement with the chips instead of
// drifting from them.
function _ltxApplyShape(qKey, lKey) {
  const cell = ltxCellFor(qKey, lKey);
  if (!cell) return;
  // A cell this canvas does not serve is REFUSED, not silently redirected: the
  // chip is already greyed with the reason in its title, so a click that
  // quietly rendered something else would be worse than a click that does
  // nothing.
  //
  // ...but "does nothing" was the whole complaint on the HQ chips. Now that
  // they are shown-disabled instead of hidden (owner ruling 2026-08-23), a
  // click has to SAY the reason rather than swallow it — a tooltip is not
  // discoverable on a chip a user has just tapped. Same #engineRowNote line
  // _h3ApplyShape writes into, and it deliberately does not set
  // `dataset.packNote`, so applyPackIncompleteGate never clears a note it
  // did not write and never has this one clobbered.
  if (cell.available === false) {
    const note = document.getElementById('engineRowNote');
    const reason = cell.unavailable_reason || '';
    if (note && reason && note.dataset.packNote !== '1') {
      note.textContent = reason;
      note.hidden = false;
    }
    renderTierAxes('ltx');
    return;
  }
  const setv = (id, v) => { const el = document.getElementById(id); if (el && v != null) el.value = v; };
  // The DURATION half is ours. The CANVAS half is setQuality()'s — it has
  // owned #quality, #width, #height, the aspect row and the upscale default
  // since long before this table existed, and a second writer here would be a
  // second definition of "what will render". So we hand it the key and let it
  // do its job; it calls back into renderTierAxes('ltx') when it is done.
  setv('ltx_length', cell.length);
  setv('duration', cell.seconds);
  setv('frames', cell.frames);
  if (typeof setQuality === 'function') setQuality(cell.quality);
  else renderTierAxes('ltx');
}

// Set the CANVAS. Length is untouched — that is the entire point of the
// refactor: "somebody may want to run the HQ 5s version for 10s".
function setH3Quality(key) {
  const q = h3QualityByKey(key);
  if (!q) return;
  _h3ApplyShape(q.key, h3CurrentLength());
}
// Set the DURATION. Canvas is untouched.
function setH3Length(key) {
  const l = h3LengthByKey(key);
  if (!l) return;
  _h3ApplyShape(h3CurrentQuality(), l.key);
}

// The nearest renderable cell to one this install can't serve. Mirrors
// h3_fallback_tier() in Python exactly — keep the CANVAS, give up length, never
// longer than was asked for, never the lab dense path — so a restored state and
// a queued job land on the same shape rather than disagreeing about it.
function _h3FallbackCell(cell) {
  const want = Number((h3LengthByKey(cell.length) || {}).seconds || 0);
  const lens = (H3.lengths || [])
    .filter(l => !l.dense && Number(l.seconds) <= want)
    .sort((a, b) => (b.seconds - a.seconds) || (b.order - a.order));
  for (const l of lens) {
    const c = h3CellFor(cell.quality, l.key);
    if (c && c.available !== false) return c;
  }
  return h3TierByKey(H3.default_tier);
}

// The one place the form's H3 state is written. Everything else — the strips,
// setH3Tier, Load Params, Draft→Finish — routes through here so there is exactly
// one definition of "what the form now says it will render".
//
// An unavailable cell is handled two different ways, and the difference matters:
//   a CLICK is refused (opts.fallback falsy). The chip is already greyed with
//     the reason, and quietly rendering something else is how a user ends up
//     with a 5 s clip they didn't ask for.
//   a RESTORE is redirected (opts.fallback true) — a persisted shape, a Load
//     Params replay, a Finish, a boot on a pack that has since lost chaining.
//     Refusing there would leave the form pointing at something unrenderable,
//     so it degrades along the same path make_job would take and says so.
// Either way make_job re-runs the gate server-side, because a stale tab must
// never win.
function _h3ApplyShape(qualityKey, lengthKey, opts) {
  opts = opts || {};
  let cell = h3CellFor(qualityKey, lengthKey);
  if (!cell) cell = h3CellFor(qualityKey, H3.default_length || '5s')
                || h3TierByKey(H3.default_tier);
  if (!cell) return;
  if (cell.available === false) {
    const note = document.getElementById('engineRowNote');
    const reason = cell.unavailable_reason || '';
    if (!opts.fallback) {
      if (note) { note.textContent = reason; note.hidden = !reason; }
      // Re-render so the click leaves the strips exactly as they were.
      renderH3Axes();
      return;
    }
    const fb = _h3FallbackCell(cell);
    if (!fb || fb.available === false) return;
    if (note) {
      note.textContent = reason + ' Falling back to ' + fb.label + '.';
      note.hidden = false;
    }
    cell = fb;
  }
  // A cell H3 Flex cannot render, clicked while Flex is the pick: refused like
  // an unavailable one (the chip is greyed with the reason). A restore lands
  // and h3FlexFit (queued from renderH3Turbo) moves it to the nearest cell.
  if (!opts.fallback && typeof h3FlexPickedAny === 'function' && h3FlexPickedAny() && !h3CellTakesFlex(cell)) {
    if (typeof phosToast === 'function') phosToast(H3_FLEX_CELL_TIP, { kind: 'info' });
    renderH3Axes();
    return;
  }
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.value = v; };
  set('h3_quality', cell.quality);
  set('h3_length', cell.length);
  set('h3_tier', cell.key);
  // Mirror the cell geometry into the shared hidden fields so the queue card,
  // the "Generate" estimate and Load Params all read the truth. make_job
  // re-stamps these server-side too — a stale tab must never win.
  set('width', cell.width);
  set('height', cell.height);
  set('frames', cell.frames);
  const s = document.getElementById('steps');
  if (s) {
    // Respect a pinned Steps override across shape switches; 'auto' follows
    // the cell's own count.
    const ov = (document.getElementById('h3_steps') || {}).value || 'auto';
    s.value = (ov !== 'auto' && /^\d+$/.test(ov)) ? parseInt(ov, 10) : cell.steps;
  }
  // Explanations belong in Docs; the controls keep only actionable status.
  const noteEl = document.getElementById('h3TierNote');
  if (noteEl) {
    noteEl.textContent = '';
    noteEl.hidden = true;
  }
  const blurbEl = document.getElementById('h3TierBlurb');
  if (blurbEl) {
    blurbEl.textContent = '';
    blurbEl.hidden = true;
  }
  // The export line is per (canvas × target): switching quality changes whether
  // this canvas exports clean or padded, so it has to be re-read here too.
  _h3SyncExportNote();
  // Per-window prompts exist only for a CHAINED length and there is one box per
  // window, so the control has to follow the Length axis live: 5s hides it, 10s
  // shows two boxes, 15s shows three (and the third is seeded, not blank).
  if (typeof renderH3WindowPrompts === 'function') {
    try { renderH3WindowPrompts(); } catch (e) {}
  }
  try {
    localStorage.setItem('phos_h3_quality', cell.quality);
    localStorage.setItem('phos_h3_length', cell.length);
    localStorage.setItem('phos_h3_tier', cell.key);
  } catch (e) {}
  renderH3Axes();
  // Turbo's pill carries THIS shape's estimate, so it has to be re-labelled
  // whenever either axis moves.
  if (typeof renderH3Turbo === 'function') { try { renderH3Turbo(); } catch (e) {} }
  if (typeof updateDerived === 'function') { try { updateDerived(); } catch (e) {} }
  if (typeof updateCustomizeSummary === 'function') { try { updateCustomizeSummary(); } catch (e) {} }
}

// ---- Per-window prompts -----------------------------------------------------
// A 10 s clip is TWO chained 5-second windows and a 15 s clip is three, and by
// default every window is handed the same prompt — which is why a one-off
// action reads as a loop even though the frames are genuinely unique. These
// five functions are the whole control; the runner has taken `--chain-prompts`
// since before v3.4.1 and nothing but the UI was missing.
//
// The default is untouched: with the toggle off the hidden field posts an empty
// string, make_job normalises that to [], and run_h3_job_inner takes exactly the
// argv it always did.

// How many windows the CURRENTLY SELECTED cell renders. Read off the server's
// cell, never computed here — the browser looks a cell up, it never invents one.
function h3ChainWindows() {
  const cell = h3CellFor(h3CurrentQuality(), h3CurrentLength());
  return Math.max(1, Number((cell && cell.chain_windows) || 1));
}

// Whether the INSTALLED runner can take a shot list at all. A SECOND probe, not
// a synonym for H3.chain: chaining and --chain-prompts landed on the runner at
// different times, so a pack exists that renders 10 s / 15 s and cannot be told
// what each window should do. That user keeps the honest warning on the cell
// (swapped in server-side) and never sees this control.
function h3ChainPromptsSupported() {
  return !!(H3 && H3.chain_prompts);
}

function _h3WinValues() {
  return Array.prototype.slice
    .call(document.querySelectorAll('#h3WinPromptsList .h3-win-ta'))
    .map(t => String(t.value || ''));
}

// The one place the hidden field is written. Anything that isn't a live, on,
// chained control posts '' — a stale list left over from a 15 s selection must
// never ride along on a 5 s render (make_job clamps it too, but the form should
// not be lying about what it is submitting).
function _h3SerializeWindowPrompts() {
  const inp = document.getElementById('h3_chain_prompts');
  if (!inp) return;
  const row = document.getElementById('h3WindowPromptsRow');
  const on = !!(document.getElementById('h3WinPromptsToggle') || {}).checked;
  if (!row || row.hidden || !on) { inp.value = ''; return; }
  const vals = _h3WinValues().slice(0, h3ChainWindows());
  // All-blank is the default by another name; post nothing rather than an array
  // of empty strings.
  inp.value = vals.some(v => v.trim()) ? JSON.stringify(vals) : '';
}

// Build (or rebuild) one labelled textarea per window. Existing text survives a
// rebuild — switching 10s → 15s keeps both beats and adds a third — and a box
// that did not exist before is seeded with the main prompt whenever the control
// is on, so the user is always editing a shot list rather than staring at a
// blank box wondering what belongs in it.
function renderH3WindowPrompts() {
  const row = document.getElementById('h3WindowPromptsRow');
  const list = document.getElementById('h3WinPromptsList');
  const toggle = document.getElementById('h3WinPromptsToggle');
  if (!row || !list || !toggle) return;
  if (!toggle.dataset.init) {
    toggle.dataset.init = '1';
    let saved = null;
    try { saved = localStorage.getItem('phos_h3_winprompts'); } catch (e) {}
    toggle.checked = (saved === '1');
    toggle.addEventListener('change', () => toggleH3WindowPrompts(toggle.checked));
  }
  const help = document.getElementById('h3WinHelpNote');
  if (help && H3 && H3.chain_prompt_help && !help.textContent) {
    help.textContent = H3.chain_prompt_help;
  }
  const windows = h3ChainWindows();
  const engineIsH3 = (((document.getElementById('engine') || {}).value) === 'h3');
  // Three conditions, and each hides it for a different honest reason: a
  // non-H3 engine has no windows, a 3 s / 5 s length is ONE window (the box
  // would change nothing), and a pack without --chain-prompts cannot use one.
  const show = engineIsH3 && windows > 1 && h3ChainPromptsSupported();
  row.hidden = !show;
  if (!show) { _h3SerializeWindowPrompts(); return; }
  const hint = document.getElementById('h3WinPromptsHint');
  if (hint) hint.textContent = windows + ' × 5s windows';
  const prev = _h3WinValues();
  const main = String((document.getElementById('prompt') || {}).value || '');
  const parts = [];
  for (let i = 0; i < windows; i++) {
    const from = i * 5;
    const v = (prev[i] != null) ? prev[i] : (toggle.checked ? main : '');
    parts.push(
      '<div class="h3-win">'
      + '<div class="h3-win-label"><span>Window ' + (i + 1) + '</span>'
      + '<span class="h3-win-span">' + from + '–' + (from + 5) + 's</span></div>'
      + '<textarea class="h3-win-ta" data-win="' + (i + 1) + '" rows="2"'
      + ' placeholder="Beat ' + (i + 1) + ' — leave empty to use the main prompt">'
      + escapeHtml(v) + '</textarea></div>');
  }
  list.innerHTML = parts.join('');
  list.hidden = !toggle.checked;
  list.querySelectorAll('.h3-win-ta').forEach(t => {
    t.addEventListener('input', _h3SerializeWindowPrompts);
  });
  _h3SerializeWindowPrompts();
}

// The toggle. Turning it ON seeds every EMPTY box with the main prompt: the
// user should be editing what they already wrote, not retyping it three times.
function toggleH3WindowPrompts(on) {
  const toggle = document.getElementById('h3WinPromptsToggle');
  const list = document.getElementById('h3WinPromptsList');
  if (!toggle) return;
  toggle.checked = !!on;
  if (toggle.checked) {
    const main = String((document.getElementById('prompt') || {}).value || '');
    document.querySelectorAll('#h3WinPromptsList .h3-win-ta').forEach(t => {
      if (!String(t.value || '').trim()) t.value = main;
    });
  }
  if (list) list.hidden = !toggle.checked;
  try {
    localStorage.setItem('phos_h3_winprompts', toggle.checked ? '1' : '0');
  } catch (e) {}
  _h3SerializeWindowPrompts();
}

// The `?`. Reveals the sentence in place rather than in a title= tooltip, and
// says so to assistive tech via aria-expanded.
function toggleH3WindowHelp() {
  const btn = document.getElementById('h3WinHelpBtn');
  const note = document.getElementById('h3WinHelpNote');
  if (!btn || !note) return;
  if (!note.textContent && H3 && H3.chain_prompt_help) {
    note.textContent = H3.chain_prompt_help;
  }
  const open = !!note.hidden;
  note.hidden = !open;
  btn.setAttribute('aria-expanded', open ? 'true' : 'false');
}

// Restore path: a sidecar's raw `params.h3_chain_prompts` (blanks included) →
// the boxes. Used by Load Params and Draft→Finish. MUST run after the shape has
// been applied, because the number of boxes is a property of the length.
function setH3ChainPrompts(listValue) {
  const toggle = document.getElementById('h3WinPromptsToggle');
  const list = document.getElementById('h3WinPromptsList');
  if (!toggle) return;
  const arr = Array.isArray(listValue)
    ? listValue.map(x => String(x == null ? '' : x))
    : [];
  const on = arr.some(v => v.trim());
  toggle.checked = on;
  renderH3WindowPrompts();
  Array.prototype.slice
    .call(document.querySelectorAll('#h3WinPromptsList .h3-win-ta'))
    .forEach((t, i) => { t.value = (arr[i] != null) ? arr[i] : ''; });
  if (list) list.hidden = !on;
  _h3SerializeWindowPrompts();
}

// Kept as the single-key entry point, because a tier key is what every SIDECAR
// carries: Load Params, Draft→Finish, setQuality's H3 re-assertion and the boot
// path all hand this a key (possibly a legacy one) and expect the form to land
// on that shape. It decomposes into the two axes and delegates.
function setH3Tier(key) {
  const tier = h3TierByKey(key);
  if (!tier) return;
  // fallback:true — every caller of this is a RESTORE (boot, Load Params,
  // Finish, setQuality's H3 re-assertion), and a restore that lands on a shape
  // this install can't serve must degrade, not leave the form armed with it.
  _h3ApplyShape(tier.quality, tier.length, { fallback: true });
}

// ---- H3-07: the Video-tab prompt helper -------------------------------------
// H3 speaks only what sits inside a <d>[Language] …</d> tag, written into the
// three-field form (integrated_multimodal_description / overall_soundscape /
// non_diegetic_music) — docs/H3_ENGINE.md "Dialogue on H3". None of that was
// discoverable from the Video tab: a line written as prose quotes left H3's
// voice switched off with no error. These three functions are the whole
// control; the format itself is unchanged — this only helps someone write it.

const H3_PROMPT_FIELD_MARKER = 'integrated_multimodal_description:';

// Structure: wrap whatever is already typed as the first field's content
// (so nothing is thrown away) and append the other two, empty and labelled.
// A prompt that already looks structured is left alone — this is an insert,
// not a reformat.
function h3InsertPromptStructure() {
  const ta = document.getElementById('prompt');
  if (!ta) return;
  const cur = ta.value || '';
  if (cur.includes(H3_PROMPT_FIELD_MARKER)) {
    if (typeof phosToast === 'function') {
      phosToast('Already structured — the three fields are there.', { kind: 'ok' });
    }
    ta.focus();
    return;
  }
  const body = cur.trim()
    ? cur.trim()
    : '[Shot 1] Who is on screen, two or three anchors (build, clothes), '
      + 'where they are and the light. What happens, described plainly.';
  ta.value = `${H3_PROMPT_FIELD_MARKER} ${body}\n\n`
    + `overall_soundscape: ambience and sound effects in the scene.\n\n`
    + `non_diegetic_music: N/A`;
  ta.dispatchEvent(new Event('input', { bubbles: true }));
  ta.focus();
}

// + Line: insert one dialogue line at the cursor (or the end) — the tag, a
// speaker id, and the mouth-stop clause the model needs in the SAME
// sentence or it keeps the mouth moving on nothing (docs/H3_ENGINE.md).
// Selects the placeholder words inside the tag so typing replaces them.
function h3InsertDialogueLine() {
  const ta = document.getElementById('prompt');
  if (!ta) return;
  const lang = (document.getElementById('h3DialogueLang') || {}).value || 'English';
  const start = (typeof ta.selectionStart === 'number') ? ta.selectionStart : ta.value.length;
  const end = (typeof ta.selectionEnd === 'number') ? ta.selectionEnd : start;
  const before = ta.value.slice(0, start);
  const after = ta.value.slice(end);
  const needsLeadingSpace = before && !/[\s\n]$/.test(before);
  const speaker = 'NAME (S1) says: ';
  const line = `${needsLeadingSpace ? ' ' : ''}${speaker}<d>[${lang}] line here</d> `
    + `Exactly as the line ends, NAME's mouth settles closed.`;
  ta.value = before + line + after;
  // Select "line here" so typing the actual words is one keystroke away.
  // Numeric, always: `before && …` is '' at position 0, and '' + 1 + … used to
  // concatenate into a string offset that selected nothing (Codex H3-9).
  const selStart = before.length + (needsLeadingSpace ? 1 : 0) + speaker.length
    + `<d>[${lang}] `.length;
  const selEnd = selStart + 'line here'.length;
  ta.focus();
  try { ta.setSelectionRange(selStart, selEnd); } catch (e) {}
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}

// Every <d>[Lang] …</d> tag's word count against this shape's budget
// (H3.speech_words_per_sec — the SAME number storyboard's own validator
// uses, so the two can't disagree), and a warning when prose quotes appear
// with no tag at all — the exact silent-failure this control exists for.
function h3SyncPromptHelper() {
  const ta = document.getElementById('prompt');
  const countEl = document.getElementById('h3DialogueWordCount');
  const warnEl = document.getElementById('h3QuoteWarning');
  if (!ta) return;
  const text = ta.value || '';
  const tagRe = /<d>\s*(?:\[[^\]]*\]\s*)?([^<]*)<\/d>/gi;
  let m, words = 0, tagCount = 0;
  while ((m = tagRe.exec(text))) {
    tagCount++;
    words += (m[1].trim().match(/\S+/g) || []).length;
  }
  if (countEl) {
    if (tagCount) {
      const cell = (typeof h3CurrentCell === 'function') ? h3CurrentCell() : null;
      const windows = Math.max(1, (cell && cell.chain_windows) || 1);
      const perWindowSec = (cell && cell.seconds) ? (cell.seconds / windows) : 5;
      const rate = (H3 && H3.speech_words_per_sec) || 2.4;
      // ONE window's speaking time, however many tags share it: splitting the
      // same words into more tags used to raise the allowance (Codex H3-8).
      // The main prompt's dialogue is window 1's; later windows have boxes.
      const budget = Math.max(1, Math.round(perWindowSec * rate));
      countEl.hidden = false;
      countEl.textContent = `${words}/${budget} dialogue words`;
      countEl.classList.toggle('over-budget', words > budget);
    } else {
      countEl.hidden = true;
    }
  }
  if (warnEl) {
    // A "-marked quote whose position falls outside every <d>…</d> span.
    const tagSpans = [];
    tagRe.lastIndex = 0;
    while ((m = tagRe.exec(text))) tagSpans.push([m.index, m.index + m[0].length]);
    const quoteRe = /["“”]/g;
    let hasStrayQuote = false;
    let q;
    while ((q = quoteRe.exec(text))) {
      if (!tagSpans.some(([s, e]) => q.index >= s && q.index < e)) { hasStrayQuote = true; break; }
    }
    warnEl.hidden = !hasStrayQuote;
  }
}
document.getElementById('prompt') && document.getElementById('prompt')
  .addEventListener('input', () => {
    if (document.body.dataset.engine === 'h3') { try { h3SyncPromptHelper(); } catch (e) {} }
  });


// ---- 4.19: H3 media modes ----------------------------------------------------
// Keyframes, Extend, Lip-sync and Add sound each need a runner flag an older
// H3 pack does not have. /status.h3 carries one boolean per capability, probed
// on the INSTALLED runner; the server (make_job + the worker) makes the same
// decision from the same probes — this copy only lets the UI say so first.
globalThis.H3_MODE_CAP = { keyframe: 'keyframes', extend: 'extend', a2v: 'audio_drive', v2a: 'video_to_audio' };
globalThis.H3_MODE_LABEL = { keyframe: 'Keyframes', extend: 'Extend', a2v: 'Lip-sync', v2a: 'Add sound' };

function h3CanServe(mode) {
  const st = (window._ENGINE_PROBES && window._ENGINE_PROBES.h3) || {};
  if (!st.available) return false;
  const cap = H3_MODE_CAP[mode];
  return cap ? !!st[cap] : true;
}

function h3ClipIsH3(o) {
  return !!(o && o.engine === 'h3');
}

// The Extend panel's length control speaks per engine: LTX adds 8-frame
// latents up to 10 s; H3 adds 5 s windows and lands on the exact seconds,
// up to 15 s (three windows, each carrying the last 17 frames and their sound).
function syncH3MediaModeUi() {
  const onH3 = document.body.dataset.engine === 'h3';
  const sec = document.getElementById('extend_seconds');
  if (sec) {
    sec.max = onH3 ? '15' : '10';
    if (!onH3 && parseFloat(sec.value) > 10) sec.value = '10';
  }
  const hint = document.getElementById('extendDurationHint');
  if (hint && onH3 && sec) {
    const s = Math.max(0.5, Math.min(15, parseFloat(sec.value) || 5));
    const windows = 1 + Math.max(0, Math.ceil((s * 24 + 1 - 124) / 123));
    hint.textContent = `Adds ${s.toFixed(1)} s · ${windows} H3 window${windows > 1 ? 's' : ''}`;
  }
}


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
Object.assign(globalThis, {
  imageModelRows, imageModelForValue, syncImageVersions, pickImageModel,
  engineById, defaultEngine, engineStatus, engineServesMode,
  h3ResolveTierKey, h3TierByKey, h3CellFor, h3CurrentCell,
  setH3Upscale, setH3FaceFixAfter, setH3Orientation, setH3Steps, h3TurboPillSub,
  h3V13State, h3V13On,
  renderH3Turbo, setH3Turbo, syncModeStripToEngine, modeChipEngineClick, renderEngineSwitch, h3FlexSubmitBlock,
  setH3Speed, h3SpeedOfParams, h3FlexOn, h3FlexState, h3FlexTurboOn, h3FlexTurboState,
  h3FlexTurboPickerEntry, h3FlexTurboPickerStatus, h3FlexTurboPickerClick, h3EstimateLine, setH3TriStep, h3TriStepOn, h3TriStepState,
  setH3CleanSound, h3CleanSoundOn, h3CleanSoundPref,
  h3JobSpeedLabel, h3JobShapeLabel, ltxCellEtaBasis, h3CellEtaBasis,
  // Published for the Shot setup summary (characters.js): a closed section has
  // to price the shape it is hiding, and it must do it with the SAME numbers
  // the chips print — never a second cost model in a second module.
  h3CellEta,
  // ...and the two the Batch section needs to price N jobs: the MINUTES behind
  // that string, and whether a Face Fix rides along. Private, they threw on
  // first call and the catch swallowed it, so an H3 batch showed no total at
  // all (lint_webapp: no-undef, queue.js:4579).
  h3CellEtaMin, h3FaceFixOn,
  renderTierAxes, renderH3Axes, ltxCellFor, ltxCurrentQuality,
  ltxCurrentLength, ltxCurrentCell, ltxCellEta, ltxCellNeedsInstall,
  ltxCellInstallLabel, _ltxApplyShape, setH3Quality, _h3ApplyShape,
  renderH3WindowPrompts, toggleH3WindowHelp, setH3ChainPrompts, setH3Tier,
  h3InsertPromptStructure, h3InsertDialogueLine, h3SyncPromptHelper,
  h3CanServe, h3ClipIsH3, syncH3MediaModeUi, h3FmtEtaMin,
  // The Ingredients chip (characters.js) IS a generation switch when 2.3 is
  // installed. Module-private, its `typeof` guard read false on every click
  // and the chip fell through to the "needs LTX-2.3" toast while its own
  // tooltip said "click to switch".
  switchLtxGeneration,
  // What the form has picked, for capabilities.js (the generic greying).
  h3SpeedPref, h3CurrentQuality, h3CurrentLength, _modeChipMode,
});
