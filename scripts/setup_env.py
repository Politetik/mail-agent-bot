#!/usr/bin/env python3
"""Мастер настройки .env: спрашивает значения и аккуратно записывает их в файл.

Секреты вводятся без эха (getpass) и не попадают в историю команд.
Запуск:  python scripts/setup_env.py
"""

from __future__ import annotations

import argparse
import getpass
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV = ROOT / ".env"
EXAMPLE_ENV = ROOT / ".env.example"

TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

KEYS = (
    "TELEGRAM_BOT_TOKEN",
    "ALLOWED_CHAT_IDS",
    "MAIL_USER",
    "MAIL_PASSWORD",
    "LLM_API_KEY",
    "STT_API_KEY",
    "AUTO_SYNC_MIN",
)


def _force_utf8() -> None:
    """Чтобы кириллица и эмодзи не роняли скрипт в консоли с cp1251."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - старые обёртки могут не поддерживать
            pass


def mask(value: str) -> str:
    if not value:
        return "— не задано"
    if len(value) <= 10:
        return "***"
    return f"{value[:4]}…{value[-2:]}"


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            values[key] = value.strip().strip("\"'")
    return values


def set_value(text: str, key: str, value: str) -> str:
    pattern = re.compile(rf"(?m)^{re.escape(key)}=.*$")
    if pattern.search(text):
        # lambda — чтобы «\» и «$» в значении не трактовались как подстановки
        return pattern.sub(lambda _m: f"{key}={value}", text, count=1)
    return text.rstrip() + f"\n{key}={value}\n"


def ask(prompt: str, current: str = "", secret: bool = False, keep_hint: bool = True) -> str:
    suffix = ""
    if keep_hint and current:
        suffix = f" [{mask(current)} — Enter, чтобы оставить]"
    elif keep_hint:
        suffix = " [Enter — пропустить]"

    while True:
        if secret:
            value = getpass.getpass(f"{prompt}{suffix}: ")
        else:
            value = input(f"{prompt}{suffix}: ").strip()
        if not value and current:
            return current
        return value


def run_doctor(env_path: Path) -> None:
    python_dir = ROOT / "python"
    venv = python_dir / ".venv" / "Scripts" / "python.exe"
    if not venv.is_file():
        venv = python_dir / ".venv" / "bin" / "python"
    executable = str(venv) if venv.is_file() else sys.executable

    print("\n🩺 Проверяю подключения…\n")
    try:
        subprocess.run(
            [executable, "-m", "app.cli", "--env", str(env_path), "doctor"],
            cwd=python_dir,
            check=False,
        )
    except Exception as error:  # noqa: BLE001
        print(f"Не удалось запустить doctor: {error}")


def main(argv=None) -> int:
    _force_utf8()
    parser = argparse.ArgumentParser(description="Мастер настройки .env почтового ИИ-агента")
    parser.add_argument("--out", default=str(DEFAULT_ENV), help="куда писать .env")
    parser.add_argument("--telegram-token", default="")
    parser.add_argument("--allowed-chat-ids", default="")
    parser.add_argument("--mail-user", default="")
    parser.add_argument("--mail-password", default="")
    parser.add_argument("--llm-api-key", default="")
    parser.add_argument("--stt-api-key", default="")
    parser.add_argument("--auto-sync-min", type=int, default=15)
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--no-doctor", action="store_true")
    args = parser.parse_args(argv)

    out_path = Path(args.out).expanduser().resolve()
    base_path = out_path if out_path.is_file() else EXAMPLE_ENV
    if not base_path.is_file():
        print(f"❌ Не найден шаблон {EXAMPLE_ENV}", file=sys.stderr)
        return 1

    text = base_path.read_text(encoding="utf-8-sig")
    current = parse_env(text)

    print()
    print("┌─────────────────────────────────────────────┐")
    print("│   Настройка .env для почтового ИИ-агента    │")
    print("└─────────────────────────────────────────────┘")
    print()

    if args.non_interactive:
        values = {
            "TELEGRAM_BOT_TOKEN": args.telegram_token or current.get("TELEGRAM_BOT_TOKEN", ""),
            "ALLOWED_CHAT_IDS": args.allowed_chat_ids or current.get("ALLOWED_CHAT_IDS", ""),
            "MAIL_USER": args.mail_user or current.get("MAIL_USER", ""),
            "MAIL_PASSWORD": args.mail_password or current.get("MAIL_PASSWORD", ""),
            "LLM_API_KEY": args.llm_api_key or current.get("LLM_API_KEY", ""),
            "STT_API_KEY": args.stt_api_key or current.get("STT_API_KEY", ""),
            "AUTO_SYNC_MIN": str(args.auto_sync_min),
        }
    else:
        print("1) Токен Telegram-бота: @BotFather → /newbot")
        print("   Ввод скрыт — вставьте токен и нажмите Enter.\n")
        token = ask("   Токен", current.get("TELEGRAM_BOT_TOKEN", ""), secret=True)

        print("\n2) Ваш chat_id (необязательно). Если не знаете — пропустите:")
        print("   бот покажет его в ответ на /start.\n")
        chat_ids = ask("   chat_id", current.get("ALLOWED_CHAT_IDS", ""))

        print("\n3) Адрес почты Mail.ru:\n")
        mail_user = ask("   Адрес", current.get("MAIL_USER", ""))

        print("\n4) Пароль ДЛЯ ВНЕШНИХ ПРИЛОЖЕНИЙ (не обычный пароль от почты).")
        print("   Mail.ru → Настройки → Безопасность → Пароли для внешних приложений.\n")
        mail_password = ask("   Пароль", current.get("MAIL_PASSWORD", ""), secret=True)

        print("\n5) Ключ ИИ (необязательно, но рекомендуется):")
        print("   DeepSeek → platform.deepseek.com → API keys.\n")
        llm_key = ask("   LLM_API_KEY", current.get("LLM_API_KEY", ""), secret=True)

        print("\n6) Ключ OpenAI для голоса (необязательно).\n")
        stt_key = ask("   OPENAI_API_KEY", current.get("STT_API_KEY", ""), secret=True)

        print("\n7) Автопроверка почты, минут (0 — выключить):\n")
        raw = ask("   AUTO_SYNC_MIN", current.get("AUTO_SYNC_MIN", "15"), keep_hint=False)
        try:
            auto_sync = max(0, int(raw or 15))
        except ValueError:
            auto_sync = 15

        values = {
            "TELEGRAM_BOT_TOKEN": token,
            "ALLOWED_CHAT_IDS": chat_ids,
            "MAIL_USER": mail_user,
            "MAIL_PASSWORD": mail_password,
            "LLM_API_KEY": llm_key,
            "STT_API_KEY": stt_key,
            "AUTO_SYNC_MIN": str(auto_sync),
        }

    # ── проверки ────────────────────────────────────────────────────────
    warnings: list[str] = []
    token = values["TELEGRAM_BOT_TOKEN"]
    if not token:
        warnings.append("Токен Telegram не задан — бот не запустится.")
    elif not TOKEN_RE.match(token):
        warnings.append("Токен не похож на настоящий (ожидается 123456789:AA…). Записан как есть.")

    if values["MAIL_USER"] and not EMAIL_RE.match(values["MAIL_USER"]):
        warnings.append(f"Адрес «{values['MAIL_USER']}» выглядит подозрительно — нужен name@mail.ru.")
    if not values["MAIL_USER"]:
        warnings.append("Почта не настроена — /sync не сможет читать письма.")
    if not values["MAIL_PASSWORD"]:
        warnings.append("Пароль почты не задан — IMAP не подключится.")

    for key in KEYS:
        text = set_value(text, key, values.get(key, ""))

    out_path.write_text(text, encoding="utf-8", newline="\n")

    print(f"\n✅ Записано в {out_path}\n")
    print("Проверьте себя:")
    print(f"   Telegram: {mask(values['TELEGRAM_BOT_TOKEN'])}")
    print(f"   Почта:    {values['MAIL_USER'] or '— не задано'}")
    print(f"   Пароль:   {'задан (' + str(len(values['MAIL_PASSWORD'])) + ' симв.)' if values['MAIL_PASSWORD'] else '— не задано'}")
    print(f"   ИИ:       {mask(values['LLM_API_KEY'])}")
    print(f"   Голос:    {mask(values['STT_API_KEY'])}")
    print(f"   Автоsync: {values['AUTO_SYNC_MIN']} мин")

    for warning in warnings:
        print(f"\n⚠️  {warning}")

    print("\nДальше:  python -m app.main   (из папки python)\n")

    if not args.non_interactive and not args.no_doctor:
        answer = input("Проверить подключения сейчас? (y/n): ").strip().lower()
        if answer.startswith(("y", "д")):
            run_doctor(out_path)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
