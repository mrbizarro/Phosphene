// ---- PHOSPHENE BENCH — the benchmark window --------------------------------
// Settings -> Phosphene Bench. The suite, the runner, the timings and the card
// all live server-side (bench.py, panel/routes_bench.py); this module paints
// GET /bench/plan and /bench/status and posts the buttons. It computes no time,
// no canvas and no eligibility of its own (Structure Law, rule 6).
//
// Copy: Codex (today-2026-10-22/bench/codex/copy_out.json), fact-checked.

const BENCH_COPY = {
  badge: {
    ready: 'Ready', missing: 'Model missing', optional_skip: 'Optional', queued: 'Queued',
    running: 'Running', done: 'Done', failed: 'Failed', skipped: 'Skipped', cancelled: 'Stopped',
  },
  reason: {
    wrong_generation: 'This install renders LTX-2.3. Bench measures LTX-2.5. Use an install that renders LTX-2.5 to run Bench.',
    ltx_missing: 'The LTX-2.5 model is not fully downloaded.',
    image_missing: 'FLUX.2 [klein] 4B is not on this Mac yet.',
    h3_not_installed: "Skipped: H3 is not installed. Bench never downloads it here.",
    h3_licence: 'Skipped: the H3 licence has not been accepted.',
    h3_not_capable: 'Skipped: this Mac does not have enough memory to run H3.',
    stopped: 'Stopped.',
    busy: 'The Mac was busy with another render.',
  },
  busy: {
    busy_queue: 'Renders are queued or running. Finish or clear them before starting Bench.',
    busy_gpu: 'Something else is using the GPU right now (an image, Enhance or an update). Wait for it to finish, then run Bench.',
    busy_download: 'A model is downloading. Bench waits for downloads so they do not slow the timed steps.',
    busy_paused: 'The queue is paused. Resume it, then run Bench.',
    bench_running: 'Bench is already running.',
  },
  warn_battery: 'Your Mac is on battery. Connect the charger for comparable results. The run records the power source.',
  warn_low_power: 'Low Power Mode is on. Turn it off for comparable results. The run records this setting.',
  warn_thermal: 'Your Mac is already warm. Thermal state: {state}. Let it cool for a fair comparison. The run records thermal state.',
  install_ltx: 'Install LTX-2.5',
  install_image: 'Install image model ({gb} GB)',
  install_image_running: 'Downloading FLUX.2 [klein] 4B…',
  install_image_failed: 'The download did not finish: {error}',
  install_h3: 'How to install H3',
  running_line: 'Step {step} of {total}: {label} · {elapsed} elapsed',
  starting: 'Starting…',
  stopped: 'Bench stopped. A run is complete only when all three core steps finish.',
  failed_step: '{label} failed: {reason}',
  copied: 'Results copied',
  copy_failed: 'Could not copy results. Save the JSON instead.',
  submit_off: 'Anonymous usage counts are off, so Bench sends nothing. Turn them on in Settings to submit.',
  submit_ineligible: 'Incomplete runs cannot be submitted. Finish all three core steps to get a complete result.',
  submit_nothing: 'Every complete PB1 run on this Mac has already been sent.',
  submit_done: 'Sent {n} complete PB1 runs. They will be included at phosphene.video/bench when the site is next rebuilt.',
  submit_error: 'Submission failed. Your results are still on this Mac. Try again.',
  history_empty: 'No Bench runs yet.',
  complete: 'complete', incomplete: 'incomplete', sent: 'sent',
};

const BENCH = { plan: null, timer: null, shown: null, wasRunning: false, submitMsg: '' };

function _bEl(id) { return document.getElementById(id); }
function _bEsc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\x22/g, '&quot;').replace(/\x27/g, '&#39;');
}
function _bFill(t, vars) {
  return String(t).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m));
}
// Same shape as bench.fmt_sec (48 s · 2m 19s · 1h 02m), for the live clock and
// the rows; the card and the copied text are formatted server-side.
function benchFmt(sec) {
  if (sec == null || isNaN(sec)) return '—';
  const s = Math.round(Number(sec));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`;
  return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`;
}
function _bToast(msg, kind) { if (typeof phosToast === 'function') phosToast(msg, { kind: kind || 'info' }); }

