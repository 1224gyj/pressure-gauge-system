/* =====================================================================
 * views2.js — 数据导入 / 删除审批 / 阈值配置 / 用户管理 / 审计日志
 *             + 全部业务动作 + 初始化
 * ===================================================================== */

/* ================= 数据导入 ================= */
function normalizeHour(v) {
  if (v instanceof Date && !isNaN(v)) {
    return v.getFullYear() + '-' + pad2(v.getMonth()+1) + '-' + pad2(v.getDate())
         + ' ' + pad2(v.getHours()) + ':00';
  }
  let s = String(v).trim().replace(/[./]/g, '-').replace('T', ' ');
  const m = s.match(/(\d{4})-(\d{1,2})-(\d{1,2})\s+(\d{1,2})(?::(\d{1,2}))?(?::(\d{1,2}))?/);
  if (m) return m[1] + '-' + pad2(m[2]) + '-' + pad2(m[3]) + ' ' + pad2(m[4]) + ':00';
  const md = s.match(/(\d{1,2})-(\d{1,2})\s+(\d{1,2})(?::(\d{1,2}))?/);
  if (md) return '2026-' + pad2(md[1]) + '-' + pad2(md[2]) + ' ' + pad2(md[3]) + ':00';
  return null;
}
function csvToRows(text) {
  return text.replace(/^\uFEFF/, '').split(/\r?\n/).filter(l => l.trim())
    .map(l => l.split(/[,;\t]/).map(c => c.trim().replace(/^"|"$/g, '')));
}
function parseRows(grid) {
  if (grid.length < 2) return { err: '文件内容为空或只有表头' };
  const head = grid[0].map(h => String(h).toLowerCase());
  const ci = keys => head.findIndex(h => keys.some(k => h.includes(k)));
  const cTs = ci(['时间', 'time', '时刻', '日期']);
  const cId = ci(['表号', '编号', 'gauge', 'id', '位号']);
  const cStd = ci(['变送器', '标准', 'transmitter']);
  const cMeas = ci(['识别', '实测', '测量', '读数', 'ocr', 'meas']);
  if (cTs < 0 || cId < 0 || cStd < 0 || cMeas < 0)
    return { err: '表头识别失败：需包含「时间 / 表号 / 变送器 / 识别读数」四类列（顺序不限）' };
  const rows = [], bad = [];
  for (let i = 1; i < grid.length; i++) {
    const ts = normalizeHour(grid[i][cTs]);
    const id = String(grid[i][cId]).trim();
    const std = parseFloat(grid[i][cStd]), meas = parseFloat(grid[i][cMeas]);
    if (!ts || !id || isNaN(std) || isNaN(meas)) { bad.push(i + 1); continue; }
    rows.push({ ts, gaugeId: id, std, meas });
  }
  return { rows, bad, raw: grid.length - 1 };
}
/* 整点对齐：同表同整点多条取均值 */
function alignHourly(rows) {
  const map = new Map();
  rows.forEach(r => {
    const k = r.gaugeId + '@' + r.ts;
    if (!map.has(k)) map.set(k, { ...r, n: 0, sum: 0 });
    const m = map.get(k); m.n++; m.sum += r.meas; m.meas = m.sum / m.n;
  });
  return [...map.values()];
}

