# External Benchmark to Nexus Release Checklist (Machine-Gated)

This checklist translates external civic benchmark operations into Nexus release gates with explicit pass/fail criteria.

## Priority Policy
- **Must**: must be `PASS` for release gate to pass.
- **Should**: must be `PASS` or `WAIVED` for release gate to pass.
- **Could**: tracked but non-blocking.

## Phase Gate Mapping
- `phase2_processing`: channel ingestion, routing, processing reliability.
- `phase3_policy`: AI/intelligence quality and signal completeness.
- `phase4_policy_workflow`: policy workflow, approvals, and prioritization.
- `phase5_transparency`: public transparency, auditability, citizen trust.

## Checklist Items + Pass/Fail Criteria

| ID | Priority | Mapped Gate(s) | Pass Criteria | Fail Criteria |
|---|---|---|---|---|
| RG-001 | Must | phase2_processing, phase5_transparency | All channel submissions generate unique request IDs and citizen-visible status timeline | Missing IDs, inconsistent status timeline across channels |
| RG-002 | Must | phase2_processing, phase4_policy_workflow | State-district-service routing matrix resolves without fallback for in-scope pilot areas | Unknown routing or frequent manual overrides |
| RG-003 | Must | phase4_policy_workflow, phase5_transparency | Re-open/escalation workflow creates auditable trail with actor/timestamp/reason | Re-open path missing or non-auditable |
| RG-004 | Must | phase5_transparency | Post-closure feedback captured and linked to closure quality metrics | Feedback loop absent or not linked to outcomes |
| RG-005 | Must | phase2_processing | Web, Voice, WhatsApp, SMS ingestion parity confirmed in burn-in | Any required ingestion channel fails burn-in |
| RG-006 | Should | phase2_processing | Assisted/offline intake (call-center/email handoff) documented and testable | No operational fallback for assisted intake |
| RG-007 | Should | phase3_policy, phase4_policy_workflow | AI assist has confidence threshold plus fallback path to human review | Low-confidence AI outputs auto-accepted without fallback |
| RG-008 | Should | phase3_policy, phase4_policy_workflow | Ward/service taxonomy signals flow into hotspot and policy prioritization outputs | Taxonomy not represented in intelligence/policy outputs |
| RG-009 | Should | phase5_transparency | Complaint export/audit retrieval works for compliance review | Export path missing or non-reproducible |
| RG-010 | Could | phase2_processing, phase5_transparency | Mobile/offline field workflow available or implementation plan signed off | No field/offline plan documented |

## Machine Source of Truth
- Checklist status values and evidence are loaded from:
  - `docs/release/benchmark_release_checklist.json`
- Schema validated against:
  - `docs/release/benchmark_release_checklist.schema.json`
- Checklist items support `machine_check` rules (`phase_runner_pass`, `evidence_files_exist`, `report_contains`, `composite`) and are auto-evaluated by `scripts/burnin_release_gate.py`.