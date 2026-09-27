from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    page.goto('http://127.0.0.1:5000/dashboard')
    page.wait_for_timeout(2000)
    
    page.click("button[data-role='admin']")
    page.wait_for_timeout(1000)
    
    panel = page.locator('#policyWorkbench')
    panel.scroll_into_view_if_needed()
    page.screenshot(path=r'C:\Users\ELCOT\.gemini\antigravity\brain\1fa95dbe-28fd-4c21-928b-fa5c93247ba5\admin_suite_verified_408_districts.png')
    print('Admin Suite screenshot saved successfully!')
    browser.close()
