# Nexus VisBharath v1 — Complete Documentation

Version: 1.0  
Last Updated: 2026-09-06  
Repository: `nexus-citizen-voice`

---

## Table of Contents
1. Executive Summary
2. Problem Statement and Mission
3. Vision, Scope, and Non-Goals
4. Product Capabilities
5. Current Implementation Snapshot (What Exists Now)
6. System Architecture
7. Repository Structure
8. Technology Stack
9. Data Model and Schema
10. Application Flows (End-to-End)
11. API Documentation
12. Authentication and Authorization (RBAC)
13. Audit Logging and Traceability
14. AI/ML Layer (Current and Target)
15. Security Design
16. Deployment and Environment Setup
17. Local Development Guide
18. Operations Runbook
19. Monitoring and Reliability
20. Testing and Validation
21. Governance, Privacy, and Compliance
22. KPI Framework
23. Known Limitations (v1)
24. Upgrade Roadmap (v1 -> v2)
25. Troubleshooting Guide
26. Glossary
27. Appendix (Sample Requests)

---

## 1) Executive Summary

**Nexus VisBharath** is a multilingual civic intelligence platform that transforms citizen development requests into actionable, auditable infrastructure priorities for policymakers.

The platform currently supports:
- Citizen request intake via API (`text` and simulated `voice` flow)
- AI-assisted classification (simulated in v1)
- District-level enrichment from reference datasets
- Hotspot and policy summary endpoints
- Production skeleton patterns: modular architecture, database persistence, token auth, role-based access control (RBAC), and audit logs

This documentation captures the full journey from concept to current v1 implementation and defines the path to production maturity.

---

## 2) Problem Statement and Mission

### Problem
Governments face fragmented citizen feedback systems, causing:
- Misaligned public spending
- Unresolved infrastructure demand hotspots
- Weak traceability and impact measurement

### Mission
Build a scalable Digital Public Good platform that:
- Aggregates multilingual citizen requests from diverse channels
- Combines citizen demand with demographic/infrastructure context
- Produces explainable, priority-ranked development insights
- Enables secure, auditable, role-governed governance workflows

---

## 3) Vision, Scope, and Non-Goals

### Vision
Convert “every citizen voice” into a structured, trusted public planning signal.

### In Scope (v1 Skeleton)
- Modular Flask service architecture
- Persistent SQLite storage
- Public and secure APIs
- Three-role RBAC model (`admin`, `analyst`, `auditor`)
- Audit event capture for sensitive operations

### Out of Scope (v1)
- Real ASR/translation/classification model integrations
- Full workflow engine for approvals/escalations
- Multi-tenant state boundary enforcement
- Advanced geospatial model scoring

---

## 4) Product Capabilities

### Citizen Side
- Submit requests with language, district, channel source
- Optional simulated voice processing path (`is_voice=true`)

### Policy Side
- View complaints and hotspot summaries
- Fetch district policy brief summaries
- View project lists from reference data

### Governance Side
- Role-guarded secure endpoints
- Audit trail access by role
- Admin-managed user/token provisioning

---

## 5) Current Implementation Snapshot (What Exists Now)

The platform has evolved from a monolithic prototype into a modular production skeleton.

### Initial State
- Single-file Flask app (`app.py`) with in-memory submissions
- Demo-focused AI simulation logic

### Current State
- App factory architecture in `visbharat/`
- Persistent DB (`visbharat.db`) with schema initialization
- Token-based authentication
- RBAC with three operational roles
- Audit logs for request viewing and user actions

---

## 6) System Architecture

### Logical Layers
1. **Presentation Layer**: HTML templates and static dashboard resources
2. **API Layer**: Public and secure endpoints
3. **Domain Services Layer**: AI simulation and reference data helpers
4. **Security Layer**: Bearer token auth + role-based endpoint controls
5. **Persistence Layer**: SQLite tables for users, citizen requests, audit logs
6. **Governance Layer**: Immutable audit event recording

