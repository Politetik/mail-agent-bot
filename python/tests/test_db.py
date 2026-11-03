import asyncio

from app.db import build_fts_query
from app.sync import persist_derived
from app.util import now_iso


def sample_email(**overrides):
    row = {
        "account": "test@mail.ru",
        "folder": "INBOX",
        "uid": 1,
        "message_id": "<1@mail.ru>",
        "from_name": "Иван Петров",
        "from_addr": "ivan@romashka.ru",
        "to_addrs": "test@mail.ru",
        "subject": "Договор поставки № 45/2025",
        "sent_at": now_iso(),
        "body_text": "Направляем договор поставки № 45/2025. Сумма 1 250 000 руб. Телефон +7 (495) 123-45-67.",
        "snippet": "Направляем договор",
        "is_bulk": 0,
    }
    row.update(overrides)
    return row


def test_fts_query_builder():
    assert build_fts_query("договор поставки") == '"договор"* OR "поставки"*'
    assert build_fts_query("!!!") is None


def test_insert_dedupe_and_cyrillic_search(store):
    email_id, inserted = store.insert_email(sample_email())
    assert email_id > 0 and inserted is True

    again_id, again_inserted = store.insert_email(sample_email())
    assert again_inserted is False and again_id == email_id

    hits = store.search_emails("договор поставки")
    assert len(hits) == 1
    assert hits[0]["subject"].startswith("Договор поставки")

    assert store.search_emails("телефон")
    assert store.search_emails("несуществующийзапрос") == []


def test_analysis_merge_keeps_fields(store):
    email_id, _ = store.insert_email(sample_email())
    store.save_analysis(
        email_id,
        {
            "kind": "contract",
            "summary": "Договор поставки",
            "amount": 1250000,
            "status": "waiting",
            "risk_level": "high",
            "risks": ["штраф"],
            "phones": ["+74951234567"],
            "addresses": ["г. Москва, ул. Ленина, д. 10"],
            "contacts": [{"name": "Иван Петров", "phone": "+74951234567"}],
        },
    )
    store.save_analysis(email_id, {"summary": "Обновлено", "risk_level": "medium"})

    analysis = store.get_analysis(email_id)
    assert analysis["summary"] == "Обновлено"
    assert analysis["risk_level"] == "medium"
    assert analysis["kind"] == "contract"
    assert analysis["status"] == "waiting"

    email = store.get_email(email_id)
    assert email["phones"] == ["+74951234567"]


def test_contacts_contracts_tasks_and_stats(store):
    email_id, _ = store.insert_email(sample_email())

    assert store.upsert_contact({"email_id": email_id, "name": "Иван Петров", "phone": "+74951234567"}) is True
    assert store.upsert_contact({"email_id": email_id, "name": "Иван Петров", "phone": "+74951234567"}) is False

    store.upsert_contract({"email_id": email_id, "number": "45/2025", "counterparty": "ООО «Ромашка»", "amount": 1250000})
    store.upsert_contract({"email_id": email_id, "number": "45/2025", "counterparty": "ООО «Ромашка»"})
    store.upsert_commitment(
        {"email_id": email_id, "thread_key": "t1", "direction": "me", "text": "Подписать договор", "due": "2025-12-15"}
    )

    stats = store.stats()
    assert stats["emails"] == 1
    assert stats["contacts"] == 1
    assert stats["contracts"] == 1, "одно упоминание договора не должно дублироваться"
    assert stats["openTasks"] == 1
    assert stats["commitments"] == 1

    assert store.contacts(query="ромашка") == []
    assert len(store.contacts(query="иван")) == 1
    assert len(store.contacts(query="+7495")) == 1


def test_sync_state_and_reset(store):
    store.set_sync_state("test@mail.ru", "INBOX", uidvalidity="123", last_uid=42)
    state = store.get_sync_state("test@mail.ru", "INBOX")
    assert state["last_uid"] == 42

    store.insert_email(sample_email())
    store.reset()
    assert store.stats()["emails"] == 0


