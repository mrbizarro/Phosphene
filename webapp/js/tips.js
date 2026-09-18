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
  '.sbe-head .ghost-btn', '.sbe-head .primary', '.sbe-info', '.po-act', '[data-tip]',
].join(', ');

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
  return node.closest(TIP_SCOPE);
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
  const raw = target.dataset.tip || '';
  const s = tipSplit(target, raw);
  const keys = s.keys.length
    ? '<span class="phos-tip-keys">' + s.keys.map(k => '<kbd>' + tipEsc(k) + '</kbd>')
        .join('<span class="phos-tip-or">or</span>') + '</span>'
    : '';
  el.innerHTML = (s.name || keys
      ? '<div class="phos-tip-head">' + (s.name ? '<span class="phos-tip-name">' + tipEsc(s.name) + '</span>' : '') + keys + '</div>'
      : '')
    + (s.body ? '<div class="phos-tip-body">' + tipEsc(s.body) + '</div>' : '');
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
  if (TIP.target && TIP.target !== target) TIP.target.removeAttribute('aria-describedby');
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
  if (TIP.target) TIP.target.removeAttribute('aria-describedby');
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
  if (!target.dataset.tip) { tipHide(); return; }
  if (TIP.target && TIP.target !== target) TIP.target.removeAttribute('aria-describedby');
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
document.addEventListener('pointerdown', () => tipHide(), true);
document.addEventListener('scroll', () => { if (TIP.shown || TIP.timer) tipHide(); }, true);
window.addEventListener('blur', () => tipHide());
document.addEventListener('keydown', (ev) => { if (ev.key === 'Escape' && TIP.shown) tipHide(); }, true);
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
      if (!el.hasAttribute('title') || !el.matches || !el.matches(TIP_SCOPE)) continue;
      tipAdopt(el);
      if (el === TIP.target && TIP.shown) { tipRender(el); tipPlace(el); }
    }
  }).observe(document.documentElement, { attributes: true, attributeFilter: ['title'], subtree: true });
}

Object.assign(globalThis, { tipSplit, tipHide, TIP_DELAY_MS, TIP_SCOPE });
