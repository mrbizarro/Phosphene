// webapp/js/health.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
// ====== Version pill (the "magic button") ======
//
// One always-visible pill in the header that changes content + colour
// based on /version state. Clicking it does the right thing for the
// current state — no modal, no nested click flows.
//
// Backend: a daemon thread polls GitHub every 30 minutes (commits API
// for the SHA + raw VERSION file for the human-friendly Y1.NNN label)
// and exposes the result at /version. The JS polls /version every 5
// minutes (cheap; pre-computed dict read). When the user clicks while
// behind, /version/pull does the actual `git pull` server-side; the
// user still has to Stop+Start phosphene in Pinokio to apply.
//
// Rationale: users keep telling us "I clicked Update but I don't see
// the new features" — by the time the feedback reaches us we've usually
// pushed three more commits. The pill turns the loop from
// "hope-they-noticed" into a literal one-click action.

let _versionState = null;
let _versionRestartPending = false;   // set after a successful /version/pull;
                                      // pill turns into a "restart" reminder.

async function refreshVersionPill() {
  try {
    const r = await fetch('/version');
    _versionState = await r.json();
  } catch (e) {
    return;             // network blip; don't blow away last good state
  }
  renderVersionPill();
  try { _ubRender(_versionState); } catch (e) {}
  try { _maybeShowUpdateModal(_versionState); } catch (e) {}
  try { _maybeShowBroadcastModal(_versionState); } catch (e) {}
}

function _versionDisplayLabel(s) {
  // Prefer the human Y1.NNN VERSION file label. Fall back to the short
  // SHA for older checkouts that predate the VERSION file. Last-resort
  // ellipsis when nothing's known yet.
  return s.local_version || s.local_short || '…';
}

function _versionRemoteLabel(s) {
  return s.remote_version || s.remote_short || 'latest';
}

function _versionBuildLabel(version, short) {
  // A build is named unambiguously only by BOTH halves: the VERSION label is
  // what a user recognises, the SHA is what actually separates two builds
  // carrying the same label.
  if (version && short) return `${version} (${short})`;
  return version || short || 'an unknown build';
}