### Runtime Flow
Citizen Input -> Ingestion API -> AI simulation -> District enrichment -> DB write -> Audit write -> Analytics endpoints -> Role-restricted governance APIs

---

## 7) Repository Structure

```text
.
+-- app.py
+-- config.py
+-- visbharat/
¦   +-- __init__.py
¦   +-- config.py
¦   +-- db.py
¦   +-- auth.py
¦   +-- audit.py
¦   +-- blueprints/
¦   ¦   +-- __init__.py
¦   ¦   +-- web.py
¦   ¦   +-- api.py
¦   +-- services/
¦       +-- __init__.py
¦       +-- ai_simulation.py
¦       +-- repository.py
+-- templates/
+-- static/
¦   +-- data/
+-- docs/
¦   +-- VisBharath_Complete_Documentation.md
+-- .env.example
```

---

## 8) Technology Stack

### Current
- Python 3.x
- Flask + Flask-CORS
- Pandas/Numpy (reference datasets and analytics helpers)
- SQLite (embedded persistence)

### Planned Evolution
- PostgreSQL + PostGIS
- Redis + worker queue
- API Gateway + WAF
- Real model serving infrastructure

---

## 9) Data Model and Schema

Defined in `visbharat/db.py`.

### `users`
- `id`
- `name`
- `api_token` (unique)
- `role` (`admin` | `analyst` | `auditor`)
- `created_at`

### `citizen_requests`
- `id`
- `request_id` (public unique id)
- `source_channel`
- `input_language`
- `district`, `state`
- `lat`, `lng`
- `original_text`, `translated_text`
- `category`, `urgency`, `sentiment`, `status`
- `submitted_by`
- `ai_metadata_json`
- `created_at`

### `audit_logs`
- `id`
- `actor`
- `action`
- `resource_type`
- `resource_id`
- `details_json`
- `ip_address`
- `created_at`

---

## 10) Application Flows (End-to-End)

### Flow A: Citizen Request Intake
1. Client calls `POST /api/submit`
2. Payload validated for text and optional voice flag
3. AI simulation performs translation/classification
4. District geolocation enrichment applied
5. Request persisted in `citizen_requests`
6. Audit event recorded (`citizen_request_submitted`)
7. API returns `request_id` and classification summary

### Flow B: Secure Analyst Access
1. Analyst calls secure endpoint with bearer token
2. Token resolved to user in `users`
3. Role checked against route policy
4. Data returned on success
5. Audit event recorded for secure access

### Flow C: Audit and Oversight
1. Admin/auditor calls `GET /api/v1/audit-logs`
2. Role validation enforced (`admin` or `auditor`)
3. Ordered audit events returned for traceability

---

## 11) API Documentation

### Public APIs
### Channel Webhook APIs (Phase 1)
- `GET/POST /api/channels/whatsapp/webhook`
  - GET verifies WhatsApp webhook challenge
  - POST ingests WhatsApp text payloads
- `POST /api/channels/telegram/webhook`
  Ingests Telegram message payloads.
- `POST /api/channels/sms/webhook`
  Ingests SMS provider payloads.
- `POST /api/channels/ivr/webhook`
  Ingests IVR transcript payloads.

All POST channel webhooks require `X-Webhook-Token` matching `WEBHOOK_SHARED_TOKEN`.
### Provider Signature Verification
- WhatsApp (Meta): validates `X-Hub-Signature-256` using `META_APP_SECRET`
- Telegram: validates `X-Telegram-Bot-Api-Secret-Token` using `TELEGRAM_WEBHOOK_SECRET`
- SMS/IVR (Twilio-style): validates `X-Twilio-Signature` using `TWILIO_AUTH_TOKEN`
- Shared gate: `X-Webhook-Token` checked against `WEBHOOK_SHARED_TOKEN`
- `GET /api/health`  
  Returns service health.

- `GET /api/db/status`  
  Returns active database backend (`sqlite` or `postgres`) and target configuration.

- `POST /api/submit`  
  Submits a citizen request.

- `GET /api/complaints?district=&limit=`  
  Returns request list from DB.

