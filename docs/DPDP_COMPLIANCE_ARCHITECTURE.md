# DPDP Act 2023 Compliance Architecture: Nexus-Visbharath
**Document ID:** ARCH-DPDP-2026-V1  
**Status:** Approved Production Architecture  
**Classification:** Public Civic Architecture & Governance  
**Regulatory Anchor:** Digital Personal Data Protection Act, 2023 (Act No. 22 of 2023, Republic of India)

---

## 1. Executive Summary & Regulatory Classification

Nexus-Visbharath (NVB) operates as an AI-orchestrated public grievance triage and spatial allocation infrastructure across 97 southern Indian municipal jurisdictions spanning Tamil Nadu, Andhra Pradesh, Telangana, Karnataka, and Kerala. Under the **Digital Personal Data Protection Act, 2023 (DPDP Act 2023)**:

- **Data Fiduciary:** Municipal administrative entities, state urban development directorates, and the Nexus-Visbharath civic operating framework.
- **Data Principal:** Citizens residing within Indian municipal territories who submit public grievance reports or whose locality telemetry is processed.
- **Consent Manager:** Integrated cryptographically verified consent gateway enforcing purpose-limited data lifecycle controls.

```
+-----------------------------------------------------------------------------------+
|                            CITIZEN INGRESS BOUNDARY                               |
|   Multilingual Notice & Purpose-Limited Consent (Sec 5 & 6)                       |
|   English | Tamil (தமிழ்) | Telugu (తెలుగు)                                        |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                        INGRESS PII SCRUBBING ENGINE                               |
|   Regex + Multilingual NER Redaction:                                             |
|   - UIDAI Aadhaar (12 digits, Verhoeff validated) -> [REDACTED_AADHAAR]           |
|   - Indian Mobile Numbers (+91 / 10 digits)       -> [REDACTED_PHONE]             |
|   - PAN Cards (10-char alphanumeric)              -> [REDACTED_PAN]               |
|   - Voter IDs (EPIC 3-alpha + 7-num)              -> [REDACTED_VOTER_ID]          |
|   - Personal Names & Contact Signatures           -> [CITIZEN_NAME]               |
|   - High-Precision GPS (<10m)                     -> Ward Centroid Centering      |
+-----------------------------------------------------------------------------------+
                                         |
                         +---------------+---------------+
                         |                               |
                         v                               v
+------------------------------------+   +------------------------------------+
|     SUBSYSTEM A: TRIAGE PIPELINE   |   |     SUBSYSTEM B: SPATIAL DEMAND    |
| - LLM Triage (Gemini 3.6 Flash)    |   | - Aggregated Ward-Level Stress     |
| - Zero PII in Prompt Payloads      |   | - Laplace Differential Privacy     |
| - Transient Context (No Retention) |   |   (ε = 1.0) on Civic Counts        |
+------------------------------------+   +------------------------------------+
                         |                               |
                         +---------------+---------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
|                       IMMUTABLE AUDIT & ACCESS BOUNDARY                           |
| - Role-Based Access Control (RBAC): Citizen / Municipal Analyst / State Auditor   |
| - SHA-256 Tamper-Evident Ledger for all access, review, and modification events   |
| - Right to Correction / Erasure tombstoning workflows (Sec 12)                    |
| - Cryptographic Merkle Root Batching for External Anchoring Verification          |
+-----------------------------------------------------------------------------------+
```

---

## 2. Statutory Alignment Matrix

The following matrix details how technical controls within Nexus-Visbharath satisfy statutory mandates under the DPDP Act 2023:

