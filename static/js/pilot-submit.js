(() => {
  'use strict';
  const cfg=window.NVBPilotPublic,$=id=>document.getElementById(id);
  const paths={'/api/states':'states','/api/districts':'districts','/api/v1/geo/ward-suggest':'ward-suggest','/api/classify':'classify','/api/translate':'translate','/api/transcribe-voice':'transcribe-voice'};
  window.pilotCitizenFetch=(url,options={})=>{
    const target=new URL(url,location.origin),feature=paths[target.pathname];
    if(!feature)throw new Error('This feature needs a pilot-scoped connection');
    let body=options.body;
    if(body)body=JSON.stringify({...JSON.parse(body),csrf:cfg.csrf,consent_granted:$('consentGranted').checked,location_id:$('pilotLocation').value});
    return fetch('/api/v2/pilot/portal/'+feature+target.search,{...options,...(body?{body}:{})});
  };
  function communities(){
    const select=$('pilotLocation'),district=$('district').value;
    select.replaceChildren(new Option('Select your enrolled community',''));
    cfg.locations.filter(l=>l.district===district).forEach(l=>select.add(new Option(l.local_body+' · '+l.ward,l.location_id)));
    if(select.options.length===2){select.selectedIndex=1;select.dispatchEvent(new Event('change'));}
    updateRoutedDepartment();
  }
  window.pilotInitialLocation=async()=>{
    const params=new URLSearchParams(location.search);
    const district=params.get('district');
    const language=params.get('language');
    if(language&&Object.hasOwn(cfg.languages,language)){
      const chip=document.querySelector('[data-lang="'+language+'"]');
      if(chip)chip.click();
      const select=$('language');
      if(select&&select.value!==language){select.value=language;select.dispatchEvent(new Event('change'));}
    }
    if(!['Vellore','Tirupati'].includes(district))return;
    const state=district==='Vellore'?'Tamil Nadu':'Andhra Pradesh';$('state').value=state;
    await loadDistrictsForState(state);$('district').value=district;communities();
  };
  window.pilotReadEvidence=async(photos,files,captures)=>{
    if(photos.length+files.length+captures.length>15)throw new Error('Use no more than 15 evidence files');
    let total=0;
    const result=[];
    for(const file of [...photos,...files]){
      total+=file.size;if(file.size>5*1024*1024||total>8*1024*1024)throw new Error('Evidence limit: 5 MiB per file and 8 MiB total');
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result.split(',')[1]);reader.onerror=()=>reject(new Error('Could not read '+file.name));reader.readAsDataURL(file);});
      const ext=file.name.split('.').pop().toLowerCase(),mime=file.type||({pdf:'application/pdf',doc:'application/msword',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}[ext]);
      result.push({name:file.name,mime_type:mime,data});
    }
    captures.forEach((url,i)=>{const match=/^data:(image\/(?:jpeg|png|webp));base64,(.+)$/.exec(url);if(!match)throw new Error('Invalid camera capture');total+=match[2].length*.75;result.push({name:'camera-'+(i+1)+'.jpg',mime_type:match[1],data:match[2]});});
    if(total>8*1024*1024)throw new Error('Evidence exceeds 8 MiB total');
    return result;
  };
  document.addEventListener('DOMContentLoaded',()=>{
    const aiStatus=cfg.ai_preview;
    for(const [id,text] of [['pilotAiBadge',aiStatus?.label],['pilotAiCardBadge',aiStatus?.status==='available'?'Regional Gemini':'Officer review fallback']]){
      const badge=$(id);if(!badge||!text)continue;
      badge.textContent=text;
      if(aiStatus.notice)badge.title=aiStatus.notice;
    }
    $('district').addEventListener('change',communities);
    $('state').addEventListener('change',()=>{$('pilotLocation').replaceChildren(new Option('Select your district first',''));});
    $('pilotLocation').addEventListener('change',()=>{const l=cfg.locations.find(l=>l.location_id===$('pilotLocation').value);if(l){$('ward').value=l.ward;$('wardStatus').textContent='Enrolled community mapping. Add a landmark in your description.';}});
    for(const option of [...$('category').options])if(!cfg.categories.includes(option.value))option.remove();
    for(const chip of document.querySelectorAll('[data-lang]'))if(!Object.hasOwn(cfg.languages,chip.dataset.lang))chip.hidden=true;
    const language=new URLSearchParams(location.search).get('language')||Object.keys(cfg.languages)[0];
    if(language&&Object.hasOwn(cfg.languages,language)){
      setTimeout(()=>{
        document.querySelector('[data-lang="'+language+'"]')?.click();
        const select=$('language');
        if(select&&select.value!==language){select.value=language;select.dispatchEvent(new Event('change'));}
      },0);
    }
    $('consentGranted').addEventListener('change',()=>{if($('consentGranted').checked)triggerAutoTranslateAndClassify();});
  });
})();
