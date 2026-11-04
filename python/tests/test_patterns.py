from app.patterns import (
    detect_bulk,
    extract_addresses,
    extract_amounts,
    extract_companies,
    extract_contact_person,
    extract_contract_numbers,
    extract_dates,
    extract_deadline,
    extract_inn,
    extract_phones,
    extract_signature_name,
    format_phone,
    guess_kind,
    guess_risk_level,
    guess_status,
    normalize_phone,
)


def test_phones_are_found_and_normalised():
    text = "Звоните: +7 (495) 123-45-67, моб. 8 916 555-77-88, а также 8-800-555-35-35."
    assert extract_phones(text) == ["+74951234567", "+79165557788", "+78005553535"]
    assert format_phone("+74951234567") == "+7 (495) 123-45-67"


def test_dates_and_inn_are_not_phones():
    assert extract_phones("Договор от 03.11.2025, ИНН 7701234567") == []
    assert normalize_phone("12345") is None


def test_addresses_keep_index_street_and_office():
    body = "Реквизиты:\nАдрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25\nИНН: 7701234567"
    addresses = extract_addresses(body)
    assert len(addresses) == 1
    assert "125009" in addresses[0]
    assert "ул. Ленина, д. 10" in addresses[0]


def test_addresses_merge_wrapped_lines_and_reject_bare_city():
    body = "г. Москва, ул. Тверская,\nд. 18, корп. 2\n\nг. Москва\n"
    addresses = extract_addresses(body)
    assert len(addresses) == 1
    assert "Тверская, д. 18" in addresses[0]


def test_companies_inn_contracts_amounts():
    text = "ООО «Ромашка», ИНН 7701234567, КПП 770101001. Договор поставки № 45/2025. Сумма 1 250 000 руб."
    assert extract_companies(text) == ["ООО «Ромашка»"]
    assert "7701234567" in extract_inn(text)
    assert "КПП 770101001" in extract_inn(text)
    assert extract_contract_numbers(text) == ["45/2025"]
    assert extract_amounts(text)[0].amount == 1250000
    assert extract_amounts("скидка 1,25 млн руб.")[0].amount == 1250000


def test_dates_and_deadline_have_no_fallback():
    assert extract_dates("до 15.12.2025 или 2026-01-31, а также 5 марта 2024") == [
        "2025-12-15",
        "2026-01-31",
        "2024-03-05",
    ]
    assert extract_deadline("Срок поставки: до 15.12.2025.") == "2025-12-15"
    assert extract_deadline("Никаких сроков нет") is None


def test_contact_person_comes_from_the_block():
    body = """Передаю контакты.

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru"""
    person = extract_contact_person(body)
    assert person["name"] == "Анна Смирнова"
    assert person["position"] == "руководитель отдела поставок"


def test_signature_does_not_capture_next_line():
    assert extract_signature_name("С уважением,\nДмитрий Орлов\nЛогистика") == "Дмитрий Орлов"


def test_kind_status_risk():
    assert guess_kind("Счёт на оплату № 12", "просим оплатить") == "invoice"
    assert guess_kind("Контакты", "передаю контакты менеджера") == "contact"
    assert guess_kind("Договор № 1", "направляем договор") == "contract"
    assert guess_kind("Переговоры", "обсудим условия") == "negotiation"

    assert guess_status("Договор подписан, оплатили") == "agreed"
    assert guess_status("Мы не согласны с условиями") == "rejected"
    assert guess_status("Ждём вашего ответа") == "waiting"

    assert guess_risk_level("штраф и неустойка за просрочку", "contract") == "high"
    assert guess_risk_level("обычное письмо", "other") == "low"


def test_bulk_detection():
    assert detect_bulk({"list-unsubscribe": "<mailto:unsub@x>"}, "news@shop.ru", "Скидки") is True
    # транзакционные уведомления важны — не считаем рассылкой
    assert detect_bulk({}, "no-reply@shop.ru", "Ваш заказ отправлен") is False
    assert detect_bulk({}, "mailer@sender.ozon.ru", "Заказ отменён") is False
    assert detect_bulk({}, "noreply@alfabank.ru", "Уведомление о новом счёте") is False
    assert detect_bulk({}, "support@example.com", "Your verification code") is False
    # маркетинг отсекаем
    assert detect_bulk({}, "mailer@sender.ozon.ru", "Samsung Galaxy S26 | S26+") is True
    assert detect_bulk({}, "news@shop.ru", "Скидки недели: −30%") is True
    assert detect_bulk({}, "no-reply@shop.ru", "Поделитесь впечатлением") is True
    # обычная переписка
    assert detect_bulk({}, "ivan@romashka.ru", "Договор") is False
