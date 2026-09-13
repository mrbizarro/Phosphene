// ---- ONE SHOT — its own door (2026-09-08) -----------------------------------
// One continuous shot of 30 s – 2 min that never cuts, rendered as parts that
// continue from each other's last frame (run_take_job_inner). It used to be a
// mode chip on the video form and a folded panel under it; it is a workflow
// tab now, with its own composer and its own API. Nothing here rides the
// video form: this module reads the tab's fields, posts ONE document to
// POST /oneshot (the same document a script would post), and paints the
// take's progress from GET /oneshot/status. One prompt is a complete request;
// every other field is a tweak, and the tab is honest about which is which.
//
// Publish block at the bottom: what the page and the other modules call.

const OS = {
  engine: 'ltx',
  seconds: 60,
  quality: { ltx: 'balanced', h3: 'standard', character: 'pro' },
  character: '',
  image: '',
  beats: [],                  // one string per five seconds, as typed; trailing blanks trimmed on read
  light: 'on',
  retake: 'on',
  handoff: 'last',
  handoffTouched: false,      // the user chose; a character pick no longer flips it
  options: null,
  optionsAt: 0,
  statusTimer: null,
  estimateSeq: 0,
  wired: false,
  lastSubmitted: null,
};

const OS_SECONDS = [30, 45, 60, 90, 120];

// ---- helpers ----------------------------------------------------------------
function osEl(id) { return document.getElementById(id); }
function osEsc(s) {
  // No quote characters inside the regex literals: the test extractor scans
  // braces with a naive string tracker and a quote in a regex derails it.
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\x22/g, '&quot;').replace(/\x27/g, '&#39;');
}
function osLengthLabel(s) {
  s = parseInt(s || 0, 10) || 0;
  if (s < 60) return `${s} s`;
  const whole = Math.floor(s / 60), rem = s - whole * 60;
  if (!rem) return `${whole} min`;
  if (rem === 30) return `${whole}½ min`;
  return `${whole} min ${rem} s`;
}
// The parts a length splits into: whole parts, and a shorter last one when
// the length is not a multiple (45 s on LTX is four 10 s parts and one of 5 s).
function osPartsText(seconds, engine) {
  const part = osPartSeconds(engine);
  const whole = Math.floor(seconds / part), rem = seconds - whole * part;
  if (!rem) return `${whole} part${whole === 1 ? '' : 's'} of ${part} s`;
  return `${whole} × ${part} s + ${rem} s`;
}
function osPartsShort(seconds, engine) {
  const part = osPartSeconds(engine);
  const whole = Math.floor(seconds / part), rem = seconds - whole * part;
  return rem ? `${whole} × ${part} s + ${rem} s` : `${whole} × ${part} s`;
}
function osPartSeconds(engine) {
  const o = OS.options && OS.options.engines && OS.options.engines[engine || OS.engine];
  return (o && o.part_seconds) || ((engine || OS.engine) === 'h3' ? 15 : 10);
}
function osClock(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return `${m}:${String(s).padStart(2, '0')}`;
}
function osActiveQuality() {
  if (OS.engine === 'h3') return OS.quality.h3;
  return OS.character ? OS.quality.character : OS.quality.ltx;
}
function osH3Available() {
  const o = OS.options && OS.options.engines && OS.options.engines.h3;
  return !!(o && o.available);
}

// ---- the document -----------------------------------------------------------
// What the tab posts. A script can post the same thing to /oneshot.
function osBeatsList() {
  const lines = (OS.beats || []).map(x => String(x || '').trim());
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  return lines;
}
function osSetBeats(lines) {
  OS.beats = (lines || []).map(x => String(x || '').replace(/\s+Continuity:.*$/, ''));
  osRenderBeats();
  osBeatsInput();
}
function osDocument() {
  const beats = osBeatsList();
  const doc = {
    prompt: String((osEl('osPrompt') || {}).value || '').trim(),
    seconds: OS.seconds,
    engine: OS.engine,
    quality: osActiveQuality(),
    camera: String((osEl('osCamera') || {}).value || '').trim(),
    light_lock: OS.light,
    retake: OS.retake,
    handoff: OS.handoff,
  };
  if (beats.some(Boolean)) doc.beats = beats;
  if (OS.image) doc.image = OS.image;
  if (OS.engine === 'ltx' && OS.character) doc.character_id = OS.character;
  const seed = String((osEl('osSeed') || {}).value || '').trim();
  if (seed) doc.seed = seed;
  const label = String((osEl('osLabel') || {}).value || '').trim();
  if (label) doc.label = label;
  if (osEl('osNoMusic') && osEl('osNoMusic').checked) doc.no_music = 'on';
  return doc;
}

