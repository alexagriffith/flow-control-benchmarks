import {bands,state} from './policy-demo-model.mjs';
export const clamp=x=>Math.max(0,Math.min(1,x));
export const ease=x=>{x=clamp(x);return x*x*(3-2*x)};
export const layout={width:1000,height:650,queueX:30,queueW:340,gateX:495,poolX:675,poolW:290,slotX:825,radius:13};
export const rowY=band=>170+160*bands.findIndex(b=>b.key===band);
export const laneY=band=>rowY(band)+75;
export const slotY=slot=>305+55*slot;
export function curve(a,b,c,d,n=80){return Array.from({length:n+1},(_,i)=>{const t=i/n,u=1-t;return a.map((v,k)=>u*u*u*v+3*u*u*t*b[k]+3*u*t*t*c[k]+t*t*t*d[k])})}
export function along(points,u){const lengths=[0];for(let i=1;i<points.length;i++)lengths.push(lengths[i-1]+Math.hypot(points[i][0]-points[i-1][0],points[i][1]-points[i-1][1]));const d=clamp(u)*lengths.at(-1);let i=1;while(i<lengths.length-1&&lengths[i]<d)i++;const f=(d-lengths[i-1])/(lengths[i]-lengths[i-1]||1);return points[i-1].map((x,k)=>x+(points[i][k]-x)*f)}
export const stem=band=>curve([495,laneY(band)],[570,laneY(band)],[580,420],[700,420]);
export const branch=slot=>curve([700,420],[750,420],[770,slotY(slot)],[825,slotY(slot)]);
export function visualRequests(t){return state(t).dots.flatMap(q=>{
  if(['future','completed'].includes(q.status))return [];
  const start=[330-q.queue*55,laneY(q.band)];let xy,alpha=1;
  if(q.status==='arriving'){xy=[-20+(start[0]+20)*ease((t-q.enter)/(q.queued-q.enter)),start[1]];alpha=clamp((t-q.enter)/180)}
  if(q.status==='queued'){
    // Approach the exit while still queued. Admission happens exactly at the check.
    const u=ease((t-(q.depart-350))/350);xy=[start[0]+(495-start[0])*u,start[1]];
  }
  if(q.status==='transit')xy=along([...stem(q.band),...branch(q.slot).slice(1)],ease((t-q.depart)/(q.arrive-q.depart)));
  if(q.status==='running'){
    xy=[825,slotY(q.slot)];
    if(Number.isFinite(q.complete)&&t>q.complete-550){
      const u=ease((t-(q.complete-550))/550);xy=[825+195*u,slotY(q.slot)];
      alpha=1-ease((xy[0]-970)/50);
    }
  }
  return [{...q,x:xy[0],y:xy[1],alpha,r:13}];
})}
