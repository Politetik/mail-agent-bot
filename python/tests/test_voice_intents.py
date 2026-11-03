from app.voice_intents import detect_voice_command, extract_number, normalize


def test_normalize_strips_punctuation_and_yo():
    assert normalize("Проверь  почту!") == "проверь почту"
    assert normalize("Сводка за неделю?") == "сводка за неделю"


def test_extract_digits_and_words():
    assert extract_number("закрой 12") == 12
    assert extract_number("закрой двенадцатое") == 12
    assert extract_number("двадцать один") == 21
    assert extract_number("сто пять") == 105
    assert extract_number("без числа") is None


def test_sync_command():
    assert detect_voice_command("проверь почту").command == "/sync"
    assert detect_voice_command("Обнови ящик, пожалуйста").command == "/sync"
    assert detect_voice_command("забери новые письма").command == "/sync"


def test_digest_command_with_period():
    command = detect_voice_command("сводка за неделю")
    assert command.command == "/digest"
    assert command.args == "7"

    assert detect_voice_command("итоги за 3 дня").args == "3"
    assert detect_voice_command("что нового").command == "/digest"


def test_tasks_contacts_contracts():
    assert detect_voice_command("что висит").command == "/tasks"
    assert detect_voice_command("какие обязательства").command == "/tasks"
    assert detect_voice_command("контакты").command == "/contacts"
    assert detect_voice_command("договоры").command == "/contracts"


def test_done_requires_confirmation():
    command = detect_voice_command("закрой 12")
    assert command.command == "/done"
    assert command.args == "12"
    assert command.confirmation is True

    assert detect_voice_command("отметь выполненным двенадцатое").args == "12"


def test_show_command():
    assert detect_voice_command("покажи письмо 12").command == "/show"
    assert detect_voice_command("открой письмо двенадцать").args == "12"


def test_long_question_is_not_a_command():
    assert detect_voice_command("какой телефон у Анны Смирновой из Ромашки") is None
    assert detect_voice_command("что по договору с Ромашкой на поставку мебели") is None


def test_help_and_status():
    assert detect_voice_command("помощь").command == "/help"
    assert detect_voice_command("статус").command == "/status"


def test_empty_and_garbage():
    assert detect_voice_command("") is None
    assert detect_voice_command("привет как дела") is None
