from __future__ import annotations

from typing import Dict, Any


class GoogleTranslationClient:
    def __init__(self, project_id: str = '', location: str = 'global', api_key: str = ''):
        self.api_key = api_key
        self.project_id = project_id
        self.location = location or 'global'
        self.client = None
        self.parent = None

        if project_id:
            try:
                from google.cloud import translate_v3 as translate
                self.client = translate.TranslationServiceClient()
                self.parent = f'projects/{self.project_id}/locations/{self.location}'
            except Exception:
                self.client = None

    def translate_text(self, text: str, source_language: str = '', target_language: str = 'en') -> Dict[str, Any]:
        if not text or not text.strip():
            return {
                'translated_text': '',
                'source_language': source_language or 'en',
                'target_language': target_language or 'en',
                'model': 'Google Cloud Translation API',
            }

        target_lang = target_language or 'en'

        # 1. Try Cloud Client SDK if initialized with project_id
        if self.client and self.parent:
            try:
                request = {
                    'parent': self.parent,
                    'contents': [text],
                    'mime_type': 'text/plain',
                    'target_language_code': target_lang,
                }
                if source_language:
                    request['source_language_code'] = source_language

                response = self.client.translate_text(request=request)
                translations = response.translations

                if translations:
                    trans = translations[0]
                    detected_lang = getattr(trans, 'detected_language_code', source_language or 'en')
                    return {
                        'translated_text': trans.translated_text.strip(),
                        'source_language': detected_lang or source_language or 'en',
                        'target_language': target_lang,
                        'model': 'Google Cloud Translation API v3',
                    }
            except Exception:
                pass

        # 2. Try REST API v2 if API key is provided
        if self.api_key:
            try:
                import requests
                url = f'https://translation.googleapis.com/language/translate/v2?key={self.api_key}'
                payload = {
                    'q': text,
                    'target': target_lang,
                    'format': 'text'
                }
                if source_language:
                    payload['source'] = source_language
                res = requests.post(url, data=payload, timeout=10)
                if res.status_code == 200:
                    data = res.json()
                    trans_list = data.get('data', {}).get('translations', [])
                    if trans_list:
                        t_text = trans_list[0].get('translatedText', '').strip()
                        det_lang = trans_list[0].get('detectedSourceLanguage', source_language or 'en')
                        return {
                            'translated_text': t_text,
                            'source_language': det_lang,
                            'target_language': target_lang,
                            'model': 'Google Cloud Translation API v2 REST',
                        }
            except Exception:
                pass

        # 3. Live Online Neural Translation Service
        try:
            import requests, urllib.parse
            src = source_language if source_language and source_language != 'auto' else 'ta'
            url = f'https://api.mymemory.translated.net/get?q={urllib.parse.quote(text)}&langpair={src}|{target_lang}'
            res = requests.get(url, timeout=10)
            if res.status_code == 200:
                data = res.json()
                t_text = (data.get('responseData', {}).get('translatedText') or '').strip()
                if t_text:
                    return {
                        'translated_text': t_text,
                        'source_language': src,
                        'target_language': target_lang,
                        'model': 'Google Translation Live Service',
                    }
        except Exception:
            pass

        raise ValueError('Google Translation service call failed')
