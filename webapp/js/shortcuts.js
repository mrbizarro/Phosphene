// THE KEYBOARD, IN ONE TABLE.
//
// Every shortcut Phosphene answers to is a row here: the keys, where it
// works, and what it does. Three consumers read it and nothing else:
//
//   * the Docs page "Keyboard shortcuts" (webapp/js/docs.js renders it);
//   * the Editor's Keys popover (sbeKeysLegend in editor.js);
//   * the tooltips — any element with data-shortcut="<id>" gets its keys
//     appended to its title, and code that builds a title at runtime calls
//     shortcutHint(id).
//
// Rows with `run` are HANDLED here, by the one listener at the bottom, so the
// row and the behaviour cannot disagree. Rows without `run` describe a
// handler that lives next to the thing it acts on (the Editor, the Outputs
// lightbox, the Storyboard grade keys, the Images canvas); `owner` names the
// file, and test_docs_and_shortcuts.py drives the Editor's real handler with
// every Editor row's keys and asserts it calls the function the row names.
//
// Conventions, so a person who cuts in Premiere / Final Cut / Resolve or
// works in Photoshop / After Effects already knows them: Space plays,
// ⌘Z / ⇧⌘Z undo and redo, ⌘S saves, S / ⌘K / ⌘B split at the playhead,
// ⌫ lifts and ⇧⌫ ripple-deletes, ↑ ↓ jump between cuts, N toggles snapping,
// ⇧Z fits the timeline, ⌘⏎ submits the prompt you are typing in, ⌘⌫ moves
// to the Trash like Finder, Esc closes what is on top, ⇧1–⇧7 switch pages the
// way Resolve's ⇧ + number does. Nothing here claims a combination the
// browser or macOS reserves (SHORTCUT_RESERVED) — ⌘R is reload, never render.

// Where a shortcut works. The order is the order the Docs page shows them.
const SHORTCUT_SCOPES = [
  { id: 'global', title: 'Anywhere',
    note: 'Not while you are typing in a text box — except ⌘⏎ and Esc.' },
  { id: 'prompt', title: 'While writing a prompt',
    note: 'These work inside the text box.' },
  { id: 'outputs', title: 'Outputs and the player',
    note: 'On the Video, One Shot, Images and Audio tabs, with an output selected.' },
  { id: 'editor', title: 'Editor — keys',
    note: 'With a timeline open on the Editor tab. The Keys button above the tracks shows the same list.' },
  { id: 'editor-mouse', title: 'Editor — mouse and modifiers',
    note: 'Hold the modifier while you click or drag.' },
  { id: 'storyboard', title: 'Storyboard', note: 'Click a shot card first to focus it.' },
  { id: 'canvas', title: 'Images — Layout canvas',
    note: 'Ideogram 4 in Layout mode, with a box selected.' },
];

const SHORTCUT_TABS = ['manual', 'oneshot', 'studio', 'storyboard', 'editor', 'audio', 'train'];

