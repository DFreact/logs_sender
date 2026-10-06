import { parseMetrics, type Metrics } from './metrics';
import { ApiError, requestJson } from './client';
import { ComponentCodeValues, HealthStatusValues, HealthReasonValues, WorkStateValues,
  type ComponentCode, type HealthStatus, type HealthReason, type WorkState } from './generated';

export interface SystemHealth {
  metrics?: Metrics; status: HealthStatus; checked_at: string;
  components: { component: ComponentCode; status: HealthStatus; code: HealthReason; last_seen_at: string | null }[];
  queue: Record<WorkState, number>; oldest_due_seconds: number;
}
function invalid(): never { throw new ApiError({ key: 'errors.internalWithoutId' }); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function date(value: unknown): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !Number.isFinite(Date.parse(value))) return invalid();
  return value;
}
function count(value: unknown): number {
  if (typeof value !== 'number' || !Number.isSafeInteger(value) || value < 0) return invalid();
  return value;
}
export function parseHealth(value: unknown): SystemHealth {
  const data = record(value);
  if (!HealthStatusValues.includes(data.status as HealthStatus) || !Array.isArray(data.components)
    || data.components.length !== ComponentCodeValues.length) return invalid();
  const seen = new Set<ComponentCode>();
  const components = data.components.map((value) => {
    const item = record(value), component = item.component as ComponentCode;
    if (!ComponentCodeValues.includes(component) || seen.has(component)) return invalid();
    seen.add(component);
    return { component,
      status: HealthStatusValues.includes(item.status as HealthStatus) ? item.status as HealthStatus : 'UNKNOWN' as const,
      code: HealthReasonValues.includes(item.code as HealthReason) ? item.code as HealthReason : 'NOT_OBSERVED' as const,
      last_seen_at: item.last_seen_at == null ? null : date(item.last_seen_at) };
  });
  const queue = record(data.queue);
  return { metrics: data.metrics == null ? undefined : parseMetrics(data.metrics), status: data.status as HealthStatus, checked_at: date(data.checked_at), components,
    queue: Object.fromEntries(WorkStateValues.map((state) => [state, count(queue[state])])) as Record<WorkState, number>,
    oldest_due_seconds: count(data.oldest_due_seconds) };
}
export async function getHealth(signal: AbortSignal) {
  return parseHealth(await requestJson('/api/v1/health', { signal }));
}
