#!/usr/bin/env node
// Screenshot every studio surface, dark and light, against a panel booted by
// serve.py. POSTs are blocked (nothing can queue, save or delete), and /status
// is replayed from one real snapshot so a "generating" stage can be staged
// without an engine.
//
//   NODE_PATH=$(npm root -g) node scripts/design_preview/shoot.cjs \
//       --url http://127.0.0.1:8240 --out docs/redesign/shots/after [--only stage,create]
//
// Each surface is one entry in SURFACES: a name and a function that drives the
// page into that state. Add a surface there; nothing else needs to change.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, all) => {
  if (v.startsWith('--')) a.push([v.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true]);
  return a;
}, []));
const BASE = (args.url || 'http://127.0.0.1:8240').replace(/\/$/, '');
const OUT = args.out || 'shots';
const ONLY = args.only ? String(args.only).split(',') : null;
const THEMES = args.themes ? String(args.themes).split(',') : ['dark', 'light'];
const VW = Number(args.width || 1440), VH = Number(args.height || 900);
fs.mkdirSync(OUT, { recursive: true });

const PROMPT = 'A lone fisherman in a yellow coat drifts across a still fjord at dusk, hundreds of paper lanterns rising around his boat.';

function job(id, pct, phase, extra = {}) {
  return {
    id, started_ts: (globalThis.__now || Date.now() / 1000) - 74,
    params: { mode: 't2v', engine: 'ltx', prompt: PROMPT, width: 1024, height: 576, frames: 121,
              quality: 'balanced', preset_label: 'Lantern fjord', seed: '41007' },
    progress: { pct, phase_label: phase, elapsed_sec: 74, eta_sec: 96, preview: {}, step: 3, steps: 8 },
    ...extra,
  };
}

async function settle(page, ms = 700) { await page.waitForTimeout(ms); }
async function wf(page, name) {
  await page.evaluate(n => workflowSwitch(n), name);
  await settle(page, 900);
}

const SURFACES = [
  ['create', async (page) => { await wf(page, 'manual'); }],
  ['create-selected', async (page) => {
    await wf(page, 'manual');
    const card = page.locator('.car-card').first();
    if (await card.count()) { await card.click(); await settle(page, 1200); }
  }],
  ['create-image-mode', async (page) => {
    await wf(page, 'manual');
    await page.locator('#modeGroup [data-mode="i2v"]').click();
    await settle(page);
  }],
  ['stage-generating', async (page, st) => {
    await wf(page, 'manual');
    st.running = true; st.current = job('preview-1', 34, 'Denoising video + audio · step 3 of 8');
    st.queue = [];
    await page.evaluate(() => poll()); await settle(page, 2500);
  }],
  ['stage-live-preview', async (page, st) => {
    await wf(page, 'manual');
    st.running = true; st.current = job('preview-2', 71, 'Denoising video + audio · step 6 of 8');
    st.current.progress.preview = { url: BASE + st.__still, meaningful: true };
    await page.evaluate(() => poll()); await settle(page, 2600);
  }],
  ['queue', async (page, st) => {
    await wf(page, 'manual');
    st.running = true; st.current = job('preview-3', 52, 'Denoising video + audio · step 4 of 8');
    st.queue = [1, 2, 3].map(i => ({ id: 'q' + i, status: 'queued', params: { ...job('x', 0, '').params,
      prompt: ['Blue hour over the old city, lights coming on one by one.', 'A lighthouse on a black cliff in a storm.', 'Morning mist in a pine forest clearing.'][i - 1],
      preset_label: ['Blue hour', 'The keeper', 'First light'][i - 1] } }));
    await page.evaluate(() => poll()); await settle(page, 600);
    await page.locator('.bottom-pane .tabs button[data-tab="queue"]').click(); await settle(page, 900);
  }],
  ['oneshot', async (page) => { await wf(page, 'oneshot'); }],
  ['images', async (page) => { await wf(page, 'studio'); }],
  ['storyboard', async (page) => { await wf(page, 'storyboard'); await settle(page, 800); }],
  ['editor', async (page) => {
    await page.evaluate(() => { try { localStorage.setItem('phos_ed_doc', 'sb_design_preview'); } catch (e) {} });
    await wf(page, 'editor');
    await page.evaluate(() => { if (typeof edOpenBoard === 'function') edOpenBoard('sb_design_preview'); });
    await settle(page, 2500);
  }],
  ['audio', async (page) => { await wf(page, 'audio'); }],
  ['speech', async (page) => { await wf(page, 'audio'); await page.evaluate(() => audioModeSet('speech')); await settle(page); }],
  ['pro', async (page) => { await wf(page, 'manual'); await page.evaluate(() => openProModal()); await settle(page); }],
  ['train', async (page) => { await wf(page, 'train'); }],
  ['loras', async (page) => {
    await wf(page, 'manual');
    await page.evaluate(() => { const d = document.getElementById('lorasDetails'); if (d) { d.open = true; d.scrollIntoView({ block: 'start' }); } });
    await settle(page, 900);
  }],
  ['civitai', async (page) => {
    await wf(page, 'manual');
    await page.evaluate(() => openCivitaiModal('video'));
    await settle(page, 1800);
  }],
  ['lightbox', async (page) => {
    await wf(page, 'manual');
    const card = page.locator('.car-card').first();
    if (await card.count()) { await card.click(); await settle(page, 900); }
    await page.evaluate(() => openExpandLightbox());
    await settle(page, 1500);
  }],
  ['settings', async (page) => { await wf(page, 'manual'); await page.evaluate(() => openSettingsModal()); await settle(page, 1200); }],
  ['models', async (page) => { await wf(page, 'manual'); await page.evaluate(() => openModelsModal()); await settle(page, 1200); }],
];

