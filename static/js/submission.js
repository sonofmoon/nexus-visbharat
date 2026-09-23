(() => {
  const $ = id => document.getElementById(id);
  const text = (tag, value, className) => { const el = document.createElement(tag); el.textContent = value; if(className) el.className=className; return el; };
  const detail = (title, data) => { const el=document.createElement('details'); el.append(text('summary',title),text('pre',JSON.stringify(data,null,2))); return el; };
  async function refresh() {
    $('refresh').disabled=true;
    try {
      const res=await fetch('/api/submission/readiness',{cache:'no-store'}), data=await res.json();
      if(!res.ok || !data.success) throw new Error(data.error || 'Evidence is unavailable.');
      $('runtime-message').textContent=data.storage.message;
      $('providers').replaceChildren(...Object.entries(data.providers.services).map(([key,service]) => {
        const card=text('article',''); card.append(text('h3',key.replace('google_','Google ').replaceAll('_',' ')),text('span',service.status,'badge '+service.status));
        card.append(detail('Operation evidence',service.operations)); return card;
      }));
      $('data-summary').textContent=`${data.dataset.rows.toLocaleString()} prepared synthetic reports. ${data.dataset.limitations}`;
      $('scope-summary').textContent=`Configured: ${Object.keys(data.scope.states).length} states; ${Object.values(data.scope.languages).join(', ')}. ${data.scope.meaning}`;
      const q=data.evaluation.overall;
      $('quality-summary').textContent=q ? `${q.samples} provisional text cases · Category macro-F1: ${q.category_macro_f1} · Urgency accuracy: ${(q.urgency_accuracy*100).toFixed(1)}% · Emergency recall: ${q.emergency_recall} on ${q.emergency_samples} cases. Independent human review: ${data.evaluation.independent_human_review ? 'recorded' : 'pending'}.` : 'No quality report available.';
    } catch(error) { $('runtime-message').textContent=error.message; }
    finally { $('refresh').disabled=false; }
  }
  $('refresh').addEventListener('click',refresh);
  $('use-demo-token')?.addEventListener('click', () => {
    $('access-token').value = 'visbharat-analyst-token';
    if (!$('ticket').value.trim()) {
      $('ticket').value = 'NVB-202608271A54';
    }
    const fb = $('fill-feedback');
    if (fb) { fb.style.display = 'inline'; setTimeout(() => { fb.style.display = 'none'; }, 3000); }
  });
  document.querySelectorAll('.sample-ticket-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      $('ticket').value = btn.dataset.ticket;
      if (!$('access-token').value.trim()) {
        $('access-token').value = 'visbharat-analyst-token';
      }
      const fb = $('fill-feedback');
      if (fb) { fb.style.display = 'inline'; setTimeout(() => { fb.style.display = 'none'; }, 3000); }
    });
  });
  $('ticket').value=new URLSearchParams(location.search).get('ticket') || '';
  $('trace-form').addEventListener('submit',async event => {
    event.preventDefault(); $('journey').replaceChildren(); $('trace-status').textContent='Loading the authorized record...';
    const token=$('access-token').value.trim();
    try {
      const res=await fetch('/api/v2/analyst/requests/'+encodeURIComponent($('ticket').value.trim())+'/journey',
        {cache:'no-store',credentials:'same-origin',headers:token ? {Authorization:'Bearer '+token} : {}});
      const data=await res.json(); if(!res.ok || !data.success)throw new Error(data.error || 'Could not load the journey.');
      const r=data.request,box=$('journey'); $('trace-status').textContent=`${r.request_id} · ${data.data_mode} · ${r.state} / ${r.district}`;
      const original=text('blockquote',r.original_text); original.lang=r.input_language;
      box.append(text('h3','1. Citizen report'),original,text('p',r.translated_text),text('p',`Channel: ${r.source_channel}. Status: ${r.status}. Department: ${r.routed_department || 'Review pending'}.`));
      box.append(text('h3','2. AI processing'),detail('Models, fallback state and invocation traces',data.processing));
      box.append(text('h3','3. Demand and proposal'),text('p',`Clusters: ${data.clusters.map(c=>c.cluster_id).join(', ') || 'Assignment pending'}. Candidate: ${data.project_id}. ${data.project_request_count} reports in the project catchment.`),text('p',data.planning_eligible ? 'Included in the candidate evidence. Funding and engineering approval remain separate.' : 'Excluded from capital evidence; emergency or pending review must follow its own handling route.'));
      box.append(detail('Reference sources and verification status',data.evidence),text('h3','4. Recorded decisions'),data.decisions.length ? detail('Decisions that include this ticket',data.decisions) : text('p','No recorded decision includes this ticket yet.'));
      box.append(text('h3','5. Delivery follow-up'),detail('Recorded lifecycle events',data.events),text('p',data.limitations.join(' ')));
      const link=text('a','Continue review in the policy dashboard →'); link.href='/dashboard?demo_ticket='+encodeURIComponent(r.request_id); box.append(link);
    } catch(error) { $('trace-status').textContent=error.message; }
  });
  refresh();
})();
