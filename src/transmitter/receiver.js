'use strict';
// 部署到 Windows 云服务器；商家 POST 接收与本机只读同步接口。
const http=require('node:http');
const fs=require('node:fs');
const path=require('node:path');
const crypto=require('node:crypto');
const configPath=path.resolve(process.argv[2]||path.join(__dirname,'config.json'));
const config=JSON.parse(fs.readFileSync(configPath,'utf8').replace(/^\uFEFF/,''));
const secretFile=config.keyFile?path.resolve(path.dirname(configPath),config.keyFile):null;
const secret=process.env.PUSH_KEY||(secretFile&&fs.existsSync(secretFile)?fs.readFileSync(secretFile,'utf8').replace(/^\uFEFF/,'').trim():null);
const readKeyFile=config.readKeyFile?path.resolve(path.dirname(configPath),config.readKeyFile):null;
const readKey=process.env.READ_KEY||(readKeyFile&&fs.existsSync(readKeyFile)?fs.readFileSync(readKeyFile,'utf8').replace(/^\uFEFF/,'').trim():null);
if(!secret) throw new Error('请设置 PUSH_KEY，并与商家后台的推送密钥保持完全一致');
if(!config.devices||!Object.keys(config.devices).length||Object.keys(config.devices).some(id=>!/^\d+$/.test(id)))
  throw new Error('请在 config.json 的 devices 中填写真实的数字设备ID');
