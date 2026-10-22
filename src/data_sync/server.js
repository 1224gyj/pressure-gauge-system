/* =====================================================================
 * server.js — 压力表数据「Excel 自动识别与实时同步」服务
 * ---------------------------------------------------------------------
 * 职责：
 *   1) 静态托管本前端（index.html / css / js / xlsx 库）
 *   2) 监听一个指定的 Excel/CSV 数据文件，内容发生新增/修改/删除时
 *      自动解析并重建 GAUGES / READINGS，实时推送给所有在线前端
 *   3) 通过 SSE(/api/stream) 低延迟推送；REST 兜底：/api/data、/api/status
 *
 * 健壮性设计：
 *   · 文件占用：Excel 保存瞬间文件被锁(EBUSY/EPERM/EACCES) → 本次轮询跳过，
 *     保留上一版有效数据，下一轮(默认 2s)重试，绝不丢数据、绝不刷空白。
 *   · 格式校验：表头缺列 / 全部行无效 → 记 lastError，保留旧数据，不覆盖。
 *   · 更新冲突：单数据源文件，服务端为唯一权威；每次成功解析 version++，
 *     前端按 version 判断自己是否落后，落后则整体替换（服务端即真相）。
 *   · 部分写入：只在「完整解析成功」后才交换内存状态，避免读到一半的脏数据。
 *
 * 启动：  node server.js            （默认 http://localhost:8787）
 * 改端口：node server.js 9000       或 config/sync.json 里设 port
 * ===================================================================== */
'use strict';
const http = require('http');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
let XLSX;
try { XLSX = require('../../web/js/xlsx.full.min.js'); }
catch (e) { console.error('[sync] xlsx 解析库加载失败：' + e.message); process.exit(1); }

const PROJECT_ROOT = path.resolve(__dirname, '../..');
const ROOT = path.join(PROJECT_ROOT, 'web');
const { createLiveMonitor } = require('./live_monitor');
const liveMonitor = createLiveMonitor(PROJECT_ROOT,
  process.env.LIVE_CONFIG_PATH || path.join(PROJECT_ROOT,'config','live.json'));

/* ---------------- 配置 ---------------- */
const DEFAULTS = {
  port: 8787,
  source: 'data/comparison/整点数据.xlsx',   // 监听的 Excel/CSV（相对 ROOT 或绝对路径）
  seedGauges: 'config/gauges.json',        // 表计台账种子（提供名称/区域/铭牌）
  watchIntervalMs: 2000,                   // 轮询间隔
  title: '压力表数据比对与判定系统 · 实时同步服务',
};
let CFG = Object.assign({}, DEFAULTS);
try {
  const c = JSON.parse(fs.readFileSync(path.join(PROJECT_ROOT, 'config', 'sync.json'), 'utf8'));
  Object.assign(CFG, c);
} catch (e) { /* 无配置文件则用默认 */ }

/* ---------------- 工具 ---------------- */
const pad2 = n => String(n).padStart(2, '0');
const nowStr = d => {
  d = d || new Date();
  return d.getFullYear() + '-' + pad2(d.getMonth() + 1) + '-' + pad2(d.getDate())
    + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes()) + ':' + pad2(d.getSeconds());
};
function resolveSource() {
  return path.isAbsolute(CFG.source) ? CFG.source : path.join(PROJECT_ROOT, CFG.source);
}

/* ---------------- 台账种子 ---------------- */
let SEED_GAUGES = [];
try {
  const p = path.isAbsolute(CFG.seedGauges) ? CFG.seedGauges : path.join(PROJECT_ROOT, CFG.seedGauges);
  const g = JSON.parse(fs.readFileSync(p, 'utf8'));
  SEED_GAUGES = Array.isArray(g) ? g : (g.gauges || []);
} catch (e) { SEED_GAUGES = []; console.warn('[sync] 未找到台账种子 ' + CFG.seedGauges + '，未知表号将用默认建档'); }

// 限定当前前端启用的表号，避免历史比较表中的其他仪表重新建档。
const ALLOWED_GAUGES = Array.isArray(CFG.allowedGaugeIds) ? new Set(CFG.allowedGaugeIds) : null;
if (ALLOWED_GAUGES) SEED_GAUGES = SEED_GAUGES.filter(g => ALLOWED_GAUGES.has(g.id));

