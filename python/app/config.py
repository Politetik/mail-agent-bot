"""Конфигурация приложения: .env + переменные окружения + переопределения."""

from __future__ import annotations

import os
import re
from pathlib import Path
from types import SimpleNamespace

APP_DIR = Path(__file__).resolve().parent
PY_DIR = APP_DIR.parent
BASE_DIR = PY_DIR.parent  # mail-agent-bot/

LLM_PRESETS = {
    "deepseek": {"base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat"},
    "openai": {"base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini"},
    "openrouter": {"base_url": "https://openrouter.ai/api/v1", "model": "deepseek/deepseek-chat"},
    "ollama": {"base_url": "http://127.0.0.1:11434/v1", "model": "qwen2.5:7b"},
    "lmstudio": {"base_url": "http://127.0.0.1:1234/v1", "model": "local-model"},
}

_ENV_LINE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_env_file(path: Path) -> dict[str, str]:
    """Минимальный парсер .env: KEY=VALUE, комментарии, кавычки."""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_LINE.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _str(value, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _int(value, default: int) -> int:
    text = _str(value, "")
    if text == "":
        return default
    try:
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _bool(value, default: bool) -> bool:
    text = _str(value, "").lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "y", "on", "да"}


def parse_id_list(value) -> list[int]:
    if isinstance(value, (list, tuple, set)):
        return [int(v) for v in value if str(v).lstrip("-").isdigit()]
    text = _str(value, "")
    if not text:
        return []
    out: list[int] = []
    for chunk in re.split(r"[\s,;]+", text):
        if chunk and chunk.lstrip("-").isdigit():
            out.append(int(chunk))
    return out


