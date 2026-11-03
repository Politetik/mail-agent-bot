"""Извлечение сущностей из русскоязычных деловых писем.

Используется как запасной движок без ИИ и как страховка после ИИ:
модели регулярно теряют телефоны и адреса.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from .util import parse_amount

PHONE_RE = re.compile(
    r"(?<!\d)(?:\+7|8|7)[\s\-(]*\d{3}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}(?!\d)"
    r"|(?<!\d)\+\d{1,3}[\s\-()]*\d{1,4}[\s\-()]*\d{2,4}[\s\-()]*\d{2,4}(?!\d)"
    r"|(?<!\d)8[\s\-]?800[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

ADDRESS_HINT = re.compile(
    r"(ул\.|улица|проспект|пр-т|пр\.|переулок|пер\.|бульвар|б-р|шоссе|ш\.|набережная|наб\.|площадь|пл\."
    r"|проезд|тупик|мкр\.?|микрорайон|корп\.|корпус|стр\.|строение|офис|оф\.|кв\.|квартира|помещени"
    r"|г\.\s*[А-ЯЁ]|г\.о\.|пос\.|поселок|деревня|д\.\s*\d|дом\s*\d)",
    re.IGNORECASE,
)

STREET_WORD = re.compile(
    r"(улица|ул\.|проспект|пр-т|переулок|пер\.|бульвар|б-р|шоссе|набережная|наб\.|площадь|пл\.|проезд|тупик|микрорайон|мкр)",
    re.IGNORECASE,
)

ADDRESS_START = re.compile(
    r"(?:г\.\s*[А-ЯЁ]|город\s+[А-ЯЁ]|ул\.|улица\s+[А-ЯЁ]|пр-т|проспект|пер\.|переулок|б-р|бульвар"
    r"|шоссе|ш\.\s*[А-ЯЁ]|наб\.|набережная|пл\.|площадь|проезд|мкр\.?|микрорайон|пос\.|поселок|деревня)",
    re.IGNORECASE,
)

PERSON_LINE = re.compile(
    r"^([А-ЯЁ][а-яё-]+(?:[ \t]+[А-ЯЁ][а-яё-]+){1,2})(?:[ \t]*,[ \t]*([^,]{3,70}))?[ \t]*$"
)

POSITION_RE = re.compile(
    r"^(генеральн\w+ директор|коммерческ\w+ директор|финансов\w+ директор|директор|руководител\w+"
    r"|начальник\w*|менеджер\w*|специалист\w*|бухгалтер\w*|юрист\w*|аналитик\w*|инженер\w*"
    r"|логист\w*|секретарь|ассистент\w*|владелец|основатель)",
    re.IGNORECASE,
)

KIND_RULES = (
    ("invoice", re.compile(r"(сч[её]т на оплату|сч[её]т\s*№|invoice|акт сверки|упд)", re.IGNORECASE)),
    ("contact", re.compile(r"(контакт|визитк|реквизит|меня зовут|перезвон|свяжитесь)", re.IGNORECASE)),
    ("contract", re.compile(r"(договор|контракт|соглашени[ея]|спецификаци|дополнительное соглашение)", re.IGNORECASE)),
    ("negotiation", re.compile(r"(переговор|предложени|коммерческое предложение|обсужд|условия|скидк)", re.IGNORECASE)),
)

MONTH_NAMES = {
    "январ": 1, "феврал": 2, "март": 3, "апрел": 4, "ма": 5, "июн": 6,
    "июл": 7, "август": 8, "сентябр": 9, "октябр": 10, "ноябр": 11, "декабр": 12,
}


@dataclass(frozen=True)
class Amount:
    amount: float
    currency: str
    raw: str


def normalize_phone(raw) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    if len(digits) < 10 or len(digits) > 15:
        return None
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return "+" + digits


def format_phone(phone) -> str:
    value = str(phone or "")
    match = re.fullmatch(r"\+7(\d{10})", value)
    if match:
        digits = match.group(1)
        return f"+7 ({digits[:3]}) {digits[3:6]}-{digits[6:8]}-{digits[8:10]}"
    return value


def extract_phones(text) -> list[str]:
    source = str(text or "")
    found: list[str] = []
    seen: set[str] = set()
    for match in PHONE_RE.finditer(source):
        raw = match.group(0).strip()
        if not raw or raw[0] not in "+78":
            continue
        normalized = normalize_phone(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        found.append(normalized)
    return found


def extract_emails(text) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in EMAIL_RE.finditer(str(text or "")):
        address = match.group(0).lower()
        if address in seen:
            continue
        seen.add(address)
        found.append(address)
    return found


def _clean_address(value: str) -> str:
    text = re.sub(r"^[\s,;:•·\-–—|]+", "", str(value or ""))
    text = re.sub(r"^(?:адрес|почтовый адрес)\s*[:\-]?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[\s,;:•·|]+$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def _trim_to_address(line: str) -> str:
    match = ADDRESS_START.search(line)
    if not match or match.start() == 0:
        return line
    before = line[: match.start()]
    index = re.search(r"\b\d{6}\b[,\s]*$", before)
    if index:
        return line[index.start():]
    return line[match.start():]


def _merge_wrapped_lines(lines: list[str]) -> list[str]:
    merged: list[str] = []
    for line in lines:
        if merged:
            prev = merged[-1]
            wrapped = (
                (
                    bool(re.search(r",\s*$", prev))
                    or bool(re.search(r"(?:^|\s)(?:г|ул|д|корп|стр|оф|кв|пр|пер|наб|пл|пос)\.$", prev, re.IGNORECASE))
                )
                and len(line) < 80
                and bool(re.match(r"^[А-ЯЁа-яё0-9]", line))
            )
            if wrapped:
                merged[-1] = f"{prev} {line}"
                continue
        merged.append(line)
    return merged


def extract_addresses(text) -> list[str]:
    source = str(text or "")
    raw_lines = [line.strip() for line in re.split(r"\r?\n|;", source) if line.strip()]
    lines = _merge_wrapped_lines(raw_lines)

    found: list[str] = []
    seen: set[str] = set()
    for line in lines:
        if len(line) < 8 or len(line) > 320:
            continue
        if re.search(r"https?:", line, re.IGNORECASE):
            continue
        if not ADDRESS_HINT.search(line):
            continue
        if not re.search(r"\d", line):
            continue

        chunk = _clean_address(_trim_to_address(line))
        if len(chunk) < 8 or len(chunk) > 260:
            continue

        has_street = bool(STREET_WORD.search(chunk))
        has_house = bool(re.search(r"(?:д\.|дом|корп\.|стр\.|офис|оф\.|кв\.|помещени)\s*\d", chunk, re.IGNORECASE))
        has_index = bool(re.search(r"\b\d{6}\b", chunk))
        if not has_street and not has_house:
            continue
        if not has_house and not has_index:
            continue

        key = re.sub(r"[^\wа-яё]", "", chunk.lower())
        if key in seen:
            continue
        seen.add(key)
        found.append(chunk)
    return found[:10]


def extract_inn(text) -> list[str]:
    source = str(text or "")
    found: list[str] = []
    for pattern in (
        r"(?:ИНН|инн)\s*[:№.]?\s*(\d{10}|\d{12})\b",
        r"(?:КПП|кпп)\s*[:№.]?\s*(\d{9})\b",
        r"(?:ОГРН|ОГРНИП|огрн)\s*[:№.]?\s*(\d{13}|\d{15})\b",
    ):
        for match in re.finditer(pattern, source):
            value = match.group(1)
            if match.group(0).lower().startswith("кпп"):
                value = f"КПП {value}"
            elif match.group(0).lower().startswith(("огрн", "огрнип")):
                value = f"ОГРН {value}"
            if value not in found:
                found.append(value)
    return found


def extract_contract_numbers(text) -> list[str]:
    source = str(text or "")
    found: list[str] = []
    patterns = (
        r"(?:договор[а-яё]*|контракт[а-яё]*|соглашени[ея])\s*(?:№|N|номер|No\.?)\s*([^\s,;()«»\"]{1,30})",
        r"(?:№|N)\s*(\d[\w\-/]{1,24})\s*(?:от\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4})?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            raw = re.sub(r"[.,;:]$", "", _clean_address(match.group(1)))
            if not re.search(r"\d", raw):
                continue
            if re.fullmatch(r"(?:от|по|в|на|для|и|с)", raw, re.IGNORECASE):
                continue
            if len(raw) > 30:
                continue
            if raw not in found:
                found.append(raw)
    return found[:5]


def extract_amounts(text) -> list[Amount]:
    source = str(text or "")
    found: list[Amount] = []
    patterns = (
        r"(\d{1,3}(?:[ \u00A0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей|ля|ль)?|rub\b|р\.)",
        r"(\d+(?:[.,]\d+)?)\s*(млн|млрд|тыс)\.?\s*(?:руб|₽)?",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, source, re.IGNORECASE):
            suffix = match.group(2) if match.lastindex and match.lastindex >= 2 else ""
            value = parse_amount(f"{match.group(1)} {suffix}")
            if value is None or value < 100:
                continue
            if any(item.amount == value for item in found):
                continue
            found.append(Amount(amount=value, currency="RUB", raw=match.group(0).strip()))
    return found


def _to_iso_date(year: int, month: int, day: int) -> str | None:
    if year < 100:
        year = 1900 + year if year > 70 else 2000 + year
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_dates(text) -> list[str]:
    source = str(text or "")
    found: list[str] = []

    def push(value: str | None) -> None:
        if value and value not in found:
            found.append(value)

    for match in re.finditer(r"\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b", source):
        push(_to_iso_date(int(match.group(3)), int(match.group(2)), int(match.group(1))))
    for match in re.finditer(r"\b(\d{4})-(\d{2})-(\d{2})\b", source):
        push(_to_iso_date(int(match.group(1)), int(match.group(2)), int(match.group(3))))

    month_re = re.compile(
        r"\b(\d{1,2})\s+(январ[яе]|феврал[яе]|марта|апрел[яе]|ма[яй]|июн[яе]|июл[яе]|августа"
        r"|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе])\s+(\d{4})",
        re.IGNORECASE,
    )
    for match in month_re.finditer(source):
        word = match.group(2).lower()
        key = next((k for k in MONTH_NAMES if word.startswith(k)), None)
        if key:
            push(_to_iso_date(int(match.group(3)), MONTH_NAMES[key], int(match.group(1))))
    return found


def extract_deadline(text) -> str | None:
    source = str(text or "")
    match = re.search(
        r"(?:до|к|не\s+позднее|срок[а-я]*\s+до)\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})",
        source,
        re.IGNORECASE,
    )
    if match:
        dates = extract_dates(match.group(1))
        if dates:
            return dates[0]
    return None


def extract_companies(text) -> list[str]:
    found: list[str] = []
    for match in re.finditer(r"(ООО|АО|ПАО|ЗАО|ОАО|ИП|ФГУП|ГУП|НКО)\s*[«\"']([^»\"'\n]{2,80})[»\"']", str(text or "")):
        name = f"{match.group(1)} «{match.group(2).strip()}»"
        if name not in found:
            found.append(name)
    return found


def extract_signature_name(text) -> str | None:
    match = re.search(
        r"(?:с\s+уважением|уважением|best\s+regards|regards|спасибо)[\s,:—-]*\r?\n?[ \t]*"
        r"([А-ЯЁ][а-яё-]+(?:[ \t]+[А-ЯЁ][а-яё-]+){0,2})",
        str(text or ""),
        re.IGNORECASE,
    )
    return match.group(1).strip() if match else None


def extract_contact_person(text) -> dict[str, str | None] | None:
    """Ищет контактное лицо в блоке «ФИО, должность» рядом с телефоном/почтой."""
    lines = str(text or "").splitlines()
    for index, raw in enumerate(lines):
        line = raw.strip()
        match = PERSON_LINE.match(line)
        if not match:
            continue
        rest = "\n".join(lines[index + 1 : index + 9])
        if not re.search(r"(тел|phone|моб|@|\+7|8\s?\()", rest, re.IGNORECASE):
            continue
        name = match.group(1).strip()
        position = (match.group(2) or "").strip() or None
        if not position and index + 1 < len(lines):
            following = lines[index + 1].strip()
            if len(following) < 70 and POSITION_RE.match(following):
                position = following
        return {"name": name, "position": position}
    return None


def guess_kind(subject, text) -> str:
    haystack = f"{subject or ''}\n{text or ''}"
    for kind, pattern in KIND_RULES:
        if pattern.search(haystack):
            return kind
    return "other"


def guess_status(text) -> str:
    value = str(text or "").lower()
    if re.search(r"(отказ|не согласн|не подходит|отклоня|расторгн|разрыва)", value):
        return "rejected"
    if re.search(r"(подписал|подписано|согласовали|согласовано|оплатили|оплачен|принято|утвердили)", value):
        return "agreed"
    if re.search(r"(жд[её]м|ожидаем|прошу ответить|напоминаю|просьба ответить|нет ответа)", value):
        return "waiting"
    if re.search(r"(обсужд|уточн|предлага|рассмотр)", value):
        return "in_progress"
    return "new"


def guess_risk_level(text, kind: str) -> str:
    value = str(text or "").lower()
    high = (
        "штраф", "неустойк", "пеня", "предоплата 100", "предоплата в размере 100",
        "без гарант", "односторонн", "расторжен", "конфиденциальн", "исключительн",
        "суд", "арбитраж", "просрочк",
    )
    if any(word in value for word in high):
        return "high"
    medium = ("срок", "изменен", "повыш", "индексац", "аванс", "гарант", "залог")
    if any(word in value for word in medium):
        return "medium"
    return "medium" if kind == "contract" else "low"


def extract_asks(text, limit: int = 3) -> list[str]:
    source = re.sub(r"\s+", " ", str(text or ""))
    sentences = re.split(r"(?<=[.!?])\s+", source)
    found: list[str] = []
    for sentence in sentences:
        if not re.search(
            r"(прошу|просим|необходимо|нужно|требуется|ожидаем от вас|ждём от вас|подтвердите|пришлите|направьте|согласуйте)",
            sentence,
            re.IGNORECASE,
        ):
            continue
        cleaned = re.sub(r"^[-–—•\s]+", "", sentence.strip())
        if len(cleaned) < 12 or len(cleaned) > 240:
            continue
        if cleaned not in found:
            found.append(cleaned)
        if len(found) >= limit:
            break
    return found


def detect_bulk(headers, from_address, subject) -> bool:
    def get(name: str) -> str:
        if headers is None:
            return ""
        if hasattr(headers, "get"):
            value = headers.get(name) or headers.get(name.lower()) or headers.get(name.title())
            return "" if value is None else str(value)
        return ""

    if get("list-unsubscribe") or get("list-id") or str(get("precedence")).lower() == "bulk":
        return True
    local = str(from_address or "").lower().split("@")[0]
    if re.match(r"^(no-?reply|noreply|do-?not-?reply|mailer-daemon|postmaster|notification)", local):
        return True
    if re.search(r"рассылк|unsubscribe|отписаться|дайджест|newsletter", str(subject or ""), re.IGNORECASE):
        return True
    return False


def extract_all(text, subject: str = "", from_address: str = "") -> dict:
    body = str(text or "")
    kind = guess_kind(subject, body)
    return {
        "phones": extract_phones(body),
        "emails": extract_emails(body),
        "addresses": extract_addresses(body),
        "inn": extract_inn(body),
        "contract_numbers": extract_contract_numbers(body),
        "amounts": extract_amounts(body),
        "dates": extract_dates(body),
        "companies": extract_companies(body),
        "signature_name": extract_signature_name(body),
        "kind": kind,
        "status": guess_status(body),
        "risk_level": guess_risk_level(body, kind),
        "asks": extract_asks(body),
        "deadline": extract_deadline(body),
    }
