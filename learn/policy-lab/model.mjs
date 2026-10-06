// Source: llm-d-router d4b8afd3. A fixed backlogged snapshot; no completion events.
export const duration=10800, names=['A','B','C'], service=[900,100,400];
export const fairnessNames={rr:'Round Robin',strict:'Global Strict',las:'Program-Aware (LAS)'};
export const orderingNames={fcfs:'First-Come, First-Served',edf:'Earliest Deadline First',slo:'SLO Deadline'};
export const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
export const rowY=r=>283+r*125;
// Seconds relative to one shared origin; all are queued by t=10s. TTLs remain valid.
export const requests=Array.from({length:10},(_,i)=>({id:i+1,row:i<5?0:i<8?1:2,enqueue:i,received:i-.1,ttl:[90,75,60,45,30,27,34,50,31,40][i],ttft:[85,70,55,40,25,36,31,26,20,30][i]}));
export function rank(q,ordering){return ordering==='fcfs'?q.enqueue:ordering==='edf'?q.enqueue+q.ttl:q.received+q.ttft}
export function compare(a,b,ordering){return rank(a,ordering)-rank(b,ordering)||(ordering==='slo'?a.received-b.received:a.enqueue-b.enqueue)}
export function build(fairness='rr',ordering='fcfs'){
 const queues=names.map((_,r)=>requests.filter(q=>q.row===r).sort((a,b)=>compare(a,b,ordering))),initial=queues.map(q=>q.map(x=>x.id)),turns=[];let cursor=0;
 while(queues.some(q=>q.length)){
  const active=queues.map((q,r)=>q.length?r:-1).filter(r=>r>=0);let r;
  if(fairness==='rr'){while(!queues[cursor].length)cursor=(cursor+1)%3;r=cursor;cursor=(cursor+1)%3}
  else if(fairness==='strict')r=active.reduce((a,b)=>compare(queues[a][0],queues[b][0],ordering)<=0?a:b);
  else {const max=Math.max(...active.map(r=>service[r])),min=Math.min(...active.map(r=>service[r]));const now=12.2+turns.length*.7;const waitMax=Math.max(...active.map(r=>now-queues[r][0].enqueue)),waitMin=Math.min(...active.map(r=>now-queues[r][0].enqueue));const score=r=>.8*(max===min?.5:(max-service[r])/(max-min))+.2*(waitMax===waitMin?.5:((now-queues[r][0].enqueue-waitMin)/(waitMax-waitMin)));r=active.reduce((a,b)=>score(a)>=score(b)?a:b)}
  const q=queues[r].shift();turns.push({...q,start:2200+turns.length*700});
 }
 return {fairness,ordering,initial,turns};
}
export function state(t,model){return requests.map(q=>{
 const turn=model.turns.find(x=>x.id===q.id),u=(t-turn.start)/620,y=rowY(q.row);
 if(u>=1)return {...q,status:'dispatched',x:1040,y:408,alpha:0};
 if(u>=0){const x=525+515*smooth(u);return {...q,status:'dispatch',x,y:y+(408-y)*smooth((x-600)/180),alpha:1}}
 const index=model.initial[q.row].indexOf(q.id),prior=model.turns.filter(v=>v.row===q.row&&model.initial[q.row].indexOf(v.id)<index).reduce((a,v)=>a+smooth((t-v.start-180)/280),0);
 return {...q,status:'queued',x:525-(index-prior)*46,y,alpha:1};
})}
export function validate(){let frames=0;
 for(const f of Object.keys(fairnessNames))for(const o of Object.keys(orderingNames)){
  const m=build(f,o);if(new Set(m.turns.map(q=>q.id)).size!==10)throw Error('Lost request');
  if(f==='strict'&&m.turns.map(q=>q.id).join()!=[...requests].sort((a,b)=>compare(a,b,o)).map(q=>q.id).join())throw Error('Global comparator');
  if(f==='las'&&m.turns.map(q=>q.row).join()!=='1,1,1,2,2,0,0,0,0,0')throw Error('LAS score');
  let prev;for(let frame=0;frame<duration*60/1000;frame++){const t=frame*1000/60,s=state(t,m);frames++;
   for(let i=0;i<s.length;i++){const a=s[i];if(prev&&a.x<prev[i].x-.001)throw Error('Backwards');for(let j=i+1;j<s.length;j++)if(a.alpha&&s[j].alpha&&Math.hypot(a.x-s[j].x,a.y-s[j].y)<30)throw Error('Collision');}prev=s;
  }
 }
 return `${frames} frames: nine combinations, conserved IDs, comparator order, LAS scores, no collisions or backward travel`;
}
