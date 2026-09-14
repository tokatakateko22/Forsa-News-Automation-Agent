"""
scripts/test_email.py
─────────────────────
Diagnostic script to test email delivery using current .env configuration.
Supports Power Automate HTTP trigger, Microsoft 365 Graph API, and SMTP providers.
Sends a test email to EMAIL_RECIPIENT.

Usage:
    py -3 scripts/test_email.py
"""
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.services.email import EmailService


def main() -> None:
    print("=" * 65)
    print("Forsa News Agent — Email Configuration Test")
    print("=" * 65)
    print(f"Email Provider:  {settings.email_provider.upper()}")
    print(f"Email From:      {settings.email_from}")
    print(f"Recipient (To):  {settings.email_recipient}")

    if settings.email_provider.lower() == "power_automate":
        print("-" * 65)
        print("Power Automate Flow Settings:")
        masked_url = (
            settings.power_automate_webhook_url[:35] + "..." + settings.power_automate_webhook_url[-15:]
            if len(settings.power_automate_webhook_url) > 50
            else (settings.power_automate_webhook_url or "<NOT SET>")
        )
        print(f"  Trigger URL:   {masked_url}")
    elif settings.email_provider.lower() == "microsoft365":
        print("-" * 65)
        print("Microsoft 365 (Graph API) Settings:")
        print(f"  Tenant ID:     {settings.microsoft_tenant_id or '<NOT SET>'}")
        print(f"  Client ID:     {settings.microsoft_client_id or '<NOT SET>'}")
        masked_secret = (
            (settings.microsoft_client_secret[:4] + "..." + settings.microsoft_client_secret[-3:])
            if len(settings.microsoft_client_secret) > 8
            else ("<SET>" if settings.microsoft_client_secret else "<NOT SET>")
        )
        print(f"  Client Secret: {masked_secret}")
    else:
        print("-" * 65)
        print("SMTP Settings:")
        print(f"  SMTP Host:     {settings.smtp_host or '<NOT SET>'}")
        print(f"  SMTP Port:     {settings.smtp_port}")
        print(f"  SMTP User:     {settings.smtp_user or '<NOT SET>'}")
        print(f"  SMTP Use TLS:  {settings.smtp_use_tls}")

    print("-" * 65)
    print("Attempting to connect and send a test message...")

    svc = EmailService()
    subject = f"[{settings.email_subject_prefix}] Test Email ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')})"
    plain_body = (
        f"This is a test email sent from Forsa Financial News Monitoring Agent.\n"
        f"Provider: {settings.email_provider.upper()}\n"
        f"If you received this, your email delivery settings are working correctly!"
    )
    html_body = f"""
    <html>
      <body style="font-family: Arial, sans-serif; color: #1e293b; padding: 20px;">
        <h2 style="color: #0f172a;">Forsa News Agent — Email Test</h2>
        <p>This is a test email to verify that your email delivery configuration is working properly.</p>
        <p style="color: #16a34a; font-weight: bold;">
          ✔ Provider [{settings.email_provider.upper()}] delivery succeeded!
        </p>
        <hr style="border: 0; border-top: 1px solid #e2e8f0; margin: 20px 0;">
        <small style="color: #64748b;">Timestamp: {datetime.now().isoformat()}</small>
      </body>
    </html>
    """

    try:
        svc.send(subject=subject, html_body=html_body, plain_body=plain_body)
        print("\n✔ SUCCESS: Test email was delivered successfully!")
        print(f"Check the inbox of: {settings.email_recipient}")
    except Exception as exc:
        print(f"\n✖ FAILED to send test email: {exc}")
        print("\nTroubleshooting Tips:")
        if settings.email_provider.lower() == "power_automate":
            print("1. Ensure your Power Automate Flow is turned ON.")
            print("2. Check the Run History in Power Automate portal to view any flow run errors.")
            print("3. Verify the POWER_AUTOMATE_WEBHOOK_URL is copied completely from the HTTP trigger.")
        elif settings.email_provider.lower() == "microsoft365":
            print("1. Ensure Azure App Registration has Application permission: 'Mail.Send'.")
            print("2. Verify that an Azure Global/Exchange Admin granted 'Admin Consent' for 'Mail.Send'.")
            print("3. Check that MICROSOFT_TENANT_ID, MICROSOFT_CLIENT_ID, and MICROSOFT_CLIENT_SECRET match your Azure App Registration.")
            print("4. Verify EMAIL_FROM is an active mailbox in your Microsoft 365 tenant.")
        else:
            print("1. If using Microsoft 365 (smtp.office365.com):")
            print("   - Ensure Authenticated SMTP is enabled for your mailbox in M365 Admin.")
            print("   - If your organization enforces MFA, generate and use an App Password.")
            print("2. If using Gmail / Google Workspace (smtp.gmail.com):")
            print("   - Generate a 16-character App Password under Google Account Security.")
            print("3. Check that firewall / VPN does not block outbound port 587.")
        sys.exit(1)


if __name__ == "__main__":
    main()