function renderVersionPill() {
  const pill = document.getElementById('versionPill');
  if (!pill) return;
  const s = _versionState || {};
  const local = _versionDisplayLabel(s);
  const remote = _versionRemoteLabel(s);
  // Reset every state class; exactly one is added below.
  pill.classList.remove('pill-update','pill-current','pill-dev','pill-checking','pill-restart','pill-busy');
  pill.style.display = '';

  // Pill text leads with the MEANING of the state, not the version code.
  // Earlier build showed bare "Y1.005" which read as a label rather than
  // a status — users didn't realize they could click it. Now every state
  // uses plain English so a user glancing at the header understands at
  // a glance whether they're current, behind, or need to restart.

  // Highest-priority state: a pull just happened and the panel needs a
  // restart to load the new code.
  if (_versionRestartPending) {
    pill.classList.add('pill-restart');
    pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-arrow-clockwise-bold"/></svg>Restart Phosphene';
    const v = s.pull_pulled_to_version || s.pull_pulled_to_short || 'the new code';
    pill.title = s.pull_requires_full_update
      ? `Pulled ${v}. This update touched dependencies — use Pinokio's Update button (not just Stop+Start).`
      : `Pulled ${v}. Click Stop → Start in Pinokio to apply.`;
    return;
  }
  // Same restart affordance for a checkout that advanced UNDER this
  // process (a promote landing while a long-lived panel serves) — the
  // server compares HEAD-on-disk with HEAD-at-boot on every /version.
  // Without this the 2026-08-14 incident repeats: newer code on disk,
  // stale code in memory, and nothing anywhere saying so.
  if (s.stale_process) {
    if (!window._staleReported) { window._staleReported = true; _uiEvent('update_prompt', {action: 'restart_needed'}); }
    pill.classList.add('pill-restart');
    pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-arrow-clockwise-bold"/></svg>Restart to finish update';
    // Name BOTH builds, each with its SHA. Most fixes land without a VERSION
    // bump, so on dev the two labels read the same number and a tooltip built
    // from labels alone said "Phosphene 4.6.0 is on disk, but this panel
    // process loaded 4.6.0" — an alarm that names nothing.
    pill.title = `Running ${_versionBuildLabel(s.local_version, s.local_short)}`
      + ` — but ${_versionBuildLabel(s.disk_version, s.disk_short)} is on disk.`
      + ` Click Stop → Start in Pinokio (or restart the panel) to load it.`;
    return;
  }
  // Suppressed (dev branch / dirty tree / no git).
  // 2026-05-21 — Mr Bizarro report: Reddit users on dev were confused
  // because the pill showed an old VERSION string (e.g. "2.0.5") while
  // they had pulled fresh code. Now we append the short SHA + commit
  // date so every dev build is uniquely identifiable. Lets us tell a
  // user "you're on 3.0.0 · dev · <your-sha> (2026-05-21) — please
  // pull again, latest is <newer-sha>" instead of just shrugging.
  //
  // The placeholders are deliberate. This comment used to carry two REAL
  // SHAs, formatted exactly like the stamp it describes, and it ships inside
  // the served page — so grepping the page for the running build answered
  // with a 2026-05-21 commit, in the stamp's own format, with total
  // confidence. build_stamp_text() puts the true answer in the page now, as
  // <meta name="phosphene-build">.
  if (s.suppress_reason) {
    pill.classList.add('pill-dev');
    const sha = s.local_short || '';
    const date = s.local_commit_date || '';
    const trail = sha ? ` · ${sha}${date ? ` (${date})` : ''}` : '';
    const branchLabel = (s.local_branch && s.local_branch !== 'main')
      ? s.local_branch : 'dev';
    // The SHA/date tail is what pushes a 1300px (Pinokio-sized) header over
    // budget and clips the health chip; below 1400px CSS hides .vp-detail.
    const _esc = (t) => String(t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
    pill.innerHTML = `${_esc(local)} · ${_esc(branchLabel)}<span class="vp-detail">${_esc(trail)}</span>`;
    pill.title = `Update check paused: ${s.suppress_reason}.`
      + (sha ? `\nFull SHA: ${s.local_sha || sha}` : '')
      + `\nClick to copy a paste-ready debug string.`;
    return;
  }
  // Behind origin/main — eye-catching action prompt.
  if (!s.error && s.checked_ts && (s.behind_by | 0) > 0) {
    pill.classList.add('pill-update');
    pill.innerHTML = `<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-arrow-up"/></svg>Update to ${remote}`;
    pill.title = `You're on ${local}; latest is ${remote}. Click to pull the update.`;
    return;
  }
  // Last check errored (offline).
  if (s.error) {
    pill.classList.add('pill-dev');
    pill.textContent = `${local} · offline`;
    pill.title = `Couldn't reach github.com (${s.error}). Click to retry.`;
    return;
  }
  // Current with origin/main.
  if (s.checked_ts && (s.behind_by | 0) === 0) {
    pill.classList.add('pill-current');
    pill.innerHTML = `<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-check-bold"/></svg>Up to date · ${local}`;
    pill.title = `You're on ${local}, the latest version. Click to re-check now.`;
    return;
  }
  // First poll hasn't landed yet.
  pill.classList.add('pill-checking');
  pill.textContent = `Checking · ${local}`;
  pill.title = 'Checking for updates…';
}


// ---- The pop-up: updates announce themselves, broadcasts reach everyone ----
//
// Owner ruling 2026-09-01: a new version must be a POP-UP on app open, not a
// banner someone has to notice — and re-shown every page load until they
// update ("people who don't update will be annoyed by it every time they open
// the app" is the intended behaviour, verbatim). The broadcast is the
// maintainer's channel: BROADCAST.json on public main, fetched by the same
// 30-minute server poll, shown ONCE per message id, dismissed forever.
window._phModalOpen = false;
window._updModalShownThisLoad = false;
window._bcHandled = Object.create(null);

function _phModalShow(opts) {
  const wrap = document.getElementById('phModalWrap');
  if (!wrap || window._phModalOpen) return false;
  document.getElementById('phModalKicker').textContent = opts.kicker || '';
  document.getElementById('phModalKicker').className = opts.broadcast ? 'is-broadcast' : '';
  document.getElementById('phModalTitle').textContent = opts.title || '';
  document.getElementById('phModalBody').textContent = opts.body || '';
  const prim = document.getElementById('phModalPrimary');
  const sec = document.getElementById('phModalSecondary');
  prim.textContent = opts.primaryLabel || 'OK';
  prim.className = opts.broadcast ? 'is-broadcast' : '';
  prim.onclick = () => { _phModalClose(); if (opts.onPrimary) opts.onPrimary(); };
  if (opts.secondaryLabel) {
    sec.hidden = false;
    sec.textContent = opts.secondaryLabel;
    sec.onclick = () => { _phModalClose(); if (opts.onSecondary) opts.onSecondary(); };
  } else {
    sec.hidden = true;
  }
  wrap.hidden = false;
  window._phModalOpen = true;
  return true;
}

function _phModalClose() {
  const wrap = document.getElementById('phModalWrap');
  if (wrap) wrap.hidden = true;
  window._phModalOpen = false;
}

// The page's one analytics channel (v4.9.7): a closed set of (event, props)
// the server allowlists — how the update pop-up was answered, a broadcast
// seen, the Editor opened/exported. Fire-and-forget; never throws.
function _uiEvent(event, props) {
  try {
    const fd = new URLSearchParams(Object.assign({event}, props || {}));
    fetch('/analytics/ui', {method: 'POST', body: fd, keepalive: true}).catch(() => {});
  } catch (_) {}
}

function _maybeShowUpdateModal(st) {
  if (window._updModalShownThisLoad || window._phModalOpen) return;
  if (!st || st.error || st.suppress_reason || !st.checked_ts) return;
  if ((st.behind_by | 0) <= 0 || _versionRestartPending) return;
  const remote = st.remote_version || st.remote_short || 'a new version';
  const local = st.local_version || st.local_short || 'your build';
  window._updModalShownThisLoad = true;
  _uiEvent('update_prompt', {action: 'shown'});
  _phModalShow({
    kicker: 'Update available',
    title: `Phosphene ${remote} is out — you're on ${local}`,
    body: 'Updates are mostly bug fixes: renders that fail less and explain '
        + 'themselves better. Updating takes about a minute and keeps your '
        + 'queue, settings and models.',
    primaryLabel: 'Update now',
    secondaryLabel: 'Later',
    onPrimary: () => {
      _uiEvent('update_prompt', {action: 'update_now'});
      const go = document.getElementById('ubUpdate');
      if (go) { go.disabled = true; go.textContent = 'Updating…'; }
      versionDoPull({skipConfirm: true});
    },
    // Later = this page load only. The banner (per-version dismissal)
    // remains as the quieter in-session reminder.
    onSecondary: () => { _uiEvent('update_prompt', {action: 'later'}); },
  });
}

function _maybeShowBroadcastModal(st) {
  if (window._phModalOpen) return;
  const b = st && st.broadcast;
  if (!b || !b.id || window._bcHandled[b.id]) return;
  const seen = ((window._ubStarSettings || {}).broadcast_seen_ids) || [];
  if (seen.indexOf(b.id) !== -1) { window._bcHandled[b.id] = true; return; }
  window._bcHandled[b.id] = true;
  _phModalShow({
    broadcast: true,
    kicker: 'From the developer',
    title: b.title || 'A note from Phosphene',
    body: b.body || '',
    primaryLabel: 'Got it',
    onPrimary: () => {
      _uiEvent('broadcast_seen', {});
      const ids = seen.concat([b.id]).slice(-50);
      window._ubStarSettings = Object.assign(window._ubStarSettings || {},
                                             {broadcast_seen_ids: ids});
      _ubSaveSetting({broadcast_seen_ids: ids.join(',')});
    },
  });
}

// ---- Update banner -------------------------------------------------------
//
// The pill has always known we were behind; it just said so quietly, in the
// header, and required a click to even check. This says it once, loudly, and
// then gets out of the way permanently for that version.
//
// The star ask rides HERE and nowhere else on purpose: it is the one moment
// the user is already waiting on us, and it appears at most once per install.
// Clicking through or saying "already did" writes a local flag — there is no
// way to ask GitHub whether a given person starred the repo without making
// them log into GitHub inside a local video panel, and that trade is not worth
// a prompt. The analytics side is a COUNT of clicks and carries no identity,
// which is the same contract as every other event we send.
window._ubStarSettings = null;

function _ubRender(s) {
  const el = document.getElementById('updateBanner');
  if (!el) return;
  const behind = (s && !s.error && s.checked_ts && (s.behind_by | 0) > 0);
  const remote = (s && (s.remote_version || s.remote_short)) || '';
  // A pull already happened this session — the banner's job is done, the
  // restart pill takes over from here.
  if (!behind || _versionRestartPending) { el.hidden = true; return; }
  const cfg = window._ubStarSettings || {};
  if (String(cfg.update_banner_dismissed || '') === String(remote) && remote) {
    el.hidden = true; return;
  }
  const local = (s.local_version || s.local_short || 'your build');
  document.getElementById('ubTitle').textContent =
    `Phosphene ${remote} is available`;
  document.getElementById('ubSub').textContent =
    `You are on ${local}.` + (s.behind_more_than ? ' 30+ commits behind.' : '');
  const star = document.getElementById('ubStar');
  if (star) star.hidden = !!cfg.star_prompt_done;
  el.hidden = false;
}

// After a successful pull the banner stops being an offer and becomes the
// instruction. Returns true when it handled the message, so the caller can skip
// its alert fallback (a panel with the banner hidden still gets the modal).
function _ubRestartState(newVersion, requiresFullUpdate) {
  const el = document.getElementById('updateBanner');
  if (!el || el.hidden) return false;
  const star = document.getElementById('ubStar');
  if (star) star.hidden = true;
  const title = document.getElementById('ubTitle');
  const sub = document.getElementById('ubSub');
  const go = document.getElementById('ubUpdate');
  const later = document.getElementById('ubLater');
  if (title) title.textContent = `Updated to ${newVersion} — restart to finish`;
  if (sub) {
    sub.textContent = requiresFullUpdate
      ? 'This update touched Python dependencies, so use Pinokio\u2019s Update button (not just Stop and Start) so they reinstall.'
      : 'Click Stop, then Start in Pinokio. Your queue and settings are preserved.';
  }
  if (go) { go.hidden = true; }
  if (later) { later.textContent = 'Dismiss'; }
  el.classList.add('ub-done');
  return true;
}

async function _ubSaveSetting(patch) {
  Object.assign(window._ubStarSettings || (window._ubStarSettings = {}), patch);
  try {
    // /settings is form-encoded — a JSON body parses to nothing and returns
    // a cheerful ok:true while saving absolutely nothing.
    const form = new URLSearchParams();
    for (const [k, v] of Object.entries(patch)) form.set(k, String(v));
    await fetch('/settings', {
      method: 'POST',
      headers: {'Content-Type': 'application/x-www-form-urlencoded'},
      body: form.toString(),
    });
  } catch (e) { /* a failed write just means we ask again next boot */ }
}

function _ubWire() {
  const go = document.getElementById('ubUpdate');
  if (go) go.onclick = () => {
    _uiEvent('update_prompt', {action: 'banner_update'});
    go.disabled = true;
    go.textContent = 'Updating…';
    // Straight to the pull, with no confirm: this button already said what it
    // does. Reuses the pill's implementation, not a second copy of it.
    versionDoPull({skipConfirm: true});
  };
  const later = document.getElementById('ubLater');
  if (later) later.onclick = () => {
    _uiEvent('update_prompt', {action: 'banner_later'});
    const s = _versionState || {};
    _ubSaveSetting({update_banner_dismissed: String(s.remote_version || s.remote_short || '')});
    const el = document.getElementById('updateBanner');
    if (el) el.hidden = true;
  };
  const link = document.getElementById('ubStarLink');
  if (link) link.onclick = () => {
    _ubSaveSetting({star_prompt_done: true});
    const el = document.getElementById('ubStar');
    if (el) el.hidden = true;
    // Anonymous count, no identity — same contract as every other event.
    try { fetch('/star-click', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({via: 'link'})}); } catch (e) {}
  };
  const done = document.getElementById('ubStarDone');
  if (done) done.onclick = () => {
    _ubSaveSetting({star_prompt_done: true});
    const el = document.getElementById('ubStar');
    if (el) el.hidden = true;
    try { fetch('/star-click', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({via: 'already'})}); } catch (e) {}
  };
}
document.addEventListener('DOMContentLoaded', () => {
  _ubWire();
  // Same anonymous count as the banner's link, so the two places agree.
  const sl = document.getElementById('starLink');
  if (sl) sl.addEventListener('click', () => {
    _ubSaveSetting({star_prompt_done: true});   // retire the banner ask too
    try { fetch('/star-click', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({via: 'link'})}); } catch (e) {}
  });
  // The banner needs two settings before it can decide anything, and the
  // Settings modal may never be opened — so read them here rather than
  // relying on _settingsCache, which is only populated when that opens.
  fetch('/settings')
    .then(r => r.json())
    .then(d => {
      const st = (d && (d.settings || d)) || {};
      window._ubStarSettings = {
        update_banner_dismissed: st.update_banner_dismissed || '',
        star_prompt_done: !!st.star_prompt_done,
        broadcast_seen_ids: st.broadcast_seen_ids || [],
      };
      if (_versionState) { try { _ubRender(_versionState); } catch (e) {} }
    })
    .catch(() => {});
});

