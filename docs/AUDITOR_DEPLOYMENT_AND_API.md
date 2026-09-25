# Auditor deployment and API

The Auditor module uses the existing Flask application, operational database and server-side bearer identities. Its read endpoints do not query BigQuery or call model providers; BigQuery remains the analytics replica for the broader application. Ordinary tab loads therefore avoid cloud scan/model costs entirely.

## Start and migrate

```powershell
python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload
```

Application initialization applies the additive migration in `visbharat/services/auditor_schema.py`. It adds case, evidence, detection, evaluation, export and purpose-restriction tables and indexes. It records the existing audit head as the **legacy boundary**. Historical events are not overwritten, deleted or retroactively certified. The ordinary audit writer and transparency writer both append to one new hash-linked segment.

Back up the database before migration. For multiple production workers, apply schema initialization once before serving traffic; DDL initialization is not a substitute for a coordinated production migration rollout. SQLite is the validated local deployment. PostgreSQL SQL paths are included but require a deployment test before rollout. No cloud deployment or public publication was performed in this implementation.

Loopback demo role switching uses the project's existing demo identity mechanism. Production must disable embedded demo tokens and use securely provisioned identities/TLS. The Auditor and Admin can review; Analyst credentials cannot invoke Auditor mutations. Do not place a bearer token in a URL.

Optional `AUDITOR_USER_STATES` is a JSON object mapping a trusted server user name to permitted state names. For example, a reviewer assigned `['Tamil Nadu']` sees only that state's linked resources. Omitted users have installation-wide Auditor access; configure all delegated accounts deliberately. Client role/geography headers do not confer this clearance. This is a single-organization deployment, not a claim of shared-database tenant isolation across governments.

## Endpoints

All endpoints below require an Auditor or Admin bearer identity.

| Method and path | Behavior |
|---|---|
| `GET /api/v2/auditor/snapshot` | Scoped counts, metrics, distributions, receipt coverage and chain-head metadata; no model call |
| `GET /api/v2/auditor/projects` | Search planning groups using canonical Analyst project IDs; paged results |
| `GET /api/v2/auditor/projects/{id}` | Tickets, sources, decisions, evidence, graph edges and readiness |
| `GET /api/v2/auditor/projects/{id}/financial` | Milestone/payment/site records and comparable-period discrepancies |
| `GET /api/v2/auditor/projects/{id}/outcomes` | Paired reviewed service observations; no unsupported causal inference |
| `GET /api/v2/auditor/events` | Full-history server search and cursor pagination through a signed declared-head snapshot |
| `GET /api/v2/auditor/events/{id}` | Authorized detail with sensitive-key redaction |
| `GET /api/v2/auditor/integrity` | Complete protected-segment verification; legacy counts and anchoring limitations |
| `GET, POST /api/v2/auditor/cases` | Read or create persistent review cases |
| `GET /api/v2/auditor/cases/{id}` | Case history |
| `POST /api/v2/auditor/cases/{id}/actions` | Assign, investigate, request evidence, resolve, independently approve high/critical resolution requests, reopen or recommend a hold |
| `POST /api/v2/auditor/projects/{id}/evidence` | Record a source, milestone, payment, site observation, outcome or evaluation protocol |
| `POST /api/v2/auditor/evidence/{id}/review` | Independent human review/rejection with rationale and optimistic version |
| `GET /api/v2/auditor/consent` | Purpose receipts with actual/latest/superseded state and cohort linkage |
| `POST /api/v2/auditor/consent/actions` | Record verified receipt/basis action and local purpose restriction |
| `GET /api/v2/auditor/security` | Typed detections and explicit monitoring coverage |
| `GET, POST /api/v2/auditor/evaluations` | Read stored results or queue labelled evaluation data |
| `POST /api/v2/auditor/exports` | Freeze an event, redacted project or evaluation pack |
| `GET /api/v2/auditor/exports/{id}` | Download the owner's frozen JSON pack; `format=csv` for event packs |
| `GET /api/v2/auditor/exports/{id}/verify` | Public metadata-only SHA-256 integrity check for an evidence pack |
| `POST /api/v2/auditor/exchange/validate` | Validate pack schema/checksum without importing or approving it |
| `GET /api/v2/auditor/readiness` | Implemented limits and outstanding external integrations |

Shared scope: `state`, `district`, `ward`, `category`, `urgency`, `language`, `channel`, `project_id`, `request_id`, `date_from`, `date_to`, `event_from`, `event_to`. Intake dates apply to citizen reports. Event dates apply to each view's events/observations. Unlinked global records are excluded by report filters. Historical evaluations retain their own dataset scope; they are not silently recalculated to match intake filters.

