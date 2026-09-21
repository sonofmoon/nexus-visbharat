"""Bounded public-data snapshots. Retrieval is not independent verification.

Refresh explicitly with scripts/refresh_public_data.py. Officer page loads use
snapshots by default and never depend on a remote API being available.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import requests
from flask import current_app, has_app_context


SOURCES = {
    'ulb': ('DATA_GOV_IN_TN_ULB_RESOURCE', 'Urban local body population', 'Tamil Nadu Commissionerate of Municipal Administration', 'https://www.data.gov.in/resource/population-distribution-across-all-urban-local-bodies-tamil-nadu', 'urban_local_body', 2011),
    'lgd': ('DATA_GOV_IN_LGD_RESOURCE', 'LGD local bodies with PIN codes', 'Ministry of Panchayati Raj', 'https://www.data.gov.in/catalog/local-government-directory-lgd', 'local_body', None),
    'sbm': ('DATA_GOV_IN_VELLORE_SBM_RESOURCE', 'Vellore rural sanitation coverage', 'Ministry of Jal Shakti', 'https://www.data.gov.in/catalog/daily-data-rural-sanitation-coverage-under-swachh-bharat-mission', 'gram_panchayat', None),
    'jjm_funds': ('DATA_GOV_IN_JJM_FUNDS_RESOURCE', 'JJM state fund allocation and utilisation', 'Rajya Sabha / Ministry of Jal Shakti', 'https://www.data.gov.in/catalog/jal-jeevan-mission-jjm', 'state', None),
    'amrut': ('DATA_GOV_IN_TIRUPATI_AMRUT_RESOURCE', 'AMRUT city project records', 'Ministry of Housing and Urban Affairs / Rajya Sabha', 'https://www.data.gov.in/resource/statecity-wise-details-projects-sanctioned-under-amrut', 'city', None),
    'jjm_rural': ('DATA_GOV_IN_TIRUPATI_JJM_RESOURCE', 'JJM rural habitation records', 'Ministry of Jal Shakti', 'https://www.data.gov.in/catalog/jal-jeevan-mission-jjm', 'habitation', None),
    'ndap': ('NDAP_API_URL', 'NDAP flood management indicators', 'NITI Aayog', 'https://ndap.niti.gov.in/', 'state', None),
}

# Values are API field names, not invented rows. Override per source after
# inspecting that resource's schema; a missing field stays missing.
FIELDS = {
    'ulb': {'city':'name_of_the_ulb', 'population':'_total', 'male':'male', 'female':'female', 'women_pct':'_of_women_population', 'sc_pct':'_of_sc_population', 'st_pct':'_of_st_population'},
    'lgd': {'state':'state_name', 'district':'district_name', 'city':'local_body_name', 'state_code':'state_code', 'district_code':'district_code', 'local_body_code':'local_body_code', 'local_body_type':'local_body_type', 'pincode':'pincode'},
    'sbm': {'state':'statename', 'district':'districtname', 'block':'blockname', 'panchayat':'grampanchayatname', 'target':'ihhltotalasperdetails', 'achieved':'ihhltotalach'},
    'jjm_funds': {'state':'state_ut', 'available':'_2024_25___available_fund', 'utilized':'_2024_25___reported_utilization', 'state_share':'_2024_25___expenditure_under_state_share'},
    'amrut': {'state':'state_name', 'city':'city_name', 'project_id':'project_id', 'cost':'total_project_cost_in_cr_'},
    'jjm_rural': {'state':'state_name', 'district':'district_name', 'mandal':'mandal_name', 'habitation':'habitation_name', 'connections':'fhtc_reported'},
    'ndap': {'state':'StateName', 'state_code':'StateCode', 'year':'Year', 'projects':'I1805_3', 'area':'I1805_4', 'beneficiaries':'I1805_5'},
}


def config(name, default=None):
    return current_app.config.get(name, default) if has_app_context() else default


def field(source, key):
    overrides = config('PUBLIC_DATA_FIELD_MAPS', {})
    return (overrides.get(source, {}) if isinstance(overrides, dict) else {}).get(key, FIELDS[source].get(key))


def value(source, row, key):
    return row.get(field(source, key))


def number(value):
    if isinstance(value, dict):
        value = value.get('sum')
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(str(value).replace(',', '').strip())
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _now():
    return datetime.now(timezone.utc).isoformat()


def _path(source):
    directory = config('PUBLIC_DATA_SNAPSHOT_DIR')
    return Path(directory) / (source + '.json') if directory else None


def _base(source):
    setting, title, publisher, url, granularity, year = SOURCES[source]
    resource = str(config(setting, '') or '').strip()
    if source == 'ndap':
        resource = 'I1805_3,I1805_4,I1805_5'
    return dict(source=source, resource_id=resource, title=title, publisher=publisher,
                url=url, granularity=granularity, observation_year=year,
                status='not_refreshed', verified_live=False, publisher_verified=False,
                retrieval_verification='not_retrieved',
                publisher_verification='not_independently_verified',
                field_validation='not_completed',
                retrieved_at=None, source_updated_at=None, sha256=None, complete=False,
                records=[], record_count=0, error=None,
                license_status='Confirm resource-specific reuse terms and attribution',
                boundary_status='Source geography must be reconciled to current boundaries')


def _decode(raw):
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError('Unexpected response schema')
    rows = payload.get('records', payload.get('Data'))
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Expected a records array')
    return payload, rows


def _read(source):
    result = _base(source)
    path = _path(source)
    if not path or not path.is_file():
        return result
    try:
        envelope = json.loads(path.read_text(encoding='utf-8'))
        raw = envelope['payload']
        checksum = hashlib.sha256(raw.encode('utf-8')).hexdigest()
        if envelope['sha256'] != checksum or envelope['resource_id'] != result['resource_id']:
            raise ValueError('Snapshot checksum or source mismatch')
        payload, rows = _decode(raw)
        retrieved = datetime.fromisoformat(envelope['retrieved_at'])
        age = (datetime.now(timezone.utc)-retrieved).total_seconds()
        if age < -60:
            raise ValueError('Future retrieval timestamp')
        result.update(status='cached' if age <= config('PUBLIC_DATA_MAX_AGE_SECONDS', 86400) else 'stale',
                      retrieved_at=envelope['retrieved_at'], sha256=checksum,
                      source_updated_at=payload.get('updated_date'), records=rows,
                      record_count=len(rows), complete=bool(envelope.get('complete')),
                      retrieval_verification='snapshot_integrity_checked')
    except (OSError, ValueError, KeyError, TypeError):
        result.update(status='invalid_snapshot', error='Snapshot integrity or schema check failed')
    return result


def load(source, refresh=False):
    """Use a persisted snapshot; external refresh is explicit and opt-in."""
    result = _read(source)
    if not refresh:
        return result
    if config('DISABLE_EXTERNAL_SERVICES', False) or os.environ.get('NVB_DISABLE_EXTERNAL_SERVICES') == '1':
        result['error'] = 'External services disabled'
        return result
    setting = SOURCES[source][0]
    credential = config('NDAP_API_KEY' if source == 'ndap' else 'DATA_GOV_IN_API_KEY', '')
    endpoint = str(config(setting, '') or '').strip()
    if not credential or not endpoint:
        result.update(error='Configure the server-side credential and resource before refresh')
        if not result['records']:
            result['status'] = 'not_configured'
        return result
    if source == 'ndap':
        parsed = urlparse(endpoint)
        allowed = config('NDAP_ALLOWED_HOSTS', [])
        if parsed.scheme != 'https' or parsed.hostname not in allowed or parsed.username or parsed.query or parsed.fragment:
            result.update(status='configuration_error', error='NDAP requires an explicitly approved HTTPS API host')
            return result
    else:
        if not re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', endpoint):
            result.update(status='configuration_error', error='Use the API resource UUID, not a catalog/node number')
            return result
        endpoint = 'https://api.data.gov.in/resource/' + endpoint
    try:
        records, seen, total, complete, updated = [], set(), None, False, None
        max_pages = min(max(int(config('PUBLIC_DATA_MAX_PAGES', 10)), 1), 100)
        for page in range(max_pages):
            params = ({'API_Key':credential, 'ind':result['resource_id'], 'dim':'Country,StateName,StateCode,Year', 'pageno':page+1}
                      if source == 'ndap' else {'api-key':credential, 'format':'json', 'offset':page*1000, 'limit':1000})
            # No redirect may forward a credential; no hidden retry loop.
            response = requests.get(endpoint, params=params, timeout=(3, 10), allow_redirects=False)
            if response.status_code != 200 or len(response.content) > 8*1024*1024:
                raise ValueError('API unavailable or response too large')
            payload, rows = _decode(response.text)
            updated = payload.get('updated_date', updated)
            reported_total = number(payload.get('total', payload.get('TotalRecords')))
            if reported_total is not None:
                if total is not None and total != int(reported_total):
                    raise ValueError('Dataset changed during pagination; refresh again')
                total = int(reported_total)
            if not rows:
                complete = total is None or len(records) == total
                break
            fingerprint = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
            if fingerprint in seen:
                raise ValueError('Repeated page; pagination is not trustworthy')
            seen.add(fingerprint)
            records.extend(rows)
            if total is not None and len(records) >= total:
                complete = len(records) == total
                break
            if source != 'ndap' and len(rows) < 1000 and total is None:
                complete = True
                break
        # Hash exactly the retained, normalized snapshot, not a resource ID.
        raw = json.dumps({'records':records, 'updated_date':updated}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        result.update(status='retrieved' if complete else 'partial', verified_live=False,
                      records=records, record_count=len(records), retrieved_at=_now(),
                      source_updated_at=updated, complete=complete, error=None,
                      retrieval_verification='retrieved_from_configured_api_not_independently_verified',
                      sha256=hashlib.sha256(raw.encode('utf-8')).hexdigest())
        path = _path(source)
        if path:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(path.name + '.' + uuid4().hex + '.tmp')
            try:
                temporary.write_text(json.dumps({'resource_id':result['resource_id'], 'retrieved_at':result['retrieved_at'],
                                                'sha256':result['sha256'], 'complete':complete, 'payload':raw}), encoding='utf-8')
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return result
    except (requests.RequestException, OSError, ValueError, TypeError, KeyError):
        # Do not expose URLs or exception strings that may contain credentials.
        result.update(error='Refresh failed; retained snapshot is explicitly dated')
        if result['records']:
            result['status'] = 'stale'
        else:
            result['status'] = 'unavailable'
        return result


def select(source, **filters):
    result = load(source)
    rows = result['records']
    for key, expected in filters.items():
        if expected is not None and str(expected).strip():
            rows = [row for row in rows if str(value(source, row, key) or '').strip().casefold() == str(expected).strip().casefold()]
    return {**result, 'records':rows, 'matched_count':len(rows)}


def summary(source, filters, mapping):
    result = select(source, **filters)
    result.update({key:None for key in mapping})
    # Ambiguous, missing, partial or stale records must not become one official fact.
    if len(result['records']) == 1 and result['complete'] and result['status'] in ('retrieved', 'cached'):
        row = result['records'][0]
        result.update({out:number(value(source, row, key)) for out,key in mapping.items()})
    return result


def registry():
    result = []
    for key in SOURCES:
        data = load(key)
        rows = []
        for raw in data['records']:
            row = {name:value(key, raw, name) for name in FIELDS[key]}
            # Do not turn city or state observations into district/ward evidence.
            row['geographic_level'] = data['granularity']
            rows.append(row)
        result.append({**data, 'source':'public_data_' + key, 'name':data['title'],
                       'loaded':bool(data['records']), 'file':str(_path(key)) if _path(key) else None,
                       'records':len(rows), 'rows':rows})
    return result


def statuses():
    return [{key:row.get(key) for key in ('source','name','status','resource_id','records','retrieved_at',
                                       'source_updated_at','sha256','complete','error','publisher_verified',
                                       'retrieval_verification','publisher_verification','field_validation')}
            for row in registry()]
