# Machine Learning Evidence Dossier: Demand Stress Forecasting

- **Model Version**: `v1.2-pilot-corridor` (Vertex AI Endpoint `projects/nexus-visbharat-prod/locations/asia-south1/endpoints/stress-forecast-v1`)
- **Generated**: 2026-09-21 09:01:17 UTC
- **Governance Label**: `Provisional Demand Stress Index (Baseline Screening)`
- **Sovereign Region**: `asia-south1` (Mumbai, India)

---

> [!IMPORTANT]
> **System Architecture Demarcation**: Nexus VisBharat separates two distinct AI subsystems with transparent evaluation postures:
> 1. **Text Classification & Triage**: The v3 dataset contains 468 developer-curated cases across 13 languages. The [current evaluation report](benchmark-v3-quality.json) records a **26-case live-mode run**, reporting **1.0000 category Macro-F1**, **65.38% urgency accuracy** and **13/13 emergency recall**. The sample covers Water Supply only. Per-case provider, served-model and fallback evidence is not retained in the report, so successful Google execution for every case remains unverified. This text evaluation does not measure Cloud Speech-to-Text accuracy.
> 2. **Exploratory Macro Spatial Forecasting (`model.bst`)**: A proxy diagnostic screening sandbox trained on open Census 2011/MPI indicators; explicitly not a validated population holdout.

---

## 0. Text Triage: Benchmark Provenance & Recorded Results

The [v3 challenge dataset](review-pack-v3.json) contains 468 developer-curated text cases across 13 languages and 10 civic categories, with Tamil-first priority. Each language has 36 cases: 11 Routine, 14 Urgent and 11 Emergency. These are dataset counts, not evidence that all 468 cases were evaluated by a live provider.

The following results come from [the recorded 27 September 2026 run](benchmark-v3-quality.json) using the evaluator's `--live` option. Its two-case-per-language sampling selects one Urgent and one Emergency Water Supply request per language, with no Routine cases. The reported 100% category accuracy and 1.0000 Macro-F1 therefore do not establish classification quality across all 10 categories. Live mode can fall back to the local classifier; the report's `live_verified` flag reflects the selected mode rather than per-case execution verification.

| Language | Evaluated Cases | Emergency Recall | Urgency Accuracy | Category Macro-F1 |
| :--- | :---: | :---: | :---: | :---: |
| Tamil (`ta`) | 2 | 1/1 | 50.00% | 1.0000 |
| Telugu (`te`) | 2 | 1/1 | 50.00% | 1.0000 |
| Hindi (`hi`) | 2 | 1/1 | 50.00% | 1.0000 |
| Bengali (`bn`) | 2 | 1/1 | 50.00% | 1.0000 |
| Marathi (`mr`) | 2 | 1/1 | 50.00% | 1.0000 |
| Kannada (`kn`) | 2 | 1/1 | 50.00% | 1.0000 |
| Malayalam (`ml`) | 2 | 1/1 | 100.00% | 1.0000 |
| Gujarati (`gu`) | 2 | 1/1 | 50.00% | 1.0000 |
| Punjabi (`pa`) | 2 | 1/1 | 50.00% | 1.0000 |
| Odia (`or`) | 2 | 1/1 | 50.00% | 1.0000 |
| Assamese (`as`) | 2 | 1/1 | 100.00% | 1.0000 |
| Urdu (`ur`) | 2 | 1/1 | 100.00% | 1.0000 |
| English (`en`) | 2 | 1/1 | 100.00% | 1.0000 |
| **Recorded subset overall** | **26** | **13/13** | **65.38%** | **1.0000** |

- **Emergency Scenarios in the Full Challenge Dataset**: Gas leaks near residential wards, high-tension live wire collapses, culvert structural failures, open drainage overflow near schools, and water tanker contamination. The current run covers only the Water Supply subset.
- **Provenance Disclosure**: Developer-curated to stress-test regional dialectal nuances and urgent boundary conditions. The current labels are provisional and await independent third-party municipal adjudication during the upcoming district pilot (calculating Cohen's Kappa $\kappa$ inter-annotator agreement).
- **Baseline v2 versus v3**: [`baseline-quality.json`](baseline-quality.json) records the local classifier on 108 v2 cases across English, Tamil and Telugu, with **0.3764 category Macro-F1**. The current v3 report evaluates a different 26-case, 13-language Water Supply subset. These results are not directly comparable and do not establish a measured Gemini improvement over the baseline. A valid comparison requires the same cases and labels, with provider and fallback provenance recorded for each prediction.

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

## 3. Illustrative Drift Diagnostics & Proposed Monitoring

The evidence generator calculates **Population Stability Index (PSI)** from fixed example distributions for stable and shifted risk-band proportions. The values below are illustrative diagnostics, not measurements from a live production stream. Continuous monitoring, alert delivery and automatic recalibration have not been established by this dossier.

$$\text{PSI} = \sum_{i=1}^{k} (A_i - E_i) \times \ln\left(\frac{A_i}{E_i}\right)$$

| Example Distribution | Reported Illustrative PSI | Proposed Threshold | Proposed Interpretation / Response |
| :--- | :--- | :--- | :--- |
| **Stable risk-band proportions (simulated)** | **`0.0018`** | `< 0.10` | Small distribution shift in the example; not a production health result |
| **Shifted risk-band proportions (simulated)** | **`0.5875`** | `> 0.25` | Investigate drift and consider fallback or retraining after review; no triggered operational action is evidenced |

---

## 4. Model Rollback & Governance Policy

1. **Local Forecasting Fallback**: The forecasting service can return the local screening proxy (`vertex-automl-proxy-v1`) when the Vertex client is absent or its prediction call raises an exception. This is a fallback computation, not a deployment rollback. A fixed 500 ms failover, PSI-triggered switching and zero-downtime operation have not been demonstrated. Availability and recovery timing require recorded deployment and failure-recovery tests; proxy outputs remain provisional screening results for human review.
2. **Audit Logging**: Every inference request records `model_name`, `model_confidence`, and input features into the tamper-evident audit ledger.
3. **No Direct Sanctioning**: As stipulated in the Model Card, predictions serve strictly as *Baseline Screening* for human executive officers; automated budgetary sanctions are strictly prohibited by software invariant.
