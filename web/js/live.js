'use strict';
/* 两组独立读数按次数绘制；当前配置下，第 n 条与第 n 条计算偏差。 */
(function () {
  if (!/^https?:$/.test(location.protocol) || new URLSearchParams(location.search).get('view')==='comparison') return;
  document.body.classList.add('monitor-theme');
  let state = null, connected = false, savedVersion = null, imageId = null;
  let chartSelection=null,activeChartPair=null,chartSignature='';
  const selected=new Set();
  let changingRecords=false, lastRoute=null;
  const host = document.getElementById('liveMonitor');
  const $ = id => document.getElementById(id);
  const number = n => typeof n==='number' && Number.isFinite(n);
  const fmt = n => number(n) ? n.toFixed(2) : '—';
  const esc = s => String(s ?? '').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const time = (s,full=false) => s && !isNaN(Date.parse(s)) ? new Intl.DateTimeFormat('zh-CN',
    {timeZone:'Asia/Shanghai',...(full?{month:'2-digit',day:'2-digit'}:{}),hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(s)) : '—';
  const labels = {recognized:'识别完成',review:'识别需复核',failed:'识别失败',normal:'偏差正常',warning:'偏差预警',alarm:'偏差超限',pending:'待比较'};
  const readingState = p => !number(p.valueKpa) ? 'failed' : p.status!=='ok' || p.valueKpa<0 || p.valueKpa>state.meter.rangeKpa ? 'review' : 'recognized';
  function comparisonRows(data=state){
    if(Array.isArray(data?.comparisonRows)&&(data.comparisonMode==='sequence'||data.transmitter?.enabled))return data.comparisonRows||[];
    return (data?.readings||[]).map((p,i)=>({sequence:i+1,recognitionId:p.id,recognitionValueKpa:p.valueKpa,
      recognitionCapturedAt:p.capturedAt,transmitterPresent:number(p.transmitterKpa),transmitterKpa:p.transmitterKpa,
      transmitterRecordedAt:p.transmitterRecordedAt,deltaKpa:p.deltaKpa,percentFS:p.percentFS,state:p.state,
      pairingStatus:number(p.deltaKpa)?'paired':'waiting_transmitter'}));
  }
  function resolveChartPair(rows,selection){
    let row=null;
    if(selection){
      if(selection.source==='transmitter'&&selection.transmitterStamp)row=rows.find(p=>p.transmitterStamp===selection.transmitterStamp);
      else if(selection.recognitionId)row=rows.find(p=>p.recognitionId===selection.recognitionId);
      else row=rows.find(p=>p.sequence===selection.sequence);
    }
    if(row)return {row,selection};
    return {row:rows.slice().reverse().find(p=>number(p.deltaKpa))||rows[rows.length-1]||null,selection:null};
  }
  function comparisonLabel(row){
    if(!row)return '待配对';
    if(row.triggerStatus==='missing_frame')return '缺少历史画面';
    if(row.state==='pending')return row.pairingStatus==='waiting_recognition'?'等待识别':'等待变送器';
    if(row.state==='review'&&row.transmitterPresent&&!number(row.transmitterKpa))return '单位待确认';
    return labels[row.state]||'待配对';
  }
  const liveRoute = () => ['dashboard','trend'].includes(NAV);
  function shellTopbar() {
    if (!SESSION || !liveRoute()) return;
    const bar=$('topbarRight');
    if (!$('liveShellConnection')) {
      bar.innerHTML='<span class="live-system-clock"><small>系统时间</small><b id="liveShellClock">—</b></span><span id="liveShellConnection" class="live-badge"></span><button id="liveShellRefresh" class="btn btn-ghost">刷新读数</button>';
      $('liveShellRefresh').addEventListener('click',refresh);
    }
    if($('liveShellClock')) $('liveShellClock').textContent=time(new Date().toISOString());
    $('liveShellConnection').textContent=connected?'实时数据已连接':'实时数据连接中 · 自动重试';
    $('liveShellConnection').className='live-badge '+(connected?'normal':'failed');
  }
  function attach(main, nav) {
    mount();
    if (host.parentElement!==main) main.replaceChildren(host);
    host.hidden=false;
    host.classList.add('live-embedded');
    host.classList.toggle('live-trend-page',nav==='trend');
    const routeChanged=lastRoute!==nav;
    if(routeChanged) {main.scrollTop=0;lastRoute=nav;}
    shellTopbar();
    if(routeChanged&&state)drawLiveChart(true);
  }
  function detach() {
    if (host.parentElement!==document.body) document.body.appendChild(host);
    host.hidden=true; lastRoute=null;
  }
  window.CameraLive={isActive:()=>true,attach,detach,renderTopbar:shellTopbar,
    alarmCount:()=>0};
  function mount() {
    if (host.childElementCount) return;
    host.innerHTML = `<header class="live-top"><h1>压力表实时监测</h1><span id="liveConnection" class="live-badge">正在连接</span><button id="liveRefresh" class="btn">刷新</button></header>
      <div class="live-container">
        <div class="live-heading"><div><p class="live-eyebrow">单表实时监测</p><h2 id="liveMeterName">测试压力表</h2><p id="liveMeterMeta"></p></div><span id="liveCaptureState" class="live-badge">等待采集</span></div>
        <div id="liveBanner" class="live-banner" hidden></div>
        <div class="live-metrics">
          <section class="live-card live-metric primary"><div class="live-kicker"><span>最新识别读数</span><span id="livePointState" class="live-badge">等待数据</span></div><div class="live-value blue"><span id="liveMeasured">—</span><small>MPa</small></div><p id="liveCapturedAt" class="live-caption">等待第一张抓拍照片</p><p id="liveLastSuccess" class="live-caption" hidden></p></section>
          <section class="live-card live-metric secondary live-transmitter-metric"><div class="live-kicker"><span>最新变送器读数</span><span id="liveTransmitterState" class="live-badge">待接入</span></div><div class="live-value"><span id="liveReference">—</span><small>MPa</small></div><p id="liveReferenceTime" class="live-caption">等待变送器实时数据</p><p id="liveReferenceSync" class="live-caption"></p></section>
          <section class="live-card live-metric secondary"><div class="live-kicker"><span id="liveCompareTitle">最近配对偏差</span><span id="liveCompareState" class="live-badge">待比较</span></div><div class="live-value"><span id="liveDelta">—</span><small>MPa</small></div><p id="livePercent" class="live-caption">视觉读数 − 配对变送器读数</p></section>
        </div>
        <div class="live-overview">
          <section class="live-card live-trend-card"><div class="live-card-head"><div class="live-section-title"><h3>读数变化趋势</h3><p id="liveTrendDescription">按有效读数次数连续展示</p></div><div class="live-legend"><span id="liveRecognitionLegend">识别</span><span id="liveReferenceLegend" class="reference unavailable">变送器待接入</span></div></div><div id="liveChart" class="live-chart"></div><div id="liveChartSelection" class="live-chart-selection" aria-live="polite" hidden><div class="live-chart-selection-head"><strong id="liveSelectedSequence"></strong><span id="liveSelectedState" class="live-badge"></span><button id="liveFollowLatest" class="btn btn-sm btn-ghost" hidden>返回最新配对</button></div><div class="live-chart-selection-values"><span>识别 <b id="liveSelectedRecognition">—</b> MPa</span><span>变送器 <b id="liveSelectedTransmitter">—</b> MPa</span><span>偏差 <b id="liveSelectedDelta">—</b> MPa</span><span>引用误差 <b id="liveSelectedPercent">—</b> %FS</span></div><p id="liveSelectedNote" class="live-note"></p></div></section>
          <section class="live-card live-photo-card"><div class="live-card-head"><h3>最新抓拍照片</h3><span id="livePhotoTime" class="live-subtle">—</span></div><div class="live-photo"><img id="liveImage" alt="最新压力表抓拍照片" hidden><div id="liveImageEmpty" class="live-photo-empty"><svg width="45" height="45" viewBox="0 0 48 48" fill="none"><rect x="5" y="12" width="38" height="29" rx="5" stroke="currentColor" stroke-width="2"/><path d="M16 12l3-5h10l3 5" stroke="currentColor" stroke-width="2"/><circle cx="24" cy="26" r="8" stroke="currentColor" stroke-width="2"/></svg><span id="liveImageMessage">等待摄像头抓拍</span></div></div><details class="live-photo-details"><summary>识别详情</summary><p id="liveReadingNote" class="live-note">新照片识别完成后自动更新</p></details></section>
        </div>
        <section class="live-card live-source-card" aria-label="数据接入状态"><div class="live-source-row"><div><b>摄像头识别系统</b><p>读数与照片同步更新</p></div><span id="liveRecognitionSource" class="live-badge">等待连接</span></div><div class="live-source-row"><div><b>变送器数据源</b><p id="liveTransmitterDetail">接入后自动进行读数比较</p></div><span id="liveTransmitterSource" class="live-badge">未接入</span></div></section>
        <section class="live-card live-history-card"><div class="live-card-head"><div class="live-section-title"><h3>时间配对记录</h3><p>按时间一对一匹配；超出容差保持未配对，不补旧值</p></div><span id="liveHistoryCount" class="live-subtle"></span></div><div class="live-history-actions"><button id="liveDeleteSelected" class="btn btn-danger-ghost" disabled>删除选中识别</button><button id="liveRestoreLast" class="btn btn-ghost" hidden>撤销上次删除</button><span id="liveDeleteMessage" class="live-subtle" role="status">可删除识别记录并撤销；删除后按保留记录重新编号。</span></div><div class="live-table-wrap"><table class="live-table"><thead><tr><th><input id="liveSelectAll" type="checkbox" aria-label="选择全部可见识别记录"></th><th>次数</th><th>识别 / MPa</th><th>变送器 / MPa</th><th>偏差 / MPa</th><th>引用误差 / %FS</th><th>状态</th><th>识别操作</th></tr></thead><tbody id="liveHistory"></tbody></table></div></section>
        <section class="live-card live-transmitter-history"><div class="live-card-head"><div class="live-section-title"><h3>变送器上报记录</h3><p>独立展示已收到的数据，摄像头停止时仍会更新</p></div><span id="liveTransmitterCount" class="live-subtle"></span></div><div class="live-table-wrap"><table class="live-table"><thead><tr><th>次数</th><th>平台接收时间</th><th>变送器 / MPa</th><th>原始读数</th><th>服务器接收时间</th><th>状态</th></tr></thead><tbody id="liveTransmitterHistory"></tbody></table></div><p class="live-note live-transmitter-help">同一设备、同一报文时间的重复推送仅保留一条；零压力也是有效数据。</p></section>
        <p class="live-footer">识别完成后自动更新 · 所有压力读数统一显示为 MPa</p>
      </div>`;
    $('liveRefresh').addEventListener('click', refresh);
    function selectChartPoint(event){
      const point=event.target.closest('[data-compare-sequence]');
      if(!point||!state)return;
      const row=comparisonRows().find(p=>p.sequence===Number(point.dataset.compareSequence));
      if(!row)return;
      chartSelection={source:point.dataset.compareSource,sequence:row.sequence,recognitionId:row.recognitionId,transmitterStamp:row.transmitterStamp};
      renderComparisonFocus();updateChartFocus();
    }
    $('liveChart').addEventListener('click',selectChartPoint);
    $('liveChart').addEventListener('keydown',event=>{
      if((event.key==='Enter'||event.key===' ')&&event.target.closest('[data-compare-sequence]')){event.preventDefault();selectChartPoint(event);}
      else if(event.key==='Escape'){chartSelection=null;renderComparisonFocus();updateChartFocus();}
    });
    $('liveFollowLatest').addEventListener('click',()=>{chartSelection=null;renderComparisonFocus();updateChartFocus();});
    if(typeof ResizeObserver!=='undefined') {
      let width=0;
      const observer=new ResizeObserver(entries=>{
        const next=entries[0].contentRect.width;
        if(state && next>0 && Math.abs(next-width)>1) {width=next;drawLiveChart(true);}
      });
      observer.observe($('liveChart'));
    }

    $('liveDeleteSelected').addEventListener('click',()=>changeRecords('delete',{sampleIds:[...selected]}));
    $('liveRestoreLast').addEventListener('click',()=>{if(state?.lastDeletion) changeRecords('restore',{batchId:state.lastDeletion.id});});
    $('liveSelectAll').addEventListener('change',event=>{
      comparisonRows().slice(-20).filter(p=>p.recognitionId).forEach(p=>event.target.checked?selected.add(p.recognitionId):selected.delete(p.recognitionId));
      updateSelection();
    });
    $('liveHistory').addEventListener('change',event=>{
      const id=event.target.dataset.selectSample;
      if(id) {event.target.checked?selected.add(id):selected.delete(id);updateSelection();}
    });
    $('liveHistory').addEventListener('click',event=>{
      const button=event.target.closest('[data-delete-sample]');
      if(button) changeRecords('delete',{sampleIds:[button.dataset.deleteSample]});
    });
    $('liveImage').addEventListener('error',() => { $('liveImage').hidden=true; $('liveImageEmpty').hidden=false; $('liveImageMessage').textContent='本次照片暂不可用'; });
  }
  function connection(ok) {
    connected=ok;
    shellTopbar();
    if ($('liveRecognitionSource')) {
      $('liveRecognitionSource').textContent=ok?'已连接':'连接中断';
      $('liveRecognitionSource').className='live-badge '+(ok?'normal':'failed');
    }
    if (!$('liveConnection')) return;
    $('liveConnection').textContent=ok?'实时连接':'连接中断 · 重试中';
    $('liveConnection').className='live-badge '+(ok?'normal':'failed');
  }
  function apply(data) {
    if (!data || data.mode!=='single') return;
    if(window.CameraResearch?.hold(data))return;
    const wasActive=!!state;
    if(state?.researchControl?.since!==data.researchControl?.since){chartSelection=null;imageId=null;selected.clear();}
    state=data; mount();
    if (!wasActive && SESSION) renderApp();
    if (SESSION && liveRoute()) attach($('main'),NAV);
    const gauge=typeof GAUGES!=='undefined'?GAUGES.find(g=>g.id===data.meter.id):null;
    $('liveMeterName').textContent=gauge?.name || data.meter.name;
    $('liveMeterMeta').textContent=data.meter.id+' · 0～'+data.meter.rangeKpa+' MPa · '+data.meter.accuracyClass+' 级';
    const r=data.latest;
    $('liveMeasured').textContent=fmt(r?.valueKpa);
    const transmitter=data.transmitter||{},reference=transmitter.latest;
    $('liveReference').textContent=fmt(reference?.valueKpa);
    $('liveReferenceTime').textContent=reference?'平台接收于 '+time(reference.vendorReceivedAt,true)+(number(reference.valueKpa)?'':' · 单位待确认'):'等待变送器实时数据';
    $('liveReferenceSync').textContent=transmitter.enabled?'已同步 '+(transmitter.recordCount||0)+' 条 · 最近同步 '+time(transmitter.lastSyncAt):'';
    const rows=comparisonRows(data);
    renderComparisonFocus(rows);
    const hasReference=(transmitter.readings||[]).some(p=>number(p.valueKpa));
    const hasRecognition=data.readings.some(p=>number(p.valueKpa));
    $('liveRecognitionLegend').hidden=false;
    $('liveRecognitionLegend').className=hasRecognition?'':'unavailable';
    $('liveTrendDescription').textContent=data.recognitionMode==='transmitter'?'新报文触发识别 · 按报文编号绑定 · 使用历史画面':data.comparisonMode==='time'?'按时间展示 · 平台接收时间近似配对 · 偏差 = 视觉 − 变送器':'两条曲线按次数对齐';
    $('liveReferenceLegend').textContent='变送器';
    $('liveReferenceLegend').className='reference'+(hasReference?'':' unavailable');
    if(data.comparisonMode!=='time')$('liveCapturedAt').textContent=r?'采集于 '+time(r.capturedAt,true):'等待第一张抓拍照片';
    const lastSuccess=data.readings.slice().reverse().find(p=>readingState(p)==='recognized');
    $('liveLastSuccess').hidden=!r || readingState(r)==='recognized' || !lastSuccess;
    $('liveLastSuccess').textContent=lastSuccess?'最近成功：'+fmt(lastSuccess.valueKpa)+' MPa（'+time(lastSuccess.capturedAt,true)+'，历史记录）':'';
    const status=r?readingState(r):'pending';
    $('livePointState').textContent=r?labels[status]:'等待数据';
    $('livePointState').className='live-badge '+status;
    if(data.comparisonMode!=='time')$('livePhotoTime').textContent=time(r?.capturedAt);
    if(data.comparisonMode!=='time')$('liveReadingNote').textContent=r?(r.origin!=='camera'?'离线图片验证。':'')+(r.status!=='ok'?r.status+'。':'')+(r.note || '识别结果与上方照片对应'):'新照片识别完成后自动更新';
    if (data.comparisonMode!=='time' && r?.id!==imageId) {
      imageId=r?.id || null;
      $('liveImage').hidden=!r?.image; $('liveImageEmpty').hidden=!!r?.image;
      $('liveImageMessage').textContent=r?'本次采集未保留照片':'等待摄像头抓拍';
      if (r?.image) $('liveImage').src='/api/live/image?sampleId='+encodeURIComponent(r.id);
    }
    renderFreshness();
    if (savedVersion===data.version) return;
    savedVersion=data.version;
    const existing=new Set(data.readings.map(p=>p.id));
    for(const id of selected) if(!existing.has(id)) selected.delete(id);
    const transmitterRows=transmitter.readings||[];
    $('liveTransmitterCount').textContent='已同步 '+transmitterRows.length+' 条，展示最近 20 条';
    $('liveTransmitterHistory').innerHTML=transmitterRows.map((p,i)=>({...p,sequence:i+1})).slice(-20).reverse().map(p=>'<tr><td>第 '+p.sequence+' 次</td><td>'+esc(time(p.vendorReceivedAt,true))+'</td><td>'+fmt(p.valueKpa)+'</td><td>'+esc(p.value)+' '+esc(p.unitName||('单位码 '+p.unitCode))+'</td><td>'+esc(time(p.serverReceivedAt,true))+'</td><td><span class="live-badge '+(number(p.valueKpa)?'normal':'warning')+'">'+(number(p.valueKpa)?'已接收':'单位待确认')+'</span></td></tr>').join('')||'<tr><td colspan="6" style="text-align:center;padding:24px">尚无变送器上报记录</td></tr>';
    $('liveHistoryCount').textContent='识别 '+data.readings.length+' 条 · 变送器 '+transmitterRows.length+' 条 · 已计算 '+rows.filter(p=>number(p.deltaKpa)).length+' 组';
    $('liveHistory').innerHTML=rows.slice(-20).reverse().map(p=>{
      const label=comparisonLabel(p);
      return '<tr data-comparison-row="'+p.sequence+'"><td>'+(p.recognitionId?'<input type="checkbox" data-select-sample="'+esc(p.recognitionId)+'" aria-label="选择第 '+p.sequence+' 次识别记录">':'—')+'</td><td>第 '+p.sequence+' 次</td><td>'+fmt(p.recognitionValueKpa)+'<small class="live-row-time">'+esc(time(p.recognitionCapturedAt,true))+'</small></td><td>'+fmt(p.transmitterKpa)+'<small class="live-row-time">'+esc(time(p.transmitterRecordedAt,true))+'</small></td><td>'+fmt(p.deltaKpa)+'</td><td>'+fmt(p.percentFS)+'</td><td><span class="live-badge '+p.state+'">'+label+'</span></td><td>'+(p.recognitionId?'<button class="btn btn-sm btn-ghost live-delete-one" data-delete-sample="'+esc(p.recognitionId)+'" aria-label="删除第 '+p.sequence+' 次识别记录">删除识别</button>':'—')+'</td></tr>';
    }).join('') || '<tr><td colspan="8" style="text-align:center;color:#7895B6;padding:32px">等待两组读数，按时间自动配对</td></tr>';
    updateSelection();
    if(data.comparisonMode==='time')renderComparisonFocus(rows);
    drawLiveChart();
  }
  function renderComparisonFocus(rows=comparisonRows()){
    const resolved=resolveChartPair(rows,chartSelection);
    chartSelection=resolved.selection;activeChartPair=resolved.row;
    const pair=activeChartPair,compared=number(pair?.deltaKpa),label=comparisonLabel(pair);
    if(state?.comparisonMode==='time'){
      $('liveMeasured').textContent=fmt(pair?.recognitionValueKpa);
      $('livePointState').textContent=pair?.recognitionId?(pair.recognitionStatus==='ok'?'识别完成':'识别需复核'):'无配对视觉记录';
      $('livePointState').className='live-badge '+(pair?.recognitionStatus==='ok'?'normal':'warning');
      $('liveReference').textContent=fmt(pair?.transmitterKpa);
      $('liveCapturedAt').textContent='配对视觉时间：'+time(pair?.recognitionCapturedAt,true);
      $('liveReferenceTime').textContent='配对平台时间：'+time(pair?.transmitterRecordedAt,true);
      const photo=state.readings.find(p=>p.id===pair?.recognitionId);
      $('livePhotoTime').textContent=time(photo?.capturedAt,true);
      $('liveImage').hidden=!photo?.image;$('liveImageEmpty').hidden=!!photo?.image;
      $('liveImageMessage').textContent=photo?'所选记录未保留照片':'所选配对没有视觉照片';
      $('liveReadingNote').textContent=photo?('识别质量：'+photo.status+' · '+(photo.note||'')+' · '+(pair.qualityNote||'按平台接收时间近似匹配')):'等待配对';
      if(photo?.image&&imageId!==photo.id){imageId=photo.id;$('liveImage').src='/api/live/image?sampleId='+encodeURIComponent(photo.id);}
    }

    $('liveCompareTitle').textContent=chartSelection&&pair?'第 '+pair.sequence+' 次偏差':'最近配对偏差';
    $('liveDelta').textContent=fmt(pair?.deltaKpa);
    $('liveCompareState').textContent=label;
    $('liveCompareState').className='live-badge '+(pair?.state||'pending');
    $('livePercent').textContent=compared?'第 '+pair.sequence+' 次：'+fmt(pair.recognitionValueKpa)+' − '+fmt(pair.transmitterKpa)+' MPa · '+fmt(pair.percentFS)+'%FS':pair?'第 '+pair.sequence+' 次 · '+label:'匹配时间范围内的两项有效读数才能计算偏差';
    $('liveChartSelection').hidden=!pair;
    $('liveSelectedSequence').textContent=pair?'第 '+pair.sequence+' 次'+(chartSelection?' · 已选中':' · 最近配对'):'';
    $('liveSelectedState').textContent=label;$('liveSelectedState').className='live-badge '+(pair?.state||'pending');
    $('liveSelectedRecognition').textContent=fmt(pair?.recognitionValueKpa);
    $('liveSelectedTransmitter').textContent=fmt(pair?.transmitterKpa);
    $('liveSelectedDelta').textContent=fmt(pair?.deltaKpa);
    $('liveSelectedPercent').textContent=fmt(pair?.percentFS);
    $('liveFollowLatest').hidden=!chartSelection;
    $('liveSelectedNote').textContent=state?.comparisonMode==='time'?('视觉：'+time(pair?.recognitionCapturedAt,true)+' · 平台：'+time(pair?.transmitterRecordedAt,true)+' · 时间差 '+fmt(pair?.timeDiffSeconds)+' 秒 · 近似配对'):chartSelection?'识别：'+time(pair.recognitionCapturedAt,true)+' · 变送器：'+time(pair.transmitterRecordedAt,true):'点击蓝线或绿线上的数据点，查看该次偏差。';
  }
  function updateChartFocus(){
    const sequence=activeChartPair?.sequence;
    host.querySelectorAll('#liveChart [data-compare-sequence]').forEach(point=>{
      const active=Number(point.dataset.compareSequence)===sequence;
      point.classList.toggle('is-selected',active);point.setAttribute('aria-pressed',String(active));
      point.setAttribute('r',active?'6':point.dataset.baseRadius);
    });
    host.querySelectorAll('#liveHistory [data-comparison-row]').forEach(row=>row.classList.toggle('is-chart-selected',Number(row.dataset.comparisonRow)===sequence));
  }
  function drawLiveChart(force=false){
    if(!state)return;
    if(state.comparisonMode==='time'&&window.CameraResearch){
      window.CameraResearch.render(state,sequence=>{const row=comparisonRows().find(r=>r.sequence===sequence);if(!row)return;chartSelection={source:'recognition',sequence,recognitionId:row.recognitionId,transmitterStamp:row.transmitterStamp};renderComparisonFocus();updateChartFocus();},apply);
      updateChartFocus();return;
    }
    const transmitter=state.transmitter?.readings||[];
    const signature=JSON.stringify([state.readings.map(p=>[p.id,p.valueKpa,p.status,p.capturedAt]),transmitter.map(p=>[p.vendorRecvAtUs,p.valueKpa]),state.meter.rangeKpa,host.classList.contains('live-trend-page')]);
    if(force||signature!==chartSignature){chart(state.readings,state.meter.rangeKpa,transmitter);chartSignature=signature;}
    updateChartFocus();
  }
  function updateSelection() {
    const visible=comparisonRows().slice(-20).filter(p=>p.recognitionId),canDelete=!!state?.deletionSupported;
    host.querySelectorAll('[data-select-sample]').forEach(box=>{
      box.checked=selected.has(box.dataset.selectSample);box.disabled=changingRecords||!canDelete;
    });
    host.querySelectorAll('[data-delete-sample]').forEach(button=>button.disabled=changingRecords||!canDelete);
    $('liveSelectAll').checked=visible.length>0&&visible.every(p=>selected.has(p.recognitionId));
    $('liveSelectAll').indeterminate=visible.some(p=>selected.has(p.recognitionId))&&!$('liveSelectAll').checked;
    $('liveSelectAll').disabled=changingRecords||!canDelete||!visible.length;
    $('liveDeleteSelected').disabled=changingRecords||!canDelete||!selected.size;
    $('liveDeleteSelected').textContent=selected.size?'删除选中识别（'+selected.size+' 条）':'删除选中识别';
    $('liveRestoreLast').hidden=!state?.lastDeletion;
    $('liveRestoreLast').disabled=changingRecords;
    $('liveRestoreLast').textContent='撤销上次删除'+(state?.lastDeletion?'（'+state.lastDeletion.count+' 条）':'');
    if(!canDelete) $('liveDeleteMessage').textContent='请重新启动前端服务以启用数据删除。';
  }
  async function changeRecords(action,input) {
    if(changingRecords || action==='delete'&&!input.sampleIds.length) return;
    changingRecords=true;updateSelection();
    $('liveDeleteMessage').textContent=action==='delete'?'正在删除…':'正在恢复…';
    try {
      const res=await fetch('/api/live/'+action,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(input)});
      const result=await res.json();
      if(!res.ok) throw new Error(result.error||'操作失败');
      apply(result.snapshot);
      $('liveDeleteMessage').textContent=action==='delete'?'已删除 '+result.count+' 条，趋势已更新；可撤销上次删除。':'已恢复 '+result.count+' 条记录。';
    } catch(e) {$('liveDeleteMessage').textContent=e.message;}
    finally {changingRecords=false;updateSelection();}
  }
  function renderFreshness() {
    if($('liveShellClock')) $('liveShellClock').textContent=time(new Date().toISOString());
    if (!state) return;
    const r=state.latest, status=state.captureStatus || {}, stale=state.recognitionMode!=='transmitter' && !state.researchControl?.paused && r && (Date.now()-Date.parse(r.capturedAt))/1000>state.staleAfterSeconds;
    let message=state.problem?'实时记录暂不可读：'+state.problem:'';
    if(!message&&status.lastError)message='上次采集异常：'+status.lastError;
    if (!message && ['error','capture_error'].includes(status.state)) message=status.message;
    if (!message && stale) message='最新读数已超过 '+state.staleAfterSeconds+' 秒未更新，请查看摄像头采集窗口。';
    const labelsCapture={paused:'采集已暂停',waiting:'等待采集',starting:'正在连接摄像头',running:state.recognitionMode==='transmitter'?'缓存中 · 等待报文':'采集中',stopped:'采集已停止',error:'采集异常',capture_error:'抓图重试中'};
    $('liveCaptureState').textContent=stale?'读数未更新':(labelsCapture[status.state] || '等待采集');
    $('liveCaptureState').className='live-badge '+(stale?'warning':status.state==='running'?'normal':['error','capture_error'].includes(status.state)?'failed':'pending');
    const source=state.transmitter||{},latest=source.latest;
    const age=latest?(Date.now()-Date.parse(latest.vendorReceivedAt))/1000:null;
    let sourceLabel='未接入',sourceClass='pending';
    if(source.enabled){
      if(source.status==='error'){sourceLabel='连接异常';sourceClass='failed';}
      else if(!latest){sourceLabel=source.status==='connected'?'等待设备上报':'正在连接';}
      else if(!number(latest.valueKpa)){sourceLabel='单位待确认';sourceClass='warning';}
      else if(age < -5){sourceLabel='时间异常';sourceClass='warning';}
      else if(age>source.staleAfterSeconds){sourceLabel='等待新上报';sourceClass='warning';}
      else if(source.status!=='connected'){sourceLabel='正在连接';}
      else {sourceLabel='已接入';sourceClass='normal';}
    }
    $('liveTransmitterState').textContent=sourceLabel;$('liveTransmitterState').className='live-badge '+sourceClass;
    $('liveTransmitterState').title=sourceLabel==='等待新上报'?'距最近平台报文已超过 '+source.staleAfterSeconds+' 秒；服务器连接状态见下方':'';
    $('liveTransmitterSource').textContent=source.status==='connected'?'服务器已连接':source.status==='error'?'同步异常':source.enabled?'正在连接':'未接入';
    $('liveTransmitterSource').className='live-badge '+(source.status==='connected'?'normal':source.status==='error'?'failed':'pending');
    $('liveTransmitterDetail').textContent=source.problem||(!source.enabled?'等待部署服务器并配置只读密钥，随后启用同步':'设备 '+source.deviceId+' · 已同步 '+(source.recordCount||0)+' 条 · 最近同步 '+time(source.lastSyncAt));
    if(!message&&source.enabled&&source.status==='error')message='变送器同步异常：'+source.problem+'；已收到的数据保留，正在自动重试。';
    $('liveBanner').hidden=!message; $('liveBanner').textContent=message;
  }
  function chart(points,max,transmitterReadings=[]) {
    // 每条源记录占一个次数；两组共用较长一组的横轴，缺项不压缩、不补值。
    const count=Math.max(points.length,transmitterReadings.length);
    const values=points.concat(transmitterReadings).map(p=>p.valueKpa).filter(number);
    if(!values.length){$('liveChart').innerHTML='<div class="live-empty-chart">等待两组有效读数，按第几次绘制对比曲线</div>';return;}
    const width=$('liveChart').clientWidth;
    const W=width?Math.max(320,Math.round(width-40)):1080;
    const H=typeof host!=='undefined'&&!host.classList.contains('live-trend-page')?215:245;
    const L=52,R=24,T=22,B=49,pw=W-L-R,ph=H-T-B;
    const x=i=>L+(count===1?0.5:i/(count-1))*pw;
    const bottom=Math.min(0,...values),top=Math.max(max,...values);
    const y=v=>T+(1-(v-bottom)/(top-bottom||1))*ph;
    let lines='';
    for(let i=0;i<=4;i++){
      const v=bottom+(top-bottom)*i/4,yy=y(v);
      lines+='<line x1="'+L+'" y1="'+yy+'" x2="'+(W-R)+'" y2="'+yy+'" stroke="#233951"/><text x="'+(L-10)+'" y="'+(yy+4)+'" text-anchor="end" fill="#7895B6" font-size="11">'+(+v.toFixed(2))+'</text>';
    }
    function series(rows,key,color){
      const reference=key==='transmitter';
      const segments=[];let segment=[];
      rows.forEach((sample,i)=>{
        if(number(sample.valueKpa)){segment.push({sample,index:i,coords:[x(i),y(sample.valueKpa)]});}
        else if(segment.length){segments.push(segment);segment=[];}
      });
      if(segment.length)segments.push(segment);
      segments.forEach(items=>{
        const coords=items.map(p=>p.coords);
        if(coords.length>1){
          const slopes=coords.slice(1).map((p,i)=>(p[1]-coords[i][1])/(p[0]-coords[i][0]));
          const tangents=coords.map((p,i)=>{
            if(i===0)return slopes[0];
            if(i===coords.length-1)return slopes[i-1];
            const a=slopes[i-1],b=slopes[i];return a*b>0?2*a*b/(a+b):0;
          });
          let path='M '+coords[0].join(' ');
          for(let i=1;i<coords.length;i++){
            const a=coords[i-1],b=coords[i],step=(b[0]-a[0])/3;
            path+=' C '+(a[0]+step)+' '+(a[1]+tangents[i-1]*step)+' '+(b[0]-step)+' '+(b[1]-tangents[i]*step)+' '+b.join(' ');
          }
          lines+='<path data-series="'+key+'" d="'+path+'" fill="none" stroke="'+color+'" stroke-width="2.3"'+(reference?' stroke-dasharray="6 3"':'')+' stroke-linejoin="round" stroke-linecap="round"/>';
        }
        items.forEach(({sample,index,coords:p})=>{
          const review=!reference&&readingState(sample)==='review';
          const stamp=reference?sample.vendorReceivedAt:sample.capturedAt;
          const radius=rows.length>100?2:reference?2.7:3.3;
          lines+='<circle '+(reference?'data-transmitter-sample':'data-sample')+'="'+(index+1)+'" cx="'+p[0]+'" cy="'+p[1]+'" r="'+radius+'" fill="'+(review?'#FFC56E':color)+'" class="live-chart-point" data-base-radius="'+radius+'" data-compare-sequence="'+(index+1)+'" data-compare-source="'+key+'" role="button" tabindex="0" aria-pressed="false" aria-controls="liveChartSelection liveDelta" aria-label="查看第 '+(index+1)+' 次'+(reference?'变送器':'识别')+'点的读数偏差"><title>第 '+(index+1)+' 次'+(reference?'变送器':'识别')+' · '+esc(time(stamp,true))+' · '+fmt(sample.valueKpa)+' MPa'+(review?'（需复核）':'')+'</title></circle>';
        });
      });
    }
    series(points,'recognition','#4CC8FF');
    series(transmitterReadings,'transmitter','#10a58a');
    const ticks=Math.min(6,count,Math.max(2,Math.floor(pw/110)+1)),indices=new Set();
    for(let i=0;i<ticks;i++)indices.add(ticks===1?0:Math.round(i*(count-1)/(ticks-1)));
    indices.forEach(i=>{
      const anchor=count===1?'middle':i===0?'start':i===count-1?'end':'middle';
      lines+='<text x="'+x(i)+'" y="'+(H-27)+'" text-anchor="'+anchor+'" fill="#7895B6" font-size="11">第 '+(i+1)+' 次</text>';
    });
    lines+='<text x="'+(L+pw/2)+'" y="'+(H-7)+'" text-anchor="middle" fill="#7895B6" font-size="11">采集 / 上报次数</text>';
    $('liveChart').innerHTML='<svg viewBox="0 0 '+W+' '+H+'" role="img" aria-label="读数对比趋势，两组按次数对齐，共 '+count+' 次">'+lines+'</svg><p class="live-note" style="padding:0">识别 '+points.length+' 条 · 变送器 '+transmitterReadings.length+' 条 · 第 n 次对应第 n 次，缺项保留空位；悬停查看各自采集时间。</p>';
  }
  async function refresh() {
    try {
      const res=await fetch('/api/live',{cache:'no-store'}); if(!res.ok) throw new Error('获取读数失败');
      apply(await res.json()); connection(true);
    } catch(e) { connection(false); }
  }
  refresh();
  if (typeof EventSource!=='undefined') {
    const stream=new EventSource('/api/live/stream');
    stream.addEventListener('live',e=>{try{apply(JSON.parse(e.data));connection(true);}catch(error){connection(false);}});
    stream.onopen=()=>connection(true); stream.onerror=()=>connection(false);
    window.addEventListener('beforeunload',()=>stream.close());
  }
  setInterval(refresh,5000);
  setInterval(renderFreshness,1000);
})();
