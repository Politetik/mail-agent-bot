import test from 'node:test';
import assert from 'node:assert/strict';
import { createConfig, parseIdList, LLM_PRESETS } from '../src/config.js';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

function withEnvFile(t, content) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mailbot-env-'));
  const file = path.join(dir, '.env');
  fs.writeFileSync(file, content, 'utf8');
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return file;
}

test('parseIdList accepts commas, spaces and semicolons', () => {
  assert.deepEqual(parseIdList('1, 2;3 4'), [1, 2, 3, 4]);
  assert.deepEqual(parseIdList(''), []);
  assert.deepEqual(parseIdList('abc, 42'), [42]);
});

test('config reads .env, strips quotes and applies defaults', (t) => {
  const envFile = withEnvFile(
    t,
    [
      '# comment',
      'TELEGRAM_BOT_TOKEN="123:ABC"',
      'ALLOWED_CHAT_IDS=111,222',
      'MAIL_USER=me@mail.ru',
      'MAIL_PASSWORD=secret',
      'LLM_API_KEY=sk-test',
      'SYNC_DAYS=14',
      'AUTO_SYNC_MIN=30',
    ].join('\n'),
  );
  const cfg = createConfig({ envFile, overrides: {} });
  assert.equal(cfg.telegram.token, '123:ABC');
  assert.deepEqual(cfg.telegram.allowedChatIds, [111, 222]);
  assert.equal(cfg.mail.imapHost, 'imap.mail.ru');
  assert.equal(cfg.mail.imapPort, 993);
  assert.equal(cfg.sync.days, 14);
  assert.equal(cfg.sync.intervalMin, 30);
  assert.equal(cfg.has.mail, true);
  assert.equal(cfg.has.llm, true);
  assert.equal(cfg.has.telegram, true);
});

test('overrides win over .env', (t) => {
  const envFile = withEnvFile(t, 'SYNC_DAYS=14\n');
  const cfg = createConfig({ envFile, overrides: { SYNC_DAYS: '3' } });
  assert.equal(cfg.sync.days, 3);
});

test('local OpenAI-compatible server needs no API key', (t) => {
  const envFile = withEnvFile(t, `LLM_PROVIDER=ollama\nLLM_BASE_URL=${LLM_PRESETS.ollama.baseUrl}\n`);
  const cfg = createConfig({ envFile });
  assert.equal(cfg.llm.provider, 'ollama');
  assert.equal(cfg.llm.model, LLM_PRESETS.ollama.model);
  assert.equal(cfg.has.llm, true);
});

test('missing credentials produce honest capability flags', (t) => {
  const envFile = withEnvFile(t, '');
  const cfg = createConfig({ envFile, overrides: {} });
  assert.equal(cfg.has.telegram, false);
  assert.equal(cfg.has.mail, false);
  assert.equal(cfg.has.stt, false);
  assert.equal(cfg.db.path.endsWith('mail-agent.db'), true);
});

test('mail folders are parsed from a list', (t) => {
  const envFile = withEnvFile(t, 'MAIL_FOLDERS=INBOX, Отправленные;Спам\n');
  const cfg = createConfig({ envFile });
  assert.deepEqual(cfg.mail.folders, ['INBOX', 'Отправленные', 'Спам']);
});
