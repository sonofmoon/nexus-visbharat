import base64
import hashlib
import hmac
import json
import time
import pytest
from tests.test_ministry_pilot import MinistryPilotTest
from visbharat.services import pilot, pilot_portal


@pytest.fixture
def env():
    test=MinistryPilotTest();test.setUp()
    try:yield test
    finally:test.doCleanups()


def test_pages_share_unique_navigation_and_workspace_sections(env):
    for path in ('/pilot','/pilot/submit','/pilot/dashboard','/pilot/settings'):
        response=env.client.get(path);assert response.status_code==200
        html=response.get_data(as_text=True)
        assert 'Citizen Portal' in html and 'Citizen Request Submission' in html
        assert html.count('>Settings</a>')==1
        assert 'Officer workspace</a>' not in html and 'Programme setup</button>' not in html
    page=env.client.get('/pilot/dashboard').get_data(as_text=True)
    assert page.count('role="tab"')==4
    assert 'Delivery &amp; Outcomes' in page
    assert 'role="tab"' not in env.client.get('/pilot/settings').get_data(as_text=True)


def test_settings_are_versioned_validated_and_affect_public_form(env):
    response=env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':1,
        'categories':['Water Supply','Road'],'languages':['ta','en'],
        'portal':{'planning':{'budget_lakh':750},'privacy':{'consent_notice':'Reviewed programme processing notice'}}})
    assert response.status_code==200,response.json
    data=env.client.get('/api/v2/pilot/public-config').json
    assert data['categories']==['Water Supply','Road'] and data['languages']==['ta','en']
    assert data['notice']=='Reviewed programme processing notice'
    assert env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':1,'portal':{}}).status_code==400
    assert env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':2,'portal':{'channels':{'telegram':{'address':'javascript:alert(1)'}}}}).status_code==400
    assert env.client.post('/api/v2/pilot/settings',headers=env.vellore,json={'version':2,'portal':{}}).status_code==403
    assert env.submit(category='Education').status_code==400


def test_private_attachments_are_atomic_deduplicated_and_scoped(env):
    evidence=[{'name':'inspection.pdf','mime_type':'application/pdf','data':base64.b64encode(b'%PDF-1.4 test evidence').decode()}]
    code='a-private-client-receipt-with-32-characters'
    first=env.submit(evidence=evidence,tracking_secret=code);assert first.status_code==202,first.json
    rid=first.json['request_id']
    replay=env.submit(evidence=evidence,tracking_secret=code)
    assert replay.json['request_id']==rid and replay.json['tracking_secret']==code
    response=env.client.get('/api/v2/pilot/requests/'+rid+'/evidence',headers=env.vellore)
    assert len(response.json['items'])==1
    sha=response.json['items'][0]['sha256']
    download=env.client.get('/api/v2/pilot/requests/'+rid+'/evidence/'+sha,headers=env.vellore)
    assert download.data==b'%PDF-1.4 test evidence'
    assert download.headers['X-Content-Type-Options']=='nosniff'
    other=env.submit(location='tirupati-1',key='another-location-ticket',evidence=evidence)
    assert env.client.get('/api/v2/pilot/requests/'+other.json['request_id']+'/evidence',headers=env.vellore).status_code==404
    assert env.client.post('/api/v2/pilot/portal/evidence',json={'request_id':rid,'tracking_secret':'wrong','evidence':evidence}).status_code==404
    assert env.submit(key='invalid-evidence-ticket',evidence=[{'name':'bad','mime_type':'text/html','data':'eA=='}]).status_code==400


def test_preview_failure_does_not_block_durable_submission(env):
    env.client.get('/pilot/submit')
    with env.client.session_transaction() as session:csrf=session['pilot_csrf']
    result=env.client.post('/api/v2/pilot/portal/classify',json={'text':'Water supply is intermittent','csrf':csrf,'consent_granted':True,'language':'en'})
    assert result.status_code==503 and not result.json['success']
    assert env.submit().status_code==202
    assert env.client.post('/api/v2/pilot/portal/classify',json={'text':'Hello'}).status_code==403


