# VisBharath Hackathon Submission Pack

## 1) What judges can verify immediately

### A. End-to-end working flow
- `POST /api/submit` accepts citizen complaint
- `POST /api/submit-voice` accepts strict validated voice complaint
- AI processing enriches complaint (translation + classification)
- Data persists in DB
- `GET /api/hotspots` surfaces demand clusters
- `GET /api/policy-brief/<district>` provides decision summary

### B. Google AI integration (mandatory)
When configured:
- `USE_REAL_GOOGLE_AI=true`
- `GOOGLE_AI_API_KEY=<key>`
- `USE_REAL_GOOGLE_STT=true`
- `GOOGLE_APPLICATION_CREDENTIALS=<service-account-json-path>`

The system uses Google AI for:
- translation
- classification (category, urgency, sentiment)
- policy brief generation
- speech-to-text (voice transcription)

Verify with:
- `GET /api/ai/status` -> `mode: google_ai_live`
- `GET /api/ai/status` -> `stt_mode: google_stt_live`

### C. Realistic data
- Reference datasets under `static/data/`
- DB stores all newly submitted requests

### D. Built for India
- Multilingual request fields
- State/district dataset-driven enrichment
- Current submission focus states: `Tamil Nadu`, `Andhra Pradesh`, `Telangana`\n- Default demo districts: `Chennai`, `Vellore`, `Karur`, `Visakhapatnam`, `Tirupati`, `Hyderabad`

### E. Multilingual/voice
- Language-first demo flow for `te`, `ta`, `en`
- Live STT available in production mode

## 2) Demo run script (judge flow)

1. Start server: `python app.py`
2. Check mode: `GET /api/health`, `GET /api/ai/status`, `GET /api/db/status`
3. Submit 5-10 complaints in mixed languages (`te`, `ta`, `en`)
4. Use strict voice endpoint: `POST /api/submit-voice` (audio file or base64)
5. Open `GET /api/hotspots`
6. Open `GET /api/policy-brief/<district>`
7. Validate governance APIs:
   - `GET /api/v1/requests` with analyst token
   - `GET /api/v1/audit-logs` with auditor token

## 3) Evaluation criteria alignment

- Problem-solution fit: citizen voice -> infrastructure prioritization pipeline
- AI execution: meaningful Google AI processing in live mode
- Reach across India: multilingual + district/state model
- Impact potential: hotspot-driven project planning insights
- Deployability: modular service, DB-backed, RBAC + audit controls

## 4) Current prototype status

- Fully functional v1 prototype in local environment
- Real Google AI path enabled through config
- Fallback mode ensures demo continuity without external keys


### Database status (judge snippet)
Use this quick check in demo walkthrough to prove active DB backend:

```bash
curl http://localhost:5000/api/db/status
```

Sample response:

```json
{
  "success": true,
  "backend": "sqlite",
  "database_url_configured": false,
  "target": "visbharat.db"
}
```

If running with PostgreSQL via `DATABASE_URL`, `backend` shows `postgres`.
