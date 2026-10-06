import { useEffect, useState, type FormEvent } from 'react';
import { useI18n, labelKey } from '../../i18n';
import { eventStatuses, severities } from '../../i18n/mappings';
import { EventStatusValues, SeverityValues } from '../../api/generated';
import { getSources, type Source } from '../../api/configuration';
import { getEvents, type EventPage } from '../../api/events';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { localDate, parseDate } from '../../components/dateTime';
const emptyFilters = () => ({ q: '', source: '', status: '', severity: '', adapter: '', received_from: '', received_to: '' });
const recentFilters = () => ({ ...emptyFilters(), received_from: localDate(new Date(Date.now() - 86400000)) });

export function EventsPage() {
  const { t, formatDate, formatNumber } = useI18n();
  const [sources, setSources] = useState<Source[]>([]);
  useEffect(() => { const control = new AbortController(); getSources(control.signal).then((v) => { if (!control.signal.aborted) setSources(v.items); }).catch(() => {}); return () => control.abort(); }, []);
  const [draft, setDraft] = useState(recentFilters);
  const [filters, setFilters] = useState(draft);
  const [positions, setPositions] = useState<string[]>(['']);
  const cursor = positions[positions.length - 1];
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<EventPage>();
  const [error, setError] = useState<SafeError>();
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    const query = new URLSearchParams({ limit: '25', ...(cursor ? { cursor } : {}) });
    Object.entries(filters).forEach(([key, value]) => { if (key === 'source') { if (value) query.set(value === 'unknown' ? 'unknown_source' : 'source_id', value === 'unknown' ? 'true' : value); } else if (value) query.set(key, key.startsWith('received_') ? parseDate(value)!.toISOString() : value); });
    setLoading(true); setError(undefined); setData(undefined);
    getEvents(query, controller.signal).then((result) => { if (!controller.signal.aborted) setData(result); })
      .catch((err) => { if (!controller.signal.aborted) setError(safeFailure(err)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [cursor, filters, revision]);
  function apply(event: FormEvent) { event.preventDefault(); const from = draft.received_from ? parseDate(draft.received_from) : null, to = draft.received_to ? parseDate(draft.received_to) : null; if ((draft.received_from && !from) || (draft.received_to && !to) || (from && to && from > to)) { setError({ key: 'events.invalidPeriod' }); return; } setPositions(['']); setFilters({ ...draft }); }
  function recent(days: number) { const value = { ...draft, received_from: days ? localDate(new Date(Date.now() - days * 86400000)) : '', received_to: '' }; setDraft(value); setFilters(value); setPositions(['']); }
  function reset() { const empty = recentFilters(); setDraft(empty); setFilters(empty); setPositions(['']); }
  return <>
    <div className="page-heading"><h1>{t('navigation.events')}</h1><p>{t('events.subtitle')}</p></div>
    <form noValidate className="card event-filters event-feed-filters" onSubmit={apply}>
      <Field id="event-search" labelKey="events.search"><input id="event-search" value={draft.q} maxLength={200} placeholder={t('events.searchPlaceholder')} onChange={(e) => setDraft({ ...draft, q: e.target.value })} /></Field>
      <Field id="event-status" labelKey="events.statusLabel"><select id="event-status" value={draft.status} onChange={(e) => setDraft({ ...draft, status: e.target.value })}><option value="">{t('events.allStatuses')}</option>{EventStatusValues.map((value) => <option key={value} value={value}>{t(eventStatuses[value])}</option>)}</select></Field>
      <Field id="event-severity" labelKey="events.severityLabel"><select id="event-severity" value={draft.severity} onChange={(e) => setDraft({ ...draft, severity: e.target.value })}><option value="">{t('events.allSeverities')}</option>{SeverityValues.map((value) => <option key={value} value={value}>{t(severities[value])}</option>)}</select></Field>
      <Field id="event-adapter" labelKey="sources.inputAdapter"><select id="event-adapter" value={draft.adapter} onChange={(e) => setDraft({ ...draft, adapter: e.target.value })}><option value="">{t('events.allAdapters')}</option><option value="SMTP">{t('events.adapter.SMTP')}</option><option value="REST">{t('events.adapter.REST')}</option></select></Field>
      <Field id="event-source" labelKey="events.source"><select id="event-source" value={draft.source} onChange={(e) => setDraft({ ...draft, source: e.target.value })}><option value="">{t('events.allSources')}</option><option value="unknown">{t('sources.unknown')}</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select></Field>
      <Field id="event-from" labelKey="events.from"><input id="event-from" type="text" maxLength={16} placeholder={t('events.datePlaceholder')} value={draft.received_from} onChange={(e) => setDraft({ ...draft, received_from: e.target.value })} /></Field>
      <Field id="event-to" labelKey="events.to"><input id="event-to" type="text" maxLength={16} placeholder={t('events.datePlaceholder')} value={draft.received_to} onChange={(e) => setDraft({ ...draft, received_to: e.target.value })} /></Field>
      <p className="field-hint">{t('events.filterTimeHint')}</p>
      <div className="form-actions"><button type="button" className="secondary" onClick={() => recent(1)}>{t('events.day')}</button><button type="button" className="secondary" onClick={() => recent(7)}>{t('events.week')}</button><button type="button" className="secondary" onClick={() => recent(0)}>{t('events.allTime')}</button></div>
      <div className="form-actions"><button type="submit">{t('common.apply')}</button><button type="button" className="secondary" onClick={reset}>{t('common.resetFilters')}</button><button type="button" className="secondary" disabled={loading} onClick={() => { setPositions(['']); setRevision(revision + 1); }}>{t('common.refresh')}</button></div>
    </form>
    <ErrorNotice error={error} />
    {loading && <p role="status">{t('common.loading')}</p>}
    {data && <section className="card">
      <p>{t('events.shown', { count: formatNumber(data.items.length) })}</p><p className="field-hint">{t('events.browseHint')}</p>
      {data.items.length === 0 ? <p>{t('events.empty')}</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}><table className="event-feed-table"><thead><tr><th>{t('events.receivedAt')}</th><th>{t('events.subject')}</th><th>{t('events.statusLabel')}</th><th>{t('events.severityLabel')}</th><th>{t('events.source')}</th><th>{t('events.occurrences')}</th></tr></thead><tbody>{data.items.map((item) => <tr key={item.id}>
        <td>{formatDate(item.received_at)}</td><td><a href={`#events/${item.id}`}>{item.subject || t('events.noSubject')}</a><p className="muted">{item.sender}</p></td><td>{t(labelKey(eventStatuses, item.status))}</td><td>{t(item.severity === null ? 'events.severity.unset' : labelKey(severities, item.severity))}</td><td>{item.source_name ?? t('sources.unknown')}</td><td>{formatNumber(item.occurrence_count)}</td>
      </tr>)}</tbody></table></div>}
      <div className="pagination"><button className="secondary" disabled={positions.length <= 1 || loading} onClick={() => setPositions((v) => v.slice(0, -1))}>{t('events.newer')}</button><button className="secondary" disabled={!cursor || loading} onClick={() => { setPositions(['']); setRevision((v) => v + 1); }}>{t('events.latest')}</button><button className="secondary" disabled={!data.next_cursor || loading} onClick={() => { if (data.next_cursor) setPositions((v) => [...v.slice(-199), data.next_cursor!]); }}>{t('events.older')}</button></div>
    </section>}
  </>;
}
