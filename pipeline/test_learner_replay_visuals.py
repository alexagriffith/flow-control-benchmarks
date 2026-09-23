"""Browser regression for exclusive request occupancy in the illustrative replay.

No benchmark runs or network requests. Requires Node.js and local Playwright.
Run: python3 -m unittest pipeline.test_learner_replay_visuals
"""
import json
import pathlib
import shutil
import subprocess
import unittest

PAGE = pathlib.Path(__file__).resolve().parents[1] / "learn" / "flow-control-interactive.html"


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ReplayVisualAccountingTests(unittest.TestCase):
    def test_event_phases_conserve_requests_and_reset_cancels_old_events(self):
        script = r"""
const assert=require('node:assert/strict');
const {chromium}=require(require.resolve('playwright',{paths:[process.cwd(),require('node:os').homedir()]}));
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
 const page=await browser.newPage({viewport:{width:1280,height:900},reducedMotion:'no-preference'});
 const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(PAGE_URL);
 await page.evaluate(()=>{
  setAuto(false,true);go(PAGES.length-1,0);resetPlay(false);
  window.visualChecks=[];
  const original=renderReplay;
  renderReplay=function(){
   original();const s=replayVisualSnapshot();
   const expected=s.completed+s.rejected+s.expired+s.routerWaiting+s.engineRunning+s.engineWaiting+s.inTransit;
   if(s.offered!==expected)throw Error('request conservation: '+JSON.stringify(s));
   const queueDots=document.querySelectorAll('[id^="rp-queue-"] circle').length;
   const engineDots=document.querySelectorAll('[id^="rp-jobs-"] circle').length;
   const bundles=[...document.querySelectorAll('#replayFx .request-dot')];
   const transitDots=bundles.reduce((n,b)=>n+Number(b.dataset.count),0);
   if(new Set(bundles.map(b=>b.dataset.route)).size!==bundles.length)throw Error('overlapping duplicate route bundles');
   for(const bundle of bundles){
    const count=Number(bundle.dataset.count);
    if(!Number.isInteger(count)||count<1)throw Error('invalid bundle count');
    if(count>1&&bundle.querySelector('text')?.textContent!=='×'+count)throw Error('bundle lacks visible count');
   }
   if(queueDots!==s.routerWaiting)throw Error('queue dots disagree: '+JSON.stringify({queueDots,s}));
   if(engineDots!==s.engineRunning+s.engineWaiting)throw Error('engine dots disagree: '+JSON.stringify({engineDots,s}));
   if(transitDots!==s.inTransit)throw Error('moving dots disagree: '+JSON.stringify({transitDots,s}));
   if(Object.values(s).some(v=>typeof v==='number'&&v<0))throw Error('negative count');
   window.visualChecks.push(s);
  };
 });
 // One fresh request must arrive, then dispatch, then occupy one engine slot.
 for(const fc of [true,false]){
  await page.evaluate(fc=>{PG.fc=fc;PG.load={p:34,s:0,b:0};resetPlay(false);playTick();},fc);
  await page.waitForTimeout(await page.evaluate(()=>replayWallTime(490)));
  const state=await page.evaluate(()=>replayVisualSnapshot());
  assert.equal(state.offered,1);assert.equal(state.inTransit,0);assert.equal(state.engineRunning,1);
  // Warm the deterministic model into overload, then let actual visual phases run.
  await page.evaluate(()=>{PG.load={p:100,s:100,b:100};for(let i=0;i<160;i++){playTick();settleReplayVisual();}playTick();});
  await page.waitForTimeout(await page.evaluate(()=>replayWallTime(490)));
  const overloaded=await page.evaluate(()=>replayVisualSnapshot());
  assert(overloaded.rejected>0);if(fc)assert(overloaded.expired>0);
  await page.evaluate(()=>{PG.load={p:0,s:0,b:0};PG.burst=0;for(let i=0;i<300;i++){playTick();settleReplayVisual();}});
  const drained=await page.evaluate(()=>replayVisualSnapshot());
  assert.equal(drained.routerWaiting+drained.engineRunning+drained.engineWaiting+drained.inTransit,0);
 }
 // Several arrivals on one route are one visibly counted bundle. Off-mode
 // dispatch includes the queue-width bypass, so the request never teleports.
 await page.evaluate(()=>{PG.fc=false;PG.load={p:100,s:0,b:0};resetPlay(false);playTick();});
 const bundle=await page.locator('#replayFx .request-dot').evaluateAll(es=>es.map(e=>({count:Number(e.dataset.count),label:e.querySelector('text')?.textContent})));
 assert.deepEqual(bundle,[{count:2,label:'×2'}]);
 await page.waitForTimeout(await page.evaluate(()=>replayWallTime(175)));
 const routes=await page.locator('#replayFx .request-dot').evaluateAll(es=>es.map(e=>e.dataset.route));
 assert(routes.length>0);assert(routes.every(r=>r.startsWith('rp-bypass-p ')));
 await page.waitForTimeout(await page.evaluate(()=>replayWallTime(320)));
 // No phantom route markers at zero demand.
 await page.evaluate(()=>{PG.load={p:0,s:0,b:0};resetPlay(false);playTick();});
 await page.waitForTimeout(await page.evaluate(()=>replayWallTime(490)));assert.equal((await page.evaluate(()=>replayVisualSnapshot())).offered,0);
 // Cancel while arrival movement is active. No stale callback can repopulate reset state.
 await page.evaluate(()=>{PG.fc=true;PG.load={p:34,s:0,b:0};resetPlay(false);playTick();});
 await page.waitForTimeout(40);
 await page.evaluate(()=>{PG.load={p:0,s:0,b:0};resetPlay(false);});
 await page.waitForTimeout(await page.evaluate(()=>replayWallTime(600)));
 const reset=await page.evaluate(()=>replayVisualSnapshot());assert.equal(reset.offered,0);assert.equal(reset.inTransit,0);
 assert.equal(await page.locator('#replayFx .request-dot').count(),0);
 // Manual steps settle the prior batch; pause also leaves a conserved stable frame.
 await page.evaluate(()=>{PG.load={p:34,s:0,b:0};playTick();playTick();setReplayRunning(false);});
 const paused=await page.evaluate(()=>replayVisualSnapshot());await page.waitForTimeout(await page.evaluate(()=>replayWallTime(600)));
 assert.deepEqual(await page.evaluate(()=>replayVisualSnapshot()),paused);
 // Reduced motion has the same accounting, with no animated token replicas.
 await page.emulateMedia({reducedMotion:'reduce'});
 await page.evaluate(()=>{resetPlay(false);playTick();});
 assert.equal((await page.evaluate(()=>replayVisualSnapshot())).inTransit,0);
 const checks=await page.evaluate(()=>window.visualChecks);
 assert(checks.some(s=>s.phase==='arrival'));assert(checks.some(s=>s.phase==='dispatch'));
 assert(checks.some(s=>s.phase==='complete'));assert(checks.length>100);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({checkedBoundaries:checks.length}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1)});
"""
        script = "const PAGE_URL=" + json.dumps(PAGE.as_uri()) + ";\n" + script
        result = subprocess.run(["node", "-"], input=script, text=True,
                                capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
