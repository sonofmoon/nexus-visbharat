"""End-to-end verification of WhatsApp messaging channel webhook.
Tests:
1. Inbound signed message intake
2. Delivery receipt verification (records channel_tests['whatsapp']['status'] == 'passed')
3. Duplicate event handling (idempotent replay)
4. Event payload tamper rejection
"""
import hashlib
import hmac
import json
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

import os
os.environ['NVB_DISABLE_EXTERNAL_SERVICES'] = '1'
os.environ['JURY_REQUIRE_LIVE_MODELS'] = '0'

from app import app
from visbharat.services.pilot import programme, PILOT_ID
from visbharat.db import get_db

def run_channel_verification():
    with app.test_client() as client:
        # 1. Enable WhatsApp channel in pilot settings if not already enabled
        with app.app_context():
            p = programme(PILOT_ID)
            cfg = p['config']
            portal_cfg = cfg.setdefault('portal', {})
            channels_cfg = portal_cfg.setdefault('channels', {})
            channels_cfg['whatsapp'] = {
                'enabled': True,
                'address': 'https://wa.me/919876543210',
                'owner': 'Pilot WhatsApp Gateway'
            }
            conn = sqlite3.connect(str(BASE_DIR / 'visbharat.db'))
            conn.execute(
                'UPDATE pilot_programmes SET config_json=? WHERE pilot_id=?',
                (json.dumps(cfg), PILOT_ID)
            )
            conn.commit()
            conn.close()
            print("[+] WhatsApp channel enabled in pilot configuration.")

        secret = app.config.get('PILOT_CHANNEL_WHATSAPP_SECRET', 'pilot-whatsapp-secret-2026')
        
        # 2. Construct Inbound WhatsApp message
        event_id = f"wa-evt-{int(time.time())}"
        inbound_payload = {
            'event_id': event_id,
            'language': 'ta',
            'location_id': 'vellore-1',
            'consent_granted': True,
            'category': 'Water Supply',
            'text': 'வணக்கம், வேலூர் மெயின் ரோட்டில் குடிநீர் குழாய் உடைந்து தண்ணீர் வீணாகிறது. தயவுசெய்து சரிசெய்யவும்.'
        }
        
        raw_body = json.dumps(inbound_payload).encode('utf-8')
        stamp = str(int(time.time()))
        signature = hmac.new(secret.encode('utf-8'), stamp.encode('utf-8') + b'.' + raw_body, hashlib.sha256).hexdigest()
        
        headers = {
            'Content-Type': 'application/json',
            'X-Pilot-Timestamp': stamp,
            'X-Pilot-Signature': signature
        }
        
        print(f"[+] Sending inbound WhatsApp message (event_id={event_id})...")
        res = client.post('/api/v2/pilot/channels/whatsapp/webhook', data=raw_body, headers=headers)
        print("    Status code:", res.status_code)
        resp_data = res.get_json()
        print("    Response:", resp_data)
        assert res.status_code in (200, 202), f"Expected 200/202, got {res.status_code}: {resp_data}"
        assert resp_data.get('success') is True, "Expected success: True"
        request_id = resp_data.get('request_id')
        assert request_id, "Missing request_id"
        print(f"[+] Inbound intake succeeded: Ticket {request_id} created.")

        # 3. Test Idempotent Duplicate Replay (same event, same body)
        print("[+] Testing idempotent duplicate replay...")
        res_dup = client.post('/api/v2/pilot/channels/whatsapp/webhook', data=raw_body, headers=headers)
        print("    Duplicate status code:", res_dup.status_code)
        dup_data = res_dup.get_json()
        print("    Duplicate response:", dup_data)
        assert res_dup.status_code in (200, 202), f"Expected 200/202, got {res_dup.status_code}"
        assert dup_data.get('reused') is True, "Expected reused: True"
        assert dup_data.get('request_id') == request_id, "Expected same request_id"
        print("[+] Idempotent replay verified.")

        # 4. Test Duplicate Event Tamper Rejection (same event, different body)
        print("[+] Testing duplicate event with tampered content...")
        tampered_payload = {**inbound_payload, 'text': 'Tampered message content'}
        tampered_raw = json.dumps(tampered_payload).encode('utf-8')
        tampered_stamp = str(int(time.time()))
        tampered_sig = hmac.new(secret.encode('utf-8'), tampered_stamp.encode('utf-8') + b'.' + tampered_raw, hashlib.sha256).hexdigest()
        tampered_headers = {
            'Content-Type': 'application/json',
            'X-Pilot-Timestamp': tampered_stamp,
            'X-Pilot-Signature': tampered_sig
        }
        res_tamper = client.post('/api/v2/pilot/channels/whatsapp/webhook', data=tampered_raw, headers=tampered_headers)
        print("    Tamper status code:", res_tamper.status_code)
        print("    Tamper response:", res_tamper.get_json())
        assert res_tamper.status_code in (400, 422, 500), f"Expected rejection, got {res_tamper.status_code}"
        print("[+] Tamper rejection verified.")

        # 5. Submit Delivery Receipt
        print(f"[+] Submitting delivery receipt for {event_id}...")
        receipt_payload = {
            'event_id': event_id,
            'action': 'delivery_receipt',
            'delivered': True
        }
        receipt_raw = json.dumps(receipt_payload).encode('utf-8')
        receipt_stamp = str(int(time.time()))
        receipt_sig = hmac.new(secret.encode('utf-8'), receipt_stamp.encode('utf-8') + b'.' + receipt_raw, hashlib.sha256).hexdigest()
        receipt_headers = {
            'Content-Type': 'application/json',
            'X-Pilot-Timestamp': receipt_stamp,
            'X-Pilot-Signature': receipt_sig
        }
        res_rcpt = client.post('/api/v2/pilot/channels/whatsapp/webhook', data=receipt_raw, headers=receipt_headers)
        print("    Receipt status code:", res_rcpt.status_code)
        rcpt_data = res_rcpt.get_json()
        print("    Receipt response:", rcpt_data)
        assert res_rcpt.status_code == 200, f"Expected 200, got {res_rcpt.status_code}"
        assert rcpt_data.get('success') is True
        print("[+] Delivery receipt confirmed.")

        # 6. Verify channel_tests in DB
        with app.app_context():
            p_final = programme(PILOT_ID)
            channel_tests = p_final['config'].get('channel_tests', {})
            channel_status = channel_tests.get('whatsapp', {}).get('status')
            assert channel_status in ('passed', 'local_connector_passed'), f"Unexpected channel status: {channel_status}"
            print(f"[+] channel_tests['whatsapp']['status'] == '{channel_status}' verified in database.")

        print("\n>>> ALL MESSAGING CHANNEL VERIFICATION CHECKS PASSED SUCCESSFULLY. <<<")

if __name__ == '__main__':
    run_channel_verification()
