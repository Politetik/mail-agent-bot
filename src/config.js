import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

/** Minimal .env parser: KEY=VALUE, # comments, optional surrounding quotes. */
export function loadEnvFile(file = path.join(ROOT, '.env')) {
  const out = {};
  if (!fs.existsSync(file)) return out;
  const text = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 0) continue;
    const key = line.slice(0, eq).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) continue;
    let val = line.slice(eq + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"') && val.length > 1) ||
      (val.startsWith("'") && val.endsWith("'") && val.length > 1)
    ) {
      val = val.slice(1, -1);
    }
    out[key] = val;
  }
  return out;
}

export const LLM_PRESETS = {
  deepseek: { baseUrl: 'https://api.deepseek.com/v1', model: 'deepseek-chat' },
  openai: { baseUrl: 'https://api.openai.com/v1', model: 'gpt-4o-mini' },
  openrouter: { baseUrl: 'https://openrouter.ai/api/v1', model: 'deepseek/deepseek-chat' },
  ollama: { baseUrl: 'http://127.0.0.1:11434/v1', model: 'qwen2.5:7b' },
  lmstudio: { baseUrl: 'http://127.0.0.1:1234/v1', model: 'local-model' },
};

function str(v, dflt = '') {
  if (v === undefined || v === null) return dflt;
  const s = String(v).trim();
  return s === '' ? dflt : s;
}

function num(v, dflt) {
  const s = str(v, '');
  if (s === '') return dflt;
  const n = Number(s);
  return Number.isFinite(n) ? n : dflt;
}

function bool(v, dflt) {
  const s = str(v, '').toLowerCase();
  if (!s) return dflt;
  return ['1', 'true', 'yes', 'y', 'on', 'да'].includes(s);
}

export function parseIdList(v) {
  if (Array.isArray(v)) {
    return v.map((x) => Number(x)).filter((n) => Number.isFinite(n));
  }
  const s = str(v, '');
  if (!s) return [];
  return s
    .split(/[\s,;]+/)
    .map((x) => x.trim())
    .filter(Boolean)
    .map((x) => Number(x))
    .filter((n) => Number.isFinite(n));
}

/**
 * Build the runtime config. Precedence: overrides > process.env > .env file > defaults.
 */
export function createConfig({ envFile, overrides = {} } = {}) {
  const fileEnv = loadEnvFile(envFile);
  const env = { ...fileEnv, ...process.env, ...overrides };

  const provider = str(env.LLM_PROVIDER, 'deepseek').toLowerCase();
  const preset = LLM_PRESETS[provider] ?? LLM_PRESETS.deepseek;

  const sttProvider = str(env.STT_PROVIDER, 'openai').toLowerCase();
  const sttPreset =
    sttProvider === 'deepseek'
      ? { baseUrl: 'https://api.openai.com/v1', model: 'whisper-1' }
      : { baseUrl: str(env.OPENAI_BASE_URL, 'https://api.openai.com/v1'), model: 'whisper-1' };

  const cfg = {
    root: ROOT,
    envFile: envFile ?? path.join(ROOT, '.env'),

    telegram: {
      token: str(env.TELEGRAM_BOT_TOKEN),
      allowedChatIds: parseIdList(env.ALLOWED_CHAT_IDS),
      apiBase: str(env.TELEGRAM_API_BASE, 'https://api.telegram.org'),
    },

    mail: {
      imapHost: str(env.MAIL_IMAP_HOST, 'imap.mail.ru'),
      imapPort: num(env.MAIL_IMAP_PORT, 993),
      smtpHost: str(env.MAIL_SMTP_HOST, 'smtp.mail.ru'),
      smtpPort: num(env.MAIL_SMTP_PORT, 465),
      user: str(env.MAIL_USER),
      password: str(env.MAIL_PASSWORD),
      folders: str(env.MAIL_FOLDERS, 'INBOX')
        .split(/[,;]/)
        .map((s) => s.trim())
        .filter(Boolean),
      includeSent: bool(env.MAIL_INCLUDE_SENT, true),
    },

    llm: {
      provider,
      baseUrl: str(env.LLM_BASE_URL, preset.baseUrl),
      apiKey: str(env.LLM_API_KEY, str(env.DEEPSEEK_API_KEY, str(env.OPENAI_API_KEY, ''))),
      model: str(env.LLM_MODEL, preset.model),
      maxTokens: num(env.LLM_MAX_TOKENS, 1200),
      temperature: num(env.LLM_TEMPERATURE, 0.2),
      timeoutMs: num(env.LLM_TIMEOUT_MS, 120000),
      enabled: bool(env.LLM_ENABLED, true),
    },

    stt: {
      enabled: bool(env.STT_ENABLED, true),
      baseUrl: str(env.STT_BASE_URL, sttPreset.baseUrl),
      apiKey: str(env.STT_API_KEY, str(env.OPENAI_API_KEY, '')),
      model: str(env.STT_MODEL, sttPreset.model),
      language: str(env.STT_LANGUAGE, 'ru'),
      timeoutMs: num(env.STT_TIMEOUT_MS, 120000),
    },

    db: {
      path: path.resolve(ROOT, str(env.DB_PATH, 'data/mail-agent.db')),
    },

    sync: {
      days: num(env.SYNC_DAYS, 30),
      limit: num(env.SYNC_LIMIT, 300),
      batchSize: num(env.SYNC_BATCH, 20),
      intervalMin: num(env.AUTO_SYNC_MIN, 0),
      analyzeLimit: num(env.ANALYZE_LIMIT, 60),
      skipBulk: bool(env.SKIP_BULK, true),
      maxBodyChars: num(env.MAX_BODY_CHARS, 20000),
    },

    agent: {
      maxContextEmails: num(env.CONTEXT_EMAILS, 8),
      maxContextChars: num(env.CONTEXT_CHARS, 14000),
      timezone: str(env.TIMEZONE, 'Europe/Moscow'),
      language: str(env.AGENT_LANGUAGE, 'ru'),
    },

    logLevel: str(env.LOG_LEVEL, 'info'),
  };

  cfg.has = {
    telegram: Boolean(cfg.telegram.token),
    mail: Boolean(cfg.mail.user && cfg.mail.password),
    llm: Boolean(cfg.llm.enabled && cfg.llm.apiKey) || /127\.0\.0\.1|localhost/i.test(cfg.llm.baseUrl),
    stt: Boolean(cfg.stt.enabled && cfg.stt.apiKey),
  };

  // A local OpenAI-compatible server (Ollama, LM Studio) needs no key.
  if (/127\.0\.0\.1|localhost/i.test(cfg.llm.baseUrl)) cfg.has.llm = cfg.llm.enabled;

  return cfg;
}

/** Config summary that is safe to print (no secrets). */
export function describeConfig(cfg) {
  return {
    telegram: cfg.has.telegram ? `настроен, чатов в белом списке: ${cfg.telegram.allowedChatIds.length}` : 'НЕ настроен',
    mail: cfg.has.mail ? `${cfg.mail.user} @ ${cfg.mail.imapHost}` : 'НЕ настроен',
    llm: cfg.has.llm ? `${cfg.llm.provider}/${cfg.llm.model} (${cfg.llm.baseUrl})` : 'НЕ настроен (будут работать правила)',
    stt: cfg.has.stt ? `${cfg.stt.model} (${cfg.stt.baseUrl})` : 'НЕ настроен (голос отключён)',
    db: cfg.db.path,
  };
}
