"""Распознавание речи через OpenAI-совместимый /audio/transcriptions."""

from __future__ import annotations

import aiohttp


class SttError(Exception):
    pass


async def transcribe(cfg, audio: bytes, filename: str = "voice.ogg", language: str | None = None) -> dict:
    if not cfg.has.stt:
        raise SttError("Распознавание речи не настроено: задайте STT_API_KEY (или OPENAI_API_KEY).")

    base_url = str(cfg.stt.base_url).rstrip("/")
    language = language or cfg.stt.language

    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type="audio/ogg")
    form.add_field("model", cfg.stt.model)
    if language:
        form.add_field("language", language)
    form.add_field("response_format", "json")

    timeout = aiohttp.ClientTimeout(total=cfg.stt.timeout_ms / 1000)
    headers = {"authorization": f"Bearer {cfg.stt.api_key}"}

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{base_url}/audio/transcriptions", data=form, headers=headers) as response:
            raw = await response.text()
            if response.status >= 400:
                raise SttError(f"STT {response.status}: {raw[:400]}")
            try:
                import json

                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise SttError("STT вернул некорректный ответ") from error

    text = str(data.get("text") or "").strip()
    if not text:
        raise SttError("Речь не распознана (пустой ответ STT).")
    return {"text": text, "model": cfg.stt.model, "language": language}