const SHORTCUTS = [
  // ---- anywhere -------------------------------------------------------------
  // `?` is what a US keyboard reports; ⇧/ is the same physical chord as some
  // layouts and synthesized events report it.
  { id: 'docs.open', scope: 'global', combos: ['?'], hidden: ['shift+slash'], owner: 'shortcuts.js',
    label: 'Open the Docs on this page',
    run: () => { if (typeof openDocs !== 'function') return false; openDocs('shortcuts'); } },
  { id: 'tabs.switch', scope: 'global', owner: 'shortcuts.js',
    combos: ['shift+digit1', 'shift+digit2', 'shift+digit3', 'shift+digit4',
             'shift+digit5', 'shift+digit6', 'shift+digit7'],
    display: [['⇧', '1'], ['⇧', '7']], displayJoin: '–',
    label: 'Switch tab: 1 Video · 2 One Shot · 3 Images · 4 Storyboard · 5 Editor · 6 Audio · 7 Train Character',
    run: (ev) => {
      const n = Number(String(ev.code || '').replace('Digit', ''));
      const tab = SHORTCUT_TABS[n - 1];
      if (!tab || typeof workflowSwitch !== 'function') return false;
      workflowSwitch(tab);
    } },
  { id: 'search.focus', scope: 'global', combos: ['mod+f'], owner: 'shortcuts.js',
    label: 'Jump to the search box on screen — Outputs, the Editor’s media pool, the LoRA browser or the Docs. Where there is none, the browser’s own Find opens.',
    run: () => shortcutFocusSearch() },
  { id: 'escape.close', scope: 'global', combos: ['escape'], owner: 'health.js, engines.js',
    label: 'Close what is on top — a dialog, a menu, the Docs, the expanded player' },

  // ---- typing a prompt --------------------------------------------------------
  { id: 'prompt.generate', scope: 'prompt', combos: ['mod+enter'], typing: true,
    owner: 'shortcuts.js (Video, Images) · oneshot.js (One Shot)',
    label: 'Generate — from the Video prompt, the Images prompt or the One Shot prompt. Same as pressing the Generate button, so it queues a render.',
    run: (ev) => shortcutGenerateFrom(ev.target) },
  { id: 'oneshot.beats', scope: 'prompt', combos: ['enter', 'backspace'], owner: 'oneshot.js',
    label: 'In One Shot beats: ⏎ goes to the next beat · ⌫ in an empty beat goes back one' },

  // ---- outputs and the player -------------------------------------------------
  { id: 'player.toggle', scope: 'outputs', combos: ['space'], owner: 'shortcuts.js',
    label: 'Play / pause the selected output',
    run: (ev) => shortcutTogglePlayer(ev) },
  { id: 'outputs.step', scope: 'outputs', combos: ['arrowleft', 'arrowright'], owner: 'queue.js',
    label: 'Previous / next output — wraps at the ends, works in the expanded player too' },
  { id: 'outputs.expand', scope: 'outputs', combos: ['f'], owner: 'queue.js',
    label: 'Expand the selected output to full screen (Esc closes it)' },
  { id: 'outputs.trash', scope: 'outputs', combos: ['mod+backspace'], owner: 'shortcuts.js',
    label: 'Move the selected output to the Trash — asks first, like the trash button on the card',
    run: () => shortcutTrashOutput() },

  // ---- the Editor: keys ---------------------------------------------------------
  // `calls` is the function the Editor's own keydown handler runs for these
  // keys; the test drives the real handler and holds it to this.
  { id: 'editor.play', scope: 'editor', combos: ['space'], owner: 'editor.js',
    calls: 'sbeTogglePlay', label: 'Play / pause' },
  { id: 'editor.frame', scope: 'editor', combos: ['arrowleft', 'arrowright'], owner: 'editor.js',
    calls: 'sbeSeek', label: 'Move the playhead one frame' },
  { id: 'editor.frame10', scope: 'editor', combos: ['shift+arrowleft', 'shift+arrowright'],
    owner: 'editor.js', calls: 'sbeSeek', label: 'Move the playhead ten frames' },
  { id: 'editor.cut', scope: 'editor', combos: ['arrowup', 'arrowdown'], owner: 'editor.js',
    calls: 'sbeJumpCut', label: 'Jump to the previous / next cut' },
  { id: 'editor.ends', scope: 'editor', combos: ['home', 'end'], owner: 'editor.js',
    calls: 'sbeSeek', label: 'Go to the start / the end of the sequence (fn ← / fn → on a laptop)' },
  { id: 'editor.nudge', scope: 'editor', combos: ['alt+arrowleft', 'alt+arrowright'],
    hidden: ['alt+shift+arrowleft', 'alt+shift+arrowright'], owner: 'editor.js',
    calls: 'sbeNudge', label: 'Nudge the selected clips one frame earlier / later · add ⇧ for ten' },
  { id: 'editor.split', scope: 'editor', combos: ['s', 'mod+k', 'mod+b'], owner: 'editor.js',
    calls: 'sbeSplitHere', label: 'Split the shot under the playhead — or, with sounds on an audio track selected, those sounds (S, or ⌘K as in Premiere, ⌘B as in Final Cut and Resolve)' },
  { id: 'editor.lift', scope: 'editor', combos: ['backspace'], hidden: ['delete'], owner: 'editor.js',
    calls: 'sbeLiftSelected', label: 'Lift — take the selected clips or sounds out and leave the hole' },
  { id: 'editor.ripple', scope: 'editor', combos: ['shift+backspace'], hidden: ['shift+delete'],
    owner: 'editor.js', calls: 'sbeRippleSelected',
    label: 'Ripple delete — take them out and close the gap (for sounds, on their own track)' },
  { id: 'editor.removeOverlay', scope: 'editor', combos: ['backspace'], hidden: ['delete'],
    owner: 'editor.js', calls: 'sbeOvDeleteSel', label: 'With a title or card selected: remove it' },
  { id: 'editor.removeTransition', scope: 'editor', combos: ['backspace'], hidden: ['delete'],
    owner: 'editor.js', calls: 'sbeTxRemoveSel', label: 'With a cut selected: remove its transition' },
  { id: 'editor.duplicate', scope: 'editor', combos: ['d'], owner: 'editor.js',
    calls: 'sbeDuplicateSel', label: 'Duplicate the selected clips or sounds — the music and a clip\'s sound are copied onto an audio track' },
  { id: 'editor.link', scope: 'editor', combos: ['shift+l'], owner: 'editor.js',
    calls: 'sbeToggleAudioLink', label: 'Unlink / link the sound' },
  { id: 'editor.resync', scope: 'editor', combos: ['shift+r'], owner: 'editor.js',
    calls: 'sbeResyncSel', label: 'Resync — slide the sound back under its own picture' },
  { id: 'editor.selectAll', scope: 'editor', combos: ['mod+a'], owner: 'editor.js',
    calls: 'sbeSelectAll', label: 'Select every clip' },
  // Escape never closes the timeline: no editor closes a project on Escape,
  // and the Escape that closed the Docs used to shut the film behind them.
  // Closing is the ⋯ menu's Close.
  { id: 'editor.deselect', scope: 'editor', combos: ['escape'], owner: 'editor.js',
    calls: 'sbeSelectNone', label: 'Close an open menu, then clear the selection (never closes the timeline)' },
  { id: 'editor.zoom', scope: 'editor', combos: ['plus', 'minus'], hidden: ['equals', 'underscore'],
    owner: 'editor.js', calls: 'sbeZoom', label: 'Zoom the timeline in / out' },
  { id: 'editor.fit', scope: 'editor', combos: ['shift+z'], hidden: ['backslash'], owner: 'editor.js',
    calls: 'sbeZoomFit', label: 'Fit the whole sequence in the window' },
  { id: 'editor.snap', scope: 'editor', combos: ['n'], owner: 'editor.js',
    calls: 'sbeToggleSnap', label: 'Snap to beat on / off' },
  { id: 'editor.mute', scope: 'editor', combos: ['m'], owner: 'editor.js',
    calls: ['sbeSetMute', 'sbeUnmuteFromRefusal'],
    label: 'Mute / unmute the preview — the film itself is not changed' },
  // The sound area's size. It manages itself; this is the key for when you
  // want one answer to stick — the ▾ on the A1 head is the same control.
  { id: 'editor.soundLanes', scope: 'editor', combos: ['shift+a'], owner: 'editor.js',
    calls: 'sbeAudioPinToggle',
    label: 'Sound lanes small / full height — A1, A2 and the audio tracks (the ▾ on the A1 head does the same)' },
  { id: 'editor.undo', scope: 'editor', combos: ['mod+z'], owner: 'editor.js',
    calls: 'sbeUndo', label: 'Undo' },
  { id: 'editor.redo', scope: 'editor', combos: ['shift+mod+z'], owner: 'editor.js',
    calls: 'sbeRedo', label: 'Redo' },
  { id: 'editor.save', scope: 'editor', combos: ['mod+s'], owner: 'editor.js',
    calls: 'sbeSaveNow', label: 'Save the draft' },
  { id: 'editor.render', scope: 'editor', combos: ['mod+e'], owner: 'editor.js',
    calls: 'sbeRenderFilm', label: 'Render the timeline into one file (⌘E, as Export in Final Cut)' },

  // ---- the Editor: mouse -----------------------------------------------------------
  { id: 'editor.click', scope: 'editor-mouse', display: [['Click']], owner: 'editor.js',
    label: 'Select a clip · click an empty part of the track to select nothing' },
  { id: 'editor.shiftClick', scope: 'editor-mouse', display: [['⇧', 'Click']], owner: 'editor.js',
    label: 'Select the range from the selected clip to this one' },
  { id: 'editor.cmdClick', scope: 'editor-mouse', display: [['⌘', 'Click']], owner: 'editor.js',
    label: 'Add one clip to the selection, or drop it' },
  { id: 'editor.drag', scope: 'editor-mouse', display: [['Drag']], owner: 'editor.js',
    label: 'Move a clip — or the whole selection, sound included · drag a clip’s edge to trim · the music strip drags and trims the same way' },
  { id: 'editor.cmdDrag', scope: 'editor-mouse', display: [['⌘', 'Drag']], owner: 'editor.js',
    label: 'Ripple — everything after the clip slides too' },
  { id: 'editor.altDrag', scope: 'editor-mouse', display: [['⌥', 'Drag']], owner: 'editor.js',
    label: 'Ignore the beat grid while dragging' },
  { id: 'editor.altShiftDrag', scope: 'editor-mouse', display: [['⌥', '⇧', 'Drag']], owner: 'editor.js',
    label: 'Reorder instead of move' },
  { id: 'editor.rightClick', scope: 'editor-mouse', display: [['Right-click']], owner: 'editor.js',
    label: 'The clip bar’s buttons at the pointer, plus Move earlier / Move later · on a hole: Close this hole or Generate a shot here' },
  { id: 'editor.levels', scope: 'editor-mouse', display: [['Click'], ['⇧', 'Click']], displayJoin: ' / ',
    owner: 'editor.js',
    label: 'Level points, on an unlinked sound: click the yellow line to add one · drag it to set the level · ⇧-click it, or right-click it to remove it' },
  { id: 'editor.wheel', scope: 'editor-mouse', display: [['⌥', 'Scroll'], ['⇧', 'Scroll']], displayJoin: ' / ',
    owner: 'editor.js',
    label: 'Over the track: ⌥ + scroll (or a trackpad pinch) zooms · ⇧ + scroll pans' },
  { id: 'editor.tlEdge', scope: 'editor-mouse', display: [['Drag'], ['Double-click']], displayJoin: ' / ',
    owner: 'editor.js',
    label: 'The timeline’s top edge: drag up for taller tracks · double-click to reset · ↑ ↓ when it is focused' },

  // ---- storyboard ------------------------------------------------------------------
  { id: 'storyboard.grade', scope: 'storyboard', combos: ['k', 'r', 'c'], owner: 'storyboard.js',
    label: 'Grade the focused shot: K Keep · R Re-roll · C Cut — the same key again clears it' },
  { id: 'storyboard.collapse', scope: 'storyboard', combos: ['escape'], owner: 'storyboard.js',
    label: 'Collapse the docked player back to the shot list' },

  // ---- images: the layout canvas -------------------------------------------------
  { id: 'canvas.nudge', scope: 'canvas', combos: ['arrowleft', 'arrowright', 'arrowup', 'arrowdown'],
    owner: 'stage.js', label: 'Nudge the selected box · add ⇧ for a bigger step' },
  { id: 'canvas.delete', scope: 'canvas', combos: ['backspace'], hidden: ['delete'], owner: 'stage.js',
    label: 'Delete the selected box' },
  { id: 'canvas.deselect', scope: 'canvas', combos: ['escape'], owner: 'stage.js',
    label: 'Deselect the box · while editing its words, stop editing' },
  { id: 'canvas.undo', scope: 'canvas', combos: ['mod+z'], owner: 'stage.js', label: 'Undo' },
];