// ---- Health chip -----------------------------------------------------------
// The pills still exist and are still written by the same updaters; at boot
// each is MOVED into its row. The chip is DERIVED from them, so whatever turns
// memPill red turns the chip red by construction, rather than by a second
// opinion that could disagree with the first.
window._hcOpen = false;

function _hcSeverity(el) {
  if (!el) return 0;
  if (el.classList.contains('pill-danger')) return 2;
  if (el.classList.contains('pill-warn')) return 1;
  return 0;
}

function _hcRelocate() {
  const pop = document.getElementById('healthPop');
  if (!pop) return;
  pop.querySelectorAll('.hc-row').forEach(row => {
    const pill = row.dataset.pill && document.getElementById(row.dataset.pill);
    if (pill && pill.parentElement !== row) row.appendChild(pill);
  });
  if (pop.parentElement !== document.body) document.body.appendChild(pop);
}

function closeHealthPop() {
  const pop = document.getElementById('healthPop');
  if (pop) pop.hidden = true;
  const chip = document.getElementById('healthChip');
  if (chip) chip.setAttribute('aria-expanded', 'false');
  window._hcOpen = false;
}

function toggleHealthPop() {
  const pop = document.getElementById('healthPop');
  const chip = document.getElementById('healthChip');
  if (!pop || !chip) return;
  if (window._hcOpen) { closeHealthPop(); return; }
  updateHealthChip();
  const r = chip.getBoundingClientRect();
  pop.hidden = false;                     // measure before placing
  const w = pop.getBoundingClientRect().width;
  pop.style.top = (r.bottom + 6) + 'px';
  pop.style.left = Math.max(8, Math.min(r.right - w, window.innerWidth - w - 8)) + 'px';
  chip.setAttribute('aria-expanded', 'true');
  window._hcOpen = true;
}

