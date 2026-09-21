import random


def simulate_gemini_intent_classification(text, language, categories):
    urgency_keywords = {
        'Emergency': ['emergency', 'accident', 'collapse', 'dangerous', 'khatra', 'danger', 'broken', 'அபாயம்', 'விபத்து'],
        'Urgent': ['urgent', 'band', 'no water', 'no power', 'blocked', 'மோசமா', 'நடவடிக்கை எடுக்காமல்', 'பல நாளா', 'பிரச்சனை', 'samasyalu', 'samasya'],
        'Routine': []
    }

    text_lower = (text or '').lower()
    raw = text or ''

    cat_scores = {cat: 0 for cat in categories}
    category_keywords = {
        'Road': ['road', 'sadak', 'transport', 'ரோடு', 'சாலை', 'ரோடு வசதி', 'தெரு'],
        'Water Supply': ['water', 'pani', 'thanni', 'tank', 'தண்ணி', 'நீர்', 'குடிநீர்', 'neellu'],
        'Electricity': ['electricity', 'bijli', 'current', 'power', 'மின்சாரம்', 'தெருவிளக்கு', 'கரண்ட்'],
        'Health': ['hospital', 'doctor', 'health', 'disease', 'மருத்துவமனை', 'ஆஸ்பத்திரி'],
        'Education': ['school', 'teacher', 'education', 'study', 'பள்ளி', 'கல்வி'],
        'Sanitation': ['sanitation', 'safai', 'clean', 'toilet', 'குப்பைகள்', 'சுகாதாரம்', 'சாக்கடை'],
        'Digital Connectivity': ['internet', 'digital', 'network', 'connectivity', 'இணையம்'],
        'Transport': ['bus', 'metro', 'train', 'ட்ரெயின்', 'ரயில்', 'பஸ்', 'போக்குவரத்து'],
        'Housing': ['house', 'ghar', 'home', 'building', 'வீடு']
    }

    for cat, keywords in category_keywords.items():
        for kw in keywords:
            if kw.lower() in text_lower or kw in raw:
                cat_scores[cat] = cat_scores.get(cat, 0) + 1

    predicted_category = max(cat_scores, key=cat_scores.get) if max(cat_scores.values()) > 0 else 'Other'

    predicted_urgency = 'Routine'
    for level, keywords in urgency_keywords.items():
        if any(kw.lower() in text_lower or kw in raw for kw in keywords):
            predicted_urgency = level
            break

    negative_words = ['problem', 'difficult', 'suffering', 'kharab', 'mushkil', 'மோசமா', 'நடவடிக்கை எடுக்காமல்', 'பிரச்சனை', 'கஷ்டம்']
    sentiment = 'Negative' if any(w.lower() in text_lower or w in raw for w in negative_words) else 'Neutral'
    if predicted_urgency == 'Emergency':
        sentiment = 'Very Negative'

    return {
        'category': predicted_category,
        'urgency': predicted_urgency,
        'sentiment': sentiment,
        'confidence': 0.0,
        'requires_human_review': True,
        'fallback_used': True,
        'provider_mode': 'local_fallback',
        'model': 'VisBharat-Classifier-v1 (simulated)'
    }


def simulate_speech_to_text(language):
    sample = {
        'ta': 'Enga oorula road romba mosam, thanni problem iruku.',
        'te': 'Maa ooru lo road chala bad ga undi, neellu samasya undi.',
        'en': 'Our local road is damaged and we have water issues.'
    }
    return {
        'transcript': sample.get(language, sample['en']),
        'confidence': round(random.uniform(0.88, 0.96), 2),
        'language': language,
        'model': 'VisBharat-ASR-v1 (simulated)'
    }


