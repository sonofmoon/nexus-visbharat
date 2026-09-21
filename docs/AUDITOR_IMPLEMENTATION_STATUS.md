# Auditor implementation status — 19 September 2026

The six-tab Auditor workspace is implemented at **[localhost](http://127.0.0.1:5000/dashboard?workspace=auditor)**. Reload any dashboard left open before this upgrade to load the new JavaScript. The implementation replaces fixture assertions with scoped records, review actions and explicit evidence requirements.

## Implementation against the plan

| Package | Implemented | Evidence still requiring an external party or deployment |
|---|---|---|
| P0-A: supported claims | Retired fixed causal results, static signed-tender claims, risk caps, frozen-money totals, audit precision and fake dispatch; legacy routes now use the same evidence rules | An observed causal effect requires a reviewed study and real outcome data |
| P0-B: contracts and access | Correct receipts and latest/superseded states; strict boolean validation; escaped UI text; safe CSV cells; positive page bounds; server-derived actors; protected mutations; no GET control seeding | Production identity, agency delegation and hosting policy remain deployment responsibilities |
| P0-C: scope and metrics | Shared Analyst project IDs; full channel/language denominators; explicit intake/event date semantics; per-view metadata; empty/unknown states; state-clearance configuration; snapshot cache scoped by user and data head | LGD crosswalk authentication and multi-agency federation review |
| P1-A: decision trace | Ticket/native-text/translation evidence, source tables, decision/scenario/review links, project evidence, readiness checklist, nodes/edges and redacted portable packs | Loaded reference files remain unverified until their publisher/release/terms/boundaries are checked |
| P1-B: integrity and cases | Transactional hash-linked writer; explicit legacy boundary; full protected-segment verification; server search/cursors; typed auth detections; persistent case assignment, investigation, evidence requests, independent closure and reopening | Independently retained chain checkpoints; broader infrastructure/security detectors |
| P1-C: consent and delivery | Purpose/basis receipts, declined/withdrawn/missing states, local restrictions, rights cases, documented milestone/payment/site evidence and comparable-period discrepancies; explicit withdrawals excluded from subsequent Analyst candidates | External-system erasure/retention completion and an authorized financial-system connector; no funds are frozen by this app |
| P1-D: outcomes and evaluation | Independently reviewed before/after service observations; synthetic labels preserved; no causal inference without a valid study; durable bounded evaluation jobs; measured category/emergency/language metrics, optional speech/translation/location/duplicate labels and baseline distribution comparison | Representative human-reviewed language/audio labels; field observations; causal-method review |
| P2-A: performance and DPG | Active-tab loading, request cancellation/deduplication, cached snapshots/counts, indexes, bounded exports/jobs, export schema/validation, restart recovery, demo setup and verification scripts | PostgreSQL/cloud deployment, cold multi-user workload, costs/queue observability under production load and a full accessibility assessment |

These external evidence gates are intentionally visible. Implemented software, synthetic records, file digests and simulated reviewer roles cannot independently certify them.

## Verification

- The final full regression pass completed **201 tests successfully** in 133.155 seconds. The report is recorded in [regression-final.log](../scratch/auditor-implementation/regression-final.log). The expected fallback warnings in that run exercise unavailable cloud/AI handling; the unittest result is `OK`.
- Tests cover authorization and server-side state scope, full denominators, zero/missing values, independent review, optimistic concurrency, case persistence, matching milestone periods, noncausal synthetic outcomes, current consent state, withdrawal effects, concurrent chain appends, content/tail tampering, legacy unsigned history, frozen exports, CSV safety, detection taxonomy and portable-pack checksum validation.
- All six tabs passed desktop and 390px layout checks, with no JavaScript page errors or document overflow. Checks cover keyboard tab navigation, dark theme, readable evaluation tables, project/state scope, synthetic outcomes, a 50-percentage-point demo discrepancy, JSON/CSV exports, legacy integrity disclosure, and clearing stale metrics on an intercepted failure. The synthetic Karur case passed investigation, evidence request, independent closure and reopening through browser forms. Switching Admin/Auditor identities fetched fresh authorized snapshots. [Browser evidence](../scratch/auditor-implementation/browser/results.json).
- The ordinary Auditor data endpoints contain no model/provider invocation. The browser no longer requests the former live-model telemetry endpoint.
- Source/schema/JavaScript checks and final regression evidence are retained in [the implementation folder](../scratch/auditor-implementation/).

## Measured performance

The isolated benchmark used **100,000 synthetic requests, 1,000,000 unsigned historical audit events and 20 concurrent authenticated readers**. The historical rows were deliberately unverified; separate tests check protected-event integrity.

| Workload | Samples | p50 | p95 | p99 | Failures |
|---|---:|---:|---:|---:|---:|
| Warm snapshot, concurrency 20 | 100 | 53.94 ms | 79.90 ms | 84.65 ms | 0 |
| Paged event history, concurrency 20 | 100 | 130.06 ms | 262.13 ms | 277.64 ms | 0 |

The first uncached snapshot took **672.51 ms in one observation**. That is not a cold-load percentile. Browser walkthroughs reached useful content in about **1.7–2.6 seconds**, with **2,601.4 ms in the final run**; the proposed two-second browser target was not consistently met. These are local measurements, with the load benchmark using SQLite/Flask test clients. It excludes network/cloud latency and does not establish national-scale capacity. Cold snapshots at 20 distinct simultaneous scopes and production PostgreSQL/Cloud Run behavior remain unmeasured.

The same paged-history workload initially measured about 1,007 ms p95. Reusing the filtered count for an immutable declared head reduced it to 262 ms. Payloads were about 4.6 KB for the overview and 20.4 KB for 100 history rows. Full report: [auditor-load.json](evaluation/auditor-load.json).

## Demonstration records and AI evidence

Three existing synthetic project groups now have explicitly synthetic review evidence: **Karur, Tirupati and Nalgonda**. Setup added ten evidence records, four cases and four receipt events, including a withdrawal. It did not replace the 12,500 citizen demands, approve a policy, publish data, contact an audit team or issue a financial instruction. Re-running setup reuses its records. [Demo identifiers](evaluation/auditor-demo.json).

The Karur example has a milestone claim of 80% and a site observation of 30% for the same reporting period; the app calculates the 50-percentage-point discrepancy. Its water-service observations change from 2 to 6 hours/day, explicitly labelled **illustrative**. Tirupati and Nalgonda have a baseline but no paired follow-up, so outcomes remain insufficient. Reviewer actions in this seeded demonstration are role simulations, not real independent verification.

The Auditor also recalculates the **27 previously recorded model predictions** from the Analyst evaluation artifact. Category macro-F1 is **0.88** and emergency recall is **1.0 on nine provisional emergency cases**. These labels still need independent review; speech error, translation fidelity, location accuracy and duplicate-pair quality remain unavailable without their respective labels. No new model calls were made for this import. [Measured result](evaluation/auditor-quality.json).

## Try the complete review flow

1. Open Auditor, search Karur, and select the Water Supply project in Ward 13. Open its citizen evidence, reference indicators and attached records.
2. Open Delivery & finance to compare the same milestone and reporting period. Inspect the existing discrepancy case; start investigation or request evidence with a reason.
3. Review the synthetic outcome pair and compare it with Tirupati's incomplete follow-up. Neither receives an unsupported causal-impact claim.
4. Open Consent & rights, select the Tirupati ticket from the demo manifest and inspect its grant/withdrawal history and rights case. Explicit local refusal/withdrawal excludes the ticket from subsequent planning candidates while preserving the audit record.
5. Use Audit trail to search that ticket, case or project across the complete history. Verify the protected segment and inspect the legacy-unverified boundary. Export the displayed snapshot as JSON or CSV.
6. Open AI quality & inclusion under Evidence & decisions. Inspect the recorded 27-case evaluation, or upload a separate labelled file to queue an evaluation. The small downloadable example is explicitly provisional.
7. A different authorized reviewer must close a finding or review evidence submitted by another user. In the loopback demo, switch to Admin and choose **Open audit review tools as Admin**. This uses the Admin identity rather than claiming an Auditor approved their own evidence.

The Analyst workflow supplies proposals and administrative decisions. The Auditor does not create a fictitious sanction when a project has no decision record; the readiness checklist shows the missing decision until it is actually recorded through the authorized Analyst/Admin workflow.

## Operational details

See [deployment/API instructions](AUDITOR_DEPLOYMENT_AND_API.md) for migration, limits, jobs, schemas and reproducible checks. The source and SQLite snapshots taken before implementation are `scratch/auditor-implementation/before.zip` and `before.db`. They are local backups, not public evidence packs. Preserve subsequent user changes before any restoration.