// Combinations the browser or macOS keeps for itself. A page that claims one
// either cannot (the OS wins) or breaks something people rely on — ⌘R was the
// Editor's Render key and turned a reload into a queued render.
const SHORTCUT_RESERVED = [
  'mod+w', 'mod+q', 'mod+t', 'mod+n', 'mod+r', 'mod+l', 'mod+h', 'mod+m',
  'mod+tab', 'mod+backquote', 'mod+space', 'shift+mod+t', 'shift+mod+n',
  'shift+mod+r', 'mod+comma', 'mod+p',
];

// ---- combos ----------------------------------------------------------------
const _SC_KEYNAMES = {
  space: ' ', enter: 'enter', escape: 'escape', backspace: 'backspace', delete: 'delete',
  arrowleft: 'arrowleft', arrowright: 'arrowright', arrowup: 'arrowup', arrowdown: 'arrowdown',
  home: 'home', end: 'end', plus: '+', minus: '-', equals: '=', underscore: '_',
  backslash: '\\', backquote: '`', comma: ',', tab: 'tab', slash: '/',
};
// Keys whose character already needs Shift on a US layout: their combos do not
// say shift, and matching ignores it.
const _SC_SHIFTED = new Set(['?', '+', '_']);

function shortcutParse(combo) {
  const parts = String(combo).toLowerCase().split('+');
  const out = { mod: false, shift: false, alt: false, ctrl: false, key: '', code: '' };
  for (const p of parts) {
    if (p === 'mod') out.mod = true;
    else if (p === 'shift') out.shift = true;
    else if (p === 'alt') out.alt = true;
    else if (p === 'ctrl') out.ctrl = true;
    else if (/^digit\d$/.test(p)) out.code = 'Digit' + p.slice(5);
    else out.key = (p in _SC_KEYNAMES) ? _SC_KEYNAMES[p] : p;
  }
  return out;
}

