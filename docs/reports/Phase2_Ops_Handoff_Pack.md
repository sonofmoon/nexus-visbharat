# Phase 2 Ops Handoff Pack

Version: 2026-09-v1
Prepared on: 2026-09-08

## 1) Scope (Phase 2 Processing Surface)

### Processing endpoints
- `GET /api/ai/status`
- `POST /api/translate`
- `POST /api/classify`
- `POST /api/submit-voice`
- `POST /api/dialogflow/session`

### Pipeline ops endpoints
- `GET /api/v1/pipeline/jobs`
- `GET /api/v1/pipeline/jobs/<job_id>`
- `GET /api/v1/pipeline/workers`
- `GET /api/v1/pipeline/metrics`
- `POST /api/v1/pipeline/jobs/<job_id>/retry`
- `POST /api/v1/pipeline/jobs/<job_id>/cancel`

## 2) Deployment Preconditions
- Python dependencies installed (`pip install -r requirements.txt`)
- DB configured and writable
- Role tokens configured (`ADMIN_API_TOKEN`, `ANALYST_API_TOKEN`, `AUDITOR_API_TOKEN`)

## 3) Startup / Runtime Commands

### API service
```powershell
python app.py
```

### Optional worker
```powershell
python scripts/pipeline_worker.py --worker-id worker-a --heartbeat-interval 10
```

### Phase 2 burn-in
```powershell
$env:PYTHONPATH='.'
python scripts/burnin_phase2_processing.py
```

Windows launcher:
```bat
scripts\burnin_phase2_processing.bat
```

## 4) Smoke Test (Post-deploy)

### Processing
```powershell
curl http://localhost:5000/api/ai/status
curl -Method POST http://localhost:5000/api/translate -H "Content-Type: application/json" -Body '{"text":"No water","source_lang":"en","target_lang":"en"}'
curl -Method POST http://localhost:5000/api/classify -H "Content-Type: application/json" -Body '{"text":"Road collapsed","language":"en"}'
curl -Method POST http://localhost:5000/api/dialogflow/session -H "Content-Type: application/json" -Body '{"session_state":"collect_issue","message":"Drainage overflow in ward","language":"en","channel":"web"}'
```

### Pipeline secure probes
```powershell
curl http://localhost:5000/api/v1/pipeline/jobs -H "Authorization: Bearer visbharat-analyst-token"
curl http://localhost:5000/api/v1/pipeline/metrics -H "Authorization: Bearer visbharat-admin-token"
```

## 5) Monitoring & KPIs (Week-1)
- `processing_endpoint_success_rate` >= 99%
- `processing_endpoint_p95_latency_ms` <= 1000 ms
- `pipeline_endpoint_success_rate` >= 99%
- `pipeline_5xx_count` = 0 sustained

## 6) Handoff Checklist
- [ ] Burn-in report archived (`docs/reports/Phase2_GoLive_Readiness_Report.md`)
- [ ] Ops pack reviewed by deployment owner
- [ ] On-call knows smoke + rollback commands

## References
- Burn-in script: `scripts/burnin_phase2_processing.py`
- Burn-in launcher: `scripts/burnin_phase2_processing.bat`
- Go-live report: `docs/reports/Phase2_GoLive_Readiness_Report.md`