// ---- rendering --------------------------------------------------------------
function osRenderEngine() {
  const h3ok = osH3Available();
  document.querySelectorAll('#osEngineGroup .pill-btn').forEach(b => {
    const e = b.dataset.osEngine;
    b.classList.toggle('active', e === OS.engine);
    b.classList.toggle('disabled', e === 'h3' && !h3ok);
    if (e === 'h3') b.title = h3ok ? '' : 'Hailuo H3 is not installed on this Mac';
  });
  const meta = osEl('osEngineMeta');
  if (meta) {
    meta.textContent = OS.engine === 'h3'
      ? 'Hailuo H3 — 15-second parts that continue from each other · motion, landscapes, crowds'
      : 'LTX 2.5 — 10-second parts that continue from the last frame · faces, voices, dialogue';
  }
  const cb = osEl('osCharacterBlock');
  if (cb) cb.hidden = OS.engine === 'h3';
  const ho = document.querySelector('[data-os-toggle="handoff"]');
  if (ho && ho.parentElement) ho.parentElement.hidden = OS.engine === 'h3';
}
function osRenderLengths() {
  const g = osEl('osLengthGroup');
  if (!g) return;
  const part = osPartSeconds();
  g.innerHTML = OS_SECONDS.map(s => {
    return `<button type="button" class="q-chip pill-btn pill-quality${s === OS.seconds ? ' active' : ''}" data-os-seconds="${s}" title="${osLengthLabel(s)}, one continuous shot: ${s / 5} beats of 5 s" aria-label="${osLengthLabel(s)}">
      <span class="ql-name">${osLengthLabel(s)}</span><span class="q-spec ql-spec sub">${s / 5} beats</span><span class="ql-tier">${osPartsShort(s)}</span></button>`;
  }).join('');
  g.querySelectorAll('[data-os-seconds]').forEach(b => b.onclick = () => osSetSeconds(parseInt(b.dataset.osSeconds, 10)));
  const meta = osEl('osLengthMeta');
  if (meta) meta.textContent = `${OS.seconds / 5} beats of five seconds · ${osPartsText(OS.seconds)}`;
}
function osQualityRows() {
  const o = OS.options || {};
  const eng = (o.engines || {})[OS.engine] || {};
  if (OS.engine === 'ltx' && OS.character) {
    return (o.character_qualities || []).map(q => ({ key: q.key, label: q.label, sub: q.sub || '' }));
  }
  return (eng.qualities || []).map(q => ({
    key: q.key, label: q.label,
    sub: OS.engine === 'h3' ? (q.size || '') : (q.eta_min ? `~${Math.round(q.eta_min)} min a part` : ''),
  }));
}
function osRenderQualities() {
  const g = osEl('osQualityGroup');
  if (!g) return;
  const rows = osQualityRows();
  const cur = osActiveQuality();
  if (!rows.length) { g.innerHTML = ''; g.hidden = true; return; }
  g.hidden = false;
  g.style.gridTemplateColumns = `repeat(${Math.min(rows.length, 5)}, minmax(0, 1fr))`;
  g.innerHTML = rows.map(q => `<button type="button" class="q-chip pill-btn pill-quality${q.key === cur ? ' active' : ''}" data-os-quality="${osEsc(q.key)}">
      <span class="ql-name">${osEsc(q.label)}</span><span class="q-spec ql-spec sub">${osEsc(q.sub)}</span></button>`).join('');
  g.querySelectorAll('[data-os-quality]').forEach(b => b.onclick = () => osSetQuality(b.dataset.osQuality));
  const meta = osEl('osQualityMeta');
  if (meta) {
    meta.textContent = OS.engine === 'h3'
      ? 'the size of every part · exports fit 720p'
      : (OS.character ? 'a trained face keeps its identity on Pro · Draft is a quick look'
                      : 'every part renders at this quality · Balanced exports at 720p');
  }
}
function osRenderCharacters() {
  const g = osEl('osCharacterGroup');
  if (!g) return;
  const chars = ((OS.options || {}).characters || []);
  const pills = [{ id: '', name: 'Nobody in particular', sub: 'the prompt describes them' }]
    .concat(chars.map(c => ({ id: c.id, name: c.name || c.id, sub: c.has_voice ? 'face + voice' : 'face' })));
  g.innerHTML = pills.map(c => `<button type="button" class="pill-btn${c.id === OS.character ? ' active' : ''}" data-os-character="${osEsc(c.id)}">
      <span>${osEsc(c.name)}</span><span class="sub">${osEsc(c.sub)}</span></button>`).join('');
  g.querySelectorAll('[data-os-character]').forEach(b => b.onclick = () => osSetCharacter(b.dataset.osCharacter));
  const meta = osEl('osCharacterMeta');
  if (meta) meta.textContent = chars.length
    ? 'optional · a trained character, with its own face and voice'
    : 'optional · train a character (Train Character tab) and it appears here';
  const note = osEl('osCharacterNote');
  if (note) {
    note.hidden = !OS.character;
    note.textContent = OS.character
      ? 'Quality is now the character\'s own set, and "Hand off where the line ends" is on so the next part starts while the mouth still moves — both can be changed below.'
      : '';
  }
}
function osRenderToggles() {
  document.querySelectorAll('[data-os-toggle]').forEach(group => {
    const name = group.dataset.osToggle;
    const cur = name === 'light' ? OS.light : name === 'retake' ? OS.retake : OS.handoff;
    group.querySelectorAll('[data-os-value]').forEach(b => b.classList.toggle('active', b.dataset.osValue === cur));
  });
}
function osRenderAnchor() {
  const thumb = osEl('osAnchorThumb'), clear = osEl('osAnchorClear'), name = osEl('osAnchorName'), pick = osEl('osAnchorPick');
  const on = !!OS.image;
  if (thumb) { thumb.hidden = !on; if (on) thumb.src = `/file?path=${encodeURIComponent(OS.image)}`; else thumb.removeAttribute('src'); }
  if (clear) clear.hidden = !on;
  if (name) name.textContent = on ? OS.image.split('/').pop() : '';
  if (pick) pick.textContent = on ? 'Change the image' : 'Choose an image';
}
// One row per five seconds, the stamp beside its own field. A row past the
// length is shown, marked, and dropped on submit — the user sees exactly
// what will and will not render.
function osRenderBeats() {
  const box = osEl('osBeats');
  if (!box) return;
  const n = OS.seconds / 5;
  const beats = OS.beats || [];
  const rows = Math.max(n, beats.length);
  const focused = document.activeElement && document.activeElement.dataset ? document.activeElement.dataset.osBeat : null;
  box.innerHTML = Array.from({ length: rows }, (_, i) => {
    const over = i >= n;
    const stamp = over ? '·' : osClock(i * 5);
    const ph = i === 0 ? 'what happens in the first five seconds' : (over ? 'past the end — will be dropped' : '');
    return `<div class="os-beat-row${over ? ' over' : ''}"><span class="os-stamp">${stamp}</span>`
      + `<input type="text" class="sb-input os-beat" data-os-beat="${i}" value="${osEsc(beats[i] || '')}" placeholder="${osEsc(ph)}" `
      + `autocomplete="off" spellcheck="false" aria-label="beat at ${osClock(i * 5)}"></div>`;
  }).join('');
  box.querySelectorAll('[data-os-beat]').forEach(inp => {
    const idx = parseInt(inp.dataset.osBeat, 10);
    inp.addEventListener('input', () => { OS.beats[idx] = inp.value; osBeatsInput(); });
    inp.addEventListener('keydown', e => {
      if (e.key === 'Enter') {
        e.preventDefault();
        const next = box.querySelector(`[data-os-beat="${idx + 1}"]`);
        if (next) next.focus();
      } else if (e.key === 'Backspace' && !inp.value && idx > 0) {
        e.preventDefault();
        const prev = box.querySelector(`[data-os-beat="${idx - 1}"]`);
        if (prev) prev.focus();
      }
    });
    // A pasted list fills this row and the rows after it, one line each.
    inp.addEventListener('paste', e => {
      const text = (e.clipboardData || window.clipboardData || {}).getData ? (e.clipboardData || window.clipboardData).getData('text') : '';
      if (!text || text.indexOf('\n') === -1) return;
      e.preventDefault();
      const lines = text.split('\n').map(x => x.trim());
      lines.forEach((l, k) => { OS.beats[idx + k] = l; });
      osRenderBeats(); osBeatsInput();
      const back = box.querySelector(`[data-os-beat="${idx}"]`); if (back) back.focus();
    });
  });
  if (focused !== null) { const again = box.querySelector(`[data-os-beat="${focused}"]`); if (again) again.focus(); }
}
function osBeatsInput() {
  const hint = osEl('osBeatsHint');
  if (!hint) return;
  const n = OS.seconds / 5;
  const lines = osBeatsList();
  const filled = lines.filter(Boolean).length;
  const extra = lines.length - n;
  hint.textContent = filled === 0
    ? `one line per five seconds · none written — the prompt carries the whole shot`
    : `${n} lines of 5 s · ${filled} written`
      + (filled < n ? ' · a blank row holds that moment' : '')
      + (extra > 0 ? ` · ${extra} row${extra === 1 ? '' : 's'} past the end will be dropped` : '');
}
function osRenderSummary() {
  const el = osEl('osSummary');
  if (!el) return;
  const who = (OS.engine === 'ltx' && OS.character)
    ? (((OS.options || {}).characters || []).find(c => c.id === OS.character) || {}).name || OS.character
    : '';
  el.innerHTML = `<strong>${osLengthLabel(OS.seconds)}</strong> · ${osPartsText(OS.seconds)}`
    + ` · ${OS.engine === 'h3' ? 'Hailuo H3' : 'LTX 2.5'}${who ? ` · ${osEsc(who)}` : ''}`;
}
function osRenderAll() {
  osRenderEngine(); osRenderLengths(); osRenderQualities(); osRenderCharacters();
  osRenderToggles(); osRenderAnchor(); osRenderBeats(); osBeatsInput(); osRenderSummary();
  osEstimate();
}

