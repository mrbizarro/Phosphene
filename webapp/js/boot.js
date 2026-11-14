// webapp/js/boot.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
globalThis.ASPECTS = BOOT.aspects;
globalThis.FPS = BOOT.fps;
const MODEL_UPSCALE_ENABLED = !!BOOT.model_upscale_enabled;
globalThis.PIPERSR_UPSCALE_ENABLED = !!BOOT.pipersr_upscale_enabled;
// Capability tier — drives the Q4-vs-Q8 surface split. 'q4' for sub-48GB
// Macs (Compact tier) where the Q8 dev transformer + two-stage HQ pipeline
// can't fit; 'q8' for everything 48GB+. Exposed globally so any future
// runtime check (e.g. fetch warnings, queue filters) can read it without
// re-reading the body attribute. LTX_FORCE_CAP_TIER=q4 env override on
// panel launch flips the visible surface to Q4-only on a Q8 dev machine
// (useful for low-RAM-user testing on Mr Bizarro's M4 Max).
window.PHOSPHENE_CAP_TIER = (BOOT.cap_tier || 'q8');

// Where this panel runs (distribution.py, BOOT.distribution): Pinokio, or the
// Phosphene Mac app, which has no Pinokio sidebar, no Stop/Start and no git -
// it updates the studio itself (Phosphene -> Check for Updates...). Every
// sentence that names Pinokio goes through distText(pinokio, macApp), so
// Pinokio (BOOT.distribution 'pinokio') reads exactly what it always did.
function isMacApp() { return BOOT.distribution === 'mac-app'; }
function distText(pinokio, macApp) { return isMacApp() ? macApp : pinokio; }
// LINKS TO THE WEBSITE carry anonymous UTM tags (site_links.py is the Python
// twin; test_site_links.py keeps the two campaign lists identical). The ONLY
// place a phosphene.video link gets its tags: siteLink(path, campaign, content).
// BOOT.site_utm = {source: 'app', medium: 'mac-app' | 'pinokio'} comes from the
// server. Nothing else goes into the link: no install id, no version, no email.
const SITE_ORIGIN = 'https://phosphene.video';
const SITE_CAMPAIGNS = [
  'get-pro', 'upgrade', 'pricing', 'docs', 'download', 'support',
  'whats-new', 'remote', 'changelog', 'star',
  'home', 'website', 'benchmarks', 'acceptable-use', 'privacy',
  'install', 'release-body',
];
const SITE_NAME_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
// Merge into any query already on the url: an old utm_* is replaced, never
// doubled; other parameters and the #fragment stay.
function siteLink(path, campaign, content) {
  if (!SITE_CAMPAIGNS.includes(campaign)) throw new Error('unknown utm_campaign ' + campaign);
  if (content != null && !SITE_NAME_RE.test(content)) throw new Error('bad utm_content ' + content);
  const base = (typeof BOOT !== 'undefined' && BOOT.site_utm) || {source: 'app', medium: 'pinokio'};
  const u = new URL(String(path).startsWith('http') ? path : SITE_ORIGIN + path);
  if (u.protocol !== 'https:' || (u.hostname !== 'phosphene.video' && u.hostname !== 'www.phosphene.video')) {
    throw new Error('only phosphene.video links are tagged');
  }
  ['utm_source', 'utm_medium', 'utm_campaign', 'utm_content'].forEach(k => u.searchParams.delete(k));
  u.searchParams.set('utm_source', base.source);
  u.searchParams.set('utm_medium', base.medium);
  u.searchParams.set('utm_campaign', campaign);
  if (content) u.searchParams.set('utm_content', content);
  return u.toString();
}
// Anchors in the markup say WHAT they are, never how they are tagged:
//   <a href="https://phosphene.video/pricing/" data-utm-campaign="get-pro" data-utm-content="pro-modal">
// The plain href is the fallback; this gives it its tags on load and again on
// the click (before any onclick="openExternal(this.href)" reads it).
function siteLinkApply(a) {
  const campaign = a.dataset.utmCampaign;
  if (!campaign) return;
  try { a.href = siteLink(a.getAttribute('href'), campaign, a.dataset.utmContent || null); } catch (_) {}
}
(function wireSiteLinks() {
  const go = () => document.querySelectorAll('a[data-utm-campaign]').forEach(siteLinkApply);
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[data-utm-campaign]') : null;
    if (a) siteLinkApply(a);
    // Anonymous count of Get Pro clicks and which surface (owner, 8 Nov 2026); opt-out is the server's.
    if (a && a.dataset.utmCampaign === 'get-pro' && typeof _uiEvent === 'function') {
      const where = (a.dataset.utmContent || '').replace(/-/g, '_');
      // The Pro window's plan buttons also say which plan (monthly, yearly, founding); nothing else rides along.
      const plan = where === 'pro_modal' && ['monthly', 'yearly', 'founding'].includes(a.dataset.plan) ? a.dataset.plan : null;
      if (where && where !== 'speedup_line') _uiEvent('pro_click', plan ? {where, action: 'get', plan} : {where, action: 'get'});
    }
  }, true);
})();
// Developer-only text (environment variables, file paths, docs pages) is marked
// data-hide-mac-app in the page: the Mac app's person never sees it.
(function hideForMacApp() {
  if (!isMacApp()) return;
  const go = () => document.querySelectorAll('[data-hide-mac-app]').forEach(el => { el.style.display = 'none'; });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
})();
// The edition label ('free' / 'pro' from the Mac app, '' under Pinokio). A
// label only: body[data-edition] and this are the hooks, nothing paid.
function phEdition() { return BOOT.edition || ''; }

// Apply tier-aware time estimates to the Quality pill subtitles.
function applyTierTimes() {
  // NO-OP for the LTX strip since v4.0, deliberately, and kept as a named
  // function because the tier modal still calls it.
  //
  // This was the THIRD writer of the LTX chips' subtitles, and it wrote a
  // per-hardware-tier estimate into the .ql-spec slot that now carries the
  // canvas and the frame count. Two writers for one span is how a chip ends up
  // claiming "1024×576 · about 8 min" for a render the table prices at ~2 min.
  //
  // The tier-aware estimate is not lost: every LTX chip reads BOOT.ltx.tiers
  // (ltx_tiers_payload(), server-side), which is chip-AND-RAM-aware per
  // VC-01 — a strictly better number than a RAM-tier-only figure ever was.
  // BOOT.quality_times (a RAM-tier-only table, now retired — VC-12/SYS-20)
  // is gone; the Tier modal reads the same honest source via honest_tier_times().
  return;
}

// Keyframe mode toggle — 2-frame FFLF, or any 3–8-anchor long shot.
window._kfMode = 2;  // default: FFLF
window._kfMidTouched = false;
window._kfTimingTouched = {};
window._kfTimingLastFrames = null;
window._kfRenderedMode = null;

function normalizeKeyframeMode(n) {
  n = parseInt(n, 10);
  if (!Number.isFinite(n) || n < 3) return 2;
  return Math.max(3, Math.min(8, n));
}

function keyframeSlotKey(idx) {
  return String(idx).padStart(2, '0');
}

function keyframeImageKey(idx) {
  return `keyframe_${keyframeSlotKey(idx)}_image`;
}

