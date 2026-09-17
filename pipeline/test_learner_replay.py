"""Exercise the learner's actual replay script without a browser or benchmarks.

Requires Node.js, using only its standard library. DOM calls are presentation
stubs. The scene arithmetic and replay are extracted from the page itself;
these tests verify accounting and recovery, not layout or runtime fidelity.
"""
import pathlib
import shutil
import subprocess
import unittest


PAGE = pathlib.Path(__file__).resolve().parents[1] / "learn" / "flow-control-journey.html"


@unittest.skipUnless(shutil.which("node"), "Node.js is required for replay checks")
class LearnerReplayTests(unittest.TestCase):
    def check_replay(self, scenario):
        html = PAGE.read_text()
        script = html.split("/* =================== playground =================== */", 1)[1]
        script = script.split("/* =================== boot =================== */", 1)[0]
        metrics = html.split("/* scene metrics start */", 1)[1]
        metrics = metrics.split("/* scene metrics end */", 1)[0]
        harness = r"""
const assert = require('node:assert/strict');
const element = {addEventListener(){}, setAttribute(){}, classList:{toggle(){}}, innerHTML:''};
const $ = () => element, $$ = () => [];
const document = {body: element};
let meter = 0, halted = false;
element.classList.toggle = (_, value) => {halted = value;};
const setMeter = value => {meter = value;};
const setPods = () => {}, setFlow = () => {}, fly = () => {}, replayFly = () => {}, updateReplayView = () => {};
let latestScene;
function renderScene(scene){
  latestScene=scene;const metrics=sceneMetrics(scene);meter=metrics.pool;
  halted=!metrics.canDispatch;return metrics;
}
let nextTimer=1;const timers=new Map();
const setInterval = fn => {const id=nextTimer++;timers.set(id,fn);return id;};
const clearInterval = id => timers.delete(id);
let reducedMotion=false;const reduceMotion = () => reducedMotion;
"""
        checks = r"""
const sum = values => Object.values(values).reduce((a,b)=>a+b,0);
function conserved() {
  const outstanding = sum(PG.q) + PG.pods.reduce((n,pod)=>n+pod.length,0);
  assert.equal(sum(PG.arrivals), sum(PG.served)+PG.rej+PG.ttl+outstanding);
  assert(PG.pods.every(pod=>pod.length<=REPLAY.runningPerPod+REPLAY.waitingPerPod));
  assert(Object.values(PG.q).every(n=>n>=0&&n<=REPLAY.queueLimit));
}
function ticks(n) { for(let i=0;i<n;i++){playTick();conserved();} }
function idle() { PG.load={p:0,s:0,b:0};PG.burst=0; }
"""
        result = subprocess.run(["node", "-"], input=harness+metrics+script+checks+scenario,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_zero_load_has_no_phantom_arrivals(self):
        self.check_replay("""
idle();startPlay();ticks(300);
assert.equal(sum(PG.arrivals),0);assert.equal(sum(PG.served),0);assert.equal(meter,0);
""")

    def test_load_changes_arrivals_continuously(self):
        self.check_replay("""
idle();startPlay();PG.load.p=17;ticks(34);assert.equal(PG.arrivals.p,17);
PG.load.p=34;ticks(34);assert.equal(PG.arrivals.p,51);
idle();const before=sum(PG.arrivals);ticks(100);assert.equal(sum(PG.arrivals),before);
""")

    def test_overload_halts_then_recovers_at_zero(self):
        self.check_replay("""
PG.load={p:100,s:100,b:100};startPlay();let sawHalt=false;
for(let i=0;i<300;i++){ticks(1);sawHalt ||= halted;}
assert(sawHalt);assert(PG.rej>0);assert(PG.ttl>0);
const before=sum(PG.served);idle();ticks(300);
assert(sum(PG.served)>before);assert.equal(sum(PG.q),0);
assert.equal(PG.pods.flat().length,0);assert.equal(meter,0);assert.equal(halted,false);
""")

    def test_mode_changes_keep_accounting_and_backend_capacity(self):
        self.check_replay("""
PG.load={p:100,s:100,b:100};startPlay();ticks(60);
const before=sum(PG.q);assert(before>0);PG.fc=false;ticks(60);
assert(PG.rej>0);PG.fc=true;ticks(60);idle();ticks(300);
assert.equal(sum(PG.q),0);assert.equal(PG.pods.flat().length,0);
""")

    def test_priority_selects_waiting_work_and_ttl_uses_age(self):
        self.check_replay("""
idle();startPlay();PG.queued={p:[PG.tick],s:[PG.tick],b:[PG.tick]};
PG.arrivals={p:1,s:1,b:1};ticks(1);
assert.equal(PG.pods.flat()[0].tier,'p');assert.equal(PG.q.s,1);assert.equal(PG.q.b,1);
// Hold backend capacity with long illustrative jobs; a shallow queue still expires by age.
idle();startPlay();PG.pods=Array.from({length:2},()=>Array.from({length:4},()=>({tier:'p',remaining:1000})));
PG.queued.b=[PG.tick];PG.arrivals={p:8,s:0,b:1};
ticks(REPLAY.ttlTicks-1);assert.equal(PG.ttl,0);assert.equal(PG.q.b,1);
ticks(1);assert.equal(PG.ttl,1);assert.equal(PG.q.b,0);
""")

    def test_offer_trace_is_repeatable_and_independent_of_flow_control(self):
        self.check_replay("""
function run(fc){
  PG.fc=fc;PG.load={p:34,s:68,b:100};resetPlay();const trace=[];
  for(let i=0;i<200;i++){
    if(i===20)PG.burst=24;
    if(i===60)PG.load={p:17,s:100,b:34};
    ticks(1);trace.push(PG.lastOffered);
  }
  return {trace,arrivals:{...PG.arrivals},served:{...PG.served}};
}
const off=run(false),again=run(false),on=run(true);
assert.deepEqual(off,again);
assert.deepEqual(off.trace,on.trace);assert.deepEqual(off.arrivals,on.arrivals);
assert.notDeepEqual(off.served,on.served);
""")

    def test_equal_load_off_has_no_systematic_tier_preference(self):
        self.check_replay("""
const totals={p:0,s:0,b:0};
for(let seed=1;seed<=24;seed++){
  PG.seed=seed;PG.fc=false;PG.load={p:100,s:100,b:100};resetPlay();ticks(1000);
  assert.equal(PG.arrivals.p,PG.arrivals.s);assert.equal(PG.arrivals.s,PG.arrivals.b);
  for(const tier of ['p','s','b'])totals[tier]+=PG.served[tier];
}
const counts=Object.values(totals),mean=sum(totals)/3;
assert(Math.max(...counts)-Math.min(...counts)<mean*.05,JSON.stringify(totals));
""")

    def test_off_trace_and_outcomes_are_invariant_under_lane_relabeling(self):
        self.check_replay("""
function run(lanes){
  PG.seed=42;PG.fc=false;PG.lanes=lanes;
  PG.load=Object.fromEntries(lanes.map((tier,i)=>[tier,[100,68,34][i]]));resetPlay();
  const trace=[];
  for(let i=0;i<1000;i++){
    if(i===50){PG.burstLane=1;PG.burst=24;}
    ticks(1);trace.push(PG.lastOffered.map(t=>lanes.indexOf(t)));
  }
  return {trace,arrivals:lanes.map(t=>PG.arrivals[t]),served:lanes.map(t=>PG.served[t])};
}
const original=run(['p','s','b']);
for(const lanes of [['p','b','s'],['s','p','b'],['s','b','p'],['b','p','s'],['b','s','p']]){
  assert.deepEqual(original,run(lanes));
}
""")

    def test_pause_resume_reset_and_mode_comparison(self):
        self.check_replay("""
PG.load={p:100,s:100,b:100};startPlay();
function clockTick(){for(const fn of [...timers.values()])fn();conserved();}
clockTick();assert.equal(PG.tick,1);setReplayRunning(false);
const frozen=JSON.stringify(PG);clockTick();assert.equal(JSON.stringify(PG),frozen);
setReplayRunning(true);clockTick();assert.equal(PG.tick,2);
resetPlay(true);assert.equal(PG.tick,0);assert.equal(sum(PG.arrivals),0);
clockTick();const offered=PG.lastOffered.slice();
setReplayMode(false);assert.equal(PG.tick,0);assert.equal(PG.pods.flat().length,0);
assert.equal(timers.size,1);clockTick();assert.deepEqual(PG.lastOffered,offered);
setReplayRunning(false);resetPlay(false);assert.equal(timers.size,0);assert.equal(PG.tick,0);
reducedMotion=true;startPlay();assert.equal(timers.size,0);assert.equal(PG.tick,0);
playTick();assert.equal(PG.tick,1);assert.equal(timers.size,0);
""")

    def test_scene_uses_raw_backend_waiting_and_marks_gate_bypass(self):
        self.check_replay("""
idle();resetPlay();PG.fc=false;
PG.pods=Array.from({length:2},()=>Array.from({length:8},()=>({tier:'p',remaining:1000})));
renderReplay();assert.equal(latestScene.endpoints[0].waiting,4);
assert.equal(latestScene.endpoints[0].kv,1);assert.equal(meter,1.25);assert.equal(halted,false);
PG.fc=true;renderReplay();assert.equal(halted,true);
""")

    def test_reduced_motion_starts_paused_and_step_does_not_start_clock(self):
        self.check_replay("""
reducedMotion=true;PG.load={p:34,s:34,b:34};startPlay();
assert.equal(PG.tick,0);assert.equal(timers.size,0);
ticks(1);assert.equal(PG.tick,1);assert.equal(timers.size,0);
setReplayMode(false);assert.equal(PG.tick,0);assert.equal(timers.size,0);
setReplayRunning(true);assert.equal(timers.size,1);
setReplayRunning(false);assert.equal(timers.size,0);
""")


if __name__ == "__main__":
    unittest.main()
