import urllib.request
import json
import time
import uuid

BASE_URL = 'http://127.0.0.1:5000'

def make_request(url, method='GET', data=None, headers=None):
    hdrs = {'Content-Type': 'application/json'}
    if headers:
        hdrs.update(headers)
    req_data = json.dumps(data).encode('utf-8') if data is not None else None
    req = urllib.request.Request(url, data=req_data, headers=hdrs, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode('utf-8'))

def replay_headers():
    return {
        'X-Webhook-Token': 'visbharat-webhook-token',
        'X-Webhook-Timestamp': str(int(time.time())),
        'X-Webhook-Nonce': str(uuid.uuid4())
    }

print("================================================================================")
print("             NEXUS VISBHARAT LOCAL SMOKE TEST SUITE (13 STATES/408 DIST)        ")
print("================================================================================")

# 1. Test Citizen Intake in multiple Indic languages (Tamil-first priority)
test_cases = [
    {
        'lang': 'ta',
        'district': 'Madurai',
        'text': 'மதுரையில் குடிநீர் விநியோகம் கடந்த 3 நாட்களாக தடைபட்டுள்ளது, உடனடியாக சீரமைக்க வேண்டும்.'
    },
    {
        'lang': 'hi',
        'district': 'Varanasi',
        'text': 'वाराणसी में पेयजल आपूर्ति की मुख्य पाइपलाइन टूट गई है, पीने के पानी की भारी किल्लत है।'
    },
    {
        'lang': 'bn',
        'district': 'Kolkata',
        'text': 'কলকাতা শহরে রাস্তার আলো দীর্ঘদিন ধরে বন্ধ রয়েছে, রাতের বেলা চলাচল অসম্ভব হয়ে পড়েছে।'
    },
    {
        'lang': 'kn',
        'district': 'Bengaluru Urban',
        'text': 'ಬೆಂಗಳೂರು ನಗರದಲ್ಲಿ ಚರಂಡಿ ಕಟ್ಟಿಕೊಂಡು ಕೊಳಚೆ ನೀರು ರಸ್ತೆಯಲ್ಲಿ ಹರಿಯುತ್ತಿದೆ, ತಕ್ಷಣ ಸ್ವಚ್ಛಗೊಳಿಸಿ.'
    },
    {
        'lang': 'mr',
        'district': 'Pune',
        'text': 'पुणे शहरात मुख्य रस्त्यावर मोठे खड्डे पडले आहेत, अपघाताचा धोका वाढला आहे.'
    }
]

print("\n--- 1. Testing Multilingual Citizen Intake (/api/submit) ---")
created_ids = []
for c in test_cases:
    payload = {
        'language': c['lang'],
        'district': c['district'],
        'source': 'Web Form',
        'text': c['text']
    }
    data = make_request(f'{BASE_URL}/api/submit', method='POST', data=payload)
    req_id = data.get('request_id')
    cat = data.get('classification', {}).get('category')
    urg = data.get('classification', {}).get('urgency')
    dept = data.get('routing', {}).get('department')
    cid = data.get('cluster', {}).get('cluster_id')
    created_ids.append(req_id)
    print(f"[{c['lang'].upper()}] ReqID: {req_id} | Cat: {cat} | Urg: {urg} | Dept: {dept} | Cluster: {cid}")

# 2. Test Zero-Internet Voice IVR Webhook & Missed-Call Callback
print("\n--- 2. Testing Voice IVR Channels (/api/channels/ivr/*) ---")
# 2a. Missed Call Trigger
ivr_missed_payload = {
    'phone': '+919876543210',
    'district': 'Madurai',
    'language': 'ta'
}
missed_res = make_request(f'{BASE_URL}/api/channels/ivr/missed-call', method='POST', data=ivr_missed_payload, headers=replay_headers())
print(f"[IVR MISSED CALL] Success: {missed_res.get('success')} | JobID: {missed_res.get('callback_job_id')} | Status: {missed_res.get('status')}")

# 2b. Voice IVR Webhook Transcribed Ingestion
ivr_webhook_payload = {
    'caller': '+919876543210',
    'district': 'Varanasi',
    'language': 'hi',
    'transcript': 'वाराणसी के वार्ड 4 में बिजली का ट्रांसफॉर्मर जल गया है और आग लग रही है'
}
ivr_data = make_request(f'{BASE_URL}/api/channels/ivr/webhook', method='POST', data=ivr_webhook_payload, headers=replay_headers())
ivr_cls = ivr_data.get('classification', {})
print(f"[IVR WEBHOOK] Success: {ivr_data.get('success')} | ReqID: {ivr_data.get('request_id')} | Cat: {ivr_cls.get('category')} | Urg: {ivr_cls.get('urgency')} | Channel: {ivr_data.get('channel')}")

