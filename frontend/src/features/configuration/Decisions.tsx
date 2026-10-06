import { useEffect, useState } from 'react';
import { useI18n, labelKey } from '../../i18n';
import { severities, deliveryModes } from '../../i18n/mappings';
import { getProcessing, type Decision, type Fields } from '../../api/routing';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Pagination } from '../../components/Forms';
import { actionKeys, outcomeKeys } from './RoutingFields';
import { dedupFields } from './Editors';

export function FieldValues({ value }: { value: Partial<Fields> }) {
  const { t } = useI18n();
  return <dl>{Object.entries(value).map(([key, item]) => <div key={key}><dt>{t(labelKey(dedupFields, key))}</dt><dd>{key === 'severity' ? t(item == null ? 'events.severity.unset' : labelKey(severities, item)) : Array.isArray(item) ? item.join(t('common.listSeparator')) || t('routing.noValue') : item || t('routing.noValue')}</dd></div>)}</dl>;
}

export function DecisionList({ rows, preview = false }: { rows: Decision[]; preview?: boolean }) {
  const { t, formatDate, formatNumber } = useI18n();
  return <ul className="decision-list">{rows.map((d, index) => <li key={`${d.rule_id}-${d.action_index}-${index}`}>
    <strong>{t('routing.ruleVersion', { name: d.rule_name, version: formatNumber(d.version) })}</strong>
    <p>{t(labelKey(actionKeys, d.action.kind))}<span className="status-badge">{t(preview && d.outcome === 'APPLIED' ? 'routing.preview.APPLIED' : labelKey(outcomeKeys, d.outcome))}</span></p>
    <p className="field-hint">{t(d.action.scope === 'EVENT' ? 'routing.scope.EVENT' : 'routing.scope.OCCURRENCE')}</p>
    {Object.keys(d.applied).length > 0 && <FieldValues value={d.applied} />}
    {d.overridden.length > 0 && <p className="field-hint">{t('routing.overridden', { fields: d.overridden.map((f) => t(labelKey(dedupFields, f))).join(t('common.listSeparator')) })}</p>}
    {d.action.kind === 'NOTIFY' && <dl><dt>{t('routing.mode')}</dt><dd>{t(labelKey(deliveryModes, d.action.mode))}</dd>{d.action.delay_seconds != null && <><dt>{t('routing.delay')}</dt><dd>{formatNumber(d.action.delay_seconds / 60)}</dd></>}{d.action.scheduled_at && <><dt>{t('routing.scheduledAt')}</dt><dd>{formatDate(d.action.scheduled_at)}</dd></>}</dl>}
    {['NOTIFY', 'DIGEST', 'ESCALATE'].includes(d.action.kind) && <p className="field-hint">{t('routing.intentsHint')}</p>}
  </li>)}</ul>;
}

export function ProcessingHistory({ identifier }: { identifier: string }) {
  const { t } = useI18n(); const [opened, setOpened] = useState(false), [offset, setOffset] = useState(0), [total, setTotal] = useState(0);
  const [rows, setRows] = useState<Decision[]>(), [error, setError] = useState<SafeError>();
  useEffect(() => { if (!opened) return; const c = new AbortController(); setRows(undefined); setError(undefined);
    getProcessing(identifier, offset, c.signal).then((p) => { if (!c.signal.aborted) { setRows(p.items.map((r) => r.decision)); setTotal(p.total); } }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }); return () => c.abort();
  }, [identifier, offset, opened]);
  return <section className="card"><button className="secondary" aria-expanded={opened} onClick={() => setOpened(!opened)}>{t('routing.history')}</button>{opened && <><ErrorNotice error={error} />{!rows && !error && <p role="status">{t('common.loading')}</p>}{rows?.length === 0 && <p>{t('routing.noHistory')}</p>}{rows && <DecisionList rows={rows} />}<Pagination offset={offset} total={total} onChange={setOffset} /></>}</section>;
}
