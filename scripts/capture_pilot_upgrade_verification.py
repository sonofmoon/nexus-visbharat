"""Capture visual verification screenshots of all 7 implemented Pilot Portal upgrades.
"""
import os
import sys
import time
from pathlib import Path
from playwright.sync_api import sync_playwright

ARTIFACT_DIR = Path(r"C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5")

def capture_screenshots():
    print(f"Saving screenshots to {ARTIFACT_DIR} ...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1366, 'height': 900})
        page = context.new_page()

        # 1. Pilot Home
        print("[+] Capturing Pilot Home (/pilot)...")
        page.goto('http://127.0.0.1:5000/pilot', wait_until='networkidle')
        time.sleep(1)
        page.screenshot(path=str(ARTIFACT_DIR / 'verified_pilot_home_channels.png'), full_page=True)

        # 2. Pilot Submit
        print("[+] Capturing Pilot Submit (/pilot/submit)...")
        page.goto('http://127.0.0.1:5000/pilot/submit', wait_until='networkidle')
        time.sleep(1)
        page.screenshot(path=str(ARTIFACT_DIR / 'verified_pilot_submit_tri_state.png'), full_page=True)

        # 3. Pilot Settings
        print("[+] Capturing Pilot Settings (/pilot/settings)...")
        page.goto('http://127.0.0.1:5000/pilot/settings', wait_until='networkidle')
        time.sleep(1.5)
        page.screenshot(path=str(ARTIFACT_DIR / 'verified_pilot_settings_safeguards.png'), full_page=True)

        # 4. Pilot Dashboard
        print("[+] Capturing Pilot Dashboard (/pilot/dashboard)...")
        page.goto('http://127.0.0.1:5000/pilot/dashboard', wait_until='networkidle')
        time.sleep(1.5)
        page.screenshot(path=str(ARTIFACT_DIR / 'verified_pilot_dashboard_enhancements.png'), full_page=True)

        browser.close()
        print("[+] All verification screenshots captured successfully.")

if __name__ == '__main__':
    capture_screenshots()
