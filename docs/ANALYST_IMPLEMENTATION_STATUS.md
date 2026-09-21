# Analyst implementation status — 19 September 2026

The six-tab Analyst workflow is implemented and running locally at `http://127.0.0.1:5000/dashboard`. Engineering changes are complete for the local decision workflow. Independent source authentication, human language evaluation and deployment-scale evidence remain open acceptance gates; this document does not label them complete.

## Plan-to-evidence mapping

| Work package | Implemented and verified locally | Remaining acceptance evidence |
|---|---|---|
| P0-A: truthful labels and edge cases | Synthetic/source/cost disclosures; unavailable ROI and hidden demand; no future-window improvement; retired Analyst market/RCT fixtures; zero-budget portfolio has no planned benefit | Continued review of separate legacy Auditor/research screens is outside this Analyst implementation |
| P0-B: shared scope and full aggregation | All ten categories; state/district/ward/urgency/language/channel/date scope; full SQL urgency/channel totals; zero-day calendar trend; one consistent read transaction and atomic browser update | Wider PostgreSQL concurrency/deployment verification; default-weight map is explicitly labelled separately from custom scenarios |
| P0-C: canonical scoring and real scenarios | Versioned demand/equity/gap score; reproducible weights/contributions; actual rank/selection deltas; zero budget; cost sensitivity; operating/capacity/district/equity constraints; known commitments and reviewed estimates; duplicate/over-budget approval prevention | Engineering uncertainty ranges, authenticated investment ledger and scheme eligibility must be supplied by responsible agencies |
| P1-A: traceable projects/evidence | Every proposal links source tickets, native/translated text, cluster assignments, ward/service catchment, alternatives, agency, source records and cost basis; full-corpus search/pagination; draft rationale, engineering review, human approval; cited brief and portable JSON | Independent category/cluster review, field catchment surveys, official source authentication, LGD crosswalk and specific scheme-budget evidence |
| P1-B: responsiveness/outcomes | Event-derived acknowledgment SLA, eligible denominators, median/p90, open aging, closure/reopen/feedback; linked decisions; explicit delivery date, project/cluster filters and completed equal windows | Actual expenditure, physical outcome baselines and delivery evidence; reviewed methodology/comparators before causal inference |
| P2-A: AI/scale/DPG package | Live 27-case provisional language evaluation; fallback baseline; 12,500/100,000-row local load benchmark; role/replay tests; versioned schema, actual API export and validation round-trip; installation, privacy/correction and federation documentation | Human-reviewed labels, noisy audio/translation/location/duplicate-pair evaluation; publisher/license authentication; Docker/PostgreSQL/cloud deployment, queue-age and billing measurements |

The pending items require independent reviewers, authenticated agency data, field evidence or a deployment environment. Prepared documentation and evaluation tooling support that work; generated fixtures cannot satisfy those gates.

## Verification record

- **176 regression tests passed**, covering the existing application, channels, queue, policy workflow, request submission, browser intake and new Analyst behavior. Final full-suite log: `scratch/analyst-implementation/regression-final.log`. The suite uses isolated databases and simulated/mocked providers.
- **31 targeted tests passed** after the final snapshot transaction changes: Analyst, request queue and jury dataset tests.
- Six Analyst tabs passed desktop and 390px layout checks with no JavaScript errors. Checked full totals, zero budget, actual weights, district capacity, search across all projects, pagination past 200, cited brief, scoped portable JSON, project evidence, future-window rejection and Admin review screen. See `scratch/analyst-implementation/browser/results.json` and screenshots in the same folder.
- Existing execution filters passed: search, combined/global filters, active/overdue status, clear, refresh, pagination, row action, mobile layout and dark theme.
- Jury walkthrough passed seven trackers, state filtering, synthetic disclosure, responsive layout and execution/public sample links.
- BigQuery/SQLite parity passed: **12,500 records, matching IDs and content, correct ticket/date formats, all explicitly synthetic**, and seven working trackers. Report: `scratch/jury-demo-v2/live-verification.json`. This work did not publish, reset or replace the demo dataset.
- Portable evidence API export and JSON Schema validation passed; round-trip preserves the record without persisting it. Invalid imports, unauthenticated reads and Auditor mutations are rejected. See `docs/evaluation/interoperability.json`.
- Python compilation and JavaScript syntax checks passed. Docker is not installed on this host, so container build/start is unverified.
- Two deployment checks passed: the workspace opens without a Maps key, and non-demo mode never embeds server role tokens. The loopback demo intentionally supports role switching; production identity/hosting configuration still requires deployment review.

Latest load results and exact timing/environment are in [`evaluation/load.json`](evaluation/load.json). Results cover SQLite test-client requests, not network/AI/Cloud Run throughput. Twenty requests per size/concurrency is a small sample; no national-scale claim follows from it.

| Rows | Concurrency | p50 ms | p95 ms | p99 ms | Requests/second |
|---|---:|---:|---:|---:|---:|
| 12,500 | 1 | 357.84 | 605.53 | 605.78 | 2.28 |
| 12,500 | 4 | 1,294.33 | 1,708.14 | 1,725.39 | 2.92 |
| 100,000 | 1 | 1,310.31 | 1,570.13 | 1,896.94 | 0.72 |
| 100,000 | 4 | 2,189.41 | 3,130.45 | 3,242.57 | 1.69 |

All four runs had zero failures and reconciled scoped channel totals. A SHA-256 manifest of the reports and implementation files is saved in `evaluation/evidence-manifest.json` for review.

## Language evidence

The live configured provider reported model `gemini-3.6-flash`; 27 provisional cases produced category macro-F1 **0.88**, urgency accuracy **0.9259**, emergency recall **1.0 on nine proposed emergency cases**, and zero fallbacks. Median end-to-end evaluation latency was **11,553.67 ms**, including translation for native-language cases.

The fallback baseline produced category macro-F1 **0.3871**, urgency accuracy **0.4074** and emergency recall **0.2222**. It is not suitable evidence of equivalent live-provider quality. Fallback confidence is no longer randomly presented as high confidence; fallback output is explicitly marked for human review. The strict live-demo path still refuses unsupported fallback success.

These are small authored samples, separate from the seeded demonstration templates, with provisional labels. Tamil/Telugu independent review remains pending. Follow [`evaluation/REVIEW_GUIDE.md`](evaluation/REVIEW_GUIDE.md); do not tune on this set and later call it an unseen holdout.

## Implementation locations

- `visbharat/services/analyst_workbench.py`: scope, scoring, proposals, constraints, commitments, evidence and event/outcome metrics.
- `visbharat/blueprints/analyst.py`: authenticated v2 API, drafts, engineering review, portable evidence and readiness.
- `templates/analyst_workbench.html`, `static/js/analyst-workbench.js`, `static/css/analyst-workbench.css`: six tabs, shared snapshot, accessible tab navigation and Admin review integration.
- `visbharat/services/analyst_compat.py`, legacy API adapters and retired fixture services: canonical overview/legacy Analyst behavior.
- `tests/test_analyst_workbench.py`, `scripts/check_analyst_workbench_ui.py`, `scripts/verify_analyst_exchange.py`, `scripts/benchmark_analyst.py`: repeatable validation.

Deployment details and exact API parameters are in [`ANALYST_DEPLOYMENT_AND_API.md`](ANALYST_DEPLOYMENT_AND_API.md). The pre-implementation source backup is `scratch/analyst-implementation/before.zip`. Database and cloud backups from the earlier dataset preparation are documented in [`JURY_DEMO.md`](JURY_DEMO.md); restore matching stores together only after preserving subsequent user changes.
