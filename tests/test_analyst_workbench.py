import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from flask import Flask
from visbharat.blueprints.analyst import analyst_bp
from visbharat.blueprints.api import api_bp
from visbharat.db import init_db, close_db, get_db, ensure_request_lifecycle_tables, ensure_demand_cluster_tables
from visbharat.security import hash_api_token
from visbharat.services import analyst_workbench as work


class AnalystWorkbenchTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.app=Flask(__name__);self.app.config.update(TESTING=True,DATABASE_URL='',DATABASE_PATH=str(Path(self.temp.name)/'audit.db'))
        self.app.teardown_appcontext(close_db);self.app.register_blueprint(analyst_bp);self.app.register_blueprint(api_bp)
        self.app.extensions['reference_repo']=SimpleNamespace(df_districts=pd.DataFrame([
            dict(state='Tamil Nadu',district='Karur',population=100000,deprivation_index=.8,water_coverage=30,road_coverage=50,electricity_coverage=90,lat=11,lng=78),
            dict(state='Telangana',district='Hyderabad',population=100000,deprivation_index=.1,water_coverage=90,road_coverage=80,electricity_coverage=95,lat=17,lng=78)]))
        self.client=self.app.test_client();self.headers={'Authorization':'Bearer analyst-test'}
        with self.app.app_context():
            init_db();ensure_request_lifecycle_tables();ensure_demand_cluster_tables()
            for role in ('analyst','admin','auditor'):
                token=role+'-test';get_db().execute('INSERT INTO users(name,api_token,api_token_hash,role,created_at) VALUES(?,?,?,?,?)',(role,token,hash_api_token(token),role,'2026-01-01'))
            self.insert('one');self.insert('two');self.insert('three',ward='Ward 2',category='Road')
            self.insert('emergency',urgency='Emergency',ward='Ward 3')
            self.insert('other',state='Telangana',district='Hyderabad',input_language='te',ward='Ward 1')
            for ticket in ('one','two'):
                get_db().execute("INSERT INTO cluster_members(request_id,cluster_id,created_at) VALUES(?,'SAME','2026-01-01')",(ticket,))
            get_db().execute("INSERT INTO request_lifecycle_events(request_id,event_type,to_status,created_at) VALUES('one','ack','Acknowledged','2026-01-02T00:00:00Z')")
            get_db().commit()

    def insert(self,ticket,**changes):
        row=dict(request_id=ticket,source_channel='SMS Keyword',input_language='ta',district='Karur',state='Tamil Nadu',ward='Ward 1',lat=11,lng=78,original_text='Citizen example',translated_text='Water supply required',category='Water Supply',urgency='Routine',sentiment='Neutral',status='Pending',routed_department='Water Board',sla_due_at='2026-01-03T00:00:00Z',ai_metadata_json='{"is_synthetic":true}',created_at='2026-01-01T00:00:00Z')
        row.update(changes);get_db().execute(f"INSERT INTO citizen_requests ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",list(row.values()))

    def call(self,path='/snapshot',**params):
        r=self.client.get('/api/v2/analyst'+path,query_string=params,headers=self.headers)
        self.assertEqual(r.status_code,200,r.get_json());return r.get_json()

    def test_full_aggregation_and_shared_filters(self):
        with self.app.app_context():
            for i in range(601):self.insert('bulk'+str(i))
            get_db().commit()
        d=self.call(state='Tamil Nadu',category='Water Supply',urgency='Routine',language='ta',channel='SMS Keyword',date_from='2026-01-01',date_to='2026-01-01')
        self.assertEqual(d['stats']['total_complaints'],603)
        self.assertEqual(sum(d['stats']['channels'].values()),603)
        self.assertEqual(sum(d['stats']['urgencies'].values()),603)
        self.assertEqual(d['stats']['distinct_issues'],602)
        self.assertTrue(all(p['state']=='Tamil Nadu' and p['category']=='Water Supply' for p in d['scenario']['items']))

    def test_no_scope_fallback_and_zero_budget(self):
        d=self.call(state='not-a-state');self.assertEqual(d['stats']['total_complaints'],0);self.assertEqual(d['scenario']['total_candidates'],0)
        d=self.call(budget_lakh=0);self.assertEqual(d['scenario']['allocation']['selected_count'],0)
        self.assertEqual(d['scenario']['outcomes']['projected_beneficiaries'],0)
        self.assertIsNone(d['scenario']['outcomes']['monetary_npv'])

    def test_emergency_exclusion_and_real_dedup(self):
        d=self.call();self.assertEqual(d['stats']['emergency_count'],1)
        self.assertEqual(d['stats']['distinct_issues'],4)
        self.assertFalse(any(p['ward']=='Ward 3' for p in d['scenario']['items']))
        p=next(p for p in d['scenario']['items'] if p['district']=='Karur' and p['ward']=='Ward 1')
        self.assertEqual((p['reports'],p['issues']),(2,1))

    def test_weights_reproduce_score_and_change_rank(self):
        a=self.call(weights=json.dumps({'demand':1,'equity':0,'gap':0}))['scenario']
        b=self.call(weights=json.dumps({'demand':0,'equity':1,'gap':0}))['scenario']
        self.assertNotEqual(a['scenario_id'],b['scenario_id'])
        for p in b['items']:self.assertAlmostEqual(sum(p['contributions'].values()),p['priority_score'],places=5)
        self.assertEqual(b['items'][0]['district'],'Karur')
        self.assertEqual(b['scenario_id'],self.call(weights=json.dumps({'demand':0,'equity':1,'gap':0}))['scenario']['scenario_id'])

    def test_preview_limit_does_not_change_portfolio(self):
        a=self.call(limit=1)['scenario'];b=self.call(limit=100)['scenario']
        self.assertEqual(a['allocation'],b['allocation']);self.assertEqual(len(a['items']),1)
        c=self.call(limit=1,offset=1)['scenario']
        self.assertEqual(c['items'][0]['project_id'],b['items'][1]['project_id'])
        c=self.call(search='Hyderabad')['scenario']
        self.assertEqual(c['matching_candidates'],1);self.assertEqual(c['allocation'],b['allocation'])
        self.assertEqual(c['top_projects'],b['top_projects'])

    def test_response_sla_uses_events_and_denominators(self):
        d=self.call('/delivery',state='Tamil Nadu')['items'][0]
        self.assertEqual((d['on_time'],d['eligible']),(1,4));self.assertEqual(d['coverage_pct'],25)
        self.assertEqual(d['median_ack_hours'],24);self.assertIsNone(d['citizen_confirmation_pct'])

    def test_outcomes_refuse_future_or_missing_windows(self):
        self.assertEqual(self.call('/outcomes')['status'],'delivery_date_required')
        d=self.call('/outcomes',district='Karur',delivery_at='2099-01-01');self.assertEqual(d['status'],'follow_up_incomplete');self.assertIsNone(d['change_pct']);self.assertFalse(d['causal_claim'])

    def test_outcome_catchment_counts(self):
        with self.app.app_context():
            self.insert('post',created_at='2026-02-03T00:00:00Z');get_db().commit()
        d=self.call('/outcomes',district='Karur',category='Water Supply',urgency='Routine',ward='Ward 1',delivery_at='2026-01-20',days=28)
        self.assertEqual(d['counts'],{'before':2,'after_observed':1});self.assertEqual(d['change_pct'],50)
        self.assertIsNone(d['confidence_interval'])

    def test_source_files_do_not_become_verified(self):
        d=self.call('/evidence');self.assertEqual(d['verified_sources'],0)
        self.assertTrue(all(not s['publisher_verified'] for s in d['sources']))

    def test_access_never_invents_reporting_propensity(self):
        d=self.call();self.assertTrue(all(r['voice_access_index'] is None and r['latent_requests'] is None for r in d['inclusion']['items']))

    def test_inclusion_methodology_and_narrative_are_explicit(self):
        d=self.call(); inclusion=d['inclusion']
        self.assertEqual(inclusion['methodology']['version'],'nvb-observed-access-screen-v2')
        self.assertEqual(inclusion['methodology']['type'],'observed_access_risk_screen')
        self.assertIn('fairness_audit',inclusion)
        self.assertEqual(inclusion['fairness_audit']['status'],'descriptive_screening_only')
        self.assertTrue(all('score_components' in row and 'score_version' in row for row in inclusion['items']))
        response=self.client.post('/api/v2/analyst/inclusion-brief',json={},headers=self.headers)
        self.assertEqual(response.status_code,200,response.get_json())
        body=response.get_json()
        self.assertEqual(body['provider_mode'],'local_fallback')
        self.assertTrue(body['fallback_used'])
        self.assertTrue(body['narrative']['validation_actions'])

    def test_validation_and_auth(self):
        self.assertEqual(self.client.get('/api/v2/analyst/snapshot').status_code,401)
        for weights in ({'demand':None},{'demand':[]},[]):
            self.assertEqual(self.client.post('/api/v2/analyst/snapshot',json={'weights':weights},headers=self.headers).status_code,400)
        for q in ({'budget_lakh':'nan'},{'weights':'{"demand":0,"equity":0,"gap":0}'},{'date_from':'nonsense'},{'date_from':'2026-02-01','date_to':'2026-01-01'},{'date_from':'2026-1-1'},{'capacity':1.5},{'limit':1.5},{'offset':1.5}):
            self.assertEqual(self.client.get('/api/v2/analyst/snapshot',query_string=q,headers=self.headers).status_code,400)

    def test_cited_brief_and_replay_safe_human_approval(self):
        d=self.call();p=d['scenario']['selected_projects'][0]
        path='/api/v2/analyst/projects/'+p['project_id']+'/draft'
        opts={'notes':'Review water service evidence before funding'}
        a=self.client.post(path,json=opts,headers=self.headers).get_json();b=self.client.post(path,json=opts,headers=self.headers).get_json()
        self.assertEqual(a['decision']['decision_id'],b['decision']['decision_id']);self.assertTrue(b['reused'])
        decision=a['decision']['decision_id'];admin={'Authorization':'Bearer admin-test'}
        self.assertEqual(self.client.post('/api/v1/policy/decisions/'+decision+'/approve',json={},headers=admin).status_code,400)
        r=self.client.post('/api/v2/analyst/decisions/'+decision+'/review',json={'engineering_cost_lakh':75,'beneficiary_count':100,'evidence_reference':'Independent survey record TEST-001'},headers=admin)
        self.assertEqual(r.status_code,200,r.get_json())
        r=self.client.post('/api/v1/policy/decisions/'+decision+'/approve',json={},headers=admin)
        self.assertEqual(r.status_code,200,r.get_json())
        after=self.call()['scenario']
        self.assertNotIn(p['project_id'],after['allocation']['selected_ids'])
        self.assertEqual(after['allocation']['existing_commitments_lakh'],75)
        self.assertNotEqual(d['scenario']['scenario_id'],after['scenario_id'])
        brief=self.client.post('/api/v2/analyst/brief',json={},headers=self.headers).get_json()
        self.assertEqual(set(brief['scenario']['allocation']['selected_ids']),{r['project_id'] for r in brief['scenario']['items']})
        self.assertTrue(all(c['source'] for c in brief['claims']))

    def test_reviewed_cost_budget_and_duplicate_guards(self):
        p=self.call(budget_lakh=150)['scenario']['selected_projects'][0]
        path='/api/v2/analyst/projects/'+p['project_id']+'/draft'
        a=self.client.post(path,json={'budget_lakh':150,'notes':'Engineering survey pending'},headers=self.headers).get_json()['decision']['decision_id']
        b=self.client.post(path,json={'budget_lakh':1000,'notes':'Alternative planning envelope'},headers=self.headers).get_json()['decision']['decision_id']
        admin={'Authorization':'Bearer admin-test'}
        for decision in (a,b):
            self.assertEqual(self.client.post('/api/v2/analyst/decisions/'+decision+'/review',json={'engineering_cost_lakh':200,'beneficiary_count':100,'evidence_reference':'Survey and engineering record TEST-002'},headers=admin).status_code,200)
        self.assertEqual(self.client.post('/api/v1/policy/decisions/'+a+'/approve',json={},headers=admin).status_code,400)
        updated=next(r for r in self.call()['scenario']['items'] if r['project_id']==p['project_id'])
        self.assertEqual(updated['estimated_cost_lakh'],200)
        self.assertEqual(updated['cost_high_lakh'],200)
        self.assertEqual(self.client.post('/api/v1/policy/decisions/'+b+'/approve',json={},headers=admin).status_code,200)
        resp=self.client.post('/api/v1/policy/decisions/'+a+'/approve',json={},headers=admin)
        self.assertEqual(resp.status_code,400);self.assertIn('already',resp.get_json()['error'])

    def test_operating_and_district_capacity_constraints(self):
        d=self.call(operating_budget_lakh=0)['scenario']
        self.assertEqual(d['allocation']['selected_count'],0)
        d=self.call(max_per_district=1)['scenario']
        self.assertEqual(len({p['district'] for p in d['selected_projects']}),len(d['selected_projects']))

    def test_missing_ward_and_unknown_cluster_do_not_broaden_outcomes(self):
        with self.app.app_context():
            self.insert('no-ward',ward=None);self.insert('empty-ward',ward='');get_db().commit()
        pid=work.candidate_id('Tamil Nadu','Karur','','Water Supply')
        d=self.call('/outcomes',project_id=pid,delivery_at='2026-01-20')
        self.assertEqual(d['counts']['before'],2)
        r=self.client.get('/api/v2/analyst/outcomes',query_string={'district':'Karur','delivery_at':'2026-01-20','cluster_id':'unknown'},headers=self.headers)
        self.assertEqual(r.status_code,404)

    def test_legacy_roi_has_no_fabricated_benefit(self):
        from visbharat.services.human_capital_roi import compute_aggregate_human_capital_roi
        self.assertEqual(compute_aggregate_human_capital_roi([])['total_school_days_gained'],0)
        self.assertIsNone(compute_aggregate_human_capital_roi([{'district':'Karur'}])['total_npv_earnings_uplift_lakh'])

    def test_missing_lgd_snapshot_uses_explicit_local_codes(self):
        for state,district in [('Tamil Nadu','Vellore'),('Andhra Pradesh','Tirupati'),('Tamil Nadu','Karur')]:
            geo=work.geography(state,district)
            self.assertIsNone(geo['lgd_code'])
            self.assertEqual(geo['code_system'],'NVB-local-v1')
            self.assertIn('not official',geo['boundary_validation'])

    def test_public_sources_do_not_invent_evidence_when_unconfigured(self):
        self.assertIsNone(work.get_official_tirupati_amrut_projects()['total_sanctioned_cr'])
        self.assertIsNone(work.get_official_tirupati_rural_jjm()['total_habitations'])
        evidence=self.call('/evidence')
        sources=[s for s in evidence['sources'] if s['source'].startswith('public_data_')]
        self.assertEqual(len(sources),7)
        for source in sources:
            self.assertFalse(source['publisher_verified'])
            self.assertIsNone(source['sha256'])
            self.assertEqual(source['records'],0)
        brief=self.client.post('/api/v2/analyst/brief',json={},headers=self.headers).get_json()
        self.assertTrue(brief['success'])
        self.assertTrue(all(c.get('source') for c in brief['claims']))
        self.assertNotIn('State Fiscal Feasibility',json.dumps(brief['claims']))
        self.assertNotIn('prevent scheme duplicate funding',json.dumps(brief['claims']))


if __name__=='__main__':unittest.main()
