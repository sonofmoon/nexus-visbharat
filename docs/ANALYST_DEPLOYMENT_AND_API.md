# Deploying and integrating the Analyst workspace

The workspace uses one scoped operational database for counts, project candidates and delivery evidence. BigQuery remains an analytics replica; its availability is not represented as a successful AI invocation. The browser is at `/dashboard` and the synthetic walkthrough at `/demo`.

## Local deployment

Use Python 3.11 for the pinned NumPy/Pandas versions in `requirements.txt`. Install with `python -m pip install -r requirements.txt`. Existing newer-Python environments can run the application if they already provide compatible dependencies; the pinned install is targeted at Python 3.11.

Set a separate `DATABASE_PATH`, keep `DATABASE_URL` empty for SQLite, and set `NVB_DISABLE_EXTERNAL_SERVICES=1` and `JURY_REQUIRE_LIVE_MODELS=false` for an offline demonstration. Configure private `ADMIN_API_TOKEN`, `ANALYST_API_TOKEN` and `AUDITOR_API_TOKEN` values. Start with `python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload`. Explicit environment settings take precedence over `.env`. The initializer installs schema, query indexes and the bundled synthetic CSV; CSV import does not reconstruct the full prepared lifecycle/cluster history. For operational deployment replace that demo import with an approved data-import process before exposing the service.

For a local container demo, set `NVB_ADMIN_TOKEN`, `NVB_ANALYST_TOKEN`, `NVB_AUDITOR_TOKEN` in the shell, then run `docker compose -f compose.demo.yaml up --build`. The bind address is loopback and a named volume holds SQLite. The image excludes service-account keys, databases, local scratch artifacts and environment files. Container startup is supplied as a reproducible path; it has not been executed in this Windows session. The existing Dockerfile uses Python 3.11 and Gunicorn.

For multiple application replicas use the existing PostgreSQL adapter and a managed secret provider. Load results in `docs/evaluation/load.json` apply only to their declared SQLite test environment. They do not establish Cloud Run capacity or a cloud cost per request. Measure ingress, workers, BigQuery synchronization and model providers separately before scaling. A production deployment also needs private authentication, backups, monitoring and a policy for data retention.

The loopback demo enables `DEMO_MODE=true` and deliberately bootstraps role tokens into the page for the role selector. Keep that deployment private. With `DEMO_MODE=false`, server role tokens are not embedded; users must supply their authorized token through the existing token input or a separately deployed identity gateway. A Google Maps key is optional for opening the workspace: without it the map is unavailable while evidence and tables remain usable. Chart/font assets currently use external CDNs, so a fully disconnected deployment also needs those assets bundled.

## Analyst API contract

All `/api/v2/analyst/*` routes require a bearer token with an appropriate server-validated role. Public overview routes expose aggregates without request text. The project detail endpoint exposes request text only to authorized Analyst/Admin/Auditor roles. Scenario reads do not alter active scoring profiles.

| Endpoint | Method | Role | Purpose |
|---|---|---|---|
| `/api/v2/analyst/snapshot` | GET or POST | Analyst, Admin, Auditor | Shared counts, inclusion screening, project rankings, allocation and cost sensitivity |
| `/api/v2/analyst/evidence` | GET | Analyst, Admin, Auditor | Source loading status, actual file hashes, sample records and unverified provenance |
| `/api/v2/analyst/projects/{project_id}` | GET | Analyst, Admin, Auditor | Source ticket IDs, native/translated text, clusters, lifecycle and linked decisions |
| `/api/v2/analyst/projects/{project_id}/export` | GET | Analyst, Admin, Auditor | Portable JSON record using the supplied scenario weights and scope |
| `/api/v2/analyst/exchange/validate` | POST | Analyst, Admin, Auditor | Validate an imported document; return it without persisting or approving it |
| `/api/v2/analyst/projects/{project_id}/draft` | POST | Analyst, Admin | Save a selected scenario project with rationale; replay returns the same draft |
| `/api/v2/analyst/decisions/{decision_id}/review` | POST | Admin | Record engineering cost, surveyed beneficiary count and evidence reference |
| `/api/v1/policy/decisions/{decision_id}/approve` | POST | Admin | Existing approval workflow; new project drafts require engineering review |
| `/api/v2/analyst/delivery` | GET | Analyst, Admin, Auditor | Event-derived SLA, aging, feedback and decisions |
| `/api/v2/analyst/outcomes` | GET | Analyst, Admin, Auditor | Equal before/after windows, completion status and observed report change |
| `/api/v2/analyst/brief` | POST | Analyst, Admin, Auditor | Read-only structured brief with claim-level source references |
| `/api/v2/analyst/readiness` | GET | Analyst, Admin, Auditor | Measured evaluation reports and explicit limitations |

