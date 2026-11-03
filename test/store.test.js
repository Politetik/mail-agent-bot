import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { openStore, buildFtsQuery, tokenize } from '../src/store.js';

function tempStore(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'mailbot-store-'));
  const store = openStore(path.join(dir, 'test.db'));
  t.after(() => {
    store.close();
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return store;
}

function sampleEmail(overrides = {}) {
  return {
    account: 'test@mail.ru',
    folder: 'INBOX',
    uid: 1,
    message_id: '<1@mail.ru>',
    from_name: 'Иван Петров',
    from_addr: 'ivan@romashka.ru',
    to_addrs: 'test@mail.ru',
    subject: 'Договор поставки № 45/2025',
    sent_at: new Date().toISOString(),
    body_text: 'Направляем договор поставки № 45/2025. Сумма 1 250 000 руб. Оплата до 15.12.2025. Телефон +7 (495) 123-45-67.',
    snippet: 'Направляем договор поставки',
    is_bulk: 0,
    ...overrides,
  };
}

test('tokenize drops stopwords and short tokens', () => {
  assert.deepEqual(tokenize('Где договор по поставке?'), ['где', 'договор', 'поставке'].filter((t) => t !== 'где' && t !== 'по'));
  assert.deepEqual(tokenize('a b cd ef'), ['cd', 'ef']);
});

test('buildFtsQuery produces prefix OR query', () => {
  assert.equal(buildFtsQuery('договор поставки'), '"договор"* OR "поставки"*');
  assert.equal(buildFtsQuery('!!!'), null);
  assert.equal(buildFtsQuery(''), null);
});

test('insert, dedupe and search emails with Cyrillic FTS', (t) => {
  const store = tempStore(t);
  const first = store.insertEmail(sampleEmail());
  assert.ok(first.id > 0);
  assert.equal(first.inserted, true);

  const again = store.insertEmail(sampleEmail());
  assert.equal(again.inserted, false);
  assert.equal(again.id, first.id);

  const hits = store.searchEmails('договор поставки');
  assert.equal(hits.length, 1);
  assert.equal(hits[0].subject, 'Договор поставки № 45/2025');

  const byPhone = store.searchEmails('1250000');
  assert.equal(byPhone.length, 0, 'digits inside words are not FTS tokens');

  const byText = store.searchEmails('оплата');
  assert.equal(byText.length, 1);

  assert.equal(store.searchEmails('несуществующийзапрос').length, 0);
});

test('analysis, contacts, contracts, tasks and stats', (t) => {
  const store = tempStore(t);
  const { id } = store.insertEmail(sampleEmail());

  store.saveAnalysis(id, {
    kind: 'contract',
    summary: 'Договор поставки на 1,25 млн',
    counterparty: 'ООО «Ромашка»',
    contract_number: '45/2025',
    amount: 1250000,
    currency: 'RUB',
    deadline: '2025-12-15',
    status: 'waiting',
    risk_level: 'high',
    risks: ['штраф за просрочку'],
    asks: ['подпишите договор'],
    contacts: [{ name: 'Иван Петров', phone: '+74951234567' }],
    phones: ['+74951234567'],
    addresses: ['г. Москва, ул. Ленина, д. 10'],
    confidence: 0.8,
    engine: 'rules',
  });

  store.saveAnalysis(id, { kind: 'contract', summary: 'Обновлено', risk_level: 'medium' });
  const analysis = store.getAnalysis(id);
  assert.equal(analysis.summary, 'Обновлено');
  assert.equal(analysis.risk_level, 'medium');

  const email = store.getEmail(id);
  assert.deepEqual(email.phones, ['+74951234567']);
  assert.equal(email.status, 'waiting', 'partial save keeps fields from the earlier analysis');
  assert.equal(email.kind, 'contract');

  assert.equal(
    store.upsertContact({
      email_id: id,
      name: 'Иван Петров',
      phone: '+74951234567',
      email: 'ivan@romashka.ru',
      company: 'ООО «Ромашка»',
    }),
    true,
  );
  assert.equal(
    store.upsertContact({ email_id: id, name: 'Иван Петров', phone: '+74951234567', email: 'ivan@romashka.ru' }),
    false,
    'duplicate contact is ignored',
  );

  store.upsertContract({ email_id: id, number: '45/2025', counterparty: 'ООО «Ромашка»', amount: 1250000 });
  store.addTask({ email_id: id, text: 'Подписать договор', due: '2025-12-15' });

  const stats = store.stats();
  assert.equal(stats.emails, 1);
  assert.equal(stats.analyzed, 1);
  assert.equal(stats.contacts, 1);
  assert.equal(stats.contracts, 1);
  assert.equal(stats.openTasks, 1);

  assert.equal(store.contacts({ query: 'ромашка' }).length, 1);
  assert.equal(store.contacts({ query: '+7495' }).length, 1);
  assert.equal(store.tasks({}).length, 1);
});

test('sync state round-trip and reset', (t) => {
  const store = tempStore(t);
  store.setSyncState('test@mail.ru', 'INBOX', { uidvalidity: '123', lastUid: 42 });
  const state = store.getSyncState('test@mail.ru', 'INBOX');
  assert.equal(state.last_uid, 42);

  store.insertEmail(sampleEmail());
  store.reset();
  assert.equal(store.stats().emails, 0);
  assert.equal(store.searchEmails('договор').length, 0);
});

test('bulk emails are excluded from recent lists', (t) => {
  const store = tempStore(t);
  store.insertEmail(sampleEmail({ uid: 1, subject: 'Рассылка', is_bulk: 1 }));
  store.insertEmail(sampleEmail({ uid: 2, subject: 'Рабочее письмо', is_bulk: 0 }));
  const recent = store.recentEmails({ limit: 10, sinceDays: 1 });
  assert.deepEqual(recent.map((r) => r.subject), ['Рабочее письмо']);
});
