// THE DOCS — the manual, inside the panel.
//
// Why a view and not a page: the panel is one window (Pinokio's), the Docs
// have to open over whatever you are doing and close back onto it with Esc,
// and the Keyboard shortcuts page has to render from the SAME registry the
// key handlers read (webapp/js/shortcuts.js) — which only exists in this
// document. So the Docs are a full-height view in the panel's own chrome,
// deep-linkable by hash: #docs/<section>/<anchor>.
//
// The words live in webapp/docs/*.md — plain Markdown an engineer edits
// without a build step — and this file renders them with a small renderer
// that knows exactly what the pages use: headings (with {#anchor}), lists,
// tables, notes (>), code, links, and three directives:
//
//   [[shortcuts]] / [[shortcuts:editor,editor-mouse]]   the registry's table
//   [[sc:editor.split]]                                 one shortcut's keys
//   [[icon:ph-trash-simple]]                            a panel icon
//
// Served from /webapp/docs/ by the panel itself: no network, no CDN.

const DOCS_SECTIONS = [
  { id: 'getting-started', title: 'Getting started', group: 'Start', file: 'getting-started.md' },
  { id: 'video', title: 'Video', group: 'Make', file: 'video.md' },
  { id: 'one-shot', title: 'One Shot', group: 'Make', file: 'one-shot.md' },
  { id: 'remix', title: 'Remix', group: 'Make', file: 'remix.md' },
  { id: 'images', title: 'Images', group: 'Make', file: 'images.md' },
  { id: 'storyboard', title: 'Storyboard', group: 'Make', file: 'storyboard.md' },
  { id: 'audio', title: 'Audio', group: 'Make', file: 'audio.md' },
  { id: 'editor', title: 'Editor', group: 'Cut', file: 'editor.md' },
  { id: 'train-character', title: 'Train Character', group: 'Characters', file: 'train-character.md' },
  { id: 'loras', title: 'LoRAs', group: 'Characters', file: 'loras.md' },
  { id: 'settings', title: 'Settings', group: 'Reference', file: 'settings.md' },
  { id: 'shortcuts', title: 'Keyboard shortcuts', group: 'Reference', file: 'shortcuts.md' },
  { id: 'buttons', title: 'Buttons and icons', group: 'Reference', file: 'buttons.md' },
  { id: 'troubleshooting', title: 'Troubleshooting', group: 'Reference', file: 'troubleshooting.md' },
];

const DOCS = {
  md: {}, rendered: {}, index: [], loading: null,
  section: 'getting-started', returnFocus: null, spyRaf: 0,
};