// ---- state changes ----------------------------------------------------------
function osSetEngine(e) {
  e = e === 'h3' ? 'h3' : 'ltx';
  if (e === 'h3' && !osH3Available()) return;
  OS.engine = e;
  if (e === 'h3' && OS.character) {
    // H3 renders no trained character: the pick is cleared, not carried
    // silently into a job that cannot use it.
    OS.character = '';
    if (!OS.handoffTouched) OS.handoff = 'last';
  }
  osRenderAll();
}
function osSetSeconds(s) {
  s = parseInt(s, 10);
  if (!OS_SECONDS.includes(s)) return;
  OS.seconds = s;
  osRenderLengths(); osRenderBeats(); osBeatsInput(); osRenderSummary(); osEstimate();
}
function osSetQuality(q) {
  if (OS.engine === 'h3') OS.quality.h3 = q;
  else if (OS.character) OS.quality.character = q;
  else OS.quality.ltx = q;
  osRenderQualities(); osEstimate();
}
function osSetCharacter(id) {
  OS.character = String(id || '');
  // A character speaks: unless the user already chose, hand off where the
  // line ends (the measured cure for the voice-over on the next part).
  if (!OS.handoffTouched) OS.handoff = OS.character ? 'speech' : 'last';
  osRenderCharacters(); osRenderQualities(); osRenderToggles(); osRenderSummary(); osEstimate();
}
function osToggle(name, value) {
  if (name === 'light') OS.light = value === 'off' ? 'off' : 'on';
  else if (name === 'retake') OS.retake = value === 'off' ? 'off' : 'on';
  else if (name === 'handoff') { OS.handoff = value === 'speech' ? 'speech' : 'last'; OS.handoffTouched = true; }
  osRenderToggles();
}
function osSplitPrompt() {
  const prompt = String((osEl('osPrompt') || {}).value || '');
  const sents = prompt.split(/(?<=[.!?])\s+/).map(x => x.trim()).filter(Boolean);
  const n = OS.seconds / 5;
  const out = [];
  for (let i = 0; i < n; i++) out.push(sents[i] || '');
  while (out.length && !out[out.length - 1]) out.pop();
  osSetBeats(out);
}
async function osPlanBeats() {
  const btn = osEl('osPlanBtn'), hint = osEl('osBeatsHint');
  const prompt = String((osEl('osPrompt') || {}).value || '').trim();
  if (!prompt) { if (hint) hint.textContent = 'write the shot first — the planner writes the beats from it'; return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Writing the beats…'; }
  if (hint) hint.textContent = 'the planner is writing one beat per five seconds — about half a minute';
  try {
    const r = await fetch('/oneshot/plan', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, seconds: OS.seconds, engine: OS.engine }) });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'the planner did not answer');
    osSetBeats(d.beats || []);
  } catch (e) {
    if (hint) hint.textContent = `could not write the beats: ${e.message || e}`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Write the beats for me'; }
  }
}
async function osEstimate() {
  const el = osEl('osEstimate');
  if (!el) return;
  const seq = ++OS.estimateSeq;
  el.textContent = `${osLengthLabel(OS.seconds)} · ${osPartsText(OS.seconds)} · pricing…`;
  try {
    const q = osActiveQuality();
    const r = await fetch(`/oneshot/estimate?engine=${encodeURIComponent(OS.engine)}&quality=${encodeURIComponent(q)}&seconds=${OS.seconds}`);
    const d = await r.json();
    if (seq !== OS.estimateSeq) return;
    if (!d.ok) { el.textContent = d.error || ''; return; }
    el.textContent = `${osLengthLabel(d.seconds)} · ${osPartsText(d.seconds)}`
      + (d.eta ? ` · about ${String(d.eta).replace(/^~\s*/, '').split(' · ')[0]} on this Mac` : '')
      + (OS.retake === 'on' ? ' · a part that drifts is rendered once more' : '');
  } catch (e) {
    if (seq === OS.estimateSeq) el.textContent = '';
  }
}
async function osUpload(file) {
  if (!file) return;
  const name = osEl('osAnchorName');
  if (name) name.textContent = `Uploading ${file.name}…`;
  try {
    const fd = new FormData(); fd.append('image', file);
    const r = await fetch('/upload', { method: 'POST', body: fd });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || 'upload failed');
    OS.image = d.path;
  } catch (e) {
    alert(`Upload failed: ${e.message || e}`);
  }
  osRenderAnchor();
}
function osClearAnchor() { OS.image = ''; osRenderAnchor(); }

