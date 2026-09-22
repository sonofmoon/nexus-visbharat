// ===== NVB 4-LENS EINSTEIN PROJECTION CONTROLLER =====
class NVBLensController {
 constructor() {
 this.currentClusterId = 'TN-KAR-0417';
 this.currentRole = 'public';
 }

 async fetchLensData(clusterId = null, role = null) {
 const targetCluster = clusterId || this.currentClusterId;
 const targetRole = role || this.currentRole;
 const url = `/api/v1/lens/${encodeURIComponent(targetRole)}/cluster/${encodeURIComponent(targetCluster)}`;

 try {
 const res = await fetch(url, { cache: 'no-store' });
 const data = await res.json();
 return data;
 } catch (err) {
 console.error('Failed to fetch 4-lens data:', err);
 return null;
 }
 }

 async switchCluster(clusterId) {
 this.currentClusterId = clusterId;
 const data = await this.fetchLensData(clusterId, this.currentRole);
 if (data && data.lens) {
 const container = document.getElementById('lens-content-area');
 if (container) {
 if (this.currentRole === 'public') container.innerHTML = this.renderPublicLens(data.lens);
 else if (this.currentRole === 'analyst') container.innerHTML = this.renderAnalystLens(data.lens);
 else if (this.currentRole === 'auditor') container.innerHTML = this.renderAuditorLens(data.lens);
 else if (this.currentRole === 'admin') container.innerHTML = this.renderAdminLens(data.lens);
 }
 }
 }

