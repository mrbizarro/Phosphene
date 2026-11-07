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
  // Reconcile the client-only restart flag against the server's authoritative
  // state. _versionRestartPending is set after a pull that moved the tree
  // (~line 791) and lives purely in this page's memory: nothing ever cleared
  // it, and Pinokio reuses the same webview across an app Stop -> Start, so
  // the flag — and the "Restart Phosphene" pill — outlived the restart that
  // was supposed to satisfy it. Users could only escape it by quitting
  // Pinokio entirely (the page finally torn down and reloaded), reported on
  // the back of issue #114. Once a pull has moved the tree the running
  // process reports stale_process=true until it restarts, so the flag can
  // only coexist with a healthy, current process AFTER the restart actually
  // happened — either this same page now polling the new process, or a fresh
  // server that no longer remembers the pull. Clear it then. We only touch
  // the client PULL flag; the server's own stale_process pill (a promote
  // landing under a long-lived panel) is untouched and still shows.
  if (_versionRestartPending && _versionState
      && !_versionState.stale_process
      && !(_versionState.post_update && _versionState.post_update.outcome === 'pending')
      && !(((_versionState.behind_by) | 0) > 0)) {
    _versionRestartPending = false;
  }
  renderVersionPill();
  try { _ubRender(_versionState); } catch (e) {}
  try { _maybeShowUpdateModal(_versionState); } catch (e) {}
  try { _maybeShowBroadcastModal(_versionState); } catch (e) {}
}

