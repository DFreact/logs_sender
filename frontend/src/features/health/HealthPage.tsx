import { MetricsPanel } from './MetricsPanel';
import { useEffect, useState } from 'react';
import { getHealth, type SystemHealth } from '../../api/health';
import { safeFailure, type SafeError } from '../../api/client';
import { WorkStateValues } from '../../api/generated';
import { ErrorNotice } from '../../components/Forms';
import { useI18n } from '../../i18n';
import { componentCodes, healthReasons, healthStatuses, workCounts } from '../../i18n/mappings';

export function HealthPage() {
  const { t, formatDate, formatNumber } = useI18n();
  const [data, setData] = useState<SystemHealth>();
  const [error, setError] = useState<SafeError>();
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    getHealth(controller.signal).then((value) => {
      if (!controller.signal.aborted) { setData(value); setError(undefined); }
    }).catch((err) => { if (!controller.signal.aborted) setError(safeFailure(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    const timer = window.setTimeout(() => setRevision((value) => value + 1), 15000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [revision]);
  return <>
    <div className="section-heading"><div><h1>{t('navigation.health')}</h1><p className="muted">{t('health.subtitle')}</p></div>
      <button className="secondary" disabled={loading} onClick={() => setRevision((value) => value + 1)}>{t('common.refresh')}</button>
    </div>
    <ErrorNotice error={error} />
    {error && data && <p className="notice warning" role="status">{t('health.oldData')}</p>}
    {!data && loading && <p role="status">{t('common.loading')}</p>}
    {data && <>
      {data.metrics && <MetricsPanel data={data.metrics} />}
      <section className="card health-summary" aria-labelledby="health-summary-title">
        <h2 id="health-summary-title">{t('health.overall')}</h2>
        <span className={`badge ${data.status === 'HEALTHY' ? 'positive' : 'warning'}`}>{t(healthStatuses[data.status])}</span>
        <p className="timestamp">{t('health.measuredAt', { date: formatDate(data.checked_at) })}</p>
        <p className="field-hint">{t('health.autoRefresh')}</p>
      </section>
      <div className="health-grid">{data.components.map((item) => <section className="card component-card" key={item.component}>
        <h2>{t(componentCodes[item.component])}</h2>
        <span className={`badge ${item.status === 'HEALTHY' ? 'positive' : 'warning'}`}>{t(healthStatuses[item.status])}</span>
        <p className="field-hint">{t(item.code === 'UNREACHABLE' && item.component === 'BROKER' ? 'health.brokerUnavailable'
          : item.code === 'UNREACHABLE' && item.component === 'STORAGE' ? 'health.storageUnavailable' : healthReasons[item.code])}</p>
        {item.last_seen_at && <p className="timestamp">{t('health.lastSeen', { date: formatDate(item.last_seen_at) })}</p>}
      </section>)}</div>
      <section className="card" aria-labelledby="queue-title"><h2 id="queue-title">{t('health.queueTitle')}</h2>
        <p className="muted">{t('health.queueHint')}</p>
        <dl className="queue-counts">{WorkStateValues.map((state) => <div key={state}><dt>{t(workCounts[state])}</dt><dd>{formatNumber(data.queue[state])}</dd></div>)}</dl>
        {Object.values(data.queue).every((value) => value === 0) && <p>{t('health.noTasks')}</p>}
        {!!data.queue.FAILED && <p className="notice warning">{t('health.failedTasks')}</p>}
        <p className="field-hint">{t('health.oldestDue', { seconds: formatNumber(data.oldest_due_seconds) })}</p>
      </section>
    </>}
  </>;
}