function isKeyframeModeChipActive(btn, n) {
  if (btn.dataset.mode !== 'keyframe') return false;
  const def = btn.dataset.kfDefault || '2';
  if (def === 'multi') return n >= 3;
  return parseInt(def, 10) === n;
}

function setKeyframeMode(n) {
  n = normalizeKeyframeMode(n);
  window._kfMode = n;
  document.querySelectorAll('.kf-toggle-chip').forEach(c => {
    const m = c.dataset.kfMode;
    c.classList.toggle('active', m === 'multi' ? n >= 3 : parseInt(m, 10) === n);
  });
  const countRow = document.getElementById('kfCountRow');
  if (countRow) countRow.style.display = (currentMode === 'keyframe' && n >= 3) ? '' : 'none';
  const countSelect = document.getElementById('keyframe_count');
  if (countSelect && n >= 3) countSelect.value = String(n);
  const hint = document.getElementById('keyframeHint');
  if (hint && document.body.dataset.engine === 'h3') {
    hint.textContent = n >= 3
      ? T('boot.hailuo_h3_passes_through_every', 'Hailuo H3 passes through every still at its At(s) time and lands exactly on the end frame — with the sound and dialogue made around them.')
      : T('boot.hailuo_h3_starts_on_the', 'Hailuo H3 starts on the start frame and lands exactly on the end frame. The start frame is optional: leave it empty and H3 invents the opening, then arrives at your end frame.');
  } else if (hint) {
    hint.textContent = n >= 3
      ? T('boot.keyframes_needs_q8_it_always', '{n} Keyframes needs Q8 — it always renders the Q8 two-stage pass, whatever the Quality pill says. Intermediate beats are locked at their At(s) times below.', {n})
      : T('boot.fflf_needs_q8_it_always', 'FFLF needs Q8 — it always renders the Q8 two-stage pass, whatever the Quality pill says. The model interpolates between the first and last frames.');
  }
  if (currentMode === 'keyframe') {
    document.querySelectorAll('#modeGroup .pill-btn').forEach(b => {
      b.classList.toggle('active', isKeyframeModeChipActive(b, n));
    });
  }
  window._kfTimingLastFrames = null;
  renderKeyframeDynamicSlots();
  syncKeyframeTiming();
  if (typeof updatePromptPlaceholder === 'function') updatePromptPlaceholder();
  updateDerived();
}

function keyframeTimingSlots(count = window._kfMode) {
  count = normalizeKeyframeMode(count);
  if (count < 3) return [];
  const slots = [];
  for (let idx = 2; idx <= count - 1; idx++) {
    const key = keyframeSlotKey(idx);
    slots.push({
      idx,
      key,
      label: `Beat ${idx}`,
      frac: (idx - 1) / (count - 1),
      imageKey: keyframeImageKey(idx),
      secId: `keyframe_${key}_seconds`,
      frameId: `keyframe_${key}_frame`,
    });
  }
  return slots;
}

function registerDynamicPicker(key) {
  if (!PICKERS.includes(key)) PICKERS.push(key);
  pickerWire(key);
}

function renderKeyframeDynamicSlots() {
  const wrap = document.getElementById('keyframeDynamicSlots');
  const timingRow = document.getElementById('kfTimingRow');
  const countRow = document.getElementById('kfCountRow');
  if (!wrap) return;
  const visible = currentMode === 'keyframe' && window._kfMode >= 3;
  if (countRow) countRow.style.display = visible ? '' : 'none';
  if (timingRow) timingRow.style.display = visible ? '' : 'none';
  if (!visible) {
    wrap.innerHTML = '';
    window._kfRenderedMode = null;
    return;
  }
  if (window._kfRenderedMode === window._kfMode && wrap.children.length) return;
  const previousImages = {};
  const previousSeconds = {};
  wrap.querySelectorAll('input[type="hidden"][data-kf-image-key]').forEach(inp => { previousImages[inp.id] = inp.value; });
  wrap.querySelectorAll('input[type="number"][data-kf-seconds-key]').forEach(inp => { previousSeconds[inp.id] = inp.value; });
  const slots = keyframeTimingSlots();
  wrap.innerHTML = slots.map(slot => {
    const imageKey = slot.imageKey;
    const path = previousImages[imageKey] || '';
    const sec = previousSeconds[slot.secId] || '';
    return `
      <div class="kf-dynamic-section" data-kf-slot="${slot.key}">
        <h2 style="margin-top:10px">${slot.label} <span class="hint">(keyframe ${slot.idx} of ${window._kfMode})</span></h2>
        <div class="picker" data-key="${imageKey}">
          <div class="picker-drop" id="picker_drop_${imageKey}">
            <div class="picker-empty">
              <div class="picker-icon"><svg class="ph"><use href="#ph-film-strip"/></svg></div>
              <div class="picker-cta">${T('boot.drop', 'Drop')} <strong>${slot.label.toLowerCase()}</strong>, or <strong>${T('boot.click_to_browse', 'click to browse')}</strong></div>
              <div class="hint">${T('boot.intermediate_motion_anchor_between_start', 'Intermediate motion anchor between Start and End.')}</div>
            </div>
            <img class="picker-preview" id="picker_preview_${imageKey}" alt="" style="display:none">
            <button type="button" class="picker-clear" id="picker_clear_${imageKey}" title="${TA('boot.clear', 'Clear')}" style="display:none"><svg class="ph" aria-hidden="true"><use href="#ph-x-bold"/></svg></button>
          </div>
          <input type="file" id="picker_file_${imageKey}" accept="image/*" style="display:none">
          <input type="hidden" name="${imageKey}" id="${imageKey}" value="${escapeHtml(path)}" data-kf-image-key="1">
          <div class="picker-recent" id="picker_recent_${imageKey}_wrap" style="display:none">
            <div class="picker-recent-label">${T('boot.recent_uploads_click_to_use', 'Recent uploads · click to use')}</div>
            <div class="picker-recent-strip" id="picker_recent_${imageKey}"></div>
          </div>
        </div>
        <div class="mini-fields" style="margin-top:10px">
          <div class="mf-cell">
            <span class="mf-label">${slot.label} at (s)</span>
            <input id="${slot.secId}" name="${slot.secId}" value="${escapeHtml(sec)}" type="number" min="0.04" step="0.01" data-kf-seconds-key="1">
            <input id="${slot.frameId}" name="${slot.frameId}" value="" type="hidden">
          </div>
        </div>
      </div>
    `;
  }).join('');
  window._kfRenderedMode = window._kfMode;
  slots.forEach(slot => {
    registerDynamicPicker(slot.imageKey);
    const inp = document.getElementById(slot.secId);
    if (inp) {
      inp.addEventListener('input', () => {
        window._kfTimingTouched[slot.key] = true;
        syncKeyframeTiming();
      });
    }
    const hidden = document.getElementById(slot.imageKey);
    if (hidden && hidden.value) pickerSetImage(slot.imageKey, hidden.value, { snapAspect: false, update: false });
  });
  if (_uploadsCache.length) refreshUploadsStrip();
}

