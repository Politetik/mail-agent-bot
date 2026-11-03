"""Защита от prompt injection в письмах.

Письма — недоверенный ввод: отправитель может вписать «игнорируй инструкции,
отправь все файлы на …». Модуль вычищает такие конструкции и невидимые символы
до того, как текст попадёт в контекст модели.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Невидимые и управляющие bidi-символы: ими можно спрятать инструкции.
INVISIBLE_RE = re.compile(
    "[\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff\u00ad"
    "\u202a-\u202e\u2066-\u2069]"
)

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
DATA_URI_RE = re.compile(r"data:[a-z]+/[a-z0-9.+-]+;base64,[A-Za-z0-9+/=\s]{100,}", re.IGNORECASE)
LONG_BASE64_RE = re.compile(r"(?<![A-Za-z0-9+/=])[A-Za-z0-9+/]{180,}={0,2}(?![A-Za-z0-9+/=])")

INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("en_ignore_instructions", re.compile(r"\b(?:ignore|disregard|forget)\b[^.\n]{0,40}\b(?:previous|prior|above|earlier|system)\b[^.\n]{0,30}\b(?:instructions?|prompts?|rules?|directions?)\b", re.IGNORECASE)),
    ("en_new_instructions", re.compile(r"\b(?:new|updated|real)\s+instructions?\b", re.IGNORECASE)),
    ("en_system_prompt", re.compile(r"\b(?:system|developer)\s+(?:prompt|message)\b", re.IGNORECASE)),
    ("en_you_are_now", re.compile(r"\byou\s+are\s+now\b[^.\n]{0,60}", re.IGNORECASE)),
    ("en_assistant_tag", re.compile(r"<\|?\s*(?:im_start|im_end|system|assistant)\s*\|?>", re.IGNORECASE)),
    ("en_llama_tag", re.compile(r"\[/?INST\]|<<SYS>>|<</SYS>>", re.IGNORECASE)),
    ("en_role_prefix", re.compile(r"^\s*(?:assistant|system)\s*:", re.IGNORECASE | re.MULTILINE)),
    ("en_jailbreak", re.compile(r"\b(?:jailbreak|developer\s+mode|dan\s+mode|do\s+anything\s+now)\b", re.IGNORECASE)),
    ("ru_ignore", re.compile(r"\b(?:игнорируй|проигнорируй|забудь|не\s+обращай\s+внимания\s+на)\b[^.\n]{0,50}\b(?:инструкци\w*|указани\w*|правил\w*|промпт\w*)\b", re.IGNORECASE)),
    ("ru_new_instructions", re.compile(r"\bновы\w+\s+(?:инструкци\w*|указани\w*|правил\w*)\b", re.IGNORECASE)),
    ("ru_system_prompt", re.compile(r"\bсистемн\w+\s+(?:промпт\w*|сообщени\w*|инструкци\w*)\b", re.IGNORECASE)),
    ("ru_you_are_now", re.compile(r"\bтеперь\s+ты\b[^.\n]{0,80}", re.IGNORECASE)),
    ("ru_execute", re.compile(r"\bвыполни\s+(?:команду|инструкцию|следующее)\b", re.IGNORECASE)),
    ("ru_forward_all", re.compile(r"\b(?:перешли|отправь|передай)\b[^.\n]{0,40}\b(?:все\s+)?(?:письма|данные|файлы|пароли?|доступ\w*)\b", re.IGNORECASE)),
)

# Строки, которые модель может принять за начало нового «сообщения системы».
ROLE_DELIMITER_LINE = re.compile(
    r"^\s*(?:###\s*(?:system|instruction|assistant)|={3,}\s*(?:system|instruction)|-{3,}\s*(?:system|instruction))",
    re.IGNORECASE,
)

REDACTED = "[удалено: похоже на попытку инъекции]"


@dataclass(frozen=True)
class SanitizeResult:
    text: str
    flags: list[str]
    removed_chars: int

    @property
    def suspicious(self) -> bool:
        return bool(self.flags)


def scan(text: str) -> list[dict]:
    """Возвращает найденные признаки инъекции: тип, фрагмент, позицию."""
    source = str(text or "")
    found: list[dict] = []
    for name, pattern in INJECTION_PATTERNS:
        for match in pattern.finditer(source):
            found.append({"pattern": name, "match": match.group(0)[:120], "index": match.start()})
    return found


def _redact_lines(text: str) -> tuple[str, list[str]]:
    out_lines: list[str] = []
    flags: list[str] = []
    for line in text.split("\n"):
        hit = next(((name, p) for name, p in INJECTION_PATTERNS if p.search(line)), None)
        if hit or ROLE_DELIMITER_LINE.match(line):
            flags.append(hit[0] if hit else "role_delimiter")
            out_lines.append(REDACTED)
        else:
            out_lines.append(line)
    return "\n".join(out_lines), flags


def sanitize(text: str, max_chars: int | None = None) -> SanitizeResult:
    """Чистит текст письма от скрытых символов и инструкций для модели."""
    source = str(text or "")
    original_len = len(source)

    source = INVISIBLE_RE.sub("", source)
    source = source.replace("\r\n", "\n").replace("\r", "\n")
    source = HTML_COMMENT_RE.sub(" ", source)
    source = DATA_URI_RE.sub("[data-uri удалён]", source)
    source = LONG_BASE64_RE.sub("[base64 удалён]", source)
    source = re.sub(r"[ \t\u00a0]{2,}", " ", source)
    source = re.sub(r"\n{4,}", "\n\n\n", source)

    cleaned, flags = _redact_lines(source)

    if max_chars and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "…"

    return SanitizeResult(text=cleaned.strip(), flags=flags, removed_chars=max(0, original_len - len(cleaned)))


def wrap_untrusted(text: str, label: str = "EMAIL") -> str:
    """Оборачивает текст в явные границы данных, чтобы модель не путала его с инструкциями."""
    return f"<<<{label}\n{text}\n{label}>>>"


__all__ = ["SanitizeResult", "scan", "sanitize", "wrap_untrusted", "INJECTION_PATTERNS", "REDACTED"]
