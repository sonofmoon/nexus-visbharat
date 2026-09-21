from __future__ import annotations

import base64


class GoogleTextToSpeechClient:
    def __init__(self, language_code: str = 'en-IN', voice_name: str = ''):
        from google.cloud import texttospeech

        self.texttospeech = texttospeech
        self.language_code = str(language_code or 'en-IN').strip() or 'en-IN'
        self.voice_name = str(voice_name or '').strip()
        self.client = texttospeech.TextToSpeechClient()

    def synthesize(self, text: str, language_code: str = '', speaking_rate: float = 1.0, pitch: float = 0.0) -> dict:
        clean_text = str(text or '').strip()
        if not clean_text:
            raise ValueError('text is required')

        lang = str(language_code or self.language_code).strip() or self.language_code

        synthesis_input = self.texttospeech.SynthesisInput(text=clean_text)
        voice_kwargs = {
            'language_code': lang,
            'ssml_gender': self.texttospeech.SsmlVoiceGender.NEUTRAL,
        }
        if self.voice_name:
            voice_kwargs['name'] = self.voice_name
        voice = self.texttospeech.VoiceSelectionParams(**voice_kwargs)

        audio_config = self.texttospeech.AudioConfig(
            audio_encoding=self.texttospeech.AudioEncoding.MP3,
            speaking_rate=float(speaking_rate or 1.0),
            pitch=float(pitch or 0.0),
        )

        response = self.client.synthesize_speech(
            input=synthesis_input,
            voice=voice,
            audio_config=audio_config,
            timeout=10, retry=None,
        )

        return {
            'audio_base64': base64.b64encode(response.audio_content).decode('utf-8') if response.audio_content else '',
            'audio_mime_type': 'audio/mpeg',
            'model': 'Google Cloud Text-to-Speech',
            'provider_mode': 'google_tts_live',
            'language_code': lang,
        }