// One normal form, so `shift+mod+z` and `mod+shift+z` are the same entry in
// the reserved check and in the tests.
function shortcutNormalise(combo) {
  const c = shortcutParse(combo);
  const bits = [];
  if (c.ctrl) bits.push('ctrl');
  if (c.alt) bits.push('alt');
  if (c.shift) bits.push('shift');
  if (c.mod) bits.push('mod');
  const inv = Object.fromEntries(Object.entries(_SC_KEYNAMES).map(([k, v]) => [v, k]));
  bits.push(c.code ? c.code.toLowerCase() : (inv[c.key] || c.key));
  return bits.join('+');
}

function shortcutMatches(ev, combo) {
  const c = shortcutParse(combo);
  if (!ev) return false;
  const mod = !!(ev.metaKey || ev.ctrlKey);
  if (c.mod !== mod) return false;
  if (c.alt !== !!ev.altKey) return false;
  if (c.code) {
    if (ev.code !== c.code) return false;
  } else {
    if (String(ev.key || '').toLowerCase() !== c.key) return false;
  }
  if (!_SC_SHIFTED.has(c.key) && c.shift !== !!ev.shiftKey) return false;
  return true;
}

// ---- glyphs ----------------------------------------------------------------
const _SC_GLYPH = {
  ' ': 'Space', enter: '⏎', escape: '⎋ Esc', backspace: '⌫', delete: '⌦',
  arrowleft: '←', arrowright: '→', arrowup: '↑', arrowdown: '↓',
  home: 'Home', end: 'End', '+': '+', '-': '−', '\\': '\\',
};

