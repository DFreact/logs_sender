import { expect, it } from 'vitest';
import { parseHealth } from '../../src/api/health';
import { ComponentCodeValues } from '../../src/api/generated';

const response = { status: 'HEALTHY', checked_at: '2026-09-29T10:00:00Z',
  components: ComponentCodeValues.map((component) => ({ component, status: 'HEALTHY', code: 'OK' })),
  queue: { PENDING: 0, RUNNING: 0, SUCCEEDED: 1, FAILED: 0 }, oldest_due_seconds: 0 };

it('discards raw details and localizes unknown component statuses through safe enums', () => {
  const data = structuredClone(response);
  data.components[0] = { ...data.components[0], status: 'password=secret', code: 'raw-error' };
  const result = parseHealth({ ...data, detail: 'redis://secret' });
  expect(result.components[0].status).toBe('UNKNOWN');
  expect(result.components[0].code).toBe('NOT_OBSERVED');
  expect(JSON.stringify(result)).not.toMatch(/secret|raw-error/);
});
it('rejects duplicate/missing components and invalid counters', () => {
  expect(() => parseHealth({ ...response, components: [response.components[0]] })).toThrow();
  expect(() => parseHealth({ ...response, components: response.components.map(() => response.components[0]) })).toThrow();
  expect(() => parseHealth({ ...response, queue: { ...response.queue, PENDING: -1 } })).toThrow();
});
