"""Illustrative sensitivity calculator, not an accusation or financial authority."""
from .auditor_workbench import number

PILOT_ANTI_CAPTURE_PROJECTS=[]


def list_anti_capture_projects(): return []


def compute_triangulation_divergence(project_id,pfms_disbursed_pct,satellite_progress_pct,citizen_complaint_pct,gemini_defect_score=.5):
    spent=number(pfms_disbursed_pct,'spent percent',0,100)
    satellite=number(satellite_progress_pct,'satellite proxy',0,100)
    citizen=number(citizen_complaint_pct,'citizen severity',0,100)
    defect=number(gemini_defect_score,'photo score',0,1)
    score=round(.4*abs(citizen-(100-spent))/100+.35*abs(spent-satellite)/100+.25*defect,4)
    return {'project_id':project_id,'divergence_score':score,'data_mode':'illustrative',
            'status':'sensitivity_example','audit_recommendation':'Review comparable milestone evidence',
            'financial_action':None,'is_anomaly_flagged':None,
            'notice':'Weights are unvalidated assumptions combining different concepts. This score cannot establish fraud, verified delivery or funding eligibility.'}
