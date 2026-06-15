"""Sends email over Gmail SMTP (stdlib only).

Kept in its own module so both the price-watch scheduler and the AI agent can send
mail without importing each other (which would create a circular import).

Setup: use a dedicated Gmail account, enable 2-Step Verification, create an
"App password", and put SMTP_USER + SMTP_APP_PASSWORD in .env. If those are missing,
send_email() does nothing and returns False (the rest of the app keeps working).
"""

import os
import smtplib
from email.message import EmailMessage

from dotenv import load_dotenv

load_dotenv()

SMTP_HOST = os.environ.get('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.environ.get('SMTP_PORT', '465'))
SMTP_USER = os.environ.get('SMTP_USER')              # the watcher Gmail address
SMTP_APP_PASSWORD = os.environ.get('SMTP_APP_PASSWORD')  # 16-char Google app password


def email_configured() -> bool:
    """True only if both credentials are present, so callers can warn early."""
    return bool(SMTP_USER and SMTP_APP_PASSWORD)


def send_email(to: str, subject: str, body: str) -> bool:
    """Send one plain-text email. Returns True on success, False on any failure.

    We never raise: a failed email should not crash the scheduler or the agent loop.
    """
    if not email_configured():
        print('[email_service] SMTP_USER / SMTP_APP_PASSWORD not set — email skipped.')
        return False

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = SMTP_USER
    msg['To'] = to
    msg.set_content(body)

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            server.login(SMTP_USER, SMTP_APP_PASSWORD)
            # Send the message's own UTF-8-safe bytes via sendmail rather than
            # server.send_message(), which re-flattens through a path that can hit the
            # platform default codec (cp1252 on Windows) and choke on characters like ₹.
            server.sendmail(SMTP_USER, [to], msg.as_bytes())
        print(f'[email_service] email sent to {to}: {subject}')
        return True
    except Exception as exc:
        print(f'[email_service] failed to send email: {exc}')
        return False
