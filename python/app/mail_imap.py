"""Чтение почты Mail.ru по IMAP (stdlib imaplib) с инкрементальной синхронизацией."""

from __future__ import annotations

import asyncio
import base64
import email
import imaplib
import re
import socket
import ssl
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime, parseaddr

from .patterns import detect_bulk
from .util import now_iso, truncate

MONTHS_ABBR = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


class MailError(Exception):
    pass


def explain_mail_error(error: Exception) -> str:
    message = str(error)
    if re.search(r"AUTHENTICATIONFAILED|Invalid credentials|LOGIN failed|535", message, re.IGNORECASE):
        return (
            "Не удалось войти в почту (неверный логин или пароль).\n"
            "Нужен ПАРОЛЬ ПРИЛОЖЕНИЯ, а не обычный пароль от почты:\n"
            "• Mail.ru: Настройки → Безопасность → Пароли для внешних приложений.\n"
            "• Яндекс: id.yandex.ru → Безопасность → Пароли приложений "
            "(и включите IMAP: Настройки → Почтовые программы).\n"
            "Проверьте, что MAIL_USER — полный адрес, а MAIL_PASSWORD — этот пароль."
        )
    if re.search(r"ENOTFOUND|getaddrinfo|Name or service not known", message, re.IGNORECASE):
        return "Не удалось подключиться к серверу почты — проверьте интернет и MAIL_IMAP_HOST."
    if re.search(r"timed out|timeout", message, re.IGNORECASE):
        return "Сервер почты не ответил вовремя — попробуйте позже или проверьте файрвол."
    return message


# ── modified UTF-7 (RFC 3501) ────────────────────────────────────────────
def imap_utf7_decode(value: str) -> str:
    result: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        char = value[index]
        if char != "&":
            result.append(char)
            index += 1
            continue
        end = value.find("-", index)
        if end == -1:
            result.append(value[index:])
            break
        chunk = value[index + 1 : end]
        if not chunk:
            result.append("&")
        else:
            try:
                raw = base64.b64decode(chunk.replace(",", "/") + "===")
                result.append(raw.decode("utf-16-be", errors="replace"))
            except Exception:
                result.append(value[index : end + 1])
        index = end + 1
    return "".join(result)


def imap_utf7_encode(value: str) -> str:
    result: list[str] = []
    pending: list[str] = []

    def flush() -> None:
        if not pending:
            return
        raw = "".join(pending).encode("utf-16-be")
        encoded = base64.b64encode(raw).decode("ascii").rstrip("=").replace("/", ",")
        result.append(f"&{encoded}-")
        pending.clear()

    for char in value:
        if 0x20 <= ord(char) <= 0x7E:
            flush()
            result.append("&-" if char == "&" else char)
        else:
            pending.append(char)
    flush()
    return "".join(result)


def _quote_mailbox(name: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9._\-]+", name):
        return name
    escaped = name.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


