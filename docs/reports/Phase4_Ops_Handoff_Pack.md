# Phase 4 Ops Handoff Pack

Generated on: 2026-09-08 UTC
Owner: Platform Engineering
Status: Ready for controlled rollout (pending burn-in confirmation)

## 1) Scope (Phase 4 Production Surface)
This pack covers the Phase 4 policy decision lifecycle APIs:
- `POST /api/v1/policy/decisions/draft` (admin/analyst)
- `GET /api/v1/policy/decisions` (admin/analyst/auditor)
- `POST /api/v1/policy/decisions/<decision_id>/approve` (admin)
- `POST /api/v1/policy/decisions/<decision_id>/reject` (admin)

Underlying data model:
- `policy_decisions` table (decision lifecycle persistence)

## 2) Access & Security Model
RBAC expectations:
- `admin`: create draft, list, approve, reject
- `analyst`: create draft, list
- `auditor`: list only

Security controls:
- Bearer token auth required for all Phase 4 endpoints
- Decision transitions restricted to valid state flow (`draft` -> `approved` or `rejected`)
- Lifecycle actions emit audit events:
  - `policy_decisions_draft_created`
  - `policy_decisions_viewed`
  - `policy_decision_approved`
  - `policy_decision_rejected`

## 3) Pre-Deployment Checklist
- [ ] Database bootstrap includes `policy_decisions` table creation
- [ ] Role tokens present for admin/analyst/auditor
- [ ] Phase 4 burn-in script available: `scripts/burnin_phase4_policy_workflow.py`
- [ ] Report path writable: `docs/reports/Phase4_GoLive_Readiness_Report.md`
- [ ] Existing Phase 3 endpoints remain healthy

## 4) Smoke Test Commands (PowerShell)
### Draft creation (analyst)
```powershell
curl -Method POST "http://localhost:5000/api/v1/policy/decisions/draft?limit=3&total_budget_lakh=1100" -H "Authorization: Bearer visbharat-analyst-token"
```

### List decisions (auditor)
```powershell
curl "http://localhost:5000/api/v1/policy/decisions?status=draft&limit=20" -H "Authorization: Bearer visbharat-auditor-token"
```

### Approve decision (admin)
```powershell
curl -Method POST "http://localhost:5000/api/v1/policy/decisions/<decision_id>/approve" -H "Authorization: Bearer visbharat-admin-token"
```

### Reject decision (admin)
```powershell
curl -Method POST "http://localhost:5000/api/v1/policy/decisions/<decision_id>/reject" -H "Authorization: Bearer visbharat-admin-token" -H "Content-Type: application/json" -Body '{"notes":"Deferred due to execution capacity"}'
```

Acceptance criteria:
- All expected authorized calls return HTTP `200`
- Unauthorized or forbidden role checks return expected `401` / `403`
- Invalid lifecycle transition returns `400`

## 5) Monitoring & KPIs (Week-1)
### Service reliability
- `phase4_decision_api_success_rate` target >= 99%
- `phase4_decision_api_p95_latency_ms` target <= 1200 ms
- `phase4_decision_api_5xx_count` target = 0 sustained

### Governance integrity
- `policy_decisions_draft_created` event volume
- `policy_decisions_viewed` event volume
- `policy_decision_approved` and `policy_decision_rejected` trend balance
- `invalid_decision_transition_count` should remain near 0

### Workflow quality
- Draft-to-decision completion ratio within SLA window
- Approval/rejection split tracked weekly for anomaly detection

## 6) Alert Thresholds
- P95 latency > 1500 ms for 15 min -> warning
- Success rate < 98% for 10 min -> critical
- Any sustained 5xx > 5/min for 5 min -> critical
- Missing audit events for successful lifecycle actions -> warning

## 7) Rollout Plan
1. Deploy API version with Phase 4 endpoints enabled.
2. Run Phase 4 smoke tests from this pack.
3. Execute `python scripts/burnin_phase4_policy_workflow.py`.
4. Verify `docs/reports/Phase4_GoLive_Readiness_Report.md` reports all PASS.
5. Enable analyst workflow first, then admin approval actions.
6. Observe KPI dashboard for 24 hours before full sign-off.

## 8) Rollback Plan
Trigger rollback on persistent 5xx, RBAC bypass, or lifecycle corruption.

Steps:
1. Route traffic to prior stable release.
2. Keep DB state unchanged; decision rows are additive and auditable.
3. Re-run smoke tests on rolled-back release.
4. Capture incident report with decision IDs and audit IDs.

## 9) Incident Triage Playbook
1. Confirm endpoint and timeframe impacted.
2. Separate auth/RBAC failures from server/runtime faults.
3. Validate DB write path and state transition query behavior.
4. Inspect audit log continuity for decision actions.
5. Decide fix-forward vs rollback in <= 15 minutes.

## 10) Handoff Checklist (Sign-off)
- [ ] Burn-in report archived (`docs/reports/Phase4_GoLive_Readiness_Report.md`)
- [ ] Ops pack reviewed by deployment owner
- [ ] On-call aware of smoke and rollback commands
- [ ] Token-rotation runbook verified for admin role
- [ ] Week-1 KPI dashboard/alerts configured

## References
- Burn-in script: `scripts/burnin_phase4_policy_workflow.py`
- Go-live report: `docs/reports/Phase4_GoLive_Readiness_Report.md`
- Phase 3 baseline report: `docs/reports/Phase3_GoLive_Readiness_Report.md`
- Full platform docs: `docs/VisBharath_Complete_Documentation.md`