def test_bulk_is_excluded_from_recent(store):
    store.insert_email(sample_email(uid=1, subject="Рассылка", is_bulk=1))
    store.insert_email(sample_email(uid=2, subject="Рабочее письмо", is_bulk=0))
    recent = store.recent_emails(limit=10, since_days=1)
    assert [row["subject"] for row in recent] == ["Рабочее письмо"]


def test_threads_aggregate_and_silent_detection(store):
    store.insert_email(
        sample_email(uid=1, thread_key="t1", account="test@mail.ru", from_addr="ivan@romashka.ru", sent_at="2026-10-01T10:00:00+00:00")
    )
    store.insert_email(
        sample_email(uid=2, thread_key="t1", account="test@mail.ru", from_addr="test@mail.ru", sent_at="2026-10-02T10:00:00+00:00")
    )

    thread = store.thread("t1")
    assert thread["message_count"] == 2
    assert thread["last_outbound_at"].startswith("2026-10-02")
    assert thread["last_inbound_at"].startswith("2026-10-01")

    silent = store.silent_threads(days=1)
    assert any(item["thread_key"] == "t1" for item in silent)
    assert store.silent_threads(days=3650) == []


def test_commitments_dedupe_direction_and_close(store):
    assert store.upsert_commitment({"direction": "them", "text": "Пришлют КП", "thread_key": "t2"}) is True
    assert store.upsert_commitment({"direction": "them", "text": "  пришлют   КП ", "thread_key": "t2"}) is False

    mine = store.upsert_commitment({"direction": "me", "text": "Выставить счёт", "due": "2026-12-01"})
    assert mine is True

    assert len(store.open_commitments(direction="them")) == 1
    assert len(store.open_commitments(direction="me")) == 1

    target = store.open_commitments(direction="me")[0]
    assert store.close_commitment(target["id"]) is True
    assert store.open_commitments(direction="me") == []


def test_stats_persist_derived_uses_commitments(store):
    email_id, _ = store.insert_email(sample_email())
    email_row = store.get_email(email_id)
    analysis = {
        "kind": "contract",
        "summary": "Договор",
        "asks": ["подписать"],
        "commitments": [{"direction": "me", "text": "Подписать договор", "due": "2025-12-15"}],
        "contacts": [],
        "risks": [],
    }
    persist_derived(store, email_row, analysis, account="test@mail.ru")
    stats = store.stats()
    assert stats["commitments"] == 1
    assert stats["openTasks"] == 1


def test_persist_derived_creates_records(store):
    email_id, _ = store.insert_email(sample_email())
    email_row = store.get_email(email_id)
    analysis = {
        "kind": "contract",
        "summary": "Договор",
        "contract_number": "45/2025",
        "counterparty": "ООО «Ромашка»",
        "amount": 1250000,
        "currency": "RUB",
        "deadline": "2025-12-15",
        "status": "waiting",
        "risks": ["неустойка"],
        "asks": ["подписать"],
        "contacts": [{"name": "Иван Петров", "phone": "+74951234567", "company": "ООО «Ромашка»"}],
        "phones": ["+74951234567"],
        "addresses": [],
    }
    persist_derived(store, email_row, analysis)

    assert store.stats()["contracts"] == 1
    assert store.stats()["contacts"] == 1
    assert store.stats()["openTasks"] == 1

def test_automated_sender_does_not_create_commitments(store):
    email_id, _ = store.insert_email(sample_email(uid=77, from_addr="it.agent@autodoc.ru"))
    email_row = store.get_email(email_id)
    persist_derived(
        store,
        email_row,
        {
            "kind": "other",
            "summary": "Уведомление о заказе",
            "commitments": [{"direction": "them", "text": "Обработать заказ #167", "due": None}],
            "contacts": [],
            "risks": [],
        },
        account="test@mail.ru",
    )
    assert store.stats()["commitments"] == 0
