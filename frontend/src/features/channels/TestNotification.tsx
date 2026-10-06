import { useState, type FormEvent } from 'react';
import type { Channel } from '../../api/channels';
import { testNotification, type TestOperation } from '../../api/notifications';
import { safeFailure, type SafeError } from '../../api/client';
import { useI18n } from '../../i18n';
import { Field, ErrorNotice } from '../../components/Forms';
import { useAuth } from '../auth/AuthContext';
export function TestNotification({ channel, close }: { channel: Channel; close: () => void }) {
  const { t } = useI18n(); const auth = useAuth();
  const [subject, setSubject] = useState(t('delivery.test')), [body, setBody] = useState(t('delivery.testBody')), [pending, setPending] = useState(false), [error, setError] = useState<SafeError>(), [operation, setOperation] = useState<TestOperation>();
  async function submit(event: FormEvent) { event.preventDefault(); if (!auth.identity || pending) return; const request = operation ?? { operation_key: crypto.randomUUID(), channel_version: channel.version, event: { subject, body } }; setOperation(request); setPending(true); setError(undefined); try { const row = await testNotification(channel.id, request, auth.identity.csrf_token); location.hash = `notifications/${row.id}`; } catch (e) { setError(safeFailure(e)); } finally { setPending(false); } }
  return <form noValidate className="card" aria-label={t('delivery.test')} onSubmit={(e) => void submit(e)}><h2>{t('delivery.test')}</h2><p>{t('delivery.testWarning', { name: channel.name })}</p><p className="field-hint">{t('delivery.testRetryHint')}</p><ErrorNotice error={error} /><fieldset disabled={pending || Boolean(operation)}><Field id="test-notification-subject" labelKey="events.subject"><input id="test-notification-subject" maxLength={1000} value={subject} onChange={(e) => setSubject(e.target.value)} /></Field><Field id="test-notification-body" labelKey="templates.eventBody"><textarea id="test-notification-body" maxLength={4096} rows={4} value={body} onChange={(e) => setBody(e.target.value)} /></Field></fieldset><div className="form-actions"><button type="submit" disabled={pending}>{t('delivery.sendTest')}</button><button type="button" className="secondary" disabled={pending} onClick={close}>{t('common.close')}</button></div></form>;
}