function _phEsc(t) {
  return String(t).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
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
  pill.classList.remove('pill-update','pill-current','pill-dev','pill-checking','pill-restart','pill-busy','pill-app');
  pill.style.display = '';

  // The Mac app updates the studio itself (Phosphene -> Check for Updates...)
  // and installs it from a release tarball: no git, nothing to pull, no
  // GitHub poll. The pill names the build - version and the commit the
  // tarball shipped - and a click copies it for a bug report.
  if (isMacApp() || s.app_updates) {
    pill.classList.add('pill-app');
    const sha = s.local_short || '';
    pill.innerHTML = _phEsc(local) + (sha ? `<span class="vp-detail"> · ${_phEsc(sha)}</span>` : '');
    pill.title = `Phosphene ${_versionBuildLabel(s.local_version, s.local_short)}. `
      + 'To update, use Phosphene \u2192 Check for Updates\u2026'
      + '\nClick to copy your build details for a bug report.';
    return;
  }

  const repair = s.post_update || {};
  if (repair.outcome === 'pending' && repair.state !== 'done') {
    pill.classList.add('pill-busy');
    pill.textContent = repair.state === 'failed' ? 'Retry update' : 'Preparing update';
    pill.title = repair.error || 'Waiting for dependency repair to finish.';
    return;
  }

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
    // SYS-29: the non-full-update case now self-restarts on click
    // (panelRestart(), same as the stale-process case below) instead of
    // asking for a manual Stop -> Start.
    pill.title = s.pull_requires_full_update
      ? `Pulled ${v}. This update touched dependencies — use Pinokio's Update button (not just Stop+Start).`
      : `Pulled ${v}. Click to restart and apply it.`;
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
    // The words sit in their own span so a narrow title bar can drop them and
    // keep the tick and the number (the tick already says "up to date").
    pill.innerHTML = `<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-check-bold"/></svg><span class="vp-state">Up to date · </span>${local}`;
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
// The one Phosphene loading mark, small: the header's own markup (footer mark), cloned.
// CSS .ph-mark-mini sets the size; Reduce Motion stops it like every other mark.
window.phosMiniMark = function phosMiniMark() {
  const src = document.querySelector('header .ph-mark');
  return '<span class="ph-mark ph-mark-mini" aria-hidden="true">' + (src ? src.innerHTML : '') + '</span>';
};
window._phModalOpen = false;
window._updModalShownThisLoad = false;
window._bcHandled = Object.create(null);

function _phModalShow(opts) {
  const wrap = document.getElementById('phModalWrap');
  if (!wrap || window._phModalOpen) return false;
  // `tone` generalizes the old broadcast-only styling: 'broadcast' (cyan,
  // maintainer notes) or 'danger' (red, a destructive confirm — Stop
  // mid-render and friends). opts.broadcast still works for callers that
  // predate this.
  const tone = opts.tone || (opts.broadcast ? 'broadcast' : '');
  const toneClass = tone ? 'is-' + tone : '';
  document.getElementById('phModalKicker').textContent = opts.kicker || '';
  document.getElementById('phModalKicker').className = toneClass;
  document.getElementById('phModalTitle').textContent = opts.title || '';
  document.getElementById('phModalBody').textContent = opts.body || '';
  const prim = document.getElementById('phModalPrimary');
  const sec = document.getElementById('phModalSecondary');
  prim.textContent = opts.primaryLabel || 'OK';
  prim.className = toneClass;
  prim.onclick = () => { _phModalClose(); if (opts.onPrimary) opts.onPrimary(); };
  if (opts.secondaryLabel) {
    sec.hidden = false;
    sec.textContent = opts.secondaryLabel;
    sec.onclick = () => { _phModalClose(); if (opts.onSecondary) opts.onSecondary(); };
  } else {
    sec.hidden = true;
  }
  // A confirm the user dismisses with Escape or a click outside must count
  // as "no" (onCancel), never silently vanish leaving the caller thinking
  // its onPrimary/onSecondary will still fire.
  wrap.onclick = (ev) => {
    if (ev.target === wrap) { _phModalClose(); if (opts.onCancel) opts.onCancel(); }
  };
  wrap._cancelModal = () => { _phModalClose(); if (opts.onCancel) opts.onCancel(); };
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

// install_step `first_use` (growth pass, 2026-10-19): the first click or key
// press anyone makes on this page, once per install, ever. Pinokio auto-runs
// Start when the ~37 GB install finishes, so a panel can boot with nobody in
// front of it; this is what tells "nobody ever looked" from "looked and left".
// A hover or a scroll is not a person deciding to use the app, so only
// pointerdown/keydown count. At most one localhost POST per page load; the
// SERVER owns once-per-install (_analytics_install_step), deliberately not
// localStorage: every install on a Mac is served from the same origin, so a
// browser-side flag would silence a reinstall's new install id for good.
function armFirstUseReport() {
  const opts = {capture: true, passive: true};
  const fire = () => {
    document.removeEventListener('pointerdown', fire, opts);
    document.removeEventListener('keydown', fire, opts);
    _uiEvent('install_step', {step: 'first_use'});
  };
  document.addEventListener('pointerdown', fire, opts);
  document.addEventListener('keydown', fire, opts);
}

function _maybeShowUpdateModal(st) {
  if (window._updModalShownThisLoad || window._phModalOpen) return;
  if (!st || st.error || st.suppress_reason || !st.checked_ts) return;
  if (isMacApp() || st.app_updates) return;     // the app owns updates
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
  const repair = (s && s.post_update) || {};
  if (!isMacApp() && !(s && s.app_updates) && repair.outcome === 'pending') {
    el.hidden = false;
    _ubRestartState(s.disk_version || s.pull_pulled_to_version || 'the candidate', false);
    return;
  }
  // Never under the Mac app: its own updater (Sparkle) owns updates.
  const behind = !isMacApp() && !(s && s.app_updates)
    && (s && !s.error && s.checked_ts && (s.behind_by | 0) > 0);
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
  if (title) title.textContent = `Version ${newVersion} is ready to start`;
  // SYS-29: this used to hide `go` and tell the user to go click Stop
  // then Start in Pinokio themselves -- a second, manual mechanism
  // sitting right next to panelRestart() (the one-click, 409-guarded
  // self-restart this same file already uses for the stale-process
  // case). No reason the banner's Update needed a human to finish what
  // it started: when the pull didn't touch dependencies, the SAME
  // button now finishes the job by calling panelRestart() directly.
  // When it DID touch dependencies, a self-restart genuinely can't
  // reinstall them (that needs Pinokio's own Update, which reinstalls
  // the venv) -- that's the one case still asking for a manual step,
  // and it says exactly why.
  // Beta, after 4.17.4: the pull is followed by post_update.sh, run by the
  // panel itself (the step Pinokio's Update runs). Until it is done the
  // banner says so and Restart waits; a failure names why.
  const pu = ((_versionState || {}).post_update) || {};
  if (pu.pending || pu.active || pu.state === 'failed') {
    if (title) title.textContent = pu.state === 'failed'
      ? 'The update did not finish'
      : `Preparing version ${newVersion}...`;
    if (sub) sub.textContent = pu.state === 'failed'
      ? (pu.error || 'post_update.sh failed') + ' \u2014 use Pinokio\u2019s Update (Stop, then Update), or try again.'
      : 'Installing what the new version needs (the same step Pinokio\u2019s Update runs). '
        + 'Restart becomes available when it finishes. ' + ((pu.log || []).slice(-1)[0] || '');
    if (go) {
      go.hidden = pu.state !== 'failed';
      go.disabled = false;
      go.textContent = 'Try again';
      go.onclick = async () => {
        go.disabled = true;
        try { await fetch('/version/finish', { method: 'POST' }); } catch (e) {}
        _ubFollowPostUpdate(newVersion);
      };
    }
    if (pu.state !== 'failed') _ubFollowPostUpdate(newVersion);
  } else if (requiresFullUpdate && !pu.state) {
    if (sub) sub.textContent = 'This update touched Python dependencies, so use Pinokio\u2019s Update button (not just Stop and Start) so they reinstall.';
    if (go) go.hidden = true;
  } else {
    if (sub) sub.textContent = 'Your queue and settings are preserved across the restart.';
    if (go) {
      go.hidden = false;
      go.disabled = false;
      go.textContent = 'Restart now';
      go.onclick = async () => {
        _uiEvent('update_prompt', {action: 'banner_restart'});
        go.disabled = true;
        go.textContent = 'Restarting\u2026';
        // Codex UI-10: a refused restart (409 while a render runs) or a
        // failed one used to leave this button disabled on "Restarting…"
        // for good. panelRestart() says whether a restart is under way;
        // when it isn't, the same button is usable again.
        const started = await panelRestart();
        if (!started) {
          go.disabled = false;
          go.textContent = 'Restart now';
        }
      };
    }
  }
  if (later) { later.textContent = 'Dismiss'; }
  el.classList.add('ub-done');
  return true;
}

// Poll /version while post_update.sh runs; repaint the banner as it moves,
// and hand over to the Restart now state when it is done.
let _ubPostUpdateTimer = null;
function _ubFollowPostUpdate(newVersion) {
  clearTimeout(_ubPostUpdateTimer);
  _ubPostUpdateTimer = setTimeout(async () => {
    try {
      const r = await fetch('/version', { cache: 'no-store' });
      if (r.ok) _versionState = await r.json();
    } catch (e) { /* keep polling */ }
    const pu = ((_versionState || {}).post_update) || {};
    _ubRestartState(newVersion, !!((_versionState || {}).pull_requires_full_update) && !pu.state);
  }, 2000);
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
  // The header star opens a short thank-you first; the GitHub link lives inside it.
  if (sl) sl.addEventListener('click', (e) => {
    e.preventDefault();
    const ask = document.getElementById('starAsk');
    if (!ask) { window.open(sl.href, '_blank', 'noopener'); return; }
    ask.classList.add('show');
    document.getElementById('starAskGo')?.focus();
  });
  const go = document.getElementById('starAskGo');
  if (go) go.addEventListener('click', () => {
    _ubSaveSetting({star_prompt_done: true});   // retire the banner ask and the twinkle
    sl?.classList.remove('is-calling');
    try { fetch('/star-click', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({via: 'link'})}); } catch (e) {}
    setTimeout(starAskClose, 300);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && document.getElementById('starAsk')?.classList.contains('show')) starAskClose(); });
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
      // Until they have starred, the header star twinkles now and then (CSS, reduced-motion aware).
      if (!st.star_prompt_done) document.getElementById('starLink')?.classList.add('is-calling');
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
    // VC-32: this used to be the bare word "attention" below 1400px — no
    // indication of WHAT needs it, on a chip whose whole job is a one-glance
    // read. Name the row instead ("models"/"helper"/"memory"), same as the
    // full-width text does; only fall back to the old word if none of the
    // three pills is even findable (shouldn't happen, but the chip must
    // never render nothing).
    const badLabel = bad === models ? 'models' : bad === helper ? 'helper'
                    : bad === mem ? 'memory' : 'attention';
    face.dataset.short = badLabel;
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
// Returns false when no restart happened (refused, failed, or the panel did
// not come back) so a caller's own button can be re-armed (UI-10); on success
// the page reloads.
async function panelRestart() {
  const pill = document.getElementById('versionPill');
  const say = (t) => { if (pill) pill.textContent = t; };
  const expectedCandidate = ((_versionState || {}).post_update || {}).candidate;
  try {
    const r = await fetch('/restart', {method: 'POST'});
    const d = await r.json().catch(() => ({}));
    if (r.status === 409) {
      phosToast(d.error || 'A render is running — stop it first.', 'warn');
      return false;
    }
    if (!d.ok) { phosToast(d.error || 'Restart failed.', 'error'); return false; }
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
      if (r.ok) {
        const state = await r.json();
        const result = state.post_update || {};
        if (!expectedCandidate || (result.candidate === expectedCandidate && result.outcome === 'ok')) {
          location.reload(); return true;
        }
      }
    } catch (e) { /* still down */ }
  }
  phosToast(distText('Panel did not come back within 60s — start it from Pinokio.',
                     'The studio did not come back within 60s — use Studio \u2192 Restart Engine.'), 'error');
  return false;
}

