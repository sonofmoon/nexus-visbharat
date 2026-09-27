import asyncio
import os
from playwright.async_api import async_playwright

async def snap():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={'width': 1400, 'height': 1200})
        await page.goto('http://127.0.0.1:5000/dashboard', wait_until='domcontentloaded')
        await asyncio.sleep(2)
        btn = page.locator('button.role-suite-btn[data-role="public"]')
        print('Public button count:', await btn.count())
        if await btn.count() > 0:
            await btn.click()
            await asyncio.sleep(2)
        
        path = r'C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5\public_suite_inspected.png'
        await page.screenshot(path=path)
        print('Saved to', path)
        await browser.close()

if __name__ == '__main__':
    asyncio.run(snap())