def test_channel_requires_signature_and_verified_receipt_before_home_activation(env):
    env.app.config['PILOT_CHANNEL_TELEGRAM_SECRET']='test-channel-signature'
    r=env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':1,'portal':{'channels':{'telegram':{'enabled':True,'address':'https://t.me/ExamplePilotBot','owner':'District team'}}}})
    assert r.status_code==200,r.json
    def send(data,signature=True):
        raw=json.dumps(data).encode();stamp=str(int(time.time()))
        sig=hmac.new(b'test-channel-signature',stamp.encode()+b'.'+raw,hashlib.sha256).hexdigest()
        return env.client.post('/api/v2/pilot/channels/telegram/webhook',data=raw,content_type='application/json',headers={'X-Pilot-Timestamp':stamp,'X-Pilot-Signature':sig if signature else 'bad'})
    payload={'event_id':'telegram-event-123','location_id':'vellore-1','text':'Drinking water is not available every morning.','language':'en','consent_granted':True}
    assert send(payload,False).status_code==403
    first=send(payload);assert first.status_code==200,first.json
    assert send(payload).json['request_id']==first.json['request_id']
    assert send({**payload,'text':'Different event content must not silently reuse a receipt'}).status_code==400
    channels=env.client.get('/api/v2/pilot/public-config').json['channels']
    assert not next(c for c in channels if c['id']=='telegram')['available']
    assert send({'event_id':'telegram-event-123','action':'delivery_receipt','delivered':True}).status_code==200
    channels=env.client.get('/api/v2/pilot/public-config').json['channels']
    assert next(c for c in channels if c['id']=='telegram')['available']
    with env.app.app_context():assert pilot.rows('SELECT source_channel FROM citizen_requests')[0]['source_channel']=='Telegram'


def test_public_geography_and_enabled_web_channel(env):
    assert env.client.get('/api/v2/pilot/portal/districts?state=Tamil%20Nadu').json['districts']==['Vellore']
    assert env.client.get('/api/v2/pilot/portal/districts?state=Telangana').json['districts']==[]
    env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':1,'portal':{'channels':{'web':{'enabled':False}}}})
    assert env.submit().status_code==403


def test_delivery_rejects_unknown_decision_and_unauthorised_writer(env):
    assert env.client.get('/api/v2/pilot/portal/delivery',headers=env.vellore).json['items']==[]
    assert env.client.post('/api/v2/pilot/portal/delivery/unknown',headers=env.admin,json={'version':0}).status_code==404
    assert env.client.post('/api/v2/pilot/portal/delivery/unknown',headers=env.auditor,json={'version':0}).status_code==403


def test_approved_project_delivery_is_versioned_and_requires_closure_evidence(env):
    rid=env.submit().json['request_id'];assert env.review(rid).status_code==200
    project=env.client.get('/api/v2/analyst/snapshot?budget_lakh=500&capacity=6',headers=env.analyst).json['scenario']['items'][0]['project_id']
    draft=env.client.post('/api/v2/analyst/projects/'+project+'/draft',headers=env.analyst,json={'budget_lakh':500,'capacity':6,'notes':'Synthetic development review for delivery regression test.'})
    did=draft.json['decision']['decision_id']
    env.client.post('/api/v2/analyst/decisions/'+did+'/review',headers=env.admin,json={'engineering_cost_lakh':20,'beneficiary_count':100,'evidence_reference':'urn:nvb:demo:site-survey'})
    assert env.client.post('/api/v1/policy/decisions/'+did+'/approve',headers=env.admin,json={}).status_code==200
    records=env.client.get('/api/v2/pilot/portal/delivery',headers=env.vellore).json['items']
    assert records[0]['decision_id']==did and records[0]['version']==0
    path='/api/v2/pilot/portal/delivery/'+did
    saved=env.client.post(path,headers=env.vellore,json={'version':0,'stage':'In progress','owner':'District engineer','milestone':'Inspect repaired supply line','expenditure_lakh':5,'due_date':'2026-10-01'})
    assert saved.status_code==200,saved.json
    assert env.client.post(path,headers=env.admin,json={'version':0}).status_code==400
    assert env.client.post(path,headers=env.admin,json={'version':1,'stage':'Completed'}).status_code==400
    result=env.client.post(path,headers=env.admin,json={'version':1,'stage':'Completed','evidence_reference':'urn:nvb:demo:inspection','observed':'Supply observed on seven consecutive days'})
    assert result.status_code==200,result.json


def test_google_tools_fail_explicitly_without_provider_connections(env):
    assert env.client.post('/api/v2/pilot/portal/forecast',headers=env.analyst,json={}).status_code==400
    assert env.client.post('/api/v2/pilot/portal/policy-assistance',headers=env.analyst,json={}).status_code==400
    assert env.client.post('/api/v2/pilot/settings',headers=env.admin,json={'version':1,'translation_provider':'unreviewed-external-service'}).status_code==400
