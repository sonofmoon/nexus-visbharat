"""Live connectivity evaluation with fictional text and synthesized speech (not human ASR accuracy)."""
import json
import os
import sys
import time
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env')
os.environ.setdefault('GOOGLE_APPLICATION_CREDENTIALS',str(Path(__file__).resolve().parents[1]/'sa-key.json'))
from visbharat.config import Config
from visbharat.services.google_dialogflow import GoogleDialogflowCXClient
from visbharat.services.google_speech import GoogleSpeechToTextClient
from visbharat.services.google_tts import GoogleTextToSpeechClient
from google.cloud import dialogflowcx_v3 as cx,texttospeech

SAMPLES={
 'en':{'text':'Our street receives water for only one hour every day.','track':'What is the status of my complaint','location':'My district is Vellore'},
 'ta':{'text':'எங்கள் தெருவில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது.','track':'என் கோரிக்கையின் நிலை என்ன','location':'என் மாவட்டம் வேலூர்'},
 'te':{'text':'మా వీధిలో తాగునీరు రోజుకు ఒక గంట మాత్రమే వస్తోంది.','track':'నా అభ్యర్థన స్థితి ఏమిటి','location':'నా జిల్లా తిరుపతి'}}

def main():
    client=GoogleDialogflowCXClient(Config.DIALOGFLOW_PROJECT_ID,Config.DIALOGFLOW_LOCATION,Config.DIALOGFLOW_AGENT_ID,api_endpoint=Config.DIALOGFLOW_API_ENDPOINT)
    stt=GoogleSpeechToTextClient();tts=GoogleTextToSpeechClient()
    report={'recorded_at':datetime.now(timezone.utc).isoformat(),'scope':'Fictional requests; synthesized-speech smoke test. No tickets submitted. Not a human/dialect ASR benchmark.','checks':[]}
    for lang,sample in SAMPLES.items():
        for key,expected in [('text','new request'),('track','check status'),('location','location confirm')]:
            started=time.perf_counter()
            try:
                result=client.detect_intent(uuid4().hex,sample[key],lang)
                row={'language':lang,'kind':'cx_'+key,'intent':result['intent'],'expected_intent':expected,'confidence':result['confidence'],'passed':result['intent']==expected}
            except Exception as err:
                row={'language':lang,'kind':'cx_'+key,'passed':False,'error_type':type(err).__name__,'error':str(err)[:300]}
            row['latency_ms']=round((time.perf_counter()-started)*1000);report['checks'].append(row);print(json.dumps(row),flush=True)
        started=time.perf_counter()
        try:
            generated=tts.client.synthesize_speech(input=texttospeech.SynthesisInput(text=sample['text']),voice=texttospeech.VoiceSelectionParams(language_code=lang+'-IN'),audio_config=texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16,sample_rate_hertz=16000),timeout=15,retry=None)
            result=stt.transcribe_bytes(generated.audio_content,lang+'-IN','audio/wav',16000)
            row={'language':lang,'kind':'tts_stt_roundtrip','passed':bool(result['transcript']),'transcript':result['transcript'],'reference_text':sample['text'],'confidence':result['confidence'],'provider_mode':result.get('provider_mode')}
        except Exception as err:
            row={'language':lang,'kind':'tts_stt_roundtrip','passed':False,'error_type':type(err).__name__,'error':str(err)[:300]}
        row['latency_ms']=round((time.perf_counter()-started)*1000);report['checks'].append(row);print(json.dumps(row),flush=True)
    report['passed']=sum(bool(c['passed']) for c in report['checks']);report['total']=len(report['checks'])
    path=Path('docs/evaluation/assistant-live.json');path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(f"Saved {path}: {report['passed']}/{report['total']} checks passed",flush=True)
    return 0 if report['passed']==report['total'] else 1
if __name__=='__main__':raise SystemExit(main())
