"""Legacy evaluation adapter. No fixture matching or causal estimates are asserted."""


def auto_select_matched_controls(decision_id, district, category, project_name=None, k=3):
    return {'decision_id':decision_id,'district':district,'category':category,'project_name':project_name,
            'status':'evaluation_design_required','control_geofence_ids':[], 'control_hash':None,
            'covariate_balance_smd':None,'parallel_trend_pvalue':None,
            'message':'Pre-treatment observations and a reviewed comparison design are required. No controls were created.'}


def compute_did_causal_impact(decision_id=None,district=None):
    return {'decision_id':decision_id,'district':district,'status':'insufficient_data',
            'causal_lift':None,'net_did_lift_pct':None,'ci_bounds':None,'treated_geofence':None,
            'matched_control_geofence':None,'matched_control_list':[], 'covariate_balance_smd':None,
            'parallel_trend_pvalue':None,'pap_audit_hash':None,'statistically_significant':False,
            'verdict':'Evaluation not ready','causal_claim':False,
            'message':'Use the project Outcomes view for reviewed service observations. Causal attribution requires an independently reviewed evaluation.'}
