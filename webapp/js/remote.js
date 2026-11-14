// Remote Control page (left menu). Public Studio UI only: it asks /remote/state what the
// Pro-side module says and draws it. Pairing, encryption and the Pro check are not here.

const $ = (id) => document.getElementById(id);
let poll = null;
let state = null;
let offer = null;          // {url, expires_at} while a pairing code is showing
let qrLib = null;
let drawnUrl = '';
let busy = false;

function rmLoadQr() {
  if (qrLib) return qrLib;
  qrLib = new Promise((resolve, reject) => {
    if (window.qrcode) return resolve(window.qrcode);
    const s = document.createElement('script');
    s.src = '/webapp/vendor/qrcode/qrcode.js';
    s.onload = () => resolve(window.qrcode);
    s.onerror = () => reject(new Error('qr'));
    document.head.appendChild(s);
  });
  return qrLib;
}

async function rmApi(path, form) {
  const opts = form === undefined ? {} : {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams(form).toString(),
  };
  const r = await fetch('/remote/' + path, opts);
  let doc = {};
  try { doc = await r.json(); } catch (e) { /* keep {} */ }
  return doc;
}

function rmSetErr(msg) {
  const el = $('rmError');
  if (!el) return;
  el.hidden = !msg;
  el.textContent = msg || '';
}

function rmAgo(ts) {
  if (!ts) return T('remote.not_seen_yet', 'not seen yet');
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return T('remote.just_now', 'just now');
  if (i18nLang() !== 'en' && s >= 60) return i18nAgo(s);
  if (s < 3600) return Math.round(s / 60) + T('remote.min_ago', ' min ago');
  if (s < 86400) return Math.round(s / 3600) + T('remote.h_ago', ' h ago');
  return Math.round(s / 86400) + T('remote.d_ago', ' d ago');
}

// Pro on this Mac: the Remote module says so, or the studio's own /pro/status did (proStatusRefresh
// sets <html data-pro>). Either way the page shows status, never "comes with Pro".
function rmIsMember(st) {
  return !!(st && st.pro) || !!document.documentElement.dataset.pro;
}

// The member's setup state while the Remote module is not loaded: what is happening, what went
// wrong in plain words, and the one next action. Never a dead "not set up".
function rmWaitView(st) {
  const parts = st.parts || {};
  let title = T('remote.remote_control_is_being_set', 'Remote Control is being set up');
  let body = T('remote.your_pro_licence_is_active', 'Your Pro licence is active. Checking for the Remote Control components.');
  let err = '';
  let retry = false, settings = false;
  let start = false;
  if (parts.managed_by === 'mac_app' && st.pro) {
    // Pro was switched on after the engine started: the engine that is running is the Free one
    // and Remote lives in the Pro one. The app starts it for you, no restart of Phosphene needed.
    title = T('remote.set_up_remote', 'Set up Remote');
    body = T('remote.pro_is_on_one_step', 'Pro is on. One step left: start Remote. Phosphene restarts its engine for a moment; your clips and queue are kept.');
    start = true;
  } else if (st.needs_licence) {
    title = T('remote.one_more_step_your_key', 'One more step: your key in this Studio');
    body = st.next || 'Paste your licence key in Settings > Pro.';
    settings = true;
  } else if (parts.restart_needed) {
    title = T('remote.restart_the_studio_to_finish', 'Restart the Studio to finish');
    body = T('remote.new_remote_control_components_are', 'New Remote Control components are installed. ') + distText(
      T('remote.restart_phosphene_in_pinokio_to', 'Restart Phosphene in Pinokio to start using them.'),
      T('remote.use_studio_restart_engine_to', 'Use Studio \u2192 Restart Engine to start using them.'));
  } else if (parts.busy) {
    const ph = parts.phase;
    title = ph === 'verifying' ? T('remote.checking_the_download', 'Checking the download') : ph === 'downloading' ? T('remote.downloading_remote_control', 'Downloading Remote Control') : T('remote.checking_for_remote_control', 'Checking for Remote Control');
    body = T('remote.this_takes_a_minute_this', 'This takes a minute. This page updates by itself.');
  } else if (parts.error) {
    title = T('remote.remote_control_could_not_be', 'Remote Control could not be installed yet');
    body = T('remote.your_pro_licence_is_fine', 'Your Pro licence is fine. The components did not install.');
    err = parts.message || 'Try again in a moment.';
    retry = true;
  } else if (st.next) {
    body = st.next;
    settings = true;
  }
  return { title, body, err, retry, settings, start };
}

