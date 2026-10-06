import type { Digest } from '../../api/digests';
import type { Policy } from '../../api/escalations';
import { localDate, parseDate } from '../../components/dateTime';
import { useState, useEffect } from 'react';
import { useId } from 'react';
import { useI18n, type TranslationKey } from '../../i18n';
import { severities, deliveryModes } from '../../i18n/mappings';
import { RuleActionValues, SeverityValues, type RuleAction, type ActionOutcome } from '../../api/generated';
import { newAction, emptyFields, type Action, type RoutingRule } from '../../api/routing';
import type { Channel } from '../../api/channels';
import type { Source } from '../../api/configuration';
import { Field } from '../../components/Forms';
import { ConditionEditor } from './Editors';

export const actionKeys = { SET_FIELDS: 'routing.action.SET_FIELDS', SUPPRESS: 'routing.action.SUPPRESS', NOTIFY: 'routing.action.NOTIFY', DIGEST: 'routing.action.DIGEST', ESCALATE: 'routing.action.ESCALATE' } satisfies Record<RuleAction, TranslationKey>;
export const outcomeKeys = { APPLIED: 'routing.outcome.APPLIED', PLANNED: 'routing.outcome.PLANNED', SUPPRESSED: 'routing.outcome.SUPPRESSED', REPEAT: 'routing.outcome.REPEAT', OVERRIDDEN: 'routing.outcome.OVERRIDDEN' } satisfies Record<ActionOutcome, TranslationKey>;

function ScheduledField({ id, value, change }: { id: string; value: string | null; change: (v: string | null) => void }) {
  const { t } = useI18n();
  const [text, setText] = useState(value ? localDate(new Date(value)) : '');
  useEffect(() => { if (value) setText(localDate(new Date(value))); }, [value]);
  const invalid = text !== '' && !parseDate(text);
  return <Field id={id} labelKey="routing.scheduledAt" hint="routing.scheduleHint" error={invalid ? 'validation.dateTime' : undefined}><input id={id} type="text" maxLength={16} placeholder={t('events.datePlaceholder')} value={text} onChange={(e) => { setText(e.target.value); change(parseDate(e.target.value)?.toISOString() ?? null); }} /></Field>;
}

