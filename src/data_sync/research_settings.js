'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto');
function atomic(file,data){fs.mkdirSync(path.dirname(file),{recursive:true});const tmp=file+'.'+crypto.randomUUID()+'.tmp';try{fs.writeFileSync(tmp,JSON.stringify(data,null,2));fs.renameSync(tmp,file);}finally{if(fs.existsSync(tmp))fs.unlinkSync(tmp);}}
function createResearchSettings(root,source,cfg){
 const file=path.join(root,'data/research/device-settings.json');let busy=false;
 function snapshot(){let saved={};try{saved=JSON.parse(fs.readFileSync(file,'utf8'));}catch{}return {intervalSeconds:cfg.researchIntervalSeconds||60,toleranceSeconds:cfg.pairingToleranceSeconds??10,device:{status:saved.status||'unverified',requestedAt:saved.requestedAt,observedAt:saved.observedAt,actualUploadSeconds:saved.actualUploadSeconds,current:saved.current?{collectInterval:saved.current.collectInterval,upInterval:saved.current.upInterval,upCT:saved.current.upCT,unit:saved.current.unit,dot:saved.current.dot,syncAt:saved.current.syncAt,setAt:saved.current.setAt}:null}};}
 async function read(){const current=await source.parameters();const saved=snapshot().device;
  const seconds=Number(current.collectInterval)/10*Number(current.upInterval);
  // Vendor GET may serialize setAt using inconsistent timezone offsets.
  // Prefer our UTC submission time, then verify an actual hardware sync after it.
  const synced=!!current.syncAt&&Date.parse(current.syncAt)>=Date.parse(saved.requestedAt||current.setAt);
  const effective=seconds===cfg.researchIntervalSeconds&&current.upInterval===1&&current.upCT===0&&synced;
  const device={...saved,current,observedAt:new Date().toISOString(),status:effective?'synced':'pending',actualUploadSeconds:seconds};atomic(file,device);return snapshot();
 }
 async function apply(interval,tolerance){
  if(busy)throw Error('正在设置设备参数，请稍候');
  if(!Number.isInteger(interval)||interval<60||interval>8640)throw Error('采集周期必须是 60～8640 秒的整数');
  if(!Number.isFinite(tolerance)||tolerance<0||tolerance>Math.min(30,interval/2-1))throw Error('匹配容差需在 0～30 秒内且小于半个采集周期');
  busy=true;try{
   const current=await source.parameters();const backup=path.join(root,'data/settings-backups',Date.now()+'-device.json');atomic(backup,current);
   const names=['blTime','bsh','bsl','cat1Standby','displayTime','dot','offset','outH','outL','outOffDelay','outOnDelay','outPA','spn','unit','zero'];
   const body=Object.fromEntries(names.filter(k=>current[k]!==undefined).map(k=>[k,current[k]]));
   // Vendor uses dot in GET, but dp in PUT.
   body.dp=current.dp??current.dot;
   body.dot=current.dot??current.dp;
   const transmitter=JSON.parse(fs.readFileSync(path.join(root,'config/transmitter.json'),'utf8'));
   Object.assign(body,{devIDs:[Number(transmitter.deviceId)],projID:Number(transmitter.projectId),collectInterval:interval*10,upInterval:1,upCT:0});
   await source.parameters(body);
   atomic(file,{status:'pending',requestedAt:new Date().toISOString(),requested:{collectInterval:interval*10,upInterval:1,upCT:0},before:current,backup});
   const pipelineFile=path.join(root,'config/pipeline.json');const pipeline=JSON.parse(fs.readFileSync(pipelineFile,'utf8'));pipeline.schedule={...pipeline.schedule,every:String(interval),at:'',between:'',count:0,first_shot_now:false,align_to_clock:true,follow_vendor_phase:true};atomic(pipelineFile,pipeline);
   Object.assign(cfg,{comparisonMode:'time',researchIntervalSeconds:interval,pairingToleranceSeconds:tolerance,staleAfterSeconds:interval*2+30});
   atomic(path.join(root,'config/live.json'),cfg);
   return snapshot();
  }finally{busy=false;}
 }
 return {snapshot,read,apply};
}
module.exports={createResearchSettings,atomic};
