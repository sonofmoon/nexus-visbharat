import sys
import os
import time
import logging
import json
import requests
from uuid import uuid4
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
SECRET_TOKEN = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "nexus_visbharat_secret_2026")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}" if TOKEN else ""
WEBHOOK_URL = os.environ.get("TELEGRAM_INTERNAL_WEBHOOK_URL", "http://127.0.0.1:5000/api/channels/telegram/webhook")

SUBMIT_URL = os.environ.get("TELEGRAM_INTERNAL_SUBMIT_URL", WEBHOOK_URL.replace("/api/channels/telegram/webhook", "/api/submit"))
VOICE_URL = os.environ.get("TELEGRAM_INTERNAL_VOICE_URL", WEBHOOK_URL.replace("/api/channels/telegram/webhook", "/api/submit-voice"))
SESSION_URL = os.environ.get("TELEGRAM_INTERNAL_SESSION_URL", WEBHOOK_URL.replace("/api/channels/telegram/webhook", "/api/channels/telegram/session"))

STRINGS = json.loads((Path(__file__).resolve().parent / 'static' / 'data' / 'assistant_i18n.json').read_text(encoding='utf-8-sig'))
LANGUAGES = {lang: {'welcome': STRINGS[lang]['welcome'], 'location': STRINGS[lang]['location'], 'review': STRINGS[lang]['review'], 'privacy': STRINGS[lang]['notice'] + ' ' + STRINGS[lang]['consent'], 'saved': STRINGS[lang]['saved']} for lang in ('en', 'ta', 'te')}
CHAT_SESSIONS = {}

def _get_session(chat_id):
    cid = str(chat_id)
    if cid in CHAT_SESSIONS:
        return CHAT_SESSIONS[cid]
    try:
        resp = requests.get(f"{SESSION_URL}/{cid}", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET_TOKEN}, timeout=4)
        if resp.ok:
            data = resp.json()
            if data.get("session") and isinstance(data["session"], dict) and data["session"].get("stage"):
                CHAT_SESSIONS[cid] = data["session"]
                return data["session"]
    except Exception:
        pass
    new_sess = {"stage": "language", "nonce": uuid4().hex[:12]}
    CHAT_SESSIONS[cid] = new_sess
    _save_session(cid, new_sess)
    return new_sess

def _save_session(chat_id, session):
    cid = str(chat_id)
    CHAT_SESSIONS[cid] = session
    try:
        requests.post(f"{SESSION_URL}/{cid}", json=session, headers={"X-Telegram-Bot-Api-Secret-Token": SECRET_TOKEN}, timeout=4)
    except Exception:
        pass

def _clear_session(chat_id):
    cid = str(chat_id)
    CHAT_SESSIONS.pop(cid, None)
    try:
        requests.delete(f"{SESSION_URL}/{cid}", headers={"X-Telegram-Bot-Api-Secret-Token": SECRET_TOKEN}, timeout=4)
    except Exception:
        pass

def _keyboard(rows): return {"inline_keyboard":[[{"text":label,"callback_data":action} for label,action in row] for row in rows]}
def _send_callback_answer(callback_id):
 if BASE_URL and callback_id:
  try: requests.post(f"{BASE_URL}/answerCallbackQuery",json={"callback_query_id":callback_id},timeout=5)
  except requests.RequestException: pass

def _start(chat_id):
 session={"stage":"language","nonce":uuid4().hex[:12]}
 _save_session(chat_id, session)
 send_message(chat_id,"Welcome to Nexus VisBharat - choose your language:",_keyboard([[ ("Tamil","lang:ta"),("Telugu","lang:te"),("English","lang:en") ]]))

def _submit_draft(chat_id,session):
 common={"language":session["language"],"district":session["location"],"ward":session["location"],"consent_granted":True,"consent_scope":"request_processing","sender":str(chat_id),"source":"NexusVisBharatBot"}
 headers={"X-Telegram-Bot-Api-Secret-Token":SECRET_TOKEN,"X-Idempotency-Key":f"telegram-{chat_id}-{session['nonce']}"}
 try:
  if session.get("voice_file_id"):
   meta=requests.get(f"{BASE_URL}/getFile",params={"file_id":session["voice_file_id"]},timeout=10).json(); path=(meta.get("result") or {}).get("file_path")
   audio=requests.get(f"https://api.telegram.org/file/bot{TOKEN}/{path}",timeout=30).content
   response=requests.post(VOICE_URL,files={"audio":("telegram.ogg",audio,"audio/ogg")},data={**common,"is_voice":"true"},headers=headers,timeout=30)
  else:
   response=requests.post(SUBMIT_URL,json={**common,"text":session["issue"]},headers=headers,timeout=15)
  result=response.json()
  if response.ok and result.get("success") and result.get("request_id"):
   session.update(stage="complete",request_id=result["request_id"])
   _save_session(chat_id, session)
   send_message(chat_id,LANGUAGES[session["language"]]["saved"].format(ticket=result["request_id"]))
  else: send_message(chat_id,"I could not register this report yet. Your draft is retained; please try again.")
 except (requests.RequestException,ValueError,KeyError): send_message(chat_id,"The service is temporarily unavailable. Your draft is retained; please try again.")

