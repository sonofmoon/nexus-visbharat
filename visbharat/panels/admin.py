"""Legacy lens: no invented approvals, service measurements or certifications."""
from flask import Blueprint

admin_panel_bp = Blueprint('admin_panel', __name__)


def build_admin_lens(cluster_data):
    return {
        'view_name': 'Administration overview',
        'cluster_id': cluster_data.get('cluster_id'),
        'district': cluster_data.get('district'),
        'status': 'authenticated_workspace_required',
        'data_mode': 'illustrative_not_measured',
        'weight_tuning_governance': {
            'active_weight_version': None,
            'signed_governance_diff': None,
            'status': 'default_baseline'
        },
        'system_health_cockpit': {
            'ingestion_latency_ms': None,
            'asr_telemetry': 'not_measured',
            'cluster_queue_depth': None,
            'silence_map_coverage': None,
            'event_bus_throughput': None,
            'status': 'illustrative_not_evaluated'
        },
        'dpg_ops_console': {
            'dpg_conformance_score': None,
            'status': 'candidate_not_certified'
        },
        'message': 'Use the authenticated dashboard for recorded decisions. Live provider execution evidence is available at /api/ai/status.'
    }