function updateHealthChip() {
  const chip = document.getElementById('healthChip');
  const face = document.getElementById('hcFace');
  if (!chip || !face) return;
  const mem = document.getElementById('memPill');
  const models = document.getElementById('modelsPill');
  const helper = document.getElementById('helperPill');
  const worst = [mem, models, helper].reduce((a, el) => Math.max(a, _hcSeverity(el)), 0);
  chip.classList.toggle('is-warn', worst === 1);
  chip.classList.toggle('is-danger', worst === 2);
  const clean = el => (el ? el.textContent : '').replace(/\s+/g, ' ').trim();
  if (worst > 0) {
    // Name the thing that is wrong. "Attention" tells the user to go looking.
    const bad = [models, helper, mem].find(el => _hcSeverity(el) === worst);
    face.textContent = clean(bad) || 'needs attention';
    face.dataset.short = 'attention';
  } else {
    face.textContent = clean(mem) || 'all good';
    // The narrow-header face (review 2026-09-02): below 1400px the chip
    // shows just the percentage — the full readout was being CLIPPED to
    // "15/64" at Pinokio's window width. CSS swaps to data-short.
    face.dataset.short = ((face.textContent.match(/(\d+)%/) || [])[1] || '') ? (face.textContent.match(/(\d+)%/)[1] + '%') : (face.textContent.split(/[\s·]+/)[0] || '');
  }
  document.querySelectorAll('#healthPop .hc-row').forEach(row => {
    const pill = row.querySelector('.pill');
    const gone = !pill || pill.style.display === 'none';
    row.classList.toggle('is-empty', gone);
    if (gone) return;
    const label = row.querySelector('.hc-label');
    if (!label) return;
    if (!label.dataset.word) label.dataset.word = label.textContent.trim();
    const first = (pill.textContent || '').trim().toLowerCase().split(/[\s·]+/)[0] || '';
    row.classList.toggle('is-selfnamed', first === label.dataset.word.toLowerCase());
  });
}

