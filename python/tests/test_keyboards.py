from app.keyboards import (
    BUTTON_ROUTES,
    confirm_done_keyboard,
    digest_keyboard,
    hub_keyboard,
    tasks_keyboard,
)


def _data(markup) -> list[str]:
    return [button.callback_data for row in markup.inline_keyboard for button in row]


def test_main_keyboard_is_compact_and_routed():
    rows = BUTTON_ROUTES
    assert len(rows) == 5
    assert rows["📥 Проверить почту"] == "/sync"
    assert rows["📊 Сводка"] == "/digest"
    assert rows["📌 Что висит"] == "/tasks"
    assert rows["📇 Контакты"] == "/contacts"
    assert rows["⚙️ Ещё"] == "/more"


def test_hub_exposes_rare_sections():
    data = _data(hub_keyboard())
    assert "nav:contracts" in data
    assert "nav:status" in data
    assert "nav:help" in data


def test_digest_keyboard_keeps_days():
    data = _data(digest_keyboard(30))
    assert "nav:digest:30" in data
    assert "nav:tasks" in data
    assert "nav:sync" in data


def test_tasks_keyboard_has_close_button_per_item():
    items = [
        {"id": 10, "direction": "me"},
        {"id": 11, "direction": "them"},
    ]
    markup = tasks_keyboard(items)
    data = _data(markup)
    assert "done:10" in data
    assert "done:11" in data
    # кнопки закрытия идут первыми строками, по одной на обязательство
    assert markup.inline_keyboard[0][0].text == "✅ #10"
    assert markup.inline_keyboard[1][0].text == "⏳ #11"


def test_tasks_keyboard_limits_items():
    items = [{"id": i, "direction": "me"} for i in range(1, 20)]
    markup = tasks_keyboard(items)
    close_buttons = [row for row in markup.inline_keyboard if row[0].callback_data.startswith("done:")]
    assert len(close_buttons) == 6


def test_confirm_done_keyboard():
    data = _data(confirm_done_keyboard(12))
    assert data == ["done:12", "cancel"]
