import sys
import types
import unittest


class _FakePayload:
    def __init__(self, data: bytes):
        self.data = data


class _FakeResponse:
    def __init__(self, data: bytes):
        self.payload = _FakePayload(data)


class _FakeSecretClient:
    def __init__(self):
        self.last_name = ''
        self.calls = 0

    def access_secret_version(self, request):
        self.calls += 1
        self.last_name = str((request or {}).get('name') or '')
        return _FakeResponse(b'secret-value')


class _FlakySecretClient(_FakeSecretClient):
    def __init__(self):
        super().__init__()
        self.failures_left = 1

    def access_secret_version(self, request):
        if self.failures_left > 0:
            self.failures_left -= 1
            raise RuntimeError('temporary error')
        return super().access_secret_version(request)


class TestGoogleSecretManagerProvider(unittest.TestCase):
    def setUp(self):
        self._modules = dict(sys.modules)

    def tearDown(self):
        sys.modules.clear()
        sys.modules.update(self._modules)

    def _install_fake_google_secretmanager(self, client_class=_FakeSecretClient):
        fake_mod = types.ModuleType('secretmanager')
        fake_mod.SecretManagerServiceClient = client_class

        google_mod = types.ModuleType('google')
        cloud_mod = types.ModuleType('google.cloud')
        cloud_mod.secretmanager = fake_mod
        google_mod.cloud = cloud_mod

        sys.modules['google'] = google_mod
        sys.modules['google.cloud'] = cloud_mod
        sys.modules['google.cloud.secretmanager'] = fake_mod

    def test_provider_resolves_short_name_to_project_path(self):
        self._install_fake_google_secretmanager()
        from visbharat.services.google_secret_manager import GoogleSecretManagerProvider

        provider = GoogleSecretManagerProvider(project_id='demo-project', version='latest')
        value = provider.get_secret('email/api_key_primary')

        self.assertEqual(value, 'secret-value')
        self.assertEqual(
            provider.client.last_name,
            'projects/demo-project/secrets/email/api_key_primary/versions/latest',
        )

    def test_provider_accepts_full_resource_name(self):
        self._install_fake_google_secretmanager()
        from visbharat.services.google_secret_manager import GoogleSecretManagerProvider

        provider = GoogleSecretManagerProvider(project_id='demo-project', version='5')
        value = provider.get_secret('projects/p1/secrets/s1')

        self.assertEqual(value, 'secret-value')
        self.assertEqual(provider.client.last_name, 'projects/p1/secrets/s1/versions/5')

    def test_provider_uses_cache_until_expiry(self):
        self._install_fake_google_secretmanager()
        from visbharat.services.google_secret_manager import GoogleSecretManagerProvider

        provider = GoogleSecretManagerProvider(project_id='demo-project', cache_ttl_seconds=600)
        first = provider.get_secret('email/api_key_primary')
        second = provider.get_secret('email/api_key_primary')

        self.assertEqual(first, 'secret-value')
        self.assertEqual(second, 'secret-value')
        self.assertEqual(provider.client.calls, 1)

    def test_provider_retries_transient_errors(self):
        self._install_fake_google_secretmanager(client_class=_FlakySecretClient)
        from visbharat.services.google_secret_manager import GoogleSecretManagerProvider

        provider = GoogleSecretManagerProvider(project_id='demo-project', max_retries=2, retry_backoff_seconds=0)
        value = provider.get_secret('email/api_key_primary')

        self.assertEqual(value, 'secret-value')
        self.assertEqual(provider.client.calls, 1)


if __name__ == '__main__':
    unittest.main()
