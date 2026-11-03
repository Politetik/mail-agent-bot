"""Хранилище: SQLite + FTS5 с поддержкой кириллицы."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

from .util import logger, now_iso

SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

CREATE TABLE IF NOT EXISTS emails (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  account        TEXT NOT NULL,
  folder         TEXT NOT NULL,
  uid            INTEGER NOT NULL,
  message_id     TEXT,
  in_reply_to    TEXT,
  thread_key     TEXT,
  from_name      TEXT,
  from_addr      TEXT,
  to_addrs       TEXT,
  cc_addrs       TEXT,
  subject        TEXT,
  sent_at        TEXT,
  body_text      TEXT NOT NULL,
  snippet        TEXT,
  attachments    TEXT,
  is_bulk        INTEGER NOT NULL DEFAULT 0,
  fetched_at     TEXT NOT NULL,
  UNIQUE (account, folder, uid)
);
CREATE INDEX IF NOT EXISTS idx_emails_sent ON emails (sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_emails_from ON emails (from_addr);
CREATE INDEX IF NOT EXISTS idx_emails_fetched ON emails (fetched_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5 (
  subject, body_text, from_name, from_addr,
  content = 'emails',
  content_rowid = 'id',
  tokenize = 'unicode61 remove_diacritics 2'
);

CREATE TRIGGER IF NOT EXISTS emails_ai AFTER INSERT ON emails BEGIN
  INSERT INTO emails_fts (rowid, subject, body_text, from_name, from_addr)
  VALUES (new.id, new.subject, new.body_text, new.from_name, new.from_addr);
END;
CREATE TRIGGER IF NOT EXISTS emails_ad AFTER DELETE ON emails BEGIN
  INSERT INTO emails_fts (emails_fts, rowid, subject, body_text, from_name, from_addr)
  VALUES ('delete', old.id, old.subject, old.body_text, old.from_name, old.from_addr);
END;
CREATE TRIGGER IF NOT EXISTS emails_au AFTER UPDATE ON emails BEGIN
  INSERT INTO emails_fts (emails_fts, rowid, subject, body_text, from_name, from_addr)
  VALUES ('delete', old.id, old.subject, old.body_text, old.from_name, old.from_addr);
  INSERT INTO emails_fts (rowid, subject, body_text, from_name, from_addr)
  VALUES (new.id, new.subject, new.body_text, new.from_name, new.from_addr);
END;

CREATE TABLE IF NOT EXISTS analyses (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id        INTEGER NOT NULL REFERENCES emails (id) ON DELETE CASCADE,
  kind            TEXT NOT NULL DEFAULT 'other',
  summary         TEXT,
  counterparty    TEXT,
  contract_number TEXT,
  amount          REAL,
  currency        TEXT,
  deadline        TEXT,
  status          TEXT,
  risk_level      TEXT,
  risks           TEXT,
  asks            TEXT,
  contacts        TEXT,
  phones          TEXT,
  addresses       TEXT,
  confidence      REAL,
  engine          TEXT,
  model           TEXT,
  created_at      TEXT NOT NULL,
  UNIQUE (email_id)
);
CREATE INDEX IF NOT EXISTS idx_analyses_kind ON analyses (kind);

CREATE TABLE IF NOT EXISTS contacts (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id   INTEGER REFERENCES emails (id) ON DELETE CASCADE,
  name       TEXT,
  position   TEXT,
  company    TEXT,
  phone      TEXT,
  email      TEXT,
  address    TEXT,
  inn        TEXT,
  context    TEXT,
  confidence REAL,
  created_at TEXT NOT NULL,
  dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_contacts_phone ON contacts (phone);
CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts (name);

CREATE TABLE IF NOT EXISTS contracts (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id     INTEGER REFERENCES emails (id) ON DELETE CASCADE,
  number       TEXT,
  counterparty TEXT,
  subject      TEXT,
  amount       REAL,
  currency     TEXT,
  start_date   TEXT,
  end_date     TEXT,
  status       TEXT,
  risks        TEXT,
  created_at   TEXT NOT NULL,
  dedupe_key   TEXT UNIQUE
);

CREATE TABLE IF NOT EXISTS tasks (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id   INTEGER REFERENCES emails (id) ON DELETE CASCADE,
  text       TEXT NOT NULL,
  due        TEXT,
  status     TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks (status);

CREATE TABLE IF NOT EXISTS sync_state (
  account     TEXT NOT NULL,
  folder      TEXT NOT NULL,
  uidvalidity TEXT,
  last_uid    INTEGER NOT NULL DEFAULT 0,
  last_run    TEXT,
  PRIMARY KEY (account, folder)
);

-- Переписка: агрегат по thread_key для статусов «кто кому должен».
CREATE TABLE IF NOT EXISTS threads (
  thread_key      TEXT PRIMARY KEY,
  subject         TEXT,
  counterparty    TEXT,
  message_count   INTEGER NOT NULL DEFAULT 0,
  last_inbound_at TEXT,
  last_outbound_at TEXT,
  last_message_at TEXT,
  status          TEXT NOT NULL DEFAULT 'new',
  updated_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_threads_last ON threads (last_message_at DESC);

-- Обязательства: обещания и просьбы с направлением и сроком.
CREATE TABLE IF NOT EXISTS commitments (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  email_id     INTEGER REFERENCES emails (id) ON DELETE CASCADE,
  thread_key   TEXT,
  direction    TEXT NOT NULL CHECK (direction IN ('me', 'them')),
  text         TEXT NOT NULL,
  due          TEXT,
  status       TEXT NOT NULL DEFAULT 'open',
  source       TEXT,
  counterparty TEXT,
  created_at   TEXT NOT NULL,
  updated_at   TEXT,
  dedupe_key   TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_commitments_status ON commitments (status, due);
CREATE INDEX IF NOT EXISTS idx_commitments_thread ON commitments (thread_key);
"""

