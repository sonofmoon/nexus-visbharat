"""Durable Telegram webhook gateway for @NexusVisBharatBot.

Telegram delivers an update at least once. This module keeps the inbound
update ledger and outbound send queue in SQL so a web process restart does not
lose a citizen draft or create a second ticket.

Supports all 13 states/UTs, 408 canonical LGD districts, and 13 languages
with Tamil strictly prioritized first.
"""

import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import requests
from flask import current_app, g

from ..db import delete_channel_session, get_channel_session, get_db, save_channel_session
from ..config import PILOT_STATE_TO_DISTRICTS

LOGGER = logging.getLogger(__name__)


def _get_strings():
    try:
        path = Path(__file__).resolve().parents[2] / "static" / "data" / "assistant_i18n.json"
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        LOGGER.warning("Failed to load dynamic assistant_i18n.json: %s", exc)
        return {}


STRINGS = _get_strings()

ORDERED_LANG_CODES = ("ta", "te", "hi", "bn", "mr", "kn", "ml", "gu", "pa", "or", "as", "ur", "en")

PROCESSING_TOAST = {
    "ta": "⏳ செயலாக்குகிறது...",
    "te": "⏳ ప్రాసెస్ చేస్తోంది...",
    "hi": "⏳ प्रक्रिया जारी है...",
    "bn": "⏳ প্রক্রিয়াকরণ হচ্ছে...",
    "mr": "⏳ प्रक्रिया सुरू आहे...",
    "kn": "⏳ ಪ್ರಕ್ರಿಯೆಗೊಳಿಸಲಾಗುತ್ತಿದೆ...",
    "ml": "⏳ പ്രോസസ്സ് ചെയ്യുന്നു...",
    "gu": "⏳ પ્રક્રિયા ચાલુ છે...",
    "pa": "⏳ ਪ੍ਰਕਿਰਿਆ ਜਾਰੀ ਹੈ...",
    "or": "⏳ ପ୍ରକ୍ରିୟାକରଣ ଚାଲିଛି...",
    "as": "⏳ প্ৰক্ৰিয়াকৰণ চলি আছে...",
    "ur": "⏳ پروسیسنگ ہو رہی ہے...",
    "en": "⏳ Processing...",
}

SUBMITTING_INTERIM = {
    "ta": "⏳ உங்கள் கோரிக்கை பதிவு செய்யப்படுகிறது. AI சரிபார்ப்பு மற்றும் வகைப்படுத்தல் நடைபெறுகிறது...",
    "te": "⏳ మీ అభ్యర్థన నమోదు చేయబడుతోంది. AI పరిశీలన మరియు వర్గీకరణ కొనసాగుతోంది...",
    "hi": "⏳ आपका अनुरोध दर्ज किया जा रहा है। AI सत्यापन और वर्गीकरण प्रगति पर है...",
    "bn": "⏳ আপনার অনুরোধ নথিভুক্ত করা হচ্ছে। AI যাচাইকরণ প্রক্রিয়াধীন...",
    "mr": "⏳ तुमची विनंती नोंदवली जात आहे. AI पडताळणी आणि वर्गीकरण सुरू आहे...",
    "kn": "⏳ ನಿಮ್ಮ ವಿನಂತಿಯನ್ನು ನೋಂದಾಯಿಸಲಾಗುತ್ತಿದೆ. AI ಪರಿಶೀಲನೆ ಪ್ರಗತಿಯಲ್ಲಿದೆ...",
    "ml": "⏳ നിങ്ങളുടെ അപേക്ഷ രേഖപ്പെടുത്തുന്നു. AI പരിശോധന പുരോഗമിക്കുന്നു...",
    "gu": "⏳ તમારી વિનંતી નોંધાઈ રહી છે. AI ચકાસણી અને વર્ગીકરણ પ્રક્રિયામાં છે...",
    "pa": "⏳ ਤੁਹਾਡੀ ਬੇਨਤੀ ਦਰਜ ਕੀਤੀ ਜਾ ਰਹੀ ਹੈ। AI ਪੜਤਾਲ ਜਾਰੀ ਹੈ...",
    "or": "⏳ ଆପଣଙ୍କ ଅନୁରୋଧ ପଞ୍ଜୀକୃତ ହେଉଛି। AI ଯାଞ୍ଚ ପ୍ରକ୍ରିୟା ଜାରି ରହିଛି...",
    "as": "⏳ আপোনাৰ অনুৰোধ পঞ্জীয়ন কৰা হৈছে। AI পৰীক্ষণ চলি আছে...",
    "ur": "⏳ آپ کی درخواست درج کی جا رہی ہے۔ AI تصدیق جاری ہے...",
    "en": "⏳ Registering your request. AI verification and classification in progress...",
}

def _processing_toast(lang):
    return PROCESSING_TOAST.get(lang, PROCESSING_TOAST["en"])

def _submitting_interim(lang):
    return SUBMITTING_INTERIM.get(lang, SUBMITTING_INTERIM["en"])


FLOW_TEXT = {
    'en': {'location': 'Confirm this location, or change it:', 'change': 'Change location',
           'ward': 'Add ward / area (optional)', 'voice': 'Recording received. Transcription is pending; you will review the words before submitting.',
           'queued': 'Request received and saved for processing. Reference: {ticket}. AI processing is pending. Use /status {ticket}.',
           'needs_attention': 'Processing could not finish. Reference: {ticket}. Your request is retained for operator review; please do not submit it again.'},
    'ta': {'location': 'இந்த இடத்தை உறுதிசெய்யவும் அல்லது மாற்றவும்:', 'change': 'இடத்தை மாற்று',
           'ward': 'வார்டு / பகுதி சேர்க்கவும் (விருப்பம்)', 'voice': 'குரல் பதிவு பெறப்பட்டது. எழுத்தாக்கம் நிலுவையில் உள்ளது; சமர்ப்பிக்கும் முன் உரையைச் சரிபார்க்கலாம்.',
           'queued': 'கோரிக்கை பெறப்பட்டுச் செயலாக்கத்திற்காகச் சேமிக்கப்பட்டது. குறிப்பு: {ticket}. AI செயலாக்கம் நிலுவையில் உள்ளது. /status {ticket}',
           'needs_attention': 'செயலாக்கம் முடியவில்லை. குறிப்பு: {ticket}. உங்கள் கோரிக்கை அலுவலர் பரிசீலனைக்காக உள்ளது; மீண்டும் சமர்ப்பிக்க வேண்டாம்.'},
    'te': {'location': 'ఈ ప్రాంతాన్ని నిర్ధారించండి లేదా మార్చండి:', 'change': 'ప్రాంతాన్ని మార్చండి',
           'ward': 'వార్డు / ప్రాంతం జోడించండి (ఐచ్ఛికం)', 'voice': 'వాయిస్ రికార్డింగ్ అందింది. వచనంగా మార్చడం పెండింగ్‌లో ఉంది; సమర్పించే ముందు వచనాన్ని సమీక్షించవచ్చు.',
           'queued': 'మీ అభ్యర్థన ప్రాసెసింగ్ కోసం సేవ్ చేయబడింది. సూచన: {ticket}. AI ప్రాసెసింగ్ పెండింగ్‌లో ఉంది. /status {ticket}',
           'needs_attention': 'ప్రాసెసింగ్ పూర్తి కాలేదు. సూచన: {ticket}. మీ అభ్యర్థన అధికారి సమీక్ష కోసం ఉంచబడింది; మళ్లీ సమర్పించవద్దు.'},
    'kn': {'location': 'ಈ ಸ್ಥಳವನ್ನು ದೃಢೀಕರಿಸಿ ಅಥವಾ ಬದಲಾಯಿಸಿ:', 'change': 'ಸ್ಥಳ ಬದಲಾಯಿಸಿ',
           'ward': 'ವಾರ್ಡ್ / ಪ್ರದೇಶ ಸೇರಿಸಿ (ಐಚ್ಛಿಕ)', 'voice': 'ಧ್ವನಿ ರೆಕಾರ್ಡಿಂಗ್ ಸ್ವೀಕರಿಸಲಾಗಿದೆ. ಪಠ್ಯ ಪರಿವರ್ತನೆ ಬಾಕಿಯಿದೆ; ಸಲ್ಲಿಸುವ ಮೊದಲು ಪಠ್ಯವನ್ನು ಪರಿಶೀಲಿಸಬಹುದು.',
           'queued': 'ವಿನಂತಿಯನ್ನು ಪ್ರಕ್ರಿಯೆಗಾಗಿ ಉಳಿಸಲಾಗಿದೆ. ಉಲ್ಲೇಖ: {ticket}. AI ಪ್ರಕ್ರಿಯೆ ಬಾಕಿಯಿದೆ. /status {ticket}',
           'needs_attention': 'ಪ್ರಕ್ರಿಯೆ ಪೂರ್ಣಗೊಳ್ಳಲಿಲ್ಲ. ಉಲ್ಲೇಖ: {ticket}. ನಿಮ್ಮ ವಿನಂತಿಯನ್ನು ಅಧಿಕಾರಿ ಪರಿಶೀಲನೆಗಾಗಿ ಉಳಿಸಲಾಗಿದೆ; ಮತ್ತೆ ಸಲ್ಲಿಸಬೇಡಿ.'},
    'hi': {'location': 'इस स्थान की पुष्टि करें या इसे बदलें:', 'change': 'स्थान बदलें',
           'ward': 'वार्ड / क्षेत्र जोड़ें (वैकल्पिक)', 'voice': 'वॉइस रिकॉर्डिंग प्राप्त हुई। लिप्यंतरण लंबित है; जमा करने से पहले आप पाठ की समीक्षा कर सकेंगे।',
           'queued': 'आपका अनुरोध प्रक्रिया के लिए सहेजा गया है। संदर्भ: {ticket}। AI प्रक्रिया लंबित है। /status {ticket}',
           'needs_attention': 'प्रक्रिया पूरी नहीं हुई। संदर्भ: {ticket}। अनुरोध अधिकारी की समीक्षा के लिए सुरक्षित है; कृपया दोबारा जमा न करें।'},
}


def _flow(lang, key):
    if key == 'confirm_location':
        return {'ta': 'இடத்தை உறுதிசெய்', 'te': 'ప్రాంతాన్ని నిర్ధారించండి', 'kn': 'ಸ್ಥಳ ದೃಢೀಕರಿಸಿ',
                'hi': 'स्थान की पुष्टि करें'}.get(lang, 'Confirm location')
    return FLOW_TEXT.get(lang, FLOW_TEXT['en'])[key]


