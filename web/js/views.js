/* =====================================================================
 * views.js — 判定看板 / 表计台账 / 表计详情
 * ===================================================================== */

/* ---------------- 整点时间轴 ---------------- */
function timelineHTML(hours, cur) {
  return '<div class="tl-strip">' + hours.map(h => {
    const w = hourWorst(h);
    return '<button class="tl-cell ' + w + (h === cur ? ' cur' : '')
         + '" data-act="pickHour" data-hour="' + h + '">'
         + '<div class="t">' + h.slice(11) + '</div>'
         + '<div class="s">' + h.slice(5, 10).replace('-', '/') + ' ' + STATE_TEXT[w] + '</div>'
         + '</button>';
  }).join('') + '</div>';
}

/* ---------------- 状态热力图 ---------------- */
function heatmapHTML(gs, hours, cur) {
  const dayGroups = [];
  hours.forEach(h => {
    const day = h.slice(5, 10);
    if (!dayGroups.length || dayGroups[dayGroups.length-1].day !== day)
      dayGroups.push({ day, n: 1 });
    else dayGroups[dayGroups.length-1].n++;
  });
  const colTpl = 'grid-template-columns:96px repeat(' + hours.length + ',26px);';

  let head = '<span class="heat-lbl">表号</span>';
  /* 第一行：日期分组（跨列用空占位模拟） */
  let dayRow = '<span class="heat-lbl"></span>';
  dayGroups.forEach(g => {
    dayRow += '<span class="heat-h day" style="grid-column:span ' + g.n + ';">' + g.day + '</span>';
  });
  let hourRow = '<span class="heat-lbl"></span>';
  hours.forEach(h => { hourRow += '<span class="heat-h">' + h.slice(11, 13) + '时</span>'; });

  let rows = '';
  gs.forEach(g => {
    rows += '<span class="heat-lbl">' + g.id + '</span>';
    hours.forEach(h => {
      const j = judgeAt(g.id, h);
      const st = j ? j.state : 'none';
      const tip = g.id + ' · ' + h + (j ? ' · δ=' + fmt(j.pct,2) + '%FS · ' + STATE_TEXT[j.state] : ' · 无数据');
      rows += '<button class="heat-cell ' + st + (h === cur ? ' cur' : '')
           + '" title="' + tip + '" data-act="pickCell" data-hour="' + h
           + '" data-gauge="' + g.id + '">●</button>';
    });
  });

  return '<div class="heat-wrap"><div class="heat-grid" style="' + colTpl + '">'
       + dayRow + hourRow + rows + '</div></div>'
       + '<div class="card-body" style="padding-top:10px;display:flex;justify-content:space-between;flex-wrap:wrap;gap:10px;">'
       + '<div class="heat-legend">'
       + '<span><i style="background:#7CC793;"></i>安全</span>'
       + '<span><i style="background:#EDB25C;"></i>预警</span>'
       + '<span><i style="background:#E06868;"></i>报警</span>'
       + '<span><i style="background:#EEF2F7;"></i>无数据</span>'
       + '</div><span class="muted" style="font-size:12px;">横向看单表劣化轨迹，纵向看整点级数据异常；点击格子进入该表的趋势分析</span></div>';
}

/* ---------------- 偏差趋势图（三色带 + 有符号偏差，手写 SVG，离线可用） ----------------
 * 读法：纵轴 = 偏差 Δ = 识别读数 − 变送器
 *   点落绿色带 = 安全；黄色带 = 预警；红色带 = 报警
 *   中线以上 = 识别偏高，中线以下 = 识别偏低
 */
