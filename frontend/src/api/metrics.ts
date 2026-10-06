import { ApiError, requestJson } from './client';
import type { TranslationKey } from '../i18n';

export const rateLabels = { accepted_total: 'metrics.accepted', normalized_total: 'metrics.normalized', sent_total: 'metrics.sent' } satisfies Record<string, TranslationKey>;
export const overviewLabels = { events: 'metrics.events', events_new: 'metrics.new', events_critical: 'metrics.critical', notification_FAILED: 'metrics.failed' } satisfies Record<string, TranslationKey>;
export type Overview = Record<keyof typeof overviewLabels, number>;
export interface Metrics {
  fresh: boolean; sampled_at: string | null; rates: Record<keyof typeof rateLabels, number | null>;
  disk: { total: number; used: number; free: number } | null; disk_remaining_seconds: number | null;
  cpu_percent: number | null; memory: { total: number; available: number } | null;
}
function bad(): never { throw new ApiError({ key: 'errors.internalWithoutId' }); }
function record(value: unknown): Record<string, unknown> { if (!value || typeof value !== 'object' || Array.isArray(value)) return bad(); return value as Record<string, unknown>; }
function number(value: unknown): number { if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) return bad(); return value; }
const nullable = (value: unknown) => value == null ? null : number(value);
export function parseMetrics(value: unknown): Metrics {
  const data = record(value), rates = record(data.rates), disk = data.disk == null ? null : record(data.disk), memory = data.memory == null ? null : record(data.memory);
  if (typeof data.fresh !== 'boolean' || (data.sampled_at !== null && (typeof data.sampled_at !== 'string' || !Number.isFinite(Date.parse(data.sampled_at))))) return bad();
  return { fresh: data.fresh, sampled_at: data.sampled_at as string | null,
    rates: { accepted_total: nullable(rates.accepted_total), normalized_total: nullable(rates.normalized_total), sent_total: nullable(rates.sent_total) },
    disk: disk ? { total: number(disk.total), used: number(disk.used), free: number(disk.free) } : null,
    memory: memory ? { total: number(memory.total), available: number(memory.available) } : null,
    cpu_percent: nullable(data.cpu_percent), disk_remaining_seconds: nullable(data.disk_remaining_seconds) };
}
export function parseOverview(value: unknown): Overview {
  const data = record(value);
  return Object.fromEntries(Object.keys(overviewLabels).map((key) => {
    const count = number(data[key]); if (!Number.isSafeInteger(count)) return bad(); return [key, count];
  })) as Overview;
}
export async function getOverview(signal?: AbortSignal) { return parseOverview(await requestJson('/api/v1/overview', { signal })); }
