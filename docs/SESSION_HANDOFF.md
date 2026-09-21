# Session Handoff

## Date
- 2026-09-09

## Current Status
- Layer 2 ASR providers now support full live HTTP request mapping for Bhashini and AI4Bharat adapters.
- `python -m unittest tests.test_phase3_intelligence tests.test_api_contract_and_rbac` -> PASS (102 tests).

## What Was Implemented in This Session
- Production-native live HTTP adapter mapping:
  - `visbharat/services/bhashini_asr.py`
    - live POST request mapping added:
      - base64 audio payload,
      - provider/language config mapping,
      - auth headers (`Authorization`, `x-api-key`),
      - timeout handling,
      - transcript/confidence extraction from multiple provider response shapes,
      - explicit request failure surfacing.
  - `visbharat/services/ai4bharat_asr.py`
    - same production mapping capabilities as above with AI4Bharat-specific response extraction fallbacks.

- Provider runtime controls:
  - `visbharat/config.py`
    - added timeout and strict-mode controls:
      - `BHASHINI_ASR_TIMEOUT_SECONDS`
      - `AI4BHARAT_ASR_TIMEOUT_SECONDS`
      - `LANGUAGE_ASR_STRICT_MODE`
  - `.env.example`
    - added corresponding env examples.

- App initialization wiring:
  - `visbharat/__init__.py`
    - passes timeout config into Bhashini/AI4Bharat client initialization.

- Runtime fallback hardening:
  - `visbharat/blueprints/api.py`
    - `_run_speech_to_text()` now executes ordered provider fallback chain based on `LANGUAGE_ASR_PROVIDER` preference.
    - if live providers fail and strict mode is off, system falls back cleanly (simulation path).
    - strict mode can force hard failure to surface provider issues in production validation.

- Tests added:
  - `tests/test_api_contract_and_rbac.py`
    - `test_bhashini_asr_live_http_mapping` (mocked live HTTP).
    - `test_ai4bharat_asr_live_http_mapping` (mocked live HTTP).
    - `test_submit_voice_uses_bhashini_provider_extension` (ingestion path provider selection).


- Priority 1 Layer-2 observability shipped:
  - Added secured endpoint:
    - `GET /api/v1/language/asr/metrics` (`admin`/`auditor`) in `visbharat/blueprints/api.py`.
  - Added ASR metrics audit logging:
    - action `language_asr_metrics_viewed`.
  - Dashboard ops wiring completed:
    - `static/js/dashboard.js` now fetches ASR metrics alongside delivery/IVR health and renders per-provider success/failure/fallback + p95 latency summary.
    - `templates/dashboard.html` delivery-health panel placeholder updated to include ASR telemetry.
  - Added contract + RBAC + ingest telemetry tests:
    - `test_language_asr_metrics_rbac_and_contract`
    - `test_language_asr_metrics_tracks_voice_ingest_events`


- Highest priority shipped: ASR provider contract matrix + provider-level circuit breaker rollout.
  - Added ASR circuit-breaker controls in config/env:
    - `LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED`
    - `LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD`
    - `LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS`
  - Added provider-level adaptive failover + circuit guard in `visbharat/blueprints/api.py`:
    - tracks per-provider failures/open windows,
    - avoids unhealthy providers during cooldown,
    - auto-resets on successful call,
    - emits circuit state in ASR metrics summary.
  - Added provider contract fixture matrix artifacts:
    - `tests/fixtures/asr_provider_contract_matrix.json`
    - `docs/release/asr_provider_contract_matrix.json`
  - Added tests:
    - `test_asr_provider_contract_fixture_matrix`
    - `test_asr_circuit_breaker_fallback_and_metrics_contract`


