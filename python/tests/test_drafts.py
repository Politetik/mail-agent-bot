from app.db import open_store


def test_draft_lifecycle(tmp_path):
    store = open_store(tmp_path / "drafts.db")
    try:
        draft_id = store.create_draft(
            123,
            ["ivan@romashka.ru"],
            "Договор",
            "Текст письма",
            instruction="напиши Ивану",
        )
        draft = store.get_draft(draft_id)
        assert draft["chat_id"] == 123
        assert draft["to_addrs"] == "ivan@romashka.ru"
        assert draft["status"] == "pending"

        assert store.update_draft(draft_id, subject="Re: Договор", feedback="короче") is True
        draft = store.get_draft(draft_id)
        assert draft["subject"] == "Re: Договор"
        assert draft["feedback"] == "короче"

        assert store.set_draft_status(draft_id, "sent") is True
        assert store.get_draft(draft_id)["status"] == "sent"
        assert store.get_draft(999999) is None
    finally:
        store.close()


def test_update_without_fields_returns_false(tmp_path):
    store = open_store(tmp_path / "drafts2.db")
    try:
        draft_id = store.create_draft(1, ["a@b.ru"], "T", "B")
        assert store.update_draft(draft_id) is False
    finally:
        store.close()
