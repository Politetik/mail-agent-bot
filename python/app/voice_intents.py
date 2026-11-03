"""Разбор голосовых/текстовых команд: «проверь почту», «сводка за неделю», «закрой 12».

Задача — не угадывать, а узнавать явные команды. Всё остальное уходит в Q&A.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNITS = {
    "ноль": 0, "один": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10,
    "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13, "четырнадцать": 14,
    "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17, "восемнадцать": 18,
    "девятнадцать": 19,
}
TENS = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
HUNDREDS = {"сто": 100, "двести": 200, "триста": 300, "четыреста": 400, "пятьсот": 500}

ORDINALS = {
    "первое": 1, "второе": 2, "третье": 3, "четвертое": 4, "пятое": 5,
    "шестое": 6, "седьмое": 7, "восьмое": 8, "девятое": 9, "десятое": 10,
    "одиннадцатое": 11, "двенадцатое": 12, "тринадцатое": 13, "четырнадцатое": 14,
    "пятнадцатое": 15, "шестнадцатое": 16, "семнадцатое": 17, "восемнадцатое": 18,
    "девятнадцатое": 19, "двадцатое": 20,
}

QUESTION_WORDS = re.compile(r"\b(какой|какая|какие|где|когда|почему|зачем|сколько|кто|чем)\b", re.IGNORECASE)


def normalize(text: str) -> str:
    value = str(text or "").lower().replace("ё", "е")
    value = re.sub(r"[«»\"'`]", "", value)
    value = re.sub(r"[!?.,;:]+", " ", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def extract_number(text: str) -> int | None:
    value = normalize(text)

    digits = re.search(r"\b(\d{1,4})\b", value)
    if digits:
        return int(digits.group(1))

    words = re.findall(r"[а-я]+", value)
    total: int | None = None
    for word in words:
        if word in ORDINALS:
            return ORDINALS[word]
        if word in HUNDREDS:
            total = (total or 0) + HUNDREDS[word]
        elif word in TENS:
            total = (total or 0) + TENS[word]
        elif word in UNITS:
            total = (total or 0) + UNITS[word]
    return total


@dataclass(frozen=True)
class VoiceCommand:
    command: str
    args: str | None = None
    confirmation: bool = False


def _days_from(text: str) -> int:
    if re.search(r"\bнедел", text):
        return 7
    if re.search(r"\bмесяц|\b30\s*дн", text):
        return 30
    number = extract_number(text)
    if number and 1 <= number <= 365 and re.search(r"\bдн|\bдень|\bсут", text):
        return number
    return 7


def detect_voice_command(text: str) -> VoiceCommand | None:
    raw = str(text or "").strip()
    if not raw:
        return None

    value = normalize(raw)
    words = value.split()

    # Длинный вопрос с вопросительным словом — это Q&A, а не команда.
    if len(words) >= 4 and QUESTION_WORDS.search(value):
        return None

    # «проверь почту», «обнови ящик», «забери письма»
    if re.search(r"\b(проверь|проверить|обнови|обновить|синхронизируй|забери)\b", value) and re.search(
        r"\b(почт|ящик|письм)", value
    ):
        return VoiceCommand("/sync")

    # «сводка за неделю», «что нового», «итоги за 3 дня»
    if re.search(r"\b(сводк\w*|дайджест\w*|итог\w*)\b", value) or re.fullmatch(r"что нового.*", value):
        return VoiceCommand("/digest", str(_days_from(value)))

    # «что висит», «задачи», «обязательства»
    if re.search(r"\b(что висит|задач\w*|обязательств\w*|что ждет|что ждут)\b", value):
        return VoiceCommand("/tasks")

    # «контакты», «телефоны», «найди телефон Анны»
    if re.search(r"\b(контакт\w*|телефон\w*|адресная книга|реквизит\w*)\b", value):
        query = value if len(words) > 2 else None
        return VoiceCommand("/contacts", query)

    # «договоры», «реестр договоров»
    if re.search(r"\b(договор\w*|контракт\w*|реестр)\b", value) and len(words) <= 5:
        return VoiceCommand("/contracts")

    # «закрой 12», «готово двенадцатое»
    if re.search(r"\b(закрой|закрыть|выполнено|готово|сделано|отметь)\b", value):
        number = extract_number(value)
        if number:
            return VoiceCommand("/done", str(number), confirmation=True)
        return VoiceCommand("/done", None)

    # «покажи письмо 12», «открой письмо двенадцать»
    if re.search(r"\b(покажи|открой|посмотри)\b", value) and re.search(r"\bписьм", value):
        number = extract_number(value)
        if number:
            return VoiceCommand("/show", str(number))

    # «статус», «помощь»
    if re.fullmatch(r"статус.*", value):
        return VoiceCommand("/status")
    if re.search(r"\b(помощь|что ты умеешь|какие команды)\b", value):
        return VoiceCommand("/help")

    return None


__all__ = ["VoiceCommand", "detect_voice_command", "extract_number", "normalize"]
