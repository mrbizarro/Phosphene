// webapp/js/speech.js — Audio → Speech: text to speech in a preset voice or a
// saved character's voice (speech_engine.py on the server, docs/SPEECH.md).
//
// The pane, its install card, the voice picker and Generate. A finished line
// lands in Outputs like any WAV, so "Use this audio for… → Lip-sync" works on
// it from the gallery too; the pane also offers it right under Generate.
//
// SAFETY: a cloned voice (a character's sample or a clip you add) needs the
// "this is my voice / I have permission" box ticked. The server refuses a
// clone job without it, so the box is the explanation, not the guard.
//
// Everything a handler in the markup calls is published at the bottom.

const SPEECH = {
  kind: 'preset',
  voices: null,            // /speech/voices payload
  clipPath: null,          // uploaded reference clip (server path)
  clipName: null,
  lastJobId: null,         // the job this pane queued last
  lastShown: null,         // the output path the pane is showing
  busy: false,
  estTimer: null,
  estReq: 0,
};
let speechInstall = { state: 'idle', active: false };
let speechInstallSeenActive = false;
let speechStatusCache = null;

function _sel(id) { return document.getElementById(id); }
function _sesc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

function speechInit() {
  speechStatusCache = BOOT.speech || null;
  speechInstall = BOOT.speech_install || speechInstall;
  try {
    const k = localStorage.getItem('phos_speech_kind');
    if (['preset', 'character', 'clip'].includes(k)) SPEECH.kind = k;
  } catch (_) {}
}

// Called when the Speech pane becomes visible (characters.js decides which
// Audio pane shows). Loads the voice list once, then on every show refreshes
// the characters (a character trained since may now have a voice).
async function speechShow() {
  speechRenderInstall();
  try {
    const r = await fetch('/speech/voices');
    if (r.ok) SPEECH.voices = await r.json();
  } catch (_) {}
  speechRenderVoices();
  speechFormChanged();
}

function speechRenderVoices() {
  const v = SPEECH.voices || {};
  const sel = _sel('speechVoice');
  if (sel && v.presets && !sel.options.length) {
    let saved = 'ryan';
    try { saved = localStorage.getItem('phos_speech_voice') || 'ryan'; } catch (_) {}
    sel.innerHTML = v.presets.map(p =>
      `<option value="${_sesc(p.id)}"${p.id === saved ? ' selected' : ''}>${_sesc(p.label)} — ${_sesc(p.sub)}${p.native !== 'English' ? ` (${_sesc(p.native)} accent)` : ''}</option>`).join('');
  }
  const pills = _sel('speechStylePills');
  if (pills && v.styles && !pills.children.length) {
    pills.innerHTML = v.styles.map(s =>
      `<button type="button" class="pill-btn${s.id === 'natural' ? ' active' : ''}" data-value="${_sesc(s.id)}" title="${_sesc(s.instruct || 'The voice as it is')}" onclick="speechStylePick(this)">${_sesc(s.label)}</button>`).join('');
  }
  const ch = _sel('speechCharacter');
  if (ch) {
    const keep = ch.value;
    const list = v.characters || [];
    ch.innerHTML = list.length
      ? list.map(c => `<option value="${_sesc(c.id)}">${_sesc(c.name)}</option>`).join('')
      : '<option value="">No character has a voice sample yet</option>';
    if (keep && list.some(c => c.id === keep)) ch.value = keep;
    // A rebuilt list that lands on a different character must never carry the
    // confirmation given for the previous one (programmatic selection fires no
    // change event).
    if (ch.value !== keep) { speechConsentReset(); speechLoadCharacterWords(); }
    const hint = _sel('speechCharacterHint');
    if (hint) hint.textContent = list.length
      ? T('speech.uses_the_voice_sample_from', "Uses the voice sample from this character's training.")
      : T('speech.add_a_voice_sample_when', "Add a voice sample when training a character in Train, or use a clip here.");
  }
  const lang = _sel('speechLanguage');
  if (lang && v.languages && !lang.options.length) {
    lang.innerHTML = v.languages.filter(l => l !== 'auto').map(l =>
      `<option value="${_sesc(l)}"${l === 'english' ? ' selected' : ''}>${_sesc(l[0].toUpperCase() + l.slice(1))}</option>`).join('');
  }
  document.querySelectorAll('#speechKindSeg .seg-btn').forEach(b => {
    const on = b.dataset.value === SPEECH.kind;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', String(on));
  });
}

