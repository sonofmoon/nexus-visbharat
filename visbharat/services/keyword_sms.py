from dataclasses import dataclass


@dataclass
class KeywordSmsCommand:
    command: str
    argument: str = ''


def parse_keyword_sms(text: str) -> KeywordSmsCommand:
    raw = str(text or '').strip()
    if not raw:
        return KeywordSmsCommand(command='invalid')

    parts = [p for p in raw.split() if p]
    if len(parts) < 2:
        return KeywordSmsCommand(command='invalid')

    if parts[0].upper() != 'NV':
        return KeywordSmsCommand(command='invalid')

    verb = parts[1].upper()
    arg = ' '.join(parts[2:]).strip()

    if verb == 'NEW':
        return KeywordSmsCommand(command='new', argument=arg)
    if verb == 'STATUS':
        return KeywordSmsCommand(command='status', argument=arg)
    if verb == 'COSIGN':
        return KeywordSmsCommand(command='cosign', argument=arg)
    return KeywordSmsCommand(command='invalid')


def keyword_help_message():
    return 'Use: NV NEW <issue>, NV STATUS <request_id>, NV COSIGN <cosign_token>'