- `GET /api/stats`  
  Returns aggregate platform stats.

- `GET /api/states`  
  Returns list of states from reference data.

- `GET /api/districts?state=`  
  Returns districts list.

- `POST /api/translate`  
  Returns simulated translation output.

- `POST /api/classify`  
  Returns simulated category/urgency/sentiment.

- `GET /api/hotspots`  
  Returns district hotspot scoring summary.

- `GET /api/priority-projects?district=`  
  Returns project rows from reference dataset.

- `GET /api/policy-brief/<district>`  
  Returns generated district summary and recommendations.

### Secure APIs
- `GET /api/v1/auth/me`  
  Returns authenticated user profile.

- `GET /api/v1/requests`  
  Roles: `admin`, `analyst`, `auditor`.

- `GET /api/v1/audit-logs`  
  Roles: `admin`, `auditor`.

- `GET /api/v1/users`  
  Roles: `admin`.

- `POST /api/v1/users`  
  Roles: `admin`.

---

## 12) Authentication and Authorization (RBAC)

### Authentication
- Bearer token from `Authorization` header
- Tokens mapped to users in DB

### Authorization model
- `admin`
  - Full operational and governance access
  - User management
  - Request and audit visibility

- `analyst`
  - Access to secure request intelligence
  - No audit log access
  - No user management

- `auditor`
  - Read access to requests and audit logs
  - No user management

### Default seed tokens
Configured via `.env`:
- `ADMIN_API_TOKEN`
- `ANALYST_API_TOKEN`
- `AUDITOR_API_TOKEN`

---

## 13) Audit Logging and Traceability

Audit events are written via `visbharat/audit.py`.

### Logged event examples
- `citizen_request_submitted`
- `secure_requests_listed`
- `audit_logs_viewed`
- `user_created`

### Fields captured
- Actor
- Action
- Resource type/id
- JSON details
- Source IP
- UTC timestamp

This supports compliance investigations and governance evidence.

---

## 14) AI/ML Layer (Current and Target)

### Current (v1)
`visbharat/services/ai_simulation.py` includes deterministic/simulated functions:
- `simulate_speech_to_text`
- `simulate_translation`
- `simulate_gemini_intent_classification`

### Target (v2+)
- Replace simulators with real multilingual ASR/translation/classification services
- Add model confidence gating and fallback to human verification
- Add drift monitoring and model registry lifecycle

---

## 15) Security Design

### Current controls
- Token auth and route-level RBAC
- Role-scoped access restrictions
- Audit logs for sensitive operations
- Input validation basics on write endpoints

### Required production hardening
- Token hashing and rotation workflows
- TLS termination and strict headers
- API gateway with rate limits and WAF
- Secret manager integration
- PII minimization and encryption at rest
- Threat detection (bot flooding, abuse patterns)

---

## 16) Deployment and Environment Setup

### Environment file
Use `.env` keys:
- `SECRET_KEY`
- `DEMO_MODE`
- `DATABASE_PATH`
- `DATABASE_URL` (for PostgreSQL/Alembic target DB)
- `ADMIN_API_TOKEN`
- `ANALYST_API_TOKEN`
- `AUDITOR_API_TOKEN`

### Startup
```bash
pip install -r requirements.txt
python app.py
```

---

## 17) Local Development Guide

1. Create and activate virtual environment
2. Install dependencies
3. Configure `.env` using `.env.example`
4. Run `python app.py`
5. Visit UI routes:
   - `/`
   - `/submit`
   - `/dashboard`
6. Test secure API with bearer token

---

## 18) Operations Runbook

### Daily checks
- Service health endpoint status
- DB file growth and write health
- Error rates from server logs
- Audit event continuity

### Weekly checks
- Token inventory validation
- Suspicious access pattern review
- Data quality scan (district/category completeness)

### Incident workflow
1. Detect anomaly
2. Identify actor and route from audit logs
3. Revoke/rotate affected token
4. Capture forensic snapshot
5. Patch root cause
6. Publish incident report

---

## 19) Monitoring and Reliability

