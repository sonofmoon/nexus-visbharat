# Nexus VisBharath (NVB)
### Scalable Multilingual AI for Citizen-Driven Infrastructure Prioritization
**Track:** AI for Digital Public Infrastructure & Governance | **Theme:** Innovation  
**Initiative:** Build with AI: Code for Communities (2nd Edition)  
**Pilot Coverage:** 3 States (Tamil Nadu, Andhra Pradesh, Telangana) across 97 Districts  
**Pilot Languages:** Tamil (`ta`), Telugu (`te`), English (`en`)  
**License:** Apache 2.0 (Open-Source Digital Public Good Architecture)

---

### Submission Deliverables & Live Cloud Deployments

| Deliverable | Production Link / Endpoint | Description |
|---|---|---|
| **Live Showcase Prototype** | [**`https://visbharat.nexusaitech.in`**](https://visbharat.nexusaitech.in) | Production Cloud Run deployment with custom domain routing |
| **GCP Cloud Run Service** | [`https://nexus-visbharath-510474645723.asia-south1.run.app`](https://nexus-visbharath-510474645723.asia-south1.run.app) | Direct Google Cloud Run microservice endpoint (`asia-south1`, Mumbai) |
| **Source Code (Public)** | [**GitHub Repository**](https://github.com/sonofmoon/nexus-visbharat) | Complete source tree, Dockerfile, Terraform, and test suites |
| **End-to-End Walkthrough** | [**Demo Video (3–5 Min)**](https://visbharat.nexusaitech.in/demo-video) | Working walkthrough: Multilingual intake &rarr; Planning &rarr; 4-Gate Audit |
| **Executive Pitch Deck** | [**Pitch Deck (10–12 Slides)**](https://visbharat.nexusaitech.in/pitch-deck) | Problem, solution, Google AI architecture, DPI scalability, and pilot roadmap |

> **Brief Solution Summary (2–3 Lines):**  
> **Nexus VisBharath (NVB)** is a Digital Public Good candidate platform that bridges citizen feedback with national infrastructure planning. Using Google AI (Gemini on Vertex AI, Cloud STT, and Dialogflow CX), NVB ingests voice and text reports in Tamil, Telugu, and English, cross-references them against live open data (LGD, data.gov.in, NITI Aayog NDAP), and applies a 4-gate governance workflow to help policymakers prioritize high-impact capital investments without autonomous AI hallucination.

---

## 1. Context, Problem Statement & The Challenge

### The Problem in Indian Governance
State and Central Governments across India manage hundreds of infrastructure programs, yet struggle with a fundamental disconnect: **citizen feedback lives in fragmented, disconnected silos while national capital investments are planned without granular ground-level demand data.**
- Today, citizen requests exist scattered across grievance portals (CPGRAMS, state CM helplines), localized physical petitions, social media, and call centers.
- Most systems act as reactive "ticket-closing" mechanisms (e.g., patching a recurring pothole) rather than identifying systemic infrastructure deficits (e.g., an entire ward lacking potable water pipelines).
- Over 80% of citizens cannot comfortably report issues in English or navigate complex digital web forms, creating massive linguistic exclusion for rural and regional populations.
- There is currently no unified mechanism to correlate grassroots citizen demand with national demographic deprivation (Census, SECC), multidimensional poverty (NITI Aayog MPI), and master infrastructure plans (PM Gati Shakti).

### The Challenge
> **"Build a scalable, multilingual AI platform — designed as a Digital Public Good — that aggregates citizen development requests via voice, text, and messaging apps across diverse linguistic regions of India. The system should analyse large datasets combining citizen feedback with national demographic data, infrastructure indices, and public investment plans, surfacing demand hotspots and recommending high-priority development projects to national policymakers."**

---

## 2. Platform Alignment with Core Evaluation Parameters

Nexus VisBharath (NVB) is engineered as an open-source **Digital Public Good (DPG) Candidate** addressing each of the five core evaluation parameters defined for the **Build with AI: Code for Communities** initiative:

### 1. Problem-Solution Fit (Weight: 20%)
* **Evaluation Focus:** *Does the platform directly and specifically address the challenge of consolidating fragmented citizen feedback, closing unaddressed infrastructure gaps, and enabling empirical measurement of public investment?*
* **Platform Architecture & Implementation:**
  * **Systemic Capital Planning Over Reactive Ticketing:** Moves beyond isolated grievance resolution (e.g., standard CPGRAMS ticket patching) by algorithmically clustering citizen demands into macro-level infrastructure proposals.
  * **Traceable Six-Stage Governance Pipeline:** Connects citizen voice/text reports &rarr; AI ingestion &amp; translation &rarr; planning scenario comparison &rarr; executive decision drafting &rarr; four-gate independent audit dossier &rarr; 30/60/90-day outcome delivery tracking.
  * **Fiscal Rationing Realism:** Integrates realistic capital constraints into proposal ranking—including capital expenditure caps, annual operating budget allowances (modeled at 3%), and project capacity quotas per district.
  * **Dual-Tier Operational Architecture:** Combines a macro-level **National Aggregation Engine** (12,500 synthetic stress records across 97 districts) with a micro-level **Turnkey Ministry Pilot Simulator** (controlled two-state administrative rehearsal across Vellore, Tamil Nadu and Tirupati, Andhra Pradesh).

### 2. AI & Technical Execution (Weight: 25%)
* **Evaluation Focus:** *Is Google AI performing meaningful, production-grade work? Does the prototype function end-to-end?*
* **Platform Architecture & Implementation:**
  * **End-to-End Google AI Integration:**
    * **Gemini on Vertex AI / Google AI Studio:** Performs semantic clustering, multilingual translation, category classification, urgency grading, and automated policy brief generation with cited data sources.
    * **Google Dialogflow CX:** Powers regional conversational citizen intake agents configured in `asia-south1`.
    * **Google Cloud Speech-to-Text & WebAudio:** Enables voice-first grievance intake directly from low-bandwidth mobile browsers.
    * **Predictive Demand Stress Modeling:** Machine learning models forecast next-quarter district grievance stress and service vulnerability indices.
  * **Accountable 4-Gate Governance (Rejection of Black-Box AI):** AI-generated priorities are strictly treated as *screening recommendations*. Formal capital sanction requires an independent 4-pillar audit dossier:
    1. *Gate 1 (Demand Evidence):* Cryptographically indexed citizen source tickets.
    2. *Gate 2 (Feasibility):* Civil engineering survey and cost estimation sign-off.
    3. *Gate 3 (Governance):* Administrative officer executive sanction.
    4. *Gate 4 (Data Integrity):* Provenance verification comparing operational vs. synthetic demonstration baselines.
  * **Production Rigor & Resilience:** Includes seamless fallback simulation for offline evaluation alongside live Google Cloud IAM connectors, backed by 68+ automated unit, integration, and browser test suites with 100% pass rate.

### 3. Depth & Reach Across India (Weight: 20%)
* **Evaluation Focus:** *Can this realistically scale from a single town to diverse linguistic regions, states, and communities across India?*
* **Platform Architecture & Implementation:**
  * **Honest Precision in Language Scope (Depth Over Superficial Claims):**
    * *Live Cloud-Deployed Multilingual Architecture:* Operational on Google Cloud Run (`https://visbharat.nexusaitech.in`), supporting **Tamil (`ta`)**, **Telugu (`te`)**, and **English (`en`)**, incorporating native script rendering, dialect variation handling, WebAudio voice transcription, and automated translation.
    * *Empirical Quality Benchmarks:* Backed by verifiable ground-truth scenarios (`docs/evaluation/quality.json`) demonstrating **88.0% Macro F1 in categorization**, **92.6% Urgency Accuracy**, and **100% Recall on emergency infrastructure hazards**.
    * *Zero-Shot Pan-India Extensibility:* The underlying Vertex AI Gemini / Cloud Translation architecture natively supports scheduled Indian languages (Hindi, Kannada, Malayalam, Marathi) using the identical schema without code changes.
  * **Empirical Geographic Realism (Macro Observatory vs. Micro Ministry Pilot):**
    * *Macro National Scalability Stress Corpus (12,500 Demands across 97 Districts):* Pre-deployment stress corpus of 12,500 synthetic citizen requests spanning the high-density southern growth corridor (**Tamil Nadu, Andhra Pradesh, Telangana**) ensuring zero-PII exposure under India's Digital Personal Data Protection (DPDP) Act 2023, coupled with live Ministry of Panchayati Raj Local Government Directory (LGD) Pin-Code crosswalk APIs (`Resource 7246652`).
    * *Turnkey Ministry Pilot Simulator (Vellore & Tirupati):* Controlled two-state pilot rehearsal between Vellore (TN) and Tirupati (AP) with 63 synthetic water-service scenarios, demonstrating strict inter-state administrative data isolation, ward/panchayat boundary resolution, and local officer review.
  * **Inclusive Multi-Channel Access:** Provides multiple accessible intake paths:
    * Browser-based voice recording for citizens with low digital or textual literacy.
    * Conversational Dialogflow CX assistant in regional dialects.
    * Standardized webhook contracts for WhatsApp and SMS integrations.
    * Batch import mechanisms for field survey teams (ASHA, Anganwadi, and Gram Panchayat workers).

### 4. Impact Potential (Weight: 15%)
* **Evaluation Focus:** *What is the scale of benefit — how many citizens, across how many states, and how meaningfully?*
* **Platform Architecture & Implementation:**
  * **Data-Driven Capital Allocation:** Algorithmically steers high-value capital expenditure to high-deprivation catchments by fusing citizen demand with Census 2011 demographics, NITI Aayog Multidimensional Poverty Index (MPI), and PM Gati Shakti infrastructure indicators.
  * **Post-Delivery DPI Impact Measurement:** Includes an **Outcomes Monitoring Module** that analyzes pre- and post-intervention grievance volume changes across 28/60/90-day observation windows to empirically verify whether completed infrastructure investments resolved community pain points.
  * **Tamper-Evident Anti-Corruption Ledger:** Implements immutable audit logging and role-based access control (RBAC), ensuring that citizen evidence and recorded auditor findings cannot be altered or bypassed by contractors or line departments.

### 5. Deployability & Scalability (Weight: 20%)
* **Evaluation Focus:** *Could this be piloted within a government ministry or across states in weeks?*
* **Platform Architecture & Implementation:**
  * **Lightweight, High-Performance Tech Stack:** Built with Python/Flask and native Web Components, ensuring sub-second response times, zero frontend build overhead, and minimal operational maintenance.
  * **Turnkey Ministry Pilot Workspace (`/pilot/dashboard`):** Fully operational administrative portal with role-based switching (Analyst, Administrator, Auditor, District Officer), quota controls, cloud spending throttles, and activation checklist gates.
  * **Enterprise Infrastructure as Code:** Complete HashiCorp Terraform configuration in `deploy/pilot/` provisioning Google Cloud Run microservices, Cloud SQL (PostgreSQL 16 High Availability in `asia-south1` with asynchronous replica blueprint in `asia-south2`), BigQuery analytics datasets, Cloud Tasks queues, and Secret Manager encryption.

---

## 3. What We Built: The Nexus VisBharath (NVB) Solution

**Nexus VisBharath (NVB)** is an open-source **Digital Public Good (DPG) Candidate** platform that connects multilingual citizen reports to empirical evidence, proposed capital projects, budget allocations, and delivery follow-up.

Demonstrated across **3 states (Tamil Nadu, Andhra Pradesh, Telangana) and 97 districts**, NVB ingests voice and text requests in **Tamil, Telugu, and English**, normalizes them using Google AI, fuses them with live national indicators, and surfaces evidence-backed capital investment priorities for policymakers.

> [!NOTE]
> **Architectural Philosophy — Honest Precision Beats Generic Claims:**
> Rather than presenting superficial mockups across all 28 states, NVB demonstrates deep-stack engineering rigor across a tri-state bilingual growth corridor (Tamil Nadu, Andhra Pradesh, Telangana — 97 districts, 12,500 stress records) and a turnkey Ministry Pilot simulator (Vellore & Tirupati). Its foundational AI architecture (Gemini on Vertex AI + Cloud Translation + Dialogflow CX) is intrinsically pan-India capable, verified on an empirical benchmark of **88.0% Macro F1** and **100% emergency recall** (`docs/evaluation/quality.json`).

### Live National Open Government Data & NITI Aayog Integration

Nexus-VisBharat directly connects to live Open Government Data (OGD) and National Analytics platforms:

| Dataset / Registry | Source Authority & Resource ID | Scope & Key Metrics | Role in NVB Decision Support |
|---|---|---|---|
| **LGD Local Bodies with PIN** | Ministry of Panchayati Raj (`7246652`) | 315,000+ local bodies; LGD State 33 (TN), 28 (AP) | Official administrative boundary crosswalk & PIN-to-LGD mapping |
| **Urban Local Body Census** | CMA Tamil Nadu (`54c6c324...`) | 423,425 population, 50.04% women, 4.85% SC | Ward & ULB equity scoring denominators |
| **Vellore SBM-G Sanitation** | Ministry of Jal Shakti (`b0d9af81...`) | 743 Gram Panchayats in Vellore District | Ground-level rural sanitation gap analysis |
| **JJM State Allocation & Utilization** | Rajya Sabha / MoJS (`cf0d2f0a...`) | ₹1,544.82 Cr (TN), ₹339.88 Cr (AP) available | Macro state fiscal feasibility & co-financing validation |
| **AMRUT Tirupati City Sanctions** | MoHUA / Rajya Sabha (`1ee4ff05...`) | ₹209.81 Cr across 12 projects (Water, Sewerage, Drainage) | City capital cross-referencing & anti-duplication guards |
| **Tirupati Rural JJM Habitations** | MoJS / TN Open Data (`51f7949b...`) | 1,223 Habitations, 48,920 FHTC tap connections | Rural drinking water habitation baseline monitoring |
| **NITI Aayog NDAP OpenAPI** | NITI Aayog (`loadqa.ndapapi.com`) | Indicators `I1805_3`, `_4`, `_5` across 23 States | National flood, drainage & water basin resilience indexing |

### The Decision Workflow

```mermaid
flowchart LR
    A[Citizen voice or text] --> B[Ticket and issue assignment]
    B --> C[Local evidence and service gaps]
    C --> D[Project proposal and alternatives]
    D --> E[Budget scenario]
    E --> F[Engineering review and human approval]
    F --> G[Delivery and citizen feedback]
    G --> H[Observed outcomes]
```

### The Six-Tab Analyst Suite

| Tab | Working Behavior |
| :--- | :--- |
| **1. Demand & Inclusion** | Complete filtered counts, report/issue distinction, observed density, and access-investigation flags across 97 districts. |
| **2. Evidence & Gaps** | Loaded source records, cryptographic hashes, missing evidence detection, and unverified publisher status. |
| **3. Project Priorities** | Searchable ward/service proposals with ticket text, cluster IDs, alternatives, cost basis, and exportable evidence. |
| **4. Budget Scenarios** | Dynamic weight adjustments, budget/capacity/operating limits, geographic diversification, cost sensitivity, and cited briefs. |
| **5. Delivery & Responsiveness** | Actual acknowledgment events, eligible SLA denominators, open aging, closure, citizen feedback, and linked decisions. |
| **6. Outcomes & Evaluation** | Completed observation windows, descriptive reporting changes, and measured evaluation reports. |

---

## 4. Comparison: Questions NVB Answers That India's Governance Systems Cannot

| Capability | Conventional Systems (CPGRAMS, CM Helplines) | Nexus VisBharath (NVB) |
| :--- | :--- | :--- |
| **Primary Paradigm** | Reactive ticket resolution (isolated fix) | Systemic capital planning (hotspot discovery) |
| **Linguistic Inclusion** | Text-heavy forms; predominantly English/Hindi | Native Voice-First + Dialect-tolerant in **Tamil, Telugu, English** via Google Cloud Speech & Gemini |
| **Geospatial Intelligence** | Dropdown text fields without spatial clustering | Dynamic spatial clustering & ward-level hotspot mapping |
| **National Data Fusion** | None. Complaints remain isolated | Deep fusion with Census 2011, NITI Aayog MPI, and PM Gati Shakti indices |
| **Budget Simulation** | No budget correlation | Dynamic scenario modeling (e.g., optimizing ₹10 Cr or ₹100 Cr outlay against verified ROI) |
| **Auditability & Integrity** | Opaque internal department remarks | Cryptographic chain of custody linking policy proposals to raw citizen voice evidence |

### Critical Questions NVB Answers for Indian Policymakers:
1. *Where do seemingly isolated citizen complaints indicate a structural capital gap requiring new infrastructure rather than repetitive maintenance?*
2. *Are government infrastructure investments reaching linguistically marginalized communities who only communicate via regional voice?*
3. *How can a ministry allocate a ₹100 Crore budget envelope across districts to maximize societal benefit while objectively minimizing regional deprivation?*
4. *Can every approved infrastructure proposal be verified against unaltered citizen requests and verified public records?*

---

## 5. Scoring Algorithm & Fiscal Planning Model

### Screening Score (`nvb-decision-v1`)
The platform calculates capital priority using the versioned formula:

$$\text{Priority Score} = 0.40 \times \text{Demand Density} + 0.35 \times \text{Deprivation Index} + 0.25 \times \text{Category Coverage Gap}$$

- **Demand Density (40%):** Spatial clustering of citizen demand over time.
- **Deprivation Index (35%):** NITI Aayog Multidimensional Poverty Index (MPI) and SECC indicators.
- **Coverage Gap (25%):** Existing infrastructure deficit mapped against PM Gati Shakti benchmarks.
*Missing components are transparently disclosed, and remaining weights renormalize automatically.*

### Fiscal Governance & Capital Control
- Project costs start as illustrative category envelopes.
- Engineering review records cost, surveyed beneficiary count, and evidence references. Human administrative approval remains separate.
- Known project commitments consume the scenario envelope and cannot be approved twice.
- An explicit **3% operating-cost assumption** is preserved. This planning model is an empirical screening tool, not an unverified treasury ledger.

---

## 6. Tools & Technology Stack

- **Generative AI & LLM:** Google Gemini API and Google AI Studio (multilingual understanding, synthesis, and policy briefs).
- **Conversational Agents:** Google Dialogflow CX (conversational citizen intake in `asia-south1`).
- **Language & Speech:** Google Cloud Speech-to-Text (v2 regional), Cloud Translation API, Cloud Text-to-Speech.
- **Analytics & Big Data:** Google BigQuery (partitioned & clustered data warehouse in `asia-south1`).
- **Backend & Compute:** Python 3.11, Flask, SQLAlchemy, Alembic, Google Cloud Run, Cloud Tasks (asynchronous outbox queue).
- **Database & Storage:** PostgreSQL 16 (Cloud SQL HA) / SQLite for local development, Cloud Storage for private audio objects.
- **Infrastructure as Code (IaC):** HashiCorp Terraform (`deploy/pilot/`).
- **Live National Open Data & APIs:** data.gov.in (MoPR LGD `7246652`, MoHUA AMRUT `1ee4ff05`, MoJS SBM-G `b0d9af81`, MoJS JJM `cf0d2f0a`), tn.data.gov.in (JJM `51f7949b`), NITI Aayog NDAP OpenAPI (`loadqa.ndapapi.com`), Census 2011, and PM Gati Shakti indicators.

---

## 7. Step-by-Step Installation & Local Setup

### Prerequisites
- Python 3.11 installed.
- Git installed.
- (Optional) A Google Cloud Project with Gemini API Key and Service Account Key.

### 1. Clone the Repository
```bash
git clone https://github.com/<your-username>/nexus-visbharath.git
cd nexus-visbharath
```

### 2. Set Up Virtual Environment
```powershell
# Windows PowerShell:
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# Linux / macOS:
python3 -m venv .venv
source .venv/bin/activate
```

### 3. Install Pinned Dependencies
```bash
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure Environment Secrets
Copy `.env.example` to `.env`:
```powershell
copy .env.example .env     # Windows
cp .env.example .env       # Linux / macOS
```

### 5. Initialize the Local Database
```bash
python -m flask --app app init-db
```
*Imports the bundled synthetic dataset and creates standard schemas and users. Tickets follow the durable standard format `NVB-YYYYMMDDXXXX` (where `XXXX` represents four uppercase hexadecimal characters).*

### 6. Start the Local Server
```bash
python -m flask --app app run --host 127.0.0.1 --port 5000 --no-reload
```
Open your browser and navigate to:
- **Public Citizen Portal:** `http://127.0.0.1:5000/`
- **Analyst Decision Suite:** `http://127.0.0.1:5000/dashboard`
- **Auditor & Governance Suite:** `http://127.0.0.1:5000/auditor`
- **Interactive Walkthrough:** `http://127.0.0.1:5000/demo`

---

## 8. Required Secrets & Configuration Guide

Open `.env` in any text editor and populate the following settings:

```ini
# ===================================================================
# 1. CORE APPLICATION CONFIGURATION
# ===================================================================
SECRET_KEY=generate-a-secure-random-key-here
DEMO_MODE=true
DATABASE_PATH=visbharat.db

# ===================================================================
# 2. GOOGLE AI (GEMINI) INTEGRATION
# ===================================================================
USE_REAL_GOOGLE_AI=true
GOOGLE_AI_API_KEY=AIzaSyYourGeminiApiKeyHere

# ===================================================================
# 3. GOOGLE CLOUD SPEECH-TO-TEXT & SERVICE ACCOUNT
# ===================================================================
USE_REAL_GOOGLE_STT=true
# Inject from the deployment environment or Secret Manager; do not store a key in this repository.
GOOGLE_APPLICATION_CREDENTIALS=/secure/path/service-account.json
PILOT_SPEECH_LOCATION=asia-south1

# ===================================================================
# 4. GOOGLE DIALOGFLOW CX (Conversational Voice/Text Agent)
# ===================================================================
USE_REAL_DIALOGFLOW_CX=true
DIALOGFLOW_PROJECT_ID=your-google-cloud-project-id
DIALOGFLOW_LOCATION=asia-south1
DIALOGFLOW_AGENT_ID=your-dialogflow-agent-id
DIALOGFLOW_LANGUAGE_CODE=en
DIALOGFLOW_API_ENDPOINT=asia-south1-dialogflow.googleapis.com

# ===================================================================
# 5. GOOGLE BIGQUERY (Live Analytics Data Warehouse)
# ===================================================================
USE_REAL_BIGQUERY=true
BIGQUERY_PROJECT_ID=your-google-cloud-project-id
BIGQUERY_DATASET=visbharat_analytics
BIGQUERY_TABLE=citizen_requests_fused
BIGQUERY_LOCATION=asia-south1

# ===================================================================
# 6. GOVERNANCE & RBAC API TOKENS
# ===================================================================
ADMIN_API_TOKEN=visbharat-admin-token
ANALYST_API_TOKEN=visbharat-analyst-token
AUDITOR_API_TOKEN=visbharat-auditor-token
```

> **Deterministic Offline Simulation Fallback:**  
> For local demonstration and automated testing without external credentials, set `NVB_DISABLE_EXTERNAL_SERVICES=1` and `USE_REAL_GOOGLE_AI=false`. NVB activates its internal deterministic simulation fallback, maintaining complete UI, scenario ranking, and workflow continuity for offline evaluators.

---

## 9. Connecting and Streaming Data to Google BigQuery

The platform supports asynchronous streaming replication of validated citizen requests to Google BigQuery (`visbharat_analytics.citizen_requests_fused` in `asia-south1`):

### Option A: Direct Web Console Upload (Quickest Local Inspection)
1. In Google Cloud Console, navigate to **BigQuery**.
2. Select your Project &rarr; Click **Create Dataset**:
   - Dataset ID: `visbharat_analytics`
   - Data location: `asia-south1 (Mumbai)`
3. Inside `visbharat_analytics`, click **Create Table**:
   - Source: **Upload**
   - File: Select `static/data/complaints.csv` from the cloned repository.
   - Table name: `citizen_requests_fused`
   - Schema: Check **Auto-detect**.
4. Click **Create Table**. The 12,500 records are immediately queryable for SQL spatial analysis.

### Option B: Automated CLI Streaming
```bash
python scripts/publish_jury_demo.py --cloud
```
*Validates schema compatibility, builds the table in `asia-south1`, and streams records into BigQuery.*

---

## 10. Turnkey Pilot Simulation: Inter-State & Inter-Language (Vellore & Tirupati)

### Ground Truth Context
The lead developer is based in **Vellore, Tamil Nadu**, located immediately adjacent to **Tirupati, Andhra Pradesh**. This real-world border geography was selected to demonstrate immediate ministerial pilot readiness:
- **Cross-Border Inter-State Administration:** Evaluates how Tamil Nadu and Andhra Pradesh district administrations operate on a single shared platform while maintaining strict jurisdictional data fencing.
- **Three Core Pilot Languages:** Real-world citizen intake in **Tamil (`ta`)**, **Telugu (`te`)**, and **English (`en`)**, with automated UI script synchronization based on selected district or URL routing.
- **Sector Focus:** Water-supply infrastructure requests across urban wards and rural panchayats.

### Dual-Tier Operational Architecture
| Operational Layer | Active Scope | Primary Persona | Purpose |
| :--- | :--- | :--- | :--- |
| **National Macro Observatory** (`:5000/dashboard`) | **12,500 Synthetic Requests** (97 districts across TN, AP, Telangana) | Central Ministry Policy Analysts | Spot macro demand clusters, multi-state infrastructure deficits, and simulate national capital allocation scenarios. |
| **Turnkey Ministry Pilot Simulator** (`:5001/pilot`) | **63 Controlled Field Scenarios** (Vellore, TN & Tirupati, AP) | District Collectors, Municipal Engineers, Auditors | Process individual citizen reports, verify engineering feasibility, draft capital sanction decisions, and release cryptographic receipts. |

### Launching the Ministry Pilot Simulator
Run the dedicated ministerial pilot instance:
```powershell
python scripts/run_ministry_pilot.py --port 5001
```
Open your browser and navigate directly to the **Ministry Pilot Portal**:
👉 **`http://127.0.0.1:5001/pilot`**

### Key Pilot Readiness Capabilities:
1. **Strict Administrative Isolation:** Vellore district officers cannot inspect Tirupati citizen data, and vice versa.
2. **Context-Aware Multilingual Routing:** Selecting Tirupati automatically configures the submission portal to Telugu (`te`), while Vellore configures to Tamil (`ta`), with zero script loss upon toggling.
3. **The 10 Operational Launch Gates:** Accessible under the *Launch Readiness* tab, tracking LGD boundary verification, role assignments, audit chains, and disaster recovery.
4. **Resilient Transactional Outbox Pattern:** Implements durable SQL-first ticket commits before triggering asynchronous model workers, drastically reducing data-loss risk during network drops or external API latency.
5. **Predictable Cloud Budget Envelope:** Documented monthly cost model estimated at **₹1.5 Lakhs (≈$1,800 USD/month)** for 10,000 requests, governed by automated 50%/80%/100% Cloud Billing alert policies in Terraform.
6. **Enterprise Disaster Recovery Blueprint:** Fully codified in HashiCorp Terraform (`deploy/pilot/`) providing infrastructure specifications for:
   - **Primary Region:** Mumbai (`asia-south1`) Cloud SQL PostgreSQL 16 HA + Cloud Run.
   - **Standby DR Region:** Delhi (`asia-south2`) asynchronous read replica and standby recovery service (Architected for **RTO &le; 60m, RPO &le; 15m** upon failover promotion).

---

## 11. Platform Assessment & Verification Walkthrough

Independent reviewers and technical evaluators can verify the complete end-to-end functionality using this structured checklist:

### Step 1: System Health & Provider Verification
```bash
# Check database backend (SQLite / PostgreSQL)
curl http://localhost:5000/api/db/status

# Check live Google AI and STT connectivity
curl http://localhost:5000/api/ai/status
```
*Expected response confirms `google_ai_live`, `google_stt_live`, and active BigQuery warehouse status.*

### Step 2: Multilingual Voice & Text Ingress (Tamil, Telugu, English)
Submit a citizen request in Tamil or Telugu:
- **Web UI:** Visit `http://localhost:5000/`, select Tamil or Telugu, and submit an infrastructure issue.
- **Voice Ingress Endpoint:**
```bash
curl -X POST http://localhost:5000/api/submit-voice \
  -F "audio=@tests/fixtures/sample_tamil_water.wav" \
  -F "language=ta" \
  -F "district=Vellore" \
  -F "state=Tamil Nadu"
```
*Google STT transcribes the audio, and Gemini translates it to English, extracts keywords, categorizes under `Water Supply`, and flags urgency.*

### Step 3: Hotspot Demand Intelligence & Geospatial Analytics
- Visit `http://localhost:5000/dashboard` &rarr; Tab 1: **Demand & Inclusion**.
- Query the API directly:
```bash
curl http://localhost:5000/api/hotspots
```
*Returns ranked infrastructure demand clusters calculated directly from BigQuery/database aggregates across 97 districts.*

### Step 4: Automated Policy Brief Synthesis (Gemini RAG)
Generate an empirical briefing for a district administrator:
```bash
curl http://localhost:5000/api/policy-brief/Chennai
```
*Gemini synthesizes citizen request clusters, ward deprivation data, and ongoing capital projects into a structured policy brief with concrete recommendations.*

### Step 5: Independent Auditor Suite & Cryptographic Logs
- Visit `http://localhost:5000/auditor`.
- Or query audit events via authorized token:
```bash
curl -H "Authorization: Bearer visbharat-auditor-token" http://localhost:5000/api/v1/audit-logs
```
*Displays immutable audit records, guaranteeing transparency from citizen intake to executive approval.*

### Step 6: Automated Test Suite & Measured Quality Verification
Run the regression suite and read-only UI checks:
```powershell
python -m unittest discover -s tests -p "test_*.py"
python scripts/check_analyst_workbench_ui.py
python scripts/check_request_filters_ui.py
python scripts/check_ministry_pilot_ui.py
python scripts/verify_analyst_exchange.py
```

#### Measured Benchmark Performance:
- **Multilingual Evaluation (27 newly authored test cases in Tamil, Telugu, English):**
  - Category Macro-F1: **0.88**
  - Urgency Accuracy: **0.9259**
  - Emergency Recall: **1.0 (on 9 proposed emergency cases)**
  - Provider Fallbacks: **0**
- **Load Measurements:** Verified on 12,500 and 100,000 synthetic rows at concurrency 1 and 4 (documented in `docs/evaluation/load.json`).

---

## 12. Production Cloud Deployment (Terraform)

The `deploy/pilot/` directory contains complete HashiCorp Terraform manifests:
```bash
cd deploy/pilot
terraform init
terraform plan -var="project_id=YOUR_GCP_PROJECT_ID" -var="billing_account=YOUR_BILLING_ACCOUNT"
# Apply with authorized approval:
# terraform apply
```
This provisions:
- Google Cloud Run web and worker microservices.
- Cloud SQL PostgreSQL 16 with regional High Availability in Mumbai (`asia-south1`).
- Cross-region asynchronous read replica in Delhi (`asia-south2`).
- BigQuery dataset (`nvb_pilot`) and Cloud Tasks queue.
- Secret Manager, Cloud Storage private audio buckets, and Cloud Monitoring alert policies.

---

## 13. Technical References & Evidence Base

- [Implementation Status and Acceptance Gates](./docs/ANALYST_IMPLEMENTATION_STATUS.md)
- [Ministry Pilot Deployment Runbook](./docs/MINISTRY_PILOT_DEPLOYMENT_RUNBOOK.md)
- [Ministry Pilot Roadmap & Cost Model](./docs/MINISTRY_PILOT_ROADMAP_AND_COST_MODEL.md)
- [Independent Review Instructions](./docs/evaluation/REVIEW_GUIDE.md)
- [Portable Decision Schema](./docs/release/analyst-decision.schema.json)
- [Measured Quality Report](./docs/evaluation/quality.json)
- [Deployment and API Instructions](./docs/ANALYST_DEPLOYMENT_AND_API.md)

---

## 14. Author & Institutional Attribution

- **Principal Architect & Lead Developer:** **Dr. Ravikumar Chandrasekaran**
- **Designation:** Lecturer
- **Institution:** Thanthai Periyar E.V. Ramasamy Government Polytechnic College
- **Location:** Vellore – 632055, Tamil Nadu, India
- **Initiative:** Built for **Build with AI: Code for Communities (2nd Edition)** — Google Cloud Hackathon
- **License:** Apache License 2.0 (Open-source Digital Public Good Architecture)
