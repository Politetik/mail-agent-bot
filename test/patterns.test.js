import test from 'node:test';
import assert from 'node:assert/strict';
import {
  extractAddresses,
  extractAmounts,
  extractCompanies,
  extractContactPerson,
  extractContractNumbers,
  extractDates,
  extractDeadline,
  extractPhones,
  extractSignatureName,
  formatPhone,
  guessKind,
  guessRiskLevel,
  guessStatus,
  normalizePhone,
  detectBulk,
  extractInn,
} from '../src/patterns.js';
import { buildFtsQuery } from '../src/store.js';

test('phones: Russian formats are found and normalised', () => {
  const text = 'Звоните: +7 (495) 123-45-67, моб. 8 916 555-77-88, а также 8-800-555-35-35.';
  const phones = extractPhones(text);
  assert.deepEqual(phones, ['+74951234567', '+79165557788', '+78005553535']);
  assert.equal(formatPhone('+74951234567'), '+7 (495) 123-45-67');
});

test('phones: dates and INN are not mistaken for phones', () => {
  assert.deepEqual(extractPhones('Договор от 03.11.2025, ИНН 7701234567'), []);
  assert.equal(normalizePhone('12345'), null);
});

test('addresses: index, street and office are kept on one line', () => {
  const body = `Реквизиты:
Адрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25
ИНН: 7701234567`;
  const addresses = extractAddresses(body);
  assert.equal(addresses.length, 1);
  assert.match(addresses[0], /125009/);
  assert.match(addresses[0], /ул\. Ленина, д\. 10/);
});

test('addresses: wrapped lines are merged, bare city is rejected', () => {
  const body = 'г. Москва, ул. Тверская,\nд. 18, корп. 2\n\nг. Москва\n';
  const addresses = extractAddresses(body);
  assert.equal(addresses.length, 1);
  assert.match(addresses[0], /Тверская, д\. 18/);
});

test('companies, INN, contract numbers and amounts', () => {
  const text = 'ООО «Ромашка», ИНН 7701234567, КПП 770101001. Договор поставки № 45/2025. Сумма 1 250 000 руб.';
  assert.deepEqual(extractCompanies(text), ['ООО «Ромашка»']);
  assert.ok(extractInn(text).includes('7701234567'));
  assert.ok(extractInn(text).includes('КПП 770101001'));
  assert.deepEqual(extractContractNumbers(text), ['45/2025']);
  assert.equal(extractAmounts(text)[0].amount, 1250000);
  assert.equal(extractAmounts('скидка 1,25 млн руб.')[0].amount, 1250000);
});

test('dates and deadline: no fallback to the letter date', () => {
  assert.deepEqual(extractDates('до 15.12.2025 или 2026-01-31, а также 5 марта 2024'), [
    '2025-12-15',
    '2026-01-31',
    '2024-03-05',
  ]);
  assert.equal(extractDeadline('Срок поставки: до 15.12.2025.'), '2025-12-15');
  assert.equal(extractDeadline('Никаких сроков нет'), null);
});

test('contact person is taken from the block, not the sender', () => {
  const body = `Передаю контакты.

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru`;
  const person = extractContactPerson(body);
  assert.equal(person.name, 'Анна Смирнова');
  assert.equal(person.position, 'руководитель отдела поставок');
});

test('signature name does not capture the next line', () => {
  const body = 'С уважением,\nДмитрий Орлов\nЛогистика';
  assert.equal(extractSignatureName(body), 'Дмитрий Орлов');
});

test('kind, status and risk heuristics', () => {
  assert.equal(guessKind('Счёт на оплату № 12', 'просим оплатить'), 'invoice');
  assert.equal(guessKind('Контакты', 'передаю контакты менеджера'), 'contact');
  assert.equal(guessKind('Договор № 1', 'направляем договор'), 'contract');
  assert.equal(guessKind('Переговоры', 'обсудим условия'), 'negotiation');

  assert.equal(guessStatus('Договор подписан, оплатили'), 'agreed');
  assert.equal(guessStatus('Мы не согласны с условиями'), 'rejected');
  assert.equal(guessStatus('Ждём вашего ответа'), 'waiting');

  assert.equal(guessRiskLevel('штраф и неустойка за просрочку', 'contract'), 'high');
  assert.equal(guessRiskLevel('обычное письмо', 'other'), 'low');
});

test('bulk detection by headers, sender and subject', () => {
  const headers = new Map([['list-unsubscribe', '<mailto:unsub@x>']]);
  assert.equal(detectBulk(headers, 'news@shop.ru', 'Скидки'), true);
  assert.equal(detectBulk(new Map(), 'no-reply@shop.ru', 'Заказ'), true);
  assert.equal(detectBulk(new Map(), 'ivan@romashka.ru', 'Договор'), false);
});

test('FTS query builder survives punctuation', () => {
  assert.equal(buildFtsQuery('Что по договору № 45/2025?'), '"договору"* OR "45"* OR "2025"*');
});
