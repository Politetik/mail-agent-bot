"""Telegram-бот на aiogram 3: команды, кнопки, голос, прогресс."""

from __future__ import annotations

from time import monotonic

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    KeyboardButton,
    LinkPreviewOptions,
    Message,
    ReplyKeyboardMarkup,
)

from .answer import (
    answer_contacts,
    answer_contracts,
    answer_question,
    answer_tasks,
    build_digest,
    kind_label,
    status_label,
)
from .config import describe_config
from .patterns import format_phone
from .stt import SttError, transcribe
from .sync import run_sync
from .telegram_format import format_for_telegram
from .util import (
    chunk_text,
    escape_html,
    format_date,
    format_datetime,
    format_money,
    logger,
    truncate,
)

KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📥 Проверить почту"), KeyboardButton(text="📊 Сводка")],
        [KeyboardButton(text="📇 Контакты"), KeyboardButton(text="📄 Договоры")],
        [KeyboardButton(text="📌 Задачи"), KeyboardButton(text="⚙️ Статус")],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

BUTTON_ROUTES = {
    "📥 Проверить почту": "/sync",
    "📊 Сводка": "/digest",
    "📇 Контакты": "/contacts",
    "📄 Договоры": "/contracts",
    "📌 Задачи": "/tasks",
    "⚙️ Статус": "/status",
}

HELP_TEXT = "\n".join(
    [
        "<b>Что я умею</b>",
        "",
        "• /sync — прочитать новую почту и разобрать её",
        "• /digest [дней] — сводка: договоры, риски, что ждёт ответа",
        "• /contacts [имя] — контакты с телефонами и адресами",
        "• /contracts — реестр договоров",
        "• /tasks — что висит и ждёт ответа",
        "• /done 12 — закрыть обязательство из /tasks",
        "• /status — состояние подключений и базы",
        "• /show 12 — показать письмо по номеру",
        "",
        "Просто напишите вопрос текстом или отправьте голосовое 🎤:",
        "«какой телефон у Анны Смирновой?»",
        "«что по договору с Ромашкой?»",
        "«о чём договорились по поставке?»",
    ]
)


