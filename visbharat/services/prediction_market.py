"""
visbharat/services/prediction_market.py
========================================
Bharat Futures — Civic Prediction Market & Hybrid AI Super-Predictor Engine

Implements information aggregation mechanisms (Hayekian market signals combined with
Vertex AI spatial/contractor risk priors) for pre-sanction public capital protection.
"""

import math
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional

# Pre-seeded pilot infrastructure projects undergoing pre-sanction evaluation
PILOT_PREDICTION_PROJECTS = [
    {
        "project_id": "PRJ-2024-KAR-019",
        "title": "Jal Jeevan Water Pipeline Extension — Karur Block 4",
        "district": "Karur",
        "state": "Tamil Nadu",
        "estimated_cost_lakhs": 1450,
        "category": "Water Supply",
        "contractor_name": "Kaveri Infra Structures Ltd.",
        "target_completion_months": 12,
        "vertex_ai_prior": 0.78,  # Satellite imagery Shear Strength & Historical Delay Model
        "vertex_ai_factors": {
            "satellite_terrain_hardness": 0.82,
            "contractor_past_delay_score": 0.14,
            "monsoon_flood_risk": 0.22
        },
        "stakes": [
            {"user_id": "usr_eng_01", "role": "Civil Engineer", "outcome": "YES", "points": 100, "weight": 2.5, "timestamp": "2026-09-10T10:00:00Z"},
            {"user_id": "usr_ngo_04", "role": "NGO Representative", "outcome": "YES", "points": 50, "weight": 1.8, "timestamp": "2026-09-10T11:30:00Z"},
            {"user_id": "usr_journo_02", "role": "Journalist", "outcome": "NO", "points": 30, "weight": 1.5, "timestamp": "2026-09-11T09:15:00Z"},
            {"user_id": "usr_cit_89", "role": "Local Citizen", "outcome": "YES", "points": 20, "weight": 1.0, "timestamp": "2026-09-11T14:20:00Z"}
        ]
    },
    {
        "project_id": "PRJ-2024-BLR-104",
        "title": "Urban Stormwater Drain Desilting & Culvert Upgrade — Bangalore Urban Ward 15",
        "district": "Bangalore Urban",
        "state": "Karnataka",
        "estimated_cost_lakhs": 2200,
        "category": "Sanitation",
        "contractor_name": "Deccan Civic Works",
        "target_completion_months": 8,
        "vertex_ai_prior": 0.42,  # Flagged by Vertex AI due to land acquisition friction
        "vertex_ai_factors": {
            "satellite_terrain_hardness": 0.45,
            "contractor_past_delay_score": 0.68,
            "monsoon_flood_risk": 0.74
        },
        "stakes": [
            {"user_id": "usr_eng_08", "role": "Civil Engineer", "outcome": "NO", "points": 120, "weight": 2.5, "timestamp": "2026-09-11T08:00:00Z"},
            {"user_id": "usr_cit_12", "role": "Local Citizen", "outcome": "NO", "points": 80, "weight": 1.0, "timestamp": "2026-09-11T10:45:00Z"},
            {"user_id": "usr_ngo_02", "role": "NGO Representative", "outcome": "NO", "points": 60, "weight": 1.8, "timestamp": "2026-09-12T12:10:00Z"},
            {"user_id": "usr_cit_99", "role": "Local Citizen", "outcome": "YES", "points": 15, "weight": 1.0, "timestamp": "2026-09-12T15:30:00Z"}
        ]
    },
    {
        "project_id": "PRJ-2024-TRP-055",
        "title": "Solar-Powered Primary Health Sub-Center Grid — Tirupati Rural",
        "district": "Tirupati",
        "state": "Andhra Pradesh",
        "estimated_cost_lakhs": 680,
        "category": "Health",
        "contractor_name": "Rayalaseema Energy Solutions",
        "target_completion_months": 6,
        "vertex_ai_prior": 0.89,
        "vertex_ai_factors": {
            "satellite_terrain_hardness": 0.92,
            "contractor_past_delay_score": 0.05,
            "monsoon_flood_risk": 0.10
        },
        "stakes": [
            {"user_id": "usr_eng_03", "role": "Civil Engineer", "outcome": "YES", "points": 150, "weight": 2.5, "timestamp": "2026-09-12T09:00:00Z"},
            {"user_id": "usr_cit_34", "role": "Local Citizen", "outcome": "YES", "points": 100, "weight": 1.0, "timestamp": "2026-09-12T11:00:00Z"}
        ]
    },
    {
        "project_id": "PRJ-2024-CHN-088",
        "title": "Smart Coastal Canal Flood Barrier Construction — Chennai North",
        "district": "Chennai",
        "state": "Tamil Nadu",
        "estimated_cost_lakhs": 4800,
        "category": "Road",
        "contractor_name": "Coromandel Engineering Corp",
        "target_completion_months": 18,
        "vertex_ai_prior": 0.35,  # High risk due to coastal tidal surge models
        "vertex_ai_factors": {
            "satellite_terrain_hardness": 0.38,
            "contractor_past_delay_score": 0.72,
            "monsoon_flood_risk": 0.88
        },
        "stakes": [
            {"user_id": "usr_eng_11", "role": "Civil Engineer", "outcome": "NO", "points": 200, "weight": 2.5, "timestamp": "2026-09-12T14:00:00Z"},
            {"user_id": "usr_journo_08", "role": "Journalist", "outcome": "NO", "points": 110, "weight": 1.5, "timestamp": "2026-09-12T16:00:00Z"}
        ]
    }
]

