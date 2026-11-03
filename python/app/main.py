"""Точка входа Python-версии: long polling + автосинхронизация."""

from __future__ import annotations

import asyncio
import contextlib
import sys

from .bot import create_bot
from .config import create_config, describe_config
from .db import open_store
from .llm import create_llm
from .sync import run_sync
from .util import logger, setup_logging

BANNER = r"""
  ┌─────────────────────────────────────────────┐
  │   Почтовый ИИ-агент для Telegram и Mail.ru  │
  │                 (Python)                    │
  └─────────────────────────────────────────────┘
"""


async def auto_sync_loop(cfg, store, llm, runner) -> None:
    interval = max(5, cfg.sync.interval_min) * 60
    while True:
        await asyncio.sleep(interval)
        try:
            result = await run_sync(cfg, store, llm)
            store.meta_set("last_sync_at", result["finished"])
            if result["fetched"] > 0:
                await runner.notify(
                    f"📬 Автопроверка: {result['fetched']} новых писем, разобрано {result['analyzed']}."
                )
        except Exception as error:  # noqa: BLE001 - цикл не должен умирать
            logger.error("Автосинхронизация: %s", error)


async def run() -> int:
    cfg = create_config()
    setup_logging(cfg.log_level)

    print(BANNER)

    if not cfg.has.telegram:
        print("❌ Не задан TELEGRAM_BOT_TOKEN.")
        print("")
        print("1. Откройте @BotFather в Telegram → /newbot")
        print("2. Скопируйте токен в файл .env:")
        print("   TELEGRAM_BOT_TOKEN=123456789:AA...")
        print("3. Запустите снова: python -m app.main")
        return 1

    info = describe_config(cfg)
    print(f"  Почта:    {info['mail']}")
    print(f"  ИИ:       {info['llm']}")
    print(f"  Голос:    {info['stt']}")
    print(f"  Telegram: {info['telegram']}")
    print(f"  База:     {info['db']}")
    print("")

    if not cfg.has.mail:
        logger.warning("Почта не настроена: /sync не сможет читать письма (MAIL_USER/MAIL_PASSWORD).")
    if not cfg.has.llm:
        logger.warning("ИИ-ключ не задан: разбор писем и ответы будут по правилам (LLM_API_KEY).")
    if not cfg.telegram.allowed_chat_ids:
        logger.warning("ALLOWED_CHAT_IDS пуст: напишите боту /start, он покажет chat_id — добавьте его в .env.")

    store = open_store(cfg.db.path)
    llm = create_llm(cfg)
    runner = create_bot(cfg, store, llm)

    auto_task: asyncio.Task | None = None
    if cfg.sync.interval_min > 0 and cfg.has.mail:
        auto_task = asyncio.create_task(auto_sync_loop(cfg, store, llm, runner))
        logger.info("Автосинхронизация каждые %s мин", cfg.sync.interval_min)

    try:
        await runner.run()
    finally:
        if auto_task:
            auto_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await auto_task
        await runner.stop()
        store.close()
    return 0


def main() -> int:
    try:
        return asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("Остановлено пользователем")
        return 0
    except Exception as error:  # noqa: BLE001
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    sys.exit(main())
