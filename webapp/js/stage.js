// webapp/js/stage.js — extracted verbatim from the panel page's inline
// script block (slice 3 of docs/ARCHITECTURE.md). ES module: top-level
// declarations are module-private; the publish block at the bottom is
// the module's public surface.
// ====== Image Studio (manual still gen) ======================================
// State: 3 reference slots. Each slot holds {path, name} when populated.
globalThis.IMG_STUDIO = {
  refs: [null, null, null],
  busy: false,
};

// Engine families that REQUIRE at least one reference image (mflux's
// qwen-edit CLI marks --image-paths as required; calling it without
// refs returns an argparse error after model load). The Studio engine
// override values map 1:1 to these check predicates. Used by
// imgStudioUpdateValidity() to gate the Generate button.
function imgStudioRequiresRefs(engineOverride) {
  return engineOverride === 'qwen_edit_inline'
      || engineOverride === 'qwen_edit_lightning_inline'
      || engineOverride === 'qwen_edit_high_inline';
}

function imgStudioUpdateValidity() {
  const btn = document.getElementById('imgStudioGenBtn');
  const eng = document.getElementById('imgStudioEngine');
  const status = document.getElementById('imgStudioStatus');
  if (!btn || !eng) return;
  const refsCount = IMG_STUDIO.refs.filter(r => r && r.path).length;
  const needsRefs = imgStudioRequiresRefs(eng.value);
  let invalidReason = '';
  // Family-install gate (issue #12): refuse upfront when the chosen
  // engine's family binary is missing. Falling through to submit lets
  // the job die deep in the helper with a buried error.
  const engInfo = (typeof _IMG_ENGINE_STATUS === 'object' && _IMG_ENGINE_STATUS)
    ? _IMG_ENGINE_STATUS[eng.value] : null;
  if (engInfo && engInfo.family_installed === false) {
    // The install-gate is generic across mflux engines; the human-readable
    // add-on name differs per family. Ideogram 4 ships as part of mflux
    // 0.18+, so its "missing" case is really "update mflux", not a Qwen hint.
    invalidReason = (eng.value === 'ideogram4_inline')
      ? 'The Ideogram 4 engine needs mflux 0.18+ (the mflux-generate-ideogram4 CLI). Update the mflux add-on, then come back and Generate.'
      : 'The Qwen-Image-Edit engine isn\'t installed. It ships with the image-engine pack: click Update in Pinokio\'s Phosphene sidebar (NOT in this panel), then come back and Generate. If it\'s still missing after Update, the sidebar also shows "Reinstall image engines (Ideogram 4 + Qwen-Edit)" — ~30 s, ~150 MB.';
  } else if (needsRefs && refsCount === 0) {
    invalidReason = 'Pick at least 1 reference image (drop a file into one of the 3 slots above) — Qwen-Image-Edit composes against an image, it cannot run text-only. Use the Lightning preset only after picking a ref.';
  }
  // Non-blocking notice (does NOT disable Generate): Ideogram is text-only,
  // so a loaded reference is silently dropped unless the reference bridge is
  // on. Surface that trap with a way out (turn on the bridge, or switch to a
  // Reference Edit engine for a faithful pixel copy). Closes the silent-drop
  // trap flagged in the spec.
  let softNotice = '';
  if (!invalidReason
      && eng.value === 'ideogram4_inline'
      && refsCount >= 1
      && !(typeof ideoState === 'object' && ideoState.refBridge)) {
    softNotice = 'Heads up: Ideogram is text-only, so your reference image will be IGNORED. Turn on "Use reference" above to have Ideogram redraw it from a description, or switch to a Reference Edit engine for a faithful copy.';
  }
  // Don't override the busy state — imgStudioGenerate manages disabled
  // during in-flight gens.
  if (!IMG_STUDIO.busy) {
    btn.disabled = !!invalidReason;
    btn.title = invalidReason || 'Generate (Cmd/Ctrl+Enter)';
    if (status && !status.textContent.startsWith('Generating')) {
      if (invalidReason) {
        status.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-warning-fill"/></svg>' + escapeHtml(invalidReason);
      } else if (softNotice) {
        status.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-warning-fill"/></svg>' + escapeHtml(softNotice);
      } else {
        status.textContent = '';
      }
    }
  }
}

// Reveal the mock-engine option only when the URL has ?debug=1.
// Mock paints flat colored rectangles — useful for testing the UX
// without spending GPU time, but a casual user picks it and gets
// confused output. Audit finding HIGH #14.
(function() {
  try {
    const params = new URLSearchParams(window.location.search);
    if (params.get('debug') === '1') {
      const m = document.getElementById('imgStudioMockOption');
      if (m) m.removeAttribute('hidden');
    }
  } catch (e) {}
})();

// Phase 1: Image Studio is now an inline pane (#studioSection) shown by
// setMode('image'). These two functions stay as no-arg shims so any
// caller still doing openImageStudio()/closeImageStudio() works.
// Switching to 'image' shows the pane; switching to any other video
// mode hides it. The pill row drives both flows.
function openImageStudio() {
  setMode('image');
  // Re-evaluate Generate button state (engine + ref count) every time the
  // user enters Studio so they don't see a stale "ready" button when the
  // engine selection makes refs mandatory.
  imgStudioUpdateValidity();
  setTimeout(() => {
    const t = document.getElementById('imgStudioPrompt');
    if (t) {
      t.focus();
      try { t.scrollIntoView({behavior:'smooth', block:'center'}); } catch (e) {}
    }
  }, 50);
}

function closeImageStudio() {
  // Return to the last sensible video mode. t2v is the safe default;
  // a future enhancement could remember the previous mode the user was
  // on before clicking Studio. For Phase 1, t2v is fine.
  setMode('t2v');
}

function imgStudioWireRefSlots() {
  // Backwards-compat: the legacy `.img-ref-slot` selector matched the old
  // 3-column flat grid. The new restyled composer uses `.studio-ref-slot`.
  // Wire both so any in-flight upgrade path keeps working.
  document.querySelectorAll('.studio-ref-slot, .img-ref-slot').forEach(slot => {
    if (slot.dataset.wired === '1') return;
    slot.dataset.wired = '1';
    const idx = parseInt(slot.dataset.slot, 10);
    // Click → file picker. Skip when the user clicked the close (×) button
    // — its handler manages the clear flow.
    slot.addEventListener('click', (e) => {
      if (e.target.classList && e.target.classList.contains('clear-x')) return;
      const input = document.createElement('input');
      input.type = 'file';
      input.accept = 'image/png,image/jpeg,image/webp';
      input.onchange = () => {
        if (input.files && input.files[0]) imgStudioUploadRef(idx, input.files[0]);
      };
      input.click();
    });
    // Drag + drop. .dragover class is style-driven (no inline style) so it
    // composes cleanly with .has-image / hover.
    slot.addEventListener('dragover', (e) => {
      e.preventDefault();
      slot.classList.add('dragover');
    });
    slot.addEventListener('dragleave', () => slot.classList.remove('dragover'));
    slot.addEventListener('drop', (e) => {
      e.preventDefault();
      slot.classList.remove('dragover');
      const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
      if (f) imgStudioUploadRef(idx, f);
    });
    imgStudioRenderSlot(idx);
  });
}

const _STUDIO_REF_TAGS = ['Primary', 'Multi-ref', 'Multi-ref'];

function imgStudioRenderSlot(idx) {
  const slot = document.querySelector(
    `.studio-ref-slot[data-slot="${idx}"], .img-ref-slot[data-slot="${idx}"]`);
  if (!slot) return;
  const ref = IMG_STUDIO.refs[idx];
  const tag = _STUDIO_REF_TAGS[idx] || `Ref ${idx + 1}`;
  if (ref && ref.path) {
    slot.classList.add('has-image');
    slot.innerHTML = `
      <span class="ref-tag">${tag}</span>
      <img src="/image?path=${encodeURIComponent(ref.path)}&w=320" alt="">
      <button class="clear-x" type="button" onclick="imgStudioClearRef(${idx});event.stopPropagation()" title="Remove"><svg class="ph" aria-hidden="true"><use href="#ph-x-bold"/></svg></button>
    `;
  } else {
    slot.classList.remove('has-image');
    const cta = idx === 0
      ? 'Drop · click<br>character'
      : (idx === 1 ? 'Drop · click<br>place' : 'Drop · click<br>style');
    slot.innerHTML = `
      <span class="ref-tag">${tag}</span>
      <div class="ref-empty">
        <div class="ref-icon"><svg class="ph" aria-hidden="true"><use href="#ph-image"/></svg></div>
        <div class="ref-cta">${cta}</div>
      </div>
    `;
  }
  imgStudioUpdateValidity();
  imgStudioUpdateRefWarning();
  // The Ideogram reference-bridge checkbox only appears once a ref exists —
  // re-evaluate its visibility whenever a slot fills or clears.
  if (typeof ideoSyncRefBridge === 'function') ideoSyncRefBridge();
  // Recent strip's "in-use" highlight depends on which paths are bound to
  // slots — re-render the strip so freshly-cleared slots release their
  // selection ring.
  if (typeof imgStudioMarkRecentInUse === 'function') imgStudioMarkRecentInUse();
}

// Multi-subject prompt-format coaching (2026-05-18 round 2).
//
// Both engines we ship — Qwen-Image-Edit-2511 and HiDream-O1-Dev-BF16 —
// support multi-reference subject composition at the model level. The
// issue is the prompt: when the user writes "two characters sitting at
// dinner" with no language anchor pointing each character to a specific
// reference image, the model blends/conflates them (the smile-woman-
// fused-to-arms ghost rendering Mr Bizarro reported on 2026-05-18).
//
// Both engines respond well to the same explicit pattern:
//   "the person from reference 1 + the person from reference 2 + scene"
//
// This banner surfaces the pattern with a copyable example whenever the
// user has 2+ refs loaded, regardless of engine selection.
function imgStudioUpdateRefWarning() {
  const box = document.getElementById('imgStudioRefWarn');
  const prompt = document.getElementById('imgStudioPrompt');
  if (!box) return;
  const refsCount = (IMG_STUDIO.refs || []).filter(r => r && r.path).length;
  // Also swap the prompt placeholder so the example the user sees in
  // empty state matches the situation. Single-ref / no-ref keep the
  // cinematic-portrait placeholder; 2+ refs swap to a multi-subject
  // example that anchors each reference explicitly.
  if (prompt) {
    // Ideogram owns the placeholder (ideoApplyComposerChrome words it per
    // Simple/Layout mode) and takes no reference images — this handler runs
    // after it in the engine onchange chain, so writing here would stomp it.
    if (typeof ideoIsActive === 'function' && ideoIsActive()) {
      /* leave Ideogram's placeholder alone */
    } else if (refsCount >= 2) {
      prompt.placeholder =
        'the man from reference 1 and the woman from reference 2 sitting at a candlelit dinner, warm tungsten light, soft bokeh, photorealistic';
    } else {
      prompt.placeholder =
        'A cinematic medium close-up of a woman in a sunlit kitchen, soft morning light through blinds, shallow depth of field, photorealistic';
    }
  }
  if (refsCount < 2) {
    box.hidden = true;
    box.innerHTML = '';
    return;
  }
  box.hidden = false;
  box.innerHTML = `
    <strong>Multi-subject tip:</strong> the model can't tell which reference is which subject
    unless you name them explicitly. Use the pattern
    <em>"the &lt;A&gt; from reference 1 and the &lt;B&gt; from reference 2 …"</em>.
    Example:
    <code>the man from reference 1 and the woman from reference 2 sitting at a candlelit dinner, warm tungsten light, photorealistic</code>.
    Without those anchors both Qwen Edit and HiDream blend the references and produce ghost-arm fusions like the one you just got.
  `;
}

// ---- Engine status pill + wall-time estimate ----
// Cache of /image/engine_status results so onchange handlers don't refetch
// on every keystroke. Refreshed when setMode('image') runs and after a
// successful Generate (in case the worker just downloaded weights).
let _IMG_ENGINE_STATUS = {};   // engine_override -> {cached, download_gb, sec_per_image}

