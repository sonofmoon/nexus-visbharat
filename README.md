# Nexus VisBharat (NVB)

Multilingual citizen reports connected to evidence-linked infrastructure planning, human review, and delivery follow-up.

**Hackathon:** Build with AI: Code for Communities, second edition  
**Track:** AI for Digital Public Infrastructure & Governance ? Innovation  
**Status:** Functioning prototype and ministry-pilot rehearsal; Digital Public Good candidate, not certified or adopted by a ministry.  
**License:** Apache 2.0

## Try the submission

- [Direct Cloud Run showcase](https://nexus-visbharath-510474645723.asia-south1.run.app)
- [Submission evidence](https://nexus-visbharath-510474645723.asia-south1.run.app/submission)
- [Demo video](https://nexus-visbharath-510474645723.asia-south1.run.app/demo-video)
- [Pitch deck](https://nexus-visbharath-510474645723.asia-south1.run.app/pitch-deck)
- [Source](https://github.com/sonofmoon/nexus-visbharat)

The custom domain is awaiting owner configuration. Repository changes are not automatically deployed; runtime endpoints show the deployed revision's actual state.

## The problem and solution

Citizen feedback is fragmented across channels while public officials need to compare local demand, infrastructure gaps and competing investment proposals. NVB connects the following workflow:

**Voice/text/message ? persisted report ? translation and classification ? demand cluster ? evidence-linked proposal ? budget scenario ? engineering and administrative review ? delivery follow-up.**

Gemini supports classification, translation and policy synthesis. Speech-to-Text supports voice intake. Deterministic screening and budget constraints remain explainable; formal project decisions require human review. Immediate hazards use a separate response path.

## Five submission requirements

| Requirement | Implementation and evidence |
|---|---|
| End-to-end flow | Citizen receipts, demand clustering, analyst scenarios, reviewed decisions, independent audit and lifecycle tracking. `/submission` traces an authorized ticket to its actual cluster, candidate and saved decisions. |
| Google AI | Gemini and speech integrations. Persisted operation evidence records model, timestamp, duration and fallback state. A configured client is not shown as verified inference. |
| Realistic data | 12,500 prepared synthetic reports across 97 configured districts. Public-data adapters and bundled indicators carry source limitations. Sample volumes and outcomes are not actual citizens served. |
| Built for India | Demonstration coverage: Tamil Nadu, Andhra Pradesh and Telangana. The Vellore?Tirupati rehearsal exercises two states and district-scoped access. Additional states require boundary, programme, routing and language validation. |
| Multilingual/voice | Tamil, Telugu and English interfaces and processing paths; editable speech transcripts and translations. Real voice and messaging demonstrations must be verified separately from fixtures. |

See [submission acceptance](docs/SUBMISSION_ACCEPTANCE.md) for the exact judge walkthrough and outstanding live checks.

## Evidence and limitations

- [Multilingual Held-Out AI Evaluation](docs/evaluation/quality.json): 108 independent challenge cases across 3 core Indian languages (36 English, 36 Tamil, 36 Telugu) covering 10 civic categories, 3 urgency tiers, and realistic dialectal/emergency boundary cases. Evaluated directly against Google Gemini 2.5/3.6 Flash and Cloud Translation with full confusion matrix and per-language metrics.
- [Baseline comparison](docs/evaluation/baseline-quality.json): local keyword-heuristic fallback on the same 108 challenge cases; demonstrates the massive quality delta over naive non-AI baselines.
- [Review procedure](docs/evaluation/REVIEW_GUIDE.md): speech error rates, translation fidelity, location extraction and duplicate clustering methodology.
- [Engineering lifecycle](docs/DEVELOPMENT_LIFECYCLE.md): multi-contributor domain ownership, trunk-based feature branching, automated PR quality gates, and modular architecture.
- [Local load measurements](docs/evaluation/load.json): 12,500/100,000 synthetic rows, concurrency 1/4, Flask test client and SQLite; excludes network and model latency.
- [Forecasting evidence](docs/evaluation/MODEL_EVIDENCE_DOSSIER.md): proxy diagnostics, not independently validated future-demand prediction.
- [Demo corpus](docs/JURY_DEMO.md): synthetic channel labels and lifecycle events are illustrative, not proof of delivered messages or government impact.

A source-file hash establishes file integrity, not publisher authenticity. Audit chains are tamper-evident, not immutable guarantees. Consent and access controls are engineering features, not legal compliance certification.

## Local setup

Use Python 3.11. Dependencies are managed via semver constraints in `requirements.txt` alongside a fully resolved, hermetic production lockfile in `requirements.lock` matching the Cloud Run container runtime.

```sh
python -m venv .venv
# Activate .venv for your shell
pip install -r requirements.txt
# Copy .env.example to .env and configure local settings
python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload
```

For isolated offline work set `NVB_DISABLE_EXTERNAL_SERVICES=1`, `JURY_REQUIRE_LIVE_MODELS=false`, and `DEMO_MODE=true`. Fallback outputs remain labelled as fallback. Real Google AI requires configured credentials; do not commit `.env` or service-account keys.

- `/submit`: citizen intake
- `/dashboard`: analyst and auditor workflows
- `/submission`: rubric evidence and authorized ticket journey
- `/pilot`: separate ministry rehearsal

The public journey page requires an authorized analyst/auditor/admin token or authenticated session to retrieve citizen details. Existing demo role credentials are for synthetic rehearsal only.

## Persistence and deployment

SQLite is for local development. Cloud Run serving instances require external PostgreSQL unless explicitly configured as disposable demonstrations. `/api/db/status` reports storage posture without exposing credentials; `/readyz` checks database/schema access.

The existing [pilot Terraform](deploy/pilot/) describes Cloud Run, Cloud SQL, regional recovery, managed tasks, private media and identity configuration. Its existence does not prove those resources are deployed. See the [deployment runbook](docs/MINISTRY_PILOT_DEPLOYMENT_RUNBOOK.md).

`scripts/deploy_submission.ps1` prepares a no-traffic revision using an existing Cloud SQL instance and Secret Manager URL. Review its plan, back up/migrate existing state, and complete restart and two-instance acceptance before promoting traffic. New billed infrastructure requires an approved budget. Do not use container-local SQLite for real citizen records.

## Verification

```sh
python -m unittest tests.test_submission_readiness tests.test_ministry_pilot tests.test_analyst_workbench tests.test_auditor_workbench
```

Run PostgreSQL/container contracts with `docker compose -f compose.pilot-postgres.yaml up --build --abort-on-container-exit --exit-code-from verify` on a machine with Docker. Tests use isolated data and disable external providers. Report actual test results separately from live Google AI demonstrations.

## Pilot and impact

The proposed water-services rehearsal covers Vellore and Tirupati in Tamil, Telugu and English. A real pilot requires participating authorities, approved data, assigned officers and operational acceptance. Existing cost figures in the [pilot roadmap](docs/MINISTRY_PILOT_ROADMAP_AND_COST_MODEL.md) are planning assumptions, not current quotes.

Measure officer preparation time, classification corrections, independently assessed proposal quality and service outcomes against a stated baseline. Synthetic before/after counts are not causal impact evidence.

**Author:** Dr. Ravikumar Chandrasekaran, Thanthai Periyar E.V. Ramasamy Government Polytechnic College, Vellore, Tamil Nadu.
