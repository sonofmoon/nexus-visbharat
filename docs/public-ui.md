# NVB citizen experience

The homepage (`/`) and request page (`/submit`) share the policy console's visual language, while keeping their citizen-facing purpose. They remain server-rendered Flask/Jinja pages. No React runtime, bundler, new route, or backend dependency is introduced.

## Component ownership

| File | Responsibility |
| --- | --- |
| `templates/partials/public_head.html` | Shared fonts, theme stylesheet, presentation script |
| `templates/partials/public_nav.html` | Brand, active navigation, mobile menu, theme control, skip link |
| `templates/partials/public_footer.html` | Shared platform links and pilot context |
| `static/css/public.css` | Scoped tokens, layout, components, responsive and reduced-motion states |
| `static/js/public-ui.js` | Presentation only: theme, navigation, dialog focus, accessible states, counters |
| `static/js/submit-request.js` | Request serialization, idempotent retry, visible errors, and evidence registration |
| `templates/index.html` | Citizen overview, real metrics, tracking, channels, technical disclosures |
| `templates/submit.html` | Existing request form and controllers, with reorganized accessible markup |

## Visual system

- Google Sans, with Inter and system fallbacks; no content depends on web fonts loading.
- Console-aligned blue, neutral surfaces, success and error tokens; light and dark palettes.
- Shared `nvb_theme` storage key with the dashboard; the system preference is used when no theme is saved.
- 24 px card padding and grid gaps, 40–64 px between major groups, 12 px action gaps.
- 24 px cards, 16 px inset panels, pill actions, outlined language chips.
- Homepage: split hero, clearly illustrative request journey, actual server-rendered metrics, prominent tracker, six channels, process overview, expandable architecture and language roadmap.
- Request page: four named sections, contextual guidance, optional evidence disclosure, compact voice panel, editable routing/translation, clear consent and success states.
- Responsive behavior at 1440, 1024, and 768 px, with additional form and small-phone adjustments. Grid children use `minmax(0, 1fr)` rather than fixed minimum widths.
- 200 ms interaction transitions and reduced-motion support. Percentage counters retain their decimals and percent sign.

## Compatibility boundaries

Original interactive IDs, form names, category values, and API routes are retained. Submission now has a dedicated controller: both typed and voice requests carry ward, category, consent, and top-level `lat`/`lng` fields expected by the existing API. The legacy `location` object is also retained. Existing state/district loading, language switching, recording, translation, classification, GPS/ward suggestion, camera, consent, and ticket controllers remain in use.

Submission failures preserve the form and display an accessible inline error. Retrying an unchanged submission reuses the API's existing `X-Idempotency-Key` support. The backend registers that key before downstream cloud/cluster processing; BigQuery inserts now have a ten-second timeout with automatic retries disabled, using the existing dead-letter fallback on failure. Live AI requirements are unchanged; unavailable inference produces a JSON 503 rather than an HTML error page.

A saved request remains visibly successful if evidence registration fails. Its evidence can be retried without creating another request, and requests without evidence skip the attachment call. Recording must finish before submission; failed voice ingestion offers an explicit written-text alternative. The existing attachment endpoint is a manifest-validation stub: it registers metadata, not uploaded photo/document bytes. This repair does not add a file-storage backend.

All channel demos, live channel links, and the Dialogflow assistant remain available. The homepage's architecture and extended language content is disclosed progressively rather than removed. Metrics are still supplied by Flask; the hero illustration is not presented as live operational data.

Legacy demo and tracking HTML uses inline presentation styles. A narrowly scoped `.public-legacy` compatibility bridge adapts that markup without rewriting its integration logic. No dashboard stylesheet, role, or route is changed. The submission reliability repair changes only the existing submission handlers and cloud insert timeout, without adding or renaming endpoints.

## Accessibility and validation

The shared shell provides landmarks, a skip link, visible focus, current-page navigation, accessible icon labels, mobile-menu state, and reduced motion. Form fields have labels and contextual help. Recording state is exposed to assistive technology, microphone errors remain visible, and successful submission receives keyboard focus. Existing demo dialogs gain focus containment, background inertness, Escape dismissal, and focus restoration.

Run against the local Flask server:

```powershell
python scripts/check_public_ui.py
python scripts/check_public_ui.py --axe-path scratch/public-review/axe.min.js
python scripts/check_dashboard_ui.py
python -m unittest tests.test_submit_flow -v
```

The optional axe-core argument accepts a locally available audit script; axe-core is not shipped to users. Automated checks supplement, rather than replace, manual screen-reader and real-device testing.

Public UI checks cover 320–1920 px layouts, light/dark themes, tracking, modal focus, dependent selections, languages, classification, GPS, camera, attachments, typed and voice success, submission errors, microphone denial, and reduced motion. All mutating API calls are intercepted with fixtures. Tests never create real requests, place calls, or send external messages. Screenshots and reports are written to `scratch/public-review/`.

Production cloud service availability, real telephony delivery, consent-policy enforcement, and backend security remain the responsibility of the existing integrations; passing UI tests does not certify those systems.

`tests/test_submit_flow.py` additionally exercises the real Flask routes, SQL persistence, consent ledger, routing, and tracking in a temporary database, through both Chromium and Flask's test client. Only external AI/ASR providers and deliberately injected network failures use fixtures. It covers typed/voice saves, GPS persistence, consent preservation, lost responses, duplicate prevention, attachment failures, invalid evidence, recording state, explicit voice-to-text fallback, non-JSON errors, AI outages, post-save failures, and bounded cloud inserts. Playwright/Chromium are optional test prerequisites; the suite skips when they are unavailable. No live citizen records or cloud messages are created.