// ---- rendering --------------------------------------------------------------
function docsEsc(t) {
  return String(t).replace(/[&<>"]/g, ch => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[ch]));
}

function docsSlug(t) {
  return String(t).toLowerCase()
    .replace(/\[\[[^\]]*\]\]/g, '').replace(/[`*_]/g, '')
    .replace(/[^a-z0-9\s-]/g, '').trim().replace(/\s+/g, '-').replace(/-+/g, '-');
}

// What a heading or a line SAYS, without its markup — for search and the TOC.
function docsPlain(t) {
  return String(t)
    .replace(/\[\[sc:([\w.-]+)\]\]/g, (m, id) => (typeof shortcutHint === 'function' ? shortcutHint(id) : ''))
    .replace(/\[\[[^\]]*\]\]/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/<\/?kbd>/g, '')
    .replace(/[`*]/g, '')
    .replace(/\s+/g, ' ').trim();
}

function docsInline(raw) {
  const codes = [];
  let s = String(raw).replace(/`([^`]+)`/g, (m, c) => { codes.push(c); return '\u0000' + (codes.length - 1) + '\u0000'; });
  s = docsEsc(s);
  s = s.replace(/\[\[sc:([\w.-]+)\]\]/g, (m, id) => {
    const e = (typeof shortcutById === 'function') ? shortcutById(id) : null;
    return e ? '<span class="docs-sc">' + shortcutKeysHtml(e, 'kbd') + '</span>'
             : '<span class="docs-missing">' + docsEsc(id) + '</span>';
  });
  s = s.replace(/\[\[icon:([\w-]+)\]\]/g,
    (m, id) => '<svg class="ph docs-icon" aria-hidden="true"><use href="#' + id + '"/></svg>');
  s = s.replace(/&lt;kbd&gt;(.*?)&lt;\/kbd&gt;/g, '<kbd class="kbd">$1</kbd>');
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (m, text, href) => {
    const h = href.replace(/&amp;/g, '&');
    if (/^#docs\//.test(h)) return '<a href="' + docsEsc(h) + '" class="docs-xref">' + text + '</a>';
    if (/^https:\/\//.test(h)) return '<a href="' + docsEsc(h) + '" target="_blank" rel="noopener">' + text + '</a>';
    return text;
  });
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[\s(])\*([^*\s][^*]*)\*/g, '$1<em>$2</em>');
  s = s.replace(/\u0000(\d+)\u0000/g, (m, i) => '<code>' + docsEsc(codes[Number(i)]) + '</code>');
  return s;
}

function docsTable(rows) {
  const cells = r => r.trim().replace(/^\|/, '').replace(/\|\s*$/, '').split('|').map(c => c.trim());
  const head = cells(rows[0]);
  let body = rows.slice(1);
  if (body.length && /^[\s|:-]+$/.test(body[0])) body = body.slice(1);
  return '<div class="docs-table-wrap"><table class="docs-table"><thead><tr>' +
    head.map(h => '<th>' + docsInline(h) + '</th>').join('') + '</tr></thead><tbody>' +
    body.map(r => '<tr>' + cells(r).map(c => '<td>' + docsInline(c) + '</td>').join('') + '</tr>').join('') +
    '</tbody></table></div>';
}

const DOCS_HEADING = /^(#{1,3})\s+(.*?)(?:\s+\{#([\w-]+)\})?\s*$/;
const DOCS_SC_BLOCK = /^\[\[shortcuts(?::([\w,-]+))?\]\]\s*$/;

// md → { html, headings: [{level, text, anchor}] }. Pure: the tests run it.
function docsRender(md, sectionId) {
  const lines = String(md).replace(/\r\n?/g, '\n').split('\n');
  const out = [];
  const heads = [];
  let para = [];
  let list = null;
  const flushPara = () => {
    if (para.length) { out.push('<p>' + docsInline(para.join(' ')) + '</p>'); para = []; }
  };
  const renderList = l => '<' + l.type + '>' + l.items.map(it =>
    '<li>' + docsInline(it.text) + (it.sub ? renderList(it.sub) : '') + '</li>').join('') + '</' + l.type + '>';
  const flushList = () => { if (list) { out.push(renderList(list)); list = null; } };
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (/^```/.test(line)) {
      flushPara(); flushList();
      const buf = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) buf.push(lines[i++]);
      i++;
      out.push('<pre class="docs-pre"><code>' + docsEsc(buf.join('\n')) + '</code></pre>');
      continue;
    }
    if (!line.trim()) { flushPara(); flushList(); i++; continue; }
    if ((m = line.match(DOCS_HEADING))) {
      flushPara(); flushList();
      const level = m[1].length;
      const text = m[2];
      if (level === 1) {
        out.push('<h1 id="docs-' + sectionId + '">' + docsInline(text) + '</h1>');
      } else {
        const anchor = m[3] || docsSlug(text);
        heads.push({ level, text: docsPlain(text), anchor });
        out.push('<h' + level + ' id="docs-' + sectionId + '-' + anchor + '">' + docsInline(text) +
          '<a class="docs-anchor" href="#docs/' + sectionId + '/' + anchor + '" aria-label="Link to this part">#</a></h' + level + '>');
      }
      i++; continue;
    }
    if ((m = line.match(DOCS_SC_BLOCK))) {
      flushPara(); flushList();
      const scopes = m[1] ? m[1].split(',') : null;
      if (typeof shortcutsDocHtml === 'function') {
        out.push(shortcutsDocHtml(scopes, 'docs-' + sectionId + '-'));
        for (const sc of SHORTCUT_SCOPES) {
          if (!scopes || scopes.includes(sc.id)) heads.push({ level: 3, text: sc.title, anchor: 'keys-' + sc.id });
        }
      }
      i++; continue;
    }
    if (/^\s*\|/.test(line)) {
      flushPara(); flushList();
      const rows = [];
      while (i < lines.length && /^\s*\|/.test(lines[i])) rows.push(lines[i++]);
      out.push(docsTable(rows));
      continue;
    }
    if (/^>\s?/.test(line)) {
      flushPara(); flushList();
      const buf = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\s?/, ''));
      out.push('<div class="docs-note">' + buf.join('\n').split(/\n\s*\n/)
        .map(p => '<p>' + docsInline(p.replace(/\n/g, ' ')) + '</p>').join('') + '</div>');
      continue;
    }
    if ((m = line.match(/^(\s*)([-*]|\d+\.)\s+(.*)$/))) {
      flushPara();
      const type = /\d/.test(m[2]) ? 'ol' : 'ul';
      if (m[1].length >= 2 && list && list.items.length) {
        const last = list.items[list.items.length - 1];
        if (!last.sub) last.sub = { type, items: [] };
        last.sub.items.push({ text: m[3] });
      } else {
        if (list && list.type !== type) flushList();
        if (!list) list = { type, items: [] };
        list.items.push({ text: m[3] });
      }
      i++; continue;
    }
    if (list && /^\s{2,}\S/.test(line)) {
      const last = list.items[list.items.length - 1];
      const tgt = last.sub ? last.sub.items[last.sub.items.length - 1] : last;
      tgt.text += ' ' + line.trim();
      i++; continue;
    }
    if (/^---+\s*$/.test(line)) { flushPara(); flushList(); out.push('<hr>'); i++; continue; }
    flushList();
    para.push(line.trim());
    i++;
  }
  flushPara(); flushList();
  return { html: out.join('\n'), headings: heads };
}