function trendChart(id) {
  const g = gaugeOf(id);
  if (!g) return '<div class="empty">表计不存在</div>';
  const hours = hoursList();
  const pts = hours.map(h => ({ h, j: judgeAt(id, h) })).filter(p => p.j);
  if (!pts.length) return '<div class="empty">该表暂无读数</div>';

  const unit = deltaUnit(g);                 /* 'MPa' | 'kPa' */
  const K = unit === 'kPa' ? 1000 : 1;
  const dec = unit === 'kPa' ? 2 : 3;
  const warnAbs  = g.range * g.acc / 100 * CONFIG.warnCoef;    /* MPa */
  const alarmAbs = g.range * g.acc / 100 * CONFIG.alarmCoef;
  const maxAbs = Math.max(alarmAbs * 1.35, ...pts.map(p => Math.abs(p.j.delta))) * 1.12;

  const W = 780, H = 300, L = 60, R = 18, T = 18, B = 40;
  const x = i => L + (pts.length === 1 ? (W-L-R)/2 : i * (W-L-R) / (pts.length-1));
  const y = v => T + (1 - (v / maxAbs + 1) / 2) * (H-T-B);     /* v 单位 MPa，上下对称 */

  /* 三色背景带 */
  const band = (vTop, vBot, color) =>
    '<rect x="' + L + '" y="' + y(vTop) + '" width="' + (W-L-R) + '" height="'
    + (y(vBot) - y(vTop)) + '" fill="' + color + '"/>';
  const bands =
      band( maxAbs,  alarmAbs, '#FDECEC') + band(-alarmAbs, -maxAbs, '#FDECEC')   /* 红：报警区 */
    + band( alarmAbs,  warnAbs, '#FDF3E0') + band(-warnAbs, -alarmAbs, '#FDF3E0') /* 黄：预警区 */
    + band( warnAbs, -warnAbs, '#E9F7EE');                                        /* 绿：安全区 */

  /* 网格与刻度（0 / ±预警 / ±报警） */
  const tick = (v, strong) => {
    const lbl = (v === 0 ? '0' : (v > 0 ? '+' : '') + fmt(v * K, dec));
    return '<line x1="' + L + '" y1="' + y(v) + '" x2="' + (W-R) + '" y2="' + y(v)
      + '" stroke="' + (strong ? '#94A3B8' : '#E2E8F0') + '" stroke-width="' + (strong ? 1.4 : 1) + '"/>'
      + '<text x="' + (L-8) + '" y="' + (y(v)+4) + '" text-anchor="end" font-size="10.5" fill="#94A3B8">'
      + lbl + '</text>';
  };
  const ticks = tick(-alarmAbs) + tick(-warnAbs) + tick(0, true) + tick(warnAbs) + tick(alarmAbs)
    + '<text x="' + (L-8) + '" y="' + (T+4) + '" text-anchor="end" font-size="10.5" fill="#64748B" font-weight="600">Δ ' + unit + '</text>';

  /* 阈值虚线 + 右侧标注 */
  const dash = (v, color, name) =>
    '<line x1="' + L + '" y1="' + y(v) + '" x2="' + (W-R) + '" y2="' + y(v)
    + '" stroke="' + color + '" stroke-width="1.3" stroke-dasharray="5 4"/>'
    + '<text x="' + (W-R) + '" y="' + (y(v) + (v > 0 ? -5 : 13)) + '" text-anchor="end" font-size="10.5" fill="'
    + color + '" font-weight="600">' + name + ' ' + (v > 0 ? '+' : '') + fmt(v * K, dec) + '</text>';
  const dashes = dash( warnAbs, '#D97706', '预警') + dash(-warnAbs, '#D97706', '预警')
               + dash( alarmAbs, '#DC2626', '报警') + dash(-alarmAbs, '#DC2626', '报警');

  const stColor = { safe:'#16A34A', warn:'#D97706', alarm:'#DC2626' };
  const linePts = pts.map((p,i) => x(i) + ',' + y(p.j.delta)).join(' ');
  const dots = pts.map((p,i) =>
    '<circle cx="' + x(i) + '" cy="' + y(p.j.delta) + '" r="5" fill="#fff" stroke="'
    + stColor[p.j.state] + '" stroke-width="2.5">'
    + '<title>' + p.h + '  Δ=' + fmtDelta(g, p.j.delta) + ' ' + unit
    + '（识别 ' + fmt(p.j.r.meas, 3) + ' / 变送器 ' + fmt(p.j.r.std, 3) + '） '
    + STATE_TEXT[p.j.state] + '</title></circle>'
  ).join('');
  const xlabels = pts.map((p,i) =>
    '<text x="' + x(i) + '" y="' + (H-16) + '" text-anchor="middle" font-size="10.5" fill="#94A3B8">'
    + p.h.slice(11) + '</text>').join('');

  return '<div class="chart-box"><svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;display:block;">'
    + bands + ticks + dashes
    + '<polyline points="' + linePts + '" fill="none" stroke="#2563EB" stroke-width="2.2" stroke-linejoin="round"/>'
    + dots + xlabels
    + '</svg></div>'
    + '<div class="chart-meta">'
    + '<span style="font-weight:600;color:var(--txt-1);">' + g.id + ' ' + esc(g.name) + '</span>'
    + '<span><i style="width:11px;height:11px;border-radius:3px;background:#E9F7EE;border:none;"></i>安全 |Δ| ≤ ' + fmt(warnAbs*K, dec) + ' ' + unit + '</span>'
    + '<span><i style="width:11px;height:11px;border-radius:3px;background:#FDF3E0;border:none;"></i>预警 '
      + fmt(warnAbs*K, dec) + ' &lt; |Δ| ≤ ' + fmt(alarmAbs*K, dec) + ' ' + unit + '</span>'
    + '<span><i style="width:11px;height:11px;border-radius:3px;background:#FDECEC;border:none;"></i>报警 |Δ| &gt; '
      + fmt(alarmAbs*K, dec) + ' ' + unit + '</span>'
    + '<span class="muted">中线以上 = 识别偏高，以下 = 识别偏低</span>'
    + '</div>';
}

/* ---------------- 双线叠加图：识别读数 vs 变送器 绝对走势 ----------------
 * 读法：蓝实线 = 识别读数，灰虚线 = 变送器（基准）
 *   两条线之间的竖线 = 当点偏差，绿/黄/红即 安全/预警/报警
 *   变送器曲线外围的浅绿带 = 安全区（±预警线）
 */
