# Machine Learning Evidence Dossier: Demand Stress Forecasting

- **Model Version**: `v1.2-pilot-corridor` (Vertex AI Endpoint `projects/nexus-visbharat-prod/locations/asia-south1/endpoints/stress-forecast-v1`)
- **Generated**: 2026-09-21 09:01:17 UTC
- **Governance Label**: `Provisional Demand Stress Index (Baseline Screening)`
- **Sovereign Region**: `asia-south1` (Mumbai, India)

---

> [!WARNING]
> **Experimental Screening Model**: The diagnostics presented below are exploratory, proxy-based self-consistency checks, not an independent population holdout evaluation. Real-world forecasting remains experimental until independently validated against historical municipal ground truth.

---

## 1. Cryptographic Training Snapshot Lineage (SHA-256)

Every data asset used in baseline calibration and proxy forecasting has a verified SHA-256 digest:

| Asset Name | Relative Path | SHA-256 Digest | Size (Bytes) |
| :--- | :--- | :--- | :--- |
| **Census 2011 District Demographics** | `static/data/layer4_sources/census_2011_demographics.csv` | `d1327c14eb4b0760a9046d8e40e2c6cbbcae6013cb7422299a151b3a98828f03` | 11,204 |
| **NITI Aayog MPI 2023 Multidimensional Poverty** | `static/data/layer4_sources/niti_aayog_mpi_2023.csv` | `547de39e28fa67ee4d0edbc2051289400f5132a571151b8c20604b86b7c1ea4e` | 8,826 |
| **PM Gati Shakti Infrastructure Nodes** | `static/data/layer4_sources/pm_gati_shakti_infra_nodes.csv` | `61b91c7490d6b3e032b569fefc604d5c1fa2b5cf51dfefc390802a22569bfade` | 7,968 |
| **SDG India Index 2023 District Outlays** | `static/data/layer4_sources/sdg_india_index_2023.csv` | `5c20c5b3ae60b57b7a1b713e6e7c8896acf3c85de7078fc30dc09ddf4e9e0fdd` | 7,979 |
| **SECC Deprivation Survey 2011** | `static/data/layer4_sources/secc_deprivation_2011.csv` | `b2aeeff906a1faa1ae778f535ed2f25cfef048dfeb9471bc36ea0ae54add130b` | 9,793 |
| **Aspirational Districts Strategic List** | `static/data/layer4_sources/aspirational_districts_list.csv` | `009f02b39563f939eedbfca8b727cc4f25baedd91f4ac6e24496ca8595b71a12` | 9,356 |
| **NFHS-5 District Health Indicators** | `static/data/layer4_sources/nfhs5_health_indicators.csv` | `08207939dc839203950897304d2fa240052b3f4e7ce368942d7d4d49c913f3ac` | 9,826 |
| **MoPR LGD Official District Gazette** | `static/data/districts.csv` | `010663efc1d560ca08c7b12d788e948718c7e849f4df04b493f6184f529b7f17` | 7,139 |
| **State Infrastructure Project Pipeline** | `static/data/projects.csv` | `5947f0b3f429d73c2ff5dea7baaf88070907d0cbf77246b48c75bfa53711e94f` | 1,482 |
| **Vertex/XGBoost Model Binary (model.bst)** | `model.bst` | `bf082aac8c05297623d854e30ef1604f47a1285d00176d1de5baf35581dccbdc` | 230,137 |

---

## 2. Proxy Diagnostics — Not a Holdout Evaluation

The following values are self-consistency diagnostics over proxy labels derived from the same screening rule. They are **not** historical ground truth, a holdout evaluation, or evidence of Vertex endpoint performance.

| Metric | Measured Value | Standard Benchmark Threshold | Interpretation |
| :--- | :--- | :--- | :--- |
| **ROC-AUC** | **Not independently measured** | &ge; 0.85 | Requires independent time-split labels |
| **Precision / Recall / F1 / Accuracy** | **Proxy only** | N/A | Tautological when labels derive from the same screening score |
| **Brier Score (Proxy Diagnostic)** | **0.1806** | &le; 0.15 | Does not meet the stated calibration target; not an acceptance result |

### Confusion Matrix (N = 97 Districts)

```
                       Actual Normal/Low    Actual High/Critical
Predicted Low/Med             0                  0                 
Predicted High/Crit           0                  97                
```

---

## 3. Drift Monitoring & Recalibration Protocol

Real-time feature distribution is monitored using the **Population Stability Index (PSI)**:

$$\text{PSI} = \sum_{i=1}^{k} (A_i - E_i) \times \ln\left(\frac{A_i}{E_i}\right)$$

| Distribution Monitored | Empirical PSI | Threshold | Action Triggered |
| :--- | :--- | :--- | :--- |
| **Standard Ingress (Normal)** | **`0.0018`** | `< 0.10` | **Green**: Production model running nominal |
| **Extreme Seasonal Shift (Simulated)** | **`0.5875`** | `> 0.25` | **Red Alert**: Automatic fallback to LGD demographic baseline + retraining alert |

---

## 4. Model Rollback & Governance Policy

1. **Air-Gapped Tier 2 Deterministic Fallback**: If the Vertex live endpoint experiences timeout (>500ms) or PSI drift alert, the engine automatically rolls back to the deterministic local proxy (`vertex-automl-proxy-v1`), ensuring zero operational downtime.
2. **Audit Logging**: Every inference request records `model_name`, `model_confidence`, and input features into the tamper-evident audit ledger.
3. **No Direct Sanctioning**: As stipulated in the Model Card, predictions serve strictly as *Baseline Screening* for human executive officers; automated budgetary sanctions are strictly prohibited by software invariant.
