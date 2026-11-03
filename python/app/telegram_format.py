"""Рендер Markdown-ответов модели в безопасный Telegram-HTML.

Идея (как в inbox-zero): не пытаться «распарсить Markdown вообще», а
преобразовать безопасное подмножество, всё остальное — экранировать.
Любая странность не ломает доставку: сообщение либо красивое, либо plain.
"""

from __future__ import annotations

import html
import re

TELEGRAM_LIMIT = 3800

FENCE_RE = re.compile(r"```([\w+-]*)[ \t]*\n?(.*?)```", re.DOTALL)
INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
HEADING_RE = re.compile(r"^[ \t]{0,3}#{1,6}[ \t]+(.+?)[ \t]*#*[ \t]*$", re.MULTILINE)
BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+(.+)$", re.MULTILINE)
NUMBERED_RE = re.compile(r"^([ \t]*)(\d+)[.)][ \t]+(.+)$", re.MULTILINE)
QUOTE_RE = re.compile(r"^[ \t]*>[ \t]?(.+)$", re.MULTILINE)
RULE_RE = re.compile(r"^[ \t]*(?:-{3,}|\*{3,}|_{3,})[ \t]*$", re.MULTILINE)
LINK_RE = re.compile(r"\[([^\]\n]{1,200})\]\(([^)\s]{1,500})\)")
BOLD_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*", re.DOTALL)
BOLD_UNDERSCORE_RE = re.compile(r"(?<![\w])__(?=\S)(.+?)(?<=\S)__(?![\w])", re.DOTALL)
STRIKE_RE = re.compile(r"~~(?=\S)(.+?)(?<=\S)~~", re.DOTALL)
ITALIC_STAR_RE = re.compile(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])")
ITALIC_UNDERSCORE_RE = re.compile(r"(?<![\w_])_(?=\S)([^_\n]+?)(?<=\S)_(?![\w_])")

ALLOWED_TAGS = ("b", "i", "s", "code", "pre", "a", "blockquote")
TAG_RE = re.compile(r"</?(b|i|s|code|pre|a|blockquote)(?:\s+href=\"[^\"]*\")?>")
PLACEHOLDER_RE = re.compile("\x00(\\d+)\x00")


def _is_safe_url(url: str) -> bool:
    return bool(re.match(r"^(https?://|tg://|mailto:)", url, re.IGNORECASE))


def _balance_ok(text: str) -> bool:
    """Проверяет, что все наши теги закрыты (страховка перед отправкой)."""
    stack: list[str] = []
    for match in TAG_RE.finditer(text):
        raw = match.group(0)
        name = match.group(1)
        if raw.startswith("</"):
            if not stack or stack[-1] != name:
                return False
            stack.pop()
        else:
            stack.append(name)
    return not stack


def to_telegram_html(text: str) -> str:
    """Markdown → HTML, понятный Telegram. Безопасно для любого ввода."""
    source = str(text or "")
    if not source.strip():
        return ""

    placeholders: dict[str, str] = {}

    def stash(snippet: str) -> str:
        key = f"\x00{len(placeholders)}\x00"
        placeholders[key] = snippet
        return key

    # Код вынимаем до экранирования, чтобы не форматировать его содержимое.
    def fence(match: re.Match) -> str:
        body = html.escape(match.group(2).strip("\n"), quote=False)
        return stash(f"<pre>{body}</pre>")

    source = FENCE_RE.sub(fence, source)

    def inline_code(match: re.Match) -> str:
        return stash(f"<code>{html.escape(match.group(1), quote=False)}</code>")

    source = INLINE_CODE_RE.sub(inline_code, source)

    # «> цитата» превращаем до экранирования: иначе «>» станет «&gt;».
    source = QUOTE_RE.sub(r"▎ \1", source)

    # Всё, что осталось, — данные, а не разметка.
    source = html.escape(source, quote=False)

    def link(match: re.Match) -> str:
        label, url = match.group(1), html.unescape(match.group(2))
        if not _is_safe_url(url):
            return f"{label}: {html.escape(url, quote=False)}"
        return stash(f'<a href="{html.escape(url, quote=True)}">{label}</a>')

    source = LINK_RE.sub(link, source)

    # Строчная разметка.
    source = STRIKE_RE.sub(r"<s>\1</s>", source)
    source = BOLD_RE.sub(r"<b>\1</b>", source)
    source = BOLD_UNDERSCORE_RE.sub(r"<b>\1</b>", source)
    source = ITALIC_STAR_RE.sub(r"<i>\1</i>", source)
    source = ITALIC_UNDERSCORE_RE.sub(r"<i>\1</i>", source)

    # Блочная разметка — построчно.
    lines: list[str] = []
    for line in source.split("\n"):
        heading = HEADING_RE.match(line)
        if heading:
            lines.append(f"<b>{heading.group(1).strip()}</b>")
            continue
        if RULE_RE.match(line):
            lines.append("———")
            continue
        quote = QUOTE_RE.match(line)
        if quote:
            lines.append(f"▎ {quote.group(1)}")
            continue
        bullet = BULLET_RE.match(line)
        if bullet:
            lines.append(f"{bullet.group(1)}• {bullet.group(2)}")
            continue
        lines.append(line)
    source = "\n".join(lines)

    source = re.sub(r"\n{3,}", "\n\n", source).strip()

    for key, snippet in placeholders.items():
        source = source.replace(key, snippet)

    if not _balance_ok(source):
        # Аварийный путь: отдаём максимально похожий plain text.
        return to_telegram_plain(source)
    return source


