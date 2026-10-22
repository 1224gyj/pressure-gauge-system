'use strict';
const assert=require('assert/strict'),fs=require('fs'),os=require('os'),path=require('path');
const {createTransmitterSource}=require('../src/data_sync/transmitter_source');
const {buildSequenceComparison}=require('../src/data_sync/sequence_comparison');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'pressure-mpa-'));
const write=(name,data)=>fs.writeFileSync(path.join(root,name),JSON.stringify(data));
let source;
try{
  fs.writeFileSync(path.join(root,'key'),'test-only');
  write('cfg.json',{enabled:true,deviceId:'18136',serverUrl:'http://127.0.0.1:1',readKeyFile:'key',cacheFile:'cache.json',maxTimeDiffSeconds:15,staleAfterSeconds:60});
  const row={deviceId:'18136',meterId:'YB-106',vendorRecvAtUs:'1792468008245066',vendorReceivedAt:new Date(1792468008245).toISOString(),serverReceivedAt:new Date().toISOString(),valueKpa:8570,value:8.57,unitName:'MPa',unitCode:1,authVerified:true};
  write('cache.json',{deviceId:'18136',meterId:'YB-106',serverUrl:'http://127.0.0.1:1',readings:[row]});
  source=createTransmitterSource(root,path.join(root,'cfg.json'),'YB-106','MPa');
  const reference=source.snapshot().latest;
  assert.equal(reference.valueKpa,8.57);
  assert.equal(source.snapshot().latest.valueKpa,8.57,'repeated snapshots must not convert twice');
  assert.equal(source.match(row.vendorReceivedAt).valueKpa,8.57);
  const cfg={meter:{rangeKpa:16,accuracyClass:2.5},warnCoef:.5,alarmCoef:1};
  const compared=buildSequenceComparison([{valueKpa:8.77,status:'ok'}],[reference],cfg).latest;
  assert.equal(compared.recognitionValueKpa,8.77);
  assert.equal(compared.deltaKpa,.2);
  assert.equal(compared.state,'warning');
  assert.equal(JSON.parse(fs.readFileSync(path.join(root,'cache.json'))).readings[0].valueKpa,8570);
  console.log('PASS: MPa conversion, unchanged recognition, error thresholds, cache and repeated reads');
}finally{
  if(source)source.stop();
  fs.rmSync(root,{recursive:true,force:true});
}
