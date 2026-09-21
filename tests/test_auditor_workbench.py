import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from tests import test_analyst_workbench as fixture
from visbharat.blueprints.auditor import auditor_bp
from visbharat.db import get_db
from visbharat.audit import write_audit_log
from visbharat.log.chain import verify_chain_integrity
from visbharat.services import analyst_workbench as analyst, auditor_evaluation as evaluation


class AuditorWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.f=fixture.AnalystWorkbenchTest();self.f.setUp();self.addCleanup(self.f.doCleanups)
        self.app=self.f.app;self.app.config['SECRET_KEY']='isolated-auditor-test'
        self.app.register_blueprint(auditor_bp);self.client=self.app.test_client()
        self.h={'Authorization':'Bearer auditor-test'};self.admin={'Authorization':'Bearer admin-test'}
        self.pid=analyst.candidate_id('Tamil Nadu','Karur','Ward 1','Water Supply')
        self.app.extensions['google_vertex_client']=Mock(side_effect=AssertionError('No provider during view load'))

    def get(self,path,**kwargs): return self.client.get('/api/v2/auditor'+path,headers=self.h,**kwargs)
    def post(self,path,data,admin=False): return self.client.post('/api/v2/auditor'+path,json=data,headers=self.admin if admin else self.h)
    def case(self,kind='discrepancy'):
        r=self.post('/cases',dict(kind=kind,title='Review delivery discrepancy',notes='Conflicting delivery observations need review',project_id=self.pid,request_id='one',owner='auditor',data_mode='synthetic'))
        self.assertEqual(r.status_code,201,r.get_json());return r.get_json()['case']
    def evidence(self,kind='site',metadata=None,admin=False):
        r=self.post('/projects/'+self.pid+'/evidence',dict(kind=kind,title='Independent site observation',source_uri='urn:nvb:demo:site',observed_at='2026-01-03',data_mode='synthetic',metadata=metadata or {'milestone':'M1','period':'2026-01','observed_progress_pct':0}),admin)
        self.assertEqual(r.status_code,201,r.get_json());return r.get_json()['evidence']

    def test_authentication_and_clearance_are_server_enforced(self):
        self.assertEqual(self.client.get('/api/v2/auditor/snapshot').status_code,401)
        self.assertEqual(self.client.get('/api/v2/auditor/snapshot',headers={'Authorization':'Bearer analyst-test','X-NVB-Role':'admin'}).status_code,403)
        self.app.config['AUDITOR_USER_STATES']={'auditor':['Telangana']}
        data=self.get('/snapshot').get_json();self.assertEqual(data['counts']['total'],1)
        self.assertEqual(self.get('/snapshot?state=Tamil%20Nadu').status_code,403)
        self.assertEqual(self.get('/projects/'+self.pid).status_code,404)

    def test_snapshot_full_denominators_and_unknown_not_success(self):
        with self.app.app_context():
            for i in range(7): self.f.insert('channel-'+str(i),source_channel='Channel '+str(i))
            get_db().commit()
        data=self.get('/snapshot').get_json()
        self.assertEqual(sum(r['value'] for r in data['distributions']['channels']),12)
        self.assertEqual(len(data['distributions']['channels']),8)
        self.assertEqual(data['consent']['missing_receipts'],12)
        self.assertEqual(data['metadata']['model_calls'],0)
        self.assertIsNone(self.get('/projects/'+self.pid+'/financial').get_json()['frozen_capital_lakh'])

    def test_project_dates_and_zero_scope(self):
        d=self.get('/snapshot?state=Tamil%20Nadu&district=Karur').get_json();self.assertEqual(d['counts']['total'],4)
        d=self.get('/snapshot?date_from=2030-01-01&date_to=2030-01-02').get_json();self.assertEqual(d['counts']['total'],0)
        self.assertIsNone(d['metrics'][-1]['value'])
        self.assertEqual(self.get('/snapshot?date_from=2026-02-03&date_to=2026-01-01').status_code,400)
        detail=self.get('/projects/'+self.pid).get_json();self.assertEqual(detail['project']['project_id'],self.pid);self.assertFalse(detail['ready'])

    def test_case_actions_persist_and_require_independent_closure(self):
        case=self.case();cid=case['case_id']
        d=self.post('/cases/'+cid+'/actions',{'version':1,'action':'start','notes':'Assigned records are now being inspected'});self.assertEqual(d.status_code,200)
        stale=self.post('/cases/'+cid+'/actions',{'version':1,'action':'request_evidence','notes':'Request site confirmation from engineer'});self.assertEqual(stale.status_code,409)
        payload={'version':2,'action':'resolve','notes':'Independent field evidence confirms the correction','evidence_reference':'urn:nvb:demo:completion'}
        self.assertEqual(self.post('/cases/'+cid+'/actions',payload).status_code,400)
        self.assertEqual(self.post('/cases/'+cid+'/actions',payload,admin=True).status_code,200)
        persisted=self.get('/cases/'+cid).get_json()['case'];self.assertEqual(persisted['status'],'resolved');self.assertEqual(len(persisted['events']),3)
        self.assertEqual(self.get('/cases?state=Telangana').get_json()['total'],0)

    def test_case_request_must_belong_to_project(self):
        data=dict(title='Review a mismatched ticket',kind='discrepancy',project_id=self.pid,request_id='other',notes='Review source ticket belonging to a different district')
        self.assertEqual(self.post('/cases',data).status_code,400)

    def test_evidence_zero_and_independent_review(self):
        e=self.evidence();self.assertEqual(e['metadata']['observed_progress_pct'],0)
        payload={'version':1,'status':'human_reviewed','notes':'Field observation reviewed against the dated site record'}
        self.assertEqual(self.post('/evidence/'+e['evidence_id']+'/review',payload).status_code,400)
        self.assertEqual(self.post('/evidence/'+e['evidence_id']+'/review',payload,admin=True).status_code,200)
        data=self.get('/projects/'+self.pid+'/financial').get_json();self.assertEqual(data['records'][0]['observed_progress_pct'],0)

    def test_milestone_comparison_matches_period_and_mode(self):
        self.evidence(metadata={'milestone':'M1','period':'2026-01','observed_progress_pct':0})
        self.evidence('milestone',{'milestone':'M1','period':'2026-01','certified_progress_pct':65})
        self.evidence('milestone',{'milestone':'M2','period':'2026-01','certified_progress_pct':80})
        data=self.get('/projects/'+self.pid+'/financial').get_json();self.assertEqual(len(data['discrepancies']),1)
        self.assertEqual(data['discrepancies'][0]['difference_pp'],65)

    def test_reviewed_outcomes_remain_noncausal_and_synthetic(self):
        for phase,value,date in [('before',2,'2026-01-01'),('after',8,'2026-01-05')]:
            r=self.post('/projects/'+self.pid+'/evidence',{'kind':'outcome','title':'Water supply measurement','source_uri':'urn:nvb:demo:water','observed_at':date,'data_mode':'synthetic',
                'metadata':{'measure':'Hours of water','unit':'hours/day','catchment':'Ward 1','phase':phase,'value':value,'sample_size':20}})
            eid=r.get_json()['evidence']['evidence_id']
            self.post('/evidence/'+eid+'/review',{'version':1,'status':'human_reviewed','notes':'Illustrative measurements reviewed for demonstration consistency'},admin=True)
        data=self.get('/projects/'+self.pid+'/outcomes').get_json();self.assertEqual(data['observations'][0]['change'],6)
        self.assertFalse(data['causal_claim']);self.assertEqual(data['observations'][0]['status'],'illustrative')

    def test_consent_current_state_and_restriction(self):
        payload={'request_id':'one','purpose':'request_processing','legal_basis':'consent','event_state':'granted','notice_version':'v1','receipt_reference':'urn:nvb:demo:identity','reason':'Verified identity and explicit consent for this purpose'}
        self.assertEqual(self.post('/consent/actions',payload).status_code,201)
        payload['event_state']='withdrawn';self.assertEqual(self.post('/consent/actions',payload).status_code,201)
        d=self.get('/consent?request_id=one').get_json();self.assertEqual(d['items'][0]['event_state'],'withdrawn');self.assertTrue(d['items'][0]['is_current']);self.assertFalse(d['items'][1]['is_current'])
        with self.app.app_context(): self.assertEqual(get_db().execute("SELECT state FROM auditor_processing_restrictions WHERE request_id='one'").fetchone()['state'],'restricted')

    def test_consent_rejects_string_boolean(self):
        from visbharat.services.governance import record_consent_event
        with self.app.app_context():
            with self.assertRaises(ValueError): record_consent_event('one','web','request_processing','false')

    def test_withdrawal_excludes_future_planning_but_preserves_audit(self):
        payload={'request_id':'one','purpose':'request_processing','legal_basis':'consent','event_state':'withdrawn','notice_version':'v1','receipt_reference':'urn:nvb:demo:identity','reason':'Verified identity and explicit withdrawal for this purpose'}
        self.post('/consent/actions',payload)
        with self.app.app_context():
            candidate=next(p for p in analyst.candidates(analyst.scope_from({})) if p['project_id']==self.pid)
            self.assertEqual(candidate['reports'],1)
        self.assertEqual(self.get('/projects/'+self.pid).get_json()['detail']['total_requests'],2)

    def test_evaluation_job_persists_and_reads_do_not_recompute(self):
        from flask import g
        data=json.loads((__import__('pathlib').Path(__file__).resolve().parents[1]/'static/data/auditor-evaluation-example.json').read_text())
        with self.app.test_request_context():
            g.current_user={'id':3,'name':'auditor','role':'auditor'}
            from visbharat.services.auditor_workbench import scope_from
            job=evaluation.enqueue(data,scope_from({}))
        evaluation.run_pending(self.app)
        first=self.get('/evaluations').get_json()['items'][0]
        second=self.get('/evaluations').get_json()['items'][0]
        self.assertEqual(first['job_id'],job['job_id']);self.assertEqual(first['status'],'completed')
        self.assertEqual(first['result'],second['result']);self.assertEqual(first['completed_at'],second['completed_at'])

    def test_hash_chain_checks_content_order_and_tail(self):
        with self.app.test_request_context():
            for i in range(3): write_audit_log('auditor','review','project',self.pid,{'index':i})
            self.assertTrue(verify_chain_integrity(limit=1)['valid'])
            get_db().execute("UPDATE audit_logs SET resource_id='altered' WHERE chain_seq=2");get_db().commit()
            self.assertFalse(verify_chain_integrity()['protected_valid'])

    def test_hash_chain_detects_missing_head(self):
        with self.app.test_request_context():
            write_audit_log('auditor','review','project',self.pid)
            get_db().execute('DELETE FROM audit_logs WHERE chain_seq=1');get_db().commit()
            self.assertFalse(verify_chain_integrity()['valid'])

    def test_unsigned_legacy_is_preserved_not_certified(self):
        from visbharat.services.auditor_schema import migrate
        with self.app.test_request_context():
            get_db().execute("INSERT INTO audit_logs(actor,action,resource_type,created_at) VALUES('old','old','legacy','2025-01-01')")
            get_db().execute('DELETE FROM auditor_chain_state');get_db().commit();migrate()
            write_audit_log('auditor','review','project',self.pid)
            result=verify_chain_integrity();self.assertTrue(result['protected_valid']);self.assertFalse(result['valid']);self.assertEqual(result['legacy_unverified'],1)

    def test_concurrent_append_has_no_forks(self):
        def write(i):
            with self.app.test_request_context(): write_audit_log('worker','concurrent','test',str(i))
        with ThreadPoolExecutor(max_workers=4) as pool: list(pool.map(write,range(20)))
        with self.app.app_context():
            result=verify_chain_integrity();self.assertTrue(result['valid']);self.assertEqual(result['count'],20)

    def test_history_search_exports_same_snapshot_after_new_events(self):
        with self.app.test_request_context():
            write_audit_log('auditor','old_special_action','citizen_request','one')
            for i in range(120): write_audit_log('system','newer','citizen_request','two')
        data=self.get('/events?search=old_special_action').get_json();self.assertEqual(data['total'],1)
        with self.app.test_request_context(): write_audit_log('auditor','old_special_action','citizen_request','one')
        r=self.post('/exports',{'kind':'events','search':'old_special_action','snapshot':data['snapshot']})
        self.assertEqual(r.status_code,200,r.get_json());sid=r.get_json()['snapshot_id']
        pack=self.get('/exports/'+sid).get_json();self.assertEqual(pack['record']['total'],1)
        self.assertEqual(self.client.get('/api/v2/auditor/exports/'+sid,headers=self.admin).status_code,404)

    def test_event_limits_and_csv_formula_safety(self):
        from visbharat.blueprints.auditor import csv_cell
        self.assertEqual(self.get('/events?limit=-1').status_code,400)
        self.assertEqual(self.get('/events?limit=abc').status_code,400)
        self.assertEqual(csv_cell(' =SUM(A1)'),"' =SUM(A1)")
        with self.app.test_request_context(): write_audit_log('<b>literal</b>','view','citizen_request','one')
        d=self.get('/events').get_json();self.assertEqual(d['items'][0]['actor'],'<b>literal</b>')

    def test_security_read_events_are_not_incidents(self):
        with self.app.test_request_context(): write_audit_log('admin','ivr_callback_alert_history_viewed','operations')
        self.assertEqual(self.get('/security').get_json()['total'],0)
        self.client.get('/api/v2/auditor/snapshot')
        d=self.get('/security').get_json();self.assertEqual(d['total'],1);self.assertEqual(d['items'][0]['detector'],'authentication_missing')

    def test_redacted_pack_excludes_citizen_text(self):
        d=self.post('/exports',{'kind':'project','project_id':self.pid}).get_json()
        pack=self.get('/exports/'+d['snapshot_id']).get_json();serialized=json.dumps(pack)
        self.assertNotIn('Citizen example',serialized);self.assertEqual(pack['record']['detail']['requests'],[])
        validated=self.post('/exchange/validate',pack)
        self.assertEqual(validated.status_code,200,validated.get_json());self.assertFalse(validated.get_json()['persisted'])
        pack['record']['ready']=not pack['record']['ready']
        self.assertEqual(self.post('/exchange/validate',pack).status_code,400)

    def test_legacy_causal_get_never_seeds_or_claims_impact(self):
        r=self.client.get('/api/v1/auditor/did-proof?district=unknown',headers=self.h)
        self.assertEqual(r.status_code,200);self.assertIsNone(r.get_json()['did_proof']['causal_lift'])
        self.assertFalse(r.get_json()['did_proof']['statistically_significant'])

    def test_legacy_dispatch_requires_role(self):
        r=self.client.post('/api/v1/anti-capture/dispatch-audit',json={'user_id':'admin'})
        self.assertEqual(r.status_code,401)

    def test_measured_evaluation_zero_recall_and_missing_audio(self):
        data={'model_version':'test','dataset_version':'test','label_status':'provisional','samples':[
            {'expected_category':'Road','predicted_category':'Water Supply','expected_urgency':'Emergency','predicted_urgency':'Routine','language':'ta'},
            {'expected_category':'Water Supply','predicted_category':'Water Supply','language':'te'}]}
        result=evaluation.calculate(data);self.assertEqual(result['overall']['emergency_recall'],0)
        self.assertIsNone(result['speech']['wer']);self.assertFalse(result['independently_verified'])


if __name__=='__main__': unittest.main()
