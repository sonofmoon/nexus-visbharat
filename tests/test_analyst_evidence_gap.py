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


class AnalystEvidenceGapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DATABASE_URL='', DATABASE_PATH=str(Path(self.temp.name) / 'audit.db'))
        self.app.teardown_appcontext(close_db)
        self.app.register_blueprint(analyst_bp)
        self.app.register_blueprint(api_bp)
        self.app.extensions['reference_repo'] = SimpleNamespace(df_districts=pd.DataFrame([
            dict(state='Tamil Nadu', district='Karur', population=100000, deprivation_index=0.8, water_coverage=75.0, road_coverage=50.0, electricity_coverage=90.0, lat=11, lng=78),
            dict(state='Telangana', district='Hyderabad', population=100000, deprivation_index=0.1, water_coverage=90.0, road_coverage=80.0, electricity_coverage=95.0, lat=17, lng=78)
        ]))
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer analyst-test'}
        with self.app.app_context():
            init_db()
            ensure_request_lifecycle_tables()
            ensure_demand_cluster_tables()
            for role in ('analyst', 'admin', 'auditor'):
                token = role + '-test'
                get_db().execute('INSERT INTO users(name,api_token,api_token_hash,role,created_at) VALUES(?,?,?,?,?)',
                                 (role, token, hash_api_token(token), role, '2026-01-01'))
            # Insert test requests
            for i in range(8):
                self.insert(f'water-{i}', category='Water Supply', urgency='Routine' if i > 1 else 'Emergency')
            self.insert('road-1', category='Roads', urgency='Routine')
            get_db().commit()

    def insert(self, ticket, **changes):
        row = dict(request_id=ticket, source_channel='SMS Keyword', input_language='ta',
                   district='Karur', state='Tamil Nadu', ward='Ward 1', lat=11, lng=78,
                   original_text='Citizen example', translated_text='Water supply required',
                   category='Water Supply', urgency='Routine', sentiment='Neutral',
                   status='Pending', routed_department='Water Board', sla_due_at='2026-01-03T00:00:00Z',
                   ai_metadata_json='{"is_synthetic":true}', created_at='2026-01-01T00:00:00Z')
        row.update(changes)
        get_db().execute(f"INSERT INTO citizen_requests ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                         list(row.values()))

    def test_evidence_gap_intelligence_computation(self):
        with self.app.app_context():
            scope = work.scope_from({'district': 'Karur', 'state': 'Tamil Nadu'})
            intel = work.evidence_gap_intelligence(scope)
            self.assertIn('executive_assessment', intel)
            self.assertIn('strategic_priorities', intel)
            self.assertIn('reconciliation_protocol', intel)
            self.assertIn('discrepancies', intel)
            self.assertGreater(intel['discrepancy_count'], 0)
            self.assertIn(intel['provider_mode'], ('gemini_live', 'local_fallback'))
            
            # Check for ghost infrastructure detection (8 water requests in 75% coverage zone)
            disc_types = [d['type'] for d in intel['discrepancies']]
            self.assertIn('ghost_infrastructure', disc_types)
            ghost = next(d for d in intel['discrepancies'] if d['type'] == 'ghost_infrastructure')
            self.assertEqual(ghost['severity'], 'critical')
            self.assertIn('JJM', ghost['evidence_anchor'])
            self.assertTrue(len(ghost['recommended_action']) > 10)

    def test_evidence_api_endpoint_payload(self):
        res = self.client.get('/api/v2/analyst/evidence?district=Karur&state=Tamil+Nadu', headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('sources', data)
        self.assertIn('loaded_sources', data)
        self.assertIn('verified_sources', data)
        self.assertIn('limitations', data)
        self.assertIn('gap_intelligence', data)
        
        gap = data['gap_intelligence']
        self.assertIn('executive_assessment', gap)
        self.assertIn('discrepancies', gap)
        self.assertIsInstance(gap['strategic_priorities'], list)

    def test_ask_evidence_copilot_endpoint(self):
        payload = {
            'query': 'Why is there water shortage in Karur despite high tap coverage?',
            'district': 'Karur',
            'state': 'Tamil Nadu'
        }
        res = self.client.post('/api/v2/analyst/evidence/ask',
                               data=json.dumps(payload),
                               content_type='application/json',
                               headers=self.headers)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('answer', data)
        self.assertTrue(len(data['answer']) > 20)
        self.assertIn('citations', data)
        self.assertIsInstance(data['citations'], list)
        self.assertIn(data['provider_mode'], ('gemini_live', 'local_fallback'))

    def test_ask_evidence_copilot_validation(self):
        # Empty query
        res = self.client.post('/api/v2/analyst/evidence/ask',
                               data=json.dumps({'query': ''}),
                               content_type='application/json',
                               headers=self.headers)
        self.assertEqual(res.status_code, 400)

        # Unauthenticated request
        res = self.client.post('/api/v2/analyst/evidence/ask',
                               data=json.dumps({'query': 'test'}),
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)


if __name__ == '__main__':
    unittest.main()
