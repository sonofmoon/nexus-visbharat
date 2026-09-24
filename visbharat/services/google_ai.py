import json
import os
import random
import time
from typing import Dict, Any

import requests


class GoogleAIClient:
    def __init__(self, api_key: str = '', project_id: str = '', location: str = 'asia-south1', use_vertex: bool = False):
        self.api_key = api_key
        self.project_id = project_id
        self.location = location or 'asia-south1'
        self.use_vertex = use_vertex or bool(project_id and not api_key)
        self.base_url = 'https://generativelanguage.googleapis.com/v1beta/models'
        # Enforce Indian sovereign data residency by default (asia-south1, Mumbai)
        self.vertex_openai_location = os.environ.get('VERTEX_OPENAI_LOCATION', 'asia-south1')
        allowed_regions = {'asia-south1', 'asia-south2'}
        profile = os.environ.get('DEPLOYMENT_PROFILE', 'local').lower()
        if profile in ('pilot', 'national'):
            if self.vertex_openai_location not in allowed_regions:
                raise RuntimeError(
                    f"Data Residency Policy Violation: Vertex AI endpoint region '{self.vertex_openai_location}' "
                    f"is not within approved Indian sovereign GCP regions ({allowed_regions})."
                )
            if self.location not in allowed_regions:
                raise RuntimeError(
                    f"Data Residency Policy Violation: Vertex AI location '{self.location}' "
                    f"is not within approved Indian sovereign GCP regions ({allowed_regions})."
                )
        self._vertex_credentials = None
        # 1b/1c: bounded retry + circuit-breaker telemetry
        self._circuit = {}          # key -> {'failures': int, 'open_until': float}
        self._cb_threshold = 3      # consecutive failures before opening circuit
        self._cb_open_seconds = 60
        self.last_transport = 'none'      # vertex_openai | api_key_rest
        self.last_provider_mode = 'none'  # live | degraded

    def _get_vertex_access_token(self) -> str:
        """Fresh OAuth2 access token from Application Default Credentials (cached, auto-refreshed)."""
        import google.auth
        import google.auth.transport.requests

        if self._vertex_credentials is None:
            creds, _ = google.auth.default(scopes=['https://www.googleapis.com/auth/cloud-platform'])
            self._vertex_credentials = creds
        creds = self._vertex_credentials
        if not creds.valid:
            creds.refresh(google.auth.transport.requests.Request())
        return creds.token

    def _get_vertex_auth_header(self) -> dict:
        return {
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + self._get_vertex_access_token(),
        }

    def _call_gemini_vertex_openai(self, model: str, prompt: str, json_mode: bool = True) -> tuple[str, str]:
        """Gemini on Vertex AI via the OpenAI-compatible chat completions endpoint.

        Uses ADC tokens billed to the GCP project, avoiding AI Studio key quota limits.
        Returns (text, served_model) where served_model comes from the RPC response.
        """
        url = (
            f"https://aiplatform.googleapis.com/v1/projects/{self.project_id}"
            f"/locations/{self.vertex_openai_location}/endpoints/openapi/chat/completions"
        )
        system_content = (
            'You are a civic infrastructure intelligence engine for India. Respond with valid JSON only.'
            if json_mode
            else 'You are a civic infrastructure intelligence engine and translator for India. Provide direct, accurate English text without JSON wrappers or metadata.'
        )
        payload = {
            'model': f'google/{model}',
            'messages': [
                {
                    'role': 'system',
                    'content': system_content,
                },
                {'role': 'user', 'content': prompt},
            ],
            'temperature': 0.2,
            'max_tokens': 1500,
        }
        if json_mode:
            payload['response_format'] = {'type': 'json_object'}
        response = requests.post(url, json=payload, headers=self._get_vertex_auth_header(), timeout=25)
        response.raise_for_status()
        data = response.json()
        choices = data.get('choices', [])
        if not choices:
            raise ValueError('Vertex OpenAI endpoint returned no choices')
        text = ((choices[0].get('message') or {}).get('content') or '').strip()
        if not text:
            raise ValueError('Vertex OpenAI endpoint returned empty content')
        served_model = str(data.get('model') or f'google/{model}').replace('google/', '')
        return text, served_model

    def _circuit_key(self, transport: str, model: str) -> str:
        return f'{transport}:{model}'

    def _circuit_open(self, key: str) -> bool:
        state = self._circuit.get(key) or {}
        return float(state.get('open_until') or 0) > time.time()

    def _circuit_success(self, key: str):
        self._circuit[key] = {'failures': 0, 'open_until': 0}

    def _circuit_failure(self, key: str):
        state = self._circuit.get(key) or {'failures': 0, 'open_until': 0}
        failures = int(state.get('failures') or 0) + 1
        open_until = time.time() + self._cb_open_seconds if failures >= self._cb_threshold else 0
        self._circuit[key] = {'failures': failures, 'open_until': open_until}

    def get_health(self) -> dict:
        now = time.time()
        cb_open = any(float(v.get('open_until') or 0) > now for v in self._circuit.values())
        if cb_open:
            status = 'unavailable'
        elif self.last_provider_mode == 'live':
            status = 'verified'
        elif self.last_provider_mode == 'degraded':
            status = 'degraded'
        else:
            status = 'configured'

        return {
            'status': status,
            'last_transport': self.last_transport if self.last_transport != 'none' else None,
            'last_provider_mode': self.last_provider_mode if self.last_provider_mode != 'none' else None,
            'circuit_breakers': {
                k: {'failures': v.get('failures', 0), 'open': float(v.get('open_until') or 0) > now}
                for k, v in self._circuit.items()
            },
        }

    def _call_gemini(self, model: str, prompt: str, json_mode: bool = True) -> tuple[str, str]:
        models_to_try = [
            'gemini-3.6-flash',
            'gemini-3.5-flash-lite',
        ]
        if model and model not in models_to_try:
            models_to_try.insert(0, model)

        last_err = None
        project = self.project_id or os.environ.get('VERTEX_PROJECT_ID', '') or os.environ.get('GOOGLE_CLOUD_PROJECT', '')
        for m in models_to_try:
            m_clean = m.replace('models/', '')

            if project:
                key = self._circuit_key('vertex_openai', m_clean)
                if self._circuit_open(key):
                    last_err = f"Vertex OpenAI '{m_clean}' circuit open"
                else:
                    for attempt in range(2):
                        try:
                            saved_project = self.project_id
                            self.project_id = project
                            text, served = self._call_gemini_vertex_openai(m_clean, prompt, json_mode=json_mode)
                            self._circuit_success(key)
                            self.last_transport = 'vertex_openai'
                            self.last_provider_mode = 'live'
                            return text, served
                        except Exception as e:
                            self.project_id = saved_project
                            last_err = f"Vertex OpenAI '{m_clean}' exception: {str(e)[:150]}"
                            if attempt == 0:
                                time.sleep(0.6 + random.uniform(0, 0.4))
                    self._circuit_failure(key)
            
            if self.use_vertex and self.project_id:
                url = f"https://{self.location}-aiplatform.googleapis.com/v1/projects/{self.project_id}/locations/{self.location}/publishers/google/models/{m_clean}:generateContent"
                headers = self._get_vertex_auth_header()
            else:
                profile = os.environ.get('DEPLOYMENT_PROFILE', 'local').lower()
                if profile in ('pilot', 'national'):
                    raise RuntimeError(
                        "Data Residency Policy Violation: AI Studio global endpoint fallback is strictly forbidden "
                        "in pilot/national profiles. Sovereign Vertex AI (asia-south1/asia-south2) is required."
                    )
                url = f'{self.base_url}/{m_clean}:generateContent?key={self.api_key}'
                headers = {'Content-Type': 'application/json'}

            payload = {
                'contents': [
                    {
                        'parts': [
                            {'text': prompt}
                        ]
                    }
                ],
                'generationConfig': {
                    'temperature': 0.2,
                    'maxOutputTokens': 1500,
                }
            }
            if json_mode:
                payload['generationConfig']['responseMimeType'] = 'application/json'
            key = self._circuit_key('api_key_rest', m_clean)
            if self._circuit_open(key):
                last_err = f"Model '{m_clean}' circuit open"
                continue
            for attempt in range(2):
                try:
                    response = requests.post(url, json=payload, headers=headers, timeout=15)
                    if response.status_code == 200:
                        data = response.json()
                        candidates = data.get('candidates', [])
                        if candidates:
                            parts = candidates[0].get('content', {}).get('parts', [])
                            if parts:
                                self._circuit_success(key)
                                self.last_transport = 'api_key_rest'
                                self.last_provider_mode = 'live'
                                return parts[0].get('text', '').strip(), str(data.get('modelVersion') or m_clean)
                        last_err = f"Model '{m_clean}' empty candidates"
                    else:
                        last_err = f"Model '{m_clean}' HTTP {response.status_code}: {response.text[:150]}"
                except Exception as e:
                    last_err = f"Model '{m_clean}' exception: {str(e)}"
                if attempt == 0:
                    time.sleep(0.6 + random.uniform(0, 0.4))
            self._circuit_failure(key)
        raise ValueError(f"Gemini API call failed across models: {last_err}")


    @staticmethod
    def _parse_json(text: str) -> Dict[str, Any]:
        text = (text or '').strip()
        if not text:
            raise ValueError('Empty Gemini response')

        # Strip markdown fences if present (e.g. ```json ... ``` or ``` ... ```)
        if text.startswith('```'):
            lines = text.splitlines()
            if len(lines) >= 2 and lines[0].startswith('```'):
                lines = lines[1:]
            if lines and lines[-1].strip().startswith('```'):
                lines = lines[:-1]
            text = '\n'.join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start = text.find('{')
            end = text.rfind('}')
            if start >= 0 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            raise

    def classify_request(self, text: str, language: str, categories: list[str]) -> Dict[str, Any]:
        prompt = f'''You are a civic infrastructure classifier for India.
Classify the citizen request into one category from this list: {categories}
Urgency must be one of: Routine, Urgent, Emergency.
Sentiment must be one of: Neutral, Negative, Very Negative.

Return strict JSON with keys:
category, urgency, sentiment, confidence

Request language: {language}
Citizen text: {text}
'''
        try:
            output, used_model = self._call_gemini('gemini-3.6-flash', prompt)
            result = self._parse_json(output)
            result['model'] = used_model
            result['provider_mode'] = 'google_ai_live'
            result['fallback_used'] = False
            if result.get('category') not in categories or result.get('urgency') not in ('Routine', 'Urgent', 'Emergency'):
                raise ValueError('Classifier returned an invalid category or urgency')
            result.setdefault('sentiment', 'Neutral')
            result.setdefault('confidence', None)
            result['confidence_basis'] = 'provider_estimate_not_calibrated' if result['confidence'] is not None else 'not_reported'
            return result
        except Exception:
            from visbharat.services.ai_simulation import simulate_gemini_intent_classification
            res = simulate_gemini_intent_classification(text, language, categories)
            res['provider_mode'] = 'local_fallback'
            res['fallback_used'] = True
            return res

    def translate_text(self, text: str, source_lang: str, target_lang: str = 'en') -> Dict[str, Any]:
        prompt = f'''You are a high-precision civic infrastructure translator for India.
Translate the citizen request below from {source_lang} to English with exact semantic accuracy and domain fidelity.

CRITICAL RULES:
1. Preserve the exact civic issue. Key terms:
   - "??????? ????" / "?????" -> sewage water (never drinking water)
   - "????????" -> drinking water supply
   - "???????????" -> street light
   - "????" / "????" -> road
2. Do not add, remove, or substitute problems.
3. Output ONLY the English translation text. No JSON, no labels, no quotes, no explanation.

Text:
{text}
'''
        try:
            output, used_model = self._call_gemini('gemini-3.6-flash', prompt, json_mode=False)
            t_text = output.strip()
            # Robust unwrap if model returned JSON or code-fence
            if t_text.startswith('{') or '```' in t_text:
                try:
                    import re, json
                    clean_json = re.sub(r'^```(?:json)?\s*', '', t_text, flags=re.MULTILINE)
                    clean_json = re.sub(r'```\s*$', '', clean_json, flags=re.MULTILINE).strip()
                    parsed = json.loads(clean_json)
                    if isinstance(parsed, dict):
                        t_text = str(parsed.get('translation') or parsed.get('translated_text') or list(parsed.values())[0]).strip()
                except Exception:
                    pass
            t_text = t_text.strip().strip('"').strip()
            for prefix in ["Translation:", "English Translation:", "English:"]:
                if t_text.startswith(prefix):
                    t_text = t_text[len(prefix):].strip()

            # Strict Output Validation: reject commentary, thought artifacts, or reasoning fragments
            reject_markers = [
                "wait!", "draft translation", "system:", "user:", "rule 1", "rule 2", "rule 3",
                "respond with valid json", "plain text?", "why does user", "```"
            ]
            if any(marker in t_text.lower() for marker in reject_markers) or len(t_text.split()) < 2:
                raise ValueError(f"Translation output failed quality validation: reasoning artifacts detected: {t_text[:80]}")

            return {
                'translated_text': t_text,
                'source_language': source_lang,
                'target_language': target_lang,
                'model': used_model,
                'provider_mode': 'google_ai_live',
                'fallback_used': False,
            }
        except Exception:
            from visbharat.services.ai_simulation import simulate_translation
            res = simulate_translation(text, source_lang, target_lang)
            res['model'] = 'google_ai_fallback_local'
            res['provider_mode'] = 'local_fallback'
            res['fallback_used'] = True
            return res

    def generate_policy_brief(self, district: str, total: int, emergency_total: int, top_categories: list[str]) -> Dict[str, Any]:
        prompt = f'''You are a policy copilot for Indian district planning.
Generate a concise policy brief as strict JSON with keys:
summary, recommendations
where recommendations is an array of 3 short actionable strings.

District: {district}
Total requests: {total}
Emergency requests: {emergency_total}
Top categories: {top_categories}
'''
        try:
            output, used_model = self._call_gemini('gemini-3.6-flash', prompt)
            result = self._parse_json(output)
            result['model'] = used_model
            result['provider_mode'] = 'google_ai_live'
            result['fallback_used'] = False
            result.setdefault('summary', f'{total} requests observed in {district}.')
            result.setdefault('recommendations', [
                'Prioritize highest-demand category for immediate action.',
                'Deploy rapid response for emergency requests.',
                'Publish monthly public progress reports.'
            ])
            return result
        except Exception:
            return {
                'summary': f'{total} requests observed in {district}. Priority focus on {", ".join(top_categories[:2]) if top_categories else "infrastructure"}.',
                'recommendations': [
                    'Prioritize highest-demand category for immediate action.',
                    'Deploy rapid response for emergency requests.',
                    'Publish monthly public progress reports.'
                ],
                'model': 'local_policy_brief_fallback',
                'provider_mode': 'local_fallback',
                'fallback_used': True,
            }

    def embed_text(self, text: str) -> list[float]:
        url = f'https://generativelanguage.googleapis.com/v1beta/models/text-embedding-004:embedContent?key={self.api_key}'
        payload = {
            'model': 'models/text-embedding-004',
            'content': {
                'parts': [
                    {'text': str(text or '')}
                ]
            }
        }
        response = requests.post(url, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        values = (((data.get('embedding') or {}).get('values')) or [])
        if not values:
            raise ValueError('No embedding values returned')
        return [float(v) for v in values]

    def transcribe_audio_bytes(self, audio_bytes: bytes, language_code: str, mime_type: str = 'audio/webm') -> dict:
        import base64
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        url = f'{self.base_url}/gemini-3.6-flash:generateContent?key={self.api_key}'
        prompt = f'''Transcribe the provided spoken audio into exact text in its native language.
Return strict JSON with keys:
transcript, confidence, language
Language: {language_code}
'''
        payload = {
            'contents': [
                {
                    'parts': [
                        {
                            'inline_data': {
                                'mime_type': str(mime_type or 'audio/webm'),
                                'data': audio_b64
                            }
                        },
                        {'text': prompt}
                    ]
                }
            ],
            'generationConfig': {
                'temperature': 0.1,
                'maxOutputTokens': 500,
                'responseMimeType': 'application/json'
            }
        }
        response = requests.post(url, json=payload, timeout=25)
        response.raise_for_status()
        data = response.json()
        candidates = data.get('candidates', [])
        if not candidates:
            raise ValueError('No candidates returned from Gemini Audio STT')
        parts = candidates[0].get('content', {}).get('parts', [])
        if not parts:
            raise ValueError('No content parts returned from Gemini Audio STT')
        parsed = self._parse_json(parts[0].get('text', ''))
        return {
            'transcript': str(parsed.get('transcript') or '').strip(),
            'confidence': float(parsed.get('confidence', 0.92) or 0.92),
            'language': language_code,
            'model': 'gemini-3.6-flash',
            'mime_type': mime_type,
            'provider': 'google_ai',
            'provider_mode': 'gemini_audio_stt_live'
        }

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        language_code: str,
        mime_type: str = 'audio/webm',
        sample_rate_hertz: int | None = None,
    ) -> dict:
        return self.transcribe_audio_bytes(audio_bytes=audio_bytes, language_code=language_code, mime_type=mime_type)