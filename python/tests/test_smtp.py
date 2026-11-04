import smtplib

import pytest

from app import smtp_client
from app.config import create_config
from app.smtp_client import SendError, build_message, extract_addresses, send_email, validate_addresses


def make_cfg(tmp_path):
    return create_config(
        env_file=tmp_path / "none.env",
        overrides={
            "MAIL_USER": "me@ya.ru",
            "MAIL_PASSWORD": "secret",
            "MAIL_SMTP_HOST": "smtp.test",
            "MAIL_SMTP_PORT": "465",
            "SENDER_NAME": "Дмитрий",
        },
    )


def test_extract_and_validate_addresses():
    assert extract_addresses("напиши на Ivan@Romashka.ru и petrov@x.ru") == ["ivan@romashka.ru", "petrov@x.ru"]
    assert validate_addresses(["a@b.ru"]) == ["a@b.ru"]
    with pytest.raises(SendError):
        validate_addresses(["не адрес"])
    with pytest.raises(SendError):
        validate_addresses([f"u{i}@b.ru" for i in range(6)])


def test_build_message_has_headers_and_alternatives(tmp_path):
    cfg = make_cfg(tmp_path)
    message = build_message(cfg, ["ivan@romashka.ru"], "Договор", "**Готово**, ждите счёт.")
    assert message["From"] == "Дмитрий <me@ya.ru>"
    assert message["To"] == "ivan@romashka.ru"
    assert message["Subject"] == "Договор"
    plain = message.get_body(preferencelist=("plain",)).get_content()
    html = message.get_body(preferencelist=("html",)).get_content()
    assert "Готово" in plain
    assert "<b>Готово</b>" in html


def test_build_message_adds_threading_headers(tmp_path):
    cfg = make_cfg(tmp_path)
    message = build_message(cfg, ["a@b.ru"], "Re: Счёт", "Спасибо", in_reply_to="<abc@mail>")
    assert message["In-Reply-To"] == "<abc@mail>"
    assert message["References"] == "<abc@mail>"


def test_send_email_uses_smtp_and_saves_copy(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    captured = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            captured["host"] = host
            captured["port"] = port
            self.messages = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def login(self, user, password):
            captured["login"] = (user, password)

        def send_message(self, message):
            captured["message"] = message

    monkeypatch.setattr(smtp_client.smtplib, "SMTP_SSL", FakeSMTP)
    monkeypatch.setattr(smtp_client, "_append_to_sent", lambda cfg, message: True)

    result = send_email(cfg, ["ivan@romashka.ru"], "Тема", "Текст письма")

    assert captured["host"] == "smtp.test"
    assert captured["login"] == ("me@ya.ru", "secret")
    assert captured["message"]["To"] == "ivan@romashka.ru"
    assert result["saved_to_sent"] is True
    assert result["subject"] == "Тема"


def test_send_email_auth_error_is_explained(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)

    class FailingSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad auth")

    monkeypatch.setattr(smtp_client.smtplib, "SMTP_SSL", FailingSMTP)

    with pytest.raises(SendError) as error:
        send_email(cfg, ["a@b.ru"], "Тема", "Текст")
    assert "пароль приложения" in str(error.value).lower()
