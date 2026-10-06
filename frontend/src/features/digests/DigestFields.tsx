import { useEffect, useId, useState } from 'react';
import { useI18n, type TranslationKey } from '../../i18n';
import type { Digest } from '../../api/digests';
import type { Channel, Template } from '../../api/channels';
import type { Source } from '../../api/configuration';
import { DigestPeriodValues, DigestSelectionValues, SeverityValues } from '../../api/generated';
import { digestPeriods, digestSelections, severities } from '../../i18n/mappings';
import { Field } from '../../components/Forms';
export const zones = { 'Europe/Moscow': 'digests.zone.Moscow', UTC: 'digests.zone.UTC', 'Europe/Berlin': 'digests.zone.Berlin', 'Europe/Kaliningrad': 'digests.zone.Kaliningrad', 'Asia/Yekaterinburg': 'digests.zone.Yekaterinburg', 'Asia/Novosibirsk': 'digests.zone.Novosibirsk', 'Asia/Vladivostok': 'digests.zone.Vladivostok' } satisfies Record<string, TranslationKey>;
function TimeField({ id, minute, change }: { id: string; minute: number; change: (v: number) => void }) {
  const format = (v: number) => `${String(Math.floor(v / 60)).padStart(2, '0')}:${String(v % 60).padStart(2, '0')}`;
  const [text, setText] = useState(() => format(minute));
  useEffect(() => { if (Number.isInteger(minute) && minute >= 0 && minute < 1440) setText(format(minute)); }, [minute]);
  return <Field id={id} labelKey="digests.time" hint="digests.timeHint"><input id={id} type="text" maxLength={5} value={text} onChange={(e) => { const value = e.target.value; setText(value); const valid = /^([01]\d|2[0-3]):[0-5]\d$/.test(value); const [h, m] = value.split(':').map(Number); change(valid ? h * 60 + m : Number.NaN); }} /></Field>;
}
export function DigestFields({ value, change, channels, templates, sources, readOnly = false }: { value: Digest; change: (v: Digest) => void; channels: Channel[]; templates: Template[]; sources: Source[]; readOnly?: boolean }) {
  const { t } = useI18n(); const id = useId(); const set = (v: Partial<Digest>) => change({ ...value, ...v });
  const channel = channels.find((v) => v.id === value.channel_id);
  return <fieldset disabled={readOnly}>
    <Field id={`${id}-name`} labelKey="config.name"><input id={`${id}-name`} maxLength={120} value={value.name} onChange={(e) => set({ name: e.target.value })} /></Field>
    <Field id={`${id}-description`} labelKey="config.description"><textarea id={`${id}-description`} maxLength={2000} value={value.description} onChange={(e) => set({ description: e.target.value })} /></Field>
    <label className="checkbox-field"><input type="checkbox" checked={value.enabled} onChange={(e) => set({ enabled: e.target.checked })} />{t('digests.enabled')}</label><p className="field-hint">{t('digests.changesHint')}</p>
    <Field id={`${id}-channel`} labelKey="channels.selection"><select id={`${id}-channel`} value={value.channel_id} onChange={(e) => set({ channel_id: e.target.value, template_id: '' })}><option value="">{t('channels.unbound')}</option>{value.channel_id && !channels.some((c) => c.available && c.id === value.channel_id) && <option value={value.channel_id}>{channel?.name ?? t('channels.existingUnavailable')}</option>}{channels.filter((c) => c.available).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></Field>
    <Field id={`${id}-template`} labelKey="digests.template" hint="digests.templateHint"><select id={`${id}-template`} value={value.template_id} onChange={(e) => set({ template_id: e.target.value })}><option value="">{t('digests.templateUnbound')}</option>{templates.filter((v) => v.kind === channel?.kind || v.id === value.template_id).map((v) => <option key={v.id} value={v.id}>{v.name}</option>)}</select></Field>
    <div className="config-columns"><TimeField id={`${id}-time`} minute={value.minute_of_day} change={(minute_of_day) => set({ minute_of_day })} /><Field id={`${id}-zone`} labelKey="digests.zone"><select id={`${id}-zone`} value={value.time_zone} onChange={(e) => set({ time_zone: e.target.value })}>{!Object.hasOwn(zones, value.time_zone) && <option value={value.time_zone}>{t('digests.zone.other')}</option>}{Object.entries(zones).map(([v, label]) => <option key={v} value={v}>{t(label)}</option>)}</select></Field></div>
    <p className="field-hint">{t('digests.scheduleHint')}</p>
    <Field id={`${id}-period`} labelKey="digests.period" hint="digests.periodHint"><select id={`${id}-period`} value={value.period} onChange={(e) => set({ period: e.target.value as Digest['period'] })}>{DigestPeriodValues.map((v) => <option key={v} value={v}>{t(digestPeriods[v])}</option>)}</select></Field>
    <Field id={`${id}-selection`} labelKey="digests.selection" hint="digests.ruleHint"><select id={`${id}-selection`} value={value.selection} onChange={(e) => set({ selection: e.target.value as Digest['selection'] })}>{DigestSelectionValues.map((v) => <option key={v} value={v}>{t(digestSelections[v])}</option>)}</select></Field>
    <fieldset className="policy-fields"><legend>{t('digests.sources')}</legend><p className="field-hint">{t('digests.allSources')}</p>{sources.map((s) => <label className="checkbox-field" key={s.id}><input type="checkbox" checked={value.source_ids.includes(s.id)} onChange={(e) => set({ source_ids: e.target.checked ? [...value.source_ids, s.id] : value.source_ids.filter((v) => v !== s.id) })} />{s.name}</label>)}</fieldset>
    <fieldset className="policy-fields"><legend>{t('digests.severities')}</legend><p className="field-hint">{t('digests.allSeverities')}</p>{(['', ...SeverityValues] as const).map((s) => <label className="checkbox-field" key={s}><input type="checkbox" checked={value.severities.includes(s)} onChange={(e) => set({ severities: e.target.checked ? [...value.severities, s] : value.severities.filter((v) => v !== s) })} />{t(s ? severities[s] : 'events.severity.unset')}</label>)}</fieldset>
    <Field id={`${id}-wait`} labelKey="digests.wait" hint="digests.waitHint"><input id={`${id}-wait`} type="number" min={1} max={10080} step={1} value={value.wait_seconds / 60} onChange={(e) => set({ wait_seconds: Number(e.target.value) * 60 })} /></Field>
    <label className="checkbox-field"><input type="checkbox" checked={value.send_empty} onChange={(e) => set({ send_empty: e.target.checked })} />{t('digests.sendEmpty')}</label>
  </fieldset>;
}
