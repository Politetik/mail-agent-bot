import fs from 'node:fs';
import path from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { nowIso } from './util.js';

const SCHEMA_VERSION = 1;

const SCHEMA = `
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
`;

const SELECT_EMAIL = `
  SELECT e.id, e.account, e.folder, e.uid, e.message_id, e.from_name, e.from_addr,
         e.to_addrs, e.subject, e.sent_at, e.snippet, e.is_bulk, e.attachments,
         a.kind, a.summary, a.counterparty, a.contract_number, a.amount, a.currency,
         a.deadline, a.status, a.risk_level, a.risks, a.asks, a.phones, a.addresses,
         a.contacts, a.confidence, a.engine
    FROM emails e
    LEFT JOIN analyses a ON a.email_id = e.id
`;

const STOPWORDS = new Set([
  'и', 'в', 'во', 'на', 'с', 'со', 'по', 'к', 'ко', 'о', 'об', 'от', 'до', 'за', 'из',
  'не', 'ни', 'но', 'или', 'что', 'как', 'где', 'кто', 'мне', 'мне', 'все', 'всё',
  'это', 'этот', 'эта', 'был', 'была', 'были', 'есть', 'the', 'a', 'an', 'of', 'to',
  'in', 'is', 'are', 'for', 'and', 'or', 'what', 'who', 'where',
]);

export function tokenize(text) {
  return (String(text ?? '').toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? []).filter(
    (t) => t.length >= 2 && !STOPWORDS.has(t),
  );
}

export function buildFtsQuery(text) {
  const tokens = tokenize(text).slice(0, 12);
  if (!tokens.length) return null;
  return tokens.map((t) => `"${t.replaceAll('"', '""')}"*`).join(' OR ');
}

function bind(v) {
  if (v === undefined) return null;
  if (typeof v === 'boolean') return v ? 1 : 0;
  if (v instanceof Date) return v.toISOString();
  return v;
}

function jsonOrNull(v) {
  if (v === undefined || v === null) return null;
  if (typeof v === 'string') return v;
  try {
    return JSON.stringify(v);
  } catch {
    return null;
  }
}

function parseJsonField(v, fallback) {
  if (!v) return fallback;
  try {
    return JSON.parse(v);
  } catch {
    return fallback;
  }
}

function hydrate(row) {
  if (!row) return null;
  return {
    ...row,
    risks: parseJsonField(row.risks, []),
    asks: parseJsonField(row.asks, []),
    phones: parseJsonField(row.phones, []),
    addresses: parseJsonField(row.addresses, []),
    contacts: parseJsonField(row.contacts, []),
    attachments: parseJsonField(row.attachments, []),
  };
}

