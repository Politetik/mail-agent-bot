"""Консольные команды Python-версии."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from .answer import answer_contacts, answer_contracts, answer_question, answer_tasks, build_digest
from .config import create_config, describe_config
from .demo import plain, run_demo
from .llm import create_llm
from .mail_imap import test_mail_connection
from .db import open_store
from .sync import run_sync
from .util import logger, setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mail-agent",
        description="Почтовый ИИ-агент: консольные команды",
    )
    parser.add_argument("--env", help="путь к .env (по умолчанию ../.env)")
    parser.add_argument("-v", "--verbose", action="store_true", help="подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="проверить настройки и подключения")
    sub.add_parser("sync", help="прочитать почту и разобрать письма").add_argument("--days", type=int)
    ask = sub.add_parser("ask", help="задать вопрос по базе")
    ask.add_argument("question", nargs="+")
    digest = sub.add_parser("digest", help="сводка")
    digest.add_argument("days", nargs="?", type=int, default=7)
    contacts = sub.add_parser("contacts", help="контакты")
    contacts.add_argument("query", nargs="*")
    sub.add_parser("contracts", help="реестр договоров")
    sub.add_parser("tasks", help="что висит: обязательства и зависшие треды")
    sub.add_parser("commitments", help="только обязательства (обещания и просьбы)")
    done = sub.add_parser("done", help="закрыть обязательство по номеру")
    done.add_argument("id", type=int)
    sub.add_parser("stats", help="статистика базы")
    sub.add_parser("demo", help="демо на тестовых письмах")
    sub.add_parser("reset", help="очистить базу")
    sub.add_parser("purge-sent", help="удалить «Отправленные» из базы и отчётов")
    sub.add_parser("reanalyze", help="пересобрать разбор писем заново")
    return parser


async def run_doctor(cfg, store, llm) -> int:
    lines = ["🩺 Проверка окружения", ""]
    info = describe_config(cfg)
    lines.append(f"Python: {sys.version.split()[0]}")
    lines.append(f"База: {store.path}")
    lines.append(f"Почта: {info['mail']}")
    lines.append(f"ИИ: {info['llm']}")
    lines.append(f"Голос: {info['stt']}")
    lines.append(f"Telegram: {info['telegram']}")
    lines.append("")

    failures = 0

    if cfg.has.mail:
        try:
            result = await asyncio.to_thread(test_mail_connection, cfg)
            lines.append(f"✅ IMAP: подключение за {result['ms']} мс, папок {result['mailboxes']}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            lines.append(f"❌ IMAP: {error}")
    else:
        lines.append("⚠️ IMAP: не настроен (MAIL_USER / MAIL_PASSWORD)")

    if cfg.has.llm:
        try:
            reply = await llm.ping()
            lines.append(f"✅ LLM: ответ «{str(reply)[:40]}» ({cfg.llm.model})")
        except Exception as error:  # noqa: BLE001
            failures += 1
            lines.append(f"❌ LLM: {error}")
    else:
        lines.append("⚠️ LLM: ключ не задан — разбор будет по правилам")

    if cfg.has.telegram:
        try:
            import aiohttp

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"https://api.telegram.org/bot{cfg.telegram.token}/getMe", timeout=aiohttp.ClientTimeout(total=15)
                ) as response:
                    data = await response.json()
            if data.get("ok"):
                lines.append(f"✅ Telegram: @{data['result']['username']}")
            else:
                failures += 1
                lines.append(f"❌ Telegram: {data.get('description')}")
        except Exception as error:  # noqa: BLE001
            failures += 1
            lines.append(f"❌ Telegram: {error}")
        lines.append(
            "✅ Доступ: " + ", ".join(str(i) for i in cfg.telegram.allowed_chat_ids)
            if cfg.telegram.allowed_chat_ids
            else "⚠️ ALLOWED_CHAT_IDS пуст — бот всем покажет ваш chat_id и не будет работать"
        )
    else:
        lines.append("⚠️ Telegram: токен не задан")

    stats = store.stats()
    lines.append("")
    lines.append(
        f"📊 База: писем {stats['emails']}, разобрано {stats['analyzed']}, "
        f"контактов {stats['contacts']}, договоров {stats['contracts']}"
    )

    print("\n".join(lines))
    return failures


async def async_main(args) -> int:
    overrides = {}
    if args.command == "demo":
        overrides["DB_PATH"] = str(Path(__file__).resolve().parent.parent / "data" / "demo.db")
    if getattr(args, "days", None) and args.command == "sync":
        overrides["SYNC_DAYS"] = str(args.days)

    cfg = create_config(env_file=args.env, overrides=overrides)
    setup_logging("debug" if args.verbose else cfg.log_level)

    store = open_store(cfg.db.path)
    llm = create_llm(cfg)

    try:
        if args.command == "doctor":
            return 1 if await run_doctor(cfg, store, llm) else 0

        if args.command == "sync":
            if not cfg.has.mail:
                print("❌ Почта не настроена: заполните MAIL_USER и MAIL_PASSWORD в .env", file=sys.stderr)
                return 1

            async def on_progress(payload):
                if payload.get("phase") == "fetch":
                    print("\r📥 читаю почту…", end="", file=sys.stderr, flush=True)
                else:
                    print(f"\r🧠 анализ {payload['done']}/{payload['total']}   ", end="", file=sys.stderr, flush=True)

            result = await run_sync(cfg, store, llm, on_progress=on_progress)
            print(file=sys.stderr)
            print(
                f"✅ Новых: {result['fetched']}, дублей: {result['duplicates']}, "
                f"разобрано: {result['analyzed']}, ошибок: {result['failed'] + len(result['errors'])}"
            )
            for error in result["errors"]:
                print(f"   ⚠️ {error}")
            store.meta_set("last_sync_at", result["finished"])
            return 0

        if args.command == "ask":
            question = " ".join(args.question)
            result = await answer_question(store, cfg, llm, question)
            print(plain(result["text"]))
            return 0

        if args.command in {"tasks", "commitments"}:
            print(
                plain(
                    answer_tasks(
                        store,
                        follow_up_days=cfg.agent.follow_up_days,
                    )["text"]
                )
            )
            return 0

        if args.command == "digest":
            print(
                plain(
                    build_digest(store, days=args.days, follow_up_days=cfg.agent.follow_up_days)["text"]
                )
            )
            return 0

        if args.command == "contacts":
            query = " ".join(args.query) if args.query else ""
            print(plain(answer_contacts(store, question=query)["text"]))
            return 0

        if args.command == "contracts":
            print(plain(answer_contracts(store)["text"]))
            return 0

        if args.command == "done":
            print("✅ Обязательство закрыто." if store.close_commitment(args.id) else f"Не найдено открытое обязательство #{args.id}.")
            return 0

        if args.command == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
            return 0

        if args.command == "demo":
            await run_demo(cfg, store)
            return 0

        if args.command == "purge-sent":
            removed = store.purge_sent()
            store.rebuild_threads()
            print(f"🗑 Удалено отправленных писем: {removed}. Треды пересчитаны.")
            return 0

        if args.command == "reset":
            store.reset()
            print("🗑 База очищена.")
            return 0

        if args.command == "reanalyze":
            from .sync import analyze_pending

            store.reset_analyses()
            print("♻️  Старый разбор удалён, разбираю письма заново…")

            async def progress(payload):
                if payload.get("phase") == "analyze":
                    print(f"\r🧠 {payload['done']}/{payload['total']}   ", end="", file=sys.stderr, flush=True)

            result = await analyze_pending(cfg, store, llm, limit=10000, on_progress=progress)
            print(file=sys.stderr)
            print(f"✅ Разобрано: {result['analyzed']}, ошибок: {result['failed']}")
            return 0

        return 1
    finally:
        await llm.close()
        store.close()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(async_main(args))
    except KeyboardInterrupt:
        return 130
    except Exception as error:  # noqa: BLE001
        logger.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
