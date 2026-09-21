import json
import os
import subprocess
import sys
from datetime import datetime, timezone


PHASE_RUNNERS = [
    {
        'phase': 'phase2_processing',
        'script': os.path.join('scripts', 'burnin_phase2_processing.py'),
        'report': os.path.join('docs', 'reports', 'Phase2_GoLive_Readiness_Report.md'),
    },
    {
        'phase': 'phase3_policy',
        'script': os.path.join('scripts', 'burnin_phase3_policy.py'),
        'report': os.path.join('docs', 'reports', 'Phase3_GoLive_Readiness_Report.md'),
    },
    {
        'phase': 'phase4_policy_workflow',
        'script': os.path.join('scripts', 'burnin_phase4_policy_workflow.py'),
        'report': os.path.join('docs', 'reports', 'Phase4_GoLive_Readiness_Report.md'),
    },
    {
        'phase': 'phase5_transparency',
        'script': os.path.join('scripts', 'burnin_phase5_transparency.py'),
        'report': os.path.join('docs', 'reports', 'Phase5_GoLive_Readiness_Report.md'),
    },
]

MASTER_REPORT_PATH = os.path.join('docs', 'reports', 'ReleaseGate_Burnin_Report.md')
CHECKLIST_JSON_PATH = os.path.join('docs', 'release', 'benchmark_release_checklist.json')
CHECKLIST_SCHEMA_PATH = os.path.join('docs', 'release', 'benchmark_release_checklist.schema.json')

ALLOWED_PRIORITIES = {'Must', 'Should', 'Could'}
ALLOWED_STATUSES = {'PASS', 'FAIL', 'WAIVED'}
ALLOWED_PHASE_GATES = {
    'phase2_processing',
    'phase3_policy',
    'phase4_policy_workflow',
    'phase5_transparency',
}
ALLOWED_MACHINE_CHECK_TYPES = {'phase_runner_pass', 'evidence_files_exist', 'report_contains', 'composite'}


def _parse_json_from_stdout(output_text):
    start = output_text.find('{')
    end = output_text.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    snippet = output_text[start:end + 1]
    try:
        return json.loads(snippet)
    except Exception:
        return None


def _run_phase_runner(python_exe, item):
    cmd = [python_exe, item['script']]

    env = dict(os.environ)
    cwd = os.getcwd()
    existing_pythonpath = env.get('PYTHONPATH', '')
    env['PYTHONPATH'] = cwd if not existing_pythonpath else f'{cwd}{os.pathsep}{existing_pythonpath}'

    result = subprocess.run(cmd, capture_output=True, text=True, env=env)

    stdout = result.stdout or ''
    stderr = result.stderr or ''
    parsed = _parse_json_from_stdout(stdout)

    success = result.returncode == 0 and isinstance(parsed, dict) and bool((parsed.get('summary') or {}).get('all_passed'))

    return {
        'phase': item['phase'],
        'script': item['script'],
        'report_path': item['report'],
        'return_code': result.returncode,
        'all_passed': bool((parsed or {}).get('summary', {}).get('all_passed', False)),
        'passed_checks': int(((parsed or {}).get('summary') or {}).get('passed_checks', 0) or 0),
        'total_checks': int(((parsed or {}).get('summary') or {}).get('total_checks', 0) or 0),
        'generated_at': (parsed or {}).get('generated_at'),
        'success': success,
        'stdout_tail': '\n'.join(stdout.strip().splitlines()[-8:]) if stdout.strip() else '',
        'stderr_tail': '\n'.join(stderr.strip().splitlines()[-8:]) if stderr.strip() else '',
    }


def _load_json_file(path):
    with open(path, 'r', encoding='utf-8-sig') as handle:
        return json.load(handle)


