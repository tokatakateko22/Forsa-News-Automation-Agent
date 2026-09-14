"""
conftest.py
────────────
Pytest configuration: sets dummy environment variables before any app module
is imported, so that pydantic-settings does not fail with "Field required".

Unit tests do NOT need real API keys — they test pure logic only.
"""
import os

# Set required env vars BEFORE any app import resolves the settings singleton
_TEST_ENV = {
    "DATABASE_URL": "postgresql+asyncpg://test:test@localhost:5432/test_db",
    "GOOGLE_API_KEY": "test-google-key",
    "SERPAPI_KEY": "test-serpapi-key",
    "SMTP_HOST": "smtp.test.com",
    "SMTP_USER": "test@test.com",
    "SMTP_PASS": "test-pass",
    "EMAIL_RECIPIENT": "ceo@test.com",
    "ENVIRONMENT": "test",
}

for key, val in _TEST_ENV.items():
    os.environ.setdefault(key, val)
