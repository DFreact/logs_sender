import { useEffect, useState, type FormEvent } from 'react';
import { useI18n } from '../../i18n';
import { ErrorNotice, Field } from '../../components/Forms';
import { getSources, getPolicy, saveSource, savePolicy, defaultPolicy, type Policy, type Source } from '../../api/configuration';
import { safeFailure, type SafeError } from '../../api/client';
import { useAuth } from '../auth/AuthContext';
import { RetentionForm } from '../settings/RetentionForm';
import { PolicyFields } from './Editors';

export function SourcesPage() {
  const { t } = useI18n(); const auth = useAuth();
  const [rows, setRows] = useState<Source[]>(); const [policy, setPolicy] = useState<Policy>();
  const [editing, setEditing] = useState<Source>(); const [global, setGlobal] = useState(false);
  const [error, setError] = useState<SafeError>(); const [pending, setPending] = useState(false);
  const [revision, setRevision] = useState(0); const [saved, setSaved] = useState(false);
  useEffect(() => { const control = new AbortController(); setError(undefined);
    Promise.all([getSources(control.signal), getPolicy(control.signal)]).then(([sources, p]) => { if (!control.signal.aborted) { setRows(sources.items); setPolicy(p); } }).catch((e) => { if (!control.signal.aborted) setError(safeFailure(e)); }); return () => control.abort();
  }, [revision]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!auth.identity || pending) return;
    const value = editing?.dedup ?? policy;
    if (!value || (editing && !editing.name.trim()) || !value.fields.length || !Number.isInteger(value.window_seconds / 60) || value.window_seconds < 60 || value.window_seconds > 86400) { setError({ key: 'config.invalid' }); return; }
    setPending(true); setError(undefined);
    try { if (editing) await saveSource(editing, auth.identity.csrf_token); else await savePolicy(value, auth.identity.csrf_token);
      setEditing(undefined); setGlobal(false); setSaved(true); setRevision(revision + 1);
    } catch (e) { setError(safeFailure(e)); } finally { setPending(false); }
  }
  return <>
    <div className="section-heading"><h1>{t('navigation.sources')}</h1><div className="form-actions"><button onClick={() => { setEditing({ id: '', name: '', description: '', enabled: true, version: 1, dedup: defaultPolicy(true) }); setGlobal(false); setSaved(false); setError(undefined); }}>{t('config.createSource')}</button><button className="secondary" disabled={!policy} onClick={() => { setEditing(undefined); setGlobal(true); setSaved(false); }}>{t('config.globalPolicy')}</button></div></div>
    <p className="muted">{t('config.sourcesHint')}</p><p><a href="#connections">{t('connections.title')}</a></p><ErrorNotice error={error} />{saved && <p role="status" className="notice">{t('config.saved')}</p>}
    {(editing || global) && policy && <form noValidate className="card" onSubmit={(e) => void submit(e)}><h2>{t(editing ? editing.id ? 'config.editSource' : 'config.createSource' : 'config.globalPolicy')}</h2><fieldset disabled={pending}>
      {editing && <><Field id="source-name" labelKey="config.name"><input id="source-name" maxLength={120} value={editing.name} onChange={(e) => setEditing({ ...editing, name: e.target.value })} /></Field><Field id="source-description" labelKey="config.description"><input id="source-description" maxLength={2000} value={editing.description} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></Field><label className="checkbox-field"><input type="checkbox" checked={editing.enabled} onChange={(e) => setEditing({ ...editing, enabled: e.target.checked })} />{t('config.sourceEnabled')}</label></>}
      <PolicyFields value={editing?.dedup ?? policy} allowInherit={!!editing} change={(v) => editing ? setEditing({ ...editing, dedup: v }) : setPolicy(v)} />
      <div className="form-actions"><button type="submit">{t('common.save')}</button><button type="button" className="secondary" onClick={() => { setEditing(undefined); setGlobal(false); setError(undefined); setRevision(revision + 1); }}>{t('common.cancel')}</button></div>
    </fieldset></form>}
    {editing?.id && <RetentionForm key={editing.id} sourceId={editing.id} />}
    {!rows && !error && <p role="status">{t('common.loading')}</p>}
    {rows && <div className="card table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}>{rows.length === 0 ? <p>{t('config.noSources')}</p> : <table><thead><tr><th>{t('config.name')}</th><th>{t('users.status')}</th><th>{t('config.dedup')}</th><th>{t('users.actions')}</th></tr></thead><tbody>{rows.map((row) => <tr key={row.id}><td>{row.name}</td><td>{t(row.enabled ? 'config.enabled' : 'config.disabled')}</td><td>{t(row.dedup.inherit ? 'config.inherited' : row.dedup.enabled ? 'config.enabled' : 'config.disabled')}</td><td><button className="secondary compact" aria-label={t('config.editNamed', { name: row.name })} onClick={() => { setEditing(row); setGlobal(false); setSaved(false); setError(undefined); }}>{t('common.edit')}</button></td></tr>)}</tbody></table>}</div>}
  </>;
}
