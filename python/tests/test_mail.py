from email.message import EmailMessage

from app.mail_imap import (
    html_to_text,
    imap_utf7_decode,
    imap_utf7_encode,
    parse_message,
    thread_key,
)


def test_utf7_roundtrip_for_russian_folders():
    for name in ("Отправленные", "Спам", "Черновики", "INBOX", "A&B"):
        assert imap_utf7_decode(imap_utf7_encode(name)) == name


def test_utf7_known_values():
    assert imap_utf7_decode("INBOX") == "INBOX"
    assert imap_utf7_decode("&-") == "&"
    encoded = imap_utf7_encode("Отправленные")
    assert encoded.startswith("&") and encoded.endswith("-")
    assert imap_utf7_decode(encoded) == "Отправленные"


def test_html_to_text_strips_markup_and_scripts():
    html = "<div>Добрый день!</div><p>Договор № 45/2025</p><br>Сумма&nbsp;1&nbsp;250&nbsp;000 руб.<script>alert(1)</script>"
    text = html_to_text(html)
    assert "Добрый день!" in text
    assert "Договор № 45/2025" in text
    assert "Сумма 1 250 000 руб." in text
    assert "alert" not in text


def test_thread_key_strips_reply_prefixes():
    assert thread_key("Re: Договор поставки № 45/2025", None) == "договор поставки 45 2025"
    assert thread_key("FWD: Отчёт", "<x@y>") == "отчёт"


def test_parse_message_extracts_fields_and_bulk_flag():
    message = EmailMessage()
    message["From"] = "Иван Петров <ivan@romashka.ru>"
    message["To"] = "me@mail.ru"
    message["Subject"] = "Договор поставки № 45/2025"
    message["Date"] = "Mon, 03 Nov 2025 10:30:00 +0300"
    message["Message-ID"] = "<abc@romashka.ru>"
    message["List-Unsubscribe"] = "<mailto:unsub@romashka.ru>"
    message.set_content("Направляем договор. Тел.: +7 (495) 123-45-67.")

    parsed = parse_message(message.as_bytes())

    assert parsed["from_name"] == "Иван Петров"
    assert parsed["from_addr"] == "ivan@romashka.ru"
    assert parsed["subject"] == "Договор поставки № 45/2025"
    assert "+7 (495) 123-45-67" in parsed["body_text"]
    assert parsed["is_bulk"] is True
    assert parsed["thread_key"] == "договор поставки 45 2025"


def test_parse_message_decodes_base64_subject():
    message = EmailMessage()
    message["From"] = "a@b.ru"
    message["Subject"] = "Привет, мир"
    message.set_content("тело")
    raw = message.as_bytes().replace(b"Subject: ", b"Subject: =?utf-8?B?0J/RgNC40LLQtdGCLCDQvNC40YA=?= \n", 1)

    parsed = parse_message(raw)
    assert "Привет" in parsed["subject"]
