'use strict';
const assert=require('assert/strict');
const fs=require('fs');
const vm=require('vm');
const source=fs.readFileSync(__dirname+'/../web/js/live.js','utf8');
const ctx={number:n=>typeof n==='number'&&Number.isFinite(n),labels:{normal:'偏差正常',review:'识别需复核'}};
vm.createContext(ctx);
vm.runInContext(source.slice(source.indexOf('  function resolveChartPair('),source.indexOf('  const liveRoute')),ctx);
const rows=[
  {sequence:1,recognitionId:'a',transmitterStamp:'t1',deltaKpa:1,state:'normal'},
  {sequence:2,recognitionId:'b',transmitterStamp:'t2',deltaKpa:0,state:'normal'},
  {sequence:3,recognitionId:null,transmitterStamp:'t3',deltaKpa:null,state:'pending',pairingStatus:'waiting_recognition'}
];
const original=JSON.stringify(rows);
assert.equal(ctx.resolveChartPair(rows,null).row,rows[1],'zero deviation is a valid latest pair');
const cameraSelection={source:'recognition',recognitionId:'a',sequence:1};
const appended=[...rows,{sequence:4,recognitionId:'d',transmitterStamp:'t4',deltaKpa:4}];
assert.equal(ctx.resolveChartPair(appended,cameraSelection).row,rows[0],'new data must not replace a selected historical sample');
const renumbered=[{...rows[0],sequence:2},rows[1],rows[2]];
assert.equal(ctx.resolveChartPair(renumbered,cameraSelection).row.sequence,2,'selection follows sample identity after renumbering');
const txSelection={source:'transmitter',transmitterStamp:'t3',sequence:3};
assert.equal(ctx.resolveChartPair(rows,txSelection).row.deltaKpa,null,'missing counterpart must not become zero');
assert.equal(ctx.comparisonLabel(ctx.resolveChartPair(rows,txSelection).row),'等待识别');
const completed=rows.map(row=>row.sequence===3?{...row,recognitionId:'c',deltaKpa:3,state:'normal'}:row);
assert.equal(ctx.resolveChartPair(completed,txSelection).row.deltaKpa,3,'a new counterpart completes the selected pair');
const removed=ctx.resolveChartPair(rows.slice(1),cameraSelection);
assert.equal(removed.selection,null,'deleted sample exits locked selection');
assert.equal(removed.row,rows[1]);
assert.equal(ctx.resolveChartPair([],cameraSelection).row,null);
assert.equal(ctx.comparisonLabel({state:'pending',pairingStatus:'waiting_transmitter'}),'等待变送器');
assert.equal(ctx.comparisonLabel({state:'review',transmitterPresent:true,transmitterKpa:null}),'单位待确认');
assert.equal(JSON.stringify(rows),original,'selection must not mutate source records');
console.log('PASS: historical selection, refresh/new records, identity-based renumbering, missing counterpart, later pairing, zero deviation, deletion fallback and no source mutation.');
