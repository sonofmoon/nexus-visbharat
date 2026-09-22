# Authenticated Analyst & Auditor Live Acceptance Report

**Date:** 2026-09-22  
**Target Revision:** `nexus-visbharath-00023-cfv` (Cloud Run `asia-south1`)  
**Production URL:** `https://nexus-visbharath-510474645723.asia-south1.run.app`  
**Database Backend:** Managed Cloud SQL PostgreSQL (`nvb-postgres`, Unix domain socket `/cloudsql/nexus-visbharat:asia-south1:nvb-postgres`)  
**Overall Acceptance Result:** **13 / 13 CHECKS PASSED (100% OPERATIONAL)**

---

## 1. Executive Summary & Verification Matrix

All authenticated Analyst and Auditor workflows were executed end-to-end against the live Cloud Run production revision `nexus-visbharath-00023-cfv`. Zero plaintext secrets remain in Cloud Run environment variables; all database credentials, AI keys, and service tokens are securely mounted from Google Cloud Secret Manager.

| # | Check Name | Target Endpoint | HTTP Status | Evidence & Output | Result |
|---|---|---|---|---|---|
| **1** | RBAC: Deny Unauth Analyst | `GET /api/v2/analyst/snapshot` | `401 Unauthorized` | `{"error": "Missing bearer token", "success": false}` | **PASSED** |
| **2** | RBAC: Deny Unauth Auditor | `GET /api/v2/auditor/snapshot` | `401 Unauthorized` | `{"error": "Missing bearer token", "success": false}` | **PASSED** |
| **3** | RBAC: Deny Invalid Token | `GET /api/v2/analyst/snapshot` | `401 Unauthorized` | Invalid bearer token rejected | **PASSED** |
| **4** | RBAC: Deny Privilege Escalation | `POST /api/v2/auditor/cases` | `403 Forbidden` | Analyst token blocked from Auditor-only route | **PASSED** |
| **5** | Analyst: Snapshot Aggregation | `GET /api/v2/analyst/snapshot?limit=10` | `200 OK` | `total_complaints=12,501`, `categories=10` | **PASSED** |
| **6** | Analyst: Multi-Criteria Prioritization | `GET /api/v2/analyst/snapshot?weights=...` | `200 OK` | 6,732 candidates ranked; top project `NVB-P-CD3E6DE867E467D7` (score=41.15) | **PASSED** |
| **7** | Analyst: Budget Allocation | `GET /api/v2/analyst/snapshot?budget_lakh=500` | `200 OK` | 3 projects selected under INR 500L simulation cap | **PASSED** |
| **8** | Analyst: Project Deep-Dive | `GET /api/v2/analyst/projects/NVB-P-CD3E6DE867E467D7` | `200 OK` | Category: Health, District: Alluri Sitharama Raju, 36 requests | **PASSED** |
| **9** | Auditor: Independent Snapshot | `GET /api/v2/auditor/snapshot` | `200 OK` | `total=12,501`, consent receipts recorded (`receipts_recorded`) | **PASSED** |
| **10** | Auditor: Create Review Case | `POST /api/v2/auditor/cases` | `201 Created` | Case ID: `NVB-AUD-20260922171736778795-37045868` | **PASSED** |
| **11** | Auditor: Progress Case Lifecycle | `POST /api/v2/auditor/cases/<id>/actions` | `200 OK` | Transitioned to `investigating` status | **PASSED** |
| **12** | Auditor: Attach Evidence | `POST /api/v2/auditor/projects/<pid>/evidence` | `201 Created` | Evidence ID: `NVB-EV-20260922171737531500-a661d475` | **PASSED** |
| **13** | Auditor: SHA-256 Ledger Audit | `GET /api/v2/auditor/integrity` | `200 OK` | `valid=True`, `head_seq=485`, hash chain fully verified (`36055ef138cc...`) | **PASSED** |

---

## 2. Zero-Trust Security & Secret Manager Audit

### Secret Migration Status
All sensitive credentials have been removed from Cloud Run environment variables and mounted from Google Cloud Secret Manager with strict IAM binding to the Cloud Run compute service account (`510474645723-compute@developer.gserviceaccount.com`).

| Configuration Variable | Secret Manager Name | Version Mounted | Status |
|---|---|---|---|
| `DATABASE_URL` | `nvb-database-url` | `2` (Rotated Cloud SQL URL) | **Verified (Mounted via secretKeyRef)** |
| `GOOGLE_AI_API_KEY` | `nvb-google-ai-api-key` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `GOOGLE_MAPS_API_KEY` | `nvb-google-maps-api-key` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `GOOGLE_TRANSLATE_API_KEY` | `nvb-google-maps-api-key` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `TELEGRAM_BOT_TOKEN` | `nvb-telegram-bot-token` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `TELEGRAM_WEBHOOK_SECRET` | `nvb-telegram-webhook-secret` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `ADMIN_API_TOKEN` | `nvb-admin-api-token` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `ANALYST_API_TOKEN` | `nvb-analyst-api-token` | `latest` | **Verified (Mounted via secretKeyRef)** |
| `AUDITOR_API_TOKEN` | `nvb-auditor-api-token` | `latest` | **Verified (Mounted via secretKeyRef)** |

### Inspection Evidence
Direct inspection of Cloud Run `spec.template.spec.containers[0].env` confirms **ZERO plaintext secrets**:
```json
{
  "name": "DATABASE_URL",
  "valueFrom": {
    "secretKeyRef": {
      "key": "2",
      "name": "nvb-database-url"
    }
  }
}
```

---

## 3. Live Google Cloud Platform Runtime Health

Execution of live probes `/api/ai/status?probe=true` and `/api/db/status` on Cloud Run revision `nexus-visbharath-00023-cfv` confirms operational readiness across all services:

```json
{
  "database": {
    "backend": "postgres",
    "cloud_runtime": true,
    "restart_durability": "external_database",
    "showcase_only": false,
    "operational_ready": true
  },
  "services": {
    "google_ai": "verified",
    "google_bigquery": "verified",
    "google_dialogflow": "verified",
    "google_maps": "verified",
    "google_stt": "verified",
    "google_translation": "verified",
    "google_tts": "verified",
    "google_vertex": "verified"
  }
}
```

---

## 4. Verification Script Execution

The complete suite can be re-run at any time against the production Cloud Run deployment:

```bash
python scripts/run_acceptance_checks.py
```

All 13 acceptance criteria pass with zero errors.
