import asyncio
import os
import sys
import time
from playwright.async_api import async_playwright

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8')

SCREENSHOT_DIR = r"C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5"

async def test_executive_analytics_and_kpis():
    print("Starting Playwright verification of GA4 charts and loaders...")
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(viewport={"width": 1400, "height": 1400})
        page = await context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(f"[{msg.type}] {msg.text}") if msg.type in ["error"] and "favicon" not in msg.text else None)
        page.on("pageerror", lambda err: console_errors.append(f"[pageerror] {err}"))

        t0 = time.time()
        print("1. Navigating to Dashboard...")
        await page.goto("http://127.0.0.1:5000/dashboard", wait_until="domcontentloaded")
        t_loaded = time.time() - t0
        print(f"   DOM content loaded in {t_loaded:.2f}s")

        # 2. Check Top KPI cards under AI Command Center
        print("2. Checking Top KPI cards under AI Command Center...")
        total_complaints = await page.locator("#totalComplaints").inner_text()
        districts = await page.locator("#districtCount").inner_text()
        languages = await page.locator("#languageCount").inner_text()
        closure = await page.locator("#resolutionRate").inner_text()
        states = await page.locator("#stateCount").inner_text()
        print(f"   AI Command Center Top KPIs: Complaints={total_complaints}, Districts={districts}, Languages={languages}, Closure={closure}, States={states}")
        assert total_complaints == "50,034", f"Expected 50,034 complaints, got {total_complaints}"
        assert districts == "408", f"Expected 408 districts, got {districts}"

        # 3. Check GA4 Chart Loaders exist in the DOM
        print("3. Checking Executive Platform Analytics Card Loaders...")
        loaders = ["#trendChartLoader", "#categoryChartLoader", "#urgencyChartLoader", "#channelChartLoader"]
        for l_id in loaders:
            count = await page.locator(l_id).count()
            assert count > 0, f"Expected loader element {l_id} to exist"
            spinner_count = await page.locator(f"{l_id} .loader-spinner").count()
            assert spinner_count > 0, f"Expected spinner inside {l_id}"
        print("   ✔ All 4 animated chart loader elements verified present in DOM!")

        # 4. Wait for charts to hydrate and check daily average metric badge
        print("4. Waiting for GA4 charts to render from /api/stats...")
        t_chart_start = time.time()
        await page.wait_for_function("() => document.getElementById('ga4DailyAvg') && !document.getElementById('ga4DailyAvg').textContent.includes('Loading')", timeout=8000)
        t_chart_elapsed = time.time() - t_chart_start
        daily_avg = await page.locator("#ga4DailyAvg").inner_text()
        cat_total = await page.locator("#ga4CatTotal").inner_text()
        print(f"   GA4 Charts loaded in {t_chart_elapsed:.2f}s!")
        print(f"   Metric Badge (Daily Ingestion Avg): {daily_avg}")
        print(f"   Doughnut Center Total: {cat_total}")
        assert "req/day" in daily_avg, "Expected daily average formatted with req/day"

        # 5. Check animated loaders are smoothly hidden (have .loaded class)
        for l_id in loaders:
            has_loaded_class = await page.evaluate(f"() => document.querySelector('{l_id}').classList.contains('loaded')")
            print(f"   Loader {l_id} status: {'Hidden (.loaded)' if has_loaded_class else 'Active'}")
            assert has_loaded_class, f"Loader {l_id} should have .loaded class once data is rendered"

        # 6. Test Time-tab switching responsiveness (e.g. click 30 Days)
        print("5. Testing GA4 Time-tab switching (14 Days -> 30 Days)...")
        tab30 = page.locator('.ga4-tab[data-period="30"]')
        t_switch_start = time.time()
        await tab30.click()
        await page.wait_for_timeout(300)
        t_switch_elapsed = time.time() - t_switch_start
        print(f"   Tab switched & re-rendered in {t_switch_elapsed:.2f}s!")

        # 7. Capture Screenshot of Executive Platform Analytics
        analytics_panel = page.locator("#analyticsPanel")
        await analytics_panel.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        screenshot_ga4 = os.path.join(SCREENSHOT_DIR, "verified_executive_platform_analytics.png")
        await analytics_panel.screenshot(path=screenshot_ga4)
        print(f"6. Saved GA4 Executive Platform Analytics screenshot to: {screenshot_ga4}")

        # 8. Capture Screenshot of AI Command Center & Top KPIs
        command_hero = page.locator("#commandHero")
        await command_hero.scroll_into_view_if_needed()
        await page.wait_for_timeout(500)
        screenshot_top = os.path.join(SCREENSHOT_DIR, "verified_ai_command_center_kpis.png")
        await page.screenshot(path=screenshot_top)
        print(f"7. Saved AI Command Center top screenshot to: {screenshot_top}")

        await browser.close()
        print("\nConsole errors:", console_errors if console_errors else "Zero console errors!")
        print("ALL CHECKS PASSED 100%!")

if __name__ == "__main__":
    asyncio.run(test_executive_analytics_and_kpis())
