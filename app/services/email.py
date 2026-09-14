"""
app/services/email.py
──────────────────────
Email delivery service.
Supports three providers:
1. "power_automate" — Microsoft Power Automate HTTP Trigger Flow (zero-admin webhook).
2. "microsoft365"    — Microsoft Graph REST API using OAuth 2.0 Client Credentials.
3. "smtp"            — Standard SMTP (smtplib) with STARTTLS or SSL.

Sends the formatted digest email to the CEO.
"""
from __future__ import annotations

import email.utils
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import settings

log = structlog.get_logger(__name__)


class EmailService:
    """Email delivery service supporting Power Automate, Microsoft 365 Graph, and SMTP."""

    def __init__(self) -> None:
        self.provider = settings.email_provider.lower()
        self._from = settings.email_from
        self._recipient = settings.email_recipient

        # Power Automate Webhook settings
        self._power_automate_webhook_url = settings.power_automate_webhook_url

        # Microsoft 365 Graph API settings
        self._tenant_id = settings.microsoft_tenant_id
        self._client_id = settings.microsoft_client_id
        self._client_secret = settings.microsoft_client_secret

        # SMTP settings
        self._host = settings.smtp_host
        self._port = settings.smtp_port
        self._user = settings.smtp_user
        self._pass = settings.smtp_pass
        self._use_tls = settings.smtp_use_tls

    @staticmethod
    def _extract_email_address(addr_str: str) -> str:
        """Extract clean email address from 'Display Name <user@domain.com>' or 'user@domain.com'."""
        _, addr = email.utils.parseaddr(addr_str)
        return addr.strip() if addr else addr_str.strip()

    def _send_power_automate(
        self, subject: str, html_body: str, plain_body: str, to: str
    ) -> bool:
        """Send email via a Microsoft Power Automate HTTP trigger flow."""
        if not self._power_automate_webhook_url:
            raise ValueError(
                "Power Automate provider requires POWER_AUTOMATE_WEBHOOK_URL to be set in .env."
            )

        payload = {
            "to": to,
            "subject": subject,
            "html_body": html_body,
            "plain_body": plain_body,
            "from": self._from,
        }
        resp = httpx.post(
            self._power_automate_webhook_url,
            json=payload,
            timeout=settings.http_timeout_seconds,
        )
        if resp.status_code not in (200, 202):
            log.error("email.power_automate_failed", status=resp.status_code, body=resp.text)
            resp.raise_for_status()

        log.info("email.sent_power_automate", to=to, subject=subject)
        return True

    def _get_microsoft_access_token(self) -> str:
        """Fetch OAuth 2.0 access token for Microsoft Graph via client credentials."""
        if not self._tenant_id or not self._client_id or not self._client_secret:
            raise ValueError(
                "Microsoft 365 email provider requires MICROSOFT_TENANT_ID, "
                "MICROSOFT_CLIENT_ID, and MICROSOFT_CLIENT_SECRET to be configured."
            )

        token_url = f"https://login.microsoftonline.com/{self._tenant_id}/oauth2/v2.0/token"
        data = {
            "client_id": self._client_id,
            "scope": "https://graph.microsoft.com/.default",
            "client_secret": self._client_secret,
            "grant_type": "client_credentials",
        }
        resp = httpx.post(token_url, data=data, timeout=settings.http_timeout_seconds)
        if resp.status_code != 200:
            log.error("email.m365_token_failed", status=resp.status_code, body=resp.text)
            resp.raise_for_status()
        return resp.json()["access_token"]

    def _send_microsoft365(
        self, subject: str, html_body: str, plain_body: str, to: str
    ) -> bool:
        """Send email via Microsoft Graph API (POST /users/{from}/sendMail)."""
        clean_from = self._extract_email_address(self._from)
        token = self._get_microsoft_access_token()

        send_url = f"https://graph.microsoft.com/v1.0/users/{clean_from}/sendMail"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        payload = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML",
                    "content": html_body,
                },
                "toRecipients": [
                    {
                        "emailAddress": {
                            "address": to,
                        }
                    }
                ],
            },
            "saveToSentItems": True,
        }
        resp = httpx.post(
            send_url,
            headers=headers,
            json=payload,
            timeout=settings.http_timeout_seconds,
        )
        if resp.status_code not in (200, 202):
            log.error("email.m365_send_failed", status=resp.status_code, body=resp.text)
            resp.raise_for_status()

        log.info("email.sent_m365", to=to, subject=subject)
        return True

    def _send_smtp(
        self, subject: str, html_body: str, plain_body: str, to: str
    ) -> bool:
        """Send email via standard SMTP."""
        if not self._host or not self._user:
            raise ValueError("SMTP email provider requires SMTP_HOST and SMTP_USER to be set.")

        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = self._from
        msg["To"] = to

        # Attach plain text first (fallback), then HTML
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
        msg.attach(MIMEText(html_body, "html", "utf-8"))

        if self._use_tls:
            context = ssl.create_default_context()
            with smtplib.SMTP(self._host, self._port, timeout=30) as server:
                server.ehlo()
                server.starttls(context=context)
                if self._user and self._pass:
                    server.login(self._user, self._pass)
                server.sendmail(self._from, [to], msg.as_string())
        else:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=30) as server:
                if self._user and self._pass:
                    server.login(self._user, self._pass)
                server.sendmail(self._from, [to], msg.as_string())

        log.info("email.sent_smtp", to=to, subject=subject)
        return True

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(settings.email_retry_attempts),
        wait=wait_exponential(multiplier=2, min=5, max=60),
    )
    def send(
        self,
        subject: str,
        html_body: str,
        plain_body: str,
        recipient: str | None = None,
    ) -> bool:
        """
        Send an email via configured provider ('power_automate', 'microsoft365', or 'smtp').
        Returns True on success, raises on final failure.
        """
        to = recipient or self._recipient

        try:
            if self.provider == "power_automate":
                return self._send_power_automate(subject, html_body, plain_body, to)
            elif self.provider == "microsoft365":
                return self._send_microsoft365(subject, html_body, plain_body, to)
            else:
                return self._send_smtp(subject, html_body, plain_body, to)
        except Exception as exc:
            log.error("email.failed", provider=self.provider, error=str(exc))
            raise


# Module-level singleton
email_service = EmailService()