def strip_html(text: str) -> str:
    import re

    value = re.sub(r"<br\s*/?>", "\n", str(text or ""), flags=re.IGNORECASE)
    value = re.sub(r"</p>", "\n", value, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", "", value)
    return (
        value.replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&amp;", "&")
        .strip()
    )


class AccessMiddleware(BaseMiddleware):
    """Пускать только chat_id из белого списка; остальным один раз показать их id."""

    def __init__(self, cfg):
        self.allowed = set(cfg.telegram.allowed_chat_ids)
        self.warned: set[int] = set()

    async def __call__(self, handler, event, data):
        if isinstance(event, Message):
            chat_id = event.chat.id
            if chat_id not in self.allowed:
                if chat_id not in self.warned:
                    self.warned.add(chat_id)
                    logger.warning("Отклонён доступ для chat_id=%s", chat_id)
                    await event.answer(
                        "🔒 Доступ не настроен.\n\n"
                        f"Ваш chat_id: <code>{chat_id}</code>\n"
                        "Добавьте его в <code>ALLOWED_CHAT_IDS</code> в файле <code>.env</code> и перезапустите бота.\n"
                        "Это защита: бот читает вашу почту, посторонние к ней доступа не получат.",
                        reply_markup=KEYBOARD,
                    )
                return None
        return await handler(event, data)


class Progress:
    """Редактируемое сообщение о ходе синхронизации."""

    def __init__(self, bot: Bot, chat_id: int):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id: int | None = None
        self._last_at = 0.0
        self._text = ""

    async def begin(self, text: str) -> None:
        message = await self.bot.send_message(self.chat_id, text)
        self.message_id = message.message_id
        self._text = text
        self._last_at = monotonic()

    async def update(self, text: str, force: bool = False) -> None:
        if not self.message_id or text == self._text:
            return
        now = monotonic()
        if not force and now - self._last_at < 1.5:
            return
        self._last_at = now
        self._text = text
        try:
            await self.bot.edit_message_text(text=text, chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest as error:
            logger.debug("editMessageText: %s", error)

    async def finish(self, text: str) -> None:
        if not self.message_id:
            await self.bot.send_message(self.chat_id, text)
            return
        try:
            await self.bot.edit_message_text(text=text, chat_id=self.chat_id, message_id=self.message_id)
        except TelegramBadRequest:
            await self.bot.send_message(self.chat_id, text)


class BotRunner:
    def __init__(self, cfg, store, llm):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.bot = Bot(token=cfg.telegram.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
        self.dispatcher = Dispatcher()
        self.router = Router()
        self.busy: set[int] = set()
        self._register()

    # ── отправка ────────────────────────────────────────────────────────
    async def _send(self, chat_id: int, text: str, reply_markup=None) -> None:
        for part in chunk_text(text, 3800):
            try:
                await self.bot.send_message(
                    chat_id,
                    part or "…",
                    reply_markup=reply_markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except TelegramBadRequest as error:
                if "entit" not in str(error).lower():
                    raise
                logger.warning("HTML не распарсился, отправляю как текст")
                await self.bot.send_message(chat_id, strip_html(part), reply_markup=reply_markup)

    async def _send_parts(self, chat_id: int, parts: list[str]) -> None:
        """Отправляет заранее подготовленные HTML-фрагменты (уже ≤ лимита)."""
        for part in parts:
            if not part or not part.strip():
                continue
            try:
                await self.bot.send_message(
                    chat_id,
                    part,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except TelegramBadRequest as error:
                if "entit" not in str(error).lower():
                    raise
                logger.warning("HTML не распарсился, отправляю как текст")
                await self.bot.send_message(chat_id, strip_html(part))

    async def notify(self, text: str) -> None:
        if not self.cfg.telegram.allowed_chat_ids:
            return
        try:
            await self._send(self.cfg.telegram.allowed_chat_ids[0], text)
        except Exception as error:  # noqa: BLE001
            logger.warning("notify: %s", error)

    # ── команды ─────────────────────────────────────────────────────────
    async def cmd_start(self, message: Message) -> None:
        await message.answer(
            "\n".join(
                [
                    "👋 <b>Я ваш почтовый ИИ-агент</b>",
                    "",
                    "Читаю почту Mail.ru и разбираю договоры, переговоры и контакты. "
                    "Ищу телефоны и адреса — текстом или голосом.",
                    "",
                    f"Ваш chat_id: <code>{message.chat.id}</code>",
                    "",
                    "Начните с «📥 Проверить почту» или просто задайте вопрос.",
                    "",
                    "/help — все команды",
                ]
            ),
            reply_markup=KEYBOARD,
        )

    async def cmd_help(self, message: Message) -> None:
        await message.answer(HELP_TEXT, reply_markup=KEYBOARD)

    async def cmd_id(self, message: Message) -> None:
        await message.answer(f"Ваш chat_id: <code>{message.chat.id}</code>")

    async def cmd_status(self, message: Message) -> None:
        stats = self.store.stats()
        info = describe_config(self.cfg)
        last_sync = self.store.meta_get("last_sync_at")
        await message.answer(
            "\n".join(
                [
                    "⚙️ <b>Статус</b>",
                    "",
                    f"📧 Почта: {escape_html(info['mail'])}",
                    f"🧠 ИИ: {escape_html(info['llm'])}",
                    f"🎤 Голос: {escape_html(info['stt'])}",
                    "",
                    f"📬 Писем: <b>{stats['emails']}</b> · разобрано: {stats['analyzed']}",
                    f"📇 Контактов: {stats['contacts']} · 📄 договоров: {stats['contracts']}",
                    f"📌 Открытых задач: {stats['openTasks']} · ❗ высокий риск: {stats['highRisk']}",
                    "",
                    "🕓 Последняя синхронизация: "
                    + (format_datetime(last_sync, self.cfg.agent.timezone) if last_sync else "ещё не было"),
                ]
            )
        )

    async def cmd_sync(self, message: Message) -> None:
        chat_id = message.chat.id
        if not self.cfg.has.mail:
            await message.answer(
                "📭 Почта не настроена.\n\nВпишите в <code>.env</code>:\n"
                "<code>MAIL_USER=you@yandex.ru</code>\n"
                "<code>MAIL_PASSWORD=пароль_приложения</code>\n\n"
                "Пароль приложения (не обычный пароль от почты):\n"
                "• Яндекс: id.yandex.ru → Безопасность → Пароли приложений; "
                "включите IMAP в Настройки → Почтовые программы.\n"
                "• Mail.ru: Настройки → Безопасность → Пароли для внешних приложений."
            )
            return

        progress = Progress(self.bot, chat_id)
        await progress.begin("🔄 Читаю почту…")
        started = monotonic()

        async def on_progress(payload: dict) -> None:
            if payload.get("phase") == "fetch":
                await progress.update("🔄 Читаю почту…")
            elif payload.get("phase") == "analyze":
                await progress.update(f"🧠 Разбираю письма: {payload['done']}/{payload['total']}")

        try:
            result = await run_sync(self.cfg, self.store, self.llm, on_progress=on_progress)
            self.store.meta_set("last_sync_at", result["finished"])
            lines = [
                "✅ <b>Готово</b>",
                "",
                f"• Новых писем: <b>{result['fetched']}</b>",
                f"• Разобрано ИИ: <b>{result['analyzed']}</b>",
                f"• Дублей пропущено: {result['duplicates']}",
                f"• Ошибок: {result['failed'] + len(result['errors'])}",
                f"• Время: {monotonic() - started:.1f} с",
            ]
            if self.cfg.has.llm:
                lines.extend(["", "Спросите что-нибудь — или нажмите «📊 Сводка»."])
            else:
                lines.extend(
                    ["", "⚠️ ИИ-ключ не задан, работает разбор по правилам. Подробные ответы — с <code>LLM_API_KEY</code>."]
                )
            await progress.finish("\n".join(lines))
        except Exception as error:  # noqa: BLE001 - показываем пользователю причину
            logger.error("Синхронизация не удалась: %s", error)
            await progress.finish(f"❌ Не удалось прочитать почту.\n\n{escape_html(str(error))}")

    async def cmd_digest(self, message: Message, command: CommandObject) -> None:
        try:
            days = max(1, min(365, int(command.args or 7)))
        except (TypeError, ValueError):
            days = 7
        digest = build_digest(self.store, days=days, follow_up_days=self.cfg.agent.follow_up_days)
        await self._send(message.chat.id, digest["text"])

    async def cmd_contacts(self, message: Message, command: CommandObject) -> None:
        result = answer_contacts(self.store, question=command.args or "")
        await self._send(message.chat.id, result["text"])

    async def cmd_contracts(self, message: Message) -> None:
        await self._send(message.chat.id, answer_contracts(self.store)["text"])

    async def cmd_tasks(self, message: Message) -> None:
        await self._send(
            message.chat.id,
            answer_tasks(self.store, follow_up_days=self.cfg.agent.follow_up_days)["text"],
        )

    async def cmd_done(self, message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip().lstrip("#")
        if not raw.isdigit():
            await message.answer("Укажите номер обязательства из /tasks: <code>/done 12</code>")
            return
        if self.store.close_commitment(int(raw)):
            await message.answer(f"✅ Обязательство #{raw} закрыто.")
        else:
            await message.answer(f"Не нашёл открытое обязательство #{raw}. Список — /tasks.")

    async def cmd_show(self, message: Message, command: CommandObject) -> None:
        raw = (command.args or "").strip()
        if not raw.isdigit():
            await message.answer("Укажите номер письма: <code>/show 12</code>")
            return
        email = self.store.get_email(int(raw))
        if not email:
            await message.answer(f"Письмо #{raw} не найдено.")
            return

        lines = [
            f"<b>[#{email['id']}] {escape_html(truncate(email.get('subject') or '(без темы)', 160))}</b>",
            "",
            f"👤 {escape_html(email.get('from_name') or '—')} &lt;{escape_html(email.get('from_addr') or '')}&gt;",
            f"📅 {format_datetime(email.get('sent_at'), self.cfg.agent.timezone)}",
            f"📁 {escape_html(email.get('folder') or '')}",
        ]
        if email.get("kind"):
            lines.append(f"🏷 {kind_label(email['kind'])} · {status_label(email.get('status'))}")
        if email.get("summary"):
            lines.extend(["", f"<i>{escape_html(truncate(email['summary'], 500))}</i>"])
        if email.get("amount"):
            lines.append(f"💰 {format_money(email['amount'], email.get('currency') or 'RUB')}")
        if email.get("deadline"):
            lines.append(f"📅 срок: {format_date(email['deadline'])}")
        if email.get("risks"):
            lines.extend(["", f"❗ Риски: {escape_html('; '.join(email['risks']))}"])
        if email.get("phones"):
            lines.append(f"📞 {', '.join(escape_html(format_phone(p)) for p in email['phones'])}")
        if email.get("addresses"):
            lines.append(f"📍 {escape_html(' | '.join(email['addresses']))}")
        lines.extend(["", "<b>Текст</b>", escape_html(truncate(email.get("body_text") or "", 2500))])
        await self._send(message.chat.id, "\n".join(lines))

    async def cmd_ask(self, message: Message, command: CommandObject) -> None:
        question = (command.args or "").strip()
        if not question:
            await message.answer("Напишите вопрос после команды: <code>/ask какой телефон у Анны?</code>")
            return
        await self.answer_question(message.chat.id, question)

    async def handle_text(self, message: Message) -> None:
        text = (message.text or "").strip()
        route = BUTTON_ROUTES.get(text)
        if route:
            if route == "/sync":
                await self.run_busy(message.chat.id, self.cmd_sync, message)
            elif route == "/digest":
                await self.cmd_digest(message, _FakeCommand())
            elif route == "/contacts":
                await self.cmd_contacts(message, _FakeCommand())
            elif route == "/contracts":
                await self.cmd_contracts(message)
            elif route == "/tasks":
                await self.cmd_tasks(message)
            elif route == "/status":
                await self.cmd_status(message)
            return
        await self.answer_question(message.chat.id, text)

    async def handle_voice(self, message: Message) -> None:
        chat_id = message.chat.id
        media = message.voice or message.audio or message.video_note
        if media is None:
            return

        if not self.cfg.has.stt:
            await message.answer(
                "🎤 Голосовые сообщения требуют распознавания речи.\n\n"
                "Добавьте в <code>.env</code>: <code>OPENAI_API_KEY=sk-...</code> (Whisper) "
                "либо укажите свой <code>STT_BASE_URL</code>."
            )
            return

        if chat_id in self.busy:
            await message.answer("⏳ Ещё работаю над прошлым запросом, секунду…")
            return

        self.busy.add(chat_id)
        try:
            await self.bot.send_chat_action(chat_id, ChatAction.RECORD_VOICE)
            file = await self.bot.get_file(media.file_id)
            buffer = await self.bot.download_file(file.file_path)
            audio = buffer.getvalue() if hasattr(buffer, "getvalue") else bytes(buffer)
            result = await transcribe(self.cfg, audio)
            await self._send(
                chat_id,
                f"🎤 <i>Распознано:</i> {escape_html(truncate(result['text'], 500))}",
            )
            await self.answer_question(chat_id, result["text"])
        except SttError as error:
            await self._send(chat_id, f"🎤 Не смог распознать голосовое.\n\n{escape_html(str(error))}")
        except Exception as error:  # noqa: BLE001
            logger.warning("Голос не обработан: %s", error)
            await self._send(chat_id, f"🎤 Не смог распознать голосовое.\n\n{escape_html(str(error))}")
        finally:
            self.busy.discard(chat_id)

    async def answer_question(self, chat_id: int, question: str) -> None:
        if chat_id in self.busy:
            await self.bot.send_message(chat_id, "⏳ Ещё работаю над прошлым запросом, секунду…")
            return
        self.busy.add(chat_id)
        try:
            await self.bot.send_chat_action(chat_id, ChatAction.TYPING)
            result = await answer_question(self.store, self.cfg, self.llm, question)

            parts: list[str] = []
            if result.get("markdown"):
                # Ответ модели рендерим в безопасный HTML; наши теги не трогаем.
                parts.extend(format_for_telegram(result["markdown"]))
                if result.get("sources_html"):
                    parts.append(result["sources_html"])
            else:
                parts.append(result["text"])

            if result.get("email_ids"):
                parts.append(
                    f"<i>Писем в ответе: {len(result['email_ids'])}. Детали: /show {result['email_ids'][0]}</i>"
                )
            await self._send_parts(chat_id, parts)
        except Exception as error:  # noqa: BLE001 - пользователь должен увидеть причину
            logger.error("Ошибка обработки вопроса: %s", error)
            await self._send(chat_id, f"❌ Ошибка: {escape_html(str(error))}")
        finally:
            self.busy.discard(chat_id)

    async def run_busy(self, chat_id: int, fn, *args) -> None:
        if chat_id in self.busy:
            await self.bot.send_message(chat_id, "⏳ Ещё работаю над прошлым запросом, секунду…")
            return
        self.busy.add(chat_id)
        try:
            await fn(*args)
        finally:
            self.busy.discard(chat_id)

    # ── регистрация ─────────────────────────────────────────────────────
    def _register(self) -> None:
        router = self.router
        router.message.middleware(AccessMiddleware(self.cfg))

        router.message.register(self.cmd_start, CommandStart())
        router.message.register(self.cmd_help, Command("help"))
        router.message.register(self.cmd_id, Command("id"))
        router.message.register(self.cmd_status, Command("status"))
        router.message.register(self.cmd_sync, Command("sync"))
        router.message.register(self.cmd_digest, Command("digest"))
        router.message.register(self.cmd_contacts, Command("contacts"))
        router.message.register(self.cmd_contracts, Command("contracts"))
        router.message.register(self.cmd_tasks, Command("tasks"))
        router.message.register(self.cmd_show, Command("show"))
        router.message.register(self.cmd_done, Command("done"))
        router.message.register(self.cmd_ask, Command("ask"))
        router.message.register(self.handle_voice, F.voice | F.audio | F.video_note)
        router.message.register(self.handle_text, F.text)

        self.dispatcher.include_router(router)

    # ── жизненный цикл ──────────────────────────────────────────────────
    async def run(self) -> None:
        me = await self.bot.get_me()
        logger.info("Бот @%s запущен (long polling)", me.username)
        await self.dispatcher.start_polling(self.bot, allowed_updates=["message", "edited_message"])

    async def stop(self) -> None:
        try:
            await self.dispatcher.stop_polling()
        except Exception:  # noqa: BLE001
            pass
        await self.bot.session.close()
        if self.llm is not None:
            await self.llm.close()


class _FakeCommand:
    """Заглушка CommandObject для кнопок клавиатуры."""

    args = None


def create_bot(cfg, store, llm) -> BotRunner:
    return BotRunner(cfg, store, llm)
