import asyncio

import pytest

from app.compose import compose_email, resolve_recipients
from app.config import create_config
from app.db import open_store


class FakeLlm:
    available = True
    model = "fake"

    def __init__(self, payload):
        self.payload = payload
        self.requests = []

    async def chat(self, messages, **kwargs):
        import json

        self.requests.append(messages)
        return {"content": json.dumps(self.payload, ensure_ascii=False), "model": self.model}


def make_cfg(tmp_path):
    return create_config(
        env_file=tmp_path / "none.env",
        overrides={"MAIL_USER": "me@ya.ru", "MAIL_PASSWORD": "x", "SENDER_NAME": "Дмитрий"},
    )


def test_resolve_recipients_prefers_explicit_address(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "c.db")
    try:
        recipients = resolve_recipients(cfg, store, "напиши на ivan@romashka.ru", {"to": ""}, [])
        assert recipients == ["ivan@romashka.ru"]
    finally:
        store.close()


def test_resolve_recipients_by_contact_name(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "c2.db")
    try:
        candidates = [{"name": "Иван Петров", "email": "ivan@romashka.ru", "company": "ООО «Ромашка»"}]
        recipients = resolve_recipients(cfg, store, "напиши Ивану Петрову", {"to": "Иван Петров"}, candidates)
        assert recipients == ["ivan@romashka.ru"]
    finally:
        store.close()


def test_resolve_recipients_without_address_fails(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "c3.db")
    try:
        with pytest.raises(ValueError):
            resolve_recipients(cfg, store, "напиши письмо", {"to": ""}, [])
    finally:
        store.close()


def test_compose_email_builds_draft_and_includes_context(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "c4.db")
    try:
        llm = FakeLlm({"to": "ivan@romashka.ru", "subject": "Договор", "body": "Иван, добрый день!\n\nДоговор подписан."})
        reply_to = {
            "id": 1,
            "from_name": "Иван Петров",
            "from_addr": "ivan@romashka.ru",
            "subject": "Договор № 45/2025",
            "body_text": "Пришлите подписанный договор.",
        }
        draft = asyncio.run(compose_email(llm, cfg, store, "ответь, что подписали", reply_to=reply_to))

        assert draft["to"] == ["ivan@romashka.ru"]
        assert draft["subject"] == "Договор"
        assert "подписан" in draft["body"]

        prompt = "\n".join(str(message["content"]) for message in llm.requests[0])
        assert "Договор № 45/2025" in prompt
        assert "EMAIL" in prompt, "контекст письма должен быть в границах данных"
    finally:
        store.close()


def test_compose_email_requires_llm(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "c5.db")
    try:
        with pytest.raises(ValueError):
            asyncio.run(compose_email(None, cfg, store, "напиши письмо"))
    finally:
        store.close()
