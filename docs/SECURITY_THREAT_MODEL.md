# STRIDE Security Threat Model: Nexus-Visbharath
**Document ID:** SEC-STRIDE-2026-V1  
**Status:** Approved Security Architecture Specification  
**Classification:** Public Security Documentation  
**Methodology:** Microsoft STRIDE Threat Modeling Framework

---

## 1. System Scope & Architecture Overview

Nexus-Visbharath (NVB) provides civic intake, multilingual AI triage, spatial demand forecasting, and resource allocation across southern Indian municipalities. Because the platform processes sensitive citizen grievances (which may include location details, infrastructure failures, and emergency reports), it requires a rigorous threat analysis to protect citizen privacy, maintain administrative integrity, and defend against malicious actors.

### 1.1 Trust Boundaries & Data Flow Diagram (DFD)

```
[ UNTRUSTED ZONE: PUBLIC INTERNET ]
     |
     | HTTPS / TLS 1.3
     v
+-------------------------------------------------------------------------+
| TRUST BOUNDARY 1: Web Ingress & Gateway (Cloud Run / Cloud Armor)       |
| - Rate Limiter (Flask-Limiter / Token Bucket per IP & Phone)            |
| - WAF / Input Sanitization & Payload Size Caps (Max 32KB)              |
+-------------------------------------------------------------------------+
     |
     v
+-------------------------------------------------------------------------+
| TRUST BOUNDARY 2: Core Application Services (Flask Backend)             |
| - RBAC Guard (@require_roles: Citizen, Analyst, Auditor, Admin)         |
| - Ingress PII Scrubber (Regex + NER Masking)                            |
| - HMAC/PBKDF2 Session Security (visbharat/security.py)                  |
+-------------------------------------------------------------------------+
     |                         |                         |
     | Sanitized Prompts       | SQL Transactions        | SHA-256 Hashes
     v                         v                         v
+------------------+  +-------------------+  +----------------------------+
| TRUST BOUNDARY 3 |  | TRUST BOUNDARY 4  |  | TRUST BOUNDARY 5           |
| External AI APIs |  | Relational Store  |  | Cryptographic Audit Ledger |
| (Vertex AI /     |  | (Cloud SQL /      |  | (Append-Only SHA-256       |
|  Gemini 3.6)     |  |  SQLite Local)    |  |  Merkle Tree Chaining)     |
+------------------+  +-------------------+  +----------------------------+
```

---

## 2. STRIDE Threat Analysis Matrix

