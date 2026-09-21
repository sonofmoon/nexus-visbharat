/* Scoped Auditor workspace. Text is escaped; only explicit review actions mutate. */
(() => {
  'use strict';
  const API='/api/v2/auditor';
  const routeParams=new URLSearchParams(location.search);
  if(routeParams.get('workspace')==='auditor')sessionStorage.setItem('nvb_active_role','auditor');
  const $=id=>document.getElementById(id);
  const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const human=v=>String(v??'').replace(/_/g,' ');
  const num=v=>v===null||v===undefined?'Not available':Number(v).toLocaleString(undefined,{maximumFractionDigits:3});
  const percent=v=>v===null||v===undefined?'Not available':num(v*100)+'%';
  const interval=v=>Array.isArray(v)&&v.length===2?v.map(percent).join(' – '):'Not available';
  const badge=(v,tone='')=>`<span class="aw-badge ${tone}">${esc(human(v))}</span>`;
  const empty=s=>`<div class="aw-empty">${esc(s)}</div>`;
  const button=(action,label,id='')=>`<button type="button" class="aw-button" data-aw-action="${action}" data-id="${esc(id)}">${esc(label)}</button>`;
  const kv=obj=>`<dl class="aw-kv">${Object.entries(obj||{}).map(([k,v])=>`<dt>${esc(human(k))}</dt><dd>${esc(v===null||v===undefined?'Not recorded':typeof v==='object'?JSON.stringify(v):v)}</dd>`).join('')}</dl>`;
  const table=(headers,rows)=>`<div class="aw-table-wrap" tabindex="0" role="region" aria-label="Scrollable data table"><table class="aw-table"><thead><tr>${headers.map(h=>`<th scope="col">${esc(h)}</th>`).join('')}</tr></thead><tbody>${rows.length?rows.map(r=>`<tr>${r.map(c=>`<td>${c}</td>`).join('')}</tr>`).join(''):`<tr><td colspan="${headers.length}">No records in this scope.</td></tr>`}</tbody></table></div>`;
  let tab='evidence',serial=0,controller=null,reviewers=[],initialized=false,eventSnapshot='',eventCursor='',consentCursor='',securityCursor='',caseCache={},projectCache={},lastSummaryKey='',lastSummaryAt=0;
  let eventSearch='',eventActor='',eventAction='',securitySeverity='',pendingView=null,optionSerial=0;
  let activeIdentity='';
  const identity=()=>JSON.stringify([getActiveRole(),getActiveToken()]);
  const summaryKey=()=>JSON.stringify([identity(),scope()]);

  function visible(){return $('auditorReportView')&&$('auditorReportView').style.display!=='none';}
  function scope(){
    return {state:$('filterState')?.value||'',district:$('filterDistrict')?.value||'',category:$('filterCategory')?.value||'',urgency:$('filterUrgency')?.value||'',
      project_id:$('awProject')?.value||'',request_id:$('awTicket')?.value.trim()||'',date_from:$('awIntakeFrom')?.value||'',date_to:$('awIntakeTo')?.value||'',event_from:$('awEventFrom')?.value||'',event_to:$('awEventTo')?.value||''};
  }
  async function api(path,options={},signal=null,extra={}){
    const params=new URLSearchParams(Object.entries({...scope(),...extra}).filter(([,v])=>v!==''&&v!==null&&v!==undefined));
    const response=await fetch(API+path+(params.size?'?'+params:''),{...options,headers:{Authorization:'Bearer '+getActiveToken(),'Content-Type':'application/json'},signal});
    const data=await response.json();if(!response.ok||!data.success)throw new Error(data.error||`Request failed (${response.status})`);return data;
  }
  function status(message,error=false){const el=$('awActionStatus');el.hidden=false;el.classList.toggle('aw-error',error);el.textContent=message;}
  function owners(){return reviewers.map(r=>`<option value="${esc(r.name)}">${esc(r.name)} (${esc(r.role)})</option>`).join('');}
  function caseForm(kind='discrepancy'){
    return `<details><summary>Open a ${esc(kind)} review case</summary><form data-aw-form="case" class="aw-form">
      <input type="hidden" name="kind" value="${esc(kind)}"><label class="aw-full">Title<input name="title" required minlength="8" maxlength="200"></label>
      <label>Linked ticket<input name="request_id" value="${esc($('awTicket').value)}" ${kind==='rights'?'required':''}></label>
      <label>Assigned reviewer<select name="owner">${owners()}</select></label><label>Severity<select name="severity"><option>MEDIUM</option><option>LOW</option><option>HIGH</option><option>CRITICAL</option></select></label>
      <label>Evidence mode<select name="data_mode"><option value="synthetic">Synthetic demonstration</option><option value="operational">Operational evidence</option></select></label>
      <label>Due date<input name="due_at" type="date"></label><label class="aw-full">Review reason<textarea name="notes" required minlength="10"></textarea></label>
      <button class="aw-button aw-primary" type="submit">Create review case</button><p class="aw-note">Creates a local case. No team is contacted and no payment is changed.</p></form></details>`;
  }
  function casesView(data,kind='discrepancy'){
    data.items.forEach(c=>caseCache[c.case_id]=c);
    return `<section class="aw-card"><h3>Review queue</h3><p>${num(data.open)} open · ${num(data.overdue)} overdue in this scope</p>${table(['Case / subject','Owner','State','Due','Review'],data.items.map(c=>[
      `<code>${esc(c.case_id)}</code><br>${esc(c.title)} ${badge(c.data_mode)}`,esc(c.owner),badge(c.status,c.status==='resolved'?'good':'warn'),esc(c.due_at.slice(0,10)),button('case', 'Inspect',c.case_id)]))}${caseForm(kind)}</section>`;
  }
  function evidenceForm(defaultKind='source'){
    return `<details><summary>Attach a source, delivery record or outcome observation</summary><form data-aw-form="evidence" class="aw-form">
      <label>Evidence type<select name="kind">${['source','milestone','payment','site','outcome','evaluation_protocol'].map(k=>`<option value="${k}" ${k===defaultKind?'selected':''}>${esc(human(k))}</option>`).join('')}</select></label>
      <label>Title<input name="title" required minlength="5"></label><label>Observation date<input name="observed_at" type="date" required></label>
      <label>Evidence mode<select name="data_mode"><option value="synthetic">Synthetic demonstration</option><option value="operational">Operational evidence</option></select></label>
      <label class="aw-full">Document reference (https, gs, ee or urn)<input name="source_uri" required placeholder="https://publisher.example/document or urn:nvb:demo:observation"></label>
      <label class="aw-full">Document SHA-256 (optional; declared digest)<input name="sha256" pattern="[a-fA-F0-9]{64}"></label>
      <label>Publisher<input name="publisher"></label><label>Release / source table<input name="release"></label><label>Redistribution terms<input name="redistribution_terms"></label><label>Boundary crosswalk<input name="boundary_crosswalk"></label>
      <label>Milestone<input name="milestone" placeholder="Required for site/payment/milestone"></label><label>Reporting period<input name="period" placeholder="e.g. 2026-08"></label>
      <label>Sanctioned (₹ lakh)<input name="sanctioned_lakh" type="number" min="0" step="any"></label><label>Released (₹ lakh)<input name="released_lakh" type="number" min="0" step="any"></label><label>Spent (₹ lakh)<input name="spent_lakh" type="number" min="0" step="any"></label>
      <label>Certified progress (%)<input name="certified_progress_pct" type="number" min="0" max="100" step="any"></label><label>Observed site progress (%)<input name="observed_progress_pct" type="number" min="0" max="100" step="any"></label>
      <label>Outcome measure<input name="measure" placeholder="e.g. Hours of water supply"></label><label>Outcome unit<input name="unit"></label><label>Catchment<input name="catchment"></label><label>Observation phase<select name="phase"><option value="">Select for outcomes</option><option value="before">Before delivery</option><option value="after">After delivery</option></select></label>
      <label>Observed value<input name="value" type="number" step="any"></label><label>Sample size<input name="sample_size" type="number" min="1"></label>
      <button class="aw-button aw-primary" type="submit">Save evidence for review</button><p class="aw-note">An independent user must review submitted evidence. A stored URL or checksum does not verify a publisher.</p></form></details>`;
  }
  function assetsView(rows){return rows.length?`<div class="aw-grid">${rows.map(e=>`<article class="aw-tile"><h4>${esc(e.title)}</h4>${badge(e.kind)}${badge(e.data_mode)}${badge(e.status,e.status==='rejected'?'bad':e.status==='human_reviewed'?'good':'warn')}<p>${esc(e.observed_at.slice(0,10))} · ${esc(e.submitted_by)}</p><details><summary>Inspect observation and review</summary>${kv({reference:e.source_uri,declared_sha256:e.sha256,...e.metadata,reviewer:e.reviewed_by,review_notes:e.review_notes})}</details>${button('review-evidence','Review evidence',e.evidence_id)}</article>`).join('')}</div>`:empty('No project evidence attached. Add a documented observation or clearly labelled demo record.');}
  function projectPrompt(){return empty('Select a project to inspect its linked evidence, delivery and outcomes. Project IDs are shared with the Analyst workspace.');}
  function classificationTable(rows){return table(['Group','Samples','Category macro-F1','Accuracy (95% interval)','Emergency recall (95% interval)','Emergency samples'],rows.map(([name,m])=>[
    esc(human(name)),num(m.n),num(m.category_macro_f1),`${percent(m.accuracy)}<br>${interval(m.accuracy_ci95)}`,`${percent(m.emergency_recall)}<br>${interval(m.emergency_recall_ci95)}`,num(m.emergency_n)]));}
  function evaluationDetails(r){
    const speech=r.speech||{},duplicates=r.duplicates||{};
    return `${kv({model_version:r.model_version,dataset_version:r.dataset_version,dataset_sha256:r.dataset_sha256,review_reference:r.review_reference})}
      ${Object.entries(r.groups||{}).map(([dimension,g])=>`<h4>${esc(human(dimension))}</h4>${classificationTable(Object.entries(g.items||{}))}<p class="aw-note">Accuracy gap: ${g.accuracy_gap===null||g.accuracy_gap===undefined?'Not available — at least two groups are required':num(g.accuracy_gap*100)+' percentage points'}. These samples describe this evaluation set.</p>`).join('')}
      <h4>Speech and reviewed labels</h4>${table(['Measure','Labelled samples','Result','95% interval'],[
        ['Word error rate',num(speech.n),percent(speech.wer),'Not estimated'],
        ['Character error rate',num(speech.n),percent(speech.cer),'Not estimated'],
        ...[['Translation review',r.translation_correct],['Location review',r.location_correct]].map(([label,m])=>[label,num(m?.n),percent(m?.accuracy),interval(m?.ci95)]),
        ['Duplicate precision',num(duplicates.n),percent(duplicates.precision),'Not estimated'],
        ['Duplicate recall',num(duplicates.n),percent(duplicates.recall),'Not estimated']])}
      <p class="aw-note">Missing labels leave a metric unavailable. Small samples have wide uncertainty; these results do not establish national language coverage.</p>
      <h4>Language distribution change</h4>${kv(r.distribution_drift)}`;
  }
  function evalView(data){return `<details id="awQuality"><summary>AI quality & inclusion — measured evaluation results</summary><p class="aw-note">Intake volume is not a fairness score. Evaluations use their own recorded datasets; intake filters do not change historical results.</p>
    ${data.items.length?data.items.map(j=>`<article class="aw-tile"><h4>${esc(j.job_id)}</h4>${badge(j.status)}${j.result?`${badge(j.result.label_status,'warn')}${classificationTable([['Overall',j.result.overall]])}<details><summary>Language, region, channel and speech results</summary>${evaluationDetails(j.result)}</details><p class="aw-note">${esc(j.result.notice)}</p>${button('export-evaluation','Export this result',j.job_id)}`:`<p>${esc(j.error||'Evaluation is queued or running. Refresh results to check completion.')}</p>`}</article>`).join(''):empty('No evaluation jobs have been run. Import labelled predictions to measure quality; unreviewed demo labels remain provisional.')}
    <form data-aw-form="evaluation" class="aw-form"><label class="aw-full">Labelled evaluation file (JSON; up to 500 samples)<input type="file" name="evaluation_file" accept="application/json,.json" required></label><button class="aw-button aw-primary" type="submit">Queue evaluation</button>${button('evaluation-refresh','Refresh results')}<a href="/static/data/auditor-evaluation-example.json" download>Download a provisional example</a></form></details>`;}

  async function render(signal){
    const pid=scope().project_id;
    if(tab==='evidence'){
      const [cases,evaluations]=await Promise.all([api('/cases',{},signal, {limit:10}),api('/evaluations',{},signal)]);
      if(!pid){
        const data=await api('/projects',{},signal,{search:$('awProjectSearch').value,limit:50});
        return `<section class="aw-card"><h3>Choose a proposal to audit</h3><p>${num(data.total)} project groups match this scope. Showing up to 50; search to narrow the list.</p>${table(['Project','Reports','Open evidence'],data.items.map(p=>[`${esc(p.title)}<br><code>${esc(p.project_id)}</code>`,num(p.reports),button('project','Inspect project',p.project_id)]))}</section>${casesView(cases)}${evalView(evaluations)}`;
      }
      const d=await api('/projects/'+encodeURIComponent(pid),{},signal);d.evidence.forEach(e=>projectCache[e.evidence_id]=e);
      const sources=d.detail.evidence.sources;
      return `<section class="aw-card"><h3>${esc(d.project.title)}</h3><code>${esc(pid)}</code><div class="aw-flow"><span>${num(d.detail.total_requests)} citizen reports</span><span>→ Proposal</span><span>→ ${num(d.decisions.length)} decision records</span><span>→ ${num(d.evidence.length)} attached observations</span></div>
        <div>${Object.entries(d.readiness).map(([k,v])=>badge(human(k)+': '+(v?'recorded':'missing'),v?'good':'warn')).join('')}</div><div class="aw-actions">${button('export-project','Export redacted evidence pack',pid)}</div>
        ${table(['Decision','Status','Scenario / score version','Review'],d.decisions.map(x=>[esc(x.decision_id),badge(x.status),`${esc(x.scenario_id)}<br>${esc(x.score_version)}`,esc(x.engineering_review?'Engineering review recorded':'Not reviewed')]))}
        <details><summary>Inspect citizen reports (${num(d.detail.total_requests)}; first 100 shown)</summary>${table(['Ticket / language','Citizen report','Translated text','Status'],d.detail.requests.map(r=>[`${esc(r.request_id)}<br>${esc(r.input_language)} ${r.is_synthetic?badge('synthetic'):''}`,esc(r.original_text),esc(r.translated_text),badge(r.status)]))}</details></section>
        <section class="aw-card"><h3>Reference sources</h3><p>Publisher verification and geography validation remain separate from file loading.</p><div class="aw-grid">${sources.map(s=>`<article class="aw-tile"><h4>${esc(s.name)}</h4>${badge('verification pending','warn')}<p>${esc(s.publisher)} · ${num(s.scoped_records)} records</p><details><summary>Source indicators</summary>${s.sample?.length?table(Object.keys(s.sample[0]),s.sample.map(r=>Object.values(r).map(esc))):empty('No reference records in this geography.')}${kv({publisher_reference:s.url,loaded_sha256:s.sha256,observation_year:s.observation_year,license:s.license_status,boundary:s.boundary_status})}</details></article>`).join('')}</div></section>
        <section class="aw-card"><h3>Project evidence and reviews</h3>${assetsView(d.evidence)}${evidenceForm()}</section>${casesView(cases)}${evalView(evaluations)}`;
    }
    if(tab==='delivery'){
      if(!pid)return projectPrompt();
      const [data,cases]=await Promise.all([api('/projects/'+pid+'/financial',{},signal),api('/cases',{},signal,{kind:'discrepancy'})]);
      return `<section class="aw-card"><h3>Milestone and financial reconciliation</h3><p>${esc(data.project.title)}</p><div class="aw-status">${esc(data.financial_action)}</div>
        ${table(['Milestone / period','Evidence','Amounts (₹ lakh)','Physical progress','Review'],data.records.map(r=>[`${esc(r.milestone)}<br>${esc(r.period)}`,`${esc(r.kind)}<br><code>${esc(r.evidence_id)}</code>`,kv({sanctioned:r.sanctioned_lakh,released:r.released_lakh,spent:r.spent_lakh}),kv({certified_pct:r.certified_progress_pct,observed_pct:r.observed_progress_pct}),`${badge(r.status)}${badge(r.data_mode)}`]))}
        <h4>Comparable observations</h4>${data.discrepancies.length?table(['Claim / observation','Difference','Finding'],data.discrepancies.map(x=>[`${esc(x.claim)}<br>${esc(x.observation)}`,`${num(x.difference_pp)} percentage points`,`${badge(x.status,'warn')} ${badge(x.data_mode)}<p>${esc(x.meaning)}</p>`])):empty('No comparable milestone and site observations yet. Match the milestone, reporting period and evidence mode before comparing.')}
        <p class="aw-note">${esc(data.satellite_status)}</p>${evidenceForm('milestone')}</section>${casesView(cases)}`;
    }
    if(tab==='outcomes'){
      if(!pid)return projectPrompt();const data=await api('/projects/'+pid+'/outcomes',{},signal);
      return `<section class="aw-card"><h3>Observed service outcomes</h3><p>${esc(data.project.title)}</p><div class="aw-status">${esc(data.message)}</div>
        ${data.observations.length?table(['Service measure','Before','After','Change','Evidence status'],data.observations.map(r=>[`${esc(r.measure)} (${esc(r.unit)})`,`${num(r.before)} · n=${num(r.before_n)}`,`${num(r.after)} · n=${num(r.after_n)}`,num(r.change),badge(r.status,'warn')])):empty('Evaluation not ready: attach independently reviewed before/after observations using the same measure, unit and catchment.')}
        <h4>Evaluation readiness</h4>${kv(data.readiness)}${evidenceForm('outcome')}</section>`;
    }
    if(tab==='events'){
      const data=await api('/events',{},signal,{search:eventSearch,actor:eventActor,action:eventAction,cursor:eventCursor,snapshot:eventSnapshot,limit:50});eventSnapshot=data.snapshot;
      return `<section class="aw-card"><h3>Search the complete audit history</h3><form class="aw-form" data-aw-form="event-search"><label>Ticket, project, actor or action<input name="search" value="${esc(eventSearch)}"></label><label>Exact actor<input name="actor" value="${esc(eventActor)}"></label><label>Exact action<input name="action" value="${esc(eventAction)}"></label><button class="aw-button aw-primary">Search</button></form>
        <p>${num(data.total)} matching events · snapshot through event ${num(data.head_id)}</p><div class="aw-actions">${button('export-events','Export evidence pack')}${button('export-events-csv','Export CSV')}${button('integrity','Verify audit integrity')}${button('events-reset','Newest snapshot')}</div>
        ${table(['Event / time','Actor','Action','Resource','Integrity','Inspect'],data.items.map(r=>[`${num(r.id)}<br>${esc(r.created_at)}`,esc(r.actor),esc(r.action),`${esc(r.resource_type)}<br><code>${esc(r.resource_id)}</code>`,badge(r.event_version?'Hash-linked; verify':'Legacy / unsigned',r.event_version?'':'warn'),button('event','Details',r.id)]))}
        ${data.next_cursor?button('events-next','Next 50 events',data.next_cursor):''}<p class="aw-note">Exports use this search, scope and snapshot. Exports above 10,000 events require narrower filters. ${esc(data.integrity_notice)}</p></section>`;
    }
    if(tab==='consent'){
      const [data,cases]=await Promise.all([api('/consent',{},signal,{cursor:consentCursor}),api('/cases',{},signal,{kind:'rights'})]);
      return `<section class="aw-card"><h3>Purpose and consent receipts</h3><p>${num(data.coverage.linked_requests)} / ${num(data.coverage.total_requests)} scoped reports have receipts; ${num(data.coverage.missing_receipts)} have no linked receipt.</p><p class="aw-note">${esc(data.coverage.notice)}</p>
        ${table(['Receipt / ticket','Purpose / basis','Consent state','Context','Time'],data.items.map(r=>[`${esc(r.event_id)}<br><code>${esc(r.request_id)}</code>`,`${esc(r.consent_scope)}<br>${esc(r.legal_basis)}`,`${badge(r.event_state,r.event_state==='granted'?'good':'warn')}${badge(r.is_current?'latest event':'superseded')}`,`${esc(r.language)} · ${esc(r.channel)}<br>${badge(r.linked?'linked':'unlinked legacy','warn')}${badge(r.data_mode)}`,esc(r.created_at)]))}${data.next_cursor?button('consent-next','Next receipts',data.next_cursor):''}
        <details><summary>Record a verified consent or processing-basis action</summary><form data-aw-form="consent" class="aw-form"><label>Ticket ID<input name="request_id" value="${esc($('awTicket').value)}" required></label><label>Event<select name="event_state">${['granted','declined','withdrawn','missing','other_basis'].map(x=>`<option>${x}</option>`).join('')}</select></label><label>Purpose<input name="purpose" value="request_processing" required></label><label>Legal basis<input name="legal_basis" value="consent" required></label><label>Notice version<input name="notice_version" value="v1" required></label><label class="aw-full">Receipt / identity-check reference<input name="receipt_reference" required placeholder="https://… or urn:nvb:demo:receipt"></label><label class="aw-full">Identity check and action reason<textarea name="reason" required minlength="10"></textarea></label><button class="aw-button aw-primary">Record receipt</button><p class="aw-note">Withdrawal records a local purpose restriction. External erasure or retention requires completion evidence.</p></form></details></section>${casesView(cases,'rights')}`;
    }
    const [data,cases]=await Promise.all([api('/security',{},signal,{cursor:securityCursor,severity:securitySeverity}),api('/cases',{},signal,{kind:'security'})]);
    return `<section class="aw-card"><h3>Security detections and coverage</h3><p>${esc(data.notice)}</p><div class="aw-grid">${data.coverage.map(c=>`<div class="aw-tile"><strong>${esc(human(c.detector))}</strong><p>${badge(c.status,c.status==='instrumented'?'':'warn')}</p></div>`).join('')}</div>
      <form data-aw-form="severity" class="aw-form"><label>Severity<select name="severity"><option value="">All severities</option>${['LOW','MEDIUM','HIGH','CRITICAL'].map(s=>`<option ${s===securitySeverity?'selected':''}>${s}</option>`).join('')}</select></label><button class="aw-button">Apply severity</button></form><p>${num(data.total)} matching detections</p>
      ${table(['Detection','Severity','Observed event','Time'],data.items.map(r=>[esc(human(r.detector)),badge(r.severity,r.severity==='HIGH'||r.severity==='CRITICAL'?'bad':'warn'),esc(r.message),esc(r.occurred_at)]))}${data.next_cursor?button('security-next','Next detections',data.next_cursor):''}</section>${casesView(cases,'security')}`;
  }

  async function refresh(force=false){
    if(!visible())return;
    const currentIdentity=identity();
    if(activeIdentity!==currentIdentity){activeIdentity=currentIdentity;reviewers=[];caseCache={};projectCache={};lastSummaryKey='';optionSerial++;resetPages();}
    if(!force&&$('awContent')?.contains(document.activeElement)&&document.activeElement.matches('input,textarea,select'))return;
    const viewKey=JSON.stringify([currentIdentity,scope(),tab,eventCursor,consentCursor,securityCursor]);
    if(!force&&pendingView===viewKey)return;
    pendingView=viewKey;
    const turn=++serial;controller?.abort();controller=new AbortController();const signal=controller.signal;
    if(lastSummaryKey!==summaryKey())$('awMetrics').innerHTML='';
    $('awContent').innerHTML='<div class="aw-loading" role="status">Loading this view…</div>';$('awInspector').hidden=true;
    try{
      if(!reviewers.length)reviewers=(await api('/reviewers',{},signal)).items;
      const key=summaryKey();
      if(force||lastSummaryKey!==key||Date.now()-lastSummaryAt>15000){
        const data=await api('/snapshot',{},signal);if(turn!==serial||currentIdentity!==identity())return;
        $('awMetrics').innerHTML=data.metrics.map(m=>`<div class="aw-metric"><span>${esc(m.name)}</span><strong>${num(m.value)}${m.unit==='percent'?'%':''}</strong><small>${esc(m.denominator!==null?`${num(m.numerator)} / ${num(m.denominator)}`:m.status)}</small></div>`).join('');
        $('awStatus').classList.remove('aw-error');$('awStatus').textContent=`${human(data.metadata.data_mode)} data · ${scope().district||scope().state||'All pilot geographies'} · as of ${new Date(data.metadata.as_of).toLocaleString()} · ${scope().project_id||'All projects'}`;
        lastSummaryKey=key;lastSummaryAt=Date.now();
      }
      const html=await render(signal);if(turn!==serial||currentIdentity!==identity())return;$('awContent').innerHTML=html;
      $('awContent').setAttribute('aria-labelledby',document.querySelector('[data-aw-tab][aria-selected=true]')?.id||'awTabEvidence');
    }catch(error){if(error.name==='AbortError')return;if(turn!==serial)return;$('awContent').innerHTML=empty(error.message);$('awMetrics').innerHTML='';lastSummaryKey='';$('awStatus').textContent='This view is unavailable. Refresh or adjust the scope.';$('awStatus').classList.add('aw-error');}
    finally{if(turn===serial)pendingView=null;}
  }
  async function loadProjectOptions(){
    const optionRequest=++optionSerial;
    const optionIdentity=identity();
    const data=await api('/projects',{},null,{project_id:'',search:$('awProjectSearch').value,limit:50});
    if(optionRequest!==optionSerial||optionIdentity!==identity())return;
    // A user may select a project while this search is in flight.
    const selected=$('awProject').value;
    $('awProject').innerHTML='<option value="">All projects</option>'+data.items.map(p=>`<option value="${esc(p.project_id)}">${esc(p.title)}</option>`).join('');
    if(selected&&!data.items.some(p=>p.project_id===selected))$('awProject').add(new Option(selected,selected));$('awProject').value=selected;
  }
  function resetPages(){eventSnapshot='';eventCursor='';consentCursor='';securityCursor='';}
  function showInspector(html){$('awInspector').innerHTML=button('close-inspector','Close details')+html;$('awInspector').hidden=false;$('awInspector').focus();$('awInspector').scrollIntoView({behavior:'smooth',block:'start'});}
  async function action(name,id){
    if(name==='project'){$('awProject').add(new Option(id,id));$('awProject').value=id;resetPages();await refresh(true);return;}
    if(name==='events-next'){eventCursor=id;await refresh();return;}
    if(name==='events-reset'){eventCursor='';eventSnapshot='';await refresh(true);return;}
    if(name==='consent-next'){consentCursor=id;await refresh();return;}
    if(name==='security-next'){securityCursor=id;await refresh();return;}
    if(name==='close-inspector'){$('awInspector').hidden=true;return;}
    if(name==='evaluation-refresh'){await refresh();return;}
    if(name==='integrity'){const d=await api('/integrity');showInspector('<h3>Audit integrity report</h3>'+kv(d));return;}
    if(name==='event'){const d=await api('/events/'+id);showInspector('<h3>Event details</h3>'+kv(d.event));return;}
    if(name==='case'){
      const {case:c}=await api('/cases/'+id);caseCache[id]=c;
      showInspector(`<h3>${esc(c.title)}</h3>${kv({case_id:c.case_id,owner:c.owner,status:c.status,data_mode:c.data_mode,version:c.version})}${table(['Time','Actor','Action','Reason'],c.events.map(e=>[esc(e.created_at),esc(e.actor),esc(human(e.action)),esc(e.notes)]))}
        <form data-aw-form="case-action" data-id="${esc(id)}" class="aw-form"><label>Action<select name="action">${['assign','start','request_evidence','resolve','reopen','request_hold'].map(x=>`<option value="${x}">${esc(human(x))}</option>`).join('')}</select></label><label>Assigned reviewer<select name="owner">${owners()}</select></label><label class="aw-full">Rationale<textarea name="notes" required minlength="10"></textarea></label><label class="aw-full">Closure evidence reference (required to resolve)<input name="evidence_reference"></label><button class="aw-button aw-primary">Record action</button></form><p class="aw-note">An independent reviewer must close a finding. Request hold records a recommendation; it does not freeze funds.</p>`);return;
    }
    if(name==='review-evidence'){
      const e=projectCache[id];showInspector(`<h3>Review ${esc(e.title)}</h3>${kv({submitted_by:e.submitted_by,source_reference:e.source_uri,...e.metadata})}<form data-aw-form="evidence-review" data-id="${esc(id)}" class="aw-form"><label>Finding<select name="status"><option value="human_reviewed">Human reviewed</option><option value="rejected">Rejected</option></select></label><label class="aw-full">Verification criteria and finding<textarea name="notes" required minlength="20"></textarea></label><button class="aw-button aw-primary">Save independent review</button></form>`);return;
    }
    if(name.startsWith('export-')){
      const kind=name.startsWith('export-events')?'events':name==='export-project'?'project':'evaluation';
      const d=await api('/exports',{method:'POST',body:JSON.stringify({kind,project_id:id,job_id:id,search:eventSearch,actor:eventActor,action:eventAction,snapshot:eventSnapshot})});
      let blob,extension='json';
      if(name==='export-events-csv'){
        const response=await fetch(API+'/exports/'+d.snapshot_id+'?format=csv',{headers:{Authorization:'Bearer '+getActiveToken()}});
        if(!response.ok)throw new Error('CSV download failed');blob=await response.blob();extension='csv';
      }else{const r=await api('/exports/'+d.snapshot_id);blob=new Blob([JSON.stringify(r,null,2)],{type:'application/json'});}
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=d.snapshot_id+'.'+extension;link.click();URL.revokeObjectURL(url);status('Export saved for the displayed scope and snapshot. The JSON evidence pack includes its SHA-256 manifest.');return;
    }
  }
  async function submit(form){
    const kind=form.dataset.awForm,data=Object.fromEntries(new FormData(form));
    if(kind==='event-search'){eventSearch=data.search;eventActor=data.actor;eventAction=data.action;eventSnapshot='';eventCursor='';await refresh();return;}
    if(kind==='severity'){securitySeverity=data.severity;securityCursor='';await refresh();return;}
    if(kind==='case')await api('/cases',{method:'POST',body:JSON.stringify({...data,project_id:scope().project_id})});
    if(kind==='case-action')await api('/cases/'+form.dataset.id+'/actions',{method:'POST',body:JSON.stringify({...data,version:caseCache[form.dataset.id].version})});
    if(kind==='consent')await api('/consent/actions',{method:'POST',body:JSON.stringify(data)});
    if(kind==='evidence-review')await api('/evidence/'+form.dataset.id+'/review',{method:'POST',body:JSON.stringify({...data,version:projectCache[form.dataset.id].version})});
    if(kind==='evidence'){
      const metadata={};const numeric=['sanctioned_lakh','released_lakh','spent_lakh','certified_progress_pct','observed_progress_pct','value','sample_size'];
      for(const k of ['publisher','release','redistribution_terms','boundary_crosswalk','milestone','period','measure','unit','catchment','phase',...numeric])if(data[k]!=='')metadata[k]=numeric.includes(k)?Number(data[k]):data[k];
      await api('/projects/'+scope().project_id+'/evidence',{method:'POST',body:JSON.stringify({...data,metadata})});
    }
    if(kind==='evaluation'){
      const f=form.elements.evaluation_file.files[0];if(f.size>1024*1024)throw new Error('Evaluation file exceeds 1 MiB');
      const content=JSON.parse(await f.text());await api('/evaluations',{method:'POST',body:JSON.stringify(content)});
    }
    status(kind==='evaluation'?'Evaluation queued. Refresh the result to check completion.':'Action saved with an audit event.');await refresh(true);
  }
  function init(){
    if(initialized||!$('auditorReportView'))return;initialized=true;
    const linkedProject=routeParams.get('audit_project');
    if(linkedProject){$('awProject').add(new Option(linkedProject,linkedProject));$('awProject').value=linkedProject;}
    $('awScopeForm').addEventListener('submit',async e=>{e.preventDefault();resetPages();try{await loadProjectOptions();await refresh(true);}catch(err){status(err.message,true);}});
    $('awProject').addEventListener('change',()=>{resetPages();refresh(true);});
    $('awClear').addEventListener('click',()=>{$('awScopeForm').reset();resetPages();refresh(true);});
    $('awRefresh').addEventListener('click',()=>{resetPages();refresh(true);});
    for(const id of ['filterState','filterDistrict','filterCategory','filterUrgency'])$(id)?.addEventListener('change',()=>{$('awProject').value='';$('awProjectSearch').value='';resetPages();});
    $('auditorReportView').addEventListener('click',async e=>{
      const b=e.target.closest('[data-aw-tab],[data-aw-action]');if(!b)return;
      if(b.dataset.awTab){tab=b.dataset.awTab;document.querySelectorAll('[data-aw-tab]').forEach(x=>{const active=x===b;x.setAttribute('aria-selected',String(active));x.tabIndex=active?0:-1;});refresh();return;}
      b.disabled=true;try{await action(b.dataset.awAction,b.dataset.id);}catch(err){status(err.message,true);}finally{b.disabled=false;}
    });
    $('auditorReportView').addEventListener('submit',async e=>{const form=e.target.closest('[data-aw-form]');if(!form)return;e.preventDefault();const b=form.querySelector('button[type=submit],button:not([type])');if(b)b.disabled=true;try{await submit(form);}catch(err){status(err.message,true);}finally{if(b)b.disabled=false;}});
    document.querySelector('.aw-tabs').addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;e.preventDefault();const tabs=[...document.querySelectorAll('[data-aw-tab]')],i=tabs.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?tabs.length-1:(i+(e.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;tabs[next].focus();tabs[next].click();});
    for(const b of document.querySelectorAll('[data-aw-tab]'))b.tabIndex=b.getAttribute('aria-selected')==='true'?0:-1;
    refresh();
  }
  window.NVBAuditor={refresh:()=>{init();if(!document.hidden)refresh();},openProject:pid=>{init();$('awProject').add(new Option(pid,pid));$('awProject').value=pid;resetPages();refresh(true);},openForAdmin:()=>{
    if(getActiveRole()!=='admin')return;$('adminReportView').style.display='none';$('auditorReportView').style.display='block';applyRoleLayout('auditor');$('rbacReportTitle').textContent='Auditor review tools · Admin identity';init();refresh(true);
  }};
  document.addEventListener('DOMContentLoaded',init);
})();