// The voice-owner confirmation belongs to ONE voice: any change of voice
// clears it, and it is never stored, so it is asked again for a new voice.
function speechConsentReset() {
  const c = _sel('speechConsent');
  if (c) c.checked = false;
}

// A character's sample never changes, so the words typed for it are kept
// (per browser) and come back the next time that character is picked.
function _speechWordsKey() { return 'phos_speech_words_' + ((_sel('speechCharacter') || {}).value || ''); }
function speechLoadCharacterWords() {
  if (SPEECH.kind !== 'character') return;
  let w = '';
  try { w = localStorage.getItem(_speechWordsKey()) || ''; } catch (_) {}
  _sel('speechClipText').value = w;
}
function speechClipTextChanged() {
  if (SPEECH.kind === 'character') {
    try { localStorage.setItem(_speechWordsKey(), _sel('speechClipText').value || ''); } catch (_) {}
  }
  speechFormChanged();
}

function speechCharacterChanged() {
  speechConsentReset();
  speechLoadCharacterWords();
  speechFormChanged();
}

function speechKindPick(btn) {
  if (SPEECH.kind !== btn.dataset.value) {
    speechConsentReset();
    _sel('speechClipText').value = '';
  }
  SPEECH.kind = btn.dataset.value;
  speechLoadCharacterWords();
  try { localStorage.setItem('phos_speech_kind', SPEECH.kind); } catch (_) {}
  speechRenderVoices();
  speechFormChanged();
}

function speechStylePick(btn) {
  _sel('speechStyle').value = btn.dataset.value;
  btn.parentElement.querySelectorAll('.pill-btn').forEach(b => {
    b.classList.toggle('active', b === btn);
    b.setAttribute('aria-pressed', String(b === btn));
  });
  speechFormChanged();
}

function speechRenderClip() {
  const slot = _sel('speechClipSlot');
  if (!slot) return;
  slot.innerHTML = SPEECH.clipPath
    ? `<div class="ss-tag">${_sesc(SPEECH.clipName || SPEECH.clipPath.split('/').pop())}</div><div class="hint">${T('speech.click_to_replace', 'Click to replace')}</div>`
    : '<div class="hint">Add 5–20 seconds of clear speech from one person, no music (WAV, MP3, M4A or FLAC)</div>';
}