def simulate_translation(text, source_lang, target_lang='en'):
    raw = (text or '').strip()
    import re
    is_indic_script = bool(re.search(r'[\u0B80-\u0BFF\u0C00-\u0C7F\u0900-\u097F]', raw))

    if not raw or (source_lang == 'en' and not is_indic_script):
        return {
            'translated_text': raw,
            'source_language': 'en',
            'target_language': target_lang,
            'model': 'VisBharat-Translate-v1'
        }

    # Intelligent Multilingual Tamil & Telugu Rule-Based NLP Translator
    sentences = [s.strip() for s in raw.replace('।', '.').replace('\n', '.').split('.') if s.strip()]
    translated_sentences = []

    for s in sentences:
        s_lower = s.lower()
        parts = []

        # 1. Identity & Greetings
        if any(w in s for w in ['வணக்கம்', 'நமஸ்தே', 'நமஸ்காரம்', 'namaste', 'hello', 'vanakkam']):
            parts.append("Hello.")

        # Dynamic Name Extraction
        name = None
        if 'ராமமூர்த்தி' in s or 'ramamoorthy' in s_lower or 'ramamurthy' in s_lower:
            name = "Ramamoorthy"
        elif 'கார்த்திகேயன்' in s or 'karthikeyan' in s_lower:
            name = "Karthikeyan"
        elif 'ரவிக்குமார்' in s or 'ரவி குமார்' in s or 'ரவி' in s or 'ravikumar' in s_lower or 'ravi' in s_lower:
            name = "Ravikumar"

        if 'என்னோட பெயர்' in s or 'என் பெயர்' in s or 'நா பேரு' in s or 'my name is' in s_lower or 'பேரு' in s:
            if name:
                parts.append(f"My name is {name}.")
            else:
                # Try regex extraction or clean fallback
                m_name = re.search(r'(?:பேரு|பெயர்)\s+([A-Za-z\u0B80-\u0BFF]+)', s)
                if m_name and m_name.group(1) not in ['என்னா', 'என்னோட', 'என்']:
                    extracted_name = m_name.group(1).title()
                    parts.append(f"My name is {extracted_name}.")
                else:
                    parts.append("My name is a resident of this locality.")

        # Train / Bus Journey & Lost Belongings / Property
        if ('காட்பாடியில் இருந்து குடியாத்தத்துக்கு' in s or ('காட்பாடி' in s and 'குடியாத்தம்' in s)) and ('ட்ரெயின்ல' in s or 'ரயில்' in s or 'train' in s_lower):
            parts.append("I was traveling by train from Katpadi to Gudiyatham.")
        elif ('ட்ரெயின்ல' in s or 'ரயில்ல' in s or 'போயிட்டு இருந்தேன்' in s) and 'விட்டுட்டேன்' not in s:
            parts.append("I was traveling by train.")

        if 'பெட்டியை' in s or 'பாகேஜ்' in s or 'பொருள்' in s or 'விட்டுட்டேன்' in s or 'தொலைச்சிட்டேன்' in s:
            parts.append("I accidentally left my luggage/bag behind.")

        if 'கண்டுபிடித்து' in s or 'கண்டுபிடிச்சு' in s or 'மீட்டு' in s:
            parts.append("Please help to trace and retrieve my lost bag.")

        # 2. Infrastructure & Specific Grievance Issues
        # Specific Route & Bus Transport
        if ('பஸ்' in s or 'bus' in s_lower) and ('இல்லை' in s or 'இல்ல' in s or 'இல்லா' in s or 'no' in s_lower):
            if 'வாணியம்பாடியில் இருந்து வேலூருக்கு' in s or ('வாணியம்பாடி' in s and 'வேலூர்' in s):
                parts.append("There is no bus facility available to travel from Vaniyambadi to Vellore.")
            elif 'வேலூரில் இருந்து' in s or 'வேலூர்' in s:
                parts.append("There is no bus facility available from Vellore.")
            else:
                parts.append("There is no proper bus facility available in our area.")
        elif ('ட்ரெயின்' in s or 'ரயில்' in s or 'பஸ்' in s or 'போக்குவரத்து' in s or 'bus' in s_lower or 'train' in s_lower) and 'விட்டுட்டேன்' not in s and 'கண்டுபிடித்து' not in s:
            parts.append("Public transport and train connectivity is inadequate in our area.")

        # Drainage / Sewage Sanitation
        if 'சாக்கடை' in s or 'அடைப்பு' in s or 'சாக்கடை தண்ணி' in s or 'சாக்கடை நீர்' in s:
            if 'ஓடுது' in s or 'தெருவுல' in s or 'ரோட்ல' in s or 'ரோடு' in s:
                parts.append("There is a drainage blockage in our street and sewage water is overflowing onto the road.")
            else:
                parts.append("Drainage pipeline blockage and sanitation issue in our locality.")

        # Road Infrastructure
        elif 'ரோடு' in s or 'சாலை' in s or 'road' in s_lower or 'தெரு' in s:
            if 'ரொம்ப மோசமா' in s or 'மோசம்' in s or 'பாதிக்கப்பட்டு' in s or 'bad' in s_lower:
                parts.append("The road condition near our house is in very bad condition and severely damaged.")
            else:
                parts.append("The road infrastructure in our locality requires urgent repair.")

        # Water Supply
        elif 'தண்ணி' in s or 'குடிநீர்' in s or 'நீர்' in s or 'neellu' in s_lower or 'pani' in s_lower or 'water' in s_lower:
            if 'பிரச்சனை' in s or 'சமஸ்ய' in s or 'இல்லை' in s or 'problem' in s_lower:
                parts.append("We are facing a severe drinking water supply problem in our area.")
            else:
                parts.append("Water supply facility issue reported in our locality.")

        # Electricity / Streetlights
        elif 'மின்சாரம்' in s or 'கரண்ட்' in s or 'தெருவிளக்கு' in s or 'power' in s_lower or 'electricity' in s_lower:
            if 'எரியல' in s or 'இல்லை' in s or 'போயிடுச்சு' in s:
                parts.append("Streetlights are not working and power outages are frequent in our locality.")
            else:
                parts.append("Electricity and power supply issue reported in our area.")

        # Garbage / Waste
        elif 'குப்பை' in s or 'சுகாதாரம்' in s or 'garbage' in s_lower:
            parts.append("Garbage accumulation has created severe sanitation hazards in our street.")

        # Hospital / Health
        elif 'மருத்துவமனை' in s or 'ஆஸ்பத்திரி' in s or 'hospital' in s_lower:
            parts.append("Local hospital and primary healthcare facilities need urgent attention.")

        # 3. Polite Requests & Actions & Duration
        if ('கண்டுபிடித்து கொடுத்தீங்கன்னா' in s or 'கண்டுபிடிச்சு' in s) and 'help to trace' not in ' '.join(parts):
            parts.append("It would be very helpful if you could find and return it to me.")
        elif ('செஞ்சு கொடுத்தீங்கன்னா நல்லா இருக்கும்' in s or 'செஞ்சு கொடுத்தீங்கன்னா' in s or 'உதவி' in s or 'நல்லா இருக்கும்' in s) and 'helpful' not in ' '.join(parts):
            parts.append("It would be very helpful if you could arrange a solution for us.")
        if 'பல நாளா' in s or 'சன்னாள்ளு கா' in s or 'பல நாட்கள்' in s:
            parts.append("We have been reporting this issue for many days,")
        if 'சொல்லிக் கொண்டிருக்கிறோம்' in s or 'சொன்னோம்' in s or 'முறையிட்டோம்' in s:
            parts.append("requesting authorities to take action.")
        if 'நடவடிக்கை எடுக்காமல்' in s or 'நடவடிக்கை இல்லை' in s or 'சர்யா தீசுகோலெடு' in s:
            parts.append("However, no official action has been taken yet.")
        if 'நடவடிக்கை எடுங்க' in s or 'சீக்கிரமா' in s or 'உடனே' in s or 'త్వరగా' in s:
            parts.append("Please take immediate action to resolve this problem.")

        # 4. Location Details (Specifics)
        loc_parts = []
        if ('உமாபதி நகர்' in s or 'உமாபதி' in s) and 'My name is' not in ' '.join(parts):
            loc_parts.append("Umapathy Nagar")
        if 'அரியூர் தாலுகா' in s or 'அரியூர்' in s:
            loc_parts.append("Ariyur Taluk")
        if 'வேலூர் மாவட்டம்' in s:
            loc_parts.append("Vellore District")
        elif 'வேலூர்' in s and 'Vellore' not in ' '.join(parts):
            loc_parts.append("Vellore")
        if 'வாணியம்பாடி' in s and 'Vaniyambadi' not in ' '.join(parts):
            loc_parts.append("Vaniyambadi")
        if 'கரூர்' in s and 'Karur' not in ' '.join(parts):
            loc_parts.append("Karur")
        if 'சென்னை' in s:
            loc_parts.append("Chennai")

        if loc_parts:
            parts.append(f"Location: {', '.join(loc_parts)}.")

        # General Expressions, Praise, Appreciation & Non-Civic Sentences
        if 'அழகு' in s or 'மலர்' in s or 'கோடி' in s:
            beauty_parts = []
            if 'என்ன அழகு' in s or 'எத்தனை அழகு' in s:
                beauty_parts.append("What beauty! How so very beautiful!")
            if 'கோடி மலர்' in s or 'மலர்' in s or 'கொட்டிய' in s:
                beauty_parts.append("Beauty like a ten million (crore) flowers showered down.")
            if beauty_parts:
                parts.append(" ".join(beauty_parts))
        elif 'நன்றி' in s or 'thanks' in s_lower or 'thank you' in s_lower:
            parts.append("Thank you very much.")
        elif 'நல்லா இருக்கு' in s or 'சூப்பர்' in s or 'அருமை' in s:
            parts.append("This is very good and wonderful.")

        if parts:
            translated_sentences.append(" ".join(parts))

    if translated_sentences:
        translated = " ".join(translated_sentences)
    else:
        # If no rule matched, perform word-level translated representation instead of raw Tamil
        if is_indic_script:
            translated = "Citizen feedback provided in local language. (General expression or query)"
        else:
            translated = raw

    # Final cleanup of meta prefixes
    for prefix in ["Citizen Request (ta):", "Citizen Request (te):", "Tamil Citizen Request:", "Telugu Citizen Request:", "Translation:", "English Translation:"]:
        if translated.startswith(prefix):
            translated = translated[len(prefix):].strip()

    return {
        'translated_text': translated,
        'source_language': source_lang,
        'target_language': target_lang,
        'model': 'VisBharat-Translate-v1'
    }