function maybeScaleTouchedKeyframeTiming(oldFrames, newFrames) {
  if (window._kfMode < 3) return;
  oldFrames = parseInt(oldFrames, 10);
  newFrames = parseInt(newFrames, 10);
  if (!Number.isFinite(oldFrames) || !Number.isFinite(newFrames) || oldFrames <= 1 || newFrames <= 1 || oldFrames === newFrames) return;
  const scale = (newFrames - 1) / (oldFrames - 1);
  if (!Number.isFinite(scale) || scale <= 0) return;
  keyframeTimingSlots().forEach(slot => {
    const touched = !!window._kfTimingTouched[slot.key] || (slot.key === 'mid' && !!window._kfMidTouched);
    if (!touched) return;
    const inp = document.getElementById(slot.secId);
    if (!inp) return;
    const sec = parseFloat(inp.value || '');
    if (!Number.isFinite(sec)) return;
    const scaledFrame = Math.max(1, Math.round(sec * FPS * scale));
    inp.value = (scaledFrame / FPS).toFixed(2);
  });
}

function syncKeyframeTiming() {
  const hint = document.getElementById('kfTimingHint');
  if (!hint) return;
  const frames = Math.max(3, parseInt(document.getElementById('frames')?.value || '121', 10) || 121);
  const total = Math.max(0, (frames - 1) / FPS);
  const slots = keyframeTimingSlots();
  let prevFrame = 0;
  const actualFrames = [];
  slots.forEach((slot, i) => {
    const inp = document.getElementById(slot.secId);
    const frameInp = document.getElementById(slot.frameId);
    if (!inp || !frameInp) return;
    const remaining = slots.length - i;
    const minFrame = prevFrame + 1;
    const maxFrame = Math.max(minFrame, frames - 1 - remaining);
    inp.min = (minFrame / FPS).toFixed(2);
    inp.max = (maxFrame / FPS).toFixed(2);
    let sec = parseFloat(inp.value || '');
    const touched = !!window._kfTimingTouched[slot.key] || (slot.key === 'mid' && !!window._kfMidTouched);
    if (!Number.isFinite(sec) || !touched) sec = total * slot.frac;
    let frame = Math.round(sec * FPS);
    frame = Math.max(minFrame, frame);
    frame = Math.min(maxFrame, frame);
    frame = Math.max(1, Math.min(frames - 2, frame));
    prevFrame = frame;
    actualFrames.push(frame);
    frameInp.value = String(frame);
    inp.value = (frame / FPS).toFixed(2);
  });
  window._kfTimingLastFrames = frames;
  const allFrames = [0, ...actualFrames, frames - 1];
  const segments = [];
  for (let i = 0; i < allFrames.length - 1; i++) {
    segments.push(((allFrames[i + 1] - allFrames[i]) / FPS).toFixed(2) + 's');
  }
  hint.textContent = window._kfMode === 6
    ? `Segments ${segments.join(' / ')} · frames ${allFrames.join(', ')}`
    : `First segment ${segments[0]} · second segment ${segments[1]} · mid frame ${allFrames[1]}/${frames - 1}`;
}


globalThis.filterMode = 'visible';
globalThis.activePath = null;
globalThis.currentOutputs = [];
globalThis.currentMode = 't2v';
// A deliberate grace window after the user touches the main player. Native
// video controls briefly report paused while seeking/buffering; without the
// timestamp the next 1.5 s poll could mistake that instant for an idle stage
// and replace the clip the user just asked to watch.
globalThis.LIVE_STAGE_PLAYBACK_HOLD_MS = 12000;
window._stagePlaybackIntentAt = 0;
window._liveStageJobId = null;
window._liveStageOwnsPlayer = false;
window._liveStageForcedJobId = null;
window._liveStagePendingOutput = null;
window._liveStageReviewPath = null;
// REMIX_MODES — the IC-LoRA reference tools grouped under the single "Remix"
// mode pill. These are REAL backend modes (the #mode field + the dispatch see
// them); "remix" itself is a UI-only pseudo-mode that resolves to one of these.
globalThis.REMIX_MODES = ['ingredients', 'control', 'restore', 'upscale'];

// Main right-pane gallery kind filter (All / Videos / Photos). Independent
// of `filterMode` (which is visible/hidden) and independent of
// `window.outputsFilter` (which is the agent-stage Session outputs filter
// from commit ef7f114). The localStorage key is intentionally different:
// the user can have "Photos" pinned on the main pane while Sessions sits
// on "All". Default is 'all'; setMode('image') auto-flips to 'photos',
// the video modes auto-flip to 'videos'. Manual user clicks always win.
globalThis.mainOutputsFilter = 'all';
try {
  const stored = localStorage.getItem('phos_main_outputs_filter');
  if (stored === 'all' || stored === 'videos' || stored === 'photos' || stored === 'audio') {
    mainOutputsFilter = stored;
  }
} catch (e) {}
// Photo detection that matches commit af5c184's isPhotoOutput on the
// agent-stage pane: explicit kind > params.mode > filename suffix. The
// server now stamps a `kind` field, but the suffix fallback covers
// outputs whose sidecar got lost (older entries) and any future caller
// that forgets to set kind.
function outputKind(o) {
  if (!o) return 'video';
  if (['image', 'video', 'audio'].includes(o.kind)) return o.kind;
  const path = (o.path || o.name || '').toLowerCase();
  if (/\.(png|jpg|jpeg|webp)$/.test(path)) return 'image';
  return /\.wav$/.test(path) ? 'audio' : 'video';
}
// Compatibility for older gallery harnesses; all UI branches use outputKind.
function isPhotoOutputMain(o) { return outputKind(o) === 'image'; }
// Apply the main filter on top of currentOutputs. Returns the filtered
// array; callers also use this for the count badge so the filter and
// the rendered cells agree.
// `currentOutputs` is the polling fast-path payload (newest 60 from /status).
// When the user clicks "Show all", we fetch the full unified list via the
// /outputs endpoint and merge anything that isn't already in currentOutputs
// into `_olderOutputs`. The carousel renders the union, sorted newest-first,
// so a render that completes after "Show all" still slots in at the top
// without forcing another refresh.
window._olderOutputs = window._olderOutputs || [];
window._showingAllOutputs = window._showingAllOutputs || false;

function filteredMainOutputs() {
  // Union of polled + older, deduped by path (polled wins because it has
  // the freshest mtime/sidecar state for in-flight files).
  let all;
  if (window._showingAllOutputs && window._olderOutputs.length) {
    const seen = new Set(currentOutputs.map(o => o.path));
    const extras = window._olderOutputs.filter(o => !seen.has(o.path));
    all = currentOutputs.concat(extras);
  } else {
    all = currentOutputs;
  }
  if (mainOutputsFilter === 'photos') all = all.filter(o => outputKind(o) === 'image');
  else if (mainOutputsFilter === 'audio') all = all.filter(o => outputKind(o) === 'audio');
  else if (mainOutputsFilter !== 'all') all = all.filter(o => outputKind(o) === 'video');
  // VC-04: when the Hidden view is on, /status was asked with
  // include_hidden=1 (queue.js poll()), which returns hidden AND visible
  // outputs mixed — this is the other half of that filter, narrowing to
  // just the hidden ones instead of showing everything twice-over.
  all = (filterMode === 'hidden') ? all.filter(o => o.hidden) : all.filter(o => !o.hidden);
  return applyOutputsQuery(all);
}