function vImport() {
  const xlsxOk = typeof window.XLSX !== 'undefined';
  const p = PENDING_IMPORT;
  const logRows = IMPORT_LOG.map(l =>
    '<tr><td class="num">' + l.ts + '</td><td>' + esc(l.file) + '</td>'
    + '<td class="num">' + l.raw + ' / ' + l.ok + '</td>'
    + '<td class="num">' + l.gauges + '</td><td>' + l.range + '</td>'
    + '<td>' + (l.created.length ? '<span class="badge info">新建档 ' + l.created.length + ' 台</span>' : '<span class="muted">—</span>') + '</td></tr>'
  ).join('');

  return '<div class="page-head"><div>'
    + '<div class="ph-sub">支持 .xlsx' + (xlsxOk ? '' : '（当前离线，仅）') + ' 与 .csv · 时间自动向整点对齐 · 同表同整点取均值</div>'
    + '</div></div>'

    + (typeof Sync !== 'undefined' && Sync.isActive && Sync.isActive() ? Sync.sourceCardHTML() : '')

    + '<div class="two-col">'
    + '<div>'
    + '<div class="card"><div class="card-head"><h3>上传数据文件</h3></div>'
    + '<div class="card-body">'
    + '<div class="step-bar"><div class="step' + (!p ? ' on' : '') + '">① 选择文件</div>'
    + '<div class="step' + (p ? ' on' : '') + '">② 预览确认</div><div class="step">③ 完成入库</div></div>'
    + '<div class="dropzone" id="dz" data-act="pickFile">'
    + '<div class="dz-ico">' + ICONS.import.replace('width="17" height="17"', 'width="26" height="26"') + '</div>'
    + '<div style="font-size:14.5px;"><b>点击选择</b> 或将 Excel / CSV 文件拖到此处</div>'
    + '<div class="muted" style="font-size:12px;margin-top:6px;">单次最多一个文件 · 新表号将自动建档（Y-100 / 1.6MPa / 1.6级）</div>'
    + '</div>'
    + '<div style="display:flex;gap:10px;margin-top:14px;">'
    + '<button class="btn btn-ghost" data-act="downloadTpl">下载导入模板 (CSV)</button>'
    + (xlsxOk ? '' : '<span class="badge warn">离线环境：xlsx 解析库未加载，请使用 CSV</span>')
    + '</div>'
    + (p ? renderPreview(p) : '')
    + '</div></div></div>'

    + '<div class="card"><div class="card-head"><h3>文件格式要求</h3></div>'
    + '<div class="card-body flush"><table class="fmt-spec">'
    + '<thead><tr><th>列</th><th>说明</th><th>示例</th></tr></thead><tbody>'
    + '<tr><td>时间</td><td>任意常见格式，自动归并到整点</td><td class="num">2026-10-04 08:15 → 08:00</td></tr>'
    + '<tr><td>表号</td><td>须与台账一致；新表号自动建档</td><td class="num">YB-104</td></tr>'
    + '<tr><td>变送器</td><td>变送器上传的基准读数</td><td class="num">0.710</td></tr>'
    + '<tr><td>识别读数</td><td>图像识别（OCR）得到的读数</td><td class="num">0.726</td></tr>'
    + '</tbody></table></div></div>'
    + '</div>'

    + '<div class="card"><div class="card-head"><h3>导入记录</h3><span class="hint">最近 ' + IMPORT_LOG.length + ' 次</span></div>'
    + '<div class="card-body flush">'
    + (logRows ? '<table><thead><tr><th class="num">时间</th><th>文件</th><th class="num">原始/入库</th><th class="num">涉及表数</th><th>整点范围</th><th>备注</th></tr></thead><tbody>' + logRows + '</tbody></table>' : '<div class="empty">暂无导入记录</div>')
    + '</div></div>';
}

function renderPreview(p) {
  const rowsHtml = p.aligned.slice(0, 8).map(r => {
    const g = gaugeOf(r.gaugeId);
    const jj = g ? judgePoint(g, r) : null;
    return '<tr><td class="num">' + r.ts + '</td><td class="tbl-strong">' + esc(r.gaugeId)
      + (p.newGauges.includes(r.gaugeId) ? ' <span class="badge info">新表</span>' : '') + '</td>'
      + '<td class="num">' + (g ? fmtReading(g, r.std) : fmt(r.std, 3)) + '</td>'
      + '<td class="num">' + (g ? fmtReading(g, r.meas) : fmt(r.meas, 3)) + '</td>'
      + '<td>' + (jj ? '<span class="badge ' + jj.state + '"><i></i>' + STATE_TEXT[jj.state] + '</span>' : '—') + '</td></tr>';
  }).join('');
  return '<div style="margin-top:18px;border-top:1px solid var(--line);padding-top:16px;">'
    + '<div style="display:flex;gap:10px;flex-wrap:wrap;margin-bottom:12px;">'
    + '<span class="badge navy">文件：' + esc(p.fileName) + '</span>'
    + '<span class="badge info">原始 ' + p.raw + ' 行</span>'
    + '<span class="badge safe">对齐后 ' + p.aligned.length + ' 条</span>'
    + (p.merged ? '<span class="badge warn">整点合并 ' + p.merged + ' 组</span>' : '')
    + (p.bad.length ? '<span class="badge alarm">跳过异常行 ' + p.bad.length + '</span>' : '')
    + '</div>'
    + '<table><thead><tr><th class="num">整点</th><th>表号</th><th class="num">变送器</th><th class="num">识别读数</th><th>预判状态</th></tr></thead><tbody>'
    + rowsHtml + '</tbody></table>'
    + (p.aligned.length > 8 ? '<div class="muted" style="font-size:12px;margin-top:8px;">仅预览前 8 条，共 ' + p.aligned.length + ' 条</div>' : '')
    + '<div style="display:flex;gap:10px;margin-top:16px;">'
    + '<button class="btn btn-primary" data-act="confirmImport">确认入库并重新判定</button>'
    + '<button class="btn btn-ghost" data-act="closePreview">取消</button>'
    + '</div></div>';
}