function dualChart(id) {
  const g = gaugeOf(id);
  if (!g) return '<div class="empty">表计不存在</div>';
  const hours = hoursList();
  const pts = hours.map(h => ({ h, j: judgeAt(id, h) })).filter(p => p.j);
  if (!pts.length) return '<div class="empty">该表暂无读数</div>';

  const unit = deltaUnit(g);
  const K = unit === 'kPa' ? 1000 : 1;
  const dec = unit === 'kPa' ? 2 : 3;
  const warnAbs = g.range * g.acc / 100 * CONFIG.warnCoef;

  /* 纵轴范围：覆盖两条线 + 安全带上下沿 */
  const vals = [];
  pts.forEach(p => vals.push(p.j.r.std, p.j.r.meas, p.j.r.std + warnAbs, p.j.r.std - warnAbs));
  let lo = Math.min.apply(null, vals), hi = Math.max.apply(null, vals);
  const pad = Math.max((hi - lo) * 0.16, warnAbs);
  lo -= pad; hi += pad;

  const W = 780, H = 340, L = 64, R = 92, T = 30, B = 40;
  const x = i => L + (pts.length === 1 ? (W-L-R)/2 : i * (W-L-R) / (pts.length-1));
  const y = v => T + (1 - (v - lo) / (hi - lo)) * (H-T-B);

  /* 安全带（围绕变送器曲线 ±预警线） */
  const up = pts.map((p,i) => x(i) + ',' + y(p.j.r.std + warnAbs));
  const dn = pts.map((p,i) => x(i) + ',' + y(p.j.r.std - warnAbs)).reverse();
  const band = '<polygon points="' + up.concat(dn).join(' ') + '" fill="#E9F7EE"/>'
    + '<polyline points="' + up.join(' ') + '" fill="none" stroke="#BBE5C8" stroke-width="1" stroke-dasharray="4 3"/>'
    + '<polyline points="' + dn.join(' ') + '" fill="none" stroke="#BBE5C8" stroke-width="1" stroke-dasharray="4 3"/>';

  const lineStd  = pts.map((p,i) => x(i) + ',' + y(p.j.r.std)).join(' ');
  const lineMeas = pts.map((p,i) => x(i) + ',' + y(p.j.r.meas)).join(' ');

  const stColor = { safe:'#16A34A', warn:'#D97706', alarm:'#DC2626' };
  const gaps = pts.map((p,i) =>
    '<line x1="' + x(i) + '" y1="' + y(p.j.r.std) + '" x2="' + x(i) + '" y2="' + y(p.j.r.meas)
    + '" stroke="' + stColor[p.j.state] + '" stroke-width="2" stroke-linecap="round" opacity=".8"/>').join('');
  const dotStd = pts.map((p,i) =>
    '<circle cx="' + x(i) + '" cy="' + y(p.j.r.std) + '" r="3.2" fill="#DC2626"/>').join('');
  const dotMeas = pts.map((p,i) =>
    '<circle cx="' + x(i) + '" cy="' + y(p.j.r.meas) + '" r="4.6" fill="#fff" stroke="'
    + stColor[p.j.state] + '" stroke-width="2.6">'
    + '<title>' + p.h + '　识别读数 ' + fmt(p.j.r.meas*K, dec) + ' / 变送器 ' + fmt(p.j.r.std*K, dec) + ' ' + unit
    + '　Δ = ' + fmtDelta(g, p.j.delta) + ' ' + unit + '（' + STATE_TEXT[p.j.state] + '）</title></circle>').join('');

  let grid = '';
  for (let i = 0; i <= 5; i++) {
    const v = lo + (hi - lo) * i / 5;
    grid += '<line x1="' + L + '" y1="' + y(v) + '" x2="' + (W-R) + '" y2="' + y(v) + '" stroke="#E2E8F0" stroke-width="1"/>'
          + '<text x="' + (L-8) + '" y="' + (y(v)+4) + '" text-anchor="end" font-size="10.5" fill="#94A3B8">'
          + fmt(v*K, dec) + '</text>';
  }
  grid += '<text x="' + (L-8) + '" y="' + (T-14) + '" text-anchor="end" font-size="10.5" fill="#64748B" font-weight="600">读数 ' + unit + '</text>';

  const xlabels = pts.map((p,i) =>
    '<text x="' + x(i) + '" y="' + (H-16) + '" text-anchor="middle" font-size="10.5" fill="#94A3B8">'
    + p.h.slice(11) + '</text>').join('');

  /* 曲线右端直接标名，避免认错线 */
  const last = pts.length - 1;
  const yStd = y(pts[last].j.r.std), yMeas = y(pts[last].j.r.meas);
  const nudge = (a, b) => Math.abs(a - b) < 14 ? (a >= b ? 8 : -8) : 0;
  const endLabels =
    '<line x1="' + x(last) + '" y1="' + yStd + '" x2="' + (W-R+8) + '" y2="' + (yStd+nudge(yStd,yMeas))
      + '" stroke="#DC2626" stroke-width="1" stroke-dasharray="3 3" opacity=".7"/>'
    + '<text x="' + (W-R+12) + '" y="' + (yStd + nudge(yStd,yMeas) + 4) + '" font-size="11.5" font-weight="700" fill="#DC2626">变送器</text>'
    + '<line x1="' + x(last) + '" y1="' + yMeas + '" x2="' + (W-R+8) + '" y2="' + (yMeas-nudge(yStd,yMeas))
      + '" stroke="#2563EB" stroke-width="1" stroke-dasharray="3 3" opacity=".7"/>'
    + '<text x="' + (W-R+12) + '" y="' + (yMeas - nudge(yStd,yMeas) + 4) + '" font-size="11.5" font-weight="700" fill="#2563EB">识别读数</text>';

  return '<div class="chart-box"><svg viewBox="0 0 ' + W + ' ' + H + '" style="width:100%;display:block;">'
    + grid + band
    + '<polyline points="' + lineStd + '" fill="none" stroke="#DC2626" stroke-width="2.2" stroke-dasharray="6 4" stroke-linejoin="round"/>'
    + '<polyline points="' + lineMeas + '" fill="none" stroke="#2563EB" stroke-width="2.4" stroke-linejoin="round"/>'
    + gaps + dotStd + dotMeas + xlabels + endLabels
    + '</svg></div>'
    + '<div class="chart-meta">'
    + '<span style="font-weight:600;color:var(--txt-1);">' + g.id + ' ' + esc(g.name) + '</span>'
    + '<span><i style="width:18px;height:2.4px;border-radius:2px;background:#2563EB;border:none;"></i>识别读数</span>'
    + '<span><i style="width:18px;height:2.2px;border-radius:2px;background:#DC2626;border:none;"></i>变送器（基准·虚线）</span>'
    + '<span><i style="width:11px;height:11px;border-radius:3px;background:#E9F7EE;border:none;"></i>变送器 ±'
      + fmt(warnAbs*K, dec) + ' ' + unit + ' 安全区</span>'
    + '<span class="muted">两线之间的竖线 = 该整点偏差（绿/黄/红 = 安全/预警/报警）</span>'
    + '</div>';
}