(async () => {
  const base = await (await fetch(BASE + '/status')).json();
  globalThis.__now = base.server_now;
  const still = (base.outputs || []).find(o => /\.png$/i.test(o.path || ''));
  const browser = await chromium.launch(args.browser ? { channel: args.browser } : {});
  const index = [];
  for (const theme of THEMES) {
    for (const [name, drive] of SURFACES) {
      if (ONLY && !ONLY.includes(name)) continue;
      const context = await browser.newContext({ viewport: { width: VW, height: VH }, deviceScaleFactor: 1,
                                                 reducedMotion: args.reduced ? 'reduce' : 'no-preference' });
      await context.addInitScript(t => { try { localStorage.setItem('phos_appearance', t); } catch (e) {} }, theme);
      await context.addCookies([{ name: 'phos_theme', value: theme, url: BASE }]);
      const st = JSON.parse(JSON.stringify(base));
      st.__still = still ? still.url : '';
      const errors = [];
      await context.route('**/*', route => {
        const req = route.request();
        if (new URL(req.url()).origin !== new URL(BASE).origin) return route.abort();
        if (req.method() === 'POST') return route.fulfill({ status: 403, json: { error: 'design preview: POST blocked' } });
        if (new URL(req.url()).pathname === '/civitai/search') {
          const imgs = (base.outputs || []).filter(o => /\.png$/i.test(o.path || ''));
          const names = ['Anamorphic Night', 'Super 8 Memories', 'Kodachrome 64', 'Neon Noir', 'Soft Morning Haze', 'Dune Light', 'Ink & Wash', 'Analog VHS'];
          const items = names.map((n, i) => ({ id: 9100 + i, name: n, creator: ['lumen', 'frameworks', 'kodaklad', 'noirhaus'][i % 4],
            downloads: 1200 + i * 731, size_kb: 120000 + i * 9000, trigger_words: [n.toLowerCase().split(' ')[0] + '_style'],
            base_model: 'LTX-2.3', preview_url: imgs.length ? BASE + imgs[i % imgs.length].url : '', preview_type: 'image',
            civitai_url: 'https://civitai.com/models/' + (9100 + i), source: 'civitai', nsfw: false }));
          return route.fulfill({ json: { items, has_more: true, next_cursor: 'x' } });
        }
        if (new URL(req.url()).pathname === '/status') {
          const body = JSON.parse(JSON.stringify(st)); delete body.__still;
          return route.fulfill({ json: body });
        }
        return route.fallback();
      });
      const page = await context.newPage();
      page.on('pageerror', e => errors.push(e.message));
      await page.goto(BASE + '/', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => typeof workflowSwitch === 'function' && typeof poll === 'function', null, { timeout: 30000 });
      // The TEST PANEL badge is the panel's own guard against mistaking a
      // throwaway instance for an install; a user never sees it, so the
      // shots do not either.
      await page.addStyleTag({ content: '.profile-badge.test-badge{display:none!important}' });
      await page.waitForTimeout(1500);
      try { await drive(page, st); } catch (e) { errors.push('drive: ' + e.message); }
      const file = `${name}-${theme}.png`;
      await page.screenshot({ path: path.join(OUT, file) });
      index.push({ name, theme, file, errors });
      console.log(file, errors.length ? 'ERR ' + errors.join(' | ').slice(0, 300) : 'ok');
      await context.close();
    }
  }
  await browser.close();
  fs.writeFileSync(path.join(OUT, 'index.json'), JSON.stringify(index, null, 2));
})();
