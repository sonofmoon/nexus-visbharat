import re

def inspect_tabs(html_file):
    with open(html_file, 'r', encoding='utf-8') as f:
        html = f.read()
    print(f"=== TABS IN {html_file} ===")
    # Look for button or a tags with tab role, class, or data attributes
    buttons = re.findall(r'<button[^>]+>', html)
    for b in buttons:
        if 'tab' in b.lower() or 'nav' in b.lower():
            print(' ', b.strip())

inspect_tabs('templates/dashboard.html')
inspect_tabs('templates/analyst_workbench.html')
inspect_tabs('templates/auditor_workbench.html')
