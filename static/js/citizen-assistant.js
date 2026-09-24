(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const micButton = $('dfcxMicBtn');
  if (micButton && !$('dfcxMicLabel')) {
    const label = document.createElement('span');
    label.id = 'dfcxMicLabel';
    label.textContent = 'Record voice';
    micButton.append(label);
  }
  if ($('dfcxAssistantWindow') && !$('dfcxMicHint')) {
    const hint = document.createElement('p');
    hint.id = 'dfcxMicHint';
    hint.className = 'nvb-assistant-mic-hint';
    hint.textContent = 'Tap Record voice to start. Tap Stop recording when finished.';
    $('dfcxAssistantWindow').insertBefore(hint, $('dfcxInputForm').nextSibling);
  }
  let strings = {}, config, language = 'en', current, sessionId = '', token = '', busy = false, opened = false;
  let pending = null, muted = false, lastReply = '', audio = null, audioEpoch = 0, audioAbort = null;
  let recorder = null, media = null, recognition = null, recordingEpoch = 0, recordingTimer = null, inputMode = 'text';
  const locale = {en:'en-IN',ta:'ta-IN',te:'te-IN'};
  const t = key => strings[language]?.[key] || strings.en?.[key] || key;
  const id = () => crypto.randomUUID().replaceAll('-', '');
  const status = key => { $('dfcxStatus').textContent = t(key); };
  const boot = Promise.all([
    fetch('/static/data/assistant_i18n.json').then(r => { if (!r.ok) throw Error(); return r.json(); }),
    fetch('/api/dialogflow/config').then(r => { if (!r.ok) throw Error(); return r.json(); })
  ]).then(([translations, settings]) => { strings = translations; config = settings; localize(); });
  // Attach a handler immediately so a failed initial request cannot become an unhandled rejection.
  boot.catch(() => { $('dfcxStatus').textContent = 'Assistant could not load. Reload the page to retry.'; });

  function localize() {
    $('dfcxAssistantWindow').lang = language;
    document.querySelectorAll('[data-assistant-text]').forEach(el => { el.textContent = t(el.dataset.assistantText); });
    document.querySelectorAll('[data-assistant-label]').forEach(el => { el.setAttribute('aria-label',t(el.dataset.assistantLabel)); });
    $('dfcxInputText').placeholder = t('placeholder');
    $('dfcxInputText').setAttribute('aria-label',t('placeholder'));
    $('dfcxDistrict').setAttribute('aria-label',t('district'));
    $('dfcxMute').textContent = t(muted ? 'unmute' : 'mute');
    $('dfcxMute').setAttribute('aria-pressed',String(muted));
    const micLabel = $('dfcxMicLabel');
    if (micLabel) micLabel.textContent = $('dfcxMicBtn').getAttribute('aria-pressed') === 'true' ? t('stop') : t('record');
    const micHint = $('dfcxMicHint');
    if (micHint) micHint.textContent = t('record_hint') === 'record_hint' ? 'Tap Record voice to start. Tap Stop recording when finished.' : t('record_hint');
    const selected = $('dfcxDistrict').value;
    $('dfcxDistrict').replaceChildren(new Option(t('select'), ''));
    Object.entries(config?.districts || {}).forEach(([state,districts]) => {
      const group = document.createElement('optgroup'); group.label = state;
      districts.forEach(district => group.append(new Option(district,district)));
      $('dfcxDistrict').append(group);
    });
    $('dfcxDistrict').value = selected;
    status('ready');
  }
  function append(text, sender='bot') {
    const el = document.createElement('div'); el.className = `dfcx-msg-${sender}`;
    el.textContent = text; el.lang = language; $('dfcxMessageStream').append(el);
    $('dfcxScrollable').scrollTop = $('dfcxScrollable').scrollHeight;
  }
  function setBusy(value) {
    busy = value;
    for (const name of ['dfcxSend','dfcxMicBtn','dfcxLanguage','dfcxNew','dfcxTrack','dfcxCancel','dfcxUseLocation','dfcxConfirm','dfcxEditIssue','dfcxEditLocation','dfcxRetry']) $(name).disabled = value;
    $('dfcxInputForm').setAttribute('aria-busy', String(value));
  }
  function setMicState(recording) {
    const button = $('dfcxMicBtn');
    const label = $('dfcxMicLabel');
    button.setAttribute('aria-pressed', String(recording));
    button.setAttribute('aria-label', t(recording ? 'stop' : 'record'));
    button.title = t(recording ? 'stop' : 'record');
    if (label) label.textContent = t(recording ? 'stop' : 'record');
  }
  function setStatusText(message) { $('dfcxStatus').textContent = message; }
  function explainError(data, fallback) { return String(data?.error || data?.message || fallback || t('network')); }
  function saveSession() {
    try { sessionStorage.setItem('nvb_assistant_session', JSON.stringify({sessionId,token,language})); } catch {}
  }
  function render(sess) {
    current = sess;
    document.querySelectorAll('#dfcxSteps li').forEach(el => {
      if (el.dataset.stage === sess.session_state) el.setAttribute('aria-current','step');
      else el.removeAttribute('aria-current');
    });
    $('dfcxLocationPanel').hidden = sess.session_state !== 'collect_location' || !!sess.draft?.tracking_pending;
    $('dfcxReviewPanel').hidden = sess.session_state !== 'collect_confirmation' || !!sess.draft?.tracking_pending;
    $('dfcxCancel').hidden = ['complete','cancelled'].includes(sess.session_state);
    if (['complete','cancelled'].includes(sess.session_state)) {
      try { sessionStorage.removeItem('nvb_assistant_session'); } catch {}
      sessionId = ''; token = '';
    }
    $('dfcxSummary').textContent = [sess.draft?.text,sess.draft?.district,sess.draft?.ward].filter(Boolean).join('\n');
    if (sess.draft?.district) $('dfcxDistrict').value = sess.draft.district;
    $('dfcxWard').value = sess.draft?.ward || '';
    $('dfcxFlowBadge').textContent = t(sess.flow === 'dialogflow_cx_live' ? 'live' : sess.flow === 'local_guided_fallback' ? 'fallback' : 'unchecked');
    $('dfcxIntentBadge').textContent = [sess.intent, Number.isFinite(sess.confidence) ? `NLU ${Math.round(sess.confidence*100)}%` : '', Number.isFinite(sess.latency_ms) ? `${sess.latency_ms} ms` : '',sess.provider_error || '',sess.receipt?.cluster_id || '',sess.receipt?.processing ? JSON.stringify(sess.receipt.processing) : ''].filter(Boolean).join(' · ');
    const card = $('dfcxReceipt'); card.replaceChildren(); card.hidden = !sess.receipt;
    if (sess.receipt) {
      const receipt = sess.receipt;
      const title = document.createElement('strong'); title.textContent = receipt.request_id; card.append(title);
      for (const value of [t('status_'+receipt.status.toLowerCase().replaceAll(' ','_')).startsWith('status_') ? receipt.status : t('status_'+receipt.status.toLowerCase().replaceAll(' ','_')),[receipt.district,receipt.ward].filter(Boolean).join(' · '),receipt.category,receipt.routed_department]) {
        if (value) { const p = document.createElement('p');p.textContent=value;card.append(p); }
      }
      const track = document.createElement('button');track.textContent=t('track');track.addEventListener('click',() => send('track',{message:receipt.request_id}));card.append(track);
      const detail = document.createElement('p');detail.textContent=t('impact');card.append(detail);
      const link = document.createElement('a');link.href=receipt.planning_url;link.textContent=t('walkthrough');card.append(link);
    }
  }
  async function send(action='message', extra={}, retry=false) {
    if (busy) return;
    await boot;
    if (busy) return;
    stopAudio();
    stopRecording(true);
    const message = extra.message ?? (action === 'message' ? $('dfcxInputText').value.trim() : '');
    if (action === 'message' && !message && !retry) return;
    if (!retry) {
      pending = {action,language,message,turn_id:id(),...extra};
      if (sessionId) Object.assign(pending,{session_id:sessionId,version:current?.version});
      if (action === 'message' || action === 'confirm') {
        pending.consent_granted = $('dfcxConsent').checked;
        pending.input_mode = inputMode;
      }
      if (message && action === 'message') { append(message,'user');$('dfcxInputText').value='';inputMode='text'; }
    }
    if (!pending) return;
    setBusy(true);status('processing');$('dfcxRetry').hidden=true;
    if (action === 'confirm') $('dfcxConfirm').textContent = 'Submitting request...';
    const abort = new AbortController();const timer = setTimeout(() => abort.abort(),45000);
    try {
      const res = await fetch('/api/dialogflow/session',{method:'POST',headers:{'Content-Type':'application/json','X-NVB-Assistant-Token':token},body:JSON.stringify(pending),signal:abort.signal});
      const data = await res.json();
      if (!res.ok || !data.success) {
        if (['session_expired','session_unavailable'].includes(data.code)) {
          sessionId='';token='';current=null;saveSession();pending=null;append(t('expired'));status('expired');return;
        }
        if (data.code === 'stale_session') {
          pending={action:'resume',language,session_id:sessionId,turn_id:id()};
        }
        const message = explainError(data, t('network'));
        append(message, 'bot');
        setStatusText(message);
        throw Error(message);
      }
      sessionId=data.session.session_id;token=data.session_token || token;saveSession();
      render(data.session);append(data.session.next_prompt);lastReply=data.session.next_prompt;
      pending=null;status('ready');
      if (opened && !muted) speak(lastReply);
    } catch (err) {
      const message = String(err?.message || '');
      if (!message || message === t('network')) status('network'); else setStatusText(message);
      $('dfcxRetry').hidden=false;
    } finally {
      clearTimeout(timer);setBusy(false);
      if (action === 'confirm' && $('dfcxConfirm')) $('dfcxConfirm').textContent = t('confirm');
      if (opened) $('dfcxInputText').focus();
    }
  }
  function stopAudio() {
    audioEpoch++;audioAbort?.abort();audioAbort=null;
    if (audio) { audio.pause();audio.src='';audio=null; }
    window.speechSynthesis?.cancel();
  }
  async function speak(text) {
    stopAudio();if (!text || muted || !opened) return;
    const epoch=audioEpoch;audioAbort=new AbortController();
    const timeout=setTimeout(() => audioAbort?.abort(),12000);
    try {
      const res=await fetch('/api/tts/synthesize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text,language}),signal:audioAbort.signal});
      const data=await res.json();
      if (epoch !== audioEpoch || !opened || muted) return;
      if (!res.ok || !data.success || !data.tts?.audio_base64) throw Error();
      audio=new Audio(`data:${data.tts.audio_mime_type || 'audio/mpeg'};base64,${data.tts.audio_base64}`);
      audio.onended=() => { if(epoch===audioEpoch) status('ready'); };
      audio.onerror=() => { if(epoch===audioEpoch) status('audio_error'); };
      await audio.play();if(epoch===audioEpoch) status('speaking');
    } catch {
      if(epoch!==audioEpoch || !opened || muted) return;
      if(!window.speechSynthesis) { status('audio_error');return; }
      const utterance=new SpeechSynthesisUtterance(text);utterance.lang=locale[language];
      const voices=window.speechSynthesis.getVoices();const voice=voices.find(v => v.lang.toLowerCase().startsWith(language));
      if(voice) utterance.voice=voice;
      utterance.onend=() => { if(epoch===audioEpoch) status('ready'); };
      utterance.onerror=() => { if(epoch===audioEpoch) status('audio_error'); };
      window.speechSynthesis.speak(utterance);status('speaking');
    } finally { clearTimeout(timeout); }
  }
  function stopRecording(discard=false) {
    if(discard) recordingEpoch++;
    clearTimeout(recordingTimer);
    if(recorder && recorder.state!=='inactive') recorder.stop();
    if(recognition) { discard ? recognition.abort() : recognition.stop();recognition=null; }
    if (!recorder || recorder.state === 'inactive') { media?.getTracks().forEach(track => track.stop()); media=null; }
    setMicState(false);
  }
  async function record() {
    if(busy) return;
    if((recorder && recorder.state==='recording') || recognition) { stopRecording();return; }
    stopAudio();const epoch=++recordingEpoch;
    if(!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      const Recognition=window.SpeechRecognition || window.webkitSpeechRecognition;
      if(!Recognition) { status('mic_error');return; }
      recognition=new Recognition();recognition.lang=locale[language];recognition.continuous=false;
      recognition.onresult=event => { if(epoch===recordingEpoch && opened) { $('dfcxInputText').value=event.results[0][0].transcript;inputMode='voice';status('transcribed'); } };
      recognition.onerror=() => { if(epoch===recordingEpoch) status('mic_error'); };
      recognition.onend=() => { recognition=null;setMicState(false); };
      try { recognition.start();status('listening');setMicState(true); } catch { recognition=null;setMicState(false);status('mic_error'); }
      return;
    }
    try {
      media=await navigator.mediaDevices.getUserMedia({audio:true});
      if(epoch!==recordingEpoch || !opened) { media.getTracks().forEach(track => track.stop());media=null;return; }
      const mime=['audio/webm;codecs=opus','audio/webm','audio/ogg;codecs=opus','audio/ogg'].find(type => MediaRecorder.isTypeSupported(type));
      if (!mime) { media.getTracks().forEach(track => track.stop()); media=null; status('mic_error'); return; }
      recorder=new MediaRecorder(media,mime ? {mimeType:mime} : undefined);
      const chunks=[];let size=0;
      recorder.ondataavailable=event => { if(event.data.size) { chunks.push(event.data);size+=event.data.size;if(size>3*1024*1024) stopRecording(); } };
      recorder.onstop=async () => {
        media?.getTracks().forEach(track => track.stop());media=null;
        const recorderMime = recorder?.mimeType || mime;
        if(epoch!==recordingEpoch || !opened) return;
        if(size>3*1024*1024 || !size) { status('asr_error');return; }
        setBusy(true);status('processing');
        recorder=null;
        const blob=new Blob(chunks,{type:recorderMime});
        const reader=new FileReader();
        const controller=new AbortController();const timeout=setTimeout(() => controller.abort(),30000);
        try {
          const encoded=await new Promise((resolve,reject) => { reader.onload=() => resolve(reader.result.split(',')[1]);reader.onerror=reject;reader.readAsDataURL(blob); });
          const res=await fetch('/api/transcribe-voice',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({audio_base64:encoded,audio_mime_type:blob.type,language,assistant:true}),signal:controller.signal});
          const data=await res.json();
          if(!res.ok || !data.success) throw Error(explainError(data, 'Speech could not be transcribed.'));
          if(epoch===recordingEpoch && opened) { $('dfcxInputText').value=data.transcript;inputMode='voice';status('transcribed');$('dfcxInputText').focus(); }
        } catch (error) { if(epoch===recordingEpoch && opened) setStatusText(error.message || t('asr_error')); }
        finally {clearTimeout(timeout);setBusy(false);}
      };
      recorder.start(250);status('listening');setMicState(true);
      recordingTimer=setTimeout(() => stopRecording(),45000);
    } catch {stopRecording(true);status('mic_error');}
  }
  function resetAssistant(startFresh = true) {
    stopRecording(true);
    stopAudio();
    sessionId = '';
    token = '';
    current = null;
    pending = null;
    try { sessionStorage.removeItem('nvb_assistant_session'); } catch {}
    $('dfcxConsent').checked = false;
    $('dfcxInputText').value = '';
    $('dfcxMessageStream').replaceChildren();
    $('dfcxLocationPanel').hidden = true;
    $('dfcxReviewPanel').hidden = true;
    const card = $('dfcxReceipt');
    card.replaceChildren();
    card.hidden = true;
    $('dfcxCancel').hidden = true;
    document.querySelectorAll('#dfcxSteps li').forEach((el, idx) => {
      if (idx === 0) el.setAttribute('aria-current', 'step');
      else el.removeAttribute('aria-current');
    });
    status('ready');
    if (startFresh) send('start');
  }

  window.toggleDfcxAssistant = async () => {
    opened = !opened;
    $('dfcxAssistantWindow').style.display = opened ? 'flex' : 'none';
    $('dfcxWidgetToggleBtn').style.display = opened ? 'none' : 'flex';
    $('dfcxWidgetToggleBtn').setAttribute('aria-expanded', String(opened));
    if (!opened) {
      stopAudio();
      stopRecording(true);
      $('dfcxWidgetToggleBtn').focus();
      return;
    }
    $('dfcxInputText').focus();
    try {
      await boot;
      if (!current && !busy) {
        resetAssistant(false);
        try {
          const savedLang = localStorage.getItem('nvb_assistant_lang');
          if (savedLang && strings[savedLang]) language = savedLang;
        } catch {}
        $('dfcxLanguage').value = language;
        localize();
        await send('start');
      }
    } catch {
      $('dfcxStatus').textContent = 'Assistant could not load. Reload the page to retry.';
    }
  };
  // Keep the established global entry points for existing page integrations.
  window.sendDfcxMessage=() => send();window.toggleDfcxSpeechRecognition=record;
  $('dfcxInputForm').addEventListener('submit',event => {event.preventDefault();send();});
  $('dfcxMicBtn').addEventListener('click',record);
  $('dfcxStop').addEventListener('click',() => {stopAudio();stopRecording();status('ready');});
  $('dfcxReplay').addEventListener('click',() => speak(lastReply));
  $('dfcxMute').addEventListener('click',() => {muted=!muted;stopAudio();$('dfcxMute').textContent=t(muted?'unmute':'mute');$('dfcxMute').setAttribute('aria-pressed',String(muted));status('ready');});
  $('dfcxLanguage').addEventListener('change', () => {
    stopRecording(true);
    stopAudio();
    language = $('dfcxLanguage').value;
    try { localStorage.setItem('nvb_assistant_lang', language); } catch {}
    localize();
    if (sessionId) {
      saveSession();
      send('resume');
    }
  });
  $('dfcxUseLocation').addEventListener('click', () => send('location', {district: $('dfcxDistrict').value, ward: $('dfcxWard').value}));
  $('dfcxConfirm').addEventListener('click', () => send('confirm'));
  $('dfcxEditIssue').addEventListener('click', async () => { $('dfcxConsent').checked = false; const text = current?.draft?.text || ''; await send('edit_issue'); $('dfcxInputText').value = text; });
  $('dfcxEditLocation').addEventListener('click', () => { $('dfcxConsent').checked = false; send('edit_location'); });
  $('dfcxCancel').addEventListener('click', () => send('cancel'));
  $('dfcxTrack').addEventListener('click', () => send('track'));
  $('dfcxRetry').addEventListener('click', () => send('message', {}, true));
  $('dfcxNew').addEventListener('click', () => resetAssistant(true));
  window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && opened) window.toggleDfcxAssistant(); });
  window.addEventListener('pagehide',() => {stopAudio();stopRecording(true);});
})();
