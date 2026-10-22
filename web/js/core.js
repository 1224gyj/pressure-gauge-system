/* =====================================================================
 * core.js — 运行状态 + 判定引擎 + 导航框架
 * ===================================================================== */

/* ---------------- 通用工具 ---------------- */
const $  = s => document.querySelector(s);
const $$ = s => Array.from(document.querySelectorAll(s));
const pad2 = n => String(n).padStart(2, '0');
const fmt  = (v, d = 2) => Number(v).toFixed(d);
const esc  = s => String(s == null ? '' : s).replace(/[&<>"']/g,
  c => ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));

const STATE_TEXT = { safe:'安全', warn:'预警', alarm:'报警' };
/* 压力读数、量程与偏差统一显示为 MPa。 */
const fmtRange = g => g.range + ' MPa';
const fmtMPE = g => '±' + fmt(g.range * g.acc / 100, 4) + ' MPa';
const deltaUnit = g => 'MPa';
const fmtDelta = (g, v) => (v > 0 ? '+' : '') + fmt(v, 3);
const fmtReading = (g, v) => fmt(v, 3);
const STATE_RANK = { safe:0, warn:1, alarm:2 };
const RANK_STATE = ['safe', 'warn', 'alarm'];

const ICONS = {
  dashboard: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><rect x="1.5" y="1.5" width="6.5" height="6.5" rx="1.6" stroke="currentColor" stroke-width="1.5"/><rect x="10" y="1.5" width="6.5" height="6.5" rx="1.6" stroke="currentColor" stroke-width="1.5"/><rect x="1.5" y="10" width="6.5" height="6.5" rx="1.6" stroke="currentColor" stroke-width="1.5"/><rect x="10" y="10" width="6.5" height="6.5" rx="1.6" stroke="currentColor" stroke-width="1.5"/></svg>',
  gauges: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7.2" stroke="currentColor" stroke-width="1.5"/><path d="M9 9l3.2-3.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="9" cy="9" r="1.1" fill="currentColor"/></svg>',
  import: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><path d="M9 11.5V2.5M9 2.5L5.8 5.7M9 2.5l3.2 3.2" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M2.5 11.5v2.4a1.6 1.6 0 001.6 1.6h9.8a1.6 1.6 0 001.6-1.6v-2.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
  approval: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><rect x="2" y="2" width="14" height="14" rx="2.4" stroke="currentColor" stroke-width="1.5"/><path d="M5.6 9.2l2.3 2.3 4.5-4.6" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  config: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="2.6" stroke="currentColor" stroke-width="1.5"/><path d="M9 1.8v2M9 14.2v2M1.8 9h2M14.2 9h2M3.9 3.9l1.4 1.4M12.7 12.7l1.4 1.4M14.1 3.9l-1.4 1.4M5.3 12.7l-1.4 1.4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  users: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><circle cx="6.5" cy="6" r="3" stroke="currentColor" stroke-width="1.5"/><path d="M1.8 15.2c.6-2.6 2.5-4 4.7-4s4.1 1.4 4.7 4" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/><circle cx="12.8" cy="6.6" r="2.3" stroke="currentColor" stroke-width="1.4"/><path d="M13.6 11.4c1.7.3 2.9 1.5 3.3 3.4" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  audit: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><path d="M4.5 2.5h7.2L15 5.8v9.7H4.5a1.5 1.5 0 01-1.5-1.5V4a1.5 1.5 0 011.5-1.5z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M6.2 8h5.6M6.2 11h3.6" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/></svg>',
  logout: '<svg width="16" height="16" viewBox="0 0 18 18" fill="none"><path d="M7 2.5H4a1.5 1.5 0 00-1.5 1.5v10A1.5 1.5 0 004 15.5h3M11.5 12.5l3.5-3.5-3.5-3.5M15 9H7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  bell: '<svg width="15" height="15" viewBox="0 0 18 18" fill="none"><path d="M9 2.2a4.6 4.6 0 00-4.6 4.6c0 3.4-1.3 4.6-1.3 4.6h11.8s-1.3-1.2-1.3-4.6A4.6 4.6 0 009 2.2zM7.3 14a1.8 1.8 0 003.4 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  up: '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 1.5l3.5 5h-7z" fill="currentColor"/></svg>',
  down: '<svg width="10" height="10" viewBox="0 0 10 10"><path d="M5 8.5L1.5 3.5h7z" fill="currentColor"/></svg>',
  alert: '<svg width="15" height="15" viewBox="0 0 18 18" fill="none"><path d="M9 2L1.8 15h14.4L9 2z" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/><path d="M9 7v3.4M9 12.6v.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  clock: '<svg width="15" height="15" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7" stroke="currentColor" stroke-width="1.5"/><path d="M9 5.2V9l2.6 1.6" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
  gauge: '<svg width="16" height="16" viewBox="0 0 18 18" fill="none"><circle cx="9" cy="9" r="7.2" stroke="currentColor" stroke-width="1.5"/><path d="M9 9l3.2-3.2" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  check: '<svg width="16" height="16" viewBox="0 0 18 18" fill="none"><path d="M2.5 9.5l4.2 4.2 8.8-9.4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  heatmap: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><rect x="2" y="2" width="4" height="4" rx="1" fill="currentColor" opacity=".95"/><rect x="7" y="2" width="4" height="4" rx="1" fill="currentColor" opacity=".55"/><rect x="12" y="2" width="4" height="4" rx="1" fill="currentColor" opacity=".3"/><rect x="2" y="7" width="4" height="4" rx="1" fill="currentColor" opacity=".55"/><rect x="7" y="7" width="4" height="4" rx="1" fill="currentColor" opacity=".3"/><rect x="12" y="7" width="4" height="4" rx="1" fill="currentColor" opacity=".7"/><rect x="2" y="12" width="4" height="4" rx="1" fill="currentColor" opacity=".3"/><rect x="7" y="12" width="4" height="4" rx="1" fill="currentColor" opacity=".8"/><rect x="12" y="12" width="4" height="4" rx="1" fill="currentColor" opacity=".45"/></svg>',
  trend: '<svg width="17" height="17" viewBox="0 0 18 18" fill="none"><path d="M2 15.5h14" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"/><path d="M2.5 12l4-4.5 3 2.5 5.5-6.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="15" cy="3.5" r="1.4" fill="currentColor"/></svg>',
};

