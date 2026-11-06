// webapp/js/capabilities.js — grey out what the picked model cannot do (5.0).
//
// Owner, 31 Oct 2026: "see what features we are offering and gray out the
// rest." The SERVER owns the facts: model_capabilities.py declares, per engine
// and per variant (H3 Best, Fast, Fast HD, Flex, Flex Turbo, LTX-2.5, ...),
// the modes, canvases, lengths, inputs, options and Pro status, plus the one
// plain sentence to show on anything it does not do. It arrives here as
// BOOT.capabilities (and GET /capabilities). make_job asks the same rows, so
// what this module greys is exactly what the server would not render.
//
// This module knows only the DOM side: which control is a mode, a canvas, a
// length or an option. It never holds a list of modes or a reason of its own;
// test_model_capabilities.py fails the build if one is typed in here. A new
// port declares its row in Python and its controls light up or grey out with
// no JS change.
//
// Greyed = `.cap-locked` + aria-disabled + the row's reason as the tooltip. A
// click on a greyed control does nothing except show that reason (one capture
// listener for the whole page, so no feature module has to learn about it).

const CAP_LOCK_CLASS = 'cap-locked';

// Options -> the controls that set them. DOM facts only (which element is the
// Steps row); whether a variant offers the option is the table's.
const CAP_OPTION_CONTROLS = {
  steps: ['#h3StepsGroup [data-h3-steps]'],
  speed: ['#h3SpeedGroup [data-h3-speed="fast"]', '#h3SpeedGroup [data-h3-speed="best"]'],
  clean_sound: ['#h3CleanSoundRow input', '#h3CleanSoundRow label'],
};
// Options whose control belongs to one engine (an LTX variant does not lock
// H3's Steps pills).
const CAP_OPTION_ENGINE = { steps: 'h3', speed: 'h3', clean_sound: 'h3' };

function capTable() {
  return (typeof BOOT !== 'undefined' && BOOT && BOOT.capabilities) || null;
}

function capVariant(id) {
  const t = capTable();
  return (t && t.variants && t.variants[id]) || null;
}

function _capVariants(engine) {
  const t = capTable();
  if (!t || !t.variants) return [];
  return Object.values(t.variants).filter(v => v.engine === engine);
}

// What the form has picked, in the vocabulary of the rows' `pick` field.
function capPickNow() {
  const engine = (typeof currentEngine === 'function') ? currentEngine() : 'ltx';
  const pick = {};
  if (engine === 'h3') {
    if (typeof h3SpeedPref === 'function') pick.speed = h3SpeedPref();
    if (typeof h3CurrentQuality === 'function') pick.quality = h3CurrentQuality();
  } else if (engine === 'ltx') {
    const gen = (BOOT.engines || []).find(e => e.id === 'ltx');
    const g = gen && gen.generation ? 'ltx' + String(gen.generation).replace('.', '') : '';
    if (g) pick.generation = g;
  }
  return { engine, pick };
}

// Same rule as model_capabilities.variant_for_pick: every row whose `pick`
// matches is a candidate, highest rank wins, else the engine's default.
function capVariantFor(engine, pick) {
  let best = null, score = null;
  for (const row of _capVariants(engine)) {
    const want = row.pick || {};
    const keys = Object.keys(want);
    if (!keys.length || !keys.every(k => String(pick[k]) === String(want[k]))) continue;
    const s = [Number(row.rank || 0), keys.length];
    if (!score || s[0] > score[0] || (s[0] === score[0] && s[1] > score[1])) { best = row; score = s; }
  }
  if (best) return best;
  const t = capTable();
  const dv = t && t.engines && t.engines[engine] && t.engines[engine].default_variant;
  return dv ? capVariant(engine + '.' + dv) : null;
}

function capVariantNow() {
  const { engine, pick } = capPickNow();
  return capVariantFor(engine, pick);
}