def _validate_machine_check(prefix, machine_check):
    errors = []
    if not isinstance(machine_check, dict):
        return [f'{prefix}.machine_check must be an object']

    check_type = machine_check.get('type')
    if check_type not in ALLOWED_MACHINE_CHECK_TYPES:
        errors.append(f"{prefix}.machine_check.type must be one of {sorted(ALLOWED_MACHINE_CHECK_TYPES)}")
        return errors

    if check_type == 'phase_runner_pass':
        required = machine_check.get('required_phase_gates')
        if not isinstance(required, list) or not required:
            errors.append(f'{prefix}.machine_check.required_phase_gates must be a non-empty array')
        else:
            for gate in required:
                if gate not in ALLOWED_PHASE_GATES:
                    errors.append(f'{prefix}.machine_check.required_phase_gates has invalid gate: {gate}')

    if check_type == 'report_contains':
        file_path = machine_check.get('file_path')
        patterns = machine_check.get('patterns')
        if not isinstance(file_path, str) or not file_path.strip():
            errors.append(f'{prefix}.machine_check.file_path is required for report_contains')
        if not isinstance(patterns, list) or not patterns:
            errors.append(f'{prefix}.machine_check.patterns must be non-empty for report_contains')

    if check_type == 'composite':
        checks = machine_check.get('checks')
        if not isinstance(checks, list) or not checks:
            errors.append(f'{prefix}.machine_check.checks must be non-empty for composite')
        else:
            for idx, sub in enumerate(checks):
                errors.extend(_validate_machine_check(f'{prefix}.machine_check.checks[{idx}]', sub))

    return errors


def _validate_checklist_schema(checklist, schema):
    errors = []

    if not isinstance(schema, dict):
        errors.append('schema file must be a JSON object')
    if not isinstance(checklist, dict):
        errors.append('checklist file must be a JSON object')
        return errors

    for top_key in ('checklist_name', 'version', 'generated_at', 'items'):
        if top_key not in checklist:
            errors.append(f'missing top-level key: {top_key}')

    items = checklist.get('items')
    if not isinstance(items, list) or not items:
        errors.append('items must be a non-empty array')
        return errors

    seen_ids = set()
    for idx, item in enumerate(items):
        prefix = f'items[{idx}]'
        if not isinstance(item, dict):
            errors.append(f'{prefix} must be an object')
            continue

        for required in ('id', 'title', 'priority', 'phase_gates', 'criterion', 'status', 'evidence'):
            if required not in item:
                errors.append(f'{prefix}.{required} is required')

        item_id = str(item.get('id', '')).strip()
        if not item_id:
            errors.append(f'{prefix}.id is empty')
        elif item_id in seen_ids:
            errors.append(f'duplicate checklist id: {item_id}')
        else:
            seen_ids.add(item_id)

        if item.get('priority') not in ALLOWED_PRIORITIES:
            errors.append(f"{prefix}.priority must be one of {sorted(ALLOWED_PRIORITIES)}")

        if item.get('status') not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status must be one of {sorted(ALLOWED_STATUSES)}")

        phase_gates = item.get('phase_gates')
        if not isinstance(phase_gates, list) or not phase_gates:
            errors.append(f'{prefix}.phase_gates must be a non-empty array')
        else:
            for gate in phase_gates:
                if gate not in ALLOWED_PHASE_GATES:
                    errors.append(f'{prefix}.phase_gates contains invalid value: {gate}')

        evidence = item.get('evidence')
        if not isinstance(evidence, list):
            errors.append(f'{prefix}.evidence must be an array')

        status = item.get('status')
        if status == 'PASS' and (not isinstance(evidence, list) or len(evidence) == 0):
            errors.append(f'{prefix}.evidence must be non-empty when status=PASS')
        if status == 'FAIL' and not str(item.get('remediation_plan', '')).strip():
            errors.append(f'{prefix}.remediation_plan required when status=FAIL')
        if status == 'WAIVED':
            if not str(item.get('waiver_reason', '')).strip():
                errors.append(f'{prefix}.waiver_reason required when status=WAIVED')
            if not str(item.get('waiver_approved_by', '')).strip():
                errors.append(f'{prefix}.waiver_approved_by required when status=WAIVED')

        if 'machine_check' in item:
            errors.extend(_validate_machine_check(prefix, item['machine_check']))

    return errors


def _evaluate_machine_check(machine_check, phase_success_map, evidence):
    check_type = machine_check.get('type')

    if check_type == 'phase_runner_pass':
        required = machine_check.get('required_phase_gates', [])
        failed = [gate for gate in required if not phase_success_map.get(gate, False)]
        return len(failed) == 0, [] if not failed else [f'phase gate not passed: {gate}' for gate in failed]

    if check_type == 'evidence_files_exist':
        missing = [path for path in evidence if not os.path.exists(path)]
        return len(missing) == 0, [] if not missing else [f'evidence file missing: {path}' for path in missing]

    if check_type == 'report_contains':
        file_path = str(machine_check.get('file_path', '')).strip()
        patterns = machine_check.get('patterns', [])
        if not file_path or not os.path.exists(file_path):
            return False, [f'report file missing: {file_path}']
        try:
            with open(file_path, 'r', encoding='utf-8') as handle:
                content = handle.read()
        except Exception as err:
            return False, [f'cannot read report file: {file_path} ({err})']

        missing_patterns = []
        lowered = content.lower()
        for pattern in patterns:
            if str(pattern).lower() not in lowered:
                missing_patterns.append(str(pattern))

        return len(missing_patterns) == 0, [] if not missing_patterns else [f'missing report pattern: {p}' for p in missing_patterns]

    if check_type == 'composite':
        all_errors = []
        for sub in machine_check.get('checks', []):
            ok, errors = _evaluate_machine_check(sub, phase_success_map, evidence)
            if not ok:
                all_errors.extend(errors)
        return len(all_errors) == 0, all_errors

    return False, [f'unsupported machine check type: {check_type}']