/* 视图切换控件（趋势页与详情页共用） */
function trendSeg() {
  const mode = TREND_MODE === 'delta' ? 'delta' : 'dual';
  return '<div class="seg">'
    + '<button data-act="setTrendMode" data-mode="dual"'  + (mode === 'dual'  ? ' class="on"' : '') + '>双线叠加</button>'
    + '<button data-act="setTrendMode" data-mode="delta"' + (mode === 'delta' ? ' class="on"' : '') + '>偏差带</button>'
    + '</div>';
}
const trendChartFor = id => TREND_MODE === 'delta' ? trendChart(id) : dualChart(id);

/* ================= 判定看板 ================= */
function vDashboard() {
  const gs = scopeGauges();
  const hours = hoursList();
  if (!hours.length) return '<div class="card"><div class="empty">暂无数据，请先到「数据导入」上传整点读数</div></div>';
  const cur = CUR_HOUR || hours[hours.length - 1];
  if (!TREND_ID || !gs.some(g => g.id === TREND_ID)) TREND_ID = gs[0].id;

  const idx = hours.indexOf(cur);
  const prev = idx > 0 ? hours[idx - 1] : null;
  const count = (ts, st) => gs.filter(g => { const j = judgeAt(g.id, ts); return j && (st ? j.state === st : j.state !== 'alarm'); }).length;
  const withData = ts => gs.filter(g => judgeAt(g.id, ts)).length;

  const alarmN = count(cur, 'alarm'), warnN = count(cur, 'warn');
  const okRate = withData(cur) ? Math.round(count(cur, null) / withData(cur) * 100) : 100;
  const pAlarm = prev ? count(prev, 'alarm') : 0;
  const pWarn  = prev ? count(prev, 'warn') : 0;
  const pRate  = prev && withData(prev) ? Math.round(count(prev, null) / withData(prev) * 100) : okRate;

  /* 报警/预警是"越少越好"，上升为坏（红）；合格率上升为好（绿） */
  const delta = (now, before, goodWhenUp, unit) => {
    const d = now - before;
    if (!prev || d === 0) return '<span class="delta flat">— 与上一整点持平</span>';
    const good = goodWhenUp ? d > 0 : d < 0;
    return '<span class="delta ' + (good ? 'good' : 'bad') + '">'
         + (d > 0 ? ICONS.up : ICONS.down) + Math.abs(d) + (unit || '')
         + ' vs ' + prev.slice(11) + '</span>';
  };

  const f = freshnessInfo();
  const kpi = (label, icon, cls, val, sub) =>
    '<div class="kpi"><div class="kpi-top"><span class="kpi-label">' + label + '</span>'
    + '<span class="kpi-ico ' + cls + '">' + icon + '</span></div>'
    + '<div class="kpi-val">' + val + '</div><div class="kpi-sub">' + sub + '</div></div>';

  /* ---- 处置队列：当前整点所有非安全表 ---- */
  const queue = gs.map(g => {
    const j = judgeAt(g.id, cur);
    if (!j || j.state === 'safe') return null;
    const run = abnormalRun(g.id, cur);
    return { g, j, run, h: run ? handleOf(g.id, run) : { state: 'open' } };
  }).filter(Boolean).sort((a, b) =>
    STATE_RANK[b.j.state] - STATE_RANK[a.j.state] || b.j.pct - a.j.pct);

  const queueRows = queue.map(({ g, j, run, h }) => {
    const btns = h.state === 'open'
      ? '<button class="btn btn-sm btn-primary" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="ack">确认</button>'
      : h.state === 'ack'
      ? '<button class="btn btn-sm btn-ghost" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="done">复检完成</button>'
      : '<button class="btn btn-sm btn-ghost" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="open">重新打开</button>';
    return '<tr>'
      + '<td class="tbl-strong">' + g.id + '</td>'
      + '<td><div style="font-size:12.5px;">' + esc(g.name) + '</div><div class="muted" style="font-size:11.5px;">' + AREAS[g.area] + '</div></td>'
      + '<td><span class="badge ' + j.state + '"><i></i>' + STATE_TEXT[j.state] + '</span></td>'
      + '<td class="num tbl-strong" style="color:' + (j.state === 'alarm' ? 'var(--alarm)' : 'var(--warn)') + ';">' + fmt(j.pct, 2) + '%</td>'
      + '<td class="num">' + fmtDelta(g, j.delta) + ' <span class="muted" style="font-size:11px;">' + deltaUnit(g) + '</span></td>'
      + '<td class="num">' + (run ? '<b style="color:' + (run.len >= 3 ? 'var(--alarm)' : 'var(--txt-1)') + ';">' + run.len + '</b> 个整点' : '—') + '</td>'
      + '<td><span class="badge ' + HANDLE_BADGE[h.state] + '">' + HANDLE_TEXT[h.state] + '</span>'
      + (h.by ? '<div class="muted" style="font-size:11px;margin-top:3px;">' + esc(h.by) + ' ' + h.at.slice(5) + '</div>' : '') + '</td>'
      + '<td style="white-space:nowrap;">' + btns
      + ' <span class="link" data-act="openDetail" data-gauge="' + g.id + '">详情</span></td>'
      + '</tr>';
  }).join('');

  return ''
  /* 页头 */
  + '<div class="page-head"><div>'
  + '<div class="ph-sub">数据范围：' + (isAdmin() ? '全部区域' : AREAS[SESSION.area])
  + ' · 在检 ' + gs.length + ' 台 · 当前整点 <b style="color:var(--txt-1);">' + cur + '</b>'
  + '（点击时间轴切换）</div></div>'
  + '<div class="head-actions">'
  + '<button class="btn btn-ghost" data-act="recalc">重新判定</button>'
  + (isAdmin()
      ? '<button class="btn btn-primary" data-act="exportCsv">导出判定台账</button>'
      : '<button class="btn btn-primary" disabled title="普通用户无导出权限">导出判定台账</button>')
  + '</div></div>'

  /* KPI */
  + '<div class="kpi-grid">'
  + kpi('报警压力表', ICONS.alert, 'alarm',
      '<span style="color:' + (alarmN ? 'var(--alarm)' : 'var(--txt-1)') + ';">' + alarmN + '</span><small>台</small>',
      delta(alarmN, pAlarm, false, ' 台') + '<span class="muted">需立即停用检修</span>')
  + kpi('预警压力表', ICONS.bell, 'warn',
      '<span style="color:' + (warnN ? 'var(--warn)' : 'var(--txt-1)') + ';">' + warnN + '</span><small>台</small>',
      delta(warnN, pWarn, false, ' 台') + '<span class="muted">建议缩短复检周期</span>')
  + kpi('本整点合格率', ICONS.check, 'safe',
      okRate + '<small>%</small>',
      delta(okRate, pRate, true, '%') + '<span class="muted">无报警即合格</span>')
  + kpi('数据新鲜度', ICONS.clock, f.stale ? 'alarm' : 'info',
      '<span style="font-size:20px;color:' + (f.stale ? 'var(--alarm)' : 'var(--txt-1)') + ';">' + f.text + '</span>',
      '<span class="muted">' + LAST_IMPORT.ts + ' 导入<br>' + esc(LAST_IMPORT.file) + '</span>')
  + '</div>'

  /* 整点时间轴 */
  + '<div class="card"><div class="card-head"><h3>整点时间轴</h3>'
  + '<span class="hint">每个整点按当时最差状态着色，点击回看</span></div>'
  + '<div class="card-body" style="padding:14px 18px 10px;">' + timelineHTML(hours, cur) + '</div></div>'

  /* 处置队列（表格内部滚动，页面本身一屏装完） */
  + '<div class="card"><div class="card-head"><h3>处置队列</h3>'
  + '<div style="display:flex;align-items:center;gap:10px;">'
  + '<span class="hint">' + cur + ' · 非安全表 ' + queue.length + ' 台，按严重度排序</span>'
  + '<button class="btn btn-sm btn-ghost" data-nav="heatmap">状态热力图 →</button>'
  + '<button class="btn btn-sm btn-ghost" data-nav="trend">趋势分析 →</button>'
  + '</div></div>'
  + '<div class="card-body flush queue-scroll">'
  + (queue.length
      ? '<table><thead><tr><th>表号</th><th>名称 / 区域</th><th>状态</th><th class="num">δ</th><th class="num">偏差 Δ</th><th class="num">连续异常</th><th>处置</th><th></th></tr></thead><tbody>'
        + queueRows + '</tbody></table>'
      : '<div class="empty">' + cur + ' 全部表计判定安全，无待处置项</div>')
  + '</div></div>';
}

