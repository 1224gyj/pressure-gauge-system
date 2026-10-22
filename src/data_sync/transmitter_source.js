'use strict';
const fs=require('fs');
const path=require('path');
const crypto=require('crypto');
const http=require('http');
const https=require('https');
const finite=n=>typeof n==='number'&&Number.isFinite(n);
const validTime=s=>typeof s==='string'&&Number.isFinite(Date.parse(s));
const {readControl}=require('./research_control');

function createTransmitterSource(root,configPath,meterId,outputUnit='kPa',controlRoot=root){
  // Cache and cloud protocol remain in kPa. Convert only at the output boundary.
  const outputRecord=r=>r?{...r,valueKpa:finite(r.valueKpa)&&outputUnit==='MPa'?r.valueKpa/1000:r.valueKpa,pressureUnit:outputUnit}:null;
  let cfg={},records=[],status='disabled',problem=null,lastSyncAt=null,request=null,timer=null,stopped=false;
  function read(file){return JSON.parse(fs.readFileSync(file,'utf8').replace(/^\uFEFF/,''));}
  function validate(rows){
    if(!Array.isArray(rows)||rows.length>1000)throw new Error('变送器数据格式不正确');
    const unique=new Map();
    for(const r of rows){
      if(!r||String(r.deviceId)!==String(cfg.deviceId)||r.meterId!==meterId||!/^\d{16}$/.test(r.vendorRecvAtUs)||!validTime(r.vendorReceivedAt)||!validTime(r.serverReceivedAt)||r.authVerified!==true||!(r.valueKpa===null||finite(r.valueKpa))||!finite(r.value))
        throw new Error('变送器设备、单位换算或时间字段不正确');
      if(Date.parse(r.vendorReceivedAt)!==Math.floor(Number(r.vendorRecvAtUs)/1000))throw new Error('变送器时间戳不一致');
      // 不将原始协议、密钥及服务器路径发往浏览器。
      unique.set(r.vendorRecvAtUs,{deviceId:r.deviceId,meterId:r.meterId,vendorRecvAtUs:r.vendorRecvAtUs,vendorReceivedAt:r.vendorReceivedAt,
        serverReceivedAt:r.serverReceivedAt,valueKpa:r.valueKpa,value:r.value,unitName:r.unitName||null,unitCode:r.unitCode,authVerified:true});
    }
    return [...unique.values()].sort((a,b)=>a.vendorRecvAtUs.localeCompare(b.vendorRecvAtUs));
  }
  try{
    if(!configPath||!fs.existsSync(configPath))return disabled();
    cfg=read(configPath);
    if(cfg.mode==='vendor-http')return require('./vendor_http_source').createVendorSource(root,cfg,meterId,outputUnit,controlRoot);
    if(!cfg.enabled)return disabled();
    if(!/^\d+$/.test(String(cfg.deviceId))||!Number.isSafeInteger(Number(cfg.deviceId)))throw new Error('请填写真实的变送器 devID');
    if(!finite(cfg.maxTimeDiffSeconds)||cfg.maxTimeDiffSeconds<0||!finite(cfg.staleAfterSeconds)||cfg.staleAfterSeconds<=0)throw new Error('请检查变送器匹配时差和过期时间配置');
    const base=new URL(cfg.serverUrl);
    if(!['http:','https:'].includes(base.protocol)||base.username||base.password)throw new Error('请填写有效的服务器 HTTP(S) 地址');
    const key=fs.readFileSync(path.resolve(root,cfg.readKeyFile),'utf8').replace(/^\uFEFF/,'').trim();
    if(!key||/[\r\n]/.test(key))throw new Error('只读密钥文件为空或格式不正确');
    const cache=path.resolve(root,cfg.cacheFile);
    status='waiting';
    if(fs.existsSync(cache)){
      try{const doc=read(cache);if(String(doc.deviceId)===String(cfg.deviceId)&&doc.meterId===meterId&&doc.serverUrl===base.origin)records=validate(doc.readings);}
      catch{problem='本机变送器缓存不可读，等待重新同步';}
    }
    const url=new URL('/api/transmitter/readings',base);
    url.searchParams.set('deviceId',String(cfg.deviceId));
    function save(rows){
      fs.mkdirSync(path.dirname(cache),{recursive:true});
      const temp=cache+'.'+crypto.randomUUID()+'.tmp';
      try{fs.writeFileSync(temp,JSON.stringify({deviceId:String(cfg.deviceId),meterId,serverUrl:base.origin,readings:rows},null,2));fs.renameSync(temp,cache);}
      finally{if(fs.existsSync(temp))fs.unlinkSync(temp);}
    }
    function sync(){
      if(stopped||request||readControl(controlRoot).paused)return;
      const transport=url.protocol==='https:'?https:http;
      const req=transport.get(url,{headers:{Authorization:'Bearer '+key},timeout:Math.max(500,Math.min(30000,Number(cfg.timeoutMs)||8000))},res=>{
        let size=0,chunks=[];
        res.on('data',chunk=>{size+=chunk.length;if(size>4*1024*1024)req.destroy(new Error('服务器响应过大'));else chunks.push(chunk);});
        res.on('error',()=>req.destroy(new Error('服务器响应中断')));
        res.on('end',()=>{
          if(stopped||readControl(controlRoot).paused||req.destroyed&&size>4*1024*1024)return;
          try{
            if(res.statusCode!==200)throw new Error('服务器返回 HTTP '+res.statusCode+(res.statusCode===401?'，请核对只读密钥':''));
            const doc=JSON.parse(Buffer.concat(chunks).toString('utf8'));
            if(String(doc.deviceId)!==String(cfg.deviceId)||doc.meterId!==meterId)throw new Error('服务器返回的设备映射不一致');
            const incoming=validate(doc.readings);
            const merged=validate([...new Map(records.concat(incoming).map(r=>[r.vendorRecvAtUs,r])).values()].sort((a,b)=>a.vendorRecvAtUs.localeCompare(b.vendorRecvAtUs)).slice(-1000));
            if(JSON.stringify(merged)!==JSON.stringify(records)||!fs.existsSync(cache))save(merged);
            records=merged;status='connected';problem=null;lastSyncAt=new Date().toISOString();
          }catch(e){status='error';problem=e.message;}
        });
      });
      request=req;
      req.on('timeout',()=>req.destroy(new Error('连接变送器服务器超时')));
      req.on('error',e=>{if(!stopped){status='error';problem=e.message.startsWith('服务器')?e.message:'无法连接变送器服务器，请检查地址、端口和网络';}});
      req.on('close',()=>{if(request===req)request=null;});
    }
    timer=setInterval(sync,Math.max(500,Number(cfg.pollIntervalMs)||2000));
    sync();
  }catch(e){status='error';problem=e.message.startsWith('请')?e.message:'变送器配置不完整，请检查地址和只读密钥文件';}
  function snapshot(){
    return {enabled:!!cfg.enabled,status,problem,deviceId:cfg.enabled?String(cfg.deviceId||''):null,lastSyncAt,
      readings:records.map(outputRecord),recordCount:records.length,latest:outputRecord(records[records.length-1]),staleAfterSeconds:cfg.staleAfterSeconds||60,
      maxTimeDiffSeconds:cfg.maxTimeDiffSeconds??15,timeBasis:'vendorReceivedAt',timeBasisLabel:'商家平台接收时间'};
  }
  function match(capturedAt){
    if(!cfg.enabled||!validTime(capturedAt))return null;
    let best=null,diff=Infinity;
    for(const r of records){
      if(!finite(r.valueKpa))continue;
      const delta=Math.abs(Date.parse(capturedAt)-Date.parse(r.vendorReceivedAt))/1000;
      if(delta<=cfg.maxTimeDiffSeconds&&delta<diff){best=r;diff=delta;}
    }
    return best?{...outputRecord(best),timeDiffSeconds:diff}:null;
  }
  function disabled(){return {snapshot:()=>({enabled:false,status:'disabled',problem:null,readings:[],recordCount:0,latest:null}),match:()=>null,stop(){}};}
  return {snapshot,match,stop(){stopped=true;clearInterval(timer);if(request)request.destroy();}};
}
module.exports={createTransmitterSource};
