// webapp/js/whats_new.js - the once-per-version "What's new" window and the feature "How it works" viewer.
//
// NOW IN PRO (5.0.23). The owner, 14 Nov: "The Now in Pro section should be on the upper side of the report of the new
// things in the app and should link to the Pro page. We want to make conversions from the app." When a release names
// Pro perks (whats_new.json "pro_perks", their words and measured gains from pro_perks.json), they come high up, before
// the hero and How it works: one plain line each with its measured gain, one Get Pro button for Free users (utm:
// get-pro / whats-new), "Included in your Pro" and no button for a member. The old New in Pro box below is then not
// drawn (no line twice).
//
// PRO BLOCK FIRST (owner, 19 Nov 2026, on the 5.0.62 window: "why is there not a call to action to the landing page in
// the Pro part, and why is the Pro part below? It should be above. We talk about these things many times. Why does
// stuff keep coming back?"). The Pro block is drawn FIRST, right under the title, for everyone; the free changes come
// straight after it. A Free user's Pro block ends with one Get Pro button to the Pro page (utm_medium whats-new,
// utm_content the version); a member reads "Included in your Pro", no buy button, and one small "What's in Pro" link.
// It came back once already: a 14 Nov change put the free list first (so it would not fall below the fold) and
// rewrote the test that pinned the order in the same commit. scripts/check_whats_new.py now renders this window as a
// Free and a Pro user and fails the release if the Pro block is not first. The tease under real items stays one line,
// so the Pro block stays short and the free changes still show on the first screen. Do not reorder; ask the owner.
//
// ONE FIXED FORMAT (5.0.20). The owner, 12 Nov, on the 5.0.19 window: empty boxes, small off-centre clips, no
// structure, it read as a debug page. Every release now reads the same, top to bottom, from whats_new.json:
//   1. header: the version, the date, the release's one-line "headline"
//   2. Now in Pro         (when the release adds to Pro or teases what is next; FIRST, see above)
//   2b. New for everyone  (only when the release has something for everyone; right under the Pro block)
//   3. the hero, when the release has one
//   4. New in Pro         (only without the Now in Pro block, and only when the release adds something to Pro: the
//                          owner, 12 Nov, "if there is nothing
//                          new in Pro, then we shouldn't have this written in there". A member reads a thank-you
//                          and what their licence added; a Free user reads the same lines, the measured "What Pro
//                          adds" item and one quiet support link)
//   5. How it works       (one card per new feature: the clip sized to its real shape, its words beside or under)
// Footer: "Earlier releases" on the left, Close on the right. Earlier releases page back in the same window and
// draw each release with the same pieces, so an old release reads like a new one. The server sends every clip's
// real width and height (read from its poster), so frames are the clip's own shape before a byte of video loads.
//
// PLAIN WORDS, IN THEIR LANGUAGE (owner, 17 Nov 2026, on a Spanish window with English lines about "the Pro pack"):
// every word here is the user's language. The lines come from the server already translated (?lang= the page's own
// language, whats_new.json "i18n"); every heading and button below goes through T(); the date uses the same language.
//
// Shown once per version (POST /whats-new/seen when it closes); the version pill reopens it (openWhatsNew(true)).
// Escape, the X, Close or a click outside closes it. Clips play only while on screen; reduced motion: none plays.

const WN = { el: null, data: null, retried: false, view: 'now', prevFocus: null, io: null };
const WN_ASK = T('whatsnew.phosphene_stays_free_and_open', 'Phosphene stays free and open source. Pro gets new features and speed-ups first, and keeps the project going.');