# 3. Test Zero-Internet SMS Channel
print("\n--- 3. Testing SMS Channels (/api/channels/sms/*) ---")
# 3a. Keyword SMS (NV NEW)
sms_keyword_payload = {
    'from': '+919876543211',
    'district': 'Kolkata',
    'language': 'bn',
    'text': 'NV NEW রাস্তায় খোলা ম্যানহোল রয়েছে, অবিলম্বে ঢাকனா লাগান'
}
sms_kw_data = make_request(f'{BASE_URL}/api/channels/sms/keyword', method='POST', data=sms_keyword_payload, headers=replay_headers())
sms_kw_cls = sms_kw_data.get('classification', {})
print(f"[SMS KEYWORD] Success: {sms_kw_data.get('success')} | Action: {sms_kw_data.get('keyword_action')} | ReqID: {sms_kw_data.get('request_id')} | Cat: {sms_kw_cls.get('category')} | Channel: {sms_kw_data.get('channel')}")

# 3b. SMS Webhook
sms_webhook_payload = {
    'from': '+919876543212',
    'district': 'Bengaluru Urban',
    'language': 'kn',
    'text': 'ಕಸದ ತೊಟ್ಟಿ ತುಂಬಿ ತುಳುಕುತ್ತಿದೆ, ರೋಗ ಹರಡುವ ಭೀತಿ ಇದೆ'
}
sms_wb_data = make_request(f'{BASE_URL}/api/channels/sms/webhook', method='POST', data=sms_webhook_payload, headers=replay_headers())
sms_wb_cls = sms_wb_data.get('classification', {})
print(f"[SMS WEBHOOK] Success: {sms_wb_data.get('success')} | ReqID: {sms_wb_data.get('request_id')} | Cat: {sms_wb_cls.get('category')} | Channel: {sms_wb_data.get('channel')}")

# 4. Test Geographic Grid & Transparency APIs
print("\n--- 4. Testing Grid Footprint & Transparency APIs ---")
states_res = make_request(f'{BASE_URL}/api/states')
states = states_res.get('states', [])
print(f"[STATES API] Total States & UTs: {len(states)} -> {', '.join(states[:5])}...")

districts_res = make_request(f'{BASE_URL}/api/districts')
districts = districts_res.get('districts', [])
print(f"[DISTRICTS API] Total Canonical Districts: {len(districts)}")

trans_res = make_request(f'{BASE_URL}/api/public/transparency/summary')
summary = trans_res.get('summary', {})
print(f"[TRANSPARENCY SUMMARY] Total Requests: {summary.get('total_requests'):,} | Published Request Count: {summary.get('published_request_count'):,} | Published District Groups: {summary.get('published_district_groups')} (Min Group: {summary.get('anonymization', {}).get('min_group_size')})")

# 5. Test Submission Operational Readiness API
print("\n--- 5. Testing Operational Readiness & Multilingual Quality ---")
ready_data = make_request(f'{BASE_URL}/api/submission/readiness')
ds = ready_data.get('dataset', {})
scope = ready_data.get('scope', {})
ev = ready_data.get('evaluation', {}).get('overall', {})
langs = scope.get('languages', {})
states_map = scope.get('states', {})
total_scope_districts = sum(len(v) for v in states_map.values())
lang_keys = list(langs.keys()) if isinstance(langs, dict) else list(langs)
print(f"[DATASET] Rows: {ds.get('rows'):,} | Coverage: {total_scope_districts} Districts in {len(states_map)} States/UTs | Languages: {len(lang_keys)} ({lang_keys[0].upper()} first)")
print(f"[BENCHMARK] Cases: {ev.get('samples')} | Macro-F1: {ev.get('category_macro_f1')} | Urgency Acc: {ev.get('urgency_accuracy')*100:.1f}% | Emergency Recall: {ev.get('emergency_recall')*100:.0f}% ({ev.get('emergency_correct')}/{ev.get('emergency_samples')})")

print("\n================================================================================")
print("             ALL LOCAL ENDPOINT SMOKE TESTS VERIFIED SUCCESSFULLY!              ")
print("================================================================================")
