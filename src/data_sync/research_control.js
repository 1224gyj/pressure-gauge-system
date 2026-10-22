'use strict';
const fs=require('fs'),path=require('path'),{spawn}=require('child_process');
const {atomic}=require('./research_settings');
function readControl(root){try{return JSON.parse(fs.readFileSync(path.join(root,'data/research/control.json'),'utf8'));}catch(e){if(e.code==='ENOENT')return {paused:false,since:null};throw e;}}
function createResearchControl(root,cfg){
 root=path.resolve(root,cfg.researchControlRoot||'.');
 const file=path.join(root,'data/research/control.json'),pidFile=path.join(root,'data/research/capture.pid');
 function alive(){try{const pid=Number(fs.readFileSync(pidFile,'utf8'));if(!Number.isInteger(pid)||pid<1)return false;process.kill(pid,0);return true;}catch{return false;}}
 function snapshot(){return {...readControl(root),captureProcessRunning:alive()};}
 function start(){
  const current=readControl(root);
  if(alive()){atomic(file,{...current,paused:false,updatedAt:new Date().toISOString()});return snapshot();}
  const interpreter=fs.readFileSync(path.join(root,'config/python_path.txt'),'utf8').trim();
  if(!fs.existsSync(interpreter))throw Error('识别 Python 路径不存在');
  atomic(file,{...current,paused:false,updatedAt:new Date().toISOString()});
  const stop=path.join(root,'data/research/stop-capture.flag');if(fs.existsSync(stop))fs.unlinkSync(stop);
  const log=fs.openSync(path.join(root,'data/research/pipeline.log'),'a'),err=fs.openSync(path.join(root,'data/research/pipeline-error.log'),'a');
  try{const child=spawn(interpreter,['-u',path.join(root,'src/pipeline/流水线.py')],{cwd:root,windowsHide:true,detached:false,stdio:['ignore',log,err]});
   child.on('error',()=>{atomic(file,{...readControl(root),paused:true,error:'启动采集进程失败'});});
   if(child.pid)fs.writeFileSync(pidFile,String(child.pid));child.unref();
  }finally{fs.closeSync(log);fs.closeSync(err);}
  return snapshot();
 }
 function pause(){atomic(file,{...readControl(root),paused:true,updatedAt:new Date().toISOString()});return snapshot();}
 function clear(){
  const state=readControl(root),since=new Date().toISOString();
  const backup=path.join(root,'data/research/clear-backups',since.replace(/[:.]/g,'-'));fs.mkdirSync(backup,{recursive:true});
  const files=[cfg.readingsFile,cfg.comparisonsFile,cfg.deletionsFile||path.join(path.dirname(cfg.readingsFile),'deletions.json'),'data/transmitter/vendor-http.json','data/transmitter/readings.json','data/research/control.json'];
  for(const [index,name]of files.entries()){const source=path.resolve(root,name);if(fs.existsSync(source))fs.copyFileSync(source,path.join(backup,index+'-'+path.basename(name)));}
  atomic(path.join(backup,'manifest.json'),{clearedAt:since,previousControl:state,files});
  // Session boundary avoids races with an in-flight Python write or HTTP response.
  atomic(file,{...state,since,updatedAt:since,lastClearBackup:path.relative(root,backup)});
  return snapshot();
 }
 return {snapshot,start,pause,clear};
}
function isAfterBoundary(row,timeField,control){return !control.since||Date.parse(row[timeField])>Date.parse(control.since);}
module.exports={readControl,createResearchControl,isAfterBoundary};
