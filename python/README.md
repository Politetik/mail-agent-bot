# 🐍 Python-версия почтового ИИ-агента

Тот же бот, что и Node-версия, но на Python: **aiogram 3** для Telegram,
`imaplib` для Mail.ru, `sqlite3` для базы. Схема базы данных совместима —
можно переключаться между реализациями, не теряя историю писем.

Проверено на **Python 3.14.10** (Windows) и aiogram 3.31.

---

## Быстрый старт

### 1. Окружение

```powershell
cd mail-agent-bot\python

# Если команда `python` открывает Microsoft Store — используйте launcher:
py -3 -m venv .venv

# либо полный путь:
# C:\Users\<вы>\AppData\Local\Programs\Python\Python314\python.exe -m venv .venv

.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 2. Настройки

Конфиг берётся из `../.env` (общий с Node-версией) или из `python/.env` — второй приоритетнее.

Мастер сам спросит токен, почту и ключи (секреты вводятся без эха):

```powershell
..\scripts\setup-env.cmd
```

Или вручную: скопируйте шаблон и отредактируйте.

```powershell
copy ..\.env.example ..\.env
notepad ..\.env
```

Минимум для запуска:

```
TELEGRAM_BOT_TOKEN=123456789:AAH...
ALLOWED_CHAT_IDS=123456789        # узнать: написать боту /start, он покажет chat_id
MAIL_USER=you@mail.ru
MAIL_PASSWORD=пароль_для_внешних_приложений
LLM_API_KEY=sk-...                # опционально, но рекомендуется
```

Пароль Mail.ru создаётся в настройках: **Безопасность → Пароли для внешних приложений**.
Обычный пароль не подойдёт.

### 3. Запуск

```powershell
.\.venv\Scripts\python.exe -m app.main
```

### 4. Проверка без ключей

```powershell
.\.venv\Scripts\python.exe -m app.cli demo
```

Демо наполнит тестовый ящик (`python/data/demo.db`) и покажет сводку, поиск телефона,
договоры и задачи — Telegram и почта не нужны.

---

## Команды

```powershell
.\.venv\Scripts\python.exe -m app.cli doctor          # проверить настройки и подключения
.\.venv\Scripts\python.exe -m app.cli sync            # прочитать почту
.\.venv\Scripts\python.exe -m app.cli ask "какой телефон у Анны?"
.\.venv\Scripts\python.exe -m app.cli digest 30
.\.venv\Scripts\python.exe -m app.cli contacts Анна
.\.venv\Scripts\python.exe -m app.cli contracts
.\.venv\Scripts\python.exe -m app.cli tasks
.\.venv\Scripts\python.exe -m app.cli stats
.\.venv\Scripts\python.exe -m app.cli demo
.\.venv\Scripts\python.exe -m app.cli reset
```

Команды внутри Telegram — те же: `/sync`, `/digest`, `/contacts`, `/contracts`, `/tasks`,
`/status`, `/show 12`, `/help`. Текст и голосовые сообщения обрабатываются одинаково.

---

## Обязательства и статусы тредов

`/tasks` показывает не абстрактные «задачи», а обязательства с направлением:

- **Я должен** — просьбы к вам и ваши обещания (входящие письма).
- **Жду от них** — обещания контрагентов и ваши просьбы (исходящие письма).
- **Молчат больше N дн.** — последним писали вы, ответа нет дольше `FOLLOW_UP_DAYS` (по умолчанию 3).

Данные хранятся в таблицах `commitments` (направление, текст, срок, статус) и `threads`
(агрегат переписки: когда последний вход/исход, сколько писем). Просроченные сроки
помечаются ⚠️. Уведомления роботов (`no-reply`, `mailer`, `agent`) обязательств не создают.

Алиасы Яндекс.Почты (`ya.ru` ↔ `yandex.ru`) распознаются как один ящик — иначе
исходящие письма считались бы входящими и направление обязательств путалось.

## Защита от prompt injection
Письма — недоверенный ввод. Перед отправкой в модель текст проходит через `app/sanitize.py`:

- удаляются невидимые и bidi-символы, HTML-комментарии, длинные base64-блоки;
- строки с типичными инъекциями («ignore previous instructions», «игнорируй инструкции»,
  `<|im_start|>`, `[INST]`, `assistant:` и т.п.) заменяются на пометку об удалении;
- тело письма оборачивается в границы `<<<EMAIL … EMAIL>>>`, а системный промпт
  прямо запрещает выполнять инструкции из письма.

Каждое срабатывание пишется в лог с номером письма.

---

## Тесты

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

40 тестов: извлечение сущностей, FTS-поиск с кириллицей, слияние правил и ИИ,
ответы по падежам, UTF-7 имена папок IMAP, разбор MIME, откат при ошибках LLM.

---

## Структура

```
python/
├── app/
│   ├── main.py       точка входа (long polling, автосинхронизация)
│   ├── bot.py        aiogram: команды, кнопки, голос, прогресс
│   ├── answer.py     интенты, поиск, ответы, сводки
│   ├── analyze.py    анализ письма: ИИ + правила
│   ├── patterns.py   извлечение сущностей из русского текста
│   ├── mail_imap.py  IMAP Mail.ru, UTF-7 папки, MIME
│   ├── db.py         SQLite + FTS5
│   ├── llm.py        клиент OpenAI-совместимого API
│   ├── stt.py        Whisper / совместимый STT
│   ├── sync.py       конвейер синхронизации
│   ├── cli.py        консольные команды
│   └── demo.py       демо на тестовых письмах
├── tests/
├── requirements.txt
└── requirements-dev.txt
```

---

## Отличия от Node-версии

| | Python | Node.js |
|---|---|---|
| Telegram | aiogram 3 | свой клиент на fetch |
| Почта | `imaplib` (stdlib) | `imapflow` + `mailparser` |
| Зависимости | одна (`aiogram`) | две (`imapflow`, `mailparser`) |
| База | та же схема, `data/mail-agent.db` | та же схема |

Обе версии **нельзя запускать одновременно с одним и тем же токеном** — Telegram
разрешает только один long polling на бота (второй получит `409 Conflict`).

---

## Диагностика

| Симптом | Решение |
|---|---|
| `python` открывает Store | используйте `py -3` или полный путь к `python.exe` |
| `AUTHENTICATIONFAILED` | пароль для внешних приложений, не обычный |
| Бот молчит | добавьте chat_id в `ALLOWED_CHAT_IDS` |
| `409 Conflict` | уже запущена другая копия (или Node-версия) |
| Голос не работает | нет `OPENAI_API_KEY` / `STT_API_KEY` |
| Пустые ответы | не было `/sync` |
## Рендер Markdown в Telegram

Модель отвечает в Markdown, а Telegram не понимает его «как есть». Модуль
pp/telegram_format.py преобразует безопасное подмножество в HTML и экранирует всё
остальное — сообщение не может «не распарситься»:

- `## Заголовок` → **жирный**;
- списки `-`/`*` → `•`, цитаты `>` → `▎`;
- `**жирный**`, `*курсив*`, ` `код` `, `~~зачёркнуто~~` → соответствующие теги;
- `[текст](https://…)` → ссылка, небезопасные схемы → обычный текст;
- код-фенсы → `<pre>` с экранированием;
- длинный ответ режется на части ≤ 3800 символов **с закрытыми тегами** в каждой.

Если разметка получилась битой, срабатывает `to_telegram_plain` — читаемый текст без тегов.
Тесты: `python/tests/test_telegram_format.py` (14 кейсов).
