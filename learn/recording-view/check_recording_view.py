"""Exercise the isolated presentation adapter against the current learner."""
import argparse, hashlib, json
from pathlib import Path
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument('--base', default='http://127.0.0.1:8879')
parser.add_argument('--out', required=True)
args = parser.parse_args()
out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parents[1] / 'flow-control-journey.html'
before = hashlib.sha256(source.read_bytes()).hexdigest()
errors = []; views = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={'width':1440,'height':810},color_scheme='dark')
    page.on('pageerror', lambda e: errors.append(str(e)))
    page.goto(args.base + '/learn/recording-view/?page=0', wait_until='networkidle')
    page.wait_for_function("document.documentElement.dataset.ready === 'true'")
    frame = page.frames[1]
    assert frame.locator('#rail').is_hidden()
    for chapter in range(15):
        page.locator('#chapter').select_option(str(chapter))
        page.wait_for_timeout(100)
        step = 0
        while True:
            assert frame.locator('#narr').inner_text().strip()
            assert frame.locator('#narr').is_hidden()
            embedded = chapter < 6 or 11 <= chapter <= 13
            assert frame.locator('#pTitle').is_hidden() == embedded
            if chapter == 0:
                assert frame.locator('#ded-head').is_visible()
                assert frame.locator('#ded-head').text_content() == 'Capacity utilization'
            scene = frame.locator('body').get_attribute('data-recording-scene')
            expected = 'replay' if chapter == 14 else 'evidence' if chapter >= 11 else 'diagram'
            assert scene == expected, (chapter,scene)
            if scene == 'evidence':
                assert frame.locator('#evidence').is_visible()
                assert frame.locator('#canvasWrap').is_hidden()
                assert frame.locator('#evidence').inner_text().strip()
            views.append({'chapter':chapter,'step':step,'scene':scene})
            label = frame.locator('#nextBtn').inner_text()
            if 'page' in label.lower() or frame.locator('#nextBtn').is_disabled(): break
            page.locator('#next').click();page.wait_for_timeout(100);step += 1
    # Real replay controls continue to work.
    frame.locator('#pgPause').click()
    assert frame.locator('#pgStep').is_enabled()
    initial = frame.locator('#pgStats').inner_text()
    frame.locator('#pgStep').click()
    assert frame.locator('#pgStats').inner_text() != initial
    frame.locator('#pgFc [data-f="0"]').click()
    assert frame.locator('#pgFc [data-f="0"]').get_attribute('aria-pressed') == 'true'
    frame.locator('#pgFc [data-f="1"]').click()
    for chapter,name in [(0,'capacity'),(8,'pressure'),(11,'evidence'),(14,'replay')]:
        page.locator('#chapter').select_option(str(chapter))
        if chapter == 11:page.locator('#next').click()
        page.locator('#clean').click();page.wait_for_timeout(1600)
        assert page.locator('#tools').get_attribute('data-hidden') == 'true'
        assert page.locator('#tools').evaluate("e => getComputedStyle(e).opacity") == '0'
        assert frame.locator('.cel.lit').count() > 0
        if chapter == 0:
            assert frame.locator('#ov-ded').evaluate("e => getComputedStyle(e).opacity") == '1'
        assert frame.locator('.replay-footnote').is_visible() if chapter==14 else True
        page.screenshot(path=str(out/f'{name}.png'))
        assert frame.locator('#narr').is_hidden()
        visual = frame.locator('#evidence' if chapter == 11 else '#canvasWrap')
        bounds = visual.bounding_box()
        assert bounds['y']+bounds['height'] <= 811, (name,bounds)
        frame.locator('body').press('Escape')
        assert page.locator('#tools').get_attribute('data-hidden') == 'false'
    # A separate ordinary page must not receive presentation styles or state.
    normal = browser.new_page(viewport={'width':1440,'height':810})
    normal.goto(args.base+'/learn/flow-control-journey.html',wait_until='networkidle')
    assert normal.locator('#rail').is_visible()
    assert 'recording-view' not in (normal.locator('body').get_attribute('class') or '')
    page.goto(args.base+'/learn/recording-view/?page=14&clean=1',wait_until='networkidle')
    page.wait_for_function("document.documentElement.dataset.ready === 'true'")
    assert page.locator('#tools').get_attribute('data-hidden')=='true'
    assert page.frames[1].locator('body').get_attribute('data-recording-scene')=='replay'
    browser.close()
after = hashlib.sha256(source.read_bytes()).hexdigest()
assert before == after, 'Learner changed concurrently; rerun validation against the latest version.'
assert not errors, errors
(out/'report.json').write_text(json.dumps({'source_sha256':before,'views':views,'errors':errors,'normal_view_unchanged':True},indent=2))
print(f'Passed {len(views)} guided views, replay controls, clean-frame escape, deep link, normal-view isolation. Source {before}')
