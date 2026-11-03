import { logger } from './util.js';

export class SttError extends Error {
  constructor(message) {
    super(message);
    this.name = 'SttError';
  }
}

/**
 * Transcribe audio through an OpenAI-compatible /audio/transcriptions endpoint
 * (OpenAI Whisper, or a local faster-whisper/openai-compatible server).
 */
export async function transcribe(cfg, audio, { filename = 'voice.ogg', language } = {}) {
  if (!cfg.has?.stt) {
    throw new SttError('Распознавание речи не настроено: задайте STT_API_KEY (или OPENAI_API_KEY).');
  }
  const baseUrl = String(cfg.stt.baseUrl).replace(/\/+$/, '');
  const form = new FormData();
  const bytes = audio instanceof Uint8Array ? audio : new Uint8Array(audio);
  form.append('file', new Blob([bytes], { type: 'audio/ogg' }), filename);
  form.append('model', cfg.stt.model);
  const lang = language ?? cfg.stt.language;
  if (lang) form.append('language', lang);
  form.append('response_format', 'json');

  logger.debug(`STT: ${bytes.length} байт → ${baseUrl}/audio/transcriptions (${cfg.stt.model})`);
  const res = await fetch(`${baseUrl}/audio/transcriptions`, {
    method: 'POST',
    headers: { authorization: `Bearer ${cfg.stt.apiKey}` },
    body: form,
    signal: AbortSignal.timeout(cfg.stt.timeoutMs),
  });

  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new SttError(`STT ${res.status}: ${text.slice(0, 400)}`);
  }
  const data = await res.json();
  const text = String(data?.text ?? '').trim();
  if (!text) throw new SttError('Речь не распознана (пустой ответ STT).');
  return { text, model: cfg.stt.model, language: lang };
}
