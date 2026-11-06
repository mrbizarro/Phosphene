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
  if (!ts) return 'not seen yet';
  const s = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (s < 60) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' d ago';
}

// Pro on this Mac: the Remote module says so, or the studio's own /pro/status did (proStatusRefresh
// sets <html data-pro>). Either way the page shows status, never "comes with Pro".
function rmIsMember(st) {
  return !!(st && st.pro) || !!document.documentElement.dataset.pro;
}

function rmConnection(st) {
  // -> [class, text]
  if (!st.available || !st.pro) return ['off', rmIsMember(st) ? 'Not set up yet' : 'Pro feature'];
  if (!st.enabled || !st.running) return ['off', 'Off'];
  const anyPhone = (st.devices || []).some((d) => d.connected);
  const lan = !!st.lan, relay = st.relay === 'connected';
  if (anyPhone) return ['on', 'Phone connected' + (lan ? ' · same Wi-Fi' : ' · via relay')];
  if (lan && relay) return ['on', 'Ready · same Wi-Fi or away'];
  if (lan) return ['on', 'Ready · same Wi-Fi'];
  if (relay) return ['warn', 'Ready · via relay only'];
  return ['bad', 'Offline · no network found'];
}

function rmRender() {
  const st = state;
  if (!st) return;
  const pro = $('rmPro'), main = $('rmMain'), wait = $('rmProWait');
  const gated = !st.available || !st.pro;
  // A Pro member never sees the upsell: no Remote module yet = a status line instead.
  const member = rmIsMember(st);
  pro.hidden = !gated || member;
  if (wait) wait.hidden = !gated || !member;
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
      (d.connected ? 'Connected now' : 'Last seen ' + rmAgo(d.last_seen)) + (d.scope === 'own' ? ' · only its own clips' : '');
    li.querySelector('.rm-phone-ic').classList.toggle('on', !!d.connected);
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'rm-link danger'; b.textContent = 'Remove';
    let armed = false, t = null;
    b.addEventListener('click', async () => {
      if (!armed) {
        armed = true; b.textContent = 'Click again to remove';
        t = setTimeout(() => { armed = false; b.textContent = 'Remove'; }, 4000);
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
  $('rmFactLan').textContent = st.lan ? (st.lan_url || st.host || 'Ready') : (st.enabled ? 'No local network found' : 'Off');
  $('rmFactRelay').textContent = ({ connected: 'Connected', connecting: 'Connecting…', off: 'Off', offline: 'Offline',
    unavailable: 'Not available yet', error: 'Not reachable', 'no-pro-token': 'Needs Pro' })[st.relay] || '—';
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
  } catch (e) { rmSetErr('Could not draw the code. Reload the page and try again.'); }
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
    if (!rmConfirm_('Remove every paired phone? They will need to pair again.')) return;
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

Object.assign(globalThis, { remoteTabEnter, remoteTabLeave });
