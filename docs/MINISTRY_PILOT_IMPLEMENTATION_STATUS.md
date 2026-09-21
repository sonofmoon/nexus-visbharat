# Vellore–Tirupati pilot implementation

Implemented following the user's approval on 19 September 2026. The isolated rehearsal is running at **http://127.0.0.1:5001/pilot**. The original 12,500 demonstration records were preserved. The pilot begins with **63 synthetic water-service reports: Vellore 32, Tirupati 31**.

## Delivered application

| Area | Implemented behaviour |
|---|---|
| Six-tab pilot workspace | Overview, citizen intake, requests/review, proposals/decisions, launch readiness and programme setup; responsive desktop/mobile interface |
| Tamil/Telugu/English intake | Text and browser voice; explicit purpose agreement; durable ticket and private tracking secret; stable idempotency/source IDs for replay-safe imports |
| Officer work | Native transcript and English correction, classification/urgency, assignment and status review; version checks, rationale and audit event; evidence required for closure |
| Access | Programme membership plus server-enforced district scope across pilot, execution table, Analyst and Auditor APIs; revocation, private audio, CSRF-protected sessions, OIDC code/PKCE and explicit subject provisioning |
| Decisions | Reviewed reports feed scoped scenarios; evidence inspection, draft, engineering/catchment review and separate Admin approval; no payment instruction implied |
| Independent audit | Existing Auditor tabs remain linked to the same programme; scoped case ownership, independent closure, protected events, consent controls and owner/scope-bound frozen exports |
| Launch controls | Six proposed catchments; editable community evidence; ten recorded launch gates; synthetic evidence can be rehearsed but cannot certify operational acceptance |
| Programme operation | Configurable report capacity, cost allowance, proposed start date, routing and officer assignments; source-ID-aware batch import with per-record outcomes |

A fresh operational bootstrap refuses existing programmes/citizen records. Opening requires accepted gate evidence, reviewed locations and named Admin/Analyst/Auditor identities. A settings change cannot convert the synthetic corpus into real citizen evidence.

## Technical and deployment work

- Added programme, geography, membership, identity, intake, outbox, provider-step and readiness tables through additive migrations. Runtime migrations/demo seeding can be disabled; the cloud migration job has a separate service account and DDL secret.
- Fixed PostgreSQL auto-increment DDL, empty-string quoting and percent wildcard handling. Tested the actual PostgreSQL 16.10 backend on a temporary localhost server with isolated per-test schemas.
- Added leased durable processing and authenticated Cloud Tasks/Scheduler dispatch. Provider outages retain tickets for manual review. Completed provider steps are reused; ambiguous model calls are not automatically rebilled. Operational Auditor evaluation uses managed dispatch instead of depending on a web-process thread.
- Added a single-attempt regional Vertex adapter with returned usage/model provenance and an opt-in regional Speech v2 adapter. The local rehearsal invokes neither service; mocked transport tests are not live AI evaluation.
- Added authenticated retained-audio playback and paired private regional object storage. SQL keeps the audio bytes until both copies exist. BigQuery uses a separate, regional, versioned MERGE table containing redacted summaries; analytics failure does not overwrite officer review state.
- Prepared and validated Terraform for Mumbai web/workers and Cloud SQL HA, Delhi SQL replica and cold standby service, private networking, secret replication, audio buckets, bounded queues, BigQuery and budget alerts. No cloud plan/apply or paid provisioning was performed.
- Added a PostgreSQL/container CI workflow, operational environment template, deployment/rollback/recovery runbook, four-week opening/six-week evaluation schedule and six-minute jury walkthrough.

The deployment remains one isolated programme/database. Broader national rollout needs additional administrative configurations, language evaluation, capacity and operational evidence. The four hexadecimal suffix characters in the current ticket format provide 65,536 combinations per UTC day per deployment; higher-volume/shared ticket namespaces require a deliberate format revision.

## Verification evidence

| Check | Result and practical limit |
|---|---|
| Full application regression | **228 passed** in 170.770 seconds; 27 PostgreSQL checks skipped by default and passed in the separate PostgreSQL run; tests isolate external services |
| PostgreSQL 16.10 | **27 passed** in 76.772 seconds; fresh migrations, concurrent duplicate intake, scope/privacy, review, complete decision/audit flow, exports, provider replay and job recovery; local backend, not Cloud SQL/network HA |
| Focused Pilot/Analyst/Auditor checks | **66 passed**, followed by a passing final programme-settings serialization check |
| Browser acceptance | All six tabs at desktop and 390px mobile widths; no horizontal overflow, JavaScript errors or failed API requests; both district roles, Tamil/Telugu intake/manual review, proposal approval, gate evidence and full-suite deep links passed |
| Browser timing | Latest first useful content **1,074 ms**, one local Chromium run on an isolated fixture copy; not a 4G/cloud p95 or national load benchmark |
| Terraform 1.11.4 / Google provider 6.50.0 | **Valid**, zero errors/warnings; configuration validation does not prove project quotas, regional SKU availability, price or actual cloud recovery |
| Running localhost smoke check | Pilot/intake/full-suite pages and readiness return HTTP 200; Admin sees 63 records, Vellore officer 32, Tirupati officer 31 |

Machine-readable evidence: [PostgreSQL](evaluation/ministry-pilot-postgres.json), [browser results](evaluation/ministry-pilot-browser/result.json), [infrastructure validation and file hashes](evaluation/ministry-pilot-infrastructure.json), [seed manifest](evaluation/ministry-pilot-demo.json). Browser screenshots are in `docs/evaluation/ministry-pilot-browser/`. Browser mutations were performed on a disposable database copy.

## Remaining external acceptance gates

Real ministry participation and both implementing authorities; six actual communities and reviewed LGD crosswalks; verified publisher releases/redistribution terms; a real IdP handshake and access review; approved regional model/speech availability and live evaluation; independently reviewed language/audio labels; container/image scan in CI; cloud load/backup/failover tests and measured RPO/RTO; approved retention/deletion procedures; dated cloud prices/billing reconciliation; and real officer training/field measurements.

These are visible launch conditions, not completed activities. No verified ministry partnership, live messaging integration, field benefit, national-scale load result or award outcome is asserted. The original translation-price line remains a conservative allowance; reprice Vertex translation using measured tokens before spending. The proposed ₹1.5 lakh/month cloud envelope remains unverified.

See [deployment and recovery runbook](MINISTRY_PILOT_DEPLOYMENT_RUNBOOK.md), [jury walkthrough](MINISTRY_PILOT_JURY_WALKTHROUGH.md) and [approved roadmap/cost model](MINISTRY_PILOT_ROADMAP_AND_COST_MODEL.md).
