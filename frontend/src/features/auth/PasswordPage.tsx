import { useState, type FormEvent } from 'react';
import { changePassword } from '../../api/identity';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { useI18n, type TranslationKey } from '../../i18n';
import { useAuth } from './AuthContext';

export function PasswordPage() {
  const { t } = useI18n();
  const auth = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [repeat, setRepeat] = useState('');
  const [error, setError] = useState<SafeError>();
  const [pending, setPending] = useState(false);
  const [validation, setValidation] = useState<TranslationKey>();
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending || !auth.identity) return;
    setError(undefined);
    const invalid = !current ? 'validation.required' : next.length < 12 || next.length > 128
      ? 'errors.passwordPolicy' : next !== repeat ? 'auth.passwordMismatch' : undefined;
    setValidation(invalid);
    if (invalid) return;
    setPending(true);
    try { await changePassword(auth.identity.csrf_token, current, next); auth.clear('auth.passwordChanged'); }
    catch (err) { setError(safeFailure(err)); }
    finally { setPending(false); setCurrent(''); setNext(''); setRepeat(''); }
  }
  return <section className="card editor-card">
    <h1>{t('auth.changePassword')}</h1><p className="muted">{t('auth.passwordChangeHint')}</p>
    <ErrorNotice error={error} />
    {validation && <p className="field-error" role="alert">{t(validation)}</p>}
    <form noValidate onSubmit={(event) => void submit(event)}>
      <Field id="current-password" labelKey="auth.currentPassword">
        <input id="current-password" type="password" autoComplete="current-password" maxLength={128} value={current} onChange={(e) => setCurrent(e.target.value)} required />
      </Field>
      <Field id="new-password" labelKey="auth.newPassword" hint="auth.passwordHint">
        <input id="new-password" type="password" autoComplete="new-password" aria-describedby="new-password-hint" maxLength={128} value={next} onChange={(e) => setNext(e.target.value)} required />
      </Field>
      <Field id="repeat-password" labelKey="auth.repeatPassword">
        <input id="repeat-password" type="password" autoComplete="new-password" maxLength={128} value={repeat} onChange={(e) => setRepeat(e.target.value)} required />
      </Field>
      <button disabled={pending}>{t(pending ? 'common.saving' : 'auth.changePassword')}</button>
    </form>
  </section>;
}
