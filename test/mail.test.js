import test from 'node:test';
import assert from 'node:assert/strict';
import { decodeEntities, explainMailError, htmlToText } from '../src/mail.js';

test('htmlToText strips markup, scripts and keeps line breaks', () => {
  const html = `<div>Добрый день!</div><p>Договор № 45/2025</p><br>Сумма&nbsp;1&nbsp;250&nbsp;000 руб.<script>alert(1)</script>`;
  const text = htmlToText(html);
  assert.match(text, /Добрый день!/);
  assert.match(text, /Договор № 45\/2025/);
  assert.match(text, /Сумма 1 250 000 руб\./);
  assert.doesNotMatch(text, /alert/);
  assert.doesNotMatch(text, /<div>/);
});

test('decodeEntities handles Russian typography and numeric codes', () => {
  assert.equal(decodeEntities('&laquo;Ромашка&raquo; &mdash; &amp;'), '«Ромашка» — &');
  assert.equal(decodeEntities('&#1055;&#1088;&#1080;&#1074;&#1077;&#1090;'), 'Привет');
  assert.equal(decodeEntities('&#x41F;'), 'П');
});

test('mail errors are explained in human terms', () => {
  assert.match(explainMailError(new Error('AUTHENTICATIONFAILED')), /ПАРОЛЬ ДЛЯ ВНЕШНИХ ПРИЛОЖЕНИЙ/);
  assert.match(explainMailError(new Error('getaddrinfo ENOTFOUND imap.mail.ru')), /интернет/i);
  assert.match(explainMailError(new Error('ETIMEDOUT')), /не ответил/i);
  assert.match(explainMailError(new Error('что-то странное')), /что-то странное/);
});