Event search supports `search`, exact `actor`, exact `action`, positive `limit` (maximum 100), `cursor` and the returned `snapshot` token. Carry the same scope/search/snapshot when paginating or exporting. Tokens expire after one hour and are bound to the user. The app's secret must be stable across workers/restarts. Event exports are capped at 10,000 records and require narrower filters above that limit; they never silently truncate. Large unrestricted archival exports are not implemented.

Case creation accepts `kind`, `title`, `notes`, `owner`, `severity`, `data_mode`, project/ticket references and optional `due_at`/`idempotency_key`. Actions require the current integer `version`; stale actions return 409. Identity is always derived from the bearer credential. A case creator cannot close their own finding. High and critical findings first enter `awaiting_approval`; the resolution requester and case creator cannot approve, and a different Auditor must perform `approve_resolution`. A submitter cannot independently review their own evidence.

Evidence records require type, title, source reference, observation time, mode and type-specific metadata. Future observations and nonfinite numbers are rejected. Source metadata includes publisher, release, redistribution terms and boundary crosswalk. Outcomes require measure, unit, catchment, before/after phase, value and sample size. A declared checksum/reference does not authenticate the document. Uploaded URLs are not automatically fetched.

Consent receipts distinguish explicit granted/declined/withdrawn/missing/non-consent-basis states. Ambiguous string booleans are rejected before intake processing. Missing receipts are not fabricated as grants. Local restrictions apply to subsequent planning candidates for `request_processing`/`policy_analytics`; retained audit records stay inspectable. External erasure, retention exceptions, identity verification and downstream completion require their own evidence and responsible review. The module is not a legal-compliance certification.

## Jobs and integrity

Evaluation jobs are stored before execution, processed by a bounded local worker and resumed after restart. The queue accepts at most ten outstanding jobs. Each upload is limited to 1 MiB, 500 samples and 20,000 transcript characters; individual transcripts are limited to 500 characters. Processing uses a computation budget and bounded recovery attempts. There are no remote inference calls. Production multi-process scheduling and queue delay should be measured under the intended deployment.

Supported evaluation inputs are illustrated in `static/data/auditor-evaluation-example.json`. Category/emergency metrics use expected and predicted labels. Optional transcript, translation/location correctness and duplicate-pair fields unlock their respective metrics. Optional baseline language counts yield Jensen–Shannon composition divergence, which is not a bias probability. Keep reviewed holdout data separate from tuning data. Provisional labels never become independently verified through computation.

The chain verifies event content, sequence, links and the captured head across the protected segment. Missing hashes, missing tail events and changed payloads fail verification. Historical unsigned events remain explicitly unverified. A privileged database administrator could rewrite both stream and checkpoint; retain checkpoints independently before making stronger tamper-resistance claims. Full protected-chain verification is explicit and is not run during every tab refresh.

Typed authentication/role denials feed security detections. Routine “viewed alert history” events do not. External infrastructure/token detectors and broad webhook-detection correlation remain deployment integrations; the coverage panel states that limitation.

## Portable evidence and reproducible checks

The export contract is [auditor-evidence.schema.json](release/auditor-evidence.schema.json). JSON packs include a manifest, stable scope/data head, registered legal-source catalog and SHA-256 digest of the canonical record. The public `/verify` endpoint returns metadata only and confirms whether the stored digest matches the retrieved record. Project packs omit citizen text, receipt references, names and private document metadata; export storage is owner/role restricted. CSV cells are neutralized against spreadsheet formula execution. Digest verification does not establish publisher authenticity, legal compliance or administrative approval; an independent trust anchor/public-key signature remains an explicit readiness gate.

```powershell
python -m unittest tests.test_auditor_workbench tests.test_analyst_workbench -q
python -m unittest discover -s tests -t . -q
python scripts/check_auditor_ui.py
python scripts/benchmark_auditor.py
```

The benchmark uses a temporary database; it does not enlarge the live demo. Browser checks inspect the local app, create redacted JSON/CSV exports, and exercise investigation, evidence request, closure and reopening on the existing labelled synthetic Karur case. The case starts and ends open; these actions add simulated review events to its history. `seed_auditor_demo.py` prepares a small labelled demonstration on existing synthetic tickets and refuses non-demo mode. `import_auditor_evaluation.py` imports the recorded 108-case multilingual predictions without making model calls. Both are replay-aware. Do not describe their simulated review actions as real independent validation.

Apache-2.0 covers the application code. Reference datasets and contributed documents have separate redistribution terms. Use the versioned interfaces and reviewed geography crosswalks for agency/federation adapters; do not equate local NVB geography codes with official LGD codes. No DPG certification is asserted.
