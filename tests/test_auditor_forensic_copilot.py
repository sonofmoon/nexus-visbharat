import json
import unittest
from tests import test_analyst_workbench as fixture
from visbharat.blueprints.auditor import auditor_bp
from visbharat.db import get_db
from visbharat.services import analyst_workbench as analyst
from visbharat.services import auditor_workbench as work


class AuditorForensicCopilotTest(unittest.TestCase):
    def setUp(self):
        self.f = fixture.AnalystWorkbenchTest()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.app = self.f.app
        self.app.config['SECRET_KEY'] = 'isolated-auditor-test'
        self.app.register_blueprint(auditor_bp)
        self.client = self.app.test_client()
        self.h = {'Authorization': 'Bearer auditor-test'}
        self.admin = {'Authorization': 'Bearer admin-test'}
        self.pid = analyst.candidate_id('Tamil Nadu', 'Karur', 'Ward 1', 'Water Supply')

    def test_forensic_audit_intelligence_computation(self):
        with self.app.app_context():
            scope = work.scope_from({'district': 'Karur', 'state': 'Tamil Nadu'})
            intel = work.forensic_audit_intelligence(scope)
            self.assertIn('trust_health_score', intel)
            self.assertIn('risk_rating', intel)
            self.assertIn('executive_opinion', intel)
            self.assertIn('statutory_violations', intel)
            self.assertIn('corrective_actions', intel)
            self.assertIn('red_flags', intel)
            self.assertIn('dpdpa_compliance', intel)
            self.assertIn('chain_state', intel)
            self.assertIn('threat_posture', intel)
            self.assertIn(intel['provider_mode'], ('gemini_live', 'local_fallback'))
            self.assertTrue(0 <= intel['trust_health_score'] <= 100)

    def test_forensic_intelligence_api_endpoint(self):
        res = self.client.get('/api/v2/auditor/intelligence?district=Karur&state=Tamil+Nadu', headers=self.h)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('trust_health_score', data)
        self.assertIn('dpdpa_compliance', data)
        self.assertIn('chain_state', data)
        self.assertIn('executive_opinion', data)

    def test_ask_auditor_copilot_endpoint(self):
        payload = {
            'query': 'Are there any unreviewed capital sanctions or GFR violations in Karur?',
            'district': 'Karur',
            'state': 'Tamil Nadu',
            'project_id': self.pid
        }
        res = self.client.post('/api/v2/auditor/copilot/ask',
                               data=json.dumps(payload),
                               content_type='application/json',
                               headers=self.h)
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data.get('success'))
        self.assertIn('answer', data)
        self.assertTrue(len(data['answer']) > 20)
        self.assertIn('citations', data)
        self.assertIsInstance(data['citations'], list)
        self.assertIn(data['provider_mode'], ('gemini_live', 'local_fallback'))

    def test_copilot_validation_and_auth(self):
        # Empty query
        res = self.client.post('/api/v2/auditor/copilot/ask',
                               data=json.dumps({'query': ''}),
                               content_type='application/json',
                               headers=self.h)
        self.assertEqual(res.status_code, 400)

        # Unauthenticated request
        res = self.client.post('/api/v2/auditor/copilot/ask',
                               data=json.dumps({'query': 'test'}),
                               content_type='application/json')
        self.assertEqual(res.status_code, 401)

        # Unauthorized role (e.g. analyst without auditor role)
        res = self.client.post('/api/v2/auditor/copilot/ask',
                               data=json.dumps({'query': 'test'}),
                               content_type='application/json',
                               headers={'Authorization': 'Bearer analyst-test'})
        self.assertEqual(res.status_code, 403)


if __name__ == '__main__':
    unittest.main()
