"""Отправка писем через SMTP с копией в «Отправленные».

Отправка всегда инициируется человеком: бот только готовит черновик и ждёт подтверждения.
"""

from __future__ import annotations

import imaplib
import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

from .telegram_format import to_telegram_html, to_telegram_plain
from .util import logger

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
MAX_RECIPIENTS = 5


class SendError(Exception):
    pass


def extract_addresses(text: str) -> list[str]:
    found: list[str] = []
    for match in EMAIL_RE.finditer(str(text or "")):
        address = match.group(0).lower()
        if address not in found:
            found.append(address)
    return found


def validate_addresses(addresses: list[str]) -> list[str]:
    valid = []
    for address in addresses:
        address = address.strip().lower()
        if EMAIL_RE.fullmatch(address) and address not in valid:
            valid.append(address)
    if not valid:
        raise SendError("Не нашёл корректный адрес получателя.")
    if len(valid) > MAX_RECIPIENTS:
        raise SendError(f"Слишком много получателей: {len(valid)} (максимум {MAX_RECIPIENTS}).")
    return valid


def build_message(cfg, to: list[str], subject: str, body: str, in_reply_to: str | None = None, references: str | None = None) -> EmailMessage:
    body = str(body or "").strip()
    if not body:
        raise SendError("Пустое тело письма.")

    message = EmailMessage()
    sender_name = getattr(cfg.mail, "sender_name", "") or ""
    message["From"] = f"{sender_name} <{cfg.mail.user}>" if sender_name else cfg.mail.user
    message["To"] = ", ".join(to)
    message["Subject"] = (subject or "(без темы)").strip()
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=cfg.mail.user.split("@")[-1] or "localhost")
    if in_reply_to:
        message["In-Reply-To"] = in_reply_to
        message["References"] = references or in_reply_to

    plain = to_telegram_plain(body)
    html_body = (
        '<div style="font-family: Arial, Helvetica, sans-serif; font-size: 15px; line-height: 1.5;">'
        f"{to_telegram_html(body)}</div>"
    )
    message.set_content(plain)
    message.add_alternative(html_body, subtype="html")
    return message


def _append_to_sent(cfg, message: EmailMessage) -> bool:
    """Кладёт копию в «Отправленные» (best-effort: письмо уже ушло)."""
    try:
        from .mail_imap import connect, list_folders

        client = connect(cfg)
        try:
            folders = list_folders(client)
            sent = next((f for f in folders if "\\sent" in " ".join(f["flags"]).lower()), None)
            if sent is None:
                sent = next((f for f in folders if f["name"].lower() in ("отправленные", "sent")), None)
            if sent is None:
                return False
            raw = message.as_bytes()
            client.append(sent["raw"] or sent["name"], "\\Seen", imaplib.Time2Internaldate(__import__("time").time()), raw)
            return True
        finally:
            try:
                client.logout()
            except Exception:  # noqa: BLE001
                pass
    except Exception as error:  # noqa: BLE001 - копия не критична
        logger.warning("Не удалось сохранить копию в «Отправленные»: %s", error)
        return False


def send_email(
    cfg,
    to: list[str],
    subject: str,
    body: str,
    *,
    in_reply_to: str | None = None,
    references: str | None = None,
) -> dict:
    """Синхронная отправка; вызывать через asyncio.to_thread."""
    recipients = validate_addresses(to)
    message = build_message(cfg, recipients, subject, body, in_reply_to=in_reply_to, references=references)

    try:
        context = ssl.create_default_context()
        with smtplib.SMTP_SSL(cfg.mail.smtp_host, cfg.mail.smtp_port, timeout=30, context=context) as smtp:
            smtp.ehlo()
            smtp.login(cfg.mail.user, cfg.mail.password)
            smtp.send_message(message)
    except smtplib.SMTPAuthenticationError as error:
        raise SendError(
            "SMTP отклонил логин. Для Яндекса нужен пароль приложения "
            "(id.yandex.ru → Безопасность → Пароли приложений)."
        ) from error
    except (smtplib.SMTPException, OSError) as error:
        raise SendError(f"Не удалось отправить письмо: {error}") from error

    saved = _append_to_sent(cfg, message)
    return {
        "to": recipients,
        "subject": message["Subject"],
        "message_id": message["Message-ID"],
        "saved_to_sent": saved,
    }


__all__ = [
    "EMAIL_RE",
    "MAX_RECIPIENTS",
    "SendError",
    "build_message",
    "extract_addresses",
    "send_email",
    "validate_addresses",
]
