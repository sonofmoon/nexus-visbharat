from typing import Dict, List
from datetime import datetime, timezone, timedelta
import math

from flask import current_app

from ..db import get_db
from .demand_analytics import compute_demand_velocity
from .scoring import compute_priority_score, get_scoring_weights, get_active_scoring_profile


def _parse_iso_utc(value):
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace('Z', '+00:00'))
    except Exception:
        return None


def _district_demand_map() -> Dict[str, int]:
    result = {}
    try:
        bq = current_app.extensions.get('google_bigquery_client')
        if bq:
            rows = bq._run(
                f'''
                SELECT district, COUNT(1) AS c
                FROM {bq.table_ref}
                GROUP BY district
                '''
            )
        else:
            db = get_db()
            rows = db.execute(
                '''
                SELECT district, COUNT(*) AS c
                FROM citizen_requests
                GROUP BY district
                '''
            ).fetchall()
        for r in rows:
            district = str(r['district'] or '').strip()
            if not district:
                continue
            result[district] = int(r['c'] or 0)
    except Exception:
        pass

    return result


def _district_emergency_map() -> Dict[str, int]:
    result = {}
    try:
        bq = current_app.extensions.get('google_bigquery_client')
        if bq:
            rows = bq._run(
                f'''
                SELECT district, COUNT(1) AS c
                FROM {bq.table_ref}
                WHERE LOWER(urgency) = 'emergency'
                GROUP BY district
                '''
            )
        else:
            db = get_db()
            rows = db.execute(
                '''
                SELECT district, COUNT(*) AS c
                FROM citizen_requests
                WHERE LOWER(urgency) = 'emergency'
                GROUP BY district
                '''
            ).fetchall()
        for r in rows:
            district = str(r['district'] or '').strip()
            if not district:
                continue
            result[district] = int(r['c'] or 0)
    except Exception:
        pass

    return result


def compute_gap_analysis(limit=50) -> List[dict]:
    repo = current_app.extensions['reference_repo']
    demand = _district_demand_map()

    rows = []
    for _, r in repo.df_districts.iterrows():
        district = str(r['district'])
        population = float(r.get('population', 0) or 0)
        deprivation_index = float(r.get('deprivation_index', 0) or 0)
        road = float(r.get('road_coverage', 0) or 0)
        water = float(r.get('water_coverage', 0) or 0)
        power = float(r.get('electricity_coverage', 0) or 0)

        avg_coverage = (road + water + power) / 3.0
        infra_gap = max(0.0, 100.0 - avg_coverage)
        complaints = demand.get(district, 0)
        demand_per_100k = (complaints / population) * 100000.0 if population > 0 else 0.0

        gap_score = (infra_gap * 0.55) + (deprivation_index * 100.0 * 0.30) + (demand_per_100k * 0.15)

        rows.append(
            {
                'district': district,
                'state': str(r.get('state', 'Unknown')),
                'population': int(population) if population else 0,
                'complaints': complaints,
                'demand_per_100k': round(demand_per_100k, 3),
                'infrastructure_gap': round(infra_gap, 3),
                'deprivation_index': round(deprivation_index, 3),
                'gap_score': round(gap_score, 3),
            }
        )

    rows.sort(key=lambda x: x['gap_score'], reverse=True)
    return rows[: min(int(limit), 500)]


def compute_hotspot_predictions(limit=50) -> List[dict]:
    repo = current_app.extensions['reference_repo']
    demand = _district_demand_map()
    emergencies = _district_emergency_map()

    rows = []
    for _, r in repo.df_districts.iterrows():
        district = str(r['district'])
        population = float(r.get('population', 0) or 0)
        deprivation_index = float(r.get('deprivation_index', 0) or 0)
        road = float(r.get('road_coverage', 0) or 0)
        water = float(r.get('water_coverage', 0) or 0)
        power = float(r.get('electricity_coverage', 0) or 0)

        avg_coverage = (road + water + power) / 3.0
        complaints = demand.get(district, 0)
        emergency = emergencies.get(district, 0)

        demand_per_100k = (complaints / population) * 100000.0 if population > 0 else 0.0
        emergency_ratio = (emergency / complaints) if complaints > 0 else 0.0

        stress_score = (
            (100.0 - avg_coverage) * 0.35
            + (deprivation_index * 100.0) * 0.25
            + demand_per_100k * 0.25
            + (emergency_ratio * 100.0) * 0.15
        )

        rows.append(
            {
                'district': district,
                'state': str(r.get('state', 'Unknown')),
                'complaints': complaints,
                'emergency_complaints': emergency,
                'demand_per_100k': round(demand_per_100k, 3),
                'emergency_ratio': round(emergency_ratio, 4),
                'predicted_stress_score_next_quarter': round(stress_score, 3),
            }
        )

    rows.sort(key=lambda x: x['predicted_stress_score_next_quarter'], reverse=True)
    return rows[: min(int(limit), 500)]


