import {duration,bands,state,validate} from './policy-demo-model.mjs';
import {layout,rowY,laneY,slotY,stem,branch,visualRequests} from './policy-demo-motion.mjs';
const $=id=>document.getElementById(id),canvas=$('stage'),ctx=canvas.getContext('2d');
const font='system-ui, -apple-system, BlinkMacSystemFont, sans-serif';
let raf,exportUrl;
function text(value,x,y,size=24,color='#e8eaf0',weight=550,align='left'){ctx.font=`${weight} ${size}px ${font}`;ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(value,x,y)}
function box(x,y,w,h,stroke='#343844',fill='#14171d',r=16){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke()}
function dot(x,y,color,r=13){ctx.beginPath();ctx.arc(x,y,r,0,Math.PI*2);ctx.fillStyle=color;ctx.fill()}
function line(points,color='#454b5d',width=2){ctx.beginPath();ctx.moveTo(...points[0]);for(const p of points.slice(1))ctx.lineTo(...p);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.stroke()}
function render(t){
  const s=state(t);ctx.fillStyle='#0e1014';ctx.fillRect(0,0,layout.width,layout.height);
  text('Shared traffic needs policies',30,58,36,'#e8eaf0',750);
  text('Ceiling',495,140,26,'#b9bfcc',600,'center');
  box(675,230,290,360,'#3fbb85');
  text('Shared pool',820,273,29,'#e8eaf0',700,'center');
  for(const b of bands){
    const y=rowY(b.key),ly=laneY(b.key),blocked=s.pressure>=b.ceiling;
    box(30,y,340,105,s.dots.some(q=>q.band===b.key&&q.status==='queued')&&!blocked?b.color:'#343844');
    text(b.key,50,y+35,30,b.color,750);
    line([[370,ly],[495,ly],...stem(b.key).slice(1)]);
    // A threshold on the path, not a separate service or a queue-size limit.
    text(b.ceiling.toFixed(2),495,y+35,28,'#e8eaf0',650,'center');
    const c=blocked?'#7b8294':b.color;
    line([[495,ly-19],[495,blocked?ly+19:ly-12]],c,4);
    if(!blocked)line([[495,ly+12],[495,ly+19]],c,4);
  }
  for(let i=0;i<5;i++){
    line(branch(i),'#303746');
    ctx.beginPath();ctx.arc(825,slotY(i),15,0,Math.PI*2);ctx.strokeStyle='#667088';ctx.lineWidth=2;ctx.stroke();
  }
  text(`Pool saturation ${s.pressure.toFixed(2)}`,675,140,27,'#e8eaf0',700);
  box(675,160,290,17,'#343844','#14171d',7);ctx.fillStyle='#7181ff';ctx.fillRect(678,163,284*s.pressure,11);
  for(const q of visualRequests(t)){ctx.globalAlpha=q.alpha;dot(q.x,q.y,bands.find(b=>b.key===q.band).color,q.r);ctx.globalAlpha=1}
  $('seek').value=String(Math.round(t));canvas.dataset.time=String(t);canvas.dataset.counts=JSON.stringify({queued:s.dots.filter(q=>q.status==='queued').length,transit:s.dots.filter(q=>q.status==='transit').length,running:s.dots.filter(q=>q.status==='running').length,completed:s.completed});
}

try{
  const verification=validate();canvas.dataset.validation=verification;
  $('play').onclick=()=>{cancelAnimationFrame(raf);const start=performance.now();const tick=now=>{const t=Math.min(duration,now-start);render(t);if(t<duration)raf=requestAnimationFrame(tick)};raf=requestAnimationFrame(tick)};
  $('seek').oninput=()=>{cancelAnimationFrame(raf);render(Number($('seek').value))};
  $('export').onclick=async()=>{
    cancelAnimationFrame(raf);$('play').disabled=$('export').disabled=true;
    try{const parts=[],enc=new TextEncoder(),count=duration*60/1000,field=(h,o,n,v)=>h.set(enc.encode(v).subarray(0,n),o);
      for(let i=0;i<count;i++){
        render(i*1000/60);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.98));if(!blob)throw Error('Frame export failed');
        const h=new Uint8Array(512);field(h,0,100,`frame-${String(i).padStart(5,'0')}.jpg`);field(h,100,8,'0000644\0');field(h,108,8,'0000000\0');field(h,116,8,'0000000\0');field(h,124,12,blob.size.toString(8).padStart(11,'0')+'\0');field(h,136,12,'00000000000\0');h.fill(32,148,156);h[156]=48;field(h,257,6,'ustar\0');field(h,263,2,'00');field(h,148,8,h.reduce((a,b)=>a+b,0).toString(8).padStart(6,'0')+'\0 ');parts.push(h,blob,new Uint8Array((512-blob.size%512)%512));
        if(i%30===0){$('status').textContent=`Exporting ${i+1}/${count}`;await new Promise(requestAnimationFrame)}
      }
      parts.push(new Uint8Array(1024));if(exportUrl)URL.revokeObjectURL(exportUrl);exportUrl=URL.createObjectURL(new Blob(parts,{type:'application/x-tar'}));$('download').href=exportUrl;$('download').download='chapter-4-admission-check-frames.tar';$('download').hidden=false;$('status').textContent=`${count} frames · 60 fps`;
    }catch(e){$('status').textContent=e.message}finally{$('play').disabled=$('export').disabled=false}
  };
  render(0);$('status').textContent='Ready · 14 seconds';document.documentElement.dataset.ready='true';
}catch(e){$('status').textContent=e.message;document.documentElement.dataset.error=e.message}
