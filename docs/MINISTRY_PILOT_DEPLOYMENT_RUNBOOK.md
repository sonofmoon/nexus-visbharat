# Vellore–Tirupati pilot deployment and recovery

Implementation authorised on 19 September 2026. This package prepares a controlled water-service pilot in Tamil, Telugu and English. Cloud provisioning, spending, ministry outreach and real citizen onboarding have not been performed. The working localhost rehearsal is separate from the original 12,500-record demonstration database.

## Local demonstration

```powershell
python scripts/run_ministry_pilot.py --port 5001
```

Open http://127.0.0.1:5001/pilot. Select a programme role or Vellore/Tirupati district officer. The initial dataset has 63 explicitly synthetic water-service reports, 32 from Vellore and 31 from Tirupati. Six catchments are proposed demonstration groups, not officially selected wards or panchayats. Tickets use `NVB-YYYYMMDDXXXX`; `XXXX` is four uppercase hexadecimal characters, checked for uniqueness. The UTC intake date supplies `YYYYMMDD`.

The SQL outbox commits with the ticket before any model call. On this local deployment, Google model calls are disabled. Processing moves a new report to manual review, where an officer can record a reviewed transcript, English translation and classification. The provider state remains visible. A high model confidence value is not an accuracy measurement.

## Deployable architecture

`deploy/pilot/` defines a Mumbai Cloud SQL PostgreSQL 16 primary with regional HA, private networking, a Delhi asynchronous SQL replica, Mumbai web and authenticated worker services, a Delhi cold standby service, Cloud Tasks, Scheduler, India-replicated secrets, paired private audio buckets, a regional BigQuery table and INR budget alerts. The same application image serves both roles; only the task service account can invoke the private worker. One operational programme uses one isolated database and deployment.

The application has an OIDC code/PKCE login, explicit local identity mapping and programme/district membership, session expiry and CSRF protection. Provision real officers; demo role credentials are disabled in operational mode. State/district filters are checked on the server in the pilot, execution table, Analyst and Auditor interfaces.

The Delhi service has no public invoker grant and no dispatch queue. It connects to the read replica while dormant. It becomes an operational service only after the old writer is fenced and the replica is promoted. This is a prepared recovery path, not active-active operation or a completed recovery drill.

## Staged release

