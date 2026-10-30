// webapp/js/characters.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
// ====== Audio → Video pane ===================================================
// State for the Audio workflow tab. Routes to make_job with mode='a2v'.
// One drop-zone for audio (required) is owned here. The optional
// reference image was migrated to the shared .picker component
// (key='a2v_image') 2026-05-20 — its state lives in the hidden input
// #a2v_image, wired automatically via PICKERS.forEach(pickerWire) at
// boot, so this struct no longer carries an imagePath field.
const AUDIO_STUDIO = {
  busy: false,
  audioPath: null,      // server-side path returned by /upload
  audioName: null,      // display name for the drop-zone tag
  audioDuration: null,  // probed duration (seconds) if ffprobe returns it
  wired: false,         // init() runs once
};

// Compose shares Audio's existing queue and poll; the server supplies every ETA.
let audioModeChoice = null;
let musicBusy = false;

function musicComposeActive() {
  // Compose is ALWAYS a real surface now — installed or not. Not installed, it
  // is the place the install lives ("it kicks in when you try to use it").
  return document.body.dataset.workflow === 'audio' && audioModeChoice === 'compose';
}
function musicInit() {
  try { audioModeChoice = localStorage.getItem('phos_audio_mode'); } catch (_) {}
  if (!['compose', 'drive'].includes(audioModeChoice)) audioModeChoice = null;
  updateMusicAvailability({ music: BOOT.music, music_install: BOOT.music_install });
}
// The install task's last snapshot (/status.music_install), and whether this
// tab watched it run — "done" only earns a toast when we saw it happen.
let musicInstall = { state: 'idle', active: false };
let musicInstallSeenActive = false;
function updateMusicAvailability(s) {
  if (s && s.music) window._ENGINE_PROBES.music = s.music;
  if (s && s.music_install) {
    const was = musicInstallSeenActive;
    musicInstall = s.music_install;
    if (musicInstall.active) musicInstallSeenActive = true;
    else if (was) {
      musicInstallSeenActive = false;
      if (musicInstall.state === 'done' && typeof phosToast === 'function') {
        phosToast('Music engine installed — Compose is ready.', { kind: 'success' });
      }
    }
  }
  const music = window._ENGINE_PROBES.music;
  const ready = !!music.available;
  if (audioModeChoice == null) audioModeChoice = ready ? 'compose' : 'drive';
  const compose = audioModeChoice === 'compose';
  document.getElementById('audioModeGroup').hidden = false;
  document.getElementById('musicComposePane').hidden = !compose;
  document.getElementById('audioDrivePane').hidden = compose;
  document.querySelectorAll('#audioModeGroup [data-audio-mode]').forEach(b => {
    const active = b.dataset.audioMode === (compose ? 'compose' : 'drive');
    b.classList.toggle('active', active);
    b.setAttribute('aria-pressed', String(active));
  });
  // Not installed: the form stays visible (so you can see what you are
  // installing) but greyed, and the install panel sits on top of it.
  const pane = document.getElementById('musicComposePane');
  if (pane) pane.classList.toggle('music-locked', !ready);
  const panel = document.getElementById('musicInstallPanel');
  if (panel) {
    panel.hidden = ready && !musicInstall.active;
    if (!panel.hidden) musicInstallRender(panel, 'inline');
  }
  const modal = document.getElementById('musicInstallModal');
  if (modal && !modal.hidden) musicInstallRender(document.getElementById('musicInstallBody'), 'modal');
  musicFormChanged();
  if (document.body.dataset.workflow === 'audio') renderEngineSwitch();
}
function audioModeSet(mode) {
  audioModeChoice = mode === 'compose' ? 'compose' : 'drive';
  try { localStorage.setItem('phos_audio_mode', audioModeChoice); } catch (_) {}
  updateMusicAvailability();
  // Compose shows the songs (the Audio chip); Video modes switch back to Videos.
  if (audioModeChoice === 'compose') setMainOutputsFilter('audio');
}
function musicPick(id, button) {
  document.getElementById(id).value = button.dataset.value;
  button.parentElement.querySelectorAll('.pill-btn').forEach(b => {
    b.classList.toggle('active', b === button);
    b.setAttribute('aria-pressed', String(b === button));
  });
  musicFormChanged();
}
function musicFormChanged() {
  const instrumental = document.getElementById('musicInstrumental').checked;
  document.getElementById('musicLyrics').disabled = instrumental;
  // The lyrics editor (music.js) reads that same flag and goes quiet — the
  // words stay in the boxes, they are just not used while this is on.
  if (typeof musicLyricsLock === 'function') musicLyricsLock(instrumental);
  document.getElementById('musicInstrumentalPill').classList.toggle('on', instrumental);
  const seconds = Number(document.getElementById('musicMaxSeconds').value);
  document.getElementById('musicMaxSecondsVal').textContent = `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  const quality = document.getElementById('musicQuality').value;
  const cfgEl = document.getElementById('musicCfg');
  if (cfgEl) {
    document.getElementById('musicCfgVal').textContent = Number(cfgEl.value).toFixed(1);
    const prec = (document.getElementById('musicPrecision') || {}).value || '8bit';
    const parts = [];
    if (Math.abs(Number(cfgEl.value) - 1) >= 0.001) parts.push(`guidance ${Number(cfgEl.value).toFixed(1)}`);
    if (prec !== '8bit') parts.push(prec);
    const sum = document.getElementById('musicAdvancedSummary');
    if (sum) sum.textContent = parts.length ? parts.join(' · ') : 'defaults';
  }
  const music = window._ENGINE_PROBES.music;
  const estimate = ((music.estimates || {})[quality] || {})[String(seconds)];
  document.getElementById('musicEstimate').textContent = estimate
    ? `≈ ${estimate.eta} at full length · estimate` : '';
  musicCoverSummary();
  musicCoverInstallCard(music);
  musicLoraRender(music);
  musicLoraInstallCard(music);
  musicLoraSummaryText();
  const btn = document.getElementById('musicGenBtn');
  btn.disabled = musicBusy || !music.capable;
  btn.title = music.available ? '' : (music.capable
    ? 'The music engine is not installed yet — click to install it'
    : `YuE2 needs ${music.min_ram_gb} GB of unified memory`);
}
function musicFormParams() {
  const instrumental = document.getElementById('musicInstrumental').checked;
  return new URLSearchParams({
    mode: 'music', engine: 'music',
    // WHICH TASK WAS CHOSEN, in words the server can check (M6-07): a cover
    // was represented only by a non-empty recording path, so Cover with the
    // path left empty was indistinguishable from Write and composed a new
    // song instead of refusing.
    music_task: (document.getElementById('musicTask') || {}).value || 'write',
    music_lyrics: instrumental ? '' : document.getElementById('musicLyrics').value,
    music_style: document.getElementById('musicStyle').value,
    music_mode: document.getElementById('musicMode').value,
    music_instrumental: instrumental ? 'on' : 'off',
    music_max_seconds: document.getElementById('musicMaxSeconds').value,
    music_seed: document.getElementById('musicSeed').value,
    music_quality: document.getElementById('musicQuality').value,
    // Cover: a path here changes the job's shape. Sent even when empty so a
    // replay of a cover job and a replay of a prompt job differ in the field
    // rather than in which fields exist.
    // Only when Cover is the task. Switching back to Write closes the cover
    // section but leaves the path in its box — and a path in the form is a
    // cover, whatever the chips say (Codex review, 2026-09-20).
    music_source_audio: (((document.getElementById('musicTask') || {}).value || 'write') === 'cover')
      ? ((document.getElementById('musicSourceAudio') || {}).value || '') : '',
    music_cover_task: (document.getElementById('musicCoverTask') || {}).value || 'melody-full',
    music_title: (document.getElementById('musicTitle') || {}).value || '',
    music_cfg_scale: musicCfgValue(),
    music_precision: (document.getElementById('musicPrecision') || {}).value || '8bit',
    // Adapters, as `id:strength` pairs. Instrumental pauses them: its own AR
    // adapter is the recipe and a second AR delta would fight it. The picks
    // stay checked in the form so turning Instrumental off brings them back.
    music_loras: instrumental ? '' : musicLoraPicks(),
  });
}

// ---- Voice & style LoRA -----------------------------------------------------
// The picker is a directory listing, not a registry: whatever is in the LoRA
// folder (and in its user/ subfolder) is pickable. State lives in the DOM —
// the checkbox and the slider ARE the selection — so a re-render has to carry
// it across rather than reset it.
// AS JSON, `[{id, strength}]` (M6-05). The picks went out as `id:strength`
// joined with commas, and an id is a FILENAME: `Voice, warm.safetensors` was
// split into two ids nobody has, both were dropped, and the song came back
// without the voice the row showed ticked. The server's parser has always
// read this JSON form, and the variation route already sends it.
function musicLoraPicks() {
  const list = document.getElementById('musicLoraList');
  if (!list) return '';
  const picks = Array.from(list.querySelectorAll('.music-lora-item'))
    .filter(row => row.querySelector('input[type="checkbox"]').checked)
    .map(row => ({ id: row.dataset.id,
                   strength: Number(Number(row.querySelector('input[type="range"]').value).toFixed(2)) }));
  return picks.length ? JSON.stringify(picks) : '';
}

function musicLoraRender(music) {
  const list = document.getElementById('musicLoraList');
  if (!list) return;
  const adapters = ((music || {}).loras || {}).adapters || [];
  // Carry the current selection across a re-render (/status polls every few
  // seconds; a slider that snapped back every poll would be unusable).
  const kept = {};
  list.querySelectorAll('.music-lora-item').forEach(row => {
    kept[row.dataset.id] = {
      on: row.querySelector('input[type="checkbox"]').checked,
      strength: row.querySelector('input[type="range"]').value,
    };
  });
  const signature = adapters.map(a => a.id).join('|');
  if (list.dataset.signature === signature && Object.keys(kept).length) {
    musicLoraSummaryText();
    return;
  }
  list.dataset.signature = signature;
  if (!adapters.length) {
    list.innerHTML = `<div class="music-lora-empty">No adapters yet. ` +
      `<button type="button" class="ghost-btn" onclick="musicLoraInstall()">Download the LoRA pack</button></div>`;
    musicLoraSummaryText();
    return;
  }
  list.innerHTML = adapters.map(a => {
    const state = kept[a.id] || {};
    const strength = state.strength != null ? state.strength : '1.00';
    const branch = a.branch === 'nar' ? 'sound' : 'writing';
    return `<div class="music-lora-item${state.on ? ' active' : ''}" data-id="${charactersEscapeAttr(a.id)}">
      <label class="music-lora-head">
        <input type="checkbox" ${state.on && !a.problem ? 'checked' : ''} ${a.problem ? 'disabled' : ''} onchange="musicLoraToggled(this)">
        <span class="music-lora-name">${charactersEscapeHtml(a.name)}</span>
        <span class="music-lora-branch">${branch}</span>
      </label>
      <div class="range-strip">
        <input type="range" min="0" max="1.5" step="0.05" value="${charactersEscapeAttr(String(strength))}"
               oninput="musicLoraToggled(this)" title="Adapter strength">
        <span class="range-val">${Number(strength).toFixed(2)}</span>
      </div>
      ${a.note ? `<div class="hint">${charactersEscapeHtml(a.note)}</div>` : ''}
    </div>`;
  }).join('');
  musicLoraSummaryText();
}

function musicLoraToggled(el) {
  const row = el.closest('.music-lora-item');
  if (row) {
    const on = row.querySelector('input[type="checkbox"]').checked;
    row.classList.toggle('active', on);
    const value = Number(row.querySelector('input[type="range"]').value);
    row.querySelector('.range-val').textContent = value.toFixed(2);
  }
  musicFormChanged();
}

function musicLoraSummaryText() {
  const el = document.getElementById('musicLoraSummary');
  if (!el) return;
  if (document.getElementById('musicInstrumental').checked) {
    el.textContent = 'paused by Instrumental';
    return;
  }
  const picks = musicLoraPicks();
  if (!picks) { el.textContent = 'off'; return; }
  const names = Array.from(document.querySelectorAll('.music-lora-item.active .music-lora-name'))
    .map(n => n.textContent);
  el.textContent = `${names.join(' · ')} @ ${JSON.parse(picks).map(p => p.strength.toFixed(2)).join(' / ')}`;
}

// Instrumental with no adapter still works — it falls back to the section-tag
// prompt that shipped in v4.16 — so this offers the upgrade, never blocks.
// A BACKGROUND DOWNLOAD SAYS WHAT IT IS DOING (M6-09). The two install cards
// used to read only the 202 that started the fetch; the next /status tick
// repainted the Download offer over it, and a fetch that then failed (a
// dropped connection, a full disk) never said so. /status now carries each
// fetch's `{running, line, error}` and this is the one painter both read.
function musicFetchCard(fetch, what, retry) {
  const f = fetch || {};
  if (f.running) {
    return `<b>Downloading ${what}…</b> <span class="hint">${charactersEscapeHtml(f.line || 'starting')}</span>`;
  }
  if (f.error) {
    return `<b>The ${what} download failed:</b> ${charactersEscapeHtml(f.error)} ` +
      `<button type="button" class="ghost-btn" onclick="${retry}()">Try again</button>`;
  }
  return '';
}

function musicLoraInstallCard(music) {
  const box = document.getElementById('musicLoraInstall');
  if (!box) return;
  const loras = (music || {}).loras || {};
  const wanted = document.getElementById('musicInstrumental').checked;
  if (loras.instrumental_ready || !wanted) { box.hidden = true; box.innerHTML = ''; return; }
  const mb = Math.round((loras.bytes || 0) / 1e6);
  box.hidden = false;
  box.innerHTML = musicFetchCard(loras.fetch, 'music LoRAs', 'musicLoraInstall') ||
    `<b>Instrumental gets better with the music LoRA pack (~${mb} MB, once).</b> ` +
    `Without it the model is only asked in words for a track with no vocal line. ` +
    `<button type="button" class="ghost-btn" onclick="musicLoraInstall()">Download it</button>`;
}

async function musicLoraInstall() {
  const box = document.getElementById('musicLoraInstall');
  if (box) { box.hidden = false; box.innerHTML = 'Downloading the music LoRAs…'; }
  try {
    const r = await fetch('/music/lora/fetch', { method: 'POST' });
    const result = await r.json();
    if (!r.ok || result.error) throw new Error(result.error || 'Could not start the download');
    if (box) box.innerHTML = 'Downloading the music LoRAs… this runs in the background.';
  } catch (e) {
    if (box) box.innerHTML = `Could not start the download: ${e.message || e}`;
  }
}

// 1.0 is the engine's own default; sending nothing for it keeps the sidecar
// honest ("cfg_scale": null = the default was used, not "1.0 was chosen").
function musicCfgValue() {
  const el = document.getElementById('musicCfg');
  if (!el) return '';
  const v = Number(el.value);
  return Math.abs(v - 1) < 0.001 ? '' : String(v);
}

// The cover section says what it is doing without being opened, the way every
// other closed section on this page does.
function musicCoverSummary() {
  const src = (document.getElementById('musicSourceAudio') || {}).value || '';
  const el = document.getElementById('musicCoverSummary');
  if (!el) return;
  if (!src.trim()) { el.textContent = 'off'; return; }
  const name = src.trim().split('/').pop();
  const task = (document.getElementById('musicCoverTask') || {}).value || 'melody-full';
  const words = {'melody-full': 'melody + chords', 'melody-vocal': 'the sung line',
                 'full': 'everything'};
  el.textContent = `${name} · ${words[task] || task}`;
}

// The two transcription models are an opt-in download, so the section says so
// in place rather than letting a cover fail ten minutes in.
function musicCoverInstallCard(music) {
  const box = document.getElementById('musicCoverInstall');
  if (!box) return;
  const cover = (music || {}).cover || {};
  const src = (document.getElementById('musicSourceAudio') || {}).value || '';
  if (cover.ready || !src.trim()) { box.hidden = true; box.innerHTML = ''; return; }
  const gb = ((cover.bytes || 0) / 1e9).toFixed(1);
  box.hidden = false;
  box.innerHTML = musicFetchCard(cover.fetch, 'cover models', 'musicCoverInstall') ||
    `<b>Covering needs two more models (~${gb} GB).</b> They read the tune off ` +
    `your recording; writing a song from a prompt never uses them. ` +
    `<button type="button" class="ghost-btn" onclick="musicCoverInstall()">Download them</button>`;
}

async function musicCoverInstall() {
  const box = document.getElementById('musicCoverInstall');
  if (box) box.innerHTML = 'Downloading the cover models…';
  try {
    const r = await fetch('/music/cover/install', { method: 'POST' });
    const result = await r.json();
    if (!r.ok || result.error) throw new Error(result.error || 'Could not start the download');
    if (box) box.innerHTML = 'Downloading the cover models… this runs in the background.';
  } catch (e) {
    if (box) box.innerHTML = `Could not start the download: ${e.message || e}`;
  }
}
async function musicGenerate() {
  if (musicBusy) return;
  // Pressing Compose before the engine exists IS the request to install it.
  if (!window._ENGINE_PROBES.music.available) { openMusicInstallCard(); return; }
  // Simple mode does not render: it asks Gemma for the style and the words
  // and shows them in the Custom fields, because a song is minutes of this
  // Mac's GPU and nobody should spend that on a brief they have not read.
  if (typeof musicSimpleActive === 'function' && musicSimpleActive()) {
    if (typeof musicSimpleCompose === 'function') await musicSimpleCompose();
    return;
  }
  // Get the score is transcription only — a different route, no song.
  const task = (document.getElementById('musicTask') || {}).value || 'write';
  if (task === 'score') { if (typeof musicTranscribe === 'function') musicTranscribe(); return; }
  const fd = musicFormParams();
  const status = document.getElementById('musicStatus');
  if (task === 'cover' && !fd.get('music_source_audio').trim()) {
    const det = document.getElementById('musicCoverDetails');
    if (det) det.open = true;
    status.textContent = 'Cover needs a recording — pick or paste the one to cover.';
    return;
  }
  if (!fd.get('music_style').trim() && !fd.get('music_lyrics').trim() && fd.get('music_instrumental') !== 'on') {
    status.textContent = 'Give it something to work with — lyrics, a style description, or both.';
    return;
  }
  // A cover with no transcription models is a ten-minute job that cannot
  // succeed. Say it here, where the fix is one click away.
  if (fd.get('music_source_audio').trim() &&
      !(((window._ENGINE_PROBES.music || {}).cover || {}).ready)) {
    document.getElementById('musicCoverDetails').open = true;
    status.textContent = 'Covering needs the two transcription models first.';
    return;
  }
  musicBusy = true;
  musicFormChanged();
  status.textContent = 'Queueing…';
  try {
    // Takes: N songs from one form, each with its own seed. A fixed seed
    // applies to the first only — identical seeds would be identical songs.
    const takes = Math.max(1, Math.min(8, Number((document.getElementById('musicTakes') || {}).value) || 1));
    for (let i = 0; i < takes; i++) {
      if (i > 0) fd.set('music_seed', '-1');
      const r = await fetch('/queue/add', { method: 'POST', body: fd });
      const result = await r.json();
      if (!r.ok || result.error) throw new Error(result.error || 'Could not queue music');
    }
    status.textContent = takes > 1 ? `${takes} takes queued.` : 'Song queued.';
    setMainOutputsFilter('audio');
    await poll();
  } catch (e) {
    status.textContent = e.message || String(e);
  } finally {
    musicBusy = false;
    musicFormChanged();
  }
}
function useTrackInA2V(path) {
  const output = findOutputByPath(path);
  AUDIO_STUDIO.audioPath = path;
  AUDIO_STUDIO.audioName = path.split('/').pop();
  AUDIO_STUDIO.audioDuration = output ? output.clip_sec : null;
  audioModeSet('drive');
  workflowSwitch('audio');
  audioStudioRenderSlots();
  // The pane this lands on is Music video now, so the track has to land in
  // ITS song slot as well as in the One-shot form underneath — a button that
  // opens a screen and fills nothing on it reads as broken. Both, because the
  // user may well have wanted the one clip.
  if (typeof mvInit === 'function') mvInit();
  if (typeof mvPickFromLibrary === 'function') mvPickFromLibrary(path);
  phosToast('Song loaded — drop your pictures, then Plan the video.');
}
// One renderer, two places: the panel on top of Compose and the modal card
// (opened by Compose, the header engine picker and Settings → Models).
function musicInstallRender(el, where) {
  if (!el) return;
  const music = window._ENGINE_PROBES.music || {};
  const inst = musicInstall || {};
  const size = 'YuE2, ~11 GB';
  let html;
  if (!music.capable) {
    html = `<p class="mi-lead">YuE2 needs about ${escapeHtml(String(music.min_ram_gb || 24))} GB of unified memory, so it can't run on this Mac. The rest of Phosphene is unaffected.</p>`;
  } else if (inst.active) {
    const pct = inst.percent || 1;
    const step = `Step ${(inst.step_index || 0) + 1} of ${inst.steps || 6} · ${escapeHtml(inst.step_label || '')}`;
    const bytes = inst.bytes_total
      ? ` · ${(inst.bytes_done / 1e9).toFixed(1)} of ${(inst.bytes_total / 1e9).toFixed(1)} GB` : '';
    html = `<p class="mi-lead"><b>${inst.state === 'stopping' ? 'Stopping…' : 'Installing the music engine…'}</b> ${pct}%</p>
      <div class="progress-bar"><div class="fill" style="width:${pct}%"></div></div>
      <p class="mi-sub">${step}${bytes}</p>
      <p class="mi-line">${escapeHtml(inst.last_line || '')}</p>
      <div class="mi-actions"><button type="button" class="danger" onclick="musicInstallStop()" ${inst.state === 'stopping' ? 'disabled' : ''}>Stop</button>
      <span class="mi-note">You can keep using Phosphene. Compose unlocks by itself when it finishes.</span></div>`;
  } else if (music.available) {
    html = `<p class="mi-lead"><b>The music engine is installed.</b> Write lyrics and a style, then press Compose.</p>
      ${where === 'modal' ? '<div class="mi-actions"><button type="button" class="primary" onclick="closeMusicInstallCard();audioModeSet(\'compose\');workflowSwitch(\'audio\')">Start composing</button></div>' : ''}`;
  } else {
    const repair = !!music.repairable;
    const failed = inst.state === 'failed' && inst.error;
    const stopped = inst.state === 'stopped';
    const label = repair ? 'Repair music engine (weights kept)' : `Install music engine (${size})`;
    const lead = repair
      ? 'The YuE2 weights are on disk but the engine needs repair. This takes a couple of minutes and re-downloads nothing.'
      : 'Compose writes a whole song — vocals and arrangement — from your lyrics and a style. It needs the YuE2 music engine: a one-time ~11 GB download that resumes if interrupted.';
    html = `<p class="mi-lead">${lead}</p>
      ${failed ? `<p class="mi-error">${escapeHtml(inst.error)}</p>` : ''}
      ${stopped ? '<p class="mi-sub">Install stopped. Click again to resume where it left off.</p>' : ''}
      <div class="mi-actions"><button type="button" class="primary" onclick="musicInstallStart(${repair ? "'repair'" : ''})">${escapeHtml(failed || stopped ? (repair ? 'Try the repair again' : 'Resume install (YuE2, ~11 GB)') : label)}</button></div>
      <p class="mi-note">Needs ${escapeHtml(String(music.min_ram_gb || 24))} GB unified memory and 14 GB free disk.${distText(' Also available from the Phosphene sidebar in Pinokio.', '')}</p>`;
  }
  el.innerHTML = html;
}
async function musicInstallStart(kind) {
  const fd = new URLSearchParams({ kind: kind === 'repair' ? 'repair' : 'install' });
  try {
    const r = await fetch('/music/install', { method: 'POST', body: fd });
    const result = await r.json().catch(() => ({}));
    if (!r.ok && !result.install) throw new Error(result.error || 'Could not start the install');
    if (result.started || result.install) musicInstall = { ...musicInstall, active: true, state: 'running', percent: 1, step_index: 0 };
  } catch (e) {
    musicInstall = { state: 'failed', active: false, error: e.message || String(e) };
  }
  updateMusicAvailability();
  if (typeof poll === 'function') poll();
}
async function musicInstallStop() {
  try { await fetch('/music/install/stop', { method: 'POST' }); } catch (_) {}
  if (typeof poll === 'function') poll();
}
function openMusicInstallCard() {
  musicInstallRender(document.getElementById('musicInstallBody'), 'modal');
  document.getElementById('musicInstallModal').hidden = false;
}
function closeMusicInstallCard() {
  document.getElementById('musicInstallModal').hidden = true;
}

// VA-01: the Video mode bar's entry point into lip-sync. The chip lives in
// #modeGroup for discoverability (it was three levels deep: Audio tab →
// Music video → a folded "One shot (audio → video)" row sharing a name with
// the unrelated One Shot tab) but the form it opens is still the Audio tab's
// one-shot composer — that form has its own submit path
// (audioStudioGenerate → mode=a2v), so this jumps rather than switching
// #genForm's mode.
function openLipSyncEntry() {
  audioModeSet('drive');
  if (typeof workflowSwitch === 'function') workflowSwitch('audio');
  const det = document.getElementById('mvOneShotDetails');
  if (det) det.open = true;
  requestAnimationFrame(() => {
    if (det) det.scrollIntoView({ block: 'start', behavior: 'smooth' });
  });
}

// ---- 4.19: Lip-sync on Hailuo H3 -------------------------------------------
// The engine row appears once the installed H3 runner can hold a soundtrack.
// LTX stays the default; the choice is remembered per browser. On H3 the
// canvas comes from H3's own quality cells (Draft / Standard / High) instead
// of free Width × Height, and the LTX-only conditioning knob folds away.
const A2V_ENGINE_KEY = 'phos_a2v_engine';
const A2V_H3_QUALITY_KEY = 'phos_a2v_h3_quality';
globalThis.A2V_ENGINE = (() => {
  try { return localStorage.getItem(A2V_ENGINE_KEY) === 'h3' ? 'h3' : 'ltx'; } catch (e) { return 'ltx'; }
})();
globalThis.A2V_H3_QUALITY = (() => {
  try { return localStorage.getItem(A2V_H3_QUALITY_KEY) || 'high'; } catch (e) { return 'high'; }
})();

function a2vH3Offered() {
  return typeof h3CanServe === 'function' && h3CanServe('a2v');
}

function a2vEngineActive() {
  return (A2V_ENGINE === 'h3' && a2vH3Offered()) ? 'h3' : 'ltx';
}

function setA2vEngine(id) {
  A2V_ENGINE = (id === 'h3') ? 'h3' : 'ltx';
  try { localStorage.setItem(A2V_ENGINE_KEY, A2V_ENGINE); } catch (e) {}
  renderA2vEngineRow();
  if (typeof audioStudioDurationChanged === 'function') audioStudioDurationChanged();
}

function setA2vH3Quality(key) {
  A2V_H3_QUALITY = key;
  try { localStorage.setItem(A2V_H3_QUALITY_KEY, key); } catch (e) {}
  renderA2vEngineRow();
  if (typeof audioStudioDurationChanged === 'function') audioStudioDurationChanged();
}

// The H3 canvases a lip-sync can use: every offered quality (not the lab
// Preview canvas).
function _a2vH3Qualities() {
  const qs = ((window._ENGINE_PROBES || {}).h3 || {}).qualities || [];
  // Not Fast HD: its Face Fix redraws the mouth the soundtrack just drove.
  return qs.filter(q => q && q.offered !== false && q.key !== 'preview' && !q.fast_hd);
}

function renderA2vEngineRow() {
  const row = document.getElementById('a2vEngineRow');
  const det = document.getElementById('mvOneShotDetails');
  const offered = a2vH3Offered();
  if (row) row.hidden = !offered;
  const active = a2vEngineActive();
  if (det) det.dataset.a2vEngine = active;
  document.querySelectorAll('#a2vEngineGroup [data-a2v-engine]').forEach(b => {
    const on = b.dataset.a2vEngine === active;
    b.classList.toggle('active', on);
    b.setAttribute('aria-checked', on ? 'true' : 'false');
  });
  const qg = document.getElementById('a2vH3QualityGroup');
  if (qg && offered) {
    const qs = _a2vH3Qualities();
    if (qs.length && !qs.some(q => q.key === A2V_H3_QUALITY)) A2V_H3_QUALITY = qs[Math.min(2, qs.length - 1)].key;
    qg.innerHTML = qs.map(q => `<button type="button" class="pill-btn${q.key === A2V_H3_QUALITY ? ' active' : ''}"
        role="radio" aria-checked="${q.key === A2V_H3_QUALITY}" onclick="setA2vH3Quality('${escapeHtml(q.key)}')">
        ${escapeHtml(q.label)}<span class="sub">${escapeHtml(q.canvas || '')}</span></button>`).join('');
  }
}