/* ---------------- 运行状态 ---------------- */
let SESSION   = null;     /* { acc, name, role, area } */
let NAV       = 'dashboard';
let CUR_HOUR  = null;     /* 看板选中的整点，null = 最新 */
let TREND_ID  = null;     /* 趋势图选中的表 */
let TREND_KW  = '';       /* 趋势图表计搜索关键字 */
let TREND_MODE = 'dual';  /* 趋势图视图：'dual' 双线叠加（默认）| 'delta' 偏差带 */
let DETAIL_ID = null;     /* 详情页的表 */
let GAUGE_Q   = { kw:'', area:'', state:'' };   /* 台账筛选 */
let PENDING_IMPORT = null;                       /* 待确认的导入解析结果 */

const isAdmin = () => SESSION && SESSION.role === 'admin';

/* ---------------- 数据作用域 ---------------- */
function scopeGauges() {
  return GAUGES.filter(g =>
    g.status === 'active' && (isAdmin() || g.area === SESSION.area));
}
function visibleGauges() { /* 台账页可见：含待审批删除（展示用，置灰） */
  return GAUGES.filter(g => isAdmin() || g.area === SESSION.area);
}
function gaugeOf(id) { return GAUGES.find(g => g.id === id); }
function hoursList() {
  const ids = new Set(scopeGauges().map(g => g.id));
  return [...new Set(READINGS.filter(r => ids.has(r.gaugeId)).map(r => r.ts))].sort();
}
function readingAt(id, ts) {
  return READINGS.find(r => r.gaugeId === id && r.ts === ts);
}

