import { describe, it, expect } from 'vitest';
import { parseRetention } from '../../src/api/retention';
import { parseMetrics, parseOverview } from '../../src/api/metrics';

describe('bounded operational responses', () => {
  it('rejects invalid days and treats missing measurements as unknown', () => {
    const policy = { version: 0, enabled: false, inherit: true, days: { event_days: 90, raw_days: 30 }, progress: [] };
    expect(parseRetention(policy, true)).toEqual(policy);
    expect(() => parseRetention({ ...policy, days: { ...policy.days, raw_days: '30' } }, true)).toThrow();
    expect(() => parseRetention({ ...policy, days: { ...policy.days, raw_days: 0 } }, true)).toThrow();
    const metrics = { fresh: false, sampled_at: null, rates: { accepted_total: null, normalized_total: null, sent_total: null }, disk: null, memory: null, cpu_percent: null, disk_remaining_seconds: null };
    expect(parseMetrics(metrics)).toEqual(metrics);
    expect(() => parseMetrics({ ...metrics, rates: { ...metrics.rates, sent_total: -1 } })).toThrow();
  });
  it('accepts fixed archive counts and rejects unsafe numbers', () => {
    const overview = { events: 1_000_000, events_new: 100, events_critical: 7, notification_FAILED: 2 };
    expect(parseOverview(overview)).toEqual(overview);
    expect(() => parseOverview({ ...overview, events: Number.POSITIVE_INFINITY })).toThrow();
    expect(() => parseOverview({ ...overview, notification_FAILED: null })).toThrow();
  });
});