async function _bJson(url, opts) {
  const r = await fetch(url, opts);
  let d = {};
  try { d = await r.json(); } catch (e) { d = {}; }
  d._status = r.status;
  return d;
}

// ---- open / close -----------------------------------------------------------
async function openBenchModal() {
  const m = _bEl('benchModal');
  if (!m) return;
  // Keep the overlay outside the scrolling workspace.
  document.body.appendChild(m);
  m.style.display = 'flex';
  await benchRefresh();
  _bStartPolling();
}
function closeBenchModal() {
  const m = _bEl('benchModal');
  if (m) m.style.display = 'none';
  _bStopPolling();
}
function _bStartPolling() {
  _bStopPolling();
  BENCH.timer = setInterval(() => {
    const m = _bEl('benchModal');
    if (!m || m.style.display === 'none' || document.hidden) return;
    benchRefresh();
  }, 2000);
}
function _bStopPolling() { if (BENCH.timer) { clearInterval(BENCH.timer); BENCH.timer = null; } }

// ---- paint ------------------------------------------------------------------
async function benchRefresh() {
  let d;
  try { d = await _bJson('/bench/plan'); } catch (e) { return; }
  if (!d.ok) return;
  BENCH.plan = d;
  const st = d.state || {};
  _bPaintSteps(d, st);
  _bPaintNotices(d, st);
  _bPaintActions(d, st);
  _bPaintHistory(d);
  // The shown run's Submit state follows the plan (analytics switched on in
  // Settings, a submit elsewhere), not just the click that opened it.
  if (BENCH.shown) _bPaintSubmit((d.runs || []).find(r => r.run_id === BENCH.shown) || {});
  // A run just ended: show its card.
  if (BENCH.wasRunning && !st.running && st.last_run) benchShowRun(st.last_run);
  if (!BENCH.shown && !st.running && d.runs && d.runs.length) benchShowRun(d.runs[0].run_id);
  BENCH.wasRunning = !!st.running;
}

function _bPaintSteps(d, st) {
  const ol = _bEl('benchSteps');
  if (!ol) return;
  const live = {};
  ((st.running || st.last_run) ? (st.steps || []) : []).forEach(s => { live[s.id] = s; });
  const rows = (d.steps || []).map((r, i) => {
    const l = live[r.id] || {};
    const status = l.status || r.status;
    const reason = l.reason || r.reason || '';
    let side = '';
    if (status === 'done' && l.wall_sec != null) side = `<span class="bench-time">${benchFmt(l.wall_sec)}</span>`;
    else if (status === 'running' && st.step_index === i) side = `<span class="bench-time is-live">${benchFmt(st.step_elapsed)}</span>`;
    let action = '';
    if (!st.running && r.install && r.install.kind === 'models') {
      action = `<button type="button" class="ghost-btn" onclick="closeBenchModal(); openModelsModal();">${_bEsc(BENCH_COPY.install_ltx)}</button>`;
    } else if (!st.running && r.install && r.install.kind === 'image') {
      const inst = d.install || {};
      if (inst.state === 'downloading') action = `<span class="bench-sub">${_bEsc(BENCH_COPY.install_image_running)}</span>`;
      else action = `<button type="button" class="ghost-btn" onclick="benchInstallImage()">${_bEsc(_bFill(BENCH_COPY.install_image, { gb: r.install.gb }))}</button>`;
    } else if (!st.running && r.reason === 'h3_not_installed' && typeof openH3InstallCard === 'function') {
      action = `<button type="button" class="ghost-btn" onclick="closeBenchModal(); openH3InstallCard('bench');">${_bEsc(BENCH_COPY.install_h3)}</button>`;
    }
    const why = reason && (status === 'missing' || status === 'optional_skip' || status === 'failed' || status === 'skipped')
      ? `<div class="bench-sub bench-why">${_bEsc(BENCH_COPY.reason[reason] || reason)}</div>` : '';
    const imgErr = (r.install && r.install.kind === 'image' && (d.install || {}).state === 'failed')
      ? `<div class="bench-sub bench-why">${_bEsc(_bFill(BENCH_COPY.install_image_failed, { error: (d.install || {}).error || '' }))}</div>` : '';
    return `<li class="bench-step is-${_bEsc(status)}${r.core ? '' : ' is-extra'}${r.timed ? '' : ' is-untimed'}">
      <span class="bench-dot" aria-hidden="true"></span>
      <div class="bench-step-main">
        <div class="bench-step-title">${_bEsc(r.label)}${r.headline ? ' <span class="bench-tag">headline</span>' : ''}</div>
        <div class="bench-sub">${_bEsc(r.what)}</div>${why}${imgErr}
      </div>
      <div class="bench-step-side"><span class="bench-badge">${_bEsc(BENCH_COPY.badge[status] || status)}</span>${side}${action}</div>
    </li>`;
  });
  ol.innerHTML = rows.join('');
}

