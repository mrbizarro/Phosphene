// webapp/js/whats_new.js - the once-per-version "What's new" window.
//
// After Phosphene updates, the first time the studio opens it shows ONE window: the release's big visual
// (hero: e.g. Make it vertical, the same clip widescreen, tall with a blurred fill, tall with AI fill), the
// lines for everyone, and a set-apart "New in Pro" panel. Escape, the X or a click outside closes it, and it
// is not shown again for that version (the server remembers: POST /whats-new/seen). The version pill reopens
// it (openWhatsNew(true)). "Earlier releases" pages back through every past entry inside the same window.
// Pro users see "New in Pro" first with a thank-you line and no sales buttons. Free users see the Pro panel
// with the measured "What Pro adds" item, then one "See Pro" and one "Support Phosphene" link. No countdown.

const WN = { el: null, data: null, retried: false, view: 'now', prevFocus: null };

function wnEsc(t) {
  return String(t == null ? '' : t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function wnReduced() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
}

function wnDate(d) {
  if (!d) return '';
  try {
    return new Date(d + 'T00:00:00Z').toLocaleDateString('en-US', {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'});
  } catch (e) { return d; }
}

// One muted looping clip (or a still). Reduced motion: poster with controls, nothing plays by itself.
function wnClip(src, poster, cls, alt) {
  if (!src) return '';
  if (/\.(mp4|webm|mov)$/i.test(src)) {
    const play = wnReduced() ? 'controls preload="none"' : 'autoplay loop preload="auto"';
    return `<video class="${cls}" src="${wnEsc(src)}"${poster ? ` poster="${wnEsc(poster)}"` : ''} muted playsinline ${play} aria-label="${wnEsc(alt || '')}"></video>`;
  }
  return `<img class="${cls}" src="${wnEsc(src)}" alt="${wnEsc(alt || '')}" loading="lazy">`;
}

// Every piece below sits in the one dialog shell the Studio's dialogs share (.dlg in panel.css): media in one
// frame (.dlg-media), captions in one style (.dlg-fig figcaption), section headings as .dlg-sec-h, the panel's
// own buttons. Pro is the one tag (.pro-tag) and the one violet (--pro-*).
const WN_PRO_URL = 'https://phosphene.video/pricing/#plans';

function wnFig(c, extra, title) {
  if (!c) return '';
  const cap = (c.label ? `<strong>${wnEsc(c.label)}</strong>` : '') + (c.caption ? `<span>${wnEsc(c.caption)}</span>` : '');
  return `<figure class="dlg-fig ${extra || ''}">${wnClip(c.video || c.image, c.poster, 'dlg-media', c.label || title)}`
    + (cap ? `<figcaption>${cap}</figcaption>` : '') + '</figure>';
}

function wnHero(h) {
  if (!h) return '';
  const title = h.title ? `<h3 class="wn-item-title">${wnEsc(h.title)}</h3>` : '';
  const cap = h.caption ? `<p class="dlg-lead">${wnEsc(h.caption)}</p>` : '';
  if (h.kind === 'vertical') {
    return `<section class="dlg-sec wn-hero wn-hero-vert">${title}${cap}<div class="wn-vert" data-wn-sync>`
      + wnFig(h.original, 'wn-vfig-wide')
      + '<span class="wn-varrow" aria-hidden="true">→</span>'
      + wnFig(h.free, 'wn-vfig-tall')
      + wnFig(h.pro, 'wn-vfig-tall dlg-fig-pro')
      + '</div></section>';
  }
  return `<section class="dlg-sec wn-hero">${title}${cap}${wnFig({video: h.video, image: h.image, poster: h.poster}, '', h.title)}</section>`;
}

// How-to clips: a few seconds of the real Studio per new feature, a Free take and a Pro take. A Pro member sees
// their own take first. The feature's own How it works viewer shows the same, without the section heading.
function wnHow(hows, proUser, bare) {
  if (!hows || !hows.length) return '';
  return hows.map(h => {
    const takes = proUser ? wnFig(h.pro, 'dlg-fig-pro', h.title) + wnFig(h.free, '', h.title)
      : wnFig(h.free, '', h.title) + wnFig(h.pro, 'dlg-fig-pro', h.title);
    return `<section class="dlg-sec wn-how">${bare ? '' : `<h3 class="dlg-sec-h">${wnEsc(h.title || 'How it works')}</h3>`}`
      + `<div class="wn-how-row">${takes}</div></section>`;
  }).join('');
}

function wnList(items) {
  if (!items || !items.length) return '';
  return '<ul class="wn-list">' + items.map(it => {
    const media = it.media ? wnFig({video: /\.(mp4|webm|mov)$/i.test(it.media) ? it.media : '', image: it.media, poster: it.poster}, '', it.text) : '';
    return `<li class="${it.media ? 'wn-has-media' : ''}"><span class="wn-text">${wnEsc(it.text)}</span>${media}</li>`;
  }).join('') + '</ul>';
}

function wnShowcase(items) {
  if (!items || !items.length) return '';
  return items.map(it => `<figure class="dlg-fig dlg-fig-pro">`
    + (it.media ? wnClip(it.media, it.poster, 'dlg-media', it.text) : '')
    + `<figcaption><strong>Measured on one Mac</strong><span>${wnEsc(it.text)}`
    + (it.link ? ` <a class="dlg-link" href="${wnEsc(it.link)}" data-utm-campaign="get-pro" data-utm-content="whats-new-speed" target="_blank" rel="noopener">How it was measured ↗</a>` : '')
    + '</span></figcaption></figure>').join('');
}

function wnNow(d) {
  const proUser = !!d.pro_active;
  const hasPro = (d.pro && d.pro.length) || (d.showcase && d.showcase.length);
  const proBlock = hasPro || proUser
    ? `<section class="dlg-sec wn-pro"><h3 class="dlg-sec-h">${wnEsc(d.pro_title)}</h3>`
      + (proUser && d.thanks ? `<p class="wn-thanks">${wnEsc(d.thanks)}</p>` : '')
      + wnList(d.pro)
      + (proUser ? '' : wnShowcase(d.showcase)
        + '<div class="wn-pro-ask"><p class="dlg-p">Phosphene stays free and open source. Pro gets new features and speed-ups first, and keeps the project going.</p>'
        + `<a class="ghost-btn" href="${wnEsc(d.pricing_url)}" data-utm-campaign="support" data-utm-content="whats-new" target="_blank" rel="noopener">Support Phosphene <span aria-hidden="true">↗</span></a></div>`)
      + '</section>'
    : '';
  const freeBlock = (d.free && d.free.length)
    ? `<section class="dlg-sec"><h3 class="dlg-sec-h">${wnEsc(d.free_title)}</h3>${wnList(d.free)}</section>`
    : '';
  return wnHero(d.hero) + wnHow(d.how, proUser) + (proUser ? proBlock + freeBlock : freeBlock + proBlock);
}

function wnEarlier(d) {
  const rows = (d.earlier || []).map(e => {
    const lines = (e.free || []).map(x => `<li>${wnEsc(x.text)}</li>`).join('')
      + (e.pro || []).map(x => `<li><span class="pro-tag is-lead">Pro</span><span>${wnEsc(x.text)}</span></li>`).join('');
    return `<li class="wn-rel"><div class="wn-rel-h"><strong>${wnEsc(e.version)}</strong>`
      + (e.date ? `<time datetime="${wnEsc(e.date)}">${wnEsc(wnDate(e.date))}</time>` : '') + '</div>'
      + (e.still ? `<img class="dlg-media" src="${wnEsc(e.still)}" alt="" loading="lazy">` : '')
      + `<ul>${lines}</ul></li>`;
  }).join('');
  return `<section class="dlg-sec wn-earlier"><h3 class="dlg-sec-h">Earlier releases</h3>`
    + (rows ? `<ol class="wn-rels">${rows}</ol>` : '<p class="wn-thanks">No earlier releases to show.</p>')
    + `<p class="wn-earlier-more"><a class="dlg-link" href="${wnEsc(d.changelog_url)}" data-utm-campaign="changelog" data-utm-content="whats-new" target="_blank" rel="noopener">Every release since the first, with pictures and clips ↗</a></p></section>`;
}

// The shell, the same for both windows this file opens: head (kicker, title, x), body, foot.
function wnShell(kicker, title, sub, body, footLeft, proUser, utm) {
  return `<header class="dlg-head"><div><span class="dlg-kicker">${wnEsc(kicker)}</span>`
    + `<h2 class="dlg-title" id="wnTitle">${wnEsc(title)}</h2>${sub ? `<p class="dlg-sub">${wnEsc(sub)}</p>` : ''}</div>`
    + '<button type="button" class="dlg-x" aria-label="Close" title="Close" data-wn-close><svg class="ph" aria-hidden="true"><use href="#ph-x"/></svg></button></header>'
    + `<div class="dlg-body">${body}</div>`
    + `<footer class="dlg-foot">${footLeft || ''}<div class="dlg-foot-r">`
    + '<button type="button" class="ghost-btn" data-wn-close>Close</button>'
    + (proUser ? '' : `<a class="primary" href="${WN_PRO_URL}" data-utm-campaign="get-pro" data-utm-content="${wnEsc(utm)}" target="_blank" rel="noopener">See Pro <span aria-hidden="true">↗</span></a>`)
    + '</div></footer>';
}

function wnRender(d) {
  const earlierBtn = (d.earlier || []).length || d.changelog_url
    ? `<button type="button" class="ghost-btn" data-wn-view>${WN.view === 'now' ? 'Show earlier releases' : 'Back to ' + wnEsc(d.version)}</button>`
    : '';
  return wnShell("What's new", 'Phosphene ' + d.version, d.date ? 'Released ' + wnDate(d.date) : '',
    WN.view === 'now' ? wnNow(d) : wnEarlier(d), earlierBtn, !!d.pro_active, 'whats-new-footer');
}

// The three Make it vertical clips are the same 5 seconds: start them together so the frames line up.
function wnSync(root) {
  root.querySelectorAll('[data-wn-sync]').forEach(box => {
    const vids = Array.from(box.querySelectorAll('video'));
    if (vids.length < 2 || wnReduced()) return;
    let ready = 0;
    const go = () => { vids.forEach(v => { try { v.currentTime = 0; v.play().catch(() => {}); } catch (e) {} }); };
    vids.forEach(v => {
      v.pause();
      const once = () => { ready += 1; if (ready === vids.length) go(); };
      if (v.readyState >= 3) once(); else v.addEventListener('canplay', once, {once: true});
      // the shortest clip restarts all three, so a slow decoder never drifts for long
    });
    vids[0].addEventListener('ended', go);
    vids[0].loop = false;
  });
}

function wnPaint() {
  const box = WN.el && WN.el.querySelector('.dlg');
  if (!box) return;
  box.innerHTML = wnRender(WN.data);
  wnSync(box);
  try { box.querySelectorAll('a[data-utm-campaign]').forEach(a => { if (typeof siteLinkApply === 'function') siteLinkApply(a); }); } catch (e) {}
}

function wnMarkSeen() {
  const d = WN.data;
  if (d && d.version && !d._seen) {
    d._seen = true;
    fetch('/whats-new/seen', {method: 'POST', body: new URLSearchParams({version: d.version})}).catch(() => {});
  }
}

function wnKey(e) {
  if (e.key === 'Escape') { e.preventDefault(); wnClose(); }
}

function wnClose() {
  if (WN.el) { WN.el.remove(); WN.el = null; }
  document.removeEventListener('keydown', wnKey, true);
  window._phModalOpen = false;
  wnMarkSeen();
  try { if (WN.prevFocus && WN.prevFocus.focus) WN.prevFocus.focus(); } catch (e) {}
}

async function openWhatsNew(force) {
  if (WN.el) return;
  let d;
  try {
    const r = await fetch('/whats-new' + (force ? '?force=1' : ''), {cache: 'no-store'});
    d = await r.json();
  } catch (e) { return; }
  if (!d || !d.show) {
    if (force && typeof phosToast === 'function') phosToast('Nothing new to show for this version.', 'info');
    return;
  }
  WN.data = d;
  WN.view = 'now';
  WN.prevFocus = document.activeElement;
  const el = document.createElement('div');
  el.id = 'whatsNewCard';
  el.className = 'models-modal wn-wrap';
  el.innerHTML = '<div class="models-card dlg dlg-lg" role="dialog" aria-modal="true" aria-labelledby="wnTitle" tabindex="-1"></div>';
  el.addEventListener('click', e => {
    if (e.target === el || e.target.closest('[data-wn-close]')) { wnClose(); return; }
    if (e.target.closest('[data-wn-view]')) {
      WN.view = WN.view === 'now' ? 'earlier' : 'now';
      wnPaint();
      const b = el.querySelector('.dlg-body'); if (b) b.scrollTop = 0;
      const t = el.querySelector('[data-wn-view]'); if (t) t.focus();
      return;
    }
    if (e.target.closest('a[href]')) wnMarkSeen();   // a link opened the site: this version counts as read
  });
  document.body.appendChild(el);
  WN.el = el;
  window._phModalOpen = true;
  document.addEventListener('keydown', wnKey, true);
  wnPaint();
  const dlg = el.querySelector('.dlg'); if (dlg) dlg.focus({preventScroll: true});
}

// A feature's own "How it works" link: the same window, showing only that feature's how-to clip.
async function openHowClip(id) {
  if (WN.el) wnClose();
  let h = null;
  try { h = (await (await fetch('/whats-new/how?id=' + encodeURIComponent(id), {cache: 'no-store'})).json()).how; } catch (e) { h = null; }
  if (!h) { if (typeof phosToast === 'function') phosToast('No how-to clip for this yet.', 'info'); return; }
  const proUser = !!document.documentElement.dataset.pro;
  WN.data = null;
  WN.prevFocus = document.activeElement;
  const el = document.createElement('div');
  el.id = 'whatsNewCard';
  el.className = 'models-modal wn-wrap';
  const name = String(h.title || '').replace(/^How (.+) works$/, '$1');   // "How Make it vertical works" -> "Make it vertical"
  el.innerHTML = '<div class="models-card dlg dlg-lg" role="dialog" aria-modal="true" aria-labelledby="wnTitle" tabindex="-1">'
    + wnShell('How it works', name, '', wnHow([h], proUser, true), '', proUser, 'how-' + id) + '</div>';
  el.addEventListener('click', e => { if (e.target === el || e.target.closest('[data-wn-close]')) wnClose(); });
  document.body.appendChild(el);
  WN.el = el;
  window._phModalOpen = true;
  document.addEventListener('keydown', wnKey, true);
  wnSync(el);
  try { el.querySelectorAll('a[data-utm-campaign]').forEach(a => { if (typeof siteLinkApply === 'function') siteLinkApply(a); }); } catch (e) {}
  const dlg = el.querySelector('.dlg'); if (dlg) dlg.focus({preventScroll: true});
}

function wnAuto() {
  // Not on top of another dialog: try once more a little later, then leave it to the version pill.
  if (window._phModalOpen) {
    if (!WN.retried) { WN.retried = true; setTimeout(wnAuto, 25000); }
    return;
  }
  openWhatsNew(false);
}
setTimeout(wnAuto, 3500);
Object.assign(globalThis, { openWhatsNew, wnClose, openHowClip });
