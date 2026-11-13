// webapp/js/whats_new.js - the once-per-version "What's new" window and the feature "How it works" viewer.
//
// ONE FIXED FORMAT (5.0.20). The owner, 12 Nov, on the 5.0.19 window: empty boxes, small off-centre clips, no
// structure, it read as a debug page. Every release now reads the same, top to bottom, from whats_new.json:
//   1. header: the version, the date, the release's one-line "headline"
//   2. the hero, when the release has one
//   3. New for everyone   (only when the release has something for everyone)
//   4. New in Pro         (only when the release adds something to Pro: the owner, 12 Nov, "if there is nothing
//                          new in Pro, then we shouldn't have this written in there". A member reads a thank-you
//                          and what their licence added; a Free user reads the same lines, the measured "What Pro
//                          adds" item and one quiet support link)
//   5. How it works       (one card per new feature: the clip sized to its real shape, its words beside or under)
// Footer: "Earlier releases" on the left, Close on the right. Earlier releases page back in the same window and
// draw each release with the same pieces, so an old release reads like a new one. The server sends every clip's
// real width and height (read from its poster), so frames are the clip's own shape before a byte of video loads.
//
// Shown once per version (POST /whats-new/seen when it closes); the version pill reopens it (openWhatsNew(true)).
// Escape, the X, Close or a click outside closes it. Clips play only while on screen; reduced motion: none plays.

const WN = { el: null, data: null, retried: false, view: 'now', prevFocus: null, io: null };
const WN_ASK = 'Phosphene stays free and open source. Pro gets new features and speed-ups first, and keeps the project going.';

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

const wnIsVideo = src => /\.(mp4|webm|mov)$/i.test(src || '');

// "How editing with X works" -> "Editing with X": the card title is the feature, the section already says how.
function wnFeatureName(t) {
  const m = String(t || '').match(/^How (.+?) works$/i);
  if (!m) return String(t || '');
  return m[1].charAt(0).toUpperCase() + m[1].slice(1);
}

// The shape of one piece of media: tall (portrait or square-ish), wide (landscape) or very wide (a strip).
function wnShape(c) {
  const ar = c && c.w && c.h ? c.w / c.h : 16 / 9;
  return {ar, kind: ar < 1.2 ? 'tall' : (ar > 2.2 ? 'strip' : 'wide')};
}

// One muted clip or still, framed at its own aspect ratio (--ar), so there is never an empty box around it.
// Videos carry data-wn-play and no autoplay: the observer below plays them while they are on screen.
function wnMedia(c, alt, extra) {
  const src = c && (c.video || c.image || c.media);
  if (!src) return '';
  const {ar, kind} = wnShape(c);
  const style = ` style="--ar:${ar.toFixed(4)}"`;
  const dims = c.w && c.h ? ` width="${c.w}" height="${c.h}"` : '';
  const cls = `wn-m wn-m-${kind}${extra ? ' ' + extra : ''}`;
  if (wnIsVideo(src)) {
    const play = wnReduced() ? ' controls preload="none"' : ' loop preload="metadata" data-wn-play';
    return `<video class="${cls}"${style}${dims} src="${wnEsc(src)}"${c.poster ? ` poster="${wnEsc(c.poster)}"` : ''}`
      + ` muted playsinline${play} aria-label="${wnEsc(alt || '')}"></video>`;
  }
  return `<img class="${cls}"${style}${dims} src="${wnEsc(src)}" alt="${wnEsc(alt || '')}" loading="lazy" decoding="async">`;
}

// ---- 2. hero ----------------------------------------------------------------------------------------------------
function wnHeroFig(c, pro) {
  if (!c) return '';
  return `<figure class="wn-hfig">${wnMedia(c, c.label)}`
    + (c.label ? `<figcaption>${pro ? '<span class="pro-tag is-lead">Pro</span>' : ''}${wnEsc(String(c.label).replace(/^Pro:\s*/, ''))}</figcaption>` : '')
    + '</figure>';
}

function wnHero(h) {
  if (!h) return '';
  const head = (h.title ? `<h3 class="wn-hero-t">${wnEsc(h.title)}</h3>` : '')
    + (h.caption ? `<p class="wn-hero-p">${wnEsc(h.caption)}</p>` : '');
  if (h.kind === 'vertical') {
    return `<section class="wn-hero" aria-label="${wnEsc(h.title || 'Highlight')}">${head}`
      + `<div class="wn-hero-vert" data-wn-sync>${wnHeroFig(h.original)}`
      + '<span class="wn-hero-arrow" aria-hidden="true">→</span>'
      + wnHeroFig(h.free) + wnHeroFig(h.pro, true) + '</div></section>';
  }
  return `<section class="wn-hero" aria-label="${wnEsc(h.title || 'Highlight')}">${head}`
    + `<figure class="wn-hfig wn-hfig-one">${wnMedia(h, h.title)}</figure></section>`;
}

