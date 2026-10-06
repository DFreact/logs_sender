import { useEffect, useState, type FormEvent } from 'react';
import { defaultAppearance, modeKeys, paletteKeys, saveAppearance, type Appearance } from '../../api/appearance';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { useI18n } from '../../i18n';
import { useAuth } from '../auth/AuthContext';
import { RetentionForm } from './RetentionForm';
import { useAppearance } from './AppearanceContext';

export function SettingsPage() {
  const { t } = useI18n(), auth = useAuth(), appearance = useAppearance();
  const [draft, setDraft] = useState<Appearance>(), [busy, setBusy] = useState(false), [error, setError] = useState<SafeError>(), [saved, setSaved] = useState(false);
  useEffect(() => { if (!appearance.loading && !appearance.error) setDraft((old) => old ?? appearance.value); }, [appearance.loading, appearance.error, appearance.value]);
  const changed = draft && (draft.palette !== appearance.value.palette || draft.mode !== appearance.value.mode);
  async function submit(event: FormEvent) {
    event.preventDefault(); if (!draft || !auth.identity || busy) return;
    setBusy(true); setError(undefined); setSaved(false);
    try { const result = await saveAppearance(draft, auth.identity.csrf_token); appearance.accept(result); setDraft(result); setSaved(true); }
    catch (err) { setError(safeFailure(err)); } finally { setBusy(false); }
  }
  function change(patch: Partial<Appearance>) { setDraft((old) => old ? { ...old, ...patch } : old); setSaved(false); }
  async function reload() { setDraft(undefined); setError(undefined); setSaved(false); await appearance.refresh(); }
  return <>
    <div className="section-heading"><div><h1>{t('navigation.settings')}</h1><p className="muted">{t('appearance.subtitle')}</p></div><button className="secondary" disabled={busy || appearance.loading} onClick={() => void reload()}>{t('common.refresh')}</button></div>
    <ErrorNotice error={error ?? appearance.error} />
    {saved && <p role="status" className="notice">{t('appearance.saved')}</p>}
    {appearance.loading && <p role="status">{t('common.loading')}</p>}
    {draft && <form noValidate onSubmit={(e) => void submit(e)} className="card appearance-settings">
      <h2>{t('appearance.title')}</h2><p className="muted">{t('appearance.hint')}</p>
      <fieldset disabled={busy}>
        <fieldset className="palette-fieldset"><legend>{t('appearance.palette')}</legend><div className="palette-grid">{Object.entries(paletteKeys).map(([value, key]) => <label className="palette-option" data-palette={value} key={value}><input type="radio" name="palette" value={value} checked={draft.palette === value} onChange={() => change({ palette: value as Appearance['palette'] })} /><span className="palette-swatch" aria-hidden="true" /><span>{t(key)}</span></label>)}</div></fieldset>
        <Field id="appearance-mode" labelKey="appearance.mode"><select id="appearance-mode" value={draft.mode} onChange={(e) => change({ mode: e.target.value as Appearance['mode'] })}>{Object.entries(modeKeys).map(([value, key]) => <option key={value} value={value}>{t(key)}</option>)}</select></Field>
        <section className="appearance-preview" data-palette={draft.palette} data-mode={draft.mode} aria-labelledby="appearance-preview-title">
          <h3 id="appearance-preview-title">{t('appearance.preview')}</h3><p className="field-hint">{t('appearance.previewHint')}</p>
          <Field id="appearance-example" labelKey="events.source"><input id="appearance-example" value={t('appearance.previewValue')} readOnly /></Field>
          <div className="preview-actions"><span className="button-sample">{t('common.save')}</span><span className="button-sample secondary">{t('common.cancel')}</span><span className="badge positive">{t('notifications.status.SENT')}</span></div>
          <p className="notice">{t('appearance.previewNotice')}</p>
        </section>
        <div className="form-actions"><button type="submit" disabled={!changed}>{t(busy ? 'common.saving' : 'appearance.save')}</button><button type="button" className="secondary" onClick={() => change({ palette: defaultAppearance.palette, mode: defaultAppearance.mode })}>{t('appearance.reset')}</button></div>
        <p className="field-hint" role="status">{t(changed ? 'appearance.unsaved' : 'appearance.current')}</p>
      </fieldset>
    </form>}
    <RetentionForm />
  </>;
}
