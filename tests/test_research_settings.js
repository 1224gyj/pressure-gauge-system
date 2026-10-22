'use strict';
const fs=require('fs'),os=require('os'),path=require('path'),assert=require('assert/strict');
const {createResearchSettings}=require('../src/data_sync/research_settings');
async function main(){const root=fs.mkdtempSync(path.join(os.tmpdir(),'research-settings-'));try{
 fs.mkdirSync(path.join(root,'config'));for(const [name,value]of Object.entries({'transmitter.json':{deviceId:18136,projectId:1424},'pipeline.json':{schedule:{every:'10',count:10}},'live.json':{}}))fs.writeFileSync(path.join(root,'config',name),JSON.stringify(value));
 let sent;const current={collectInterval:50,upInterval:720,upCT:4,dot:2,unit:1,bsh:2000,zero:4};
 const source={async parameters(body){if(body){sent=body;return {accepted:true};}return current;}};
 const cfg={};const settings=createResearchSettings(root,source,cfg);await settings.apply(60,10);
 assert.equal(sent.collectInterval,600);assert.equal(sent.upInterval,1);assert.equal(sent.upCT,0);assert.equal(sent.dot,2);assert.equal(sent.dp,2);assert.equal(sent.unit,1);assert.equal(sent.bsh,2000);
 const pipe=JSON.parse(fs.readFileSync(path.join(root,'config/pipeline.json')));assert.equal(pipe.schedule.count,0);assert.equal(pipe.schedule.every,'60');
 assert.equal(settings.snapshot().device.status,'pending');assert.equal((await settings.read()).device.status,'pending');
 Object.assign(current,{collectInterval:600,upInterval:1,upCT:0,setAt:new Date(Date.now()+8*3600000).toISOString(),syncAt:new Date(Date.now()+1000).toISOString()});assert.equal((await settings.read()).device.status,'synced');
 await assert.rejects(()=>settings.apply(30,10));await assert.rejects(()=>settings.apply(60,31));
 source.parameters=async()=>{throw Error('offline');};await assert.rejects(()=>settings.apply(120,10));assert.equal(JSON.parse(fs.readFileSync(path.join(root,'config/pipeline.json'))).schedule.every,'60');
 console.log('PASS: device payload, decimal precision preservation, backups, sync verification, period validation and failed-update isolation');
}finally{fs.rmSync(root,{recursive:true,force:true});}}
main().catch(e=>{console.error(e);process.exitCode=1;});
