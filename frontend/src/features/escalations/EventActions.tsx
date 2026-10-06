import { useEffect, useState } from 'react';
import { changeEvent, getActions, type Action } from '../../api/escalations';
import type { Operation } from '../../api/notifications';
import type { Permission } from '../../api/generated';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n, type TranslationKey } from '../../i18n';
import { eventStatuses } from '../../i18n/mappings';
import { useAuth } from '../auth/AuthContext';
import { ErrorNotice, Pagination } from '../../components/Forms';
const labels = { acknowledge: 'eventActions.acknowledge', resolve: 'eventActions.resolve', suppress: 'eventActions.suppress' } satisfies Record<Action, TranslationKey>;
const titles = { acknowledge: 'eventActions.acknowledgeTitle', resolve: 'eventActions.resolveTitle', suppress: 'eventActions.suppressTitle' } satisfies Record<Action, TranslationKey>;
const permissions = { acknowledge: 'ACKNOWLEDGE_EVENTS', resolve: 'RESOLVE_EVENTS', suppress: 'SUPPRESS_EVENTS' } satisfies Record<Action, Permission>;
export function EventActions({ identifier, changed }: { identifier: string; changed: () => void }) {
  const { t, formatDate } = useI18n(); const auth = useAuth();
  const [data, setData] = useState<Awaited<ReturnType<typeof getActions>>>(), [offset, setOffset] = useState(0), [error, setError] = useState<SafeError>(), [busy, setBusy] = useState(false), [operation, setOperation] = useState<{ action: Action; body: Operation }>();
  useEffect(() => { const c = new AbortController(); getActions(identifier, offset, c.signal).then((r) => { if (!c.signal.aborted) { setData(r); setError(undefined); } }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }); return () => c.abort(); }, [identifier, offset]);
  async function confirm() { if (!operation || !auth.identity || busy) return; setBusy(true); setError(undefined); try { await changeEvent(identifier, operation.action, operation.body, auth.identity.csrf_token); setOperation(undefined); changed(); } catch (e) { setError(safeFailure(e)); } finally { setBusy(false); } }
  return <section className="card"><h2>{t('eventActions.title')}</h2><ErrorNotice error={error} />{!data && !error && <p role="status">{t('common.loading')}</p>}{data && <>
    <p>{t(eventStatuses[data.status])}</p>{data.acknowledged_at && <dl><dt>{t('eventActions.ackBy')}</dt><dd>{data.acknowledged_by ?? t('common.unknownValue')}</dd><dt>{t('eventActions.ackAt')}</dt><dd>{formatDate(data.acknowledged_at)}</dd></dl>}
    <div className="form-actions">{(['acknowledge', 'resolve', 'suppress'] as const).filter((action) => auth.identity?.permissions.includes(permissions[action]) && (action === 'acknowledge' ? data.status === 'NEW' : ['NEW', 'ACKNOWLEDGED'].includes(data.status))).map((action) => <button key={action} disabled={busy || Boolean(operation)} onClick={() => setOperation({ action, body: { operation_key: crypto.randomUUID(), version: data.version } })}>{t(labels[action])}</button>)}</div>
    {operation && <div role="alertdialog" aria-labelledby="event-confirm"><h3 id="event-confirm">{t(titles[operation.action])}</h3><p>{t(operation.action === 'suppress' ? 'eventActions.suppressHint' : 'eventActions.stopHint')}</p>{operation.action !== 'acknowledge' && <p>{t('eventActions.terminalHint')}</p>}<div className="form-actions"><button disabled={busy} onClick={() => void confirm()}>{t(labels[operation.action])}</button><button className="secondary" disabled={busy} onClick={() => setOperation(undefined)}>{t('common.close')}</button></div></div>}
    <h3>{t('eventActions.history')}</h3>{data.items.length === 0 ? <p>{t('eventActions.empty')}</p> : <ul>{data.items.map((item) => <li key={item.id}><strong>{t('eventActions.transition', { from: t(eventStatuses[item.from_status]), to: t(eventStatuses[item.to_status]) })}</strong><p>{item.author}</p><p>{formatDate(item.created_at)}</p></li>)}</ul>}<Pagination offset={offset} total={data.total} onChange={setOffset} />
  </>}</section>;
}
