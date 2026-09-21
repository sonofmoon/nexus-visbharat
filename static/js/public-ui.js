(() => {
  'use strict';

  const themeQuery = window.matchMedia('(prefers-color-scheme: dark)');
  const motionQuery = window.matchMedia('(prefers-reduced-motion: reduce)');
  let savedTheme;
  try { savedTheme = localStorage.getItem('nvb_theme'); } catch {}
  const root = document.documentElement;
  root.dataset.publicTheme = ['light', 'dark'].includes(savedTheme) ? savedTheme : themeQuery.matches ? 'dark' : 'light';

  document.addEventListener('DOMContentLoaded', () => {
    if (!document.body.classList.contains('public-page') && !document.body.classList.contains('pilot-workspace')) return;

    const themeButton = document.querySelector('[data-public-theme-toggle]');
    const updateThemeButton = () => {
      const dark = root.dataset.publicTheme === 'dark';
      themeButton?.setAttribute('aria-label', `Switch to ${dark ? 'light' : 'dark'} theme`);
      if (themeButton) themeButton.innerHTML = `<i class="bi bi-${dark ? 'sun' : 'moon'}" aria-hidden="true"></i>`;
    };
    updateThemeButton();
    themeButton?.addEventListener('click', () => {
      root.dataset.publicTheme = root.dataset.publicTheme === 'dark' ? 'light' : 'dark';
      savedTheme = root.dataset.publicTheme;
      try { localStorage.setItem('nvb_theme', savedTheme); } catch {}
      updateThemeButton();
    });
    themeQuery.addEventListener('change', event => {
      if (['light', 'dark'].includes(savedTheme)) return;
      root.dataset.publicTheme = event.matches ? 'dark' : 'light';
      updateThemeButton();
    });

    const menuButton = document.querySelector('[data-public-menu]');
    const navigation = document.getElementById('public-navigation');
    const setMenu = expanded => {
      navigation?.classList.toggle('is-open', expanded);
      menuButton?.setAttribute('aria-expanded', String(expanded));
      menuButton?.setAttribute('aria-label', expanded ? 'Close navigation' : 'Open navigation');
    };
    menuButton?.addEventListener('click', () => setMenu(menuButton.getAttribute('aria-expanded') !== 'true'));
    document.addEventListener('click', event => {
      if (!event.target.closest('.public-header')) setMenu(false);
    });
    navigation?.addEventListener('click', event => {
      if (event.target.closest('a')) setMenu(false);
    });
    window.matchMedia('(max-width: 768px)').addEventListener('change', () => setMenu(false));

    const trackInput = document.getElementById('trackTicketInput') || document.getElementById('trackRequestIdInput');
    trackInput?.addEventListener('keydown', event => {
      if (event.key !== 'Enter') return;
      event.preventDefault();
      if (trackInput.id === 'trackTicketInput') window.trackTicketProgress?.();
      else if(document.getElementById('pilotTrackForm')) document.getElementById('pilotTrackForm').requestSubmit();
      else document.getElementById('trackStatusBtn')?.click();
    });

    const recorder = document.getElementById('recordBtn');
    if (recorder) {
      const updateRecorder = () => {
        const recording = recorder.classList.contains('recording');
        recorder.setAttribute('aria-pressed', String(recording));
        recorder.setAttribute('aria-label', recording ? 'Stop voice recording' : 'Start voice recording');
      };
      new MutationObserver(updateRecorder).observe(recorder, { attributes: true, attributeFilter: ['class'] });
      updateRecorder();
    }
    const success = document.getElementById('successMessage');
    if (success) {
      new MutationObserver(() => {
        if (!success.classList.contains('hidden')) success.focus();
      }).observe(success, { attributes: true, attributeFilter: ['class'] });
    }

    const modalIds = ['ivrModalOverlay', 'whatsAppModalOverlay', 'smsModalOverlay', 'emailModalOverlay'];
    let activeModal = null;
    let returnFocus = null;
    const backgroundNodes = [...document.querySelectorAll('.public-header, .public-main, .public-footer, #dfcxVoiceWidgetWrapper')];
    const inertStates = new Map();
    let previousOverflow = '';
    modalIds.forEach(modalId => {
      const overlay = document.getElementById(modalId);
      if (!overlay) return;
      overlay.classList.add('public-legacy', 'public-legacy-modal');
      const dialog = overlay.firstElementChild;
      const heading = dialog.querySelector('h3');
      if (heading) {
        heading.id ||= `${modalId}-title`;
        dialog.setAttribute('aria-labelledby', heading.id);
      }
      dialog.setAttribute('role', 'dialog');
      dialog.setAttribute('aria-modal', 'true');
      const closeButton = dialog.querySelector('button[onclick^="close"]');
      closeButton?.setAttribute('aria-label', 'Close dialog');
      new MutationObserver(() => {
        const open = getComputedStyle(overlay).display !== 'none';
        if (open && activeModal !== overlay) {
          activeModal = overlay;
          returnFocus = document.activeElement;
          previousOverflow = document.body.style.overflow;
          document.body.style.overflow = 'hidden';
          backgroundNodes.forEach(node => { inertStates.set(node, node.inert); node.inert = true; });
          closeButton?.focus();
        } else if (!open && activeModal === overlay) {
          activeModal = null;
          document.body.style.overflow = previousOverflow;
          backgroundNodes.forEach(node => { node.inert = inertStates.get(node) || false; });
          returnFocus?.focus();
        }
      }).observe(overlay, { attributes: true, attributeFilter: ['style'] });
    });
    document.addEventListener('keydown', event => {
      if (activeModal) {
        if (event.key === 'Escape') {
          event.preventDefault();
          activeModal.querySelector('button[onclick^="close"]')?.click();
        }
        if (event.key === 'Tab') {
          const focusable = [...activeModal.querySelectorAll('a[href], button, input, select, textarea, [tabindex="0"]')].filter(element => !element.disabled && element.getClientRects().length);
          const first = focusable[0];
          const last = focusable.at(-1);
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      } else if (event.key === 'Escape' && menuButton?.getAttribute('aria-expanded') === 'true') {
        setMenu(false);
        menuButton.focus();
      }
    });

    const legacyLabels = {
      ivrPhoneInput: 'Callback phone number', ivrLangSelect: 'Callback language', ivrDistrictSelect: 'Callback district',
      waPhoneInput: 'WhatsApp phone number', waTextInput: 'WhatsApp request', emailTextInput: 'Email request',
      smsPhoneInput: 'SMS phone number', smsTextInput: 'SMS request', dfcxInputText: 'Message the voice assistant', dfcxMicBtn: 'Record assistant message'
    };
    Object.entries(legacyLabels).forEach(([identifier, label]) => document.getElementById(identifier)?.setAttribute('aria-label', label));
    document.querySelectorAll('.public-track-result').forEach(node => node.classList.add('public-legacy'));
    const assistant = document.getElementById('dfcxAssistantWindow');

    if (assistant) {
      assistant.setAttribute('role', 'region');
      assistant.setAttribute('aria-label', 'NVB voice assistant');
      const toggle = document.getElementById('dfcxWidgetToggleBtn');
      toggle?.setAttribute('aria-label', 'Talk to the NVB voice assistant');
      toggle?.setAttribute('aria-controls', assistant.id);
      toggle?.setAttribute('aria-expanded', 'false');
      assistant.querySelector('button[onclick="toggleDfcxAssistant()"]')?.setAttribute('aria-label', 'Close voice assistant');
      assistant.querySelector('button[onclick="sendDfcxMessage()"]')?.setAttribute('aria-label', 'Send assistant message');
      document.getElementById('dfcxMessageStream')?.setAttribute('role', 'log');
      new MutationObserver(() => toggle?.setAttribute('aria-expanded', String(getComputedStyle(assistant).display !== 'none'))).observe(assistant, { attributes: true, attributeFilter: ['style'] });
      assistant.addEventListener('keydown', event => {
        if (event.key === 'Escape') { window.toggleDfcxAssistant?.(); toggle?.focus(); }
      });
    }

    document.querySelectorAll('.public-metric-value').forEach(element => {
      const original = element.textContent.trim();
      const target = Number.parseFloat(original.replaceAll(',', ''));
      if (!Number.isFinite(target)) return;
      const precision = original.includes('.') ? original.split('.')[1].replace(/\D/g, '').length : 0;
      const suffix = original.endsWith('%') ? '%' : '';
      const format = value => value.toLocaleString(undefined, { minimumFractionDigits: precision, maximumFractionDigits: precision }) + suffix;
      const formatted = format(target);
      element.textContent = formatted;
      element.setAttribute('aria-label', formatted);
      if (motionQuery.matches || target <= 0) return;
      const started = performance.now();
      const frame = now => {
        const progress = Math.min((now - started) / 700, 1);
        element.textContent = progress === 1 ? formatted : format(target * (1 - Math.pow(1 - progress, 3)));
        if (progress < 1 && !motionQuery.matches) requestAnimationFrame(frame);
        else element.textContent = formatted;
      };
      requestAnimationFrame(frame);
    });
  });
})();
