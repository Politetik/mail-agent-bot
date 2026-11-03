import { extractAddresses, extractPhones, formatPhone } from './patterns.js';
import { escapeHtml, formatDate, formatMoney, humanRuDate, logger, truncate } from './util.js';

const INTENT_RULES = [
  { intent: 'contacts', re: /(телефон|тел\.|моб\.|позвон|номер|адрес|контакт|почт[ау]|e-?mail|связаться|найти\s+(?:человека|контакт))/i },
  { intent: 'negotiations', re: /(переговор|о\s*ч[её]м\s+договорились|итог|статус|сводк|предложени|услови[яй]|скидк)/i },
  { intent: 'tasks', re: /(задач|что\s+(?:нужно|надо|висит|сделать)|дедлайн|поручен|напомни|ожида|ждут)/i },
  { intent: 'contracts', re: /(договор|контракт|соглашени|сумм[аы]|счёт|счет|оплат|сроки?\s+действия|подпис)/i },
];

export function detectIntent(question) {
  const q = String(question ?? '');
  for (const rule of INTENT_RULES) if (rule.re.test(q)) return rule.intent;
  return 'general';
}

const NAME_STOP = new Set([
  'Кто', 'Какой', 'Какие', 'Какая', 'Где', 'Когда', 'Телефон', 'Адрес', 'Договор',
  'Почта', 'Письмо', 'Компания', 'Контакт', 'Найди', 'Покажи', 'Скажи', 'Мне',
  'Нужен', 'Нужно', 'Есть', 'Что', 'Этот', 'Этой', 'Наши', 'Мой', 'Моя', 'Все',
  'Дай', 'Выведи', 'Список', 'Пожалуйста', 'Привет', 'Спасибо',
]);

const QUERY_STOP = new Set([
  'какой', 'какая', 'какие', 'какое', 'где', 'когда', 'кто', 'кого', 'кому', 'чем',
  'телефон', 'телефоны', 'тел', 'мобильный', 'рабочий', 'номер', 'адрес', 'адреса',
  'почта', 'почту', 'контакт', 'контакты', 'найди', 'найти', 'покажи', 'скажи',
  'есть', 'ли', 'мне', 'нужен', 'нужна', 'нужно', 'пожалуйста', 'компания', 'компании',
  'договор', 'договору', 'письмо', 'письмах', 'письма', 'все', 'всё', 'это', 'этот',
  'этой', 'наши', 'мой', 'моя', 'дай', 'выведи', 'список', 'про', 'по', 'у', 'в',
  'на', 'с', 'к', 'от', 'до', 'и', 'или', 'а', 'же',
]);

const RU_ENDINGS = [
  'иями', 'ями', 'ами', 'ов', 'ев', 'ой', 'ый', 'ий', 'ая', 'ое', 'ые', 'ых',
  'им', 'ем', 'ом', 'ах', 'ях', 'ию', 'ия', 'у', 'ю', 'а', 'я', 'о', 'е', 'ы', 'и', 'ь',
];

export function stemRu(word) {
  const s = String(word ?? '').toLowerCase();
  for (const end of RU_ENDINGS) {
    if (s.length - end.length >= 3 && s.endsWith(end)) return s.slice(0, -end.length);
  }
  return s;
}

/**
 * Fuzzy contact lookup: matches inflected names ("Анны Смирновой" → "Анна Смирнова")
 * and phone digits. Scoring avoids false positives.
 */
export function findMatchingContacts(store, question, limit = 8) {
  const q = String(question ?? '');
  const tokens = (q.toLowerCase().match(/[\p{L}\p{N}+]{2,}/gu) ?? []).filter((t) => !QUERY_STOP.has(t));
  if (!tokens.length) return [];

  const rows = store.contacts({ limit: 500 });
  const scored = [];

  for (const row of rows) {
    const hay = [row.name, row.company, row.address, row.email, row.position]
      .filter(Boolean)
      .join(' ')
      .toLowerCase();
    const hayStems = new Set((hay.match(/[\p{L}\p{N}+]{2,}/gu) ?? []).map(stemRu));
    const phoneDigits = String(row.phone ?? '').replace(/\D/g, '');

    let score = 0;
    for (const token of tokens) {
      const digits = token.replace(/\D/g, '');
      if (digits.length >= 5 && phoneDigits.includes(digits.slice(-7))) {
        score += 4;
        continue;
      }
      if (hayStems.has(stemRu(token))) score += 2;
    }
    if (score > 0) scored.push({ row, score });
  }

  scored.sort((a, b) => b.score - a.score);
  return scored.slice(0, limit).map((s) => s.row);
}

