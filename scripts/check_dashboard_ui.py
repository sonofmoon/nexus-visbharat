import argparse
import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright


def layout(page):
    return page.evaluate("""() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        panels: [...document.querySelectorAll('.dashboard-panel')].filter(panel => panel.getClientRects().length).map(panel => ({
            title: panel.querySelector('h3')?.textContent.trim(),
            width: panel.clientWidth,
            content: panel.scrollWidth
        }))
    })""")


def assert_contained(page):
    report = layout(page)
    assert report['document'] <= report['viewport'] + 1, report
    for panel in report['panels']:
        assert panel['content'] <= panel['width'] + 2, panel
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5000')
    arguments = parser.parse_args()
    output = Path('scratch/dashboard-review')
    output.mkdir(parents=True, exist_ok=True)
    results = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: (errors.append(str(error)), print('Browser error:', str(error), flush=True)))
        response = page.goto(arguments.base_url + '/dashboard', wait_until='domcontentloaded')
        assert response.status == 200
        expect(page.locator('#projectsList article').first).to_be_visible(timeout=30000)
        page.wait_for_function("window.Chart && Object.keys(Chart.instances).length === 4")
        page.wait_for_timeout(600)
        for width in [1920, 1440, 1024, 768, 390, 320]:
            page.set_viewport_size({'width': width, 'height': 1000})
            page.wait_for_timeout(300)
            results[str(width)] = assert_contained(page)
            if width in [1440, 390]:
                page.evaluate('window.scrollTo(0, 0)')
                page.screenshot(path=str(output / f'dashboard-{width}.png'))

        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('#analyticsPanel').screenshot(path=str(output / 'analytics.png'))
        page.locator('#priorityPanel').screenshot(path=str(output / 'projects.png'))
        page.get_by_role('button', name='Switch to dark theme').click()
        page.wait_for_timeout(300)
        page.evaluate('window.scrollTo(0, 0)')
        page.screenshot(path=str(output / 'dashboard-dark.png'))
        assert_contained(page)
        page.get_by_role('button', name='Switch to light theme').click()
        page.wait_for_timeout(300)
        toggle = page.locator('[data-project-toggle]')
        if toggle.count():
            toggle.click()
            expect(page.locator('[data-project-overflow]').first).to_be_visible()
            toggle.click()
            expect(page.locator('[data-project-overflow]').first).to_be_hidden()

        details = page.locator('#projectsList details').first
        details.locator('summary').click()
        expect(details).to_have_attribute('open', '')
        page.evaluate('loadPriorityProjects()')
        expect(page.locator('#projectsList details').first).to_have_attribute('open', '')

        page.locator('.chart-data summary').first.click()
        expect(page.locator('.chart-data table').first).to_be_visible()
        page.locator('.chart-data summary').first.click()

        state = page.locator('#filterState option').nth(1).get_attribute('value')
        with page.expect_request(lambda request: '/api/stats?' in request.url and parse_qs(urlparse(request.url).query).get('state') == [state]):
            page.select_option('#filterState', state)
        page.wait_for_function("document.querySelector('#filterDistrict').options.length > 1")
        district = page.locator('#filterDistrict option').nth(1).get_attribute('value')
        with page.expect_request(lambda request: '/api/stats?' in request.url and parse_qs(urlparse(request.url).query).get('district') == [district]):
            page.select_option('#filterDistrict', district)
        with page.expect_request(lambda request: '/api/v1/geo/layers?' in request.url and parse_qs(urlparse(request.url).query).get('layer') == ['spend']):
            page.select_option('#mapLayer', 'spend')
        expect(page.locator('#mapLayerLabel')).to_have_text('Public spend', timeout=20000)
        page.locator('.ga4-tab[data-period="30"]').click()
        page.wait_for_function("selectedTrendPeriod === '30'")

        for role in ['auditor', 'admin', 'public', 'analyst']:
            page.select_option('#userRoleSelector', role)
            expect(page.locator('body')).to_have_attribute('data-role', role)
            if role == 'analyst':
                expect(page.locator('#mapPanel')).to_be_visible()
            else:
                expect(page.locator('#mapPanel')).to_be_hidden()
            expect(page.locator('#' + ('public' if role == 'public' else role) + 'ReportView')).to_be_visible()
            results[role] = assert_contained(page)

        page.locator('#sectionSearch').fill('Executive analytics')
        page.locator('#sectionSearch').press('Enter')
        expect(page.locator('#analyticsPanel')).to_be_focused()
        page.set_viewport_size({'width': 390, 'height': 900})
        page.locator('#railToggle').click()
        expect(page.locator('#consoleSidebar')).to_be_visible()
        page.keyboard.press('Escape')
        expect(page.locator('#consoleSidebar')).to_be_hidden()
        expect(page.locator('#railToggle')).to_be_focused()

        page.emulate_media(reduced_motion='reduce')
        page.evaluate("animateNumber('languageCount', 7)")
        expect(page.locator('#languageCount')).to_have_text('7')
        assert not errors, errors
        results['page_errors'] = errors

        fallback = context.new_page()
        fallback_errors = []
        fallback.on('pageerror', lambda error: fallback_errors.append(str(error)))
        fallback.route('**/unpkg.com/**', lambda route: route.abort())
        fallback.route('**/maps.googleapis.com/**', lambda route: route.abort())
        fallback.goto(arguments.base_url + '/dashboard', wait_until='domcontentloaded')
        expect(fallback.locator('#commandHero h1')).to_have_text('Policy overview')
        expect(fallback.locator('#policyInsight h2')).to_be_visible()
        expect(fallback.locator('#filterState')).to_be_visible()
        fallback.wait_for_timeout(4000)
        expect(fallback.locator('#hotspotMap .brief-placeholder')).to_be_visible()
        results['cdn_fallback'] = assert_contained(fallback)
        assert not fallback_errors, fallback_errors
        browser.close()

    (output / 'checks.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    print('PASS: responsive widths, chart data, themes, filters, map layers, role switching, keyboard navigation, reduced motion, CDN fallback')


if __name__ == '__main__':
    main()
