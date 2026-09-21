import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from visbharat import create_app
from visbharat.db import get_db,DbConnectionAdapter
from visbharat.security import hash_api_token,token_last4
from visbharat.services import pilot,pilot_worker


class MinistryPilotTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app=create_app({'TESTING':True,'DATABASE_URL':'','DATABASE_PATH':str(Path(self.tmp.name)/'pilot.db'),
            'DEMO_MODE':True,'SEED_DEMO_DATA':False,'AUTO_MIGRATE':True,'DISABLE_EXTERNAL_SERVICES':True,
            'SECRET_KEY':'isolated-pilot-tests','PILOT_ID':pilot.PILOT_ID,'PILOT_ONLY':True,'PILOT_MODEL_CALLS':False,
            'ADMIN_API_TOKEN':'admin-test','ANALYST_API_TOKEN':'analyst-test','AUDITOR_API_TOKEN':'auditor-test'})
        self.client=self.app.test_client();self.admin={'Authorization':'Bearer admin-test'}
        self.analyst={'Authorization':'Bearer analyst-test'};self.auditor={'Authorization':'Bearer auditor-test'}
        with self.app.app_context():
            pilot.create_default();db=get_db()
            for u in pilot.rows('SELECT id FROM users'):
                db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1)',(pilot.PILOT_ID,u['id'],'',''))
            db.execute("INSERT INTO users(name,api_token,api_token_hash,token_last4,role,created_at) VALUES('Vellore officer',?,?,?,'analyst',?)",
                ('vellore-test',hash_api_token('vellore-test'),token_last4('vellore-test'),pilot.now()))
            uid=pilot.rows("SELECT id FROM users WHERE name='Vellore officer'")[0]['id']
            db.execute('INSERT INTO pilot_memberships VALUES(?,?,?,?,1)',(pilot.PILOT_ID,uid,'Tamil Nadu','Vellore'));db.commit()
        self.vellore={'Authorization':'Bearer vellore-test'}

    def submit(self,location='vellore-1',key='submission-key-00001',**kw):
        return self.client.post('/api/v2/pilot/intake',json={'location_id':location,'language':'en',
            'text':'Drinking water is available for only one hour every morning.','consent_granted':True,**kw},headers={'Idempotency-Key':key})

    def test_durable_intake_replay_and_private_tracking(self):
        first=self.submit();self.assertEqual(first.status_code,202,first.get_json());r=first.get_json()
        second=self.submit();self.assertEqual(second.status_code,200);self.assertEqual(second.get_json()['request_id'],r['request_id'])
        self.assertEqual(self.submit(text='A different report must not reuse the previous key.').status_code,400)
        invalid=self.client.post('/api/v2/pilot/track',json={'request_id':r['request_id'],'tracking_secret':'wrong'})
        self.assertEqual(invalid.status_code,404)
        valid=self.client.post('/api/v2/pilot/track',json={'request_id':r['request_id'],'tracking_secret':r['tracking_secret']})
        self.assertEqual(valid.status_code,200)
        with self.app.app_context():
            self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM pilot_outbox')[0]['n'],1)
            self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM consent_ledger')[0]['n'],1)

    def test_missing_consent_and_unenrolled_locations_are_rejected(self):
        self.assertEqual(self.submit(consent_granted='true').status_code,400)
        self.assertEqual(self.submit(location='Karur').status_code,400)
        with self.app.app_context():self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM citizen_requests')[0]['n'],0)

    def test_geography_is_server_authorised_across_suites(self):
        one=self.submit().get_json();two=self.submit('tirupati-1','submission-key-00002').get_json()
        self.assertEqual(self.client.get('/api/v2/pilot/snapshot',headers=self.vellore).get_json()['stats']['total_complaints'],1)
        self.assertEqual(self.client.get('/api/v2/pilot/requests?state=Andhra%20Pradesh',headers=self.vellore).status_code,403)
        self.assertEqual(self.client.get('/api/v2/analyst/snapshot?district=Tirupati',headers=self.vellore).status_code,403)
        self.assertEqual(self.client.get('/api/v2/auditor/snapshot',headers=self.auditor).get_json()['counts']['total'],2)
        self.assertEqual(self.client.get('/api/complaints',headers=self.vellore).status_code,403)
        self.assertEqual(self.client.get('/api/v2/pilot/requests').status_code,401)
        self.assertEqual(self.client.post('/api/v2/pilot/requests/'+two['request_id']+'/review',json={'version':1},headers=self.vellore).status_code,404)

    def test_worker_outage_retains_ticket_and_requests_review(self):
        r=self.submit().get_json()
        with self.app.app_context():
            result=pilot_worker.run_one();self.assertEqual(result['status'],'manual_review')
            self.assertEqual(pilot_worker.run_one()['status'],'idle')
            self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM pilot_provider_steps')[0]['n'],0)
            self.assertEqual(pilot.rows('SELECT processing_status FROM pilot_requests')[0]['processing_status'],'manual_review')

    def test_provider_step_reuses_completed_result_and_blocks_uncertain_retry(self):
        with self.app.app_context():
            fn=Mock(return_value={'model':'test-model','provider_mode':'test_verified','category':'Water Supply'})
            first=pilot_worker.provider_step('test-id','classify',{'text':'example'},fn)
            self.assertEqual(pilot_worker.provider_step('test-id','classify',{'text':'example'},fn),first)
            self.assertEqual(fn.call_count,1)
            failed=Mock(side_effect=TimeoutError())
            with self.assertRaises(pilot_worker.ReviewRequired):pilot_worker.provider_step('test-id','translate',{},failed)
            with self.assertRaises(pilot_worker.ReviewRequired):pilot_worker.provider_step('test-id','translate',{},failed)
            self.assertEqual(failed.call_count,1)

    def test_review_is_versioned_and_not_an_auditor_self_action(self):
        r=self.submit().get_json();rid=r['request_id']
        data={'version':1,'reason':'Synthetic officer checks the transcript and service request.',
            'original_text':'Drinking water arrives only one hour each day.','translated_text':'Drinking water arrives only one hour each day.',
            'category':'Water Supply','urgency':'Routine','status':'Acknowledged'}
        self.assertEqual(self.client.post('/api/v2/pilot/requests/'+rid+'/review',json=data,headers=self.auditor).status_code,403)
        response=self.client.post('/api/v2/pilot/requests/'+rid+'/review',json=data,headers=self.analyst)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(self.client.post('/api/v2/pilot/requests/'+rid+'/review',json=data,headers=self.analyst).status_code,400)
        with self.app.app_context():
            self.assertEqual(pilot.rows('SELECT processing_status FROM pilot_requests')[0]['processing_status'],'human_reviewed')
            self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM request_lifecycle_events')[0]['n'],1)

    def test_pending_reports_are_excluded_from_capital_candidates(self):
        self.submit()
        response=self.client.get('/api/v2/analyst/snapshot',headers=self.analyst)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.get_json()['scenario']['total_candidates'],0)

    def test_import_respects_assignment_and_source_id_replay(self):
        row={'location_id':'vellore-1','language':'en','source_id':'agency-101','text':'Water supply stops at the end of the street.','consent_granted':True}
        first=self.client.post('/api/v2/pilot/import',json={'records':[row]},headers=self.vellore)
        self.assertEqual(first.status_code,200,first.get_json())
        second=self.client.post('/api/v2/pilot/import',json={'records':[row]},headers=self.vellore)
        self.assertTrue(second.get_json()['items'][0]['reused'])
        row['location_id']='tirupati-1'
        self.assertEqual(self.client.post('/api/v2/pilot/import',json={'records':[row]},headers=self.vellore).status_code,403)

    def test_settings_do_not_turn_synthetic_evidence_into_live_approval(self):
        response=self.client.post('/api/v2/pilot/settings',json={'version':1,'status':'active','data_mode':'operational'},headers=self.admin)
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertEqual(response.get_json()['programme']['status'],'rehearsal')
        self.assertEqual(response.get_json()['programme']['data_mode'],'synthetic')

    def test_postgres_ddl_normalisation_removes_sqlite_autoincrement(self):
        adapter=DbConnectionAdapter(None,'postgres')
        self.assertEqual(adapter._normalize_query('CREATE TABLE test(id INTEGER PRIMARY KEY AUTOINCREMENT)'),
            'CREATE TABLE test(id BIGSERIAL PRIMARY KEY)')

    def test_operational_identity_does_not_accept_embedded_demo_bearer(self):
        self.app.config['DEMO_MODE']=False
        self.assertEqual(self.client.get('/api/v2/pilot/snapshot',headers=self.admin).status_code,401)
        html=self.client.get('/pilot').get_data(as_text=True)
        self.assertNotIn('admin-test',html)

    def test_execution_table_and_options_are_district_scoped(self):
        self.submit();self.submit('tirupati-1','submission-key-00002')
        out=self.client.get('/api/v1/requests?include_options=1',headers=self.vellore)
        self.assertEqual(out.status_code,200,out.get_json())
        self.assertEqual(out.get_json()['total'],1)
        self.assertEqual(out.get_json()['filter_options']['geography'],{'Tamil Nadu':['Vellore']})
        self.assertEqual(self.client.get('/api/v1/requests?district=Tirupati',headers=self.vellore).status_code,403)
        self.assertEqual(self.client.get('/api/districts',headers=self.vellore).get_json()['districts'],['Vellore'])

    def test_reference_files_follow_programme_geography(self):
        out=self.client.get('/api/v2/analyst/evidence',headers=self.analyst)
        self.assertEqual(out.status_code,200,out.get_json())
        for source in out.get_json()['sources']:
            for row in source['sample']:
                self.assertIn(row.get('district'),('Vellore','Tirupati',None,''))

    def test_reauthentication_and_cookie_csrf(self):
        self.submit();self.submit('tirupati-1','submission-key-00002')
        with self.app.app_context():
            self.assertEqual(self.client.get('/api/v2/pilot/snapshot',headers=self.admin).get_json()['stats']['total_complaints'],2)
            self.assertEqual(self.client.get('/api/v2/pilot/snapshot',headers=self.vellore).get_json()['stats']['total_complaints'],1)
            self.assertEqual(self.client.get('/api/v2/pilot/snapshot').status_code,401)
            uid=pilot.rows("SELECT id FROM users WHERE role='admin'")[0]['id']
        import time
        with self.client.session_transaction() as session:session.update(pilot_user_id=uid,pilot_expires=time.time()+120,pilot_csrf='csrf-test')
        self.assertEqual(self.client.post('/api/v2/pilot/settings',json={'version':1}).status_code,403)
        self.assertEqual(self.client.post('/api/v2/pilot/settings',json={'version':1},headers={'X-CSRF-Token':'csrf-test'}).status_code,200)

    def test_launch_evidence_is_versioned_and_synthetic_cannot_certify(self):
        data={'version':1,'status':'rehearsed','reference_uri':'urn:nvb:demo:restore-test','notes':'Synthetic restoration exercise evidence only.'}
        out=self.client.post('/api/v2/pilot/checks/restore_drill',json=data,headers=self.auditor)
        self.assertEqual(out.status_code,200,out.get_json())
        self.assertEqual(self.client.post('/api/v2/pilot/checks/restore_drill',json=data,headers=self.auditor).status_code,400)
        data.update(version=2,status='accepted')
        self.assertEqual(self.client.post('/api/v2/pilot/checks/restore_drill',json=data,headers=self.admin).status_code,400)
        self.assertEqual(self.client.post('/api/v2/pilot/checks/restore_drill',json=data,headers=self.analyst).status_code,403)

    def test_enrolment_does_not_rewrite_historical_catchments(self):
        self.submit()
        data={'version':1,'local_body':'Proposed community review','ward':'New real ward','verification_status':'proposed'}
        self.assertEqual(self.client.post('/api/v2/pilot/locations/vellore-1',json=data,headers=self.admin).status_code,400)
        data['ward']='Demo catchment 1'
        out=self.client.post('/api/v2/pilot/locations/vellore-1',json=data,headers=self.admin)
        self.assertEqual(out.status_code,200,out.get_json())

    def test_assignment_revocation_immediately_denies_access(self):
        with self.app.app_context():uid=pilot.rows("SELECT id FROM users WHERE name='Vellore officer'")[0]['id']
        out=self.client.post('/api/v2/pilot/members',json={'user_id':uid,'state':'Tamil Nadu','district':'Vellore','active':False},headers=self.admin)
        self.assertEqual(out.status_code,200,out.get_json())
        self.assertEqual(self.client.get('/api/v2/pilot/snapshot',headers=self.vellore).status_code,403)

    def review(self,rid):
        out=self.client.get('/api/v2/pilot/requests',headers=self.admin).get_json()
        row=next(r for r in out['items'] if r['request_id']==rid)
        return self.client.post('/api/v2/pilot/requests/'+rid+'/review',headers=self.analyst,json={
            'version':row['version'],'original_text':row['original_text'],'translated_text':'Drinking water reaches the street for only one hour daily.',
            'category':'Water Supply','urgency':'Routine','status':'Acknowledged','reason':'Synthetic officer reviewed native text and confirmed classification.'})

    def test_complete_decision_and_independent_audit_flow(self):
        rid=self.submit().get_json()['request_id'];self.assertEqual(self.review(rid).status_code,200)
        out=self.client.get('/api/v2/analyst/snapshot?budget_lakh=500&capacity=6',headers=self.analyst).get_json()
        project=out['scenario']['items'][0]['project_id']
        out=self.client.post('/api/v2/analyst/projects/'+project+'/draft',headers=self.analyst,json={'budget_lakh':500,'capacity':6,'notes':'Synthetic water-service feasibility rehearsal.'})
        self.assertEqual(out.status_code,200,out.get_json());decision=out.get_json()['decision']['decision_id']
        out=self.client.post('/api/v2/analyst/decisions/'+decision+'/review',headers=self.admin,json={'engineering_cost_lakh':20,'beneficiary_count':100,'evidence_reference':'urn:nvb:demo:engineering-review'})
        self.assertEqual(out.status_code,200,out.get_json())
        out=self.client.post('/api/v1/policy/decisions/'+decision+'/approve',headers=self.admin,json={})
        self.assertEqual(out.status_code,200,out.get_json())
        out=self.client.post('/api/v2/auditor/cases',headers=self.auditor,json={'project_id':project,'request_id':rid,'title':'Synthetic independent evidence finding','notes':'Verify the illustrative engineering evidence and record limitations.','data_mode':'synthetic'})
        self.assertEqual(out.status_code,201,out.get_json())
        case=out.get_json()['case'];path='/api/v2/auditor/cases/'+case['case_id']+'/actions'
        out=self.client.post(path,headers=self.auditor,json={'action':'start','version':1,'notes':'Start independent examination of this synthetic finding.'})
        self.assertEqual(out.status_code,200,out.get_json())
        closure={'action':'resolve','version':2,'notes':'Second reviewer confirms the synthetic correction and its limitations.','evidence_reference':'urn:nvb:demo:independent-check'}
        self.assertEqual(self.client.post(path,headers=self.auditor,json=closure).status_code,400)
        out=self.client.post(path,headers=self.admin,json=closure);self.assertEqual(out.status_code,200,out.get_json())
        out=self.client.post('/api/v2/auditor/exports',headers=self.auditor,json={'kind':'project','project_id':project})
        self.assertEqual(out.status_code,200,out.get_json());sid=out.get_json()['snapshot_id']
        self.assertEqual(self.client.get('/api/v2/auditor/exports/'+sid,headers=self.auditor).status_code,200)
        with self.app.app_context():
            uid=pilot.rows("SELECT id FROM users WHERE role='auditor'")[0]['id']
            get_db().execute("UPDATE pilot_memberships SET state='Tamil Nadu',district='Vellore' WHERE user_id=?",(uid,));get_db().commit()
        self.assertEqual(self.client.get('/api/v2/auditor/exports/'+sid,headers=self.auditor).status_code,403)

    def test_analytics_outage_preserves_human_review(self):
        rid=self.submit().get_json()['request_id'];self.review(rid)
        with self.app.app_context():
            job=pilot.rows("SELECT job_id FROM pilot_outbox WHERE kind='analytics_sync'")[0]['job_id']
            self.assertEqual(pilot_worker.run_one(job)['status'],'review_required')
            self.assertEqual(pilot.rows('SELECT processing_status FROM pilot_requests')[0]['processing_status'],'human_reviewed')

    def test_worker_endpoint_rejects_unassigned_identity(self):
        self.assertEqual(self.client.post('/api/v2/pilot/internal/work',json={'job_id':'a-job-id'},headers=self.admin).status_code,403)

    def test_expired_lease_is_recovered_without_duplicate_ticket(self):
        self.submit()
        with self.app.app_context():
            old=pilot_worker.claim();get_db().execute("UPDATE pilot_outbox SET lease_until='2000-01-01' WHERE job_id=?",(old['job_id'],));get_db().commit()
            new=pilot_worker.claim();self.assertNotEqual(new['lease_id'],old['lease_id'])
            self.assertFalse(pilot_worker.active_lease(old));self.assertTrue(pilot_worker.active_lease(new))
            self.assertEqual(pilot.rows('SELECT COUNT(*) n FROM citizen_requests')[0]['n'],1)

    def test_operational_bootstrap_rejects_existing_rehearsal(self):
        self.app.config.update(DEMO_MODE=False,SEED_DEMO_DATA=False)
        result=self.app.test_cli_runner().invoke(args=['pilot-bootstrap','--operational'])
        self.assertNotEqual(result.exit_code,0)
        with self.app.app_context():self.assertEqual(pilot.programme(pilot.PILOT_ID)['data_mode'],'synthetic')

    def test_retained_voice_is_private_and_district_scoped(self):
        import base64
        content=b'RIFF synthetic browser audio fixture'
        rid=self.submit(audio_base64=base64.b64encode(content).decode(),mime_type='audio/wav').get_json()['request_id']
        url='/api/v2/pilot/requests/'+rid+'/audio'
        self.assertEqual(self.client.get(url).status_code,401)
        response=self.client.get(url,headers=self.vellore)
        self.assertEqual(response.data,content);self.assertIn('no-store',response.headers['Cache-Control'])
        self.assertEqual(self.client.get(url+'?district=Tirupati',headers=self.vellore).status_code,403)

    def test_pilot_vertex_transport_never_retries_an_ambiguous_call(self):
        from visbharat.services.pilot_ai import PilotGoogleAI
        ai=PilotGoogleAI('test-project','asia-south1','gemini-2.5-flash')
        with patch.object(ai,'_get_vertex_auth_header',return_value={}),patch('visbharat.services.pilot_ai.requests.post',side_effect=TimeoutError()) as post:
            with self.assertRaises(TimeoutError):ai.translate_text('Synthetic example','ta','en')
            self.assertEqual(post.call_count,1)
            self.assertTrue(post.call_args.args[0].startswith('https://asia-south1-aiplatform.googleapis.com/'))

    def test_successful_pilot_vertex_records_usage_and_actual_model(self):
        from visbharat.services.pilot_ai import PilotGoogleAI
        ai=PilotGoogleAI('test-project','asia-south1','gemini-2.5-flash')
        response=Mock();response.json.return_value={'candidates':[{'content':{'parts':[{'text':'{"translated_text":"Water supply is irregular."}'}]}}],
            'modelVersion':'test-served-model','usageMetadata':{'totalTokenCount':27}}
        with patch.object(ai,'_get_vertex_auth_header',return_value={}),patch('visbharat.services.pilot_ai.requests.post',return_value=response):
            result=ai.translate_text('Synthetic example','ta','en')
        self.assertEqual(result['usage']['totalTokenCount'],27);self.assertEqual(result['model'],'test-served-model')
        self.assertFalse(result['fallback_used'])

    def test_concurrent_idempotent_admission_commits_one_ticket(self):
        from concurrent.futures import ThreadPoolExecutor
        def submit_one(_):
            with self.app.test_client() as client:
                response=client.post('/api/v2/pilot/intake',headers={'Idempotency-Key':'concurrent-same-submission'},json={
                    'location_id':'vellore-1','language':'en','text':'Water arrives for only an hour in our street.','consent_granted':True})
                return response.status_code,response.get_json()['request_id']
        with ThreadPoolExecutor(max_workers=6) as pool:results=list(pool.map(submit_one,range(6)))
        self.assertEqual(len({rid for _,rid in results}),1)
        self.assertEqual([status for status,_ in results].count(202),1)

    def test_operational_evaluations_remain_queued_for_authenticated_dispatch(self):
        self.app.config['LOCAL_EVALUATION_WORKER']=False
        out=self.client.post('/api/v2/auditor/evaluations',headers=self.auditor,json={'dataset_version':'synthetic-test-v1','model_version':'recorded-test-output',
            'label_status':'provisional','samples':[{'expected_category':'Water Supply','predicted_category':'Water Supply','language':'ta'}]})
        self.assertEqual(out.status_code,202,out.get_json())
        with self.app.app_context():self.assertEqual(pilot.rows('SELECT status FROM auditor_jobs')[0]['status'],'queued')


if __name__=='__main__':unittest.main()