def compute_priority_rankings_with_budget(limit=25, total_budget_lakh=5000.0) -> dict:
    ranked_gap = compute_gap_analysis(limit=500)
    ranked_hotspots = compute_hotspot_predictions(limit=500)
    velocity_payload = compute_demand_velocity(days=30, limit=500)

    repo = current_app.extensions['reference_repo']

    db = get_db()
    try:
        cosign_rows = db.execute(
            '''
            SELECT r.district, COUNT(c.id) AS verified_cosigns
            FROM request_cosigns c
            JOIN citizen_requests r ON r.request_id = c.request_id
            WHERE c.verified = 1
            GROUP BY r.district
            '''
        ).fetchall()
    except Exception:
        cosign_rows = []
    cosign_index = {str(row['district']): int(row['verified_cosigns'] or 0) for row in cosign_rows}

    try:
        cluster_rows = db.execute(
            '''
            SELECT r.district, COUNT(DISTINCT m.request_id) AS deduped_cluster_size
            FROM cluster_members m
            JOIN citizen_requests r ON r.request_id = m.request_id
            GROUP BY r.district
            '''
        ).fetchall()
    except Exception:
        cluster_rows = []
    cluster_index = {str(row['district']): int(row['deduped_cluster_size'] or 0) for row in cluster_rows}

    try:
        investment_rows = db.execute(
            '''
            SELECT district, SUM(estimated_project_cost_lakh) AS investment_lakh
            FROM policy_decisions
            WHERE status IN ('approved', 'funded')
            GROUP BY district
            '''
        ).fetchall()
    except Exception:
        investment_rows = []
    investment_index = {str(row['district']): float(row['investment_lakh'] or 0.0) for row in investment_rows}

    velocity_index = {
        str(item.get('district')): float(item.get('velocity_index', 0.0) or 0.0)
        for item in (velocity_payload.get('items') or [])
    }

    eag_states_cfg = str(current_app.config.get('SCORING_EAG_STATES', 'Bihar,Chhattisgarh,Jharkhand,Madhya Pradesh,Odisha,Rajasthan,Uttar Pradesh,Uttarakhand')).strip()
    aspirational_cfg = str(current_app.config.get('SCORING_ASPIRATIONAL_DISTRICTS', '')).strip()
    lwe_cfg = str(current_app.config.get('SCORING_LWE_DISTRICTS', '')).strip()
    tribal_cfg = str(current_app.config.get('SCORING_TRIBAL_DISTRICTS', '')).strip()

    eag_states = {s.strip().lower() for s in eag_states_cfg.split(',') if s.strip()}
    aspirational_districts = {s.strip().lower() for s in aspirational_cfg.split(',') if s.strip()}
    lwe_districts = {s.strip().lower() for s in lwe_cfg.split(',') if s.strip()}
    tribal_districts = {s.strip().lower() for s in tribal_cfg.split(',') if s.strip()}

    scheme_norm_per_beneficiary_lakh = max(float(current_app.config.get('SCORING_SCHEME_NORM_PER_BENEFICIARY_LAKH', 0.05) or 0.05), 0.001)

    active_profile = get_active_scoring_profile()
    weight_profile = get_scoring_weights(profile=str(active_profile.get('active_profile') or 'default'))
    weights = dict(weight_profile.get('weights') or {})

    hotspot_index = {str(r['district']): r for r in ranked_hotspots}
    rows = []
    prepared = []

    district_population = {
        str(r.get('district')): int(float(r.get('population', 0) or 0))
        for _, r in repo.df_districts.iterrows()
    }

    for g in ranked_gap:
        district = str(g['district'])
        state = str(g.get('state', 'Unknown'))
        h = hotspot_index.get(district, {})
        stress_score = float(h.get('predicted_stress_score_next_quarter', 0.0) or 0.0)

        verified_cosigns = int(cosign_index.get(district, 0) or 0)
        cluster_size = int(cluster_index.get(district, 0) or 0)
        complaints = int(g.get('complaints', 0) or 0)
        deduped_cluster_size = max(cluster_size, complaints)

        infra_gap = float(g.get('infrastructure_gap', 0.0) or 0.0)
        deprivation_index = float(g.get('deprivation_index', 0.0) or 0.0)
        population = int(g.get('population', 0) or district_population.get(district, 0) or 0)

        investment_already_made_lakh = float(investment_index.get(district, 0.0) or 0.0)
        estimated_cost_lakh = (
            120.0
            + (infra_gap * 12.0)
            + (deprivation_index * 100.0 * 1.5)
            + (complaints * 2.0)
        )

        per_beneficiary_cost_lakh = (float(estimated_cost_lakh) / max(float(population), 1.0)) if population > 0 else float(estimated_cost_lakh)
        relative_cost = per_beneficiary_cost_lakh / scheme_norm_per_beneficiary_lakh

        district_key = district.strip().lower()
        equity_mandate = 0.0
        if state.strip().lower() in eag_states:
            equity_mandate += 35.0
        if district_key in aspirational_districts:
            equity_mandate += 25.0
        if district_key in lwe_districts:
            equity_mandate += 20.0
        if district_key in tribal_districts:
            equity_mandate += 20.0
        equity_mandate = min(100.0, equity_mandate)

        prepared.append(
            {
                'district': district,
                'state': state,
                'population': population,
                'complaints': complaints,
                'deduped_cluster_size': deduped_cluster_size,
                'velocity_index': float(velocity_index.get(district, 0.0) or 0.0),
                'deprivation_index': deprivation_index,
                'infrastructure_gap': infra_gap,
                'investment_already_made_lakh': investment_already_made_lakh,
                'relative_cost': relative_cost,
                'equity_mandate': equity_mandate,
                'estimated_project_cost_lakh': round(max(25.0, estimated_cost_lakh), 2),
                'verified_support_count': verified_cosigns,
                'predicted_stress_score_next_quarter': round(stress_score, 3),
                'gap_score': float(g.get('gap_score', 0.0) or 0.0),
            }
        )

    max_demand_density = 0.0
    max_velocity = 0.0
    max_investment = 0.0
    max_relative_cost = 0.0
    for item in prepared:
        population = max(float(item.get('population', 0) or 0), 1.0)
        density = (float(item.get('deduped_cluster_size', 0) or 0) / population) * 100000.0
        max_demand_density = max(max_demand_density, density)
        max_velocity = max(max_velocity, float(item.get('velocity_index', 0.0) or 0.0))
        max_investment = max(max_investment, float(item.get('investment_already_made_lakh', 0.0) or 0.0))
        max_relative_cost = max(max_relative_cost, float(item.get('relative_cost', 0.0) or 0.0))

    scales = {
        'max_demand_density_per_100k': max(max_demand_density, 1.0),
        'max_velocity_index': max(max_velocity, 1.0),
        'max_investment_lakh': max(max_investment, 1.0),
        'max_relative_cost': max(max_relative_cost, 1.0),
    }

    for item in prepared:
        scored = compute_priority_score(item, scales=scales, weights=weights)
        priority_score = float(scored.get('priority_score', 0.0) or 0.0)

        cosign_boost = min(20.0, (int(item.get('verified_support_count', 0) or 0) * 0.15))
        final_score = priority_score + cosign_boost

        rows.append(
            {
                'district': item['district'],
                'state': item['state'],
                'population': int(item['population']),
                'complaints': int(item['complaints']),
                'priority_score': round(final_score, 3),
                'base_priority_score': round(priority_score, 3),
                'cosign_boost': round(cosign_boost, 3),
                'gap_score': float(item.get('gap_score', 0.0) or 0.0),
                'predicted_stress_score_next_quarter': float(item.get('predicted_stress_score_next_quarter', 0.0) or 0.0),
                'estimated_project_cost_lakh': float(item['estimated_project_cost_lakh']),
                'verified_support_count': int(item.get('verified_support_count', 0) or 0),
                'score_components': scored.get('components', {}),
                'score_explainability': {
                    'formula': 'P=+w1*demand_density +w2*demand_velocity +w3*deprivation +w4*infrastructure_gap -w5*investment_already_made -w6*relative_cost +w7*equity_mandate +cosign_boost',
                    'weights': scored.get('weights', {}),
                    'normalized': scored.get('components', {}),
                    'raw': scored.get('raw', {}),
                },
            }
        )

    rows.sort(key=lambda x: x['priority_score'], reverse=True)
    rows = rows[: min(int(limit), 500)]

    budget_remaining = max(0.0, float(total_budget_lakh))
    budget_used = 0.0

    for idx, item in enumerate(rows, start=1):
        cost = float(item['estimated_project_cost_lakh'])
        if cost <= budget_remaining:
            funded = True
            budget_remaining -= cost
            budget_used += cost
        else:
            funded = False

        item['rank'] = idx
        item['funded_in_draft_plan'] = funded
        item['cumulative_budget_used_lakh'] = round(budget_used, 2)

    return {
        'total_budget_lakh': round(float(total_budget_lakh), 2),
        'budget_used_lakh': round(budget_used, 2),
        'budget_remaining_lakh': round(max(0.0, budget_remaining), 2),
        'funded_count': len([r for r in rows if r['funded_in_draft_plan']]),
        'total_ranked': len(rows),
        'items': rows,
    }