document.addEventListener('DOMContentLoaded', () => {
  _hcRelocate();
  const chip = document.getElementById('healthChip');
  if (chip) chip.onclick = (ev) => { ev.stopPropagation(); toggleHealthPop(); };
  updateHealthChip();
});
document.addEventListener('click', (ev) => {
  if (!window._hcOpen) return;
  if (ev.target.closest('#healthPop') || ev.target.closest('#healthChip')) return;
  closeHealthPop();
}, true);
document.addEventListener('keydown', (ev) => {
  if (ev.key === 'Escape' && window._hcOpen) closeHealthPop();
});
window.addEventListener('resize', closeHealthPop);

// One click — does the right thing for the current state. Magic button.
async function panelRestart() {
  const pill = document.getElementById('versionPill');
  const say = (t) => { if (pill) pill.textContent = t; };
  try {
    const r = await fetch('/restart', {method: 'POST'});
    const d = await r.json().catch(() => ({}));
    if (r.status === 409) {
      phosToast(d.error || 'A render is running — stop it first.', 'warn');
      return;
    }
    if (!d.ok) { phosToast(d.error || 'Restart failed.', 'error'); return; }
  } catch (e) {
    // The process may have exec'd before the response reached us. That is a
    // SUCCESSFUL restart, not a failure — fall through to polling.
  }
  say('restarting…');
  // Poll until the new image answers, then reload so the browser gets the
  // new HTML too (the whole point: the old page is the stale code).
  const deadline = Date.now() + 60000;
  while (Date.now() < deadline) {
    await new Promise(r => setTimeout(r, 700));
    try {
      const r = await fetch('/version', {cache: 'no-store'});
      if (r.ok) { location.reload(); return; }
    } catch (e) { /* still down */ }
  }
  phosToast('Panel did not come back within 60s — start it from Pinokio.', 'error');
}

