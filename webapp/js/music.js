// webapp/js/music.js — Music Studio: the song as a thing you come back to.
//
// The composer (Write / Cover / Score) lives in characters.js with the rest
// of the Audio tab; this module owns what happens AFTER a song exists — the
// Song card under the player: its sheet music, its words, where it came from,
// and the four ways to get another song out of it. It also owns the two
// helpers the composer borrows: Gemma writing lyrics, and section-tag chips.
//
// Everything a handler in the markup calls is published at the bottom.

let SONG = null;            // the /music/score payload for the selected song
let songReq = 0;            // stale-response guard for the fetch below
let songShown = '';         // the path the card is showing (or loading)

function _el(id) { return document.getElementById(id); }
function _esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function _say(msg) { const el = _el('songStatus'); if (el) el.textContent = msg || ''; }

// ---- the Song card --------------------------------------------------------

// Called by selectOutput() for every selection; only a song fills it in.
async function songCardRender(o) {
  const card = _el('songCard');
  if (!card) return;
  const isSong = o && o.kind === 'audio' && o.engine === 'music';
  card.hidden = !isSong;
  // THE PREVIOUS SONG IS GONE THE MOMENT ANOTHER IS PICKED (M6-02). SONG
  // used to keep the last song until the new one's score arrived, so an
  // action pressed in that window — or after a failed read — used the OLD
  // song's score or recording under the NEW song's title. Leaving the card
  // also retires any read still on its way.
  SONG = null;
  if (!isSong) { songShown = ''; ++songReq; return; }
  songShown = o.path;
  const m = o.music || {};
  // The bar is the player, the card is the reader. Looking at a song never
  // interrupts the one that is playing; the bar only takes a song when it has
  // none yet (so the transport is there to press), and never starts it.
  if (!_barSong) musicBarShow(o);
  _el('songTitle').textContent = m.title || _songNameFromFile(o.name);
  _el('songStyle').textContent = m.style || '';
  _el('songBadges').innerHTML = _badges(m);
  _say('');
  musicScoreEditToggle(false);
  // Facts we already have from the gallery row; the score itself is fetched.
  const req = ++songReq;
  try {
    const r = await fetch(`/music/score?path=${encodeURIComponent(o.path)}`);
    const data = await r.json();
    if (req !== songReq) return;               // the user moved on
    if (!r.ok || data.error) throw new Error(data.error || 'Could not read the song');
    SONG = data;
    _el('songTitle').textContent = data.title || _songNameFromFile(o.name);
    _el('songStyle').textContent = data.style || '';
    _renderLineage(data);
    _renderScore(data.abc);
    _renderLyrics(data.lyrics);
    // A song with nothing saved cannot be performed again: say so on the
    // buttons rather than after a click.
    const noArt = !data.has_artifacts;
    for (const b of _el('songActions').querySelectorAll('button')) {
      const kind = (b.getAttribute('onclick') || '').match(/musicVariation\('(\w+)'\)/);
      if (kind) { b.disabled = noArt; b.title = noArt ? 'This song kept no artifacts (made before v4.16). Compose it again to get takes.' : b.title; }
    }
  } catch (e) {
    if (req === songReq) _say(e.message || String(e));
  }
}

function _songNameFromFile(name) {
  return String(name || '').replace(/\.wav$/i, '').replace(/^music_\d{8}_\d{6}_/, '').replace(/_/g, ' ');
}

// The gallery block carries `loras: [{name, strength}]`; /music/score answers
// with the runner's full `lora` block. Read both shapes, show one badge.
function _loraList(m) {
  if (Array.isArray(m.loras)) return m.loras;
  const adapters = ((m.lora || {}).adapters) || [];
  return adapters.map(a => ({name: a.file, strength: a.strength}));
}
function _loraLabel(file) {
  return String(file || 'lora').replace(/\.bf16\.safetensors$|\.safetensors$/i, '')
    .replace(/_/g, ' ').slice(0, 28);
}

function _badges(m) {
  const out = [];
  if (m.variation) out.push(`<span class="song-badge">${_esc({take: 'new take', sound: 're-rolled', restyle: 'restyled'}[m.variation] || m.variation)}</span>`);
  if (m.cover) out.push('<span class="song-badge">cover</span>');
  if (m.instrumental) out.push(`<span class="song-badge muted"${
    m.instrumental_recipe === 'mothersuperior-ar-lora'
      ? ' title="Made with Mothersuperior\'s instrumental AR adapter at strength 1"' : ''
    }>instrumental${m.instrumental_recipe === 'mothersuperior-ar-lora' ? ' · lora' : ''}</span>`);
  // Which adapters made this song. The sidecar keeps their hashes; the badge
  // keeps the two things a listener compares by — which one, and how hard.
  for (const l of (_loraList(m))) {
    out.push(`<span class="song-badge" title="LoRA ${_esc(l.name || '')}">${
      _esc(_loraLabel(l.name))}${l.strength != null ? ` ${Number(l.strength).toFixed(2)}` : ''}</span>`);
  }
  if (m.mode && m.mode !== 'off') out.push(`<span class="song-badge muted">${_esc(m.mode === 'full' ? 'melody + chords' : 'melody')}</span>`);
  if (m.ended_naturally === false) out.push('<span class="song-badge muted" title="The song hit Max length before it ended on its own">cut at max length</span>');
  return out.join('');
}

function _renderLineage(d) {
  const el = _el('songLineage');
  if (!d.parent) { el.innerHTML = ''; return; }
  const parentName = String(d.parent).split('/').pop();
  const how = {take: 'a new take of', sound: 'a re-recording of', restyle: 'a restyle of'}[d.variation] || 'made from';
  // The handler's argument is JSON inside an HTML attribute, so it is
  // ESCAPED for the attribute: the bare JSON's opening quote used to close
  // `onclick="` and every parent link was a syntax error (M6-10).
  const arg = _esc(JSON.stringify(String(d.parent)));
  el.innerHTML = `${how} <a onclick="musicOpenSong(${arg})">${_esc(parentName)}</a>`;
}

function _renderScore(abc) {
  const host = _el('songScore');
  const sum = _el('songScoreSum');
  const editBtn = _el('songScoreEditBtn');
  host.innerHTML = '';
  if (!abc || !abc.trim()) {
    host.innerHTML = '<div class="song-score-empty">This song was written with No score — there is nothing to draw. Compose with Melody + chords or Melody only to get sheet music.</div>';
    sum.textContent = 'none';
    if (editBtn) editBtn.disabled = true;
    return;
  }
  if (editBtn) editBtn.disabled = false;
  const facts = _abcFacts(abc);
  sum.textContent = facts;
  _drawAbc(host, abc);
}

// abcjs renders into a host element; sizing is responsive to the card.
function _drawAbc(host, abc) {
  if (!window.ABCJS) { host.innerHTML = '<div class="song-score-empty">Score renderer not loaded.</div>'; return; }
  try {
    window.ABCJS.renderAbc(host, abc, {
      responsive: 'resize', add_classes: true, staffwidth: 640,
      paddingtop: 4, paddingbottom: 4, paddingleft: 4, paddingright: 4,
    });
  } catch (e) {
    host.innerHTML = `<div class="song-score-empty">Could not draw this score: ${_esc(e.message || e)}</div>`;
  }
}

// Key, meter and tempo read straight off the ABC header — the three facts a
// musician asks first.
function _abcFacts(abc) {
  const get = (k) => { const m = abc.match(new RegExp(`^${k}:\\s*(.+)$`, 'm')); return m ? m[1].trim() : ''; };
  const key = get('K'), meter = get('M'), q = get('Q');
  const bpm = q ? (q.match(/=(\d+)/) || [])[1] : '';
  return [key && `${key}`, meter && `${meter}`, bpm && `${bpm} BPM`].filter(Boolean).join(' · ');
}