export function guessEntities(question) {
  const q = String(question ?? '');
  const phones = extractPhones(q);
  const addresses = extractAddresses(q);
  const names = (q.match(/\b[А-ЯЁ][а-яё]{2,}\b/g) ?? []).filter((w) => !NAME_STOP.has(w));
  return {
    phones,
    addresses,
    names,
    person: names.slice(0, 2).join(' ') || null,
  };
}

function sourceLine(email, index) {
  if (!email) return '';
  const when = email.sent_at ? formatDate(email.sent_at) : '—';
  const who = escapeHtml(email.from_name || email.from_addr || 'неизвестно');
  const what = escapeHtml(truncate(email.subject ?? '(без темы)', 70));
  return `🔗 <i>${index + 1}. ${who}, ${when}: «${what}»</i>`;
}

function contactBlock(g) {
  const lines = [];
  const title = [g.name, g.position].filter(Boolean).join(', ');
  const company = g.company ? ` — ${escapeHtml(g.company)}` : '';
  lines.push(`<b>${escapeHtml(title || 'Контакт')}</b>${company}`);
  for (const phone of g.phones) lines.push(`📞 ${escapeHtml(formatPhone(phone))}`);
  if (g.email) lines.push(`✉️ ${escapeHtml(g.email)}`);
  for (const address of g.addresses) lines.push(`📍 ${escapeHtml(address)}`);
  if (g.inn) lines.push(`🏢 ИНН ${escapeHtml(g.inn)}`);
  return lines.join('\n');
}

/** Merge per-phone contact rows into one card per person. */
function groupContacts(rows) {
  const map = new Map();
  for (const r of rows) {
    const named = String(r.name ?? '').trim();
    const key = named
      ? `${named.toLowerCase()}|${String(r.company ?? '').toLowerCase()}`
      : `p:${r.phone ?? ''}|${r.email ?? ''}`;
    let g = map.get(key);
    if (!g) {
      g = {
        name: r.name ?? null,
        position: r.position ?? null,
        company: r.company ?? null,
        email: r.email ?? null,
        inn: r.inn ?? null,
        phones: [],
        addresses: [],
        emailIds: [],
      };
      map.set(key, g);
    }
    if (r.phone && !g.phones.includes(r.phone)) g.phones.push(r.phone);
    if (r.address && !g.addresses.includes(r.address)) g.addresses.push(r.address);
    if (!g.email && r.email) g.email = r.email;
    if (!g.position && r.position) g.position = r.position;
    if (!g.inn && r.inn) g.inn = r.inn;
    if (r.email_id && !g.emailIds.includes(r.email_id)) g.emailIds.push(r.email_id);
  }
  return [...map.values()];
}

function dedupeById(rows) {
  const out = [];
  const seen = new Set();
  for (const r of rows) {
    if (!r || seen.has(r.id)) continue;
    seen.add(r.id);
    out.push(r);
  }
  return out;
}