async function versionPillClick() {
  if (_versionRestartPending) {
    // Educational click: tell the user what's needed.
    const s = _versionState || {};
    const tip = s.pull_requires_full_update
      ? "Pulled. Because this update touched Python deps / patches, use Pinokio's Update button (it reinstalls + reapplies patches). After that click Start."
      : "Pulled. Click Stop, then Start in Pinokio to apply (your queue and settings are preserved).";
    alert(tip);
    return;
  }
  if ((_versionState || {}).stale_process) {
    // Actually restart, rather than telling the user to go and do it. The
    // panel re-execs itself: same pid, same port, code from disk. We poll
    // until it answers again and then reload, so the click finishes the job
    // the button's own label promised.
    await panelRestart();
    return;
  }
  if (false) {
    const s = _versionState || {};
    alert(`Phosphene ${s.disk_version || s.disk_short || '(newer)'} is on `
      + `disk, but this running panel loaded `
      + `${s.local_version || s.local_short || 'an older build'} before the `
      + `update landed.\n\nClick Stop, then Start in Pinokio (or restart `
      + `the panel process) to load it. Your queue and settings are `
      + `preserved.`);
    return;
  }
  const s = _versionState || {};
  if (s.suppress_reason) {
    // 2026-05-21 — dev-pill click now copies a paste-ready debug string
    // to clipboard. Mr Bizarro report: Reddit dev users couldn't say
    // exactly which commit they were on, leading to "I'm on 2.0.5"
    // confusion when they had pulled fresh code. One click gives them
    // a string they can paste into a bug report.
    const debug = `Phosphene ${s.local_version || '?'} `
      + `· ${s.local_branch || 'branch?'} `
      + `· ${s.local_short || 'sha?'}`
      + (s.local_commit_date ? ` (${s.local_commit_date})` : '')
      + (s.local_dirty ? ' · dirty tree' : '')
      // The whole point of this string is "which build produced the bug".
      // If the tree has moved on, the panel is running neither what the
      // header first suggested nor what a maintainer would check out.
      + (s.stale_process ? ` · NOT RESTARTED, disk is at ${s.disk_short || '?'}` : '');
    try {
      await navigator.clipboard.writeText(debug);
      alert(`Update check is paused: ${s.suppress_reason}.\n\n`
        + `Copied this debug string to your clipboard so you can paste `
        + `it into a bug report:\n\n${debug}`);
    } catch (e) {
      alert(`Update check is paused: ${s.suppress_reason}.\n\n`
        + `Your build:\n${debug}\n\n`
        + `Phosphene only checks GitHub when you're on a clean main `
        + `branch. Commit your local changes (or switch back to main) `
        + `to re-enable updates.`);
    }
    return;
  }
  // Behind: pull the update.
  if (!s.error && s.checked_ts && (s.behind_by | 0) > 0) {
    await versionDoPull();
    return;
  }
  // Current OR error OR pre-first-poll: re-check now.
  await versionDoRefresh();
}

