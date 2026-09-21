"""Exercise request filters against localhost without submitting lifecycle updates."""
import re
from urllib.parse import parse_qs, urlparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def main():
    output = Path('scratch/request-filters-review')
    output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.add_init_script("sessionStorage.setItem('nvb_active_role', 'admin')")
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto('http://127.0.0.1:5000/dashboard', wait_until='domcontentloaded')
        summary = page.locator('#adminRequestsSummary')

        def ready():
            expect(summary).to_have_text(re.compile(r'^Showing '), timeout=20000)
            expect(page.locator('#adminRequestsTbody')).to_have_attribute('aria-busy', 'false')

        ready()
        expect(page.locator('#adminRequestStatus')).to_have_value('active')
        data = page.evaluate("""async () => (await fetch('/api/v1/requests?status=active&limit=50&offset=50',
            {headers: {Authorization: `Bearer ${getActiveToken()}`}})).json()""")
        assert data['requests'], 'Browser check needs at least one active request'
        sample = data['requests'][-1]
        ticket = sample['request_id']
        search = page.locator('#adminRequestTicket')
        search.fill(ticket)
        expect(summary).to_have_text('Showing 1–1 of 1 matching requests')
        expect(page.locator('#adminRequestsTbody code')).to_have_text(ticket)
        page.evaluate('window.loadAdminRequestsTable()')
        ready()
        expect(search).to_have_value(ticket)
        expect(page.locator('#adminRequestsTbody code')).to_have_text(ticket)
        page.locator('#adminRequestsTbody button').click()
        expect(page.locator('#adminUpdateTicketId')).to_have_value(ticket)

        search.fill('NVB-NO-SUCH-TICKET-QUEUE-CHECK')
        expect(summary).to_have_text('Showing 0–0 of 0 matching requests')
        expect(page.locator('#adminRequestsTbody')).to_contain_text('No requests match')
        page.locator('#adminRequestClear').click()
        ready()
        expect(search).to_have_value('')
        expect(page.locator('#adminRequestStatus')).to_have_value('active')
        if data['total'] > 50:
            page.locator('#adminRequestsNext').click()
            expect(page.locator('#adminRequestsPage')).to_have_text(re.compile(r'^Page 2 of '))
            page.evaluate('window.loadAdminRequestsTable()')
            ready()
            expect(page.locator('#adminRequestsPage')).to_have_text(re.compile(r'^Page 2 of '))
            page.locator('#adminRequestsPrev').click()
            expect(page.locator('#adminRequestsPage')).to_have_text(re.compile(r'^Page 1 of '))

        for name, key in [('State', 'state'), ('District', 'district'), ('Category', 'category'),
                          ('Department', 'routed_department'), ('Urgency', 'urgency')]:
            if not sample.get(key):
                continue
            page.locator(f'#adminRequest{name}').select_option(sample[key])
            ready()
            if name != 'Department':
                expect(page.locator(f'#filter{name}')).to_have_value(sample[key])
        search.fill(ticket)
        expect(summary).to_have_text('Showing 1–1 of 1 matching requests')
        page.locator('#filterCategory').select_option('')
        expect(page.locator('#adminRequestCategory')).to_have_value('')
        ready()

        # Deliver an older state lookup last to exercise out-of-order responses.
        states = page.locator('#adminRequestState option').evaluate_all('(options) => options.map(option => option.value).filter(Boolean)')
        if len(states) >= 2:
            pending = []

            def delayed_districts(route):
                state = parse_qs(urlparse(route.request.url).query)['state'][0]
                if state == states[0]:
                    pending.append(route)
                else:
                    route.fulfill(json={'success': True, 'districts': ['Current state district']})

            page.route('**/api/districts?state=*', delayed_districts)
            page.locator('#adminRequestState').select_option(states[0])
            page.locator('#adminRequestState').select_option(states[1])
            expect(page.locator('#filterDistrict option')).to_have_text(['All Districts', 'Current state district'])
            assert pending
            pending[0].fulfill(json={'success': True, 'districts': ['Stale state district']})
            ready()
            expect(page.locator('#filterDistrict option')).to_have_text(['All Districts', 'Current state district'])
            page.unroute('**/api/districts?state=*', delayed_districts)

        page.locator('#adminRequestClear').click()
        ready()
        page.locator('#adminRequestStatus').select_option('Resolved')
        ready()
        for status in page.locator('#adminRequestsTbody .request-status').all_text_contents():
            assert status == 'Resolved'
        page.locator('#adminRequestClear').click()
        ready()
        page.locator('#adminRequestOverdue').check()
        ready()
        assert page.locator('#adminRequestsTbody code').count() == page.locator('#adminRequestsTbody .is-overdue').count()
        page.locator('#adminRequestClear').click()
        ready()
        search.fill(ticket)
        expect(summary).to_have_text('Showing 1–1 of 1 matching requests')

        for width in (1440, 768, 390, 320):
            page.set_viewport_size({'width': width, 'height': 1000})
            page.locator('#adminRequestsCard').scroll_into_view_if_needed()
            dimensions = page.evaluate("""() => ({viewport: innerWidth, document: document.documentElement.scrollWidth,
                card: document.getElementById('adminRequestsCard').clientWidth,
                content: document.getElementById('adminRequestsCard').scrollWidth})""")
            assert dimensions['document'] <= width + 1, dimensions
            assert dimensions['content'] <= dimensions['card'] + 1, dimensions
            if width in (1440, 390):
                page.locator('#adminRequestsCard').screenshot(path=str(output / f'filters-{width}.png'))
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.locator('#themeSwitch').click()
        page.locator('#adminRequestsCard').screenshot(path=str(output / 'filters-dark.png'))
        assert not errors, errors
        browser.close()
    print('PASS: search across pages, combined filters, shared filters, status, overdue, clear, refresh, pagination, row action, responsive layout, dark theme')


if __name__ == '__main__':
    main()