function capServesMode(row, mode) {
  if (!row || !Array.isArray(row.modes)) return true;
  if (mode === 'oneshot') mode = 't2v';
  return row.modes.indexOf(mode) !== -1;
}

function capCellAllowed(row, cell) {
  if (!row || !cell) return true;
  if (Array.isArray(row.qualities) && row.qualities.indexOf(cell.quality) === -1) return false;
  if (Array.isArray(row.lengths) && row.lengths.indexOf(cell.length) === -1) return false;
  const flags = (row.cells && row.cells.exclude_flags) || [];
  return !flags.some(f => !!cell[f]);
}

// The JS twin of model_capabilities.check(): [] when the row renders this,
// else one {axis, value, reason, fallback} per problem.
function capCheck(row, req) {
  req = req || {};
  const out = [];
  if (!row) return out;
  const bad = (axis, value) => out.push({ axis, value, reason: (row.reasons || {})[axis] || '', fallback: row.fallback || null });
  if (req.mode != null && !capServesMode(row, req.mode)) bad('modes', req.mode);
  if (req.cell && !capCellAllowed(row, req.cell)) {
    const c = req.cell;
    if (Array.isArray(row.qualities) && row.qualities.indexOf(c.quality) === -1) bad('qualities', c.quality);
    else if (Array.isArray(row.lengths) && row.lengths.indexOf(c.length) === -1) bad('lengths', c.length);
    else bad('cells', c.key || c.label);
  }
  (req.options || []).forEach(o => { if ((row.options || []).indexOf(o) === -1) bad('options', o); });
  (req.inputs || []).forEach(i => { if ((row.inputs || []).indexOf(i) === -1) bad('inputs', i); });
  const t = capTable();
  if (row.pro && t && t.pro_active === false) bad('pro', true);
  return out;
}

function capLock(el, reason) {
  if (!el) return;
  if (el.dataset.capTitle == null) el.dataset.capTitle = el.getAttribute('title') || '';
  el.classList.add(CAP_LOCK_CLASS);
  el.setAttribute('aria-disabled', 'true');
  el.dataset.capReason = reason || '';
  if (reason) el.setAttribute('title', reason);
}

function capUnlock(el) {
  if (!el || !el.classList.contains(CAP_LOCK_CLASS)) return;
  el.classList.remove(CAP_LOCK_CLASS);
  el.removeAttribute('aria-disabled');
  if (el.dataset.capTitle) el.setAttribute('title', el.dataset.capTitle);
  else el.removeAttribute('title');
  delete el.dataset.capTitle;
  delete el.dataset.capReason;
}

function _capSet(el, reason) {
  if (reason) capLock(el, reason); else capUnlock(el);
}

function _capChipMode(chip) {
  if (typeof _modeChipMode === 'function') return _modeChipMode(chip);
  return chip.dataset.lipsync ? 'a2v' : (chip.dataset.mode || '');
}

// Mode chips: a mode the ENGINE renders but the picked variant does not. (A
// mode the engine itself lacks is engines.js's foreign-mode business: that
// chip switches engine on click, it is not a dead end.)
function _capSyncModes(row) {
  const eng = (typeof engineById === 'function') ? engineById(row.engine) : null;
  const base = capVariantFor(row.engine, {});
  document.querySelectorAll('#modeGroup .mode-chip').forEach(chip => {
    const mode = _capChipMode(chip);
    if (!mode) return;
    const kids = mode === 'remix'
      ? ((typeof REMIX_MODES !== 'undefined' && REMIX_MODES) ? REMIX_MODES : [])
      : [mode];
    const engineHas = kids.some(m => (eng && typeof engineServesMode === 'function')
      ? engineServesMode(eng, m) : capServesMode(base, m));
    const rowHas = kids.some(m => capServesMode(row, m));
    _capSet(chip, engineHas && !rowHas ? row.reasons.modes : '');
  });
}