/* ---------------- 判定引擎 ----------------
 * Δ  = 识别读数(meas) − 变送器(std)（MPa，带符号）
 * δ  = |Δ| / 量程 × 100%（引用误差，判定唯一依据）
 * 报警：δ > 精度等级 × alarmCoef ；预警：δ > 精度等级 × warnCoef
 */
function judgePoint(g, r) {
  const delta = r.meas - r.std;
  const pct = Math.abs(delta) / g.range * 100;
  const warnLine  = g.acc * CONFIG.warnCoef;
  const alarmLine = g.acc * CONFIG.alarmCoef;
  const state = pct > alarmLine ? 'alarm' : pct > warnLine ? 'warn' : 'safe';
  return { delta, pct, state, warnLine, alarmLine };
}
function judgeAt(id, ts) {
  const g = gaugeOf(id), r = readingAt(id, ts);
  if (!g || !r) return null;
  return { g, r, ...judgePoint(g, r) };
}
function hourWorst(ts) {
  let worst = 'safe';
  scopeGauges().forEach(g => {
    const j = judgeAt(g.id, ts);
    if (j && STATE_RANK[j.state] > STATE_RANK[worst]) worst = j.state;
  });
  return worst;
}
/* 连续异常段：从 ts 往前逐整点回溯，要求整点连续（缺数据即断） */
function abnormalRun(id, ts) {
  const hours = hoursList();
  const idx = hours.indexOf(ts);
  if (idx < 0) return null;
  let start = null, len = 0, worst = 'safe';
  for (let i = idx; i >= 0; i--) {
    if (start !== null) {
      /* 校验相邻整点间隔确为 1 小时 */
      const gap = (new Date(hours[i+1].replace(' ','T')) - new Date(hours[i].replace(' ','T'))) / 3600000;
      if (gap !== 1) break;
    }
    const j = judgeAt(id, hours[i]);
    if (!j || j.state === 'safe') break;
    start = hours[i]; len++;
    if (STATE_RANK[j.state] > STATE_RANK[worst]) worst = j.state;
  }
  return start ? { start, len, worst } : null;
}
/* 处置状态挂在「异常段」上：修好又坏会产生新段，状态自动回到未处理 */
const handleKey = (id, run) => id + '@' + run.start;
function handleOf(id, run) {
  return HANDLES[handleKey(id, run)] || { state: 'open' };
}
function setHandle(id, run, to) {
  HANDLES[handleKey(id, run)] = {
    state: to, by: SESSION.name,
    at: fmtDateTime(new Date()),
  };
  pushAudit(to === 'open' ? '重新打开' : to === 'ack' ? '异常确认' : '异常复检',
    id + ' 自 ' + run.start.slice(5) + ' 起的异常段 → ' + HANDLE_TEXT[to]);
}
const HANDLE_TEXT = { open: '未处理', ack: '已确认', done: '已复检' };
const HANDLE_BADGE = { open: 'alarm', ack: 'warn', done: 'safe' };

/* ---------------- 时间 / 新鲜度 ---------------- */
function fmtDateTime(d) {
  return d.getFullYear() + '-' + pad2(d.getMonth()+1) + '-' + pad2(d.getDate())
       + ' ' + pad2(d.getHours()) + ':' + pad2(d.getMinutes());
}
function freshnessInfo() {
  if (!LAST_IMPORT.ts) return { hrs:0, stale:false, text:'尚未导入整点比较数据', empty:true };
  const now = new Date();
  const t = new Date(LAST_IMPORT.ts.replace(' ', 'T'));
  const hrs = (now - t) / 3600000;
  const stale = hrs > 6;
  return {
    hrs, stale,
    text: hrs < 1 ? Math.round(hrs*60) + ' 分钟前' : fmt(hrs, 1) + ' 小时前',
  };
}

/* ---------------- 审计 ---------------- */
function pushAudit(action, detail) {
  AUDIT.unshift({ ts: fmtDateTime(new Date()), user: SESSION.name, action, detail });
}

