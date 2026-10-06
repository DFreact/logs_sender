import { useEffect, useState } from 'react';
import { getRuns, type Run } from '../../api/escalations';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n } from '../../i18n';
import { escalationStates, escalationStepStates, eventStatuses, notificationStatuses } from '../../i18n/mappings';
import { ErrorNotice, Pagination } from '../../components/Forms';
export function EscalationHistory({ identifier }: { identifier: string }) {
  const { t, formatDate, formatNumber } = useI18n();
  const [rows, setRows] = useState<Run[]>(), [total, setTotal] = useState(0), [offset, setOffset] = useState(0), [revision, setRevision] = useState(0), [error, setError] = useState<SafeError>();
  useEffect(() => { const c = new AbortController(); let timer: ReturnType<typeof setTimeout> | undefined;
    async function load() { try { const r = await getRuns(identifier, offset, c.signal); if (!c.signal.aborted) { setRows(r.items); setTotal(r.total); setError(undefined); if (r.items.some((v) => v.state === 'ACTIVE')) timer = setTimeout(() => void load(), 5000); } } catch (e) { if (!c.signal.aborted) setError(safeFailure(e)); } } void load(); return () => { c.abort(); clearTimeout(timer); }; }, [identifier, offset, revision]);
  return <section className="card"><div className="section-heading"><h2>{t('escalation.history')}</h2><button className="secondary" onClick={() => setRevision((v) => v + 1)}>{t('common.refresh')}</button></div><p className="field-hint">{t('escalation.deliveryHint')}</p><ErrorNotice error={error} />{!rows && !error && <p role="status">{t('common.loading')}</p>}{rows?.length === 0 && <p>{t('escalation.noRuns')}</p>}{rows?.map((r) => <article key={r.id} className="rule-version"><h3>{t('routing.ruleVersion', { name: r.name, version: formatNumber(r.version) })}</h3><p>{t(escalationStates[r.state])}</p><p>{formatDate(r.created_at)}</p>{r.stop_reason && <p>{t('escalation.stopped', { status: t(eventStatuses[r.stop_reason]) })}</p>}<ol>{r.steps.map((s) => <li key={s.ordinal}><strong>{s.channel_name}</strong><p>{formatDate(s.due_at)}</p><p>{t(escalationStepStates[s.state])}</p>{s.notification_id && s.notification_status ? <a href={`#notifications/${s.notification_id}`}>{t(notificationStatuses[s.notification_status])}</a> : <p className="field-hint">{t('escalation.waitingDelivery')}</p>}</li>)}</ol></article>)}<Pagination offset={offset} total={total} onChange={setOffset} /></section>;
}
