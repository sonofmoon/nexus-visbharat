import json
import os
from dotenv import load_dotenv

load_dotenv(override=False)


def _as_bool(name: str, default: bool) -> bool:
    val = str(os.environ.get(name, str(default).lower())).strip().lower()
    return val in ('true', '1', 'yes') or val.startswith('true')


def _load_json_dict(name: str, default: dict) -> dict:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return parsed if isinstance(parsed, dict) else default


def _load_json_list(name: str, default: list) -> list:
    raw = (os.environ.get(name) or '').strip()
    if not raw:
        return default
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return default
    return parsed if isinstance(parsed, list) else default


class Config:
    PILOT_ID = os.environ.get('PILOT_ID','')
    PILOT_ONLY = _as_bool('PILOT_ONLY',False)
    PILOT_MODEL_CALLS = _as_bool('PILOT_MODEL_CALLS',False)
    PILOT_MODEL_NAME = os.environ.get('PILOT_MODEL_NAME','gemini-3.6-flash')
    PILOT_ANALYTICS_SYNC = _as_bool('PILOT_ANALYTICS_SYNC',False)
    PILOT_BIGQUERY_TABLE = os.environ.get('PILOT_BIGQUERY_TABLE','')
    PILOT_WORKER_AUDIENCE = os.environ.get('PILOT_WORKER_AUDIENCE','')
    PILOT_WORKER_SERVICE_ACCOUNT = os.environ.get('PILOT_WORKER_SERVICE_ACCOUNT','')
    PILOT_TASK_QUEUE = os.environ.get('PILOT_TASK_QUEUE','')
    PILOT_WORKER_URL = os.environ.get('PILOT_WORKER_URL','')
    PILOT_MODEL_REGIONS = _load_json_list('PILOT_MODEL_REGIONS_JSON',['asia-south1'])
    PILOT_SPEECH_LOCATION = os.environ.get('PILOT_SPEECH_LOCATION','')
    PILOT_SPEECH_MODEL = os.environ.get('PILOT_SPEECH_MODEL','chirp_2')
    PILOT_AUDIO_BUCKET = os.environ.get('PILOT_AUDIO_BUCKET','')
    PILOT_AUDIO_RECOVERY_BUCKET = os.environ.get('PILOT_AUDIO_RECOVERY_BUCKET','')
    PILOT_BQ_MAX_BYTES = int(os.environ.get('PILOT_BQ_MAX_BYTES','1000000000'))
    JURY_REQUIRE_LIVE_MODELS = _as_bool('JURY_REQUIRE_LIVE_MODELS', False)
    LOCAL_EVALUATION_WORKER = _as_bool('LOCAL_EVALUATION_WORKER',_as_bool('DEMO_MODE',True))
    OIDC_ISSUER = os.environ.get('OIDC_ISSUER','')
    OIDC_CLIENT_ID = os.environ.get('OIDC_CLIENT_ID','')
    OIDC_CLIENT_SECRET = os.environ.get('OIDC_CLIENT_SECRET','')
    OIDC_AUTHORIZATION_ENDPOINT = os.environ.get('OIDC_AUTHORIZATION_ENDPOINT','')
    OIDC_TOKEN_ENDPOINT = os.environ.get('OIDC_TOKEN_ENDPOINT','')
    OIDC_JWKS_URI = os.environ.get('OIDC_JWKS_URI','')
    OIDC_REDIRECT_URI = os.environ.get('OIDC_REDIRECT_URI','')
    SEED_DEMO_DATA = _as_bool('SEED_DEMO_DATA',True)
    AUTO_MIGRATE = _as_bool('AUTO_MIGRATE',_as_bool('DEMO_MODE',True))
    LOCAL_EVALUATION_WORKER = _as_bool('LOCAL_EVALUATION_WORKER',_as_bool('DEMO_MODE',True))
    AUDITOR_USER_STATES = _load_json_dict('AUDITOR_USER_STATES', {})
    SECRET_KEY = os.environ.get('SECRET_KEY', 'citizenvoice-dev-key-2026')
    ASR_AUDIT_EXPORT_SIGNING_SECRET = os.environ.get('ASR_AUDIT_EXPORT_SIGNING_SECRET', SECRET_KEY)
    BASE_DIR = os.path.dirname(os.path.abspath(os.path.join(__file__, '..')))
    UPLOAD_FOLDER = os.path.join(BASE_DIR, 'uploads')
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024
    VOICE_MAX_BYTES = int(os.environ.get('VOICE_MAX_BYTES', str(10 * 1024 * 1024)))
    DATA_GOV_IN_API_KEY = os.environ.get('DATA_GOV_IN_API_KEY', '')
    DATA_GOV_IN_TN_ULB_RESOURCE = os.environ.get('DATA_GOV_IN_TN_ULB_RESOURCE', '54c6c324-7b32-4814-a8e5-aab935764a68')
    DATA_GOV_IN_LGD_RESOURCE = os.environ.get('DATA_GOV_IN_LGD_RESOURCE', '')
    DATA_GOV_IN_VELLORE_SBM_RESOURCE = os.environ.get('DATA_GOV_IN_VELLORE_SBM_RESOURCE', 'b0d9af81-e730-4a88-ab45-326ce43ed06e')
    DATA_GOV_IN_JJM_FUNDS_RESOURCE = os.environ.get('DATA_GOV_IN_JJM_FUNDS_RESOURCE', 'cf0d2f0a-f5fe-444e-b5eb-6e17ee0eb85a')
    DATA_GOV_IN_TIRUPATI_AMRUT_RESOURCE = os.environ.get('DATA_GOV_IN_TIRUPATI_AMRUT_RESOURCE', '1ee4ff05-c487-47d4-bfdd-613331a361f9')
    DATA_GOV_IN_TIRUPATI_JJM_RESOURCE = os.environ.get('DATA_GOV_IN_TIRUPATI_JJM_RESOURCE', '51f7949b-3d72-434e-a4de-32229e0c6d13')
    NDAP_API_URL = os.environ.get('NDAP_API_URL', '')
    NDAP_API_KEY = os.environ.get('NDAP_API_KEY', '')

    PUBLIC_DATA_SNAPSHOT_DIR = os.environ.get('PUBLIC_DATA_SNAPSHOT_DIR', os.path.join(BASE_DIR, 'instance', 'public-data'))
    PUBLIC_DATA_MAX_AGE_SECONDS = int(os.environ.get('PUBLIC_DATA_MAX_AGE_SECONDS', '86400'))
    PUBLIC_DATA_MAX_PAGES = int(os.environ.get('PUBLIC_DATA_MAX_PAGES', '10'))
    PUBLIC_DATA_FIELD_MAPS = _load_json_dict('PUBLIC_DATA_FIELD_MAPS_JSON', {})
    PUBLIC_DATA_OBSERVATION_PERIODS = _load_json_dict('PUBLIC_DATA_OBSERVATION_PERIODS_JSON', {})
    NDAP_ALLOWED_HOSTS = _load_json_list('NDAP_ALLOWED_HOSTS_JSON', [])

    DEPLOYMENT_PROFILE = (os.environ.get('DEPLOYMENT_PROFILE', 'pilot') or 'pilot').strip().lower()
    if DEPLOYMENT_PROFILE not in {'pilot', 'national'}:
        DEPLOYMENT_PROFILE = 'pilot'

    DEMO_MODE = _as_bool('DEMO_MODE', True)
    ALLOW_EPHEMERAL_SHOWCASE = _as_bool('ALLOW_EPHEMERAL_SHOWCASE', False)
    PROVIDER_VERIFICATION_TTL_SECONDS = int(os.environ.get('PROVIDER_VERIFICATION_TTL_SECONDS', '900'))

    GOOGLE_AI_API_KEY = os.environ.get('GOOGLE_AI_API_KEY', '')
    USE_REAL_GOOGLE_AI = _as_bool('USE_REAL_GOOGLE_AI', True)
    JURY_REQUIRE_LIVE_MODELS = _as_bool('JURY_REQUIRE_LIVE_MODELS', True)
    AGENTIC_API_KEY = os.environ.get('AGENTIC_API_KEY', '')

    USE_REAL_GOOGLE_STT = _as_bool('USE_REAL_GOOGLE_STT', True)
    USE_REAL_GOOGLE_TTS = _as_bool('USE_REAL_GOOGLE_TTS', True)
    GOOGLE_TTS_LANGUAGE_CODE = os.environ.get('GOOGLE_TTS_LANGUAGE_CODE', 'en-IN')
    LANGUAGE_ASR_PROVIDER = (os.environ.get('LANGUAGE_ASR_PROVIDER', 'google') or 'google').strip().lower()
    CLOUD_RUN_SERVICE_URL = os.environ.get('CLOUD_RUN_SERVICE_URL', 'https://nexus-visbharath-510474645723.asia-south1.run.app')
    USE_CLOUD_RUN_SERVICE = _as_bool('USE_CLOUD_RUN_SERVICE', True)
    LANGUAGE_ASR_STRICT_MODE = _as_bool('LANGUAGE_ASR_STRICT_MODE', False)
    LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED = _as_bool('LANGUAGE_ASR_CIRCUIT_BREAKER_ENABLED', True)
    LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD = int(os.environ.get('LANGUAGE_ASR_CIRCUIT_FAIL_THRESHOLD', '3'))
    LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS = int(os.environ.get('LANGUAGE_ASR_CIRCUIT_OPEN_SECONDS', '60'))
    USE_REAL_BIGQUERY = _as_bool('USE_REAL_BIGQUERY', True)
    BIGQUERY_PROJECT_ID = os.environ.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat')
    BIGQUERY_DATASET = os.environ.get('BIGQUERY_DATASET', 'visbharat_analytics')
    BIGQUERY_TABLE = os.environ.get('BIGQUERY_TABLE', 'citizen_requests_fused')
    BIGQUERY_LOCATION = os.environ.get('BIGQUERY_LOCATION', 'asia-south1')
    USE_REAL_PUBSUB = _as_bool('USE_REAL_PUBSUB', True)
    PUBSUB_TOPIC_ID = os.environ.get('PUBSUB_TOPIC_ID', 'citizen-ingestion-topic')
    USE_REAL_VERTEX_PREDICTION = _as_bool('USE_REAL_VERTEX_PREDICTION', True)
    VERTEX_PROJECT_ID = os.environ.get('VERTEX_PROJECT_ID', 'nexus-visbharat')
    VERTEX_LOCATION = os.environ.get('VERTEX_LOCATION', 'asia-south1')
    VERTEX_ENDPOINT_ID = os.environ.get('VERTEX_ENDPOINT_ID', 'nvb-sps-model-v24')
    VERTEX_API_ENDPOINT = os.environ.get('VERTEX_API_ENDPOINT', '')
    USE_REAL_DIALOGFLOW_CX = _as_bool('USE_REAL_DIALOGFLOW_CX', True)
    DIALOGFLOW_PROJECT_ID = os.environ.get('DIALOGFLOW_PROJECT_ID', 'nexus-visbharat')
    DIALOGFLOW_LOCATION = os.environ.get('DIALOGFLOW_LOCATION', 'asia-south1')
    DIALOGFLOW_AGENT_ID = os.environ.get('DIALOGFLOW_AGENT_ID', 'da15c334-15cd-4d44-ac12-8e59a896b675')
    DIALOGFLOW_LANGUAGE_CODE = os.environ.get('DIALOGFLOW_LANGUAGE_CODE', 'en')
    DIALOGFLOW_API_ENDPOINT = os.environ.get('DIALOGFLOW_API_ENDPOINT', '')

    USE_REAL_GOOGLE_TRANSLATION = _as_bool('USE_REAL_GOOGLE_TRANSLATION', False)
    GOOGLE_TRANSLATION_PROJECT_ID = os.environ.get('GOOGLE_TRANSLATION_PROJECT_ID', '')
    GOOGLE_TRANSLATION_LOCATION = os.environ.get('GOOGLE_TRANSLATION_LOCATION', 'global')

    GOOGLE_APPLICATION_CREDENTIALS = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')
    USE_GCP_SECRET_MANAGER = _as_bool('USE_GCP_SECRET_MANAGER', False)
    GCP_SECRET_MANAGER_PROJECT_ID = os.environ.get('GCP_SECRET_MANAGER_PROJECT_ID', '')
    GCP_SECRET_MANAGER_VERSION = os.environ.get('GCP_SECRET_MANAGER_VERSION', 'latest')
    GCP_SECRET_MANAGER_CACHE_TTL_SECONDS = int(os.environ.get('GCP_SECRET_MANAGER_CACHE_TTL_SECONDS', '300'))
    GCP_SECRET_MANAGER_MAX_RETRIES = int(os.environ.get('GCP_SECRET_MANAGER_MAX_RETRIES', '2'))
    GCP_SECRET_MANAGER_RETRY_BACKOFF_SECONDS = float(os.environ.get('GCP_SECRET_MANAGER_RETRY_BACKOFF_SECONDS', '0.25'))
    GCP_SECRET_MANAGER_RETRY_BACKOFF_MULTIPLIER = float(os.environ.get('GCP_SECRET_MANAGER_RETRY_BACKOFF_MULTIPLIER', '2.0'))
    GCP_SECRET_MANAGER_RETRY_JITTER_SECONDS = float(os.environ.get('GCP_SECRET_MANAGER_RETRY_JITTER_SECONDS', '0.1'))

    GOOGLE_MAPS_API_KEY = os.environ.get('GOOGLE_MAPS_API_KEY', '')
    USE_REAL_GOOGLE_MAPS = _as_bool('USE_REAL_GOOGLE_MAPS', False)
    GEO_STRICT_DISTRICT_MATCH = _as_bool('GEO_STRICT_DISTRICT_MATCH', True)
    GEO_ENFORCE_MIN_CONFIDENCE = _as_bool('GEO_ENFORCE_MIN_CONFIDENCE', False)
    GEO_MIN_CONFIDENCE = float(os.environ.get('GEO_MIN_CONFIDENCE', '0.45'))
    ALLOWED_AUDIO_MIME_TYPES = [
        'audio/wav',
        'audio/x-wav',
        'audio/webm',
        'audio/ogg',
        'audio/opus',
        'audio/mpeg',
        'audio/mp3',
        'audio/flac',
    ]

    DATABASE_PATH = os.environ.get(
        'DATABASE_PATH',
        os.path.join(os.path.dirname(os.path.abspath(os.path.join(__file__, '..'))), 'visbharat.db')
    )
    DATABASE_URL = os.environ.get('DATABASE_URL', '')

    ROLE_CHOICES = ['admin', 'analyst', 'auditor']

    ADMIN_API_TOKEN = os.environ.get('ADMIN_API_TOKEN', 'visbharat-admin-token' if DEMO_MODE else '')
    ANALYST_API_TOKEN = os.environ.get('ANALYST_API_TOKEN', 'visbharat-analyst-token' if DEMO_MODE else '')
    AUDITOR_API_TOKEN = os.environ.get('AUDITOR_API_TOKEN', 'visbharat-auditor-token' if DEMO_MODE else '')
    WEBHOOK_SHARED_TOKEN = os.environ.get('WEBHOOK_SHARED_TOKEN', 'visbharat-webhook-token' if DEMO_MODE else '')
    WHATSAPP_VERIFY_TOKEN = os.environ.get('WHATSAPP_VERIFY_TOKEN', 'visbharat-whatsapp-verify' if DEMO_MODE else '')
    META_APP_SECRET = os.environ.get('META_APP_SECRET', '')
    TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    TELEGRAM_WEBHOOK_SECRET = os.environ.get('TELEGRAM_WEBHOOK_SECRET', '')
    TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN', '')
    EXOTEL_WEBHOOK_SECRET = os.environ.get('EXOTEL_WEBHOOK_SECRET', '')
    WEBHOOK_REQUIRE_REPLAY_PROTECTION = _as_bool('WEBHOOK_REQUIRE_REPLAY_PROTECTION', True)
    WEBHOOK_REPLAY_WINDOW_SECONDS = int(os.environ.get('WEBHOOK_REPLAY_WINDOW_SECONDS', '300'))
    WEBHOOK_NONCE_CACHE_MAX = int(os.environ.get('WEBHOOK_NONCE_CACHE_MAX', '10000'))
    WEBHOOK_BIND_REPLAY_IN_SIGNATURE = _as_bool('WEBHOOK_BIND_REPLAY_IN_SIGNATURE', False)
    WEBHOOK_ALLOWED_IPS = os.environ.get('WEBHOOK_ALLOWED_IPS', '')
    WEBHOOK_REPLAY_STORE = os.environ.get('WEBHOOK_REPLAY_STORE', 'db').lower()
    WEBHOOK_SECURITY_POLICY_VERSION = os.environ.get('WEBHOOK_SECURITY_POLICY_VERSION', '2026-09-v1')
    # Emergency acknowledgement is deliberately narrower than general analyst access.
    # Tokens are short-lived bearer capabilities for the receiving response desk;
    # privileged acknowledgement is limited to operational/audit roles.
    EMERGENCY_ACK_TOKEN_TTL_SECONDS = int(os.environ.get('EMERGENCY_ACK_TOKEN_TTL_SECONDS', '900'))
    EMERGENCY_ACK_ROLES = _load_json_list('EMERGENCY_ACK_ROLES_JSON', ['admin', 'auditor'])

    PILOT_LANGUAGES = {
        'ta': 'Tamil',
        'te': 'Telugu',
        'en': 'English',
    }
    NATIONAL_LANGUAGES = {
        'en': 'English',
        'ta': 'Tamil',
        'te': 'Telugu',
        'hi': 'Hindi',
        'bn': 'Bengali',
        'mr': 'Marathi',
        'kn': 'Kannada',
        'ml': 'Malayalam',
        'gu': 'Gujarati',
        'pa': 'Punjabi',
        'or': 'Odia',
    }

    LANGUAGES = PILOT_LANGUAGES if DEPLOYMENT_PROFILE == 'pilot' else NATIONAL_LANGUAGES

    PILOT_STATE_TO_DISTRICTS = {
        'Tamil Nadu': [
            'Ariyalur', 'Chengalpattu', 'Chennai', 'Coimbatore', 'Cuddalore', 'Dharmapuri',
            'Dindigul', 'Erode', 'Kallakurichi', 'Kanchipuram', 'Kanyakumari', 'Karur',
            'Krishnagiri', 'Madurai', 'Mayiladuthurai', 'Nagapattinam', 'Namakkal', 'Nilgiris',
            'Perambalur', 'Pudukkottai', 'Ramanathapuram', 'Ranipet', 'Salem', 'Sivaganga',
            'Tenkasi', 'Thanjavur', 'Theni', 'Thoothukudi', 'Tiruchirappalli', 'Tirunelveli',
            'Tirupathur', 'Tiruppur', 'Tiruvallur', 'Tiruvannamalai', 'Tiruvarur', 'Vellore',
            'Viluppuram', 'Virudhunagar'
        ],
        'Andhra Pradesh': [
            'Alluri Sitharama Raju', 'Anakapalli', 'Ananthapuramu', 'Annamayya', 'Bapatla',
            'Chittoor', 'Dr. B.R. Ambedkar Konaseema', 'East Godavari', 'Eluru', 'Guntur',
            'Kakinada', 'Krishna', 'Kurnool', 'Nandyal', 'NTR', 'Palnadu',
            'Parvathipuram Manyam', 'Prakasam', 'Sri Potti Sriramulu Nellore', 'Sri Sathya Sai',
            'Srikakulam', 'Tirupati', 'Visakhapatnam', 'Vizianagaram', 'West Godavari', 'YSR Kadapa'
        ],
        'Telangana': [
            'Adilabad', 'Bhadradri Kothagudem', 'Hanamkonda', 'Hyderabad', 'Jagtial', 'Jangaon',
            'Jayashankar Bhupalpally', 'Jogulamba Gadwal', 'Kamareddy', 'Karimnagar', 'Khammam',
            'Kumuram Bheem Asifabad', 'Mahabubabad', 'Mahabubnagar', 'Mancherial', 'Medak',
            'Medchal-Malkajgiri', 'Mulugu', 'Nagarkurnool', 'Nalgonda', 'Narayanpet', 'Nirmal',
            'Nizamabad', 'Peddapalli', 'Rajanna Sircilla', 'Ranga Reddy', 'Sangareddy',
            'Siddipet', 'Suryapet', 'Vikarabad', 'Wanaparthy', 'Warangal', 'Yadadri Bhuvanagiri'
        ],
    }

    CORRIDOR_STATE_TO_DISTRICTS = PILOT_STATE_TO_DISTRICTS
    NATIONAL_STATE_TO_DISTRICTS = _load_json_dict('NATIONAL_STATE_TO_DISTRICTS_JSON', PILOT_STATE_TO_DISTRICTS)

    ALLOWED_STATE_TO_DISTRICTS = (
        NATIONAL_STATE_TO_DISTRICTS
        if DEPLOYMENT_PROFILE == 'national'
        else PILOT_STATE_TO_DISTRICTS
    )

    @classmethod
    def validate_production_security(cls, target=None):
        """Fail closed if production environment lacks cryptographic secrets or uses dev defaults."""
        obj = target or cls
        demo_mode = getattr(obj, 'DEMO_MODE', True)
        profile = getattr(obj, 'DEPLOYMENT_PROFILE', 'corridor')
        secret_key = getattr(obj, 'SECRET_KEY', '')
        admin_token = getattr(obj, 'ADMIN_API_TOKEN', '')
        analyst_token = getattr(obj, 'ANALYST_API_TOKEN', '')
        auditor_token = getattr(obj, 'AUDITOR_API_TOKEN', '')

        if not demo_mode or profile in ('pilot', 'national'):
            insecure_keys = {'citizenvoice-dev-key-2026', 'nexus-dev-secret-key-2026', 'default-insecure-key', 'change-this-in-production'}
            if not secret_key or secret_key in insecure_keys:
                if not demo_mode:
                    raise RuntimeError("P0 Security Violation: SECRET_KEY must be set securely in production from Secret Manager or environment.")
            insecure_tokens = {'visbharat-admin-token', 'visbharat-analyst-token', 'visbharat-auditor-token'}
            if not demo_mode:
                if admin_token in insecure_tokens or not admin_token:
                    raise RuntimeError("P0 Security Violation: ADMIN_API_TOKEN must be securely provisioned for non-demo deployments.")
                if analyst_token in insecure_tokens or not analyst_token:
                    raise RuntimeError("P0 Security Violation: ANALYST_API_TOKEN must be securely provisioned for non-demo deployments.")
                if auditor_token in insecure_tokens or not auditor_token:
                    raise RuntimeError("P0 Security Violation: AUDITOR_API_TOKEN must be securely provisioned for non-demo deployments.")

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

    CATEGORY_TO_SERVICE = _load_json_dict(
        'CATEGORY_TO_SERVICE_JSON',
        {
            'Road': 'roads',
            'Water Supply': 'water',
            'Electricity': 'electricity',
            'Health': 'health',
            'Education': 'education',
            'Sanitation': 'sanitation',
            'Digital Connectivity': 'digital',
            'Transport': 'transport',
            'Housing': 'housing',
            'Other': 'general',
        },
    )

    SERVICE_TO_DEPARTMENT = _load_json_dict(
        'SERVICE_TO_DEPARTMENT_JSON',
        {
            'roads': 'Public Works Department',
            'water': 'Water Board',
            'electricity': 'Electricity Board',
            'health': 'Health Department',
            'education': 'Education Department',
            'sanitation': 'Sanitation Department',
            'digital': 'IT Department',
            'transport': 'Transport Department',
            'housing': 'Housing Board',
            'general': 'District Grievance Cell',
        },
    )

    WARD_ROUTING_MATRIX = _load_json_dict(
        'WARD_ROUTING_MATRIX_JSON',
        {
            'Tamil Nadu': {
                'Chennai': {
                    'service_to_department': {
                        'roads': 'Greater Chennai Corporation - Roads',
                        'water': 'Chennai Metro Water',
                        'sanitation': 'Greater Chennai Corporation - Sanitation',
                    },
                    'ward_to_service_department': {
                        '6': {
                            'electricity': 'TANGEDCO Chennai North Division',
                        }
                    },
                }
            }
        },
    )

    URGENCY_LEVELS = ['Routine', 'Urgent', 'Emergency']
    SLA_URGENCY_HOURS = _load_json_dict(
        'SLA_URGENCY_HOURS_JSON',
        {'Routine': 72, 'Urgent': 24, 'Emergency': 2},
    )
    SLA_TIER2_DELAY_HOURS = int(os.environ.get('SLA_TIER2_DELAY_HOURS', '24'))
    SLA_RULES = _load_json_dict(
        'SLA_RULES_JSON',
        {
            'by_category': {
                'Road': {'Routine': 48, 'Urgent': 18, 'Emergency': 2},
                'Water Supply': {'Routine': 36, 'Urgent': 12, 'Emergency': 2},
                'Electricity': {'Routine': 24, 'Urgent': 8, 'Emergency': 2},
                'Sanitation': {'Routine': 36, 'Urgent': 12, 'Emergency': 2},
                'Health': {'Routine': 24, 'Urgent': 6, 'Emergency': 2},
            },
            'by_channel': {
                'WhatsApp': {'Routine': 60, 'Urgent': 20, 'Emergency': 2},
                'Voice IVR': {'Routine': 54, 'Urgent': 16, 'Emergency': 2},
            },
            'by_category_channel': {
                'Road|WhatsApp': {'Routine': 42, 'Urgent': 14, 'Emergency': 2},
            },
            'tier2_delay_hours': 24,
        },
    )
    SLA_ESCALATION_TARGETS = _load_json_dict(
        'SLA_ESCALATION_TARGETS_JSON',
        {
            'by_department': {
                'Greater Chennai Corporation - Roads': {
                    'department': 'City Engineering Escalation Cell',
                    'assignee': 'Executive Engineer (Roads)',
                },
                'Water Board': {
                    'department': 'Regional Water Escalation Cell',
                    'assignee': 'Assistant Engineer (Water)',
                },
                'Electricity Board': {
                    'department': 'Disaster Quick Response Team (Electrical)',
                    'assignee': 'Superintending Engineer (Grid Safety)',
                },
                'Sanitation Department': {
                    'department': 'Hazardous Spill & Gas Emergency Cell',
                    'assignee': 'Municipal Health & Safety Officer',
                },
                'District Grievance Cell': {
                    'department': 'District Disaster Management Authority (DDMA)',
                    'assignee': 'Collectorate Rapid Response Desk',
                },
            },
            'by_channel': {
                'Voice IVR': {
                    'department': '24x7 Command Desk',
                    'assignee': 'Shift Supervisor',
                },
            },
            'by_department_tier2': {
                'City Engineering Escalation Cell': {
                    'department': 'City Commissioner Desk',
                    'assignee': 'Chief Engineer',
                },
                'Regional Water Escalation Cell': {
                    'department': 'Chief Water Office',
                    'assignee': 'Superintending Engineer',
                },
            },
            'by_channel_tier2': {
                'Voice IVR': {
                    'department': 'State War Room',
                    'assignee': 'Escalation Duty Manager',
                },
            },
        },
    )

    CLUSTER_SIMILARITY_THRESHOLD = float(os.environ.get('CLUSTER_SIMILARITY_THRESHOLD', '0.6'))
    CLUSTER_MIN_SHARED_TOKENS = int(os.environ.get('CLUSTER_MIN_SHARED_TOKENS', '2'))
    CLUSTER_KEY_MAX_TOKENS = int(os.environ.get('CLUSTER_KEY_MAX_TOKENS', '6'))
    CLUSTER_TOKEN_EXAMPLES_LIMIT = int(os.environ.get('CLUSTER_TOKEN_EXAMPLES_LIMIT', '12'))
    CLUSTER_REVIEW_SIMILARITY_THRESHOLD = float(os.environ.get('CLUSTER_REVIEW_SIMILARITY_THRESHOLD', '0.72'))
    ASYNC_PIPELINE_ENABLED = _as_bool('ASYNC_PIPELINE_ENABLED', False)
    PIPELINE_MAX_RETRIES = int(os.environ.get('PIPELINE_MAX_RETRIES', '5'))
    PIPELINE_RETRY_BACKOFF_SECONDS = int(os.environ.get('PIPELINE_RETRY_BACKOFF_SECONDS', '30'))
    PIPELINE_POLL_INTERVAL_SECONDS = int(os.environ.get('PIPELINE_POLL_INTERVAL_SECONDS', '2'))


    NOTIFICATION_TEMPLATE_VERSION = os.environ.get('NOTIFICATION_TEMPLATE_VERSION', 'v1')
    NOTIFICATION_TEMPLATES = _load_json_dict(
        'NOTIFICATION_TEMPLATES_JSON',
        {
            'en': {
                'tier1': 'Request {request_id} breached SLA and is escalated to tier-1.',
                'tier2': 'Request {request_id} remains unresolved and is escalated to tier-2.',
            },
            'ta': {
                'tier1': '?????? ???????? {request_id} SLA ????? ??????? ????? ???? ???????????????????.',
                'tier2': '?????? ???????? {request_id} ???????? ???? ???????????????????.',
            },
        },
    )
    NOTIFICATION_RECEIPT_TOKEN = os.environ.get('NOTIFICATION_RECEIPT_TOKEN', 'visbharat-notification-receipt')
    NOTIFICATION_RECEIPT_HMAC_SECRET = os.environ.get('NOTIFICATION_RECEIPT_HMAC_SECRET', '')
    NOTIFICATION_RECEIPT_EMAIL_SECRET = os.environ.get('NOTIFICATION_RECEIPT_EMAIL_SECRET', '')
    NOTIFICATION_RECEIPT_SMS_SECRET = os.environ.get('NOTIFICATION_RECEIPT_SMS_SECRET', '')
    NOTIFICATION_RECEIPT_WHATSAPP_SECRET = os.environ.get('NOTIFICATION_RECEIPT_WHATSAPP_SECRET', '')
    NOTIFICATION_RECEIPT_BIND_REPLAY_IN_SIGNATURE = _as_bool('NOTIFICATION_RECEIPT_BIND_REPLAY_IN_SIGNATURE', True)
    NOTIFICATION_RETRY_BACKOFF_SECONDS = int(os.environ.get('NOTIFICATION_RETRY_BACKOFF_SECONDS', '1'))
    NOTIFICATION_RETRY_BACKOFF_MULTIPLIER = float(os.environ.get('NOTIFICATION_RETRY_BACKOFF_MULTIPLIER', '2.0'))
    NOTIFICATION_RETRY_MAX_BACKOFF_SECONDS = int(os.environ.get('NOTIFICATION_RETRY_MAX_BACKOFF_SECONDS', '30'))
    NOTIFICATION_RETRY_JITTER_SECONDS = float(os.environ.get('NOTIFICATION_RETRY_JITTER_SECONDS', '0'))
    NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS = int(os.environ.get('NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS', '8'))
    NOTIFICATION_CIRCUIT_BREAKER_ENABLED = _as_bool('NOTIFICATION_CIRCUIT_BREAKER_ENABLED', True)
    NOTIFICATION_CIRCUIT_FAIL_THRESHOLD = int(os.environ.get('NOTIFICATION_CIRCUIT_FAIL_THRESHOLD', '3'))
    NOTIFICATION_CIRCUIT_OPEN_SECONDS = int(os.environ.get('NOTIFICATION_CIRCUIT_OPEN_SECONDS', '60'))
    NOTIFICATION_SLO_MIN_DELIVERIES = int(os.environ.get('NOTIFICATION_SLO_MIN_DELIVERIES', '20'))
    NOTIFICATION_SLO_MAX_FAILURE_RATE = float(os.environ.get('NOTIFICATION_SLO_MAX_FAILURE_RATE', '0.1'))
    NOTIFICATION_SLO_MAX_OPEN_CIRCUITS = int(os.environ.get('NOTIFICATION_SLO_MAX_OPEN_CIRCUITS', '0'))
    OUTBOUND_CONNECTORS_ENABLED = _as_bool('OUTBOUND_CONNECTORS_ENABLED', False)
    OUTBOUND_CONNECTOR_SIGNING_ENABLED = _as_bool('OUTBOUND_CONNECTOR_SIGNING_ENABLED', True)
    OUTBOUND_CONNECTOR_SIGN_BIND_NONCE = _as_bool('OUTBOUND_CONNECTOR_SIGN_BIND_NONCE', True)
    NOTIFICATION_SECRET_PROVIDER_ENABLED = _as_bool('NOTIFICATION_SECRET_PROVIDER_ENABLED', False)
    NOTIFICATION_SECRET_PROVIDER_PREFIX = os.environ.get('NOTIFICATION_SECRET_PROVIDER_PREFIX', 'visbharat/connectors').strip()
    NOTIFICATION_SLO_ALERT_TARGET = _load_json_dict('NOTIFICATION_SLO_ALERT_TARGET_JSON', {'email': '', 'language': 'en'})
    NOTIFICATION_SLO_ALERT_TARGETS = _load_json_dict('NOTIFICATION_SLO_ALERT_TARGETS_JSON', {'targets': []})
    NOTIFICATION_SLO_ALERT_MAX_TARGETS_PER_ALERT = int(os.environ.get('NOTIFICATION_SLO_ALERT_MAX_TARGETS_PER_ALERT', '3'))
    NOTIFICATION_SLO_ALERT_COOLDOWN_SECONDS = int(os.environ.get('NOTIFICATION_SLO_ALERT_COOLDOWN_SECONDS', '900'))
    NOTIFICATION_SLO_ALERT_DEDUPE_SECONDS = int(os.environ.get('NOTIFICATION_SLO_ALERT_DEDUPE_SECONDS', '1800'))
    NOTIFICATION_SLO_ALERT_MAX_PER_RUN = int(os.environ.get('NOTIFICATION_SLO_ALERT_MAX_PER_RUN', '5'))
    OPS_ALERT_ROUTING_ENABLED = _as_bool('OPS_ALERT_ROUTING_ENABLED', True)
    OPS_ALERT_ROUTE_EMAIL_ENABLED = _as_bool('OPS_ALERT_ROUTE_EMAIL_ENABLED', True)
    OPS_ALERT_ROUTE_PAGER_ENABLED = _as_bool('OPS_ALERT_ROUTE_PAGER_ENABLED', True)
    OPS_ALERT_ROUTE_DEDUPE_SECONDS = int(os.environ.get('OPS_ALERT_ROUTE_DEDUPE_SECONDS', '900'))
    OPS_ALERT_ESCALATION_REPEAT_THRESHOLD = int(os.environ.get('OPS_ALERT_ESCALATION_REPEAT_THRESHOLD', '3'))
    OPS_ALERT_ESCALATION_COOLDOWN_SECONDS = int(os.environ.get('OPS_ALERT_ESCALATION_COOLDOWN_SECONDS', '1800'))
    OPS_ALERT_PAGER_WEBHOOK_URL = os.environ.get('OPS_ALERT_PAGER_WEBHOOK_URL', '').strip()
    OPS_ALERT_PAGER_WEBHOOK_TOKEN = os.environ.get('OPS_ALERT_PAGER_WEBHOOK_TOKEN', '').strip()
    OPS_ALERT_PAGER_SIGNING_SECRET = os.environ.get('OPS_ALERT_PAGER_SIGNING_SECRET', '').strip()
    OPS_ALERT_PAGER_PROVIDER = os.environ.get('OPS_ALERT_PAGER_PROVIDER', 'google_pubsub').strip().lower()
    OPS_ALERT_GCP_SOURCE = os.environ.get('OPS_ALERT_GCP_SOURCE', 'nexus-visbharath').strip()
    OPS_ALERT_PUBSUB_TOPIC_ID = os.environ.get('OPS_ALERT_PUBSUB_TOPIC_ID', 'ops-alert-topic').strip()
    OPS_WEBHOOK_VERIFY_SECRET = os.environ.get('OPS_WEBHOOK_VERIFY_SECRET', '').strip()
    OPS_WEBHOOK_VERIFY_WINDOW_SECONDS = int(os.environ.get('OPS_WEBHOOK_VERIFY_WINDOW_SECONDS', str(WEBHOOK_REPLAY_WINDOW_SECONDS)))
    OPS_DAILY_REPORT_SIGNING_ENABLED = _as_bool('OPS_DAILY_REPORT_SIGNING_ENABLED', True)
    OPS_DAILY_REPORT_SIGNING_SECRET = os.environ.get('OPS_DAILY_REPORT_SIGNING_SECRET', SECRET_KEY)
    OPS_DAILY_REPORT_SIGNING_KEY_ID = os.environ.get('OPS_DAILY_REPORT_SIGNING_KEY_ID', 'ops-hs256-v1').strip()
    NOTIFICATION_CHANNEL_PREFERENCE = [
        item.strip().lower()
        for item in (os.environ.get('NOTIFICATION_CHANNEL_PREFERENCE', 'email,sms,whatsapp') or '').split(',')
        if item.strip()
    ]
    NOTIFICATION_MAX_RETRIES = int(os.environ.get('NOTIFICATION_MAX_RETRIES', '2'))

    EMAIL_CONNECTOR_PROVIDER = os.environ.get('EMAIL_CONNECTOR_PROVIDER', 'http').strip().lower()
    EMAIL_CONNECTOR_ENDPOINT = os.environ.get('EMAIL_CONNECTOR_ENDPOINT', '')
    EMAIL_CONNECTOR_API_KEY = os.environ.get('EMAIL_CONNECTOR_API_KEY', '')
    EMAIL_CONNECTOR_API_KEY_NEXT = os.environ.get('EMAIL_CONNECTOR_API_KEY_NEXT', '')
    EMAIL_CONNECTOR_SIGNING_SECRET = os.environ.get('EMAIL_CONNECTOR_SIGNING_SECRET', '')
    EMAIL_CONNECTOR_ACTIVE_KEY_SLOT = os.environ.get('EMAIL_CONNECTOR_ACTIVE_KEY_SLOT', 'primary')
    EMAIL_CONNECTOR_TIMEOUT_SECONDS = int(os.environ.get('EMAIL_CONNECTOR_TIMEOUT_SECONDS', str(NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS)))
    EMAIL_SMTP_HOST = os.environ.get('EMAIL_SMTP_HOST', '')
    EMAIL_SMTP_PORT = int(os.environ.get('EMAIL_SMTP_PORT', '587'))
    EMAIL_SMTP_USERNAME = os.environ.get('EMAIL_SMTP_USERNAME', '')
    EMAIL_SMTP_PASSWORD = os.environ.get('EMAIL_SMTP_PASSWORD', '')
    EMAIL_SMTP_FROM = os.environ.get('EMAIL_SMTP_FROM', '')
    SMS_CONNECTOR_PROVIDER = os.environ.get('SMS_CONNECTOR_PROVIDER', 'http').strip().lower()
    SMS_CONNECTOR_ENDPOINT = os.environ.get('SMS_CONNECTOR_ENDPOINT', '')
    SMS_CONNECTOR_API_KEY = os.environ.get('SMS_CONNECTOR_API_KEY', '')
    SMS_CONNECTOR_API_KEY_NEXT = os.environ.get('SMS_CONNECTOR_API_KEY_NEXT', '')
    SMS_CONNECTOR_SIGNING_SECRET = os.environ.get('SMS_CONNECTOR_SIGNING_SECRET', '')
    SMS_CONNECTOR_ACTIVE_KEY_SLOT = os.environ.get('SMS_CONNECTOR_ACTIVE_KEY_SLOT', 'primary')
    SMS_CONNECTOR_TIMEOUT_SECONDS = int(os.environ.get('SMS_CONNECTOR_TIMEOUT_SECONDS', str(NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS)))
    SMS_TWILIO_ACCOUNT_SID = os.environ.get('SMS_TWILIO_ACCOUNT_SID', '')
    SMS_TWILIO_AUTH_TOKEN = os.environ.get('SMS_TWILIO_AUTH_TOKEN', '')
    SMS_TWILIO_FROM_NUMBER = os.environ.get('SMS_TWILIO_FROM_NUMBER', '')
    WHATSAPP_CONNECTOR_PROVIDER = os.environ.get('WHATSAPP_CONNECTOR_PROVIDER', 'http').strip().lower()
    WHATSAPP_CONNECTOR_ENDPOINT = os.environ.get('WHATSAPP_CONNECTOR_ENDPOINT', '')
    WHATSAPP_CONNECTOR_API_KEY = os.environ.get('WHATSAPP_CONNECTOR_API_KEY', '')
    WHATSAPP_CONNECTOR_API_KEY_NEXT = os.environ.get('WHATSAPP_CONNECTOR_API_KEY_NEXT', '')
    WHATSAPP_CONNECTOR_SIGNING_SECRET = os.environ.get('WHATSAPP_CONNECTOR_SIGNING_SECRET', '')
    WHATSAPP_CONNECTOR_ACTIVE_KEY_SLOT = os.environ.get('WHATSAPP_CONNECTOR_ACTIVE_KEY_SLOT', 'primary')
    WHATSAPP_CONNECTOR_TIMEOUT_SECONDS = int(os.environ.get('WHATSAPP_CONNECTOR_TIMEOUT_SECONDS', str(NOTIFICATION_CONNECTOR_TIMEOUT_SECONDS)))
    WHATSAPP_TWILIO_ACCOUNT_SID = os.environ.get('WHATSAPP_TWILIO_ACCOUNT_SID', '')
    WHATSAPP_TWILIO_AUTH_TOKEN = os.environ.get('WHATSAPP_TWILIO_AUTH_TOKEN', '')
    WHATSAPP_TWILIO_FROM_NUMBER = os.environ.get('WHATSAPP_TWILIO_FROM_NUMBER', '')

    MAP_WARD_TAGGING_ENABLED = _as_bool('MAP_WARD_TAGGING_ENABLED', True)
    MAP_WARD_GRID = _load_json_dict(
        'MAP_WARD_GRID_JSON',
        {
            'Chennai': {'lat_origin': 13.05, 'lng_origin': 80.2, 'cell_size': 0.02, 'prefix': 'W'},
            'Hyderabad': {'lat_origin': 17.3, 'lng_origin': 78.4, 'cell_size': 0.03, 'prefix': 'W'},
        },
    )

    PIN_GEOCODE_INDEX = _load_json_dict(
        'PIN_GEOCODE_INDEX_JSON',
        {
            '600001': {'lat': 13.0836, 'lng': 80.2752, 'district': 'Chennai', 'village': 'Fort St George'},
            '500001': {'lat': 17.3850, 'lng': 78.4867, 'district': 'Hyderabad', 'village': 'Nampally'},
            '560001': {'lat': 12.9762, 'lng': 77.6033, 'district': 'Bengaluru Urban', 'village': 'Bangalore Central'},
            '632001': {'lat': 12.9165, 'lng': 79.1325, 'district': 'Vellore', 'village': 'Vellore Fort', 'lgd_code': '252654', 'local_body': 'Vellore Municipal Corporation'},
            '517501': {'lat': 13.6288, 'lng': 79.4192, 'district': 'Tirupati', 'village': 'Tirupati Central', 'lgd_code': '252781', 'local_body': 'Tirupati Municipal Corporation'},
        },
    )

    WARD_POLYGONS = _load_json_dict(
        'WARD_POLYGONS_JSON',
        {
            'Chennai': [
                {
                    'ward': 'W0101',
                    'points': [
                        {'lat': 13.06, 'lng': 80.25},
                        {'lat': 13.10, 'lng': 80.25},
                        {'lat': 13.10, 'lng': 80.30},
                        {'lat': 13.06, 'lng': 80.30},
                    ],
                }
            ]
        },
    )

    SVAMITVA_VILLAGE_MAPS = _load_json_dict(
        'SVAMITVA_VILLAGE_MAPS_JSON',
        {
            'Chennai': [
                {
                    'village': 'Sample Village Block A',
                    'points': [
                        {'lat': 13.07, 'lng': 80.26},
                        {'lat': 13.09, 'lng': 80.26},
                        {'lat': 13.09, 'lng': 80.29},
                        {'lat': 13.07, 'lng': 80.29},
                    ],
                }
            ]
        },
    )

    PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE = int(os.environ.get('PUBLIC_TRANSPARENCY_MIN_GROUP_SIZE', '3'))
    PUBLIC_TRANSPARENCY_MAX_LIMIT = int(os.environ.get('PUBLIC_TRANSPARENCY_MAX_LIMIT', '200'))
    IVR_CALLBACK_MAX_RETRIES = int(os.environ.get('IVR_CALLBACK_MAX_RETRIES', '2'))
    IVR_CALLBACK_RETRY_BASE_SECONDS = float(os.environ.get('IVR_CALLBACK_RETRY_BASE_SECONDS', '60'))
    IVR_CALLBACK_RETRY_BACKOFF_MULTIPLIER = float(os.environ.get('IVR_CALLBACK_RETRY_BACKOFF_MULTIPLIER', '2.0'))
    IVR_CALLBACK_RETRY_MAX_SECONDS = float(os.environ.get('IVR_CALLBACK_RETRY_MAX_SECONDS', '1800'))
    IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD = int(os.environ.get('IVR_CALLBACK_ALERT_FAILURE_COUNT_THRESHOLD', '5'))
    IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD = int(os.environ.get('IVR_CALLBACK_ALERT_DEAD_LETTER_COUNT_THRESHOLD', '3'))
    IVR_CALLBACK_ALERT_DEDUPE_SECONDS = int(os.environ.get('IVR_CALLBACK_ALERT_DEDUPE_SECONDS', '1800'))
    IVR_CALLBACK_ALERT_COOLDOWN_SECONDS = int(os.environ.get('IVR_CALLBACK_ALERT_COOLDOWN_SECONDS', '900'))
    PUBLIC_TRANSPARENCY_DP_ENABLED = _as_bool('PUBLIC_TRANSPARENCY_DP_ENABLED', False)
    PUBLIC_TRANSPARENCY_DP_EPSILON = float(os.environ.get('PUBLIC_TRANSPARENCY_DP_EPSILON', '0.75'))
    L4_SECC_DATA_PATH = os.environ.get('L4_SECC_DATA_PATH', 'static/data/layer4_sources/secc_deprivation_2011.csv')
    L4_CENSUS_DATA_PATH = os.environ.get('L4_CENSUS_DATA_PATH', 'static/data/layer4_sources/census_2011_demographics.csv')
    L4_NFHS_DATA_PATH = os.environ.get('L4_NFHS_DATA_PATH', 'static/data/layer4_sources/nfhs5_health_indicators.csv')
    L4_SDG_DATA_PATH = os.environ.get('L4_SDG_DATA_PATH', 'static/data/layer4_sources/sdg_india_index_2023.csv')
    L4_NITI_MPI_DATA_PATH = os.environ.get('L4_NITI_MPI_DATA_PATH', 'static/data/layer4_sources/niti_aayog_mpi_2023.csv')
    L4_ASPIRATIONAL_DATA_PATH = os.environ.get('L4_ASPIRATIONAL_DATA_PATH', 'static/data/layer4_sources/aspirational_districts_list.csv')
    L4_GATI_SHAKTI_DATA_PATH = os.environ.get('L4_GATI_SHAKTI_DATA_PATH', 'static/data/layer4_sources/pm_gati_shakti_infra_nodes.csv')
    L4_BUDGET_OUTLAY_DATA_PATH = os.environ.get('L4_BUDGET_OUTLAY_DATA_PATH', 'static/data/layer4_sources/state_budget_outlays_2025_26.csv')
    L5_RAG_CORPUS_PATH = os.environ.get('L5_RAG_CORPUS_PATH', 'docs/release/layer5_rag_corpus.sample.json')
    L5_RAG_MAX_CITATIONS = int(os.environ.get('L5_RAG_MAX_CITATIONS', '5'))

    DPDP_CONTROL_MAPPING_VERSION = os.environ.get('DPDP_CONTROL_MAPPING_VERSION', 'dpdp-2023-v1')
    DPDP_EVIDENCE_ARTIFACT_PATH = os.environ.get('DPDP_EVIDENCE_ARTIFACT_PATH', 'docs/reports/DPDP_Control_Evidence.md')
    DPDP_EVIDENCE_ARTIFACTS = _load_json_list('DPDP_EVIDENCE_ARTIFACTS_JSON', [
        {'name': 'Consent Ledger API', 'path': '/api/v1/governance/consent-ledger'},
        {'name': 'DPDP Control Mapping', 'path': '/api/v1/governance/dpdp-controls'},
        {'name': 'PII Scrubbing Pipeline', 'path': 'visbharat/services/pii_scrubber.py'},
        {'name': 'Public DP Transparency', 'path': '/api/public/transparency/summary'},
    ])
    DPDP_LAST_VERIFIED_AT = os.environ.get('DPDP_LAST_VERIFIED_AT', '')
    DPG_REGISTRATION_STATUS = os.environ.get('DPG_REGISTRATION_STATUS', 'in_progress')
    DPG_REGISTRY_ID = os.environ.get('DPG_REGISTRY_ID', '')
    DPG_PROJECT_URL = os.environ.get('DPG_PROJECT_URL', '')
    DPG_OPEN_SOURCE_LICENSE = os.environ.get('DPG_OPEN_SOURCE_LICENSE', 'MIT')
    DPG_STATUS_LAST_UPDATED = os.environ.get('DPG_STATUS_LAST_UPDATED', '')
    DPG_STATUS_NOTES = os.environ.get('DPG_STATUS_NOTES', '')
    DPG_EVIDENCE_LINKS = _load_json_list('DPG_EVIDENCE_LINKS_JSON', [
        '/api/v1/governance/dpg-status',
        '/api/v1/governance/dpdp-controls',
    ])

    DPDP_CONTROL_MATRIX = _load_json_dict('DPDP_CONTROL_MATRIX_JSON', {
        'consent_capture': {'status': 'implemented', 'artifact': '/api/v1/governance/consent-ledger'},
        'purpose_limitation': {'status': 'implemented', 'artifact': 'consent_scope'},
        'data_minimization': {'status': 'partial', 'artifact': 'subject_ref hashing + metadata'},
        'privacy_by_design': {'status': 'implemented', 'artifact': '/api/public/transparency/* dp metadata'},
        'security_safeguards': {'status': 'implemented', 'artifact': 'signed webhooks + auth'},
    })















