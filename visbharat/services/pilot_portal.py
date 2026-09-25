"""Shared portal configuration and private evidence for a bounded programme."""
import base64
import copy
import hashlib
import json
import math
from urllib.parse import urlparse
from flask import current_app, g
from ..db import get_db
from ..audit import write_audit_log
from . import pilot

CHANNELS = {'web': 'Web & voice', 'dialogflow': 'Talk to NVB (Dialogflow AI)', 'whatsapp': 'WhatsApp', 'telegram': 'Telegram',
            'ivr': 'Phone / IVR', 'sms': 'SMS', 'email': 'Email'}
SOURCES = {'web': 'Web Form', 'dialogflow': 'Dialogflow CX', 'whatsapp': 'WhatsApp', 'telegram': 'Telegram',
           'ivr': 'Voice IVR', 'sms': 'SMS', 'email': 'Email'}

# Labels and field contracts are shared by validation and the Settings UI.
GROUPS = [
    ('programme', 'Programme & districts', [('description','Programme description','text'),('support_contact','Public support contact','text'),('end_date','End date','date')]),
    ('departments', 'Departments & officers', [('directory','Department directory and escalation contacts','text'),('delegation','Delegation and approval responsibilities','text')]),
    ('workflow', 'Service workflows', [('review_days','Review target (days)','number'),('closure_requirements','Closure evidence requirements','text'),('reopening_policy','Reopening and feedback procedure','text')]),
    ('planning', 'Development & policy', [('financial_year','Financial year','text'),('budget_lakh','Capital planning envelope (₹ lakh)','number'),('operating_budget_lakh','Annual operating envelope (₹ lakh)','number'),('capacity','Project capacity','number'),('max_per_district','Projects per district','number'),('equity_share','Equity reservation (0–1)','fraction'),('funding_sources','Funding sources and eligibility','text'),('policy_library','Policy references and source owners','text')]),
    ('data', 'Data & maps', [('source_register','Data sources, owners and refresh schedule','text'),('boundary_reference','Boundary / ward reference','url'),('quality_requirements','Data-quality requirements','text')]),
    ('evaluation', 'Delivery & evaluation', [('baseline_requirements','Baseline and measurement method','text'),('milestone_template','Milestones and inspection requirements','text'),('review_frequency_days','Outcome review interval (days)','number')]),
    ('privacy', 'Privacy & access', [('consent_notice','Citizen processing notice','text'),('retention_days','Retention review interval (days)','number'),('public_progress','Show aggregate public progress','boolean')]),
    ('operations', 'Operations & activation', [('owner','Operational owner','text'),('alert_contacts','Alert recipients','text'),('recovery_reference','Recovery drill evidence','url'),('runbook_reference','Operating runbook','url')]),
]

INTEGRATIONS = [
    ('gemini','Gemini on Vertex AI','Translation, classification and policy assistance','google_ai_client',['VERTEX_PROJECT_ID','VERTEX_LOCATION','PILOT_MODEL_NAME']),
    ('vertex','Vertex AI predictions','Demand and development scenarios','google_vertex_client',['VERTEX_ENDPOINT_ID']),
    ('speech','Speech-to-Text','Tamil, Telugu and English voice intake','pilot_regional_stt',['PILOT_SPEECH_LOCATION','PILOT_SPEECH_MODEL']),
    ('tts','Text-to-Speech','Spoken citizen responses','google_tts_client',['GOOGLE_TTS_LANGUAGE_CODE']),
    ('translation','Cloud Translation','Optional configured translation provider','google_translation_client',['USE_REAL_GOOGLE_TRANSLATION']),
    ('dialogflow','Dialogflow CX','Guided conversations and IVR','google_dialogflow_client',['DIALOGFLOW_PROJECT_ID','DIALOGFLOW_AGENT_ID']),
    ('maps','Google Maps','Location assistance and maps','google_maps_client',['USE_REAL_GOOGLE_MAPS']),
    ('earth_engine','Earth Engine','Dated geographic evidence; not completion certification',None,['EARTH_ENGINE_PROJECT']),
    ('bigquery','BigQuery','Redacted programme analytics','google_bigquery_client',['PILOT_BIGQUERY_TABLE']),
    ('storage','Cloud Storage','Private evidence and recovery',None,['PILOT_AUDIO_BUCKET','PILOT_AUDIO_RECOVERY_BUCKET']),
    ('pubsub','Pub/Sub & Cloud Functions','Integration events and analytics','google_pubsub_client',['PUBSUB_TOPIC_ID']),
    ('tasks','Cloud Tasks & Scheduler','Durable processing and scheduled dispatch',None,['PILOT_TASK_QUEUE','PILOT_WORKER_URL']),
    ('runtime','Cloud Run & Cloud SQL','Application and operational database',None,['PILOT_WORKER_AUDIENCE']),
    ('secrets','Secret Manager & IAM','Managed credentials and service identity','secret_provider',['PILOT_WORKER_SERVICE_ACCOUNT']),
]


