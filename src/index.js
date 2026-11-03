import { createBot } from './bot.js';
import { createConfig, describeConfig } from './config.js';
import { createLlm } from './llm.js';
import { openStore } from './store.js';
import { runSync } from './sync.js';
import { logger, setLogLevel, sleep } from './util.js';

const BANNER = `
  ┌─────────────────────────────────────────────┐
  │   Почтовый ИИ-агент для Telegram и Mail.ru  │
  └─────────────────────────────────────────────┘
`;

async function main() {
  const cfg = createConfig();
  setLogLevel(cfg.logLevel);

  console.log(BANNER);

  if (!cfg.has.telegram) {
    console.error('❌ Не задан TELEGRAM_BOT_TOKEN.');
    console.error('');
    console.error('1. Откройте @BotFather в Telegram → /newbot');
    console.error('2. Скопируйте токен в файл .env:');
    console.error('   TELEGRAM_BOT_TOKEN=123456789:AA...');
    console.error('3. Запустите снова: npm start');
    console.error('');
    console.error('Подсказка: скопируйте .env.example в .env');
    process.exit(1);
  }

  const info = describeConfig(cfg);
  console.log(`  Почта:    ${info.mail}`);
  console.log(`  ИИ:       ${info.llm}`);
  console.log(`  Голос:    ${info.stt}`);
  console.log(`  Telegram: ${info.telegram}`);
  console.log(`  База:     ${info.db}`);
  console.log('');

  if (!cfg.has.mail) {
    logger.warn('Почта не настроена: бот запустится, но /sync не сможет читать письма (MAIL_USER/MAIL_PASSWORD).');
  }
  if (!cfg.has.llm) {
    logger.warn('ИИ-ключ не задан: разбор писем и ответы будут по правилам (LLM_API_KEY).');
  }
  if (!cfg.telegram.allowedChatIds.length) {
    logger.warn('ALLOWED_CHAT_IDS пуст: напишите боту /start, он покажет ваш chat_id — добавьте его в .env.');
  }

  const store = openStore(cfg.db.path);
  const llm = createLlm(cfg);
  const bot = createBot({ cfg, store, llm });

  let stopping = false;
  let syncing = false;
  let timer = null;

  async function shutdown(signal) {
    if (stopping) return;
    stopping = true;
    logger.info(`Получен ${signal}, останавливаюсь…`);
    bot.stop();
    if (timer) clearInterval(timer);
    // Give the polling loop a moment to unwind before closing the DB.
    await sleep(800);
    try {
      store.close();
    } catch {
      /* ignore */
    }
    process.exit(0);
  }

  process.on('SIGINT', () => shutdown('SIGINT'));
  process.on('SIGTERM', () => shutdown('SIGTERM'));

  if (cfg.sync.intervalMin > 0 && cfg.has.mail) {
    const everyMs = Math.max(5, cfg.sync.intervalMin) * 60_000;
    timer = setInterval(async () => {
      if (syncing || stopping) return;
      syncing = true;
      try {
        const res = await runSync({ cfg, store, llm });
        store.metaSet('last_sync_at', res.finished);
        if (res.fetched > 0) {
          await bot.notify(`📬 Автопроверка: ${res.fetched} новых писем, разобрано ${res.analyzed}.`);
        }
      } catch (err) {
        logger.error(`Автосинхронизация: ${err.message}`);
      } finally {
        syncing = false;
      }
    }, everyMs);
    logger.info(`Автосинхронизация каждые ${cfg.sync.intervalMin} мин`);
  }

  try {
    await bot.start();
  } catch (err) {
    logger.error(`Бот не смог запуститься: ${err.message}`);
    store.close();
    process.exit(1);
  }

  await shutdown('конец работы');
}

process.on('unhandledRejection', (err) => {
  logger.error(`Необработанный rejected promise: ${err?.stack ?? err}`);
});

main().catch((err) => {
  logger.error(err?.stack ?? String(err));
  process.exit(1);
});
