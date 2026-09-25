"""Controlled legal and audit-source registry used by the Auditor Workbench.

The registry deliberately separates a source published by an authority from the
application's screening interpretation.  It is provenance metadata, not a legal
opinion or a compliance certificate.
"""
from copy import deepcopy


SOURCE_REGISTRY = {
    'gfr2017_rule130': {
        'source_id': 'gfr2017_rule130',
        'title': 'General Financial Rules, 2017',
        'publisher': 'Department of Expenditure, Ministry of Finance, Government of India',
        'section': 'Rule 130; preliminary surveys and technical sanction',
        'version': 'Official compilation/update reference through 31 January 2026',
        'official_url': 'https://doe.gov.in/bi-annual-compilationupdation-general-financial-rules-2017-upto-31012026general-financial-rules',
        'document_url': 'https://doe.gov.in/files/circulars_document/UpdatedGFR31July2025_0.pdf',
        'verification_status': 'official_publisher_reference',
    },
    'gfr2017_rule138': {
        'source_id': 'gfr2017_rule138',
        'title': 'General Financial Rules, 2017',
        'publisher': 'Department of Expenditure, Ministry of Finance, Government of India',
        'section': 'Rule 138; payment against measured and certified work',
        'version': 'Official compilation/update reference through 31 January 2026',
        'official_url': 'https://doe.gov.in/bi-annual-compilationupdation-general-financial-rules-2017-upto-31012026general-financial-rules',
        'document_url': 'https://doe.gov.in/files/circulars_document/UpdatedGFR31July2025_0.pdf',
        'verification_status': 'official_publisher_reference',
    },
    'cvc_vigilance_manual': {
        'source_id': 'cvc_vigilance_manual',
        'title': 'Vigilance Manual',
        'publisher': 'Central Vigilance Commission, Government of India',
        'section': 'Technical vetting and vigilance controls; verify the applicable paragraph in the current edition',
        'version': 'Updated 2021 manual; current official index should be checked before reliance',
        'official_url': 'https://cvc.gov.in/?q=guidelines/vigilance-manual',
        'document_url': 'https://npcc.gov.in/writereaddata/others/CVC%20Manual%202021%20%28updated%29.pdf',
        'verification_status': 'official_index_reference_document_host_external',
    },
    'cag_performance_audit': {
        'source_id': 'cag_performance_audit',
        'title': 'Performance Audit Guidelines and manuals',
        'publisher': 'Comptroller and Auditor General of India',
        'section': 'Data authenticity, audit evidence and provenance; select the applicable current manual',
        'version': 'Current edition must be selected from the official manuals catalogue',
        'official_url': 'https://cag.gov.in/en/manuals',
        'document_url': 'https://cag.gov.in/en/manuals',
        'verification_status': 'official_catalog_reference',
    },
    'dpdpa2023_section6': {
        'source_id': 'dpdpa2023_section6',
        'title': 'Digital Personal Data Protection Act, 2023',
        'publisher': 'Ministry of Law and Justice / Ministry of Electronics and Information Technology, Government of India',
        'section': 'Section 6; notice and consent requirements',
        'version': 'Act No. 22 of 2023; commencement is notification-dependent',
        'official_url': 'https://www.indiacode.nic.in/indiacode/handle/123456789/22037?view_type=browse',
        'document_url': 'https://www.meity.gov.in/static/uploads/2024/02/Digital-Personal-Data-Protection-Act-2023.pdf',
        'verification_status': 'official_statute_reference',
    },
}


def source(source_id):
    """Return a defensive copy so callers cannot mutate the registry."""
    item = SOURCE_REGISTRY.get(source_id)
    return deepcopy(item) if item else None


def sources(source_ids=None):
    ids = list(source_ids or SOURCE_REGISTRY)
    return [source(source_id) for source_id in ids if source(source_id)]


def citation(source_id, citation_text=None):
    item = source(source_id)
    if not item:
        return {'source_id': source_id, 'citation': citation_text or source_id, 'verification_status': 'unregistered'}
    item['citation'] = citation_text or f"{item['title']} — {item['section']}"
    return item


PROVENANCE_NOTICE = (
    'Source links identify official publisher or catalogue references. Application findings are '
    'screening interpretations and do not certify legal compliance, lawful processing, or an official audit opinion.'
)
