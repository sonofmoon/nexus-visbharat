import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from flask import Flask
from visbharat.blueprints.api_intake import intake_bp
from visbharat.db import init_db, close_db, get_db, ensure_request_lifecycle_tables, ensure_demand_cluster_tables
from visbharat.security import hash_api_token


class PolicyBriefRAGTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.app = Flask(__name__)
        self.app.config.update(
            TESTING=True,
            DATABASE_URL='',
            DATABASE_PATH=str(Path(self.temp.name) / 'audit.db'),
            L5_RAG_CORPUS_PATH='docs/release/layer5_rag_corpus.sample.json'
        )
        self.app.teardown_appcontext(close_db)
        self.app.register_blueprint(intake_bp)
        self.app.extensions['reference_repo'] = SimpleNamespace(df_districts=pd.DataFrame([
            dict(state='Tamil Nadu', district='Vellore', population=100000, deprivation_index=0.5,
                 water_coverage=60, road_coverage=70, electricity_coverage=90, lat=12.9, lng=79.1)
        ]))
        self.client = self.app.test_client()
        with self.app.app_context():
            init_db()
            ensure_request_lifecycle_tables()
            ensure_demand_cluster_tables()
            token = 'analyst-test'
            get_db().execute('INSERT INTO users(name,api_token,api_token_hash,role,created_at) VALUES(?,?,?,?,?)',
                             ('analyst', token, hash_api_token(token), 'analyst', '2026-01-01'))
            # Routine in Vellore
            self.insert('REQ-001', district='Vellore', category='Water Supply', urgency='Routine')
            # Emergency in Vellore
            self.insert('REQ-002', district='Vellore', category='Water Supply', urgency='Emergency')
            get_db().commit()

    def insert(self, ticket, **changes):
        row = dict(request_id=ticket, source_channel='Web Form', input_language='ta',
                   district='Vellore', state='Tamil Nadu', ward='Ward 1', lat=12.9, lng=79.1,
                   original_text='Pipeline broken', translated_text='Pipeline broken', category='Water Supply',
                   urgency='Routine', sentiment='Neutral', status='Pending', routed_department='Water Board',
                   sla_due_at='2026-01-03T00:00:00Z', ai_metadata_json='{}', created_at='2026-01-01T00:00:00Z')
        row.update(changes)
        get_db().execute(f"INSERT INTO citizen_requests ({','.join(row)}) VALUES ({','.join('?' for _ in row)})",
                         list(row.values()))

    def test_policy_brief_rag_synthesis_and_citations(self):
        res = self.client.get('/api/policy-brief/Vellore')
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        
        self.assertTrue(data['success'])
        self.assertEqual(data['district'], 'Vellore')
        self.assertIn('summary', data)
        self.assertIn('recommendations', data)
        self.assertIsInstance(data['recommendations'], list)
        self.assertGreaterEqual(len(data['recommendations']), 1)
        
        # Verify demand signals
        signals = data.get('demand_signals') or {}
        self.assertEqual(signals['total_complaints'], 2)
        self.assertEqual(signals['emergency_count'], 1)
        self.assertIn('Water Supply', signals['top_categories'])
        
        # Verify citations from governed corpus
        citations = data.get('citations') or []
        self.assertGreaterEqual(len(citations), 1)
        self.assertEqual(data['citation_count'], len(citations))
        first_cite = citations[0]
        self.assertIn('title', first_cite)
        self.assertIn('publisher', first_cite)
        self.assertIn('excerpt', first_cite)
        self.assertIn('score', first_cite)
        
        # Verify provider telemetry (either gemini_live or local_fallback)
        self.assertIn(data['provider_mode'], ('gemini_live', 'local_fallback'))
        self.assertIn(data['ai_mode'], ('gemini_rag_live', 'local_governed_retrieval'))
        self.assertIsNotNone(data['llm_model'])