async function versionDoRefresh() {
  const pill = document.getElementById('versionPill');
  pill.classList.add('pill-busy');
  pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px;animation:phSpin 1.2s linear infinite"><use href="#ph-arrow-clockwise-bold"/></svg>checking…';
  try {
    const r = await fetch('/version/check', { method: 'POST' });
    const data = await r.json();
    if (data && data.state) _versionState = data.state;
  } catch (e) {
    // Leave _versionState as-is so the pill returns to the prior render
    // instead of flashing to "unknown".
  }
  pill.classList.remove('pill-busy');
  renderVersionPill();
}

async function versionDoPull(opts) {
  opts = opts || {};
  const s = _versionState || {};
  const target = _versionRemoteLabel(s);
  const local = _versionDisplayLabel(s);
  // A button that says "Update now" does not need to ask whether you meant it.
  // The confirm stays for the version PILL, which is a small ambiguous target
  // that also does four other things depending on state.
  if (!opts.skipConfirm) {
    const ok = confirm(
      `Pull update from ${local} → ${target}?\n\n` +
      `This runs git pull on your phosphene install. After it succeeds, ` +
      `you'll need to click Stop, then Start in Pinokio to load the new code. ` +
      `Your queue and settings are preserved across restarts.`
    );
    if (!ok) return;
  }
  const pill = document.getElementById('versionPill');
  pill.classList.add('pill-busy');
  pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px;animation:phSpin 1.2s linear infinite"><use href="#ph-arrow-clockwise-bold"/></svg>pulling…';
  // Tell poll() not to flash the offline banner during this call —
  // /version/pull is single-threaded server-side and blocks /status for
  // ~30s while git fetch + git pull + remote-check run. Without this
  // suppression the user sees an "offline → reconnected" flash at the
  // tail of every successful Update click.
  window._suppressOfflineBanner = true;
  try {
    const r = await fetch('/version/pull', { method: 'POST' });
    const data = await r.json();
    if (data && data.state) _versionState = data.state;
    if (!r.ok || !data.ok) {
      pill.classList.remove('pill-busy');
      renderVersionPill();
      alert(`Pull failed:\n\n${(data && data.error) || 'unknown error'}\n\n` +
            `Tip: try the full Pinokio Update button instead — it also handles ` +
            `cases where you have local changes that block a fast-forward.`);
      return;
    }
    _versionRestartPending = true;
    pill.classList.remove('pill-busy');
    renderVersionPill();
    const newVersion = (data.state && (data.state.pull_pulled_to_version || data.state.pull_pulled_to_short)) || 'new version';
    const fullUpdateNote = data.state && data.state.pull_requires_full_update
      ? `\n\n⚠ This update touched Python dependencies / patches. Use ` +
        `Pinokio's Update button (not just Stop+Start) so deps reinstall.`
      : '';
    // Inline, not a modal: the banner is already on screen and is where the
    // user is looking. An alert here was the second click in "I had to click
    // twice" — the first pulled, the second only told you what to do next.
    if (typeof _ubRestartState === 'function'
        && _ubRestartState(newVersion, !!(data.state && data.state.pull_requires_full_update))) {
      // handled inline
    } else {
      alert(`Pulled to ${newVersion}.\n\nClick Stop, then Start in Pinokio to apply.${fullUpdateNote}`);
    }
  } catch (e) {
    pill.classList.remove('pill-busy');
    renderVersionPill();
    alert(`Pull failed: ${e.message || e}`);
  } finally {
    // Reset the suppression flag so future genuine outages still surface
    // the banner. Also clear the failure counter so we don't fire on the
    // very next legit poll because of stale state from the pull window.
    window._suppressOfflineBanner = false;
    _POLL_FAILS = 0;
    // Force one immediate clean poll — also clears any latent banner
    // that snuck through (e.g. if the page was on a slow tab and missed
    // a couple seconds of state).
    _setOfflineBanner(false);
    if (typeof poll === 'function') poll();
  }
}

