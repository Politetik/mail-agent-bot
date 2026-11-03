"""Конвейер: скачать новую почту → разобрать → сохранить производные записи."""

from __future__ import annotations

import re

from .analyze import analyze_email, email_is_outgoing
from .mail_imap import fetch_new_emails_async
from .util import logger, now_iso


AUTOMATED_SENDER_RE = re.compile(
    r"(^|[._-])(no-?reply|noreply|do-?not-?reply|robot|bot|notification|notify|mailer|daemon|agent|support)([._-]|$)",
    re.IGNORECASE,
)


def is_automated_sender(address: str | None) -> bool:
    local = str(address or "").lower().split("@")[0]
    return bool(AUTOMATED_SENDER_RE.search(local))


def _is_own_mailbox(contact_email: str | None, account: str | None) -> bool:
    """Письма самому себе не должны превращаться в «контакт»."""
    if not contact_email or not account:
        return False
    contact = contact_email.lower().strip()
    owner = account.lower().strip()
    if contact == owner:
        return True
    # saturninfo@ya.ru и saturninfo@yandex.ru — один и тот же ящик.
    return contact.split("@")[0] == owner.split("@")[0]


def persist_derived(store, email_row: dict, analysis: dict, account: str | None = None) -> None:
    """Сохраняет контакты, договоры и задачи, вытекающие из анализа письма."""
    contacts = analysis.get("contacts") or []

    for contact in contacts:
        if _is_own_mailbox(contact.get("email"), account):
            # Это адрес владельца ящика — не показываем его как контакт человека.
            if not contact.get("phone"):
                continue
            contact = {**contact, "email": None}
        # Запись без имени, телефона и почты — это не контакт, а обрывок адреса.
        if not contact.get("name") and not contact.get("phone") and not contact.get("email"):
            continue
        store.upsert_contact(
            {
                "email_id": email_row.get("id"),
                "name": contact.get("name"),
                "position": contact.get("position"),
                "company": contact.get("company"),
                "phone": contact.get("phone"),
                "email": contact.get("email"),
                "address": contact.get("address"),
                "inn": contact.get("inn"),
                "context": analysis.get("summary"),
                "confidence": analysis.get("confidence"),
            }
        )

    if not contacts and (analysis.get("phones") or analysis.get("addresses")):
        phones = analysis.get("phones") or [None]
        for phone in phones:
            store.upsert_contact(
                {
                    "email_id": email_row.get("id"),
                    "name": email_row.get("from_name"),
                    "phone": phone,
                    "email": email_row.get("from_addr"),
                    "address": (analysis.get("addresses") or [None])[0],
                    "company": analysis.get("counterparty"),
                    "context": analysis.get("summary"),
                }
            )

    if analysis.get("kind") == "contract":
        store.upsert_contract(
            {
                "email_id": email_row.get("id"),
                "number": analysis.get("contract_number"),
                "counterparty": analysis.get("counterparty"),
                "subject": email_row.get("subject"),
                "amount": analysis.get("amount"),
                "currency": analysis.get("currency") or "RUB",
                "end_date": analysis.get("deadline"),
                "status": analysis.get("status") or "new",
                "risks": analysis.get("risks") or [],
            }
        )

    commitments = analysis.get("commitments") or []
    # Уведомления роботов (заказы, коды, статусы) — не обязательства.
    automated = is_automated_sender(email_row.get("from_addr"))
    if not commitments and not automated:
        # Страховка: старые разборы без commitments — берём просьбы из asks.
        direction = "them" if email_is_outgoing(email_row) else "me"
        commitments = [{"direction": direction, "text": ask, "due": analysis.get("deadline")} for ask in analysis.get("asks") or []]

    for commitment in commitments if not automated else []:
        store.upsert_commitment(
            {
                "email_id": email_row.get("id"),
                "thread_key": email_row.get("thread_key"),
                "direction": commitment.get("direction"),
                "text": commitment.get("text"),
                "due": commitment.get("due"),
                "source": analysis.get("engine"),
                "counterparty": analysis.get("counterparty"),
            }
        )


async def analyze_pending(cfg, store, llm, limit: int | None = None, on_progress=None) -> dict:
    cap = max(0, int(limit if limit is not None else cfg.sync.analyze_limit))
    pending = store.emails_without_analysis(cap)
    analyzed = 0
    failed = 0

    if on_progress:
        await on_progress({"phase": "analyze", "total": len(pending), "done": 0})

    for index, row in enumerate(pending, start=1):
        try:
            analysis = await analyze_email(row, llm=llm, model=cfg.llm.model)
            store.save_analysis(row["id"], analysis)
            persist_derived(store, row, analysis, account=cfg.mail.user)
            analyzed += 1
        except Exception as error:  # noqa: BLE001 - продолжаем остальные письма
            failed += 1
            logger.warning("Не удалось разобрать письмо #%s: %s", row.get("id"), error)
        if on_progress:
            await on_progress({"phase": "analyze", "total": len(pending), "done": index})

    return {"analyzed": analyzed, "failed": failed}


async def run_sync(cfg, store, llm, analyze: bool = True, on_progress=None) -> dict:
    started = now_iso()

    async def progress(payload: dict) -> None:
        if on_progress is None:
            return
        try:
            await on_progress(payload)
        except Exception as error:  # noqa: BLE001
            logger.warning("on_progress: %s", error)

    await progress({"phase": "fetch", "done": 0, "total": 0, "folder": None})
    mail = await fetch_new_emails_async(cfg, store)

    analysis = {"analyzed": 0, "failed": 0}
    if analyze:
        analysis = await analyze_pending(cfg, store, llm, on_progress=on_progress)

    result = {
        "started": started,
        "finished": now_iso(),
        "fetched": mail["fetched"],
        "duplicates": mail["duplicates"],
        "folders": mail["folders"],
        "errors": mail["errors"],
        "analyzed": analysis["analyzed"],
        "failed": analysis["failed"],
    }
    logger.info(
        "Синхронизация: новых %s, дублей %s, разобрано %s, ошибок %s",
        result["fetched"],
        result["duplicates"],
        result["analyzed"],
        result["failed"],
    )
    return result
