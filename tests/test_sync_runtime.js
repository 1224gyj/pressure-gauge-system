'use strict';
/* 临时测试：在 Node 沙箱里加载 sync.js，验证「探测->激活->合并」逻辑不抛错 */
const vm = require('vm');
const fs = require('fs');
const code = fs.readFileSync(__dirname + '/../web/js/sync.js', 'utf8');

let renderCount = 0;
const payload = {
  version: 3, live: true,
  gauges: [{ id: 'YB-999', name: '沙箱表', area: 'A', range: 1.6, acc: 1.6, status: 'active' }],
  readings: [{ ts: '2026-10-04 07:00', gaugeId: 'YB-999', std: 0.5, meas: 0.51 }],
  importLog: [{ ts: 't', file: 'x.xlsx', raw: 1, ok: 1, bad: 0, gauges: 1, range: '07:00', created: [] }],
  lastImport: { ts: 't', file: 'x.xlsx' },
  source: 'C:/x.xlsx', exists: true, lastError: null,
};
const sandbox = {
  GAUGES: [{ id: 'OLD', name: '旧', range: 1, acc: 1.6 }],
  READINGS: [], IMPORT_LOG: [], LAST_IMPORT: null,
  SESSION: { name: '测试', role: 'admin', area: 'A' },
  renderApp: () => { renderCount++; },
  document: { getElementById: () => null, addEventListener() {} },
  fetch: () => Promise.resolve({ ok: true, json: () => Promise.resolve(payload) }),
  EventSource: undefined,                 // 触发轮询兜底分支
  console, setTimeout, clearInterval: () => {}, setInterval: () => 0,
  JSON, Date, Math,
};
vm.createContext(sandbox);
vm.runInContext(code + '\n;globalThis.Sync = Sync;', sandbox);
const Sync = sandbox.Sync;
Sync.init();
setTimeout(() => {
  console.log('Sync typeof      :', typeof Sync);
  console.log('isActive         :', Sync.isActive());
  console.log('renderCount(before):', renderCount);
  Sync.refreshNow();
  setTimeout(() => {
    console.log('renderCount(after) :', renderCount);
    console.log('GAUGES replaced?  :', sandbox.GAUGES.length === 1 && sandbox.GAUGES[0].id === 'YB-999');
    console.log('READINGS merged?  :', sandbox.READINGS.length === 1 && sandbox.READINGS[0].gaugeId === 'YB-999');
    console.log('pillHTML ok?     :', /实时同步/.test(Sync.pillHTML()));
    console.log('sourceCard ok?   :', /实时数据源/.test(Sync.sourceCardHTML()));
  }, 60);
}, 60);