function _bPaintNotices(d, st) {
  const box = _bEl('benchNotices');
  if (!box) return;
  const out = [];
  if (!st.running) {
    if (d.why && BENCH_COPY.busy[d.why]) out.push(['warn', BENCH_COPY.busy[d.why]]);
    if (d.why === 'wrong_generation') out.push(['warn', BENCH_COPY.reason.wrong_generation]);
    const c = d.conditions || {};
    if (c.power === 'battery') out.push(['info', BENCH_COPY.warn_battery]);
    if (c.low_power_mode) out.push(['info', BENCH_COPY.warn_low_power]);
    if (c.thermal === 'serious' || c.thermal === 'critical') out.push(['info', _bFill(BENCH_COPY.warn_thermal, { state: c.thermal })]);
  }
  if (st.error) out.push(['warn', st.error]);
  box.innerHTML = out.map(([k, t]) => `<div class="bench-notice is-${k}">${_bEsc(t)}</div>`).join('');
}

function _bPaintActions(d, st) {
  const run = _bEl('benchRunBtn'), stop = _bEl('benchStopBtn'), line = _bEl('benchLine');
  if (run) { run.hidden = !!st.running; run.disabled = !d.can_run; }
  if (stop) stop.hidden = !st.running;
  if (!line) return;
  if (st.running) {
    const steps = (d.steps || []);
    const i = st.step_index;
    if (i == null) { line.textContent = BENCH_COPY.starting; return; }
    line.textContent = _bFill(BENCH_COPY.running_line, {
      step: i + 1, total: steps.length, label: (steps[i] || {}).label || '', elapsed: benchFmt(st.step_elapsed),
    });
  } else if (st.stopped && st.last_run) {
    line.textContent = BENCH_COPY.stopped;
  } else {
    line.textContent = '';
  }
}

function _bPaintHistory(d) {
  const ul = _bEl('benchHistory');
  if (!ul) return;
  const runs = d.runs || [];
  if (!runs.length) { ul.innerHTML = `<li class="bench-empty">${_bEsc(BENCH_COPY.history_empty)}</li>`; return; }
  ul.innerHTML = runs.map(r => {
    const when = String(r.finished_at || '').replace('T', ' ').replace('Z', ' UTC');
    const tags = [r.complete ? BENCH_COPY.complete : BENCH_COPY.incomplete, r.submitted ? BENCH_COPY.sent : '']
      .filter(Boolean).join(' · ');
    const on = BENCH.shown === r.run_id ? ' is-on' : '';
    return `<li><button type="button" class="bench-run${on}" onclick="benchShowRun('${_bEsc(r.run_id)}')">
      <span class="bench-run-time">${benchFmt(r.headline_sec)}</span>
      <span class="bench-sub">${_bEsc(r.suite)} · ${_bEsc(when)} · Phosphene ${_bEsc(r.version)} · ${_bEsc(tags)}</span>
    </button></li>`;
  }).join('');
}