| DPDP Act Section | Statutory Obligation | Technical Implementation in Nexus-Visbharath | Verification & Enforcement Artifact |
| :--- | :--- | :--- | :--- |
| **Section 5** | Notice of Processing | Multilingual notice presented prior to grievance submission in English, Tamil, and Telugu detailing specific processing purposes and retention terms. | `apps/nvb/index.html` Consent Modal & Form Headers |
| **Section 6** | Consent of Data Principal | Granular, revocable opt-in for triage routing vs. analytical aggregation. Complainants may revoke consent, triggering PII tombstoning. | `/api/citizen/consent` endpoint |
| **Section 7** | Certain Legitimate Uses | Public emergency grievance escalation (life/safety hazards such as open electrical lines or collapsed drains) routed under Sec 7(b) state response exemption. | `visbharat/blueprints/api.py` triage route |
| **Section 8(1)** | General Obligations of Fiduciary | Platform ensures accuracy and completeness of citizen grievance data before municipal allocation. | Dual-model triage adjudication & human override |
| **Section 8(5)** | Protection of Personal Data | Ingress PII scrubber executes in-process before database write. Zero raw Aadhaar or phone numbers stored in unencrypted form. | `visbharat/services/pii_scrubber.py` |
| **Section 8(6)** | Personal Data Breach Notification | Real-time security event telemetry logged to Cloud Logging and audited against Data Protection Board (DPB) SLAs. | `visbharat/audit.py` security events |
| **Section 8(7)** | Storage Limitation & Erasure | Automated retention limit (180 days post-resolution) followed by cryptographic zeroization of personal identifiers. | Data lifecycle retention policies |
| **Section 9** | Processing of Children's Data | Age verification gatekeeper. Parental/guardian consent required for minors; zero behavioral profiling or targeted tracking. | Form validation gatekeeper |
| **Section 11** | Right to Access Information | Citizen Portal provides self-service summary of all complaints filed, processing history, and sharing metadata. | Citizen Tracking Dashboard |
| **Section 12** | Right to Correction and Erasure | Citizens may rectify erroneous grievance details or request erasure of resolved complaints via verified OTP. | `visbharat/blueprints/api.py` `/api/complaint/erase` |
| **Section 13** | Grievance Redressal Mechanism | Built-in Data Protection Officer (DPO) escalation channel with statutory 72-hour acknowledgment timer. | `docs/DPDP_COMPLIANCE_ARCHITECTURE.md` |

---

## 3. Ingress PII Scrubbing Engine

Before any citizen text is stored in SQLite/Cloud SQL or forwarded to the Gemini 3.6 Flash triage pipeline, it passes through `visbharat/services/pii_scrubber.py`.

### 3.1 Pattern Redaction Specifications
- **UIDAI Aadhaar:** 12-digit Indian national identity numbers matching `\b\d{4}[\s-]?\d{4}[\s-]?\d{4}\b` are replaced with `[REDACTED_AADHAAR]`. Verified against Verhoeff checksum algorithm where applicable.
- **Indian Mobile Numbers:** 10-digit mobile phone numbers prefixed by `+91`, `0`, or bare `[6-9]\d{9}` are replaced with `[REDACTED_PHONE]`.
- **Permanent Account Number (PAN):** 10-character alphanumeric sequences matching `[A-Z]{5}[0-9]{4}[A-Z]{1}` are replaced with `[REDACTED_PAN]`.
- **Voter ID (EPIC):** Standard 3-letter prefix followed by 7-digit identifier `[A-Z]{3}[0-9]{7}` replaced with `[REDACTED_VOTER_ID]`.
- **Named Entity Redaction:** High-confidence personal names extracted via lightweight multilingual regex-NER models are tokenized and masked as `[CITIZEN_NAME]`.

### 3.2 Geospatial Obfuscation
Citizen street addresses and high-resolution GPS coordinates ($< 10\text{m}$ precision) are mapped to municipal ward centroids ($> 500\text{m}$ resolution) before analytical dissemination, preventing pinpoint residential tracking.

---

## 4. Differential Privacy in Spatial Analytics ($\epsilon = 1.0$)

For municipal resource allocation and demand forecasting (Subsystem B), citizen complaint counts are aggregated at the ward level and protected using the **Laplace Mechanism**:

$$M(D) = f(D) + \text{Lap}\left(\frac{\Delta f}{\epsilon}\right)$$

Where:
- $f(D)$ is the true count of grievances in ward $w$ within time window $t$.
- $\Delta f = 1$ is the $L_1$ global sensitivity (a single citizen filing or withdrawing a grievance changes the count by at most 1).
- $\epsilon = 1.0$ is the privacy budget per municipal reporting epoch.
- Scale parameter $b = \frac{\Delta f}{\epsilon} = 1.0$.

Noise is drawn from the Laplace distribution:

$$P(X = x) = \frac{1}{2b} \exp\left(-\frac{|x|}{b}\right)$$

This mathematical guarantee ensures that an adversary with arbitrary auxiliary knowledge cannot infer the presence or absence of an individual citizen's grievance from public dashboard metrics.

---

## 5. Security & Access Boundaries

1. **Role-Based Access Control (RBAC):** Strict separation of duties via cryptographically verified session tokens (`visbharat/blueprints/auth.py`):
   - `citizen`: Read-only access to self-filed complaints via verified phone token.
   - `analyst`: Read access to de-identified ward queues and stress maps; write access to triage adjudication.
   - `auditor`: Read-only access to tamper-evident audit logs and verification endpoints; zero access to citizen identities.
2. **Cryptographic Ledger Integrity:** Every access request, status change, and model inference event generates an append-only entry in the SHA-256 audit ledger (`visbharat/audit.py`), guaranteeing complete evidentiary accountability.
3. **Secret Management:** Zero plaintext API keys or credentials exist in source code. All secrets are dynamically injected via Google Cloud Secret Manager at container runtime.