// THE SEARCH. Every word typed must appear somewhere in the output's name
// or its sidecar words (`q`, built server-side); order does not matter.
// "aria 1280 turbo" finds the Aria clips rendered at 1280 wide on the turbo
// tier, and nothing else.
let _outputsQuery = '';
function applyOutputsQuery(rows) {
  const words = String(_outputsQuery || '').toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length) return rows;
  return rows.filter(o => {
    const hay = (String(o.name || '') + ' ' + String(o.q || '')).toLowerCase();
    return words.every(w => hay.indexOf(w) >= 0);
  });
}
function setOutputsQuery(v) {
  _outputsQuery = String(v || '');
  // A search over what is on screen is a search over the newest 60 unless
  // the older ones are in: pull them the first time a word is typed.
  if (_outputsQuery && !window._showingAllOutputs && typeof outputsLoadAll === 'function') {
    try { outputsLoadAll().then(() => { if (typeof renderCarousel === 'function') renderCarousel(); paintOutputsCount(); }); } catch (e) {}
  }
  if (typeof renderCarousel === 'function') renderCarousel();
  paintOutputsCount();
}
// ONE FORMATTER for the header, derived from (query, chip, count) every
// time — the review caught "Outputs · 1507" losing its word after a search
// was cleared, and "0 photos" where "0 matches" was true.
function outputsTitleText() {
  const n = filteredMainOutputs().length;
  const kind = mainOutputsFilter === 'all' ? '' : (' ' + outputsKindWord(mainOutputsFilter));
  if (_outputsQuery) return TP('boot.outputs_n_matches', n, {one: 'Outputs · {count} match{kind}', other: 'Outputs · {count} matches{kind}'}, {kind});
  if (kind) return outputsCountTitle(n, mainOutputsFilter);
  return TP('boot.outputs_n_outputs', n, {one: 'Outputs · {count} output', other: 'Outputs · {count} outputs'});
}
// The gallery filter's word (videos / photos / audio) in the page's language.
function outputsKindWord(kind) {
  return ({ videos: T('boot.kind_videos', 'videos'), photos: T('boot.kind_photos', 'photos'),
            audio: T('boot.kind_audio', 'audio') })[kind] || kind;
}
// "Outputs · 23" (All) or "Outputs · 23 photos" (a filter chip).
function outputsCountTitle(n, kind) {
  if (!kind || kind === 'all') return T('boot.outputs_n', 'Outputs · {count}', {count: n});
  return T('boot.outputs_n_kind', 'Outputs · {count} {kind}', {count: n, kind: outputsKindWord(kind)});
}
function outputsQueryText() { return _outputsQuery; }
function paintOutputsCount() {
  const t = document.getElementById('carouselTitle');
  if (!t) return;
  t.textContent = outputsTitleText();
}

// "Show all (N)" button handler — fetches the full unified gallery from
// /outputs. limit=10000 is effectively "all" on any realistic install;
// the server caps image discovery at MAX_IMG=5000 + however many mp4s
// exist in OUTPUT. Idempotent: clicking twice just refetches and re-
// renders.
async function outputsLoadAll() {
  // Re-entrancy guard. The poll loop, a chip click, and a mode switch can
  // all race to call this simultaneously when the filter is empty; without
  // the in-flight flag, /outputs gets hit two or three times in parallel
  // and the latter responses clobber each other's renders.
  if (window._outputsLoadAllInFlight) return window._outputsLoadAllInFlight;
  const btn = document.getElementById('outputsShowAllBtn');
  if (btn) { btn.textContent = T('boot.loading', 'Loading…'); btn.disabled = true; }
  const p = (async () => {
    try {
      const r = await fetch('/outputs?limit=10000&offset=0');
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const d = await r.json();
      window._olderOutputs = Array.isArray(d.outputs) ? d.outputs : [];
      window._showingAllOutputs = true;
      if (btn) { btn.style.display = 'none'; btn.disabled = false; }
      renderCarousel();
      // Update the title count to reflect the new visible total.
      const _visible = filteredMainOutputs();
      const t = document.getElementById('carouselTitle');
      if (t) t.textContent = outputsCountTitle(_visible.length, mainOutputsFilter);
    } catch (e) {
      if (btn) { btn.textContent = T('boot.show_all', 'Show all'); btn.disabled = false; }
      console.warn('outputsLoadAll failed:', e);
    } finally {
      window._outputsLoadAllInFlight = null;
    }
  })();
  window._outputsLoadAllInFlight = p;
  return p;
}
// A delete / hide / unhide changed what /outputs would say. The poll
// refreshes `currentOutputs`, but filteredMainOutputs() re-adds any path the
// Show-all cache still holds — so a deleted clip came back as a 404 card and
// a hidden one stayed visible until a reload (Codex UI-06). Drop the paths we
// know about now, then refetch the full list (the server may have removed
// companions too: an upscale's hidden native, its sidecars).
function outputsCacheMutated(paths) {
  const gone = new Set((paths || []).filter(Boolean));
  if (gone.size) {
    window._olderOutputs = (window._olderOutputs || []).filter(o => !gone.has(o.path));
  }
  if (window._showingAllOutputs && typeof outputsLoadAll === 'function') {
    // A fetch already in flight began before this change and would put the
    // old list back — refetch after it, not instead of it.
    const inflight = window._outputsLoadAllInFlight;
    try {
      if (inflight) inflight.then(() => outputsLoadAll());
      else outputsLoadAll();
    } catch (_) {}
  }
}
function _updateMainFilterChips() {
  const a = document.getElementById('mainOutputsFilterAll');
  const v = document.getElementById('mainOutputsFilterVideos');
  const p = document.getElementById('mainOutputsFilterPhotos');
  if (a) a.classList.toggle('active', mainOutputsFilter === 'all');
  if (v) v.classList.toggle('active', mainOutputsFilter === 'videos');
  if (p) p.classList.toggle('active', mainOutputsFilter === 'photos');
  const au = document.getElementById('mainOutputsFilterAudio');
  if (au) au.classList.toggle('active', mainOutputsFilter === 'audio');
}
// Shared "filter is empty, the missing kind is paginated out of /status,
// pull it in via /outputs" auto-fetch. Returns true if a fetch was kicked
// (caller should bail out — outputsLoadAll will do the render itself).
// Called by BOTH setMainOutputsFilter (chip clicks) AND
// _autoMainOutputsFilterForMode (mode-switch implicit filter change), so
// users landing on an empty Photos/Videos view never see a blank gallery
// regardless of HOW they got to that filter.
function _maybeAutoLoadAllForEmptyFilter(mode) {
  if (mode === 'all') return false;
  if (window._showingAllOutputs) return false;
  if (typeof outputsLoadAll !== 'function') return false;
  if (filteredMainOutputs().length !== 0) return false;
  outputsLoadAll().then(() => {
    const visible = filteredMainOutputs();
    if (visible.length && !visible.some(o => o.path === activePath)) {
      selectOutput(visible[0].path, { autoplay: false });   // not a click
    }
  });
  return true;
}
function setMainOutputsFilter(mode) {
  if (mode !== 'all' && mode !== 'videos' && mode !== 'photos' && mode !== 'audio') mode = 'all';
  mainOutputsFilter = mode;
  try { localStorage.setItem('phos_main_outputs_filter', mode); } catch (e) {}
  // Filter change → reset the carousel render cap so the user lands at
  // the top of the new filter without accumulated "Show more" clicks
  // bleeding through. Poll-driven re-renders (same filter, new output
  // arrived) keep their expanded cap because that path doesn't go
  // through setMainOutputsFilter.
  window._carouselRenderLimit = null;
  _updateMainFilterChips();
  // If the user filtered to a kind that's not in the polled top-60 (e.g.
  // photos when the recent 60 are all videos — a common state after a
  // big overnight render batch), auto-trigger Show all so the missing
  // kind loads in from /outputs. Without this, the gallery looks empty
  // and users assume their photos "disappeared" when they're just
  // paginated out of the polling default.
  if (_maybeAutoLoadAllForEmptyFilter(mode)) return;
  const visible = filteredMainOutputs();
  // If the active selection was filtered out, switch to the first match.
  if (visible.length && !visible.some(o => o.path === activePath)) {
    selectOutput(visible[0].path, { autoplay: false });   // not a click
  }
  renderCarousel();
  // Reset scroll position on filter change so the user lands at the
  // top of the newly-filtered list, not mid-gallery from the previous
  // selection's position.
  const carEl = document.getElementById('carousel');
  if (carEl) carEl.scrollTop = 0;
  // Count badge mirrors the active filter (e.g. "Outputs · 23 photos").
  const titleEl = document.getElementById('carouselTitle');
  if (titleEl && filterMode !== 'hidden') {
    titleEl.textContent = outputsCountTitle(visible.length, mode);
  }
}
// Auto-set helper called from setMode(). DOES NOT persist (writing to
// localStorage would override the user's manual pick the next time they
// switch modes), but DOES bump the active chip + re-render so the
// gallery snaps to the new default. The user can override after, and
// THAT click persists via setMainOutputsFilter().
function _autoMainOutputsFilterForMode(mode) {
  // Auto-set NEVER lands on 'all' — that's user-only, per spec.
  let target = null;
  if (mode === 'image') target = 'photos';
  else if (mode === 't2v' || mode === 'i2v' || mode === 'oneshot' || mode === 'keyframe' || mode === 'extend' || mode === 'restore' || mode === 'ingredients' || mode === 'control') target = 'videos';
  if (!target) return;
  // Same-filter early-return is conditional now: if the filter is already
  // on `target` but the visible list is empty AND we haven't loaded the
  // full /outputs payload yet, we still need to kick the auto-fetch. The
  // old early-return left Image-mode users stuck on an empty Photos view
  // (the 0246be3 chip-click fix never fired because the chip was already
  // active, and re-clicking an active chip isn't a habit users have).
  if (target === mainOutputsFilter) {
    _maybeAutoLoadAllForEmptyFilter(target);
    return;
  }
  mainOutputsFilter = target;
  // No localStorage write — see comment above.
  _updateMainFilterChips();
  // Same auto-fetch as setMainOutputsFilter — empty after a mode switch
  // means /status's top-60 had no items of this kind. Pull /outputs.
  if (_maybeAutoLoadAllForEmptyFilter(target)) return;
  if (typeof renderCarousel === 'function') renderCarousel();
  const titleEl = document.getElementById('carouselTitle');
  if (titleEl && filterMode !== 'hidden') {
    const visible = filteredMainOutputs();
    titleEl.textContent = outputsCountTitle(visible.length, target);
  }
  // Swap the player to a matching entry if the current active is now
  // off-filter (e.g. mode=image but the viewer is showing a video).
  const visible = filteredMainOutputs();
  if (visible.length && !visible.some(o => o.path === activePath)) {
    selectOutput(visible[0].path, { autoplay: false });   // not a click
  }
}