### Current
- Basic Flask runtime and endpoint checks

### Recommended
- Structured JSON logging
- Metrics: request rate, latency, auth failures, 5xx rates
- Alerts for repeated 401/403 spikes
- Backup and recovery checks for DB

---

## 20) Testing and Validation

### Completed smoke validations
- App imports and boots via app factory
- Public routes return 200
- Secure routes enforce auth (`401` when missing)
- RBAC role boundaries enforce `403` where expected
- Citizen request submission writes DB and audit logs

### Next test expansions
- [x] Unit tests for auth decorators and role matrix (`tests/test_api_contract_and_rbac.py`)
- [x] API contract smoke tests for core public and secure endpoints (`tests/test_api_contract_and_rbac.py`)
- [x] Data integrity tests for schema constraints (`tests/test_data_integrity.py`)

---

## 21) Governance, Privacy, and Compliance

### Governance principles
- Explainability in policy output paths
- Role segregation of duties
- Independent oversight via auditor role

### Privacy principles
- Data minimization for citizen-identifying fields
- Purpose-limited retention policy
- Access logging and role-based visibility

### Compliance readiness (target)
- Policy-aligned retention/deletion workflows
- External audit exportability
- Change management and sign-off trails

---

## 22) KPI Framework

### Platform KPIs
- Total citizen requests processed
- Processing latency
- Uptime for intake and secure APIs

### Governance KPIs
- Audit coverage ratio for secure actions
- Unauthorized access attempt rate
- Role-policy violation attempts

### Outcome KPIs (target)
- Repeat complaint reduction by district
- Time-to-priority-decision
- Budget alignment to high-demand hotspots

---

## 23) Known Limitations (v1)

- AI is simulated; not connected to real inference systems
- SQLite is suitable for pilot but not high-throughput national scale
- No background job queue for heavy async processing
- No native geospatial database scoring (PostGIS pending)
- API tokens are plain in seed/bootstrap flows (needs managed secrets)

---

## 24) Upgrade Roadmap (v1 -> v2)

### Priority 1
- [x] Replace SQLite with PostgreSQL scaffold (config + migration baseline added)
- [x] Add Alembic migrations (initial migration scaffold added)
- [x] Token hashing and rotation APIs (implemented in current baseline)

### Priority 2
- Real ASR/translation/classification integrations
- Queue-based asynchronous pipelines
- Rich hotspot scoring model

### Priority 3
- State tenancy boundaries and data partitioning
- Policy workflow approvals and decision lifecycle engine
- Public transparency portal with anonymized analytics

---

## 25) Troubleshooting Guide

### `401 Missing bearer token`
- Ensure `Authorization: Bearer <token>` header exists

### `401 Invalid token`
- Verify token value matches seeded/admin-created user token

### `403 Forbidden for this role`
- Verify endpoint role policy:
  - `/api/v1/users` -> admin only
  - `/api/v1/audit-logs` -> admin or auditor

### Empty dashboard/analytics responses
- Check if `citizen_requests` has data
- Submit test request using `/api/submit`

### DB issues
- Confirm `DATABASE_PATH` is writable
- Remove stale/corrupt DB only in local non-production setup and restart app for clean bootstrap

---

## 26) Glossary

- **RBAC**: Role-Based Access Control
- **ASR**: Automatic Speech Recognition
- **PII**: Personally Identifiable Information
- **DPI/DPG**: Digital Public Infrastructure / Digital Public Good
- **Hotspot**: Geographic area with high volume/urgency of citizen demand

---

## 27) Appendix (Sample Requests)

### A) Submit citizen request
```bash
curl -X POST http://localhost:5000/api/submit \
  -H "Content-Type: application/json" \
  -d '{"text":"Road damaged and water supply failed","language":"en","district":"Chennai","source":"Web"}'
```

### B) Get authenticated profile
```bash
curl http://localhost:5000/api/v1/auth/me \
  -H "Authorization: Bearer visbharat-analyst-token"
```

### C) Admin list users
```bash
curl http://localhost:5000/api/v1/users \
  -H "Authorization: Bearer visbharat-admin-token"
```

