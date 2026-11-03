import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { createBot } from '../src/bot.js';
import { createConfig } from '../src/config.js';
import { createLlm } from '../src/llm.js';
import { tempDb, seedStore } from './helpers.js';

function jsonResponse(payload) {
  return new Response(JSON.stringify(payload), { status: 200, headers: { 'content-type': 'application/json' } });
}

function stubTelegram(t) {
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, opts = {}) => {
    const method = String(url).split('/').pop();
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ method, body, url: String(url) });
    if (method === 'getMe') return jsonResponse({ ok: true, result: { username: 'test_bot' } });
    if (method === 'getUpdates') return jsonResponse({ ok: true, result: [] });
    return jsonResponse({ ok: true, result: { message_id: calls.length } });
  };
  t.after(() => {
    globalThis.fetch = original;
  });
  return calls;
}

function makeCfg(t, overrides = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mailbot-cfg-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return createConfig({
    envFile: path.join(dir, 'missing.env'),
    overrides: {
      TELEGRAM_BOT_TOKEN: '123:TEST',
      ALLOWED_CHAT_IDS: '1',
      DB_PATH: path.join(dir, 'bot.db'),
      ...overrides,
    },
  });
}

const update = (chatId, extra = {}) => ({
  message: { chat: { id: chatId }, from: { id: chatId, is_bot: false }, ...extra },
});

test('unknown chat gets its id and no data', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(update(999, { text: '/start' }).message);

  assert.equal(calls.length, 1);
  assert.match(calls[0].body.text, /999/);
  assert.match(calls[0].body.text, /ALLOWED_CHAT_IDS/);
  assert.doesNotMatch(calls[0].body.text, /почтовый ИИ-агент/i);
});

test('/start and /help send the keyboard and instructions', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(update(1, { text: '/start' }).message);
  assert.match(calls[0].body.text, /ИИ-агент/);
  assert.ok(calls[0].body.reply_markup.keyboard.length >= 2);

  await bot.handleMessage(update(1, { text: '/help' }).message);
  assert.match(calls[1].body.text, /\/sync/);
  assert.match(calls[1].body.text, /\/digest/);
});

test('a text question is answered from the database', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  await seedStore(store);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(update(1, { text: 'Какой телефон у Анны Смирновой?' }).message);

  const text = calls.map((c) => c.body.text).join('\n');
  assert.match(text, /\+7 \(916\) 555-77-88/);
});

test('keyboard buttons are routed to commands', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  await seedStore(store);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(update(1, { text: '📇 Контакты' }).message);
  assert.match(calls[0].body.text, /Контакты из писем/);
});

test('voice message without STT explains how to enable it', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(
    update(1, { voice: { file_id: 'abc', duration: 3, mime_type: 'audio/ogg' } }).message,
  );

  assert.match(calls[0].body.text, /распознавания речи|OPENAI_API_KEY/i);
});

test('/sync without mail settings explains configuration', async (t) => {
  const calls = stubTelegram(t);
  const store = tempDb(t);
  const cfg = makeCfg(t);
  const bot = createBot({ cfg, store, llm: createLlm(cfg) });

  await bot.handleMessage(update(1, { text: '/sync' }).message);
  assert.match(calls[0].body.text, /MAIL_USER/);
});
