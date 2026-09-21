# -*- coding: utf-8 -*-
"""One-off verification: Gemini translation via Vertex OpenAI transport (UTF-8 safe)."""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from visbharat.config import Config
from visbharat.services.google_ai import GoogleAIClient

g = GoogleAIClient(Config.GOOGLE_AI_API_KEY, project_id="nexus-visbharat")

cases = [
    ("ta", "மூன்று நாட்களாக குடிநீர் வழங்கல் இல்லை.", "drinking water supply absent for three days"),
    ("ta", "சாக்கடை நீர் தெருவில் தேங்கி நிற்கிறது, உடனடியாக சுத்தம் செய்யவும்.", "sewage water stagnant on street, clean immediately"),
    ("te", "మా కాలనీలో రోడ్ పూర్తిగా దెబ్బతింది, వర్షంలో వెళ్లడం కష్టం.", "road in our colony badly damaged, hard to travel in rain"),
]

for lang, text, expected in cases:
    try:
        r = g.translate_text(text, lang, "en")
        print({"lang": lang, "model": r.get("model"), "translation": r.get("translated_text"), "expected_meaning": expected})
    except Exception as e:
        print({"lang": lang, "failed": str(e)[:200]})