Scope fields: `state`, `district`, `ward`, `category`, `urgency`, `language`, `channel`, `date_from`, `date_to`. Dates are inclusive UTC calendar dates. Unknown geography returns no matching citizen records, not a national fallback. Reference source panels use state/district geography; source files do not change with the intake period. Outcome windows replace intake date filters explicitly.

Scenario fields: `budget_lakh` (0–100,000,000), `capacity` (0–200), `max_per_district` (1–200), `operating_budget_lakh`, `equity_share` (0–1), `cost_case` (`low`, `mid`, `high`) and `weights` (`demand`, `equity`, `gap`, each 0–1). Weights normalize to sum one; unavailable indicators are disclosed and remaining weights renormalize. All-zero weights are rejected. Results include scenario ID, baseline, actual deltas, selected IDs, cost sensitivity and quality labels. `limit`/`offset` affect the preview only, not the funded set.

Default budget policy: score-order greedy selection, ten-project capacity, two projects per district, and a ₹1,000 lakh annual operating ceiling. An optional high-deprivation reservation is attempted first; unused reservation is released and any shortfall is reported. This is a transparent selection rule, not a claim of mathematical optimality. Proposed capital cost ranges and 3% annual maintenance assumptions are illustrative. Engineering review can replace the estimate before approval.

Draft requests also require `notes`. Review requests require `engineering_cost_lakh`, `beneficiary_count` and `evidence_reference`. A review records a responsible human assertion; it is not automatic document authentication. Actual spending, physical completion and independently measured beneficiaries must not be inferred from approval.

Known versioned approved/funded projects consume capital, project capacity, district capacity and assumed operating costs across all intake dates/languages/channels in the selected planning geography/service. Approval serializes policy writes and checks the saved envelope against current commitments and reviewed costs. Duplicate project approval is rejected. Legacy district-only decisions cannot be reconciled automatically; no treasury-wide available balance is claimed. Reviewed estimates replace illustrative costs; they have no invented uncertainty range.

`search`, `limit` and `offset` browse every ranked proposal without changing the scenario or overview's top projects. Snapshot responses use one database read transaction. The overview priority map explicitly uses default weights; custom-weight portfolios appear in the scenario and its project cards.

For correction/retention, an authorized officer should first locate a ticket and its linked clusters, decisions, attachments and export copies. Use the existing lifecycle/feedback workflow for status corrections and record the reason. Text/identity correction or erasure requires a steward to update the operational record and its BigQuery replica, handle attachments and downstream exports, and retain only the permitted audit metadata. Back up before migration and verify both stores afterward. This is a documented steward procedure, not a newly implemented automatic erasure service or a legal compliance assertion.

Outcome fields: `delivery_at`, `days` (7–365), optional `project_id` and `cluster_id`. The supplied delivery date must be checked against delivery evidence. Incomplete future follow-up returns no percentage improvement. No causal inference or confidence interval is asserted by this descriptive endpoint.

## Portable evidence and federation

`docs/release/analyst-decision.schema.json` defines a portable project evidence record. Run `python scripts/verify_analyst_exchange.py` to validate a fixture and lossless JSON round-trip. Existing citizen federation schemas and connectors remain in `docs/release/india_demand_federation*`. Request IDs preserve their originating system identifiers and internal standard IDs.

Project geography includes stable `NVB-local-v1` state/district codes. These are explicitly not LGD codes. Importing another state's data requires a checked name/code crosswalk, administrative boundary year, source provenance and service/department mappings. Preserve original identifiers; do not guess LGD codes from district names.

## Evaluation and evidence limitations

Run `python scripts/evaluate_analyst_quality.py --provider baseline` for the local fallback baseline. Use `--provider google` for configured live translation/classification calls; no citizen requests are submitted. Reports explicitly identify fallbacks and provisional labels. The 27-case review pack is separate from the synthetic demand generator, but still needs independent review. Speech quality, dialects/noisy audio, translation fidelity, location extraction and cluster-pair labels require independent annotated samples before they can support validated claims. Annotate reviewer/date/notes in the review pack and preserve a holdout before tuning.

Run `python scripts/benchmark_analyst.py --rows 100000 --requests 20` for an isolated SQLite copy at the current and larger synthetic sizes. No demo records are changed. Reports include latency percentiles, throughput, concurrency and aggregation checks. Costs, queue age and cloud scaling remain explicitly unmeasured rather than fabricated.

The workspace does not claim DPG certification, official-source authenticity, a calibrated hidden-demand estimate, a live RCT or realized monetary ROI. Apache-2.0 licensing, schemas, reproducibility and role controls are concrete reuse assets; source-data licenses and independent validation still matter.
