'use strict';
const fs=require('fs'),path=require('path'),crypto=require('crypto'),http=require('http'),https=require('https');
const units={1:['MPa',1000],2:['kPa',1],3:['Pa',.001],4:['bar',100],5:['mbar',.1]};
const {readControl}=require('./research_control');
function normalize(r,deviceId,meterId,outputUnit){
  if(String(r.devID)!==String(deviceId))return null;
  const stamp=String(r.recvAt),dot=r.dot;
  if(!/^\d{16}$/.test(stamp)||!Number.isSafeInteger(r.recvAt)||!Number.isFinite(r.value)||!Number.isInteger(dot)||dot<0||dot>9)throw Error('厂家数据数值或时间格式不正确');
  const unit=units[r.unit],value=r.value/10**dot;
  return {deviceId:String(deviceId),meterId,vendorRecvAtUs:stamp,vendorReceivedAt:new Date(Number(BigInt(stamp)/1000n)).toISOString(),serverReceivedAt:new Date().toISOString(),value,valueKpa:unit?value*unit[1]/(outputUnit==='MPa'?1000:1):null,unitCode:r.unit,unitName:unit?.[0]||null,pressureUnit:outputUnit,authVerified:true,timeout:r.timeout===true};
}
function createVendorSource(root,cfg,meterId,outputUnit,controlRoot=root){
  let records=[],status='waiting',problem=null,lastSyncAt=null,token='',cursor='0',busy=false,stopped=false,timer,active;
  const cache=path.resolve(root,cfg.vendorCacheFile||'data/transmitter/vendor-http.json');
  const base=new URL(cfg.vendorBaseUrl||'https://iot.wffdck.cn:1010');
  function save(){
    fs.mkdirSync(path.dirname(cache),{recursive:true});const tmp=cache+'.'+crypto.randomUUID()+'.tmp';
    try{fs.writeFileSync(tmp,JSON.stringify({identity:identity(),cursor,records}));fs.renameSync(tmp,cache);}finally{if(fs.existsSync(tmp))fs.unlinkSync(tmp);}
  }
  function identity(){return [base.origin,String(cfg.projectId),String(cfg.deviceId),meterId,outputUnit].join('|');}
  function request(url,body,method){return new Promise((resolve,reject)=>{
    const payload=body?JSON.stringify(body):null;
    const req=(url.protocol==='https:'?https:http).request(url,{method:method||(payload?'POST':'GET'),headers:{...(token?{token}:{}),...(payload?{'Content-Type':'application/json','Content-Length':Buffer.byteLength(payload)}:{})},timeout:10000},res=>{
      let size=0,chunks=[];res.on('data',d=>{size+=d.length;if(size>4*1024*1024)req.destroy(Error('厂家响应过大'));else chunks.push(d);});res.on('error',reject);
      res.on('end',()=>{try{if(res.statusCode===401||res.statusCode===403){token='';throw Error('厂家登录已失效，将重新登录');}if(res.statusCode!==200)throw Error('厂家接口 HTTP '+res.statusCode);const data=JSON.parse(Buffer.concat(chunks).toString('utf8'));if(res.headers.token)token=String(res.headers.token);resolve(data);}catch(e){reject(e);}});
    });active=req;req.on('error',reject);req.on('timeout',()=>req.destroy(Error('厂家接口连接超时')));if(payload)req.write(payload);req.end();
  });}
  async function sync(){
    if(stopped||busy||readControl(controlRoot).paused)return;busy=true;
    try{
      if(!token){
        const credentials=JSON.parse(fs.readFileSync(path.resolve(root,cfg.credentialsFile||'config/vendor-credentials.local.json'),'utf8').replace(/^\uFEFF/,''));
        const account=String(credentials.account||'');if(!account||!credentials.password)throw Error('请填写厂家查询账号和密码');
        const key=Math.floor(Date.now()/1000);const password=crypto.createHash('md5').update(String(credentials.password)+key+account.slice(-4)+'XRTech').digest('hex');
        const login=await request(new URL('/viplogin',base),{phone:account,key,password});
        if(login.code!==undefined&&login.code!==0){token='';throw Error('厂家登录失败，请核对查询账号');}if(!token)throw Error('厂家登录没有返回 token');
      }
      if(stopped||readControl(controlRoot).paused)return;
      const url=new URL('/projdata',base);url.searchParams.set('pid',String(cfg.projectId));url.searchParams.set('afterTime',cursor);
      const doc=await request(url);
      if(stopped||readControl(controlRoot).paused)return;
      if(doc.code!==0||!Array.isArray(doc.data)){token='';throw Error('厂家接口返回异常，将重新验证登录');}
      let next=BigInt(cursor);const incoming=[];
      for(const raw of doc.data){if(!Number.isSafeInteger(raw.recvAt)||raw.recvAt<0)throw Error('厂家时间戳无效');const row=normalize(raw,cfg.deviceId,meterId,outputUnit);if(row)incoming.push(row);if(BigInt(raw.recvAt)>next)next=BigInt(raw.recvAt);}
      const merged=new Map(records.map(r=>[r.vendorRecvAtUs,r]));for(const r of incoming)if(!merged.has(r.vendorRecvAtUs))merged.set(r.vendorRecvAtUs,r);
      const previous=records,previousCursor=cursor;
      records=[...merged.values()].sort((a,b)=>a.vendorRecvAtUs.localeCompare(b.vendorRecvAtUs)).slice(-1000);cursor=String(next);
      try{save();}catch(e){records=previous;cursor=previousCursor;throw Error('厂家数据本地保存失败');}
      status='connected';problem=null;lastSyncAt=new Date().toISOString();
    }catch(e){if(!stopped){status='error';problem=e.code?'厂家连接或本地配置读取失败':e.message;}}finally{busy=false;}
  }
  try{
    if(!cfg.enabled)return {snapshot:()=>({enabled:false,status:'disabled',readings:[],latest:null}),match:()=>null,stop(){}};
    if(!/^\d+$/.test(String(cfg.projectId))||!/^\d+$/.test(String(cfg.deviceId)))throw Error('请配置厂家项目 ID 和设备编号');
    if(base.protocol!=='https:'&&base.hostname!=='127.0.0.1')throw Error('厂家接口必须使用 HTTPS');
    if(fs.existsSync(cache)){const doc=JSON.parse(fs.readFileSync(cache,'utf8'));if(doc.identity===identity()&&/^\d+$/.test(doc.cursor)&&Array.isArray(doc.records)){cursor=doc.cursor;records=doc.records;}}
    timer=setInterval(sync,Math.max(16000,Number(cfg.vendorPollIntervalMs)||20000));sync();
  }catch(e){status='error';problem=e.message;}
  async function parameters(body){
    if(stopped||busy||!token)throw Error('厂家连接尚未就绪，请稍后重试');
    busy=true;
    try{
      const url=new URL('/dpara/user',base);
      if(!body){url.searchParams.set('did',String(cfg.deviceId));url.searchParams.set('pid',String(cfg.projectId));}
      const result=await request(url,body,body?'PUT':'GET');
      if(result.code!==0||(!body&&!result.data))throw Error('厂家参数接口返回失败，请确认账号具备设备参数权限');
      return result.data||{accepted:true};
    }finally{busy=false;}
  }
  return {parameters,snapshot:()=>({enabled:!!cfg.enabled,source:'vendor-http',status,problem,deviceId:String(cfg.deviceId),lastSyncAt,readings:records,latest:records.at(-1)||null,recordCount:records.length,staleAfterSeconds:cfg.staleAfterSeconds||60,maxTimeDiffSeconds:cfg.maxTimeDiffSeconds||15,timeBasis:'vendorReceivedAt',timeBasisLabel:'厂家平台接收时间'}),match(at){let best=null,diff=Infinity;for(const r of records){const d=Math.abs(Date.parse(at)-Date.parse(r.vendorReceivedAt))/1000;if(r.valueKpa!==null&&d<diff&&d<=(cfg.maxTimeDiffSeconds||15)){best=r;diff=d;}}return best?{...best,timeDiffSeconds:diff}:null;},stop(){stopped=true;clearInterval(timer);active?.destroy();}};
}
module.exports={createVendorSource,normalize};