function handleFile(file) {
  if (!file) return;
  const isCsv = /\.csv$/i.test(file.name);
  const isXlsx = /\.xlsx?$/i.test(file.name);
  if (!isCsv && !isXlsx) { toast('仅支持 .xlsx / .csv 文件', false); return; }
  if (isXlsx && typeof window.XLSX === 'undefined') { toast('离线环境无法解析 xlsx，请另存为 CSV 后导入', false); return; }

  const finish = grid => {
    const r = parseRows(grid);
    if (r.err) { toast(r.err, false); return; }
    const aligned = alignHourly(r.rows);
    const known = new Set(GAUGES.map(g => g.id));
    PENDING_IMPORT = {
      fileName: file.name, raw: r.raw, bad: r.bad, aligned,
      merged: r.rows.length - aligned.length,
      newGauges: [...new Set(aligned.map(a => a.gaugeId).filter(id => !known.has(id)))],
    };
    renderMain();
  };
  if (isCsv) {
    const rd = new FileReader();
    rd.onload = () => finish(csvToRows(rd.result));
    rd.readAsText(file, 'utf-8');
  } else {
    const rd = new FileReader();
    rd.onload = () => {
      const wb = XLSX.read(rd.result, { type: 'array', cellDates: true });
      finish(XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, raw: true }));
    };
    rd.readAsArrayBuffer(file);
  }
}
function doConfirmImport() {
  const p = PENDING_IMPORT; if (!p) return;
  p.newGauges.forEach(id => GAUGES.push({
    id, name: '（导入自动建档）', area: isAdmin() ? 'A' : SESSION.area,
    pos: '待补充', type: 'Y-100 弹簧管', range: 1.6, acc: 1.6,
    status: 'active', installDate: '2026-10-04',
  }));
  const keys = new Set(p.aligned.map(a => a.gaugeId + '@' + a.ts));
  READINGS = READINGS.filter(r => !keys.has(r.gaugeId + '@' + r.ts)).concat(
    p.aligned.map(({ ts, gaugeId, std, meas }) => ({ ts, gaugeId, std, meas: +meas.toFixed(4) })));
  const hours = [...new Set(p.aligned.map(a => a.ts))].sort();
  IMPORT_LOG.unshift({
    ts: fmtDateTime(new Date()), file: p.fileName, raw: p.raw, ok: p.aligned.length,
    gauges: new Set(p.aligned.map(a => a.gaugeId)).size,
    range: hours[0].slice(11) + ' ~ ' + hours[hours.length-1].slice(11), created: p.newGauges,
  });
  LAST_IMPORT = { ts: fmtDateTime(new Date()), file: p.fileName };
  pushAudit('数据导入', '导入 ' + p.fileName + '，对齐 ' + p.aligned.length + ' 条读数'
    + (p.newGauges.length ? '，自动建档 ' + p.newGauges.join('、') : ''));
  PENDING_IMPORT = null;
  CUR_HOUR = null;
  go('dashboard');
  toast('已入库 ' + p.aligned.length + ' 条读数并完成全量重判');
}
function downloadTpl() {
  const csv = '时间,表号,变送器(MPa),识别读数(MPa)\n'
    + '2026-10-04 15:00,YB-101,0.620,0.623\n'
    + '2026-10-04 15:00,YB-104,0.710,0.728\n'
    + '2026/10/4 15:12,YB-203,0.830,0.848\n';
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\uFEFF' + csv], { type: 'text/csv' }));
  a.download = '压力表整点数据_导入模板.csv';
  a.click(); URL.revokeObjectURL(a.href);
}