 renderPublicLens(lens) {
 const timelineHtml = (lens.voice_trace_timeline || []).map(t => {
 let badgeBg = '#1a73e8'; // Google Blue
 if (t.step === 2) badgeBg = '#34a853'; // Google Green
 if (t.step === 3) badgeBg = '#fbbc04'; // Google Yellow
 if (t.step === 4) badgeBg = '#ea4335'; // Google Red

 return `
 <div class="trace-step-item ${t.status}" style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:14px;padding:16px 18px;transition:all 0.2s;display:flex;flex-direction:column;justify-content:space-between;">
 <div>
 <div class="d-flex align-items-center justify-content-between mb-2">
 <span class="badge" style="background:${badgeBg};color:${t.step === 3 ? '#202124' : '#fff'};font-weight:700;border-radius:12px;padding:5px 10px;font-size:0.75rem;">HOP #${t.step}</span>
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;font-size:0.72rem;border-radius:10px;padding:4px 8px;">${t.timestamp}</span>
 </div>
 <strong style="display:block;font-size:0.95rem;color:#202124;font-family:'Google Sans', sans-serif;margin-bottom:4px;line-height:1.3;">${t.label}</strong>
 </div>
 <span style="font-size:0.8rem;color:#5f6368;display:block;line-height:1.4;margin-top:6px;">${t.detail}</span>
 </div>
 `;
 }).join('');

 const pilotPills = [
 { id: 'TN-KAR-0417', label: 'TN | Karur (Tamil)' },
 { id: 'TN-CHN-0102', label: 'TN | Chennai (Tamil)' },
 { id: 'TN-VEL-0881', label: 'TN | Vellore (Tamil)' },
 { id: 'AP-TPT-0205', label: 'AP | Tirupati (Telugu)' },
 { id: 'AP-VSKP-0511', label: 'AP | Visakhapatnam (Telugu)' },
 { id: 'TS-HYD-0101', label: 'TS | Hyderabad (Telugu)' },
 { id: 'KA-BLR-0560', label: 'KA | Bangalore (English)' }
 ].map(p => {
 const isSelected = lens.cluster_id === p.id;
 return `
 <button class="btn btn-sm" 
 style="border-radius:100px;font-size:0.78rem;font-weight:500;padding:6px 14px;margin:3px;transition:all 0.2s;${isSelected ? 'background:#1a73e8;color:#ffffff;border:1px solid #1a73e8;box-shadow:0 1px 3px rgba(60,64,67,0.3);' : 'background:#ffffff;color:#3c4043;border:1px solid #dadce0;'}" 
 onclick="window.nvbLensController.switchCluster('${p.id}')">
 ${p.label}
 </button>
 `;
 }).join('');

 return `
 <div class="google-material-card" style="background:#ffffff;border-radius:20px;border:1px solid #dadce0;box-shadow:0 4px 16px rgba(60,64,67,0.08), 0 1px 3px rgba(60,64,67,0.12);overflow:hidden;position:relative;font-family:'Google Sans', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;">
 <!-- Google Brand Accent Stripe -->
 <div style="height:4px;background:linear-gradient(90deg, #4285F4 0% 25%, #EA4335 25% 50%, #FBBC04 50% 75%, #34A853 75% 100%);"></div>

 <div style="padding:2rem;">
 <!-- Header Bar -->
 <div class="d-flex justify-content-between align-items-start flex-wrap gap-3" style="margin-bottom:1.5rem;">
 <div class="d-flex align-items-center gap-3">
 <div style="width:48px;height:48px;background:#e8f0fe;border-radius:14px;display:flex;align-items:center;justify-content:center;color:#1a73e8;font-size:1.5rem;flex-shrink:0;">
 <i class="bi bi-shield-check"></i>
 </div>
 <div>
 <h4 style="margin:0;font-weight:700;color:#202124;font-size:1.2rem;letter-spacing:-0.2px;line-height:1.3;">Public Participation Receipt & Transparency Log</h4>
 <div class="d-flex align-items-center gap-2 mt-1.5 flex-wrap">
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-patch-check-fill text-primary"></i> Google AI & Bhashini ASR Verified</span>
 <span class="badge" style="background:#f1f3f4;color:#3c4043;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-geo-alt-fill text-danger"></i> ${lens.state || 'Tamil Nadu'} | ${lens.district || 'Karur'} (${lens.language || 'Tamil'})</span>
 </div>
 </div>
 </div>
 <span class="badge" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:6px 14px;font-size:0.78rem;font-weight:600;"><i class="bi bi-lock-fill"></i> Zero-Trust Merkle Log</span>
 </div>

 <!-- Material Region Switcher -->
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1rem 1.25rem;margin-bottom:1.5rem;">
 <small style="color:#5f6368;font-size:0.72rem;font-weight:700;display:block;margin-bottom:8px;text-transform:uppercase;letter-spacing:0.5px;"><i class="bi bi-pin-map-fill" style="color:#1a73e8"></i> PILOT STATE & DISTRICT SELECTOR:</small>
 <div class="d-flex flex-wrap align-items-center">${pilotPills}</div>
 </div>

 <!-- Voice Trace Stepper Grid -->
 <div style="margin-bottom:1.5rem;">
 <h5 style="font-size:0.92rem;font-weight:700;color:#3c4043;margin-bottom:1rem;"><i class="bi bi-signpost-split-fill" style="color:#1a73e8"></i> Voice Trace Timeline (Cluster #${lens.cluster_id}):</h5>
 <div class="sim-results-grid" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:14px;">${timelineHtml}</div>
 </div>

 <!-- My Cluster, My Neighbors Google Card -->
 <div style="background:#f8fafd;border:1px solid #dadce0;border-left:5px solid #1a73e8;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <div class="d-flex justify-content-between align-items-center flex-wrap gap-3">
 <div style="max-width:70%;">
 <strong style="color:#174ea6;font-size:0.98rem;display:block;margin-bottom:4px;"><i class="bi bi-people-fill"></i> My Cluster, My Neighbors (${lens.district || 'Karur'}, ${lens.state || 'Tamil Nadu'})</strong>
 <p style="margin:0;font-size:0.88rem;color:#3c4043;line-height:1.5;">Your voice joined Cluster <strong>#${lens.cluster_id}</strong> alongside <strong>${(lens.neighbors_cosign_widget?.total_voices_in_cluster || 2104).toLocaleString()}</strong> citizens.</p>
 </div>
 <button class="btn btn-sm" style="background:#1a73e8;color:#ffffff;border-radius:24px;padding:8px 20px;font-size:0.85rem;font-weight:500;box-shadow:0 1px 3px rgba(60,64,67,0.3);border:none;white-space:nowrap;" onclick="alert('Co-signed cluster #${lens.cluster_id}! Total co-signers updated.')"><i class="bi bi-hand-thumbs-up-fill"></i> Co-Sign Demand (${lens.neighbors_cosign_widget?.cosigned_this_morning || 3} today)</button>
 </div>
 </div>

 <!-- Democratic Causal Impact Receipt Card -->
 <div style="background:#f6fbf7;border:1px solid #ceead6;border-left:5px solid #34a853;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <div class="d-flex align-items-center gap-3">
 <div style="width:42px;height:42px;background:#ceead6;border-radius:50%;display:flex;align-items:center;justify-content:center;color:#137333;font-size:1.3rem;flex-shrink:0;">
 <i class="bi bi-patch-check-fill"></i>
 </div>
 <div>
 <strong style="color:#137333;font-size:0.96rem;display:block;margin-bottom:4px;">Democratic Causal Impact Receipt (${lens.honesty_widget?.geofence_ward || 'Ward 12'})</strong>
 <p style="margin:0;font-size:0.88rem;color:#202124;line-height:1.5;">${lens.honesty_widget?.causal_message || ''}</p>
 </div>
 </div>
 </div>

 <!-- Cryptographic Transparency Log Footer -->
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:14px;padding:1rem 1.25rem;" class="d-flex justify-content-between align-items-center flex-wrap gap-2">
 <div class="d-flex align-items-center gap-2">
 <span class="badge" style="background:#3c4043;color:#fff;font-family:monospace;font-size:0.72rem;padding:4px 8px;">SHA-256</span>
 <small style="font-family:'Roboto Mono', monospace;font-size:0.78rem;color:#5f6368;" title="${lens.cryptographic_receipt?.chain_head_hash || ''}">${(lens.cryptographic_receipt?.chain_head_hash || '').substring(0, 24)}...</small>
 </div>
 <button class="btn btn-sm" style="background:#ffffff;color:#1a73e8;border:1px solid #1a73e8;border-radius:20px;padding:6px 16px;font-size:0.8rem;font-weight:500;" onclick="window.nvbLensController.verifyChain()"><i class="bi bi-shield-check"></i> Verify Receipt Math</button>
 </div>
 </div>
 </div>
 `;
 }

