import { analyzeEmail } from './analyze.js';
import { answerQuestion, buildDigest } from './answer.js';
import { persistDerived } from './sync.js';
import { logger, nowIso } from './util.js';

const daysAgo = (n) => new Date(Date.now() - n * 86400000).toISOString();

const SAMPLE_MAIL = [
  {
    days: 0,
    from_name: 'Иван Петров',
    from_addr: 'ivan.petrov@romashka.ru',
    subject: 'Договор поставки № 45/2025 — просим подписать',
    body: `Добрый день!

Направляем на подписание договор поставки № 45/2025 от 03.11.2025.

Предмет: поставка офисной мебели в количестве 120 единиц.
Сумма договора: 1 250 000 руб., в том числе НДС 20%.
Условия оплаты: предоплата 50%, остаток — в течение 5 банковских дней после поставки.
Срок поставки: до 15.12.2025.
За просрочку поставки предусмотрена неустойка 0,1% от суммы договора за каждый день.

Просим подписать и вернуть скан до 20.11.2025.

С уважением,
Иван Петров
Менеджер по работе с клиентами
ООО «Ромашка»
Тел.: +7 (495) 123-45-67, доб. 214`,
  },
  {
    days: 1,
    from_name: 'Алексей Смирнов',
    from_addr: 'a.smirnov@mycompany.ru',
    subject: 'Re: Договор поставки № 45/2025',
    body: `Иван, добрый день!

Посмотрели договор № 45/2025. В целом условия подходят, но просим:
1) снизить предоплату с 50% до 30%;
2) исключить неустойку за просрочку, не зависящую от нас.

Готовы подписать до 25.11.2025 и оплатить первый транш.

С уважением,
Алексей Смирнов
Финансовый директор`,
  },
  {
    days: 2,
    from_name: 'Мария Ковалёва',
    from_addr: 'm.kovaleva@romashka.ru',
    subject: 'Контакты менеджера по поставкам',
    body: `Коллеги, добрый день!

Передаю контакты нашего менеджера по поставкам:

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (495) 123-45-67
Мобильный: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru
Адрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25
ИНН: 7701234567

По вопросам договора № 45/2025 обращайтесь к ней.`,
  },
  {
    days: 3,
    from_name: 'Бухгалтерия Ромашка',
    from_addr: 'buh@romashka.ru',
    subject: 'Счёт на оплату № 128 от 01.11.2025',
    body: `Добрый день!

Направляем счёт на оплату № 128 от 01.11.2025 на сумму 625 000 руб.
(предоплата 50% по договору поставки № 45/2025).

Просим оплатить до 10.11.2025.

Реквизиты: ООО «Ромашка», ИНН 7701234567, КПП 770101001.`,
  },
  {
    days: 5,
    from_name: 'Мария Ковалёва',
    from_addr: 'm.kovaleva@vector.ru',
    subject: 'Переговоры по аренде офиса — наше предложение',
    body: `Здравствуйте!

По переговорам об аренде офиса: готовы предложить помещение 180 кв.м по адресу
г. Москва, ул. Тверская, д. 18, корп. 2.

Ставка — 250 000 руб. в месяц, срок 11 месяцев, договор № А-12/2025.
Обеспечительный платёж — один месяц.

Ждём вашего решения до 01.12.2025.

С уважением,
Мария Ковалёва
Директор по аренде
ООО «Вектор»
+7 (499) 765-43-21`,
  },
  {
    days: 6,
    from_name: 'Дмитрий Орлов',
    from_addr: 'd.orlov@logistika.ru',
    subject: 'Адрес склада для самовывоза',
    body: `Добрый день!

Сообщаю адрес склада для самовывоза:
г. Химки, ул. Победы, д. 5, стр. 2, склад № 7.
Режим работы: пн–пт с 9:00 до 18:00.

Телефон склада: 8 (800) 555-35-35.

С уважением,
Дмитрий Орлов
Логистика`,
  },
  {
    days: 1,
    from_name: 'Магазин мебели',
    from_addr: 'no-reply@shop.example',
    subject: 'Скидки недели: −30% на всё',
    body: 'Рассылка. Скидки недели, промокод SALE30. Отписаться можно по ссылке.',
    is_bulk: 1,
  },
];

function plain(html) {
  return String(html ?? '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&amp;', '&')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * Seed a demo mailbox into the current store, analyse it with rules,
 * and answer a few sample questions. Works with no API keys at all.
 */
export async function runDemo({ cfg, store }) {
  const out = [];
  const print = (s = '') => {
    out.push(s);
    console.log(s);
  };

  store.reset();
  print('📦 Демо-режим: наполняю тестовый ящик (ключи и настройки не нужны)');
  print('');

  let inserted = 0;
  for (const [i, sample] of SAMPLE_MAIL.entries()) {
    const sent = daysAgo(sample.days);
    const { id, inserted: isNew } = store.insertEmail({
      account: 'demo@mail.ru',
      folder: sample.is_bulk ? 'INBOX' : 'INBOX',
      uid: 1000 + i,
      message_id: `<demo-${i}@mail.ru>`,
      thread_key: sample.subject.toLowerCase(),
      from_name: sample.from_name,
      from_addr: sample.from_addr,
      to_addrs: 'demo@mail.ru',
      subject: sample.subject,
      sent_at: sent,
      body_text: sample.body,
      snippet: sample.body.replace(/\s+/g, ' ').slice(0, 200),
      is_bulk: sample.is_bulk ?? 0,
      fetched_at: nowIso(),
    });
    if (isNew) inserted += 1;
    if (sample.is_bulk) continue;

    const analysis = await analyzeEmail(
      { ...sample, id, body_text: sample.body, sent_at: sent, to_addrs: 'demo@mail.ru' },
      { llm: null },
    );
    store.saveAnalysis(id, analysis);
    persistDerived(store, { ...sample, id, body_text: sample.body, sent_at: sent }, analysis);
  }

  print(`✅ Загружено писем: ${inserted}, проанализировано правилами`);

  const stats = store.stats();
  print('');
  print('📊 База:');
  print(`   писем: ${stats.emails}, разобрано: ${stats.analyzed}`);
  print(`   контактов: ${stats.contacts}, договоров: ${stats.contracts}, задач: ${stats.openTasks}`);
  print(`   высокий риск: ${stats.highRisk}`);
  print('');

  const digest = buildDigest(store, { days: 7 });
  print(plain(digest.text));
  print('');

  const questions = [
    'Какой телефон у Анны Смирновой?',
    'Что по договору с Ромашкой?',
    'Какие адреса есть в письмах?',
    'Что висит на этой неделе?',
  ];

  for (const q of questions) {
    print(`❓ ${q}`);
    const res = await answerQuestion({ store, cfg, llm: null, question: q });
    print(plain(res.text));
    print('');
  }

  print('Готово. Запустите бота: npm start (после настройки .env)');
  logger.info('Демо завершено');
  return out.join('\n');
}
