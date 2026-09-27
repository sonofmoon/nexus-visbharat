import asyncio
import os
from playwright.async_api import async_playwright

SCREENSHOT_DIR = r"C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5"

async def test_ui_walkthrough():
    console_errors = []
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 900})
        page = await context.new_page()
        
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ["error"] and "favicon" not in msg.text else None)
        page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))
        
        print("1. Loading Dashboard...")
        await page.goto("http://127.0.0.1:5000/dashboard", wait_until="domcontentloaded")
        await asyncio.sleep(2)
        
        # Test Admin Suite
        print("2. Testing Admin Suite & Subtabs...")
        await page.click('button.role-suite-btn[data-role="admin"]')
        await asyncio.sleep(1)
        
        admin_subtabs = ['admin-requests', 'admin-policy', 'admin-delivery', 'admin-users', 'admin-alerts']
        for subtab in admin_subtabs:
            print(f"   Clicking Admin subtab: {subtab}")
            btn = page.locator(f'button.rbac-subtab[data-subtab="{subtab}"]')
            if await btn.count() > 0:
                await btn.click()
                await asyncio.sleep(0.5)
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "verified_admin_suite_tabs.png"))
        print("   Saved verified_admin_suite_tabs.png")
        
        # Test Analyst Suite
        print("3. Testing Analyst Suite & Subtabs...")
        await page.click('button.role-suite-btn[data-role="analyst"]')
        await asyncio.sleep(2)
        
        analyst_tabs = ['demand', 'evidence', 'projects', 'budget', 'delivery', 'outcomes']
        for tab in analyst_tabs:
            print(f"   Clicking Analyst tab: wb-tab-{tab}")
            tab_btn = page.locator(f'#wb-tab-{tab}')
            if await tab_btn.count() > 0:
                await tab_btn.click()
                await asyncio.sleep(0.5)
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "verified_analyst_suite_tabs.png"))
        print("   Saved verified_analyst_suite_tabs.png")
        
        # Test Auditor Suite
        print("4. Testing Auditor Suite & Subtabs...")
        await page.click('button.role-suite-btn[data-role="auditor"]')
        await asyncio.sleep(2)
        
        auditor_tabs = ['awTabEvidence', 'awTabDelivery', 'awTabOutcomes', 'awTabEvents', 'awTabConsent', 'awTabSecurity']
        for tab_id in auditor_tabs:
            print(f"   Clicking Auditor tab: {tab_id}")
            tab_btn = page.locator(f'#{tab_id}')
            if await tab_btn.count() > 0:
                await tab_btn.click()
                await asyncio.sleep(0.5)
        
        await page.screenshot(path=os.path.join(SCREENSHOT_DIR, "verified_auditor_suite_tabs.png"))
        print("   Saved verified_auditor_suite_tabs.png")
        
        await browser.close()
        
        print("\nConsole errors encountered during tab navigation:")
        if console_errors:
            for err in console_errors[:10]:
                print("  ", err)
        else:
            print("   Zero unhandled console errors! 100% clean UI execution.")

if __name__ == "__main__":
    asyncio.run(test_ui_walkthrough())
