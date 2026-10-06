import { useEffect, useRef, useState } from 'react';
import { ApiError, getLiveness, type SafeError } from '../api/client';
import { useI18n } from '../i18n';

export function ConnectionCard() {
  const { t, formatDate, formatTimeZone } = useI18n();
  const [checking, setChecking] = useState(true);
  const [checkedAt, setCheckedAt] = useState<string>();
  const [error, setError] = useState<SafeError>();
  const active = useRef<AbortController | null>(null);

  async function check() {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    setChecking(true);
    setError(undefined);
    try {
      const result = await getLiveness(controller.signal);
      if (!controller.signal.aborted) setCheckedAt(result.checked_at);
    } catch (err) {
      if (!controller.signal.aborted) {
        setError(err instanceof ApiError ? err.safe : { key: 'errors.internalWithoutId' });
      }
    } finally {
      if (!controller.signal.aborted) setChecking(false);
    }
  }

  useEffect(() => { void check(); return () => active.current?.abort(); }, []);

  return <section className="card connection-card" aria-labelledby="connection-title">
    <div className="card-heading">
      <h2 id="connection-title">{t('health.connectionTitle')}</h2>
      <span className={`status-dot ${checking ? 'pending' : error ? 'failed' : 'available'}`} aria-hidden="true" />
    </div>
    <p className="muted">{t('health.connectionHint')}</p>
    <div className="connection-result" role="status" aria-live="polite" aria-atomic="true">
      <strong>{t(checking ? 'health.checking' : error ? 'health.unavailable' : 'health.available')}</strong>
      {!checking && error && <div className="error-message">
        <p>{t(error.key)}</p>
        {error.requestId && <p className="request-id">{t('common.requestId', { id: error.requestId })}</p>}
      </div>}
      {checkedAt && <p className="timestamp">{t('health.checkedAt', { date: formatDate(checkedAt) })}</p>}
    </div>
    <div className="card-footer">
      <button type="button" onClick={() => void check()} disabled={checking}>{t('health.check')}</button>
      <span className="muted timezone">{t('health.timeZone', { zone: formatTimeZone(checkedAt || new Date()) })}</span>
    </div>
  </section>;
}
