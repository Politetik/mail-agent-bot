/**
 * Rule-based extraction of Russian business entities from e-mail text.
 * Used as a fallback when no LLM key is configured, and as a safety net that
 * augments LLM output (LLMs regularly drop phone numbers and addresses).
 */

import { parseAmount } from './util.js';

const DIGIT_BOUNDARY_BEFORE = '(?<![\\d])';
const DIGIT_BOUNDARY_AFTER = '(?![\\d])';

const PHONE_RE = new RegExp(
  [
    // +7 / 8 / 7 with separators: +7 (495) 123-45-67, 8 916 123 45 67
    `${DIGIT_BOUNDARY_BEFORE}(?:\\+7|8|7)[\\s\\-(]*\\d{3}[\\s\\-)]*\\d{3}[\\s\\-]*\\d{2}[\\s\\-]*\\d{2}${DIGIT_BOUNDARY_AFTER}`,
    // international: +44 20 7946 0958
    `${DIGIT_BOUNDARY_BEFORE}\\+\\d{1,3}[\\s\\-()]*\\d{1,4}[\\s\\-()]*\\d{2,4}[\\s\\-()]*\\d{2,4}${DIGIT_BOUNDARY_AFTER}`,
    // 8-800-555-35-35
    `${DIGIT_BOUNDARY_BEFORE}8[\\s\\-]?800[\\s\\-]?\\d{3}[\\s\\-]?\\d{2}[\\s\\-]?\\d{2}${DIGIT_BOUNDARY_AFTER}`,
  ].join('|'),
  'g',
);

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

const ADDRESS_HINT =
  /(ул\.|улица|проспект|пр-т|пр\.|переулок|пер\.|бульвар|б-р|шоссе|ш\.|набережная|наб\.|площадь|пл\.|проезд|тупик|мкр\.?|микрорайон|корп\.|корпус|стр\.|строение|офис|оф\.|кв\.|квартира|помещени|г\.\s*[А-ЯЁ]|г\.о\.|пос\.|поселок|деревня|д\.\s*\d|дом\s*\d)/i;

const STREET_WORD =
  /(улица|ул\.|проспект|пр-т|переулок|пер\.|бульвар|б-р|шоссе|набережная|наб\.|площадь|пл\.|проезд|тупик|микрорайон|мкр)/i;

/**
 * Normalise a phone number to +7XXXXXXXXXX (RU) or +<digits> (international).
 * Returns null for implausible values.
 */
export function normalizePhone(raw) {
  const digits = String(raw ?? '').replace(/\D/g, '');
  if (digits.length < 10 || digits.length > 15) return null;
  if (digits.length === 11 && (digits[0] === '7' || digits[0] === '8')) {
    return `+7${digits.slice(1)}`;
  }
  if (digits.length === 10) return `+7${digits}`;
  return `+${digits}`;
}

export function formatPhone(phone) {
  const p = String(phone ?? '');
  if (/^\+7\d{10}$/.test(p)) {
    const d = p.slice(2);
    return `+7 (${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6, 8)}-${d.slice(8, 10)}`;
  }
  return p;
}

export function extractPhones(text) {
  const src = String(text ?? '');
  const found = [];
  const seen = new Set();
  for (const m of src.matchAll(PHONE_RE)) {
    const before = src[m.index - 1] ?? '';
    const after = src[m.index + m[0].length] ?? '';
    if (/\d/.test(before) || /\d/.test(after)) continue;
    const normalized = normalizePhone(m[0]);
    if (!normalized || seen.has(normalized)) continue;
    // Reject numbers that look like dates/accounts: phones start with +7, 8, 7 or +
    if (!/^(\+7|8|7|\+)/.test(m[0].trim())) continue;
    seen.add(normalized);
    found.push(normalized);
  }
  return found;
}

export function extractEmails(text) {
  const src = String(text ?? '');
  const out = [];
  const seen = new Set();
  for (const m of src.matchAll(EMAIL_RE)) {
    const addr = m[0].toLowerCase();
    if (seen.has(addr)) continue;
    seen.add(addr);
    out.push(addr);
  }
  return out;
}

