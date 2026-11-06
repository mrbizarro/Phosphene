// Bench UI failure handling without a browser or network.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('webapp/js/bench.js', 'utf8');
const nodes = new Map();
const toasts = [];
let copyOk = false;
const context = vm.createContext({
  console, setInterval, clearInterval,
  navigator: {clipboard: {writeText: async () => { throw Error('denied'); }}},
  fetch: async () => ({status:200,json:async()=>({ok:true,text:'PB1 results'})}),
  phosToast: (msg, opts) => toasts.push([msg, opts.kind]),
  document: {
    hidden:false,
    body:{appendChild:()=>{}},
    getElementById: id => { if (!nodes.has(id)) nodes.set(id, {}); return nodes.get(id); },
    createElement: () => ({select(){},remove(){}}),
    execCommand: () => copyOk,
  },
});
vm.runInContext(source, context);
(async () => {
  vm.runInContext("BENCH.shown='test';", context);
  await context.benchCopy();
  assert.equal(toasts.pop()[1], 'warning');
  copyOk = true;
  await context.benchCopy();
  assert.deepEqual(toasts.pop(), ['Results copied', 'success']);
  context.fetch = async () => {throw Error('offline');};
  await context.benchCopy();
  assert.equal(toasts.pop()[1], 'warning');
  vm.runInContext(`_bPaintSteps({steps:[{id:'warmup',label:'Engine warm-up',status:'ready'}]},
    {running:false,last_run:'test',steps:[{id:'warmup',status:'failed',reason:'engine unavailable'}]});`, context);
  assert.match(nodes.get('benchSteps').innerHTML, /engine unavailable/);
  assert.match(nodes.get('benchSteps').innerHTML, /is-failed/);
  console.log('Bench UI: clipboard failure, fallback success, offline copy, retained failure PASS');
})().catch(e => {console.error(e);process.exitCode=1;});
