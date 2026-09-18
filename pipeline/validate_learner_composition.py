#!/usr/bin/env python3
"""Check connector ownership and sibling geometry in the rendered learner."""
from pathlib import Path
from playwright.sync_api import sync_playwright
ROOT=Path(__file__).resolve().parents[1]
with sync_playwright() as pw:
    browser=pw.chromium.launch()
    for width in (1844,375):
        page=browser.new_page(viewport={'width':width,'height':1265},reduced_motion='reduce')
        errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto((ROOT/'learn/flow-control-journey.html').as_uri());page.evaluate('setAuto(false,true)')
        for theme in ('light','dark'):
            page.evaluate('t=>document.documentElement.dataset.theme=t',theme)
            page.evaluate('go(4,0)')
            assert page.locator('#fairViz path[marker-end]').count()==1,'stacked fairness arrowheads'
            assert page.locator('#fair-turn-label').text_content()=='A’s turn'
            assert page.locator('#fair-request').get_attribute('opacity')=='.95'
            assert page.evaluate('''()=>{
              const target=document.querySelector('#fair-dispatch-route');const p=target.getPointAtLength(0);
              return ['A','B','C'].every(id=>{const r=document.querySelector('#fair-route-'+id);const q=r.getPointAtLength(r.getTotalLength());return q.x===p.x&&q.y===p.y});
            }'''),'fairness branches do not join the shared stem'
            page.evaluate('go(10,0)')
            assert page.evaluate("""()=>{
              const value=document.querySelector('#meterVal').getBBox(),label=document.querySelector('#meterLabel').getBBox();
              const boundary=document.querySelector('#g-epp rect').getBBox(),bar=document.querySelector('#meterTrack').getBBox();
              return label.x+label.width+12<value.x && value.x+value.width<=boundary.x+boundary.width-16 && label.y+label.height<bar.y && value.y+value.height<bar.y;
            }"""),'saturation label/value overlaps its bar or owning boundary'
            assert page.evaluate("""()=>{
              const texts=[...document.querySelectorAll('#g-hdr text')],rects=[...document.querySelectorAll('#g-hdr rect')];
              return texts.every((e,i)=>{const t=e.getBBox(),r=rects[i].getBBox();return t.x>=r.x+6&&t.x+t.width<=r.x+r.width-6&&r.x+r.width<=354+1});
            }"""),'header text/box escapes its owning area' 
            for count in (0,1,4,5,7,14,100):
                page.evaluate('n=>{for(let j=0;j<3;j++)setFlow("b0",j,"tenant "+j,n,n?"":"empty")}',count)
                assert page.evaluate('''n=>[0,1,2].every(j=>{
                  const label=document.querySelector('#flx-b0-'+j),r=label.getBBox(),x=FLOWX[j];
                  const dots=[...document.querySelector('#fld-b0-'+j).children].filter(e=>+e.getAttribute('opacity')>0).length;
                  const extra=label.textContent.startsWith('+')?+label.textContent.slice(1):0;
                  return (!label.textContent||(r.x>=x&&r.x+r.width<=(j===2?790:FLOWX[j+1]-10)))&&dots+extra===n;
                })''',count),'overflow/empty status leaves its flow or miscounts'
            assert page.locator('#g-gw').evaluate('e=>getComputedStyle(e).opacity==="1"'),'context owner faded'
            assert page.evaluate('''()=>{
              const ends=['b100','b0','bm10'].map(id=>{const p=document.querySelector('#bandRoute-'+id);return p.getPointAtLength(p.getTotalLength())});
              return ends.every(p=>p.x===ends[0].x&&p.y===ends[0].y);
            }'''),'bands do not share dispatch junction'
            page.evaluate("go(9,3);$('#lessonPanel').showPopover()");page.select_option('#executionChoice','evict')
            assert page.evaluate('''()=>{
              const actor=document.querySelector('#fx circle').getBBox();
              return [...document.querySelectorAll('#g-pod1 text')].every(e=>{const t=e.getBBox();return actor.x+actor.width<t.x||actor.x>t.x+t.width||actor.y+actor.height<t.y||actor.y>t.y+t.height});
            }'''),'request actor overlaps endpoint text'
            assert page.evaluate("""()=>{
              const banner=document.querySelector('#haltBanner').getBoundingClientRect();
              return [...document.querySelectorAll('#fx path[stroke-dasharray]')].every(e=>e.getBoundingClientRect().top>=banner.bottom+6);
            }"""),'cancel route overlaps saturation status'
            page.select_option('#executionChoice','complete')
            assert page.evaluate("""()=>{
              const actor=document.querySelector('#fx circle').getBBox();
              return [...document.querySelectorAll('#g-tprem text')].every(e=>{const t=e.getBBox();return actor.y>=t.y+t.height+6});
            }"""),'completion marker overlaps caller label'
            assert not page.locator('#lessonDetails details details').evaluate('e=>e.open'),'optional eviction dominates ordinary phases'
            page.evaluate('go(14,0)')
            assert page.evaluate('''()=>{
              const trunk=document.querySelector('#rp-trunk'),start=trunk.getPointAtLength(0),end=trunk.getPointAtLength(trunk.getTotalLength());
              return ['p','s','b'].every(t=>{const path=document.querySelector('#rp-out-'+t),p=path.getPointAtLength(path.getTotalLength());return p.x===start.x&&p.y===start.y})&&[0,1].every(i=>{const p=document.querySelector('#rp-pod-'+i).getPointAtLength(0);return p.x===end.x&&p.y===end.y});
            }'''),'replay merge and fan-out are disconnected'
        page.emulate_media(reduced_motion='no-preference')
        page.evaluate('setAuto(false,true);go(4,0)')
        assert page.locator('#fairViz .fly-dot').count()==1
        page.evaluate('go(0,0)')
        assert page.locator('#fairViz .fly-dot').count()==0,'cancelled turn leaves a stale request'
        page.evaluate('go(12,0)')
        for pause in (50,1000):
            page.wait_for_timeout(pause)
            assert page.evaluate("""()=>{
              const labels=[...document.querySelectorAll('#g-gw text')].map(e=>e.getBBox());
              return [...document.querySelectorAll('#fx .fly-dot')].every(dot=>dot.getAnimations().every(animation=>animation.effect.getKeyframes().every(frame=>{
                if(!frame.transform)return true;
                const m=new DOMMatrix(frame.transform),x=+dot.getAttribute('cx')+m.m41,y=+dot.getAttribute('cy')+m.m42;
                return labels.every(r=>x+6<r.x||x-6>r.x+r.width||y+6<r.y||y-6>r.y+r.height);
              })));
            }"""),'batch animation crosses gateway labels'
        assert not errors,errors
        page.close()
    browser.close()
print('PASS: one fairness output, flow-count ownership, readable context, shared dispatch, actor clearance and replay junctions')