export function openStore(dbPath) {
  fs.mkdirSync(path.dirname(path.resolve(dbPath)), { recursive: true });
  const db = new DatabaseSync(dbPath);
  db.exec('PRAGMA journal_mode = WAL;');
  db.exec('PRAGMA foreign_keys = ON;');
  db.exec('PRAGMA busy_timeout = 5000;');
  db.exec(SCHEMA);

  // SQLite has no ICU: built-in lower()/LIKE are ASCII-only. This function makes
  // case-insensitive search work for Cyrillic.
  db.function('ru_lower', { deterministic: true }, (s) => String(s ?? '').toLowerCase());

  const version = db.prepare('PRAGMA user_version').get().user_version ?? 0;
  if (version < SCHEMA_VERSION) {
    db.exec("INSERT INTO emails_fts (emails_fts) VALUES ('rebuild');");
    db.exec(`PRAGMA user_version = ${SCHEMA_VERSION};`);
  }

  const stmtCache = new Map();
  const prep = (sql) => {
    let s = stmtCache.get(sql);
    if (!s) {
      s = db.prepare(sql);
      stmtCache.set(sql, s);
    }
    return s;
  };

  function tx(fn) {
    db.exec('BEGIN IMMEDIATE');
    try {
      const out = fn();
      db.exec('COMMIT');
      return out;
    } catch (err) {
      try {
        db.exec('ROLLBACK');
      } catch {
        /* ignore */
      }
      throw err;
    }
  }

  const store = {
    db,
    path: dbPath,
    close() {
      stmtCache.clear();
      db.close();
    },
    tx,

    metaGet(key) {
      return prep('SELECT value FROM meta WHERE key = ?').get(bind(key))?.value ?? null;
    },
    metaSet(key, value) {
      prep('INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value').run(
        bind(key),
        bind(value),
      );
    },

    insertEmail(row) {
      const info = prep(
        `INSERT OR IGNORE INTO emails
           (account, folder, uid, message_id, in_reply_to, thread_key, from_name, from_addr,
            to_addrs, cc_addrs, subject, sent_at, body_text, snippet, attachments, is_bulk, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        bind(row.account),
        bind(row.folder),
        bind(row.uid),
        bind(row.message_id),
        bind(row.in_reply_to),
        bind(row.thread_key),
        bind(row.from_name),
        bind(row.from_addr),
        bind(row.to_addrs),
        bind(row.cc_addrs),
        bind(row.subject),
        bind(row.sent_at),
        bind(row.body_text ?? ''),
        bind(row.snippet),
        bind(jsonOrNull(row.attachments)),
        bind(row.is_bulk ? 1 : 0),
        bind(row.fetched_at ?? nowIso()),
      );
      const id = prep('SELECT id FROM emails WHERE account = ? AND folder = ? AND uid = ?').get(
        bind(row.account),
        bind(row.folder),
        bind(row.uid),
      )?.id;
      return { id, inserted: Number(info.changes) > 0 };
    },

    emailExists(account, folder, uid) {
      return Boolean(
        prep('SELECT 1 AS x FROM emails WHERE account = ? AND folder = ? AND uid = ?').get(
          bind(account),
          bind(folder),
          bind(uid),
        ),
      );
    },

    getEmail(id) {
      return hydrate(prep(`${SELECT_EMAIL} WHERE e.id = ?`).get(bind(id)));
    },

    saveAnalysis(emailId, a) {
      // Merge with a previous analysis so a partial re-run does not wipe fields.
      const prev = prep('SELECT * FROM analyses WHERE email_id = ?').get(bind(emailId)) ?? null;
      const pick = (key, dflt = null) => (a[key] !== undefined && a[key] !== null ? a[key] : (prev?.[key] ?? dflt));
      const pickJson = (key) =>
        a[key] !== undefined && a[key] !== null ? a[key] : parseJsonField(prev?.[key], []);

      a = {
        kind: pick('kind', 'other'),
        summary: pick('summary'),
        counterparty: pick('counterparty'),
        contract_number: pick('contract_number'),
        amount: pick('amount'),
        currency: pick('currency', 'RUB'),
        deadline: pick('deadline'),
        status: pick('status', 'new'),
        risk_level: pick('risk_level', 'low'),
        risks: pickJson('risks'),
        asks: pickJson('asks'),
        contacts: pickJson('contacts'),
        phones: pickJson('phones'),
        addresses: pickJson('addresses'),
        confidence: pick('confidence'),
        engine: pick('engine', 'rules'),
        model: pick('model'),
      };

      prep(
        `INSERT INTO analyses
          (email_id, kind, summary, counterparty, contract_number, amount, currency, deadline,
           status, risk_level, risks, asks, contacts, phones, addresses, confidence, engine, model, created_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (email_id) DO UPDATE SET
           kind = excluded.kind, summary = excluded.summary, counterparty = excluded.counterparty,
           contract_number = excluded.contract_number, amount = excluded.amount, currency = excluded.currency,
           deadline = excluded.deadline, status = excluded.status, risk_level = excluded.risk_level,
           risks = excluded.risks, asks = excluded.asks, contacts = excluded.contacts,
           phones = excluded.phones, addresses = excluded.addresses, confidence = excluded.confidence,
           engine = excluded.engine, model = excluded.model, created_at = excluded.created_at`,
      ).run(
        bind(emailId),
        bind(a.kind ?? 'other'),
        bind(a.summary),
        bind(a.counterparty),
        bind(a.contract_number),
        bind(a.amount ?? null),
        bind(a.currency ?? 'RUB'),
        bind(a.deadline),
        bind(a.status ?? 'new'),
        bind(a.risk_level ?? 'low'),
        bind(jsonOrNull(a.risks ?? [])),
        bind(jsonOrNull(a.asks ?? [])),
        bind(jsonOrNull(a.contacts ?? [])),
        bind(jsonOrNull(a.phones ?? [])),
        bind(jsonOrNull(a.addresses ?? [])),
        bind(a.confidence ?? null),
        bind(a.engine ?? 'rules'),
        bind(a.model ?? null),
        bind(nowIso()),
      );
    },

    getAnalysis(emailId) {
      return prep('SELECT * FROM analyses WHERE email_id = ?').get(bind(emailId)) ?? null;
    },

    emailsWithoutAnalysis(limit = 50) {
      return prep(
        `SELECT * FROM emails e
          WHERE NOT EXISTS (SELECT 1 FROM analyses a WHERE a.email_id = e.id)
            AND e.is_bulk = 0
          ORDER BY e.sent_at ASC LIMIT ?`,
      ).all(bind(limit));
    },

    upsertContact(c) {
      const key = [c.name, c.phone, c.email]
        .map((x) => String(x ?? '').trim().toLowerCase())
        .join('|');
      const info = prep(
        `INSERT OR IGNORE INTO contacts
           (email_id, name, position, company, phone, email, address, inn, context, confidence, created_at, dedupe_key)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        bind(c.email_id),
        bind(c.name),
        bind(c.position),
        bind(c.company),
        bind(c.phone),
        bind(c.email),
        bind(c.address),
        bind(c.inn),
        bind(c.context),
        bind(c.confidence ?? null),
        bind(nowIso()),
        bind(key),
      );
      if (Number(info.changes) === 0) {
        // Enrich an existing contact with new address/company details when found.
        prep(
          `UPDATE contacts SET
             address = COALESCE(address, ?), company = COALESCE(company, ?),
             position = COALESCE(position, ?), inn = COALESCE(inn, ?)
           WHERE dedupe_key = ?`,
        ).run(bind(c.address), bind(c.company), bind(c.position), bind(c.inn), bind(key));
      }
      return Number(info.changes) > 0;
    },

    upsertContract(c) {
      // A contract is unique by its number, so the same deal mentioned in several
      // letters (offer, reply, invoice) enriches one registry row instead of many.
      const key = c.number
        ? `n:${String(c.number).trim().toLowerCase().replace(/\s+/g, '')}`
        : `e:${c.email_id ?? 'x'}|${String(c.subject ?? '').trim().toLowerCase()}`;
      const info = prep(
        `INSERT OR IGNORE INTO contracts
           (email_id, number, counterparty, subject, amount, currency, start_date, end_date, status, risks, created_at, dedupe_key)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      ).run(
        bind(c.email_id),
        bind(c.number),
        bind(c.counterparty),
        bind(c.subject),
        bind(c.amount ?? null),
        bind(c.currency ?? 'RUB'),
        bind(c.start_date),
        bind(c.end_date),
        bind(c.status ?? 'new'),
        bind(jsonOrNull(c.risks ?? [])),
        bind(nowIso()),
        bind(key),
      );
      if (Number(info.changes) === 0) {
        // Enrich the existing record with the newest known values.
        prep(
          `UPDATE contracts SET
             amount = COALESCE(amount, ?),
             end_date = COALESCE(end_date, ?),
             risks = CASE WHEN risks IS NULL OR risks = '[]' THEN ? ELSE risks END
           WHERE dedupe_key = ?`,
        ).run(bind(c.amount ?? null), bind(c.end_date), bind(jsonOrNull(c.risks ?? [])), bind(key));
      }
      return Number(info.changes) > 0;
    },

    addTask(t) {
      const key = [t.email_id, t.text].map((x) => String(x ?? '').trim().toLowerCase()).join('|');
      const info = prep(
        `INSERT OR IGNORE INTO tasks (email_id, text, due, status, created_at, dedupe_key)
         VALUES (?, ?, ?, ?, ?, ?)`,
      ).run(bind(t.email_id), bind(t.text), bind(t.due), bind(t.status ?? 'open'), bind(nowIso()), bind(key));
      return Number(info.changes) > 0;
    },

    searchEmails(query, { limit = 8, sinceDays = null } = {}) {
      const lim = Math.max(1, Math.min(50, Number(limit) || 8));
      const since = sinceDays
        ? new Date(Date.now() - sinceDays * 86400000).toISOString()
        : null;

      const results = [];
      const seen = new Set();
      const pushRows = (rows) => {
        for (const r of rows) {
          if (seen.has(r.id)) continue;
          seen.add(r.id);
          results.push(hydrate(r));
        }
      };

      const fts = buildFtsQuery(query);
      if (fts) {
        try {
          const rows = prep(
            `${SELECT_EMAIL}
             JOIN emails_fts ON emails_fts.rowid = e.id
             WHERE emails_fts MATCH ? ${since ? 'AND e.sent_at >= ?' : ''}
             ORDER BY bm25(emails_fts) LIMIT ?`,
          ).all(...[fts, since, lim].filter((x) => x !== null));
          pushRows(rows);
        } catch {
          /* malformed FTS query — fall through to LIKE */
        }
      }

      if (results.length < lim) {
        const tokens = tokenize(query).slice(0, 3);
        if (tokens.length) {
          const conds = tokens.map(
            () =>
              '(ru_lower(e.subject) LIKE ? OR ru_lower(e.body_text) LIKE ? OR ru_lower(e.from_name) LIKE ? OR ru_lower(e.from_addr) LIKE ?)',
          );
          const params = [];
          for (const t of tokens) {
            const like = `%${t}%`;
            params.push(like, like, like, like);
          }
          if (since) params.push(since);
          const rows = prep(
            `${SELECT_EMAIL}
             WHERE ${conds.join(' AND ')} ${since ? 'AND e.sent_at >= ?' : ''}
             ORDER BY e.sent_at DESC LIMIT ?`,
          ).all(...params, lim);
          pushRows(rows);
        }
      }

      return results.slice(0, lim);
    },

    recentEmails({ limit = 10, sinceDays = 7, kind = null } = {}) {
      const since = sinceDays ? new Date(Date.now() - sinceDays * 86400000).toISOString() : null;
      const params = [];
      let where = 'WHERE e.is_bulk = 0';
      if (since) {
        where += ' AND e.sent_at >= ?';
        params.push(since);
      }
      if (kind) {
        where += ' AND a.kind = ?';
        params.push(kind);
      }
      return prep(
        `${SELECT_EMAIL} ${where} ORDER BY e.sent_at DESC LIMIT ?`,
      ).all(...params, bind(limit)).map(hydrate);
    },

    contacts({ query = '', limit = 20 } = {}) {
      const lim = Math.max(1, Math.min(1000, Number(limit) || 20));
      const q = String(query ?? '').trim();
      if (!q) {
        return prep('SELECT * FROM contacts ORDER BY created_at DESC LIMIT ?').all(bind(lim));
      }
      const like = `%${q.toLowerCase()}%`;
      return prep(
        `SELECT * FROM contacts
          WHERE ru_lower(name) LIKE ? OR phone LIKE ? OR ru_lower(address) LIKE ?
             OR ru_lower(company) LIKE ? OR ru_lower(email) LIKE ?
          ORDER BY created_at DESC LIMIT ?`,
      ).all(like, like, like, like, like, bind(lim));
    },

    contracts({ limit = 20 } = {}) {
      return prep('SELECT * FROM contracts ORDER BY created_at DESC LIMIT ?').all(bind(limit));
    },

    tasks({ status = 'open', limit = 20 } = {}) {
      return prep('SELECT * FROM tasks WHERE status = ? ORDER BY due IS NULL, due ASC, created_at DESC LIMIT ?').all(
        bind(status),
        bind(limit),
      );
    },

    getSyncState(account, folder) {
      return (
        prep('SELECT * FROM sync_state WHERE account = ? AND folder = ?').get(bind(account), bind(folder)) ?? null
      );
    },

    setSyncState(account, folder, { uidvalidity = null, lastUid = 0, lastRun = null }) {
      prep(
        `INSERT INTO sync_state (account, folder, uidvalidity, last_uid, last_run)
         VALUES (?, ?, ?, ?, ?)
         ON CONFLICT (account, folder) DO UPDATE SET
           uidvalidity = excluded.uidvalidity,
           last_uid = excluded.last_uid,
           last_run = excluded.last_run`,
      ).run(bind(account), bind(folder), bind(uidvalidity), bind(lastUid), bind(lastRun ?? nowIso()));
    },

    stats() {
      const one = (sql) => Number(prep(sql).get()?.n ?? 0);
      const kinds = prep('SELECT kind, COUNT(*) AS n FROM analyses GROUP BY kind').all();
      return {
        emails: one('SELECT COUNT(*) AS n FROM emails'),
        analyzed: one('SELECT COUNT(*) AS n FROM analyses'),
        contacts: one('SELECT COUNT(*) AS n FROM contacts'),
        contracts: one('SELECT COUNT(*) AS n FROM contracts'),
        openTasks: one("SELECT COUNT(*) AS n FROM tasks WHERE status = 'open'"),
        highRisk: one("SELECT COUNT(*) AS n FROM analyses WHERE risk_level = 'high'"),
        byKind: Object.fromEntries(kinds.map((k) => [k.kind, Number(k.n)])),
      };
    },

    recentWithAnalysis(limit = 30) {
      return prep(
        `${SELECT_EMAIL} WHERE a.id IS NOT NULL ORDER BY e.sent_at DESC LIMIT ?`,
      ).all(bind(limit)).map(hydrate);
    },

    reset() {
      tx(() => {
        for (const t of ['tasks', 'contracts', 'contacts', 'analyses', 'emails', 'sync_state', 'meta']) {
          db.exec(`DELETE FROM ${t};`);
        }
        db.exec("INSERT INTO emails_fts (emails_fts) VALUES ('rebuild');");
      });
    },
  };

  return store;
}
