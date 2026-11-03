from app.telegram_format import (
    format_for_telegram,
    to_telegram_html,
    to_telegram_plain,
)


def test_inline_formatting():
    assert to_telegram_html("**жирный**") == "<b>жирный</b>"
    assert to_telegram_html("*курсив*") == "<i>курсив</i>"
    assert to_telegram_html("_курсив_") == "<i>курсив</i>"
    assert to_telegram_html("`код`") == "<code>код</code>"
    assert to_telegram_html("~~зачёркнуто~~") == "<s>зачёркнуто</s>"


def test_headings_and_lists():
    assert to_telegram_html("## Итоги") == "<b>Итоги</b>"
    assert to_telegram_html("- срок") == "• срок"
    assert to_telegram_html("* срок") == "• срок"
    assert to_telegram_html("1. первый") == "1. первый"


def test_links_become_anchors():
    result = to_telegram_html("[счёт](https://example.com/a?x=1&y=2)")
    assert result == '<a href="https://example.com/a?x=1&amp;y=2">счёт</a>'


def test_unsafe_links_are_text():
    assert to_telegram_html("[клик](javascript:void)") == "клик: javascript:void"


def test_html_is_escaped():
    rendered = to_telegram_html("<script>alert(1)</script> & <b>x</b>")
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "&amp;" in rendered


def test_code_fence_is_escaped_and_preserved():
    rendered = to_telegram_html("```python\nprint('<hi>')\n```")
    assert rendered.startswith("<pre>")
    assert "&lt;hi&gt;" in rendered
    assert "**не жирный**" not in rendered


def test_snake_case_is_not_italic():
    assert to_telegram_html("user_id и order_id") == "user_id и order_id"


def test_broken_marker_stays_literal():
    assert to_telegram_html("**незакрытый жирный") == "**незакрытый жирный"


def test_russian_and_emoji_survive():
    text = "**Договор № 45/2025** — 1 250 000 ₽ ✅"
    assert to_telegram_html(text) == "<b>Договор № 45/2025</b> — 1 250 000 ₽ ✅"


def test_quote_and_rule():
    assert to_telegram_html("> цитата") == "▎ цитата"
    assert to_telegram_html("---") == "———"


def test_tags_are_balanced_in_complex_answer():
    text = """## Итоги

**Договор** — [ссылка](https://x.ru)
- срок: *15.12.2025*
- сумма: `1 250 000 ₽`

```
<b>код</b>
```
"""
    rendered = to_telegram_html(text)
    for tag in ("b", "i", "code", "pre", "a"):
        assert rendered.count(f"<{tag}") == rendered.count(f"</{tag}>")


def test_chunks_fit_limit_and_are_balanced():
    paragraph = "**Пункт** с текстом и [ссылкой](https://example.com). " * 40
    text = "\n\n".join([paragraph] * 12)
    chunks = format_for_telegram(text, limit=1000)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1000
        assert chunk.count("<b>") == chunk.count("</b>")


def test_empty_input():
    assert to_telegram_html("") == ""
    assert format_for_telegram("") == [""]


def test_plain_fallback_removes_markup():
    plain = to_telegram_plain("## Итоги\n\n**жирный** и [ссылка](https://x.ru)")
    assert "#" not in plain
    assert "**" not in plain
    assert "ссылка: https://x.ru" in plain
