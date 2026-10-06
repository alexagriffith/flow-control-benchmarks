// Illustrative request-count detector: pressure = admitted requests / 5.
// Ceilings are example values, not GPU utilization or production tuning advice.
export const duration=14000;
export const bands=[
  {key:'premium',color:'#7181ff',ceiling:1,y:206},
  {key:'standard',color:'#e0a63a',ceiling:.8,y:358},
  {key:'batch',color:'#2fb8b8',ceiling:.6,y:510}
];
// Begin at the batch ceiling. Premium arrives as a new burst, fills the
// remaining headroom, and its third request waits for an actual completion.
export const requests=[
  {id:'B0',band:'batch',slot:0,depart:-1,arrive:-1,complete:9500},
  {id:'B1',band:'batch',slot:1,depart:-1,arrive:-1,complete:11400},
  {id:'B2',band:'batch',slot:2,depart:-1,arrive:-1,complete:Infinity},
  {id:'P1',band:'premium',queue:0,enter:1500,queued:2200,slot:3,depart:3000,arrive:3800,complete:6800},
  {id:'P2',band:'premium',queue:1,enter:1600,queued:2300,slot:4,depart:4000,arrive:4800,complete:8200},
  {id:'P3',band:'premium',queue:2,enter:1700,queued:2400,slot:3,depart:7100,arrive:7900,complete:10800},
  {id:'S1',band:'standard',queue:0,enter:4200,queued:4900,slot:0,depart:9800,arrive:10600,complete:Infinity},
  {id:'B3',band:'batch',queue:0,slot:1,depart:11800,arrive:12600,complete:Infinity},
  {id:'B4',band:'batch',queue:1,slot:null,depart:Infinity,arrive:Infinity,complete:Infinity}
];
export function state(t){
  const dots=requests.map(r=>({...r,status:t<(r.enter??-Infinity)?'future':t<(r.queued??-Infinity)?'arriving':t<r.depart?'queued':t<r.arrive?'transit':t<r.complete?'running':'completed'}));
  const admitted=dots.filter(r=>r.status==='transit'||r.status==='running').length;
  return {dots,admitted,pressure:admitted/5,completed:dots.filter(r=>r.status==='completed').length};
}
export function validate(){
  for(const r of requests.filter(r=>r.depart>=0&&Number.isFinite(r.depart))){
    const before=state(r.depart-.01),band=bands.find(b=>b.key===r.band);
    if(before.pressure>=band.ceiling)throw Error(`Ineligible dispatch: ${r.id}`);
    if(before.dots.some(q=>q.status==='queued'&&bands.findIndex(b=>b.key===q.band)<bands.indexOf(band)))throw Error(`Higher priority waiting: ${r.id}`);
    if(before.dots.some(q=>['running','transit'].includes(q.status)&&q.slot===r.slot))throw Error(`Occupied destination: ${r.id}`);
  }
  for(let t=0;t<=duration;t+=10){
    const s=state(t),active=s.dots.filter(r=>['transit','running'].includes(r.status));
    if(s.dots.length!==9||new Set(s.dots.map(r=>r.id)).size!==9)throw Error('Request conservation');
    if(new Set(active.map(r=>r.slot)).size!==active.length||s.admitted>5)throw Error('Duplicate occupancy');
  }
  for(const [t,p] of [[0,.6],[2500,.6],[3800,.8],[4800,1],[6900,.8],[8000,1],[9000,.8],[9600,.6],[11000,.6],[11600,.4],[13000,.6]]){
    if(state(t).pressure!==p)throw Error(`Unexpected pressure at ${t}`);
  }
  if(state(6000).dots.find(q=>q.id==='P3').status!=='queued')throw Error('Premium must wait at full capacity');
  if(state(2500).dots.filter(q=>q.band==='premium'&&q.status==='queued').length!==3)throw Error('Premium burst missing');
  return '9 requests tracked; burst arrival, priority, strict ceilings, full-pool waiting and slot reuse verified';
}
