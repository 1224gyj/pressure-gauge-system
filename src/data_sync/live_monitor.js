'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const {createTransmitterSource}=require('./transmitter_source');
const {buildSequenceComparison}=require('./sequence_comparison');
const {buildTimeComparison}=require('./time_comparison');
const {createResearchSettings}=require('./research_settings');
const {createResearchControl,isAfterBoundary}=require('./research_control');

function createLiveMonitor(projectRoot, configPath) {
  let cfg, readings = [], references = {}, capture = {state:'waiting',message:'等待启动摄像头采集'};
  let version = 1, signature = '', problem = null;
  const clients = new Set();
  cfg = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  const resolve = p => path.resolve(projectRoot, p);
  const jsonFile = (p, fallback) => fs.existsSync(p) ? JSON.parse(fs.readFileSync(p,'utf8')) : fallback;
  const finite = n => typeof n === 'number' && Number.isFinite(n);
  // Legacy *Kpa field names are retained for stored recognition/API compatibility.
  // With meter.unit=MPa, recognition numbers are reinterpreted without rescaling.
  const transmitter=createTransmitterSource(projectRoot,cfg.transmitterConfigFile?resolve(cfg.transmitterConfigFile):null,cfg.meter.id,cfg.meter.unit||'kPa',resolve(cfg.researchControlRoot||'.'));
  const research=createResearchSettings(projectRoot,transmitter,cfg);
  const control=createResearchControl(projectRoot,cfg);
  function sourceSnapshot(){const source=transmitter.snapshot(),session=control.snapshot();const rows=(source.readings||[]).filter(r=>isAfterBoundary(r,'vendorReceivedAt',session));return {...source,readings:rows,latest:rows.at(-1)||null,recordCount:rows.length};}

  const comparisonFor=source=>cfg.comparisonMode==='sequence'?buildSequenceComparison(readings,source.enabled?source.readings:[],cfg):buildTimeComparison(readings,source.enabled?source.readings:[],cfg);
  const deletionFile=cfg.deletionsFile ? resolve(cfg.deletionsFile) : path.join(path.dirname(resolve(cfg.readingsFile)),'deletions.json');
  let deletionLog={batches:[]};
  function readDeletions() {
    const doc=jsonFile(deletionFile,{batches:[]});
    if(!doc || !Array.isArray(doc.batches) || doc.batches.some(b=>!b || !/^[0-9a-f]{32}$/.test(b.id) || !Array.isArray(b.samples) || b.samples.some(s=>!s || !/^[0-9a-f]{32}$/.test(s.id) || s.meterId!==cfg.meter.id)))
      throw new Error('删除记录格式不正确，未更改数据');
    return doc;
  }
  function saveDeletions(doc) {
    fs.mkdirSync(path.dirname(deletionFile),{recursive:true});
    const temp=deletionFile+'.'+crypto.randomUUID()+'.tmp';
    try {fs.writeFileSync(temp,JSON.stringify(doc,null,2),'utf8');fs.renameSync(temp,deletionFile);}
    finally {if(fs.existsSync(temp)) fs.unlinkSync(temp);}
  }

  function point(raw,index=readings.findIndex(r=>r.id===raw.id),sequenceRow=null) {
    const source=sourceSnapshot();
    if(cfg.comparisonMode==='sequence'||(cfg.comparisonMode==='time'&&source.enabled)){
      const row=sequenceRow||comparisonFor(source).rows.find(r=>r.recognitionId===raw.id);
      if(row)return {...raw,sequence:row.sequence,transmitterKpa:row.transmitterKpa,transmitterRecordedAt:row.transmitterRecordedAt,
        transmitterSource:row.transmitterPresent?'api':null,transmitterTimeDiffSeconds:row.timeDiffSeconds??null,comparisonMode:cfg.comparisonMode,
        deltaKpa:row.deltaKpa,percentFS:row.percentFS,state:row.state,pairingStatus:row.pairingStatus};
    }
    const match=transmitter.match(raw.capturedAt);
    const manual = source.enabled?null:references[raw.id];
    const std = match?match.valueKpa:manual && finite(manual.valueKpa) ? manual.valueKpa : null;
    const delta = finite(raw.valueKpa) && std !== null ? +(raw.valueKpa - std).toFixed(10) : null;
    const pct = delta !== null ? +(Math.abs(delta) / cfg.meter.rangeKpa * 100).toFixed(10) : null;
    let state = 'pending';
    if (!finite(raw.valueKpa)) state = 'failed';
    else if (raw.status !== 'ok' || raw.valueKpa < 0 || raw.valueKpa > cfg.meter.rangeKpa) state = 'review';
    else if (pct !== null) state = pct+1e-9 >= cfg.meter.accuracyClass*cfg.alarmCoef ? 'alarm'
      : pct+1e-9 >= cfg.meter.accuracyClass*cfg.warnCoef ? 'warning' : 'normal';
    return {...raw,transmitterKpa:std,transmitterRecordedAt:match?match.vendorReceivedAt:manual ? manual.recordedAt : null,
      transmitterSource:match?'api':manual?'manual':null,transmitterTimeDiffSeconds:match?match.timeDiffSeconds:null,
      deltaKpa:delta,percentFS:pct,state};
  }
  function snapshot() {
    const source=sourceSnapshot();
    const comparison=(cfg.comparisonMode==='sequence'||source.enabled)?comparisonFor(source):null;
    const points = readings.map((raw,index)=>point(raw,index,comparison?.rows.find(r=>r.recognitionId===raw.id)));
    const lastDeletion=deletionLog.batches.slice().reverse().find(b=>!b.restoredAt&&isAfterBoundary(b,'deletedAt',control.snapshot()));
    return {mode:'single',version,recognitionMode:cfg.recognitionMode||'scheduled',frameCacheSeconds:cfg.frameCacheSeconds||120,researchControl:control.snapshot(),research:research.snapshot(),pairingToleranceSeconds:cfg.pairingToleranceSeconds??10,timeBasisLabel:'按厂家平台接收时间近似配对，非设备采集时间',meter:cfg.meter,readings:points,transmitter:source,
      comparisonMode:cfg.comparisonMode==='sequence'?'sequence':'time',comparisonRows:comparison?.rows||[],
      latestComparison:comparison?.latest||null,pairedCount:comparison?.pairedCount||0,
      deletionSupported:true,lastDeletion:lastDeletion?{id:lastDeletion.id,count:lastDeletion.samples.length,deletedAt:lastDeletion.deletedAt}:null,
      latest:points.length ? points[points.length-1] : null,captureStatus:control.snapshot().paused?{...capture,state:'paused',lastError:capture.state==='error'?capture.message:null,message:'本机研究采集已暂停，设备仍按自身周期上报'}:capture,problem,
      serverTime:new Date().toISOString(),staleAfterSeconds:cfg.staleAfterSeconds,
      warnKpa:cfg.meter.rangeKpa*cfg.meter.accuracyClass*cfg.warnCoef/100,
      alarmKpa:cfg.meter.rangeKpa*cfg.meter.accuracyClass*cfg.alarmCoef/100};
  }
  function broadcast() {
    const message = 'event: live\ndata: '+JSON.stringify(snapshot())+'\n\n';
    clients.forEach(res => { try { res.write(message); } catch (e) { clients.delete(res); } });
  }
  function poll() {
    try {
      const doc = jsonFile(resolve(cfg.readingsFile), {readings:[]});
      const nextCapture = jsonFile(resolve(cfg.statusFile), {state:'waiting',message:'等待启动摄像头采集'});
      const nextReferences = jsonFile(resolve(cfg.comparisonsFile), {});
      const nextDeletions=readDeletions();
      if (!Array.isArray(doc.readings) || !nextReferences || typeof nextReferences !== 'object' || Array.isArray(nextReferences))
        throw new Error('实时数据格式不正确');
      const deleted=new Set(nextDeletions.batches.filter(b=>!b.restoredAt).flatMap(b=>b.samples.map(s=>s.id)));
      const restored=nextDeletions.batches.filter(b=>b.restoredAt).flatMap(b=>b.samples);
      // 独立删除日志不改采集文件，摄像头并发追加不会覆盖删除操作；撤销也能从备份恢复。
      const merged=new Map(restored.concat(doc.readings).map(r=>[r && r.id,r]));
      const nextReadings = [...merged.values()].filter(r => r && typeof r.id === 'string' && /^[0-9a-f]{32}$/.test(r.id)
        && r.meterId === cfg.meter.id && !isNaN(Date.parse(r.capturedAt))
        && isAfterBoundary(r,'capturedAt',control.snapshot()) && !deleted.has(r.id) && (r.valueKpa === null || finite(r.valueKpa)))
        .sort((a,b)=>Date.parse(a.capturedAt)-Date.parse(b.capturedAt)).slice(-cfg.historyLimit);
      const nextSignature = JSON.stringify([nextReadings,nextCapture,nextReferences,nextDeletions,sourceSnapshot(),control.snapshot()]);
      if (nextSignature === signature && problem === null) return;
      signature = nextSignature;
      readings = nextReadings; capture = nextCapture; references = nextReferences; deletionLog=nextDeletions; problem = null;
      version++; broadcast();
    } catch (e) {
      if (problem !== e.message) { problem = e.message; version++; broadcast(); }
    }
  }
  function reply(res, code, data) {
    res.writeHead(code, {'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});
    res.end(JSON.stringify(data));
  }
  function handle(req, res, url) {
    if (!url.pathname.startsWith('/api/live')) return false;
    if (url.pathname==='/api/live/research/control' && req.method==='POST') {
      let body='';req.on('data',d=>{body+=d;if(body.length>1000)req.destroy();});
      req.on('end',()=>{try{const input=JSON.parse(body);if(!['start','pause','clear'].includes(input.action))return reply(res,400,{error:'未知采集操作'});const result=control[input.action]();poll();version++;broadcast();reply(res,200,{ok:true,control:result,snapshot:snapshot()});}catch(e){reply(res,400,{error:e.message});}});return true;
    }
    if (url.pathname==='/api/live/research/settings' && req.method==='GET') {
      research.read().then(data=>reply(res,200,data)).catch(e=>reply(res,503,{error:e.message}));return true;
    }
    if (url.pathname==='/api/live/research/settings' && req.method==='POST') {
      let body='';req.on('data',d=>{body+=d;if(body.length>2000)req.destroy();});
      req.on('end',async()=>{try{const input=JSON.parse(body);const result=await research.apply(input.intervalSeconds,input.toleranceSeconds);version++;broadcast();reply(res,200,result);}catch(e){reply(res,400,{error:e.message});}});return true;
    }
    if (url.pathname === '/api/live' && req.method === 'GET') {
      poll(); reply(res,200,snapshot()); return true;
    }
    if (url.pathname === '/api/live/stream' && req.method === 'GET') {
      res.writeHead(200,{'Content-Type':'text/event-stream','Cache-Control':'no-cache','Connection':'keep-alive'});
      res.write('retry: 1500\n\nevent: live\ndata: '+JSON.stringify(snapshot())+'\n\n');
      clients.add(res);
      res.on('error',()=>clients.delete(res));
      const ping = setInterval(() => res.write(': ping\n\n'),15000);
      req.on('close',() => { clearInterval(ping); clients.delete(res); });
      return true;
    }
    if (['/api/live/delete','/api/live/restore'].includes(url.pathname) && req.method==='POST') {
      let body='';
      req.on('data',d=>{body+=d;if(body.length>100000) req.destroy();});
      req.on('end',()=>{
        try {
          const input=JSON.parse(body);
          poll();
          if(problem) return reply(res,409,{error:'实时记录存在异常，暂不能更改：'+problem});
          const saved=readDeletions();
          if(url.pathname==='/api/live/delete') {
            if(!Array.isArray(input.sampleIds) || !input.sampleIds.length || input.sampleIds.length>1000 || input.sampleIds.some(id=>typeof id!=='string'||!/^[0-9a-f]{32}$/.test(id)))
              return reply(res,400,{error:'请选择需要删除的有效记录'});
            const ids=new Set(input.sampleIds),samples=readings.filter(r=>ids.has(r.id));
            if(samples.length!==ids.size) return reply(res,409,{error:'部分记录已删除或不再存在，请刷新后重试'});
            const batch={id:crypto.randomBytes(16).toString('hex'),deletedAt:new Date().toISOString(),samples,
              references:Object.fromEntries(samples.filter(s=>references[s.id]).map(s=>[s.id,references[s.id]]))};
            saved.batches.push(batch);
            saveDeletions(saved);poll();
            reply(res,200,{ok:true,count:samples.length,snapshot:snapshot()});
          } else {
            const batch=saved.batches.find(b=>b.id===input.batchId && !b.restoredAt);
            if(!batch) return reply(res,409,{error:'这批记录已经恢复或不存在'});
            batch.restoredAt=new Date().toISOString();
            saveDeletions(saved);poll();
            reply(res,200,{ok:true,count:batch.samples.length,snapshot:snapshot()});
          }
        } catch(e) {reply(res,400,{error:e.message});}
      });
      return true;
    }
    if (url.pathname === '/api/live/image' && req.method === 'GET') {
      const sample = readings.find(r => r.id === url.searchParams.get('sampleId'));
      const image = sample && sample.image ? resolve(sample.image) : null;
      const base = resolve('data')+path.sep;
      if (!image || !image.startsWith(base) || !/\.(jpe?g|png)$/i.test(image)) {
        reply(res,404,{error:'没有可显示的抓拍照片'}); return true;
      }
      fs.readFile(image,(err,bytes) => {
        if (err) return reply(res,404,{error:'抓拍照片已不存在'});
        res.writeHead(200,{'Content-Type':/\.png$/i.test(image)?'image/png':'image/jpeg','Cache-Control':'no-store'});
        res.end(bytes);
      });
      return true;
    }
    if (url.pathname === '/api/live/transmitter' && req.method === 'POST') {
      let body = '';
      req.on('data',d => { body += d; if (body.length > 10000) req.destroy(); });
      req.on('end',() => {
        try {
          const input = JSON.parse(body);
          poll();
          if (!readings.some(r => r.id === input.sampleId)) return reply(res,409,{error:'对应抓拍记录已不存在，请刷新后重新填写'});
          if (!finite(input.valueKpa) || input.valueKpa < 0 || input.valueKpa > cfg.meter.rangeKpa)
            return reply(res,400,{error:'请输入 0～'+cfg.meter.rangeKpa+' MPa 的变送器读数'});
          const file = resolve(cfg.comparisonsFile);
          // 从磁盘重新读取，损坏文件不能被一次输入覆盖。
          const saved = jsonFile(file, {});
          if (!saved || typeof saved !== 'object' || Array.isArray(saved)) throw new Error('变送器记录格式不正确');
          saved[input.sampleId] = {valueKpa:input.valueKpa,recordedAt:new Date().toISOString()};
          fs.mkdirSync(path.dirname(file),{recursive:true});
          const temp = file+'.'+crypto.randomUUID()+'.tmp';
          try { fs.writeFileSync(temp,JSON.stringify(saved,null,2),'utf8'); fs.renameSync(temp,file); }
          finally { if (fs.existsSync(temp)) fs.unlinkSync(temp); }
          poll(); reply(res,200,{ok:true,sample:point(readings.find(r=>r.id===input.sampleId))});
        } catch (e) { reply(res,400,{error:e.message}); }
      });
      return true;
    }
    reply(res,404,{error:'接口不存在'}); return true;
  }
  poll();
  const timer = setInterval(poll,Math.max(100,cfg.watchIntervalMs));
  let lastDeviceCheck=0;
  const deviceTimer=setInterval(()=>{
    if(control.snapshot().paused||!transmitter.parameters||Date.now()-lastDeviceCheck<60000)return;
    research.read().then(()=>{lastDeviceCheck=Date.now();version++;broadcast();}).catch(()=>{});
  },5000);
  return {handle,snapshot,stop() { clearInterval(timer); clearInterval(deviceTimer); transmitter.stop(); clients.forEach(r=>r.end()); }};
}
module.exports = {createLiveMonitor};