/* ================= 状态热力图（独立页） ================= */
function vHeatmap() {
  const gs = scopeGauges();
  const hours = hoursList();
  if (!hours.length) return '<div class="card"><div class="empty">暂无数据，请先到「数据导入」上传整点读数</div></div>';
  const cur = CUR_HOUR || hours[hours.length - 1];
  const nAbn = gs.filter(g => { const j = judgeAt(g.id, cur); return j && j.state !== 'safe'; }).length;

  return '<div class="page-head"><div>'
    + '<div class="ph-sub">' + gs.length + ' 台表 × ' + hours.length + ' 个整点 · 当前整点 <b style="color:var(--txt-1);">'
    + cur + '</b> · 该整点非安全 ' + nAbn + ' 台</div></div></div>'
    + '<div class="card"><div class="card-head"><h3>状态热力图</h3>'
    + '<span class="hint">行 = 表计，列 = 整点，颜色 = 该整点判定状态</span></div>'
    + heatmapHTML(gs, hours, cur)
    + '</div>';
}

/* ================= 趋势分析（独立页，两种视图可切换） ================= */
function vTrend() {
  const gs = scopeGauges();
  const hours = hoursList();
  if (!gs.length || !hours.length) return '<div class="card"><div class="empty">暂无数据，请先到「数据导入」上传整点读数</div></div>';
  if (!TREND_ID || !gs.some(g => g.id === TREND_ID)) TREND_ID = gs[0].id;
  const kw = TREND_KW.trim().toLowerCase();
  const filtered = kw ? gs.filter(x => (x.id + x.name + x.pos).toLowerCase().includes(kw)) : gs;
  if (filtered.length && !filtered.some(x => x.id === TREND_ID)) TREND_ID = filtered[0].id;
  const g = gaugeOf(TREND_ID);
  const cur = CUR_HOUR || hours[hours.length - 1];
  const j = filtered.length ? judgeAt(TREND_ID, cur) : null;
  const run = j && j.state !== 'safe' ? abnormalRun(TREND_ID, cur) : null;

  const trendOpts = filtered.map(x =>
    '<option value="' + x.id + '"' + (x.id === TREND_ID ? ' selected' : '') + '>'
    + x.id + ' ' + esc(x.name) + '</option>').join('');

  const searchBox =
    '<input class="input" id="trendKw" type="text" placeholder="搜索表号 / 名称 / 位置" value="'
    + esc(TREND_KW) + '" style="height:32px;font-size:13px;width:190px;padding:0 10px;">';

  const mode = TREND_MODE === 'dual' ? 'dual' : 'delta';
  const seg = trendSeg();
  const cardTitle = mode === 'delta' ? '偏差时间趋势' : '读数叠加走势';

  return '<div class="page-head"><div>'
    + '<div class="ph-sub">全部 ' + hours.length + ' 个整点 · '
    + (mode === 'delta' ? '纵轴为偏差 Δ = 识别读数 − 变送器' : '纵轴为绝对读数，两条线叠加对比')
    + (kw ? ' · 搜索「' + esc(TREND_KW) + '」匹配 ' + filtered.length + ' 台' : '') + '</div></div>'
    + '<div class="head-actions">'
    + seg
    + (j ? '<span class="badge ' + j.state + '">' + cur + ' · ' + STATE_TEXT[j.state]
      + ' Δ=' + fmtDelta(g, j.delta) + ' ' + deltaUnit(g) + '</span>' : '')
    + (run ? '<span class="badge ' + HANDLE_BADGE[handleOf(TREND_ID, run).state] + '">连续异常 '
      + run.len + ' 个整点 · ' + HANDLE_TEXT[handleOf(TREND_ID, run).state] + '</span>' : '')
    + (filtered.length ? '<button class="btn btn-ghost" data-act="openDetail" data-gauge="' + TREND_ID + '">表计详情 →</button>' : '')
    + '</div></div>'
    + '<div class="card"><div class="card-head"><h3>' + cardTitle + '</h3>'
    + '<div style="display:flex;gap:8px;align-items:center;">' + searchBox
    + '<select class="sel" id="trendSel" style="height:32px;font-size:13px;min-width:260px;"'
    + (filtered.length ? '' : ' disabled') + '>' + trendOpts + '</select></div></div>'
    + (filtered.length ? trendChartFor(TREND_ID)
        : '<div class="empty">没有匹配「' + esc(TREND_KW) + '」的压力表，换个关键字试试</div>')
    + '</div>';
}

