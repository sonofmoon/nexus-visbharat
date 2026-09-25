/**
 * VisBharat Dynamic Civic Demand Screening & Stress Matrix
 * Multi-lens empirical indicator engine for real-time district screening.
 */
(function(window) {
  'use strict';

  function esc(s) {
    if (s == null) return '';
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  function num(n, fallback = '—') {
    if (n == null || isNaN(n)) return fallback;
    return Number(n).toLocaleString('en-IN', { maximumFractionDigits: 2 });
  }

  const NVBDemandScreening = {
    data: [],
    stats: null,
    metadata: null,
    activeLens: 'stress', // 'stress' | 'access_gap' | 'infra_gap' | 'emergency' | 'all'
    searchQuery: '',
    sortBy: 'stress_desc', // 'stress_desc' | 'volume_desc' | 'density_desc' | 'deprivation_desc' | 'alpha'
    showAll: false,
    initialized: false,

    init() {
      if (this.initialized) return;
      this.initialized = true;
      this.bindEvents();
    },

    bindEvents() {
      const container = document.getElementById('panelDemandScreening') || document.querySelector('.panel-prediction');
      if (!container) return;

      // Event delegation for lens switcher buttons
      container.addEventListener('click', (e) => {
        const lensBtn = e.target.closest('[data-screening-lens]');
        if (lensBtn) {
          e.preventDefault();
          this.activeLens = lensBtn.dataset.screeningLens;
          container.querySelectorAll('[data-screening-lens]').forEach(b => b.classList.remove('active'));
          lensBtn.classList.add('active');
          this.renderList();
          return;
        }

        const expandBtn = e.target.closest('#screeningExpandBtn');
        if (expandBtn) {
          e.preventDefault();
          this.showAll = !this.showAll;
          this.renderList();
          return;
        }

        const focusBtn = e.target.closest('.screening-focus-btn, .screening-card');
        if (focusBtn && !e.target.closest('a, input, select')) {
          const card = focusBtn.closest('.screening-card');
          if (card) {
            const state = card.dataset.state;
            const district = card.dataset.district;
            const lat = parseFloat(card.dataset.lat);
            const lng = parseFloat(card.dataset.lng);
            this.focusDistrict(state, district, lat, lng);
          }
        }
      });

      // Search input handler
      container.addEventListener('input', (e) => {
        if (e.target.id === 'screeningSearch') {
          this.searchQuery = e.target.value.trim().toLowerCase();
          this.renderList();
        }
      });

      // Sort dropdown handler
      container.addEventListener('change', (e) => {
        if (e.target.id === 'screeningSort') {
          this.sortBy = e.target.value;
          this.renderList();
        }
      });
    },

    focusDistrict(state, district, lat, lng) {
      // 1. Highlight map if Leaflet is active
      if (window.map && Number.isFinite(lat) && Number.isFinite(lng) && lat !== 0 && lng !== 0) {
        try {
          window.map.flyTo([lat, lng], 10, { duration: 1.2 });
        } catch (_) {
          window.map.setView([lat, lng], 10);
        }
      }

      // 2. Cross-filter Live Complaint Feed if district elements exist
      const feedItems = document.querySelectorAll('#complaintFeed .feed-item, #complaintFeed tr');
      if (feedItems.length > 0) {
        feedItems.forEach(item => {
          const text = item.textContent || '';
          if (text.toLowerCase().includes(district.toLowerCase())) {
            item.style.display = '';
            item.style.backgroundColor = 'rgba(26, 115, 232, 0.08)';
          } else {
            item.style.backgroundColor = '';
          }
        });
      }

      // 3. Mark selected card visually
      document.querySelectorAll('.screening-card').forEach(c => {
        if (c.dataset.district === district) {
          c.classList.add('screening-card-active');
        } else {
          c.classList.remove('screening-card-active');
        }
      });

      // 4. Update status pill
      const feedback = document.getElementById('screeningFeedback');
      if (feedback) {
        feedback.textContent = `Focused on ${district}, ${state}`;
        feedback.style.display = 'inline-block';
        clearTimeout(this._feedbackTimer);
        this._feedbackTimer = setTimeout(() => { feedback.style.display = 'none'; }, 4000);
      }
    },

    render(inclusionData, statsData) {
      this.init();
      if (!inclusionData || !Array.isArray(inclusionData.items)) {
        return;
      }
      this.data = inclusionData.items.map(item => ({
        ...item,
        requests: Number(item.requests || 0),
        reports_per_100k: item.reports_per_100k != null ? Number(item.reports_per_100k) : null,
        deprivation_index: item.deprivation_index != null ? Number(item.deprivation_index) : 0,
        composite_stress_score: item.composite_stress_score != null ? Number(item.composite_stress_score) : this.computeStress(item),
        emergency_count: Number(item.emergency_count || 0),
        emergency_pct: Number(item.emergency_pct || 0),
        infrastructure_gap_pct: item.infrastructure_gap_pct != null ? Number(item.infrastructure_gap_pct) : null
      }));
      this.stats = statsData || null;
      this.metadata = inclusionData.metadata || null;
      this.accessAlertCount = Number(inclusionData.access_investigation_count || 0);

      this.renderContainer();
      this.renderList();
    },

    loadFromHotspots(payload) {
      if (!payload) return;
      const rawList = Array.isArray(payload) ? payload : (payload.hotspots || payload.items || []);
      const items = rawList.map(h => {
        const count = Number(h.complaint_count || 0);
        const emergencies = Number(h.emergency_count || 0);
        const emergPct = count > 0 ? (emergencies / count) * 100 : 0;
        const density = h.hotspot_score != null ? Number(h.hotspot_score) : null;
        const dep = h.deprivation_index != null ? Number(h.deprivation_index) : 0;
        const isAccessGap = Boolean(dep >= 0.5 && (density == null || density < 10));
        return {
          state: h.state,
          district: h.district,
          requests: count,
          reports_per_100k: density,
          deprivation_index: dep,
          emergency_count: emergencies,
          emergency_pct: emergPct,
          infrastructure_gap_pct: h.alignment_gap != null ? Number(h.alignment_gap) : null,
          investigate_access: isAccessGap,
          lat: Number(h.lat || 0),
          lng: Number(h.lng || 0)
        };
      });
      const alertCount = items.filter(i => i.investigate_access).length;
      this.render({ items, access_investigation_count: alertCount }, null);
    },

    computeStress(item) {
      const density = item.reports_per_100k || 0;
      const densityVal = Math.min(100, density * 2.0);
      const depVal = Math.min(100, (Number(item.deprivation_index) || 0) * 100);
      const gapVal = item.infrastructure_gap_pct != null ? Number(item.infrastructure_gap_pct) : 50;
      const emergVal = Math.min(100, (Number(item.emergency_pct) || 0) * 2.0);
      return Math.round((0.35 * densityVal + 0.35 * depVal + 0.15 * gapVal + 0.15 * emergVal) * 10) / 10;
    },

    renderContainer() {
      const container = document.getElementById('predictionContent');
      if (!container) return;

      // Only build the controls shell if it doesn't already exist
      if (!document.getElementById('screeningControlsShell')) {
        container.innerHTML = `
          <div id="screeningControlsShell" class="screening-controls-shell mb-3">
            <div class="screening-lens-bar d-flex flex-wrap gap-2 mb-2" role="tablist" aria-label="Screening indicators lens">
              <button class="screening-lens-btn active" data-screening-lens="stress" title="Rank by composite stress combining volume, deprivation, and emergency ratio">
                <i class="bi bi-lightning-charge-fill text-warning"></i> Civic Stress
              </button>
              <button class="screening-lens-btn" data-screening-lens="access_gap" title="Districts with high deprivation but suppressed citizen reports">
                <i class="bi bi-shield-exclamation text-danger"></i> Silent / Access Gaps
              </button>
              <button class="screening-lens-btn" data-screening-lens="infra_gap" title="Districts with high municipal infrastructure coverage deficit">
                <i class="bi bi-tools text-primary"></i> Infra Deficit
              </button>
              <button class="screening-lens-btn" data-screening-lens="emergency" title="Districts with highest proportion of emergency requests">
                <i class="bi bi-fire text-danger"></i> Emergency Surge
              </button>
              <button class="screening-lens-btn" data-screening-lens="all" title="View all covered districts">
                <i class="bi bi-globe"></i> All Covered
              </button>
            </div>

            <div class="screening-toolbar d-flex flex-wrap align-items-center justify-content-between gap-2 mb-3">
              <div class="screening-search-box d-flex align-items-center">
                <i class="bi bi-search text-muted me-2"></i>
                <input type="text" id="screeningSearch" class="screening-search-input" placeholder="Quick search district..." aria-label="Quick search district">
              </div>
              <div class="d-flex align-items-center gap-2">
                <label for="screeningSort" class="screening-sort-label text-muted small">Sort:</label>
                <select id="screeningSort" class="screening-sort-select form-select form-select-sm" aria-label="Sort districts">
                  <option value="stress_desc">Stress Index (High &rarr; Low)</option>
                  <option value="volume_desc">Report Volume (High &rarr; Low)</option>
                  <option value="density_desc">Density (/100k pop)</option>
                  <option value="deprivation_desc">Deprivation index</option>
                  <option value="emergency_desc">Emergency Ratio</option>
                  <option value="alpha">District (A &rarr; Z)</option>
                </select>
              </div>
            </div>

            <div class="d-flex align-items-center justify-content-between mb-2 px-1">
              <span id="screeningStatsBadge" class="screening-summary-text text-muted small">
                <!-- Count filled dynamically -->
              </span>
              <span id="screeningFeedback" class="badge bg-primary-subtle text-primary small px-2 py-1" style="display:none;"></span>
            </div>
          </div>

          <div id="screeningCardsContainer" class="screening-cards-container d-flex flex-column gap-2" role="region" aria-live="polite">
            <!-- Cards rendered dynamically -->
          </div>

          <div class="screening-footer d-flex flex-wrap align-items-center justify-content-between mt-3 pt-2 border-top">
            <button id="screeningExpandBtn" class="btn btn-sm btn-outline-secondary screening-toggle-btn">
              <i class="bi bi-chevron-down"></i> Show All Districts
            </button>
            <span class="screening-governance-badge text-muted small">
              <i class="bi bi-check-circle-fill text-success me-1"></i> Strictly Observed Data (SECC 2011 + Live Intake)
            </span>
          </div>
        `;
      }
    },

    getFilteredData() {
      let list = [...this.data];

      // 1. Lens filter
      if (this.activeLens === 'stress') {
        list = list.filter(r => r.requests > 0 || (r.deprivation_index || 0) > 0.4);
      } else if (this.activeLens === 'access_gap') {
        list = list.filter(r => r.investigate_access);
      } else if (this.activeLens === 'infra_gap') {
        list = list.filter(r => r.infrastructure_gap_pct != null && r.infrastructure_gap_pct > 25);
      } else if (this.activeLens === 'emergency') {
        list = list.filter(r => (r.emergency_count || 0) > 0 || (r.emergency_pct || 0) > 0);
      }

      // 2. Search filter
      if (this.searchQuery) {
        list = list.filter(r => 
          (r.district && r.district.toLowerCase().includes(this.searchQuery)) ||
          (r.state && r.state.toLowerCase().includes(this.searchQuery))
        );
      }

      // 3. Sorting
      list.sort((a, b) => {
        if (this.sortBy === 'stress_desc') {
          return (b.composite_stress_score || 0) - (a.composite_stress_score || 0);
        } else if (this.sortBy === 'volume_desc') {
          return (b.requests || 0) - (a.requests || 0);
        } else if (this.sortBy === 'density_desc') {
          return (b.reports_per_100k || 0) - (a.reports_per_100k || 0);
        } else if (this.sortBy === 'deprivation_desc') {
          return (b.deprivation_index || 0) - (a.deprivation_index || 0);
        } else if (this.sortBy === 'emergency_desc') {
          return (b.emergency_pct || 0) - (a.emergency_pct || 0);
        } else if (this.sortBy === 'alpha') {
          return (a.district || '').localeCompare(b.district || '');
        }
        return 0;
      });

      return list;
    },

    renderList() {
      const container = document.getElementById('screeningCardsContainer');
      const statsBadge = document.getElementById('screeningStatsBadge');
      const expandBtn = document.getElementById('screeningExpandBtn');
      if (!container) return;

      const filtered = this.getFilteredData();
      const totalCount = filtered.length;
      const displayList = this.showAll ? filtered : filtered.slice(0, 6);

      // Update summary badge
      if (statsBadge) {
        const alertNote = this.accessAlertCount > 0 ? ` · <span class="text-danger fw-semibold">${this.accessAlertCount} Access Alert${this.accessAlertCount > 1 ? 's' : ''}</span>` : '';
        statsBadge.innerHTML = `Showing <strong>${displayList.length}</strong> of <strong>${totalCount}</strong> districts screened${alertNote}`;
      }

      // Update expand button text
      if (expandBtn) {
        if (totalCount <= 6) {
          expandBtn.style.display = 'none';
        } else {
          expandBtn.style.display = 'inline-block';
          expandBtn.innerHTML = this.showAll 
            ? `<i class="bi bi-chevron-up me-1"></i> Show Top 6 Only` 
            : `<i class="bi bi-chevron-down me-1"></i> View All (${totalCount}) Districts`;
        }
      }

      if (displayList.length === 0) {
        container.innerHTML = `
          <div class="screening-empty-state text-center py-4 text-muted">
            <i class="bi bi-funnel text-muted" style="font-size: 1.8rem;"></i>
            <p class="mt-2 mb-0">No districts match the selected lens and search criteria.</p>
            <small>Try selecting "All Covered" or clearing the search filter.</small>
          </div>
        `;
        return;
      }

      container.innerHTML = displayList.map(r => this.renderCard(r)).join('');
    },

    renderCard(r) {
      const stress = Math.min(100, Math.max(0, r.composite_stress_score || 0));
      let stressLevel = 'Stable';
      let stressColor = '#34a853'; // green
      let stressBg = '#e6f4ea';

      if (stress >= 65) {
        stressLevel = 'Elevated Stress';
        stressColor = '#ea4335'; // crimson
        stressBg = '#fce8e6';
      } else if (stress >= 40) {
        stressLevel = 'Moderate Demand';
        stressColor = '#b06000'; // amber
        stressBg = '#fef7e0';
      }

      const hasAlert = Boolean(r.investigate_access);
      const densityStr = r.reports_per_100k != null ? num(r.reports_per_100k) : '—';
      const mpiStr = r.deprivation_index != null ? Number(r.deprivation_index).toFixed(2) : '—';
      const gapStr = r.infrastructure_gap_pct != null ? `${num(r.infrastructure_gap_pct)}% gap` : '—';
      const emergStr = (r.emergency_count || 0) > 0 ? `${r.emergency_count} (${num(r.emergency_pct)}%)` : '0';

      return `
        <div class="google-material-card screening-card ${hasAlert ? 'screening-card-alert' : ''}" 
             data-district="${esc(r.district)}" 
             data-state="${esc(r.state)}"
             data-lat="${r.lat || 0}"
             data-lng="${r.lng || 0}">
          
          <div class="screening-card-header d-flex justify-content-between align-items-center mb-2">
            <div class="d-flex align-items-center gap-2">
              <i class="bi bi-geo-alt-fill ${stress >= 65 ? 'text-danger' : (stress >= 40 ? 'text-warning' : 'text-primary')}" style="font-size:1.05rem;"></i>
              <div>
                <strong class="screening-district-title">${esc(r.district)}</strong>
                <span class="text-muted small ms-1">${esc(r.state)}</span>
              </div>
            </div>
            <div class="d-flex align-items-center gap-2">
              <span class="screening-stress-badge badge" style="background:${stressBg};color:${stressColor};font-weight:700;">
                ${stressLevel} (${stress}%)
              </span>
              <button class="btn btn-sm btn-outline-primary screening-focus-btn py-0 px-2" style="font-size:0.75rem;" title="Highlight on Map and Live Feed">
                <i class="bi bi-crosshair"></i> Focus
              </button>
            </div>
          </div>

          <!-- Dynamic Stress Gauge Meter -->
          <div class="screening-meter-wrapper mb-2" title="Composite Civic Stress: ${stress}/100">
            <div class="screening-meter-track">
              <div class="screening-meter-fill" style="width:${stress}%;background:${stressColor};"></div>
            </div>
          </div>

          <!-- Multidimensional Observed Indicators Grid -->
          <div class="screening-metrics-grid row g-1">
            <div class="col-3">
              <div class="screening-metric-pill">
                <span class="screening-metric-label">REPORTS</span>
                <strong class="screening-metric-val">${num(r.requests)}</strong>
              </div>
            </div>
            <div class="col-3">
              <div class="screening-metric-pill">
                <span class="screening-metric-label">DENSITY /100k</span>
                <strong class="screening-metric-val">${densityStr}</strong>
              </div>
            </div>
            <div class="col-3">
              <div class="screening-metric-pill">
                <span class="screening-metric-label">DEPRIVATION INDEX</span>
                <strong class="screening-metric-val">${mpiStr}</strong>
              </div>
            </div>
            <div class="col-3">
              <div class="screening-metric-pill">
                <span class="screening-metric-label">EMERGENCY</span>
                <strong class="screening-metric-val ${r.emergency_count > 0 ? 'text-danger fw-bold' : ''}">${emergStr}</strong>
              </div>
            </div>
          </div>

          <!-- Access Disparity Alert Callout -->
          ${hasAlert ? `
            <div class="screening-access-callout d-flex align-items-center gap-2 mt-2 p-2">
              <i class="bi bi-shield-exclamation text-danger flex-shrink-0" style="font-size:1.1rem;"></i>
              <div class="small">
                <strong class="text-danger">Access Disparity Alert:</strong>
                High deprivation index (${mpiStr}) with low report volume suggests a possible citizen voice barrier. <em>Ground survey recommended.</em>
              </div>
            </div>
          ` : ''}

        </div>
      `;
    }
  };

  window.NVBDemandScreening = NVBDemandScreening;

})(window);
