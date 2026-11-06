#!/usr/bin/env python3
"""Exercise the real Pro dialog without submitting jobs or opening store links.
Run with the shared GPU locks held: python3 scripts/measure_pro_lamp.py URL OUT
Requires Playwright Chromium and WebKit. OUT receives screenshots and a 5.2 s MP4.
"""
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright

url, out = sys.argv[1], Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
checks = []

def check(value, label):
    assert value, label
    checks.append(label)

with sync_playwright() as p:
    for engine in ('chromium', 'webkit'):
        browser = getattr(p, engine).launch()
        for reduced in (False, True):
            context = browser.new_context(viewport={'width': 1440, 'height': 1000},
                                          reduced_motion='reduce' if reduced else 'no-preference')
            context.route('**/*', lambda r: r.continue_() if urlparse(r.request.url).netloc == urlparse(url).netloc else r.abort())
            page = context.new_page()
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_function('typeof openProModal === "function"')
            page.locator('#proBtn').click()
            check(page.locator('#proModal').is_visible(), engine + ': Pro opens')
            check(page.locator('#proModal button').evaluate('(e)=>e===document.activeElement'), engine + ': close receives focus')
            page.keyboard.press('Shift+Tab')
            check(page.locator('#proModal a').last.evaluate('(e)=>e===document.activeElement'), engine + ': backward focus trap')
            page.keyboard.press('Tab')
            check(page.locator('#proModal button').evaluate('(e)=>e===document.activeElement'), engine + ': forward focus trap')
            text = page.locator('#proModal').inner_text()
            for fact in ('$15', '$120', '$149', '200 buyers', 'Hardship licences', 'free app stays free', 'not on sale yet'):
                check(fact in text, engine + ': ' + fact)
            check(page.locator('#lightsOnTitle').evaluate('(e)=>getComputedStyle(e).textTransform') == 'none', engine + ': title stays in sentence case')
            get_animations = "document.querySelector('.lights-on-header').getAnimations({subtree:true})"
            if reduced:
                check(page.evaluate(get_animations + '.length') == 0, engine + ': reduced motion has no animations')
                check(page.locator('.lamp-bulb').evaluate('(e)=>getComputedStyle(e).opacity') == '1', engine + ': reduced motion starts lit')
            else:
                page.wait_for_timeout(3100)
                check(page.evaluate(get_animations + '.every(a=>a.playState==="finished")'), engine + ': entrance finishes')
                page.evaluate('openProModal()')
                check(page.evaluate(get_animations + '.every(a=>a.playState==="finished")'), engine + ': duplicate open does not replay')
            page.screenshot(path=str(out / f'studio-{engine}-{ "reduced" if reduced else "on"}.png'))
            page.keyboard.press('Escape')
            check(not page.locator('#proModal').is_visible(), engine + ': Escape closes')
            check(page.locator('#proBtn').evaluate('(e)=>e===document.activeElement'), engine + ': focus returns')
            page.locator('#proBtn').click()
            if not reduced:
                check(page.evaluate(get_animations + '.some(a=>a.playState==="running" && a.currentTime<700)'), engine + ': revisit replays')
            page.locator('#proModal button').click()
            page.set_viewport_size({'width': 390, 'height': 844})
            page.evaluate('openProModal()')
            page.wait_for_timeout(3000 if not reduced else 50)
            check(page.locator('.pro-card').evaluate('(e)=>e.scrollWidth<=e.clientWidth+1'), engine + ': narrow dialog has no horizontal overflow')
            page.screenshot(path=str(out / f'studio-{engine}-narrow-{reduced}.png'))
            if reduced:
                page.set_viewport_size({'width': 1440, 'height': 1000})
                page.evaluate("document.documentElement.dataset.theme='light'")
                page.screenshot(path=str(out / f'studio-{engine}-light.png'))
            context.close()
        if engine == 'chromium':
            c = browser.new_context(viewport={'width':1440,'height':1000}, device_scale_factor=1)
            c.route('**/*', lambda r: r.continue_() if urlparse(r.request.url).netloc == urlparse(url).netloc else r.abort())
            page=c.new_page(); page.goto(url,wait_until='domcontentloaded'); page.wait_for_function('typeof openProModal === "function"'); page.evaluate('document.fonts.ready')
            # Capture real elapsed-time browser frames as PNGs. This avoids the
            # lossy WebM recorder adding ringing to the lamp's thin SVG lines.
            frames = out / 'recording'; frames.mkdir(exist_ok=True)
            page.wait_for_timeout(500)
            start = time.monotonic(); page.locator('#proBtn').click()
            captures = []
            while time.monotonic() - start < 5.2:
                stamp = time.monotonic() - start
                frame = frames / f'frame-{len(captures):04d}.png'
                page.screenshot(path=str(frame))
                captures.append((frame.resolve(), stamp))
                page.wait_for_timeout(max(0, int(1000 * (1/24 - (time.monotonic()-start-stamp)))))
            c.close()
            listing = []
            for i, (frame, stamp) in enumerate(captures):
                following = captures[i+1][1] if i+1 < len(captures) else 5.2
                listing += ["file '" + str(frame).replace("'", "'\\''") + "'", f'duration {max(.001,following-stamp):.6f}']
            listing += ["file '" + str(captures[-1][0]).replace("'", "'\\''") + "'"]
            (frames/'frames.txt').write_text('\n'.join(listing)+'\n')
            subprocess.run(['ffmpeg','-v','error','-y','-f','concat','-safe','0','-i',str(frames/'frames.txt'),'-t','5.2','-r','30','-an','-c:v','libx264','-preset','fast','-crf','16','-pix_fmt','yuv420p','-movflags','+faststart',str(out/'lamp.mp4')],check=True)
        browser.close()
(out/'browser-checks.json').write_text(json.dumps(checks,indent=2)+'\n')
print(f'{len(checks)} browser checks passed')
