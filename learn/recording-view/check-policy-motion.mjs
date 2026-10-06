// Validate the exact geometry consumed by the recording at every exported frame.
import {visualRequests,layout} from './policy-demo-motion.mjs';
import {validate,duration,state,requests} from './policy-demo-model.mjs';
const failures=[];let previous=new Map(),maxStep=0;
const frames=duration*60/1000;
for(let frame=0;frame<frames;frame++){
  const t=frame*1000/60,dots=visualRequests(t);
  const expected=state(t).dots.filter(q=>!['future','completed'].includes(q.status));
  if(dots.length!==expected.length||new Set(dots.map(q=>q.id)).size!==dots.length)failures.push({frame,kind:'visual conservation'});
  for(let i=0;i<dots.length;i++){
    const a=dots[i],prev=previous.get(a.id);
    if(!Number.isFinite(a.x+a.y+a.alpha))failures.push({frame,kind:'nonfinite',id:a.id});
    if(prev){
      const step=Math.hypot(a.x-prev.x,a.y-prev.y);maxStep=Math.max(maxStep,step);
      if(step>30)failures.push({frame,kind:'position jump',id:a.id,step});
      if(a.x<prev.x-.01)failures.push({frame,kind:'backward motion',id:a.id});
    }
    if(a.status==='queued'&&a.x>layout.gateX+.01)failures.push({frame,kind:'queued beyond check',id:a.id});
    if(a.status==='transit'&&a.x<layout.gateX-.01)failures.push({frame,kind:'admitted before check',id:a.id});
    for(const b of dots.slice(i+1))if(a.alpha>.1&&b.alpha>.1&&Math.hypot(a.x-b.x,a.y-b.y)<a.r+b.r)failures.push({frame,kind:'overlap',ids:[a.id,b.id]});
  }
  previous=new Map(dots.map(d=>[d.id,d]));
}
for(const q of requests.filter(r=>r.depart>=0&&Number.isFinite(r.depart))){
  const before=visualRequests(q.depart-.001).find(r=>r.id===q.id),after=visualRequests(q.depart).find(r=>r.id===q.id);
  if(Math.hypot(before.x-after.x,before.y-after.y)>.01)failures.push({kind:'admission discontinuity',id:q.id});
}
console.log(JSON.stringify({model:validate(),frames,maxStepPixels:maxStep,failures},null,2));
if(failures.length)process.exitCode=1;
