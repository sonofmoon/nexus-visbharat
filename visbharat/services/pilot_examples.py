"""Prepared fictional requests, never live citizen or Google execution evidence."""

EXAMPLES = [
    {'id':'vellore-water','district':'Vellore','state':'Tamil Nadu','language':'ta',
     'location_id':'vellore-1','category':'Water Supply',
     'text':'எங்கள் பகுதியில் குடிநீர் தினமும் ஒரு மணி நேரம் மட்டுமே வருகிறது. பள்ளி அருகில் குழாயில் கசிவு உள்ளது. தயவுசெய்து சரிசெய்யவும்.',
     'translation':'Our area receives drinking water for only one hour daily. A pipe near the school is leaking. Please repair it.'},
    {'id':'tirupati-sanitation','district':'Tirupati','state':'Andhra Pradesh','language':'te',
     'location_id':'tirupati-1','category':'Sanitation',
     'text':'మా వీధిలో కాలువ మూసుకుపోయింది. వర్షం వచ్చినప్పుడు పాఠశాల దగ్గర నీరు నిలుస్తోంది. దయచేసి కాలువను శుభ్రం చేయండి.',
     'translation':'The drain in our street is blocked. Rainwater collects near the school. Please clear the drain.'},
    {'id':'bengaluru-road','district':'Bengaluru Urban','state':'Karnataka','language':'kn',
     'location_id':'bengaluru_urban-1','category':'Road',
     'text':'ನಮ್ಮ ಪ್ರದೇಶದ ಬಸ್ ನಿಲ್ದಾಣದ ಬಳಿ ರಸ್ತೆಯಲ್ಲಿ ದೊಡ್ಡ ಗುಂಡಿಗಳಿವೆ. ಶಾಲೆಗೆ ಹೋಗುವ ಮಕ್ಕಳಿಗೆ ತೊಂದರೆಯಾಗುತ್ತಿದೆ. ದಯವಿಟ್ಟು ರಸ್ತೆ ದುರಸ್ತಿ ಮಾಡಿ.',
     'translation':'There are large potholes near the bus stop in our area. Children travelling to school are affected. Please repair the road.'},
]


def available_examples(programme, locations):
    if programme['data_mode'] != 'synthetic':
        return []
    enrolled={row['location_id'] for row in locations}
    config=programme['config']
    return [dict(item) for item in EXAMPLES if item['location_id'] in enrolled
            and item['language'] in config['languages'] and item['category'] in config.get('categories',[])]