// ---- 3 and 4. the two lists ----------------------------------------------------------------------------------------
function wnLines(items) {
  if (!items || !items.length) return '';
  return '<ul class="wn-lines">' + items.map(it => {
    const media = it.media ? `<div class="wn-line-media">${wnMedia(it, it.text)}</div>` : '';
    return `<li${it.media ? ' class="has-media"' : ''}><span class="wn-line-t">${wnEsc(it.text)}</span>${media}</li>`;
  }).join('') + '</ul>';
}

function wnSecHead(title, mod, count) {
  return `<div class="wn-sec-h"><span class="wn-mark ${mod}" aria-hidden="true"></span><h3>${wnEsc(title)}</h3>`
    + (count ? `<span class="wn-count">${count}</span>` : '') + '</div>';
}

// The measured "What Pro adds" items: Free users only, in the Pro section, once per version (the window is).
function wnShowcase(items) {
  if (!items || !items.length) return '';
  return items.map(it => `<figure class="wn-measured">`
    + (it.media ? wnMedia(it, it.text) : '')
    + `<figcaption><span class="wn-measured-k">Measured on one Mac</span><span>${wnEsc(it.text)}`
    + (it.link ? ` <a class="dlg-link" href="${wnEsc(it.link)}" data-utm-campaign="get-pro" data-utm-content="whats-new-speed" target="_blank" rel="noopener">How it was measured <span aria-hidden="true">↗</span></a>` : '')
    + '</span></figcaption></figure>').join('');
}

// A section that has nothing in it is not drawn: no "New in Pro" heading over an empty box.
function wnFreeSec(r) {
  if (!(r.free && r.free.length)) return '';
  return `<section class="wn-sec wn-sec-free">${wnSecHead(r.free_title || 'New for everyone', 'is-free', (r.free || []).length)}`
    + wnLines(r.free) + '</section>';
}

// opts.full: the current release (thank-you, showcase, support link). Earlier releases show the lines only.
function wnProSec(r, proUser, opts) {
  if (!(r.pro && r.pro.length)) return '';
  const full = opts && opts.full;
  let body = '';
  if (full && proUser && r.thanks) body += `<p class="wn-pro-thanks">${wnEsc(r.thanks)}</p>`;
  body += wnLines(r.pro);
  if (full && !proUser) {
    body += wnShowcase(r.showcase)
      + `<div class="wn-ask"><p>${wnEsc(WN_ASK)}</p>`
      + `<a class="wn-ask-link" href="${wnEsc(r.pricing_url || 'https://phosphene.video/pricing/')}" data-utm-campaign="support" data-utm-content="whats-new" target="_blank" rel="noopener">Support Phosphene <span aria-hidden="true">↗</span></a></div>`;
  }
  return `<section class="wn-sec wn-sec-pro">${wnSecHead(r.pro_title || 'New in Pro', 'is-pro', (r.pro || []).length)}${body}</section>`;
}

// ---- 5. how it works --------------------------------------------------------------------------------------------
function wnTake(c, pro, title, proUser, heading) {
  if (!c) return '';
  const label = String(c.label || (pro ? 'Pro' : '')).replace(/^Pro:\s*/, '').replace(/^Pro$/, 'With Pro');
  const link = pro && !proUser
    ? ` <a class="dlg-link" href="https://phosphene.video/pricing/#plans" data-utm-campaign="get-pro" data-utm-content="how-take" target="_blank" rel="noopener">About Pro <span aria-hidden="true">↗</span></a>` : '';
  return `<figure class="wn-take${pro ? ' is-pro' : ''}" style="--ar:${wnShape(c).ar.toFixed(4)}">${wnMedia(c, c.label || title)}`
    + '<figcaption>' + (heading ? `<h4 class="wn-feat-t">${wnEsc(heading)}</h4>` : '')
    + (heading && !pro ? '' : `<strong class="wn-take-l">${pro ? '<span class="pro-tag is-lead">Pro</span>' : ''}${wnEsc(label)}</strong>`)
    + (c.caption ? `<span class="wn-take-c">${wnEsc(c.caption)}${link}</span>` : '')
    + '</figcaption></figure>';
}

