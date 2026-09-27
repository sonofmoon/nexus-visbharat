import asyncio
from playwright.async_api import async_playwright

async def capture_proof():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 900})

        # 1. Pilot Submit page (/pilot/submit)
        await page.goto("http://127.0.0.1:5000/pilot/submit", wait_until="networkidle", timeout=30000)
        submit_path = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_submit_tristate_languages.png"
        await page.screenshot(path=submit_path, full_page=False)
        print(f"Captured: {submit_path}")

        # 2. Pilot Dashboard (/pilot/dashboard)
        await page.goto("http://127.0.0.1:5000/pilot/dashboard", wait_until="networkidle", timeout=30000)
        # Wait for workspace cards to load
        await page.wait_for_selector(".workspace-heading", timeout=10000)
        await page.wait_for_timeout(2000)
        dash_path = "C:/Users/ELCOT/.gemini/antigravity/brain/1fa95dbe-28fd-4c21-928b-fa5c93247ba5/verified_pilot_dashboard_tristate.png"
        await page.screenshot(path=dash_path, full_page=False)
        print(f"Captured: {dash_path}")

        await browser.close()

if __name__ == "__main__":
    asyncio.run(capture_proof())