### D) Auditor fetch logs
```bash
curl http://localhost:5000/api/v1/audit-logs \
  -H "Authorization: Bearer visbharat-auditor-token"
```

---

## Final Note

This document is the authoritative end-to-end technical baseline for Nexus VisBharath v1 in its current repository state. It includes concept, architecture, implementation, security, governance, RBAC, operations, and evolution path.











### Replay Protection
- Channel webhooks enforce X-Webhook-Timestamp and X-Webhook-Nonce.
- Requests outside WEBHOOK_REPLAY_WINDOW_SECONDS or repeated nonce/timestamp combinations are rejected.


### Optional Signature Binding
- If WEBHOOK_BIND_REPLAY_IN_SIGNATURE=true, verification for Meta and Twilio-style signatures additionally binds X-Webhook-Timestamp and X-Webhook-Nonce into the signature base string.


### How to generate provider signatures
- See README.md for concrete Meta and Twilio local curl examples with replay headers.


### Source IP Allowlist
- Channel webhooks can be restricted with `WEBHOOK_ALLOWED_IPS` (comma-separated IP/CIDR).

### Replay Store Mode
- `WEBHOOK_REPLAY_STORE=db` stores nonce/timestamp entries in `webhook_nonces` table for multi-worker durability.
- `WEBHOOK_REPLAY_STORE=memory` keeps in-process cache for local/dev use.



### Security Ops API
- `GET /api/security/webhook-status` (admin only) exposes effective webhook security settings without returning secret values.

- Status payload includes last_checked_at and policy_version for auditability/versioning (no secret values).


### Phase 2 Processing Orchestration
- Queue persistence in `processing_jobs` table.
- Async mode controlled by `ASYNC_PIPELINE_ENABLED`.
- Worker executes retries with backoff and dead-letter transition.
- Job status visibility through secure APIs:
  - `GET /api/v1/pipeline/jobs`
  - `GET /api/v1/pipeline/jobs/<job_id>`

- `GET /api/v1/pipeline/workers` returns current worker heartbeats (admin/auditor).
- Worker script supports graceful signal handling (`SIGINT`/`SIGTERM`) and heartbeat row updates.

- Pipeline control APIs:
  - `GET /api/v1/pipeline/metrics` (admin/auditor)
  - `POST /api/v1/pipeline/jobs/<job_id>/retry` (admin)
  - `POST /api/v1/pipeline/jobs/<job_id>/cancel` (admin)
- Channel webhooks support `X-Idempotency-Key` for enqueue deduplication.


### Phase 3 Intelligence Layer
- Secure intelligence APIs (admin/analyst/auditor):
  - `GET /api/v1/intelligence/gap-analysis`
  - `GET /api/v1/intelligence/hotspot-predictions`
- `gap-analysis` returns ranked districts by infrastructure severity and deprivation-weighted demand.
- `hotspot-predictions` returns ranked near-term pressure predictions based on current inflow + baseline stress.
- Both endpoints append governance audit records for traceable policy usage.


### Phase 3 Policy Layer (Priority + Budget Draft)
- Secure policy endpoint (admin/analyst/auditor):
  - `GET /api/v1/policy/priority-rankings`
  - `GET /api/v1/policy/impact-brief`
- Query parameters:
  - `limit` (default 25, max 500)
  - `total_budget_lakh` (default 5000)
- Response includes ranked district priorities plus draft budget allocation fields:
  - `priority_score`
  - `estimated_project_cost_lakh`
  - `funded_in_draft_plan`
  - `cumulative_budget_used_lakh`
- Endpoint writes `policy_priority_rankings_viewed` audit entries.
- `impact-brief` returns portfolio-level impact metrics and auto-generated policy brief draft fields (`summary`, `recommendations`, `risks`).
- Endpoint writes `policy_impact_brief_viewed` audit entries.


### Phase 3 Burn-in and Go-Live Readiness
- Burn-in script: `scripts/burnin_phase3_policy.py`
- Execute (PowerShell):
  - `$env:PYTHONPATH='.'`
  - `python scripts/burnin_phase3_policy.py`