// One feature: a single take sits media-left, words-right (stacked when the clip is wide); a Free and a Pro take sit
// side by side, each with its words under it. A member sees their own (Pro) take first.
function wnFeature(h, proUser, bare) {
  const takes = [h.free && [h.free, false], h.pro && [h.pro, true]].filter(Boolean);
  if (proUser) takes.reverse();
  const shape = wnShape(takes[0][0]).kind;
  const name = wnFeatureName(h.title);
  const solo = takes.length === 1;
  const title = bare ? '' : (name || 'How it works');
  return `<article class="wn-feat ${solo ? 'is-solo' : 'is-duo'} is-${shape}">`
    + (title && !solo ? `<h4 class="wn-feat-t">${wnEsc(title)}</h4>` : '')
    + `<div class="wn-takes">${takes.map(([c, pro]) => wnTake(c, pro, name, proUser, solo ? title : '')).join('')}</div></article>`;
}

// Two or more single tall clips: two cards side by side (clip on top, words under) instead of a tall stack.
function wnPairs(hows) {
  return hows.length > 1 && hows.every(h => !(h.free && h.pro) && wnShape(h.free || h.pro).kind === 'tall');
}

function wnHowSec(hows, proUser) {
  if (!hows || !hows.length) return '';
  return `<section class="wn-sec wn-sec-how">${wnSecHead('How it works', 'is-how')}`
    + `<div class="wn-feats${wnPairs(hows) ? ' is-pairs' : ''}">${hows.map(h => wnFeature(h, proUser)).join('')}</div></section>`;
}

// ---- the views ----------------------------------------------------------------------------------------------------
function wnNow(d) {
  const proUser = !!d.pro_active;
  return wnHero(d.hero) + wnFreeSec(d) + wnProSec(d, proUser, {full: true}) + wnHowSec(d.how, proUser);
}

function wnEarlier(d) {
  const proUser = !!d.pro_active;
  const rows = (d.earlier || []).map(e => {
    const r = Object.assign({free_title: d.free_title, pro_title: d.pro_title}, e);
    const still = e.still ? wnMedia({image: e.still, w: e.still_w, h: e.still_h}, '', 'wn-rel-still') : '';
    return `<article class="wn-rel"><div class="wn-rel-h"><h3>Phosphene ${wnEsc(e.version)}</h3>`
      + (e.date ? `<time datetime="${wnEsc(e.date)}">${wnEsc(wnDate(e.date))}</time>` : '')
      + (e.headline ? `<p>${wnEsc(e.headline)}</p>` : '') + '</div>'
      + still
      + `<div class="wn-rel-cols">${wnFreeSec(r)}${wnProSec(r, proUser)}</div>`
      + wnHowSec(e.how, proUser) + '</article>';
  }).join('');
  return (rows || `<p class="wn-empty">No earlier releases to show.</p>`)
    + `<p class="wn-more"><a class="dlg-link" href="${wnEsc(d.changelog_url)}" data-utm-campaign="changelog" data-utm-content="whats-new" target="_blank" rel="noopener">Every release since the first, on phosphene.video <span aria-hidden="true">↗</span></a></p>`;
}

// The shell both windows share: head (kicker, title, date and headline, the x), body, foot.
function wnShell(o) {
  const meta = [o.date ? `<time datetime="${wnEsc(o.date)}">${wnEsc(wnDate(o.date))}</time>` : '',
    o.proUser ? '<span class="pro-tag">Pro ✓</span>' : ''].filter(Boolean).join('');
  return `<header class="dlg-head wn-head"><div class="wn-head-main">`
    + `<div class="wn-meta"><span class="dlg-kicker">${wnEsc(o.kicker)}</span>${meta}</div>`
    + `<h2 class="dlg-title wn-title" id="wnTitle">${o.title}</h2>`
    + (o.headline ? `<p class="wn-headline">${wnEsc(o.headline)}</p>` : '') + '</div>'
    + '<button type="button" class="dlg-x" aria-label="Close" title="Close" data-wn-close><svg class="ph" aria-hidden="true"><use href="#ph-x"/></svg></button></header>'
    + `<div class="dlg-body wn-body">${o.body}</div>`
    + `<footer class="dlg-foot wn-foot">${o.footLeft || ''}<div class="dlg-foot-r">`
    + '<button type="button" class="ghost-btn" data-wn-close>Close</button></div></footer>';
}

function wnRender(d) {
  const now = WN.view === 'now';
  const earlierBtn = (d.earlier || []).length || d.changelog_url
    ? `<button type="button" class="ghost-btn" data-wn-view>${now ? 'Earlier releases' : '<span aria-hidden="true">←</span> Back to ' + wnEsc(d.version)}</button>`
    : '';
  return wnShell({
    kicker: now ? "What's new" : 'Earlier releases',
    title: now ? `Phosphene <span class="wn-ver">${wnEsc(d.version)}</span>` : 'Every past release',
    date: now ? d.date : '', headline: now ? d.headline : '', proUser: now && !!d.pro_active,
    body: now ? wnNow(d) : wnEarlier(d), footLeft: earlierBtn,
  });
}

