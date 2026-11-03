"""Общие утилиты: логи, экранирование, форматирование дат и денег."""

from __future__ import annotations

import html
import json
import logging
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

logger = logging.getLogger("mailagent")

MONTHS_RU = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)


def setup_logging(level: str = "info") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="[%(asctime)s] %(levelname)-5s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def escape_html(text) -> str:
    return html.escape(str(text or ""), quote=False)


def truncate(text, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def chunk_text(text, limit: int = 3800) -> list[str]:
    """Разбивает длинное сообщение на части по естественным границам."""
    value = str(text or "")
    if len(value) <= limit:
        return [value]
    parts: list[str] = []
    rest = value
    while len(rest) > limit:
        cut = rest.rfind("\n\n", 0, limit)
        if cut < limit * 0.55:
            cut = rest.rfind("\n", 0, limit)
        if cut < limit * 0.55:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit * 0.55:
            cut = limit
        parts.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        parts.append(rest)
    return parts


def _parse_iso(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _tz(timezone_name: str):
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(timezone_name)
    except Exception:  # pragma: no cover - зависит от системных tzdata
        return timezone.utc


def format_date(value, timezone_name: str = "Europe/Moscow") -> str:
    moment = _parse_iso(value)
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_tz(timezone_name)).strftime("%d.%m.%Y")


def format_datetime(value, timezone_name: str = "Europe/Moscow") -> str:
    moment = _parse_iso(value)
    if moment is None:
        return "—"
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(_tz(timezone_name)).strftime("%d.%m.%Y %H:%M")


def parse_amount(raw) -> float | None:
    if isinstance(raw, (int, float)):
        return float(raw) if raw == raw else None
    text = str(raw or "").lower().replace("\u00a0", " ").strip()
    if not text:
        return None
    multiplier = 1
    if re.search(r"млн|million", text):
        multiplier = 1_000_000
    elif re.search(r"млрд|billion", text):
        multiplier = 1_000_000_000
    elif re.search(r"тыс|thousand", text):
        multiplier = 1_000
    text = re.sub(r"(руб\.?|рублей|рубля|₽|rub|р\.|млн|млрд|тыс\.?|thousand|million|billion)", " ", text)
    text = re.sub(r"[^\d.,\-]", "", text).strip()
    if not text:
        return None
    last_comma = text.rfind(",")
    last_dot = text.rfind(".")
    if last_comma >= 0 and last_dot >= 0:
        if last_comma > last_dot:
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif last_comma >= 0:
        decimals = len(text) - last_comma - 1
        text = text.replace(",", "") if decimals == 3 and "." not in text else text.replace(",", ".")
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return round(float(value) * multiplier, 2)


def format_money(amount, currency: str = "RUB") -> str:
    if amount is None:
        return "—"
    try:
        number = float(amount)
    except (TypeError, ValueError):
        return "—"
    symbol = {"RUB": "₽", "USD": "$", "EUR": "€", "KZT": "₸", "BYN": "Br"}.get(currency or "RUB", currency or "")
    formatted = f"{number:,.2f}".replace(",", " ").replace(".", ",")
    if formatted.endswith(",00"):
        formatted = formatted[:-3]
    return f"{formatted} {symbol}".strip()


def parse_json_loose(text):
    """Терпимый разбор JSON: снимает ```-обёртки, ищет первую {...}."""
    if isinstance(text, (dict, list)):
        return text
    value = str(text or "").strip()
    if not value:
        return None
    value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
    value = re.sub(r"\s*```$", "", value).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass
    start, end = value.find("{"), value.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(value[start : end + 1])
        except json.JSONDecodeError:
            return None
    return None


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    mod10, mod100 = n % 10, n % 100
    if mod10 == 1 and mod100 != 11:
        return one
    if 2 <= mod10 <= 4 and (mod100 < 10 or mod100 >= 20):
        return few
    return many