// ---- generate + status ------------------------------------------------------
async function osGenerate() {
  const btn = osEl('osGenBtn'), est = osEl('osEstimate');
  const doc = osDocument();
  if (!doc.prompt) {
    if (est) est.textContent = 'write the shot first — one paragraph is a complete request';
    const p = osEl('osPrompt'); if (p) p.focus();
    return;
  }
  if (btn) { btn.disabled = true; btn.textContent = 'Queueing…'; }
  try {
    const r = await fetch('/oneshot', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(doc) });
    const d = await r.json();
    if (!d.ok) throw new Error(d.error || `HTTP ${r.status}`);
    OS.lastSubmitted = d.id;
    if (est) {
      const plan = d.plan || {};
      est.textContent = `queued · ${osLengthLabel(plan.seconds || OS.seconds)} in ${plan.parts || '?'} parts`
        + (plan.eta ? ` · about ${String(plan.eta).replace(/^~\s*/, '').split(' · ')[0]}` : '');
    }
    osStatusTick();
    if (typeof poll === 'function') { try { poll(); } catch (e) {} }
  } catch (e) {
    if (est) est.textContent = `not queued: ${e.message || e}`;
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Generate'; }
  }
}
function osVerdict(v) {
  if (v === null || v === undefined) return { cls: 'quiet', text: 'silent' };
  const n = Number(v);
  if (n >= 0.25) return { cls: 'ok', text: `voice on the mouth (${n >= 0 ? '+' : ''}${n.toFixed(2)})` };
  if (n >= 0.2) return { cls: 'mid', text: `borderline (${n >= 0 ? '+' : ''}${n.toFixed(2)})` };
  return { cls: 'bad', text: `voice-over (${n >= 0 ? '+' : ''}${n.toFixed(2)})` };
}
function osPaintStatus(d) {
  const card = osEl('osStatus'), title = osEl('osStatusTitle'), meta = osEl('osStatusMeta'), parts = osEl('osParts'), log = osEl('osLog');
  if (!card) return;
  const cur = d.current;
  const recent = (d.recent || [])[0];
  const show = cur || (d.queued || []).length || recent;
  card.hidden = !show;
  if (!show) return;
  const queued = d.queued || [];
  const paused = !!d.paused;
  // The job this tab queued comes first; otherwise whatever is running.
  const mineIdx = OS.lastSubmitted ? queued.findIndex(q => q.id === OS.lastSubmitted) : -1;
  const mine = mineIdx >= 0 ? queued[mineIdx] : null;
  const t = (cur && cur.id === OS.lastSubmitted) ? cur : (mine || cur || (queued.length ? queued[0] : recent));
  const name = (j) => (j && j.label) ? ` · ${j.label}` : '';
  if (cur && t === cur) {
    if (title) title.textContent = `Rendering${name(cur)}`;
    if (meta) meta.textContent = `part ${cur.part || 1} of ${cur.parts} · ${osLengthLabel(cur.seconds)} on ${cur.engine === 'h3' ? 'Hailuo H3' : 'LTX 2.5'}`
      + (queued.length ? ` · ${queued.length} more waiting` : '');
  } else if (t && queued.indexOf(t) >= 0) {
    const place = queued.indexOf(t);
    if (title) title.textContent = `${paused ? 'Queued, paused' : 'Waiting'}${name(t)}`;
    if (meta) meta.textContent = `${osLengthLabel(t.seconds)} in ${t.parts} parts`
      + (place === 0 ? (cur ? ' · next after the one rendering' : (paused ? ' · starts when you resume the queue' : ' · starts now')) : ` · ${place} ahead of it`)
      + (paused && place > 0 ? ' · the queue is paused' : '');
  } else {
    const st = recent.status === 'done' ? 'Done' : recent.status === 'cancelled' ? 'Stopped' : 'Failed';
    if (title) title.textContent = `${st}${recent.label ? ` · ${recent.label}` : ''}`;
    if (meta) meta.textContent = recent.status === 'done'
      ? `${osLengthLabel(recent.seconds)} · in the gallery on the right${recent.elapsed_sec ? ` · ${Math.round(recent.elapsed_sec / 60)} min` : ''}`
      : (recent.error || '');
  }
  if (parts) {
    const n = t.parts || 0, done = (t.lipsync || []).length;
    parts.innerHTML = Array.from({ length: n }, (_, i) => {
      const k = i + 1;
      let cls = 'todo', text = `part ${k}`;
      if (i < done) {
        const v = osVerdict((t.lipsync || [])[i]);
        cls = v.cls; text = `part ${k} · ${v.text}`;
        const drift = (t.drift || [])[i];
        if (typeof drift === 'number' && drift > 0.28) text += ' · light drifted';
      } else if (cur && k === (cur.part || 1)) { cls = 'now'; text = `part ${k} · rendering`; }
      return `<span class="os-part ${cls}">${osEsc(text)}</span>`;
    }).join('');
  }
  if (log) log.textContent = (d.log || []).slice(-8).map(l => l.replace(/^\[\d\d:\d\d:\d\d\]\s*/, '').replace(/^\[take\]\s*/, '')).join('\n');
}
async function osStatusTick() {
  try {
    const r = await fetch('/oneshot/status');
    const d = await r.json();
    if (d && d.ok) osPaintStatus(d);
  } catch (e) {}
}
function osStartStatus() {
  osStopStatus();
  osStatusTick();
  OS.statusTimer = setInterval(osStatusTick, 3000);
}
function osStopStatus() {
  if (OS.statusTimer) { clearInterval(OS.statusTimer); OS.statusTimer = null; }
}

