/* Keep the existing suites attached to the selected programme and authenticated identity. */
(() => {
  const q=new URLSearchParams(location.search),pid=q.get('pilot_id')||(window.NVB_PILOT_ONLY?'vellore-tirupati-water':'');
  if(!pid)return;
  if(window.NVB_PILOT_ONLY){
    const existing=sessionStorage.getItem('nvb_active_role');
    if(['vellore','tirupati'].includes(existing)){
      sessionStorage.setItem('nvb_token_analyst',sessionStorage.getItem('nvb_token_'+existing)||'');
      sessionStorage.setItem('nvb_active_role','analyst');
    }
    if(window.NVB_PILOT_USER_ROLE)sessionStorage.setItem('nvb_active_role',window.NVB_PILOT_USER_ROLE);
    const requested=q.get('workspace');
    if(['analyst','auditor'].includes(requested))sessionStorage.setItem('nvb_active_role',requested);
    refreshAll=()=>getActiveRole()==='auditor'?window.NVBAuditor?.refresh():window.NVBAnalyst?.refresh();
    switchRbacRoleView=role=>{
      if(!['analyst','auditor'].includes(role)){location.href='/pilot';return;}
      sessionStorage.setItem('nvb_active_role',role);
      const selector=document.getElementById('userRoleSelector');if(selector)selector.value=role;
      for(const name of ['analyst','auditor','admin','public'])document.getElementById(name+'ReportView').style.display=name===role?'block':'none';
      applyRoleLayout(role);
      setDisplayForSelectors(['#aiRuntimePanel','.panel-map','.panel-projects','.panel-brief','.panel-feed','.panel-prediction','.panel-ops'],'none');
      document.getElementById('rbacReportTitle').textContent=role==='auditor'?'Independent pilot audit':'Pilot evidence and investment';
      document.getElementById('rbacReportSubtitle').textContent='Vellore · Tirupati · Authorised programme scope';
      const bar=document.getElementById('roleQuickActions');bar.innerHTML='<button type="button" class="btn btn-primary">Refresh pilot evidence</button>';
      bar.querySelector('button').onclick=refreshAll;refreshAll();
    };
  }
  const baseFetch=window.fetch.bind(window);
  window.fetch=(input,options={})=>{
    const raw=typeof input==='string'?input:input instanceof URL?input.href:null;
    if(!raw)return baseFetch(input,options);
    const target=new URL(raw,location.href);
    if(target.origin!==location.origin||!target.pathname.startsWith('/api/'))return baseFetch(input,options);
    target.searchParams.set('pilot_id',pid);
    const headers=new Headers(options.headers||{});
    if(!headers.has('Authorization')&&typeof getActiveToken==='function')headers.set('Authorization','Bearer '+getActiveToken());
    const csrf=window.NVB_PILOT_CSRF;
    if(csrf)headers.set('X-CSRF-Token',csrf);
    return baseFetch(target.href,{...options,headers});
  };
  document.addEventListener('DOMContentLoaded',()=>{
    document.body.classList.add('pilot-scoped-theme');
    document.documentElement.dataset.pilotScoped = 'true';
    if(q.get('workspace')==='analyst')sessionStorage.setItem('nvb_active_role','analyst');
    for(const [name,id] of [['state','filterState'],['district','filterDistrict']]){
      const val=q.get(name),el=document.getElementById(id);
      if(val&&el){el.add(new Option(val,val));el.value=val;}
    }
    const banner=document.createElement('div');
    banner.className='aw-status pilot-scope-banner';
    banner.innerHTML=`
      <div class="pilot-scope-banner-left">
        <span class="pilot-scope-tag"><i class="bi bi-shield-check"></i> BOUNDED PILOT SCOPE</span>
        <span>Active Programme: <strong>Vellore–Tirupati Water Action Pilot</strong> (Tamil Nadu &amp; Andhra Pradesh)</span>
      </div>
      <a class="pilot-scope-return-link" href="/pilot"><i class="bi bi-arrow-left-circle-fill"></i> Return to Ministry Pilot Portal</a>
    `;
    document.getElementById('dashboardGlobalFilters')?.before(banner);
  });
})();