export function answerContacts({ store, question, limit = 8 }) {
  const q = String(question ?? '').trim();
  const { phones, names } = guessEntities(q);
  const found = [];

  for (const phone of phones) found.push(...store.contacts({ query: phone, limit }));
  if (q) found.push(...findMatchingContacts(store, q, limit));

  let rows = dedupeById(found).slice(0, limit);

  if (!rows.length) {
    // No structured contact matched: show what we have, newest first.
    rows = store.contacts({ limit }).filter((c) => c.phone || c.address || c.name);
  }

  // Narrow by what the user is actually asking for.
  if (/адрес/i.test(q)) {
    const withAddress = rows.filter((r) => r.address);
    if (withAddress.length) rows = withAddress;
  } else if (/телефон|тел\.|моб/i.test(q)) {
    const withPhone = rows.filter((r) => r.phone);
    if (withPhone.length) rows = withPhone;
  }

  if (!rows.length) {
    return {
      text: '📇 В базе пока нет контактов с телефонами или адресами.\n\nСделайте /sync — я разберу новые письма.',
      emailIds: [],
    };
  }

  const groups = groupContacts(rows).slice(0, limit);
  const blocks = groups.map((g) => {
    const src = g.emailIds[0] ? store.getEmail(g.emailIds[0]) : null;
    return src ? `${contactBlock(g)}\n${sourceLine(src, 0)}` : contactBlock(g);
  });

  const header = phones.length
    ? `📇 <b>Контакт по номеру ${escapeHtml(formatPhone(phones[0]))}</b>`
    : names.length
      ? `📇 <b>Контакты по запросу «${escapeHtml(names.join(' '))}»</b>`
      : '📇 <b>Контакты из писем</b>';

  return {
    text: `${header}\n\n${blocks.join('\n\n')}`,
    emailIds: groups.flatMap((g) => g.emailIds),
  };
}

export function answerContracts({ store, query = '', limit = 10 }) {
  const q = String(query ?? '').trim();
  let rows = store.contracts({ limit: 50 });

  if (q) {
    const tokens = (q.toLowerCase().match(/[\p{L}\p{N}]+/gu) ?? [])
      .filter((t) => t.length >= 2 && !QUERY_STOP.has(t))
      .map(stemRu);
    if (tokens.length) {
      const filtered = rows.filter((c) => {
        const hay = [c.number, c.counterparty, c.subject].filter(Boolean).join(' ').toLowerCase();
        const stems = new Set((hay.match(/[\p{L}\p{N}]+/gu) ?? []).map(stemRu));
        return tokens.some((t) => stems.has(t));
      });
      if (filtered.length) rows = filtered;
    }
  }
  rows = rows.slice(0, limit);

  const fallback = rows.length ? [] : store.recentEmails({ limit, sinceDays: 365, kind: 'contract' });

  if (!rows.length && !fallback.length) {
    return { text: '📄 Договоров в базе пока нет. Сделайте /sync.', emailIds: [] };
  }

  const lines = [
    q ? `📄 <b>Договоры по запросу «${escapeHtml(truncate(q, 60))}»</b>` : '📄 <b>Договоры и соглашения</b>',
    '',
  ];

  for (const c of rows) {
    const head = [c.number ? `№ ${escapeHtml(c.number)}` : null, c.counterparty ? escapeHtml(c.counterparty) : null]
      .filter(Boolean)
      .join(' · ');
    const bits = [];
    if (c.amount) bits.push(`💰 ${formatMoney(c.amount, c.currency ?? 'RUB')}`);
    if (c.end_date) bits.push(`📅 до ${formatDate(c.end_date)}`);
    if (c.status) bits.push(statusLabel(c.status));
    lines.push(`<b>${head || 'Договор'}</b>`);
    if (c.subject) lines.push(escapeHtml(truncate(c.subject, 120)));
    if (bits.length) lines.push(bits.join(' · '));
    const email = c.email_id ? store.getEmail(c.email_id) : null;
    if (email) lines.push(sourceLine(email, 0));
    lines.push('');
  }

  if (!rows.length) {
    lines.push('Договоры найдены в письмах (разбор без реестра):');
    for (const e of fallback) {
      lines.push(`• <b>${escapeHtml(e.contract_number ?? 'без номера')}</b> — ${escapeHtml(e.counterparty ?? '—')}`);
      if (e.amount) lines.push(`  💰 ${formatMoney(e.amount, e.currency ?? 'RUB')}`);
      lines.push(`  ${sourceLine(e, 0)}`);
    }
  }

  return { text: lines.join('\n').trim(), emailIds: rows.map((r) => r.email_id).filter(Boolean) };
}

