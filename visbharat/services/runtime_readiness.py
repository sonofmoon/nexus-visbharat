"""Storage checks for operational and explicitly disposable showcase deployments."""
import os


def persistence(config):
    url = str(config.get('DATABASE_URL') or '').strip()
    postgres = url.startswith(('postgresql://', 'postgres://'))
    cloud = bool(os.environ.get('K_SERVICE'))
    return {'backend': 'postgres' if postgres else 'sqlite',
            'database_url_configured': bool(url),
            'shared_database': postgres,
            'cloud_runtime': cloud,
            'restart_durability': 'external_database' if postgres else 'not_verified' if cloud else 'local_file',
            'operational_ready': postgres if cloud else True,
            'showcase_only': bool(cloud and not postgres),
            'message': ('External PostgreSQL configured; restore and multi-instance checks are still required.' if postgres
                        else 'Disposable showcase storage: new records may be lost on instance replacement.' if cloud
                        else 'Local SQLite development storage.')}


def validate(config):
    url = str(config.get('DATABASE_URL') or '').strip()
    if url and not url.startswith(('postgresql://','postgres://','sqlite:///')):
        raise RuntimeError('Unsupported DATABASE_URL scheme; refusing to fall back to SQLite.')
    state = persistence(config)
    if state['cloud_runtime'] and not state['shared_database']:
        allow = bool(config.get('ALLOW_EPHEMERAL_SHOWCASE')) or 'true' in str(os.environ.get('ALLOW_EPHEMERAL_SHOWCASE', '')).lower()
        demo = bool(config.get('DEMO_MODE', True))
        if not (demo and allow):
            raise RuntimeError('Cloud Run requires a PostgreSQL DATABASE_URL. Only a disposable DEMO_MODE showcase may explicitly set ALLOW_EPHEMERAL_SHOWCASE=true.')
