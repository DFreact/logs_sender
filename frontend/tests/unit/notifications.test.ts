import { describe, expect, it } from 'vitest';
import { parseNotification, parseAttempt, parseDetail } from '../../src/api/notifications';
import { createI18n } from '../../src/i18n/core';
import { defaultLocale } from '../../src/i18n/registry';
import { severities } from '../../src/i18n/mappings';
import { readFileSync } from 'node:fs';
const serverTexts = JSON.parse(readFileSync(new URL('../../../backend/app/i18n/notifications.ru.json', import.meta.url), 'utf8')) as { severity: Record<string, string> };
const id = '11111111-1111-1111-1111-111111111111', date = '2026-09-29T00:00:00Z';
const notification = { id, event_id: null, channel_id: id, channel_name: 'Почта', channel_version: 1, kind: 'SMTP', subject: 'Событие', status: 'FAILED', mode: 'IMMEDIATE', created_at: date, due_at: date, finished_at: date, generation: 1, attempt_count: 5, max_attempts: 5, failure_code: 'CONNECTION_FAILED', uncertain: true, version: 2, is_test: false, dead_lettered: true };
describe('notification boundaries', () => {
  it('does not pass secrets, context or exceptions to components', () => {
    expect(parseNotification({ ...notification, configuration: { password: 'private' }, context: { secret: 'private' }, detail: 'SQL exception' })).toEqual(notification);
    expect(parseDetail({ ...notification, prepared: null, retry_delays: [30, 120], secret: 'private' })).not.toHaveProperty('secret');
    expect(parseAttempt({ id, generation: 1, attempt_number: 1, started_at: date, finished_at: date, status: 'UNKNOWN', failure_code: 'UNKNOWN_RESULT', detail: 'private', lease_token: id })).not.toHaveProperty('detail');
  });
  it('rejects invalid statuses, dates and unbounded values safely', () => {
    for (const update of [{ status: 'INTERNAL_VALUE' }, { id: 'bad' }, { created_at: 'bad' }, { uncertain: 'true' }, { attempt_count: -1 }, { failure_code: 'x'.repeat(41) }]) expect(() => parseNotification({ ...notification, ...update })).toThrow();
    expect(() => parseDetail({ ...notification, prepared: null, retry_delays: Array(9).fill(1) })).toThrow();
    expect(() => parseDetail({ ...notification, prepared: { subject: '', body: 'x'.repeat(65537), html: '', payload: '' }, retry_delays: [] })).toThrow();
  });
  it('uses identical Russian severity labels in rendered messages and UI', () => {
    const i18n = createI18n(defaultLocale, 'Europe/Moscow');
    for (const [severity, key] of Object.entries(severities)) expect(serverTexts.severity[severity as keyof typeof serverTexts.severity]).toBe(i18n.t(key));
  });
});