async function imgStudioRefreshEngineStatus() {
  try {
    const r = await fetch('/image/engine_status');
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const j = await r.json();
    const map = {};
    (j.engines || []).forEach(e => { map[e.engine] = e; });
    _IMG_ENGINE_STATUS = map;
  } catch (e) {
    // Silent — pill falls back to "unknown" (dim ellipsis).
    _IMG_ENGINE_STATUS = {};
  }
  imgStudioRenderEnginePill();
  imgStudioUpdateEstimate();
  // The family-install gate lives in imgStudioUpdateValidity; refresh
  // it as soon as engine status comes back so the Generate button can
  // refuse upfront on a missing add-on (issue #12).
  if (typeof imgStudioUpdateValidity === 'function') imgStudioUpdateValidity();
}

// Open an external link reliably. A plain target="_blank" anchor does nothing
// inside Pinokio's webview, so route through the backend (`open <url>`), which
// opens the system default browser. Falls back to window.open in a plain
// browser. Call as: onclick="openExternal(this.href); return false;"
async function openExternal(url) {
  try {
    const r = await fetch('/external/open', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: 'url=' + encodeURIComponent(url),
    });
    if (r.ok) { try { phosToast && phosToast('Opening ' + (new URL(url)).host + '…'); } catch (_) {} return; }
  } catch (_) {}
  try { window.open(url, '_blank', 'noopener'); } catch (_) {}
}

// Show the one-time license/setup note while the (gated) Ideogram weights
// aren't cached yet. Driven off /image/engine_status (gated + cached + url).
function ideoUpdateSetupNote() {
  const note = document.getElementById('ideoSetupNote');
  if (!note) return;
  const engEl = document.getElementById('imgStudioEngine');
  const eng = engEl ? engEl.value : '';
  const info = (typeof _IMG_ENGINE_STATUS === 'object' && _IMG_ENGINE_STATUS)
    ? _IMG_ENGINE_STATUS[eng] : null;
  const show = !!(eng === 'ideogram4_inline' && info && !info.cached);
  note.hidden = !show;
  if (show) {
    const gb = document.getElementById('ideoSetupDlGb');
    if (gb && info.download_gb) gb.textContent = '~' + info.download_gb.toFixed(0) + ' GB';
  }
}

function imgStudioRenderEnginePill() {
  const pill = document.getElementById('imgStudioEnginePill');
  const eng = document.getElementById('imgStudioEngine');
  if (!pill || !eng) return;
  try { ideoUpdateSetupNote(); } catch (_) {}
  const v = eng.value;
  // 'auto' resolves to whatever the user saved in Settings — we don't try
  // to second-guess that here, just show a neutral pill.
  if (v === 'auto') {
    pill.dataset.state = 'unknown';
    pill.textContent = 'auto';
    pill.title = 'Uses your saved Settings engine';
    return;
  }
  const info = _IMG_ENGINE_STATUS[v];
  if (!info) {
    pill.dataset.state = 'unknown';
    pill.textContent = '…';
    pill.title = 'Checking weights…';
    return;
  }
  // Family-install gate (issue #12): the mflux family binary itself
  // needs to be installed BEFORE the user can submit. Distinct from
  // the weights-cached check below, which is about HF model downloads.
  // When family_installed is false, the engine literally can't run —
  // every other state is moot.
  if (info.family_installed === false) {
    pill.dataset.state = 'missing-engine';
    if (v === 'ideogram4_inline') {
      pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-warning-bold"/></svg>update mflux';
      pill.title = 'The Ideogram 4 engine needs mflux 0.18+ ' +
                   '(the mflux-generate-ideogram4 CLI). Update the mflux add-on.';
    } else {
      // Status pill, NOT a button — label it as a state ("not installed"), not
      // an imperative ("install ..."), or users click the pill, get the help
      // cursor from its tooltip, and nothing happens (reported on Pinokio by
      // @poppy0396). The real action lives in Pinokio's sidebar, not here.
      pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-warning-bold"/></svg>Qwen-Image-Edit not installed';
      pill.title = 'The Qwen-Image-Edit engine isn\'t installed.\n' +
                   'This pill is a status indicator — the action is in Pinokio\'s\n' +
                   'Phosphene sidebar, not in this panel: click Update there.\n' +
                   'If it\'s still missing afterwards, the sidebar also shows\n' +
                   '"Reinstall image engines (Ideogram 4 + Qwen-Edit)" — ~30 s, ~150 MB.';
    }
    return;
  }
  if (info.cached) {
    pill.dataset.state = 'ready';
    pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-check-bold"/></svg>ready';
    pill.title = 'Weights are cached locally';
  } else {
    pill.dataset.state = 'missing';
    const gb = info.download_gb || 0;
    pill.innerHTML = '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-download-simple"/></svg>' + (gb > 0 ? gb.toFixed(0) + ' GB' : 'fetch');
    pill.title = (gb > 0
      ? `Weights not cached — first run will download ~${gb.toFixed(0)} GB`
      : 'Weights not cached — first run will fetch');
  }
}

function imgStudioUpdateEstimate() {
  const out = document.getElementById('imgStudioWallEstimate');
  const btnLabel = document.getElementById('imgStudioGenBtnLabel');
  if (!out || !btnLabel) return;
  const eng = (document.getElementById('imgStudioEngine') || {}).value || 'auto';
  const n = parseInt((document.getElementById('imgStudioN') || {}).value || '1', 10);
  const safeN = Math.max(1, Math.min(8, isFinite(n) ? n : 1));
  // Wall-time = cold_start (paid once per batch) + n × per-image (denoise).
  // /image/engine_status returns both fields. The HiDream lab subprocess
  // pays ~45s cold load per batch (BF16 weights -> MLX); mflux engines
  // fold theirs into sec_per_image (cold_start_sec=0). The auto preset
  // doesn't know its target ahead of time so we hide.
  const info = _IMG_ENGINE_STATUS[eng];
  let label = 'Generate';
  if (info && !info.cached && (info.download_gb || 0) > 0) {
    label = `Generate · downloads ~${info.download_gb.toFixed(0)} GB first`;
  }
  btnLabel.textContent = label;
  if (eng === 'auto' || !info || !info.sec_per_image) {
    out.textContent = '';
    out.classList.add('dim');
    return;
  }
  const coldStart = info.cold_start_sec || 0;
  const total = coldStart + safeN * info.sec_per_image;
  out.textContent = '~' + _imgStudioFmtDuration(total) + (safeN > 1 ? ` · ${safeN} imgs` : '');
  out.classList.remove('dim');
}

function _imgStudioFmtDuration(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + 's';
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return s ? `${m}m ${s}s` : `${m}m`;
}

// ---- Recent uploads strip ----
async function imgStudioRefreshRecent() {
  const strip = document.getElementById('imgStudioRecentStrip');
  const wrap = document.getElementById('imgStudioRecentWrap');
  if (!strip || !wrap) return;
  let data;
  try { data = await api('/uploads?limit=18'); }
  catch (e) { return; }
  const items = (data && data.uploads) || [];
  if (!items.length) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = '';
  strip.innerHTML = items.map(u => `
    <img class="studio-ref-recent-thumb"
         src="${escapeHtml(u.url)}"
         data-path="${escapeHtml(u.path)}"
         title="${escapeHtml(u.name)} · ${u.size_kb} KB · ${escapeHtml(u.mtime)}"
         alt="">
  `).join('');
  strip.querySelectorAll('img').forEach(img => {
    img.addEventListener('click', () => {
      // Find the next empty slot, or replace slot 0 if all are full.
      const path = img.dataset.path;
      let target = IMG_STUDIO.refs.findIndex(r => !r || !r.path);
      if (target < 0) target = 0;
      const fname = (path || '').split('/').pop() || 'recent';
      IMG_STUDIO.refs[target] = { path, name: fname };
      imgStudioRenderSlot(target);
      imgStudioMarkRecentInUse();
    });
  });
  imgStudioMarkRecentInUse();
}

function imgStudioMarkRecentInUse() {
  const strip = document.getElementById('imgStudioRecentStrip');
  if (!strip) return;
  const usedPaths = new Set(IMG_STUDIO.refs.filter(r => r && r.path).map(r => r.path));
  strip.querySelectorAll('img.studio-ref-recent-thumb').forEach(img => {
    img.classList.toggle('in-use', usedPaths.has(img.dataset.path));
  });
}

// Click handler for the engine-status pill. Only meaningful when the pill
// is in 'missing' state — focus the Generate button so the user can hit it
// with the download-consent label already showing. (We don't kick off a
// pre-emptive `hf download` here: the queue worker already triggers one
// on first render, and a separate code path would risk drift between the
// prefetch + render configs.)
function imgStudioOnPillClick() {
  const pill = document.getElementById('imgStudioEnginePill');
  if (!pill || pill.dataset.state !== 'missing') return;
  const btn = document.getElementById('imgStudioGenBtn');
  if (!btn) return;
  btn.focus();
  try { btn.scrollIntoView({behavior:'smooth', block:'center'}); } catch (e) {}
  const s = document.getElementById('imgStudioStatus');
  if (s) s.textContent = 'Click Generate to start — first run will download the engine weights.';
}

function imgStudioClearRef(idx) {
  IMG_STUDIO.refs[idx] = null;
  imgStudioRenderSlot(idx);
}

async function imgStudioUploadRef(idx, file) {
  const fd = new FormData();
  // The panel's /upload endpoint expects the multipart field to be named
  // "image" (cgi.FieldStorage lookup); using "file" silently 400s.
  fd.append('image', file);
  try {
    const r = await fetch('/upload', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`upload ${r.status}`);
    const j = await r.json();
    if (!j.path) throw new Error('upload returned no path');
    IMG_STUDIO.refs[idx] = { path: j.path, name: file.name };
    imgStudioRenderSlot(idx);
  } catch (e) {
    const s = document.getElementById('imgStudioStatus');
    if (s) s.textContent = 'Upload failed: ' + e.message;
  }
}

// ====== Ideogram 4 — visual text-placement canvas ===========================
// The user places text/object boxes on the target frame; the serializer
// ideoBuildCaption() turns the canvas state into the EXACT structured JSON
// caption the mflux Ideogram4 verifier expects (key order matters). On
// submit (Layout mode) imgStudioGenerate posts JSON.stringify(caption) as
// the `prompt` and sets `ideo_preset`; in Simple mode the textarea text is
// the prompt verbatim (the model accepts plain strings).
//
// Coordinate model: each box stores x,y,w,h as 0..1 FRACTIONS of the frame
// (aspect-independent). An aspect change only restyles the stage; coords
// never move. bbox is [y_min,x_min,y_max,x_max] ROW-FIRST normalized ints
// 0..1000 — the single source of bbox truth is ideoRectToBbox().
const IDEO_MAX_TEXT_BOXES = 6;          // cap on text boxes
const IDEO_MIN_FRAC = 0.03;             // min box edge as a fraction (avoid 0-area)
const IDEO_HISTORY_MAX = 50;            // bounded undo

globalThis.ideoState = {
  mode: 'simple',                       // 'simple' | 'layout'
  render: 'art',                        // 'art' | 'photo'
  preset: 'V4_DEFAULT_20',
  boxes: [],                            // [{id,type,x,y,w,h,text,style,align,color,descManual}]
  selId: null,
  imagePalette: [],                     // up to 16 '#RRGGBB'
  snap: true,
  history: [],
  _editing: false,                      // a text input is focused (don't steal keys)
  _editId: null,                        // box currently being edited inline on the canvas
  fast: false,                          // Fast mode → 4-bit quantize (slower-GPU / low-RAM)
  refBridge: false,                     // Reference bridge → caption a loaded ref into the prompt (ideogram only)
  _seq: 1,
};

// Curated swatch palette for the per-element color picker (UPPERCASE hex).
const IDEO_SWATCHES = ['#FFFFFF','#0A0A0A','#F5C518','#E63946','#2F81F7','#3FB950','#8B5E3C','#5A6B3A'];