def compute_impact_metrics_and_auto_brief(limit=25, total_budget_lakh=5000.0) -> dict:
    ranking = compute_priority_rankings_with_budget(limit=limit, total_budget_lakh=total_budget_lakh)
    items = ranking.get('items', [])
    funded = [row for row in items if bool(row.get('funded_in_draft_plan'))]

    total_population_covered = sum(int(row.get('population', 0) or 0) for row in funded)
    total_complaints_targeted = sum(int(row.get('complaints', 0) or 0) for row in funded)

    avg_priority_score = (
        sum(float(row.get('priority_score', 0.0) or 0.0) for row in items) / len(items)
        if items else 0.0
    )
    avg_funded_priority_score = (
        sum(float(row.get('priority_score', 0.0) or 0.0) for row in funded) / len(funded)
        if funded else 0.0
    )

    funding_coverage_ratio = (len(funded) / len(items)) if items else 0.0

    top_funded = funded[:3]
    focus_districts = [row['district'] for row in top_funded]

    summary = (
        f"Draft allocation funds {len(funded)} of {len(items)} ranked districts, "
        f"covering about {total_population_covered:,} people with "
        f"{total_complaints_targeted} mapped complaints in current data."
    )

    recommendations = [
        "Approve immediate execution for top funded districts and publish district-wise timelines.",
        "Ring-fence contingency funds for high-stress districts not funded in the first draft.",
        "Track monthly impact using complaint closure rate and emergency complaint reduction.",
    ]

    risks = [
        "Rankings use deterministic proxy scoring and should be calibrated with field validation.",
        "Uneven complaint reporting across districts can bias demand-weighted prioritization.",
    ]

    # Deterministic Human-Capital ROI Computation
    from .human_capital_roi import compute_aggregate_human_capital_roi
    hc_roi = compute_aggregate_human_capital_roi(funded)

    summary_with_hc = summary + " " + hc_roi['summary_sentence']

    return {
        'total_budget_lakh': ranking.get('total_budget_lakh', 0.0),
        'budget_used_lakh': ranking.get('budget_used_lakh', 0.0),
        'budget_remaining_lakh': ranking.get('budget_remaining_lakh', 0.0),
        'funded_count': ranking.get('funded_count', 0),
        'total_ranked': ranking.get('total_ranked', 0),
        'impact_metrics': {
            'funding_coverage_ratio': round(funding_coverage_ratio, 4),
            'population_covered_estimate': int(total_population_covered),
            'complaints_targeted': int(total_complaints_targeted),
            'average_priority_score': round(avg_priority_score, 3),
            'average_funded_priority_score': round(avg_funded_priority_score, 3),
            'human_capital_child_population_covered': hc_roi['total_child_population_covered'],
            'human_capital_stunting_cases_averted': hc_roi['total_stunting_cases_averted'],
            'human_capital_school_days_gained': hc_roi['total_school_days_gained'],
            'human_capital_npv_earnings_lakh': hc_roi['total_npv_earnings_uplift_lakh'],
        },
        'auto_brief': {
            'summary': summary_with_hc,
            'focus_districts': focus_districts,
            'recommendations': recommendations,
            'risks': risks,
            'human_capital_roi': hc_roi,
        },
        'items': items,
    }


