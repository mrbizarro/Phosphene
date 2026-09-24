"""Deleted and hidden outputs do not come back from the Show-all cache (Codex UI-06).

After "Show all" (or typing a search, which loads the same list),
filteredMainOutputs() re-adds every cached path missing from the poll. Delete
and hide only cleared `currentOutputs` and polled, so a deleted clip came back
as a 404 card and a hidden one stayed visible. Runs the REAL boot.js / queue.js
functions in node against stubbed fetch, poll and DOM.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_JS = r"""
const fs = require('fs'), vm = require('vm');
function extract(file, name) {
  const s = fs.readFileSync(file, 'utf8');
  const start = s.search(new RegExp('(async )?function ' + name + '\\('));
  if (start < 0) throw new Error('missing ' + name);
  return s.slice(start, s.indexOf('\n}', start) + 2);
}
const B = process.argv[1] + '/webapp/js/boot.js', Q = process.argv[1] + '/webapp/js/queue.js';
let server = ['/o/new.mp4', '/o/old1.mp4', '/o/old2.png', '/o/hideme.mp4'];
const ctx = {
  window: {}, console, out: {}, Set, Promise, URLSearchParams, JSON, String, Array,
  currentOutputs: [], mainOutputsFilter: 'all', activePath: null,
  confirm: () => true, phosToast: () => {}, renderCarousel: () => {}, paintOutputsCount: () => {},
  outputKind: () => 'video',
  document: {getElementById: () => null},
  poll: () => { ctx.currentOutputs = [{path: server[0]}]; },   // the newest only, like /status
  fetch: async (url, opts) => {
    if (url.startsWith('/outputs')) return {ok: true, json: async () => ({outputs: server.map(p => ({path: p}))})};
    const m = url.match(/path=([^&]+)/);
    const p = m ? decodeURIComponent(m[1]) : (opts && opts.body && opts.body.get('path'));
    if (url.startsWith('/output/delete') || url.startsWith('/output/hide')) server = server.filter(x => x !== p);
    return {ok: true, json: async () => ({ok: true})};
  },
};
ctx.window = ctx;
vm.createContext(ctx);
for (const [f, n] of [[B, 'filteredMainOutputs'], [B, 'applyOutputsQuery'], [B, 'outputsLoadAll'],
                      [B, 'outputsCacheMutated'], [Q, 'deleteOutput'], [Q, 'hide']]) {
  try { vm.runInContext(extract(f, n), ctx); }
  catch (e) { if (n !== 'outputsCacheMutated') throw e; }   // absent before the fix
}
vm.runInContext(`var _outputsQuery = '';`, ctx);
(async () => {
  await vm.runInContext('outputsLoadAll()', ctx);
  ctx.poll();
  ctx.out.before = vm.runInContext('filteredMainOutputs().map(o => o.path)', ctx);
  await vm.runInContext("deleteOutput('/o/old1.mp4')", ctx);
  await new Promise(r => setTimeout(r, 20));
  ctx.out.afterDelete = vm.runInContext('filteredMainOutputs().map(o => o.path)', ctx);
  await vm.runInContext("hide('/o/hideme.mp4')", ctx);
  await new Promise(r => setTimeout(r, 20));
  ctx.out.afterHide = vm.runInContext('filteredMainOutputs().map(o => o.path)', ctx);
  process.stdout.write(JSON.stringify(ctx.out));
})().catch(e => { console.error(e); process.exit(1); });
"""


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class ShowAllCacheFollowsMutations(unittest.TestCase):
    def test_deleted_and_hidden_outputs_stay_gone(self):
        r = subprocess.run(["node", "-e", _JS, str(ROOT)], capture_output=True, text=True,
                           errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertIn("/o/old1.mp4", out["before"])
        self.assertNotIn("/o/old1.mp4", out["afterDelete"])
        self.assertIn("/o/old2.png", out["afterDelete"])
        self.assertNotIn("/o/hideme.mp4", out["afterHide"])


if __name__ == "__main__":
    unittest.main()
