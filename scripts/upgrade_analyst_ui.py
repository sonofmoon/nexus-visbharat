"""One-time structural upgrade. Preserves the officer queue and other role suites."""
from pathlib import Path

p=Path('templates/dashboard.html');s=p.read_text(encoding='utf-8')
start=s.index(' <!-- Analyst Role Suite')
end=s.index(' <!-- Auditor Role Suite',start)
s=s[:start]+" {% include 'analyst_workbench.html' %}\n\n"+s[end:]
s=s.replace('</head>','<link rel="stylesheet" href="/static/css/analyst-workbench.css">\n</head>')
s=s.replace('<script src="/static/js/dashboard-react-layer.js"></script>','<script src="/static/js/analyst-workbench.js?v=1"></script>\n <script src="/static/js/dashboard-react-layer.js"></script>')
s=s.replace('>Languages</div>','>Languages represented</div>').replace('>Resolution Rate</div>','>Operational closure</div>')
s=s.replace('>Demand-Spend Gap</option>','>Service coverage gap</option>').replace('>Spend Layer</option>','>Approved cost estimates</option>')
s=s.replace('Next-Quarter Prediction','Demand screening').replace('<span class="panel-badge">Vertex AI</span>','<span class="panel-badge">Observed indicators</span>')
s=s.replace('<option value="Sanitation">Sanitation</option>','<option value="Sanitation">Sanitation</option>\n <option value="Transport">Transport</option><option value="Housing">Housing</option><option value="Digital Connectivity">Digital Connectivity</option><option value="Other">Other</option>',1)
anchor=' <button class="btn btn-primary btn-sm" id="refreshBtn"'
pos=s.index(anchor)
extra=''' <div class="filter-group"><label for="filterDateFrom">From (UTC)</label><input class="filter-select" id="filterDateFrom" type="date"></div>
 <div class="filter-group"><label for="filterDateTo">Through (UTC)</label><input class="filter-select" id="filterDateTo" type="date"></div>
 <div class="filter-group"><label for="filterLanguage">Language</label><select class="filter-select" id="filterLanguage"><option value="">All languages</option><option value="ta">Tamil</option><option value="te">Telugu</option><option value="en">English</option></select></div>
 <div class="filter-group"><label for="filterChannel">Channel</label><select class="filter-select" id="filterChannel"><option value="">All channels</option><option>Web Form</option><option>Voice IVR</option><option>IVR Missed Call</option><option>WhatsApp</option><option>Telegram</option><option>SMS Keyword</option><option>Email/Open API</option></select></div>
'''
s=s[:pos]+extra+s[pos:];p.write_text(s,encoding='utf-8')

p=Path('static/js/dashboard.js');s=p.read_text(encoding='utf-8')
# Keep the global entry point used by the existing role switcher.
start=s.index('async function runAnalystSimulation() {');end=s.index('\nasync function fetchAndRenderHumanCapitalRoi',start)
s=s[:start]+"async function runAnalystSimulation() { return window.NVBAnalyst?.refresh(); }\n"+s[end:]
s=s.replace("mkBtn('<i class=\"bi bi-diagram-3\"></i> Futures Market', 'btn-outline-primary', 'open_futures')","mkBtn('<i class=\"bi bi-diagram-3\"></i> Project Evidence', 'btn-outline-primary', 'open_futures')")
s=s.replace("'Analyst Executive Suite - Budget & Priority Simulator'","'Analyst workspace — evidence to investment'")
s=s.replace("'Supported languages'","'Languages represented'")
s=s.replace("overallEl.textContent = 'Overall: LIVE';","overallEl.textContent = 'Services configured';").replace("overallEl.textContent = 'Overall: PARTIAL LIVE';","overallEl.textContent = 'Services partly configured';")
s=s.replace("const services = [","const services = [",1)
# Runtime flags prove configuration, not a successful model invocation.
s=s.replace("gridEl.innerHTML = services.map(([label, ok]) => renderAiRuntimeCard(label, ok)).join('');","gridEl.innerHTML = services.map(([label, ok]) => renderAiRuntimeCard(label, ok).replace(/>Live</g, '>Configured<')).join('');")
s=s.replace("modesEl.textContent = '';","modesEl.textContent = 'Configuration status only. Model quality and successful invocation require separate evidence.';")
p.write_text(s,encoding='utf-8')
print('Analyst template and role integration upgraded')
