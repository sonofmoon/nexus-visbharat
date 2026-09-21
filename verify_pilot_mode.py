import os
import json
from visbharat import create_app

app = create_app()
app.config.update(
    TESTING=True,
    WEBHOOK_SHARED_TOKEN='visbharat-webhook-token',
    TELEGRAM_WEBHOOK_SECRET='nexus_visbharat_secret_2026',
    META_APP_SECRET='',
    TWILIO_AUTH_TOKEN='',
    WEBHOOK_REQUIRE_REPLAY_PROTECTION=False
)

client = app.test_client()

channels_to_test = [
    {
        "name": "Web Form",
        "url": "/api/submit",
        "headers": {"Content-Type": "application/json"},
        "payload": {
            "source": "Web Form",
            "text": "Water pipeline leaking near Anna Salai Chennai Ward 14",
            "state": "Tamil Nadu",
            "district": "Chennai",
            "language": "en"
        }
    },
    {
        "name": "Voice IVR Missed Call",
        "url": "/api/channels/ivr/missed-call",
        "headers": {"Content-Type": "application/json", "X-Webhook-Token": "visbharat-webhook-token"},
        "payload": {
            "phone": "+919876543210",
            "district": "Tirupati",
            "state": "Andhra Pradesh",
            "language": "te"
        }
    },
    {
        "name": "WhatsApp Bot",
        "url": "/api/channels/whatsapp/webhook",
        "headers": {"Content-Type": "application/json", "X-Webhook-Token": "visbharat-webhook-token"},
        "payload": {
            "entry": [{
                "changes": [{
                    "value": {
                        "messages": [{
                            "from": "918807437931",
                            "text": {"body": "Street light not working in Hyderabad Ward 12"}
                        }]
                    }
                }]
            }]
        }
    },
    {
        "name": "Telegram Bot",
        "url": "/api/channels/telegram/webhook",
        "headers": {"Content-Type": "application/json", "X-Telegram-Bot-Api-Secret-Token": "nexus_visbharat_secret_2026"},
        "payload": {
            "update_id": 88812,
            "message": {
                "message_id": 999,
                "from": {"id": 822098515, "first_name": "Ravi"},
                "chat": {"id": 822098515},
                "text": "Karur bus stand road romba damage ah irukku"
            }
        }
    },
    {
        "name": "SMS Helpline",
        "url": "/api/channels/sms/webhook",
        "headers": {"Content-Type": "application/json", "X-Webhook-Token": "visbharat-webhook-token"},
        "payload": {
            "sender": "+919840123456",
            "text": "NVB Ward 14 Street light damaged",
            "shortcode": "56161"
        }
    },
    {
        "name": "Email & Open API",
        "url": "/api/channels/email/webhook",
        "headers": {"Content-Type": "application/json", "X-Webhook-Token": "visbharat-webhook-token"},
        "payload": {
            "sender": "citizen@visbharat.gov.in",
            "subject": "Garbage dump near Madurai hospital",
            "body": "Accumulated waste causing health hazard in Ward 5"
        }
    }
]

print("=== STARTING PILOT MODE 6-CHANNEL VERIFICATION ===")
all_passed = True

with app.app_context():
    for ch in channels_to_test:
        try:
            resp = client.post(ch["url"], data=json.dumps(ch["payload"]), headers=ch["headers"])
            status = resp.status_code
            data = resp.get_json(silent=True) or {}
            req_id = data.get("request_id") or data.get("callback_job_id") or "N/A"
            success = data.get("success", False)
            if status == 200 and success:
                print(f"[PASS] [{ch['name']}]: SUCCESS (200 OK) -> Ticket ID: {req_id}")
            else:
                print(f"[FAIL] [{ch['name']}]: FAILED ({status}) -> Response: {data}")
                all_passed = False
        except Exception as e:
            print(f"[FAIL] [{ch['name']}]: ERROR -> {e}")
            all_passed = False

if all_passed:
    print("\nALL 6 CHANNELS VERIFIED 100% OPERATIONAL IN PILOT MODE!")
else:
    print("\nSOME CHANNELS EXPERIENCED ISSUES.")
