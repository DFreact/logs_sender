import { useEffect, useId, useRef, useState, type FormEvent } from 'react';
import { useI18n, labelKey, type TranslationKey } from '../../i18n';
import { eventStatuses, severities } from '../../i18n/mappings';
import { SeverityValues } from '../../api/generated';
import { getSources, type Source } from '../../api/configuration';
import { simulate, type Simulation, type SimulationInput } from '../../api/routing';
import { ApiError, safeFailure, type SafeError } from '../../api/client';
import { Field, ErrorNotice, Pagination } from '../../components/Forms';
import { useAuth } from '../auth/AuthContext';
import { DecisionList, FieldValues } from './Decisions';

type Pair = { name: string; value: string; type: 'TEXT' | 'NUMBER' | 'BOOLEAN' | 'NULL' };
const typeKeys = { TEXT: 'simulation.type.TEXT', NUMBER: 'simulation.type.NUMBER', BOOLEAN: 'simulation.type.BOOLEAN', NULL: 'simulation.type.NULL' } satisfies Record<Pair['type'], TranslationKey>;
const dedupKeys: Record<string, TranslationKey> = { DISABLED: 'simulation.dedup.DISABLED', NEW: 'simulation.dedup.NEW', REPEAT: 'simulation.dedup.REPEAT', UNKNOWN_STREAM: 'simulation.dedup.UNKNOWN_STREAM' };

function Pairs({ rows, change, metadata = false }: { rows: Pair[]; change: (v: Pair[]) => void; metadata?: boolean }) {
  const { t } = useI18n(); const id = useId();
  const set = (index: number, pair: Pair) => change(rows.map((v, i) => i === index ? pair : v));
  return <fieldset><legend>{t(metadata ? 'simulation.metadata' : 'simulation.headers')}</legend>
    {rows.map((row, index) => <div key={index} className="condition-group"><div className="config-columns">
      <Field id={`${id}-${index}-name`} labelKey="simulation.fieldName"><input id={`${id}-${index}-name`} maxLength={120} value={row.name} onChange={(e) => set(index, { ...row, name: e.target.value })} /></Field>
      {metadata && <Field id={`${id}-${index}-type`} labelKey="simulation.valueType"><select id={`${id}-${index}-type`} value={row.type} onChange={(e) => set(index, { ...row, type: e.target.value as Pair['type'], value: e.target.value === 'BOOLEAN' ? 'true' : '' })}>{Object.entries(typeKeys).map(([type, key]) => <option value={type} key={type}>{t(key)}</option>)}</select></Field>}
      {row.type !== 'NULL' && <Field id={`${id}-${index}-value`} labelKey="config.value">{row.type === 'BOOLEAN' ? <select id={`${id}-${index}-value`} value={row.value} onChange={(e) => set(index, { ...row, value: e.target.value })}><option value="true">{t('simulation.true')}</option><option value="false">{t('simulation.false')}</option></select> : <input id={`${id}-${index}-value`} maxLength={1000} type={row.type === 'NUMBER' ? 'number' : 'text'} value={row.value} onChange={(e) => set(index, { ...row, value: e.target.value })} />}</Field>}
    </div><button type="button" className="secondary compact" onClick={() => change(rows.filter((_, i) => i !== index))}>{t('common.delete')}</button></div>)}
    <button type="button" className="secondary" disabled={rows.length >= 100} onClick={() => change([...rows, { name: '', value: '', type: 'TEXT' }])}>{t(metadata ? 'simulation.addMetadata' : 'simulation.addHeader')}</button>
  </fieldset>;
}

function Result({ value }: { value: Simulation }) {
  const { t, formatDate, formatNumber } = useI18n(); const [offset, setOffset] = useState(0);
  return <section className="card simulation-result"><h2>{t('simulation.result')}</h2><p>{t('simulation.checkedAt', { date: formatDate(value.checked_at) })}</p><p className="field-hint">{t('simulation.futureNotice')}</p>
    <dl><dt>{t('events.source')}</dt><dd>{value.source_name ?? t('sources.unknown')}</dd><dt>{t('events.statusLabel')}</dt><dd>{t(labelKey(eventStatuses, value.status))}</dd></dl>
    {value.manual_source ? <p>{t('simulation.manual')}</p> : value.identification ? <p>{t('routing.ruleVersion', { name: value.identification.name, version: formatNumber(value.identification.version) })}</p> : <p>{t('simulation.noIdentification')}</p>}
    <FieldValues value={value.result} />
    <h3>{t('config.dedup')}</h3><p>{t(labelKey(dedupKeys, value.deduplication.status))}</p>{value.deduplication.window_seconds && <p>{t('simulation.window', { seconds: formatNumber(value.deduplication.window_seconds) })}</p>}{value.deduplication.event_id && <a href={`#events/${value.deduplication.event_id}`}>{t('simulation.openEvent')}</a>}
    <h3>{t('simulation.retention')}</h3><p>{value.retention.event_days && value.retention.raw_days ? t(value.retention.status === 'ENABLED' ? 'simulation.retentionEnabled' : 'simulation.retentionDisabled', { events: formatNumber(value.retention.event_days), raw: formatNumber(value.retention.raw_days) }) : t('simulation.retentionUnavailable')}</p>
    <h3>{t('simulation.decisions')}</h3>{!value.decisions.length && <p>{t('routing.noMatches')}</p>}<DecisionList rows={value.decisions.slice(offset, offset + 25)} preview /><Pagination offset={offset} total={value.decisions.length} onChange={setOffset} />
  </section>;
}