// ---- the ONE place bbox math lives (the #1 bug source) ----
function ideoRectToBbox(b){const c=v=>Math.max(0,Math.min(1,v));const x0=c(b.x),y0=c(b.y),x1=c(b.x+b.w),y1=c(b.y+b.h);const Y0=Math.round(y0*1000),X0=Math.round(x0*1000),Y1=Math.round(y1*1000),X1=Math.round(x1*1000);return [Math.min(Y0,Y1),Math.min(X0,X1),Math.max(Y0,Y1),Math.max(X0,X1)];}
// Inverse — bbox [y0,x0,y1,x1] (0..1000) → {x,y,w,h} fractions. Used to
// round-trip Raw-JSON edits back into the canvas.
function ideoBboxToRect(bbox){
  const [y0,x0,y1,x1] = bbox.map(Number);
  const yMin=Math.min(y0,y1)/1000, xMin=Math.min(x0,x1)/1000;
  const yMax=Math.max(y0,y1)/1000, xMax=Math.max(x0,x1)/1000;
  return { x:xMin, y:yMin, w:Math.max(0,xMax-xMin), h:Math.max(0,yMax-yMin) };
}

function ideoIsActive(){
  const eng = document.getElementById('imgStudioEngine');
  return !!eng && eng.value === 'ideogram4_inline';
}
function ideoInLayout(){ return ideoIsActive() && ideoState.mode === 'layout'; }