# ── MIME ─────────────────────────────────────────────────────────────────
def decode_mime_header(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def html_to_text(html: str) -> str:
    text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&laquo;", "«")
        .replace("&raquo;", "»")
        .replace("&mdash;", "—")
        .replace("&ndash;", "–")
    )
    text = re.sub(r"\r", "", text)
    text = re.sub(r"[ \t\u00a0]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _decode_part(part) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def extract_body(message) -> str:
    plain: list[str] = []
    html: list[str] = []
    for part in message.walk():
        if part.is_multipart():
            continue
        disposition = str(part.get("Content-Disposition") or "").lower()
        if "attachment" in disposition:
            continue
        content_type = part.get_content_type()
        if content_type == "text/plain":
            plain.append(_decode_part(part))
        elif content_type == "text/html":
            html.append(_decode_part(part))
    if plain and "".join(plain).strip():
        return "\n".join(plain)
    if html:
        return html_to_text("\n".join(html))
    return ""


def extract_attachments(message) -> list[dict]:
    attachments = []
    for part in message.walk():
        if part.is_multipart():
            continue
        filename = part.get_filename()
        disposition = str(part.get("Content-Disposition") or "").lower()
        if not filename and "attachment" not in disposition:
            continue
        attachments.append(
            {
                "filename": decode_mime_header(filename) if filename else None,
                "contentType": part.get_content_type(),
                "size": len(part.get_payload(decode=True) or b""),
            }
        )
    return attachments


def thread_key(subject: str | None, message_id: str | None) -> str | None:
    base = re.sub(r"^\s*((re|fw|fwd|отв|пересл)\s*:\s*)+", "", str(subject or ""), flags=re.IGNORECASE)
    base = re.sub(r"[^\w]+", " ", base, flags=re.UNICODE).strip().lower()
    return base or message_id


def parse_message(raw: bytes) -> dict:
    message = email.message_from_bytes(raw)
    from_name, from_addr = parseaddr(decode_mime_header(message.get("From")))

    sent_at = None
    if message.get("Date"):
        try:
            sent_at = parsedate_to_datetime(message.get("Date"))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            sent_at = None

    to_value = decode_mime_header(message.get("To"))
    cc_value = decode_mime_header(message.get("Cc"))
    body = extract_body(message)
    subject = decode_mime_header(message.get("Subject")) or "(без темы)"
    headers = {key.lower(): value for key, value in message.items()}

    return {
        "message_id": message.get("Message-ID"),
        "in_reply_to": message.get("In-Reply-To"),
        "thread_key": thread_key(subject, message.get("Message-ID")),
        "from_name": from_name or None,
        "from_addr": (from_addr or "").lower() or None,
        "to_addrs": to_value or None,
        "cc_addrs": cc_value or None,
        "subject": subject,
        "sent_at": sent_at,
        "body_text": body,
        "attachments": extract_attachments(message),
        "is_bulk": detect_bulk(headers, from_addr, subject),
    }


# ── IMAP ─────────────────────────────────────────────────────────────────
def connect(cfg) -> imaplib.IMAP4_SSL:
    context = ssl.create_default_context()
    client = imaplib.IMAP4_SSL(
        cfg.mail.imap_host,
        cfg.mail.imap_port,
        ssl_context=context,
        timeout=60,
    )
    try:
        client.login(cfg.mail.user, cfg.mail.password)
    except imaplib.IMAP4.error as error:
        raise MailError(explain_mail_error(error)) from error
    return client


def list_folders(client: imaplib.IMAP4_SSL) -> list[dict]:
    status, data = client.list()
    folders: list[dict] = []
    if status != "OK":
        return folders
    pattern = re.compile(rb'\((?P<flags>[^)]*)\)\s+"(?P<delimiter>[^"]*)"\s+(?P<name>.+)$')
    for line in data or []:
        if not isinstance(line, bytes):
            continue
        match = pattern.match(line)
        if not match:
            continue
        raw_name = match.group("name").decode("utf-8", errors="replace").strip()
        if raw_name.startswith('"') and raw_name.endswith('"'):
            raw_name = raw_name[1:-1]
        name = imap_utf7_decode(raw_name)
        folders.append(
            {
                "name": name,
                "flags": match.group("flags").decode("utf-8", errors="replace").split(),
                "raw": raw_name,
            }
        )
    return folders


def resolve_folders(client, cfg) -> list[dict]:
    available = list_folders(client)
    by_name = {folder["name"].lower(): folder for folder in available}
    selected: list[dict] = []

    for wanted in cfg.mail.folders:
        found = by_name.get(wanted.lower())
        if found:
            if found not in selected:
                selected.append(found)
        else:
            selected.append({"name": wanted, "flags": [], "raw": wanted})

    if cfg.mail.include_sent and not any("\\sent" in " ".join(f["flags"]).lower() for f in selected):
        sent = next((f for f in available if "\\sent" in " ".join(f["flags"]).lower()), None)
        if sent is None:
            sent = next((f for f in available if re.match(r"^(отправленные|sent|sent items|sent messages)$", f["name"], re.IGNORECASE)), None)
        if sent and sent not in selected:
            selected.append(sent)

    return selected


def _search_uids(client, last_uid: int, days: int) -> list[int]:
    if last_uid > 0:
        status, data = client.uid("SEARCH", None, "UID", f"{last_uid + 1}:*")
    else:
        since = datetime.now(timezone.utc) - timedelta(days=days)
        date_text = f"{since.day:02d}-{MONTHS_ABBR[since.month - 1]}-{since.year}"
        status, data = client.uid("SEARCH", None, "SINCE", date_text)

    if status != "OK" or not data:
        return []
    raw = data[0]
    if isinstance(raw, bytes):
        raw = raw.decode("ascii", errors="ignore")
    return [int(part) for part in str(raw or "").split() if part.isdigit()]


def fetch_new_emails(cfg, store, max_messages: int | None = None) -> dict:
    """Синхронная загрузка новых писем; вызывать через asyncio.to_thread."""
    summary = {"fetched": 0, "duplicates": 0, "folders": [], "errors": []}
    account = cfg.mail.user
    client = connect(cfg)

    try:
        folders = resolve_folders(client, cfg)
        for folder in folders:
            name = folder["name"]
            try:
                status, _ = client.select(_quote_mailbox(folder["raw"] if folder["raw"] else name), readonly=True)
                if status != "OK":
                    summary["errors"].append(f"Папка «{name}»: не удалось открыть")
                    continue
            except imaplib.IMAP4.error as error:
                summary["errors"].append(f"Папка «{name}»: {error}")
                continue

            try:
                uidvalidity = client.response("UIDVALIDITY")[1]
                uidvalidity = uidvalidity[0].decode("ascii", errors="ignore") if uidvalidity else ""
            except (imaplib.IMAP4.error, IndexError, AttributeError):
                uidvalidity = ""

            state = store.get_sync_state(account, name)
            reset = not state or str(state.get("uidvalidity") or "") != uidvalidity
            last_uid = 0 if reset else int(state.get("last_uid") or 0)

            try:
                uids = _search_uids(client, last_uid, cfg.sync.days)
            except imaplib.IMAP4.error as error:
                summary["errors"].append(f"Папка «{name}»: {error}")
                continue

            uids = sorted(uid for uid in uids if uid > last_uid)
            limit = max_messages or cfg.sync.limit
            if len(uids) > limit:
                uids = uids[-limit:]

            max_uid = last_uid
            batch_size = max(1, cfg.sync.batch_size)

            for start in range(0, len(uids), batch_size):
                batch = uids[start : start + batch_size]
                try:
                    status, data = client.uid("FETCH", ",".join(str(uid) for uid in batch), "(UID RFC822)")
                except imaplib.IMAP4.error as error:
                    summary["errors"].append(f"Папка «{name}»: {error}")
                    break
                if status != "OK":
                    continue

                for item in data or []:
                    if not isinstance(item, tuple):
                        continue
                    meta, raw = item[0], item[1]
                    match = re.search(rb"UID (\d+)", meta or b"")
                    if not match:
                        continue
                    uid = int(match.group(1))
                    try:
                        parsed = parse_message(raw)
                        body = truncate(parsed["body_text"].replace("\r\n", "\n"), cfg.sync.max_body_chars)
                        row = {
                            "account": account,
                            "folder": name,
                            "uid": uid,
                            "message_id": parsed["message_id"],
                            "in_reply_to": parsed["in_reply_to"],
                            "thread_key": parsed["thread_key"],
                            "from_name": parsed["from_name"],
                            "from_addr": parsed["from_addr"],
                            "to_addrs": parsed["to_addrs"],
                            "cc_addrs": parsed["cc_addrs"],
                            "subject": parsed["subject"],
                            "sent_at": (parsed["sent_at"] or datetime.now(timezone.utc)).isoformat(),
                            "body_text": body,
                            "snippet": truncate(re.sub(r"\s+", " ", body), 240),
                            "attachments": parsed["attachments"],
                            "is_bulk": 1 if parsed["is_bulk"] else 0,
                            "fetched_at": now_iso(),
                        }
                        _, inserted = store.insert_email(row)
                        max_uid = max(max_uid, uid)
                        summary["fetched" if inserted else "duplicates"] += 1
                    except Exception as error:  # noqa: BLE001 - одно плохое письмо не рвёт синхронизацию
                        summary["errors"].append(f"Письмо UID {uid}: {error}")

            store.set_sync_state(account, name, uidvalidity=uidvalidity, last_uid=max_uid)
            summary["folders"].append(name)
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass

    return summary


def test_mail_connection(cfg) -> dict:
    started = datetime.now(timezone.utc)
    client = connect(cfg)
    try:
        folders = resolve_folders(client, cfg)
        return {
            "ok": True,
            "ms": int((datetime.now(timezone.utc) - started).total_seconds() * 1000),
            "mailboxes": len(folders),
            "folders": [folder["name"] for folder in folders],
        }
    finally:
        try:
            client.logout()
        except Exception:  # noqa: BLE001
            pass


async def fetch_new_emails_async(cfg, store) -> dict:
    return await asyncio.to_thread(fetch_new_emails, cfg, store)


__all__ = [
    "explain_mail_error",
    "fetch_new_emails",
    "fetch_new_emails_async",
    "html_to_text",
    "imap_utf7_decode",
    "imap_utf7_encode",
    "parse_message",
    "test_mail_connection",
    "MailError",
    "socket",
]
