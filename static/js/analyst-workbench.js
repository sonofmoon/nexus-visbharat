/* One scope and snapshot for the Analyst decision workflow. */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = value => value == null ? 'Not available' : Number(value).toLocaleString(undefined,{maximumFractionDigits:2});
  let payload = null, activeTab = 'demand', generation = 0, controller, loading, projectOffset = 0, dirty = false, searchTimer;
  const extraScope = {date_from:'filterDateFrom',date_to:'filterDateTo',language:'filterLanguage',channel:'filterChannel'};
  const originalParams = buildFilterParams;
  buildFilterParams = function() {
    const params = originalParams();
    Object.entries(extraScope).forEach(([key,id]) => { if ($(id)?.value) params.set(key,$(id).value); });
    return params;
  };
  function opts() {
    return {...Object.fromEntries(buildFilterParams()),budget_lakh:Number($('wbBudget').value),capacity:Number($('wbCapacity').value),
      max_per_district:Number($('wbDistrictCapacity').value),operating_budget_lakh:Number($('wbOperatingBudget').value),
      equity_share:Number($('wbEquityShare').value)/100,cost_case:$('wbCostCase').value,
      weights:{demand:Number($('wbDemandWeight').value),equity:Number($('wbEquityWeight').value),gap:Number($('wbGapWeight').value)},limit:50,offset:projectOffset,search:$('wbProjectSearch').value.trim()};
  }
  function params(options) {
    const p = new URLSearchParams();
    Object.entries(options).forEach(([k,v])=>{if(v!=='' && v!=null)p.set(k,typeof v==='object'?JSON.stringify(v):String(v));});return p;
  }
  async function api(path, options={}) {
    const token = getActiveToken();
    if (!token && !window.NVB_PILOT_ONLY) throw new Error('An authorized workspace token is required.');
    const r = await fetch(path,{...options,headers:{Authorization:`Bearer ${token}`,'Content-Type':'application/json',...options.headers}});
    const d=await r.json(); if(!r.ok || d.success===false)throw new Error(d.error||`Request failed (${r.status})`); return d;
  }
  function error(err) {
    if(err.name==='AbortError')return;
    $('wbError').hidden=false;$('wbError').textContent=err.message;
  }
  function table(headers, rows) {
    return `<div class="wb-table-wrap"><table class="wb-table"><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.map(row=>`<tr>${row.map(v=>`<td>${v}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
  }
  function metric(label,value,unit='') { return `<div class="wb-metric"><span>${esc(label)}</span><strong>${num(value)}</strong><small>${esc(unit)}</small></div>`; }
  const sourceDescriptions = {
    secc:'Household deprivation indicators provide context for reviewing underserved areas.',
    census:'Population and rural/urban composition put citizen report volumes in context.',
    nfhs:'Health and household-service indicators provide context for health and sanitation proposals.',
    sdg_india_index:'Development-goal indicators provide context for water, health and economic-access gaps.',
    niti_mpi:'Poverty indicators provide context for reviewing equity in project priorities.',
    aspirational_districts:'Recorded programme membership and priority sectors provide planning context.',
    pm_gati_shakti:'Recorded road, logistics and electricity indicators provide infrastructure context.',
    budget_outlays:'Recorded budget outlays provide planning context; they are not project commitments or actual expenditure.'
  };
  const sourceFields = {
    district:['District'],state:['State'],population:['Population','number'],rural_population:['Rural population','number'],urban_population:['Urban population','number'],
    literacy_rate:['Literacy (%)','number'],deprivation_index:['Deprivation index (0–1)','number'],households_deprived:['Deprived households','number'],
    sc_st_households_pct:['SC/ST households (%)','number'],female_headed_households_pct:['Female-headed households (%)','number'],
    stunting_rate:['Stunting (%)','number'],anemia_rate:['Anaemia (%)','number'],full_immunization_pct:['Full immunisation (%)','number'],clean_cooking_fuel_pct:['Clean cooking fuel (%)','number'],
    mpi_score:['MPI (0–1)','number'],poverty_headcount_pct:['People in multidimensional poverty (%)','number'],poverty_intensity_pct:['Poverty intensity (%)','number'],
    sdg_index_score:['SDG index (0–100)','number'],clean_water_sdg6:['Clean water: SDG 6 (0–100)','number'],good_health_sdg3:['Health: SDG 3 (0–100)','number'],decent_work_sdg8:['Decent work: SDG 8 (0–100)','number'],
    is_aspirational:['Programme membership','membership'],delta_rank:['Delta rank','number'],priority_sector:['Priority sector'],
    road_coverage:['Road coverage (%)','number'],logistics_park_access:['Logistics park access','access'],grid_power_coverage:['Grid power coverage (%)','number'],
    total_outlay_lakh:['Total outlay (₹ lakh)','number'],water_outlay_lakh:['Water (₹ lakh)','number'],road_outlay_lakh:['Roads (₹ lakh)','number'],education_outlay_lakh:['Education (₹ lakh)','number']
  };
  function sourceValue(key,value) {
    if(value==null || String(value).trim()==='')return '<span class="wb-muted">Not supplied</span>';
    const format=sourceFields[key]?.[1];
    if(format==='number' && Number.isFinite(Number(value)))return num(value);
    if(['membership','access'].includes(format)) {
      if(['1','true'].includes(String(value).toLowerCase()))return format==='membership'?'Listed':'Recorded';
      if(['0','false'].includes(String(value).toLowerCase()))return format==='membership'?'Not listed':'Not recorded';
    }
    return esc(typeof value==='object'?JSON.stringify(value):value);
  }
  function sourceCards(data) {
    const overview=`<div class="wb-evidence-note"><strong>${num(data.loaded_sources)} reference datasets loaded</strong><p>${num(data.verified_sources)} independently verified sources. “Verification pending” means the local values have not been checked against the publisher’s original document. A publisher name or file checksum alone does not establish that verification.</p></div>`;
    return overview+data.sources.map(s=>{
      const rows=Array.isArray(s.sample)?s.sample:[];
      const keys=[...new Set(rows.flatMap(r=>Object.keys(r)))].filter(k=>!['source_publisher','source'].includes(k));
      const fields=['district','state',...keys.filter(k=>!['district','state'].includes(k))].filter(k=>keys.includes(k));
      const status=s.status?String(s.status).replaceAll('_',' '):(!s.loaded?'Unavailable':'Verification pending');
      const preview=rows.length?table(fields.map(k=>sourceFields[k]?.[0]||k.replaceAll('_',' ').replace(/^./,c=>c.toUpperCase())),rows.map(r=>fields.map(k=>sourceValue(k,r[k])))):'<p>No source records match the selected state and district.</p>';
      const href=/^https?:\/\//.test(s.url||'')?s.url:null;
      return `<details class="wb-card wb-source-card" data-evidence-source="${esc(s.source)}"><summary><span class="wb-source-heading">${esc(s.name)}</span><span class="wb-source-status">${status}</span><span class="wb-source-byline">${esc(s.publisher||'Publisher not documented')} · ${num(s.scoped_records)} matching source records in scope</span></summary><p>${esc(sourceDescriptions[s.source]||'Reference indicators for reviewing local proposals.')}</p><p class="wb-source-context">Observation dates: ${s.observation_year?esc(s.observation_year)+' stated in the reference title; district observation dates are not documented in the file.':'Not documented in the loaded file. A year in the title identifies its stated edition, not a verified observation period.'}</p><h5>District indicators</h5><p class="wb-muted">Showing ${rows.length} of ${num(s.scoped_records)} matching records${s.scoped_records>rows.length?'. Select a district in the filters to inspect its values': ''}.</p>${preview}${href?`<p><a href="${esc(href)}" target="_blank" rel="noopener">Open publisher reference ↗</a> <span class="wb-muted">— background link; not proof of this file’s values</span></p>`:'<p>Publisher reference link not supplied.</p>'}<details class="wb-source-technical"><summary>Verification and technical details</summary><p>Resource identifier: <code>${esc(s.resource_id||s.source)}</code>. Retrieved: ${esc(s.retrieved_at||'Not recorded')}. API-reported update: ${esc(s.source_updated_at||'Not recorded')}.</p><p>Publisher verification: ${s.publisher_verified?'Recorded':'Pending — no matching publisher document/table and completed review are recorded.'}</p><p>Geography: ${esc(s.boundary_status)}.</p><p>Reuse: ${esc(s.license_status)}.</p><p>Retained content checksum (SHA-256): <code>${esc(s.sha256||'No retained content')}</code></p><p class="wb-muted">This checksum identifies retained content. It does not establish publisher authenticity, current geographic compatibility or accuracy.</p><details><summary>Raw data preview (JSON)</summary><pre>${esc(JSON.stringify(rows,null,2))}</pre></details></details></details>`;
    }).join('');
  }
  function card(p) {
    return `<article class="wb-card"><span class="wb-tag">#${p.rank} · ${p.committed?'Already committed':p.selected?'Selected in draft scenario':'Not selected'}</span><span class="wb-tag">${esc(p.review_status)}</span><h5>${esc(p.title)}</h5><p>${esc(p.district)}, ${esc(p.state)} · ${esc(p.ward||'Ward unspecified')} · ${esc(p.category)}</p><p>${num(p.reports)} reports · ${num(p.issues)} issue assignments · Screening score ${num(p.priority_score)}/100</p><p>${p.engineering_review?'Reviewed':'Illustrative'} capital ₹${num(p.cost_low_lakh)}–${num(p.cost_high_lakh)} lakh · Annual operation ₹${num(p.annual_operating_cost_lakh)} lakh (assumed)</p><p>Beneficiaries: ${p.beneficiaries==null?'survey required':num(p.beneficiaries)+' (human-reviewed project count; overlaps not deducted)'}. ${esc(p.outcome_measure)}: baseline required.</p><details><summary>Why this score?</summary>${table(['Component','Indicator (0–100)','Contribution'],Object.keys(p.components).map(k=>[esc(k),num(p.components[k]),num(p.contributions[k])]))}<p>Missing: ${esc(p.missing_components.join(', ')||'None')} · Available weights are renormalized. Unverified reference inputs make this a screening result.</p></details><button type="button" class="btn btn-sm btn-outline-primary" data-project="${esc(p.project_id)}">Inspect requests and evidence</button></article>`;
  }
  function renderProjects() {
    if(!payload)return;
    const p=payload.scenario,list=p.items;
    $('wbProjects').innerHTML=`<p>Showing ${list.length?num(p.offset+1):0}–${num(p.offset+list.length)} of ${num(p.matching_candidates)} matching projects (${num(p.total_candidates)} planning bundles in scope). Search covers every project; pagination does not change allocation.</p>`+(list.map(card).join('')||'<p>No projects match this scope.</p>');
    $('wbMoreProjects').disabled=p.offset+list.length>=p.matching_candidates;
    $('wbPreviousProjects').disabled=p.offset===0;
  }
  function renderSnapshot(d) {
    const s=d.stats, p=d.scenario;
    const scope=Object.entries(p.scope).filter(([,v])=>v).map(([k,v])=>`${k.replace('_',' ')}: ${v}`).join(' · ');
    $('wbScope').textContent=scope||'All pilot records · all intake dates';
    $('analystFreshnessBar').textContent=`${s.metadata.data_mode} data · Latest submission: ${s.metadata.as_of||'none'} · Calculated ${new Date(s.metadata.calculated_at).toLocaleTimeString()} · ${p.metadata.version}`;
    $('wbDemandMetrics').innerHTML=metric('Reports',s.total_complaints)+metric('Issue assignments',s.distinct_issues)+metric('Emergency reports',s.emergency_count,'Immediate response; excluded from capital selection')+metric('Operational closure',s.resolution_rate,'% of current scoped requests');
    $('wbInclusion').innerHTML=`<p>${num(d.inclusion.access_investigation_count)} districts warrant an access investigation. Reporting propensity is unavailable; no hidden demand is invented.</p>`+table(['District','Reports','Reports /100k','Deprivation (0–1)','Access review'],d.inclusion.items.map(r=>[esc(r.district),num(r.requests),num(r.reports_per_100k),num(r.deprivation_index),r.investigate_access?'Survey recommended':'No flag from this rule']))+`<p>${esc(d.inclusion.outreach_status)}</p>`;
    renderProjects();
    const a=p.allocation;
    $('wbBudgetResults').innerHTML=`<p>${esc(p.formula)}</p><p>Effective weights: ${Object.entries(p.weights).map(([k,v])=>`${esc(k)} ${num(v*100)}%`).join(' · ')}</p><div class="wb-metrics">${metric('Candidate projects',p.total_candidates)}${metric('Selected',a.selected_count)}${metric('Capital used',a.budget_used_lakh,'₹ lakh; scenario only')}${metric('Existing commitments',a.existing_commitments_lakh,'₹ lakh; known project decisions')}${metric('Capital remaining',a.budget_remaining_lakh,'₹ lakh')}${metric('Recurring operation',a.annual_operating_cost_lakh,'₹ lakh/year; assumed')}${metric('Ranks changed',p.delta.ranks_changed)}</div><p>${p.delta.newly_selected.length} newly selected · ${p.delta.removed.length} removed · capital change ₹${num(p.delta.budget_used_lakh)} lakh · linked-report change ${num(p.delta.reports_linked)}.</p><p>${esc(p.outcomes.status)}. Monetary NPV and IRR are not estimated.</p><p>Equity reservation shortfall: ₹${num(a.equity_reservation_shortfall_lakh)} lakh. Unused reservation is released to other eligible candidates.</p>${table(['Cost case','Selected','Capital (₹ lakh)','Reports linked'],Object.entries(p.sensitivity).map(([k,v])=>[esc(k),num(v.selected_count),num(v.budget_used_lakh),num(v.reports_linked)]))}<p class="wb-muted">${esc(a.method)} Scenario ID: ${esc(p.scenario_id)}. Preview limit does not change allocation.</p><p>${esc(p.limitations.join(' '))}</p>${p.selected_projects.map(card).join('')}`;
    [['totalComplaints',s.total_complaints],['districtCount',s.districts_covered],['languageCount',s.languages_supported],['stateCount',s.states_covered],['resolutionRate',s.resolution_rate]].forEach(([id,v])=>animateNumber(id,v,id==='resolutionRate'?'%':''));
    const overview=p.top_projects.map(x=>({...x,project_title:x.title+' — '+x.district, social_priority_score:x.priority_score,sub_indices:x.components,policy_recommendation:{estimated_cost:`INR ${x.cost_low_lakh}–${x.cost_high_lakh} lakh; see cost basis`}}));
    $('projectsList').innerHTML=overview.map((x,i)=>window.NVBConsole.renderProjectCard(x,i,new Set())).join('')+window.NVBConsole.projectFooter(overview.length);
    window.NVBConsole.setSnapshot?.({stats:{totalComplaints:s.total_complaints,districtCount:s.districts_covered,languageCount:s.languages_supported,stateCount:s.states_covered,resolutionRate:s.resolution_rate,dailyTrend:s.daily_trend},projects:overview});
    renderCharts();
    $('predictionContent').innerHTML='<p>Screening uses observed report density, deprivation and service coverage. No next-quarter forecast is asserted.</p>'+d.inclusion.items.filter(r=>r.requests).slice(0,6).map(r=>`<p><strong>${esc(r.district)}</strong>: ${num(r.requests)} reports; ${num(r.reports_per_100k)} per 100,000 district residents. Reference population requires verification.</p>`).join('');
    $('projectsRefreshedAt').textContent='Snapshot '+new Date(s.metadata.calculated_at).toLocaleTimeString();
  }
  function renderCharts() {
    if(!payload || !window.Chart)return;
    const s=payload.stats, all=Object.entries(s.daily_trend), period=typeof selectedTrendPeriod==='undefined'?'14':selectedTrendPeriod;
    const trend=period==='all'?all:all.slice(-Number(period||14));
    const definitions=[['trendChart',Object.fromEntries(trend),'line'],['categoryChart',s.categories,'doughnut'],['urgencyChart',s.urgencies,'bar'],['channelChart',s.channels,'bar']];
    const colors=['#1a73e8','#34a853','#fbbc04','#ea4335','#9333ea','#0891b2','#ea580c','#64748b','#db2777','#059669'];
    definitions.forEach(([id,data,type])=>{
      const node=$(id);if(!node)return;
      Chart.getChart(node)?.destroy();
      new Chart(node,{type,data:{labels:Object.keys(data),datasets:[{label:'Requests',data:Object.values(data),backgroundColor:colors,borderColor:type==='line'?'#1a73e8':undefined,tension:.2}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:type==='doughnut'}},scales:type==='doughnut'?undefined:{y:{beginAtZero:true}}}});
    });
    if($('ga4CatTotal'))$('ga4CatTotal').textContent=num(s.total_complaints);
  }
  async function refresh(resetProjects=true) {
    if(resetProjects)projectOffset=0;
    const n=++generation;controller?.abort();controller=new AbortController();
    $('wbError').hidden=true;$('wbScenarioStatus').textContent='Calculating scoped scenario…';
    loading=api('/api/v2/analyst/snapshot?'+params(opts()),{signal:controller.signal});
    try{
      const d=await loading;if(n!==generation)return;
      payload=d;dirty=false;renderSnapshot(d);$('wbScenarioStatus').textContent='Scenario calculated. Cost basis is shown per project; approval is a separate action.';
      if(['evidence','delivery','outcomes'].includes(activeTab))await loadTab(activeTab,n);
    }catch(err){if(n===generation){error(err);$('wbScenarioStatus').textContent='Calculation unavailable; previous results are stale.';}}
  }
  async function loadTab(tab,n=generation) {
    const qs=buildFilterParams();
    try{
      if(tab==='evidence') {const d=await api('/api/v2/analyst/evidence?'+qs);if(n===generation)$('wbEvidence').innerHTML=sourceCards(d);}
      if(tab==='delivery') {
        const d=await api('/api/v2/analyst/delivery?'+qs);if(n!==generation)return;
        $('wbDelivery').innerHTML=table(['District','SLA on time / eligible','Coverage','Ack median /p90 (hours)','Closure','Open age p90 (days)','Citizen feedback'],d.items.map(r=>[esc(r.district),`${r.on_time}/${r.eligible}`,num(r.coverage_pct)+'%',`${num(r.median_ack_hours)} / ${num(r.p90_ack_hours)}`,`${r.closed}/${r.requests}`,num(r.p90_open_age_days),`${r.positive_feedback}/${r.feedback} positive`]))+'<p>No composite governance score is assigned. Response time, capital delivery and service outcomes are different measures.</p><h5>Linked decisions</h5>'+table(['Decision','Project','Status','Estimated capital','Review'],d.decisions.map(r=>[esc(r.decision_id),esc(r.project_id||'Legacy district decision'),esc(r.status),'₹'+num(r.estimated_project_cost_lakh)+' lakh',r.review?esc(r.review.evidence_reference):'Engineering review pending']));
      }
      if(tab==='outcomes') {
        const d=await api('/api/v2/analyst/readiness');if(n!==generation)return;
        const q=d.reports.quality,overall=q.overall||{},load=d.reports.load;
        $('wbReadiness').innerHTML='<p>Apache-2.0 · No DPG certification asserted</p>'+
          '<h5>Multilingual classification</h5><p>Provisional labels; independent human review pending. These samples are separate from the demonstration dataset.</p>'+
          '<div class="wb-metrics">'+metric('Evaluation samples',overall.samples)+metric('Category macro-F1',overall.category_macro_f1)+metric('Emergency recall',overall.emergency_recall,'On '+(overall.emergency_samples||0)+' proposed emergency cases')+metric('Provider fallbacks',overall.fallback_count)+'</div>'+
          table(['Language','Samples','Category macro-F1','Urgency accuracy'],Object.entries(q.by_language||{}).map(([lang,r])=>[esc(lang),num(r.samples),num(r.category_macro_f1),num(r.urgency_accuracy)]))+
          '<h5>Local analytics load</h5><p>SQLite copy; excludes network and live model latency. Small samples do not establish national capacity.</p>'+
          table(['Rows','Concurrency','p50 / p95 / p99 (ms)','Requests / second'],(load.results||[]).map(r=>[num(r.rows),num(r.concurrency),[r.p50_ms,r.p95_ms,r.p99_ms].map(num).join(' / '),num(r.throughput_per_second)]))+
          '<p>Translation fidelity, noisy audio, location accuracy and duplicate-cluster quality still need independent annotations. Cloud cost and queue age are unmeasured.</p><details><summary>Full evaluation records and limitations</summary><pre>'+esc(JSON.stringify(d.reports,null,2))+'</pre></details>';
      }
    }catch(err){error(err);}
  }
  function selectTab(tab) {
    activeTab=tab;
    document.querySelectorAll('[data-workbench-tab]').forEach(b=>{const active=b.dataset.workbenchTab===tab;b.setAttribute('aria-selected',String(active));b.tabIndex=active?0:-1;$('wb-'+b.dataset.workbenchTab).hidden=!active;});
    loadTab(tab);
  }
  async function detail(id) {
    const dialog=$('wbProjectDialog');dialog.showModal();$('wbProjectDetail').textContent='Loading linked evidence…';
    try{
      const d=await api('/api/v2/analyst/projects/'+encodeURIComponent(id)+'?'+buildFilterParams());
      const p=payload?.scenario.items.concat(payload.scenario.selected_projects).find(x=>x.project_id===id);
      $('wbProjectDetail').innerHTML=`<p><code>${esc(id)}</code> · ${esc(d.district)} · ${esc(d.ward)} · ${esc(d.category)}</p><p>${num(d.total_requests)} linked requests; ${num(d.excluded_emergency_request_ids.length)} emergency reports excluded from capital selection. Preview capped at ${d.request_preview_limit}. ${esc(d.catchment_status)}.</p>${p?`<p>${esc(p.cost_basis)}</p><p>Possible scheme routes (eligibility unverified): ${esc(p.schemes.join('; '))}</p><p>Alternatives: ${esc(p.alternatives.join('; '))}</p>`:''}<button type="button" class="btn btn-sm btn-outline-primary" data-outcome-project="${esc(id)}">Use this catchment for outcome monitoring</button>${p?.selected?`<form id="wbDraftForm"><label>Reason for proposing this project<textarea id="wbDraftNotes" class="form-control" minlength="10" required></textarea></label><button class="btn btn-primary" type="submit">Save draft for human review</button><p id="wbDraftStatus" role="status"></p></form>`:'<p>Select this project in a budget scenario before saving an approval draft.</p>'}<button type="button" class="btn btn-sm btn-outline-primary" data-export-project="${esc(id)}">Download portable evidence (JSON)</button><h5>Citizen requests</h5>${d.requests.map(r=>`<details class="wb-card"><summary>${esc(r.request_id)} · ${esc(r.urgency)} · ${esc(r.status)} · ${esc(r.input_language)} · ${esc(r.source_channel)}</summary><span class="wb-tag">${r.is_synthetic?'Synthetic example':'Operational record; verify evidence'}</span><blockquote lang="${esc(r.input_language)}">${esc(r.original_text)}</blockquote><p>${esc(r.translated_text)}</p><p>Cluster: <code>${esc(r.cluster_id||'Unassigned')}</code>. ${esc(r.cluster_basis)}</p><a href="/dashboard?demo_ticket=${encodeURIComponent(r.request_id)}">Open execution record</a></details>`).join('')}<h5>Lifecycle evidence</h5>${table(['Ticket','Stage','When','Reason'],d.events.map(e=>[esc(e.request_id),esc(e.to_status),esc(e.created_at),esc(e.reason)]))}<h5>Source records</h5>${sourceCards(d.evidence)}`;
      $('wbDraftForm')?.addEventListener('submit',async e=>{e.preventDefault();const status=$('wbDraftStatus');try{if(dirty)throw new Error('Recalculate the scenario before saving a draft.');const result=await api('/api/v2/analyst/projects/'+id+'/draft',{method:'POST',body:JSON.stringify({...opts(),notes:$('wbDraftNotes').value})});status.textContent=`${result.reused?'Existing':'Saved'} draft ${result.decision.decision_id}. Admin engineering review and approval are still required.`;}catch(err){status.textContent=err.message;}});
    }catch(err){$('wbProjectDetail').textContent=err.message;}
  }
  async function outcome(e) {
    e.preventDefault();const qs=buildFilterParams();qs.set('delivery_at',$('wbDeliveryDate').value);qs.set('days',$('wbWindowDays').value);
    if($('wbOutcomeProject').value)qs.set('project_id',$('wbOutcomeProject').value.trim());if($('wbOutcomeCluster').value)qs.set('cluster_id',$('wbOutcomeCluster').value.trim());
    try{const d=await api('/api/v2/analyst/outcomes?'+qs);$('wbOutcomeResult').innerHTML=`<div class="wb-warning"><strong>${esc(d.status.replaceAll('_',' '))}</strong><p>${esc(d.message||'Observed association only; not causal proof.')}</p></div>`+(d.counts?`<div class="wb-metrics">${metric('Before reports',d.counts.before)}${metric('Observed after reports',d.counts.after_observed)}${metric('Observed decrease',d.change_pct,'% — only for completed windows')}</div><p>${num(d.windows.observed_post_days)} of ${d.windows.days_each} follow-up days observed. Delivery date: ${esc(d.delivery_at)}. ${esc(d.delivery_verification)}</p><p>${esc(d.cautions.join(' '))}</p>`:'');}catch(err){$('wbOutcomeResult').textContent=err.message;}
  }
  function download(name,text,type) {const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([text],{type}));a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000);}
  async function exportBrief() {
    try{if(dirty)await refresh();const d=await api('/api/v2/analyst/brief',{method:'POST',body:JSON.stringify(opts())});const text=[d.title,d.status,'Generated: '+d.generated_at,'Scenario: '+d.scenario.scenario_id,'Scope: '+JSON.stringify(d.scenario.scope),'',...d.claims.map(c=>`${c.text}\nSource: ${c.source}`),'','Assumptions:',...d.assumptions,'','Source registry:',...d.sources.sources.map(s=>`${s.name}: ${s.status}; SHA-256 ${s.sha256}; ${s.url}`),'','Required review:',...d.evaluation_required].join('\n');download('NVB-decision-brief-'+d.scenario.scenario_id+'.txt',text,'text/plain;charset=utf-8');}catch(err){error(err);}
  }
  async function approvals() {
    const box=$('wbApprovals');box.textContent='Loading project drafts…';
    try{const d=await api('/api/v2/analyst/delivery?'+buildFilterParams());
      box.innerHTML=d.decisions.filter(r=>r.project_id).map(r=>`<article class="wb-card"><h5>${esc(r.decision_id)}</h5><p>${esc(r.district)} · ${esc(r.status)} · ${esc(r.project_id)}</p><p>${esc(r.notes)}</p>${r.status==='draft'?`<form data-review-decision="${esc(r.decision_id)}" class="wb-controls"><label>Engineering cost (₹ lakh)<input name="engineering_cost_lakh" type="number" min="0.01" step="0.01" value="${r.review?.cost_lakh??''}" required></label><label>Surveyed beneficiaries<input name="beneficiary_count" type="number" min="1" value="${r.review?.beneficiary_count??''}" required></label><label>Survey and cost evidence reference<input name="evidence_reference" minlength="10" value="${esc(r.review?.evidence_reference??'')}" required></label><button class="btn btn-outline-primary" type="submit">Record engineering review</button></form><button class="btn btn-success" type="button" data-approve-decision="${esc(r.decision_id)}" ${r.review?'':'disabled'}>Approve reviewed project</button>`:`<p>Approved at: ${esc(r.approved_at||'Not approved')}</p>`}<p role="status" data-decision-status="${esc(r.decision_id)}"></p></article>`).join('')||'<p>No project-linked decisions in this scope. Save a selected project draft from the Analyst workspace.</p>';
      box.querySelectorAll('[data-review-decision]').forEach(form=>form.addEventListener('submit',async e=>{e.preventDefault();const id=form.dataset.reviewDecision;try{await api('/api/v2/analyst/decisions/'+id+'/review',{method:'POST',body:JSON.stringify(Object.fromEntries(new FormData(form)))});await approvals();}catch(err){form.parentElement.querySelector('[role=status]').textContent=err.message;}}));
      box.querySelectorAll('[data-approve-decision]').forEach(button=>button.addEventListener('click',async()=>{const id=button.dataset.approveDecision;button.disabled=true;try{await api('/api/v1/policy/decisions/'+id+'/approve',{method:'POST',body:'{}'});await approvals();}catch(err){button.parentElement.querySelector('[role=status]').textContent=err.message;button.disabled=false;}}));
    }catch(err){box.textContent=err.message;}
  }
  window.NVBAnalyst={refresh,selectTab,getSnapshot:()=>payload};
  // Replace independent legacy fetches with one atomic Analyst snapshot.
  const legacyRefresh=refreshAll;
  refreshAll=function(){if(getActiveRole()==='analyst'){refresh();loadAiRuntimeStatus();loadComplaintFeed();loadHotspots();}else legacyRefresh();};
  const legacyStats=loadStats,legacyCharts=loadCharts,legacyProjects=loadPriorityProjects;
  loadStats=function(){if(getActiveRole()==='analyst')return loading;return legacyStats();};
  loadCharts=function(){if(getActiveRole()==='analyst')return renderCharts();return legacyCharts();};
  loadPriorityProjects=function(){if(getActiveRole()==='analyst')return loading;return legacyProjects();};
  loadPrediction=function(){ /* The canonical snapshot renders observed screening. */ };
  document.addEventListener('DOMContentLoaded',()=>{
    document.querySelectorAll('[data-workbench-tab]').forEach(b=>{b.addEventListener('click',()=>selectTab(b.dataset.workbenchTab));b.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const all=[...document.querySelectorAll('[data-workbench-tab]')],i=all.indexOf(b),next=e.key==='Home'?0:e.key==='End'?all.length-1:(i+(e.key==='ArrowRight'?1:-1)+all.length)%all.length;all[next].click();all[next].focus();});});
    Object.values(extraScope).forEach(id=>$(id).addEventListener('change',refreshAll));
    $('wbScenarioForm').addEventListener('submit',e=>{e.preventDefault();refresh();});
    $('wbScenarioForm').addEventListener('input',e=>{dirty=true;if(e.target.type==='range')e.target.nextElementSibling.textContent=Number(e.target.value).toFixed(2);$('wbScenarioStatus').textContent='Settings changed — compare scenarios to apply.';});
    $('wbProjectSearch').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>refresh(),350);});
    $('wbMoreProjects').addEventListener('click',()=>{projectOffset+=50;refresh(false);});
    $('wbPreviousProjects').addEventListener('click',()=>{projectOffset=Math.max(0,projectOffset-50);refresh(false);});
    $('wbCloseDialog').addEventListener('click',()=>$('wbProjectDialog').close());
    $('wbOutcomeForm').addEventListener('submit',outcome);$('wbExportBrief').addEventListener('click',exportBrief);
    document.querySelector('[data-subtab="admin-policy"]')?.addEventListener('click',approvals);$('wbRefreshApprovals')?.addEventListener('click',approvals);
    document.addEventListener('click',e=>{const b=e.target.closest('[data-project]');if(b)detail(b.dataset.project);const x=e.target.closest('[data-export-project]');if(x){api('/api/v2/analyst/projects/'+encodeURIComponent(x.dataset.exportProject)+'/export?'+params(opts())).then(d=>download(x.dataset.exportProject+'.json',JSON.stringify(d.record,null,2),'application/json')).catch(error);}const o=e.target.closest('[data-outcome-project]');if(o){$('wbOutcomeProject').value=o.dataset.outcomeProject;$('wbProjectDialog').close();selectTab('outcomes');}});
  });
})();
