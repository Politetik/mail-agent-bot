import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { analyzeEmail } from '../src/analyze.js';
import { answerQuestion } from '../src/answer.js';
import { createConfig } from '../src/config.js';
import { createLlm } from '../src/llm.js';
import { tempDb } from './helpers.js';

const LLM_JSON = {
  kind: 'contract',
  summary: 'Контрагент прислал договор поставки на 1,25 млн и просит подписать.',
  counterparty: 'ООО «Ромашка»',
  contract_number: '45/2025',
  amount: 1250000,
  currency: 'RUB',
  deadline: '2025-12-15',
  status: 'waiting',
  risk_level: 'high',
  risks: ['неустойка 0,1% в день'],
  asks: ['подписать договор'],
  contacts: [{ name: 'Иван Петров', position: 'менеджер', company: 'ООО «Ромашка»', phone: null, email: 'ivan@romashka.ru', address: null }],
};

/** Spins up a fake OpenAI-compatible server on localhost. */
async function fakeLlm(t) {
  const state = { requests: [] };
  const server = http.createServer(async (req, res) => {
    let raw = '';
    for await (const chunk of req) raw += chunk;
    const body = raw ? JSON.parse(raw) : {};
    state.requests.push({ url: req.url, body });

    const system = body.messages?.[0]?.content ?? '';
    const isExtraction = /JSON-объект|Схема ответа/i.test(system);
    const content = isExtraction
      ? JSON.stringify(LLM_JSON)
      : 'По переписке: договор № 45/2025 с ООО «Ромашка» на 1 250 000 ₽, ждут подписания [#1, 03.11.2025].';

    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(
      JSON.stringify({
        id: 'chatcmpl-test',
        object: 'chat.completion',
        model: body.model,
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
      }),
    );
  });

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const { port } = server.address();
  return { url: `http://127.0.0.1:${port}/v1`, state };
}

function cfgWith(t, baseUrl) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mailbot-llm-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return createConfig({
    envFile: path.join(dir, 'missing.env'),
    overrides: {
      LLM_BASE_URL: baseUrl,
      LLM_API_KEY: 'test-key',
      LLM_MODEL: 'fake-model',
      STT_API_KEY: '',
      DB_PATH: path.join(dir, 'llm.db'),
    },
  });
}

test('LLM analysis is requested as JSON and merged with rule extraction', async (t) => {
  const fake = await fakeLlm(t);
  const cfg = cfgWith(t, fake.url);
  assert.equal(cfg.has.llm, true);

  const llm = createLlm(cfg);
  const email = {
    id: 1,
    from_name: 'Иван Петров',
    from_addr: 'ivan@romashka.ru',
    to_addrs: 'me@mail.ru',
    subject: 'Договор поставки № 45/2025',
    sent_at: new Date().toISOString(),
    body_text: 'Направляем договор № 45/2025. Сумма 1 250 000 руб. Телефон +7 (495) 123-45-67.',
  };

  const analysis = await analyzeEmail(email, { llm, model: cfg.llm.model });

  assert.equal(analysis.engine, 'llm');
  assert.equal(analysis.counterparty, 'ООО «Ромашка»');
  assert.equal(analysis.contract_number, '45/2025');
  assert.equal(analysis.status, 'waiting');
  assert.deepEqual(analysis.risks, ['неустойка 0,1% в день']);

  // Rule-extracted phone survives even though the LLM left it null.
  assert.ok(analysis.phones.includes('+74951234567'));

  const request = fake.state.requests[0];
  assert.equal(request.body.model, 'fake-model');
  assert.equal(request.body.response_format.type, 'json_object');
  assert.equal(request.body.temperature, 0);
});

test('LLM failure falls back to rules instead of crashing', async (t) => {
  const server = http.createServer((req, res) => {
    res.writeHead(500, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ error: { message: 'boom' } }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => server.close());
  const { port } = server.address();

  const cfg = cfgWith(t, `http://127.0.0.1:${port}/v1`);
  const llm = createLlm(cfg);
  const analysis = await analyzeEmail(
    {
      id: 2,
      from_name: 'X',
      from_addr: 'x@example.com',
      subject: 'Договор',
      sent_at: new Date().toISOString(),
      body_text: 'Договор № 7 на 50 000 руб.',
    },
    { llm },
  );

  assert.equal(analysis.engine, 'rules');
  assert.match(analysis.summary ?? '', /Договор/i);
});

test('questions are answered through the LLM when available', async (t) => {
  const fake = await fakeLlm(t);
  const cfg = cfgWith(t, fake.url);
  const llm = createLlm(cfg);
  const store = tempDb(t);

  const { id } = store.insertEmail({
    account: 'me@mail.ru',
    folder: 'INBOX',
    uid: 1,
    from_name: 'Иван Петров',
    from_addr: 'ivan@romashka.ru',
    subject: 'Договор поставки № 45/2025',
    sent_at: new Date().toISOString(),
    body_text: 'Направляем договор поставки № 45/2025 на 1 250 000 руб. Ждём подписания.',
    is_bulk: 0,
  });

  const res = await answerQuestion({ store, cfg, llm, question: 'Что по договору с Ромашкой?' });

  assert.equal(res.usedLlm, true);
  assert.match(res.text, /45\/2025/);
  assert.match(res.text, /Источники/);
  assert.ok(res.emailIds.includes(id));

  const qaRequest = fake.state.requests.at(-1);
  assert.equal(qaRequest.body.response_format, undefined, 'QA call must not force JSON mode');
  assert.match(qaRequest.body.messages[1].content, /Фрагменты писем/);
});
