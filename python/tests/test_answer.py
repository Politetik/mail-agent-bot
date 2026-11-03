import asyncio

from app.answer import (
    answer_contacts,
    answer_contracts,
    answer_tasks,
    build_digest,
    detect_intent,
    find_matching_contacts,
    stem_ru,
)
from conftest import seed_store


def test_stem_matches_inflections():
    assert stem_ru("Смирновой") == stem_ru("Смирнова")
    assert stem_ru("Анны") == stem_ru("Анна")


def test_intent_detection():
    assert detect_intent("Какой телефон у Анны?") == "contacts"
    assert detect_intent("Что с договором поставки?") == "contracts"
    assert detect_intent("Что висит на неделе?") == "tasks"
    assert detect_intent("О чём договорились?") == "negotiations"


def test_contacts_found_by_inflected_name(store):
    asyncio.run(seed_store(store))
    result = answer_contacts(store, "Какой телефон у Анны Смирновой?")
    assert "Анна Смирнова" in result["text"]
    assert "+7 (916) 555-77-88" in result["text"]
    assert "+7 (495) 123-45-67" in result["text"]
    assert "a.smirnova@romashka.ru" in result["text"]
    assert "ул. Ленина, д. 10" in result["text"]


def test_contacts_found_by_phone(store):
    asyncio.run(seed_store(store))
    result = answer_contacts(store, "Кто это +7 916 555 77 88?")
    assert "Анна Смирнова" in result["text"]


def test_address_query_filters_contacts(store):
    asyncio.run(seed_store(store))
    result = answer_contacts(store, "Какие адреса есть в письмах?")
    assert "ул. Ленина" in result["text"]


def test_contracts_filter(store):
    asyncio.run(seed_store(store))
    all_contracts = answer_contracts(store)
    assert "45/2025" in all_contracts["text"]
    filtered = answer_contracts(store, query="Что по договору с Ромашкой?")
    assert "ООО «Ромашка»" in filtered["text"]


def test_fuzzy_contact_search(store):
    asyncio.run(seed_store(store))
    rows = find_matching_contacts(store, "дай телефон Анны Смирновой")
    assert rows and rows[0]["name"] == "Анна Смирнова"


def test_digest_and_tasks(store):
    asyncio.run(seed_store(store))
    digest = build_digest(store, days=7)
    assert "Сводка" in digest["text"]
    assert "45/2025" in digest["text"]
    tasks = answer_tasks(store)
    assert "подписать" in tasks["text"].lower() or "просим" in tasks["text"].lower()

def test_overdue_commitment_is_marked(store):
    store.upsert_commitment({"direction": "me", "text": "Старый долг", "due": "2020-01-01"})
    text = answer_tasks(store)["text"]
    assert "Я должен" in text
    assert "просрочено" in text
