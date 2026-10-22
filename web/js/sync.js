/* =====================================================================
 * sync.js — 前端实时同步客户端
 * ---------------------------------------------------------------------
 * 检测当前是否由同步服务(server.js)托管：
 *   · 是 → 进入「实时同步」模式，用 EventSource(/api/stream) 监听，
 *         数据一变立即合并 GAUGES/READINGS 并重渲染；断线自动回退轮询。
 *   · 否（如直接双击 index.html 以 file:// 打开）→ 自动降级为演示模式，
 *         不显示同步药丸，也不发起任何请求，原有手动导入照常可用。
 *
 * 合并策略：服务端为唯一权威(version 单调递增)。收到 version 大于本地
 *           已应用版本的数据包即整体替换，避免脏数据 / 半量刷新。
 * ===================================================================== */
const Sync = (function () {
  const initialGauges=typeof GAUGES!=='undefined'?GAUGES.map(g=>({...g})):[];
  let active = false;        // 是否处于实时同步模式
  let es = null;             // EventSource
  let pollTimer = null;      // 轮询兜底
  let version = 0;           // 本地已应用的版本号
  let lastSync = 0;          // 最近一次成功同步的时间戳
  let lastError = null;      // 最近一次错误（服务端下发的 lastError）
  let sourcePath = '';       // 监控的数据源文件
  let statusCache = null;    // /api/status 缓存

  const esc = s => String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  function rel(ts) {
    if (!ts) return '—';
    const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
    if (s < 2) return '刚刚';
    if (s < 60) return s + ' 秒前';
    if (s < 3600) return Math.round(s / 60) + ' 分钟前';
    return Math.round(s / 3600) + ' 小时前';
  }

  /* 探测是否在同步服务下 */
  function detect() {
    fetch('/api/status')
      .then(r => r.ok ? r.json() : Promise.reject(new Error('no server')))
      .then(s => { active = true; statusCache = s; sourcePath = s.source || ''; lastError = s.lastError || null; connect(); })
      .catch(() => { active = false; });   // 降级演示模式
  }

  function connect() {
    if (typeof EventSource === 'undefined') { startPoll(); return; }
    try {
      es = new EventSource('/api/stream');
      es.addEventListener('data', e => {
        try { apply(JSON.parse(e.data)); } catch (err) { /* ignore */ }
      });
      es.onerror = () => {                 // 连接断开 → 轮询兜底
        if (es) { try { es.close(); } catch (e) {} es = null; }
        startPoll();
      };
    } catch (e) { startPoll(); }
  }

  function startPoll() {
    if (pollTimer) return;
    pollTimer = setInterval(() => {
      fetch('/api/data').then(r => r.ok ? r.json() : null).then(d => { if (d) apply(d); }).catch(() => {});
    }, 4000);
  }

  function apply(payload) {
    if (!payload || payload.version == null) { if (payload) applyStatusFromPayload(payload); return; }
    if (payload.version <= version && version > 0) { applyStatusFromPayload(payload); return; }  // 已是最新
    version = payload.version;
    lastSync = Date.now();
    lastError = payload.lastError || null;
    sourcePath = payload.source || sourcePath;
    /* 整体替换（服务端即真相） */
    GAUGES = payload.gauges || GAUGES;
    READINGS = payload.readings || READINGS;
    // 页面刷新即可移除旧服务缓存中的历史表号，服务重启后服务端也按配置筛选。
    if (typeof ACTIVE_GAUGE_IDS !== 'undefined') {
      const allowed=new Set(ACTIVE_GAUGE_IDS);
      const selected=GAUGES.filter(g=>allowed.has(g.id));
      GAUGES=selected.length?selected:initialGauges.map(g=>({...g}));
      READINGS=READINGS.filter(r=>allowed.has(r.gaugeId));
    }
    if (payload.importLog) IMPORT_LOG = payload.importLog;
    if (payload.lastImport) LAST_IMPORT = payload.lastImport;
    if (typeof ACTIVE_GAUGE_IDS !== 'undefined' && !READINGS.length) {
      IMPORT_LOG=[];
      LAST_IMPORT={ts:'',file:'尚未导入整点比较数据'};
    }
    if (typeof SESSION !== 'undefined' && SESSION) renderApp();   // 已登录则立即刷新界面
    updatePill();
  }

  function applyStatusFromPayload(p) { lastError = p.lastError || lastError; sourcePath = p.source || sourcePath; updatePill(); }

  /* 手动刷新（拖拽/编辑 Excel 后服务端可能尚未轮询到，可点此立即拉取） */
  function refreshNow() {
    fetch('/api/data').then(r => r.ok ? r.json() : null).then(d => { if (d) apply(d); }).catch(() => {});
    return false;
  }

  /* ---------------- 状态药丸（顶栏右侧） ---------------- */
  function pillHTML() {
    if (!active) return '';
    const ok = !lastError;
    const cls = ok ? 'sync-ok' : 'sync-err';
    const dot = ok ? '<i class="dot"></i>' : '<i class="dot"></i>';
    const txt = ok ? '实时同步 · ' + rel(lastSync)
                   : '同步中断 · ' + (lastError.length > 14 ? lastError.slice(0, 14) + '…' : lastError);
    return '<span class="sync-pill ' + cls + '" title="' + (lastError || '数据源由 server.js 实时监听，Excel 改动将自动刷新') + '">'
      + dot + '<span class="sp-text">' + txt + '</span>'
      + '<button class="sync-refresh" data-act="syncRefresh" title="立即从数据源拉取最新">↻</button></span>';
  }
  function updatePill() {
    if (typeof CameraLive!=='undefined' && CameraLive.isActive() && ['dashboard','trend'].includes(NAV)) {
      CameraLive.renderTopbar(); return;
    }
    const host = document.getElementById('topbarRight');
    if (host && active) host.innerHTML = pillHTML();
  }

  /* ---------------- 数据源卡片（数据导入页） ---------------- */
  function sourceCardHTML() {
    if (!active) return '';
    const s = statusCache || {};
    const exists = s.exists !== false;
    const errLine = lastError
      ? '<div class="sync-err-line">⚠ ' + esc(lastError) + '</div>' : '';
    const fileOnly = (sourcePath || '').split(/[\\/]/).pop();
    return '<div class="card sync-card"><div class="card-head"><h3>实时数据源（自动同步）</h3>'
      + '<span class="hint">' + (s.watching ? '每 ' + ((s.watchIntervalMs || 2000) / 1000) + 's 监听一次' : '') + '</span></div>'
      + '<div class="card-body">'
      + '<div class="sync-row"><span class="muted">监控文件</span><b class="tbl-strong">' + esc(fileOnly) + '</b></div>'
      + '<div class="sync-row"><span class="muted">完整路径</span><code class="sync-path">' + esc(sourcePath) + '</code></div>'
      + '<div class="sync-row"><span class="muted">状态</span>'
      + (exists ? '<span class="badge safe"><i></i>监听中</span>' : '<span class="badge alarm">文件缺失</span>')
      + ' <span class="badge info">v' + (s.version || version) + '</span>'
      + ' <span class="badge neutral">' + (s.readingCount || 0) + ' 条读数</span>'
      + ' <span class="badge neutral">' + (s.gaugeCount || 0) + ' 台表</span></div>'
      + errLine
      + '<div class="muted" style="font-size:12px;margin-top:8px;">改动该 Excel 并保存后，系统将在数秒内自动识别新增 / 修改 / 删除并刷新全部页面，无需手动导入。</div>'
      + '<div style="display:flex;gap:10px;margin-top:12px;">'
      + '<button class="btn btn-ghost" data-act="syncRefresh">立即拉取最新</button>'
      + '</div></div></div>';
  }

  return {
    init: detect,
    isActive: () => active,
    pillHTML,
    sourceCardHTML,
    refreshNow,
  };
})();
