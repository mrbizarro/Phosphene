#!/usr/bin/env python3
"""Browser gate for studio motion. All POSTs blocked; no GPU jobs.
Run with Playwright installed: python scripts/measure_studio_motion.py --url URL
"""
import argparse
import copy
import json
from urllib.request import urlopen
from playwright.sync_api import sync_playwright


def check(url):
    with urlopen(url + '/status', timeout=10) as response:
        base = json.load(response)
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for reduced in ('no-preference', 'reduce'):
            context = browser.new_context(viewport={'width': 1440, 'height': 900}, reduced_motion=reduced)
            page = context.new_page()
            state = copy.deepcopy(base)
            state.update(running=False, current=None, queue=[], history=[])
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))
            context.route('**/*', lambda route: route.fulfill(status=403, json={'error': 'UI test: POST blocked'})
                          if route.request.method == 'POST' else route.fallback())
            context.route('**/status', lambda route: route.fulfill(json=state))
            page.goto(url, wait_until='domcontentloaded')
            page.wait_for_function("typeof window.studioProgress === 'function' && typeof poll === 'function'")
            assert page.locator('#studioStageMark').is_hidden()
            assert page.locator('#nowCard .ph-mark').count() == 0
            for outcome in ('done', 'failed', 'stopped'):
                job = {'id': 'motion-' + outcome, 'started_ts': base['server_now'] - 10,
                       'params': {'mode': 't2v', 'engine': 'ltx', 'prompt': 'UI status replay',
                                  'width': 1024, 'height': 576, 'frames': 121, 'quality': 'balanced'},
                       'progress': {'pct': 45, 'phase_label': 'Working', 'elapsed_sec': 10, 'preview': {}}}
                state.update(running=True, current=job, history=[])
                page.evaluate('poll()')
                assert page.locator('#studioStageMark').is_visible()
                assert page.locator('[aria-label="Render progress"]').get_attribute('aria-valuenow') == '45'
                # Polling must not reconstruct the mark or restart its motion.
                page.evaluate("window.reviewLoadingMark = document.getElementById('studioStageMark')")
                page.evaluate('poll()')
                assert page.locator('#studioProgressValue').inner_text() == '45%'
                assert page.evaluate("reviewLoadingMark === document.getElementById('studioStageMark')")
                state.update(running=False, current=None, history=[dict(job, status=outcome, error='UI test')])
                page.evaluate('poll()')
                assert page.locator('#studioStageMark').is_hidden()
                complete = page.locator('#nowCard').evaluate("e => e.classList.contains('is-complete')")
                assert complete == (outcome == 'done'), (outcome, complete)
                page.wait_for_timeout(1200)
                assert page.locator('#studioProgressValue').is_hidden()
            # A new job must cancel the previous completion reset timer.
            state.update(running=True, current=dict(job, id='first'), history=[])
            page.evaluate('poll()')
            state.update(running=False, current=None, history=[dict(job, id='first', status='done')])
            page.evaluate('poll()')
            state.update(running=True, current=dict(job, id='next'), history=[])
            page.evaluate('poll()')
            page.wait_for_timeout(1200)
            assert page.locator('#studioProgressValue').inner_text() == '45%'
            assert page.locator('#studioProgressValue').is_visible()
            assert page.locator('#studioStageMark').is_visible()
            # Six independent directions, a slow morph and hue drift; static
            # under reduced motion, even while a job is actively running.
            animations = page.locator('#studioStageMark').evaluate(
                "e => e.getAnimations({subtree:true}).filter(a => a.playState === 'running').map(a => a.animationName)")
            if reduced == 'reduce':
                assert not animations, animations
            else:
                assert animations.count('fm-turn') == 6, animations
                assert 'fm-breathe' in animations
                assert 'fm-hue' in animations
            page.evaluate("phosToast('Motion gate', {duration:150})")
            page.wait_for_timeout(500)
            assert page.locator('.phos-toast').count() == 0
            if reduced == 'reduce':
                assert page.locator('#progressFill').evaluate("e => getComputedStyle(e,'::after').display") == 'none'
                assert page.evaluate("document.getAnimations().filter(a => a.playState === 'running' && a.effect.getTiming().iterations === Infinity).length") == 0
            # Failed/delayed first frames must retain the artwork. Successful
            # decode crossfades both layers, then stops hidden ring work.
            page.route('**/motion-preview.jpg', lambda r: r.fulfill(status=404))
            state['current']['progress']['preview'] = {'url': url + '/motion-preview.jpg', 'meaningful': True}
            page.evaluate('poll()')
            page.wait_for_timeout(450)
            assert page.locator('#studioStageMark').is_visible()
            assert not page.locator('#playerWrap').evaluate("e => e.classList.contains('has-live-frame')")
            page.unroute('**/motion-preview.jpg')
            page.route('**/motion-preview.jpg*', lambda r: r.fulfill(
                content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"><rect width="100" height="100" fill="teal"/></svg>'))
            state['current']['progress']['preview']['url'] += '?ready=1'
            page.evaluate('poll()')
            page.wait_for_function("document.querySelector('#playerWrap').classList.contains('has-live-frame')")
            page.wait_for_timeout(100)
            opacity = page.locator('.live-stage-warming').evaluate('e => Number(getComputedStyle(e).opacity)')
            assert 0 < opacity < 1, opacity
            page.wait_for_timeout(900)
            assert page.locator('#studioStageMark').is_hidden()
            assert page.locator('#studioStageMark').evaluate(
                "e => e.getAnimations({subtree:true}).filter(a => a.playState === 'running').length") == 0
            # Back-to-back jobs cannot inherit the previous job's loaded frame.
            state['current'] = dict(job, id='fresh', progress={'pct': 3, 'phase_label': 'Loading model', 'preview': {}})
            page.evaluate('poll()')
            assert page.locator('#studioStageMark').is_visible()
            assert page.locator('.live-stage-image').count() == 0
            assert page.locator('.live-stage-progress').inner_text() == '3%'
            assert not errors, errors
            results.append({'motion': reduced, 'success_failure_stop': True, 'successive_jobs': True, 'toast_dismissal': True, 'first_frame_crossfade': True, 'failed_image_retains_mark': True, 'fresh_job_resets_frame': True})
            context.close()
        browser.close()
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    args = parser.parse_args()
    print(json.dumps(check(args.url.rstrip('/')), indent=2))
