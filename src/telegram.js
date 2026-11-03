import { chunkText, sleep } from './util.js';

export class TelegramError extends Error {
  constructor(method, code, description) {
    super(`${method}: ${description} (code ${code})`);
    this.name = 'TelegramError';
    this.method = method;
    this.code = code;
    this.description = description;
  }
}

/**
 * Minimal Telegram Bot API client over fetch — no SDK dependency.
 */
export class TelegramApi {
  constructor(token, { base = 'https://api.telegram.org', timeoutMs = 60000 } = {}) {
    if (!token) throw new Error('TELEGRAM_BOT_TOKEN не задан');
    this.token = token;
    this.base = String(base).replace(/\/+$/, '');
    this.timeoutMs = timeoutMs;
  }

  async call(method, payload = {}, { timeoutMs } = {}) {
    const url = `${this.base}/bot${this.token}/${method}`;
    let res;
    try {
      res = await fetch(url, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(timeoutMs ?? this.timeoutMs),
      });
    } catch (err) {
      throw new TelegramError(method, 0, `сетевая ошибка: ${err.message}`);
    }

    let data;
    try {
      data = await res.json();
    } catch {
      throw new TelegramError(method, res.status, 'некорректный ответ Telegram');
    }

    if (data.ok) return data.result;

    if (data.error_code === 429) {
      const retryAfter = Number(data.parameters?.retry_after ?? 3);
      await sleep(Math.min(30, retryAfter) * 1000);
      return this.call(method, payload, { timeoutMs });
    }
    throw new TelegramError(method, data.error_code, data.description ?? 'unknown error');
  }

  getMe() {
    return this.call('getMe');
  }

  getUpdates(offset, timeoutSeconds = 50) {
    return this.call(
      'getUpdates',
      {
        offset,
        timeout: timeoutSeconds,
        allowed_updates: ['message', 'edited_message'],
      },
      { timeoutMs: (timeoutSeconds + 20) * 1000 },
    );
  }

  sendMessage(chatId, text, { parseMode = 'HTML', disablePreview = true, replyMarkup = null } = {}) {
    const parts = chunkText(text, 3800);
    let last = null;
    for (const part of parts) {
      last = this.call('sendMessage', {
        chat_id: chatId,
        text: part || '…',
        parse_mode: parseMode,
        link_preview_options: { is_disabled: disablePreview },
        ...(replyMarkup ? { reply_markup: replyMarkup } : {}),
      });
    }
    return last;
  }

  editMessageText(chatId, messageId, text, { parseMode = 'HTML' } = {}) {
    return this.call('editMessageText', {
      chat_id: chatId,
      message_id: messageId,
      text: text || '…',
      parse_mode: parseMode,
      link_preview_options: { is_disabled: true },
    });
  }

  sendChatAction(chatId, action = 'typing') {
    return this.call('sendChatAction', { chat_id: chatId, action }).catch(() => null);
  }

  getFile(fileId) {
    return this.call('getFile', { file_id: fileId });
  }

  async downloadFile(filePath) {
    const url = `${this.base}/file/bot${this.token}/${filePath}`;
    const res = await fetch(url, { signal: AbortSignal.timeout(this.timeoutMs) });
    if (!res.ok) throw new Error(`Не удалось скачать файл из Telegram: HTTP ${res.status}`);
    return Buffer.from(await res.arrayBuffer());
  }
}