1. Confirm the sponsoring authority, participating bodies, two-state processing basis, exact communities and LGD boundary crosswalks. Current district names alone are insufficient for joining Census 2011 indicators: Vellore and Tirupati boundaries have changed. Retain publisher release, observation year, redistribution terms and source checksum. A checksum never certifies a publisher.
2. Build the image from the repository Dockerfile; run the PostgreSQL contract job in `.github/workflows/ministry-pilot.yml`. Pin the resulting digest and scan dependencies/image using the ministry's approved tooling. Neither a Cloud Run image nor a Docker build was produced by the local rehearsal.
3. Replace `terraform.tfvars.example` placeholders with the approved project, billing account, image digest and IdP endpoints. Configure an organisation-managed Terraform backend with locking and restricted state access. Never commit passwords, `.tfvars`, state or service-account keys. Keep `enable_runtime=false`, `enable_dispatch=false`, `public_intake=false` initially.
4. Run `terraform init`, `terraform validate`, then produce a saved plan. Verify regional service/SKU availability, IAM, replica costs, quotas and the dated calculator export. Apply only with the budget owner's spending authority. Creating HA SQL plus a replica incurs continuing charges even when Cloud Run scales to zero.
5. Provision separate runtime and migration SQL users and add secret versions for the runtime database URL, migration database URL, session key, OIDC client secret and recovery database URL. The migration service account can read the DDL credential; the runtime service account cannot. Do this through the approved secret-handling workflow; do not paste secrets into command logs or the browser. SQL passwords must be URL-encoded. The runtime connects using the `/cloudsql/PROJECT:REGION:INSTANCE` socket and a private VPC path. Verify database connectivity before migration.
6. Execute the `nvb-pilot-migrate` Cloud Run job and inspect its exit status. Job creation alone does not execute a migration. The runtime does not create tables or seed demonstration users. All application migrations are additive/idempotent; the runtime database account should have DML access, while the migration job uses a separately controlled DDL-capable credential in the ministry deployment.
7. Using an approved one-off job with the same image and operational configuration, run `python -m flask --app app pilot-bootstrap --operational`. It refuses a database containing any programme or citizen records. It creates an empty preparing programme with intake closed. Repeating bootstrap is intentionally rejected; do not reset an existing programme.
8. Provision named identities with `pilot-provision-identity --subject ISSUER_SUBJECT --name DISPLAY_NAME --role admin|analyst|auditor [--state STATE --district DISTRICT]`. Configure at least one programme administrator, district reviewers and a separate auditor. The subject must be the IdP's stable subject claim, not a guessed email. Generated legacy credentials are never returned. Assignment changes/revocations are audited in Programme setup.
9. Set `enable_runtime=true` and keep public intake closed. Verify `/health/ready`, IdP redirect/audience/issuer/nonce, session expiry, role separation, district isolation, frozen export restrictions, private audio and manual review. The Admin interface edits official community information before reports exist; historical catchments cannot be overwritten through this form.
10. Confirm the approved Vertex model is supported in Mumbai. Set `USE_REAL_GOOGLE_AI=true` and `PILOT_MODEL_CALLS=true` only for the authorised provider evaluation. `PILOT_MODEL_NAME` defaults to `gemini-2.5-flash`, whose region/SKU/pricing must be checked. Each pilot provider step makes one HTTP attempt. A timeout or unknown result is held for review; no automatic model retry/fallback is billed. Translation and classification are separate model calls with returned usage and model provenance retained.
11. Enable Speech v2 only after verifying Tamil, Telugu, English, chosen model and decoding support in the declared India region. Set `PILOT_SPEECH_LOCATION` and `PILOT_SPEECH_MODEL` accordingly. Unsupported/unavailable speech requires manual transcription. The deployment does not silently use a global speech endpoint. Run the reviewed language/audio evaluation described below.
12. Enable the scheduler (`enable_dispatch=true`) after its OIDC/IAM invocation is tested. It dispatches SQL outbox jobs to Cloud Tasks and recovers expired leases. Labelled Auditor evaluations use the same authenticated dispatcher in operational mode; they calculate metrics on supplied predictions without billing a model. Enable `PILOT_ANALYTICS_SYNC=true` after checking the dedicated regional table. BigQuery receives versioned, redacted summaries, never citizen text or audio. It is not the ticket source of truth.
13. Record all ten launch gates with evidence and reviewers. Each location needs reviewed official geography. Set the correct routing departments and a proposed start date. In an operational deployment only, `pilot-open --authority-reference HTTPS_REFERENCE` checks the gates, named roles and locations before opening intake. Synthetic evidence cannot be accepted as operational authorisation. Configure the approved HTTPS domain, network access policy and public route only after this gate.

The prototype exposes web text, browser voice and an authorised replay-aware agency import. Imports accept up to 100 records, require an enrolled location, language, text/audio, explicit consent and stable `source_id`, and return per-record outcomes. Existing real WhatsApp/SMS/IVR connectors must have a programme-scoped adapter and provider approval before being exposed; legacy unscoped ingestion endpoints are blocked in this deployment. A live messaging rollout is not claimed.

## Cost controls and measurements

The existing conservative planning model reserves approximately ₹123,486/month before tax, including 25% contingency, or ₹145,713 with illustrative 18% tax. The UI starts at a ₹150,000 monthly allowance and 10,000 reports/month. These are assumptions, not measured charges or a supplier quote. The original per-character Translation API allowance remains a conservative placeholder; this implementation uses Vertex translation, so replace that line with observed translation tokens and the selected model's rates before procurement. Human review, district visits, surveys and engineering work are separate costs.

Implemented controls: bounded intake length/audio size and monthly admission count; max three web and two worker instances; task rate/concurrency limits; completed provider-step reuse; no automatic rebilling after an ambiguous call; BigQuery maximum bytes per query; regional tables; 30-day audio object lifecycle; private SQL audit logs; and 50/80/100% budget alerts. Set named alert recipients in the billing account and verify delivery. Budget alerts are not hard spending caps. The Terraform sample reserves platform components; it does not replace organisation quotas, billing export or finance acceptance.

The readiness view shows actual stored step counts/status and mean provider latency. Actual INR spend remains unavailable until billing is connected. Mean latency is not p95, and local page timing does not prove 4G/cloud capacity. Cloud load tests must include cold starts, 50 concurrent sessions, long/invalid audio, retries, multiple workers and dependency failures. Report p50/p95/p99, valid-admission denominator, queue age and manual-review share. Stop admission expansion if reviewers cannot service the queue.

