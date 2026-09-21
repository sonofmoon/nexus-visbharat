(() => {
  'use strict';
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const badge=(value,cls='')=>`<span class="badge ${cls}">${esc(String(value??'').replace(/_/g,' '))}</span>`;
  const num=value=>value==null?'Not available':Number(value).toLocaleString();
  const button=(action,label,id='')=>`<button type="button" data-action="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
  const table=(headers,rows)=>`<div class="table-wrap" tabindex="0" role="region" aria-label="Data table"><table><thead><tr>${headers.map(x=>`<th>${esc(x)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(row=>`<tr>${row.map(x=>`<td>${x}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">No records in this scope.</td></tr>`}</tbody></table></div>`;
  const field=(name,label,value='',type='text')=>`<label class="${type==='textarea'?'wide':''}">${esc(label)}${type==='textarea'?`<textarea name="${name}" maxlength="8000">${esc(value)}</textarea>`:type==='checkbox'?`<input type="checkbox" name="${name}" ${value?'checked':''}>`:`<input name="${name}" type="${type}" ${type==='number'?'min="0" step="any"':''} value="${esc(value)}">`}</label>`;
  let setup=null,deliveryItems=[],planOptions=null,planningPlan=null;
  const apiBase='/api/v2/pilot/portal/';
  function options(d){return planOptions||d.programme.portal.planning;}
  window.PilotWorkspace={
    options,
    async settings(d,api,role,configurationTools,readiness){
      setup=await api(apiBase+'settings');const editable=setup.editable;
      const core=`<section class="card settings-section" id="setup-programme"><h2>Programme & districts</h2><p>One programme for Vellore and Tirupati. Community boundaries and identities retain their separate review evidence.</p><form data-form="setup-core"><fieldset ${editable?'':'disabled'}><div class="settings-fields">${field('title','Programme title',d.programme.title)}${field('start_date','Start date',d.programme.config.start_date,'date')}${field('monthly_report_capacity','Monthly request capacity',d.programme.config.monthly_report_capacity,'number')}${field('monthly_cloud_budget_inr','Cloud allowance (₹ / month)',d.programme.config.monthly_cloud_budget_inr,'number')}${field('vellore','Vellore routing department',d.programme.config.routing.Vellore)}${field('tirupati','Tirupati routing department',d.programme.config.routing.Tirupati)}<div class="wide"><h3>Languages</h3>${['ta','te','en'].map(l=>`<label class="checkline"><input type="checkbox" name="language" value="${l}" ${d.programme.config.languages.includes(l)?'checked':''}>${{ta:'Tamil',te:'Telugu',en:'English'}[l]}</label>`).join('')}</div><div class="wide"><h3>Enabled services</h3>${['Water Supply','Road','Sanitation','Electricity','Health','Education','Transport','Housing','Digital Connectivity','Other'].map(c=>`<label class="checkline"><input type="checkbox" name="category" value="${esc(c)}" ${(d.programme.config.categories||['Water Supply','Road','Sanitation','Electricity','Health','Education','Transport','Housing','Digital Connectivity','Other']).includes(c)?'checked':''}>${esc(c)}</label>`).join('')}</div></div><div class="actions"><button class="primary">Save programme</button></div></fieldset></form></section>`;
      const groups=setup.groups.map(([key,title,fields])=>`<section class="card settings-section" id="setup-${key==='programme'?'details':key}"><h2>${key==='programme'?'Programme details':esc(title)}</h2><form data-form="setup-group" data-group="${key}"><fieldset ${editable?'':'disabled'}><div class="settings-fields">${fields.map(([name,label,type])=>field(name,label,setup.settings[key][name]??'',type==='boolean'?'checkbox':['number','fraction'].includes(type)?'number':type==='date'?'date':'textarea')).join('')}</div><div class="actions"><button class="primary">Save ${esc(title.toLowerCase())}</button></div></fieldset></form>${key==='departments'?`<h3>Assigned officers</h3>${table(['Identity','Role','State','District'],d.members.map(m=>[esc(m.name),esc(m.role),esc(m.state||'Programme-wide'),esc(m.district||'Assigned states')]))}`:''}</section>`).join('');
      const channels=`<section class="card settings-section" id="setup-channels"><h2>Channels & communication</h2><p>Configure each channel once. External channels appear as available on Home only after a signed inbound test and confirmed receipt delivery. Credentials are configured through the deployment’s Secret Manager references.</p><div class="integration-grid">${setup.channels.map(c=>`<form class="integration-card" data-form="setup-channel" data-channel="${c.id}"><h3>${esc(c.title)}</h3><span class="setting-status">${esc(c.status)}</span><fieldset ${editable?'':'disabled'}>${field('enabled','Enable this channel',setup.settings.channels[c.id].enabled,'checkbox')}${!['web','dialogflow'].includes(c.id)?field('address','Public channel address',setup.settings.channels[c.id].address):''}${field('owner','Responsible team',setup.settings.channels[c.id].owner)}<div class="actions"><button>Save channel</button></div></fieldset>${!['web','dialogflow'].includes(c.id)?`<p class="note">Gateway: <code>/api/v2/pilot/channels/${c.id}/webhook</code>Deployment secret: <code>PILOT_CHANNEL_${c.id.toUpperCase()}_SECRET</code>Signed normalized messages and delivery receipts are required. <a href="/docs/MINISTRY_PILOT_CHANNELS.md" target="_blank" rel="noopener">Connection contract</a></p>`:''}</form>`).join('')}</div></section>`;
      const integrations=`<section class="card settings-section" id="setup-integrations"><h2>Google AI & Cloud</h2><form data-form="setup-ai"><fieldset ${editable?'':'disabled'}><label>Translation provider<select name="translation_provider"><option value="vertex" ${d.programme.config.translation_provider!=='cloud_translation'?'selected':''}>Gemini on Vertex AI</option><option value="cloud_translation" ${d.programme.config.translation_provider==='cloud_translation'?'selected':''}>Regional Cloud Translation</option></select></label><div class="actions"><button>Save translation provider</button></div></fieldset></form><p>Deployment configuration and live service availability are distinct. Saving an owner or resource reference does not provision cloud infrastructure or enable model calls.</p><div class="integration-grid">${setup.integrations.map(i=>`<form class="integration-card" data-form="setup-integration" data-integration="${i.id}"><h3>${esc(i.title)}</h3><p>${esc(i.purpose)}</p><span class="setting-status">${esc(i.status)}</span><p class="note">Deployment requirements</p>${i.requirements.map(v=>`<code>${esc(v)}</code>`).join('')}<fieldset ${editable?'':'disabled'}>${field('owner','Service owner',i.owner)}${field('reference','Resource / runbook reference',i.reference)}<div class="actions"><button>Save service record</button></div></fieldset></form>`).join('')}</div><p class="note">Secrets and private keys are never entered into programme settings. Connection changes are applied through the controlled deployment.</p></section>`;
      const sources=`<section class="card settings-section"><h2>Public data snapshots</h2><p>Source retrieval is separate from independent verification. Missing and stale sources do not supply planning facts. Refresh through the deployment job after configuring the resource and its schema.</p>${table(['Source','Status','Rows retained','Retrieved','Integrity'],(setup.data_sources||[]).map(s=>[esc(s.name),esc(s.status),num(s.records),esc(s.retrieved_at||'Not retrieved'),s.sha256?'<code>'+esc(s.sha256)+'</code>':'No snapshot']))}</section>`;
      const links=[['programme','Programme & districts'],['details','Programme details'],['departments','Departments & officers'],['workflow','Service workflows'],['channels','Channels & communication'],['planning','Development & policy'],['data','Data & maps'],['integrations','Google AI & Cloud'],['evaluation','Delivery & evaluation'],['privacy','Privacy & access'],['operations','Operations & activation'],['readiness','Activation checks'],['communities','Communities & agency imports']];
      const notice=!editable?`<div class="notice"><strong>Read-only settings.</strong> ${role!=='admin'?`Your active demonstration role is <strong>${esc(role==='analyst'?'Programme analyst':role==='auditor'?'Independent auditor':role+' district officer')}</strong>. Under Role-Based Access Control (RBAC), only a programme-wide administrator can modify configurations.`:'A state or district filter is active in the toolbar. Settings apply programme-wide across both Vellore and Tirupati.'}${role!=='admin'&&window.NVBPilotConfig.demo?` ${button('switch-admin','Switch to Programme Administrator')}`:''}</div>`:'';
      return `<div class="settings-layout"><nav class="settings-index" aria-label="Settings categories">${links.map(([id,title])=>`<a href="#setup-${id}">${title}</a>`).join('')}</nav><div>${notice}${core}${groups}${sources}${channels}${integrations}<section id="setup-readiness" class="settings-section">${readiness}</section><section id="setup-communities" class="settings-section">${configurationTools}</section></div></div>`;
    },
    overview(d){
      return `<section class="card"><span class="eyebrow">YOUR DISTRICT WORK</span><h2>What needs attention today?</h2><p>Choose the work stage. Your district assignment stays with you.</p><div class="workspace-action-grid"><button data-tab="requests">Requests &amp; Review<small>Review, assign and respond to citizen needs.</small></button><button data-tab="proposals">Development &amp; Policy<small>Compare priorities and prepare evidence-backed decisions.</small></button><button data-tab="delivery">Delivery &amp; Outcomes<small>Record progress and review the result.</small></button></div></section>`;
    },
    async planning(d,api,role){
      const opts=options(d),out=await api('/api/v2/analyst/snapshot','GET',null,{budget_lakh:opts.budget_lakh,operating_budget_lakh:opts.operating_budget_lakh,capacity:opts.capacity,max_per_district:opts.max_per_district,equity_share:opts.equity_share});
      const plan=out.scenario;
      planningPlan=plan;
      return `<section class="card"><h2>Development &amp; Policy</h2><p>Compare reviewed needs within your district’s planning envelope. Changing this scenario does not approve expenditure.</p><form data-form="scenario" class="form">${field('budget_lakh','Capital budget (₹ lakh)',opts.budget_lakh,'number')}${field('operating_budget_lakh','Annual operating budget (₹ lakh)',opts.operating_budget_lakh,'number')}${field('capacity','Project capacity',opts.capacity,'number')}${field('max_per_district','Projects per district',opts.max_per_district,'number')}${field('equity_share','Equity reservation (0–1)',opts.equity_share,'number')}<button>Compare scenario</button></form><div class="decision-summary"><div>Selected projects<strong>${esc(plan.allocation.selected_count)}</strong></div><div>Proposed capital (₹ lakh)<strong>${esc(plan.allocation.budget_used_lakh)}</strong></div><div>Existing commitments (₹ lakh)<strong>${esc(plan.allocation.existing_commitments_lakh)}</strong></div><div>Reports linked<strong>${esc(plan.allocation.reports_linked)}</strong></div></div>${table(['Project / location','Evidence & priority','Cost basis','Next action'],plan.items.map(p=>{
        const hasDecision=Boolean(p.decision_id);
        const draftBadge=hasDecision?`<span class="badge" style="background:#e6f4ea;color:#137333;margin-bottom:4px;display:inline-block;font-weight:600;"><i class="bi bi-check2-circle"></i> Decision Drafted</span><br>`:'';
        const decisionBtn=role!=='auditor'?(hasDecision?button('draft','Manage decision',p.project_id):button('draft','Prepare decision',p.project_id)):'';
        const selectionBadge=p.selected?`<span class="badge" style="background:#e8f0fe;color:#1a73e8;display:inline-block;margin-top:2px;">Selected in scenario</span>`:`<span class="badge" style="background:#fef7e0;color:#b06000;display:inline-block;margin-top:2px;">Outside selection</span>`;
        return [
          `${esc(p.title)}<br>${esc(p.district+' · '+p.ward)}<br><code>${esc(p.project_id)}</code>`,
          `${esc(p.reports)} reports · score ${esc(p.priority_score)}<br>${selectionBadge}`,
          `₹${esc(p.estimated_cost_lakh)} lakh<br><small style="color:var(--muted);">${esc(p.cost_basis)}</small>`,
          draftBadge+
          button('inspect-proposal','Review evidence',p.project_id)+
          decisionBtn+
          (p.decision_id&&role==='admin'?button('engineering','Record engineering review',p.decision_id)+button('approve','Approve decision',p.decision_id):'')+
          button('audit-project','Review audit evidence',p.project_id)+
          button('site-evidence','Inspect site evidence',p.project_id)
        ];
      }))}<div class="actions">${button('policy-brief','Prepare policy brief')}${button('policy-sources','Inspect planning sources')}${button('forecast','Forecast district demand')}</div><p class="note">${esc(plan.formula)}</p></section>`;
    },
    async delivery(d,api,role){
      const data=await api(apiBase+'delivery');deliveryItems=data.items;
      return `<section class="card"><h2>Delivery &amp; Outcomes</h2><p>Follow approved development decisions from mobilisation to inspection and measured outcomes.</p>${table(['Project / district','Delivery stage','Owner / next milestone','Observed result','Action'],data.items.map(p=>[`${esc(p.title)}<br>${esc(p.district)}<br><code>${esc(p.decision_id)}</code>`,esc(p.delivery.stage||'Planned'),`${esc(p.delivery.owner||'Assign an owner')}<br>${esc(p.delivery.milestone||'Set the first milestone')}<br>${esc(p.delivery.due_date||'Set a due date')}`,`${esc(p.delivery.observed||'Baseline and follow-up measurement required')}<br>Recorded expenditure: ₹${esc(p.delivery.expenditure_lakh??0)} lakh`,(role!=='auditor'?button('delivery-edit','Update delivery',p.decision_id):button('delivery-edit','Inspect delivery',p.decision_id))+button('audit-project','Review audit evidence',p.project_id)+button('site-evidence','Inspect site evidence',p.project_id)]))}<p class="note">Only approved or funded decisions appear here. Recorded expenditure is an officer declaration, not a treasury payment instruction.</p></section>`;
    },
    async action(action,id,ctx){
      const {api,inspect,d,role}=ctx;
      if(action==='forecast'){
        const out=await api(apiBase+'forecast','POST',{});inspect(`<h2>District demand forecast</h2>${table(['District','Reports','Predicted stress','Model'],out.items.map(x=>[esc(x.district),esc(x.complaints),esc(x.predicted_stress_score_next_quarter),esc(x.model_name)]))}<p>${esc(out.notice)}</p>`);return true;
      }
      if(action==='site-evidence'){
        inspect(`<h2>Inspect project location</h2><p>Confirm the site before requesting geographic evidence.</p><form data-form="site-intelligence" data-id="${esc(id)}"><div class="settings-fields"><label>Action<select name="action"><option value="geocode">Find site address</option><option value="satellite">Observe geographic conditions</option></select></label>${field('address','Site address or landmark')}${field('lat','Latitude')}${field('lng','Longitude')}${field('radius_m','Observation radius (10–1,000 metres)',100,'number')}</div><div class="actions"><button>Inspect site</button></div></form>`);return true;
      }
      if(action==='policy-brief'){
        const result=await api('/api/v2/analyst/brief','POST',{...ctx.scope,...options(d)});
        inspect(`<h2>${esc(result.title)}</h2><p>${esc(result.status)}</p>${result.claims.map(c=>`<p>${esc(c.text)}</p><small>Source: ${esc(c.source)}</small>`).join('')}<h3>Assumptions</h3><ul>${result.assumptions.map(a=>`<li>${esc(a)}</li>`).join('')}</ul><div class="actions">${button('ai-policy','Draft an AI explanation')}</div>`);return true;
      }
      if(action==='ai-policy'){
        const out=await api(apiBase+'policy-assistance','POST',{});inspect(`<h2>AI policy assistance</h2><p>${esc(out.summary)}</p><p class="note">Draft for officer review. Sources: ${esc(out.sources.join(', '))}</p>`);return true;
      }
      if(action==='policy-sources'){
        const data=await api('/api/v2/analyst/evidence');inspect(`<h2>Planning sources</h2>${table(['Source','State'],(data.sources||[]).map(s=>[esc(s.title||s.name||s.source||s.file),esc(s.status||s.notice||'Review source provenance')]))}<p>${esc(d.programme.portal.data.source_register||'Record district source ownership in Settings.')}</p><p>${esc(d.programme.portal.planning.policy_library||'Policy references have not yet been recorded.')}</p>`);return true;
      }
      if(action==='delivery-edit'){
        const p=deliveryItems.find(p=>p.decision_id===id),r=p.delivery;
        inspect(`<h2>${esc(p.title)}</h2><form data-form="delivery" data-id="${esc(id)}" data-version="${p.version}"><fieldset ${role==='auditor'?'disabled':''}><div class="settings-fields"><label>Stage<select name="stage">${['Planned','In progress','Inspection','Completed'].map(v=>`<option ${v===(r.stage||'Planned')?'selected':''}>${v}</option>`).join('')}</select></label>${field('owner','Responsible officer / team',r.owner)}${field('milestone','Next milestone',r.milestone)}${field('due_date','Due date',r.due_date,'date')}${field('expenditure_lakh','Recorded expenditure (₹ lakh)',r.expenditure_lakh??0,'number')}${field('evidence_reference','Inspection evidence reference',r.evidence_reference)}${field('baseline','Baseline and measure',r.baseline,'textarea')}${field('target','Target and measurement date',r.target,'textarea')}${field('observed','Observed outcome',r.observed,'textarea')}${field('citizen_feedback','Citizen feedback',r.citizen_feedback,'textarea')}${field('inspection_notes','Inspection notes',r.inspection_notes,'textarea')}</div><div class="actions"><button class="primary">Save delivery record</button></div></fieldset></form>`);return true;
      }
      if(action==='audit-project'){
        const data=await api('/api/v2/auditor/projects/'+encodeURIComponent(id));
        const p=data.project||{}, r=data.readiness||{};
        const gateCard=(num,title,ok,subtitle,detail)=>`
          <div style="padding:14px;border:1px solid var(--line);border-radius:10px;background:${ok?'#f0f9f5':'#fffdf5'};">
            <div style="font-weight:600;font-size:11px;color:var(--muted);letter-spacing:1px;margin-bottom:4px;">GATE ${num}: ${title}</div>
            <div style="font-weight:650;font-size:13px;display:flex;align-items:center;gap:6px;color:var(--ink);">
              <i class="bi ${ok?'bi-check-circle-fill':'bi-dash-circle'}" style="color:${ok?'#11695c':'#b06000'};font-size:15px;"></i>
              ${subtitle}
            </div>
            <div style="margin-top:6px;"><span class="badge ${ok?'good':'warn'}">${detail}</span></div>
          </div>`;
        const gatesHtml=`<div class="grid" style="grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0;">
          ${gateCard(1,'DEMAND EVIDENCE',r.source_tickets,'Source Tickets Verified',r.source_tickets?`${p.reports||1} citizen report(s) linked`:'Awaiting verified tickets')}
          ${gateCard(2,'FEASIBILITY',r.engineering_review,'Engineering Review',r.engineering_review?'Feasibility survey recorded':'Awaiting engineering survey')}
          ${gateCard(3,'GOVERNANCE',r.human_decision,'Administrative Approval',r.human_decision?'Approved decision recorded':'Decision pending review')}
          ${gateCard(4,'DATA INTEGRITY',r.verified_reference,'Reference Integrity',r.verified_reference?'Verified operational baseline':'Synthetic demonstration baseline')}
        </div>`;
        const evidenceRows=(data.evidence||data.assets||[]);
        const evidenceHtml=evidenceRows.length
          ? table(['Evidence','Kind','Status'],evidenceRows.map(e=>[esc(e.title||e.evidence_id),esc(e.kind),badge(e.status)]))
          : `<div class="empty" style="padding:14px 18px;margin:14px 0;background:var(--soft);border-radius:10px;font-size:13px;"><i class="bi bi-info-circle"></i> No external evidence files attached yet. Citizen tickets and AI telemetry are cryptographically indexed in the ledger.</div>`;
        const decisionsHtml=data.decisions&&data.decisions.length
          ? `<h3>Recorded decisions</h3>${table(['Decision ID','Status','Version','Engineering review'],data.decisions.map(d=>[`<code>${esc(d.decision_id)}</code>`,badge(d.status),esc(d.score_version||'v1'),esc(d.engineering_review?'Recorded':'Pending')]))}`
          : '';
        const statusBadge=data.ready
          ? '<span class="badge good" style="font-size:12px;padding:5px 12px;"><i class="bi bi-shield-check"></i> Audit Ready</span>'
          : `<span class="badge warn" style="font-size:12px;padding:5px 12px;"><i class="bi bi-shield-exclamation"></i> ${Object.values(r).filter(Boolean).length}/4 Gates Completed</span>`;
        inspect(`
          <div style="display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap;margin-bottom:8px;">
            <div>
              <span class="eyebrow">INDEPENDENT EVIDENCE REVIEW</span>
              <h2 style="margin:4px 0 6px;">${esc(p.title||id)}</h2>
              <code>${esc(id)}</code> · <span class="badge">${esc(p.district||'')}</span> · <span class="badge">${esc(p.category||'')}</span>
            </div>
            <div>${statusBadge}</div>
          </div>
          ${gatesHtml}
          <h3>Attached project evidence</h3>
          ${evidenceHtml}
          ${decisionsHtml}
          <p class="note" style="margin-top:12px;"><i class="bi bi-shield-lock"></i> Evidence review and programme decisions retain their independent permissions and audit trails.</p>
          ${role==='auditor'?`<div class="actions">${button('audit-case','Record an audit finding',id)}</div>`:''}
        `);
        return true;
      }
      if(action==='audit-case'){
        inspect(`<h2>Record an audit finding</h2><p>Use the independent audit case register for this project.</p><form data-form="audit-case" data-id="${esc(id)}">${field('title','Finding title')}${field('reason','Evidence and rationale','','textarea')}<button class="primary">Create audit case</button></form>`);return true;
      }
      if(action==='evidence-file'){
        const [rid,sha]=id.split('/'),response=await api('/api/v2/pilot/requests/'+rid+'/evidence/'+sha,'BLOB');
        const url=URL.createObjectURL(response),a=document.createElement('a');a.href=url;a.download='evidence-'+sha.slice(0,12);a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);return true;
      }
      if(action==='draft'){
        const detail = await api('/api/v2/analyst/projects/'+encodeURIComponent(id));
        const p = (planningPlan?.items||[]).find(x=>x.project_id===id) || detail;
        const isSelected = p?.selected ?? false;
        const decision = (detail.decisions||[]).find(x=>x.decision_id===(p?.decision_id||detail?.decision_id)) || (detail.decisions||[])[0];

        if(!isSelected && !decision){
          inspect(`
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;">
              <div>
                <div style="font-weight:700;font-size:11px;letter-spacing:1px;color:#c05621;margin-bottom:4px;">EXECUTIVE DECISION WORKFLOW</div>
                <h2 style="margin:0 0 6px 0;">${esc(p.title || detail.category + ' in ' + detail.district)}</h2>
                <div>
                  <code>${esc(id)}</code> &middot; ${esc(detail.district)} &middot; ${esc(detail.ward || 'Ward unspecified')} &middot; <span class="badge">${esc(detail.category)}</span>
                </div>
              </div>
              <span class="badge" style="background:#feebc8;color:#7b341e;border:1px solid #fbd38d;font-size:12px;padding:4px 10px;">Outside Current Allocation</span>
            </div>

            <div class="note" style="background:#fffaf0;border-left:4px solid #dd6b20;padding:14px 18px;margin:16px 0;border-radius:4px;">
              <strong style="color:#9c4221;font-size:14px;"><i class="bi bi-exclamation-triangle-fill"></i> Cannot draft decision: Project is outside the current district planning envelope</strong>
              <p style="margin:8px 0 0 0;font-size:13px;color:#4a5568;line-height:1.5;">
                Under public finance rules, an administrative officer can only prepare expenditure sanction drafts for proposals prioritized and selected within the active budget scenario.<br>
                Currently, this project is ranked below the funding cutoff for the active scenario parameters
                (Allocated: <strong>${planningPlan?.allocation?.selected_count ?? 0} projects</strong>, Budget used: <strong>₹${planningPlan?.allocation?.budget_used_lakh ?? 0} lakh</strong>).
              </p>
            </div>

            <div style="padding:16px;background:var(--bg-light,#f8f9fa);border-radius:8px;border:1px solid var(--line,#e2e8f0);">
              <h4 style="margin:0 0 8px 0;font-size:13px;font-weight:600;color:var(--ink);">How to include this project for decision preparation:</h4>
              <ul style="margin:0;padding-left:20px;font-size:13px;color:var(--muted);line-height:1.6;">
                <li>Increase <strong>Capital budget</strong> in the scenario form above (Estimated capital required: ₹${esc(p.estimated_cost_lakh || 'N/A')} lakh).</li>
                <li>Increase <strong>Projects per district</strong> or <strong>Project capacity</strong> in the scenario form.</li>
                <li>Adjust priority weights to boost <strong>${esc(detail.category)}</strong> or demand-density scores.</li>
              </ul>
            </div>
          `);
          return true;
        }

        if(decision){
          const rev = decision.review || decision.source?.engineering_review;
          const isApproved = decision.status === 'approved' || decision.status === 'funded';
          inspect(`
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;">
              <div>
                <div style="font-weight:700;font-size:11px;letter-spacing:1px;color:var(--primary);margin-bottom:4px;">EXECUTIVE DECISION DOSSIER</div>
                <h2 style="margin:0 0 6px 0;">${esc(p.title || detail.category + ' in ' + detail.district)}</h2>
                <div>
                  <code>${esc(decision.decision_id)}</code> &middot; ${esc(detail.district)} &middot; ${esc(detail.ward || 'Ward unspecified')} &middot; <span class="badge">${esc(detail.category)}</span>
                </div>
              </div>
              <span class="badge ${isApproved ? 'badge-ready' : 'badge-progress'}" style="font-size:12px;padding:4px 10px;background:${isApproved?'#e6f4ea':'#e8f0fe'};color:${isApproved?'#137333':'#1a73e8'};">
                <i class="bi ${isApproved ? 'bi-patch-check-fill' : 'bi-file-earmark-text'}"></i> ${isApproved ? 'Approved Decision' : 'Draft Decision Recorded'}
              </span>
            </div>

            <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0;">
              <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
                <div style="font-size:11px;font-weight:600;color:var(--muted);">1. DEMAND EVIDENCE</div>
                <div style="font-size:14px;font-weight:700;color:#137333;margin-top:4px;"><i class="bi bi-check-circle-fill"></i> Reports linked</div>
                <div style="font-size:12px;color:var(--muted);">${num(detail.total_requests)} citizen reports linked</div>
              </div>
              <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
                <div style="font-size:11px;font-weight:600;color:var(--muted);">2. PROPOSAL DRAFT</div>
                <div style="font-size:14px;font-weight:700;color:#137333;margin-top:4px;"><i class="bi bi-check-circle-fill"></i> Recorded</div>
                <div style="font-size:12px;color:var(--muted);">Drafted by Analyst</div>
              </div>
              <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
                <div style="font-size:11px;font-weight:600;color:var(--muted);">3. ENGINEERING REVIEW</div>
                <div style="font-size:14px;font-weight:700;color:${rev?'#137333':'#b06000'};margin-top:4px;">
                  <i class="bi ${rev?'bi-check-circle-fill':'bi-dash-circle'}"></i> ${rev?'Review recorded':'Survey Pending'}
                </div>
                <div style="font-size:12px;color:var(--muted);">${rev?`₹${esc(rev.cost_lakh)} lakh · ${num(rev.beneficiary_count)} persons`:'Awaiting field survey'}</div>
              </div>
              <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
                <div style="font-size:11px;font-weight:600;color:var(--muted);">4. GOVERNANCE SANCTION</div>
                <div style="font-size:14px;font-weight:700;color:${isApproved?'#137333':'#b06000'};margin-top:4px;">
                  <i class="bi ${isApproved?'bi-check-circle-fill':'bi-dash-circle'}"></i> ${isApproved?'Approved in NVB':'Pending Approval'}
                </div>
                <div style="font-size:12px;color:var(--muted);">${isApproved?esc(decision.approved_at||'Sanctioned'):'Awaiting admin sign-off'}</div>
              </div>
            </div>

            <div style="padding:14px 16px;border:1px solid var(--line);border-radius:8px;margin-bottom:16px;background:#fff;">
              <h4 style="margin:0 0 6px 0;font-size:13px;font-weight:600;">Recorded Review Rationale</h4>
              <p style="margin:0;font-size:13px;color:var(--ink);">${esc(decision.notes || 'No review notes provided.')}</p>
              <p style="margin:8px 0 0 0;font-size:12px;color:var(--muted);">Cost basis: ₹${esc(p.estimated_cost_lakh)} lakh (${esc(p.cost_basis)})</p>
            </div>

            ${!isApproved && role === 'admin' ? `
              <div style="border-top:1px solid var(--line);padding-top:16px;margin-top:16px;">
                <h3 style="margin:0 0 12px 0;font-size:15px;">Administrative Actions</h3>
                ${!rev ? `
                  <form data-form="engineering" data-id="${esc(decision.decision_id)}" class="form">
                    <label>Reviewed Engineering Cost (₹ lakh)<input name="engineering_cost_lakh" type="number" min="0.01" step="any" value="${p.estimated_cost_lakh}" required></label>
                    <label>Surveyed Beneficiary Count<input name="beneficiary_count" type="number" min="1" value="" placeholder="Enter the surveyed project count" required></label>
                    <label class="full">Engineering Survey &amp; Evidence Reference<input name="evidence_reference" required placeholder="urn:nvb:survey:... or https://..."></label>
                    <button class="primary">Record Engineering Review</button>
                  </form>
                ` : `
                  <p style="color:#137333;font-weight:600;font-size:13px;"><i class="bi bi-check2-all"></i> Engineering review completed: ₹${esc(rev.cost_lakh)} lakh for ${num(rev.beneficiary_count)} surveyed beneficiaries.</p>
                  <button class="primary" data-action="approve" data-id="${esc(decision.decision_id)}">Approve Decision &amp; Authorize Delivery</button>
                `}
              </div>
            ` : !isApproved ? `
              <div class="note" style="margin-top:12px;">
                <strong>Next governance stage:</strong> Engineering survey sign-off and administrative sanction require Programme Administrator role.
                ${window.NVBPilotConfig?.demo ? `<button type="button" data-action="switch-admin" style="margin-left:8px;font-size:12px;padding:4px 8px;">Switch to Administrator</button>` : ''}
              </div>
            ` : ''}
          `);
          return true;
        }

        // If not drafted yet, but selected in scenario:
        inspect(`
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:1rem;">
            <div>
              <div style="font-weight:700;font-size:11px;letter-spacing:1px;color:var(--primary);margin-bottom:4px;">PREPARE POLICY DECISION</div>
              <h2 style="margin:0 0 6px 0;">${esc(p.title || detail.category + ' in ' + detail.district)}</h2>
              <div>
                <code>${esc(id)}</code> &middot; ${esc(detail.district)} &middot; ${esc(detail.ward || 'Ward unspecified')} &middot; <span class="badge">${esc(detail.category)}</span>
              </div>
            </div>
            <span class="badge" style="background:#e8f0fe;color:#1a73e8;border:1px solid #c2e7ff;font-size:12px;padding:4px 10px;">
              <i class="bi bi-check2-circle"></i> Selected in Planning Scenario
            </span>
          </div>

          <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:12px;margin:16px 0;">
            <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
              <div style="font-size:11px;font-weight:600;color:var(--muted);">CITIZEN EVIDENCE</div>
              <div style="font-size:18px;font-weight:700;color:var(--ink);">${num(detail.total_requests)} Reports</div>
              <div style="font-size:12px;color:var(--muted);">Linked reports; field validation required</div>
            </div>
            <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
              <div style="font-size:11px;font-weight:600;color:var(--muted);">PRIORITY SCORE</div>
              <div style="font-size:18px;font-weight:700;color:var(--primary);">${esc(p.priority_score)} / 100</div>
              <div style="font-size:12px;color:var(--muted);">Screening score; inspect assumptions</div>
            </div>
            <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
              <div style="font-size:11px;font-weight:600;color:var(--muted);">ESTIMATED CAPITAL</div>
              <div style="font-size:18px;font-weight:700;color:var(--ink);">₹${esc(p.estimated_cost_lakh)} lakh</div>
              <div style="font-size:12px;color:var(--muted);">${esc(p.cost_basis)}</div>
            </div>
            <div style="padding:12px;border:1px solid var(--line);border-radius:8px;background:var(--bg-light);">
              <div style="font-size:11px;font-weight:600;color:var(--muted);"><i class="bi bi-shield-check" style="color:#137333;"></i> SURVEYED PROJECT BENEFICIARIES</div>
              <div style="font-size:18px;font-weight:700;color:var(--ink);">${detail.beneficiaries ? num(detail.beneficiaries) + ' Citizens' : 'Catchment survey required'}</div>
              <div style="font-size:12px;color:var(--muted);">${esc(detail.catchment_status || 'Catchment survey required')}</div>
            </div>
          </div>

          <form data-form="create-draft" data-id="${esc(id)}" class="form" style="margin-top:16px;">
            <label class="full" style="font-weight:600;">
              Review Rationale &amp; Expenditure Justification (minimum 10 characters):
              <textarea name="notes" required minlength="10" rows="3" style="margin-top:6px;width:100%;">District development proposal prepared for authorised review based on linked citizen reports; field and source verification remain required.</textarea>
            </label>
            <div class="actions" style="margin-top:12px;">
              <button class="primary" type="submit">Save Decision Draft for Human Review</button>
            </div>
          </form>
          <p class="note" style="margin-top:12px;">Saving a draft records an accountable proposal in the governance ledger. Administrative engineering review and approval remain separate steps.</p>
        `);
        return true;
      }
      return false;
    },
    async submit(form,values,ctx){
      const {api,d}=ctx,kind=form.dataset.form;let data;
      if(kind==='create-draft'){
        const res=await api('/api/v2/analyst/projects/'+form.dataset.id+'/draft','POST',{
          ...options(d),
          notes:values.notes
        });
        if(ctx.message) ctx.message('Draft '+res.decision.decision_id+' saved.');
        if(ctx.refresh) await ctx.refresh();
        await PilotWorkspace.action('draft', form.dataset.id, ctx);
        return 'inspected';
      }
      if(kind==='site-intelligence'){
        const out=await api(apiBase+'projects/'+form.dataset.id+'/intelligence','POST',{...values,lat:Number(values.lat),lng:Number(values.lng),radius_m:Number(values.radius_m)});
        ctx.inspect(`<h2>Site evidence</h2><p>${esc(out.notice)}</p>${table(['Observation','Value'],Object.entries(out.result).filter(([k])=>k!=='provenance').map(([k,v])=>[esc(k.replaceAll('_',' ')),esc(typeof v==='object'?JSON.stringify(v):v)]))}`);return 'inspected';
      }
      if(kind==='setup-ai')data={translation_provider:values.translation_provider};
      if(kind==='scenario'){planOptions=Object.fromEntries(Object.entries(values).map(([k,v])=>[k,Number(v)]));return true;}
      if(kind==='setup-core')data={...values,languages:new FormData(form).getAll('language'),categories:new FormData(form).getAll('category'),monthly_report_capacity:Number(values.monthly_report_capacity),monthly_cloud_budget_inr:Number(values.monthly_cloud_budget_inr),routing:{Vellore:values.vellore,Tirupati:values.tirupati}};
      if(kind==='setup-group'){
        const key=form.dataset.group,fields=setup.groups.find(x=>x[0]===key)[2],value={};
        fields.forEach(([name,,type])=>value[name]=type==='boolean'?form.elements[name].checked:['number','fraction'].includes(type)?Number(values[name]):values[name]);
        data={portal:{[key]:value}};
      }
      if(kind==='setup-channel')data={portal:{channels:{[form.dataset.channel]:{enabled:form.elements.enabled.checked,address:values.address||'',owner:values.owner}}}};
      if(kind==='setup-integration')data={portal:{integrations:{[form.dataset.integration]:values}}};
      if(data){await api('/api/v2/pilot/settings','POST',{...data,version:d.programme.version});return true;}
      if(kind==='delivery'){await api(apiBase+'delivery/'+form.dataset.id,'POST',{...values,version:Number(form.dataset.version),expenditure_lakh:Number(values.expenditure_lakh)});return true;}
      if(kind==='audit-case'){await api('/api/v2/auditor/cases','POST',{project_id:form.dataset.id,title:values.title,notes:values.reason,kind:'discrepancy',severity:'MEDIUM',data_mode:window.NVBPilotConfig.demo?'synthetic':'operational'});return true;}
      return false;
    }
  };
})();