// ---- one result -----------------------------------------------------------------
function benchShowRun(runId) {
  if (!runId) return;
  if (BENCH.shown !== runId) BENCH.submitMsg = '';
  BENCH.shown = runId;
  const sec = _bEl('benchResult');
  if (sec) sec.hidden = false;
  const id = encodeURIComponent(runId);
  const img = _bEl('benchCardImg');
  if (img) img.src = `/bench/card?id=${id}&t=${Date.now()}`;
  const a1 = _bEl('benchSaveCard'), a2 = _bEl('benchSaveJson');
  if (a1) a1.href = `/bench/card?id=${id}&download=1`;
  if (a2) a2.href = `/bench/run?id=${id}&download=1`;
  const row = ((BENCH.plan || {}).runs || []).find(r => r.run_id === runId) || {};
  const inc = _bEl('benchIncomplete');
  if (inc) inc.hidden = row.complete !== false;
  const note = _bEl('benchSubmitNote');
  if (note) note.textContent = '';
  const consent = _bEl('benchConsent');
  if (consent) consent.hidden = true;
  _bPaintSubmit(row);
  if (BENCH.plan) _bPaintHistory(BENCH.plan);
}

function _bPaintSubmit(row) {
  const btn = _bEl('benchSubmitBtn'), note = _bEl('benchSubmitNote');
  const d = BENCH.plan || {};
  if (!btn) return;
  btn.disabled = false;
  if (!d.analytics_enabled) { btn.disabled = true; if (note) note.textContent = BENCH_COPY.submit_off; return; }
  if (!d.unsent_eligible) {
    btn.disabled = true;
    if (note) note.textContent = (row && row.complete === false) ? BENCH_COPY.submit_ineligible : BENCH_COPY.submit_nothing;
  } else if (note) {
    note.textContent = '';
  }
  // The outcome of the last Submit stays on screen until another run is shown.
  if (BENCH.submitMsg && note) note.textContent = BENCH.submitMsg;
}

async function benchCopy() {
  if (!BENCH.shown) return;
  let d;
  try { d = await _bJson(`/bench/text?id=${encodeURIComponent(BENCH.shown)}`); }
  catch (e) { _bToast(BENCH_COPY.copy_failed, 'warning'); return; }
  if (!d.ok) { _bToast(BENCH_COPY.copy_failed, 'warning'); return; }
  try {
    await navigator.clipboard.writeText(d.text);
  } catch (e) {
    const ta = document.createElement('textarea');
    ta.value = d.text; document.body.appendChild(ta); ta.select();
    let copied = false;
    try { copied = document.execCommand('copy'); } catch (e2) { /* report below */ }
    ta.remove();
    if (!copied) { _bToast(BENCH_COPY.copy_failed, 'warning'); return; }
  }
  _bToast(BENCH_COPY.copied, 'success');
}

function benchSubmitOpen() {
  const c = _bEl('benchConsent');
  if (c) c.hidden = false;
}

async function benchSubmit() {
  const note = _bEl('benchSubmitNote');
  let d;
  try { d = await _bJson('/bench/submit', { method: 'POST' }); } catch (e) { d = { ok: false }; }
  const c = _bEl('benchConsent');
  if (c) c.hidden = true;
  BENCH.submitMsg = d.ok ? _bFill(BENCH_COPY.submit_done, { n: d.sent })
    : d.code === 'analytics_off' ? BENCH_COPY.submit_off
      : d.code === 'nothing_to_send' ? BENCH_COPY.submit_nothing : BENCH_COPY.submit_error;
  if (note) note.textContent = BENCH.submitMsg;
  await benchRefresh();
}

// ---- run / stop / install ---------------------------------------------------------
async function benchRun() {
  const run = _bEl('benchRunBtn');
  if (run) run.disabled = true;
  const d = await _bJson('/bench/start', { method: 'POST' });
  if (!d.ok) {
    const msg = BENCH_COPY.busy[d.code] || BENCH_COPY.reason[d.code] || d.code || 'Bench could not start.';
    _bToast(msg, 'warning');
  } else {
    BENCH.wasRunning = true;
  }
  await benchRefresh();
}

async function benchStop() {
  await _bJson('/bench/stop', { method: 'POST' });
  await benchRefresh();
}

async function benchInstallImage() {
  const d = await _bJson('/bench/install_image', { method: 'POST' });
  if (!d.ok) _bToast(d.error || BENCH_COPY.busy[d.code] || 'The download could not start.', 'warning');
  await benchRefresh();
}

// ---- published to the page --------------------------------------------------------
Object.assign(globalThis, {
  openBenchModal, closeBenchModal, benchRefresh, benchRun, benchStop, benchCopy,
  benchSubmitOpen, benchSubmit, benchShowRun, benchInstallImage, benchFmt,
});