/* ================= 表计增删 ================= */
function openAddGauge() {
  const areaFld = isAdmin()
    ? '<label class="fld"><span>所属区域</span><select class="sel" id="ngArea">'
      + Object.keys(AREAS).map(k => '<option value="' + k + '">' + AREAS[k] + '</option>').join('')
      + '</select></label>'
    : '<label class="fld"><span>所属区域</span><input class="input" value="' + AREAS[SESSION.area] + '" disabled></label>';
  openModal('新增压力表',
    '<div class="form-grid">'
    + '<label class="fld"><span>表号 *</span><input class="input" id="ngId" placeholder="如 YB-106"></label>'
    + '<label class="fld"><span>名称 *</span><input class="input" id="ngName" placeholder="如 5#泵出口总管压力表"></label>'
    + areaFld
    + '<label class="fld"><span>安装位置</span><input class="input" id="ngPos" placeholder="如 一号泵站 泵房"></label>'
    + '<label class="fld"><span>量程</span><select class="sel" id="ngRange">'
    + '<optgroup label="低压（kPa 级）">'
    + '<option value="0.016">0 ~ 16 kPa（0.016 MPa）</option>'
    + '<option value="0.025">0 ~ 25 kPa（0.025 MPa）</option>'
    + '<option value="0.04">0 ~ 40 kPa（0.040 MPa）</option>'
    + '<option value="0.06">0 ~ 60 kPa（0.060 MPa）</option>'
    + '<option value="0.1">0 ~ 100 kPa（0.100 MPa）</option>'
    + '<option value="0.16">0 ~ 160 kPa（0.160 MPa）</option>'
    + '<option value="0.25">0 ~ 250 kPa（0.250 MPa）</option>'
    + '<option value="0.4">0 ~ 400 kPa（0.400 MPa）</option>'
    + '<option value="0.6">0 ~ 600 kPa（0.600 MPa）</option>'
    + '</optgroup>'
    + '<optgroup label="常用（MPa 级）">'
    + '<option value="1.0">0 ~ 1.0 MPa</option>'
    + '<option value="1.6" selected>0 ~ 1.6 MPa</option>'
    + '<option value="2.5">0 ~ 2.5 MPa</option>'
    + '<option value="4.0">0 ~ 4.0 MPa</option>'
    + '<option value="6.0">0 ~ 6.0 MPa</option>'
    + '<option value="10">0 ~ 10 MPa</option>'
    + '<option value="16">0 ~ 16 MPa</option>'
    + '</optgroup></select></label>'
    + '<label class="fld"><span>精度等级</span><select class="sel" id="ngAcc"><option value="1.6">1.6 级</option><option value="2.5">2.5 级</option><option value="1.0">1.0 级</option></select></label>'
    + '</div>'
    + '<div class="muted" style="font-size:12px;">新增后即时参与整点判定；量程与精度决定该表的预警 / 报警阈值，请与铭牌一致。</div>',
    '<button class="btn btn-ghost" data-act="closeModal">取消</button>'
    + '<button class="btn btn-primary" data-act="saveGauge">保存并启用</button>');
}
function saveGauge() {
  const id = $('#ngId').value.trim().toUpperCase();
  const name = $('#ngName').value.trim();
  if (!id || !name) { toast('表号与名称为必填项', false); return; }
  if (gaugeOf(id)) { toast('表号 ' + id + ' 已存在', false); return; }
  GAUGES.push({
    id, name,
    area: isAdmin() ? $('#ngArea').value : SESSION.area,
    pos: $('#ngPos').value.trim() || '待补充',
    type: 'Y-100 弹簧管',
    range: parseFloat($('#ngRange').value), acc: parseFloat($('#ngAcc').value),
    status: 'active', installDate: '2026-10-04',
  });
  pushAudit('新增表计', '新增 ' + id + ' ' + name);
  closeModal(); renderApp();
  toast('已新增 ' + id + '，即刻参与判定');
}
function openDeleteGauge(id) {
  const g = gaugeOf(id); if (!g) return;
  const n = READINGS.filter(r => r.gaugeId === id).length;
  openModal('删除压力表',
    '<div class="modal-warn">' + ICONS.alert
    + '<div>删除为<b>不可逆操作</b>：将同时删除 <b>' + id + '</b> 的台账信息与 <b>' + n + '</b> 条历史读数。该表此前产生的审计日志保留。</div></div>'
    + '<table style="margin-bottom:14px;"><tbody>'
    + '<tr><td class="muted" style="width:90px;border:none;padding:6px 0;">表号</td><td style="border:none;padding:6px 0;" class="tbl-strong">' + g.id + '</td></tr>'
    + '<tr><td class="muted" style="border:none;padding:6px 0;">名称</td><td style="border:none;padding:6px 0;">' + esc(g.name) + '</td></tr>'
    + '<tr><td class="muted" style="border:none;padding:6px 0;">位置</td><td style="border:none;padding:6px 0;">' + esc(g.pos) + '</td></tr>'
    + '</tbody></table>'
    + '<label class="fld"><span>删除原因（写入审计日志）*</span>'
    + '<textarea class="input" id="delReason" placeholder="如：表计损坏已更换 / 随设备拆除停用"></textarea></label>',
    '<button class="btn btn-ghost" data-act="closeModal">取消</button>'
    + '<button class="btn btn-danger" data-act="confirmDelete" data-gauge="' + id + '">确认删除</button>');
}
function doDeleteGauge(id) {
  const reason = ($('#delReason') || {}).value || '';
  if (!reason.trim()) { toast('请填写删除原因', false); return; }
  GAUGES = GAUGES.filter(g => g.id !== id);
  READINGS = READINGS.filter(r => r.gaugeId !== id);
  Object.keys(HANDLES).forEach(k => { if (k.startsWith(id + '@')) delete HANDLES[k]; });
  pushAudit('删除表计', '删除 ' + id + '（原因：' + reason.trim() + '）');
  closeModal();
  if (NAV === 'detail' && DETAIL_ID === id) NAV = 'gauges';
  renderApp();
  toast('已删除 ' + id);
}
function openDeleteRequest(id) {
  const g = gaugeOf(id); if (!g) return;
  if (DEL_REQUESTS.some(r => r.gaugeId === id && r.status === 'pending')) {
    toast('该表已有待审批的删除申请', false); return;
  }
  openModal('申请删除压力表',
    '<div class="modal-warn" style="background:var(--warn-bg);border-color:var(--warn-line);color:#7A4A08;">'
    + ICONS.alert + '<div>普通用户提交的删除申请需<b>管理员审批</b>后生效。审批期间该表标记「待审批删除」，<b>暂停参与判定</b>，数据保留。</div></div>'
    + '<div style="margin-bottom:14px;font-size:13.5px;">申请删除 <b>' + g.id + '</b> ' + esc(g.name) + '（' + esc(g.pos) + '）</div>'
    + '<label class="fld"><span>申请原因 *</span>'
    + '<textarea class="input" id="reqReason" placeholder="请说明损坏情况 / 更换情况，便于管理员审批"></textarea></label>',
    '<button class="btn btn-ghost" data-act="closeModal">取消</button>'
    + '<button class="btn btn-primary" data-act="submitDelReq" data-gauge="' + id + '">提交申请</button>');
}
function submitDeleteRequest(id) {
  const reason = ($('#reqReason') || {}).value || '';
  if (!reason.trim()) { toast('请填写申请原因', false); return; }
  const g = gaugeOf(id);
  DEL_REQUESTS.unshift({
    id: 'REQ-' + Date.now().toString().slice(-10), gaugeId: id,
    applicant: SESSION.name, acc: SESSION.acc, reason: reason.trim(),
    ts: fmtDateTime(new Date()), status: 'pending',
  });
  if (g) g.status = 'pending';
  pushAudit('申请删除', '申请删除 ' + id + ' ' + (g ? g.name : '') + '（待审批）');
  closeModal(); renderApp();
  toast('删除申请已提交，等待管理员审批');
}
function reviewRequest(reqId, approve) {
  const r = DEL_REQUESTS.find(x => x.id === reqId);
  if (!r || r.status !== 'pending') return;
  const g = gaugeOf(r.gaugeId);
  r.status = approve ? 'approved' : 'rejected';
  r.reviewer = SESSION.name; r.reviewTs = fmtDateTime(new Date());
  if (approve) {
    GAUGES = GAUGES.filter(x => x.id !== r.gaugeId);
    READINGS = READINGS.filter(x => x.gaugeId !== r.gaugeId);
    Object.keys(HANDLES).forEach(k => { if (k.startsWith(r.gaugeId + '@')) delete HANDLES[k]; });
    pushAudit('批准删除', '批准删除 ' + r.gaugeId + '（申请人：' + r.applicant + '）');
    toast('已批准，' + r.gaugeId + ' 及其历史读数已删除');
  } else {
    if (g) g.status = 'active';
    pushAudit('驳回删除', '驳回 ' + r.gaugeId + ' 的删除申请（申请人：' + r.applicant + '）');
    toast('已驳回，该表恢复参与判定');
  }
  renderApp();
}

