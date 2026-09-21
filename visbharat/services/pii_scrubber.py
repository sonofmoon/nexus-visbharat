import re


PHONE_RE = re.compile(r'(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)')
EMAIL_RE = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
AADHAAR_RE = re.compile(r'(?<!\d)(?:\d\s*){12}(?!\d)')
PAN_RE = re.compile(r'\b[A-Z]{5}[0-9]{4}[A-Z]\b', re.IGNORECASE)
PIN_RE = re.compile(r'(?<!\d)\d{6}(?!\d)')


def _mask(value: str, keep_last: int = 2):
    if not value:
        return ''
    clean = str(value)
    visible = clean[-keep_last:] if len(clean) > keep_last else clean
    return f"[REDACTED:{'*' * max(len(clean) - len(visible), 0)}{visible}]"


def _clean_digits(v: str) -> str:
    return re.sub(r'\D', '', str(v or ''))


def scrub_text(text: str):
    original = str(text or '')
    scrubbed = original
    rules_triggered = []

    def apply(pattern, tag, repl_fn=None):
        nonlocal scrubbed

        def _replace(match):
            value = match.group(0)
            if repl_fn:
                return repl_fn(value)
            return f"[{tag}]"

        new_val, count = pattern.subn(_replace, scrubbed)
        if count > 0:
            rules_triggered.append({'rule': tag.lower(), 'matches': int(count)})
            scrubbed = new_val

    apply(EMAIL_RE, 'PII_EMAIL', lambda v: f"[PII_EMAIL:{_mask(v, keep_last=3)}]")
    apply(PHONE_RE, 'PII_PHONE', lambda v: f"[PII_PHONE:{_mask(_clean_digits(v), keep_last=4)}]")
    apply(AADHAAR_RE, 'PII_AADHAAR', lambda v: f"[PII_AADHAAR:{_mask(_clean_digits(v), keep_last=4)}]")
    apply(PAN_RE, 'PII_PAN', lambda v: f"[PII_PAN:{_mask(v.upper(), keep_last=2)}]")

    lowered = scrubbed.lower()
    address_hits = 0
    for marker in (' street ', ' road ', ' lane ', ' nagar ', ' colony ', ' apartment ', ' near ', ' opposite '):
        if marker in f" {lowered} ":
            address_hits += 1
    if address_hits:
        scrubbed = scrubbed.replace('\n', ' ').strip()
        if not scrubbed.startswith("[PII_ADDRESS_REDACTED]"):
            scrubbed = f"[PII_ADDRESS_REDACTED] {scrubbed}"
        rules_triggered.append({'rule': 'pii_address_heuristic', 'matches': int(address_hits)})

    apply(PIN_RE, 'PII_PIN', lambda v: f"[PII_PIN:{_mask(v, keep_last=2)}]")

    risk_level = 'none'
    if rules_triggered:
        severe = {'pii_aadhaar', 'pii_pan'}
        medium = {'pii_phone', 'pii_email', 'pii_address_heuristic'}
        triggered = {str(r.get('rule') or '').lower() for r in rules_triggered}
        if triggered.intersection(severe):
            risk_level = 'high'
        elif triggered.intersection(medium):
            risk_level = 'medium'
        else:
            risk_level = 'low'

    return {
        'original': original,
        'scrubbed': scrubbed,
        'changed': scrubbed != original,
        'rules_triggered': rules_triggered,
        'risk_level': risk_level,
    }


def scrub_payload_fields(payload: dict, text_keys=None):
    if not isinstance(payload, dict):
        return payload, {'changed': False, 'fields': []}

    keys = text_keys or ['text', 'body', 'subject', 'address', 'sender', 'from', 'phone', 'supporter_ref']
    out = dict(payload)
    fields = []
    changed = False
    highest = 'none'
    rank = {'none': 0, 'low': 1, 'medium': 2, 'high': 3}

    for key in keys:
        if key in out and out.get(key) is not None:
            result = scrub_text(str(out.get(key)))
            out[key] = result['scrubbed']
            if result['changed']:
                changed = True
            fields.append({'field': key, 'changed': result['changed'], 'rules_triggered': result['rules_triggered'], 'risk_level': result['risk_level']})
            if rank.get(result['risk_level'], 0) > rank.get(highest, 0):
                highest = result['risk_level']

    return out, {'changed': changed, 'fields': fields, 'risk_level': highest}
