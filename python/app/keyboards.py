"""Клавиатуры и кнопки бота: постоянная панель + контекстные inline-меню.

Принципы:
- постоянная клавиатура — только 3–4 самых частых действия, без «меню всего»;
- действия над объектом (закрыть обязательство, открыть письмо) — inline, рядом с данными;
- редкое (договоры, статус, помощь) — в «⚙️ Ещё» и в меню команд Telegram.
"""

from __future__ import annotations

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📥 Проверить почту"), KeyboardButton(text="📊 Сводка")],
        [KeyboardButton(text="📌 Что висит"), KeyboardButton(text="📇 Контакты")],
        [KeyboardButton(text="⚙️ Ещё")],
    ],
    resize_keyboard=True,
    is_persistent=True,
    input_field_placeholder="Спросите или отправьте голосовое",
)

# Что делает каждая кнопка постоянной панели.
BUTTON_ROUTES = {
    "📥 Проверить почту": "/sync",
    "📊 Сводка": "/digest",
    "📌 Что висит": "/tasks",
    "📇 Контакты": "/contacts",
    "⚙️ Ещё": "/more",
}


def _button(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def hub_keyboard() -> InlineKeyboardMarkup:
    """Редкие разделы — чтобы не занимать постоянную панель."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📄 Договоры", "nav:contracts"), _button("⚙️ Статус", "nav:status")],
            [_button("❓ Помощь", "nav:help"), _button("🔎 Письмо по номеру", "nav:show_hint")],
        ]
    )


def digest_keyboard(days: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("🔄 Обновить", f"nav:digest:{days}"),
                _button("📌 Что висит", "nav:tasks"),
            ],
            [_button("📥 Проверить почту", "nav:sync"), _button("📇 Контакты", "nav:contacts")],
        ]
    )


def tasks_keyboard(items: list[dict]) -> InlineKeyboardMarkup:
    """По кнопке на каждое обязательство — закрывать в один тап."""
    rows: list[list[InlineKeyboardButton]] = []
    for item in items[:6]:
        label = f"✅ #{item['id']}"
        if item.get("direction") == "them":
            label = f"⏳ #{item['id']}"
        rows.append([_button(label, f"done:{item['id']}")])

    rows.append([_button("🔄 Обновить", "nav:tasks"), _button("📊 Сводка", "nav:digest:7")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def email_keyboard(email_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("📊 Сводка", "nav:digest:7"),
                _button("📌 Что висит", "nav:tasks"),
            ],
            [_button("📇 Контакты", "nav:contacts")],
        ]
    )


def confirm_done_keyboard(commitment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button("✅ Закрыть", f"done:{commitment_id}"),
                _button("Отмена", "cancel"),
            ]
        ]
    )


__all__ = [
    "MAIN_KEYBOARD",
    "BUTTON_ROUTES",
    "confirm_done_keyboard",
    "digest_keyboard",
    "email_keyboard",
    "hub_keyboard",
    "tasks_keyboard",
]