// Minutes for an H3 lip-sync of `sec` seconds at the chosen canvas: the 5 s
// cell's own price (Fast when installed) times the windows the length needs.
function a2vH3EstimateMin(sec) {
  if (typeof h3CellFor !== 'function' || typeof h3CellEtaMin !== 'function') return null;
  const cell = h3CellFor(A2V_H3_QUALITY, '5s');
  if (!cell) return null;
  const per = h3CellEtaMin(cell);
  if (!(per > 0)) return null;
  const frames = Math.round(sec * 24);
  const windows = frames <= 124 ? 1 : 1 + Math.ceil((frames - 124) / 123);
  return per * windows;
}

function audioStudioInit() {
  renderA2vEngineRow();
  if (musicComposeActive()) setMainOutputsFilter('audio');
  // The Drive-video pane is the Music video pane now (music.js owns it; the
  // single-clip form this function wires is the "One shot" disclosure at the
  // bottom of it). `mvInit` is idempotent and re-reads the song library every
  // time, so it belongs BEFORE the wired guard — a song composed since the
  // last visit has to appear in the select.
  if (typeof mvInit === 'function') mvInit();
  // Re-read every visit, not once: the tier is known by the time the pane is
  // opened and the hint names a lane-specific number. A thumb the user has
  // already dragged is left exactly where they put it.
  const _acsEl = document.getElementById('audioConditioningScale');
  if (!_acsEl || _acsEl.dataset.auto) audioConditioningScaleReset();
  audioStudioRenderLoraNote();
  audioStudioRefreshSeparator();
  audioStudioApplyTierClamp();
  // VA-06: seed the price on every visit (not just after a slider drag) so
  // the estimate is on screen before Generate is ever pressed.
  if (typeof audioStudioDurationChanged === 'function') audioStudioDurationChanged();
  if (AUDIO_STUDIO.wired) return;
  AUDIO_STUDIO.wired = true;
  const audioSlot = document.getElementById('audioStudioAudioSlot');
  const audioInput = document.getElementById('audioStudioAudioInput');

  // Audio drop-zone wiring — click to pick, drag-and-drop, paste.
  // (Image drop-zone wiring lives in pickerWire('a2v_image'), called
  // once at module boot from PICKERS.forEach(pickerWire).)
  if (audioSlot && audioInput) {
    audioSlot.addEventListener('click', () => audioInput.click());
    audioSlot.addEventListener('dragover', (e) => {
      e.preventDefault();
      audioSlot.classList.add('drop-active');
    });
    audioSlot.addEventListener('dragleave', () => audioSlot.classList.remove('drop-active'));
    audioSlot.addEventListener('drop', (e) => {
      e.preventDefault();
      audioSlot.classList.remove('drop-active');
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0]) {
        audioStudioUploadAudio(e.dataTransfer.files[0]);
      }
    });
    audioInput.addEventListener('change', () => {
      if (audioInput.files && audioInput.files[0]) {
        audioStudioUploadAudio(audioInput.files[0]);
      }
    });
  }
  audioStudioRenderSlots();
}

// VA-04: LoRAs the Video tab's picker has active ride into every a2v render
// (audioStudioGenerate forwards _activeLoras below) with nothing on screen
// to say so. A face LoRA left over from an earlier character render quietly
// changes identity on a lip-sync shot with no way to see, remove, or
// deliberately reproduce it. This is the visibility line — the picker
// itself stays on the Video tab; "change" jumps there and opens it.
function audioStudioRenderLoraNote() {
  const el = document.getElementById('audioStudioLorasNote');
  if (!el) return;
  const active = (typeof _activeLoras !== 'undefined' && Array.isArray(_activeLoras)) ? _activeLoras : [];
  if (!active.length) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }
  const names = active.map(l => escapeHtml(l.name || String(l.path || '').split('/').pop())).join(', ');
  el.style.display = '';
  el.innerHTML = (active.length === 1 ? '1 LoRA' : active.length + ' LoRAs')
    + ' from the Video tab will be applied here too: <b>' + names + '</b> — '
    + '<a href="#" onclick="event.preventDefault();workflowSwitch(\'manual\');'
    + 'var d=document.getElementById(\'lorasDetails\');if(d)d.open=true;'
    + 'd&&d.scrollIntoView({block:\'start\'});">change</a>'
    + '<br>' + a2vLoraStageLine(window.PHOSPHENE_CAP_TIER === 'q4');
}
// Keep the face, free the mouth (lip-sync quality, 4.17.x): a face LoRA at
// full strength mutes the singer, so on the Q8 lane Lip-sync leaves the LoRA
// out while the performance is shaped and uses it for the face detail. The Q4
// lane has no such seam — say that plainly instead.
function a2vLoraStageLine(isQ4) {
  return isQ4
    ? 'On this Mac a LoRA shapes the whole lip-sync render, which can make the mouth move less \u2014 lower its strength if the mouth barely moves.'
    : 'Lip-sync uses LoRAs for the face detail only, so they keep the likeness without stiffening the mouth.';
}

// VOCAL SEPARATION — "Listen to the voice only" needs it, and until 4.17 a
// missing separator quietly rendered the FULL MIX (one line in the job log).
// Install and Update put it in place now; this line says so when it is not,
// and offers the same install from right here. PURE text builder so it is
// tested by executing it.
const A2V_SEPARATOR = { ready: null, timer: null };
function audioStudioSeparatorNote(s, wantsStem) {
  if (!s) return '';
  const inst = s.install || {};
  if (inst.active) {
    const last = (inst.log || []).slice(-1)[0] || 'starting';
    return '<b>Installing vocal separation\u2026</b> <span class="hint">' + escapeHtml(last) + '</span>';
  }
  // 4.17.3 review: the package can install while the 80 MB voice model fails
  // to download (a network drop) - status then says ready:true, weights:false,
  // install failed. The ready branch below used to swallow that failure and
  // its Try again; say it, since the first render would stop to fetch it.
  if (s.ready && s.weights === false && inst.state === 'failed' && wantsStem) {
    return '<b>The voice model did not download:</b> ' + escapeHtml(inst.error || 'unknown error')
      + ' <button type="button" class="ghost-btn" onclick="audioStudioInstallSeparator()">Try again</button>'
      + ' <span class="hint">or leave it: the first voice-only render downloads it (about 80 MB).</span>';
  }
  if (s.ready) {
    return inst.state === 'done' ? '<b>Vocal separation is installed.</b> The model will listen to the voice only.' : '';
  }
  if (!wantsStem) return '';
  if (inst.state === 'failed') {
    return '<b>Vocal separation did not install:</b> ' + escapeHtml(inst.error || 'unknown error')
      + ' <button type="button" class="ghost-btn" onclick="audioStudioInstallSeparator()">Try again</button>'
      + ' <span class="hint">or untick \u201cListen to the voice only\u201d to use the full mix.</span>';
  }
  return '<b>Vocal separation is not installed on this Mac yet</b>, so \u201cListen to the voice only\u201d cannot run. '
    + '<button type="button" class="ghost-btn" onclick="audioStudioInstallSeparator()">Install it (about 90 MB, once)</button>'
    + ' <span class="hint">or untick the box to render against the full mix.</span>';
}
function audioStudioRenderSeparator(s) {
  if (s) A2V_SEPARATOR.last = s;
  s = A2V_SEPARATOR.last;
  const el = document.getElementById('audioStudioStemStatus');
  if (!el) return;
  const box = document.getElementById('audioStudioStemAuto');
  const html = audioStudioSeparatorNote(s, !!(box && box.checked));
  el.innerHTML = html;
  el.style.display = html ? '' : 'none';
}
async function audioStudioRefreshSeparator() {
  try {
    const r = await fetch('/a2v/separator');
    const s = await r.json();
    A2V_SEPARATOR.ready = !!s.ready;
    audioStudioRenderSeparator(s);
    clearTimeout(A2V_SEPARATOR.timer);
    if (s.install && s.install.active) {
      A2V_SEPARATOR.timer = setTimeout(audioStudioRefreshSeparator, 1500);
    }
  } catch (e) { /* the form still works; the server refuses with the same words */ }
}
async function audioStudioInstallSeparator() {
  try {
    const r = await fetch('/a2v/separator/install', { method: 'POST', body: new URLSearchParams({}) });
    const res = await r.json().catch(() => ({}));
    if (!r.ok && !res.install) throw new Error(res.error || ('HTTP ' + r.status));
  } catch (e) {
    if (typeof phosToast === 'function') phosToast('Could not start the install: ' + (e.message || e), { kind: 'danger' });
  }
  audioStudioRefreshSeparator();
}
// The words the server answers a refused a2v submit with, or the raw text.
function audioStudioSubmitError(status, txt) {
  try {
    const j = JSON.parse(txt);
    if (j && j.error) return j.error;
  } catch (e) { /* not JSON */ }
  return 'HTTP ' + status + ' ' + txt;
}