/* ---------------- 运行时状态 ---------------- */
const STATE = {
  version: 0,          // 成功解析次数（单调递增）
  gauges: SEED_GAUGES.map(g => ({...g})), readings: [], importLog: [], lastImport: null,
  source: resolveSource(), exists: false,
  lastModified: null, fileHash: null,
  lastCheck: null, lastChange: null, lastError: null,
  _sig: null,          // 上次处理的 mtime:size 签名
};
const clients = [];    // SSE 客户端 {res}

/* ---------------- 解析（与前端 parseRows / alignHourly / normalizeHour 对齐） ---------------- */
function normalizeHour(v) {
  if (v instanceof Date && !isNaN(v)) {
    return v.getFullYear() + '-' + pad2(v.getMonth() + 1) + '-' + pad2(v.getDate())
      + ' ' + pad2(v.getHours()) + ':00';
  }
  let s = String(v == null ? '' : v).trim().replace(/[./]/g, '-').replace('T', ' ');
  let m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2})(?::(\d{1,2}))?(?::(\d{1,2}))?/);
  if (m) return m[1] + '-' + pad2(m[2]) + '-' + pad2(m[3]) + ' ' + pad2(m[4]) + ':00';
  let md = s.match(/(\d{1,2})-(\d{1,2})\s+(\d{1,2})(?::(\d{1,2}))?/);
  if (md) return '2026-' + pad2(md[1]) + '-' + pad2(md[2]) + ' ' + pad2(md[3]) + ':00';
  return null;
}
function readGrid(file) {
  if (/\.csv$/i.test(file)) {
    const text = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    return text.split(/\r?\n/).filter(l => l.trim())
      .map(l => l.split(/[,;\t]/).map(c => c.trim().replace(/^"|"$/g, '')));
  }
  const wb = XLSX.read(fs.readFileSync(file), { type: 'buffer', cellDates: true });
  const ws = wb.Sheets[wb.SheetNames[0]];
  return XLSX.utils.sheet_to_json(ws, { header: 1, raw: true, defval: '' });
}
function parseGrid(grid) {
  if (!grid || grid.length < 2) return { err: '文件内容为空或只有表头' };
  const head = grid[0].map(h => String(h == null ? '' : h).toLowerCase());
  const ci = keys => head.findIndex(h => keys.some(k => h.includes(k)));
  const cTs = ci(['时间', 'time', '时刻', '日期']);
  const cId = ci(['表号', '编号', 'gauge', 'id', '位号']);
  const cStd = ci(['变送器', '标准', 'transmitter']);
  const cMeas = ci(['识别', '实测', '测量', '读数', 'ocr', 'meas']);
  const cRange = ci(['量程', 'range']);     // 可选：数据源自带铭牌规格
  const cAcc = ci(['精度', 'acc', '等级']); // 可选
  if (cTs < 0 || cId < 0 || cStd < 0 || cMeas < 0)
    return { err: '表头识别失败：需包含「时间 / 表号 / 变送器 / 识别读数」四类列（顺序不限）' };
  const rows = [], bad = [];
  for (let i = 1; i < grid.length; i++) {
    const ts = normalizeHour(grid[i][cTs]);
    const id = String(grid[i][cId] == null ? '' : grid[i][cId]).trim();
    const std = parseFloat(grid[i][cStd]);
    const meas = parseFloat(grid[i][cMeas]);
    if (!ts || !id || isNaN(std) || isNaN(meas)) { bad.push(i + 1); continue; }
    const row = { ts, gaugeId: id, std: +std.toFixed(4), meas: +meas.toFixed(4) };
    if (cRange >= 0) { const v = parseFloat(grid[i][cRange]); if (!isNaN(v) && v > 0) row.range = +v.toFixed(4); }
    if (cAcc >= 0)   { const v = parseFloat(grid[i][cAcc]);   if (!isNaN(v) && v > 0) row.acc = +v.toFixed(2); }
    rows.push(row);
  }
  if (rows.length === 0) return { err: '未解析到任何有效读数行（请检查时间与数值列）' };
  /* 整点对齐：同表同整点多条取均值（变送器基准值同组一致，取首条） */
  const map = new Map();
  rows.forEach(r => {
    const k = r.gaugeId + '@' + r.ts;
    if (!map.has(k)) map.set(k, { ts: r.ts, gaugeId: r.gaugeId, n: 0, sum: 0, std: r.std, range: r.range, acc: r.acc });
    const m = map.get(k); m.n++; m.sum += r.meas;
  });
  const aligned = [...map.values()].map(m => ({
    ts: m.ts, gaugeId: m.gaugeId, std: m.std,
    meas: +(m.sum / m.n).toFixed(4), range: m.range, acc: m.acc,
  }));
  return { rows: aligned, bad, raw: grid.length - 1, merged: rows.length - aligned.length };
}
function buildState(pr) {
  const gauges = SEED_GAUGES.map(g => ({ ...g }));
  const gmap = new Map(gauges.map(g => [g.id, g]));
  const newIds = [];
  const readings = [];
  pr.rows.forEach(r => {
    if (ALLOWED_GAUGES && !ALLOWED_GAUGES.has(r.gaugeId)) return;
    let g = gmap.get(r.gaugeId);
    if (!g) {
      g = {
        id: r.gaugeId, name: '(导入自动建档)', area: 'A', pos: '待补充', type: 'Y-100 弹簧管',
        range: r.range || 1.6, acc: r.acc || 1.6, status: 'active',
        installDate: r.ts.slice(0, 10),
      };
      gauges.push(g); gmap.set(g.id, g); newIds.push(g.id);
    } else if (r.range || r.acc) {
      if (r.range) g.range = r.range;   // 数据源自带规格时以数据源为准
      if (r.acc) g.acc = r.acc;
    }
    readings.push({ ts: r.ts, gaugeId: r.gaugeId, std: r.std == null ? 0 : r.std, meas: r.meas });
  });
  readings.sort((a, b) => a.ts < b.ts ? -1 : a.ts > b.ts ? 1 : 0);
  const hours = [...new Set(readings.map(r => r.ts))].sort();
  const importLog = readings.length ? [{
    ts: nowStr(), file: path.basename(STATE.source),
    raw: pr.raw, ok: readings.length, bad: pr.bad.length,
    gauges: new Set(readings.map(r => r.gaugeId)).size,
    range: hours.length ? hours[0].slice(11) + ' ~ ' + hours[hours.length - 1].slice(11) : '—',
    created: newIds, live: true,
  }] : [];
  return {
    gauges, readings, importLog,
    lastImport: readings.length ? { ts: nowStr(), file: path.basename(STATE.source) } : null,
  };
}

