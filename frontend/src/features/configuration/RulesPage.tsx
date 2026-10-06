import { useEffect, useId, useState, type FormEvent } from 'react';
import { useI18n } from '../../i18n';
import { ErrorNotice, Field, Pagination } from '../../components/Forms';
import { getSources, getRules, getVersions, saveRule, newCondition, type Rule, type Source, type Version } from '../../api/configuration';
import { safeFailure, type SafeError } from '../../api/client';
import { SeverityValues } from '../../api/generated';
import { severities } from '../../i18n/mappings';
import { useAuth } from '../auth/AuthContext';
import { ConditionEditor } from './Editors';

function RuleFields({ value, change, sources, readOnly = false }: { value: Rule; change: (v: Rule) => void; sources: Source[]; readOnly?: boolean }) {
  const { t } = useI18n(); const id = useId();
  return <fieldset disabled={readOnly} className="rule-fields">
    <div className="config-columns"><Field id={`${id}-name`} labelKey="config.name"><input id={`${id}-name`} maxLength={120} value={value.name} onChange={(e) => change({ ...value, name: e.target.value })} /></Field>
    <Field id={`${id}-source`} labelKey="events.source"><select id={`${id}-source`} value={value.source_id} onChange={(e) => change({ ...value, source_id: e.target.value })}><option value="">{t('config.chooseSource')}</option>{sources.map((s) => <option key={s.id} value={s.id}>{s.enabled ? s.name : t('config.disabledNamed', { name: s.name })}</option>)}</select></Field>
    <Field id={`${id}-priority`} labelKey="config.priority" hint="config.priorityHint"><input id={`${id}-priority`} type="number" min={0} max={100000} value={value.priority} onChange={(e) => change({ ...value, priority: Number(e.target.value) })} /></Field></div>
    <label className="checkbox-field"><input type="checkbox" checked={value.enabled} onChange={(e) => change({ ...value, enabled: e.target.checked })} />{t('config.ruleEnabled')}</label>
    <p className="field-hint">{t('config.conditionHint')}</p><ConditionEditor value={value.conditions} change={(conditions) => change({ ...value, conditions })} readOnly={readOnly} />
    <h3>{t('config.assign')}</h3><div className="config-columns"><Field id={`${id}-severity`} labelKey="events.severityLabel"><select id={`${id}-severity`} value={value.assignments.severity ?? ''} onChange={(e) => change({ ...value, assignments: { ...value.assignments, severity: e.target.value || null } })}><option value="">{t('config.keepSeverity')}</option>{SeverityValues.map((v) => <option key={v} value={v}>{t(severities[v])}</option>)}</select></Field>
    <Field id={`${id}-category`} labelKey="config.category"><input id={`${id}-category`} maxLength={120} value={value.assignments.category} onChange={(e) => change({ ...value, assignments: { ...value.assignments, category: e.target.value } })} /></Field>
    <Field id={`${id}-type`} labelKey="config.eventType"><input id={`${id}-type`} maxLength={120} value={value.assignments.event_type} onChange={(e) => change({ ...value, assignments: { ...value.assignments, event_type: e.target.value } })} /></Field></div>
    <Field id={`${id}-tags`} labelKey="config.tags" hint="config.tagsHint"><input id={`${id}-tags`} value={value.assignments.tags.join(',')} onChange={(e) => change({ ...value, assignments: { ...value.assignments, tags: e.target.value ? e.target.value.split(',') : [] } })} /></Field>
  </fieldset>;
}

function History({ rule, sources, close }: { rule: Rule; sources: Source[]; close: () => void }) {
  const { t, formatDate, formatNumber } = useI18n();
  const [rows, setRows] = useState<Version[]>(); const [total, setTotal] = useState(0); const [offset, setOffset] = useState(0); const [error, setError] = useState<SafeError>();
  useEffect(() => { const control = new AbortController(); setRows(undefined); setError(undefined); getVersions(rule.id, offset, control.signal).then((page) => { if (!control.signal.aborted) { setRows(page.items); setTotal(page.total); } }).catch((e) => { if (!control.signal.aborted) setError(safeFailure(e)); }); return () => control.abort(); }, [rule.id, offset]);
  return <section className="card"><div className="section-heading"><h2>{t('config.historyNamed', { name: rule.name })}</h2><button className="secondary" onClick={close}>{t('common.close')}</button></div><ErrorNotice error={error} />{!rows && !error && <p role="status">{t('common.loading')}</p>}
    {rows?.map((v) => <details key={v.version} className="rule-version"><summary>{t('config.versionSummary', { version: formatNumber(v.version), author: v.author, date: formatDate(v.created_at) })}</summary><RuleFields value={{ ...v.snapshot, id: rule.id }} change={() => {}} sources={sources} readOnly /></details>)}
    <Pagination offset={offset} total={total} onChange={setOffset} />
  </section>;
}

