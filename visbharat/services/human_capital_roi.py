"""Benefits require measured catchment and a reviewed model; no fixture estimates."""


def compute_human_capital_roi(district, category, population=None, deprivation_index=None):
    return {'district':district,'category':category,'status':'not_estimated',
            'child_population_evaluated':None,'physical_impact_value':None,'physical_impact_unit':None,
            'npv_earnings_uplift_lakh':None,'human_capital_irr':None,'citation':None,
            'prompt_injection_text':'No physical or monetized benefit is estimated without a reviewed intervention model.'}


def compute_aggregate_human_capital_roi(funded_items):
    value=None if funded_items else 0
    return {'status':'not_estimated' if funded_items else 'empty_portfolio',
            'total_child_population_covered':value,'total_stunting_cases_averted':value,
            'total_school_days_gained':value,'total_npv_earnings_uplift_lakh':None,
            'human_capital_irr_range':None,'citations':[],
            'summary_sentence':'Benefit estimation requires a catchment, baseline and reviewed intervention model.' if funded_items else 'No projects selected; no planned benefit.',
            'district_breakdown':[]}
