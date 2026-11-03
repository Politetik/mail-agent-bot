import { ImapFlow } from 'imapflow';
import { simpleParser } from 'mailparser';
import { detectBulk } from './patterns.js';
import { logger, nowIso, truncate } from './util.js';

const ENTITIES = {
  nbsp: ' ', amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", laquo: '«', raquo: '»',
  mdash: '—', ndash: '–', hellip: '…', copy: '©', reg: '®', trade: '™', deg: '°',
};

export function decodeEntities(text) {
  return String(text ?? '').replace(/&(#x?[0-9a-f]+|[a-z]+);/gi, (m, code) => {
    if (code[0] === '#') {
      const num = code[1] === 'x' || code[1] === 'X' ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10);
      return Number.isFinite(num) ? String.fromCodePoint(num) : m;
    }
    return ENTITIES[code.toLowerCase()] ?? m;
  });
}

export function htmlToText(html) {
  return decodeEntities(
    String(html ?? '')
      .replace(/<(script|style)[\s\S]*?<\/\1>/gi, ' ')
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|tr|li|h[1-6])>/gi, '\n')
      .replace(/<li[^>]*>/gi, '• ')
      .replace(/<[^>]+>/g, ' '),
  )
    .replace(/[ \t\u00a0]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function createImapClient(cfg) {
  const client = new ImapFlow({
    host: cfg.mail.imapHost,
    port: cfg.mail.imapPort,
    secure: true,
    auth: { user: cfg.mail.user, pass: cfg.mail.password },
    logger: false,
    tls: { rejectUnauthorized: true, minVersion: 'TLSv1.2' },
    connectionTimeout: 30000,
    greetingTimeout: 20000,
    socketTimeout: 120000,
  });
  return client;
}

export function explainMailError(err) {
  const msg = String(err?.message ?? err);
  if (/AUTHENTICATIONFAILED|Invalid credentials|LOGIN failed|535/i.test(msg)) {
    return [
      'Не удалось войти в почту (неверный логин или пароль).',
      'Нужен ПАРОЛЬ ПРИЛОЖЕНИЯ, а не обычный пароль от почты:',
      '• Mail.ru: Настройки → Безопасность → Пароли для внешних приложений.',
      '• Яндекс: id.yandex.ru → Безопасность → Пароли приложений (и включите IMAP в Настройки → Почтовые программы).',
      'Проверьте, что MAIL_USER — полный адрес, а MAIL_PASSWORD — этот пароль.',
    ].join('\n');
  }
  if (/ENOTFOUND|EAI_AGAIN|getaddrinfo/i.test(msg)) {
    return 'Не удалось подключиться к серверу почты — проверьте интернет и MAIL_IMAP_HOST.';
  }
  if (/ETIMEDOUT|timeout/i.test(msg)) {
    return 'Сервер почты не ответил вовремя — попробуйте позже или проверьте файрвол.';
  }
  return msg;
}

function pickAddress(list) {
  if (!list) return { name: null, address: null };
  const arr = Array.isArray(list) ? list : list.value ?? [list];
  const first = arr[0] ?? {};
  return { name: first.name ?? null, address: first.address ?? null };
}

function threadKey(subject, messageId) {
  const base = String(subject ?? '')
    .replace(/^\s*((re|fw|fwd|отв|пересл)\s*:\s*)+/gi, '')
    .toLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, ' ')
    .trim();
  return base || messageId || null;
}

/** Resolve which folders to sync: configured ones plus the Sent folder. */
export async function resolveFolders(client, cfg) {
  const list = await client.list();
  const paths = [];
  const push = (p) => {
    if (p && !paths.includes(p)) paths.push(p);
  };

  for (const wanted of cfg.mail.folders) {
    const exact = list.find((m) => m.path === wanted);
    if (exact) {
      push(exact.path);
      continue;
    }
    const byName = list.find((m) => String(m.name ?? '').toLowerCase() === wanted.toLowerCase());
    push(byName?.path ?? wanted);
  }

  if (cfg.mail.includeSent) {
    const sent = list.find((m) => (m.specialUse ?? '').toLowerCase() === '\\sent') ?? list.find((m) => /^(отправленные|sent|sent items|sent messages)$/i.test(String(m.name ?? '')));
    if (sent) push(sent.path);
  }

  return { paths, mailboxes: list };
}

/**
 * Fetch new messages from Mail.ru over IMAP and persist them.
 * @returns {Promise<{fetched:number, duplicates:number, folders:string[], errors:string[]}>}
 */