/* ---------------- 菜单 / 路由 ---------------- */
const MENUS = [
  { key:'dashboard', label:'判定看板',   icon:'dashboard', roles:['admin','user'] },
  { key:'heatmap',   label:'状态热力图', icon:'heatmap',   roles:['admin','user'] },
  { key:'trend',     label:'趋势分析',   icon:'trend',     roles:['admin','user'] },
  { key:'gauges',    label:'表计台账',   icon:'gauges',    roles:['admin','user'] },
  { key:'import',    label:'数据导入',   icon:'import',    roles:['admin','user'] },
  { key:'approval',  label:'删除审批',   icon:'approval',  roles:['admin'] },
  { key:'config',    label:'阈值配置',   icon:'config',    roles:['admin'] },
  { key:'users',     label:'用户管理',   icon:'users',     roles:['admin'] },
  { key:'audit',     label:'审计日志',   icon:'audit',     roles:['admin'] },
];
const PAGE_TITLES = {
  dashboard:'判定看板', heatmap:'状态热力图', trend:'趋势分析',
  gauges:'表计台账', detail:'表计详情',
  import:'数据导入', approval:'删除审批', config:'阈值配置',
  users:'用户管理', audit:'审计日志',
};
const menuList = () => MENUS.filter(m => m.roles.includes(SESSION.role));
const pendingRequests = () => DEL_REQUESTS.filter(r => r.status === 'pending');

function renderNav() {
  const hours = hoursList();
  const cur = CUR_HOUR || hours[hours.length - 1];
  const nAlarm = typeof CameraLive!=='undefined' && CameraLive.isActive() ? CameraLive.alarmCount() : scopeGauges().filter(g => {
    const j = judgeAt(g.id, cur); return j && j.state === 'alarm';
  }).length;
  const nReq = isAdmin() ? pendingRequests().length
    : DEL_REQUESTS.filter(r => r.acc === SESSION.acc && r.status === 'pending').length;

  $('#mainNav').innerHTML =
    '<div class="nav-group">' + (isAdmin() ? '管理端 · 全部区域' : '操作端 · ' + AREAS[SESSION.area]) + '</div>'
    + menuList().map(m => {
        let tag = '';
        if (m.key === 'gauges' && nAlarm > 0)  tag = '<span class="tag red">' + nAlarm + '</span>';
        if (m.key === 'approval' && nReq > 0)  tag = '<span class="tag amber">' + nReq + '</span>';
        return '<button class="nav-item' + (NAV === m.key ? ' active' : '')
             + '" data-nav="' + m.key + '">' + ICONS[m.icon]
             + '<span>' + m.label + '</span>' + tag + '</button>';
      }).join('');

  $('#sideUser').innerHTML =
    '<div class="avatar">' + esc(SESSION.name.slice(0, 1)) + '</div>'
    + '<div><div class="su-name">' + esc(SESSION.name) + '</div>'
    + '<div class="su-role">' + (isAdmin() ? '管理员' : '操作员 · ' + AREAS[SESSION.area]) + '</div></div>'
    + '<button class="icon-btn" data-act="logout" title="退出登录">' + ICONS.logout + '</button>';
}

function renderTopbar() {
  $('#pageTitle').textContent = PAGE_TITLES[NAV] || '';
  if (typeof CameraLive!=='undefined' && CameraLive.isActive() && ['dashboard','trend'].includes(NAV)) {
    CameraLive.renderTopbar();
    return;
  }
  if (typeof Sync !== 'undefined' && Sync.isActive && Sync.isActive()) {   /* 实时同步模式：用同步药丸替换新鲜度药丸 */
    $('#topbarRight').innerHTML = Sync.pillHTML();
    return;
  }
  const f = freshnessInfo();
  $('#topbarRight').innerHTML =
    '<span class="fresh-pill ' + (f.stale ? 'stale' : 'ok') + '"><i class="dot"></i>'
    + '<span class="fp-text">' + (f.empty ? f.text : (f.stale ? '数据超期 · ' : '数据新鲜 · ') + f.text + '导入') + '</span></span>';
}

