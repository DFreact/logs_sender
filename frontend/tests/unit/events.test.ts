import { describe, expect, it } from 'vitest';
import { parseEvent, parseEventDetail } from '../../src/api/events';
import { labelKey } from '../../src/i18n/mappings';
import { eventStatuses } from '../../src/i18n/mappings';

const item = { id: '00000000-0000-0000-0000-000000000123', subject: 'Событие', sender: '',
  status: 'NEW', severity: null, input_adapter: 'REST', received_at: '2026-09-29T12:00:00Z', occurrence_count: 1 };
describe('event response boundary', () => {
  it('keeps received content as data and unknown status has a Russian fallback', () => {
    const event = parseEvent({ ...item, status: 'INTERNAL_STATE', subject: '<script>alert(1)</script>' });
    expect(event.subject).toBe('<script>alert(1)</script>');
    expect(labelKey(eventStatuses, event.status)).toBe('common.unknownStatus');
  });
  it('rejects invalid IDs, unlimited previews and malformed occurrences', () => {
    expect(() => parseEvent({ ...item, id: '../secret' })).toThrow();
    expect(() => parseEventDetail({ ...item, body: 'a'.repeat(65537), occurrences: [] })).toThrow();
    expect(() => parseEventDetail({ ...item, body: '', occurrences: [{ id: item.id }] })).toThrow();
  });
});