def find_env_file(explicit: str | Path | None = None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    for candidate in (PY_DIR / ".env", BASE_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return PY_DIR / ".env"


def create_config(env_file: str | Path | None = None, overrides: dict | None = None) -> SimpleNamespace:
    path = find_env_file(env_file)
    values: dict[str, str] = {}
    values.update(load_env_file(path))
    values.update({k: str(v) for k, v in os.environ.items()})
    if overrides:
        values.update({k: str(v) for k, v in overrides.items()})

    provider = _str(values.get("LLM_PROVIDER"), "deepseek").lower()
    preset = LLM_PRESETS.get(provider, LLM_PRESETS["deepseek"])

    llm_base = _str(values.get("LLM_BASE_URL"), preset["base_url"])
    llm_key = _str(values.get("LLM_API_KEY"), "") or _str(values.get("DEEPSEEK_API_KEY"), "") or _str(
        values.get("OPENAI_API_KEY"), ""
    )

    stt_base = _str(values.get("STT_BASE_URL"), _str(values.get("OPENAI_BASE_URL"), "https://api.openai.com/v1"))
    stt_key = _str(values.get("STT_API_KEY"), "") or _str(values.get("OPENAI_API_KEY"), "")

    folders = [f.strip() for f in re.split(r"[,;]", _str(values.get("MAIL_FOLDERS"), "INBOX")) if f.strip()]

    db_path = Path(_str(values.get("DB_PATH"), str(BASE_DIR / "data" / "mail-agent.db")))
    if not db_path.is_absolute():
        db_path = (BASE_DIR / db_path).resolve()

    cfg = SimpleNamespace(
        base_dir=BASE_DIR,
        env_file=path,
        telegram=SimpleNamespace(
            token=_str(values.get("TELEGRAM_BOT_TOKEN"), ""),
            allowed_chat_ids=parse_id_list(values.get("ALLOWED_CHAT_IDS")),
        ),
        mail=SimpleNamespace(
            imap_host=_str(values.get("MAIL_IMAP_HOST"), "imap.mail.ru"),
            imap_port=_int(values.get("MAIL_IMAP_PORT"), 993),
            smtp_host=_str(values.get("MAIL_SMTP_HOST"), "smtp.mail.ru"),
            smtp_port=_int(values.get("MAIL_SMTP_PORT"), 465),
            user=_str(values.get("MAIL_USER"), ""),
            password=_str(values.get("MAIL_PASSWORD"), ""),
            sender_name=_str(values.get("SENDER_NAME"), ""),
            send_enabled=_bool(values.get("EMAIL_SEND_ENABLED"), True),
            send_limit_hour=_int(values.get("EMAIL_SEND_LIMIT_HOUR"), 10),
            folders=folders,
            include_sent=_bool(values.get("MAIL_INCLUDE_SENT"), False),
        ),
        llm=SimpleNamespace(
            provider=provider,
            base_url=llm_base.rstrip("/"),
            api_key=llm_key,
            model=_str(values.get("LLM_MODEL"), preset["model"]),
            max_tokens=_int(values.get("LLM_MAX_TOKENS"), 1200),
            temperature=float(_str(values.get("LLM_TEMPERATURE"), "0.2") or 0.2),
            timeout_ms=_int(values.get("LLM_TIMEOUT_MS"), 120000),
            enabled=_bool(values.get("LLM_ENABLED"), True),
        ),
        stt=SimpleNamespace(
            enabled=_bool(values.get("STT_ENABLED"), True),
            provider=_str(values.get("STT_PROVIDER"), "local"),
            base_url=stt_base.rstrip("/"),
            api_key=stt_key,
            model=_str(values.get("STT_MODEL"), "small"),
            language=_str(values.get("STT_LANGUAGE"), "ru"),
            device=_str(values.get("STT_DEVICE"), "auto"),
            compute_type=_str(values.get("STT_COMPUTE_TYPE"), "auto"),
            beam_size=_int(values.get("STT_BEAM_SIZE"), 5),
            cache_dir=str(Path(_str(values.get("STT_CACHE_DIR"), str(BASE_DIR / "data" / "models"))).resolve()),
            prompt=_str(
                values.get("STT_PROMPT"),
                "Договор, счёт, ИНН, накладная, предоплата, поставка, обязательство, Ромашка, Соловьёв.",
            ),
            timeout_ms=_int(values.get("STT_TIMEOUT_MS"), 120000),
        ),
        db=SimpleNamespace(path=str(db_path)),
        sync=SimpleNamespace(
            days=_int(values.get("SYNC_DAYS"), 30),
            limit=_int(values.get("SYNC_LIMIT"), 300),
            batch_size=_int(values.get("SYNC_BATCH"), 20),
            interval_min=_int(values.get("AUTO_SYNC_MIN"), 0),
            analyze_limit=_int(values.get("ANALYZE_LIMIT"), 60),
            skip_bulk=_bool(values.get("SKIP_BULK"), True),
            max_body_chars=_int(values.get("MAX_BODY_CHARS"), 20000),
        ),
        agent=SimpleNamespace(
            max_context_emails=_int(values.get("CONTEXT_EMAILS"), 8),
            max_context_chars=_int(values.get("CONTEXT_CHARS"), 14000),
            timezone=_str(values.get("TIMEZONE"), "Europe/Moscow"),
            language=_str(values.get("AGENT_LANGUAGE"), "ru"),
            follow_up_days=_int(values.get("FOLLOW_UP_DAYS"), 3),
        ),
        log_level=_str(values.get("LOG_LEVEL"), "info").lower(),
    )

    local_llm = bool(re.search(r"127\.0\.0\.1|localhost", cfg.llm.base_url))
    cfg.has = SimpleNamespace(
        telegram=bool(cfg.telegram.token),
        mail=bool(cfg.mail.user and cfg.mail.password),
        llm=(cfg.llm.enabled and bool(cfg.llm.api_key)) or (local_llm and cfg.llm.enabled),
        stt=cfg.stt.enabled and (cfg.stt.provider == "local" or bool(cfg.stt.api_key)),
    )
    return cfg


def describe_config(cfg: SimpleNamespace) -> dict[str, str]:
    return {
        "telegram": (
            f"настроен, чатов в белом списке: {len(cfg.telegram.allowed_chat_ids)}"
            if cfg.has.telegram
            else "НЕ настроен"
        ),
        "mail": f"{cfg.mail.user} @ {cfg.mail.imap_host}" if cfg.has.mail else "НЕ настроен",
        "llm": (
            f"{cfg.llm.provider}/{cfg.llm.model} ({cfg.llm.base_url})"
            if cfg.has.llm
            else "НЕ настроен (будут работать правила)"
        ),
        "stt": f"{cfg.stt.model} ({cfg.stt.base_url})" if cfg.has.stt else "НЕ настроен (голос отключён)",
        "db": cfg.db.path,
    }
