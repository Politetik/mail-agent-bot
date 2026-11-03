"""Клиент OpenAI-совместимого API (DeepSeek, OpenAI, OpenRouter, Ollama)."""

from __future__ import annotations

import asyncio
import json

import aiohttp

from .util import logger


class LlmError(Exception):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class Llm:
    def __init__(self, cfg):
        self.base_url = str(cfg.llm.base_url).rstrip("/")
        self.api_key = cfg.llm.api_key
        self.model = cfg.llm.model
        self.temperature = cfg.llm.temperature
        self.max_tokens = cfg.llm.max_tokens
        self.timeout = cfg.llm.timeout_ms / 1000
        self.available = bool(cfg.has.llm)
        self._session: aiohttp.ClientSession | None = None

    async def _session_get(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, path: str, body: dict, attempts: int = 3) -> dict:
        if not self.available:
            raise LlmError("LLM не настроен: заполните LLM_API_KEY в .env")

        headers = {"content-type": "application/json"}
        if self.api_key:
            headers["authorization"] = f"Bearer {self.api_key}"

        last_error: Exception | None = None
        session = await self._session_get()
        for attempt in range(attempts):
            try:
                async with session.post(f"{self.base_url}{path}", json=body, headers=headers) as response:
                    raw = await response.text()
                    if response.status == 429 or response.status >= 500:
                        last_error = LlmError(f"LLM {response.status}: {raw[:300]}", response.status)
                        await asyncio.sleep(1.2 * (attempt + 1))
                        continue
                    if response.status >= 400:
                        raise LlmError(f"LLM {response.status}: {raw[:500]}", response.status)
                    return json.loads(raw)
            except LlmError:
                raise
            except (aiohttp.ClientError, asyncio.TimeoutError) as error:
                last_error = error
                if attempt < attempts - 1:
                    await asyncio.sleep(1.2 * (attempt + 1))

        raise LlmError(str(last_error) if last_error else "LLM request failed")

    async def chat(
        self,
        messages: list[dict],
        *,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        json_mode: bool = False,
    ) -> dict:
        body = {
            "model": model or self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
            "stream": False,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        try:
            data = await self._request("/chat/completions", body)
        except LlmError as error:
            # Некоторые провайдеры не поддерживают response_format.
            if json_mode and error.status == 400:
                body.pop("response_format", None)
                data = await self._request("/chat/completions", body, attempts=1)
            else:
                raise

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise LlmError("LLM вернул пустой ответ") from error
        if not isinstance(content, str):
            raise LlmError("LLM вернул пустой ответ")
        return {"content": content, "usage": data.get("usage"), "model": data.get("model", body["model"])}

    async def ping(self) -> str:
        # Лимит с запасом: reasoning-модели тратят токены на размышления.
        result = await self.chat(
            [{"role": "user", "content": "Ответь одним словом: ok"}],
            max_tokens=300,
            temperature=0,
        )
        return result["content"].strip()


def create_llm(cfg) -> Llm:
    logger.debug("LLM инициализирован: %s / %s", cfg.llm.base_url, cfg.llm.model)
    return Llm(cfg)
