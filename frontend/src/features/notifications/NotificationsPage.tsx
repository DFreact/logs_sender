import { useEffect, useState } from 'react';
import { getNotifications, type Notification } from '../../api/notifications';
import { NotificationStatusValues } from '../../api/generated';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n } from '../../i18n';
import { notificationStatuses, deliveryReasons, labelKey } from '../../i18n/mappings';
import { ErrorNotice, Pagination } from '../../components/Forms';
export function NotificationsPage({ deadLetters = false, eventId }: { deadLetters?: boolean; eventId?: string }) {
  const { t, formatDate, formatNumber } = useI18n();
  const [rows, setRows] = useState<Notification[]>(), [total, setTotal] = useState(0), [offset, setOffset] = useState(0), [status, setStatus] = useState(''), [error, setError] = useState<SafeError>(), [revision, setRevision] = useState(0), [loading, setLoading] = useState(true);
  useEffect(() => { const c = new AbortController(); setLoading(true); setError(undefined); getNotifications(offset, { status, dead_lettered: deadLetters, event_id: eventId }, c.signal).then((r) => { if (!c.signal.aborted) { setRows(r.items); setTotal(r.total); } }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }).finally(() => { if (!c.signal.aborted) setLoading(false); }); return () => c.abort(); }, [offset, status, deadLetters, eventId, revision]);
  return <>
    <div className="section-heading"><h1>{t(deadLetters ? 'navigation.deadLetters' : 'navigation.notifications')}</h1><button disabled={loading} onClick={() => setRevision((v) => v + 1)}>{t('common.refresh')}</button></div>
    <p className="muted">{t(deadLetters ? 'delivery.deadHint' : 'delivery.listHint')}</p>
    {eventId && <p><a href={`#events/${eventId}`}>{t('delivery.toEvent')}</a><span>{t('delivery.eventFilter')}</span></p>}
    {!deadLetters && <div className="filter-bar"><label htmlFor="notification-status">{t('users.status')}</label><select id="notification-status" value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">{t('delivery.allStatuses')}</option>{NotificationStatusValues.map((s) => <option key={s} value={s}>{t(notificationStatuses[s])}</option>)}</select></div>}
    <ErrorNotice error={error} />{loading && <p role="status">{t('common.loading')}</p>}
    {rows && <section className="card table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}>{rows.length === 0 ? <p>{t(deadLetters ? 'delivery.deadEmpty' : 'delivery.empty')}</p> : <table className="delivery-table"><thead><tr><th>{t('events.subject')}</th><th>{t('channels.selection')}</th><th>{t('users.status')}</th><th>{t('delivery.created')}</th><th>{t('delivery.attempts')}</th><th>{t('delivery.reason')}</th></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td><a href={`#notifications/${r.id}`}>{r.subject || t('events.noSubject')}</a>{r.is_test && <small>{t('delivery.test')}</small>}</td><td>{r.channel_name}</td><td>{t(notificationStatuses[r.status])}{r.uncertain && <small>{t('delivery.uncertainShort')}</small>}</td><td>{formatDate(r.created_at)}</td><td>{t('delivery.attemptCount', { count: formatNumber(r.attempt_count), max: formatNumber(r.max_attempts) })}</td><td>{r.failure_code ? t(labelKey(deliveryReasons, r.failure_code, 'errors.delivery')) : t('delivery.noFailure')}</td></tr>)}</tbody></table>}<Pagination offset={offset} total={total} onChange={setOffset} /></section>}
  </>;
}