def compute_bigquery_analytics(limit=50) -> dict:
    safe_limit = min(max(int(limit), 1), 500)

    bigquery_client = current_app.extensions.get('google_bigquery_client')
    if bigquery_client:
        try:
            result = bigquery_client.aggregate_requests(limit=safe_limit)
            result.setdefault('mode', 'bigquery_live')
            result.setdefault('warehouse', 'bigquery')
            return result
        except Exception:
            pass

    db = get_db()

    district_rows = db.execute(
        '''
        SELECT district, state,
               COUNT(*) AS request_count,
               SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count,
               SUM(CASE WHEN urgency IN ('Emergency', 'Urgent') THEN 1 ELSE 0 END) AS high_priority_count,
               COUNT(DISTINCT category) AS category_diversity
        FROM citizen_requests
        GROUP BY district, state
        ORDER BY request_count DESC, emergency_count DESC, district ASC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    category_rows = db.execute(
        '''
        SELECT category,
               COUNT(*) AS request_count,
               SUM(CASE WHEN urgency = 'Emergency' THEN 1 ELSE 0 END) AS emergency_count
        FROM citizen_requests
        GROUP BY category
        ORDER BY request_count DESC, category ASC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    source_rows = db.execute(
        '''
        SELECT source_channel, COUNT(*) AS request_count
        FROM citizen_requests
        GROUP BY source_channel
        ORDER BY request_count DESC, source_channel ASC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    trend_rows = db.execute(
        '''
        SELECT SUBSTR(created_at, 1, 10) AS day, COUNT(*) AS request_count
        FROM citizen_requests
        GROUP BY SUBSTR(created_at, 1, 10)
        ORDER BY day DESC
        LIMIT ?
        ''',
        (safe_limit,),
    ).fetchall()

    return {
        'mode': 'local_sql_analytics',
        'warehouse': 'bigquery-compatible-metrics',
        'district_aggregates': [
            {
                'district': row['district'],
                'state': row['state'],
                'request_count': int(row['request_count'] or 0),
                'emergency_count': int(row['emergency_count'] or 0),
                'high_priority_count': int(row['high_priority_count'] or 0),
                'category_diversity': int(row['category_diversity'] or 0),
            }
            for row in district_rows
        ],
        'category_aggregates': [
            {
                'category': row['category'],
                'request_count': int(row['request_count'] or 0),
                'emergency_count': int(row['emergency_count'] or 0),
            }
            for row in category_rows
        ],
        'source_aggregates': [
            {
                'source_channel': row['source_channel'],
                'request_count': int(row['request_count'] or 0),
            }
            for row in source_rows
        ],
        'daily_trend': [
            {
                'day': row['day'],
                'request_count': int(row['request_count'] or 0),
            }
            for row in trend_rows
        ],
    }

def _risk_band_for_stress(stress: float) -> tuple[str, float]:
    if stress >= 65:
        return 'critical', 0.89
    if stress >= 45:
        return 'high', 0.84
    if stress >= 25:
        return 'medium', 0.79
    return 'low', 0.74


def compute_vertex_predictions(limit=50) -> dict:
    safe_limit = min(max(int(limit), 1), 500)
    baseline = compute_hotspot_predictions(limit=safe_limit)

    vertex_client = current_app.extensions.get('google_vertex_client')
    if vertex_client:
        try:
            instances = [
                {
                    'predicted_stress_score_next_quarter': float(row.get('predicted_stress_score_next_quarter', 0.0) or 0.0),
                    'complaints': int(row.get('complaints', 0) or 0),
                    'emergency_complaints': int(row.get('emergency_complaints', 0) or 0),
                    'demand_per_100k': float(row.get('demand_per_100k', 0.0) or 0.0),
                    'emergency_ratio': float(row.get('emergency_ratio', 0.0) or 0.0),
                }
                for row in baseline
            ]
            live_preds = vertex_client.predict_stress(instances=instances)

            items = []
            for index, row in enumerate(baseline):
                lp = live_preds[index] if index < len(live_preds) else {}
                stress = float(lp.get('predicted_stress_score_next_quarter', row.get('predicted_stress_score_next_quarter', 0.0)) or 0.0)
                risk_band = str(lp.get('risk_band', '') or '').strip().lower()
                confidence = float(lp.get('model_confidence', 0.0) or 0.0)
                model_name = str(lp.get('model_name', 'vertex-live-endpoint'))

                if risk_band not in {'critical', 'high', 'medium', 'low'}:
                    risk_band, default_conf = _risk_band_for_stress(stress)
                    if confidence <= 0:
                        confidence = default_conf
                elif confidence <= 0:
                    _, confidence = _risk_band_for_stress(stress)

                items.append(
                    {
                        'district': row.get('district'),
                        'state': row.get('state'),
                        'predicted_stress_score_next_quarter': round(stress, 3),
                        'risk_band': risk_band,
                        'model_confidence': round(confidence, 3),
                        'model_name': model_name,
                    }
                )

            return {
                'mode': 'vertex_live',
                'prediction_horizon': 'next_quarter',
                'governance_label': 'Provisional Demand Stress Index (Baseline Screening)',
                'model_lineage': {
                    'model_card': '/docs/evaluation/MODEL_CARD_STRESS_FORECASTING.md',
                    'lineage': 'Vertex AI Tabular AutoML / Regional Baseline Projection',
                    'feature_schema': ['predicted_stress_score_next_quarter', 'complaints', 'emergency_complaints', 'demand_per_100k', 'emergency_ratio'],
                    'drift_monitoring': 'Population Stability Index (PSI) & Distribution Divergence'
                },
                'items': items,
            }
        except Exception:
            pass

    items = []
    for row in baseline:
        stress = float(row.get('predicted_stress_score_next_quarter', 0.0) or 0.0)
        risk_band, confidence = _risk_band_for_stress(stress)
        items.append(
            {
                'district': row.get('district'),
                'state': row.get('state'),
                'predicted_stress_score_next_quarter': round(stress, 3),
                'risk_band': risk_band,
                'model_confidence': confidence,
                'model_name': 'vertex-automl-proxy-v1',
            }
        )

    return {
        'mode': 'vertex_proxy_model',
        'prediction_horizon': 'next_quarter',
        'governance_label': 'Provisional Demand Stress Index (Baseline Screening)',
        'model_lineage': {
            'model_card': '/docs/evaluation/MODEL_CARD_STRESS_FORECASTING.md',
            'lineage': 'Local Baseline Exponential Smoothing & Population Deprivation Proxy',
            'feature_schema': ['predicted_stress_score_next_quarter', 'complaints', 'emergency_complaints', 'demand_per_100k', 'emergency_ratio'],
            'drift_monitoring': 'Census 2011/LGD Deprivation Weight Consistency'
        },
        'items': items,
    }


def compute_demand_decay_impact(
    district: str | None = None,
    decision_id: str | None = None,
    pre_days: int = 30,
    post_days: int = 30,
    anchor_at: str | None = None,
    comparator_mode: str = 'state',
    peer_districts: list[str] | None = None,
    category: str | None = None,
    routed_department: str | None = None,
    channel: str | None = None,
) -> dict:
    db = get_db()

    safe_pre_days = min(max(int(pre_days or 30), 7), 365)
    safe_post_days = min(max(int(post_days or 30), 7), 365)

    decision = None
    resolved_district = str(district or '').strip()
    resolved_anchor = _parse_iso_utc(anchor_at)

    if decision_id:
        row = db.execute(
            '''
            SELECT decision_id, district, status, approved_at, created_at, estimated_project_cost_lakh, total_budget_lakh
            FROM policy_decisions
            WHERE decision_id = ?
            LIMIT 1
            ''',
            (str(decision_id).strip(),),
        ).fetchone()
        if row:
            decision = dict(row)
            if not resolved_district:
                resolved_district = str(row['district'] or '').strip()
            if not resolved_anchor:
                resolved_anchor = _parse_iso_utc(row['approved_at']) or _parse_iso_utc(row['created_at'])

    if not resolved_district:
        raise ValueError('district or decision_id is required')

    if not resolved_anchor:
        resolved_anchor = datetime.now(timezone.utc)

    repo = current_app.extensions['reference_repo']
    state = 'Unknown'
    try:
        district_rows = repo.df_districts[repo.df_districts['district'] == resolved_district]
        if len(district_rows.index) > 0:
            state = str(district_rows.iloc[0].get('state') or 'Unknown')
    except Exception:
        pass

    pre_start = (resolved_anchor - timedelta(days=safe_pre_days)).isoformat().replace('+00:00', 'Z')
    anchor_iso = resolved_anchor.isoformat().replace('+00:00', 'Z')
    post_end = (resolved_anchor + timedelta(days=safe_post_days)).isoformat().replace('+00:00', 'Z')

    normalized_category = str(category or '').strip()
    normalized_routed_department = str(routed_department or '').strip()
    normalized_channel = str(channel or '').strip()

    def _segment_clause_and_params(alias=''):
        prefix = f"{alias}." if alias else ''
        clauses = []
        params = []
        if normalized_category:
            clauses.append(f"{prefix}category = ?")
            params.append(normalized_category)
        if normalized_routed_department:
            clauses.append(f"{prefix}routed_department = ?")
            params.append(normalized_routed_department)
        if normalized_channel:
            clauses.append(f"{prefix}source_channel = ?")
            params.append(normalized_channel)
        return clauses, params

    def _count_requests(where_base: str, base_params: list, start_at: str, end_at: str):
        segment_clauses, segment_params = _segment_clause_and_params()
        sql = [
            'SELECT COUNT(*) AS c',
            'FROM citizen_requests',
            f'WHERE {where_base}',
            'AND created_at >= ?',
            'AND created_at < ?',
        ]
        if segment_clauses:
            sql.extend([f'AND {clause}' for clause in segment_clauses])
        row = db.execute(
            '\n'.join(sql),
            tuple(base_params + [start_at, end_at] + segment_params),
        ).fetchone()
        return int(row['c'] or 0) if row else 0

    def _fetch_event_datetimes(where_base: str, base_params: list, start_at: str, end_at: str):
        segment_clauses, segment_params = _segment_clause_and_params()
        sql = [
            'SELECT created_at',
            'FROM citizen_requests',
            f'WHERE {where_base}',
            'AND created_at >= ?',
            'AND created_at < ?',
        ]
        if segment_clauses:
            sql.extend([f'AND {clause}' for clause in segment_clauses])
        rows = db.execute(
            '\n'.join(sql),
            tuple(base_params + [start_at, end_at] + segment_params),
        ).fetchall()
        out = []
        for row in rows:
            dt = _parse_iso_utc(row['created_at'])
            if dt:
                out.append(dt)
        return out

    def _seasonality_factor(pre_events: list, comp_pre_events: list, comp_post_events: list):
        if not pre_events or not comp_pre_events or not comp_post_events:
            return 1.0, {'weekday_effect': 1.0, 'month_effect': 1.0}

        pre_weekday = {i: 0 for i in range(7)}
        comp_pre_weekday = {i: 0 for i in range(7)}
        comp_post_weekday = {i: 0 for i in range(7)}
        pre_month = {i: 0 for i in range(1, 13)}
        comp_pre_month = {i: 0 for i in range(1, 13)}
        comp_post_month = {i: 0 for i in range(1, 13)}

        for dt in pre_events:
            pre_weekday[dt.weekday()] += 1
            pre_month[dt.month] += 1
        for dt in comp_pre_events:
            comp_pre_weekday[dt.weekday()] += 1
            comp_pre_month[dt.month] += 1
        for dt in comp_post_events:
            comp_post_weekday[dt.weekday()] += 1
            comp_post_month[dt.month] += 1

        total_pre = float(len(pre_events))
        total_comp_pre = float(len(comp_pre_events))
        total_comp_post = float(len(comp_post_events))

        weekday_effect = 0.0
        month_effect = 0.0
        for day in range(7):
            w = pre_weekday[day] / max(total_pre, 1.0)
            pre_r = comp_pre_weekday[day] / max(total_comp_pre, 1.0)
            post_r = comp_post_weekday[day] / max(total_comp_post, 1.0)
            ratio = post_r / max(pre_r, 1e-9)
            weekday_effect += w * ratio

        for month_no in range(1, 13):
            w = pre_month[month_no] / max(total_pre, 1.0)
            pre_r = comp_pre_month[month_no] / max(total_comp_pre, 1.0)
            post_r = comp_post_month[month_no] / max(total_comp_post, 1.0)
            ratio = post_r / max(pre_r, 1e-9)
            month_effect += w * ratio

        factor = max((weekday_effect + month_effect) / 2.0, 1e-6)
        return factor, {'weekday_effect': round(weekday_effect, 6), 'month_effect': round(month_effect, 6)}

    comparator_mode_norm = str(comparator_mode or 'state').strip().lower() or 'state'
    if comparator_mode_norm not in {'state', 'peer'}:
        raise ValueError('comparator_mode must be state or peer')

    chosen_peers = []
    if comparator_mode_norm == 'peer':
        if peer_districts:
            chosen_peers = [str(d).strip() for d in peer_districts if str(d).strip() and str(d).strip() != resolved_district]
        if not chosen_peers:
            try:
                target_row = repo.df_districts[repo.df_districts['district'] == resolved_district].iloc[0]
                target_pop = float(target_row.get('population', 0) or 0)
                peer_rows = repo.df_districts[(repo.df_districts['state'] == state) & (repo.df_districts['district'] != resolved_district)].copy()
                if len(peer_rows.index) > 0:
                    peer_rows['pop_distance'] = (peer_rows['population'].astype(float) - target_pop).abs()
                    peer_rows = peer_rows.sort_values(by=['pop_distance', 'district'], ascending=[True, True])
                    chosen_peers = [str(x) for x in peer_rows['district'].head(3).tolist()]
            except Exception:
                chosen_peers = []
        if not chosen_peers:
            comparator_mode_norm = 'state'

    pre_count = _count_requests('district = ?', [resolved_district], pre_start, anchor_iso)
    post_count = _count_requests('district = ?', [resolved_district], anchor_iso, post_end)

    if comparator_mode_norm == 'peer':
        placeholders = ','.join(['?'] * len(chosen_peers))
        base_where = f'district IN ({placeholders})'
        base_params = list(chosen_peers)
        comp_label = 'peer'
    else:
        base_where = 'state = ?'
        base_params = [state]
        comp_label = 'state'

    comparator_pre_count = _count_requests(base_where, base_params, pre_start, anchor_iso)
    comparator_post_count = _count_requests(base_where, base_params, anchor_iso, post_end)

    pre_rate = pre_count / float(safe_pre_days)
    post_rate = post_count / float(safe_post_days)
    comparator_pre_rate = comparator_pre_count / float(safe_pre_days)
    comparator_post_rate = comparator_post_count / float(safe_post_days)

    pre_events = _fetch_event_datetimes('district = ?', [resolved_district], pre_start, anchor_iso)
    comparator_pre_events = _fetch_event_datetimes(base_where, base_params, pre_start, anchor_iso)
    comparator_post_events = _fetch_event_datetimes(base_where, base_params, anchor_iso, post_end)

    seasonality_factor, seasonality_effects = _seasonality_factor(pre_events, comparator_pre_events, comparator_post_events)
    seasonality_adjusted_post_rate = post_rate / max(seasonality_factor, 1e-9)

    demand_decay_pct = ((pre_rate - seasonality_adjusted_post_rate) / max(pre_rate, 1e-9)) * 100.0
    comparator_decay_pct = ((comparator_pre_rate - comparator_post_rate) / max(comparator_pre_rate, 1e-9)) * 100.0
    baseline_adjusted_decay_pct = demand_decay_pct - comparator_decay_pct

    diff_rate = pre_rate - seasonality_adjusted_post_rate
    comparator_diff_rate = comparator_pre_rate - comparator_post_rate
    se_rate = math.sqrt((pre_count / float(safe_pre_days * safe_pre_days)) + (post_count / float(safe_post_days * safe_post_days)))
    comparator_se_rate = math.sqrt((comparator_pre_count / float(safe_pre_days * safe_pre_days)) + (comparator_post_count / float(safe_post_days * safe_post_days)))

    ci_low_rate = diff_rate - (1.96 * se_rate)
    ci_high_rate = diff_rate + (1.96 * se_rate)
    adj_se_rate = math.sqrt((se_rate * se_rate) + (comparator_se_rate * comparator_se_rate))
    adj_low_rate = (diff_rate - comparator_diff_rate) - (1.96 * adj_se_rate)
    adj_high_rate = (diff_rate - comparator_diff_rate) + (1.96 * adj_se_rate)

    ci_low_pct = (ci_low_rate / max(pre_rate, 1e-9)) * 100.0
    ci_high_pct = (ci_high_rate / max(pre_rate, 1e-9)) * 100.0
    adj_low_pct = (adj_low_rate / max(pre_rate, 1e-9)) * 100.0
    adj_high_pct = (adj_high_rate / max(pre_rate, 1e-9)) * 100.0

    district_samples = int(pre_count + post_count)
    comparator_samples = int(comparator_pre_count + comparator_post_count)
    district_sufficient = district_samples >= 30
    comparator_sufficient = comparator_samples >= 30

    ci_width = abs(float(adj_high_pct) - float(adj_low_pct))
    min_samples = min(district_samples, comparator_samples)

    if min_samples >= 120 and ci_width <= 20:
        reliability_tag = 'high'
    elif min_samples >= 60 and ci_width <= 40:
        reliability_tag = 'medium'
    else:
        reliability_tag = 'low'

    cautions = []
    if not district_sufficient:
        cautions.append('District sample size is below sufficiency threshold (30 events).')
    if not comparator_sufficient:
        cautions.append(f"{comp_label.capitalize()} comparator sample size is below sufficiency threshold (30 events).")
    if ci_width > 50:
        cautions.append('Confidence interval is wide; interpret impact signal with caution.')
    if reliability_tag == 'low':
        cautions.append('Reliability is low for policy action without additional monitoring window.')

    return {
        'district': resolved_district,
        'state': state,
        'decision': decision,
        'anchor_at': anchor_iso,
        'windows': {
            'pre_days': safe_pre_days,
            'post_days': safe_post_days,
            'pre_start': pre_start,
            'pre_end': anchor_iso,
            'post_start': anchor_iso,
            'post_end': post_end,
        },
        'counts': {
            'district_pre': pre_count,
            'district_post': post_count,
            f'{comp_label}_pre': comparator_pre_count,
            f'{comp_label}_post': comparator_post_count,
        },
        'rates_per_day': {
            'district_pre': round(pre_rate, 6),
            'district_post': round(post_rate, 6),
            'district_post_seasonality_adjusted': round(seasonality_adjusted_post_rate, 6),
            f'{comp_label}_pre': round(comparator_pre_rate, 6),
            f'{comp_label}_post': round(comparator_post_rate, 6),
        },
        'comparator': {
            'mode': comp_label,
            'state': state,
            'peer_districts': chosen_peers,
        },
        'filters': {
            'category': normalized_category or None,
            'routed_department': normalized_routed_department or None,
            'channel': normalized_channel or None,
        },
        'seasonality': {
            'factor': round(seasonality_factor, 6),
            **seasonality_effects,
        },
        'reliability': {
            'sample_sufficiency': {
                'district': district_sufficient,
                comp_label: comparator_sufficient,
                'district_events': district_samples,
                f'{comp_label}_events': comparator_samples,
                'threshold_events': 30,
            },
            'confidence': {
                'tag': reliability_tag,
                'interval_width_pct': round(ci_width, 3),
            },
            'cautions': cautions,
        },
        'impact': {
            'demand_decay_pct': round(demand_decay_pct, 3),
            f'{comp_label}_baseline_decay_pct': round(comparator_decay_pct, 3),
            'baseline_adjusted_decay_pct': round(baseline_adjusted_decay_pct, 3),
            'confidence_95_pct': {
                'district_decay_low': round(ci_low_pct, 3),
                'district_decay_high': round(ci_high_pct, 3),
                'baseline_adjusted_low': round(adj_low_pct, 3),
                'baseline_adjusted_high': round(adj_high_pct, 3),
            },
            'signal': 'improved' if baseline_adjusted_decay_pct > 0 else 'worsened_or_flat',
        },
    }
