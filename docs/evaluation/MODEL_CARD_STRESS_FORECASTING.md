# Model Card: Nexus VisBharat Demand Stress Forecasting

**Model Version:** `v1.2-pilot-corridor`  
**Date:** September 2026  
**Governance Label:** `Provisional Demand Stress Index (Baseline Screening)`  
**Authors:** Nexus VisBharat AI & Infrastructure Engineering Team  
**Review Status:** Evaluator-Grade Technical Documentation

---

## 1. Model Overview

### 1.1 Model Description
The Demand Stress Forecasting model computes a projected quarter-ahead **Demand Stress Score** (scale: 0–100) and associated **Risk Band** (`Low`, `Medium`, `High`, `Critical`) for administrative districts across India. The system operates in a dual-tier execution architecture:
1. **Tier 1 (Vertex AI Tabular AutoML Endpoint):** Cloud-hosted supervised ensemble trained on regional grievance velocity, demographic deprivation weights, and emergency hazard frequency.
2. **Tier 2 (Deterministic Local Proxy `vertex-automl-proxy-v1`):** Robust, air-gapped fallback model combining exponential moving average (EMA) complaint acceleration with MoPR LGD census population denominators.

### 1.2 Intended Use
- **Primary Use Case:** Proactive civil infrastructure and disaster resource pre-positioning (e.g. allocating water tankers, standby electrical repair teams, monsoon drain clearance crews).
- **Prohibited Out-of-Scope Uses:**
  - Automated budget de-allocation or sanctions without human departmental review.
  - Performance punitive assessments against municipal ground officers.
  - Denying citizen service eligibility or automated ticket dismissal.

---

## 2. Input Features and Schema

All input features are strictly **aggregated district-level or ward-level indicators**. In full compliance with the Digital Personal Data Protection Act (DPDP-2023), **zero citizen personal identifiers, phone numbers, or free-text narrative fields** are ingested into the feature pipeline.

| Feature Name | Data Type | Source Pipeline | Description |
| :--- | :--- | :--- | :--- |
| `complaints_velocity_30d` | `INTEGER` | Cloud Outbox / SQLite Aggregation | Total unique citizen service requests recorded in last 30 days. |
| `emergency_complaints_30d` | `INTEGER` | Fast-Path Emergency Triage | Count of high-voltage wire, gas leak, building collapse, or epidemic alerts. |
| `emergency_ratio` | `FLOAT [0.0 - 1.0]` | Intelligence Service | `emergency_complaints_30d / max(complaints_velocity_30d, 1)`. |
| `demand_per_100k` | `FLOAT` | MoPR LGD & Census 2011 | Population-normalized complaint density per 100,000 residents. |
| `historical_stress_prev_quarter`| `FLOAT [0 - 100]` | BigQuery Historical Partition | Preceding quarter baseline stress index. |
| `infrastructure_deprivation_idx`| `FLOAT [0.0 - 1.0]` | NITI Aayog NDAP / JJM Open Data | Multi-dimensional public utility gap score. |

---

## 3. Training Data & Calibration Lineage

- **Corridor Pilot Baseline:** Trained and calibrated across Tamil Nadu (Chennai, Vellore, Tiruvallur, Kanchipuram), Andhra Pradesh (Chittoor, Tirupati), and Karnataka (Bengaluru Urban, Bengaluru Rural).
- **Ground Truth Definition:** Composite stress benchmark derived from:
  - SLA breach rate exceeding 15% in preceding 90-day window.
  - Multi-ward cluster emergence (>3 complaints in 500m radius within 48 hours).
  - Departmental escalation notifications triggered to District Collectorate.

---

## 4. Evaluation Metrics & Operational Bounds

| Risk Band | Stress Score Range | Action SLA | Model Confidence Baseline |
| :--- | :--- | :--- | :--- |
| **Low** | 0.0 – 24.9 | Routine Monitoring (Quarterly) | 0.74 |
| **Medium** | 25.0 – 49.9 | Preventative Maintenance Inspection | 0.79 |
| **High** | 50.0 – 74.9 | District Collector Briefing & Priority Allocation | 0.86 |
| **Critical** | 75.0 – 100.0 | Rapid Response Pre-positioning (< 24h) | 0.92 |

### Limitations & Edge Cases
1. **Low Citizen Ingress Districts (Silence Map):** Areas with low digital literacy or cellular blackspots may exhibit falsely low grievance velocity. To prevent systemic neglect, VisBharat couples the Stress Index with the **Silence Map Detector** (`visbharat/services/demand_analytics.py`), flagging silence as latent risk rather than infrastructure adequacy.
2. **Post-Disaster Anomalies:** Severe cyclonic storms or flash floods cause localized spikes that exceed standard historical bounds. In such events, deterministic Fast-Path Triage overrides model smoothing.

---

## 5. Drift Monitoring & Recalibration Protocol

- **Population Stability Index (PSI):** Evaluated weekly across incoming complaint distributions. If `PSI > 0.25` relative to the training baseline, a drift warning is emitted to the Auditor Dashboard.
- **Jensen-Shannon Divergence (JSD):** Monitored per category (Water, Power, Roads, Sanitation) across pilot corridor districts.
- **Quarterly Retraining Cadence:** Re-sync with MoPR LGD gazette updates and updated Jal Jeevan Mission fund allocation statistics.
