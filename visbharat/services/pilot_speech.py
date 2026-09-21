"""Opt-in Speech v2 adapter; deployment must verify region/model/language support."""
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech
from google.api_core.client_options import ClientOptions


class RegionalSpeech:
    def __init__(self,project,location,model):
        if not project or location not in ('asia-south1','asia-south2'):
            raise ValueError('Configure a reviewed India Speech v2 region')
        self.recognizer=f'projects/{project}/locations/{location}/recognizers/_'
        self.model=model
        self.client=speech_v2.SpeechClient(client_options=ClientOptions(api_endpoint=f'{location}-speech.googleapis.com'))

    def transcribe_bytes(self,content,language,mime):
        response=self.client.recognize(request=cloud_speech.RecognizeRequest(
            recognizer=self.recognizer,content=content,config=cloud_speech.RecognitionConfig(
                auto_decoding_config=cloud_speech.AutoDetectDecodingConfig(),language_codes=[language],model=self.model)),timeout=75)
        transcript=' '.join(r.alternatives[0].transcript for r in response.results if r.alternatives)
        if not transcript.strip():raise ValueError('No transcript returned')
        return {'transcript':transcript,'provider_mode':'google_speech_v2_live','model':self.model,
                'recognizer':self.recognizer,'fallback_used':False}