function ActionFields({ value, change, channels, policies, digests }: { value: Action; change: (v: Action) => void; channels: Channel[]; policies: Policy[]; digests: Digest[] }) {
  const { t } = useI18n(); const id = useId(); const fields = value.fields ?? emptyFields();
  const set = (v: Partial<typeof fields>) => change({ ...value, fields: { ...fields, ...v } });
  return <>
    <div className="config-columns"><Field id={`${id}-kind`} labelKey="routing.action"><select id={`${id}-kind`} value={value.kind} onChange={(e) => change({ ...newAction(e.target.value as RuleAction), scope: value.scope })}>{RuleActionValues.map((kind) => <option key={kind} value={kind}>{t(actionKeys[kind])}</option>)}</select></Field>
      <Field id={`${id}-scope`} labelKey="routing.scope"><select id={`${id}-scope`} value={value.scope} onChange={(e) => change({ ...value, scope: e.target.value as Action['scope'] })}><option value="EVENT">{t('routing.scope.EVENT')}</option><option value="OCCURRENCE">{t('routing.scope.OCCURRENCE')}</option></select></Field></div>
    {value.kind === 'SET_FIELDS' && <>
      <Field id={`${id}-severity`} labelKey="events.severityLabel"><select id={`${id}-severity`} value={fields.severity ?? ''} onChange={(e) => set({ severity: e.target.value || null })}><option value="">{t('config.keepSeverity')}</option>{SeverityValues.map((v) => <option key={v} value={v}>{t(severities[v])}</option>)}</select></Field>
      <label className="checkbox-field"><input type="checkbox" checked={fields.category !== null} onChange={(e) => set({ category: e.target.checked ? '' : null })} />{t('routing.setCategory')}</label>
      {fields.category !== null && <Field id={`${id}-category`} labelKey="config.category"><input id={`${id}-category`} maxLength={120} value={fields.category} onChange={(e) => set({ category: e.target.value })} /></Field>}
      <label className="checkbox-field"><input type="checkbox" checked={fields.event_type !== null} onChange={(e) => set({ event_type: e.target.checked ? '' : null })} />{t('routing.setType')}</label>
      {fields.event_type !== null && <Field id={`${id}-type`} labelKey="config.eventType"><input id={`${id}-type`} maxLength={120} value={fields.event_type} onChange={(e) => set({ event_type: e.target.value })} /></Field>}
      <Field id={`${id}-tags`} labelKey="config.tags" hint="config.tagsHint"><input id={`${id}-tags`} value={fields.tags.join(',')} onChange={(e) => set({ tags: e.target.value ? e.target.value.split(',') : [] })} /></Field>
    </>}
    {value.kind === 'DIGEST' && <Field id={`${id}-digest`} labelKey="digests.title" hint="digests.ruleHint"><select id={`${id}-digest`} value={value.target_id ?? ''} onChange={(e) => change({ ...value, target_id: e.target.value || null })}><option value="">{t('digests.unbound')}</option>{value.target_id && !digests.some((d) => d.enabled && d.id === value.target_id) && <option value={value.target_id}>{digests.find((d) => d.id === value.target_id)?.name ?? t('channels.existingUnavailable')}</option>}{digests.filter((d) => d.enabled && d.selection === 'RULE').map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}</select></Field>}
    {value.kind === 'ESCALATE' && <Field id={`${id}-policy`} labelKey="escalation.selection" hint="escalation.scheduleHint"><select id={`${id}-policy`} value={value.target_id ?? ''} onChange={(e) => change({ ...value, target_id: e.target.value || null })}><option value="">{t('escalation.unbound')}</option>{value.target_id && !policies.some((p) => p.enabled && p.id === value.target_id) && <option value={value.target_id}>{policies.find((p) => p.id === value.target_id)?.name ?? t('channels.existingUnavailable')}</option>}{policies.filter((p) => p.enabled).map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></Field>}
    {value.kind === 'NOTIFY' && <>
      <Field id={`${id}-channel`} labelKey="channels.selection"><select id={`${id}-channel`} value={value.target_id ?? ''} onChange={(e) => change({ ...value, target_id: e.target.value || null })}><option value="">{t('channels.unbound')}</option>{value.target_id && !channels.some((c) => c.available && c.id === value.target_id) && <option value={value.target_id}>{channels.find((c) => c.id === value.target_id)?.name ?? t('channels.existingUnavailable')}</option>}{channels.filter((c) => c.available).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
      <Field id={`${id}-mode`} labelKey="routing.mode"><select id={`${id}-mode`} value={value.mode} onChange={(e) => change({ ...value, mode: e.target.value as Action['mode'], delay_seconds: e.target.value === 'DELAYED' ? 600 : null, scheduled_at: null })}>{(['IMMEDIATE', 'DELAYED', 'SCHEDULED'] as const).map((mode) => <option key={mode} value={mode}>{t(deliveryModes[mode])}</option>)}</select></Field>
      {value.mode === 'DELAYED' && <Field id={`${id}-delay`} labelKey="routing.delay" hint="routing.delayHint"><input id={`${id}-delay`} type="number" min={1} max={43200} step={1} value={(value.delay_seconds ?? 600) / 60} onChange={(e) => change({ ...value, delay_seconds: Number(e.target.value) * 60 })} /></Field>}
      {value.mode === 'SCHEDULED' && <ScheduledField id={`${id}-at`} value={value.scheduled_at ?? null} change={(scheduled_at) => change({ ...value, scheduled_at })} />}
    </>}
  </>;
}

export function RoutingFields({ value, change, sources, channels = [], policies = [], digests = [], readOnly = false }: { value: RoutingRule; change: (v: RoutingRule) => void; sources: Source[]; channels?: Channel[]; policies?: Policy[]; digests?: Digest[]; readOnly?: boolean }) {
  const { t } = useI18n(); const id = useId();
  return <fieldset disabled={readOnly}>
    <div className="config-columns"><Field id={`${id}-name`} labelKey="config.name"><input id={`${id}-name`} maxLength={120} value={value.name} onChange={(e) => change({ ...value, name: e.target.value })} /></Field><Field id={`${id}-priority`} labelKey="config.priority" hint="config.priorityHint"><input id={`${id}-priority`} type="number" min={0} max={100000} value={value.priority} onChange={(e) => change({ ...value, priority: Number(e.target.value) })} /></Field></div>
    <Field id={`${id}-description`} labelKey="config.description"><input id={`${id}-description`} maxLength={2000} value={value.description} onChange={(e) => change({ ...value, description: e.target.value })} /></Field>
    <label className="checkbox-field"><input type="checkbox" checked={value.enabled} onChange={(e) => change({ ...value, enabled: e.target.checked })} />{t('config.ruleEnabled')}</label>
    <p className="field-hint">{t('config.conditionHint')}</p><ConditionEditor value={value.conditions} change={(conditions) => change({ ...value, conditions })} routing sources={sources} readOnly={readOnly} />
    <h3>{t('routing.actions')}</h3><p className="field-hint">{t('routing.scopeHint')}</p>
    {value.actions.map((action, index) => <div className="condition-group" key={index}><ActionFields digests={digests} policies={policies} channels={channels} value={action} change={(a) => change({ ...value, actions: value.actions.map((old, i) => i === index ? a : old) })} />{!readOnly && value.actions.length > 1 && <button type="button" className="secondary compact" onClick={() => change({ ...value, actions: value.actions.filter((_, i) => i !== index) })}>{t('routing.removeAction')}</button>}</div>)}
    {!readOnly && value.actions.length < 10 && <button type="button" className="secondary" onClick={() => change({ ...value, actions: [...value.actions, newAction('NOTIFY')] })}>{t('routing.addAction')}</button>}
    <p className="field-hint">{t('routing.intentsHint')}</p>
  </fieldset>;
}
