import { extractAll, extractContactPerson, formatPhone, normalizePhone } from './patterns.js';
import { formatDate, logger, parseJsonLoose, truncate } from './util.js';

const KINDS = new Set(['contract', 'negotiation', 'contact', 'invoice', 'other']);
const STATUSES = new Set(['new', 'in_progress', 'waiting', 'agreed', 'signed', 'rejected']);
const RISKS = new Set(['low', 'medium', 'high']);

const SYSTEM_PROMPT = `Ты — ассистент, который разбирает деловую переписку на русском языке.
Проанализируй ОДНО письмо и верни СТРОГО один JSON-объект, без markdown и пояснений.

Схема ответа:
{
  "kind": "contract" | "negotiation" | "contact" | "invoice" | "other",
  "summary": "суть письма в 1-3 предложениях, по-русски",
  "counterparty": "название компании или ФИО контрагента, либо null",
  "contract_number": "номер договора/счёта, либо null",
  "amount": число (только цифры, в рублях или валюте письма) либо null,
  "currency": "RUB" | "USD" | "EUR" | null,
  "deadline": "YYYY-MM-DD либо null",
  "status": "new" | "in_progress" | "waiting" | "agreed" | "signed" | "rejected",
  "risk_level": "low" | "medium" | "high",
  "risks": ["конкретные риски, штрафы, невыгодные условия — до 3 пунктов"],
  "asks": ["что просят сделать, подтвердить или прислать — до 3 пунктов"],
  "contacts": [
    {
      "name": "ФИО или null",
      "position": "должность или null",
      "company": "компания или null",
      "phone": "телефон в формате +7XXXXXXXXXX или null",
      "email": "почта или null",
      "address": "почтовый адрес одной строкой или null"
    }
  ]
}

Правила:
1. Не придумывай данные. Если чего-то нет в тексте — ставь null или пустой массив.
2. Телефоны и адреса копируй ТОЧНО из текста (телефон нормализуй в +7XXXXXXXXXX).
3. status="waiting", если от нас ждут ответа; "agreed"/"signed" — если договорённость уже достигнута; "rejected" — если отказали.
4. risk_level="high" при штрафах, неустойках, 100% предоплате, односторонних правах, суде.
5. Ответ только JSON.`;

function stripQuotedReply(text) {
  const src = String(text ?? '');
  const markers = [
    /\n\s*-{2,}\s*(?:Original Message|Исходное сообщение|Пересылаемое сообщение)\s*-{2,}/i,
    /\n\s*От:\s.*\n\s*Отправлено:/i,
    /\n\s*On .{5,80} wrote:/i,
    /\n\s*>{1,}\s?/,
  ];
  let cut = src.length;
  for (const re of markers) {
    const m = src.match(re);
    if (m && m.index !== undefined && m.index > 40) cut = Math.min(cut, m.index);
  }
  return src.slice(0, cut);
}