// The pill's click inside the Mac app: never a git pull. Name the build (for a
// bug report) and point at the app's own updater.
async function _appVersionPillClick() {
  const s = _versionState || {};
  const debug = `Phosphene ${s.local_version || '?'} · mac-app`
    + (phEdition() ? ` · ${phEdition()}` : '')
    + ` · ${s.local_short || 'sha?'}`;
  let copied = false;
  try { await navigator.clipboard.writeText(debug); copied = true; } catch (e) {}
  phosToast((copied ? `Copied "${debug}". ` : `${debug}. `)
    + 'To update, use Phosphene \u2192 Check for Updates\u2026', 'info');
}

async function versionPillClick() {
  if (isMacApp() || (_versionState || {}).app_updates) return _appVersionPillClick();
  const recovery = (_versionState || {}).post_update || {};
  if (recovery.outcome === 'pending' && recovery.state === 'failed') {
    const r = await fetch('/version/finish', {method: 'POST'});
    const d = await r.json();
    if (!r.ok || !d.ok) phosToast(d.error || 'Update could not resume.', 'warn');
    await refreshVersionPill();
    return;
  }
  if (_versionRestartPending) {
    const s = _versionState || {};
    const _pu = s.post_update || {};
    if (_pu.pending || _pu.active || _pu.state === 'failed') {
      phosToast(_pu.state === 'failed'
        ? 'The update did not finish: ' + (_pu.error || '') + ' Use Pinokio\u2019s Update.'
        : 'Finishing the update first \u2014 restart becomes available when it is done.', 'warn');
      return;
    }
    if (s.pull_requires_full_update && !_pu.state) {
      // Genuinely can't self-restart into this one -- dependencies /
      // patches changed, and only Pinokio's own Update reinstalls those.
      alert("Pulled. Because this update touched Python deps / patches, use Pinokio's Update button (it reinstalls + reapplies patches). After that click Start.");
      return;
    }
    // SYS-29: same fix as the update banner's button -- finish the update
    // the pill already started instead of telling the user to go click
    // Stop then Start themselves.
    await panelRestart();
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
  pill.innerHTML = phosMiniMark() + 'checking…';
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
  if (isMacApp()) {               // the server refuses it too (409 app_updates)
    phosToast('To update Phosphene, use Phosphene \u2192 Check for Updates\u2026', 'info');
    return;
  }
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
  pill.innerHTML = phosMiniMark() + 'pulling…';
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
            `Keep your local edits. Save or commit them before retrying Update.`);
      return;
    }
    // 4.17.5: a pull that moved nothing is not an update - no restart, no
    // "Updated to" banner (the restart came back on the same build and was
    // counted as a failed update). A stale process still gets its own pill.
    if (data.moved === false && !(data.state && data.state.post_update
        && data.state.post_update.outcome === 'pending') && !(data.state && data.state.stale_process)) {
      pill.classList.remove('pill-busy');
      renderVersionPill();
      const go = document.getElementById('ubUpdate');
      if (go) { go.disabled = false; go.textContent = 'Update now'; }
      try { _ubRender(_versionState); } catch (e) {}
      if (typeof phosToast === 'function') {
        phosToast('Already up to date - nothing to restart.', { kind: 'success' });
      }
      return;
    }
    _versionRestartPending = true;
    pill.classList.remove('pill-busy');
    renderVersionPill();
    const newVersion = (data.state && (data.state.pull_pulled_to_version || data.state.pull_pulled_to_short)) || 'new version';
    const _puNow = (data.state && data.state.post_update) || {};
    const fullUpdateNote = (_puNow.pending || _puNow.active)
      ? `\n\nPhosphene is now finishing the update in the background (the same ` +
        `step Pinokio's Update runs). Restart once the banner says it is done.`
      : data.state && data.state.pull_requires_full_update
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
  //
  // EVERY .modal-bg, not only the ones showing at startup (Codex UI-08). The
  // selector said `.modal-bg.show`, so the observers below were registered
  // only on dialogs already open when the page loaded — none — and isVisible()
  // did not know the `show` class at all: Storyboard's re-plan dialog and the
  // Editor's Generate-shot dialog open with classList.add('show'), so Escape,
  // the Tab trap and the scroll lock all ignored them.
  const MODAL_SEL = '.models-modal, .model-browser-modal, .expand-lightbox, .modal-bg, #phModalWrap';
  const isVisible = el => {
    if (el.id === 'phModalWrap') return !el.hidden;
    if (el.hidden) return false;
    const d = el.style.display;
    // Some modals open by adding an .open class (.model-browser-modal), the
    // .modal-bg family by adding .show; others toggle the inline style.
    if (el.classList.contains('open') || el.classList.contains('show')) return true;
    return d === 'flex' || d === 'block';
  };
  const visibleModals = () =>
    Array.from(document.querySelectorAll(MODAL_SEL)).filter(isVisible);
  const stack = [];
  let opener = null;
  let inertNodes = [];
  document.addEventListener('click', e => {
    opener = e.target.closest('button, a, input, [tabindex]') || document.activeElement;
  }, true);
  const topVisible = () => stack.length ? stack[stack.length - 1].el : null;
  window.phosTopModal = topVisible;
  const FOCUSABLE = 'summary, button:not([disabled]), [href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  const focusables = el => Array.from(el.querySelectorAll(FOCUSABLE))
    .filter(n => !n.closest('[inert]') && n.getClientRects().length);
  const focusFirst = el => {
    const target = focusables(el)[0] || el;
    if (target === el) el.tabIndex = -1;
    target.focus();
  };
  const refreshScrollLock = () => {
    const visible = visibleModals();
    let returnTo = null;
    for (let i = stack.length - 1; i >= 0; i--) {
      if (!visible.includes(stack[i].el)) {
        const closed = stack.splice(i, 1)[0];
        closed.el.style.zIndex = closed.zIndex;
        returnTo = closed.opener;
      }
    }
    for (const el of visible) {
      if (!stack.some(item => item.el === el)) {
        stack.push({el, opener: opener || document.activeElement, zIndex: el.style.zIndex});
        el.style.zIndex = String(10000 + stack.length);
      }
    }
    // Inert siblings along the whole ancestor path, including lower dialogs.
    for (const [node, value] of inertNodes) node.inert = value;
    inertNodes = [];
    const top = topVisible();
    if (top) {
      for (let node = top; node && node !== document.body; node = node.parentElement) {
        for (const sibling of node.parentElement.children) {
          if (sibling !== node) { inertNodes.push([sibling, sibling.inert]); sibling.inert = true; }
        }
      }
    }
    opener = null;
    document.body.classList.toggle('modal-open', !!top);
    // Polling may have replaced the opener while its child dialog was up.
    if (returnTo && !returnTo.isConnected && top) {
      const action = returnTo.getAttribute('onclick');
      returnTo = focusables(top).find(el =>
        (returnTo.id && el.id === returnTo.id) ||
        (action && el.getAttribute('onclick') === action)) || null;
    }
    if (returnTo && returnTo.isConnected && (!top || top.contains(returnTo))) returnTo.focus();
    if (top && !top.contains(document.activeElement)) focusFirst(top);
  };
  // Wire a single MutationObserver per modal element. Cheaper than a
  // global subtree observer and avoids the cost of body-wide attribute
  // tracking. Lazy — modals added later (none today, but defensive)
  // need to re-register.
  const watched = new WeakSet();
  const watch = el => {
    if (watched.has(el)) return;
    watched.add(el);
    new MutationObserver(refreshScrollLock).observe(el, {
      attributes: true,
      attributeFilter: ['style', 'class', 'hidden'],
    });
  };
  document.querySelectorAll(MODAL_SEL).forEach(watch);
  // A modal appended to <body> later (the batch modal) registers itself.
  // Direct children only — not the body-wide subtree observer ruled out above.
  new MutationObserver(recs => {
    for (const r of recs) {
      for (const n of (r.addedNodes || [])) {
        if (n.nodeType === 1 && n.matches && n.matches(MODAL_SEL)) { watch(n); refreshScrollLock(); }
      }
    }
  }).observe(document.body, { childList: true });
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
    e.stopImmediatePropagation();
    if (top._cancelModal) { top._cancelModal(); return; }
    // Case-insensitive: sbCloseReplan() / sbeGenClose() are the class-based
    // dialogs' own close functions and never matched a lower-case "close".
    const closeBtn =
      top.querySelector('button[onclick*="close"]') ||
      Array.from(top.querySelectorAll('button[onclick]'))
        .find(b => /close/i.test(b.getAttribute('onclick') || '')) ||
      top.querySelector('.close-btn') ||
      top.querySelector('.expand-close');
    if (closeBtn) closeBtn.click();
    else if (top.classList.contains('show') || top.classList.contains('open')) {
      // Class-opened: drop the class. An inline display:none here would
      // outrank `.modal-bg.show` and the dialog could never open again.
      top.classList.remove('show', 'open');
    } else { top.style.display = 'none'; }
  }, true);

  // Focus trap — when a modal is visible, Tab cycles inside it.
  document.addEventListener('keydown', e => {
    if (e.key !== 'Tab') return;
    const top = topVisible();
    if (!top) return;
    const controls = focusables(top);
    if (!controls.length) { e.preventDefault(); focusFirst(top); return; }
    const first = controls[0];
    const last = controls[controls.length - 1];
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
  renderVersionPill, updateHealthChip, versionPillClick, _uiEvent, armFirstUseReport,
  // SYS-19: closeHealthPop wasn't published -- openTierModal (settings.js)
  // and openModelsModal (preview.js) need it so clicking a health-popover
  // row closes the popover before its modal opens on top of it.
  closeHealthPop, starAskClose,
  // The update/broadcast pop-up is the panel's only real dialog component
  // (kicker/title/body/two buttons, one at a time via _phModalOpen) — VC-35's
  // fix is "one in-app confirm/undo component", not a second modal shape, so
  // Stop's confirm-with-cost (queue.js) reuses this rather than inventing one.
  _phModalShow, _phModalClose,
});