// --- Ideogram canvas portal -------------------------------------------------
// In Layout mode the placement canvas (#ideoStageWrap) moves into the big right
// stage (#ideoCanvasHost); otherwise it sits in the composer just before the
// note. Every box handler resolves #ideoStage by id, so relocating the node is
// safe — coordinates are fractional and survive the move untouched.
var _stageMode = 'edit';
var _ideoStageWasOn = false;
function ideoCanvasPortal(toStage){
  var wrap = document.getElementById('ideoStageWrap');
  if (!wrap) return;
  if (toStage){
    var host = document.getElementById('ideoCanvasHost');
    if (host && wrap.parentNode !== host) host.appendChild(wrap);
  } else {
    var note = document.getElementById('ideoStageNote');
    if (note && note.parentNode && wrap.parentNode !== note.parentNode){
      note.parentNode.insertBefore(wrap, note);
    }
  }
}
// Size the canvas to FIT the host box at the exact aspect ratio — width-bound
// on wide hosts, height-bound on short ones — so the artboard always sits with
// even margins instead of width-filling and overflowing the leftover height.
// Inline px width is cleared when the canvas portals home (the composer copy
// keeps its own responsive sizing).
function ideoFitStage(){
  var host = document.getElementById('ideoCanvasHost');
  var stage = document.getElementById('ideoStage');
  var wrap = document.getElementById('ideoStageWrap');
  if (!host || !stage || !wrap || wrap.parentNode !== host) return;
  if (!document.body.classList.contains('stage-editing')) return;
  var cs = getComputedStyle(host);
  var availW = host.clientWidth - parseFloat(cs.paddingLeft || 0) - parseFloat(cs.paddingRight || 0);
  var availH = host.clientHeight - parseFloat(cs.paddingTop || 0) - parseFloat(cs.paddingBottom || 0);
  if (availW <= 50 || availH <= 50) return;
  var ar = String(stage.style.aspectRatio || '16 / 9').split('/');
  var r = (parseFloat(ar[0]) || 16) / (parseFloat(ar[1]) || 9);
  var w = Math.min(availW, availH * r);
  stage.style.width = Math.max(220, Math.floor(w)) + 'px';
}
// Flip the right stage between the editor canvas (edit) and the rendered output.
function stageSetMode(which){
  _stageMode = (which === 'result') ? 'result' : 'edit';
  document.body.classList.toggle('stage-editing', _stageMode === 'edit');
  document.querySelectorAll('#stageModeToggle .smt-btn').forEach(function(b){
    var on = b.dataset.stageMode === _stageMode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  if (_stageMode === 'edit') ideoFitStage();   // host was hidden in result view
}
// Park the canvas in the right place for the current engine + mode.
// The edit canvas is a property of the Images workflow: leaving for
// Video/Audio/Train suspends it (classes off, canvas parked home, player
// restored) and coming back restores it — ideoState keeps the boxes, so
// nothing is lost across the round-trip.
function ideoSyncStage(){
  var on = ideoInLayout() && document.body.getAttribute('data-workflow') === 'studio';
  document.body.classList.toggle('ideo-canvas-on', on);
  ideoCanvasPortal(on);
  if (on){
    if (!_ideoStageWasOn) stageSetMode('edit');   // fresh entry → show the canvas
    var sn = document.getElementById('ideoSnapToggleStage'); if (sn) sn.checked = !!ideoState.snap;
    ideoApplyAspect(); ideoFitStage(); ideoRender();
  } else {
    if (typeof ideoExitEdit === 'function') ideoExitEdit();   // drop any live caret
    document.body.classList.remove('stage-editing');
    var st = document.getElementById('ideoStage');
    if (st) st.style.width = '';                  // composer copy sizes itself
  }
  _ideoStageWasOn = on;
}

// Show/hide the panel + re-skin the surrounding composer based on engine.
function ideoSyncVisibility(){
  const panel = document.getElementById('ideoPanel');
  if (!panel) return;
  const active = ideoIsActive();
  panel.hidden = !active;
  ideoApplyComposerChrome();
  if (active){
    ideoApplyAspect();
    ideoRender();
    ideoRefreshRaw();
  }
  ideoSyncStage();
}

// When Ideogram is active+Layout: relabel the prompt box to "Scene &
// background", hide the reference-image slots (text-to-image, no refs), and
// swap the prompt placeholder. Otherwise restore the studio's normal chrome.
function ideoApplyComposerChrome(){
  const refsWrap = document.querySelector('#studioSection .composer-refs');
  const promptLabel = document.getElementById('imgStudioPromptLabel');
  const prompt = document.getElementById('imgStudioPrompt');
  const layout = ideoInLayout();
  const active = ideoIsActive();
  // Refs stay visible for Ideogram so the user can drop one for the
  // reference bridge (vision-caption → prompt). Ideogram still can't copy
  // pixels — the bridge checkbox + ref-warning explain that. (Previously
  // the refs were force-hidden whenever Ideogram was active.)
  if (refsWrap) refsWrap.style.display = '';
  // Show/hide the bridge checkbox based on engine + whether a ref is loaded.
  if (typeof ideoSyncRefBridge === 'function') ideoSyncRefBridge();
  if (promptLabel){
    if (layout){
      promptLabel.hidden = false;
      promptLabel.textContent = 'Scene & background';
    } else {
      promptLabel.hidden = true;
    }
  }
  if (prompt){
    if (layout){
      prompt.placeholder = 'Describe the overall scene and background — e.g. "Near-black background with subtle aged-paper texture and a soft vignette."';
    } else if (active){
      prompt.placeholder = 'Describe the image you want — Ideogram 4 renders text especially well. e.g. "A bold typographic poster that reads HELLO WORLD in chunky retro letters."';
    } else {
      prompt.placeholder = 'A cinematic medium close-up of a woman in a sunlit kitchen, soft morning light through blinds, shallow depth of field, photorealistic';
    }
  }
}

// Show the Ideogram reference-bridge checkbox only when Ideogram is the
// engine AND at least one reference image is loaded. Keep the checkbox and
// ideoState.refBridge in sync. Called from ideoApplyComposerChrome (engine
// change) and imgStudioRenderSlot (refs added/removed).
function ideoSyncRefBridge(){
  const row = document.getElementById('ideoRefBridgeRow');
  const cb = document.getElementById('ideoRefBridge');
  if (!row) return;
  const active = (typeof ideoIsActive === 'function') && ideoIsActive();
  const refsCount = ((typeof IMG_STUDIO === 'object' && IMG_STUDIO.refs) || []).filter(r => r && r.path).length;
  // Bridge is simple-mode only: in Layout the prompt is a serialized JSON
  // caption, and the backend skips the bridge to avoid corrupting it.
  const show = active && refsCount >= 1
               && !((typeof ideoInLayout === 'function') && ideoInLayout());
  row.style.display = show ? 'flex' : 'none';
  if (cb) cb.checked = !!ideoState.refBridge;
}

function ideoSetMode(mode){
  ideoState.mode = (mode === 'layout') ? 'layout' : 'simple';
  document.querySelectorAll('#ideoPanel [data-ideo-mode]').forEach(b => {
    const on = b.dataset.ideoMode === ideoState.mode;
    b.classList.toggle('active', on);
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
  });
  const layoutEl = document.getElementById('ideoLayout');
  const hint = document.getElementById('ideoSimpleHint');
  if (layoutEl) layoutEl.hidden = (ideoState.mode !== 'layout');
  if (hint) hint.hidden = (ideoState.mode === 'layout');
  ideoApplyComposerChrome();
  if (ideoState.mode === 'layout'){
    ideoApplyAspect();
    ideoRender();
    ideoRefreshRaw();
  }
  ideoSyncStage();
}

// Map the studio aspect select to a CSS aspect-ratio on the stage. Boxes are
// fractional so this NEVER moves coordinates — it just restyles the frame.
function ideoApplyAspect(){
  const stage = document.getElementById('ideoStage');
  const sel = document.getElementById('imgStudioAspect');
  if (!stage || !sel) return;
  const a = (sel.value || '16:9').split(':');
  const w = parseFloat(a[0]) || 16, h = parseFloat(a[1]) || 9;
  stage.style.aspectRatio = `${w} / ${h}`;
  if (typeof ideoFitStage === 'function') ideoFitStage();   // refit the artboard
}

function ideoSetRender(r){
  ideoState.render = (r === 'photo') ? 'photo' : 'art';
  document.querySelectorAll('#ideoPanel [data-ideo-render]').forEach(b =>
    b.classList.toggle('active', b.dataset.ideoRender === ideoState.render));
  ideoRefreshRaw();
}

// ---- history (bounded undo) ----
function ideoSnapshot(){
  try {
    ideoState.history.push(JSON.stringify({ boxes: ideoState.boxes, imagePalette: ideoState.imagePalette }));
    if (ideoState.history.length > IDEO_HISTORY_MAX) ideoState.history.shift();
  } catch(e){}
  ideoUpdateUndoBtn();
}
function ideoUpdateUndoBtn(){
  const dis = ideoState.history.length === 0;
  const b = document.getElementById('ideoUndoBtn');
  if (b) b.disabled = dis;
  const bs = document.getElementById('ideoUndoBtnStage');
  if (bs) bs.disabled = dis;
}
function ideoUndo(){
  if (!ideoState.history.length) return;
  const snap = ideoState.history.pop();
  try {
    const st = JSON.parse(snap);
    ideoState.boxes = st.boxes || [];
    ideoState.imagePalette = st.imagePalette || [];
    if (!ideoState.boxes.some(x => x.id === ideoState.selId)) ideoState.selId = null;
  } catch(e){}
  ideoUpdateUndoBtn();
  ideoRender();
  ideoRenderImagePalette();
  ideoRefreshRaw();
}

// ---- box creation ----
function ideoDefaultBox(type){
  // Centered default box; text boxes are wide+short, objects squarer.
  const w = type === 'text' ? 0.6 : 0.45;
  const h = type === 'text' ? 0.12 : 0.4;
  return {
    id: 'b' + (ideoState._seq++),
    type: type === 'obj' ? 'obj' : 'text',
    x: (1 - w) / 2, y: (1 - h) / 2, w, h,
    text: '', style: 'headline', align: 'center',
    color: '#FFFFFF', descManual: '',
  };
}
function ideoInsertBox(type){
  if (type === 'text'){
    const nText = ideoState.boxes.filter(b => b.type === 'text').length;
    if (nText >= IDEO_MAX_TEXT_BOXES){
      if (typeof phosToast === 'function') phosToast(`Max ${IDEO_MAX_TEXT_BOXES} text boxes — delete one to add another`, { kind:'warning' });
      return;
    }
  }
  ideoSnapshot();
  const box = ideoDefaultBox(type);
  // Cascade new boxes slightly so stacked inserts don't perfectly overlap.
  const n = ideoState.boxes.length;
  const off = Math.min(0.18, n * 0.04);
  box.x = Math.max(0, Math.min(1 - box.w, box.x + off - 0.09));
  box.y = Math.max(0, Math.min(1 - box.h, box.y + off - 0.09));
  ideoState.boxes.push(box);
  ideoState.selId = box.id;
  ideoRender();
  ideoRefreshRaw();
  // Drop the caret straight into the new box so the user types/describes on the
  // canvas (text boxes get a text caret; object boxes get the describe field).
  ideoEnterEdit(box.id);
}

function ideoSelectedBox(){ return ideoState.boxes.find(b => b.id === ideoState.selId) || null; }

function ideoUpdateSel(patch){
  const box = ideoSelectedBox();
  if (!box) return;
  // 'style' and 'align' clear any manual desc override so the synthesized
  // prose tracks the controls; an explicit descManual edit wins.
  Object.assign(box, patch);
  ideoRenderBoxes();      // cheap re-render (don't rebuild handles mid-type)
  ideoSyncInspector();
  ideoRefreshRaw();
}

function ideoSetSelType(type){
  const box = ideoSelectedBox();
  if (!box) return;
  if (type === 'text'){
    const nText = ideoState.boxes.filter(b => b.type === 'text' && b.id !== box.id).length;
    if (nText >= IDEO_MAX_TEXT_BOXES){
      if (typeof phosToast === 'function') phosToast(`Max ${IDEO_MAX_TEXT_BOXES} text boxes`, { kind:'warning' });
      return;
    }
  }
  ideoSnapshot();
  box.type = (type === 'obj') ? 'obj' : 'text';
  ideoRender();
  ideoRefreshRaw();
}

function ideoDeleteSel(){
  const box = ideoSelectedBox();
  if (!box) return;
  ideoSnapshot();
  ideoState.boxes = ideoState.boxes.filter(b => b.id !== box.id);
  ideoState.selId = null;
  ideoState._editId = null;
  ideoState._editing = false;
  ideoRender();
  ideoRefreshRaw();
}

// ---- inline text editing on the canvas ----
// Turn the selected text box into a live-editable element: caret in the box,
// type/paste directly on the frame. Coordinates are untouched; only box.text
// changes. _editing flips so the global key handler stops stealing keystrokes.
function ideoEnterEdit(id){
  const box = ideoState.boxes.find(b => b.id === id);
  if (!box) return;
  ideoState.selId = id;
  ideoState._editId = id;
  ideoRenderBoxes();                       // re-render so this box gets contentEditable
  // Text boxes edit the .ideo-box-text; object boxes edit the .ideo-box-obj-label.
  const sel = box.type === 'text' ? '.ideo-box-text' : '.ideo-box-obj-label';
  const el = document.querySelector('.ideo-box[data-id="' + id + '"] ' + sel);
  if (el){
    el.focus();
    try {
      const range = document.createRange(); range.selectNodeContents(el); range.collapse(false);
      const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(range);
    } catch(e){}
  }
  ideoSyncInspector();
}
function ideoExitEdit(){
  if (ideoState._editId == null) return;
  ideoState._editId = null;
  ideoState._editing = false;
  ideoRenderBoxes();
  ideoSyncInspector();
  ideoRefreshRaw();
}
// Live keystroke handler: mirror the box text without a full re-render (which
// would blow away the caret), and keep the empty/warn outline + inspector field
// in sync.
function ideoBoxTextInput(ev, id){
  const box = ideoState.boxes.find(b => b.id === id);
  if (!box) return;
  box.text = ev.target.textContent || '';
  const wrap = ev.target.closest('.ideo-box');
  if (wrap) wrap.classList.toggle('warn', !box.text.trim());
  const inp = document.getElementById('ideoInspText');
  if (inp && document.activeElement !== inp) inp.value = box.text;
  ideoRefreshRaw();
}
// Object-box twin of ideoBoxTextInput: live-mirror the describe text into
// box.descManual (no full re-render, which would drop the caret) and keep the
// inspector's describe field in sync.
function ideoObjDescInput(ev, id){
  const box = ideoState.boxes.find(b => b.id === id);
  if (!box) return;
  box.descManual = ev.target.textContent || '';
  const inp = document.getElementById('ideoInspDesc');
  if (inp && document.activeElement !== inp) inp.value = box.descManual;
  ideoRefreshRaw();
}
// The floating per-box toolbar (color, alignment, delete) — appended to the
// stage and positioned above the box (or below if the box hugs the top edge),
// so common edits live right on the canvas instead of a far-off side panel.
function ideoBuildBoxToolbar(box, stage){
  const bar = document.createElement('div');
  bar.className = 'ideo-fab';
  let cx = (box.x + box.w / 2) * 100;
  cx = Math.max(15, Math.min(85, cx));     // keep it from overflowing the stage sides
  bar.style.left = cx + '%';
  if (box.y < 0.14){ bar.style.top = ((box.y + box.h) * 100) + '%'; bar.classList.add('below'); }
  else { bar.style.top = (box.y * 100) + '%'; bar.classList.add('above'); }
  // no drag, keep caret focus — but DON'T preventDefault on a <select>, or its
  // native dropdown won't open (preventDefault on pointerdown swallows it).
  bar.addEventListener('pointerdown', function(e){ e.stopPropagation(); if (!e.target.closest('select')) e.preventDefault(); });
  const A = [['left','M2 4h16M2 8h9M2 12h16'],['center','M2 4h16M5 8h10M2 12h16'],['right','M2 4h16M9 8h7M2 12h16']];
  let html = '';
  if (box.type === 'text'){
    html += '<span class="ideo-fab-sw-row">';
    IDEO_SWATCHES.forEach(hex => {
      const on = (box.color || '').toUpperCase() === hex.toUpperCase();
      html += '<button type="button" class="ideo-fab-sw' + (on ? ' on' : '') + '" data-color="' + hex + '" style="background:' + hex + '" title="' + hex + '"></button>';
    });
    html += '</span><span class="ideo-fab-sep"></span>';
    A.forEach(([al, d]) => {
      const on = box.align === al;
      html += '<button type="button" class="ideo-fab-btn' + (on ? ' on' : '') + '" data-align="' + al + '" title="Align ' + al + '"><svg viewBox="0 0 18 16" width="15" height="13"><path d="' + d + '" stroke="currentColor" stroke-width="1.5" fill="none" stroke-linecap="round"/></svg></button>';
    });
    html += '<span class="ideo-fab-sep"></span>';
    // text style picker (Headline/Subhead/Body/Caps/Script/Serif)
    const STY = [['headline','Headline'],['subhead','Subhead'],['body','Body'],['caps','Caps'],['script','Script'],['serif','Serif']];
    html += '<select class="ideo-fab-style" title="Text style">';
    STY.forEach(([v, lbl]) => {
      html += '<option value="' + v + '"' + (box.style === v ? ' selected' : '') + '>' + lbl + '</option>';
    });
    html += '</select><span class="ideo-fab-sep"></span>';
  }
  html += '<button type="button" class="ideo-fab-btn ideo-fab-del" data-del title="Delete (Backspace)"><svg viewBox="0 0 20 20" width="14" height="14"><path d="M5 6h10M8 6V4.5h4V6M7.5 6l.6 9h3.8l.6-9" stroke="currentColor" stroke-width="1.4" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
  bar.innerHTML = html;
  bar.querySelectorAll('[data-color]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); ideoState.selId = box.id; ideoUpdateSel({ color: b.dataset.color }); }));
  bar.querySelectorAll('[data-align]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); ideoState.selId = box.id; ideoUpdateSel({ align: b.dataset.align }); }));
  var st = bar.querySelector('.ideo-fab-style');
  if (st) st.addEventListener('change', function(e){ e.stopPropagation(); ideoState.selId = box.id; ideoUpdateSel({ style: st.value }); });
  const delB = bar.querySelector('[data-del]');
  if (delB) delB.addEventListener('click', e => { e.stopPropagation(); ideoState.selId = box.id; ideoDeleteSel(); });
  stage.appendChild(bar);
}

// ---- rendering ----
function ideoRender(){
  ideoRenderBoxes();
  ideoSyncInspector();
}

function ideoRenderBoxes(){
  const stage = document.getElementById('ideoStage');
  if (!stage) return;
  // Remove existing boxes + guides + floating toolbars (keep the hint node).
  stage.querySelectorAll('.ideo-box, .ideo-guide, .ideo-fab').forEach(n => n.remove());
  stage.classList.toggle('has-boxes', ideoState.boxes.length > 0);
  const overlaps = ideoComputeOverlaps();
  ideoState.boxes.forEach(box => {
    const el = document.createElement('div');
    el.className = 'ideo-box ' + (box.type === 'obj' ? 'obj' : 'text');
    if (box.id === ideoState.selId) el.className += ' selected';
    if (box.type === 'text' && !box.text.trim()) el.className += ' warn';
    if (overlaps.has(box.id)) el.className += ' warn';
    el.style.left = (box.x * 100) + '%';
    el.style.top = (box.y * 100) + '%';
    el.style.width = (box.w * 100) + '%';
    el.style.height = (box.h * 100) + '%';
    el.dataset.id = box.id;
    el.setAttribute('role', 'group');
    el.setAttribute('tabindex', '0');
    el.setAttribute('aria-label',
      (box.type === 'text' ? ('Text box: ' + (box.text || '(empty)')) : ('Object region: ' + (box.descManual || 'unlabeled'))));
    if (box.type === 'text'){
      const t = document.createElement('div');
      t.className = 'ideo-box-text al-' + box.align + ' st-' + box.style;
      t.style.color = box.color;
      t.textContent = box.text || '';
      // scale font roughly to box height so the preview reads as placement
      const px = Math.max(8, Math.min(40, Math.round(box.h * (stage.clientHeight || 300) * 0.55)));
      t.style.fontSize = px + 'px';
      t.setAttribute('data-ph', 'Type text…');
      // Inline editing: the selected-for-edit box becomes a live caret target.
      // plaintext-only means a paste drops formatting → clean caption text.
      if (box.id === ideoState._editId){
        t.contentEditable = 'plaintext-only';
        t.spellcheck = false;
        t.classList.add('editing');
        t.addEventListener('input', e => ideoBoxTextInput(e, box.id));
        t.addEventListener('focus', () => { ideoState._editing = true; });
        t.addEventListener('blur', () => { ideoExitEdit(); });
        t.addEventListener('pointerdown', e => e.stopPropagation());  // caret, not drag
        t.addEventListener('dblclick', e => e.stopPropagation());
        t.addEventListener('keydown', e => {
          e.stopPropagation();                                        // don't nudge/delete
          if (e.key === 'Escape'){ e.preventDefault(); t.blur(); }
        });
      }
      el.appendChild(t);
    } else {
      const lbl = document.createElement('div');
      lbl.className = 'ideo-box-obj-label';
      if (box.id === ideoState._editId){
        // Describe mode: the label fills the box as a live caret target. Empty
        // descManual shows the placeholder (don't fall back to 'object' here).
        lbl.contentEditable = 'plaintext-only';
        lbl.spellcheck = false;
        lbl.classList.add('editing');
        lbl.setAttribute('data-ph', 'Describe the object…');
        lbl.textContent = box.descManual || '';
        lbl.addEventListener('input', e => ideoObjDescInput(e, box.id));
        lbl.addEventListener('focus', () => { ideoState._editing = true; });
        lbl.addEventListener('blur', () => { ideoExitEdit(); });
        lbl.addEventListener('pointerdown', e => e.stopPropagation());  // caret, not drag
        lbl.addEventListener('dblclick', e => e.stopPropagation());
        lbl.addEventListener('keydown', e => {
          e.stopPropagation();                                          // don't nudge/delete
          if (e.key === 'Escape'){ e.preventDefault(); lbl.blur(); }
        });
      } else {
        lbl.textContent = box.descManual ? box.descManual.slice(0, 40) : 'object';
      }
      el.appendChild(lbl);
    }
    // delete affordance on every box (fades in on hover / when selected)
    const del = document.createElement('button');
    del.type = 'button';
    del.className = 'ideo-box-del';
    del.setAttribute('aria-label', 'Delete this box');
    del.title = 'Delete (Backspace)';
    del.innerHTML = '&times;';
    del.addEventListener('pointerdown', e => { e.stopPropagation(); e.preventDefault(); });
    del.addEventListener('click', e => { e.stopPropagation(); ideoState.selId = box.id; ideoDeleteSel(); });
    el.appendChild(del);
    // resize handles only on the selected box
    if (box.id === ideoState.selId){
      ['nw','n','ne','e','se','s','sw','w'].forEach(dir => {
        const h = document.createElement('div');
        h.className = 'ideo-handle ' + dir;
        h.dataset.handle = dir;
        el.appendChild(h);
      });
    }
    stage.appendChild(el);
    // floating toolbar for the selected box (color / align / delete)
    if (box.id === ideoState.selId){
      ideoBuildBoxToolbar(box, stage);
    }
  });
}

// Boxes whose normalized bbox rectangles intersect (non-blocking warn).
function ideoComputeOverlaps(){
  const out = new Set();
  const rs = ideoState.boxes.map(b => ({ id:b.id, bb: ideoRectToBbox(b) }));
  for (let i=0;i<rs.length;i++) for (let j=i+1;j<rs.length;j++){
    const a=rs[i].bb, b=rs[j].bb;
    const inter = !(a[2] <= b[0] || b[2] <= a[0] || a[3] <= b[1] || b[3] <= a[1]);
    if (inter){ out.add(rs[i].id); out.add(rs[j].id); }
  }
  return out;
}

// ---- inspector sync ----
function ideoSyncInspector(){
  const insp = document.getElementById('ideoInspector');
  const box = ideoSelectedBox();
  if (!insp) return;
  if (!box){ insp.hidden = true; return; }
  insp.hidden = false;
  const isText = box.type === 'text';
  // type pills
  insp.querySelectorAll('[data-ideo-type]').forEach(b =>
    b.classList.toggle('active', b.dataset.ideoType === box.type));
  // text row visible only for text boxes. When the canvas is on the right
  // stage, text/align live on the canvas (inline + floating toolbar), so the
  // inspector drops them and keeps only what the canvas can't do: type, style,
  // describe.
  const onStage = document.body.classList.contains('ideo-canvas-on');
  const textRow = document.getElementById('ideoInspTextRow');
  const styleCell = document.getElementById('ideoInspStyleCell');
  const alignCell = document.getElementById('ideoInspAlignCell');
  if (textRow) textRow.style.display = (isText && !onStage) ? '' : 'none';
  if (styleCell) styleCell.style.display = isText ? '' : 'none';
  if (alignCell) alignCell.style.display = (isText && !onStage) ? '' : 'none';
  const descLabel = document.querySelector('#ideoInspDescRow .mf-label');
  if (descLabel) descLabel.textContent = isText
    ? 'Describe it (optional — refines look)'
    : 'Describe the object (what should appear here)';
  // values
  const tInp = document.getElementById('ideoInspText');
  if (tInp && document.activeElement !== tInp) tInp.value = box.text || '';
  const dInp = document.getElementById('ideoInspDesc');
  if (dInp && document.activeElement !== dInp) dInp.value = box.descManual || '';
  const sSel = document.getElementById('ideoInspStyle');
  if (sSel) sSel.value = box.style;
  insp.querySelectorAll('[data-ideo-align]').forEach(b =>
    b.classList.toggle('active', b.dataset.ideoAlign === box.align));
  // swatches
  ideoRenderInspectorSwatches(box);
  const bb = ideoRectToBbox(box);
  const bbEl = document.getElementById('ideoInspBbox');
  if (bbEl) bbEl.textContent = 'bbox [' + bb.join(', ') + ']';
}

function ideoRenderInspectorSwatches(box){
  const wrap = document.getElementById('ideoInspSwatches');
  if (!wrap) return;
  wrap.innerHTML = '';
  IDEO_SWATCHES.forEach(hex => {
    const sw = document.createElement('button');
    sw.type = 'button';
    sw.className = 'ideo-swatch' + (ideoNormHex(box.color) === hex ? ' selected' : '');
    sw.style.background = hex;
    sw.title = hex;
    sw.setAttribute('aria-label', 'Set color ' + hex);
    sw.onclick = () => ideoUpdateSel({ color: hex });
    wrap.appendChild(sw);
  });
  // native color input for an arbitrary color
  const native = document.createElement('input');
  native.type = 'color';
  native.className = 'ideo-swatch-color';
  native.value = ideoNormHex(box.color);
  native.title = 'Custom color';
  native.setAttribute('aria-label', 'Custom text color');
  native.oninput = () => ideoUpdateSel({ color: ideoNormHex(native.value) });
  wrap.appendChild(native);
}

function ideoNormHex(s){
  s = String(s || '').trim();
  if (/^#?[0-9a-fA-F]{6}$/.test(s)){
    if (s[0] !== '#') s = '#' + s;
    return s.toUpperCase();
  }
  return '#FFFFFF';
}

// ---- image-level palette ----
function ideoAddImagePaletteColor(){
  const inp = document.getElementById('ideoPaletteColor');
  if (!inp) return;
  const hex = ideoNormHex(inp.value);
  if (ideoState.imagePalette.length >= 16){
    if (typeof phosToast === 'function') phosToast('Image palette is capped at 16 colors', { kind:'warning' });
    return;
  }
  if (!ideoState.imagePalette.includes(hex)){
    ideoSnapshot();
    ideoState.imagePalette.push(hex);
    ideoRenderImagePalette();
    ideoRefreshRaw();
  }
}
function ideoRenderImagePalette(){
  const wrap = document.getElementById('ideoImagePalette');
  const count = document.getElementById('ideoPaletteCount');
  if (count) count.textContent = ideoState.imagePalette.length ? ('(' + ideoState.imagePalette.length + ')') : '';
  if (!wrap) return;
  wrap.innerHTML = '';
  ideoState.imagePalette.forEach((hex, i) => {
    const sw = document.createElement('button');
    sw.type = 'button';
    sw.className = 'ideo-swatch removable';
    sw.style.background = hex;
    sw.title = hex + ' — click to remove';
    sw.setAttribute('aria-label', 'Remove palette color ' + hex);
    sw.onclick = () => { ideoSnapshot(); ideoState.imagePalette.splice(i,1); ideoRenderImagePalette(); ideoRefreshRaw(); };
    wrap.appendChild(sw);
  });
}

// ---- desc prose synthesis (style + align → natural-language desc) ----
function ideoSynthDesc(box){
  if (box.descManual && box.descManual.trim()) return box.descManual.trim();
  if (box.type === 'obj') return 'An object in the scene.';
  const styleWord = {
    headline: 'a bold headline',
    subhead: 'a medium-weight subheading',
    body: 'clean body text',
    caps: 'small-caps lettering',
    script: 'a flowing script',
    serif: 'an elegant serif',
  }[box.style] || 'text';
  const alignWord = { left: 'left-aligned', center: 'centered', right: 'right-aligned' }[box.align] || 'centered';
  const colorName = ideoColorName(box.color);
  return `${styleWord[0].toUpperCase()}${styleWord.slice(1)}, ${alignWord}, in ${colorName}.`;
}
function ideoColorName(hex){
  const map = {
    '#FFFFFF':'white', '#0A0A0A':'near-black', '#000000':'black',
    '#F5C518':'gold', '#E63946':'red', '#2F81F7':'blue',
    '#3FB950':'green', '#8B5E3C':'brown', '#5A6B3A':'olive green',
  };
  return map[ideoNormHex(hex)] || ('the color ' + ideoNormHex(hex));
}

// ---- THE SERIALIZER — canvas state → exact Ideogram4 caption schema ----
// Key order matters: JS object insertion order is preserved by JSON.stringify.
function ideoBuildCaption(){
  const background = (document.getElementById('imgStudioPrompt')?.value || '').trim()
                   || 'A clean, simple background.';
  const textBoxes = ideoState.boxes.filter(b => b.type === 'text');
  const nFilled = textBoxes.filter(b => b.text.trim()).length;
  const hlBits = [];
  if (nFilled) hlBits.push(`text reading ${textBoxes.filter(b=>b.text.trim()).map(b=>'"'+b.text.trim()+'"').slice(0,4).join(', ')}`);
  const hld = ideoState.render === 'photo'
    ? `A photographic image${hlBits.length ? ' featuring ' + hlBits.join('; ') : ''}.`
    : `A graphic design${hlBits.length ? ' featuring ' + hlBits.join('; ') : ''}.`;

  // root (insertion order = schema order): high_level_description,
  // style_description, compositional_deconstruction.
  const cap = {};
  cap.high_level_description = hld;

  // style_description — EXACTLY ONE of photo XOR art_style.
  const style = {};
  if (ideoState.render === 'photo'){
    // order: aesthetics, lighting, photo, medium, color_palette
    style.aesthetics = 'clean, considered, balanced composition';
    style.lighting = 'natural, well-exposed lighting';
    style.photo = 'eye-level, 50mm lens, natural depth of field';
    style.medium = 'photograph';
  } else {
    // order: aesthetics, lighting, medium, art_style, color_palette
    style.aesthetics = 'bold, graphic, high-contrast';
    style.lighting = 'even graphic lighting';
    style.medium = 'graphic_design';
    style.art_style = 'clean vector-style graphic design with strong typography and clear layout';
  }
  const imgPal = ideoState.imagePalette.map(ideoNormHex).slice(0, 16);
  if (imgPal.length) style.color_palette = imgPal;
  cap.style_description = style;

  // compositional_deconstruction — order: background, elements.
  const elements = [];
  ideoState.boxes.forEach(box => {
    if (box.type === 'text' && !box.text.trim()) return;   // drop empty text boxes
    const bbox = ideoRectToBbox(box);
    const el = {};
    el.type = box.type === 'obj' ? 'obj' : 'text';         // type first
    el.bbox = bbox;                                         // then bbox
    if (el.type === 'text') el.text = box.text.trim();      // text (text only)
    el.desc = ideoSynthDesc(box);                           // desc
    const elPal = [ideoNormHex(box.color)].slice(0, 5);     // per-element palette ≤5
    if (box.type === 'text') el.color_palette = elPal;
    elements.push(el);
  });
  cap.compositional_deconstruction = { background, elements };
  return cap;
}

// ---- a JS mirror of the caption.py verifier (so the UI can show validity
// + block a broken Raw-JSON edit before it ever reaches the server). Returns
// a list of human-readable problems; empty = valid. ----
function ideoValidateCaption(cap){
  const out = [];
  const HEX = /^#[0-9A-F]{6}$/;
  const isStr = v => typeof v === 'string';
  if (typeof cap !== 'object' || cap === null || Array.isArray(cap)){ out.push('root must be an object'); return out; }
  const rootKnown = ['high_level_description','style_description','compositional_deconstruction'];
  Object.keys(cap).forEach(k => { if (!rootKnown.includes(k)) out.push('root: unknown key ' + k); });
  if ('high_level_description' in cap && !isStr(cap.high_level_description)) out.push('high_level_description must be a string');
  // style_description
  if ('style_description' in cap){
    const sd = cap.style_description;
    if (typeof sd !== 'object' || sd === null || Array.isArray(sd)){ out.push('style_description must be an object'); }
    else {
      const hasPhoto = 'photo' in sd, hasArt = 'art_style' in sd;
      if (hasPhoto === hasArt) out.push("style_description: exactly one of 'photo' or 'art_style'");
      const order = hasPhoto
        ? ['aesthetics','lighting','photo','medium','color_palette']
        : ['aesthetics','lighting','medium','art_style','color_palette'];
      const present = Object.keys(sd).filter(k => order.includes(k));
      const want = order.filter(k => k !== 'color_palette' || 'color_palette' in sd);
      if (present.join(',') !== want.join(',')) out.push('style_description: key order ' + present.join(',') + ' != ' + want.join(','));
      Object.keys(sd).forEach(k => { if (!order.includes(k)) out.push('style_description: key ' + k + ' not allowed'); });
      order.forEach(k => { if (k !== 'color_palette' && !(k in sd)) out.push('style_description: missing ' + k); });
      if ('color_palette' in sd){
        if (!Array.isArray(sd.color_palette)) out.push('style_description.color_palette must be a list');
        else { if (sd.color_palette.length > 16) out.push('style_description.color_palette: >16 colors'); sd.color_palette.forEach(c => { if (!HEX.test(c)) out.push('style_description.color_palette: ' + c + ' not uppercase #RRGGBB'); }); }
      }
    }
  }
  // compositional_deconstruction (required)
  if (!('compositional_deconstruction' in cap)){ out.push("root: 'compositional_deconstruction' should exist"); return out; }
  const cd = cap.compositional_deconstruction;
  if (typeof cd !== 'object' || cd === null || Array.isArray(cd)){ out.push('compositional_deconstruction must be an object'); return out; }
  const cdPresent = Object.keys(cd).filter(k => ['background','elements'].includes(k));
  const cdWant = ['background','elements'].filter(k => k in cd);
  if (cdPresent.join(',') !== cdWant.join(',')) out.push('compositional_deconstruction: key order');
  Object.keys(cd).forEach(k => { if (!['background','elements'].includes(k)) out.push('compositional_deconstruction: key ' + k + ' not allowed'); });
  if (!('background' in cd)) out.push("compositional_deconstruction: 'background' should exist");
  else if (!isStr(cd.background)) out.push('compositional_deconstruction.background must be a string');
  if (!('elements' in cd)) { out.push("compositional_deconstruction: 'elements' should exist"); return out; }
  if (!Array.isArray(cd.elements)) { out.push('compositional_deconstruction.elements must be a list'); return out; }
  cd.elements.forEach((el, i) => {
    const p = 'elements[' + i + ']';
    if (typeof el !== 'object' || el === null || Array.isArray(el)){ out.push(p + ' must be an object'); return; }
    const elKnown = ['type','bbox','text','desc','color_palette'];
    Object.keys(el).forEach(k => { if (!elKnown.includes(k)) out.push(p + ': unknown key ' + k); });
    if (el.type !== 'obj' && el.type !== 'text'){ out.push(p + ": type must be 'obj' or 'text'"); return; }
    const order = el.type === 'text' ? ['type','bbox','text','desc','color_palette'] : ['type','bbox','desc','color_palette'];
    const present = Object.keys(el).filter(k => order.includes(k));
    const want = order.filter(k => (k !== 'bbox' && k !== 'color_palette') || k in el);
    if (present.join(',') !== want.join(',')) out.push(p + ': key order ' + present.join(',') + ' != ' + want.join(','));
    if (!('desc' in el)) out.push(p + ": 'desc' should exist");
    else if (!isStr(el.desc)) out.push(p + '.desc must be a string');
    if (el.type === 'text'){
      if (!('text' in el)) out.push(p + ": text elements should include 'text'");
      else if (!isStr(el.text)) out.push(p + '.text must be a string');
    }
    if ('bbox' in el){
      const bb = el.bbox;
      if (!Array.isArray(bb) || bb.length !== 4) out.push(p + '.bbox: expected [y_min,x_min,y_max,x_max]');
      else if (!bb.every(v => Number.isInteger(v))) out.push(p + '.bbox: all values must be integers');
      else {
        if (!bb.every(v => v >= 0 && v <= 1000)) out.push(p + '.bbox: values must be in [0,1000]');
        if (bb[0] > bb[2]) out.push(p + '.bbox: y_min > y_max');
        if (bb[1] > bb[3]) out.push(p + '.bbox: x_min > x_max');
      }
    }
    if ('color_palette' in el){
      if (!Array.isArray(el.color_palette)) out.push(p + '.color_palette must be a list');
      else { if (el.color_palette.length > 5) out.push(p + '.color_palette: >5 colors'); el.color_palette.forEach(c => { if (!HEX.test(c)) out.push(p + '.color_palette: ' + c + ' not uppercase #RRGGBB'); }); }
    }
  });
  return out;
}

// ---- raw JSON drawer ----
let _ideoRawDirty = false;   // user is hand-editing — don't clobber on re-render
function ideoRefreshRaw(){
  if (_ideoRawDirty) return;
  const ta = document.getElementById('ideoRawJson');
  if (!ta) return;
  let cap, problems;
  try { cap = ideoBuildCaption(); problems = ideoValidateCaption(cap); }
  catch(e){ problems = ['serialize error: ' + e.message]; }
  if (cap) ta.value = JSON.stringify(cap, null, 2);
  ideoSetValidityChip(problems);
}
function ideoSetValidityChip(problems){
  const chip = document.getElementById('ideoValidityChip');
  if (!chip) return;
  if (!problems || problems.length === 0){
    chip.dataset.ok = 'yes';
    chip.textContent = 'valid';
    chip.title = 'Caption matches the Ideogram 4 schema';
  } else {
    chip.dataset.ok = 'no';
    chip.textContent = problems.length + ' issue' + (problems.length === 1 ? '' : 's');
    chip.title = problems.slice(0, 6).join('\n');
  }
}
function ideoOnRawInput(){
  _ideoRawDirty = true;
  const ta = document.getElementById('ideoRawJson');
  const msg = document.getElementById('ideoRawMsg');
  if (!ta) return;
  let problems;
  try { problems = ideoValidateCaption(JSON.parse(ta.value)); }
  catch(e){ problems = ['invalid JSON: ' + e.message]; }
  ideoSetValidityChip(problems);
  if (msg){
    if (problems.length === 0){ msg.textContent = 'Valid — click Apply edits to load it into the canvas.'; msg.className = 'ideo-raw-msg ok'; }
    else { msg.textContent = problems[0]; msg.className = 'ideo-raw-msg err'; }
  }
}
// Round-trip raw JSON back into the canvas (ideoBboxToRect).
function ideoApplyRaw(){
  const ta = document.getElementById('ideoRawJson');
  const msg = document.getElementById('ideoRawMsg');
  if (!ta) return;
  let cap;
  try { cap = JSON.parse(ta.value); }
  catch(e){ if (msg){ msg.textContent = 'Cannot apply — invalid JSON: ' + e.message; msg.className = 'ideo-raw-msg err'; } return; }
  const problems = ideoValidateCaption(cap);
  if (problems.length){ if (msg){ msg.textContent = 'Fix first: ' + problems[0]; msg.className = 'ideo-raw-msg err'; } return; }
  ideoSnapshot();
  // background → prompt textarea
  const bg = cap.compositional_deconstruction?.background;
  if (typeof bg === 'string'){ const pe = document.getElementById('imgStudioPrompt'); if (pe) pe.value = bg; }
  // render style
  if (cap.style_description && 'photo' in cap.style_description) ideoState.render = 'photo';
  else if (cap.style_description && 'art_style' in cap.style_description) ideoState.render = 'art';
  // image palette
  ideoState.imagePalette = Array.isArray(cap.style_description?.color_palette)
    ? cap.style_description.color_palette.map(ideoNormHex).slice(0,16) : [];
  // elements → boxes
  const els = cap.compositional_deconstruction?.elements || [];
  ideoState.boxes = els.map(el => {
    const r = el.bbox ? ideoBboxToRect(el.bbox) : { x:0.2,y:0.2,w:0.6,h:0.2 };
    const color = (Array.isArray(el.color_palette) && el.color_palette[0]) ? ideoNormHex(el.color_palette[0]) : '#FFFFFF';
    return {
      id: 'b' + (ideoState._seq++),
      type: el.type === 'obj' ? 'obj' : 'text',
      x: r.x, y: r.y, w: r.w, h: r.h,
      text: el.type === 'text' ? String(el.text || '') : '',
      style: 'headline', align: 'center', color,
      descManual: String(el.desc || ''),   // keep the prose as a manual override
    };
  });
  ideoState.selId = null;
  _ideoRawDirty = false;
  ideoSetRender(ideoState.render);
  ideoRender();
  ideoRenderImagePalette();
  ideoRefreshRaw();
  if (msg){ msg.textContent = 'Applied to canvas.'; msg.className = 'ideo-raw-msg ok'; }
}

// ---- examples (literal canvas-state objects lifted from the README) ----
const IDEO_EXAMPLES = {
  poster: {  // 16:9 jazz-fest event poster
    render: 'art', preset: 'V4_TURBO_12',
    background: 'Near-black background with subtle aged paper texture.',
    imagePalette: ['#0A0A0A','#F5C518','#E63946','#FFFFFF'],
    boxes: [
      { type:'text', x:0.10, y:0.03, w:0.80, h:0.15, text:'NEW ORLEANS JAZZ FEST', style:'serif', align:'center', color:'#FFFFFF', descManual:'Bold uppercase serif headline in bright white spanning the top of the poster.' },
      { type:'obj',  x:0.30, y:0.20, w:0.40, h:0.65, text:'', style:'headline', align:'center', color:'#F5C518', descManual:'A silhouette of a trumpet player mid-performance, arm raised, dramatic pose, rendered in deep gold against the dark background.' },
      { type:'text', x:0.20, y:0.87, w:0.60, h:0.09, text:'JULY 12 . ARMSTRONG PARK', style:'body', align:'center', color:'#E63946', descManual:'Smaller red sans-serif text at the bottom with the date and venue.' },
    ],
  },
  logo: {  // 1:1 hand-sketched wordmark
    render: 'art', preset: 'V4_DEFAULT_20',
    background: 'Warm cream aged parchment filling the square frame with soft deckle edges.',
    imagePalette: ['#F3E9D2','#5A6B3A','#8B5E3C','#C4A574','#3E3228'],
    boxes: [
      { type:'text', x:0.25, y:0.18, w:0.50, h:0.20, text:'OLIVA', style:'serif', align:'center', color:'#8B5E3C', descManual:'Large hand-drawn wordmark in warm sepia-brown ink with cross-hatched shading inside the letterforms, sketched not printed.' },
      { type:'obj',  x:0.20, y:0.42, w:0.60, h:0.40, text:'', style:'headline', align:'center', color:'#5A6B3A', descManual:'Hand-sketched olive branch with leaves and ripe olives in olive-green and sepia ink cross-hatching, the central focal point.' },
      { type:'text', x:0.20, y:0.85, w:0.60, h:0.08, text:'Cold Pressed . Organic', style:'caps', align:'center', color:'#C4A574', descManual:'Small hand-sketched lettering in golden-ochre ink beneath the branch.' },
    ],
  },
  label: {  // 9:16 product label
    render: 'art', preset: 'V4_DEFAULT_20',
    background: 'Warm ivory paper with letterpress texture and margin ticks filling the tall narrow frame.',
    imagePalette: ['#F3E9D2','#5A6B3A','#3E3228'],
    boxes: [
      { type:'text', x:0.10, y:0.05, w:0.80, h:0.07, text:'EXTRA VIRGIN OLIVE OIL', style:'caps', align:'center', color:'#5A6B3A', descManual:'Hand-sketched uppercase lettering in muted olive-green ink arced gently across the top of the label.' },
      { type:'obj',  x:0.20, y:0.30, w:0.60, h:0.45, text:'', style:'headline', align:'center', color:'#5A6B3A', descManual:'Vertical botanical olive-branch illustration in olive-green and sepia ink, the central focal point.' },
      { type:'text', x:0.20, y:0.88, w:0.60, h:0.06, text:'750 ml', style:'body', align:'center', color:'#3E3228', descManual:'Tiny hand-sketched volume note in sepia ink near the bottom.' },
    ],
  },
  meme: {  // 1:1 top/bottom meme
    render: 'photo', preset: 'V4_DEFAULT_20',
    background: 'A single bold photographic scene filling the square frame.',
    imagePalette: ['#FFFFFF','#0A0A0A'],
    boxes: [
      { type:'text', x:0.05, y:0.03, w:0.90, h:0.16, text:'WHEN THE BUILD PASSES', style:'caps', align:'center', color:'#FFFFFF', descManual:'Bold white uppercase impact-style meme caption across the top with a heavy dark outline.' },
      { type:'text', x:0.05, y:0.81, w:0.90, h:0.16, text:'ON THE FIRST TRY', style:'caps', align:'center', color:'#FFFFFF', descManual:'Bold white uppercase impact-style meme caption across the bottom with a heavy dark outline.' },
    ],
  },
};
function ideoLoadExample(name){
  const ex = IDEO_EXAMPLES[name];
  if (!ex) return;
  ideoSnapshot();
  ideoState.render = ex.render;
  ideoState.preset = ex.preset;
  ideoState.imagePalette = (ex.imagePalette || []).map(ideoNormHex).slice(0,16);
  ideoState.boxes = (ex.boxes || []).map(b => Object.assign(ideoDefaultBox(b.type), b, { id: 'b' + (ideoState._seq++) }));
  ideoState.selId = null;
  const pe = document.getElementById('imgStudioPrompt');
  if (pe) pe.value = ex.background || '';
  const q = document.getElementById('ideoQuality');
  if (q) q.value = ex.preset;
  // make sure we're in layout mode so the user sees the result
  if (ideoState.mode !== 'layout') ideoSetMode('layout');
  ideoSetRender(ex.render);
  ideoApplyAspect();
  ideoRender();
  ideoRenderImagePalette();
  ideoRefreshRaw();
  if (typeof phosToast === 'function') phosToast('Loaded example — tweak the boxes, then Generate', { kind:'success' });
}

// ---- pointer interaction (create / move / resize) ----
let _ideoDrag = null;   // {mode:'create'|'move'|'resize', id, handle, startX, startY, orig, created}
function ideoStageMetrics(){
  const stage = document.getElementById('ideoStage');
  const r = stage.getBoundingClientRect();
  return { stage, r, W: r.width || 1, H: r.height || 1 };
}
function ideoEventFrac(ev, m){
  return {
    x: Math.max(0, Math.min(1, (ev.clientX - m.r.left) / m.W)),
    y: Math.max(0, Math.min(1, (ev.clientY - m.r.top) / m.H)),
  };
}
function ideoStagePointerDown(ev){
  if (!ideoInLayout()) return;
  const m = ideoStageMetrics();
  const handleEl = ev.target.closest('.ideo-handle');
  const boxEl = ev.target.closest('.ideo-box');
  const f = ideoEventFrac(ev, m);
  if (handleEl && boxEl){
    // resize the selected box
    const box = ideoState.boxes.find(b => b.id === boxEl.dataset.id);
    if (!box) return;
    ideoSnapshot();
    _ideoDrag = { mode:'resize', id:box.id, handle:handleEl.dataset.handle, startX:f.x, startY:f.y, orig:{...box}, created:false };
    ideoState.selId = box.id;
  } else if (boxEl){
    // move
    const box = ideoState.boxes.find(b => b.id === boxEl.dataset.id);
    if (!box) return;
    const wasSel = (ideoState.selId === box.id);
    ideoSnapshot();
    _ideoDrag = { mode:'move', id:box.id, startX:f.x, startY:f.y, orig:{...box}, created:false, wasSel:wasSel, moved:false };
    ideoState.selId = box.id;
    ideoRenderBoxes(); ideoSyncInspector();
  } else {
    // empty stage → start a create-drag (tiny drag becomes a click→default box)
    ideoSnapshot();
    const box = ideoDefaultBox('text');
    box.x = f.x; box.y = f.y; box.w = 0; box.h = 0;
    ideoState.boxes.push(box);
    ideoState.selId = box.id;
    _ideoDrag = { mode:'create', id:box.id, startX:f.x, startY:f.y, orig:{...box}, created:true };
  }
  try { document.getElementById('ideoStage').setPointerCapture(ev.pointerId); } catch(e){}
  ev.preventDefault();
}
function ideoStagePointerMove(ev){
  if (!_ideoDrag) return;
  const m = ideoStageMetrics();
  const f = ideoEventFrac(ev, m);
  const box = ideoState.boxes.find(b => b.id === _ideoDrag.id);
  if (!box) return;
  const dx = f.x - _ideoDrag.startX, dy = f.y - _ideoDrag.startY;
  if (Math.abs(dx) > 0.004 || Math.abs(dy) > 0.004) _ideoDrag.moved = true;
  if (_ideoDrag.mode === 'move'){
    let nx = _ideoDrag.orig.x + dx, ny = _ideoDrag.orig.y + dy;
    nx = Math.max(0, Math.min(1 - box.w, nx));
    ny = Math.max(0, Math.min(1 - box.h, ny));
    const snapped = ideoSnapMove(box, nx, ny);
    box.x = snapped.x; box.y = snapped.y;
  } else if (_ideoDrag.mode === 'resize'){
    ideoApplyResize(box, _ideoDrag.handle, _ideoDrag.orig, dx, dy);
  } else if (_ideoDrag.mode === 'create'){
    const x0 = Math.min(_ideoDrag.startX, f.x), y0 = Math.min(_ideoDrag.startY, f.y);
    box.x = x0; box.y = y0;
    box.w = Math.abs(f.x - _ideoDrag.startX);
    box.h = Math.abs(f.y - _ideoDrag.startY);
  }
  ideoRenderBoxes();
  ideoSyncInspector();
}
function ideoStagePointerUp(ev){
  if (!_ideoDrag) return;
  const drag = _ideoDrag;
  const box = ideoState.boxes.find(b => b.id === drag.id);
  if (box){
    if (drag.mode === 'create' && (box.w < IDEO_MIN_FRAC || box.h < IDEO_MIN_FRAC)){
      // tiny drag = click → snap to a default-sized box centered on the click
      const d = ideoDefaultBox('text');
      box.w = d.w; box.h = d.h;
      box.x = Math.max(0, Math.min(1 - box.w, box.x - box.w/2));
      box.y = Math.max(0, Math.min(1 - box.h, box.y - box.h/2));
    }
    // enforce minimum size on any box
    box.w = Math.max(IDEO_MIN_FRAC, Math.min(1, box.w));
    box.h = Math.max(IDEO_MIN_FRAC, Math.min(1, box.h));
    box.x = Math.max(0, Math.min(1 - box.w, box.x));
    box.y = Math.max(0, Math.min(1 - box.h, box.y));
  }
  _ideoDrag = null;
  ideoClearGuides();
  ideoRender();
  ideoRefreshRaw();
  // Enter inline edit when a text box was just drawn (drag-create is always a
  // text box), or any already-selected box was clicked without dragging
  // (Figma-style click-to-edit — text boxes type, object boxes describe).
  const enterEdit = box && (
    (drag.mode === 'create' && box.type === 'text') ||
    (drag.mode === 'move' && !drag.moved && drag.wasSel)
  );
  if (enterEdit) ideoEnterEdit(box.id);
}
function ideoApplyResize(box, handle, orig, dx, dy){
  let x0 = orig.x, y0 = orig.y, x1 = orig.x + orig.w, y1 = orig.y + orig.h;
  if (handle.includes('n')) y0 = orig.y + dy;
  if (handle.includes('s')) y1 = orig.y + orig.h + dy;
  if (handle.includes('w')) x0 = orig.x + dx;
  if (handle.includes('e')) x1 = orig.x + orig.w + dx;
  // clamp into frame; allow inversion to be resolved by min/max
  x0 = Math.max(0, Math.min(1, x0)); x1 = Math.max(0, Math.min(1, x1));
  y0 = Math.max(0, Math.min(1, y0)); y1 = Math.max(0, Math.min(1, y1));
  box.x = Math.min(x0, x1); box.y = Math.min(y0, y1);
  box.w = Math.max(IDEO_MIN_FRAC, Math.abs(x1 - x0));
  box.h = Math.max(IDEO_MIN_FRAC, Math.abs(y1 - y0));
  if (box.x + box.w > 1) box.x = 1 - box.w;
  if (box.y + box.h > 1) box.y = 1 - box.h;
}

// snapping to thirds / center / edges of other boxes, with guide lines
function ideoSnapMove(box, nx, ny){
  if (!ideoState.snap) { ideoClearGuides(); return { x:nx, y:ny }; }
  const TOL = 0.018;
  const xTargets = [0, 1/3, 0.5, 2/3, 1];
  const yTargets = [0, 1/3, 0.5, 2/3, 1];
  ideoState.boxes.forEach(o => { if (o.id !== box.id){ xTargets.push(o.x, o.x+o.w/2, o.x+o.w); yTargets.push(o.y, o.y+o.h/2, o.y+o.h); } });
  let bestX = null, bestXd = TOL, guideX = null;
  // candidate anchor points on the moving box: left, center, right
  [[nx,'l'], [nx+box.w/2,'c'], [nx+box.w,'r']].forEach(([val, kind]) => {
    xTargets.forEach(t => { const d = Math.abs(val - t); if (d < bestXd){ bestXd = d; bestX = kind === 'l' ? t : kind === 'c' ? t - box.w/2 : t - box.w; guideX = t; } });
  });
  let bestY = null, bestYd = TOL, guideY = null;
  [[ny,'t'], [ny+box.h/2,'c'], [ny+box.h,'b']].forEach(([val, kind]) => {
    yTargets.forEach(t => { const d = Math.abs(val - t); if (d < bestYd){ bestYd = d; bestY = kind === 't' ? t : kind === 'c' ? t - box.h/2 : t - box.h; guideY = t; } });
  });
  const fx = bestX != null ? Math.max(0, Math.min(1 - box.w, bestX)) : nx;
  const fy = bestY != null ? Math.max(0, Math.min(1 - box.h, bestY)) : ny;
  ideoDrawGuides(guideX, guideY);
  return { x:fx, y:fy };
}
function ideoClearGuides(){ const st = document.getElementById('ideoStage'); if (st) st.querySelectorAll('.ideo-guide').forEach(n => n.remove()); }
function ideoDrawGuides(gx, gy){
  const st = document.getElementById('ideoStage');
  if (!st) return;
  ideoClearGuides();
  if (gx != null){ const v = document.createElement('div'); v.className = 'ideo-guide v'; v.style.left = (gx*100)+'%'; st.appendChild(v); }
  if (gy != null){ const h = document.createElement('div'); h.className = 'ideo-guide h'; h.style.top = (gy*100)+'%'; st.appendChild(h); }
}

// ---- keyboard: arrows nudge, Delete, Esc, Cmd/Ctrl+Z ----
function ideoOnKeyDown(ev){
  if (!ideoInLayout()) return;
  if (ideoState._editing) return;                 // a text input is focused
  const tag = (ev.target.tagName || '').toLowerCase();
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
  // undo
  if ((ev.metaKey || ev.ctrlKey) && (ev.key === 'z' || ev.key === 'Z')){ ev.preventDefault(); ideoUndo(); return; }
  const box = ideoSelectedBox();
  if (ev.key === 'Escape'){ ideoState.selId = null; ideoRender(); return; }
  if (!box) return;
  if (ev.key === 'Delete' || ev.key === 'Backspace'){ ev.preventDefault(); ideoDeleteSel(); return; }
  const STEP = ev.shiftKey ? 0.05 : 0.005;
  let moved = false;
  if (ev.key === 'ArrowLeft'){ box.x = Math.max(0, box.x - STEP); moved = true; }
  else if (ev.key === 'ArrowRight'){ box.x = Math.min(1 - box.w, box.x + STEP); moved = true; }
  else if (ev.key === 'ArrowUp'){ box.y = Math.max(0, box.y - STEP); moved = true; }
  else if (ev.key === 'ArrowDown'){ box.y = Math.min(1 - box.h, box.y + STEP); moved = true; }
  if (moved){ ev.preventDefault(); ideoRenderBoxes(); ideoSyncInspector(); ideoRefreshRaw(); }
}

// Wire global pointer move/up (capture started in pointerdown) + keydown once.
(function ideoWireGlobalHandlers(){
  if (typeof window === 'undefined') return;
  window.addEventListener('pointermove', ideoStagePointerMove, { passive:false });
  window.addEventListener('pointerup', ideoStagePointerUp);
  window.addEventListener('pointercancel', ideoStagePointerUp);
  window.addEventListener('keydown', ideoOnKeyDown);
  window.addEventListener('resize', function(){
    if (document.body.classList.contains('ideo-canvas-on')) ideoFitStage();
  });
})();

async function imgStudioGenerate() {
  if (IMG_STUDIO.busy) return;
  const engineVal = document.getElementById('imgStudioEngine').value;
  // ----- Ideogram 4 prompt source -----
  // Layout mode: the canvas is the source — the posted `prompt` is the
  // serialized JSON caption (the textarea is the scene/background field,
  // which ideoBuildCaption already folds in). Block on invalid JSON.
  // Simple mode (and every other engine): the textarea is the prompt.
  let prompt;
  let ideoPreset = null;
  if (typeof ideoInLayout === 'function' && ideoInLayout()) {
    let caption, problems;
    try { caption = ideoBuildCaption(); problems = ideoValidateCaption(caption); }
    catch (e) { problems = ['serialize error: ' + e.message]; }
    if (problems && problems.length) {
      document.getElementById('imgStudioStatus').innerHTML =
        '<svg class="ph" aria-hidden="true" style="margin-right:4px;vertical-align:-2px"><use href="#ph-warning-fill"/></svg>' +
        'Fix the caption before generating: ' + escapeHtml(problems[0]);
      const det = document.getElementById('ideoRawDetails'); if (det) det.open = true;
      if (typeof phosToast === 'function') phosToast('Caption has ' + problems.length + ' schema issue' + (problems.length===1?'':'s'), { kind:'danger' });
      return;
    }
    const hasText = (caption.compositional_deconstruction.elements || []).some(e => e.type === 'text');
    if (!hasText && (caption.compositional_deconstruction.elements || []).length === 0) {
      document.getElementById('imgStudioStatus').textContent = 'Add at least one text or object box (or switch to Simple mode for a plain prompt).';
      return;
    }
    // Warn (non-blocking) if the user left a text box empty — it was dropped.
    const emptyText = (ideoState.boxes || []).filter(b => b.type === 'text' && !b.text.trim()).length;
    if (emptyText && typeof phosToast === 'function') phosToast(emptyText + ' empty text box' + (emptyText===1?'':'es') + ' dropped from the caption', { kind:'warning' });
    prompt = JSON.stringify(caption);
    ideoPreset = ideoState.preset || 'V4_DEFAULT_20';
  } else {
    prompt = (document.getElementById('imgStudioPrompt').value || '').trim();
    if (!prompt) {
      document.getElementById('imgStudioStatus').textContent = 'Prompt is required.';
      return;
    }
    // Simple-mode Ideogram still carries its Quality preset.
    if (engineVal === 'ideogram4_inline') ideoPreset = (typeof ideoState === 'object' && ideoState.preset) ? ideoState.preset : 'V4_DEFAULT_20';
  }
  const refs = IMG_STUDIO.refs.filter(r => r && r.path).map(r => r.path);
  const body = {
    prompt,
    n: parseInt(document.getElementById('imgStudioN').value || '1', 10),
    aspect: document.getElementById('imgStudioAspect').value || '16:9',
    seed: parseInt(document.getElementById('imgStudioSeed').value || '-1', 10),
    engine_override: engineVal,
    ideo_preset: ideoPreset,
    // Fast mode → 4-bit quantize (Ideogram only). Null otherwise so the
    // server applies its M1-safe q6 default.
    ideo_quantize: (engineVal === 'ideogram4_inline' && typeof ideoState === 'object' && ideoState.fast) ? 4 : null,
    // Reference bridge → caption a loaded ref into the Ideogram prompt. Only
    // meaningful for ideogram4_inline AND when at least one ref is present.
    ideo_reference_bridge: (engineVal === 'ideogram4_inline'
      && typeof ideoState === 'object' && ideoState.refBridge
      && IMG_STUDIO.refs.filter(r => r && r.path).length >= 1) ? 1 : null,
    refs,
  };
  // Submit through the same /queue/add endpoint video jobs use, with
  // mode='image'. The worker thread routes mode==='image' to
  // run_image_job_inner (commit 1). Progress streams into the panel
  // log + the right-rail Now/Queue/Recent cards exactly the way video
  // jobs do — no need for the Studio's own elapsed-counter or in-pane
  // result grid. The Recent tab's Photos filter (commit 2) surfaces
  // the result with thumbnail + Animate.
  IMG_STUDIO.busy = true;
  document.getElementById('imgStudioGenBtn').disabled = true;
  document.getElementById('imgStudioResults').innerHTML =
    '<div class="hint">Submitted to queue. Watch the Now / Recent tabs on the right ' +
    '(Recent → Photos for just images).</div>';
  document.getElementById('imgStudioStatus').textContent = 'Queueing…';
  try {
    const fd = new URLSearchParams();
    fd.set('mode', 'image');
    fd.set('prompt', body.prompt);
    fd.set('engine_override', body.engine_override || 'auto');
    // Ideogram 4 sampler preset — only meaningful for ideogram4_inline;
    // _build_image_engine_config reads it into ImageEngineConfig.mflux_preset.
    if (body.ideo_preset) fd.set('ideo_preset', body.ideo_preset);
    if (body.ideo_quantize) fd.set('ideo_quantize', String(body.ideo_quantize));
    // Reference bridge flag — _build_image_engine_config reads it into
    // ImageEngineConfig.ideogram_reference_bridge (ideogram branch).
    if (body.ideo_reference_bridge) fd.set('ideo_reference_bridge', '1');
    fd.set('aspect', body.aspect || '16:9');
    fd.set('n', String(body.n || 1));
    fd.set('seed', String(body.seed != null ? body.seed : -1));
    // refs is a list of paths — the existing /image/generate code
    // accepts a JSON-encoded list as a form field, and make_job()
    // mode='image' parses the same shape (commit 1).
    fd.set('refs', JSON.stringify(body.refs || []));
    // Carry user-picked LoRAs through the same `loras` form field the
    // video form uses (parse_loras_from_form on the server is shared).
    // imgStudioGenerate doesn't go through FormData(genForm), so we
    // attach it explicitly from _activeLoras here. Stacks on top of any
    // preset LoRA the chosen engine_override pinned (Lightning, etc.) —
    // run_image_job_inner does the merge.
    if (typeof _activeLoras !== 'undefined' && Array.isArray(_activeLoras) && _activeLoras.length) {
      const slim = _activeLoras.map(l => ({ path: l.path, strength: l.strength }));
      fd.set('loras', JSON.stringify(slim));
    }
    const r = await fetch('/queue/add', { method: 'POST', body: fd });
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    // The job is queued — if the Ideogram canvas is holding the right stage,
    // flip it to Result so the player (with its inline job-progress overlay) is
    // visible the moment the render starts.
    if (typeof ideoInLayout === 'function' && ideoInLayout() && typeof stageSetMode === 'function') stageSetMode('result');
    document.getElementById('imgStudioStatus').textContent =
      'Submitted. Watch Now / Recent → Photos.';
    // Non-blocking toast — the user can stay focused on the form (e.g.
    // queue a second variant) without reading the inline status line.
    if (typeof phosToast === 'function') {
      const n = body.n || 1;
      phosToast(`Queued ${n} image${n === 1 ? '' : 's'} · watch Recent → Photos`,
                { kind: 'success' });
    }
    // Briefly flash the bottom-pane Now tab so the user's eye is drawn
    // to where the live progress will appear. CSS handles the pulse;
    // we just toggle a class for a second.
    const nowTab = document.querySelector('.tabs button[data-tab="now"]');
    if (nowTab) {
      nowTab.classList.add('flash');
      setTimeout(() => nowTab.classList.remove('flash'), 1200);
    }
    // Recent uploads may now include the refs the user just touched, and
    // the worker will start downloading weights for an uncached engine —
    // refresh both so the next user click sees the fresh state.
    if (typeof imgStudioRefreshRecent === 'function') imgStudioRefreshRecent();
    setTimeout(() => {
      if (typeof imgStudioRefreshEngineStatus === 'function') imgStudioRefreshEngineStatus();
    }, 2000);
  } catch (e) {
    document.getElementById('imgStudioStatus').textContent = 'Submit failed: ' + e.message;
    if (typeof phosToast === 'function') {
      phosToast('Image submit failed: ' + (e.message || 'unknown'),
                { kind: 'danger', duration: 5000 });
    }
  } finally {
    IMG_STUDIO.busy = false;
    document.getElementById('imgStudioGenBtn').disabled = false;
  }
}

// imgStudioRefreshLibrary used to populate the right-pane Studio
// gallery. The unified Recent tab (commit 2) now covers that need —
// the function is kept as a no-op so any setMode('image') call sites
// that still reference it (defensive setMode plumbing) don't error.
async function imgStudioRefreshLibrary() {
  // intentionally empty: replaced by the unified Recent tab.
  return;
}

function imgStudioCopyPath(path) {
  if (!path) return;
  const s = document.getElementById('imgStudioStatus');
  const flash = (msg) => {
    if (!s) return;
    const prev = s.textContent;
    s.textContent = msg;
    setTimeout(() => { if (s.textContent === msg) s.textContent = prev; }, 1500);
  };
  // navigator.clipboard isn't available in non-secure contexts (HTTP origins
  // other than localhost) and may reject if the page lacks focus. Fall back
  // to the legacy execCommand path so the user still gets the path on their
  // clipboard.
  const legacy = () => {
    try {
      const ta = document.createElement('textarea');
      ta.value = path;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand('copy');
      document.body.removeChild(ta);
      flash(ok ? 'Path copied.' : 'Copy failed — select the path manually.');
    } catch (e) {
      flash('Copy failed: ' + (e.message || 'unknown'));
    }
  };
  if (!navigator.clipboard || !navigator.clipboard.writeText) { legacy(); return; }
  navigator.clipboard.writeText(path).then(() => flash('Path copied.')).catch(() => legacy());
}


// ---- published to the page --------------------------------------------------
// Inline handlers in the markup and the other files resolve these through
// the global scope; everything NOT listed here is private to this module.
Object.assign(globalThis, {
  imgStudioUpdateValidity, imgStudioWireRefSlots, imgStudioRenderSlot, imgStudioUpdateRefWarning,
  imgStudioRefreshEngineStatus, ideoUpdateSetupNote, imgStudioUpdateEstimate, imgStudioRefreshRecent,
  imgStudioOnPillClick, ideoInLayout, stageSetMode, ideoSyncStage,
  ideoSyncVisibility, ideoSyncRefBridge, ideoSetMode, ideoApplyAspect,
  ideoSetRender, ideoUndo, ideoInsertBox, ideoUpdateSel,
  ideoSetSelType, ideoDeleteSel, ideoRender, ideoAddImagePaletteColor,
  ideoBuildCaption, ideoOnRawInput, ideoApplyRaw, ideoLoadExample,
  ideoStagePointerDown, imgStudioGenerate, imgStudioRefreshLibrary, imgStudioCopyPath,
});

// --- expose inline event-handler functions to window --------------------
// index.html's dynamically-generated markup (candidate cards, modals, etc.)
// wires up interactivity with plain onclick="fn(...)" attribute strings.
// Since this file now runs as <script type="module"> (v4.9.0 restructuring),
// top-level `function`/`const` bindings are scoped to the module and are no
// longer visible as globals, so every such inline handler threw
// "fn is not defined" in the console and silently did nothing when clicked.
// Re-exporting the handlers actually referenced by inline attributes here
// restores click wiring without rewriting every template string to use
// addEventListener/delegation.
Object.assign(window, {
  imgStudioClearRef,
  openExternal
});
