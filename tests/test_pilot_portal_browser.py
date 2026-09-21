"""Headless application regression tests; never connects to a user's browser."""
import threading
import pytest
from werkzeug.serving import make_server
from tests.test_pilot_portal import env


@pytest.fixture
def browser_page(env):
    playwright=pytest.importorskip('playwright.sync_api')
    with playwright.sync_playwright() as runtime:
        try:browser=runtime.chromium.launch(headless=True)
        except playwright.Error:pytest.skip('Chromium test runtime is not installed')
        server=make_server('127.0.0.1',0,env.app,threaded=True)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        context=browser.new_context(viewport={'width':1440,'height':1000})
        context.route('https://**/*',lambda route:route.abort())
        page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
        try:yield page,f'http://127.0.0.1:{server.server_port}',env
        finally:
            context.close();browser.close();server.shutdown();thread.join(timeout=10)
            assert not errors,errors


def test_citizen_can_submit_evidence_and_track_private_receipt(browser_page):
    from playwright.sync_api import expect
    page,url,env=browser_page
    page.goto(url+'/pilot/submit?district=Vellore&language=en')
    expect(page.locator('#district')).to_have_value('Vellore')
    page.locator('#pilotLocation').select_option('vellore-1')
    page.locator('#complaint-text').fill('Drinking water is available for only one hour each morning.')
    page.locator('#photo-attachments').set_input_files({'name':'photo.png','mimeType':'image/png','buffer':b'\x89PNG\r\n\x1a\nregression-evidence'})
    page.locator('#consentGranted').check()
    page.locator('#submitBtn').click()
    expect(page.locator('#successMessage')).to_be_visible(timeout=15000)
    rid=page.locator('#complaintId').inner_text()
    assert rid.startswith('NVB-')
    assert len(page.locator('#pilotReceiptSecret').inner_text())>=32
    page.locator('#trackStatusBtn').click()
    expect(page.locator('#trackResultContainer')).to_contain_text(rid)
    assert len(env.client.get('/api/v2/pilot/requests/'+rid+'/evidence',headers=env.admin).json['items'])==1


def test_officer_sections_and_admin_settings_work_without_duplicate_navigation(browser_page):
    from playwright.sync_api import expect
    page,url,env=browser_page
    page.goto(url+'/pilot/dashboard')
    expect(page.get_by_role('heading',name='What needs attention today?')).to_be_visible()
    page.get_by_role('tab',name='Requests & Review').click()
    expect(page.get_by_role('heading',name='Requests and officer review')).to_be_visible()
    page.get_by_role('tab',name='Development & Policy').click()
    expect(page.get_by_role('heading',name='Development & Policy')).to_be_visible()
    page.get_by_role('tab',name='Delivery & Outcomes').click()
    expect(page.get_by_role('heading',name='Delivery & Outcomes')).to_be_visible()
    page.get_by_role('link',name='Settings',exact=True).click()
    expect(page.get_by_text('Read-only settings.',exact=False)).to_be_visible()
    page.locator('#pilotRole').select_option('admin')
    expect(page.locator('[name="title"]')).to_be_enabled()
    page.locator('[name="title"]').fill('Vellore and Tirupati District Development Pilot')
    page.get_by_role('button',name='Save programme',exact=True).click()
    expect(page.locator('#pilotMessage')).to_contain_text('Saved with an audit record.')
    assert env.client.get('/api/v2/pilot/public-config').json['title']=='Vellore and Tirupati District Development Pilot'
    assert page.get_by_role('link',name='Settings',exact=True).count()==1


def test_mobile_navigation_and_district_selection(browser_page):
    from playwright.sync_api import expect
    page,url,_=browser_page;page.set_viewport_size({'width':390,'height':844})
    page.goto(url+'/pilot')
    page.get_by_role('button',name='Open navigation').click()
    page.get_by_role('link',name='State Policy Dashboard',exact=True).click()
    expect(page.get_by_role('heading',name='What needs attention today?')).to_be_visible()
    page.get_by_role('button',name='Open navigation').click()
    page.get_by_role('link',name='Citizen Portal',exact=True).click()
    page.locator('.pilot-district-card').filter(has_text='Tirupati').click()
    expect(page.locator('#district')).to_have_value('Tirupati')
    expect(page.locator('#language')).to_have_value('te')
