import { describe, it, expect } from 'vitest';
import { parseChannel, parsePreview, parseAdapter, parseTemplate } from '../../src/api/channels';
import { parseAction } from '../../src/api/routing';
const identifier = '11111111-1111-1111-1111-111111111111';
const channel = { id: identifier, name: 'Канал', kind: 'SMTP', enabled: true, configured: true, available: true, version: 1, secret_version: 1, secret_configured: true, configuration: { kind: 'SMTP', host: 'mail.example.org', port: 587, tls: 'STARTTLS', username: '', sender: 'hub@example.org', recipients: ['ops@example.org'] }, template_id: null, health_status: 'UNKNOWN', checked_at: null };
describe('channel and template boundaries', () => {
  it('drops unexpected fields, including accidentally returned secrets', () => {
    expect(parseChannel({ ...channel, secret: 'PRIVATE', secret_ciphertext: 'CIPHER', detail: 'SQL TRACE' })).toEqual(channel);
    expect(parseChannel({ ...channel, configuration: { ...channel.configuration, password: 'PRIVATE' } }).configuration).toEqual(channel.configuration);
  });
  it('rejects malformed and unbounded responses', () => {
    for (const v of [{ ...channel, kind: 'MAX' }, { ...channel, secret_configured: 'true' }, { ...channel, id: 'unknown' }, { ...channel, checked_at: 'invalid' }, { ...channel, configuration: { ...channel.configuration, tls: 'NONE' } }]) expect(() => parseChannel(v)).toThrow();
    expect(() => parsePreview({ subject: '', body: 'x'.repeat(65537), html: '', payload: '' })).toThrow();
    expect(() => parseAdapter({ kind: 'SMTP', installed: true, enabled: true, visible: true, configured: true, health_status: 'UNKNOWN', version: 1, affected_rules: new Array(101).fill('x') })).toThrow();
    expect(() => parseTemplate({ id: identifier, kind: 'UNKNOWN', name: 'x', subject: '', body: 'x', html: '', version: 1 })).toThrow();
  });
  it('preserves channel references when editing existing rules', () => {
    expect(parseAction({ kind: 'NOTIFY', scope: 'EVENT', mode: 'IMMEDIATE', target_id: identifier }).target_id).toBe(identifier);
  });
});
