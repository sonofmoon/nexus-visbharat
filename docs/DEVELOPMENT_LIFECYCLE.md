# Nexus VisBharat: Engineering Development Lifecycle & Contribution Architecture

This document formalizes the multi-contributor development standards, branch lifecycle, code review gates, and dependency management policies for **Nexus VisBharat (NVB)**.

---

## 1. Domain Component Ownership & Team Matrix

The platform is engineered across 4 decoupled architectural pillars:

| Domain Pillar | Core Repositories & Modules | Primary Responsibilities | Review Owners |
|---|---|---|---|
| **AI & Multilingual Pipelines** | `visbharat/services/google_ai.py`<br>`visbharat/services/ai_simulation.py`<br>`scripts/evaluate_analyst_quality.py` | Prompt engineering, Gemini 2.5/3.6 Flash integration, Speech-to-Text (Chirp Indic models), translation fidelity, and evaluation benchmarks. | AI/ML Lead, Indic NLP Specialist |
| **Cloud Infrastructure & Security** | `Dockerfile`<br>`scripts/rotate_and_migrate_secrets.ps1`<br>`visbharat/db.py`<br>`visbharat/config.py` | Cloud Run serverless deployment, Cloud SQL (PostgreSQL) connection pools, Google Secret Manager IAM bindings, zero-trust RBAC. | Cloud Platform Engineer, SRE Lead |
| **Civic Intake & Telephony** | `visbharat/services/ivr_orchestrator.py`<br>`visbharat/blueprints/api_channels.py`<br>`visbharat/blueprints/api_intake.py` | Missed-call IVR orchestration, dead-letter recovery queues, webhook nonces, emergency escalation dispatch. | Telephony & Voice Lead, Backend Engineer |
| **Governance & Decision Analytics** | `visbharat/blueprints/api_governance.py`<br>`visbharat/services/rct_experimentation.py`<br>`static/js/*`<br>`templates/*` | Decision dossiers, multi-criteria capital budget ranking, SHA-256 Merkle chain verification, responsive UI/UX. | Product Architect, Governance Lead |

---

## 2. Git Branching Model & Commit Taxonomy

To maintain high release velocity while preserving cryptographic and operational integrity, all contributions adhere to the **Trunk-Based Feature Branching Model**:

```
 [main] ──●──────────────●──────────────●─────── (Continuous Deployment to Cloud Run)
           \            /              /
    [feat/asr-chirp] ──●              /
                       \             /
            [fix/ivr-alert-events] ─●
```

### Branch Naming Standards
- `feat/<scope>-<description>` (e.g., `feat/chirp-indic-streaming`, `feat/analyst-budget-cap`)
- `fix/<scope>-<description>` (e.g., `fix/ivr-alert-table-migration`, `fix/canvas-id-collision`)
- `refactor/<scope>-<description>` (e.g., `refactor/api-blueprint-modularization`)
- `docs/<scope>-<description>` (e.g., `docs/evaluation-benchmark-v2`)

### Commit Message Convention (Conventional Commits)
All commit messages must follow the standard:
`<type>(<scope>): <short imperative description>`
* Examples:
  - `feat(production): finalize Google Cloud AI speech integration and harden admin telemetry workflows`
  - `fix(db): register missing alert events tables in application migration chain`
  - `refactor(api): decompose monolithic api blueprint into domain-driven sub-modules`

---

## 3. Pull Request (PR) Quality Gates & Automated CI Checks

Before any branch can merge into `main`, the following automated validation pipeline must pass with 100% green status:

1. **Unit & Workbench Suite:**
   ```bash
   python -m unittest discover -s tests -p "test_analyst_workbench*.py"
   ```
   *Gate:* All 18 suite tests must pass without errors.

2. **RBAC & API Contract Matrix:**
   ```bash
   python -m pytest tests/test_api_contract_and_rbac.py
   ```
   *Gate:* All 91 contract tests must verify strict HTTP 401/403 authorization rejections and role boundary enforcement.

3. **Zero Plaintext Credentials Static Check:**
   ```bash
   git diff main...HEAD -S "AIzaSy"
   git diff main...HEAD -S "nvb-adm"
   ```
   *Gate:* Zero hardcoded API keys or database URLs allowed. All secrets must reference Google Secret Manager.

4. **Live Production Acceptance Suite (Post-Deployment):**
   ```bash
   python scripts/run_acceptance_checks.py
   ```
   *Gate:* All 13 live acceptance tests against Cloud Run must return `HTTP 200/201`.

---

## 4. Hermetic Dependency Management & Lockfile Policy

To ensure complete build reproducibility across local workstations and Google Cloud Build pipelines:
- **`requirements.txt`**: Declares loose semver version constraints for development agility.
- **`requirements.lock`**: Pinned, cryptographically reproducible lockfile matching the container execution layer on Google Cloud Run (Debian 12 / Python 3.11).
- **Updating Dependencies**: When adding or updating a Python package:
  1. Add entry to `requirements.txt`.
  2. Test in local virtual environment.
  3. Update `requirements.lock` with the exact pinned version.
  4. Submit both in the same PR.
