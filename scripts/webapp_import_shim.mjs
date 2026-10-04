// A DOM/browser shim just deep enough to `import` the real webapp/js/*.js
// modules in node — so a test can run the ACTUAL file the browser loads,
// module top-level and all, instead of extracting function text out of it
// (see scripts/extract_panel_js.py's docstring for why extraction existed
// and docs/ARCHITECTURE.md slice 3 for why imports replace it).
//
// Usage in a node -e harness, BEFORE importing any module:
//
//   import { installShim, el } from './scripts/webapp_import_shim.mjs';
//   installShim({ BOOT: {...} });          // seed page globals the module reads
//   el('someId', { value: 'x' });          // pre-seed elements a test asserts on
//   await import('../webapp/js/loras.js'); // the real file
//   globalThis.importH3Lora(...)           // published functions are now live
//
// Elements are cached by id: repeated getElementById returns the SAME
// object, so a test can read back what the function under test wrote.
// Unknown ids materialize as permissive stubs — module top-level wiring
// touches many elements a given test does not care about.

const _els = new Map();

export function el(id, props) {
  if (!_els.has(id)) {
    _els.set(id, makeEl(id));
  }
  const e = _els.get(id);
  if (props) Object.assign(e, props);
  return e;
}

function makeEl(id) {
  const listeners = {};
  return {
    id,
    hidden: false,
    disabled: false,
    checked: false,
    value: "",
    textContent: "",
    innerHTML: "",
    className: "",
    style: {},
    dataset: {},
    children: [],
    classList: {
      _set: new Set(),
      add(...c) { c.forEach((x) => this._set.add(x)); },
      remove(...c) { c.forEach((x) => this._set.delete(x)); },
      toggle(c, force) {
        const on = force === undefined ? !this._set.has(c) : force;
        on ? this._set.add(c) : this._set.delete(c);
        return on;
      },
      contains(c) { return this._set.has(c); },
    },
    addEventListener(type, fn) { (listeners[type] ||= []).push(fn); },
    removeEventListener() {},
    dispatch(type, ev) { (listeners[type] || []).forEach((f) => f(ev)); },
    click() { this.clicked = (this.clicked || 0) + 1; },
    focus() {}, blur() {},
    appendChild(c) { this.children.push(c); return c; },
    removeChild() {}, remove() {},
    setAttribute(k, v) { this[k] = v; }, getAttribute(k) { return this[k]; },
    querySelector: () => null,
    querySelectorAll: () => [],
    closest: () => null,
    getBoundingClientRect: () => ({ left: 0, top: 0, width: 0, height: 0,
                                    right: 0, bottom: 0 }),
    scrollIntoView() {},
  };
}

export function installShim(globalsSeed = {}) {
  globalThis.document = {
    getElementById: (id) => el(id),
    querySelector: () => null,
    querySelectorAll: () => [],
    createElement: (tag) => makeEl(`<${tag}>`),
    addEventListener() {}, removeEventListener() {},
    body: makeEl("<body>"),
    documentElement: makeEl("<html>"),
    head: makeEl("<head>"),
    hidden: false,
    readyState: "complete",
  };
  globalThis.window = globalThis;
  globalThis.localStorage = {
    _m: new Map(),
    getItem(k) { return this._m.has(k) ? this._m.get(k) : null; },
    setItem(k, v) { this._m.set(k, String(v)); },
    removeItem(k) { this._m.delete(k); },
  };
  globalThis.location = { href: "http://127.0.0.1/", reload() {} };
  Object.defineProperty(globalThis, "navigator", { value: { userAgent: "phosphene-test-shim", clipboard: { writeText: async () => {} } }, configurable: true, writable: true });
  globalThis.fetch = async () => ({ ok: true, status: 200,
                                    json: async () => ({}),
                                    text: async () => "" });
  globalThis.alert = (m) => (globalThis._alerts ||= []).push(String(m));
  globalThis.confirm = () => true;
  globalThis.requestAnimationFrame = (fn) => { fn(0); return 0; };
  globalThis.cancelAnimationFrame = () => {};
  globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
  globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });
  globalThis.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.MutationObserver = class { observe() {} disconnect() {} };
  globalThis.IntersectionObserver = class { observe() {} disconnect() {} };
  globalThis.Image = class { set src(_) { this.onload && this.onload(); } };
  globalThis.Audio = class { play() {} pause() {} };
  globalThis.FormData = class {
    constructor() { this.parts = []; }
    append(k, v, n) { this.parts.push([k, n ?? v]); }
    get(k) { const hit = this.parts.find(([a]) => a === k); return hit ? hit[1] : null; }
    set(k, v) { this.parts = this.parts.filter(([a]) => a !== k); this.parts.push([k, v]); }
  };
  // Page globals the modules read at import time. BOOT is the big one —
  // in the page it is the one inline line page() substitutes.
  globalThis.BOOT = globalsSeed.BOOT ?? {
    presets: {}, aspects: {}, fps: 24, model: "", profile: "test",
    tier: { key: "standard", label: "Comfortable" }, quality_times: {},
    cap_tier: "q8", h3: {}, ltx: { tiers: [], lengths: [], qualities: [] },
    storyboard: {}, engines: [], default_engine: "ltx",
    train_presets: {}, train_style_presets: {}, train_profile: {},
    train_default_preset: {}, train_min_images: 15, train_max_images: 40,
    generation_profile: {},
  };
  // Cross-module page state owned by boot.js/queue.js — modules read these
  // through the global scope, so importing one module alone needs them
  // seeded. Overridable via globalsSeed.
  globalThis.currentMode ??= "t2v";
  globalThis.filterMode ??= "all";
  globalThis.currentOutputs ??= [];
  globalThis.activePath ??= "";
  globalThis.LAST_STATUS ??= null;
  for (const [k, v] of Object.entries(globalsSeed)) {
    if (k !== "BOOT") globalThis[k] = v;
  }
  return { el };
}
