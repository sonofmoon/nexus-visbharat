"""Deployment contract: optional map provider and no production token bootstrap."""
import unittest
from pathlib import Path
from types import SimpleNamespace
from flask import Flask
from visbharat.blueprints.web import web_bp


class DashboardDeploymentTest(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        self.app=Flask(__name__,template_folder=str(root/'templates'),static_folder=str(root/'static'))
        self.app.config.update(TESTING=True,DEMO_MODE=False,GOOGLE_MAPS_API_KEY='',
                               ADMIN_API_TOKEN='private-admin-fixture',ANALYST_API_TOKEN='private-analyst-fixture',AUDITOR_API_TOKEN='private-auditor-fixture')
        self.app.extensions['reference_repo']=SimpleNamespace(list_states=lambda:[])
        self.app.register_blueprint(web_bp)

    def test_workspace_opens_without_map_and_does_not_embed_production_tokens(self):
        response=self.app.test_client().get('/dashboard')
        self.assertEqual(response.status_code,200)
        html=response.get_data(as_text=True)
        self.assertIn('Demand &amp; Inclusion',html)
        self.assertNotIn('private-admin-fixture',html)
        self.assertNotIn('private-analyst-fixture',html)
        self.assertNotIn('private-auditor-fixture',html)

    def test_explicit_demo_mode_supports_local_role_switching(self):
        self.app.config['DEMO_MODE']=True
        html=self.app.test_client().get('/dashboard').get_data(as_text=True)
        self.assertIn('private-analyst-fixture',html)
