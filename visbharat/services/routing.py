from __future__ import annotations

import re
from typing import Any


_CATEGORY_TO_SERVICE_DEFAULT = {
    'Road': 'roads',
    'Water Supply': 'water',
    'Electricity': 'electricity',
    'Health': 'health',
    'Education': 'education',
    'Sanitation': 'sanitation',
    'Digital Connectivity': 'digital',
    'Transport': 'transport',
    'Housing': 'housing',
    'Other': 'general',
}

_SERVICE_TO_DEPARTMENT_DEFAULT = {
    'roads': 'Public Works Department',
    'water': 'Water Board',
    'electricity': 'Electricity Board',
    'health': 'Health Department',
    'education': 'Education Department',
    'sanitation': 'Sanitation Department',
    'digital': 'IT Department',
    'transport': 'Transport Department',
    'housing': 'Housing Board',
    'general': 'District Grievance Cell',
}


def _normalize_key(value: Any) -> str:
    return str(value or '').strip().lower()


def _extract_ward(ward: str | None, text: str | None) -> str:
    explicit = str(ward or '').strip()
    if explicit:
        return explicit

    body = str(text or '')
    match = re.search(r'\bward\s*[-:]?\s*([a-z0-9]+)\b', body, flags=re.IGNORECASE)
    if not match:
        return ''
    return match.group(1).strip()


def resolve_ward_department_route(
    *,
    category: str,
    district: str,
    state: str,
    text: str = '',
    ward: str = '',
    matrix: dict | None = None,
    category_to_service: dict | None = None,
    service_to_department: dict | None = None,
) -> dict:
    state_key = str(state or '').strip()
    district_key = str(district or '').strip()
    normalized_category = str(category or 'Other').strip() or 'Other'
    ward_value = _extract_ward(ward, text)

    service_map = dict(_CATEGORY_TO_SERVICE_DEFAULT)
    service_map.update(category_to_service or {})
    service = str(service_map.get(normalized_category) or 'general')

    default_department_map = dict(_SERVICE_TO_DEPARTMENT_DEFAULT)
    default_department_map.update(service_to_department or {})
    department = str(default_department_map.get(service) or 'District Grievance Cell')
    mode = 'default'

    matrix_obj = matrix if isinstance(matrix, dict) else {}
    state_obj = matrix_obj.get(state_key) if isinstance(matrix_obj.get(state_key), dict) else {}
    district_obj = state_obj.get(district_key) if isinstance(state_obj.get(district_key), dict) else {}

    service_dept = district_obj.get('service_to_department') if isinstance(district_obj.get('service_to_department'), dict) else {}
    matched_service_dept = service_dept.get(service)
    if isinstance(matched_service_dept, str) and matched_service_dept.strip():
        department = matched_service_dept.strip()
        mode = 'district_service'

    ward_matrix = district_obj.get('ward_to_service_department') if isinstance(district_obj.get('ward_to_service_department'), dict) else {}
    ward_obj = ward_matrix.get(ward_value) if ward_value and isinstance(ward_matrix.get(ward_value), dict) else {}
    matched_ward_department = ward_obj.get(service)
    if isinstance(matched_ward_department, str) and matched_ward_department.strip():
        department = matched_ward_department.strip()
        mode = 'ward_service'

    return {
        'state': state_key,
        'district': district_key,
        'ward': ward_value,
        'category': normalized_category,
        'service': service,
        'department': department,
        'routing_mode': mode,
    }