def configuration(p):
    defaults = {key: {} for key, _, _ in GROUPS}
    defaults.update(channels={key: {'enabled': key in ('web','dialogflow'), 'address': '', 'owner': 'NVB Dialogflow CX' if key=='dialogflow' else ''} for key in CHANNELS},
                    integrations={key: {'owner':'','reference':''} for key,*_ in INTEGRATIONS})
    defaults['workflow']={'review_days':p['config'].get('review_days',2)}
    defaults['planning']={'budget_lakh':500,'operating_budget_lakh':1000,'capacity':6,'max_per_district':2,'equity_share':0}
    defaults['privacy']={'consent_notice':p['config'].get('notice',''),'retention_days':30,'public_progress':False}
    defaults['evaluation']={'review_frequency_days':28}
    for key, value in p['config'].get('portal',{}).items():
        if key in defaults and isinstance(value,dict):
            for sub,value2 in value.items():
                if isinstance(value2,dict) and isinstance(defaults[key].get(sub),dict):defaults[key][sub].update(value2)
                else: defaults[key][sub]=value2
    return defaults


def validate_url(value):
    if value and (urlparse(value).scheme!='https' or not urlparse(value).netloc or urlparse(value).username):
        raise ValueError('Use an HTTPS reference without embedded credentials')
    return value


def validate_settings(p, incoming):
    if not isinstance(incoming,dict):raise ValueError('Settings must be an object')
    result=configuration(p)
    known={k:fields for k,_,fields in GROUPS}
    if set(incoming)-set(result):raise ValueError('Unknown settings section')
    for key,value in incoming.items():
        if not isinstance(value,dict):raise ValueError('Settings section must be an object')
        if key in ('channels','integrations'):
            allowed=CHANNELS if key=='channels' else dict((x[0],x[1]) for x in INTEGRATIONS)
            if set(value)-set(allowed):raise ValueError('Unknown channel or integration')
            for name,item in value.items():
                fields={'enabled':'boolean','address':'text','owner':'text'} if key=='channels' else {'owner':'text','reference':'text'}
                if not isinstance(item,dict) or set(item)-set(fields):raise ValueError('Invalid connection settings; credentials belong in Secret Manager')
                for field,v in item.items():
                    result[key][name][field]=validate_value(v,fields[field],field)
                if key=='channels':channel_link(name,result[key][name]['address'])
        else:
            fields={f:t for f,_,t in known[key]}
            if set(value)-set(fields):raise ValueError('Unknown settings field')
            for field,v in value.items():result[key][field]=validate_value(v,fields[field],field)
    if result['planning']['capacity']>200 or not 1<=result['planning']['max_per_district']<=200:
        raise ValueError('Project capacities must be within 0–200 (district capacity at least 1)')
    if result['workflow']['review_days']<1:raise ValueError('Review target must be at least one day')
    return result


def validate_value(value,kind,name):
    if kind=='boolean':
        if not isinstance(value,bool):raise ValueError(f'{name} must be true or false')
    elif kind in ('number','fraction'):
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=100000000:
            raise ValueError(f'Invalid {name}')
        if kind=='fraction' and value>1:raise ValueError('Equity reservation must be between 0 and 1')
        if name in ('capacity','max_per_district','review_days','retention_days','review_frequency_days') and int(value)!=value:raise ValueError('Use a whole number')
    else:
        value=pilot.text(value,name,0,8000)
        if kind=='url':validate_url(value)
        if kind=='date' and value:
            from datetime import date
            date.fromisoformat(value)
    return value


def channel_link(channel,address):
    address=pilot.text(address,'channel address',0,300)
    if not address:return ''
    if channel in ('sms','ivr'):
        if not address.lstrip('+').isdigit() or not 6<=len(address.lstrip('+'))<=15:raise ValueError('Use a complete channel phone number')
        return ('sms:' if channel=='sms' else 'tel:')+address
    if channel=='email':
        import re
        if not re.fullmatch(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}',address):raise ValueError('Enter a valid channel mailbox')
        return 'mailto:'+address
    if channel in ('whatsapp','telegram'):
        validate_url(address)
        if urlparse(address).hostname not in ({'wa.me','api.whatsapp.com'} if channel=='whatsapp' else {'t.me'}):raise ValueError('Use the official channel link')
        return address
    if channel=='dialogflow':
        return '#dialogflow'
    return ''