def _after_issue(chat_id, session):
    """Suggest only unambiguous known locations, always requiring confirmation."""
    text = str(session.get('issue') or '').casefold()
    matches = set()
    aliases = {'வேலூர்': ('Tamil Nadu', 'Vellore'), 'வேலூரி': ('Tamil Nadu', 'Vellore'), 'తిరుపతి': ('Andhra Pradesh', 'Tirupati'),
               'ಬೆಂಗಳೂರು': ('Karnataka', 'Bengaluru Urban')}
    for state, districts in PILOT_STATE_TO_DISTRICTS.items():
        for district in districts:
            if re.search(r'(?<!\w)' + re.escape(district.casefold()) + r'(?!\w)', text):
                matches.add((state, district))
    for alias, pair in aliases.items():
        if alias in text:
            matches.add(pair)
    previous = (session.get('state'), session.get('district'))
    if len(matches) == 1:
        suggestion = next(iter(matches))
    elif not matches and previous[1] in PILOT_STATE_TO_DISTRICTS.get(previous[0], []):
        suggestion = previous
    else:
        suggestion = None
    session['consent_granted'] = False
    if suggestion:
        session.update(stage='location_confirm', suggested_state=suggestion[0], suggested_district=suggestion[1])
        _save(chat_id, session)
        lang = _lang(session)
        _text(chat_id, f"{_flow(lang, 'location')}\n📍 {suggestion[1]}, {suggestion[0]}",
              _keyboard([[(_flow(lang, 'confirm_location'), 'location:confirm'), (_flow(lang, 'change'), 'location:change')]]))
    else:
        session['stage'] = 'state'
        _save(chat_id, session)
        _text(chat_id, _prompt(_lang(session), 'select_state'), _keyboard(STATE_KEYBOARD))

LANGUAGE_KEYBOARD = [
    [("தமிழ் (Tamil)", "lang:ta"), ("తెలుగు (Telugu)", "lang:te")],
    [("हिन्दी (Hindi)", "lang:hi"), ("বাংলা (Bengali)", "lang:bn")],
    [("मराठी (Marathi)", "lang:mr"), ("ಕನ್ನಡ (Kannada)", "lang:kn")],
    [("മലയാളം (Malayalam)", "lang:ml"), ("ગુજરાતી (Gujarati)", "lang:gu")],
    [("ਪੰਜਾਬੀ (Punjabi)", "lang:pa"), ("ଓଡ଼ିଆ (Odia)", "lang:or")],
    [("অসমীয়া (Assamese)", "lang:as"), ("اردو (Urdu)", "lang:ur")],
    [("English", "lang:en")],
]

STATE_KEYBOARD = [
    [("Tamil Nadu", "state:Tamil Nadu"), ("Andhra Pradesh", "state:Andhra Pradesh")],
    [("Telangana", "state:Telangana"), ("Kerala", "state:Kerala")],
    [("Karnataka", "state:Karnataka"), ("Maharashtra", "state:Maharashtra")],
    [("Gujarat", "state:Gujarat"), ("Odisha", "state:Odisha")],
    [("West Bengal", "state:West Bengal"), ("Punjab", "state:Punjab")],
    [("Assam", "state:Assam"), ("Uttar Pradesh", "state:Uttar Pradesh")],
    [("Delhi", "state:Delhi")],
]

# Major regional hub buttons for each of the 13 states/UTs (all strictly canonical)
DISTRICT_KEYBOARDS = {
    "Tamil Nadu": [
        [("Vellore", "dist:Vellore"), ("Ranipet", "dist:Ranipet")],
        [("Tirupathur", "dist:Tirupathur"), ("Chennai", "dist:Chennai")],
        [("Coimbatore", "dist:Coimbatore"), ("Salem", "dist:Salem")],
        [("Madurai", "dist:Madurai"), ("Tiruchirappalli", "dist:Tiruchirappalli")],
    ],
    "Andhra Pradesh": [
        [("Tirupati", "dist:Tirupati"), ("Chittoor", "dist:Chittoor")],
        [("Visakhapatnam", "dist:Visakhapatnam"), ("Guntur", "dist:Guntur")],
        [("NTR (Vijayawada)", "dist:NTR"), ("Kurnool", "dist:Kurnool")],
    ],
    "Telangana": [
        [("Hyderabad", "dist:Hyderabad"), ("Warangal", "dist:Warangal")],
        [("Medchal-Malkajgiri", "dist:Medchal-Malkajgiri"), ("Karimnagar", "dist:Karimnagar")],
        [("Nizamabad", "dist:Nizamabad"), ("Ranga Reddy", "dist:Ranga Reddy")],
    ],
    "Kerala": [
        [("Thiruvananthapuram", "dist:Thiruvananthapuram"), ("Ernakulam", "dist:Ernakulam")],
        [("Kozhikode", "dist:Kozhikode"), ("Thrissur", "dist:Thrissur")],
        [("Kollam", "dist:Kollam"), ("Palakkad", "dist:Palakkad")],
    ],
    "Karnataka": [
        [("Bengaluru Urban", "dist:Bengaluru Urban"), ("Mysuru", "dist:Mysuru")],
        [("Belagavi", "dist:Belagavi"), ("Dakshina Kannada", "dist:Dakshina Kannada")],
        [("Dharwad", "dist:Dharwad"), ("Kalaburagi", "dist:Kalaburagi")],
    ],
    "Maharashtra": [
        [("Mumbai City", "dist:Mumbai City"), ("Mumbai Suburban", "dist:Mumbai Suburban")],
        [("Pune", "dist:Pune"), ("Nagpur", "dist:Nagpur")],
        [("Thane", "dist:Thane"), ("Nashik", "dist:Nashik")],
    ],
    "Gujarat": [
        [("Ahmedabad", "dist:Ahmedabad"), ("Surat", "dist:Surat")],
        [("Vadodara", "dist:Vadodara"), ("Rajkot", "dist:Rajkot")],
        [("Bhavnagar", "dist:Bhavnagar"), ("Gandhinagar", "dist:Gandhinagar")],
    ],
    "Odisha": [
        [("Khordha", "dist:Khordha"), ("Cuttack", "dist:Cuttack")],
        [("Ganjam", "dist:Ganjam"), ("Puri", "dist:Puri")],
        [("Sambalpur", "dist:Sambalpur"), ("Balasore", "dist:Balasore")],
    ],
    "West Bengal": [
        [("Kolkata", "dist:Kolkata"), ("North 24 Parganas", "dist:North 24 Parganas")],
        [("Howrah", "dist:Howrah"), ("Darjeeling", "dist:Darjeeling")],
        [("South 24 Parganas", "dist:South 24 Parganas"), ("Hooghly", "dist:Hooghly")],
    ],
    "Punjab": [
        [("Ludhiana", "dist:Ludhiana"), ("Amritsar", "dist:Amritsar")],
        [("Jalandhar", "dist:Jalandhar"), ("Patiala", "dist:Patiala")],
        [("Bathinda", "dist:Bathinda"), ("Hoshiarpur", "dist:Hoshiarpur")],
    ],
    "Assam": [
        [("Kamrup Metropolitan", "dist:Kamrup Metropolitan"), ("Dibrugarh", "dist:Dibrugarh")],
        [("Cachar", "dist:Cachar"), ("Jorhat", "dist:Jorhat")],
        [("Nagaon", "dist:Nagaon"), ("Kamrup", "dist:Kamrup")],
    ],
    "Uttar Pradesh": [
        [("Lucknow", "dist:Lucknow"), ("Kanpur Nagar", "dist:Kanpur Nagar")],
        [("Varanasi", "dist:Varanasi"), ("Prayagraj", "dist:Prayagraj")],
        [("Agra", "dist:Agra"), ("Gautam Buddha Nagar", "dist:Gautam Buddha Nagar")],
        [("Meerut", "dist:Meerut"), ("Ghaziabad", "dist:Ghaziabad")],
    ],
    "Delhi": [
        [("New Delhi", "dist:New Delhi"), ("Central Delhi", "dist:Central Delhi")],
        [("South Delhi", "dist:South Delhi"), ("North Delhi", "dist:North Delhi")],
        [("East Delhi", "dist:East Delhi"), ("West Delhi", "dist:West Delhi")],
    ],
}

# District aliases for common informal names across pilot states
DISTRICT_ALIASES = {
    "trichy": "Tiruchirappalli",
    "tiruchi": "Tiruchirappalli",
    "tanjore": "Thanjavur",
    "madras": "Chennai",
    "tuticorin": "Thoothukudi",
    "vizag": "Visakhapatnam",
    "waltair": "Visakhapatnam",
    "vijayawada": "NTR",
    "bezawada": "NTR",
    "calicut": "Kozhikode",
    "cochin": "Ernakulam",
    "trivandrum": "Thiruvananthapuram",
    "bangalore": "Bengaluru Urban",
    "bengaluru": "Bengaluru Urban",
    "mysore": "Mysuru",
    "mangalore": "Dakshina Kannada",
    "bombay": "Mumbai City",
    "mumbai": "Mumbai City",
    "calcutta": "Kolkata",
    "baroda": "Vadodara",
    "banaras": "Varanasi",
    "kashi": "Varanasi",
    "allahabad": "Prayagraj",
    "kanpur": "Kanpur Nagar",
    "noida": "Gautam Buddha Nagar",
    "greater noida": "Gautam Buddha Nagar",
    "mohali": "Sahibzada Ajit Singh Nagar",
    "sas nagar": "Sahibzada Ajit Singh Nagar",
    "rangareddy": "Ranga Reddy",
    "delhi": "New Delhi",
}

