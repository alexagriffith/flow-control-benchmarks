#!/usr/bin/env python3
"""Check V1 control behavior and stable counters in a local rendered learner."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    for width in (1844, 375):
        page = browser.new_page(viewport={"width": width, "height": 1265}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto((ROOT / "learn/flow-control-interactive.html").as_uri() + "#page-14")
        page.evaluate("setAuto(false,true)")
        for theme in ("light", "dark"):
            page.evaluate("t=>document.documentElement.dataset.theme=t", theme)
            page.locator("#settingsBtn").focus()
            page.keyboard.press("Space")
            assert page.locator("#settingsPanel").is_visible()
            page.keyboard.press("Escape")
            assert not page.locator("#settingsPanel").is_visible()
            assert page.locator("#settingsBtn").evaluate("e=>document.activeElement===e")
            assert page.evaluate("state.page===14&&state.step===0")
            # Legacy descendants can override inherited visibility, so hide their roots with display.
            assert page.locator("#sysCanvas > :not(defs):not(#replayView)").evaluate_all(
                "es=>es.every(e=>getComputedStyle(e).display==='none')")
            page.evaluate("PG.load={p:100,s:100,b:100};for(let i=0;i<80;i++)playTick()")
            assert page.evaluate("document.body.classList.contains('halted')")
            assert page.locator("#canvasWrap").evaluate("e=>getComputedStyle(e).filter==='none'")
            initial = None
            for n in (9, 10, 99, 100, 9999, 100000):
                page.evaluate("n=>{PG.arrivals={p:n,s:n,b:n};PG.served={p:n,s:n,b:n};PG.rej=n;PG.ttl=n;PG.tick=n;renderReplay()}", n)
                boxes = page.locator("#pgStats th,#pgStats td,#pgStats .totals span").evaluate_all(
                    "es=>es.map(e=>{const r=e.getBoundingClientRect();return [r.x,r.y,r.width,r.height]})")
                assert initial is None or boxes == initial, (width, theme, n, "counter moved")
                initial = boxes
                assert page.locator("#pgStats th,#pgStats td,#pgStats .totals span").evaluate_all(
                    "es=>es.every(e=>e.scrollWidth<=e.clientWidth+1)")
            page.evaluate("PG.load={p:20,s:30,b:20};resetPlay()")
        assert not errors, errors
        page.close()
    browser.close()
print("PASS: Settings keyboard/focus, legacy replay layers, saturation colors, fixed counters at two widths and themes")

# The previous audit missed transient overlays and option-to-option state changes.
with sync_playwright() as pw:
    browser = pw.chromium.launch()
    page = browser.new_page(viewport={"width": 1844, "height": 1265}, reduced_motion="reduce")
    page.goto((ROOT / "learn/flow-control-interactive.html").as_uri())
    page.evaluate("setAuto(false,true);go(1,0)")
    assert page.evaluate("""(()=>{
      for(let i=0;i<=100;i++){
        burstWave(i/100,true);
        const waiting=[...$('#burstWaitCells').children].some(e=>+e.getAttribute('opacity')>0);
        const full=[...$('#burstSlots').children].every(e=>e.getAttribute('fill')==='var(--brand)');
        if(waiting&&!full)return false;
      }return true;
    })()"""), "Overflow shown with unused illustrated capacity"
    page.evaluate("go(2,0)")
    assert page.locator('#kneeThroughput,#kneeLatency,#kneeFlat,#kneeQueue').evaluate_all(
        "es=>es.every(e=>getComputedStyle(e).strokeDashoffset==='0px')"), "Reduced-motion chart hides delayed lines"
    assert page.evaluate("""(()=>{
      for(const id of ['kneeValueLabel','kneeLatencyLabel','kneeClimbLabel','kneeFlatLabel']){
        const r=document.getElementById(id).getBBox();
        for(const path of $('#kneeViz').querySelectorAll('path')){
          for(let d=0;d<=path.getTotalLength();d+=1){
            const p=path.getPointAtLength(d);
            if(p.x>r.x-2&&p.x<r.x+r.width+2&&p.y>r.y-2&&p.y<r.y+r.height+2)return false;
          }
        }
      }return true;
    })()"""), "Chart label intersects a plotted line"
    assert page.evaluate("(()=>{return ['#kneeClimbLabel','#kneeFlatLabel'].every(id=>$(id).getBBox().x>+$('#kneeSchematicThreshold').getAttribute('x1')+4)})()"), "Caption touches the threshold marker"
    page.evaluate("go(10,0);$('#lessonPanel').showPopover();$('#lessonDetails details').open=true")
    page.select_option("#policyChoice", "ceilings")
    assert page.locator("#g-bm10").evaluate("e=>e.classList.contains('lit')")
    assert page.locator("#g-meter").evaluate("e=>e.classList.contains('lit')")
    page.select_option("#policyChoice", "fairness")
    assert page.locator("#g-b0").evaluate("e=>e.classList.contains('lit')")
    assert not page.locator("#haltBanner").is_visible(), "Eligible state shows a waiting banner"
    assert page.locator("#haltBanner").inner_text()==""
    assert not page.locator("#evidence").is_visible(), "Stale ceiling evidence after selecting fairness"
    page.emulate_media(reduced_motion="no-preference")
    page.evaluate("setAuto(false,true);go(7,2)")
    assert page.locator('#fld-b100-1 circle[opacity="1"]').count()==0, "GC scene discards a queued request"
    assert page.locator("#fl-b100-1").evaluate("e=>e.style.opacity==='1'")
    page.wait_for_timeout(1750)
    assert page.locator("#fl-b100-1").evaluate("e=>e.style.opacity==='0'")
    browser.close()
print("PASS: burst occupancy, chart-label clearance, policy selection state and empty-flow cleanup")

# Replay cleanup must not repaint a guided lesson with playground queues or metrics.
with sync_playwright() as pw:
    browser=pw.chromium.launch()
    page=browser.new_page(reduced_motion='reduce')
    page.goto((ROOT/'learn/flow-control-interactive.html').as_uri())
    page.evaluate('setAuto(false,true)')
    assert page.evaluate("""()=>{
      go(14,0);PG.load={p:100,s:100,b:100};for(let i=0;i<60;i++)playTick();
      for(let p=0;p<14;p++)for(let s=0;s<PAGES[p].steps.length;s++){
        const step=PAGES[p].steps[s],run=step.run;let expected;
        const snapshot=()=>JSON.stringify({scene:currentScene,queues:CS.q,labels:[...document.querySelectorAll('[id^=fll-]')].map(e=>e.textContent)});
        step.run=function(){run.call(this);expected=snapshot();};
        go(p,s);step.run=run;if(expected!==snapshot())throw Error(`Replay repainted ${p}.${s}`);
      }
      go(8,3);return Math.abs(sceneMetrics(currentScene).pool-.85)<1e-9;
    }"""), 'guided scene changed after replay cleanup'
    # Wheel and keyboard inside supporting details cannot navigate the underlying lesson.
    page.click('#detailBtn')
    page.locator('#lessonPanel').evaluate("e=>e.dispatchEvent(new WheelEvent('wheel',{deltaY:100,bubbles:true}))")
    assert page.evaluate('state.page===8&&state.step===3')
    page.keyboard.press('Escape')
    assert not page.locator('#lessonPanel').is_visible()
    assert page.locator('#detailBtn').evaluate('e=>document.activeElement===e')
    browser.close()
print('PASS: replay-to-lesson isolation and detail-panel wheel/Escape/focus behavior')
