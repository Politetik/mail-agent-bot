import {
  answerContacts,
  answerContracts,
  answerQuestion,
  answerTasks,
  buildDigest,
  kindLabel,
  statusLabel,
} from './answer.js';
import { describeConfig } from './config.js';
import { formatPhone } from './patterns.js';
import { transcribe } from './stt.js';
import { runSync } from './sync.js';
import { TelegramApi, TelegramError } from './telegram.js';
import { escapeHtml, formatDate, formatDateTime, formatMoney, logger, sleep, truncate } from './util.js';

const KEYBOARD = {
  keyboard: [
    [{ text: '📥 Проверить почту' }, { text: '📊 Сводка' }],
    [{ text: '📇 Контакты' }, { text: '📄 Договоры' }],
    [{ text: '📌 Задачи' }, { text: '⚙️ Статус' }],
  ],
  resize_keyboard: true,
  is_persistent: true,
};

const BUTTON_ROUTES = {
  '📥 Проверить почту': '/sync',
  '📊 Сводка': '/digest',
  '📇 Контакты': '/contacts',
  '📄 Договоры': '/contracts',
  '📌 Задачи': '/tasks',
  '⚙️ Статус': '/status',
};

function stripHtml(text) {
  return String(text ?? '')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<\/p>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replaceAll('&lt;', '<')
    .replaceAll('&gt;', '>')
    .replaceAll('&amp;', '&')
    .trim();
}

