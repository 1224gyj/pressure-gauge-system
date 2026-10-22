'use strict';
(function(){
 let latest=null,paused=false,pending=null,choose=null,resume=null,device=null;
 const $=id=>document.getElementById(id),finite=Number.isFinite;
 const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const fmt=n=>finite(n)?n.toFixed(3):'—';
 const stamp=s=>s?new Date(s).toLocaleString('zh-CN',{hour12:false}):'—';
 function rows(){const start=Date.parse($('researchFrom')?.value),end=Date.parse($('researchTo')?.value);return (latest?.comparisonRows||[]).filter(r=>{const t=Date.parse(r.recognitionCapturedAt||r.transmitterRecordedAt);return (!finite(start)||t>=start)&&(!finite(end)||t<=end);});}
 function mount(){
  if($('researchPanel'))return;
  const panel=document.createElement('section');panel.id='researchPanel';panel.className='live-card research-panel';
  panel.innerHTML=`<div class="live-card-head"><div><h3>报文触发同步研究</h3><p>每条新报文触发一次识别，选取平台接收时间附近的历史画面；非设备采集时刻严格同步。</p></div><button id="researchPause" class="btn btn-ghost">冻结展示</button></div>
  <div class="research-controls"><button id="researchStartCapture" class="btn">启动采集</button><button id="researchPauseCapture" class="btn btn-ghost">暂停采集</button><button id="researchClearData" class="btn btn-danger-ghost">一键清除</button><span id="researchControlState" role="status"></span></div><p class="live-note" style="padding:0 20px">暂停本机视觉采集与变送器取数，设备仍按自身周期上报。清除当前研究记录、图表和统计，自动备份，保留照片和设备配置。</p><div class="research-controls"><label>变送器周期（秒）<input id="researchInterval" type="number" min="60" max="8640" step="1" value="60"></label><label>配对容差（秒）<input id="researchTolerance" type="number" min="0" max="30" step="1" value="10"></label><button id="researchSave" class="btn">应用设备周期与容差</button><button id="researchCheck" class="btn btn-ghost">检查设备同步</button><span id="researchDeviceStatus" role="status"></span></div>
  <div class="research-controls"><label>开始时间<input id="researchFrom" type="datetime-local" step="1"></label><label>结束时间<input id="researchTo" type="datetime-local" step="1"></label><button id="researchClear" class="btn btn-ghost">全部时间</button><button id="researchCsv" class="btn btn-ghost">导出 CSV</button><button id="researchXlsx" class="btn btn-ghost">导出 Excel</button></div><p id="researchPeriod" class="live-note"></p><div id="researchStats" class="research-stats"></div>
  <div class="research-grid"><section><h3>偏差趋势 · MPa</h3><div id="researchError"></div></section><section><h3>相关性散点 · MPa</h3><div id="researchScatter"></div></section></div>
  <details class="research-details"><summary>配对时间与照片（最近 100 条）</summary><div class="live-table-wrap"><table class="live-table"><thead><tr><th>视觉时间</th><th>平台接收时间</th><th>时间差 / 秒</th><th>识别质量</th><th>配对状态</th><th>照片与详情</th></tr></thead><tbody id="researchRows"></tbody></table></div></details>`;
  $('liveMonitor').querySelector('.live-overview').after(panel);
  for(const id of ['researchFrom','researchTo'])$(id).addEventListener('change',draw);
  $('researchClear').onclick=()=>{$('researchFrom').value='';$('researchTo').value='';draw();};
  $('researchPause').onclick=()=>{paused=!paused;$('researchPause').textContent=paused?'继续实时展示':'冻结展示';if(!paused&&pending){const next=pending;pending=null;resume(next);}};
  $('researchSave').onclick=save;$('researchCheck').onclick=check;
  $('researchCsv').onclick=()=>download(false);$('researchXlsx').onclick=()=>download(true);
  panel.addEventListener('click',event=>{const node=event.target.closest('[data-research-sequence]');if(node)choose(Number(node.dataset.researchSequence));});
  panel.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){const node=event.target.closest('[data-research-sequence]');if(node){event.preventDefault();choose(Number(node.dataset.researchSequence));}}});
  $('researchStartCapture').onclick=()=>command('start');$('researchPauseCapture').onclick=()=>command('pause');$('researchClearData').onclick=()=>command('clear');
  check();
 }
 async function command(action){
  const ids=['researchStartCapture','researchPauseCapture','researchClearData'];ids.forEach(id=>$(id).disabled=true);
  try{const res=await fetch('/api/live/research/control',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});const result=await res.json();if(!res.ok)throw Error(result.error);
   paused=false;pending=null;$('researchPause').textContent='冻结展示';if(action==='clear'){$('researchFrom').value='';$('researchTo').value='';}
   resume(result.snapshot);$('researchControlState').textContent=action==='clear'?'已清空本次研究，历史数据已备份':action==='pause'?'已暂停本机采集':'已启动采集，等待计划时刻';
  }catch(e){$('researchControlState').textContent=e.message;}finally{ids.forEach(id=>$(id).disabled=false);}
 }
 async function check(){try{const res=await fetch('/api/live/research/settings');const data=await res.json();if(!res.ok)throw Error(data.error);device=data.device;showDevice();}catch(e){$('researchDeviceStatus').textContent=e.message;}}
 function showDevice(){if(!$('researchDeviceStatus'))return;const d=device||latest?.research?.device||{};$('researchDeviceStatus').textContent=d.status==='synced'?'设备已同步 · '+d.actualUploadSeconds+' 秒/次':d.status==='pending'?'参数已提交，待设备同步':'设备周期未验证';}
 async function save(){const button=$('researchSave');button.disabled=true;try{
  const res=await fetch('/api/live/research/settings',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({intervalSeconds:Number($('researchInterval').value),toleranceSeconds:Number($('researchTolerance').value)})});const data=await res.json();if(!res.ok)throw Error(data.error);device=data.device;$('researchDeviceStatus').textContent='设置已提交；设备同步后生效。视觉识别继续由新报文触发。';
 }catch(e){$('researchDeviceStatus').textContent=e.message;}finally{button.disabled=false;}}
 function stats(selected){const valid=selected.filter(r=>finite(r.deltaKpa));const n=valid.length;const values=valid.map(r=>r.deltaKpa);const total=selected.filter(r=>r.recognitionId).length;const failed=selected.filter(r=>r.recognitionId&&(r.recognitionStatus!=='ok'||!finite(r.recognitionValueKpa)||r.recognitionValueKpa<0||r.recognitionValueKpa>latest.meter.rangeKpa)).length;
  const metrics=[['有效配对',String(n)],['未配对',String(selected.filter(r=>r.pairingStatus!=='paired').length)],['识别失败/需复核',total?(failed/total*100).toFixed(1)+'%':'—'],['平均偏差',n?fmt(values.reduce((a,b)=>a+b,0)/n):'—'],['平均绝对误差',n?fmt(values.reduce((a,b)=>a+Math.abs(b),0)/n):'—'],['RMSE',n?fmt(Math.sqrt(values.reduce((a,b)=>a+b*b,0)/n)):'—'],['最大绝对误差',n?fmt(Math.max(...values.map(Math.abs))):'—'],['平均配对时间差',n?fmt(valid.reduce((s,r)=>s+r.timeDiffSeconds,0)/n)+'秒':'—']];
  $('researchStats').innerHTML=metrics.map(([label,value])=>'<div><span>'+label+'</span><b>'+value+'</b></div>').join('');return valid;
 }
 function svg(target,selected,kind){
  const W=900,H=260,L=58,R=20,T=25,B=48,pw=W-L-R,ph=H-T-B;
  const valid=selected.filter(r=>finite(r.deltaKpa));const scatter=kind==='scatter',error=kind==='error';
  const times=selected.flatMap(r=>[Date.parse(r.recognitionCapturedAt),Date.parse(r.transmitterRecordedAt)]).filter(finite);
  let minX=scatter?0:Math.min(...times),maxX=scatter?latest.meter.rangeKpa:Math.max(...times);if(!finite(minX)){target.innerHTML='<p class="live-note">当前时间范围暂无数据</p>';return;}
  if(minX===maxX){minX-=30000;maxX+=30000;}
  const extent=Math.max(latest.meter.rangeKpa,...valid.flatMap(r=>[r.recognitionValueKpa,r.transmitterKpa]));if(scatter)maxX=extent;
  const alarm=latest.alarmKpa,warn=latest.warnKpa;
  const bound=Math.max(alarm*1.2,...valid.map(r=>Math.abs(r.deltaKpa)*1.1),.01);
  const minY=error?-bound:Math.min(0,...valid.map(r=>r.recognitionValueKpa)),maxY=error?bound:extent;
  const x=v=>L+(v-minX)/(maxX-minX||1)*pw,y=v=>T+(maxY-v)/(maxY-minY||1)*ph;
  let content='';for(let i=0;i<=4;i++){const v=minY+(maxY-minY)*i/4;content+=`<line x1="${L}" y1="${y(v)}" x2="${W-R}" y2="${y(v)}" stroke="#233951"/><text x="${L-8}" y="${y(v)+4}" fill="#9bb1c9" text-anchor="end" font-size="11">${v.toFixed(error?3:1)}</text>`;}
  if(error){for(const [value,color]of [[0,'#9bb1c9'],[warn,'#ffc56e'],[-warn,'#ffc56e'],[alarm,'#ff6b7a'],[-alarm,'#ff6b7a']])content+=`<line x1="${L}" y1="${y(value)}" x2="${W-R}" y2="${y(value)}" stroke="${color}" stroke-dasharray="5 4"><title>${value.toFixed(3)} MPa</title></line>`;}
  if(scatter)content+=`<line x1="${x(0)}" y1="${y(0)}" x2="${x(extent)}" y2="${y(extent)}" stroke="#ffc56e" stroke-dasharray="5 4"><title>y=x</title></line>`;
  function series(key,color){let segment=[];function flush(){if(segment.length>1)content+=`<polyline points="${segment.join(' ')}" fill="none" stroke="${color}" stroke-width="2"/>`;segment=[];}
   selected.forEach(r=>{let value=error?r.deltaKpa:r[key];const t=Date.parse(key==='transmitterKpa'?r.transmitterRecordedAt:(r.recognitionCapturedAt||r.transmitterRecordedAt));if(!finite(value)||!finite(t)){flush();return;}const xx=x(scatter?r.transmitterKpa:t),yy=y(value);if(!scatter)segment.push(xx+','+yy);content+=`<circle cx="${xx}" cy="${yy}" r="4" fill="${color}" tabindex="0" role="button" data-research-sequence="${r.sequence}" data-compare-sequence="${r.sequence}" data-compare-source="${key==='transmitterKpa'?'transmitter':'recognition'}" data-base-radius="4" aria-label="查看配对 ${r.sequence}"><title>${esc(stamp(r.recognitionCapturedAt||r.transmitterRecordedAt))} · ${value.toFixed(3)} MPa · 时间差 ${r.timeDiffSeconds??'—'} 秒</title></circle>`;});flush();}
  if(scatter)series('recognitionValueKpa','#4cc8ff');else if(error)series('deltaKpa','#4cc8ff');else{series('recognitionValueKpa','#4cc8ff');series('transmitterKpa','#10a58a');}
  for(let i=0;i<=4;i++){const v=minX+(maxX-minX)*i/4;const label=scatter?v.toFixed(1):new Date(v).toLocaleString('zh-CN',{month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});content+=`<text x="${x(v)}" y="${H-23}" text-anchor="${i===0?'start':i===4?'end':'middle'}" fill="#9bb1c9" font-size="11">${label}</text>`;}
  content+=`<text x="${W/2}" y="${H-4}" text-anchor="middle" fill="#9bb1c9" font-size="11">${scatter?'横轴：变送器 MPa · 纵轴：视觉 MPa · 虚线 y=x':'时间（本机时区）'}</text>`;
  target.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${scatter?'相关性散点':error?'偏差趋势':'按时间读数趋势'}">${content}</svg>`;
 }
 function draw(){if(!latest)return;const selected=rows();stats(selected);svg($('liveChart'),selected,'trend');svg($('researchError'),selected,'error');svg($('researchScatter'),selected.filter(r=>finite(r.deltaKpa)),'scatter');
  $('researchRows').innerHTML=selected.slice(-100).reverse().map(r=>`<tr><td>${esc(stamp(r.recognitionCapturedAt))}</td><td>${esc(stamp(r.transmitterRecordedAt))}</td><td>${fmt(r.timeDiffSeconds)}</td><td>${esc(r.recognitionStatus||'无识别')}</td><td>${esc(r.qualityNote||(finite(r.deltaKpa)?'有效配对':r.pairingStatus==='paired'?'需复核':'未配对'))}</td><td><button class="btn btn-sm btn-ghost" data-research-sequence="${r.sequence}">查看照片与读数</button></td></tr>`).join('')||'<tr><td colspan="6">当前范围暂无数据</td></tr>';
 }
 function download(excel){const selected=rows();const data=[['视觉时间','平台接收时间','视觉 MPa','变送器 MPa','偏差 MPa','引用误差 %FS','时间差 秒','识别质量','配对状态','照片接口','报文编号','触发结果','时间依据'],...selected.map(r=>[r.recognitionCapturedAt||'',r.transmitterRecordedAt||'',r.recognitionValueKpa??'',r.transmitterKpa??'',r.deltaKpa??'',r.percentFS??'',r.timeDiffSeconds??'',r.recognitionStatus||'',r.qualityNote||r.pairingStatus,r.recognitionId?location.origin+'/api/live/image?sampleId='+r.recognitionId:'',r.transmitterStamp||'',r.triggerStatus||'','厂家平台接收时间近似配对'])];
  const name='压力比对研究-'+new Date().toISOString().replace(/[:.]/g,'-');
  if(excel){if(typeof XLSX==='undefined'){alert('Excel 导出库尚未加载');return;}const book=XLSX.utils.book_new();XLSX.utils.book_append_sheet(book,XLSX.utils.aoa_to_sheet(data),'研究记录');XLSX.writeFile(book,name+'.xlsx');return;}
  const safe=v=>{let s=String(v);if(/^[=+@]/.test(s)||(/^-/.test(s)&&!Number.isFinite(Number(s))))s="'"+s;return '"'+s.replace(/"/g,'""')+'"';};const blob=new Blob(['\uFEFF'+data.map(row=>row.map(safe).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'});const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name+'.csv';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
 }
 window.CameraResearch={hold(data){if(paused){pending=data;return true;}return false;},render(data,select,onResume){latest=data;choose=select;resume=onResume;mount();$('researchControlState').textContent=data.researchControl?.paused?'已暂停本机采集':data.researchControl?.captureProcessRunning?'采集运行中':'采集未启动';if(document.activeElement!==$('researchInterval'))$('researchInterval').value=data.research?.intervalSeconds||60;if(document.activeElement!==$('researchTolerance'))$('researchTolerance').value=data.pairingToleranceSeconds??10;if(data.research?.device&&Date.parse(data.research.device.observedAt||0)>=Date.parse(device?.observedAt||0))device=data.research.device;showDevice();$('researchPeriod').textContent=data.recognitionMode==='transmitter'?('每条新报文识别一次 · '+data.frameCacheSeconds+' 秒历史画面缓存 · 选帧容差 ±'+data.pairingToleranceSeconds+' 秒 · 相同报文不重复识别 · 单位 MPa'):'每 '+(data.research?.intervalSeconds||60)+' 秒采集一组 · 一对一匹配容差 ±'+(data.pairingToleranceSeconds??10)+' 秒 · 单位 MPa · 图表和统计按所选时间范围计算';draw();},draw};
})();
