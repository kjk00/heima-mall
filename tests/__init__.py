"""Unit tests run hermetically: external services are disabled by default.

app.config.load_environment() exports .env values via os.environ.setdefault, so a
test that builds a default app would otherwise leak local MongoDB/MinIO/ES/Redis
settings into every later test. Neutralize them here; gated integration tests
re-enable specific services inside unittest.mock.patch.dict(os.environ, ...).
"""
import os

for _key in ('REDIS_URL', 'ELASTICSEARCH_URL', 'RABBITMQ_URL', 'MONGODB_URL', 'OSS_ENDPOINT', 'PHOTO_SEARCH_API_KEY'):
    os.environ[_key] = ''
