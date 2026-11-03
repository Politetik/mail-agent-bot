export const LEVELS = { debug: 10, info: 20, warn: 30, error: 40 };

let currentLevel = LEVELS.info;

export function setLogLevel(name) {
  currentLevel = LEVELS[String(name).toLowerCase()] ?? LEVELS.info;
}

function emit(level, msg, extra) {
  if ((LEVELS[level] ?? LEVELS.info) < currentLevel) return;
  const line = `[${new Date().toISOString()}] ${level.toUpperCase().padEnd(5)} ${msg}`;
  if (extra === undefined) console.error(line);
  else if (typeof extra === 'string') console.error(`${line} ${extra}`);
  else console.error(line, safeJson(extra));
}

export const logger = {
  debug: (m, e) => emit('debug', m, e),
  info: (m, e) => emit('info', m, e),
  warn: (m, e) => emit('warn', m, e),
  error: (m, e) => emit('error', m, e),
};

export function safeJson(v) {
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

export function escapeHtml(s) {
  return String(s ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;');
}

export function truncate(s, max) {
  const str = String(s ?? '');
  if (str.length <= max) return str;
  return `${str.slice(0, Math.max(0, max - 1)).trimEnd()}…`;
}

/** Split a long message into Telegram-safe chunks, preferring natural boundaries. */
export function chunkText(text, limit = 3800) {
  const s = String(text ?? '');
  if (s.length <= limit) return [s];
  const parts = [];
  let rest = s;
  while (rest.length > limit) {
    let cut = rest.lastIndexOf('\n\n', limit);
    if (cut < limit * 0.55) cut = rest.lastIndexOf('\n', limit);
    if (cut < limit * 0.55) cut = rest.lastIndexOf(' ', limit);
    if (cut < limit * 0.55) cut = limit;
    parts.push(rest.slice(0, cut).trim());
    rest = rest.slice(cut).trim();
  }
  if (rest) parts.push(rest);
  return parts;
}

export function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function nowIso() {
  return new Date().toISOString();
}

const MONTHS_RU = [
  'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
  'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря',
];

/** dd.MM.yyyy HH:mm in the agent timezone. */
export function formatDateTime(value, timeZone = 'Europe/Moscow') {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('ru-RU', {
    timeZone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).formatToParts(d);
  const get = (t) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('day')}.${get('month')}.${get('year')} ${get('hour')}:${get('minute')}`;
}

export function formatDate(value, timeZone = 'Europe/Moscow') {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('ru-RU', {
    timeZone,
    day: '2-digit',
    month: '2-digit',
    year: 'numeric',
  }).formatToParts(d);
  const get = (t) => parts.find((p) => p.type === t)?.value ?? '';
  return `${get('day')}.${get('month')}.${get('year')}`;
}

export function humanRuDate(value, timeZone = 'Europe/Moscow') {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return '—';
  const parts = new Intl.DateTimeFormat('ru-RU', {
    timeZone,
    day: 'numeric',
    month: 'long',
    year: 'numeric',
  }).formatToParts(d);
  const day = parts.find((p) => p.type === 'day')?.value ?? '';
  const month = parts.find((p) => p.type === 'month')?.value ?? '';
  const year = parts.find((p) => p.type === 'year')?.value ?? '';
  return `${day} ${month} ${year} г.`;
}

export { MONTHS_RU };

/** Parse a money-ish string into a number. Handles "1 250 000,50" and "1,25 млн". */
export function parseAmount(raw) {
  if (typeof raw === 'number') return Number.isFinite(raw) ? raw : null;
  let s = String(raw ?? '').toLowerCase().replace(/\u00a0/g, ' ').trim();
  if (!s) return null;
  let multiplier = 1;
  if (/(млн|million)/.test(s)) multiplier = 1_000_000;
  else if (/(млрд|billion)/.test(s)) multiplier = 1_000_000_000;
  else if (/(тыс|thousand)/.test(s)) multiplier = 1_000;
  s = s.replace(/(руб\.?|рублей|рубля|₽|rub|р\.|млн|млрд|тыс\.?|thousand|million|billion)/g, ' ');
  s = s.replace(/[^\d.,\-]/g, '').trim();
  if (!s) return null;
  const lastComma = s.lastIndexOf(',');
  const lastDot = s.lastIndexOf('.');
  if (lastComma >= 0 && lastDot >= 0) {
    if (lastComma > lastDot) s = s.replaceAll('.', '').replace(',', '.');
    else s = s.replaceAll(',', '');
  } else if (lastComma >= 0) {
    const decimals = s.length - lastComma - 1;
    s = decimals === 3 && !s.includes('.') ? s.replaceAll(',', '') : s.replace(',', '.');
  }
  const n = Number(s);
  if (!Number.isFinite(n)) return null;
  return Math.round(n * multiplier * 100) / 100;
}

export function formatMoney(amount, currency = 'RUB') {
  const n = Number(amount);
  if (!Number.isFinite(n)) return '—';
  const symbol = { RUB: '₽', USD: '$', EUR: '€', KZT: '₸', BYN: 'Br' }[currency] ?? currency;
  return `${new Intl.NumberFormat('ru-RU', { maximumFractionDigits: 2 }).format(n)} ${symbol}`;
}

/** Safe JSON parse that tolerates ```json fences and trailing prose. */
export function parseJsonLoose(text) {
  if (text && typeof text === 'object') return text;
  let s = String(text ?? '').trim();
  if (!s) return null;
  s = s.replace(/^```(?:json)?\s*/i, '').replace(/```\s*$/i, '').trim();
  try {
    return JSON.parse(s);
  } catch {
    /* fall through */
  }
  const start = s.indexOf('{');
  const end = s.lastIndexOf('}');
  if (start >= 0 && end > start) {
    try {
      return JSON.parse(s.slice(start, end + 1));
    } catch {
      return null;
    }
  }
  return null;
}
