import { analyzeEmail } from './analyze.js';
import { fetchNewEmails } from './mail.js';
import { logger, nowIso } from './util.js';

/**
 * Persist derived records (contacts, contracts, tasks) for an analysed e-mail.
 * Kept separate from the analysis itself so a re-run stays idempotent.
 */
export function persistDerived(store, email, analysis) {
  const contacts = Array.isArray(analysis.contacts) ? analysis.contacts : [];

  for (const c of contacts) {
    store.upsertContact({
      email_id: email.id,
      name: c.name ?? null,
      position: c.position ?? null,
      company: c.company ?? null,
      phone: c.phone ?? null,
      email: c.email ?? null,
      address: c.address ?? null,
      inn: c.inn ?? null,
      context: analysis.summary ?? null,
      confidence: analysis.confidence ?? null,
    });
  }

  // Even if the LLM produced no contacts, rule phones/addresses are worth keeping.
  if (!contacts.length && (analysis.phones?.length || analysis.addresses?.length)) {
    const phones = analysis.phones?.length ? analysis.phones : [null];
    for (const phone of phones) {
      store.upsertContact({
        email_id: email.id,
        name: email.from_name ?? null,
        phone,
        email: email.from_addr ?? null,
        address: analysis.addresses?.[0] ?? null,
        company: analysis.counterparty ?? null,
        context: analysis.summary ?? null,
      });
    }
  }

  if (analysis.kind === 'contract') {
    store.upsertContract({
      email_id: email.id,
      number: analysis.contract_number ?? null,
      counterparty: analysis.counterparty ?? null,
      subject: email.subject ?? null,
      amount: analysis.amount ?? null,
      currency: analysis.currency ?? 'RUB',
      end_date: analysis.deadline ?? null,
      status: analysis.status ?? 'new',
      risks: analysis.risks ?? [],
    });
  }

  for (const ask of analysis.asks ?? []) {
    store.addTask({ email_id: email.id, text: ask, due: analysis.deadline ?? null });
  }
}

/**
 * Analyse up to `limit` e-mails that have no analysis yet.
 * @returns {Promise<{analyzed:number, failed:number}>}
 */
export async function analyzePending({ cfg, store, llm, limit = null, onProgress = null }) {
  const cap = Math.max(0, Number(limit ?? cfg.sync.analyzeLimit) || 0);
  const pending = store.emailsWithoutAnalysis(cap);
  let analyzed = 0;
  let failed = 0;

  if (onProgress) onProgress({ phase: 'analyze', total: pending.length, done: 0 });

  for (let i = 0; i < pending.length; i += 1) {
    const row = pending[i];
    try {
      const analysis = await analyzeEmail(row, { llm, model: cfg.llm.model });
      store.saveAnalysis(row.id, analysis);
      persistDerived(store, row, analysis);
      analyzed += 1;
    } catch (err) {
      failed += 1;
      logger.warn(`Не удалось разобрать письмо #${row.id}: ${err.message}`);
    }
    if (onProgress) onProgress({ phase: 'analyze', total: pending.length, done: i + 1 });
  }

  return { analyzed, failed };
}

/**
 * Full sync: download new mail, then analyse whatever has not been analysed yet.
 */
export async function runSync({ cfg, store, llm, analyze = true, onProgress = null }) {
  const started = nowIso();
  const progress = (p) => {
    try {
      onProgress?.(p);
    } catch (err) {
      logger.warn(`onProgress: ${err.message}`);
    }
  };

  progress({ phase: 'fetch', done: 0, total: 0, folder: null });
  const mail = await fetchNewEmails(cfg, store, {
    onProgress: (p) => progress({ phase: 'fetch', ...p }),
  });

  let analysis = { analyzed: 0, failed: 0 };
  if (analyze) {
    analysis = await analyzePending({ cfg, store, llm, onProgress: progress });
  }

  const result = {
    started,
    finished: nowIso(),
    fetched: mail.fetched,
    duplicates: mail.duplicates,
    folders: mail.folders,
    errors: mail.errors,
    analyzed: analysis.analyzed,
    failed: analysis.failed,
  };
  logger.info(
    `Синхронизация: новых ${result.fetched}, дублей ${result.duplicates}, разобрано ${result.analyzed}, ошибок ${result.failed}`,
  );
  return result;
}
