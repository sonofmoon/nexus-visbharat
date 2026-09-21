"""Observed response metrics; no fixture league score."""


def compute_district_responsiveness_index(district=None):
    from . import analyst_workbench as work
    result=work.responsiveness(work.scope_from({'district':district}))
    return {**result,'league_table':result['items'],'average_dri_score':None,
            'status':'event_metrics_only','composite_score':None}
