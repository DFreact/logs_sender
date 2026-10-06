import { useId } from 'react';
import { useI18n, labelKey, type TranslationKey } from '../../i18n';
import { Field } from '../../components/Forms';
import { newCondition, type Condition, type Policy, type Source } from '../../api/configuration';
import { SeverityValues } from '../../api/generated';
import { severities } from '../../i18n/mappings';

export const conditionFields: Record<string, TranslationKey> = { sender: 'sources.sender', recipient: 'events.recipients', subject: 'events.subject', body: 'events.body', header: 'config.field.header', adapter: 'sources.inputAdapter', metadata: 'config.field.metadata' };
export const operators: Record<string, TranslationKey> = { equals: 'config.op.equals', not_equals: 'config.op.not_equals', contains: 'config.op.contains', not_contains: 'config.op.not_contains', starts_with: 'config.op.starts_with', ends_with: 'config.op.ends_with', regex: 'config.op.regex', exists: 'config.op.exists', gt: 'config.op.gt', gte: 'config.op.gte', lt: 'config.op.lt', lte: 'config.op.lte' };
export const dedupFields: Record<string, TranslationKey> = { sender: 'sources.sender', subject: 'events.subject', body: 'events.body', event_type: 'config.eventType', category: 'config.category', severity: 'events.severityLabel', tags: 'config.tags' };
const groupKeys: Record<string, TranslationKey> = { AND: 'config.group.AND', OR: 'config.group.OR', NOT: 'config.group.NOT' };

export function PolicyFields({ value, change, allowInherit = false }: { value: Policy; change: (v: Policy) => void; allowInherit?: boolean }) {
  const { t } = useI18n(); const id = useId();
  return <fieldset className="policy-fields"><legend>{t('config.dedup')}</legend>
    {allowInherit && <label className="checkbox-field"><input type="checkbox" checked={value.inherit} onChange={(e) => change({ ...value, inherit: e.target.checked })} />{t('config.inherit')}</label>}
    <fieldset disabled={allowInherit && value.inherit}>
      <label className="checkbox-field"><input type="checkbox" checked={value.enabled} onChange={(e) => change({ ...value, enabled: e.target.checked })} />{t('config.dedupEnabled')}</label>
      <Field id={`${id}-window`} labelKey="config.window" hint="config.windowHint"><input id={`${id}-window`} type="number" min={1} max={1440} step={1} value={value.window_seconds / 60} onChange={(e) => change({ ...value, window_seconds: Number(e.target.value) * 60 })} aria-describedby={`${id}-window-hint`} /></Field>
      <p>{t('config.compareFields')}</p><div className="checkbox-grid">{Object.entries(dedupFields).map(([field, key]) => <label key={field} className="checkbox-field"><input type="checkbox" checked={value.fields.includes(field)} onChange={(e) => change({ ...value, fields: e.target.checked ? [...value.fields, field] : value.fields.filter((f) => f !== field) })} />{t(key)}</label>)}</div>
      <p className="field-hint">{t('config.compareHint')}</p>
    </fieldset>
  </fieldset>;
}