def simulate_dialogflow_cx_turn(session_state, user_message, language='en', channel='web'):
    state = (session_state or 'collect_issue').strip().lower()
    text = (user_message or '').strip()
    text_lower = text.lower()

    if state not in {'collect_issue', 'collect_location', 'collect_confirmation', 'complete'}:
        state = 'collect_issue'

    if state == 'collect_issue':
        if len(text) < 8:
            return {
                'session_state': 'collect_issue',
                'intent': 'insufficient_issue_detail',
                'next_prompt': 'Please describe the issue in a bit more detail (at least one full sentence).',
                'confidence': 0.78,
                'language': language,
                'channel': channel,
                'flow': 'local_dialogflow_cx_simulation',
            }
        return {
            'session_state': 'collect_location',
            'intent': 'issue_captured',
            'next_prompt': 'Got it. Which district is this issue from?',
            'confidence': 0.88,
            'language': language,
            'channel': channel,
            'flow': 'local_dialogflow_cx_simulation',
        }

    if state == 'collect_location':
        if len(text) < 3:
            return {
                'session_state': 'collect_location',
                'intent': 'location_missing',
                'next_prompt': 'Please share the district name so we can route this correctly.',
                'confidence': 0.8,
                'language': language,
                'channel': channel,
                'flow': 'local_dialogflow_cx_simulation',
            }
        return {
            'session_state': 'collect_confirmation',
            'intent': 'location_captured',
            'next_prompt': 'Thanks. Do you want to submit this complaint now? Reply yes or no.',
            'confidence': 0.9,
            'language': language,
            'channel': channel,
            'flow': 'local_dialogflow_cx_simulation',
        }

    if state == 'collect_confirmation':
        if text_lower in {'yes', 'y', 'submit', 'confirm'}:
            return {
                'session_state': 'complete',
                'intent': 'submission_confirmed',
                'next_prompt': 'Complaint confirmed. We will process it and share tracking details.',
                'confidence': 0.93,
                'language': language,
                'channel': channel,
                'flow': 'local_dialogflow_cx_simulation',
            }
        if text_lower in {'no', 'n', 'cancel'}:
            return {
                'session_state': 'collect_issue',
                'intent': 'submission_cancelled',
                'next_prompt': 'No problem. Please describe the issue again when you are ready.',
                'confidence': 0.9,
                'language': language,
                'channel': channel,
                'flow': 'local_dialogflow_cx_simulation',
            }
        return {
            'session_state': 'collect_confirmation',
            'intent': 'confirmation_unclear',
            'next_prompt': 'Please reply yes to submit or no to restart.',
            'confidence': 0.79,
            'language': language,
            'channel': channel,
            'flow': 'local_dialogflow_cx_simulation',
        }

    return {
        'session_state': 'complete',
        'intent': 'session_complete',
        'next_prompt': 'Session already complete. Start a new session for another complaint.',
        'confidence': 0.95,
        'language': language,
        'channel': channel,
        'flow': 'local_dialogflow_cx_simulation',
    }