/* ================= 删除审批页 ================= */
function vApproval() {
  const pend = isAdmin() ? pendingRequests() : DEL_REQUESTS.filter(r => r.acc === SESSION.acc);
  const hist = DEL_REQUESTS.filter(r => r.status !== 'pending');
  const reqCard = r => {
    const g = gaugeOf(r.gaugeId);
    return '<div class="card"><div class="card-body">'
      + '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:8px;">'
      + '<span class="tbl-strong" style="font-size:15px;">' + r.gaugeId + '</span>'
      + '<span style="color:var(--txt-2);">' + esc(g ? g.name : '（已删除）') + '</span>'
      + '<span class="badge warn">待审批</span>'
      + '<span class="muted" style="margin-left:auto;font-size:12px;">' + r.ts + '</span></div>'
      + '<div style="font-size:13px;color:var(--txt-2);background:#F8FAFC;border-radius:8px;padding:10px 14px;margin-bottom:12px;">'
      + '<b style="color:var(--txt-1);">' + esc(r.applicant) + '</b>：' + esc(r.reason) + '</div>'
      + (isAdmin()
        ? '<div style="display:flex;gap:10px;">'
          + '<button class="btn btn-sm btn-danger-ghost" data-act="reject" data-req="' + r.id + '">驳回</button>'
          + '<button class="btn btn-sm btn-primary" data-act="approve" data-req="' + r.id + '">批准删除</button></div>'
        : '<div class="muted" style="font-size:12px;">已提交，等待管理员审批…</div>')
      + '</div></div>';
  };
  const histRows = hist.map(r =>
    '<tr><td class="num">' + r.ts + '</td><td class="tbl-strong">' + r.gaugeId + '</td>'
    + '<td>' + esc(r.applicant) + '</td>'
    + '<td style="font-size:12.5px;color:var(--txt-2);max-width:320px;">' + esc(r.reason) + '</td>'
    + '<td>' + (r.status === 'approved' ? '<span class="badge alarm">已批准删除</span>' : '<span class="badge safe">已驳回</span>') + '</td>'
    + '<td style="font-size:12.5px;">' + esc(r.reviewer || '—') + '<div class="muted" style="font-size:11.5px;">' + (r.reviewTs || '') + '</div></td></tr>'
  ).join('');

  return '<div class="page-head"><div><div class="ph-sub">'
    + (isAdmin() ? '普通用户提交的删除申请在此审批；批准后表计与历史读数一并删除'
                 : '你提交的删除申请及处理进度')
    + '</div></div></div>'
    + (pend.length ? pend.map(reqCard).join('')
        : '<div class="card"><div class="empty">暂无待审批的删除申请</div></div>')
    + '<div class="card"><div class="card-head"><h3>审批历史</h3></div>'
    + '<div class="card-body flush">'
    + (histRows ? '<table><thead><tr><th class="num">申请时间</th><th>表号</th><th>申请人</th><th>申请原因</th><th>结果</th><th>审批人</th></tr></thead><tbody>' + histRows + '</tbody></table>'
        : '<div class="empty">暂无审批历史</div>')
    + '</div></div>';
}