function _renderLyrics(lyrics) {
  const el = _el('songLyrics');
  const sum = _el('songLyricsSum');
  if (!lyrics || !lyrics.trim()) { el.textContent = '(instrumental)'; sum.textContent = 'instrumental'; return; }
  el.innerHTML = _esc(lyrics).replace(/^(\[[^\]]+\])$/gm, '<span class="tag">$1</span>');
  const tags = (lyrics.match(/^\[[^\]]+\]$/gm) || []).length;
  sum.textContent = tags ? `${tags} sections` : '';
}

// ---- the four ways to get another song ------------------------------------

async function musicVariation(kind, path) {
  const target = path || (SONG && SONG.path);
  if (!target) return;
  const seed = _el('musicSeed') ? _el('musicSeed').value : '-1';
  _say(kind === 'sound' ? 'Queueing a re-recording…' : 'Queueing a new take…');
  if (typeof phosToast === 'function' && path) phosToast(kind === 'sound' ? 'Re-recording queued' : 'New take queued');
  try {
    const fd = new URLSearchParams({path: target, kind, seed});
    const r = await fetch('/music/variation', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Could not queue');
    _say(kind === 'sound' ? 'Re-recording queued — a fraction of a full song.' : 'New take queued.');
    if (typeof poll === 'function') poll();
  } catch (e) { _say(e.message || String(e)); }
}

// "New style on this score": the composer's current style + lyrics go on
// THIS song's score. The form is the instrument; the card is the source.
// THE SONG AN ACTION IS FOR, read for that path and no other. The card's
// SONG is whatever the card last finished reading; an action from a song's
// ⋯ menu names its song and must not borrow another one while the card
// catches up.
async function _songFor(path) {
  if (SONG && SONG.path === path) return SONG;
  try {
    const r = await fetch(`/music/score?path=${encodeURIComponent(path)}`);
    const data = await r.json();
    if (!r.ok || !data || data.error) return null;
    return data;
  } catch (e) { return null; }
}

// The ⋯ menu's verbs that act on a song's own score or recording. Opening
// the card is for the eye; the action waits for THIS song and uses it —
// it used to fire 400 ms later against whatever SONG was by then (M6-02).
async function musicSongAction(path, action) {
  if (typeof selectOutput === 'function') selectOutput(path);
  const song = await _songFor(path);
  if (!song) { _say('Could not read that song — nothing was done.'); return; }
  if (action === 'restyle') return musicRestyleFromSong(song);
  if (action === 'cover') return musicCoverFromSong(song);
  if (action === 'edit') {
    if (songShown !== path) return;           // the user has moved on
    SONG = song;
    musicScoreEditToggle(true);
  }
}

async function musicRestyleFromSong(song) {
  song = (song && song.path) ? song : SONG;
  if (!song) { _say('Still reading this song — try again in a moment.'); return; }
  const style = (_el('musicStyle') || {}).value || '';
  const lyrics = (_el('musicLyrics') || {}).value || '';
  if (!style.trim() && !lyrics.trim()) {
    _say('Put the new style and/or lyrics in the composer first, then click again.');
    if (typeof audioModeSet === 'function') audioModeSet('compose');
    return;
  }
  _say('Queueing a restyle…');
  try {
    // Max length is the composer's like the style is: the slider on screen,
    // not the limit the parent happened to be made with (M6-01).
    const fd = new URLSearchParams({path: song.path, kind: 'restyle', style, lyrics,
      seed: (_el('musicSeed') || {}).value || '-1', quality: (_el('musicQuality') || {}).value || 'final',
      title: (_el('musicTitle') || {}).value || '',
      max_seconds: (_el('musicMaxSeconds') || {}).value || ''});
    const r = await fetch('/music/variation', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Could not queue');
    _say('Restyle queued: this score, your new words and style.');
    if (typeof poll === 'function') poll();
  } catch (e) { _say(e.message || String(e)); }
}

function musicCoverFromSong(song) {
  song = (song && song.path) ? song : SONG;
  if (!song) { _say('Still reading this song — try again in a moment.'); return; }
  const src = _el('musicSourceAudio');
  if (src) src.value = song.path;
  const task = _el('musicTask');
  if (task) musicTaskSet('cover');
  if (typeof audioModeSet === 'function') audioModeSet('compose');
  if (typeof musicFormChanged === 'function') musicFormChanged();
  _say('Loaded as the source in Cover a song.');
}

function musicOpenSong(path) {
  if (typeof selectOutput === 'function') selectOutput(path);
}

// ---- the score editor ------------------------------------------------------

function musicScoreEditToggle(on) {
  const ed = _el('songScoreEditor');
  if (!ed) return;
  ed.hidden = !on;
  if (on && SONG) {
    _el('songScoreText').value = SONG.abc || '';
    _el('songScoreText').focus();
  }
}

function musicScorePreview() {
  const abc = _el('songScoreText').value;
  _drawAbc(_el('songScore'), abc);
  _el('songScoreSum').textContent = _abcFacts(abc) + ' · edited';
}

async function musicRenderEditedScore() {
  if (!SONG) return;
  const abc = _el('songScoreText').value;
  if (!/^K:/m.test(abc)) { _say('That is not a complete ABC score (no K: key line).'); return; }
  _say('Queueing the edited score…');
  try {
    const fd = new URLSearchParams({path: SONG.path, abc,
      style: (_el('musicStyle') || {}).value || '', lyrics: (_el('musicLyrics') || {}).value || '',
      seed: (_el('musicSeed') || {}).value || '-1', quality: (_el('musicQuality') || {}).value || 'final',
      title: (_el('musicTitle') || {}).value || '',
      max_seconds: (_el('musicMaxSeconds') || {}).value || ''});
    const r = await fetch('/music/score/render', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Could not queue');
    _say('Edited score queued. The new song lands beside this one.');
    musicScoreEditToggle(false);
    if (typeof poll === 'function') poll();
  } catch (e) { _say(e.message || String(e)); }
}

async function musicScoreCopy() {
  if (!SONG || !SONG.abc) return;
  try { await navigator.clipboard.writeText(SONG.abc); _say('ABC copied.'); }
  catch (_) { _say('Could not copy — select the text in Edit the score instead.'); }
}

// ---- composer helpers borrowed by characters.js --------------------------

function musicTaskSet(task) {
  const hidden = _el('musicTask');
  if (hidden) hidden.value = task;
  document.querySelectorAll('#musicTaskPills .seg-btn').forEach(b => {
    const on = b.dataset.value === task;
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
  });
  const cover = _el('musicCoverDetails');
  if (cover) cover.open = task !== 'write';
  // Score only: the composer's words and style are not used, say so by
  // dimming them rather than hiding a form the user may be mid-way through.
  for (const id of ['musicLyrics', 'musicStyle', 'musicConcept', 'musicTitle']) {
    const el = _el(id); if (el) el.disabled = task === 'score';
  }
  const btn = _el('musicGenBtn');
  if (btn) btn.textContent = task === 'score' ? 'Get the score' : task === 'cover' ? 'Cover it' : 'Compose';
}
function musicTaskPick(button) { musicTaskSet(button.dataset.value); if (typeof musicFormChanged === 'function') musicFormChanged(); }

// ---- Simple mode -----------------------------------------------------------
//
// Suno's best idea is that the form is optional: one box, and the machine
// fills in the brief. Ours differs in one place on purpose — it does not
// render. A song is minutes of this Mac's GPU, so what Gemma writes lands in
// the Custom fields and the person reads it before pressing Compose.
//
// Custom stays the truth. Simple's two knobs (Instrumental, Max length) are
// the same two decisions Custom already owns, mirrored rather than duplicated,
// so there is never a second value to reconcile at queue time.

let musicMode = 'custom';

function musicSimpleActive() { return musicMode === 'simple'; }

function musicSimpleMirror(dir) {
  const secs = _el('musicMaxSeconds'), inst = _el('musicInstrumental');
  const mine = _el('musicSimpleSeconds'), myInst = _el('musicSimpleInstrumental');
  if (!secs || !mine) return;
  if (dir === 'out') { secs.value = mine.value; inst.checked = myInst.checked; }
  else { mine.value = secs.value; myInst.checked = inst.checked; }
  const val = _el('musicSimpleSecondsVal');
  if (val) val.textContent = _mmss(Number(mine.value));
}

function musicSimpleChanged() {
  musicSimpleMirror('out');
  if (typeof musicFormChanged === 'function') musicFormChanged();
  musicSimpleMirror('in');
}

// Panes and the segment only — no form callbacks, so this is safe at boot
// before the engine probes have landed.
function musicModeApply() {
  const simple = musicMode === 'simple';
  const sp = _el('musicSimplePane'), cp = _el('musicCustomPane');
  if (sp) sp.hidden = !simple;
  if (cp) cp.hidden = simple;
  document.querySelectorAll('#musicModeSeg .seg-btn').forEach(b => {
    const on = b.dataset.value === musicMode;
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
  });
  musicSimpleMirror('in');
}

function musicModeSet(mode) {
  musicMode = mode === 'simple' ? 'simple' : 'custom';
  try { localStorage.setItem('phos_music_mode', musicMode); } catch (_) {}
  musicModeApply();
  const notice = _el('musicSimpleNotice');
  if (notice && musicMode === 'simple') notice.hidden = true;
  const btn = _el('musicGenBtn');
  if (musicMode === 'simple') { if (btn) btn.textContent = 'Compose'; }
  else musicTaskSet((_el('musicTask') || {}).value || 'write');
  if (typeof musicFormChanged === 'function') musicFormChanged();
}
function musicModePick(button) { musicModeSet(button.dataset.value); }

function musicModeInit() {
  let saved = null;
  try { saved = localStorage.getItem('phos_music_mode'); } catch (_) {}
  musicMode = saved === 'simple' ? 'simple' : 'custom';
  musicModeApply();
}

// Compose in Simple mode: write the brief, show it, stop. The button ends up
// focused on the Custom form so the next press is the one that renders.
async function musicSimpleCompose() {
  const box = _el('musicSimpleDescription');
  const status = _el('musicStatus');
  const notice = _el('musicSimpleNotice');
  const desc = (box || {}).value || '';
  if (!desc.trim()) {
    if (status) status.textContent = 'Say what the song should be first.';
    if (box) box.focus();
    return;
  }
  const btn = _el('musicGenBtn');
  if (notice) notice.hidden = true;
  if (btn) { btn.disabled = true; btn.textContent = 'Writing…'; }
  if (status) status.textContent = 'Gemma is writing the style and the words (the first time loads the model, ~15 s)…';
  try {
    musicSimpleMirror('out');
    const fd = new URLSearchParams({
      description: desc,
      instrumental: (_el('musicInstrumental') || {}).checked ? 'on' : 'off',
      seconds: (_el('musicMaxSeconds') || {}).value || '150',
    });
    const r = await fetch('/music/simple', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Nothing came back');
    if (_el('musicTitle')) _el('musicTitle').value = data.title || '';
    if (_el('musicStyle')) _el('musicStyle').value = data.style || '';
    // The description is the concept the per-section Rewrite works from.
    if (_el('musicConcept') && !(_el('musicConcept').value || '').trim()) _el('musicConcept').value = desc;
    musicLyricsSet(data.lyrics || '');
    musicTaskSet('write');
    musicModeSet('custom');
    if (notice) {
      notice.textContent = 'Written from your description — edit anything, then Compose.';
      notice.hidden = false;
    }
    if (status) status.textContent = '';
    if (btn) btn.focus();
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Compose'; }
    if (typeof musicFormChanged === 'function') musicFormChanged();
  }
}

// ---- the lyrics editor -----------------------------------------------------
//
// A song is sections. The old box was one textarea and seven chips that typed
// a tag at the caret, which meant the structure existed only in the user's
// head and in the brackets. Here the structure IS the interface: a stack of
// sections, each with its label, its lines, its line count and a Rewrite that
// asks Gemma for that part alone.
//
// The bracket text stays the truth: #musicLyrics is written on every edit and
// is still the only field musicFormParams, the restyle verb and the score
// editor read. Text mode is the same truth, typed by hand — it re-parses into
// sections on blur, so neither view can drift from the other.

const LYRIC_LABELS = ['Intro', 'Verse', 'Pre-Chorus', 'Chorus', 'Bridge', 'Instrumental', 'Outro'];
let LYRICS = [];              // [{tag: 'Verse', body: 'a line\nanother line'}]
let lyricsView = 'sections';  // 'sections' | 'text'
let lyricsBusy = -1;          // index of the section Gemma is rewriting

// Text → sections. A bracketed line on its own opens a section; everything
// else belongs to the one above it. Words before the first tag are a real
// thing a user can type, so they become a section with no tag rather than
// being thrown away.
function _lyricsParse(text) {
  const out = [];
  let cur = null;
  for (const raw of String(text || '').split('\n')) {
    const tag = raw.trim().match(/^\[([^\]]+)\]$/);
    if (tag) { cur = {tag: tag[1].trim(), body: []}; out.push(cur); continue; }
    if (!cur) { if (!raw.trim()) continue; cur = {tag: '', body: []}; out.push(cur); }
    cur.body.push(raw);
  }
  return out.map(s => ({tag: s.tag, body: s.body.join('\n').replace(/^\n+/, '').replace(/\s+$/, '')}));
}

// Sections → text, in YuE2's format: the tag on its own line, the lines under
// it, a blank line between sections. A tag with nothing under it survives —
// that is how an instrumental passage is written.
function _lyricsText(sections) {
  return sections
    .map(s => (s.tag ? `[${s.tag}]` : '') + (s.body ? (s.tag ? '\n' : '') + s.body : ''))
    .filter(Boolean).join('\n\n');
}

function _lyricsLineCount(body) {
  return String(body || '').split('\n').filter(l => l.trim()).length;
}

// What the section costs, in the unit a lyricist counts in. No words is not
// nothing: it is how an instrumental passage is written.
function _lyricsCountLabel(n) {
  return n ? n + (n === 1 ? ' line' : ' lines') : 'no words';
}

// Every path that changes the sections ends here: the hidden field is the
// contract with the rest of the form, so it is written before anything else
// is told that the lyrics changed.
function _lyricsSync() {
  const ta = _el('musicLyrics');
  if (ta) ta.value = _lyricsText(LYRICS);
  const raw = _el('musicLyricsRaw');
  if (raw && lyricsView !== 'text') raw.value = ta ? ta.value : '';
  if (typeof musicFormChanged === 'function') musicFormChanged();
}

// A textarea that grows with its content: a chorus is four lines and a verse
// is eight, and scrolling inside a four-line box to read eight is the thing
// the old single textarea already did badly.
function _lyricsGrow(ta) {
  if (!ta) return;
  ta.style.height = 'auto';
  // A box inside a hidden pane measures 0 — the editor is built at boot, long
  // before anyone opens the Audio tab — so fall back to counting its rows.
  const h = ta.scrollHeight || String(ta.value || '').split('\n').length * 19 + 14;
  ta.style.height = `${Math.max(h, 30)}px`;
}

function musicLyricsRender() {
  const host = _el('musicLyricsSections');
  if (!host) return;
  if (!LYRICS.length) {
    host.innerHTML = '<div class="lyr-blank">No words yet. Add a section below, or say what ' +
      'the song is about and let Gemma write the whole thing.</div>';
    return;
  }
  host.innerHTML = LYRICS.map((s, i) => {
    const n = _lyricsLineCount(s.body);
    const count = _lyricsCountLabel(n);
    const labels = LYRIC_LABELS.includes(s.tag) || !s.tag ? LYRIC_LABELS : [s.tag, ...LYRIC_LABELS];
    const opts = labels.map(l => `<option value="${_esc(l)}"${l === s.tag ? ' selected' : ''}>${_esc(l)}</option>`).join('');
    const busy = lyricsBusy === i;
    return `<div class="lyr-sec${n ? '' : ' wordless'}">
      <div class="lyr-sec-head">
        <span class="lyr-tag-wrap"><select class="lyr-tag" aria-label="Section" onchange="musicLyricsRetag(${i}, this.value)">${opts}</select></span>
        <span class="lyr-count">${count}</span>
        <span class="lyr-acts">
          <button type="button" class="lyr-btn"${busy ? ' disabled' : ''} onclick="musicLyricsRewrite(${i})"
                  title="Gemma rewrites this section only, in the style you gave">${busy ? 'Writing…' : 'Rewrite'}</button>
          <button type="button" class="lyr-ico"${i === 0 ? ' disabled' : ''} onclick="musicLyricsMove(${i}, -1)" title="Move up" aria-label="Move up">&#8593;</button>
          <button type="button" class="lyr-ico"${i === LYRICS.length - 1 ? ' disabled' : ''} onclick="musicLyricsMove(${i}, 1)" title="Move down" aria-label="Move down">&#8595;</button>
          <button type="button" class="lyr-ico lyr-del" onclick="musicLyricsRemove(${i})" title="Delete this section" aria-label="Delete this section">&#10005;</button>
        </span>
      </div>
      <textarea class="lyr-lines" rows="1" aria-label="${_esc(s.tag || 'Lyrics')} lines"
                oninput="musicLyricsType(${i}, this)">${_esc(s.body)}</textarea>
      <div class="lyr-wordless">Nothing sung here — an instrumental passage. Type to give it words.</div>
    </div>`;
  }).join('');
  host.querySelectorAll('textarea.lyr-lines').forEach(_lyricsGrow);
  // Fresh controls arrive enabled; if the song is instrumental they must not.
  musicLyricsLock(!!(_el('musicLyrics') || {}).disabled);
}

// Typing must not re-render (the caret would jump), so the one thing that
// changes with the words — the line count and the wordless hint — is updated
// in place and everything else is left alone.
function musicLyricsType(i, ta) {
  if (!LYRICS[i]) return;
  LYRICS[i].body = ta.value;
  const sec = ta.closest('.lyr-sec');
  const n = _lyricsLineCount(ta.value);
  if (sec) {
    sec.classList.toggle('wordless', !n);
    const count = sec.querySelector('.lyr-count');
    if (count) count.textContent = _lyricsCountLabel(n);
  }
  _lyricsGrow(ta);
  _lyricsSync();
}

function musicLyricsRetag(i, tag) {
  if (!LYRICS[i]) return;
  LYRICS[i].tag = tag;
  _lyricsSync(); musicLyricsRender();
}

function musicLyricsMove(i, dir) {
  const j = i + dir;
  if (!LYRICS[i] || !LYRICS[j]) return;
  [LYRICS[i], LYRICS[j]] = [LYRICS[j], LYRICS[i]];
  _lyricsSync(); musicLyricsRender();
}

function musicLyricsRemove(i) {
  if (!LYRICS[i]) return;
  LYRICS.splice(i, 1);
  _lyricsSync(); musicLyricsRender();
}

function musicLyricsAdd(tag) {
  LYRICS.push({tag: tag || 'Verse', body: ''});
  _lyricsSync(); musicLyricsRender();
  const boxes = _el('musicLyricsSections').querySelectorAll('textarea.lyr-lines');
  const last = boxes[boxes.length - 1];
  if (last) last.focus();
}

// Whatever set the words — Gemma, Simple mode, a song being loaded — comes
// through here, so there is exactly one way the editor is filled.
function musicLyricsSet(text) {
  LYRICS = _lyricsParse(text);
  _lyricsSync();
  const raw = _el('musicLyricsRaw');
  if (raw) raw.value = _el('musicLyrics') ? _el('musicLyrics').value : String(text || '');
  musicLyricsRender();
}

function musicLyricsViewPick(button) { musicLyricsView(button.dataset.value); }

function musicLyricsView(mode) {
  const want = mode === 'text' ? 'text' : 'sections';
  const wasText = lyricsView === 'text';
  lyricsView = want;
  const ed = _el('musicLyricsEditor'), raw = _el('musicLyricsRaw');
  if (ed) ed.hidden = want === 'text';
  if (raw) {
    raw.hidden = want !== 'text';
    if (want === 'text') raw.value = _el('musicLyrics') ? _el('musicLyrics').value : '';
  }
  // Leaving Text takes the typed brackets with it, otherwise the sections
  // would quietly overwrite what the user just wrote — and it re-parses AFTER
  // the editor is visible again, so the boxes can measure their own height.
  if (wasText && want !== 'text') musicLyricsRawSync();
  document.querySelectorAll('#musicLyricsViewSeg .seg-btn').forEach(b => {
    const on = b.dataset.value === want;
    b.classList.toggle('active', on); b.setAttribute('aria-pressed', String(on));
  });
}

// Text mode's blur: the brackets the user typed become the sections again.
function musicLyricsRawSync() {
  const raw = _el('musicLyricsRaw');
  if (!raw) return;
  LYRICS = _lyricsParse(raw.value);
  _lyricsSync();
  musicLyricsRender();
}

// Instrumental: the words are not used, so the editor says so by going quiet
// rather than disappearing — they are still there when the toggle comes off.
function musicLyricsLock(on) {
  const ed = _el('musicLyricsEditor'), raw = _el('musicLyricsRaw'), add = _el('musicLyricsAdd');
  for (const el of [ed, raw, add]) if (el) el.classList.toggle('lyr-off', !!on);
  if (raw) raw.disabled = !!on;
  if (ed) for (const c of ed.querySelectorAll('textarea, select, button')) c.disabled = !!on;
}

async function musicLyricsRewrite(i) {
  const s = LYRICS[i];
  if (!s || lyricsBusy >= 0) return;
  const status = _el('musicStatus');
  lyricsBusy = i; musicLyricsRender();
  if (status) status.textContent = `Gemma is rewriting the ${s.tag || 'section'}…`;
  try {
    const fd = new URLSearchParams({
      section: s.tag || 'Verse', lines: s.body,
      concept: (_el('musicConcept') || {}).value || '',
      style: (_el('musicStyle') || {}).value || '',
      seconds: (_el('musicMaxSeconds') || {}).value || '150',
      seed: String(Math.floor(Math.random() * 1e6)),   // a rewrite asked twice should differ
    });
    const r = await fetch('/music/lyrics/section', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'No lines came back');
    // The answer belongs to the section object captured above, wherever it
    // has been moved to since — never to "whatever is at index i now".
    if (!LYRICS.includes(s)) throw new Error('That section was removed while Gemma was writing — nothing changed.');
    s.body = data.lines;
    if (status) status.textContent = `${s.tag || 'Section'} rewritten in ${Math.round(data.elapsed_sec || 0)} s.`;
    _lyricsSync();
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
  } finally {
    lyricsBusy = -1;
    musicLyricsRender();
  }
}

function musicLyricsInit() {
  if (!_el('musicLyricsSections')) return;
  musicLyricsSet((_el('musicLyrics') || {}).value || '');
  musicLyricsView('sections');
}

async function musicWriteLyrics() {
  const concept = (_el('musicConcept') || {}).value || '';
  const btn = _el('musicWriteLyricsBtn');
  const status = _el('musicStatus');
  if (!concept.trim()) { if (status) status.textContent = 'Say what the song is about first.'; _el('musicConcept').focus(); return; }
  if (btn) { btn.disabled = true; btn.textContent = 'Writing…'; }
  if (status) status.textContent = 'Gemma is writing the lyrics (the first time loads the model, ~15 s)…';
  try {
    const fd = new URLSearchParams({concept, style: (_el('musicStyle') || {}).value || '',
      seconds: (_el('musicMaxSeconds') || {}).value || '150'});
    const r = await fetch('/music/lyrics', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'No lyrics came back');
    musicLyricsSet(data.lyrics);
    if (status) status.textContent = `Lyrics written in ${Math.round(data.elapsed_sec || 0)} s — edit anything, then Compose.`;
  } catch (e) {
    if (status) status.textContent = e.message || String(e);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Write lyrics'; }
  }
}

// "Get the score": transcription only, then show it on the card.
async function musicTranscribe() {
  const src = (_el('musicSourceAudio') || {}).value || '';
  const status = _el('musicStatus');
  if (!src.trim()) { if (status) status.textContent = 'Pick the recording to read.'; return; }
  if (status) status.textContent = 'Queueing the transcription…';
  try {
    const fd = new URLSearchParams({path: src, task: (_el('musicCoverTask') || {}).value || 'melody-full'});
    const r = await fetch('/music/transcribe', {method: 'POST', body: fd});
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || 'Could not queue');
    if (status) status.textContent = 'Listening… the score appears here when it is done.';
    _watchScoreJob(data.id);
    if (typeof poll === 'function') poll();
  } catch (e) { if (status) status.textContent = e.message || String(e); }
}

function _watchScoreJob(id) {
  const status = _el('musicStatus');
  const tick = async () => {
    try {
      const r = await fetch(`/music/score?job=${encodeURIComponent(id)}`);
      const d = await r.json();
      if (d.abc) {
        const card = _el('songCard');
        SONG = {path: d.path, abc: d.abc, title: d.title, style: '', lyrics: '', has_artifacts: false};
        card.hidden = false;
        _el('songTitle').textContent = d.title || 'Score';
        _el('songStyle').textContent = d.cover ? `read from ${String(d.cover.artifacts || '').split('/').pop() ? 'the recording' : 'the recording'} · ${d.cover.task}` : '';
        _el('songBadges').innerHTML = '<span class="song-badge">score</span>';
        _el('songLineage').innerHTML = '';
        for (const b of _el('songActions').querySelectorAll('button')) b.disabled = true;
        _renderScore(d.abc);
        _renderLyrics('');
        if (status) status.textContent = 'Score ready — it is on the card. Edit it and render, or copy the ABC.';
        return;
      }
      if (d.status === 'failed' || d.status === 'cancelled') { if (status) status.textContent = d.error || 'The transcription did not finish.'; return; }
    } catch (_) {}
    setTimeout(tick, 2500);
  };
  setTimeout(tick, 2500);
}

Object.assign(globalThis, {
  songCardRender, musicVariation, musicRestyleFromSong, musicCoverFromSong, musicOpenSong,
  musicSongAction,
  musicScoreEditToggle, musicScorePreview, musicRenderEditedScore, musicScoreCopy,
  musicTaskSet, musicTaskPick, musicWriteLyrics, musicTranscribe,
  musicLyricsRender, musicLyricsType, musicLyricsRetag, musicLyricsMove, musicLyricsRemove,
  musicLyricsAdd, musicLyricsSet, musicLyricsView, musicLyricsViewPick, musicLyricsRawSync,
  musicLyricsLock, musicLyricsRewrite, musicLyricsInit,
  musicModeSet, musicModePick, musicModeInit, musicSimpleActive, musicSimpleChanged,
  musicSimpleCompose,
});

// ===========================================================================
// THE LIBRARY — songs listed like songs, and a player that stays put.
//
// Studied from Suno and kept: a list, not a grid (a song has no thumbnail
// worth a grid cell); cover art on every row; title / style / duration at a
// glance; a play button and a ⋯ menu carrying every verb; families nested one
// level under their parent the way Suno nests versions; a persistent player
// bar with a real waveform. Not kept: their two-clips-per-generation default
// (a full song is minutes of GPU here — Re-roll the sound is the cheap second
// opinion and it is one click on every row).
// ===========================================================================

const _peaks = new Map();                 // path -> Float32Array of peaks
let _barSong = null;                      // the row the bar is showing
let _barList = [];                        // the songs the bar can step through

function _mmss(sec) {
  sec = Math.max(0, Math.round(Number(sec) || 0));
  return `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, '0')}`;
}

// Cover art, deterministic from the song: two hues off a hash of the title and
// style, so a family shares a look and every song has one before an image is
// ever generated for it. (The image engine can paint a real one later; this is
// the fallback that must always exist.)
function _hash(s) { let h = 2166136261; for (const c of String(s)) { h ^= c.charCodeAt(0); h = Math.imul(h, 16777619); } return h >>> 0; }
function songCoverStyle(o) {
  const m = o.music || {};
  const h = _hash((m.title || o.name) + '|' + (m.style || ''));
  const h1 = h % 360, h2 = (h1 + 40 + (h >> 8) % 60) % 360;
  return `background: linear-gradient(135deg, hsl(${h1} 70% 52%), hsl(${h2} 75% 30%));`;
}
function _songTitle(o) {
  const m = o.music || {};
  return m.title || String(o.name || '').replace(/\.wav$/i, '').replace(/^music_\d{8}_\d{6}_/, '').replace(/_/g, ' ');
}
function _styleChips(style, n = 3) {
  return String(style || '').split(/,|·/).map(s => s.trim()).filter(Boolean).slice(0, n)
    .map(s => `<span class="song-chip">${_esc(s)}</span>`).join('');
}
function _kindBadge(m) {
  if (!m) return '';
  const k = m.variation ? ({take: 'take', sound: 're-roll', restyle: 'restyle'}[m.variation] || m.variation)
          : m.cover ? 'cover' : '';
  return k ? `<span class="song-badge">${_esc(k)}</span>` : '';
}

// One level of family: a song's takes sit under it, indented, in order.
function _familyOrder(songs) {
  const byPath = new Map(songs.map(o => [o.path, o]));
  const children = new Map();
  const roots = [];
  for (const o of songs) {
    const parent = o.music && o.music.parent;
    if (parent && byPath.has(parent)) {
      if (!children.has(parent)) children.set(parent, []);
      children.get(parent).push(o);
    } else roots.push(o);
  }
  // Every descendant is listed — a re-roll of a take is a grandchild and
  // used to vanish from the library. Indentation stays capped at one level.
  const out = [], seen = new Set();
  const walk = (o, depth) => {
    if (seen.has(o.path)) return;
    seen.add(o.path);
    out.push({o, depth: Math.min(depth, 1)});
    for (const c of (children.get(o.path) || [])) walk(c, depth + 1);
  };
  for (const r of roots) walk(r, 0);
  for (const o of songs) if (!seen.has(o.path)) walk(o, 0);   // a cycle, or an orphan whose parent is filtered out
  return out;
}

function renderSongList(el, songs) {
  _barList = songs.slice();
  const rows = _familyOrder(songs).map(({o, depth}) => {
    const m = o.music || {};
    const playing = _barSong && _barSong.path === o.path && !_barAudio().paused;
    const attr = JSON.stringify(o.path).replace(/"/g, '&quot;');
    return `<div class="song-row${o.path === activePath ? ' active' : ''}${depth ? ' child' : ''}" data-path="${_esc(o.path)}"
                 onclick="selectOutput(${attr})" role="button" tabindex="0" onkeydown="if(event.key==='Enter')selectOutput(${attr})">
      <div class="song-cover" style="${songCoverStyle(o)}" aria-hidden="true">${depth ? '' : `<span>${_esc(_songTitle(o).slice(0, 1).toUpperCase())}</span>`}</div>
      <div class="song-main">
        <div class="song-row-title">${_esc(_songTitle(o))} ${_kindBadge(m)}${m.instrumental ? '<span class="song-badge muted">instrumental</span>' : ''}</div>
        <div class="song-row-sub">${_styleChips(m.style)}${m.has_score ? '<span class="song-chip score" title="Has sheet music">♪ score</span>' : ''}</div>
      </div>
      <div class="song-dur">${o.clip_sec ? _mmss(o.clip_sec) : ''}</div>
      <button type="button" class="song-play${playing ? ' on' : ''}" title="${playing ? 'Pause' : 'Play'}" aria-label="${playing ? 'Pause' : 'Play'}"
              onclick="event.stopPropagation(); musicBarToggle(${attr})">${playing ? _svgPause() : _svgPlay()}</button>
      <button type="button" class="song-more" title="More" aria-label="More actions"
              onclick="event.stopPropagation(); musicMenuOpen(event, ${attr})">⋯</button>
    </div>`;
  });
  el.classList.add('song-list');
  el.innerHTML = rows.join('');
}
function _svgPlay() { return '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5 L13.5 8 L4 13.5 Z" fill="currentColor"/></svg>'; }
function _svgPause() { return '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><rect x="3" y="2.5" width="3.5" height="11" fill="currentColor"/><rect x="9.5" y="2.5" width="3.5" height="11" fill="currentColor"/></svg>'; }

// The hero for the player surface: a song is not a black rectangle with a
// stock control in it. Cover, title, style — playback lives in the bar.
function songHero(o) {
  const m = o.music || {};
  return `<div class="song-hero">
    <div class="song-hero-cover" style="${songCoverStyle(o)}"><span>${_esc(_songTitle(o).slice(0, 1).toUpperCase())}</span></div>
    <div class="song-hero-text">
      <div class="song-hero-title">${_esc(_songTitle(o))}</div>
      <div class="song-hero-style">${_esc(m.style || '')}</div>
      <div class="song-hero-meta">${o.clip_sec ? _mmss(o.clip_sec) : ''}${m.mode && m.mode !== 'off' ? ' · sheet music' : ''}${m.cover ? ' · cover' : ''}</div>
      <button type="button" class="primary song-hero-play" onclick="musicBarToggle(${JSON.stringify(o.path).replace(/"/g, '&quot;')})">${_svgPlay()} Play</button>
    </div>
  </div>`;
}

// ---- the ⋯ menu ----------------------------------------------------------

function musicMenuOpen(ev, path) {
  const menu = _el('songMenu');
  if (!menu) return;
  const o = (typeof findOutputByPath === 'function') ? findOutputByPath(path) : null;
  const m = (o && o.music) || {};
  const attr = JSON.stringify(path).replace(/"/g, '&quot;');
  const item = (label, sub, fn, disabled, why) =>
    `<button type="button" class="song-menu-item" ${disabled ? `disabled title="${_esc(why || '')}"` : ''} onclick="musicMenuClose(); ${fn}">${label}${sub ? `<span class="sub">${sub}</span>` : ''}</button>`;
  const noArt = !m.has_artifacts, noScore = !m.has_score;
  menu.innerHTML = [
    item('Play', '', `musicBarToggle(${attr})`),
    item('Open', 'score, lyrics, family', `selectOutput(${attr})`),
    '<div class="song-menu-sep"></div>',
    item('New take', 'same song, performed again', `musicVariation('take', ${attr})`, noArt, 'This song kept no artifacts'),
    item('Re-roll the sound', 'same performance, new recording · fast', `musicVariation('sound', ${attr})`, noArt, 'This song kept no artifacts'),
    item('New style on this score', "the composer's style + lyrics", `musicSongAction(${attr}, 'restyle')`, noScore, 'No score on this song'),
    item('Cover it', 'read the tune, write it fresh', `musicSongAction(${attr}, 'cover')`),
    item('Edit the score', '', `musicSongAction(${attr}, 'edit')`, noScore, 'No score on this song'),
    '<div class="song-menu-sep"></div>',
    item('Show in Finder', 'the outputs folder', `if (typeof openOutputsFolder === 'function') openOutputsFolder()`),
    item('Delete', '', `if (typeof deleteOutput === 'function') deleteOutput(${attr})`),
  ].join('');
  menu.hidden = false;
  const r = ev.currentTarget.getBoundingClientRect();
  const w = 260;
  menu.style.left = `${Math.max(8, Math.min(window.innerWidth - w - 8, r.right - w))}px`;
  menu.style.top = `${Math.min(window.innerHeight - menu.offsetHeight - 8, r.bottom + 6)}px`;
  setTimeout(() => document.addEventListener('click', musicMenuClose, {once: true}), 0);
}
function musicMenuClose() { const menu = _el('songMenu'); if (menu) menu.hidden = true; }

// ---- the player bar ------------------------------------------------------

function _barAudio() { return _el('musicBarAudio'); }

function musicBarShow(o, {play = false} = {}) {
  const bar = _el('musicBar');
  if (!bar || !o) return;
  const a = _barAudio();
  const changed = !_barSong || _barSong.path !== o.path;
  _barSong = o;
  bar.hidden = false;
  document.body.classList.add('music-bar-on');
  _el('musicBarCover').setAttribute('style', songCoverStyle(o));
  _el('musicBarTitle').textContent = _songTitle(o);
  _el('musicBarStyle').textContent = (o.music || {}).style || '';
  if (changed) {
    a.src = o.url;
    a.load();
    _el('musicBarTime').textContent = `0:00 / ${o.clip_sec ? _mmss(o.clip_sec) : '–:––'}`;
    _drawWave(o, 0);
    _loadPeaks(o).then(() => { if (_barSong && _barSong.path === o.path) _drawWave(o, a.currentTime / (a.duration || 1)); });
  }
  if (play) a.play().catch(() => {});
  _barSyncButtons();
}
function musicBarToggle(path) {
  const o = (typeof findOutputByPath === 'function') ? findOutputByPath(path) : null;
  if (!o) return;
  const a = _barAudio();
  if (_barSong && _barSong.path === path) { if (a.paused) a.play().catch(() => {}); else a.pause(); }
  else musicBarShow(o, {play: true});
  _barSyncButtons();
}
// The bar's own play/pause. Module state is private to this file, so an
// inline handler cannot read `_barSong` — it has to be a published function.
// (That was the pause button that "did not work": the handler threw.)
function musicBarTogglePlay() {
  if (!_barSong) return;
  const a = _barAudio();
  if (a.paused) a.play().catch(() => {}); else a.pause();
  _barSyncButtons();
}
function musicBarStep(dir) {
  if (!_barSong || !_barList.length) return;
  const i = _barList.findIndex(o => o.path === _barSong.path);
  const next = _barList[(i + dir + _barList.length) % _barList.length];
  if (next) { musicBarShow(next, {play: true}); if (typeof selectOutput === 'function') selectOutput(next.path); }
}
function musicBarSeek(ev) {
  const a = _barAudio();
  if (!a.duration) return;
  const r = ev.currentTarget.getBoundingClientRect();
  a.currentTime = Math.max(0, Math.min(1, (ev.clientX - r.left) / r.width)) * a.duration;
}
function _barSyncButtons() {
  const a = _barAudio();
  const playing = a && !a.paused;
  const btn = _el('musicBarPlay');
  if (btn) { btn.innerHTML = playing ? _svgPause() : _svgPlay(); btn.title = playing ? 'Pause' : 'Play'; }
  document.querySelectorAll('.song-row .song-play').forEach(b => {
    const row = b.closest('.song-row');
    const on = playing && _barSong && row && row.dataset.path === _barSong.path;
    b.classList.toggle('on', on);
    b.innerHTML = on ? _svgPause() : _svgPlay();
  });
}

// Peaks: decode once per song, 480 buckets, cached. Drawn as mirrored bars —
// the played part in the accent, the rest muted. Click to seek.
async function _loadPeaks(o) {
  if (_peaks.has(o.path)) return _peaks.get(o.path);
  try {
    const buf = await (await fetch(o.url)).arrayBuffer();
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const audio = await ctx.decodeAudioData(buf);
    const ch = audio.getChannelData(0);
    const n = 480, step = Math.max(1, Math.floor(ch.length / n));
    const peaks = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let m = 0; const s = i * step, e = Math.min(ch.length, s + step);
      for (let j = s; j < e; j += 4) { const v = Math.abs(ch[j]); if (v > m) m = v; }
      peaks[i] = m;
    }
    ctx.close && ctx.close();
    _peaks.set(o.path, peaks);
    return peaks;
  } catch (_) { return null; }
}
function _drawWave(o, frac) {
  const c = _el('musicBarWave');
  if (!c) return;
  const peaks = _peaks.get(o.path);
  const dpr = window.devicePixelRatio || 1;
  const W = c.clientWidth || 300, H = c.clientHeight || 36;
  if (c.width !== Math.round(W * dpr)) { c.width = Math.round(W * dpr); c.height = Math.round(H * dpr); }
  const g = c.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const cs = getComputedStyle(document.documentElement);
  const accent = cs.getPropertyValue('--accent-bright').trim() || '#58a6ff';
  const muted = cs.getPropertyValue('--border-strong').trim() || '#3d477a';
  const n = 160, bw = W / n;
  for (let i = 0; i < n; i++) {
    let v = 0.15;
    if (peaks) { const s = Math.floor(i * peaks.length / n), e = Math.floor((i + 1) * peaks.length / n); for (let j = s; j < e; j++) v = Math.max(v, peaks[j]); }
    const h = Math.max(2, v * (H - 4));
    g.fillStyle = (i / n) <= frac ? accent : muted;
    g.fillRect(i * bw + bw * 0.2, (H - h) / 2, bw * 0.6, h);
  }
}
function musicBarInit() {
  const a = _barAudio();
  if (!a || a.dataset.wired) return;
  a.dataset.wired = '1';
  a.addEventListener('timeupdate', () => {
    if (!_barSong) return;
    _el('musicBarTime').textContent = `${_mmss(a.currentTime)} / ${_mmss(a.duration || _barSong.clip_sec || 0)}`;
    _drawWave(_barSong, a.duration ? a.currentTime / a.duration : 0);
  });
  a.addEventListener('play', _barSyncButtons);
  a.addEventListener('pause', _barSyncButtons);
  a.addEventListener('ended', () => { _barSyncButtons(); });
  window.addEventListener('resize', () => { if (_barSong) _drawWave(_barSong, a.duration ? a.currentTime / a.duration : 0); });
}
function musicStudioBoot() { musicBarInit(); musicLyricsInit(); musicModeInit(); }
document.addEventListener('DOMContentLoaded', musicStudioBoot);
if (document.readyState !== 'loading') musicStudioBoot();


// ===========================================================================
// MUSIC VIDEO — the song, the pictures, and one line about the look
// ===========================================================================
// The pane is a brief, not a render form: nothing here queues anything. It
// posts to /music/video/plan, which writes an ORDINARY storyboard board, and
// then it opens that board. Every decision about what the shots are lives in
// music_video.py on the server; this file collects four fields and paints
// what came back.
//
// WHY THE CAST IS ITS OWN LITTLE LIST. The `.picker` component is one image
// per slot — it owns a hidden input, a preview and a clear button — and a
// music video wants five or ten pictures each carrying a ROLE and maybe a
// note. So the drop tile is the picker's (same class, same look, same
// `/upload` route and the same `image` field name), and the thumbnails below
// it are this module's own row list. Nothing about the upload path is new.

const MV = { song: null, songName: '', songSeconds: null, cast: [], busy: false, boardId: '' };

function mvSay(msg) { const el = _el('mvStatus'); if (el) el.textContent = msg || ''; }

function mvInit() {
  const drop = _el('mvImagesDrop');
  const file = _el('mvImagesFile');
  const slot = _el('mvSongSlot');
  const song = _el('mvSongInput');
  if (drop && file && !drop.dataset.wired) {
    drop.dataset.wired = '1';
    drop.addEventListener('click', () => file.click());
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('dragover'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
    drop.addEventListener('drop', (e) => {
      e.preventDefault(); drop.classList.remove('dragover');
      mvAddPictures(e.dataTransfer && e.dataTransfer.files);
    });
    file.addEventListener('change', () => { mvAddPictures(file.files); file.value = ''; });
  }
  if (slot && song && !slot.dataset.wired) {
    slot.dataset.wired = '1';
    slot.addEventListener('click', () => song.click());
    slot.addEventListener('dragover', (e) => { e.preventDefault(); slot.classList.add('drop-active'); });
    slot.addEventListener('dragleave', () => slot.classList.remove('drop-active'));
    slot.addEventListener('drop', (e) => {
      e.preventDefault(); slot.classList.remove('drop-active');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) mvUploadSong(f);
    });
    song.addEventListener('change', () => { if (song.files[0]) mvUploadSong(song.files[0]); });
  }
  mvRenderSong();
  mvRenderCast();
  mvFillLibrary();
}

// The library select is filled from the gallery the panel already polls, so
// it needs no endpoint of its own and it can never list a song the outputs
// column does not show.
function mvFillLibrary() {
  const sel = _el('mvSongLibrary');
  if (!sel) return;
  const songs = (typeof currentOutputs !== 'undefined' && currentOutputs || [])
    .filter(o => o && o.kind === 'audio' && o.engine === 'music');
  const keep = sel.value;
  sel.innerHTML = songs.length
    ? '<option value="">Pick one…</option>' + songs.map(o => {
        const m = o.music || {};
        const label = m.title || _songNameFromFile(o.name) || o.name;
        return `<option value="${_esc(o.path)}">${_esc(label)}</option>`;
      }).join('')
    : '<option value="">Nothing composed yet</option>';
  if (keep && songs.some(o => o.path === keep)) sel.value = keep;
}

function mvPickFromLibrary(path) {
  if (!path) return;
  const o = (typeof currentOutputs !== 'undefined' && currentOutputs || [])
    .find(x => x && x.path === path);
  MV.song = path;
  MV.songName = (o && ((o.music || {}).title || _songNameFromFile(o.name))) || path.split('/').pop();
  MV.songSeconds = (o && (o.clip_sec != null ? Number(o.clip_sec) : null));
  mvRenderSong();
  mvSay('');
}

async function mvUploadSong(file) {
  mvSay(`Uploading ${file.name}…`);
  try {
    const fd = new FormData();
    fd.append('audio', file);                      // /upload takes image or audio
    const r = await fetch('/upload', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
    MV.song = data.path;
    MV.songName = file.name;
    MV.songSeconds = (data.duration_sec != null) ? Number(data.duration_sec) : null;
    const sel = _el('mvSongLibrary'); if (sel) sel.value = '';
    mvRenderSong();
    mvSay('');
  } catch (e) {
    mvSay('Could not read that song: ' + (e.message || e));
  }
}

function mvClearSong() {
  MV.song = null; MV.songName = ''; MV.songSeconds = null;
  const sel = _el('mvSongLibrary'); if (sel) sel.value = '';
  mvRenderSong();
}

function mvRenderSong() {
  const slot = _el('mvSongSlot');
  if (!slot) return;
  if (!MV.song) {
    slot.innerHTML = '<span class="ref-tag">Song</span>'
      + '<div class="hint" style="padding:24px 12px;text-align:center;color:var(--muted);font-size:12px;">'
      + 'Drop a WAV / MP3 / M4A / FLAC here, or click to pick a file.</div>';
    return;
  }
  const dur = (MV.songSeconds != null && isFinite(MV.songSeconds))
    ? ' · ' + _mmss(MV.songSeconds) : '';
  slot.innerHTML = '<span class="ref-tag">Song</span>'
    + '<div style="padding:18px 12px;text-align:center;font-size:12px;color:var(--text);overflow:hidden;text-overflow:ellipsis;">'
    + '<svg class="ph" aria-hidden="true" style="width:18px;height:18px;vertical-align:-3px;margin-right:6px;"><use href="#ph-music-notes"/></svg>'
    + _esc(MV.songName || 'song') + dur
    + '<div class="hint" style="margin-top:8px;">'
    + '<a href="#" onclick="event.stopPropagation();mvClearSong();return false;">Remove</a>'
    + '</div></div>';
}

async function mvAddPictures(files) {
  const list = Array.from(files || []);
  if (!list.length) return;
  mvSay(`Uploading ${list.length} picture${list.length > 1 ? 's' : ''}…`);
  for (const f of list) {
    try {
      const fd = new FormData();
      fd.append('image', f);
      const r = await fetch('/upload', { method: 'POST', body: fd });
      const data = await r.json();
      if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
      // The FIRST picture is the singer unless the user says otherwise: a cast
      // with no face plans as B-roll only, and arriving at that by default
      // would read as the feature not working.
      MV.cast.push({ path: data.path, name: f.name,
                     role: MV.cast.length === 0 ? 'singer' : 'room', prompt: '' });
    } catch (e) {
      mvSay(`Could not add ${f.name}: ` + (e.message || e));
      mvRenderCast();
      return;
    }
  }
  mvSay('');
  mvRenderCast();
}

function mvSetRole(i, role) {
  if (!MV.cast[i]) return;
  MV.cast[i].role = role;
  mvRenderCast();
}

function mvSetPrompt(i, text) {
  if (MV.cast[i]) MV.cast[i].prompt = String(text || '');
}

function mvRemovePicture(i) {
  MV.cast.splice(i, 1);
  mvRenderCast();
}

const MV_ROLES = [
  ['singer', 'Singer', 'Filmed singing, in sync with the song'],
  ['instrument', 'Instrument', 'B-roll — hands, keys, strings'],
  ['room', 'Room', 'B-roll — the wide, the lights, the audience'],
];

function mvRenderCast() {
  const box = _el('mvCast');
  if (!box) return;
  box.innerHTML = MV.cast.map((im, i) => `
    <div class="mv-pic">
      <img class="mv-pic-thumb" src="/image?path=${encodeURIComponent(im.path)}&w=160" alt="">
      <div class="mv-pic-body">
        <div class="mv-pic-name" title="${_esc(im.path)}">${_esc(im.name || im.path.split('/').pop())}</div>
        <div class="mv-roles" role="group" aria-label="What this picture is for">
          ${MV_ROLES.map(([key, label, tip]) => `
            <button type="button" class="pill-btn${im.role === key ? ' active' : ''}"
                    data-i="${i}" data-role="${key}" title="${_esc(tip)}">${label}</button>`).join('')}
        </div>
        <input type="text" class="mv-pic-prompt" data-i="${i}"
               placeholder="Optional — what happens in this shot"
               value="${_esc(im.prompt || '')}">
      </div>
      <button type="button" class="mv-pic-x" data-i="${i}" title="Take this picture out">
        <svg class="ph" aria-hidden="true"><use href="#ph-x-bold"/></svg>
      </button>
    </div>`).join('');
  // Listeners, not inline onclick: generated markup resolves inline handlers
  // through the global scope, which is the v4.9.0 regression class the webapp
  // lint exists to catch.
  box.querySelectorAll('.mv-roles button').forEach(b =>
    b.addEventListener('click', () => mvSetRole(+b.dataset.i, b.dataset.role)));
  box.querySelectorAll('.mv-pic-prompt').forEach(inp =>
    inp.addEventListener('input', () => mvSetPrompt(+inp.dataset.i, inp.value)));
  box.querySelectorAll('.mv-pic-x').forEach(b =>
    b.addEventListener('click', () => mvRemovePicture(+b.dataset.i)));
  const chip = _el('mvSummary');
  if (chip) {
    const n = MV.cast.length;
    const faces = MV.cast.filter(c => c.role === 'singer').length;
    chip.textContent = n ? `${n} picture${n > 1 ? 's' : ''} · ${faces} singer${faces === 1 ? '' : 's'}` : '';
  }
}

async function mvPlan() {
  if (MV.busy) return;
  if (!MV.song) { mvSay('Pick a song first.'); return; }
  if (!MV.cast.length) { mvSay('Drop at least one picture.'); return; }
  const btn = _el('mvPlanBtn');
  MV.busy = true;
  if (btn) { btn.disabled = true; btn.textContent = 'Reading the song…'; }
  mvSay('Reading the beat and writing the shot list…');
  try {
    const fd = new URLSearchParams();
    fd.set('song', MV.song);
    fd.set('images', JSON.stringify(MV.cast.map(c => ({
      path: c.path, role: c.role, prompt: c.prompt || '' }))));
    fd.set('style', (_el('mvStyle') || {}).value || '');
    fd.set('title', MV.songName || 'Music video');
    const r = await fetch('/music/video/plan', { method: 'POST', body: fd });
    const data = await r.json();
    if (!r.ok || data.error) throw new Error(data.error || ('HTTP ' + r.status));
    MV.boardId = data.board_id;
    mvSay(data.summary + (data.notes && data.notes.length ? ' — ' + data.notes.join(' ') : ''));
    const chip = _el('mvSummary'); if (chip) chip.textContent = data.summary;
    if (typeof phosToast === 'function') phosToast(data.summary, { kind: 'success' });
    // AND SHOW IT. A shot list nobody can see is a toast that fades; the plan
    // ends where the work continues, which is the board.
    if (typeof workflowSwitch === 'function') workflowSwitch('storyboard');
    if (typeof sbOpen === 'function') await sbOpen(data.board_id);
  } catch (e) {
    mvSay('Could not plan that video: ' + (e.message || e));
  } finally {
    MV.busy = false;
    if (btn) { btn.disabled = false; btn.textContent = 'Plan the video'; }
  }
}

Object.assign(globalThis, {
  renderSongList, songHero, songCoverStyle, musicMenuOpen, musicMenuClose,
  musicBarShow, musicBarToggle, musicBarTogglePlay, musicBarStep, musicBarSeek, musicBarInit,
  mvInit, mvPlan, mvPickFromLibrary, mvClearSong, mvFillLibrary,
});
