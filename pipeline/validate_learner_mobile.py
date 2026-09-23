#!/usr/bin/env python3
"""Check phone navigation, config controls, and replay accounting at both layouts."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--engine', choices=('chromium', 'webkit'), default='chromium')
args = parser.parse_args()
views = 0
with sync_playwright() as pw:
    browser = getattr(pw, args.engine).launch()
    page = browser.new_page(viewport={'width': 390, 'height': 844}, reduced_motion='reduce')
    errors = []
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto((ROOT / 'learn/flow-control-interactive.html').as_uri())
    assert page.evaluate('!auto'), 'Phone should open without advancing lessons'
    for width, height in ((320, 740), (375, 812), (430, 932), (700, 900), (844, 390), (1844, 1265)):
        page.set_viewport_size({'width': width, 'height': height})
        page.wait_for_timeout(60)
        for theme in ('light', 'dark'):
            page.evaluate('t=>document.documentElement.dataset.theme=t', theme)
            for chapter, count in enumerate(page.evaluate('PAGES.map(p=>p.steps.length)')):
                for step in range(count):
                    page.evaluate('([p,s])=>go(p,s)', [chapter, step])
                    page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                    result = page.evaluate('''()=>{
                      const card=$('#card').getBoundingClientRect(),canvas=$('#canvasWrap').getBoundingClientRect(),n=$('#narr');
                      return {overflow:document.documentElement.scrollWidth>innerWidth+1,
                        narration:n.scrollHeight>n.clientHeight+1||n.scrollWidth>n.clientWidth+1,
                        navFirst:innerWidth>700||canvas.height===0||card.bottom<=canvas.top,
                        nextHeight:$('#nextBtn').getBoundingClientRect().height};
                    }''')
                    assert not result['overflow'] and not result['narration'] and result['navFirst'], (width, theme, chapter, step, result)
                    if width <= 700:
                        assert result['nextHeight'] >= 44
                    views += 1
            if width <= 700:
                assert page.locator('#chapterSelect option').count() == 15
                page.select_option('#chapterSelect', '6')
                assert page.evaluate('state.page===6 && state.step===0')
                page.click('#diagramZoom')
                assert page.evaluate("document.body.classList.contains('diagram-fit')")
                page.click('#diagramZoom')
                assert not page.evaluate("document.body.classList.contains('diagram-fit')")
            page.evaluate('go(14,0)')
            for key in ('detector', 'ceiling'):
                button = page.locator(f'[data-config-tip="{key}"]')
                button.focus()
                page.keyboard.press('Enter')
                assert page.locator('#configTip').evaluate("e=>e.matches(':popover-open')")
                assert len(page.locator('#configTipText').inner_text()) > 30
                bounds = page.locator('#configTip').bounding_box()
                assert bounds['x'] >= 0 and bounds['x'] + bounds['width'] <= width + 1, bounds
                page.keyboard.press('Escape')
                assert not page.locator('#configTip').evaluate("e=>e.matches(':popover-open')")
    # Every model choice is applied, resets the same traffic, and conserves requests.
    page.set_viewport_size({'width':390,'height':844})
    page.evaluate('go(14,0);PG.load={p:51,s:30,b:20};playTick();settleReplayVisual()')
    page.click('#detailBtn')
    for selector, field, values in (('modelCeiling','ceiling',('.8','1','1.2')),('modelKV','kvLimit',('.6','.8','1'))):
        for value in values:
            page.select_option('#'+selector, str(float(value)).removesuffix('.0'))
            result=page.evaluate('''key=>({value:REPLAY[key],tick:PG.tick,load:PG.load,seed:PG.seed})''',field)
            assert result=={'value':float(value),'tick':0,'load':{'p':51,'s':30,'b':20},'seed':42},result
    assert page.locator('#configCeilingValue').inner_text() == '1.20'
    page.locator('[aria-label="Close details"]').click()
    checks=page.evaluate('''()=>{
      const reports=[];
      for(const fc of [true,false])for(const ceiling of [.8,1,1.2])for(const waitingLimit of [4])for(const kvLimit of [.6,.8,1]){
        Object.assign(REPLAY,{ceiling,waitingLimit,kvLimit});PG.fc=fc;resetPlay(false);PG.load={p:70,s:100,b:60};
        for(let i=0;i<160;i++){
          playTick();const s=replayVisualSnapshot();
          if(s.offered!==s.completed+s.rejected+s.expired+s.routerWaiting+s.engineRunning+s.engineWaiting+s.inTransit)throw Error('Requests lost');
        }
        reports.push({fc,ceiling,waitingLimit,kvLimit,completed:Object.values(PG.served).reduce((a,b)=>a+b,0)});
      }
      return reports;
    }''')
    assert len(checks)==18
    # A less restrictive ceiling must affect capacity in this illustrative model.
    protected=[r for r in checks if r['fc'] and r['waitingLimit']==4 and r['kvLimit']==.8]
    assert protected[0]['completed'] < protected[-1]['completed'],protected
    cache_cases=[r for r in checks if r['fc'] and r['ceiling']==1]
    assert cache_cases[0]['completed'] < cache_cases[-1]['completed'],cache_cases
    before=page.evaluate('replayVisualSnapshot()')
    page.set_viewport_size({'width':1844,'height':1265});page.wait_for_timeout(80)
    assert page.evaluate('replayVisualSnapshot()')==before
    page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(80)
    assert page.evaluate('replayVisualSnapshot()')==before
    assert page.locator('#replayView').count()==1
    # The same accounting holds during actual motion and when rotating mid-transfer.
    page.emulate_media(reduced_motion='no-preference')
    page.evaluate('PG.fc=true;resetPlay(false);PG.load={p:70,s:100,b:60}')
    for tick in range(8):
        page.evaluate('playTick()')
        for delay in (50,100,150,220):
            page.wait_for_timeout(delay)
            snapshot=page.evaluate('replayVisualSnapshot()')
            assert snapshot['offered']==sum(snapshot[k] for k in ('completed','rejected','expired','routerWaiting','engineRunning','engineWaiting','inTransit')),snapshot
        if tick==3:
            page.evaluate('playTick()');page.wait_for_timeout(150)
            offered=page.evaluate('replayVisualSnapshot().offered')
            page.set_viewport_size({'width':844,'height':390});page.wait_for_timeout(80)
            assert page.evaluate('replayVisualSnapshot().offered')==offered
            page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(80)
    assert not errors,errors
    browser.close()
print(f'PASS ({args.engine}): {views} responsive views; mobile navigation and tips; 18 configuration/mode combinations; request conservation and resize continuity')
