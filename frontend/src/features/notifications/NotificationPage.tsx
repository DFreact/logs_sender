import { useEffect, useState } from 'react';
import { getNotification, getAttempts, mutateNotification, type Detail, type Attempt, type Operation } from '../../api/notifications';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n } from '../../i18n';
import { notificationStatuses, attemptStatuses, deliveryModes, deliveryReasons, labelKey } from '../../i18n/mappings';
import { ErrorNotice, Pagination } from '../../components/Forms';
import { useAuth } from '../auth/AuthContext';
export function NotificationPage({ identifier }: { identifier: string }) {
  const { t, formatDate, formatNumber } = useI18n(); const auth = useAuth();
  const [row, setRow] = useState<Detail>(), [attempts, setAttempts] = useState<Attempt[]>([]), [total, setTotal] = useState(0), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0), [error, setError] = useState<SafeError>(), [busy, setBusy] = useState(false), [notice, setNotice] = useState<'retry' | 'cancel'>(), [operation, setOperation] = useState<{ action: 'retry' | 'cancel'; body: Operation }>();
  useEffect(() => { const c = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() { try { const [n, a] = await Promise.all([getNotification(identifier, c.signal), getAttempts(identifier, offset, c.signal)]); if (!c.signal.aborted) { setRow(n); setAttempts(a.items); setTotal(a.total); if (['PENDING', 'PROCESSING', 'RETRYING'].includes(n.status)) timer = setTimeout(() => void load(), 3000); } } catch (e) { if (!c.signal.aborted) setError(safeFailure(e)); } }
    void load(); return () => { c.abort(); clearTimeout(timer); }; }, [identifier, offset, revision]);
  async function confirm() { if (!operation || !auth.identity || busy) return; setBusy(true); setError(undefined); try { await mutateNotification(identifier, operation.action, operation.body, auth.identity.csrf_token); setNotice(operation.action); setOperation(undefined); setRevision((v) => v + 1); } catch (e) { setError(safeFailure(e)); } finally { setBusy(false); } }
  function choose(action: 'retry' | 'cancel') { if (!row) return; setOperation({ action, body: { operation_key: crypto.randomUUID(), version: row.version } }); setNotice(undefined); setError(undefined); }
  return <>
    <a href="#notifications">{t('delivery.toList')}</a><h1>{t('delivery.single')}</h1><ErrorNotice error={error} />
    {notice && <p className="notice" role="status">{t(notice === 'retry' ? 'delivery.retryQueued' : 'delivery.cancelled')}</p>}
    {!row && !error && <p role="status">{t('common.loading')}</p>}
    {row && <>
      <section className="card"><div className="section-heading"><h2>{row.subject || t('events.noSubject')}</h2><button className="secondary" onClick={() => { setError(undefined); setRevision((v) => v + 1); }}>{t('common.refresh')}</button></div>
        {row.uncertain && <p className="notice">{t('delivery.uncertain')}</p>}
        <dl><div><dt>{t('users.status')}</dt><dd>{t(notificationStatuses[row.status])}</dd></div><div><dt>{t('channels.selection')}</dt><dd>{row.channel_name}</dd></div><div><dt>{t('routing.mode')}</dt><dd>{t(labelKey(deliveryModes, row.mode))}</dd></div><div><dt>{t('delivery.created')}</dt><dd>{formatDate(row.created_at)}</dd></div><div><dt>{t('delivery.due')}</dt><dd>{formatDate(row.due_at)}</dd></div><div><dt>{t('delivery.generation')}</dt><dd>{formatNumber(row.generation)}</dd></div><div><dt>{t('delivery.attempts')}</dt><dd>{t('delivery.attemptCount', { count: formatNumber(row.attempt_count), max: formatNumber(row.max_attempts) })}</dd></div></dl>
        {row.failure_code && <p className="notice">{t(labelKey(deliveryReasons, row.failure_code, 'errors.delivery'))}</p>}
        {row.event_id && <a href={`#events/${row.event_id}`}>{t('delivery.toEvent')}</a>}
        {row.is_test && <p>{t('delivery.test')}</p>}
        <p className="field-hint">{t('delivery.acceptedHint')}</p>
        <div className="form-actions">{auth.identity?.permissions.includes('RETRY_NOTIFICATIONS') && row.status === 'FAILED' && <button disabled={busy || Boolean(operation)} onClick={() => choose('retry')}>{t('delivery.retry')}</button>}{auth.identity?.permissions.includes('CANCEL_NOTIFICATIONS') && ['PENDING', 'RETRYING', 'FAILED'].includes(row.status) && <button className="secondary" disabled={busy || Boolean(operation)} onClick={() => choose('cancel')}>{t('delivery.cancel')}</button>}</div>
        {row.status === 'PROCESSING' && <p className="field-hint">{t('delivery.processingHint')}</p>}
      </section>
      {operation && <section className="card" role="alertdialog" aria-labelledby="delivery-confirm"><h2 id="delivery-confirm">{t(operation.action === 'retry' ? 'delivery.retryTitle' : 'delivery.cancelTitle')}</h2><p>{t(operation.action === 'retry' ? 'delivery.retryHint' : 'delivery.cancelHint')}</p>{row.uncertain && <p>{t('delivery.uncertain')}</p>}<div className="form-actions"><button disabled={busy} onClick={() => void confirm()}>{t(operation.action === 'retry' ? 'delivery.retry' : 'delivery.cancel')}</button><button className="secondary" disabled={busy} onClick={() => setOperation(undefined)}>{t('common.close')}</button></div></section>}
      <section className="card"><h2>{t('delivery.prepared')}</h2>{row.prepared ? <><h3>{t('templates.body')}</h3><pre>{row.prepared.body}</pre>{row.prepared.html && <><h3>{t('templates.html')}</h3><pre>{row.prepared.html}</pre></>}</> : <p>{t('delivery.notPrepared')}</p>}<p className="field-hint">{t('delivery.frozenHint')}</p></section>
      <section className="card table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}><h2>{t('delivery.attemptHistory')}</h2>{attempts.length === 0 ? <p>{t('delivery.noAttempts')}</p> : <table className="delivery-table"><thead><tr><th>{t('delivery.generation')}</th><th>{t('delivery.attemptNumber')}</th><th>{t('delivery.started')}</th><th>{t('delivery.finished')}</th><th>{t('users.status')}</th><th>{t('delivery.reason')}</th></tr></thead><tbody>{attempts.map((a) => <tr key={a.id}><td>{formatNumber(a.generation)}</td><td>{formatNumber(a.attempt_number)}</td><td>{formatDate(a.started_at)}</td><td>{a.finished_at ? formatDate(a.finished_at) : t('delivery.inProgress')}</td><td>{t(attemptStatuses[a.status])}</td><td>{a.failure_code ? t(labelKey(deliveryReasons, a.failure_code, 'errors.delivery')) : t('delivery.noFailure')}</td></tr>)}</tbody></table>}<Pagination offset={offset} total={total} onChange={setOffset} /></section>
    </>}
  </>;
}