# Localized conversational prompts for all 13 languages (Tamil first)
BOT_PROMPTS = {
    "ta": {
        "greeting": (
            "🏛️ வணக்கம்! Nexus VisBharat (@NexusVisBharatBot) மக்கள் உதவி மையத்திற்கு வரவேற்கிறோம்.\n"
            "பொறுப்புமிக்க மாவட்ட நிர்வாகத்திற்கான டிஜிட்டல் பொது உள்கட்டமைப்பு\n\n"
            "குடிநீர், சாலைகள், சுகாதாரம், மின்சாரம் போன்ற பொதுப் பிரச்சனைகளை உங்கள் சொந்த மொழியில் "
            "குரல் பதிவு, புகைப்படம் அல்லது தட்டச்சு மூலம் பதிவு செய்யலாம்.\n\n"
            "📌 முக்கிய கட்டளைகள் (Core Commands):\n"
            "• /start — புதிய புகாரைத் தொடங்க / மீட்டமைக்க\n"
            "• /language — மொழியை மாற்ற\n"
            "• /status [குறிப்பு எண்] — புகாரின் நிலையை அறிய\n"
            "• /cancel — வரைவை ரத்து செய்ய\n"
            "• /help — உதவி மற்றும் வழிகாட்டுதல்\n\n"
            "👇 தயவுசெய்து உங்கள் விருப்ப மொழியைத் தேர்ந்தெடுக்கவும்:"
        ),
        "select_state": "உங்கள் மாநிலம் அல்லது யூனியன் பிரதேசத்தைத் தேர்ந்தெடுக்கவும்:",
        "select_district": "மாநிலம்: {state}\n\nகீழேயுள்ள பொத்தான்களில் உங்கள் மாவட்டத்தைத் தேர்ந்தெடுக்கவும், அல்லது மாவட்டத்தின் பெயரை நேரடியாக தட்டச்சு செய்யவும்:",
        "district_not_found": "'{text}' என்ற மாவட்டம் {state} மாநிலத்தில் கண்டறியப்படவில்லை. தயவுசெய்து கீழேயுள்ள பொத்தான்களில் இருந்து தேர்வு செய்யவும் அல்லது சரியான மாவட்டப் பெயரை தட்டச்சு செய்யவும்:",
        "enter_ward": "மாவட்டம்: {district}\n\nஉங்கள் வார்டு எண், கிராமம் அல்லது பகுதியின் பெயரை உள்ளிடவும் (அல்லது 'தவிர்க்கவும்' என்பதை அழுத்தவும்):",
        "issue_label": "புகார் விவரம்",
        "location_label": "இடம்",
        "confirm_btn": "✓ உறுதிசெய்து சமர்ப்பிக்கவும்",
        "edit_btn": "✏ திருத்து",
        "cancel_btn": "✖ ரத்து செய்",
        "skip_btn": "தவிர்க்கவும் (Skip)",
        "consent_btn": "✓ ஒப்புதல் அளித்து சமர்ப்பிக்கவும்",
        "track_btn": "🔍 நிலையை அறிய",
        "new_btn": "➕ புதிய புகார்",
        "track_tip": "உங்கள் புகாரின் தற்போதைய நிலையை எப்போது வேண்டுமானாலும் அறிய /status {ticket} என அனுப்பவும்.",
        "cancelled_msg": "வரைவு ரத்து செய்யப்பட்டது. புதிய புகாரைப் பதிவு செய்ய /start என அனுப்பவும்.",
        "help": "Nexus VisBharat மக்கள் உதவி மையம் (@NexusVisBharatBot):\n\n1. /start - உங்கள் மொழியைத் தேர்ந்தெடுத்து புதிய புகாரைப் பதிவு செய்யவும்.\n2. /status [குறிப்பு எண்] - உங்கள் புகாரின் நிலையை அறிய (எ.கா: /status NVB-XXXXXXXXXXXX).\n3. /language - மொழியை மாற்ற.\n4. /cancel - நடப்பு வரைவை ரத்து செய்ய.\n\nகுரல் பதிவு, புகைப்படம் அல்லது தட்டச்சு மூலம் புகாரைப் பதிவு செய்யலாம். உங்கள் தகவல்கள் DPDP சட்டத்தின்படி பாதுகாக்கப்படும்.",
    },
    "te": {
        "greeting": "నమస్కారం! Nexus VisBharat సహాయ కేంద్రానికి స్వాగతం. దయచేసి మీ భాషను ఎంచుకోండి:",
        "select_state": "దయచేసి మీ రాష్ట్రం లేదా కేంద్రపాలిత ప్రాంతాన్ని ఎంచుకోండి:",
        "select_district": "రాష్ట్రం: {state}\n\nక్రింది బటన్లలో మీ జిల్లాను ఎంచుకోండి, లేదా మీ జిల్లా పేరును నేరుగా టైప్ చేయండి:",
        "district_not_found": "'{text}' అనే జిల్లా {state} లో కనుగొనబడలేదు. దయచేసి క్రింది బటన్ల నుండి ఎంచుకోండి లేదా సరైన పేరును టైప్ చేయండి:",
        "enter_ward": "జిల్లా: {district}\n\nదయచేసి మీ వార్డు నంబర్, గ్రామం లేదా ప్రాంతం పేరును నమోదు చేయండి (లేదా 'దాటవేయి' క్లిక్ చేయండి):",
        "issue_label": "ఫిర్యాదు వివరాలు",
        "location_label": "ప్రాంతం",
        "confirm_btn": "✓ నిర్ధారించి సమర్పించండి",
        "edit_btn": "✏ సవరించు",
        "cancel_btn": "✖ రద్దు చేయి",
        "skip_btn": "దాటవేయి (Skip)",
        "consent_btn": "✓ సమ్మతి తెలిపి సమర్పించండి",
        "track_btn": "🔍 స్థితిని తనిఖీ చేయండి",
        "new_btn": "➕ కొత్త ఫిర్యాదు",
        "track_tip": "మీ ఫిర్యాదు స్థితిని తెలుసుకోవడానికి ఎప్పుడైనా /status {ticket} అని పంపండి.",
        "cancelled_msg": "డ్రాఫ్ట్ రద్దు చేయబడింది. కొత్త ఫిర్యాదు నమోదు చేయడానికి /start అని పంపండి.",
        "help": "Nexus VisBharat సహాయ కేంద్రం:\n\n1. /start - కొత్త ఫిర్యాదు ప్రారంభించండి.\n2. /status [రిఫరెన్స్ ID] - ఫిర్యాదు స్థితిని తనిఖీ చేయండి.\n3. /language - భాషను మార్చండి.\n4. /cancel - రద్దు చేయండి.",
    },
    "hi": {
        "greeting": "नमस्ते! Nexus VisBharat नागरिक सहायता केंद्र में आपका स्वागत है। कृपया अपनी भाषा चुनें:",
        "select_state": "कृपया अपना राज्य या केंद्र शासित प्रदेश चुनें:",
        "select_district": "राज्य: {state}\n\nकृपया नीचे दिए गए बटनों से अपना ज़िला चुनें, या सीधे अपने ज़िले का नाम टाइप करें:",
        "district_not_found": "'{text}' ज़िला {state} में नहीं मिला। कृपया नीचे दिए गए बटनों में से चुनें या सही नाम टाइप करें:",
        "enter_ward": "ज़िला: {district}\n\nकृपया अपना वार्ड नंबर, गाँव या क्षेत्र का नाम दर्ज करें (या 'छोड़ें' पर क्लिक करें):",
        "issue_label": "शिकायत का विवरण",
        "location_label": "स्थान",
        "confirm_btn": "✓ पुष्टि करें और जमा करें",
        "edit_btn": "✏ सुधारें",
        "cancel_btn": "✖ रद्द करें",
        "skip_btn": "छोड़ें (Skip)",
        "consent_btn": "✓ सहमति दें और जमा करें",
        "track_btn": "🔍 स्थिति देखें",
        "new_btn": "➕ नई शिकायत",
        "track_tip": "अपनी शिकायत की स्थिति कभी भी जानने के लिए /status {ticket} भेजें।",
        "cancelled_msg": "प्रारूप रद्द कर दिया गया। नई शिकायत दर्ज करने के लिए /start भेजें।",
        "help": "Nexus VisBharat नागरिक सहायता केंद्र:\n\n1. /start - नई शिकायत दर्ज करें।\n2. /status [रेफ़रेंस आईडी] - स्थिति ट्रैक करें।\n3. /language - भाषा बदलें।\n4. /cancel - प्रारूप रद्द करें।",
    },
    "bn": {
        "greeting": "নমস্কার! Nexus VisBharat নাগরিক সহায়তা কেন্দ্রে আপনাকে স্বাগতম। আপনার ভাষা নির্বাচন করুন:",
        "select_state": "অনুগ্রহ করে আপনার রাজ্য বা কেন্দ্রশাসিত অঞ্চল নির্বাচন করুন:",
        "select_district": "রাজ্য: {state}\n\nঅনুগ্রহ করে নিচের বোতাম থেকে আপনার জেলা নির্বাচন করুন, অথবা সরাসরি জেলার নাম টাইপ করুন:",
        "district_not_found": "'{text}' জেলা {state} এ পাওয়া যায়নি। অনুগ্রহ করে নিচের বোতাম থেকে নির্বাচন করুন:",
        "enter_ward": "জেলা: {district}\n\nআপনার ওয়ার্ড নম্বর, গ্রাম বা এলাকার নাম লিখুন (বা 'এড়িয়ে যান' চাপুন):",
        "issue_label": "অভিযোগের বিবরণ",
        "location_label": "অবস্থান",
        "confirm_btn": "✓ নিশ্চিত করুন ও জমা দিন",
        "edit_btn": "✏ সংশোধন",
        "cancel_btn": "✖ বাতিল",
        "skip_btn": "এড়িয়ে যান (Skip)",
        "consent_btn": "✓ সম্মতি দিয়ে জমা দিন",
        "track_btn": "🔍 স্থিতি দেখুন",
        "new_btn": "➕ নতুন অভিযোগ",
        "track_tip": "স্থিতি জানতে যেকোনো সময় পাঠান: /status {ticket}",
        "cancelled_msg": "খসড়া বাতিল করা হয়েছে। নতুন অভিযোগের জন্য /start পাঠান।",
        "help": "Nexus VisBharat সহায়তা:\n\n1. /start - নতুন অভিযোগ।\n2. /status [টিকিট] - স্থিতি ট্র্যাক করুন।\n3. /language - ভাষা পরিবর্তন করুন।",
    },
    "mr": {
        "greeting": "नमस्कार! Nexus VisBharat नागरी सहाय्यता केंद्रात आपले स्वागत आहे. कृपया आपली भाषा निवडा:",
        "select_state": "कृपया आपले राज्य किंवा केंद्रशासित प्रदेश निवडा:",
        "select_district": "राज्य: {state}\n\nकृपया खालील बटणांमधून आपला जिल्हा निवडा, किंवा जिल्ह्याचे नाव थेट टाईप करा:",
        "district_not_found": "'{text}' हा जिल्हा {state} मध्ये सापडला नाही. कृपया खालील बटणांमधून निवडा:",
        "enter_ward": "जिल्हा: {district}\n\nकृपया आपला प्रभाग क्रमांक, गाव किंवा परिसराचे नाव प्रविष्ट करा (किंवा 'वगळा' दाबा):",
        "issue_label": "तक्रार तपशील",
        "location_label": "स्थान",
        "confirm_btn": "✓ पुष्टी करा आणि सादर करा",
        "edit_btn": "✏ संपादन",
        "cancel_btn": "✖ रद्द करा",
        "skip_btn": "वगळा (Skip)",
        "consent_btn": "✓ संमती द्या आणि सादर करा",
        "track_btn": "🔍 स्थिती तपासा",
        "new_btn": "➕ नवीन तक्रार",
        "track_tip": "आपल्या तक्रारीची स्थिती जाणून घेण्यासाठी /status {ticket} पाठवा.",
        "cancelled_msg": "मसुदा रद्द केला. नवीन तक्रारीसाठी /start पाठवा.",
        "help": "Nexus VisBharat नागरी सहाय्यता केंद्र:\n\n1. /start - नवीन तक्रार नोंदवा.\n2. /status [तिकीट आयडी] - स्थिती तपासा.\n3. /language - भाषा बदला.",
    },
    "kn": {
        "greeting": "ನಮಸ್ಕಾರ! Nexus VisBharat ನಾಗರಿಕ ಸಹಾಯ ಕೇಂದ್ರಕ್ಕೆ ಸುಸ್ವಾಗತ. ದಯವಿಟ್ಟು ನಿಮ್ಮ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ:",
        "select_state": "ದಯವಿಟ್ಟು ನಿಮ್ಮ ರಾಜ್ಯ ಅಥವಾ ಕೇಂದ್ರಾಡಳಿತ ಪ್ರದೇಶವನ್ನು ಆಯ್ಕೆಮಾಡಿ:",
        "select_district": "ರಾಜ್ಯ: {state}\n\nದಯವಿಟ್ಟು ಕೆಳಗಿನ ಬಟನ್‌ಗಳಿಂದ ನಿಮ್ಮ ಜಿಲ್ಲೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ, ಅಥವಾ ಜಿಲ್ಲೆಯ ಹೆಸರನ್ನು ಟೈಪ್ ಮಾಡಿ:",
        "district_not_found": "'{text}' ಜಿಲ್ಲೆಯು {state} ನಲ್ಲಿ ಕಂಡುಬಂದಿಲ್ಲ. ದಯವಿಟ್ಟು ಬಟನ್‌ಗಳಿಂದ ಆಯ್ಕೆಮಾಡಿ:",
        "enter_ward": "ಜಿಲ್ಲೆ: {district}\n\nದಯವಿಟ್ಟು ನಿಮ್ಮ ವಾರ್ಡ್ ಸಂಖ್ಯೆ, ಗ್ರಾಮ ಅಥವಾ ಪ್ರದೇಶದ ಹೆಸರನ್ನು ನಮೂದಿಸಿ (ಅಥವಾ 'ಬಿಟ್ಟುಬಿಡಿ' ಕ್ಲಿಕ್ ಮಾಡಿ):",
        "issue_label": "ದೂರಿನ ವಿವರಗಳು",
        "location_label": "ಸ್ಥಳ",
        "confirm_btn": "✓ ದೃಢೀಕರಿಸಿ ಮತ್ತು ಸಲ್ಲಿಸಿ",
        "edit_btn": "✏ ತಿದ್ದುಪಡಿ",
        "cancel_btn": "✖ ರದ್ದುಮಾಡಿ",
        "skip_btn": "ಬಿಟ್ಟುಬಿಡಿ (Skip)",
        "consent_btn": "✓ ಸಮ್ಮತಿ ನೀಡಿ ಸಲ್ಲಿಸಿ",
        "track_btn": "🔍 ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ",
        "new_btn": "➕ ಹೊಸ ದೂರು",
        "track_tip": "ಸ್ಥಿತಿ ತಿಳಿಯಲು ಯಾವುದೇ ಸಮಯದಲ್ಲಿ /status {ticket} ಕಳುಹಿಸಿ.",
        "cancelled_msg": "ಕರಡು ರದ್ದುಗೊಂಡಿದೆ. ಹೊಸ ದೂರಿಗೆ /start ಕಳುಹಿಸಿ.",
        "help": "Nexus VisBharat ಸಹಾಯ:\n\n1. /start - ಹೊಸ ದೂರು ದಾಖಲಿಸಿ.\n2. /status [ಟಿಕೆಟ್] - ಸ್ಥಿತಿ ಪರಿಶೀಲಿಸಿ.\n3. /language - ಭಾಷೆ ಬದಲಾಯಿಸಿ.",
    },
    "ml": {
        "greeting": "നമസ്കാരം! Nexus VisBharat പൗരസഹായ കേന്ദ്രത്തിലേക്ക് സ്വാഗതം. നിങ്ങളുടെ ഭാഷ തിരഞ്ഞെടുക്കുക:",
        "select_state": "ദയവായി നിങ്ങളുടെ സംസ്ഥാനം അല്ലെങ്കിൽ കേന്ദ്രഭരണ പ്രദേശം തിരഞ്ഞെടുക്കുക:",
        "select_district": "സംസ്ഥാനം: {state}\n\nതാഴെയുള്ള ബട്ടണുകളിൽ നിന്ന് ജില്ല തിരഞ്ഞെടുക്കുക, അല്ലെങ്കിൽ പേര് ടൈപ്പ് ചെയ്യുക:",
        "district_not_found": "'{text}' എന്ന ജില്ല {state} ൽ കണ്ടെത്തിയില്ല. താഴെ നിന്ന് തിരഞ്ഞെടുക്കുക:",
        "enter_ward": "ജില്ല: {district}\n\nനിങ്ങളുടെ വാർഡ് നമ്പർ, ഗ്രാമം അല്ലെങ്കിൽ പ്രദേശം നൽകുക (അല്ലെങ്കിൽ 'ഒഴിവാക്കുക' അമർത്തുക):",
        "issue_label": "പരാതി വിവരങ്ങൾ",
        "location_label": "സ്ഥലം",
        "confirm_btn": "✓ സ്ഥിരീകരിച്ച് സമർപ്പിക്കുക",
        "edit_btn": "✏ തിരുത്തുക",
        "cancel_btn": "✖ റദ്ദാക്കുക",
        "skip_btn": "ഒഴിവാക്കുക (Skip)",
        "consent_btn": "✓ സമ്മതിച്ച് സമർപ്പിക്കുക",
        "track_btn": "🔍 സ്ഥിതി പരിശോധിക്കുക",
        "new_btn": "➕ പുതിയ പരാതി",
        "track_tip": "നില പരിശോധിക്കാൻ എപ്പോൾ വേണമെങ്കിലും /status {ticket} അയക്കുക.",
        "cancelled_msg": "ഡ്രാഫ്റ്റ് റദ്ദാക്കി. പുതിയ പരാതിക്ക് /start അയക്കുക.",
        "help": "Nexus VisBharat സഹായം:\n\n1. /start - പുതിയ പരാതി.\n2. /status [ടിക്കറ്റ്] - സ്ഥിതി അറിയുക.\n3. /language - ഭാഷ മാറ്റുക.",
    },
    "gu": {
        "greeting": "નમસ્તે! Nexus VisBharat નાગરિક સહાય કેન્દ્રમાં આપનું સ્વાગત છે. કૃપા કરીને તમારી ભાષા પસંદ કરો:",
        "select_state": "કૃપા કરીને તમારું રાજ્ય અથવા કેન્દ્રશાસિત પ્રદેશ પસંદ કરો:",
        "select_district": "રાજ્ય: {state}\n\nનીચેના બટનોમાંથી તમારો જિલ્લો પસંદ કરો, અથવા સીધું નામ લખો:",
        "district_not_found": "'{text}' જિલ્લો {state} માં મળ્યો નથી. કૃપા કરીને નીચેના બટનોમાંથી પસંદ કરો:",
        "enter_ward": "જિલ્લો: {district}\n\nતમારો વોર્ડ નંબર, ગામ અથવા વિસ્તારનું નામ દાખલ કરો (અથવા 'છોડો' દબાવો):",
        "issue_label": "ફરિયાદ વિગત",
        "location_label": "સ્થળ",
        "confirm_btn": "✓ પુષ્ટિ કરો અને સબમિટ કરો",
        "edit_btn": "✏ સુધારો",
        "cancel_btn": "✖ રદ કરો",
        "skip_btn": "છોડો (Skip)",
        "consent_btn": "✓ સંમતિ આપી સબમિટ કરો",
        "track_btn": "🔍 સ્થિતિ તપાસો",
        "new_btn": "➕ નવી ફરિયાદ",
        "track_tip": "સ્થિતિ જાણવા માટે ગમે ત્યારે /status {ticket} મોકલો.",
        "cancelled_msg": "ડ્રાફ્ટ રદ કરવામાં આવ્યો. નવી ફરિયાદ માટે /start મોકલો.",
        "help": "Nexus VisBharat સહાય:\n\n1. /start - નવી ફરિયાદ નોંધાવો.\n2. /status [ટિકિટ] - સ્થિતિ તપાસો.\n3. /language - ભાષા બદલો.",
    },
    "pa": {
        "greeting": "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ! Nexus VisBharat ਨਾਗਰਿਕ ਸਹਾਇਤਾ ਕੇਂਦਰ ਵਿੱਚ ਤੁਹਾਡਾ ਸਵਾਗਤ ਹੈ। ਕਿਰਪਾ ਕਰਕੇ ਆਪਣੀ ਭਾਸ਼ਾ ਚੁਣੋ:",
        "select_state": "ਕਿਰਪਾ ਕਰਕੇ ਆਪਣਾ ਰਾਜ ਜਾਂ ਕੇਂਦਰ ਸ਼ਾਸਿਤ ਪ੍ਰਦੇਸ਼ ਚੁਣੋ:",
        "select_district": "ਰਾਜ: {state}\n\nਹੇਠਾਂ ਦਿੱਤੇ ਬਟਨਾਂ ਤੋਂ ਆਪਣਾ ਜ਼ਿਲ੍ਹਾ ਚੁਣੋ, ਜਾਂ ਸਿੱਧਾ ਜ਼ਿਲ੍ਹੇ ਦਾ ਨਾਮ ਟਾਈਪ ਕਰੋ:",
        "district_not_found": "'{text}' ਜ਼ਿਲ੍ਹਾ {state} ਵਿੱਚ ਨਹੀਂ ਮਿਲਿਆ। ਹੇਠਾਂ ਦਿੱਤੇ ਬਟਨਾਂ ਵਿੱਚੋਂ ਚੁਣੋ:",
        "enter_ward": "ਜ਼ਿਲ੍ਹਾ: {district}\n\nਕਿਰਪਾ ਕਰਕੇ ਆਪਣਾ ਵਾਰਡ ਨੰਬਰ, ਪਿੰਡ ਜਾਂ ਖੇਤਰ ਦਾ ਨਾਮ ਦਰਜ ਕਰੋ (ਜਾਂ 'ਛੱਡੋ' ਦਬਾਓ):",
        "issue_label": "ਸ਼ਿਕਾਇਤ ਦਾ ਵੇਰਵਾ",
        "location_label": "ਸਥਾਨ",
        "confirm_btn": "✓ ਪੁਸ਼ਟੀ ਕਰੋ ਅਤੇ ਜਮ੍ਹਾਂ ਕਰੋ",
        "edit_btn": "✏ ਸੋਧੋ",
        "cancel_btn": "✖ ਰੱਦ ਕਰੋ",
        "skip_btn": "ਛੱਡੋ (Skip)",
        "consent_btn": "✓ ਸਹਿਮਤੀ ਦੇ ਕੇ ਜਮ੍ਹਾਂ ਕਰੋ",
        "track_btn": "🔍 ਸਥਿਤੀ ਦੇਖੋ",
        "new_btn": "➕ ਨਵੀਂ ਸ਼ਿਕਾਇਤ",
        "track_tip": "ਸਥਿਤੀ ਜਾਣਨ ਲਈ ਕਿਸੇ ਵੀ ਸਮੇਂ /status {ticket} ਭੇਜੋ।",
        "cancelled_msg": "ਡਰਾਫਟ ਰੱਦ ਕਰ ਦਿੱਤਾ ਗਿਆ। ਨਵੀਂ ਸ਼ਿਕਾਇਤ ਲਈ /start ਭੇਜੋ।",
        "help": "Nexus VisBharat ਸਹਾਇਤਾ:\n\n1. /start - ਨਵੀਂ ਸ਼ਿਕਾਇਤ।\n2. /status [ਟਿਕਟ] - ਸਥਿਤੀ ਦੇਖੋ।\n3. /language - ਭਾਸ਼ਾ ਬਦਲੋ।",
    },
    "or": {
        "greeting": "ନମସ୍କାର! Nexus VisBharat ନାଗରିକ ସହାୟତା କେନ୍ଦ୍ରକୁ ସ୍ୱାଗତ। ଦୟାକରି ଆପଣଙ୍କ ଭାଷା ଚୟନ କରନ୍ତୁ:",
        "select_state": "ଦୟାକରି ଆପଣଙ୍କ ରାଜ୍ୟ କିମ୍ବା କେନ୍ଦ୍ରଶାସିତ ଅଞ୍ଚଳ ଚୟନ କରନ୍ତୁ:",
        "select_district": "ରାଜ୍ୟ: {state}\n\nଦୟାକରି ତଳ ବଟନରୁ ଆପଣଙ୍କ ଜିଲ୍ଲା ଚୟନ କରନ୍ତୁ, କିମ୍ବା ସିଧାସଳଖ ଟାଇପ୍ କରନ୍ତୁ:",
        "district_not_found": "'{text}' ଜିଲ୍ଲା {state} ରେ ମିଳିଲା ନାହିଁ। ଦୟାକରି ତଳ ବଟନରୁ ବାଛନ୍ତୁ:",
        "enter_ward": "ଜିଲ୍ଲା: {district}\n\nଦୟାକରି ଆପଣଙ୍କ ୱାର୍ଡ ନମ୍ବର, ଗ୍ରାମ ବା ଅଞ୍ଚଳର ନାମ ଦିଅନ୍ତୁ (କିମ୍ବା 'ଛାଡ଼ନ୍ତୁ' ଦବାନ୍ତୁ):",
        "issue_label": "ଅଭିଯୋଗ ବିବରଣୀ",
        "location_label": "ସ୍ଥାନ",
        "confirm_btn": "✓ ନିଶ୍ଚିତ କରନ୍ତୁ ଓ ଦାଖଲ କରନ୍ତୁ",
        "edit_btn": "✏ ସଂଶୋଧନ",
        "cancel_btn": "✖ ବାତିଲ",
        "skip_btn": "ଛାଡ଼ନ୍ତୁ (Skip)",
        "consent_btn": "✓ ସମ୍ମତି ଦେଇ ଦାଖଲ କରନ୍ତୁ",
        "track_btn": "🔍 ସ୍ଥିତି ଯାଞ୍ଚ କରନ୍ତୁ",
        "new_btn": "➕ ନୂତନ ଅଭିଯୋଗ",
        "track_tip": "ସ୍ଥିତି ଜାଣିବା ପାଇଁ ଯେକୌଣସି ସମୟରେ /status {ticket} ପଠାନ୍ତୁ।",
        "cancelled_msg": "ଡ୍ରାଫ୍ଟ ବାତିଲ କରାଗଲା। ନୂତନ ଅଭିଯୋଗ ପାଇଁ /start ପଠାନ୍ତୁ।",
        "help": "Nexus VisBharat ସହାୟତା:\n\n1. /start - ନୂତନ ଅଭିଯୋଗ।\n2. /status [ଟିକେଟ୍] - ସ୍ଥିତି ଯାଞ୍ଚ।\n3. /language - ଭାଷା ପରିବର୍ତ୍ତନ।",
    },
    "as": {
        "greeting": "নমস্কাৰ! Nexus VisBharat নাগৰিক সাহায্য কেন্দ্ৰলৈ স্বাগতম। অনুগ্ৰহ কৰি আপোনাৰ ভাষা বাছক:",
        "select_state": "অনুগ্ৰহ কৰি আপোনাৰ ৰাজ্য বা কেন্দ্ৰীয় শাসিত অঞ্চল বাছক:",
        "select_district": "ৰাজ্য: {state}\n\nতলৰ বুটামৰ পৰা আপোনাৰ জিলা বাছক, বা জিলাৰ নাম পোনপটীয়াকৈ টাইপ কৰক:",
        "district_not_found": "'{text}' জিলাখন {state} ত পোৱা নগল। অনুগ্ৰহ কৰি তলৰ বুটামৰ পৰা বাছক:",
        "enter_ward": "জিলা: {district}\n\nআপোনাৰ ৱাৰ্ড নম্বৰ, গাঁও বা অঞ্চলৰ নাম দিয়ক (বা 'বাদ দিয়ক' টিপক):",
        "issue_label": "অভিযোগৰ বিৱৰণ",
        "location_label": "স্থান",
        "confirm_btn": "✓ নিশ্চিত কৰি দাখিল কৰক",
        "edit_btn": "✏ সংশোধন",
        "cancel_btn": "✖ বাতিল কৰক",
        "skip_btn": "বাদ দিয়ক (Skip)",
        "consent_btn": "✓ সন্মতি দি দাখিল কৰক",
        "track_btn": "🔍 স্থিতি পৰীক্ষা কৰক",
        "new_btn": "➕ নতুন অভিযোগ",
        "track_tip": "স্থিতি জানিবলৈ যিকোনো সময়তে প্ৰেৰণ কৰক: /status {ticket}",
        "cancelled_msg": "খচৰা বাতিল কৰা হ’ল। নতুন অভিযোগৰ বাবে /start প্ৰেৰণ কৰক।",
        "help": "Nexus VisBharat সাহায্য:\n\n1. /start - নতুন অভিযোগ।\n2. /status [টিকিট] - স্থিতি পৰীক্ষা।\n3. /language - ভাষা সলনি কৰক।",
    },
    "ur": {
        "greeting": "آداب! Nexus VisBharat شہری امدادی مرکز میں خوش آمدید۔ براہ کرم اپنی زبان منتخب کریں:",
        "select_state": "براہ کرم اپنی ریاست یا مرکز کے زیر انتظام علاقہ منتخب کریں:",
        "select_district": "ریاست: {state}\n\nبراہ کرم نیچے دیے گئے بٹنوں سے اپنا ضلع منتخب کریں، یا براہ راست ٹائپ کریں:",
        "district_not_found": "ضلع '{text}' ریاست {state} میں نہیں ملا۔ براہ کرم نیچے دیے گئے بٹنوں سے منتخب کریں:",
        "enter_ward": "ضلع: {district}\n\nبراہ کرم اپنا وارڈ نمبر، گاؤں یا علاقے کا نام درج کریں (یا 'چھوڑ دیں' پر کلک کریں):",
        "issue_label": "شکایت کی تفصیل",
        "location_label": "مقام",
        "confirm_btn": "✓ تصدیق کریں اور جمع کریں",
        "edit_btn": "✏ ترمیم",
        "cancel_btn": "✖ منسوخ",
        "skip_btn": "چھوڑ دیں (Skip)",
        "consent_btn": "✓ رضامندی دیں اور جمع کریں",
        "track_btn": "🔍 کیفیت دیکھیں",
        "new_btn": "➕ نئی شکایت",
        "track_tip": "اپنی شکایت کی کیفیت جاننے کے لیے کسی بھی وقت /status {ticket} بھیجیں۔",
        "cancelled_msg": "مسودہ منسوخ کر دیا گیا۔ نئی شکایت کے لیے /start بھیجیں۔",
        "help": "Nexus VisBharat شہری امداد:\n\n1. /start - نئی شکایت।\n2. /status [ٹکٹ] - کیفیت چیک کریں۔\n3. /language - زبان تبدیل کریں۔",
    },
    "en": {
        "greeting": (
            "🏛️ Welcome to Nexus VisBharat (@NexusVisBharatBot)\n"
            "Public Digital Infrastructure for Accountable District Governance\n\n"
            "Report civic and infrastructure issues (water supply, roads, sanitation, electricity, health) "
            "in your own language using text, voice notes, or photos. Reports are verified, translated, "
            "and routed directly to responsible district authorities under the DPDP Act 2023.\n\n"
            "📌 Core Universal Commands:\n"
            "• /start — Start a new grievance report or reset\n"
            "• /language — Change your active language anytime\n"
            "• /status <Ticket ID> — Track progress of a submitted report (e.g. /status NVB-20260928XXXX)\n"
            "• /cancel — Discard current draft & start over\n"
            "• /help — View assistance & commands\n\n"
            "👇 Please choose your preferred language to begin / தொடங்குவதற்கு உங்கள் மொழியைத் தேர்ந்தெடுக்கவும்:"
        ),
        "select_state": "Please select your State or Union Territory:",
        "select_district": "State: {state}\n\nPlease select your District using the buttons below, or type your district name directly:",
        "district_not_found": "District '{text}' was not recognized in {state}. Please select from the buttons below or type a valid district name:",
        "enter_ward": "District: {district}\n\nPlease enter your local Ward number, Village, or Area name (or tap 'Skip'):",
        "issue_label": "Grievance / Issue",
        "location_label": "Location",
        "confirm_btn": "✓ Confirm & Submit",
        "edit_btn": "✏ Edit Report",
        "cancel_btn": "✖ Cancel Draft",
        "skip_btn": "Skip",
        "consent_btn": "✓ I Consent & Submit",
        "track_btn": "🔍 Track Status",
        "new_btn": "➕ New Report",
        "track_tip": "To track your report anytime, send /status {ticket}",
        "cancelled_msg": "Draft cancelled. Send /start to begin a new report.",
        "help": "Nexus VisBharat Citizen Intake (@NexusVisBharatBot):\n\n1. /start - Select language and begin a new public infrastructure report.\n2. /status [ticket ID] - Track the status of a registered report (e.g. /status NVB-XXXXXXXXXXXX).\n3. /language - Change your active language anytime.\n4. /cancel - Discard the current report draft.\n\nYou can report issues using text, voice notes, or photos. All personal data is governed under the DPDP Act 2023.",
    },
}


