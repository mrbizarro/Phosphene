// webapp/js/tips.js — FAST TOOLTIPS for the panel's icon buttons.
//
// "The tooltips take too long … The tooltips on the buttons are too slow."
// The browser's own `title` tooltip waits about a second and cannot be told to
// hurry. This is one shared tooltip element instead:
//
//   * 150 ms after the pointer settles on a button (TIP_DELAY_MS), and at once
//     while you slide along a row — a tip that was showing, or closed less
//     than TIP_WARM_MS ago, makes the next one immediate;
//   * name · what it does · the keys, the keys as keycaps. The name is the
//     title's first line, or the words before " — "; the keys come from the
//     element's data-shortcut (the one SHORTCUTS table) or a trailing "(⌘I)";
//   * placed below the element, flipped above when there is no room, and kept
//     8px inside the window on every side;
//   * keyboard focus shows it too (focus-visible only, so a click does not);
//   * no transition under prefers-reduced-motion (the CSS owns that).
//
// NOTHING THAT WRITES A TITLE HAD TO CHANGE. The code that paints these
// buttons keeps setting `title` (and the tests keep reading it). A button in
// TIP_SCOPE has its title moved into data-tip the moment the pointer or the
// focus reaches it, and a MutationObserver moves it again whenever a repaint
// writes a new one — so the OS tooltip never gets its second, and the custom
// one always shows the current text. aria-label is left alone; when a button
// has no accessible name but its title, the name is copied to aria-label
// before the title goes.

const TIP_DELAY_MS = 150;
const TIP_WARM_MS = 400;
const TIP_GAP = 6;
const TIP_MARGIN = 8;
// The icon buttons: the Editor's strips, tool row, View group and header, the
// player's action pills (.po-act), and anything that opts in with data-tip.
const TIP_SCOPE = [
  '.sbe-ibtn', '.sbe-tbtn', '.sbe-vbtn', '#sbeCbar .sbe-cbar-btn', '.sbe-chipbtn',
  '.sbe-head .ghost-btn', '.sbe-head .primary', '.sbe-info', '.po-act', '[data-tip]', '.help-dot[data-help]', '.help-dot[aria-controls]',
].join(', ');
// Feedback 5 (owner, 22 Nov 2026: "tooltips are buggy, sometimes they don't pop, long text gets cut"):
// every titled control of the left form and the character voice menu shows this same fast tip, not the
// OS one (slow, and flaky inside the Mac app's web view). These titles are BORROWED, not adopted: the
// title moves to data-tip only while the tip is up and goes back when it hides, so code that reads or
// appends to a title keeps seeing it.
const TIP_BORROW_SCOPE = [
  ['aside.form-pane', '[title]:not(textarea)'].join(' '),
  '#charVoiceMenu [title]', '#osVoiceMenu [title]',
].join(', ');
const TIP_ALL_SCOPE = TIP_SCOPE + ', ' + TIP_BORROW_SCOPE;
function tipIsBorrow(el) {
  return !!(el && el.matches && !el.matches(TIP_SCOPE) && (el.dataset.tipBorrow === '1' || el.matches(TIP_BORROW_SCOPE)));
}
// Hover shows the first sentences only; a click on the "?" opens the whole text (feedback 5: short text).
const TIP_HOVER_MAX = 210;

const TIP = { el: null, target: null, timer: 0, lastHide: 0, shown: false };

function tipEl() {
  if (TIP.el) return TIP.el;
  const el = document.createElement('div');
  el.className = 'phos-tip';
  el.id = 'phosTip';
  el.setAttribute('role', 'tooltip');
  el.hidden = true;
  document.body.appendChild(el);
  TIP.el = el;
  return el;
}

