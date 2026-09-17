#!/usr/bin/env python3
"""Render the learner's scenes/options; local files only, never benchmark traffic."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit, unquote
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
OPTIONS=[(8,0,'detectorChoice',['utilization','requests','tokens','hybrid']),
         (8,3,'bandChoice',['high','low']),
         (9,3,'executionChoice',['queued','dispatch','engine','complete','evict']),
         (10,0,'policyChoice',['fairness','fcfs','edf','slo','ceilings'])]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,default=Path('/tmp/learner-revision-20260916/rendered'));args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=True)
    records=[];errors=[];links=set();names=['learn/flow-control-journey.html','learn/flow-control.html']
    hashes={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names}
    with sync_playwright() as pw:
        browser=pw.chromium.launch()
        for width in (1440,375):
            page=browser.new_page(viewport={'width':width,'height':1000},reduced_motion='reduce')
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto((ROOT/names[0]).as_uri());page.evaluate('setAuto(false,true)')
            def capture(label):
                page.wait_for_timeout(50)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),label+' page overflow'
                assert page.evaluate('document.getAnimations().every(a=>a.playState!=="running")'),label+' reduced motion'
                assert page.evaluate('''[...document.querySelectorAll('#lessonDetails,#evidence.show,#playCtl.show')].every(e=>e.scrollWidth<=e.clientWidth+1)'''),label+' control clipping'
                # Every scene uses the same computed values for the visible meter and gate.
                assert page.evaluate('''(()=>{const m=sceneMetrics(currentScene);return $('#meterVal').textContent===m.pool.toFixed(3)&&document.body.classList.contains('halted')===!m.canDispatch})()'''),label+' arithmetic/display mismatch'
                for link in page.locator('a[href]').evaluate_all('(es)=>es.map(e=>e.href)'):links.add(link)
                path=args.output/f'{width}-{label}.png';page.screenshot(path=str(path),full_page=True)
                records.append({'width':width,'view':label,'screenshot':str(path)})
                if width==375 and (label.startswith(('scene-08','scene-09','detectorChoice','executionChoice','bandChoice'))):
                    page.locator('#diagramViewport').evaluate('(e)=>e.scrollLeft=e.scrollWidth')
                    path=args.output/f'{width}-{label}-right.png';page.screenshot(path=str(path),full_page=True)
                    records.append({'width':width,'view':label+'-right','screenshot':str(path)})
                    page.locator('#diagramViewport').evaluate('(e)=>e.scrollLeft=0')
            for chapter,count in enumerate(page.evaluate('PAGES.map(p=>p.steps.length)')):
                for step in range(count):
                    page.evaluate(f'go({chapter},{step})');capture(f'scene-{chapter:02}-{step}')
                    if page.locator('#lessonDetails details').count():
                        page.locator('#lessonDetails details').evaluate_all('(es)=>es.forEach(e=>e.open=true)');capture(f'details-{chapter:02}-{step}')
            for chapter,step,control,values in OPTIONS:
                page.evaluate(f'go({chapter},{step})');page.locator('#lessonDetails details').evaluate_all('(es)=>es.forEach(e=>e.open=true)')
                for value in values:
                    page.select_option('#'+control,value);capture(f'{control}-{value}')
                    if control=='executionChoice' and value=='evict':
                        assert page.evaluate("!sceneMetrics(currentScene).canDispatch&&$('#g-gw').classList.contains('lit')")
                        assert page.locator('#fx path').count()==2
                    if control=='policyChoice' and value!='ceilings':
                        assert page.locator('#fld-b100-0 circle[opacity="1"]').count()==0
                    if control=='bandChoice':
                        assert page.locator('#fl-b0-0').evaluate('(e)=>e.style.opacity==="0"')
                    if control=='detectorChoice':
                        page.check('#topologyChoice');capture(f'{control}-{value}-pd');page.uncheck('#topologyChoice')
            # Keyboard summary and selector keep their native semantics.
            page.evaluate('go(8,0)');summary=page.locator('#lessonDetails summary').first;summary.focus();page.keyboard.press('Space');assert page.evaluate('state.page===8&&state.step===0')
            page.locator('#detectorChoice').focus();page.keyboard.press('ArrowDown');assert page.evaluate('state.page===8&&state.step===0')
            page.evaluate('go(14,0)');assert page.evaluate('PG.timer===null');page.locator('#pgStep').click();assert page.evaluate('PG.tick===1')
            page.locator('#pgReset').click();assert page.evaluate('PG.tick===0');page.locator('#pgPause').click();page.wait_for_timeout(550);assert page.evaluate('PG.tick>0');page.locator('#pgPause').click();tick=page.evaluate('PG.tick');page.wait_for_timeout(350);assert page.evaluate('PG.tick')==tick
            page.locator('#pgFc [data-f="0"]').click();assert page.evaluate('PG.tick===0&&!PG.fc');page.evaluate('stopPlay()')
            assert not errors,errors
            page.goto((ROOT/names[1]).as_uri());page.locator('#current-reference details').evaluate_all('(es)=>es.forEach(e=>e.open=true)')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1');page.screenshot(path=str(args.output/f'{width}-companion.png'),full_page=True)
            for link in page.locator('a[href]').evaluate_all('(es)=>es.map(e=>e.href)'):links.add(link)
            page.close()
        # Normal motion: sample every step during and after transitions, no runtime errors.
        page=browser.new_page(viewport={'width':1440,'height':1000},reduced_motion='no-preference');page.on('pageerror',lambda error:errors.append(str(error)));page.goto((ROOT/names[0]).as_uri());page.evaluate('setAuto(false,true)')
        for chapter,count in enumerate(page.evaluate('PAGES.map(p=>p.steps.length)')):
            for step in range(count):
                page.evaluate(f'go({chapter},{step})');page.wait_for_timeout(400)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        page.evaluate('go(9,3)');page.wait_for_timeout(3000);assert page.locator('#executionChoice').input_value()=='complete'
        page.screenshot(path=str(args.output/'motion-completion.png'));page.evaluate('go(14,0)');assert page.evaluate('PG.timer!==null');page.locator('#pgPause').click();page.evaluate('stopPlay()');assert not errors,errors
        browser.close()
    for link in links:
        parsed=urlsplit(link)
        if parsed.scheme=='file':
            path=Path(unquote(parsed.path));assert path.exists(),f'Missing local source: {link}'
    assert hashes=={n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in names},'Source changed during render'
    report={'hashes':hashes,'views':records,'links':sorted(links),'errors':errors,'checks':['all scenes and optional modes at 1440/375','expanded details','arithmetic/display/gate agreement','local source links','keyboard controls','replay pause/resume/step/reset/mode','reduced motion','normal motion every scene']}
    (args.output/'report.json').write_text(json.dumps(report,indent=2));print(f'PASS: {len(records)} rendered scene/detail/option views, 34 normal-motion steps, companion at two widths; hashes in {args.output}/report.json')

if __name__=='__main__':main()
