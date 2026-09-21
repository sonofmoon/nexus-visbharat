"""Test isolation: never construct live external service clients during the suite.

Tests verify contracts against deterministic simulation fallbacks; live GCP/AI
verification is done separately by the verify_*.py scripts, not unit tests.
"""
import os

os.environ['NVB_DISABLE_EXTERNAL_SERVICES'] = '1'
os.environ['JURY_REQUIRE_LIVE_MODELS'] = '0'
