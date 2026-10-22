'use strict';
const {buildSequenceComparison}=require('./sequence_comparison');
const finite=Number.isFinite;
// Chronological one-to-one assignment: maximize pair count, then minimize total time gap.
function buildTimeComparison(recognition,transmitter,cfg){
  const a=recognition.slice().sort((a,b)=>Date.parse(a.capturedAt)-Date.parse(b.capturedAt));
  const b=transmitter.slice().sort((a,b)=>Date.parse(a.vendorReceivedAt)-Date.parse(b.vendorReceivedAt));
  let entries;
  if(cfg.recognitionMode==='transmitter'){
    const byStamp=new Map(a.filter(r=>r.transmitterStamp).map(r=>[r.transmitterStamp,r]));const used=new Set();
    entries=b.map(t=>{const r=byStamp.get(t.vendorRecvAtUs)||null;if(r)used.add(r.id);return {r,t};});
    a.forEach(r=>{if(!used.has(r.id))entries.push({r,t:null});});
  }else{
  const n=a.length,m=b.length,tolerance=cfg.pairingToleranceSeconds??10;
  const directions=new Uint8Array((n+1)*(m+1));let counts=new Int32Array(m+1),costs=new Float64Array(m+1);
  for(let i=1;i<=n;i++){
    const nextCounts=new Int32Array(m+1),nextCosts=new Float64Array(m+1);
    for(let j=1;j<=m;j++){
      let count=counts[j],cost=costs[j],direction=1;
      if(nextCounts[j-1]>count||(nextCounts[j-1]===count&&nextCosts[j-1]<cost)){count=nextCounts[j-1];cost=nextCosts[j-1];direction=2;}
      const gap=Math.abs(Date.parse(a[i-1].capturedAt)-Date.parse(b[j-1].vendorReceivedAt))/1000;
      if(a[i-1].origin!=='offline'&&finite(gap)&&gap<=tolerance&&(counts[j-1]+1>count||(counts[j-1]+1===count&&costs[j-1]+gap<=cost))){count=counts[j-1]+1;cost=costs[j-1]+gap;direction=3;}
      nextCounts[j]=count;nextCosts[j]=cost;directions[i*(m+1)+j]=direction;
    }
    counts=nextCounts;costs=nextCosts;
  }
  const matched=new Map(),used=new Set();let i=n,j=m;
  while(i&&j){const direction=directions[i*(m+1)+j];if(direction===3){matched.set(i-1,j-1);used.add(j-1);i--;j--;}else if(direction===1)i--;else j--;}
  entries=a.map((r,index)=>({r,t:matched.has(index)?b[matched.get(index)]:null}));
  b.forEach((t,index)=>{if(!used.has(index))entries.push({r:null,t});});
  }
  entries.sort((a,b)=>Date.parse(a.r?.capturedAt||a.t.vendorReceivedAt)-Date.parse(b.r?.capturedAt||b.t.vendorReceivedAt));
  const rows=entries.map(({r,t},index)=>{
    const row=buildSequenceComparison(r?[r]:[],t?[t]:[],cfg).rows[0];
    const missing=r?.triggerStatus==='missing_frame';
    if(missing)row.recognitionCapturedAt=null;
    const quality=missing?'缺少对应历史画面':r&&r.origin!=='camera'?'非实时摄像头数据':t?.timeout?'变送器报文标记超时':null;
    if(quality){row.state='review';row.deltaKpa=null;row.percentFS=null;row.pairingStatus='invalid';}
    const gap=r&&t&&!missing?Math.abs(Date.parse(r.capturedAt)-Date.parse(t.vendorReceivedAt))/1000:null;
    if(cfg.recognitionMode==='transmitter'&&gap!==null&&gap>(cfg.pairingToleranceSeconds??10)){row.state='review';row.pairingStatus='invalid';}
    if(row.state==='review'||row.state==='failed'){row.deltaKpa=null;row.percentFS=null;}
    return {...row,sequence:index+1,triggerStatus:r?.triggerStatus||null,timeDiffSeconds:gap,timeBasis:'vendorReceivedAt',qualityNote:quality};
  });
  const valid=rows.filter(r=>finite(r.deltaKpa));
  return {rows,pairedCount:valid.length,latest:valid.at(-1)||null};
}
module.exports={buildTimeComparison};
