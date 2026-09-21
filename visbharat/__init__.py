import os
from flask import Flask, jsonify
from flask_cors import CORS

from .config import Config
from .db import init_app as init_db
from .services.repository import ReferenceDataRepository
from .services.google_ai import GoogleAIClient
from .services.google_speech import GoogleSpeechToTextClient
from .services.google_tts import GoogleTextToSpeechClient
from .services.google_bigquery import GoogleBigQueryClient
from .services.google_vertex import GoogleVertexPredictionClient
from .services.google_dialogflow import GoogleDialogflowCXClient
from .services.google_translation import GoogleTranslationClient
from .services.google_maps import GoogleMapsPlatformClient
from .services.google_secret_manager import GoogleSecretManagerProvider
from .blueprints.web import web_bp
from .blueprints.api import api_bp


def create_app(config=None):
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    app = Flask(
        __name__,
        template_folder=os.path.join(root_dir, 'templates'),
        static_folder=os.path.join(root_dir, 'static')
    )
    app.config.from_object(Config)
    if config:
        app.config.update(config)

    Config.validate_production_security()

    cors_origins = [
        origin.strip()
        for origin in os.environ.get(
            'CORS_ALLOWED_ORIGINS',
            'https://visbharat.nexusaitech.in,https://nexus-visbharath-510474645723.asia-south1.run.app,http://localhost:5000,http://127.0.0.1:5000,http://localhost:5001,http://127.0.0.1:5001'
        ).split(',')
        if origin.strip()
    ]
    CORS(app, origins=cors_origins, supports_credentials=True)

    app.extensions['reference_repo'] = ReferenceDataRepository(
        root_dir,
        allowed_state_to_districts=app.config.get('ALLOWED_STATE_TO_DISTRICTS', {}),
    )
    app.extensions['google_ai_client'] = None
    app.extensions['google_stt_client'] = None
    app.extensions['google_tts_client'] = None
    app.extensions['google_bigquery_client'] = None
    app.extensions['google_vertex_client'] = None
    app.extensions['google_dialogflow_client'] = None
    app.extensions['google_translation_client'] = None
    app.extensions['google_maps_client'] = None
    app.extensions['secret_provider'] = None
    app.extensions['google_ops_pubsub_client'] = None

    # Test isolation gate: when NVB_DISABLE_EXTERNAL_SERVICES=1 (set by tests/__init__.py),
    # no external Google clients are constructed; all AI paths use deterministic simulation.
    external_disabled = app.config.get('DISABLE_EXTERNAL_SERVICES', False) or os.environ.get('NVB_DISABLE_EXTERNAL_SERVICES', '').strip().lower() in {'1', 'true', 'yes'}
    app.config['EXTERNAL_SERVICES_ENABLED'] = not external_disabled

    api_key = app.config.get('GOOGLE_AI_API_KEY', '')
    vertex_project = app.config.get('VERTEX_PROJECT_ID', '')
    vertex_location = app.config.get('VERTEX_LOCATION', 'asia-south1')
    use_vertex = app.config.get('USE_VERTEX_FOR_GEMINI', False)

    if not external_disabled and app.config.get('USE_REAL_GOOGLE_AI') and (api_key or vertex_project):
        app.extensions['google_ai_client'] = GoogleAIClient(
            api_key=api_key,
            project_id=vertex_project,
            location=vertex_location,
            use_vertex=use_vertex,
        )
        if app.config.get('PILOT_ONLY'):
            from .services.pilot_ai import PilotGoogleAI
            app.extensions['google_ai_client'] = PilotGoogleAI(vertex_project,vertex_location,app.config['PILOT_MODEL_NAME'])

    if not external_disabled and app.config.get('USE_REAL_GOOGLE_TTS'):
        try:
            app.extensions['google_tts_client'] = GoogleTextToSpeechClient(
                language_code=app.config.get('GOOGLE_TTS_LANGUAGE_CODE', 'en-IN'),
            )
        except Exception:
            app.extensions['google_tts_client'] = None

    if not external_disabled and app.config.get('USE_REAL_GOOGLE_STT'):
        try:
            app.extensions['google_stt_client'] = GoogleSpeechToTextClient()
        except Exception:
            app.extensions['google_stt_client'] = None

    if not external_disabled and app.config.get('PILOT_SPEECH_LOCATION'):
        from .services.pilot_speech import RegionalSpeech
        app.extensions['pilot_regional_stt'] = RegionalSpeech(vertex_project,
            app.config['PILOT_SPEECH_LOCATION'],app.config['PILOT_SPEECH_MODEL'])

    if not external_disabled and (app.config.get('USE_REAL_BIGQUERY') or app.config.get('BIGQUERY_PROJECT_ID')):
        try:
            app.extensions['google_bigquery_client'] = GoogleBigQueryClient(
                project_id=app.config.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat'),
                dataset=app.config.get('BIGQUERY_DATASET', 'visbharat_analytics'),
                table=app.config.get('BIGQUERY_TABLE', 'citizen_requests_fused'),
                location=app.config.get('BIGQUERY_LOCATION', 'asia-south1'),
            )
        except Exception:
            app.extensions['google_bigquery_client'] = None

    if not external_disabled and (app.config.get('USE_REAL_PUBSUB') or app.config.get('BIGQUERY_PROJECT_ID')):
        try:
            from .services.google_pubsub import GooglePubSubPublisherClient
            app.extensions['google_pubsub_client'] = GooglePubSubPublisherClient(
                project_id=app.config.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat'),
                topic_id=app.config.get('PUBSUB_TOPIC_ID', 'citizen-ingestion-topic'),
            )
        except Exception:
            app.extensions['google_pubsub_client'] = None

        try:
            from .services.google_pubsub import GooglePubSubPublisherClient
            app.extensions['google_ops_pubsub_client'] = GooglePubSubPublisherClient(
                project_id=app.config.get('BIGQUERY_PROJECT_ID', 'nexus-visbharat'),
                topic_id=app.config.get('OPS_ALERT_PUBSUB_TOPIC_ID', 'ops-alert-topic'),
            )
        except Exception:
            app.extensions['google_ops_pubsub_client'] = None

    if not external_disabled and (app.config.get('USE_REAL_VERTEX_PREDICTION') or app.config.get('VERTEX_PROJECT_ID')):
        try:
            app.extensions['google_vertex_client'] = GoogleVertexPredictionClient(
                project_id=app.config.get('VERTEX_PROJECT_ID', 'nexus-visbharat'),
                location=app.config.get('VERTEX_LOCATION', 'asia-south1'),
                endpoint_id=app.config.get('VERTEX_ENDPOINT_ID', 'nvb-sps-model-v24'),
                api_endpoint=app.config.get('VERTEX_API_ENDPOINT', ''),
            )
        except Exception:
            app.extensions['google_vertex_client'] = None

    if not external_disabled and app.config.get('USE_GCP_SECRET_MANAGER'):
        try:
            app.extensions['secret_provider'] = GoogleSecretManagerProvider(
                project_id=app.config.get('GCP_SECRET_MANAGER_PROJECT_ID', 'nexus-visbharath-prod'),
                version=app.config.get('GCP_SECRET_MANAGER_VERSION', 'latest'),
                cache_ttl_seconds=app.config.get('GCP_SECRET_MANAGER_CACHE_TTL_SECONDS', 300),
                max_retries=app.config.get('GCP_SECRET_MANAGER_MAX_RETRIES', 2),
                retry_backoff_seconds=app.config.get('GCP_SECRET_MANAGER_RETRY_BACKOFF_SECONDS', 0.25),
                retry_backoff_multiplier=app.config.get('GCP_SECRET_MANAGER_RETRY_BACKOFF_MULTIPLIER', 2.0),
                retry_jitter_seconds=app.config.get('GCP_SECRET_MANAGER_RETRY_JITTER_SECONDS', 0.1),
            )
        except Exception:
            app.extensions['secret_provider'] = None

    if not external_disabled and (app.config.get('USE_REAL_DIALOGFLOW_CX') or app.config.get('DIALOGFLOW_PROJECT_ID')):
        try:
            app.extensions['google_dialogflow_client'] = GoogleDialogflowCXClient(
                project_id=app.config.get('DIALOGFLOW_PROJECT_ID', 'nexus-visbharath-prod'),
                location=app.config.get('DIALOGFLOW_LOCATION', 'asia-south1'),
                agent_id=app.config.get('DIALOGFLOW_AGENT_ID', 'nvb-omnichannel-agent-v1'),
                language_code=app.config.get('DIALOGFLOW_LANGUAGE_CODE', 'en'),
                api_endpoint=app.config.get('DIALOGFLOW_API_ENDPOINT', ''),
            )
        except Exception:
            app.extensions['google_dialogflow_client'] = None

    trans_api_key = app.config.get('GOOGLE_TRANSLATE_API_KEY') or app.config.get('GOOGLE_AI_API_KEY') or ''
    trans_proj_id = app.config.get('GOOGLE_TRANSLATION_PROJECT_ID', '')
    if not external_disabled and (trans_proj_id or trans_api_key or app.config.get('USE_REAL_GOOGLE_TRANSLATION')):
        try:
            app.extensions['google_translation_client'] = GoogleTranslationClient(
                project_id=trans_proj_id,
                location=app.config.get('GOOGLE_TRANSLATION_LOCATION', 'global'),
                api_key=trans_api_key
            )
        except Exception:
            app.extensions['google_translation_client'] = None

    if not external_disabled and app.config.get('USE_REAL_GOOGLE_MAPS') and app.config.get('GOOGLE_MAPS_API_KEY'):
        try:
            app.extensions['google_maps_client'] = GoogleMapsPlatformClient(
                api_key=app.config.get('GOOGLE_MAPS_API_KEY', ''),
            )
        except Exception:
            app.extensions['google_maps_client'] = None

    init_db(app)

    app.register_blueprint(web_bp)
    app.register_blueprint(api_bp)
    from .blueprints.analyst import analyst_bp
    app.register_blueprint(analyst_bp)
    from .blueprints.auditor import auditor_bp
    app.register_blueprint(auditor_bp)
    from .blueprints.pilot import pilot_bp, install_pilot
    app.register_blueprint(pilot_bp)
    install_pilot(app)
    # Resume bounded, local evaluation jobs after a worker/process restart.
    if app.config.get('LOCAL_EVALUATION_WORKER', app.config.get('DEMO_MODE', True)):
      with app.app_context():
        from .db import get_db
        pending=get_db().execute("SELECT job_id FROM auditor_jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()
        if pending:
            from .services.auditor_evaluation import start_worker
            start_worker(app)

    @app.errorhandler(404)
    def not_found(error):
        return jsonify({'success': False, 'error': 'Not found'}), 404

    @app.errorhandler(500)
    def internal_error(error):
        return jsonify({'success': False, 'error': 'Internal server error'}), 500

    return app