- Next priority shipped: admin ASR circuit-reset + manual provider override controls.
  - Added admin/auditor control visibility endpoint:
    - `GET /api/v1/language/asr/control`
  - Added admin provider override endpoint:
    - `POST /api/v1/language/asr/provider-override`
    - supports `mode` (`auto`/`force`), `forced_provider` (`bhashini`/`ai4bharat`/`google`/`simulation`), `bypass_circuit`.
  - Added admin circuit reset endpoint:
    - `POST /api/v1/language/asr/circuit/reset` (single provider or all).
  - Runtime wiring in ASR path now honors override controls and exposes override state in metrics summary.
  - Added API tests for RBAC + contract for control/reset/override endpoints.


- Ops UI wiring completed for ASR admin controls.
  - `static/js/dashboard.js` Delivery Health panel now includes:
    - `GET /api/v1/language/asr/control` state read,
    - override controls (`mode`, `forced_provider`, `bypass_circuit`) via `POST /api/v1/language/asr/provider-override`,
    - circuit reset actions via `POST /api/v1/language/asr/circuit/reset` (selected/all),
    - inline action status feedback and auto-refresh after action.
  - `templates/dashboard.html` placeholder updated to include secure ASR control actions.


- ASR control safety hardening completed.
  - Added reset-all confirmation guard in dashboard control flow.
  - Added role-aware control disabling in dashboard for non-admin tokens (read-only UI state).
  - Extended `GET /api/v1/language/asr/control` payload with authenticated `role` to drive UI gating.


- ASR control observability UX completed.
  - Added secured audit-trail API for ASR control actions:
    - `GET /api/v1/language/asr/control/audit-trail` (`admin`/`auditor`)
    - returns recent `language_asr_override_updated`, `language_asr_circuit_reset`, `language_asr_control_viewed` events.
  - Wired Delivery Health panel to fetch and render recent ASR control audit entries.
  - Added RBAC/contract test coverage for ASR control audit-trail endpoint.


- ASR audit exportability completed.
  - Enhanced `GET /api/v1/language/asr/control/audit-trail` with export/filter support:
    - `format` (`json`/`csv`), `action`, `actor`, `date_from`, `date_to`, `limit`.
  - Added CSV export response with attachment header for compliance/ops evidence download.
  - Wired Delivery Health panel with `Export ASR Audit CSV` action using secure token auth.
  - Added test coverage for CSV export contract and invalid action filter handling.


- ASR audit governance hardening completed.
  - Added signed export metadata for ASR audit exports (JSON + CSV):
    - `exported_by`, `exported_at`, canonical filter set, `digest_sha256`, `signature_hmac_sha256`.
  - CSV exports now include metadata header lines before tabular rows.
  - JSON exports now include `export_metadata` payload for compliance verification.
  - Added `ASR_AUDIT_EXPORT_SIGNING_SECRET` config/env support (fallback to `SECRET_KEY`).


- ASR audit verification tooling completed.
  - Added secured verification endpoint:
    - `POST /api/v1/language/asr/control/audit-trail/verify` (`admin`/`auditor`)
    - verifies `digest_sha256` + `signature_hmac_sha256` against canonical payload.
  - Added verification utility logic in API layer for canonical digest/signature recomputation.
  - Added RBAC + valid/tampered/invalid-payload test coverage for verification flow.


- Layer 3 geocoding hardening completed (partial -> functional in-app).
  - Added PIN geocoding index support (`PIN_GEOCODE_INDEX`).
  - Added ward polygon resolver (`WARD_POLYGONS`) with point-in-polygon mapping.
  - Added SVAMITVA-style village polygon hooks (`SVAMITVA_VILLAGE_MAPS`).
  - Upgraded `/api/v1/geo/ward-suggest` to return full geo context (`ward`, `ward_source`, `lat`, `lng`, `pin`, `village`, `district`).
  - Wired submit flows (`/api/submit`, `/api/submit-voice`) to use enhanced geo context for routing ward resolution and metadata.
  - Added config/env placeholders in `config.py` + `.env.example`.
  - Added API tests for PIN+polygon+SVAMITVA ward resolution and submit-path usage.