// ---- options ----------------------------------------------------------------
async function osLoadOptions(force) {
  if (!force && OS.options && (Date.now() - OS.optionsAt) < 60000) return OS.options;
  try {
    const r = await fetch('/oneshot/options');
    const d = await r.json();
    if (d && d.ok) { OS.options = d; OS.optionsAt = Date.now(); }
  } catch (e) {}
  if (OS.engine === 'h3' && !osH3Available()) OS.engine = 'ltx';
  return OS.options;
}

// ---- entering, leaving, loading ---------------------------------------------
function oneshotTabEnter() {
  osWire();
  // A pane that scrolled sideways during an earlier layout must not open shifted.
  const pane = document.querySelector('aside.form-pane');
  if (pane) { pane.scrollLeft = 0; pane.scrollTop = 0; }
  osRenderAll();
  osLoadOptions(true).then(() => osRenderAll());
  osStartStatus();
}
function oneshotTabLeave() {
  osStopStatus();
}
// A finished one shot's Load Params lands here with the job's params: the
// document is rebuilt from the take block, and the tab opens on it.
function oneshotOpenFromParams(p) {
  p = p || {};
  const take = p.take || {};
  OS.engine = (take.engine || p.engine) === 'h3' ? 'h3' : 'ltx';
  const s = parseInt(take.seconds || p.take_seconds || 60, 10);
  OS.seconds = OS_SECONDS.includes(s) ? s : 60;
  OS.character = String(p.character_id || '');
  OS.image = String(p.image || '');
  OS.light = take.light_lock === '' ? 'off' : 'on';
  OS.retake = take.retake === false ? 'off' : 'on';
  OS.handoff = take.handoff === 'speech' ? 'speech' : 'last';
  OS.handoffTouched = true;
  if (OS.engine === 'h3') OS.quality.h3 = p.h3_quality || OS.quality.h3;
  else if (OS.character) OS.quality.character = p.quality_choice || OS.quality.character;
  else OS.quality.ltx = p.quality || OS.quality.ltx;
  const prompt = osEl('osPrompt'); if (prompt) prompt.value = String(p.prompt || '');
  const beats = (take.beats || take.beat_prompts || []);
  OS.beats = Array.isArray(beats) ? beats.map(b => String(b || '').replace(/\s+Continuity:.*$/, '')) : [];
  const cam = osEl('osCamera'); if (cam) cam.value = String(take.camera || '');
  const seed = osEl('osSeed'); if (seed) seed.value = (p.seed && String(p.seed) !== '-1') ? String(p.seed) : '';
  const label = osEl('osLabel'); if (label) label.value = String(p.preset_label || '');
  const nm = osEl('osNoMusic'); if (nm) nm.checked = String(p.no_music || '') === 'on';
  if (typeof workflowSwitch === 'function') workflowSwitch('oneshot');
  else oneshotTabEnter();
}

