import sys
import os
import time
import logging
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SECRET_TOKEN = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "nexus_visbharat_secret_2026")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
WEBHOOK_URL = os.environ.get("TELEGRAM_INTERNAL_WEBHOOK_URL", "http://127.0.0.1:5000/api/channels/telegram/webhook")

def delete_webhook():
    try:
        resp = requests.get(f"{BASE_URL}/deleteWebhook", timeout=10)
        logging.info(f"deleteWebhook result: {resp.json()}")
    except Exception as e:
        logging.error(f"Error deleting webhook: {e}")

def send_message(chat_id, text, reply_markup=None):
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
    except Exception as e:
        logging.error(f"Error sending message to {chat_id}: {e}")

def process_update(update):
    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return
    
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    from_user = msg.get("from") or {}
    user_name = from_user.get("first_name", "Citizen")

    if not chat_id:
        return

    logging.info(f"Received message from {user_name} ({chat_id}): '{text}'")

    if text.startswith("/start"):
        start_msg = (
            f"👋 *Vanakkam & Welcome {user_name}!*\n\n"
            f"🏛️ *Nexus-VisBharath (NVB) Omni-Channel Municipal Portal*\n"
            f"Zero-Internet Citizen Access & Request Routing Engine.\n\n"
            f"✍️ *How to submit a Request:*\n"
            f"Simply type your municipal demand in *English, Tamil, or Tanglish*!\n"
            f"_Example_: `Street light not working in Ward 14` or `Road romba damage ah irukku Anna Nagar-la`\n\n"
            f"📞 *Zero-Internet Toll-Free Hotline*: `1800-103-8472` (Give a Missed Call)\n"
            f"💬 *WhatsApp Bot*: +91 88074 37931"
        )
        send_message(chat_id, start_msg)
        return

    if text.startswith("/help"):
        help_msg = (
            f"ℹ️ *Nexus-VisBharath Help & Info*\n\n"
            f"• *Submit Request*: Type your demand directly in chat.\n"
            f"• *Languages Supported*: English, Tamil (தமிழ்), Tanglish (Code-Mixed).\n"
            f"• *Toll-Free Helpline*: `1800-103-8472`\n"
            f"• *AI Engines*: Google Gemini 1.5 Flash, Dialogflow CX & Vertex AI."
        )
        send_message(chat_id, help_msg)
        return

    if text.startswith("/status"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            send_message(chat_id, "🔍 *Status Check*\nPlease provide your Request ID.\n_Example_: `/status NVB-202609141234`")
            return
        req_id = parts[1].strip()
        try:
            res = requests.get(f"http://127.0.0.1:5000/api/requests/{req_id}", timeout=5)
            if res.status_code == 200 and res.json().get("success"):
                d = res.json().get("data", {})
                status_msg = (
                    f"📌 *Request Status*: `{req_id}`\n\n"
                    f"🏛️ *Category*: {d.get('category')}\n"
                    f"⚡ *Urgency*: {d.get('urgency_score')}\n"
                    f"🔄 *Current State*: *{d.get('status', 'In Progress')}*\n"
                    f"🏢 *Assigned Ward*: {d.get('assigned_ward')}"
                )
                send_message(chat_id, status_msg)
            else:
                send_message(chat_id, f"⚠️ Request ID `{req_id}` not found in VisBharath records.")
        except Exception as e:
            send_message(chat_id, "⚠️ System momentarily busy. Please try again.")
        return

    # Forward message payload to NVB Webhook
    payload = {
        "update_id": update.get("update_id"),
        "message": msg
    }
    headers = {
        "Content-Type": "application/json",
        "X-Telegram-Bot-Api-Secret-Token": SECRET_TOKEN
    }
    try:
        resp = requests.post(WEBHOOK_URL, json=payload, headers=headers, timeout=10)
        logging.info(f"Webhook forward response ({resp.status_code}): {resp.text}")
    except Exception as e:
        logging.error(f"Error forwarding to webhook: {e}")
        send_message(chat_id, "⚠️ Failed to register request with VisBharath backend. Please retry.")

def main():
    delete_webhook()
    offset = None
    logging.info("Starting Telegram Bot Poller for @NexusVisBharatBot...")
    while True:
        try:
            url = f"{BASE_URL}/getUpdates?timeout=20"
            if offset:
                url += f"&offset={offset}"
            resp = requests.get(url, timeout=25)
            if resp.status_code == 200:
                data = resp.json()
                if data.get("ok"):
                    for item in data.get("result", []):
                        offset = item["update_id"] + 1
                        process_update(item)
            else:
                logging.warning(f"getUpdates status {resp.status_code}: {resp.text}")
        except Exception as e:
            logging.error(f"Poller error: {e}")
            time.sleep(3)

if __name__ == "__main__":
    main()
