// i18n_node_stub.js — the English-only T()/TA()/TP() for node harnesses that
// run real webapp modules without webapp/js/i18n.js (docs/TRANSLATING.md).
// Same answers webapp/js/i18n.js gives when no translation is loaded.
// Plain script: vm.runInContext(src, ctx) or eval() puts the three on the
// global; require() returns them too.
var __i18nFill = function (s, v) {
  return v ? String(s).replace(/\{([A-Za-z_][A-Za-z0-9_]*)\}/g, function (m, k) {
    return Object.prototype.hasOwnProperty.call(v, k) ? String(v[k]) : m; }) : String(s);
};
var T = function (key, en, v) { return __i18nFill(en == null ? '' : en, v); };
var TA = T;
var TP = function (key, n, f, v) {
  f = f || {};
  return __i18nFill((Number(n) === 1 && f.one) ? f.one : (f.other || f.one || ''), Object.assign({ count: n }, v || {}));
};
var i18nLang = function () { return 'en'; };
var i18nAgo = function (s) { return Math.round(s) + ' s ago'; };
if (typeof globalThis !== 'undefined') { globalThis.T = T; globalThis.TA = TA; globalThis.TP = TP; globalThis.i18nLang = i18nLang; globalThis.i18nAgo = i18nAgo; }
if (typeof module !== 'undefined' && module.exports) module.exports = { T: T, TA: TA, TP: TP };