// Apple's order: ⌃ ⌥ ⇧ ⌘, then the key.
function shortcutTokens(combo) {
  const c = shortcutParse(combo);
  const t = [];
  if (c.ctrl) t.push('⌃');
  if (c.alt) t.push('⌥');
  if (c.shift) t.push('⇧');
  if (c.mod) t.push('⌘');
  if (c.code) t.push(c.code.slice(5));
  else t.push(_SC_GLYPH[c.key] || c.key.toUpperCase());
  return t;
}

function shortcutById(id) {
  return SHORTCUTS.find(s => s.id === id) || null;
}

// The token groups a row DISPLAYS: its `display` override, or one group per
// visible combo.
function shortcutGroups(s) {
  if (!s) return [];
  if (s.display) return s.display;
  return (s.combos || []).map(shortcutTokens);
}

// Plain text for a tooltip: "⇧⌘Z", "S or ⌘K or ⌘B".
function shortcutHint(id) {
  const s = shortcutById(id);
  if (!s) return '';
  const groups = shortcutGroups(s).map(g => g.join(g.some(x => x.length > 1) ? ' ' : ''));
  return groups.join(s.displayJoin ? s.displayJoin : ' or ');
}

function _scEsc(t) {
  return String(t).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

// Keycaps as markup. `cls` is the keycap class — the Docs page and the
// Editor's popover draw keys differently.
function shortcutKeysHtml(s, cls) {
  const k = cls || 'kbd';
  const joiner = s.displayJoin ? s.displayJoin : ' ';
  return shortcutGroups(s)
    .map(g => '<span class="sc-chord">' + g.map(x => '<kbd class="' + k + '">' + _scEsc(x) + '</kbd>').join('') + '</span>')
    .join('<span class="sc-or">' + _scEsc(joiner) + '</span>');
}

// Rows for a set of scopes, already in markup: [{id, scope, keys, label}].
function shortcutRows(scopes, cls) {
  const want = new Set(scopes || SHORTCUT_SCOPES.map(x => x.id));
  return SHORTCUTS.filter(s => want.has(s.scope))
    .map(s => ({ id: s.id, scope: s.scope, keys: shortcutKeysHtml(s, cls), label: _scEsc(s.label) }));
}

// The Docs page's table, grouped by scope. Every row carries data-shortcut so
// the test can find each registry entry on the page.
function shortcutsDocHtml(scopes, idPrefix) {
  const want = scopes ? new Set(scopes) : null;
  const pre = idPrefix || '';
  return SHORTCUT_SCOPES.map(sc => {
    if (want && !want.has(sc.id)) return '';
    const rows = SHORTCUTS.filter(s => s.scope === sc.id);
    if (!rows.length) return '';
    return '<h3 id="' + _scEsc(pre) + 'keys-' + sc.id + '">' + _scEsc(sc.title) + '</h3>' +
      (sc.note ? '<p class="docs-muted">' + _scEsc(sc.note) + '</p>' : '') +
      '<table class="docs-keys"><tbody>' +
      rows.map(s => '<tr data-shortcut="' + _scEsc(s.id) + '"><td class="docs-keys-k">' +
        shortcutKeysHtml(s, 'kbd') + '</td><td>' + _scEsc(s.label) + '</td></tr>').join('') +
      '</tbody></table>';
  }).join('');
}

// Any element with data-shortcut="<id>" names its keys in its tooltip. The
// title written in the markup is the base; the keys are appended once.
function shortcutDecorate(root) {
  const scope = root || document;
  if (!scope.querySelectorAll) return;
  scope.querySelectorAll('[data-shortcut]').forEach(el => {
    const hint = shortcutHint(el.getAttribute('data-shortcut'));
    if (!hint) return;
    if (el.dataset.tipBase === undefined) el.dataset.tipBase = el.getAttribute('title') || '';
    const base = el.dataset.tipBase;
    el.setAttribute('title', base ? (base + ' (' + hint + ')') : hint);
  });
}

// ---- the handlers this file owns ---------------------------------------------
function shortcutTyping(t) {
  if (!t) return false;
  if (t.isContentEditable) return true;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName || '');
}

