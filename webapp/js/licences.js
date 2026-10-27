// webapp/js/licences.js — per-model licence screens (4.19.8).
//
// Some models Phosphene downloads carry licences that ask for more than a
// README line: MiniMax H3 (territory, use restrictions, a "MiniMax H3" label,
// a misuse-report path) and the non-commercial ones (Ideogram 4, YuE2,
// FLUX.1 Krea [dev], Bria FIBO). The SERVER decides when one is needed:
// every route that would download or run such a model answers HTTP 451 with
// {code: 'licence_required', licence: <key>} until the user has accepted that
// licence on this Mac (model_licences.py, panel/routes_licences.py).
//
// This module turns that answer into the screen, generically: a fetch()
// wrapper sees the 451, shows the model's licence (name, link, the
// restrictions that matter, an explicit "I accept" box), records the
// acceptance with POST /licences/accept, and re-sends the original request
// once. Decline leaves the 451 response to the caller, whose own error path
// prints the server's sentence. No call site in any other module had to learn
// about licences.

let _LIC = null;            // last GET /licences payload
let _gateKey = null;        // the model shown by the current screen
let _gateOpen = null;       // the promise of the screen currently shown
const _nativeFetch = globalThis.fetch.bind(globalThis);

async function licencesLoad(force) {
  if (_LIC && !force) return _LIC;
  try {
    const r = await _nativeFetch('/licences', { cache: 'no-store' });
    _LIC = await r.json();
  } catch (e) {
    _LIC = _LIC || { licences: [], ui: {} };
  }
  return _LIC;
}

function _licInfo(key) {
  return ((_LIC && _LIC.licences) || []).find(l => l.key === key) || null;
}

function _licEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// Show one model's licence. Resolves true when the user accepted it (now or
// earlier), false when they closed the screen. `readOnly` shows an accepted
// licence again (the H3 credit line's "Licence" link) without a checkbox.
async function openLicence(key, opts) {
  opts = opts || {};
  await licencesLoad(true);
  const info = _licInfo(key);
  if (!info) return false;                    // no current licence was loaded
  if (info.accepted && !opts.readOnly && !opts.force) return true;
  // "Read the licence" on a licence nobody accepted yet IS the place to
  // accept it (the H3 credit line is the door for storyboard / API users).
  if (!info.accepted) opts = Object.assign({}, opts, { readOnly: false });
  if (_gateOpen) {
    if (_gateKey === key) return _gateOpen;
    await _gateOpen;
    return openLicence(key, opts);
  }
  const ui = (_LIC && _LIC.ui) || {};
  const modal = document.getElementById('licenceModal');
  const body = document.getElementById('licenceBody');
  const title = document.getElementById('licenceTitle');
  if (!modal || !body) return false;
  const terms = (info.terms || []).map(t => `<li>${_licEsc(t)}</li>`).join('');
  const accepted = info.accepted
    ? `<p class="licence-accepted">Accepted on this Mac${info.accepted_at ? ' (' + _licEsc(String(info.accepted_at).slice(0, 10)) + ')' : ''}.</p>`
    : '';
  title.textContent = info.title || info.licence || ui.modal_title || 'Model licence';
  body.innerHTML = `
    <div class="licence-head">
      <span class="licence-model">${_licEsc(info.model)}</span>
      <span class="licence-badge${info.non_commercial ? ' nc' : ''}">${_licEsc(info.badge || '')}</span>
    </div>
    <p class="licence-summary">${_licEsc(info.summary || '')}</p>
    ${info.territory_notice ? `<p class="licence-territory">${_licEsc(info.territory_notice)}</p>` : ''}
    <ul class="licence-terms">${terms}</ul>
    <p class="licence-meta">
      ${_licEsc(info.licence)} &middot; ${_licEsc(info.licensor || '')}
      &middot; <a href="${_licEsc(info.url)}" target="_blank" rel="noopener">${_licEsc(ui.full_licence_link || 'Read the full licence')}</a>
    </p>
    ${info.report_line && info.report_url ? `<p class="licence-meta">${_licEsc(info.report_line)}
      <a href="${_licEsc(info.report_url)}" target="_blank" rel="noopener">Report misuse</a></p>` : ''}
    ${accepted}
    ${(!info.accepted || opts.force) && !opts.readOnly ? `
      <label class="licence-check"><input type="checkbox" id="licenceAcceptBox">
        <span>${_licEsc(info.accept_label || 'I have read this licence and I accept it.')}</span></label>
      <div class="licence-actions">
        <button class="ghost-btn" id="licenceDecline">${_licEsc(ui.decline_button || 'Not now')}</button>
        <button class="primary-btn" id="licenceAccept" disabled>${_licEsc(ui.accept_button || 'Accept')}</button>
      </div>
      <p class="licence-error" id="licenceError" hidden></p>` : `
      <div class="licence-actions">
        <button class="ghost-btn" id="licenceDone">Close</button>
      </div>`}`;
  modal.style.display = 'flex';
  let settled = false;
  let promise = null;
  promise = new Promise((resolve) => {
    // Idempotent, and it only tears down ITS OWN screen: a slow accept that
    // lands after the user closed this one and opened another must not hide
    // the newer screen or orphan its promise (Codex review, 4.19.8).
    const done = (ok) => {
      if (settled) return;
      settled = true;
      if (_gateOpen === promise) {
        modal.style.display = 'none';
        modal.onclick = null;
        _gateOpen = null;
        _gateKey = null;
      }
      resolve(ok);
    };
    const box = document.getElementById('licenceAcceptBox');
    const acc = document.getElementById('licenceAccept');
    const dec = document.getElementById('licenceDecline');
    if (box && acc) box.onchange = () => { acc.disabled = !box.checked; };
    if (dec) dec.onclick = () => done(!!info.accepted && !opts.force);
    const fin = document.getElementById('licenceDone');
    if (fin) fin.onclick = () => done(!!info.accepted);
    modal.onclick = (ev) => { if (ev.target === modal) done(!!info.accepted && !opts.force); };
    if (acc) acc.onclick = async () => {
      if (!box.checked) return;
      acc.disabled = true;
      try {
        const r = await _nativeFetch('/licences/accept', {
          method: 'POST',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
          body: new URLSearchParams({ key, version: info.version, accept: '1' }),
        });
        const j = await r.json().catch(() => ({}));
        if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
        await licencesLoad(true);
        licenceStatusRefresh();
        done(true);
      } catch (e) {
        if (settled) return;
        const err = document.getElementById('licenceError');
        if (err) { err.hidden = false; err.textContent = 'Could not save: ' + e.message; }
        acc.disabled = !box.checked;
      }
    };
  });
  _gateKey = key;
  _gateOpen = promise;
  return promise;
}

