'use strict';
const assert=require('assert/strict'),http=require('http'),fs=require('fs'),os=require('os'),path=require('path'),crypto=require('crypto');
const {createVendorSource,normalize}=require('../src/data_sync/vendor_http_source');
const packet={devID:18136,recvAt:1792468008245066,value:857,dot:2,unit:1};
assert.equal(normalize(packet,18136,'YB-106','MPa').valueKpa,8.57);
assert.equal(normalize({...packet,unit:2},18136,'YB-106','MPa').valueKpa,.00857);
assert.equal(normalize({...packet,value:0},18136,'YB-106','MPa').valueKpa,0);
assert.equal(normalize(packet,999,'YB-106','MPa'),null);
assert.equal(normalize({...packet,unit:99},18136,'YB-106','MPa').valueKpa,null);
async function main(){
 const root=fs.mkdtempSync(path.join(os.tmpdir(),'vendor-http-'));let source,seenCursor;
 const server=http.createServer((req,res)=>{let body='';req.on('data',d=>body+=d);req.on('end',()=>{
   res.setHeader('Content-Type','application/json');
   if(req.url==='/viplogin'){const data=JSON.parse(body);assert.equal(data.password,crypto.createHash('md5').update('secret'+data.key+'1234XRTech').digest('hex'));res.setHeader('token','test-token');res.end('{"code":0}');}
   else{assert.equal(req.headers.token,'test-token');seenCursor=new URL(req.url,'http://localhost').searchParams.get('afterTime');res.setHeader('token','renewed-token');res.end(JSON.stringify({code:0,data:[packet,packet]}));}
 });});
 try{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));fs.writeFileSync(path.join(root,'creds.json'),JSON.stringify({account:'user1234',password:'secret'}));
 const cfg={enabled:true,deviceId:18136,projectId:123,vendorBaseUrl:'http://127.0.0.1:'+server.address().port,credentialsFile:'creds.json'};
 source=createVendorSource(root,cfg,'YB-106','MPa');
 const deadline=Date.now()+4000;while(source.snapshot().status==='waiting'&&Date.now()<deadline)await new Promise(r=>setTimeout(r,20));
 assert.equal(source.snapshot().status,'connected');assert.equal(seenCursor,'0');assert.equal(source.snapshot().recordCount,1);assert.equal(source.snapshot().latest.valueKpa,8.57);
 const cache=JSON.parse(fs.readFileSync(path.join(root,'data/transmitter/vendor-http.json')));assert.equal(cache.cursor,String(packet.recvAt));assert(!JSON.stringify(source.snapshot()).includes('test-token'));
 console.log('PASS: login signature, token header, real vendor sample, unit conversion, deduplication, cursor persistence and secret exclusion');
 }finally{source?.stop();await new Promise(r=>server.close(r));fs.rmSync(root,{recursive:true,force:true});}
}
main().catch(e=>{console.error(e);process.exitCode=1;});
