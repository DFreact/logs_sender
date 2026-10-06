import { Children, cloneElement, isValidElement, type ReactNode, type HTMLAttributes } from 'react';
import { useI18n, type TranslationKey } from '../i18n';
import type { SafeError } from '../api/client';
import { formFields } from '../i18n/mappings';

export function ErrorNotice({ error }: { error?: SafeError }) {
  const { t } = useI18n();
  if (!error) return null;
  return <div className="form-error" role="alert">
    <p>{t(error.key)}</p>
    {error.fields && <ul>{Object.entries(error.fields).map(([field, key]) => <li key={field}>
      <strong>{t(formFields[field as keyof typeof formFields])}</strong>{t(key!)}
    </li>)}</ul>}
    {error.requestId && <p className="request-id">{t('common.requestId', { id: error.requestId })}</p>}
  </div>;
}

export function Field({ id, labelKey, hint, error, children }: {
  id: string; labelKey: TranslationKey; hint?: TranslationKey; error?: TranslationKey; children: ReactNode;
}) {
  const { t } = useI18n();
  return <div className="form-field">
    <label htmlFor={id}>{t(labelKey)}</label>
    {Children.map(children, (child) => {
      if (!isValidElement<HTMLAttributes<HTMLElement>>(child) || child.props.id !== id) return child;
      const describedBy = [...new Set([child.props['aria-describedby'], hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ').split(' ').filter(Boolean))].join(' ');
      return cloneElement(child, { 'aria-describedby': describedBy || undefined, 'aria-invalid': error ? true : child.props['aria-invalid'] });
    })}
    {hint && <p className="field-hint" id={`${id}-hint`}>{t(hint)}</p>}
    {error && <p className="field-error" id={`${id}-error`}>{t(error)}</p>}
  </div>;
}

export function Pagination({ offset, total, onChange }: { offset: number; total: number; onChange: (value: number) => void }) {
  const { t, formatNumber } = useI18n();
  return <div className="pagination">
    <button className="secondary" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - 25))}>{t('common.previousPage')}</button>
    <span>{t('common.pageOf', { page: formatNumber(Math.floor(offset / 25) + 1), pages: formatNumber(Math.max(1, Math.ceil(total / 25))) })}</span>
    <button className="secondary" disabled={offset + 25 >= total} onClick={() => onChange(offset + 25)}>{t('common.nextPage')}</button>
  </div>;
}
