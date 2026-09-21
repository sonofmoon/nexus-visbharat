"""Browser acceptance on an isolated copy; never alters the demo or operational DB."""
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.prepare_ministry_pilot import configuration,DEST
from visbharat import create_app
from werkzeug.serving import make_server,WSGIRequestHandler
from playwright.sync_api import sync_playwright,expect


class QuietHandler(WSGIRequestHandler):
    def log_request(self,*args,**kwargs):pass


OUT=ROOT/'docs/evaluation/ministry-pilot-browser';OUT.mkdir(parents=True,exist_ok=True)
result={'checks':[],'tabs':{},'page_errors':[],'failed_api':[]}
with tempfile.TemporaryDirectory() as td:
    path=Path(td)/'pilot-ui.db'
    with sqlite3.connect(DEST) as source,sqlite3.connect(path) as target:source.backup(target)
    source.close();target.close()
    app=create_app({**configuration(),'DATABASE_PATH':str(path),'LOCAL_EVALUATION_WORKER':False})
    server=make_server('127.0.0.1',5012,app,threaded=True,request_handler=QuietHandler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
      with sync_playwright() as pw:
        browser=pw.chromium.launch(headless=True)
        page=browser.new_page(viewport={'width':1440,'height':1000})
        page.on('pageerror',lambda e:result['page_errors'].append(str(e)))
        page.on('response',lambda r:result['failed_api'].append({'path':r.url.split('5012')[-1].split('?')[0],'status':r.status}) if '/api/' in r.url and r.status>=400 else None)
        started=time.perf_counter();page.goto('http://127.0.0.1:5012/pilot')
        expect(page.locator('#pilotContent')).to_contain_text('Citizen reports',timeout=30000)
        result['first_content_ms']=round((time.perf_counter()-started)*1000)
        for tab in ('overview','intake','requests','proposals','readiness','settings'):
            page.locator('[data-tab="'+tab+'"]').click()
            expect(page.locator('#pilotContent > [role="status"]')).to_have_count(0,timeout=30000)
            assert 'Workspace unavailable' not in page.locator('#pilotContent').inner_text()
            page.locator('#pilotContent').screenshot(path=str(OUT/(tab+'-desktop.png')))
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),tab
            page.locator('#pilotContent').screenshot(path=str(OUT/(tab+'-mobile.png')))
            page.set_viewport_size({'width':1440,'height':1000})
            result['tabs'][tab]='desktop + mobile passed'
        for role,district in [('vellore','Vellore'),('tirupati','Tirupati')]:
            page.locator('#pilotRole').select_option(role)
            page.locator('[data-tab="requests"]').click()
            expect(page.locator('#pilotContent tbody')).to_contain_text(district)
            assert ('Tirupati' if district=='Vellore' else 'Vellore') not in page.locator('#pilotContent tbody').inner_text()
        result['checks'].append('Both district officers see only their assigned reports')
        page.locator('#pilotRole').select_option('admin')
        examples=[('vellore-1','ta','எங்கள் தெருவில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது.'),('tirupati-1','te','మా వీధిలో తాగునీరు రోజుకు ఒక గంట మాత్రమే వస్తోంది.')]
        for loc,lang,text in examples:
            page.locator('[data-tab="intake"]').click();form=page.locator('[data-form="intake"]')
            form.locator('[name=location_id]').select_option(loc);form.locator('[name=language]').select_option(lang)
            form.locator('[name=text]').fill(text);form.locator('[name=consent_granted]').check()
            form.get_by_role('button',name='Save request').click()
            expect(page.locator('#pilotInspector')).to_contain_text('Ticket saved')
            rid=page.locator('#pilotInspector strong').inner_text()
            page.locator('[data-tab="requests"]').click()
            page.locator('[data-action=process][data-id="'+rid+'"]').click()
            expect(page.locator('#pilotContent')).to_contain_text('manual review')
            page.locator('[data-action=review-request][data-id="'+rid+'"]').click()
            form=page.locator('[data-form=review]')
            form.locator('[name=translated_text]').fill('Our street receives drinking water for only one hour every day.')
            form.locator('[name=category]').select_option('Water Supply');form.locator('[name=status]').select_option('Acknowledged')
            form.locator('[name=reason]').fill('Synthetic language review for the jury demonstration; no live model output claimed.')
            form.get_by_role('button',name='Save officer review').click()
            expect(page.locator('#pilotInspector')).to_be_hidden()
        result['checks'].append('Tamil and Telugu intake -> durable ticket -> provider unavailable -> human-reviewed English/classification')
        page.locator('[data-tab="proposals"]').click()
        page.locator('[data-action=inspect-proposal]').first.click()
        expect(page.locator('#pilotInspector')).to_contain_text('linked citizen reports')
        page.locator('[data-action=close]').click()
        page.locator('[data-action=draft]').first.click()
        expect(page.locator('#pilotContent [data-action=engineering]').first).to_be_visible()
        page.locator('[data-action=engineering]').first.click()
        form=page.locator('[data-form=engineering]')
        form.locator('[name=engineering_cost_lakh]').fill('20');form.locator('[name=beneficiary_count]').fill('100')
        form.locator('[name=evidence_reference]').fill('urn:nvb:demo:browser-engineering')
        form.get_by_role('button',name='Record review').click()
        expect(page.locator('#pilotInspector')).to_be_hidden()
        page.locator('[data-action=approve]').first.click()
        expect(page.locator('#pilotMessage')).to_contain_text('Decision recorded')
        result['checks'].append('Evidence inspection -> draft -> engineering review -> explicit approval')
        page.locator('[data-tab=readiness]').click()
        page.locator('[data-action=gate][data-id=restore_drill]').click();form=page.locator('[data-form=gate]')
        form.locator('[name=status]').select_option('rehearsed');form.locator('[name=reference_uri]').fill('urn:nvb:demo:browser-restore')
        form.locator('[name=notes]').fill('Synthetic rehearsal of readiness evidence entry; no cloud recovery performed.')
        form.get_by_role('button',name='Save gate evidence').click();expect(page.locator('#pilotInspector')).to_be_hidden()
        expect(page.locator('#pilotContent')).to_contain_text('rehearsed')
        result['checks'].append('Launch evidence saved with synthetic status')
        page.locator('[data-tab=overview]').click();page.locator('[data-action=suite-analyst]').click()
        expect(page.locator('#wbScope')).to_be_visible(timeout=30000)
        result['checks'].append('Analyst suite deep link')
        page.goto('http://127.0.0.1:5012/pilot');page.locator('#pilotRole').select_option('auditor')
        page.locator('[data-tab=overview]').click();page.locator('[data-action=suite-auditor]').click()
        expect(page.locator('#awContent')).to_contain_text('Choose a proposal',timeout=30000)
        result['checks'].append('Auditor suite deep link')
        browser.close()
        (OUT/'result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2))
        assert not result['page_errors'],result['page_errors']
        assert not result['failed_api'],result['failed_api']
    finally:server.shutdown();thread.join(timeout=10)
