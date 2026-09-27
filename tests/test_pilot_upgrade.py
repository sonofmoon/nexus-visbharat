"""Regression coverage using isolated databases; no provider or government claims."""
import json
import pytest
from tests.test_pilot_portal import env
from visbharat.db import get_db
from visbharat.services import pilot, analyst_workbench as work
from visbharat.services.pilot_examples import EXAMPLES


@pytest.mark.parametrize('indent,separators', [(None,(',',':')),(2,None),(None,None)])
def test_reconstruction_quarantine_and_review_transition(env,indent,separators):
    rid=env.submit().json['request_id']
    with env.app.app_context():
        db=get_db()
        meta={'is_synthetic':True,'reconstruction_status':'synthetic_reconstructed_unreviewed'}
        db.execute('UPDATE citizen_requests SET ai_metadata_json=? WHERE request_id=?',(json.dumps(meta,indent=indent,separators=separators),rid))
        db.execute("UPDATE pilot_requests SET processing_status='human_reviewed' WHERE request_id=?",(rid,))
        db.commit()
    snapshot=env.client.get('/api/v2/analyst/snapshot',headers=env.analyst).json
    assert snapshot['scenario']['total_candidates']==0
    assert env.review(rid).status_code==200
    snapshot=env.client.get('/api/v2/analyst/snapshot',headers=env.analyst).json
    assert snapshot['scenario']['total_candidates']==1
    project=snapshot['scenario']['items'][0]['project_id']
    detail=env.client.get('/api/v2/analyst/projects/'+project,headers=env.analyst).json
    assert detail['capital_request_ids']==[rid]
    with env.app.app_context():
        meta=json.loads(pilot.rows('SELECT ai_metadata_json FROM citizen_requests WHERE request_id=?',(rid,))[0]['ai_metadata_json'])
    assert meta['reconstruction_status']=='synthetic_rehearsal_reviewed'
    assert meta['human_review']['actor'] and meta['is_synthetic']
    assert meta['human_review']['at']


def test_export_excludes_pending_tickets_without_calling_them_emergencies(env):
    one=env.submit().json['request_id'];env.review(one)
    two=env.submit(key='second-pending-example',category='Water Supply').json['request_id']
    p=env.client.get('/api/v2/analyst/snapshot',headers=env.analyst).json['scenario']['items'][0]
    detail=env.client.get('/api/v2/analyst/projects/'+p['project_id'],headers=env.analyst).json
    assert detail['capital_request_ids']==[one]
    assert detail['excluded_review_or_restriction_request_ids']==[two]
    assert detail['excluded_emergency_request_ids']==[]
    export=env.client.get('/api/v2/analyst/projects/'+p['project_id']+'/export',headers=env.analyst).json
    assert export['record']['request_ids']==[one]


@pytest.mark.parametrize('example',EXAMPLES,ids=lambda e:e['id'])
def test_each_district_runs_intake_review_decision_and_redacted_export(env,example):
    received=env.submit(location=example['location_id'],key='prepared-'+example['id'],language=example['language'],text=example['text'],category=example['category'])
    assert received.status_code==202,received.json
    rid=received.json['request_id']
    if example['district']!='Vellore':
        assert env.client.post('/api/v2/pilot/requests/'+rid+'/review',headers=env.vellore,json={'version':1}).status_code==404
    reviewed=env.client.post('/api/v2/pilot/requests/'+rid+'/review',headers=env.analyst,json={
        'version':1,'original_text':example['text'],'translated_text':example['translation'],
        'category':example['category'],'urgency':'Routine','status':'Acknowledged',
        'reason':'Prepared fictional scenario; automated officer-role rehearsal, not field verification.'})
    assert reviewed.status_code==200,reviewed.json
    plan=env.client.get('/api/v2/analyst/snapshot',headers=env.analyst).json
    candidate=plan['scenario']['items'][0]
    assert candidate['district']==example['district'] and candidate['category']==example['category']
    project=candidate['project_id']
    draft=env.client.post('/api/v2/analyst/projects/'+project+'/draft',headers=env.analyst,json={'notes':'Fictional cross-district judging rehearsal; no government approval.'})
    assert draft.status_code==200,draft.json
    did=draft.json['decision']['decision_id']
    reviewed=env.client.post('/api/v2/analyst/decisions/'+did+'/review',headers=env.admin,json={'engineering_cost_lakh':20,'beneficiary_count':100,'evidence_reference':'urn:nvb:demo:fictional-survey'})
    assert reviewed.status_code==200,reviewed.json
    assert env.client.post('/api/v1/policy/decisions/'+did+'/approve',headers=env.admin,json={}).status_code==200
    exported=env.client.post('/api/v2/auditor/exports',headers=env.auditor,json={'kind':'project','project_id':project})
    assert exported.status_code==200,exported.json
    dossier=env.client.get('/api/v2/auditor/exports/'+exported.json['snapshot_id'],headers=env.auditor)
    assert dossier.status_code==200
    assert received.json['tracking_secret'] not in dossier.get_data(as_text=True)
    assert example['text'] not in dossier.get_data(as_text=True)


def test_prepared_examples_are_visible_only_for_synthetic_programmes(env):
    html=env.client.get('/pilot').get_data(as_text=True)
    assert all('example='+item['id'] in html for item in EXAMPLES)
    with env.app.app_context():
        get_db().execute("UPDATE pilot_programmes SET data_mode='operational'");get_db().commit()
    html=env.client.get('/pilot').get_data(as_text=True)
    assert 'example=bengaluru-road' not in html
