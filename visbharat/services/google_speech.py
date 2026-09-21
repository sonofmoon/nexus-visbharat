from __future__ import annotations

from typing import Optional


class GoogleSpeechToTextClient:
    def __init__(self):
        from google.cloud import speech

        self.speech = speech
        self.client = speech.SpeechClient()

    def _encoding_from_mime(self, mime_type: str):
        mime = (mime_type or '').lower()
        if 'wav' in mime:
            return self.speech.RecognitionConfig.AudioEncoding.LINEAR16
        if 'flac' in mime:
            return self.speech.RecognitionConfig.AudioEncoding.FLAC
        if 'webm' in mime:
            return self.speech.RecognitionConfig.AudioEncoding.WEBM_OPUS
        if 'opus' in mime or 'ogg' in mime:
            return self.speech.RecognitionConfig.AudioEncoding.OGG_OPUS
        if 'mp3' in mime or 'mpeg' in mime:
            return self.speech.RecognitionConfig.AudioEncoding.MP3
        return self.speech.RecognitionConfig.AudioEncoding.ENCODING_UNSPECIFIED

    def transcribe_bytes(
        self,
        audio_bytes: bytes,
        language_code: str,
        mime_type: str = 'audio/wav',
        sample_rate_hertz: Optional[int] = None,
    ) -> dict:
        if not audio_bytes:
            raise ValueError('audio bytes are empty')

        config_kwargs = {
            'language_code': language_code,
            'enable_automatic_punctuation': True,
            'model': 'latest_long',
            'encoding': self._encoding_from_mime(mime_type),
        }
        if sample_rate_hertz:
            config_kwargs['sample_rate_hertz'] = int(sample_rate_hertz)

        config = self.speech.RecognitionConfig(**config_kwargs)
        audio = self.speech.RecognitionAudio(content=audio_bytes)

        response = self.client.recognize(config=config, audio=audio, timeout=25, retry=None)

        transcripts = []
        confidences = []
        for result in response.results:
            if not result.alternatives:
                continue
            alt = result.alternatives[0]
            transcripts.append(alt.transcript)
            if hasattr(alt, 'confidence'):
                confidences.append(float(alt.confidence))

        transcript = ' '.join(t.strip() for t in transcripts if t.strip()).strip()
        confidence = round(sum(confidences) / len(confidences), 2) if confidences else 0.0

        return {
            'transcript': transcript,
            'confidence': confidence,
            'language': language_code,
            'model': 'Google Cloud Speech-to-Text',
            'provider_mode': 'google_stt_live',
            'mime_type': mime_type,
        }