SELECT_EMAIL = """
  SELECT e.id, e.account, e.folder, e.uid, e.message_id, e.from_name, e.from_addr,
         e.to_addrs, e.subject, e.sent_at, e.snippet, e.is_bulk, e.attachments,
         a.kind, a.summary, a.counterparty, a.contract_number, a.amount, a.currency,
         a.deadline, a.status, a.risk_level, a.risks, a.asks, a.phones, a.addresses,
         a.contacts, a.confidence, a.engine
    FROM emails e
    LEFT JOIN analyses a ON a.email_id = e.id
"""

STOPWORDS = {
    "и", "в", "во", "на", "с", "со", "по", "к", "ко", "о", "об", "от", "до", "за", "из",
    "не", "ни", "но", "или", "что", "как", "где", "кто", "мне", "все", "всё",
    "это", "этот", "эта", "был", "была", "были", "есть", "the", "a", "an", "of", "to",
    "in", "is", "are", "for", "and", "or", "what", "who", "where",
}

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def tokenize(text) -> list[str]:
    return [t for t in (m.group(0).lower() for m in TOKEN_RE.finditer(str(text or ""))) if len(t) >= 2 and t not in STOPWORDS]


def build_fts_query(text) -> str | None:
    tokens = tokenize(text)[:12]
    if not tokens:
        return None
    return " OR ".join(f'"{t.replace(chr(34), chr(34) * 2)}"*' for t in tokens)


