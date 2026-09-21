"""Private replay-safe audio storage. SQL holds bytes until both region copies exist."""
import base64
import hashlib
import json
from flask import current_app
from ..db import get_db


def read_audio(payload):
    if payload.get('audio_base64'):return base64.b64decode(payload['audio_base64'],validate=True)
    media=payload.get('audio_object')
    if not media:raise LookupError('No retained audio is available for this report')
    from google.cloud import storage
    approved={current_app.config.get('PILOT_AUDIO_BUCKET'),current_app.config.get('PILOT_AUDIO_RECOVERY_BUCKET')}
    if media['bucket'] not in approved:raise PermissionError('Audio location is outside this deployment')
    content=storage.Client().bucket(media['bucket']).blob(media['name'],generation=media['generation']).download_as_bytes(timeout=30)
    if hashlib.sha256(content).hexdigest()!=media['sha256']:raise ValueError('Audio integrity check failed')
    return content


def archive_audio(pr):
    from google.cloud import storage
    from google.api_core.exceptions import PreconditionFailed
    payload=json.loads(pr['payload_json']);bucket_name=current_app.config.get('PILOT_AUDIO_BUCKET')
    if not payload.get('audio_base64') or not bucket_name:return payload
    content=read_audio(payload);sha=hashlib.sha256(content).hexdigest();client=storage.Client()
    name=pr['pilot_id']+'/'+pr['request_id']+'/'+sha
    primary=None
    for destination in (bucket_name,current_app.config.get('PILOT_AUDIO_RECOVERY_BUCKET')):
        if not destination:raise ValueError('Configure both private audio regions')
        blob=client.bucket(destination).blob(name);blob.metadata={'sha256':sha}
        try:blob.upload_from_string(content,content_type=payload['mime_type'],if_generation_match=0,timeout=30)
        except PreconditionFailed:
            blob.reload(timeout=15)
            if (blob.metadata or {}).get('sha256')!=sha:raise ValueError('Stored audio digest conflicts with the receipt')
        if destination==bucket_name:primary={'bucket':destination,'name':name,'generation':int(blob.generation),'sha256':sha}
    payload['audio_object']=primary;payload['audio_base64']=''
    get_db().execute('UPDATE pilot_requests SET payload_json=? WHERE request_id=?',(json.dumps(payload),pr['request_id']));get_db().commit()
    return payload
