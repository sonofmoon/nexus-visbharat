import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from flask import Flask
from visbharat.blueprints.analyst import analyst_bp
from visbharat.db import init_db, close_db, get_db, ensure_request_lifecycle_tables, ensure_demand_cluster_tables
from visbharat.security import hash_api_token
from visbharat.services import analyst_workbench as work


class DemandScreeningTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(TESTING=True, DATABASE_URL='', DATABASE_PATH=str(Path(self.temp.name) / 'audit.db'))
        self.app.teardown_appcontext(close_db)
        self.app.register_blueprint(analyst_bp)
        self.app.extensions['reference_repo'] = SimpleNamespace(df_districts=pd.DataFrame([
            dict(state='Tamil Nadu', district='Karur', population=100000, deprivation_index=0.8,
                 water_coverage=30, road_coverage=50, electricity_coverage=90, lat=11.0, lng=78.0),
            dict(state='Tamil Nadu', district='Ariyalur', population=50000, deprivation_index=0.6,
                 water_coverage=40, road_coverage=40, electricity_coverage=70, lat=11.1, lng=79.0),
            dict(state='Telangana', district='Hyderabad', population=100000, deprivation_index=0.1,
                 water_coverage=90, road_coverage=80, electricity_coverage=95, lat=17.0, lng=78.0)
        ]))
        self.client = self.app.test_client()
        self.headers = {'Authorization': 'Bearer analyst-test'}
        with self.app.app_context():
            init_db()
            ensure_request_lifecycle_tables()
            ensure_demand_cluster_tables()
            token = 'analyst-test'
            get_db().execute('INSERT INTO users(name,api_token,api_token_hash,role,created_at) VALUES(?,?,?,?,?)',
                             ('analyst', token, hash_api_token(token), 'analyst', '2026-01-01'))
            # Routine in Karur
            self.insert('REQ-001', district='Karur', urgency='Routine')
            # Emergency in Karur
            self.insert('REQ-002', district='Karur', urgency='Emergency')
            # Routine in Hyderabad
            self.insert('REQ-003', state='Telangana', district='Hyderabad', urgency='Routine')
            # Ariyalur has 0 complaints -> High deprivation (0.6) + 0 complaints -> investigate_access = True
            get_db().commit()

    def insert(self, ticket, **changes):
        row = dict(request_id=ticket, source_channel='Web Form', input_language='ta',
                   district='Karur', state='Tamil Nadu', ward='Ward 1', lat=11.0, lng=78.0,
                   original_text='Test text', translated_text='Test text', category='Water Supply',
                   urgency='Routine', sentiment='Neutral', status='Pending', routed_department='Water Board',
                   sla_due_at='2026-01-03T00:00:00Z', ai_metadata_json='{}', created_at='2026-01-01T00:00:00Z')
        row.update(changes)
        get_db().execute(f"INSERT INTO citizen_requests ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                         list(row.values()))

    def test_inclusion_screening_indicators(self):
        with self.app.app_context():
            scope = work.scope_from({})
            data = work.inclusion(scope)
            items = {r['district']: r for r in data['items']}
            
            # Karur checks: 2 complaints, 1 emergency
            karur = items['Karur']
            self.assertEqual(karur['requests'], 2)
            self.assertEqual(karur['emergency_count'], 1)
            self.assertEqual(karur['emergency_pct'], 50.0)
            self.assertIsNotNone(karur['infrastructure_gap_pct'])
            self.assertGreater(karur['composite_stress_score'], 0)
            self.assertIsNone(karur['voice_access_index'])
            self.assertIsNone(karur['latent_requests'])

            # Ariyalur checks: 0 complaints, deprivation 0.6 >= 0.5 -> Access Alert
            ariyalur = items['Ariyalur']
            self.assertEqual(ariyalur['requests'], 0)
            self.assertTrue(ariyalur['investigate_access'])

            # Verify access alert count (Karur has 2/100k < 10 & dep 0.8; Ariyalur has 0/100k < 10 & dep 0.6)
            self.assertEqual(data['access_investigation_count'], 2)

            # Hyderabad checks: low deprivation, 0 emergencies
            hyd = items['Hyderabad']
            self.assertEqual(hyd['requests'], 1)
            self.assertEqual(hyd['emergency_count'], 0)
            self.assertEqual(hyd['emergency_pct'], 0.0)
            self.assertFalse(hyd['investigate_access'])
