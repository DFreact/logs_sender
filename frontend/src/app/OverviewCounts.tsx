import { useEffect, useState } from 'react';
import { getOverview, overviewLabels, type Overview } from '../api/metrics';
import { safeFailure, type SafeError } from '../api/client';
import { ErrorNotice } from '../components/Forms';
import { useI18n } from '../i18n';

export function OverviewCounts() {
  const { t, formatNumber } = useI18n();
  const [data, setData] = useState<Overview>(), [error, setError] = useState<SafeError>();
  const [revision, setRevision] = useState(0);
  useEffect(() => { const controller = new AbortController();
    getOverview(controller.signal).then((value) => { if (!controller.signal.aborted) { setData(value); setError(undefined); } }).catch((err) => { if (!controller.signal.aborted) setError(safeFailure(err)); });
    const timer = window.setTimeout(() => setRevision((old) => old + 1), 15000);
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, [revision]);
  return <section className="card"><h2>{t('metrics.overview')}</h2><p className="field-hint">{t('metrics.overviewHint')}</p><ErrorNotice error={error} />
    {error && data && <p role="status" className="notice warning">{t('health.oldData')}</p>}
    {!data && !error && <p role="status">{t('common.loading')}</p>}
    {data && <dl className="queue-counts">{(Object.keys(overviewLabels) as (keyof Overview)[]).map((key) => <div key={key}><dt>{t(overviewLabels[key])}</dt><dd>{formatNumber(data[key])}</dd></div>)}</dl>}
  </section>;
}