def _hydrate(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    for key in ("risks", "asks", "phones", "addresses", "contacts", "attachments"):
        raw = data.get(key)
        if not raw:
            data[key] = []
            continue
        try:
            data[key] = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            data[key] = []
    return data


def _json(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return None


class Store:
    def __init__(self, path: str | Path):
        self.path = str(Path(path).expanduser().resolve())
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self.conn.execute("PRAGMA synchronous = NORMAL")
        # Каскадные удаления и FTS-триггеры могут требовать временных файлов,
        # а рабочая папка/OneDrive не всегда это позволяют — держим всё в памяти.
        self.conn.execute("PRAGMA temp_store = MEMORY")
        # Встроенный lower()/LIKE в SQLite понимают только ASCII.
        self.conn.create_function("ru_lower", 1, lambda s: (s or "").lower(), deterministic=True)
        self.conn.executescript(SCHEMA)

        version = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self.conn.execute("INSERT INTO emails_fts (emails_fts) VALUES ('rebuild')")
        if version < 2:
            self._migrate_v2()
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _migrate_v2(self) -> None:
        """Создаёт агрегаты тредов и переносит старые задачи в обязательства."""
        with self.tx():
            self.conn.execute(
                """INSERT OR IGNORE INTO threads
                     (thread_key, subject, counterparty, message_count,
                      last_inbound_at, last_outbound_at, last_message_at, updated_at)
                   SELECT e.thread_key,
                          (SELECT subject FROM emails x WHERE x.thread_key = e.thread_key
                            ORDER BY x.sent_at DESC LIMIT 1),
                          (SELECT from_name FROM emails x
                            WHERE x.thread_key = e.thread_key AND ru_lower(x.from_addr) <> ru_lower(x.account)
                            ORDER BY x.sent_at DESC LIMIT 1),
                          COUNT(*),
                          MAX(CASE WHEN ru_lower(e.from_addr) = ru_lower(e.account) THEN NULL ELSE e.sent_at END),
                          MAX(CASE WHEN ru_lower(e.from_addr) = ru_lower(e.account) THEN e.sent_at ELSE NULL END),
                          MAX(e.sent_at),
                          datetime('now')
                     FROM emails e
                    WHERE e.thread_key IS NOT NULL
                    GROUP BY e.thread_key"""
            )
            has_tasks = self.conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()
            if has_tasks:
                self.conn.execute(
                    """INSERT OR IGNORE INTO commitments
                         (email_id, thread_key, direction, text, due, status, source, created_at, dedupe_key)
                       SELECT t.email_id, e.thread_key, 'me', t.text, t.due,
                              CASE WHEN t.status = 'open' THEN 'open' ELSE 'done' END,
                              'migrated', t.created_at, 'legacy|' || t.id
                         FROM tasks t LEFT JOIN emails e ON e.id = t.email_id"""
                )
        logger.info("Миграция схемы v2: треды и обязательства созданы")

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def tx(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        else:
            self.conn.execute("COMMIT")

    # ── meta ────────────────────────────────────────────────────────────
    def meta_get(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def meta_set(self, key: str, value) -> None:
        self.conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    # ── emails ──────────────────────────────────────────────────────────
    def insert_email(self, row: dict) -> tuple[int, bool]:
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO emails
               (account, folder, uid, message_id, in_reply_to, thread_key, from_name, from_addr,
                to_addrs, cc_addrs, subject, sent_at, body_text, snippet, attachments, is_bulk, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                row.get("account"),
                row.get("folder"),
                row.get("uid"),
                row.get("message_id"),
                row.get("in_reply_to"),
                row.get("thread_key"),
                row.get("from_name"),
                row.get("from_addr"),
                row.get("to_addrs"),
                row.get("cc_addrs"),
                row.get("subject"),
                row.get("sent_at"),
                row.get("body_text") or "",
                row.get("snippet"),
                _json(row.get("attachments")),
                1 if row.get("is_bulk") else 0,
                row.get("fetched_at") or now_iso(),
            ),
        )
        found = self.conn.execute(
            "SELECT id FROM emails WHERE account = ? AND folder = ? AND uid = ?",
            (row.get("account"), row.get("folder"), row.get("uid")),
        ).fetchone()
        if cursor.rowcount > 0:
            try:
                self._touch_thread(row)
            except sqlite3.Error as error:  # агрегат не должен ломать импорт письма
                logger.warning("Не удалось обновить тред: %s", error)
        return (int(found["id"]) if found else 0), cursor.rowcount > 0

    def _touch_thread(self, row: dict) -> None:
        key = row.get("thread_key")
        if not key:
            return
        account = str(row.get("account") or "").lower()
        sender = str(row.get("from_addr") or "").lower()
        outbound = bool(account) and sender == account
        sent_at = row.get("sent_at")
        self.conn.execute(
            """INSERT INTO threads
                 (thread_key, subject, counterparty, message_count,
                  last_inbound_at, last_outbound_at, last_message_at, updated_at)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?)
               ON CONFLICT (thread_key) DO UPDATE SET
                 subject = COALESCE(threads.subject, excluded.subject),
                 counterparty = COALESCE(threads.counterparty, excluded.counterparty),
                 message_count = threads.message_count + 1,
                 last_inbound_at = COALESCE(excluded.last_inbound_at, threads.last_inbound_at),
                 last_outbound_at = COALESCE(excluded.last_outbound_at, threads.last_outbound_at),
                 last_message_at = CASE
                   WHEN COALESCE(excluded.last_message_at, '') >= COALESCE(threads.last_message_at, '')
                   THEN excluded.last_message_at ELSE threads.last_message_at END,
                 updated_at = excluded.updated_at""",
            (
                key,
                row.get("subject"),
                None if outbound else (row.get("from_name") or row.get("from_addr")),
                None if outbound else sent_at,
                sent_at if outbound else None,
                sent_at,
                now_iso(),
            ),
        )

    def get_email(self, email_id: int) -> dict | None:
        return _hydrate(self.conn.execute(f"{SELECT_EMAIL} WHERE e.id = ?", (email_id,)).fetchone())

    def emails_without_analysis(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT * FROM emails e
                WHERE NOT EXISTS (SELECT 1 FROM analyses a WHERE a.email_id = e.id)
                  AND e.is_bulk = 0
                ORDER BY e.sent_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── analyses ────────────────────────────────────────────────────────
    def get_analysis(self, email_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM analyses WHERE email_id = ?", (email_id,)).fetchone()
        return dict(row) if row else None

    def save_analysis(self, email_id: int, analysis: dict) -> None:
        prev = self.get_analysis(email_id) or {}

        def pick(key, default=None):
            value = analysis.get(key)
            if value is not None:
                return value
            return prev.get(key, default)

        def pick_json(key):
            value = analysis.get(key)
            if value is not None:
                return json.loads(prev[key]) if isinstance(value, str) else value
            raw = prev.get(key)
            if isinstance(raw, str):
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return []
            return raw or []

        self.conn.execute(
            """INSERT INTO analyses
              (email_id, kind, summary, counterparty, contract_number, amount, currency, deadline,
               status, risk_level, risks, asks, contacts, phones, addresses, confidence, engine, model, created_at)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
             ON CONFLICT (email_id) DO UPDATE SET
               kind = excluded.kind, summary = excluded.summary, counterparty = excluded.counterparty,
               contract_number = excluded.contract_number, amount = excluded.amount, currency = excluded.currency,
               deadline = excluded.deadline, status = excluded.status, risk_level = excluded.risk_level,
               risks = excluded.risks, asks = excluded.asks, contacts = excluded.contacts,
               phones = excluded.phones, addresses = excluded.addresses, confidence = excluded.confidence,
               engine = excluded.engine, model = excluded.model, created_at = excluded.created_at""",
            (
                email_id,
                pick("kind", "other"),
                pick("summary"),
                pick("counterparty"),
                pick("contract_number"),
                pick("amount"),
                pick("currency", "RUB"),
                pick("deadline"),
                pick("status", "new"),
                pick("risk_level", "low"),
                _json(pick_json("risks")),
                _json(pick_json("asks")),
                _json(pick_json("contacts")),
                _json(pick_json("phones")),
                _json(pick_json("addresses")),
                pick("confidence"),
                pick("engine", "rules"),
                pick("model"),
                now_iso(),
            ),
        )

    # ── производные записи ──────────────────────────────────────────────
    def upsert_contact(self, contact: dict) -> bool:
        key = "|".join(str(contact.get(k) or "").strip().lower() for k in ("name", "phone", "email"))
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO contacts
               (email_id, name, position, company, phone, email, address, inn, context, confidence, created_at, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contact.get("email_id"),
                contact.get("name"),
                contact.get("position"),
                contact.get("company"),
                contact.get("phone"),
                contact.get("email"),
                contact.get("address"),
                contact.get("inn"),
                contact.get("context"),
                contact.get("confidence"),
                now_iso(),
                key,
            ),
        )
        if cursor.rowcount == 0:
            self.conn.execute(
                """UPDATE contacts SET
                     address = COALESCE(address, ?), company = COALESCE(company, ?),
                     position = COALESCE(position, ?), inn = COALESCE(inn, ?)
                   WHERE dedupe_key = ?""",
                (contact.get("address"), contact.get("company"), contact.get("position"), contact.get("inn"), key),
            )
            return False
        return True

    def upsert_contract(self, contract: dict) -> bool:
        number = contract.get("number")
        key = (
            "n:" + re.sub(r"\s+", "", str(number).strip().lower())
            if number
            else f"e:{contract.get('email_id') or 'x'}|{str(contract.get('subject') or '').strip().lower()}"
        )
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO contracts
               (email_id, number, counterparty, subject, amount, currency, start_date, end_date, status, risks, created_at, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.get("email_id"),
                number,
                contract.get("counterparty"),
                contract.get("subject"),
                contract.get("amount"),
                contract.get("currency") or "RUB",
                contract.get("start_date"),
                contract.get("end_date"),
                contract.get("status") or "new",
                _json(contract.get("risks") or []),
                now_iso(),
                key,
            ),
        )
        if cursor.rowcount == 0:
            self.conn.execute(
                """UPDATE contracts SET
                     amount = COALESCE(amount, ?),
                     end_date = COALESCE(end_date, ?),
                     risks = CASE WHEN risks IS NULL OR risks = '[]' THEN ? ELSE risks END
                   WHERE dedupe_key = ?""",
                (contract.get("amount"), contract.get("end_date"), _json(contract.get("risks") or []), key),
            )
            return False
        return True

    def add_task(self, task: dict) -> bool:
        key = "|".join(str(task.get(k) or "").strip().lower() for k in ("email_id", "text"))
        cursor = self.conn.execute(
            "INSERT OR IGNORE INTO tasks (email_id, text, due, status, created_at, dedupe_key) VALUES (?, ?, ?, ?, ?, ?)",
            (task.get("email_id"), task.get("text"), task.get("due"), task.get("status") or "open", now_iso(), key),
        )
        return cursor.rowcount > 0

    # ── обязательства (обещания и просьбы) ───────────────────────────────
    def upsert_commitment(self, commitment: dict) -> bool:
        text = re.sub(r"\s+", " ", str(commitment.get("text") or "")).strip()
        if not text:
            return False
        direction = "them" if commitment.get("direction") == "them" else "me"
        due = commitment.get("due")
        key = f"{commitment.get('thread_key') or ''}|{direction}|{text.lower()}|{due or ''}"
        cursor = self.conn.execute(
            """INSERT OR IGNORE INTO commitments
                 (email_id, thread_key, direction, text, due, status, source, counterparty, created_at, dedupe_key)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                commitment.get("email_id"),
                commitment.get("thread_key"),
                direction,
                text,
                due,
                commitment.get("status") or "open",
                commitment.get("source"),
                commitment.get("counterparty"),
                now_iso(),
                key,
            ),
        )
        return cursor.rowcount > 0

    def get_commitment(self, commitment_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM commitments WHERE id = ?", (commitment_id,)).fetchone()
        return dict(row) if row else None

    def open_commitments(self, direction: str | None = None, limit: int = 50) -> list[dict]:
        sql = "SELECT * FROM commitments WHERE status = 'open'"
        params: list[Any] = []
        if direction:
            sql += " AND direction = ?"
            params.append(direction)
        sql += " ORDER BY due IS NULL, due ASC, created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.conn.execute(sql, params).fetchall()]

    def close_commitment(self, commitment_id: int) -> bool:
        cursor = self.conn.execute(
            "UPDATE commitments SET status = 'done', updated_at = ? WHERE id = ? AND status = 'open'",
            (now_iso(), commitment_id),
        )
        return cursor.rowcount > 0

    def thread(self, thread_key: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM threads WHERE thread_key = ?", (thread_key,)).fetchone()
        return dict(row) if row else None

    def silent_threads(self, days: int = 3, limit: int = 10) -> list[dict]:
        """Треды, где последним писали мы и ответа нет дольше N дней."""
        rows = self.conn.execute(
            """SELECT *, CAST(julianday('now') - julianday(last_outbound_at) AS INTEGER) AS silent_days
                 FROM threads
                WHERE last_outbound_at IS NOT NULL
                  AND COALESCE(last_outbound_at, '') > COALESCE(last_inbound_at, '')
                  AND julianday('now') - julianday(last_outbound_at) >= ?
                ORDER BY last_outbound_at DESC
                LIMIT ?""",
            (days, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── поиск ───────────────────────────────────────────────────────────
    def search_emails(self, query: str, limit: int = 8, since_days: int | None = None) -> list[dict]:
        limit = max(1, min(50, int(limit or 8)))
        since = None
        if since_days:
            from datetime import datetime, timedelta, timezone

            since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat()

        results: list[dict] = []
        seen: set[int] = set()

        def push(rows: Iterable[sqlite3.Row]) -> None:
            for row in rows:
                if row["id"] in seen:
                    continue
                seen.add(row["id"])
                results.append(_hydrate(row))

        fts = build_fts_query(query)
        if fts:
            sql = f"{SELECT_EMAIL} JOIN emails_fts ON emails_fts.rowid = e.id WHERE emails_fts MATCH ?"
            params: list[Any] = [fts]
            if since:
                sql += " AND e.sent_at >= ?"
                params.append(since)
            sql += " ORDER BY bm25(emails_fts) LIMIT ?"
            params.append(limit)
            try:
                push(self.conn.execute(sql, params).fetchall())
            except sqlite3.OperationalError:
                pass

        if len(results) < limit:
            tokens = tokenize(query)[:3]
            if tokens:
                conditions = " AND ".join(
                    "(ru_lower(e.subject) LIKE ? OR ru_lower(e.body_text) LIKE ? OR ru_lower(e.from_name) LIKE ? OR ru_lower(e.from_addr) LIKE ?)"
                    for _ in tokens
                )
                params = []
                for token in tokens:
                    like = f"%{token}%"
                    params.extend([like, like, like, like])
                sql = f"{SELECT_EMAIL} WHERE {conditions}"
                if since:
                    sql += " AND e.sent_at >= ?"
                    params.append(since)
                sql += " ORDER BY e.sent_at DESC LIMIT ?"
                params.append(limit)
                push(self.conn.execute(sql, params).fetchall())

        return results[:limit]

    def recent_emails(self, limit: int = 10, since_days: int = 7, kind: str | None = None) -> list[dict]:
        from datetime import datetime, timedelta, timezone

        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).isoformat() if since_days else None
        sql = f"{SELECT_EMAIL} WHERE e.is_bulk = 0"
        params: list[Any] = []
        if since:
            sql += " AND e.sent_at >= ?"
            params.append(since)
        if kind:
            sql += " AND a.kind = ?"
            params.append(kind)
        sql += " ORDER BY e.sent_at DESC LIMIT ?"
        params.append(limit)
        return [_hydrate(r) for r in self.conn.execute(sql, params).fetchall()]

    def recent_with_analysis(self, limit: int = 30) -> list[dict]:
        rows = self.conn.execute(f"{SELECT_EMAIL} WHERE a.id IS NOT NULL ORDER BY e.sent_at DESC LIMIT ?", (limit,))
        return [_hydrate(r) for r in rows.fetchall()]

    def contacts(self, query: str = "", limit: int = 20) -> list[dict]:
        limit = max(1, min(1000, int(limit or 20)))
        value = (query or "").strip()
        if not value:
            rows = self.conn.execute("SELECT * FROM contacts ORDER BY created_at DESC LIMIT ?", (limit,))
            return [dict(r) for r in rows.fetchall()]
        like = f"%{value.lower()}%"
        rows = self.conn.execute(
            """SELECT * FROM contacts
                WHERE ru_lower(name) LIKE ? OR phone LIKE ? OR ru_lower(address) LIKE ?
                   OR ru_lower(company) LIKE ? OR ru_lower(email) LIKE ?
                ORDER BY created_at DESC LIMIT ?""",
            (like, like, like, like, like, limit),
        )
        return [dict(r) for r in rows.fetchall()]

    def contracts(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute("SELECT * FROM contracts ORDER BY created_at DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows.fetchall()]

    def tasks(self, status: str = "open", limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM tasks WHERE status = ? ORDER BY due IS NULL, due ASC, created_at DESC LIMIT ?",
            (status, limit),
        )
        return [dict(r) for r in rows.fetchall()]

    # ── состояние синхронизации ─────────────────────────────────────────
    def get_sync_state(self, account: str, folder: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM sync_state WHERE account = ? AND folder = ?", (account, folder)
        ).fetchone()
        return dict(row) if row else None

    def set_sync_state(self, account: str, folder: str, uidvalidity=None, last_uid: int = 0, last_run=None) -> None:
        self.conn.execute(
            """INSERT INTO sync_state (account, folder, uidvalidity, last_uid, last_run)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT (account, folder) DO UPDATE SET
                 uidvalidity = excluded.uidvalidity,
                 last_uid = excluded.last_uid,
                 last_run = excluded.last_run""",
            (account, folder, uidvalidity, int(last_uid or 0), last_run or now_iso()),
        )

    # ── служебное ───────────────────────────────────────────────────────
    def stats(self) -> dict:
        def one(sql: str) -> int:
            row = self.conn.execute(sql).fetchone()
            return int(row[0]) if row else 0

        kinds = self.conn.execute("SELECT kind, COUNT(*) AS n FROM analyses GROUP BY kind").fetchall()
        return {
            "emails": one("SELECT COUNT(*) FROM emails"),
            "analyzed": one("SELECT COUNT(*) FROM analyses"),
            "contacts": one("SELECT COUNT(*) FROM contacts"),
            "contracts": one("SELECT COUNT(*) FROM contracts"),
            "openTasks": one("SELECT COUNT(*) FROM commitments WHERE status = 'open'"),
            "commitments": one("SELECT COUNT(*) FROM commitments"),
            "threads": one("SELECT COUNT(*) FROM threads"),
            "highRisk": one("SELECT COUNT(*) FROM analyses WHERE risk_level = 'high'"),
            "byKind": {row["kind"]: int(row["n"]) for row in kinds},
        }

    def reset(self) -> None:
        with self.tx():
            for table in ("commitments", "threads", "tasks", "contracts", "contacts", "analyses", "emails", "sync_state", "meta"):
                self.conn.execute(f"DELETE FROM {table}")
            self.conn.execute("INSERT INTO emails_fts (emails_fts) VALUES ('rebuild')")

    def reset_analyses(self) -> None:
        """Удаляет разбор и производные записи, оставляя сами письма и треды."""
        with self.tx():
            for table in ("commitments", "tasks", "contracts", "contacts", "analyses"):
                self.conn.execute(f"DELETE FROM {table}")

    SENT_FOLDERS = {"отправленные", "sent", "sent items", "sent messages", "sent mail"}

    def purge_sent(self) -> int:
        """Удаляет письма из «Отправленных» и пересчитывает треды."""
        folders = [row["folder"] for row in self.conn.execute("SELECT DISTINCT folder FROM emails").fetchall()]
        sent = [folder for folder in folders if str(folder).strip().lower() in self.SENT_FOLDERS]
        if not sent:
            return 0

        placeholders = ",".join("?" for _ in sent)
        with self.tx():
            cursor = self.conn.execute(f"DELETE FROM emails WHERE folder IN ({placeholders})", sent)
            removed = cursor.rowcount

        self.rebuild_threads()
        return int(removed)

    def rebuild_threads(self) -> None:
        """Пересобирает агрегаты тредов из писем (после удаления папок)."""
        with self.tx():
            self.conn.execute("DELETE FROM threads")
            self.conn.execute(
                """INSERT INTO threads
                     (thread_key, subject, counterparty, message_count,
                      last_inbound_at, last_outbound_at, last_message_at, updated_at)
                   SELECT e.thread_key,
                          (SELECT subject FROM emails x WHERE x.thread_key = e.thread_key
                            ORDER BY x.sent_at DESC LIMIT 1),
                          (SELECT from_name FROM emails x
                            WHERE x.thread_key = e.thread_key AND ru_lower(x.from_addr) <> ru_lower(x.account)
                            ORDER BY x.sent_at DESC LIMIT 1),
                          COUNT(*),
                          MAX(CASE WHEN ru_lower(e.from_addr) = ru_lower(e.account) THEN NULL ELSE e.sent_at END),
                          MAX(CASE WHEN ru_lower(e.from_addr) = ru_lower(e.account) THEN e.sent_at ELSE NULL END),
                          MAX(e.sent_at),
                          datetime('now')
                     FROM emails e
                    WHERE e.thread_key IS NOT NULL
                    GROUP BY e.thread_key"""
            )


def open_store(path: str | Path) -> Store:
    return Store(os.fspath(path))
