import { describe, expect, it } from 'vitest';
import { parseUser } from '../../src/api/identity';

const user = { id: '00000000-0000-0000-0000-000000000001', username: 'reader',
  display_name: 'Наблюдатель', active: true, role: 'VIEWER', version: 1,
  created_at: '2026-09-29T00:00:00Z' };

describe('identity response boundary', () => {
  it('drops unexpected fields from a valid user response', () => {
    expect(parseUser({ ...user, password_hash: 'never-display', token: 'never-display' })).toEqual(user);
  });
  it.each([{ role: 'UNKNOWN' }, { active: 'true' }, { version: 0 },
    { created_at: 'raw-service-message' }, { display_name: {} }])('rejects invalid user data %s', (change) => {
    expect(() => parseUser({ ...user, ...change })).toThrow('API_REQUEST_FAILED');
  });
});

it('accepts a full event subject as an audit target and discards private fields', async () => {
  const { vi } = await import('vitest');
  const { getAudit } = await import('../../src/api/identity');
  const entry = { id: user.id, timestamp: user.created_at, actor: 'Оператор', target: 'Я'.repeat(500), action: 'EVENT_ACKNOWLEDGED', before: {}, after: {}, request_ip: null, secret: 'private' };
  const mock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ items: [entry], total: 1 }), { headers: { 'Content-Type': 'application/json' } }));
  try { const result = await getAudit(); expect(result.items[0].target).toHaveLength(500); expect(result.items[0]).not.toHaveProperty('secret'); }
  finally { mock.mockRestore(); }
});