function cleanAddress(s) {
  return String(s ?? '')
    .replace(/^[\s,;:•·\-–—|]+/, '')
    .replace(/^(?:адрес|почтовый адрес)\s*[:\-]?\s*/i, '')
    .replace(/[\s,;:•·|]+$/, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

const ADDRESS_START =
  /(?:г\.\s*[А-ЯЁ]|город\s+[А-ЯЁ]|ул\.|улица\s+[А-ЯЁ]|пр-т|проспект|пер\.|переулок|б-р|бульвар|шоссе|ш\.\s*[А-ЯЁ]|наб\.|набережная|пл\.|площадь|проезд|мкр\.?|микрорайон|пос\.|поселок|деревня)/i;

/** Cut the leading small talk from a line that contains an address. */
function trimToAddress(line) {
  const m = String(line).match(ADDRESS_START);
  if (!m || m.index === undefined || m.index === 0) return line;
  const before = line.slice(0, m.index);
  const index = before.match(/\b\d{6}\b[,\s]*$/);
  if (index) return line.slice(index.index);
  return line.slice(m.index);
}

/** Join hard-wrapped address lines back together. */
function mergeWrappedLines(lines) {
  const out = [];
  for (const line of lines) {
    const prev = out[out.length - 1];
    const wrapped =
      prev &&
      (/,\s*$/.test(prev) || /(?:^|\s)(?:г|ул|д|корп|стр|оф|кв|пр|пер|наб|пл|пос)\.$/i.test(prev)) &&
      line.length < 80 &&
      /^[А-ЯЁа-яё0-9]/.test(line);
    if (wrapped) {
      out[out.length - 1] = `${prev} ${line}`;
    } else {
      out.push(line);
    }
  }
  return out;
}

/**
 * Heuristic Russian postal-address extraction. Line-oriented on purpose:
 * splitting by sentences breaks at "ул. Ленина".
 */
export function extractAddresses(text) {
  const src = String(text ?? '');
  const lines = mergeWrappedLines(
    src
      .split(/\r?\n|;/)
      .map((s) => s.trim())
      .filter(Boolean),
  );

  const out = [];
  const seen = new Set();
  for (const line of lines) {
    if (line.length < 8 || line.length > 320) continue;
    if (/https?:/i.test(line)) continue;
    if (!ADDRESS_HINT.test(line)) continue;
    if (!/\d/.test(line)) continue;

    const chunk = cleanAddress(trimToAddress(line));
    if (chunk.length < 8 || chunk.length > 260) continue;

    const hasStreet = STREET_WORD.test(chunk);
    const hasHouse = /(?:д\.|дом|корп\.|стр\.|офис|оф\.|кв\.|помещени)\s*\d/i.test(chunk);
    const hasIndex = /\b\d{6}\b/.test(chunk);
    if (!hasStreet && !hasHouse) continue;
    if (!hasHouse && !hasIndex) continue;

    const key = chunk.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(chunk);
  }
  return out.slice(0, 10);
}

export function extractInn(text) {
  const src = String(text ?? '');
  const out = [];
  for (const m of src.matchAll(/(?:ИНН|инн)\s*[:№.]?\s*(\d{10}|\d{12})\b/g)) out.push(m[1]);
  for (const m of src.matchAll(/(?:КПП|кпп)\s*[:№.]?\s*(\d{9})\b/g)) out.push(`КПП ${m[1]}`);
  for (const m of src.matchAll(/(?:ОГРН|ОГРНИП|огрн)\s*[:№.]?\s*(\d{13}|\d{15})\b/g)) out.push(`ОГРН ${m[1]}`);
  return [...new Set(out)];
}

export function extractContractNumbers(text) {
  const src = String(text ?? '');
  const out = [];
  const patterns = [
    /(?:договор[а-яё]*|контракт[а-яё]*|соглашени[ея])\s*(?:№|N|номер|No\.?)\s*([^\s,;()«»"]{1,30})/gi,
    /(?:№|N)\s*(\d[\w\-/]{1,24})\s*(?:от\s*\d{1,2}[./-]\d{1,2}[./-]\d{2,4})?/g,
  ];
  for (const re of patterns) {
    for (const m of src.matchAll(re)) {
      const raw = cleanAddress(m[1]).replace(/[.,;:]$/, '');
      if (!/\d/.test(raw)) continue;
      if (/^(?:от|по|в|на|для|и|с)$/i.test(raw)) continue;
      if (raw.length > 30) continue;
      if (!out.includes(raw)) out.push(raw);
    }
  }
  return out.slice(0, 5);
}

export function extractAmounts(text) {
  const src = String(text ?? '');
  const out = [];
  const patterns = [
    /(\d{1,3}(?:[ \u00A0]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?)\s*(?:₽|руб(?:\.|лей|ля|ль)?|rub\b|р\.)/gi,
    /(\d+(?:[.,]\d+)?)\s*(млн|млрд|тыс)\.?\s*(?:руб|₽)?/gi,
  ];
  for (const re of patterns) {
    for (const m of src.matchAll(re)) {
      const value = parseAmount(`${m[1]} ${m[2] ?? ''}`);
      if (value === null || value < 100) continue;
      if (out.some((o) => o.amount === value)) continue;
      out.push({ amount: value, currency: 'RUB', raw: m[0].trim() });
    }
  }
  return out;
}

const MONTH_NAMES = {
  январ: 1, феврал: 2, март: 3, апрел: 4, ма: 5, июн: 6,
  июл: 7, август: 8, сентябр: 9, октябр: 10, ноябр: 11, декабр: 12,
};

function toIsoDate(y, m, d) {
  const year = y < 100 ? (y > 70 ? 1900 + y : 2000 + y) : y;
  if (m < 1 || m > 12 || d < 1 || d > 31) return null;
  const date = new Date(Date.UTC(year, m - 1, d));
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 10);
}

export function extractDates(text) {
  const src = String(text ?? '');
  const out = [];
  const push = (iso) => {
    if (iso && !out.includes(iso)) out.push(iso);
  };
  for (const m of src.matchAll(/\b(\d{1,2})[./-](\d{1,2})[./-](\d{2,4})\b/g)) {
    push(toIsoDate(Number(m[3]), Number(m[2]), Number(m[1])));
  }
  for (const m of src.matchAll(/\b(\d{4})-(\d{2})-(\d{2})\b/g)) {
    push(toIsoDate(Number(m[1]), Number(m[2]), Number(m[3])));
  }
  const monthRe =
    /\b(\d{1,2})\s+(январ[яе]|феврал[яе]|марта|апрел[яе]|ма[яй]|июн[яе]|июл[яе]|августа|сентябр[яе]|октябр[яе]|ноябр[яе]|декабр[яе])\s+(\d{4})/gi;
  for (const m of src.matchAll(monthRe)) {
    const key = Object.keys(MONTH_NAMES).find((k) => m[2].toLowerCase().startsWith(k));
    if (key) push(toIsoDate(Number(m[3]), MONTH_NAMES[key], Number(m[1])));
  }
  return out;
}

export function extractCompanies(text) {
  const src = String(text ?? '');
  const out = [];
  for (const m of src.matchAll(/(ООО|АО|ПАО|ЗАО|ОАО|ИП|ФГУП|ГУП|НКО)\s*[«"']([^»"'\n]{2,80})[»"']/g)) {
    const name = `${m[1]} «${m[2].trim()}»`;
    if (!out.includes(name)) out.push(name);
  }
  return out;
}

export function extractSignatureName(text) {
  const src = String(text ?? '');
  const m = src.match(
    /(?:с\s+уважением|уважением|best\s+regards|regards|спасибо)[\s,:—-]*\r?\n?[ \t]*([А-ЯЁ][а-яё-]+(?:[ \t]+[А-ЯЁ][а-яё-]+){0,2})/i,
  );
  return m ? m[1].trim() : null;
}

const PERSON_LINE = /^([А-ЯЁ][а-яё-]+(?:[ \t]+[А-ЯЁ][а-яё-]+){1,2})(?:[ \t]*,[ \t]*([^,]{3,70}))?[ \t]*$/;
const POSITION_RE =
  /^(генеральн\w+ директор|коммерческ\w+ директор|финансов\w+ директор|директор|руководител\w+|начальник\w*|менеджер\w*|специалист\w*|бухгалтер\w*|юрист\w*|аналитик\w*|инженер\w*|логист\w*|секретарь|ассистент\w*|владелец|основатель)/i;

/**
 * Find the person a contact block is about: "Анна Смирнова, руководитель отдела
 * поставок" followed by phone/e-mail lines. This beats using the From header,
 * because the From author is often just forwarding someone else's contacts.
 */
export function extractContactPerson(text) {
  const lines = String(text ?? '').split(/\r?\n/);
  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].trim();
    const m = line.match(PERSON_LINE);
    if (!m) continue;
    const rest = lines.slice(i + 1, i + 9).join('\n');
    if (!/(тел|phone|моб|@|\+7|8\s?\()/i.test(rest)) continue;

    const name = m[1].trim();
    let position = m[2]?.trim() ?? null;
    if (!position) {
      const next = lines[i + 1]?.trim() ?? '';
      if (POSITION_RE.test(next) && next.length < 70) position = next;
    }
    return { name, position };
  }
  return null;
}

const KIND_RULES = [
  { kind: 'invoice', re: /(сч[её]т на оплату|сч[её]т\s*№|invoice|акт сверки|упд)/i },
  { kind: 'contact', re: /(контакт|визитк|реквизит|меня зовут|перезвон|свяжитесь)/i },
  { kind: 'contract', re: /(договор|контракт|соглашени[ея]|спецификаци|дополнительное соглашение)/i },
  { kind: 'negotiation', re: /(переговор|предложени|коммерческое предложение|обсужд|условия|скидк)/i },
];

export function guessKind(subject, text) {
  const hay = `${subject ?? ''}\n${text ?? ''}`;
  for (const rule of KIND_RULES) if (rule.re.test(hay)) return rule.kind;
  return 'other';
}

export function guessStatus(text) {
  const t = String(text ?? '').toLowerCase();
  if (/(отказ|не согласн|не подходит|отклоня|расторгн|разрыва)/.test(t)) return 'rejected';
  if (/(подписал|подписано|согласовали|согласовано|оплатили|оплачен|принято|утвердили)/.test(t)) return 'agreed';
  if (/(жд[её]м|ожидаем|прошу ответить|напоминаю|просьба ответить|нет ответа)/.test(t)) return 'waiting';
  if (/(обсужд|уточн|предлага|рассмотр)/.test(t)) return 'in_progress';
  return 'new';
}

export function guessRiskLevel(text, kind) {
  const t = String(text ?? '').toLowerCase();
  const high = [
    'штраф', 'неустойк', 'пеня', 'предоплата 100', 'предоплата в размере 100',
    'без гарант', 'односторонн', 'расторжен', 'конфиденциальн', 'исключительн',
    'суд', 'арбитраж', 'просрочк',
  ];
  if (high.some((w) => t.includes(w))) return 'high';
  const medium = ['срок', 'изменен', 'повыш', 'индексац', 'аванс', 'гарант', 'залог'];
  if (medium.some((w) => t.includes(w))) return 'medium';
  return kind === 'contract' ? 'medium' : 'low';
}

export function extractAsks(text, max = 3) {
  const src = String(text ?? '').replace(/\s+/g, ' ');
  const sentences = src.split(/(?<=[.!?])\s+/);
  const out = [];
  for (const s of sentences) {
    if (!/(прошу|просим|необходимо|нужно|требуется|ожидаем от вас|ждём от вас|подтвердите|пришлите|направьте|согласуйте)/i.test(s)) continue;
    const clean = s.trim().replace(/^[-–—•\s]+/, '');
    if (clean.length < 12 || clean.length > 240) continue;
    if (!out.includes(clean)) out.push(clean);
    if (out.length >= max) break;
  }
  return out;
}

export function extractDeadline(text) {
  const src = String(text ?? '');
  const m = src.match(/(?:до|к|не\s+позднее|срок[а-я]*\s+до)\s+(\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4}-\d{2}-\d{2})/i);
  if (m) {
    const dates = extractDates(m[1]);
    if (dates[0]) return dates[0];
  }
  return null;
}

export function detectBulk(headers, fromAddress, subject) {
  const h = headers ?? {};
  const get = (name) => {
    if (typeof h.get === 'function') return h.get(name) ?? '';
    return h[name] ?? h[name.toLowerCase()] ?? '';
  };
  if (get('list-unsubscribe') || get('list-id') || String(get('precedence')).toLowerCase() === 'bulk') {
    return true;
  }
  const from = String(fromAddress ?? '').toLowerCase();
  if (/^(no-?reply|noreply|do-?not-?reply|mailer-daemon|postmaster|notification)/.test(from.split('@')[0] ?? '')) {
    return true;
  }
  if (/рассылк|unsubscribe|отписаться|дайджест|newsletter/i.test(String(subject ?? ''))) return true;
  return false;
}

/** Run all cheap extractors at once. */
export function extractAll(text, { subject = '', fromAddress = '', sentAt = null } = {}) {
  const body = String(text ?? '');
  return {
    phones: extractPhones(body),
    emails: extractEmails(body),
    addresses: extractAddresses(body),
    inn: extractInn(body),
    contractNumbers: extractContractNumbers(body),
    amounts: extractAmounts(body),
    dates: extractDates(body),
    companies: extractCompanies(body),
    signatureName: extractSignatureName(body),
    kind: guessKind(subject, body),
    status: guessStatus(body),
    riskLevel: guessRiskLevel(body, guessKind(subject, body)),
    asks: extractAsks(body),
    deadline: extractDeadline(body),
  };
}
