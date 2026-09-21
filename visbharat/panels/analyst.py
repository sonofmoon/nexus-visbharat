"""Cluster lens backed by the same scoped metrics as the Analyst workspace."""
from flask import Blueprint

analyst_panel_bp = Blueprint('analyst_panel', __name__)


def build_analyst_lens(cluster_data: dict) -> dict:
    from ..services import analyst_workbench as work
    scope=work.scope_from(cluster_data)
    if not scope['district']:
        return {'view_name':'Analyst evidence','status':'district_required','cluster_id':cluster_data.get('cluster_id')}
    return {'view_name':'Analyst evidence','cluster_id':cluster_data.get('cluster_id'),
            'scope':scope,'aggregation_scope':'District/service scope; not a cluster-specific aggregate',
            'district_responsiveness':work.responsiveness(scope),'inclusion':work.inclusion(scope),
            'observed_demand':work.stats(scope),'scenario_endpoint':'/api/v2/analyst/snapshot',
            'cluster_quality':{'dedup_precision':None,'status':'Independent labelled pairs required'},
            'outreach_status':'No outreach queued by this analysis'}