function _scVisible(el) {
  return !!el && (el.offsetWidth > 0 || el.offsetHeight > 0);
}

// The same list health.js's Esc scaffold closes, plus the Docs.
function shortcutTopModal() {
  const sel = '.models-modal, .model-browser-modal, .expand-lightbox, .modal-bg.show';
  const open = Array.from(document.querySelectorAll(sel)).filter(el =>
    el.classList.contains('open') || el.classList.contains('show')
    || el.style.display === 'flex' || el.style.display === 'block');
  return open.length ? open[open.length - 1] : null;
}

function shortcutFocusSearch() {
  const top = shortcutTopModal();
  let box = null;
  if (top) {
    box = Array.from(top.querySelectorAll('input[type="search"], input.docs-search')).find(_scVisible) || null;
  } else if (document.body.dataset.workflow === 'editor') {
    box = document.getElementById('edPoolSearch');
  } else {
    box = document.getElementById('outputsSearch');
  }
  if (!_scVisible(box)) return false;
  box.focus();
  if (box.select) box.select();
}

function shortcutGenerateFrom(t) {
  const id = t && t.id;
  if (id === 'prompt') {
    const form = document.getElementById('genForm');
    const btn = document.getElementById('genBtn');
    if (!form || !btn) return false;
    if (btn.disabled) {
      if (typeof phosToast === 'function') phosToast(btn.title || 'Generate is not available right now.', { duration: 5000 });
      return;
    }
    // The ONE submit path — the same listener the Generate button fires.
    if (form.requestSubmit) form.requestSubmit(btn); else btn.click();
    return;
  }
  if (id === 'imgStudioPrompt') {
    const btn = document.getElementById('imgStudioGenBtn');
    if (!btn || typeof imgStudioGenerate !== 'function') return false;
    if (btn.disabled) {
      if (typeof phosToast === 'function') phosToast(btn.title || 'Generate is not available right now.', { duration: 5000 });
      return;
    }
    imgStudioGenerate();
    return;
  }
  return false;   // One Shot's prompt has its own listener (oneshot.js)
}