function renderMain() {
  if (typeof CameraLive!=='undefined' && CameraLive.isActive() && ['dashboard','trend'].includes(NAV)) {
    $('#pageCrumb').textContent = NAV==='trend' ? '实时监控 / 摄像头单表趋势' : '实时监控 / 摄像头单表读数';
    CameraLive.attach($('#main'), NAV);
    return;
  }
  if (typeof CameraLive!=='undefined') CameraLive.detach();
  const views = {
    dashboard: vDashboard, heatmap: vHeatmap, trend: vTrend,
    gauges: vGauges, detail: vDetail,
    import: vImport, approval: vApproval, config: vConfig,
    users: vUsers, audit: vAudit,
  };
  const crumbs = {
    dashboard:'实时监控 / 判定看板', heatmap:'实时监控 / 状态热力图', trend:'实时监控 / 趋势分析',
    gauges:'基础数据 / 表计台账',
    detail:'基础数据 / 表计台账 / ' + (DETAIL_ID || ''),
    import:'数据接入 / Excel 导入', approval:'流程中心 / 删除审批',
    config:'系统管理 / 阈值配置', users:'系统管理 / 用户管理', audit:'系统管理 / 审计日志',
  };
  $('#pageCrumb').textContent = crumbs[NAV] || '';
  if (typeof CameraLive!=='undefined' && CameraLive.isActive()) $('#pageCrumb').textContent += ' · 整点导入数据';
  $('#main').innerHTML = (views[NAV] || vDashboard)();
  $('#main').scrollTop = 0;
  window.scrollTo(0, 0);
}
function renderApp() { renderNav(); renderTopbar(); renderMain(); }
function go(nav) {
  NAV = nav;
  document.body.classList.remove('nav-open');
  renderApp();
}

/* ---------------- 弹窗 / 提示 ---------------- */
function openModal(title, bodyHTML, footHTML, width) {
  $('#modalRoot').innerHTML =
    '<div class="modal-mask" data-act="closeModal">'
    + '<div class="modal"' + (width ? ' style="width:' + width + 'px"' : '') + '>'
    + '<div class="modal-head"><h3>' + title + '</h3>'
    + '<button class="icon-btn" data-act="closeModal">'
    + '<svg width="15" height="15" viewBox="0 0 14 14"><path d="M2 2l10 10M12 2L2 12" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>'
    + '</button></div>'
    + '<div class="modal-body">' + bodyHTML + '</div>'
    + (footHTML ? '<div class="modal-foot">' + footHTML + '</div>' : '')
    + '</div></div>';
}
function closeModal() { $('#modalRoot').innerHTML = ''; }
let toastTimer = null;
function toast(msg, ok = true) {
  const el = document.createElement('div');
  el.className = 'toast ' + (ok ? 'ok' : 'err');
  el.textContent = msg;
  $('#toastRoot').appendChild(el);
  setTimeout(() => el.remove(), 2600);
}

/* ---------------- 登录 ---------------- */
function doLogin() {
  const acc = $('#loginAcc').value.trim();
  const u = USERS.find(x => x.acc === acc && x.status === '启用');
  if (!u) { $('#loginErr').textContent = '账号不存在或已停用，请使用演示账号登录'; return; }
  SESSION = { acc: u.acc, name: u.name, role: u.role, area: u.area };
  u.lastLogin = fmtDateTime(new Date());
  NAV = 'dashboard'; CUR_HOUR = null;
  TREND_ID = scopeGauges()[0] ? scopeGauges()[0].id : null;
  $('#loginView').hidden = true;
  $('#appView').hidden = false;
  pushAudit('登录系统', '登录成功（' + (isAdmin() ? '管理端' : '操作端') + '）');
  renderApp();
  toast('欢迎，' + u.name);
}
function doLogout() {
  SESSION = null;
  $('#appView').hidden = true;
  $('#loginView').hidden = false;
  $('#loginPwd').value = ''; $('#loginErr').textContent = '';
}

