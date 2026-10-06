import { useEffect, useRef, useState, type FormEvent } from 'react';
import { getUsers, saveUser, type User, type Page } from '../../api/identity';
import { safeFailure, type SafeError } from '../../api/client';
import { UserRoleValues, type UserRole } from '../../api/generated';
import { Field, ErrorNotice, Pagination } from '../../components/Forms';
import { useI18n, labelKey, type TranslationKey } from '../../i18n';
import { userRoles } from '../../i18n/mappings';
import { useAuth } from '../auth/AuthContext';

const hints = {
  ADMINISTRATOR: 'users.roleHint.ADMINISTRATOR', OPERATOR: 'users.roleHint.OPERATOR', VIEWER: 'users.roleHint.VIEWER',
} satisfies Record<UserRole, TranslationKey>;

function UserEditor({ user, close, saved }: { user?: User; close: () => void; saved: (user: User) => void }) {
  const { t } = useI18n();
  const auth = useAuth();
  const dialog = useRef<HTMLDialogElement>(null);
  const [username, setUsername] = useState(user?.username ?? '');
  const [name, setName] = useState(user?.display_name ?? '');
  const [password, setPassword] = useState('');
  const [role, setRole] = useState<UserRole>(user?.role ?? 'VIEWER');
  const [active, setActive] = useState(user?.active ?? true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<SafeError>();
  const [invalid, setInvalid] = useState<TranslationKey>();
  useEffect(() => { dialog.current?.showModal(); }, []);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!auth.identity || pending) return;
    const issue = !user && !/^[a-z0-9][a-z0-9._-]{2,63}$/i.test(username.trim()) ? 'auth.usernameInvalid'
      : !name.trim() || name.trim().length > 120 ? 'users.displayNameInvalid'
      : ((!user || password) && (password.length < 12 || password.length > 128)) ? 'errors.passwordPolicy' : undefined;
    setInvalid(issue); setError(undefined);
    if (issue) return;
    setPending(true);
    try {
      const common = { display_name: name.trim(), role, ...(password ? { password } : {}) };
      const updated = await saveUser(auth.identity.csrf_token, user
        ? { ...common, active, version: user.version }
        : { ...common, username: username.trim() }, user?.id);
      saved(updated);
      if (updated.id === auth.identity.user.id) await auth.refresh();
    } catch (err) { setError(safeFailure(err)); }
    finally { setPassword(''); setPending(false); }
  }
  return <dialog ref={dialog} className="user-dialog" aria-labelledby="editor-title"
    onCancel={(event) => { event.preventDefault(); if (!pending) close(); }}>
    <h2 id="editor-title">{t(user ? 'users.edit' : 'users.create')}</h2>
    <ErrorNotice error={error} />
    {invalid && <p role="alert" className="field-error">{t(invalid)}</p>}
    <form noValidate onSubmit={(event) => void submit(event)}>
      <Field id="edit-username" labelKey="auth.username" hint={!user ? 'auth.usernameHint' : undefined}>
        <input id="edit-username" value={username} onChange={(e) => setUsername(e.target.value)}
          autoComplete="off" readOnly={!!user} maxLength={64} required
          aria-describedby={!user ? 'edit-username-hint' : undefined} />
      </Field>
      <Field id="display-name" labelKey="users.displayName">
        <input id="display-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={120} required />
      </Field>
      <Field id="user-role" labelKey="users.role" hint={hints[role]}>
        <select id="user-role" value={role} onChange={(e) => setRole(e.target.value as UserRole)} aria-describedby="user-role-hint">
          {UserRoleValues.map((value) => <option key={value} value={value}>{t(userRoles[value])}</option>)}
        </select>
      </Field>
      <Field id="user-password" labelKey={user ? 'auth.newPassword' : 'auth.password'} hint={user ? 'users.newPasswordHint' : 'auth.passwordHint'}>
        <input id="user-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password" maxLength={128} required={!user} aria-describedby="user-password-hint" />
      </Field>
      {user && <label className="checkbox-field"><input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} />{t('users.enabled')}</label>}
      {user && <p className="field-hint">{t('users.accessChangeHint')}</p>}
      <div className="form-actions">
        <button type="submit" disabled={pending}>{t(pending ? 'common.saving' : 'common.save')}</button>
        <button type="button" className="secondary" disabled={pending} onClick={close}>{t('common.cancel')}</button>
      </div>
    </form>
  </dialog>;
}

export function UsersPage() {
  const { t } = useI18n();
  const [page, setPage] = useState<Page<User>>();
  const [offset, setOffset] = useState(0);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState<SafeError>();
  const [loading, setLoading] = useState(true);
  const [editor, setEditor] = useState<User | 'new'>();
  const [notice, setNotice] = useState<TranslationKey>();
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError(undefined);
    getUsers(offset, controller.signal).then((value) => { if (!controller.signal.aborted) setPage(value); })
      .catch((err) => { if (!controller.signal.aborted) { setError(safeFailure(err)); setPage(undefined); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [offset, revision]);
  return <>
    <div className="section-heading"><div><h1>{t('navigation.users')}</h1><p className="muted">{t('users.subtitle')}</p></div>
      <div className="form-actions"><button className="secondary" onClick={() => setRevision(revision + 1)} disabled={loading}>{t('common.refresh')}</button>
        <button onClick={() => { setEditor('new'); setNotice(undefined); }}>{t('users.create')}</button></div>
    </div>
    {notice && <p role="status" className="notice">{t(notice)}</p>}
    <ErrorNotice error={error} />
    {loading ? <p role="status">{t('common.loading')}</p> : page && <div className="card table-card">
      {page.items.length === 0 ? <p>{t('users.empty')}</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}><table>
        <thead><tr><th>{t('auth.username')}</th><th>{t('users.displayName')}</th><th>{t('users.role')}</th><th>{t('users.status')}</th><th>{t('users.actions')}</th></tr></thead>
        <tbody>{page.items.map((user) => <tr key={user.id}>
          <td>{user.username}</td><td>{user.display_name}</td><td>{t(labelKey(userRoles, user.role))}</td>
          <td><span className={`badge ${user.active ? 'positive' : 'neutral'}`}>{t(user.active ? 'users.active' : 'users.inactive')}</span></td>
          <td><button className="secondary compact" aria-label={t('users.editNamed', { name: user.username })}
            onClick={() => { setEditor(user); setNotice(undefined); }}>{t('common.edit')}</button></td>
        </tr>)}</tbody>
      </table></div>}
      <Pagination offset={offset} total={page.total} onChange={setOffset} />
    </div>}
    {editor && <UserEditor user={editor === 'new' ? undefined : editor} close={() => setEditor(undefined)} saved={() => {
      setNotice(editor === 'new' ? 'users.created' : 'users.saved'); setEditor(undefined); setRevision(revision + 1);
    }} />}
  </>;
}
