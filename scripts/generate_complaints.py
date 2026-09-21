import csv
import random
from pathlib import Path
from datetime import datetime, timedelta

# Target scope: 3 states, 6 pilot districts, 10 categories
DISTRICTS = [
    {"district": "Chennai", "state": "Tamil Nadu", "lat": 13.0827, "lng": 80.2707, "lang": "ta"},
    {"district": "Vellore", "state": "Tamil Nadu", "lat": 12.9165, "lng": 79.1325, "lang": "ta"},
    {"district": "Karur", "state": "Tamil Nadu", "lat": 10.9601, "lng": 78.0766, "lang": "ta"},
    {"district": "Visakhapatnam", "state": "Andhra Pradesh", "lat": 17.6868, "lng": 83.2185, "lang": "te"},
    {"district": "Tirupati", "state": "Andhra Pradesh", "lat": 13.6288, "lng": 79.4192, "lang": "te"},
    {"district": "Hyderabad", "state": "Telangana", "lat": 17.3850, "lng": 78.4867, "lang": "te"},
]

CATEGORIES = [
    'Road',
    'Water Supply',
    'Electricity',
    'Health',
    'Education',
    'Sanitation',
    'Digital Connectivity',
    'Transport',
    'Housing',
    'Other'
]

# 3-Language Template Pool
TEMPLATES = {
    'Road': {
        'ta': [
            ("தெருவில் பெரிய பள்ளங்கள் ஏற்பட்டுள்ளன, வாகன ஓட்டிகள் விபத்தில் சிக்குகின்றனர்.", "Large potholes on the main street causing accidents for commuters."),
            ("சாலை மேற்பரப்பு முற்றிலும் சேதமடைந்துள்ளது, தார் கொட்டப்பட வேண்டும்.", "Road surface is completely damaged, tar paving is urgently needed."),
            ("மழைநீரால் சாலை அரித்து செல்லப்பட்டு பாதசாரிகள் நடக்க முடியாமல் தவிக்கின்றனர்.", "Rainwater has eroded the road, leaving pedestrians unable to walk."),
            ("வேகத்தடை சீராக அமைக்கப்படவில்லை, உடனடியாக சரிசெய்யவும்.", "Speed breaker is improperly constructed, please fix immediately."),
            ("தெரு முனை சாலையில் மண் குவியல் சேர்ந்து போக்குவரத்து அடைபட்டுள்ளது.", "Dirt piles at street junction blocking local traffic flow.")
        ],
        'te': [
            ("రోడ్డుపై పెద్ద గుంతలు పడ్డాయి, వాహనదారులు ప్రమాదాలకు గురవుతున్నారు.", "Potholes on main road causing frequent accidents for commuters."),
            ("రోడ్డు ఉపరితలం పూర్తిగా దెబ్బతింది, వెంటనే రీ-తార్ వేయాలి.", "Road surface completely damaged, tar re-surfacing needed immediately."),
            ("వర్షపు నీటికి రోడ్డు కొట్టుకుపోయి బాటసారులు తీవ్ర ఇబ్బంది పడుతున్నారు.", "Rainwater washed away road, pedestrians suffering greatly."),
            ("స్పీడ్ బ్రేకర్ సరిగ్గా నిర్మించలేదు, వెంటనే సరిచేయండి.", "Speed breaker improperly built, requires urgent repair."),
            ("సందు చివరి రోడ్డుపై మట్టి కుప్పలు చేరి రాకపోకలు నిలిచిపోయాయి.", "Dirt heaps accumulated at street corner blocking traffic.")
        ],
        'en': [
            ("Severe potholes on main road near bus station.", "Severe potholes on main road near bus station."),
            ("Road digging work incomplete for over two weeks.", "Road digging work incomplete for over two weeks."),
            ("Dangerous open trench along street pavement.", "Dangerous open trench along street pavement."),
            ("Street paving broken causing severe dust pollution.", "Street paving broken causing severe dust pollution."),
            ("No street markings or warning signs at blind curve.", "No street markings or warning signs at blind curve.")
        ]
    },
    'Water Supply': {
        'ta': [
            ("கடந்த 3 நாட்களாக குடிநீர் விநியோகம் முற்றிலும் நிறுத்தப்பட்டுள்ளது.", "Drinking water supply has been completely halted for 3 days."),
            ("குடிநீரில் கழிவுநீர் கலந்து துர்நாற்றம் வீசுகிறது, உடனே பரிசோதிக்கவும்.", "Sewage is mixing with drinking water causing foul smell."),
            ("குடிநீர் குழாயில் உடைப்பு ஏற்பட்டு ஆயிரக்கணக்கான லிட்டர் நீர் வீணாகிறது.", "Major pipe burst in water line wasting thousands of liters."),
            ("குறைந்த அழுத்தத்தில் மட்டுமே நீர் வருகிறது, மேல்நிலை தொட்டி நிரம்பவில்லை.", "Water supplied at very low pressure, overhead tank not filling."),
            ("பொதுக்குழாயில் அடைப்பு ஏற்பட்டு பொதுமக்கள் நீண்ட வரிசையில் காத்திருக்கின்றனர்.", "Public tap blocked leading to long queues for daily water.")
        ],
        'te': [
            ("గత 3 రోజులుగా మంచినీటి సరఫరా పూర్తిగా నిలిచిపోయింది.", "Drinking water supply completely disrupted for the past 3 days."),
            ("మంచినీటిలో మురుగునీరు కలసి దుర్వాసన వస్తోంది.", "Sewage mixing into drinking water lines causing foul odor."),
            ("నీటి పైపులైన్ పగిలి వేలాది లీటర్ల మంచినీరు వృధా అవుతోంది.", "Water pipeline burst wasting thousands of liters of clean water."),
            ("తక్కువ ఒత్తిడితో నీరు సరఫరా కావడం వల్ల ట్యాంకులు నిండటం లేదు.", "Low pressure water supply prevents overhead tanks from filling."),
            ("పబ్లిక్ కుళాయి మూసుకుపోవడంతో ప్రజలు గంటల తరబడి క్యూలో ఉన్నారు.", "Public tap clogged forcing citizens into long queues.")
        ],
        'en': [
            ("No water supply in ward 12 for the last 48 hours.", "No water supply in ward 12 for the last 48 hours."),
            ("Contaminated muddy water coming from municipal taps.", "Contaminated muddy water coming from municipal taps."),
            ("Water pipeline leak flooding local street junction.", "Water pipeline leak flooding local street junction."),
            ("Irregular supply timings without prior public notice.", "Irregular supply timings without prior public notice."),
            ("Public hand pump broken and leaking near market.", "Public hand pump broken and leaking near market.")
        ]
    },
    'Electricity': {
        'ta': [
            ("இரவு நேரத்தில் அடிக்கடி மின்வெட்டு ஏற்படுகிறது, மின்மாற்றி பழுது அடையலாம்.", "Frequent power outages at night, transformer may be faulty."),
            ("தெரு விளக்குகள் வாரக்கணக்கில் எரியாமல் இருட்டாக உள்ளது.", "Street lights out for weeks causing safety hazard in darkness."),
            ("மின்கம்பம் சாய்ந்த நிலையில் உள்ளது, உடனடியாக நிமிர்த்த வேண்டும்.", "Electric pole leaning dangerously, needs immediate straightening."),
            ("உயர் மின்னழுத்த கம்பிகள் தாழ்வாக தொங்குகின்றன.", "High voltage wires hanging dangerously low."),
            ("மின்சார மீட்டர் மிக வேகமாக ஓடுகிறது, தவறான கட்டணம் வந்துள்ளது.", "Electricity meter running excessively fast, incorrect billing.")
        ],
        'te': [
            ("రాత్రి వేళల్లో తరచూ విద్యుత్ కోతలు విధించడం వల్ల ఇబ్బందిగా ఉంది.", "Frequent night power cuts causing severe hardship."),
            ("వీధి దీపాలు వారాల తరబడి వెలగడం లేదు.", "Streetlights non-functional for weeks leaving area pitch dark."),
            ("విద్యుత్ స్తంభం ఒరిగిపోయి ప్రమాదకరంగా మారింది.", "Electric pole leaning dangerously over road."),
            ("హై టెన్షన్ విద్యుత్ తీగలు కిందకు వేలాడుతున్నాయి.", "High tension electric wires dangling dangerously close to ground."),
            ("కరెంట్ మీటర్ వేగంగా తిరుగుతోంది, ఎక్కువ బిల్లు వచ్చింది.", "Electricity meter running abnormally fast resulting in inflated bill.")
        ],
        'en': [
            ("Frequent unannounced power cuts lasting over 4 hours.", "Frequent unannounced power cuts lasting over 4 hours."),
            ("Streetlights not working on main residential avenue.", "Streetlights not working on main residential avenue."),
            ("Transformer sparking and emitting loud buzzing noise.", "Transformer sparking and emitting loud buzzing noise."),
            ("Low voltage issues damaging domestic appliances.", "Low voltage issues damaging domestic appliances."),
            ("Overhead electrical wires dangling low near trees.", "Overhead electrical wires dangling low near trees.")
        ]
    },
    'Health': {
        'ta': [
            ("அரசு மருத்துவமனையில் போதிய மருந்துகள் மற்றும் மருத்துவர்கள் இல்லை.", "Government clinic lacks essential medicines and doctors."),
            ("கொசுத் தொல்லை அதிகமாக உள்ளது, மருந்து புகை தெளிக்க வேண்டும்.", "Severe mosquito infestation in the ward, fogging required."),
            ("ஆரம்ப சுகாதார நிலையத்தில் ஆம்புலன்ஸ் சேவை இயங்கவில்லை.", "Primary health center ambulance service is non-functional."),
            ("மருத்துவக் கழிவுகள் திறந்தவெளியில் கொட்டப்பட்டு நோய் பரவும் அபாயம் உள்ளது.", "Medical waste dumped in open area creating disease risk."),
            ("தடுப்பூசி முகாம் சரியாக நடத்தப்படவில்லை.", "Vaccination camp is not being conducted regularly.")
        ],
        'te': [
            ("ప్రభుత్వ ఆసుపత్రిలో తగినన్ని మందులు, డాక్టర్లు అందుబాటులో లేరు.", "Government hospital lacks required medicines and medical staff."),
            ("దోమల బెడద ఎక్కువగా ఉంది, వెంటనే ఫాగింగ్ చేయించాలి.", "Severe mosquito breeding in locality, chemical fogging required."),
            ("ప్రాథమిక ఆరోగ్య కేంద్రంలో అంబులెన్స్ సేవలు అందుబాటులో లేవు.", "Primary Health Centre ambulance service is out of service."),
            ("ఆసుపత్రి వ్యర్థాలను బయట పారబోయడం వల్ల వ్యాధులు వ్యాపించే ప్రమాదం ఉంది.", "Hospital waste dumped in open area posing disease risk."),
            ("ఉచిత టీకా కార్యక్రమం సరిగ్గా నిర్వహించడం లేదు.", "Free immunization drive not being conducted properly.")
        ],
        'en': [
            ("Primary health centre doctor absent during OPD hours.", "Primary health centre doctor absent during OPD hours."),
            ("Mosquito breeding in stagnant water, fogging needed urgently.", "Mosquito breeding in stagnant water, fogging needed urgently."),
            ("Lack of essential anti-snake venom vials at local clinic.", "Lack of essential anti-snake venom vials at local clinic."),
            ("Unsanitary conditions inside government dispensary.", "Unsanitary conditions inside government dispensary."),
            ("Ambulance response time exceeds one hour in emergency.", "Ambulance response time exceeds one hour in emergency.")
        ]
    },
    'Education': {
        'ta': [
            ("அரசு பள்ளிக் கட்டிடத்தின் மேற்கூரை பழுதடைந்து நீர் கசிகிறது.", "Government school building roof damaged and leaking severely."),
            ("பள்ளியில் மாணவிகளுக்கு போதிய கழிப்பறை வசதி இல்லை.", "Lack of proper functioning toilets for female students at school."),
            ("பள்ளி வகுப்பறைகளில் மின்விசிறி மற்றும் குடிநீர் வசதி இல்லை.", "Classrooms lack basic ceiling fans and drinking water facilities."),
            ("பள்ளி விளையாட்டு மைதானம் குப்பைக் கிடங்காக மாறியுள்ளது.", "School playground turned into dumping ground."),
            ("கணினி ஆய்வகத்தில் சாதனங்கள் பழுதடைந்து இயங்காமல் உள்ளன.", "Computer lab equipment broken and non-functional.")
        ],
        'te': [
            ("ప్రభుత్వ పాఠశాల భవనం వర్షానికి కారుతోంది.", "Government school building leaking badly during rain."),
            ("బడిలో బాలికలకు ప్రత్యేక శౌచాలయ వసతి లేదు.", "Lack of separate functional toilets for girls in school."),
            ("తరగతి గదుల్లో పంకాలు, తాగునీటి సౌకర్యం లేదు.", "Classrooms lack working ceiling fans and clean drinking water."),
            ("పాఠశాల ఆటస్థలం చెత్తమయంగా మారింది.", "School playground turned into a garbage dumping ground."),
            ("కంప్యూటర్ ల్యాబ్‌లో సిస్టమ్‌లు పనిచేయడం లేదు.", "Computers in school lab damaged and not working.")
        ],
        'en': [
            ("Dilapidated boundary wall of municipal high school.", "Dilapidated boundary wall of municipal high school."),
            ("Drinking water purifier broken at government primary school.", "Drinking water purifier broken at government primary school."),
            ("Insufficient benches for students in primary classes.", "Insufficient benches for students in primary classes."),
            ("Mid-day meal kitchen sanitation needs inspection.", "Mid-day meal kitchen sanitation needs inspection."),
            ("Teacher shortages affecting science curriculum classes.", "Teacher shortages affecting science curriculum classes.")
        ]
    },
    'Sanitation': {
        'ta': [
            ("சாக்கடை நீர் வீதிகளில் பெருகி சுகாதார சீர்கேடு ஏற்பட்டுள்ளது.", "Sewage overflowing onto streets creating health hazard."),
            ("குப்பைத் தொட்டிகள் நிரம்பி வழிகின்றன, பல நாட்களாக அகற்றப்படவில்லை.", "Public dustbins overflowing, not cleared for days."),
            ("பொது கழிப்பறை அடைபட்டு துர்நாற்றம் வீசுகிறது.", "Public toilet blocked and emitting severe unbearable stench."),
            ("கழிவுநீர் கால்வாய் தூர்வாரப்படாமல் அடைப்பு ஏற்பட்டுள்ளது.", "Drainage channel not desilted causing severe clog."),
            ("இறந்த விலங்கின் உடல் சாலையில் அகற்றப்படாமல் கிடக்கிறது.", "Dead animal carcass lying uncleared on main road.")
        ],
        'te': [
            ("డ్రైనేజీ నీరు రోడ్లపై పారి అపరిశుభ్రత నెలకొంది.", "Drainage water overflowing on roads creating health hazard."),
            ("చెత్త కుండీలు నిండిపోయి రోజుల తరబడి తరలించడం లేదు.", "Garbage bins overflowing, not cleared for days."),
            ("పబ్లిక్ టాయిలెట్లు మూసుకుపోయి దుర్వాసన వస్తున్నాయి.", "Public toilets blocked emitting foul smell."),
            ("కాల్వల్లో పూడిక తీయకపోవడంతో మురుగునీరు నిలిచిపోయింది.", "Drains choked with silt causing stagnant wastewater."),
            ("రోడ్డుపై చనిపోయిన జంతువు కళేబరం తొలగించలేదు.", "Dead animal carcass on road not removed by sanitation staff.")
        ],
        'en': [
            ("Overflowing garbage bin spilling waste onto footpath.", "Overflowing garbage bin spilling waste onto footpath."),
            ("Clogged storm drain causing sewage backup near houses.", "Clogged storm drain causing sewage backup near houses."),
            ("Public toilet facility locked and dirty in central market.", "Public toilet facility locked and dirty in central market."),
            ("Garbage collection vehicle skipping street for three days.", "Garbage collection vehicle skipping street for three days."),
            ("Open dumping of construction debris along highway line.", "Open dumping of construction debris along highway line.")
        ]
    },
    'Digital Connectivity': {
        'ta': [
            ("இப்பகுதியில் பிஎஸ்என்எல் பிராட்பேண்ட் சேவை அடிக்கடி துண்டிக்கப்படுகிறது.", "BSNL broadband network frequently disconnected in the locality."),
            ("பொது சேவை மையத்தில் இணைய வேகம் மிகவும் குறைவாக உள்ளது.", "Common Service Centre (CSC) internet speed is extremely slow."),
            ("மொபைல் நெட்வொர்க் டவர் சிக்னல் இல்லை, அழைப்புகள் துண்டிக்கப்படுகின்றன.", "Mobile network tower signal unavailable causing dropped calls."),
            ("இணைய கேபிள் கம்பிகள் தெருவில் அறுந்து தொங்குகின்றன.", "Internet fiber cables snapped and dangling dangerously on street."),
            ("இ-சேவை மையத்தில் இணைய இணைப்பு இல்லாததால் சேவைகள் முடங்கியுள்ளன.", "E-seva center services stalled due to network connectivity failure.")
        ],
        'te': [
            ("మా ప్రాంతంలో బిఎస్ఎన్ఎల్ ఇంటర్నెట్ తరచూ కట్ అవుతోంది.", "BSNL internet connectivity frequently dropping in local area."),
            ("మీ-సేవ కేంద్రంలో ఇంటర్నెట్ వేగం చాలా తక్కువగా ఉంది.", "MeeSeva service center internet speed extremely slow."),
            ("మొబైల్ సిగ్నల్స్ సరిగ్గా అందడం లేదు, కాల్ డ్రాప్స్ అవుతున్నాయి.", "Weak mobile signal coverage leading to constant call drops."),
            ("ఇంటర్నెట్ కేబుల్స్ రోడ్డుపై తెగిపడి ఉన్నాయి.", "Optical fiber internet wires snapped and hanging on street."),
            ("డిజిటల్ సేవలు నెట్‌వర్క్ సమస్యల వల్ల ఆగిపోయాయి.", "Online public services halted due to server connection outage.")
        ],
        'en': [
            ("Slow internet speed at citizen facilitation e-kiosk.", "Slow internet speed at citizen facilitation e-kiosk."),
            ("Optical fiber cable snapped hanging low near street lamp.", "Optical fiber cable snapped hanging low near street lamp."),
            ("Poor 4G/5G mobile tower coverage causing call drops.", "Poor 4G/5G mobile tower coverage causing call drops."),
            ("CSC center offline due to broadband line fault.", "CSC center offline due to broadband line fault."),
            ("Digital payment terminal at bus depot frequently offline.", "Digital payment terminal at bus depot frequently offline.")
        ]
    },
    'Transport': {
        'ta': [
            ("நகரப் பேருந்துகள் குறித்த நேரத்தில் வருவதில்லை, பயணிகள் தவிக்கின்றனர்.", "City buses not adhering to schedule causing long delays."),
            ("பேருந்து நிறுத்தத்தில் நிழற்குடை சேதமடைந்துள்ளது.", "Bus stop passenger shelter severely damaged."),
            ("ஆட்டோ ஓட்டுநர்கள் அளவுக்கு அதிகமான கட்டணம் வசூலிக்கின்றனர்.", "Auto rickshaw drivers overcharging citizens beyond meter rates."),
            ("பள்ளி நேரங்களில் கூடுதல் பேருந்துகள் இயக்கப்பட வேண்டும்.", "Additional buses needed during school rush hours."),
            ("போக்குவரத்து சமிக்ஞை விளக்குகள் எரியாமல் குழப்பம் ஏற்படுகிறது.", "Traffic signal lights non-functional causing severe congestion.")
        ],
        'te': [
            ("సిటీ బస్సులు సమయానికి రాక ప్రయాణికులు తీవ్ర ఇబ్బంది పడుతున్నారు.", "City buses not arriving on schedule causing delay for commuters."),
            ("బస్సు షెల్టర్ దెబ్బతిని వర్షానికి నిలబడలేకపోతున్నాము.", "Bus stop shelter broken, unable to take cover during rain."),
            ("ఆటో డ్రైవర్లు అదనపు ఛార్జీలు వసూలు చేస్తున్నారు.", "Auto drivers charging excess fare beyond government rates."),
            ("పాఠశాల వేళల్లో అదనపు బస్సులు నడపాలి.", "Need extra bus trips during school and office rush hours."),
            ("ట్రాఫిక్ సిగ్నల్స్ పనిచేయక ట్రాఫిక్ జామ్ అవుతోంది.", "Traffic signals non-operational causing massive traffic jams.")
        ],
        'en': [
            ("Bus frequency inadequate during peak morning hours.", "Bus frequency inadequate during peak morning hours."),
            ("Bus shelter roof broken exposing passengers to heat.", "Bus shelter roof broken exposing passengers to heat."),
            ("Traffic light signal failing at major intersection.", "Traffic light signal failing at major intersection."),
            ("Auto rickshaw drivers refusing short distance rides.", "Auto rickshaw drivers refusing short distance rides."),
            ("Lack of pedestrian crossing signal near hospital.", "Lack of pedestrian crossing signal near hospital.")
        ]
    },
    'Housing': {
        'ta': [
            ("அரசு வீட்டுவசதி குடியிருப்பு சுவர்களில் விரிசல் ஏற்பட்டுள்ளது.", "Cracks observed in government housing tenement walls."),
            ("குடியிருப்பு வளாகத்தில் குடிநீர் தொட்டி தூய்மைப்படுத்தப்படவில்லை.", "Overhead water storage tank in housing colony not cleaned."),
            ("வீட்டுமனை பட்டா வழங்குவதில் தாமதம் ஏற்படுகிறது.", "Delays in issuing land patta documentation for residents."),
            ("குடியிருப்பு பகுதியில் மழைநீர் தேங்கி அடித்தளத்தை பாதிக்கிறது.", "Stagnant rainwater in housing colony affecting building foundation."),
            ("வீட்டுவசதி வாரிய கட்டிட மின்தூக்கி (லிப்ட்) இயங்கவில்லை.", "Housing board building elevator non-functional.")
        ],
        'te': [
            ("ప్రభుత్వ గృహ నిర్మాణ గోడలకు బిటలు వారాయి.", "Cracks developing on government housing scheme building walls."),
            ("కాలనీ నీటి ట్యాంకును శుభ్రపరచలేదు.", "Colony overhead water tank not cleaned for months."),
            ("ఇళ్ల పట్టాలు ఇవ్వడంలో తీవ్ర జాప్యం జరుగుతోంది.", "Severe delay in distribution of house site pattas."),
            ("వర్షపు నీరు ఇళ్ల ముందు నిలిచి పునాదులు దెబ్బతింటున్నాయి.", "Stagnant rainwater around houses damaging foundations."),
            ("హౌసింగ్ బోర్డు బిల్డింగ్ లిఫ్ట్ పనిచేయడం లేదు.", "Housing board apartment building elevator not working.")
        ],
        'en': [
            ("Water leakage from terrace of housing board apartment.", "Water leakage from terrace of housing board apartment."),
            ("Delay in allotment of patta for Economically Weaker Section (EWS) quarters.", "Delay in allotment of patta for Economically Weaker Section (EWS) quarters."),
            ("Broken staircase railing in public housing colony.", "Broken staircase railing in public housing colony."),
            ("Cracks observed in outer plastering of tenement block.", "Cracks observed in outer plastering of tenement block."),
            ("Stormwater logging inside housing complex basement.", "Stormwater logging inside housing complex basement.")
        ]
    },
    'Other': {
        'ta': [
            ("தெரு நாய்களின் தொல்லை அதிகரித்து பொதுமக்கள் அச்சமடைந்துள்ளனர்.", "Stray dog menace increased significantly causing resident fear."),
            ("பூங்காவில் உடற்பயிற்சி உபகரணங்கள் உடைந்த நிலையில் உள்ளன.", "Public park open gym equipment damaged and unsafe."),
            ("இரவு நேரத்தில் திறந்தவெளியில் மது அருந்துவது தடுக்கப்பட வேண்டும்.", "Open drinking at public space during night must be checked."),
            ("வீதி விளம்பரம் பானர்கள் போக்குவரத்திற்கு இடையூறாக உள்ளன.", "Illegal advertising banners obstructing street visibility."),
            ("சுடுகாடு பாதையில் மின்விளக்கு வசதி செய்யப்பட வேண்டும்.", "Burial ground access pathway requires proper street lighting.")
        ],
        'te': [
            ("వీధి కుక్కల బెడద వల్ల పిల్లలు బయటకు రావడానికి భయపడుతున్నారు.", "Stray dog menace scaring children from coming outside."),
            ("పార్కులో వ్యాయామ పరికరాలు విరిగిపోయాయి.", "Exercise equipment in public park broken and unusable."),
            ("రాత్రి వేళల్లో బహిరంగంగా మద్యం సేవించడం అరికట్టాలి.", "Open drinking in public places at night needs immediate police check."),
            ("అక్రమ రవాణా ఫ్లెక్సీలు ట్రాఫిక్‌కు ఆటంకంగా మారాయి.", "Illegal advertising flex banners blocking road visibility."),
            ("స్మశాన వాటిక దారిలో లైట్లు ఏర్పాటు చేయాలి.", "Graveyard approach road needs street lights installation.")
        ],
        'en': [
            ("Stray dog packs creating safety issue for morning walkers.", "Stray dog packs creating safety issue for morning walkers."),
            ("Broken bench and gym equipment in public park.", "Broken bench and gym equipment in public park."),
            ("Encroachments on footpath by roadside vendors.", "Encroachments on footpath by roadside vendors."),
            ("Illegal hoardings obscuring road directional signage.", "Illegal hoardings obscuring road directional signage."),
            ("Lack of streetlights on cemetery approach road.", "Lack of streetlights on cemetery approach road.")
        ]
    }
}

