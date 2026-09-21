import argparse
import json
import re
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright


def assert_contained(page):
    report = page.evaluate("""() => ({
        viewport: innerWidth,
        document: document.documentElement.scrollWidth,
        clipped: [...document.querySelectorAll('.m3-card, .public-channel-card, .public-tracker')]
            .filter(node => node.getClientRects().length && node.scrollWidth > node.clientWidth + 2)
            .map(node => ({id: node.id, width: node.clientWidth, content: node.scrollWidth}))
    })""")
    assert report['document'] <= report['viewport'] + 1, report
    assert not report['clipped'], report
    return report


def audit(page, axe_path):
    if not axe_path:
        return {'skipped': 'Pass --axe-path to run WCAG checks with a local axe-core script.'}
    page.add_script_tag(path=str(axe_path))
    result = page.evaluate("""async () => {
        const result = await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21aa']}});
        return result.violations.map(item => ({id: item.id, impact: item.impact,
            nodes: item.nodes.map(node => ({target: node.target, summary: node.failureSummary}))}));
    }""")
    if result:
        print(json.dumps({'accessibility': result}), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', default='http://127.0.0.1:5000')
    parser.add_argument('--axe-path', type=Path)
    arguments = parser.parse_args()
    output = Path('scratch/public-review')
    output.mkdir(parents=True, exist_ok=True)
    reports = {}
    payloads = []
    errors = []
    unexpected_writes = []
    fail_submit = False
    stages = [{'step': index, 'name': name, 'desc': 'UI test fixture'} for index, name in enumerate(['Submitted', 'Assigned', 'In progress', 'Review', 'Resolved'], 1)]

    def intercept(route):
        request = route.request
        path = urlparse(request.url).path
        if request.method in ['POST', 'PUT', 'DELETE', 'PATCH']:
            body = request.post_data_json or {}
            payloads.append({'path': path, 'body': body})
            fixtures = {
                '/api/translate': {'result': {'translated_text': body.get('text', '')}},
                '/api/classify': {'result': {'category': 'Road', 'urgency': 'Routine', 'sentiment': 'Concerned', 'confidence': 0.94}},
                '/api/v1/geo/ward-suggest': {'success': True, 'ward': 'Ward 12', 'ward_source': 'test_fixture'},
                '/api/transcribe-voice': {'success': True, 'transcript': 'The road near the school needs repair.', 'stt_mode': 'test_fixture'},
                '/api/submit': {'success': True, 'request_id': 'NVB-UI-TEST', 'routing': {'ward': 'Ward 12', 'department': 'Public Works Department'}},
                '/api/submit-voice': {'success': True, 'request_id': 'NVB-VOICE-TEST', 'transcript': 'The road near the school needs repair.'},
                '/api/attachments/ingest-stub': {'validated': True},
            }
            if path not in fixtures:
                unexpected_writes.append(path)
                route.fulfill(status=400, json={'success': False, 'error': 'Unexpected write blocked by UI test'})
            elif fail_submit and path == '/api/submit':
                route.fulfill(status=503, json={'success': False, 'error': 'Test service unavailable. Please retry.'})
            else:
                route.fulfill(json=fixtures[path])
            return
        if path.endswith('/track'):
            if 'MISSING' in path:
                route.fulfill(json={'success': False, 'error': 'No matching request found.'})
            else:
                route.fulfill(json={'success': True, 'request_id': 'NVB-UI-TEST', 'category': 'Road', 'district': 'Karur', 'state': 'Tamil Nadu', 'status': 'Assigned', 'current_stage_index': 2, 'stages': stages, 'timeline_events': [], 'routed_department': 'Public Works Department', 'sla_due_at': '2026-09-20T10:00:00'})
            return
        if path.endswith('/timeline'):
            route.fulfill(json={'success': True, 'request': {'request_id': 'NVB-UI-TEST', 'status': 'Assigned', 'ward': 'Ward 12', 'district': 'Karur', 'state': 'Tamil Nadu', 'category': 'Road', 'original_text': 'Road near the school needs repair.', 'routed_department': 'Public Works Department'}})
            return
        route.continue_()

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'])
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, permissions=['microphone', 'camera', 'geolocation'], geolocation={'latitude': 10.96, 'longitude': 78.07})
        context.route('**/api/**', intercept)
        context.add_init_script('window.SpeechRecognition = undefined; window.webkitSpeechRecognition = undefined;')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))

        for name, route in [('home', '/'), ('submit', '/submit')]:
            response = page.goto(arguments.base_url + route, wait_until='domcontentloaded')
            assert response.status == 200
            page.wait_for_timeout(1000)
            expect(page.locator('h1')).to_have_count(1)
            duplicate_ids = page.evaluate("""() => { const ids = [...document.querySelectorAll('[id]')].map(node => node.id); return ids.filter((id, index) => ids.indexOf(id) !== index); }""")
            assert not duplicate_ids, duplicate_ids
            for width in [1920, 1440, 1024, 768, 390, 320]:
                page.set_viewport_size({'width': width, 'height': 1000})
                page.wait_for_timeout(250)
                reports[f'{name}-{width}'] = assert_contained(page)
                if width in [1440, 390]:
                    page.screenshot(path=str(output / f'{name}-{width}.png'))
            page.get_by_role('button', name='Open navigation').click()
            expect(page.locator('#public-navigation')).to_be_visible()
            page.keyboard.press('Escape')
            expect(page.locator('#public-navigation')).to_be_hidden()
            expect(page.locator('[data-public-menu]')).to_be_focused()
            page.set_viewport_size({'width': 1440, 'height': 1000})
            reports[f'{name}-accessibility-light'] = audit(page, arguments.axe_path)
            page.get_by_role('button', name='Switch to dark theme').click()
            page.wait_for_timeout(300)
            page.screenshot(path=str(output / f'{name}-dark.png'))
            reports[f'{name}-accessibility-dark'] = audit(page, arguments.axe_path)
            assert_contained(page)
            page.get_by_role('button', name='Switch to light theme').click()
            page.wait_for_timeout(300)

        page.goto(arguments.base_url, wait_until='domcontentloaded')
        page.locator('.public-technical summary').first.click()
        expect(page.locator('.public-technical-grid article')).to_have_count(6)
        page.locator('.public-technical summary').nth(1).click()
        expect(page.locator('.public-architecture li')).to_have_count(5)
        page.locator('#trackTicketInput').fill('NVB-UI-TEST')
        page.locator('#trackTicketInput').press('Enter')
        expect(page.locator('#trackerResultContainer')).to_contain_text('Assigned')
        page.locator('#trackTicketInput').fill('MISSING')
        page.locator('#trackTicketInput').press('Enter')
        expect(page.locator('#trackerResultContainer')).to_contain_text('No matching request')
        page.locator('.public-samples button').first.click()
        expect(page.locator('#trackTicketInput')).to_have_value('NVB-202609140456')
        expect(page.locator('#trackerResultContainer')).to_contain_text('Assigned')
        for modal, opener in [('ivrModalOverlay', 'openIvrModal'), ('whatsAppModalOverlay', 'openWhatsAppModal'), ('smsModalOverlay', 'openSmsModal'), ('emailModalOverlay', 'openEmailModal')]:
            page.set_viewport_size({'width': 390, 'height': 844})
            button = page.locator(f'button[onclick="{opener}()"]')
            button.click()
            expect(page.locator(f'#{modal} [role="dialog"]')).to_be_visible()
            close = page.locator(f'#{modal} button[aria-label="Close dialog"]')
            expect(close).to_be_focused()
            page.keyboard.press('Shift+Tab')
            assert page.evaluate(f"document.getElementById('{modal}').contains(document.activeElement)")
            assert_contained(page)
            page.wait_for_timeout(350)
            reports[f'{modal}-accessibility'] = audit(page, arguments.axe_path)
            page.evaluate("document.documentElement.dataset.publicTheme = 'dark'")
            page.wait_for_timeout(350)
            reports[f'{modal}-accessibility-dark'] = audit(page, arguments.axe_path)
            page.evaluate("document.documentElement.dataset.publicTheme = 'light'")
            page.keyboard.press('Escape')
            expect(page.locator(f'#{modal}')).to_be_hidden()
            expect(button).to_be_focused()

        page.locator('#dfcxWidgetToggleBtn').click()
        expect(page.locator('#dfcxAssistantWindow')).to_be_visible()
        expect(page.locator('#dfcxInputText')).to_be_focused()
        assert_contained(page)
        page.keyboard.press('Escape')
        expect(page.locator('#dfcxAssistantWindow')).to_be_hidden()

        page.goto(arguments.base_url + '/submit', wait_until='domcontentloaded')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        page.wait_for_function("document.querySelector('#state').options.length > 1")
        expect(page.locator('#district')).to_be_disabled()
        page.select_option('#state', label='Tamil Nadu')
        page.wait_for_function("document.querySelector('#district').options.length > 1")
        page.select_option('#district', label='Karur')
        page.locator('#complaint-text').fill('The road near our school needs repair.')
        page.get_by_role('button', name='Tamil language input').focus()
        page.keyboard.press('Space')
        expect(page.locator('#language')).to_have_value('ta')
        expect(page.locator('#complaint-text')).to_have_value('The road near our school needs repair.')
        page.get_by_role('button', name='Telugu language input').click()
        expect(page.locator('#language')).to_have_value('te')
        page.get_by_role('button', name='English language input').click()
        expect(page.locator('#ai-confidence')).to_have_text('94.0%', timeout=10000)
        expect(page.locator('#routedDepartment')).to_have_value('Public Works Department')
        page.locator('#captureLocationBtn').click()
        expect(page.locator('#ward')).to_have_value('Ward 12')
        expect(page.locator('#location-lat')).to_have_value('10.960000')
        page.select_option('#category', 'Water Supply')
        expect(page.locator('#routedDepartment')).to_have_value('Water Supply & Drainage Board')
        page.locator('#translateBtn').click()
        expect(page.locator('#category')).to_have_value('Road')
        page.locator('#request-evidence summary').click()
        page.locator('#photo-attachments').set_input_files({'name': 'road.png', 'mimeType': 'image/png', 'buffer': b'test image fixture'})
        page.locator('#file-attachments').set_input_files({'name': 'report.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-test fixture'})
        expect(page.locator('#photoCountHint')).to_have_text('1 photo selected.')
        expect(page.locator('#fileCountHint')).to_have_text('1 file selected.')
        page.locator('#openCameraBtn').click()
        expect(page.locator('#cameraPreview')).to_be_visible()
        page.wait_for_function("document.getElementById('cameraPreview').videoWidth > 0")
        page.locator('#captureCameraBtn').click()
        expect(page.locator('#cameraCountHint')).to_have_text('1 camera photo captured.')
        page.locator('#clearCameraCapturesBtn').click()
        expect(page.locator('#cameraCountHint')).to_have_text('No camera photo captured.')
        page.locator('#closeCameraBtn').click()
        expect(page.locator('#cameraPreview')).to_be_hidden()
        page.locator('#trackRequestIdInput').fill('NVB-UI-TEST')
        page.locator('#trackRequestIdInput').press('Enter')
        expect(page.locator('#trackResultContainer')).to_contain_text('Assigned')
        page.locator('#request-review').screenshot(path=str(output / 'submit-ai-review.png'))
        reports['submit-populated-accessibility'] = audit(page, arguments.axe_path)
        page.locator('#consentGranted').uncheck()
        expect(page.locator('#consentGranted')).not_to_be_checked()
        page.locator('#consentGranted').check()
        fail_submit = True
        page.locator('#submitBtn').click()
        expect(page.locator('#submissionErrorText')).to_contain_text('Test service unavailable')
        expect(page.locator('#complaintForm')).to_be_visible()
        expect(page.locator('#submitBtn')).to_be_enabled()
        fail_submit = False
        page.locator('#submitBtn').click()
        expect(page.locator('#successMessage')).to_be_visible()
        expect(page.locator('#successMessage')).to_be_focused()
        expect(page.locator('#complaintId')).to_have_text('NVB-UI-TEST')
        expect(page.locator('#complaintForm')).to_be_hidden()
        submission = next(entry['body'] for entry in reversed(payloads) if entry['path'] == '/api/submit')
        assert submission['attachment_count'] == 2
        assert submission['consent_granted'] is True
        assert submission['location'] == {'lat': 10.96, 'lng': 78.07}
        assert submission['state'] == 'Tamil Nadu' and submission['district'] == 'Karur'
        assert submission['language'] == 'en' and submission['source'] == 'Web Form'
        page.locator('#successMessage').screenshot(path=str(output / 'submit-success.png'))

        page.goto(arguments.base_url + '/submit', wait_until='domcontentloaded')
        page.wait_for_function("document.querySelector('#state').options.length > 1")
        page.select_option('#state', label='Tamil Nadu')
        page.wait_for_function("document.querySelector('#district').options.length > 1")
        page.select_option('#district', label='Karur')
        page.locator('#recordBtn').click()
        expect(page.locator('#recordBtn')).to_have_attribute('aria-pressed', 'true')
        page.wait_for_timeout(600)
        page.locator('#recordBtn').click()
        expect(page.locator('#recordBtn')).to_have_attribute('aria-pressed', 'false')
        expect(page.locator('#complaint-text')).to_have_value('The road near the school needs repair.', timeout=10000)
        expect(page.locator('#voiceStatus')).to_contain_text('Voice transcribed')
        page.locator('#submitBtn').click()
        expect(page.locator('#complaintId')).to_have_text('NVB-VOICE-TEST')
        voice = next(entry['body'] for entry in reversed(payloads) if entry['path'] == '/api/submit-voice')
        assert voice['audio_base64'] and voice['district'] == 'Karur'

        context.clear_permissions()
        page.goto(arguments.base_url + '/submit', wait_until='domcontentloaded')
        page.evaluate("() => { navigator.mediaDevices.getUserMedia = () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')); }")
        page.locator('#recordBtn').click()
        expect(page.locator('#voiceStatus')).to_contain_text('Permission denied')
        expect(page.locator('#recordBtnText')).to_have_text('Click or hold to record your voice')
        reports['microphone-error-accessibility'] = audit(page, arguments.axe_path)
        page.locator('#clearRequestBtn').click()
        expect(page.locator('#complaint-text')).to_have_value('')
        expect(page.locator('#aiPreview')).to_be_hidden()
        page.emulate_media(reduced_motion='reduce')
        page.goto(arguments.base_url, wait_until='domcontentloaded')
        expect(page.locator('.public-metric-value').last).to_have_text(re.compile(r'%$'))
        reports['reduced-motion'] = page.locator('#dfcxWidgetToggleBtn i').evaluate('node => getComputedStyle(node).animationName')
        assert reports['reduced-motion'] == 'none'
        assert not errors, errors
        assert not unexpected_writes, unexpected_writes
        reports['browser-errors'] = errors
        reports['mocked-write-endpoints'] = sorted({entry['path'] for entry in payloads})
        reports['functional-checks'] = 'Passed: languages, location, routing, tracking, attachments, camera, typed and voice submission, errors, success, modal focus, navigation, reduced motion'
        (output / 'checks.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
        violations = {key: value for key, value in reports.items() if 'accessibility' in key and isinstance(value, list) and value}
        print(json.dumps({'functional_checks': reports['functional-checks'], 'accessibility_violations': violations}, indent=2), flush=True)
        browser.close()
        assert not violations, 'Accessibility violations recorded in scratch/public-review/checks.json'


if __name__ == '__main__':
    main()