/* ---------------- 全局事件委托 ---------------- */
document.addEventListener('click', e => {
  const navEl = e.target.closest('[data-nav]');
  if (navEl) { go(navEl.dataset.nav); return; }

  const chip = e.target.closest('.demo-chip');
  if (chip) { $('#loginAcc').value = chip.dataset.acc; $('#loginPwd').value = 'demo'; $('#loginErr').textContent = ''; return; }

  const actEl = e.target.closest('[data-act]');
  if (!actEl) return;
  const act = actEl.dataset.act, d = actEl.dataset;
  const actions = {
    login: doLogin,
    logout: doLogout,
    openNav: () => document.body.classList.add('nav-open'),
    closeNav: () => document.body.classList.remove('nav-open'),
    /* 遮罩关窗：只有直接点在遮罩上才关；点弹窗内部（按钮除外）不关 */
    closeModal: () => {
      if (actEl.classList.contains('modal-mask') && e.target !== actEl) return;
      closeModal();
    },
    recalc: () => { renderApp(); toast('已按当前阈值完成全量重算'); },
    pickHour: () => { CUR_HOUR = d.hour; TREND_ID = d.gauge || TREND_ID; renderApp(); },
    pickCell: () => { CUR_HOUR = d.hour; TREND_ID = d.gauge || TREND_ID; go('trend'); },
    pickTrend: () => { TREND_ID = d.gauge; renderApp(); },
    setTrendMode: () => { TREND_MODE = d.mode === 'dual' ? 'dual' : 'delta'; renderMain(); },
    openDetail: () => { DETAIL_ID = d.gauge; NAV = 'detail'; renderApp(); },
    backGauges: () => go('gauges'),
    addGauge: openAddGauge,
    saveGauge,
    delGauge: () => openDeleteGauge(d.gauge),
    reqDelete: () => openDeleteRequest(d.gauge),
    confirmDelete: () => doDeleteGauge(d.gauge),
    submitDelReq: () => submitDeleteRequest(d.gauge),
    approve: () => reviewRequest(d.req, true),
    reject: () => reviewRequest(d.req, false),
    handle: () => doHandle(d.gauge, d.start, d.to),
    exportCsv,
    downloadTpl,
    syncRefresh: () => { if (typeof Sync !== 'undefined') Sync.refreshNow(); },
    pickFile: () => $('#fileInput').click(),
    confirmImport: doConfirmImport,
    saveConfig: doSaveConfig,
    addUser: openAddUser,
    saveUser: doSaveUser,
    toggleUser: () => doToggleUser(d.acc),
  };
  if (actions[act]) actions[act]();
});
document.addEventListener('change', e => {
  const t = e.target;
  if (t.id === 'trendSel')  { TREND_ID = t.value; renderMain(); }
  if (t.id === 'gqKw')      GAUGE_Q.kw = t.value;
  if (t.id === 'gqArea')  { GAUGE_Q.area = t.value; renderMain(); }
  if (t.id === 'gqState') { GAUGE_Q.state = t.value; renderMain(); }
  if (t.id === 'auditType') renderMain();
});
document.addEventListener('input', e => {
  if (e.target.id === 'gqKw') { GAUGE_Q.kw = e.target.value; renderMain(); const el = $('#gqKw'); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  if (e.target.id === 'trendKw') {
    TREND_KW = e.target.value;
    const hit = scopeGauges().filter(g =>
      (g.id + g.name + g.pos).toLowerCase().includes(TREND_KW.trim().toLowerCase()));
    if (hit.length && !hit.some(g => g.id === TREND_ID)) TREND_ID = hit[0].id;
    renderMain();
    const el = $('#trendKw');
    if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
  }
});
document.addEventListener('keydown', e => {
  if (e.key === 'Enter' && !$('#loginView').hidden) doLogin();
  if (e.key === 'Escape') closeModal();
});