/* ---------------- 轮询重载 ---------------- */
function reload() {
  STATE.lastCheck = Date.now();
  const src = resolveSource();
  STATE.source = src;
  if (!fs.existsSync(src)) {
    STATE.exists = false;
    STATE.lastError = '数据源文件不存在：' + src;
    return;
  }
  STATE.exists = true;
  let st;
  try { st = fs.statSync(src); } catch (e) { STATE.lastError = '无法访问文件：' + e.code; return; }

  const sig = st.mtimeMs + ':' + st.size;
  if (STATE._sig === sig && STATE.version > 0) return;   // 未变化

  let buf;
  try { buf = fs.readFileSync(src); }
  catch (e) {
    if (['EBUSY', 'EPERM', 'EACCES'].includes(e.code)) {
      STATE.lastError = '文件被占用（Excel 正在保存？），下次轮询自动重试';
    } else {
      STATE.lastError = '读取失败：' + e.code;
    }
    return;   // 保留上一版有效数据，不覆盖、不刷空白
  }
  const hash = crypto.createHash('md5').update(buf).digest('hex');
  if (STATE.fileHash === hash && STATE.version > 0) { STATE._sig = sig; STATE.lastError = null; return; }

  let grid;
  try { grid = readGrid(src); } catch (e) { STATE.lastError = '解析失败：' + e.message; return; }
  const pr = parseGrid(grid);
  if (pr.err) { STATE.lastError = '格式校验未通过：' + pr.err; return; }

  const built = buildState(pr);
  STATE.gauges = built.gauges;
  STATE.readings = built.readings;
  STATE.importLog = built.importLog;
  STATE.lastImport = built.lastImport;
  STATE.fileHash = hash;
  STATE._sig = sig;
  STATE.lastModified = st.mtimeMs;
  STATE.lastChange = Date.now();
  STATE.lastError = null;
  STATE.version++;
  broadcast();
  console.log('[sync] v' + STATE.version + ' 解析 ' + STATE.readings.length
    + ' 条读数 / ' + STATE.gauges.length + ' 台表' + (pr.bad.length ? '（跳过 ' + pr.bad.length + ' 异常行）' : ''));
}

