"""Escape, the Tab trap and the scroll lock cover dialogs opened with `.show` (Codex UI-08).

health.js's scaffold matched `.modal-bg.show` at startup only (no dialog is
open then) and its isVisible() did not know the `show` class, so Storyboard's
re-plan dialog and the Editor's Generate-shot dialog — both opened with
classList.add('show') — ignored Escape, let Tab walk out, and never locked the
page scroll. Runs the REAL scaffold in node against a small DOM shim.
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
const src = fs.readFileSync(process.argv[1] + '/webapp/js/health.js', 'utf8');
const a = src.indexOf('(function _phosModalScaffold');
const scaffold = src.slice(a, src.indexOf('})();', a) + 5);

const observers = [];
class CL {
  constructor(el, names) { this.el = el; this.s = new Set(names); }
  contains(c) { return this.s.has(c); }
  add(...c) { c.forEach(x => this.s.add(x)); this.el._mut('class'); }
  remove(...c) { c.forEach(x => this.s.delete(x)); this.el._mut('class'); }
  toggle(c, on) { on ? this.s.add(c) : this.s.delete(c); }
}
class El {
  constructor(tag, classes = [], attrs = {}) {
    this.tagName = tag.toUpperCase(); this.classList = new CL(this, classes);
    this.attrs = attrs; this.children = []; this.nodeType = 1; this.offsetWidth = 10;
    const self = this;
    this.style = new Proxy({}, {set(t, k, v) { t[k] = v; self._mut('style'); return true; }});
  }
  _mut(attr) { observers.filter(o => o.el === this).forEach(o => o.cb([{attributeName: attr}])); }
  add(c) { c.parent = this; this.children.push(c); return c; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  all() { return this.children.flatMap(c => [c, ...c.all()]); }
  _one(sel) {
    sel = sel.trim();
    if (/^button/.test(sel)) {
      if (this.tagName !== 'BUTTON') return false;
      const m = sel.match(/\[onclick\*="(\w+)"\]/);
      if (m) return (this.attrs.onclick || '').includes(m[1]);
      if (/\[onclick\]/.test(sel)) return 'onclick' in this.attrs;
      return true;
    }
    if (sel.startsWith('.')) return sel.slice(1).split('.').every(c => this.classList.contains(c));
    return false;
  }
  matches(sel) { return sel.split(',').some(s => this._one(s)); }
  querySelectorAll(sel) { return this.all().filter(e => e.matches(sel)); }
  querySelector(sel) { return this.querySelectorAll(sel)[0] || null; }
  contains(e) { return e === this || this.all().includes(e); }
  focus() { doc.activeElement = this; }
  click() { if (this.onclick) this.onclick(); }
}
const body = new El('body');
const listeners = {};
const doc = {
  body, activeElement: body,
  querySelectorAll: sel => body.querySelectorAll(sel),
  querySelector: sel => body.querySelector(sel),
  addEventListener: (t, f) => (listeners[t] = listeners[t] || []).push(f),
};
const outside = body.add(new El('button', [], {}));
const replan = body.add(new El('div', ['modal-bg']));
const cancel = replan.add(new El('button', [], {onclick: 'sbCloseReplan()'}));
cancel.onclick = () => replan.classList.remove('show');
const go = replan.add(new El('button', [], {onclick: 'sbReplan()'}));
const raw = body.add(new El('div', ['modal-bg']));
// sbRawModal's Close button names no close function: the fallback must close it
raw.add(new El('button', [], {onclick: "document.getElementById('sbRawModal').classList.remove('show')"}));

const ctx = {document: doc, console, Array, Set, WeakSet,
  MutationObserver: class { constructor(cb) { this.cb = cb; } observe(el) { this.el = el; observers.push(this); } }};
vm.createContext(ctx);
vm.runInContext(scaffold, ctx);
const key = (k, shift) => { const e = {key: k, shiftKey: !!shift, preventDefault() {}, stopPropagation() {}};
  (listeners.keydown || []).forEach(f => f(e)); };
const out = {};
replan.classList.add('show');                          // opened AFTER startup
out.lockedOnOpen = body.classList.contains('modal-open');
doc.activeElement = outside;
key('Tab');
out.tabLandsInside = replan.contains(doc.activeElement);
key('Escape');
out.closedByEscape = !replan.classList.contains('show');
out.unlockedOnClose = !body.classList.contains('modal-open');
replan.classList.add('show');
out.reopens = replan.classList.contains('show') && replan.style.display !== 'none';
key('Escape');
raw.classList.add('show');
key('Escape');
out.rawClosed = !raw.classList.contains('show');
raw.classList.add('show');
out.rawReopens = raw.classList.contains('show') && raw.style.display !== 'none';
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not on PATH")
class ClassOpenedDialogs(unittest.TestCase):
    def test_escape_tab_and_scroll_lock_follow_a_show_dialog(self):
        r = subprocess.run(["node", "-e", _JS, str(ROOT)], capture_output=True, text=True,
                           errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = json.loads(r.stdout)
        self.assertTrue(out["lockedOnOpen"], out)
        self.assertTrue(out["tabLandsInside"], out)
        self.assertTrue(out["closedByEscape"], out)
        self.assertTrue(out["unlockedOnClose"], out)
        self.assertTrue(out["reopens"], out)
        self.assertTrue(out["rawClosed"], out)
        self.assertTrue(out["rawReopens"], out)


if __name__ == "__main__":
    unittest.main()
