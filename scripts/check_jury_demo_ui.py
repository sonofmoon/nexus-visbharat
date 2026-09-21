"""Check the jury walkthrough, public samples, and execution links without changing tickets."""
import json
from pathlib import Path
from playwright.sync_api import expect, sync_playwright


def main():
    output = Path('scratch/jury-demo-v2/browser-review')
    output.mkdir(parents=True, exist_ok=True)
    manifest = json.loads(Path('static/data/demo_showcase.json').read_text(encoding='utf-8'))
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:5000/demo', wait_until='domcontentloaded')
        expect(page.locator('.case-card')).to_have_count(7)
        expect(page.locator('.disclosure')).to_contain_text('fictional')
        for state, count in [('Tamil Nadu', 2), ('Andhra Pradesh', 3), ('Telangana', 2)]:
            page.locator('#demoState').select_option(state)
            expect(page.locator('.case-card:visible')).to_have_count(count)
        page.locator('#demoState').select_option('')
        for case in manifest['cases']:
            card = page.locator('#' + case['key'])
            card.locator('[data-track]').click()
            expect(card.locator('.live-progress > strong')).to_contain_text(case['status'])
            expect(card.locator('.live-progress li').last).to_contain_text(case['status'])
        for width in (1440, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'), width
            if width in (1440, 390):
                page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(output / f'walkthrough-{width}.png'))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('#karur_water').screenshot(path=str(output / 'karur-water.png'))
        # A completed ticket must remain findable even though the normal queue defaults to active.
        closed = next(case for case in manifest['cases'] if case['status'] == 'Closed')
        page.goto('http://127.0.0.1:5000/dashboard?demo_ticket=' + closed['request_id'], wait_until='domcontentloaded')
        expect(page.locator('#adminRequestStatus')).to_have_value('')
        expect(page.locator('#adminRequestsTbody code')).to_have_text(closed['request_id'], timeout=20000)
        expect(page.locator('#adminRequestsTbody .request-status')).to_have_text('Closed')
        expect(page.locator('#adminUpdateTicketId')).to_have_value(closed['request_id'])
        page.locator('#adminRequestsCard').screenshot(path=str(output / 'execution-ticket.png'))
        page.goto('http://127.0.0.1:5000/', wait_until='domcontentloaded')
        expect(page.locator('.public-demo-notice')).to_be_visible()
        page.locator('.public-samples button').first.click()
        expect(page.locator('#trackerResultContainer')).to_contain_text(manifest['cases'][0]['request_id'], timeout=15000)
        assert not errors, errors
        browser.close()
    print('PASS: seven live trackers, state filters, synthetic disclosure, responsive walkthrough, closed-ticket execution link, working public sample')


if __name__ == '__main__':
    main()