function wnEsc(t) {
  return String(t == null ? '' : t).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function wnReduced() {
  try { return window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
}

function wnDate(d) {
  if (!d) return '';
  try {
    return new Date(d + 'T00:00:00Z').toLocaleDateString(wnLang(), {month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC'});
  } catch (e) { return d; }
}

// The page's language (BOOT.i18n.lang, i18n.js), for the server's lines and the date.
function wnLang() {
  try { return (typeof BOOT !== 'undefined' && BOOT && BOOT.i18n && BOOT.i18n.lang) || 'en'; } catch (e) { return 'en'; }
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
    return `<section class="wn-hero" aria-label="${wnEsc(h.title || T('whatsnew.highlight', 'Highlight'))}">${head}`
      + `<div class="wn-hero-vert" data-wn-sync>${wnHeroFig(h.original)}`
      + '<span class="wn-hero-arrow" aria-hidden="true">→</span>'
      + wnHeroFig(h.free) + wnHeroFig(h.pro, true) + '</div></section>';
  }
  return `<section class="wn-hero" aria-label="${wnEsc(h.title || T('whatsnew.highlight', 'Highlight'))}">${head}`
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
    + `<figcaption><span class="wn-measured-k">${T('whatsnew.measured_on_one_mac', 'Measured on one Mac')}</span><span>${wnEsc(it.text)}`
    + (it.link ? ` <a class="dlg-link" href="${wnEsc(it.link)}" data-utm-campaign="get-pro" data-utm-content="whats-new-speed" target="_blank" rel="noopener">${T('whatsnew.how_it_was_measured', 'How it was measured')} <span aria-hidden="true">↗</span></a>` : '')
    + '</span></figcaption></figure>').join('');
}

// A section that has nothing in it is not drawn: no "New in Pro" heading over an empty box.
// first: the current window draws it right under the header, tighter, so it shares the first screen with the Pro block.
function wnFreeSec(r, first) {
  if (!(r.free && r.free.length)) return '';
  return `<section class="wn-sec wn-sec-free${first ? ' is-first' : ''}">${wnSecHead(r.section || T('whatsnew.new_for_everyone', 'New for everyone'), 'is-free', (r.free || []).length)}`
    + wnLines(r.free) + '</section>';
}

// opts.full: the current release (thank-you, showcase, support link). Earlier releases show the lines only.
function wnProSec(r, proUser, opts) {
  if (!(r.pro && r.pro.length)) return '';
  const full = opts && opts.full;
  let body = '';
  if (full && proUser && r.thanks) body += `<p class="wn-pro-thanks">${wnEsc(T('whatsnew.thanks', 'Thank you for supporting Phosphene. This is what your Pro licence added.'))}</p>`;
  body += wnLines(r.pro);
  if (full && !proUser) {
    body += wnShowcase(r.showcase)
      + `<div class="wn-ask"><p>${wnEsc(WN_ASK)}</p>`
      + `<a class="wn-ask-link" href="${wnEsc(r.pricing_url || 'https://phosphene.video/pricing/')}" data-utm-campaign="get-pro" data-utm-content="whats-new" target="_blank" rel="noopener">${T('whatsnew.support_phosphene', 'Support Phosphene')} <span aria-hidden="true">↗</span></a></div>`;
  }
  return `<section class="wn-sec wn-sec-pro">${wnSecHead(T('whatsnew.new_in_pro', 'New in Pro'), 'is-pro', (r.pro || []).length)}${body}</section>`;
}

// ---- 0. Now in Pro, at the top ------------------------------------------------------------------------------------
// THE OFFER (owner, 18 Nov 2026, on the 5.0.54 window: "add in there a visible link to go and check up on the offers
// ... Let's give them a chance to get into the lander"). Free users only, at the foot of the Now in Pro block: the
// support line, then a Founding pill and the bright monthly button side by side (20 Nov, Option A), both to the
// Pro page in the user's language (the site has every Studio language; the Chinese page carries the yuan Founding
// price, so its line says yuan). Founding always reads "first 100 buyers", never a count, and no other price is
// shown. Both keep the 5.0.65 tags (utm_medium=whats-new, utm_campaign=get-pro, utm_content=<version>, the closed
// where word whats_new) and say their plan, which boot.js adds to the anonymous Get Pro count.
const WN_SITE_LANG = {de: 'de', es: 'es', fr: 'fr', it: 'it', ja: 'ja', ko: 'ko', pt: 'pt', ru: 'ru', zh: 'zh'};

function wnPricingUrl(url) {
  const seg = WN_SITE_LANG[String(wnLang()).toLowerCase().split('-')[0]];
  try {
    const u = new URL(url || 'https://phosphene.video/pricing/');
    if (seg && u.hostname === 'phosphene.video' && u.pathname.startsWith('/pricing')) u.pathname = '/' + seg + u.pathname;
    return u.href;
  } catch (e) { return url; }
}

function wnOffer(url, tag) {
  const href = wnPricingUrl(url) || '';
  const foundingHref = href.replace(/#.*$/, '') + '#plans';
  return `<div class="wn-np-foot wn-np-offer"><p>${wnEsc(WN_ASK)}</p><div class="wn-np-offer-act">`
    + `<a class="wn-np-founding" href="${wnEsc(foundingHref)}" data-plan="founding" data-utm-campaign="get-pro"${tag}>`
    + `<b>${wnEsc(T('whatsnew.offer_founding_short', 'Founding · $199 / 3 yrs'))}</b>`
    + `<span>${wnEsc(T('whatsnew.offer_founding_sub', 'One-time · first 100 buyers'))} <span aria-hidden="true">↗</span></span></a>`
    + `<a class="wn-np-cta" href="${wnEsc(href)}" data-plan="monthly" data-utm-campaign="get-pro"${tag}>`
    + `${wnEsc(T('whatsnew.offer_try_pro_15', 'Try Pro · $15/mo'))} <span aria-hidden="true">→</span></a>`
    + '</div></div>';
}

function wnNowPro(d) {
  const n = d.now_in_pro;
  const items = (n && n.items) || [], coming = (n && n.coming) || [];
  if (!n || (!items.length && !coming.length)) return '';
  const proUser = !!d.pro_active;
  const teasing = !items.length;   // nothing new for Pro this release: the tease carries the block (owner, 14 Nov 2026)
  const comingTitle = proUser ? T('whatsnew.coming_next_in_your_pro', 'Coming next in your Pro') : T('whatsnew.coming_next_in_pro', 'Coming next in Pro');
  const title = teasing ? comingTitle
    : (proUser ? T('whatsnew.included_in_your_pro', 'Included in your Pro') : T('whatsnew.now_in_pro', 'Now in Pro'));
  const soon = `<span class="wn-np-soon">${T('whatsnew.soon', 'Soon')}</span>`;
  const lines = items.map(it => `<li><span class="wn-np-t">${wnEsc(it.text)}</span>`
    + (it.gain ? `<span class="wn-np-gain">${wnEsc(it.gain)}</span>` : '') + '</li>').join('');
  const comingLines = coming.map(it => `<li class="is-coming"><span class="wn-np-t">${wnEsc(it.text)}</span>`
    + `<span class="wn-np-tags">${soon}${it.gain ? `<span class="wn-np-gain">${wnEsc(it.gain)}</span>` : ''}</span></li>`).join('');
  // Under real items the tease is ONE brief line (name and gain per perk), so the block stays short and the free
  // changes above and the Get Pro button below share the first screen. With no items the tease is the block: full lines.
  const comingBrief = coming.map(it => `<span class="wn-np-coming-it">${wnEsc(it.name || it.text)}`
    + (it.gain ? ` <span class="wn-np-gain">${wnEsc(it.gain)}</span>` : '') + '</span>').join('<span class="wn-np-dot" aria-hidden="true"> · </span>');
  const comingBlock = !coming.length ? ''
    : teasing ? `<div class="wn-np-coming is-lead"><ul class="wn-np-lines">${comingLines}</ul></div>`
    : `<p class="wn-np-coming is-brief"><span class="wn-np-coming-k">${wnEsc(comingTitle)}</span>${soon}${comingBrief}</p>`;
  const also = !proUser && n.also && n.also.length
    ? `<p class="wn-np-also"><span class="wn-np-also-k">${T('whatsnew.also_in_pro', 'Also in Pro')}</span> ${n.also.map(wnEsc).join(' · ')}</p>` : '';
  // The Pro page, tagged utm_medium=whats-new&utm_campaign=get-pro&utm_content=<version> (siteLinkApply adds them from
  // these data- attributes; data-pro-where keeps the anonymous Get Pro count on its closed word, whats_new).
  const proUrl = (n.cta && n.cta.url) || n.pro_url || d.pricing_url;
  const proPage = wnEsc(proUrl);
  const tag = ` data-utm-medium="whats-new" data-utm-content="${wnEsc(d.version || '')}" data-pro-where="whats_new" target="_blank" rel="noopener"`;
  const cta = !proUser && n.cta ? wnOffer(proUrl, tag) : '';
  const memberLink = proUser
    ? `<p class="wn-np-member"><a class="dlg-link wn-np-what" href="${proPage}" data-utm-campaign="pricing"${tag}>`
      + `${T('whatsnew.what_s_in_pro', "What's in Pro")} <span aria-hidden="true">↗</span></a></p>` : '';
  return `<section class="wn-np${proUser ? ' is-member' : ''}${teasing ? ' is-teasing' : ''}" aria-label="${wnEsc(title)}">`
    + `<div class="wn-np-h"><span class="pro-tag is-lead${proUser ? ' pro-on' : ''}">${proUser ? 'Pro ✓' : 'Pro'}</span><h3>${wnEsc(title)}</h3></div>`
    + (lines ? `<div class="wn-np-row"><ul class="wn-np-lines">${lines}</ul>${wnNpClip(d)}</div>` : '') + `${comingBlock}${also}${cta}${memberLink}</section>`;
}

// The release's first how-to clip, small, playing silently in the empty right corner of Now in Pro; a click opens it.
function wnNpClip(d) {
  const h = (d.how || [])[0]; const c = h && (h.pro || h.free);
  if (!c || !c.video) return '';
  // Silent loop until clicked: browsers refuse sound before a click. A click restarts it IN PLACE with sound and
  // controls, so the characters' voices are heard and the window stays open (owner, 21 Nov 2026: "people need
  // to hear that the characters have voices").
  return `<div class="wn-np-clip" role="button" tabindex="0" onclick="wnNpSound(this)" aria-label="${wnEsc(h.title || '')}">`
    + `<video src="${wnEsc(c.video)}" poster="${wnEsc(c.poster || '')}" muted autoplay loop playsinline></video>`
    + `<span class="wn-np-clip-k">🔊 ${T('whatsnew.how_it_works', 'How it works')}</span></div>`;
}

function wnNpSound(el) {
  const v = el.querySelector('video');
  if (!v || el.classList.contains('is-on')) return;
  el.classList.add('is-on');
  v.muted = false; v.loop = false; v.currentTime = 0; v.controls = true;
  v.play().catch(() => {});
}

// ---- 5. how it works --------------------------------------------------------------------------------------------
function wnTake(c, pro, title, proUser, heading) {
  if (!c) return '';
  const label = String(c.label || (pro ? 'Pro' : '')).replace(/^Pro:\s*/, '').replace(/^Pro$/, T('whatsnew.with_pro', 'With Pro'));
  const link = pro && !proUser
    ? ` <a class="dlg-link" href="https://phosphene.video/pricing/#plans" data-utm-campaign="get-pro" data-utm-content="how-take" target="_blank" rel="noopener">${T('whatsnew.about_pro', 'About Pro')} <span aria-hidden="true">↗</span></a>` : '';
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
  return `<section class="wn-sec wn-sec-how">${wnSecHead(T('whatsnew.how_it_works', 'How it works'), 'is-how')}`
    + `<div class="wn-feats${wnPairs(hows) ? ' is-pairs' : ''}">${hows.map(h => wnFeature(h, proUser)).join('')}</div></section>`;
}

// ---- the views ----------------------------------------------------------------------------------------------------
function wnNow(d) {
  const proUser = !!d.pro_active;
  const top = wnNowPro(d);
  // THE PRO BLOCK FIRST, right under the title (owner, 19 Nov 2026), the free changes straight after, then the hero and
  // the rest. scripts/check_whats_new.py renders this and refuses a release where anything comes before the Pro block.
  const pro = top || wnProSec(d, proUser, {full: true});
  return pro + wnFreeSec(d, true) + wnHero(d.hero);
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
      + `<div class="wn-rel-cols">${wnProSec(r, proUser)}${wnFreeSec(r)}</div>`
      + wnHowSec(e.how, proUser) + '</article>';
  }).join('');
  return (rows || `<p class="wn-empty">${T('whatsnew.no_earlier_releases', 'No earlier releases to show.')}</p>`)
    + `<p class="wn-more"><a class="dlg-link" href="${wnEsc(d.changelog_url)}" data-utm-campaign="changelog" data-utm-content="whats-new" target="_blank" rel="noopener">${T('whatsnew.every_release_since_the_first', 'Every release since the first, on phosphene.video')} <span aria-hidden="true">↗</span></a></p>`;
}

// The shell both windows share: head (kicker, title, date and headline, the x), body, foot.
function wnShell(o) {
  const meta = [o.date ? `<time datetime="${wnEsc(o.date)}">${wnEsc(wnDate(o.date))}</time>` : '',
    o.proUser ? '<span class="pro-tag">Pro ✓</span>' : ''].filter(Boolean).join('');
  return `<header class="dlg-head wn-head"><div class="wn-head-main">`
    + `<div class="wn-meta"><span class="dlg-kicker">${wnEsc(o.kicker)}</span>${meta}</div>`
    + `<h2 class="dlg-title wn-title" id="wnTitle">${o.title}</h2>`
    + (o.headline ? `<p class="wn-headline">${wnEsc(o.headline)}</p>` : '') + '</div>'
    + `<button type="button" class="dlg-x" aria-label="${TA('whatsnew.close', 'Close')}" title="${TA('whatsnew.close', 'Close')}" data-wn-close>`
    + '<svg class="ph" aria-hidden="true"><use href="#ph-x"/></svg></button></header>'
    + `<div class="dlg-body wn-body">${o.body}</div>`
    + `<footer class="dlg-foot wn-foot">${o.footLeft || ''}<div class="dlg-foot-r">`
    + `<button type="button" class="ghost-btn" data-wn-close>${T('whatsnew.close', 'Close')}</button></div></footer>`;
}

function wnRender(d) {
  const now = WN.view === 'now';
  const earlierBtn = (d.earlier || []).length || d.changelog_url
    ? `<button type="button" class="ghost-btn" data-wn-view>${now ? T('whatsnew.earlier_releases', 'Earlier releases') : '<span aria-hidden="true">←</span> ' + wnEsc(T('whatsnew.back_to', 'Back to {version}', {version: d.version}))}</button>`
    : '';
  return wnShell({
    kicker: now ? T('whatsnew.what_s_new', "What's new") : T('whatsnew.earlier_releases', 'Earlier releases'),
    title: now ? `Phosphene <span class="wn-ver">${wnEsc(d.version)}</span>` : T('whatsnew.every_past_release', 'Every past release'),
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

// A clip that leaves the screen must stop decoding: pause it and drop its source. WebKit keeps decoding a removed or
// replaced <video> until it is garbage-collected, and that background decoding made the main player stutter (L211).
function wnUnload(root) {
  if (!root) return;
  root.querySelectorAll('video').forEach(v => {
    try { v.pause(); v.removeAttribute('src'); v.load(); } catch (e) {}
  });
}

function wnPaint() {
  const box = WN.el && WN.el.querySelector('.dlg');
  if (!box) return;
  wnUnload(box);
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
  if (WN.el) { wnUnload(WN.el); WN.el.remove(); WN.el = null; }
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
    const r = await fetch('/whats-new?lang=' + encodeURIComponent(wnLang()) + (force ? '&force=1' : ''), {cache: 'no-store'});
    d = await r.json();
  } catch (e) { return; }
  if (!d || !d.show) {
    if (force && typeof phosToast === 'function') phosToast(T('whatsnew.nothing_new_to_show_for', 'Nothing new to show for this version.'), 'info');
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
  if (!h) { if (typeof phosToast === 'function') phosToast(T('whatsnew.no_how_to_clip_for', 'No how-to clip for this yet.'), 'info'); return; }
  const proUser = !!document.documentElement.dataset.pro;
  WN.data = null;
  const el = wnMount(wnShell({kicker: T('whatsnew.how_it_works', 'How it works'), title: wnEsc(wnFeatureName(h.title)), proUser: false,
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
Object.assign(globalThis, {wnNpSound,  openWhatsNew, wnClose, openHowClip });
