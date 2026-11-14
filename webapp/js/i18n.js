// webapp/js/i18n.js — the Studio's string lookup (docs/TRANSLATING.md).
//
// English lives in the code: markup carries data-i18n="key" attributes on
// the element whose text it is, JS calls T('key', 'English {name}', {name})
// or TP('key', n, {one: '{count} clip', other: '{count} clips'}). Capital T
// on purpose: `t` is a common local name in these modules and would shadow it. The
// server picks the language (i18n.py: the Studio setting, else the Mac's
// language list) and ships only that language's strings in BOOT.i18n, so a
// lookup is synchronous and a missing key always falls back to the English
// written next to it — the page can never show a raw key.
//
// This tag loads FIRST among the modules so every other module may call
// T() at evaluation time. The markup pass (applyI18n(document)) is a
// run-once startup call and lives in main.js like every other.

const _I18N = (typeof BOOT !== 'undefined' && BOOT && BOOT.i18n) || {};
const _STR = _I18N.strings || {};
const _LANG = _I18N.lang || 'en';
let _plural = null;
try { _plural = new Intl.PluralRules(_LANG); } catch (e) { _plural = new Intl.PluralRules('en'); }

function _fill(s, vars) {
  if (!vars) return s;
  return s.replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, (m, k) =>
    Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m);
}

// T('key', 'English with {name}', {name: value}) -> the translation, or the
// English, with {name} placeholders filled.
function T(key, english, vars) {
  const tr = _STR[key];
  return _fill((typeof tr === 'string' && tr) ? tr : String(english == null ? '' : english), vars);
}