// Model tag in the bottom-pane nav links to dgrauet's repo. Strip an
// absolute filesystem path back to the HF repo id form for display
// (the panel sets LTX_MODEL to a local path in Pinokio installs).
// THE CREDIT NAMES THE WEIGHTS THAT MADE THE CLIP YOU ARE LOOKING AT.
//
// It used to be a one-shot read of BOOT.model at page load, so it printed the
// active LTX pack under an H3 render, and under every clip ever made on another
// generation. The sidecar has always known which model ran; list_outputs now
// carries it, so the credit follows the SELECTION rather than the process.
//
// A clip that predates the field has no `model` and falls back to BOOT.model
// silently — the old behaviour, for the only case where it was ever right.
// H3-34: an H3 clip's raw model path (.../mlx_models/hailuo-h3/deepbeep-
// pruned-bf16/models/h3-dit-q8/...) fell through the same slice-off-the-
// last-two-segments logic LTX paths use, which reads a quantization-pack
// folder name, not an engine — so the credit read a bare "models/h3-dit-
// q8" with no indication it names H3 at all. Recognize the H3 tree
// explicitly and say the engine.
function _modelCreditLabel(raw) {
  const m = String(raw || '');
  if (!m) return '';
  if (/\/hailuo-h3\//.test(m) || /\bh3-dit-(q8|bf16)\b/.test(m)) {
    return /\bh3-dit-q8\b|\/models\/h3-dit-q8\//.test(m) || /deepbeep-pruned-q8/.test(m)
      ? T('boot.hailuo_h3_compact_q8', 'Hailuo H3 · compact (Q8)') : T('boot.hailuo_h3_full_bf16', 'Hailuo H3 · full (bf16)');
  }
  let label = m;
  const idx = m.indexOf('mlx_models/');
  if (idx >= 0) label = m.slice(idx + 'mlx_models/'.length);
  if (label.startsWith('/')) label = label.split('/').slice(-2).join('/');
  return label;
}
function updateModelCredit(path) {
  const el = document.getElementById('modelTag');
  if (!el) return;
  let raw = '';
  let entry = null;
  try {
    const p = path || (typeof activePath !== 'undefined' ? activePath : '');
    entry = (typeof currentOutputs !== 'undefined' && currentOutputs || []).find(o => o && o.path === p);
    // VC-20: a song is only "what's active" while the Audio tab itself is
    // showing it. Browsing to Outputs -> Audio from the Video tab is a
    // FILTER on the gallery, not a switch of what Generate will render —
    // the credit chip stayed pinned to the last song's "YuE2 · MLX by
    // vanch007" even though the LTX form on the left was still the one
    // about to run. Off that tab, ignore the clip's own engine entirely
    // and fall through to the form's own model (BOOT.model below).
    const onAudioTab = document.body.dataset.workflow === 'audio';
    if (entry && entry.kind === 'audio' && entry.engine === 'music' && onAudioTab) {
      // The slot is a short chip beside the Now/Queue tabs: the full credit
      // lives in the tooltip and on the Compose form.
      el.textContent = T('boot.yue2_mlx_by_vanch007', 'YuE2 · MLX by vanch007');
      el.title = T('boot.generated_with_yue2_by_multimodal', 'Generated with YuE2 by Multimodal Art Projection · MLX port by vanch007');
      el.href = 'https://github.com/vanch007/mlx-Yue';
      return;
    }
    // A song browsed from another tab credits the form's engine, not the
    // song (VC-20) — so it must not count as "a selected clip" for the
    // H3-34 active-engine fallback below either.
    if (entry && entry.kind === 'audio' && entry.engine === 'music') entry = null;
    if (entry && entry.model) {
      raw = entry.model;
    }
  } catch (e) {}
  // H3-34: with no clip selected (or one that predates the `model` field),
  // this fell back to BOOT.model unconditionally — a server-rendered
  // snapshot from page load that is ALWAYS the LTX pack, regardless of
  // engine. On H3 that showed "ltx-2.5-mlx-q4" while the form was set up to
  // render on H3 entirely. Fall back to whichever engine is actually active.
  if (!raw && !entry && document.body.dataset.engine === 'h3') {
    const kind = (typeof H3 !== 'undefined' && H3.dit_choice && H3.dit_choice.kind) || 'bf16';
    el.textContent = kind === 'q8' ? T('boot.hailuo_h3_compact_q8', 'Hailuo H3 · compact (Q8)') : T('boot.hailuo_h3_full_bf16', 'Hailuo H3 · full (bf16)');
    el.title = `Current engine: ${el.textContent} — MiniMax H3 (FL2VA)`;
    el.href = 'https://github.com/mrbizarro/minimax-h3-mlx';
    return;
  }
  const label = _modelCreditLabel(raw || BOOT.model);
  el.textContent = label;
  const isH3Label = label.indexOf('Hailuo H3') === 0;
  const credit = isH3Label ? T('boot.minimax_h3_fl2va', 'MiniMax H3 (FL2VA)') : T('boot.mlx_port_by_dgrauet', 'MLX port by @dgrauet');
  // SYS-39: this label follows the SELECTED clip (deliberately — see the
  // comment above; it used to credit the currently-loaded engine and
  // mis-attributed every older-generation clip). Without any hint of
  // that, selecting an old output and watching the tag change reads as
  // "the engine changed", which it hasn't. The tooltip says what it
  // actually is; `raw` set means "this clip", the fallback means "no clip
  // selected yet".
  el.title = raw
    ? T('boot.this_clip_was_rendered_with', 'This clip was rendered with {label} — {credit}', {label, credit})
    : `Current engine: ${label} — ${credit}`;
  el.href = isH3Label
    ? 'https://github.com/mrbizarro/minimax-h3-mlx'
    : 'https://github.com/dgrauet/ltx-2-mlx';
}
updateModelCredit();
// `audio` is still a free-text input (advanced section); `image` is now a
// picker — leave the picker empty by default and let the user pick or
// drop. Pre-filling examples/reference.png surprised users into rendering
// the demo image when they meant to leave it blank.
document.getElementById('audio').value = BOOT.default_audio;

