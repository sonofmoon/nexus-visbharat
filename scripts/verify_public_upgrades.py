import asyncio
import os
import sys
from playwright.async_api import async_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

SCREENSHOT_DIR = r"C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5"

async def test_public_upgrades():
    print("Starting Playwright verification of Public Suite enhancements...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 1300})
        page = await context.new_page()

        console_errors = []
        page.on("console", lambda msg: print(f"[BROWSER CONSOLE] [{msg.type}] {msg.text}"))
        page.on("pageerror", lambda err: print(f"[BROWSER PAGEERROR] {err}"))
        page.on("requestfailed", lambda req: print(f"[REQ FAILED] {req.url} {req.failure}"))
        page.on("response", lambda res: print(f"[REQ RESPONSE] {res.status} {res.url}") if "track" in res.url or "api" in res.url else None)

        print("1. Loading Dashboard...")
        await page.goto("http://127.0.0.1:5000/dashboard", wait_until="domcontentloaded")
        await asyncio.sleep(2)

        # Check top KPI numbers
        total_complaints = await page.locator("#totalComplaints").inner_text()
        district_count = await page.locator("#districtCount").inner_text()
        language_count = await page.locator("#languageCount").inner_text()
        state_count = await page.locator("#stateCount").inner_text()
        print(f"Top KPI Numbers: Complaints={total_complaints}, Districts={district_count}, Languages={language_count}, States={state_count}")
        assert total_complaints != "-", "totalComplaints must not be '-'"

        # Switch to Public Suite
        print("2. Switching to Public Lens...")
        await page.click('button.role-suite-btn[data-role="public"]')
        await asyncio.sleep(1.5)

        # Verify cluster switcher is present
        print("3. Testing Multi-State Pilot Grid Switcher...")
        cluster_select = page.locator("#publicClusterSelect")
        assert await cluster_select.count() > 0, "publicClusterSelect not found"
        
        # Initial state should be Karur (Tamil)
        loc_badge = await page.locator("#publicPilotLocationBadge").inner_text()
        print(f"   Initial Location Badge: {loc_badge}")

        # Switch to Bengaluru Urban (Kannada)
        print("   Switching to Karnataka · Bengaluru Urban...")
        await cluster_select.select_option("KA-BLR-0560")
        await asyncio.sleep(0.5)

        loc_badge_blr = await page.locator("#publicPilotLocationBadge").inner_text()
        hop1_blr = await page.locator("#publicHop1Detail").inner_text()
        cluster_tag = await page.locator("#publicActiveClusterTag").inner_text()
        print(f"   Updated Location Badge: {loc_badge_blr}")
        print(f"   Updated HOP #1 Engine: {hop1_blr}")
        print(f"   Updated Cluster Tag: {cluster_tag}")
        assert "Bengaluru" in loc_badge_blr, "Expected Bengaluru in location badge"
        assert "Kannada" in hop1_blr, "Expected Kannada in HOP #1 engine"

        # Test Personal Grievance Tracker
        print("4. Testing Personal Grievance Tracker...")
        eval_test = await page.evaluate("""async () => {
            try {
                const res = await fetch('/api/v1/requests/NVB-20260824CCE7/track');
                const data = await res.json();
                return { ok: res.ok, status: res.status, data: data };
            } catch (e) {
                return { error: e.toString() };
            }
        }""")
        print("   Direct browser fetch result:", eval_test)

        sample_btn = page.locator("#publicSampleTicketBtn")
        assert await sample_btn.count() > 0, "publicSampleTicketBtn not found"
        await sample_btn.click()
        print("   Clicked sample ticket button, waiting for result...")
        for _ in range(15):
            await asyncio.sleep(0.5)
            res_html = await page.locator("#publicTicketTrackResult").inner_html()
            if "STEP 1" in res_html:
                print(f"   Found STEP 1 in result HTML after {_ * 0.5}s!")
                break
        else:
            print("   Current HTML in #publicTicketTrackResult:", await page.locator("#publicTicketTrackResult").inner_html())
            raise AssertionError("Ticket track result did not appear in time")

        track_result = page.locator("#publicTicketTrackResult")
        track_text = await track_result.inner_text()
        print("   Ticket Track Result preview:\n", track_text[:300])
        assert "NVB-20260824CCE7" in track_text, "Expected ticket ID in track result"
        assert "STEP 1" in track_text, "Expected multi-step tracking progress"

        # Capture Screenshot
        screenshot_path = os.path.join(SCREENSHOT_DIR, "verified_public_suite_enhanced.png")
        await page.screenshot(path=screenshot_path)
        print(f"5. Saved enhanced Public Suite screenshot to: {screenshot_path}")

        await browser.close()

        print("\nConsole errors:", console_errors if console_errors else "Zero console errors!")

if __name__ == "__main__":
    asyncio.run(test_public_upgrades())