function starAskClose() { document.getElementById('starAsk')?.classList.remove('show'); }

// ---- The one-time "star us on GitHub" card (owner, 2 Nov 2026; v2 4 Nov) --
// After the fifth finished render the server sets `star_prompt_due` on
// /status (see _star_prompt_after_job). v2, learned from how T3 Code asks
// (our own code):
//   1. Ask the server first (/star-prompt/github): if this Mac's GitHub CLI
//      says the account already starred us, the card is retired unseen.
//   2. Wait until the person has been idle 30 s with the page visible and
//      focused. Any pointer, key, wheel or typing restarts the wait, so it
//      never lands mid-typing or mid-drag.
//   3. One button, "Star on GitHub | <live count>" (no number when unknown).
//      Where gh is signed in it stars in place and the card says thank you
//      with a short burst, then closes; otherwise it opens GitHub.
// The quiet close X and Escape record `not_now`. The card says `shown` when
// it really goes up, so it is never put up again on this Mac.
const STAR_CARD_URL = 'https://github.com/mrbizarro/Phosphene';
const STAR_CARD_IDLE_MS = 30000;
const STAR_CARD_THANKS_MS = 5000;
const STAR_CARD_ACTIVITY = ['pointerdown', 'pointermove', 'keydown', 'wheel',
                            'touchstart', 'input', 'compositionstart', 'focus'];