URGENCIES = ['Routine', 'Urgent', 'Emergency']
URGENCY_WEIGHTS = [0.5, 0.35, 0.15]

SENTIMENTS = ['Negative', 'Very Negative', 'Neutral']
SENTIMENT_WEIGHTS = [0.6, 0.3, 0.1]

STATUSES = ['New', 'Under Review', 'Assigned', 'In Progress', 'Resolved', 'Closed']
STATUS_WEIGHTS = [0.3, 0.2, 0.2, 0.15, 0.1, 0.05]

SOURCES = ['Web Form', 'WhatsApp', 'Voice IVR', 'Telegram', 'SMS Keyword', 'IVR Missed Call']

START_DATE = datetime(2026, 8, 1)
END_DATE = datetime(2026, 9, 10)

def random_date(start, end):
    delta = end - start
    random_days = random.randint(0, delta.days)
    random_seconds = random.randint(0, 86400)
    dt = start + timedelta(days=random_days, seconds=random_seconds)
    return dt.strftime('%Y-%m-%d')

def generate():
    records = []
    comp_id_counter = 3001

    # For each district (6) and category (10), generate 50 complaints -> Total 3,000
    for dist_info in DISTRICTS:
        district_name = dist_info['district']
        state_name = dist_info['state']
        base_lat = dist_info['lat']
        base_lng = dist_info['lng']
        primary_lang = dist_info['lang']

        for cat in CATEGORIES:
            for i in range(50):
                # 60% native language, 40% English
                is_native = random.random() < 0.6
                chosen_lang = primary_lang if is_native else 'en'

                tpl_list = TEMPLATES[cat][chosen_lang]
                orig_text, trans_text = random.choice(tpl_list)

                # Geo jitter (+/- 0.035 degrees)
                lat = round(base_lat + random.uniform(-0.035, 0.035), 4)
                lng = round(base_lng + random.uniform(-0.035, 0.035), 4)

                urgency = random.choices(URGENCIES, URGENCY_WEIGHTS)[0]
                sentiment = random.choices(SENTIMENTS, SENTIMENT_WEIGHTS)[0]
                status = random.choices(STATUSES, STATUS_WEIGHTS)[0]
                source = random.choice(SOURCES)
                date_str = random_date(START_DATE, END_DATE)

                records.append({
                    'id': f"COMP-{comp_id_counter}",
                    'date': date_str,
                    'district': district_name,
                    'state': state_name,
                    'lat': lat,
                    'lng': lng,
                    'language': chosen_lang,
                    'original_text': orig_text,
                    'translated_text': trans_text,
                    'category': cat,
                    'urgency': urgency,
                    'sentiment': sentiment,
                    'status': status,
                    'source': source
                })
                comp_id_counter += 1

    # Shuffle to ensure clean date/category mix
    random.seed(42)
    random.shuffle(records)

    # Re-assign sequential IDs after shuffle
    for idx, r in enumerate(records, start=3001):
        r['id'] = f"COMP-{idx}"

    out_file = Path("static/data/complaints.csv")
    fieldnames = [
        'id', 'date', 'district', 'state', 'lat', 'lng', 'language',
        'original_text', 'translated_text', 'category', 'urgency',
        'sentiment', 'status', 'source'
    ]

    with open(out_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(records)

    print(f"Successfully generated {len(records)} realistic complaints in {out_file}.")

if __name__ == '__main__':
    generate()