const base=path.resolve(path.dirname(configPath),config.dataDir||'data');
fs.mkdirSync(base,{recursive:true});
const pressureUnits={1:{name:'MPa',factor:1000},2:{name:'kPa',factor:1},3:{name:'Pa',factor:.001},4:{name:'bar',factor:100},5:{name:'mbar',factor:.1}};
function reply(res,status,message){if(!res.destroyed&&!res.writableEnded){res.writeHead(status,{'Content-Type':'application/json; charset=utf-8','Cache-Control':'no-store'});res.end(JSON.stringify(message));}}
function atomicJson(file,data){
  fs.mkdirSync(path.dirname(file),{recursive:true});
  const temp=file+'.'+crypto.randomUUID()+'.tmp';
  let fd;
  try{fd=fs.openSync(temp,'wx',0o600);fs.writeFileSync(fd,JSON.stringify(data,null,2));fs.fsyncSync(fd);fs.closeSync(fd);fd=undefined;fs.renameSync(temp,file);}
  finally{if(fd!==undefined)fs.closeSync(fd);if(fs.existsSync(temp))fs.unlinkSync(temp);}
}
function accept(packet){
  if(!packet||typeof packet!=='object'||Array.isArray(packet))return [400,{error:'JSON必须是一个对象'}];
  if(!Number.isSafeInteger(packet.recvAt)||!/^\d{16}$/.test(String(packet.recvAt)))return [400,{error:'recvAt应为16位整数微秒时间戳'}];
  const expected=crypto.createHash('md5').update(String(packet.recvAt)+secret,'utf8').digest('hex');
  if(typeof packet.auth!=='string'||!/^[0-9a-f]{32}$/.test(packet.auth)||!crypto.timingSafeEqual(Buffer.from(packet.auth,'hex'),Buffer.from(expected,'hex')))
    return [401,{error:'鉴权失败'}];
  if(!Number.isSafeInteger(packet.devID)||!Object.hasOwn(config.devices,String(packet.devID)))return [403,{error:'设备未配置'}];
  if(!Number.isSafeInteger(packet.value)||!Number.isInteger(packet.dot)||packet.dot<0||packet.dot>12||!Number.isInteger(packet.unit))return [400,{error:'value、dot或unit格式不正确'}];
  const vendorReceivedAt=new Date(Math.floor(packet.recvAt/1000)).toISOString();
  const value=packet.value/10**packet.dot,unit=pressureUnits[packet.unit];
  const {auth,...raw}=packet;
  const record={meterId:config.devices[String(packet.devID)],deviceId:packet.devID,vendorRecvAtUs:String(packet.recvAt),vendorReceivedAt,
    serverReceivedAt:new Date().toISOString(),value,unitCode:packet.unit,unitName:unit?.name||null,
    valueKpa:unit?value*unit.factor:null,normalizationStatus:unit?'converted':'单位待确认，已保存原始值',authVerified:true,raw};
  const file=path.join(base,'history',String(packet.devID),String(packet.recvAt)+'.json');
  const duplicate=fs.existsSync(file);
  // 单进程同步提交：记录成功落盘后才回200。设备ID+recvAt作为暂定幂等键。
  if(!duplicate)atomicJson(file,record);
  const stored=duplicate?JSON.parse(fs.readFileSync(file,'utf8')):record;
  const latestPath=path.join(base,'latest',String(packet.devID)+'.json');
  const latest=fs.existsSync(latestPath)?JSON.parse(fs.readFileSync(latestPath,'utf8')):null;
  if(!latest||BigInt(stored.vendorRecvAtUs)>=BigInt(latest.vendorRecvAtUs))atomicJson(latestPath,stored);
  const feedPath=path.join(base,'feed',String(packet.devID)+'.json');
  const previous=fs.existsSync(feedPath)?JSON.parse(fs.readFileSync(feedPath,'utf8')):[];
  const feed=new Map(previous.map(r=>[r.vendorRecvAtUs,r]));
  feed.set(stored.vendorRecvAtUs,stored);
  atomicJson(feedPath,[...feed.values()].sort((a,b)=>a.vendorRecvAtUs.localeCompare(b.vendorRecvAtUs)).slice(-1000));
  console.log(JSON.stringify({receivedAt:record.serverReceivedAt,devID:packet.devID,recvAt:packet.recvAt,duplicate,valueKpa:stored.valueKpa}));
  return [200,{ok:true,duplicate}];
}
const server=http.createServer(async(req,res)=>{
  const url=new URL(req.url,'http://localhost');
  if(req.method==='GET'&&url.pathname==='/health')return reply(res,200,{ok:true});
  if(req.method==='GET'&&url.pathname==='/api/transmitter/readings'){
    if(!readKey)return reply(res,503,{error:'服务器尚未设置只读密钥'});
    const token=req.headers.authorization||'';
    const expected='Bearer '+readKey;
    if(Buffer.byteLength(token)!==Buffer.byteLength(expected)||!crypto.timingSafeEqual(Buffer.from(token),Buffer.from(expected)))
      return reply(res,401,{error:'只读鉴权失败'});
    const id=url.searchParams.get('deviceId');
    if(!id||!Object.hasOwn(config.devices,id))return reply(res,404,{error:'设备未配置'});
    try{
      const file=path.join(base,'feed',id+'.json');
      const readings=fs.existsSync(file)?JSON.parse(fs.readFileSync(file,'utf8')):[];
      return reply(res,200,{deviceId:id,meterId:config.devices[id],readings,serverTime:new Date().toISOString()});
    }catch{return reply(res,503,{error:'服务器数据暂不可读'});}
  }
  if(url.pathname!=='/api/transmitter/push')return reply(res,404,{error:'接口不存在'});
  if(req.method!=='POST')return reply(res,405,{error:'请使用POST'});
  if((req.headers['content-type']||'').split(';')[0].trim().toLowerCase()!=='application/json')return reply(res,415,{error:'Content-Type应为application/json'});
  try{
    const chunks=[];let size=0;
    for await(const chunk of req){size+=chunk.length;if(size>65536){reply(res,413,{error:'请求过大'});return;}chunks.push(chunk);}
    let packet;
    try{packet=JSON.parse(Buffer.concat(chunks).toString('utf8'));}catch{return reply(res,400,{error:'JSON格式错误'});}
    const [status,body]=accept(packet);reply(res,status,body);
  }catch(error){console.error('接收或保存失败：'+error.message);reply(res,503,{error:'保存失败，请重试'});}
});
server.requestTimeout=10000;
server.headersTimeout=10000;
server.on('error',error=>{console.error(error.message);process.exitCode=1;});
server.listen(config.port||8890,config.host||'127.0.0.1',()=>console.log('接收接口已启动 '+(config.host||'127.0.0.1')+':'+server.address().port+'/api/transmitter/push'));