export function answerTasks({ store, limit = 12 }) {
  const tasks = store.tasks({ status: 'open', limit });
  const emails = store.recentEmails({ limit: 20, sinceDays: 30 }).filter((e) => (e.asks?.length ?? 0) > 0);

  if (!tasks.length && !emails.length) {
    return { text: '✅ Открытых задач и просьб не найдено.', emailIds: [] };
  }

  const lines = ['📌 <b>Что висит</b>', ''];

  if (tasks.length) {
    for (const t of tasks) {
      const due = t.due ? ` · до ${formatDate(t.due)}` : '';
      lines.push(`• ${escapeHtml(truncate(t.text, 180))}${due}`);
    }
    lines.push('');
  }

  const asks = [];
  const knownTasks = new Set(tasks.map((t) => String(t.text ?? '').trim().toLowerCase()));
  for (const e of emails) {
    for (const ask of e.asks ?? []) {
      if (knownTasks.has(String(ask).trim().toLowerCase())) continue;
      asks.push({ ask, email: e });
      if (asks.length >= limit) break;
    }
    if (asks.length >= limit) break;
  }
  if (asks.length) {
    lines.push('<b>Просьбы из переписки</b>');
    for (const { ask, email } of asks) {
      lines.push(`• ${escapeHtml(truncate(ask, 180))} — <i>${escapeHtml(email.from_name || email.from_addr || '')}, ${formatDate(email.sent_at)}</i>`);
    }
  }

  return {
    text: lines.join('\n').trim(),
    emailIds: [...tasks.map((t) => t.email_id), ...emails.map((e) => e.id)].filter(Boolean),
  };
}

export function statusLabel(status) {
  return (
    {
      new: '🆕 новое',
      in_progress: '⏳ в работе',
      waiting: '🕓 ждёт ответа',
      agreed: '🤝 согласовано',
      signed: '✍️ подписано',
      rejected: '❌ отказ',
    }[status] ?? status ?? ''
  );
}

const RISK_LABEL = { low: 'низкий', medium: 'средний', high: 'высокий' };

export function emailBlock(email, index) {
  const a = email;
  const head = [`[#${email.id}]`, formatDate(email.sent_at), escapeHtml(email.from_name || email.from_addr || '—')];
  const lines = [`<b>${head.join(' · ')}</b>`];
  if (email.subject) lines.push(`<i>${escapeHtml(truncate(email.subject, 120))}</i>`);
  if (a.summary) lines.push(escapeHtml(truncate(a.summary, 400)));
  const bits = [];
  if (a.kind) bits.push(kindLabel(a.kind));
  if (a.status) bits.push(statusLabel(a.status));
  if (a.amount) bits.push(formatMoney(a.amount, a.currency ?? 'RUB'));
  if (a.risk_level && a.risk_level !== 'low') bits.push(`риск: ${RISK_LABEL[a.risk_level]}`);
  if (bits.length) lines.push(bits.join(' · '));
  if (a.deadline) lines.push(`📅 срок: ${formatDate(a.deadline)}`);
  return lines.join('\n');
}

export function kindLabel(kind) {
  return (
    {
      contract: '📄 договор',
      negotiation: '💬 переговоры',
      contact: '📇 контакт',
      invoice: '🧾 счёт',
      other: '📬 прочее',
    }[kind] ?? kind ?? ''
  );
}

function answerTemplate({ store, question, intent }) {
  if (intent === 'contacts') return answerContacts({ store, question });
  if (intent === 'contracts') return answerContracts({ store, query: question });
  if (intent === 'tasks') return answerTasks({ store });

  const hits = store.searchEmails(question, { limit: 6 });
  const emails = hits.length ? hits : store.recentEmails({ limit: 5, sinceDays: 14 });

  if (!emails.length) {
    return { text: '📭 По этому запросу ничего не нашёл. Попробуйте /sync или переформулируйте.', emailIds: [] };
  }

  const lines = [hits.length ? `🔎 <b>Нашёл по запросу «${escapeHtml(truncate(question, 60))}»</b>` : '📬 <b>Последние письма</b>', ''];
  emails.forEach((e, i) => {
    lines.push(emailBlock(e, i));
    if (i < emails.length - 1) lines.push('');
  });
  lines.push('', '<i>Без ИИ — краткие карточки писем. Подключите LLM_API_KEY для развёрнутых ответов.</i>');
  return { text: lines.join('\n'), emailIds: emails.map((e) => e.id) };
}

