import re
import glob

def find_endpoints(files):
    results = {}
    for fp in files:
        with open(fp, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        eps = set(re.findall(r'[\'"`](/api/[^\'"`\s\?#]+)', content))
        results[fp] = sorted(list(eps))
    return results

print('Admin endpoints:')
admin_eps = find_endpoints(['templates/dashboard.html', 'static/js/dashboard.js'])
for fp, eps in admin_eps.items():
    print(fp)
    for ep in eps:
        print('  ', ep)

print('\nAnalyst endpoints:')
analyst_eps = find_endpoints(['templates/analyst_workbench.html', 'static/js/analyst-workbench.js'])
for fp, eps in analyst_eps.items():
    print(fp)
    for ep in eps:
        print('  ', ep)

print('\nAuditor endpoints:')
auditor_eps = find_endpoints(['templates/auditor_workbench.html', 'static/js/auditor-workbench.js'])
for fp, eps in auditor_eps.items():
    print(fp)
    for ep in eps:
        print('  ', ep)