Audio stays in private SQL until the worker verifies both regional bucket copies. It then retains a digest, generation and private object reference. Officers retrieve retained audio through an authenticated, geography-scoped no-store route; public signed audio URLs are not issued. The 30-day object lifecycle is a proposed default requiring the processing steward's retention approval. Restriction/deletion requests also need a documented decision covering model-result caches, SQL backups, bucket soft-delete retention and already-exported files; restriction is not proof of physical erasure from all copies.

## Backup, failover and rollback exercise

Do this in an isolated cloud rehearsal first. Record the operator, UTC timeline, original audit head, last admitted ticket, row counts, replica lag, recovery point and a separately held evidence manifest.

1. Stop ingress/admission and pause Scheduler/Cloud Tasks dispatch. Revoke the old runtime's write access or fence its network/database connections; draining traffic alone does not fence a writer. Preserve the incident log. Do not let two primaries accept reports.
2. Record replica lag and compare it with the proposed RPO ≤15 minutes. If the recovery point is older, declare the potentially missing acknowledged-ticket window. Do not report zero loss merely because the replica promoted.
3. Promote Delhi under the incident commander's authority. Update the recovery SQL secret/socket, approved DNS/HTTPS routing and region configuration. Keep model calls disabled if the selected model has not been approved in Delhi. Enable the standby service only after write and identity tests succeed.
4. Create/repoint a Delhi worker queue and authenticated dispatcher using the same validated definitions with Delhi locations. No queue replication is assumed. Replay eligible SQL outbox rows; idempotent ticket/source IDs, provider-step state and versioned analytics MERGE protect the retained work. Ambiguous provider calls remain manual review. Never clear `pilot_provider_steps` to force retries.
5. Reconcile retained tickets, review versions, decision IDs, consent restrictions, private audio checksums and the protected audit chain against the independent checkpoint. Test both district accounts and duplicate imports. Rebuild BigQuery from SQL if necessary. Restore audio from the paired Delhi bucket through an approved reference migration when Mumbai is unavailable.
6. Time recovery from declared incident to verified service availability. The proposed RTO ≤60 minutes and RPO ≤15 minutes are acceptance targets until a recorded drill proves them. Re-establish replication before resuming ordinary changes. Update Terraform state/configuration deliberately after promotion; do not apply a stale plan that tries to restore the old topology.

For application rollback, retain the previous immutable image digest and shift traffic back only after checking schema compatibility. Do not undo additive schema changes or restore over newer reports as a routine rollback. For corrupted data, restore a backup into a separate instance, reconcile the outage window and obtain authority for cutover. Preserve the original instance/evidence until retention decisions are made.

## Four-week opening and six-week evaluation

| Week | Owner | Deliverable and exit evidence |
|---|---|---|
| 1 | Sponsor, data steward, platform lead | Authority/processing agreement; six actual communities; LGD crosswalks; priced cloud plan; migrated isolated database; manual handling baseline |
| 2 | District nodal officers and technical lead | Named IdP users; district routing; replay-aware intake/import; private audio; source and cost evidence attached to proposals |
| 3 | Evaluation lead and independent auditor | Reviewed 900-item set (300/language); language/urgency and noisy-voice evaluation; access tests; load/replay/restore drill; operator training |
| 4 | Sponsor and both district owners | Evidence-backed go/no-go; controlled cohort only; daily backlog, failure, restriction and cost review |
| 5–6 | Operations, finance and audit leads | Observed review time and routing quality; cloud cost reconciliation; unresolved findings; week-six expand/correct/hold decision |

The four-week clock begins after authority, cloud/identity access and data prerequisites exist. This software does not shorten procurement by assumption. A delay preserves the synthetic/staff rehearsal and shifts real opening.

Language acceptance targets remain provisional: macro-F1 ≥0.85 per language, urgency recall ≥0.95, WER ≤25% plus critical service/location extraction checks. Use held-out, double-reviewed examples with noise/device variation and confidence intervals; separately disclose synthetic labels and real field observations. The existing Auditor evaluator handles batches of up to 500 items, so evaluate three 300-item language batches and compare all subgroups.

The week-six pack includes sample counts, unresolved-case age, actual successful/failed processing costs, all proposal evidence, independent findings, measured RPO/RTO and a proposed 90-day service-outcome follow-up. Report any reduction in officer preparation time against comparable timed baseline tasks. Two weeks of use cannot establish national reach, annual availability, causal infrastructure impact or an award outcome.