def to_telegram_plain(text: str) -> str:
    """Fallback: снимаем разметку, оставляем читаемый текст."""
    source = str(text or "")
    source = FENCE_RE.sub(lambda m: m.group(2).strip("\n"), source)
    source = INLINE_CODE_RE.sub(r"\1", source)
    source = LINK_RE.sub(lambda m: f"{m.group(1)}: {html.unescape(m.group(2))}", source)
    source = re.sub(r"^[ \t]{0,3}#{1,6}[ \t]+", "", source, flags=re.MULTILINE)
    source = BULLET_RE.sub(r"\1• \2", source)
    source = QUOTE_RE.sub(r"▎ \1", source)
    source = RULE_RE.sub("———", source)
    source = STRIKE_RE.sub(r"\1", source)
    source = BOLD_RE.sub(r"\1", source)
    source = BOLD_UNDERSCORE_RE.sub(r"\1", source)
    source = ITALIC_STAR_RE.sub(r"\1", source)
    source = ITALIC_UNDERSCORE_RE.sub(r"\1", source)
    source = re.sub(r"\x00\d+\x00", "", source)
    source = re.sub(r"\n{3,}", "\n\n", source)
    return source.strip()


def split_blocks(text: str) -> list[str]:
    """Делит Markdown на блоки по пустым строкам, не разрывая код-фенсы."""
    blocks: list[str] = []
    current: list[str] = []
    in_fence = False
    for line in str(text or "").split("\n"):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            current.append(line)
            continue
        if not in_fence and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _iter_units(block: str, limit: int):
    """Дробит слишком большой блок на строки → предложения → слова."""
    half = max(200, limit // 2)
    for line in block.split("\n"):
        if len(line) <= half:
            yield line
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", line):
            if len(sentence) <= half:
                yield sentence
                continue
            buffer = ""
            for word in sentence.split(" "):
                candidate = f"{buffer} {word}".strip()
                if len(candidate) > half and buffer:
                    yield buffer
                    buffer = word
                else:
                    buffer = candidate
            if buffer:
                yield buffer


def format_for_telegram(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Возвращает готовые HTML-фрагменты ≤ limit, каждый с закрытыми тегами."""
    chunks: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current.strip():
            chunks.append(current.strip())
        current = ""

    for block in split_blocks(text):
        rendered = to_telegram_html(block)
        if not rendered:
            continue

        if len(rendered) <= limit:
            candidate = f"{current}\n\n{rendered}" if current else rendered
            if len(candidate) <= limit:
                current = candidate
            else:
                flush()
                current = rendered
            continue

        # Блок больше лимита — режем на единицы и пакуем по одной.
        flush()
        for unit in _iter_units(block, limit):
            piece = to_telegram_html(unit)
            if len(piece) > limit:
                piece = to_telegram_plain(unit)[:limit]
            candidate = f"{current}\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                flush()
                current = piece

    flush()
    return chunks or [""]


__all__ = [
    "format_for_telegram",
    "split_blocks",
    "to_telegram_html",
    "to_telegram_plain",
    "TELEGRAM_LIMIT",
]