/* ================= 表计台账 ================= */
function vGauges() {
  const cur = (() => { const h = hoursList(); return CUR_HOUR || h[h.length - 1]; })();
  let list = visibleGauges();
  if (GAUGE_Q.area)  list = list.filter(g => g.area === GAUGE_Q.area);
  if (GAUGE_Q.kw)    list = list.filter(g => (g.id + g.name + g.pos).toLowerCase().includes(GAUGE_Q.kw.toLowerCase()));
  const rows = list.map(g => {
    const j = g.status === 'pending' ? null : judgeAt(g.id, cur);
    if (GAUGE_Q.state && (!j || j.state !== GAUGE_Q.state)) return '';
    const dis = g.status === 'pending';
    return '<tr' + (dis ? ' class="row-disabled"' : '') + '>'
      + '<td class="tbl-strong">' + g.id
      + (dis ? ' <span class="badge warn" style="margin-left:6px;">待审批删除</span>' : '') + '</td>'
      + '<td>' + esc(g.name) + '</td>'
      + '<td>' + AREAS[g.area] + '</td>'
      + '<td style="font-size:12.5px;color:var(--txt-2);">' + esc(g.pos) + '</td>'
      + '<td class="num">' + fmtRange(g) + ' / ' + g.acc + ' 级</td>'
      + '<td class="num">' + (j ? fmt(j.r.std, 2) + ' / ' + fmt(j.r.meas, 3) : '—') + '</td>'
      + '<td class="num" style="font-weight:600;color:' + (j ? (j.delta > 0 ? 'var(--alarm)' : 'var(--info)') : 'inherit') + ';">'
      + (j ? fmtDelta(g, j.delta) : '—') + '</td>'
      + '<td>' + (j ? '<span class="badge ' + j.state + '"><i></i>' + STATE_TEXT[j.state] + '</span>' : '<span class="badge neutral">暂停判定</span>') + '</td>'
      + '<td style="white-space:nowrap;">'
      + '<span class="link" data-act="openDetail" data-gauge="' + g.id + '">详情</span> · '
      + (dis ? '<span class="muted">已申请</span>'
          : isAdmin()
            ? '<span class="link" style="color:var(--alarm);" data-act="delGauge" data-gauge="' + g.id + '">删除</span>'
            : '<span class="link" style="color:var(--warn);" data-act="reqDelete" data-gauge="' + g.id + '">申请删除</span>')
      + '</td></tr>';
  }).join('');

  const areaOpts = '<option value="">全部区域</option>'
    + (isAdmin() ? Object.keys(AREAS).map(k =>
        '<option value="' + k + '"' + (GAUGE_Q.area === k ? ' selected' : '') + '>' + AREAS[k] + '</option>').join('')
      : '<option value="' + SESSION.area + '" selected>' + AREAS[SESSION.area] + '</option>');

  return '<div class="page-head"><div>'
    + '<div class="ph-sub">共 ' + visibleGauges().length + ' 台 · 当前整点 ' + (cur || '—')
    + ' · 新增即时生效，删除需管理员审批</div></div>'
    + '<div class="head-actions"><button class="btn btn-primary" data-act="addGauge">＋ 新增压力表</button></div></div>'

    + '<div class="card"><div class="card-body" style="padding:14px 20px;display:flex;gap:10px;flex-wrap:wrap;">'
    + '<input class="input" id="gqKw" placeholder="搜索表号 / 名称 / 安装位置" value="' + esc(GAUGE_Q.kw) + '" style="width:240px;">'
    + '<select class="sel" id="gqArea">' + areaOpts + '</select>'
    + '<select class="sel" id="gqState"><option value="">全部状态</option>'
    + ['safe','warn','alarm'].map(s => '<option value="' + s + '"' + (GAUGE_Q.state === s ? ' selected' : '') + '>' + STATE_TEXT[s] + '</option>').join('')
    + '</select></div>'

    + '<div class="card-body flush">'
    + (rows
      ? '<table><thead><tr><th>表号</th><th>名称</th><th>区域</th><th>安装位置</th><th class="num">量程 / 精度</th><th class="num">变送器 / 识别</th><th class="num">偏差 Δ</th><th>状态</th><th>操作</th></tr></thead><tbody>'
        + rows + '</tbody></table>'
      : '<div class="empty">没有符合条件的压力表</div>')
    + '</div></div>';
}