export function buildContext(store, emails, cfg) {
  const blocks = [];
  let budget = cfg.agent.maxContextChars;
  for (const e of emails) {
    const body = truncate(String(e.body_text ?? '').replace(/\s+/g, ' ').trim(), 1400);
    const meta = [
      `[#${e.id}] ${formatDate(e.sent_at)} — ${e.from_name || e.from_addr || '—'} <${e.from_addr ?? ''}>`,
      `Тема: ${e.subject ?? '(без темы)'}`,
      e.kind ? `Тип: ${e.kind}; статус: ${e.status ?? '—'}; риск: ${e.risk_level ?? '—'}` : null,
      e.summary ? `Анализ: ${e.summary}` : null,
      e.amount ? `Сумма: ${formatMoney(e.amount, e.currency ?? 'RUB')}` : null,
      e.risks?.length ? `Риски: ${e.risks.join('; ')}` : null,
      e.asks?.length ? `Просят: ${e.asks.join('; ')}` : null,
      e.phones?.length ? `Телефоны: ${e.phones.map(formatPhone).join(', ')}` : null,
      e.addresses?.length ? `Адреса: ${e.addresses.join(' | ')}` : null,
      `Текст: ${body}`,
    ]
      .filter(Boolean)
      .join('\n');

    if (meta.length > budget) break;
    budget -= meta.length;
    blocks.push(meta);
  }
  return blocks.join('\n\n---\n\n');
}

const QA_SYSTEM = `Ты — ассистент руководителя по деловой переписке. Отвечай по-русски, кратко и по делу.
Опирайся ТОЛЬКО на фрагменты писем ниже. Не выдумывай факты, суммы, телефоны и адреса.
Если ответа в письмах нет — прямо скажи: «В письмах этого нет».
Ссылайся на источники в формате [#id, дата].
Телефоны и адреса приводи дословно из писем.`;

export async function answerWithLlm({ store, cfg, llm, question, intent }) {
  const hits = store.searchEmails(question, { limit: cfg.agent.maxContextEmails });
  const emails = hits.length ? hits : store.recentEmails({ limit: Math.min(5, cfg.agent.maxContextEmails), sinceDays: 30 });
  if (!emails.length) {
    return { text: '📭 В базе пока нет писем. Сделайте /sync.', emailIds: [], usedLlm: false };
  }

  const context = buildContext(store, emails, cfg);
  const hint = {
    contacts: 'Вопрос про контакты: назови ФИО, телефоны, адреса, компании.',
    contracts: 'Вопрос про договоры: номера, стороны, суммы, сроки, риски.',
    tasks: 'Вопрос про задачи: что ждёт ответа и какие сроки.',
    negotiations: 'Вопрос про переговоры: о чём договорились, что осталось.',
    general: '',
  }[intent];

  const res = await llm.chat({
    messages: [
      { role: 'system', content: QA_SYSTEM },
      {
        role: 'user',
        content: `${hint ? `${hint}\n\n` : ''}Вопрос: ${question}\n\nФрагменты писем:\n\n${context}`,
      },
    ],
    temperature: 0.1,
    maxTokens: 1200,
  });

  const answer = res.content.trim();
  const sources = emails.map((e) => `[#${e.id}] ${formatDate(e.sent_at)} — ${e.from_name || e.from_addr || ''}`).join('\n');
  return {
    text: `${answer}\n\n<i>Источники:</i>\n${escapeHtml(sources)}`,
    emailIds: emails.map((e) => e.id),
    usedLlm: true,
  };
}

/**
 * Main entry point for a text question.
 * @returns {Promise<{text:string, intent:string, emailIds:number[], usedLlm:boolean}>}
 */
