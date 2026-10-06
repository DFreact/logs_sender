import { ApiError, requestJson } from './client';
import type { TranslationKey } from '../i18n';

export const retentionFields = {
  event_days: 'retention.event', raw_days: 'retention.raw', notification_days: 'retention.notification',
  attempt_days: 'retention.attempt', audit_days: 'retention.audit', task_days: 'retention.task',
  history_days: 'retention.history', health_days: 'retention.health',
} satisfies Record<string, TranslationKey>;
export const retentionHints = {
  event_days: 'retention.eventHint', raw_days: 'retention.rawHint', notification_days: 'retention.notificationHint',
  attempt_days: 'retention.attemptHint', audit_days: 'retention.auditHint', task_days: 'retention.taskHint',
  history_days: 'retention.historyHint', health_days: 'retention.healthHint',
} satisfies Record<keyof typeof retentionFields, TranslationKey>;
export const retentionPhases = {
  RECALCULATE_EVENTS: 'retention.phaseEvents', RECALCULATE_RAW: 'retention.phaseRaw',
  RAW_FILES: 'retention.phaseFiles', EVENTS: 'retention.phaseArchive',
  NOTIFICATIONS: 'retention.phaseNotifications', DIGEST_HISTORY: 'retention.phaseDigests',
} satisfies Record<string, TranslationKey>;
export type RetentionField = keyof typeof retentionFields;
export interface Retention {
  version: number; enabled: boolean; inherit: boolean; days: Partial<Record<RetentionField, number>>;
  progress: { kind: string; checked_at: string; processed: number; held: number; complete: boolean }[];
}
export function parseRetention(value: unknown, source = false): Retention {
  const data = value as Retention;
  const keys: RetentionField[] = source ? ['event_days', 'raw_days'] : Object.keys(retentionFields) as RetentionField[];
  if (!data || typeof data !== 'object' || !Number.isSafeInteger(data.version) || data.version < 0
    || typeof data.enabled !== 'boolean' || typeof data.inherit !== 'boolean'
    || !data.days || Object.keys(data.days).length !== keys.length
    || keys.some((key) => !Number.isSafeInteger(data.days[key]) || data.days[key]! < 1 || data.days[key]! > 3650)
    || !Array.isArray(data.progress) || data.progress.length > 16
    || data.progress.some((row) => !row || typeof row.kind !== 'string' || !Number.isFinite(Date.parse(row.checked_at))
      || !Number.isSafeInteger(row.processed) || row.processed < 0 || !Number.isSafeInteger(row.held) || row.held < 0 || typeof row.complete !== 'boolean')) {
    throw new ApiError({ key: 'errors.internalWithoutId' });
  }
  return data;
}
const path = (source?: string) => `/api/v1/settings/retention${source ? `?source_id=${encodeURIComponent(source)}` : ''}`;
export async function getRetention(source?: string, signal?: AbortSignal) {
  return parseRetention(await requestJson(path(source), { signal }), !!source);
}
export async function saveRetention(data: Retention, csrf: string, source?: string) {
  const { version, enabled, inherit, days } = data;
  return parseRetention(await requestJson(path(source), { method: 'PATCH', body: { version, enabled, inherit, days }, csrf }), !!source);
}
