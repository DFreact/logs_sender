import { useEffect, useRef, useState, type FormEvent } from 'react';
import { getRetention, saveRetention, retentionFields, retentionHints, retentionPhases, type Retention, type RetentionField } from '../../api/retention';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { useI18n, labelKey } from '../../i18n';
import { useAuth } from '../auth/AuthContext';

export function RetentionForm({ sourceId }: { sourceId?: string }) {
  const { t, formatDate, formatNumber } = useI18n(), auth = useAuth();
  const [value, setValue] = useState<Retention>(), [initial, setInitial] = useState<Retention>();
  const [error, setError] = useState<SafeError>(), [busy, setBusy] = useState(false), [saved, setSaved] = useState(false);
  const confirmButton = useRef<HTMLButtonElement>(null);
  const [confirm, setConfirm] = useState(false), [revision, setRevision] = useState(0);
  useEffect(() => { if (confirm) confirmButton.current?.focus(); }, [confirm]);
  useEffect(() => {
    const control = new AbortController(); setValue(undefined); setError(undefined); setSaved(false); setConfirm(false);
    getRetention(sourceId, control.signal).then((data) => { if (!control.signal.aborted) { setValue(data); setInitial(data); } }).catch((err) => { if (!control.signal.aborted) setError(safeFailure(err)); });
    return () => control.abort();
  }, [sourceId, revision]);
  function change(patch: Partial<Retention>) { setValue((old) => old ? { ...old, ...patch } : old); setSaved(false); setConfirm(false); }
  const changed = value && initial && JSON.stringify([value.days, value.enabled, value.inherit]) !== JSON.stringify([initial.days, initial.enabled, initial.inherit]);
  async function save() {
    if (!value || !auth.identity || busy) return;
    setBusy(true); setError(undefined);
    try { const result = await saveRetention(value, auth.identity.csrf_token, sourceId); setValue(result); setInitial(result); setSaved(true); setConfirm(false); }
    catch (err) { setError(safeFailure(err)); } finally { setBusy(false); }
  }
  function submit(event: FormEvent) {
    event.preventDefault(); if (!value || !initial) return;
    if (Object.values(value.days).some((days) => !Number.isInteger(days) || days! < 1 || days! > 3650) || value.days.raw_days! > value.days.event_days!) {
      setError({ key: 'retention.invalid' }); return;
    }
    setError(undefined);
    if (value.enabled && (!initial.enabled || sourceId || Object.entries(value.days).some(([key, days]) => days < initial.days[key as RetentionField]!))) setConfirm(true);
    else void save();
  }
  const prefix = `retention-${sourceId ?? 'global'}`;
  const recalculation = value?.progress.filter((row) => row.kind.startsWith('RECALCULATE_'));
  return <section className="card retention-settings">
    <div className="section-heading"><h2>{t('retention.title')}</h2><button className="secondary" disabled={busy} onClick={() => setRevision((old) => old + 1)}>{t('retention.refresh')}</button></div>
    <p className="muted">{t(sourceId ? 'retention.sourceHint' : 'retention.globalHint')}</p>
    <ErrorNotice error={error} />{saved && <p role="status" className="notice">{t('retention.saved')}</p>}
    {!value && !error && <p role="status">{t('common.loading')}</p>}
    {value && <form noValidate onSubmit={submit}><fieldset disabled={busy || confirm}>
      <label className="checkbox-field"><input type="checkbox" checked={sourceId ? value.inherit : value.enabled} onChange={(e) => change(sourceId ? { inherit: e.target.checked } : { enabled: e.target.checked })} />{t(sourceId ? 'retention.inherit' : 'retention.enabled')}</label>
      <p className="field-hint">{t(initial?.enabled ? 'retention.on' : 'retention.off')}</p>
      <div className="retention-fields">{(Object.keys(value.days) as RetentionField[]).map((key) => <Field key={key} id={`${prefix}-${key}`} labelKey={retentionFields[key]} hint={retentionHints[key]}>
        <input id={`${prefix}-${key}`} type="number" inputMode="numeric" min={1} max={3650} step={1} disabled={!!sourceId && value.inherit} value={Number.isNaN(value.days[key]) ? '' : value.days[key]} onChange={(e) => change({ days: { ...value.days, [key]: e.target.value === '' ? NaN : Number(e.target.value) } })} />
      </Field>)}</div>
      <p className="notice warning">{t('retention.safety')}</p>
      {changed && <p className="field-hint">{t('retention.unsaved')}</p>}
      <div className="form-actions"><button type="submit" disabled={!changed}>{t(busy ? 'common.saving' : 'retention.save')}</button></div>
    </fieldset></form>}
    {confirm && <section role="alertdialog" aria-labelledby={`${prefix}-confirm`} className="notice warning"><h3 id={`${prefix}-confirm`}>{t('retention.confirmTitle')}</h3><p>{t('retention.confirmHint')}</p><div className="form-actions"><button ref={confirmButton} disabled={busy} onClick={() => void save()}>{t('retention.apply')}</button><button className="secondary" disabled={busy} onClick={() => setConfirm(false)}>{t('common.cancel')}</button></div></section>}
    {value && <p className="field-hint" role="status">{t(!value.progress.length && value.version === 0 ? 'retention.notSaved' : recalculation?.length === 2 && recalculation.every((row) => row.complete) ? initial?.enabled ? 'retention.recalculated' : 'retention.recalculatedDisabled' : 'retention.recalculating')}</p>}
    {value?.progress.length ? <div className="table-scroll" role="region" tabIndex={0} aria-label={t('retention.progress')}><table><thead><tr><th>{t('retention.phase')}</th><th>{t('retention.processed')}</th><th>{t('retention.held')}</th></tr></thead><tbody>{value.progress.map((row) => <tr key={row.kind}><td>{t(labelKey(retentionPhases, row.kind, 'common.unknownValue'))}</td><td>{formatNumber(row.processed)}</td><td>{formatNumber(row.held)}</td></tr>)}</tbody></table><p className="field-hint">{t('retention.progressHint')}</p></div> : null}
    {value?.progress.length ? <p className="timestamp">{t('health.measuredAt', { date: formatDate(value.progress.reduce((latest, row) => row.checked_at > latest ? row.checked_at : latest, value.progress[0].checked_at)) })}</p> : null}
  </section>;
}
