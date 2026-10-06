import { expect, it } from 'vitest';
import { parseDigest, parseRun, parseSummary } from '../../src/api/digests';
const id = '12345678-1234-1234-1234-123456789abc';
const definition = { id, name: 'Сводка', description: '', enabled: true, version: 1, channel_id: id, template_id: id, time_zone: 'Europe/Moscow', minute_of_day: 480, period: 'CALENDAR', selection: 'FILTER', source_ids: [], severities: [], wait_seconds: 3600, send_empty: false };
it('validates daily configuration and strips internal settings', () => {
  expect(parseDigest({ ...definition, secret: 'hidden' })).toEqual(definition);
  for (const changed of [{ minute_of_day: 1440 }, { period: 'unknown' }, { wait_seconds: -1 }, { severities: ['DEBUG_INTERNAL'] }]) expect(() => parseDigest({ ...definition, ...changed })).toThrow();
});
it('rejects unknown digest states and protects prepared history', () => {
  const run = { id, name: 'Сводка', definition_version: 1, version: 1, state: 'ATTENTION', window_start: '2026-10-05T05:00:00Z', window_end: '2026-10-06T05:00:00Z', review_at: '2026-10-06T06:00:00Z', partial: false, pending_count: 1, missing_at_cutoff: 1, late_count: 0, summary: {}, notification_id: null, notification_status: null };
  expect(parseRun({ ...run, operation_key: 'hidden' }).state).toBe('ATTENTION');
  expect(parseRun(run).summary).toBeNull();
  expect(() => parseRun({ ...run, state: 'INTERNAL_FAILED' })).toThrow();
  expect(() => parseSummary({ occurrences: 1, events: 1, first: null, last: null, sources: [], severities: [{ label: 'TRACE', count: 1 }], frequent: [] })).toThrow();
});
