import { errorKeys, labelKey, type TranslationKey } from '../i18n';
import { formFields } from '../i18n/mappings';

export interface SafeError {
  key: TranslationKey;
  requestId?: string;
  fields?: Partial<Record<keyof typeof formFields, TranslationKey>>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

export function decodeError(value: unknown): SafeError {
  const payload = isRecord(value) ? value : {};
  const requestId = typeof payload.request_id === 'string' && /^[a-f0-9]{32}$/.test(payload.request_id)
    ? payload.request_id : undefined;
  let key = labelKey(errorKeys, payload.code, 'errors.internal');
  if (key === 'errors.internal' && !requestId) key = 'errors.internalWithoutId';
  const fields: SafeError['fields'] = {};
  if (Array.isArray(payload.field_errors)) for (const field of payload.field_errors.slice(0, 20)) {
    if (isRecord(field) && typeof field.field === 'string' && Object.hasOwn(formFields, field.field)) {
      fields[field.field as keyof typeof formFields] = labelKey(errorKeys, field.code, 'validation.invalid');
    }
  }
  return Object.keys(fields).length ? { key, requestId, fields } : { key, requestId };
}

export class ApiError extends Error {
  constructor(readonly safe: SafeError, readonly status = 0) { super('API_REQUEST_FAILED'); }
}

export const SESSION_EXPIRED_EVENT = 'eventhub:session-expired';

export async function requestJson(path: string, options: {
  method?: 'GET' | 'POST' | 'PATCH'; body?: unknown; csrf?: string; signal?: AbortSignal;
} = {}): Promise<unknown> {
  const method = options.method ?? 'GET';
  let response: Response;
  try {
    response = await fetch(path, {
      method, credentials: 'same-origin', cache: 'no-store',
      headers: { Accept: 'application/json', ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.csrf ? { 'X-CSRF-Token': options.csrf } : {}) },
      body: options.body ? JSON.stringify(options.body) : undefined,
      signal: options.signal ? AbortSignal.any([options.signal, AbortSignal.timeout(15000)]) : AbortSignal.timeout(15000),
    });
  } catch {
    throw new ApiError({ key: method === 'GET' ? 'errors.network' : 'errors.uncertainResult' });
  }
  if (response.status === 401 && path !== '/api/v1/auth/login') {
    window.dispatchEvent(new Event(SESSION_EXPIRED_EVENT));
  }
  if (response.status === 204) return undefined;
  let data: unknown;
  try { data = await response.json(); }
  catch { throw new ApiError({ key: 'errors.internalWithoutId' }, response.status); }
  if (!response.ok) throw new ApiError(decodeError(data), response.status);
  return data;
}

export function safeFailure(error: unknown): SafeError {
  return error instanceof ApiError ? error.safe : { key: 'errors.internalWithoutId' };
}

export interface Liveness {
  status: 'HEALTHY';
  checked_at: string;
}

export async function getLiveness(signal: AbortSignal): Promise<Liveness> {
  let response: Response;
  try {
    response = await fetch('/health/live', {
      headers: { Accept: 'application/json' }, cache: 'no-store',
      signal: AbortSignal.any([signal, AbortSignal.timeout(8000)]),
    });
  } catch {
    throw new ApiError({ key: 'errors.network' });
  }
  let data: unknown;
  try { data = await response.json(); }
  catch { throw new ApiError({ key: 'errors.internalWithoutId' }); }
  if (!response.ok) throw new ApiError(decodeError(data));
  if (!isRecord(data) || data.status !== 'HEALTHY' || typeof data.checked_at !== 'string'
    || !/^\d{4}-\d{2}-\d{2}T/.test(data.checked_at) || !Number.isFinite(Date.parse(data.checked_at))) {
    throw new ApiError({ key: 'errors.internalWithoutId' });
  }
  return { status: 'HEALTHY', checked_at: data.checked_at };
}
