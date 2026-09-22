"""Storage checks for operational and explicitly disposable showcase deployments."""
import os


def disposable_showcase_allowed(config):
    def enabled(value):
        return str(value).strip().lower() in ('true', '1', 'yes')
    return enabled(config.get('DEMO_MODE', False)) and enabled(
        config.get('ALLOW_EPHEMERAL_SHOWCASE', False))


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
        if not disposable_showcase_allowed(config):
            raise RuntimeError('Cloud Run requires a PostgreSQL DATABASE_URL. Only a disposable DEMO_MODE showcase may explicitly set ALLOW_EPHEMERAL_SHOWCASE=true.')
