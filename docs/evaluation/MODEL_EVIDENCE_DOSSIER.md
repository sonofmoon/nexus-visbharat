# Machine Learning Evidence Dossier: Demand Stress Forecasting

- **Model Version**: `v1.2-pilot-corridor` (Vertex AI Endpoint `projects/nexus-visbharat-prod/locations/asia-south1/endpoints/stress-forecast-v1`)
- **Generated**: 2026-09-21 09:01:17 UTC
- **Governance Label**: `Provisional Demand Stress Index (Baseline Screening)`
- **Sovereign Region**: `asia-south1` (Mumbai, India)

---

> [!IMPORTANT]
> **System Architecture Demarcation**: Nexus VisBharat separates two distinct AI subsystems with transparent evaluation postures:
> 1. **Production Operational NLP & Triage (Gemini 3.6 Flash & Cloud Speech-to-Text)**: Evaluated on a 468-case multilingual benchmark achieving **0.9910 Macro-F1**, **95.09% Urgency Accuracy**, and **100% Emergency Recall (143/143 across 13 national languages)**.
> 2. **Exploratory Macro Spatial Forecasting (`model.bst`)**: A proxy diagnostic screening sandbox trained on open Census 2011/MPI indicators; explicitly not a validated population holdout.

---

## 0. Production NLP Triage: Benchmark Provenance & Safety Breakdown

The 468 challenge cases in [`docs/evaluation/review-pack-v3.json`](review-pack-v3.json) were curated to test multilingual classification, transliteration handling, and emergency safety triage across 13 national languages with strict 1:1:1 linguistic parity (11 emergency cases per language across all 10 civic categories, with Tamil-first priority):

| Language | Total Evaluated | Routine | Urgent | **Emergency Cases** | **Emergency Recall** | **Urgency Acc** | **Category Macro-F1** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tamil (`ta`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 97.2% | **1.0000** |
| **Telugu (`te`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 97.2% | **0.9689** |
| **Hindi (`hi`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9746** |
| **Bengali (`bn`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 91.7% | **1.0000** |
| **Marathi (`mr`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Kannada (`kn`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Malayalam (`ml`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9657** |
| **Gujarati (`gu`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Punjabi (`pa`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Odia (`or`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Assamese (`as`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Urdu (`ur`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9689** |
| **English (`en`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 100.0% | **1.0000** |
| **Overall Platform** | **468** | **143** | **182** | **143** | **143 / 143 (100.0%)** | **95.09%** | **0.9910** |

- **Emergency Scenarios Tested**: Gas leaks near residential wards, high-tension live wire collapses, culvert structural failures, open drainage overflow near schools, and water tanker contamination.
- **Provenance Disclosure**: Developer-curated to stress-test regional dialectal nuances and urgent boundary conditions. The current labels are provisional and await independent third-party municipal adjudication during the upcoming district pilot (calculating Cohen's Kappa $\kappa$ inter-annotator agreement).
- **Fallback Quality Comparison**: On the same challenge suite, local keyword fallback ([`baseline-quality.json`](baseline-quality.json)) achieved only **0.3764 Macro-F1**, demonstrating the critical value of Gemini Flash multilingual semantic inference.

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

### Confusion Matrix (N = 408 Districts)

```
                       Actual Normal/Low    Actual High/Critical
Predicted Low/Med             0                  0                 
Predicted High/Crit           0                  408                               
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
