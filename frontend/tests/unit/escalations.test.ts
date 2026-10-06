import { expect, it } from 'vitest';
import { parsePolicy, parseRun, parseState, parseEventAction } from '../../src/api/escalations';
const id = '11111111-1111-1111-1111-111111111111';
const date = '2026-10-06T00:00:00Z';
it('drops internal fields from event actions and runs', () => {
  expect(parseState({ version: 2, status: 'ACKNOWLEDGED', acknowledged_by: 'Оператор', acknowledged_at: date, detail: 'secret' })).not.toHaveProperty('detail');
  expect(parseEventAction({ id, author: 'Оператор', from_status: 'NEW', to_status: 'RESOLVED', created_at: date, operation_key: id })).not.toHaveProperty('operation_key');
  expect(parseRun({ id, name: 'Политика', version: 1, state: 'STOPPED', created_at: date, stopped_at: date, stop_reason: 'ACKNOWLEDGED', steps: [], context: { secret: 'private' } })).not.toHaveProperty('context');
});
it('rejects unknown states, dates and oversized policies safely', () => {
  expect(() => parseState({ version: 2, status: 'RAW_STATE', acknowledged_by: null, acknowledged_at: null })).toThrow();
  expect(() => parseState({ version: 2, status: 'NEW', acknowledged_by: null, acknowledged_at: 'bad' })).toThrow();
  for (const steps of [[], Array(11).fill({ channel_id: id, delay_seconds: 0 }), [{ channel_id: id, delay_seconds: 2592001 }]]) expect(() => parsePolicy({ id, name: 'Проверка', description: '', enabled: true, version: 1, steps })).toThrow();
});
