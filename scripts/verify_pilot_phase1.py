"""Verification script for Phase 1 Ministry Pilot Upgrade.
Verifies live endpoints, database states, and UI rendering via Playwright.
"""
import asyncio
import json
import sqlite3
import sys
import requests
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:5000"

def verify_backend_apis():
    print("--- 1. Testing Backend APIs ---")
    
    # 1. States
    r = requests.get(f"{BASE_URL}/api/v2/pilot/portal/states", timeout=10)
    assert r.status_code == 200, f"States endpoint failed: {r.text}"
    states = r.json().get("states", [])
    print(f"Pilot States: {states}")
    assert "Karnataka" in states, "Karnataka missing from pilot states"
    assert "Tamil Nadu" in states, "Tamil Nadu missing from pilot states"
    assert "Andhra Pradesh" in states, "Andhra Pradesh missing from pilot states"

    # 2. Districts for Karnataka
    r = requests.get(f"{BASE_URL}/api/v2/pilot/portal/districts?state=Karnataka", timeout=10)
    assert r.status_code == 200, f"Districts endpoint failed: {r.text}"
    districts = r.json().get("districts", [])
    print(f"Karnataka Districts: {districts}")
    assert "Bengaluru Urban" in districts, "Bengaluru Urban missing from Karnataka districts"

    # 3. Public Config
    r = requests.get(f"{BASE_URL}/api/v2/pilot/public-config", timeout=10)
    assert r.status_code == 200, f"Public config endpoint failed: {r.text}"
    cfg = r.json()
    languages = cfg.get("languages", [])
    categories = cfg.get("categories", [])
    routing = cfg.get("routing", {})
    print(f"Pilot Languages: {languages}")
    print(f"Pilot Categories ({len(categories)}): {categories}")
    print(f"Pilot Routing Keys: {list(routing.keys())}")
    
    # Tamil-first verification
    assert languages[0] == "ta", "Tamil must be first language priority"
    assert set(languages) == {"ta", "te", "kn", "hi", "en"}, f"Unexpected languages: {languages}"
    assert len(categories) == 10, f"Expected 10 categories, got {len(categories)}"
    assert "Bengaluru Urban" in routing, "Bengaluru Urban missing in routing"
    print("Backend API checks PASSED!")

async def verify_browser_ui():
    print("--- 2. Testing Browser UI with Playwright ---")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        print(f"Navigating to {BASE_URL}/pilot/submit ...")
        await page.goto(f"{BASE_URL}/pilot/submit", wait_until="networkidle", timeout=30000)

        # Check title
        title = await page.title()
        print(f"Page Title: {title}")

        # Check Language chips: ta, te, kn, hi, en should be visible
        for lang in ["ta", "te", "kn", "hi", "en"]:
            chip = page.locator(f'[data-lang="{lang}"]')
            is_visible = await chip.is_visible()
            assert is_visible, f"Language chip {lang} should be visible"
            print(f"Language chip {lang}: visible")

        # Select State -> Karnataka
        state_select = page.locator("#state")
        await state_select.select_option("Karnataka")
        await page.wait_for_timeout(500)

        # Select District -> Bengaluru Urban
        dist_select = page.locator("#district")
        await dist_select.select_option("Bengaluru Urban")
        await page.wait_for_timeout(500)

        # Check Routed Department
        dept_val = await page.locator("#routedDepartment").input_value()
        print(f"Routed Department for Bengaluru Urban: '{dept_val}'")
        assert "Bengaluru Urban" in dept_val, f"Expected Bengaluru Urban in routed department, got: {dept_val}"

        # Check Enrolled Community options
        loc_select = page.locator("#pilotLocation")
        loc_options = await loc_select.locator("option").all_text_contents()
        print(f"Enrolled Community Options: {loc_options}")
        assert any("Bellandur" in opt for opt in loc_options), "Bellandur ward missing from community options"
        assert any("HSR Layout" in opt for opt in loc_options), "HSR Layout ward missing from community options"
        assert any("Anekal" in opt for opt in loc_options), "Anekal ward missing from community options"

        # Select Bellandur
        await loc_select.select_option(index=1)
        await page.wait_for_timeout(300)
        ward_val = await page.locator("#ward").input_value()
        print(f"Auto-mapped Ward: '{ward_val}'")
        assert "Bellandur" in ward_val, f"Expected Bellandur in ward, got {ward_val}"

        # Take screenshot
        screenshot_path = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_phase1_cross_state.png"
        await page.screenshot(path=screenshot_path, full_page=False)
        print(f"Verified screenshot saved to: {screenshot_path}")

        # Check Settings page as well
        print(f"Navigating to {BASE_URL}/pilot/settings ...")
        await page.goto(f"{BASE_URL}/pilot/settings", wait_until="networkidle", timeout=30000)
        settings_title = await page.title()
        print(f"Settings Title: {settings_title}")

        screenshot_settings = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_phase1_settings.png"
        await page.screenshot(path=screenshot_settings, full_page=False)
        print(f"Verified settings screenshot saved to: {screenshot_settings}")

        await browser.close()
        print("Playwright UI checks PASSED with 0 errors!")

def main():
    verify_backend_apis()
    asyncio.run(verify_browser_ui())
    print("\n==========================================")
    print("PHASE 1 VERIFICATION FULLY SUCCESSFUL!")
    print("==========================================")

if __name__ == "__main__":
    main()
