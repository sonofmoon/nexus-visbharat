# Nexus VisBharat (NVB)

## Multilingual civic intelligence for better public services

Nexus VisBharat connects citizen voice with the public-service teams responsible for understanding, prioritising and following through on local needs. A person can submit a report in text or speech; NVB protects the input, structures the context, routes the case and preserves the human decision trail.

This repository is the Code for Communities submission for [Build with AI: Code for Communities, second edition](https://hack2skill.com/event/codeforcommunities2?utm_source=hack2skill&utm_medium=homepage&sectionid=6a7aeec965fbd7acf70c1764).

## Live service and interactive showcase

- **Primary Live Cloud Run Deployment:** [nexus-visbharath-510474645723.asia-south1.run.app](https://nexus-visbharath-510474645723.asia-south1.run.app/)
- **Interactive Visual Showcase App:** [nexusaitech.in/apps/nvb/index.html](https://nexusaitech.in/apps/nvb/index.html)
- **Source code:** [github.com/sonofmoon/nexus-visbharat](https://github.com/sonofmoon/nexus-visbharat)

The live deployment is a working demonstration environment. The showcase corpus is synthetic, and the evidence notes below describe what has been measured, what is provisional and what still requires a real municipal pilot.

## The idea in one minute

Public-service information often arrives as short messages, voice notes and local-language descriptions. The signal is valuable, but it is difficult to compare across channels, languages, locations and departments.

NVB creates a shared operating picture:

```mermaid
flowchart LR
    A[Citizen voice or text] --> B[Consent and PII scrubbing]
    B --> C[Speech, translation and classification]
    C --> D[Urgency, location and service routing]
    D --> E[Related demand and evidence]
    E --> F[Analyst review and prioritisation]
    F --> G[Human decision and delivery follow-up]
    G --> H[Auditable outcome history]
```

The system keeps model-assisted interpretation separate from policy decisions. Immediate hazards use a deterministic FastPath, while capital and programme decisions remain subject to human review.

## See the product in action

The screenshots below are part of the repository and show the main product journey. They use the prepared showcase environment; they do not represent authenticated citizen records or proof of government adoption.

### 1. Start with a citizen signal

A multilingual intake experience gives people a practical way to describe a local problem through text or voice.

![NVB citizen portal](docs/screenshots/01_hero_portal.png)

*The public-facing entry point introduces the workflow and makes the live service easy to reach.*

### 2. Capture voice with language context

The intake flow supports speech and text, keeps the original language visible and gives the person an opportunity to review the interpreted content before submission.

![NVB citizen voice intake](docs/screenshots/02_citizen_intake_voice.png)

*Voice intake is designed around transcript review and human confirmation.*

### 3. Move from individual reports to patterns

The policy dashboard helps teams compare demand, service categories, geography and inclusion signals before considering a response.

![NVB policy dashboard](docs/screenshots/03_national_dashboard.png)

*The dashboard is a decision-support view; it does not make an automatic budget award.*

### 4. Rehearse a scoped pilot

The pilot workspace shows how a programme can rehearse local routing and state or district boundaries before a wider rollout.

![NVB pilot workspace](docs/screenshots/05_ministry_pilot_dashboard.png)

*Pilot scenarios remain labelled as rehearsal data until an authority supplies approved operational data.*

### 5. Preserve the evidence trail

The auditor workspace links cases, evidence and recorded actions so that an authorised team can inspect what happened and why.

![NVB audit dossier](docs/screenshots/06_audit_dossier.png)

*The audit chain is tamper-evident and explicitly verifiable; it is not presented as an immutable ledger.*

## Product capabilities

| Capability | What NVB provides |
|---|---|
| Citizen intake | Text, browser voice and assisted channel workflows with consent and source context. |
| Language intelligence | Speech transcription, translation and structured extraction for English, Tamil and Telugu paths. |
| Emergency FastPath | Deterministic hazard screening and escalation when provider calls are unavailable or a high-severity signal needs immediate handling. |
| Analyst workspace | Demand, inclusion, gap, project and budget scenario views for human-led planning. |
| Delivery follow-up | Tasks, decisions, evidence and completed-window outcome reporting in one timeline. |
| Audit and governance | Role-scoped access, minimized provider telemetry, PII scrubbing, idempotent intake and hash-linked audit events. |

## Google Cloud implementation

NVB uses Google Cloud services where they provide useful operational capability:

- **Cloud Run** hosts the web application and service endpoints.
- **Gemini / Vertex AI** supports structured classification, translation and synthesis where configured.
- **Cloud Speech-to-Text** supports voice intake.
- **Cloud Translation** supports multilingual workflows.
- **Cloud SQL PostgreSQL** is the durable deployment path for operational records.
- **Pub/Sub and an outbox pattern** support controlled agency delivery and replay-safe processing.
- **BigQuery and reference-data adapters** support analytics and contextual indicators where configured.

The application includes a deterministic local path for emergency screening and offline development. A configured provider is not treated as proof of a successful live inference; provider evidence is recorded separately when available.

## Trust model and operating boundaries

NVB is designed to make the important boundaries visible:

- Personal identifiers are scrubbed at ingress before storage or model processing where the configured scrubber applies.
- Model output is kept separate from policy scoring and human authorisation.
- Role-scoped access limits analyst, auditor and administrator operations.
- Audit events are hash-linked and can be explicitly verified.
- Synthetic, descriptive and externally verified evidence are labelled separately.
- A source checksum establishes file integrity; it does not establish publisher authenticity.
- Consent and access controls are engineering implementations of DPDP Act 2023 principles, not formal legal certification.

## Public release and live-feed contract

The Public Suite is a publication-safe view of NVB. It is intentionally
different from the authenticated Analyst, Auditor and Admin workspaces.

### Public data boundary

- The public complaint feed publishes category-level summaries only.
- Citizen-submitted text, contact details, PII and exact locations are withheld.
- Ward values are not fabricated when they are unavailable.
- Repeated records and automated confirmation messages are suppressed or marked.
- Small geographic and category groups are suppressed using
  `PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE`, which defaults to `3`.
- Differential privacy can be enabled for public aggregates with
  `PUBLIC_TRANSPARENCY_DP_ENABLED=true`.
- When enabled, the default public-transparency epsilon is `0.75`, configurable
  through `PUBLIC_TRANSPARENCY_DP_EPSILON`.

### Public endpoints

| Endpoint | Purpose |
|---|---|
| `/api/complaints` | Filtered, redacted public complaint feed |
| `/api/v1/live-feed/stream` | Server-sent live-feed updates with reconnect cursors |
| `/api/public/transparency/summary` | Privacy-thresholded public metrics |
| `/api/public/transparency/priority-signals` | Screening-only priority signals |
| `/api/public/transparency/districts` | Thresholded district aggregates |
| `/api/public/transparency/categories` | Thresholded category aggregates |
| `/api/v1/transparency/verify-chain` | Hash-linked audit-chain verification |
| `/api/v2/pilot/track` | Private ticket tracking using a citizen receipt code |

Priority signals are planning-screening outputs. They are not funded projects,
budget approvals, delivery-progress claims or causal impact evidence. Engineering
review, administrative approval and field evidence are required before any
implementation claim.

### Live-feed behavior

The live feed supports state, district, category, urgency, language, channel,
date, stage, ticket-reference search and sort filters. It uses deduplication,
server-sent events, reconnect cursors and polling fallback.

Anonymous browser clicks cannot create verified public support. Verified support
requires a valid citizen receipt token.

## Authenticated suites and decision controls

NVB separates public transparency from authenticated operational workspaces.
Analyst, Auditor and Admin views expose progressively stronger controls and
never treat an AI output, imported document or checksum as automatic proof.

### Analyst Suite

The Analyst Suite supports human-led planning through:

- Demand and Inclusion screening using observed request patterns, deprivation
  context, service gaps, emergency pressure and evidence-gap indicators.
- Project Priorities that produce ranked planning candidates labelled as
  screening outputs when reference evidence is incomplete or unverified.
- Budget Scenarios with explicit weights, capacity limits, district constraints,
  cost sensitivity and operating-cost assumptions.
- Delivery and Responsiveness views derived from recorded events, SLA timing,
  ageing, feedback and decisions.
- Outcomes and Evaluation views using comparable before/after observations while
  avoiding unsupported causal-impact claims.
- Source-aware policy briefs with claim-level references and governed corpus
  citations where available.

Scenario analysis is separated from approval. Scenario reads do not change active
scoring profiles, imported documents are validated before persistence, and an
Analyst cannot approve a project or claim that an illustrative cost represents
actual expenditure.

### Auditor Suite

The Auditor Suite provides independent review and evidence controls:

- Case lifecycle management for investigation, evidence requests, resolution,
  reopening and hold recommendations.
- Registered legal-source provenance with source title, official URL, section,
  release information and verification status.
- Evidence records for sources, milestones, payments, site observations,
  outcomes and evaluation protocols.
- Independent evidence review or rejection with reviewer identity, rationale and
  version checks.
- Consent and processing-purpose records that distinguish granted, declined,
  withdrawn, missing and other-basis states.
- Independent approval controls for high- and critical-severity resolutions.
  The resolution requester and case creator cannot approve their own finding.
- Portable redacted evidence packs with manifests, stable scope/data heads and
  SHA-256 integrity verification.

A checksum confirms record integrity only. It does not establish publisher
authenticity, legal compliance, administrative approval or causal impact.

### Admin Suite

The Admin Suite provides controlled operational governance:

- Engineering-cost and catchment evidence review before project approval.
- A separate approval action that does not represent expenditure, physical
  completion or verified public impact.
- Versioned project decisions, commitment checks and duplicate-approval
  protection.
- Officer lifecycle updates, delivery-health review, user controls and security
  alert visibility.
- Access to Auditor review tools under Admin identity while preserving
  independent-approval rules.
- Export and verification workflows for externally inspectable evidence.

Compliance-related labels are intentionally phrased as control screenings,
processing-receipt coverage or source-linked findings. They are not legal
opinions or formal compliance certification.

Implementation details:

- [Analyst deployment and API contract](docs/ANALYST_DEPLOYMENT_AND_API.md)
- [Auditor deployment and API contract](docs/AUDITOR_DEPLOYMENT_AND_API.md)
- [Auditor evidence schema](docs/release/auditor-evidence.schema.json)
- [Analyst decision schema](docs/release/analyst-decision.schema.json)

## Evidence and current limits

- [Multilingual evaluation](docs/evaluation/quality.json) contains 108 developer-curated challenge cases across English, Tamil and Telugu. It reports category macro-F1 of 0.9746, urgency accuracy of 86.11% and emergency recall of 33/33 (11 EN, 11 TA, 11 TE). Independent external adjudication remains pending.
- [Baseline comparison](docs/evaluation/baseline-quality.json) records the local keyword fallback on the same challenge set.
- [DPDP architecture](docs/DPDP_COMPLIANCE_ARCHITECTURE.md) documents consent, data-minimisation, configurable public-transparency differential privacy, and ingress-scrubbing design choices. Public transparency defaults to epsilon `0.75` when differential privacy is enabled.
- [Security threat model](docs/SECURITY_THREAT_MODEL.md) describes trust boundaries, token handling, Secret Manager rotation and RBAC controls under the STRIDE framework.
- [External audit anchoring](docs/EXTERNAL_ANCHORING_SPEC.md) specifies Merkle root batching, RFC 3161 trusted timestamps and Sigstore Rekor public transparency log integration.
- [Model evidence dossier](docs/evaluation/MODEL_EVIDENCE_DOSSIER.md) records proxy diagnostics, calibration telemetry and forecasting limitations.
- [Reproducible ML pipeline](scripts/train_stress_model.py) implements reproducible training and verification for the Layer 4 spatial demand stress booster (`model.bst`).
- [Load measurements](docs/evaluation/load.json) cover synthetic rows and local Flask test-client conditions; they exclude network and provider latency.
- [Pilot deployment runbook](docs/MINISTRY_PILOT_DEPLOYMENT_RUNBOOK.md) describes the Cloud SQL, recovery and identity configuration required for an operational pilot.

These artifacts establish implementation evidence and documented limits. They do not establish population-level accuracy, ministry adoption, causal public-service impact or formal certification.

### Public endpoint rate limits

The Public Suite applies per-IP, per-process safeguards. Requests exceeding a
limit receive HTTP `429 Too Many Requests` with a `Retry-After` header.

| Endpoint | Limit |
|---|---:|
| `/api/complaints` | 120 requests/minute/IP |
| `/api/v1/live-feed/stream` | 12 connections/minute/IP |
| `/api/public/transparency/summary` | 60 requests/minute/IP |
| `/api/public/transparency/priority-signals` | 60 requests/minute/IP |
| `/api/public/transparency/districts` | 60 requests/minute/IP |
| `/api/public/transparency/categories` | 60 requests/minute/IP |
| `/api/v1/transparency/verify-chain` | 30 requests/minute/IP |
| `/api/v2/pilot/public-config` | 60 requests/minute/IP |
| `/api/v2/pilot/intake` | 120 requests/minute/IP |
| `/api/v2/pilot/track` | 20 requests/minute/IP |
| `/api/v2/pilot/portal/{translate,classify,transcribe-voice}` | 60 requests/minute/IP per feature |
| `/api/v2/pilot/portal/evidence` | 30 requests/minute/IP |
| `/api/v2/pilot/channels/{channel}/webhook` | 240 requests/minute/IP after signature validation |

These are application-level safeguards maintained in memory by each service
process. They are not a replacement for distributed Cloud Run, API Gateway or
load-balancer quotas. Production deployments should also configure edge-level
rate limiting, abuse detection and monitoring.

## Explore the live workflows

- [Citizen voice intake](https://nexus-visbharath-510474645723.asia-south1.run.app/submit)
- [Ticket journey and submission trace](https://nexus-visbharath-510474645723.asia-south1.run.app/submission)
- [Policy Dashboard](https://nexus-visbharath-510474645723.asia-south1.run.app/dashboard)
- [Pilot Portal](https://nexus-visbharath-510474645723.asia-south1.run.app/pilot)
- [Auditor workspace](https://nexus-visbharath-510474645723.asia-south1.run.app/auditor)
- [AI operation status](https://nexus-visbharath-510474645723.asia-south1.run.app/api/ai/status)
- [Readiness endpoint](https://nexus-visbharath-510474645723.asia-south1.run.app/readyz)

## Run locally

Use Python 3.11. Dependencies are defined in `requirements.txt` and resolved in `requirements.lock`.

```sh
python -m venv .venv
# Activate .venv for your shell
pip install -r requirements.txt
python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload
```

For an isolated offline demonstration:

```sh
NVB_DISABLE_EXTERNAL_SERVICES=1
DEMO_MODE=true
```

Set the variables in your shell or local environment before starting the application. Do not commit `.env` files, credentials or service-account keys.

## Persistence and deployment

SQLite is intended for local development and disposable demonstrations. Cloud Run serving instances require external PostgreSQL for durable operational records. The pilot Terraform and [deployment runbook](docs/MINISTRY_PILOT_DEPLOYMENT_RUNBOOK.md) describe the Cloud SQL, Secret Manager, recovery and identity path. Review the plan, back up existing state and complete restart and two-instance acceptance before promoting traffic.

## Verification

```sh
python -m unittest tests.test_submission_readiness tests.test_ministry_pilot tests.test_analyst_workbench tests.test_auditor_workbench
```

These tests use isolated data and disable external providers. Report local contract results separately from live provider demonstrations.

## License and attribution

Released under the [Apache License 2.0](LICENSE).

Developed by **Dr. Ravikumar Chandrasekaran**, Thanthai Periyar E.V. Ramasamy Government Polytechnic College, Vellore, Tamil Nadu.


