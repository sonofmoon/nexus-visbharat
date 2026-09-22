"""Authenticated Analyst and Auditor acceptance verification suite for Nexus VisBharat."""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error

BASE_URL = os.environ.get('CLOUD_RUN_URL', 'https://nexus-visbharath-510474645723.asia-south1.run.app')
ANALYST_TOKEN = os.environ.get('ANALYST_API_TOKEN', 'nvb-ana-fee1d877c44740c3afc4c00d5424da46')
AUDITOR_TOKEN = os.environ.get('AUDITOR_API_TOKEN', 'nvb-aud-a9c17bdddb1a437aa2df8cd11fff8c66')
if not ANALYST_TOKEN or not AUDITOR_TOKEN:
    raise SystemExit('Set ANALYST_API_TOKEN and AUDITOR_API_TOKEN from Secret Manager before running acceptance checks.')

results = []

def record(test_name, success, details):
    status = 'PASSED' if success else 'FAILED'
    print(f'[{status}] {test_name}: {details}')
    results.append({'test': test_name, 'status': status, 'details': details})

def request(path, token=None, method='GET', data=None):
    url = BASE_URL + path
    headers = {'User-Agent': 'AcceptanceSuite/1.0', 'Accept': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    body = None
    if data is not None:
        body = json.dumps(data).encode('utf-8')
        headers['Content-Type'] = 'application/json'
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            content = resp.read().decode('utf-8')
            return resp.status, json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        content = e.read().decode('utf-8')
        try:
            parsed = json.loads(content)
        except Exception:
            parsed = {'error': content}
        return e.code, parsed

def run():
    print(f'Starting Authenticated Analyst/Auditor Acceptance Suite on {BASE_URL}...\n')

    # ==================== RBAC TESTS ====================
    # 1. Unauthenticated request to protected endpoint
    st, resp = request('/api/v2/analyst/snapshot')
    record('RBAC: Deny Unauthenticated Analyst Access', st == 401, f'HTTP {st} (Expected 401)')

    st, resp = request('/api/v2/auditor/snapshot')
    record('RBAC: Deny Unauthenticated Auditor Access', st == 401, f'HTTP {st} (Expected 401)')

    # 2. Invalid token
    st, resp = request('/api/v2/analyst/snapshot', token='fake-token-1234')
    record('RBAC: Deny Invalid Token', st == 401, f'HTTP {st} (Expected 401)')

    # 3. Cross-role unauthorized access (Analyst attempting Auditor case creation)
    st, resp = request('/api/v2/auditor/cases', token=ANALYST_TOKEN, method='POST', data={
        'kind': 'discrepancy', 'title': 'Analyst attempting auditor action'
    })
    record('RBAC: Deny Cross-Role Privilege Escalation', st == 403, f'HTTP {st} (Expected 403)')

    # ==================== ANALYST TESTS ====================
    # 4. Authenticated Analyst Snapshot
    st, resp = request('/api/v2/analyst/snapshot?limit=10', token=ANALYST_TOKEN)
    total_complaints = resp.get('stats', {}).get('total_complaints', 0)
    record('Analyst: Snapshot Aggregation', st == 200 and total_complaints > 0,
           f'HTTP {st}, total_complaints={total_complaints}, categories={len(resp.get("stats", {}).get("categories", {}))}')

    # 5. Multi-Criteria Scenario Scoring & Custom Weights
    weights_json = json.dumps({'demand': 0.5, 'equity': 0.3, 'gap': 0.2})
    st, resp = request(f'/api/v2/analyst/snapshot?weights={urllib.parse.quote(weights_json)}&budget_lakh=500', token=ANALYST_TOKEN)
    scenario = resp.get('scenario', {})
    items = scenario.get('items', [])
    top_cand = items[0] if items else {}
    score = top_cand.get('priority_score')
    score_str = f'{score:.2f}' if isinstance(score, (int, float)) else str(score)
    record('Analyst: Scenario Multi-Criteria Prioritization', st == 200 and len(items) > 0,
           f'HTTP {st}, total_candidates={scenario.get("total_candidates")}, top_project={top_cand.get("project_id")} (score={score_str})')

    # 6. Budget Allocation Simulation
    allocation = scenario.get('allocation', {})
    selected_count = allocation.get('selected_count', 0)
    record('Analyst: Budget Allocation (INR 500L cap)', st == 200 and selected_count > 0,
           f'HTTP {st}, selected_count={selected_count}, total_allocated_lakh={allocation.get("total_allocated_lakh", 0)}')

    # 7. Project Detail inspection
    if items:
        pid = items[0]['project_id']
        st, p_resp = request(f'/api/v2/analyst/projects/{pid}', token=ANALYST_TOKEN)
        record('Analyst: Project Deep-Dive Inspection', st == 200 and 'project_id' in p_resp,
               f'HTTP {st}, project_id={pid}, category={p_resp.get("category")}, total_requests={p_resp.get("total_requests")}')

    # ==================== AUDITOR TESTS ====================
    # 8. Authenticated Auditor Snapshot
    st, resp = request('/api/v2/auditor/snapshot', token=AUDITOR_TOKEN)
    counts = resp.get('counts', {})
    record('Auditor: Independent Audit Snapshot', st == 200 and counts.get('total', 0) > 0,
           f'HTTP {st}, total={counts.get("total")}, consent_receipts={resp.get("consent", {}).get("status")}')

    # 9. Create Review Case
    case_payload = {
        'kind': 'discrepancy',
        'title': 'Automated Acceptance Verification Case',
        'notes': 'Independent acceptance check of delivery progress and community receipts',
        'project_id': items[0]['project_id'] if items else 'NVB-P-SAMPLE',
        'data_mode': 'synthetic'
    }
    st, case_resp = request('/api/v2/auditor/cases', token=AUDITOR_TOKEN, method='POST', data=case_payload)
    case_id = case_resp.get('case', {}).get('case_id')
    record('Auditor: Create Review Case', st == 201 and case_id is not None,
           f'HTTP {st}, case_id={case_id}')

    # 10. Progress Review Case Lifecycle
    if case_id:
        st, act_resp = request(f'/api/v2/auditor/cases/{case_id}/actions', token=AUDITOR_TOKEN, method='POST', data={
            'version': 1,
            'action': 'start',
            'notes': 'Investigation begun by certified independent auditor'
        })
        record('Auditor: Progress Review Case Lifecycle', st == 200 and act_resp.get('success'),
               f'HTTP {st}, status={act_resp.get("case", {}).get("status")}')

    # 11. Attach Independent Site Evidence
    if items:
        pid = items[0]['project_id']
        ev_payload = {
            'kind': 'site',
            'title': 'Auditor On-Site Water Purity & Flow Rate Inspection',
            'source_uri': 'urn:nvb:audit:site:inspection:2026',
            'observed_at': '2026-09-22',
            'data_mode': 'synthetic',
            'metadata': {'milestone': 'M1', 'period': '2026-Q3', 'observed_progress_pct': 35}
        }
        st, ev_resp = request(f'/api/v2/auditor/projects/{pid}/evidence', token=AUDITOR_TOKEN, method='POST', data=ev_payload)
        record('Auditor: Attach Independent Evidence Observation', st == 201 and ev_resp.get('success'),
               f'HTTP {st}, evidence_id={ev_resp.get("evidence", {}).get("evidence_id")}')

    # 12. Cryptographic Ledger Hash Chain Verification
    st, ledger_resp = request('/api/v2/auditor/integrity', token=AUDITOR_TOKEN)
    record('Auditor: Tamper-Evident SHA-256 Ledger Verification', st == 200 and ledger_resp.get('success'),
           f'HTTP {st}, valid={ledger_resp.get("valid")}, head_seq={ledger_resp.get("head_seq")}, head_hash={ledger_resp.get("head_hash")[:12]}...')

    print('\n==================== ACCEPTANCE SUMMARY ====================')
    all_passed = all(r['status'] == 'PASSED' for r in results)
    print(f'Total Checks: {len(results)} | Passed: {sum(1 for r in results if r["status"] == "PASSED")} | Failed: {sum(1 for r in results if r["status"] == "FAILED")}')
    if all_passed:
        print('ALL ACCEPTANCE CHECKS PASSED SUCCESSFULLY WITH STRICT ZERO-TRUST RBAC.')
    else:
        print('SOME CHECKS FAILED.')
    return all_passed

if __name__ == '__main__':
    import urllib.parse
    ok = run()
    sys.exit(0 if ok else 1)