// ---- search -----------------------------------------------------------------
// One entry per heading: its words and the words under it, until the next.
function docsBuildIndex() {
  const idx = [];
  for (const sec of DOCS_SECTIONS) {
    const md = DOCS.md[sec.id] || '';
    let cur = { section: sec.id, anchor: '', title: sec.title, level: 1, text: [] };
    const push = () => { if (cur) idx.push({ section: cur.section, anchor: cur.anchor, title: cur.title, level: cur.level, text: cur.text.join(' ') }); };
    let inCode = false;
    for (const line of md.split('\n')) {
      if (/^```/.test(line)) { inCode = !inCode; continue; }
      const h = !inCode && line.match(DOCS_HEADING);
      if (h) {
        if (h[1].length === 1) continue;
        push();
        cur = { section: sec.id, anchor: h[3] || docsSlug(h[2]), title: docsPlain(h[2]), level: h[1].length, text: [] };
        continue;
      }
      const sc = !inCode && line.match(DOCS_SC_BLOCK);
      if (sc && typeof SHORTCUT_SCOPES !== 'undefined') {
        const scopes = sc[1] ? sc[1].split(',') : SHORTCUT_SCOPES.map(x => x.id);
        for (const scope of SHORTCUT_SCOPES) {
          if (!scopes.includes(scope.id)) continue;
          idx.push({ section: sec.id, anchor: 'keys-' + scope.id, title: scope.title, level: 3,
            text: SHORTCUTS.filter(s => s.scope === scope.id)
              .map(s => shortcutHint(s.id) + ' ' + s.label).join(' · ') });
        }
        continue;
      }
      cur.text.push(docsPlain(line.replace(/^\s*(?:[-*>]|\d+\.)\s+/, '').replace(/\|/g, ' ')));
    }
    push();
  }
  DOCS.index = idx.filter(e => e.title);
}

function docsSearch(q) {
  const terms = String(q || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) return [];
  const hits = [];
  for (const e of DOCS.index) {
    const title = e.title.toLowerCase();
    const text = e.text.toLowerCase();
    const sec = (DOCS_SECTIONS.find(s => s.id === e.section) || {}).title || '';
    let score = 0;
    let ok = true;
    for (const t of terms) {
      if (title.includes(t)) score += 10;
      else if (text.includes(t)) score += 2;
      else if (sec.toLowerCase().includes(t)) score += 1;
      else { ok = false; break; }
    }
    if (ok) hits.push({ e, score: score - e.level * 0.1 });
  }
  hits.sort((a, b) => b.score - a.score);
  return hits.slice(0, 40).map(h => h.e);
}

function docsSnippet(text, terms) {
  const low = text.toLowerCase();
  let at = -1;
  for (const t of terms) { const k = low.indexOf(t); if (k >= 0 && (at < 0 || k < at)) at = k; }
  let s = at < 0 ? text.slice(0, 140) : text.slice(Math.max(0, at - 50), at + 110);
  if (at > 50) s = '…' + s;
  let html = docsEsc(s);
  for (const t of terms) {
    if (!t) continue;
    const re = new RegExp('(' + docsEsc(t).replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
    html = html.replace(re, '<mark>$1</mark>');
  }
  return html;
}

// ---- the view -----------------------------------------------------------------
function docsEl(id) { return document.getElementById(id); }

async function docsLoad() {
  if (DOCS.loading) return DOCS.loading;
  DOCS.loading = Promise.all(DOCS_SECTIONS.map(async sec => {
    try {
      const r = await fetch('/webapp/docs/' + sec.file, { cache: 'no-cache' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      DOCS.md[sec.id] = await r.text();
    } catch (e) {
      DOCS.md[sec.id] = '# ' + sec.title + '\n\nThis page could not be loaded (' + String(e.message || e) +
        '). The panel serves it from webapp/docs/' + sec.file + '.';
    }
  })).then(() => { docsBuildIndex(); });
  return DOCS.loading;
}

function docsRendered(id) {
  if (!DOCS.rendered[id]) DOCS.rendered[id] = docsRender(DOCS.md[id] || '', id);
  return DOCS.rendered[id];
}

function docsPaintToc() {
  const toc = docsEl('docsToc');
  if (!toc) return;
  let html = '';
  let group = '';
  for (const sec of DOCS_SECTIONS) {
    if (sec.group !== group) {
      group = sec.group;
      html += '<div class="docs-toc-group">' + docsEsc(group) + '</div>';
    }
    const on = sec.id === DOCS.section;
    html += '<a class="docs-toc-sec' + (on ? ' is-active' : '') + '" href="#docs/' + sec.id + '"' +
      (on ? ' aria-current="page"' : '') + '>' + docsEsc(sec.title) + '</a>';
    if (on) {
      const subs = docsRendered(sec.id).headings.filter(h => h.level === 2);
      if (subs.length) {
        html += '<div class="docs-toc-subs">' + subs.map(h =>
          '<a class="docs-toc-sub" data-anchor="' + docsEsc(h.anchor) + '" href="#docs/' + sec.id + '/' + h.anchor + '">' +
          docsEsc(h.text) + '</a>').join('') + '</div>';
      }
    }
  }
  toc.innerHTML = html;
}

function docsGo(section, anchor) {
  const sec = DOCS_SECTIONS.find(s => s.id === section) || DOCS_SECTIONS[0];
  const changed = sec.id !== DOCS.section || !docsEl('docsArticle').dataset.section;
  DOCS.section = sec.id;
  const art = docsEl('docsArticle');
  if (changed) {
    art.innerHTML = docsRendered(sec.id).html;
    art.dataset.section = sec.id;
    const i = DOCS_SECTIONS.indexOf(sec);
    const prev = DOCS_SECTIONS[i - 1];
    const next = DOCS_SECTIONS[i + 1];
    docsEl('docsPageNav').innerHTML =
      (prev ? '<a class="docs-page-prev" href="#docs/' + prev.id + '"><span>Previous</span>' + docsEsc(prev.title) + '</a>' : '<span></span>') +
      (next ? '<a class="docs-page-next" href="#docs/' + next.id + '"><span>Next</span>' + docsEsc(next.title) + '</a>' : '<span></span>');
    docsPaintToc();
  }
  docsEl('docsCrumb').textContent = sec.group + '  ›  ' + sec.title;
  const scroller = docsEl('docsScroll');
  const target = anchor ? docsEl('docs-' + sec.id + '-' + anchor) : null;
  requestAnimationFrame(() => {
    if (target) {
      scroller.scrollTop += target.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 12;
      target.classList.remove('docs-flash');
      void target.offsetWidth;
      target.classList.add('docs-flash');
    } else if (changed) {
      scroller.scrollTop = 0;
    }
    docsSpy();
  });
  try {
    const want = '#docs/' + sec.id + (anchor ? '/' + anchor : '');
    if (location.hash !== want) history.replaceState(null, '', want);
  } catch (e) {}
  docsEl('docsView').classList.remove('nav-open');
}

// The TOC follows the reading position.
function docsSpy() {
  const scroller = docsEl('docsScroll');
  const toc = docsEl('docsToc');
  if (!scroller || !toc) return;
  const top = scroller.getBoundingClientRect().top + 90;
  let current = '';
  for (const h of docsEl('docsArticle').querySelectorAll('h2[id]')) {
    if (h.getBoundingClientRect().top <= top) current = h.id.slice(('docs-' + DOCS.section + '-').length);
  }
  toc.querySelectorAll('.docs-toc-sub').forEach(a => a.classList.toggle('is-here', a.dataset.anchor === current));
}

function docsPaintResults(q) {
  const box = docsEl('docsResults');
  const toc = docsEl('docsToc');
  const terms = String(q || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!terms.length) { box.hidden = true; toc.hidden = false; box.innerHTML = ''; return; }
  const hits = docsSearch(q);
  toc.hidden = true;
  box.hidden = false;
  if (!hits.length) {
    box.innerHTML = '<div class="docs-noresult">Nothing matches “' + docsEsc(q) + '”. Try a word you see on the button, like <b>unlink</b> or <b>upscale</b>.</div>';
    return;
  }
  box.innerHTML = '<div class="docs-toc-group">' + hits.length + (hits.length === 1 ? ' match' : ' matches') + '</div>' +
    hits.map((e, n) => {
      const sec = DOCS_SECTIONS.find(s => s.id === e.section);
      return '<a class="docs-hit" href="#docs/' + e.section + (e.anchor ? '/' + e.anchor : '') + '" data-n="' + n + '">' +
        '<span class="docs-hit-t">' + docsSnippet(e.title, terms).replace(/^…/, '') + '</span>' +
        '<span class="docs-hit-s">' + docsEsc(sec ? sec.title : '') + '</span>' +
        (e.text ? '<span class="docs-hit-x">' + docsSnippet(e.text, terms) + '</span>' : '') + '</a>';
    }).join('');
}

function docsParseHash(h) {
  const m = String(h || '').match(/^#docs(?:\/([\w-]+))?(?:\/([\w-]+))?/);
  return m ? { section: m[1] || '', anchor: m[2] || '' } : null;
}

async function openDocs(section, anchor) {
  const view = docsEl('docsView');
  if (!view) return;
  const wasOpen = view.classList.contains('show');
  if (!wasOpen) {
    DOCS.returnFocus = document.activeElement;
    view.classList.add('show');
    view.style.display = 'flex';
    document.body.classList.add('modal-open');
  }
  await docsLoad();
  const known = DOCS_SECTIONS.some(s => s.id === section);
  docsGo(known ? section : DOCS.section, known ? (anchor || '') : '');
  if (!wasOpen) {
    const s = docsEl('docsSearch');
    if (s) setTimeout(() => { try { s.focus({ preventScroll: true }); } catch (e) {} }, 0);
  }
}

function closeDocs() {
  const view = docsEl('docsView');
  if (!view || !view.classList.contains('show')) return;
  view.classList.remove('show', 'nav-open');
  view.style.display = '';
  if (!document.querySelector('.modal-bg.show')) document.body.classList.remove('modal-open');
  try {
    if (/^#docs/.test(location.hash)) history.replaceState(null, '', location.pathname + location.search);
  } catch (e) {}
  const back = DOCS.returnFocus;
  DOCS.returnFocus = null;
  if (back && back.focus && document.contains(back)) { try { back.focus({ preventScroll: true }); } catch (e) {} }
}

(function docsWire() {
  const view = docsEl('docsView');
  if (!view || !view.addEventListener) return;
  // Every #docs/ link inside the view — TOC, results, cross-references,
  // prev / next, heading anchors — goes through docsGo, so clicking the link
  // you are already on still scrolls to it.
  view.addEventListener('click', ev => {
    const a = ev.target.closest && ev.target.closest('a[href^="#docs"]');
    if (!a) return;
    const p = docsParseHash(a.getAttribute('href'));
    if (!p) return;
    ev.preventDefault();
    docsGo(p.section || DOCS.section, p.anchor);
  });
  const search = docsEl('docsSearch');
  if (search) {
    search.addEventListener('input', () => docsPaintResults(search.value));
    search.addEventListener('keydown', ev => {
      if (ev.key === 'Enter') {
        const first = docsEl('docsResults').querySelector('.docs-hit');
        if (first) { ev.preventDefault(); first.click(); }
      } else if (ev.key === 'ArrowDown') {
        const first = docsEl('docsResults').querySelector('.docs-hit');
        if (first) { ev.preventDefault(); first.focus(); }
      } else if (ev.key === 'Escape' && search.value) {
        // The first Esc empties the box; the next one closes the Docs.
        ev.preventDefault();
        ev.stopPropagation();
        search.value = '';
        docsPaintResults('');
      }
    });
  }
  const results = docsEl('docsResults');
  if (results) {
    results.addEventListener('keydown', ev => {
      if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
      const hits = Array.from(results.querySelectorAll('.docs-hit'));
      const i = hits.indexOf(document.activeElement);
      if (i < 0) return;
      ev.preventDefault();
      if (ev.key === 'ArrowUp' && i === 0) { search.focus(); return; }
      const next = hits[Math.max(0, Math.min(hits.length - 1, i + (ev.key === 'ArrowDown' ? 1 : -1)))];
      if (next) next.focus();
    });
  }
  const scroller = docsEl('docsScroll');
  if (scroller) {
    scroller.addEventListener('scroll', () => {
      if (DOCS.spyRaf) return;
      DOCS.spyRaf = requestAnimationFrame(() => { DOCS.spyRaf = 0; docsSpy(); });
    }, { passive: true });
  }
  const toggle = docsEl('docsNavToggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      const on = !view.classList.contains('nav-open');
      view.classList.toggle('nav-open', on);
      toggle.setAttribute('aria-expanded', on ? 'true' : 'false');
    });
  }
  // A #docs/... link anywhere in the panel — an error message, a tooltip's
  // "read more", a pasted URL — opens the Docs there.
  if (typeof window.addEventListener === 'function') {
    window.addEventListener('hashchange', () => {
      const p = docsParseHash(location.hash);
      if (p) openDocs(p.section, p.anchor);
    });
  }
  const p = (typeof location !== 'undefined') ? docsParseHash(location.hash) : null;
  if (p) openDocs(p.section, p.anchor);
})();

// ---- published to the page -----------------------------------------------------
Object.assign(globalThis, {
  DOCS_SECTIONS, openDocs, closeDocs, docsRender, docsSlug, docsPlain,
  docsSearch, docsBuildIndex, docsLoad,
});
