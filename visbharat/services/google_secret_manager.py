import random
import time


class GoogleSecretManagerProvider:
    def __init__(
        self,
        project_id: str = '',
        version: str = 'latest',
        cache_ttl_seconds: int = 300,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_jitter_seconds: float = 0.1,
    ):
        try:
            from google.cloud import secretmanager  # type: ignore
        except Exception as err:  # pragma: no cover
            raise RuntimeError('google-cloud-secret-manager is not installed') from err

        self.secretmanager = secretmanager
        self.project_id = str(project_id or '').strip()
        self.version = str(version or 'latest').strip() or 'latest'
        self.client = secretmanager.SecretManagerServiceClient()

        self.cache_ttl_seconds = max(int(cache_ttl_seconds or 0), 0)
        self.max_retries = max(int(max_retries or 0), 0)
        self.retry_backoff_seconds = max(float(retry_backoff_seconds or 0), 0.0)
        self.retry_backoff_multiplier = max(float(retry_backoff_multiplier or 1.0), 1.0)
        self.retry_jitter_seconds = max(float(retry_jitter_seconds or 0), 0.0)
        self._cache = {}

    def _secret_path(self, name: str):
        value = str(name or '').strip()
        if not value:
            raise ValueError('secret name is required')
        if value.startswith('projects/'):
            if '/versions/' in value:
                return value
            return f"{value}/versions/{self.version}"
        if not self.project_id:
            raise ValueError('project_id is required when using short secret names')
        return f"projects/{self.project_id}/secrets/{value}/versions/{self.version}"

    def _decode_secret_payload(self, response):
        data = getattr(getattr(response, 'payload', None), 'data', b'') or b''
        if isinstance(data, bytes):
            return data.decode('utf-8', errors='ignore')
        return str(data or '')

    def _read_with_retry(self, path: str):
        attempts = 0
        while attempts <= self.max_retries:
            attempts += 1
            try:
                response = self.client.access_secret_version(request={'name': path})
                return self._decode_secret_payload(response)
            except Exception:
                if attempts > self.max_retries:
                    raise
                delay = self.retry_backoff_seconds * (self.retry_backoff_multiplier ** (attempts - 1))
                if self.retry_jitter_seconds > 0:
                    delay += random.uniform(0.0, self.retry_jitter_seconds)
                if delay > 0:
                    time.sleep(delay)
        raise RuntimeError('secret manager retry loop exited unexpectedly')

    def get_secret(self, name: str, *, refresh: bool = False):
        path = self._secret_path(name)
        now_epoch = time.time()

        if not refresh and self.cache_ttl_seconds > 0:
            cached = self._cache.get(path)
            if cached and float(cached.get('expires_at', 0)) > now_epoch:
                return str(cached.get('value') or '')

        value = self._read_with_retry(path)
        if self.cache_ttl_seconds > 0:
            self._cache[path] = {'value': value, 'expires_at': now_epoch + self.cache_ttl_seconds}
        return value

    def get_secret_with_rotation(self, name: str, versions=None):
        candidates = [str(item or '').strip() for item in (versions or []) if str(item or '').strip()]
        if not candidates:
            candidates = [self.version, 'latest'] if self.version != 'latest' else ['latest']

        last_error = None
        for ver in candidates:
            original_version = self.version
            try:
                self.version = ver
                return self.get_secret(name)
            except Exception as err:
                last_error = err
            finally:
                self.version = original_version

        if last_error:
            raise last_error
        return ''
