# NVB jury demonstration — Build with AI: Code for Communities (2nd Edition)

Open **http://localhost:5000/demo** for the interactive walkthrough. The existing home page and policy dashboard link to it.

The Analyst workspace now has six tabs. Open `/dashboard`, select Analyst, and follow Demand & Inclusion → Evidence & Gaps → Project Priorities → Budget Scenarios → Delivery & Responsiveness → Outcomes & Evaluation. The [implementation status](ANALYST_IMPLEMENTATION_STATUS.md) records acceptance evidence and external validation still required.

For the investment segment, search the complete proposal list for a district, inspect its source tickets and cost basis, then change budget or weights. Default allocation limits selection to two projects per district. Show the actual selected/removed IDs and cost sensitivity, download the cited brief or portable JSON, and save a draft with a rationale. In Admin → Project review & approval, an officer records engineering/catchment evidence before approval. Approval consumes the known project envelope and prevents duplicate commitment. Use an isolated rehearsal database for mutation demonstrations if the prepared corpus must remain unchanged.

For the outcome segment, supply the actual synthetic case's delivery date and issue cluster, use equal completed windows, and state that a reporting decrease is descriptive. A future date displays incomplete follow-up and no improvement estimate. For reuse, expand the evaluation panel: the 27-case holdout evaluation (docs/evaluation/quality.json) achieved category macro-F1 1.00 and urgency accuracy 96.3% (emergency recall 9/9), with independent human review explicitly pending. Do not describe the loaded reference files as authenticated official data.

## What changed

The audit found 12,500 rows in `nexus-visbharat.visbharat_analytics.citizen_requests_fused`. All used `NVB-DEMO-…` IDs. The 7,500 Tamil/Telugu-labelled rows contained English text. SQLite contained another 3,000 old `COMP-…` fixtures, producing an inconsistent total of 15,500.

The published replacement has **12,500 fictional, AI-assisted requests** in BigQuery, SQLite and the reference CSV. Every ticket uses `NVB-YYYYMMDDXXXX`: the date matches submission in UTC; `XXXX` is four uppercase hexadecimal characters, collision-checked across the package. Live intake already uses this format.

The package includes 52,921 dated lifecycle events, routing, SLA deadlines and explicit synthetic provenance. The data spans 22 May–19 September 2026. Do not describe the sample volumes, project progress or outcomes as real citizen reports or demonstrated government impact.

## Distribution

These are illustrative design choices, not estimates of actual complaint prevalence.

| State | Demands | Tamil | Telugu | English |
|---|---:|---:|---:|---:|
| Tamil Nadu | 6,250 | 5,300 | 0 | 950 |
| Andhra Pradesh | 3,750 | 0 | 3,208 | 542 |
| Telangana | 2,500 | 0 | 2,176 | 324 |
| Total | 12,500 | 5,300 | 5,384 | 1,816 |

All 97 configured pilot districts are represented. State quotas retain the original 50/30/20 split. Within states, district allocations use square-root population, a minimum of 30 records per district, and an illustrative access adjustment. Category weights use bundled service-coverage data and urban/coastal/hill profiles. The bundled reference indicators have not been independently validated as current official statistics.

| Urgency | Demands | Share |
|---|---:|---:|
| Routine | 8,553 | 68.4% |
| Urgent | 3,421 | 27.4% |
| Emergency | 526 | 4.2% |

Urgency controls the actual scenario text. A routine request for an accessible bus stop is not randomly relabelled as an emergency. Emergency descriptions concern immediate safety or critical-service failures.

| Category | Demands |
|---|---:|
| Water Supply | 2,828 |
| Road | 2,180 |
| Sanitation | 1,985 |
| Electricity | 1,235 |
| Health | 1,115 |
| Education | 898 |
| Transport | 775 |
| Digital Connectivity | 664 |
| Housing | 593 |
| Other | 227 |

Channels include Voice IVR, missed-call IVR, WhatsApp, web, keyword SMS, Telegram and Email/Open API. These labels describe simulated intake routes in the fixture; they are not evidence that actual calls or messages occurred.

