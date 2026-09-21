(() => {
  'use strict';
  window.trackPilotRequest=async () => {
    const box=document.getElementById('trackResultContainer');box.classList.remove('hidden');box.textContent='Checking your private receipt…';
    try {
      const response=await fetch('/api/v2/pilot/track',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:document.getElementById('trackRequestIdInput').value.trim(),tracking_secret:document.getElementById('trackSecret').value.trim()})});
      const data=await response.json();if(!response.ok)throw new Error(data.error||'Tracking unavailable');
      box.replaceChildren();
      for(const text of [data.record.request_id,'Stage: '+data.record.status,'Processing: '+data.record.processing_status,'Department: '+data.record.routed_department,data.record.translated_text||'Translation awaits review']){const p=document.createElement('p');p.textContent=text;box.append(p);}
    }catch(error){box.textContent=error.message;}
  };
  document.getElementById('pilotTrackForm')?.addEventListener('submit',event=>{event.preventDefault();window.trackPilotRequest();});
})();