export function RulesPage() {
  const { t, formatNumber } = useI18n(); const auth = useAuth();
  const [sources, setSources] = useState<Source[]>([]); const [rows, setRows] = useState<Rule[]>(); const [draft, setDraft] = useState<Rule>(); const [history, setHistory] = useState<Rule>();
  const [revision, setRevision] = useState(0); const [error, setError] = useState<SafeError>(); const [pending, setPending] = useState(false); const [saved, setSaved] = useState(false);
  useEffect(() => { const control = new AbortController(); setError(undefined); Promise.all([getSources(control.signal), getRules(control.signal)]).then(([s, r]) => { if (!control.signal.aborted) { setSources(s.items); setRows(r.items); } }).catch((e) => { if (!control.signal.aborted) setError(safeFailure(e)); }); return () => control.abort(); }, [revision]);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!draft || !auth.identity || pending) return;
    if (!draft.name.trim() || !draft.source_id || !Number.isInteger(draft.priority) || draft.priority < 0 || draft.priority > 100000) { setError({ key: 'config.invalidRule' }); return; }
    setPending(true); setError(undefined);
    try { await saveRule({ ...draft, assignments: { ...draft.assignments, tags: draft.assignments.tags.map((tag) => tag.trim()).filter(Boolean) } }, auth.identity.csrf_token); setDraft(undefined); setSaved(true); setRevision(revision + 1); }
    catch (e) { setError(safeFailure(e)); } finally { setPending(false); }
  }
  return <>
    <div className="section-heading"><h1>{t('rules.identification')}</h1><button disabled={!sources.length} onClick={() => { setDraft({ id: '', name: '', source_id: sources[0]?.id ?? '', priority: 100, enabled: true, version: 1, conditions: { group: 'AND', children: [newCondition()] }, assignments: { severity: null, category: '', event_type: '', tags: [] } }); setHistory(undefined); setSaved(false); setError(undefined); }}>{t('config.createRule')}</button></div>
    <p className="muted">{t('config.rulesHint')}</p><ErrorNotice error={error} />{saved && <p role="status" className="notice">{t('config.saved')}</p>}
    {rows && !sources.length && <p>{t('config.sourceFirst')}</p>}
    {draft && <form noValidate className="card" onSubmit={(e) => void submit(e)}><h2>{t(draft.id ? 'config.editRule' : 'config.createRule')}</h2><fieldset disabled={pending}><RuleFields value={draft} change={setDraft} sources={sources} /><div className="form-actions"><button type="submit">{t('common.save')}</button><button type="button" className="secondary" onClick={() => { setDraft(undefined); setError(undefined); }}>{t('common.cancel')}</button></div></fieldset></form>}
    {history && <History key={history.id} rule={history} sources={sources} close={() => setHistory(undefined)} />}
    {!rows && !error && <p role="status">{t('common.loading')}</p>}
    {rows && <div className="card table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}>{!rows.length ? <p>{t('config.noRules')}</p> : <table><thead><tr><th>{t('config.name')}</th><th>{t('config.priority')}</th><th>{t('events.source')}</th><th>{t('users.status')}</th><th>{t('users.actions')}</th></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td>{r.name}</td><td>{formatNumber(r.priority)}</td><td>{sources.find((s) => s.id === r.source_id)?.name ?? t('sources.unknown')}</td><td>{t(r.enabled ? 'config.enabled' : 'config.disabled')}</td><td><div className="form-actions"><button className="secondary compact" aria-label={t('config.editNamed', { name: r.name })} onClick={() => { setDraft(r); setHistory(undefined); setSaved(false); setError(undefined); }}>{t('common.edit')}</button><button className="secondary compact" aria-label={t('config.historyNamed', { name: r.name })} onClick={() => { setHistory(r); setDraft(undefined); }}>{t('config.history')}</button></div></td></tr>)}</tbody></table>}</div>}
  </>;
}
