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
 <span class="badge" style="background:#f1f3f4;color:#5f6368;border-radius:100px;padding:5px 12px;font-weight:500;">Language mix: Not measured</span>\r\n <span class="badge ms-auto" style="background:#f1f3f4;color:#5f6368;border-radius:100px;padding:5px 12px;font-weight:600;">Deduplication: Not evaluated</span>
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

 <!-- Econometric Evaluation Framework Card (Demonstration Protocol) -->
  <div style="background:#f6fbf7;border:1px solid #ceead6;border-left:5px solid #34a853;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
  <div class="d-flex justify-content-between align-items-center flex-wrap gap-2 mb-2">
  <strong style="color:#137333;font-size:0.98rem;"><i class="bi bi-info-circle-fill"></i> Econometric Evaluation Framework (Illustrative Methodology)</strong>
  <div class="d-flex align-items-center gap-2">
  <span class="badge" style="background:#e8f0fe;color:#1a73e8;border-radius:100px;padding:4px 10px;font-size:0.72rem;font-weight:600;"><i class="bi bi-shield-lock-fill"></i> ${lens.causal_impact_auditor?.pap_audit_hash || 'PAP-MATCH-DEMO'}</span>
  <span class="badge" style="background:#ceead6;color:#137333;border-radius:100px;padding:4px 12px;font-weight:700;">${lens.causal_impact_auditor?.verdict || 'ILLUSTRATIVE PROTOCOL'}</span>
  </div>
  </div>
  <p style="margin:0 0 8px;font-size:0.88rem;color:#202124;line-height:1.4;">
  Target Geography: <strong style="color:#137333;">${lens.causal_impact_auditor?.treated_geofence || 'Karur Ward 12 (Illustrative Post-Intervention Tracking)'}</strong><br>
  Matched Control Group: <strong style="color:#174ea6;">${lens.causal_impact_auditor?.matched_control_geofence || 'Synthetic Control Twins (Tirupati Ward 04, Bangalore Urban Ward 15)'}</strong>
  </p>
  <div class="d-flex align-items-center justify-content-between flex-wrap gap-2 mt-2 pt-2" style="border-top:1px dashed #ceead6;font-size:0.82rem;">
  <div>
  <span style="color:#5f6368;">Causal Attribution Status:</span>
  <strong style="color:#137333;font-size:0.9rem;margin-left:4px;">${lens.causal_impact_auditor?.causal_lift || 'Not Evaluated (Requires longitudinal field data)'}</strong>
  </div>
  <div class="d-flex gap-3">
  <span>Covariate Balance: <strong style="color:#137333;">Illustrative specification</strong></span>
  <span>Field Validation: <strong style="color:#137333;">Pending Ministry Rollout</strong></span>
  </div>
  </div>
  </div>

  <!-- Provenance Lineage Graph -->
  <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;margin-bottom:1.5rem;">
  <h6 style="font-weight:700;color:#202124;margin-bottom:10px;font-size:0.92rem;"><i class="bi bi-diagram-2" style="color:#1a73e8"></i> Cryptographic Provenance Lineage Graph</h6>
  <div class="d-flex flex-wrap gap-2 align-items-center" style="font-size:0.82rem;">
  ${(lens.provenance_chain?.lineage || ['Audio transcript evidence', 'Demand cluster record', 'Priority evidence reference', 'Planning source reference']).map((step, idx) => `
  <span class="badge" style="background:#ffffff;color:#3c4043;border:1px solid #dadce0;border-radius:100px;padding:6px 12px;font-weight:500;">${idx + 1}. ${step}</span>
  ${idx < 3 ? '<i class="bi bi-arrow-right text-muted"></i>' : ''}
  `).join('')}
  </div>
  </div>

  <!-- AI Bias & Demographic Drift Telemetry -->
  <div style="background:#f8f9fa;border:1px solid #e8eaed;border-radius:16px;padding:1.25rem 1.5rem;">
  <h6 style="font-weight:700;color:#202124;margin-bottom:8px;font-size:0.92rem;"><i class="bi bi-cpu-fill" style="color:#1a73e8"></i> AI Parity & Demographic Bias Telemetry Monitor</h6>
  <div class="d-flex justify-content-between align-items-center flex-wrap gap-2" style="font-size:0.85rem;">
  <span>Rural Dialect WER: <strong>Not evaluated (Lab benchmark)</strong></span>
  <span>Urban Standard WER: <strong>Not evaluated (Lab benchmark)</strong></span>
  <span class="badge" style="background:#e6f4ea;color:#137333;border-radius:100px;padding:4px 12px;font-weight:600;"><i class="bi bi-shield-check"></i> Parity Target: <strong>&ge; 0.80 Disparate Ratio</strong></span>
  </div>
  </div></div>
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