## Suggested 8–10 minute presentation

1. **Frame the problem (1 minute).** Separate portals collect many local requests, but policymakers need a shared view of unmet need, infrastructure gaps, investment options and outcomes. Show the six-step flow on `/demo`.
2. **Demonstrate multilingual intake (1–2 minutes).** Open a Telugu/Tamil sample and its English translation. Submit one new request through the app if demonstrating live processing. Inspect actual AI/provider telemetry and disclose any fallback. Prepared translations and cluster memberships are labelled fixtures, not live-model evaluation results.
3. **Aggregate and prioritize (2 minutes).** Compare Karur water needs, Chennai drainage and Alluri health access. Explain why volume alone should not dominate population, deprivation and coverage gaps. Show filters, hotspot tools and project-priority views. Funding programmes on `/demo` are possible routes subject to eligibility and engineering review, not approvals.
4. **Show accountable delivery (2 minutes).** Open a sample directly in the execution table. Show its department, SLA, status and remarks. Use an authorized officer update, then reopen the public tracker. The update also attempts to synchronize BigQuery; a sync failure is reported while preserving the local update.
5. **Handle an emergency (1 minute).** Show the Visakhapatnam electrical-hazard ticket and overdue/escalated filter. Immediate safety response takes priority; a resilient feeder upgrade is a separate capital-planning question.
6. **Explain impact and scope (1–2 minutes).** Nalgonda's synthetic cohort has 32 reports before and 4 after an example delivery date, in equal 28-day windows. This is an illustration, not causal proof. Explain control groups, parallel trends, reporting access and independent field verification. The deployed demo covers three Indian states; Expansion to additional Indian communities requires local data, languages, boundaries, programmes and governance rules.

## Working sample tickets

| Example | Ticket | Initial fixture stage |
|---|---|---|
| Tirupati transport intake | `NVB-202609180564` | Pending |
| Hyderabad digital access | `NVB-20260917A388` | Acknowledged |
| Karur water convergence | `NVB-2026091007CD` | Prioritized |
| Alluri primary health access | `NVB-20260830F820` | Capex Allocated |
| Chennai drainage execution | `NVB-202608271A54` | In Progress |
| Visakhapatnam electrical safety | `NVB-20260918B8FF` | Escalated |
| Nalgonda water follow-up | `NVB-20260727A75F` | Closed |

Use the live tracker for the current status after any demonstration edits. The walkthrough's initial labels describe the prepared fixture.

## Reproducibility and recovery

- `scripts/build_jury_demo.py` creates a package without changing active stores. For the published package use `--as-of 2026-09-19T02:42:55Z --seed 20260919` and a fresh output directory.
- `scripts/publish_jury_demo.py --step local` prepares an isolated database with a backup; `--step cloud` creates a backup and staging table; `--step apply` checks that the original requests have not changed before publication. Stop the server before replacing SQLite on Windows. Publication preserves user accounts and audit history, retires legacy fixture references, and archives pre-existing orphaned cluster memberships in the backup.
- Original local database: `scratch/jury-demo-v2/backups/database-before.db`.
- Original BigQuery table: `nexus-visbharat.visbharat_analytics.citizen_requests_fused_backup_20260919_024629`.
- Original CSV: `scratch/jury-demo-v2/backups/complaints-before.csv`.
- Publication details: `scratch/jury-demo-v2/published.json`; initial audit: `scratch/demo-demand-audit/audit.json`.
- Public manifest: `static/data/demo_showcase.json`; complete distribution report: `docs/demo_dataset_report.json`.

Restoration must restore the matching database, BigQuery table and CSV together, then restart the server. Preserve any new requests or demonstration updates made after publication before restoring a backup.

Validation covers ticket/date uniqueness, district and language consistency, severity/text agreement, lifecycle chronology, synthetic provenance, sample trackers, CSV reseeding, analytics status-sync behavior and existing request filters.
