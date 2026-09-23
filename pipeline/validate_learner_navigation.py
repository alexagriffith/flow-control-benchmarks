#!/usr/bin/env python3
"""Check stable navigation geometry and shared arrow styling across learner states."""
import argparse
from pathlib import Path
from playwright.sync_api import sync_playwright
from validate_learner_browser import OPTIONS
ROOT=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser()
parser.add_argument('--engine',choices=('chromium','webkit'),default='chromium')
args=parser.parse_args()
checked=0
with sync_playwright() as pw:
    browser=getattr(pw,args.engine).launch()
    for width in (1844,1440,375):
        page=browser.new_page(viewport={'width':width,'height':1265},reduced_motion='reduce')
        page.goto((ROOT/'learn/flow-control-interactive.html').as_uri())
        page.evaluate('setAuto(false,true)')
        for theme in ('light','dark'):
            page.evaluate('t=>document.documentElement.dataset.theme=t',theme)
            baseline=None
            def check(label):
                global checked
                page.evaluate('new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
                result=page.evaluate('''()=>{
                  const card=$('#card').getBoundingClientRect(),next=$('#nextBtn').getBoundingClientRect();
                  const narrative=$('#narr'),title=$('#pTitle');
                  const marker=$('#arr');
                  const expected=getComputedStyle(document.documentElement).getPropertyValue('--txt3').trim();
                  const probe=document.createElement('span');probe.style.color=expected;document.body.appendChild(probe);
                  const neutral=getComputedStyle(probe).color;probe.remove();
                  return {
                    bounds:[card.width,card.height,next.x-card.x,next.y-card.y,next.width,next.height].map(x=>Math.round(x*100)/100),
                    proseFits:narrative.scrollHeight<=narrative.clientHeight+1 && narrative.scrollWidth<=narrative.clientWidth+1,
                    titleFits:title.scrollHeight<=title.clientHeight+1,
                    neutral:[...document.querySelectorAll('.connector')].every(e=>getComputedStyle(e).stroke===neutral),
                    marker:marker.getAttribute('markerUnits')==='userSpaceOnUse'&&marker.getAttribute('refX')==='9'&&marker.querySelector('path').getAttribute('fill')==='var(--txt3)',
                    heads:[...document.querySelectorAll('[marker-end]')].every(e=>{
                      const id=e.getAttribute('marker-end').slice(5,-1),m=document.getElementById(id);
                      return m&&m.getAttribute('markerWidth')==='9'&&m.getAttribute('markerUnits')==='userSpaceOnUse'&&getComputedStyle(m.querySelector('path')).fill===getComputedStyle(e).stroke;
                    }),
                    label:$('#nextBtn').textContent==='Next →',
                    hidden:$('#stepLab').getBoundingClientRect().width===1,
                    noPageOverflow:document.documentElement.scrollWidth<=innerWidth+1
                  };
                }''')
                assert all(result[k] for k in ('proseFits','titleFits','neutral','marker','heads','label','hidden','noPageOverflow')),(width,theme,label,result)
                checked+=1
                return result['bounds']
            for chapter,count in enumerate(page.evaluate('PAGES.map(p=>p.steps.length)')):
                positions=[]
                for step in range(count):
                    page.evaluate('([p,s])=>go(p,s)',[chapter,step])
                    bounds=check(f'{chapter}.{step}')
                    positions.append(page.locator("#nextBtn").bounding_box()["y"])
                    if baseline is None:baseline=bounds
                    assert bounds==baseline,(width,theme,chapter,step,bounds,baseline)
                assert max(positions)-min(positions)<1,(width,theme,chapter,positions,"Next moved within topic")
            for chapter,step,control,values in OPTIONS:
                page.evaluate('([p,s])=>go(p,s)',[chapter,step])
                page.evaluate("if(!$('#lessonPanel').matches(':popover-open'))$('#lessonPanel').showPopover()");page.locator('#lessonDetails details').evaluate_all('(es)=>es.forEach(e=>e.open=true)')
                for value in values:
                    page.select_option('#'+control,value)
                    assert check(control+'-'+value)==baseline
                    if control=='detectorChoice':
                        page.check('#topologyChoice');assert check(control+'-'+value+'-pd')==baseline;page.uncheck('#topologyChoice')
            # The help stays out of the reading path until explicitly opened.
            page.evaluate("go(6,1);$('#lessonPanel').showPopover()")
            assert not page.locator('#hints').evaluate('e=>e.open')
            page.locator('#hints summary').focus();page.keyboard.press('Enter')
            assert page.locator('#hints').evaluate('e=>e.open')
            page.keyboard.press('Space')
            assert not page.locator('#hints').evaluate('e=>e.open')
            assert page.evaluate('state.page===6 && state.step===1'),'help key leaked into lesson navigation'
        page.close()
    browser.close()
print(f'PASS: {checked} states, stable card/Next bounds, unclipped prose, consistent markers, neutral connectors and keyboard disclosure')