export function createBot({ cfg, store, llm }) {
  const tg = new TelegramApi(cfg.telegram.token, { base: cfg.telegram.apiBase });
  const busy = new Set();
  const warned = new Set();
  let running = false;
  let offset = 0;

  const isAllowed = (chatId) => cfg.telegram.allowedChatIds.includes(Number(chatId));

  async function send(chatId, text, opts = {}) {
    try {
      return await tg.sendMessage(chatId, text, opts);
    } catch (err) {
      if (err instanceof TelegramError && /parse entities|entit/i.test(String(err.description))) {
        logger.warn('HTML не распарсился, отправляю как текст');
        return tg.sendMessage(chatId, stripHtml(text), { ...opts, parseMode: undefined });
      }
      throw err;
    }
  }

  function progressReporter(chatId) {
    let messageId = null;
    let lastAt = 0;
    let lastText = '';
    return {
      async begin(text) {
        const msg = await send(chatId, text);
        messageId = msg?.message_id ?? null;
        lastText = text;
        lastAt = Date.now();
      },
      async update(text, { force = false } = {}) {
        if (!messageId) return;
        if (text === lastText) return;
        const now = Date.now();
        if (!force && now - lastAt < 1500) return;
        lastAt = now;
        lastText = text;
        try {
          await tg.editMessageText(chatId, messageId, text);
        } catch (err) {
          logger.debug(`editMessageText: ${err.message}`);
        }
      },
      async finish(text) {
        if (!messageId) return send(chatId, text);
        try {
          await tg.editMessageText(chatId, messageId, text);
          return null;
        } catch {
          return send(chatId, text);
        }
      },
    };
  }

  function denyMessage(chatId) {
    return [
      '🔒 Доступ не настроен.',
      '',
      'Ваш chat_id: <code>' + chatId + '</code>',
      'Добавьте его в <code>ALLOWED_CHAT_IDS</code> в файле <code>.env</code> и перезапустите бота.',
      'Это защита: бот читает вашу почту, посторонние к ней доступа не получат.',
    ].join('\n');
  }

  const HELP = [
    '<b>Что я умею</b>',
    '',
    '• /sync — прочитать новую почту и разобрать её',
    '• /digest [дней] — сводка: договоры, риски, что ждёт ответа',
    '• /contacts [имя] — контакты с телефонами и адресами',
    '• /contracts — реестр договоров',
    '• /tasks — что висит и ждёт ответа',
    '• /status — состояние подключений и базы',
    '• /show 12 — показать письмо по номеру',
    '',
    'Просто напишите вопрос текстом или отправьте голосовое 🎤:',
    '«какой телефон у Анны Смирновой?»',
    '«что по договору с Ромашкой?»',
    '«о чём договорились по поставке?»',
  ].join('\n');

  async function handleSync(chatId) {
    if (!cfg.has.mail) {
      return send(
        chatId,
        '📭 Почта не настроена.\n\nВпишите в <code>.env</code>:\n<code>MAIL_USER=you@yandex.ru</code>\n<code>MAIL_PASSWORD=пароль_приложения</code>\n\nПароль приложения (не обычный пароль от почты):\n• Яндекс: id.yandex.ru → Безопасность → Пароли приложений; включите IMAP в Настройки → Почтовые программы.\n• Mail.ru: Настройки → Безопасность → Пароли для внешних приложений.',
      );
    }

    const reporter = progressReporter(chatId);
    await reporter.begin('🔄 Читаю почту…');
    const started = Date.now();
    try {
      const result = await runSync({
        cfg,
        store,
        llm,
        onProgress: (p) => {
          if (p.phase === 'fetch') {
            const where = p.folder ? ` · ${p.folder} ${p.done}/${p.total}` : '';
            reporter.update(`🔄 Читаю почту…${where}`);
          } else if (p.phase === 'analyze') {
            reporter.update(`🧠 Разбираю письма: ${p.done}/${p.total}`);
          }
        },
      });
      const seconds = ((Date.now() - started) / 1000).toFixed(1);
      store.metaSet('last_sync_at', result.finished);
      const lines = [
        '✅ <b>Готово</b>',
        '',
        `• Новых писем: <b>${result.fetched}</b>`,
        `• Разобрано ИИ: <b>${result.analyzed}</b>`,
        `• Дублей пропущено: ${result.duplicates}`,
        `• Ошибок: ${result.failed + result.errors.length}`,
        `• Время: ${seconds} с`,
      ];
      if (cfg.has.llm) lines.push('', 'Спросите что-нибудь — или нажмите «📊 Сводка».');
      else lines.push('', '⚠️ ИИ-ключ не задан, работает разбор по правилам. Подробные ответы — с <code>LLM_API_KEY</code>.');
      await reporter.finish(lines.join('\n'));
    } catch (err) {
      logger.error(`Синхронизация не удалась: ${err.message}`);
      await reporter.finish(`❌ Не удалось прочитать почту.\n\n${escapeHtml(err.message)}`);
    }
  }

  async function askAndReply(chatId, question) {
    await tg.sendChatAction(chatId, 'typing');
    const res = await answerQuestion({ store, cfg, llm, question });
    let text = res.text;
    if (res.emailIds?.length) {
      text += `\n\n<i>Писем в ответе: ${res.emailIds.length}. Детали: /show ${res.emailIds[0]}</i>`;
    }
    await send(chatId, text);
  }

  async function handleVoice(chatId, message) {
    const file = message.voice ?? message.audio ?? message.video_note;
    if (!file) return;

    if (!cfg.has.stt) {
      return send(
        chatId,
        '🎤 Голосовые сообщения требуют распознавания речи.\n\nДобавьте в <code>.env</code>: <code>OPENAI_API_KEY=sk-...</code> (Whisper) либо укажите свой <code>STT_BASE_URL</code>.',
      );
    }

    await tg.sendChatAction(chatId, 'record_voice');
    try {
      const info = await tg.getFile(file.file_id);
      const buffer = await tg.downloadFile(info.file_path);
      const { text } = await transcribe(cfg, buffer, { filename: 'voice.ogg' });
      await send(chatId, `🎤 <i>Распознано:</i> ${escapeHtml(truncate(text, 500))}`);
      await askAndReply(chatId, text);
    } catch (err) {
      logger.warn(`Голос не обработан: ${err.message}`);
      await send(chatId, `🎤 Не смог распознать голосовое.\n\n${escapeHtml(err.message)}`);
    }
  }

  async function showEmail(chatId, id) {
    const email = store.getEmail(Number(id));
    if (!email) return send(chatId, `Письмо #${escapeHtml(String(id))} не найдено.`);
    const lines = [
      `<b>[#${email.id}] ${escapeHtml(truncate(email.subject ?? '(без темы)', 160))}</b>`,
      '',
      `👤 ${escapeHtml(email.from_name || '—')} &lt;${escapeHtml(email.from_addr ?? '')}&gt;`,
      `📅 ${formatDateTime(email.sent_at, cfg.agent.timezone)}`,
      `📁 ${escapeHtml(email.folder)}`,
    ];
    if (email.kind) lines.push(`🏷 ${kindLabel(email.kind)} · ${statusLabel(email.status)}`);
    if (email.summary) lines.push('', `<i>${escapeHtml(truncate(email.summary, 500))}</i>`);
    if (email.amount) lines.push(`💰 ${formatMoney(email.amount, email.currency ?? 'RUB')}`);
    if (email.deadline) lines.push(`📅 срок: ${formatDate(email.deadline)}`);
    if (email.risks?.length) lines.push('', `❗ Риски: ${escapeHtml(email.risks.join('; '))}`);
    if (email.phones?.length) lines.push(`📞 ${email.phones.map((p) => escapeHtml(formatPhone(p))).join(', ')}`);
    if (email.addresses?.length) lines.push(`📍 ${escapeHtml(email.addresses.join(' | '))}`);
    lines.push('', '<b>Текст</b>', escapeHtml(truncate(email.body_text ?? '', 2500)));
    await send(chatId, lines.join('\n'));
  }

  async function handleCommand(chatId, text) {
    const [rawCmd, ...args] = text.split(/\s+/);
    const cmd = rawCmd.toLowerCase().replace(/@\w+$/, '');
    const arg = args.join(' ').trim();

    switch (cmd) {
      case '/start':
        return send(
          chatId,
          [
            '👋 <b>Я ваш почтовый ИИ-агент</b>',
            '',
            'Читаю почту Mail.ru и разбираю договоры, переговоры и контакты. Ищу телефоны и адреса — текстом или голосом.',
            '',
            `Ваш chat_id: <code>${chatId}</code>`,
            '',
            'Начните с «📥 Проверить почту» или просто задайте вопрос.',
            '',
            '/help — все команды',
          ].join('\n'),
          { replyMarkup: KEYBOARD },
        );
      case '/help':
        return send(chatId, HELP, { replyMarkup: KEYBOARD });
      case '/id':
        return send(chatId, `Ваш chat_id: <code>${chatId}</code>`);
      case '/status': {
        const stats = store.stats();
        const info = describeConfig(cfg);
        const lastSync = store.metaGet('last_sync_at');
        return send(
          chatId,
          [
            '⚙️ <b>Статус</b>',
            '',
            `📧 Почта: ${escapeHtml(info.mail)}`,
            `🧠 ИИ: ${escapeHtml(info.llm)}`,
            `🎤 Голос: ${escapeHtml(info.stt)}`,
            '',
            `📬 Писем: <b>${stats.emails}</b> · разобрано: ${stats.analyzed}`,
            `📇 Контактов: ${stats.contacts} · 📄 договоров: ${stats.contracts}`,
            `📌 Открытых задач: ${stats.openTasks} · ❗ высокий риск: ${stats.highRisk}`,
            '',
            `🕓 Последняя синхронизация: ${lastSync ? formatDateTime(lastSync, cfg.agent.timezone) : 'ещё не было'}`,
          ].join('\n'),
        );
      }
      case '/sync':
        return handleSync(chatId);
      case '/digest': {
        const days = Math.max(1, Math.min(365, Number(arg) || 7));
        const digest = buildDigest(store, { days });
        return send(chatId, digest.text);
      }
      case '/contacts': {
        const res = arg ? answerContacts({ store, question: arg }) : answerContacts({ store, question: '' });
        return send(chatId, res.text);
      }
      case '/contracts':
        return send(chatId, answerContracts({ store }).text);
      case '/tasks':
        return send(chatId, answerTasks({ store }).text);
      case '/show': {
        if (!arg) return send(chatId, 'Укажите номер письма: <code>/show 12</code>');
        return showEmail(chatId, arg);
      }
      case '/ask':
        if (!arg) return send(chatId, 'Напишите вопрос после команды: <code>/ask какой телефон у Анны?</code>');
        return askAndReply(chatId, arg);
      default:
        return send(chatId, `Не знаю команду ${escapeHtml(cmd)}. /help — список команд.`);
    }
  }

  async function handleMessage(message) {
    const chatId = Number(message.chat.id);
    if (!isAllowed(chatId)) {
      if (!warned.has(chatId)) {
        warned.add(chatId);
        logger.warn(`Отклонён доступ для chat_id=${chatId}`);
        await send(chatId, denyMessage(chatId));
      }
      return;
    }

    if (busy.has(chatId)) {
      await send(chatId, '⏳ Ещё работаю над прошлым запросом, секунду…');
      return;
    }

    const text = (message.text ?? message.caption ?? '').trim();

    if (message.voice || message.audio || message.video_note) {
      busy.add(chatId);
      try {
        await handleVoice(chatId, message);
      } finally {
        busy.delete(chatId);
      }
      return;
    }

    if (!text) {
      await send(chatId, 'Пока понимаю текст и голосовые. Пришлите вопрос текстом или голосом 🎤');
      return;
    }

    const routed = BUTTON_ROUTES[text] ?? (text.startsWith('/') ? text : null);
    busy.add(chatId);
    try {
      if (routed) {
        await handleCommand(chatId, routed);
      } else {
        await askAndReply(chatId, text);
      }
    } catch (err) {
      logger.error(`Ошибка обработки сообщения: ${err.message}`);
      await send(chatId, `❌ Ошибка: ${escapeHtml(err.message)}`).catch(() => null);
    } finally {
      busy.delete(chatId);
    }
  }

  const bot = {
    tg,
    get running() {
      return running;
    },
    async start() {
      const me = await tg.getMe();
      logger.info(`Бот @${me.username} запущен (long polling)`);
      running = true;
      while (running) {
        try {
          const updates = await tg.getUpdates(offset, 50);
          for (const update of updates) {
            offset = update.update_id + 1;
            const message = update.message ?? update.edited_message;
            if (!message) continue;
            if (message.from?.is_bot) continue;
            handleMessage(message).catch((err) => logger.error(`update ${update.update_id}: ${err.message}`));
          }
        } catch (err) {
          if (!running) break;
          logger.error(`Polling error: ${err.message}`);
          await sleep(3000);
        }
      }
      logger.info('Бот остановлен');
    },
    stop() {
      running = false;
    },
    handleMessage,
    async notify(text) {
      const chatId = cfg.telegram.allowedChatIds[0];
      if (!chatId) return;
      await send(chatId, text).catch((err) => logger.warn(`notify: ${err.message}`));
    },
  };

  return bot;
}
