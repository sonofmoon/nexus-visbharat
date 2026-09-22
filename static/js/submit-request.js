(() => {
  'use strict';

  let submitting = false;
  let attempt = null;
  let pendingEvidence = null;
  let evidenceInFlight = false;
  const pilotReceiptCode=window.NVBPilotPublic?Array.from(crypto.getRandomValues(new Uint8Array(32)),x=>x.toString(16).padStart(2,'0')).join(''):null;
  const element = identifier => document.getElementById(identifier);

  function showError(message, voiceAvailable = false) {
    element('submissionErrorText').textContent = message;
    element('submissionError').classList.remove('hidden');
    element('submitTextInsteadBtn').classList.toggle('hidden', !voiceAvailable);
    element('submissionError').focus();
  }

  async function postJson(endpoint, body, key, timeout = 90000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await fetch(endpoint, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...(key ? { 'X-Idempotency-Key': key, 'Idempotency-Key': key } : {}) },
        body,
        signal: controller.signal
      });
      let data;
      try { data = await response.json(); } catch {
        const message = response.status === 413
          ? 'This request is too large. Remove some attachments or use a shorter recording.'
          : `The server returned an unreadable response (HTTP ${response.status}). Your form is still here; please retry.`;
        throw new Error(message);
      }
      if (!response.ok || data.success === false) {
        throw new Error(data.error || data.issues?.[0] || `Request failed (HTTP ${response.status}). Please try again.`);
      }
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('The server is taking longer than expected. Your form is still here. Retry without changing it to check the same submission.');
      if (error instanceof TypeError) throw new Error('Could not reach the server. Check your connection and retry; your form has not been cleared.');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  async function saveEvidence() {
    if (!pendingEvidence || evidenceInFlight) return;
    evidenceInFlight = true;
    const button = element('retryAttachmentsBtn');
    const notice = element('submissionAttachmentNotice');
    button.disabled = true;
    notice.classList.remove('hidden');
    notice.textContent = 'Your request is saved. Registering the evidence manifest…';
    try {
      const data = await postJson('/api/attachments/ingest-stub', JSON.stringify(pendingEvidence), null, 30000);
      if (!data.validated) throw new Error(data.issues?.[0] || 'Evidence registration was not confirmed.');
      pendingEvidence = null;
      notice.textContent = 'Your request and evidence manifest have been accepted.';
      button.classList.add('hidden');
    } catch (error) {
      notice.textContent = `Your request is saved, but evidence registration failed: ${error.message} Retry evidence only; do not submit another request.`;
      button.classList.remove('hidden');
    } finally {
      button.disabled = false;
      evidenceInFlight = false;
    }
  }

  function showSavedRequest(data, payload) {
    element('complaintId').textContent = data.request_id;
    const proof = document.getElementById('submissionEvidenceLink');
    if (proof) proof.href = '/submission?ticket=' + encodeURIComponent(data.request_id);
    element('successWard').textContent = data.routing?.ward || payload.ward || 'Pending assignment';
    element('successDept').textContent = data.routing?.department || payload.department || 'Pending routing';
    element('trackRequestIdInput').value = data.request_id;
    element('complaintForm').classList.add('hidden');
    element('successMessage').classList.remove('hidden');
    element('successMessage').focus();
    if(window.NVBPilotPublic){
      element('pilotReceiptSecret').textContent=data.tracking_secret||'This request was already received. Use the private code from its original receipt.';
      element('trackSecret').value=data.tracking_secret||'';
      element('successDept').textContent=window.NVBPilotPublic.routing[payload.district]||'District review team';
    }
    if (payload.attachment_count > 0 && !window.NVBPilotPublic) {
      pendingEvidence = {
        request_id: data.request_id,
        attachment_manifest: payload.attachment_manifest,
        camera_captures_base64: payload.camera_captures_base64
      };
    }
  }

  window.submitComplaint = async event => {
    event.preventDefault();
    if (submitting || !element('successMessage').classList.contains('hidden')) return;
    const form = element('complaintForm');
    if (!form.reportValidity()) return;
    element('submissionError').classList.add('hidden');
    const button = element('submitBtn');
    const status = element('submissionStatus');
    let slowTimer;
    let isVoice = false;
    try {
      if (recording || voiceFinalizing) throw new Error('Stop recording and wait for transcription to finish before submitting.');
      isVoice = Boolean(lastVoiceAudioBase64.trim());
      const text = getCurrentRequestText();
      if (!text && !isVoice) throw new Error('Describe your request or record a voice note before submitting.');
      const photos = Array.from(element('photo-attachments').files || []);
      const files = Array.from(element('file-attachments').files || []);
      const captures = JSON.parse(element('camera-captures-json').value || '[]');
      if ([photos, files, captures].some(items => items.length > 20)) throw new Error('Use no more than 20 photos, 20 documents, and 20 camera captures per request.');
      if (captures.some(capture => typeof capture !== 'string' || !capture.trim() || capture.length > 8000000)) throw new Error('A camera capture is too large or invalid. Clear the captures and try again.');
      const maxAudio = Number(form.dataset.maxAudioBytes);
      if (isVoice && Math.floor(lastVoiceAudioBase64.replace(/=+$/, '').length * 3 / 4) > maxAudio) throw new Error('The recording is too large. Make a shorter recording or submit the written text instead.');
      const latitude = element('location-lat').value.trim();
      const longitude = element('location-lng').value.trim();
      const location = latitude && longitude ? { lat: Number(latitude), lng: Number(longitude) } : null;
      if (location && (!Number.isFinite(location.lat) || !Number.isFinite(location.lng) || Math.abs(location.lat) > 90 || Math.abs(location.lng) > 180)) throw new Error('The captured location is invalid. Please detect your location again.');
      const payload = {
        ward: element('ward').value.trim(),
        category: element('category').value || 'Other',
        urgency: element('urgency').value || 'Routine',
        urgency_source: element('urgencySource').value || 'ai',
        department: element('routedDepartment').value.trim(),
        consent_granted: element('consentGranted').checked,
        consent_scope: element('consentScope').value || 'request_processing',
        text,
        language: element('language').value,
        district: element('district').value,
        state: element('state').value,
        source: isVoice ? 'Voice IVR' : 'Web Form',
        is_voice: isVoice,
        reviewed_transcript: isVoice ? text : undefined,
        reviewed_translation: element('translated-text-box').value.trim(),
        lat: location?.lat ?? null,
        lng: location?.lng ?? null,
        location,
        attachment_names: [...photos, ...files].map(file => file.name).concat(captures.map((capture, index) => `camera-capture-${index + 1}.jpg`)),
        attachment_count: photos.length + files.length + captures.length,
        camera_captures_base64: captures,
        attachment_manifest: {
          photos: photos.map(file => ({ name: file.name, type: file.type || 'image/*', source: 'photos' })),
          files: files.map(file => ({ name: file.name, type: file.type || 'application/octet-stream', source: 'files' })),
          camera_captures: captures.map((capture, index) => ({ name: `camera-capture-${index + 1}.jpg`, type: 'image/jpeg', source: 'camera' })),
          google_ready: true
        },
        ...(isVoice ? { audio_base64: lastVoiceAudioBase64, audio_mime_type: lastVoiceMimeType || 'audio/webm' } : {})
      };
      if(window.NVBPilotPublic){
        payload.tracking_secret=pilotReceiptCode;
        payload.location_id=element('pilotLocation').value;
        payload.mime_type=lastVoiceMimeType||'audio/webm';
        payload.translated_text=element('translated-text-box').value.trim();
        payload.evidence=await window.pilotReadEvidence(photos,files,captures);
        delete payload.camera_captures_base64;
      }
      const endpoint = window.NVBPilotPublic?'/api/v2/pilot/intake':isVoice ? '/api/submit-voice' : '/api/submit';
      const body = JSON.stringify(payload);
      if (new Blob([body]).size > Number(form.dataset.maxRequestBytes)) throw new Error('This request is too large. Remove some camera captures or use a shorter recording.');
      if (!attempt || attempt.endpoint !== endpoint || attempt.body !== body) {
        const key = window.crypto.randomUUID ? window.crypto.randomUUID() : Array.from(window.crypto.getRandomValues(new Uint32Array(4)), value => value.toString(16)).join('-');
        attempt = { endpoint, body, key };
      }
      submitting = true;
      button.disabled = true;
      button.textContent = 'Submitting request…';
      form.setAttribute('aria-busy', 'true');
      status.textContent = 'Saving your request and preparing its routing. Please keep this page open.';
      slowTimer = setTimeout(() => { status.textContent = 'Still processing. Your form is safe; please do not submit a second request.'; }, 8000);
      const data = await postJson(endpoint, body, attempt.key);
      if (!data.success || !data.request_id) throw new Error('The server did not confirm a request ID. Retry without changing the form to check the same submission.');
      if (isVoice && data.transcript) {
        element('voice-transcript').textContent = data.transcript;
        element('complaint-text').value = data.transcript;
        updateVoiceStatus('Voice request accepted.', false);
      }
      showSavedRequest(data, payload);
      await saveEvidence();
    } catch (error) {
      showError(error.message || 'Could not submit your request. Your form has been preserved.', isVoice && Boolean(element('complaint-text').value.trim()));
    } finally {
      clearTimeout(slowTimer);
      submitting = false;
      button.disabled = false;
      button.textContent = 'Submit request';
      form.setAttribute('aria-busy', 'false');
      status.textContent = '';
    }
  };

  element('retryAttachmentsBtn').addEventListener('click', saveEvidence);
  element('submitTextInsteadBtn').addEventListener('click', () => {
    lastVoiceAudioBase64 = '';
    showError('The recording will not be sent. Review your written request, then select Submit request.');
    element('complaint-text').focus();
  });
})();