function rmRenderWait(st) {
  const v = rmWaitView(st);
  $('rmWaitTitle').textContent = v.title;
  $('rmWaitBody').textContent = v.body;
  const e = $('rmWaitErr'); e.hidden = !v.err; e.textContent = v.err;
  $('rmWaitRetry').hidden = !v.retry;
  $('rmWaitSettings').hidden = !v.settings;
  const go = $('rmWaitStart'); if (go) go.hidden = !v.start;
}

// "Set up Remote" in the Mac app: ask the app (WKWebView bridge, NativeBridge.swift) to start the
// engine that carries Remote. Anywhere else there is no bridge: say what to do by hand.
function rmStartInApp() {
  const h = window.webkit && window.webkit.messageHandlers && window.webkit.messageHandlers.phosphene;
  if (h) {
    try {
      h.postMessage({ type: 'startRemote' });
      phosToast(T('remote.starting_remote_this_takes_a', 'Starting Remote. This takes a moment.'));
      return;
    } catch (e) { /* fall through */ }
  }
  phosToast(T('remote.quit_phosphene_and_open_it', 'Quit Phosphene and open it again to finish setting up Remote.'), { kind: 'info' });
}

async function rmPartsRetry() {
  const b = $('rmWaitRetry'); if (b) b.disabled = true;
  try {
    const r = await fetch('/remote/parts_retry', { method: 'POST' });
    const d = await r.json();
    if (d && d.message) phosToast(d.message);
  } catch (e) { phosToast(T('remote.could_not_reach_the_studio', 'Could not reach the Studio.'), { kind: 'danger' }); }
  if (b) setTimeout(() => { b.disabled = false; }, 3000);
  rmRefresh();
}

function rmConnection(st) {
  // -> [class, text]
  if (!st.available || !st.pro) {
    if (!rmIsMember(st)) return ['off', T('remote.pro_feature', 'Pro feature')];
    const p = st.parts || {};
    if (p.managed_by === 'mac_app' && st.pro) return ['warn', T('remote.not_started_yet', 'Not started yet')];
    if (st.needs_licence) return ['warn', T('remote.needs_your_key_here', 'Needs your key here')];
    if (p.restart_needed) return ['warn', T('remote.restart_to_finish', 'Restart to finish')];
    if (p.busy) return ['warn', T('remote.setting_up', 'Setting up\u2026')];
    if (p.error) return ['bad', T('remote.setup_failed_try_again', 'Setup failed \u00b7 try again')];
    return ['off', T('remote.setting_up', 'Setting up\u2026')];
  }
  if (!st.enabled || !st.running) return ['off', T('remote.off', 'Off')];
  if (!(st.devices || []).some((d) => d.confirmed)) return ['warn', T('remote.not_paired_yet_set_up', 'Not paired yet \u00b7 set up below')];
  const anyPhone = (st.devices || []).some((d) => d.connected);
  const lan = !!st.lan, relay = st.relay === 'connected';
  if (anyPhone) return ['on', T('remote.phone_connected', 'Phone connected') + (lan ? T('remote.same_wi_fi', ' · same Wi-Fi') : T('remote.via_relay', ' · via relay'))];
  if (lan && relay) return ['on', T('remote.ready_same_wi_fi_or', 'Ready · same Wi-Fi or away')];
  if (lan) return ['on', T('remote.ready_same_wi_fi', 'Ready · same Wi-Fi')];
  if (relay) return ['warn', T('remote.ready_via_relay_only', 'Ready · via relay only')];
  return ['bad', T('remote.offline_no_network_found', 'Offline · no network found')];
}