function tipEsc(s) {
  return String(s).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

// Take the title off an element in scope and keep it as data-tip.
function tipAdopt(el) {
  if (!el || !el.getAttribute) return;
  const t = el.getAttribute('title');
  if (t === null) return;
  if (tipIsBorrow(el)) el.dataset.tipBorrow = '1';
  if (!t) delete el.dataset.tip;
  else {
    el.dataset.tip = t;
    const named = el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')
      || (el.textContent || '').trim();
    if (!named) el.setAttribute('aria-label', tipSplit(el, t).name || t);
  }
  el.removeAttribute('title');
}

function tipScoped(node) {
  if (!node || node.nodeType !== 1 || !node.closest) return null;
  return node.closest(TIP_ALL_SCOPE);
}
// Code that clears a title while the tip has it borrowed (removeAttribute on an attribute that is not
// there is a no-op the observer never sees) must still clear the tip, not get the old words back later.
if (typeof Element === 'function' && Element.prototype && !Element.prototype._phosTipRm) {
  const rm = Element.prototype.removeAttribute;
  Element.prototype._phosTipRm = rm;
  Element.prototype.removeAttribute = function (name) {
    if (String(name).toLowerCase() === 'title' && this.dataset && this.dataset.tipBorrow === '1' && !this.hasAttribute('title')) {
      delete this.dataset.tipBorrow;
      delete this.dataset.tip;
      if (TIP.target === this) tipHide();
    }
    return rm.call(this, name);
  };
}
// Give a borrowed title back (the tip is gone or moved on).
function tipGiveBack(el) {
  if (!el || !el.dataset || el.dataset.tipBorrow !== '1') return;
  const t = el.dataset.tip;
  delete el.dataset.tipBorrow;
  delete el.dataset.tip;
  if (t && !el.hasAttribute('title')) el.setAttribute('title', t);
}

// Keys: data-shortcut first (the registry is the truth), then a trailing
// "(…)" that reads like keys. Returns [text-without-keys, [groups]].
function tipKeys(el, text) {
  let groups = [];
  let body = text;
  const id = el.getAttribute && el.getAttribute('data-shortcut');
  const hint = (id && typeof shortcutHint === 'function') ? shortcutHint(id) : '';
  const m = body.match(/\s*\(([^()]{1,28})\)\s*$/);
  const looksLikeKeys = (s) => /[⌘⇧⌥⌃⌫⏎↑↓←→`]/.test(s) || /^[A-Za-z0-9+\-]$/.test(s.trim())
    || /^([A-Za-z0-9+\-]|⌘\S+)( or ([A-Za-z0-9+\-]|⌘\S+))+$/.test(s.trim());
  if (m && (m[1] === hint || looksLikeKeys(m[1]))) {
    body = body.slice(0, m.index);
    if (!hint) groups = m[1].split(/\s+or\s+/);
  }
  if (hint) groups = hint.split(/\s+or\s+/);
  return [body, groups];
}

function tipSplit(el, raw) {
  const [text, keys] = tipKeys(el, String(raw || '').trim());
  let name = '', body = text;
  const nl = text.indexOf('\n');
  if (nl > 0 && nl <= 48) { name = text.slice(0, nl); body = text.slice(nl + 1); }
  else {
    const dash = text.indexOf(' — ');
    if (dash > 0 && dash <= 40) { name = text.slice(0, dash); body = text.slice(dash + 3); }
    else if (text.length <= 40) { name = text; body = ''; }
  }
  body = body.trim();
  if (body) body = body.charAt(0).toUpperCase() + body.slice(1);
  return { name: name.trim(), body: body, keys: keys };
}

function tipRender(target) {
  const el = tipEl();
  let raw = target.dataset.tip || '';
  let more = false;
  if (target.matches && target.matches('.help-dot[data-help], .help-dot[aria-controls]') && raw.length > TIP_HOVER_MAX) {
    const cut = raw.slice(0, TIP_HOVER_MAX);
    const stop = Math.max(cut.lastIndexOf('. '), cut.lastIndexOf('? '), cut.lastIndexOf('! '));
    raw = stop > 60 ? cut.slice(0, stop + 1) : cut.replace(/\s+\S*$/, '') + '\u2026';
    more = true;
  }
  const s = tipSplit(target, raw);
  const keys = s.keys.length
    ? '<span class="phos-tip-keys">' + s.keys.map(k => '<kbd>' + tipEsc(k) + '</kbd>')
        .join('<span class="phos-tip-or">or</span>') + '</span>'
    : '';
  el.innerHTML = (s.name || keys
      ? '<div class="phos-tip-head">' + (s.name ? '<span class="phos-tip-name">' + tipEsc(s.name) + '</span>' : '') + keys + '</div>'
      : '')
    + (s.body ? '<div class="phos-tip-body">' + tipEsc(s.body) + '</div>' : '')
    + (more ? '<div class="phos-tip-more">' + tipEsc(typeof T === 'function' ? T('tips.click_for_more', 'Click ? for the rest') : '') + '</div>' : '');
}

function tipPlace(target) {
  const el = tipEl();
  const r = target.getBoundingClientRect();
  const vw = window.innerWidth, vh = window.innerHeight;
  const w = el.offsetWidth, h = el.offsetHeight;
  let top = r.bottom + TIP_GAP;
  if (top + h > vh - TIP_MARGIN && r.top - TIP_GAP - h >= TIP_MARGIN) top = r.top - TIP_GAP - h;
  top = Math.max(TIP_MARGIN, Math.min(vh - TIP_MARGIN - h, top));
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(TIP_MARGIN, Math.min(vw - TIP_MARGIN - w, left));
  el.style.left = Math.round(left) + 'px';
  el.style.top = Math.round(top) + 'px';
}

function tipShow(target) {
  clearTimeout(TIP.timer);
  if (!target.isConnected || !target.dataset.tip) { tipHide(); return; }
  const el = tipEl();
  const warm = TIP.shown || (performance.now() - TIP.lastHide < TIP_WARM_MS);
  if (TIP.target && TIP.target !== target) { TIP.target.removeAttribute('aria-describedby'); tipGiveBack(TIP.target); }
  TIP.target = target;
  tipRender(target);
  el.hidden = false;
  el.classList.toggle('is-warm', warm);
  tipPlace(target);
  target.setAttribute('aria-describedby', 'phosTip');
  el.classList.add('is-shown');
  TIP.shown = true;
}

function tipHide() {
  clearTimeout(TIP.timer);
  TIP.timer = 0;
  if (TIP.target) { TIP.target.removeAttribute('aria-describedby'); tipGiveBack(TIP.target); }
  TIP.target = null;
  if (!TIP.shown) return;
  TIP.shown = false;
  TIP.lastHide = performance.now();
  const el = tipEl();
  el.classList.remove('is-shown');
  el.hidden = true;
}

function tipArm(target) {
  tipAdopt(target);
  tipHelpText(target);
  if (!target.dataset.tip) { tipGiveBack(target); tipHide(); return; }
  if (TIP.target && TIP.target !== target) { TIP.target.removeAttribute('aria-describedby'); tipGiveBack(TIP.target); }
  clearTimeout(TIP.timer);
  const warm = TIP.shown || (performance.now() - TIP.lastHide < TIP_WARM_MS);
  if (warm) { tipShow(target); return; }
  TIP.target = target;
  TIP.timer = setTimeout(() => tipShow(target), TIP_DELAY_MS);
}

// Pointer: `pointerover` reaches disabled buttons in current Chromium and
// WebKit, and the disabled ones are where the tooltip says WHY.
document.addEventListener('pointerover', (ev) => {
  if (ev.pointerType === 'touch') return;
  const t = tipScoped(ev.target);
  if (t) { if (t !== TIP.target) tipArm(t); return; }
  if (TIP.target) tipHide();
}, true);
document.addEventListener('pointerout', (ev) => {
  const t = tipScoped(ev.target);
  if (!t || t !== TIP.target) return;
  if (ev.relatedTarget && t.contains(ev.relatedTarget)) return;
  tipHide();
}, true);
document.addEventListener('pointerdown', (ev) => {
  tipHide();
  const pop = HELP.pop;
  if (pop && !(pop.contains(ev.target)) && !(HELP.dot && HELP.dot.contains(ev.target))) helpUnpin();
}, true);
document.addEventListener('scroll', (ev) => {
  if (TIP.shown || TIP.timer) tipHide();
  if (HELP.pop && !(ev.target && ev.target.nodeType === 1 && HELP.pop.contains(ev.target))) helpPlace();
}, true);
window.addEventListener('resize', () => { if (HELP.pop) helpPlace(); });
window.addEventListener('blur', () => tipHide());
document.addEventListener('keydown', (ev) => {
  if (ev.key !== 'Escape') return;
  if (TIP.shown) tipHide();
  if (HELP.pop) { const d = HELP.dot; helpUnpin(); if (d && d.focus) d.focus(); }
}, true);
document.addEventListener('focusin', (ev) => {
  const t = tipScoped(ev.target);
  if (!t || t !== ev.target) return;
  let visible = false;
  try { visible = t.matches(':focus-visible'); } catch (e) {}
  if (visible) tipArm(t);
});
document.addEventListener('focusout', (ev) => { if (tipScoped(ev.target) === TIP.target) tipHide(); });

// A repaint that writes a new title on a button in scope: adopt it at once,
// and refresh the tip if it is the one on screen (a toggle that just flipped).
if (typeof MutationObserver === 'function') {
  new MutationObserver((muts) => {
    for (const m of muts) {
      const el = m.target;
      if (!el.hasAttribute('title') || !el.matches) continue;
      if (!el.matches(TIP_SCOPE) && !(el === TIP.target && el.matches(TIP_ALL_SCOPE))) continue;
      tipAdopt(el);
      if (el === TIP.target && TIP.shown) { tipRender(el); tipPlace(el); }
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['title'], subtree: true });
}

// "?" HELP (owner, 22 Nov 2026, feedback 3: "No explanation text in the middle of the interface. Use
// a small ? with a tooltip next to the feature, or a sleek More info that expands"). Markup:
//   <button type="button" class="help-dot" data-help="fooHelp" aria-expanded="false">?</button>
//   <div class="help-pop" id="fooHelp" hidden>The sentence, translated like any other markup.</div>
// Hover (or keyboard focus) shows the sentence as the fast tooltip; a click pins it open in place
// (touch screens, long reads) and a second click folds it again.
// Feedback 5: the "?" never opens text INSIDE the form any more (an in-place paragraph could be cut by
// its row, push the layout, and while it was open the hover tip went silent). Hover and keyboard focus
// show the fast tip; a click or a tap PINS the whole explanation in a floating card beside the "?"
// (the very same element, moved into a fixed layer and back, so its links, ids and translations stay),
// sized to its text and kept inside the window. A second click, Escape or a click elsewhere closes it.
// The older dots (aria-controls + their own onclick, which fills the note lazily) are the same help.
const HELP_DOT = '.help-dot[data-help], .help-dot[aria-controls]';
function helpPopOf(dot) {
  const id = dot.dataset.help || dot.getAttribute('aria-controls');
  const pop = id ? document.getElementById(id) : null;
  if (pop && !dot.dataset.help && !(pop.textContent || '').trim() && typeof dot.onclick === 'function') {
    // Let its own handler fill the words (it toggles; twice leaves it as it was), then keep it folded.
    try { dot.onclick.call(dot); dot.onclick.call(dot); } catch (e) {}
    pop.hidden = true; dot.setAttribute('aria-expanded', 'false');
  }
  return pop;
}
function tipHelpText(target) {
  if (!target || !target.matches || !target.matches(HELP_DOT)) return;
  const pop = helpPopOf(target);
  const text = pop && pop !== HELP.pop ? (pop.textContent || '').replace(/\s+/g, ' ').trim() : '';
  if (text) target.dataset.tip = text; else delete target.dataset.tip;
}
const HELP = { dot: null, pop: null, mark: null, layer: null };
function helpLayer() {
  if (HELP.layer && HELP.layer.isConnected) return HELP.layer;
  const el = document.createElement('div');
  el.className = 'phos-pop-layer';
  el.id = 'phosPopLayer';
  document.body.appendChild(el);
  HELP.layer = el;
  return el;
}
function helpPlace() {
  const pop = HELP.pop, dot = HELP.dot;
  if (!pop || !dot) return;
  const r = dot.getBoundingClientRect();
  if (!dot.isConnected || (r.width === 0 && r.height === 0) || r.bottom < 0 || r.top > window.innerHeight) { helpUnpin(); return; }
  // Scrolled out of its own scroller (the form's edge, the sticky footer's top): close, do not float on.
  for (let a = dot.parentElement; a && a !== document.body; a = a.parentElement) {
    const oy = getComputedStyle(a).overflowY;
    if (oy !== 'auto' && oy !== 'scroll') continue;
    const c = a.getBoundingClientRect();
    if (r.bottom < c.top || r.top > c.bottom) { helpUnpin(); return; }
    break;
  }
  const vw = window.innerWidth, vh = window.innerHeight;
  pop.style.maxHeight = (vh - 2 * TIP_MARGIN) + 'px';
  const w = pop.offsetWidth, h = pop.offsetHeight;
  let top = r.bottom + TIP_GAP;
  if (top + h > vh - TIP_MARGIN && r.top - TIP_GAP - h >= TIP_MARGIN) top = r.top - TIP_GAP - h;
  top = Math.max(TIP_MARGIN, Math.min(vh - TIP_MARGIN - h, top));
  let left = r.left + r.width / 2 - w / 2;
  left = Math.max(TIP_MARGIN, Math.min(vw - TIP_MARGIN - w, left));
  pop.style.left = Math.round(left) + 'px';
  pop.style.top = Math.round(top) + 'px';
}
function helpUnpin() {
  const pop = HELP.pop, dot = HELP.dot, mark = HELP.mark;
  HELP.pop = HELP.dot = HELP.mark = null;
  if (dot) dot.setAttribute('aria-expanded', 'false');
  if (!pop) return;
  pop.classList.remove('is-floating');
  pop.style.left = pop.style.top = pop.style.maxHeight = '';
  pop.hidden = true;
  if (mark && mark.parentNode) { mark.parentNode.insertBefore(pop, mark); mark.remove(); }
  else pop.remove();   // its section was redrawn while it was open: the redraw made a fresh one
}
function helpPin(dot) {
  const pop = helpPopOf(dot);
  if (!pop || !(pop.textContent || '').trim()) return;
  pop.classList.add('help-pop');
  helpUnpin();
  tipHide();
  const mark = document.createComment('help-pop');
  pop.parentNode.insertBefore(mark, pop);
  HELP.dot = dot; HELP.pop = pop; HELP.mark = mark;
  helpLayer().appendChild(pop);
  pop.classList.add('is-floating');
  pop.hidden = false;
  dot.setAttribute('aria-expanded', 'true');
  helpPlace();
}
document.addEventListener('click', (ev) => {
  const b = ev.target && ev.target.closest && ev.target.closest(HELP_DOT);
  if (!b) return;
  ev.preventDefault(); ev.stopPropagation();
  if (HELP.dot === b) helpUnpin(); else helpPin(b);
}, true);

// Static explanation paragraphs of the Video form become a "?" next to their heading (feedback 3).
// Only text that never changes at run time (no id, or listed here) is taken; the element itself stays
// (same data-i18n key, translated as before) and turns into the help-pop the "?" opens.
const HELPIFY_SCOPE = [
  '#genForm .mode-only > .hint:not([id])', '#genForm .mode-only > div > .hint:not([id])',
  '#genForm .ct-hint', '#h3SpeedRow .qs-meta', '#genForm .cz-body > .hint:not([id])', '#genForm .cz-body > .cz-control > .hint:not([id])',
].join(', ');
let _helpifyN = 0;
function helpifyHints(root) {
  root = root || document;
  if (!root || typeof root.querySelectorAll !== 'function') return;   // no DOM (node test harness)
  root.querySelectorAll(HELPIFY_SCOPE).forEach(el => {
    if (el.dataset.helpified || el.closest('.picker-empty, .help-pop')) return;
    if ((el.textContent || '').replace(/\s+/g, ' ').trim().length < 40) return;
    el.dataset.helpified = '1';
    if (!el.id) el.id = 'helpAuto' + (++_helpifyN);
    const dot = document.createElement('button');
    dot.type = 'button'; dot.className = 'help-dot'; dot.dataset.help = el.id; dot.textContent = '?';
    dot.setAttribute('aria-expanded', 'false');
    if (typeof T === 'function') dot.setAttribute('aria-label', T('tips.more_info', 'More info'));
    // The dot shows exactly when the paragraph would have.
    ['extend-only', 'v2a-only'].forEach(c => { if (el.classList.contains(c)) dot.classList.add(c); });
    ['data-h3-only', 'data-ltx-only'].forEach(a => { if (el.hasAttribute(a)) dot.setAttribute(a, ''); });
    let host = null, sib = el.previousElementSibling;
    for (let i = 0; sib && i < 4 && !host; i++, sib = sib.previousElementSibling) {
      if (sib.matches('h2, .qs-name, .ph-sec-head, .px-q, .cz-label')) host = sib;
    }
    if (!host && el.parentElement && el.parentElement.matches('.mode-only')) host = el.parentElement.querySelector(':scope > h2');
    if (host) host.appendChild(dot); else el.before(dot);
    el.classList.add('help-pop');
    el.style.marginTop = '';
    el.hidden = true;
  });
  // After the paragraphs above found their headings, so a heading never gets two dots.
  helpifyLabelHints(root);
  helpDotsDedupe(root);
}
// One "?" per heading: where two landed on the same line, the second's words join the first's pop.
function helpDotsDedupe(root) {
  root = root || document;
  if (!root || typeof root.querySelectorAll !== 'function') return;
  const seen = new Set();
  root.querySelectorAll('aside.form-pane .help-dot').forEach(dot => {
    const host = dot.parentElement;
    if (!host || seen.has(host)) return;
    const dots = Array.from(host.children).filter(c => c.classList && c.classList.contains('help-dot'));
    if (dots.length < 2) return;
    seen.add(host);
    const first = document.getElementById(dots[0].dataset.help || '');
    dots.slice(1).forEach(d => {
      const pop = document.getElementById(d.dataset.help || '');
      if (first && pop && pop !== first) {
        const extra = (pop.textContent || '').trim();
        if (extra && !(first.textContent || '').includes(extra)) first.append(document.createElement('br'), document.createTextNode(extra));
        pop.dataset.mergedInto = first.id || '';
      }
      d.remove();
    });
  });
}
// Feedback 4 (owner, 22 Nov 2026): the short grey hints written INSIDE a control's label ("draft
// schedule · changes the take", "-1 = random", "portrait rotates the tier's canvas") become that
// label's "?" too. The hint element itself moves just after the label and becomes the pop (same id,
// so code that rewrites it keeps working); the dot hides while the hint is empty.
const LABEL_HINT_SCOPE = [
  'aside.form-pane .cz-label > .cz-label-hint', 'aside.form-pane .mf-label > .mf-hint',
  'aside.form-pane h2 > .h2-hint', 'aside.form-pane .qs-label > .qs-name ~ .qs-meta',
].join(', ');
// Kept in plain view: a few words ("optional", "· all optional") and anything that is an estimate.
// Live state (progress, errors, counts, the picked track) is never folded behind a "?" (Codex r4-1 #5).
const LABEL_HINT_KEEP = /^(qualityMeta|ltxLengthMeta|h3LengthMeta|osStatusMeta|batchTakesNote|sbTrackMeta|osBeatsHint|sbeMatchStrengthHint)$|Estimate|Eta|Status|Error|Progress/;
function helpifyLabelHints(root) {
  root = root || document;
  if (!root || typeof root.querySelectorAll !== 'function') return;
  root.querySelectorAll(LABEL_HINT_SCOPE).forEach(el => {
    if (el.dataset.helpified) return;
    const label = el.parentElement;
    if (!label || label.tagName === 'LABEL' || label.closest('label.check, .modal, [role="dialog"]')) return;   // a checkbox's own words stay
    if (LABEL_HINT_KEEP.test(el.id || '') || LABEL_HINT_KEEP.test(label.id || '') || el.closest('#h3SpeedRow')) return;
    const words = (el.textContent || '').replace(/\s+/g, ' ').trim();
    if (words && !el.classList.contains('mf-hint') && words.replace(/^[·\s]+/, '').length <= 14) return;
    el.dataset.helpified = '1';
    // The label already has its own "?": the sub-line's words become the label's tooltip.
    if (label.querySelector(':scope > .help-dot') || (label.nextElementSibling && label.nextElementSibling.matches('.help-dot'))) {
      if (words && !label.title) label.title = words;
      el.classList.add('label-hint-folded');
      return;
    }
    if (!el.id) el.id = 'helpAuto' + (++_helpifyN);
    const dot = document.createElement('button');
    dot.type = 'button'; dot.className = 'help-dot'; dot.dataset.help = el.id; dot.textContent = '?';
    dot.setAttribute('aria-expanded', 'false');
    if (typeof T === 'function') dot.setAttribute('aria-label', T('tips.more_info', 'More info'));
    label.appendChild(dot);
    label.after(el);
    el.classList.add('help-pop', 'label-hint-pop');
    el.hidden = true;
  });
}
// Feedback 4: option buttons show their name only; the grey sub-line under the name is the button's
// tooltip. Works for buttons drawn later too (Storyboard's engine pills), so it is read on hover.
const SUB_TIP_SCOPE = '#audioModeGroup .mode-chip, #trainTypeGroup .pill-btn, #sbEngineGroup .pill-btn, #osCharacterGroup .pill-btn, #h3OrientationGroup .pill-btn, #genForm .cz-body .pill-group:not(#h3SpeedGroup):not(#schedPresetGroup) > .pill-btn, #genForm .kf-toggle-chip';
if (typeof document !== 'undefined' && document.addEventListener) {
  document.addEventListener('pointerover', (e) => {
    const b = e.target && e.target.closest && e.target.closest(SUB_TIP_SCOPE);
    if (!b || b.dataset.subTip) return;
    const sub = b.querySelector('.sub, .mc-sub');
    const words = sub ? (sub.textContent || '').replace(/\s+/g, ' ').trim() : '';
    if (!words) return;
    b.dataset.subTip = '1';
    const name = Array.from(b.childNodes).filter(n => !(n.classList && (n.classList.contains('sub') || n.classList.contains('mc-sub') || n.classList.contains('pro-tag'))))
      .map(n => n.textContent || '').join(' ').replace(/\s+/g, ' ').trim();
    // The fast tip may already have borrowed the title (it runs first): write where the text now lives.
    const borrowed = b.dataset.tipBorrow === '1';
    const cur = borrowed ? (b.dataset.tip || '') : (b.title || '');
    const next = !cur ? (name ? `${name}: ${words}` : words) : (cur.includes(words) ? cur : `${cur}\n${words}`);
    if (next === cur) return;
    if (borrowed) { b.dataset.tip = next; if (b === TIP.target && TIP.shown) { tipRender(b); tipPlace(b); } }
    else b.title = next;
  }, true);
}
// main.js calls helpifyHints() right after applyI18n(document): converting first would add a child
// to translated headings and make their stored translations no longer match (i18n.js _applyMessage).

Object.assign(globalThis, { tipSplit, tipHide, helpPin, helpUnpin, TIP_BORROW_SCOPE, TIP_DELAY_MS, TIP_SCOPE, helpifyHints, helpifyLabelHints, helpDotsDedupe });
