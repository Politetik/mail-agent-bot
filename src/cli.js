import path from 'node:path';
import { answerContacts, answerContracts, answerQuestion, answerTasks, buildDigest } from './answer.js';
import { createConfig, describeConfig } from './config.js';
import { runDemo } from './demo.js';
import { createLlm } from './llm.js';
import { testMailConnection } from './mail.js';
import { openStore } from './store.js';
import { runSync } from './sync.js';
import { setLogLevel, formatDate, logger } from './util.js';

function plain(html) {
  return String(html ?? '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&amp;', '&')
    .trim();
}

function usage() {
  console.log(`Почтовый ИИ-агент — консоль

  node src/cli.js doctor              проверить настройки и подключения
  node src/cli.js sync [--days N]     прочитать почту и разобрать письма
  node src/cli.js ask "вопрос"        задать вопрос по базе
  node src/cli.js digest [дней]       сводка
  node src/cli.js contacts [запрос]   контакты с телефонами и адресами
  node src/cli.js contracts           реестр договоров
  node src/cli.js tasks               открытые задачи
  node src/cli.js stats               статистика базы
  node src/cli.js demo                демо на тестовых письмах (без ключей)
  node src/cli.js reset               очистить базу

Настройки берутся из .env в корне проекта.`);
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const key = a.slice(2);
      const next = argv[i + 1];
      if (next && !next.startsWith('--')) {
        out[key] = next;
        i += 1;
      } else {
        out[key] = true;
      }
    } else {
      out._.push(a);
    }
  }
  return out;
}

async function doctor({ cfg, store, llm }) {
  const lines = ['🩺 Проверка окружения', ''];
  const info = describeConfig(cfg);
  lines.push(`Node.js: ${process.version}`);
  lines.push(`База: ${store.path}`);
  lines.push(`Почта: ${info.mail}`);
  lines.push(`ИИ: ${info.llm}`);
  lines.push(`Голос: ${info.stt}`);
  lines.push(`Telegram: ${info.telegram}`);
  lines.push('');

  let failures = 0;

  if (cfg.has.mail) {
    try {
      const res = await testMailConnection(cfg);
      lines.push(`✅ IMAP: подключение за ${res.ms} мс, папок ${res.mailboxes}`);
    } catch (err) {
      failures += 1;
      lines.push(`❌ IMAP: ${err.message}`);
    }
  } else {
    lines.push('⚠️ IMAP: не настроен (MAIL_USER / MAIL_PASSWORD)');
  }

  if (cfg.has.llm) {
    try {
      const reply = await llm.ping();
      lines.push(`✅ LLM: ответ «${String(reply).slice(0, 40)}» (${cfg.llm.model})`);
    } catch (err) {
      failures += 1;
      lines.push(`❌ LLM: ${err.message}`);
    }
  } else {
    lines.push('⚠️ LLM: ключ не задан — разбор будет по правилам');
  }

  if (cfg.has.telegram) {
    try {
      const me = await fetch(`${cfg.telegram.apiBase}/bot${cfg.telegram.token}/getMe`).then((r) => r.json());
      if (me.ok) lines.push(`✅ Telegram: @${me.result.username}`);
      else {
        failures += 1;
        lines.push(`❌ Telegram: ${me.description}`);
      }
    } catch (err) {
      failures += 1;
      lines.push(`❌ Telegram: ${err.message}`);
    }
    lines.push(
      cfg.telegram.allowedChatIds.length
        ? `✅ Доступ: ${cfg.telegram.allowedChatIds.join(', ')}`
        : '⚠️ ALLOWED_CHAT_IDS пуст — бот всем покажет ваш chat_id и не будет работать',
    );
  } else {
    lines.push('⚠️ Telegram: токен не задан');
  }

  const stats = store.stats();
  lines.push('', `📊 База: писем ${stats.emails}, разобрано ${stats.analyzed}, контактов ${stats.contacts}, договоров ${stats.contracts}`);

  console.log(lines.join('\n'));
  return failures;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0] ?? 'help';

  const overrides = {};
  if (cmd === 'demo') overrides.DB_PATH = 'data/demo.db';
  if (args.days) overrides.SYNC_DAYS = String(args.days);
  if (args.limit) overrides.SYNC_LIMIT = String(args.limit);

  const cfg = createConfig({ overrides });
  setLogLevel(args.verbose ? 'debug' : cfg.logLevel);

  if (cmd === 'help' || cmd === '--help' || cmd === '-h') {
    usage();
    return 0;
  }

  const store = openStore(cfg.db.path);
  const llm = createLlm(cfg);

  try {
    switch (cmd) {
      case 'doctor':
        return (await doctor({ cfg, store, llm })) > 0 ? 1 : 0;

      case 'sync': {
        if (!cfg.has.mail) {
          console.error('❌ Почта не настроена: заполните MAIL_USER и MAIL_PASSWORD в .env');
          return 1;
        }
        const res = await runSync({
          cfg,
          store,
          llm,
          onProgress: (p) => {
            if (p.phase === 'fetch') process.stderr.write(`\r📥 ${p.folder ?? ''} ${p.done}/${p.total}   `);
            else process.stderr.write(`\r🧠 анализ ${p.done}/${p.total}   `);
          },
        });
        process.stderr.write('\n');
        console.log(
          `✅ Новых: ${res.fetched}, дублей: ${res.duplicates}, разобрано: ${res.analyzed}, ошибок: ${res.failed + res.errors.length}`,
        );
        for (const e of res.errors) console.log(`   ⚠️ ${e}`);
        store.metaSet('last_sync_at', res.finished);
        return 0;
      }

      case 'ask': {
        const question = args._.slice(1).join(' ');
        if (!question) {
          console.error('Укажите вопрос: node src/cli.js ask "какой телефон у Анны?"');
          return 1;
        }
        const res = await answerQuestion({ store, cfg, llm, question });
        console.log(plain(res.text));
        return 0;
      }

      case 'digest': {
        const days = Number(args._[1] ?? args.days ?? 7) || 7;
        console.log(plain(buildDigest(store, { days }).text));
        return 0;
      }

      case 'contacts': {
        const q = args._.slice(1).join(' ');
        console.log(plain(answerContacts({ store, question: q }).text));
        return 0;
      }

      case 'contracts':
        console.log(plain(answerContracts({ store }).text));
        return 0;

      case 'tasks':
        console.log(plain(answerTasks({ store }).text));
        return 0;

      case 'stats': {
        const s = store.stats();
        console.log(JSON.stringify(s, null, 2));
        return 0;
      }

      case 'demo':
        await runDemo({ cfg, store });
        return 0;

      case 'reset': {
        store.reset();
        console.log('🗑 База очищена.');
        return 0;
      }

      default:
        console.error(`Неизвестная команда: ${cmd}`);
        usage();
        return 1;
    }
  } finally {
    store.close();
  }
}

main()
  .then((code) => process.exit(code ?? 0))
  .catch((err) => {
    logger.error(err.stack ?? err.message);
    process.exit(1);
  });
