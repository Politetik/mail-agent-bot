"""Демо-режим: тестовый ящик, разбор правилами, примеры вопросов."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from .analyze import analyze_email
from .answer import answer_question, build_digest
from .sync import persist_derived
from .util import logger, now_iso

SAMPLE_MAIL = [
    {
        "days": 0,
        "from_name": "Иван Петров",
        "from_addr": "ivan.petrov@romashka.ru",
        "subject": "Договор поставки № 45/2025 — просим подписать",
        "body": """Добрый день!

Направляем на подписание договор поставки № 45/2025 от 03.11.2025.

Предмет: поставка офисной мебели в количестве 120 единиц.
Сумма договора: 1 250 000 руб., в том числе НДС 20%.
Условия оплаты: предоплата 50%, остаток — в течение 5 банковских дней после поставки.
Срок поставки: до 15.12.2025.
За просрочку поставки предусмотрена неустойка 0,1% от суммы договора за каждый день.

Просим подписать и вернуть скан до 20.11.2025.

С уважением,
Иван Петров
Менеджер по работе с клиентами
ООО «Ромашка»
Тел.: +7 (495) 123-45-67, доб. 214""",
    },
    {
        "days": 1,
        "from_name": "Алексей Смирнов",
        "from_addr": "a.smirnov@mycompany.ru",
        "subject": "Re: Договор поставки № 45/2025",
        "body": """Иван, добрый день!

Посмотрели договор № 45/2025. В целом условия подходят, но просим:
1) снизить предоплату с 50% до 30%;
2) исключить неустойку за просрочку, не зависящую от нас.

Готовы подписать до 25.11.2025 и оплатить первый транш.

С уважением,
Алексей Смирнов
Финансовый директор""",
    },
    {
        "days": 2,
        "from_name": "Мария Ковалёва",
        "from_addr": "m.kovaleva@romashka.ru",
        "subject": "Контакты менеджера по поставкам",
        "body": """Коллеги, добрый день!

Передаю контакты нашего менеджера по поставкам:

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (495) 123-45-67
Мобильный: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru
Адрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25
ИНН: 7701234567

По вопросам договора № 45/2025 обращайтесь к ней.""",
    },
    {
        "days": 3,
        "from_name": "Бухгалтерия Ромашка",
        "from_addr": "buh@romashka.ru",
        "subject": "Счёт на оплату № 128 от 01.11.2025",
        "body": """Добрый день!

Направляем счёт на оплату № 128 от 01.11.2025 на сумму 625 000 руб.
(предоплата 50% по договору поставки № 45/2025).

Просим оплатить до 10.11.2025.

Реквизиты: ООО «Ромашка», ИНН 7701234567, КПП 770101001.""",
    },
    {
        "days": 5,
        "from_name": "Мария Ковалёва",
        "from_addr": "m.kovaleva@vector.ru",
        "subject": "Переговоры по аренде офиса — наше предложение",
        "body": """Здравствуйте!

По переговорам об аренде офиса: готовы предложить помещение 180 кв.м по адресу
г. Москва, ул. Тверская, д. 18, корп. 2.

Ставка — 250 000 руб. в месяц, срок 11 месяцев, договор № А-12/2025.
Обеспечительный платёж — один месяц.

Ждём вашего решения до 01.12.2025.

С уважением,
Мария Ковалёва
Директор по аренде
ООО «Вектор»
+7 (499) 765-43-21""",
    },
    {
        "days": 6,
        "from_name": "Дмитрий Орлов",
        "from_addr": "d.orlov@logistika.ru",
        "subject": "Адрес склада для самовывоза",
        "body": """Добрый день!

Сообщаю адрес склада для самовывоза:
г. Химки, ул. Победы, д. 5, стр. 2, склад № 7.
Режим работы: пн–пт с 9:00 до 18:00.

Телефон склада: 8 (800) 555-35-35.

С уважением,
Дмитрий Орлов
Логистика""",
    },
    {
        "days": 1,
        "from_name": "Магазин мебели",
        "from_addr": "no-reply@shop.example",
        "subject": "Скидки недели: −30% на всё",
        "body": "Рассылка. Скидки недели, промокод SALE30. Отписаться можно по ссылке.",
        "is_bulk": 1,
    },
]

QUESTIONS = (
    "Какой телефон у Анны Смирновой?",
    "Что по договору с Ромашкой?",
    "Какие адреса есть в письмах?",
    "Что висит на этой неделе?",
)


def plain(text: str) -> str:
    value = re.sub(r"<br\s*/?>", "\n", str(text or ""), flags=re.IGNORECASE)
    value = re.sub(r"</p>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    value = value.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\n{3,}", "\n\n", value).strip()


async def run_demo(cfg, store) -> str:
    output: list[str] = []

    def emit(line: str = "") -> None:
        output.append(line)
        print(line)

    store.reset()
    emit("📦 Демо-режим: наполняю тестовый ящик (ключи и настройки не нужны)")
    emit("")

    inserted = 0
    for index, sample in enumerate(SAMPLE_MAIL):
        sent = datetime.now(timezone.utc) - timedelta(days=sample["days"])
        row = {
            "account": "demo@mail.ru",
            "folder": "INBOX",
            "uid": 1000 + index,
            "message_id": f"<demo-{index}@mail.ru>",
            "thread_key": sample["subject"].lower(),
            "from_name": sample["from_name"],
            "from_addr": sample["from_addr"],
            "to_addrs": "demo@mail.ru",
            "subject": sample["subject"],
            "sent_at": sent.isoformat(),
            "body_text": sample["body"],
            "snippet": sample["body"].replace("\n", " ")[:200],
            "is_bulk": sample.get("is_bulk", 0),
            "fetched_at": now_iso(),
        }
        email_id, is_new = store.insert_email(row)
        if is_new:
            inserted += 1
        if sample.get("is_bulk"):
            continue

        email_row = {**row, "id": email_id}
        analysis = await analyze_email(email_row, llm=None)
        store.save_analysis(email_id, analysis)
        persist_derived(store, email_row, analysis)

    emit(f"✅ Загружено писем: {inserted}, проанализировано правилами")

    stats = store.stats()
    emit("")
    emit("📊 База:")
    emit(f"   писем: {stats['emails']}, разобрано: {stats['analyzed']}")
    emit(f"   контактов: {stats['contacts']}, договоров: {stats['contracts']}, задач: {stats['openTasks']}")
    emit(f"   высокий риск: {stats['highRisk']}")
    emit("")

    digest = build_digest(store, days=7)
    emit(plain(digest["text"]))
    emit("")

    for question in QUESTIONS:
        emit(f"❓ {question}")
        result = await answer_question(store, cfg, None, question)
        emit(plain(result["text"]))
        emit("")

    emit("Готово. Запустите бота: python -m app.main (после настройки .env)")
    logger.info("Демо завершено")
    return "\n".join(output)
