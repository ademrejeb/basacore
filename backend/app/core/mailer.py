"""Pluggable transactional email delivery.

Development uses the log sink so the platform is fully usable without an SMTP
server. Production must configure SMTP; delivery failures are logged but never
leak tokens into logs.
"""

from __future__ import annotations

import asyncio
import smtplib
from email.message import EmailMessage

import structlog

from app.core.config import get_settings

logger = structlog.get_logger("mailer")
settings = get_settings()


def _deliver(recipient: str, subject: str, body: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from or "no-reply@basacore.local"
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(settings.smtp_host or "localhost", settings.smtp_port, timeout=10) as smtp:
        if settings.smtp_starttls:
            smtp.starttls()
        if settings.smtp_username:
            smtp.login(settings.smtp_username, settings.smtp_password or "")
        smtp.send_message(message)


async def send_email(recipient: str, subject: str, body: str) -> bool:
    """Send an email. Returns False when no transport is configured."""
    if not settings.smtp_host:
        logger.info("email_log_sink", recipient=recipient, subject=subject)
        return False
    try:
        await asyncio.to_thread(_deliver, recipient, subject, body)
        return True
    except Exception as exc:  # noqa: BLE001 - delivery is best effort
        logger.error("email_delivery_failed", recipient=recipient, error=str(exc))
        return False
