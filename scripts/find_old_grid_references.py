import os
import re

PATTERNS = [
    re.compile(r'Southern Grid', re.IGNORECASE),
    re.compile(r'97 Districts', re.IGNORECASE),
    re.compile(r'97 canonical districts', re.IGNORECASE),
    re.compile(r'TN, AP, TS', re.IGNORECASE),
    re.compile(r'Telangana', re.IGNORECASE),
]

ROOT_DIR = '.'
EXCLUDE_DIRS = {'.git', 'node_modules', '__pycache__', '.pytest_cache', 'data', 'scratch', 'instance', '.gemini'}

matches = []

for root, dirs, files in os.walk(ROOT_DIR):
    dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
    for file in files:
        if not file.endswith(('.py', '.html', '.js', '.md', '.css')):
            continue
        filepath = os.path.join(root, file)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    for pat in PATTERNS:
                        if pat.search(line):
                            matches.append((filepath, line_num, line.strip()))
                            break
        except Exception:
            pass

print(f"Total matching lines found: {len(matches)}\n")
for path, line_no, text in matches:
    print(f"{path}:{line_no}: {text[:140]}")
