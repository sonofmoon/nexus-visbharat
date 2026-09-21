import os
import unittest

from visbharat import create_app
from visbharat.db import init_db, seed_default_users, get_db


class TestPhase3Intelligence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db_path = os.path.join(os.getcwd(), 'tests', 'test_phase3_intelligence.db')
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

        cls.app = create_app()
        cls.app.config.update(TESTING=True, DATABASE_PATH=cls.db_path, DATABASE_URL='')

        with cls.app.app_context():
            init_db()
            seed_default_users()

        cls.client = cls.app.test_client()
        cls.admin_token = cls.app.config['ADMIN_API_TOKEN']
        cls.analyst_token = cls.app.config['ANALYST_API_TOKEN']

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.db_path):
            try:
                os.remove(cls.db_path)
            except OSError:
                pass

    @staticmethod
    def _auth(token):
        return {'Authorization': f'Bearer {token}'}

    def _seed_some_requests(self):
        for district, text in [
            ('Chennai', 'Drainage overflow in zone 5'),
            ('Chennai', 'Water supply outage in ward 8'),
            ('Koraput', 'Road access is blocked for village'),
            ('Hyderabad', 'Transformer issue causing outage'),
        ]:
            self.client.post(
                '/api/submit',
                json={
                    'text': text,
                    'language': 'en',
                    'district': district,
                    'source': 'Web',
                },
            )

    def test_intelligence_endpoints_require_auth(self):
        r1 = self.client.get('/api/v1/intelligence/gap-analysis')
        r2 = self.client.get('/api/v1/intelligence/hotspot-predictions')
        self.assertEqual(r1.status_code, 401)
        self.assertEqual(r2.status_code, 401)

    def test_policy_priority_rankings_requires_auth(self):
        r = self.client.get('/api/v1/policy/priority-rankings')
        self.assertEqual(r.status_code, 401)

    def test_policy_priority_rankings_with_budget_draft(self):
        self._seed_some_requests()
        resp = self.client.get(
            '/api/v1/policy/priority-rankings?limit=5&total_budget_lakh=1200',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('budget_used_lakh', payload)
        self.assertIn('budget_remaining_lakh', payload)
        self.assertIn('funded_count', payload)
        self.assertIn('items', payload)
        self.assertTrue(len(payload['items']) > 0)

        first = payload['items'][0]
        self.assertIn('district', first)
        self.assertIn('priority_score', first)
        self.assertIn('base_priority_score', first)
        self.assertIn('cosign_boost', first)
        self.assertIn('estimated_project_cost_lakh', first)
        self.assertIn('funded_in_draft_plan', first)
        self.assertIn('rank', first)
        self.assertIn('score_components', first)
        self.assertIn('score_explainability', first)
        self.assertIn('weights', first['score_explainability'])
        self.assertIn('normalized', first['score_explainability'])
        self.assertIn('raw', first['score_explainability'])

    def test_policy_impact_brief_requires_auth(self):
        r = self.client.get('/api/v1/policy/impact-brief')
        self.assertEqual(r.status_code, 401)

    def test_policy_impact_decay_requires_auth(self):
        r = self.client.get('/api/v1/policy/impact-decay?district=Chennai')
        self.assertEqual(r.status_code, 401)

    def test_policy_impact_brief_returns_metrics_and_auto_brief(self):
        self._seed_some_requests()
        resp = self.client.get(
            '/api/v1/policy/impact-brief?limit=5&total_budget_lakh=1200',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('impact_metrics', payload)
        self.assertIn('auto_brief', payload)
        self.assertIn('items', payload)

        metrics = payload['impact_metrics']
        self.assertIn('population_covered_estimate', metrics)
        self.assertIn('funding_coverage_ratio', metrics)

        auto_brief = payload['auto_brief']
        self.assertIn('summary', auto_brief)
        self.assertIn('recommendations', auto_brief)
        self.assertTrue(len(auto_brief['recommendations']) >= 1)

    def test_legacy_sps_does_not_invent_beneficiary_or_outcome_values(self):
        from visbharat.services.demand_analytics import compute_social_priority_score

        with self.app.app_context():
            payload = compute_social_priority_score(limit=1)

        recommendation = payload['ranked_projects'][0]['policy_recommendation']
        self.assertIsNone(recommendation['beneficiary_count'])
        self.assertIn('Catchment survey', recommendation['beneficiary_status'])
        self.assertIn('No quantified service outcome', recommendation['expected_impact'])

    def test_policy_impact_decay_contract_with_decision_linkage(self):
        with self.app.app_context():
            db = get_db()
            db.execute('DELETE FROM citizen_requests')
            db.execute('DELETE FROM policy_decisions')

            district = 'Chennai'
            state = 'Tamil Nadu'
            for idx in range(6):
                db.execute(
                    '''
                    INSERT INTO citizen_requests (
                        request_id, source_channel, input_language, district, state, lat, lng,
                        original_text, translated_text, category, urgency, sentiment, status,
                        submitted_by, ward, service_type, routed_department, sla_due_at,
                        sla_breached_at, sla_escalation_level, ai_metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        f'TEST-PRE-{idx}', 'Web', 'en', district, state, 13.0, 80.0,
                        'pre-window issue', 'pre-window issue', 'Water', 'Routine', 'neutral', 'open',
                        'tester', '', '', '', '', '', 0, '{}', f'2026-08-{10+idx:02d}T10:00:00Z',
                    ),
                )

            for idx in range(2):
                db.execute(
                    '''
                    INSERT INTO citizen_requests (
                        request_id, source_channel, input_language, district, state, lat, lng,
                        original_text, translated_text, category, urgency, sentiment, status,
                        submitted_by, ward, service_type, routed_department, sla_due_at,
                        sla_breached_at, sla_escalation_level, ai_metadata_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (
                        f'TEST-POST-{idx}', 'Web', 'en', district, state, 13.0, 80.0,
                        'post-window issue', 'post-window issue', 'Water', 'Routine', 'neutral', 'open',
                        'tester', '', '', '', '', '', 0, '{}', f'2026-09-{10+idx:02d}T10:00:00Z',
                    ),
                )

            db.execute(
                '''
                INSERT INTO policy_decisions (
                    decision_id, district, priority_score, estimated_project_cost_lakh,
                    total_budget_lakh, status, notes, source_json, created_by,
                    approved_by, approved_at, rejected_by, rejected_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''',
                (
                    'PD-IMPACT-TEST-1', district, 88.0, 250.0,
                    5000.0, 'approved', 'impact test', '{}', 'tester',
                    'Platform Admin', '2026-09-10T00:00:00Z', None, None, '2026-09-09T00:00:00Z', '2026-09-10T00:00:00Z',
                ),
            )
            db.commit()

        resp = self.client.get(
            '/api/v1/policy/impact-decay?decision_id=PD-IMPACT-TEST-1&pre_days=30&post_days=30&category=Water&channel=Web&comparator_mode=peer',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        # Approval date cannot stand in for verified physical delivery.
        self.assertEqual(payload['status'],'delivery_date_required')
        self.assertIsNone(payload['change_pct'])
        self.assertFalse(payload['causal_claim'])
        observed=self.client.get('/api/v1/policy/impact-decay?district=Chennai&anchor_at=2026-08-20&post_days=30&category=Water&channel=Web',headers=self._auth(self.analyst_token)).get_json()
        self.assertEqual(observed['counts'],{'before':6,'after_observed':2})
        self.assertFalse(observed['causal_claim'])
        self.assertIsNone(observed['confidence_interval'])

    def test_policy_impact_brief_rejects_invalid_budget(self):
        resp = self.client.get(
            '/api/v1/policy/impact-brief?total_budget_lakh=-1',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_policy_priority_rankings_rejects_invalid_budget(self):
        resp = self.client.get(
            '/api/v1/policy/priority-rankings?total_budget_lakh=-10',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, 400)

    def test_intelligence_endpoints_return_ranked_items(self):
        self._seed_some_requests()

        gap_resp = self.client.get(
            '/api/v1/intelligence/gap-analysis?limit=5',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(gap_resp.status_code, 200)
        gap_items = gap_resp.get_json()['items']
        self.assertTrue(len(gap_items) > 0)
        self.assertIn('district', gap_items[0])
        self.assertIn('gap_score', gap_items[0])

        pred_resp = self.client.get(
            '/api/v1/intelligence/hotspot-predictions?limit=5',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(pred_resp.status_code, 200)
        pred_items = pred_resp.get_json()['items']
        self.assertTrue(len(pred_items) > 0)
        self.assertIn('district', pred_items[0])
        self.assertIn('predicted_stress_score_next_quarter', pred_items[0])

    def test_bigquery_and_vertex_endpoints_require_auth(self):
        r1 = self.client.get('/api/v1/intelligence/bigquery-analytics')
        r2 = self.client.get('/api/v1/intelligence/vertex-predictions')
        self.assertEqual(r1.status_code, 401)
        self.assertEqual(r2.status_code, 401)

    def test_bigquery_analytics_contract(self):
        self._seed_some_requests()
        resp = self.client.get(
            '/api/v1/intelligence/bigquery-analytics?limit=5',
            headers=self._auth(self.analyst_token),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('mode', payload)
        self.assertIn('warehouse', payload)
        self.assertIn('district_aggregates', payload)
        self.assertIn('category_aggregates', payload)
        self.assertIn('source_aggregates', payload)
        self.assertIn('daily_trend', payload)

    def test_vertex_predictions_contract(self):
        self._seed_some_requests()
        resp = self.client.get(
            '/api/v1/intelligence/vertex-predictions?limit=5',
            headers=self._auth(self.admin_token),
        )
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload['success'])
        self.assertIn('mode', payload)
        self.assertIn('prediction_horizon', payload)
        self.assertIn('items', payload)
        self.assertTrue(len(payload['items']) > 0)
        first = payload['items'][0]
        self.assertIn('district', first)
        self.assertIn('risk_band', first)
        self.assertIn('model_confidence', first)
        self.assertIn('model_name', first)


if __name__ == '__main__':
    unittest.main()