def _find_matching_district(state, text):
    if not text:
        return None
    raw = str(text).strip()
    valid_districts = PILOT_STATE_TO_DISTRICTS.get(state, [])
    # 1. Exact case-insensitive match
    for d in valid_districts:
        if d.lower() == raw.lower():
            return d
    # 2. Known aliases
    cleaned = raw.lower()
    if cleaned in DISTRICT_ALIASES:
        canonical = DISTRICT_ALIASES[cleaned]
        if canonical in valid_districts:
            return canonical
    # 3. Substring match
    for d in valid_districts:
        if cleaned in d.lower() or d.lower() in cleaned:
            return d
    return None


def _now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def migrate(db):
    """Install the Telegram delivery ledger on both SQLite and PostgreSQL."""
    db.execute(
        """CREATE TABLE IF NOT EXISTS telegram_inbound_events (
            update_id TEXT PRIMARY KEY,
            payload_hash TEXT NOT NULL,
            chat_id TEXT,
            status TEXT NOT NULL,
            response_json TEXT,
            received_at TEXT NOT NULL,
            lease_until_epoch INTEGER,
            processed_at TEXT
        )"""
    )
    if getattr(db, 'backend', 'sqlite') == 'postgres':
        cols = {
            row['column_name']
            for row in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'telegram_inbound_events'"
            ).fetchall()
        }
    else:
        cols = {
            row['name']
            for row in db.execute("PRAGMA table_info(telegram_inbound_events)").fetchall()
        }
    if 'lease_until_epoch' not in cols:
        db.execute("ALTER TABLE telegram_inbound_events ADD COLUMN lease_until_epoch INTEGER")
    db.execute(
        """CREATE TABLE IF NOT EXISTS telegram_outbox (
            message_key TEXT PRIMARY KEY,
            chat_id TEXT NOT NULL,
            method TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            next_attempt_at_epoch INTEGER NOT NULL,
            lease_until_epoch INTEGER,
            last_error TEXT,
            sent_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )"""
    )
    db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_outbox_due ON telegram_outbox(status, next_attempt_at_epoch)")
    if getattr(db, 'backend', 'sqlite') == 'postgres':
        outbox_cols = {r['column_name'] for r in db.execute("SELECT column_name FROM information_schema.columns WHERE table_name='telegram_outbox'").fetchall()}
    else:
        outbox_cols = {r['name'] for r in db.execute('PRAGMA table_info(telegram_outbox)').fetchall()}
    if 'lease_owner' not in outbox_cols:
        db.execute('ALTER TABLE telegram_outbox ADD COLUMN lease_owner TEXT')
    db.execute("CREATE INDEX IF NOT EXISTS idx_telegram_inbound_chat ON telegram_inbound_events(chat_id, received_at)")
    db.execute("CREATE TABLE IF NOT EXISTS telegram_chat_guards (chat_id TEXT PRIMARY KEY, touched_at TEXT)")
    from .telegram_jobs import migrate as migrate_jobs
    migrate_jobs(db)
    db.commit()