export async function fetchNewEmails(cfg, store, { onProgress = null, onEmail = null } = {}) {
  const client = createImapClient(cfg);
  const summary = { fetched: 0, duplicates: 0, folders: [], errors: [] };
  const account = cfg.mail.user;

  try {
    await client.connect();
    logger.info(`IMAP подключён: ${account} @ ${cfg.mail.imapHost}`);
  } catch (err) {
    throw new Error(explainMailError(err));
  }

  try {
    const { paths } = await resolveFolders(client, cfg);
    logger.info(`Папки для синхронизации: ${paths.join(', ')}`);

    for (const folderPath of paths) {
      let lock;
      try {
        lock = await client.getMailboxLock(folderPath);
      } catch (err) {
        summary.errors.push(`Папка «${folderPath}»: ${err.message}`);
        logger.warn(`Не удалось открыть папку ${folderPath}: ${err.message}`);
        continue;
      }

      try {
        const mailbox = client.mailbox;
        const exists = Number(mailbox?.exists ?? 0);
        const uidValidity = String(mailbox?.uidValidity ?? '');
        const state = store.getSyncState(account, folderPath);
        const reset = !state || state.uidvalidity !== uidValidity;
        const lastUid = reset ? 0 : Number(state.last_uid ?? 0);

        if (!exists) {
          store.setSyncState(account, folderPath, { uidvalidity: uidValidity, lastUid: 0 });
          summary.folders.push(folderPath);
          continue;
        }

        let uids = [];
        if (lastUid > 0) {
          uids = await client.search({ uid: `${lastUid + 1}:*` }, { uid: true });
        } else {
          const since = new Date(Date.now() - cfg.sync.days * 86400000);
          uids = await client.search({ since }, { uid: true });
        }
        uids = (uids ?? []).filter((u) => Number(u) > lastUid).sort((a, b) => a - b);

        if (uids.length > cfg.sync.limit) uids = uids.slice(-cfg.sync.limit);

        logger.info(`Папка ${folderPath}: ${exists} писем, новых UID: ${uids.length}`);
        if (onProgress) onProgress({ folder: folderPath, total: uids.length, done: 0 });

        let done = 0;
        let maxUid = lastUid;

        for (let i = 0; i < uids.length; i += cfg.sync.batchSize) {
          const batch = uids.slice(i, i + cfg.sync.batchSize);
          // imapflow expects a sequence-set string, e.g. "101,102,103".
          for await (const msg of client.fetch(batch.join(','), {
            uid: true,
            source: true,
            envelope: true,
            internalDate: true,
          })) {
            try {
              const parsed = await simpleParser(msg.source);
              const from = pickAddress(parsed.from);
              const bodyRaw = parsed.text?.trim() ? parsed.text : htmlToText(parsed.html ?? '');
              const body = truncate(bodyRaw.replace(/\r\n/g, '\n'), cfg.sync.maxBodyChars);
              const isBulk = detectBulk(parsed.headers, from.address, parsed.subject)
                ? 1
                : 0;

              const row = {
                account,
                folder: folderPath,
                uid: msg.uid,
                message_id: parsed.messageId ?? null,
                in_reply_to: parsed.inReplyTo ?? null,
                thread_key: threadKey(parsed.subject, parsed.messageId),
                from_name: from.name || null,
                from_addr: (from.address ?? '').toLowerCase() || null,
                to_addrs: (parsed.to?.value ?? []).map((t) => t.address).filter(Boolean).join(', ') || null,
                cc_addrs: (parsed.cc?.value ?? []).map((t) => t.address).filter(Boolean).join(', ') || null,
                subject: parsed.subject ?? '(без темы)',
                sent_at: (parsed.date instanceof Date ? parsed.date : msg.internalDate ?? new Date()).toISOString(),
                body_text: body,
                snippet: truncate(body.replace(/\s+/g, ' '), 240),
                attachments: (parsed.attachments ?? []).map((a) => ({
                  filename: a.filename ?? null,
                  contentType: a.contentType ?? null,
                  size: a.size ?? null,
                })),
                is_bulk: isBulk,
                fetched_at: nowIso(),
              };

              const { id, inserted } = store.insertEmail(row);
              maxUid = Math.max(maxUid, Number(msg.uid));
              if (inserted) {
                summary.fetched += 1;
                if (onEmail) await onEmail({ ...row, id });
              } else {
                summary.duplicates += 1;
              }
            } catch (err) {
              summary.errors.push(`Письмо UID ${msg.uid}: ${err.message}`);
              logger.warn(`Ошибка разбора письма UID ${msg.uid}: ${err.message}`);
            }
            done += 1;
            if (onProgress) onProgress({ folder: folderPath, total: uids.length, done });
          }
        }

        store.setSyncState(account, folderPath, { uidvalidity: uidValidity, lastUid: maxUid });
        summary.folders.push(folderPath);
      } finally {
        if (lock) await lock.release();
      }
    }
  } finally {
    try {
      await client.logout();
    } catch {
      /* ignore */
    }
  }

  return summary;
}

/** Lightweight connectivity check used by `npm run cli -- doctor`. */
export async function testMailConnection(cfg) {
  const client = createImapClient(cfg);
  const started = Date.now();
  await client.connect();
  try {
    const { paths, mailboxes } = await resolveFolders(client, cfg);
    return {
      ok: true,
      ms: Date.now() - started,
      mailboxes: mailboxes.length,
      folders: paths,
      existing: paths.length ? Number((await client.mailbox)?.exists ?? 0) : 0,
    };
  } finally {
    try {
      await client.logout();
    } catch {
      /* ignore */
    }
  }
}
