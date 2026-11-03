import { logger } from './util.js';

export class LlmError extends Error {
  constructor(message, status) {
    super(message);
    this.name = 'LlmError';
    this.status = status;
  }
}

function normalizeBaseUrl(url) {
  return String(url ?? '').replace(/\/+$/, '');
}

/**
 * Thin OpenAI-compatible client. Works with DeepSeek, OpenAI, OpenRouter,
 * Ollama and LM Studio — anything exposing /chat/completions.
 */
export function createLlm(cfg) {
  const baseUrl = normalizeBaseUrl(cfg.llm.baseUrl);
  const apiKey = cfg.llm.apiKey;
  const defaultModel = cfg.llm.model;
  const timeoutMs = cfg.llm.timeoutMs;

  const available = Boolean(cfg.has?.llm);

  async function request(path, body, { attempts = 3 } = {}) {
    if (!available) throw new LlmError('LLM не настроен: заполните LLM_API_KEY в .env');
    let lastError = null;
    for (let i = 0; i < attempts; i += 1) {
      try {
        const res = await fetch(`${baseUrl}${path}`, {
          method: 'POST',
          headers: {
            'content-type': 'application/json',
            ...(apiKey ? { authorization: `Bearer ${apiKey}` } : {}),
          },
          body: JSON.stringify(body),
          signal: AbortSignal.timeout(timeoutMs),
        });

        if (res.status === 429 || res.status >= 500) {
          const text = await res.text().catch(() => '');
          lastError = new LlmError(`LLM ${res.status}: ${text.slice(0, 300)}`, res.status);
          await new Promise((r) => setTimeout(r, 1200 * (i + 1)));
          continue;
        }
        if (!res.ok) {
          const text = await res.text().catch(() => '');
          throw new LlmError(`LLM ${res.status}: ${text.slice(0, 500)}`, res.status);
        }
        return await res.json();
      } catch (err) {
        if (err instanceof LlmError && err.status && err.status < 500 && err.status !== 429) throw err;
        lastError = err;
        if (i < attempts - 1) await new Promise((r) => setTimeout(r, 1200 * (i + 1)));
      }
    }
    throw lastError ?? new LlmError('LLM request failed');
  }

  async function chat({ messages, model, temperature, maxTokens, json = false }) {
    const body = {
      model: model ?? defaultModel,
      messages,
      temperature: temperature ?? cfg.llm.temperature,
      max_tokens: maxTokens ?? cfg.llm.maxTokens,
      stream: false,
    };
    if (json) body.response_format = { type: 'json_object' };

    let data;
    try {
      data = await request('/chat/completions', body);
    } catch (err) {
      // Some providers reject response_format; retry once without it.
      if (json && err instanceof LlmError && err.status === 400) {
        delete body.response_format;
        data = await request('/chat/completions', body, { attempts: 1 });
      } else {
        throw err;
      }
    }

    const content = data?.choices?.[0]?.message?.content;
    if (typeof content !== 'string') {
      throw new LlmError('LLM вернул пустой ответ');
    }
    return {
      content,
      usage: data?.usage ?? null,
      model: data?.model ?? body.model,
    };
  }

  async function ping() {
    const res = await chat({
      messages: [{ role: 'user', content: 'Ответь одним словом: ok' }],
      maxTokens: 300,
      temperature: 0,
    });
    return res.content.trim();
  }

  logger.debug(`LLM инициализирован: ${baseUrl} / ${defaultModel}`);
  return { available, baseUrl, model: defaultModel, chat, ping };
}
