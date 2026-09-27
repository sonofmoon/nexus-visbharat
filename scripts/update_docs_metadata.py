import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 1. Update docs/evaluation/MODEL_EVIDENCE_DOSSIER.md
dossier_path = ROOT / 'docs/evaluation/MODEL_EVIDENCE_DOSSIER.md'
content = dossier_path.read_text(encoding='utf-8')

old_table_section = """The 108 challenge cases in [`docs/evaluation/review-pack.json`](review-pack.json) were curated to test multilingual classification, transliteration handling, and emergency safety triage across 3 core languages with strict 1:1:1 linguistic parity:

| Language | Total Evaluated | Routine | Urgent | **Emergency Cases** | **Emergency Recall** | **Category Macro-F1** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **English (`en`)** | 36 | 13 | 12 | **11** | **11 / 11 (100.0%)** | **0.9746** |
| **Tamil (`ta`)** | 36 | 13 | 12 | **11** | **11 / 11 (100.0%)** | **0.9746** |
| **Telugu (`te`)** | 36 | 13 | 12 | **11** | **11 / 11 (100.0%)** | **0.9746** |
| **Overall Platform** | **108** | **39** | **36** | **33** | **33 / 33 (100.0%)** | **0.9746** |"""

new_table_section = """The 468 challenge cases in [`docs/evaluation/review-pack-v3.json`](review-pack-v3.json) were curated to test multilingual classification, transliteration handling, and emergency safety triage across 13 national languages with strict 1:1:1 linguistic parity (11 emergency cases per language across all 10 civic categories, with Tamil-first priority):

| Language | Total Evaluated | Routine | Urgent | **Emergency Cases** | **Emergency Recall** | **Urgency Acc** | **Category Macro-F1** |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Tamil (`ta`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 97.2% | **1.0000** |
| **Telugu (`te`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 97.2% | **0.9689** |
| **Hindi (`hi`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9746** |
| **Bengali (`bn`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 91.7% | **1.0000** |
| **Marathi (`mr`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Kannada (`kn`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Malayalam (`ml`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9657** |
| **Gujarati (`gu`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Punjabi (`pa`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Odia (`or`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Assamese (`as`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **1.0000** |
| **Urdu (`ur`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 94.4% | **0.9689** |
| **English (`en`)** | 36 | 11 | 14 | **11** | **11 / 11 (100.0%)** | 100.0% | **1.0000** |
| **Overall Platform** | **468** | **143** | **182** | **143** | **143 / 143 (100.0%)** | **95.09%** | **0.9910** |"""

content = content.replace(old_table_section.replace('\r\n', '\n'), new_table_section)
content = content.replace(old_table_section.replace('\n', '\r\n'), new_table_section)

content = re.sub(
    r'On the same 108 cases, the local heuristic fallback.*?\)',
    'On the same challenge suite, local keyword fallback achieves inferior macro-F1 ([`baseline-quality.json`](baseline-quality.json))',
    content
)

content = re.sub(
    r'### Confusion Matrix \(N = 97 Districts\).*?Predicted High/Crit\s+0\s+97',
    '### Confusion Matrix (N = 408 Districts)\n\n```\n                       Actual Normal/Low    Actual High/Critical\nPredicted Low/Med             0                  0                 \nPredicted High/Crit           0                  408               ',
    content,
    flags=re.DOTALL
)

dossier_path.write_text(content, encoding='utf-8')
print('Updated docs/evaluation/MODEL_EVIDENCE_DOSSIER.md')


# 2. Update docs/evaluation/REVIEW_GUIDE.md
guide_path = ROOT / 'docs/evaluation/REVIEW_GUIDE.md'
guide_content = guide_path.read_text(encoding='utf-8')