def _evaluate_checklist_gate(runs):
    result = {
        'gate': 'external_benchmark_checklist',
        'checklist_path': CHECKLIST_JSON_PATH,
        'schema_path': CHECKLIST_SCHEMA_PATH,
        'success': False,
        'summary': {
            'total_items': 0,
            'pass_items': 0,
            'fail_items': 0,
            'waived_items': 0,
            'blocking_failures': 0,
        },
        'errors': [],
        'blocking_items': [],
        'evaluated_items': [],
    }

    if not os.path.exists(CHECKLIST_SCHEMA_PATH):
        result['errors'].append(f'missing schema file: {CHECKLIST_SCHEMA_PATH}')
        return result
    if not os.path.exists(CHECKLIST_JSON_PATH):
        result['errors'].append(f'missing checklist file: {CHECKLIST_JSON_PATH}')
        return result

    try:
        schema = _load_json_file(CHECKLIST_SCHEMA_PATH)
        checklist = _load_json_file(CHECKLIST_JSON_PATH)
    except Exception as err:
        result['errors'].append(f'failed to load checklist/schema JSON: {err}')
        return result

    schema_errors = _validate_checklist_schema(checklist, schema)
    if schema_errors:
        result['errors'].extend(schema_errors)
        return result

    phase_success_map = {run['phase']: run['success'] for run in runs}

    evaluated = []
    for item in checklist['items']:
        status = item.get('status')
        reasons = []
        machine_check = item.get('machine_check')

        if machine_check and status != 'WAIVED':
            check_ok, check_errors = _evaluate_machine_check(machine_check, phase_success_map, item.get('evidence', []))
            status = 'PASS' if check_ok else 'FAIL'
            reasons.extend(check_errors)

        evaluated.append({
            'id': item.get('id'),
            'title': item.get('title'),
            'priority': item.get('priority'),
            'effective_status': status,
            'base_status': item.get('status'),
            'phase_gates': item.get('phase_gates', []),
            'reasons': reasons,
        })

    total_items = len(evaluated)
    pass_items = sum(1 for item in evaluated if item.get('effective_status') == 'PASS')
    fail_items = sum(1 for item in evaluated if item.get('effective_status') == 'FAIL')
    waived_items = sum(1 for item in evaluated if item.get('effective_status') == 'WAIVED')

    blocking_items = []
    for item in evaluated:
        priority = item.get('priority')
        status = item.get('effective_status')
        if priority == 'Must' and status != 'PASS':
            blocking_items.append(item)
        if priority == 'Should' and status == 'FAIL':
            blocking_items.append(item)

    result['summary'] = {
        'total_items': total_items,
        'pass_items': pass_items,
        'fail_items': fail_items,
        'waived_items': waived_items,
        'blocking_failures': len(blocking_items),
    }
    result['blocking_items'] = [
        {
            'id': item.get('id'),
            'priority': item.get('priority'),
            'status': item.get('effective_status'),
            'title': item.get('title'),
        }
        for item in blocking_items
    ]
    result['evaluated_items'] = evaluated
    result['success'] = len(blocking_items) == 0
    return result


