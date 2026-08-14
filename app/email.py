import logging
import smtplib
from email.message import EmailMessage

from app.config import get_settings

logger = logging.getLogger(__name__)


def email_enabled() -> bool:
    return bool(get_settings().smtp_host)


def send_email(to: str, subject: str, text: str, html: str | None = None) -> None:
    settings = get_settings()
    if not settings.smtp_host:
        logger.info("SMTP not configured; skipping email to %s", to)
        return
    message = EmailMessage()
    message["From"] = settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    if html:
        message.add_alternative(html, subtype="html")
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        server.starttls()
        if settings.smtp_username:
            server.login(settings.smtp_username, settings.smtp_password)
        server.send_message(message)
    logger.info("sent email to %s: %s", to, subject)


def build_verification_email(to: str, verification_link: str) -> tuple[str, str]:
    subject = "Verify your email"
    text = f"Click this link to verify your email:\n\n{verification_link}\n"
    return subject, text


def send_verification_email(to: str, verification_link: str) -> None:
    subject, text = build_verification_email(to, verification_link)
    send_email(to, subject, text)


def build_password_reset_email(to: str, reset_link: str) -> tuple[str, str]:
    subject = "Reset your password"
    text = f"Click this link to reset your password (expires in 1 hour):\n\n{reset_link}\n"
    return subject, text


def send_password_reset_email(to: str, reset_link: str) -> None:
    subject, text = build_password_reset_email(to, reset_link)
    send_email(to, subject, text)