async function speechClipUpload(files) {
  const file = files && files[0];
  if (!file) return;
  const status = _sel('speechStatus');
  if (status) status.textContent = T('speech.uploading_the_clip', 'Uploading the clip…');
  try {
    const fd = new FormData();
    fd.append('audio', file);
    const r = await fetch('/upload', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
    SPEECH.clipPath = data.path;
    SPEECH.clipName = file.name;
    speechConsentReset();
    if (status) status.textContent = '';
  } catch (e) {
    if (status) status.textContent = T('speech.clip_upload_failed', 'Clip upload failed: ') + (e.message || 'unknown');
  }
  speechFormChanged();
}

function speechFormChanged() {
  const kind = SPEECH.kind;
  const presetRow = _sel('speechPresetRow');
  if (!presetRow) return;
  presetRow.hidden = kind !== 'preset';
  _sel('speechCharacterRow').hidden = kind !== 'character';
  _sel('speechClipRow').hidden = kind !== 'clip';
  const words = _sel('speechClipText');
  words.hidden = kind === 'preset';
  words.placeholder = kind === 'character'
    ? "Optional: type the words in this character's voice sample, for a closer match"
    : "Optional: type the clip's words for a closer voice match";
  _sel('speechConsentRow').hidden = kind === 'preset';
  _sel('speechCloneHint').hidden = kind === 'preset';
  speechRenderClip();
  const speed = Number(_sel('speechSpeed').value || 1);
  _sel('speechSpeedVal').textContent = speed.toFixed(2) + '×';
  const lang = (_sel('speechLanguage') || {}).value || 'english';
  const seed = Number((_sel('speechSeed') || {}).value || -1);
  const sum = _sel('speechAdvancedSummary');
  if (sum) sum.textContent = `${lang[0].toUpperCase() + lang.slice(1)} · ${seed >= 0 ? 'seed ' + seed : T('speech.random_seed', 'random seed')}`;
  try { if (_sel('speechVoice').value) localStorage.setItem('phos_speech_voice', _sel('speechVoice').value); } catch (_) {}
  const st = speechStatusCache || {};
  const ready = !!st.available;
  const pane = _sel('speechPane');
  if (pane) pane.classList.toggle('speech-locked', !ready);
  const btn = _sel('speechGenBtn');
  const text = (_sel('speechText').value || '').trim();
  const needsConsent = kind !== 'preset' && !_sel('speechConsent').checked;
  const noVoice = (kind === 'character' && !_sel('speechCharacter').value) || (kind === 'clip' && !SPEECH.clipPath);
  const installing = !!(speechInstall && speechInstall.active);
  if (btn) {
    btn.disabled = SPEECH.busy || installing || (ready && (!text || needsConsent || noVoice));
    btn.textContent = ready ? T('speech.generate', 'Generate') : installing ? T('speech.installing', 'Installing…') : `Install Speech (~${st.install_gb || 6} GB)`;
    btn.title = !ready ? T('speech.downloads_the_voice_models_once', 'Downloads the voice models once, then Generate works offline')
      : !text ? T('speech.write_the_line_first', 'Write the line first') : needsConsent ? T('speech.tick_the_voice_confirmation_first', 'Tick the voice confirmation first')
      : noVoice ? (kind === 'clip' ? T('speech.add_a_clip_of_the', 'Add a clip of the voice first') : T('speech.pick_a_character_with_a', 'Pick a character with a voice sample')) : '';
  }
  speechEstimateSoon(text, speed);
}

// The server prices the line (rule: the browser computes no estimate of its
// own). Debounced; a stale answer is dropped.
function speechEstimateSoon(text, speed) {
  clearTimeout(SPEECH.estTimer);
  const fit = _sel('speechFit');
  const est = _sel('speechEstimate');
  if (!text) { if (fit) fit.textContent = ''; if (est) est.textContent = ''; return; }
  SPEECH.estTimer = setTimeout(async () => {
    const req = ++SPEECH.estReq;
    try {
      const r = await fetch(`/speech/estimate?speed=${encodeURIComponent(speed)}&text=${encodeURIComponent(text.slice(0, 2000))}`);
      const e = await r.json();
      if (req !== SPEECH.estReq) return;
      if (fit) {
        fit.textContent = `About ${e.audio_sec} s of speech.` + (e.lipsync_fits ? T('speech.fits_one_lip_sync_clip', ' Fits one lip-sync clip.')
          : T('speech.one_lip_sync_clip_fits', ' One lip-sync clip fits about {v} s. Split your script into shorter parts.', {v: (speechStatusCache || {}).lipsync_max_sec || 10}));
        fit.classList.toggle('warn', !e.lipsync_fits);
      }
      if (est) est.textContent = (speechStatusCache || {}).available ? `≈ ${e.eta_sec} s · estimate` : '';
    } catch (_) {}
  }, 250);
}

// ---- install card ---------------------------------------------------------

function speechRenderInstall() {
  const el = _sel('speechInstallPanel');
  if (!el) return;
  const st = speechStatusCache || {};
  const inst = speechInstall || {};
  el.hidden = !!st.available && !inst.active;
  if (el.hidden) return;
  const size = `~${st.install_gb || 6} GB`;
  let html;
  if (st.capable === false) {
    html = `<p class="mi-lead">${T('speech.speech_needs_about_gb_of', 'Speech needs about {v} GB of unified memory, so it can\'t run on this Mac.', {v: _sesc(st.min_ram_gb || 16)})}</p>`;
  } else if (inst.active) {
    const pct = inst.percent || 1;
    const bytes = inst.bytes_total ? ` · ${(inst.bytes_done / 1e9).toFixed(1)} of ${(inst.bytes_total / 1e9).toFixed(1)} GB` : '';
    html = `<p class="mi-lead"><b>${inst.state === 'stopping' ? T('speech.stopping', 'Stopping…') : T('speech.installing_speech', 'Installing Speech…')}</b> ${pct}%</p>
      <div class="progress-bar"><div class="fill" style="width:${pct}%"></div></div>
      <p class="mi-sub">Step ${(inst.step_index || 0) + 1} of ${inst.steps || 4} · ${_sesc(inst.step_label || '')}${bytes}</p>
      <p class="mi-line">${_sesc(inst.last_line || '')}</p>
      <div class="mi-actions"><button type="button" class="danger" onclick="speechInstallStop()" ${inst.state === 'stopping' ? 'disabled' : ''}>${T('speech.stop', 'Stop')}</button>
      <span class="mi-note">${T('speech.keep_working_speech_is_ready', 'Keep working. Speech is ready to use when the install finishes.')}</span></div>`;
  } else {
    const failed = inst.state === 'failed' && inst.error;
    const stopped = inst.state === 'stopped';
    html = `<p class="mi-lead">${T('speech.turn_a_script_into_speech', 'Turn a script into speech with nine preset voices, your character\'s voice or a clip you add. Download the engine and voice models once ({size}). Interrupted downloads resume. After installation, Speech works offline.', {size})}</p>
      ${failed ? `<p class="mi-error">${_sesc(inst.error)}</p>` : ''}
      ${stopped ? '<p class="mi-sub">Install stopped. Click again to resume where it left off.</p>' : ''}
      <div class="mi-actions"><button type="button" class="primary" onclick="speechInstallStart()">${failed || stopped ? T('speech.resume_install', 'Resume install') : T('speech.install_speech', 'Install Speech')} (${size})</button></div>
      <p class="mi-note">${T('speech.needs_at_least_gb_of', 'Needs at least {v} GB of unified memory and about {v2} GB of free disk.', {v: _sesc(st.min_ram_gb || 16), v2: Math.ceil((st.install_gb || 6) + 2)})}</p>`;
  }
  el.innerHTML = html;
}

async function speechInstallStart() {
  try {
    const r = await fetch('/speech/install', { method: 'POST', body: new URLSearchParams({}) });
    const res = await r.json().catch(() => ({}));
    if (!r.ok && !res.install) throw new Error(res.error || 'Could not start the install');
    if (res.started || res.install) speechInstall = { ...speechInstall, active: true, state: 'running', percent: 1, step_index: 0 };
  } catch (e) {
    speechInstall = { state: 'failed', active: false, error: e.message || String(e) };
  }
  speechRenderInstall();
  speechFormChanged();
  if (typeof poll === 'function') poll();
}

async function speechInstallStop() {
  try { await fetch('/speech/install/stop', { method: 'POST' }); } catch (_) {}
  if (typeof poll === 'function') poll();
}

// /status, every poll: install progress, availability, and the line this
// pane queued (shown under Generate once it is written).
function speechStatusUpdate(s) {
  if (!s) return;
  if (s.speech) speechStatusCache = s.speech;
  if (s.speech_install) {
    const was = speechInstallSeenActive;
    speechInstall = s.speech_install;
    if (speechInstall.active) speechInstallSeenActive = true;
    else if (was) {
      speechInstallSeenActive = false;
      if (speechInstall.state === 'done' && typeof phosToast === 'function') {
        phosToast(T('speech.speech_is_ready_write_a', 'Speech is ready. Write a script and click Generate.'), { kind: 'success' });
      }
    }
  }
  const pane = _sel('speechPane');
  if (!pane || pane.hidden) return;
  speechRenderInstall();
  speechFormChanged();
  if (SPEECH.lastJobId && Array.isArray(s.history)) {
    const j = s.history.find(x => x.id === SPEECH.lastJobId);
    if (j && j.status === 'done' && j.output_path && j.output_path !== SPEECH.lastShown) speechShowLast(j.output_path);
    if (j && ['failed', 'stopped', 'cancelled'].includes(j.status)) {
      _sel('speechStatus').textContent = j.status === 'failed' ? (j.error || 'Speech failed.') : T('speech.stopped_nothing_was_saved', 'Stopped. Nothing was saved.');
      SPEECH.lastJobId = null;
    }
  }
}

function speechShowLast(path) {
  SPEECH.lastShown = path;
  const box = _sel('speechLast');
  if (!box) return;
  const o = (typeof findOutputByPath === 'function') ? findOutputByPath(path) : null;
  const url = (o && o.url) || ('/file?path=' + encodeURIComponent(path));
  const attr = _sesc(JSON.stringify(path));
  box.innerHTML = `<audio controls preload="metadata" src="${_sesc(url)}"></audio>
    <div class="actions">
      <button type="button" class="primary" onclick="speechLipSync(${attr})">${T('speech.lip_sync_it', 'Lip-sync it')}</button>
      <button type="button" class="ghost-btn" aria-haspopup="menu" onclick="openAudioUseMenu(event, ${attr})">${T('speech.use_this_audio_for', 'Use this audio for…')}</button>
    </div>`;
  box.hidden = false;
  _sel('speechStatus').textContent = T('speech.done_also_saved_in_outputs', 'Done. Also saved in Outputs.');
}

function speechLipSync(path) {
  if (typeof audioUseApply === 'function') audioUseApply(path, 'lipsync');
  const scene = _sel('audioStudioSceneSound');
  if (scene) scene.checked = true;
  // This is already clean speech; don't run source separation on it.
  const stem = _sel('audioStudioStemAuto');
  if (stem) stem.checked = false;
  if (typeof audioStudioRenderSeparator === 'function') audioStudioRenderSeparator();
  if (typeof audioStudioDurationChanged === 'function') audioStudioDurationChanged();
}

async function speechGenerate() {
  const st = speechStatusCache || {};
  if (!st.available) { speechInstallStart(); return; }
  const status = _sel('speechStatus');
  const kind = SPEECH.kind;
  const fd = new FormData();
  fd.set('mode', 'speech');
  fd.set('speech_text', _sel('speechText').value || '');
  fd.set('speech_voice_kind', kind);
  fd.set('speech_voice', kind === 'preset' ? _sel('speechVoice').value : '');
  fd.set('speech_character', kind === 'character' ? _sel('speechCharacter').value : '');
  fd.set('speech_ref_audio', kind === 'clip' ? (SPEECH.clipPath || '') : '');
  fd.set('speech_ref_text', kind !== 'preset' ? (_sel('speechClipText').value || '') : '');
  fd.set('speech_style', _sel('speechStyle').value || 'natural');
  fd.set('speech_instruct', kind === 'preset' ? (_sel('speechInstruct').value || '') : '');
  fd.set('speech_speed', _sel('speechSpeed').value || '1');
  fd.set('speech_language', (_sel('speechLanguage') || {}).value || 'english');
  fd.set('speech_seed', (_sel('speechSeed') || {}).value || '-1');
  if (kind !== 'preset' && _sel('speechConsent').checked) fd.set('speech_voice_consent', '1');
  SPEECH.busy = true;
  speechFormChanged();
  status.textContent = T('speech.queueing', 'Queueing…');
  try {
    const r = await fetch('/queue/add', { method: 'POST', body: new URLSearchParams(fd) });
    const res = await r.json();
    if (!r.ok || res.error) throw new Error(res.error || 'Could not queue the line');
    SPEECH.lastJobId = res.id;
    status.textContent = T('speech.queued_your_speech_plays_here', "Queued. Your speech plays here when it's ready.");
    if (typeof setMainOutputsFilter === 'function') setMainOutputsFilter('audio');
    if (typeof poll === 'function') await poll();
  } catch (e) {
    status.textContent = e.message || String(e);
  } finally {
    SPEECH.busy = false;
    speechFormChanged();
  }
}

Object.assign(globalThis, {
  speechInit, speechShow, speechKindPick, speechCharacterChanged, speechClipTextChanged, speechStylePick, speechClipUpload, speechFormChanged,
  speechInstallStart, speechInstallStop, speechStatusUpdate, speechGenerate, speechLipSync,
});
