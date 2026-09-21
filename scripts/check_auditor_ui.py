"""Browser acceptance, exports and review actions on the labelled synthetic case."""
import csv
import json
from pathlib import Path
import time
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'scratch/auditor-implementation/browser';OUT.mkdir(parents=True,exist_ok=True)
result={'errors':[],'tabs':{},'api_paths':[],'checks':[]}
with sync_playwright() as p:
    browser=p.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
    page.add_init_script("sessionStorage.setItem('nvb_active_role','auditor')")
    page.on('pageerror',lambda e:result['errors'].append(str(e)))
    page.on('request',lambda r:result['api_paths'].append(r.url.split('5000')[-1]) if r.url.startswith('http://127.0.0.1:5000/api/') else None)
    started=time.perf_counter();page.goto('http://127.0.0.1:5000/dashboard?workspace=auditor',wait_until='domcontentloaded',timeout=60000)
    expect(page.locator('#awContent')).to_contain_text('Choose a proposal',timeout=30000)
    result['first_useful_content_ms']=round((time.perf_counter()-started)*1000,1)
    expect(page.locator('#awMetrics')).to_contain_text('12,500')
    page.locator('#awProjectSearch').fill('Karur');page.locator('#awScopeForm button[type=submit]').click()
    expect(page.locator('#awContent')).to_contain_text('Karur',timeout=15000)
    seed=json.loads((ROOT/'docs/evaluation/auditor-demo.json').read_text())['projects'][0]
    page.evaluate('(pid)=>NVBAuditor.openProject(pid)',seed['project_id'])
    expect(page.locator('#awContent')).to_contain_text('Project evidence and reviews',timeout=30000)
    for tab in ('evidence','delivery','outcomes','events','consent','security'):
        page.locator('[data-aw-tab="'+tab+'"]').click()
        expect(page.locator('#awContent .aw-loading')).to_have_count(0,timeout=30000)
        page.locator('#awContent').screenshot(path=str(OUT/(tab+'-desktop.png')))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),tab
        page.locator('#awContent').screenshot(path=str(OUT/(tab+'-mobile.png')))
        result['tabs'][tab]='desktop and 390px rendering passed'
        page.set_viewport_size({'width':1440,'height':1000})
    result['checks'].append('six tabs and mobile layout')
    page.locator('[data-aw-tab="evidence"]').focus()
    page.keyboard.press('ArrowRight')
    expect(page.locator('[data-aw-tab="delivery"]')).to_be_focused()
    expect(page.locator('[data-aw-tab="delivery"]')).to_have_attribute('aria-selected','true')
    page.keyboard.press('Home')
    expect(page.locator('[data-aw-tab="evidence"]')).to_be_focused()
    expect(page.locator('#awQuality')).to_be_visible(timeout=15000)
    page.locator('#awQuality > summary').click()
    page.locator('#awQuality details > summary').first.click()
    expect(page.locator('#awQuality')).to_contain_text('Accuracy (95% interval)')
    expect(page.locator('#awQuality')).to_contain_text('Word error rate')
    expect(page.locator('#awQuality')).to_contain_text('Not available')
    assert '"accuracy"' not in page.locator('#awQuality').inner_text()
    page.set_viewport_size({'width':390,'height':844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
    page.locator('#awQuality').screenshot(path=str(OUT/'evaluation-mobile.png'))
    page.set_viewport_size({'width':1440,'height':1000})
    page.locator('#themeSwitch').click()
    expect(page.locator('body')).to_have_attribute('data-theme','dark')
    page.locator('#awContent').screenshot(path=str(OUT/'evidence-dark.png'))
    page.locator('#themeSwitch').click()
    result['checks'].append('keyboard tabs, dark layout and readable evaluation tables')
    page.locator('[data-aw-tab="outcomes"]').click();expect(page.locator('#awContent')).to_contain_text('illustrative',timeout=15000)
    page.locator('[data-aw-tab="delivery"]').click();expect(page.locator('#awContent')).to_contain_text('50 percentage points',timeout=15000)
    expect(page.locator('#awContent')).to_contain_text('cannot freeze or approve funds')
    assert 'null' not in page.locator('#awContent').inner_text()
    result['checks'].append('synthetic outcomes and recomputed same-milestone discrepancy')
    # Exercise the existing synthetic case and reopen it for the jury walkthrough.
    for action,state in [('start','investigating'),('request_evidence','awaiting_evidence'),('resolve','resolved'),('reopen','open')]:
        page.locator('[data-aw-action="case"][data-id="'+seed['case_id']+'"]').click()
        form=page.locator('[data-aw-form="case-action"]')
        expect(form).to_be_visible(timeout=15000)
        form.locator('[name="action"]').select_option(action)
        form.locator('[name="notes"]').fill('Synthetic browser acceptance: '+action+'; role simulation only, no real site visit or financial action.')
        if action=='resolve': form.locator('[name="evidence_reference"]').fill('urn:nvb:demo:browser-review')
        with page.expect_response(lambda r:'/cases/'+seed['case_id']+'/actions' in r.url and r.request.method=='POST') as response:
            form.locator('button').click()
        payload=response.value.json()
        assert response.value.ok and payload['case']['status']==state,payload
        expect(page.locator('#awContent .aw-loading')).to_have_count(0,timeout=15000)
    result['checks'].append('synthetic case start, evidence request, independent closure and reopening through forms')
    page.locator('[data-aw-tab="evidence"]').click();expect(page.locator('[data-aw-action="export-project"]')).to_be_visible(timeout=15000)
    with page.expect_download() as download: page.locator('[data-aw-action="export-project"]').click()
    download.value.save_as(str(OUT/'project-pack.json'))
    pack=json.loads((OUT/'project-pack.json').read_text());assert pack['record']['detail']['requests']==[]
    result['checks'].append('redacted evidence pack with frozen manifest')
    page.locator('#awClear').click();page.locator('#filterState').select_option('Telangana')
    expect(page.locator('#awMetrics')).to_contain_text('2,500',timeout=30000)
    expect(page.locator('#awContent')).not_to_contain_text('Karur',timeout=20000)
    page.locator('[data-aw-tab="events"]').click();expect(page.locator('#awContent')).to_contain_text('Search the complete audit history',timeout=15000)
    page.locator('[data-aw-action="integrity"]').click();expect(page.locator('#awInspector')).to_contain_text('legacy unverified',timeout=20000)
    result['checks'].append('state scope and explicit legacy integrity boundary')
    with page.expect_download() as download: page.locator('[data-aw-action="export-events-csv"]').click()
    download.value.save_as(str(OUT/'audit-events.csv'))
    with (OUT/'audit-events.csv').open(encoding='utf-8-sig',newline='') as f:
        exported=list(csv.reader(f))
    assert len(exported)>1 and 'action' in exported[0],exported[:1]
    result['checks'].append('CSV download for the displayed event scope')
    page.evaluate("switchRbacRoleView('admin')")
    with page.expect_response(lambda r:'/api/v2/auditor/snapshot' in r.url) as response:
        page.get_by_role('button',name='Open audit review tools as Admin').click()
    assert response.value.ok
    expect(page.locator('#awContent')).to_contain_text('Search the complete audit history',timeout=15000)
    assert page.evaluate('getActiveRole()')=='admin'
    with page.expect_response(lambda r:'/api/v2/auditor/snapshot' in r.url) as response:
        page.evaluate("switchRbacRoleView('auditor')")
    assert response.value.ok
    expect(page.locator('#awContent')).to_contain_text('Search the complete audit history',timeout=15000)
    result['checks'].append('Admin review tools retain identity; returning to Auditor fetches a fresh snapshot')
    # Intercept an error and ensure old values are removed, not kept as success.
    page.route('**/api/v2/auditor/projects*',lambda r:r.fulfill(status=503,content_type='application/json',body='{"success":false,"error":"Test connection unavailable"}'))
    page.locator('[data-aw-tab="evidence"]').click();expect(page.locator('#awContent')).to_contain_text('Test connection unavailable',timeout=20000)
    expect(page.locator('#awMetrics')).to_be_empty()
    result['checks'].append('error clears stale content')
    assert not any('/api/v1/auditor/ai-telemetry' in u for u in result['api_paths'])
    assert not result['errors'],result['errors']
    browser.close()
(OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in result.items() if k!='api_paths'},indent=2))