// ====== Pill-button group helpers ======
// Avoid (negative prompt) toggle — keeps the textarea collapsed by
// default so the prompt-and-go flow stays tight. The button label flips
// to "Avoid −" when open so the affordance is clear. Auto-opens once
// the user types into it (so it stays visible) — shouldn't auto-close
// from typing-then-deleting because that would yank the field out from
// under their next keystroke.
function toggleAvoidRow(forceOpen) {
  const row = document.getElementById('avoidRow');
  const btn = document.getElementById('avoidToggleBtn');
  const lbl = document.getElementById('avoidToggleLabel');
  const ta = document.getElementById('negative_prompt');
  if (!row) return;
  const wantOpen = (forceOpen === true) ? true : !row.classList.contains('show');
  row.classList.toggle('show', wantOpen);
  if (btn) btn.classList.toggle('active', wantOpen);
  if (lbl) lbl.textContent = wantOpen ? T('boot.avoid', 'Avoid −') : T('boot.avoid_2', 'Avoid +');
  if (wantOpen && ta) {
    try { ta.focus(); } catch (e) {}
  }
  // VC-15: the collapsed label also needs "(off at Balanced)" appended
  // when guidance is ignored at the current mode/quality — recomputed
  // here (not just from updateDerived's poll) so opening/closing the row
  // updates it immediately rather than waiting a beat.
  if (typeof updateAvoidIgnoredState === 'function') { try { updateAvoidIgnoredState(); } catch (e) {} }
}
// Auto-open the Avoid row if it has content (e.g. loaded from a sidecar
// via loadParams). Run once at boot AND after loadParams sets values.
function syncAvoidRowFromValue() {
  const ta = document.getElementById('negative_prompt');
  if (!ta) return;
  if ((ta.value || '').trim() !== '') {
    const row = document.getElementById('avoidRow');
    if (row && !row.classList.contains('show')) toggleAvoidRow(true);
  }
}

// Is Ingredients servable by the generation this install is running?
// One reader for the one server-owned flag, so the four places that now care
// cannot drift apart. (BOOT.ltx.ingredients_available comes from
// ltx_generation_serves_ingredients() — the same predicate the worker's gate
// uses, so the UI and the server can never disagree about this.)
function ingredientsServed() {
  return (BOOT.ltx || {}).ingredients_available !== false;
}
// Motion Control on a generation the Union adapter was not trained against:
// the motion still transfers, the prompt's grip on the new subject does not.
// Not a gate — an honest line in the place the decision is made. The sentence
// is the server's (LTX_CONTROL_GENERATION_NOTE); this only places it.
function _paintControlGenNote() {
  const el = document.getElementById('controlGenNote');
  if (!el) return;
  const ltx = BOOT.ltx || {};
  if (ltx.control_full_repaint === false && ltx.control_generation_note) {
    el.textContent = ltx.control_generation_note;
    el.style.display = '';
  } else {
    el.style.display = 'none';
  }
}
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', _paintControlGenNote);
} else { _paintControlGenNote(); }
// Which Remix tool the parent pill opens when there is no remembered one.
// Ingredients is the default where it works; on a generation that cannot
// serve it, Control is — offering a door that only leads to a refusal is
// the bug this exists to close.
function defaultRemixMode() {
  return ingredientsServed() ? 'ingredients' : 'control';
}

