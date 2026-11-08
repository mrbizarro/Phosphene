// webapp/js/whats_new.js - the once-per-version "What's new" card.
//
// After Phosphene updates, the first time the studio opens it shows ONE small card in the corner:
// not a blocking modal, one click closes it, and it is not shown again for that version
// (the server remembers: POST /whats-new/seen). The version pill reopens it (openWhatsNew(true)).
// Pro users see "New in Pro" first with a thank-you line. Free users see "New for everyone" and then
// the Pro list too, each line tagged Pro, with one "Get Pro" link under it. No countdown, no nagging.

const WN = { el: null, data: null, retried: false };

function wnEsc(t) {
  return String(t == null ? '' : t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function wnList(items, tag) {
  if (!items || !items.length) return '';
  return '<ul class="wn-list">' + items.map(it => {
    const media = it.media
      ? (/\.(mp4|webm)$/i.test(it.media)
          ? `<video class="wn-media" src="${wnEsc(it.media)}" muted loop autoplay playsinline></video>`
          : `<img class="wn-media" src="${wnEsc(it.media)}" alt="">`)
      : '';
    return `<li>${tag ? '<span class="wn-protag">Pro</span>' : ''}<span class="wn-text">${wnEsc(it.text)}</span>${media}</li>`;
  }).join('') + '</ul>';
}

function wnRender(d) {
  const proUser = !!d.pro_active;
  const proBlock = (d.pro && d.pro.length)
    ? `<section class="wn-sec"><h3>${wnEsc(d.pro_title)}</h3>`
      + (proUser && d.thanks ? `<p class="wn-thanks">${wnEsc(d.thanks)}</p>` : '')
      + wnList(d.pro, !proUser)
      + (proUser ? '' : `<a class="wn-getpro" href="${wnEsc(d.pricing_url)}" data-utm-campaign="get-pro" data-utm-content="whats-new" target="_blank" rel="noopener">Get Pro <span aria-hidden="true">↗</span></a>`)
      + '</section>'
    : '';
  const freeBlock = (d.free && d.free.length)
    ? `<section class="wn-sec"><h3>${wnEsc(d.free_title)}</h3>${wnList(d.free, false)}</section>`
    : '';
  return `<div class="wn-head"><strong>${wnEsc(d.title)}</strong>`
    + '<button type="button" class="wn-x" aria-label="Close" data-wn-close>×</button></div>'
    + (proUser ? proBlock + freeBlock : freeBlock + proBlock);
}

function wnClose() {
  const d = WN.data;
  if (WN.el) { WN.el.remove(); WN.el = null; }
  if (d && d.version) {
    fetch('/whats-new/seen', {method: 'POST', body: new URLSearchParams({version: d.version})}).catch(() => {});
  }
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
  const el = document.createElement('aside');
  el.id = 'whatsNewCard';
  el.className = 'wn-card';
  el.setAttribute('role', 'region');
  el.setAttribute('aria-label', d.title);
  el.innerHTML = wnRender(d);
  el.addEventListener('click', e => {
    if (e.target.closest('[data-wn-close]') || e.target.closest('.wn-getpro')) wnClose();
  });
  document.body.appendChild(el);
  WN.el = el;
  try { document.querySelectorAll('#whatsNewCard a[data-utm-campaign]').forEach(a => { if (typeof siteLinkApply === 'function') siteLinkApply(a); }); } catch (e) {}
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
Object.assign(globalThis, { openWhatsNew, wnClose });