/* ================= 处置流转 ================= */
function doHandle(id, start, to) {
  const run = { start };
  const full = abnormalRun(id, CUR_HOUR || hoursList().slice(-1)[0]);
  setHandle(id, full && full.start === start ? full : run, to);
  renderApp();
  toast(id + ' 异常段已标记为「' + HANDLE_TEXT[to] + '」');
}

/* ================= 导出 ================= */
function exportCsv() {
  if (!isAdmin()) { toast('普通用户无导出权限', false); return; }
  const hours = hoursList();
  let csv = '整点,表号,名称,区域,变送器,识别读数,Δ,单位,δ(%FS),状态\n';
  hours.forEach(h => scopeGauges().forEach(g => {
    const j = judgeAt(g.id, h); if (!j) return;
    const u = deltaUnit(g), k = u === 'kPa' ? 1000 : 1, d = u === 'kPa' ? 2 : 3;
    csv += [h, g.id, g.name, AREAS[g.area],
      fmt(j.r.std * k, d), fmt(j.r.meas * k, d),
      (j.delta > 0 ? '+' : '') + fmt(j.delta * k, d), u,
      fmt(j.pct, 2), STATE_TEXT[j.state]].join(',') + '\n';
  }));
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob(['\uFEFF' + csv], { type: 'text/csv' }));
  a.download = '压力表判定台账_' + hours[0].slice(0,10) + '.csv';
  a.click(); URL.revokeObjectURL(a.href);
  pushAudit('导出数据', '导出判定台账（' + hours.length + ' 个整点 × ' + scopeGauges().length + ' 台）');
  toast('台账已导出');
}