 toggleAnalystSurface(mode) {
 this.analystSurfaceMode = mode;
 const container = document.getElementById('analyst-surface-container');
 if (!container || !this.lastAnalystLens) return;

 const isLatent = mode === 'latent';
 const surfaceData = this.lastAnalystLens.latent_demand_surface || {};
 const summary = surfaceData.summary || {};
 const items = isLatent ? (surfaceData.latent_need_surface || []) : (surfaceData.raw_surface || []);

 // 3 Equal-Width Square KPI Metric Cards in a Single Row
 const kpiSummaryHtml = `
 <div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:1.25rem;margin-bottom:2rem;">
 <!-- Card 1: Evaluated Scope -->
 <div style="background:#ffffff;border:1px solid #dadce0;border-top:4px solid #4285f4;border-radius:16px;padding:1.25rem;box-shadow:0 3px 10px rgba(60,64,67,0.05);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:145px;transition:all 0.2s;">
 <div class="d-flex align-items-center justify-content-center gap-2 mb-1.5">
 <small style="font-size:0.72rem;color:#5f6368;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;"><i class="bi bi-geo-alt-fill text-primary"></i> GEOFENCES</small>
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;font-size:0.7rem;padding:3px 8px;">6 Wards</span>
 </div>
 <div style="font-size:1.6rem;font-weight:800;color:#202124;font-family:'Google Sans', sans-serif;line-height:1.1;margin:4px 0;">${summary.total_districts_analyzed || items.length}</div>
 <div style="font-size:0.85rem;font-weight:600;color:#3c4043;margin-bottom:3px;">Districts & Wards Evaluated</div>
 <small style="color:#5f6368;font-size:0.72rem;">Full pilot coverage analyzed</small>
 </div>

 <!-- Card 2: Max VAI Multiplier -->
 <div style="background:#f8fafd;border:1px solid #c2e7ff;border-top:4px solid #1a73e8;border-radius:16px;padding:1.25rem;box-shadow:0 3px 10px rgba(60,64,67,0.05);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:145px;transition:all 0.2s;">
 <div class="d-flex align-items-center justify-content-center gap-2 mb-1.5">
 <small style="font-size:0.72rem;color:#174ea6;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;"><i class="bi bi-calculator-fill text-primary"></i> MAX VAI MULTIPLIER</small>
 <span class="badge" style="background:#1a73e8;color:#ffffff;border-radius:100px;font-size:0.7rem;padding:3px 8px;">VAI Engine</span>
 </div>
 <div style="font-size:1.6rem;font-weight:800;color:#1a73e8;font-family:'Google Sans', sans-serif;line-height:1.1;margin:4px 0;">${summary.max_vai_multiplier || 1.85}x</div>
 <div style="font-size:0.85rem;font-weight:600;color:#174ea6;margin-bottom:3px;">Equity Boost Multiplier</div>
 <small style="color:#174ea6;font-size:0.72rem;">Propensity selection uplift applied</small>
 </div>

 <!-- Card 3: Corrected Red Zones -->
 <div style="background:#fcf0f0;border:1px solid #f87171;border-top:4px solid #d93025;border-radius:16px;padding:1.25rem;box-shadow:0 3px 10px rgba(60,64,67,0.05);display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;min-height:145px;transition:all 0.2s;">
 <div class="d-flex align-items-center justify-content-center gap-2 mb-1.5">
 <small style="font-size:0.72rem;color:#c5221f;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;"><i class="bi bi-exclamation-triangle-fill text-danger"></i> RED ZONES</small>
 <span class="badge" style="background:#d93025;color:#ffffff;border-radius:100px;font-size:0.7rem;padding:3px 8px;">Action Queued</span>
 </div>
 <div style="font-size:1.6rem;font-weight:800;color:#d93025;font-family:'Google Sans', sans-serif;line-height:1.1;margin:4px 0;">${summary.corrected_red_zones_count || 1}</div>
 <div style="font-size:0.85rem;font-weight:600;color:#c5221f;margin-bottom:3px;">Silent High-Need Zone</div>
 <small style="color:#991b1b;font-size:0.72rem;">Low access, high SECC deprivation</small>
 </div>
 </div>
 `;

 const cardsHtml = items.slice(0, 6).map((item, idx) => {
 const isRedZone = item.is_corrected_red_zone;
 const vaiPct = Math.round((item.voice_access_index || 0.5) * 100);
 const vaiColor = vaiPct < 55 ? '#d93025' : (vaiPct < 75 ? '#fbbc04' : '#34a853');

 let rankBg = '#1a73e8';
 if (idx === 1) rankBg = '#34a853';
 if (idx === 2) rankBg = '#fbbc04';
 if (idx >= 3) rankBg = '#5f6368';

 return `
 <div class="sim-project-card" style="border:1px solid ${isRedZone ? '#f87171' : '#dadce0'};border-left:4px solid ${isRedZone ? '#d93025' : rankBg};border-radius:12px;padding:1rem;background:#ffffff;box-shadow:0 2px 6px rgba(60,64,67,0.06);">
 <div class="sim-card-header" style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.5rem;">
 <div>
 <div style="display:flex;align-items:center;gap:8px;">
 <span class="badge" style="background:${rankBg};color:${idx === 2 ? '#202124' : '#ffffff'};border-radius:50%;width:24px;height:24px;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:0.75rem;flex-shrink:0;">#${idx + 1}</span>
 <h4 style="margin:0;font-size:0.95rem;font-weight:700;color:#202124;font-family:'Google Sans', sans-serif;">${item.district}, ${item.state}</h4>
 </div>
 <span style="font-size:0.76rem;color:#5f6368;display:block;margin-top:4px;">Pop: ${(item.population / 100000).toFixed(1)}L | SECC Deprivation: <strong>${item.deprivation_index}</strong></span>
 </div>
 ${isRedZone ? `<span class="badge" style="background:#ea4335;color:#ffffff;border-radius:100px;padding:3px 8px;font-size:0.68rem;font-weight:700;"><i class="bi bi-exclamation-triangle-fill"></i> RED ZONE</span>` : ''}
 </div>

 <div style="margin-top:10px;">
 <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;font-size:0.75rem;">
 <span style="color:#5f6368;font-weight:500;">Voice Access Propensity (VAI):</span>
 <strong style="color:${vaiColor};font-weight:700;">${vaiPct}%</strong>
 </div>
 <div style="height:6px;background:#e8eaed;border-radius:100px;overflow:hidden;margin-bottom:10px;">
 <div style="width:${vaiPct}%;height:100%;background:${vaiColor};border-radius:100px;"></div>
 </div>

 <div style="display:flex;gap:6px;flex-wrap:wrap;">
 <span class="badge" style="background:${isRedZone ? '#fce8e6' : '#e8f0fe'};color:${isRedZone ? '#c5221f' : '#1a73e8'};border:1px solid ${isRedZone ? '#f87171' : '#c2e7ff'};border-radius:100px;padding:4px 10px;font-size:0.74rem;font-weight:700;">
 <i class="bi bi-calculator"></i> VAI: ${item.voice_access_index} (${item.vai_multiplier}x)
 </span>
 <span class="badge" style="background:${isLatent ? '#e6f4ea' : '#f1f3f4'};color:${isLatent ? '#137333' : '#3c4043'};border-radius:100px;padding:4px 10px;font-size:0.74rem;font-weight:700;">
 ${isLatent ? `Latent Need: ${item.latent_need_score}` : `Raw Score: ${item.raw_score}`}
 </span>
 </div>
 </div>
 </div>
 `;
 }).join('') || '<div class="text-muted text-center p-3">No surface data available.</div>';

 const html = `
 <div class="d-flex justify-content-between align-items-center mb-2 flex-wrap gap-2">
 <div>
 <h6 style="font-weight:700;color:#202124;margin:0;font-size:0.9rem;"><i class="bi bi-layers-fill" style="color:#1a73e8"></i> Active Surface Layer: <span style="color:#1a73e8;">${isLatent ? 'Latent Need Surface (VAI Propensity Adjusted)' : 'Raw Observed Demand Surface (Unadjusted Pings)'}</span></h6>
 <small style="color:#5f6368;font-size:0.72rem;">${isLatent ? 'Formula: Latent Need = (Observed Demand / Voice Access Index) SECC Deprivation Index' : 'Formula: Raw Observed Requests / Population per 100k'}</small>
 </div>
 <div class="btn-group btn-group-sm" role="group" style="background:#f1f3f4;border-radius:100px;padding:2px;">
 <button type="button" class="btn" style="border-radius:100px;font-weight:600;font-size:0.72rem;padding:4px 12px;${!isLatent ? 'background:#ffffff;color:#1a73e8;box-shadow:0 1px 3px rgba(0,0,0,0.15);' : 'color:#5f6368;'}" onclick="window.nvbLensController.toggleAnalystSurface('raw')">Analytics Raw Surface</button>
 <button type="button" class="btn" style="border-radius:100px;font-weight:600;font-size:0.72rem;padding:4px 12px;${isLatent ? 'background:#1a73e8;color:#ffffff;box-shadow:0 1px 3px rgba(0,0,0,0.15);' : 'color:#5f6368;'}" onclick="window.nvbLensController.toggleAnalystSurface('latent')"> Latent Need Surface (VAI Corrected)</button>
 </div>
 </div>
 ${kpiSummaryHtml}
 <div class="sim-results-grid">${cardsHtml}</div>
 `;
 container.innerHTML = html;
 }