// TP('key', count, {one: '{count} clip', other: '{count} clips'}, vars) —
// plural forms. A translation supplies its own language's CLDR categories
// (Russian one/few/many/other, Japanese other only); a missing category
// falls back to "other", then to the English.
// TA() — the same lookup for text that lands inside an HTML attribute
// value in a template (title="${TA(...)}"): a translation is escaped for the
// attribute; the English is returned exactly as written in the code.
function TA(key, english, vars) {
  const tr = _STR[key];
  if (typeof tr === 'string' && tr) {
    return _fill(tr, vars).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  return _fill(String(english == null ? '' : english), vars);
}

function TP(key, count, forms, vars) {
  const n = Number(count);
  const v = Object.assign({ count: count }, vars || {});
  const tr = _STR[key];
  if (tr && typeof tr === 'object') {
    const cat = _plural.select(n);
    const s = tr[cat] || tr.other;
    if (s) return _fill(s, v);
  }
  const en = forms || {};
  return _fill((n === 1 && en.one) ? en.one : (en.other || en.one || ''), v);
}

function i18nLang() { return _LANG; }

// i18nAgo(seconds) -> "hace 5 min", "vor 3 Std.", "3분 전"… in the page's
// language (Intl, CLDR rules). English call sites keep their own wording, so
// they only ask for this when i18nLang() !== 'en'.
function i18nAgo(sec) {
  const s = Math.max(0, Number(sec) || 0);
  let rtf;
  try { rtf = new Intl.RelativeTimeFormat(_LANG, { numeric: 'auto', style: 'short' }); } catch (e) { rtf = new Intl.RelativeTimeFormat('en', { style: 'short' }); }
  if (s < 60) return rtf.format(-Math.round(s), 'second');
  if (s < 3600) return rtf.format(-Math.round(s / 60), 'minute');
  if (s < 86400) return rtf.format(-Math.round(s / 3600), 'hour');
  if (s < 86400 * 30) return rtf.format(-Math.round(s / 86400), 'day');
  return rtf.format(-Math.round(s / (86400 * 30)), 'month');
}
function i18nDir() { return _I18N.dir || 'ltr'; }

// ---- markup ---------------------------------------------------------------
// An element with data-i18n="key" is one message made of its direct
// children: text runs, plus each child element as a numbered marker —
// <0>text</0> for an inline child holding only text (its text is
// translatable in place, e.g. <b>Pro</b>), <0/> for anything else (an icon,
// an input, a child with its own data-i18n). The translation may move the
// markers but must keep exactly the same set; the ORIGINAL child nodes are
// reused, so ids, listeners and classes survive. Text is set as text nodes,
// never parsed as HTML — a translation cannot inject markup.
// Same set as scripts/i18n_tool.py PAIRED — keep the two in step. Code,
// keyboard keys and file names (<code>, <kbd>) are opaque: never translated.
const _PAIRED = new Set(['B', 'I', 'EM', 'STRONG', 'A', 'SPAN', 'SMALL',
  'U', 'S', 'MARK', 'SUB', 'SUP', 'ABBR', 'Q', 'CITE', 'DFN', 'TIME']);

function _isPaired(el) {
  if (!_PAIRED.has(el.tagName)) return false;
  if (el.id || el.hasAttribute('data-i18n')) return false;
  for (const c of el.childNodes) if (c.nodeType !== 3) return false;
  return /[A-Za-z]/.test(el.textContent);
}

function _tokens(s) {
  const out = [];
  const re = /<(\/?)(\d+)(\/?)>/g;
  let last = 0, m;
  while ((m = re.exec(s))) {
    if (m.index > last) out.push({ text: s.slice(last, m.index) });
    out.push({ n: +m[2], close: !!m[1], self: !!m[3] });
    last = re.lastIndex;
  }
  if (last < s.length) out.push({ text: s.slice(last) });
  return out;
}

function _applyMessage(el, tr) {
  const kids = Array.from(el.childNodes).filter(c => c.nodeType === 1);
  const want = new Set();
  kids.forEach((c, i) => want.add(_isPaired(c) ? `p${i}` : `o${i}`));
  const toks = _tokens(tr);
  const got = new Set();
  const nodes = [];
  for (let i = 0; i < toks.length; i++) {
    const tk = toks[i];
    if (tk.text != null) { nodes.push(document.createTextNode(tk.text)); continue; }
    const c = kids[tk.n];
    if (!c || tk.close) return false;
    if (tk.self) {
      if (!want.has(`o${tk.n}`)) return false;
      got.add(`o${tk.n}`); nodes.push(c); continue;
    }
    if (!want.has(`p${tk.n}`)) return false;
    const hasText = toks[i + 1] && toks[i + 1].text != null;
    const close = toks[i + (hasText ? 2 : 1)];
    if (!close || close.n !== tk.n || !close.close) return false;
    c.textContent = hasText ? toks[i + 1].text : '';
    got.add(`p${tk.n}`); nodes.push(c);
    i += hasText ? 2 : 1;
  }
  if (got.size !== want.size) return false;
  // Keep the outer whitespace the English had (inline layouts depend on it).
  const first = el.firstChild, lastN = el.lastChild;
  if (first && first.nodeType === 3 && /^\s/.test(first.data)) nodes.unshift(document.createTextNode(' '));
  if (lastN && lastN.nodeType === 3 && /\s$/.test(lastN.data)) nodes.push(document.createTextNode(' '));
  el.replaceChildren(...nodes);
  return true;
}

const _ATTRS = ['title', 'placeholder', 'aria-label', 'alt'];

// Translate every data-i18n element under root. English needs no pass: the
// markup already is the English.
function applyI18n(root) {
  root = root || document;
  if (_LANG === 'en' || !Object.keys(_STR).length) return;
  root.querySelectorAll('[data-i18n]').forEach(el => {
    const tr = _STR[el.getAttribute('data-i18n')];
    if (typeof tr === 'string' && tr) _applyMessage(el, tr);
  });
  for (const a of _ATTRS) {
    root.querySelectorAll(`[data-i18n-${a}]`).forEach(el => {
      const tr = _STR[el.getAttribute(`data-i18n-${a}`)];
      if (typeof tr === 'string' && tr) el.setAttribute(a, tr);
    });
  }
}

// The Settings > Language menu: saves `ui_language` server-side (the page
// is rendered in the chosen language from the first byte) and reloads.
function i18nFillLanguageMenu() {
  const sel = document.getElementById('settingsLanguage');
  if (!sel) return;
  sel.replaceChildren();
  const auto = document.createElement('option');
  auto.value = 'auto';
  auto.textContent = T('settings.language.auto', 'Same as this Mac');
  sel.appendChild(auto);
  (_I18N.available || [{ code: 'en', name: 'English' }]).forEach(l => {
    const o = document.createElement('option');
    o.value = l.code;
    o.textContent = l.name;
    o.lang = l.code;
    sel.appendChild(o);
  });
  sel.value = _I18N.setting || 'auto';
  if (!sel.value) sel.value = 'auto';
}

async function i18nSetLanguage(code) {
  // url-encoded, like every /settings call (the route reads a form body).
  const body = new URLSearchParams({ ui_language: code || 'auto' });
  try {
    const r = await fetch('/settings', { method: 'POST', body });
    if (r.ok) location.reload();
  } catch (e) { /* the menu keeps its old value; nothing changed */ }
}

Object.assign(globalThis, { T, TA, TP, i18nLang, i18nAgo, i18nDir, applyI18n, i18nFillLanguageMenu, i18nSetLanguage });
