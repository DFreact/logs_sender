import { useEffect, useState, type FormEvent } from 'react';
import { getConnections, getIngestKeys, createIngestKey, revokeIngestKey, keyStatus, type ConnectionInfo, type IngestKey } from '../../api/connections';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { useI18n } from '../../i18n';
import { useAuth } from '../auth/AuthContext';

export function ConnectionsPage() {
  const { t, formatDate, formatNumber } = useI18n(), auth = useAuth();
  const [info, setInfo] = useState<ConnectionInfo>(), [keys, setKeys] = useState<IngestKey[]>();
  const [offset, setOffset] = useState(0), [more, setMore] = useState(false), [revision, setRevision] = useState(0);
  const [name, setName] = useState(''), [issued, setIssued] = useState<{ key: IngestKey; token: string }>();
  const [revoke, setRevoke] = useState<IngestKey>(), [busy, setBusy] = useState(false), [error, setError] = useState<SafeError>();
  useEffect(() => {
    const c = new AbortController(); setKeys(undefined);
    Promise.all([getConnections(c.signal), getIngestKeys(offset, c.signal)]).then(([connection, page]) => {
      if (!c.signal.aborted) { setInfo(connection); setKeys(page.items); setMore(page.has_more); }
    }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); });
    return () => c.abort();
  }, [offset, revision]);
  async function create(event: FormEvent) {
    event.preventDefault(); if (busy || issued || !auth.identity) return;
    if (!name.trim()) { setError({ key: 'validation.required' }); return; }
    setBusy(true); setError(undefined);
    try { setIssued(await createIngestKey(name, auth.identity.csrf_token)); setName(''); setOffset(0); setRevision((r) => r + 1); }
    catch (e) { setError(safeFailure(e)); } finally { setBusy(false); }
  }
  async function confirmRevoke() {
    if (busy || !revoke || !auth.identity) return;
    setBusy(true); setError(undefined);
    try { await revokeIngestKey(revoke.id, auth.identity.csrf_token); if (issued?.key.id === revoke.id) setIssued(undefined); setRevoke(undefined); setRevision((r) => r + 1); }
    catch (e) { setError(safeFailure(e)); } finally { setBusy(false); }
  }
  return <>
    <div className="section-heading"><h1>{t('connections.title')}</h1><button className="secondary" disabled={busy} onClick={() => { setError(undefined); setRevision((r) => r + 1); }}>{t('common.refresh')}</button></div>
    <p className="muted">{t('connections.hint')}</p><ErrorNotice error={error} />
    {!info && !error && <p role="status">{t('common.loading')}</p>}
    {info && <>
      <section className="card"><h2>{t('connections.rest')}</h2>
        <Field id="ingest-url" labelKey="connections.url" hint="connections.urlHint"><input id="ingest-url" readOnly value={new URL(info.rest_path, window.location.origin).href} /></Field>
        <p>{t('connections.restInstructions')}</p><p className="field-hint">{t('connections.limits', { bytes: formatNumber(info.max_bytes), count: formatNumber(info.per_minute) })}</p>
        <pre className="connection-example">{t('connections.example')}</pre>
        <p>{t('connections.receipt')}</p>
      </section>
      <section className="card"><h2>{t('connections.smtp')}</h2><p>{t('connections.smtpHint')}</p>
        {info.smtp.host ? <div className="config-columns"><Field id="ingest-smtp-host" labelKey="channels.host"><input id="ingest-smtp-host" readOnly value={info.smtp.host} /></Field><Field id="ingest-smtp-port" labelKey="channels.port"><input id="ingest-smtp-port" readOnly value={info.smtp.port} /></Field></div> : <p className="notice warning">{t('connections.smtpUnpublished')}</p>}
        <Field id="ingest-smtp-recipients" labelKey="connections.recipient" hint="connections.recipientHint"><textarea id="ingest-smtp-recipients" readOnly rows={2} value={info.smtp.recipients.join('\n')} /></Field>
        <p>{t(info.smtp.tls_required ? 'connections.smtpTls' : 'connections.smtpLocal')}</p>
        <p className="field-hint">{t('connections.smtpAuth')}</p><p className="field-hint">{t('connections.smtpDeployment')}</p>
      </section>
    </>}
    <section className="card"><h2>{t('connections.keys')}</h2><p>{t('connections.keysHint')}</p>
      <p className="notice warning">{t('connections.scopeHint')}</p>
      <form noValidate onSubmit={(e) => void create(e)}><fieldset disabled={busy || Boolean(issued)}>
        <Field id="ingest-key-name" labelKey="connections.keyName" hint="connections.keyNameHint"><input id="ingest-key-name" maxLength={120} value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <button type="submit">{t('connections.createKey')}</button>
      </fieldset></form>
      {issued && <section className="notice" aria-labelledby="issued-key-title"><h3 id="issued-key-title">{t('connections.created')}</h3><p>{t('connections.once')}</p>
        <Field id="ingest-key-value" labelKey="connections.keyValue"><input id="ingest-key-value" readOnly autoComplete="off" spellCheck={false} value={issued.token} /></Field>
        <button className="secondary" onClick={() => setIssued(undefined)}>{t('connections.hideKey')}</button>
      </section>}
      <p className="field-hint">{t('connections.rotation')}</p>
      {revoke && <section className="notice warning" aria-labelledby="revoke-key-title"><h3 id="revoke-key-title">{t('connections.revokeTitle', { name: revoke.name })}</h3><p>{t('connections.revokeHint')}</p><div className="form-actions"><button disabled={busy} onClick={() => void confirmRevoke()}>{t('connections.revoke')}</button><button className="secondary" disabled={busy} onClick={() => setRevoke(undefined)}>{t('common.cancel')}</button></div></section>}
      {!keys && !error && <p role="status">{t('common.loading')}</p>}
      {keys && (keys.length ? <div className="table-scroll" tabIndex={0} role="region" aria-label={t('connections.keys')}><table><thead><tr><th>{t('config.name')}</th><th>{t('users.status')}</th><th>{t('connections.expires')}</th><th>{t('users.actions')}</th></tr></thead><tbody>{keys.map((key) => <tr key={key.id}><td>{key.name}</td><td>{t(keyStatus[key.status])}</td><td>{formatDate(key.expires_at)}</td><td>{key.status === 'ACTIVE' && <button className="secondary compact" disabled={busy} onClick={() => setRevoke(key)} aria-label={t('connections.revokeNamed', { name: key.name })}>{t('connections.revoke')}</button>}</td></tr>)}</tbody></table></div> : <p>{t('connections.empty')}</p>)}
      <div className="form-actions"><button className="secondary" disabled={busy || !keys || offset === 0} onClick={() => setOffset((v) => Math.max(0, v - 25))}>{t('common.previousPage')}</button><button className="secondary" disabled={busy || !keys || !more} onClick={() => setOffset((v) => v + 25)}>{t('common.nextPage')}</button></div>
    </section>
    <section className="card"><h2>{t('connections.next')}</h2><p>{t('connections.identification')}</p><div className="form-actions"><a href="#sources">{t('navigation.sources')}</a><a href="#identification">{t('rules.identification')}</a><a href="#routing">{t('routing.title')}</a><a href="#events">{t('navigation.events')}</a></div></section>
  </>;
}