# In-memory dynamic stakes store for runtime modifications
_DYNAMIC_PROJECTS = [dict(p, stakes=list(p["stakes"])) for p in PILOT_PREDICTION_PROJECTS]


def compute_quadratic_cost(points: int) -> int:
    """
    Computes Quadratic Wagering Cost C = q^2 for points q.
    Quadratic cost prevents whale manipulation by making large bets exponentially expensive.
    """
    q = max(0, int(points))
    return q * q


def get_role_multiplier(role: str) -> float:
    """
    Returns credential-weighted stake multiplier based on verified user domain expertise.
    """
    r = (role or "").lower()
    if "engineer" in r or "officer" in r:
        return 2.5
    elif "ngo" in r:
        return 1.8
    elif "journalist" in r or "auditor" in r:
        return 1.5
    return 1.0


def compute_hybrid_super_prediction(project: Dict[str, Any]) -> Dict[str, Any]:
    """
    Fuses Market Implied Probability (P_market) with Vertex AI Spatial/Procurement Prior (P_Vertex).
    
    Formula:
        P_market = Weighted_Stakes_YES / (Weighted_Stakes_YES + Weighted_Stakes_NO)
        P_composite = 0.45 * P_Vertex + 0.55 * P_market
    """
    stakes = project.get("stakes", [])
    yes_weighted = 0.0
    no_weighted = 0.0

    for s in stakes:
        pts = float(s.get("points", 0))
        w = float(s.get("weight", 1.0))
        outcome = (s.get("outcome") or "YES").upper()

        # Quadratic effective voting power = sqrt(points) * weight
        power = math.sqrt(pts) * w
        if outcome == "YES":
            yes_weighted += power
        else:
            no_weighted += power

    total_weighted = yes_weighted + no_weighted
    if total_weighted > 0:
        p_market = round(yes_weighted / total_weighted, 4)
    else:
        p_market = 0.50  # Uninformative prior if zero bets

    p_vertex = float(project.get("vertex_ai_prior", 0.60))
    
    # Bayesian hybrid ensemble score
    p_composite = round((0.45 * p_vertex) + (0.55 * p_market), 4)

    # Anomaly detection: check wash betting co-occurrence
    anomaly_status = "CLEAN"
    if len(stakes) >= 3:
        unique_users = len(set(s.get("user_id") for s in stakes))
        if unique_users < len(stakes) * 0.5:
            anomaly_status = "COORDINATED_BETTING_FLAGGED"

    # Pre-Sanction Evaluation Status
    if p_composite >= 0.70:
        status_label = "HIGH SUCCESS CONFIDENCE ✅"
        status_code = "APPROVED"
        recommendation = "Project cleared for immediate administrative sanctioning and budget disbursement."
    elif p_composite >= 0.50:
        status_label = "MODERATE RISK ⚠️"
        status_code = "MODERATE"
        recommendation = "Proceed with caution. Recommend quarterly milestone verification checks."
    else:
        status_label = "PRE-SANCTION HOLD & MANDATORY AUDIT 🛑"
        status_code = "HOLD"
        recommendation = "High risk of project failure or cost overrun. Trigger mandatory pre-sanction engineering review."

    return {
        "project_id": project["project_id"],
        "title": project["title"],
        "district": project["district"],
        "state": project["state"],
        "estimated_cost_lakhs": project["estimated_cost_lakhs"],
        "category": project["category"],
        "contractor_name": project["contractor_name"],
        "target_completion_months": project["target_completion_months"],
        "p_market": p_market,
        "p_vertex": p_vertex,
        "p_composite": p_composite,
        "p_composite_pct": round(p_composite * 100, 1),
        "total_staked_points": sum(int(s.get("points", 0)) for s in stakes),
        "total_participants": len(set(s.get("user_id") for s in stakes)),
        "vertex_ai_factors": project.get("vertex_ai_factors", {}),
        "anomaly_status": anomaly_status,
        "status_label": status_label,
        "status_code": status_code,
        "recommendation": recommendation,
        "stakes": stakes
    }


def list_prediction_market_projects() -> List[Dict[str, Any]]:
    """
    Returns list of all active prediction market projects with super-prediction scores.
    """
    return [compute_hybrid_super_prediction(p) for p in _DYNAMIC_PROJECTS]


def place_civic_stake(project_id: str, user_id: str, role: str, outcome: str, points: int) -> Dict[str, Any]:
    """
    Places a civic reputation stake on a project using quadratic wagering rules.
    """
    target = None
    for p in _DYNAMIC_PROJECTS:
        if p["project_id"] == project_id:
            target = p
            break

    if not target:
        raise ValueError(f"Project '{project_id}' not found in prediction market registry.")

    pts = max(1, min(points, 500))  # Capped between 1 and 500 Karma points
    cost = compute_quadratic_cost(pts)
    weight = get_role_multiplier(role)

    new_stake = {
        "user_id": user_id or f"usr_{int(time.time())}",
        "role": role or "Local Citizen",
        "outcome": (outcome or "YES").upper(),
        "points": pts,
        "weight": weight,
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    target["stakes"].append(new_stake)

    updated_summary = compute_hybrid_super_prediction(target)
    updated_summary["stake_placed"] = new_stake
    updated_summary["quadratic_karma_cost"] = cost
    return updated_summary
