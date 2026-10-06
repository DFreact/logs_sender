import { EventActions } from '../escalations/EventActions';
import { EscalationHistory } from '../escalations/EscalationHistory';
import { useEffect, useState } from 'react';
import { useI18n, labelKey } from '../../i18n';
import { eventStatuses, severities } from '../../i18n/mappings';
import { ProcessingHistory } from '../configuration/Decisions';
import { getEvent, type EventDetail } from '../../api/events';
import { ApiError, decodeError, safeFailure, SESSION_EXPIRED_EVENT, type SafeError } from '../../api/client';
import { ErrorNotice, Pagination } from '../../components/Forms';

export function EventPage({ identifier }: { identifier: string }) {
  const { t, formatDate, formatNumber } = useI18n();
  const [data, setData] = useState<EventDetail>();
  const [error, setError] = useState<SafeError>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [downloading, setDownloading] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); setData(undefined); setError(undefined);
    getEvent(identifier, controller.signal, offset).then((result) => { if (!controller.signal.aborted) setData(result); })
      .catch((err) => { if (!controller.signal.aborted) setError(safeFailure(err)); });
    return () => controller.abort();
  }, [identifier, revision, offset]);
  async function download(path: string, filename: string) {
    setError(undefined); setDownloading(true);
    try {
      let response: Response;
      try { response = await fetch(path, { credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(15000) }); }
      catch { throw new ApiError({ key: 'errors.network' }); }
      if (response.status === 401) window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
      if (!response.ok) { let payload: unknown; try { payload = await response.json(); } catch { /* safe fallback */ } throw new ApiError(decodeError(payload)); }
      if (response.headers.get('content-type') !== 'application/octet-stream') throw new ApiError({ key: 'errors.internalWithoutId' });
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) { setError(safeFailure(err)); } finally { setDownloading(false); }
  }
  const isMail = data?.input_adapter === 'SMTP';
  return <>
    <div className="page-heading"><a href="#events">{t('events.back')}</a><h1>{t('events.detailTitle')}</h1></div>
    <ErrorNotice error={error} />
    <button className="secondary" onClick={() => setRevision(revision + 1)}>{t('common.refresh')}</button>
    {!data && !error && <p role="status">{t('common.loading')}</p>}
    {data && <>
      <section className="card event-detail"><h2>{data.subject || t('events.noSubject')}</h2>
        <dl><dt>{t('events.statusLabel')}</dt><dd>{t(labelKey(eventStatuses, data.status))}</dd>
          <dt>{t('events.severityLabel')}</dt><dd>{t(data.severity === null ? 'events.severity.unset' : labelKey(severities, data.severity))}</dd>
          <dt>{t('config.category')}</dt><dd>{data.category || t('common.unknownValue')}</dd><dt>{t('config.eventType')}</dt><dd>{data.event_type || t('common.unknownValue')}</dd><dt>{t('config.tags')}</dt><dd>{data.tags.join(t('common.listSeparator')) || t('common.unknownValue')}</dd>
          <dt>{t('events.source')}</dt><dd>{data.source_name ?? t('sources.unknown')}</dd>
          <dt>{t('events.firstSeen')}</dt><dd>{formatDate(data.first_seen_at)}</dd><dt>{t('events.lastSeen')}</dt><dd>{formatDate(data.last_seen_at)}</dd>
          <dt>{t('sources.inputAdapter')}</dt><dd>{t(data.input_adapter === 'SMTP' ? 'events.adapter.SMTP' : data.input_adapter === 'REST' ? 'events.adapter.REST' : 'common.unknownValue')}</dd>
          <dt>{t('sources.sender')}</dt><dd>{data.sender || t('common.unknownValue')}</dd>
          <dt>{t('events.receivedAt')}</dt><dd>{formatDate(data.received_at)}</dd>
        </dl><h3>{t('events.body')}</h3><p className="field-hint">{t('events.previewHint')}</p><pre className="event-body">{data.body || t('events.emptyBody')}</pre>
      </section>
      <p><a href={`#notifications?event_id=${identifier}`}>{t('navigation.notifications')}</a></p>
      <EventActions identifier={identifier} changed={() => setRevision((v) => v + 1)} />
      <EscalationHistory identifier={identifier} />
      <ProcessingHistory identifier={identifier} />
      <section className="card"><h2>{t('events.history')}</h2><p>{t('events.historyCount', { count: formatNumber(data.occurrence_count) })}</p>
        {data.occurrences.map((item) => <article className="event-occurrence" key={item.id}>
          <h3>{formatDate(item.received_at)}</h3>
          {item.rule_name && item.rule_version && <p>{t('events.ruleVersion', { name: item.rule_name, version: formatNumber(item.rule_version) })}</p>}
          <details><summary>{t('events.receiptContent')}</summary><h4>{item.snapshot.subject || t('events.noSubject')}</h4><pre className="event-body">{item.snapshot.body || t('events.emptyBody')}</pre></details>
          {item.limited && item.raw_available && <p className="notice" role="status">{t('events.limited')}</p>}
          {isMail && item.raw_available && <dl><dt>{t('events.envelopeSender')}</dt><dd>{item.envelope_sender || t('events.emptySender')}</dd><dt>{t('events.recipients')}</dt><dd>{item.recipients.join(t('common.listSeparator'))}</dd></dl>}
          {item.raw_available ? <button className="secondary" disabled={downloading} onClick={() => void download(`/api/v1/events/${data.id}/raw/${item.raw_message_id}`, t(isMail ? 'events.mailFilename' : 'events.rawFilename'))}>{t('events.downloadRaw')}</button> : <p className="notice">{t('events.rawExpired')}</p>}
          <h3>{t('events.attachments')}</h3>
          {item.attachments.length === 0 ? <p>{t('events.noAttachments')}</p> : <ul>{item.attachments.map((attachment) => <li key={attachment.id}><span>{attachment.filename || t('events.unnamedAttachment')}</span><span className="muted">{t('events.bytes', { count: formatNumber(attachment.size) })}</span><button className="secondary compact" disabled={downloading} onClick={() => void download(`/api/v1/events/${data.id}/attachments/${attachment.id}`, t('events.attachmentFilename'))}>{t('events.downloadAttachment')}</button></li>)}</ul>}
        </article>)}
        <Pagination offset={offset} total={data.occurrence_count} onChange={setOffset} />
      </section>
    </>}
  </>;
}