function closeLicence() {
  const btn = document.getElementById('licenceDecline') || document.getElementById('licenceDone');
  if (btn) btn.click();
}

// Small "accepted / not yet" lines other screens embed (the H3 install card):
// <span data-licence-status="h3"></span> is filled from /licences.
function licenceStatusRefresh() {
  document.querySelectorAll('[data-licence-status]').forEach((el) => {
    const info = _licInfo(el.getAttribute('data-licence-status'));
    if (!info) { el.textContent = ''; return; }
    el.textContent = info.accepted
      ? 'Licence accepted on this Mac.'
      : 'Not accepted yet.';
    el.classList.toggle('ok', !!info.accepted);
  });
}

// The generic gate. Every fetch() in the page goes through here; only a 451
// with code=licence_required is touched, every other response passes as-is.
// The original request is re-sent once after an accept (its body is a
// FormData / URLSearchParams / string, all of which can be sent twice).
function installLicenceGate() {
  if (globalThis.fetch && globalThis.fetch._licenceGate) return;
  const gated = async function (input, init) {
    // A Request object's body can be read once: keep a clone for the retry.
    const spare = (typeof globalThis.Request === 'function' && input instanceof globalThis.Request)
      ? input.clone() : null;
    const r = await _nativeFetch(input, init);
    if (r.status !== 451) return r;
    let body = null;
    try { body = await r.clone().json(); } catch (e) { return r; }
    if (!body || body.code !== 'licence_required' || !body.licence) return r;
    const ok = await openLicence(body.licence);
    if (!ok) return r;
    return _nativeFetch(spare || input, init);
  };
  gated._licenceGate = true;
  globalThis.fetch = gated;
  licencesLoad().then(licenceStatusRefresh);
}

Object.assign(globalThis, {
  openLicence, closeLicence, installLicenceGate, licenceStatusRefresh, licencesLoad,
});