// Canvas and length chips (H3's two strips): greyed when the cell they would
// land on is one the picked variant does not render.
function _capSyncCells(row) {
  if (row.engine !== 'h3' || typeof h3CellFor !== 'function') return;
  const q = (typeof h3CurrentQuality === 'function') ? h3CurrentQuality() : null;
  const l = (typeof h3CurrentLength === 'function') ? h3CurrentLength() : null;
  const reasonFor = cell => {
    const p = capCheck(row, { cell });
    return p.length ? p[0].reason : '';
  };
  // A canvas chip can itself change the variant (Fast HD is a canvas), so it
  // is judged against the row a click on it would pick, not the live one:
  // otherwise Fast HD would lock every other canvas and trap the picker.
  const { pick } = capPickNow();
  document.querySelectorAll('#h3QualityGroup [data-h3-quality]').forEach(chip => {
    const next = capVariantFor(row.engine, Object.assign({}, pick, { quality: chip.dataset.h3Quality })) || row;
    const p = capCheck(next, { cell: h3CellFor(chip.dataset.h3Quality, l) });
    _capSet(chip, p.length ? p[0].reason : '');
  });
  document.querySelectorAll('#h3LengthGroup [data-h3-length]').forEach(chip => {
    _capSet(chip, reasonFor(h3CellFor(q, chip.dataset.h3Length)));
  });
}

function _capSyncOptions(row) {
  Object.keys(CAP_OPTION_CONTROLS).forEach(opt => {
    const mine = !CAP_OPTION_ENGINE[opt] || CAP_OPTION_ENGINE[opt] === row.engine;
    const off = mine && (row.options || []).indexOf(opt) === -1;
    CAP_OPTION_CONTROLS[opt].forEach(sel => document.querySelectorAll(sel).forEach(el => {
      _capSet(el, off ? row.reasons.options : '');
    }));
  });
}

// Re-grey the form for whatever is picked now. Cheap and idempotent: safe to
// call after any click or change.
let _capBusy = false;
function capSync() {
  if (_capBusy || !capTable()) return null;
  _capBusy = true;
  try {
    const row = capVariantNow();
    document.querySelectorAll('.' + CAP_LOCK_CLASS).forEach(el => {
      if (!row) capUnlock(el);
    });
    if (!row) return null;
    document.body.dataset.capVariant = row.id;
    _capSyncModes(row);
    _capSyncCells(row);
    _capSyncOptions(row);
    return row;
  } finally {
    _capBusy = false;
  }
}

let _capSyncQueued = false;
function capSyncSoon() {
  if (_capSyncQueued) return;
  _capSyncQueued = true;
  setTimeout(() => { _capSyncQueued = false; capSync(); }, 0);
}

// A click on a greyed control: nothing switches; the reason shows. Capture
// phase, so it runs before the control's own handler.
function _capClickGuard(ev) {
  const el = ev.target && ev.target.closest && ev.target.closest('.' + CAP_LOCK_CLASS);
  if (!el) return;
  // A lock can be stale: loading a clip's settings or any other programmatic
  // switch moves the form without a click. Re-judge against the form as it is
  // now before refusing anything.
  capSync();
  if (!el.classList.contains(CAP_LOCK_CLASS)) return;
  ev.stopImmediatePropagation();
  ev.preventDefault();
  const reason = el.dataset.capReason || '';
  if (reason && typeof phosToast === 'function') phosToast(reason, { kind: 'info' });
}

// Run once from main.js.
function capInstall() {
  document.addEventListener('click', _capClickGuard, true);
  // Whatever a click or a change moved (engine, speed, canvas, mode), re-grey
  // after the owning module has repainted.
  document.addEventListener('click', capSyncSoon, false);
  document.addEventListener('change', capSyncSoon, false);
  capSync();
}

Object.assign(globalThis, {
  capTable, capVariant, capVariantFor, capVariantNow, capServesMode,
  capCellAllowed, capCheck, capLock, capUnlock, capSync, capSyncSoon,
  capInstall,
});
