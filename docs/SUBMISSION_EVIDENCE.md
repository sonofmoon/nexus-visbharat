# Submission evidence and validation boundaries

Every successful `/api/submit` and `/api/submit-voice` response includes `submission_evidence`. The same object is stored in the request's `ai_metadata_json` as a provenance record.

| Evidence field | Meaning | Does not prove |
| --- | --- | --- |
| `local_persistence: saved` | The citizen request and consent event committed to the configured operational database. | Cloud replication, service delivery, or a field outcome. |
| `cloud_replication_status` | The observed BigQuery/Pub/Sub dispatch state for that request. `not_configured` means no cloud client was configured; `failed_dead_letter` means a configured dispatch failed and was retained for recovery. | That BigQuery, Pub/Sub, or a downstream worker completed processing unless the status says so. |
| `cloud_validation` | Whether this submission attempted a configured cloud replication path. | Live cloud credentials, production IAM, quota, or end-to-end cloud deployment validation. |
| `field_validation: not_completed` | No site visit, service inspection, beneficiary survey, or independent outcome measurement is attached. | That an intervention was delivered or helped residents. |

Public-reference snapshots keep retrieval and verification separate. `retrieval_verification` can show that a configured API was reached or that a retained snapshot's hash was checked. `publisher_verification` remains `not_independently_verified` and `field_validation` remains `not_completed` until responsible reviewers supply that evidence.

Beneficiary counts are only displayed after an engineering review records a surveyed project count. Portfolio-level unique beneficiaries remain unavailable until overlap across project catchments is measured. The legacy SPS presentation likewise reports `beneficiary_count: null` rather than deriving people or outcomes from a screening score.

## Automated checks

The local regression suite verifies typed and voice persistence, consent recording, idempotent retry, routing, cloud-disabled evidence, sanitized cloud errors, source-snapshot integrity, and the complete synthetic citizen-to-policy pilot workflow. These checks use simulated or mocked AI/cloud providers and synthetic records.

They do not validate production cloud credentials, real provider delivery, authenticated public-source ownership, boundary review, field surveys, engineering costs, service completion, beneficiary deduplication, or causal outcomes. Those require deployment evidence and independent review.