let _starCardUp = false;       // on screen
let _starCardArmed = false;    // due seen this session: checking or waiting
let _starCardDone = false;     // answered; the card only closes now
let _starCardInfo = null;      // {gh, starred, stars} from the server
function _starCardPost(action) {
  try {
    return fetch('/star-prompt', {method: 'POST', body: new URLSearchParams({action}),
                                  keepalive: true}).catch(() => {});
  } catch (_) { return Promise.resolve(); }
}
function _starCardCheck() {
  try {
    return fetch('/star-prompt/github', {cache: 'no-store'})
      .then((r) => (r && r.ok ? r.json() : null)).catch(() => null);
  } catch (_) { return Promise.resolve(null); }
}
function _starCardCount(n) {
  if (typeof n !== 'number' || !isFinite(n) || n < 0) return '';
  if (n < 1000) return String(Math.round(n));
  const k = n / 1000;
  return (k < 10 ? k.toFixed(1).replace(/\.0$/, '') : String(Math.round(k))) + 'k';
}
function _starCardSetCount(n) {
  const c = document.getElementById('starCardCount');
  if (!c) return;
  const t = _starCardCount(n);
  c.textContent = t;
  c.hidden = !t;
}
// Resolves once the page has been idle STAR_CARD_IDLE_MS while visible and
// focused. A timer that ends while the page is hidden or unfocused waits for
// the next focus / visibility change and starts over.
function _starCardWaitIdle() {
  return new Promise((resolve) => {
    let timer = null;
    const restart = () => {
      clearTimeout(timer);
      timer = setTimeout(() => {
        if (document.hidden || (typeof document.hasFocus === 'function' && !document.hasFocus())) return;
        for (const t of STAR_CARD_ACTIVITY) window.removeEventListener(t, restart, true);
        document.removeEventListener('visibilitychange', restart, true);
        resolve();
      }, STAR_CARD_IDLE_MS);
    };
    for (const t of STAR_CARD_ACTIVITY) window.addEventListener(t, restart, {capture: true, passive: true});
    document.addEventListener('visibilitychange', restart, true);
    restart();
  });
}
function _starCardShow() {
  const el = document.getElementById('starCard');
  if (!el || _starCardUp || _starCardDone) return;
  _starCardSetCount(_starCardInfo && _starCardInfo.stars);
  _starCardUp = true;
  el.hidden = false;
  _starCardPost('shown');
}
function starCardPoll(s) {
  if (_starCardArmed || _starCardUp || !s || !s.star_prompt_due) return;
  if (!document.getElementById('starCard')) return;
  _starCardArmed = true;
  return _starCardCheck().then((info) => {
    _starCardInfo = info || null;
    if (info && info.starred === true) {           // they already starred
      _starCardDone = true;
      return _starCardPost('already');
    }
    return _starCardWaitIdle().then(_starCardShow);
  });
}
function _starCardClose() {
  const el = document.getElementById('starCard');
  if (el) { el.hidden = true; el.classList.remove('is-thanks'); }
  _starCardUp = false;
}
function starCardAnswer(action) {
  if (!_starCardUp) { _starCardClose(); return; }
  if (_starCardDone) { _starCardClose(); return; }  // the thank-you, closed early
  _starCardDone = true;
  _starCardClose();
  if (action === 'star') {
    document.getElementById('starLink')?.classList.remove('is-calling');
    if (typeof openExternal === 'function') openExternal(STAR_CARD_URL);
    else window.open(STAR_CARD_URL, '_blank', 'noopener');
  } else if (action === 'waitlist') {
    const url = 'https://phosphene.video/pricing/';
    if (typeof openExternal === 'function') openExternal(url);
    else window.open(url, '_blank', 'noopener');
  } else {
    action = 'not_now';
  }
  _starCardPost(action);
}
// The one button. In place where gh is signed in, GitHub otherwise (and on
// any failure). Only ever on the person's click.
async function starCardStar() {
  if (!_starCardUp || _starCardDone) return;
  const btn = document.getElementById('starCardStar');
  if (_starCardInfo && _starCardInfo.gh) {
    const label = btn && btn.querySelector('.star-card-label');
    if (btn) btn.disabled = true;
    if (label) label.textContent = 'Starring…';
    let r = null;
    try {
      r = await fetch('/star-prompt/star', {method: 'POST',
        headers: {'Content-Type': 'application/json'}, body: '{}'})
        .then((x) => x.json()).catch(() => null);
    } catch (_) { r = null; }
    if (btn) btn.disabled = false;
    if (label) label.textContent = 'Star on GitHub';
    if (r && r.ok) { _starCardThank(r.stars); return; }
  }
  starCardAnswer('star');
}
function _starCardThank(stars) {
  _starCardDone = true;                           // the server recorded star_inapp
  const el = document.getElementById('starCard');
  if (!el) return;
  _starCardSetCount(stars);
  el.classList.add('is-thanks');
  const ask = el.querySelector('.star-card-ask');
  if (ask) ask.hidden = true;
  const ty = document.getElementById('starCardThanks');
  if (ty) ty.hidden = false;
  _starCardBurst(el);
  setTimeout(_starCardClose, STAR_CARD_THANKS_MS);
}
// A short burst of little stars and sparks out of the lamp's bulb (the lamp
// lights up as the card says thank you), drawn on the card's own canvas, no
// library. Skipped under prefers-reduced-motion.
function _starCardBurst(card) {
  const reduce = typeof matchMedia === 'function'
    && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const cv = document.getElementById('starCardBurst');
  if (reduce || !cv || !cv.getContext) return false;
  const rect = card.getBoundingClientRect();
  const bulb = card.querySelector && card.querySelector('.lamp-bulb');
  const bb = bulb && bulb.getBoundingClientRect ? bulb.getBoundingClientRect() : null;
  const b = bb && bb.width ? bb : {left: rect.left + rect.width / 2 - 1, top: rect.top + rect.height / 2 - 1, width: 2, height: 2};
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  cv.width = Math.round(rect.width * dpr); cv.height = Math.round(rect.height * dpr);
  const ctx = cv.getContext('2d');
  if (!ctx) return false;
  ctx.scale(dpr, dpr);
  const ox = b.left - rect.left + b.width / 2, oy = b.top - rect.top + b.height / 2;
  const colors = ['#ffd391', '#ffe5b4', '#5eeaff', '#b14aff', '#f4f4f5'];
  const parts = [];
  for (let i = 0; i < 44; i++) {
    const a = (Math.PI * 2 * i) / 44 + Math.random() * 0.3;
    const v = 1.8 + Math.random() * 3.6;
    parts.push({x: ox, y: oy, vx: Math.cos(a) * v * 1.25, vy: Math.sin(a) * v - 1.6,
                r: 1.2 + Math.random() * 2, c: colors[i % colors.length],
                star: i % 3 === 0, spin: Math.random() * 6});
  }
  const t0 = performance.now(), life = 1400;
  const star = (x, y, r, rot) => {
    ctx.beginPath();
    for (let k = 0; k < 10; k++) {
      const rr = k % 2 ? r * 0.45 : r, an = rot + (Math.PI * k) / 5;
      ctx.lineTo(x + Math.cos(an) * rr, y + Math.sin(an) * rr);
    }
    ctx.closePath(); ctx.fill();
  };
  const frame = (now) => {
    const t = (now - t0) / life;
    ctx.clearRect(0, 0, rect.width, rect.height);
    if (t >= 1) return;
    ctx.globalAlpha = 1 - t * t;
    for (const p of parts) {
      p.vy += 0.16; p.vx *= 0.985; p.vy *= 0.985;
      p.x += p.vx; p.y += p.vy;
      ctx.fillStyle = p.c;
      if (p.star) star(p.x, p.y, p.r * 1.9, p.spin + t * 6);
      else { ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2); ctx.fill(); }
    }
    requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
  return true;
}
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !_starCardUp) return;
  const el = document.getElementById('starCard');
  if (el && el.contains(document.activeElement)) starCardAnswer('not_now');
});
Object.assign(globalThis, { starCardPoll, starCardAnswer, starCardStar });