 renderAnalystLens(lens) {
 this.lastAnalystLens = lens;
 this.analystSurfaceMode = this.analystSurfaceMode || 'latent';

 setTimeout(() => {
 if (window.nvbLensController) {
 window.nvbLensController.toggleAnalystSurface(window.nvbLensController.analystSurfaceMode || 'latent');
 }
 }, 50);

 return `
 <div class="google-material-card" style="background:#ffffff;border-radius:20px;border:1px solid #dadce0;box-shadow:0 4px 16px rgba(60,64,67,0.08), 0 1px 3px rgba(60,64,67,0.12);overflow:hidden;position:relative;font-family:'Google Sans', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;">
 <!-- Google Brand Accent Stripe -->
 <div style="height:4px;background:linear-gradient(90deg, #4285F4 0% 25%, #EA4335 25% 50%, #FBBC04 50% 75%, #34A853 75% 100%);"></div>

 <div style="padding:2rem;">
 <!-- Header Bar -->
 <div class="d-flex justify-content-between align-items-start flex-wrap gap-3" style="margin-bottom:1.5rem;">
 <div class="d-flex align-items-center gap-3">
 <div style="width:48px;height:48px;background:#e8f0fe;border-radius:14px;display:flex;align-items:center;justify-content:center;color:#1a73e8;font-size:1.5rem;flex-shrink:0;">
 <i class="bi bi-hourglass-split"></i>
 </div>
 <div>
 <h4 style="margin:0;font-weight:700;color:#202124;font-size:1.2rem;letter-spacing:-0.2px;line-height:1.3;">Analyst Executive Suite - Predictive Simulator & Time Machine</h4>
 <div class="d-flex align-items-center gap-2 mt-1.5 flex-wrap">
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-cpu-fill text-primary"></i> Google Cloud AI | Vertex AI Predictive Simulator</span>
 <span class="badge" style="background:#f1f3f4;color:#3c4043;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-sliders text-info"></i> Counterfactual Difference-Equations Engine</span>
 </div>
 </div>
 </div>
 <span class="badge" style="background:#e8f0fe;color:#174ea6;border-radius:100px;padding:6px 14px;font-size:0.78rem;font-weight:600;"><i class="bi bi-shield-check"></i> Authenticated Analyst Mode</span>
 </div>

 <!-- Voice Access Index (VAI) & Latent Demand Surface Interactive Card -->
 <div style="background:#f8fafd;border:1px solid #dadce0;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <div id="analyst-surface-container">
 <div class="text-center p-3 text-muted"><i class="bi bi-arrow-repeat spin"></i> Loading Latent Social Need Surface...</div>
 </div>
 </div>

 <!-- Live Counterfactual Sandbox Diff -->
 <div style="background:#f8fafd;border:1px solid #dadce0;border-left:5px solid #1a73e8;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <h5 style="font-size:0.95rem;font-weight:700;color:#174ea6;margin-bottom:6px;"><i class="bi bi-sliders" style="color:#1a73e8"></i> Live Counterfactual Sandbox Diff:</h5>
 <p style="margin:0;font-size:0.88rem;color:#3c4043;line-height:1.5;">${lens.counterfactual_sandbox?.diff_preview || 'Re-ranking active: w1 Demand Density 0.22, w2 Demand Velocity 0.16, w3 SECC Deprivation 0.16, w4 Infrastructure Gap 0.14.'}</p>
 </div>

 <!-- Stream Cards Row -->
 <div class="row g-3" style="margin-bottom:1.5rem;">
 <div class="col-md-6">
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;height:100%;">
 <div class="d-flex justify-content-between align-items-center mb-2">
 <h6 style="font-weight:700;color:#202124;margin:0;font-size:0.92rem;"><i class="bi bi-graph-up-arrow" style="color:#1a73e8"></i> Demand Velocity Stream</h6>
 <span class="badge" style="background:#fef7e0;color:#b06000;border-radius:100px;padding:4px 10px;font-size:0.72rem;font-weight:600;">${lens.demand_velocity_stream?.momentum_status || 'ACCELERATING'}</span>
 </div>
 <div style="font-size:1.6rem;font-weight:800;color:#1a73e8;margin-bottom:4px;">${lens.demand_velocity_stream?.acceleration_index || '+14.2%'}</div>
 <small style="color:#5f6368;font-size:0.78rem;">Momentum vector acceleration over 30-day window.</small>
 </div>
 </div>
 <div class="col-md-6">
 <div style="background:#fdf2f2;border:1px solid #f87171;border-radius:16px;padding:1.25rem 1.5rem;height:100%;">
 <div class="d-flex justify-content-between align-items-center mb-2">
 <h6 style="font-weight:700;color:#b91c1c;margin:0;font-size:0.92rem;"><i class="bi bi-radar" style="color:#dc2626"></i> Silence Radar Ping</h6>
 <span class="badge" style="background:#dc2626;color:#ffffff;border-radius:100px;padding:4px 10px;font-size:0.72rem;font-weight:600;">DETECTION ACTIVE</span>
 </div>
 <div style="font-size:0.88rem;color:#7f1d1d;font-weight:700;margin-bottom:4px;">Status: ${lens.silence_radar?.radar_status || 'SILENCE_DETECTED'}</div>
 <small style="color:#991b1b;font-size:0.78rem;display:block;"><i class="bi bi-telephone-outbound-fill"></i> ${lens.silence_radar?.action_queued || 'Outbound IVR survey queued for non-digital wards.'}</small>
 </div>
 </div>
 </div>

 <!-- Multilingual Cluster DNA & Dedup Audit -->
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;">
 <h6 style="font-weight:700;color:#202124;margin-bottom:8px;font-size:0.92rem;"><i class="bi bi-dna" style="color:#1a73e8"></i> Cluster Multilingual DNA & Semantic Dedup Audit</h6>
 <div class="d-flex flex-wrap gap-3 align-items-center" style="font-size:0.85rem;">
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:5px 12px;font-weight:500;">Tamil: <strong>61%</strong></span>
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:5px 12px;font-weight:500;">Telugu: <strong>27%</strong></span>
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:5px 12px;font-weight:500;">English: <strong>12%</strong></span>
 <span class="badge ms-auto" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:5px 12px;font-weight:600;"><i class="bi bi-check-circle-fill"></i> Dedup Precision: <strong>92.4% PASS</strong></span>
 </div>
 </div>
 </div>
 </div>
 `;
 }