const _SC_OUTPUT_TABS = new Set(['manual', 'oneshot', 'studio', 'audio']);

function _scOnOutputsTab() {
  if (!_SC_OUTPUT_TABS.has(document.body.dataset.workflow || 'manual')) return false;
  // The Layout canvas owns the arrows and ⌫ while it holds the stage.
  if (typeof ideoInLayout === 'function' && ideoInLayout()) return false;
  return true;
}

function shortcutTogglePlayer(ev) {
  const t = ev.target;
  // A focused control keeps Space — that is how a keyboard presses a button.
  if (t && t.closest && t.closest('button, a, [role="button"], video, audio, summary, label')) return false;
  const lb = document.getElementById('expandLightbox');
  const lbOpen = lb && lb.style.display === 'flex';
  if (!lbOpen && (shortcutTopModal() || !_scOnOutputsTab())) return false;
  const v = lbOpen
    ? lb.querySelector('video')
    : document.querySelector('#playerWrap video');
  if (!v) return false;
  window._stagePlaybackIntentAt = Date.now();
  if (v.paused) { const p = v.play(); if (p && p.catch) p.catch(() => {}); }
  else v.pause();
}

function shortcutTrashOutput() {
  if (shortcutTopModal() || !_scOnOutputsTab()) return false;
  if (typeof activePath === 'undefined' || !activePath || typeof deleteOutput !== 'function') return false;
  deleteOutput(activePath);   // confirms before it moves anything
}

document.addEventListener('keydown', (ev) => {
  if (ev.defaultPrevented || ev.isComposing) return;
  const typing = shortcutTyping(ev.target);
  for (const s of SHORTCUTS) {
    if (!s.run) continue;
    if (typing && !s.typing) continue;
    if (!s.typing && ev.target && ev.target.id === 'docsSearch' && s.id !== 'search.focus') continue;
    const combos = (s.combos || []).concat(s.hidden || []);
    if (!combos.some(c => shortcutMatches(ev, c))) continue;
    // The page-level keys stand down while a dialog is up — except ⌘F, which
    // finds the dialog's own search box, and ?, which is harmless on the Docs.
    if (s.scope === 'global' && s.id !== 'search.focus') {
      const top = shortcutTopModal();
      if (top && !(s.id === 'docs.open' && top.id === 'docsView')) continue;
    }
    if (s.run(ev) === false) continue;
    ev.preventDefault();
    return;
  }
});

shortcutDecorate(document);

// ---- published to the page ---------------------------------------------------
Object.assign(globalThis, {
  SHORTCUTS, SHORTCUT_SCOPES, SHORTCUT_RESERVED,
  shortcutParse, shortcutNormalise, shortcutMatches, shortcutTokens,
  shortcutById, shortcutHint, shortcutKeysHtml, shortcutRows, shortcutsDocHtml,
  shortcutDecorate, shortcutTyping,
});