/* ================= 阈值配置 ================= */
function vConfig() {
  const ex = { range: 1.6, acc: 1.6 };
  const warnAbs = ex.range * ex.acc / 100 * CONFIG.warnCoef;
  const alarmAbs = ex.range * ex.acc / 100 * CONFIG.alarmCoef;
  return '<div class="page-head"><div><div class="ph-sub">阈值按「精度等级 × 系数」计算，适用于全部表计；保存后全量数据立即重算</div></div></div>'
    + '<div class="two-col">'
    + '<div class="card"><div class="card-head"><h3>判定阈值</h3></div><div class="card-body">'
    + '<label class="fld"><span>预警系数（× 精度等级）</span>'
    + '<input class="input" id="cfgWarn" type="number" step="0.05" min="0.1" max="1" value="' + CONFIG.warnCoef + '"></label>'
    + '<label class="fld"><span>报警系数（× 精度等级）</span>'
    + '<input class="input" id="cfgAlarm" type="number" step="0.05" min="0.2" max="2" value="' + CONFIG.alarmCoef + '"></label>'
    + '<div style="background:#F8FAFC;border-radius:8px;padding:12px 14px;font-size:12.5px;color:var(--txt-2);margin-bottom:16px;">'
    + '以量程 1.6 MPa / 1.6 级表为例（MPE = ±' + fmt(ex.range*ex.acc/100, 4) + ' MPa）：<br>'
    + '预警线 |Δ| &gt; <b>' + fmt(warnAbs, 4) + ' MPa</b>（δ=' + fmt(ex.acc*CONFIG.warnCoef, 2) + '%FS）<br>'
    + '报警线 |Δ| &gt; <b>' + fmt(alarmAbs, 4) + ' MPa</b>（δ=' + fmt(ex.acc*CONFIG.alarmCoef, 2) + '%FS）</div>'
    + '<button class="btn btn-primary" data-act="saveConfig">保存并全量重算</button>'
    + '</div></div>'
    + '<div class="card"><div class="card-head"><h3>判定口径</h3><span class="hint">JJG 52 弹性元件式一般压力表检定规程</span></div>'
    + '<div class="card-body flush"><table>'
    + '<thead><tr><th>状态</th><th>判定条件</th><th>处置要求</th></tr></thead><tbody>'
    + '<tr><td><span class="badge safe"><i></i>安全</span></td><td>δ ≤ 精度等级 × ' + fmt(CONFIG.warnCoef,2) + '</td><td>正常使用，按周期复检</td></tr>'
    + '<tr><td><span class="badge warn"><i></i>预警</span></td><td>精度等级 × ' + fmt(CONFIG.warnCoef,2) + ' &lt; δ ≤ 精度等级 × ' + fmt(CONFIG.alarmCoef,2) + '</td><td>缩短复检周期，持续观察</td></tr>'
    + '<tr><td><span class="badge alarm"><i></i>报警</span></td><td>δ &gt; 精度等级 × ' + fmt(CONFIG.alarmCoef,2) + '</td><td>立即停用检修，判定不合格</td></tr>'
    + '</tbody></table></div>'
    + '<div class="card-body" style="font-size:12.5px;color:var(--txt-2);">'
    + '判定依据为<b>引用误差 δ = |识别读数 − 变送器| / 量程 × 100%</b>，取所有整点中最差状态定级；'
    + '不使用相对误差（低压端会失真）。任一测点报警即判该表不合格。</div>'
    + '</div></div>';
}
function doSaveConfig() {
  const w = parseFloat($('#cfgWarn').value), a = parseFloat($('#cfgAlarm').value);
  if (isNaN(w) || isNaN(a) || w <= 0 || a <= 0 || w >= a) {
    toast('系数不合法：需满足 0 < 预警系数 < 报警系数', false); return;
  }
  const ow = CONFIG.warnCoef;
  CONFIG.warnCoef = w; CONFIG.alarmCoef = a;
  pushAudit('调整阈值', '预警系数 ' + ow + ' → ' + w + '，报警系数 → ' + a + '，全量数据已重算');
  CUR_HOUR = null;
  renderApp();
  toast('阈值已保存，全部数据已按新口径重判');
}

/* ================= 用户管理 ================= */
function vUsers() {
  const rows = USERS.map(u =>
    '<tr><td class="tbl-strong">' + u.acc + '</td><td>' + esc(u.name) + '</td>'
    + '<td>' + (u.role === 'admin' ? '<span class="badge navy">管理员</span>' : '<span class="badge info">操作员</span>') + '</td>'
    + '<td>' + (u.area ? AREAS[u.area] : '全部区域') + '</td>'
    + '<td class="num">' + u.lastLogin + '</td>'
    + '<td>' + (u.status === '启用' ? '<span class="badge safe"><i></i>启用</span>' : '<span class="badge neutral">停用</span>') + '</td>'
    + '<td>' + (u.acc === 'admin' ? '<span class="muted">—</span>'
        : '<span class="link" data-act="toggleUser" data-acc="' + u.acc + '">' + (u.status === '启用' ? '停用' : '启用') + '</span>')
    + '</td></tr>').join('');
  return '<div class="page-head"><div><div class="ph-sub">管理员可见全部区域并可配置系统；操作员仅见本区域、只读数据、删除需审批</div></div>'
    + '<div class="head-actions"><button class="btn btn-primary" data-act="addUser">＋ 新增用户</button></div></div>'
    + '<div class="card"><div class="card-body flush">'
    + '<table><thead><tr><th>账号</th><th>姓名</th><th>角色</th><th>负责区域</th><th class="num">最近登录</th><th>状态</th><th>操作</th></tr></thead><tbody>'
    + rows + '</tbody></table></div></div>'
    + '<div class="card"><div class="card-head"><h3>角色权限对照</h3></div>'
    + '<div class="card-body flush"><table>'
    + '<thead><tr><th>能力</th><th>操作员</th><th>管理员</th></tr></thead><tbody>'
    + '<tr><td>查看判定结果 / 热力图 / 趋势</td><td>仅本区域</td><td>全部区域</td></tr>'
    + '<tr><td>导入整点数据</td><td><span class="badge safe">可</span></td><td><span class="badge safe">可</span></td></tr>'
    + '<tr><td>新增压力表</td><td>仅本区域</td><td>全部区域</td></tr>'
    + '<tr><td>删除压力表</td><td><span class="badge warn">需提交审批</span></td><td><span class="badge safe">直接删除</span></td></tr>'
    + '<tr><td>导出判定台账</td><td><span class="badge alarm">不可</span></td><td><span class="badge safe">可</span></td></tr>'
    + '<tr><td>阈值配置 / 用户管理 / 审计日志</td><td><span class="badge alarm">不可见</span></td><td><span class="badge safe">可</span></td></tr>'
    + '</tbody></table></div></div>';
}
function openAddUser() {
  openModal('新增用户',
    '<div class="form-grid">'
    + '<label class="fld"><span>账号 *</span><input class="input" id="nuAcc" placeholder="登录账号，如 wangfang"></label>'
    + '<label class="fld"><span>姓名 *</span><input class="input" id="nuName" placeholder="真实姓名"></label>'
    + '<label class="fld"><span>角色</span><select class="sel" id="nuRole"><option value="user">操作员</option><option value="admin">管理员</option></select></label>'
    + '<label class="fld"><span>负责区域（操作员必填）</span><select class="sel" id="nuArea">'
    + Object.keys(AREAS).map(k => '<option value="' + k + '">' + AREAS[k] + '</option>').join('')
    + '</select></label></div>',
    '<button class="btn btn-ghost" data-act="closeModal">取消</button>'
    + '<button class="btn btn-primary" data-act="saveUser">创建用户</button>');
}
function doSaveUser() {
  const acc = $('#nuAcc').value.trim(), name = $('#nuName').value.trim();
  if (!acc || !name) { toast('账号与姓名为必填项', false); return; }
  if (USERS.some(u => u.acc === acc)) { toast('账号已存在', false); return; }
  const role = $('#nuRole').value;
  USERS.push({ acc, name, role, area: role === 'admin' ? null : $('#nuArea').value, status: '启用', lastLogin: '—' });
  pushAudit('新增用户', '新增' + (role === 'admin' ? '管理员' : '操作员') + ' ' + name + '（' + acc + '）');
  closeModal(); renderApp();
  toast('已创建用户 ' + name);
}
function doToggleUser(acc) {
  const u = USERS.find(x => x.acc === acc); if (!u) return;
  u.status = u.status === '启用' ? '停用' : '启用';
  pushAudit(u.status === '启用' ? '启用用户' : '停用用户', u.name + '（' + acc + '）');
  renderApp(); toast(u.name + ' 已' + u.status);
}