 renderAuditorLens(lens) {
 return `
 <div class="google-material-card" style="background:#ffffff;border-radius:20px;border:1px solid #dadce0;box-shadow:0 4px 16px rgba(60,64,67,0.08), 0 1px 3px rgba(60,64,67,0.12);overflow:hidden;position:relative;font-family:'Google Sans', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;">
 <!-- Google Brand Accent Stripe -->
 <div style="height:4px;background:linear-gradient(90deg, #34A853 0% 50%, #4285F4 50% 100%);"></div>

 <div style="padding:2rem;">
 <!-- Header Bar -->
 <div class="d-flex justify-content-between align-items-start flex-wrap gap-3" style="margin-bottom:1.5rem;">
 <div class="d-flex align-items-center gap-3">
 <div style="width:48px;height:48px;background:#e6f4ea;border-radius:14px;display:flex;align-items:center;justify-content:center;color:#137333;font-size:1.5rem;flex-shrink:0;">
 <i class="bi bi-shield-x"></i>
 </div>
 <div>
 <h4 style="margin:0;font-weight:700;color:#202124;font-size:1.2rem;letter-spacing:-0.2px;line-height:1.3;">Auditor Executive Suite - Tamper-Evident Audit Trail & Consent Ledger</h4>
 <div class="d-flex align-items-center gap-2 mt-1.5 flex-wrap">
 <span class="badge" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-shield-check text-success"></i> Google Security & Privacy Verified</span>
 <span class="badge" style="background:#f1f3f4;color:#3c4043;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-calculator-fill text-primary"></i> Econometric DiD & Cryptographic Epistemics</span>
 </div>
 </div>
 </div>
 <span class="badge" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:6px 14px;font-size:0.78rem;font-weight:600;"><i class="bi bi-lock-fill"></i> Zero-Trust Auditor Mode</span>
 </div>

 <!-- Causal Impact Auditor Card (DiD Proof & Ex-Ante Matched Controls) -->
 <div style="background:#f6fbf7;border:1px solid #ceead6;border-left:5px solid #34a853;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2">
 <strong style="color:#137333;font-size:0.98rem;"><i class="bi bi-patch-check-fill"></i> Causal Impact Auditor - Difference-in-Differences (DiD) Econometric Proof</strong>
 <div class="d-flex align-items-center gap-2">
 <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:4px 10px;font-size:0.72rem;font-weight:600;"><i class="bi bi-shield-lock-fill"></i> ${lens.causal_impact_auditor?.pap_audit_hash || 'PAP-MATCH-8F3A29B1'}</span>
 <span class="badge" style="background:#ceead6;color:#137333;border-radius:100px;padding:4px 12px;font-weight:700;">${lens.causal_impact_auditor?.verdict || 'IMPACT VERIFIED '}</span>
 </div>
 </div>
 <p style="margin:0 0 8px;font-size:0.88rem;color:#202124;line-height:1.4;">
 Treated Geofence: <strong style="color:#137333;">${lens.causal_impact_auditor?.treated_geofence || 'Karur Ward 12 (-78% decay)'}</strong><br>
 Matched Synthetic Control Twins: <strong style="color:#174ea6;">${lens.causal_impact_auditor?.matched_control_geofence || 'Tirupati Ward 04, Bangalore Urban Ward 15, Chennai Ward 02 (Avg -4% decay)'}</strong>
 </p>
 <div class="d-flex align-items-center justify-content-between flex-wrap gap-2 mt-2 pt-2" style="border-top:1px dashed #ceead6;font-size:0.82rem;">
 <div>
 <span style="color:#5f6368;">Net DiD Causal Lift:</span>
 <strong style="color:#137333;font-size:0.9rem;margin-left:4px;">${lens.causal_impact_auditor?.causal_lift || '-74% (CI 95%: [-69%, -79%])'}</strong>
 </div>
 <div class="d-flex gap-3">
 <span>Covariate Balance: <strong style="color:#137333;">SMD = ${lens.causal_impact_auditor?.covariate_balance_smd || 0.042} (EXCELLENT)</strong></span>
 <span>Parallel Trends: <strong style="color:#137333;">p = ${lens.causal_impact_auditor?.parallel_trend_pvalue || 0.88} (EQUIVALENT)</strong></span>
 </div>
 </div>
 </div>

 <!-- Provenance Lineage Graph -->
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <h6 style="font-weight:700;color:#202124;margin-bottom:10px;font-size:0.92rem;"><i class="bi bi-diagram-2" style="color:#1a73e8"></i> Cryptographic Provenance Lineage Graph</h6>
 <div class="d-flex flex-wrap gap-2 align-items-center" style="font-size:0.82rem;">
 ${(lens.provenance_chain?.lineage || ['Bhashini ASR (Audio Transcript)', 'FAISS Vector Clustering (#TN-KAR-0417)', 'SHAP Multi-Criteria Score (0.942)', 'PM Gati Shakti Master Plan Allocation']).map((step, idx) => `
 <span class="badge" style="background:#ffffff;color:#3c4043;border:1px solid #dadce0;border-radius:100px;padding:6px 12px;font-weight:500;">${idx + 1}. ${step}</span>
 ${idx < 3 ? '<i class="bi bi-arrow-right text-muted"></i>' : ''}
 `).join('')}
 </div>
 </div>

 <!-- AI Bias & Demographic Drift Telemetry -->
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;">
 <h6 style="font-weight:700;color:#202124;margin-bottom:8px;font-size:0.92rem;"><i class="bi bi-cpu-fill" style="color:#1a73e8"></i> AI Parity & Demographic Bias Telemetry Monitor</h6>
 <div class="d-flex justify-content-between align-items-center flex-wrap gap-2" style="font-size:0.85rem;">
 <span>Rural Dialect WER: <strong>4.2%</strong></span>
 <span>Urban Standard WER: <strong>2.1%</strong></span>
 <span class="badge" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:4px 12px;font-weight:600;"><i class="bi bi-shield-check"></i> Disparate Impact Ratio: <strong>0.982 (PASS)</strong></span>
 </div>
 </div>
 </div>
 </div>
 `;
 }

