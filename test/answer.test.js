import test from 'node:test';
import assert from 'node:assert/strict';
import {
  answerContacts,
  answerContracts,
  answerQuestion,
  answerTasks,
  buildDigest,
  detectIntent,
  findMatchingContacts,
  stemRu,
} from '../src/answer.js';
import { tempDb, seedStore } from './helpers.js';

async function prepared(t) {
  const store = tempDb(t);
  const ids = await seedStore(store);
  return { store, ids };
}

test('stemRu matches Russian inflections', () => {
  assert.equal(stemRu('Смирновой'), stemRu('Смирнова'));
  assert.equal(stemRu('Анны'), stemRu('Анна'));
  assert.equal(stemRu('договором'), stemRu('договор'));
});

test('intent detection', () => {
  assert.equal(detectIntent('Какой телефон у Анны?'), 'contacts');
  assert.equal(detectIntent('Что с договором поставки?'), 'contracts');
  assert.equal(detectIntent('Что висит на неделе?'), 'tasks');
  assert.equal(detectIntent('О чём договорились?'), 'negotiations');
  assert.equal(detectIntent('Привет'), 'general');
});

test('contacts are found by an inflected name and group phones', async (t) => {
  const { store } = await prepared(t);
  const res = answerContacts({ store, question: 'Какой телефон у Анны Смирновой?' });
  assert.match(res.text, /Анна Смирнова/);
  assert.match(res.text, /\+7 \(916\) 555-77-88/);
  assert.match(res.text, /\+7 \(495\) 123-45-67/);
  assert.match(res.text, /a\.smirnova@romashka\.ru/);
  assert.match(res.text, /ул\. Ленина, д\. 10/);
});

test('contacts are found by phone number', async (t) => {
  const { store } = await prepared(t);
  const res = answerContacts({ store, question: 'Кто это +7 916 555 77 88?' });
  assert.match(res.text, /Анна Смирнова/);
});

test('address query returns only contacts with an address', async (t) => {
  const { store } = await prepared(t);
  const res = answerContacts({ store, question: 'Какие адреса есть в письмах?' });
  assert.match(res.text, /ул\. Ленина/);
  assert.doesNotMatch(res.text, /Бухгалтерия/);
});

test('contracts can be filtered by counterparty stem', async (t) => {
  const { store } = await prepared(t);
  const all = answerContracts({ store });
  assert.match(all.text, /45\/2025/);
  const filtered = answerContracts({ store, query: 'Что по договору с Ромашкой?' });
  assert.match(filtered.text, /ООО «Ромашка»/);
  assert.doesNotMatch(filtered.text, /Вектор/);
});

test('fuzzy contact search scores matches, ignores question words', async (t) => {
  const { store } = await prepared(t);
  const rows = findMatchingContacts(store, 'дай телефон Анны Смирновой');
  assert.ok(rows.length >= 1);
  assert.equal(rows[0].name, 'Анна Смирнова');
});

test('digest mentions contracts and risks', async (t) => {
  const { store } = await prepared(t);
  const digest = buildDigest(store, { days: 7 });
  assert.match(digest.text, /Сводка/);
  assert.match(digest.text, /45\/2025/);
  assert.match(digest.text, /риск/i);
});

test('tasks list asks from mail', async (t) => {
  const { store } = await prepared(t);
  const res = answerTasks({ store });
  assert.match(res.text, /подписать|Просим/i);
});

test('questions without an LLM answer from the template', async (t) => {
  const { store } = await prepared(t);
  const res = await answerQuestion({ store, cfg: { has: { llm: false } }, llm: null, question: 'Что нового?' });
  assert.equal(res.usedLlm, false);
  assert.ok(res.text.length > 0);
  assert.ok(res.emailIds.length > 0);
});

test('user text is HTML-escaped in answers', async (t) => {
  const store = tempDb(t);
  await seedStore(store, [
    {
      from_name: 'Хакер',
      from_addr: 'evil@example.com',
      subject: '<script>alert(1)</script> договор',
      body: 'Договор поставки № 1 на 100 000 руб.',
    },
  ]);
  const res = await answerQuestion({ store, cfg: { has: { llm: false } }, llm: null, question: 'договор' });
  assert.doesNotMatch(res.text, /<script>/);
  assert.match(res.text, /&lt;script&gt;/);
});
