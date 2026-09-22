"""Keep citizen corrections distinct from the actual provider output."""
from .pii_scrubber import scrub_text


def validate(data):
    for key in ('reviewed_transcript','reviewed_translation'):
        value=data.get(key)
        if value is not None and (not isinstance(value,str) or len(value)>6000):
            raise ValueError(key + ' must be text of at most 6000 characters')
        if value:
            data[key]=scrub_text(value)['scrubbed'].strip()


def transcript(data, speech):
    original=str(speech.get('transcript') or '').strip()
    corrected=str(data.get('reviewed_transcript') or '').strip()
    if corrected:
        speech['citizen_review']={'provider_transcript':original,'submitted_transcript':corrected,
                                  'changed':corrected!=original}
    return corrected or original


def outputs(data, translation, classification):
    # Preserve the model's labels before applying form selections.
    classification['provider_output']={k:classification.get(k) for k in ('category','urgency','sentiment','confidence')}
    corrected=str(data.get('reviewed_translation') or '').strip()
    if corrected:
        original=translation.get('translated_text') or ''
        translation['citizen_review']={'provider_translation':original,'submitted_translation':corrected,
                                      'changed':corrected!=original}
        translation['translated_text']=corrected