// ---- behaviour ------------------------------------------------------------------------------------------------------
// Clips play while on screen and pause when scrolled away (a dozen looping videos would cost the render its memory).
function wnWatch(root) {
  if (WN.io) { WN.io.disconnect(); WN.io = null; }
  const vids = Array.from(root.querySelectorAll('video[data-wn-play]'));
  if (!vids.length) return;
  const body = root.querySelector('.dlg-body');
  if (!('IntersectionObserver' in window)) { vids.forEach(v => { v.play().catch(() => {}); }); return; }
  WN.io = new IntersectionObserver(entries => {
    entries.forEach(en => {
      const v = en.target;
      if (v.closest('[data-wn-sync]')) return;   // the synced trio starts together (wnSync)
      if (en.isIntersecting) v.play().catch(() => {}); else v.pause();
    });
  }, {root: body || null, threshold: 0.35});
  vids.forEach(v => WN.io.observe(v));
}

// The three Make it vertical clips are the same 5 seconds: start them together so the frames line up.
function wnSync(root) {
  root.querySelectorAll('[data-wn-sync]').forEach(box => {
    const vids = Array.from(box.querySelectorAll('video'));
    if (vids.length < 2 || wnReduced()) return;
    let ready = 0;
    const go = () => { vids.forEach(v => { try { v.currentTime = 0; v.play().catch(() => {}); } catch (e) {} }); };
    vids.forEach(v => {
      v.preload = 'auto';
      const once = () => { ready += 1; if (ready === vids.length) go(); };
      if (v.readyState >= 3) once(); else v.addEventListener('canplay', once, {once: true});
    });
    // the first clip restarts all three, so a slow decoder never drifts for long
    vids[0].loop = false;
    vids[0].addEventListener('ended', go);
  });
}

function wnWire(root) {
  wnSync(root);
  wnWatch(root);
  try { root.querySelectorAll('a[data-utm-campaign]').forEach(a => { if (typeof siteLinkApply === 'function') siteLinkApply(a); }); } catch (e) {}
}

function wnPaint() {
  const box = WN.el && WN.el.querySelector('.dlg');
  if (!box) return;
  box.innerHTML = wnRender(WN.data);
  wnWire(box);
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
  if (WN.io) { WN.io.disconnect(); WN.io = null; }
  if (WN.el) { WN.el.remove(); WN.el = null; }
  document.removeEventListener('keydown', wnKey, true);
  window._phModalOpen = false;
  wnMarkSeen();
  try { if (WN.prevFocus && WN.prevFocus.focus) WN.prevFocus.focus(); } catch (e) {}
}

function wnMount(inner, onClick) {
  WN.prevFocus = document.activeElement;
  const el = document.createElement('div');
  el.id = 'whatsNewCard';
  el.className = 'models-modal wn-wrap';
  el.innerHTML = `<div class="models-card dlg dlg-lg wn-dlg" role="dialog" aria-modal="true" aria-labelledby="wnTitle" tabindex="-1">${inner}</div>`;
  el.addEventListener('click', e => {
    if (e.target === el || e.target.closest('[data-wn-close]')) { wnClose(); return; }
    if (onClick) onClick(e, el);
  });
  document.body.appendChild(el);
  WN.el = el;
  window._phModalOpen = true;
  document.addEventListener('keydown', wnKey, true);
  return el;
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
  const el = wnMount('', (e, root) => {
    if (e.target.closest('[data-wn-view]')) {
      WN.view = WN.view === 'now' ? 'earlier' : 'now';
      wnPaint();
      const b = root.querySelector('.dlg-body'); if (b) b.scrollTop = 0;
      const t = root.querySelector('[data-wn-view]'); if (t) t.focus();
      return;
    }
    if (e.target.closest('a[href]')) wnMarkSeen();   // a link opened the site: this version counts as read
  });
  wnPaint();
  const dlg = el.querySelector('.dlg'); if (dlg) dlg.focus({preventScroll: true});
}

// A feature's own "How it works" link: the same window and the same feature card, nothing else.
async function openHowClip(id) {
  if (WN.el) wnClose();
  let h = null;
  try { h = (await (await fetch('/whats-new/how?id=' + encodeURIComponent(id), {cache: 'no-store'})).json()).how; } catch (e) { h = null; }
  if (!h) { if (typeof phosToast === 'function') phosToast('No how-to clip for this yet.', 'info'); return; }
  const proUser = !!document.documentElement.dataset.pro;
  WN.data = null;
  const el = wnMount(wnShell({kicker: 'How it works', title: wnEsc(wnFeatureName(h.title)), proUser: false,
    body: wnFeature(h, proUser, true)}));
  el.querySelector('.dlg').classList.add('wn-dlg-how');
  wnWire(el);
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
