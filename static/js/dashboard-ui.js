(function () {
  'use strict';

  const subscribers = new Set();
  let portfolioExpanded = false;
  let snapshot = { stats: null, projects: [], role: 'analyst', updatedAt: null };
  const number = value => Number.isFinite(Number(value)) ? Number(value).toLocaleString() : '—';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]));
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function insightCopy(state) {
    if (!state.stats) return { title: 'Your decision brief, at a glance', body: 'Your current demand and resolution metrics will appear here when the dashboard finishes loading.', source: 'Awaiting dashboard data' };
    const stats = state.stats;
    const project = state.projects[0];
    const priority = project ? ' The top-ranked project is ' + (project.project_title || [project.district, project.category, 'project'].filter(Boolean).join(' ')) + '.' : '';
    return {
      title: number(stats.resolutionRate) + '% resolution rate in the current scope',
      body: number(stats.totalComplaints) + ' citizen requests across ' + number(stats.districtCount) + ' districts.' + priority,
      source: 'From dashboard metrics' + (project ? ' and priority rankings' : '') + ' · Updated ' + state.updatedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    };
  }

  function publish(update) {
    snapshot = { ...snapshot, ...update };
    subscribers.forEach(subscriber => subscriber(snapshot));
  }

  window.NVBConsole = {
    escape,
    insightCopy,
    renderProjectCard(project, index, expanded) {
      const recommendation = project.policy_recommendation || {};
      const rawScore = project.social_priority_score ?? (project.priority_score == null ? null : Number(project.priority_score) * 100);
      const score = rawScore == null || !Number.isFinite(Number(rawScore)) ? null : Number(rawScore);
      const title = project.project_title || [project.district, project.category || 'Infrastructure', 'project'].filter(Boolean).join(' ');
      const key = String(project.project_id || [project.state, project.district, title].join('|'));
      const cost = recommendation.estimated_cost || (project.estimated_cost_lakhs != null ? 'INR ' + number(project.estimated_cost_lakhs) + ' lakh' : 'Not supplied');
      const timeline = recommendation.timeline || project.timeline || 'Not supplied';
      const risk = project.risk_level || recommendation.risk_level || 'Not supplied';
      const beneficiaries = recommendation.beneficiary_count ?? project.beneficiaries;
      const stats = [['Funding', cost], ['Timeline', timeline], ['Beneficiaries', beneficiaries == null ? 'Not supplied' : number(beneficiaries)], ['Risk level', risk]];
      const subIndices = Object.entries(project.sub_indices || {}).map(([label, value]) => '<p><strong>' + escape(label.replace(/_/g, ' ')) + ':</strong> ' + escape(value) + '</p>').join('');
      const scoreLabel = project.social_priority_score != null ? 'Social priority score' : 'Priority score';
      return '<article class="project-item"' + (index >= 6 ? ' data-project-overflow' + (portfolioExpanded ? '' : ' hidden') : '') + '><div class="project-heading"><span class="project-rank-badge">#' + escape(project.sps_rank || index + 1) + '</span><h4 class="project-title">' + escape(title) + '</h4></div>'
        + '<p class="project-location">' + escape([project.district, project.state].filter(Boolean).join(', ')) + '</p>'
        + '<dl class="project-stats">' + stats.map(([label, value]) => '<div><dt>' + label + '</dt><dd>' + escape(value) + '</dd></div>').join('') + '</dl>'
        + '<div class="project-score-row"><span>' + scoreLabel + '</span><strong>' + (score == null ? 'Not supplied' : score.toFixed(1) + ' / 100') + '</strong></div>'
        + '<div class="project-score-track" aria-hidden="true"><span style="width:' + (score == null ? 0 : Math.max(0, Math.min(100, score))) + '%"></span></div>'
        + '<details class="project-details" data-project-key="' + escape(key) + '"' + (expanded.has(key) ? ' open' : '') + '><summary>View project details</summary>'
        + '<p><strong>Agency:</strong> ' + escape(recommendation.implementing_agency || 'Not supplied') + '</p>'
        + (recommendation.fiscal_scheme_fit ? '<p><strong>Scheme alignment:</strong> ' + escape(recommendation.fiscal_scheme_fit) + '</p>' : '')
        + (recommendation.expected_impact ? '<p><strong>Expected impact:</strong> ' + escape(recommendation.expected_impact) + '</p>' : '')
        + subIndices
        + '<button type="button" class="btn btn-secondary" data-focus-district="' + escape(project.district || '') + '" data-project-state="' + escape(project.state || '') + '">Focus district</button></details></article>';
    },
    subscribe(subscriber) {
      subscribers.add(subscriber);
      subscriber(snapshot);
      return () => subscribers.delete(subscriber);
    },
    setSnapshot(update) { publish({ ...update, updatedAt: new Date() }); },
    setStats(stats) { publish({ stats, updatedAt: new Date() }); },
    projectFooter(count) { return count > 6 ? '<button class="btn btn-secondary portfolio-toggle" type="button" data-project-toggle="' + count + '" aria-expanded="' + portfolioExpanded + '">' + (portfolioExpanded ? 'Show top 6 projects' : 'View all ' + count + ' projects') + '</button>' : ''; },
    setProjects(projects) { publish({ projects }); },
    setRole(role) { document.body.dataset.role = role; publish({ role }); },
    setMap(points, layer, count) {
      const label = document.getElementById('mapLayerLabel');
      const status = document.getElementById('mapDataStatus');
      if (label) label.textContent = ({ demand: 'Demand layer', spend: 'Public spend', gap: 'Demand–spend gap', priority: 'Priority layer' })[layer] || 'District signals';
      if (status) status.textContent = number(points) + ' districts · ' + number(count) + ' critical hotspots';
    }
  };

  function readPreference(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
  }

  function savePreference(key, value) {
    try { localStorage.setItem(key, value); } catch (_) {}
  }

  function applyTheme(theme) {
    document.body.dataset.theme = theme;
    const button = document.getElementById('themeSwitch');
    const dark = theme === 'dark';
    button.setAttribute('aria-pressed', String(dark));
    button.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
    button.firstElementChild.className = dark ? 'bi bi-sun' : 'bi bi-moon';
    if (window.Chart) Object.values(Chart.instances).forEach(chart => chart.update('none'));
  }

  function updateKpis(state) {
    const stats = state.stats;
    if (!stats) return;
    const captions = { totalComplaints: 'Requests in current scope', languageCount: 'Languages represented', districtCount: 'Districts represented', resolutionRate: 'Resolved or Closed / scoped requests', stateCount: 'States represented' };
    Object.entries(captions).forEach(([id, caption]) => {
      const card = document.getElementById(id)?.closest('.stat-card-mini');
      if (!card) return;
      let note = card.querySelector('.kpi-note');
      if (!note) { note = document.createElement('span'); note.className = 'kpi-note'; card.querySelector('.stat-info').append(note); }
      note.textContent = caption;
    });
    const entries = Object.entries(stats.dailyTrend || {}).sort(([left], [right]) => left.localeCompare(right)).filter(([, value]) => Number.isFinite(Number(value)));
    const card = document.getElementById('totalComplaints').closest('.stat-card-mini');
    card.querySelector('.kpi-spark')?.remove();
    if (entries.length < 2) return;
    const values = entries.slice(-14).map(([, value]) => Number(value));
    const low = Math.min(...values), high = Math.max(...values);
    const points = values.map((value, index) => (index * 56 / (values.length - 1)).toFixed(1) + ',' + (20 - 18 * (value - low) / (high - low || 1)).toFixed(1)).join(' ');
    const spark = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
    spark.setAttribute('viewBox', '0 0 56 22');
    spark.setAttribute('aria-hidden', 'true');
    spark.classList.add('kpi-spark');
    const line = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
    line.setAttribute('points', points);
    line.setAttribute('fill', 'none');
    line.setAttribute('stroke', 'currentColor');
    line.setAttribute('stroke-width', '2');
    spark.append(line);
    card.append(spark);
    card.querySelector('.kpi-note').textContent = 'Daily request trend';
    spark.setAttribute('data-source', 'daily_trend');
  }

  function enhanceCharts() {
    if (!window.Chart) return;
    Chart.defaults.font.family = "'Google Sans', Inter, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.animation.duration = reducedMotion() ? 0 : 200;
    Chart.register({
      id: 'nvbPresentation',
      beforeUpdate(chart) {
        const tokens = getComputedStyle(document.body);
        const foreground = tokens.getPropertyValue('--console-muted').trim();
        const border = tokens.getPropertyValue('--console-border').trim();
        const options = chart.config.options;
        options.plugins = options.plugins || {};
        options.plugins.tooltip = { ...options.plugins.tooltip, padding: 12, cornerRadius: 10, titleFont: { size: 13, family: "'Google Sans', sans-serif" }, bodyFont: { size: 12, family: "'Google Sans', sans-serif" }, backgroundColor: '#202b3d', titleColor: '#ffffff', bodyColor: '#ffffff' };
        options.layout = { padding: 8 };
        for (const scale of Object.values(options.scales || {})) {
          scale.ticks = { ...scale.ticks, font: { size: 11, family: "'Google Sans', Inter, sans-serif" }, color: foreground, maxTicksLimit: 7, autoSkip: true, maxRotation: 0, padding: 8 };
          scale.grid = { ...scale.grid, color: border };
          scale.border = { display: false };
        }
        if (options.plugins.legend) {
          options.plugins.legend.labels = { ...options.plugins.legend.labels, color: foreground, font: { size: 11 }, padding: 16, usePointStyle: true, boxWidth: 8 };
          if (chart.config.type === 'doughnut') options.plugins.legend.position = 'bottom';
        }
      },
      afterUpdate(chart) {
        const card = chart.canvas.closest('.ga4-card');
        if (!card) return;
        const title = card.querySelector('h4').textContent.trim();
        chart.canvas.setAttribute('role', 'img');
        chart.canvas.setAttribute('aria-label', title + '. Values are available in the adjacent data table.');
        let details = card.querySelector('.chart-data');
        if (!details) {
          details = document.createElement('details');
          details.className = 'chart-data';
          details.innerHTML = '<summary>View chart data</summary><div></div>';
          card.append(details);
        }
        const datasets = chart.data.datasets;
        details.lastElementChild.innerHTML = '<table><caption class="sr-only">' + escape(title) + '</caption><thead><tr><th scope="col">Period / category</th>' + datasets.map(dataset => '<th scope="col">' + escape(dataset.label || 'Requests') + '</th>').join('') + '</tr></thead><tbody>' + chart.data.labels.map((label, index) => '<tr><th scope="row">' + escape(label) + '</th>' + datasets.map(dataset => '<td>' + escape(number(dataset.data[index])) + '</td>').join('') + '</tr>').join('') + '</tbody></table>';
      }
    });
  }

  function initialize() {
    const main = document.querySelector('.dashboard-main');
    const runtime = document.getElementById('aiRuntimePanel');
    main.insertBefore(document.getElementById('dashboardTopStats'), runtime);
    const insight = document.createElement('section');
    insight.id = 'policyInsight';
    insight.setAttribute('aria-label', 'Policy intelligence');
    main.insertBefore(insight, runtime);
    const toolbar = document.createElement('div');
    toolbar.className = 'dashboard-toolbar';
    toolbar.append(document.getElementById('dashboardGlobalFilters'), document.getElementById('roleQuickActions'));
    main.insertBefore(toolbar, runtime);
    const grid = document.querySelector('.dashboard-grid');
    ['.panel-map', '.panel-charts', '.panel-projects', '.panel-rbac-reports', '.panel-prediction', '.panel-brief', '.panel-feed', '.panel-ops'].forEach(selector => {
      const panel = grid.querySelector(selector);
      if (panel) grid.append(panel);
    });
    const theme = readPreference('nvb_theme', window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    applyTheme(theme === 'dark' ? 'dark' : 'light');
    document.getElementById('themeSwitch').addEventListener('click', () => {
      const next = document.body.dataset.theme === 'dark' ? 'light' : 'dark';
      applyTheme(next);
      savePreference('nvb_theme', next);
    });
    document.body.dataset.rail = readPreference('nvb_rail', innerWidth <= 1024 ? 'collapsed' : 'expanded');
    const toggle = document.getElementById('railToggle');
    const syncRail = () => toggle.setAttribute('aria-expanded', String(innerWidth <= 768 ? document.body.dataset.menuOpen === 'true' : document.body.dataset.rail !== 'collapsed'));
    syncRail();
    toggle.addEventListener('click', () => {
      if (innerWidth <= 768) document.body.dataset.menuOpen = document.body.dataset.menuOpen === 'true' ? 'false' : 'true';
      else { document.body.dataset.rail = document.body.dataset.rail === 'collapsed' ? 'expanded' : 'collapsed'; savePreference('nvb_rail', document.body.dataset.rail); }
      syncRail();
    });
    window.addEventListener('resize', syncRail);
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape') return;
      const open = document.querySelector('.console-popover[open]');
      if (open) { open.open = false; open.querySelector('summary').focus(); }
      if (document.body.dataset.menuOpen === 'true') { document.body.dataset.menuOpen = 'false'; toggle.focus(); syncRail(); }
    });
    document.addEventListener('click', event => {
      document.querySelectorAll('.console-popover[open]').forEach(popover => { if (!popover.contains(event.target)) popover.open = false; });
      if (!event.target.closest('.console-sidebar, #railToggle')) { document.body.dataset.menuOpen = 'false'; syncRail(); }
    });
    const links = [...document.querySelectorAll('.console-navigation a')];
    function navigate(link) {
      const target = document.querySelector(link.getAttribute('href'));
      if (!target || !target.getClientRects().length) return;
      target.setAttribute('tabindex', '-1');
      target.scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' });
      target.focus({ preventScroll: true });
      links.forEach(item => { item.classList.toggle('is-active', item === link); item.removeAttribute('aria-current'); });
      link.setAttribute('aria-current', 'location');
      document.body.dataset.menuOpen = 'false';
      syncRail();
    }
    links.forEach(link => {
      link.setAttribute('aria-label', link.textContent.trim());
      link.title = link.textContent.trim();
      link.addEventListener('click', event => { event.preventDefault(); navigate(link); });
    });
    document.getElementById('sectionSearchForm').addEventListener('submit', event => {
      event.preventDefault();
      const query = document.getElementById('sectionSearch').value.toLowerCase().trim();
      const link = links.find(item => !item.hidden && item.textContent.toLowerCase().includes(query));
      if (query && link) navigate(link);
    });
    window.NVBConsole.subscribe(state => {
      links.forEach(link => { const target = document.querySelector(link.getAttribute('href')); link.hidden = !!target && target.style.display === 'none'; });
      document.getElementById('dashboardSections').innerHTML = links.filter(link => !link.hidden).map(link => '<option value="' + escape(link.textContent.trim()) + '"></option>').join('');
      updateKpis(state);
      if (!insight.dataset.reactMounted) {
        const copy = insightCopy(state);
        insight.innerHTML = '<div class="insight-card"><i class="bi bi-stars insight-symbol" aria-hidden="true"></i><div class="insight-body"><h2>' + escape(copy.title) + '</h2><p>' + escape(copy.body) + '</p><span class="insight-source">' + escape(copy.source) + '</span></div></div>';
      }
    });
    const updateNotification = () => { document.getElementById('runtimeNotification').textContent = document.getElementById('aiRuntimeOverallBadge').textContent + '. ' + document.getElementById('aiRuntimeCheckedAt').textContent; };
    new MutationObserver(updateNotification).observe(runtime, { childList: true, subtree: true, characterData: true });
    updateNotification();
    enhanceCharts();
    document.getElementById('projectsList').addEventListener('click', async event => {
      const toggle = event.target.closest('[data-project-toggle]');
      if (toggle) {
        portfolioExpanded = !portfolioExpanded;
        document.querySelectorAll('[data-project-overflow]').forEach(card => { card.hidden = !portfolioExpanded; });
        toggle.setAttribute('aria-expanded', String(portfolioExpanded));
        toggle.textContent = portfolioExpanded ? 'Show top 6 projects' : 'View all ' + toggle.dataset.projectToggle + ' projects';
        return;
      }
      const button = event.target.closest('[data-focus-district]');
      if (!button || button.disabled) return;
      button.disabled = true;
      try { await window.focusPriorityDistrict(button.dataset.projectState, button.dataset.focusDistrict); }
      finally { button.disabled = false; }
    });
  }

  document.addEventListener('DOMContentLoaded', initialize);
})();