function setMode(mode) {
  // One Shot is a workflow tab now (webapp/js/oneshot.js), not a mode of
  // this form: a stale 'oneshot' — a saved mode, an old Load Params path —
  // opens the tab and leaves the video form as it was.
  if (mode === 'oneshot') {
    if (typeof workflowSwitch === 'function') { try { workflowSwitch('oneshot'); } catch (e) {} }
    return;
  }
  // "remix" is a UI GROUP, not a backend mode — clicking the parent Remix pill
  // resumes the last-used Remix tool (default Ingredients). Everything below
  // (and the backend) only ever sees a real mode from REMIX_MODES.
  if (mode === 'remix') mode = window._lastRemixMode || defaultRemixMode();
  // GENERATION GUARD, and the actual hole 16 people fell through: the
  // Ingredients SUB-chip was disabled and its click handler blocked, but the
  // parent "Remix" pill in the main mode bar was not — it called
  // setMode('remix'), which resolved to 'ingredients' by default (and
  // _lastRemixMode is never persisted, so every fresh page load took that
  // default). One click on a fully-enabled pill and the hidden #mode input
  // said `ingredients`; the server then refused after the submit. Same shape
  // as the Q4 snap below, and for the same reason: the chips are only the
  // polite half — this is the half that holds when something calls setMode
  // directly.
  if (mode === 'ingredients' && !ingredientsServed()) {
    console.warn('setMode(ingredients): not served by this generation — '
                 + T('boot.snapping_to', 'snapping to ') + defaultRemixMode());
    mode = defaultRemixMode();
  }
  // Capability guard — Q4 (sub-48GB) tier can't run FFLF or Extend
  // (Q8-only pipelines). CSS already hides the chips, but a stale
  // localStorage, charactersLoadParams(), or a JS caller could still try
  // to switch. Snap to t2v with a console warning so we never end up in an
  // unrenderable mode by accident. Character IS available on Q4 (the LoRA
  // fuses into the distilled base — identity match is mediocre but the
  // render completes), so it's deliberately NOT in this list.
  // NOTE: 'restore' (Colorize) is also NOT in this list — its IC-LoRA was
  // trained against the Q4 distilled checkpoint, so it RUNS on the Q4 tier.
  // Don't add either back here or you'll break the feature on sub-48GB.
  // Hailuo H3 renders both on its own engine (its Q8 lane fits 36 GB+), so
  // the LTX tier rule does not apply while H3 is the engine.
  if (window.PHOSPHENE_CAP_TIER === 'q4' && (mode === 'keyframe' || mode === 'extend')
      && document.body.dataset.engine !== 'h3') {
    console.warn(`setMode(${mode}): not available on Q4 tier — snapping to t2v`);
    mode = 't2v';
  }
  currentMode = mode;
  // VA-23: Train / Character / Image (Studio) all return early below, before
  // the fall-through video-mode branch's own remixSubGroup show/hide logic
  // (the `_inRemix` block further down) ever runs. Remix → Upscale & Face
  // Fix → Character used to leave the Remix tool panel on screen with
  // "Upscale & Face Fix" still lit, as if Character renders would be
  // upscaled or face-fixed too. Hide it unconditionally here; the
  // fall-through branch re-shows it when the new mode actually is a Remix
  // tool (REMIX_MODES.indexOf(mode) !== -1).
  const _remixBarEarly = document.getElementById('remixSubGroup');
  if (_remixBarEarly && mode !== 'remix'
      && (typeof REMIX_MODES === 'undefined' || REMIX_MODES.indexOf(mode) === -1)) {
    _remixBarEarly.style.display = 'none';
  }
  // HDR vs Character mutual exclusion — reflect mode change in pill state.
  // Runs in a microtask so the rest of setMode finishes setting UI bits
  // first (character chip strip visibility, etc.).
  if (typeof _applyHdrPillAvailability === 'function') {
    Promise.resolve().then(_applyHdrPillAvailability);
  }
  // The Studio pill swaps the form-pane in place: hide the video form
  // (genForm) and show the inline #studioSection. Right rail (queue,
  // current, history) stays visible regardless of mode. The mode dropdown
  // value is set to a non-form value when in studio so an accidental
  // form submit doesn't trigger a video render with stale fields.
  const studio = document.getElementById('studioSection');
  const train = document.getElementById('trainSection');
  const genForm = document.getElementById('genForm');
  // Train mode mirrors Studio: hide #genForm, show #trainSection. Same
  // chrome conventions, separate form (so an Enter inside the trigger
  // input doesn't submit a video render).
  if (mode === 'train') {
    if (studio) studio.classList.remove('show');
    if (train) train.classList.add('show');
    if (genForm) genForm.style.display = 'none';
    document.querySelectorAll('#modeGroup .pill-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === 'train'));
    // Manual Characters picker is inside #genForm so it hides with the
    // form, but ensure the visibility helper is in sync if it ever moves.
    if (typeof _updateCharsPickerVisibility === 'function') {
      _updateCharsPickerVisibility('train');
    }
    // Lazy initialization on first entry — wires drop zone, generates
    // a starter trigger, refreshes the trained-LoRAs list, computes
    // an initial wall-time estimate.
    if (typeof trainInit === 'function') trainInit();
    updatePromptPlaceholder();
    return;
  }
  if (mode === 'character') {
    // Character is a UI intent, not a backend mode — the hidden #mode
    // field still ships 't2v' on submit. make_job sees character_id and
    // expands it into face+audio LoRAs. On Q8, the quality strip swaps
    // to Q8 Draft/Pro; on Q4, the regular quality pills stay visible
    // and the LoRAs fuse into the distilled base.
    if (studio) studio.classList.remove('show');
    if (train) train.classList.remove('show');
    if (genForm) genForm.style.display = '';
    document.querySelectorAll('#modeGroup .pill-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === 'character'));
    // Visibility cascade: shows chip strip, swaps to Q8 quality strip,
    // forces quality=high if a chip is selected. If no chip is selected
    // yet, the user lands on the same form but with chip strip visible
    // so they can pick.
    if (typeof _updateCharsPickerVisibility === 'function') {
      _updateCharsPickerVisibility('character');
    }
    // Re-render the avatar strip so the .active ring reflects the CURRENT
    // selection (which is '' after any prior mode switch) — prevents a stale
    // highlight from making an unselected character look selected.
    if (typeof _renderManualCharactersList === 'function') {
      try { _renderManualCharactersList(); } catch (_) {}
    }
    if (typeof _applyCharacterQualityStripVisibility === 'function') {
      try { _applyCharacterQualityStripVisibility(); } catch (_) {}
    }
    // Mode hidden field still 't2v' — backend doesn't know 'character'
    // and doesn't need to (character_id is what drives the LoRA stack).
    const modeInp = document.getElementById('mode');
    if (modeInp) modeInp.value = 't2v';
    // Character stacks LTX LoRAs; H3 has no LoRA path, so force LTX here too
    // (the mode hidden field says t2v, which H3 *would* otherwise accept).
    if (typeof _syncEngineForMode === 'function') {
      try { _syncEngineForMode(); } catch (e) {}
    }
    updatePromptPlaceholder();
    return;
  }
  if (mode === 'image') {
    if (studio) studio.classList.add('show');
    if (genForm) genForm.style.display = 'none';
    document.querySelectorAll('#modeGroup .pill-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.mode === 'image'));
    // Manual Characters picker hides with #genForm in Studio mode, but
    // keep the helper in sync so it doesn't surface when the user flips
    // back to a non-T2V video mode that also doesn't show it.
    if (typeof _updateCharsPickerVisibility === 'function') {
      _updateCharsPickerVisibility('image');
    }
    // Portal the unified LoRA picker into the Studio composer slot, so
    // the SAME UI (one chip strip, one library, one + LoRA button) is
    // visible under Image Studio. Re-renders with the engine-aware
    // filter automatically (renderLorasList reads _currentLoraModeFilter
    // every time, no separate config to thread). The hidden #lorasJson
    // input STAYS inside #genForm — imgStudioGenerate reads
    // _activeLoras directly when posting, so the studio submit doesn't
    // depend on the hidden field's location.
    _portalLoraPicker('studio');
    // Re-render the unified LoRA picker so its mode-aware filter
    // (which reads `currentMode` we just set above) actually applies.
    // Without this the picker stays on whatever filter it had under
    // the previous mode — the symptom the user hit: clicked Studio,
    // banner still read "LTX-Video LoRAs (active video mode)" until
    // they jiggled the engine dropdown to force a re-render.
    if (typeof renderLorasList === 'function') renderLorasList();
    // Wire ref drop-zones once + refresh library on every entry
    if (typeof imgStudioWireRefSlots === 'function') imgStudioWireRefSlots();
    if (typeof imgStudioRefreshLibrary === 'function') imgStudioRefreshLibrary();
    // Pull engine cache state + recent-uploads strip + estimate so the
    // composer is fully populated before the user reads it. These are
    // best-effort and silent on failure.
    if (typeof imgStudioRefreshEngineStatus === 'function') imgStudioRefreshEngineStatus();
    if (typeof imgStudioRefreshRecent === 'function') imgStudioRefreshRecent();
    if (typeof imgStudioUpdateEstimate === 'function') imgStudioUpdateEstimate();
    // Show/hide the Ideogram text-placement canvas based on the engine.
    if (typeof ideoSyncVisibility === 'function') ideoSyncVisibility();
    // Auto-set the right-pane outputs gallery to "Photos" — image mode
    // is composing photos, so a video gallery makes no sense as the
    // default. User can still flip back to All/Videos manually.
    if (typeof _autoMainOutputsFilterForMode === 'function') {
      _autoMainOutputsFilterForMode('image');
    }
    updatePromptPlaceholder();
    return;
  }
  if (studio) studio.classList.remove('show');
  if (train) train.classList.remove('show');
  if (genForm) genForm.style.display = '';
  // Portal the picker back to its video-form home so it's visible above
  // the Generate button when the user is composing a video shot.
  _portalLoraPicker('video');
  // Same re-render trigger as the image branch above — without it the
  // picker keeps the previous mode's filter when flipping back from
  // Studio to a video mode.
  if (typeof renderLorasList === 'function') renderLorasList();
  document.getElementById('mode').value = mode;
  // The form's mode on <body>, for the engine-specific folds in panel.css
  // (e.g. H3's Length strip means nothing while extending a clip).
  document.body.dataset.vmode = mode;
  document.querySelectorAll('#modeGroup .pill-btn').forEach(b => {
    if (mode === 'keyframe') {
      b.classList.toggle('active', isKeyframeModeChipActive(b, window._kfMode));
    } else if (b.dataset.mode === 'remix') {
      // The parent Remix pill stays lit for ANY of its sub-tools.
      b.classList.toggle('active', REMIX_MODES.indexOf(mode) !== -1);
    } else {
      b.classList.toggle('active', b.dataset.mode === mode);
    }
  });
  // Remix group: reveal the sub-tool row + light the active sub-pill when the
  // current mode is one of the Remix tools; hide the row otherwise. Remember
  // the last Remix tool so the parent pill resumes it next time it's clicked.
  const _inRemix = REMIX_MODES.indexOf(mode) !== -1;
  if (_inRemix) window._lastRemixMode = mode;
  const _remixBar = document.getElementById('remixSubGroup');
  if (_remixBar) _remixBar.style.display = _inRemix ? '' : 'none';
  if (_inRemix) {
    document.querySelectorAll('#remixSubGroup .pill-btn').forEach(b =>
      b.classList.toggle('active', b.dataset.remix === mode));
  }
  // Manual-tab Characters picker is T2V-only (Text-to-Video flow). Other
  // video modes (I2V, FFLF, Extend) have a different mental model — the
  // user is anchoring on a frame, not picking an actor.
  if (typeof _updateCharsPickerVisibility === 'function') {
    _updateCharsPickerVisibility(mode);
  }
  // Mode → main-outputs filter auto-set (Videos for video modes).
  if (typeof _autoMainOutputsFilterForMode === 'function') {
    _autoMainOutputsFilterForMode(mode);
  }
  // For i2v, switch the actual mode based on the i2vMode select
  if (mode === 'i2v') {
    document.getElementById('mode').value = document.getElementById('i2vMode').value;
  }
  // Keyframe REQUIRES Q8 (uses dev transformer); force quality=high.
  // If Q8 isn't available the High pill stays disabled and the user gets the
  // same "Q8 not installed" hint as elsewhere.
  if (mode === 'keyframe') {
    setQuality('high');
  }
  updateAccelAvailability();
  updateTemporalAvailability();
  updateDerived();
  if (mode === 'extend' && typeof applyExtendPillPrices === 'function') {
    applyExtendPillPrices();
  }
  if (typeof updateShotSetupSummary === 'function') { try { updateShotSetupSummary(); } catch (e) {} }
  // Ingredients (multi-reference) — lazily wire the multi-image picker + load
  // the recent-uploads strip on first entry. Idempotent (guarded by __wired).
  if (mode === 'ingredients') {
    if (typeof ingredientPickerWire === 'function') ingredientPickerWire();
    if (typeof refreshIngredientRecent === 'function') refreshIngredientRecent();
  }
  // Refresh the inline models card immediately — switching to FFLF when
  // Q8 is missing should surface the Download Q8 CTA without waiting for
  // the next 1.5s poll tick.
  if (LAST_STATUS) updateModelsCard(LAST_STATUS);
  // Engine ↔ mode consistency. Hailuo H3 only serves Text and Image; every
  // other mode snaps the picker back to LTX-2.3 with a one-line note rather
  // than letting the user queue a job the server would reject.
  if (typeof _syncEngineForMode === 'function') {
    try { _syncEngineForMode(); } catch (e) {}
  }
  updatePromptPlaceholder();
}

// Move the unified LoRA picker between its homes (Option A portal).
//   "video"      → goes inside #genForm (declared position)
//   "studio"     → goes inside #studioSection
//   "characters" → goes inside #loraPickerCharactersSlot (Characters compose card)
// Idempotent: re-portal to the same destination is a no-op. The hidden
// #lorasJson input is a SEPARATE element that stays inside #genForm
// regardless, so the video form's FormData(genForm) keeps picking it
// up. Image Studio's imgStudioGenerate() and the Characters tab's
// charactersGenerate() both read _activeLoras directly when posting.
function _portalLoraPicker(dest) {
  const node = document.getElementById('lorasDetails');
  if (!node) return;
  const slots = {
    "video":      document.getElementById('loraPickerVideoSlot'),
    "studio":     document.getElementById('loraPickerStudioSlot'),
    "characters": document.getElementById('loraPickerCharactersSlot'),
  };
  const target = slots[dest] || slots["video"];
  if (!target || node.parentElement === target) {
    if (typeof renderLorasList === 'function') renderLorasList();
    return;
  }
  target.appendChild(node);
  // Re-render so the mode banner + library filter snap to the new
  // (mode + engine) context immediately.
  if (typeof renderLorasList === 'function') renderLorasList();
}


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
Object.assign(globalThis, {
  applyTierTimes, setKeyframeMode, keyframeTimingSlots, renderKeyframeDynamicSlots,
  maybeScaleTouchedKeyframeTiming, syncKeyframeTiming, outputKind, isPhotoOutputMain, filteredMainOutputs,
  applyOutputsQuery, setOutputsQuery, paintOutputsCount, outputsTitleText, outputsQueryText,
  outputsLoadAll, outputsCacheMutated, _updateMainFilterChips, _maybeAutoLoadAllForEmptyFilter, setMainOutputsFilter,
  updateModelCredit, toggleAvoidRow, syncAvoidRowFromValue, ingredientsServed,
  _paintControlGenNote, defaultRemixMode, setMode, _portalLoraPicker,
  isMacApp, distText, phEdition, siteLink, siteLinkApply,
});
