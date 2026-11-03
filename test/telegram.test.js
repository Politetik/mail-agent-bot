import test from 'node:test';
import assert from 'node:assert/strict';
import { TelegramApi, TelegramError } from '../src/telegram.js';

function jsonResponse(payload, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'content-type': 'application/json' },
  });
}

function stubFetch(t) {
  const calls = [];
  const original = globalThis.fetch;
  const handler = { fn: null };
  globalThis.fetch = async (url, opts = {}) => {
    const body = opts.body ? JSON.parse(opts.body) : null;
    calls.push({ url: String(url), body });
    return handler.fn(String(url), body, calls.length);
  };
  t.after(() => {
    globalThis.fetch = original;
  });
  return { calls, handler };
}

test('sendMessage splits long text into several messages', async (t) => {
  const { calls, handler } = stubFetch(t);
  handler.fn = async () => jsonResponse({ ok: true, result: { message_id: 1 } });

  const api = new TelegramApi('123:TEST');
  const long = `${'абв'.repeat(1500)}\n\n${'где'.repeat(1200)}`;
  await api.sendMessage(42, long);

  assert.ok(calls.length >= 2, `ожидалось ≥2 сообщения, получено ${calls.length}`);
  for (const call of calls) {
    assert.equal(call.url, 'https://api.telegram.org/bot123:TEST/sendMessage');
    assert.equal(call.body.chat_id, 42);
    assert.equal(call.body.parse_mode, 'HTML');
    assert.ok(call.body.text.length <= 3800);
  }
});

test('429 is retried after the indicated delay', async (t) => {
  const { calls, handler } = stubFetch(t);
  handler.fn = async (_url, _body, n) =>
    n === 1
      ? jsonResponse({ ok: false, error_code: 429, description: 'Too Many Requests', parameters: { retry_after: 0 } }, 429)
      : jsonResponse({ ok: true, result: { message_id: 7 } });

  const api = new TelegramApi('123:TEST');
  const res = await api.sendMessage(1, 'привет');
  assert.equal(calls.length, 2);
  assert.equal(res.message_id, 7);
});

test('API errors surface with code and description', async (t) => {
  const { handler } = stubFetch(t);
  handler.fn = async () => jsonResponse({ ok: false, error_code: 403, description: 'Forbidden: bot was blocked' }, 403);

  const api = new TelegramApi('123:TEST');
  await assert.rejects(() => api.sendMessage(1, 'привет'), (err) => {
    assert.ok(err instanceof TelegramError);
    assert.equal(err.code, 403);
    assert.match(err.message, /blocked/);
    return true;
  });
});

test('getUpdates sends offset, timeout and allowed_updates', async (t) => {
  const { calls, handler } = stubFetch(t);
  handler.fn = async () => jsonResponse({ ok: true, result: [] });

  const api = new TelegramApi('123:TEST');
  await api.getUpdates(15, 5);

  assert.equal(calls[0].body.offset, 15);
  assert.equal(calls[0].body.timeout, 5);
  assert.deepEqual(calls[0].body.allowed_updates, ['message', 'edited_message']);
});

test('downloadFile uses the file endpoint', async (t) => {
  const { calls, handler } = stubFetch(t);
  const original = globalThis.fetch;
  handler.fn = async () => new Response(Buffer.from('audio-bytes'), { status: 200 });
  t.after(() => {
    globalThis.fetch = original;
  });

  const api = new TelegramApi('123:TEST');
  const buf = await api.downloadFile('voice/file_1.oga');
  assert.equal(calls[0].url, 'https://api.telegram.org/file/bot123:TEST/voice/file_1.oga');
  assert.equal(buf.toString(), 'audio-bytes');
});

test('missing token fails fast', () => {
  assert.throws(() => new TelegramApi(''), /TELEGRAM_BOT_TOKEN/);
});