// Boot: first /version read happens 2 seconds after DOM ready (gives the
// panel's startup-delay thread time to complete its first remote check),
// then every 5 minutes thereafter.
setTimeout(refreshVersionPill, 2000);
setInterval(refreshVersionPill, 5 * 60 * 1000);

// ====== Modal reliability scaffold ======
// One global scaffold for all 8 modals on the panel (.models-modal,
// .model-browser-modal, .expand-lightbox). Covers three things the
// individual open/close functions didn't:
//   1. Esc closes the topmost-visible modal.
//   2. Tab/Shift-Tab cycles focus inside the modal (no escape into
//      the form behind it).
//   3. body.modal-open is toggled whenever any modal is visible so
//      CSS can lock background scroll.
// No need to patch each open/close fn — a MutationObserver watches
// inline style changes on the modals themselves; whoever toggles
// display gets the side-effects for free.
(function _phosModalScaffold() {
  // .modal-bg.show covers the batch modal (and any future modal that
  // uses the same .modal-bg + .show idiom). Including it here so Esc /
  // Tab focus-trap / body.modal-open scroll-lock all work for it too.
  const MODAL_SEL = '.models-modal, .model-browser-modal, .expand-lightbox, .modal-bg.show';
  const isVisible = el => {
    const d = el.style.display;
    // Some modals open by adding an .open class (.model-browser-modal);
    // others toggle the inline style. Cover both.
    if (el.classList.contains('open')) return true;
    return d === 'flex' || d === 'block';
  };
  const visibleModals = () =>
    Array.from(document.querySelectorAll(MODAL_SEL)).filter(isVisible);
  const topVisible = () => {
    const v = visibleModals();
    return v.length ? v[v.length - 1] : null;
  };
  const FOCUSABLE = 'button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const refreshScrollLock = () => {
    document.body.classList.toggle('modal-open', visibleModals().length > 0);
  };
  // Wire a single MutationObserver per modal element. Cheaper than a
  // global subtree observer and avoids the cost of body-wide attribute
  // tracking. Lazy — modals added later (none today, but defensive)
  // need to re-register.
  document.querySelectorAll(MODAL_SEL).forEach(el => {
    new MutationObserver(refreshScrollLock).observe(el, {
      attributes: true,
      attributeFilter: ['style', 'class'],
    });
  });
  refreshScrollLock();

  // Esc closes the topmost modal. We find the close button inside it
  // and click it (preserves any per-modal cleanup logic — settings
  // save, civitai abort, etc.). Fall back to hiding the element if no
  // close button is found.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    const top = topVisible();
    if (!top) return;
    e.preventDefault();
    e.stopPropagation();
    const closeBtn =
      top.querySelector('button[onclick*="close"]') ||
      top.querySelector('.close-btn') ||
      top.querySelector('.expand-close');
    if (closeBtn) closeBtn.click();
    else { top.style.display = 'none'; top.classList.remove('open'); }
  });

  // Focus trap — when a modal is visible, Tab cycles inside it.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    const top = topVisible();
    if (!top) return;
    const focusables = Array.from(top.querySelectorAll(FOCUSABLE))
      .filter(el => el.offsetWidth > 0 || el.offsetHeight > 0);
    if (!focusables.length) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement;
    if (e.shiftKey && (active === first || !top.contains(active))) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && (active === last || !top.contains(active))) {
      e.preventDefault();
      first.focus();
    }
  });
})();


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
Object.assign(globalThis, {
  renderVersionPill, updateHealthChip, versionPillClick, _uiEvent,
});
