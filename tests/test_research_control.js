'use strict';
const fs=require('fs'),path=require('path'),os=require('os'),http=require('http'),assert=require('assert/strict');
const {createLiveMonitor}=require('../src/data_sync/live_monitor');
const {createResearchControl}=require('../src/data_sync/research_control');
async function main(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'research-control-'));let monitor,server;
 try{
  fs.mkdirSync(path.join(root,'config'),{recursive:true});fs.mkdirSync(path.join(root,'data/live'),{recursive:true});
  const cfg={meter:{id:'YB-106',rangeKpa:16,unit:'MPa',accuracyClass:2.5},readingsFile:'data/live/readings.json',statusFile:'data/live/status.json',comparisonsFile:'data/live/comparisons.json',historyLimit:1000,watchIntervalMs:100,staleAfterSeconds:150,warnCoef:.5,alarmCoef:1,comparisonMode:'time'};
  const file=path.join(root,'config/live.json');fs.writeFileSync(file,JSON.stringify(cfg));
  const sample={id:'a'.repeat(32),meterId:'YB-106',capturedAt:new Date(Date.now()-60000).toISOString(),valueKpa:8.5,status:'ok',origin:'camera'};
  const dataFile=path.join(root,cfg.readingsFile);fs.writeFileSync(dataFile,JSON.stringify({readings:[sample]}));
  monitor=createLiveMonitor(root,file);server=http.createServer((req,res)=>monitor.handle(req,res,new URL(req.url,'http://localhost')));await new Promise(r=>server.listen(0,'127.0.0.1',r));
  const base='http://127.0.0.1:'+server.address().port;
  const post=async action=>{const res=await fetch(base+'/api/live/research/control',{method:'POST',body:JSON.stringify({action})});return {status:res.status,data:await res.json()};};
  assert.equal((await post('bad')).status,400);
  assert.equal((await post('pause')).data.snapshot.captureStatus.state,'paused');
  fs.writeFileSync(path.join(root,'data/research/capture.pid'),String(process.pid));
  const start=await post('start');assert.equal(start.status,200);assert.equal(start.data.control.paused,false);assert.equal(start.data.control.captureProcessRunning,true);
  await post('pause');const cleared=await post('clear');assert.equal(cleared.status,200);assert.equal(cleared.data.snapshot.readings.length,0);assert.equal(cleared.data.control.paused,true);
  const backup=path.join(root,cleared.data.control.lastClearBackup);assert.equal(JSON.parse(fs.readFileSync(path.join(backup,'0-readings.json'))).readings.length,1);
  fs.writeFileSync(dataFile,JSON.stringify({readings:[sample]}));assert.equal((await (await fetch(base+'/api/live')).json()).readings.length,0,'in-flight old writes do not resurrect cleared data');
  const fresh={...sample,id:'b'.repeat(32),capturedAt:new Date(Date.now()+1000).toISOString()};fs.writeFileSync(dataFile,JSON.stringify({readings:[sample,fresh]}));assert.equal((await (await fetch(base+'/api/live')).json()).readings.length,1);
  monitor.stop();monitor=createLiveMonitor(root,file);assert.equal(monitor.snapshot().readings.length,1,'clear boundary persists after restart');assert.equal(monitor.snapshot().researchControl.paused,true);
  console.log('PASS: real HTTP pause/start/clear, idempotent resume, backup, concurrency boundary and restart persistence');
 }finally{monitor?.stop();if(server)await new Promise(r=>server.close(r));fs.rmSync(root,{recursive:true,force:true});}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