- Output report: `docs/reports/Phase3_GoLive_Readiness_Report.md`
- Ops handoff pack: `docs/reports/Phase3_Ops_Handoff_Pack.md`
- Checklist validates auth enforcement, payload contracts, budget sensitivity, repeated-call stability, and policy audit-trail creation.

### Phase 4 Decision Workflow (Approvals Lifecycle)
- Secure lifecycle APIs:
  - `POST /api/v1/policy/decisions/draft` (admin/analyst)
  - `GET /api/v1/policy/decisions` (admin/analyst/auditor)
  - `POST /api/v1/policy/decisions/<decision_id>/approve` (admin)
  - `POST /api/v1/policy/decisions/<decision_id>/reject` (admin)
- Persists lifecycle state in `policy_decisions` table.
- Enforces transition guardrails (`draft` -> `approved` or `rejected`).
- Emits governance audit events for draft/list/approve/reject actions.

### Phase 4 Burn-in and Go-Live Readiness
- Burn-in script: `scripts/burnin_phase4_policy_workflow.py`
- Execute (PowerShell):
  - `$env:PYTHONPATH='.'`
  - `python scripts/burnin_phase4_policy_workflow.py`
- Output report: `docs/reports/Phase4_GoLive_Readiness_Report.md`
- Ops handoff pack: `docs/reports/Phase4_Ops_Handoff_Pack.md`

### Phase 5 Public Transparency Portal (Anonymized Analytics)
- Public transparency APIs (no bearer token required):
  - `GET /api/public/transparency/summary`
  - `GET /api/public/transparency/districts`
  - `GET /api/public/transparency/categories`
- Aggregation-only payloads for public accountability dashboards.
- Privacy enforcement: low-volume group suppression via `PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE`.
- District listing limit bounded by `PUBLIC_TRANSPARENCY_MAX_LIMIT`.

### Phase 5 Burn-in and Go-Live Readiness
- Burn-in script: `scripts/burnin_phase5_transparency.py`
- Execute (PowerShell):
  - `$env:PYTHONPATH='.'`
  - `python scripts/burnin_phase5_transparency.py`
- Windows launcher:
  - `scripts\burnin_phase5_transparency.bat`
- Output report: `docs/reports/Phase5_GoLive_Readiness_Report.md`
- Ops handoff pack: `docs/reports/Phase5_Ops_Handoff_Pack.md`

### Master Release Gate Burn-in (Phase 2 + 3 + 4 + 5)
- Master script: `scripts/burnin_release_gate.py`
- Execute (PowerShell):
  - `$env:PYTHONPATH='.'`
  - `python scripts/burnin_release_gate.py`
- Windows launcher:
  - `scripts\burnin_release_gate.bat`
- Consolidated report: `docs/reports/ReleaseGate_Burnin_Report.md`
- Runs and validates:
  - `scripts/burnin_phase3_policy.py`
  - `scripts/burnin_phase4_policy_workflow.py`
  - `scripts/burnin_phase5_transparency.py`


### Local Processing Completeness (All 4 Components)
- Cloud Speech-to-Text: local simulation + optional live mode via `POST /api/submit-voice`
- Translation API: local simulation + optional live mode via `POST /api/translate`
- Gemini Classification: local simulation + optional live mode via `POST /api/classify`
- Dialogflow CX guided intake: local deterministic conversation flow via `POST /api/dialogflow/session`

### Phase 2 Burn-in and Go-Live Readiness
- Burn-in script: `scripts/burnin_phase2_processing.py`
- Execute (PowerShell):
  - `$env:PYTHONPATH='.'`
  - `python scripts/burnin_phase2_processing.py`
- Windows launcher:
  - `scripts\burnin_phase2_processing.bat`
- Output report: `docs/reports/Phase2_GoLive_Readiness_Report.md`
- Ops handoff pack: `docs/reports/Phase2_Ops_Handoff_Pack.md`
