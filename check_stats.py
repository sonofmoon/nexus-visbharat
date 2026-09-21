import urllib.request
import re

html = urllib.request.urlopen("http://127.0.0.1:5000/").read().decode("utf-8")
matches = re.findall(r'(\d+)\s*</div>\s*<div class="stat-label">\s*([A-Za-z\s]+)\s*</div>', html)

print("Index Stat Cards Rendered:")
for num, label in matches:
    print(f"  • {label.strip()}: {num}")
