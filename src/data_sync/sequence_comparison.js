'use strict';
const finite=n=>typeof n==='number'&&Number.isFinite(n);

// 两个已按采集/平台时间排序的保留记录列表，按当前位置配对。
// 失败识别和未知单位仍占一个次数，不让后续记录向前错位。
function buildSequenceComparison(recognition,transmitter,cfg){
  const rows=[];
  for(let index=0;index<Math.max(recognition.length,transmitter.length);index++){
    const camera=recognition[index]||null,reference=transmitter[index]||null;
    const valueKpa=camera&&finite(camera.valueKpa)?camera.valueKpa:null;
    const transmitterKpa=reference&&finite(reference.valueKpa)?reference.valueKpa:null;
    const deltaKpa=valueKpa!==null&&transmitterKpa!==null?+(valueKpa-transmitterKpa).toFixed(10):null;
    const percentFS=deltaKpa!==null?+(Math.abs(deltaKpa)/cfg.meter.rangeKpa*100).toFixed(10):null;
    let state='pending';
    if(camera&&valueKpa===null)state='failed';
    else if(camera&&(camera.status!=='ok'||valueKpa<0||valueKpa>cfg.meter.rangeKpa))state='review';
    else if(reference&&transmitterKpa===null)state='review';
    else if(percentFS!==null)state=percentFS+1e-9>=cfg.meter.accuracyClass*cfg.alarmCoef?'alarm'
      :percentFS+1e-9>=cfg.meter.accuracyClass*cfg.warnCoef?'warning':'normal';
    rows.push({sequence:index+1,recognitionId:camera?.id||null,recognitionValueKpa:valueKpa,
      recognitionCapturedAt:camera?.capturedAt||null,recognitionStatus:camera?.status||null,
      transmitterPresent:!!reference,transmitterKpa,transmitterRecordedAt:reference?.vendorReceivedAt||null,
      transmitterStamp:reference?.vendorRecvAtUs||null,deltaKpa,percentFS,state,
      pairingStatus:!camera?'waiting_recognition':!reference?'waiting_transmitter':deltaKpa===null?'invalid':'paired'});
  }
  const calculated=rows.filter(row=>row.deltaKpa!==null);
  return {rows,pairedCount:calculated.length,latest:calculated.length?calculated[calculated.length-1]:null};
}
module.exports={buildSequenceComparison};
