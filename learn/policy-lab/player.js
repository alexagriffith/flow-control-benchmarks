import {duration,names,rowY,state,smooth,validate,build,requests,rank,service,fairnessNames,orderingNames} from './model.mjs';
const $=id=>document.getElementById(id),canvas=$('stage'),ctx=canvas.getContext('2d');
const font='system-ui, -apple-system, BlinkMacSystemFont, sans-serif';let raf,exportUrl,current=0;
const params=new URLSearchParams(location.search),suite=params.get('suite')==='1';
if(params.get('embed'))document.body.classList.add('embed');
$('fairness').value=fairnessNames[params.get('fairness')]?params.get('fairness'):'rr';
$('ordering').value=orderingNames[params.get('ordering')]?params.get('ordering'):'fcfs';
let model=build($('fairness').value,$('ordering').value);const total=suite?duration*3:duration;
$('seek').max=total;
$('inputs').innerHTML=requests.map(q=>`<tr><td>${q.id}</td><td>${names[q.row]}</td><td>${q.enqueue}</td><td>${rank(q,'edf')}</td><td>${rank(q,'slo').toFixed(1)}</td></tr>`).join('');

function text(v,x,y,size=28,color='#e8eaf0',weight=600,align='left'){ctx.font=`${weight} ${size}px ${font}`;ctx.fillStyle=color;ctx.textAlign=align;ctx.fillText(v,x,y)}
function box(x,y,w,h,stroke='#343844',fill='#14171d',r=16){ctx.beginPath();ctx.roundRect(x,y,w,h,r);ctx.fillStyle=fill;ctx.fill();ctx.strokeStyle=stroke;ctx.lineWidth=2;ctx.stroke()}
function render(t){
 ctx.fillStyle='#0e1014';ctx.fillRect(0,0,1000,700);
 text('Fairness within a priority band',30,58,36,'#e8eaf0',750);
 const phase=suite?Math.min(2,Math.floor(Math.min(t,total-1)/duration)):0,time=suite?Math.min(t-phase*duration,duration):t;
 if(suite){model=build(['rr','strict','las'][phase],$('ordering').value);$('fairness').value=model.fairness}
 const turns=model.turns;
 text('Ordering (within each queue): '+orderingNames[model.ordering],30,98,25,'#b9bfcc',550);
 text('Fairness (across queues in the same priority band): '+fairnessNames[model.fairness],30,133,24,'#b9bfcc',550);
 text(model.fairness==='strict'?'Compare queue heads using the ordering policy':model.fairness==='las'?'Lower prior service wins here, with head wait included':'Take turns between active queues',30,665,27,'#e8eaf0',550);
 box(30,150,720,465,'#7181ff');text('premium',55,194,28,'#8996ff',700);
 const turn=turns.find(v=>time>=v.start-100&&time<v.start+620);
 for(let r=0;r<3;r++){
  const y=rowY(r);box(55,y-55,525,95,turn?.row===r?'#3fbb85':'#343844');
  text(names[r],75,y-18,27,'#e8eaf0',750);
  const head=turns.find(q=>q.row===r&&time<q.start+620);
  const headLabel=head?(time>=head.start?'Selected #':'Head #')+head.id+' · ':'';
  if(model.fairness==='las')text('Prior service '+service[r],125,y-18,20,'#b9bfcc',500);
  if(head&&model.ordering!=='fcfs')text(headLabel+rank(head,model.ordering).toFixed(1)+'s',model.fairness==='las'?325:125,y-18,model.fairness==='las'?18:20,'#b9bfcc',500);
  ctx.beginPath();ctx.moveTo(580,y);ctx.lineTo(600,y);for(let x=604;x<=804;x+=4)ctx.lineTo(x,y+(408-y)*smooth((x-600)/180));ctx.lineTo(805,408);ctx.strokeStyle=turn?.row===r?'#7181ff':'#454b5d';ctx.lineWidth=2;ctx.stroke();
 }
 box(805,325,155,140,'#3fbb85');text('Dispatch',882,362,26,'#e8eaf0',650,'center');
 ctx.beginPath();ctx.moveTo(960,408);ctx.lineTo(1000,408);ctx.strokeStyle='#454b5d';ctx.stroke();
 for(const q of state(time,model)){if(q.alpha===0)continue;ctx.beginPath();ctx.arc(q.x,q.y,15,0,Math.PI*2);ctx.fillStyle='#7181ff';ctx.fill();text(String(q.id),q.x,q.y+6,17,'#0e1014',750,'center')}
 $('seek').value=String(Math.round(t));canvas.dataset.time=String(t);current=t;canvas.dataset.fairness=model.fairness;canvas.dataset.ordering=model.ordering;
}
try{
  const verification=validate();canvas.dataset.validation=verification;
  let playing=false;
  const stop=()=>{cancelAnimationFrame(raf);playing=false;$('play').textContent='Play'};
  $('play').onclick=()=>{if(playing){stop();return}playing=true;$('play').textContent='Pause';const start=performance.now()-(current>=total?0:current);const tick=now=>{const t=Math.min(total,now-start);render(t);if(t<total)raf=requestAnimationFrame(tick);else stop()};raf=requestAnimationFrame(tick)};
  $('reset').onclick=()=>{stop();render(0)};
  for(const id of ['fairness','ordering'])$(id).onchange=()=>{stop();model=build($('fairness').value,$('ordering').value);render(0)};
  $('seek').oninput=()=>{stop();render(Number($('seek').value))};
  $('export').onclick=async()=>{
    stop();$('play').disabled=$('export').disabled=true;
    try{const parts=[],enc=new TextEncoder(),count=total*60/1000,field=(h,o,n,v)=>h.set(enc.encode(v).subarray(0,n),o);
      for(let i=0;i<count;i++){
        render(i*1000/60);const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.98));if(!blob)throw Error('Frame export failed');
        const h=new Uint8Array(512);field(h,0,100,`frame-${String(i).padStart(5,'0')}.jpg`);field(h,100,8,'0000644\0');field(h,108,8,'0000000\0');field(h,116,8,'0000000\0');field(h,124,12,blob.size.toString(8).padStart(11,'0')+'\0');field(h,136,12,'00000000000\0');h.fill(32,148,156);h[156]=48;field(h,257,6,'ustar\0');field(h,263,2,'00');field(h,148,8,h.reduce((a,b)=>a+b,0).toString(8).padStart(6,'0')+'\0 ');parts.push(h,blob,new Uint8Array((512-blob.size%512)%512));
        if(i%30===0){$('status').textContent=`Exporting ${i+1}/${count}`;await new Promise(requestAnimationFrame)}
      }
      parts.push(new Uint8Array(1024));if(exportUrl)URL.revokeObjectURL(exportUrl);exportUrl=URL.createObjectURL(new Blob(parts,{type:'application/x-tar'}));$('download').href=exportUrl;$('download').download=`policies-${$('ordering').value}-reviewed-frames.tar`;$('download').hidden=false;$('status').textContent=`${count} frames · 60 fps`;
    }catch(e){$('status').textContent=e.message}finally{$('play').disabled=$('export').disabled=false}
  };
  render(0);$('status').textContent=`Ready · ${total/1000} seconds`;document.documentElement.dataset.ready='true';
}catch(e){$('status').textContent=e.message;document.documentElement.dataset.error=e.message}
