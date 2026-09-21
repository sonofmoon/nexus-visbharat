"""Configure the approved NVB languages/intents; back up before changing the cloud agent.
Run without --apply for a read-only audit. Does not submit citizen requests.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1]/'.env')
from visbharat.config import Config
from google.cloud import dialogflowcx_v3 as cx
from google.protobuf.field_mask_pb2 import FieldMask

PHRASES = {
 'new request': {
  'en':['Report a water supply problem','There is sewage overflowing near our school','The street lights are broken','Our street receives water for only one hour every day'],
  'ta':['புதிய கோரிக்கையைப் பதிவு செய்ய வேண்டும்','எங்கள் தெருவில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது','கழிவுநீர் தேங்கி உள்ளது','தெருவிளக்கு எரியவில்லை','சாலை சேதமடைந்துள்ளது'],
  'te':['కొత్త అభ్యర్థన నమోదు చేయాలి','మా వీధిలో తాగునీరు రోజుకు ఒక గంట మాత్రమే వస్తోంది','మురుగునీరు పొంగుతోంది','వీధి దీపాలు పనిచేయడం లేదు','రోడ్డు పాడైంది']},
 'check status': {
  'en':['Track my request','What is the status of my complaint','Check my ticket'],
  'ta':['என் கோரிக்கையின் நிலை என்ன','கோரிக்கையைக் கண்காணிக்க வேண்டும்','என் புகாரின் நிலையைச் சொல்லுங்கள்'],
  'te':['నా అభ్యర్థన స్థితి ఏమిటి','నా ఫిర్యాదు స్థితి చెప్పండి','నా టికెట్ స్థితి చూడాలి']},
 'location confirm': {
  'en':['My district is Vellore','This is in Tirupati','Confirm my location is Chennai'],
  'ta':['என் மாவட்டம் வேலூர்','இது திருப்பதியில் உள்ளது','என் இடம் சென்னை','வேலூர் மாவட்டம்'],
  'te':['నా జిల్లా తిరుపతి','ఇది వేలూరులో ఉంది','నా ప్రాంతం హైదరాబాద్','తిరుపతి జిల్లా']},
 'nvb_confirm':{'en':['Yes submit my request','Confirm and submit','Yes'], 'ta':['ஆம் சமர்ப்பிக்கவும்','உறுதிசெய்து சமர்ப்பி','ஆம்'], 'te':['అవును సమర్పించండి','నిర్ధారించి సమర్పించండి','అవును']},
 'nvb_cancel':{'en':['Cancel this draft','Do not submit','Cancel'], 'ta':['வரைவை ரத்து செய்','சமர்ப்பிக்க வேண்டாம்','ரத்து'], 'te':['ముసాయిదా రద్దు చేయండి','సమర్పించవద్దు','రద్దు']},
 'nvb_edit_location':{'en':['Change my district','Correct the location','Edit location'], 'ta':['இடத்தைத் திருத்த வேண்டும்','மாவட்டத்தை மாற்று','இடத்தைத் திருத்து'], 'te':['ప్రాంతం సరిచేయండి','జిల్లా మార్చండి','ప్రాంతం మార్చాలి']},
 'nvb_edit_issue':{'en':['Correct my description','Edit the request text','Change the issue details'], 'ta':['கோரிக்கை உரையைத் திருத்து','விவரங்களை மாற்ற வேண்டும்','உரையைத் திருத்து'], 'te':['అభ్యర్థన వచనం సరిచేయండి','వివరాలు మార్చాలి','వచనం సరిచేయండి']}
}
PROMPT = {
 'en':{'new request':'Please describe the infrastructure issue. NVB will show a review before saving.', 'check status':'Please enter your NVB tracking ID.', 'location confirm':'Review your district and local landmark in NVB.', 'nvb_confirm':'NVB will check your consent and save the reviewed request.', 'nvb_cancel':'You can cancel the draft in NVB.', 'nvb_edit_location':'Please correct your location.', 'nvb_edit_issue':'Please correct your description.'},
 'ta':{'new request':'உள்கட்டமைப்புப் பிரச்சினையை விவரிக்கவும். பதிவு செய்யும் முன் NVB விவரங்களைக் காட்டும்.', 'check status':'உங்கள் NVB கண்காணிப்பு எண்ணை உள்ளிடவும்.', 'location confirm':'NVB இல் மாவட்டத்தையும் இடத்தையும் சரிபார்க்கவும்.', 'nvb_confirm':'NVB உங்கள் ஒப்புதலைச் சரிபார்த்து கோரிக்கையைப் பதிவு செய்யும்.', 'nvb_cancel':'NVB இல் வரைவை ரத்து செய்யலாம்.', 'nvb_edit_location':'இடத்தைத் திருத்தவும்.', 'nvb_edit_issue':'விவரங்களைத் திருத்தவும்.'},
 'te':{'new request':'మౌలిక సదుపాయాల సమస్యను వివరించండి. సేవ్ చేసే ముందు NVB వివరాలు చూపుతుంది.', 'check status':'మీ NVB ట్రాకింగ్ నంబర్ నమోదు చేయండి.', 'location confirm':'NVBలో జిల్లా మరియు ప్రాంతం సరిచూడండి.', 'nvb_confirm':'NVB మీ అంగీకారం తనిఖీ చేసి అభ్యర్థన సేవ్ చేస్తుంది.', 'nvb_cancel':'NVBలో ముసాయిదా రద్దు చేయవచ్చు.', 'nvb_edit_location':'ప్రాంతం సరిచేయండి.', 'nvb_edit_issue':'వివరాలు సరిచేయండి.'}}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    os.environ.setdefault('GOOGLE_APPLICATION_CREDENTIALS',str(Path(__file__).resolve().parents[1]/'sa-key.json'))
    opts={'api_endpoint':Config.DIALOGFLOW_API_ENDPOINT or f'{Config.DIALOGFLOW_LOCATION}-dialogflow.googleapis.com'}
    parent=f'projects/{Config.DIALOGFLOW_PROJECT_ID}/locations/{Config.DIALOGFLOW_LOCATION}/agents/{Config.DIALOGFLOW_AGENT_ID}'
    agents,intents,flows=cx.AgentsClient(client_options=opts),cx.IntentsClient(client_options=opts),cx.FlowsClient(client_options=opts)
    agent=agents.get_agent(name=parent,timeout=15)
    print(json.dumps({'agent':agent.display_name,'default_language':agent.default_language_code,'supported_languages':list(agent.supported_language_codes)}),flush=True)
    if not args.apply:return
    backup=Path('scratch/assistant-cloud')/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');backup.mkdir(parents=True,exist_ok=False)
    export=agents.export_agent(request={'name':parent}).result(timeout=60)
    (backup/'agent-before.zip').write_bytes(export.agent_content)
    (backup/'agent-before.json').write_text(cx.Agent.to_json(agent),encoding='utf-8')
    languages=sorted(set(agent.supported_language_codes)|{'ta','te'})
    agents.update_agent(request={'agent':{'name':parent,'supported_language_codes':languages},'update_mask':FieldMask(paths=['supported_language_codes'])},timeout=20)
    existing={i.display_name:i for i in intents.list_intents(request={'parent':parent,'language_code':'en','intent_view':cx.IntentView.INTENT_VIEW_FULL},timeout=15)}
    names={}
    for display,bylang in PHRASES.items():
        original=existing.get(display)
        if original:
            (backup/(display.replace(' ','_')+'-en.json')).write_text(cx.Intent.to_json(original),encoding='utf-8')
            name=original.name
        else:
            name=intents.create_intent(request={'parent':parent,'intent':{'display_name':display},'language_code':'en'},timeout=15).name
        names[display]=name
        for lang,phrases in bylang.items():
            current=intents.get_intent(request={'name':name,'language_code':lang},timeout=15)
            (backup/(display.replace(' ','_')+'-'+lang+'.json')).write_text(cx.Intent.to_json(current),encoding='utf-8')
            known={''.join(p.text for p in phrase.parts) for phrase in current.training_phrases}
            for text in phrases:
                if text not in known:current.training_phrases.append(cx.Intent.TrainingPhrase(parts=[cx.Intent.TrainingPhrase.Part(text=text)],repeat_count=1))
            intents.update_intent(request={'intent':current,'language_code':lang,'update_mask':FieldMask(paths=['training_phrases'])},timeout=20)
        print('Updated multilingual intent: '+display,flush=True)
    flowname=parent+'/flows/00000000-0000-0000-0000-000000000000'
    for lang in ['en','ta','te']:
        flow=flows.get_flow(request={'name':flowname,'language_code':lang},timeout=15)
        (backup/('flow-'+lang+'.json')).write_text(cx.Flow.to_json(flow),encoding='utf-8')
        for display,name in names.items():
            route=next((r for r in flow.transition_routes if r.intent==name),None)
            if route is None:
                flow.transition_routes.append(cx.TransitionRoute(intent=name));route=flow.transition_routes[-1]
            route.trigger_fulfillment.messages=[cx.ResponseMessage(text=cx.ResponseMessage.Text(text=[PROMPT[lang][display]]))]
        flows.update_flow(request={'flow':flow,'language_code':lang,'update_mask':FieldMask(paths=['transition_routes'])},timeout=20)
    operation=flows.train_flow(request={'name':flowname},timeout=20)
    (backup/'training-operation.txt').write_text(operation.operation.name,encoding='utf-8')
    print(json.dumps({'backup':str(backup),'training_operation':operation.operation.name,'languages':['en','ta','te']}),flush=True)

if __name__=='__main__':main()