export function cleanBody(text) {
  return stripQuotedReply(text)
    .replace(/\r/g, '')
    .replace(/[ \t\u00a0]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function firstSentences(text, maxChars = 320) {
  const src = cleanBody(text);
  if (!src) return '';
  const sentences = src.split(/(?<=[.!?])\s+/).slice(0, 3).join(' ');
  return truncate(sentences || src, maxChars);
}

function fallbackSummary(email) {
  const body = firstSentences(email.body_text, 280);
  if (body) return body;
  return truncate(String(email.subject ?? '(без темы)'), 200);
}

function domainOf(address) {
  const m = String(address ?? '').toLowerCase().match(/@([\w.-]+)$/);
  return m ? m[1] : null;
}

function companyFromDomain(address) {
  const domain = domainOf(address);
  if (!domain) return null;
  const free = /(mail\.ru|yandex|gmail|inbox|list|bk|yahoo|outlook|hotmail|rambler|icloud)/i;
  if (free.test(domain)) return null;
  const core = domain.replace(/\.(ru|com|net|org|io|su|by|kz)$/i, '').split('.').pop();
  if (!core) return null;
  return core.charAt(0).toUpperCase() + core.slice(1);
}

function guessPosition(text) {
  const m = String(text ?? '').match(
    /\b(генеральн\w+ директор|директор|коммерческ\w+ директор|финансов\w+ директор|руководител\w+|менеджер|специалист|бухгалтер|юрист|аналитик|инженер)\b/i,
  );
  return m ? m[0].toLowerCase() : null;
}

function dedupeContacts(list) {
  const out = [];
  const seen = new Set();
  for (const c of list) {
    if (!c) continue;
    const key = [c.name, c.phone, c.email, c.address].map((x) => String(x ?? '').trim().toLowerCase()).join('|');
    if (key.replace(/\|/g, '') === '') continue;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(c);
  }
  return out.slice(0, 6);
}

/** Contacts derived purely from rules (sender + phones + addresses found in body). */
export function ruleContacts(email, rules) {
  const person = extractContactPerson(email.body_text);
  const name = person?.name || rules.signatureName || email.from_name || null;
  const company = rules.companies[0] ?? companyFromDomain(email.from_addr);
  const position = person?.position ?? guessPosition(email.body_text);
  const inn = rules.inn.find((x) => /^\d{10}$/.test(x)) ?? null;
  const sender = String(email.from_addr ?? '').toLowerCase();
  const bodyEmails = (rules.emails ?? []).filter((e) => e !== sender);
  const personEmail = person ? bodyEmails[0] ?? email.from_addr ?? null : email.from_addr ?? null;
  const base = {
    name: name || null,
    position,
    company: company || null,
    email: personEmail,
    inn,
  };

  const list = [];
  const phones = rules.phones.slice(0, 3);
  const addresses = rules.addresses.slice(0, 2);

  if (!phones.length && !addresses.length) {
    if (name || company) list.push(base);
    return list;
  }
  for (const phone of phones) list.push({ ...base, phone, address: addresses[0] ?? null });
  if (!phones.length) for (const address of addresses) list.push({ ...base, address });
  for (const extra of addresses.slice(1)) list.push({ ...base, address: extra });
  return dedupeContacts(list);
}

function ruleAnalysis(email) {
  const rules = extractAll(email.body_text, {
    subject: email.subject,
    fromAddress: email.from_addr,
    sentAt: email.sent_at,
  });
  const amount = rules.amounts.length ? Math.max(...rules.amounts.map((a) => a.amount)) : null;
  return {
    kind: rules.kind,
    summary: fallbackSummary(email),
    counterparty: rules.companies[0] ?? companyFromDomain(email.from_addr),
    contract_number: rules.contractNumbers[0] ?? null,
    amount,
    currency: amount ? 'RUB' : null,
    deadline: rules.deadline,
    status: rules.status,
    risk_level: rules.riskLevel,
    risks: [],
    asks: rules.asks,
    contacts: ruleContacts(email, rules),
    phones: rules.phones,
    addresses: rules.addresses,
    confidence: 0.35,
    engine: 'rules',
    model: null,
    _rules: rules,
  };
}

function normalizeString(v, max = 400) {
  if (v === null || v === undefined) return null;
  const s = String(v).replace(/\s+/g, ' ').trim();
  if (!s || s.toLowerCase() === 'null' || s === '—') return null;
  return truncate(s, max);
}

function normalizeArray(v, max = 5, maxLen = 300) {
  if (!Array.isArray(v)) return [];
  return v
    .map((x) => normalizeString(x, maxLen))
    .filter(Boolean)
    .slice(0, max);
}

function normalizeDate(v) {
  const s = normalizeString(v, 32);
  if (!s) return null;
  const m = s.match(/(\d{4})-(\d{2})-(\d{2})/) || s.match(/(\d{1,2})[./](\d{1,2})[./](\d{4})/);
  if (!m) return null;
  if (m[1].length === 4) return `${m[1]}-${m[2]}-${m[3]}`;
  return `${m[3]}-${String(m[2]).padStart(2, '0')}-${String(m[1]).padStart(2, '0')}`;
}

function normalizeLlmOutput(raw) {
  if (!raw || typeof raw !== 'object') return null;
  const amount = Number(raw.amount);
  const contacts = Array.isArray(raw.contacts)
    ? raw.contacts
        .map((c) => ({
          name: normalizeString(c?.name, 120),
          position: normalizeString(c?.position, 120),
          company: normalizeString(c?.company, 160),
          phone: normalizePhone(c?.phone),
          email: normalizeString(c?.email, 160)?.toLowerCase() ?? null,
          address: normalizeString(c?.address, 240),
        }))
        .filter((c) => c.name || c.phone || c.email || c.address)
    : [];

  return {
    kind: KINDS.has(raw.kind) ? raw.kind : null,
    summary: normalizeString(raw.summary, 700),
    counterparty: normalizeString(raw.counterparty, 200),
    contract_number: normalizeString(raw.contract_number, 60),
    amount: Number.isFinite(amount) && amount > 0 ? amount : null,
    currency: /^(rub|usd|eur|kzt|byn)$/i.test(String(raw.currency ?? '')) ? String(raw.currency).toUpperCase() : null,
    deadline: normalizeDate(raw.deadline),
    status: STATUSES.has(raw.status) ? raw.status : null,
    risk_level: RISKS.has(raw.risk_level) ? raw.risk_level : null,
    risks: normalizeArray(raw.risks, 3, 260),
    asks: normalizeArray(raw.asks, 3, 300),
    contacts,
  };
}

function buildUserMessage(email) {
  const body = truncate(cleanBody(email.body_text), 6000);
  return [
    `От: ${email.from_name ? `${email.from_name} <${email.from_addr}>` : email.from_addr ?? 'неизвестно'}`,
    `Кому: ${email.to_addrs ?? '—'}`,
    `Тема: ${email.subject ?? '(без темы)'}`,
    `Дата: ${email.sent_at ? formatDate(email.sent_at) : '—'}`,
    '',
    'Текст письма:',
    body || '(пусто)',
  ].join('\n');
}

/**
 * Analyse one e-mail. Uses the LLM when available, always augments with rules.
 * @returns {Promise<object>} analysis row ready for store.saveAnalysis
 */
export async function analyzeEmail(email, { llm = null, model = null } = {}) {
  const rules = ruleAnalysis(email);

  if (!llm?.available) return { ...rules, engine: 'rules' };

  let llmOut = null;
  try {
    const res = await llm.chat({
      messages: [
        { role: 'system', content: SYSTEM_PROMPT },
        { role: 'user', content: buildUserMessage(email) },
      ],
      temperature: 0,
      maxTokens: 2000,
      json: true,
    });
    llmOut = normalizeLlmOutput(parseJsonLoose(res.content));
    if (!llmOut) logger.warn(`LLM вернул не-JSON для письма #${email.id ?? '?'}`);
  } catch (err) {
    logger.warn(`Ошибка LLM при анализе письма #${email.id ?? '?'}: ${err.message}`);
  }

  if (!llmOut) return { ...rules, engine: 'rules' };

  const mergedPhones = [...new Set([...(llmOut.contacts.map((c) => c.phone) ?? []), ...rules._rules.phones].filter(Boolean))];
  const mergedAddresses = [
    ...new Set([...(llmOut.contacts.map((c) => c.address) ?? []), ...rules._rules.addresses].filter(Boolean)),
  ];

  const contacts = dedupeContacts([
    ...llmOut.contacts.map((c) => ({ ...c, phone: c.phone ?? null })),
    ...rules.contacts.filter((c) => !llmOut.contacts.some((x) => x.phone && x.phone === c.phone)),
  ]);

  return {
    kind: llmOut.kind ?? rules.kind,
    summary: llmOut.summary ?? rules.summary,
    counterparty: llmOut.counterparty ?? rules.counterparty,
    contract_number: llmOut.contract_number ?? rules.contract_number,
    amount: llmOut.amount ?? rules.amount,
    currency: llmOut.currency ?? rules.currency,
    deadline: llmOut.deadline ?? rules.deadline,
    status: llmOut.status ?? rules.status,
    risk_level: llmOut.risk_level ?? rules.risk_level,
    risks: llmOut.risks,
    asks: llmOut.asks.length ? llmOut.asks : rules.asks,
    contacts,
    phones: mergedPhones,
    addresses: mergedAddresses,
    confidence: 0.8,
    engine: 'llm',
    model: model ?? llm.model,
  };
}

export { ruleAnalysis, normalizeLlmOutput };
export { formatPhone };