async function audioStudioUploadAudio(file) {
  const status = document.getElementById('audioStudioStatus');
  if (status) status.textContent = 'Uploading audio…';
  try {
    const fd = new FormData();
    // /upload accepts both `image` and `audio` field names.
    fd.append('audio', file);
    const r = await fetch('/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const data = await r.json();
    if (data.error) throw new Error(data.error);
    AUDIO_STUDIO.audioPath = data.path || null;
    AUDIO_STUDIO.audioName = file.name;
    AUDIO_STUDIO.audioDuration = (data.duration_sec != null) ? Number(data.duration_sec) : null;
    audioStudioRenderSlots();
    audioStudioDurationFromFile();
    if (status) status.textContent = '';
  } catch (e) {
    if (status) status.textContent = 'Audio upload failed: ' + (e.message || 'unknown');
  }
}

// Default the window to the file, not a fixed 7 s (VA-03): a fresh audio
// file should default Duration to the whole clip (capped at the slider's own
// 30 s ceiling) so the common case — one short line — needs no manual
// trimming to avoid rendering silence. Shared by an upload and by a file sent
// here from the gallery (audioStudioUseAudio).
function audioStudioDurationFromFile() {
  if (AUDIO_STUDIO.audioDuration != null) {
    const slider = document.getElementById('audioStudioDuration');
    if (slider) {
      // floor(len + 0.25): a 12.9 s file defaults to 13 s (0.1 s of tail,
      // under the silent-tail threshold below), a 12.6 s file to 12 s —
      // never a whole extra second of silence (4.17.0 render check).
      const target = Math.max(1, Math.min(30, Math.floor(AUDIO_STUDIO.audioDuration + 0.25)));
      slider.value = String(target);
    }
  }
  if (typeof audioStudioDurationChanged === 'function') audioStudioDurationChanged();
}

// 4.18.0 — "Use this audio for… → Lip-sync" from the gallery: the file lands
// in the Lip-sync audio slot exactly as if it had been dropped there (same
// slot, same Duration default), and the Lip-sync form opens. A song or a
// voice line already in Outputs never has to be found in Finder again.
// The length comes from the output's sidecar when it has one, else from the
// browser reading the file's own header.
function audioStudioUseAudio(path, durationSec, url) {
  if (!path) return;
  AUDIO_STUDIO.audioPath = path;
  AUDIO_STUDIO.audioName = String(path).split('/').pop();
  const d = Number(durationSec);
  AUDIO_STUDIO.audioDuration = (durationSec != null && isFinite(d) && d > 0) ? d : null;
  openLipSyncEntry();
  audioStudioRenderSlots();
  audioStudioDurationFromFile();
  if (AUDIO_STUDIO.audioDuration == null && url) {
    const probe = new Audio();
    probe.preload = 'metadata';
    probe.addEventListener('loadedmetadata', () => {
      if (AUDIO_STUDIO.audioPath !== path || !isFinite(probe.duration)) return;
      AUDIO_STUDIO.audioDuration = probe.duration;
      audioStudioRenderSlots();
      audioStudioDurationFromFile();
    }, { once: true });
    probe.src = url;
  }
}

// audioStudioUploadImage / audioStudioClearImage were retired
// 2026-05-20 when the image slot was migrated to the shared .picker
// component (key='a2v_image'). pickerUploadFile + pickerSetImage now
// handle upload and clear, and the hidden input #a2v_image carries the
// path that audioStudioGenerate() reads.

// Open-mouth anchor check (build item 3 of the "Keyframe + lip-sync"
// section, report_video-advanced.txt). The 2026-09-22 anchor lesson: a
// strength-1.0 frame-0 anchor cut from an open-mouth still freezes the
// performance open-mouthed for the whole clip — "mouth open, doesn't move,
// doesn't sing" was the owner's exact report. Called from queue.js's
// pickerSetImage() whenever the a2v_image picker gets a new (or cleared)
// path; a crop preview belongs on the SAME picker tile (VA-02, the
// size-faces package) — this only owns the openness verdict line under it.
function audioStudioMouthCheck(path) {
  const warn = document.getElementById('audioStudioMouthWarn');
  if (!warn) return;
  if (!path) { warn.style.display = 'none'; warn.innerHTML = ''; return; }
  warn.style.display = '';
  warn.style.color = '';
  warn.textContent = 'Checking the mouth in this picture…';
  fetch('/a2v/mouth_check', { method: 'POST', body: new URLSearchParams({ image: path }) })
    .then(r => r.json())
    .then(res => {
      // A picker change while the request was in flight — don't overwrite
      // a newer check's result with a stale one.
      const current = (document.getElementById('a2v_image') || {}).value;
      if (current !== path) return;
      if (!res.ok || !res.measured) { warn.style.display = 'none'; warn.innerHTML = ''; return; }
      if (res.open) {
        warn.style.color = 'var(--warn,#c98a2b)';
        warn.innerHTML = 'This picture’s mouth looks open — lip-sync tends to '
          + 'freeze on an open-mouth start frame. A closed-mouth, face-forward '
          + 'picture works better.';
      } else {
        warn.style.display = 'none';
        warn.innerHTML = '';
      }
    })
    .catch(() => { warn.style.display = 'none'; warn.innerHTML = ''; });
}

function audioStudioClearAudio() {
  AUDIO_STUDIO.audioPath = null;
  AUDIO_STUDIO.audioName = null;
  AUDIO_STUDIO.audioDuration = null;
  audioStudioRenderSlots();
}

function audioStudioRenderSlots() {
  // Only the audio slot is rendered here. The image slot is now a
  // shared .picker (key='a2v_image') wired by pickerWire — it manages
  // its own preview, clear button, and recent-uploads strip.
  const audioSlot = document.getElementById('audioStudioAudioSlot');
  if (!audioSlot) return;
  if (AUDIO_STUDIO.audioPath) {
    const dur = (AUDIO_STUDIO.audioDuration != null)
      ? ' · ' + AUDIO_STUDIO.audioDuration.toFixed(1) + ' s'
      : '';
    // escapeHtml — user-supplied filename can contain &, <, >, ", '
    // that would otherwise break out of innerHTML into attribute/tag
    // context. Same defense the rest of the panel uses for any user
    // string that ends up in innerHTML.
    const safeName = escapeHtml(AUDIO_STUDIO.audioName || 'audio');
    audioSlot.innerHTML =
      '<span class="ref-tag">Audio</span>' +
      '<div style="padding:18px 12px;text-align:center;font-size:12px;color:var(--text);overflow:hidden;text-overflow:ellipsis;">' +
      '<svg class="ph" aria-hidden="true" style="width:18px;height:18px;vertical-align:-3px;margin-right:6px;"><use href="#ph-music-notes"/></svg>' +
      safeName + dur +
      '<div class="hint" style="margin-top:8px;">' +
        '<a href="#" onclick="event.stopPropagation();audioStudioClearAudio();return false;">Remove</a>' +
      '</div>' +
      '</div>';
  } else {
    audioSlot.innerHTML =
      '<span class="ref-tag">Audio</span>' +
      '<div class="hint" style="padding:24px 12px;text-align:center;color:var(--muted);font-size:12px;">' +
      'Drop a WAV / MP3 / M4A / FLAC here, or click to pick a file.' +
      '</div>';
  }
}

// Duration slider readout + length warning.
//
// The frame arithmetic here is the same one the submit path uses (24 fps,
// rounded up to the 8k+1 grid), so the number shown is the number the model
// is actually asked for — not a rounded-down approximation of it.
//
// This warns rather than blocks on purpose, and as of 3.8.3 it warns on the
// right quantity.
//
// It used to fire on FRAME COUNT alone, past ~257. @blackest then answered the
// two questions that were open in #46, and both answers moved the target:
//
//   1. The AUDIO survives. "I am certain it continues right to the end of the
//      20 seconds" — so this is the picture running out of coherence, not the
//      joint sequence failing.
//   2. It is not a frame cap. Same 20 s request, same machine (M2 Max 96 GB):
//        640x480   481 frames   fine, all 20 s
//        1024x576  481 asked    picture dies at frame ~454
//        1280x720  481 asked    white out by 481
//      "I think its not so much a frame cap but a limit on how many frames at
//      x size can be generated."
//
// So the budget is frames x AREA, and the old warning was simply wrong in the
// case that matters most: it shouted at a 640x480 20 s render that works.
//
// AND THEN THE REPORTER'S OWN FOUR DATAPOINTS REFUTED THAT. No frames x area
// constant can separate them: 832x480 x 721 = 287.9 Mpx holds together, while
// 1024x576 x 481 = 283.7 Mpx falls apart — a SMALLER product failing. A budget
// that calls the working one worse than the failing one is not a budget, it is
// a coin toss with a number on it.
//
// They separate cleanly on PER-FRAME AREA. Clean: 0.307 and 0.399 Mpx/frame,
// at 721 and 481 frames. Failing: 0.590 and 0.922 Mpx/frame, both giving out
// around frame 450. So the lever is the CANVAS, and length is nearly free
// below the knee — which is the opposite of what the old warning told people,
// and it told them so at exactly the 640x480 20 s render that works.
//
// The knee sits at ~0.45 Mpx a frame, between the highest clean reading and
// the lowest failing one. Below it, nothing is warned about within the
// slider's range; above it, the warning names the canvas as the lever and
// ~450 frames as where the picture gives out. Still field reports from other
// people's machines, not a limit measured here, and the copy still says so.
const A2V_AREA_KNEE = 0.45e6;      // pixels per frame
const A2V_KNEE_FRAMES = 450;       // where the reports say the picture goes
function _a2vFramesForSeconds(sec) {
  const target = Math.max(1, Math.round(sec * 24));
  return ((target - 1 + 7) >> 3 << 3) + 1;   // round up to 8k+1
}
// VA-06: A2V showed no time estimate before a 10-25 minute render — the
// #audioStudioEstimate span existed in the markup and nothing wrote to it.
// Priced server-side (GET /a2v/estimate -> ltx_a2v_estimate_minutes), from
// the lane that actually runs (Q8 two-stage vs Q4 distilled) and the real
// clamped canvas, the same way the Video tab and One Shot already price a
// render before Generate. Debounced + sequence-guarded (osEstimate's
// pattern in oneshot.js) since duration is a drag slider that fires on
// every tick.
let _a2vEstimateSeq = 0;
let _a2vEstimateTimer = null;
function a2vUpdateEstimate(frames, w, h) {
  const el = document.getElementById('audioStudioEstimate');
  if (!el) return;
  el.textContent = 'pricing…';
  clearTimeout(_a2vEstimateTimer);
  const seq = ++_a2vEstimateSeq;
  _a2vEstimateTimer = setTimeout(async () => {
    try {
      const r = await fetch(`/a2v/estimate?frames=${frames}&width=${w}&height=${h}`);
      const d = await r.json();
      if (seq !== _a2vEstimateSeq) return;
      if (!d.ok) { el.textContent = ''; return; }
      const pack = d.pack === 'q8' ? 'Q8' : 'Q4';
      el.textContent = `${d.width}×${d.height} · ${pack} · about ${String(d.eta).replace(/^~\s*/, '')} on this Mac`;
    } catch (e) {
      if (seq === _a2vEstimateSeq) el.textContent = '';
    }
  }, 150);
}
// VA-35: on Compact tier (t2v_max_dim, e.g. 768) the A2V form used to show
// and price the size the user typed (e.g. 1024×576) while the server
// silently clamped to the tier cap at render time — the canvas warning
// below was then computed on the wrong number too. Runs once per Audio-tab
// visit: sets the Width/Height inputs' max attribute and pulls down any
// value that already exceeds it (same proportional-fit shape as the
// server's ltx_fit_canvas), so what's on screen is what will render.
function audioStudioApplyTierClamp() {
  const cap = ((BOOT || {}).tier || {}).t2v_max_dim || 0;
  if (!cap) return;
  const wEl = document.getElementById('audioStudioWidth');
  const hEl = document.getElementById('audioStudioHeight');
  if (!wEl || !hEl) return;
  wEl.max = String(cap);
  hEl.max = String(cap);
  const w = parseInt(wEl.value || '1024', 10);
  const h = parseInt(hEl.value || '576', 10);
  if (Math.max(w, h) > cap) {
    const scale = cap / Math.max(w, h);
    wEl.value = String(Math.max(32, Math.round((w * scale) / 32) * 32));
    hEl.value = String(Math.max(32, Math.round((h * scale) / 32) * 32));
    if (typeof phosToast === 'function') {
      phosToast(`This Mac renders Audio → Video up to ${cap} px on the longer side — size adjusted to fit.`,
                { kind: 'info' });
    }
  }
}

function audioStudioDurationChanged(val) {
  const slider = document.getElementById('audioStudioDuration');
  // Called with no argument from the Width/Height inputs, so the canvas and
  // the length always warn against each other rather than in isolation.
  const sec = parseInt((val !== undefined && val !== null ? val
                        : (slider ? slider.value : '7')) || '7', 10);
  const out = document.getElementById('audioStudioDurationVal');
  if (out) out.textContent = sec + ' s';
  const frames = _a2vFramesForSeconds(sec);
  const w = parseInt((document.getElementById('audioStudioWidth') || {}).value || '1024', 10);
  const h = parseInt((document.getElementById('audioStudioHeight') || {}).value || '576', 10);
  if (typeof a2vEngineActive === 'function' && a2vEngineActive() === 'h3') {
    const el = document.getElementById('audioStudioEstimate');
    const m = a2vH3EstimateMin(sec);
    const cell = (typeof h3CellFor === 'function') ? h3CellFor(A2V_H3_QUALITY, '5s') : null;
    if (el) el.textContent = cell
      ? `Hailuo H3 · ${cell.width}×${cell.height}` + (m ? ` · about ${typeof h3FmtEtaMin === 'function' ? h3FmtEtaMin(m) : Math.round(m) + ' min'} on this Mac` : '')
      : 'Hailuo H3';
  } else if (typeof a2vUpdateEstimate === 'function') a2vUpdateEstimate(frames, w, h);
  const warn = document.getElementById('audioStudioDurationWarn');
  if (!warn) return;
  const area = (w > 0 && h > 0) ? w * h : 1024 * 576;
  // THE WINDOW VS THE FILE (VA-03). Duration and Audio start used to be
  // free-floating numbers with no idea how long the uploaded file actually
  // is — a 4 s vocal silently rendered 7 s, 43% of it silence, which is
  // exactly where a lip-sync mouth freezes. Now that /upload returns
  // duration_sec, warn (start+duration past the end) or refuse outright
  // (start already past the end — nothing would play at all).
  const startEl = document.getElementById('audioStudioStart');
  const startSec = Math.max(0, parseFloat((startEl && startEl.value) || '0') || 0);
  if (AUDIO_STUDIO.audioDuration != null) {
    const fileLen = AUDIO_STUDIO.audioDuration;
    if (startSec >= fileLen) {
      warn.style.display = '';
      warn.innerHTML = '<b>Start at</b> (' + startSec.toFixed(1) + ' s) is past '
        + 'the end of this file (' + fileLen.toFixed(1) + ' s long) — this would '
        + 'render a fully silent clip. Pick a start under ' + fileLen.toFixed(1) + ' s.';
      return;
    }
  }
  // A MEMORY LIMIT COMES FIRST. On a Compact-tier Mac the panel refuses an
  // a2v render past BOOT.a2v_max_frames (see a2v_max_frames() server-side):
  // a 30 s clip on an 8 GB Mac died in the GPU watchdog instead. Say so here,
  // before Generate, with the same number the worker refuses on.
  // 4.17.0 render check: this used to sit AFTER the silent-tail note, so a
  // 12.9 s song defaulted to 13 s on a 32 GB Mac showed "the last 0.1 s
  // renders against silence" and never the cap or its Split link — the one
  // action that renders the song on that Mac. A refusal outranks a nuance.
  const a2vCap = (BOOT && BOOT.a2v_max_frames) || 0;
  if (a2vCap && frames > a2vCap) {
    const capSec = Math.round((a2vCap - 1) / 24);
    warn.style.display = '';
    // VA-30: was a manual-chore instruction only ("set Start at to 0, N,
    // 2N... and join them in the Editor"). "Split into clips" queues the
    // whole song as a sequence, each part after the first anchored on the
    // previous part's own most-closed-mouth frame (the "Continue the song"
    // primitive, build item 2) — no jump cuts at the joins, no hand math.
    warn.innerHTML = 'This Mac renders Audio → Video up to <b>' + capSec
      + ' s</b> per clip — longer ones outgrow its memory and are refused. '
      + '<a href="#" onclick="event.preventDefault();audioStudioSplitIntoClips(' + capSec + ');">'
      + 'Split into ' + capSec + ' s clips</a> — queues the whole song as a '
      + 'sequence, each part starting where the last one’s mouth closed, '
      + 'no jump cuts at the joins. One at a time; this Mac’s own a2v speed '
      + 'applies per part, so a long song is a long wait, not a fast one.';
    return;
  }
  if (AUDIO_STUDIO.audioDuration != null) {
    const fileLen = AUDIO_STUDIO.audioDuration;
    // Under a quarter second of tail is the rounding of a whole-second
    // slider, not a clip that "renders against silence".
    if (startSec + sec > fileLen + 0.25) {
      const silentTail = (startSec + sec - fileLen).toFixed(1);
      warn.style.display = '';
      warn.innerHTML = 'The last <b>' + silentTail + ' s</b> of this clip render '
        + 'against silence (the file ends at ' + fileLen.toFixed(1) + ' s) — the '
        + 'mouth will stop there. Shorten Duration or move Start at earlier.';
      return;
    }
  }
  // THE CANVAS IS THE LEVER, NOT THE LENGTH. Below the knee the reports run
  // clean to 721 frames, so there is nothing to say; above it they give out
  // around frame 450 whatever the length asked for.
  if (area > A2V_AREA_KNEE && frames > A2V_KNEE_FRAMES) {
    const kneeSec = Math.max(1, Math.floor((A2V_KNEE_FRAMES - 1) / 24));
    warn.style.display = '';
    warn.innerHTML = '<b>' + w + '\u00d7' + h + '</b> is past the canvas where '
      + 'long Audio \u2192 Video renders have been reported holding together. '
      + 'What gives out is the picture, not the sound: the audio plays to the '
      + 'end while anatomy drifts and the frame washes out, and the reports put '
      + 'that around <b>frame ' + A2V_KNEE_FRAMES + '</b> (~' + kneeSec + ' s) '
      + 'whatever length was asked for. It is the <b>canvas</b>, not the length: '
      + '832\u00d7480 has been reported clean at 721 frames, while 1024\u00d7576 '
      + 'died around frame 454 on the same machine '
      + '(<a href="https://github.com/mrbizarro/Phosphene/issues/46" target="_blank" rel="noopener">#46</a> '
      + '\u2014 field reports, not a limit measured here). '
      + 'Drop to about 832\u00d7480 or smaller and the length comes back. Longer still renders.';
  } else {
    warn.style.display = 'none';
    warn.textContent = '';
  }
}

// VA-05 (client half): "this freezes the mouth — Fix" for the lip-sync
// prompt box. Debounced (400ms) so it doesn't fire a request per keystroke.
// The server-side pass (storyboard.a2v_prompt in run_job_inner) is the real
// safety net and runs unconditionally on every a2v render; this is purely a
// before-you-render heads-up.
let _a2vPromptCheckTimer = null;
function audioStudioPromptCheck() {
  clearTimeout(_a2vPromptCheckTimer);
  _a2vPromptCheckTimer = setTimeout(_audioStudioPromptCheckNow, 400);
}
async function _audioStudioPromptCheckNow() {
  const ta = document.getElementById('audioStudioPrompt');
  const warn = document.getElementById('audioStudioPromptWarn');
  if (!ta || !warn) return;
  const text = ta.value;
  if (!text.trim()) { warn.style.display = 'none'; warn.innerHTML = ''; return; }
  try {
    const r = await fetch('/a2v/prompt_check', { method: 'POST', body: new URLSearchParams({ prompt: text }) });
    const res = await r.json();
    if (!res.ok || !res.problems || !res.problems.length) {
      warn.style.display = 'none';
      warn.innerHTML = '';
      return;
    }
    const phrases = res.problems.map(p => '"' + escapeHtml(p) + '"').join(', ');
    warn.style.display = '';
    warn.innerHTML = 'This freezes the mouth: ' + phrases
      + ' — audio-driven clips read stillness language as "nothing moves, '
      + 'including the lips." <a href="#" onclick="event.preventDefault();audioStudioPromptApplyFix(' + JSON.stringify(res.cleaned) + ');">Fix</a>';
  } catch (e) { /* best-effort — the server-side pass is the real gate */ }
}
function audioStudioPromptApplyFix(cleaned) {
  const ta = document.getElementById('audioStudioPrompt');
  if (!ta) return;
  ta.value = cleaned;
  const warn = document.getElementById('audioStudioPromptWarn');
  if (warn) { warn.style.display = 'none'; warn.innerHTML = ''; }
}

async function audioStudioEnhancePrompt() {
  const ta = document.getElementById('audioStudioPrompt');
  const original = ta.value.trim();
  if (!original) { phosToast('Type a prompt before enhancing it.', { kind: 'warning' }); return; }
  const btn = document.getElementById('audioStudioEnhanceBtn');
  const originalLabel = btn.innerHTML;
  btn.disabled = true;
  // VC-23: same plain-English swap as queue.js's enhancePrompt().
  btn.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:6px;vertical-align:-2px"><use href="#ph-sparkle-fill"/></svg>Loading the prompt helper\u2026 (~15s)';
  try {
    // VA-24: was mode 't2v' — that Gemma system prompt asks for "describe
    // the scene AND the sound", which competes with the actual conditioning
    // audio, and readily writes stillness phrasing ("a static shot") that
    // freezes the mouth. mode 'a2v' (routes_queue.py) borrows the i2v system
    // prompt instead and runs the result through the same stillness-cleanup
    // + word-cap every other a2v prompt goes through.
    const fd = new URLSearchParams({ prompt: original, mode: 'a2v' });
    // 4.18.0: Lip-sync renders with the Video tab's LoRAs (audioStudioGenerate
    // forwards them), so Enhance describes the same ones and keeps their
    // trigger words — it used to send neither, and a face LoRA's trigger
    // could be rewritten away.
    const loraPaths = (typeof enhanceLoraPaths === 'function') ? enhanceLoraPaths() : [];
    if (loraPaths.length) {
      fd.set('loras', JSON.stringify(loraPaths));
      const triggers = [];
      for (const l of (globalThis._activeLoras || [])) {
        if (!loraPaths.includes(l.path)) continue;
        for (const t of (l.trigger_words || [])) { const v = String(t || '').trim(); if (v) triggers.push(v); }
      }
      if (triggers.length) fd.set('preserve_tokens', JSON.stringify(triggers));
    }
    const r = await fetch('/prompt/enhance', { method: 'POST', body: fd });
    const res = await r.json();
    if ((typeof packNeededToast === 'function' && packNeededToast(res))) return;   // 4.20: Gemma 3 still downloading / not yet fetched
    if (res.error) { phosToast('Enhance failed: ' + res.error, { kind: 'danger', duration: 8000 }); return; }
    // No native confirm (it blocked the page and could not be undone): apply
    // the result, say what the LoRAs changed, and offer Undo in the toast.
    // Codex 4.18.0: Enhance takes seconds and the box stays editable — if the
    // prompt changed meanwhile, the new words are the person's and win; the
    // result is offered instead of written over them. Undo, likewise, only
    // puts the old prompt back while the box still holds the enhanced one.
    const note = (typeof enhanceLoraNote === 'function') ? enhanceLoraNote(res) : '';
    const setPrompt = (v) => { ta.value = v; ta.dispatchEvent(new Event('input', { bubbles: true })); };
    const action = (el, label, fn) => {
      if (!el) return;
      const a = document.createElement('a');
      a.href = '#'; a.className = 'phos-toast-action'; a.textContent = label;
      a.onclick = (ev) => { ev.preventDefault(); el.remove(); fn(); };
      el.appendChild(a);
    };
    if (ta.value.trim() !== original) {
      const el = phosToast('Your prompt changed while Enhance was working, so it was kept.'
        + (note ? ' ' + note : ''), { kind: 'warning', duration: 12000 });
      action(el, 'Use the enhanced one', () => setPrompt(res.enhanced));
      return;
    }
    setPrompt(res.enhanced);
    const el = phosToast('Prompt enhanced.' + (note ? ' ' + note : ''), { kind: 'success', duration: 9000 });
    action(el, 'Undo', () => {
      if (ta.value === res.enhanced) setPrompt(original);
      else phosToast('The prompt was edited since — Undo left it as it is.', { kind: 'warning' });
    });
  } catch (e) { phosToast('Enhance request failed: ' + (e.message || e), { kind: 'danger', duration: 8000 }); }
  finally { btn.disabled = false; btn.innerHTML = originalLabel; }
}

// --- Audio conditioning strength: AUTO until the user says otherwise -------
// The Q8 two-stage lane and the Q4 distilled lane read this number with
// OPPOSITE meanings at 1.0 — on Q8 it lands on the guider's modality_scale,
// whose term is (value - 1) * …, so 1.0 switches audio guidance off, while on
// Q4 it multiplies the audio tokens and 1.0 is the identity. The panel picks
// the lane at queue time (and can fall back from Q8 to Q4 when the Q8 files
// are missing), so the browser must not guess: while the control is on Auto
// the field is simply not sent and the panel fills in the lane's own default.
function a2vLaneAudioScale() {
  return (window.PHOSPHENE_CAP_TIER === 'q4') ? 1.0 : 3.0;
}

function audioConditioningScaleChanged(el) {
  if (!el) return;
  el.dataset.auto = '';
  const out = document.getElementById('audioConditioningScaleVal');
  if (out) out.textContent = el.value;
}

function audioConditioningScaleReset() {
  const el = document.getElementById('audioConditioningScale');
  if (!el) return;
  el.dataset.auto = '1';
  const isQ4 = window.PHOSPHENE_CAP_TIER === 'q4';
  // VA-19: on Q8, 1.0 is exactly "audio guidance off" and below 1.0 inverts
  // the term — a user dragging the thumb "down a bit" from Auto's 3.0 could
  // land in that dead zone with no warning. Q8's floor moves up to 1.5 so
  // the slider itself cannot reach the off point; Q4 has no such trap.
  el.min = isQ4 ? '0.5' : '1.5';
  el.value = String(a2vLaneAudioScale());
  const out = document.getElementById('audioConditioningScaleVal');
  if (out) out.textContent = 'Auto';
  const hint = document.getElementById('audioConditioningScaleHint');
  if (hint) {
    hint.textContent = 'How hard the mouth follows the audio. Auto is best '
      + 'for most clips — it picks the right value for this Mac\'s render '
      + 'lane (' + (isQ4 ? 'Q4' : 'Q8') + '). Drag right for tighter sync, '
      + 'left for more visual freedom.';
  }
}

// LOAD PARAMS ON AN AUDIO → VIDEO CLIP (LTX-04). The generic loader has no
// a2v branch: it fell through to Text mode, copied the track into the Manual
// form's #audio (which a2v never reads) and left the Audio tab untouched, so
// Generate queued a different kind of video with no audio conditioning. This
// puts the request back where it came from — the Audio tab's one-shot form,
// read by audioStudioGenerate(): the track, the reference image, the canvas,
// the length, the seed, the offset into the song and the conditioning
// strength, with Auto kept as Auto (a blank in the sidecar) rather than
// frozen at whichever lane default the clip happened to render on.
function a2vLoadParams(p) {
  p = p || {};
  const audio = String(p.audio || '');
  AUDIO_STUDIO.audioPath = audio || null;
  AUDIO_STUDIO.audioName = audio ? audio.split('/').pop() : null;
  AUDIO_STUDIO.audioDuration = null;
  audioModeSet('drive');
  if (typeof workflowSwitch === 'function') workflowSwitch('audio');
  const det = document.getElementById('mvOneShotDetails');
  if (det) {
    det.open = true;
    // VA-10: opening the details left the restored form below the fold —
    // the data landed correctly, the user just never scrolled down far
    // enough to see it. Scroll it into view and give it the same brief
    // pulse the Params flash uses elsewhere (.flash / phos-tab-flash).
    // setTimeout(…, 0), not requestAnimationFrame: the JS test harness
    // (scripts/extract_panel_js.py) runs this in plain node, which has no
    // rAF. Either defers past the details' own reflow from `open = true`
    // just above, which is all this needs.
    setTimeout(() => {
      try { det.scrollIntoView({ block: 'start', behavior: 'smooth' }); } catch (e) { try { det.scrollIntoView(); } catch (e2) {} }
      det.classList.add('flash');
      setTimeout(() => det.classList.remove('flash'), 1200);
    }, 0);
  }
  audioStudioRenderSlots();
  const setVal = (id, v) => {
    const el = document.getElementById(id);
    if (el && v !== undefined && v !== null && v !== '') el.value = String(v);
  };
  setVal('audioStudioPrompt', p.prompt);
  setVal('audioStudioWidth', p.width);
  setVal('audioStudioHeight', p.height);
  const seed = (p.seed_used != null && p.seed_used !== '') ? p.seed_used : p.seed;
  setVal('audioStudioSeed', seed);
  const startEl = document.getElementById('audioStudioStart');
  if (startEl) startEl.value = String(Math.max(0, parseFloat(p.audio_start_time) || 0));
  const frames = parseInt(p.frames, 10);
  if (Number.isFinite(frames) && frames > 1) {
    const slider = document.getElementById('audioStudioDuration');
    const sec = Math.max(1, Math.round((frames - 1) / 24));
    if (slider) slider.value = String(sec);
    audioStudioDurationChanged(String(sec));
  }
  if (typeof pickerSetImage === 'function') {
    pickerSetImage('a2v_image', p.image ? String(p.image) : '', { snapAspect: false });
    // The lip-sync picture's saved crop framing (VA-02), not the last one's.
    if (typeof _setCropFocus === 'function' && typeof _cropFocusFromParams === 'function') {
      _setCropFocus('a2v_image', _cropFocusFromParams(p));
    }
  }
  const acs = p.audio_conditioning_scale;
  const acsEl = document.getElementById('audioConditioningScale');
  // A NUMBER 1 is the pre-4.15.2 default, not a choice: the old make_job
  // stored a float 1.0 on every a2v job, and on Q8 1.0 switches audio
  // guidance off. The server reads that float as Auto (a2v_requested_scale);
  // restoring it here as an explicit "1" would undo that. Today's sidecars
  // carry strings ("" = Auto, "2.5" = dragged), which are kept as they are.
  const legacyDefault = (typeof acs === 'number' && acs === 1);
  if (acsEl && !legacyDefault && acs !== undefined && acs !== null && String(acs).trim() !== ''
      && Number.isFinite(parseFloat(acs))) {
    acsEl.value = String(parseFloat(acs));
    audioConditioningScaleChanged(acsEl);
  } else {
    audioConditioningScaleReset();
  }
  // LIPSYNC-10: "Listen to the voice only" as the clip was made — Generate
  // and Continue the song submit this checkbox. Same accepted values as the
  // server's a2v_conditioning_audio(); an explicit vocal stem (a music-video
  // shot) also means the model listened to the voice alone. A sidecar
  // without the field predates the control and rendered the full mix.
  const stemEl = document.getElementById('audioStudioStemAuto');
  if (stemEl) {
    const autoOn = ['1', 'true', 'on', 'yes'].includes(String(p.audio_stem_auto ?? '').trim().toLowerCase());
    stemEl.checked = autoOn || !!String(p.audio_stem || '').trim();
  }
  // 4.19: an H3 lip-sync reopens on H3, at its canvas and its own length
  // (24 fps frames, not LTX's 8k+1 grid).
  if (String(p.engine || '') === 'h3') {
    if (p.h3_quality) {
      A2V_H3_QUALITY = String(p.h3_quality);
      try { localStorage.setItem(A2V_H3_QUALITY_KEY, A2V_H3_QUALITY); } catch (e) {}
    }
    if (typeof setA2vEngine === 'function') setA2vEngine('h3');
    const f24 = parseInt(p.h3_a2v_frames || p.frames, 10);
    const slider = document.getElementById('audioStudioDuration');
    if (Number.isFinite(f24) && f24 > 0 && slider) {
      slider.value = String(Math.max(1, Math.round(f24 / 24)));
      audioStudioDurationChanged(slider.value);
    }
  } else if (p.mode === 'a2v' && typeof setA2vEngine === 'function') {
    setA2vEngine('ltx');
  }
  audioStudioRenderLoraNote();
}

// VA-07: Draft · Final. Draft renders the first ~3s of the CHOSEN window at
// full settings — same seed, same image, same audio start, same prompt —
// so "does this picture and prompt move the mouth?" costs about 3 min
// instead of the full 10-25. Final is the same recipe at the full duration
// the Duration slider still shows (Draft does not touch it). The pairing
// is seed-locked: Draft resolves a random seed (-1) to a concrete number
// and writes it back into the Seed field, so Final — pressed after —
// reproduces the same take, just longer, rather than drawing a new one.
// VA-30: "Split into N clips" — queues the WHOLE song as a sequence of a2v
// jobs on a Compact-tier Mac (a2v_max_frames cap), one at a time, each part
// after the first anchored on the previous part's own most-closed-mouth
// frame (the "Continue the song" primitive above — /a2v/continue_song) so
// the joins don't show a mid-syllable jump. This is a real client-side
// orchestration loop, not a server job: it takes as long as N a2v renders
// take (each 10-25 min on this Mac's own a2v speed), and the tab has to
// stay open for it. NEEDS RENDER CHECK (see ledger_lipsync.txt): the
// single-clip a2v path and /a2v/continue_song are both already exercised
// elsewhere; the multi-part CHAIN — does part 2's anchor really read back
// cleanly after part 1 is on disk, N times in a row — has not been proven
// against a real render.
let _a2vSplitCancel = false;
// LIPSYNC-7: one split chain at a time. AUDIO_STUDIO.busy only covers a single
// submit; a second Split (or a double-activated link) used to start a second
// chain that queued duplicate parts and reset the first one's cancel flag.
let _a2vSplitRunning = false;
function audioStudioSplitCancel() { _a2vSplitCancel = true; }
async function _waitForJob(jobId, onProgress) {
  const started = Date.now();
  const timeoutMs = 40 * 60 * 1000;   // generous over the measured 10-25 min a2v ceiling
  while (Date.now() - started < timeoutMs) {
    if (_a2vSplitCancel) return { status: 'cancelled' };
    try {
      const r = await fetch('/status');
      const s = await r.json();
      const all = [].concat(s.current ? [s.current] : [], s.queue || [], s.history || []);
      const job = all.find(j => j && j.id === jobId);
      if (job) {
        if (job.status === 'done') return { status: 'done', output_path: job.output_path };
        if (job.status === 'failed' || job.status === 'error') return { status: job.status, error: job.error };
        if (job.status === 'stopped' || job.status === 'cancelled') return { status: job.status };
        if (onProgress) onProgress(job.status === 'running' ? 'rendering…' : 'queued…');
      }
    } catch (e) { /* transient network hiccup — keep polling */ }
    await new Promise(res => setTimeout(res, 4000));
  }
  return null;   // timed out
}
async function audioStudioSplitIntoClips(capSec) {
  if (_a2vSplitRunning) {
    if (typeof phosToast === 'function') {
      phosToast('A split is already running — Stop it first, or wait for it to finish.', { kind: 'warning' });
    }
    return;
  }
  if (AUDIO_STUDIO.busy) { alert('Wait for the current render to finish first.'); return; }
  const totalDur = AUDIO_STUDIO.audioDuration;
  if (!totalDur) {
    alert('This file’s length is not known yet — wait for the upload to finish, or re-drop it.');
    return;
  }
  const startEl = document.getElementById('audioStudioStart');
  const fromSec = Math.max(0, parseFloat((startEl && startEl.value) || '0') || 0);
  const remaining = totalDur - fromSec;
  if (remaining <= 0) { alert('Start at is already past the end of this file.'); return; }
  const n = Math.max(1, Math.ceil(remaining / capSec));
  if (!confirm('Queue ' + n + ' clips of up to ' + capSec + 's each, one at a time — '
    + 'this Mac’s own a2v speed applies per part, so this can take a while. Continue?')) return;
  // Taken BEFORE the first await and released in finally — a second call
  // returns above without touching this chain's cancel flag.
  _a2vSplitRunning = true;
  _a2vSplitCancel = false;
  try {
    await _a2vSplitChain(capSec, totalDur, fromSec, n);
  } finally {
    _a2vSplitRunning = false;
  }
}
async function _a2vSplitChain(capSec, totalDur, fromSec, n) {
  const status = document.getElementById('audioStudioStatus');
  const w = Math.max(32, Math.round(parseInt(document.getElementById('audioStudioWidth').value || '1024', 10) / 32) * 32);
  const h = Math.max(32, Math.round(parseInt(document.getElementById('audioStudioHeight').value || '576', 10) / 32) * 32);
  const prompt = (document.getElementById('audioStudioPrompt').value || '').trim() || 'A performer sings to camera.';
  const a2vImageEl = document.getElementById('a2v_image');
  let imagePath = (a2vImageEl && a2vImageEl.value) || '';
  // LIPSYNC-6: the same request settings Generate sends. The picked
  // picture's crop position applies to the FIRST part only — later parts
  // anchor on a frame of the previous clip, already at the output size.
  const focusEl = document.getElementById('a2v_image_crop_focus');
  let cropFocus = imagePath ? ((focusEl && focusEl.value) || '0.5') : '';
  const lorasJson = (typeof _activeLoras !== 'undefined' && Array.isArray(_activeLoras) && _activeLoras.length)
    ? JSON.stringify(_activeLoras.map(l => ({ path: l.path, strength: l.strength })))
    : '';
  const acsEl = document.getElementById('audioConditioningScale');
  const audioConditioningScale = (acsEl && !acsEl.dataset.auto) ? parseFloat(acsEl.value) : null;
  const stemAutoEl = document.getElementById('audioStudioStemAuto');
  const stemAuto = !!(stemAutoEl && stemAutoEl.checked);
  // LIPSYNC-4: each closed-mouth anchor sits a little before its part's end,
  // so the parts overlap and the song needs MORE parts than the first
  // estimate. Loop until the rendered audio reaches the end of the file; `n`
  // is re-estimated as it goes. A non-advancing anchor falls back to the
  // plain cut, and a hard ceiling stops any runaway.
  const EPS = 0.05;
  const maxParts = 2 * n + 2;
  let nextStart = fromSec;
  let done = 0;
  while (nextStart < totalDur - EPS) {
    const i = done;
    n = Math.max(n, i + Math.ceil((totalDur - nextStart - EPS) / capSec));
    if (_a2vSplitCancel) { if (status) status.textContent = 'Split stopped after part ' + i + ' of ' + n + '.'; return; }
    if (i >= maxParts) break;
    const partDur = Math.min(capSec, totalDur - nextStart);
    const frames = _a2vFramesForSeconds(partDur);
    if (status) status.textContent = 'Queueing part ' + (i + 1) + ' of ' + n + ' ('
      + nextStart.toFixed(1) + 's–' + (nextStart + partDur).toFixed(1) + 's)…';
    const fd = new URLSearchParams();
    fd.set('mode', 'a2v');
    fd.set('prompt', prompt);
    fd.set('audio', AUDIO_STUDIO.audioPath);
    if (imagePath) {
      fd.set('image', imagePath);
      if (cropFocus) fd.set('image_crop_focus', cropFocus);
    }
    fd.set('width', String(w));
    fd.set('height', String(h));
    fd.set('frames', String(frames));
    fd.set('seed', '-1');
    if (audioConditioningScale !== null && !Number.isNaN(audioConditioningScale)) {
      fd.set('audio_conditioning_scale', String(audioConditioningScale));
    }
    fd.set('audio_start_time', String(nextStart));
    if (stemAuto) fd.set('audio_stem_auto', 'on');
    fd.set('quality', 'high');
    fd.set('accel', 'off');
    fd.set('enhance', 'off');
    if (lorasJson) fd.set('loras', lorasJson);
    fd.set('preset_label', 'Split ' + (i + 1) + '/' + n);
    let jobId;
    try {
      const r = await fetch('/queue/add', { method: 'POST', body: fd });
      const res = await r.json().catch(() => ({}));
      if (!r.ok || res.error) throw new Error(res.error || ('HTTP ' + r.status));
      jobId = res.id;
    } catch (e) {
      const msg = 'Part ' + (i + 1) + ' failed to queue: ' + (e.message || e)
        + ' — ' + i + ' clip(s) already rendered are safe in Outputs.';
      if (status) status.textContent = msg;
      if (typeof phosToast === 'function') phosToast(msg, { kind: 'danger', duration: 8000 });
      return;
    }
    const result = await _waitForJob(jobId, (msg) => {
      if (status) status.textContent = 'Part ' + (i + 1) + ' of ' + n + ': ' + msg;
    });
    if (!result || result.status !== 'done' || !result.output_path) {
      const msg = 'Part ' + (i + 1) + ' did not finish (' + ((result && result.error) || result?.status || 'timed out')
        + ') — ' + i + ' clip(s) already rendered are safe in Outputs.';
      if (status) status.textContent = msg;
      if (typeof phosToast === 'function') phosToast(msg, { kind: 'danger', duration: 8000 });
      return;
    }
    done = i + 1;
    const partEnd = nextStart + partDur;
    if (partEnd < totalDur - EPS) {
      // Anchor the NEXT part on THIS part's own closed-mouth frame — the
      // same primitive "Continue the song" uses — instead of a naive fixed
      // cut, so the joins don't show a mid-syllable jump. Falls back to the
      // naive cut (still correct, just un-anchored) if the probe fails or
      // the anchor would not move the song forward.
      let anchored = false;
      try {
        const cr = await fetch('/a2v/continue_song', { method: 'POST', body: new URLSearchParams({ clip: result.output_path }) });
        const cres = await cr.json();
        const at = Number(cres && cres.audio_start_time);
        if (cres && cres.ok && cres.anchor_image && Number.isFinite(at)
            && at > nextStart + EPS && at <= partEnd + EPS) {
          imagePath = cres.anchor_image;
          cropFocus = '';
          nextStart = at;
          anchored = true;
        }
      } catch (e) { /* fall through to the plain cut */ }
      if (!anchored) nextStart = partEnd;
    } else {
      nextStart = partEnd;
    }
    if (typeof poll === 'function') poll();
  }
  if (nextStart < totalDur - EPS) {
    const msg = 'Split stopped after ' + done + ' clips at ' + nextStart.toFixed(1)
      + 's — the rest of the song was not queued. The clips made are safe in Outputs.';
    if (status) status.textContent = msg;
    if (typeof phosToast === 'function') phosToast(msg, { kind: 'danger', duration: 8000 });
    return;
  }
  if (status) status.textContent = 'Split into ' + done + ' clips done — join them in the Editor.';
  if (typeof phosToast === 'function') {
    phosToast('Split into ' + done + ' clips finished — join them in the Editor.', { kind: 'success' });
  }
}

async function audioStudioGenerate(opts) {
  opts = opts || {};
  const draft = !!opts.draft;
  if (AUDIO_STUDIO.busy) return;
  const status = document.getElementById('audioStudioStatus');
  const btn = document.getElementById(draft ? 'audioStudioDraftBtn' : 'audioStudioGenBtn');
  const prompt = (document.getElementById('audioStudioPrompt').value || '').trim();
  if (!AUDIO_STUDIO.audioPath) {
    if (status) status.textContent = 'Audio file is required.';
    return;
  }
  if (!prompt) {
    if (status) status.textContent = 'Prompt is required.';
    return;
  }
  // Width / height from free-form inputs; snap to 32 px multiple
  // (LTX latent grid requirement).
  const w = Math.max(32, Math.round(parseInt(document.getElementById('audioStudioWidth').value || '1024', 10) / 32) * 32);
  const h = Math.max(32, Math.round(parseInt(document.getElementById('audioStudioHeight').value || '576', 10) / 32) * 32);
  // Duration from slider → frames at 8k+1 cadence the model expects. Draft
  // clamps the SUBMITTED frame count only — the slider itself is left
  // alone, so it still shows the length Final will use.
  const dur = parseInt(document.getElementById('audioStudioDuration').value || '7', 10);
  const frames = _a2vFramesForSeconds(draft ? Math.min(3, dur) : dur);
  const seedEl = document.getElementById('audioStudioSeed');
  let seed = parseInt((seedEl && seedEl.value) || '-1', 10);
  if (draft && (!Number.isFinite(seed) || seed < 0)) {
    // Resolve -1 to a concrete seed NOW, client-side, and write it back —
    // Final needs to see the same number Draft is about to render with,
    // and the server would otherwise draw its own random seed per job.
    seed = Math.floor(Math.random() * 2147483647);
    if (seedEl) seedEl.value = String(seed);
  }
  // Auto = do not send the field at all; see audioConditioningScaleChanged.
  const acsEl = document.getElementById('audioConditioningScale');
  const audioConditioningScale = (acsEl && !acsEl.dataset.auto)
    ? parseFloat(acsEl.value || String(a2vLaneAudioScale()))
    : null;
  // Offset into the source file. Clamped at 0 here as well as server-side —
  // a negative start would be silently swallowed by load_audio.
  const audioStartEl = document.getElementById('audioStudioStart');
  const audioStart = Math.max(0, parseFloat((audioStartEl && audioStartEl.value) || '0') || 0);
  // VA-03: a start past the end of the known file renders pure silence —
  // refuse it the same way the missing-audio / missing-prompt checks do,
  // rather than spend 10-25 minutes finding out.
  if (AUDIO_STUDIO.audioDuration != null && audioStart >= AUDIO_STUDIO.audioDuration) {
    if (status) status.textContent = 'Start at (' + audioStart.toFixed(1)
      + ' s) is past the end of this file (' + AUDIO_STUDIO.audioDuration.toFixed(1) + ' s).';
    return;
  }

  // "Listen to the voice only" on a Mac without the separator. 4.17.4: while
  // an install runs - or when the panel can start one - the job queues and
  // waits for it (the server decides and answers 400 when it cannot). Only a
  // FAILED install stops the submit here, with its Try again just above.
  const _stemBox = document.getElementById('audioStudioStemAuto');
  const _sepWaits = !!(_stemBox && _stemBox.checked && A2V_SEPARATOR.ready === false);
  const _sepInst = (_sepWaits && ((A2V_SEPARATOR.last || {}).install)) || {};
  if (_sepWaits && !_sepInst.active && _sepInst.state === 'failed') {
    audioStudioRenderSeparator();
    if (status) status.textContent = 'Vocal separation did not install - press Try again just above, or untick \u201cListen to the voice only\u201d.';
    return;
  }

  AUDIO_STUDIO.busy = true;
  if (btn) btn.disabled = true;
  if (status) status.textContent = 'Queueing…';
  try {
    // Image path lives in the shared .picker hidden input (#a2v_image),
    // not AUDIO_STUDIO.imagePath anymore. Empty string when the user
    // didn't pick one — pure A2V flow.
    const a2vImageEl = document.getElementById('a2v_image');
    const a2vImagePath = (a2vImageEl && a2vImageEl.value) || '';
    const fd = new URLSearchParams();
    fd.set('mode', 'a2v');
    fd.set('prompt', prompt);
    fd.set('audio', AUDIO_STUDIO.audioPath);
    const _onH3 = typeof a2vEngineActive === 'function' && a2vEngineActive() === 'h3';
    if (_onH3) {
      // Hailuo H3 lip-sync: the canvas is the H3 cell's; the length is the
      // seconds asked for (24 fps, H3's own clock — not LTX's 8k+1 grid).
      fd.set('engine', 'h3');
      fd.set('h3_quality', A2V_H3_QUALITY);
      fd.set('h3_length', '5s');
    }
    if (a2vImagePath) {
      fd.set('image', a2vImagePath);
      // VA-02: the crop-preview overlay's drag position for this picker.
      const focusEl = document.getElementById('a2v_image_crop_focus');
      fd.set('image_crop_focus', (focusEl && focusEl.value) || '0.5');
    }
    fd.set('width', String(w));
    fd.set('height', String(h));
    fd.set('frames', String(_onH3 ? Math.max(24, Math.round((draft ? Math.min(3, dur) : dur) * 24)) : frames));
    fd.set('seed', String(seed));
    if (!_onH3 && audioConditioningScale !== null && !Number.isNaN(audioConditioningScale)) {
      fd.set('audio_conditioning_scale', String(audioConditioningScale));
    }
    fd.set('audio_start_time', String(audioStart));
    // VA-21: vocal-stem seam, exposed on the manual form for the first time.
    const stemAutoEl = document.getElementById('audioStudioStemAuto');
    if (stemAutoEl && stemAutoEl.checked) fd.set('audio_stem_auto', 'on');
    fd.set('quality', 'high');  // A2V is always pipeline-class (Q8 dev or Q4 distilled)
    // No accel, no enhance — A2V uses A2VidPipelineTwoStage's own walks.
    fd.set('accel', 'off');
    fd.set('enhance', 'off');
    // Forward picked LoRAs if any are active in the unified picker.
    if (typeof _activeLoras !== 'undefined' && Array.isArray(_activeLoras) && _activeLoras.length) {
      const slim = _activeLoras.map(l => ({ path: l.path, strength: l.strength }));
      fd.set('loras', JSON.stringify(slim));
    }
    fd.set('preset_label', (draft ? 'Draft' : (prompt.slice(0, 40) || 'Lip-sync')));
    const r = await fetch('/queue/add', { method: 'POST', body: fd });
    if (!r.ok) {
      const txt = await r.text();
      if (r.status === 400 && txt.indexOf('vocal_separator_missing') >= 0) {
        A2V_SEPARATOR.ready = false;
        audioStudioRefreshSeparator();
      }
      throw new Error(audioStudioSubmitError(r.status, txt));
    }
    if (status) {
      status.textContent = (draft
        ? 'Draft queued (~3s, same seed) — check the mouth, then Generate the full clip.'
        : 'Submitted. Watch Now / Recent.')
        + (_sepWaits ? ' It starts when vocal separation finishes installing.' : '');
    }
    if (_sepWaits) audioStudioRefreshSeparator();
    if (typeof phosToast === 'function') {
      phosToast(draft
        ? 'Draft queued · ~3s at this seed — watch Now, then Generate for the full clip'
        : 'Queued Audio → Video clip · watch Now', { kind: 'success' });
    }
    const nowTab = document.querySelector('.tabs button[data-tab="now"]');
    if (nowTab) {
      nowTab.classList.add('flash');
      setTimeout(() => nowTab.classList.remove('flash'), 1200);
    }
  } catch (e) {
    if (status) status.textContent = 'Submit failed: ' + (e.message || 'unknown');
    if (typeof phosToast === 'function') {
      phosToast('A2V submit failed: ' + (e.message || 'unknown'),
                { kind: 'danger', duration: 5000 });
    }
  } finally {
    AUDIO_STUDIO.busy = false;
    if (btn) btn.disabled = false;
  }
}

// ====== Train Character pane ================================================
// In-progress dataset state. The job_id is minted by the server on the FIRST
// upload of a session and echoed back on every subsequent /train/upload so
// the whole batch lands in one folder under state/train_character/<id>/.
globalThis.TRAIN = {
  jobId: null,
  // Mirror of the server-side image list. Each: {filename, path, src}.
  images: [],
  // Mirrors the server's TRAIN_DEFAULT_PRESET. Character defaults to HIGH:
  // it is the only recipe ever graded on a face, and for two months the panel
  // pre-selected + badged Quick instead, which is how a first-time user spent
  // his GPU hours on a 1.98e-04 adapter (#62). Style defaults to Quick — a
  // look is what rank 8/16 is genuinely good at.
  defaultPresets: BOOT.train_default_preset || { character: 'high', style: 'quick' },
  preset: (BOOT.train_default_preset && BOOT.train_default_preset.character) || 'high',
  // True once the user has clicked a preset pill themselves. Until then the
  // train-type toggle is free to snap the preset to that type's default; once
  // it is true, the panel never overrides a deliberate choice.
  presetTouched: false,
  // Train type — 'character' (face + optional voice) or 'style' (no voice,
  // aesthetic LoRA). Drives the preset table lookup + UI visibility (Voice
  // card hides for style; guidance + labels swap). Mirrors the server-side
  // TRAIN_TYPES tuple in mlx_ltx_panel.py.
  trainType: 'character',
  // Local mirror of the server preset table. BOOT carries the authoritative,
  // hardware-adjusted version so 48 GB Macs see the compact training profile
  // instead of the old 64 GB-class defaults. Static fallbacks below are only
  // for damaged/old boot payloads.
  //
  // Schema change 2026-05-19: presets describe EPOCHS, not steps. Actual
  // step count is computed from `epochs × image_count` at the consumer
  // (trainComputeSteps below) so the same preset auto-scales with
  // dataset size. Advanced trainSteps override still wins.
  presets: BOOT.train_presets || {
    quick:  { epochs:  30, rank: 8,  resolution: 512, seconds_per_step: 1.5, ram_peak_gb: 12,
              label: 'Quick',  subtitle: '~30 epochs · rank 8 · 512px', max_steps: 3000 },
    medium: { epochs:  60, rank: 16, resolution: 576, seconds_per_step: 2.2, ram_peak_gb: 18,
              label: 'Medium', subtitle: '~60 epochs · rank 16 · 576px', max_steps: 5000 },
    high:   { epochs: 100, rank: 32, resolution: 512, seconds_per_step: 2.0, ram_peak_gb: 28,
              label: 'High',   subtitle: '~100 epochs · rank 32 · 512px (v2 recipe)', max_steps: 7000 },
  },
  // Mirror of the server-side TRAIN_STYLE_PRESETS. Style table differs from
  // character: quick uses rank 16 (not rank 8), and "high" adds epochs not
  // rank (rank 32 is the validated capacity ceiling for our LTX-2.3 stack).
  stylePresets: BOOT.train_style_presets || {
    quick:  { epochs:  30, rank: 16, resolution: 512, seconds_per_step: 1.5, ram_peak_gb: 12,
              label: 'Quick',  subtitle: '~30 epochs · rank 16 · 512px', max_steps: 3000 },
    medium: { epochs:  60, rank: 32, resolution: 512, seconds_per_step: 2.0, ram_peak_gb: 18,
              label: 'Medium', subtitle: '~60 epochs · rank 32 · 512px', max_steps: 5000 },
    high:   { epochs: 100, rank: 32, resolution: 512, seconds_per_step: 2.0, ram_peak_gb: 28,
              label: 'High',   subtitle: '~100 epochs · rank 32 · 512px', max_steps: 7000 },
  },
  trainProfile: BOOT.train_profile || {},
  // Voice (optional) state. `voiceFile` is the server-saved record once
  // an upload completes; `voiceEnabled` mirrors the toggle.
  //
  // Default voiceEnabled = TRUE so the checkbox reads "audio LoRA: on"
  // even before the user uploads a clip. The checkbox is still HTML-
  // disabled until upload, so the user can't actually submit
  // train_audio=true without a voice file — trainStart() gates on
  // `voiceEnabled && voiceFile` (line ~19334), so this default is a
  // visual signal ("we'll train audio if you give us a clip") rather
  // than a behavioral trap. After upload the disabled flag flips off
  // and the user can uncheck if they want face-only.
  voiceFile: null,           // {filename, path, size, audioUrl}
  voiceEnabled: true,         // default ON visually; behaviorally gated on voiceFile
  voicePreset: 'standard',    // 'smoke' | 'standard' | 'long'
  voicePresets: {
    smoke:    { steps: 100, label: 'Smoke',    sub: '~7 min wall' },
    standard: { steps: 250, label: 'Standard', sub: '~17 min wall' },
    long:     { steps: 500, label: 'Long',     sub: '~33 min wall' },
  },
  // ~30 s/step on M4 Max. Mirrors TRAIN_AUDIO_SECONDS_PER_STEP py-side;
  // these labels are guidance not contract.
  voiceSecondsPerStep: 30,
  initialised: false,
};

// Helper — returns the right preset table for the current train type.
// Centralizes the lookup so trainUpdateEstimate / trainUpdateButtonState
// don't each have to know about both tables.
function trainActivePresets() {
  return TRAIN.trainType === 'style' ? TRAIN.stylePresets : TRAIN.presets;
}

// The preset this panel recommends for the active train type. Served by the
// backend (TRAIN_DEFAULT_PRESET) so the badge, the pre-selection and make_job's
// fallback are one decision in one place.
function trainRecommendedPreset() {
  const map = TRAIN.defaultPresets || {};
  const key = map[TRAIN.trainType];
  const table = trainActivePresets();
  if (key && table && table[key]) return key;
  return (table && table.high) ? 'high' : 'quick';
}

function trainActivePreset() {
  const table = trainActivePresets();
  return table[TRAIN.preset] || table[trainRecommendedPreset()] || {};
}

function trainUpdatePresetButtons() {
  const table = trainActivePresets();
  const recommended = trainRecommendedPreset();
  document.querySelectorAll('#trainPresetGroup .pill-btn').forEach((b) => {
    const key = b.dataset.trainPreset;
    b.classList.toggle('active', key === TRAIN.preset);
    // The badge follows the recommendation instead of being nailed to one
    // pill in the HTML. A badge on a preset that cannot do the job the tab
    // is for is the interface lying, and that is the whole of #62.
    const badge = b.querySelector('[data-rec-slot]');
    if (badge) badge.hidden = (key !== recommended);
  });
  const subIds = {
    quick: 'trainPresetQuickSub',
    medium: 'trainPresetMediumSub',
    high: 'trainPresetHighSub',
  };
  Object.keys(subIds).forEach((key) => {
    const el = document.getElementById(subIds[key]);
    const p = table[key];
    if (el && p && p.subtitle) el.textContent = p.subtitle;
  });
  trainUpdatePresetNote();
}

// The sentence under the pills. Says what the number means, and on a sub-64 GB
// Mac says the thing no pill NAME can say: "High" there is rank 8 / 500 steps /
// 448px on two projections, so the graded rank-32 recipe is unreachable from
// this menu and "just use High" is advice this machine cannot honour (#62).
function trainUpdatePresetNote() {
  const el = document.getElementById('trainPresetNote');
  if (!el) return;
  const profile = TRAIN.trainProfile || {};
  const compact = !!profile.compact;
  if (TRAIN.trainType === 'style') {
    el.textContent = compact
      ? 'This Mac trains on the compact profile (rank 4–8, 384–448px, capped steps), so a style adapter here will be a light touch. No style preset has been graded.'
      : 'A style adapter teaches a look, not a person, so Quick is a fair place to start; Medium and High buy capacity for a more complex look. None of the style presets has been graded.';
    return;
  }
  if (compact) {
    const ram = profile.ram_gb ? `${profile.ram_gb} GB` : 'under 64 GB';
    el.innerHTML =
      `<strong>This Mac has ${escapeHtml(String(ram))}, so training runs the compact profile — and "High" here is not the graded recipe.</strong> ` +
      'It trains rank 8 / 500 steps / 448px on two of the four attention projections. The recipe that has carried a face is rank 32 / ~100 epochs / 512px on all four, ' +
      'and no preset on this machine can reach it. Expect a weak adapter and treat the result as a look rather than an identity — that is the hardware, not your photos.';
    return;
  }
  // SYS-15: this used to lead with raw delta_rms numbers (lab jargon a
  // creator can't act on) while they were still picking a preset. Outcome
  // language up front; the measurement stays available for anyone who
  // wants it (or is filing a bug report) behind a details disclosure,
  // same pattern as the Now-card's own "Details for troubleshooting".
  el.innerHTML =
    '<strong>High is the only recipe ever graded on a face.</strong> ' +
    'Pick Quick for a fast look or a style, not for a person — it has not ' +
    'held an identity in any measured run. ' +
    '<details class="h3-diag"><summary>Measured numbers</summary>' +
    '<p>Measured with <code>lora_compat.py</code>: rank-32 (High) adapters ' +
    'that carry an identity sit at 5.4e-04 to 1.6e-03 delta_rms. Quick\'s ' +
    'rank 8 has measured 1.54e-04 and 1.98e-04 on real datasets — at or ' +
    'under the 2.0e-04 floor no working adapter has been below.</p></details>';
}

function trainDisableSelectAbove(selectId, maxValue) {
  const sel = document.getElementById(selectId);
  const max = Number(maxValue || 0);
  if (!sel || !max) return;
  Array.from(sel.options).forEach((opt) => {
    if (!opt.value) {
      opt.disabled = false;
      return;
    }
    const n = Number(opt.value);
    opt.disabled = Number.isFinite(n) && n > max;
  });
  const selected = Number(sel.value || 0);
  if (selected && selected > max) sel.value = '';
}

function trainApplyAdvancedLimits() {
  const preset = trainActivePreset();
  const maxRank = Number(preset.max_rank || TRAIN.trainProfile.max_rank || 0);
  const maxResolution = Number(preset.max_resolution || TRAIN.trainProfile.max_resolution || 0);
  const maxSteps = Number(preset.max_steps || TRAIN.trainProfile.max_steps || 0);
  if (maxRank) trainDisableSelectAbove('trainRank', maxRank);
  if (maxResolution) trainDisableSelectAbove('trainResolution', maxResolution);
  const stepsInput = document.getElementById('trainSteps');
  if (stepsInput && maxSteps) {
    stepsInput.max = String(maxSteps);
    const current = Number(stepsInput.value || 0);
    if (current && current > maxSteps) stepsInput.value = '';
  }
}

function trainUpdateAdvancedFields() {
  trainApplyAdvancedLimits();
}

// ============================================================================
// CHARACTERS TAB — discover paired LoRAs, pick one, type the scene, ship.
// ============================================================================
//
// Server endpoints:
//   GET  /characters                  — discover bundles, returns list
//   GET  /characters/<id>/preview     — serve the sample training image
//   POST /characters/<id>/generate    — assemble prompt + queue render job
//
// Recipe defaults (TC=1.8, stage1=10/stage2=3, cfg=3.0, seed=-1,
// enhance=false, video_skip=1+audio_skip=1) are applied server-side per
// docs/API.md. The UI collects prompt (trigger pre-filled) + duration +
// quality. Draft/Pro use the generation's graded default; LTX-2.5 also offers
// the two-stage High canvases. All four tokens come from BOOT.ltx.character.

window.CHARACTERS = {
  list: [],            // [{id, name, trigger, pronoun, subject_noun, sample_image_url, ...}]
  selected: null,      // currently-composing character (object from list)
  duration: '7s',      // 5s | 7s | 10s | 15s
  quality: 'pro',      // token: draft | pro | high | high720 (last two: 2.5)
  // ONE CHARACTER, ONE PAIR OF STRENGTHS, WHICHEVER SURFACE LAUNCHED IT.
  // These were 0.8 and "applied to both face_lora and audio_lora" — a 2.3-era
  // correction for over-baked quirks at 5000 steps, and a comment that stopped
  // being true when the server split the two files. The server has defaulted
  // face 1.0 / voice 1.0 since; this surface kept sending 0.8 and never sent a
  // voice value at all, so the same character rendered face 0.8 / voice 1.0
  // here and face 1.0 / voice 1.0 from the Manual tab. Same numbers on both
  // lanes or the surfaces disagree again.
  charStrength: 1.0,
  voiceStrength: 1.0,
  // Reference audio for i2v_clean_audio mode (character lip-syncs to
  // this clip). Image-to-video deliberately omitted on the Characters
  // surface per Mr Bizarro 2026-05-16.
  audioPath: null,     // server-side path returned by /upload
  audioName: null,     // original filename for display
  // Extra LoRAs are NOT tracked here — the Characters tab portals the
  // Manual tab's existing #lorasDetails picker into its compose card
  // and reads window._activeLoras directly on submit. One source of
  // truth, no duplicated picker UI.
  loading: false,
  initialised: false,
};

// Triples: [value, short chip label, full descriptive text used in
// the assembled prompt + as a tooltip on the chip].
const CHARACTERS_FRAMING = [
  ['ECU', 'ECU', 'extreme close-up'],
  ['CU',  'CU',  'close-up'],
  ['MCU', 'MCU', 'medium close-up'],
  ['MS',  'MS',  'medium shot'],
  ['LS',  'LS',  'long shot'],
];
// DERIVED from the engine's own length table, not typed. This was the fourth
// independent duration vocabulary in the panel (after the storyboard card's
// [3,5,7,10], the Manual strip and H3's table), and four tables for one concept
// is how the 7-second lie happened. The blurb is the table's own.
const CHARACTERS_DURATION = (((BOOT.ltx || {}).lengths) || [])
  .filter(l => l.offered !== false)
  .map(l => [l.key, l.label, `${l.seconds} seconds — ${l.blurb}`]);
// DERIVED from BOOT.ltx.character — the same server-resolved table the
// composer's character strip renders, so the two surfaces cannot disagree
// about what a character render is.
//
// It used to be a hardcoded pair, `draft` / `high`, carrying 2.3-era wall times
// and "Turbo". Two problems at once: there was no way for the tab to ASK for
// the graded 2.5 path (q8 + distilled), and `high` on 2.5 meant the two-stage
// HQ pipeline plus the 29.5 GB add-on — so the tab was offering the only two
// choices that were wrong.
//
// Each tuple's fourth item is the server row. That makes install routing use
// the same pack/pipeline fields as the main High chips.
const CHARACTERS_QUALITY = (() => {
  const c = ((BOOT.ltx || {}).character) || {};
  const rows = Array.isArray(c.tokens) && c.tokens.length
    ? c.tokens
    : [c.draft, c.pro].filter(Boolean);
  if (!rows.length) {
    return [['pro', 'Q8 Pro', 'Q8 Pro — the graded default character recipe', null]];
  }
  return rows.map(row => [
    row.key,
    row.label,
    `${row.label} — ${row.width}×${row.height}, ${row.tier}`,
    row,
  ]);
})();
// Look-up by value → full descriptive text (the third tuple slot).
const CHARACTERS_FRAMING_TEXT = Object.fromEntries(
  CHARACTERS_FRAMING.map(([v, , full]) => [v, full])
);

async function downloadSampleCharacter() {
  // Class-based (not id) so the button can live in more than one empty state
  // (the Character-mode strip AND the standalone Characters grid) and both
  // update together.
  const btns = Array.from(document.querySelectorAll('.js-get-sample-char'));
  const statuses = Array.from(document.querySelectorAll('.js-get-sample-char-status'));
  if (!btns.length) return;
  const setStatus = (msg) => statuses.forEach(s => { s.hidden = false; s.textContent = msg; });
  const setDisabled = (v) => btns.forEach(b => { b.disabled = v; });
  setDisabled(true);
  setStatus('Starting… (~817 MB, one-time)');
  const finishOk = async (msg) => {
    setStatus(msg);
    // Re-init the picker(s) so the new character appears + the empty state hides.
    try { if (typeof charactersInit === 'function') await charactersInit(); } catch (_) {}
    try { if (typeof refreshManualCharacters === 'function') await refreshManualCharacters(); } catch (_) {}
    setDisabled(false);
  };
  try {
    const r = await fetch('/characters/download-sample', { method: 'POST' });
    const j = await r.json().catch(() => ({}));
    if (j && j.already) { await finishOk('Already installed — "Bizarro" is in your characters.'); return; }
    if (!r.ok && r.status !== 202) { throw new Error((j && j.error) || ('HTTP ' + r.status)); }
    const poll = setInterval(async () => {
      let s;
      try { s = await (await fetch('/characters/download-sample/status')).json(); }
      catch (_) { return; }
      if (s.status === 'downloading') {
        setStatus('Downloading… ' + (s.mb || 0) + ' / ' + (s.total_mb || '?') + ' MB');
      } else if (s.status === 'done' || s.present) {
        clearInterval(poll);
        await finishOk('Installed! "Bizarro" is in your characters now — pick it to start.');
      } else if (s.status === 'error') {
        clearInterval(poll);
        setStatus('Download failed: ' + (s.error || 'unknown') + ' — try again.');
        setDisabled(false);
      }
    }, 2500);
  } catch (e) {
    setStatus('Could not start: ' + (e.message || e));
    setDisabled(false);
  }
}

async function charactersInit() {
  // Idempotent. The grid is the default view; compose state only
  // appears after the user picks a card.
  if (!window.CHARACTERS.initialised) {
    window.CHARACTERS.initialised = true;
    charactersRenderChips();
    charactersSyncStrengthControls();   // the slider shows what we will submit
    charactersBackToGrid();   // ensure grid state visible
  }
  await charactersLoadList();
}

async function charactersLoadList() {
  if (window.CHARACTERS.loading) return;
  window.CHARACTERS.loading = true;
  const grid = document.getElementById('charactersGrid');
  const empty = document.getElementById('charactersEmpty');
  if (grid) grid.innerHTML = '<div class="characters-loading">Scanning <code>mlx_models/loras/</code>…</div>';
  if (empty) empty.hidden = true;
  try {
    const r = await fetch('/characters', { credentials: 'same-origin' });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    window.CHARACTERS.list = Array.isArray(j.characters) ? j.characters : [];
    charactersRenderGrid();
  } catch (e) {
    if (grid) grid.innerHTML = `<div class="characters-loading">Couldn't load characters: ${charactersEscapeHtml(String(e.message || e))}</div>`;
  } finally {
    window.CHARACTERS.loading = false;
  }
}

function charactersRenderGrid() {
  const grid = document.getElementById('charactersGrid');
  const empty = document.getElementById('charactersEmpty');
  if (!grid) return;
  const list = window.CHARACTERS.list || [];
  if (list.length === 0) {
    grid.innerHTML = '';
    if (empty) empty.hidden = false;
    return;
  }
  if (empty) empty.hidden = true;
  const cards = list.map(c => {
    const img = c.sample_image_url
      ? `<img class="characters-card-img" src="${charactersEscapeAttr(c.sample_image_url)}" alt="${charactersEscapeAttr(c.name || c.trigger)}" loading="lazy">`
      : `<div class="characters-card-placeholder">${charactersEscapeHtml((c.name || c.trigger || '?').slice(0, 1).toUpperCase())}</div>`;
    // Silent characters: muted-speaker overlay on the avatar + a small
    // "Silent" pill in the chip row. has_voice is set server-side by
    // list_characters() based on whether <trigger>.audio.safetensors
    // exists alongside the face LoRA.
    const silentOverlay = c.has_voice
      ? ''
      : `<span class="characters-card-silent-overlay" title="No voice LoRA — character is silent">
           <svg class="ph" aria-hidden="true"><use href="#ph-speaker-slash"/></svg>
         </span>`;
    const silentBadge = c.has_voice
      ? ''
      : `<span class="characters-silent-badge" title="No voice LoRA on disk — train one via the Train tab">
           <svg class="ph" aria-hidden="true"><use href="#ph-speaker-slash"/></svg>Silent
         </span>`;
    return `
      <div class="characters-card" role="button" tabindex="0"
           data-character-id="${charactersEscapeAttr(c.id)}"
           onclick="charactersOpenCompose(${escapeHtml(JSON.stringify(String(c.id)))})"
           onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();charactersOpenCompose(${escapeHtml(JSON.stringify(String(c.id)))})}">
        ${img}
        ${silentOverlay}
        <div class="characters-card-meta">
          <div class="characters-card-name">${charactersEscapeHtml(c.name || c.trigger)}</div>
          <div class="characters-card-chips">
            <code class="characters-trigger-chip">${charactersEscapeHtml(c.trigger)}</code>
            <span class="characters-pronoun-chip">${charactersEscapeHtml((c.pronoun || 'they') + ' · ' + (c.subject_noun || 'person'))}</span>
            ${silentBadge}
          </div>
        </div>
      </div>`;
  }).join('');
  grid.innerHTML = cards;
}

function charactersRenderChips() {
  // Each option is [value, shortLabel, fullText, serverRow]. The chip face shows
  // the short label (so 5 framing options fit on one row at ~420px);
  // the full text lives on `title` for accessibility / hover discovery.
  const renderGroup = (containerId, options, current, fieldName) => {
    const el = document.getElementById(containerId);
    if (!el) return;
    el.innerHTML = options.map(([val, label, full, row]) => {
      const needsInstall = fieldName === 'quality' && row
        && typeof ltxCellNeedsInstall === 'function' && ltxCellNeedsInstall(row);
      const title = needsInstall && typeof ltxCellInstallLabel === 'function'
        ? ltxCellInstallLabel(row) : (full || label);
      return (
      `<button type="button" class="characters-chip${val === current ? ' active' : ''}${needsInstall ? ' needs-install' : ''}"
               data-val="${charactersEscapeAttr(val)}"
               data-pipeline="${charactersEscapeAttr((row && row.pipeline) || '')}"
               title="${charactersEscapeAttr(title)}"
               onclick="charactersPickChip(${escapeHtml(JSON.stringify(String(fieldName)))}, ${escapeHtml(JSON.stringify(String(val)))})">${charactersEscapeHtml(label)}</button>`
      );
    }).join('');
  };
  renderGroup('charactersDurationChips', CHARACTERS_DURATION, window.CHARACTERS.duration, 'duration');
  renderGroup('charactersQualityChips',  CHARACTERS_QUALITY,  window.CHARACTERS.quality,  'quality');
}

function charactersPickChip(field, val) {
  if (field !== 'duration' && field !== 'quality') return;
  if (field === 'quality') {
    const option = CHARACTERS_QUALITY.find(([key]) => key === val);
    const row = option && option[3];
    if (row && typeof ltxCellNeedsInstall === 'function'
        && ltxCellNeedsInstall(row)) {
      if (typeof openModelsModal === 'function') openModelsModal();
      return;
    }
  }
  window.CHARACTERS[field] = val;
  charactersRenderChips();
}

function charactersOpenCompose(id) {
  const c = (window.CHARACTERS.list || []).find(x => x.id === id);
  if (!c) return;
  window.CHARACTERS.selected = c;
  // Every entry to the compose view repaints the strength control from state,
  // so a value restored by Load Params is on screen before the user can submit.
  charactersSyncStrengthControls();

  // Toggle states.
  const grid = document.getElementById('charactersGridState');
  const compose = document.getElementById('charactersComposeState');
  if (grid) grid.hidden = true;
  if (compose) compose.hidden = false;

  // Populate the avatar.
  const av = document.getElementById('charactersComposeAvatar');
  const placeholder = document.getElementById('charactersComposeAvatarPlaceholder');
  if (av) {
    if (c.sample_image_url) {
      av.innerHTML = `<img src="${charactersEscapeAttr(c.sample_image_url)}" alt="${charactersEscapeAttr(c.name || c.trigger)}">`;
    } else {
      av.innerHTML = `<div class="characters-avatar-placeholder">${charactersEscapeHtml((c.name || c.trigger || '?').slice(0, 1).toUpperCase())}</div>`;
    }
  }

  // Populate the titles + chips.
  const nameEl = document.getElementById('charactersComposeName');
  if (nameEl) nameEl.textContent = c.name || c.trigger;
  const trigEl = document.getElementById('charactersComposeTrigger');
  if (trigEl) trigEl.textContent = c.trigger;
  const pronounEl = document.getElementById('charactersComposePronoun');
  if (pronounEl) pronounEl.textContent = `${c.pronoun || 'they'} · ${c.subject_noun || 'person'}`;

  // Silent indicator in the compose head. Reuses the same badge style as
  // the grid card so the state is obvious wherever the character is shown.
  let silentEl = document.getElementById('charactersComposeSilent');
  const chipRow = pronounEl ? pronounEl.parentElement : null;
  if (!c.has_voice) {
    if (!silentEl && chipRow) {
      silentEl = document.createElement('span');
      silentEl.id = 'charactersComposeSilent';
      silentEl.className = 'characters-silent-badge';
      silentEl.title = 'No voice LoRA — train one via the Train tab';
      silentEl.innerHTML =
        '<svg class="ph" aria-hidden="true"><use href="#ph-speaker-slash"/></svg>Silent';
      chipRow.appendChild(silentEl);
    } else if (silentEl) {
      silentEl.hidden = false;
    }
  } else if (silentEl) {
    silentEl.hidden = true;
  }

  // Silent characters can't benefit from audio_skip_step (no audio LoRA
  // to skip-guidance). Force Turbo off and disable the checkbox so users
  // aren't confused by a no-op. The label gets a tooltip explaining why.
  const turboCb = document.getElementById('charactersTurbo');
  if (turboCb) {
    turboCb.disabled = !c.has_voice;
    if (!c.has_voice) {
      turboCb.checked = false;
      turboCb.title =
        'This character has no voice LoRA — Turbo (audio skip-guidance) ' +
        'is unavailable. Train a voice via the Train tab to enable.';
    } else {
      turboCb.title = '';
    }
  }

  // Pre-fill the prompt textarea with the trigger word as a starting
  // point so the user keeps it in their prompt. They can edit anything
  // around it but the trigger is what binds identity. Cursor lands at
  // the end so they can immediately type the rest of their scene.
  const ta = document.getElementById('charactersPrompt');
  if (ta) {
    ta.value = `${c.trigger} `;
    setTimeout(() => {
      ta.focus();
      const len = ta.value.length;
      ta.setSelectionRange(len, len);
    }, 60);
  }
  const toast = document.getElementById('charactersToast');
  if (toast) { toast.hidden = true; toast.textContent = ''; toast.classList.remove('error'); }
  // Reset audio state when switching characters.
  charactersClearAudio();
  // Portal the unified LoRA picker into this compose card. Same picker
  // the Manual tab uses (#lorasDetails) — moved here while the user is
  // composing a character clip, so stack-on-top-of-character LoRAs
  // (cinematronx style, etc.) use the same UI everywhere.
  if (typeof _portalLoraPicker === 'function') {
    _portalLoraPicker('characters');
  }
}

function charactersBackToGrid() {
  window.CHARACTERS.selected = null;
  const grid = document.getElementById('charactersGridState');
  const compose = document.getElementById('charactersComposeState');
  if (grid) grid.hidden = false;
  if (compose) compose.hidden = true;
  // Move the LoRA picker back to its declared home (Manual tab #genForm)
  // so that tab's FormData submit still picks up #lorasJson.
  if (typeof _portalLoraPicker === 'function') {
    _portalLoraPicker('video');
  }
}

// Preview removed — the user's prompt IS what the model sees, verbatim.
// Stub kept so legacy oninput hooks calling it don't error.
function charactersUpdatePreview() { /* no-op (preview block removed) */ }

// ---- Reference audio upload (Characters compose card) -----------------

async function charactersHandleAudioUpload(ev) {
  const file = ev.target.files && ev.target.files[0];
  if (!file) return;
  const fnameEl = document.getElementById('charactersAudioFname');
  const clearEl = document.getElementById('charactersAudioClear');
  if (fnameEl) fnameEl.textContent = 'uploading…';
  try {
    const fd = new FormData();
    fd.append('audio', file);  // /upload accepts both `image` and `audio` field names
    const r = await fetch('/upload', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    window.CHARACTERS.audioPath = j.path;
    window.CHARACTERS.audioName = file.name;
    if (fnameEl) fnameEl.textContent = file.name;
    if (clearEl) clearEl.hidden = false;
  } catch (e) {
    if (fnameEl) fnameEl.textContent = 'upload failed: ' + (e.message || e);
  }
  ev.target.value = '';
}

function charactersClearAudio() {
  window.CHARACTERS.audioPath = null;
  window.CHARACTERS.audioName = null;
  const fnameEl = document.getElementById('charactersAudioFname');
  const clearEl = document.getElementById('charactersAudioClear');
  if (fnameEl) fnameEl.textContent = 'none';
  if (clearEl) clearEl.hidden = true;
}

function charactersUpdateStrengthDisplay(val) {
  const v = parseFloat(val);
  if (!isFinite(v)) return;
  window.CHARACTERS.charStrength = v;
  const out = document.getElementById('charactersStrengthValue');
  if (out) out.textContent = v.toFixed(2);
}

// THE CONTROL IS RENDERED FROM THE STATE, NEVER THE OTHER WAY AROUND.
// The slider's `value` was hardcoded in the markup, so when the state default
// moved to 1.0 the two silently disagreed: an untouched form DISPLAYED 0.80 and
// SUBMITTED 1.0. The user reads one number and gets another, and no amount of
// care in the submit path can fix a control that was never told what it holds.
// Called on every entry to the compose view and after Load Params.
function charactersSyncStrengthControls() {
  const v = Number(window.CHARACTERS.charStrength ?? 1.0);
  const slider = document.getElementById('charactersStrength');
  if (slider) {
    // The markup's min/max must not silently clamp the state either.
    const lo = parseFloat(slider.min), hi = parseFloat(slider.max);
    if (isFinite(lo) && v < lo) slider.min = String(v);
    if (isFinite(hi) && v > hi) slider.max = String(v);
    slider.value = String(v);
  }
  const out = document.getElementById('charactersStrengthValue');
  if (out) out.textContent = v.toFixed(2);
}

async function charactersGenerate() {
  const c = window.CHARACTERS.selected;
  if (!c) return;
  const qualityRow = (CHARACTERS_QUALITY.find(
    ([key]) => key === window.CHARACTERS.quality) || [])[3];
  if (qualityRow && typeof ltxCellNeedsInstall === 'function'
      && ltxCellNeedsInstall(qualityRow)) {
    if (typeof openModelsModal === 'function') openModelsModal();
    return;
  }
  const btn = document.getElementById('charactersGenerateBtn');
  const labelSpan = btn ? btn.querySelector('.characters-generate-label') : null;
  const busySpan  = btn ? btn.querySelector('.characters-generate-busy')  : null;
  const promptEl = document.getElementById('charactersPrompt');
  const toast = document.getElementById('charactersToast');
  const prompt_body = (promptEl?.value || '').trim();
  if (btn) btn.disabled = true;
  if (labelSpan) labelSpan.hidden = true;
  if (busySpan)  busySpan.hidden  = false;
  if (toast) { toast.hidden = true; toast.textContent = ''; toast.classList.remove('error'); }

  const fd = new URLSearchParams();
  // The textarea content IS the prompt — passed verbatim. The backend
  // no longer composes anything around it (no framing prefix, no
  // 'Photorealistic, cinematic, atmospheric.' suffix). The trigger
  // word was pre-filled in the textarea so it's already in here.
  fd.set('prompt_body', prompt_body);
  fd.set('duration', window.CHARACTERS.duration);
  fd.set('quality',  window.CHARACTERS.quality);
  // Character LoRA strength — passed through so the backend can apply
  // it to both face_lora and audio_lora at job-build time.
  // BOTH strengths, always. Sending only the face left the voice to the
  // server's default, which meant this surface could not express the pair it
  // was showing and a sidecar could never round-trip it.
  fd.set('character_strength', String(window.CHARACTERS.charStrength ?? 1.0));
  fd.set('character_voice_strength', String(window.CHARACTERS.voiceStrength ?? 1.0));
  // Reference audio (i2v_clean_audio mode) — optional.
  if (window.CHARACTERS.audioPath) fd.set('audio', window.CHARACTERS.audioPath);
  // Extra LoRAs come from the portaled #lorasDetails picker — read
  // _activeLoras directly so we share state with the Manual tab. The
  // character's OWN face + audio LoRAs are auto-stacked on the
  // backend, so only the picker-selected ones go in extra_loras.
  if (typeof _activeLoras !== 'undefined' && Array.isArray(_activeLoras) && _activeLoras.length) {
    const extras = _activeLoras
      .filter(l => l && l.path)
      .map(l => ({ path: l.path, strength: (typeof l.strength === 'number' ? l.strength : 1.0) }));
    if (extras.length) fd.set('extra_loras', JSON.stringify(extras));
  }

  try {
    const r = await fetch(`/characters/${encodeURIComponent(c.id)}/generate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      credentials: 'same-origin',
      body: fd.toString(),
    });
    const j = await r.json();
    if (!r.ok || !j.ok) throw new Error(j.error || ('HTTP ' + r.status));
    if (toast) {
      toast.hidden = false;
      toast.textContent = `Queued ${c.name || c.trigger} · ${window.CHARACTERS.duration} · ${window.CHARACTERS.quality} → job ${j.job_id}`;
    }
    // Refresh the queue/recent display so the user sees the new job
    // land. The panel's status loop polls every couple of seconds; nudge
    // it for an immediate refresh so the new card pops without delay.
    if (typeof poll === 'function') {
      try { poll(); } catch (e) {}
    }
  } catch (e) {
    if (toast) {
      toast.hidden = false;
      toast.classList.add('error');
      toast.textContent = `Couldn't queue: ${String(e.message || e)}`;
    }
  } finally {
    if (btn) btn.disabled = false;
    if (labelSpan) labelSpan.hidden = false;
    if (busySpan)  busySpan.hidden  = true;
  }
}

function charactersEscapeHtml(s) {
  return String(s).replace(/[&<>"']/g, ch => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[ch]));
}
function charactersEscapeAttr(s) { return charactersEscapeHtml(s); }

// Restore the Characters compose state from a sidecar `params` dict that
// was written by /characters/<id>/generate (source === 'characters').
// Called from loadParams() when the clip's sidecar carries that flag.
//
// Sequence:
//   1. Switch workflow tab to Characters (this calls charactersInit which
//      lazy-loads the list if needed).
//   2. Wait for the character list to be ready (charactersLoadList is
//      idempotent — we just await it).
//   3. Call charactersOpenCompose(character_id) — same code path the
//      grid card uses, so all the avatar / chip / preview wiring is
//      reused.
//   4. Restore framing / duration / quality / turbo chip state from the
//      sidecar values. Re-render chips to update .active classes.
//   5. Repopulate the prompt textarea with prompt_body verbatim and
//      refresh the preview.
// ONE RESTORER, BOTH LOAD PATHS. Load Params has two: the Manual path, and
// this Characters branch, which returns early and therefore never reached the
// Manual path's strength restoration at all — so a character clip reloaded
// with BOTH strengths silently back at their defaults. The external review
// found the Manual half fixed and this half untouched, which is exactly what a
// second copy of a rule buys you. There is one copy now.
function _restoreCharacterStrengths(p) {
  const put = (value, inputId) => {
    if (typeof value === 'undefined' || value === null) return;
    const num = parseFloat(value);
    if (Number.isNaN(num)) return;
    const inp = document.getElementById(inputId);
    if (inp) inp.value = num;
  };
  put(p.character_strength, 'characterStrength');
  put(p.character_voice_strength, 'characterVoiceStrength');
  if (typeof _renderCharsAppliedNote === 'function') _renderCharsAppliedNote();
  // The Characters-tab state and its visible slider follow the same numbers,
  // so a clip reopened there shows what it will re-submit.
  if (window.CHARACTERS) {
    const f = parseFloat(p.character_strength);
    const v = parseFloat(p.character_voice_strength);
    if (!Number.isNaN(f)) window.CHARACTERS.charStrength = f;
    if (!Number.isNaN(v)) window.CHARACTERS.voiceStrength = v;
    if (typeof charactersSyncStrengthControls === 'function') {
      charactersSyncStrengthControls();
    }
  }
}

async function charactersLoadParams(p) {
  // 2026-05-17 — Characters is no longer its own workflow tab. Load Params
  // on a Characters-origin sidecar restores EVERYTHING into the Manual
  // tab's NEW Character mode (Codex C+ pass 3: Character is a first-
  // class mode pill, not a chip strip inside T2V). selectManualCharacter
  // sets up the rest: chip strip, Q8 quality strip swap, auto-stacked
  // face+audio LoRAs at submit.
  // Mr Bizarro's intent: "When you click load params, you get everything
  // exactly the same, so you can replicate clips. Else, what for?"
  workflowSwitch('manual');
  if (typeof setMode === 'function') setMode('character');

  // Wait for the Manual chip strip to be populated so selectManualCharacter
  // can find the character. refreshManualCharacters is idempotent + safe
  // to call multiple times.
  if (typeof refreshManualCharacters === 'function') {
    try { await refreshManualCharacters(); } catch (_) {}
  }
  const charList = (typeof _manualCharacters !== 'undefined' && Array.isArray(_manualCharacters))
    ? _manualCharacters : [];
  const found = charList.find(c => c.id === p.character_id);
  if (!found) {
    throw new Error(`character ${p.character_id} not in list`);
  }

  // (Skip-step restore removed in v4.0.5 — the control was dead at the
  // engine boundary, so an old sidecar's value has nothing to restore into.)

  // Pre-select character. selectManualCharacter() writes the id to the
  // hidden #characterIdInput, appends the trigger to the prompt
  // (idempotent), and (via _applyCharacterQualityStripVisibility) swaps
  // the quality strip to Q8 Draft / Q8 Pro.
  if (typeof selectManualCharacter === 'function') {
    selectManualCharacter(p.character_id);
  }

  // Restore the UI TOKEN, not merely the canvas. Pro and High are both
  // 1024×576, so dimensions alone cannot reproduce the selected pipeline.
  // New sidecars carry quality_choice; older ones fall back to their real
  // quality and finally to geometry. There is no schema/version marker on the
  // old dc0051c sidecars that mapped `high` to Pro, so on LTX-2.5 an old bare
  // `quality=high` now reopens as the newly first-class High token.
  const sidecarW = parseInt(p.width || '0', 10);
  const sidecarH = parseInt(p.height || '0', 10);
  const charCfg = ((BOOT.ltx || {}).character) || {};
  const charRows = Array.isArray(charCfg.tokens) ? charCfg.tokens : [];
  const charKeys = charRows.map(row => row.key);
  const aliases = {
    high_720p: 'high720', balanced: 'pro', standard: 'pro', quick: 'draft'
  };
  let charChoice = String(p.quality_choice || '').toLowerCase();
  charChoice = aliases[charChoice] || charChoice;
  if (!charKeys.includes(charChoice)) {
    const pipelineQuality = String(p.quality || '').toLowerCase();
    charChoice = aliases[pipelineQuality] || pipelineQuality;
  }
  const _draftPairs = [[704, 384], [736, 416]];   // current, then legacy
  const isDraft = _draftPairs.some(([w, h]) =>
    (sidecarW === w && sidecarH === h) || (sidecarW === h && sidecarH === w));
  if (!charKeys.includes(charChoice)) charChoice = isDraft ? 'draft' : 'pro';
  const charQualityGroup = document.getElementById('qualityGroupCharacter');
  if (charQualityGroup) {
    const sel = `[data-char-quality="${charChoice}"]`;
    const btn = charQualityGroup.querySelector(sel);
    if (btn && typeof _setCharacterQuality === 'function') {
      _setCharacterQuality(btn, { allowMissing: true });
    }
  }
  if (window.CHARACTERS) window.CHARACTERS.quality = charChoice;

  // Restore aspect (landscape vs vertical). If the sidecar's recorded
  // h > w, it was a vertical render. setAspect already exists and
  // re-derives width/height via QUALITY_PRESETS — but for character mode
  // we want to keep the exact 736×416 / 1024×576 from the sidecar, so
  // we set the hidden aspect input + active chip ourselves and then
  // restore w/h verbatim.
  const aspectInp = document.getElementById('aspect');
  if (aspectInp) {
    const wantVertical = sidecarH > sidecarW;
    aspectInp.value = wantVertical ? 'vertical' : 'landscape';
    document.querySelectorAll('#aspectGroup .pill-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.aspect === aspectInp.value));
  }
  // Now restore the verbatim dims so any later setQuality / setAspect
  // callbacks don't overwrite them.
  const wInp = document.getElementById('width');
  const hInp = document.getElementById('height');
  if (wInp && sidecarW > 0) wInp.value = sidecarW;
  if (hInp && sidecarH > 0) hInp.value = sidecarH;

  // Both strengths — this branch returns before the Manual path's restoration.
  _restoreCharacterStrengths(p);

  // Restore frames + duration. Frames is the source of truth for the
  // render; the duration field is metadata that drives the UI estimate.
  const sidecarFrames = parseInt(p.frames || '0', 10);
  if (sidecarFrames > 0) {
    const framesInp = document.getElementById('frames');
    if (framesInp) framesInp.value = sidecarFrames;
    // Compute duration from frames (8k+1 → seconds at 24fps).
    const durSec = Math.round(((sidecarFrames - 1) / 24) * 10) / 10;
    const durInp = document.getElementById('duration');
    if (durInp) durInp.value = durSec;
  }

  // Restore seed — prefer seed_used (the integer the helper actually
  // picked at generation time) over seed (often '-1' when the user let
  // the panel randomize). Without this preference, Load Params on a
  // -1 submission restored -1 and the next render got a fresh random
  // seed instead of reproducing the source clip. Mirrors the Manual
  // loadParams fix (commit b024bb5, 2026-05-18); the Character branch
  // was missed in that pass and reported by Mr Bizarro on the panel.
  const _seedRaw = (p.seed_used != null && String(p.seed_used) !== '' && String(p.seed_used) !== '-1')
                    ? p.seed_used
                    : p.seed;
  if (typeof _seedRaw !== 'undefined' && _seedRaw !== null && String(_seedRaw) !== '-1') {
    const seedInp = document.getElementById('seed');
    if (seedInp) seedInp.value = String(_seedRaw);
  }

  // Prompt textarea — verbatim. Use prompt_body if present (legacy
  // sidecars), else the full prompt.
  const ta = document.getElementById('prompt');
  if (ta) {
    ta.value = (typeof p.prompt_body === 'string' && p.prompt_body)
      ? p.prompt_body
      : (p.prompt || '');
    ta.dispatchEvent(new Event('input', { bubbles: true }));
  }

  // Restore extra LoRAs (style stacks on top of character). The sidecar
  // stores `loras` as the FULL stack (face + audio + extras). Filter out
  // the character's own face/audio paths to recover only the extras.
  if (Array.isArray(p.loras) && p.loras.length > 0) {
    const facePath  = (found.face_lora_path  || '');
    const audioPath = (found.audio_lora_path || '');
    const extras = p.loras.filter(l => {
      const lp = l && l.path;
      return lp && lp !== facePath && lp !== audioPath;
    });
    if (extras.length && typeof _activeLoras !== 'undefined') {
      _activeLoras.length = 0;
      for (const l of extras) {
        _activeLoras.push({ path: l.path, strength: l.strength });
      }
      if (typeof renderLorasList === 'function') renderLorasList();
    }
  }

  // Scroll to top so the user sees the restored state.
  const formPane = document.querySelector('aside.form-pane');
  if (formPane) formPane.scrollTop = 0;
  // Done — early-exit before the old dead-UI code that followed.
  // The Manual tab IS the character surface now, and the restoration above is
  // the whole of it. What used to follow this point was 37 lines targeting the
  // retired Characters-tab UI, unreachable behind a `return;` and labelled
  // "DEAD CODE BELOW (kept temporarily to avoid bigger diff)". It outlived the
  // follow-up that was meant to prune it and became actively dangerous: it held
  // NEWER-looking quality-mapping logic than the live path above, so the next
  // reader had two plausible implementations and only one that runs. Deleted.
}


// ============================================================================
// TRAIN CHARACTER tab
// ============================================================================
const TRAIN_MIN = Number(BOOT.train_min_images || 15);
const TRAIN_MAX = Number(BOOT.train_max_images || 50);

// SYS-03: on a Mac under TRAIN_MIN_RAM_GB, /train/start has always
// refused (409) -- but only AFTER a user drops a whole dataset, captions
// it, and downloads 41 GB of LTX-2.3 weights the preflight card offers
// unconditionally. Gate the whole tab at the entry instead: every direct
// child of #trainSection except the gate card itself is hidden, and
// Generate never reaches the 41 GB offer or the dataset dropzone.
// Returns true when gated (caller should skip the rest of trainInit).
function trainApplyRamGate() {
  const card = document.getElementById('trainGateCard');
  const section = document.getElementById('trainSection');
  if (!card || !section) return false;
  const tier = (LAST_STATUS && LAST_STATUS.tier) || {};
  const ram = typeof tier.ram_gb === 'number' ? tier.ram_gb : null;
  const minRam = typeof tier.train_min_ram_gb === 'number' ? tier.train_min_ram_gb : 24;
  const gated = ram !== null && ram > 0 && ram < minRam;
  card.hidden = !gated;
  // Codex UI-7: this used to set every other child's `hidden` to `gated`
  // — on every /status poll — which overwrote
  // visibility the children own themselves: Style training hides the Voice
  // card, and the next poll un-hid it. The gate is a class on the section
  // now (panel.css hides every child but the gate card); no child's own
  // `hidden` is touched.
  section.classList.toggle('train-ram-gated', gated);
  if (gated) {
    document.getElementById('trainGateTitle').textContent =
      `Training a character needs a Mac with ${minRam} GB or more`;
    document.getElementById('trainGateBody').textContent =
      `This one has ${ram} GB — the trainer runs out of memory before it `
      + `finishes on this Mac, every time. You can still use characters: `
      + `try the sample character below, or a community-trained one.`;
  }
  return gated;
}

function trainInit() {
  // SYS-03: check the RAM gate before anything else runs — a gated Mac
  // never wires the drop zone, never renders presets, never shows the
  // 41 GB download offer.
  if (trainApplyRamGate()) return;
  // Idempotent — safe to call on every setMode('train') without re-binding
  // the drop zone. Triggers a list refresh too so the user sees the LoRAs
  // they trained in earlier sessions.
  if (!TRAIN.initialised) {
    TRAIN.initialised = true;
    trainWireDropZone();
    trainWireBundleDropZone();
    // Note: train-type pill clicks are now handled by `setTrainType()`
    // wired directly via the buttons' onclick=, not via this init
    // path. See setTrainType definition below for context.
    trainWirePresetButtons();
    trainWireAdvancedFields();
    trainWireVoice();
    // Initial trigger value — JS-side generator (instant); /train/suggest-trigger
    // exists for non-JS callers.
    const t = document.getElementById('trainTrigger');
    if (t && !t.value) t.value = trainGenerateTriggerJS();
    document.getElementById('trainTriggerExample').textContent =
      (document.getElementById('trainTrigger').value || 'mrztrn');
    trainTriggerDigitWarn();
    document.getElementById('trainTrigger').addEventListener('input', () => {
      document.getElementById('trainTriggerExample').textContent =
        (document.getElementById('trainTrigger').value || 'mrztrn');
      trainTriggerDigitWarn();
      trainUpdateButtonState();
      trainUpdateEstimate();
    });
  }
  trainUpdatePresetButtons();
  trainUpdateAdvancedFields();
  trainRefreshLoraList();
  trainUpdateEstimate();
  trainUpdateButtonState();
  trainUpdateStartLabel();
  trainCheckPreflight();
  trainGuidanceRestore();
}

// Train-type pill click handler. Top-level (not behind trainInit's
// initialised-once gate) so it always works regardless of how the user
// got to the Train tab — including page reloads where the JS engine
// kept a previous TRAIN.initialised=true in memory. Same pattern as
// setMode / setQuality / setAccel (direct onclick from the HTML).
//
// First attempt (d1a8f9e, addEventListener inside trainInit) was
// reported as still-not-clickable on 2026-05-20; suspect the gate
// kept it from firing on subsequent train-tab opens. Direct onclick
// at the button is bulletproof.
window.setTrainType = function(t) {
  if (t !== 'character' && t !== 'style') return;
  // 1. Toggle .active across the pill group
  const group = document.getElementById('trainTypeGroup');
  if (group) {
    group.querySelectorAll('.pill-btn[data-train-type]').forEach((p) => {
      p.classList.toggle('active', p.dataset.trainType === t);
    });
  }
  // 2. State
  if (typeof TRAIN === 'object' && TRAIN) {
    TRAIN.trainType = t;
    // Snap to the new type's recommended preset — but only while the user
    // has not picked one themselves. Character wants High (the graded
    // identity recipe), style wants Quick (a look, cheaply); silently
    // carrying a 3-hour High over to a style run, or a rank-8 Quick over to
    // a face, is the same class of mistake #62 is about.
    const table = (t === 'style') ? TRAIN.stylePresets : TRAIN.presets;
    const want = (TRAIN.defaultPresets || {})[t];
    if (!TRAIN.presetTouched && want && table && table[want]) {
      TRAIN.preset = want;
    } else if (table && !table[TRAIN.preset]) {
      TRAIN.preset = (want && table[want]) ? want : 'quick';
    }
  }
  // 3. Swap guidance bodies (both shipped in HTML; one starts hidden)
  const gc = document.getElementById('trainGuidanceBodyCharacter');
  const gs = document.getElementById('trainGuidanceBodyStyle');
  if (gc) gc.hidden = (t === 'style');
  if (gs) gs.hidden = (t !== 'style');
  // 4. Hide voice card for style (styles don't have voices)
  const vc = document.getElementById('trainVoiceCard');
  if (vc) vc.hidden = (t === 'style');
  // 5. Re-render downstream UI bits if they exist
  if (typeof trainUpdatePresetButtons === 'function') trainUpdatePresetButtons();
  if (typeof trainUpdateAdvancedFields === 'function') trainUpdateAdvancedFields();
  if (typeof trainUpdateEstimate === 'function') trainUpdateEstimate();
  if (typeof trainUpdateButtonState === 'function') trainUpdateButtonState();
  if (typeof trainUpdateStartLabel === 'function') trainUpdateStartLabel();
};

// "How to train well" guidance — open by default, dismissible. The dismissed
// state persists in localStorage so power users don't see the panel every
// session. New users always see it on first visit.
function trainGuidanceRestore() {
  const el = document.getElementById('trainGuidance');
  if (!el) return;
  try {
    if (localStorage.getItem('phos_train_guidance_dismissed') === '1') {
      el.removeAttribute('open');
    }
  } catch (_) { /* private browsing — leave open */ }
}

function trainGuidanceDismiss() {
  const el = document.getElementById('trainGuidance');
  if (el) el.removeAttribute('open');
  try { localStorage.setItem('phos_train_guidance_dismissed', '1'); } catch (_) {}
}

// Hits /train/preflight and surfaces a banner inside the Train form when a
// required model is missing. Today the only on-demand download is the LTX-2.3
// full-precision dev transformer (~21 GB, Q8 repo) — Phosphene's default
// install ships the distilled transformer only, and the Q4 repo's smaller
// transformer-dev is quantized (trainer refuses it, #35). Banner offers a
// one-click Download button that triggers /train/install + redirects to
// /status for progress.
async function trainCheckPreflight() {
  const box = document.getElementById('trainPreflight');
  if (!box) return;
  try {
    const r = await fetch('/train/preflight');
    const data = await r.json();
    if (!data || !data.ok) { box.style.display = 'none'; return; }
    const missing = (data.required || []).filter(m => !m.ready);
    if (missing.length === 0) {
      box.style.display = 'none';
      return;
    }
    box.style.display = 'block';
    // SYS-04: this used to disclose the cost piecemeal, one Download
    // button per row, with the actual total (41 GB across these two
    // downloads, on a build that already has everything else) never
    // stated anywhere. One combined total + one "Download all" that
    // runs them in sequence (the download lane is a singleton — only
    // one hf pull runs at a time, so this awaits each before starting
    // the next rather than firing them together).
    const totalGb = missing.reduce((sum, m) => sum + (Number(m.size_gb) || 0), 0);
    const q8ExtraNote = (LAST_STATUS && LAST_STATUS.q8_available)
      ? ''
      : ` Using a trained character afterward needs the separate 30 GB `
      + `Q8 pack too — that one's offered from Character mode when you get there.`;
    box.innerHTML = `
      <div class="train-preflight-card">
        <div class="train-preflight-title">
          <svg class="ph" aria-hidden="true" style="margin-right:6px;vertical-align:-2px"><use href="#ph-warning-fill"/></svg>Get ready to train: ~${totalGb.toFixed(1)} GB (LTX-2.3 training weights)
        </div>
        <div class="train-preflight-list">
          ${missing.map(m => `
            <div class="train-preflight-row">
              <div class="train-preflight-label">${m.label}
                <span class="train-preflight-size">~${m.size_gb} GB</span></div>
              <div class="train-preflight-blurb">${m.blurb}</div>
              <button type="button" class="btn btn-primary"
                      onclick="trainInstall('${m.key}')">Download</button>
            </div>
          `).join('')}
        </div>
        <div class="train-preflight-actions">
          <button type="button" class="btn btn-primary" id="trainInstallAllBtn">Download all (~${totalGb.toFixed(1)} GB)</button>
        </div>
        <div class="train-preflight-foot">
          Phosphene installs only what it renders with. Training runs against
          LTX-2.3 and needs its own weights, so they are downloaded on demand
          rather than shipped to everyone. Each download is resumable, and
          nothing above is needed to render.${q8ExtraNote}
        </div>
      </div>
    `;
    // Codex UI-4: the handler was an inline onclick="trainInstallAll([...])"
    // whose JSON's double quotes closed the double-quoted attribute, leaving
    // `trainInstallAll([` — a syntax error, so "Download all" did nothing.
    // Bound here instead, with the key list as a real array.
    const allKeys = missing.map(m => m.key);
    const allBtn = box.querySelector('#trainInstallAllBtn');
    if (allBtn) allBtn.onclick = () => trainInstallAll(allKeys);
  } catch (e) {
    box.style.display = 'none';
  }
}

// SYS-04: "Download all" — runs the missing items through the SAME
// singleton download lane trainInstall() already uses, one at a time
// (only one hf pull can be active, so firing them concurrently would
// just 409 on the second). Each step awaits the preflight actually
// reporting that key ready before starting the next.
async function trainInstallAll(keys) {
  const btn = document.getElementById('trainInstallAllBtn');
  for (const key of keys) {
    if (btn) { btn.disabled = true; btn.textContent = 'Downloading…'; }
    await trainInstallAndWait(key);
  }
  trainCheckPreflight();
}

function trainInstallAndWait(key) {
  return new Promise((resolve) => {
    trainInstall(key, resolve);
  });
}

async function trainInstall(key, onDone) {
  // The panel parses POST bodies with parse_qs (urlencoded ONLY); a FormData
  // body serializes as multipart and reads back empty, so /train/install saw
  // key='' and returned "unknown install key" (reported by @cocktailpeanut,
  // 2026-06-04). Send urlencoded like every other POST in this panel.
  //
  // `onDone` (SYS-04, added for trainInstallAll's sequential "Download
  // all"): called once this KEY is ready, or once, on failure, so a
  // multi-item sequence never hangs waiting for a download that never
  // started. Optional — the plain single-button Download path ignores it.
  const finish = () => { if (typeof onDone === 'function') onDone(); };
  try {
    const r = await fetch('/train/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'key=' + encodeURIComponent(key),
    });
    const data = await r.json();
    if (!data.ok) {
      alert('Download failed: ' + (data.error || r.status));
      finish();
      return;
    }
    // Progress streams to STATUS log. Re-poll preflight every 5s; banner
    // self-hides when the file lands. Watches THIS key specifically (not
    // "everything missing is now ready") so a sequential multi-download
    // resolves after each step instead of only after the last one.
    const watch = setInterval(async () => {
      try {
        const r2 = await fetch('/train/preflight');
        const d2 = await r2.json();
        const row = (d2.required || []).find(m => m.key === key);
        if (!row || row.ready) {
          clearInterval(watch);
          trainCheckPreflight();
          finish();
        }
      } catch (e) { /* ignore */ }
    }, 5000);
  } catch (e) {
    alert('Download request failed: ' + e.message);
    finish();
  }
}

// Trigger generator — `<3 consonants><2 digits>` for rare/uncommon tokens.
// Skip vowels + ambiguous letters (l, i, o → 1, 0). Mirrors the Python
// _suggest_trigger_token; not a security boundary so the algorithms can
// drift slightly — server has its own generator for non-JS callers.
// Letters-only, `<3 consonants>trn` — the shape of the earliest triggers
// that carried a face here (e.g. bizarrotrn). The old `mrz07` shape
// tokenizes to m / rz / 0 / 7: two single-digit tokens with a huge prior of
// their own. Mirrors _suggest_trigger_token() server-side (#62).
function trainGenerateTriggerJS() {
  const cons = 'bcdfghjkmnpqrstvwxyz';
  let letters = '';
  for (let i = 0; i < 3; i++) letters += cons[Math.floor(Math.random() * cons.length)];
  return letters + 'trn';
}

// Warn (never block) when a typed trigger carries digits — see above.
function trainTriggerDigitWarn() {
  const t = document.getElementById('trainTrigger');
  const hint = document.getElementById('trainTriggerHint');
  if (!t || !hint) return;
  const bad = /\d/.test((t.value || '').trim());
  hint.textContent = bad
    ? 'digits split into their own very common tokens — every trigger that has carried a face here was letters-only (e.g. mrztrn)'
    : 'a rare, letters-only token the model will associate with this character';
  hint.classList.toggle('warn', bad);
}

function trainSuggestTrigger() {
  const t = document.getElementById('trainTrigger');
  t.value = trainGenerateTriggerJS();
  document.getElementById('trainTriggerExample').textContent = t.value;
  trainTriggerDigitWarn();
}

function trainWireDropZone() {
  const drop = document.getElementById('trainDrop');
  const input = document.getElementById('trainFileInput');
  if (!drop || !input) return;
  drop.addEventListener('click', (e) => {
    // Avoid re-firing the picker when the user clicks an existing thumb's
    // controls (× / num pill).
    if (e.target.closest('.train-thumb')) return;
    input.click();
  });
  drop.addEventListener('dragover', (e) => {
    e.preventDefault();
    drop.classList.add('dragover');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    const files = e.dataTransfer && e.dataTransfer.files;
    if (files && files.length) trainUploadFiles(files);
  });
  input.addEventListener('change', () => {
    if (input.files && input.files.length) trainUploadFiles(input.files);
    input.value = '';
  });
}

function trainWireBundleDropZone() {
  const drop = document.getElementById('trainBundleDrop');
  const input = document.getElementById('trainBundleInput');
  if (!drop || !input) return;
  drop.addEventListener('click', () => { if (!drop.classList.contains('busy')) input.click(); });
  drop.addEventListener('dragover', (e) => {
    e.preventDefault();
    drop.classList.add('dragover');
  });
  drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
  drop.addEventListener('drop', (e) => {
    e.preventDefault();
    drop.classList.remove('dragover');
    const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (f) trainUploadBundle(f);
  });
  input.addEventListener('change', () => {
    if (input.files && input.files.length) trainUploadBundle(input.files[0]);
    input.value = '';
  });
}

async function trainUploadBundle(file) {
  const drop = document.getElementById('trainBundleDrop');
  const status = document.getElementById('trainStatus');
  if (!file) return;
  if (!/\.zip$/i.test(file.name)) {
    if (status) status.textContent = 'Bundle must be a .zip file.';
    return;
  }
  if (drop) drop.classList.add('busy');
  if (status) status.textContent = `Unpacking ${file.name}…`;
  try {
    const fd = new FormData();
    fd.append('file', file, file.name);
    if (TRAIN.jobId) fd.append('job_id', TRAIN.jobId);
    const r = await fetch('/train/upload-bundle', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      if (status) status.textContent = 'Bundle error: ' + (j.error || r.status);
      return;
    }
    TRAIN.jobId = j.job_id;
    await trainRefreshDataset();
    const bits = [
      `${j.image_count} image(s) staged`,
      `${j.caption_count} caption(s)`,
      `${j.paired_count} paired`,
    ];
    if (Array.isArray(j.unpaired_warnings) && j.unpaired_warnings.length) {
      bits.push(`warnings: ${j.unpaired_warnings.slice(0, 2).join(' · ')}${j.unpaired_warnings.length > 2 ? '…' : ''}`);
    }
    if (status) status.textContent = bits.join(' · ');
  } catch (e) {
    if (status) status.textContent = 'Bundle upload failed: ' + (e.message || 'unknown');
  } finally {
    if (drop) drop.classList.remove('busy');
  }
}

function trainWirePresetButtons() {
  document.querySelectorAll('#trainPresetGroup .pill-btn').forEach(b => {
    b.addEventListener('click', () => {
      TRAIN.preset = b.dataset.trainPreset;
      TRAIN.presetTouched = true;
      document.querySelectorAll('#trainPresetGroup .pill-btn').forEach(x =>
        x.classList.toggle('active', x === b));
      trainUpdateAdvancedFields();
      trainUpdateEstimate();
    });
  });
  // Crop strategy pills — same click pattern. Updates the hidden
  // #trainCropStrategy input which rides the form on submit. No estimate
  // change because letterbox uses the same square canvas as center crop;
  // wall time + memory are unaffected.
  document.querySelectorAll('#trainCropStrategyGroup .pill-btn').forEach(b => {
    b.addEventListener('click', () => {
      const v = b.dataset.trainCrop;
      const inp = document.getElementById('trainCropStrategy');
      if (inp) inp.value = v;
      document.querySelectorAll('#trainCropStrategyGroup .pill-btn').forEach(x =>
        x.classList.toggle('active', x === b));
    });
  });
}

function trainWireAdvancedFields() {
  ['trainRank', 'trainSteps', 'trainLR', 'trainResolution', 'trainCaptionStrategy']
    .forEach(id => {
      const el = document.getElementById(id);
      if (el) el.addEventListener('change', () => {
        trainApplyAdvancedLimits();
        trainUpdateEstimate();
      });
      if (el) el.addEventListener('input', () => {
        trainApplyAdvancedLimits();
        trainUpdateEstimate();
      });
    });
  trainApplyAdvancedLimits();
}

async function trainUploadFiles(fileList) {
  const status = document.getElementById('trainStatus');
  // Partition incoming files into images and captions. The user may drop
  // either type into the same zone — image_001.png + image_001.txt is the
  // intended workflow.
  const all = Array.from(fileList);
  // SYS-02: HEIC/HEIF/AVIF (macOS Photos/AirDrop's default hand-off) used
  // to fail BOTH tests here and get silently dropped from the selection —
  // no toast, no row in the grid, nothing. The server now converts them to
  // JPEG on upload (normalize_ingested_image_bytes); this filter just has
  // to stop throwing them away before they get there.
  const imgs = all.filter(f =>
    /^image\/(png|jpe?g|webp|heic|heif|avif)$/.test(f.type) ||
    /\.(png|jpe?g|webp|heic|heif|avif)$/i.test(f.name));
  const caps = all.filter(f =>
    /\.txt$/i.test(f.name) || /^text\/plain$/.test(f.type));
  // SYS-10b: files that are neither an accepted image type nor a caption
  // used to just vanish from the drop with zero message. They're now named
  // in the persistent end-of-batch summary below instead of silently
  // dropped. (HEIC is accepted above since SYS-02 — the server converts it.)
  const rejected = all.filter(f => !imgs.includes(f) && !caps.includes(f))
    .map(f => `${f.name} (unsupported format)`);
  if (!imgs.length && !caps.length) {
    if (status) {
      status.textContent = rejected.length
        ? `No supported files in that selection. Skipped: ${rejected.join(', ')}`
        : 'No supported files in that selection (need PNG / JPG / WEBP / HEIC / TXT).';
    }
    return;
  }
  const total = imgs.length + caps.length;
  if (status) status.textContent = `Uploading 0 / ${total}…`;

  let done = 0;
  let capThinWarnings = [];
  // Per-file failures (413 too-large, server error, network) used to only
  // ever live in status.textContent for the instant before the NEXT file's
  // "Uploaded N / total…" overwrote it — so a failure on file 2 of 6 was
  // invisible by the time file 3 finished. Collected here and folded into
  // the persistent end-of-batch summary instead.
  let failed = [];

  // ----- IMAGES (optimistic placeholders so the grid lights up immediately) -----
  for (let i = 0; i < imgs.length; i++) {
    const f = imgs[i];
    if (TRAIN.images.length >= TRAIN_MAX) {
      if (status) status.textContent = `At max ${TRAIN_MAX} images. Stopping upload.`;
      break;
    }
    const localUrl = await trainFileToDataURL(f).catch(() => null);
    const placeholderIdx = TRAIN.images.length;
    TRAIN.images.push({
      filename: '__uploading__' + placeholderIdx,
      path: '',
      src: localUrl || '',
      uploading: true,
      captioned: false,
    });
    trainRenderThumbs();

    try {
      const fd = new FormData();
      fd.append('file', f, f.name);
      if (TRAIN.jobId) fd.append('job_id', TRAIN.jobId);
      const r = await fetch('/train/upload', { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok || !j.ok) {
        TRAIN.images.splice(placeholderIdx, 1);
        const reason = j.error || `HTTP ${r.status}`;
        failed.push(`${f.name} (${reason})`);
        if (status) status.textContent = 'Upload error: ' + reason;
        trainRenderThumbs();
        continue;
      }
      TRAIN.jobId = j.job_id;
      TRAIN.images[placeholderIdx] = {
        filename: j.filename,
        path: j.path,
        src: '/train/file?job_id=' + encodeURIComponent(j.job_id) +
             '&filename=' + encodeURIComponent(j.filename) +
             '&v=' + Date.now(),
        uploading: false,
        captioned: !!j.captioned,
        original_stem: j.original_stem || null,
      };
      done += 1;
      trainRenderThumbs();
      if (status) status.textContent = `Uploaded ${done} / ${total}…`;
    } catch (e) {
      TRAIN.images.splice(placeholderIdx, 1);
      const reason = e.message || 'unknown';
      failed.push(`${f.name} (${reason})`);
      if (status) status.textContent = 'Upload failed: ' + reason;
      trainRenderThumbs();
    }
  }

  // ----- CAPTIONS (server pairs by stem against caption_map.json) -----
  for (let i = 0; i < caps.length; i++) {
    const f = caps[i];
    try {
      const fd = new FormData();
      fd.append('file', f, f.name);
      if (TRAIN.jobId) fd.append('job_id', TRAIN.jobId);
      const r = await fetch('/train/upload', { method: 'POST', body: fd });
      const j = await r.json();
      if (!r.ok || !j.ok) {
        const reason = j.error || `HTTP ${r.status}`;
        failed.push(`${f.name} (${reason})`);
        if (status) status.textContent = 'Caption upload error: ' + reason;
        continue;
      }
      TRAIN.jobId = j.job_id;
      if (typeof j.word_count === 'number' && j.word_count < 10) {
        capThinWarnings.push(`${f.name} (${j.word_count} words)`);
      }
      done += 1;
      if (status) status.textContent = `Uploaded ${done} / ${total}…`;
    } catch (e) {
      const reason = e.message || 'unknown';
      failed.push(`${f.name} (${reason})`);
      if (status) status.textContent = 'Caption upload failed: ' + reason;
    }
  }
  if (caps.length) {
    // Pull canonical state so the captioned badges on existing thumbs
    // refresh (a caption may have just paired with an already-uploaded image).
    await trainRefreshDataset();
  }

  if (status) {
    const n = TRAIN.images.length;
    const captioned = TRAIN.images.filter(x => x.captioned).length;
    const bits = [];
    if (n < TRAIN_MIN) bits.push(`${n} image${n === 1 ? '' : 's'} — need ${TRAIN_MIN - n} more to train`);
    else bits.push(`${n} images ready`);
    if (caps.length) bits.push(`${captioned} captioned`);
    if (capThinWarnings.length) {
      bits.push(`thin captions: ${capThinWarnings.slice(0, 3).join(', ')}${capThinWarnings.length > 3 ? '…' : ''}`);
    }
    const skipped = rejected.concat(failed);
    if (skipped.length) {
      bits.push(`${skipped.length} file${skipped.length === 1 ? '' : 's'} skipped: `
        + `${skipped.slice(0, 3).join(', ')}${skipped.length > 3 ? '…' : ''}`);
    }
    status.textContent = bits.join(' · ');
    status.classList.toggle('train-status-warn', skipped.length > 0);
  }
  trainUpdateEstimate();
  trainUpdateButtonState();
}

function trainFileToDataURL(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result);
    r.onerror = reject;
    r.readAsDataURL(file);
  });
}

function trainRenderThumbs() {
  const wrap = document.getElementById('trainThumbs');
  const empty = document.getElementById('trainDropEmpty');
  const drop = document.getElementById('trainDrop');
  const clearAllBtn = document.getElementById('trainClearAllBtn');
  if (!wrap) return;
  if (TRAIN.images.length === 0) {
    wrap.hidden = true;
    if (empty) empty.style.display = '';
    if (drop) drop.classList.remove('has-images');
    if (clearAllBtn) clearAllBtn.hidden = true;
    trainUpdateCounter();
    return;
  }
  wrap.hidden = false;
  if (empty) empty.style.display = 'none';
  if (drop) drop.classList.add('has-images');
  if (clearAllBtn) clearAllBtn.hidden = false;
  wrap.innerHTML = TRAIN.images.map((img, idx) => {
    const cls = 'train-thumb' + (img.uploading ? ' uploading' : '');
    const removeAttr = img.uploading
      ? ''
      : `onclick="trainRemoveImage(${escapeHtml(JSON.stringify(img.filename))})"`;
    const capCls = img.uploading ? '' :
      (img.captioned ? 'captioned' : 'uncaptioned');
    const capLabel = img.uploading ? '' :
      (img.captioned ? '✓ cap' : 'no cap');
    const capTitle = img.uploading ? '' :
      (img.captioned ? 'caption paired' : 'no caption — will fall back to trigger_simple');
    return `<div class="${cls}" data-idx="${idx}">
      <img src="${img.src || ''}" alt="char ${idx + 1}" loading="lazy">
      <span class="train-thumb-num">${String(idx + 1).padStart(3, '0')}</span>
      ${capLabel ? `<span class="train-thumb-cap ${capCls}" title="${capTitle}">${capLabel}</span>` : ''}
      <button type="button" class="train-thumb-x" ${removeAttr} title="Remove"><svg class="ph" aria-hidden="true"><use href="#ph-x-bold"/></svg></button>
    </div>`;
  }).join('');
  trainUpdateCounter();
}

function trainUpdateCounter() {
  const counter = document.getElementById('trainCounter');
  const hint = document.getElementById('trainCounterHint');
  const capChip = document.getElementById('trainCaptionCounter');
  if (!counter) return;
  const n = TRAIN.images.length;
  const captioned = TRAIN.images.filter(x => x.captioned).length;
  // SYS-36: this used to be "N / 500 images" -- TRAIN_MAX (500) is a
  // technical ceiling (mlx_ltx_panel.py's own comment calls the earlier
  // 50 "a friendly-default-misread-as-hard-rule"), not a target, so it
  // reads as a goal nobody should aim for. The sweet spot (20-50) is
  // what's worth stating; the hard ceiling only matters once someone is
  // actually near it.
  counter.textContent = `${n} image${n === 1 ? '' : 's'}`;
  counter.classList.toggle('ok', n >= TRAIN_MIN);
  counter.classList.toggle('short', n > 0 && n < TRAIN_MIN);
  if (hint) {
    if (n === 0) hint.textContent = `${TRAIN_MIN} minimum · 20–50 is the sweet spot`;
    else if (n < TRAIN_MIN) hint.textContent = `need ${TRAIN_MIN - n} more`;
    else if (n < TRAIN_MAX) hint.textContent = `ready · 20–50 is the sweet spot, more helps variety`;
    else hint.textContent = `at the ${TRAIN_MAX}-image limit`;
  }
  if (capChip) {
    if (captioned === 0) {
      capChip.hidden = true;
    } else {
      capChip.hidden = false;
      capChip.textContent = (captioned === n)
        ? `${captioned} captioned · all paired`
        : `${captioned} / ${n} captioned`;
      capChip.classList.toggle('ok', captioned === n && n > 0);
      capChip.classList.toggle('partial', captioned > 0 && captioned < n);
    }
  }
}

async function trainRemoveImage(filename) {
  if (!TRAIN.jobId || !filename) return;
  try {
    const fd = new URLSearchParams();
    fd.set('train_job_id', TRAIN.jobId);
    fd.set('filename', filename);
    const r = await fetch('/train/remove-image', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      console.warn('remove-image failed', j);
      return;
    }
    // Server renumbers char_001…NN; re-pull the canonical list so our
    // src URLs match the new filenames on disk.
    await trainRefreshDataset();
  } catch (e) {
    console.warn('remove-image error', e);
  }
}

async function trainClearAll() {
  if (!TRAIN.images.length) return;
  if (!confirm(`Remove all ${TRAIN.images.length} uploaded images?`)) return;
  const toRemove = TRAIN.images.filter(x => !x.uploading).map(x => x.filename);
  for (const f of toRemove) {
    await trainRemoveImage(f);
  }
  TRAIN.images = [];
  trainRenderThumbs();
  trainUpdateEstimate();
  trainUpdateButtonState();
}

async function trainRefreshDataset() {
  if (!TRAIN.jobId) {
    TRAIN.images = [];
    trainRenderThumbs();
    return;
  }
  try {
    const r = await fetch('/train/dataset?job_id=' + encodeURIComponent(TRAIN.jobId));
    const j = await r.json();
    if (!j.ok) return;
    TRAIN.images = (j.images || []).map(im => ({
      filename: im.filename,
      path: im.path,
      src: '/train/file?job_id=' + encodeURIComponent(TRAIN.jobId) +
           '&filename=' + encodeURIComponent(im.filename) + '&v=' + Date.now(),
      uploading: false,
      captioned: !!im.captioned,
      caption_words: im.caption_words || null,
      original_stem: im.original_stem || null,
    }));
    if (Array.isArray(j.parked_captions) && j.parked_captions.length) {
      const status = document.getElementById('trainStatus');
      if (status) {
        const msg = `${j.parked_captions.length} caption(s) parked without matching images: ${j.parked_captions.slice(0, 3).join(', ')}${j.parked_captions.length > 3 ? '…' : ''}`;
        status.textContent = msg;
      }
    }
    trainRenderThumbs();
    trainUpdateEstimate();
    trainUpdateButtonState();
  } catch (e) { console.warn('train dataset refresh failed', e); }
}

// ====== Auto-caption (Gemma 3) ======
//
// One-click captioning of every image in the current dataset. POSTs to
// /train/auto-caption which spawns caption_with_gemma.py as a subprocess;
// progress streams to STATE.log and is mirrored on /train/auto-caption/status
// (i / n / current file). We poll that status endpoint every 1 sec while a
// run is active, update the inline progress bar, and refresh the dataset
// view on completion so the user sees the new captioned count + per-image
// caption_words update without a manual reload.
//
// Memory note: Gemma 3 12B (~6 GB at 4-bit) loads in the subprocess and
// frees on exit, so it doesn't accumulate on top of the dev transformer
// the trainer needs next. The backend refuses to start a second run while
// one is in flight + refuses if a training job is currently running.
let _trainCaptionPoll = null;

async function trainAutoCaption() {
  const btn = document.getElementById('trainAutoCaptionBtn');
  const label = document.getElementById('trainAutoCaptionLabel');
  const prog = document.getElementById('trainAutoCaptionProgress');
  const status = document.getElementById('trainAutoCaptionStatus');
  const fill = document.getElementById('trainAutoCaptionBarFill');
  if (!btn || btn.disabled) return;
  if (!TRAIN.jobId) {
    if (status) status.textContent = 'No dataset yet — drop some images first.';
    return;
  }
  const trig = (document.getElementById('trainTrigger').value || '').trim();
  if (!trig) {
    if (status) status.textContent = 'Set a trigger word first.';
    return;
  }
  const n = TRAIN.images.filter(x => !x.uploading).length;
  // Confirmation if there are already captions — auto-caption OVERWRITES.
  // We treat any caption count > 0 as "user has invested in captions" and
  // ask before clobbering. Cheap safety net.
  const existingCaps = TRAIN.images.filter(x => x.captioned).length;
  if (existingCaps > 0) {
    if (!confirm(
      `Auto-caption will OVERWRITE ${existingCaps} existing caption(s) ` +
      `for "${trig}". Continue?`
    )) return;
  }
  btn.disabled = true;
  if (label) label.textContent = 'Captioning…';
  if (prog) prog.hidden = false;
  if (fill) fill.style.width = '0%';
  if (status) status.textContent = `Loading the prompt helper (~3s)…`;
  const fd = new FormData();
  fd.set('train_job_id', TRAIN.jobId);
  fd.set('trigger', trig);
  let r;
  try {
    r = await fetch('/train/auto-caption', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(fd),
    });
  } catch (e) {
    if (status) status.textContent = 'Network error: ' + (e.message || e);
    trainAutoCaptionFinish();
    return;
  }
  const j = await r.json().catch(() => ({}));
  if (!r.ok || !j.ok) {
    if (status) status.textContent = 'Failed: ' + (j.error || `HTTP ${r.status}`);
    (typeof packNeededToast === 'function' && packNeededToast(j));   // 4.20: the captioning model may still be downloading
    trainAutoCaptionFinish();
    return;
  }
  // Backend accepted — start polling for progress.
  _trainCaptionPoll = setInterval(trainAutoCaptionPollOnce, 1000);
}

async function trainAutoCaptionPollOnce() {
  let s;
  try {
    const r = await fetch('/train/auto-caption/status');
    s = await r.json();
  } catch (e) { return; }
  const status = document.getElementById('trainAutoCaptionStatus');
  const fill = document.getElementById('trainAutoCaptionBarFill');
  const i = s.i || 0, n = s.n || 0;
  if (fill) fill.style.width = (n > 0 ? (i / n) * 100 : 0).toFixed(1) + '%';
  if (status) {
    if (s.error) {
      status.textContent = `Error: ${s.error}`;
    } else if (s.running) {
      const f = s.current_file ? ` · ${s.current_file}` : '';
      status.textContent = `${i} / ${n}${f} · ${s.elapsed_sec}s elapsed`;
    } else {
      status.textContent = `Done · ${i} captions in ${s.elapsed_sec}s. Refreshing…`;
    }
  }
  if (!s.running) {
    if (_trainCaptionPoll) { clearInterval(_trainCaptionPoll); _trainCaptionPoll = null; }
    // Refresh the dataset so the captioned-count + per-image word_count
    // updates in the thumb grid, then re-enable the button.
    await trainRefreshDataset();
    setTimeout(trainAutoCaptionFinish, 600);
  }
}

function trainAutoCaptionFinish() {
  const btn = document.getElementById('trainAutoCaptionBtn');
  const label = document.getElementById('trainAutoCaptionLabel');
  if (btn) btn.disabled = false;
  if (label) label.textContent = 'Auto-caption with Gemma 3';
  // Leave the progress strip visible at 100% so the user has a record
  // of the run; it'll reset to 0% next time the button is clicked.
  trainUpdateButtonState();
}

// Reflect upload/trigger state into the auto-caption button. Called from
// the existing trainUpdateButtonState() so we don't add another mutation
// observer.
function trainUpdateAutoCaptionState() {
  const btn = document.getElementById('trainAutoCaptionBtn');
  if (!btn) return;
  const n = TRAIN.images.filter(x => !x.uploading).length;
  const trig = (document.getElementById('trainTrigger').value || '').trim();
  // Don't toggle while a run is in flight — the runner manages the button.
  if (_trainCaptionPoll) return;
  const ok = n >= 1 && trig.length >= 3 && trig.length <= 32;
  btn.disabled = !ok;
  if (!ok) {
    if (n < 1) btn.title = 'Drop at least one image first.';
    else if (trig.length < 3) btn.title = 'Trigger word must be 3+ characters.';
    else btn.title = 'Trigger word is too long (max 32).';
  } else {
    btn.title = `Run Gemma 3 on all ${n} image(s) — ~${(2.5 * n).toFixed(0)}s`;
  }
}

function trainEffectiveSteps() {
  const v = (document.getElementById('trainSteps').value || '').trim();
  if (!v) return null;
  const n = parseInt(v, 10);
  return Number.isFinite(n) && n > 0 ? n : null;
}

// Mirrors py-side _preset_steps_for(): steps = epochs × image_count, capped
// by preset.max_steps when the active hardware profile supplies one.
// Used by trainUpdateEstimate to keep the ETA chip honest as the user
// drops more photos in. Floor of 1 image so n=0 doesn't yield zero
// steps (matches server behavior).
function trainComputeSteps(preset, imageCount) {
  const epochs = parseInt(preset.epochs, 10) || 0;
  let steps = Math.max(1, epochs * Math.max(1, imageCount | 0));
  const maxSteps = Number(preset.max_steps || 0);
  if (maxSteps > 0) steps = Math.min(steps, maxSteps);
  return steps;
}

function trainUpdateEstimate() {
  trainApplyAdvancedLimits();
  const preset = trainActivePreset();
  const stepOverride = trainEffectiveSteps();
  const n = TRAIN.images.length;
  let steps = stepOverride || trainComputeSteps(preset, n);
  const maxSteps = Number(preset.max_steps || TRAIN.trainProfile.max_steps || 0);
  if (maxSteps > 0) steps = Math.min(steps, maxSteps);
  const sec = Math.round(3 * Math.max(0, n) + steps * preset.seconds_per_step + 30);
  // Estimate row is hidden until the user has dropped at least one
  // image. Before that, the row reads as missing data — not a useful
  // signal. Once there's data, reveal the row + populate it.
  const row = document.getElementById('trainEstimate');
  if (row) row.style.display = n === 0 ? 'none' : '';
  const ramRow = document.getElementById('trainEstimateRam');
  const timeRow = document.getElementById('trainEstimateTime');
  const outRow = document.getElementById('trainEstimateOut');
  if (timeRow && n > 0) {
    const profile = TRAIN.trainProfile && TRAIN.trainProfile.label
      ? ` · ${TRAIN.trainProfile.label}`
      : '';
    timeRow.textContent = trainFmtDuration(sec) + ` · ${steps} steps${profile}`;
  }
  if (ramRow) ramRow.textContent = `~${preset.ram_peak_gb} GB peak`;
  if (outRow) {
    const trig = (document.getElementById('trainTrigger').value || 'mrz07');
    outRow.textContent = `mlx_models/loras/${(TRAIN.jobId || 'trn-<new>')}.safetensors · trigger "${trig}"`;
  }
}

function trainFmtDuration(sec) {
  if (sec < 60) return `${sec} s`;
  const m = Math.round(sec / 60);
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const rem = m % 60;
  return rem ? `${h} h ${rem} min` : `${h} h`;
}

function trainUpdateButtonState() {
  const btn = document.getElementById('trainStartBtn');
  if (!btn) return;
  const n = TRAIN.images.filter(x => !x.uploading).length;
  const trig = (document.getElementById('trainTrigger').value || '').trim();
  const ok = n >= TRAIN_MIN && trig.length >= 3 && trig.length <= 32;
  btn.disabled = !ok;
  if (!ok) {
    if (n < TRAIN_MIN) btn.title = `Need at least ${TRAIN_MIN} images (have ${n}).`;
    else if (trig.length < 3) btn.title = 'Trigger word must be 3+ characters.';
    else btn.title = 'Trigger word is too long (max 32).';
  } else {
    btn.title = 'Queue this training job.';
  }
  // The auto-caption button has a LOWER bar (1 image + 3-char trigger,
  // versus 15 images for training) so it's tracked separately. Folded
  // into this same controller so we don't add another mutation observer
  // — every place that calls trainUpdateButtonState() now also reflects
  // the auto-caption state.
  trainUpdateAutoCaptionState();
}

async function trainStart() {
  const btn = document.getElementById('trainStartBtn');
  const status = document.getElementById('trainStatus');
  if (!TRAIN.jobId || TRAIN.images.length < TRAIN_MIN) {
    if (status) status.textContent = `Need at least ${TRAIN_MIN} images.`;
    return;
  }
  const trig = (document.getElementById('trainTrigger').value || '').trim();
  if (!trig) {
    if (status) status.textContent = 'Trigger word required.';
    return;
  }
  // Caption warnings: thin captions (< 10 words) or user_provided strategy
  // with no .txt files at all. Surface as a one-time confirm so power users
  // can override (e.g. they want bare trigger_simple anyway).
  const captionStrategy = document.getElementById('trainCaptionStrategy').value || 'user_provided';
  const captionedCount = TRAIN.images.filter(x => x.captioned).length;
  const thinCaps = TRAIN.images.filter(x =>
    x.captioned && typeof x.caption_words === 'number' && x.caption_words < 10);
  if (captionStrategy === 'user_provided' && captionedCount === 0) {
    if (!confirm('Caption strategy is "user_provided" but no .txt captions were uploaded. ' +
                 'Continue with the trigger_simple fallback for every image?')) return;
  } else if (thinCaps.length) {
    if (!confirm(`${thinCaps.length} caption(s) are very short (< 10 words). ` +
                 'Short captions usually train weaker LoRAs. Continue anyway?')) return;
  }
  const fd = new URLSearchParams();
  fd.set('train_job_id', TRAIN.jobId);
  // train_type drives whether the /train/start server treats this as
  // a character (face + optional voice) or a style (look + color +
  // lighting) training. The pill at the top of the Train tab sets
  // TRAIN.trainType; without sending it here, the server would default
  // to 'character' for every submission and clicking Style would
  // silently train a character LoRA against style frames.
  fd.set('train_type', TRAIN.trainType || 'character');
  fd.set('trigger', trig);
  fd.set('preset', TRAIN.preset);
  fd.set('image_count', String(TRAIN.images.length));
  fd.set('caption_strategy', captionStrategy);
  // Crop strategy chip — center (default) or letterbox.
  const cropInp = document.getElementById('trainCropStrategy');
  if (cropInp && cropInp.value) fd.set('crop_strategy', cropInp.value);
  const rank = document.getElementById('trainRank').value;
  if (rank) fd.set('rank', rank);
  const stepsVal = document.getElementById('trainSteps').value;
  if (stepsVal) fd.set('steps', stepsVal);
  const lrVal = document.getElementById('trainLR').value;
  if (lrVal) fd.set('lr', lrVal);
  const resVal = document.getElementById('trainResolution').value;
  if (resVal) fd.set('resolution', resVal);

  // Voice phase — only sent when the toggle is on AND a clip is uploaded.
  // /train/start re-validates voice presence server-side and 400s if the
  // clip is missing.
  if (TRAIN.voiceEnabled && TRAIN.voiceFile) {
    fd.set('train_audio', 'true');
    const vp = TRAIN.voicePresets[TRAIN.voicePreset]
            || TRAIN.voicePresets.standard;
    fd.set('audio_steps', String(vp.steps));
  }

  const restoreLabel = () => { if (btn) btn.textContent = trainStartLabelText(); };
  if (btn) { btn.disabled = true; btn.textContent = 'Submitting…'; }
  try {
    const r = await fetch('/train/start', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      if (status) status.textContent = 'Failed to enqueue: ' + (j.error || r.status);
      if (btn) { btn.disabled = false; restoreLabel(); }
      return;
    }
    if (status) status.textContent = `Queued · job ${j.queued_id}. Watch the Now / Queue pane for progress.`;
    // Reset the form so the user can stage another dataset. The current
    // job's files stay on disk — only our local mirror is cleared.
    TRAIN.jobId = null;
    TRAIN.images = [];
    TRAIN.voiceFile = null;
    TRAIN.voiceEnabled = false;
    trainRenderThumbs();
    trainRenderVoice();
    trainUpdateEstimate();
    restoreLabel();
    // Refresh queue poll immediately so the new job card appears without
    // waiting for the next 1.5s tick.
    if (typeof poll === 'function') poll();
  } catch (e) {
    if (status) status.textContent = 'Enqueue failed: ' + (e.message || 'unknown');
    if (btn) { btn.disabled = false; restoreLabel(); }
  }
}

// ============================================================
// Voice (optional) — drop zone + toggle + audio-step presets
// ============================================================

function trainStartLabelText() {
  if (TRAIN.trainType === 'style') return 'Train Style';
  return (TRAIN.voiceEnabled && TRAIN.voiceFile)
    ? 'Train Character + Voice'
    : 'Train Character';
}

function trainUpdateStartLabel() {
  const btn = document.getElementById('trainStartBtn');
  if (!btn) return;
  // Don't override 'Submitting…' mid-request.
  if (btn.textContent === 'Submitting…') return;
  btn.textContent = trainStartLabelText();
}

function trainWireVoice() {
  const drop = document.getElementById('trainVoiceDrop');
  const input = document.getElementById('trainVoiceFileInput');
  if (drop && input) {
    drop.addEventListener('click', (e) => {
      // Don't re-open the picker if the user clicks the remove button or
      // interacts with the <audio> element.
      if (e.target.closest('.train-voice-remove')) return;
      if (e.target.closest('audio')) return;
      input.click();
    });
    drop.addEventListener('dragover', (e) => {
      e.preventDefault();
      drop.classList.add('dragover');
    });
    drop.addEventListener('dragleave', () =>
      drop.classList.remove('dragover'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault();
      drop.classList.remove('dragover');
      const files = e.dataTransfer && e.dataTransfer.files;
      if (files && files.length) trainVoiceUpload(files[0]);
    });
    input.addEventListener('change', () => {
      if (input.files && input.files.length) trainVoiceUpload(input.files[0]);
      input.value = '';
    });
  }
  // Voice preset chips.
  document.querySelectorAll('#trainVoicePresetGroup .pill-btn').forEach(b => {
    b.addEventListener('click', () => {
      TRAIN.voicePreset = b.dataset.voicePreset || 'standard';
      document.querySelectorAll('#trainVoicePresetGroup .pill-btn').forEach(x =>
        x.classList.toggle('active', x === b));
    });
  });
  trainRenderVoice();
}

function _trainVoiceInlineError(msg) {
  const el = document.getElementById('trainVoiceInlineError');
  if (!el) return;
  if (msg) { el.textContent = msg; el.hidden = false; }
  else { el.hidden = true; el.textContent = ''; }
}

async function trainVoiceUpload(file) {
  if (!file) return;
  const status = document.getElementById('trainStatus');
  _trainVoiceInlineError(null);
  // The voice endpoint requires a dataset (job_id) — the user must drop
  // at least one image first. Guard with a friendly message, shown BOTH
  // on the card itself (SYS-10c) and on the shared status line.
  if (!TRAIN.jobId) {
    const msg = 'Upload at least one training image first — the voice clip attaches to that dataset.';
    if (status) status.textContent = msg;
    _trainVoiceInlineError(msg);
    return;
  }
  // Quick client-side extension + size check (server re-validates).
  const ext = (file.name.match(/\.[^.]+$/) || [''])[0].toLowerCase();
  if (!['.wav', '.mp3', '.m4a', '.flac'].includes(ext)) {
    const msg = `Unsupported audio type ${ext} — use WAV / MP3 / M4A / FLAC.`;
    if (status) status.textContent = msg;
    _trainVoiceInlineError(msg);
    return;
  }
  if (file.size > 50 * 1024 * 1024) {
    const msg = `Voice clip too large (${(file.size / 1024 / 1024).toFixed(1)} MB) — max 50 MB.`;
    if (status) status.textContent = msg;
    _trainVoiceInlineError(msg);
    return;
  }
  try {
    const fd = new FormData();
    fd.append('job_id', TRAIN.jobId);
    fd.append('file', file, file.name);
    const r = await fetch('/train/upload-voice', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      const msg = 'Voice upload failed: ' + (j.error || r.status);
      if (status) status.textContent = msg;
      _trainVoiceInlineError(msg);
      return;
    }
    // Build a local object URL for the audio preview so we can play
    // immediately without round-tripping through /train/voice-file (which
    // doesn't exist yet — preview is local-only).
    const audioUrl = URL.createObjectURL(file);
    TRAIN.voiceFile = {
      filename: j.filename,
      path: j.path,
      size: j.size,
      // Server-side ffprobe duration — authoritative since it uses the
      // same ffmpeg the trainer uses to decode. The client-side
      // <audio>.duration reading is still a fallback for browsers that
      // can't decode the format inline (rare but possible for FLAC).
      durationSeconds: typeof j.duration_seconds === 'number' ? j.duration_seconds : null,
      audioUrl,
      originalName: file.name,
    };
    TRAIN.voiceEnabled = true;     // auto-on after a successful upload
    trainRenderVoice();
    trainUpdateStartLabel();
    if (status) {
      const sizeMB = (j.size / 1024 / 1024).toFixed(2);
      const dur = TRAIN.voiceFile.durationSeconds;
      // Soft warning band: anything outside the 10–25 s sweet spot but
      // still inside [min, max] passes the upload but gets a friendly
      // nudge. Under min already 400'd at the server.
      let durMsg = '';
      if (typeof dur === 'number') {
        durMsg = ` · ${dur.toFixed(1)} s`;
        if (dur > 30) durMsg += ' (long — consider trimming to 10–25 s)';
        else if (dur < 5) durMsg += ' (short — recommend ≥5 s)';
      }
      status.textContent =
        `Voice clip ready (${sizeMB} MB${durMsg}). ` +
        'Press play above to preview, then start training.';
    }
  } catch (e) {
    if (status) status.textContent =
      'Voice upload failed: ' + (e.message || 'unknown');
  }
}

async function trainVoiceRemove(ev) {
  if (ev) { ev.preventDefault(); ev.stopPropagation(); }
  const f = TRAIN.voiceFile;
  // Free the local object URL before dropping the reference.
  if (f && f.audioUrl) {
    try { URL.revokeObjectURL(f.audioUrl); } catch (_) {}
  }
  if (TRAIN.jobId) {
    try {
      const fd = new URLSearchParams();
      fd.set('train_job_id', TRAIN.jobId);
      await fetch('/train/remove-voice', { method: 'POST', body: fd });
    } catch (e) {
      console.warn('remove-voice error', e);
    }
  }
  TRAIN.voiceFile = null;
  TRAIN.voiceEnabled = false;
  trainRenderVoice();
  trainUpdateStartLabel();
}

function trainVoiceToggleChanged() {
  const cb = document.getElementById('trainVoiceToggle');
  TRAIN.voiceEnabled = !!(cb && cb.checked);
  trainRenderVoice();
  trainUpdateStartLabel();
}

function trainRenderVoice() {
  const drop = document.getElementById('trainVoiceDrop');
  const empty = document.getElementById('trainVoiceEmpty');
  const loaded = document.getElementById('trainVoiceLoaded');
  const audio = document.getElementById('trainVoiceAudio');
  const nameEl = document.getElementById('trainVoiceFilename');
  const metaEl = document.getElementById('trainVoiceMeta');
  const toggle = document.getElementById('trainVoiceToggle');
  const toggleHint = document.getElementById('trainVoiceToggleHint');
  const stepsRow = document.getElementById('trainVoiceStepsRow');
  const f = TRAIN.voiceFile;
  if (f) {
    if (drop) drop.classList.add('has-file');
    if (empty) empty.hidden = true;
    if (loaded) loaded.hidden = false;
    if (audio) {
      if (audio.src !== f.audioUrl) audio.src = f.audioUrl;
    }
    if (nameEl) nameEl.textContent = f.originalName || f.filename;
    if (metaEl) {
      const sizeKB = (f.size / 1024).toFixed(0);
      const sizeStr = f.size > 1024 * 1024
        ? `${(f.size / 1024 / 1024).toFixed(2)} MB`
        : `${sizeKB} KB`;
      // Once the <audio> metadata loads we can append duration.
      metaEl.textContent = sizeStr;
      if (audio) {
        audio.addEventListener('loadedmetadata', () => {
          if (Number.isFinite(audio.duration)) {
            metaEl.textContent = `${audio.duration.toFixed(1)} s · ${sizeStr}`;
          }
        }, { once: true });
      }
    }
    if (toggle) {
      toggle.disabled = false;
      toggle.checked = !!TRAIN.voiceEnabled;
    }
    if (toggleHint) {
      toggleHint.textContent = TRAIN.voiceEnabled
        ? '(adds ~25–125 min depending on preset)'
        : '(uploaded but training skipped)';
    }
    if (stepsRow) stepsRow.hidden = !TRAIN.voiceEnabled;
  } else {
    if (drop) drop.classList.remove('has-file');
    if (empty) empty.hidden = false;
    if (loaded) loaded.hidden = true;
    if (audio) audio.removeAttribute('src');
    if (toggle) {
      toggle.disabled = true;
      toggle.checked = false;
    }
    if (toggleHint) toggleHint.textContent = 'upload a clip to enable';
    if (stepsRow) stepsRow.hidden = true;
  }
}

async function trainRefreshLoraList() {
  const list = document.getElementById('trainLoraList');
  if (!list) return;
  // Keep the global picker in sync. /loras scans the same directory but
  // also rebuilds _knownUserLoras + drops stale entries from _activeLoras.
  // Cheap (one fs scan) and harmless if it races with a render. Letting
  // it run unconditionally so a freshly-trained LoRA appears in T2V's
  // picker the moment the trained list updates — no manual refresh.
  try { if (typeof refreshLoras === 'function') refreshLoras(); } catch (e) {}
  try {
    const r = await fetch('/train/list');
    const j = await r.json();
    const items = (j.loras || []);
    trainRenderVerdictBanner(items);
    if (!items.length) {
      list.innerHTML = '<div class="hint" style="padding:12px 0">No trained LoRAs yet. Start your first run above.</div>';
      return;
    }
    // Compact name-only chips (rewritten 2026-05-18 per Mr Bizarro). Each
    // chip is a link that switches to the Video tab + drops the LoRA
    // into the picker. The previous per-row "Use in T2V / I2V / Copy
    // trigger / delete" chrome moved off this card; deletion lives in
    // the LoRA picker's row controls and the trigger is one click away
    // on the Video tab.
    list.innerHTML = items.map(it => {
      const safeTrig = (it.trigger || '').replace(/[<>&"']/g, c =>
        ({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'})[c]);
      const displayName = it.name || it.filename;
      // The verdict rides on the chip so a dead LoRA is visible in the list,
      // not only in the log of the run that made it (#62). `unknown` — every
      // LoRA older than the measurement — is deliberately undecorated:
      // silence is not weakness.
      const verdict = String(it.adapter_verdict || 'unknown').toLowerCase();
      const flagged = (verdict === 'weak' || verdict === 'inert');
      const advice = it.adapter_advice || '';
      const title = flagged
        ? `"${safeTrig}" — ${verdict.toUpperCase()}: ${advice}`
        : `"${safeTrig}" — ${it.size_mb} MB · ${trainFmtAge(Date.now() - (Number(it.created_at) || 0) * 1000)}`;
      const badge = flagged
        ? `<span class="train-lora-verdict">${verdict === 'inert' ? 'DEAD' : 'WEAK'}</span>`
        : '';
      return `<a href="#" class="train-lora-chip${flagged ? ' is-weak' : ''}" title="${escapeHtml(title)}"
        onclick="trainUseInVideo(${escapeHtml(JSON.stringify(it.path || ''))},${escapeHtml(JSON.stringify(it.trigger || ''))},'t2v'); return false;">${escapeHtml(displayName)}${badge}</a>`;
    }).join('');
  } catch (e) {
    list.innerHTML = '<div class="hint">Load failed: ' + (e.message || 'unknown') + '</div>';
  }
}

// The banner above the chips. Speaks only when the most recent training is
// weak or inert — the moment a user is standing in this tab wondering why the
// character they just spent hours on does nothing.
function trainRenderVerdictBanner(items) {
  const el = document.getElementById('trainVerdictBanner');
  if (!el) return;
  const rows = Array.isArray(items) ? items.slice() : [];
  rows.sort((a, b) => (Number(b.created_at) || 0) - (Number(a.created_at) || 0));
  const newest = rows[0];
  const verdict = newest ? String(newest.adapter_verdict || 'unknown').toLowerCase() : 'unknown';
  if (!newest || (verdict !== 'weak' && verdict !== 'inert')) {
    el.hidden = true;
    el.innerHTML = '';
    return;
  }
  const name = escapeHtml(newest.name || newest.filename || 'this adapter');
  const advice = escapeHtml(newest.adapter_advice ||
    'Its learned deltas are too small to visibly change a render.');
  const head = verdict === 'inert'
    ? `<strong>${name} carries nothing.</strong>`
    : `<strong>${name} came out weak.</strong>`;
  el.innerHTML =
    `<div class="train-verdict-head">${head}</div>` +
    `<div class="train-verdict-body">${advice}</div>` +
    `<div class="train-verdict-body train-verdict-how">Measured by Phosphene when the run finished. ` +
    `You can re-check any adapter yourself: <code>./ltx-2-mlx/env/bin/python3.11 lora_compat.py &lt;file&gt;.safetensors</code></div>`;
  el.hidden = false;
}

function trainFmtAge(ms) {
  if (!Number.isFinite(ms) || ms < 0) return 'just now';
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return `${d}d ago`;
}

// SYS-35: called from queue.js's notifyJobsDone the moment a mode='train'
// job finishes. /train/list is the same source trainRefreshLoraList()
// already renders from, so the newest entry by created_at IS the character
// that just finished — reused rather than trusting anything off the job
// dict itself, whose output shape for training differs from a render job's.
async function _trainAnnounceReady(job) {
  try {
    const r = await fetch('/train/list');
    const j = await r.json();
    const items = (j.loras || []);
    if (!items.length) return;
    const newest = items.reduce((a, b) =>
      (Number(b.created_at) || 0) > (Number(a.created_at) || 0) ? b : a);
    if (!newest || !newest.path || !newest.trigger) return;
    const el = phosToast(`Your character "${newest.trigger}" is ready.`,
      { kind: 'success', duration: 0 });
    if (!el) return;
    const a = document.createElement('a');
    a.href = '#';
    a.className = 'phos-toast-action';
    a.textContent = 'Make a clip with it';
    a.onclick = (ev) => {
      ev.preventDefault();
      el.remove();
      trainUseInVideo(newest.path, newest.trigger, 't2v');
    };
    el.appendChild(a);
  } catch (e) { /* best-effort */ }
}

function trainUseInVideo(loraPath, trigger, targetMode) {
  // Switch the panel into the requested video mode + pre-fill the LoRA
  // picker by emitting an "add" against the unified picker's state. The
  // picker's _activeLoras list is what the form serialises into the
  // hidden #lorasJson input on submit. Falls back to copying the path
  // to clipboard if the picker hooks aren't present.
  try { setMode(targetMode); } catch (e) {}
  // Pre-fill the prompt textarea with the trigger word as a starter so
  // the user sees how it's used.
  const prompt = document.getElementById('prompt');
  if (prompt && !prompt.value) {
    prompt.value = `${trigger} man, cinematic medium shot, soft natural light`;
  }
  // Use the existing picker plumbing if available. _activeLoras and
  // renderLorasList come from the unified LoRA picker module.
  if (typeof window._activeLoras !== 'undefined' && Array.isArray(window._activeLoras)) {
    // Go through the picker's own add path: it renders the chip AND
    // serialises #lorasJson, which is what the form actually posts. The
    // previous hand-rolled push rendered the chip but called a serialiser
    // that never existed (_syncLorasJsonField), so a freshly trained
    // character LOOKED applied and rendered without it (review 2026-09-02).
    addLoraToActive({ path: loraPath, strength: 1.0, name: loraPath.split('/').pop() });
  } else {
    // Fall back: copy the path so the user can paste it into the LoRA
    // picker manually.
    if (typeof imgStudioCopyPath === 'function') imgStudioCopyPath(loraPath);
  }
}

function trainCopyTriggerCmd(trigger) {
  if (!trigger) return;
  const text = `${trigger} man, cinematic medium shot, soft natural light`;
  if (typeof imgStudioCopyPath === 'function') imgStudioCopyPath(text);
}

async function trainDeleteLora(loraPath) {
  if (!loraPath) return;
  if (!confirm('Delete this LoRA?\n\n' + loraPath)) return;
  try {
    const fd = new URLSearchParams();
    fd.set('path', loraPath);
    const r = await fetch('/train/delete', { method: 'POST', body: fd });
    const j = await r.json();
    if (!r.ok || !j.ok) {
      alert('Delete failed: ' + (j.error || r.status));
      return;
    }
    trainRefreshLoraList();
  } catch (e) {
    alert('Delete error: ' + (e.message || 'unknown'));
  }
}

// Quality presets (Y1.013) — each one bundles the backend quality value
// (which selects the model + sampler) with the canonical dimensions.
// Backend routes on the cell's `pipeline`, not the key name, so
  // 'quick', 'balanced', and 'standard' all run Q4 distilled — they differ in
// pixel count. The richer label is preserved in the sidecar so the
// info modal can show "Quick" / "Standard" / "High" verbatim.
globalThis.QUALITY_PRESETS = {
  quick:    { w: 640,  h: 448, upscale: 'off' },        // 10:7, fastest sanity check. 448 not 480: the two-stage lane snaps to multiples of 64, so 480 was never delivered.
  balanced: { w: 1024, h: 576, upscale: 'fit_720p' },   // exact 16:9 → 1280×720
  standard: { w: 1280, h: 704, upscale: 'off' },        // LTX-wide canonical render
  // High = Q8 quality at 1024×576. Lab finding 2026-05-09: at 1024×576
  // Q8 produces "outstanding" output at ~7:48 wall vs ~11:51 at
  // 1280×704 — Q8's quality differentiator is per-token detail
  // capacity, not raw pixels. User can still pick 1280×704 explicitly
  // via the resolution chip; this preset just sets the saner default.
  high:     { w: 1024, h: 576, upscale: 'off' },
  // High · 720p (v4.0.2): the same two-stage HQ lane at the native 720p canvas.
  // Without this row setQuality() treated the key as unknown and fell back to
  // 'standard', so clicking the High · 720p chip queued a Standard render
  // (quality=standard, 1280×704, 8 steps). test_high720_quality_preset.py
  // holds every LTX registry key to a row here.
  high_720p: { w: 1280, h: 704, upscale: 'off' },
};

function setQuality(q) {
  // Tolerate legacy values from old sidecars: 'draft' → 'standard'.
  if (q === 'draft' || !QUALITY_PRESETS[q]) q = 'standard';
  document.getElementById('quality').value = q;
  document.querySelectorAll('#qualityGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.quality === q));
  // Set canonical dimensions for the preset, respecting the current
  // aspect choice. Quick is 4:3 only — landscape orientation only.
  const preset = QUALITY_PRESETS[q];
  const aspect = document.getElementById('aspect').value || 'landscape';
  if (q === 'quick') {
    document.getElementById('width').value  = preset.w;
    document.getElementById('height').value = preset.h;
  } else {
    // VC-39: square (1:1) and 4:5 are fixed cells, shared with applyAspect
    // via _aspectDims so the two never disagree about what a pick renders.
    const dims = (typeof _aspectDims === 'function')
      ? _aspectDims(aspect, q)
      : { w: (aspect === 'vertical' ? preset.h : preset.w),
          h: (aspect === 'vertical' ? preset.w : preset.h) };
    document.getElementById('width').value  = dims.w;
    document.getElementById('height').value = dims.h;
  }
  setUpscale(preset.upscale || 'off');
  // Hide the Aspect row when Quick is active (only 4:3 supported); show
  // it for Standard/High where 16:9 vs 9:16 is a real choice.
  const aspectRow = document.getElementById('aspectRow');
  if (aspectRow) aspectRow.style.display = (q === 'quick') ? 'none' : '';
  applyQuality();
  updateAccelAvailability();
  updateTemporalAvailability();
  updateCustomizeSummary();
  if (typeof _applyStgRowVisibility === 'function') {
    try { _applyStgRowVisibility(); } catch (_) {}
  }
  if (LAST_STATUS) updateModelsCard(LAST_STATUS);
  // H3 owns the render shape while it's the active engine, and setQuality()
  // has several callers (boot, aspect changes, Load Params, the workflow-tab
  // restore) that would otherwise stomp the tier geometry with LTX preset dims
  // and re-arm the LTX upscale. Re-applying here makes every path
  // self-correcting instead of leaving the quick-settings advertising
  // "1024×576 → 1280×720 fit" for a render that ships 768×448.
  if (document.body.dataset.engine === 'h3' && typeof setH3Tier === 'function') {
    try { setH3Tier((document.getElementById('h3_tier') || {}).value); } catch (_) {}
    if (typeof setUpscale === 'function') { try { setUpscale('off'); } catch (_) {} }
  }
  // Repaint the LTX strips LAST, after every field this function owns is
  // settled, so the chips print the shape that is now actually in the form.
  // Both axes are repainted because moving the canvas re-prices every length.
  if (typeof renderTierAxes === 'function') {
    try { renderTierAxes('ltx'); } catch (_) {}
  }
}
// Does the selected quality run the two-stage HQ lane? Reads the SAME registry
// cell the server does (BOOT.ltx.qualities[].pipeline) instead of comparing to
// the literal 'high' — which is what made these gates blind to a second HQ tier
// and would have offered accel/interpolation on High · 720p, where the backend
// refuses them.
function _qualityUsesHq(q) {
  const cells = ((BOOT.ltx || {}).qualities) || [];
  const cell = Array.isArray(cells)
    ? cells.find(c => c && c.key === String(q || ''))
    : cells[String(q || '')];
  return !!(cell && cell.pipeline === 'hq');
}
function setAccel(a) {
  const allowed = !_qualityUsesHq(document.getElementById('quality').value) && currentMode !== 'extend' && currentMode !== 'keyframe';
  const v = allowed ? a : 'off';
  document.getElementById('accel').value = v;
  document.querySelectorAll('#accelGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.accel === v));
  updateCustomizeSummary();
  updateDerived();
}
function temporalModeAllowed() {
  const q = document.getElementById('quality').value;
  const mode = document.getElementById('mode').value;
  return !_qualityUsesHq(q) && currentMode !== 'extend' && currentMode !== 'keyframe' && (mode === 't2v' || mode === 'i2v');
}
// ---- ONE SHOT: its own tab and module since 2026-09-08 — webapp/js/oneshot.js ---

function setTemporalMode(t) {
  const allowed = temporalModeAllowed();
  const v = (allowed && (t === 'fps12_interp24' || t === 'windows')) ? t : 'native';
  document.getElementById('temporal_mode').value = v;
  document.querySelectorAll('#temporalGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.temporal === v));
  // The per-window prompts belong to ONE of the three answers.
  const row = document.getElementById('windowsRow');
  if (row) row.hidden = (v !== 'windows');
  if (v === 'windows') windowPromptsInput();
  updateCustomizeSummary();
  updateDerived();
}

// VA-18: one LABELLED, TIMED box per LATER window, a JSON array on the wire
// (same shape the H3 chain posts) whose index 0 is ALWAYS blank — window 1
// is always the main prompt above (ltx_windows.window_prompts' own
// contract), and there is now no UI control that can override it. This
// replaces a free-text textarea whose placeholder read "(first window uses
// the prompt above)⏎he turns to the window…" — the first REAL line typed
// there became index 0 and silently replaced the main prompt.
//
// Real per-window time ranges come from /ltx/windows_plan
// (ltx_windows.plan_windows, a pure function — same numbers the engine's
// own chain will use), not a client-side re-derivation that could drift
// from the server's rounding.
let _windowsPlanCache = { frames: null, plan: null };
let _windowsPlanFetching = null;
function windowPromptsInput() {
  const out = document.getElementById('window_prompts');
  const wrap = document.getElementById('windowsDynamicSlots');
  if (!out || !wrap) return;
  // Only fetch/render while Windows mode is actually selected — this runs
  // on every updateDerived() call (every frames/duration/width/height
  // edit, in every mode), so a mode guard keeps it from hitting the
  // endpoint on unrelated keystrokes.
  const tmode = (document.getElementById('temporal_mode') || {}).value;
  if (tmode !== 'windows') return;
  const f = parseInt(document.getElementById('frames')?.value || '121', 10) || 121;
  if (_windowsPlanCache.frames === f && _windowsPlanCache.plan) {
    _renderWindowsSlotsFromPlan(_windowsPlanCache.plan);
    return;
  }
  if (_windowsPlanFetching === f) return;   // already in flight for this length
  _windowsPlanFetching = f;
  fetch('/ltx/windows_plan?frames=' + f).then(r => r.json()).then(plan => {
    if (_windowsPlanFetching === f) _windowsPlanFetching = null;
    if (!plan || !plan.ok) return;
    // LIPSYNC-9: a plan for a length the form no longer shows is stale — two
    // quick length changes can answer out of order, and the older, shorter
    // plan used to redraw the boxes (dropping later windows' typed text) and
    // take the cache slot from the current one.
    const nowFrames = parseInt(document.getElementById('frames')?.value || '121', 10) || 121;
    const nowMode = (document.getElementById('temporal_mode') || {}).value;
    if (nowFrames !== f || nowMode !== 'windows') return;
    _windowsPlanCache = { frames: f, plan };
    _renderWindowsSlotsFromPlan(plan);
  }).catch(() => { if (_windowsPlanFetching === f) _windowsPlanFetching = null; });
}
function _fmtMinSec(sec) {
  sec = Math.max(0, Number(sec) || 0);
  const m = Math.floor(sec / 60), s = Math.floor(sec % 60);
  return m + ':' + String(s).padStart(2, '0');
}
function _renderWindowsSlotsFromPlan(plan) {
  const wrap = document.getElementById('windowsDynamicSlots');
  const out = document.getElementById('window_prompts');
  const hint = document.getElementById('windowsHint');
  if (!wrap || !out) return;
  const n = plan.count || 1;
  if (hint) {
    hint.textContent = n <= 1
      ? 'this length fits one window — pick a longer clip'
      : n + ' windows · window 1 is the prompt above · blank holds the last moment';
  }
  if (n <= 1) {
    wrap.innerHTML = '<div class="hint">This length renders in one pass — no per-window prompts needed.</div>';
    out.value = '';
    return;
  }
  // Preserve already-typed text for a window index that survives a
  // duration change (20s -> 15s keeps windows 2-3's lines) — UNLESS a Load
  // Params restore is pending, which wins (it is a deliberate reopen of a
  // specific clip's exact recipe, not an in-progress edit).
  const previous = {};
  wrap.querySelectorAll('textarea[data-window-index]').forEach(t => {
    previous[t.dataset.windowIndex] = t.value;
  });
  const restore = (typeof window !== 'undefined') ? window._pendingWindowPromptsRestore : null;
  if (typeof window !== 'undefined') window._pendingWindowPromptsRestore = null;   // consume once
  const rows = (plan.windows || []).slice(1);   // window 0 has no box — it's the main prompt
  wrap.innerHTML = rows.map(w => {
    const label = 'Window ' + (w.index + 1) + ' · ' + _fmtMinSec(w.start_sec) + '–' + _fmtMinSec(w.end_sec);
    return '<div class="mf-cell" style="margin-top:6px">'
      + '<span class="mf-label">' + escapeHtml(label) + '</span>'
      + '<textarea class="sb-textarea" rows="2" data-window-index="' + w.index + '" '
      + 'oninput="_windowsSlotChanged()" placeholder="the next beat, led by the movement — blank holds the previous moment"></textarea>'
      + '</div>';
  }).join('');
  rows.forEach(w => {
    const el = wrap.querySelector('textarea[data-window-index="' + w.index + '"]');
    if (!el) return;
    const fromRestore = Array.isArray(restore) ? restore[w.index] : undefined;
    el.value = (fromRestore !== undefined && fromRestore !== null)
      ? fromRestore : (previous[String(w.index)] || '');
  });
  _windowsSlotChanged();
}
function _windowsSlotChanged() {
  const wrap = document.getElementById('windowsDynamicSlots');
  const out = document.getElementById('window_prompts');
  if (!wrap || !out) return;
  const boxes = Array.from(wrap.querySelectorAll('textarea[data-window-index]'))
    .sort((a, b) => Number(a.dataset.windowIndex) - Number(b.dataset.windowIndex));
  // Index 0 is ALWAYS blank — window 1 is the main prompt, by construction.
  const lines = [''].concat(boxes.map(b => b.value.trim()));
  while (lines.length && !lines[lines.length - 1]) lines.pop();
  out.value = lines.length ? JSON.stringify(lines) : '';
}
function setUpscale(u) {
  const v = ['off', 'fit_720p', 'x2'].includes(u) ? u : 'off';
  document.getElementById('upscale').value = v;
  document.querySelectorAll('#upscaleGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.upscale === v));
  // Show / hide the Method pill row — only relevant when an upscale is
  // actually being applied. When toggled to "off", revert method to Fast
  // so a later toggle back to fit_720p starts from the safe default.
  const methodRow = document.getElementById('upscaleMethodRow');
  if (methodRow) methodRow.style.display = (v === 'off' || !PIPERSR_UPSCALE_ENABLED) ? 'none' : '';
  if (v === 'off' || !PIPERSR_UPSCALE_ENABLED) setUpscaleMethod('lanczos');
  updateCustomizeSummary();
  updateDerived();
}
function setUpscaleMethod(m) {
  if (m === 'model') m = 'pipersr'; // legacy sidecars from the retired LTX Sharp path
  const v = (PIPERSR_UPSCALE_ENABLED && m === 'pipersr') ? 'pipersr' : 'lanczos';
  document.getElementById('upscale_method').value = v;
  document.querySelectorAll('#upscaleMethodGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.method === v));
  updateCustomizeSummary();
  updateDerived();
}
function updateAccelAvailability() {
  const allowed = !_qualityUsesHq(document.getElementById('quality').value) && currentMode !== 'extend' && currentMode !== 'keyframe';
  document.querySelectorAll('#accelGroup .pill-btn').forEach(b => {
    const disabled = !allowed && b.dataset.accel !== 'off';
    b.classList.toggle('disabled', disabled);
  });
  if (!allowed && document.getElementById('accel').value !== 'off') setAccel('off');
}
function updateTemporalAvailability() {
  const allowed = temporalModeAllowed();
  document.querySelectorAll('#temporalGroup .pill-btn').forEach(b => {
    const disabled = !allowed && b.dataset.temporal !== 'native';
    b.classList.toggle('disabled', disabled);
    if (b.dataset.temporal === 'fps12_interp24') {
      b.title = allowed
        ? 'Generate at 12fps, then interpolate to a normal 24fps export.'
        : 'Available for Q4 Text/Image renders. Off for High, FFLF, Extend, and external-audio I2V.';
    }
  });
  if (!allowed && document.getElementById('temporal_mode').value !== 'native') setTemporalMode('native');
}
function setAspect(a) {
  if (!ASPECTS[a]) return;
  document.getElementById('aspect').value = a;
  document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.classList.toggle('active', b.dataset.aspect === a));
  applyAspect(a);
  // Same guard as setQuality: the H3 tier pins the canvas, so an orientation
  // change (the row is hidden under H3, but boot and Load Params still call
  // this) must not leave the quick-settings advertising dims the render won't
  // use.
  if (document.body.dataset.engine === 'h3' && typeof setH3Tier === 'function') {
    try { setH3Tier((document.getElementById('h3_tier') || {}).value); } catch (_) {}
  }
}

// ============ THE THREE DISCLOSURE SUMMARIES ============
// The render form is three collapsed sections now (Shot setup · After the
// render · Advanced), and a section you cannot see into still owes you its
// state — that is the whole price of folding it away. So each summary prints
// what ITS section is hiding, and nothing that belongs to another one:
//   Shot setup       → the shape: quality, length, orientation, speed, steps,
//                      a locked seed, and the live estimate for all of it.
//   After the render → what happens to the file afterwards.
//   Advanced         → only the overrides that are OFF their default; a
//                      default deserves no words, so it prints "defaults".
// updateCustomizeSummary() keeps its name and stays the entry point (23 call
// sites across five modules call it after every state change) and fans out.
function updateCustomizeSummary() {
  updateShotSetupSummary();
  updateFinishSummary();
  updateAdvancedSummary();
  if (typeof updateBatchSummary === 'function') updateBatchSummary();
}
function updateShotSetupSummary() {
  const el = document.getElementById('shotSetupSummary');
  if (!el) return;
  const parts = [];
  if (document.body.dataset.engine === 'h3') {
    let cell = null;
    try { cell = h3CurrentCell(); } catch (_) {}
    if (cell) {
      parts.push(cell.quality_label || 'Hailuo H3');
      if (cell.length_label) parts.push(cell.length_label);
    } else {
      parts.push('Hailuo H3');
    }
    if (((document.getElementById('h3_orientation') || {}).value) === 'portrait') {
      parts.push('9:16');
    }
    let fast = false;
    try { fast = h3TriStepOn(cell); } catch (_) {}
    let flex = false;
    try { flex = !fast && typeof h3FlexOn === 'function' && h3FlexOn(cell); } catch (_) {}
    parts.push(fast ? 'Fast' : (flex ? 'Pika H3 Flex' : 'Best'));
    const st = (document.getElementById('h3_steps') || {}).value || 'auto';
    if (st !== 'auto') parts.push(st + ' steps');
    // Priced by the engine module's own helper, so the line can never
    // disagree with the chips inside the section.
    let eta = '';
    try { eta = h3CellEta(cell); } catch (_) {}
    if (eta) parts.push(eta);
    // H3-08: the closed section named quality/length/speed/eta but never
    // said whether any LoRA was going to influence the render at all —
    // the LoRA panel is its own disclosure, further down, easy to miss.
    // Lane lookup mirrors _serializeLoras()'s _laneOf (loras.js): the
    // active entry itself carries no reliable lane, _knownUserLoras does.
    try {
      const laneOf = (p) => {
        const u = Array.isArray(_knownUserLoras)
          ? _knownUserLoras.find(x => x.path === p) : null;
        return (u && u.lane) || 'ltx';
      };
      const n = (Array.isArray(_activeLoras) ? _activeLoras : [])
        .filter(a => a && laneOf(a.path) === 'h3').length;
      if (n > 0) parts.push(n + (n === 1 ? ' LoRA' : ' LoRAs'));
    } catch (_) {}
  } else if (currentMode === 'keyframe' || currentMode === 'extend') {
    // VA-11 / VA-12: these two modes run a pipeline the Quality strip's
    // selected cell does NOT describe (keyframe is always Q8 two-stage at
    // this Mac's clamp; extend runs its own step count) — read the real,
    // server-computed price instead of ltxCellFor's cell.
    const tier = (typeof BOOT !== 'undefined' && BOOT.tier) || {};
    let card = null, pending = false;
    if (currentMode === 'keyframe') {
      // EST-7: priced for the duration and canvas the form holds, not 5 s.
      const kp = (typeof keyframePriceFor === 'function')
        ? keyframePriceFor() : { card: tier.keyframe_price, pending: false };
      card = kp.card; pending = kp.pending;
      parts.push('Q8 two-stage');
    } else {
      const proOn = (document.getElementById('extend_steps') || {}).value === '30';
      card = proOn ? tier.extend_price_pro : tier.extend_price_draft;
      parts.push(proOn ? 'Q8 Pro' : 'Q8 Draft');
    }
    if (card && pending) {
      parts.push('pricing…');
    } else if (card) {
      parts.push(`${card.width}×${card.height}`);
      parts.push(card.eta);
    } else {
      parts.push('needs more memory than this Mac has');
    }
  } else {
    let cell = null;
    try { cell = ltxCellFor(ltxCurrentQuality(), ltxCurrentLength()); } catch (_) {}
    const q = (document.getElementById('quality') || {}).value || 'standard';
    parts.push((cell && cell.quality_label) || q);
    // A custom duration lights no chip, so the summary says the frames rather
    // than a length that isn't on the axis.
    if (cell && cell.length_label) parts.push(cell.length_label);
    else parts.push(((document.getElementById('frames') || {}).value || '?') + 'f');
    if (q === 'quick') parts.push('10:7');
    else parts.push(((document.getElementById('aspect') || {}).value === 'vertical') ? '9:16' : '16:9');
    // ltxCellEta() already says "· fast draft" when that schedule is armed —
    // which is exactly why the eta comes from it and not from cell.eta: a
    // summary must not advertise the tuned wall clock for a draft render.
    let eta = '';
    try { eta = ltxCellEta(cell); } catch (_) {}
    if (!eta && typeof schedPresetActive === 'function' && schedPresetActive()) eta = 'fast draft';
    if (eta) parts.push(eta);
  }
  const seed = String((document.getElementById('seed') || {}).value || '-1').trim();
  if (seed && seed !== '-1') parts.push('seed ' + seed);
  el.textContent = parts.filter(Boolean).join(' · ');
}
function updateFinishSummary() {
  const el = document.getElementById('finishSummary');
  if (!el) return;
  const parts = [];
  if (document.body.dataset.engine === 'h3') {
    const up = (document.getElementById('h3_upscale') || {}).value || 'off';
    // H3-38: bare "720p export" on a Draft cell (640×384, a 1.875× lanczos
    // stretch) reads as "this shipped at 720p detail" — it didn't, the
    // source canvas decided the detail and this pass only fills the frame.
    // Say the upscale explicitly whenever the source is below the 720p
    // long side; a cell that's already there (High · 720p) keeps the plain
    // label because nothing is being stretched.
    let cell = null;
    try { cell = (typeof h3CurrentCell === 'function') ? h3CurrentCell() : null; } catch (_) {}
    const upscaling720 = cell && Math.max(cell.width, cell.height) < 1280;
    const upscaling1080 = cell && Math.max(cell.width, cell.height) < 1920;
    if (up === 'fit_720p') {
      parts.push(upscaling720
        ? `720p export (upscaled from ${cell.width}×${cell.height}, not added detail)`
        : '720p export');
    }
    else if (up === 'fit_1080p') {
      parts.push(upscaling1080
        ? `1080p export (upscaled from ${cell.width}×${cell.height}, not added detail)`
        : '1080p export');
    }
    else if (up === 'ltx_x2') parts.push('native + Upscale & Face Fix after');
    else parts.push('native export');
  } else {
    const up = (document.getElementById('upscale') || {}).value || 'off';
    const method = (document.getElementById('upscale_method') || {}).value || 'lanczos';
    const tag = (method === 'pipersr' || method === 'model') ? ' sharp' : '';
    if (up === 'fit_720p') parts.push('720p export' + tag);
    else if (up === 'x2') parts.push('2× export' + tag);
    else parts.push('native export');
  }
  if ((document.getElementById('open_when_done') || {}).checked) parts.push('opens when done');
  el.textContent = parts.join(' · ');
}
function updateAdvancedSummary() {
  const el = document.getElementById('customizeSummary');
  if (!el) return;
  // H3 renders on the tier's own geometry and ignores every LTX override in
  // this section (make_job neutralises them server-side), so summarising them
  // would describe a render that isn't happening.
  const adv = document.getElementById('customizeDetails');
  if (document.body.dataset.engine === 'h3') {
    // Every row in here is an LTX override that H3 ignores (make_job
    // neutralises them server-side), so on H3 the section opened onto
    // NOTHING — a row that says "Advanced · LTX only" and rewards a click
    // with an empty box is exactly the clutter this redesign is removing.
    // Hidden, not removed: the inputs stay in the form, so the posted
    // FormData is byte-identical on both engines.
    el.textContent = 'LTX only';
    if (adv) { adv.open = false; adv.hidden = true; }
    return;
  }
  if (adv) adv.hidden = false;
  const parts = [];
  const q = (document.getElementById('quality') || {}).value || 'standard';
  const w = parseInt((document.getElementById('width') || {}).value || 0, 10);
  const h = parseInt((document.getElementById('height') || {}).value || 0, 10);
  const preset = QUALITY_PRESETS[q] || QUALITY_PRESETS['standard'];
  const vertical = (((document.getElementById('aspect') || {}).value) === 'vertical' && q !== 'quick');
  if (q !== 'quick' && (w !== (vertical ? preset.h : preset.w) || h !== (vertical ? preset.w : preset.h))) {
    parts.push(w + '×' + h + ' custom');
  }
  const accel = (document.getElementById('accel') || {}).value || 'off';
  if (accel === 'boost' || accel === 'turbo') parts.push(accel);
  const stg = parseFloat((document.getElementById('stgScale') || {}).value || '0');
  if (stg > 0) parts.push('STG ' + stg.toFixed(1));
  const temporal = (document.getElementById('temporal_mode') || {}).value || 'native';
  if (temporal === 'fps12_interp24') parts.push('12→24fps long clip');
  else if (temporal === 'windows') parts.push('chained windows');
  if (currentMode === 'i2v'
      && ((document.getElementById('i2vMode') || {}).value === 'i2v_clean_audio')) {
    parts.push('external audio');
  }
  // Inspire changes what the reference DOES; it lives in the composer, but the
  // sentence is worth carrying where the rest of the deviations are listed.
  if (typeof i2vInspireActive === 'function' && i2vInspireActive() && currentMode === 'i2v') {
    parts.push('inspire · new shot from the image');
  }
  el.textContent = parts.length ? parts.join(' · ') : 'defaults';
}
function setExtendMode(m) {
  // Fast = no-CFG path, fits in 64 GB at 1280×704. Quality = upstream
  // defaults, requires headroom. Both are exposed on the form via hidden
  // inputs; this just flips the values + active pill.
  //
  // VA-12: Draft used to send 12 steps here — 50% slower than the
  // validated default (owner ruling 2026-05-21, mlx_ltx_panel.py's extend
  // branch: "steps cut from 12 -> 8"). The server has defaulted to 8 for a
  // while; this client value was silently overriding it back up to 12 on
  // every Draft render.
  const steps = m === 'quality' ? 30 : 8;
  const cfg   = m === 'quality' ? 3.0 : 1.0;
  document.getElementById('extend_steps').value = String(steps);
  document.getElementById('extend_cfg').value   = String(cfg);
  document.querySelectorAll('#extendModeGroup .pill-btn').forEach(b =>
    b.classList.toggle('active', b.dataset.extendMode === m));
  if (typeof updateDerivedForClampedMode === 'function') updateDerivedForClampedMode('extend');
  if (typeof updateShotSetupSummary === 'function') { try { updateShotSetupSummary(); } catch (e) {} }
}

// VA-12: rewrite the Extend pill subtitles from the server's real,
// clamped-canvas price instead of the hardcoded "12 steps · ~16 min" /
// "30 steps · ~38 min" HTML — and hide Q8 Pro below the tier where it
// pushes the render into swap (the pill's own hint already warned about
// this; now the pill itself doesn't offer it there).
function applyExtendPillPrices() {
  const tier = (typeof BOOT !== 'undefined' && BOOT.tier) || {};
  const group = document.getElementById('extendModeGroup');
  if (!group) return;
  const draftBtn = group.querySelector('[data-extend-mode="fast"]');
  const proBtn = group.querySelector('[data-extend-mode="quality"]');
  const fill = (btn, card, stepsLabel) => {
    if (!btn || !card) return;
    const spec = btn.querySelector('.ql-spec');
    const tierEl = btn.querySelector('.ql-tier');
    if (spec) spec.textContent = `${stepsLabel} · ${card.width}×${card.height} safe`;
    if (tierEl) tierEl.textContent = `${card.pipeline_note} · ${card.eta}`;
  };
  fill(draftBtn, tier.extend_price_draft, '8 steps');
  fill(proBtn, tier.extend_price_pro, '30 steps');
  // Pro pushes the render past what Comfortable/Compact tiers can hold
  // resident (the hint text already said so) — hide it below "high" rather
  // than let it look like an equally-safe option.
  if (proBtn) {
    const proSafe = tier.key === 'high' || tier.key === 'pro';
    proBtn.hidden = !proSafe;
    if (!proSafe && proBtn.classList.contains('active')) setExtendMode('fast');
  }
}

function updatePromptPlaceholder() {
  const prompt = document.getElementById('prompt');
  if (!prompt) return;
  const base = 'Describe the scene AND the sound — e.g. wizard in a forest clearing, fireflies spiraling up · low whispered chant, ember crackle, distant owl. Audio is generated jointly with video; without sound cues the model outputs near-silent ambient.';
  const keyframeTwo = 'Describe the full first-to-last transition in one prompt. Include motion, camera, mood, and audio cues; the start/end images anchor the visual endpoints.';
  const keyframeMulti = `One prompt controls the whole ${window._kfMode}-keyframe shot; the Beat at(s) controls define segment timing. Write one continuous action with the beats described in order, plus audio cues.`;
  if (currentMode === 'keyframe') {
    prompt.placeholder = window._kfMode >= 3 ? keyframeMulti : keyframeTwo;
  } else if (currentMode === 'i2v') {
    prompt.placeholder = 'Describe how the reference image should move, plus sound cues. The image anchors frame 0; the prompt directs the full clip.';
  } else if (currentMode === 'ingredients') {
    prompt.placeholder = "Describe WHAT'S in the reference sheet — each character, prop, and the location. e.g. a friendly cartoon hedgehog with rounded chestnut fur; a green coiled garden hose; the bright interior of a 'Greenfield' garden store. (The Action field above describes the shot itself.)";
  } else if (currentMode === 'v2a') {
    prompt.placeholder = 'Describe the sound this clip should have — e.g. a busy kitchen, a pan sizzling, a knife on a board, two people murmuring off-screen. The picture stays exactly as it is.';
  } else if (currentMode === 'extend' && document.body.dataset.engine === 'h3') {
    prompt.placeholder = 'What happens next — the action, the camera, and the sound. Hailuo H3 carries on from the last 17 frames and their sound.';
  } else if (currentMode === 'control') {
    prompt.placeholder = "Describe the NEW subject/scene to paint onto the control clip's motion and structure — plus sound cues. e.g. a red origami crane unfolding on a black table · soft paper rustle. The control video drives the composition; this prompt swaps what's in it.";
  } else {
    prompt.placeholder = base;
  }
}

// Mode chip click — keyframe has two visible chips backed by one backend
// mode. The click handler chooses the 2- or 3-frame UI after setMode()
// restores the shared keyframe screen.
document.querySelectorAll('#modeGroup .pill-btn').forEach(b => b.onclick = () => {
  // Lip-sync (VA-01) isn't a #genForm mode — it's a jump to the Audio tab's
  // audio-driven form, which submits through its own path
  // (audioStudioGenerate). Keep it out of setMode()'s dispatch entirely.
  if (b.dataset.lipsync) { openLipSyncEntry(); return; }
  setMode(b.dataset.mode);
  if (b.dataset.mode === 'keyframe') {
    const def = b.dataset.kfDefault || '2';
    const fallback = parseInt(document.getElementById('keyframe_count')?.value || '3', 10);
    setKeyframeMode(def === 'multi' ? fallback : parseInt(def, 10));
  }
});
// Remix sub-tool clicks set the REAL backend mode (ingredients/control/restore);
// setMode keeps the parent Remix pill lit + this sub-pill active + the section
// shown. Wired here alongside the #modeGroup handler so both rows behave alike.
document.querySelectorAll('#remixSubGroup .pill-btn').forEach(b => b.onclick = () => {
  // Ingredients needs the 2.3 generation: its IC-LoRA is 2.3-trained and no
  // 2.5 one exists, so on 2.5 the references are silently ignored and the
  // clip costs full two-stage time (owner-reproduced 2026-08-15). Say that
  // instead of letting someone spend 11 GPU-minutes finding out. The server
  // refuses too — this is the polite half.
  if (b.dataset.remix === 'ingredients'
      && (BOOT.ltx || {}).ingredients_available === false) {
    // 2.3 is installed alongside: the chip is the switch. One click pins LTX-2.3
    // (the panel restarts); switching back is the LTX generation row in the
    // engine menu. Default stays 2.5 - nothing changes until this is clicked.
    const g23 = _ltx23Switchable();
    // No typeof guard: engines.js publishes switchLtxGeneration, and an
    // unguarded reference is what lets lint_webapp's no-undef catch it the
    // day it stops being published (the guard hid exactly that).
    if (g23) {
      switchLtxGeneration(g23.id);
      return;
    }
    if (typeof phosToast === 'function') {
      phosToast('Ingredients needs the LTX-2.3 generation — its reference '
        + 'adapter has no 2.5 release yet, so on 2.5 your references would '
        + 'be ignored. For reference-guided work here, use Image mode with '
        + 'Inspire.', { kind: 'danger' });
    }
    return;
  }
  setMode(b.dataset.remix);
});
// The installed, non-active LTX-2.3 generation the server offers, or null. The
// server only lists a generation whose base pack is complete on disk, so a
// non-null answer means a click can actually land on something that renders.
function _ltx23Switchable() {
  // BOOT.engines, NOT globalThis.ENGINES: this runs while characters.js loads
  // and engines.js (which assigns ENGINES from BOOT.engines) loads AFTER it, so
  // ENGINES is still undefined here and the chip would never see 2.3.
  const ltx = (BOOT.engines || []).find(e => e.id === 'ltx');
  const gens = (ltx && ltx.generations) || [];
  return gens.find(g => g.id === 'ltx23' && !g.active) || null;
}
// Paint the Ingredients chip as unavailable on a generation that cannot
// serve it, so the state is visible before the click.
(function _markIngredientsAvailability() {
  const apply = () => {
    if ((BOOT.ltx || {}).ingredients_available !== false) return;
    const chip = document.querySelector('#remixSubGroup [data-remix="ingredients"]');
    if (!chip) return;
    // `.disabled` carries pointer-events:none, which also swallows the click
    // and the tooltip. A chip that can switch to 2.3 must stay clickable, so it
    // gets its own muted-but-live class instead.
    chip.classList.add(_ltx23Switchable() ? 'needs-switch' : 'disabled');
    chip.title = 'Needs LTX-2.3 — the 2.5 reference adapter is not published '
               + 'yet. Use Image mode with Inspire for reference-guided work '
               + 'on 2.5, or install the 2.3 pack from the Train tab. '
               + '(The same two routes the server names if you get here.)';
    if (!_ltx23Switchable()) chip.setAttribute('aria-disabled', 'true');
    const sub = chip.querySelector('.mc-sub');
    if (sub) sub.textContent = 'needs LTX-2.3';
    if (_ltx23Switchable()) {
      // Switchable: say what the click does, and keep it off the greyed look.
      chip.title = 'Ingredients needs LTX-2.3. Click to switch to LTX-2.3 - the '
                 + 'panel restarts, and you can switch back to 2.5 from the '
                 + 'LTX generation row in the engine menu.';
      if (sub) sub.textContent = 'click to switch to LTX-2.3';
    }
  };
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', apply);
  } else { apply(); }
})();
document.querySelectorAll('#qualityGroup .pill-btn').forEach(b => b.onclick = () => {
  // Disabled-but-actionable: the High pill becomes a "click to install Q8"
  // CTA when Q8 is missing. Routes to the Models modal so the user lands
  // on the download button with full context (size, current state, etc.).
  if (b.classList.contains('disabled')) {
    if (b.classList.contains('needs-install') && typeof openModelsModal === 'function') {
      openModelsModal();
    }
    return;
  }
  setQuality(b.dataset.quality);
});
document.querySelectorAll('#accelGroup .pill-btn').forEach(b => b.onclick = () => { if (!b.classList.contains('disabled')) setAccel(b.dataset.accel); });
document.querySelectorAll('#temporalGroup .pill-btn').forEach(b => b.onclick = () => { if (!b.classList.contains('disabled')) setTemporalMode(b.dataset.temporal); });
document.querySelectorAll('#upscaleGroup .pill-btn').forEach(b => b.onclick = () => { if (!b.classList.contains('disabled')) setUpscale(b.dataset.upscale); });
document.querySelectorAll('#upscaleMethodGroup .pill-btn').forEach(b => b.onclick = () => { if (!b.classList.contains('disabled')) setUpscaleMethod(b.dataset.method); });
document.querySelectorAll('#aspectGroup .pill-btn').forEach(b => b.onclick = () => setAspect(b.dataset.aspect));
document.querySelectorAll('#extendModeGroup .pill-btn').forEach(b => b.onclick = () => setExtendMode(b.dataset.extendMode));


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
Object.assign(globalThis, {
  musicComposeActive, musicInit, updateMusicAvailability, audioModeSet, musicPick, musicFormChanged,
  setA2vEngine, setA2vH3Quality, renderA2vEngineRow, a2vEngineActive,
  musicFormParams, musicGenerate, useTrackInA2V, audioStudioUseAudio, openMusicInstallCard, closeMusicInstallCard,
  musicInstallRender, musicInstallStart, musicInstallStop,
  // Music Studio (cover): the install card's button is generated markup.
  musicCoverInstall, musicCoverSummary, musicCoverInstallCard, musicCfgValue,
  // Music Studio (LoRA): the picker's rows and the install card are generated markup.
  musicLoraPicks, musicLoraRender, musicLoraToggled, musicLoraSummaryText,
  musicLoraInstallCard, musicLoraInstall, musicFetchCard,
  windowPromptsInput,
  // VA-18: _windowsSlotChanged is an inline onclick/oninput target on the
  // generated per-window textareas.
  _windowsSlotChanged,
  audioStudioInit, audioStudioDurationChanged, audioStudioEnhancePrompt, audioStudioGenerate,
  audioConditioningScaleChanged, audioConditioningScaleReset, a2vLoadParams,
  trainRecommendedPreset, trainUpdatePresetButtons, trainUpdatePresetNote, downloadSampleCharacter,
  charactersInit, charactersRenderChips, charactersOpenCompose, charactersBackToGrid,
  charactersHandleAudioUpload, charactersClearAudio, charactersUpdateStrengthDisplay, charactersSyncStrengthControls,
  charactersGenerate, charactersEscapeHtml, charactersEscapeAttr, _restoreCharacterStrengths,
  charactersLoadParams, trainInit, trainApplyRamGate, trainGuidanceDismiss, trainCheckPreflight,
  trainSuggestTrigger, trainClearAll, trainAutoCaption, trainStart,
  trainVoiceRemove, trainVoiceToggleChanged, trainRefreshLoraList, trainRenderVerdictBanner,
  _trainAnnounceReady,
  setQuality, _qualityUsesHq, setAccel, setTemporalMode,
  setUpscale, setUpscaleMethod, updateAccelAvailability, updateTemporalAvailability,
  setAspect, updateCustomizeSummary, updatePromptPlaceholder,
  updateShotSetupSummary, updateFinishSummary, updateAdvancedSummary,
  setExtendMode, applyExtendPillPrices,
  // inline-handler targets: generated markup resolves these through the
  // global scope (the v4.9.0 regression, PR #69)
  audioStudioClearAudio, charactersPickChip, trainInstall, trainInstallAll, trainRemoveImage,
  trainUseInVideo,
  // VA-01 / VA-04 / VA-20: openLipSyncEntry is an inline onclick target from
  // both the mode-bar chip's delegate (same file, would work unpublished)
  // and the mux-warning link in index.html (a different module's markup —
  // needs the global). audioStudioRenderLoraNote's own onclick lives in the
  // string it builds, so it needs the global too.
  openLipSyncEntry, audioStudioRenderLoraNote,
  audioStudioPromptCheck, audioStudioPromptApplyFix, audioStudioMouthCheck,
  audioStudioSplitIntoClips, audioStudioSplitCancel,
  // Vocal separation: the checkbox's onchange and the generated Install /
  // Try again buttons resolve through the global scope.
  audioStudioRenderSeparator, audioStudioInstallSeparator, audioStudioRefreshSeparator,
});