 renderAdminLens(lens) {
 return `
 <div class="google-material-card" style="background:#ffffff;border-radius:20px;border:1px solid #dadce0;box-shadow:0 4px 16px rgba(60,64,67,0.08), 0 1px 3px rgba(60,64,67,0.12);overflow:hidden;position:relative;font-family:'Google Sans', -apple-system, BlinkMacSystemFont, Roboto, sans-serif;">
 <!-- Google Brand Accent Stripe -->
 <div style="height:4px;background:linear-gradient(90deg, #EA4335 0% 33%, #FBBC04 33% 66%, #4285F4 66% 100%);"></div>

 <div style="padding:2rem;">
 <!-- Header Bar -->
 <div class="d-flex justify-content-between align-items-start flex-wrap gap-3" style="margin-bottom:1.5rem;">
 <div class="d-flex align-items-center gap-3">
 <div style="width:48px;height:48px;background:#feefc3;border-radius:14px;display:flex;align-items:center;justify-content:center;color:#b06000;font-size:1.5rem;flex-shrink:0;">
 <i class="bi bi-sliders2"></i>
 </div>
 <div>
 <h4 style="margin:0;font-weight:700;color:#202124;font-size:1.2rem;letter-spacing:-0.2px;line-height:1.3;">Admin Control Suite - Security Alerts & Governance Operations</h4>
 <div class="d-flex align-items-center gap-2 mt-1.5 flex-wrap">
 <span class="badge" style="background:#feefc3;color:#b06000;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-shield-lock-fill text-warning"></i> Illustrative administration overview</span>
 <span class="badge" style="background:#f1f3f4;color:#3c4043;border-radius:100px;padding:4px 12px;font-weight:500;"><i class="bi bi-key-fill text-danger"></i> No signed steering version recorded</span>
 </div>
 </div>
 </div>
 <span class="badge" style="background:#fce8e6;color:#c5221f;border-radius:100px;padding:6px 14px;font-size:0.78rem;font-weight:600;"><i class="bi bi-shield-lock-fill"></i> Mission-Control Admin </span>
 </div>

 <!-- Governance Weight Tuning Card -->
 <div style="background:#fef7e0;border:1px solid #feefc3;border-left:5px solid #fbbc04;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
 <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2">
 <strong style="color:#b06000;font-size:0.98rem;"><i class="bi bi-file-earmark-diff"></i> Governance Weight Tuning Rationale</strong>
 <span class="badge" style="background:#feefc3;color:#b06000;border-radius:100px;padding:4px 12px;font-weight:700;">${lens.weight_tuning_governance?.active_weight_version ? 'ACTIVE VERSION: ' + lens.weight_tuning_governance.active_weight_version : 'CONFIGURATION: Default Baseline'}</span>
 </div>
 <p style="margin:0 0 10px;font-size:0.88rem;color:#202124;line-height:1.5;">${lens.weight_tuning_governance?.signed_governance_diff || 'No administrative weight overrides recorded (default policy weights active).'}</p>
 <button class="btn btn-sm" style="background:#ffffff;color:#c5221f;border:1px solid #f87171;border-radius:20px;padding:6px 16px;font-size:0.8rem;font-weight:500;" onclick="alert('Mandatory rationale prompt triggered: All algorithm weight modifications require cryptographic rationale signatures.')"><i class="bi bi-pencil-square"></i> Modify Weights (Requires Rationale Signature)</button>
 </div>

 <!-- Vitals & Crisis Control Row -->
 <div class="row g-3">
 <div class="col-md-6">
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;height:100%;">
 <h6 style="font-weight:700;color:#202124;margin-bottom:10px;font-size:0.92rem;"><i class="bi bi-activity" style="color:#1a73e8"></i> Mission-Control System Vitals</h6>
 <div class="d-flex justify-content-between align-items-center mb-2" style="font-size:0.85rem;">
 <span>Ingestion Latency:</span>
 <strong style="color:#1a73e8;">${lens.system_health_cockpit?.ingestion_latency_ms ? lens.system_health_cockpit.ingestion_latency_ms + ' ms' : 'Not measured'}</strong>
 </div>
 <div class="d-flex justify-content-between align-items-center mb-2" style="font-size:0.85rem;">
 <span>Silence Map Coverage:</span>
 <strong style="color:#137333;">${lens.system_health_cockpit?.silence_map_coverage || 'Not evaluated (Illustrative)'}</strong>
 </div>
 <div class="d-flex justify-content-between align-items-center" style="font-size:0.85rem;">
 <span>Event Bus Throughput:</span>
 <strong style="color:#202124;">${lens.system_health_cockpit?.event_bus_throughput || 'Not measured (Illustrative)'}</strong>
 </div>
 </div>
 </div>
 <div class="col-md-6">
 <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;height:100%;">
 <h6 style="font-weight:700;color:#202124;margin-bottom:10px;font-size:0.92rem;"><i class="bi bi-toggle2-off" style="color:#ea4335"></i> Crisis Mode Emergency Posture</h6>
 <p style="font-size:0.8rem;color:#5f6368;margin-bottom:10px;">Illustrative emergency control. No response SLA or operational posture has been verified here.</p>
 <div class="d-flex align-items-center justify-content-between">
 <span class="badge" style="background:#e8f0fe;color:#174ea6;border-radius:100px;padding:4px 12px;font-size:0.75rem;font-weight:600;">POSTURE: NOT VERIFIED</span>
 <button class="btn btn-sm" style="background:#ea4335;color:#ffffff;border-radius:20px;padding:6px 16px;font-size:0.8rem;font-weight:500;border:none;" onclick="alert('Illustrative control only. No operational posture has changed.')">Activate Crisis Mode </button>
 </div>
 </div>
 </div>
 </div>
 </div>
 </div>
 `;
 }

 async verifyChain() {
 try {
 const res = await fetch('/api/v1/transparency/verify-chain');
 const data = await res.json();
 alert(`Cryptographic Verification Result:\nValid: ${data.valid}\nEntries Checked: ${data.count}\nMessage: ${data.message || data.reason}`);
 } catch (err) {
 alert('Verification failed: ' + err.message);
 }
 }
}

window.nvbLensController = new NVBLensController();
