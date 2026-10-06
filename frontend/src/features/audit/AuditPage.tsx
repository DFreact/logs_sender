import { paletteKeys, modeKeys } from '../../api/appearance';
import { useEffect, useState } from 'react';
import { getAudit, type AuditItem, type Page } from '../../api/identity';
import { safeFailure, type SafeError } from '../../api/client';
import { AuditActionValues } from '../../api/generated';
import { auditActions, userRoles, formFields } from '../../i18n/mappings';
import { labelKey, useI18n } from '../../i18n';
import { ErrorNotice, Pagination } from '../../components/Forms';

function Changes({ item }: { item: AuditItem }) {
  const { t } = useI18n();
  const fields = ['username', 'display_name', 'role', 'active', 'password_changed', 'appearance_palette', 'appearance_mode', 'retention_enabled', 'retention_inherit', 'event_days', 'raw_days', 'notification_days', 'attempt_days', 'audit_days', 'task_days', 'history_days', 'health_days'] as const;
  const changed = fields.filter((key) => Object.hasOwn(item.after, key) && item.after[key] !== item.before[key]);
  function value(key: string, data: unknown) {
    if (key === 'appearance_palette') return t(labelKey(paletteKeys, data, 'common.unknownValue'));
    if (key === 'appearance_mode') return t(labelKey(modeKeys, data, 'common.unknownValue'));
    if (key === 'role') return t(labelKey(userRoles, data, 'common.unknownValue'));
    if (key === 'retention_enabled' || key === 'retention_inherit') return typeof data === 'boolean' ? t(data ? 'config.enabled' : 'config.disabled') : t('common.unknownValue');
    if (key === 'active') return typeof data === 'boolean' ? t(data ? 'users.active' : 'users.inactive') : t('common.unknownValue');
    return typeof data === 'string' ? data : t('common.unknownValue');
  }
  return changed.length ? <ul className="audit-changes">{changed.map((key) => <li key={key}>
    {key === 'password_changed' ? t('audit.passwordChanged') : <>
      <strong>{t(formFields[key])}</strong>
      <div><span className="muted">{t('audit.before')}</span><span>{value(key, item.before[key])}</span></div>
      <div><span className="muted">{t('audit.after')}</span><span>{value(key, item.after[key])}</span></div>
    </>}
  </li>)}</ul> : <span className="muted">{t('audit.noChanges')}</span>;
}

export function AuditPage() {
  const { t, formatDate } = useI18n();
  const [page, setPage] = useState<Page<AuditItem>>();
  const [offset, setOffset] = useState(0);
  const [action, setAction] = useState('');
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<SafeError>();
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(undefined);
    getAudit(offset, action, controller.signal).then((value) => { if (!controller.signal.aborted) setPage(value); })
      .catch((err) => { if (!controller.signal.aborted) { setError(safeFailure(err)); setPage(undefined); } })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [offset, action, revision]);
  return <>
    <div className="section-heading"><div><h1>{t('navigation.audit')}</h1><p className="muted">{t('audit.subtitle')}</p></div>
      <button className="secondary" disabled={loading} onClick={() => setRevision(revision + 1)}>{t('common.refresh')}</button>
    </div>
    <div className="filter-bar"><label htmlFor="audit-action">{t('audit.action')}</label>
      <select id="audit-action" value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }}>
        <option value="">{t('audit.allActions')}</option>
        {AuditActionValues.map((value) => <option key={value} value={value}>{t(auditActions[value])}</option>)}
      </select>
    </div>
    <ErrorNotice error={error} />
    {loading ? <p role="status">{t('common.loading')}</p> : page && <div className="card table-card">
      {page.items.length === 0 ? <p>{t('audit.empty')}</p> : <div className="table-scroll" tabIndex={0} role="region" aria-label={t('common.scrollTable')}><table>
        <thead><tr><th>{t('audit.date')}</th><th>{t('audit.actor')}</th><th>{t('audit.action')}</th><th>{t('audit.target')}</th><th>{t('audit.changes')}</th><th>{t('audit.ip')}</th></tr></thead>
        <tbody>{page.items.map((item) => <tr key={item.id}>
          <td className="date-cell">{formatDate(item.timestamp)}</td><td>{item.actor ?? t(item.action === 'INGEST_KEY_CREATED' || item.action === 'INGEST_KEY_REVOKED' ? 'audit.localConsole' : 'audit.anonymous')}</td>
          <td>{t(labelKey(auditActions, item.action, 'audit.unknownAction'))}</td><td>{item.target ?? t(item.action === 'APPEARANCE_UPDATED' ? 'appearance.title' : item.action === 'RETENTION_UPDATED' ? 'retention.title' : 'audit.anonymous')}</td>
          <td><Changes item={item} /></td><td>{item.request_ip ?? t('common.unknownValue')}</td>
        </tr>)}</tbody>
      </table></div>}
      <Pagination offset={offset} total={page.total} onChange={setOffset} />
    </div>}
  </>;
}
