#!/usr/bin/env python3
"""Check shared evidence-card geometry, not just outer-page overflow."""
from pathlib import Path
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
CHECK = """() => {
  const box = e => e.getBoundingClientRect();
  const shown = e => e && e.getClientRects().length && box(e).height > 0;
  const assert = (ok, why) => {if (!ok) throw Error(why)};
  const card = document.querySelector('#evidence');
  const title = card.querySelector('.et');
  const badge = card.querySelector('.ebadge'), heading = card.querySelector('.eheading');
  if (badge && heading) assert(box(badge).bottom + 3 <= box(heading).top, 'badge overlaps heading');
  const scale = card.querySelector('.eaxis span');
  const rows = [...card.querySelectorAll('.ebar')];
  if (scale && rows.length) {
    assert(box(title).bottom <= box(scale).top, 'heading overlaps scale');
    assert(box(scale).bottom + 3 <= box(rows[0]).top, 'scale overlaps first bar');
  }
  rows.forEach((row, i) => {
    const label=box(row.querySelector('.el')), value=box(row.querySelector('.ev'));
    const track=box(row.querySelector('.etrack'));
    assert(label.right + 3 <= value.left, 'label overlaps value');
    assert(Math.max(label.bottom,value.bottom) + 3 <= track.top, 'label/value overlaps bar');
    assert(track.left >= box(card).left && track.right <= box(card).right, 'bar escapes card');
    if (i) assert(box(rows[i-1]).bottom + 3 <= box(row).top, 'bar rows overlap');
  });
  for (const e of card.querySelectorAll('.et,.eaxis,.ebar,.esource,.esource a')) {
    if (!shown(e)) continue;
    const r=box(e), parent=box(card);
    assert(r.left >= parent.left && r.right <= parent.right + 1, 'child escapes card');
  }
  assert(document.documentElement.scrollWidth <= innerWidth + 1, 'page overflow');
}"""

with sync_playwright() as pw:
    browser=pw.chromium.launch()
    count=0
    for width in (1844,375):
        page=browser.new_page(viewport={'width':width,'height':1265},reduced_motion='reduce')
        page.goto((ROOT/'learn/flow-control-journey.html').as_uri())
        page.evaluate('setAuto(false,true)')
        for theme in ('dark','light'):
            page.evaluate('t=>document.documentElement.dataset.theme=t',theme)
            states=[(p,s,None,None) for p in (11,12,13) for s in range(3)]
            states += [(8,3,'bandChoice',x) for x in ('high','low')]
            states += [(10,0,'policyChoice','ceilings')]
            states += [(9,3,'executionChoice',x) for x in ('queued','dispatch','engine','complete','evict')]
            for p,s,control,value in states:
                page.evaluate('x=>go(...x)',[p,s])
                if control:
                    page.evaluate("if(!$('#lessonPanel').matches(':popover-open'))$('#lessonPanel').showPopover()");page.locator('#lessonDetails details').evaluate_all('es=>es.forEach(e=>e.open=true)')
                    page.select_option('#'+control,value)
                assert page.locator('#evidence').is_visible(),(p,s,control,value)
                page.evaluate(CHECK)
                page.locator('#evidence details').evaluate_all('es=>es.forEach(e=>e.open=true)')
                page.evaluate(CHECK)
                if (p,s)==(12,1):
                    assert page.locator('.ebar').nth(1).locator('.efill').evaluate('e=>e.getBoundingClientRect().width===0'), 'zero events draw a nonzero bar'
                count+=1
        page.close()
    browser.close()
print(f'PASS: {count} evidence-card states, closed/open source details, two widths and themes')
