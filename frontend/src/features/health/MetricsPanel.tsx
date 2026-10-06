import type { Metrics } from '../../api/metrics';
import { rateLabels } from '../../api/metrics';
import { useI18n } from '../../i18n';

export function MetricsPanel({ data }: { data: Metrics }) {
  const { t, formatDate, formatNumber } = useI18n();
  const amount = (value: number | null) => value === null ? t('metrics.noData') : formatNumber(Math.round(value * 100) / 100);
  const bytes = (value: number | null) => value === null ? t('metrics.noData') : t('metrics.gib', { value: amount(value / 2 ** 30) });
  return <section className="card metrics-panel" aria-labelledby="metrics-title"><h2 id="metrics-title">{t('metrics.title')}</h2>
    <p className="field-hint">{t('metrics.ratesHint')}</p>
    {!data.fresh && <p className="notice warning" role="status">{t('metrics.stale')}</p>}
    <dl className="queue-counts">{(Object.keys(rateLabels) as (keyof typeof rateLabels)[]).map((key) => <div key={key}><dt>{t(rateLabels[key])}</dt><dd>{data.rates[key] == null ? t('metrics.noData') : t('metrics.perSecond', { value: amount(data.rates[key]) })}</dd></div>)}</dl>
    <h3>{t('metrics.resources')}</h3><p className="field-hint">{t('metrics.resourcesHint')}</p>
    <dl className="queue-counts">
      <div><dt>{t('metrics.cpu')}</dt><dd>{data.cpu_percent == null ? t('metrics.noData') : t('metrics.percent', { value: amount(data.cpu_percent) })}</dd></div>
      <div><dt>{t('metrics.memory')}</dt><dd>{bytes(data.memory?.available ?? null)}</dd></div>
      <div><dt>{t('metrics.disk')}</dt><dd>{bytes(data.disk?.free ?? null)}</dd></div>
      <div><dt>{t('metrics.forecast')}</dt><dd>{data.disk_remaining_seconds == null ? t('metrics.noData') : t('metrics.days', { value: amount(data.disk_remaining_seconds / 86400) })}</dd></div>
    </dl><p className="field-hint">{t('metrics.forecastHint')}</p>
    {data.sampled_at && <p className="timestamp">{t('metrics.sampledAt', { date: formatDate(data.sampled_at) })}</p>}
  </section>;
}