export function SimulationPage() {
  const { t } = useI18n(); const auth = useAuth(); const control = useRef<AbortController | undefined>(undefined);
  const [sources, setSources] = useState<Source[]>([]), [value, setValue] = useState<SimulationInput>({ adapter: 'SMTP', source_id: null, sender: '', envelope_sender: '', subject: '', body: '', recipients: [], severity: null, headers: [], metadata: [] });
  const [headerRows, setHeaderRows] = useState<Pair[]>([]), [metadata, setMetadata] = useState<Pair[]>([]), [error, setError] = useState<SafeError>(), [pending, setPending] = useState(false), [result, setResult] = useState<Simulation>();
  useEffect(() => { const c = new AbortController(); getSources(c.signal).then((s) => { if (!c.signal.aborted) setSources(s.items.filter((v) => v.enabled)); }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }); return () => { c.abort(); control.current?.abort(); }; }, []);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!auth.identity || pending) return;
    if ([...metadata, ...headerRows].some((p) => !p.name.trim()) || metadata.some((p) => p.type === 'NUMBER' && (!p.value.trim() || !Number.isFinite(Number(p.value)) || Math.abs(Number(p.value)) > 1e100))) { setError({ key: 'simulation.invalid' }); return; }
    setPending(true); setError(undefined); setResult(undefined); const c = new AbortController(); control.current = c;
    const body = { ...value, recipients: value.recipients.map((s) => s.trim()).filter(Boolean), headers: headerRows.map((p) => ({ name: p.name, value: p.value })), metadata: metadata.map((p) => ({ name: p.name, value: p.type === 'NUMBER' ? Number(p.value) : p.type === 'BOOLEAN' ? p.value === 'true' : p.type === 'NULL' ? null : p.value })) };
    try { const r = await simulate(body, auth.identity.csrf_token, c.signal); if (!c.signal.aborted) setResult(r); }
    catch (e) { if (!c.signal.aborted) setError(e instanceof ApiError && e.status === 0 ? { key: 'errors.network' } : safeFailure(e)); } finally { if (!c.signal.aborted) setPending(false); }
  }
  return <><h1>{t('routing.test')}</h1><p className="muted">{t('simulation.hint')}</p><ErrorNotice error={error} />
    <form className="card" noValidate onSubmit={(e) => void submit(e)}><fieldset disabled={pending}><div className="config-columns">
      <Field id="test-source" labelKey="events.source"><select id="test-source" value={value.source_id ?? ''} onChange={(e) => setValue({ ...value, source_id: e.target.value || null })}><option value="">{t('simulation.automatic')}</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <Field id="test-adapter" labelKey="sources.inputAdapter"><select id="test-adapter" value={value.adapter} onChange={(e) => setValue({ ...value, adapter: e.target.value as 'SMTP' | 'REST' })}><option value="SMTP">{t('events.adapter.SMTP')}</option><option value="REST">{t('events.adapter.REST')}</option></select></Field>
      <Field id="test-severity" labelKey="events.severityLabel"><select id="test-severity" value={value.severity ?? ''} onChange={(e) => setValue({ ...value, severity: e.target.value || null })}><option value="">{t('events.severity.unset')}</option>{SeverityValues.map((s) => <option key={s} value={s}>{t(severities[s])}</option>)}</select></Field></div>
      <Field id="test-sender" labelKey="sources.sender"><input id="test-sender" maxLength={320} value={value.sender} onChange={(e) => setValue({ ...value, sender: e.target.value })} /></Field>
      {value.adapter === 'SMTP' && <Field id="test-envelope" labelKey="events.envelopeSender"><input id="test-envelope" maxLength={320} value={value.envelope_sender} onChange={(e) => setValue({ ...value, envelope_sender: e.target.value })} /></Field>}
      <Field id="test-recipients" labelKey="events.recipients" hint="simulation.recipientsHint"><input id="test-recipients" value={value.recipients.join(',')} onChange={(e) => setValue({ ...value, recipients: e.target.value.split(',') })} /></Field>
      <Field id="test-subject" labelKey="events.subject"><input id="test-subject" maxLength={500} value={value.subject} onChange={(e) => setValue({ ...value, subject: e.target.value })} /></Field>
      <Field id="test-body" labelKey="events.body"><textarea id="test-body" rows={5} maxLength={65536} value={value.body} onChange={(e) => setValue({ ...value, body: e.target.value })} /></Field>
      {value.adapter === 'SMTP' ? <Pairs rows={headerRows} change={setHeaderRows} /> : <Pairs rows={metadata} change={setMetadata} metadata />}
      <div className="form-actions"><button type="submit">{t(pending ? 'simulation.pending' : 'simulation.run')}</button></div>
    </fieldset></form>{result && <Result key={result.checked_at} value={result} />}</>;
}