/* ================= 审计日志 ================= */
function vAudit() {
  const sel = $('#auditType') ? $('#auditType').value : '';
  const types = [...new Set(AUDIT.map(a => a.action))];
  const rows = AUDIT.filter(a => !sel || a.action === sel).map(a =>
    '<tr><td class="num" style="white-space:nowrap;">' + a.ts + '</td>'
    + '<td>' + esc(a.user) + '</td>'
    + '<td><span class="badge info">' + esc(a.action) + '</span></td>'
    + '<td style="font-size:13px;color:var(--txt-2);">' + esc(a.detail) + '</td></tr>').join('');
  return '<div class="page-head"><div><div class="ph-sub">登录、导入、阈值调整、删除审批、异常处置全量留痕，共 ' + AUDIT.length + ' 条</div></div>'
    + '<div class="head-actions"><select class="sel" id="auditType"><option value="">全部操作类型</option>'
    + types.map(t => '<option value="' + t + '"' + (sel === t ? ' selected' : '') + '>' + t + '</option>').join('')
    + '</select></div></div>'
    + '<div class="card"><div class="card-body flush">'
    + '<table><thead><tr><th class="num">时间</th><th>操作人</th><th>操作类型</th><th>详情</th></tr></thead><tbody>'
    + (rows || '<tr><td colspan="4" class="empty">无匹配记录</td></tr>') + '</tbody></table></div></div>';
}

/* ================= 初始化 ================= */
(function init() {
  /* 隐藏文件输入 */
  const fi = document.createElement('input');
  fi.type = 'file'; fi.id = 'fileInput'; fi.accept = '.csv,.xlsx,.xls';
  fi.style.display = 'none';
  fi.addEventListener('change', () => { handleFile(fi.files[0]); fi.value = ''; });
  document.body.appendChild(fi);

  /* 取消预览按钮（渲染在预览块内，委托里没有，单独注册） */
  document.addEventListener('click', e => {
    if (e.target.closest('[data-act="closePreview"]')) { PENDING_IMPORT = null; renderMain(); }
  });

  /* 拖拽上传 */
  document.addEventListener('dragover', e => {
    const dz = e.target.closest('#dz');
    if (dz) { e.preventDefault(); dz.classList.add('over'); }
  });
  document.addEventListener('dragleave', e => {
    const dz = e.target.closest('#dz');
    if (dz) dz.classList.remove('over');
  });
  document.addEventListener('drop', e => {
    const dz = e.target.closest('#dz');
    if (dz) { e.preventDefault(); dz.classList.remove('over'); handleFile(e.dataTransfer.files[0]); }
  });

  /* 实时同步：若当前由同步服务托管则自动进入实时模式 */
  if (typeof Sync !== 'undefined' && typeof Sync.init === 'function') Sync.init();
})();
