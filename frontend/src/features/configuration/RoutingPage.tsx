import { getDigests, type Digest } from '../../api/digests';
import { getPolicies, type Policy } from '../../api/escalations';
import { useEffect, useState, type FormEvent } from 'react';
import { getChannels, type Channel } from '../../api/channels';
import { getSources, newCondition, type Source } from '../../api/configuration';
import { getRouting, saveRouting, getRoutingVersions, newAction, type RoutingRule, type RoutingVersion } from '../../api/routing';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n } from '../../i18n';
import { ErrorNotice, Pagination } from '../../components/Forms';
import { useAuth } from '../auth/AuthContext';
import { RoutingFields } from './RoutingFields';

function History({ rule, sources, policies, channels, digests, close }: { rule: RoutingRule; sources: Source[]; policies: Policy[]; channels: Channel[]; digests: Digest[]; close: () => void }) {
  const { t, formatDate, formatNumber } = useI18n(); const [rows, setRows] = useState<RoutingVersion[]>();
  const [offset, setOffset] = useState(0), [total, setTotal] = useState(0), [error, setError] = useState<SafeError>();
  useEffect(() => { const c = new AbortController(); setRows(undefined); setError(undefined); getRoutingVersions(rule.id, offset, c.signal).then((v) => { if (!c.signal.aborted) { setRows(v.items); setTotal(v.total); } }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }); return () => c.abort(); }, [rule.id, offset]);
  return <section className="card"><div className="section-heading"><h2>{t('config.historyNamed', { name: rule.name })}</h2><button className="secondary" onClick={close}>{t('common.close')}</button></div><ErrorNotice error={error} />{!rows && !error && <p role="status">{t('common.loading')}</p>}{rows?.map((v) => <details className="rule-version" key={v.version}><summary>{t('config.versionSummary', { version: formatNumber(v.version), author: v.author, date: formatDate(v.created_at) })}</summary><RoutingFields digests={digests} policies={policies} channels={channels} value={v.snapshot} change={() => {}} sources={sources} readOnly /></details>)}<Pagination offset={offset} total={total} onChange={setOffset} /></section>;
}

export function RoutingPage() {
  const { t, formatNumber } = useI18n(); const auth = useAuth();
  const [digests, setDigests] = useState<Digest[]>([]);
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rows, setRows] = useState<RoutingRule[]>(), [sources, setSources] = useState<Source[]>([]), [draft, setDraft] = useState<RoutingRule>(), [history, setHistory] = useState<RoutingRule>();
  const [error, setError] = useState<SafeError>(), [pending, setPending] = useState(false), [saved, setSaved] = useState(false), [revision, setRevision] = useState(0);
  useEffect(() => { const c = new AbortController(); setError(undefined); Promise.all([getRouting(c.signal), getSources(c.signal), getChannels(c.signal), getPolicies(c.signal), getDigests(c.signal)]).then(([r, s, ch, p, d]) => { if (!c.signal.aborted) { setRows(r.items); setSources(s.items); setChannels(ch.items); setPolicies(p.items); setDigests(d.items); } }).catch((e) => { if (!c.signal.aborted) setError(safeFailure(e)); }); return () => c.abort(); }, [revision]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!draft || !auth.identity || pending) return;
    if (!draft.name.trim() || !Number.isInteger(draft.priority) || draft.priority < 0 || draft.priority > 100000) { setError({ key: 'config.invalidRule' }); return; }
    setPending(true); setError(undefined);
    try { await saveRouting({ ...draft, actions: draft.actions.map((a) => a.fields ? { ...a, fields: { ...a.fields, tags: a.fields.tags.map((v) => v.trim()).filter(Boolean) } } : a) }, auth.identity.csrf_token); setDraft(undefined); setSaved(true); setRevision((v) => v + 1); } catch (e) { setError(safeFailure(e)); } finally { setPending(false); }
  }
  return <>
    <div className="section-heading"><h1>{t('routing.title')}</h1><div className="form-actions"><a href="#simulation">{t('routing.test')}</a><button onClick={() => { setDraft({ id: '', name: '', description: '', priority: 100, enabled: true, version: 1, conditions: { group: 'AND', children: [newCondition()] }, actions: [newAction('SET_FIELDS')] }); setHistory(undefined); setError(undefined); setSaved(false); }}>{t('config.createRule')}</button></div></div>
    <p className="muted">{t('routing.hint')}</p><ErrorNotice error={error} />{saved && <p role="status" className="notice">{t('config.saved')}</p>}
    {draft && <form noValidate className="card" onSubmit={(e) => void submit(e)}><h2>{t(draft.id ? 'config.editRule' : 'config.createRule')}</h2><fieldset disabled={pending}><RoutingFields digests={digests} policies={policies} value={draft} change={setDraft} sources={sources} channels={channels} /><div className="form-actions"><button type="submit">{t('common.save')}</button><button type="button" className="secondary" onClick={() => { setDraft(undefined); setError(undefined); }}>{t('common.cancel')}</button></div></fieldset></form>}
    {history && <History digests={digests} policies={policies} channels={channels} key={history.id} rule={history} sources={sources} close={() => setHistory(undefined)} />}
    {!rows && !error && <p role="status">{t('common.loading')}</p>}
    {rows && <div className="card table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}>{rows.length === 0 ? <p>{t('routing.noRules')}</p> : <table><thead><tr><th>{t('config.name')}</th><th>{t('config.priority')}</th><th>{t('users.status')}</th><th>{t('users.actions')}</th></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td>{r.name}</td><td>{formatNumber(r.priority)}</td><td>{t(r.enabled ? 'config.enabled' : 'config.disabled')}</td><td><div className="form-actions"><button className="secondary compact" aria-label={t('config.editNamed', { name: r.name })} onClick={() => { setDraft(r); setHistory(undefined); setError(undefined); setSaved(false); }}>{t('common.edit')}</button><button className="secondary compact" aria-label={t('config.historyNamed', { name: r.name })} onClick={() => { setHistory(r); setDraft(undefined); }}>{t('config.history')}</button></div></td></tr>)}</tbody></table>}</div>}
  </>;
}