def _callback(update):
 cb=update.get("callback_query") or {}
 if not cb:return False
 chat_id=((cb.get("message") or {}).get("chat") or {}).get("id"); _send_callback_answer(cb.get("id"))
 if not chat_id:return True
 action=str(cb.get("data") or ""); session=_get_session(chat_id)
 if action.startswith("lang:") and action[5:] in LANGUAGES:
  session.update(language=action[5:],stage="issue")
  _save_session(chat_id, session)
  send_message(chat_id,LANGUAGES[session["language"]]["welcome"])
 elif action=="confirm" and session.get("stage")=="review":
  session["stage"]="privacy"
  _save_session(chat_id, session)
  send_message(chat_id,LANGUAGES[session["language"]]["privacy"],_keyboard([[ ("I consent","consent") ],[("Cancel","cancel")]]))
 elif action=="consent" and session.get("stage")=="privacy": _submit_draft(chat_id,session)
 elif action=="edit":
  session["stage"]="issue"
  _save_session(chat_id, session)
  send_message(chat_id,"Please send the corrected issue description by text or voice.")
 elif action=="cancel":
  _clear_session(chat_id)
  send_message(chat_id,"Draft cancelled. No report was submitted.")
 return True

def delete_webhook():
    if not BASE_URL:
        return
    try:
        resp = requests.get(f"{BASE_URL}/deleteWebhook", timeout=10)
        logging.info(f"deleteWebhook result: {resp.json()}")
    except Exception as e:
        logging.error(f"Error deleting webhook: {e}")

def send_message(chat_id, text, reply_markup=None):
    if not BASE_URL:
        logging.info(f"[Dry Run] Would send to {chat_id}: {text}")
        return
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
 if _callback(update): return
 msg=update.get("message") or update.get("edited_message")
 if not msg:return
 chat_id=(msg.get("chat") or {}).get("id") or (msg.get("from") or {}).get("id")
 if not chat_id:return
 text=(msg.get("text") or "").strip()
 if text.startswith("/start"): _start(chat_id); return
 if text.startswith("/help"):
  send_message(chat_id,"Choose Tamil, Telugu or English, describe an infrastructure issue by text or voice, provide the locality, review privacy, and confirm to receive a reference ID. AI assists routing; authorized human officials approve projects.")
  return
 if text.startswith("/status"):
  parts=text.split(maxsplit=1)
  if len(parts)<2: send_message(chat_id,"Please provide your reference ID. Example: /status NVB-202609141234"); return
  try:
   response=requests.get(f"http://127.0.0.1:5000/api/requests/{parts[1].strip()}/track",timeout=5); data=response.json()
   send_message(chat_id,f"Reference ID: {parts[1].strip()}\nStatus: {data.get('status','Not found')}" if response.ok and data.get("success") else "No request matches that reference ID.")
  except (requests.RequestException,ValueError): send_message(chat_id,"The status service is temporarily unavailable. Please try again.")
  return
 session=CHAT_SESSIONS.setdefault(str(chat_id),{"stage":"language","nonce":uuid4().hex[:12]})
 if session.get("stage")=="language": _start(chat_id)
 elif session.get("stage")=="issue":
  voice_id=(msg.get("voice") or {}).get("file_id")
  if voice_id: session.update(issue="Voice report",voice_file_id=voice_id,stage="location")
  elif text: session.update(issue=text,stage="location")
  else: send_message(chat_id,"Please send a text report or voice note."); return
  send_message(chat_id,LANGUAGES[session["language"]]["location"])
 elif session.get("stage")=="location":
  if not text: send_message(chat_id,LANGUAGES[session["language"]]["location"]); return
  session.update(location=text,stage="review"); strings=LANGUAGES[session["language"]]
  send_message(chat_id,strings["review"].format(issue=session["issue"],location=text),_keyboard([[ ("Confirm & submit","confirm"),("Edit report","edit") ],[("Cancel","cancel")]]))
 elif session.get("stage") in {"review","privacy"}: send_message(chat_id,"Please use the review buttons to confirm, edit, or cancel.")
 else: send_message(chat_id,"This report is already registered. Use /status <reference ID>, or /start for a new report.")

def main():
    # Webhook delivery is the production path.  Keep polling available only
    # as an explicitly enabled local fallback; an accidental poller must not
    # delete the production webhook or compete for the same Telegram updates.
    if os.environ.get("TELEGRAM_ENABLE_LEGACY_POLLER", "").strip().lower() not in {"1", "true", "yes"}:
        logging.error("Legacy Telegram polling is disabled. Use /api/channels/telegram/webhook or set TELEGRAM_ENABLE_LEGACY_POLLER=true for local fallback.")
        return
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

