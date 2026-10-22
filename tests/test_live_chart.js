'use strict';
const assert=require('assert/strict');
const fs=require('fs');
const vm=require('vm');
const source=fs.readFileSync(__dirname+'/../web/js/live.js','utf8');
const start=source.indexOf('  function chart('),end=source.indexOf('  async function refresh()',start);
const target={innerHTML:''};
const ctx={$:()=>target,number:n=>typeof n==='number'&&Number.isFinite(n),fmt:n=>n.toFixed(2),
  esc:s=>String(s),time:s=>s,readingState:p=>p.status==='ok'?'recognized':'review'};
vm.createContext(ctx);vm.runInContext(source.slice(start,end),ctx);
const recognition=values=>values.map(valueKpa=>({valueKpa,status:'ok',capturedAt:'2026-10-16T21:00:00+08:00'}));
const transmitter=values=>values.map(valueKpa=>({valueKpa,vendorReceivedAt:'2026-10-16T04:00:00Z'}));
function draw(a,b=[]){
  ctx.chart(recognition(a),16,transmitter(b));
  assert(!/NaN|Infinity/.test(target.innerHTML));return target.innerHTML;
}
function dots(html,key){
  const expression=new RegExp('<circle '+key+'="(\\d+)" cx="([^"]+)" cy="([^"]+)"','g');
  return Array.from(html.matchAll(expression),m=>({sequence:+m[1],x:+m[2],y:+m[3]}));
}
assert(!draw([]).includes('<path'));
assert(!draw([null,null]).includes('<path'));
let html=draw([0],[0]);
assert.equal(dots(html,'data-sample').length,1);
assert.equal(dots(html,'data-transmitter-sample').length,1);
assert.equal(dots(html,'data-sample')[0].x,554);
assert.equal(dots(html,'data-sample')[0].x,dots(html,'data-transmitter-sample')[0].x);
assert(!html.includes('<path'));
html=draw([4,5,6],[0,1,2,3,4]);
let camera=dots(html,'data-sample'),reference=dots(html,'data-transmitter-sample');
assert.equal(camera.length,3);assert.equal(reference.length,5);
for(let i=0;i<camera.length;i++)assert.equal(camera[i].x,reference[i].x,'nth samples must share x coordinate');
assert.equal(reference[0].x,52);assert.equal(reference[4].x,1056);
assert(camera[2].x<reference[4].x,'shorter series must not stretch to a different scale');
assert(html.includes('data-series="recognition"')&&html.includes('data-series="transmitter"'));
assert(html.includes('stroke-dasharray="6 3"'),'overlapping lines must remain distinguishable');
html=draw([4,null,8,9],[0,1,2,3]);
camera=dots(html,'data-sample');reference=dots(html,'data-transmitter-sample');
assert.deepEqual(camera.map(p=>p.sequence),[1,3,4]);
assert.equal(camera[1].x,reference[2].x);
assert.equal((html.match(/data-series="recognition"/g)||[]).length,1);
const firstPath=html.match(/data-series="recognition" d="([^"]+)"/)[1];
assert.equal(+firstPath.split(' ')[1],camera[1].x,'a gap must not be bridged');
html=draw([],Array(10).fill(0));reference=dots(html,'data-transmitter-sample');
assert.equal(reference.length,10);assert.equal(reference[0].x,52);assert.equal(reference[9].x,1056);
assert.equal(new Set(reference.map(p=>p.y)).size,1);
assert(!html.includes('data-series="recognition"'));
assert(html.includes('data-series="transmitter"'));
for(const values of [[16,0,16,0,8],[0,0,0],Array.from({length:1000},(_,i)=>8+6*Math.sin(i))]){
  html=draw(values,Array(Math.min(700,values.length)).fill(0));
  camera=dots(html,'data-sample');reference=dots(html,'data-transmitter-sample');
  assert.equal(camera[0].x,52);assert.equal(camera[camera.length-1].x,1056);
  assert.equal(camera.length,values.length);
  for(let i=0;i<reference.length;i++)assert.equal(reference[i].x,camera[i].x);
  assert((html.match(/>第 \d+ 次<\/text>/g)||[]).length<=6);
  for(const match of html.matchAll(/data-series="[^"]+" d="([^"]+)"/g)){
    const numbers=match[1].match(/-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/g).map(Number);
    let a=numbers.slice(0,2);
    for(let i=2;i<numbers.length;i+=6){
      const [x1,y1,x2,y2,bx,by]=numbers.slice(i,i+6);
      assert(x1>a[0]&&x2>x1&&bx>x2);
      for(let j=0;j<=20;j++){
        const t=j/20,u=1-t,yy=u*u*u*a[1]+3*u*u*t*y1+3*u*t*t*y2+t*t*t*by;
        assert(yy>=Math.min(a[1],by)-1e-8&&yy<=Math.max(a[1],by)+1e-8,'curve overshot adjacent readings');
      }
      a=[bx,by];
    }
  }
}
html=draw([-2,20],[0,16]);
assert(!html.includes('NaN'));
camera=dots(html,'data-sample');assert(camera[0].y>camera[1].y);
console.log('PASS: two independent series, nth shared x coordinate, unequal lengths, zero, single point, missing slots and disconnected gaps, full width, 1000 records, adaptive ticks, no smoothing overshoot and out-of-range values.');