def _token():
    configured = current_app.config.get("TELEGRAM_BOT_TOKEN")
    return str(configured if configured is not None else os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def _api_url(method):
    token = _token()
    return f"https://api.telegram.org/bot{token}/{method}" if token else ""


def _send_chat_action(chat_id, action="typing"):
    if not chat_id:
        return
    token = _token()
    if not token or current_app.config.get("TESTING"):
        return
    url = _api_url("sendChatAction")
    if not url:
        return
    try:
        requests.post(url, json={"chat_id": chat_id, "action": action}, timeout=2)
    except Exception:
        pass


def _keyboard(rows):
    return {"inline_keyboard": [[{"text": label, "callback_data": action} for label, action in row] for row in rows]}


def _queue(message_key, chat_id, method, payload):
    db = get_db()
    now = int(time.time())
    now_iso = _now()
    db.execute(
        """INSERT INTO telegram_outbox
           (message_key, chat_id, method, payload_json, status, attempts,
            next_attempt_at_epoch, lease_until_epoch, last_error, sent_at,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, 'queued', 0, ?, NULL, NULL, NULL, ?, ?)
           ON CONFLICT(message_key) DO NOTHING""",
        (str(message_key), str(chat_id), method, json.dumps(payload, ensure_ascii=False), now, now_iso, now_iso),
    )
    _commit()


def _commit():
    if not getattr(g, 'telegram_atomic', False):
        get_db().commit()


def _lock_chat(chat_id):
    db = get_db()
    db.execute("INSERT INTO telegram_chat_guards(chat_id) VALUES (?) ON CONFLICT(chat_id) DO NOTHING", (str(chat_id),))
    # A short row write serializes each chat on PostgreSQL and SQLite.
    db.execute("UPDATE telegram_chat_guards SET touched_at=? WHERE chat_id=?", (_now(), str(chat_id)))


@contextmanager
def _chat_transaction(chat_id):
    previous = getattr(g, 'telegram_atomic', False)
    g.telegram_atomic = True
    try:
        _lock_chat(chat_id)
        yield
        get_db().commit()
    except Exception:
        get_db().rollback()
        raise
    finally:
        g.telegram_atomic = previous


def _queue_text(chat_id, text, reply_markup=None, message_key=None):
    payload = {"chat_id": chat_id, "text": str(text or "")[:4096]}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    _queue(message_key or f"chat:{chat_id}:message:{uuid4().hex}",
           chat_id, "sendMessage", payload)


def _send_quick_action(method, payload):
    if current_app.config.get("TESTING"):
        return
    token = _token()
    if not token:
        return
    try:
        requests.post(_api_url(method), json=payload, timeout=2)
    except Exception:
        pass


def _queue_callback_answer(callback_id, message_key, text=None):
    if not callback_id:
        return
    payload = {"callback_query_id": callback_id}
    if text:
        payload["text"] = str(text)[:200]
    _queue(message_key, "callback", "answerCallbackQuery", payload)


def dispatch_outbox(limit=None):
    """Try due Telegram sends; failures remain queued for the next worker run."""
    token = _token()
    if not token:
        return {"sent": 0, "failed": 0, "pending": _pending_count()}

    max_items = int(limit or current_app.config.get("TELEGRAM_OUTBOX_BATCH_SIZE", 20) or 20)
    max_attempts = int(current_app.config.get("TELEGRAM_OUTBOX_MAX_ATTEMPTS", 12) or 12)
    sent = failed = 0
    db = get_db()
    now = int(time.time())
    db.execute("UPDATE telegram_outbox SET status='dead',lease_owner=NULL WHERE status='sending' AND lease_until_epoch<? AND attempts>=?", (now, max_attempts))
    db.commit()
    for _ in range(max_items):
        row = db.execute(
            """SELECT * FROM telegram_outbox
               WHERE (status='queued' OR status='failed' OR (status='sending' AND lease_until_epoch<?))
                 AND next_attempt_at_epoch<=?
                 AND (lease_until_epoch IS NULL OR lease_until_epoch<?)
                 AND attempts<?
               ORDER BY CASE WHEN method='answerCallbackQuery' THEN 0 ELSE 1 END, created_at, message_key LIMIT 1""",
            (now, now, now, max_attempts),
        ).fetchone()
        if not row:
            break
        key = row["message_key"]
        lease_until = int(time.time()) + 60
        owner = uuid4().hex
        claim = db.execute(
            """UPDATE telegram_outbox SET status='sending', lease_until_epoch=?, lease_owner=?, updated_at=?, attempts=attempts+1
               WHERE message_key=? AND (status='queued' OR status='failed' OR (status='sending' AND lease_until_epoch<?))
                 AND (lease_until_epoch IS NULL OR lease_until_epoch<?) RETURNING *""",
            (lease_until, owner, _now(), key, now, now),
        ).fetchone()
        db.commit()
        if not claim:
            continue

        try:
            response = requests.post(_api_url(row["method"]), json=json.loads(row["payload_json"]), timeout=8)
            body = response.json()
            if not response.ok or not body.get("ok"):
                desc = str(body.get("description") or "").lower()
                if row["method"] == "answerCallbackQuery" and ("too old" in desc or "invalid" in desc or "query id" in desc):
                    db.execute(
                        """UPDATE telegram_outbox SET status='sent', sent_at=?, lease_until_epoch=NULL,
                           last_error=NULL, updated_at=?,lease_owner=NULL WHERE message_key=? AND lease_owner=?""",
                        (_now(), _now(), key, owner),
                    )
                    db.commit()
                    continue
                raise RuntimeError(f"Telegram API rejected {row['method']} ({response.status_code}): {body.get('description')}")
            db.execute(
                """UPDATE telegram_outbox SET status='sent', sent_at=?, lease_until_epoch=NULL,
                   last_error=NULL, updated_at=?,lease_owner=NULL WHERE message_key=? AND lease_owner=?""",
                (_now(), _now(), key, owner),
            )
            db.commit()
            sent += 1
        except Exception as exc:
            attempts = int(claim["attempts"])
            delay = min(900, 2 ** min(attempts, 8))
            db.execute(
                """UPDATE telegram_outbox SET status=?, next_attempt_at_epoch=?,
                   lease_until_epoch=NULL,lease_owner=NULL,last_error=?, updated_at=? WHERE message_key=? AND lease_owner=?""",
                ('dead' if attempts >= max_attempts else 'failed', int(time.time()) + delay, type(exc).__name__, _now(), key, owner),
            )
            db.commit()
            failed += 1
            LOGGER.warning("Telegram outbound delivery failed for %s: %s", key, type(exc).__name__)
    return {"sent": sent, "failed": failed, "pending": _pending_count()}


def _pending_count():
    try:
        row = get_db().execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE status IN ('queued','failed','sending')").fetchone()
        return int(row["n"] or 0)
    except Exception:
        return 0


def health():
    db = get_db()
    inbound = db.execute("SELECT COUNT(*) AS n FROM telegram_inbound_events").fetchone()
    counts = {r['status']: int(r['n']) for r in db.execute('SELECT status,COUNT(*) AS n FROM telegram_jobs GROUP BY status').fetchall()}
    oldest = db.execute("SELECT MIN(created_at) AS oldest FROM telegram_jobs WHERE status IN ('queued','processing')").fetchone()
    dead = db.execute("SELECT COUNT(*) AS n FROM telegram_outbox WHERE status='dead'").fetchone()
    worker_age = {r['role']: max(0, int(time.time())-int(r['last_seen_epoch']))
                  for r in db.execute('SELECT role,last_seen_epoch FROM telegram_worker_heartbeats').fetchall()}
    return {
        "token_configured": bool(_token()),
        "webhook_secret_configured": bool((current_app.config.get("TELEGRAM_WEBHOOK_SECRET") or "").strip()),
        "inbound_events": int(inbound["n"] or 0),
        "outbox_pending": _pending_count(),
        'outbox_dead': int(dead['n'] or 0),
        'jobs_by_status': counts,
        'oldest_pending_job_at': oldest['oldest'],
        'worker_last_seen_seconds_ago': worker_age,
        'dedicated_workers_recent': worker_age.get('outbox', 999999) < 30 and worker_age.get('jobs', 999999) < 900,
    }


def _session(chat_id):
    session = get_channel_session("telegram", str(chat_id))
    return session if isinstance(session, dict) and session.get("stage") else {"stage": "language", "nonce": uuid4().hex[:12]}


def _save(chat_id, session):
    get_db().execute("""INSERT INTO channel_sessions(channel,session_key,data_json,updated_at)
        VALUES ('telegram',?,?,?) ON CONFLICT(session_key) DO UPDATE SET
        data_json=excluded.data_json,updated_at=excluded.updated_at""",
        (str(chat_id), json.dumps(session), _now()))
    _commit()


def _clear(chat_id):
    get_db().execute("DELETE FROM channel_sessions WHERE channel='telegram' AND session_key=?", (str(chat_id),))
    _commit()


def _lang(session):
    """Strictly return selected language with Tamil ('ta') as default priority."""
    value = str((session or {}).get("language") or "ta").lower()
    return value if value in ORDERED_LANG_CODES else "ta"


def _prompt(lang, key):
    lang_map = BOT_PROMPTS.get(lang) or BOT_PROMPTS["ta"]
    return lang_map.get(key) or BOT_PROMPTS["en"].get(key, "")


def _i18n(lang, key):
    strings = STRINGS
    lang_dict = strings.get(lang) or strings.get("ta") or {}
    return lang_dict.get(key) or strings.get("en", {}).get(key, "")


def _text(chat_id, text, reply_markup=None, key=None):
    if reply_markup:
        reply_markup = json.loads(json.dumps(reply_markup))
        nonce = _session(chat_id).get('nonce')
        if nonce:
            for row in reply_markup.get('inline_keyboard', []):
                for button in row:
                    action = button.get('callback_data', '')
                    if action and not action.startswith('track:'):
                        button['callback_data'] = f'n:{nonce}:{action}'
    _queue_text(chat_id, text, reply_markup, key)


def _start(chat_id, choose_language=False):
    previous = _session(chat_id)
    session = {"stage": "language", "nonce": uuid4().hex[:12]}
    if not choose_language and previous.get('stage') == 'complete' and previous.get('language') in ORDERED_LANG_CODES:
        session.update(language=previous['language'], stage='issue')
        session.update({key: previous[key] for key in ('state', 'district') if previous.get(key)})
    _save(chat_id, session)
    if session['stage'] == 'issue':
        lang = _lang(session)
        issue_p = _i18n(lang, 'issue') or "Please describe the civic infrastructure issue in full detail:"
        cmd_hint = (
            f"\n\n📌 Core Commands:\n"
            f"• /language — Change language / மொழியை மாற்ற\n"
            f"• /status <Ticket ID> — Track grievance\n"
            f"• /cancel — Start over\n"
            f"• /help — Assistance"
        )
        change_btn = "🌐 Change Language / மொழியை மாற்று" if lang != "ta" else "🌐 மொழியை மாற்று (Change Language)"
        cancel_btn = _prompt(lang, "cancel_btn") or "✖ Cancel"
        kb = _keyboard([[(change_btn, "action:change_lang"), (cancel_btn, "cancel")]])
        _text(chat_id, f"{issue_p}{cmd_hint}", kb)
        return session
    greeting = BOT_PROMPTS.get("en", {}).get("greeting") or BOT_PROMPTS["ta"]["greeting"]
    _text(chat_id, greeting, _keyboard(LANGUAGE_KEYBOARD), f"chat:{chat_id}:start:{session['nonce']}")
    return session


def _help(chat_id, session=None):
    lang = _lang(session or _session(chat_id))
    help_text = _prompt(lang, "help")
    _text(chat_id, help_text)


def _status(chat_id, ticket, session=None):
    lang = _lang(session or _session(chat_id))
    clean_ticket = str(ticket or "").strip().upper()
    db = get_db()
    job = db.execute("SELECT status FROM telegram_jobs WHERE request_id=? AND chat_id=?", (clean_ticket, str(chat_id))).fetchone()
    if job and job['status'] != 'done':
        key = 'needs_attention' if job['status'] == 'failed' else 'queued'
        _text(chat_id, _flow(lang, key).format(ticket=clean_ticket))
        return
    row = db.execute(
        """SELECT request_id, status, district, state, category, urgency,
                  routed_department, sla_due_at, created_at
           FROM citizen_requests WHERE request_id=? AND submitted_by=?""",
        (clean_ticket, str(chat_id))
    ).fetchone()
    if not row:
        not_found_msg = _i18n(lang, "not_found") or f"No request matches ticket {clean_ticket}."
        _text(chat_id, not_found_msg)
        return

    status_val = row['status']
    status_key = f"status_{status_val.lower().replace(' ', '_')}"
    status_label = _i18n(lang, status_key) or status_val

    due_str = row['sla_due_at'][:16].replace('T', ' ') if row['sla_due_at'] else 'Standard'
    details = (
        f"📋 Ticket: {row['request_id']}\n"
        f"📍 Location: {row['district']}, {row['state']}\n"
        f"🏷 Category: {row['category']} ({row['urgency']})\n"
        f"🏛 Department: {row['routed_department'] or 'Grievance Redressal'}\n"
        f"⚡ Status: {status_label}\n"
        f"⏱ SLA Due: {due_str}"
    )
    track_btn = _prompt(lang, "track_btn")
    new_btn = _prompt(lang, "new_btn")
    kb = _keyboard([[ (track_btn, f"track:{clean_ticket}"), (new_btn, "new_report") ]])
    _text(chat_id, details, kb)


def _voice_text(file_id, language):
    """Download and transcribe a Telegram voice note through the existing ASR path."""
    if not _token():
        raise ValueError("Telegram bot token is not configured")
    meta = requests.get(_api_url("getFile"), params={"file_id": file_id}, timeout=10).json()
    path = (meta.get("result") or {}).get("file_path")
    if not path:
        raise ValueError("Telegram voice file is unavailable")
    raw = requests.get(f"https://api.telegram.org/file/bot{_token()}/{path}", timeout=30).content
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError("Telegram voice file is empty or too large")
    from ..blueprints.api import _run_speech_to_text
    result = _run_speech_to_text(
        {"language": language}, language, audio_bytes=raw, mime_type="audio/ogg", require_live=True,
    )
    transcript = result.get("transcript") or result.get("text") or result.get("transcription")
    if not isinstance(transcript, str) or not transcript.strip():
        raise ValueError("Speech-to-text returned an empty transcript")
    return transcript.strip()


def _request_voice_retry(chat_id, session):
    """Retain the draft and allow replacement audio/text without confirmation."""
    session.update(stage="issue", consent_granted=False)
    _save(chat_id, session)
    retry_msg = _i18n(_lang(session), "asr_error") or "I could not transcribe that voice note. Please type your report; your draft is retained."
    _text(chat_id, retry_msg)


def _queue_voice(chat_id, session):
    _send_chat_action(chat_id, "record_voice")
    from .telegram_jobs import enqueue
    session.update(issue='', stage='transcribing', consent_granted=False)
    session['voice_revision'] = int(session.get('voice_revision') or 0) + 1
    job = enqueue(chat_id, session, 'voice')
    session['job_id'] = job['job_id']
    _save(chat_id, session)
    _text(chat_id, _flow(_lang(session), 'voice'), key=f"job:{job['job_id']}:received")


def _submit(chat_id, session, ingest_text):
    _send_chat_action(chat_id, "typing")
    language = _lang(session)
    issue = str(session.get('issue') or '').strip()
    if session.get('voice_file_id') and (not issue or issue == 'Voice report'):
        _queue_voice(chat_id, session)
        return
    if not issue or not session.get('consent_granted'):
        _send_review(chat_id, session)
        return
    if session.get('district') not in PILOT_STATE_TO_DISTRICTS.get(session.get('state'), []):
        session['stage'] = 'state'
        _save(chat_id, session)
        _text(chat_id, _prompt(language, 'select_state'), _keyboard(STATE_KEYBOARD))
        return
    from .telegram_jobs import enqueue
    job = enqueue(chat_id, session, 'intake')
    session.update(stage='processing', job_id=job['job_id'], request_id=job['request_id'])
    _save(chat_id, session)
    _text(chat_id, _flow(language, 'queued').format(ticket=job['request_id']),
          _keyboard([[(_prompt(language, 'track_btn'), f"track:{job['request_id']}")]]),
          key=f"job:{job['job_id']}:received")


def _announce_saved(chat_id, session):
    language = _lang(session)
    request_id = session['request_id']
    saved = (_i18n(language, 'saved') or 'Your request was saved. Reference: {ticket}').format(ticket=request_id)
    tip = _prompt(language, 'track_tip').format(ticket=request_id)
    kb = _keyboard([[(_prompt(language, 'track_btn'), f'track:{request_id}'),
                     (_prompt(language, 'new_btn'), 'new_report')]])
    _text(chat_id, f'{saved}\n\n{tip}', kb, key=f'chat:{chat_id}:saved:{request_id}')


def _callback(update, chat_id, ingest_text):
    callback = update.get("callback_query") or {}
    chat_id = (callback.get("message") or {}).get("chat", {}).get("id") or chat_id
    action = str(callback.get("data") or "")
    _send_chat_action(chat_id, "typing")
    session = _session(chat_id)
    if action.startswith('n:'):
        _, nonce, action = action.split(':', 2)
        if nonce != session.get('nonce'):
            _queue_callback_answer(callback.get('id'), f"callback:{callback.get('id')}", text='This button belongs to an earlier draft. Use the latest message.')
            return
    lang = action[5:] if action.startswith("lang:") and action[5:] in ORDERED_LANG_CODES else _lang(session)
    toast = _processing_toast(lang)
    _queue_callback_answer(callback.get("id"), f"callback:{callback.get('id') or hashlib.sha256(action.encode()).hexdigest()}", text=toast)

    if session.get('stage') == 'processing' and not action.startswith(('track:', 'action:change_lang', 'cancel', 'new_report', 'lang:')):
        _status(chat_id, session['request_id'], session)
        return

    if action.startswith("lang:") and action[5:] in ORDERED_LANG_CODES:
        language = action[5:]
        session.update(language=language, stage="issue")
        _save(chat_id, session)
        welcome = _i18n(language, "welcome")
        issue_p = _i18n(language, "issue")
        _text(chat_id, f"{welcome}\n\n{issue_p}")
    elif action == 'location:change' and session.get('stage') in {'location_confirm', 'review'}:
        session.update(stage='state', consent_granted=False)
        _save(chat_id, session)
        _text(chat_id, _prompt(lang, 'select_state'), _keyboard(STATE_KEYBOARD))
    elif action == 'location:confirm' and session.get('stage') == 'location_confirm':
        session.update(state=session['suggested_state'], district=session['suggested_district'], ward='', stage='review')
        _save(chat_id, session)
        _send_review(chat_id, session)
    elif action.startswith("state:") and session.get('stage') == 'state':
        state = action[6:]
        if state not in PILOT_STATE_TO_DISTRICTS:
            return
        session.update(state=state, stage="district")
        _save(chat_id, session)
        lang = _lang(session)
        keyboard = DISTRICT_KEYBOARDS.get(state, DISTRICT_KEYBOARDS["Tamil Nadu"])
        prompt_text = _prompt(lang, "select_district").format(state=state)
        _text(chat_id, prompt_text, _keyboard(keyboard))
    elif action.startswith("dist:") and session.get('stage') == 'district':
        district = action[5:]
        if district not in PILOT_STATE_TO_DISTRICTS.get(session.get('state'), []):
            return
        session.update(district=district, location=district, ward='', stage="review")
        _save(chat_id, session)
        _send_review(chat_id, session)
    elif action == 'ward:add' and session.get('stage') == 'review':
        session.update(stage='ward', consent_granted=False)
        _save(chat_id, session)
        _text(chat_id, _prompt(lang, 'enter_ward').format(district=session['district']),
              _keyboard([[(_prompt(lang, 'skip_btn'), 'ward:skip')]]))
    elif action == "ward:skip" and session.get("stage") == "ward":
        session.update(ward="", stage="review")
        _save(chat_id, session)
        _send_review(chat_id, session)
    elif action == "confirm" and session.get("stage") == "review":
        session["stage"] = "privacy"
        _save(chat_id, session)
        lang = _lang(session)
        notice = _i18n(lang, "notice")
        consent = _i18n(lang, "consent")
        consent_btn = _prompt(lang, "consent_btn")
        cancel_btn = _prompt(lang, "cancel_btn")
        _text(chat_id, f"{notice}\n\n{consent}", _keyboard([[ (consent_btn, 'consent') ], [ (cancel_btn, 'cancel') ]]))
    elif action == "consent" and session.get("stage") in {"review", "privacy"}:
        session["consent_granted"] = True
        _save(chat_id, session)
        _submit(chat_id, session, ingest_text)
    elif action == "edit" and session.get('stage') in {'review', 'privacy'}:
        lang = _lang(session)
        session.update(stage='issue', consent_granted=False)
        _save(chat_id, session)
        _text(chat_id, _i18n(lang, "issue"))
    elif action == "cancel":
        lang = _lang(session)
        _clear(chat_id)
        _text(chat_id, _prompt(lang, "cancelled_msg"))
    elif action.startswith("track:"):
        ticket = action[6:]
        _status(chat_id, ticket, session)
    elif action in ("action:change_lang", "change_lang"):
        _start(chat_id, choose_language=True)
    elif action == "new_report":
        _start(chat_id, choose_language=True)


def _send_review(chat_id, session):
    issue = str(session.get("issue") or "").strip()
    if session.get("voice_file_id") and (not issue or issue == "Voice report"):
        _request_voice_retry(chat_id, session)
        return
    lang = _lang(session)
    state = session.get("state", "Tamil Nadu")
    district = session.get("district", "Vellore")
    ward = session.get("ward", "")
    loc_display = f"{district}, {state}" + (f" (Ward: {ward})" if ward else "")
    review_head = _i18n(lang, "review")
    issue_lbl = _prompt(lang, "issue_label")
    loc_lbl = _prompt(lang, "location_label")
    review_text = f"{review_head}\n\n📝 {issue_lbl}: {session.get('issue', '')}\n📍 {loc_lbl}: {loc_display}"
    review_text += f"\n\n{_i18n(lang, 'notice')}\n\n{_i18n(lang, 'consent')}"
    confirm_btn = _prompt(lang, "consent_btn")
    edit_btn = _prompt(lang, "edit_btn")
    cancel_btn = _prompt(lang, "cancel_btn")
    _text(chat_id, review_text, _keyboard([[(confirm_btn, 'consent')], [(edit_btn, 'edit'), (_flow(lang, 'change'), 'location:change')],
                                          [(_flow(lang, 'ward'), 'ward:add')], [(cancel_btn, 'cancel')]]))


def _message(update, chat_id, ingest_text):
    message = update.get("message") or update.get("edited_message") or {}
    text = str(message.get("text") or "").strip()
    voice_id = (message.get("voice") or {}).get("file_id")
    _send_chat_action(chat_id, "record_voice" if voice_id else "typing")

    if text.startswith("/start"):
        _start(chat_id)
        return
    if text.startswith("/help"):
        _help(chat_id)
        return
    if text.startswith("/language") or text.startswith("/lang"):
        _start(chat_id, choose_language=True)
        return
    if text.startswith("/cancel") or text.startswith("/reset"):
        session = _session(chat_id)
        lang = _lang(session)
        _clear(chat_id)
        _text(chat_id, _prompt(lang, "cancelled_msg"))
        return
    if text.startswith("/status"):
        parts = text.split(maxsplit=1)
        if len(parts) != 2:
            _text(chat_id, "Please provide a ticket ID. Example: /status NVB-20260924ABC1")
        else:
            _status(chat_id, parts[1].strip())
        return

    session = _session(chat_id)
    stage = session.get("stage")
    lang = _lang(session)

    if stage == "language":
        _start(chat_id)
    elif stage == "issue":
        voice_id = (message.get("voice") or {}).get("file_id")
        photos = message.get("photo")
        caption = str(message.get("caption") or "").strip()
        if voice_id:
            session['voice_file_id'] = voice_id
            _queue_voice(chat_id, session)
            return
        elif photos:
            photo_file_id = photos[-1].get("file_id")
            issue_desc = caption or "Civic hazard photo attached"
            session.update(issue=issue_desc, photo_file_id=photo_file_id, stage="state")
        elif text:
            session.update(issue=text, stage="state")
            session.pop('voice_file_id', None)
        else:
            _text(chat_id, _i18n(lang, "issue"))
            return
        _after_issue(chat_id, session)
    elif stage == "state":
        _text(chat_id, _prompt(lang, "select_state"), _keyboard(STATE_KEYBOARD))
    elif stage == "district":
        state = session.get("state", "Tamil Nadu")
        if text:
            matched = _find_matching_district(state, text)
            if matched:
                session.update(district=matched, location=matched, ward='', stage="review")
                _save(chat_id, session)
                _send_review(chat_id, session)
            else:
                err_text = _prompt(lang, "district_not_found").format(text=text, state=state)
                keyboard = DISTRICT_KEYBOARDS.get(state, DISTRICT_KEYBOARDS["Tamil Nadu"])
                _text(chat_id, err_text, _keyboard(keyboard))
        else:
            keyboard = DISTRICT_KEYBOARDS.get(state, DISTRICT_KEYBOARDS["Tamil Nadu"])
            prompt_text = _prompt(lang, "select_district").format(state=state)
            _text(chat_id, prompt_text, _keyboard(keyboard))
    elif stage == "ward":
        lowered = text.lower()
        ward = "" if lowered in ("skip", "தவிர்க்கவும்", "விட்டுவிடுக", "దాటవేయి", "छोड़ें", "वगळा", "वाद দিয়ক", "ଛାଡ଼ନ୍ତୁ") else text
        session.update(ward=ward, stage="review")
        _save(chat_id, session)
        _send_review(chat_id, session)
    elif stage == "location":
        session.update(location=text, district="Vellore", state="Tamil Nadu", stage="review")
        _save(chat_id, session)
        _send_review(chat_id, session)
    elif stage == 'processing':
        _status(chat_id, session['request_id'], session)
    elif stage == 'transcribing':
        _text(chat_id, _flow(lang, 'voice'))
    elif stage in {"review", "privacy", "location_confirm"}:
        _text(chat_id, "Please use the interactive buttons above to continue or cancel.")
    else:
        _text(chat_id, "Your previous report is registered. Use /status <ticket ID> to track it, or /start to begin a new report.")


def _handle_update_locked(update, ingest_text):
    """Process one Telegram update and return a safe acknowledgement payload."""
    if not isinstance(update, dict):
        raise ValueError("Telegram update must be an object")
    raw = json.dumps(update, sort_keys=True, ensure_ascii=False).encode("utf-8")
    update_id = str(update.get("update_id") or "legacy-" + hashlib.sha256(raw).hexdigest()[:32])
    payload_hash = hashlib.sha256(raw).hexdigest()
    chat_id = None
    if update.get("callback_query"):
        chat_id = ((update["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    else:
        message = update.get("message") or update.get("edited_message") or {}
        chat_id = (message.get("chat") or {}).get("id") or (message.get("from") or {}).get("id")

    db = get_db()
    existing = db.execute("SELECT payload_hash, response_json, status, lease_until_epoch FROM telegram_inbound_events WHERE update_id=?", (update_id,)).fetchone()
    if existing:
        if existing["payload_hash"] != payload_hash:
            raise ValueError("Telegram update ID was reused for different content")
        if existing["response_json"]:
            response = json.loads(existing["response_json"] or "{}")
            response["duplicate"] = True
            return response
        if int(existing["lease_until_epoch"] or 0) > int(time.time()):
            return {"success": True, "channel": "Telegram", "event_id": update_id, "duplicate": True, "processing": True}
        db.execute(
            "UPDATE telegram_inbound_events SET status='processing', lease_until_epoch=?, received_at=? WHERE update_id=?",
            (int(time.time()) + 30, _now(), update_id),
        )
        _commit()
    else:
        db.execute(
            """INSERT INTO telegram_inbound_events(update_id,payload_hash,chat_id,status,response_json,received_at,lease_until_epoch)
               VALUES(?,?,?,'processing',NULL,?,?)""",
            (update_id, payload_hash, str(chat_id) if chat_id is not None else None, _now(), int(time.time()) + 30),
        )
        _commit()

    if chat_id is not None:
        if update.get("callback_query"):
            _callback(update, chat_id, ingest_text)
        else:
            _message(update, chat_id, ingest_text)
    response = {"success": True, "channel": "Telegram", "event_id": update_id, "outbox_pending": _pending_count()}
    db.execute(
        "UPDATE telegram_inbound_events SET status='processed', response_json=?, lease_until_epoch=NULL, processed_at=? WHERE update_id=?",
        (json.dumps(response), _now(), update_id),
    )
    _commit()
    return response


def handle_update(update, ingest_text):
    if not isinstance(update, dict):
        raise ValueError('Telegram update must be an object')
    message = (update.get('callback_query') or {}).get('message') or update.get('message') or update.get('edited_message') or {}
    chat_id = (message.get('chat') or {}).get('id') or (message.get('from') or {}).get('id')
    with _chat_transaction(chat_id or 'unknown'):
        return _handle_update_locked(update, ingest_text)