def _render_master_report(summary):
    lines = [
        '# Release Gate Burn-in Report (Phase 2 + Phase 3 + Phase 4 + Phase 5)',
        '',
        f"Generated on: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC",
        '',
        '## Scope',
        '- `scripts/burnin_phase2_processing.py`',
        '- `scripts/burnin_phase3_policy.py`',
        '- `scripts/burnin_phase4_policy_workflow.py`',
        '- `scripts/burnin_phase5_transparency.py`',
        '- `docs/release/benchmark_release_checklist.json` (schema-gated + machine checks)',
        '',
        '## Results',
        '',
        '| Phase | Status | Checks | Runner | Report |',
        '|---|---|---:|---|---|',
    ]

    for run in summary['runs']:
        status = 'PASS' if run['success'] else 'FAIL'
        lines.append(
            f"| {run['phase']} | {status} | {run['passed_checks']}/{run['total_checks']} | `{run['script']}` | `{run['report_path']}` |"
        )

    checklist = summary['checklist_gate']
    checklist_status = 'PASS' if checklist['success'] else 'FAIL'
    checks = checklist['summary']
    lines.append(
        f"| {checklist['gate']} | {checklist_status} | {checks['pass_items']}/{checks['total_items']} | `{checklist['checklist_path']}` | `{checklist['schema_path']}` |"
    )

    lines.extend(
        [
            '',
            '## Checklist Gate Summary',
            f"- `success`: `{str(checklist['success']).lower()}`",
            f"- `total_items`: `{checks['total_items']}`",
            f"- `pass_items`: `{checks['pass_items']}`",
            f"- `fail_items`: `{checks['fail_items']}`",
            f"- `waived_items`: `{checks['waived_items']}`",
            f"- `blocking_failures`: `{checks['blocking_failures']}`",
            '',
            '| ID | Priority | Status | Title |',
            '|---|---|---|---|',
        ]
    )

    for item in checklist.get('evaluated_items', []):
        lines.append(
            f"| {item.get('id')} | {item.get('priority')} | {item.get('effective_status')} | {item.get('title')} |"
        )

    lines.extend(
        [
            '',
            '## Overall Gate Decision',
            '**READY FOR RELEASE GATE**' if summary['all_passed'] else '**NOT READY FOR RELEASE GATE**',
            '',
            f"- `all_passed`: `{str(summary['all_passed']).lower()}`",
            f"- `phases_passed`: `{summary['phases_passed']}`",
            f"- `total_phases`: `{summary['total_phases']}`",
            f"- `checklist_gate_passed`: `{str(checklist['success']).lower()}`",
            '',
            '## Notes',
            '- Individual phase reports are regenerated by each phase runner.',
            '- Checklist gate validates schema + machine-check policy for Must/Should/Could items.',
            '- If any phase fails, inspect its script output and report first.',
            '',
        ]
    )

    failing = [run for run in summary['runs'] if not run['success']]
    if failing:
        lines.append('## Phase Failure Snippets')
        for run in failing:
            lines.append(f"- `{run['phase']}` return_code={run['return_code']}")
            if run['stderr_tail']:
                lines.append(f"  - stderr: `{run['stderr_tail'].replace('`', "'")}`")
            elif run['stdout_tail']:
                lines.append(f"  - stdout: `{run['stdout_tail'].replace('`', "'")}`")

    if checklist['errors']:
        lines.append('## Checklist Schema Errors')
        for err in checklist['errors']:
            lines.append(f'- {err}')

    if checklist['blocking_items']:
        lines.append('## Checklist Blocking Items')
        for item in checklist['blocking_items']:
            lines.append(
                f"- `{item['id']}` [{item['priority']}/{item['status']}] {item['title']}"
            )

    reason_items = [item for item in checklist.get('evaluated_items', []) if item.get('reasons')]
    if reason_items:
        lines.append('## Checklist Evaluation Notes')
        for item in reason_items:
            for reason in item.get('reasons', []):
                lines.append(f"- `{item['id']}` {reason}")

    return '\n'.join(lines)


def main():
    python_exe = sys.executable

    runs = []
    for item in PHASE_RUNNERS:
        runs.append(_run_phase_runner(python_exe, item))

    phases_passed = sum(1 for run in runs if run['success'])
    checklist_gate = _evaluate_checklist_gate(runs)
    summary = {
        'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
        'runs': runs,
        'phases_passed': phases_passed,
        'total_phases': len(runs),
        'checklist_gate': checklist_gate,
        'all_passed': phases_passed == len(runs) and checklist_gate['success'],
    }

    os.makedirs(os.path.dirname(MASTER_REPORT_PATH), exist_ok=True)
    with open(MASTER_REPORT_PATH, 'w', encoding='utf-8') as report_file:
        report_file.write(_render_master_report(summary))

    print(json.dumps(summary, indent=2))
    print(f'\nGenerated master report: {MASTER_REPORT_PATH}')

    if not summary['all_passed']:
        sys.exit(1)


if __name__ == '__main__':
    main()