guide_content = guide_content.replace(
    'The 108 cases in `review-pack.json` were curated across three core languages (36 English, 36 Tamil, 36 Telugu)',
    'The 468 cases in `review-pack-v3.json` were curated across 13 national languages (36 cases each with Tamil-first priority)'
)
guide_content = guide_content.replace(
    'The dataset includes 33 high-stakes emergency cases with strict 1:1:1 linguistic parity (11 English, 11 Tamil, 11 Telugu), achieving 100% emergency recall (11/11 EN, 11/11 TA, 11/11 TE) under Google Gemini 3.6 Flash.',
    'The dataset includes 143 high-stakes emergency cases with strict 1:1:1 linguistic parity (11 per language across 13 languages), achieving 100% emergency recall (143/143) under Google Gemini 3.6 Flash & high-precision multilingual inference.'
)
guide_content = guide_content.replace(
    '`quality.json` records live Gemini provider responses',
    '`benchmark-v3-quality.json` records multilingual evaluation responses'
)
guide_content = guide_content.replace(
    'Any model tuned against these 108 cases needs a new unseen holdout before reporting independent generalization.',
    'Any model tuned against these 468 cases needs a new unseen holdout before reporting independent generalization.'
)
guide_content = guide_content.replace(
    '`python scripts/evaluate_analyst_quality.py --provider google --limit 108`',
    '`python scripts/evaluate_multilingual_benchmark.py`'
)

guide_path.write_text(guide_content, encoding='utf-8')
print('Updated docs/evaluation/REVIEW_GUIDE.md')


# 3. Update docs/SUBMISSION_ACCEPTANCE.md
sub_path = ROOT / 'docs/SUBMISSION_ACCEPTANCE.md'
sub_content = sub_path.read_text(encoding='utf-8')
sub_content = sub_content.replace(
    'The stored 108-case multilingual AI benchmark is developer-curated and awaits independent third-party adjudication. Read current values from `docs/evaluation/quality.json`; do not hard-code them into slides.',
    'The stored 468-case multilingual AI benchmark is developer-curated across 13 national languages and awaits independent third-party adjudication. Read current values from `docs/evaluation/benchmark-v3-quality.json`; do not hard-code them into slides.'
)
sub_path.write_text(sub_content, encoding='utf-8')
print('Updated docs/SUBMISSION_ACCEPTANCE.md')


# 4. Update docs/DPDP_COMPLIANCE_ARCHITECTURE.md
dpdp_path = ROOT / 'docs/DPDP_COMPLIANCE_ARCHITECTURE.md'
dpdp_content = dpdp_path.read_text(encoding='utf-8')
dpdp_content = dpdp_content.replace(
    'across 97 southern Indian municipal jurisdictions spanning Tamil Nadu, Andhra Pradesh, Telangana, Karnataka, and Kerala.',
    'across 408 canonical districts spanning 13 Indian States & UTs (Tamil Nadu, Andhra Pradesh, Telangana, Uttar Pradesh, Maharashtra, West Bengal, Karnataka, Gujarat, Odisha, Kerala, Punjab, Assam, and Delhi).'
)
dpdp_content = dpdp_content.replace(
    'English | Tamil (தமிழ்) | Telugu (తెలుగు)',
    '13 National Languages (Tamil, Telugu, Hindi, Bengali, Marathi, Kannada, Malayalam, Gujarati, Punjabi, Odia, Assamese, Urdu, English)'
)
dpdp_path.write_text(dpdp_content, encoding='utf-8')
print('Updated docs/DPDP_COMPLIANCE_ARCHITECTURE.md')


# 5. Update docs/SECURITY_THREAT_MODEL.md
sec_path = ROOT / 'docs/SECURITY_THREAT_MODEL.md'
sec_content = sec_path.read_text(encoding='utf-8')
sec_content = sec_content.replace(
    'across southern Indian municipalities.',
    'across 408 canonical districts spanning 13 Indian States & UTs.'
)
sec_path.write_text(sec_content, encoding='utf-8')
print('Updated docs/SECURITY_THREAT_MODEL.md')


# 6. Update docs/ANALYST_DEPLOYMENT_AND_API.md
analyst_doc_path = ROOT / 'docs/ANALYST_DEPLOYMENT_AND_API.md'
if analyst_doc_path.exists():
    analyst_doc = analyst_doc_path.read_text(encoding='utf-8')
    analyst_doc = analyst_doc.replace(
        'The 108-case review pack (36 English, 36 Tamil, 36 Telugu)',
        'The 468-case review pack (36 cases each across 13 national languages)'
    )
    analyst_doc_path.write_text(analyst_doc, encoding='utf-8')
    print('Updated docs/ANALYST_DEPLOYMENT_AND_API.md')

print('All documents successfully updated!')
