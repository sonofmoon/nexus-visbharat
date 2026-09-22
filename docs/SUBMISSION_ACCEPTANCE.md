# Submission acceptance: Code for Communities

This checklist separates local contract tests, stored developer evaluations, and actual cloud/provider demonstrations. Passing local tests does not establish live Google AI, message delivery, ministry adoption, or field impact.

## Entry points

- Submission evidence: `/submission`
- Citizen intake: `/submit`
- Scoped rehearsal: `/pilot`
- Policy workflow: `/dashboard`
- Runtime: `/readyz`, `/api/ai/status`, `/api/db/status`

Use the direct Cloud Run URL until the owner configures the custom domain.

## Five mandatory demonstrations

| Requirement | Procedure | Evidence to retain |
|---|---|---|
| End-to-end flow | Submit a fresh, non-emergency water-service report; inspect its journey; find the same candidate in the analyst workspace; draft, review and record a human decision | Ticket, cluster, candidate and decision IDs; the saved decision must include that ticket |
| Google AI | Use previously unseen Tamil/Telugu text; inspect the classification and translation operation results | Served model, trace ID, timestamp, duration, fallback flag and native/translated text; configured status alone fails this gate |
| Realistic data | Use the prepared synthetic corpus; inspect reference-source metadata beside the project | Synthetic label, publisher/source, year, geographic level, verification status and missing fields |
| India reach | Repeat in Vellore (Tamil Nadu) and Tirupati (Andhra Pradesh); sign in as each assigned district officer | Correct routing and language; attempts to retrieve the other district's ticket must be denied |
| Language/voice | Record original Tamil or Telugu speech; inspect/correct transcription before sending; compare location and meaning | Original audio with consent, transcript, correction, translation and STT operation evidence |

The challenge also names messaging apps. Send one report through a configured channel using a controlled test account, retain the provider event ID and receipt, and replay the same event to prove one-ticket idempotency. Do not describe a signed local webhook fixture as an actual delivered message.

Prepared supporting reports may establish a demand cluster. A single new complaint does not prove the need for a capital investment. Emergency deterministic triage is useful but does not, by itself, demonstrate Google AI inference.

## Storage and restart acceptance

1. Provision PostgreSQL/Cloud SQL and a dedicated Secret Manager database URL. Do not commit credentials.
2. Back up the existing database and reconcile its requests, reviews, audit chain and pending jobs before changing the service. The deploy helper does not migrate or delete existing data.
3. Run migrations as a separate job. Disable schema migration and demo seeding in serving containers.
4. Create a controlled test ticket, human review and pending task on the new deployment. Verify records after replacing the revision and through two instances.
5. Verify `/api/db/status` reports PostgreSQL and no credential URL; verify `/readyz`.
6. Retain rollback revision and database backup until parity and restore checks pass.

Cloud Run rejects SQLite by default. `ALLOW_EPHEMERAL_SHOWCASE=true` is available only with `DEMO_MODE=true` for an explicitly disposable showcase. It does not meet durable operational acceptance.

## Truthful evidence

- `/api/ai/status` distinguishes configured, verified, degraded and unavailable operations. Verification expires after 15 minutes by default. It is evidence of execution, not correctness or availability guarantees.
- Provider evidence stores no citizen text, audio, credentials or provider error messages. Ticket metadata links operation traces to authorized requests.
- The stored 27-case AI benchmark is provisional and lacks independent human review. Read current values from `docs/evaluation/quality.json`; do not hard-code them into slides.
- Missing measurements remain unknown. DPG candidacy, source hashes and a tamper-evident ledger are not certification, publisher authentication or immutable storage.
- `docs/evaluation/load.json` is a local SQLite test-client benchmark excluding network/model latency.

## Adding a state

Configure allowed state/district identifiers, verify current administrative boundary crosswalks, map local departments and programmes, add reviewed language examples and consent text, and provision district assignments. Repeat intake, cross-district denial, source matching and proposal review tests. Model language support alone does not establish evaluated rollout.

## Reproduce local contracts

Run `python -m unittest tests.test_submission_readiness tests.test_ministry_pilot tests.test_analyst_workbench tests.test_auditor_workbench` with `NVB_DISABLE_EXTERNAL_SERVICES=1`. PostgreSQL contracts use the disposable database in `compose.pilot-postgres.yaml`. Live provider and cloud checks must be recorded separately.