function rmRender() {
  const st = state;
  if (!st) return;
  const pro = $('rmPro'), main = $('rmMain'), wait = $('rmProWait');
  const gated = !st.available || !st.pro;
  // A Pro member never sees the upsell: no Remote module yet = a status line instead.
  const member = rmIsMember(st);
  pro.hidden = !gated || member;
  if (wait) { wait.hidden = !gated || !member; if (!wait.hidden) rmRenderWait(st); }
  main.hidden = gated;
  const [cls, text] = rmConnection(st);
  $('rmConnDot').className = 'rm-dot ' + cls;
  $('rmConnText').textContent = text;
  if (gated) return;

  const pairing = st.pairing || {};
  const joined = pairing.joined;
  $('rmAllow').hidden = !joined;
  $('rmQrLive').hidden = !!joined || !pairing.open;
  $('rmQrIdle').hidden = !!joined || !!pairing.open;
  if (joined) {
    $('rmAllowName').textContent = joined.name || 'A phone';
    const c = String(joined.code || '').replace(/\D/g, '');
    $('rmAllowCode').textContent = c.length === 6 ? c.slice(0, 3) + ' ' + c.slice(3) : c;
  }
  if (pairing.open && offer) {
    const left = Math.max(0, (pairing.expires_at || offer.expires_at) - Date.now() / 1000);
    $('rmCountdown').textContent = Math.floor(left / 60) + ':' + String(Math.floor(left % 60)).padStart(2, '0');
  }
  if (!pairing.open) { offer = null; drawnUrl = ''; }

  const devs = (st.devices || []).filter((d) => d.confirmed);
  const pt = $('rmPairTitle'); if (pt) pt.textContent = devs.length ? T('remote.pair_another_iphone', 'Pair another iPhone') : T('remote.set_up_remote', 'Set up Remote');
  $('rmPhonesCount').textContent = devs.length ? String(devs.length) : '';
  $('rmPhonesEmpty').hidden = devs.length > 0;
  const ul = $('rmPhones');
  ul.textContent = '';
  for (const d of devs) {
    const li = document.createElement('li');
    li.className = 'rm-phone';
    li.innerHTML = '<span class="rm-phone-ic" aria-hidden="true"></span><div class="rm-phone-main"><b></b><span></span></div>';
    li.querySelector('b').textContent = d.name || 'iPhone';
    li.querySelector('.rm-phone-main span').textContent =
      (d.connected ? T('remote.connected_now', 'Connected now') : T('remote.last_seen', 'Last seen ') + rmAgo(d.last_seen)) + (d.scope === 'own' ? T('remote.only_its_own_clips', ' · only its own clips') : '');
    li.querySelector('.rm-phone-ic').classList.toggle('on', !!d.connected);
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'rm-link danger'; b.textContent = T('remote.remove', 'Remove');
    let armed = false, t = null;
    b.addEventListener('click', async () => {
      if (!armed) {
        armed = true; b.textContent = T('remote.click_again_to_remove', 'Click again to remove');
        t = setTimeout(() => { armed = false; b.textContent = T('remote.remove', 'Remove'); }, 4000);
        return;
      }
      clearTimeout(t);
      state = await rmApi('revoke', { id: d.id });
      rmRender();
    });
    li.appendChild(b);
    ul.appendChild(li);
  }
  $('rmForgetAll').hidden = devs.length === 0;

  $('rmEnable').checked = !!st.enabled && !!st.running;
  $('rmFactLan').textContent = st.lan ? (st.lan_url || st.host || 'Ready') : (st.enabled ? T('remote.no_local_network_found', 'No local network found') : T('remote.off', 'Off'));
  $('rmFactRelay').textContent = ({ connected: T('remote.connected', 'Connected'), connecting: T('remote.connecting', 'Connecting…'), off: T('remote.off', 'Off'), offline: T('remote.offline', 'Offline'),
    unavailable: T('remote.not_available_yet', 'Not available yet'), error: T('remote.not_reachable', 'Not reachable'), 'no-pro-token': T('remote.needs_pro', 'Needs Pro') })[st.relay] || '—';
  const nm = $('rmName');
  if (document.activeElement !== nm) nm.value = st.mac_name || '';
  if (st.error) rmSetErr(st.error);
}

