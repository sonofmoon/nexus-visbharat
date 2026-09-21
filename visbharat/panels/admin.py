from flask import Blueprint, jsonify

admin_panel_bp = Blueprint('admin_panel', __name__)

def build_admin_lens(cluster_data: dict) -> dict:
    cluster_id = cluster_data.get('cluster_id') or 'TN-KAR-0417'
    district = cluster_data.get('district') or 'Karur'

    return {
        'view_name': 'Admin View — The Control Room with a Conscience',
        'cluster_id': cluster_id,
        'district': district,
        'weight_tuning_governance': {
            'active_weight_version': 'v2.4-governance-approved',
            'mandatory_rationale_required': True,
            'signed_governance_diff': 'Demotes 0 Aspirational Districts. Signed by Planning Secretary.'
        },
        'system_health_cockpit': {
            'ingestion_latency_ms': 142,
            'asr_telemetry': 'NOMINAL (22 Languages)',
            'cluster_queue_depth': 0,
            'silence_map_coverage': '98.6%'
        },
        'dpg_ops_console': {
            'dpg_conformance_score': '98/100',
            'state_onboarding_wizard': 'Andhra Pradesh (Live Feed Validated)',
            'open_api_schema_valid': True
        },
        'crisis_mode': {
            'active': False,
            'posture': 'NORMAL',
            'haz_priority_override': False,
            'emergency_sla_hours': 24
        }
    }
