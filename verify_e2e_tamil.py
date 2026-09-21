# -*- coding: utf-8 -*-
"""One-off verification: end-to-end Tamil civic request through the app pipeline."""
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from visbharat import create_app

app = create_app()
client = app.test_client()

resp = client.post(
    "/api/submit",
    json={
        "text": "சாக்கடை நீர் தெருவில் தேங்கி நிற்கிறது, உடனடியாக சுத்தம் செய்யவும்.",
        "language": "ta",
        "district": "Karur",
        "source": "Verification E2E",
    },
)
p = resp.get_json() or {}
print({
    "http": resp.status_code,
    "success": p.get("success"),
    "request_id": p.get("request_id"),
    "classification_model": (p.get("classification") or {}).get("model"),
    "category": (p.get("classification") or {}).get("category"),
    "translated_text": p.get("translated_text") or (p.get("translation") or {}).get("translated_text"),
})

g = app.extensions.get("google_ai_client")
print("policy_brief:", g.generate_policy_brief("Karur", 57, 4, ["Sanitation", "Water Supply", "Road"]).get("model"))

rag = client.get(
    "/api/v1/policy/rag-brief?district=Karur&query=sanitation",
    headers={"Authorization": "Bearer " + app.config["ANALYST_API_TOKEN"]},
).get_json() or {}
gov = ((rag.get("citation_chain") or {}).get("governance") or {})
print({"rag_mode": gov.get("retrieval_mode"), "rag_model": gov.get("llm_model")})
