// Same arrivals, eligible priority band, two flow-selection policies, FCFS in each queue.
export const phaseDuration=10800,duration=21600,counts=[5,3,2],names=['A','B','C'];
export const smooth=x=>{x=Math.max(0,Math.min(1,x));return x*x*(3-2*x)};
export const rowY=r=>283+r*125;
export const requests=counts.flatMap((n,row)=>Array.from({length:n},(_,index)=>({id:counts.slice(0,row).reduce((a,b)=>a+b,0)+index+1,row,index})));
function order(roundRobin){const left=[...counts],out=[];let cursor=0;while(left.some(Boolean)){if(roundRobin){while(!left[cursor])cursor=(cursor+1)%3}else cursor=left.findIndex(Boolean);out.push(requests.find(q=>q.row===cursor&&q.index===counts[cursor]-left[cursor]));left[cursor]--;if(roundRobin)cursor=(cursor+1)%3}return out.map((q,i)=>({...q,start:2600+i*700}))}
export const orders=[order(true),order(false)];
export function scene(t){return {phase:Math.min(1,Math.floor(t/phaseDuration)),time:t%phaseDuration}}
export function state(t){const {phase,time}=scene(t),turns=orders[phase];return requests.map(q=>{
 const turn=turns.find(v=>v.id===q.id),u=(time-turn.start)/620,y=rowY(q.row),arrival=200+(q.id-1)*110;
 if(time<arrival)return {...q,status:'pending',x:-30,y,alpha:0};
 if(u>=1)return {...q,status:'dispatched',x:1040,y:408,alpha:0};
 if(u>=0){const x=525+515*smooth(u),bend=smooth((x-600)/180);return {...q,status:'dispatch',x,y:y+(408-y)*bend,alpha:1}}
 const prior=turns.filter(v=>v.row===q.row&&v.index<q.index).reduce((a,v)=>a+smooth((time-v.start-180)/280),0);
 const target=525-(q.index-prior)*46;
 return {...q,status:'queued',x:-30+(target+30)*smooth((time-arrival)/600),y,alpha:1};
})}
export function validate(){for(let t=0;t<duration;t+=1000/60){const q=state(t);if(q.length!==10||new Set(q.map(x=>x.id)).size!==10)throw Error('Request identity lost');for(let i=0;i<q.length;i++)for(let j=i+1;j<q.length;j++)if(q[i].alpha&&q[j].alpha&&Math.hypot(q[i].x-q[j].x,q[i].y-q[j].y)<30)throw Error(`Collision at ${t}: ${q[i].id}/${q[j].id}`)}
 if(orders[0].map(v=>v.id).join(',')!=='1,6,9,2,7,10,3,8,4,5'||orders[1].map(v=>v.id).join(',')!=='1,2,3,4,5,6,7,8,9,10')throw Error('Policy sequence');return '1296frames: same10identities/arrivals, correct round-robin/global FCFS order, no collisions'}
