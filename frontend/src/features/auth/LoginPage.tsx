import { useState, type FormEvent } from 'react';
import { useI18n, type TranslationKey } from '../../i18n';
import { safeFailure, type SafeError } from '../../api/client';
import { ErrorNotice, Field } from '../../components/Forms';
import { useAuth } from './AuthContext';

export function LoginPage() {
  const { t } = useI18n();
  const auth = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<SafeError>();
  const [fields, setFields] = useState<Partial<Record<'username' | 'password', TranslationKey>>>({});
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (pending) return;
    const invalid: typeof fields = {};
    if (!username.trim()) invalid.username = 'validation.required';
    if (!password) invalid.password = 'validation.required';
    setFields(invalid); setError(undefined);
    if (Object.keys(invalid).length) return;
    setPending(true);
    try { await auth.login(username.trim(), password); }
    catch (err) { setError(safeFailure(err)); setPassword(''); }
    finally { setPending(false); }
  }
  return <main className="login-page">
    <a className="brand" href="/">{t('common.appName')}</a>
    <section className="card login-card">
      <h1>{t('auth.title')}</h1><p className="muted">{t('auth.subtitle')}</p>
      {auth.notice && <p role="status" className="notice">{t(auth.notice)}</p>}
      <ErrorNotice error={error || auth.error} />
      <form noValidate onSubmit={(event) => void submit(event)}>
        <Field id="username" labelKey="auth.username" error={fields.username}>
          <input id="username" name="username" autoComplete="username" value={username} maxLength={64}
            aria-invalid={!!fields.username} aria-describedby={fields.username ? 'username-error' : undefined}
            onChange={(event) => setUsername(event.target.value)} required />
        </Field>
        <Field id="password" labelKey="auth.password" error={fields.password}>
          <input id="password" name="password" type="password" autoComplete="current-password" value={password}
            aria-invalid={!!fields.password} aria-describedby={fields.password ? 'password-error' : undefined}
            onChange={(event) => setPassword(event.target.value)} maxLength={128} required />
        </Field>
        <button className="full-width" disabled={pending}>{t(pending ? 'auth.signingIn' : 'auth.signIn')}</button>
      </form>
      <p className="field-hint login-help">{t('auth.accountHelp')}</p>
    </section>
  </main>;
}
