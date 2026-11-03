import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import open_store  # noqa: E402
from app.sync import persist_derived  # noqa: E402


@pytest.fixture()
def store(tmp_path):
    database = open_store(tmp_path / "test.db")
    yield database
    database.close()


SAMPLE_EMAILS = [
    {
        "from_name": "Иван Петров",
        "from_addr": "ivan@romashka.ru",
        "subject": "Договор поставки № 45/2025",
        "body": """Направляем договор поставки № 45/2025 от 03.11.2025.
Сумма договора: 1 250 000 руб. Предоплата 50%.
Срок поставки: до 15.12.2025.
За просрочку предусмотрена неустойка 0,1% в день.

Просим подписать и вернуть скан до 20.11.2025.

С уважением,
Иван Петров
Менеджер по работе с клиентами
ООО «Ромашка»
Тел.: +7 (495) 123-45-67""",
    },
    {
        "from_name": "Мария Ковалёва",
        "from_addr": "m.kovaleva@romashka.ru",
        "subject": "Контакты менеджера по поставкам",
        "body": """Передаю контакты нашего менеджера.

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (495) 123-45-67
Мобильный: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru
Адрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25
ИНН: 7701234567""",
    },
]


async def seed_store(store, emails=None):
    from app.analyze import analyze_email
    from app.util import now_iso
    from datetime import datetime, timedelta, timezone

    emails = emails or SAMPLE_EMAILS
    ids = []
    for index, sample in enumerate(emails):
        sent = datetime.now(timezone.utc) - timedelta(days=len(emails) - index)
        row = {
            "account": "test@mail.ru",
            "folder": "INBOX",
            "uid": 100 + index,
            "message_id": f"<{100 + index}@mail.ru>",
            "from_name": sample["from_name"],
            "from_addr": sample["from_addr"],
            "to_addrs": "test@mail.ru",
            "subject": sample["subject"],
            "sent_at": sent.isoformat(),
            "body_text": sample["body"],
            "snippet": sample["body"][:120],
            "is_bulk": 0,
            "fetched_at": now_iso(),
        }
        email_id, _ = store.insert_email(row)
        email_row = {**row, "id": email_id}
        analysis = await analyze_email(email_row, llm=None)
        store.save_analysis(email_id, analysis)
        persist_derived(store, email_row, analysis)
        ids.append(email_id)
    return ids
