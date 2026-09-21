"""Exercise six Analyst tabs and read-only scenarios; no policy mutations."""
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

OUT=Path('scratch/analyst-implementation/browser');OUT.mkdir(parents=True,exist_ok=True)
result={'errors':[],'tabs':{},'checks':[]}
with sync_playwright() as pw:
    browser=pw.chromium.launch(headless=True)
    page=browser.new_page(viewport={'width':1440,'height':1000},accept_downloads=True)
    page.on('pageerror',lambda e:result['errors'].append(str(e)))
    page.goto('http://127.0.0.1:5000/dashboard',wait_until='domcontentloaded')
    page.wait_for_function('window.NVBAnalyst?.getSnapshot()?.stats.total_complaints === 12500',timeout=60000)
    snapshot=page.evaluate('NVBAnalyst.getSnapshot()')
    assert sum(snapshot['stats']['urgencies'].values())==12500
    assert sum(snapshot['stats']['channels'].values())==12500
    result['checks'].append('full-dataset urgency and channel totals')
    districts={}
    for project in snapshot['scenario']['selected_projects']:
        district=project['district'];districts[district]=districts.get(district,0)+1
    assert max(districts.values())<=2
    page.locator('[data-workbench-tab="projects"]').click()
    page.locator('#wbProjectSearch').fill('Nalgonda')
    page.wait_for_function("NVBAnalyst.getSnapshot().scenario.items.length>0 && NVBAnalyst.getSnapshot().scenario.items.every(p=>p.district==='Nalgonda')")
    assert page.evaluate('NVBAnalyst.getSnapshot().scenario.allocation.selected_ids')==snapshot['scenario']['allocation']['selected_ids']
    page.locator('#wbProjectSearch').fill('')
    page.wait_for_function('NVBAnalyst.getSnapshot().scenario.matching_candidates===NVBAnalyst.getSnapshot().scenario.total_candidates')
    for offset in (50,100,150,200,250):
        page.locator('#wbMoreProjects').click()
        page.wait_for_function('(offset)=>NVBAnalyst.getSnapshot().scenario.offset===offset',arg=offset)
    assert page.evaluate('NVBAnalyst.getSnapshot().scenario.allocation.selected_ids')==snapshot['scenario']['allocation']['selected_ids']
    result['checks'].append('district capacity, full-corpus search, pagination beyond 200 without allocation changes')
    for tab in ('demand','evidence','projects','budget','delivery','outcomes'):
        print('Checking '+tab,flush=True)
        page.locator('[data-workbench-tab="'+tab+'"]').click()
        expect(page.locator('#wb-'+tab)).to_be_visible()
        if tab=='evidence':expect(page.locator('#wbEvidence')).to_contain_text('independently verified',timeout=15000)
        if tab=='delivery':expect(page.locator('#wbDelivery')).to_contain_text('Linked decisions',timeout=15000)
        if tab=='outcomes':expect(page.locator('#wbReadiness')).to_contain_text('Apache',timeout=15000)
        page.locator('#wb-'+tab).screenshot(path=str(OUT/(tab+'.png')))
        page.set_viewport_size({'width':390,'height':844})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth+1'),tab
        result['tabs'][tab]='desktop and 390px layout passed'
        page.set_viewport_size({'width':1440,'height':1000})
    page.locator('[data-workbench-tab="budget"]').click()
    page.locator('#wbBudget').fill('0');page.locator('#wbScenarioForm button[type=submit]').click()
    page.wait_for_function('NVBAnalyst.getSnapshot().scenario.budget_lakh===0')
    assert page.evaluate('NVBAnalyst.getSnapshot().scenario.allocation.selected_count')==0
    expect(page.locator('#wbBudgetResults')).to_contain_text('No projects selected')
    page.locator('#wbBudget').fill('8500')
    page.locator('#wbEquityWeight').evaluate("el=>{el.value='1';el.dispatchEvent(new Event('input',{bubbles:true}))}")
    page.locator('#wbDemandWeight').evaluate("el=>{el.value='0';el.dispatchEvent(new Event('input',{bubbles:true}))}")
    page.locator('#wbGapWeight').evaluate("el=>{el.value='0';el.dispatchEvent(new Event('input',{bubbles:true}))}")
    page.locator('#wbScenarioForm button[type=submit]').click()
    page.wait_for_function('NVBAnalyst.getSnapshot().scenario.weights.equity===1')
    assert page.evaluate('NVBAnalyst.getSnapshot().scenario.delta.ranks_changed')>0
    with page.expect_download() as download:page.locator('#wbExportBrief').click()
    download.value.save_as(str(OUT/'decision-brief.txt'))
    result['checks'].append('zero budget, live weight calculation and cited brief download')
    page.locator('#filterState').select_option('Telangana')
    page.wait_for_function("NVBAnalyst.getSnapshot().scenario.scope.state==='Telangana'")
    d=page.evaluate('NVBAnalyst.getSnapshot()')
    assert d['stats']['total_complaints']==2500
    assert all(p['state']=='Telangana' for p in d['scenario']['items'])
    page.locator('[data-workbench-tab="projects"]').click()
    page.locator('#wbProjects [data-project]').first.click()
    expect(page.locator('#wbProjectDetail')).to_contain_text('Citizen requests',timeout=20000)
    expect(page.locator('#wbProjectDetail')).to_contain_text('NVB-2026')
    page.locator('#wbProjectDialog').screenshot(path=str(OUT/'project-evidence.png'))
    with page.expect_download() as evidence_download:
        page.locator('#wbProjectDetail [data-export-project]').click()
    evidence_download.value.save_as(str(OUT/'project-evidence.json'))
    exported=json.loads((OUT/'project-evidence.json').read_text(encoding='utf-8'))
    assert exported['scenario_id']==d['scenario']['scenario_id']
    assert exported['request_ids'] and exported['provenance']['data_mode']=='synthetic'
    page.locator('#wbCloseDialog').click()
    result['checks'].append('shared state filter, ticket-to-project evidence and scenario-matched portable export')
    page.locator('#filterDistrict').select_option('Nalgonda')
    page.wait_for_function("NVBAnalyst.getSnapshot().scenario.scope.district==='Nalgonda'")
    page.locator('[data-workbench-tab="outcomes"]').click()
    page.locator('#wbDeliveryDate').fill('2099-01-01')
    page.locator('#wbOutcomeForm button[type=submit]').click()
    expect(page.locator('#wbOutcomeResult')).to_contain_text('follow up incomplete',timeout=15000)
    result['checks'].append('future observation window cannot claim improvement')
    page.locator('#userRoleSelector').select_option('admin')
    page.locator('[data-subtab="admin-policy"]').click()
    expect(page.locator('#wbApprovals')).not_to_contain_text('Loading project',timeout=15000)
    result['checks'].append('admin engineering review screen loads')
    browser.close()
(OUT/'results.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
assert not result['errors'],result['errors']
print(json.dumps(result))