- Added Layer 3 geo data-pack artifacts for ops onboarding under `docs/release/`:
  - `pin_geocode_index.schema.json`
  - `ward_polygons.schema.json`
  - `svamitva_village_maps.schema.json`
  - `pin_geocode_index.sample.json`
  - `ward_polygons.sample.json`
  - `svamitva_village_maps.sample.json`
  - `layer3_geo_data_packs.md`


- Layer 4 federated dataset connector + governed-join implementation completed.
  - Added production connector service: `visbharat/services/layer4_fusion.py`.
  - Added source readiness endpoint: `GET /api/v1/layer4/fusion/sources`.
  - Added governed fusion endpoint: `GET /api/v1/layer4/fusion`.
  - Implemented source-by-source ingestion paths for:
    - SECC, Census, NFHS, SDG India Index, NITI MPI,
    - Aspirational Districts, PM Gati Shakti, Budget Outlays.
  - Added config/env paths for all Layer 4 datasets in `config.py` and `.env.example`.
  - Added release sample packs/docs under `docs/release/layer4_*` and `layer4_federated_data_packs.md`.
  - Added API contract test: `test_layer4_fusion_sources_and_join_contract`.


- Layer 5 partial gaps closed to implementation-complete.
  - Added dedicated Demand–Investment geospatial alignment API:
    - `GET /api/v1/policy/alignment-map`
    - includes district geo points, demand count, investment outlay, alignment gap, and explainability payload.
  - Added governed RAG brief API with explicit citation chain:
    - `GET /api/v1/policy/rag-brief`
    - returns `citation_chain.sources[]` with source_id/title/source_type/publisher/jurisdiction/published_at/url/checksum/excerpt/score.
  - Added Layer 5 policy output service:
    - `visbharat/services/policy_outputs.py`
  - Added RAG corpus config and env controls:
    - `L5_RAG_CORPUS_PATH`, `L5_RAG_MAX_CITATIONS`
  - Added sample governed corpus:
    - `docs/release/layer5_rag_corpus.sample.json`
  - Added API contract test:
    - `test_policy_alignment_map_and_rag_brief_contract`

## Next Suggested Priority
1. ASR telemetry retention hardening:
   - persist metrics snapshots for long-window trend analysis (beyond in-memory rolling store).
2. Provider conformance expansion:
   - extend fixture matrix with negative/error payload variants and malformed response guards.
3. ASR verification UX:
   - add dashboard-side verify action that accepts pasted `export_metadata` and renders pass/fail evidence.

## Key Files Touched
- `visbharat/services/bhashini_asr.py`
- `visbharat/services/ai4bharat_asr.py`
- `visbharat/config.py`
- `visbharat/__init__.py`
- `visbharat/blueprints/api.py`
- `.env.example`
- `tests/test_api_contract_and_rbac.py`
- `docs/SESSION_HANDOFF.md`

## Milestone - Trust/Governance Closure (DPDP + PII + DPG)
- Implemented centralized end-to-end PII scrubbing service at `visbharat/services/pii_scrubber.py` and wired ingress paths (`/api/submit`, `/api/submit-voice`, SMS keyword, IVR missed-call, federation publish/batch via normalization + pipeline ingestion).
- Added DPDP governance closure APIs: `GET /api/v1/governance/dpdp/evidence`, `GET /api/v1/governance/dpdp/readiness`; added DPG status API `GET /api/v1/governance/dpg-status`.
- Added governance config/env controls for DPDP evidence artifacts and DPG registration metadata (`visbharat/config.py`, `.env.example`, `.env.production.template`).
- Fixed public transparency payload duplication defects in differential privacy metadata serialization.
- Added contract tests for new governance endpoints and PII scrubbing behavior in `tests/test_api_contract_and_rbac.py`.

### Next Priority
- Implement automated DP budget accounting/rotation for public transparency queries with per-endpoint epsilon ledger and monthly reset policy.