def public_channels(p):
    settings=configuration(p)['channels'];result=[]
    for key,title in CHANNELS.items():
        c=settings[key];built_in=key in ('web','dialogflow')
        # A configured address is not proof of a working webhook or outbound provider.
        evidence=p['config'].get('channel_tests',{}).get(key,{})
        tested=evidence.get('config_hash')==pilot.digest(c) and evidence.get('status')=='passed'
        available=c['enabled'] and (built_in or tested)
        if available:
            status=('Rehearsal' if p['data_mode']=='synthetic' else 'Available') + (' - receipt verified' if tested else '')
        elif built_in:
            status='Paused in settings'
        else:
            status='Awaiting signed test + receipt'
        result.append({'id':key,'title':title,'available':available,'status':status,
                       'connection_status':'verified_receipt' if tested else 'built_in' if built_in else 'not_verified',
                       'verified_at':evidence.get('verified_at'),'last_test_event_id':evidence.get('event_id'),
                       'operator_action':'No provider gateway required' if built_in else ('Provider receipt verified' if tested else 'Connect gateway, send signed test and confirm receipt'),
                       'href':('/pilot/submit' if key=='web' else '#dialogflow' if key=='dialogflow' else channel_link(key,c['address'])) if available else '',
                       'address':c['address'] if available else ''})
    return result


def integration_status(p):
    config=configuration(p);items=[]
    for key,title,purpose,extension,variables in INTEGRATIONS:
        loaded=bool(current_app.extensions.get(extension)) if extension else False
        configured=loaded or all(current_app.config.get(v) for v in variables)
        items.append({'id':key,'title':title,'purpose':purpose,'status':'Client loaded; live test required' if loaded else 'Deployment configured; live test required' if configured else 'Not configured',
                      'requirements':variables,**config['integrations'][key]})
    return items


def decode_evidence(items):
    if not isinstance(items,list) or len(items)>15:raise ValueError('Attach no more than 15 evidence files')
    out=[];total=0
    allowed={'image/jpeg','image/png','image/webp','application/pdf','application/msword','application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    for item in items:
        if not isinstance(item,dict):raise ValueError('Invalid evidence file')
        name=pilot.text(item.get('name'),'filename',1,160);mime=item.get('mime_type')
        if mime not in allowed:raise ValueError('Use JPG, PNG, WebP, PDF, DOC or DOCX evidence')
        try:raw=base64.b64decode(item.get('data',''),validate=True)
        except Exception:raise ValueError('Invalid evidence encoding')
        total+=len(raw)
        if not raw or len(raw)>5*1024*1024 or total>8*1024*1024:raise ValueError('Evidence limit: 5 MiB per file and 8 MiB total')
        out.append({'name':name,'mime_type':mime,'data':item['data'],'sha256':hashlib.sha256(raw).hexdigest(),'size':len(raw)})
    return out


def save_evidence(rid,items):
    decoded=decode_evidence(items)
    # Serialize cumulative limits across concurrent upload requests.
    get_db().execute('UPDATE auditor_chain_state SET head_seq=head_seq WHERE id=1')
    existing={r['sha256']:r['size_bytes'] for r in evidence_manifest(rid)}
    combined={**existing,**{item['sha256']:item['size'] for item in decoded}}
    if len(combined)>15 or sum(combined.values())>8*1024*1024:
        raise ValueError('Ticket evidence limit: 15 files and 8 MiB total')
    for item in decoded:
        get_db().execute('''INSERT INTO pilot_evidence(request_id,sha256,name,mime_type,data_base64,size_bytes,created_at)
            VALUES(?,?,?,?,?,?,?) ON CONFLICT(request_id,sha256) DO NOTHING''',
            (rid,item['sha256'],item['name'],item['mime_type'],item['data'],item['size'],pilot.now()))


def evidence_manifest(rid):
    return pilot.rows('SELECT sha256,name,mime_type,size_bytes,created_at FROM pilot_evidence WHERE request_id=? ORDER BY created_at',(rid,))


def translate(p,ai,message,language):
    """Select one translation provider; never silently fall back to an external service."""
    if p['config'].get('translation_provider','vertex')=='vertex':return ai.translate_text(message,language,'en')
    client=current_app.extensions.get('google_translation_client')
    if not client or not client.client or client.location not in current_app.config.get('PILOT_MODEL_REGIONS',['asia-south1']):
        raise ValueError('A reviewed regional Cloud Translation client is required')
    result=client.client.translate_text(request={'parent':client.parent,'contents':[message],'mime_type':'text/plain','source_language_code':language,'target_language_code':'en'},timeout=30,retry=None)
    if not result.translations:raise ValueError('No translation returned')
    return {'translated_text':result.translations[0].translated_text,'provider_mode':'google_translation_v3_live','region':client.location,'model':'Cloud Translation'}
