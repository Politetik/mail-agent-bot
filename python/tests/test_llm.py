import asyncio
import json
from pathlib import Path

from app.answer import answer_question, answer_with_llm
from app.config import create_config
from app.db import open_store
from app.llm import Llm, LlmError
from conftest import seed_store


def make_cfg(tmp_path):
    return create_config(
        env_file=tmp_path / "missing.env",
        overrides={
            "LLM_BASE_URL": "http://127.0.0.1:9/v1",
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "fake-model",
            "DB_PATH": str(tmp_path / "llm.db"),
        },
    )


def test_chat_sends_json_mode_and_parses_content(tmp_path, monkeypatch):
    cfg = make_cfg(tmp_path)
    llm = Llm(cfg)
    captured = {}

    async def fake_request(path, body, attempts=3):
        captured["path"] = path
        captured["body"] = body
        return {"choices": [{"message": {"content": "ok"}}], "model": body["model"]}

    monkeypatch.setattr(llm, "_request", fake_request)

    result = asyncio.run(llm.chat([{"role": "user", "content": "привет"}], json_mode=True))

    assert result["content"] == "ok"
    assert captured["path"] == "/chat/completions"
    assert captured["body"]["response_format"] == {"type": "json_object"}
    assert captured["body"]["model"] == "fake-model"


def test_unavailable_llm_raises(tmp_path):
    cfg = create_config(
        env_file=tmp_path / "missing.env",
        overrides={"DB_PATH": str(tmp_path / "x.db"), "LLM_API_KEY": ""},
    )
    llm = Llm(cfg)
    try:
        asyncio.run(llm.chat([{"role": "user", "content": "hi"}]))
    except LlmError as error:
        assert "не настроен" in str(error)
    else:  # pragma: no cover
        raise AssertionError("ожидалась ошибка")


class FakeLlm:
    available = True
    model = "fake-model"

    def __init__(self, answer="По переписке: договор № 45/2025 ждёт подписания [#1]."):
        self.answer = answer
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return {"content": self.answer, "usage": None, "model": self.model}


def test_answer_question_uses_llm(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "q.db")
    try:
        asyncio.run(seed_store(store))
        result = asyncio.run(answer_question(store, cfg, FakeLlm(), "Что по договору с Ромашкой?"))
        assert result["used_llm"] is True
        assert "45/2025" in result["text"]
        assert "Источники" in result["text"]
        assert result["email_ids"]
    finally:
        store.close()


def test_answer_question_falls_back_when_llm_breaks(tmp_path):
    cfg = make_cfg(tmp_path)
    store = open_store(tmp_path / "q2.db")
    try:
        asyncio.run(seed_store(store))

        class Broken:
            available = True
            model = "broken"

            async def chat(self, messages, **kwargs):
                raise RuntimeError("network down")

        result = asyncio.run(answer_question(store, cfg, Broken(), "Что нового?"))
        assert result["used_llm"] is False
        assert result["text"]
    finally:
        store.close()


def test_contacts_are_answered_without_llm(tmp_path):
    cfg = make_cfg(tmp_path)
    llm = FakeLlm(answer="этого не должно быть в ответе")
    store = open_store(tmp_path / "q3.db")
    try:
        asyncio.run(seed_store(store))
        result = asyncio.run(answer_question(store, cfg, llm, "Какой телефон у Анны Смирновой?"))
        assert result["used_llm"] is False
        assert "+7 (916) 555-77-88" in result["text"]
        assert llm.calls == 0
    finally:
        store.close()
