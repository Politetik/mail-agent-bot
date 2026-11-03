import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { analyzeEmail } from '../src/analyze.js';
import { openStore } from '../src/store.js';
import { persistDerived } from '../src/sync.js';
import { nowIso } from '../src/util.js';

export function tempDb(t, prefix = 'mailbot-') {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix));
  const store = openStore(path.join(dir, 'test.db'));
  t.after(() => {
    try {
      store.close();
    } catch {
      /* ignore */
    }
    fs.rmSync(dir, { recursive: true, force: true });
  });
  return store;
}

export const SAMPLE_EMAILS = [
  {
    from_name: 'Иван Петров',
    from_addr: 'ivan@romashka.ru',
    subject: 'Договор поставки № 45/2025',
    body: `Направляем договор поставки № 45/2025 от 03.11.2025.
Сумма договора: 1 250 000 руб. Предоплата 50%.
Срок поставки: до 15.12.2025.
За просрочку предусмотрена неустойка 0,1% в день.

Просим подписать и вернуть скан до 20.11.2025.

С уважением,
Иван Петров
Менеджер по работе с клиентами
ООО «Ромашка»
Тел.: +7 (495) 123-45-67`,
  },
  {
    from_name: 'Мария Ковалёва',
    from_addr: 'm.kovaleva@romashka.ru',
    subject: 'Контакты менеджера по поставкам',
    body: `Передаю контакты нашего менеджера.

Анна Смирнова, руководитель отдела поставок
ООО «Ромашка»
Телефон: +7 (495) 123-45-67
Мобильный: +7 (916) 555-77-88
E-mail: a.smirnova@romashka.ru
Адрес: 125009, г. Москва, ул. Ленина, д. 10, офис 25
ИНН: 7701234567`,
  },
];

/** Insert e-mails, analyse them with rules only, persist derived rows. */
export async function seedStore(store, emails = SAMPLE_EMAILS, startUid = 100) {
  const ids = [];
  for (const [i, sample] of emails.entries()) {
    const sent = new Date(Date.now() - (emails.length - i) * 86400000).toISOString();
    const row = {
      account: 'test@mail.ru',
      folder: 'INBOX',
      uid: startUid + i,
      message_id: `<${startUid + i}@mail.ru>`,
      from_name: sample.from_name,
      from_addr: sample.from_addr,
      to_addrs: 'test@mail.ru',
      subject: sample.subject,
      sent_at: sent,
      body_text: sample.body,
      snippet: sample.body.slice(0, 120),
      is_bulk: 0,
      fetched_at: nowIso(),
    };
    const { id } = store.insertEmail(row);
    const email = { ...row, id };
    const analysis = await analyzeEmail(email, { llm: null });
    store.saveAnalysis(id, analysis);
    persistDerived(store, email, analysis);
    ids.push(id);
  }
  return ids;
}