export function ConditionEditor({ value, change, depth = 0, readOnly = false, routing = false, sources = [] }: { value: Condition; change: (v: Condition) => void; depth?: number; readOnly?: boolean; routing?: boolean; sources?: Source[] }) {
  const { t } = useI18n(); const id = useId();
  if ('group' in value) return <fieldset className="condition-group" disabled={readOnly}>
    <legend>{t('config.conditions')}</legend>
    <Field id={`${id}-group`} labelKey="config.match"><select id={`${id}-group`} value={value.group} onChange={(e) => { const group = e.target.value as 'AND' | 'OR' | 'NOT'; change({ group, children: group === 'NOT' ? value.children.slice(0, 1) : value.children }); }}>{Object.entries(groupKeys).map(([v, key]) => <option key={v} value={v}>{t(key)}</option>)}</select></Field>
    {value.children.map((child, index) => <div key={index} className="condition-row"><ConditionEditor value={child} change={(v) => change({ ...value, children: value.children.map((c, i) => i === index ? v : c) })} depth={depth + 1} readOnly={readOnly} routing={routing} sources={sources} />
      {!readOnly && value.children.length > 1 && <button type="button" className="secondary compact" aria-label={t('config.removeCondition')} onClick={() => change({ ...value, children: value.children.filter((_, i) => i !== index) })}>{t('common.delete')}</button>}
    </div>)}
    {!readOnly && ['AND', 'OR'].includes(value.group) && value.children.length < 100 && <div className="form-actions"><button type="button" className="secondary compact" onClick={() => change({ ...value, children: [...value.children, newCondition()] })}>{t('config.addCondition')}</button>{depth < 2 && <button type="button" className="secondary compact" onClick={() => change({ ...value, children: [...value.children, { group: 'AND', children: [newCondition()] }] })}>{t('config.addGroup')}</button>}</div>}
  </fieldset>;
  const fields: Record<string, TranslationKey> = routing ? { ...conditionFields, source: 'events.source', severity: 'events.severityLabel', category: 'config.category', event_type: 'config.eventType', tags: 'config.tags' } : conditionFields;
  const typed = ['source', 'severity'].includes(value.field);
  const selector = value.field === 'header' || value.field === 'metadata';
  const numeric = ['gt', 'gte', 'lt', 'lte'].includes(value.operator);
  return <fieldset className="condition-leaf" disabled={readOnly}>
    <Field id={`${id}-field`} labelKey="config.field"><select id={`${id}-field`} value={value.field} onChange={(e) => change({ field: e.target.value, operator: ['source', 'severity'].includes(e.target.value) ? 'equals' : 'contains', value: '', case_sensitive: false, ...(['header', 'metadata'].includes(e.target.value) ? { selector: '' } : {}) })}>{Object.entries(fields).map(([v, key]) => <option key={v} value={v}>{t(key)}</option>)}</select></Field>
    {selector && <Field id={`${id}-selector`} labelKey={value.field === 'header' ? 'config.headerName' : 'config.metadataKey'} hint="config.selectorHint"><input id={`${id}-selector`} maxLength={120} value={value.selector ?? ''} onChange={(e) => change({ ...value, selector: e.target.value })} /></Field>}
    <Field id={`${id}-op`} labelKey="config.operator"><select id={`${id}-op`} value={value.operator} onChange={(e) => change({ ...value, operator: e.target.value, value: e.target.value === 'exists' ? undefined : ['gt', 'gte', 'lt', 'lte'].includes(e.target.value) ? 0 : '' })}>{Object.entries(operators).filter(([v]) => typed ? ['equals', 'not_equals', 'exists'].includes(v) : value.field === 'metadata' || !['gt', 'gte', 'lt', 'lte'].includes(v)).map(([v, key]) => <option key={v} value={v}>{t(key)}</option>)}</select></Field>
    {!['exists'].includes(value.operator) && <Field id={`${id}-value`} labelKey="config.value" hint={value.operator === 'regex' ? 'config.regexHint' : value.field === 'adapter' ? 'config.adapterHint' : undefined}>
      {typed ? <select id={`${id}-value`} value={String(value.value ?? '')} onChange={(e) => change({ ...value, value: e.target.value })}><option value="">{t('config.choose')}</option>{value.field === 'source' ? sources.map((source) => <option key={source.id} value={source.id}>{source.name}</option>) : SeverityValues.map((severity) => <option key={severity} value={severity}>{t(severities[severity])}</option>)}</select> : value.field === 'adapter' && value.operator !== 'regex' ? <select id={`${id}-value`} value={String(value.value ?? '')} onChange={(e) => change({ ...value, value: e.target.value })}><option value="">{t('config.choose')}</option><option value="SMTP">{t('events.adapter.SMTP')}</option><option value="REST">{t('events.adapter.REST')}</option></select> : <input id={`${id}-value`} type={numeric ? 'number' : 'text'} maxLength={500} value={value.value ?? ''} onChange={(e) => change({ ...value, value: numeric ? Number(e.target.value) : e.target.value })} />}
    </Field>}
    {!typed && !numeric && !['exists'].includes(value.operator) && <label className="checkbox-field"><input type="checkbox" checked={value.case_sensitive ?? false} onChange={(e) => change({ ...value, case_sensitive: e.target.checked })} />{t('config.caseSensitive')}</label>}
    {readOnly && !Object.hasOwn(operators, value.operator) && <p>{t(labelKey(operators, value.operator))}</p>}
  </fieldset>;
}
