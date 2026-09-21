# Phase 3 Ops Handoff Pack

Version: 2026-09-v2
Prepared on: 2026-09-08

## 1) Scope (Phase 3 Production Surface)

### Intelligence endpoints
- `GET /api/v1/intelligence/gap-analysis`
- `GET /api/v1/intelligence/hotspot-predictions`
- `GET /api/v1/intelligence/bigquery-analytics`
- `GET /api/v1/intelligence/vertex-predictions`

### Policy endpoints
- `GET /api/v1/policy/priority-rankings`
- `GET /api/v1/policy/impact-brief`

### Security model
- Bearer token RBAC via `Authorization: Bearer <token>`
- Allowed roles: `admin`, `analyst`, `auditor`
- Audit events emitted for all secure policy/intelligence views

## 2) Deployment Preconditions
- Python environment installed with project dependencies (`pip install -r requirements.txt`)
- DB configured (`DATABASE_URL` for PostgreSQL recommended in production; SQLite fallback for dev)
- Admin/analyst/auditor tokens configured (`ADMIN_API_TOKEN`, `ANALYST_API_TOKEN`, `AUDITOR_API_TOKEN`)
- Phase 1 webhook security config retained for ingestion protection

## 3) Startup / Runtime Commands

### API service
```powershell
python app.py
```

### Optional pipeline worker
```powershell
python scripts/pipeline_worker.py --worker-id worker-a --heartbeat-interval 10
```

### Phase 3 burn-in
```powershell
$env:PYTHONPATH='.'
python scripts/burnin_phase3_policy.py
```

## 4) Smoke Test (Post-deploy)

### Health and auth
```powershell
curl http://localhost:5000/api/health
curl http://localhost:5000/api/v1/auth/me -H "Authorization: Bearer visbharat-admin-token"
```

### Intelligence
```powershell
curl http://localhost:5000/api/v1/intelligence/gap-analysis -H "Authorization: Bearer visbharat-analyst-token"
curl http://localhost:5000/api/v1/intelligence/hotspot-predictions -H "Authorization: Bearer visbharat-analyst-token"
curl http://localhost:5000/api/v1/intelligence/bigquery-analytics -H "Authorization: Bearer visbharat-analyst-token"
curl http://localhost:5000/api/v1/intelligence/vertex-predictions -H "Authorization: Bearer visbharat-analyst-token"
```

### Policy
```powershell
curl "http://localhost:5000/api/v1/policy/priority-rankings?limit=5&total_budget_lakh=1200" -H "Authorization: Bearer visbharat-analyst-token"
curl "http://localhost:5000/api/v1/policy/impact-brief?limit=5&total_budget_lakh=1200" -H "Authorization: Bearer visbharat-analyst-token"
```

Acceptance criteria:
- All calls return HTTP `200` (except expected `401` for unauthenticated checks)
- Intelligence payloads contain required fields (`items`/aggregate slices/risk predictions)
- Policy payload contains required fields (`items`, budget fields, `impact_metrics`, `auto_brief`)

## 5) Monitoring & KPIs (Week-1)

### Service reliability
- `intelligence_endpoint_success_rate` target >= 99%
- `policy_endpoint_success_rate` target >= 99%
- `intelligence_policy_p95_latency_ms` target <= 1200 ms
- `intelligence_policy_5xx_count` target = 0 sustained

### Security & governance
- `unauthorized_intelligence_access_count` (track `401` spikes)
- `unauthorized_policy_access_count` (track `401` spikes)
- `intelligence_*_viewed` audit event volumes
- `policy_priority_rankings_viewed` + `policy_impact_brief_viewed` volumes

### Data quality guardrails
- `funded_count` should increase or stay stable as budget increases
- `budget_used_lakh` should not exceed `total_budget_lakh`
- `funding_coverage_ratio` should remain within `[0,1]`
- Vertex risk-band distribution should not collapse unexpectedly week-over-week

## 6) Alert Thresholds
- P95 latency > 1500 ms for 15 min -> warning
- Success rate < 98% for 10 min -> critical
- Any sustained 5xx > 5/min for 5 min -> critical
- Missing audit events for successful intelligence/policy calls -> warning

## 7) Rollout Plan
1. Deploy API with Phase 3 endpoints enabled.
2. Run smoke tests from this pack.
3. Execute `scripts/burnin_phase3_policy.py` and confirm all checks pass.
4. Enable analyst traffic first, then auditor/admin access.
5. Observe KPIs for 24 hours before full volume sign-off.

## 8) Rollback Plan
- Trigger rollback if: persistent 5xx, incorrect budget/prediction semantics, or security/audit regression.
- Steps:
  1. Route clients back to previous stable release.
  2. Keep DB unchanged (Phase 3 endpoints are read analytics + policy views).
  3. Re-run smoke tests on rolled-back version.
  4. Open incident record with failing payload samples and audit IDs.

## 9) Incident Triage Playbook
1. Confirm endpoint and timeframe impacted.
2. Check auth failures vs server errors.
3. Verify database connectivity and query timings.
4. Validate intelligence/policy response invariants and contract fields.
5. Confirm audit log writes are present.
6. Decide fix-forward vs rollback within 15 minutes.

## 10) Handoff Checklist (Sign-off)
- [ ] Burn-in report archived (`docs/reports/Phase3_GoLive_Readiness_Report.md`)
- [ ] Ops pack reviewed by deployment owner
- [ ] On-call knows smoke/rollback commands
- [ ] Postman collection imported for support team
- [ ] Week-1 KPI dashboard/alerts configured

## References
- Burn-in script: `scripts/burnin_phase3_policy.py`
- Go-live report: `docs/reports/Phase3_GoLive_Readiness_Report.md`
- API collection: `docs/postman/VisBharath_API.postman_collection.json`
- Full platform docs: `docs/VisBharath_Complete_Documentation.md`
