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
        page.goto((ROOT / "learn/flow-control-journey.html").as_uri() + "#page-14")
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