| STRIDE Category | Threat Scenario | Attack Vector & Impact | Codebase Controls & Mitigations | Residual Risk & Posture |
| :--- | :--- | :--- | :--- | :--- |
| **Spoofing Identity** | Attacker impersonates municipal analyst or state auditor to approve fake resource allocations. | Session hijacking or forged JWT/session cookie; unauthorized administrative action. | - PBKDF2/SHA-256 token hashing in `visbharat/security.py`<br>- Ephemeral cryptographically signed session tokens<br>- Production secrets managed via Google Secret Manager (zero hardcoded secrets). | **Low**: Session tokens are cryptographically signed with high-entropy secret; tokens expire after inactivity. |
| **Spoofing Identity** | Sybil attack submitting thousands of automated fake grievances. | Botnets submitting automated complaint forms to skew municipal resource allocation. | - IP and phone rate-limiting via reverse proxy/Flask-Limiter<br>- OTP verification on citizen intake<br>- CAPTCHA / Proof-of-Work thresholding. | **Low**: Rate-limiting and intake throttles prevent automated flooding. |
| **Tampering with Data** | Malicious insider or compromised analyst modifies audit logs or changes grievance priority to hide civic negligence. | Direct SQL update on database to alter historical complaint triage records. | - SHA-256 chained audit ledger (`visbharat/audit.py`)<br>- Each block computes `hash = SHA256(prev_hash + actor + action + timestamp + payload_hash)`<br>- Tamper-evident verification endpoint fails if any historical row is modified. | **Negligible**: Cryptographic chaining detects any retroactive modification instantly. |
| **Repudiation** | Civic official denies rejecting an emergency grievance or reallocating budget away from critical repair. | Administrative officer claims their account was misused or that no allocation order was issued. | - Non-repudiation ledger records actor ID, authenticated role, client IP, action, timestamp, and signed hash<br>- Audit trail exported for third-party RFC 3161 verification. | **Negligible**: Cryptographically signed audit trail provides non-repudiation evidence. |
| **Information Disclosure** | Adversary intercepts citizen PII (Aadhaar number, phone number, residential address) from public grievance data or LLM prompt leaks. | Citizen PII leaked via API responses, model training pipelines, or public dashboards. | - Ingress PII scrubber (`visbharat/services/pii_scrubber.py`) strips Aadhaar, phone, PAN, and voter IDs in-process prior to storage or inference<br>- LLM prompts receive only scrubbed text<br>- Spatial analytics use Laplace Differential Privacy ($\epsilon = 1.0$). | **Negligible**: Unscrubbed PII is never stored or transmitted to LLM inference pipelines. |
| **Denial of Service** | Adversary spams high-cost LLM triage requests to exhaust Google Cloud Vertex AI quota and budget. | Automated script sends complex text to `/api/triage` repeatedly. | - Fast deterministic heuristic pre-filter before calling Gemini 3.6 Flash<br>- Cloud Armor / Flask rate-limiting per IP/User<br>- Strict payload size caps (32KB max per request). | **Low**: Deterministic caching and rate caps prevent upstream quota exhaustion. |
| **Elevation of Privilege** | Citizen user alters request parameters to access municipal analyst workbench or auditor ledger. | Parameter tampering on REST endpoints (e.g. manipulating `/api/analyst/*` headers). | - Strict server-side `@require_roles(['analyst', 'admin'])` decorators in `visbharat/blueprints/auth.py`<br>- Session role claims validated against server session state, not client cookies. | **Negligible**: Client cannot elevate privileges without cryptographic session authorization. |

---

## 3. Detailed Component Security Controls

### 3.1 Authentication & Session Management
- **Token Security:** Sessions use SHA-256 token hashing with high-entropy cryptographic salts (`secrets.token_hex(32)`).
- **Session Expiration:** Standard session timeout enforced; token rotation on privilege change.
- **Zero Hardcoded Secrets:** Application strictly reads `SECRET_KEY`, `GEMINI_API_KEY`, and database credentials from Google Cloud Secret Manager or environment variables at startup. If missing in production mode, the application halts immediately with an explicit error.

### 3.2 Ingress PII Scrubbing
- **Pipeline Order:** Citizen Raw Text $\rightarrow$ `pii_scrubber.scrub_text()` $\rightarrow$ Sanitized Text $\rightarrow$ Database Storage $\rightarrow$ Gemini 3.6 Flash Prompt.
- **Verification:** Aadhaar matches are verified against the Verhoeff algorithm to prevent false positives while ensuring 100% redaction of valid national IDs.

### 3.3 Audit Ledger & Non-Repudiation
- **Linear Hash Chaining:**
  $$H_i = \text{SHA-256}(H_{i-1} \,\|\, t_i \,\|\, \text{actor}_i \,\|\, \text{action}_i \,\|\, \text{SHA-256}(\text{payload}_i))$$
- **Integrity Verification:** The `/api/audit/verify` endpoint recomputes the entire hash chain from the genesis block ($H_0$). Any altered, deleted, or inserted record causes immediate chain invalidation and triggers security alerts.

---

## 4. Threat Model Maintenance & Continuous Verification

1. **Automated Security Regression Tests:**
   - `tests/test_api_contract_and_rbac.py` tests unauthorized role escalation, unauthenticated access, and privilege isolation.
   - `tests/test_audit_chain_and_evidence.py` tests ledger tampering detection by intentionally corrupting block hashes.
2. **Review Cadence:** Threat model reviewed annually or upon any architectural modification to ingress routes, ML inference pipelines, or data storage layers.
