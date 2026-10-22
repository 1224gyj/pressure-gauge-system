'use strict';
// 统一启动入口：已有实时服务则复用；端口上的其他服务给出明确提示。
const fs = require('fs');
const http = require('http');
const path = require('path');
const {spawn} = require('child_process');
const root = path.resolve(__dirname,'..');
let port;
try {
  const cfg = JSON.parse(fs.readFileSync(path.join(root,'config/sync.json'),'utf8'));
  port = process.argv[2] === undefined ? cfg.port : Number(process.argv[2]);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('端口必须是 1～65535 的整数');
} catch(e) { console.error('[启动失败] '+e.message); process.exit(1); }
function probe() {
  return new Promise(resolve => {
    const req=http.get({hostname:'127.0.0.1',port,path:'/api/live',timeout:1500},res=>{
      let body='';
      res.on('data',d=>{body+=d;});
      res.on('end',()=>{
        try {const data=JSON.parse(body);resolve({occupied:true,live:res.statusCode===200 && data.mode==='single'});}
        catch(e){resolve({occupied:true,live:false});}
      });
    });
    req.on('timeout',()=>{req.destroy();resolve({occupied:true,live:false});});
    req.on('error',e=>resolve({occupied:e.code!=='ECONNREFUSED',live:false}));
  });
}
(async()=>{
  const status=await probe();
  if(status.live){console.log('实时前端已经运行，无需重复启动。\n请打开 http://localhost:'+port+'/');return;}
  if(status.occupied){console.error('[启动失败] '+port+' 端口被其他服务占用。\n请先关闭旧前端服务，或修改 config\\sync.json 的 port。');process.exitCode=1;return;}
  console.log('正在启动单表实时前端：http://localhost:'+port+'/\n关闭此窗口或按 Ctrl+C 可停止服务。');
  const child=spawn(process.execPath,[path.join(root,'src/data_sync/server.js'),String(port)],{cwd:root,stdio:'inherit',windowsHide:true});
  child.on('error',e=>{console.error('[启动失败] '+e.message);process.exitCode=1;});
  child.on('exit',code=>{if(code)console.error('[前端服务退出] 错误代码 '+code);process.exitCode=code || 0;});
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,()=>child.kill(signal));
})().catch(e=>{console.error('[启动失败] '+e.message);process.exitCode=1;});
