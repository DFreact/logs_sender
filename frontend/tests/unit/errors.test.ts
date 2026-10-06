import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, decodeError, getLiveness } from '../../src/api/client';

afterEach(() => vi.unstubAllGlobals());

describe('safe API failures', () => {
  it('discards exception details, params, input and invalid request IDs', () => {
    const unsafe = 'SQL exception password=secret-token';
    expect(decodeError({ code: unsafe, detail: unsafe, request_id: unsafe,
      params: { reason: unsafe }, field_errors: [{ input: unsafe }] }))
      .toEqual({ key: 'errors.internalWithoutId', requestId: undefined });
    expect(decodeError({ code: 'MAIL_CONNECTION_FAILED', params: { reason: unsafe }, request_id: 'a'.repeat(32) }))
      .toEqual({ key: 'errors.mailConnection', requestId: 'a'.repeat(32) });
    expect(decodeError({ code: '__proto__' }).key).toBe('errors.internalWithoutId');
  });

  it.each([null, [], '<h1>nginx internal service password</h1>', {}])('handles untrusted shape %s', (value) => {
    expect(decodeError(value).key).toBe('errors.internalWithoutId');
  });

  it('replaces non-JSON proxy errors', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('password=secret', { status: 502 })));
    await expect(getLiveness(new AbortController().signal)).rejects.toMatchObject({ safe: { key: 'errors.internalWithoutId' } });
  });

  it('accepts only known form fields and never displays server-provided messages', () => {
    expect(decodeError({ code: 'VALIDATION_ERROR', field_errors: [
      { field: 'password', code: 'PASSWORD_POLICY', params: { message: 'secret' } },
      { field: 'display_name', code: 'RAW_EXCEPTION', message: 'Traceback' },
      { field: 'password_hash', code: 'VALIDATION_REQUIRED' },
      { field: '__proto__', code: 'VALIDATION_REQUIRED' },
    ] }).fields).toEqual({ password: 'errors.passwordPolicy', display_name: 'validation.invalid' });
  });

  it('replaces network exceptions and rejects unknown success payloads', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('smtp-token-secret')));
    await expect(getLiveness(new AbortController().signal)).rejects.toMatchObject({ safe: { key: 'errors.network' } });
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({ status: 'raw-service', checked_at: 'password' })));
    await expect(getLiveness(new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
  });
});