export async function answerQuestion({ store, cfg, llm, question }) {
  const q = String(question ?? '').trim();
  if (!q) return { text: 'Пустой вопрос.', intent: 'general', emailIds: [], usedLlm: false };

  const intent = detectIntent(q);
  const llmReady = Boolean(llm?.available && cfg.has?.llm);

  if (!llmReady) {
    const res = answerTemplate({ store, question: q, intent });
    return { ...res, intent, usedLlm: false };
  }

  // Contacts are answered deterministically: phones/addresses must never be hallucinated.
  if (intent === 'contacts' && (guessEntities(q).phones.length || guessEntities(q).names.length)) {
    const res = answerContacts({ store, question: q });
    if (res.emailIds.length) return { ...res, intent, usedLlm: false };
  }

  try {
    const res = await answerWithLlm({ store, cfg, llm, question: q, intent });
    return { ...res, intent };
  } catch (err) {
    logger.warn(`LLM не ответил, откат к шаблону: ${err.message}`);
    const res = answerTemplate({ store, question: q, intent });
    return { ...res, intent, usedLlm: false };
  }
}

/** Short digest used by /digest and the CLI. */
export function buildDigest(store, { days = 7 } = {}) {
  const emails = store.recentEmails({ limit: 100, sinceDays: days });
  const stats = store.stats();
  const contracts = store.contracts({ limit: 5 });
  const tasks = store.tasks({ status: 'open', limit: 5 });

  const risky = emails.filter((e) => e.risk_level === 'high');
  const waiting = emails.filter((e) => e.status === 'waiting');
  const totalAmount = emails.reduce((sum, e) => sum + (Number(e.amount) || 0), 0);

  const lines = [`🗓 <b>Сводка за ${days} ${plural(days, 'день', 'дня', 'дней')}</b>`, ''];
  lines.push(`📬 Писем в базе: ${stats.emails} · разобрано: ${stats.analyzed}`);

  if (!emails.length) {
    lines.push('', 'За этот период новых писем не было.');
  } else {
    lines.push(`📥 За период: ${emails.length}` + (totalAmount ? ` · на сумму ${formatMoney(totalAmount)}` : ''));
  }

  if (contracts.length) {
    lines.push('', '<b>Договоры</b>');
    for (const c of contracts) {
      const bits = [c.number ? `№ ${escapeHtml(c.number)}` : null, c.counterparty ? escapeHtml(c.counterparty) : null]
        .filter(Boolean)
        .join(' · ');
      lines.push(`• ${bits || 'без номера'}${c.amount ? ` — ${formatMoney(c.amount, c.currency ?? 'RUB')}` : ''}`);
    }
  }

  if (risky.length) {
    lines.push('', '❗ <b>Высокий риск</b>');
    for (const e of risky.slice(0, 5)) {
      lines.push(`• [#${e.id}] ${escapeHtml(truncate(e.subject ?? '', 80))}${e.risks?.[0] ? ` — ${escapeHtml(truncate(e.risks[0], 100))}` : ''}`);
    }
  }

  if (waiting.length) {
    lines.push('', '🕓 <b>Ждут ответа</b>');
    for (const e of waiting.slice(0, 5)) {
      lines.push(`• [#${e.id}] ${escapeHtml(truncate(e.subject ?? '', 80))} — ${escapeHtml(e.from_name || e.from_addr || '')}`);
    }
  }

  if (tasks.length) {
    lines.push('', '📌 <b>Задачи</b>');
    for (const t of tasks) lines.push(`• ${escapeHtml(truncate(t.text, 120))}${t.due ? ` (до ${formatDate(t.due)})` : ''}`);
  }

  if (!emails.length && !contracts.length && !tasks.length) {
    lines.push('', 'Пока пусто. Сделайте /sync, чтобы я прочитал почту.');
  }

  return { text: lines.join('\n'), emailIds: emails.map((e) => e.id) };
}

function plural(n, one, few, many) {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
  return many;
}

export { humanRuDate };