async function rmDrawQr(url) {
  if (!url || url === drawnUrl) return;
  drawnUrl = url;
  try {
    const q = await rmLoadQr();
    const qr = q(0, 'L');
    qr.addData(url);
    qr.make();
    const n = qr.getModuleCount(), cv = $('rmQr'), ctx = cv.getContext('2d');
    const quiet = 3, cell = Math.floor(cv.width / (n + quiet * 2));
    const size = cell * (n + quiet * 2);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, cv.width, cv.height);
    const off = Math.floor((cv.width - size) / 2) + quiet * cell;
    ctx.fillStyle = '#0e0e10';
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) {
      if (qr.isDark(r, c)) ctx.fillRect(off + c * cell, off + r * cell, cell, cell);
    }
  } catch (e) { rmSetErr(T('remote.could_not_draw_the_code', 'Could not draw the code. Reload the page and try again.')); }
}

async function rmRefresh() {
  try { state = await rmApi('state'); } catch (e) { return; }
  rmRender();
}

async function rmShowCode() {
  if (busy) return;
  busy = true; rmSetErr('');
  $('rmPairBtn').disabled = true;
  try {
    const doc = await rmApi('pair', {});
    if (!doc.ok) { rmSetErr(doc.error || 'Could not open pairing.'); }
    else { offer = doc; state = doc.state || state; rmRender(); await rmDrawQr(doc.url); }
  } finally { busy = false; $('rmPairBtn').disabled = false; }
}

function rmWire() {
  if (rmWire.done) return; rmWire.done = true;
  $('rmPairBtn').addEventListener('click', rmShowCode);
  $('rmQrCancel').addEventListener('click', async () => { offer = null; drawnUrl = ''; state = await rmApi('pair_cancel', {}); rmRender(); });
  const confirm = async (allow) => {
    const j = state && state.pairing && state.pairing.joined;
    if (!j) return;
    const scope = (document.querySelector('input[name="rmScope"]:checked') || {}).value || 'all';
    state = await rmApi('confirm', { id: j.id, allow: allow ? '1' : '0', scope });
    rmRender();
  };
  $('rmAllowYes').addEventListener('click', () => confirm(true));
  $('rmAllowNo').addEventListener('click', () => confirm(false));
  $('rmEnable').addEventListener('change', async (e) => {
    rmSetErr('');
    const doc = await rmApi('enable', { on: e.target.checked ? '1' : '0' });
    if (doc.ok === false) rmSetErr(doc.error || 'Could not change Remote Control.');
    else state = doc;
    rmRender();
  });
  $('rmName').addEventListener('change', async (e) => { state = await rmApi('name', { name: e.target.value }); rmRender(); });
  $('rmForgetAll').addEventListener('click', async () => {
    if (!rmConfirm_(T('remote.remove_every_paired_phone_they', 'Remove every paired phone? They will need to pair again.'))) return;
    state = await rmApi('forget_all', {}); rmRender();
  });
}
function rmConfirm_(msg) { return window.confirm(msg); }

function remoteTabEnter() {
  rmWire();
  rmRefresh();
  if (!poll) poll = setInterval(rmRefresh, 1500);
  const piece = $('rmQrLive');
  if (piece && !piece.hidden && offer) rmDrawQr(offer.url);
}
function remoteTabLeave() {
  if (poll) { clearInterval(poll); poll = null; }
}

Object.assign(globalThis, { remoteTabEnter, remoteTabLeave, rmPartsRetry, rmStartInApp });
