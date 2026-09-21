# Phase 5 Ops Handoff Pack

Generated on: 2026-09-08 UTC
Owner: Platform Engineering
Status: Ready for controlled rollout (pending burn-in confirmation)

## 1) Scope (Phase 5 Production Surface)
This pack covers the Phase 5 public transparency APIs:
- `GET /api/public/transparency/summary`
- `GET /api/public/transparency/districts`
- `GET /api/public/transparency/categories`

Primary objective:
- Publish anonymized, aggregated civic demand analytics for public accountability.

## 2) Access & Security Model
Access model:
- Endpoints are intentionally public (no bearer token required).

Data-protection controls:
- Aggregation-only outputs (no raw complaint text, no submitter identifiers)
- Group suppression via `PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE` (k-anonymity style)
- Response size bound via `PUBLIC_TRANSPARENCY_MAX_LIMIT`

Guardrail expectation:
- Keep `PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE >= 3` in production.

## 3) Pre-Deployment Checklist
- [ ] Phase 5 routes deployed and reachable
- [ ] `PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE` explicitly set in runtime config
- [ ] `PUBLIC_TRANSPARENCY_MAX_LIMIT` set to operationally safe bound
- [ ] Burn-in script available: `scripts/burnin_phase5_transparency.py`
- [ ] Report path writable: `docs/reports/Phase5_GoLive_Readiness_Report.md`

## 4) Smoke Test Commands (PowerShell)
### Public summary
```powershell
curl "http://localhost:5000/api/public/transparency/summary"
```

### Public district aggregates
```powershell
curl "http://localhost:5000/api/public/transparency/districts?limit=20"
```

### Public category aggregates
```powershell
curl "http://localhost:5000/api/public/transparency/categories"
```

### Negative validation probe
```powershell
curl "http://localhost:5000/api/public/transparency/districts?limit=abc"
```

Acceptance criteria:
- Public endpoints return `200` with expected payload fields
- Invalid `limit` returns `400`
- No raw `original_text` or `submitted_by` fields appear in public payloads

## 5) Monitoring & KPIs (Week-1)
### Service reliability
- `phase5_transparency_success_rate` target >= 99.5%
- `phase5_transparency_p95_latency_ms` target <= 800 ms
- `phase5_transparency_5xx_count` target = 0 sustained

### Privacy quality
- `suppressed_groups_ratio` tracked daily
- `published_groups_count` trend monitored for abrupt changes
- `public_payload_sensitive_field_violations` target = 0

### Usage insight
- `transparency_endpoint_qps` by route
- `top_limit_values` distribution for district endpoint

## 6) Alert Thresholds
- P95 latency > 1200 ms for 15 min -> warning
- Success rate < 99% for 10 min -> critical
- Any 5xx > 5/min for 5 min -> critical
- Sensitive field detection > 0 -> critical
- Suppressed-group ratio drops unexpectedly (>40% relative) -> warning

## 7) Rollout Plan
1. Deploy release with Phase 5 routes enabled.
2. Run smoke tests from this pack.
3. Execute `python scripts/burnin_phase5_transparency.py`.
4. Verify `docs/reports/Phase5_GoLive_Readiness_Report.md` reports all PASS.
5. Enable public traffic and observe metrics for 24 hours.

## 8) Rollback Plan
Trigger rollback on repeated 5xx, sensitive-field leakage, or major latency regression.

Steps:
1. Route traffic back to previous stable release.
2. Re-run smoke tests on rolled-back version.
3. Record incident with payload samples and timestamps.
4. Root-cause before reattempting rollout.

## 9) Incident Triage Playbook
1. Confirm impacted endpoint and time window.
2. Separate availability issues from payload-integrity issues.
3. Validate anonymization threshold and grouping queries.
4. Check logs for malformed query parameters and traffic spikes.
5. Decide fix-forward vs rollback in <= 15 minutes.

## 10) Handoff Checklist (Sign-off)
- [ ] Burn-in report archived (`docs/reports/Phase5_GoLive_Readiness_Report.md`)
- [ ] Ops pack reviewed by deployment owner
- [ ] On-call aware of smoke/rollback steps
- [ ] Privacy guardrail values documented in runtime config
- [ ] Week-1 KPI dashboard/alerts configured

## References
- Burn-in script: `scripts/burnin_phase5_transparency.py`
- Burn-in launcher: `scripts/burnin_phase5_transparency.bat`
- Go-live report: `docs/reports/Phase5_GoLive_Readiness_Report.md`
- Full platform docs: `docs/VisBharath_Complete_Documentation.md`
