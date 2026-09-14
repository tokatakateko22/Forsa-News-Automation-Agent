"""
tests/unit/test_email_service.py
─────────────────────────────────
Unit tests for EmailService:
- SMTP provider path
- Microsoft 365 Graph API provider path
- Address extraction helper
- Email subject prefix trimming
"""
from unittest.mock import MagicMock, patch
import pytest

from app.services.email import EmailService


def test_extract_email_address():
    svc = EmailService()
    assert svc._extract_email_address("user@domain.com") == "user@domain.com"
    assert svc._extract_email_address("Forsa Agent <news@domain.com>") == "news@domain.com"
    assert svc._extract_email_address("  <test@domain.com>  ") == "test@domain.com"


def test_send_smtp_mocked():
    svc = EmailService()
    svc.provider = "smtp"
    svc._host = "smtp.test.com"
    svc._user = "user@test.com"
    svc._pass = "pass"
    svc._use_tls = True

    with patch("smtplib.SMTP") as mock_smtp:
        instance = MagicMock()
        mock_smtp.return_value.__enter__.return_value = instance

        result = svc.send(
            subject="Test Subject",
            html_body="<p>Hello</p>",
            plain_body="Hello",
            recipient="recipient@test.com",
        )

        assert result is True
        assert instance.sendmail.called


def test_send_microsoft365_mocked():
    svc = EmailService()
    svc.provider = "microsoft365"
    svc._from = "Forsa Agent <sender@domain.com>"
    svc._tenant_id = "tenant-123"
    svc._client_id = "client-123"
    svc._client_secret = "secret-123"

    with patch("httpx.post") as mock_post:
        # Mock token response
        token_resp = MagicMock()
        token_resp.status_code = 200
        token_resp.json.return_value = {"access_token": "mock-token-xyz"}

        # Mock sendMail response
        send_resp = MagicMock()
        send_resp.status_code = 202

        mock_post.side_effect = [token_resp, send_resp]

        result = svc.send(
            subject="Digest Subject",
            html_body="<h1>News</h1>",
            plain_body="News",
            recipient="ceo@domain.com",
        )

        assert result is True
        assert mock_post.call_count == 2

        # Verify token call
        token_call = mock_post.call_args_list[0]
        assert "login.microsoftonline.com/tenant-123" in token_call[0][0]
        assert token_call[1]["data"]["client_id"] == "client-123"

        # Verify sendMail call
        send_call = mock_post.call_args_list[1]
        assert "graph.microsoft.com/v1.0/users/sender@domain.com/sendMail" in send_call[0][0]
        assert "mock-token-xyz" in send_call[1]["headers"]["Authorization"]
        assert send_call[1]["json"]["message"]["subject"] == "Digest Subject"


def test_send_power_automate_mocked():
    svc = EmailService()
    svc.provider = "power_automate"
    svc._power_automate_webhook_url = "https://prod-01.westus.logic.azure.com:443/workflows/mock-id/triggers/manual/paths/invoke"

    with patch("httpx.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 202
        mock_post.return_value = resp

        result = svc.send(
            subject="Test Power Automate",
            html_body="<p>Digest</p>",
            plain_body="Digest",
            recipient="ceo@test.com",
        )

        assert result is True
        assert mock_post.called
        call_args = mock_post.call_args
        assert call_args[0][0] == svc._power_automate_webhook_url
        assert call_args[1]["json"]["subject"] == "Test Power Automate"
        assert call_args[1]["json"]["to"] == "ceo@test.com"
        assert call_args[1]["json"]["html_body"] == "<p>Digest</p>"