// ---- wiring (once) ----------------------------------------------------------
function osWire() {
  if (OS.wired) return;
  OS.wired = true;
  document.querySelectorAll('#osEngineGroup [data-os-engine]').forEach(b => b.onclick = () => osSetEngine(b.dataset.osEngine));
  document.querySelectorAll('[data-os-toggle]').forEach(group => {
    group.querySelectorAll('[data-os-value]').forEach(b => b.onclick = () => osToggle(group.dataset.osToggle, b.dataset.osValue));
  });
  const split = osEl('osSplitBtn'); if (split) split.onclick = osSplitPrompt;
  const plan = osEl('osPlanBtn'); if (plan) plan.onclick = osPlanBeats;
  const pick = osEl('osAnchorPick'); const file = osEl('osAnchorFile');
  if (pick && file) {
    pick.onclick = () => file.click();
    file.addEventListener('change', () => { if (file.files[0]) osUpload(file.files[0]); file.value = ''; });
  }
  const clear = osEl('osAnchorClear'); if (clear) clear.onclick = osClearAnchor;
  const gen = osEl('osGenBtn'); if (gen) gen.onclick = osGenerate;
  const prompt = osEl('osPrompt');
  if (prompt) prompt.addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') { e.preventDefault(); osGenerate(); } });
}

// ---- published to the page --------------------------------------------------
Object.assign(globalThis, {
  oneshotTabEnter, oneshotTabLeave, oneshotOpenFromParams, osWire,
  osSetEngine, osSetSeconds, osSetQuality, osSetCharacter, osToggle,
  osBeatsInput, osSplitPrompt, osPlanBeats, osGenerate, osDocument, osBeatsList, osSetBeats, osRenderBeats,
  osLengthLabel, osPartSeconds, osPartsText, osPartsShort, osClock, osVerdict, osPaintStatus,
});
