"""Распознавание речи: локальный faster-whisper (по умолчанию), OpenAI, Яндекс.

Локальный провайдер не требует ключей и VPN: модель скачивается один раз,
аудио не покидает машину.
"""

from __future__ import annotations

import asyncio
import io
import json
import threading

import aiohttp

from .util import logger


class SttError(Exception):
    pass


_MODEL_LOCK = threading.Lock()
_MODELS: dict[tuple[str, str, str], object] = {}


def _resolve_device(cfg) -> tuple[str, str]:
    device = str(cfg.stt.device or "auto").lower()
    compute = str(cfg.stt.compute_type or "auto").lower()

    if device == "auto":
        # CPU — безопасный дефолт: CUDA-колёса требуют cuDNN, которого может не быть.
        device = "cpu"
    if compute == "auto":
        compute = "float16" if device in {"cuda", "gpu"} else "int8"
    return device, compute


def get_local_model(cfg):
    """Лениво загружает и кэширует модель Whisper."""
    import os
    from pathlib import Path

    cache_dir = Path(cfg.stt.cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    # huggingface_hub по умолчанию пишет в ~/.cache — вне рабочей папки это может быть запрещено.
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(cache_dir))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

    from faster_whisper import WhisperModel

    device, compute = _resolve_device(cfg)
    key = (cfg.stt.model, device, compute)
    with _MODEL_LOCK:
        model = _MODELS.get(key)
        if model is None:
            logger.info("Загружаю Whisper «%s» (%s/%s) — первый раз может занять время", cfg.stt.model, device, compute)
            model = WhisperModel(
                cfg.stt.model,
                device=device,
                compute_type=compute,
                download_root=str(cache_dir),
            )
            _MODELS[key] = model
            logger.info("Модель Whisper загружена")
    return model


def decode_audio(audio: bytes, sample_rate: int = 16000):
    """Декодирует OGG/Opus, MP3, WAV в моно float32 (16 кГц) через PyAV.

    faster-whisper 1.2.1 несовместим с PyAV 19 (удалён аргумент metadata_errors),
    поэтому декодируем сами и отдаём модели numpy-массив.
    """
    import av
    import numpy as np

    chunks: list["np.ndarray"] = []
    with av.open(io.BytesIO(audio)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise SttError("В файле нет аудиодорожки.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=sample_rate)
        for frame in container.decode(stream):
            for resampled in resampler.resample(frame):
                chunks.append(resampled.to_ndarray().reshape(-1))
        for resampled in resampler.resample(None):
            chunks.append(resampled.to_ndarray().reshape(-1))

    if not chunks:
        raise SttError("Не удалось декодировать аудио.")

    pcm = np.concatenate(chunks).astype("float32") / 32768.0
    if pcm.size == 0:
        raise SttError("Аудиодорожка пустая.")
    return pcm


def transcribe_local(cfg, audio: bytes, language: str | None = None) -> dict:
    """Синхронное распознавание; вызывать через asyncio.to_thread."""
    model = get_local_model(cfg)
    pcm = decode_audio(audio)
    segments, info = model.transcribe(
        pcm,
        language=language or cfg.stt.language or None,
        beam_size=int(cfg.stt.beam_size or 5),
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 300},
        initial_prompt=cfg.stt.prompt or None,
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text:
        raise SttError("Речь не распознана (пустой результат).")
    return {
        "text": text,
        "provider": "local",
        "model": cfg.stt.model,
        "language": getattr(info, "language", None),
        "language_probability": round(float(getattr(info, "language_probability", 0.0) or 0.0), 3),
        "duration": round(float(pcm.size) / 16000, 2),
    }


async def transcribe_openai(cfg, audio: bytes, filename: str, language: str | None) -> dict:
    base_url = str(cfg.stt.base_url).rstrip("/")
    form = aiohttp.FormData()
    form.add_field("file", audio, filename=filename, content_type="audio/ogg")
    form.add_field("model", cfg.stt.model)
    lang = language or cfg.stt.language
    if lang:
        form.add_field("language", lang)
    form.add_field("response_format", "json")

    timeout = aiohttp.ClientTimeout(total=cfg.stt.timeout_ms / 1000)
    headers = {"authorization": f"Bearer {cfg.stt.api_key}"}
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(f"{base_url}/audio/transcriptions", data=form, headers=headers) as response:
            raw = await response.text()
            if response.status >= 400:
                raise SttError(f"STT {response.status}: {raw[:400]}")
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as error:
                raise SttError("STT вернул некорректный ответ") from error

    text = str(data.get("text") or "").strip()
    if not text:
        raise SttError("Речь не распознана (пустой ответ STT).")
    return {"text": text, "provider": "openai", "model": cfg.stt.model, "language": lang}


async def transcribe(cfg, audio: bytes, filename: str = "voice.ogg", language: str | None = None) -> dict:
    provider = str(cfg.stt.provider or "local").lower()

    if provider == "local":
        if not audio:
            raise SttError("Пустое аудио.")
        return await asyncio.to_thread(transcribe_local, cfg, audio, language)

    if provider == "openai":
        if not cfg.has.stt:
            raise SttError("STT не настроен: задайте STT_API_KEY (или OPENAI_API_KEY).")
        return await transcribe_openai(cfg, audio, filename, language)

    if provider == "yandex":
        raise SttError("Провайдер Yandex SpeechKit пока не реализован — используйте local.")

    raise SttError(f"Неизвестный STT-провайдер: {provider}")
