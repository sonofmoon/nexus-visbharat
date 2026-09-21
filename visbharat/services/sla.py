from __future__ import annotations

from datetime import datetime, timezone, timedelta


def _to_int(value, default):
    try:
        return int(value)
    except Exception:
        return int(default)


def compute_sla_due_at(urgency: str, urgency_to_hours: dict | None = None, now: datetime | None = None) -> str:
    mapping = {'Routine': 72, 'Urgent': 24, 'Emergency': 2}
    if isinstance(urgency_to_hours, dict):
        for key, value in urgency_to_hours.items():
            mapping[str(key)] = _to_int(value, mapping.get(str(key), 72))

    issued_at = now or datetime.now(timezone.utc)
    hours = mapping.get(str(urgency or '').strip(), 72)
    due = issued_at + timedelta(hours=max(int(hours), 1))
    return due.isoformat().replace('+00:00', 'Z')


def resolve_sla_policy(
    *,
    urgency: str,
    category: str,
    channel: str,
    routed_department: str,
    urgency_to_hours: dict | None = None,
    sla_rules: dict | None = None,
    escalation_targets: dict | None = None,
    tier2_default_delay_hours: int = 24,
    now: datetime | None = None,
) -> dict:
    base_hours_map = {'Routine': 72, 'Urgent': 24, 'Emergency': 2}
    if isinstance(urgency_to_hours, dict):
        for key, value in urgency_to_hours.items():
            base_hours_map[str(key)] = _to_int(value, base_hours_map.get(str(key), 72))

    normalized_urgency = str(urgency or 'Routine').strip() or 'Routine'
    normalized_category = str(category or 'Other').strip() or 'Other'
    normalized_channel = str(channel or 'Unknown').strip() or 'Unknown'

    hours = _to_int(base_hours_map.get(normalized_urgency, 72), 72)
    policy_mode = 'urgency_default'

    rules_obj = sla_rules if isinstance(sla_rules, dict) else {}
    by_category = rules_obj.get('by_category') if isinstance(rules_obj.get('by_category'), dict) else {}
    by_channel = rules_obj.get('by_channel') if isinstance(rules_obj.get('by_channel'), dict) else {}
    by_category_channel = rules_obj.get('by_category_channel') if isinstance(rules_obj.get('by_category_channel'), dict) else {}

    category_rule = by_category.get(normalized_category) if isinstance(by_category.get(normalized_category), dict) else {}
    if category_rule:
        hours = _to_int(category_rule.get(normalized_urgency, hours), hours)
        policy_mode = 'category'

    channel_rule = by_channel.get(normalized_channel) if isinstance(by_channel.get(normalized_channel), dict) else {}
    if channel_rule:
        hours = _to_int(channel_rule.get(normalized_urgency, hours), hours)
        policy_mode = 'channel'

    category_channel_key = f"{normalized_category}|{normalized_channel}"
    category_channel_rule = by_category_channel.get(category_channel_key) if isinstance(by_category_channel.get(category_channel_key), dict) else {}
    if category_channel_rule:
        hours = _to_int(category_channel_rule.get(normalized_urgency, hours), hours)
        policy_mode = 'category_channel'

    # Life-Safety Emergency Constraint: Emergency SLA is strictly capped at 2 hours
    if normalized_urgency == 'Emergency':
        hours = min(hours, 2)

    tier2_delay_hours = _to_int(rules_obj.get('tier2_delay_hours', tier2_default_delay_hours), tier2_default_delay_hours)

    base_time = now or datetime.now(timezone.utc)
    due_at = (base_time + timedelta(hours=max(hours, 1))).isoformat().replace('+00:00', 'Z')

    if normalized_urgency == 'Emergency' and (not routed_department or routed_department == 'District Grievance Cell'):
        default_target = {
            'department': 'District Disaster Management Authority (DDMA)',
            'assignee': 'Collectorate Rapid Response Desk',
        }
    else:
        default_target = {
            'department': str(routed_department or 'District Grievance Cell'),
            'assignee': 'Duty Officer',
        }
    tier1_target = dict(default_target)
    tier2_target = {
        'department': f"{tier1_target['department']} - Escalation",
        'assignee': 'Senior Duty Officer',
    }

    targets_obj = escalation_targets if isinstance(escalation_targets, dict) else {}
    by_department = targets_obj.get('by_department') if isinstance(targets_obj.get('by_department'), dict) else {}
    by_channel_targets = targets_obj.get('by_channel') if isinstance(targets_obj.get('by_channel'), dict) else {}
    by_department_tier2 = targets_obj.get('by_department_tier2') if isinstance(targets_obj.get('by_department_tier2'), dict) else {}
    by_channel_tier2 = targets_obj.get('by_channel_tier2') if isinstance(targets_obj.get('by_channel_tier2'), dict) else {}

    dep_target = by_department.get(default_target['department']) if isinstance(by_department.get(default_target['department']), dict) else {}
    if dep_target:
        if dep_target.get('department'):
            tier1_target['department'] = str(dep_target['department'])
        if dep_target.get('assignee'):
            tier1_target['assignee'] = str(dep_target['assignee'])

    channel_target = by_channel_targets.get(normalized_channel) if isinstance(by_channel_targets.get(normalized_channel), dict) else {}
    if channel_target:
        if channel_target.get('department'):
            tier1_target['department'] = str(channel_target['department'])
        if channel_target.get('assignee'):
            tier1_target['assignee'] = str(channel_target['assignee'])

    tier2_dep_target = by_department_tier2.get(tier1_target['department']) if isinstance(by_department_tier2.get(tier1_target['department']), dict) else {}
    if tier2_dep_target:
        if tier2_dep_target.get('department'):
            tier2_target['department'] = str(tier2_dep_target['department'])
        if tier2_dep_target.get('assignee'):
            tier2_target['assignee'] = str(tier2_dep_target['assignee'])

    tier2_channel_target = by_channel_tier2.get(normalized_channel) if isinstance(by_channel_tier2.get(normalized_channel), dict) else {}
    if tier2_channel_target:
        if tier2_channel_target.get('department'):
            tier2_target['department'] = str(tier2_channel_target['department'])
        if tier2_channel_target.get('assignee'):
            tier2_target['assignee'] = str(tier2_channel_target['assignee'])

    return {
        'due_at': due_at,
        'hours': int(hours),
        'policy_mode': policy_mode,
        'tier2_delay_hours': max(int(tier2_delay_hours), 1),
        'tier1_target': tier1_target,
        'tier2_target': tier2_target,
    }