/* ================= 表计详情 ================= */
function vDetail() {
  const g = gaugeOf(DETAIL_ID);
  if (!g) return '<div class="card"><div class="empty">表计不存在或已删除</div></div>';
  const hours = hoursList();
  const cur = CUR_HOUR || hours[hours.length - 1];
  const j = judgeAt(g.id, cur);
  const run = j && j.state !== 'safe' ? abnormalRun(g.id, cur) : null;
  const h = run ? handleOf(g.id, run) : null;

  const pts = hours.map(ts => ({ ts, j: judgeAt(g.id, ts) })).filter(p => p.j);
  const histRows = pts.slice().reverse().map(p =>
    '<tr><td class="num">' + p.ts + '</td>'
    + '<td class="num">' + fmtReading(g, p.j.r.std) + '</td>'
    + '<td class="num">' + fmtReading(g, p.j.r.meas) + '</td>'
    + '<td class="num" style="font-weight:600;color:' + (p.j.delta > 0 ? 'var(--alarm)' : 'var(--info)') + ';">'
    + fmtDelta(g, p.j.delta) + ' ' + deltaUnit(g) + '</td>'
    + '<td class="num tbl-strong">' + fmt(p.j.pct, 2) + '%</td>'
    + '<td><span class="badge ' + p.j.state + '"><i></i>' + STATE_TEXT[p.j.state] + '</span></td></tr>'
  ).join('');

  return '<div class="page-head"><div>'
    + '<div class="ph-sub"><span class="link" data-act="backGauges">← 返回台账</span></div></div>'
    + '<div class="head-actions">'
    + (j && j.state !== 'safe' && h
      ? (h.state === 'open'
          ? '<button class="btn btn-primary" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="ack">确认异常</button>'
          : h.state === 'ack'
          ? '<button class="btn btn-ghost" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="done">复检完成</button>'
          : '<button class="btn btn-ghost" data-act="handle" data-gauge="' + g.id + '" data-start="' + run.start + '" data-to="open">重新打开</button>')
      : '')
    + '</div></div>'

    + '<div class="card"><div class="card-body"><div class="detail-head">'
    + '<div class="dh-main">'
    + '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;">'
    + '<span style="font-size:19px;font-weight:700;">' + g.id + '</span>'
    + '<span style="font-size:14px;color:var(--txt-2);">' + esc(g.name) + '</span>'
    + (g.status === 'pending' ? '<span class="badge warn">待审批删除</span>' : '')
    + '</div>'
    + '<div class="dh-grid">'
    + '<div class="dh-item"><div class="k">所属区域</div><div class="v">' + AREAS[g.area] + '</div></div>'
    + '<div class="dh-item"><div class="k">安装位置</div><div class="v">' + esc(g.pos) + '</div></div>'
    + '<div class="dh-item"><div class="k">表计型号</div><div class="v">' + g.type + '</div></div>'
    + '<div class="dh-item"><div class="k">量程 / 精度</div><div class="v">' + fmtRange(g) + ' / ' + g.acc + ' 级</div></div>'
    + '<div class="dh-item"><div class="k">投用日期</div><div class="v">' + g.installDate + '</div></div>'
    + '<div class="dh-item"><div class="k">最大允许误差 MPE</div><div class="v">' + fmtMPE(g) + '</div></div>'
    + '</div></div>'
    + (j
      ? '<div class="big-state ' + j.state + '"><div class="bs-t">' + STATE_TEXT[j.state] + '</div>'
        + '<div class="bs-s">' + cur + '</div>'
        + '<div style="font-size:13px;font-weight:700;margin-top:4px;">δ = ' + fmt(j.pct, 2) + '%FS</div>'
        + '<div class="bs-s">Δ = ' + fmtDelta(g, j.delta) + ' ' + deltaUnit(g) + '</div>'
        + (run ? '<div class="bs-s" style="margin-top:4px;">已连续异常 ' + run.len + ' 个整点</div>' : '')
        + '</div>'
      : '<div class="big-state"><div class="bs-t muted">无数据</div></div>')
    + '</div></div></div>'

    + '<div class="card"><div class="card-head"><h3>' + (TREND_MODE === 'delta' ? '偏差时间趋势' : '读数叠加走势') + '</h3>'
    + '<div style="display:flex;gap:10px;align-items:center;">' + trendSeg()
    + '<span class="hint">全部 ' + pts.length + ' 个整点</span></div></div>'
    + trendChartFor(g.id) + '</div>'

    + '<div class="card"><div class="card-head"><h3>整点读数明细</h3>'
    + '<span class="hint">Δ = 识别读数 − 变送器 · δ = |Δ| / 量程 × 100%</span></div>'
    + '<div class="card-body flush"><table><thead><tr><th class="num">整点</th><th class="num">变送器 (' + deltaUnit(g) + ')</th><th class="num">识别读数 (' + deltaUnit(g) + ')</th><th class="num">Δ (' + deltaUnit(g) + ')</th><th class="num">δ</th><th>状态</th></tr></thead><tbody>'
    + histRows + '</tbody></table></div></div>';
}
