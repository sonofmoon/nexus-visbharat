import re


_CODE_MIX_MAP = {
    # Tanglish / common Tamil transliterations
    'thanni': 'water',
    'tanni': 'water',
    'vellam': 'water',
    'roadu': 'road',
    'lightu': 'light',
    'sarakku': 'garbage',
    'kuppai': 'garbage',
    'safai': 'cleaning',
    'drainageu': 'drainage',
    # Telglish / common Telugu transliterations
    'neellu': 'water',
    'neeru': 'water',
    'roaddu': 'road',
    'currentu': 'electricity',
    'chetta': 'garbage',
    'kaluva': 'drainage',
}


def normalize_code_mix(text: str, language: str | None = None) -> dict:
    raw = str(text or '').strip()
    if not raw:
        return {
            'normalized_text': '',
            'replacements': [],
            'language': str(language or '').strip().lower() or 'unknown',
            'profile': 'indic_code_mix_v1',
        }

    normalized = re.sub(r'\s+', ' ', raw)
    replacements = []

    def _replace_word(m):
        word = m.group(0)
        key = word.lower()
        if key in _CODE_MIX_MAP:
            mapped = _CODE_MIX_MAP[key]
            replacements.append({'from': word, 'to': mapped})
            return mapped
        return word

    # Replace Latin code-mix tokens using word boundaries, without touching native Indic scripts
    normalized_text = re.sub(r'\b[A-Za-z]+\b', _replace_word, normalized)

    return {
        'normalized_text': normalized_text,
        'replacements': replacements,
        'language': str(language or '').strip().lower() or 'unknown',
        'profile': 'indic_code_mix_v1',
    }

