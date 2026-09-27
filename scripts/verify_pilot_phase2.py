"""Phase 2 Verification: Multilingual Speech & Regional AI Classification + Distinct Pilot Styling.
Tests Kannada, Hindi, Tamil, Telugu, English processing, worker flow, and captures visual proof.
"""
import asyncio
import json
import secrets
import sqlite3
import sys
from pathlib import Path
import requests
from playwright.async_api import async_playwright

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
sys.stdout.reconfigure(encoding='utf-8')

BASE_URL = "http://127.0.0.1:5000"

def test_multilingual_ai_endpoints():
    print("--- 1. Testing Multilingual Classification & Translation ---")
    from visbharat.services.google_ai import GoogleAIClient
    from visbharat.services.ai_simulation import simulate_gemini_intent_classification, simulate_translation

    categories = [
        "Water Supply", "Road", "Sanitation", "Electricity", "Health",
        "Education", "Transport", "Housing", "Digital Connectivity", "Other"
    ]

    cases = [
        ("ta", "எங்கள் பகுதியில் 4 நாட்களாக குடிநீர் வரவில்லை", "Water Supply"),
        ("te", "మా వీధిలో తాగునీరు రావడం లేదు, దయచేసి చూడండి", "Water Supply"),
        ("kn", "ನಮ್ಮ ಬಡಾವಣೆಯಲ್ಲಿ ಕುಡಿಯುವ ನೀರು ಪೂರೈಕೆ ಸ್ಥಗಿತಗೊಂಡಿದೆ", "Water Supply"),
        ("hi", "सड़क पर बड़ा गड्ढा है जिससे दुर्घटनाएं हो रही हैं", "Road"),
        ("kn", "ಬೆಳ್ಳಂದೂರು ಮುಖ್ಯ ರಸ್ತೆಯಲ್ಲಿ ದೊಡ್ಡ ಗುಂಡಿಗಳು ಬಿದ್ದಿವೆ", "Road"),
        ("hi", "मोहल्ले में बिजली का तार टूटकर नीचे गिर गया है", "Electricity"),
        ("en", "Streetlights on Main Road are completely damaged and dark", "Electricity"),
    ]

    for lang, text, expected_cat in cases:
        res = simulate_gemini_intent_classification(text, lang, categories)
        print(f"[{lang.upper()}] '{text[:35]}...' -> Cat: {res['category']} | Urg: {res['urgency']}")
        assert res['category'] == expected_cat, f"Expected {expected_cat}, got {res['category']} for '{text}'"

        trans = simulate_translation(text, lang, "en")
        print(f"      Translation: '{trans['translated_text']}'")
        assert len(trans['translated_text'].strip()) > 5, "Translation output too short"

    print("Multilingual classification & translation tests PASSED!")

def test_kannada_pilot_submission_and_worker():
    print("\n--- 2. Testing Pilot Submission with Kannada & Bengaluru Urban Location ---")
    from visbharat.services import pilot, pilot_worker
    from visbharat import create_app

    app = create_app({"TESTING": True, "DATABASE_PATH": str(BASE_DIR / "visbharat.db")})

    with app.app_context():
        # Check intake via pilot.intake
        intake_data = {
            "location_id": "bengaluru_urban-1",
            "language": "kn",
            "text": "ಬೆಳ್ಳಂದೂರು ವಾರ್ಡ್ 150 ರಲ್ಲಿ ಕಳೆದ ಮೂರು ದಿನಗಳಿಂದ ಕಾವೇರಿ ಕುಡಿಯುವ ನೀರು ಸರಬರಾಜು ಆಗುತ್ತಿಲ್ಲ.",
            "consent_granted": True
        }
        test_key = f"phase2-kn-test-key-{secrets.token_hex(4)}"
        res = pilot.intake(pilot.PILOT_ID, intake_data, test_key)
        print(f"Intake Response: request_id={res['request_id']}, status={res.get('status')}")
        assert res['request_id'].startswith("NVB-"), "Invalid request ID"

        # Run worker processing for this request
        job = pilot.rows("SELECT job_id FROM pilot_outbox WHERE request_id=?", (res['request_id'],))[0]
        worker_res = pilot_worker.run_one(job['job_id'])
        print(f"Worker Result: {worker_res}")

        # Verify tracking
        track_rec = pilot.tracking(res['request_id'], res['tracking_secret'])
        print(f"Track Record: status={track_rec['status']}, dept={track_rec['routed_department']}, translated={track_rec.get('translated_text')}")
        assert "Bengaluru Urban" in track_rec['routed_department'], "Department not correctly routed"

    print("Kannada submission and worker execution PASSED!")

async def capture_distinct_styling_screenshots():
    print("\n--- 3. Testing Distinct Pilot Background & Capturing Visual Comparison ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. Main Portal Submit (/submit) -> should be white/blue
        await page.goto(f"{BASE_URL}/submit", wait_until="networkidle", timeout=30000)
        main_submit_img = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_main_portal_submit.png"
        await page.screenshot(path=main_submit_img, full_page=False)
        print(f"Saved Main Portal Submit screenshot: {main_submit_img}")

        # 2. Pilot Portal Submit (/pilot/submit) -> should be Distinct Sage/Teal Wash
        await page.goto(f"{BASE_URL}/pilot/submit", wait_until="networkidle", timeout=30000)
        pilot_submit_img = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_portal_submit_sage_bg.png"
        await page.screenshot(path=pilot_submit_img, full_page=False)
        print(f"Saved Pilot Portal Submit screenshot: {pilot_submit_img}")

        # 3. Pilot Portal Home (/pilot) -> should have distinct background + indicator badge
        await page.goto(f"{BASE_URL}/pilot", wait_until="networkidle", timeout=30000)
        pilot_home_img = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_portal_home_sage_bg.png"
        await page.screenshot(path=pilot_home_img, full_page=False)
        print(f"Saved Pilot Portal Home screenshot: {pilot_home_img}")

        # 4. Pilot Dashboard (/pilot/dashboard) -> should have distinct background
        await page.goto(f"{BASE_URL}/pilot/dashboard", wait_until="networkidle", timeout=30000)
        pilot_dash_img = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_dashboard_sage_bg.png"
        await page.screenshot(path=pilot_dash_img, full_page=False)
        print(f"Saved Pilot Dashboard screenshot: {pilot_dash_img}")

        await browser.close()
    print("Visual comparison screenshots captured successfully!")

def main():
    test_multilingual_ai_endpoints()
    test_kannada_pilot_submission_and_worker()
    asyncio.run(capture_distinct_styling_screenshots())
    print("\n==========================================")
    print("PHASE 2 UPGRADE & VERIFICATION COMPLETED!")
    print("==========================================")

if __name__ == "__main__":
    main()