/* ---------------- 推送 ---------------- */
function snapshot() {
  return {
    version: STATE.version, live: true,
    gauges: STATE.gauges, readings: STATE.readings,
    importLog: STATE.importLog, lastImport: STATE.lastImport,
    source: STATE.source, exists: STATE.exists,
    lastModified: STATE.lastModified, lastError: STATE.lastError,
  };
}
function statusObj() {
  return {
    source: STATE.source, exists: STATE.exists,
    version: STATE.version, watching: true,
    watchIntervalMs: CFG.watchIntervalMs,
    lastCheck: STATE.lastCheck, lastChange: STATE.lastChange, lastModified: STATE.lastModified,
    fileHash: STATE.fileHash, lastError: STATE.lastError,
    gaugeCount: STATE.gauges.length, readingCount: STATE.readings.length,
  };
}
function broadcast() {
  if (!clients.length) return;
  const payload = 'data: ' + JSON.stringify(snapshot()) + '\n\n';
  clients.forEach(c => { try { c.res.write('event: data\n'); c.res.write(payload); } catch (e) { /* drop */ } });
}

/* ---------------- HTTP ---------------- */
const MIME = {
  '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xls': 'application/vnd.ms-excel', '.csv': 'text/csv; charset=utf-8',
  '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml',
};
function sendJSON(res, obj) {
  res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(obj));
}
function sse(req, res) {
  res.writeHead(200, {
    'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache',
    'Connection': 'keep-alive', 'Access-Control-Allow-Origin': '*',
  });
  res.write('retry: 3000\n\n');
  const client = { res };
  clients.push(client);
  res.write('event: data\n');
  res.write('data: ' + JSON.stringify(snapshot()) + '\n\n');   // 立即推当前全量
  const ping = setInterval(() => { try { res.write(': ping\n\n'); } catch (e) {} }, 15000);
  req.on('close', () => { clearInterval(ping); const i = clients.indexOf(client); if (i >= 0) clients.splice(i, 1); });
}

const server = http.createServer((req, res) => {
  const u = new URL(req.url, 'http://localhost');
  const p = u.pathname;
  if (liveMonitor.handle(req,res,u)) return;
  if (p === '/api/status') return sendJSON(res, statusObj());
  if (p === '/api/data')   return sendJSON(res, snapshot());
  if (p === '/api/stream') return sse(req, res);
  if (p === '/api/source' && req.method === 'POST') {
    let body = '';
    req.on('data', d => { body += d; if (body.length > 1e5) req.destroy(); });
    req.on('end', () => {
      try {
        const o = JSON.parse(body);
        if (o.path) { CFG.source = o.path; STATE._sig = null; }
        reload();
        sendJSON(res, { ok: true, source: resolveSource(), status: statusObj() });
      } catch (e) { sendJSON(res, { ok: false, error: e.message }); }
    });
    return;
  }
  /* 静态文件 */
  let rel = decodeURIComponent(p);
  if (rel === '/' || rel === '') rel = '/index.html';
  const fp = path.join(ROOT, rel);
  if (!fp.startsWith(ROOT)) { res.writeHead(403); res.end('forbidden'); return; }
  fs.readFile(fp, (err, data) => {
    if (err) { res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' }); res.end('404 Not Found: ' + rel); return; }
    res.writeHead(200, { 'Content-Type': MIME[path.extname(fp)] || 'application/octet-stream' });
    res.end(data);
  });
});

/* ---------------- 启动 ---------------- */
const PORT = parseInt(process.argv[2], 10) || CFG.port;
reload();                       // 首次加载
setInterval(reload, CFG.watchIntervalMs);
server.listen(PORT, () => {
  console.log('========================================================');
  console.log(' ' + CFG.title);
  console.log(' 前端地址 : http://localhost:' + PORT + '/');
  console.log(' 数据监听 : ' + STATE.source);
  console.log(' 轮询间隔 : ' + CFG.watchIntervalMs + 'ms  ·  台账种子 ' + SEED_GAUGES.length + ' 台');
  if (STATE.lastError) console.log(' 初始状态 : 警告 — ' + STATE.lastError);
  else console.log(' 初始状态 : 已加载 v' + STATE.version + '（' + STATE.readings.length + ' 条读数）');
  console.log('========================================================');
});
